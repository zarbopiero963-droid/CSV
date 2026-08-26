"""Avvia il relay e pilota il GIRO COMPLETO del cliente in un browser vero.

Lo script accanto (`giro_flow.py`) fa il lavoro — dalla libreria mercati alla
sorgente squadre al parser, fino alla prova sul server e al token del feed —
esattamente come `test_mercati_web.py` fa con `mercati_flow.py`: l'avvio del
servizio passa da `tests.servizio` (fonte unica) e le credenziali da
`credenziali_prova.py`.

E' insieme una regressione end-to-end del percorso di onboarding e la fonte
degli screenshot della guida «Il giro completo»: gli stessi passi, la stessa
prova sul server. Headless in CI; per la galleria a browser vero si lancia
`giro_flow.py` con `GIRO_HEADED=1` sotto `xvfb-run`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE))

import main  # noqa: E402 - dopo l'inserimento del percorso
from tests.ambiente import ambiente_di_supporto  # noqa: E402
from tests.runtime import esigi_browser  # noqa: E402
from tests.servizio import relay_avviato  # noqa: E402
from tests.web.credenziali_prova import PASSWORD_PROVA  # noqa: E402

esigi_browser()


@pytest.fixture(scope='module')
def base_url(tmp_path_factory):
    ambiente = dict(TELEGRAM_BOT_TOKEN='123456789:AAFinto',
                    ADMIN_PASSWORD_HASH=main.hash_password(PASSWORD_PROVA))
    with relay_avviato(tmp_path_factory.mktemp('giro-web'), **ambiente) as base:
        yield base + '/app/'


def test_il_giro_completo_dal_mercato_al_token(base_url, tmp_path):
    """Mercati -> sorgenti squadre -> parser -> prova sul server -> token,
    in browser, con zero errori in console e la traduzione degli alias
    verificata nell'output del server."""
    proc = subprocess.run(  # noqa: S603 - comando fisso, nessun input esterno
        [sys.executable, str(Path(__file__).with_name('giro_flow.py')),
         base_url, str(tmp_path)],
        cwd=RADICE, capture_output=True, text=True, timeout=300,
        env=ambiente_di_supporto(PYTHONUNBUFFERED='1'),
    )
    if proc.returncode != 0:
        raise AssertionError(
            f'giro_flow.py fallito (exit {proc.returncode})\n'
            f'--- stdout ---\n{proc.stdout[-4000:]}\n'
            f'--- stderr ---\n{proc.stderr[-4000:]}'
        )
