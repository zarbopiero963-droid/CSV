"""Il file unico dimostrativo si comporta come la versione modulare (CLAUDE.md).

Il bundle di `tools/build_single_file.py` concatena `api_finta.js` al posto di
`api.js` — si apre da `file://`, dove `fetch` non esiste — e converte il
JavaScript in ASCII puro. Sono i due punti in cui puo' rompersi in silenzio:
una funzione mancante nel layer finto e' un `TypeError` alla prima azione, un
emoji non escapato e' un confronto che fallisce sempre (la storia di
`suggestConfig` nella REGOLA CODIFICA). Qui il bundle viene GENERATO ed
ESEGUITO in un browser vero, da `file://`, fino al CSV.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE))

from tests.ambiente import ambiente_di_supporto  # noqa: E402
from tests.runtime import apri_chromium, esigi_browser  # noqa: E402

esigi_browser()

MSG = 'P.Bet. LIVE 2,5\n\U0001F19A Inter v Milan\n@ 1.85'


def test_il_file_unico_esegue_il_flusso_demo_da_file(tmp_path):
    generato = tmp_path / 'prototipo.html'
    esito = subprocess.run(
        [sys.executable, str(RADICE / 'tools' / 'build_single_file.py'), str(generato)],
        capture_output=True, text=True, timeout=60, env=ambiente_di_supporto(),
    )
    assert esito.returncode == 0, esito.stderr
    testo = generato.read_bytes()
    assert max(testo) < 128, 'il bundle non e\' ASCII puro'

    from playwright.sync_api import sync_playwright

    errori = []
    with sync_playwright() as pw:
        b = apri_chromium(pw)
        pg = b.new_page(viewport={'width': 1280, 'height': 900})
        pg.on('console', lambda m: errori.append(m.text) if m.type == 'error' else None)
        pg.on('pageerror', lambda e: errori.append(str(e)))
        pg.goto(generato.as_uri())

        # login demo: la porta a password accetta qualunque coppia non vuota
        pg.wait_for_selector('#login-pass')
        pg.fill('#login-user', 'demo')
        pg.fill('#login-pass', 'demo')
        pg.click('[data-act="login-password"]')
        pg.wait_for_selector('.stats')

        # parser nuovo, suggerimento, prova: tutto nel browser, zero rete
        pg.click('[data-act="new-parser"]')
        pg.fill('#np-name', 'Demo LIVE')
        pg.click('[data-act="create-parser"]')
        pg.wait_for_selector('#paste-msg')
        pg.fill('#paste-msg', MSG)
        # Il suggeritore confronta il marcatore emoji col messaggio: e' il
        # confronto che il mojibake della codifica rompeva in silenzio.
        pg.click('[data-act="ai-suggest"]')
        pg.wait_for_selector('.map-table', timeout=8000)
        assert 'Inter' in pg.inner_text('.map-table'), (
            'il suggeritore non ha estratto l\'evento: se il bundle ha rotto '
            'la codifica del marcatore, il sintomo e\' esattamente questo')
        # La prova del solo suggerimento e' onestamente INCOMPLETA: il
        # suggeritore non puo' inventare MarketType/SelectionName/BetType
        # (regola #39: niente dati inventati), quindi niente riga nel feed.
        pg.click('[data-act="run-test"]')
        pg.wait_for_selector('#test-csv')
        assert 'incompleto' in pg.inner_text('#test-result'), \
            pg.inner_text('#test-result')
        csv_txt = pg.inner_text('#test-csv')
        assert 'Inter' not in csv_txt, f'una riga incompleta ha raggiunto il CSV: {csv_txt!r}'
        assert csv_txt.strip().count('\n') == 0, 'atteso feed a sola intestazione'

        # token demo: il modale una-volta-sola esiste anche qui
        pg.click('nav a[href="#/feed"]')
        pg.wait_for_selector('[data-act="ask-token"]')
        pg.click('[data-act="ask-token"]')
        pg.wait_for_selector('.modal .secret')
        assert pg.locator('.modal .secret').first.inner_text().startswith('xt_')

        b.close()
    assert not errori, errori
