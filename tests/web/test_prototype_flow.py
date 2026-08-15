"""Avvia il servizio e pilota la web app REALE con un browser vero (#32).

I due script accanto (`prototype_flow.py`, `mobile_layout.py`) fanno il lavoro;
qui si tira su `uvicorn` su una porta libera, si aspetta `/health` e si eseguono.

Dalla PR dell'aggancio la pagina su `/app` parla col backend: la fixture deve
quindi dare al relay cio' che serve a un login vero — `TELEGRAM_BOT_TOKEN`
finto (da cui deriva `SEGRETO_SESSIONE`: senza, nessun cookie viene firmato) e
`ADMIN_PASSWORD_HASH` calcolato dalle credenziali di `credenziali_prova.py`.
Il proxy morto e `PUBLIC_URL` inesistente sono la stessa cinghia di sicurezza
di `tests/relay/test_login.py`: con un bot token nell'ambiente lo startup
proverebbe una `setWebhook` VERA, e un test non deve toccare il bot di nessuno.

Si saltano con motivo scritto se Playwright o Chromium non ci sono: CLAUDE.md
vieta di dichiarare coperto un comportamento che non e' stato eseguito, e un
test che finge di passare senza browser sarebbe esattamente quello.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE))

import main  # noqa: E402 - per hash_password, dopo l'inserimento del percorso
from tests.ambiente import ambiente_di_supporto  # noqa: E402
from tests.relay.test_login import AMBIENTE_DEL_SERVIZIO  # noqa: E402
from tests.runtime import esigi_browser  # noqa: E402
from tests.servizio import relay_avviato  # noqa: E402
from tests.web.credenziali_prova import PASSWORD_PROVA  # noqa: E402

# Playwright e Chromium: se mancano si salta con motivo scritto, MA in CI la
# variabile TEST_RUNTIME_OBBLIGATORIO trasforma lo skip in un fallimento. Una CI
# che salta i test browser esce verde senza averli eseguiti, ed e' la stessa classe
# del check verde senza review chiusa dalla PR #16. La decisione vive in
# `tests/runtime.py`, in un punto solo.
esigi_browser()


@pytest.fixture(scope='module')
def base_url(tmp_path_factory):
    """La web app servita da `/app`, dal relay vero, con le due porte di login.

    `AMBIENTE_DEL_SERVIZIO` viene da `tests/relay/test_login.py` (fonte unica,
    regola 3): bot token finto, admin id finto, proxy morto. Qui si aggiunge
    solo l'hash della password di prova, calcolato con la STESSA
    `hash_password` del servizio — un formato ricopiato a mano divergerebbe
    al primo cambio di algoritmo.
    """
    ambiente = dict(AMBIENTE_DEL_SERVIZIO,
                    ADMIN_PASSWORD_HASH=main.hash_password(PASSWORD_PROVA))
    with relay_avviato(tmp_path_factory.mktemp('web'), **ambiente) as base:
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


def test_flusso_completo_della_web_app(base_url, tmp_path):
    """Login a password, dashboard, wizard, prova sul server, persistenza al
    reload, token una-volta-sola, feed letto via HTTP col token vero, logout.

    Copre anche la regressione del gestore delle trasformazioni in modalita' regex:
    la PRIMA attivazione veniva scritta su una copia orfana della regola e persa.
    """
    _esegui('prototype_flow.py', base_url, tmp_path)


def test_nessuno_scorrimento_orizzontale_su_mobile(base_url, tmp_path):
    _esegui('mobile_layout.py', base_url, tmp_path)
