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


def test_due_PUT_dalla_stessa_base_la_seconda_PERDE_in_modo_visibile(servizio):
    """Il lost update della #51: due sessioni leggono lo stesso parser e salvano
    entrambe — oggi vince l'ULTIMA in silenzio. Con la precondizione, chi salva
    con la versione vecchia riceve 409 col motivo, e il salvataggio del primo
    resta intatto. La PUT SENZA versione resta incondizionata (compat legacy)."""
    cookie, _ = _login_a(servizio)
    creato = json.loads(_crea(servizio, cookie, 'Conteso')[1])
    slug = creato['slug']
    assert 'versione' in creato, 'la vista del parser deve portare la versione'
    base = creato['versione']

    # Prima sessione: salva con la versione letta → vince, versione incrementata.
    config_a = dict(CONFIG_OK, match={'type': 'contains', 'value': 'PRIMA'})
    stato, corpo, _ = _chiama(servizio, 'PUT', f'/api/me/parsers/{slug}', cookie=cookie,
                              corpo={'titolo': 'Conteso', 'config': config_a,
                                     'active': True, 'versione': base})
    assert stato == 200, corpo
    assert json.loads(corpo)['versione'] == base + 1

    # Seconda sessione, STESSA base ormai vecchia → deve perdere in modo VISIBILE.
    config_b = dict(CONFIG_OK, match={'type': 'contains', 'value': 'SECONDA'})
    stato, corpo, _ = _chiama(servizio, 'PUT', f'/api/me/parsers/{slug}', cookie=cookie,
                              corpo={'titolo': 'Conteso', 'config': config_b,
                                     'active': True, 'versione': base})
    assert stato == 409, f'la PUT con la versione vecchia deve perdere: {stato} {corpo}'
    assert 'modificato altrove' in json.loads(corpo)['detail'], corpo

    # Il salvataggio del primo e' intatto.
    lista = json.loads(_chiama(servizio, 'GET', '/api/me/parsers', cookie=cookie)[1])
    vivo = next(p for p in lista if p['slug'] == slug)
    assert vivo['config']['match']['value'] == 'PRIMA', vivo

    # Senza versione: incondizionata come sempre, e la versione avanza comunque.
    stato, corpo, _ = _chiama(servizio, 'PUT', f'/api/me/parsers/{slug}', cookie=cookie,
                              corpo={'titolo': 'Conteso', 'config': config_b, 'active': True})
    assert stato == 200, corpo
    assert json.loads(corpo)['versione'] == base + 2


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

    # E nessuno dei due SCRIVE sull'altro, benche' lo slug sia identico
    # (suggerito da GPT-5.5 sulla PR #74: con `uid` la separazione non e' piu'
    # ovvia a lettura, quindi va misurata). Entrambe le rotte leggono la riga
    # con `WHERE user_id=? AND slug=?`, quindi ognuno trova solo la propria.
    stato, _, _ = _chiama(servizio, 'PUT', '/api/me/parsers/test-1', cookie=cookie_a,
                          corpo={'titolo': 'Solo di A', 'config': CONFIG_OK,
                                 'active': True})
    assert stato == 200
    di_b = next(p for p in json.loads(
        _chiama(servizio, 'GET', '/api/me/parsers', cookie=cookie_b)[1])
        if p['slug'] == 'test-1')
    assert di_b['titolo'] == 'Test 1', \
        f'la PUT di A ha toccato il parser omonimo di B: {di_b}'
    assert _chiama(servizio, 'DELETE', '/api/me/parsers/test-1',
                   cookie=cookie_a)[0] == 200
    assert [p['slug'] for p in json.loads(
        _chiama(servizio, 'GET', '/api/me/parsers', cookie=cookie_b)[1])] == ['test-1'], \
        'la DELETE di A ha portato via anche il parser omonimo di B'


def test_user_id_viene_dalla_SESSIONE_non_dal_corpo(servizio):
    """Un `user_id` nel corpo non deve poter assegnare il parser a un altro utente."""
    cookie_a, _ = _login_a(servizio)
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


def test_DELETE_pulisce_parser_chats_e_solo_quelle(servizio, tmp_path):
    """La cancellazione rimuove le associazioni chat DEL parser, e solo quelle —
    né quelle di un altro parser dello stesso utente, né quelle di un ALTRO UTENTE.

    `parser_chats` è vuota oggi (nessun codice la scrive ancora), quindi la si popola
    a mano nel DB del sottoprocesso per esercitare la pulizia aggiunta come fix del
    bloccante (GPT-5.5, Fable 5): un parser cancellato non deve lasciare associazioni
    orfane che il dispatch futuro seguirebbe. La pulizia è **scoped** all'`id` del
    parser cancellato — e il caso cross-utente (chiesto da GPT-5.5) lo dimostra:
    l'eliminazione di un parser dell'utente A non tocca le associazioni di B.
    """
    import sqlite3
    cookie_a, _ = _login_a(servizio)
    cookie_b, _ = _login_b(servizio)
    a1 = json.loads(_crea(servizio, cookie_a, 'Uno')[1])
    a2 = json.loads(_crea(servizio, cookie_a, 'Due')[1])
    b1 = json.loads(_crea(servizio, cookie_b, 'Di B')[1])

    db = sqlite3.connect(tmp_path / 'signals.db')
    for pid, chat in ((a1['id'], 111), (a2['id'], 222), (b1['id'], 333)):
        db.execute('INSERT INTO parser_chats(parser_id, chat_id) VALUES (?,?)', (pid, chat))
    db.commit()
    db.close()

    # A elimina il proprio parser a1.
    assert _chiama(servizio, 'DELETE', f'/api/me/parsers/{a1["slug"]}', cookie=cookie_a)[0] == 200

    db = sqlite3.connect(tmp_path / 'signals.db')
    restano = sorted(r[0] for r in db.execute('SELECT parser_id FROM parser_chats').fetchall())
    db.close()
    # Resta l'altro parser di A e — soprattutto — quello di B, intatto.
    assert restano == sorted([a2['id'], b1['id']]), (
        f'DELETE ha lasciato orfani o toccato associazioni di un altro (anche di un '
        f'altro UTENTE): {restano}')


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


def test_una_config_con_COLONNA_non_eseguibile_da_422(servizio):
    """Anche una COLONNA storta va rifiutata, non solo la condizione.

    `esegui_parser` costruisce sempre la `row` (esegue TUTTE le colonne), a
    prescindere dal fatto che la condizione riconosca «probe»: quindi il dry-run di
    `_valida_config_parser` esegue le regole delle colonne e una regola non eseguibile
    (qui un `pattern` non-stringa) da' 422. GPT-5.6 Sol dubitava che le colonne fossero
    validate quando `match` non combacia con «probe»: questo caso lo verifica, sia con
    un match che combacia sia con uno che NON combacia.
    """
    cookie, _ = _login_a(servizio)
    for match in ({'type': 'contains', 'value': 'x'},           # 'probe' non contiene 'x'
                  {'type': 'contains', 'value': 'probe'}):        # 'probe' contiene 'probe'
        cfg = {'match': match, 'columns': {'EventName': {'source': 'regex', 'pattern': 123}}}
        stato, corpo, _ = _crea(servizio, cookie, 'Colonna storta', config=cfg)
        assert stato == 422, (
            f'colonna non eseguibile con match {match!r}: atteso 422, ricevuto {stato} '
            f'({corpo[:120]!r})')


def test_prova_senza_sessione_da_401_anche_con_corpo_INVALIDO(servizio):
    """La rotta `/test` non deve rivelare la propria esistenza con un 422.

    Prima leggeva `MessageIn` nella firma: FastAPI validava il corpo PRIMA della
    sessione, quindi un corpo malformato senza cookie dava 422 (cioè «la rotta
    esiste») invece di 401. Ora il corpo si legge dopo il controllo di sessione.
    Segnalato da Claude Fable 5 e GPT-5.6 Sol sulla PR #30.
    """
    # Corpo che NON passerebbe MessageIn ('message' manca), senza cookie → 401, non 422.
    stato, _, _ = _chiama(servizio, 'POST', '/api/me/parsers/qualunque/test', corpo={})
    assert stato == 401, f'atteso 401, ricevuto {stato}: la rotta rivela di esistere'
    # E con la sessione ma corpo invalido → 422 (ora, dopo il 401).
    cookie, _ = _login_a(servizio)
    slug = json.loads(_crea(servizio, cookie, 'X')[1])['slug']
    stato, _, _ = _chiama(servizio, 'POST', f'/api/me/parsers/{slug}/test', cookie=cookie, corpo={})
    assert stato == 422, f'corpo senza «message» con sessione: atteso 422, ricevuto {stato}'


def test_creazione_disambigua_su_collisione_di_NAME_legacy(tmp_path, monkeypatch):
    """Un parser gia' chiamato `u{id}-{slug}` (admin/legacy) non blocca la creazione.

    Il `name` è PRIMARY KEY globale. Se un parser esiste gia' con quel `name` ma NON
    è fra gli slug dell'utente, ricalcolare dagli soli slug dell'utente darebbe
    all'infinito lo stesso `name` → 409 permanente. Segnando lo slug come «bruciato»,
    il giro dopo `_slug_libero` ne prende un altro. Segnalato da Fable 5 e Sol, PR #30.
    """
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'legacy.db'))
    main._PERCORSI_MIGRATI.discard(main.DB_PATH)
    c = main.db()
    # Un parser gia' chiamato come quello che l'utente 7 genererebbe (name PK globale).
    c.execute("INSERT INTO parsers(name, header) VALUES ('u7-test-1', '')")
    c.commit()
    config = {'match': {'type': 'contains', 'value': 'x'},
              'columns': {'EventName': {'source': 'constant', 'value': 'X'}}}
    parser = main._crea_parser_utente(c, 7, 'Test 1', config, True)
    c.close()
    assert parser['slug'] == 'test-1-2', (
        f"la creazione non ha disambiguato sul name legacy: slug {parser['slug']!r}")


def test_crea_CHIUDE_la_connessione_anche_su_409(tmp_path, monkeypatch):
    """Il fail-first del leak: `_crea_parser_utente` che solleva 409 non deve lasciare
    la connessione aperta con la transazione in corso. Segnalato da Claude Fable 5.
    """
    import asyncio
    import sqlite3
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'leak.db'))
    main._PERCORSI_MIGRATI.discard(main.DB_PATH)
    connessioni = []
    reale = main.db
    monkeypatch.setattr(main, 'db', lambda: connessioni.append(reale()) or connessioni[-1])
    monkeypatch.setattr(main, '_sessione_valida', lambda r: {'id': 1, 'versione': 1})

    def solleva_409(*a, **k):
        raise main.HTTPException(409, 'contesa')

    monkeypatch.setattr(main, '_crea_parser_utente', solleva_409)

    class Req:
        # L'interfaccia reale di lettura del corpo (`_json_dal_corpo`): headers + stream.
        grezzo = json.dumps({'titolo': 'X', 'config': {'match': {'type': 'contains',
                                                                 'value': 'zzz'}}}).encode()
        headers = {'content-length': str(len(grezzo))}

        async def stream(self):
            yield self.grezzo

    with pytest.raises(main.HTTPException) as e:
        asyncio.run(main.crea_parser_mio(Req()))
    assert e.value.status_code == 409
    assert connessioni, 'db() non e- stato chiamato'
    with pytest.raises(sqlite3.ProgrammingError):
        connessioni[0].execute('SELECT 1')   # una execute su connessione chiusa solleva


def test_PUT_che_perde_la_corsa_con_una_DELETE_da_404_non_500(tmp_path, monkeypatch):
    """PUT su un parser svuotato da una DELETE concorrente → **404**, non 500.

    `elimina_parser_mio` e' sync (FastAPI la esegue nel threadpool anyio),
    `modifica_parser_mio` e' async (event loop): una DELETE puo' vincere la corsa e
    svuotare la riga fra il SELECT iniziale e l'UPDATE del PUT. Senza guardia il SELECT
    finale torna `None` e `_vista_parser(None)` solleva `TypeError` → 500. Con la
    guardia: 404, come se la cancellazione avesse vinto. E' una self-race dello STESSO
    utente (stesso `user_id`+`slug`), non un leak fra utenti. Bloccante di GPT-5.6 Sol,
    PR #30.

    Fail-first: sul codice VECCHIO qui vola un `TypeError`, che `pytest.raises(
    HTTPException)` non cattura → il test e' rosso. Con la guardia diventa 404.
    """
    import asyncio
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'corsa_put.db'))
    main._PERCORSI_MIGRATI.discard(main.DB_PATH)
    reale = main.db()
    config = {'match': {'type': 'contains', 'value': 'x'},
              'columns': {'EventName': {'source': 'constant', 'value': 'X'}}}
    parser = main._crea_parser_utente(reale, 1, 'Test 1', config, True)
    reale.commit()
    slug = parser['slug']

    class ConnCorsaDelete:
        """Alla prima UPDATE del PUT, svuota la riga PRIMA: la DELETE vince la corsa."""

        def __init__(self, sotto):
            self._sotto = sotto
            self._fatta = False

        def execute(self, sql, params=()):
            if not self._fatta and sql.lstrip().startswith('UPDATE parsers SET titolo'):
                self._fatta = True
                self._sotto.execute('DELETE FROM parsers WHERE user_id=? AND slug=?',
                                    (1, slug))
            return self._sotto.execute(sql, params)

        def __getattr__(self, nome):
            return getattr(self._sotto, nome)

    corsa = ConnCorsaDelete(reale)
    monkeypatch.setattr(main, 'db', lambda: corsa)
    monkeypatch.setattr(main, '_sessione_valida', lambda r: {'id': 1, 'versione': 1})

    class Req:
        # L'interfaccia reale di lettura del corpo (`_json_dal_corpo`): headers + stream.
        grezzo = json.dumps({'titolo': 'Rinominato', 'config': config}).encode()
        headers = {'content-length': str(len(grezzo))}

        async def stream(self):
            yield self.grezzo

    try:
        with pytest.raises(main.HTTPException) as e:
            asyncio.run(main.modifica_parser_mio(slug, Req()))
    finally:
        reale.close()
    # Senza questa asserzione il test passerebbe A VUOTO se l'UPDATE venisse riscritto e
    # il wrapper non intercettasse piu' nulla: la guardia 404 non sarebbe esercitata.
    assert corsa._fatta, 'la DELETE forzata non e- scattata: il test non prova la guardia'
    assert e.value.status_code == 404, (
        f'PUT che perde la corsa con una DELETE: atteso 404, ricevuto '
        f'{e.value.status_code}')


def test_la_migrazione_retrocompila_il_titolo_dei_parser_legacy(tmp_path, monkeypatch):
    """Un parser preesistente (schema pre-`titolo`) non deve restare con `titolo` NULL.

    La colonna `titolo` e' additiva e nullable, ma il contratto API dichiara
    `titolo: str`: il proprietario loggato che chiama `GET /api/me/parsers` vedrebbe
    `titolo: null` sul parser PIERO di default. La migrazione lo retrocompila dal
    `name` — un'etichetta onesta, non un dato inventato. Bloccante di GPT-5.6 Sol, PR #30.

    Fail-first: senza il backfill in `_completa_colonne_nuove`, `titolo` resta NULL e
    l'asserzione fallisce; con esso vale `name`.
    """
    import sqlite3
    percorso = str(tmp_path / 'legacy_titolo.db')
    # Un DB nello schema ORIGINALE (nessuna colonna `titolo`), con una riga legacy.
    c = sqlite3.connect(percorso)
    for istruzione in main.SCHEMA_ORIGINALE:
        c.execute(istruzione)
    c.execute("INSERT INTO parsers(name, header) VALUES ('vecchio_parser', 'H')")
    c.commit()
    c.close()

    monkeypatch.setattr(main, 'DB_PATH', percorso)
    main._PERCORSI_MIGRATI.discard(percorso)
    conn = main.db()   # esegue migra()
    riga = conn.execute("SELECT titolo FROM parsers WHERE name='vecchio_parser'").fetchone()
    conn.close()
    assert riga is not None and riga[0], (
        f'la migrazione non ha retrocompilato il titolo del parser legacy: {riga!r}')


def test_config_con_NaN_o_Infinity_da_422_e_non_viene_MAI_scritta(servizio):
    """Numeri JSON non-finiti nella config → **422** alla scrittura, mai memorizzati.

    `request.json()` accetta `NaN`/`Infinity` (Python li ammette) e `json.dumps` di
    default li scriverebbe come JSON NON standard; poi `JSONResponse` li rifiuta quando
    riserializza la config, e l'utente prenderebbe un **500 su OGNI lista/creazione** che
    include quel parser. `esegui_parser` non li tocca (campo inutilizzato), quindi la
    guardia dev'essere un `json.dumps(config, allow_nan=False)` esplicito alla scrittura.
    Bloccante Major di CodeRabbit sulla PR #30.

    Fail-first: sul codice VECCHIO la POST torna **500** (config scritta, poi la risposta
    non si riserializza) invece di 422.
    """
    cookie, _ = _login_a(servizio)
    for cattivo in (float('nan'), float('inf'), float('-inf')):
        cfg = {'match': {'type': 'contains', 'value': 'X'},
               'columns': {'EventName': {'source': 'constant', 'value': 'X'}},
               'inutile': cattivo}
        stato, corpo, _ = _crea(servizio, cookie, 'Cattiva', config=cfg)
        assert stato == 422, (
            f'config con {cattivo!r}: atteso 422, ricevuto {stato} ({corpo[:120]!r})')
    # E nessun parser e' stato scritto: la lista dell'utente resta vuota.
    assert json.loads(_chiama(servizio, 'GET', '/api/me/parsers', cookie=cookie)[1]) == [], (
        'una config non-finita e- stata scritta comunque: la lista non e- vuota')


def test_l_eliminazione_del_parser_e_vincolata_anche_nel_write_lock(tmp_path,
                                                                    monkeypatch):
    """Issue #65: la DELETE dei parser dopo un travaso concorrente.

    La rotta leggeva `parsers.id` (proprieta' verificata: una LETTURA), poi
    eseguiva `DELETE FROM parser_chats WHERE parser_id=?` SENZA vincolo di
    proprieta'. Misurato sul codice precedente, col travaso gia' committato
    quando gli statement partono: il parser — ormai dell'account superstite —
    sopravviveva alla sua DELETE (che filtra per user_id e slug), ma i suoi
    LINK chat→parser venivano distrutti, e la rotta rispondeva `ok: true`:
    il parser del superstite smetteva di ricevere dalla sua chat, in silenzio.

    `_elimina_parser` ripete il vincolo dentro ENTRAMBI gli statement: per il
    proprietario sbagliato zero righe toccate e None al chiamante (la rotta
    risponde 404); per quello vero via il parser E i suoi link.

    Dalla #73 la riga si identifica con `uid` invece che con `id` + `slug`: il
    vincolo `user_id` che questo test misura non cambia, cambia solo la chiave
    con cui i due statement nominano il parser.
    """
    import sqlite3
    from tests.dati import relay_in_processo
    percorso = relay_in_processo(monkeypatch, tmp_path / 'parser65.db')
    c = sqlite3.connect(percorso)
    utenti = {}
    for slug in ('svuotato', 'superstite'):
        c.execute("INSERT INTO users(slug, first_name, status) VALUES (?, ?, 'attivo')",
                  (slug, slug.capitalize()))
        utenti[slug] = c.execute('SELECT id FROM users WHERE slug=?',
                                 (slug,)).fetchone()[0]
    # Il parser e la sua chat come li lascia il travaso: gia' del superstite.
    c.execute("INSERT INTO parsers(name, header, user_id, slug, titolo,"
              " config_json, active, ordine, uid) VALUES ('u-b-conteso', '', ?,"
              " 'conteso', 'Conteso', '{}', 1, 0, ?)",
              (utenti['superstite'], main._uid_parser(c)))
    c.execute("UPDATE parsers SET id=rowid WHERE name='u-b-conteso'")
    pid, puid = c.execute(
        "SELECT id, uid FROM parsers WHERE name='u-b-conteso'").fetchone()
    c.execute('INSERT INTO chats(telegram_chat_id, owner_user_id) VALUES (?,?)',
              ('-100999', utenti['superstite']))
    chat = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    c.execute('INSERT INTO parser_chats(parser_id, chat_id) VALUES (?,?)', (pid, chat))

    # La richiesta stantia dell'account svuotato, che aveva letto `pid` quando
    # il parser era suo: zero righe toccate, None al chiamante.
    assert main._elimina_parser(c, utenti['svuotato'], puid) is None
    assert c.execute('SELECT COUNT(*) FROM parsers WHERE id=?',
                     (pid,)).fetchone()[0] == 1
    assert c.execute('SELECT COUNT(*) FROM parser_chats WHERE parser_id=?',
                     (pid,)).fetchone()[0] == 1, \
        'i link del proprietario superstite non si toccano'

    # Il proprietario vero: via il parser E i suoi link.
    assert main._elimina_parser(c, utenti['superstite'], puid) is True
    assert c.execute('SELECT COUNT(*) FROM parsers WHERE id=?',
                     (pid,)).fetchone()[0] == 0
    assert c.execute('SELECT COUNT(*) FROM parser_chats WHERE parser_id=?',
                     (pid,)).fetchone()[0] == 0
    c.close()


def test_l_eliminazione_stantia_NON_colpisce_un_parser_ricreato_stesso_slug(
        tmp_path, monkeypatch):
    """La race ABA della DELETE, nei DUE casi — e la storia di come si e' chiusa.

    [REAL_FINDING] di GPT-5.6 Sol al gate finale della PR #72. La cascata dei
    link era vincolata per `id`, la DELETE del parser per `(user_id, slug)`:
    fra la lettura e il write-lock un elimina+ricrea concorrente dello STESSO
    slug faceva divergere i due statement — la richiesta stantia cancellava il
    parser RICREATO e i suoi link restavano orfani. Misurato allora: la stantia
    restituiva True, il ricreato spariva, il link restava.

    La #72 lego' entrambi gli statement all'`id`, e chiuse **un caso su due**.
    `parsers` e' la tabella originale SENZA AUTOINCREMENT: se il parser
    eliminato deteneva il rowid massimo, sqlite lo riusa e il ricreato riceve lo
    stesso `id` — indistinguibile per qualunque filtro. La #72 lo dichiaro'
    aperto invece di tacerlo, e la #73 lo chiude con `uid`, che non si riusa
    mai. **Cambia quindi l'esito atteso del secondo scenario**: prima era «la
    DELETE arriva per ultima e porta via il ricreato con i suoi link» (coerente
    ma distruttivo), ora e' «la stantia non tocca niente», identico al primo.

    I due scenari restano separati perche' misurano cose diverse: il primo che
    il vincolo funziona quando gli id differiscono, il secondo che funziona
    ANCHE quando l'id e' lo stesso — cioe' che l'identita' usata non e' l'id.
    """
    import sqlite3
    from tests.dati import relay_in_processo
    percorso = relay_in_processo(monkeypatch, tmp_path / 'aba65.db')
    c = sqlite3.connect(percorso)
    c.execute("INSERT INTO users(slug, first_name, status) VALUES ('solo', 'Solo', 'attivo')")
    uid = c.execute("SELECT id FROM users WHERE slug='solo'").fetchone()[0]

    def crea(nome):
        c.execute('INSERT INTO parsers(name, header, user_id, slug, titolo,'
                  " config_json, active, ordine, uid) VALUES (?, '', ?, 'rifatto',"
                  " 'Rifatto', '{}', 1, 0, ?)", (nome, uid, main._uid_parser(c)))
        c.execute('UPDATE parsers SET id=rowid WHERE name=?', (nome,))
        return c.execute('SELECT id, uid FROM parsers WHERE name=?',
                         (nome,)).fetchone()

    vecchio = crea('u-a-rifatto')
    # La sentinella tiene occupato il rowid massimo, cosi' il ricreato ne prende
    # uno NUOVO: e' il primo scenario, quello con le identita' gia' distinte.
    c.execute("INSERT INTO parsers(name, header, user_id, slug, titolo,"
              " config_json, active, ordine, uid) VALUES ('u-a-sentinella', '', ?,"
              " 'sentinella', 'Sentinella', '{}', 1, 1, ?)", (uid, main._uid_parser(c)))
    # La richiesta stantia ha letto `vecchio`; ADESSO l'elimina+ricrea
    # concorrente committa: stesso slug, riga nuova (name diverso: e' PK).
    c.execute('DELETE FROM parser_chats WHERE parser_id=?', (vecchio[0],))
    c.execute('DELETE FROM parsers WHERE id=?', (vecchio[0],))
    nuovo = crea('u-a-rifatto-2')
    assert nuovo[0] != vecchio[0], 'lo scenario esige due id distinti'
    c.execute('INSERT INTO chats(telegram_chat_id, owner_user_id) VALUES (?,?)',
              ('-100888', uid))
    chat = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    c.execute('INSERT INTO parser_chats(parser_id, chat_id) VALUES (?,?)',
              (nuovo[0], chat))

    # ...e la richiesta stantia prosegue con l'uid vecchio: non tocca niente.
    assert main._elimina_parser(c, uid, vecchio[1]) is None
    assert c.execute('SELECT COUNT(*) FROM parsers WHERE id=?',
                     (nuovo[0],)).fetchone()[0] == 1, \
        'il parser ricreato non si tocca: la richiesta stantia parlava di un altro'
    assert c.execute('SELECT COUNT(*) FROM parser_chats WHERE parser_id=?',
                     (nuovo[0],)).fetchone()[0] == 1
    # E per il proprietario col dato FRESCO tutto funziona come sempre.
    assert main._elimina_parser(c, uid, nuovo[1]) is True
    assert c.execute('SELECT COUNT(*) FROM parser_chats').fetchone()[0] == 0, \
        'nessun link orfano: la cascata e la DELETE colpiscono la stessa riga'

    # Secondo scenario: il parser eliminato detiene il rowid MASSIMO, sqlite lo
    # riusa, e il ricreato ha lo STESSO id. Prima della #73 nessun filtro poteva
    # separarli e la stantia lo cancellava; con `uid` l'id riusato non basta piu'
    # a farsi passare per la riga vecchia.
    c.execute("DELETE FROM parsers WHERE name='u-a-sentinella'")
    vecchio2 = crea('u-a-riuso')             # ora detiene il rowid massimo
    c.execute('DELETE FROM parser_chats WHERE parser_id=?', (vecchio2[0],))
    c.execute('DELETE FROM parsers WHERE id=?', (vecchio2[0],))
    nuovo2 = crea('u-a-riuso-2')             # sqlite riusa il massimo: id uguale
    assert nuovo2[0] == vecchio2[0], \
        ('lo scenario esige il riuso del rowid: se questa riga fallisce lo'
         ' schema di `parsers` e\' cambiato (AUTOINCREMENT aggiunto) e il caso'
         ' qui sotto non esiste piu\' — va riconsiderato, non silenziato')
    assert nuovo2[1] != vecchio2[1], 'l\'uid invece non si riusa mai: e\' il punto'
    c.execute('INSERT INTO chats(telegram_chat_id, owner_user_id) VALUES (?,?)',
              ('-100777', uid))
    chat2 = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    c.execute('INSERT INTO parser_chats(parser_id, chat_id) VALUES (?,?)',
              (nuovo2[0], chat2))
    assert main._elimina_parser(c, uid, vecchio2[1]) is None, \
        'con l\'id riusato la stantia NON deve piu\' colpire il ricreato (#73)'
    assert c.execute('SELECT COUNT(*) FROM parsers WHERE slug=?',
                     ('rifatto',)).fetchone()[0] == 1
    assert c.execute('SELECT COUNT(*) FROM parser_chats').fetchone()[0] == 1
    # E il proprietario con l'uid fresco elimina riga e link insieme.
    assert main._elimina_parser(c, uid, nuovo2[1]) is True
    assert c.execute('SELECT COUNT(*) FROM parsers WHERE slug=?',
                     ('rifatto',)).fetchone()[0] == 0
    assert c.execute('SELECT COUNT(*) FROM parser_chats').fetchone()[0] == 0, \
        'nessun link orfano nemmeno qui'
    c.close()


def test_una_PUT_stantia_NON_riscrive_un_parser_ricreato_stesso_slug(tmp_path,
                                                                     monkeypatch):
    """Issue #73: l'identita' del parser deve essere NON RIUSABILE.

    Lo stesso ABA della DELETE vive sulla PUT, e con una conseguenza peggiore.
    `parsers` e' la tabella originale senza AUTOINCREMENT: sqlite riusa il rowid
    massimo, quindi un elimina+ricrea dello stesso slug produce una riga NUOVA
    indistinguibile dalla vecchia — stesso id, stesso user_id, stesso slug,
    stesso name. La precondizione di `versione` (#51) non la separa: `versione`
    parte da 1 e il ricreato ha 1, cioe' proprio quella che la richiesta stantia
    porta con se'.

    Misurato sul codice precedente: la PUT stantia toccava 1 riga e
    SOVRASCRIVEVA titolo e `config_json` del parser appena ricreato. E'
    peggio di una cancellazione: il parser resta e continua a funzionare, ma con
    le regole di parsing vecchie — cioe' produce righe CSV sbagliate verso
    XTrader senza nessun sintomo visibile.

    Il rimedio e' `parsers.uid`, assegnato alla creazione e mai riusato: la
    richiesta stantia porta l'uid di una riga che non esiste piu', tocca zero
    righe, e la rotta risponde 404 — come se la sostituzione fosse arrivata
    prima. La precondizione di `versione` resta dov'e': copre il caso diverso
    (due sessioni che salvano lo stesso parser VIVO), e qui non c'entra.
    """
    import sqlite3
    from tests.dati import relay_in_processo
    percorso = relay_in_processo(monkeypatch, tmp_path / 'aba73.db')
    c = sqlite3.connect(percorso)
    c.execute("INSERT INTO users(slug, first_name, status) VALUES ('solo','Solo','attivo')")
    uid = c.execute("SELECT id FROM users WHERE slug='solo'").fetchone()[0]

    def crea(nome, titolo):
        c.execute('INSERT INTO parsers(name, header, user_id, slug, titolo,'
                  " config_json, active, ordine, uid) VALUES (?, '', ?, 'bet365',"
                  " ?, '{}', 1, 0, ?)", (nome, uid, titolo, main._uid_parser(c)))
        c.execute('UPDATE parsers SET id=rowid WHERE name=?', (nome,))
        return c.execute('SELECT id, uid FROM parsers WHERE name=?',
                         (nome,)).fetchone()

    vecchio = crea('u-a-bet365', 'Bet365 vecchio')
    # La richiesta PUT ha letto il parser (id e uid). ADESSO l'elimina+ricrea
    # concorrente committa, e il ricreato riprende il rowid massimo.
    c.execute('DELETE FROM parser_chats WHERE parser_id=?', (vecchio[0],))
    c.execute('DELETE FROM parsers WHERE id=?', (vecchio[0],))
    nuovo = crea('u-a-bet365-2', 'Bet365 NUOVO')
    assert nuovo[0] == vecchio[0], 'lo scenario esige il riuso del rowid'
    assert nuovo[1] != vecchio[1], 'l\'uid invece NON si riusa: e\' il punto'

    # La PUT stantia, con l'uid che aveva letto: zero righe toccate.
    assert main._aggiorna_parser(
        c, uid, vecchio[1], 'bet365', 'Titolo STANTIO',
        '{"match": {"type": "contains", "value": "STANTIO"}}', True, None) is None
    titolo, config = c.execute(
        "SELECT titolo, config_json FROM parsers WHERE slug='bet365'").fetchone()
    assert titolo == 'Bet365 NUOVO', \
        f'la PUT stantia ha riscritto il parser ricreato: {titolo!r}'
    assert 'STANTIO' not in config, \
        f'la config del parser ricreato e\' stata sovrascritta: {config!r}'

    # E il proprietario col dato FRESCO salva come sempre.
    assert main._aggiorna_parser(
        c, uid, nuovo[1], 'bet365', 'Bet365 rinominato', '{}', True, None) is True
    assert c.execute("SELECT titolo FROM parsers WHERE slug='bet365'"
                     ).fetchone()[0] == 'Bet365 rinominato'
    c.close()


def test_l_uid_e_unico_e_le_righe_esistenti_lo_ricevono_dalla_migrazione(
        tmp_path, monkeypatch):
    """La colonna nasce su un database GIA' popolato: le righe legacy vanno
    riempite, e con valori DISTINTI — un default costante darebbe a tutti i
    parser la stessa identita', cioe' il difetto che la colonna deve chiudere.
    La migrazione e' idempotente: rigirarla non cambia gli uid gia' assegnati.
    """
    import sqlite3
    from tests.dati import relay_in_processo
    percorso = relay_in_processo(monkeypatch, tmp_path / 'uid.db')
    c = sqlite3.connect(percorso)
    # Il parser legacy della produzione simulata c'e' gia' (`semina_produzione`):
    # la migrazione al primo avvio deve avergli dato un uid.
    uid_legacy = c.execute('SELECT uid FROM parsers WHERE name=?',
                           (main.DEFAULT_PARSER,)).fetchone()[0]
    assert uid_legacy, 'la riga legacy e\' rimasta senza uid'
    c.execute("INSERT INTO users(slug, first_name, status) VALUES ('u','U','attivo')")
    utente = c.execute("SELECT id FROM users WHERE slug='u'").fetchone()[0]
    primo = main._crea_parser_utente(c, utente, 'Uno', {}, True)
    secondo = main._crea_parser_utente(c, utente, 'Due', {}, True)
    c.commit()
    uids = [r[0] for r in c.execute('SELECT uid FROM parsers').fetchall()]
    assert all(uids), f'un parser e\' senza uid: {uids}'
    assert len(set(uids)) == len(uids), f'due parser con lo stesso uid: {uids}'
    assert primo['slug'] != secondo['slug']
    # Dalla #75 `uid` ESCE dall'API, ed e' un cambiamento deliberato: serve al
    # client per dire quale riga intende modificare (vedi la PUT dalla scheda
    # vecchia). Fino alla #74 era identita' interna e questo test asseriva il
    # contrario — l'inversione e' il fatto, non un incidente.
    # Non e' un segreto: e' un identificatore opaco delle PROPRIE righe, che la
    # sessione gia' autorizza a leggere e modificare. Cio' che resta interno e'
    # `name` (identita' globale fra tutti gli utenti) e `user_id`.
    assert primo['uid'] and secondo['uid'], primo
    assert primo['uid'] != secondo['uid'], 'due parser, due identita\''
    assert 'name' not in primo and 'user_id' not in primo, primo

    # Idempotenza: una seconda migrazione non ribatte gli uid gia' assegnati.
    prima = dict(c.execute('SELECT name, uid FROM parsers').fetchall())
    main._PERCORSI_MIGRATI = set()
    main.db().close()
    dopo = dict(c.execute('SELECT name, uid FROM parsers').fetchall())
    assert prima == dopo, f'la migrazione ha riscritto gli uid: {prima} -> {dopo}'
    c.close()


def test_NESSUN_percorso_di_creazione_lascia_un_parser_senza_uid(tmp_path, monkeypatch):
    """Rischio segnalato da GPT-5.5 sulla PR #74, verificato invece che dedotto.

    `uid` e' nullable — `ALTER TABLE ADD COLUMN NOT NULL` esige un default
    costante, e un uid costante su tutte le righe sarebbe l'esatto contrario di
    un'identita'. In sqlite un indice UNIQUE tollera piu' NULL, quindi lo schema
    da solo non basta: un parser con `uid IS NULL` sarebbe **visibile ma
    immortale**, perche' ne' `uid=?` della PUT ne' quello della DELETE
    combaciano mai con NULL. La garanzia deve venire dai percorsi di scrittura,
    ed e' qui che va misurata.

    I percorsi che inseriscono in `parsers` sono DUE (grep `INSERT INTO parsers`
    su `main.py`):

    - `_crea_parser_utente`, la rotta del cliente: nomina `uid` nell'INSERT;
    - `save_parser`, la rotta admin `POST /api/parsers`: **non** lo nomina, ma
      chiama `_completa_colonne_nuove` nello stesso commit, che riempie gli uid
      mancanti. E' coperto per conseguenza, non per costruzione — e questo test
      e' cio' che lo tiene vero: se un domani quella chiamata sparisse, o
      nascesse un terzo percorso senza ne' l'una ne' l'altra cosa, qui diventa
      rosso invece di produrre un parser immortale in silenzio.
    """
    import sqlite3
    from tests.dati import relay_in_processo
    percorso = relay_in_processo(monkeypatch, tmp_path / 'senza_uid.db')
    c = sqlite3.connect(percorso)
    senza = "SELECT COUNT(*) FROM parsers WHERE uid IS NULL"

    # 1) La riga legacy della produzione simulata, riempita dalla migrazione.
    assert c.execute(senza).fetchone()[0] == 0, 'la migrazione ha lasciato un uid NULL'

    # 2) Il percorso del cliente.
    c.execute("INSERT INTO users(slug, first_name, status) VALUES ('u','U','attivo')")
    utente = c.execute("SELECT id FROM users WHERE slug='u'").fetchone()[0]
    main._crea_parser_utente(c, utente, 'Del cliente', {}, True)
    c.commit()
    assert c.execute(senza).fetchone()[0] == 0, \
        'la creazione dal cliente ha lasciato un parser senza uid'

    # 3) Il percorso ADMIN, che non nomina `uid` nel suo INSERT: si esercita la
    #    funzione vera della rotta, non una sua imitazione.
    campi = {campo: '' for campo in main.ParserIn.model_fields}
    campi.update(name='parser-admin', header='INTESTAZIONE')
    # `auth()` esige il token del relay: la fixture in processo lo azzera per non
    # dipendere dalla macchina, quindi qui se ne mette uno finto e si passa QUELLO.
    monkeypatch.setattr(main, 'TOKEN', 'token-di-prova-per-la-rotta-admin')
    main.save_parser(main.ParserIn(**campi), main.TOKEN)
    c2 = sqlite3.connect(percorso)
    assert c2.execute(senza).fetchone()[0] == 0, \
        'la creazione dall\'admin ha lasciato un parser senza uid'
    assert c2.execute('SELECT COUNT(*) FROM parsers WHERE name=?',
                      ('parser-admin',)).fetchone()[0] == 1, 'il parser admin non c\'e\''
    c2.close()
    c.close()


def test_una_PUT_dalla_scheda_VECCHIA_non_sovrascrive_il_parser_ricreato(servizio):
    """La finestra client->server, CHIUSA (#75) — ed e' il test della PR #74 che
    la fotografava, ora invertito: era scritto apposta perche' diventasse rosso
    il giorno in cui il limite fosse caduto, e quel giorno e' questo.

    Lo scenario e' quello vero: due schede aperte. Nella prima l'utente elimina
    il parser e lo ricrea (per ripartire da zero). Nella seconda, rimasta aperta
    da prima, preme Salva.

    Misurato PRIMA della patch: `200`, con titolo e `config_json` del parser
    ricreato sovrascritti da quelli della scheda vecchia — e `config_json` e'
    cio' che genera le righe CSV verso XTrader, quindi il parser continuava a
    girare con le regole che l'utente credeva di aver sostituito.

    La precondizione di `versione` (#51) non bastava: il ricreato riparte da
    `versione = 1`, cioe' esattamente il valore che la scheda vecchia porta con
    se'. Serviva l'identita' della RIGA, non il suo contatore: `uid`, che dalla
    #73 esiste e dalla #75 il client rimanda indietro.
    """
    cookie, _ = _login_a(servizio)
    creato = json.loads(_crea(servizio, cookie, 'Due schede')[1])
    slug, uid_letto, versione = creato['slug'], creato['uid'], creato['versione']
    assert uid_letto, 'la vista del parser deve portare uid: e\' la precondizione'

    # L'utente, dall'ALTRA scheda, elimina e ricrea lo stesso parser.
    assert _chiama(servizio, 'DELETE', f'/api/me/parsers/{slug}', cookie=cookie)[0] == 200
    ricreato = json.loads(_crea(servizio, cookie, 'Due schede')[1])
    assert ricreato['slug'] == slug, 'lo slug torna libero e viene riusato'
    assert ricreato['uid'] != uid_letto, 'ma l\'uid no: e\' una riga diversa'
    assert ricreato['versione'] == versione == 1, \
        'il contatore riparte da 1: e\' il motivo per cui la #51 non bastava'

    # ...e adesso salva la PRIMA scheda, con l'uid e la versione che aveva letto.
    stato, corpo, _ = _chiama(
        servizio, 'PUT', f'/api/me/parsers/{slug}', cookie=cookie,
        corpo={'titolo': 'Dalla scheda vecchia',
               'config': dict(CONFIG_OK, match={'type': 'contains', 'value': 'VECCHIO'}),
               'active': True, 'versione': versione, 'uid': uid_letto})
    assert stato == 409, f'la scheda vecchia deve perdere in modo VISIBILE: {stato} {corpo}'
    assert 'ricreato' in json.loads(corpo)['detail'], corpo

    # Il parser ricreato e' intatto: nessuna sovrascrittura silenziosa.
    vivo = next(p for p in json.loads(
        _chiama(servizio, 'GET', '/api/me/parsers', cookie=cookie)[1])
        if p['slug'] == slug)
    assert vivo['titolo'] == 'Due schede', vivo
    assert vivo['config']['match']['value'] == 'SEGNALE', \
        'la config del ricreato non deve essere toccata'

    # E la DELETE dalla scheda vecchia non porta via il ricreato.
    stato, corpo, _ = _chiama(servizio, 'DELETE',
                              f'/api/me/parsers/{slug}?uid={uid_letto}', cookie=cookie)
    assert stato == 409, f'anche la DELETE stantia deve perdere: {stato} {corpo}'
    assert [p['slug'] for p in json.loads(
        _chiama(servizio, 'GET', '/api/me/parsers', cookie=cookie)[1])] == [slug]

    # Con l'uid FRESCO invece si elimina normalmente.
    assert _chiama(servizio, 'DELETE',
                   f'/api/me/parsers/{slug}?uid={ricreato["uid"]}',
                   cookie=cookie)[0] == 200
    assert json.loads(_chiama(servizio, 'GET', '/api/me/parsers', cookie=cookie)[1]) == []
