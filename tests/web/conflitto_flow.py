"""Il conflitto della PUT (#51) pilotato in browser, sul relay vero.

Il lost update visto dal cliente: il wizard ha letto il parser, un'ALTRA
sessione lo salva nel frattempo (qui simulata con una PUT dalla stessa
sessione del browser, che al server e' indistinguibile), e il «Salva» del
wizard NON deve sovrascrivere in silenzio: toast di conflitto, il
salvataggio altrui resta intatto, le modifiche del wizard restano nel
draft, e il secondo «Salva» e' la sovrascrittura deliberata che vince.

Esce diverso da zero al primo problema. Zero errori in console — tranne il
409 della PUT, che e' il contratto sotto verifica.
"""

import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from playwright.sync_api import sync_playwright

from tests.runtime import apri_chromium  # noqa: E402
from tests.web.credenziali_prova import PASSWORD_PROVA, UTENTE_PROVA  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8099/app/'
OUT = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else pathlib.Path(tempfile.mkdtemp())
OUT.mkdir(parents=True, exist_ok=True)

errors = []
ROTTE_401_ATTESE = ('/api/me',)


def _console(m):
    if m.type != 'error':
        return
    if 'Failed to load resource' in m.text:
        url = (m.location or {}).get('url', '')
        percorso = url.split('?')[0]
        if '401' in m.text and any(percorso.endswith(r) for r in ROTTE_401_ATTESE):
            return
        # Il 409 della PUT e' il contratto sotto verifica, non un guasto.
        if '409' in m.text and '/api/me/parsers/' in percorso:
            return
    errors.append(f'console.{m.type}: {m.text}')


def shot(page, name):
    page.screenshot(path=str(OUT / f'{name}.png'), full_page=True)
    print('shot', name)


MSG = 'P.Bet.\nJuve v Milan\n@ 1.85'

CONFIG_BASE = {
    'match': {'type': 'contains', 'value': 'P.Bet.'},
    'columns': {
        'EventName': {'source': 'line', 'anchor': ' v ', 'part': 'whole',
                      'transforms': [
                          {'op': 'replace_last', 'from': ' v ', 'to': ' - '},
                          {'op': 'trim'}]},
        'MarketType': {'source': 'constant', 'value': 'OVER_UNDER_15'},
        'SelectionName': {'source': 'constant', 'value': 'Over 1,5'},
        'BetType': {'source': 'constant', 'value': 'PUNTA'},
        'Price': {'source': 'constant', 'value': '1.85'}},
}


def config_sul_server(pg, slug):
    return pg.evaluate(
        """async slug => {
             const r = await fetch('/api/me/parsers');
             const elenco = await r.json();
             return elenco.find(p => p.slug === slug).config;
           }""", slug)


with sync_playwright() as pw:
    b = apri_chromium(pw)
    pg = b.new_page(viewport={'width': 1420, 'height': 1000})
    pg.on('console', _console)
    pg.on('pageerror', lambda e: errors.append(f'pageerror: {e}'))

    pg.goto(BASE)
    pg.wait_for_selector('#login-user')
    pg.fill('#login-user', UTENTE_PROVA)
    pg.fill('#login-pass', PASSWORD_PROVA)
    pg.click('[data-act="login-password"]')
    pg.wait_for_selector('.stats')

    # ------------------------------------------------ parser con base salvata
    pg.click('nav a[href="#/parsers"]')
    pg.wait_for_selector('[data-act="new-parser"]')
    pg.click('[data-act="new-parser"]')
    pg.wait_for_selector('#np-name')
    pg.fill('#np-name', 'Conteso')
    pg.click('[data-act="create-parser"]')
    pg.wait_for_selector('#paste-msg')
    slug = pg.evaluate('location.hash').split('/')[2]
    pg.fill('#paste-msg', MSG)
    pg.click('[data-act="start-wizard"]')
    pg.wait_for_selector('#match-val')
    stato = pg.evaluate(
        """async ([slug, config]) => {
             const r = await fetch(`/api/me/parsers/${slug}`, {
               method: 'PUT',
               headers: {'Content-Type': 'application/json'},
               body: JSON.stringify({titolo: 'Conteso', config, active: true})});
             return r.status;
           }""", [slug, CONFIG_BASE])
    assert stato == 200, f'PUT base config: atteso 200, avuto {stato}'
    pg.reload()
    pg.wait_for_selector('#test-msg')     # riepilogo: la cache ha letto la versione

    # ----------------------------------- «l'altra sessione» salva nel frattempo
    config_altrui = dict(CONFIG_BASE)
    config_altrui['match'] = {'type': 'contains', 'value': 'ALTRA_SESSIONE'}
    stato = pg.evaluate(
        """async ([slug, config]) => {
             const r = await fetch(`/api/me/parsers/${slug}`, {
               method: 'PUT',
               headers: {'Content-Type': 'application/json'},
               body: JSON.stringify({titolo: 'Conteso', config, active: true})});
             return r.status;
           }""", [slug, config_altrui])
    assert stato == 200, f'la PUT altrui deve riuscire: {stato}'

    # ------------------------------------ il «Salva» del wizard: DEVE perdere
    pg.click('[data-act="wiz-save"]')
    pg.wait_for_selector('.toast')
    toast = pg.inner_text('.toast')
    assert 'Modificato altrove' in toast, \
        f'il salvataggio con la versione vecchia deve dire il conflitto: {toast!r}'
    salvata = config_sul_server(pg, slug)
    assert salvata['match']['value'] == 'ALTRA_SESSIONE', \
        f'il salvataggio altrui deve restare intatto: {json.dumps(salvata)[:120]}'
    shot(pg, '01-conflitto')

    # --------------------------- il secondo «Salva» e' la scelta deliberata
    pg.click('[data-act="wiz-save"]')
    pg.wait_for_selector('.toast:has-text("salvata")')
    salvata = config_sul_server(pg, slug)
    assert salvata['match']['value'] == 'P.Bet.', \
        f'il secondo salvataggio deve vincere consapevolmente: {json.dumps(salvata)[:120]}'
    shot(pg, '02-sovrascrittura-deliberata')

    b.close()

if errors:
    print('\n'.join(errors))
    sys.exit(1)
print('ok: il lost update e\' visibile e la sovrascrittura e\' una scelta')
