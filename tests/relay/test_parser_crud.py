"""CRUD dei parser legato alla SESSIONE: ogni cliente tocca solo i propri.

E' il primo pezzo del passo 3 («la web app funziona»): le rotte `/api/me/parsers*`
che la web app chiamera' al posto dei dati finti. La proprieta' del test non e' che
il CRUD funzioni — quello e' il minimo — ma **l'isolamento fra utenti**, la regola
non negoziabile del progetto: `user_id` viene dalla sessione e mai dalla richiesta, e
un utente non vede, non modifica e non elimina i parser di un altro (404, non 403,
perche' un 403 confermerebbe che quel parser esiste).

I test parlano al servizio vero via HTTP con due sessioni diverse — l'unico modo di
verificare l'isolamento e' con due utenti reali, ciascuno col proprio cookie firmato.
La firma del cookie e del Login Widget e' ricalcolata a mano (come in
`test_login.py`), non importata dal codice che verifica: due lati che cambiano insieme
non proverebbero niente.
"""

from __future__ import annotations

import hashlib
import hmac
import json
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
# DUE clienti non-admin: ciascuno nasce con ZERO parser, cosi' la lista di uno non
# porta il parser di default (che appartiene all'utente PIERO/admin). E' l'unico modo
# di misurare l'isolamento senza che il parser seminato inquini le asserzioni.
CLIENTE_A = '555000111'
CLIENTE_B = '555000222'
PROXY_MORTO = 'http://127.0.0.1:1'

# Il segreto delle sessioni con la stessa formula del servizio, ricalcolato a mano.
SEGRETO_ATTESO = hashlib.sha256(('betrelay-sessione-v1:' + BOT_FINTO).encode()).hexdigest()

AMBIENTE_DEL_SERVIZIO = {
    'CSV_ACCESS_TOKEN': TOKEN_DI_PROVA,
    'TELEGRAM_BOT_TOKEN': BOT_FINTO,
    'TELEGRAM_ADMIN_ID': ADMIN_FINTO,
    'PUBLIC_URL': 'https://non-esiste.invalid',
    'HTTPS_PROXY': PROXY_MORTO,
    'https_proxy': PROXY_MORTO,
}

# Una config valida e completa: riconosce «SEGNALE» e mappa le quattro obbligatorie.
CONFIG_OK = {
    'match': {'type': 'contains', 'value': 'SEGNALE'},
    'columns': {
        'EventName': {'source': 'line', 'anchor': 'evento', 'part': 'after', 'marker': ':',
                      'transforms': [{'op': 'trim'}]},
        'MarketType': {'source': 'constant', 'value': 'OVER_UNDER_15'},
        'SelectionName': {'source': 'constant', 'value': 'Over 1,5 goal'},
        'BetType': {'source': 'constant', 'value': 'PUNTA'},
    },
}


@pytest.fixture(autouse=True)
def _ambiente_pulito(monkeypatch):
    for chiave in CHIAVI_PERICOLOSE:
        monkeypatch.delenv(chiave, raising=False)


@pytest.fixture
def servizio(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'SEGRETO_SESSIONE', SEGRETO_ATTESO)
    with relay_avviato(tmp_path, **AMBIENTE_DEL_SERVIZIO) as base:
        yield base


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


def _chiama(base, metodo, path, corpo=None, cookie=None, token=None):
    url = f'{base}{path}'
    if token:
        url += ('&' if '?' in path else '?') + 'token=' + token
    dati = json.dumps(corpo).encode() if corpo is not None else None
    intestazioni = {}
    if dati:
        intestazioni['Content-Type'] = 'application/json'
    if cookie:
        intestazioni['Cookie'] = f'{main.NOME_COOKIE}={cookie}'
    req = urllib.request.Request(url, data=dati, headers=intestazioni, method=metodo)
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
    """Apre una sessione e restituisce (cookie, user_id)."""
    stato, corpo, intestazioni = _chiama(base, 'POST', '/api/login/telegram',
                                         corpo=_dati_login(**extra))
    assert stato == 200, corpo
    return _cookie_dalla_risposta(intestazioni), json.loads(corpo)['utente']


def _login_a(base):
    return _login(base, id=CLIENTE_A, first_name='ClienteA', username='clientea')


def _login_b(base):
    return _login(base, id=CLIENTE_B, first_name='ClienteB', username='clienteb')


_SENZA_CONFIG = object()   # sentinella: distingue «non passato» da un `config` falsy


def _crea(base, cookie, titolo, config=_SENZA_CONFIG, active=True):
    return _chiama(base, 'POST', '/api/me/parsers', cookie=cookie,
                   corpo={'titolo': titolo,
                          'config': CONFIG_OK if config is _SENZA_CONFIG else config,
                          'active': active})


# --------------------------------------------------- il CRUD, dal punto di vista del proprietario

def test_crea_e_lista_i_propri_parser(servizio):
    cookie, _ = _login_a(servizio)
    stato, corpo, _ = _crea(servizio, cookie, 'Test 1')
    assert stato == 200, corpo
    creato = json.loads(corpo)
    assert creato['slug'] == 'test-1' and creato['titolo'] == 'Test 1'
    assert creato['config']['match']['value'] == 'SEGNALE'

    stato, corpo, _ = _chiama(servizio, 'GET', '/api/me/parsers', cookie=cookie)
    assert stato == 200, corpo
    lista = json.loads(corpo)
    slugs = [p['slug'] for p in lista]
    assert 'test-1' in slugs, lista


def test_modifica_cambia_titolo_e_config_ma_NON_lo_slug(servizio):
    cookie, _ = _login_a(servizio)
    slug = json.loads(_crea(servizio, cookie, 'Vecchio')[1])['slug']
    nuovo_config = dict(CONFIG_OK, match={'type': 'contains', 'value': 'ALTRO'})
    stato, corpo, _ = _chiama(servizio, 'PUT', f'/api/me/parsers/{slug}', cookie=cookie,
                              corpo={'titolo': 'Nuovo', 'config': nuovo_config, 'active': False})
    assert stato == 200, corpo
    agg = json.loads(corpo)
    assert agg['slug'] == slug, 'lo slug non deve cambiare con una rinomina'
    assert agg['titolo'] == 'Nuovo' and agg['active'] is False
    assert agg['config']['match']['value'] == 'ALTRO'


def test_elimina_il_proprio_parser(servizio):
    cookie, _ = _login_a(servizio)
    slug = json.loads(_crea(servizio, cookie, 'Da eliminare')[1])['slug']
    assert _chiama(servizio, 'DELETE', f'/api/me/parsers/{slug}', cookie=cookie)[0] == 200
    lista = json.loads(_chiama(servizio, 'GET', '/api/me/parsers', cookie=cookie)[1])
    assert slug not in [p['slug'] for p in lista], 'il parser eliminato e- ancora in lista'
    # Rieliminarlo ora da' 404: non c'e' piu'.
    assert _chiama(servizio, 'DELETE', f'/api/me/parsers/{slug}', cookie=cookie)[0] == 404


# --------------------------------------------------- ISOLAMENTO fra utenti (il cuore)

def test_un_utente_NON_vede_i_parser_di_un_altro(servizio):
    cookie_a, _ = _login_a(servizio)
    _crea(servizio, cookie_a, 'Solo di A')
    cookie_b, _ = _login_b(servizio)

    lista_b = json.loads(_chiama(servizio, 'GET', '/api/me/parsers', cookie=cookie_b)[1])
    assert lista_b == [], f'B vede parser che non sono suoi: {lista_b}'


def test_un_utente_NON_puo_MODIFICARE_il_parser_di_un_altro(servizio):
    cookie_a, _ = _login_a(servizio)
    slug = json.loads(_crea(servizio, cookie_a, 'Di A')[1])['slug']
    cookie_b, _ = _login_b(servizio)

    stato, _, _ = _chiama(servizio, 'PUT', f'/api/me/parsers/{slug}', cookie=cookie_b,
                          corpo={'titolo': 'Rubato', 'config': CONFIG_OK})
    assert stato == 404, 'B ha potuto modificare il parser di A (o ha ricevuto 403, che lo rivela)'
    # E il parser di A e' rimasto intatto.
    di_a = json.loads(_chiama(servizio, 'GET', '/api/me/parsers', cookie=cookie_a)[1])
    assert di_a[0]['titolo'] == 'Di A', di_a


def test_un_utente_NON_puo_ELIMINARE_il_parser_di_un_altro(servizio):
    cookie_a, _ = _login_a(servizio)
    slug = json.loads(_crea(servizio, cookie_a, 'Di A')[1])['slug']
    cookie_b, _ = _login_b(servizio)

    assert _chiama(servizio, 'DELETE', f'/api/me/parsers/{slug}', cookie=cookie_b)[0] == 404
    # A ce l'ha ancora.
    di_a = json.loads(_chiama(servizio, 'GET', '/api/me/parsers', cookie=cookie_a)[1])
    assert [p['slug'] for p in di_a] == [slug], di_a


def test_due_utenti_STESSO_titolo_nessuna_collisione(servizio):
    cookie_a, _ = _login_a(servizio)
    cookie_b, _ = _login_b(servizio)

    a = json.loads(_crea(servizio, cookie_a, 'Test 1')[1])
    b = json.loads(_crea(servizio, cookie_b, 'Test 1')[1])
    assert a['slug'] == 'test-1' and b['slug'] == 'test-1', 'lo slug per-utente e- indipendente'
    assert a['id'] != b['id'], 'due parser distinti devono avere id distinti'
    # E ciascuno vede solo il proprio.
    assert [p['slug'] for p in json.loads(_chiama(servizio, 'GET', '/api/me/parsers', cookie=cookie_a)[1])] == ['test-1']
    assert [p['slug'] for p in json.loads(_chiama(servizio, 'GET', '/api/me/parsers', cookie=cookie_b)[1])] == ['test-1']


def test_user_id_viene_dalla_SESSIONE_non_dal_corpo(servizio):
    """Un `user_id` nel corpo non deve poter assegnare il parser a un altro utente."""
    cookie_a, id_a = _login_a(servizio)
    cookie_b, id_b = _login_b(servizio)

    # A crea, ma prova a intestare il parser a B mettendo user_id nel corpo.
    stato, corpo, _ = _chiama(servizio, 'POST', '/api/me/parsers', cookie=cookie_a,
                              corpo={'titolo': 'Mio', 'config': CONFIG_OK,
                                     'user_id': id_b, 'id': 999})
    assert stato == 200, corpo
    # Il parser e' di A (la sessione), non di B (il corpo): B non lo vede, A si'.
    assert json.loads(_chiama(servizio, 'GET', '/api/me/parsers', cookie=cookie_b)[1]) == []
    di_a = json.loads(_chiama(servizio, 'GET', '/api/me/parsers', cookie=cookie_a)[1])
    assert [p['titolo'] for p in di_a] == ['Mio'], di_a


def test_senza_sessione_le_rotte_me_danno_401(servizio):
    assert _chiama(servizio, 'GET', '/api/me/parsers')[0] == 401
    assert _chiama(servizio, 'POST', '/api/me/parsers',
                   corpo={'titolo': 'X', 'config': CONFIG_OK})[0] == 401


# --------------------------------------------------- validazione e prova a secco

def test_una_config_non_valida_da_422(servizio):
    cookie, _ = _login_a(servizio)
    for cattiva in ([], {'match': 5}, {'columns': []},
                    {'match': {'type': 'regex', 'value': 123}}):
        stato, corpo, _ = _crea(servizio, cookie, 'Rotta', config=cattiva)
        assert stato == 422, f'config {cattiva!r}: atteso 422, ricevuto {stato} ({corpo[:120]!r})'


def test_prova_a_secco_diagnostica_e_NON_tocca_il_feed(servizio):
    cookie, _ = _login_a(servizio)
    slug = json.loads(_crea(servizio, cookie, 'Diagnostica')[1])['slug']

    def feed():
        return _chiama(servizio, 'GET', '/xtrader.csv', token=TOKEN_DI_PROVA)[1]

    prima = feed()

    # Messaggio riconosciuto e completo → matched, complete, csv con l'evento.
    stato, corpo, _ = _chiama(servizio, 'POST', f'/api/me/parsers/{slug}/test', cookie=cookie,
                              corpo={'message': 'SEGNALE\nEvento: Roma v Lazio\n@ 2.10'})
    assert stato == 200, corpo
    esito = json.loads(corpo)
    assert esito['matched'] is True and esito['complete'] is True, esito
    assert esito['event'] == 'Roma v Lazio' and '"Roma v Lazio"' in esito['csv']

    # Messaggio non riconosciuto → la diagnostica dice matched:false, niente csv.
    esito2 = json.loads(_chiama(servizio, 'POST', f'/api/me/parsers/{slug}/test', cookie=cookie,
                                corpo={'message': 'niente di riconoscibile'})[1])
    assert esito2['matched'] is False and esito2['complete'] is False, esito2
    assert 'csv' not in esito2

    # E il feed di PIERO non e' stato toccato dalla prova a secco.
    assert feed() == prima, 'la prova a secco ha scritto nel feed: non deve'


def test_provare_il_parser_di_un_altro_da_404(servizio):
    cookie_a, _ = _login_a(servizio)
    slug = json.loads(_crea(servizio, cookie_a, 'Di A')[1])['slug']
    cookie_b, _ = _login_b(servizio)
    stato, _, _ = _chiama(servizio, 'POST', f'/api/me/parsers/{slug}/test', cookie=cookie_b,
                          corpo={'message': 'SEGNALE\nEvento: A v B'})
    assert stato == 404, 'B ha potuto provare il parser di A'


# --------------------------------------------------- concorrenza e budget regex

def test_creazioni_CONCORRENTI_stesso_titolo_nessun_500(servizio):
    """Sei POST concorrenti stesso titolo → sei slug distinti, nessun 500.

    La race segnalata da GPT-5.5 e Claude Fable 5: `_slug_libero` e `MAX(ordine)`
    sono letti con SELECT separate dall'INSERT, quindi due creazioni simultanee
    calcolerebbero lo stesso slug e il secondo INSERT violerebbe `UNIQUE(user_id,
    slug)`. Senza il retry su `IntegrityError` sarebbe un 500. Il servizio gira i
    gestori sync in un threadpool, quindi la corsa e' raggiungibile davvero.
    """
    import threading
    cookie, _ = _login_a(servizio)
    numero = 6
    porta = threading.Barrier(numero)
    esiti = []

    def prova():
        porta.wait()
        stato, corpo, _ = _crea(servizio, cookie, 'Test 1')
        esiti.append((stato, corpo))

    fili = [threading.Thread(target=prova) for _ in range(numero)]
    for f in fili:
        f.start()
    for f in fili:
        f.join(30)
    assert all(not f.is_alive() for f in fili), 'un thread di creazione non ha finito'
    assert len(esiti) == numero
    falliti = [c[:140] for s, c in esiti if s != 200]
    assert not falliti, f'creazioni concorrenti fallite (atteso 200): {falliti}'
    slugs = sorted(json.loads(c)['slug'] for _, c in esiti)
    assert len(set(slugs)) == numero, f'slug non distinti sotto contesa: {slugs}'


def test_la_creazione_RITENTA_su_collisione_di_slug(tmp_path, monkeypatch):
    """La corsa sullo slug, resa DETERMINISTICA: collisione forzata → retry → successo.

    Il test HTTP concorrente qui sopra mostra che sotto contesa non si rompe, ma il
    lock di SQLite spesso serializza le scritture e la collisione non scatta — quindi
    da solo non e' una prova del retry. Qui la corsa si FORZA: un wrapper della
    connessione, alla prima INSERT del parser, inserisce di nascosto la stessa riga
    PRIMA, cosi' l'INSERT vero solleva `IntegrityError`. E' la corsa esatta (un altro
    POST che prende lo slug fra il SELECT e l'INSERT). Col retry si ricalcola lo slug
    in «test-1-2» e si riesce; togliendo il retry (`range(1)`) questo test e' rosso.
    """
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'corsa.db'))
    main._PERCORSI_MIGRATI.discard(main.DB_PATH)
    reale = main.db()
    config = {'match': {'type': 'contains', 'value': 'X'},
              'columns': {'EventName': {'source': 'constant', 'value': 'X'},
                          'MarketType': {'source': 'constant', 'value': 'Y'},
                          'SelectionName': {'source': 'constant', 'value': 'Z'},
                          'BetType': {'source': 'constant', 'value': 'W'}}}

    class ConnCorsa:
        def __init__(self, sotto):
            self._sotto = sotto
            self._corsa_fatta = False

        def execute(self, sql, params=()):
            if not self._corsa_fatta and sql.lstrip().startswith('INSERT INTO parsers'):
                self._corsa_fatta = True
                self._sotto.execute(sql, params)   # l'altro POST vince la corsa
            return self._sotto.execute(sql, params)

        def __getattr__(self, nome):
            return getattr(self._sotto, nome)

    parser = main._crea_parser_utente(ConnCorsa(reale), 7, 'Test 1', config, True)
    reale.close()
    assert parser['slug'] == 'test-1-2', (
        f"la creazione non ha ritentato dopo la collisione: slug {parser['slug']!r}, "
        'atteso «test-1-2»')


def test_prova_con_regex_CATASTROFICA_e_limitata(servizio):
    """`/test` esegue la regex dell'utente sul messaggio dell'utente, ma col budget.

    Una condizione regex catastrofica passa la creazione (il dry-run gira su 'probe',
    corto e senza 'a'), ma `/test` con un messaggio cattivo eseguirebbe la regex su di
    esso: dev'essere limitata dal budget di `esegui_parser` (~0,1s), non appendere il
    worker — altrimenti sarebbe un DoS autenticato. Segnalato da Fable 5 e GPT-5.5.
    """
    import time
    cookie, _ = _login_a(servizio)
    config = {'match': {'type': 'regex', 'value': '(a|aa)+$'},
              'columns': {'EventName': {'source': 'constant', 'value': 'X'},
                          'MarketType': {'source': 'constant', 'value': 'Y'},
                          'SelectionName': {'source': 'constant', 'value': 'Z'},
                          'BetType': {'source': 'constant', 'value': 'W'}}}
    slug = json.loads(_crea(servizio, cookie, 'Cattiva', config=config)[1])['slug']
    t = time.monotonic()
    stato, _, _ = _chiama(servizio, 'POST', f'/api/me/parsers/{slug}/test', cookie=cookie,
                          corpo={'message': 'a' * 60 + 'b'})
    trascorso = time.monotonic() - t
    assert stato == 200, stato
    assert trascorso < 3.0, (
        f'/test ha impiegato {trascorso:.2f}s su una regex catastrofica: il budget di '
        f'parser non la limita, ed e- un DoS autenticato')
