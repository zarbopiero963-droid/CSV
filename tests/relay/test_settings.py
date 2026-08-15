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


def test_una_PUBLIC_URL_vuota_vale_come_assente(monkeypatch):
    """`os.getenv(chiave, default)` NON usa il default se la variabile esiste vuota.

    Una `PUBLIC_URL` presente ma vuota (o di soli spazi) usciva come `base_url`
    inservibile, e il flusso redirect di Telegram si rompeva senza errore.
    Fail-first del finding di CodeRabbit sulla PR #50; la stessa espressione
    viveva anche in `assicura_registrazione` — regola 2, corretta la classe con
    la fonte unica `public_url()`.
    """
    for valore in ('', '   '):
        monkeypatch.setenv('PUBLIC_URL', valore)
        corpo = main.impostazioni_pubbliche()
        assert corpo['base_url'].startswith('https://'), (
            f'PUBLIC_URL={valore!r} e\' uscita come base_url {corpo["base_url"]!r}')
    monkeypatch.setenv('PUBLIC_URL', 'https://esempio.invalid')
    assert main.impostazioni_pubbliche()['base_url'] == 'https://esempio.invalid'


def test_la_rotta_risponde_davvero_via_http(tmp_path):
    """La rotta VERA, senza autenticazione: registrazione, serializzazione, 200.

    I test qui sopra chiamano la funzione in processo — veloci, ma ciechi su
    rotta e serializzazione (segnalato da CodeRabbit sulla PR #50). Questo
    passa dal servizio in sottoprocesso e asserisce i byte della risposta.
    """
    import json as _json
    import urllib.request

    from tests.relay.test_login import AMBIENTE_DEL_SERVIZIO
    from tests.relay.test_login import BOT_FINTO as BOT_DEL_SERVIZIO
    from tests.servizio import relay_avviato

    with relay_avviato(tmp_path, **AMBIENTE_DEL_SERVIZIO) as base:
        with urllib.request.urlopen(f'{base}/api/settings', timeout=10) as r:  # noqa: S310
            assert r.status == 200
            grezzo = r.read().decode('utf-8')
    corpo = _json.loads(grezzo)
    assert corpo['bot_id'] == BOT_DEL_SERVIZIO.split(':', 1)[0]
    assert BOT_DEL_SERVIZIO.split(':', 1)[1] not in grezzo, (
        'il token del bot e\' uscito dalla rotta HTTP')
    assert corpo['base_url'].startswith('http')
