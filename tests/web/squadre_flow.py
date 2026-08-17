"""Le sorgenti squadre (#34, pezzo 2) pilotate da un browser vero, sul relay vero.

Il percorso del cliente sugli sketch approvati (13/08): sezione VUOTA al primo
login; lo sport nasce nella libreria mercati (#33) perche' le competizioni
vivono sotto di lui; poi la competizione (Serie A), le squadre Betfair salvate
UNA volta (col doppione rifiutato), la prima sorgente con la tabella a due
colonne Betfair<->alias, la seconda sorgente che TROVA le stesse squadre senza
ridigitarle, il badge «compilati», la «⌫ alias» che svuota solo una sorgente,
la rinomina, la «× squadra» che sparisce da TUTTE le sorgenti, l'eliminazione
della sorgente che NON porta via le squadre. Chiusura a 390px senza scroll
orizzontale.

Esce diverso da zero al primo problema. Zero errori in console, come tutti i
flussi web di questo repository.
"""

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
# I 409 PROVOCATI dal test: la squadra doppia. Chromium logga il fetch fallito
# come errore di risorsa, ma e' il contratto sotto verifica.
ROTTE_409_ATTESE = ('/squadre',)
# E i 404 degli hash morti (id inventato 999999): sono il fallback sotto verifica.
ROTTE_404_ATTESE = ('/999999',)


def _console(m):
    if m.type != 'error':
        return
    if 'Failed to load resource' in m.text:
        url = (m.location or {}).get('url', '')
        percorso = url.split('?')[0]
        if '401' in m.text and any(percorso.endswith(r) for r in ROTTE_401_ATTESE):
            return
        if '409' in m.text and any(percorso.endswith(r) for r in ROTTE_409_ATTESE):
            return
        if '404' in m.text and any(percorso.endswith(r) for r in ROTTE_404_ATTESE):
            return
    errors.append(f'console.{m.type}: {m.text}')


def shot(page, name):
    page.screenshot(path=str(OUT / f'{name}.png'), full_page=True)
    print('shot', name)


def alias_visibili(pg):
    """La tabella della sorgente aperta: {squadra: alias} dagli input."""
    coppie = {}
    for riga in pg.query_selector_all('[data-squadra]'):
        squadra = riga.get_attribute('data-nome')
        coppie[squadra] = riga.input_value()
    return coppie


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

    # ------------------------------------------------ la sezione, vuota
    pg.click('nav a[href="#/squadre"]')
    pg.wait_for_selector('[data-act="comp-new"]')
    assert 'Non hai ancora competizioni' in pg.inner_text('#app'), \
        'al primo login la sezione deve essere vuota'
    shot(pg, 'squadre-vuoto')

    # Senza sport la modale deve DIRE dove si crea, non fallire in silenzio.
    pg.click('[data-act="comp-new"]')
    pg.wait_for_selector('.modal')
    assert 'Mercati Betfair' in pg.inner_text('.modal'), \
        'senza sport la modale deve rimandare alla libreria (#33)'
    pg.click('.modal [data-act="close"]')

    # Lo sport nasce nella libreria (#33): il percorso REALE del cliente.
    pg.click('nav a[href="#/mercati"]')
    pg.wait_for_selector('[data-act="sport-new"]')
    pg.click('[data-act="sport-new"]')
    pg.fill('#ns-nome', 'Calcio')
    pg.click('[data-act="sport-create"]')
    pg.wait_for_selector('a.name:has-text("Calcio")')

    # ------------------------------------------ competizione + squadre
    pg.click('nav a[href="#/squadre"]')
    pg.wait_for_selector('[data-act="comp-new"]')
    pg.click('[data-act="comp-new"]')
    pg.wait_for_selector('#nc-sport')
    pg.select_option('#nc-sport', label='Calcio')
    pg.fill('#nc-nome', 'Serie A')
    pg.click('[data-act="comp-create"]')
    pg.wait_for_selector('a.name:has-text("Serie A")')
    pg.click('a.name:has-text("Serie A")')
    pg.wait_for_selector('#sq-nome')
    cid = pg.evaluate('location.hash').split('/')[2]

    for squadra in ('Juventus', 'AC Milan'):
        pg.fill('#sq-nome', squadra)
        pg.click('[data-act="sq-add"]')
        pg.wait_for_selector(f'.list-item .name:text-is("{squadra}")')

    # Il doppione e' un 409 col motivo VISIBILE, non un fallimento muto.
    pg.fill('#sq-nome', 'Juventus')
    pg.click('[data-act="sq-add"]')
    pg.wait_for_selector('#sq-err:has-text("presente")')
    shot(pg, 'squadre-competizione')

    # ------------------------------------------------- la prima sorgente
    pg.click('[data-act="src-new"]')
    pg.fill('#nsrc-nome', 'test 1')
    pg.click('[data-act="src-create"]')
    # Il pulsante della sorgente porta il badge: 0 alias su 2 squadre.
    pg.wait_for_selector('a.src-btn:has-text("test 1")')
    assert '0/2' in pg.inner_text('a.src-btn:has-text("test 1")')

    pg.click('a.src-btn:has-text("test 1")')
    pg.wait_for_selector('[data-act="alias-save"]')
    assert alias_visibili(pg) == {'AC Milan': '', 'Juventus': ''}, \
        'la tabella parte dalle squadre Betfair della competizione, alias vuoti'
    pg.fill('[data-nome="Juventus"]', 'Juve')
    pg.fill('[data-nome="AC Milan"]', 'Milan')
    pg.click('[data-act="alias-save"]')
    pg.wait_for_selector('.toast:has-text("Alias salvati")')
    shot(pg, 'squadre-alias')

    # Il badge si aggiorna: 2/2.
    pg.click('.crumb a:has-text("Serie A")')
    pg.wait_for_selector('a.src-btn:has-text("test 1")')
    assert '2/2' in pg.inner_text('a.src-btn:has-text("test 1")')

    # ------------------------- la seconda sorgente RIUSA la lista Betfair
    pg.click('[data-act="src-new"]')
    pg.fill('#nsrc-nome', 'fonte B')
    pg.click('[data-act="src-create"]')
    pg.wait_for_selector('a.src-btn:has-text("fonte B")')
    pg.click('a.src-btn:has-text("fonte B")')
    pg.wait_for_selector('[data-act="alias-save"]')
    assert alias_visibili(pg) == {'AC Milan': '', 'Juventus': ''}, \
        'la seconda sorgente vede le STESSE squadre, senza ridigitarle'
    pg.fill('[data-nome="Juventus"]', 'JUV')
    pg.click('[data-act="alias-save"]')
    pg.wait_for_selector('.toast:has-text("Alias salvati")')

    # ------------------------------------ ⌫ alias: svuota SOLO una sorgente
    pg.click('.crumb a:has-text("Serie A")')
    pg.wait_for_selector('a.src-btn:has-text("test 1")')
    pg.click('a.src-btn:has-text("test 1")')
    pg.wait_for_selector('[data-act="alias-save"]')
    pg.click('[data-act="alias-clear"][data-nome="Juventus"]')
    pg.wait_for_selector('[data-nome="Juventus"][data-vuoto="1"]')
    assert alias_visibili(pg)['Juventus'] == ''
    assert alias_visibili(pg)['AC Milan'] == 'Milan', 'le altre righe non si muovono'
    pg.click('.crumb a:has-text("Serie A")')
    pg.wait_for_selector('a.src-btn:has-text("fonte B")')
    pg.click('a.src-btn:has-text("fonte B")')
    pg.wait_for_selector('[data-act="alias-save"]')
    assert alias_visibili(pg)['Juventus'] == 'JUV', \
        'la ⌫ su test 1 non deve toccare fonte B'

    # ------------------------------------------------ rinomina la sorgente
    pg.click('[data-act="src-ren"]')
    pg.fill('#rsrc-nome', 'canale X')
    pg.click('[data-act="src-ren-ok"]')
    pg.wait_for_selector('.crumb:has-text("canale X")')

    # ------------------------- × squadra: via da TUTTE le sorgenti, conferma
    pg.click('.crumb a:has-text("Serie A")')
    pg.wait_for_selector('.list-item .name:text-is("Juventus")')
    pg.click('[data-act="sq-del"][data-nome="Juventus"]')
    pg.wait_for_selector('.modal:has-text("tutte le sorgenti")')
    pg.click('[data-act="sq-del-ok"]')
    # La scomparsa della RIGA, non la presenza dell'altra: AC Milan stava gia'
    # nel DOM vecchio e il render e' asincrono — aspettare lei non prova niente.
    pg.wait_for_selector('.list-item .name:text-is("Juventus")', state='detached')
    pg.wait_for_selector('.list-item .name:text-is("AC Milan")')
    pg.click('a.src-btn:has-text("canale X")')
    pg.wait_for_selector('[data-act="alias-save"]')
    assert alias_visibili(pg) == {'AC Milan': ''}, \
        'la × squadra porta via anche gli alias della sorgente rinominata'

    # --------------------- eliminare la sorgente NON porta via le squadre
    pg.click('[data-act="src-del"]')
    pg.wait_for_selector('.modal:has-text("restano")')
    pg.click('[data-act="src-del-ok"]')
    pg.wait_for_selector('[data-act="src-new"]')
    assert 'canale X' not in pg.inner_text('#app')
    assert pg.locator('.list-item .name:text-is("AC Milan")').count() == 1, \
        'le squadre Betfair sopravvivono alla sorgente'
    shot(pg, 'squadre-finale')

    # ------------------- gli hash morti RISALGONO, non si piantano (CodeRabbit)
    # Una competizione eliminata da un altro dispositivo, o un segnalibro
    # stantio: il 404 del server deve riportare al livello sopra, non lasciare
    # la pagina su «Caricamento…».
    pg.goto(BASE + '#/squadre/999999')
    pg.wait_for_selector('[data-act="comp-new"]')      # risaliti all'elenco
    pg.goto(BASE + f'#/squadre/{cid}/999999')
    pg.wait_for_selector('[data-act="src-new"]')       # risaliti alla competizione
    # E il caso a DUE salti (CodeRabbit): l'hash alias di una competizione
    # morta risale prima alla competizione (ancora 404) e poi all'elenco.
    pg.goto(BASE + '#/squadre/999999/999999')
    pg.wait_for_selector('[data-act="comp-new"]')      # due 404, due salti, elenco

    # ------------------------------------------------ 390px, zero h-scroll
    pg.set_viewport_size({'width': 390, 'height': 844})
    pg.wait_for_timeout(200)
    troppo = pg.evaluate(
        'document.documentElement.scrollWidth - document.documentElement.clientWidth')
    assert troppo <= 0, f'scroll orizzontale a 390px: {troppo}px di troppo'
    shot(pg, 'squadre-390')

    b.close()

if errors:
    print('ERRORI CONSOLE/PAGINA:')
    for e in errors:
        print(' -', e)
    sys.exit(1)
print('OK squadre_flow')
