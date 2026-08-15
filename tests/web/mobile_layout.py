"""Verifica che su schermo stretto la pagina non scorra in orizzontale.

Il difetto e' tornato quattro volte (vedi regola 2 in CLAUDE.md): i figli di flex e
grid hanno `min-width: auto`, quindi un contenuto largo allarga il contenitore
invece di scorrere dentro il proprio. Qui si misura, non si guarda.

Dalla #32 la pagina e' l'app REALE: il login e' quello a password contro il
relay avviato dalla fixture, con le credenziali di `credenziali_prova.py`.
"""

import sys, pathlib, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from playwright.sync_api import sync_playwright

# Fonte unica dell-avvio del browser: il percorso pinnato in questo
# ambiente, quello di Playwright in CI.
from tests.runtime import apri_chromium  # noqa: E402
from tests.web.credenziali_prova import PASSWORD_PROVA, UTENTE_PROVA  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8099/app/'
OUT = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else pathlib.Path(tempfile.mkdtemp())
OUT.mkdir(parents=True, exist_ok=True)
errs = []


def _niente_scroll_orizzontale(pg, dove):
    ow = pg.evaluate("document.documentElement.scrollWidth")
    cw = pg.evaluate("document.documentElement.clientWidth")
    print(dove, 'scrollWidth', ow, 'clientWidth', cw)
    assert ow <= cw + 1, f'la pagina scorre in orizzontale su mobile: {dove}'


with sync_playwright() as pw:
    b = apri_chromium(pw)
    pg = b.new_page(viewport={'width': 390, 'height': 844})
    pg.on('pageerror', lambda e: errs.append(str(e)))
    pg.goto(BASE)
    pg.wait_for_selector('#login-pass')
    pg.screenshot(path=str(OUT / 'm1-login.png'), full_page=True)
    _niente_scroll_orizzontale(pg, 'login')

    pg.fill('#login-user', UTENTE_PROVA)
    pg.fill('#login-pass', PASSWORD_PROVA)
    pg.click('[data-act="login-password"]')
    pg.wait_for_selector('.stats')
    pg.screenshot(path=str(OUT / 'm2-dashboard.png'), full_page=True)
    _niente_scroll_orizzontale(pg, 'dashboard')

    # il dettaglio parser, col wizard: e' la vista piu' larga (tabella 14 colonne)
    pg.click('a.name')
    pg.wait_for_selector('.wizard-grid')
    pg.screenshot(path=str(OUT / 'm3-parser.png'), full_page=True)
    _niente_scroll_orizzontale(pg, 'parser')

    b.close()
print('errori:', errs)
sys.exit(1 if errs else 0)
