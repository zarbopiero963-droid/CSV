"""La verifica delle chat col codice usa-e-getta — 3.2 della Issue #32.

**Cosa manca oggi a un cliente, ed e' il motivo per cui questo file esiste.** La
web app dichiara, testualmente: *«La verifica delle chat con il codice usa-e-getta
arriva con uno dei prossimi aggiornamenti. Oggi le chat autorizzate le collega
l'amministratore.»* Ed e' vero: l'unico percorso e' `/api/profiles` con l'admin
token, cioe' il proprietario a mano. Un cliente non puo' autorizzare da solo il
canale da cui arrivano i suoi segnali.

**Il meccanismo, e perche' e' fatto cosi'.** L'utente chiede un codice dalla web
app; lo incolla NEL CANALE che vuole autorizzare; il webhook lo riconosce e
registra la chat come sua. Incollarlo nel canale **e' la prova**: chi non puo'
scrivere li' dentro non puo' autorizzarlo. Nessun altro passaggio lo dimostra.

**Il pericolo, che questi test tengono.** `CLAUDE.md` elenca il filtro delle chat
fra le aree da non indebolire: un messaggio da una chat non associata va ignorato,
e *«l'unica eccezione prevista e' un codice di verifica valido»*. Questo file
verifica che l'eccezione sia **esattamente** quella e niente di piu':

- il ramo del codice non tocca `signals`, non cerca parser, non scrive nel feed;
- un codice scaduto, gia' consumato o inventato non registra niente;
- una chat gia' di un altro utente **non e' rubabile**;
- `user_id` viene sempre dalla sessione, e le chat di un altro danno 404 (non 403,
  che confermerebbe l'esistenza).

I test parlano al servizio vero via HTTP, con due sessioni distinte: l'isolamento
si misura con due utenti reali, non con una funzione chiamata due volte.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE))

import main  # noqa: E402 - dopo l'inserimento del percorso
from tests.ambiente import CHIAVI_PERICOLOSE, TOKEN_DI_PROVA  # noqa: E402
from tests.servizio import relay_avviato  # noqa: E402

BOT_FINTO = '123456789:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'
ADMIN_FINTO = '987654321'
CLIENTE_A = '555000111'
CLIENTE_B = '555000222'
PROXY_MORTO = 'http://127.0.0.1:1'

CANALE_A = '-1002000000101'
CANALE_B = '-1002000000102'

SEGRETO_ATTESO = hashlib.sha256(('betrelay-sessione-v1:' + BOT_FINTO).encode()).hexdigest()

AMBIENTE_DEL_SERVIZIO = {
    'CSV_ACCESS_TOKEN': TOKEN_DI_PROVA,
    'TELEGRAM_BOT_TOKEN': BOT_FINTO,
    'TELEGRAM_ADMIN_ID': ADMIN_FINTO,
    'PUBLIC_URL': 'https://non-esiste.invalid',
    'HTTPS_PROXY': PROXY_MORTO,
    'https_proxy': PROXY_MORTO,
}

# `EventName` LEGGE dal messaggio, e non e' un dettaglio di comodo: una config
# con le quattro obbligatorie tutte costanti viene rifiutata dal motore —
# scriverebbe la stessa scommessa per qualunque messaggio. Misurato scrivendo
# prima questo file con soli valori fissi: il dispatch arrivava fino in fondo e
# scartava con quel motivo, che e' la guardia delle #39/#41 che fa il suo lavoro.
CONFIG_OK = {
    'match': {'type': 'contains', 'value': 'SEGNALE'},
    'columns': {
        'EventName': {'source': 'line', 'anchor': 'evento', 'part': 'after',
                      'marker': ':', 'transforms': [{'op': 'trim'}]},
        'MarketType': {'source': 'constant', 'value': 'OVER_UNDER_15'},
        'SelectionName': {'source': 'constant', 'value': 'Over 1,5 goal'},
        'BetType': {'source': 'constant', 'value': 'PUNTA'},
    },
}

MESSAGGIO_VALIDO = 'SEGNALE\nevento: SQUADRA-A v SQUADRA-B'


@pytest.fixture(autouse=True)
def _ambiente_pulito(monkeypatch):
    for chiave in CHIAVI_PERICOLOSE:
        monkeypatch.delenv(chiave, raising=False)
    monkeypatch.setattr(main, 'TOKEN', TOKEN_DI_PROVA)


@pytest.fixture
def servizio(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'SEGRETO_SESSIONE', SEGRETO_ATTESO)
    with relay_avviato(tmp_path, **AMBIENTE_DEL_SERVIZIO) as base:
        yield base, tmp_path / 'signals.db'


# ------------------------------------------------------------- utilita' HTTP

def _firma_telegram(campi: dict, bot_token: str = BOT_FINTO) -> str:
    stringa = '\n'.join(f'{k}={campi[k]}' for k in sorted(campi) if k != 'hash')
    chiave = hashlib.sha256(bot_token.encode()).digest()
    return hmac.new(chiave, stringa.encode(), hashlib.sha256).hexdigest()


def _dati_login(**extra) -> dict:
    campi = {'id': ADMIN_FINTO, 'first_name': 'Piero', 'username': 'piero',
             'auth_date': str(int(time.time()))}
    campi.update(extra)
    campi['hash'] = _firma_telegram(campi)
    return campi


def _chiama(base, metodo, path, corpo=None, cookie=None):
    dati = json.dumps(corpo).encode() if corpo is not None else None
    intestazioni = {}
    if dati:
        intestazioni['Content-Type'] = 'application/json'
    if cookie:
        intestazioni['Cookie'] = f'{main.NOME_COOKIE}={cookie}'
    req = urllib.request.Request(f'{base}{path}', data=dati,
                                 headers=intestazioni, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 - loopback
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def _cookie_dalla_risposta(intestazioni):
    for grezzo in (intestazioni.get_all('Set-Cookie') or []):
        for pezzo in grezzo.split(';'):
            chiave, _, valore = pezzo.strip().partition('=')
            if chiave == main.NOME_COOKIE:
                return valore
    return None


def _login(base, **extra):
    stato, corpo, intestazioni = _chiama(base, 'POST', '/api/login/telegram',
                                         corpo=_dati_login(**extra))
    assert stato == 200, corpo
    return _cookie_dalla_risposta(intestazioni), json.loads(corpo)['utente']


def _attiva(percorso_db, telegram_id):
    """Attiva un utente a database, come farebbe il proprietario dal pannello.

    Serve perche' collegare una chat richiede un accesso ATTIVO: `registrato` non
    basta piu' (vedi `test_un_utente_REGISTRATO_non_produce_segnali...`). I test
    che esercitano il giro completo devono quindi partire da un utente attivato,
    che e' anche lo scenario reale — un cliente collega il canale dopo essere
    stato approvato, non prima.

    `access_expires_at` a NULL significa «senza scadenza»: e' l'unico valore che
    `stato_effettivo` tratta cosi', e va bene per un test.
    """
    c = sqlite3.connect(percorso_db)
    c.execute("UPDATE users SET status='attivo', access_expires_at=NULL"
              ' WHERE telegram_id=?', (telegram_id,))
    c.commit()
    c.close()


def _login_a(base, percorso_db):
    """Cliente A, gia' ATTIVATO: collegare una chat lo richiede."""
    esito = _login(base, id=CLIENTE_A, first_name='ClienteA', username='clientea')
    _attiva(percorso_db, CLIENTE_A)
    return esito


def _login_b(base, percorso_db):
    esito = _login(base, id=CLIENTE_B, first_name='ClienteB', username='clienteb')
    _attiva(percorso_db, CLIENTE_B)
    return esito


def _consegna(base, chat, testo):
    """Una consegna di Telegram autentica: col segreto derivato dal bot."""
    payload = {'message': {'chat': {'id': int(chat)}, 'text': testo}}
    req = urllib.request.Request(
        f'{base}/telegram/webhook', data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json',
                 'X-Telegram-Bot-Api-Secret-Token': main.webhook_secret(BOT_FINTO)},
        method='POST')
    try:
        with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 - loopback
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _codice(base, cookie):
    """Chiede un codice di verifica e lo restituisce in chiaro."""
    stato, corpo, _ = _chiama(base, 'POST', '/api/chats/verify/start', cookie=cookie)
    assert stato == 200, corpo
    return json.loads(corpo)['codice']


def _chats(base, cookie):
    stato, corpo, _ = _chiama(base, 'GET', '/api/chats', cookie=cookie)
    assert stato == 200, corpo
    return json.loads(corpo)


def _verifica(base, cookie, chat=CANALE_A):
    """Il giro completo: codice → incollato nel canale → chat registrata."""
    codice = _codice(base, cookie)
    stato, corpo = _consegna(base, chat, codice)
    assert stato == 200, corpo
    return codice, corpo


# --------------------------------------------------- il giro che deve funzionare

def test_il_codice_incollato_nel_canale_registra_la_chat(servizio):
    """Il percorso felice, e la sola cosa che dimostra il controllo del canale.

    Nessun passaggio manuale del proprietario: l'utente chiede il codice, lo
    incolla nel canale, e la chat compare fra le sue. E' la funzione che oggi
    manca, e per cui la UI dice «arriva con uno dei prossimi aggiornamenti».
    """
    base, percorso_db = servizio
    cookie, _ = _login_a(base, percorso_db)
    assert _chats(base, cookie) == [], 'un utente nuovo non ha chat'

    _verifica(base, cookie)

    chats = _chats(base, cookie)
    assert len(chats) == 1, chats
    assert chats[0]['telegram_chat_id'] == CANALE_A
    assert chats[0]['verified_at'], 'la chat risulta verificata'


def test_riverificare_la_stessa_chat_non_la_duplica_e_non_rompe_i_link(servizio):
    """Il caso che il vincolo UNIQUE **non** copre da solo, quindi va misurato.

    `chats` ha `UNIQUE (telegram_chat_id, message_thread_id)`, ma la seconda
    colonna resta NULL — e in SQLite due NULL sono distinti, quindi quel vincolo
    NON impedisce due righe per lo stesso canale. A tenerlo e' il controllo di
    esistenza dentro la transazione, che e' codice e non uno schema: senza un
    test, un domani lo si toglie credendo che il vincolo basti.

    E i link non devono saltare: un utente che riverifica per rinfrescare la
    scadenza perderebbe altrimenti l'associazione ai propri parser, cioe' i
    segnali, senza nessun errore. Chiesto da GPT-5.5 sul primo giro di gate.
    """
    base, percorso_db = servizio
    cookie, _ = _login(base)
    _verifica(base, cookie, chat=CANALE_A)
    id_chat = _chats(base, cookie)[0]['id']
    slug = _crea_parser(base, cookie)
    _chiama(base, 'PUT', f'/api/me/parsers/{slug}/chats', cookie=cookie,
            corpo={'chat_ids': [id_chat]})

    _verifica(base, cookie, chat=CANALE_A)

    chats = _chats(base, cookie)
    assert len(chats) == 1, f'la riverifica ha duplicato la chat: {chats}'
    assert chats[0]['id'] == id_chat, 'la riverifica ha cambiato identita- alla chat'

    c = sqlite3.connect(percorso_db)
    righe = c.execute('SELECT COUNT(*) FROM chats WHERE telegram_chat_id=?',
                      (CANALE_A,)).fetchone()[0]
    link = c.execute('SELECT COUNT(*) FROM parser_chats WHERE chat_id=?',
                     (id_chat,)).fetchone()[0]
    c.close()
    assert righe == 1, f'{righe} righe in `chats` per lo stesso canale'
    assert link == 1, 'la riverifica ha perso il link al parser'


def test_lo_stato_della_verifica_dice_quando_e_arrivata(servizio):
    """La web app deve poter chiedere «e' arrivato?» senza ricaricare tutto.

    E lo stato **non ripete il codice**: chi l'ha chiesto ce l'ha gia', e
    ripeterlo a ogni sondaggio lo moltiplica nei log del server e nella
    cronologia del browser. Il codice esiste in chiaro una volta sola, come il
    token del feed.
    """
    base, percorso_db = servizio
    cookie, _ = _login_a(base, percorso_db)
    codice = _codice(base, cookie)

    stato, corpo, _ = _chiama(base, 'GET', '/api/chats/verify/status', cookie=cookie)
    assert stato == 200, corpo
    prima = json.loads(corpo)
    assert prima['in_attesa'] is True
    assert prima.get('chat') is None
    assert codice not in corpo.decode('utf-8'), (
        'lo stato ripete il codice in chiaro a ogni sondaggio'
    )

    _consegna(base, CANALE_A, codice)

    stato, corpo, _ = _chiama(base, 'GET', '/api/chats/verify/status', cookie=cookie)
    assert stato == 200, corpo
    dopo = json.loads(corpo)
    assert dopo['in_attesa'] is False
    assert (dopo.get('chat') or {}).get('telegram_chat_id') == CANALE_A


# ------------------------------------ il filtro delle chat NON si indebolisce

def test_un_messaggio_qualsiasi_da_una_chat_sconosciuta_resta_ignorato(servizio):
    """La regola non negoziabile: solo il CODICE e' l'eccezione, non il canale.

    Se registrare la chat bastasse a un messaggio qualunque, il codice sarebbe
    decorazione e il filtro delle chat sarebbe caduto.
    """
    base, percorso_db = servizio
    cookie, _ = _login_a(base, percorso_db)

    stato, corpo = _consegna(base, CANALE_A, 'SEGNALE qualunque, senza codice')
    assert stato == 200, corpo

    assert _chats(base, cookie) == [], (
        'un messaggio senza codice ha registrato la chat: il filtro delle chat '
        'e- caduto e chiunque conosca un chat_id puo- entrare'
    )


def test_un_codice_inventato_non_registra_niente(servizio):
    base, percorso_db = servizio
    cookie, _ = _login_a(base, percorso_db)
    _codice(base, cookie)   # ne esiste uno valido, ma non e' questo

    _consegna(base, CANALE_A, 'BETRELAY-ZZZZZZZZ')

    assert _chats(base, cookie) == [], 'un codice inventato ha registrato la chat'


def test_un_codice_gia_consumato_non_vale_una_seconda_volta(servizio):
    """Usa-e-getta: la seconda chat NON entra col codice della prima.

    E' lo scenario del codice ricopiato — una persona che lo incolla in due
    canali, o un estraneo che lo legge nel primo canale e lo rilancia nel suo.
    """
    base, percorso_db = servizio
    cookie, _ = _login_a(base, percorso_db)
    codice, _ = _verifica(base, cookie, chat=CANALE_A)

    _consegna(base, CANALE_B, codice)

    chats = _chats(base, cookie)
    assert [c['telegram_chat_id'] for c in chats] == [CANALE_A], (
        f'il codice ha funzionato due volte: {chats}'
    )


def test_un_codice_scaduto_non_registra_niente(servizio):
    """La scadenza si misura invecchiando la riga, non aspettando davvero."""
    base, percorso_db = servizio
    cookie, _ = _login_a(base, percorso_db)
    codice = _codice(base, cookie)

    c = sqlite3.connect(percorso_db)
    c.execute('UPDATE chat_verifications SET expires_at=? WHERE code=?',
              (int(time.time()) - 1, codice))
    c.commit()
    c.close()

    _consegna(base, CANALE_A, codice)

    assert _chats(base, cookie) == [], 'un codice scaduto ha registrato la chat'


def test_il_codice_non_apre_il_feed(servizio):
    """Il ramo del codice registra una chat e NIENTE altro.

    E' la promessa che rende accettabile l'eccezione al filtro: il codice non e'
    un percorso di scrittura verso i segnali che XTrader legge.
    """
    base, percorso_db = servizio
    cookie, _ = _login_a(base, percorso_db)
    _verifica(base, cookie)

    c = sqlite3.connect(percorso_db)
    quanti = c.execute('SELECT COUNT(*) FROM signals').fetchone()[0]
    c.close()
    assert quanti == 0, 'la verifica della chat ha scritto un segnale nel feed'


# ------------------------------------------------ isolamento fra utenti

def test_una_chat_di_un_altro_utente_non_e_rubabile(servizio):
    """B incolla il proprio codice in un canale gia' verificato da A.

    Il canale resta di A. Senza questo, chiunque riesca a scrivere in un canale
    altrui potrebbe portarselo via — e con esso i segnali che ci passano.
    """
    base, percorso_db = servizio
    cookie_a, _ = _login_a(base, percorso_db)
    _verifica(base, cookie_a, chat=CANALE_A)

    cookie_b, _ = _login_b(base, percorso_db)
    codice_b = _codice(base, cookie_b)
    _consegna(base, CANALE_A, codice_b)

    assert [c['telegram_chat_id'] for c in _chats(base, cookie_a)] == [CANALE_A], (
        'il proprietario originale ha perso la chat'
    )
    assert _chats(base, cookie_b) == [], (
        'la chat di un altro utente e- stata rubata con un codice di verifica'
    )


def test_una_chat_SENZA_proprietario_non_si_adotta(servizio):
    """Le chat del percorso legacy non si prendono con un codice.

    Una riga di `chats` senza `owner_user_id` puo' portare link ai parser di
    **altri** utenti: `_attacca_link_del_profilo` scrive nei due posti in modo
    indipendente. Adottarla darebbe al nuovo proprietario una chat che alimenta
    i parser di qualcun altro, e — con la DELETE — il potere di tagliarne i link.
    `[REAL_FINDING]` di OpenRouter Sol e rilievo convergente di Claude Fable 5.1
    al gate della PR #112.

    Nessuna adozione, quindi: non e' un caso da gestire meglio, e' un caso da non
    avere. Chi ha bisogno di quella chat la fa passare dal proprietario, come
    oggi.
    """
    base, percorso_db = servizio
    cookie, _ = _login_a(base, percorso_db)

    c = sqlite3.connect(percorso_db)
    c.execute('INSERT INTO chats(telegram_chat_id, owner_user_id) VALUES (?, NULL)',
              (CANALE_A,))
    c.commit()
    c.close()

    codice = _codice(base, cookie)
    _consegna(base, CANALE_A, codice)

    assert _chats(base, cookie) == [], (
        'una chat legacy senza proprietario e- stata adottata con un codice'
    )
    c = sqlite3.connect(percorso_db)
    consumato = c.execute('SELECT consumed_at FROM chat_verifications'
                          ' WHERE code=?', (codice,)).fetchone()[0]
    c.close()
    assert consumato is None, 'il codice e- stato bruciato per un rifiuto'


def test_eliminare_una_propria_chat_non_taglia_i_link_di_un_altro(servizio):
    """La DELETE tocca SOLO i link dei parser di chi chiama.

    Una chat posseduta da A puo' portare link a parser di B: il percorso legacy
    scrive `chats` e `parser_chats` separatamente, e nulla impone che il
    proprietario della chat sia il proprietario dei parser collegati. Una DELETE
    che cancellasse `parser_chats WHERE chat_id=?` senza guardare di chi e' il
    parser fermerebbe i segnali di B — silenziosamente, e da parte di A.
    `[REAL_FINDING]` di OpenRouter Sol al gate della PR #112.

    Quando restano link altrui la riga di `chats` non si cancella (lascerebbe
    orfani che il dispatch legge ancora): il chiamante la **disconosce**, e
    sparisce dalla sua lista.
    """
    base, percorso_db = servizio
    cookie_a, _ = _login_a(base, percorso_db)
    _verifica(base, cookie_a, chat=CANALE_A)
    id_chat = _chats(base, cookie_a)[0]['id']

    cookie_b, _ = _login_b(base, percorso_db)
    slug_b = _crea_parser(base, cookie_b, 'Parser di B')
    c = sqlite3.connect(percorso_db)
    parser_b = c.execute('SELECT id FROM parsers WHERE slug=?', (slug_b,)).fetchone()[0]
    c.execute('INSERT INTO parser_chats(parser_id, chat_id) VALUES (?,?)',
              (parser_b, id_chat))
    c.commit()
    c.close()

    stato, corpo, _ = _chiama(base, 'DELETE', f'/api/chats/{id_chat}', cookie=cookie_a)
    assert stato == 200, corpo

    assert _chats(base, cookie_a) == [], 'la chat e- rimasta nella lista di chi l-ha tolta'
    c = sqlite3.connect(percorso_db)
    rimasti = c.execute('SELECT parser_id FROM parser_chats WHERE chat_id=?',
                        (id_chat,)).fetchall()
    c.close()
    assert rimasti == [(parser_b,)], (
        f'il link del parser di un altro utente e- stato tagliato: {rimasti}'
    )


def test_lo_stato_non_spaccia_una_vecchia_chat_per_l_esito_di_un_codice_nuovo(servizio):
    """Lo stato deve dire l'esito DEL CODICE, non «l'ultima chat che hai».

    Lo scenario: l'utente ha gia' una chat verificata, ne chiede un'altra, e il
    codice scade senza essere incollato. Uno stato che restituisse l'ultima chat
    storica direbbe alla web app «fatto», mostrando un canale che con questa
    verifica non c'entra — un falso positivo che il PR 2 consumerebbe.
    `[REAL_FINDING]` di OpenRouter Sol al gate della PR #112.
    """
    base, percorso_db = servizio
    cookie, _ = _login_a(base, percorso_db)
    _verifica(base, cookie, chat=CANALE_A)

    codice_nuovo = _codice(base, cookie)
    c = sqlite3.connect(percorso_db)
    c.execute('UPDATE chat_verifications SET expires_at=? WHERE code=?',
              (int(time.time()) - 1, codice_nuovo))
    c.commit()
    c.close()

    stato, corpo, _ = _chiama(base, 'GET', '/api/chats/verify/status', cookie=cookie)
    assert stato == 200, corpo
    esito = json.loads(corpo)
    assert esito['in_attesa'] is False
    assert esito['scaduto'] is True, esito
    assert esito.get('chat') is None, (
        f'lo stato spaccia una vecchia chat per l-esito del codice nuovo: {esito}'
    )


def test_le_chat_elencate_sono_solo_le_proprie(servizio):
    base, percorso_db = servizio
    cookie_a, _ = _login_a(base, percorso_db)
    _verifica(base, cookie_a, chat=CANALE_A)
    cookie_b, _ = _login_b(base, percorso_db)
    _verifica(base, cookie_b, chat=CANALE_B)

    assert [c['telegram_chat_id'] for c in _chats(base, cookie_a)] == [CANALE_A]
    assert [c['telegram_chat_id'] for c in _chats(base, cookie_b)] == [CANALE_B]


def test_eliminare_la_chat_di_un_altro_da_404_e_non_403(servizio):
    """404 e non 403: un 403 confermerebbe che quella chat esiste."""
    base, percorso_db = servizio
    cookie_a, _ = _login_a(base, percorso_db)
    _verifica(base, cookie_a, chat=CANALE_A)
    id_di_a = _chats(base, cookie_a)[0]['id']

    cookie_b, _ = _login_b(base, percorso_db)
    stato, corpo, _ = _chiama(base, 'DELETE', f'/api/chats/{id_di_a}', cookie=cookie_b)
    assert stato == 404, (stato, corpo)

    assert len(_chats(base, cookie_a)) == 1, 'la chat e- stata eliminata da un altro'


def test_senza_sessione_nessuna_rotta_delle_chat_risponde(servizio):
    """`user_id` viene dalla sessione: senza sessione non c'e' niente da servire."""
    base, percorso_db = servizio
    for metodo, path in (('GET', '/api/chats'),
                         ('POST', '/api/chats/verify/start'),
                         ('GET', '/api/chats/verify/status'),
                         ('DELETE', '/api/chats/1')):
        stato, corpo, _ = _chiama(base, metodo, path)
        assert stato == 401, f'{metodo} {path} risponde {stato}: {corpo}'


# ------------------------------------------- associazione parser ↔ chat

def _crea_parser(base, cookie, titolo='Parser'):
    stato, corpo, _ = _chiama(base, 'POST', '/api/me/parsers', cookie=cookie,
                              corpo={'titolo': titolo, 'config': CONFIG_OK,
                                     'active': True})
    assert stato == 200, corpo
    return json.loads(corpo)['slug']


def test_collegare_le_proprie_chat_a_un_proprio_parser(servizio):
    base, percorso_db = servizio
    cookie, _ = _login_a(base, percorso_db)
    _verifica(base, cookie, chat=CANALE_A)
    id_chat = _chats(base, cookie)[0]['id']
    slug = _crea_parser(base, cookie)

    stato, corpo, _ = _chiama(base, 'PUT', f'/api/me/parsers/{slug}/chats',
                              cookie=cookie, corpo={'chat_ids': [id_chat]})
    assert stato == 200, corpo
    assert json.loads(corpo)['chat_ids'] == [id_chat]

    stato, corpo, _ = _chiama(base, 'GET', f'/api/me/parsers/{slug}/chats',
                              cookie=cookie)
    assert stato == 200, corpo
    assert json.loads(corpo)['chat_ids'] == [id_chat]


def test_non_si_collega_la_chat_di_un_altro_utente(servizio):
    """Il `chat_id` arriva dal corpo: e' l'unico posto dove un utente puo' NOMINARE
    una risorsa altrui, quindi e' li' che la proprieta' va verificata."""
    base, percorso_db = servizio
    cookie_a, _ = _login_a(base, percorso_db)
    _verifica(base, cookie_a, chat=CANALE_A)
    id_di_a = _chats(base, cookie_a)[0]['id']

    cookie_b, _ = _login_b(base, percorso_db)
    slug_b = _crea_parser(base, cookie_b, 'Parser di B')
    stato, corpo, _ = _chiama(base, 'PUT', f'/api/me/parsers/{slug_b}/chats',
                              cookie=cookie_b, corpo={'chat_ids': [id_di_a]})
    assert stato == 404, (stato, corpo)


def test_non_si_collegano_chat_al_parser_di_un_altro(servizio):
    base, percorso_db = servizio
    cookie_a, _ = _login_a(base, percorso_db)
    slug_a = _crea_parser(base, cookie_a, 'Parser di A')

    cookie_b, _ = _login_b(base, percorso_db)
    _verifica(base, cookie_b, chat=CANALE_B)
    id_di_b = _chats(base, cookie_b)[0]['id']
    stato, corpo, _ = _chiama(base, 'PUT', f'/api/me/parsers/{slug_a}/chats',
                              cookie=cookie_b, corpo={'chat_ids': [id_di_b]})
    assert stato == 404, (stato, corpo)


def test_eliminare_la_chat_toglie_anche_i_suoi_link(servizio):
    """Senza, resterebbe una riga di `parser_chats` che riferisce una chat morta —
    e il dispatch la leggerebbe ancora."""
    base, percorso_db = servizio
    cookie, _ = _login_a(base, percorso_db)
    _verifica(base, cookie, chat=CANALE_A)
    id_chat = _chats(base, cookie)[0]['id']
    slug = _crea_parser(base, cookie)
    _chiama(base, 'PUT', f'/api/me/parsers/{slug}/chats', cookie=cookie,
            corpo={'chat_ids': [id_chat]})

    stato, corpo, _ = _chiama(base, 'DELETE', f'/api/chats/{id_chat}', cookie=cookie)
    assert stato == 200, corpo

    c = sqlite3.connect(percorso_db)
    rimasti = c.execute('SELECT COUNT(*) FROM parser_chats WHERE chat_id=?',
                        (id_chat,)).fetchone()[0]
    c.close()
    assert rimasti == 0, 'la chat eliminata ha lasciato i suoi link vivi'


# ------------------------------------------------- il giro completo, end-to-end

def test_dopo_la_verifica_il_canale_alimenta_davvero_il_feed(servizio):
    """La prova che il 3.2 serve a qualcosa: verifico, collego, e il segnale passa.

    Prima di questo PR quel percorso esisteva solo con l'**admin token**, cioe' il
    proprietario che modificava un profilo a mano. Qui non c'e' nessun passaggio
    amministrativo: sessione, codice, link, segnale.

    La sessione e' quella del proprietario e non di un cliente nuovo, e il motivo
    e' il modello d'accesso, non una comodita': un utente appena registrato e'
    `registrato`, e `_blocco_della_riga` ferma i suoi parser con `access_registrato`
    finche' il proprietario non lo attiva. Misurato scrivendo prima questo test con
    un cliente: zero segnali, esattamente per quella ragione. Verificare la chat
    non aggira l'attivazione — e questo test lo dimostra usando un utente che
    l'attivazione ce l'ha.
    """
    base, percorso_db = servizio
    cookie, _ = _login(base)
    _verifica(base, cookie, chat=CANALE_A)
    id_chat = _chats(base, cookie)[0]['id']
    slug = _crea_parser(base, cookie)
    stato, corpo, _ = _chiama(base, 'PUT', f'/api/me/parsers/{slug}/chats',
                              cookie=cookie, corpo={'chat_ids': [id_chat]})
    assert stato == 200, corpo

    _consegna(base, CANALE_A, MESSAGGIO_VALIDO)

    c = sqlite3.connect(percorso_db)
    quanti = c.execute('SELECT COUNT(*) FROM signals').fetchone()[0]
    c.close()
    assert quanti >= 1, 'il canale verificato e collegato non ha prodotto nessun segnale'


def test_un_utente_REGISTRATO_non_produce_segnali_nemmeno_dopo_la_verifica(servizio):
    """La verifica della chat **non** aggira l'attivazione.

    Lo scenario che CodeRabbit ha segnalato come bypass di autorizzazione sulla
    PR #112: un utente appena entrato crea un parser, verifica una chat e ci manda
    un messaggio riconosciuto. Se il segnale uscisse, la verifica sarebbe una
    porta di servizio verso le funzioni operative senza passare dall'attivazione.

    Non esce, e la ragione precede questo PR: `_blocco_della_riga` ferma i parser
    di chi non e' `attivo` con `access_registrato`. Questo test la inchioda sul
    percorso NUOVO — l'invariante c'era, la prova che regga anche di qui no.
    """
    base, percorso_db = servizio
    # Login SENZA attivazione: e' il punto del test, quindi non passa da `_login_a`.
    cookie, _ = _login(base, id=CLIENTE_A, first_name='ClienteA', username='clientea')

    stato, corpo, _ = _chiama(base, 'POST', '/api/chats/verify/start', cookie=cookie)
    assert stato == 403, (
        f'un utente `registrato` ha ottenuto un codice di verifica: {stato} {corpo}'
    )

    # E anche se la chat gli arrivasse per un'altra via, collegarla e- chiuso.
    _attiva(percorso_db, CLIENTE_A)
    _verifica(base, cookie, chat=CANALE_A)
    id_chat = _chats(base, cookie)[0]['id']
    slug = _crea_parser(base, cookie)
    c = sqlite3.connect(percorso_db)
    c.execute("UPDATE users SET status='registrato' WHERE telegram_id=?", (CLIENTE_A,))
    c.commit()
    c.close()
    stato, corpo, _ = _chiama(base, 'PUT', f'/api/me/parsers/{slug}/chats',
                              cookie=cookie, corpo={'chat_ids': [id_chat]})
    assert stato == 403, (stato, corpo)

    _consegna(base, CANALE_A, MESSAGGIO_VALIDO)

    c = sqlite3.connect(percorso_db)
    quanti = c.execute('SELECT COUNT(*) FROM signals').fetchone()[0]
    c.close()
    assert quanti == 0, (
        'un utente `registrato` ha scritto nel feed: la verifica della chat sta '
        'aggirando l-attivazione'
    )


@pytest.mark.parametrize('corpo', ([1, 2], 'stringa', 42, None),
                         ids=('lista', 'stringa', 'numero', 'null'))
def test_un_corpo_json_che_non_e_un_oggetto_da_422_e_non_500(servizio, corpo):
    """Un JSON valido ma non-oggetto non deve diventare un errore del server.

    `dati.get('chat_ids')` su una lista solleva `AttributeError`, cioe' **500** a
    un utente autenticato per un corpo malformato. Segnalato da CodeRabbit sulla
    PR #112.
    """
    base, percorso_db = servizio
    cookie, _ = _login_a(base, percorso_db)
    slug = _crea_parser(base, cookie)
    stato, risposta, _ = _chiama(base, 'PUT', f'/api/me/parsers/{slug}/chats',
                                 cookie=cookie, corpo=corpo)
    assert stato == 422, (stato, risposta)


def test_il_codice_non_compare_nei_log_dei_messaggi(servizio):
    """Un codice in `message_logs` sarebbe riusabile da chi legge quel log."""
    base, percorso_db = servizio
    cookie, _ = _login_a(base, percorso_db)
    codice, _ = _verifica(base, cookie, chat=CANALE_A)

    c = sqlite3.connect(percorso_db)
    testi = [r[0] or '' for r in c.execute('SELECT text FROM message_logs').fetchall()]
    c.close()
    assert not any(codice in t for t in testi), (
        'il codice di verifica e- finito in message_logs'
    )
