"""Mercati Betfair per-utente (#33): sport → mercato → selezioni, tutto ISOLATO.

Il modello della issue, dopo la correzione del proprietario del 13/08/2026: NESSUN
catalogo incorporato — ogni utente crea i propri sport, mercati (`MarketType` +
`MarketName`) e selezioni (`SelectionName`), e il wizard del parser li consuma come
regole costanti. La proprieta' da vincolare non e' il CRUD: e' che

- un utente non vede, non modifica e non elimina i dati di un altro (**404**, non 403);
- il parser accetta in «Da mercati Betfair» SOLO una selezione che esiste fra quelle
  create dall'utente per quel mercato: una selezione arbitraria via HTTP → **422**;
- una selezione con i segnaposto `{HOME_TEAM}`/`{AWAY_TEAM}` non e' usabile nel parser
  finche' la sorgente squadre (#34) non esiste: nel CSV finirebbe il token letterale,
  e XTrader scarterebbe il segnale in silenzio. Fail-closed, con il motivo.

La firma dei cookie e' ricalcolata a mano come in `test_login.py`: due lati che
cambiano insieme non proverebbero niente.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
import threading
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

SEGRETO_ATTESO = hashlib.sha256(('betrelay-sessione-v1:' + BOT_FINTO).encode()).hexdigest()

AMBIENTE_DEL_SERVIZIO = {
    'CSV_ACCESS_TOKEN': TOKEN_DI_PROVA,
    'TELEGRAM_BOT_TOKEN': BOT_FINTO,
    'TELEGRAM_ADMIN_ID': ADMIN_FINTO,
    'PUBLIC_URL': 'https://non-esiste.invalid',
    'HTTPS_PROXY': PROXY_MORTO,
    'https_proxy': PROXY_MORTO,
    # Quota bassa APPOSTA: il test della quota deve esaurirla davvero, non
    # crearne venti. La variabile esiste per Railway; qui dimostra che funziona.
    'MAX_SPORT_PER_UTENTE': '3',
}


@pytest.fixture(autouse=True)
def _ambiente_pulito(monkeypatch):
    for chiave in CHIAVI_PERICOLOSE:
        monkeypatch.delenv(chiave, raising=False)


@pytest.fixture(scope='module')
def servizio(tmp_path_factory):
    """Un servizio per modulo: i test usano slug diversi e non si pestano."""
    with relay_avviato(tmp_path_factory.mktemp('mercati'),
                       **AMBIENTE_DEL_SERVIZIO) as base:
        yield base


def _firma_telegram(campi: dict, bot_token: str = BOT_FINTO) -> str:
    stringa = '\n'.join(f'{k}={campi[k]}' for k in sorted(campi) if k != 'hash')
    chiave = hashlib.sha256(bot_token.encode()).digest()
    return hmac.new(chiave, stringa.encode(), hashlib.sha256).hexdigest()


def _dati_login(telegram_id, **extra) -> dict:
    campi = {'id': telegram_id, 'first_name': f'Utente{telegram_id[-3:]}',
             'username': f'utente{telegram_id[-3:]}',
             'auth_date': str(int(time.time()))}
    campi.update(extra)
    campi['hash'] = _firma_telegram(campi)
    return campi


def _chiama(base, metodo, path, corpo=None, cookie=None, grezzo=None):
    url = f'{base}{path}'
    dati = grezzo if grezzo is not None else (
        json.dumps(corpo).encode() if corpo is not None else None)
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


def _login(base, telegram_id):
    stato, corpo, intestazioni = _chiama(base, 'POST', '/api/login/telegram',
                                         corpo=_dati_login(telegram_id))
    assert stato == 200, corpo
    return _cookie_dalla_risposta(intestazioni)


@pytest.fixture(scope='module')
def sessioni(servizio):
    """Cookie di due clienti distinti: l'isolamento si misura in due, non in uno."""
    return _login(servizio, CLIENTE_A), _login(servizio, CLIENTE_B)


def _json(corpo):
    return json.loads(corpo)


def _crea_sport(base, cookie, nome):
    stato, corpo, _ = _chiama(base, 'POST', '/api/me/sports',
                              corpo={'nome': nome}, cookie=cookie)
    assert stato == 200, corpo
    return _json(corpo)


def _crea_mercato(base, cookie, sport, **campi):
    corpo_req = {'marketType': 'OVER_UNDER_05HT', 'marketName': 'Over/Under 0.5 Goals HT',
                 'selections': ['Over 0,5 goal', 'Under 0,5 goal']}
    corpo_req.update(campi)
    stato, corpo, _ = _chiama(base, 'POST', f'/api/me/sports/{sport}/mercati',
                              corpo=corpo_req, cookie=cookie)
    assert stato == 200, corpo
    return _json(corpo)


# ------------------------------------------------------------------ CRUD base

def test_il_primo_login_parte_VUOTO_e_lo_sport_si_crea_da_zero(servizio, sessioni):
    """«Al primo login e' tutto vuoto»: nessun catalogo, nessun seme."""
    cookie_a, _ = sessioni
    stato, corpo, intestazioni = _chiama(servizio, 'GET', '/api/me/sports',
                                         cookie=cookie_a)
    assert stato == 200
    assert _json(corpo)['sports'] == []
    # Ogni rotta autenticata rinnova il cookie: e' il contratto di
    # `_rispondi_con_sessione`, e una rotta nuova che lo salta fa scadere la
    # sessione a meta' navigazione.
    assert _cookie_dalla_risposta(intestazioni) is not None

    sport = _crea_sport(servizio, cookie_a, 'Calcio')
    assert sport['slug'] == 'calcio'
    assert sport['nome'] == 'Calcio'

    stato, corpo, _ = _chiama(servizio, 'GET', '/api/me/sports', cookie=cookie_a)
    assert [s['slug'] for s in _json(corpo)['sports']] == ['calcio']


def test_mercato_e_selezioni_si_creano_e_si_leggono_annidati(servizio, sessioni):
    """Sport → mercato → selezioni, come negli sketch approvati della #33."""
    cookie_a, _ = sessioni
    _crea_sport(servizio, cookie_a, 'Tennis')
    mercato = _crea_mercato(servizio, cookie_a, 'tennis')
    assert mercato['marketType'] == 'OVER_UNDER_05HT'
    assert mercato['marketName'] == 'Over/Under 0.5 Goals HT'
    assert [s['selectionName'] for s in mercato['selezioni']] == \
        ['Over 0,5 goal', 'Under 0,5 goal']

    # La lista annidata: il wizard legge tutto con una chiamata.
    stato, corpo, _ = _chiama(servizio, 'GET', '/api/me/sports/tennis/mercati',
                              cookie=cookie_a)
    assert stato == 200
    mercati = _json(corpo)['mercati']
    assert len(mercati) == 1
    assert len(mercati[0]['selezioni']) == 2

    # Una selezione aggiunta dopo («Aggiungi» dello sketch): quante ne servono.
    mid = mercati[0]['id']
    stato, corpo, _ = _chiama(servizio, 'POST',
                              f'/api/me/sports/tennis/mercati/{mid}/selezioni',
                              corpo={'selectionName': 'Pareggio'}, cookie=cookie_a)
    assert stato == 200
    stato, corpo, _ = _chiama(servizio, 'GET',
                              f'/api/me/sports/tennis/mercati/{mid}/selezioni',
                              cookie=cookie_a)
    nomi = [s['selectionName'] for s in _json(corpo)['selezioni']]
    assert 'Pareggio' in nomi and len(nomi) == 3

    # Eliminare UNA selezione non tocca le altre.
    sid = next(s['id'] for s in _json(corpo)['selezioni']
               if s['selectionName'] == 'Pareggio')
    stato, _, _ = _chiama(servizio, 'DELETE',
                          f'/api/me/sports/tennis/mercati/{mid}/selezioni/{sid}',
                          cookie=cookie_a)
    assert stato == 200
    stato, corpo, _ = _chiama(servizio, 'GET',
                              f'/api/me/sports/tennis/mercati/{mid}/selezioni',
                              cookie=cookie_a)
    assert len(_json(corpo)['selezioni']) == 2


def test_eliminare_lo_sport_porta_via_i_SUOI_mercati_e_nient_altro(servizio, sessioni):
    """La cascata e' esplicita e confinata: l'altro sport resta intero."""
    cookie_a, _ = sessioni
    # La quota del modulo e' 3 e 'tennis' (test precedente) non serve piu': si
    # libera il posto qui, PRIMA di crearne due — non alla fine, dove la quota
    # sarebbe gia' esplosa. Senza asserzione: da solo, questo test non lo trova.
    _chiama(servizio, 'DELETE', '/api/me/sports/tennis', cookie=cookie_a)
    _crea_sport(servizio, cookie_a, 'Basket')
    _crea_sport(servizio, cookie_a, 'Volley')
    _crea_mercato(servizio, cookie_a, 'basket')
    superstite = _crea_mercato(servizio, cookie_a, 'volley',
                               marketType='MATCH_ODDS', marketName='Match Odds',
                               selections=['Casa'])

    stato, _, _ = _chiama(servizio, 'DELETE', '/api/me/sports/basket', cookie=cookie_a)
    assert stato == 200
    stato, _, _ = _chiama(servizio, 'GET', '/api/me/sports/basket/mercati',
                          cookie=cookie_a)
    assert stato == 404, 'lo sport eliminato risponde ancora'

    stato, corpo, _ = _chiama(servizio, 'GET', '/api/me/sports/volley/mercati',
                              cookie=cookie_a)
    assert stato == 200
    assert _json(corpo)['mercati'][0]['id'] == superstite['id']
    assert len(_json(corpo)['mercati'][0]['selezioni']) == 1

    # Pulizia per non consumare la quota bassa del modulo.
    _chiama(servizio, 'DELETE', '/api/me/sports/volley', cookie=cookie_a)


def test_due_sport_con_lo_stesso_nome_si_disambiguano_come_i_parser(servizio, sessioni):
    _, cookie_b = sessioni
    primo = _crea_sport(servizio, cookie_b, 'Calcio')
    secondo = _crea_sport(servizio, cookie_b, 'Calcio')
    assert primo['slug'] == 'calcio'
    assert secondo['slug'] == 'calcio-2'
    _chiama(servizio, 'DELETE', '/api/me/sports/calcio-2', cookie=cookie_b)


# ------------------------------------------------------------------ isolamento

def test_i_dati_di_un_utente_NON_esistono_per_un_altro(servizio, sessioni):
    """404, non 403: un 403 confermerebbe che quello sport esiste."""
    cookie_a, cookie_b = sessioni
    # A ha 'calcio' (creato sopra); B ha il SUO 'calcio': ognuno vede il proprio.
    mercato_a = _crea_mercato(servizio, cookie_a, 'calcio',
                              marketType='SOLO_DI_A', marketName='Solo di A',
                              selections=['Riga di A'])
    stato, corpo, _ = _chiama(servizio, 'GET', '/api/me/sports/calcio/mercati',
                              cookie=cookie_b)
    assert stato == 200, 'B ha il proprio sport calcio: deve vedere QUELLO'
    assert all(m['marketType'] != 'SOLO_DI_A' for m in _json(corpo)['mercati']), \
        'B vede un mercato di A: isolamento rotto'

    # B non puo' toccare il mercato di A nemmeno conoscendone l'id.
    mid = mercato_a['id']
    stato, _, _ = _chiama(servizio, 'DELETE',
                          f'/api/me/sports/calcio/mercati/{mid}', cookie=cookie_b)
    assert stato == 404, f'B elimina il mercato di A: {stato}'
    stato, corpo, _ = _chiama(servizio, 'GET', '/api/me/sports/calcio/mercati',
                              cookie=cookie_a)
    assert any(m['id'] == mid for m in _json(corpo)['mercati']), \
        'il mercato di A e\' sparito dopo il tentativo di B'

    # Uno sport che B non ha proprio → 404.
    stato, _, _ = _chiama(servizio, 'GET', '/api/me/sports/non-esiste/mercati',
                          cookie=cookie_b)
    assert stato == 404


# ------------------------------------------------------------------ validazione

def test_senza_sessione_401_anche_con_corpo_malformato(servizio):
    """Il 401 arriva PRIMA del 422: un estraneo non scopre che la rotta esiste."""
    stato, _, _ = _chiama(servizio, 'POST', '/api/me/sports',
                          grezzo=b'{non json')
    assert stato == 401
    stato, _, _ = _chiama(servizio, 'GET', '/api/me/sports')
    assert stato == 401


@pytest.mark.parametrize('corpo', [
    {'nome': ''},
    {'nome': '   '},
    {},
    {'nome': 'x' * 121},
])
def test_sport_con_nome_vuoto_o_oltre_il_tetto_422(servizio, sessioni, corpo):
    cookie_a, _ = sessioni
    stato, _, _ = _chiama(servizio, 'POST', '/api/me/sports',
                          corpo=corpo, cookie=cookie_a)
    assert stato == 422


@pytest.mark.parametrize('campi', [
    {'marketType': ''},
    {'marketName': ''},
    {'marketType': 'x' * 121},
    {'selections': ['']},
    {'selections': ['ok', 'x' * 121]},
    {'marketType': 'CON_EMOJI_🆚'},       # XTrader scarta i simboli in silenzio (#42)
    {'selections': ['Over ⭐']},
])
def test_mercato_con_campi_storti_422(servizio, sessioni, campi):
    cookie_a, _ = sessioni
    corpo_req = {'marketType': 'MT_OK', 'marketName': 'Nome ok', 'selections': ['S1']}
    corpo_req.update(campi)
    stato, _, _ = _chiama(servizio, 'POST', '/api/me/sports/calcio/mercati',
                          corpo=corpo_req, cookie=cookie_a)
    assert stato == 422


def test_il_doppione_esatto_di_mercato_e_di_selezione_409(servizio, sessioni):
    cookie_a, _ = sessioni
    _crea_mercato(servizio, cookie_a, 'calcio',
                  marketType='MATCH_ODDS', marketName='Match Odds',
                  selections=['Casa'])
    stato, _, _ = _chiama(servizio, 'POST', '/api/me/sports/calcio/mercati',
                          corpo={'marketType': 'MATCH_ODDS', 'marketName': 'Match Odds',
                                 'selections': []},
                          cookie=cookie_a)
    assert stato == 409, 'lo stesso mercato due volte non e\' un errore silenzioso'

    stato, corpo, _ = _chiama(servizio, 'GET', '/api/me/sports/calcio/mercati',
                              cookie=cookie_a)
    mid = next(m['id'] for m in _json(corpo)['mercati']
               if m['marketType'] == 'MATCH_ODDS')
    stato, _, _ = _chiama(servizio, 'POST',
                          f'/api/me/sports/calcio/mercati/{mid}/selezioni',
                          corpo={'selectionName': 'Casa'}, cookie=cookie_a)
    assert stato == 409


def test_la_quota_sport_e_un_409_e_si_regola_da_variabile(servizio, sessioni):
    """MAX_SPORT_PER_UTENTE=3 nell'ambiente del servizio: il quarto non entra."""
    _, cookie_b = sessioni
    # B ha gia' 'calcio'; salire fino a 3 e provare il quarto.
    _crea_sport(servizio, cookie_b, 'Sport due')
    _crea_sport(servizio, cookie_b, 'Sport tre')
    stato, corpo, _ = _chiama(servizio, 'POST', '/api/me/sports',
                              corpo={'nome': 'Sport quattro'}, cookie=cookie_b)
    assert stato == 409, corpo
    _chiama(servizio, 'DELETE', '/api/me/sports/sport-due', cookie=cookie_b)
    _chiama(servizio, 'DELETE', '/api/me/sports/sport-tre', cookie=cookie_b)


def test_gli_identificativi_non_numerici_sono_404_non_422(servizio, sessioni):
    """Come le rotte admin: un id malformato non conferma l'esistenza della rotta."""
    cookie_a, _ = sessioni
    stato, _, _ = _chiama(servizio, 'DELETE',
                          '/api/me/sports/calcio/mercati/NON-NUMERO', cookie=cookie_a)
    assert stato == 404
    stato, _, _ = _chiama(servizio, 'DELETE',
                          '/api/me/sports/calcio/mercati/1/selezioni/NON-NUMERO',
                          cookie=cookie_a)
    assert stato == 404


def test_le_creazioni_concorrenti_sullo_stesso_nome_non_danno_500(servizio, sessioni):
    """La corsa dello slug, come per i parser (PR #30): retry, non 500."""
    cookie_a, _ = sessioni
    esiti = []

    def crea():
        stato, corpo, _ = _chiama(servizio, 'POST', '/api/me/sports',
                                  corpo={'nome': 'Corsa'}, cookie=cookie_a)
        esiti.append((stato, corpo))

    fili = [threading.Thread(target=crea) for _ in range(2)]
    for f in fili:
        f.start()
    for f in fili:
        f.join()
    assert all(stato in (200, 409) for stato, _ in esiti), esiti
    riusciti = [_json(corpo)['slug'] for stato, corpo in esiti if stato == 200]
    assert len(riusciti) == len(set(riusciti)), f'slug duplicati: {riusciti}'
    for slug in riusciti:
        _chiama(servizio, 'DELETE', f'/api/me/sports/{slug}', cookie=cookie_a)


# ------------------------------------------- il parser consuma la libreria (#33)

CONFIG_BASE = {
    'match': {'type': 'contains', 'value': 'SEGNALE'},
    'columns': {
        'EventName': {'source': 'line', 'anchor': 'evento', 'part': 'after',
                      'marker': ':', 'transforms': [{'op': 'trim'}]},
        'BetType': {'source': 'constant', 'value': 'PUNTA'},
    },
}


def _config_betfair(mercato, selezione, market_id, selection_id):
    config = json.loads(json.dumps(CONFIG_BASE))
    config['columns']['MarketType'] = {'source': 'constant', 'value': mercato['marketType']}
    config['columns']['MarketName'] = {'source': 'constant', 'value': mercato['marketName']}
    config['columns']['SelectionName'] = {'source': 'constant', 'value': selezione}
    config['betfair'] = {'market_id': market_id, 'selection_id': selection_id}
    return config


@pytest.fixture()
def mercato_di_a(servizio, sessioni):
    cookie_a, _ = sessioni
    stato, corpo, _ = _chiama(servizio, 'GET', '/api/me/sports/calcio/mercati',
                              cookie=cookie_a)
    assert stato == 200, 'serve lo sport calcio di A creato dai test precedenti'
    for m in _json(corpo)['mercati']:
        if m['marketType'] == 'OVER_UNDER_05HT':
            return m
    return _crea_mercato(servizio, cookie_a, 'calcio')


def test_il_parser_da_mercati_betfair_scrive_le_TRE_colonne_giuste(
        servizio, sessioni, mercato_di_a):
    """Il caso buono: mercato e selezione DELL'UTENTE → riga XTrader corretta."""
    cookie_a, _ = sessioni
    selezione = mercato_di_a['selezioni'][0]
    config = _config_betfair(mercato_di_a, selezione['selectionName'],
                             mercato_di_a['id'], selezione['id'])
    stato, corpo, _ = _chiama(servizio, 'POST', '/api/me/parsers',
                              corpo={'titolo': 'Da libreria', 'config': config},
                              cookie=cookie_a)
    assert stato == 200, corpo
    slug = _json(corpo)['slug']

    stato, corpo, _ = _chiama(servizio, 'POST', f'/api/me/parsers/{slug}/test',
                              corpo={'message': 'SEGNALE\nevento: Juventus v Palermo'},
                              cookie=cookie_a)
    assert stato == 200
    esito = _json(corpo)
    assert esito['complete'], esito
    assert '"OVER_UNDER_05HT"' in esito['csv']
    assert '"Over/Under 0.5 Goals HT"' in esito['csv']
    assert '"Over 0,5 goal"' in esito['csv']


def test_una_selezione_che_NON_hai_creato_viene_rifiutata(servizio, sessioni,
                                                          mercato_di_a):
    """Il test che la #33 chiede per nome: selezione arbitraria via HTTP → 422."""
    cookie_a, _ = sessioni
    selezione = mercato_di_a['selezioni'][0]

    # selection_id inesistente.
    config = _config_betfair(mercato_di_a, selezione['selectionName'],
                             mercato_di_a['id'], 99999)
    stato, _, _ = _chiama(servizio, 'POST', '/api/me/parsers',
                          corpo={'titolo': 'Selezione finta', 'config': config},
                          cookie=cookie_a)
    assert stato == 422, 'una selezione mai creata e\' stata accettata'

    # Valore costante DIVERSO da quello della libreria: il riferimento non basta,
    # contano i byte che finirebbero nel CSV.
    config = _config_betfair(mercato_di_a, 'Valore inventato',
                             mercato_di_a['id'], selezione['id'])
    stato, _, _ = _chiama(servizio, 'POST', '/api/me/parsers',
                          corpo={'titolo': 'Valore alterato', 'config': config},
                          cookie=cookie_a)
    assert stato == 422, 'un valore diverso dalla libreria e\' stato accettato'


def test_il_mercato_di_un_ALTRO_utente_non_e_spendibile(servizio, sessioni,
                                                        mercato_di_a):
    cookie_a, cookie_b = sessioni
    selezione = mercato_di_a['selezioni'][0]
    config = _config_betfair(mercato_di_a, selezione['selectionName'],
                             mercato_di_a['id'], selezione['id'])
    stato, _, _ = _chiama(servizio, 'POST', '/api/me/parsers',
                          corpo={'titolo': 'Rubato', 'config': config},
                          cookie=cookie_b)
    assert stato == 422, 'B ha speso il mercato di A: isolamento rotto'


def test_i_segnaposto_squadra_sono_fail_closed_finche_manca_la_34(servizio, sessioni):
    """`{HOME_TEAM}` letterale nel CSV = segnale scartato in silenzio da XTrader.

    La selezione handicap SI PUO' creare (e' un dato della libreria, e la #34 la
    rendera' spendibile); e' l'uso nel parser, oggi, a dover essere rifiutato con
    il motivo.
    """
    cookie_a, _ = sessioni
    mercato = _crea_mercato(servizio, cookie_a, 'calcio',
                            marketType='TEAM_A_1', marketName='{HOME_TEAM} +1',
                            selections=['Pareggio · {HOME_TEAM} +1 · {AWAY_TEAM} -1'])
    selezione = mercato['selezioni'][0]
    config = _config_betfair(mercato, selezione['selectionName'],
                             mercato['id'], selezione['id'])
    stato, corpo, _ = _chiama(servizio, 'POST', '/api/me/parsers',
                              corpo={'titolo': 'Handicap presto', 'config': config},
                              cookie=cookie_a)
    assert stato == 422, 'un segnaposto irrisolto sarebbe finito letteralmente nel CSV'
    assert 'squadre' in _json(corpo)['detail'].lower(), \
        'il motivo non dice all\'utente da dove viene il rifiuto'


def test_eliminare_il_mercato_NON_rompe_il_parser_gia_salvato(servizio, sessioni,
                                                              mercato_di_a):
    """Le regole sono COSTANTI: la libreria e' provenienza, non dipendenza viva.

    Il comportamento e' deliberato e va vincolato: chi elimina un mercato non deve
    scoprire mesi dopo che un parser attivo ha smesso di scrivere.
    """
    cookie_a, _ = sessioni
    selezione = mercato_di_a['selezioni'][0]
    config = _config_betfair(mercato_di_a, selezione['selectionName'],
                             mercato_di_a['id'], selezione['id'])
    stato, corpo, _ = _chiama(servizio, 'POST', '/api/me/parsers',
                              corpo={'titolo': 'Sopravvive', 'config': config},
                              cookie=cookie_a)
    assert stato == 200, corpo
    slug = _json(corpo)['slug']

    stato, _, _ = _chiama(servizio, 'DELETE',
                          f'/api/me/sports/calcio/mercati/{mercato_di_a["id"]}',
                          cookie=cookie_a)
    assert stato == 200

    stato, corpo, _ = _chiama(servizio, 'POST', f'/api/me/parsers/{slug}/test',
                              corpo={'message': 'SEGNALE\nevento: Juventus v Palermo'},
                              cookie=cookie_a)
    assert _json(corpo)['complete'], 'il parser salvato dipende ancora dalla libreria'


def test_l_INSERT_e_condizionato_al_padre_e_non_lascia_orfani(tmp_path, monkeypatch):
    """[REAL_FINDING] di Claude Fable 5 sulla PR #55: la finestra TOCTOU.

    Il controllo di proprieta' delle rotte e' una LETTURA; fra quella lettura e
    l'INSERT una DELETE concorrente del padre puo' committare, e l'INSERT diretto
    scriveva una riga orfana — invisibile alle API e non piu' eliminabile.
    Misurato sul codice precedente: 1 riga orfana. La simulazione qui e'
    deterministica: la DELETE e' GIA' committata quando l'INSERT parte, che e'
    esattamente lo stato del mondo dentro la finestra.
    """
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'orfani.db'))
    monkeypatch.setattr(main, '_PERCORSI_MIGRATI', set())
    c = main.db()
    c.execute("INSERT INTO users(telegram_id) VALUES ('1')")
    uid = c.execute('SELECT last_insert_rowid()').fetchone()[0]

    # Mercato il cui sport muore fra il controllo e l'INSERT.
    c.execute("INSERT INTO sports(user_id, slug, nome) VALUES (?, 'calcio', 'Calcio')",
              (uid,))
    sport_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    c.execute('DELETE FROM sports WHERE id=?', (sport_id,))
    assert main._inserisci_mercato(c, sport_id, 'MATCH_ODDS', 'Match Odds') is None
    assert c.execute('SELECT COUNT(*) FROM betfair_markets').fetchone()[0] == 0, \
        'l\'INSERT ha scritto un mercato orfano'

    # Selezione il cui mercato muore fra il controllo e l'INSERT (regola 2:
    # stessa classe, entrambi i siti).
    c.execute("INSERT INTO sports(user_id, slug, nome) VALUES (?, 'tennis', 'Tennis')",
              (uid,))
    sport_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    c.execute('INSERT INTO betfair_markets(sport_id, market_type, market_name)'
              " VALUES (?, 'MATCH_ODDS', 'Match Odds')", (sport_id,))
    market_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    c.execute('DELETE FROM betfair_markets WHERE id=?', (market_id,))
    assert main._inserisci_selezione(c, market_id, 'Casa') is None
    assert c.execute('SELECT COUNT(*) FROM betfair_selections').fetchone()[0] == 0, \
        'l\'INSERT ha scritto una selezione orfana'

    # E il caso normale resta normale: padre vivo → id vero, riga scritta.
    mid = main._inserisci_mercato(c, sport_id, 'OVER_UNDER_05', 'Over/Under 0.5')
    assert isinstance(mid, int)
    assert main._inserisci_selezione(c, mid, 'Over 0,5 goal') is not None
    c.close()


# --------------------------------------------- riparazione account e libreria

def test_la_riconciliazione_travasa_gli_sport_e_ridisambigua_gli_slug(tmp_path,
                                                                      monkeypatch):
    """Come per i parser (bloccante GPT-5.5): stesso slug su due utenti = stato legale.

    `UNIQUE (user_id, slug)` lo vieta sulla STESSA riga utente, quindi il travaso
    cieco solleverebbe — e sollevare in `riconcilia_su_utente` significa che la
    riparazione dell'account muore a meta'. In-process di proposito: la
    riconciliazione non ha una rotta HTTP.
    """
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'sport.db'))
    monkeypatch.setattr(main, '_PERCORSI_MIGRATI', set())
    c = main.db()
    c.execute("INSERT INTO users(telegram_id) VALUES ('111')")
    vincitore = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    c.execute("INSERT INTO users(telegram_id) VALUES ('222')")
    perdente = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    c.execute("INSERT INTO sports(user_id, slug, nome) VALUES (?, 'calcio', 'Calcio')",
              (vincitore,))
    c.execute("INSERT INTO sports(user_id, slug, nome) VALUES (?, 'calcio', 'Calcio')",
              (perdente,))
    sport_perdente = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    # Il mercato segue il SUO sport: riferisce `sport_id`, che non cambia.
    c.execute('INSERT INTO betfair_markets(sport_id, market_type, market_name)'
              " VALUES (?, 'MATCH_ODDS', 'Match Odds')", (sport_perdente,))

    main.riconcilia_su_utente(c, perdente, vincitore)
    c.commit()

    slugs = sorted(r[0] for r in c.execute(
        'SELECT slug FROM sports WHERE user_id=?', (vincitore,)).fetchall())
    assert slugs == ['calcio', 'calcio-2'], \
        f'il travaso degli sport non ridisambigua: {slugs}'
    assert not c.execute('SELECT 1 FROM sports WHERE user_id=?',
                         (perdente,)).fetchone(), 'il perdente ha ancora sport'
    # Chi era gia' del vincitore tiene il suo slug; a cambiare nome e' chi arriva.
    arrivato = c.execute('SELECT id FROM sports WHERE user_id=? AND slug=?',
                         (vincitore, 'calcio-2')).fetchone()[0]
    assert arrivato == sport_perdente
    assert c.execute('SELECT COUNT(*) FROM betfair_markets WHERE sport_id=?',
                     (sport_perdente,)).fetchone()[0] == 1
    c.close()
