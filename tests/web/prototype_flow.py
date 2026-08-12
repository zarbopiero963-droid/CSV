"""Flusso completo del prototipo, pilotato da un browser vero.

CLAUDE.md pretende che una modifica a `web/` sia verificata caricando davvero la
pagina: un `py_compile` non dice nulla sul JavaScript. Questo script esegue il
percorso reale — login, wizard, mappatura colonna per colonna, prova messaggio,
token, verifica chat, log — e asserisce il CSV prodotto e l'assenza di errori in
console. Esce diverso da zero al primo problema.

Lo avvia `test_prototype_flow.py`, che tira su il server. A mano:

    uvicorn main:app --port 8099
    python tests/web/prototype_flow.py http://127.0.0.1:8099/app/ /tmp/shots
"""

import sys, pathlib, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from playwright.sync_api import sync_playwright

# Fonte unica dell-avvio del browser: il percorso pinnato in questo
# ambiente, quello di Playwright in CI. Prima era ricopiato in cinque file.
from tests.runtime import apri_chromium  # noqa: E402

# Chromium preinstallato nell'immagine; PLAYWRIGHT_BROWSERS_PATH punta qui.

BASE = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8099/app/'
OUT = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else pathlib.Path(tempfile.mkdtemp())
OUT.mkdir(parents=True, exist_ok=True)
MSG = "P.Bet. LIVE 2,5\n\U0001F19A Inter v Milan\n⏰ 20:45\n@ 1.85"

errors = []

def shot(page, name):
    page.screenshot(path=str(OUT / f'{name}.png'), full_page=True)
    print('shot', name)

with sync_playwright() as pw:
    b = apri_chromium(pw)
    pg = b.new_page(viewport={'width': 1420, 'height': 1000})
    pg.on('console', lambda m: errors.append(f'console.{m.type}: {m.text}') if m.type == 'error' else None)
    pg.on('pageerror', lambda e: errors.append(f'pageerror: {e}'))

    pg.goto(BASE)
    pg.wait_for_selector('.login')
    shot(pg, '01-login')

    pg.click('[data-act="login-widget"]')
    pg.wait_for_selector('.list-item')
    shot(pg, '02-dashboard')

    # parser esistente -> configurazione
    pg.click('a.name')
    pg.wait_for_selector('.wizard-grid')
    shot(pg, '03-parser-esistente')

    # feed tab
    pg.click('text=Feed CSV')
    pg.wait_for_selector('.csv-out')
    shot(pg, '04-feed')

    # nuovo parser
    pg.click('nav a[href="#/parsers"]')
    pg.wait_for_selector('.list-item')
    shot(pg, '04b-lista-parser')
    pg.click('[data-act="new-parser"]')
    pg.fill('#np-name', 'Over 2,5 LIVE')
    pg.click('[data-act="create-parser"]')
    pg.wait_for_selector('#paste-msg')
    shot(pg, '05-wizard-incolla')

    # AI suggest
    pg.fill('#paste-msg', MSG)
    pg.click('[data-act="ai-suggest"]')
    pg.wait_for_selector('.map-table', timeout=8000)
    shot(pg, '06-suggerimento')

    # riparti dal messaggio e vai a mano: condizione
    pg.click('[data-act="wiz-restart"]')
    pg.wait_for_selector('#paste-msg')
    pg.click('[data-act="start-wizard"]')
    pg.wait_for_selector('#match-val')
    pg.click('.frag >> nth=0')
    pg.click('[data-act="save-match"]')
    pg.wait_for_selector('[data-act="wiz-next"]')
    shot(pg, '07-colonna-provider')

    # Regressione del finding Critical di CodeRabbit: partendo da una regola SENZA
    # trasformazioni (Provider e' una costante), readCurrentRule() in modalita' regex
    # crea un oggetto nuovo con un array vuoto SUO. Se il gestore cattura la regola
    # prima della rilettura, spinge la trasformazione in una copia orfana e la perde.
    pg.click('[data-act="wiz-mode"][data-mode="regex"]')
    pg.wait_for_selector('#rule-pattern')
    # La trasformazione si attiva PRIMA di digitare: se si digitasse per primo,
    # l'evento input avrebbe gia' convertito la regola in regex e l'array delle
    # trasformazioni risulterebbe condiviso, mascherando il difetto.
    # click SINGOLO, non check(): check() ritenta finche' la casella non risulta
    # spuntata, e il secondo tentativo mascherava il difetto (la regola era ormai
    # diventata regex e l'array condiviso). Il difetto colpisce la PRIMA attivazione.
    pg.click('[data-act="toggle-transform"][data-op="lower"]')
    pg.wait_for_timeout(300)
    assert pg.is_checked('[data-act="toggle-transform"][data-op="lower"]'), \
        'la PRIMA trasformazione attivata in modalita regex e stata persa al render'
    pg.fill('#rule-pattern', '(Inter)')
    pg.wait_for_timeout(250)
    got = pg.inner_text('.xt tbody td >> nth=0')
    assert got == 'inter', f'trasformazione minuscolo non applicata: {got!r}'
    pg.uncheck('[data-act="toggle-transform"][data-op="lower"]')
    pg.wait_for_timeout(200)

    # Provider = costante
    pg.click('[data-act="wiz-mode"][data-mode="constant"]')
    pg.fill('#rule-const', 'XTrader')
    prov = pg.inner_text('.xt tbody td >> nth=0')
    assert prov == 'XTrader', f'anteprima Provider={prov!r}'
    pg.click('[data-act="wiz-next"]')          # -> EventId
    pg.click('[data-act="rule-empty"]')        # EventId vuoto -> EventName
    pg.wait_for_selector('.frag')
    pg.click('.frag >> nth=1')                 # riga con l'emoji versus
    pg.wait_for_selector('#rule-anchor')
    shot(pg, '08-eventname-frammento')
    ev = pg.inner_text('.xt tbody td >> nth=2')
    assert ev == 'Inter v Milan', f'EventName grezzo={ev!r}'

    # attiva la sostituzione dell'ultimo " v "
    pg.check('[data-act="toggle-transform"][data-op="replace_last"]')
    pg.wait_for_timeout(200)
    ev2 = pg.inner_text('.xt tbody td >> nth=2')
    assert ev2 == 'Inter - Milan', f'EventName trasformato={ev2!r}'
    shot(pg, '09-eventname-trasformato')

    # salta fino al riepilogo
    for _ in range(12):
        pg.click('[data-act="wiz-next"]')
    pg.wait_for_selector('#test-msg')
    shot(pg, '10-riepilogo')

    # prova messaggio: caso riconosciuto
    pg.click('[data-act="run-test"]')
    pg.wait_for_selector('#test-csv')
    csv_txt = pg.inner_text('#test-csv')
    assert 'Riconosciuto' in pg.inner_text('#test-result'), 'atteso esito riconosciuto'
    assert 'Inter - Milan' in csv_txt, f'CSV senza evento: {csv_txt!r}'
    assert csv_txt.count('"') == 56, f'campi quotati attesi 56, trovati {csv_txt.count(chr(34))}'
    assert csv_txt.strip().count(chr(10)) == 1, 'attese 2 righe: intestazione + segnale'
    shot(pg, '11-prova-ok')
    print('CSV riconosciuto:', repr(csv_txt))

    # prova messaggio: caso da ignorare (la condizione non corrisponde)
    pg.fill('#test-msg', 'Buongiorno ragazzi, nessun segnale adesso')
    pg.click('[data-act="run-test"]')
    pg.wait_for_function("document.querySelector('#test-result')"
                         " && document.querySelector('#test-result').innerText.includes('Ignorato')")
    only_head = pg.inner_text('#test-csv')
    assert 'Inter' not in only_head, f'il feed non doveva cambiare: {only_head!r}'
    assert only_head.strip().count(chr(10)) == 0, 'atteso solo header'
    shot(pg, '11b-prova-ignorata')
    print('CSV ignorato:', repr(only_head))

    # ripristina il messaggio buono e rilancia, per lasciare un segnale vivo nel feed
    pg.fill('#test-msg', MSG)
    pg.click('[data-act="run-test"]')
    pg.wait_for_function("document.querySelector('#test-result')"
                         " && document.querySelector('#test-result').innerText.includes('Riconosciuto')")

    pg.click('[data-act="wiz-save"]')
    pg.wait_for_timeout(400)

    # token
    pg.click('text=Feed CSV')
    pg.wait_for_selector('[data-act="rotate-token"]')
    pg.click('[data-act="rotate-token"]')
    pg.wait_for_selector('.modal .secret')
    shot(pg, '12-token')
    pg.click('[data-act="after-token"]')
    pg.wait_for_selector('.csv-out')
    shot(pg, '13-feed-attivo')

    # Terzo punto di aggancio del verificatore: quello che il cliente vede.
    # Il pallino verde deve dirlo, non solo il codice deve saperlo.
    pg.wait_for_selector('#csv-format')
    stato = pg.inner_text('#csv-format')
    assert stato == 'formato valido per XTrader', f'indicatore di formato: {stato!r}'
    # E il CSV mostrato deve avere davvero il BOM, non solo il pallino verde.
    primo = pg.evaluate("document.querySelector('.csv-out').textContent.codePointAt(0)")
    assert primo == 0xfeff, f'il CSV mostrato non comincia col BOM: {primo:#x}'

    # verifica chat
    pg.click('nav a[href="#/chats"]')
    pg.wait_for_selector('[data-act="verify-chat"]')
    pg.click('[data-act="verify-chat"]')
    pg.wait_for_selector('[data-act="verify-step2"]')
    shot(pg, '14-collega-bot')
    pg.click('[data-act="verify-step2"]')
    pg.wait_for_selector('.code-big')
    shot(pg, '15-codice-verifica')
    pg.fill('#vf-title', 'Segnali LIVE Test')
    pg.click('[data-act="simulate-verify"]')
    pg.wait_for_selector('[data-act="after-verify"]')
    pg.click('[data-act="after-verify"]')
    pg.wait_for_selector('.list-item')
    shot(pg, '16-chat-collegate')

    # assegna la chat al nuovo parser
    pg.click('nav a[href="#/parsers"]')
    pg.wait_for_selector('.list-item')
    pg.click('a.name >> nth=1')
    pg.wait_for_selector('text=Chat assegnate')
    pg.click('text=Chat assegnate')
    pg.wait_for_selector('[data-act="toggle-chat"]')
    boxes = pg.locator('[data-act="toggle-chat"]')
    boxes.nth(boxes.count() - 1).check()
    pg.wait_for_timeout(500)
    shot(pg, '17-chat-assegnate')

    # log
    pg.click('nav a[href="#/logs"]')
    pg.wait_for_selector('table')
    shot(pg, '18-log')

    pg.click('nav a[href="#/settings"]')
    pg.wait_for_selector('[data-act="reset-proto"]')
    shot(pg, '19-impostazioni')

    b.close()

print('\nERRORI JS:', len(errors))
for e in errors:
    print(' -', e)
sys.exit(1 if errors else 0)
