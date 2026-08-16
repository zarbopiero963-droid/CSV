"""Il pannello Richieste dell'amministratore, pilotato da un browser vero (#7).

Backend dal PR 7; qui si verifica il LATO ADMIN in UI: l'elenco delle richieste
coi dati del cliente, il campo giorni libero + «Attiva», il «Rifiuta» con
conferma, il giro dei promemoria — e SOPRATTUTTO che un avviso Telegram fallito
sia visibile e mai ingoiato (trappola 1 della Issue #7: in questo ambiente
l'invio fallisce per costruzione — bot finto e proxy morto — quindi l'esito
`notificato: false` e' deterministico). Chiude il giro il punto di vista del
cliente approvato: dashboard attiva coi giorni concessi.

Argomenti: base_url, cartella screenshot, JSON con
`{admin_cookie, cliente_cookie, nome_uno, nome_due, giorni}`.
"""

import json, sys, pathlib, tempfile, urllib.error, urllib.request
from urllib.parse import urlsplit
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from playwright.sync_api import sync_playwright

from tests.runtime import apri_chromium  # noqa: E402

BASE = sys.argv[1]
OUT = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else pathlib.Path(tempfile.mkdtemp())
OUT.mkdir(parents=True, exist_ok=True)
DATI = json.loads(pathlib.Path(sys.argv[3]).read_text(encoding='utf-8'))

errors = []


def _console(m):
    # Nessun filtro, deliberatamente: la sonda del 404 admin sta FUORI dalla
    # pagina (urllib col cookie del cliente, in fondo al flusso), quindi la
    # console deve restare pulita in entrambi i contesti. Il filtro precedente
    # ignorava i 404 su /api/admin/* anche col cookie ADMIN — una regressione
    # di routing sarebbe passata in silenzio (bloccante di Claude Fable 5).
    if m.type == 'error':
        errors.append(f'console.{m.type}: {m.text}')


def _contesto(browser, cookie):
    ctx = browser.new_context(viewport={'width': 1360, 'height': 950})
    pezzi = urlsplit(BASE)
    # Il nome viene da main.NOME_COOKIE via JSON (fonte unica, regola 3): con
    # un letterale, un rinomino lato server avrebbe fatto passare la sonda 404
    # per «cookie assente» invece che per isolamento (bloccante di Fable, #53).
    ctx.add_cookies([{'name': DATI['nome_cookie'], 'value': cookie,
                      'url': f'{pezzi.scheme}://{pezzi.netloc}/'}])
    pg = ctx.new_page()
    pg.on('console', _console)
    pg.on('pageerror', lambda e: errors.append(f'pageerror: {e}'))
    return ctx, pg


with sync_playwright() as pw:
    b = apri_chromium(pw)

    # ---- l'amministratore: elenco, approvazione, rifiuto, promemoria
    ctx, pg = _contesto(b, DATI['admin_cookie'])
    pg.goto(BASE)
    pg.wait_for_selector('nav a[href="#/richieste"]')
    pg.click('nav a[href="#/richieste"]')
    # L'attesa e' sui NOMI, non su un generico `.list-item`: la dashboard
    # appena lasciata ha le sue righe `.list-item`, e su un runner lento il
    # selettore generico combaciava con quelle mentre «Richieste» era ancora
    # al «Caricamento…» — misurato rosso in CI, verde in locale.
    pg.wait_for_selector(f'.list-item:has-text("{DATI["nome_uno"]}")')
    pg.wait_for_selector(f'.list-item:has-text("{DATI["nome_due"]}")')
    pg.screenshot(path=str(OUT / '01-richieste.png'), full_page=True)

    # Attiva senza giorni: il campo e' obbligatorio, niente POST alla cieca
    pg.click('[data-act="approva-richiesta"] >> nth=0')
    pg.wait_for_selector('.toast')

    # Attiva col campo libero: l'avviso Telegram QUI fallisce per costruzione,
    # e l'esito deve dirlo in faccia, non sparire in un toast
    riga_uno = pg.locator('.list-item', has_text=DATI['nome_uno'])
    riga_uno.locator('input[type="number"]').fill(str(DATI['giorni']))
    riga_uno.locator('[data-act="approva-richiesta"]').click()
    pg.wait_for_selector('#esito-decisione')
    esito = pg.inner_text('#esito-decisione')
    assert 'NON' in esito, (
        f'l\'avviso Telegram fallito e\' stato ingoiato dall\'esito: {esito!r}')
    assert str(DATI['giorni']) in esito, f'giorni assenti dall\'esito: {esito!r}'
    assert DATI['nome_uno'] not in pg.inner_text('.card .list-item'), (
        'la richiesta approvata e\' rimasta nell\'elenco')
    pg.screenshot(path=str(OUT / '02-approvata-avviso-fallito.png'), full_page=True)

    # Rifiuta, con conferma: il cliente torna «registrato», non sospeso
    pg.locator('.list-item', has_text=DATI['nome_due']) \
      .locator('[data-act="rifiuta-richiesta"]').click()
    pg.wait_for_selector('[data-act="rifiuta-richiesta-ok"]')
    pg.click('[data-act="rifiuta-richiesta-ok"]')
    pg.wait_for_selector('.empty')
    assert 'rifiutata' in pg.inner_text('#esito-decisione').lower()
    pg.screenshot(path=str(OUT / '03-rifiutata.png'), full_page=True)

    # Il giro dei promemoria: nessun candidato seminato, zero e zero
    pg.click('[data-act="giro-promemoria"]')
    pg.wait_for_function(
        "document.getElementById('esito-promemoria')"
        " && document.getElementById('esito-promemoria').textContent.includes('avvisati')")
    promemoria = pg.inner_text('#esito-promemoria')
    assert 'avvisati: 0' in promemoria and 'falliti: 0' in promemoria, promemoria
    pg.screenshot(path=str(OUT / '04-promemoria.png'), full_page=True)
    ctx.close()

    # ---- il cliente approvato: dashboard attiva coi giorni concessi
    ctx, pg = _contesto(b, DATI['cliente_cookie'])
    pg.goto(BASE)
    pg.wait_for_selector('.stats')
    testa = pg.inner_text('.head')
    assert 'attivo' in testa and f"{DATI['giorni']} giorni" in testa, (
        f'il cliente approvato non vede i giorni concessi: {testa!r}')
    # e il pannello Richieste per lui NON esiste: ne' la voce nel menu...
    assert pg.locator('nav a[href="#/richieste"]').count() == 0, (
        'un cliente vede la voce Richieste nel menu')
    # ...ne' la vista, nemmeno digitando l'hash a mano
    pg.goto(BASE + '#/richieste')
    pg.wait_for_selector('.stats')
    assert 'Richieste' not in pg.inner_text('h1'), 'un cliente ha aperto il pannello'
    # E il CSV del cliente APPENA APPROVATO funziona: token coniato dalla
    # sessione, feed letto e asserito sui BYTE (arrayBuffer, non text(): la
    # decodifica del browser toglie il BOM). Chiesto da CodeRabbit sulla PR #53.
    esito = pg.evaluate("""async () => {
      const r = await fetch('/api/me/token', {method: 'POST'});
      if (!r.ok) return {errore: 'token: ' + r.status};
      const {token, feed} = await r.json();
      const f = await fetch(feed + '?token=' + encodeURIComponent(token));
      const byte = new Uint8Array(await f.arrayBuffer());
      return {stato: f.status, primi: Array.from(byte.slice(0, 3)),
              corpo: new TextDecoder().decode(byte)};
    }""")
    assert esito.get('stato') == 200, f'feed del cliente approvato: {esito!r}'
    assert esito['primi'] == [0xEF, 0xBB, 0xBF], (
        f'il feed non comincia col BOM: {esito["primi"]}')
    assert esito['corpo'].startswith('"Provider"'), (
        f'intestazione inattesa: {esito["corpo"][:40]!r}')
    assert esito['corpo'].strip().count('\n') == 0, 'atteso feed a sola intestazione'
    pg.screenshot(path=str(OUT / '05-cliente-attivo.png'), full_page=True)
    ctx.close()

    b.close()

# La sonda del 404 sulle rotte admin sta FUORI dalla pagina: urllib col cookie
# del CLIENTE. Cosi' la console del browser resta pulita in ENTRAMBI i contesti
# e non serve nessun filtro — il filtro precedente ignorava i 404 admin anche
# col cookie dell'amministratore, e una regressione di routing sarebbe passata
# in silenzio (bloccante di Claude Fable 5). Il 404 e' del server: la UI e'
# solo il riflesso (chiesto da GPT-5.5).
pezzi = urlsplit(BASE)
richiesta = urllib.request.Request(
    f'{pezzi.scheme}://{pezzi.netloc}/api/admin/requests',
    headers={'Cookie': DATI['nome_cookie'] + '=' + DATI['cliente_cookie']})
try:
    urllib.request.urlopen(richiesta, timeout=10)  # noqa: S310 - loopback del test
    raise AssertionError('le rotte admin rispondono 200 a un cliente')
except urllib.error.HTTPError as e:
    assert e.code == 404, f'rotte admin a un cliente: atteso 404, avuto {e.code}'

print('\nERRORI JS:', len(errors))
for e in errors:
    print(' -', e)
sys.exit(1 if errors else 0)
