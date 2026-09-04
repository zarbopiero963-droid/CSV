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
from tests.telegram_finto import telegram_finto  # noqa: E402

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


def _consegna(base, chat, testo, titolo=None, tipo='channel', mittente=None):
    """Una consegna di Telegram autentica: col segreto derivato dal bot.

    `titolo`/`tipo` sono i campi che Telegram mette in `message.chat` per gruppi e
    canali. Il percorso della verifica li usa: sono l'unico modo che la web app ha
    di dire QUALE canale ha appena registrato.

    **`tipo` vale `'channel'` per difetto, e non e' una comodita'.** Telegram il
    tipo lo manda SEMPRE: una consegna senza `type` non esiste, e modellarla
    significava provare il servizio su un input che non ricevera' mai. Dal #115
    la differenza pesa — in un canale scrivono solo gli amministratori, quindi la
    prova e' forte, mentre fuori serve `getChatMember` — e un tipo assente cade
    fra quelli che la prova la devono dare. I test che vogliono davvero misurare
    l'assenza passano `tipo=None` esplicito.

    `mittente` e' `message.from.id`: i canali non lo portano, i gruppi si'.
    """
    dati_chat = {'id': int(chat)}
    if titolo is not None:
        dati_chat['title'] = titolo
    if tipo is not None:
        dati_chat['type'] = tipo
    messaggio = {'chat': dati_chat, 'text': testo}
    if mittente == '':
        # `from` c'e' ma non porta un `id`, e va detto cosa NON e': non e' una
        # consegna che Telegram manda. Nella Bot API `from` e' opzionale — i post
        # di canale non l'hanno — ma quando c'e' porta sempre un `id`. Questa
        # forma prova la NORMALIZZAZIONE difensiva del relay
        # (`(msg.get('from') or {}).get('id') or ''`), che esiste e va tenuta
        # ferma, non un input osservabile.
        #
        # La distinzione la deve fare chi legge, perche' questo file altrove
        # rifiuta di modellare input impossibili: `tipo` ha il difetto `'channel'`
        # proprio perche' Telegram il tipo lo manda sempre. Il caso realistico qui
        # e' `mittente=None`, cioe' nessun `from`. Segnalato da GPT-5.5 sulla PR
        # #122, che chiedeva se `from: {}` fosse osservabile: non lo e'.
        messaggio['from'] = {}
    elif mittente is not None:
        messaggio['from'] = {'id': int(mittente)}
    payload = {'message': messaggio}
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


# ------------------------------- il rifiuto si DICE a chi sta aspettando (#116)
#
# Il codice arriva, il server lo rifiuta, e fin qui e' giusto. Ma il motivo lo sa
# solo il server: la web app resta «in attesa» finche' il TTL scade, e poi dice
# «Il codice precedente e' scaduto senza essere usato» — che e' FALSO, perche' il
# codice e' stato usato ed e' stato rifiutato. L'esito chiude quella bugia.
#
# `esito` NON consuma il codice, e i due test qui sotto tengono i due versi: si
# scrive il motivo, e il codice resta spendibile altrove.

def _stato_verifica(base, cookie):
    stato, corpo, _ = _chiama(base, 'GET', '/api/chats/verify/status', cookie=cookie)
    assert stato == 200, corpo
    return json.loads(corpo)


def test_la_chat_di_un_altro_NON_si_distingue_da_un_codice_mai_arrivato(servizio):
    """L'oracle fra tenant che questo PR aveva introdotto, e che e' stato tolto.

    B incolla il suo codice nel canale di A. Il rifiuto e' giusto, ma il MOTIVO
    parla di una chat che non e' sua: se lo `status` lo restituisse, chiunque
    possa scrivere in una chat — e in un GRUPPO scrive qualunque membro —
    potrebbe incollarci un proprio codice e scoprire se quella chat e' gia' sul
    servizio.

    **L'oracle non preesisteva**, ed e' la ragione per cui questo test asserisce
    l'assenza e non la presenza: prima di questo PR un codice rifiutato e uno mai
    arrivato erano indistinguibili — il timer scadeva in entrambi i casi. Una
    versione precedente di questo stesso PR li aveva separati.
    `[REAL_FINDING]` di OpenRouter Sol, ripetuto su due head; Fable 5.1 non lo
    bloccava. Decisione del proprietario: congelato, e portato nella sua Issue
    insieme alla #115 e alla #119.

    Il confronto e' con lo stato di un codice MAI CONSEGNATO, non con `None` in
    astratto: e' l'indistinguibilita' la proprieta' da tenere, e scriverla cosi'
    la rende vera anche se un domani lo stato guadagnasse altri campi.
    """
    base, percorso_db = servizio
    cookie_a, _ = _login_a(base, percorso_db)
    _verifica(base, cookie_a, chat=CANALE_A)

    cookie_b, _ = _login_b(base, percorso_db)
    codice_b = _codice(base, cookie_b)
    mai_arrivato = _stato_verifica(base, cookie_b)

    _consegna(base, CANALE_A, codice_b)
    rifiutato = _stato_verifica(base, cookie_b)

    # `scade_fra_s` scorre col tempo: e' l'unico campo che puo' differire senza
    # dire niente su chi possiede la chat.
    confronta = {k: v for k, v in rifiutato.items() if k != 'scade_fra_s'}
    atteso = {k: v for k, v in mai_arrivato.items() if k != 'scade_fra_s'}
    assert confronta == atteso, (
        'un codice rifiutato si distingue da uno mai arrivato: e- un oracle fra '
        f'tenant.\n  mai arrivato: {atteso!r}\n  rifiutato:    {confronta!r}')
    assert rifiutato['in_attesa'] is True, (
        f'il codice risulta consumato da un tentativo rifiutato: {rifiutato!r}')
    assert _chats(base, cookie_b) == [], 'la chat di un altro e- stata registrata'


def test_l_esito_non_brucia_il_codice_che_resta_spendibile_altrove(servizio):
    """L'altra meta', e senza di essa il primo test sarebbe una trappola.

    Registrare il motivo non deve trasformare il rifiuto in un consumo: chi ha
    sbagliato canale deve poter incollare **lo stesso** codice in un canale suo.
    """
    base, percorso_db = servizio
    cookie_a, _ = _login_a(base, percorso_db)
    _verifica(base, cookie_a, chat=CANALE_A)

    cookie_b, _ = _login_b(base, percorso_db)
    codice_b = _codice(base, cookie_b)
    _consegna(base, CANALE_A, codice_b)          # rifiutato
    _consegna(base, CANALE_B, codice_b)          # lo stesso codice, canale suo

    assert [c['telegram_chat_id'] for c in _chats(base, cookie_b)] == [CANALE_B], (
        'il codice rifiutato una volta non vale piu- nemmeno nel canale giusto')
    st = _stato_verifica(base, cookie_b)
    assert st['in_attesa'] is False, f'il codice non risulta consumato: {st!r}'
    assert st.get('esito') is None, (
        f'l-esito del tentativo rifiutato sopravvive al successo: {st!r}')


def test_un_codice_NUOVO_non_eredita_l_esito_del_precedente(servizio):
    """`verify/start` cancella la riga vecchia: il motivo vecchio deve sparire con
    lei, o la schermata accoglierebbe un codice appena chiesto con l'errore del
    tentativo prima."""
    base, percorso_db = servizio
    cookie, _ = _login_a(base, percorso_db)
    codice = _codice(base, cookie)

    c = sqlite3.connect(percorso_db)
    c.execute("UPDATE users SET status='sospeso' WHERE telegram_id=?", (CLIENTE_A,))
    c.commit()
    c.close()
    _consegna(base, CANALE_A, codice)
    c = sqlite3.connect(percorso_db)
    c.execute("UPDATE users SET status='attivo' WHERE telegram_id=?", (CLIENTE_A,))
    c.commit()
    c.close()
    assert _stato_verifica(base, cookie).get('esito') == 'accesso_non_attivo'

    _codice(base, cookie)
    st = _stato_verifica(base, cookie)
    assert st.get('esito') is None, (
        f'il codice nuovo nasce gia- con l-errore del precedente: {st!r}')


def test_un_accesso_sospeso_a_meta_verifica_lo_dice_invece_di_tacere(servizio):
    """L'altro motivo di rifiuto che il server conosce e l'utente no.

    Fra il `verify/start` e l'incollata passano fino a 600 s, e in mezzo il
    proprietario puo' sospendere l'accesso. Il codice non si consuma (giusto: la
    sospensione puo' rientrare), ma tacere lascia l'utente a fissare un timer.
    """
    base, percorso_db = servizio
    cookie, _ = _login_a(base, percorso_db)
    codice = _codice(base, cookie)

    c = sqlite3.connect(percorso_db)
    c.execute("UPDATE users SET status='sospeso' WHERE telegram_id=?", (CLIENTE_A,))
    c.commit()
    c.close()

    _consegna(base, CANALE_A, codice)

    c = sqlite3.connect(percorso_db)
    c.execute("UPDATE users SET status='attivo' WHERE telegram_id=?", (CLIENTE_A,))
    c.commit()
    c.close()

    st = _stato_verifica(base, cookie)
    assert st.get('esito') == 'accesso_non_attivo', (
        f'lo stato non dice che il rifiuto veniva dall-accesso: {st!r}')
    assert st['in_attesa'] is True, f'il codice e- stato bruciato: {st!r}'


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

    # A collega ANCHE un proprio parser: il caso misto e' quello che conta, perche'
    # separa «cancella tutto» da «cancella i miei». Chiesto da GPT-5.5 sulla PR #112.
    slug_a = _crea_parser(base, cookie_a, 'Parser di A')
    stato, corpo, _ = _chiama(base, 'PUT', f'/api/me/parsers/{slug_a}/chats',
                              cookie=cookie_a, corpo={'chat_ids': [id_chat]})
    assert stato == 200, corpo

    cookie_b, _ = _login_b(base, percorso_db)
    slug_b = _crea_parser(base, cookie_b, 'Parser di B')
    c = sqlite3.connect(percorso_db)
    parser_a = c.execute('SELECT id FROM parsers WHERE slug=?', (slug_a,)).fetchone()[0]
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
    proprietario = c.execute('SELECT owner_user_id FROM chats WHERE id=?',
                             (id_chat,)).fetchone()
    c.close()
    assert rimasti == [(parser_b,)], (
        f'attesi solo i link di B, trovati {rimasti} (parser di A: {parser_a})'
    )
    assert proprietario is not None, (
        'la riga di `chats` e- stata cancellata con i link di B ancora vivi: '
        'il dispatch li leggerebbe come orfani'
    )
    assert proprietario[0] is None, (
        f'la chat non e- stata disconosciuta: proprietario {proprietario[0]}'
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


def test_un_codice_emesso_da_ATTIVO_non_vale_piu_dopo_la_sospensione(servizio):
    """Lo stato si ricontrolla al CONSUMO, non solo all'emissione.

    Il cancello su `verify/start` guarda l'accesso nel momento in cui il codice
    nasce. Ma fra quel momento e l'incollata passano fino a 600 secondi, e in
    mezzo il proprietario puo' sospendere l'utente: un codice gia' in mano
    resterebbe spendibile, cioe' aggirerebbe la sospensione per dieci minuti.
    `[REAL_FINDING]` di OpenRouter Sol al gate della PR #112.
    """
    base, percorso_db = servizio
    cookie, _ = _login_a(base, percorso_db)
    codice = _codice(base, cookie)

    c = sqlite3.connect(percorso_db)
    c.execute("UPDATE users SET status='sospeso' WHERE telegram_id=?", (CLIENTE_A,))
    c.commit()
    c.close()

    _consegna(base, CANALE_A, codice)

    c = sqlite3.connect(percorso_db)
    quante = c.execute('SELECT COUNT(*) FROM chats WHERE telegram_chat_id=?',
                       (CANALE_A,)).fetchone()[0]
    consumato = c.execute('SELECT consumed_at FROM chat_verifications'
                          ' WHERE code=?', (codice,)).fetchone()[0]
    c.close()
    assert quante == 0, 'un utente sospeso ha registrato una chat con un codice vecchio'
    assert consumato is None, 'il codice e- stato bruciato per un rifiuto'


def test_la_chat_dell_esito_e_correlata_dal_LEGAME_non_dal_secondo(servizio):
    """La correlazione e' esplicita, non dedotta da due timestamp che coincidono.

    `verified_at` ha la risoluzione del secondo: due chat verificate nello stesso
    secondo erano indistinguibili, e la scelta fra loro era un'euristica
    (`ORDER BY id DESC`). Questo test forza proprio quel caso — stesso
    `verified_at` su due chat dello stesso utente — e pretende la chat GIUSTA,
    cioe' quella che il codice corrente ha davvero verificato.

    Rilievo ripetuto di OpenRouter Sol e GPT-5.5 sulla PR #112: finche' la
    correlazione e' un indizio, la risposta e' una scommessa.
    """
    base, percorso_db = servizio
    cookie, _ = _login_a(base, percorso_db)
    # La sequenza conta, ed e' l'unico modo di distinguere una correlazione vera da
    # una che indovina: A nasce per prima (id piu' basso), poi B (id piu' alto), poi
    # si RIVERIFICA A — che aggiorna la riga vecchia invece di crearne una nuova.
    # La chat giusta e' quindi A, con l'id piu' BASSO, e l'euristica
    # `ORDER BY id DESC` risponderebbe B. Le prime due stesure di questo test
    # mettevano la risposta giusta sull'id piu' alto e passavano per fortuna.
    _verifica(base, cookie, chat=CANALE_A)
    _verifica(base, cookie, chat=CANALE_B)
    _verifica(base, cookie, chat=CANALE_A)

    # Le due chat si ritrovano con lo STESSO istante di verifica: e' la collisione
    # che in produzione capita se due verifiche cadono nello stesso secondo.
    c = sqlite3.connect(percorso_db)
    istante = c.execute('SELECT consumed_at FROM chat_verifications').fetchone()[0]
    c.execute('UPDATE chats SET verified_at=?', (istante,))
    c.commit()
    c.close()

    stato, corpo, _ = _chiama(base, 'GET', '/api/chats/verify/status', cookie=cookie)
    assert stato == 200, corpo
    esito = json.loads(corpo)
    assert (esito.get('chat') or {}).get('telegram_chat_id') == CANALE_A, (
        f'la correlazione ha scelto la chat sbagliata: {esito}'
    )


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


# --------------------------------------------- il nome del canale, per la web app

def test_la_chat_verificata_porta_titolo_e_tipo_della_consegna(servizio):
    """Senza il nome, la web app puo' mostrare solo `-1002000000101`.

    Le colonne `title` e `type` esistono in `chats` dalla prima migrazione e
    **nessun percorso le scriveva**: `grep 'INSERT INTO chats'` dava tre siti, tutti
    senza quei campi. Finche' le chat le collegava l'amministratore la cosa non si
    vedeva — era lui a sapere quale canale fosse quale. Da quando le collega il
    cliente, una lista di interi negativi non e' una schermata: due canali si
    distinguono solo dal numero, e nessuno riconosce i propri canali dal numero.

    Il titolo arriva dalla stessa consegna che porta il codice: e' il modo in cui
    Telegram dice come si chiama la chat, e non costa nessuna chiamata in piu'.
    """
    base, percorso_db = servizio
    cookie, _ = _login_a(base, percorso_db)
    codice = _codice(base, cookie)
    stato, corpo = _consegna(base, CANALE_A, codice,
                             titolo='Canale segnali', tipo='channel')
    assert stato == 200, corpo

    chat = _chats(base, cookie)[0]
    assert chat['titolo'] == 'Canale segnali', f'titolo non registrato: {chat}'
    assert chat['tipo'] == 'channel', f'tipo non registrato: {chat}'


def test_il_titolo_del_canale_e_capato_e_ripulito(servizio):
    """Il titolo e' testo di un ESTRANEO: arriva da Telegram, non dal servizio.

    Chi controlla un canale ne sceglie il nome, quindi quel valore va trattato come
    ogni altro input esterno: capato in lunghezza — o una riga di `chats` diventa
    grande a piacere di chi la scrive — e senza caratteri di controllo, che nella
    lista della web app produrrebbero righe spezzate.
    """
    base, percorso_db = servizio
    cookie, _ = _login_a(base, percorso_db)
    codice = _codice(base, cookie)
    stato, corpo = _consegna(base, CANALE_A, codice,
                             titolo='  A\nB' + 'x' * 500, tipo='channel')
    assert stato == 200, corpo

    chat = _chats(base, cookie)[0]
    assert len(chat['titolo']) <= main.MAX_TITOLO_CHAT, (
        f'titolo non capato: {len(chat["titolo"])} caratteri')
    assert '\n' not in chat['titolo'], f'ritorno a capo nel titolo: {chat["titolo"]!r}'
    assert chat['titolo'].startswith('A B'), f'titolo non ripulito: {chat["titolo"]!r}'


def test_riverificare_la_stessa_chat_ne_aggiorna_il_nome(servizio):
    """Un canale rinominato non deve restare col nome vecchio nella lista."""
    base, percorso_db = servizio
    cookie, _ = _login_a(base, percorso_db)
    _consegna(base, CANALE_A, _codice(base, cookie),
              titolo='Nome vecchio', tipo='channel')
    _consegna(base, CANALE_A, _codice(base, cookie),
              titolo='Nome nuovo', tipo='channel')

    chat = _chats(base, cookie)[0]
    assert chat['titolo'] == 'Nome nuovo', f'il nome non si aggiorna: {chat}'


# ------------------------------- la prova di RUOLO fuori dai canali (#115)
#
# Il codice dimostra che chi lo presenta puo' SCRIVERE nella chat. In un canale
# coincide col controllarla — scrivono solo gli amministratori — ma in un GRUPPO
# scrive qualunque membro: il primo che incolla un codice si prende il gruppo di
# un altro, e il titolare legittimo non lo puo' piu' collegare.
#
# Questi test usano un finto `api.telegram.org` sul loopback, perche' sono i primi
# del repository che devono vedere una chiamata in uscita RIUSCIRE: col solo proxy
# morto un cancello che si limita a fallire non si distingue da uno che funziona.

GRUPPO = '-1002000000777'
MEMBRO = '555000999'


@pytest.fixture
def servizio_con_telegram(tmp_path, monkeypatch):
    """Il relay, piu' un Telegram finto raggiungibile.

    `HTTPS_PROXY` resta sulla porta morta: il finto e' `http://`, quindi non passa
    dal proxy. Le due cose convivono di proposito — cosi' un test puo' avere
    `getChatMember` raggiungibile e tutto il resto no.
    """
    monkeypatch.setattr(main, 'SEGRETO_SESSIONE', SEGRETO_ATTESO)
    with telegram_finto() as (base_telegram, finto):
        ambiente = dict(AMBIENTE_DEL_SERVIZIO, TELEGRAM_API_BASE=base_telegram)
        with relay_avviato(tmp_path, **ambiente) as base:
            yield base, tmp_path / 'signals.db', finto


def test_in_un_GRUPPO_un_membro_qualunque_non_si_prende_la_chat(servizio_con_telegram):
    """Il furto della verifica, che e' il difetto per cui la #115 esiste.

    L'utente e' attivo e il codice e' suo e valido: l'unica cosa che manca e' il
    RUOLO. Telegram dice `member`, quindi la chat non si registra.
    """
    base, percorso_db, finto = servizio_con_telegram
    cookie, _ = _login_a(base, percorso_db)
    finto.ruolo('member')

    codice = _codice(base, cookie)
    stato, corpo = _consegna(base, GRUPPO, codice, titolo='Gruppo segnali',
                             tipo='supergroup', mittente=CLIENTE_A)

    assert stato == 200, corpo
    assert corpo.get('ignored') == 'ruolo_non_provato', corpo
    assert _chats(base, cookie) == [], (
        'un membro qualunque si e- preso il gruppo: e- il furto della verifica')


def test_in_un_GRUPPO_un_amministratore_la_collega(servizio_con_telegram):
    """L'altra meta': col ruolo giusto il percorso funziona come prima.

    Senza questo test il precedente passerebbe anche con un cancello sempre
    chiuso, che non e' una prova di ruolo ma un blocco.
    """
    base, percorso_db, finto = servizio_con_telegram
    cookie, _ = _login_a(base, percorso_db)
    finto.ruolo('administrator')

    codice = _codice(base, cookie)
    stato, corpo = _consegna(base, GRUPPO, codice, titolo='Gruppo segnali',
                             tipo='supergroup', mittente=CLIENTE_A)

    assert stato == 200, corpo
    assert corpo.get('verified') is True, corpo
    assert [c['telegram_chat_id'] for c in _chats(base, cookie)] == [GRUPPO]
    metodo, parametri = finto.chiamate[-1]
    assert metodo == 'getChatMember', finto.chiamate
    assert parametri.get('user_id') == CLIENTE_A, parametri
    assert parametri.get('chat_id') == GRUPPO, parametri


@pytest.mark.parametrize('ruolo', ['member', 'restricted', 'left', 'kicked'])
def test_nessun_ruolo_senza_controllo_basta(servizio_con_telegram, ruolo):
    """Solo `creator` e `administrator` provano qualcosa: la lista e' chiusa."""
    base, percorso_db, finto = servizio_con_telegram
    cookie, _ = _login_a(base, percorso_db)
    finto.ruolo(ruolo)

    _consegna(base, GRUPPO, _codice(base, cookie), tipo='supergroup',
              mittente=CLIENTE_A)

    assert _chats(base, cookie) == [], f'il ruolo «{ruolo}» ha collegato la chat'


def test_il_creatore_del_gruppo_la_collega(servizio_con_telegram):
    base, percorso_db, finto = servizio_con_telegram
    cookie, _ = _login_a(base, percorso_db)
    finto.ruolo('creator')

    _consegna(base, GRUPPO, _codice(base, cookie), tipo='supergroup',
              mittente=CLIENTE_A)

    assert [c['telegram_chat_id'] for c in _chats(base, cookie)] == [GRUPPO]


def test_in_un_CANALE_non_si_chiama_Telegram(servizio_con_telegram):
    """La prova e' gia' forte: chiedere il ruolo sarebbe una chiamata sprecata.

    E non e' solo un risparmio: nei canali `message.from` non esiste, quindi non
    ci sarebbe nemmeno un utente da chiedere.
    """
    base, percorso_db, finto = servizio_con_telegram
    cookie, _ = _login_a(base, percorso_db)
    finto.ruolo('member')   # se venisse chiamato, rifiuterebbe

    _consegna(base, CANALE_A, _codice(base, cookie), tipo='channel')

    assert [c['telegram_chat_id'] for c in _chats(base, cookie)] == [CANALE_A]
    assert finto.quante('getChatMember') == 0, (
        f'chiamato Telegram per un canale: {finto.chiamate}')


def test_un_codice_MORTO_non_fa_chiamare_Telegram(servizio_con_telegram):
    """Il filtro contro l'abuso, e vale la pena dire cosa protegge.

    Senza, chiunque possa scrivere in una chat dove il bot e' presente potrebbe
    farci fare una raffica di chiamate in uscita incollando stringhe della forma
    `BETRELAY-XXXXXXXX`. La lettura di filtro costa una query senza lock.
    """
    base, percorso_db, finto = servizio_con_telegram
    _login_a(base, percorso_db)
    finto.ruolo('administrator')

    _consegna(base, GRUPPO, 'BETRELAY-INVENTATO', tipo='supergroup',
              mittente=CLIENTE_A)

    assert finto.quante('getChatMember') == 0, (
        f'Telegram chiamato per un codice inventato: {finto.chiamate}')


def test_se_Telegram_non_risponde_la_chat_NON_si_collega(servizio):
    """Fail-closed, ed e' una scelta dichiarata, non un default.

    Qui non c'e' nessun Telegram finto: la fixture `servizio` ha il proxy morto,
    quindi la chiamata fallisce come farebbe un guasto vero. Il costo e' reale —
    un'interruzione di Telegram impedisce di collegare gruppi nuovi — ma il verso
    opposto renderebbe la protezione assente proprio quando serve, perche'
    basterebbe far fallire la chiamata.
    """
    base, percorso_db = servizio
    cookie, _ = _login_a(base, percorso_db)

    stato, corpo = _consegna(base, GRUPPO, _codice(base, cookie),
                             tipo='supergroup', mittente=CLIENTE_A)

    assert stato == 200, corpo
    assert corpo.get('ignored') == 'ruolo_non_provato', corpo
    assert _chats(base, cookie) == []


def test_un_TIPO_sconosciuto_deve_dare_la_prova(servizio):
    """La lista dice cosa SALTA il controllo, non cosa lo richiede.

    Un tipo assente o nuovo cade fra quelli che la prova la devono dare: col
    verso opposto, tutto cio' che non riconosciamo passerebbe.
    """
    base, percorso_db = servizio
    cookie, _ = _login_a(base, percorso_db)

    _consegna(base, CANALE_A, _codice(base, cookie), tipo=None,
              mittente=CLIENTE_A)

    assert _chats(base, cookie) == [], 'un tipo sconosciuto ha saltato la prova'


def test_senza_mittente_fuori_da_un_canale_non_si_collega(servizio):
    """Una consegna non attribuibile non registra niente."""
    base, percorso_db = servizio
    cookie, _ = _login_a(base, percorso_db)

    _consegna(base, GRUPPO, _codice(base, cookie), tipo='supergroup',
              mittente=None)

    assert _chats(base, cookie) == []


def test_la_conversazione_PRIVATA_col_bot_resta_collegabile(servizio_con_telegram):
    """La prova piu' forte di tutte, e questa PR stava per toglierla.

    In una chat privata gli interlocutori sono due: quella persona e il bot.
    Scriverci dentro non prova che «puoi scrivere», prova che **e' la tua**. La
    conferma sta nell'identificatore: per una chat privata Telegram usa come
    `chat.id` l'id dell'utente stesso, quindi `chat_id == from.id` e' verificabile
    qui, senza chiedere niente a nessuno.

    `getChatMember` non potrebbe confermarla: in una chat privata il ruolo di
    amministratore non esiste, quindi la risposta non sara' mai
    `creator`/`administrator` e il cancello del #115 rifiuterebbe **sempre**. Non
    era una decisione, era una conseguenza non vista: prima del #115 il codice
    incollato in privato collegava la chat, e nessun test lo teneva fermo.

    Segnalato da Claude Fable 5.1 sulla PR #122 come `[INSUFFICIENT_CONTEXT]`.
    """
    base, percorso_db, finto = servizio_con_telegram
    cookie, _ = _login_a(base, percorso_db)
    finto.ruolo('member')   # se venisse chiamato, rifiuterebbe

    stato, corpo = _consegna(base, CLIENTE_A, _codice(base, cookie),
                             tipo='private', mittente=CLIENTE_A)

    assert stato == 200, corpo
    assert corpo.get('verified') is True, corpo
    assert [c['telegram_chat_id'] for c in _chats(base, cookie)] == [CLIENTE_A]
    assert finto.quante('getChatMember') == 0, (
        f'chiesto a Telegram un ruolo che in privato non esiste: {finto.chiamate}')


def test_una_chat_PRIVATA_che_non_e_la_tua_non_si_collega(servizio_con_telegram):
    """L'altra meta', e costa una riga: la prova e' `chat_id == from.id`.

    Su Telegram le due cose coincidono per costruzione, quindi questo caso non
    dovrebbe arrivare mai. Proprio per questo il controllo va scritto: se un
    domani arrivasse, «tipo privato» da solo autorizzerebbe una chat altrui.
    """
    base, percorso_db, finto = servizio_con_telegram
    cookie, _ = _login_a(base, percorso_db)
    finto.ruolo('administrator')   # nemmeno un si- di Telegram deve bastare

    _consegna(base, CLIENTE_A, _codice(base, cookie), tipo='private',
              mittente=CLIENTE_B)

    assert _chats(base, cookie) == []


@pytest.mark.parametrize('mittente', [None, ''])
def test_una_chat_PRIVATA_senza_mittente_non_si_collega(servizio_con_telegram, mittente):
    """Fail-closed anche in privato, e il test serve a fissare l'ORDINE dei rami.

    Oggi il caso e' chiuso due volte: `if not mittente` sta prima, e anche se non
    ci fosse il confronto `chat_id == mittente` fallirebbe lo stesso. E' proprio
    per questo che vale scriverlo: la doppia chiusura e' una proprieta' della
    disposizione attuale del codice, non una garanzia, e spostare il ramo del
    privato piu' in alto la ridurrebbe a una sola.

    I due parametri non hanno lo stesso statuto, e vale la pena dirlo: `None` e'
    una consegna senza `from`, che Telegram manda davvero; `''` e' `from` senza
    `id`, che non manda mai e che prova la normalizzazione difensiva del relay.

    Suggerito da GPT-5.5 sulla PR #122.
    """
    base, percorso_db, finto = servizio_con_telegram
    cookie, _ = _login_a(base, percorso_db)
    finto.ruolo('administrator')

    _consegna(base, CLIENTE_A, _codice(base, cookie), tipo='private',
              mittente=mittente)

    assert _chats(base, cookie) == []
    assert finto.quante('getChatMember') == 0, finto.chiamate


def test_un_rifiuto_di_Telegram_con_HTTP_200_resta_un_rifiuto(servizio_con_telegram):
    """`ok: false` dentro una risposta 200 e' un NO, anche se il corpo sembra un SI'.

    E' l'unico percorso d'errore che non solleva niente: l'HTTP e' andato a buon
    fine, `urlopen` non alza, e senza il controllo su `ok` il codice leggerebbe il
    `result` di una risposta che Telegram ha gia' dichiarato non valida. Qui il
    finto risponde `ok: false` portando comunque `status: administrator`, che e' la
    sola forma in cui la differenza fra «leggo il flag» e «leggo lo stato» diventa
    visibile: se il flag non viene letto, la chat si collega.

    Segnalato da GPT-5.5 sulla PR #122 come copertura mancante — il ramo esisteva
    in `ruolo_in_chat`, nessun test lo esercitava.
    """
    base, percorso_db, finto = servizio_con_telegram
    cookie, _ = _login_a(base, percorso_db)
    finto.rispondi('getChatMember', {'status': 'administrator'}, ok=False)

    stato, corpo = _consegna(base, GRUPPO, _codice(base, cookie),
                             tipo='supergroup', mittente=CLIENTE_A)

    assert stato == 200, corpo
    assert corpo.get('ignored') == 'ruolo_non_provato', corpo
    assert _chats(base, cookie) == [], (
        'un «ok: false» di Telegram ha collegato la chat: letto il result, non il flag')
    assert finto.quante('getChatMember') == 1, finto.chiamate


def test_un_errore_HTTP_di_Telegram_non_collega_la_chat(servizio_con_telegram):
    """L'altro verso: 403 con un corpo che direbbe `administrator`.

    Qui `urlopen` solleva, quindi il ramo e' quello dell'eccezione; il corpo
    plausibile serve a rendere il test capace di fallire se un domani qualcuno
    provasse a leggerlo lo stesso.
    """
    base, percorso_db, finto = servizio_con_telegram
    cookie, _ = _login_a(base, percorso_db)
    finto.rispondi('getChatMember', {'status': 'administrator'}, ok=False, http=403)

    _consegna(base, GRUPPO, _codice(base, cookie), tipo='supergroup',
              mittente=CLIENTE_A)

    assert _chats(base, cookie) == []


def _sblocca_il_freno(percorso_db, codice):
    """Riporta indietro `ruolo_chiesto_at` come farebbe il passare del tempo.

    Il freno del #115 lascia una prova di ruolo ogni `ATTESA_FRA_PROVE_DI_RUOLO_S`
    secondi. In produzione non si nota — promuovere qualcuno ad amministratore su
    Telegram richiede molto di piu' — ma in un test le due consegne distano
    millisecondi. Retrodatare la colonna e' piu' onesto di una `sleep`: misura il
    comportamento voluto («passata la finestra si richiama Telegram») invece di
    aspettare un orologio.
    """
    c = sqlite3.connect(percorso_db)
    try:
        c.execute('UPDATE chat_verifications SET ruolo_chiesto_at=0 WHERE code=?',
                  (codice,))
        c.commit()
    finally:
        c.close()


def test_lo_STESSO_codice_ripetuto_non_moltiplica_le_chiamate(servizio_con_telegram):
    """Il freno, ed e' un difetto che questa PR aveva introdotto.

    La lettura di filtro ferma i codici INVENTATI. Il codice VIVO no — e quello e'
    incollato nella chat, quindi lo vedono tutti i membri: chiunque di loro poteva
    ripeterlo e farci fare una chiamata in uscita **per messaggio**. Misurato prima
    della correzione: 10 consegne, 10 `getChatMember`.

    Il costo non e' teorico: ogni consegna occupa un thread del pool per fino a 10
    secondi di timeout, e quel pool serve anche l'elaborazione dei segnali di tutti
    gli altri utenti.

    `[REAL_FINDING]` di OpenRouter Sol al gate finale della PR #122.
    """
    base, percorso_db, finto = servizio_con_telegram
    cookie, _ = _login_a(base, percorso_db)
    finto.ruolo('member')

    codice = _codice(base, cookie)
    for _ in range(10):
        _consegna(base, GRUPPO, codice, tipo='supergroup', mittente=CLIENTE_A)

    assert finto.quante('getChatMember') == 1, (
        f'il codice vivo ripetuto moltiplica le chiamate: {finto.quante("getChatMember")}')
    assert _chats(base, cookie) == [], 'il freno non deve collegare niente'


def test_il_freno_e_una_FINESTRA_non_un_blocco(servizio_con_telegram):
    """Senza questo, il test sopra passerebbe anche con un freno che chiude per
    sempre dopo la prima prova — che non e' un freno, e' un guasto."""
    base, percorso_db, finto = servizio_con_telegram
    cookie, _ = _login_a(base, percorso_db)
    finto.ruolo('member')

    codice = _codice(base, cookie)
    _consegna(base, GRUPPO, codice, tipo='supergroup', mittente=CLIENTE_A)
    _sblocca_il_freno(percorso_db, codice)
    _consegna(base, GRUPPO, codice, tipo='supergroup', mittente=CLIENTE_A)

    assert finto.quante('getChatMember') == 2, finto.chiamate


def test_il_rifiuto_per_ruolo_SI_DICE(servizio_con_telegram):
    """Senza, la schermata contava alla rovescia e poi diceva «scaduto»: falso.

    Il codice non era scaduto, era stato rifiutato. E' la stessa bugia che la #120
    aveva tolto per `accesso_non_attivo`, rimasta in piedi sul rifiuto piu' comune
    di questa funzione. Segnalato da CodeRabbit sulla PR #122.
    """
    base, percorso_db, finto = servizio_con_telegram
    cookie, _ = _login_a(base, percorso_db)
    finto.ruolo('member')

    _consegna(base, GRUPPO, _codice(base, cookie), tipo='supergroup',
              mittente=CLIENTE_A)

    st = _stato_verifica(base, cookie)
    assert st.get('esito') == 'ruolo_non_provato', (
        f'il rifiuto per ruolo non lascia traccia: {st!r}')
    assert st['in_attesa'] is True, (
        f'scrivere il motivo ha consumato il codice: {st!r}')


def test_il_motivo_scritto_NON_impedisce_di_riprovare(servizio_con_telegram):
    """L'altra meta': `esito` non e' un cancello.

    La prova di ruolo guarda `consumed_at` e la scadenza, mai `esito` — e questo
    test e' l'unica cosa che lo tiene fermo.
    """
    base, percorso_db, finto = servizio_con_telegram
    cookie, _ = _login_a(base, percorso_db)
    finto.ruolo('member')

    codice = _codice(base, cookie)
    _consegna(base, GRUPPO, codice, tipo='supergroup', mittente=CLIENTE_A)
    finto.ruolo('administrator')
    _sblocca_il_freno(percorso_db, codice)
    _consegna(base, GRUPPO, codice, tipo='supergroup', mittente=CLIENTE_A)

    assert [c['telegram_chat_id'] for c in _chats(base, cookie)] == [GRUPPO], (
        'il motivo scritto ha reso il codice inservibile')


def test_il_freno_non_consuma_il_codice(servizio_con_telegram):
    """Una consegna frenata dev'essere indistinguibile da una rifiutata per ruolo:
    stesso motivo, e il codice resta spendibile."""
    base, percorso_db, finto = servizio_con_telegram
    cookie, _ = _login_a(base, percorso_db)
    finto.ruolo('member')

    codice = _codice(base, cookie)
    _consegna(base, GRUPPO, codice, tipo='supergroup', mittente=CLIENTE_A)
    stato, corpo = _consegna(base, GRUPPO, codice, tipo='supergroup',
                             mittente=CLIENTE_A)

    assert stato == 200, corpo
    assert corpo.get('ignored') == 'ruolo_non_provato', corpo
    st = _stato_verifica(base, cookie)
    assert st['in_attesa'] is True, f'il freno ha bruciato il codice: {st!r}'


def test_il_freno_non_tocca_i_CANALI(servizio_con_telegram):
    """Nei canali non si chiama Telegram, quindi non c'e' niente da frenare: due
    consegne ravvicinate devono funzionare come prima."""
    base, percorso_db, finto = servizio_con_telegram
    cookie_a, _ = _login_a(base, percorso_db)

    _consegna(base, CANALE_A, _codice(base, cookie_a), tipo='channel')
    cookie_b, _ = _login_b(base, percorso_db)
    _consegna(base, CANALE_B, _codice(base, cookie_b), tipo='channel')

    assert [c['telegram_chat_id'] for c in _chats(base, cookie_a)] == [CANALE_A]
    assert [c['telegram_chat_id'] for c in _chats(base, cookie_b)] == [CANALE_B]
    assert finto.quante('getChatMember') == 0, finto.chiamate


def test_il_rifiuto_per_ruolo_NON_brucia_il_codice(servizio_con_telegram):
    """Chi non era ancora amministratore deve poter riprovare dopo esserlo
    diventato, senza chiedere un codice nuovo."""
    base, percorso_db, finto = servizio_con_telegram
    cookie, _ = _login_a(base, percorso_db)
    finto.ruolo('member')

    codice = _codice(base, cookie)
    _consegna(base, GRUPPO, codice, tipo='supergroup', mittente=CLIENTE_A)
    assert _chats(base, cookie) == []

    finto.ruolo('administrator')
    _sblocca_il_freno(percorso_db, codice)
    _consegna(base, GRUPPO, codice, tipo='supergroup', mittente=CLIENTE_A)

    assert [c['telegram_chat_id'] for c in _chats(base, cookie)] == [GRUPPO], (
        'il codice era stato bruciato da un rifiuto per ruolo')



# ------------------------------- la radice dell'API di Telegram (#115)
#
# `TELEGRAM_API_BASE` finisce dentro un URL che porta il TOKEN DEL BOT. Non e' una
# stringa qualunque: un valore `http://` verso un host remoto spedirebbe il token in
# chiaro, un host sbagliato lo spedirebbe a qualcun altro.
#
# Nella prima versione della PR la variabile era solo DOCUMENTATA come «in produzione
# non si imposta». Tre reviewer di fila l'hanno segnalata — GPT-5.5 come rischio
# manuale, Fable 5.1 come nota, CodeRabbit come **Major** con CWE-200 — e avevano
# ragione tutti e tre: una cautela scritta non e' un vincolo, e questo repository ha
# la frase per nome. Adesso e' un controllo, e questi test sono il controllo.

@pytest.mark.parametrize('radice', [
    'https://api.telegram.org',      # l'API vera
    'https://api.telegram.org/',     # con la barra finale
    'http://127.0.0.1:8081',         # il loopback: non esce dalla macchina
    'http://[::1]:8081',
])
def test_le_radici_AMMESSE_si_usano(radice, monkeypatch):
    monkeypatch.setenv('TELEGRAM_API_BASE', radice)
    url = main.url_telegram('123:abc', 'getChatMember')
    assert url.startswith(radice.rstrip('/') + '/bot'), url


@pytest.mark.parametrize('radice', [
    'http://api.telegram.org',           # HTTPS o niente: il token non viaggia in chiaro
    'https://evil.example.com',
    'https://api.telegram.org.evil.com',  # il suffisso non e' l'host
    'http://10.0.0.1:8080',               # una rete interna resta un altro host
    'http://localhost:9',                 # un NOME, non un indirizzo: dipende dal DNS
    'ftp://api.telegram.org',
    'non-un-url',
    '   ',
])
def test_una_radice_NON_ammessa_viene_ignorata(radice, monkeypatch):
    """Si ripiega sull'API vera: un errore di configurazione non dirotta il token.

    Il verso opposto — fidarsi del valore — e' esattamente il difetto: chi sbaglia a
    scrivere la variabile manderebbe `bot<token>` all'host scritto per sbaglio.
    """
    monkeypatch.setenv('TELEGRAM_API_BASE', radice)
    url = main.url_telegram('123:abc', 'getChatMember')
    assert url == 'https://api.telegram.org/bot123:abc/getChatMember', url


def test_senza_la_variabile_si_usa_l_API_vera(monkeypatch):
    monkeypatch.delenv('TELEGRAM_API_BASE', raising=False)
    assert main.url_telegram('123:abc', 'setWebhook') == (
        'https://api.telegram.org/bot123:abc/setWebhook')


# ------------------------------- risposte malformate di Telegram (#115)

@pytest.mark.parametrize('corpo', [[], 'una stringa', None, 42, {'ok': True, 'result': []}])
def test_una_risposta_MALFORMATA_non_solleva(corpo):
    """I due lettori promettono nel docstring di non sollevare, e non lo facevano.

    `json.loads` sta DENTRO il `try`, `.get()` fuori: un JSON valido che non e' un
    oggetto — `[]`, `"x"`, `null`, un numero — passava il primo e moriva sul secondo
    con un `AttributeError`. Dal webhook quello diventa un HTTP 500 invece di un
    rifiuto pulito. Segnalato da CodeRabbit sulla PR #122.
    """
    assert main.telegram_ha_detto_si(corpo) in (True, False)
    assert isinstance(main.risultato_telegram(corpo), dict)


def test_un_result_non_oggetto_non_diventa_un_ruolo():
    """`ok: true` con un `result` che non e' un oggetto non deve dare un ruolo."""
    assert main.risultato_telegram({'ok': True, 'result': ['administrator']}) == {}
