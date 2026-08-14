"""Quote e tetti per-tenant sul CRUD dei parser (#31 B2, PR 3 della sequenza #2).

La regola non negoziabile che questi tetti servono: «tutto personale del
cliente, non deve bloccare gli altri». Senza un cap sul numero di parser e
sulla dimensione di titolo e config, una sessione approvata puo' gonfiare lo
SQLite e il volume Railway CONDIVISO — e il danno lo pagano tutti gli utenti,
non chi lo causa.

Cosa vincolano questi test:

- la quota sul NUMERO di parser e' per utente: chi la esaurisce riceve un 4xx
  col motivo, e l'utente accanto continua a creare;
- la quota regge la corsa: due creazioni simultanee sull'ultimo posto non
  bucano il tetto, perche' il conteggio sta nella stessa transazione
  dell'INSERT;
- titolo e config hanno un tetto di dimensione, su creazione E modifica: un
  parser gia' dentro non puo' gonfiarsi con una PUT;
- i messaggi d'errore dicono il limite e non nominano risorse di altri utenti;
- (a bordo, #27) i tre messaggi della richiesta di accesso dicono «gia'», non
  «giu'»: refuso mancato dalla correzione sulla PR #26.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE))

import main  # noqa: E402 - dopo l'inserimento del percorso
from tests.ambiente import CHIAVI_PERICOLOSE, TOKEN_DI_PROVA  # noqa: E402
from tests.relay.test_dispatch_motore import CONFIG_WEB  # noqa: E402
from tests.relay.test_login import (  # noqa: E402
    BOT_FINTO, SEGRETO_ATTESO, _dati_login)


@pytest.fixture(autouse=True)
def _ambiente_pulito(monkeypatch):
    """Nessuna variabile della macchina entra in questi test."""
    for chiave in CHIAVI_PERICOLOSE:
        monkeypatch.delenv(chiave, raising=False)
    monkeypatch.setattr(main, 'TOKEN', TOKEN_DI_PROVA)


class RichiestaConCorpo:
    """Il minimo che le rotte del CRUD leggono: cookie di sessione e corpo JSON.

    Espone l'interfaccia REALE di lettura (`headers` + `stream()`), non un
    `json()` gia' materializzato: da quando il corpo ha un tetto in byte
    (`_json_dal_corpo`), un fake che consegnasse l'oggetto gia' parsato
    renderebbe il tetto invisibile a tutti i test di questo file.
    """

    def __init__(self, cookie, corpo):
        self.cookies = {main.NOME_COOKIE: cookie}
        self._grezzo = json.dumps(corpo).encode()
        self.headers = {'content-length': str(len(self._grezzo))}

    async def stream(self):
        for i in range(0, len(self._grezzo), 8192):
            yield self._grezzo[i:i + 8192]


def _relay(tmp_path, monkeypatch, nome):
    percorso = str(tmp_path / nome)
    monkeypatch.setattr(main, 'DB_PATH', percorso)
    monkeypatch.setattr(main, '_PERCORSI_MIGRATI', set())
    monkeypatch.setattr(main, 'BOT_TOKEN', BOT_FINTO)
    monkeypatch.setattr(main, 'SEGRETO_SESSIONE', SEGRETO_ATTESO)
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', '')
    main.db().close()
    return percorso


def _cookie_di(telegram_id):
    risposta = main.login_telegram(main.LoginTelegramIn(**_dati_login(id=telegram_id)))
    for pezzo in (risposta.headers.get('set-cookie') or '').split(';'):
        chiave, _, valore = pezzo.strip().partition('=')
        if chiave == main.NOME_COOKIE:
            return valore
    raise AssertionError('login senza cookie')


def _crea(cookie, titolo='Parser Di Prova', config=None):
    """Chiama la rotta vera di creazione; restituisce (stato, corpo_o_messaggio)."""
    corpo = {'titolo': titolo, 'config': config or CONFIG_WEB, 'active': True}
    try:
        risposta = asyncio.run(main.crea_parser_mio(RichiestaConCorpo(cookie, corpo)))
        return 200, json.loads(bytes(risposta.body).decode())
    except main.HTTPException as e:
        return e.status_code, e.detail


def _modifica(cookie, slug, titolo='Parser Di Prova', config=None):
    corpo = {'titolo': titolo, 'config': config or CONFIG_WEB, 'active': True}
    try:
        risposta = asyncio.run(
            main.modifica_parser_mio(slug, RichiestaConCorpo(cookie, corpo)))
        return 200, json.loads(bytes(risposta.body).decode())
    except main.HTTPException as e:
        return e.status_code, e.detail


# --------------------------------------------------------- la quota sul numero

def test_la_quota_si_esaurisce_e_l_utente_ACCANTO_continua(tmp_path, monkeypatch):
    """Il tetto e' PER utente: chi lo esaurisce riceve un 4xx, l'altro crea."""
    _relay(tmp_path, monkeypatch, 'quota.db')
    monkeypatch.setattr(main, 'MAX_PARSER_PER_UTENTE', 3, raising=False)
    anna = _cookie_di('111000111')
    for n in range(3):
        stato, _ = _crea(anna, titolo=f'Parser {n}')
        assert stato == 200, f'creazione {n} fallita sotto quota: {stato}'
    stato, dettaglio = _crea(anna, titolo='Parser Di Troppo')
    assert stato == 409, (
        f'la quota esaurita risponde {stato} invece di 409: {dettaglio!r}')
    assert '3' in str(dettaglio), (
        f'il messaggio non dice il limite: {dettaglio!r}')

    bruno = _cookie_di('222000222')
    stato, _ = _crea(bruno, titolo='Il Primo Di Bruno')
    assert stato == 200, (
        f'la quota di un utente ha bloccato l\'utente ACCANTO: {stato}')


def test_la_quota_regge_la_CORSA_sull_ultimo_posto(tmp_path, monkeypatch):
    """Due creazioni simultanee sull'ultimo posto: una passa, una no.

    Il conteggio sta nella stessa transazione dell'INSERT: senza, due richieste
    concorrenti leggerebbero entrambe «uno sotto quota» e la bucherebbero.
    """
    percorso = _relay(tmp_path, monkeypatch, 'quota_corsa.db')
    monkeypatch.setattr(main, 'MAX_PARSER_PER_UTENTE', 2, raising=False)
    anna = _cookie_di('333000333')
    assert _crea(anna, titolo='Primo')[0] == 200

    esiti, via = [], threading.Barrier(2)

    def concorrente(n):
        via.wait()
        esiti.append(_crea(anna, titolo=f'Corsa {n}')[0])

    fili = [threading.Thread(target=concorrente, args=(n,)) for n in range(2)]
    for f in fili:
        f.start()
    for f in fili:
        f.join()

    c = sqlite3.connect(percorso)
    quanti = c.execute('SELECT COUNT(*) FROM parsers p JOIN users u ON u.id=p.user_id'
                       " WHERE u.telegram_id='333000333'").fetchone()[0]
    c.close()
    # ESATTAMENTE un successo e un 409, non «al piu' due»: la versione lasca
    # passerebbe anche se entrambe le richieste fallissero — segnalato da
    # GPT-5.5, ed e' la differenza fra un tetto e un servizio rotto.
    assert sorted(esiti) == [200, 409], (
        f'attesi un 200 e un 409 sulla corsa: {esiti}')
    assert quanti == 2, f'{quanti} parser dopo la corsa: attesi esattamente 2'


def test_il_messaggio_di_quota_non_nomina_risorse_ALTRUI(tmp_path, monkeypatch):
    _relay(tmp_path, monkeypatch, 'quota_msg.db')
    monkeypatch.setattr(main, 'MAX_PARSER_PER_UTENTE', 1, raising=False)
    anna = _cookie_di('444000444')
    assert _crea(anna)[0] == 200
    _, dettaglio = _crea(anna, titolo='Secondo')
    testo = str(dettaglio)
    assert 'PIERO' not in testo and 'piero' not in testo, (
        f'il messaggio di quota nomina risorse di un altro utente: {testo!r}')


# ------------------------------------------------------ i tetti di dimensione

def test_un_titolo_OLTRE_il_tetto_e_respinto_in_creazione_e_modifica(tmp_path, monkeypatch):
    _relay(tmp_path, monkeypatch, 'titolo.db')
    anna = _cookie_di('555000555')
    stato, dettaglio = _crea(anna, titolo='x' * 500)
    assert stato == 422, (
        f'un titolo di 500 caratteri e\' stato accettato in creazione: {stato}')
    assert str(main.MAX_TITOLO_PARSER) in str(dettaglio), (
        f'il messaggio non dice il limite: {dettaglio!r}')
    stato, corpo = _crea(anna, titolo='Normale')
    assert stato == 200
    stato, dettaglio = _modifica(anna, corpo['slug'], titolo='y' * 500)
    assert stato == 422, (
        f'un titolo di 500 caratteri e\' stato accettato in MODIFICA: {stato}')
    assert str(main.MAX_TITOLO_PARSER) in str(dettaglio)


def test_una_config_OLTRE_il_tetto_e_respinta_in_creazione_e_modifica(tmp_path, monkeypatch):
    _relay(tmp_path, monkeypatch, 'config.db')
    anna = _cookie_di('666000666')
    gonfia = dict(CONFIG_WEB)
    gonfia['columns'] = dict(CONFIG_WEB['columns'])
    gonfia['columns']['MarketName'] = {'source': 'constant', 'value': 'Z' * 50_000}
    stato, dettaglio = _crea(anna, config=gonfia)
    assert stato == 422, (
        f'una config da 50k caratteri e\' stata accettata in creazione: {stato}')
    assert str(main.MAX_CONFIG_PARSER) in str(dettaglio), (
        f'il messaggio non dice il limite: {dettaglio!r}')
    stato, corpo = _crea(anna)
    assert stato == 200
    stato, dettaglio = _modifica(anna, corpo['slug'], config=gonfia)
    assert stato == 422, (
        f'una config da 50k caratteri e\' stata accettata in MODIFICA: {stato}')
    assert str(main.MAX_CONFIG_PARSER) in str(dettaglio)


def test_il_bordo_esatto_dei_tetti(tmp_path, monkeypatch):
    """Al limite esatto passa; un carattere oltre no — con la STESSA
    serializzazione usata per salvare (json.dumps), non una stima."""
    _relay(tmp_path, monkeypatch, 'bordo.db')
    anna = _cookie_di('999000999')
    assert _crea(anna, titolo='t' * main.MAX_TITOLO_PARSER)[0] == 200
    assert _crea(anna, titolo='t' * (main.MAX_TITOLO_PARSER + 1))[0] == 422

    base = {'match': {'type': 'contains', 'value': 'SEGNALE'},
            'columns': {'EventName': {'source': 'constant', 'value': ''},
                        'MarketType': {'source': 'constant', 'value': 'OVER_UNDER_15'},
                        'SelectionName': {'source': 'constant', 'value': 'Over 1,5 goal'},
                        'BetType': {'source': 'constant', 'value': 'PUNTA'}}}
    scheletro = len(json.dumps(base)) - len(json.dumps(''))  # il posto del valore
    riempi = main.MAX_CONFIG_PARSER - scheletro - 2  # le virgolette del valore JSON
    base['columns']['EventName']['value'] = 'x' * riempi
    assert len(json.dumps(base)) == main.MAX_CONFIG_PARSER
    assert _crea(anna, titolo='Al Limite', config=base)[0] == 200
    base['columns']['EventName']['value'] += 'x'
    assert _crea(anna, titolo='Oltre Il Limite', config=base)[0] == 422


def test_la_variabile_della_quota_NON_butta_giu_l_avvio(monkeypatch):
    """`MAX_PARSER_PER_UTENTE` vuota o non numerica sul pannello Railway non deve
    trasformarsi in un servizio che non parte — segnalato da GPT-5.5, stessa
    classe del fail-closed di auth(): l'errore di configurazione si assorbe con
    il default, dichiarato nel log."""
    monkeypatch.setenv('MAX_PARSER_PER_UTENTE', '')
    assert main._intero_da_env('MAX_PARSER_PER_UTENTE', 20) == 20
    monkeypatch.setenv('MAX_PARSER_PER_UTENTE', 'venti')
    assert main._intero_da_env('MAX_PARSER_PER_UTENTE', 20) == 20
    monkeypatch.setenv('MAX_PARSER_PER_UTENTE', '-3')
    assert main._intero_da_env('MAX_PARSER_PER_UTENTE', 20) == 20
    monkeypatch.setenv('MAX_PARSER_PER_UTENTE', '7')
    assert main._intero_da_env('MAX_PARSER_PER_UTENTE', 20) == 7
    monkeypatch.delenv('MAX_PARSER_PER_UTENTE')
    assert main._intero_da_env('MAX_PARSER_PER_UTENTE', 20) == 20


def test_una_config_NORMALE_resta_sotto_i_tetti(tmp_path, monkeypatch):
    """Il verso opposto: i tetti non strozzano una config vera del motore."""
    _relay(tmp_path, monkeypatch, 'normale.db')
    anna = _cookie_di('777000777')
    stato, _ = _crea(anna, titolo='Un Titolo Del Tutto Ragionevole', config=CONFIG_WEB)
    assert stato == 200


# -------------------------------------------------------------- i refusi (#27)

def test_i_messaggi_della_richiesta_di_accesso_dicono_GIA(tmp_path, monkeypatch):
    """I tre 409 dicono «gia'», non «giu'» — il refuso mancato sulla PR #26 (#27)."""
    percorso = _relay(tmp_path, monkeypatch, 'refusi.db')
    cookie = _cookie_di('888000888')

    class Richiesta:
        cookies = {main.NOME_COOKIE: cookie}

    # Ramo 1 — accesso gia' attivo.
    c = sqlite3.connect(percorso)
    c.execute("UPDATE users SET status='attivo', access_expires_at=NULL"
              " WHERE telegram_id='888000888'")
    c.commit()
    c.close()
    with pytest.raises(main.HTTPException) as e:
        main.chiedi_accesso(Richiesta())
    assert e.value.status_code == 409
    assert "gia'" in e.value.detail and 'giu' not in e.value.detail, (
        f'il messaggio dice ancora «giu\'»: {e.value.detail!r}')

    # Ramo 2 — richiesta gia' in corso (stato in_attesa): la prima richiesta
    # passa, la seconda trova lo stato e rifiuta col messaggio corretto.
    c = sqlite3.connect(percorso)
    c.execute("UPDATE users SET status='registrato' WHERE telegram_id='888000888'")
    c.commit()
    c.close()
    main.chiedi_accesso(Richiesta())
    with pytest.raises(main.HTTPException) as e:
        main.chiedi_accesso(Richiesta())
    assert e.value.status_code == 409
    assert "gia'" in e.value.detail and 'giu' not in e.value.detail

    # Ramo 3 — la corsa persa sull'INSERT (l'indice richiesta_aperta_unica dice
    # no): stato NON in_attesa ma richiesta aperta gia' a database, come la
    # lascerebbe l'altro processo che ha vinto la corsa.
    c = sqlite3.connect(percorso)
    c.execute("UPDATE users SET status='registrato' WHERE telegram_id='888000888'")
    c.commit()
    c.close()
    with pytest.raises(main.HTTPException) as e:
        main.chiedi_accesso(Richiesta())
    assert e.value.status_code == 409
    assert "gia'" in e.value.detail and 'giu' not in e.value.detail, (
        f'il ramo della corsa dice ancora «giu\'»: {e.value.detail!r}')


# ------------------------------------------------- il tetto sul CORPO, in byte

class RichiestaCorpoGrezzo:
    """Una richiesta con l'interfaccia VERA di lettura del corpo: headers + stream.

    Il fake storico di questo file espone solo `json()`, cioe' il corpo gia'
    materializzato: qualunque tetto sui byte risulterebbe invisibile. Questo fake
    serve i byte a pezzi come farebbe uvicorn, e conta quanti pezzi gli sono stati
    chiesti: se il tetto funziona, la lettura si ferma PRIMA di aver consumato
    tutto. `json()` resta presente solo perche' il test deve poter girare (rosso)
    anche sul codice vecchio, che leggeva con `request.json()`.
    """

    def __init__(self, cookie, grezzo, content_length=None):
        self.cookies = {main.NOME_COOKIE: cookie}
        self._grezzo = grezzo
        self.headers = {}
        if content_length is not None:
            self.headers['content-length'] = str(content_length)
        self.pezzi_serviti = 0
        self.pezzi_totali = (len(grezzo) + 8191) // 8192

    async def stream(self):
        for i in range(0, len(self._grezzo), 8192):
            self.pezzi_serviti += 1
            yield self._grezzo[i:i + 8192]

    async def json(self):
        return json.loads(self._grezzo)


def _corpo_gonfio():
    """Un corpo JSON valido ma enorme: ~100 KiB, ben oltre MAX_CORPO_JSON."""
    return json.dumps({'titolo': 'x', 'config': {'pad': 'a' * 100_000},
                       'active': True}).encode()


def test_un_corpo_enorme_SENZA_content_length_e_respinto_a_meta_lettura(
        tmp_path, monkeypatch):
    """Chi mente sull'intestazione (o usa il chunked) va fermato DALLA LETTURA.

    Atteso: 413 prima di aver consumato tutto lo stream. Sul codice vecchio la
    rotta materializza l'intero corpo con `request.json()` e risponde 422 dal
    tetto sul CAMPO — a quel punto i 100 KiB sono gia' tutti in RAM, che e'
    esattamente cio' che il bloccante di GPT-5.6 Sol contesta.
    """
    _relay(tmp_path, monkeypatch, 'corpo1.db')
    anna = _cookie_di('771000771')
    richiesta = RichiestaCorpoGrezzo(anna, _corpo_gonfio())
    with pytest.raises(main.HTTPException) as e:
        asyncio.run(main.crea_parser_mio(richiesta))
    assert e.value.status_code == 413, (
        f'atteso 413 dal tetto sul corpo, arrivato {e.value.status_code}: '
        f'{e.value.detail!r}')
    assert str(main.MAX_CORPO_JSON) in str(e.value.detail)
    assert richiesta.pezzi_serviti < richiesta.pezzi_totali, (
        'lo stream e\' stato consumato per intero: il tetto non ha fermato la lettura')


def test_un_content_length_oltre_il_tetto_e_respinto_SENZA_leggere(
        tmp_path, monkeypatch):
    """Il client onesto che dichiara un corpo enorme non fa leggere neanche un byte."""
    _relay(tmp_path, monkeypatch, 'corpo2.db')
    anna = _cookie_di('772000772')
    grezzo = _corpo_gonfio()
    richiesta = RichiestaCorpoGrezzo(anna, grezzo, content_length=len(grezzo))
    with pytest.raises(main.HTTPException) as e:
        asyncio.run(main.modifica_parser_mio('slug-qualunque', richiesta))
    assert e.value.status_code == 413
    assert richiesta.pezzi_serviti == 0, (
        f'il corpo e\' stato letto ({richiesta.pezzi_serviti} pezzi) nonostante '
        f'il Content-Length dichiarato oltre il tetto')


def test_un_corpo_normale_passa_dal_tetto_sul_corpo(tmp_path, monkeypatch):
    """Guardia sul verso opposto: il corpo legittimo (config vera) resta sotto il
    tetto e la creazione riesce attraverso l'interfaccia reale headers+stream."""
    _relay(tmp_path, monkeypatch, 'corpo3.db')
    anna = _cookie_di('773000773')
    grezzo = json.dumps({'titolo': 'Parser Normale', 'config': CONFIG_WEB,
                         'active': True}).encode()
    assert len(grezzo) <= main.MAX_CORPO_JSON
    richiesta = RichiestaCorpoGrezzo(anna, grezzo, content_length=len(grezzo))
    risposta = asyncio.run(main.crea_parser_mio(richiesta))
    corpo = json.loads(bytes(risposta.body).decode())
    assert corpo['slug']
