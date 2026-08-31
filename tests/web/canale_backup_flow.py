"""La card «Canale di backup» del pannello admin, pilotata da un browser vero (#56 pezzo 2).

Il backend (rotte `/api/admin/canale-backup`) e' coperto dai test del relay
(`tests/relay/test_canale_backup.py`, 16 casi). Qui si verifica il LATO UI, in un browser
vero: la card mostra i tre stati (candidato proposto → configurato → vuoto), la conferma
manda l'`chat_id` che la card ha MOSTRATO (precondizione dal client, bloccante di GPT-5.6 Sol
al gate finale #56), prova e rimozione chiamano le rotte giuste, e la console resta pulita.

Le quattro rotte del canale sono STUBBATE via Playwright: in questo ambiente l'invio Telegram
fallisce per costruzione (bot finto, proxy morto), quindi il percorso «configurato» non
sarebbe raggiungibile dal vivo — mentre app.js, api.js e il rendering della card sono TUTTI
reali, esercitati dal browser. Lo stub tiene uno stato server finto e si comporta come il
backend vero, incluso il 409 se la conferma porta un `chat_id` diverso dal candidato corrente.

Il viewport e' stretto (390px) di proposito: la card non deve far sfondare la pagina in
orizzontale (regola 2 di CLAUDE.md).

Argomenti: base_url, cartella screenshot, JSON con `{nome_cookie, admin_cookie}`.
"""

import json
import pathlib
import sys
import tempfile
from urllib.parse import urlsplit

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from playwright.sync_api import sync_playwright  # noqa: E402

from tests.runtime import apri_chromium  # noqa: E402

BASE = sys.argv[1]
OUT = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else pathlib.Path(tempfile.mkdtemp())
OUT.mkdir(parents=True, exist_ok=True)
DATI = json.loads(pathlib.Path(sys.argv[3]).read_text(encoding='utf-8'))

CANDIDATO = {'chat_id': '-1001222333444', 'titolo': 'Backup Piero'}
errors = []
corpi_conferma = []   # i corpi JSON ricevuti da POST .../conferma


def _console(m):
    if m.type == 'error':
        errors.append(f'console.{m.type}: {m.text}')


with sync_playwright() as pw:
    b = apri_chromium(pw)
    ctx = b.new_context(viewport={'width': 390, 'height': 850})   # mobile stretto
    pezzi = urlsplit(BASE)
    ctx.add_cookies([{'name': DATI['nome_cookie'], 'value': DATI['admin_cookie'],
                      'url': f'{pezzi.scheme}://{pezzi.netloc}/'}])
    pg = ctx.new_page()
    pg.on('console', _console)
    pg.on('pageerror', lambda e: errors.append(f'pageerror: {e}'))

    # Lo stato server finto: comincia con un candidato proposto, nessun configurato.
    stato = {'valore': {'configurato': None, 'candidato': dict(CANDIDATO)}}

    def stub_stato(route):
        route.fulfill(json=stato['valore'])

    def stub_rimuovi(route):
        stato['valore'] = {'configurato': None, 'candidato': None}
        route.fulfill(json=stato['valore'])

    def stub_conferma(route):
        corpo = route.request.post_data_json or {}
        corpi_conferma.append(corpo)
        corrente = stato['valore'].get('candidato') or {}
        # Come il server: si configura SOLO se il chat_id combacia col candidato corrente
        # (precondizione dal client). Altrimenti 409.
        if corpo.get('chat_id') != corrente.get('chat_id'):
            route.fulfill(status=409, json={'detail': 'il candidato e- cambiato'})
            return
        stato['valore'] = {'configurato': dict(corrente), 'candidato': None}
        route.fulfill(json=stato['valore'])

    def stub_prova(route):
        route.fulfill(json={'inviato': True})

    pg.route('**/api/admin/canale-backup', lambda r: (
        stub_rimuovi(r) if r.request.method == 'DELETE' else stub_stato(r)))
    pg.route('**/api/admin/canale-backup/conferma', stub_conferma)
    pg.route('**/api/admin/canale-backup/prova', stub_prova)

    pg.goto(BASE)
    pg.wait_for_selector('nav a[href="#/richieste"]')
    pg.click('nav a[href="#/richieste"]')

    # 1) Il candidato proposto: titolo e chat_id, col pulsante Conferma.
    card = pg.locator('.card', has_text='Canale di backup')
    card.wait_for()
    testo = card.inner_text()
    assert CANDIDATO['titolo'] in testo, f'titolo del candidato assente: {testo!r}'
    assert CANDIDATO['chat_id'] in testo, f'chat_id del candidato assente: {testo!r}'
    # La pagina non sfonda in orizzontale su schermo stretto (regola 2).
    assert pg.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1'), \
        'la pagina sfonda in orizzontale su schermo stretto'
    pg.screenshot(path=str(OUT / '01-candidato.png'), full_page=True)

    # 2) Conferma: la card manda l'chat_id MOSTRATO e passa a «configurato».
    pg.locator('[data-act="conferma-canale-backup"]').click()
    pg.wait_for_selector('.pill.on:has-text("configurato")')
    assert corpi_conferma, 'la conferma non ha mandato nessun corpo'
    assert corpi_conferma[-1].get('chat_id') == CANDIDATO['chat_id'], \
        f'la conferma non ha mandato l-chat_id mostrato: {corpi_conferma[-1]!r}'
    conf = pg.locator('.card', has_text='Canale di backup').inner_text()
    assert CANDIDATO['titolo'] in conf, f'il configurato non mostra il titolo: {conf!r}'
    pg.screenshot(path=str(OUT / '02-configurato.png'), full_page=True)

    # 3) Prova sul configurato: un toast di conferma, console pulita.
    pg.locator('[data-act="prova-canale-backup"]').click()
    pg.wait_for_selector('.toast')

    # 4) Rimozione, con conferma nel modale: si torna allo stato vuoto.
    pg.locator('[data-act="rimuovi-canale-backup"]').click()
    pg.wait_for_selector('[data-act="rimuovi-canale-backup-ok"]')
    pg.click('[data-act="rimuovi-canale-backup-ok"]')
    pg.wait_for_selector('.card:has-text("Nessun canale configurato")')
    pg.screenshot(path=str(OUT / '03-rimosso.png'), full_page=True)

    ctx.close()
    b.close()

print('\nERRORI JS:', len(errors))
for e in errors:
    print(' -', e)
sys.exit(1 if errors else 0)
