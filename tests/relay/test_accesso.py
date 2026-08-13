"""Accesso su approvazione: stato, scadenza, richiesta, decisione del proprietario.

E' il PR 7 della Issue #2, e la sua sostanza non e' «aggiungere una schermata»: e' decidere
**chi puo' ricevere segnali**, che e' la stessa domanda dell'autenticazione vista dall'altro
lato. Un difetto qui non da' un errore: da' il feed a chi non ha pagato, o lo toglie a chi ha
pagato senza dirglielo.

Cosa vincolano questi test, e perche' ognuno esiste:

- **la scadenza e' un istante, non un evento.** Nessun processo riscrive `status` a
  mezzanotte, quindi la colonna resta `'attivo'` per sempre e chi la legge direttamente
  mente. Misurato prima della correzione: `GET /api/me` di un cliente scaduto il giorno
  prima rispondeva `stato: attivo`. La conversione vive in `stato_effettivo()`, una sola
  volta, e la usano `/api/me`, il feed e il webhook — se ognuno decidesse da se', il
  pannello direbbe una cosa e il feed un'altra;
- **il caso limite dei rinnovi.** Prorogare di 30 giorni un cliente scaduto da due mesi
  deve dargli 30 giorni **da oggi**, non una scadenza nel passato. E' scritto nella Issue #2
  come trappola da non riscoprire, e senza il secondo ramo di `nuova_scadenza()` il sintomo
  e' di nuovo «pannello attivo, feed vuoto»;
- **la scadenza NON revoca il token.** «Scaduto» e «token revocato» sono stati diversi:
  revocare costringerebbe il cliente a riconfigurare XTrader a ogni rinnovo. Alla scadenza
  il feed risponde `200` con la **sola intestazione**, come «nessun segnale», e **non**
  `401`;
- **il proprietario non e' un cliente.** Il suo accesso non dipende da un'approvazione, e
  un difetto che lo trattasse come tale spegnerebbe il feed che XTrader interroga in
  produzione.
"""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE))

import main  # noqa: E402 - dopo l'inserimento del percorso
from tests.ambiente import CHIAVI_PERICOLOSE, TOKEN_DI_PROVA  # noqa: E402
from tests.relay.test_csv_contract import RIGA_VALIDA  # noqa: E402

# Importati da `test_login.py` e NON ricopiati: la firma del Login Widget, il bot finto e il
# segreto atteso sono la stessa cosa in tutti i test che hanno bisogno di una sessione, e due
# copie divergono al primo cambio del formato (regola 3).
from tests.relay.test_login import (  # noqa: E402
    ADMIN_FINTO, BOT_FINTO, SEGRETO_ATTESO, _dati_login)

GIORNO = 86400


def _riga_con_evento(evento):
    """La riga CSV canonica con un `EventName` riconoscibile.

    Parte da `RIGA_VALIDA` di `test_csv_contract.py` invece di comporre una riga a mano:
    `make_csv()` vuole una RIGA di 14 valori, e passandogli un dizionario scriveva le CHIAVI
    come riga — `verify_csv` degradava il feed a sola intestazione e il test sul cliente
    scaduto sarebbe passato per il motivo sbagliato. Misurato scrivendolo male.
    """
    riga = list(RIGA_VALIDA)
    riga[main.HEADERS.index('EventName')] = evento
    return riga


@pytest.fixture(autouse=True)
def _ambiente_pulito(monkeypatch):
    """Nessuna variabile della macchina entra in questi test.

    Stessa ragione delle fixture gemelle in `tests/relay/`: con il `.env` del proprietario
    caricato l'esito dipenderebbe dalla macchina invece che dal codice, e un test che avvia
    l'app ripunterebbe il webhook del bot vero.
    """
    for chiave in CHIAVI_PERICOLOSE:
        monkeypatch.delenv(chiave, raising=False)
    monkeypatch.setattr(main, 'TOKEN', '')


# ------------------------------------------------------- lo stato che conta adesso

def test_un_cliente_ATTIVO_ma_SCADUTO_non_risulta_attivo():
    """Il difetto misurato prima di `stato_effettivo()`, ridotto alla sua unita'.

    La colonna dice `attivo` perche' nessuno l'ha riscritta: la scadenza e' passata da se'.
    Chi legge la colonna vede un cliente attivo, chi legge la scadenza vede un cliente
    scaduto, e i due pezzi del servizio decidono in modo diverso.
    """
    adesso = 1_000_000
    assert main.stato_effettivo('attivo', adesso - GIORNO, adesso=adesso) == 'scaduto'
    assert main.stato_effettivo('attivo', adesso + GIORNO, adesso=adesso) == 'attivo'


def test_lo_stato_DECISO_a_mano_non_viene_riscritto_dalla_scadenza():
    """`sospeso` e `registrato` non diventano `scaduto` per il passare del tempo.

    Sono decisioni del proprietario, non conseguenze del calendario: se la scadenza le
    sovrascrivesse, un cliente sospeso a mano risulterebbe «scaduto» e il pannello
    perderebbe la sola informazione che diceva perche' e' fuori.
    """
    adesso = 1_000_000
    for stato in ('registrato', 'in_attesa', 'sospeso'):
        assert main.stato_effettivo(stato, adesso - GIORNO, adesso=adesso) == stato
    # E `attivo` senza scadenza resta attivo: e' il proprietario, che non ha un abbonamento.
    assert main.stato_effettivo('attivo', None, adesso=adesso) == 'attivo'


def test_i_giorni_rimasti_arrotondano_per_ECCESSO():
    """A 30 ore dalla scadenza restano 2 giorni, non 1.

    Troncando, l'ultimo giorno di accesso il cliente leggerebbe «0 giorni rimasti» mentre il
    feed funziona ancora: un numero che dice zero su un accesso vivo insegna a non fidarsi
    del numero. E scaduto da' `0`, mai un negativo — «-3 giorni rimasti» non e'
    un'informazione.
    """
    adesso = 1_000_000
    assert main.giorni_rimasti(adesso + 30 * 3600, adesso=adesso) == 2
    assert main.giorni_rimasti(adesso + GIORNO, adesso=adesso) == 1
    assert main.giorni_rimasti(adesso + 60, adesso=adesso) == 1
    assert main.giorni_rimasti(adesso - 3 * GIORNO, adesso=adesso) == 0
    assert main.giorni_rimasti(None, adesso=adesso) is None


# --------------------------------------------------------------- la regola dei rinnovi

def test_i_rinnovi_si_SOMMANO_se_la_scadenza_e_nel_futuro():
    """5 giorni residui + 30 concessi = 35 giorni, non 30.

    Chi rinnova in anticipo non deve perdere i giorni che gli restano, altrimenti il
    cliente impara ad aspettare la scadenza per non essere derubato — e nel frattempo il
    feed gli si spegne.
    """
    adesso = 1_000_000
    fra_cinque = adesso + 5 * GIORNO
    assert main.nuova_scadenza(fra_cinque, 30, adesso=adesso) == adesso + 35 * GIORNO


def test_un_cliente_SCADUTO_riparte_da_OGGI_non_dal_passato():
    """Scaduto da 60 giorni + 30 concessi = 30 giorni **da oggi**.

    Il caso limite della Issue #2: sommando alla scadenza vecchia si otterrebbe un istante
    ancora nel passato, quindi il pannello direbbe «attivo» e il feed sarebbe vuoto. E' il
    ramo che si dimentica, perche' il caso normale funziona senza.
    """
    adesso = 1_000_000
    scaduto_da_due_mesi = adesso - 60 * GIORNO
    nuova = main.nuova_scadenza(scaduto_da_due_mesi, 30, adesso=adesso)
    assert nuova == adesso + 30 * GIORNO, (
        f'la nuova scadenza e- {nuova - adesso} secondi da adesso: sommando alla scadenza '
        'vecchia resta nel passato, e il cliente risulta attivo con il feed vuoto')
    assert main.stato_effettivo('attivo', nuova, adesso=adesso) == 'attivo'


def test_senza_scadenza_precedente_si_parte_da_OGGI():
    """Il primo accesso di un cliente nuovo: `access_expires_at` e' `NULL`."""
    adesso = 1_000_000
    assert main.nuova_scadenza(None, 7, adesso=adesso) == adesso + 7 * GIORNO


# ------------------------------------------------- lo stato arriva davvero all'API

def _cliente(tmp_path, monkeypatch, nome='accesso.db'):
    """Un relay in processo con un cliente registrato, e il suo cookie di sessione."""
    percorso = str(tmp_path / nome)
    monkeypatch.setattr(main, 'DB_PATH', percorso)
    monkeypatch.setattr(main, '_PERCORSI_MIGRATI', set())
    monkeypatch.setattr(main, 'BOT_TOKEN', BOT_FINTO)
    monkeypatch.setattr(main, 'SEGRETO_SESSIONE', SEGRETO_ATTESO)
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', '')
    risposta = main.login_telegram(main.LoginTelegramIn(**_dati_login(id='555000555')))
    cookie = None
    for pezzo in (risposta.headers.get('set-cookie') or '').split(';'):
        chiave, _, valore = pezzo.strip().partition('=')
        if chiave == main.NOME_COOKIE:
            cookie = valore
    c = sqlite3.connect(percorso)
    utente = c.execute("SELECT id FROM users WHERE telegram_id='555000555'").fetchone()[0]
    c.close()

    class Richiesta:
        cookies = {main.NOME_COOKIE: cookie}

    return percorso, utente, Richiesta()


def test_API_ME_dice_SCADUTO_e_ZERO_giorni_a_un_cliente_scaduto(tmp_path, monkeypatch):
    """Il difetto misurato sull'API vera, non sulla funzione da sola.

    E' la regola 2-bis di `CLAUDE.md`: la funzione giusta non serve a niente se il
    chiamante continua a leggere la colonna. Prima della correzione questa rotta rispondeva
    letteralmente `{'stato': 'attivo', 'accesso_scade': <ieri>}`.
    """
    import json
    percorso, utente, richiesta = _cliente(tmp_path, monkeypatch, 'me_scaduto.db')
    c = sqlite3.connect(percorso)
    c.execute("UPDATE users SET status='attivo', access_expires_at=? WHERE id=?",
              (int(time.time()) - GIORNO, utente))
    c.commit()
    c.close()

    corpo = json.loads(bytes(main.chi_sono(richiesta).body).decode())
    assert corpo['stato'] == 'scaduto', (
        f"la rotta dice stato={corpo['stato']!r} con una scadenza nel passato: il cliente "
        'legge «attivo» e il feed e- vuoto')
    assert corpo['giorni_rimasti'] == 0, (
        f"giorni_rimasti={corpo['giorni_rimasti']!r} invece di 0")


def test_API_ME_riporta_i_giorni_a_un_cliente_ATTIVO(tmp_path, monkeypatch):
    """Il verso opposto, che e' cio' che il cliente vede in dashboard."""
    import json
    percorso, utente, richiesta = _cliente(tmp_path, monkeypatch, 'me_attivo.db')
    c = sqlite3.connect(percorso)
    c.execute("UPDATE users SET status='attivo', access_expires_at=? WHERE id=?",
              (int(time.time()) + 10 * GIORNO, utente))
    c.commit()
    c.close()

    corpo = json.loads(bytes(main.chi_sono(richiesta).body).decode())
    assert corpo['stato'] == 'attivo'
    assert corpo['giorni_rimasti'] == 10, (
        f"giorni_rimasti={corpo['giorni_rimasti']!r} invece di 10")


# ------------------------------------------- la scadenza arriva al feed e al webhook

def _profilo_di_un_cliente(percorso, nome_profilo='MARCO', scadenza=None,
                           stato='attivo'):
    """Un profilo con feed, con dentro un segnale vivo, intestato a un cliente NON admin."""
    c = sqlite3.connect(percorso)
    c.execute('INSERT OR IGNORE INTO profiles(name,chat_ids,parser) VALUES (?,?,?)',
              (nome_profilo, '-100999', main.DEFAULT_PARSER))
    c.execute('INSERT INTO users(origin_profile, slug, first_name, status,'
              ' access_expires_at, is_admin) VALUES (?,?,?,?,?,0)',
              (nome_profilo, nome_profilo.lower(), nome_profilo, stato, scadenza))
    c.execute('INSERT INTO signals(csv, parser, profile, expires_at) VALUES (?,?,?,?)',
              (main.make_csv(_riga_con_evento('Tizio v Caio')),
               main.DEFAULT_PARSER, nome_profilo, int(time.time()) + 90))
    c.commit()
    c.close()


def test_il_feed_di_un_cliente_SCADUTO_torna_SOLA_INTESTAZIONE(tmp_path, monkeypatch):
    """Bloccante di Claude Fable 5 sulla PR #26, e aveva ragione: la funzione non basta.

    `stato_effettivo()` diceva la verita' e il feed non la guardava, quindi un cliente
    scaduto continuava a ricevere i segnali. E' il difetto peggiore di questa PR, perche' e'
    l'unica cosa che l'abbonamento deve davvero governare.

    Tre asserzioni, e ognuna esclude un modo sbagliato di negare l'accesso:

    - `200`, **non** `401`: per XTrader un errore HTTP e' un guasto da segnalare, mentre
      «nessun segnale» e' uno stato normale che gestisce da se';
    - i byte cominciano col **BOM** e con l'intestazione: un corpo vuoto sarebbe un CSV rotto;
    - il **token non e' revocato** — la riga in `users` conserva `token_hash`, e al rinnovo il
      cliente non deve riconfigurare XTrader.
    """
    percorso = str(tmp_path / 'feed_scaduto.db')
    monkeypatch.setattr(main, 'DB_PATH', percorso)
    monkeypatch.setattr(main, '_PERCORSI_MIGRATI', set())
    monkeypatch.setattr(main, 'TOKEN', TOKEN_DI_PROVA)
    main.db().close()
    _profilo_di_un_cliente(percorso, scadenza=int(time.time()) - GIORNO)

    risposta = main.named_profile_csv('MARCO', token=TOKEN_DI_PROVA)
    assert risposta.status_code == 200, (
        f'il feed di un cliente scaduto risponde {risposta.status_code}: per XTrader e- un '
        'guasto, non «nessun segnale»')
    corpo = bytes(risposta.body)
    assert corpo == main.empty_csv().encode('utf-8'), (
        f'il feed di un cliente scaduto consegna ancora un segnale: {corpo[:120]!r}')
    # `main.CSV_BOM` e non un U+FEFF letterale: la «REGOLA CODIFICA» di `CLAUDE.md` lo vieta
    # nei sorgenti — e' invisibile in un editor — e `tests/safety/test_codifica.py` lo cerca
    # anche qui. L'ho scritto letterale alla prima stesura, e la guardia esiste per questo.
    assert corpo.startswith(main.CSV_BOM.encode('utf-8') + b'"Provider"'), (
        f'il feed scaduto non e- un CSV valido: {corpo[:40]!r}')


def test_lo_stesso_feed_ATTIVO_consegna_il_segnale(tmp_path, monkeypatch):
    """Il verso opposto, senza cui il test sopra passerebbe anche con il feed sempre vuoto."""
    percorso = str(tmp_path / 'feed_attivo.db')
    monkeypatch.setattr(main, 'DB_PATH', percorso)
    monkeypatch.setattr(main, '_PERCORSI_MIGRATI', set())
    monkeypatch.setattr(main, 'TOKEN', TOKEN_DI_PROVA)
    main.db().close()
    _profilo_di_un_cliente(percorso, scadenza=int(time.time()) + 10 * GIORNO)

    corpo = bytes(main.named_profile_csv('MARCO', token=TOKEN_DI_PROVA).body)
    assert b'Tizio v Caio' in corpo, (
        f'il feed di un cliente ATTIVO non consegna il segnale: {corpo[:120]!r}')


def test_il_feed_del_PROPRIETARIO_non_dipende_da_una_scadenza(tmp_path, monkeypatch):
    """`is_admin` non ha un abbonamento, e questo test difende la produzione.

    Il feed del profilo PIERO e' quello che XTrader interroga adesso. Se dipendesse da
    `access_expires_at`, una riga sbagliata nel database lo spegnerebbe — e il sintomo
    sarebbe «XTrader non riceve piu' niente» senza nessun errore da nessuna parte.
    """
    percorso = str(tmp_path / 'feed_admin.db')
    monkeypatch.setattr(main, 'DB_PATH', percorso)
    monkeypatch.setattr(main, '_PERCORSI_MIGRATI', set())
    monkeypatch.setattr(main, 'TOKEN', TOKEN_DI_PROVA)
    main.db().close()

    c = sqlite3.connect(percorso)
    # Una scadenza nel passato sulla riga del proprietario: non deve cambiare niente.
    c.execute("UPDATE users SET access_expires_at=? WHERE origin_profile=?",
              (int(time.time()) - 30 * GIORNO, main.PIERO_PROFILE))
    c.execute('INSERT INTO signals(csv, parser, profile, expires_at) VALUES (?,?,?,?)',
              (main.make_csv(_riga_con_evento('Suo v Segnale')),
               main.DEFAULT_PARSER, main.PIERO_PROFILE, int(time.time()) + 90))
    c.commit()
    c.close()

    corpo = bytes(main.xtrader_csv(token=TOKEN_DI_PROVA).body)
    assert b'Suo v Segnale' in corpo, (
        f'il feed del proprietario e- stato spento da una scadenza: {corpo[:120]!r}')


def test_un_profilo_SENZA_utente_collegato_continua_a_funzionare(tmp_path, monkeypatch):
    """Assenza di dati non e' scadenza.

    Un profilo senza riga in `users` non ha un abbonamento da far scadere: negargli il feed
    sarebbe una regressione provocata da un'informazione che manca, non da una decisione.
    """
    percorso = str(tmp_path / 'feed_orfano.db')
    monkeypatch.setattr(main, 'DB_PATH', percorso)
    monkeypatch.setattr(main, '_PERCORSI_MIGRATI', set())
    monkeypatch.setattr(main, 'TOKEN', TOKEN_DI_PROVA)
    main.db().close()
    c = sqlite3.connect(percorso)
    c.execute('INSERT INTO profiles(name,chat_ids,parser) VALUES (?,?,?)',
              ('ORFANO', '-100888', main.DEFAULT_PARSER))
    c.execute('INSERT INTO signals(csv, parser, profile, expires_at) VALUES (?,?,?,?)',
              (main.make_csv(_riga_con_evento('Orfa v No')),
               main.DEFAULT_PARSER, 'ORFANO', int(time.time()) + 90))
    c.commit()
    c.close()

    corpo = bytes(main.named_profile_csv('ORFANO', token=TOKEN_DI_PROVA).body)
    assert b'Orfa v No' in corpo, f'feed negato a un profilo senza utente: {corpo[:120]!r}'


def test_una_scadenza_ILLEGGIBILE_blocca_invece_di_aprire(tmp_path, monkeypatch):
    """Rischio alzato da GPT-5.5 sulla PR #26, e la direzione del rimedio conta.

    SQLite ha tipi dinamici: `access_expires_at` e' `INTEGER` ma niente vieta che ci finisca
    `''`. Prima, `int('')` sollevava — quindi `/api/me` avrebbe risposto **500** su una rotta
    che deve solo dire chi sei. Ora un valore illeggibile vale **scaduto**, non attivo: un
    accesso di cui non si sa quando finisce non e' un accesso infinito. Il caso «senza fine»
    si scrive `NULL`, ed e' il proprietario.
    """
    # La stringa vuota vale **scaduto**, non «nessuna scadenza». La prima versione di questo
    # test pretendeva il contrario, cioe' cementava un fail-open: un `attivo` con `''` restava
    # attivo per sempre. Bloccante di Claude Fable 5 sulla PR #26, e aveva ragione — «nessuna
    # scadenza» si scrive `NULL`, e solo `NULL`, perche' quello lo scrive una decisione mentre
    # `''` lo scrive un difetto.
    assert main.stato_effettivo('attivo', '', adesso=1_000_000) == 'scaduto', (
        'una stringa vuota lascia l-accesso attivo per sempre: fail-open')
    assert main.stato_effettivo('attivo', None, adesso=1_000_000) == 'attivo', (
        'NULL e- «nessuna scadenza», ed e- lo stato del proprietario')
    assert main.stato_effettivo('attivo', 'domani', adesso=1_000_000) == 'scaduto', (
        'una scadenza illeggibile ha lasciato l-accesso attivo: fail-open')
    assert main.giorni_rimasti('domani', adesso=1_000_000) is None
    # E la rotta non solleva piu': era un 500 su /api/me.
    percorso, utente, richiesta = _cliente(tmp_path, monkeypatch, 'me_illeggibile.db')
    c = sqlite3.connect(percorso)
    c.execute("UPDATE users SET status='attivo', access_expires_at='domani' WHERE id=?",
              (utente,))
    c.commit()
    c.close()
    import json
    corpo = json.loads(bytes(main.chi_sono(richiesta).body).decode())
    assert corpo['stato'] == 'scaduto'
    assert corpo['giorni_rimasti'] is None


def _consegna_in_processo(chat_id, testo):
    """Una consegna di Telegram, chiamando l'handler in processo.

    In processo e non via HTTP come `tests/relay/test_webhook.py`: quel file avvia il
    servizio in sottoprocesso perche' misura il rifiuto **sul servizio vero**, con le sue
    intestazioni. Qui il soggetto e' la decisione sull'accesso, che sta dentro l'handler, e
    un sottoprocesso aggiungerebbe soltanto tempo e un altro database da preparare.
    """
    import asyncio

    class Richiesta:
        headers = {'X-Telegram-Bot-Api-Secret-Token': main.webhook_secret(BOT_FINTO)}

        async def json(self):
            return {'message': {'chat': {'id': int(chat_id)}, 'text': testo}}

    return asyncio.run(main.telegram_webhook(Richiesta()))


MESSAGGIO = ('P.Bet. PREMACHT 0,5HT\nTizio v Caio 🆚 Tizio v Caio\nOver 1,5 goal\n@ 1,85')


def test_un_messaggio_di_un_cliente_SCADUTO_non_viene_elaborato(tmp_path, monkeypatch):
    """Bloccante di Claude Fable 5 sulla PR #26, sull'altro percorso: il webhook.

    Alla scadenza il servizio non deve piu' elaborare i messaggi delle chat di quel cliente,
    e non deve nemmeno **registrarli**: il log dei messaggi e' una funzione del servizio, non
    un archivio, e continuare a riempirlo per chi non ha accesso significherebbe conservare i
    messaggi dei suoi canali senza dargli niente in cambio.

    E il feed **non** viene toccato: non si svuota e non si aggiorna. Allo svuotamento ci
    pensa il TTL di 90 secondi, che e' l'unico che deve toccarlo — un webhook che azzerasse
    il feed a ogni messaggio di un cliente scaduto sarebbe una scrittura provocata
    dall'esterno su un percorso che deve restare fermo.
    """
    percorso = str(tmp_path / 'webhook_scaduto.db')
    monkeypatch.setattr(main, 'DB_PATH', percorso)
    monkeypatch.setattr(main, '_PERCORSI_MIGRATI', set())
    monkeypatch.setattr(main, 'TOKEN', TOKEN_DI_PROVA)
    monkeypatch.setattr(main, 'SEGRETO_WEBHOOK', main.webhook_secret(BOT_FINTO))
    main.db().close()
    _profilo_di_un_cliente(percorso, scadenza=int(time.time()) - GIORNO)

    c = sqlite3.connect(percorso)
    prima = c.execute("SELECT csv FROM signals WHERE profile='MARCO'").fetchone()[0]
    c.close()

    esito = _consegna_in_processo('-100999', MESSAGGIO)
    assert esito.get('ignored') == 'access_scaduto', (
        f'il messaggio di un cliente scaduto e- stato elaborato: {esito}')

    c = sqlite3.connect(percorso)
    dopo = c.execute("SELECT csv FROM signals WHERE profile='MARCO'").fetchone()
    log = c.execute('SELECT COUNT(*) FROM message_logs').fetchone()[0]
    c.close()
    assert dopo is not None and dopo[0] == prima, (
        'il feed del cliente scaduto e- stato toccato dal webhook: allo svuotamento ci pensa '
        'il TTL, non una consegna')
    assert log == 0, (
        f'{log} righe in message_logs per un cliente senza accesso: il log e- una funzione '
        'del servizio, non un archivio')


def test_lo_stesso_messaggio_di_un_cliente_ATTIVO_viene_elaborato(tmp_path, monkeypatch):
    """Il verso opposto: senza, il test sopra passerebbe anche se il webhook fosse rotto."""
    percorso = str(tmp_path / 'webhook_attivo.db')
    monkeypatch.setattr(main, 'DB_PATH', percorso)
    monkeypatch.setattr(main, '_PERCORSI_MIGRATI', set())
    monkeypatch.setattr(main, 'TOKEN', TOKEN_DI_PROVA)
    monkeypatch.setattr(main, 'SEGRETO_WEBHOOK', main.webhook_secret(BOT_FINTO))
    main.db().close()
    _profilo_di_un_cliente(percorso, scadenza=int(time.time()) + 10 * GIORNO)

    esito = _consegna_in_processo('-100999', MESSAGGIO)
    assert 'ignored' not in esito or esito.get('ignored') != 'access_scaduto', (
        f'il messaggio di un cliente ATTIVO e- stato rifiutato per accesso: {esito}')


def test_un_cliente_SOSPESO_e_bloccato_come_uno_scaduto(tmp_path, monkeypatch):
    """Chiesto da GPT-5.5 sulla PR #26: `sospeso` era coperto solo dalla lista, non da un test.

    `sospeso` e' l'unico stato che il proprietario mette a mano per tagliare l'accesso
    **subito**, senza aspettare una scadenza: se non bloccasse, quel gesto sarebbe teatro
    esattamente come lo era cambiare `TELEGRAM_ADMIN_ID` prima della PR #24. E deve bloccare
    **anche con una scadenza nel futuro**, altrimenti sospendere un cliente appena rinnovato
    non farebbe niente — che e' proprio il caso in cui si sospende.
    """
    percorso = str(tmp_path / 'sospeso.db')
    monkeypatch.setattr(main, 'DB_PATH', percorso)
    monkeypatch.setattr(main, '_PERCORSI_MIGRATI', set())
    monkeypatch.setattr(main, 'TOKEN', TOKEN_DI_PROVA)
    monkeypatch.setattr(main, 'SEGRETO_WEBHOOK', main.webhook_secret(BOT_FINTO))
    main.db().close()
    _profilo_di_un_cliente(percorso, scadenza=int(time.time()) + 30 * GIORNO,
                           stato='sospeso')

    corpo = bytes(main.named_profile_csv('MARCO', token=TOKEN_DI_PROVA).body)
    assert corpo == main.empty_csv().encode('utf-8'), (
        f'il feed di un cliente SOSPESO consegna ancora il segnale: {corpo[:120]!r}')
    esito = _consegna_in_processo('-100999', MESSAGGIO)
    assert esito.get('ignored') == 'access_sospeso', (
        f'il messaggio di un cliente SOSPESO e- stato elaborato: {esito}')


def test_un_cliente_REGISTRATO_non_ha_NESSUN_feed_da_bloccare(tmp_path, monkeypatch):
    """La misura che sostiene la linea di scopo, invece di un'affermazione nel commento.

    GPT-5.5 l'ha alzata come bloccante: «`registrato` e `in_attesa` continuano a ricevere il
    feed». La frase e' vera come lettura del codice e **vuota** come rischio, e la differenza
    e' verificabile: un utente `registrato` non ha nessun feed da ricevere. Il feed di oggi e'
    per **profilo**, i profili li crea solo il proprietario (`POST /api/profiles` chiede
    `X-Admin-Token`), e il legame utente-profilo lo scrive la migrazione. Un cliente che si
    registra col Login Widget nasce quindi con `origin_profile` NULL e senza profilo: non c'e'
    niente da bloccargli.

    Cio' che la linea di scopo protegge sono gli utenti nati `registrato` **dalla migrazione**
    dai profili che il proprietario aveva giu': quelli hanno un feed che oggi funziona, e
    bloccarli sarebbe stata una regressione in produzione, non un irrigidimento.
    """
    percorso, utente, _richiesta = _cliente(tmp_path, monkeypatch, 'registrato_senza_feed.db')
    c = sqlite3.connect(percorso)
    riga = c.execute('SELECT status, origin_profile FROM users WHERE id=?',
                     (utente,)).fetchone()
    profili = c.execute('SELECT COUNT(*) FROM profiles').fetchone()[0]
    c.close()
    assert riga[0] == 'registrato', f'un cliente nuovo nasce {riga[0]!r} invece di registrato'
    assert riga[1] is None, (
        f'un cliente registrato col Login Widget ha origin_profile={riga[1]!r}: se un utente '
        'nuovo potesse arrivare a un profilo, la linea di scopo gli darebbe un feed')
    # Esiste solo il profilo PIERO, creato dal seed: nessuna rotta pubblica ne crea altri.
    assert profili == 1, f'{profili} profili invece del solo PIERO'


def test_origin_profile_e_UNIQUE_quindi_la_lettura_e_DETERMINISTICA(tmp_path, monkeypatch):
    """Rischio alzato da GPT-5.5: «`SELECT ... WHERE origin_profile=?` senza unicita'».

    Il rischio sarebbe reale — con due righe per lo stesso profilo, la `SELECT` senza
    `ORDER BY` sceglierebbe non deterministicamente fra un utente sospeso e uno attivo, e il
    feed si aprirebbe o si chiuderebbe a caso. Non lo e' perche' `users.origin_profile` e'
    dichiarato **UNIQUE** nello schema: lo stato che rende ambigua la lettura non esiste.

    Il test misura il vincolo invece di fidarsi di averlo scritto: se un domani qualcuno
    togliesse `UNIQUE` da quella colonna, questo diventa rosso e la lettura va irrigidita.
    """
    percorso = str(tmp_path / 'unique.db')
    monkeypatch.setattr(main, 'DB_PATH', percorso)
    monkeypatch.setattr(main, '_PERCORSI_MIGRATI', set())
    main.db().close()
    c = sqlite3.connect(percorso)
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO users(origin_profile, status) VALUES (?, 'attivo')",
                  (main.PIERO_PROFILE,))
    c.close()


def test_un_segnale_STANTIO_non_riemerge_quando_l_accesso_torna(tmp_path, monkeypatch):
    """Terzo rilievo di Claude Fable 5 sulla PR #26: il ramo bloccato salta la pulizia.

    L'osservazione e' esatta — mentre l'accesso e' bloccato nessuno cancella i segnali oltre
    il TTL — e la conseguenza che ne trae non lo e': il segnale stantio **non** puo' essere
    consegnato. In `profile_csv` la `DELETE` dei segnali scaduti sta **prima** della `SELECT`,
    quindi la prima richiesta dopo lo sblocco pulisce e poi legge: non c'e' un momento in cui
    una riga oltre il TTL viene servita.

    Questo test misura quell'ordine, che e' la ragione per cui il ramo bloccato puo'
    permettersi di non scrivere niente — e non scrivere e' cio' che si vuole: XTrader
    interroga il feed a raffica, e una `DELETE` con `commit` per ogni interrogazione di un
    cliente bloccato sarebbe una scrittura continua per un feed che non consegna nulla.
    """
    percorso = str(tmp_path / 'stantio.db')
    monkeypatch.setattr(main, 'DB_PATH', percorso)
    monkeypatch.setattr(main, '_PERCORSI_MIGRATI', set())
    monkeypatch.setattr(main, 'TOKEN', TOKEN_DI_PROVA)
    main.db().close()
    _profilo_di_un_cliente(percorso, scadenza=int(time.time()) - GIORNO)

    # Il segnale invecchia oltre il TTL mentre l'accesso e' bloccato.
    c = sqlite3.connect(percorso)
    c.execute("UPDATE signals SET expires_at=? WHERE profile='MARCO'",
              (int(time.time()) - 300,))
    c.commit()
    c.close()
    assert bytes(main.named_profile_csv('MARCO', token=TOKEN_DI_PROVA).body) \
        == main.empty_csv().encode('utf-8'), 'il feed bloccato ha consegnato qualcosa'

    # Il proprietario rinnova: la PRIMA richiesta dopo lo sblocco non deve consegnare il
    # segnale vecchio.
    c = sqlite3.connect(percorso)
    c.execute("UPDATE users SET access_expires_at=? WHERE origin_profile='MARCO'",
              (int(time.time()) + 30 * GIORNO,))
    c.commit()
    c.close()

    corpo = bytes(main.named_profile_csv('MARCO', token=TOKEN_DI_PROVA).body)
    assert corpo == main.empty_csv().encode('utf-8'), (
        f'un segnale oltre il TTL e- riemerso al rinnovo: {corpo[:120]!r}')
    c = sqlite3.connect(percorso)
    restano = c.execute("SELECT COUNT(*) FROM signals WHERE profile='MARCO'").fetchone()[0]
    c.close()
    assert restano == 0, f'{restano} segnali stantii sopravvivono alla prima lettura'


# ------------------------------------------------- richiesta, decisione, notifica

def _admin(tmp_path, monkeypatch, nome='admin.db'):
    """Il proprietario con una sessione, piu' un cliente registrato. Restituisce i due."""
    percorso = str(tmp_path / nome)
    monkeypatch.setattr(main, 'DB_PATH', percorso)
    monkeypatch.setattr(main, '_PERCORSI_MIGRATI', set())
    monkeypatch.setattr(main, 'BOT_TOKEN', BOT_FINTO)
    monkeypatch.setattr(main, 'SEGRETO_SESSIONE', SEGRETO_ATTESO)
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', ADMIN_FINTO)
    sessione_admin = _cookie(main.login_telegram(main.LoginTelegramIn(**_dati_login())))
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', '')
    sessione_cliente = _cookie(
        main.login_telegram(main.LoginTelegramIn(**_dati_login(id='555000555'))))
    c = sqlite3.connect(percorso)
    cliente = c.execute("SELECT id FROM users WHERE telegram_id='555000555'").fetchone()[0]
    c.close()
    return percorso, sessione_admin, sessione_cliente, cliente


def _cookie(risposta):
    """Una richiesta finta che porta il cookie di quella risposta."""
    valore = None
    for pezzo in (risposta.headers.get('set-cookie') or '').split(';'):
        chiave, _, v = pezzo.strip().partition('=')
        if chiave == main.NOME_COOKIE:
            valore = v

    class Richiesta:
        cookies = {main.NOME_COOKIE: valore}

    return Richiesta()


def _corpo(risposta):
    import json
    return json.loads(bytes(risposta.body).decode())


class _CorpoFinto:
    """Una richiesta con cookie **e** corpo JSON, per le rotte che lo leggono a mano."""

    def __init__(self, richiesta, dati):
        self.cookies = richiesta.cookies
        self._dati = dati

    async def json(self):
        return self._dati


def test_una_richiesta_DOPPIA_viene_rifiutata(tmp_path, monkeypatch):
    """Senza, un doppio clic riempie il pannello di richieste identiche.

    E con tre richieste identiche la decisione diventa «quale approvo?»: il proprietario
    concede giorni a una, le altre restano aperte, e il pannello mostra per sempre un lavoro
    da fare che non esiste.
    """
    from fastapi import HTTPException
    percorso, _admin_s, cliente_s, cliente = _admin(tmp_path, monkeypatch, 'doppia.db')
    assert _corpo(main.chiedi_accesso(cliente_s))['stato'] == 'in_attesa'
    with pytest.raises(HTTPException) as errore:
        main.chiedi_accesso(cliente_s)
    assert errore.value.status_code == 409, f'{errore.value.status_code} invece di 409'
    c = sqlite3.connect(percorso)
    quante = c.execute('SELECT COUNT(*) FROM access_requests WHERE user_id=?',
                       (cliente,)).fetchone()[0]
    c.close()
    assert quante == 1, f'{quante} richieste per lo stesso cliente'


def test_un_cliente_GIA_ATTIVO_non_puo_richiedere(tmp_path, monkeypatch):
    """Chi ha accesso non ha niente da chiedere, e il pannello non deve mostrarlo."""
    from fastapi import HTTPException
    percorso, _a, cliente_s, cliente = _admin(tmp_path, monkeypatch, 'gia_attivo.db')
    c = sqlite3.connect(percorso)
    c.execute("UPDATE users SET status='attivo', access_expires_at=? WHERE id=?",
              (int(time.time()) + 10 * GIORNO, cliente))
    c.commit()
    c.close()
    with pytest.raises(HTTPException) as errore:
        main.chiedi_accesso(cliente_s)
    assert errore.value.status_code == 409


def test_l_approvazione_CONCEDE_i_giorni_e_lo_traccia(tmp_path, monkeypatch):
    """Il giro completo: richiesta, approvazione con giorni liberi, giorni rimasti, audit."""
    import asyncio
    percorso, admin_s, cliente_s, cliente = _admin(tmp_path, monkeypatch, 'approva.db')
    monkeypatch.setattr(main, 'invia_messaggio_telegram', lambda *a, **k: (True, None))
    main.chiedi_accesso(cliente_s)

    aperte = _corpo(main.elenco_richieste(admin_s))['richieste']
    assert len(aperte) == 1 and aperte[0]['utente'] == cliente, aperte
    numero = aperte[0]['richiesta']

    esito = _corpo(asyncio.run(main.approva_richiesta(
        str(numero), _CorpoFinto(admin_s, {'giorni': 30}))))
    assert esito['notificato'] is True
    assert esito['giorni_rimasti'] == 30, f"giorni_rimasti={esito['giorni_rimasti']}"

    c = sqlite3.connect(percorso)
    riga = c.execute('SELECT status, access_expires_at FROM users WHERE id=?',
                     (cliente,)).fetchone()
    decisa = c.execute('SELECT granted_days, outcome, decided_by FROM access_requests'
                       ' WHERE id=?', (numero,)).fetchone()
    tracciato = c.execute("SELECT COUNT(*) FROM admin_audit"
                          " WHERE action='accesso_approvato' AND target_user_id=?",
                          (cliente,)).fetchone()[0]
    c.close()
    assert main.stato_effettivo(riga[0], riga[1]) == 'attivo'
    assert decisa[0] == 30 and decisa[1] == 'approvata' and decisa[2] is not None
    assert tracciato == 1, 'l-approvazione non e- tracciata in admin_audit'
    # E la richiesta non e' piu' fra quelle da decidere.
    assert _corpo(main.elenco_richieste(admin_s))['richieste'] == []


def test_l_errore_di_INVIO_non_viene_ingoiato(tmp_path, monkeypatch):
    """Trappola 1 della Issue #2, ed e' il caso normale, non l'eccezione.

    Il bot **non puo' scrivere per primo**: verso un cliente che non ha mai aperto la
    conversazione, `sendMessage` falisce. Se quell'errore venisse ingoiato si otterrebbe lo
    stato peggiore possibile — il proprietario crede di aver avvisato, il cliente non sa di
    essere attivo, e nessuno dei due ha modo di accorgersene.

    L'accesso invece **resta concesso**: e' stato deciso, e una decisione non si annulla
    perche' l'avviso non e' partito. E `telegram_reachable` va a 0, cosi' il pannello puo'
    mostrare chi va contattato a mano.
    """
    import asyncio
    percorso, admin_s, cliente_s, cliente = _admin(tmp_path, monkeypatch, 'invio_fallito.db')
    monkeypatch.setattr(main, 'invia_messaggio_telegram',
                        lambda *a, **k: (False, 'Telegram ha rifiutato la consegna'))
    main.chiedi_accesso(cliente_s)
    numero = _corpo(main.elenco_richieste(admin_s))['richieste'][0]['richiesta']

    esito = _corpo(asyncio.run(main.approva_richiesta(
        str(numero), _CorpoFinto(admin_s, {'giorni': 7}))))
    assert esito['notificato'] is False, 'un invio fallito e- stato riportato come riuscito'
    assert esito['motivo'], 'nessun motivo: il proprietario non sa perche- non e- arrivato'

    c = sqlite3.connect(percorso)
    riga = c.execute('SELECT status, access_expires_at, telegram_reachable FROM users'
                     ' WHERE id=?', (cliente,)).fetchone()
    c.close()
    assert main.stato_effettivo(riga[0], riga[1]) == 'attivo', (
        'l-accesso e- stato annullato perche- l-avviso non e- partito: sono due cose diverse')
    assert riga[2] == 0, 'telegram_reachable non e- stato azzerato'


def test_il_RIFIUTO_riporta_a_registrato_e_si_puo_richiedere(tmp_path, monkeypatch):
    """Un rifiuto non e' una punizione: chi e' rifiutato puo' chiedere di nuovo.

    `registrato` e non `sospeso` — magari il cliente non aveva ancora pagato, e la
    sospensione resta un gesto separato del proprietario.
    """
    percorso, admin_s, cliente_s, cliente = _admin(tmp_path, monkeypatch, 'rifiuta.db')
    main.chiedi_accesso(cliente_s)
    numero = _corpo(main.elenco_richieste(admin_s))['richieste'][0]['richiesta']
    esito = _corpo(main.rifiuta_richiesta(str(numero), admin_s))
    assert esito['stato'] == 'registrato'
    # E puo' richiedere: nessun 409.
    assert _corpo(main.chiedi_accesso(cliente_s))['stato'] == 'in_attesa'
    c = sqlite3.connect(percorso)
    esiti = [r[0] for r in c.execute('SELECT outcome FROM access_requests ORDER BY id')]
    c.close()
    assert esiti == ['rifiutata', None], esiti


def test_un_CLIENTE_sul_pannello_riceve_404_non_403(tmp_path, monkeypatch):
    """`404` perche' un `403` confermerebbe a un estraneo che il pannello sta li'.

    E' la stessa regola con cui un utente non vede i parser di un altro, ed e' scritta nella
    Issue #2 per `/admin/*`.
    """
    import asyncio
    from fastapi import HTTPException
    _p, _admin_s, cliente_s, _cliente = _admin(tmp_path, monkeypatch, 'pannello.db')
    with pytest.raises(HTTPException) as errore:
        main.elenco_richieste(cliente_s)
    assert errore.value.status_code == 404, f'{errore.value.status_code} invece di 404'
    with pytest.raises(HTTPException) as errore:
        asyncio.run(main.approva_richiesta('1', _CorpoFinto(cliente_s, {'giorni': 30})))
    assert errore.value.status_code == 404, (
        f'{errore.value.status_code} invece di 404: un cliente puo- dedurre che la rotta di '
        'approvazione esiste')


def test_i_giorni_FUORI_INTERVALLO_vengono_rifiutati(tmp_path, monkeypatch):
    """Il limite esiste per fermare un refuso, non per fare da listino.

    `3650` sono dieci anni: uno zero di troppo su una tastiera e' piu' probabile di un
    abbonamento decennale. E `0` o un negativo non sono una concessione.
    """
    import asyncio
    from fastapi import HTTPException
    _p, admin_s, cliente_s, _c = _admin(tmp_path, monkeypatch, 'giorni_matti.db')
    main.chiedi_accesso(cliente_s)
    numero = _corpo(main.elenco_richieste(admin_s))['richieste'][0]['richiesta']
    for giorni in (0, -5, 3651, 300000):
        with pytest.raises(HTTPException) as errore:
            asyncio.run(main.approva_richiesta(
                str(numero), _CorpoFinto(admin_s, {'giorni': giorni})))
        assert errore.value.status_code == 422, f'{giorni} giorni accettati'
