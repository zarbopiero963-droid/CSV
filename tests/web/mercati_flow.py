"""La libreria mercati Betfair (#33) pilotata da un browser vero, sul relay vero.

Il percorso del cliente, per intero: primo login con la sezione VUOTA (nessun
catalogo precompilato), creazione di uno sport, di due mercati — uno da due
selezioni (Over/Under), uno da molte (il caso «Risultato esatto») — piu' un
mercato handicap coi segnaposto squadra; poi il wizard del parser in modalita'
«Da mercati Betfair» a due passi: mercato → risultato, con MarketName compilato
da solo, la selezione handicap SPENTA (serve la #34), la prova messaggio sul
server e la persistenza del riferimento `betfair` nella config salvata.

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

MSG = 'SEGNALE\nevento: Juventus v Palermo'

errors = []
ROTTE_401_ATTESE = ('/api/me',)
# Il 409 della selezione duplicata e' PROVOCATO dal test (vedi sotto): Chromium
# logga il fetch fallito come errore di risorsa, ma e' il contratto che si sta
# verificando, non un difetto della pagina.
ROTTE_409_ATTESE = ('/selezioni',)


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
    errors.append(f'console.{m.type}: {m.text}')


def shot(page, name):
    page.screenshot(path=str(OUT / f'{name}.png'), full_page=True)
    print('shot', name)


def crea_mercato(pg, tipo, nome):
    pg.click('[data-act="mercato-new"]')
    pg.fill('#nm-type', tipo)
    pg.fill('#nm-name', nome)
    pg.click('[data-act="mercato-create"]')
    pg.wait_for_selector(f'a.name:has-text("{tipo}")')


def aggiungi_selezioni(pg, mercato, *selezioni):
    pg.click(f'a.name:has-text("{mercato}")')
    pg.wait_for_selector('#sel-nome')
    for s in selezioni:
        pg.fill('#sel-nome', s)
        pg.click('[data-act="sel-add"]')
        pg.wait_for_selector(f'.list-item .name:text-is("{s}")')
    pg.go_back()
    pg.wait_for_selector('[data-act="mercato-new"]')


with sync_playwright() as pw:
    b = apri_chromium(pw)
    pg = b.new_page(viewport={'width': 1420, 'height': 1000})
    pg.on('console', _console)
    pg.on('pageerror', lambda e: errors.append(f'pageerror: {e}'))

    # Login a password (porta di riserva, la stessa del flusso principale).
    pg.goto(BASE)
    pg.wait_for_selector('#login-pass')
    pg.fill('#login-user', UTENTE_PROVA)
    pg.fill('#login-pass', PASSWORD_PROVA)
    pg.click('[data-act="login-password"]')
    pg.wait_for_selector('nav a[href="#/mercati"]')

    # ---- primo accesso: VUOTO, nessun catalogo precompilato --------------
    pg.click('nav a[href="#/mercati"]')
    pg.wait_for_selector('[data-act="sport-new"]')
    assert pg.is_visible('.empty'), 'la libreria non parte vuota'
    shot(pg, '01-mercati-vuoto')

    # ---- crea lo sport ---------------------------------------------------
    pg.click('[data-act="sport-new"]')
    pg.fill('#ns-nome', 'Calcio')
    pg.click('[data-act="sport-create"]')
    pg.wait_for_selector('a.name:has-text("Calcio")')

    pg.click('a.name:has-text("Calcio")')
    pg.wait_for_selector('[data-act="mercato-new"]')
    shot(pg, '02-sport-calcio')

    # ---- mercati: due selezioni e molte selezioni ------------------------
    crea_mercato(pg, 'OVER_UNDER_05HT', 'Over/Under 0.5 Goals HT')
    aggiungi_selezioni(pg, 'OVER_UNDER_05HT', 'Over 0,5 goal', 'Under 0,5 goal')

    crea_mercato(pg, 'CORRECT_SCORE', 'Risultato esatto')
    aggiungi_selezioni(pg, 'CORRECT_SCORE', '0 - 0', '1 - 0', '2 - 0', '2 - 1', '3 - 0')

    # Il mercato handicap coi segnaposto squadra: si PUO' creare (#33)...
    crea_mercato(pg, 'TEAM_A_1', '{HOME_TEAM} +1')
    aggiungi_selezioni(pg, 'TEAM_A_1', 'Pareggio · {HOME_TEAM} +1 · {AWAY_TEAM} -1')
    shot(pg, '03-tre-mercati')

    # ---- la selezione duplicata viene rifiutata con il motivo ------------
    pg.click('a.name:has-text("OVER_UNDER_05HT")')
    pg.wait_for_selector('#sel-nome')
    pg.fill('#sel-nome', 'Over 0,5 goal')
    pg.click('[data-act="sel-add"]')
    pg.wait_for_selector('#sel-err:has-text("presente")')
    # Si contano le RIGHE-SELEZIONE (il loro bottone di elimina), non ogni
    # .list-item della pagina: una riga estranea futura non deve far accusare
    # il controllo del doppione. Segnalato da CodeRabbit sulla PR #55.
    quante = pg.locator('.list-item [data-act="sel-del"]').count()
    assert quante == 2, f'il doppione ha cambiato la lista: {quante} selezioni'

    # ...e l'eliminazione di UNA selezione non tocca l'altra.
    pg.click('.list-item:has-text("Under 0,5 goal") [data-act="sel-del"]')
    pg.wait_for_selector('.list-item .name:text-is("Under 0,5 goal")', state='detached')
    # Aspetta il RIATTACCO di "Over" prima di contarlo: l'eliminazione ricostruisce la lista
    # (clear + rebuild), e fra i due "Over" e' transitoriamente staccato. Contarlo subito e'
    # una corsa che su un runner CI carico perde (count()==0). Stesso schema gia' usato in
    # squadre_flow.py dopo un'eliminazione.
    pg.wait_for_selector('.list-item .name:text-is("Over 0,5 goal")')
    assert pg.locator('.list-item .name:text-is("Over 0,5 goal")').count() == 1
    # La si rimette: serve al wizard qui sotto.
    pg.fill('#sel-nome', 'Under 0,5 goal')
    pg.click('[data-act="sel-add"]')
    pg.wait_for_selector('.list-item .name:text-is("Under 0,5 goal")')
    shot(pg, '04-selezioni')

    # ---- il wizard a due passi -------------------------------------------
    pg.click('nav a[href="#/parsers"]')
    pg.click('[data-act="new-parser"]')
    pg.fill('#np-name', 'Da libreria')
    pg.click('[data-act="create-parser"]')
    pg.wait_for_selector('#paste-msg')
    pg.fill('#paste-msg', MSG)
    pg.click('[data-act="start-wizard"]')
    pg.wait_for_selector('#match-val')
    pg.fill('#match-val', 'SEGNALE')

    # Condizione → colonne. EventName (passo 3) dal messaggio, cliccando il
    # frammento: quattro obbligatorie tutte costanti sarebbero rifiutate (#41).
    pg.click('[data-act="save-match"]')        # -> 1 Provider
    pg.click('[data-act="wiz-next"]')          # -> 2 EventId
    pg.click('[data-act="wiz-next"]')          # -> 3 EventName
    pg.wait_for_selector('.bubble:has-text("EventName")')
    pg.click('.frag:has-text("evento:")')
    pg.click('[data-act="wiz-next"]')          # -> 4 MarketId
    pg.click('[data-act="wiz-next"]')          # -> 5 MarketName
    pg.click('[data-act="wiz-next"]')          # -> 6 MarketType
    pg.wait_for_selector('.bubble:has-text("MarketType")')

    # Il tab «Da mercati Betfair» esiste SOLO qui, e apre il passo ①.
    pg.click('[data-act="wiz-mode"][data-mode="betfair"]')
    pg.wait_for_selector('[data-act="bf-market"]')
    shot(pg, '05-passo-1-mercati')

    # La selezione handicap e' SPENTA col motivo (#34).
    pg.click('[data-act="bf-market"]:has-text("TEAM_A_1")')
    pg.wait_for_selector('button.frag[disabled]:has-text("sorgente squadre")')

    # Passo ① sul mercato buono → passo ② solo con le selezioni create.
    pg.click('[data-act="bf-market"]:has-text("OVER_UNDER_05HT")')
    pg.wait_for_selector('[data-act="bf-selection"]')
    visibili = pg.locator('[data-act="bf-selection"]').all_text_contents()
    assert sorted(v.strip() for v in visibili) == ['Over 0,5 goal', 'Under 0,5 goal'], \
        f'la tendina non mostra le selezioni create: {visibili}'
    pg.click('[data-act="bf-selection"]:has-text("Over 0,5 goal")')
    pg.wait_for_selector('.banner.ok')
    shot(pg, '06-passo-2-scelto')

    # MarketName si e' compilato DA SOLO: lo dice l'anteprima a fianco.
    anteprima = pg.locator('.sticky-pane').inner_text()
    for atteso in ('OVER_UNDER_05HT', 'Over/Under 0.5 Goals HT', 'Over 0,5 goal'):
        assert atteso in anteprima, f'l\'anteprima non mostra {atteso!r}'

    # Avanti fino a BetType (passo 13: indice 12 di COLUMNS), poi Points e riepilogo.
    for _ in range(7):
        pg.click('[data-act="wiz-next"]')
    pg.wait_for_selector('.bubble:has-text("BetType")')
    pg.click('[data-act="wiz-mode"][data-mode="constant"]')
    pg.fill('#rule-const', 'PUNTA')
    pg.click('[data-act="wiz-next"]')           # -> 14 Points
    pg.click('[data-act="wiz-next"]')           # -> riepilogo
    pg.wait_for_selector('#test-msg')

    # Prova sul SERVER: la riga porta i tre valori della libreria.
    pg.fill('#test-msg', MSG)
    pg.click('[data-act="run-test"]')
    pg.wait_for_selector('#test-csv')
    csv_out = pg.locator('#test-csv').inner_text()
    for atteso in ('"OVER_UNDER_05HT"', '"Over/Under 0.5 Goals HT"', '"Over 0,5 goal"'):
        assert atteso in csv_out, f'nel CSV manca {atteso}: {csv_out!r}'
    shot(pg, '07-prova-server')

    # Salva e verifica la PERSISTENZA del riferimento betfair nella config.
    pg.click('[data-act="wiz-save"]')
    # Il toast SPECIFICO del salvataggio: aspettare un `.toast` qualunque
    # passerebbe su quello della scelta betfair ancora nel DOM, e la fetch qui
    # sotto leggerebbe la config PRECEDENTE. Segnalato da CodeRabbit, PR #55.
    pg.wait_for_selector('.toast:has-text("Configurazione salvata")',
                         state='attached', timeout=5000)
    salvato = pg.evaluate(
        "fetch('/api/me/parsers').then(r => r.json())")
    parser = next(p for p in salvato if p['titolo'] == 'Da libreria')
    assert parser['config'].get('betfair'), 'il riferimento betfair non e\' stato salvato'
    assert parser['config']['columns']['MarketName']['value'] == 'Over/Under 0.5 Goals HT'
    print('betfair salvato:', json.dumps(parser['config']['betfair']))

    # ---- cache FREDDA: il tab betfair carica la libreria DA SOLO ---------
    # Il [Major] di CodeRabbit sulla PR #55: entrando nel wizard senza aver
    # visitato #/mercati, il loader fotografava `generazione` prima del render
    # dell'azione e scartava il proprio render di completamento — il passo
    # restava su «Caricamento della tua libreria…» per sempre.
    pg.reload()
    pg.wait_for_selector('[data-act="wiz-goto"]')       # riepilogo (campione salvato)
    pg.click('[data-act="wiz-goto"][data-i="5"]')       # -> passo MarketType
    pg.wait_for_selector('.bubble:has-text("MarketType")')
    pg.click('[data-act="wiz-mode"][data-mode="betfair"]')
    pg.wait_for_selector('[data-act="bf-market"]', timeout=10000)
    shot(pg, '08-cache-fredda')

    # ---- niente scorrimento orizzontale a 390 px sulla libreria ----------
    # STESSA pagina (il cookie di sessione vive nel suo contesto): si cambia
    # solo il viewport, come farebbe una rotazione del telefono.
    pg.set_viewport_size({'width': 390, 'height': 844})
    pg.goto(BASE + '#/mercati/calcio')
    pg.wait_for_selector('[data-act="mercato-new"]')
    largo = pg.evaluate('document.documentElement.scrollWidth')
    visibile = pg.evaluate('document.documentElement.clientWidth')
    assert largo <= visibile + 1, f'la libreria scorre in orizzontale ({largo} > {visibile})'
    shot(pg, '09-mercati-390')

    pg.close()
    b.close()

print('errori in console:', errors)
if errors:
    sys.exit(1)
print('OK')
