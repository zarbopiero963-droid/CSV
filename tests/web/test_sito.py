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
from tests.servizio import relay_avviato  # noqa: E402

CHROMIUM = Path('/opt/pw-browsers/chromium-1194/chrome-linux/chrome')

playwright = pytest.importorskip('playwright', reason='playwright non installato')

pytestmark = pytest.mark.skipif(
    not CHROMIUM.is_file(),
    reason=f'Chromium non presente in {CHROMIUM}: il flusso browser non e\' eseguibile',
)


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
