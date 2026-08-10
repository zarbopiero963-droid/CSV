"""Avvia il servizio e pilota il prototipo con un browser vero.

I due script accanto (`prototype_flow.py`, `mobile_layout.py`) fanno il lavoro;
qui si tira su `uvicorn` su una porta libera, si aspetta `/health` e si eseguono.

Si saltano con motivo scritto se Playwright o Chromium non ci sono: CLAUDE.md
vieta di dichiarare coperto un comportamento che non e' stato eseguito, e un
test che finge di passare senza browser sarebbe esattamente quello.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[2]
CHROMIUM = Path('/opt/pw-browsers/chromium-1194/chrome-linux/chrome')

playwright = pytest.importorskip('playwright', reason='playwright non installato')

pytestmark = pytest.mark.skipif(
    not CHROMIUM.is_file(),
    reason=f'Chromium non presente in {CHROMIUM}: il flusso browser non e\' eseguibile',
)


def _porta_libera() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@pytest.fixture(scope='module')
def base_url():
    porta = _porta_libera()
    proc = subprocess.Popen(
        [sys.executable, '-m', 'uvicorn', 'main:app', '--host', '127.0.0.1',
         '--port', str(porta), '--log-level', 'warning'],
        cwd=RADICE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    url = f'http://127.0.0.1:{porta}'
    try:
        scaduto = time.monotonic() + 30
        while time.monotonic() < scaduto:
            if proc.poll() is not None:
                pytest.fail(f'uvicorn e\' morto durante l\'avvio:\n{proc.stdout.read()[-2000:]}')
            try:
                with urllib.request.urlopen(f'{url}/health', timeout=1) as r:
                    if r.status == 200:
                        break
            except (urllib.error.URLError, OSError):
                time.sleep(0.2)
        else:
            pytest.fail('uvicorn non ha risposto su /health entro 30 s')
        yield f'{url}/app/'
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def _esegui(script: str, url: str, tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).with_name(script)), url, str(tmp_path)],
        cwd=RADICE, capture_output=True, text=True, timeout=300,
        env={**os.environ, 'PYTHONUNBUFFERED': '1'},
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
