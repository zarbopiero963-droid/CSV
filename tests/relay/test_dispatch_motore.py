"""Il dispatcher e la blindatura ReDoS: il motore configurabile entra nel server.

Questo file copre il passo 2 del lavoro «togliere il parser dal codice»:

- **il dispatcher** `elabora_messaggio`: un parser con `config_json` gira sul motore
  configurabile (`esegui_parser`); un parser senza — PIERO e ogni parser legacy —
  resta su `parse_message`, byte per byte com'era. Il feed di produzione non cambia,
  e i test del contratto CSV di PIERO (`test_csv_contract.py`, `test_webhook.py`) non
  si muovono;

- **la ReDoS**, cioe' la richiesta esplicita del proprietario «non deve bloccare a
  tutti, deve essere tutto personale del cliente». Le regex dei parser le scrivono
  gli utenti e girano sul worker Railway CONDIVISO: senza un limite, un pattern con
  backtracking catastrofico scritto da un cliente bloccherebbe il parsing di TUTTI.
  Lo `re` di stdlib non ha timeout; il modulo `regex` si', e `_cerca_regex_utente`
  interrompe il match allo scadere del deadline. Il caso di un cliente che INONDA di
  messaggi cattivi resta scoperto (serve un rate-limit per-utente, rimandato per
  decisione del proprietario): qui si chiude il caso «un solo pattern blocca tutti».

Fail-first, per la ReDoS: `test_lo_re_di_stdlib_APPENDE_dove_il_motore_scade` fa
girare lo `re` di stdlib sullo stesso pattern in un sottoprocesso e lo vede NON
finire entro il limite — la vulnerabilita' che la patch chiude — mentre il motore
con timeout torna subito.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE))

import main  # noqa: E402 - dopo l'inserimento del percorso
from tests.ambiente import CHIAVI_PERICOLOSE, TOKEN_DI_PROVA  # noqa: E402

# Una regex catastrofica per un motore con backtracking: `(a|aa)+$` su una stringa
# di sole 'a' che NON termina come richiesto esplora un numero esponenziale di
# suddivisioni. Misurato: lo `re` di stdlib non finisce; il modulo `regex` con
# `timeout=0.1` scade a 0,1 s.
REGEX_CATASTROFICA = r'(a|aa)+$'
SOGGETTO_CATTIVO = 'a' * 60 + 'b'

# Un tetto ampio: il timeout del motore e' 0,1 s, quindi una risposta sana arriva
# in una frazione di secondo. 2 s e' lontano da un match sano e lontanissimo dal
# «non finisce mai» dello `re` di stdlib, cosi' il test non e' fragile in CI.
TETTO_S = 2.0


@pytest.fixture(autouse=True)
def _ambiente_pulito(monkeypatch):
    """Nessuna variabile della macchina entra: come nelle altre suite di `relay`.

    L'handler di avvio legge `os.environ` direttamente, quindi non basta azzerare le
    costanti del modulo. Vedi `tests/ambiente.py`.
    """
    for chiave in CHIAVI_PERICOLOSE:
        monkeypatch.delenv(chiave, raising=False)
    monkeypatch.setattr(main, 'TOKEN', TOKEN_DI_PROVA)


# --------------------------------------------------- ReDoS: il timeout duro

def test_condizione_con_regex_catastrofica_scade_e_non_appende():
    """La condizione di riconoscimento con un pattern cattivo scade, non appende.

    E' uno dei due punti dove una regex dell'utente incontra il messaggio
    (`condizione_soddisfatta`). Senza il timeout, questa chiamata non tornerebbe: il
    worker resterebbe fermo e nessun altro cliente verrebbe servito.
    """
    t = time.monotonic()
    esito = main.condizione_soddisfatta(SOGGETTO_CATTIVO,
                                        {'type': 'regex', 'value': REGEX_CATASTROFICA})
    trascorso = time.monotonic() - t
    assert esito is False, 'un match scaduto deve valere «non riconosciuto», non True'
    assert trascorso < TETTO_S, (
        f'la condizione ha impiegato {trascorso:.2f}s: il timeout non ha interrotto '
        f'il backtracking catastrofico, e il worker resterebbe bloccato per tutti')


def test_estrazione_con_regex_catastrofica_scade_e_non_appende():
    """L'altro punto: una colonna con sorgente `regex` e un pattern cattivo.

    Regola 2 (cercare la CLASSE, non il sito): la ReDoS non e' su una sola funzione,
    e' su OGNI posto che esegue una regex dell'utente. Sono due — la condizione e
    l'estrazione — e vanno chiusi entrambi.
    """
    regola = {'source': 'regex', 'pattern': REGEX_CATASTROFICA, 'group': 0}
    t = time.monotonic()
    valore = main._estrai_valore(SOGGETTO_CATTIVO, regola)
    trascorso = time.monotonic() - t
    assert valore == '', 'un match scaduto deve dare colonna vuota, non appendere'
    assert trascorso < TETTO_S, (
        f'l-estrazione ha impiegato {trascorso:.2f}s: il timeout non ha interrotto il '
        f'pattern catastrofico')


def test_lo_re_di_stdlib_APPENDE_dove_il_motore_scade():
    """Fail-first della ReDoS: lo `re` di stdlib NON finisce; il motore si'.

    Non si puo' eseguire lo `re` catastrofico in-process — appenderebbe la suite —
    quindi gira in un sottoprocesso con un tetto di 3 s. Che non finisca entro il
    tetto E' la vulnerabilita' che la patch chiude: prima di `_cerca_regex_utente` il
    motore usava `re.search` su questi stessi due punti, e un pattern cosi' avrebbe
    bloccato il worker condiviso. Subito dopo, lo stesso pattern nel motore torna
    entro il tetto.
    """
    codice = f'import re; re.search({REGEX_CATASTROFICA!r}, {SOGGETTO_CATTIVO!r})'
    with pytest.raises(subprocess.TimeoutExpired):
        subprocess.run([sys.executable, '-c', codice], timeout=3, capture_output=True)

    # Lo stesso pattern nel motore, con il timeout: torna subito.
    t = time.monotonic()
    assert main.condizione_soddisfatta(SOGGETTO_CATTIVO,
                                       {'type': 'regex', 'value': REGEX_CATASTROFICA}) is False
    assert time.monotonic() - t < TETTO_S


def test_una_regex_SANA_funziona_ancora():
    """Il controllo che impedisce la correzione pigra «rifiuta ogni regex».

    Il timeout non deve rompere l'uso normale: una regex sana estrae ancora il suo
    valore. Senza questo caso, far restituire None a ogni match passerebbe tutti i
    test qui sopra e spegnerebbe la sorgente `regex` del motore.
    """
    regola = {'source': 'regex', 'pattern': r'@\s*([0-9.,]+)', 'group': 1}
    assert main._estrai_valore('quota @ 1,85 sul match', regola) == '1,85'
    assert main.condizione_soddisfatta('P.Bet LIVE', {'type': 'regex', 'value': r'p\.?bet'}) is True


# --------------------------------------------------- il dispatcher

# Un parser della web app: riconosce un messaggio e mappa le quattro obbligatorie.
CONFIG_WEB = {
    'match': {'type': 'contains', 'value': 'SEGNALE'},
    'columns': {
        'EventName': {'source': 'line', 'anchor': 'evento', 'part': 'after', 'marker': ':',
                      'transforms': [{'op': 'trim'}]},
        'MarketType': {'source': 'constant', 'value': 'OVER_UNDER_15'},
        'SelectionName': {'source': 'constant', 'value': 'Over 1,5 goal'},
        'BetType': {'source': 'constant', 'value': 'PUNTA'},
    },
}

# Il parser PIERO, nella forma che `get_parser` restituisce: `config_json` a None,
# come la colonna vuota in produzione.
CFG_PIERO = {
    'name': main.DEFAULT_PARSER,
    'header': 'P.Bet. PREMACHT 0,5HT',
    'market_name': 'Over/Under 1,5 gol',
    'market_type': 'OVER_UNDER_15',
    'selection_name': 'Over 1,5 goal',
    'handicap': '0',
    'bet_type': 'PUNTA',
    'config_json': None,
}

MSG_PIERO = ('P.Bet. PREMACHT 0,5HT\n'
             '\U0001F19A Inter v Milan\n'
             '@ 1.85')


def _cfg_web(config):
    return {'name': 'web', 'header': '', 'market_name': '', 'market_type': '',
            'selection_name': '', 'handicap': '', 'bet_type': '',
            'config_json': json.dumps(config)}


def test_senza_config_json_il_dispatcher_e_IDENTICO_a_parse_message():
    """PIERO e ogni parser legacy: il dispatcher deve restare `parse_message`.

    E' l'invariante che tiene fermo il feed di produzione. Non e' una tautologia da
    lasciare implicita: se un domani qualcuno cambiasse `elabora_messaggio` per
    passare ANCHE i parser legacy dal motore, il feed di PIERO potrebbe divergere di
    un byte e questo test lo fermerebbe. Confronto sull'output completo — evento e
    CSV — non solo sul fatto che entrambi non siano None.
    """
    via_dispatcher = main.elabora_messaggio(MSG_PIERO, CFG_PIERO)
    via_legacy = main.parse_message(MSG_PIERO, CFG_PIERO)
    assert via_dispatcher == via_legacy, (
        'il dispatcher ha cambiato il risultato del parser legacy:\n'
        f'  dispatcher: {via_dispatcher!r}\n  parse_message: {via_legacy!r}')
    # E il risultato e' quello atteso, non due None uguali per un errore comune.
    assert via_dispatcher is not None
    assert via_dispatcher['event'] == 'Inter - Milan', via_dispatcher


def test_con_config_json_il_dispatcher_usa_il_MOTORE():
    """Un parser della web app gira su `esegui_parser` e produce il feed atteso."""
    cfg = _cfg_web(CONFIG_WEB)
    parsed = main.elabora_messaggio('SEGNALE\nEvento: Roma v Lazio\n@ 2.10', cfg)
    assert parsed is not None, 'il messaggio riconosciuto non ha prodotto segnale'
    assert parsed['event'] == 'Roma v Lazio', parsed['event']
    # Il CSV e' quello del motore, valido per il contratto (14 colonne, BOM, CRLF).
    # `verify_csv` restituisce la stringa validata; il BOM si scrive con l'escape
    # `\ufeff`, mai come carattere letterale (REGOLA CODIFICA / test_codifica.py).
    corpo = main.verify_csv(parsed['csv'])
    assert corpo.startswith('\ufeff'), 'manca il BOM'
    assert '"Roma v Lazio"' in corpo
    assert '"OVER_UNDER_15"' in corpo
    assert '"PUNTA"' in corpo


def test_config_json_che_NON_riconosce_da_None():
    """Condizione non soddisfatta → nessun segnale, come `parse_message` senza header."""
    cfg = _cfg_web(CONFIG_WEB)
    assert main.elabora_messaggio('un messaggio qualunque senza la parola chiave', cfg) is None


def test_config_json_riconosciuto_ma_INCOMPLETO_da_None():
    """Riconosciuto ma senza una colonna obbligatoria → nessuna riga.

    E' la regola «guarda `complete`, non `matched`»: un messaggio che soddisfa la
    condizione ma lascia vuota `EventName` (qui la riga «evento:» non c'e') non deve
    produrre una riga quotata e priva di senso per XTrader.
    """
    cfg = _cfg_web(CONFIG_WEB)
    assert main.elabora_messaggio('SEGNALE senza la riga evento', cfg) is None


def test_config_json_NON_VALIDO_da_None_e_non_solleva():
    """Un `config_json` corrotto non deve far cadere l'handler.

    Nel webhook `elabora_messaggio` che sollevasse diventerebbe un 500, e Telegram
    ritenterebbe la consegna in loop. Un JSON illeggibile vale «non riconosciuto».
    """
    cfg = {'name': 'rotto', 'header': '', 'config_json': '{ questo non e- json'}
    assert main.elabora_messaggio('SEGNALE\nEvento: A v B', cfg) is None


# --------------------------------------------------- isolamento: uno non blocca l'altro

def test_un_parser_con_regex_CATTIVA_non_blocca_un_altro_parser():
    """La richiesta del proprietario, resa test: «non deve bloccare a tutti».

    Il cliente A ha una condizione regex catastrofica; il cliente B un parser sano.
    Si elabora un messaggio con A (che scade e non produce segnale) e SUBITO uno con
    B (che produce il suo segnale). Le due cose insieme devono restare ben sotto il
    tetto: la regex malata di A non ruba il worker a B. E' il singolo match a essere
    limitato — contro un cliente che INONDA serve il rate-limit per-utente, rimandato.
    """
    cfg_a = _cfg_web({'match': {'type': 'regex', 'value': REGEX_CATASTROFICA},
                      'columns': {'EventName': {'source': 'constant', 'value': 'X'},
                                  'MarketType': {'source': 'constant', 'value': 'Y'},
                                  'SelectionName': {'source': 'constant', 'value': 'Z'},
                                  'BetType': {'source': 'constant', 'value': 'W'}}})
    cfg_b = _cfg_web(CONFIG_WEB)

    t = time.monotonic()
    esito_a = main.elabora_messaggio(SOGGETTO_CATTIVO, cfg_a)
    esito_b = main.elabora_messaggio('SEGNALE\nEvento: Roma v Lazio', cfg_b)
    trascorso = time.monotonic() - t

    assert esito_a is None, 'il parser con regex catastrofica non doveva produrre segnale'
    assert esito_b is not None and esito_b['event'] == 'Roma v Lazio', (
        'il parser sano del cliente B non ha prodotto il suo segnale: A lo ha bloccato')
    assert trascorso < TETTO_S, (
        f'A e B insieme hanno impiegato {trascorso:.2f}s: la regex malata di A ha '
        f'rubato il worker a B, cioe- ha «bloccato a tutti»')


# --------------------------------------------------- end-to-end: config_json → feed

@pytest.fixture
def db_isolato(tmp_path, monkeypatch):
    """Un DB SQLite per test, isolato: `DB_PATH` puntato in `tmp_path`.

    `migra()` gira una volta per percorso (`_PERCORSI_MIGRATI`): si toglie il percorso
    nuovo dall'insieme cosi' la migrazione lo popola con PIERO, come in produzione.
    """
    p = str(tmp_path / 'signals.db')
    monkeypatch.setattr(main, 'DB_PATH', p)
    main._PERCORSI_MIGRATI.discard(p)
    c = main.db()
    yield c
    c.close()


def test_un_parser_config_json_scrive_un_feed_valido(db_isolato):
    """End-to-end del percorso nuovo: parser `config_json` → `store_signal` → feed.

    Si semina un parser con `config_json` e un profilo, si elabora un messaggio e si
    scrive con `store_signal` (lo stesso che usa il webhook), poi si rilegge la riga e
    la si valida col verificatore del contratto. E' il collegamento del motore al
    percorso del feed, senza il livello HTTP — quello, per PIERO, e' gia' coperto da
    `test_webhook.py` e non cambia.
    """
    c = db_isolato
    c.execute('INSERT INTO parsers(name, header, config_json) VALUES (?,?,?)',
              ('web1', 'SEGNALE', json.dumps(CONFIG_WEB)))
    c.execute('INSERT INTO profiles(name, chat_ids, parser) VALUES (?,?,?)',
              ('CLIENTE1', '-100777', 'web1'))
    c.commit()

    cfg = main.get_parser(c, 'web1')
    parsed = main.elabora_messaggio('SEGNALE\nEvento: Roma v Lazio\n@ 2.10', cfg)
    assert parsed is not None, 'il parser config_json non ha prodotto segnale'

    main.store_signal(c, parsed['csv'], 'web1', 'CLIENTE1')
    c.commit()

    r = c.execute('SELECT csv FROM signals WHERE profile=?', ('CLIENTE1',)).fetchone()
    assert r is not None, 'nessun segnale scritto nel feed del cliente'
    corpo = main.verify_csv(r[0])   # solleva se il contratto e' rotto
    assert '"Roma v Lazio"' in corpo
    # E il feed di PIERO, nello stesso DB, non e' stato toccato dal parser del cliente.
    piero = c.execute('SELECT csv FROM signals WHERE profile=?', ('PIERO',)).fetchone()
    assert piero is None, 'il parser del cliente ha scritto nel feed di PIERO: isolamento rotto'
