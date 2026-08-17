"""La demo delle sorgenti squadre davanti a un localStorage vecchio o rotto.

`web/api_finta.js` non e' importabile da Python: i casi girano in node sul file
reale (`api_finta_squadre.mjs`, con un localStorage finto seminato con una
competizione SENZA l'array `squadre`) e qui vengono asseriti uno per uno.

E' il vincolo della normalizzazione a fonte unica in `_competizioniDemo()`:
prima la guardia `|| []` viveva solo dentro `deleteSport()` e ogni altro
consumatore di `k.squadre` moriva di TypeError — misurato rosso 6/7 prima
della patch (Fable e GPT-5.5, PR #66; regole 2 e 3).
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

CASI_JS = Path(__file__).with_name('api_finta_squadre.mjs')


@pytest.fixture(scope='module')
def esiti() -> list[dict]:
    proc = subprocess.run(
        [esigi_node(), str(CASI_JS)],
        cwd=RADICE, capture_output=True, text=True, timeout=60,
    )
    if not proc.stdout.strip():
        raise AssertionError(
            f'api_finta_squadre.mjs non ha prodotto output.\n'
            f'exit={proc.returncode}\nstderr:\n{proc.stderr[-2000:]}'
        )
    return json.loads(proc.stdout)


def test_i_casi_girano_e_ne_esistono_abbastanza(esiti):
    assert len(esiti) >= 7, f'attesi almeno 7 casi, trovati {len(esiti)}'


def test_nessun_consumatore_muore_sul_localStorage_rotto(esiti):
    falliti = [c for c in esiti if not c['ok']]
    if falliti:
        righe = '\n'.join(f'  - {c["nome"]}: {c.get("errore")}' for c in falliti)
        raise AssertionError(f'{len(falliti)} casi della demo falliti:\n{righe}')
