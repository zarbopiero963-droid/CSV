"""Il pannello Richieste dell'amministratore, pilotato da un browser vero (#7).

Backend dal PR 7; qui si verifica il LATO ADMIN in UI: l'elenco delle richieste
coi dati del cliente, il campo giorni libero + «Attiva», il «Rifiuta» con
conferma, il giro dei promemoria — e SOPRATTUTTO che un avviso Telegram fallito
sia visibile e mai ingoiato (trappola 1 della Issue #7: in questo ambiente
l'invio fallisce per costruzione — bot finto e proxy morto — quindi l'esito
`notificato: false` e' deterministico). Seguono le due race della guardia
anti-stantio (bloccante di GPT-5.6 Sol, PR #53), rese deterministiche
trattenendo la risposta con una route Playwright: la risposta di una visita
PRECEDENTE allo stesso hash non deve ridisegnare la vista (ABA), e l'errore di
una vista abbandonata non deve tostificare sopra quella attiva. Chiude il giro
il punto di vista del cliente approvato: dashboard attiva coi giorni concessi.

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

    # Il backup (#56): il pulsante nella card «Backup del database», e il click
    # scarica davvero un file betrelay-backup-...db. La rotta /api/admin/backup e'
    # protetta dal cookie di sessione (questo contesto porta quello dell'admin);
    # la risposta e' Content-Disposition attachment, quindi il browser scarica.
    pg.wait_for_selector('[data-act="scarica-backup"]')
    with pg.expect_download() as scarico:
        pg.click('[data-act="scarica-backup"]')
    scaricato = scarico.value
    nome = scaricato.suggested_filename
    assert nome.startswith('betrelay-backup-') and nome.endswith('.db'), \
        f'nome del backup inatteso: {nome!r}'
    percorso_backup = OUT / 'backup-scaricato.db'
    scaricato.save_as(str(percorso_backup))
    with open(percorso_backup, 'rb') as f:
        primi = f.read(16)
    assert primi == b'SQLite format 3\x00', f'il backup scaricato non e- SQLite: {primi!r}'
    pg.screenshot(path=str(OUT / '04b-backup.png'), full_page=True)

    # ---- la race ABA della guardia anti-stantio (bloccante GPT-5.6 Sol, #53):
    # uscire e RIENTRARE nello stesso hash lascia l'hash identico, quindi un
    # confronto sull'hash non basta — una risposta della visita PRECEDENTE,
    # arrivata fuori ordine, ridisegnerebbe sopra quella fresca. Qui la race
    # e' deterministica: si TRATTIENE la risposta della prima visita, si esce,
    # si rientra (la seconda risponde subito, vuota), e solo allora si libera
    # la prima, piena. La vista deve restare vuota.
    # Un SOLO handler con stato: un unroute mentre una route e' in sospeso la
    # cancella e la fa proseguire («Route is already handled», misurato qui),
    # quindi la modalita' si cambia senza mai smontare l'handler.
    trattenute = []
    modalita = {'fai': 'trattieni'}

    def gestore(route):
        if modalita['fai'] == 'trattieni':
            trattenute.append(route)          # in sospeso, si libera dopo
        else:
            route.fulfill(json={'richieste': []})

    def attendi_trattenuta():
        for _ in range(200):
            if trattenute:
                return
            pg.wait_for_timeout(25)
        raise AssertionError('la richiesta da trattenere non e\' mai partita')

    pg.route('**/api/admin/requests', gestore)
    pg.click('nav a[href="#/parsers"]')
    pg.wait_for_selector('[data-act="new-parser"]')
    pg.click('nav a[href="#/richieste"]')          # visita 1: trattenuta
    attendi_trattenuta()
    pg.click('nav a[href="#/parsers"]')            # si esce...
    pg.wait_for_selector('[data-act="new-parser"]')
    modalita['fai'] = 'vuota'
    pg.click('nav a[href="#/richieste"]')          # ...e si RIENTRA: fresca, vuota
    pg.wait_for_selector('.empty:has-text("Nessuna richiesta")')
    # Barriera CAUSALE, non a orologio (CodeRabbit): prima si aspetta che la
    # risposta liberata sia ARRIVATA alla pagina, poi due requestAnimationFrame
    # — quando parte il secondo frame la coda dei microtask della fetch e' gia'
    # consumata. Un'attesa fissa su un runner lento poteva scadere PRIMA del
    # render stantio: verde senza potere di rilevazione.
    with pg.expect_response('**/api/admin/requests'):
        trattenute.pop(0).fulfill(json={'richieste': [{
            'richiesta': 999, 'utente': 999, 'chiesto_il': '2026-08-16 00:00',
            'nome': 'FantasmaStantio', 'username': None, 'stato': 'in_attesa',
            'giorni_rimasti': None, 'raggiungibile': False}]})
    pg.evaluate(
        '() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))')
    assert 'FantasmaStantio' not in pg.content(), (
        'la risposta stantia della visita precedente ha ridisegnato la vista (ABA)')
    assert pg.locator('.empty').count() == 1, 'la vista fresca e\' sparita'

    # ---- e l'ERRORE di una vista abbandonata non tostifica sopra quella
    # attiva: si trattiene la risposta, si cambia pagina, e solo allora la si
    # fa fallire. Niente toast: il fallimento appartiene a una vista morta.
    modalita['fai'] = 'trattieni'
    pg.click('nav a[href="#/parsers"]')
    pg.wait_for_selector('[data-act="new-parser"]')
    pg.click('nav a[href="#/richieste"]')          # trattenuta di nuovo
    attendi_trattenuta()
    pg.click('nav a[href="#/parsers"]')            # abbandono della vista
    pg.wait_for_selector('[data-act="new-parser"]')
    # I toast di inizio flusso («Scrivi i giorni…») vivono 2,6 s e su questa
    # macchina il flusso corre piu' veloce: si aspetta il DOM pulito, o
    # l'asserzione a zero toast conterebbe un residuo, non il nostro errore.
    pg.wait_for_function("document.querySelectorAll('.toast').length === 0")
    # Il guasto e' un 200 col corpo non-JSON, non un 500: `risposta.json()`
    # solleva comunque (stesso ramo d'errore della vista), ma un 500 farebbe
    # scrivere a Chromium il SUO «Failed to load resource» in console — e il
    # collettore qui e' volutamente senza filtri (bloccante di Fable, sopra).
    with pg.expect_response('**/api/admin/requests'):
        trattenute.pop(0).fulfill(status=200, content_type='application/json',
                                  body='GuastoStantio: non-JSON di proposito')
    pg.evaluate(
        '() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))')
    assert pg.locator('.toast').count() == 0, (
        'l\'errore di una vista abbandonata e\' arrivato a toast sulla vista attiva')
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
