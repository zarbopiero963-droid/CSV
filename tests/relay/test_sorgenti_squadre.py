"""Sorgenti squadre (#34, pezzo 1): competizioni, squadre Betfair, sorgenti, alias.

Il modello congelato nella issue dopo le correzioni del proprietario (13-14/08):
la **competizione** (Sport → Serie A) possiede la lista canonica dei nomi
Betfair, salvata UNA volta; ogni **sorgente** (nominata, con rinomina) e' una
colonna di alias sopra quella stessa lista — un solo alias per squadra per
sorgente; le competizioni organizzano, la ricerca del pezzo 3 sara' su tutta la
sorgente. Le azioni di riga hanno semantica diversa e vanno inchiodate:

- **⌫ alias** (alias vuoto nel PUT) svuota SOLO l'alias di quella sorgente:
  la squadra Betfair resta, e resta negli altri sorgenti;
- **× squadra** (DELETE della squadra) la toglie dalla competizione e a cascata
  dai suoi alias in TUTTE le sorgenti;
- eliminare una sorgente rimuove i SUOI alias, mai le squadre;
- eliminare una competizione rimuove squadre e alias relativi;
- eliminare lo SPORT (#33) ora cascata anche qui: prima di questa PR avrebbe
  lasciato competizioni orfane, invisibili e non eliminabili.

L'isolamento e' quello di sempre: `user_id` dalla sessione, roba altrui = 404.
Stile e fixture di `test_mercati.py` (servizio vero in sottoprocesso, cookie
firmati a mano), NON ricopiati: importati (regola 3).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE))

import main  # noqa: E402 - dopo l'inserimento del percorso
from tests.ambiente import CHIAVI_PERICOLOSE, TOKEN_DI_PROVA  # noqa: E402
from tests.dati import relay_in_processo  # noqa: E402
from tests.servizio import relay_avviato  # noqa: E402
from tests.relay.test_mercati import (  # noqa: E402
    BOT_FINTO, ADMIN_FINTO, PROXY_MORTO, _chiama, _login)

AMBIENTE_DEL_SERVIZIO = {
    'CSV_ACCESS_TOKEN': TOKEN_DI_PROVA,
    'TELEGRAM_BOT_TOKEN': BOT_FINTO,
    'TELEGRAM_ADMIN_ID': ADMIN_FINTO,
    'PUBLIC_URL': 'https://non-esiste.invalid',
    'HTTPS_PROXY': PROXY_MORTO,
    'https_proxy': PROXY_MORTO,
    # Quote basse APPOSTA: i test delle quote le esauriscono davvero.
    'MAX_SORGENTI_PER_UTENTE': '2',
    'MAX_COMPETIZIONI_PER_UTENTE': '2',
    'MAX_SQUADRE_PER_COMPETIZIONE': '3',
}

CLIENTE_A = '777000111'
CLIENTE_B = '777000222'


@pytest.fixture(autouse=True)
def _ambiente_pulito(monkeypatch):
    for chiave in CHIAVI_PERICOLOSE:
        monkeypatch.delenv(chiave, raising=False)


@pytest.fixture(scope='module')
def servizio(tmp_path_factory):
    """Un servizio per modulo: i test si spartiscono i dati per nome."""
    with relay_avviato(tmp_path_factory.mktemp('sorgenti'),
                       **AMBIENTE_DEL_SERVIZIO) as base:
        yield base


@pytest.fixture(scope='module')
def cookie_a(servizio):
    return _login(servizio, CLIENTE_A)


@pytest.fixture(scope='module')
def cookie_b(servizio):
    return _login(servizio, CLIENTE_B)


def _json(corpo):
    return json.loads(corpo)


def _sport(servizio, cookie, nome):
    """Uno sport dell'utente (rotta #33), fondamenta delle competizioni."""
    stato, corpo, _ = _chiama(servizio, 'POST', '/api/me/sports',
                              corpo={'nome': nome}, cookie=cookie)
    assert stato == 200, corpo
    return _json(corpo)['slug']


def _competizione(servizio, cookie, sport, nome):
    stato, corpo, _ = _chiama(servizio, 'POST', '/api/me/competizioni',
                              corpo={'sport': sport, 'nome': nome}, cookie=cookie)
    assert stato == 200, corpo
    return _json(corpo)['id']


def _squadra(servizio, cookie, competizione, nome):
    stato, corpo, _ = _chiama(servizio, 'POST',
                              f'/api/me/competizioni/{competizione}/squadre',
                              corpo={'nome': nome}, cookie=cookie)
    assert stato == 200, corpo
    return _json(corpo)['id']


def _sorgente(servizio, cookie, nome):
    stato, corpo, _ = _chiama(servizio, 'POST', '/api/me/sorgenti-squadre',
                              corpo={'nome': nome}, cookie=cookie)
    assert stato == 200, corpo
    return _json(corpo)['id']


def _metti_alias(servizio, cookie, competizione, sorgente, alias, atteso=200):
    stato, corpo, _ = _chiama(
        servizio, 'PUT', f'/api/me/competizioni/{competizione}/alias/{sorgente}',
        corpo={'alias': alias}, cookie=cookie)
    assert stato == atteso, corpo
    return _json(corpo) if stato == 200 else corpo


def _leggi_alias(servizio, cookie, competizione, sorgente):
    stato, corpo, _ = _chiama(
        servizio, 'GET', f'/api/me/competizioni/{competizione}/alias/{sorgente}',
        cookie=cookie)
    assert stato == 200, corpo
    return {r['squadra']: r['alias'] for r in _json(corpo)['alias']}


# ------------------------------------------------------------------ il vuoto

def test_al_primo_login_non_esiste_niente(servizio, cookie_a):
    stato, corpo, _ = _chiama(servizio, 'GET', '/api/me/sorgenti-squadre',
                              cookie=cookie_a)
    assert stato == 200, corpo
    assert _json(corpo)['sorgenti'] == []
    stato, corpo, _ = _chiama(servizio, 'GET', '/api/me/competizioni',
                              cookie=cookie_a)
    assert stato == 200, corpo
    assert _json(corpo)['competizioni'] == []


# ------------------------------------------------------- sorgenti: CRUD vero

def test_sorgente_creata_rinominata_e_doppioni_409(servizio, cookie_a):
    sid = _sorgente(servizio, cookie_a, 'test 1')
    stato, corpo, _ = _chiama(servizio, 'POST', '/api/me/sorgenti-squadre',
                              corpo={'nome': 'test 1'}, cookie=cookie_a)
    assert stato == 409, 'il doppione di nome deve essere rifiutato'
    stato, corpo, _ = _chiama(servizio, 'PATCH', f'/api/me/sorgenti-squadre/{sid}',
                              corpo={'nome': 'fonte A'}, cookie=cookie_a)
    assert stato == 200, corpo
    stato, corpo, _ = _chiama(servizio, 'GET', '/api/me/sorgenti-squadre',
                              cookie=cookie_a)
    nomi = [s['nome'] for s in _json(corpo)['sorgenti']]
    assert 'fonte A' in nomi and 'test 1' not in nomi, nomi

    altra = _sorgente(servizio, cookie_a, 'seconda')
    stato, corpo, _ = _chiama(servizio, 'PATCH', f'/api/me/sorgenti-squadre/{altra}',
                              corpo={'nome': 'fonte A'}, cookie=cookie_a)
    assert stato == 409, 'la rinomina su un nome gia\' preso deve essere rifiutata'
    # pulizia: il modulo condivide il servizio e le quote sono basse apposta
    for lasciata in (altra, sid):
        stato, _, _ = _chiama(servizio, 'DELETE',
                              f'/api/me/sorgenti-squadre/{lasciata}',
                              cookie=cookie_a)
        assert stato == 200


def test_quota_sorgenti_e_un_409_misurato(servizio, cookie_b):
    primo = _sorgente(servizio, cookie_b, 'b uno')
    _sorgente(servizio, cookie_b, 'b due')
    stato, corpo, _ = _chiama(servizio, 'POST', '/api/me/sorgenti-squadre',
                              corpo={'nome': 'b tre'}, cookie=cookie_b)
    assert stato == 409 and b'quota' in corpo, corpo
    for percorso in (f'/api/me/sorgenti-squadre/{primo}',):
        _chiama(servizio, 'DELETE', percorso, cookie=cookie_b)


# --------------------------------------------------- competizioni e squadre

def test_competizione_vive_sotto_lo_sport_dell_utente(servizio, cookie_a):
    calcio = _sport(servizio, cookie_a, 'Calcio')
    cid = _competizione(servizio, cookie_a, calcio, 'Serie A')
    stato, corpo, _ = _chiama(servizio, 'POST', '/api/me/competizioni',
                              corpo={'sport': calcio, 'nome': 'Serie A'},
                              cookie=cookie_a)
    assert stato == 409, 'stesso sport + stesso nome = doppione'
    stato, corpo, _ = _chiama(servizio, 'POST', '/api/me/competizioni',
                              corpo={'sport': 'non-esiste', 'nome': 'X'},
                              cookie=cookie_a)
    assert stato == 404, 'sport inesistente: 404, non 422'
    stato, corpo, _ = _chiama(servizio, 'GET', '/api/me/competizioni',
                              cookie=cookie_a)
    trovate = _json(corpo)['competizioni']
    assert [c for c in trovate if c['id'] == cid and c['sport'] == calcio
            and c['nome'] == 'Serie A'], trovate
    # pulizia: la DELETE dello sport porta via la competizione (testato sotto)
    stato, _, _ = _chiama(servizio, 'DELETE', f'/api/me/sports/{calcio}',
                          cookie=cookie_a)
    assert stato == 200


def test_squadre_betfair_salvate_una_volta_con_i_loro_422(servizio, cookie_a):
    calcio = _sport(servizio, cookie_a, 'Squadre')
    cid = _competizione(servizio, cookie_a, calcio, 'Prova 422')
    _squadra(servizio, cookie_a, cid, 'Juventus')
    stato, corpo, _ = _chiama(servizio, 'POST', f'/api/me/competizioni/{cid}/squadre',
                              corpo={'nome': 'Juventus'}, cookie=cookie_a)
    assert stato == 409, 'squadra doppia nella stessa competizione'
    stato, corpo, _ = _chiama(servizio, 'POST', f'/api/me/competizioni/{cid}/squadre',
                              corpo={'nome': ''}, cookie=cookie_a)
    assert stato == 422, 'nome vuoto'
    stato, corpo, _ = _chiama(servizio, 'POST', f'/api/me/competizioni/{cid}/squadre',
                              corpo={'nome': 'Juve \U0001F525'}, cookie=cookie_a)
    assert stato == 422, 'il nome Betfair finisce nel CSV: emoji vietata (#42)'
    _squadra(servizio, cookie_a, cid, 'Milan')
    _squadra(servizio, cookie_a, cid, 'Inter')
    stato, corpo, _ = _chiama(servizio, 'POST', f'/api/me/competizioni/{cid}/squadre',
                              corpo={'nome': 'Roma'}, cookie=cookie_a)
    assert stato == 409 and b'quota' in corpo, 'quota squadre (3 nel modulo)'
    stato, _, _ = _chiama(servizio, 'DELETE', f'/api/me/sports/{calcio}',
                          cookie=cookie_a)
    assert stato == 200


def test_la_lista_betfair_e_condivisa_e_gli_alias_indipendenti(servizio, cookie_a):
    calcio = _sport(servizio, cookie_a, 'Condivisa')
    cid = _competizione(servizio, cookie_a, calcio, 'Serie X')
    ju = _squadra(servizio, cookie_a, cid, 'Juventus')
    mi = _squadra(servizio, cookie_a, cid, 'AC Milan')

    s1 = _sorgente(servizio, cookie_a, 'canale uno')
    s2 = _sorgente(servizio, cookie_a, 'canale due')
    try:
        # La seconda sorgente vede le STESSE squadre senza ridigitarle: la
        # lista arriva dalla competizione, con alias tutti vuoti.
        vuoti = _leggi_alias(servizio, cookie_a, cid, s2)
        assert vuoti == {'Juventus': '', 'AC Milan': ''}, vuoti

        _metti_alias(servizio, cookie_a, cid, s1, {str(ju): 'Juve', str(mi): 'Milan'})
        _metti_alias(servizio, cookie_a, cid, s2, {str(ju): 'JUV'})
        assert _leggi_alias(servizio, cookie_a, cid, s1) == \
            {'Juventus': 'Juve', 'AC Milan': 'Milan'}
        assert _leggi_alias(servizio, cookie_a, cid, s2) == \
            {'Juventus': 'JUV', 'AC Milan': ''}

        # UN alias per squadra per sorgente: il PUT successivo SOSTITUISCE.
        _metti_alias(servizio, cookie_a, cid, s1, {str(ju): 'Juventus FC'})
        assert _leggi_alias(servizio, cookie_a, cid, s1)['Juventus'] == 'Juventus FC'

        # ⌫ alias: vuoto = svuotato SOLO qui; l'altra sorgente non si muove.
        _metti_alias(servizio, cookie_a, cid, s1, {str(ju): ''})
        assert _leggi_alias(servizio, cookie_a, cid, s1)['Juventus'] == ''
        assert _leggi_alias(servizio, cookie_a, cid, s2)['Juventus'] == 'JUV'

        # squadra_id estranea alla competizione: 422 col motivo, niente scrittura.
        _metti_alias(servizio, cookie_a, cid, s1, {'999999': 'X'}, atteso=422)

        # Il badge del pezzo 2: quante squadre hanno l'alias in ogni sorgente.
        stato, corpo, _ = _chiama(servizio, 'GET', f'/api/me/competizioni/{cid}',
                                  cookie=cookie_a)
        assert stato == 200, corpo
        dettaglio = _json(corpo)
        conte = {s['id']: s['compilati'] for s in dettaglio['sorgenti']}
        assert conte[s1] == 1 and conte[s2] == 1, dettaglio
        assert [q['nome'] for q in dettaglio['squadre']] == ['AC Milan', 'Juventus']

        # × squadra: sparisce dalla competizione E dagli alias di TUTTE le sorgenti.
        stato, corpo, _ = _chiama(servizio, 'DELETE',
                                  f'/api/me/competizioni/{cid}/squadre/{ju}',
                                  cookie=cookie_a)
        assert stato == 200, corpo
        assert _leggi_alias(servizio, cookie_a, cid, s1) == {'AC Milan': 'Milan'}
        assert _leggi_alias(servizio, cookie_a, cid, s2) == {'AC Milan': ''}

        # Eliminare la sorgente rimuove i SUOI alias, non le squadre.
        stato, corpo, _ = _chiama(servizio, 'DELETE',
                                  f'/api/me/sorgenti-squadre/{s1}',
                                  cookie=cookie_a)
        assert stato == 200, corpo
        stato, corpo, _ = _chiama(servizio, 'GET', f'/api/me/competizioni/{cid}',
                                  cookie=cookie_a)
        assert [q['nome'] for q in _json(corpo)['squadre']] == ['AC Milan']
        s1 = None
    finally:
        for sid in (s1, s2):
            if sid is not None:
                _chiama(servizio, 'DELETE', f'/api/me/sorgenti-squadre/{sid}',
                        cookie=cookie_a)
        _chiama(servizio, 'DELETE', f'/api/me/sports/{calcio}', cookie=cookie_a)


def test_eliminare_la_competizione_porta_via_squadre_e_alias(servizio, cookie_a):
    calcio = _sport(servizio, cookie_a, 'Cascata')
    cid = _competizione(servizio, cookie_a, calcio, 'Da eliminare')
    ju = _squadra(servizio, cookie_a, cid, 'Juventus')
    sid = _sorgente(servizio, cookie_a, 'per cascata')
    try:
        _metti_alias(servizio, cookie_a, cid, sid, {str(ju): 'Juve'})
        stato, corpo, _ = _chiama(servizio, 'DELETE', f'/api/me/competizioni/{cid}',
                                  cookie=cookie_a)
        assert stato == 200, corpo
        stato, _, _ = _chiama(servizio, 'GET', f'/api/me/competizioni/{cid}',
                              cookie=cookie_a)
        assert stato == 404
        stato, corpo, _ = _chiama(servizio, 'GET', '/api/me/sorgenti-squadre',
                                  cookie=cookie_a)
        resti = [s for s in _json(corpo)['sorgenti'] if s['id'] == sid]
        assert resti, 'la sorgente sopravvive alla competizione'
    finally:
        _chiama(servizio, 'DELETE', f'/api/me/sorgenti-squadre/{sid}',
                cookie=cookie_a)
        _chiama(servizio, 'DELETE', f'/api/me/sports/{calcio}', cookie=cookie_a)


def test_eliminare_lo_sport_cascata_anche_sulle_competizioni(servizio, cookie_a):
    """Il consumatore esistente (regola 2-bis): la DELETE dello sport (#33) ora
    porta via anche competizioni, squadre e alias — prima di questa PR le
    avrebbe lasciate orfane, invisibili alle API e non piu' eliminabili."""
    sport = _sport(servizio, cookie_a, 'Da spazzare')
    cid = _competizione(servizio, cookie_a, sport, 'Orfanabile')
    ju = _squadra(servizio, cookie_a, cid, 'Juventus')
    sid = _sorgente(servizio, cookie_a, 'spazzata')
    try:
        _metti_alias(servizio, cookie_a, cid, sid, {str(ju): 'Juve'})
        stato, corpo, _ = _chiama(servizio, 'DELETE', f'/api/me/sports/{sport}',
                                  cookie=cookie_a)
        assert stato == 200, corpo
        stato, _, _ = _chiama(servizio, 'GET', f'/api/me/competizioni/{cid}',
                              cookie=cookie_a)
        assert stato == 404, 'la competizione deve seguire il suo sport'
        stato, corpo, _ = _chiama(servizio, 'GET', '/api/me/competizioni',
                                  cookie=cookie_a)
        assert cid not in [c['id'] for c in _json(corpo)['competizioni']]
    finally:
        _chiama(servizio, 'DELETE', f'/api/me/sorgenti-squadre/{sid}',
                cookie=cookie_a)


def test_quota_competizioni_e_un_409(servizio, cookie_b):
    sport = _sport(servizio, cookie_b, 'B-Sport')
    _competizione(servizio, cookie_b, sport, 'Uno')
    _competizione(servizio, cookie_b, sport, 'Due')
    stato, corpo, _ = _chiama(servizio, 'POST', '/api/me/competizioni',
                              corpo={'sport': sport, 'nome': 'Tre'},
                              cookie=cookie_b)
    assert stato == 409 and b'quota' in corpo, corpo
    stato, _, _ = _chiama(servizio, 'DELETE', f'/api/me/sports/{sport}',
                          cookie=cookie_b)
    assert stato == 200


# ------------------------------------------------------------- l'isolamento

def test_la_roba_di_un_utente_non_esiste_per_un_altro(servizio, cookie_a, cookie_b):
    sport = _sport(servizio, cookie_a, 'Privato')
    cid = _competizione(servizio, cookie_a, sport, 'Mio')
    ju = _squadra(servizio, cookie_a, cid, 'Juventus')
    sid = _sorgente(servizio, cookie_a, 'privata')
    try:
        # B non crea una competizione sullo SPORT di A: per B quello slug non esiste.
        stato, _, _ = _chiama(servizio, 'POST', '/api/me/competizioni',
                              corpo={'sport': 'privato', 'nome': 'Abuso'},
                              cookie=cookie_b)
        assert stato == 404
        for metodo, percorso, corpo in (
                ('GET', f'/api/me/competizioni/{cid}', None),
                ('DELETE', f'/api/me/competizioni/{cid}', None),
                ('POST', f'/api/me/competizioni/{cid}/squadre', {'nome': 'X'}),
                ('DELETE', f'/api/me/competizioni/{cid}/squadre/{ju}', None),
                ('GET', f'/api/me/competizioni/{cid}/alias/{sid}', None),
                ('PUT', f'/api/me/competizioni/{cid}/alias/{sid}',
                 {'alias': {str(ju): 'X'}}),
                ('PATCH', f'/api/me/sorgenti-squadre/{sid}', {'nome': 'rubata'}),
                ('DELETE', f'/api/me/sorgenti-squadre/{sid}', None),
        ):
            stato, risposta, _ = _chiama(servizio, metodo, percorso,
                                         corpo=corpo, cookie=cookie_b)
            assert stato == 404, f'{metodo} {percorso}: atteso 404, avuto {stato} {risposta[:120]!r}'
        # e per A e' ancora tutto al suo posto
        assert _leggi_alias(servizio, cookie_a, cid, sid) == {'Juventus': ''}
    finally:
        _chiama(servizio, 'DELETE', f'/api/me/sorgenti-squadre/{sid}', cookie=cookie_a)
        # Lo slug RESTITUITO dal server, non quello immaginato: `crea_sport_mio`
        # disambigua col retry, e «privato» potrebbe essere nato «privato-2»
        # (CodeRabbit, PR #64). Una pulizia sullo slug sbagliato lascerebbe lo
        # sport di prova vivo e farebbe fallire un test LONTANO, sulla quota.
        _chiama(servizio, 'DELETE', f'/api/me/sports/{sport}', cookie=cookie_a)


def test_la_sorgente_di_a_non_si_aggancia_alla_competizione_di_b(servizio, cookie_a,
                                                                 cookie_b):
    """L'incrocio: competizione MIA + sorgente ALTRUI nel percorso alias → 404.
    Senza questo controllo un PUT scriverebbe alias di B dentro la sorgente di A."""
    sport = _sport(servizio, cookie_b, 'B-incrocio')
    cid = _competizione(servizio, cookie_b, sport, 'Di B')
    sid_a = _sorgente(servizio, cookie_a, 'di A')
    try:
        stato, _, _ = _chiama(servizio, 'GET',
                              f'/api/me/competizioni/{cid}/alias/{sid_a}',
                              cookie=cookie_b)
        assert stato == 404, 'sorgente altrui sul percorso alias'
    finally:
        _chiama(servizio, 'DELETE', f'/api/me/sorgenti-squadre/{sid_a}',
                cookie=cookie_a)
        _chiama(servizio, 'DELETE', f'/api/me/sports/{sport}', cookie=cookie_b)


# ------------------------------------------------- gli errori prima dei dati

def test_senza_sessione_401_anche_con_corpo_marcio(servizio):
    for metodo, percorso in (('POST', '/api/me/sorgenti-squadre'),
                             ('POST', '/api/me/competizioni'),
                             ('PUT', '/api/me/competizioni/1/alias/1')):
        stato, _, _ = _chiama(servizio, metodo, percorso,
                              grezzo=b'{non-json')
        assert stato == 401, f'{metodo} {percorso}: la porta viene prima del corpo'


def test_id_non_numerici_sono_404(servizio, cookie_a):
    for metodo, percorso in (('GET', '/api/me/competizioni/banana'),
                             ('DELETE', '/api/me/competizioni/banana'),
                             ('DELETE', '/api/me/sorgenti-squadre/banana'),
                             ('GET', '/api/me/competizioni/banana/alias/pera')):
        stato, _, _ = _chiama(servizio, metodo, percorso, cookie=cookie_a)
        assert stato == 404, f'{metodo} {percorso}'


# ----------------------------------------------- le corse (classe della #55)

def test_l_inserimento_non_lascia_orfani_se_il_padre_sparisce(tmp_path, monkeypatch):
    """`_inserisci_competizione` / `_inserisci_squadra` col padre gia' eliminato:
    zero righe scritte, None al chiamante. Stessa classe del TOCTOU dei mercati
    (PR #55), applicata QUI da subito invece che dopo il bloccante."""
    import sqlite3
    percorso = relay_in_processo(monkeypatch, tmp_path / 'orfani.db')
    c = sqlite3.connect(percorso)
    c.execute("INSERT INTO users(slug, first_name, status) VALUES ('solo', 'Solo', 'attivo')")
    utente = c.execute("SELECT id FROM users WHERE slug='solo'").fetchone()[0]
    c.execute('INSERT INTO sports(user_id, slug, nome) VALUES (?, ?, ?)',
              (utente, 'calcio', 'Calcio'))
    sport = c.execute("SELECT id FROM sports WHERE slug='calcio'").fetchone()[0]
    c.commit()

    sparito_sport = sport + 1000
    assert main._inserisci_competizione(c, utente, sparito_sport, 'Serie A') is None
    assert c.execute('SELECT COUNT(*) FROM competizioni').fetchone()[0] == 0

    competizione_sparita = 4242
    assert main._inserisci_squadra(c, utente, competizione_sparita, 'Juventus') is None
    assert c.execute('SELECT COUNT(*) FROM squadre_betfair').fetchone()[0] == 0

    # E il TERZO sito della stessa classe, quello mancato al primo giro:
    # l'upsert dell'alias ([REAL_FINDING] di Claude Fable 5 sulla PR #64). Fra
    # la lettura delle squadre valide e la scrittura, una DELETE concorrente
    # della squadra puo' committare: senza la guardia EXISTS l'alias orfano
    # veniva scritto — invisibile (le letture joinano squadre_betfair) e
    # rimovibile solo eliminando la sorgente.
    c.execute('INSERT INTO sorgenti_squadre(user_id, nome) VALUES (?, ?)',
              (utente, 'sorgente di prova'))
    sorgente = c.execute('SELECT id FROM sorgenti_squadre').fetchone()[0]
    cid = main._inserisci_competizione(c, utente, sport, 'Serie A')
    squadra = main._inserisci_squadra(c, utente, cid, 'Juventus')

    assert main._scrivi_alias(c, utente, sorgente, squadra, cid, 'Juve') is True
    assert main._scrivi_alias(c, utente, sorgente, squadra, cid, 'JUV') is True, \
        'la sovrascrittura conta come cambiamento anche a valore identico'
    assert c.execute('SELECT alias FROM alias_squadre WHERE sorgente_id=?'
                     ' AND squadra_id=?', (sorgente, squadra)).fetchone()[0] == 'JUV'
    assert main._scrivi_alias(c, utente, sorgente, squadra, cid, 'JUV') is True, \
        'valore identico: changes() deve contare comunque, o la rotta darebbe 404'

    squadra_sparita = squadra + 1000
    assert main._scrivi_alias(c, utente, sorgente, squadra_sparita, cid,
                              'Orfano') is None
    assert c.execute('SELECT COUNT(*) FROM alias_squadre WHERE squadra_id=?',
                     (squadra_sparita,)).fetchone()[0] == 0, 'alias orfano scritto'

    # La squadra di un'ALTRA competizione non si aggancia: l'id esiste, ma non
    # dentro QUESTA competizione.
    altra = main._inserisci_competizione(c, utente, sport, 'Serie B')
    assert main._scrivi_alias(c, utente, sorgente, squadra, altra, 'X') is None, \
        'squadra fuori dalla competizione del percorso: non va scritta'

    # E la SORGENTE sparita (GPT-5.5, PR #64): senza la guardia sul secondo
    # padre una DELETE concorrente della sorgente lascerebbe una riga con
    # sorgente_id pendente — mai letta e non piu' eliminabile, perche' la
    # cascata della sorgente e' gia' passata.
    sorgente_sparita = sorgente + 1000
    assert main._scrivi_alias(c, utente, sorgente_sparita, squadra, cid,
                              'Orfano') is None
    assert c.execute('SELECT COUNT(*) FROM alias_squadre WHERE sorgente_id=?',
                     (sorgente_sparita,)).fetchone()[0] == 0, \
        'alias con sorgente pendente scritto'

    # E la COMPETIZIONE travasata a un altro utente (terzo gate di Fable): la
    # squadra esiste ancora dentro quella competizione, la sorgente e' ancora
    # mia, ma la competizione non lo e' piu' — l'alias non si scrive. Simula
    # il travaso committato fra la lettura della rotta e il write-lock.
    c.execute("INSERT INTO users(slug, first_name, status) VALUES ('terzo', 'Terzo', 'attivo')")
    terzo = c.execute("SELECT id FROM users WHERE slug='terzo'").fetchone()[0]
    c.execute('UPDATE competizioni SET user_id=? WHERE id=?', (terzo, cid))
    assert main._scrivi_alias(c, utente, sorgente, squadra, cid, 'Tardi') is None, \
        'la competizione travasata non deve piu' + "' accettare alias dal vecchio conto"
    c.execute('UPDATE competizioni SET user_id=? WHERE id=?', (utente, cid))

    # E la sorgente di un ALTRO utente (hardening dal secondo giro di Fable):
    # l'id e' vivo ma non e' del chiamante — non si scrive. Con AUTOINCREMENT
    # gli id non si riusano mai (misurato), quindi il vincolo e' difesa in
    # profondita', non la chiusura di una falla viva: l'invariante smette di
    # dipendere da una proprieta' sottile dello schema.
    c.execute("INSERT INTO users(slug, first_name, status) VALUES ('altro', 'Altro', 'attivo')")
    altro = c.execute("SELECT id FROM users WHERE slug='altro'").fetchone()[0]
    c.execute('INSERT INTO sorgenti_squadre(user_id, nome) VALUES (?, ?)',
              (altro, 'sorgente altrui'))
    sorgente_altrui = c.execute('SELECT id FROM sorgenti_squadre WHERE user_id=?',
                                (altro,)).fetchone()[0]
    assert main._scrivi_alias(c, utente, sorgente_altrui, squadra, cid,
                              'Abuso') is None, \
        'la sorgente di un altro utente non deve accettare alias'
    assert c.execute('SELECT COUNT(*) FROM alias_squadre WHERE sorgente_id=?',
                     (sorgente_altrui,)).fetchone()[0] == 0
    c.close()


def test_le_scritture_sono_vincolate_al_proprietario_anche_nel_write_lock(
        tmp_path, monkeypatch):
    """[REAL_FINDING] di GPT-5.6 Sol al gate della PR #64: la proprieta' letta
    PRIMA del write-lock puo' invecchiare — una riconciliazione concorrente
    travasa il padre fra il check e la scrittura, e uno statement che filtra
    solo per id atterrerebbe su dati ormai di un altro account. Qui ogni
    helper distruttivo ripete il vincolo `user_id` DENTRO lo statement: col
    proprietario sbagliato, zero righe toccate e None al chiamante.

    (Il travaso avviene solo fra account della STESSA persona, quindi non e'
    una falla cross-utente viva: e' la stessa difesa in profondita' del
    vincolo di proprieta' in `_scrivi_alias`, applicata al lato distruttivo.)
    """
    import sqlite3
    percorso = relay_in_processo(monkeypatch, tmp_path / 'proprietario.db')
    c = sqlite3.connect(percorso)
    utenti = {}
    for slug in ('mio', 'altrui'):
        c.execute("INSERT INTO users(slug, first_name, status) VALUES (?, ?, 'attivo')",
                  (slug, slug.capitalize()))
        utenti[slug] = c.execute('SELECT id FROM users WHERE slug=?',
                                 (slug,)).fetchone()[0]
    c.execute('INSERT INTO sports(user_id, slug, nome) VALUES (?, ?, ?)',
              (utenti['mio'], 'calcio', 'Calcio'))
    sport = c.execute("SELECT id FROM sports WHERE slug='calcio'").fetchone()[0]
    cid = main._inserisci_competizione(c, utenti['mio'], sport, 'Serie A')
    squadra = main._inserisci_squadra(c, utenti['mio'], cid, 'Juventus')
    c.execute('INSERT INTO sorgenti_squadre(user_id, nome) VALUES (?, ?)',
              (utenti['mio'], 'fonte'))
    sorgente = c.execute('SELECT id FROM sorgenti_squadre').fetchone()[0]
    assert main._scrivi_alias(c, utenti['mio'], sorgente, squadra, cid, 'Juve') is True

    # Il proprietario SBAGLIATO non tocca niente, nemmeno conoscendo gli id.
    assert main._elimina_squadra(c, utenti['altrui'], cid, squadra) is None
    assert main._elimina_competizione(c, utenti['altrui'], cid) is None
    assert main._elimina_sorgente(c, utenti['altrui'], sorgente) is None
    assert main._rinomina_sorgente(c, utenti['altrui'], sorgente, 'rubata') is None
    assert c.execute('SELECT COUNT(*) FROM squadre_betfair').fetchone()[0] == 1
    assert c.execute('SELECT COUNT(*) FROM competizioni').fetchone()[0] == 1
    assert c.execute('SELECT COUNT(*) FROM alias_squadre').fetchone()[0] == 1
    assert c.execute('SELECT nome FROM sorgenti_squadre').fetchone()[0] == 'fonte'

    # E il creare sotto un padre TRAVASATO nel frattempo: zero righe.
    assert main._inserisci_competizione(c, utenti['altrui'], sport, 'Abuso') is None
    assert main._inserisci_squadra(c, utenti['altrui'], cid, 'Abuso') is None

    # «⌫ alias» (secondo gate di Sol): la cancellazione del solo alias e'
    # anch'essa vincolata nel write-lock — il proprietario sbagliato non
    # cancella l'alias del nuovo proprietario, e per lui e' un no-op.
    assert main._cancella_alias(c, utenti['altrui'], sorgente, squadra) == 0
    assert c.execute('SELECT COUNT(*) FROM alias_squadre').fetchone()[0] == 1
    assert main._cancella_alias(c, utenti['mio'], sorgente, squadra) == 1
    assert c.execute('SELECT COUNT(*) FROM alias_squadre').fetchone()[0] == 0
    assert main._scrivi_alias(c, utenti['mio'], sorgente, squadra, cid, 'Juve') is True

    # E lo SPORT intero (secondo gate di Sol): la cascata comprende anche
    # mercati e selezioni (#33), e nessun pezzo si muove col proprietario
    # sbagliato — prima della correzione quei due DELETE filtravano solo per
    # sport_id, e una richiesta invecchiata avrebbe distrutto i mercati
    # travasati al nuovo proprietario.
    c.execute('INSERT INTO betfair_markets(sport_id, market_type, market_name)'
              ' VALUES (?,?,?)', (sport, 'OVER_UNDER_15', 'Over/Under 1,5 gol'))
    mercato = c.execute('SELECT id FROM betfair_markets').fetchone()[0]
    c.execute('INSERT INTO betfair_selections(market_id, selection_name)'
              ' VALUES (?,?)', (mercato, 'Over 1,5 goal'))
    assert main._elimina_sport(c, utenti['altrui'], sport) is None
    assert c.execute('SELECT COUNT(*) FROM betfair_markets').fetchone()[0] == 1
    assert c.execute('SELECT COUNT(*) FROM sports').fetchone()[0] == 1
    assert c.execute('SELECT COUNT(*) FROM competizioni').fetchone()[0] == 1

    # Il proprietario vero fa tutto, nell'ordine inverso delle guardie.
    assert main._rinomina_sorgente(c, utenti['mio'], sorgente, 'fonte B') is True
    assert main._elimina_squadra(c, utenti['mio'], cid, squadra) is True
    assert main._elimina_competizione(c, utenti['mio'], cid) is True
    assert main._elimina_sorgente(c, utenti['mio'], sorgente) is True
    assert main._elimina_sport(c, utenti['mio'], sport) is True
    for tabella in ('squadre_betfair', 'competizioni', 'alias_squadre',
                    'sorgenti_squadre', 'betfair_markets', 'betfair_selections',
                    'sports'):
        assert c.execute(f'SELECT COUNT(*) FROM {tabella}').fetchone()[0] == 0, tabella
    c.close()


# ------------------------------------------- la riconciliazione degli account

def test_il_travaso_porta_sorgenti_e_competizioni_senza_collisioni(tmp_path,
                                                                   monkeypatch):
    """`_trasferisci_sorgenti_squadre`: il perdente passa tutto al superstite.
    `UNIQUE (user_id, nome)` rende legale lo stesso nome di sorgente su due
    utenti: il travaso cieco collide e solleva — qui si rinomina chi arriva,
    come per parser e sport. Le competizioni riferiscono `sport_id` che non
    cambia, quindi seguono gli sport; il loro `user_id` va comunque riscritto o
    resterebbero appese all'account svuotato."""
    import sqlite3
    percorso = relay_in_processo(monkeypatch, tmp_path / 'travaso.db')
    c = sqlite3.connect(percorso)
    utenti = {}
    for slug in ('vince', 'perde'):
        c.execute("INSERT INTO users(slug, first_name, status) VALUES (?, ?, 'attivo')",
                  (slug, slug.capitalize()))
        utenti[slug] = c.execute('SELECT id FROM users WHERE slug=?', (slug,)).fetchone()[0]
    for proprietario, slug_sport in ((utenti['vince'], 'calcio'),
                                     (utenti['perde'], 'calcio-2')):
        c.execute('INSERT INTO sports(user_id, slug, nome) VALUES (?, ?, ?)',
                  (proprietario, slug_sport, 'Calcio'))
    sport_perdente = c.execute("SELECT id FROM sports WHERE slug='calcio-2'").fetchone()[0]
    c.execute('INSERT INTO competizioni(user_id, sport_id, nome) VALUES (?,?,?)',
              (utenti['perde'], sport_perdente, 'Serie A'))
    for proprietario in (utenti['vince'], utenti['perde']):
        c.execute('INSERT INTO sorgenti_squadre(user_id, nome) VALUES (?, ?)',
                  (proprietario, 'test 1'))
    c.commit()

    main._trasferisci_sorgenti_squadre(c, utenti['perde'], utenti['vince'])
    c.commit()

    assert c.execute('SELECT COUNT(*) FROM sorgenti_squadre WHERE user_id=?',
                     (utenti['perde'],)).fetchone()[0] == 0
    assert c.execute('SELECT COUNT(*) FROM competizioni WHERE user_id=?',
                     (utenti['perde'],)).fetchone()[0] == 0
    nomi = sorted(r[0] for r in c.execute(
        'SELECT nome FROM sorgenti_squadre WHERE user_id=?',
        (utenti['vince'],)).fetchall())
    assert len(nomi) == 2 and nomi[0] == 'test 1' and nomi[1] != 'test 1', (
        f'attesa la rinomina di chi arriva, trovato {nomi}')
    c.close()


# --------------------------------------- alias ambiguo vietato (#34 pezzo 3)

def test_lo_stesso_alias_su_due_squadre_della_stessa_sorgente_e_un_422(servizio, cookie_a):
    """Deciso dal proprietario (17/08/2026), regola del pezzo 3: a parse-time la
    ricerca alias->Betfair corre su TUTTA la sorgente, quindi lo stesso testo su
    due squadre sarebbe ambiguo. L'ambiguita' non deve poter nascere: il PUT la
    rifiuta al salvataggio, anche fra competizioni diverse, e il corpo che la
    contiene non scrive niente (il no-commit del 422). Ribadire lo stesso alias
    sulla stessa squadra resta l'upsert di sempre, e in un'ALTRA sorgente lo
    stesso testo e' libero: la colonna di alias e' della sorgente.
    """
    sport = _sport(servizio, cookie_a, 'Dup')
    cid = _competizione(servizio, cookie_a, sport, 'Serie Dup')
    cid2 = _competizione(servizio, cookie_a, sport, 'Coppa Dup')
    ju = _squadra(servizio, cookie_a, cid, 'Juventus')
    mi = _squadra(servizio, cookie_a, cid, 'Milan')
    inter = _squadra(servizio, cookie_a, cid2, 'Inter')
    sid = _sorgente(servizio, cookie_a, 'dup 1')
    sid2 = _sorgente(servizio, cookie_a, 'dup 2')
    try:
        _metti_alias(servizio, cookie_a, cid, sid, {str(ju): 'Juve'})
        # Upsert sulla STESSA squadra: lecito, ieri come oggi.
        _metti_alias(servizio, cookie_a, cid, sid, {str(ju): 'Juve'})
        # Stessa sorgente, altra squadra: 422 col motivo, mappa intatta.
        corpo = _metti_alias(servizio, cookie_a, cid, sid, {str(mi): 'Juve'},
                             atteso=422)
        motivo = corpo.decode('utf-8')
        assert 'Juve' in motivo and 'altra squadra' in motivo, motivo
        assert _leggi_alias(servizio, cookie_a, cid, sid)['Milan'] == ''
        # Stessa sorgente, altra COMPETIZIONE: la ricerca e' su tutta la
        # sorgente, quindi vietato anche qui.
        _metti_alias(servizio, cookie_a, cid2, sid, {str(inter): 'Juve'},
                     atteso=422)
        # Un'ALTRA sorgente e' un'altra colonna: lo stesso testo e' libero.
        _metti_alias(servizio, cookie_a, cid, sid2, {str(mi): 'Juve'})
        # Duplicato DENTRO lo stesso corpo: 422 e nessuna coppia scritta,
        # nemmeno la prima (senza commit non resta niente).
        _metti_alias(servizio, cookie_a, cid, sid,
                     {str(ju): 'Zebra', str(mi): 'Zebra'}, atteso=422)
        assert _leggi_alias(servizio, cookie_a, cid, sid)['Juventus'] == 'Juve'
    finally:
        for s in (sid, sid2):
            _chiama(servizio, 'DELETE', f'/api/me/sorgenti-squadre/{s}',
                    cookie=cookie_a)
        _chiama(servizio, 'DELETE', f'/api/me/sports/{sport}', cookie=cookie_a)
