"""Avvia il servizio e pilota il prototipo con un browser vero.

I due script accanto (`prototype_flow.py`, `mobile_layout.py`) fanno il lavoro;
qui si tira su `uvicorn` su una porta libera, si aspetta `/health` e si eseguono.

Si saltano con motivo scritto se Playwright o Chromium non ci sono: CLAUDE.md
vieta di dichiarare coperto un comportamento che non e' stato eseguito, e un
test che finge di passare senza browser sarebbe esattamente quello.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
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
def base_url(tmp_path_factory):
    """Il prototipo servito da `/app`, dal relay vero.

    L'avvio passa da `tests.servizio` come le altre due fixture. Qui NON serve
    `CSV_ACCESS_TOKEN`: `/app` e- pubblico per progetto — sono file statici — e
    questi test non toccano nessuna rotta protetta.
    """
    with relay_avviato(tmp_path_factory.mktemp('web')) as base:
        yield f'{base}/app/'


def _esegui(script: str, url: str, tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).with_name(script)), url, str(tmp_path)],
        cwd=RADICE, capture_output=True, text=True, timeout=300,
        env=ambiente_di_supporto(PYTHONUNBUFFERED='1'),
    )
    if proc.returncode != 0:
        raise AssertionError(
            f'{script} fallito (exit {proc.returncode})\n'
            f'--- stdout ---\n{proc.stdout[-4000:]}\n'
            f'--- stderr ---\n{proc.stderr[-4000:]}'
        )


def test_flusso_completo_del_prototipo(base_url, tmp_path):
    """Login, wizard, mappatura, prova messaggio, token, verifica chat, log.

    Copre anche la regressione del gestore delle trasformazioni in modalita' regex:
    la PRIMA attivazione veniva scritta su una copia orfana della regola e persa.
    """
    _esegui('prototype_flow.py', base_url, tmp_path)


def test_nessuno_scorrimento_orizzontale_su_mobile(base_url, tmp_path):
    _esegui('mobile_layout.py', base_url, tmp_path)
