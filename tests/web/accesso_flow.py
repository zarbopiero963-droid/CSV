"""Le schermate dell'accesso su approvazione, pilotate da un browser vero (#7).

Il backend del flusso esiste dal PR 7 (server-side): questo script verifica il
LATO CLIENTE — un `registrato` vede la richiesta d'accesso e non la dashboard,
un `in_attesa` vede l'attesa col deep link del bot (trappola 1: il bot non puo'
scrivere per primo), uno `scaduto` puo' richiedere di nuovo, un attivo con la
scadenza vicina vede la pillola gialla. Gli utenti sono seminati nel database
del relay di test e le sessioni sono cookie firmati con la formula vera del
servizio: nessun mock, il POST di «Richiedi accesso» arriva alla rotta vera.

Argomenti: base_url, cartella screenshot, percorso di un JSON con
`[{nome, cookie, atteso}, ...]` dove atteso e' uno di:
`richiedi` · `in_attesa` · `scaduto` · `dashboard_gialla`.
"""

import json, sys, pathlib, tempfile
from urllib.parse import urlsplit
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from playwright.sync_api import sync_playwright

from tests.runtime import apri_chromium  # noqa: E402

BASE = sys.argv[1]
OUT = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else pathlib.Path(tempfile.mkdtemp())
OUT.mkdir(parents=True, exist_ok=True)
CASI = json.loads(pathlib.Path(sys.argv[3]).read_text(encoding='utf-8'))

errors = []


def _console(m):
    if m.type != 'error':
        return
    errors.append(f'console.{m.type}: {m.text}')


def _titolo_accesso(pg):
    try:
        pg.wait_for_selector('.accesso h1')
    except Exception:
        print('PAGINA AL TIMEOUT:', pg.content()[:800])
        raise
    return pg.inner_text('.accesso h1')


with sync_playwright() as pw:
    b = apri_chromium(pw)
    for caso in CASI:
        ctx = b.new_context(viewport={'width': 1280, 'height': 900})
        # Il cookie va legato alla RADICE, non a BASE: con `url` che porta
        # `/app/` il path del cookie diventa `/app` e il browser non lo manda
        # a `/api/me` — il boot vede 401 e la pagina resta al login.
        pezzi = urlsplit(BASE)
        ctx.add_cookies([{'name': 'betrelay_sessione', 'value': caso['cookie'],
                          'url': f'{pezzi.scheme}://{pezzi.netloc}/'}])
        pg = ctx.new_page()
        pg.on('console', _console)
        pg.on('pageerror', lambda e: errors.append(f'pageerror: {e}'))
        pg.goto(BASE)

        if caso['atteso'] == 'richiedi':
            titolo = _titolo_accesso(pg)
            assert 'accesso' in titolo.lower(), f'{caso["nome"]}: titolo {titolo!r}'
            assert pg.locator('.stats').count() == 0, (
                f'{caso["nome"]}: un registrato vede la dashboard')
            pg.screenshot(path=str(OUT / f'{caso["nome"]}-1.png'), full_page=True)
            # il POST vero: dopo la richiesta si atterra sull'attesa, col deep
            # link che rende il cliente raggiungibile (t.me/<bot>?start=...)
            pg.click('[data-act="request-access"]')
            pg.wait_for_function(
                "document.querySelector('.accesso h1')"
                " && document.querySelector('.accesso h1').textContent"
                "     .toLowerCase().includes('inviata')")
            href = pg.get_attribute('.accesso a[data-ruolo="bot-link"]', 'href')
            assert href and 't.me/BetRelayBot?start=' in href, (
                f'{caso["nome"]}: deep link del bot assente o storto: {href!r}')
            pg.screenshot(path=str(OUT / f'{caso["nome"]}-2.png'), full_page=True)

        elif caso['atteso'] == 'in_attesa':
            titolo = _titolo_accesso(pg)
            assert 'inviata' in titolo.lower(), f'{caso["nome"]}: titolo {titolo!r}'
            href = pg.get_attribute('.accesso a[data-ruolo="bot-link"]', 'href')
            assert href and 't.me/BetRelayBot?start=' in href, (
                f'{caso["nome"]}: deep link assente al ritorno: {href!r}')
            pg.screenshot(path=str(OUT / f'{caso["nome"]}.png'), full_page=True)

        elif caso['atteso'] == 'scaduto':
            titolo = _titolo_accesso(pg)
            assert 'scaduto' in titolo.lower(), f'{caso["nome"]}: titolo {titolo!r}'
            pg.screenshot(path=str(OUT / f'{caso["nome"]}-1.png'), full_page=True)
            pg.click('[data-act="request-access"]')
            pg.wait_for_function(
                "document.querySelector('.accesso h1')"
                " && document.querySelector('.accesso h1').textContent"
                "     .toLowerCase().includes('inviata')")
            pg.screenshot(path=str(OUT / f'{caso["nome"]}-2.png'), full_page=True)

        elif caso['atteso'] == 'dashboard_gialla':
            # attivo con la scadenza vicina: la dashboard NORMALE, con la
            # pillola gialla dei giorni rimasti (soglia 5 della Issue #7)
            pg.wait_for_selector('.stats')
            pg.wait_for_selector('.pill.warn')
            testo = pg.inner_text('.pill.warn')
            assert 'giorn' in testo.lower(), f'{caso["nome"]}: pillola {testo!r}'
            pg.screenshot(path=str(OUT / f'{caso["nome"]}.png'), full_page=True)

        else:
            raise AssertionError(f'atteso sconosciuto: {caso["atteso"]!r}')

        print('caso ok:', caso['nome'])
        ctx.close()
    b.close()

print('\nERRORI JS:', len(errors))
for e in errors:
    print(' -', e)
sys.exit(1 if errors else 0)
