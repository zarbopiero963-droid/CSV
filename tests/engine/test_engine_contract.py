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
