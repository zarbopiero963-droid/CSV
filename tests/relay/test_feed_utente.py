"""Un feed per utente: `/feed/{slug}.csv`, col token che vive sull'utente.

E' la PR 1 del piano sincronizzato nella Issue #2 (PR 8 del piano originale). Il
modello deciso: il feed, il token e il timer stanno sull'UTENTE; il parser
possiede solo configurazione e log. Il token esiste in chiaro una volta sola,
alla generazione; il server conserva soltanto `sha256`.

Cosa vincolano questi test, e perche' ognuno esiste:

- **il 404 uniforme.** Slug inesistente, token assente, token sbagliato, token di
  un ALTRO utente: tutti 404, mai 401 o 403. Un 401 su uno slug esistente
  direbbe a chi enumera «questo cliente esiste, cerca il token»; il 404 uniforme
  non distingue niente. E' scritto nella Issue #2: «token di un utente non apre
  il feed di un altro (404, non 403)»;
- **il token si mostra una volta.** Dopo `POST /api/me/token` il database
  contiene solo l'hash, e nessuna risposta successiva — `/api/me` compreso — lo
  restituisce di nuovo;
- **rigenerare revoca subito.** Il vecchio token smette di aprire il feed alla
  richiesta successiva, non alla scadenza di qualcosa;
- **la chiave del segnale e' l'utente.** `store_signal` scrive `user_id` e
  sostituisce la riga viva DELLO STESSO utente: e' il prerequisito del dispatch
  multi-parser (PR 2), dove due parser dello stesso utente devono contendersi
  UNA riga senza toccare quelle altrui;
- **gli alias legacy non si muovono.** `/xtrader.csv` e' l'URL configurato in
  XTrader dal profilo PIERO (regola 5): lo stesso segnale deve uscire dal
  percorso vecchio e da quello nuovo.

Lo stile e' quello di `test_accesso.py`: relay in processo con `DB_PATH`
monkeypatchato per i casi di logica, piu' un giro in sottoprocesso per i byte
HTTP veri della rotta nuova — content-type compreso, che in-processo non si
misura fino in fondo.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE))

import main  # noqa: E402 - dopo l'inserimento del percorso
from tests.ambiente import CHIAVI_PERICOLOSE, TOKEN_DI_PROVA  # noqa: E402
from tests.relay.test_csv_contract import RIGA_VALIDA  # noqa: E402
from tests.servizio import relay_avviato  # noqa: E402

# La firma del Login Widget e l'ambiente del servizio vengono da `test_login.py`
# e NON vengono ricopiati: sono la stessa cosa in tutti i test che hanno bisogno
# di una sessione, e due copie divergono al primo cambio di formato (regola 3).
from tests.relay.test_login import (  # noqa: E402
    AMBIENTE_DEL_SERVIZIO, BOT_FINTO, SEGRETO_ATTESO, _chiama,
    _cookie_dalla_risposta, _dati_login)

GIORNO = 86400


@pytest.fixture(autouse=True)
def _ambiente_pulito(monkeypatch):
    """Nessuna variabile della macchina entra in questi test.

    Stessa ragione delle fixture gemelle di `tests/relay/`: col `.env` del
    proprietario caricato, un test che avvia l'app ripunterebbe il webhook del
    bot vero e l'esito dipenderebbe dalla macchina invece che dal codice.
    """
    for chiave in CHIAVI_PERICOLOSE:
        monkeypatch.delenv(chiave, raising=False)
    monkeypatch.setattr(main, 'TOKEN', TOKEN_DI_PROVA)


def _relay_in_processo(tmp_path, monkeypatch, nome):
    """Un relay in processo su un database proprio, migrato."""
    percorso = str(tmp_path / nome)
    monkeypatch.setattr(main, 'DB_PATH', percorso)
    monkeypatch.setattr(main, '_PERCORSI_MIGRATI', set())
    monkeypatch.setattr(main, 'BOT_TOKEN', BOT_FINTO)
    monkeypatch.setattr(main, 'SEGRETO_SESSIONE', SEGRETO_ATTESO)
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', '')
    main.db().close()
    return percorso


def _utente_con_feed(percorso, slug='marco', token='xt_token-di-prova-abcdef',
                     stato='attivo', scadenza=None, admin=0):
    """Una riga di `users` con slug e token gia' armati; restituisce l'id.

    L'hash e' ricalcolato QUI con la formula attesa — `sha256` esadecimale del
    token — e non chiamando la funzione del servizio: se la calcolasse col
    codice che poi verifica, il test direbbe solo «coerente con se stesso».
    """
    c = sqlite3.connect(percorso)
    c.execute('INSERT INTO users(slug, first_name, status, access_expires_at,'
              ' is_admin, token_hash, token_prefix) VALUES (?,?,?,?,?,?,?)',
              (slug, slug.capitalize(), stato, scadenza, admin,
               hashlib.sha256(token.encode('utf-8')).hexdigest(), token[:9]))
    utente = c.execute('SELECT id FROM users WHERE slug=?', (slug,)).fetchone()[0]
    c.commit()
    c.close()
    return utente


def _segnale_di(percorso, utente, evento='Tizio v Caio', scade_fra=90):
    """Una riga viva in `signals` intestata a QUESTO utente."""
    riga = list(RIGA_VALIDA)
    riga[main.HEADERS.index('EventName')] = evento
    c = sqlite3.connect(percorso)
    c.execute('INSERT INTO signals(csv, parser, user_id, expires_at)'
              ' VALUES (?,?,?,?)',
              (main.make_csv(riga), 'parser-di-prova', utente,
               int(time.time()) + scade_fra))
    c.commit()
    c.close()


# ------------------------------------------------------------- il 404 uniforme

def test_slug_inesistente_404(tmp_path, monkeypatch):
    _relay_in_processo(tmp_path, monkeypatch, 'slug_no.db')
    with pytest.raises(main.HTTPException) as e:
        main.feed_utente_csv('nessuno', token='xt_qualunque')
    assert e.value.status_code == 404


def test_senza_token_404(tmp_path, monkeypatch):
    percorso = _relay_in_processo(tmp_path, monkeypatch, 'no_token.db')
    _utente_con_feed(percorso)
    with pytest.raises(main.HTTPException) as e:
        main.feed_utente_csv('marco', token=None)
    assert e.value.status_code == 404


def test_token_sbagliato_404(tmp_path, monkeypatch):
    percorso = _relay_in_processo(tmp_path, monkeypatch, 'token_errato.db')
    _utente_con_feed(percorso)
    with pytest.raises(main.HTTPException) as e:
        main.feed_utente_csv('marco', token='xt_sbagliato-di-proposito')
    assert e.value.status_code == 404


def test_il_token_di_un_ALTRO_utente_404(tmp_path, monkeypatch):
    """L'isolamento del feed: il token di A sullo slug di B non apre niente.

    E' il test nominato dalla Issue #2, ed e' quello che impedisce di
    implementare il controllo come «il token esiste da qualche parte».
    """
    percorso = _relay_in_processo(tmp_path, monkeypatch, 'isolamento.db')
    _utente_con_feed(percorso, slug='anna', token='xt_token-di-anna-000000')
    _utente_con_feed(percorso, slug='bruno', token='xt_token-di-bruno-11111')
    with pytest.raises(main.HTTPException) as e:
        main.feed_utente_csv('bruno', token='xt_token-di-anna-000000')
    assert e.value.status_code == 404


def test_un_utente_SENZA_token_armato_404(tmp_path, monkeypatch):
    """Slug valido ma `token_hash` NULL: non c'e' niente da confrontare, e un
    confronto con NULL che «passa» aprirebbe ogni feed non ancora armato."""
    percorso = _relay_in_processo(tmp_path, monkeypatch, 'disarmato.db')
    c = sqlite3.connect(percorso)
    c.execute("INSERT INTO users(slug, first_name, status) VALUES ('nudo','Nudo','attivo')")
    c.commit()
    c.close()
    with pytest.raises(main.HTTPException) as e:
        main.feed_utente_csv('nudo', token='xt_qualunque')
    assert e.value.status_code == 404


# ------------------------------------------------------- il feed che consegna

def test_il_feed_consegna_il_segnale_col_SUO_token(tmp_path, monkeypatch):
    percorso = _relay_in_processo(tmp_path, monkeypatch, 'consegna.db')
    utente = _utente_con_feed(percorso)
    _segnale_di(percorso, utente, 'Verona v Como')
    risposta = main.feed_utente_csv('marco', token='xt_token-di-prova-abcdef')
    assert risposta.status_code == 200
    corpo = bytes(risposta.body)
    assert corpo.startswith(main.CSV_BOM.encode('utf-8') + b'"Provider"'), (
        f'il feed per utente non comincia con BOM+intestazione: {corpo[:40]!r}')
    assert b'Verona v Como' in corpo


def test_il_TTL_vale_anche_sul_feed_per_utente(tmp_path, monkeypatch):
    percorso = _relay_in_processo(tmp_path, monkeypatch, 'ttl.db')
    utente = _utente_con_feed(percorso)
    _segnale_di(percorso, utente, 'Vecchio v Stantio', scade_fra=-5)
    corpo = bytes(main.feed_utente_csv('marco', token='xt_token-di-prova-abcdef').body)
    assert corpo == main.empty_csv().encode('utf-8'), (
        f'un segnale scaduto esce ancora dal feed per utente: {corpo[:120]!r}')


def test_il_feed_di_un_utente_SCADUTO_torna_sola_intestazione(tmp_path, monkeypatch):
    """`200` con sola intestazione, non `401`: per XTrader un errore HTTP e' un
    guasto, «nessun segnale» e' uno stato normale. E il token NON viene revocato."""
    percorso = _relay_in_processo(tmp_path, monkeypatch, 'scaduto.db')
    utente = _utente_con_feed(percorso, scadenza=int(time.time()) - GIORNO)
    _segnale_di(percorso, utente)
    risposta = main.feed_utente_csv('marco', token='xt_token-di-prova-abcdef')
    assert risposta.status_code == 200
    assert bytes(risposta.body) == main.empty_csv().encode('utf-8')
    c = sqlite3.connect(percorso)
    resta = c.execute('SELECT token_hash FROM users WHERE id=?', (utente,)).fetchone()[0]
    c.close()
    assert resta, 'la scadenza ha revocato il token: al rinnovo il cliente dovrebbe riconfigurare XTrader'


def test_i_segnali_di_due_utenti_NON_si_mescolano(tmp_path, monkeypatch):
    percorso = _relay_in_processo(tmp_path, monkeypatch, 'due_utenti.db')
    anna = _utente_con_feed(percorso, slug='anna', token='xt_token-di-anna-000000')
    bruno = _utente_con_feed(percorso, slug='bruno', token='xt_token-di-bruno-11111')
    _segnale_di(percorso, anna, 'Segnale Di Anna v Solo Suo')
    _segnale_di(percorso, bruno, 'Segnale Di Bruno v Solo Suo')
    corpo_anna = bytes(main.feed_utente_csv('anna', token='xt_token-di-anna-000000').body)
    corpo_bruno = bytes(main.feed_utente_csv('bruno', token='xt_token-di-bruno-11111').body)
    assert b'Anna' in corpo_anna and b'Bruno' not in corpo_anna
    assert b'Bruno' in corpo_bruno and b'Anna' not in corpo_bruno


# ------------------------------------------- la chiave del segnale e' l'utente

def test_store_signal_scrive_la_chiave_UTENTE(tmp_path, monkeypatch):
    """La riga scritta per il profilo PIERO porta il `user_id` del proprietario.

    La colonna e il backfill esistono dalla migrazione (PR #22); questa e' la
    meta' mancante — le righe NUOVE. Senza, il feed per utente del proprietario
    resterebbe vuoto per sempre mentre `/xtrader.csv` consegna.
    """
    percorso = _relay_in_processo(tmp_path, monkeypatch, 'chiave.db')
    c = main.db()
    main.store_signal(c, main.make_csv(RIGA_VALIDA), 'parser-finto', 'PIERO')
    c.commit()
    riga = c.execute('SELECT s.user_id, u.id FROM signals s, users u'
                     " WHERE u.origin_profile='PIERO'").fetchone()
    c.close()
    assert riga[0] is not None, 'store_signal ha scritto user_id NULL: le righe nuove non hanno la chiave utente'
    assert riga[0] == riga[1], f'user_id {riga[0]} diverso dal proprietario {riga[1]}'


def test_lo_stesso_segnale_esce_dal_percorso_VECCHIO_e_da_quello_NUOVO(tmp_path, monkeypatch):
    """`/xtrader.csv` (regola 5) e `/feed/{slug}.csv` consegnano lo stesso corpo."""
    percorso = _relay_in_processo(tmp_path, monkeypatch, 'due_percorsi.db')
    c = main.db()
    riga = list(RIGA_VALIDA)
    riga[main.HEADERS.index('EventName')] = 'Legacy v Nuovo'
    main.store_signal(c, main.make_csv(riga), 'parser-finto', 'PIERO')
    c.commit()
    c.close()
    conn = sqlite3.connect(percorso)
    conn.execute("UPDATE users SET token_hash=?, token_prefix=? WHERE origin_profile='PIERO'",
                 (hashlib.sha256(b'xt_token-del-proprietario').hexdigest(), 'xt_token-'))
    slug = conn.execute("SELECT slug FROM users WHERE origin_profile='PIERO'").fetchone()[0]
    conn.commit()
    conn.close()
    vecchio = bytes(main.xtrader_csv(token=TOKEN_DI_PROVA).body)
    nuovo = bytes(main.feed_utente_csv(slug, token='xt_token-del-proprietario').body)
    assert b'Legacy v Nuovo' in vecchio, 'l\'alias legacy ha smesso di consegnare'
    assert vecchio == nuovo, 'i due percorsi consegnano corpi diversi per lo stesso segnale'


def test_una_sola_riga_viva_per_UTENTE(tmp_path, monkeypatch):
    """Due segnali ravvicinati: il secondo sostituisce, non si accoda."""
    percorso = _relay_in_processo(tmp_path, monkeypatch, 'una_riga.db')
    c = main.db()
    main.store_signal(c, main.make_csv(RIGA_VALIDA), 'parser-finto', 'PIERO')
    main.store_signal(c, main.make_csv(RIGA_VALIDA), 'parser-finto', 'PIERO')
    c.commit()
    quante = c.execute('SELECT COUNT(*) FROM signals').fetchone()[0]
    c.close()
    assert quante == 1, f'{quante} righe vive dopo due segnali: il feed puo\' servirne una stantia'


# ------------------------------------------------------ la nascita del token

def test_generare_il_token_lo_mostra_UNA_volta_e_salva_solo_l_hash(tmp_path, monkeypatch):
    percorso = _relay_in_processo(tmp_path, monkeypatch, 'genera.db')
    risposta = main.login_telegram(main.LoginTelegramIn(**_dati_login(id='777000777')))
    cookie = None
    for pezzo in (risposta.headers.get('set-cookie') or '').split(';'):
        chiave, _, valore = pezzo.strip().partition('=')
        if chiave == main.NOME_COOKIE:
            cookie = valore

    class Richiesta:
        cookies = {main.NOME_COOKIE: cookie}

    corpo = json.loads(bytes(main.genera_token_feed(Richiesta()).body).decode())
    token = corpo['token']
    assert token.startswith('xt_'), f'il token non ha il prefisso xt_: {token[:6]}…'
    assert len(token) >= 21, "token piu' corto di xt_ + 18 byte"
    assert corpo['token_prefix'] == token[:9]
    assert corpo['feed'].endswith('.csv'), f'la risposta non dice l\'URL del feed: {corpo}'

    c = sqlite3.connect(percorso)
    salvato = c.execute("SELECT token_hash, token_prefix FROM users"
                        " WHERE telegram_id='777000777'").fetchone()
    tutto = ' '.join(str(v) for r in c.execute('SELECT * FROM users').fetchall() for v in r)
    c.close()
    assert salvato[0] == hashlib.sha256(token.encode('utf-8')).hexdigest(), (
        'il database non conserva sha256(token): o e\' in chiaro o e\' un\'altra formula')
    assert token not in tutto, 'il token in chiaro e\' finito nel database'

    # E la seconda lettura non lo restituisce: `/api/me` dice il prefisso, mai il token.
    me = json.loads(bytes(main.chi_sono(Richiesta()).body).decode())
    assert token not in json.dumps(me), '/api/me restituisce il token: doveva esistere in chiaro una volta sola'
    assert me.get('token_prefix') == token[:9]


def test_rigenerare_REVOCA_il_vecchio_subito(tmp_path, monkeypatch):
    percorso = _relay_in_processo(tmp_path, monkeypatch, 'rigenera.db')
    risposta = main.login_telegram(main.LoginTelegramIn(**_dati_login(id='888000888')))
    cookie = None
    for pezzo in (risposta.headers.get('set-cookie') or '').split(';'):
        chiave, _, valore = pezzo.strip().partition('=')
        if chiave == main.NOME_COOKIE:
            cookie = valore

    class Richiesta:
        cookies = {main.NOME_COOKIE: cookie}

    primo = json.loads(bytes(main.genera_token_feed(Richiesta()).body).decode())
    secondo = json.loads(bytes(main.genera_token_feed(Richiesta()).body).decode())
    assert primo['token'] != secondo['token']
    slug = secondo['feed'].rsplit('/', 1)[-1][:-len('.csv')]

    c = sqlite3.connect(percorso)
    c.execute("UPDATE users SET status='attivo' WHERE telegram_id='888000888'")
    c.commit()
    c.close()
    with pytest.raises(main.HTTPException) as e:
        main.feed_utente_csv(slug, token=primo['token'])
    assert e.value.status_code == 404, 'il token rigenerato non revoca il precedente subito'
    assert main.feed_utente_csv(slug, token=secondo['token']).status_code == 200


def test_generare_il_token_ASSEGNA_uno_slug_a_chi_non_lo_ha(tmp_path, monkeypatch):
    """Gli utenti nati dal login Telegram non hanno slug: senza questo passo il
    feed di un cliente nuovo non avrebbe un URL."""
    percorso = _relay_in_processo(tmp_path, monkeypatch, 'slug_nuovo.db')
    risposta = main.login_telegram(main.LoginTelegramIn(**_dati_login(id='999000999')))
    cookie = None
    for pezzo in (risposta.headers.get('set-cookie') or '').split(';'):
        chiave, _, valore = pezzo.strip().partition('=')
        if chiave == main.NOME_COOKIE:
            cookie = valore

    class Richiesta:
        cookies = {main.NOME_COOKIE: cookie}

    corpo = json.loads(bytes(main.genera_token_feed(Richiesta()).body).decode())
    c = sqlite3.connect(percorso)
    slug = c.execute("SELECT slug FROM users WHERE telegram_id='999000999'").fetchone()[0]
    c.close()
    assert slug, 'l\'utente e\' rimasto senza slug'
    assert corpo['feed'] == f'/feed/{slug}.csv'
    assert slug == slug.lower(), 'lo slug deve essere minuscolo: finisce in un URL'


def test_la_corsa_sullo_slug_RIPARTE_dalla_base_non_dal_candidato(tmp_path, monkeypatch):
    """Il retry dopo `IntegrityError` ricalcola dalla BASE, non dal candidato perso.

    Segnalato da CodeRabbit sulla PR #43: ripartendo dal candidato, una collisione
    su `base-2` produrrebbe `base-2-2` — uno slug brutto e instabile che finisce
    nell'URL che il cliente incolla in XTrader. La corsa vera (due primi-token
    simultanei) non si riproduce in processo: qui si forza il primo candidato su
    uno slug GIA' occupato, cosi' l'UPDATE solleva davvero `IntegrityError` e il
    retry deve dimostrare da dove riparte.
    """
    percorso = _relay_in_processo(tmp_path, monkeypatch, 'corsa_slug.db')
    _utente_con_feed(percorso, slug='occupato', token='xt_token-occupato-00000')
    risposta = main.login_telegram(main.LoginTelegramIn(**_dati_login(id='444000444')))
    cookie = None
    for pezzo in (risposta.headers.get('set-cookie') or '').split(';'):
        chiave, _, valore = pezzo.strip().partition('=')
        if chiave == main.NOME_COOKIE:
            cookie = valore

    class Richiesta:
        cookies = {main.NOME_COOKIE: cookie}

    vero = main._slug_libero
    basi_chieste = []

    def truccato(base, presi):
        basi_chieste.append(base)
        if len(basi_chieste) == 1:
            return 'occupato'  # gia' preso in `users` → l'UPDATE solleva IntegrityError
        return vero(base, presi)

    monkeypatch.setattr(main, '_slug_libero', truccato)
    corpo = json.loads(bytes(main.genera_token_feed(Richiesta()).body).decode())
    slug = corpo['feed'].rsplit('/', 1)[-1][:-len('.csv')]
    assert len(basi_chieste) == 2, f'atteso un retry: chiamate {basi_chieste}'
    assert basi_chieste[1] == basi_chieste[0], (
        f'il retry e\' ripartito da {basi_chieste[1]!r} invece che dalla base '
        f'{basi_chieste[0]!r}: una collisione su base-2 produrrebbe base-2-2')
    assert slug != 'occupato' and not slug.startswith('occupato-'), (
        f'lo slug finale {slug!r} deriva dal candidato perso, non dalla base')


def test_senza_sessione_NIENTE_token(tmp_path, monkeypatch):
    _relay_in_processo(tmp_path, monkeypatch, 'anonimo.db')

    class Richiesta:
        cookies = {}

    with pytest.raises(main.HTTPException) as e:
        main.genera_token_feed(Richiesta())
    assert e.value.status_code == 401


def test_il_poll_del_feed_NON_scrive_sul_database(tmp_path, monkeypatch):
    """La consegna e' una LETTURA: regge anche su un database di sola lettura.

    Bloccante di GPT-5.6 Sol sulla PR #43: il percorso del feed faceva DELETE +
    commit a ogni poll, anche a vuoto — e XTrader interroga a raffica. Su un
    utente non si vede; su N utenti sono N transazioni di scrittura al secondo
    che serializzano sul write-lock di SQLite, in contesa col webhook.

    Il test impone la proprieta' nel modo piu' duro: connessione SQLite aperta
    con `mode=ro` — non un `chmod`, che i test eseguiti da root attraverserebbero
    senza accorgersene (misurato: con `chmod 444` il vecchio codice passava,
    perche' root ignora i permessi del file). Se il percorso di consegna prova a
    scrivere, SQLite solleva «attempt to write a readonly database» e il test e'
    rosso — che e' esattamente cio' che faceva il codice precedente. La pulizia
    delle righe scadute spetta a `store_signal`, che gia' cancella per entrambe
    le chiavi alla scrittura successiva; il filtro sul TTL sta nella SELECT, e il
    test del segnale STANTIO in `test_accesso.py` vincola che una riga oltre il
    TTL non esca mai.

    Vale per ENTRAMBI i percorsi — nuovo e legacy — perche' hanno la stessa
    forma (regola 2: la classe, non il sito).
    """
    percorso = _relay_in_processo(tmp_path, monkeypatch, 'sola_lettura.db')
    utente = _utente_con_feed(percorso)
    _segnale_di(percorso, utente, 'Lettura v Pura')

    def db_sola_lettura():
        c = sqlite3.connect(f'file:{percorso}?mode=ro', uri=True)
        c.execute('PRAGMA busy_timeout = 5000')
        return c

    monkeypatch.setattr(main, 'db', db_sola_lettura)
    corpo = bytes(main.feed_utente_csv('marco', token='xt_token-di-prova-abcdef').body)
    assert b'Lettura v Pura' in corpo, (
        f'il feed per utente su database di sola lettura non consegna: {corpo[:120]!r}')
    corpo = bytes(main.xtrader_csv(token=TOKEN_DI_PROVA).body)
    assert corpo.startswith(main.CSV_BOM.encode('utf-8')), (
        f'il feed legacy su database di sola lettura non risponde: {corpo[:40]!r}')


def test_conii_CONCORRENTI_lasciano_uno_stato_coerente(tmp_path, monkeypatch):
    """Quattro conii simultanei dello stesso utente: vince l'ultimo, stato coerente.

    Dal finding di GPT-5.6 Sol sul gate finale della PR #43, verificato la' con
    questo stesso scenario e smentito come difetto: «solo l'ultimo hash resta» e'
    la semantica documentata della rotazione — due rigenerazioni IN FILA hanno la
    stessa proprieta' — e lo stato «strappato» (hash di un token, prefix di un
    altro) non puo' esistere perche' le tre colonne viaggiano in un solo UPDATE.
    Questo test tiene vincolate entrambe le cose: esattamente UN token apre il
    feed, e prefix/hash nel database appartengono a quel token.
    """
    import threading

    percorso = _relay_in_processo(tmp_path, monkeypatch, 'conii_concorrenti.db')
    risposta = main.login_telegram(main.LoginTelegramIn(**_dati_login(id='606000606')))
    cookie = None
    for pezzo in (risposta.headers.get('set-cookie') or '').split(';'):
        chiave, _, valore = pezzo.strip().partition('=')
        if chiave == main.NOME_COOKIE:
            cookie = valore

    class Richiesta:
        cookies = {main.NOME_COOKIE: cookie}

    ricevuti, errori = [], []
    via = threading.Barrier(4)

    def conia():
        try:
            via.wait()
            corpo = json.loads(bytes(main.genera_token_feed(Richiesta()).body).decode())
            ricevuti.append(corpo['token'])
        except Exception as e:  # noqa: BLE001 - il tipo dell'errore E' il risultato
            errori.append(repr(e))

    fili = [threading.Thread(target=conia) for _ in range(4)]
    for f in fili:
        f.start()
    for f in fili:
        f.join()
    assert len(ricevuti) == 4 and not errori, (ricevuti, errori)

    c = sqlite3.connect(percorso)
    c.execute("UPDATE users SET status='attivo' WHERE telegram_id='606000606'")
    c.commit()
    slug, hash_db, prefix_db = c.execute(
        "SELECT slug, token_hash, token_prefix FROM users"
        " WHERE telegram_id='606000606'").fetchone()
    c.close()

    aperti = []
    for tok in ricevuti:
        try:
            stato = main.feed_utente_csv(slug, token=tok).status_code
        except main.HTTPException as e:
            stato = e.status_code
        if stato == 200:
            aperti.append(tok)
    assert len(aperti) == 1, (
        f'{len(aperti)} token su 4 aprono il feed: la rotazione deve lasciarne UNO')
    vincente = aperti[0]
    assert hash_db == hashlib.sha256(vincente.encode('utf-8')).hexdigest()
    assert prefix_db == vincente[:9], (
        f'stato STRAPPATO: prefix {prefix_db!r} di un token, hash di un altro')


# ----------------------------------------------- i byte HTTP del percorso vero

def test_HTTP_la_rotta_nuova_serve_text_csv_col_BOM(tmp_path, monkeypatch):
    """Sottoprocesso vero: la rotta esiste, risponde `text/csv`, i BYTE hanno il
    BOM, e senza token la stessa rotta e' un 404. In-processo il content-type
    non si misura fino in fondo, e la registrazione della rotta nemmeno."""
    monkeypatch.setattr(main, 'SEGRETO_SESSIONE', SEGRETO_ATTESO)
    with relay_avviato(tmp_path, **AMBIENTE_DEL_SERVIZIO) as base:
        stato, _, headers = _chiama(base, 'POST', '/api/login/telegram',
                                    corpo=_dati_login(id='321000321'))
        assert stato == 200
        cookie = _cookie_dalla_risposta(headers)
        stato, corpo, _ = _chiama(base, 'POST', '/api/me/token', cookie=cookie)
        assert stato == 200, f'POST /api/me/token risponde {stato}: {corpo[:200]!r}'
        dati = json.loads(corpo)

        percorso = f"/feed/{dati['feed'].rsplit('/', 1)[-1]}"
        url = f"{base}{percorso}?token={dati['token']}"
        with urllib.request.urlopen(url, timeout=10) as r:  # noqa: S310 - loopback
            assert r.status == 200
            assert r.headers.get('Content-Type', '').startswith('text/csv'), (
                f"content-type {r.headers.get('Content-Type')!r}: XTrader legge un CSV")
            byte = r.read()
        assert byte.startswith(b'\xef\xbb\xbf"Provider"'), (
            f'i byte HTTP non cominciano con BOM+intestazione: {byte[:24]!r}')

        # Stessa rotta, senza token: 404, e il corpo non nomina il token atteso.
        try:
            with urllib.request.urlopen(f'{base}{percorso}', timeout=10):  # noqa: S310
                stato_nudo = 200
        except urllib.error.HTTPError as e:
            stato_nudo = e.code
            assert dati['token'] not in e.read().decode('utf-8', 'replace')
        assert stato_nudo == 404
