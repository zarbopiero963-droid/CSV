"""`GET /api/settings`: i valori pubblici che la pagina di login deve conoscere (#32).

La pagina di login del prototipo reale ha bisogno di due cose PRIMA di avere una
sessione: lo username del bot (per il link «Accedi con Telegram» in modalita'
redirect di oauth.telegram.org, senza script esterni — la regola di CLAUDE.md
vieta i CDN) e il `bot_id` numerico che quella modalita' richiede. Il `bot_id`
e' il prefisso del token PRIMA dei due punti: e' pubblico per costruzione
(compare in ogni embed del widget), il token no — e non deve MAI uscire.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE))

import main  # noqa: E402 - dopo l'inserimento del percorso
from tests.ambiente import CHIAVI_PERICOLOSE, TOKEN_DI_PROVA  # noqa: E402

BOT_FINTO = '123456789:AAFinto-token-che-non-deve-mai-uscire'


@pytest.fixture(autouse=True)
def _ambiente_pulito(monkeypatch):
    for chiave in CHIAVI_PERICOLOSE:
        monkeypatch.delenv(chiave, raising=False)
    monkeypatch.setattr(main, 'TOKEN', TOKEN_DI_PROVA)


def test_le_impostazioni_pubbliche_esistono_e_non_contengono_il_token(monkeypatch):
    """Fail-first della #32 (3.3a): oggi la rotta non esiste (404)."""
    monkeypatch.setattr(main, 'BOT_TOKEN', BOT_FINTO)
    monkeypatch.setattr(main, 'TELEGRAM_BOT_USERNAME', 'BetRelayBot')
    corpo = main.impostazioni_pubbliche()
    assert corpo['bot_username'] == 'BetRelayBot'
    assert corpo['bot_id'] == '123456789'
    assert corpo['base_url'].startswith('http')
    testo = str(corpo)
    assert 'AAFinto' not in testo, 'il token del bot e\' uscito dalle impostazioni'


def test_senza_bot_configurato_niente_500(monkeypatch):
    """Deploy senza bot: la pagina di login deve poter offrire la sola password."""
    monkeypatch.setattr(main, 'BOT_TOKEN', '')
    monkeypatch.setattr(main, 'TELEGRAM_BOT_USERNAME', '')
    corpo = main.impostazioni_pubbliche()
    assert corpo['bot_id'] is None
    assert corpo['bot_username'] == ''
