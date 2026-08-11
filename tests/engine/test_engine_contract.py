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
import shutil
import subprocess
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[2]
CASI_JS = Path(__file__).with_name('engine_cases.mjs')

NODE = shutil.which('node')


def _esegui_casi() -> list[dict]:
    proc = subprocess.run(
        [NODE, str(CASI_JS)],
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
    if NODE is None:
        pytest.skip('node non disponibile in questo ambiente: il motore JS non e\' eseguibile')
    return _esegui_casi()


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
    assert esportato and esportato['ok'], 'il caso che esporta l\'intestazione non e\' passato'
    dal_js = esportato['dettaglio']

    assert dal_js['bom'] == 0xfeff, f'il BOM del motore JS non e\' U+FEFF: {dal_js["bom"]:#x}'
    assert ord(main.CSV_BOM) == 0xfeff, f'il BOM del relay non e\' U+FEFF: {ord(main.CSV_BOM):#x}'
    assert dal_js['intestazione'] == main.HEADER_LINE, (
        'le due implementazioni scrivono intestazioni diverse:\n'
        f'  JS     : {dal_js["intestazione"]}\n'
        f'  Python : {main.HEADER_LINE}'
    )
