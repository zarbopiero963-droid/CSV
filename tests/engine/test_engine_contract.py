"""Test hard del motore di parsing e del contratto CSV verso XTrader.

Il motore vive in JavaScript (`web/engine.js`) e non e' importabile da Python:
i casi girano in node su quel file reale (`engine_cases.mjs`) e qui vengono
asseriti uno per uno, cosi' un fallimento nomina il caso invece di dare un
generico exit code diverso da zero.

Se node non e' disponibile i test si saltano con motivo scritto, come prescrive
CLAUDE.md: mai dichiarare coperto un comportamento non eseguito.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE))

from tests.runtime import esigi_node  # noqa: E402

CASI_JS = Path(__file__).with_name('engine_cases.mjs')

def _esegui_casi(node: str) -> list[dict]:
    """Esegue i casi con l'interprete che `esigi_node()` ha trovato.

    Il percorso arriva come ARGOMENTO e non da una costante di modulo. Prima
    c'erano due fonti — `NODE = shutil.which('node')` all'import piu' il gate
    `esigi_node()` — e il valore restituito dal gate veniva scartato: due
    risoluzioni della stessa cosa, che e' la duplicazione vietata dalla regola 3 e
    che avrebbe potuto divergere (per esempio con un PATH modificato da una
    fixture). Segnalato da Claude Fable 5 sulla PR #18.
    """
    proc = subprocess.run(
        [node, str(CASI_JS)],
        cwd=RADICE, capture_output=True, text=True, timeout=60,
    )
    if not proc.stdout.strip():
        raise AssertionError(
            f'engine_cases.mjs non ha prodotto output.\n'
            f'exit={proc.returncode}\nstderr:\n{proc.stderr[-2000:]}'
        )
    return json.loads(proc.stdout)


@pytest.fixture(scope='module')
def casi() -> list[dict]:
    return _esegui_casi(esigi_node())


def test_i_casi_girano_e_ne_esistono_abbastanza(casi):
    assert len(casi) >= 18, f'attesi almeno 18 casi, trovati {len(casi)}'


def test_nessun_caso_fallito(casi):
    falliti = [c for c in casi if not c['ok']]
    if falliti:
        righe = '\n'.join(f'  - {c["nome"]}: {c.get("errore")}' for c in falliti)
        raise AssertionError(f'{len(falliti)} casi del motore falliti:\n{righe}')


# Un test per caso: cosi' pytest -q elenca esattamente cosa si e' rotto.
def test_ogni_caso_singolarmente(casi, subtests=None):
    for c in casi:
        assert c['ok'], f'{c["nome"]}: {c.get("errore")}'


def test_le_due_implementazioni_RIFIUTANO_lo_stesso_evento_vuoto(casi):
    """Regola 3 sul caso corretto in `main.py` il 12/08/2026.

    Il motore JS era gia' giusto: `EventName` sta fra le colonne obbligatorie e
    `missing` si calcola dopo il `trim`, quindi un evento vuoto da' `complete: false`
    e nessuna riga. Il relay no: `''.splitlines()[0]` sollevava `IndexError`, cioe'
    un 500 sul webhook pubblico e Telegram che riconsegna lo stesso messaggio.

    Le due implementazioni erano quindi **divergenti su un ingresso reale**, e
    nessun test lo vedeva perche' ciascuna passava i propri. Questo confronto e' il
    posto dove quella divergenza diventa rossa: gli stessi quattro messaggi, e
    l'unica cosa che si pretende e' che entrambe rifiutino.

    Non si confrontano i valori di ritorno — uno e' `{complete, missing}` e l'altro
    e' `None` — ma la DECISIONE, che e' l'unica cosa che il contratto vincola.
    """
    import importlib
    import sys
    sys.path.insert(0, str(RADICE))
    main = importlib.import_module('main')

    esportato = next((c for c in casi if 'marcatore senza evento' in c['nome']), None)
    assert esportato and esportato['ok'], \
        'il caso JS sull\'evento vuoto non e\' passato: ' + str(esportato and esportato.get('errore'))

    # La stessa config del caso JS, nella forma che `parse_message` si aspetta.
    cfg = {'name': 'confronto', 'header': 'P.Bet. PREMACHT 0,5HT',
           'market_name': 'Over/Under 1,5 gol', 'market_type': 'OVER_UNDER_15',
           'selection_name': 'Over 1,5 goal', 'handicap': '0', 'bet_type': 'PUNTA'}

    divergenti = []
    for esito in esportato['dettaglio']:
        messaggio = esito['messaggio']
        js_rifiuta = esito['complete'] is False
        try:
            py_rifiuta = main.parse_message(messaggio, cfg) is None
        except Exception as e:  # noqa: BLE001 - un'eccezione E' la divergenza in prova
            divergenti.append(f'{messaggio!r}: JS rifiuta, Python SOLLEVA {type(e).__name__}')
            continue
        if js_rifiuta != py_rifiuta:
            divergenti.append(
                f'{messaggio!r}: JS rifiuta={js_rifiuta}, Python rifiuta={py_rifiuta}')

    assert not divergenti, (
        'le due implementazioni dello stesso contratto non concordano:\n'
        + '\n'.join(f'  - {r}' for r in divergenti)
    )


def test_il_motore_js_e_il_relay_producono_lo_STESSO_formato(casi):
    """Guardiano della regola 3: due implementazioni, un contratto.

    Il motore vive in JavaScript e il relay in Python. Finche' sono due, il
    rischio non e' che una sia sbagliata — e' che divergano, e che la divergenza
    resti invisibile perche' ciascuna passa i propri test. Qui l'intestazione e
    il BOM prodotti da `web/engine.js` vengono confrontati con quelli di
    `main.py`: se qualcuno cambia il formato in un posto solo, questo diventa
    rosso.
    """
    import importlib
    import sys
    sys.path.insert(0, str(RADICE))
    main = importlib.import_module('main')

    esportato = next((c for c in casi if 'confronto col motore Python' in c['nome']), None)
    assert esportato and esportato['ok'], 'il caso che esporta il CSV non e\' passato'
    dal_js = esportato['dettaglio']

    assert dal_js['bom'] == 0xfeff, f'il BOM del motore JS non e\' U+FEFF: {dal_js["bom"]:#x}'
    assert ord(main.CSV_BOM) == 0xfeff, f'il BOM del relay non e\' U+FEFF: {ord(main.CSV_BOM):#x}'

    # La stessa riga costruita in Python: campi vuoti, una virgola e una
    # virgoletta da raddoppiare. Confrontare solo l'intestazione non vedrebbe
    # una divergenza nel quoting — segnalato da CodeRabbit.
    riga = [''] * len(main.HEADERS)
    riga[main.HEADERS.index('Provider')] = 'XTrader'
    riga[main.HEADERS.index('EventName')] = 'Squadra "A", Citta - Altra'
    riga[main.HEADERS.index('BetType')] = 'PUNTA'

    assert dal_js['soloIntestazione'] == main.empty_csv(), (
        'feed vuoto diverso fra le due implementazioni:\n'
        f'  JS     : {dal_js["soloIntestazione"]!r}\n'
        f'  Python : {main.empty_csv()!r}'
    )
    assert dal_js['csvCompleto'] == main.make_csv(riga), (
        'CSV completo diverso fra le due implementazioni:\n'
        f'  JS     : {dal_js["csvCompleto"]!r}\n'
        f'  Python : {main.make_csv(riga)!r}'
    )


def test_i_due_motori_producono_lo_STESSO_runParser(casi):
    """Regola 3, la forma piena: `esegui_parser` (Python) == `runParser` (JS).

    I casi vivono in `engine_cases.mjs` — definiti UNA volta, con l'output JS come
    ORACOLO — e qui ciascun `(messaggio, config)` gira nel motore Python. Si
    confronta l'intero risultato: `matched`, `row` (le 14 colonne), `missing`,
    `complete`. Un solo campo diverso su un solo caso nomina il caso e la colonna,
    invece di un generico «i motori divergono».

    E' il guardiano che la Issue #2 chiede per il PR 10: senza, le due
    implementazioni dello stesso contratto si allontanano al primo `str.replace`
    che in Python cambia tutte le occorrenze e in JS solo la prima.
    """
    import importlib
    import sys
    sys.path.insert(0, str(RADICE))
    main = importlib.import_module('main')

    esportato = next((c for c in casi if 'gemello Python' in c['nome']), None)
    assert esportato and esportato['ok'], \
        'il caso JS di confronto non e\' passato: ' + str(esportato and esportato.get('errore'))

    divergenti = []
    for caso_confronto in esportato['dettaglio']:
        nome = caso_confronto['nome']
        atteso = caso_confronto['atteso']
        try:
            ottenuto = main.esegui_parser(caso_confronto['message'], caso_confronto['config'])
        except Exception as e:  # noqa: BLE001 - un'eccezione E' una divergenza
            divergenti.append(f'{nome}: Python SOLLEVA {type(e).__name__}: {e}')
            continue
        # `scarti` sta fra i campi confrontati, e non e' un di piu': e' il MOTIVO
        # mostrato all'utente. Due motori che scartano lo stesso messaggio per
        # ragioni diverse manderebbero il cliente su due piste diverse — e' il
        # difetto del Bridge che la #39 ha deciso di non ereditare.
        for campo in ('matched', 'row', 'missing', 'scarti', 'complete'):
            if ottenuto[campo] != atteso[campo]:
                divergenti.append(
                    f'{nome} · {campo}: JS={atteso[campo]!r} Python={ottenuto[campo]!r}')

    assert not divergenti, (
        'il motore Python diverge da web/engine.js:\n'
        + '\n'.join(f'  - {r}' for r in divergenti)
    )


def test_l_ORACOLO_non_arriva_troncato(casi):
    """L'output di node deve arrivare INTERO, e la soglia va superata davvero.

    `engine_cases.mjs` chiudeva con `process.exit()`: su una pipe la scrittura di
    stdout e' asincrona, e `exit()` scarta cio' che non e' ancora stato
    scaricato. L'output arrivava troncato a **esattamente 65536 byte** e il
    wrapper riceveva un JSON tagliato a meta'. Il difetto era latente finche' i
    casi stavano sotto i 64 KiB: invisibile fino al giro in cui il payload
    cresce, e allora il sintomo — `JSONDecodeError` su una riga qualunque — non
    somiglia alla causa.

    Questo test e' il filo teso: pretende che il payload SUPERI la soglia, cosi'
    il caso resta esercitato. Se un giorno i casi dimagriscono sotto i 64 KiB,
    questo test diventa rosso e chiede di ripensarlo invece di lasciare la
    protezione a scadere in silenzio.
    """
    proc = subprocess.run(
        [esigi_node(), str(CASI_JS)],
        cwd=RADICE, capture_output=True, text=True, timeout=60,
    )
    grezzo = proc.stdout
    # I BYTE VERI che node ha scritto, non il JSON ricompattato: il troncamento
    # avviene sull'uscita, e misurare una rappresentazione diversa misurerebbe
    # un'altra cosa. (Prima versione di questo test: `json.dumps(casi)`, che
    # perde l'indentazione e stava sotto la soglia — il filo non toccava niente.)
    assert len(grezzo.encode('utf-8')) > 65536, (
        f'l\'oracolo scrive {len(grezzo.encode("utf-8"))} byte: sotto i 64 KiB la '
        'protezione contro il troncamento di `process.exit()` non e\' piu\' '
        'esercitata da nessun test'
    )
    json.loads(grezzo)  # e deve essere JSON INTERO, non tagliato a meta'


def test_ci_sono_abbastanza_casi_di_confronto(casi):
    """Il guardiano non deve svuotarsi: se i casi di confronto sparissero, il test
    sopra passerebbe a vuoto. Qui si pretende che ce ne siano abbastanza."""
    esportato = next((c for c in casi if 'gemello Python' in c['nome']), None)
    assert esportato and esportato['ok'], 'caso di confronto assente o fallito'
    assert len(esportato['dettaglio']) >= 10, (
        f'solo {len(esportato["dettaglio"])} casi di confronto: il guardiano copre troppo poco')


def test_le_QUATTRO_obbligatorie_in_Python_sono_verbatim():
    """La lista Python, verbatim, decisa dal proprietario (Issue #2/#25).

    Il confronto JS/Python lega i RISULTATI (`missing`/`complete`): se le due liste
    fossero sbagliate nello STESSO modo, quel confronto passerebbe comunque. Questo
    caso invece pinna la lista Python parola per parola, e il caso gemello in
    `engine_cases.mjs` pinna quella JS: insieme, una divergenza fra le due — o una
    deriva di entrambe dalla decisione — diventa rossa. Chiesto da Claude Fable 5
    sulla PR #28.
    """
    import importlib
    import sys
    sys.path.insert(0, str(RADICE))
    main = importlib.import_module('main')
    assert main.COLONNE_OBBLIGATORIE == ['EventName', 'MarketType', 'SelectionName', 'BetType'], (
        f'COLONNE_OBBLIGATORIE e- {main.COLONNE_OBBLIGATORIE}, non le quattro decise su #2/#25')
    # Provider e Price NON sono obbligatorie: pretenderle bloccherebbe segnali validi.
    assert 'Provider' not in main.COLONNE_OBBLIGATORIE
    assert 'Price' not in main.COLONNE_OBBLIGATORIE
