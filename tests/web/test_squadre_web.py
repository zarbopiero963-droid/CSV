"""Avvia il relay e pilota le sorgenti squadre (#34, pezzo 2) in un browser vero.

Lo script accanto (`squadre_flow.py`) fa il lavoro; qui si tira su il servizio
con la porta a password, come `test_mercati_web.py` — l'avvio passa da
`tests.servizio`, fonte unica, e le credenziali da `credenziali_prova.py`.
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
    with relay_avviato(tmp_path_factory.mktemp('squadre-web'), **ambiente) as base:
        yield base + '/app/'


def test_sorgenti_squadre_dal_vuoto_alla_tabella_alias(base_url, tmp_path):
    """Competizione → squadre Betfair → sorgenti → alias, in browser."""
    proc = subprocess.run(  # noqa: S603 - comando fisso, nessun input esterno
        [sys.executable, str(Path(__file__).with_name('squadre_flow.py')),
         base_url, str(tmp_path)],
        cwd=RADICE, capture_output=True, text=True, timeout=300,
        env=ambiente_di_supporto(PYTHONUNBUFFERED='1'),
    )
    if proc.returncode != 0:
        raise AssertionError(
            f'squadre_flow.py fallito (exit {proc.returncode})\n'
            f'--- stdout ---\n{proc.stdout[-4000:]}\n'
            f'--- stderr ---\n{proc.stderr[-4000:]}'
        )
