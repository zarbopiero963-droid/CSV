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


# ------------------------------------------------------------ il promemoria a 5 giorni

def test_il_promemoria_parte_UNA_VOLTA_per_scadenza(tmp_path, monkeypatch):
    """E la seconda volta riparte, che e' la parte che un booleano sbaglierebbe.

    `promemoria_per` conserva **quale** scadenza e' stata annunciata. Con un booleano
    «giu' avvisato», il cliente riceverebbe l'avviso al primo ciclo e **mai piu'** — e il caso
    in cui serve davvero e' il rinnovo numero cinque, non il primo.
    """
    inviati = []
    percorso, admin_s, cliente_s, cliente = _admin(tmp_path, monkeypatch, 'promemoria.db')
    monkeypatch.setattr(main, 'invia_messaggio_telegram',
                        lambda chat_id, testo, *a, **k: (inviati.append(testo), (True, None))[1])
    c = sqlite3.connect(percorso)
    c.execute("UPDATE users SET status='attivo', access_expires_at=? WHERE id=?",
              (int(time.time()) + 3 * GIORNO, cliente))
    c.commit()
    c.close()

    assert _corpo(main.manda_promemoria(admin_s))['avvisati'] == [cliente]
    assert len(inviati) == 1 and '3 giorni' in inviati[0], inviati
    # Rieseguito subito: nessun secondo avviso per la stessa scadenza.
    assert _corpo(main.manda_promemoria(admin_s))['avvisati'] == []
    assert len(inviati) == 1, f'{len(inviati)} avvisi per la stessa scadenza'

    # Il proprietario rinnova: il promemoria del ciclo NUOVO deve poter partire.
    c = sqlite3.connect(percorso)
    c.execute('UPDATE users SET access_expires_at=?, promemoria_per=NULL WHERE id=?',
              (int(time.time()) + 2 * GIORNO, cliente))
    c.commit()
    c.close()
    assert _corpo(main.manda_promemoria(admin_s))['avvisati'] == [cliente]
    assert len(inviati) == 2, 'dopo un rinnovo il promemoria non e- ripartito'


def test_il_promemoria_NON_riguarda_chi_ha_tempo_o_e_scaduto(tmp_path, monkeypatch):
    """Ne' chi ha 40 giorni davanti, ne' chi e' giu' fuori.

    Al primo l'avviso e' rumore, e il rumore insegna a ignorare gli avvisi; al secondo e'
    troppo tardi — quello che gli serve e' la schermata di accesso scaduto, non un promemoria
    di qualcosa che e' giu' successo.
    """
    inviati = []
    percorso, admin_s, cliente_s, cliente = _admin(tmp_path, monkeypatch, 'no_promemoria.db')
    monkeypatch.setattr(main, 'invia_messaggio_telegram',
                        lambda *a, **k: (inviati.append(1), (True, None))[1])
    for scadenza in (int(time.time()) + 40 * GIORNO, int(time.time()) - GIORNO):
        c = sqlite3.connect(percorso)
        c.execute("UPDATE users SET status='attivo', access_expires_at=?, promemoria_per=NULL"
                  ' WHERE id=?', (scadenza, cliente))
        c.commit()
        c.close()
        assert _corpo(main.manda_promemoria(admin_s))['avvisati'] == [], scadenza
    assert inviati == [], f'{len(inviati)} avvisi mandati a chi non li deve ricevere'


def test_un_promemoria_FALLITO_non_si_consuma(tmp_path, monkeypatch):
    """Se l'invio non parte, il giro successivo riprova.

    Il contrario di cio' che si fa col freno del login, dove il tentativo si consuma **prima**
    della verifica: la' il rischio e' che qualcuno provi troppe volte, qui il rischio e' che il
    cliente non sappia che sta scadendo. Consumare un promemoria non partito significherebbe
    perderlo per sempre proprio nel caso in cui il canale e' rotto.
    """
    percorso, admin_s, cliente_s, cliente = _admin(tmp_path, monkeypatch, 'promemoria_ko.db')
    monkeypatch.setattr(main, 'invia_messaggio_telegram', lambda *a, **k: (False, 'rifiutata'))
    c = sqlite3.connect(percorso)
    c.execute("UPDATE users SET status='attivo', access_expires_at=? WHERE id=?",
              (int(time.time()) + 2 * GIORNO, cliente))
    c.commit()
    c.close()

    esito = _corpo(main.manda_promemoria(admin_s))
    assert esito['avvisati'] == [] and esito['falliti'], esito
    c = sqlite3.connect(percorso)
    riga = c.execute('SELECT promemoria_per, telegram_reachable FROM users WHERE id=?',
                     (cliente,)).fetchone()
    c.close()
    assert riga[0] is None, (
        'il promemoria e- stato segnato come inviato anche se non e- partito: il cliente non '
        'sapra- mai che sta scadendo')
    assert riga[1] == 0, 'il cliente non e- stato segnato come non raggiungibile'


# ----------------------------------------------- il canale verso Telegram, da solo

def test_un_rifiuto_di_TELEGRAM_con_200_viene_riconosciuto(tmp_path, monkeypatch):
    """Il codice HTTP non basta: Telegram rifiuta con `200` e `ok: false`.

    Ed e' proprio la forma del caso che conta — «il bot non puo' scrivere per primo» arriva
    cosi', non come un errore di rete. Un controllo che guardasse solo lo stato HTTP direbbe
    «inviato» a ogni approvazione mai consegnata.
    """
    import io

    class RispostaFinta(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(main, 'BOT_TOKEN', BOT_FINTO)
    monkeypatch.setattr('urllib.request.urlopen',
                        lambda *a, **k: RispostaFinta(
                            b'{"ok": false, "description": "bot can\'t initiate conversation"}'))
    riuscito, motivo = main.invia_messaggio_telegram('555000555', 'ciao')
    assert riuscito is False, 'un rifiuto con HTTP 200 e- stato letto come consegna riuscita'
    assert motivo and 'rifiutato' in motivo

    monkeypatch.setattr('urllib.request.urlopen',
                        lambda *a, **k: RispostaFinta(b'{"ok": true, "result": {}}'))
    assert main.invia_messaggio_telegram('555000555', 'ciao') == (True, None)


def test_il_motivo_non_contiene_il_TOKEN_del_bot(tmp_path, monkeypatch):
    """Un `HTTPError` di Telegram fa eco all'URL, e l'URL porta il token nel percorso.

    Per questo il motivo riporta il **tipo** dell'eccezione e non il suo testo: il tipo dice al
    proprietario se e' rete o rifiuto, il testo direbbe anche il token del bot — e quel motivo
    finisce in una risposta API.
    """
    import urllib.error

    def esplode(*a, **k):
        raise urllib.error.HTTPError(
            f'https://api.telegram.org/bot{BOT_FINTO}/sendMessage', 400, 'Bad Request',
            {}, None)

    monkeypatch.setattr(main, 'BOT_TOKEN', BOT_FINTO)
    monkeypatch.setattr('urllib.request.urlopen', esplode)
    riuscito, motivo = main.invia_messaggio_telegram('555000555', 'ciao')
    assert riuscito is False
    assert BOT_FINTO not in motivo and BOT_FINTO.split(':')[1] not in motivo, (
        f'il motivo contiene il token del bot: {motivo!r}')


def test_START_da_chat_privata_rende_il_cliente_RAGGIUNGIBILE(tmp_path, monkeypatch):
    """L'unico modo di sapere che il bot puo' scrivere a qualcuno, e la sua unica eccezione.

    Telegram non offre nessun modo di CHIEDERE se il bot puo' scrivere a una persona: lo si
    scopre provando, o lo si registra quando la persona scrive. Questo ramo registra.

    Il test misura anche il confine: quella consegna **non** scrive un segnale e **non** finisce
    nel log dei messaggi. Il filtro delle chat resta quello che era — qui non si cerca nessun
    parser e non si guarda nessun profilo.
    """
    percorso, _admin_s, _cliente_s, cliente = _admin(tmp_path, monkeypatch, 'start.db')
    monkeypatch.setattr(main, 'SEGRETO_WEBHOOK', main.webhook_secret(BOT_FINTO))
    import asyncio

    class Richiesta:
        headers = {'X-Telegram-Bot-Api-Secret-Token': main.webhook_secret(BOT_FINTO)}

        async def json(self):
            return {'message': {'chat': {'id': 555000555, 'type': 'private'},
                                'from': {'id': 555000555},
                                'text': '/start accesso'}}

    esito = asyncio.run(main.telegram_webhook(Richiesta()))
    assert esito.get('start') is True, esito
    c = sqlite3.connect(percorso)
    riga = c.execute('SELECT telegram_reachable FROM users WHERE id=?', (cliente,)).fetchone()
    segnali = c.execute('SELECT COUNT(*) FROM signals').fetchone()[0]
    log = c.execute('SELECT COUNT(*) FROM message_logs').fetchone()[0]
    c.close()
    assert riga[0] == 1, 'il cliente non e- stato segnato come raggiungibile'
    assert segnali == 0, 'un /start ha scritto un segnale: il filtro delle chat e- indebolito'
    assert log == 0, 'un /start e- finito nel log dei messaggi dei canali'


def test_due_richieste_CONCORRENTI_lasciano_UNA_sola_riga_aperta(tmp_path, monkeypatch):
    """Corsa alzata indipendentemente da Claude Fable 5 e GPT-5.5 sulla PR #26.

    Lo stato veniva letto dalla sessione **prima** della transazione: fra quella lettura e
    l'inserimento c'e' spazio per un altro clic, quindi due richieste concorrenti passavano
    entrambe il controllo e inserivano due righe aperte — il caso che il `409` esiste per
    impedire. E' la stessa corsa SELECT-poi-INSERT del login che la PR #24 ha chiuso, ripetuta
    da me qui: la classe di difetto non e' stata cercata, e questo test la fissa.

    Il test pretende **una** riga aperta e almeno un rifiuto, non sei successi: sotto contesa il
    comportamento giusto e' che uno vinca e gli altri sappiano di aver perso.
    """
    import threading
    percorso, _admin_s, cliente_s, cliente = _admin(tmp_path, monkeypatch, 'corsa.db')
    esiti = []
    porta = threading.Barrier(6)

    def prova():
        porta.wait()
        try:
            main.chiedi_accesso(cliente_s)
            esiti.append('ok')
        except Exception as e:
            esiti.append(getattr(e, 'status_code', type(e).__name__))

    fili = [threading.Thread(target=prova) for _ in range(6)]
    for f in fili:
        f.start()
    for f in fili:
        f.join(timeout=30)
    vivi = [f for f in fili if f.is_alive()]
    assert not vivi, f'{len(vivi)} thread non hanno finito'
    assert len(esiti) == len(fili), f'{len(esiti)} esiti per {len(fili)} richieste'

    c = sqlite3.connect(percorso)
    aperte = c.execute('SELECT COUNT(*) FROM access_requests'
                       ' WHERE user_id=? AND decided_at IS NULL', (cliente,)).fetchone()[0]
    c.close()
    assert aperte == 1, (
        f'{aperte} richieste aperte per lo stesso cliente: sei clic insieme hanno aggirato il '
        f'409. Esiti: {esiti}')
    assert esiti.count('ok') == 1, f'{esiti.count("ok")} richieste riuscite invece di una'


def test_un_database_della_PR22_riceve_la_colonna_del_promemoria(tmp_path, monkeypatch):
    """Chiesto da GPT-5.5 sulla PR #26: la migrazione su un database che esiste giu'.

    `users` e' nata nella PR #22, quindi in produzione la tabella **esiste** e un
    `CREATE TABLE IF NOT EXISTS` non le aggiunge niente: senza la voce in
    `COLONNE_MULTIUTENTE`, `promemoria_per` non esisterebbe e la prima approvazione
    risponderebbe 500 con «no such column». Il test simula quel database creando la tabella
    **senza** la colonna nuova, poi fa girare la migrazione.
    """
    percorso = str(tmp_path / 'vecchio.db')
    c = sqlite3.connect(percorso)
    c.execute('CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT,'
              ' origin_profile TEXT UNIQUE, telegram_id TEXT UNIQUE, username TEXT,'
              ' first_name TEXT, slug TEXT UNIQUE, token_hash TEXT, token_prefix TEXT,'
              " status TEXT NOT NULL DEFAULT 'registrato', access_expires_at INTEGER,"
              ' telegram_reachable INTEGER NOT NULL DEFAULT 0,'
              ' session_version INTEGER NOT NULL DEFAULT 1,'
              ' is_admin INTEGER NOT NULL DEFAULT 0, created_at DATETIME)')
    c.execute("INSERT INTO users(telegram_id, status) VALUES ('555000555','attivo')")
    c.commit()
    c.close()
    assert 'promemoria_per' not in _colonne(percorso, 'users'), 'il test non parte dal vecchio'

    monkeypatch.setattr(main, 'DB_PATH', percorso)
    monkeypatch.setattr(main, '_PERCORSI_MIGRATI', set())
    main.db().close()

    assert 'promemoria_per' in _colonne(percorso, 'users'), (
        'la migrazione non aggiunge promemoria_per a un database che esiste giu-: la prima '
        'approvazione risponderebbe 500 con «no such column»')
    c = sqlite3.connect(percorso)
    resta = c.execute("SELECT status FROM users WHERE telegram_id='555000555'").fetchone()[0]
    c.close()
    assert resta == 'attivo', 'la migrazione ha alterato una riga che esisteva giu-'


def _colonne(percorso, tabella):
    c = sqlite3.connect(percorso)
    nomi = {r[1] for r in c.execute(f'PRAGMA table_info({tabella})')}
    c.close()
    return nomi


def test_due_giri_di_promemoria_CONCORRENTI_mandano_UN_solo_avviso(tmp_path, monkeypatch):
    """Alzata da Claude Fable 5 e GPT-5.5 sulla PR #26: SELECT, invio, UPDATE non atomici.

    Due chiamate parallele leggevano gli stessi candidati e mandavano **due** avvisi per la
    stessa scadenza. Ora la prenotazione precede l'invio e porta nella `WHERE` il valore che si
    aspetta di trovare, quindi solo una delle due tocca la riga: chi trova `rowcount == 0` ha
    perso la corsa e passa oltre.

    L'invio e' rallentato di proposito: senza attesa, i due giri si serializzerebbero da soli e
    il test misurerebbe la fortuna invece del meccanismo — lo stesso errore che avevo fatto col
    test a sei thread sulla revoca nella PR #24.
    """
    import threading
    inviati = []
    percorso, admin_s, _cliente_s, cliente = _admin(tmp_path, monkeypatch, 'promemoria_corsa.db')

    def invio_lento(chat_id, testo, *a, **k):
        time.sleep(0.2)
        inviati.append(testo)
        return True, None

    monkeypatch.setattr(main, 'invia_messaggio_telegram', invio_lento)
    c = sqlite3.connect(percorso)
    c.execute("UPDATE users SET status='attivo', access_expires_at=? WHERE id=?",
              (int(time.time()) + 2 * GIORNO, cliente))
    c.commit()
    c.close()

    porta = threading.Barrier(2)
    esiti = []

    def giro():
        porta.wait()
        esiti.append(_corpo(main.manda_promemoria(admin_s))['avvisati'])

    fili = [threading.Thread(target=giro) for _ in range(2)]
    for f in fili:
        f.start()
    for f in fili:
        f.join(timeout=30)
    assert not [f for f in fili if f.is_alive()], 'un giro non ha finito'
    assert len(esiti) == 2, f'{len(esiti)} esiti per 2 giri'

    assert len(inviati) == 1, (
        f'{len(inviati)} avvisi per la stessa scadenza: due giri concorrenti hanno letto gli '
        'stessi candidati e mandato ognuno il proprio')
    assert sorted(len(e) for e in esiti) == [0, 1], f'esiti: {esiti}'


def test_il_promemoria_non_TIENE_il_lock_durante_la_rete(tmp_path, monkeypatch):
    """Bloccante di Claude Fable 5 sulla PR #26, e il sintomo sarebbe il feed fermo.

    La prima versione scriveva dentro il ciclo e faceva un `commit` solo alla fine: con dieci
    secondi di timeout per utente, SQLite teneva il lock di **scrittura** per tutto il giro, e
    in quel tempo webhook e feed rispondevano «database is locked». Un promemoria che congela il
    feed e' molto peggio di un promemoria mancato.

    Il test misura la proprieta' che lo esclude: **mentre** un invio e' in corso, un'altra
    connessione riesce a scrivere. Con la transazione aperta, questa `INSERT` fallirebbe.
    """
    import threading
    percorso, admin_s, _cliente_s, cliente = _admin(tmp_path, monkeypatch, 'promemoria_lock.db')
    scritture = []

    def invio_che_controlla(chat_id, testo, *a, **k):
        altra = sqlite3.connect(percorso, timeout=1)
        try:
            altra.execute('INSERT INTO message_logs(user_id, text, esito)'
                          " VALUES (?, 'durante l-invio', 'ok')", (cliente,))
            altra.commit()
            scritture.append(True)
        except sqlite3.OperationalError as e:
            scritture.append(str(e))
        finally:
            altra.close()
        return True, None

    monkeypatch.setattr(main, 'invia_messaggio_telegram', invio_che_controlla)
    c = sqlite3.connect(percorso)
    c.execute("UPDATE users SET status='attivo', access_expires_at=? WHERE id=?",
              (int(time.time()) + 2 * GIORNO, cliente))
    c.commit()
    c.close()

    main.manda_promemoria(admin_s)
    assert scritture == [True], (
        f'un-altra connessione non ha potuto scrivere durante l-invio: {scritture}. Il giro dei '
        'promemoria tiene aperta una transazione di scrittura, quindi congela feed e webhook')


def test_una_richiesta_persa_per_l_INDICE_da_409_non_500(tmp_path, monkeypatch):
    """Bloccante di Claude Fable 5 sulla PR #26: con due worker l'indice e' l'unico arbitro.

    La rilettura dentro `BEGIN IMMEDIATE` copre un processo solo. Con due — due worker uvicorn,
    o un domani due istanze — la seconda `INSERT` viola `richiesta_aperta_unica` e sollevava
    `IntegrityError`, cioe' **500** su un doppio clic. Non e' un guasto: e' la corsa persa, e la
    risposta e' la stessa che daremmo avendola vista noi.

    Lo stato di partenza e' quello che l'interleaving produce, costruito direttamente invece di
    simulato con una patch: una richiesta **aperta** in tabella mentre `users.status` e' ancora
    `registrato`. E' lo stato che si ottiene quando l'altro worker ha fatto l'`INSERT` e non
    ancora la `UPDATE`, ed e' anche lo stato in cui si trova un database su cui qualcuno ha
    scritto a mano — quindi il test copre due cose con un caso solo.
    """
    from fastapi import HTTPException
    percorso, _admin_s, cliente_s, cliente = _admin(tmp_path, monkeypatch, 'indice_409.db')
    c = sqlite3.connect(percorso)
    c.execute('INSERT INTO access_requests(user_id) VALUES (?)', (cliente,))
    c.commit()
    stato = c.execute('SELECT status FROM users WHERE id=?', (cliente,)).fetchone()[0]
    c.close()
    assert stato == 'registrato', (
        f'lo stato e- giu- {stato!r}: il test non riproduce l-incoerenza che vuole misurare')

    with pytest.raises(HTTPException) as errore:
        main.chiedi_accesso(cliente_s)
    assert errore.value.status_code == 409, (
        f'{errore.value.status_code} invece di 409: la corsa persa contro un altro worker '
        'diventa un errore del server su un doppio clic')

    c = sqlite3.connect(percorso)
    aperte = c.execute('SELECT COUNT(*) FROM access_requests WHERE user_id=?',
                       (cliente,)).fetchone()[0]
    c.close()
    assert aperte == 1, f'{aperte} richieste per lo stesso cliente'


def test_la_MIGRAZIONE_non_muore_sui_duplicati_che_deve_correggere(tmp_path, monkeypatch):
    """Bloccante di Claude Fable 5 sulla PR #26, e la conseguenza era il servizio giu'.

    `CREATE UNIQUE INDEX` su dati che lo violano **solleva**. Quell'istruzione sta dentro
    `migra()`, che gira dentro `db()`: sollevare li' significa che `db()` non torna piu' e il
    servizio non risponde a **nessuna** richiesta, feed compreso. Cioe' il vincolo aggiunto per
    correggere un difetto avrebbe ucciso il servizio proprio sui database che quel difetto ha
    prodotto.

    Il test parte da quello stato — due richieste aperte per lo stesso utente — e pretende che
    la migrazione lo sistemi invece di morirci sopra: la piu' vecchia resta aperta (e' quella che
    l'utente ha fatto davvero), le altre vengono chiuse come `duplicata` e **non** cancellate,
    cosi' il proprietario vede cosa e' successo.
    """
    percorso = str(tmp_path / 'duplicati.db')
    monkeypatch.setattr(main, 'DB_PATH', percorso)
    monkeypatch.setattr(main, '_PERCORSI_MIGRATI', set())
    main.db().close()

    c = sqlite3.connect(percorso)
    c.execute("INSERT INTO users(telegram_id, status) VALUES ('555000555','in_attesa')")
    utente = c.execute("SELECT id FROM users WHERE telegram_id='555000555'").fetchone()[0]
    c.execute('DROP INDEX richiesta_aperta_unica')
    for _ in range(3):
        c.execute('INSERT INTO access_requests(user_id) VALUES (?)', (utente,))
    c.commit()
    aperte = c.execute('SELECT COUNT(*) FROM access_requests WHERE decided_at IS NULL'
                       ).fetchone()[0]
    c.close()
    assert aperte == 3, 'il test non parte dallo stato che vuole misurare'

    # La migrazione rigira, come farebbe al riavvio del container.
    monkeypatch.setattr(main, '_PERCORSI_MIGRATI', set())
    main.db().close()   # non deve sollevare

    c = sqlite3.connect(percorso)
    restano = c.execute('SELECT COUNT(*) FROM access_requests WHERE decided_at IS NULL'
                        ).fetchone()[0]
    chiuse = c.execute("SELECT COUNT(*) FROM access_requests WHERE outcome='duplicata'"
                       ).fetchone()[0]
    tenuta = c.execute('SELECT MIN(id) FROM access_requests WHERE decided_at IS NULL'
                       ).fetchone()[0]
    tutte = c.execute('SELECT COUNT(*) FROM access_requests').fetchone()[0]
    indice = c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='index'"
                       " AND name='richiesta_aperta_unica'").fetchone()[0]
    c.close()
    assert restano == 1, f'{restano} richieste aperte dopo la migrazione'
    assert chiuse == 2, f'{chiuse} chiuse come duplicata invece di 2'
    assert tenuta == 1, f'la richiesta tenuta e- la {tenuta}, non la piu- vecchia'
    assert tutte == 3, f'{tutte} righe: la migrazione ha CANCELLATO invece di chiudere'
    assert indice == 1, 'l-indice non e- stato creato dopo la deduplica'


def test_il_409_dell_INDICE_non_lascia_appeso_il_lock(tmp_path, monkeypatch):
    """Verifica chiesta da Claude Fable 5 sulla PR #26, e vale trasformarla in misura.

    Il `409` nasce da un `IntegrityError` **dentro** `BEGIN IMMEDIATE`: se quel percorso non
    facesse `rollback`, il lock di scrittura resterebbe appeso e da quel momento feed e webhook
    risponderebbero «database is locked» — un doppio clic di un cliente fermerebbe il servizio.

    Nel codice il `raise` sta dentro il `try` che fa `rollback` e `close`, quindi la proprieta'
    c'e'. Ma «c'e' se leggo bene» non e' una misura: questo test scrive da un'ALTRA connessione
    subito dopo il rifiuto, e se il lock fosse appeso non riuscirebbe.
    """
    from fastapi import HTTPException
    percorso, _admin_s, cliente_s, cliente = _admin(tmp_path, monkeypatch, 'lock_409.db')
    c = sqlite3.connect(percorso)
    c.execute('INSERT INTO access_requests(user_id) VALUES (?)', (cliente,))
    c.commit()
    c.close()

    with pytest.raises(HTTPException) as errore:
        main.chiedi_accesso(cliente_s)
    assert errore.value.status_code == 409

    altra = sqlite3.connect(percorso, timeout=1)
    try:
        altra.execute("INSERT INTO message_logs(user_id, text, esito)"
                      " VALUES (?, 'dopo il 409', 'ok')", (cliente,))
        altra.commit()
    except sqlite3.OperationalError as e:
        raise AssertionError(
            f'dopo il 409 il database e- ancora bloccato ({e}): il rifiuto ha lasciato appesa '
            'una transazione di scrittura, quindi un doppio clic ferma feed e webhook') from e
    finally:
        altra.close()


def test_un_RINNOVO_durante_l_invio_non_perde_la_prenotazione_nuova(tmp_path, monkeypatch):
    """Rilievo di GPT-5.5 sulla PR #26: il rilascio deve riguardare **la nostra** prenotazione.

    Scenario: prenotiamo il promemoria per la scadenza X, l'invio va male, e nel frattempo il
    proprietario ha rinnovato — quindi `promemoria_per` porta ormai la scadenza Y. Un rilascio
    scritto come `SET promemoria_per=NULL WHERE id=?` cancellerebbe **Y**, e il giro successivo
    rimanderebbe un avviso per un ciclo di cui il cliente e' giu' stato avvisato.

    Il test fa avvenire il rinnovo **durante** l'invio, che e' l'unico momento in cui la finestra
    esiste.
    """
    percorso, admin_s, _cliente_s, cliente = _admin(tmp_path, monkeypatch, 'rinnovo_invio.db')
    nuova = int(time.time()) + 90 * GIORNO

    def invio_con_rinnovo(chat_id, testo, *a, **k):
        altra = sqlite3.connect(percorso, timeout=5)
        altra.execute('UPDATE users SET access_expires_at=?, promemoria_per=? WHERE id=?',
                      (nuova, nuova, cliente))
        altra.commit()
        altra.close()
        return False, 'rifiutata'

    monkeypatch.setattr(main, 'invia_messaggio_telegram', invio_con_rinnovo)
    c = sqlite3.connect(percorso)
    c.execute("UPDATE users SET status='attivo', access_expires_at=? WHERE id=?",
              (int(time.time()) + 2 * GIORNO, cliente))
    c.commit()
    c.close()

    main.manda_promemoria(admin_s)

    c = sqlite3.connect(percorso)
    resta = c.execute('SELECT promemoria_per FROM users WHERE id=?', (cliente,)).fetchone()[0]
    c.close()
    assert resta == nuova, (
        f'promemoria_per e- {resta!r} invece della prenotazione nuova ({nuova}): il rilascio ha '
        'cancellato la prenotazione di un ciclo piu- recente')
