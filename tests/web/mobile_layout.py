"""Verifica che su schermo stretto la pagina non scorra in orizzontale.

Il difetto e' tornato quattro volte (vedi regola 2 in CLAUDE.md): i figli di flex e
grid hanno `min-width: auto`, quindi un contenuto largo allarga il contenitore
invece di scorrere dentro il proprio. Qui si misura, non si guarda.
"""

import sys, pathlib, tempfile
from playwright.sync_api import sync_playwright

CHROMIUM = '/opt/pw-browsers/chromium-1194/chrome-linux/chrome'
BASE = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8099/app/'
OUT = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else pathlib.Path(tempfile.mkdtemp())
OUT.mkdir(parents=True, exist_ok=True)
errs = []
with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path=CHROMIUM)
    pg = b.new_page(viewport={'width': 390, 'height': 844})
    pg.on('pageerror', lambda e: errs.append(str(e)))
    pg.goto(BASE)
    pg.wait_for_selector('.login'); pg.screenshot(path=str(OUT/'m1-login.png'), full_page=True)
    pg.click('[data-act="login-widget"]'); pg.wait_for_selector('.list-item')
    pg.screenshot(path=str(OUT/'m2-dashboard.png'), full_page=True)
    pg.click('a.name'); pg.wait_for_selector('.wizard-grid')
    pg.screenshot(path=str(OUT/'m3-parser.png'), full_page=True)
    # nessuno scroll orizzontale del body
    ow = pg.evaluate("document.documentElement.scrollWidth")
    cw = pg.evaluate("document.documentElement.clientWidth")
    print('scrollWidth', ow, 'clientWidth', cw)
    assert ow <= cw + 1, 'la pagina scorre in orizzontale su mobile'
    b.close()
print('errori:', errs)
sys.exit(1 if errs else 0)
