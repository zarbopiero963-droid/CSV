"""Avvia il relay e apre la facciata con un browser vero.

Lo script accanto (`sito_flow.py`) fa il lavoro; qui si tira su `uvicorn` e si
esegue, con la stessa struttura di `test_prototype_flow.py` — l'avvio passa da
`tests.servizio`, fonte unica.

Si salta con motivo scritto se Playwright o Chromium non ci sono: CLAUDE.md vieta
di dichiarare coperto un comportamento che non e' stato eseguito.

La differenza dall'altro file: qui la base e' l'**apex**, non `/app/`. E' il
punto: la facciata deve funzionare dove la gente arriva scrivendo il dominio.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE))

from tests.ambiente import ambiente_di_supporto  # noqa: E402
from tests.runtime import esigi_browser  # noqa: E402
from tests.servizio import relay_avviato  # noqa: E402

# Playwright e Chromium: se mancano si salta con motivo scritto, MA in CI la
# variabile TEST_RUNTIME_OBBLIGATORIO trasforma lo skip in un fallimento. Una CI
# che salta i test browser esce verde senza averli eseguiti, ed e' la stessa classe
# del check verde senza review chiusa dalla PR #16. La decisione vive in
# `tests/runtime.py`, in un punto solo.
esigi_browser()


@pytest.fixture(scope='module')
def apex(tmp_path_factory):
    """L'apex del relay vero. Nessun token: la facciata e' pubblica per progetto."""
    with relay_avviato(tmp_path_factory.mktemp('sito')) as base:
        yield base


def test_la_facciata_si_apre_su_telefono_e_su_scrivania(apex, tmp_path):
    """Zero errori in console, zero risorse fallite, nessuno scorrimento, CTA cliccata.

    Gli screenshot restano in `tmp_path`: servono a guardare la pagina, non solo a
    leggere che il test e' verde.
    """
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).with_name('sito_flow.py')), apex, str(tmp_path)],
        cwd=RADICE, capture_output=True, text=True, timeout=300,
        env=ambiente_di_supporto(PYTHONUNBUFFERED='1'),
    )
    if proc.returncode != 0:
        raise AssertionError(
            f'sito_flow.py fallito (exit {proc.returncode})\n'
            f'--- stdout ---\n{proc.stdout[-4000:]}\n'
            f'--- stderr ---\n{proc.stderr[-4000:]}'
        )
