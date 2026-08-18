"""La card «Output e condizioni» (#35, pezzo 3) pilotata in browser, sul relay vero.

Il percorso del cliente: parser con la base configurata, righe di override
aggiunte dalla card nel riepilogo (due mercati di cui uno con la quota rotta,
una selezione BANCA), la prova che mostra il k su N per riga e il CSV COMPOSTO
(un solo header, le sole righe piazzabili), il salvataggio che sopravvive alla
riapertura del wizard (la config.multi non deve sparire dal draft), la rimozione
di una riga che resta rimossa. Chiusura a 390px senza scroll orizzontale.

Esce diverso da zero al primo problema. Zero errori in console, come tutti i
flussi web di questo repository.
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
    errors.append(f'console.{m.type}: {m.text}')


def shot(page, name):
    page.screenshot(path=str(OUT / f'{name}.png'), full_page=True)
    print('shot', name)


MSG = 'P.Bet.\nJuve v Milan\n@ 1.85'

# La base del parser: la stessa forma dei test relay del pezzo 2.
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


def righe_card(pg):
    """Le righe multi disegnate nella card, come [(lista, indice), ...]."""
    return [(r.get_attribute('data-lista'), r.get_attribute('data-i'))
            for r in pg.query_selector_all('[data-mrow]')]


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

    # ------------------------------------------------ parser + base config
    pg.click('nav a[href="#/parsers"]')
    pg.wait_for_selector('[data-act="new-parser"]')
    pg.click('[data-act="new-parser"]')
    pg.wait_for_selector('#np-name')
    pg.fill('#np-name', 'Multi riga')
    pg.click('[data-act="create-parser"]')
    pg.wait_for_selector('#paste-msg')
    slug = pg.evaluate('location.hash').split('/')[2]

    # Il campione si salva dal percorso REALE (start-wizard); la base della
    # config invece e' una FIXTURE, non il soggetto del test: si scrive con la
    # stessa PUT della web app, dalla stessa sessione del browser.
    pg.fill('#paste-msg', MSG)
    pg.click('[data-act="start-wizard"]')
    pg.wait_for_selector('#match-val')
    stato = pg.evaluate(
        """async ([slug, config]) => {
             const r = await fetch(`/api/me/parsers/${slug}`, {
               method: 'PUT',
               headers: {'Content-Type': 'application/json'},
               body: JSON.stringify({titolo: 'Multi riga', config, active: true})});
             return r.status;
           }""", [slug, CONFIG_BASE])
    assert stato == 200, f'PUT base config: atteso 200, avuto {stato}'
    pg.reload()
    pg.wait_for_selector('#test-msg')     # riepilogo: base configurata + campione
    shot(pg, '01-riepilogo')

    # ------------------------------------------------ la card, vuota
    card = pg.query_selector('#multi-card')
    assert card, 'nel riepilogo manca la card «Output e condizioni»'
    testo_card = pg.inner_text('#multi-card')
    assert 'Output e condizioni' in testo_card, testo_card
    assert righe_card(pg) == [], 'una config senza multi deve mostrare zero righe'
    shot(pg, '02-card-vuota')

    # ------------------------------------------------ tre righe di override
    pg.click('[data-act="multi-add"][data-lista="markets"]')
    pg.wait_for_selector('[data-mrow="markets:0"]')
    pg.fill('[data-mrow="markets:0"] [data-mfield="market_type"]', 'OVER_UNDER_25')
    pg.fill('[data-mrow="markets:0"] [data-mfield="selection_name"]', 'Over 2,5')

    pg.click('[data-act="multi-add"][data-lista="markets"]')
    pg.wait_for_selector('[data-mrow="markets:1"]')
    pg.fill('[data-mrow="markets:1"] [data-mfield="market_type"]', 'OVER_UNDER_05')
    pg.fill('[data-mrow="markets:1"] [data-mfield="selection_name"]', 'Over 0,5')
    pg.fill('[data-mrow="markets:1"] [data-mfield="price"]', 'abc')

    pg.click('[data-act="multi-add"][data-lista="selections"]')
    pg.wait_for_selector('[data-mrow="selections:0"]')
    pg.fill('[data-mrow="selections:0"] [data-mfield="selection_name"]', 'Under 1,5')
    pg.fill('[data-mrow="selections:0"] [data-mfield="bet_type"]', 'BANCA')
    shot(pg, '03-righe-compilate')

    # ------------------------------------------------ la prova: k su N
    pg.fill('#test-msg', MSG)
    pg.click('[data-act="run-test"]')
    pg.wait_for_selector('#test-result')
    esito = pg.inner_text('#test-result')
    assert '2 di 3' in esito, f'la prova deve dire il k su N (2 di 3): {esito!r}'
    pille = pg.query_selector_all('#test-righe .pill')
    assert len(pille) == 3, 'una pill di esito per ciascuna riga generata'
    assert 'abc' in pg.inner_text('#test-righe'), \
        'la riga rotta deve mostrare il SUO motivo'
    csv = pg.inner_text('#test-csv')
    assert csv.count('"Provider"') == 1, 'CSV composto: header UNA volta sola'
    assert csv.count('"OVER_UNDER_25"') == 1 and csv.count('"BANCA"') == 1, csv
    assert '"OVER_UNDER_05"' not in csv, 'la riga rotta non entra nel CSV'
    # L'anteprima live (motore JS) deve comporre gli stessi byte del server.
    assert pg.inner_text('#live-csv') == csv, \
        'anteprima live e prova server devono coincidere'
    shot(pg, '04-prova-k-su-n')

    # ------------------------------------------------ salvataggio e riapertura
    pg.click('[data-act="wiz-save"]')
    pg.wait_for_selector('.toast')
    pg.reload()
    pg.wait_for_selector('#multi-card')
    assert len(righe_card(pg)) == 3, \
        'le righe multi salvate devono sopravvivere alla riapertura del wizard'
    valore = pg.input_value('[data-mrow="markets:0"] [data-mfield="market_type"]')
    assert valore == 'OVER_UNDER_25', valore
    # E la config sul server porta davvero multi (non solo il DOM).
    config_salvata = pg.evaluate(
        """async slug => {
             const r = await fetch('/api/me/parsers');
             const elenco = await r.json();
             return elenco.find(p => p.slug === slug).config;
           }""", slug)
    assert 'multi' in config_salvata, \
        f'config.multi non e\' sul server dopo il salvataggio: {json.dumps(config_salvata)[:200]}'
    assert len(config_salvata['multi']['markets']) == 2
    assert len(config_salvata['multi']['selections']) == 1
    shot(pg, '05-riaperto')

    # ------------------------------------------------ rimozione di una riga
    pg.click('[data-mrow="markets:1"] [data-act="multi-del"]')
    pg.wait_for_selector('[data-mrow="markets:1"]', state='detached')
    pg.click('[data-act="wiz-save"]')
    pg.wait_for_selector('.toast')
    pg.reload()
    pg.wait_for_selector('#multi-card')
    assert len(righe_card(pg)) == 2, 'la riga rimossa deve restare rimossa'
    shot(pg, '06-riga-rimossa')

    # ------------------------------------ un re-render QUALUNQUE non deve
    # perdere le righe digitate e non salvate (Fable, PR #70): «Sospendi»
    # ridisegna il riepilogo senza passare dalle azioni della card.
    pg.fill('[data-mrow="markets:0"] [data-mfield="market_name"]', 'Over/Under 2.5')
    pg.click('[data-act="toggle-active"]')
    pg.wait_for_selector('.pill.off')          # sospeso: la vista si e' ridisegnata
    valore = pg.input_value('[data-mrow="markets:0"] [data-mfield="market_name"]')
    assert valore == 'Over/Under 2.5', \
        f'un re-render fuori dalla card non deve perdere gli input: {valore!r}'
    pg.click('[data-act="toggle-active"]')     # riattivo per i passi successivi
    pg.wait_for_selector('.pill.on')
    shot(pg, '06b-rerender-non-perde')

    # ------------------------------------ UNA sola riga attiva, rotta: il
    # motivo NON deve sparire (CodeRabbit, PR #70): il k/N si mostra anche
    # con una riga sola quando il multi e' attivo.
    pg.click('[data-mrow="selections:0"] [data-act="multi-del"]')
    pg.wait_for_selector('[data-mrow="selections:0"]', state='detached')
    pg.fill('[data-mrow="markets:0"] [data-mfield="price"]', 'abc')
    pg.fill('#test-msg', MSG)
    pg.click('[data-act="run-test"]')
    pg.wait_for_selector('#test-righe')
    esito_uno = pg.inner_text('#test-result')
    assert '0 di 1' in esito_uno, \
        f'una sola riga rotta deve dire 0 di 1, non nascondersi: {esito_uno!r}'
    assert 'abc' in pg.inner_text('#test-righe'), \
        'il motivo della riga singola rotta deve restare visibile'
    shot(pg, '07-riga-singola-rotta')

    # ------------------------------------------------ 390px, niente scroll X
    pg.set_viewport_size({'width': 390, 'height': 844})
    pg.wait_for_timeout(200)
    scroll = pg.evaluate(
        'document.documentElement.scrollWidth - document.documentElement.clientWidth')
    assert scroll <= 0, f'scroll orizzontale a 390px: {scroll}px di troppo'
    shot(pg, '08-mobile')

    b.close()

if errors:
    print('\n'.join(errors))
    sys.exit(1)
print('ok: card multi-riga, prova k su N, persistenza e mobile verificati')
