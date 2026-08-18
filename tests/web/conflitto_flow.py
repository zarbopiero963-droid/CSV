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

    # Dopo la sovrascrittura, la PROVA deve produrre il CSV atteso byte per
    # byte: intestazione, CRLF, quota localizzata (CodeRabbit, PR #71).
    INTESTAZIONE = ('"Provider","EventId","EventName","MarketId","MarketName",'
                    '"MarketType","SelectionId","SelectionName","Handicap",'
                    '"Price","MinPrice","MaxPrice","BetType","Points"')
    RIGA = ('"","","Juve - Milan","","","OVER_UNDER_15","","Over 1,5","",'
            '"1,85","","","PUNTA",""')
    # Nel DOM il parser HTML normalizza CRLF in LF (misurato): il confronto
    # qui e' sul testo MOSTRATO, BOM e quota localizzata compresi. I byte veri
    # con CRLF sono vincolati al layer relay (`tests/relay/test_csv_contract.py`
    # asserisce i byte della risposta HTTP).
    ATTESO = '\ufeff' + INTESTAZIONE + '\n' + RIGA + '\n'
    pg.fill('#test-msg', MSG)
    pg.click('[data-act="run-test"]')
    pg.wait_for_selector('#test-csv')
    csv = pg.eval_on_selector('#test-csv', 'el => el.textContent')
    assert csv == ATTESO, f'CSV della prova diverso dall\'atteso:\n{csv!r}\n{ATTESO!r}'

    # ------------------------ il conflitto sul TOGGLE Sospendi/Riattiva:
    # anche fuori dal wizard il 409 deve RIALLINEARE la cache — senza, ogni
    # toggle successivo rimanda la stessa versione vecchia e fallisce per
    # sempre (CodeRabbit, PR #71).
    pg.reload()
    pg.wait_for_selector('#test-msg')          # cache fresca alla versione V
    stato = pg.evaluate(
        """async ([slug, config]) => {
             const r = await fetch(`/api/me/parsers/${slug}`, {
               method: 'PUT',
               headers: {'Content-Type': 'application/json'},
               body: JSON.stringify({titolo: 'Conteso', config, active: true})});
             return r.status;
           }""", [slug, CONFIG_BASE])
    assert stato == 200, stato                  # «l'altra sessione»: ora V+1
    pg.click('[data-act="toggle-active"]')      # parte con V → 409
    pg.wait_for_selector('.toast:has-text("Modificato altrove")')
    pg.click('[data-act="toggle-active"]')      # cache riallineata: deve riuscire
    pg.wait_for_selector('.pill.off')
    shot(pg, '03-toggle-dopo-conflitto')

    # ---------------- le DUE SCHEDE: eliminato e ricreato mentre ero aperto (#75)
    # Il caso che la #74 aveva misurato e lasciato aperto: la scheda rimasta
    # indietro non deve sovrascrivere il parser NUOVO che porta lo stesso nome.
    # Qui la scheda vecchia e' il browser (cache con l'uid vecchio), e «l'altra
    # scheda» e' la coppia DELETE+POST via fetch, indistinguibile al server.
    pg.reload()
    pg.wait_for_selector('#test-msg')
    esito = pg.evaluate(
        """async ([slug, config]) => {
             const d = await fetch(`/api/me/parsers/${slug}`, {method: 'DELETE'});
             const c = await fetch('/api/me/parsers', {
               method: 'POST',
               headers: {'Content-Type': 'application/json'},
               body: JSON.stringify({titolo: 'Conteso', config, active: true})});
             const nuovo = await c.json();
             return [d.status, c.status, nuovo.slug, nuovo.uid];
           }""", [slug, CONFIG_BASE])
    assert esito[0] == 200 and esito[1] == 200, esito
    assert esito[2] == slug, f'il ricreato deve riprendere lo slug: {esito}'

    # La scheda vecchia salva: deve perdere, e con il motivo GIUSTO.
    pg.click('[data-act="wiz-save"]')
    pg.wait_for_selector('.toast')
    toast = pg.inner_text('.toast')
    assert 'Eliminato e ricreato altrove' in toast, \
        f'il conflitto di IDENTITA- va detto diverso da quello di versione: {toast!r}'
    salvata = config_sul_server(pg, slug)
    assert salvata['match']['value'] == 'P.Bet.', \
        f'il parser ricreato e- stato sovrascritto: {json.dumps(salvata)[:120]}'
    shot(pg, '04-eliminato-e-ricreato')

    b.close()

if errors:
    print('\n'.join(errors))
    sys.exit(1)
print('ok: il lost update e\' visibile e la sovrascrittura e\' una scelta')
