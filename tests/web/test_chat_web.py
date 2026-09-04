"""Avvia il relay e pilota la vista «Chat Telegram» (#32, 3.2) in un browser vero.

Lo script accanto (`chat_flow.py`) fa il lavoro; qui si tira su il servizio con la
porta a password, come `test_mercati_web.py`. Il `TELEGRAM_BOT_TOKEN` non e' un
dettaglio della fixture: da quello il servizio deriva il segreto del webhook, e il
flusso consegna il codice di verifica con quel segreto — e' cio' che rende il giro
un end-to-end senza stub.
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
    """L'URL della web app, piu' il percorso del database accanto.

    Il database serve per UNA cosa sola, dichiarata qui perche' non sembri una
    scorciatoia: il flusso deve provocare un rifiuto del codice, e l'unico modo e'
    che esista una chat di un ALTRO utente. Un secondo utente non e' raggiungibile
    dal browser — la fixture ha una sola porta a password — quindi la riga si
    scrive direttamente, come fanno i test di `tests/relay/`.
    """
    # `TELEGRAM_BOT_USERNAME` serve dal #116: la vista costruisce il link da
    # copiare per aggiungere il bot al canale, e senza username mostrerebbe il
    # ripiego «Nessun bot configurato» — cioe' il percorso principale non
    # sarebbe misurato da nessuna parte.
    ambiente = dict(TELEGRAM_BOT_TOKEN='123456789:AAFinto',
                    TELEGRAM_BOT_USERNAME='BetrelayProvaBot',
                    ADMIN_PASSWORD_HASH=main.hash_password(PASSWORD_PROVA))
    cartella = tmp_path_factory.mktemp('chat-web')
    with relay_avviato(cartella, **ambiente) as base:
        yield base + '/app/', str(cartella / 'signals.db')


def test_dal_codice_alla_chat_collegata_al_parser(base_url, tmp_path):
    """Codice → incollato nel canale → chat verificata → collegata → eliminata."""
    url, percorso_db = base_url
    proc = subprocess.run(  # noqa: S603 - comando fisso, nessun input esterno
        [sys.executable, str(Path(__file__).with_name('chat_flow.py')),
         url, str(tmp_path), percorso_db],
        cwd=RADICE, capture_output=True, text=True, timeout=300,
        env=ambiente_di_supporto(PYTHONUNBUFFERED='1'),
    )
    if proc.returncode != 0:
        raise AssertionError(
            f'chat_flow.py fallito (exit {proc.returncode})\n'
            f'--- stdout ---\n{proc.stdout[-4000:]}\n'
            f'--- stderr ---\n{proc.stderr[-4000:]}'
        )
