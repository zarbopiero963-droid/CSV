"""Il giro completo del cliente, in browser vero, sul relay vero (documentazione).

Dalla libreria mercati alla sorgente squadre al parser, fino alla prova sul
server e al token del feed: il percorso che un cliente nuovo fa la prima volta,
nell'ordine che conta (prima cio' a cui il parser si appoggia, poi il parser).
Ogni passaggio e' uno screenshot numerato in `OUT`.

Non e' una simulazione: apre Chromium e clicca quello che cliccherebbe una
persona. Esce diverso da zero al primo problema e pretende ZERO errori in
console, come tutti i flussi web di questo repository.

Lanciato da `test_giro_web.py`, che avvia `main:app`. Da solo:

    python tests/web/giro_flow.py http://127.0.0.1:8099/app/ /una/cartella

Headless di default (cosi' gira in CI senza display). Per rigenerare la
galleria di screenshot «a browser vero» come chiesto dal proprietario:

    GIRO_HEADED=1 xvfb-run -a python tests/web/giro_flow.py <base> <out>
"""

import json
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from playwright.sync_api import sync_playwright  # noqa: E402

from tests.runtime import apri_chromium  # noqa: E402
from tests.web.credenziali_prova import PASSWORD_PROVA, UTENTE_PROVA  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8099/app/'
OUT = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else pathlib.Path(tempfile.mkdtemp())
OUT.mkdir(parents=True, exist_ok=True)

# Il messaggio di prova, uguale dal primo all'ultimo passo. Il marcatore e' il
# vero 🆚 (U+1F19A): confrontato per codepoint, non per aspetto (REGOLA CODIFICA).
MSG = 'P.Bet. PREMACHT 0,5HT\n\U0001F19A Juve v Milan\n@ 1.42'

errors = []
passi = []

# I due 4xx ATTESI e provocati dal giro stesso, vincolati alla ROTTA — non al
# solo codice. Lo status sta in `m.text`, l'URL in `m.location`: un filtro sul
# solo testo ingoierebbe QUALSIASI 401/409, incluso un 401 di regressione su
# `/api/me/parsers` (la classe leak-fra-utenti, invariante numero uno del repo)
# o un 409 di collisione slug su una create. Stesso filtro route-bound di
# `mercati_flow.py` e `squadre_flow.py`, fonte unica del pattern (regola 3).
#   - 401: solo la prima `/api/me` prima del login (nessuna sessione);
#   - 409: solo il doppione di selezione, mostrato apposta al passo 08.
ROTTE_401_ATTESE = ('/api/me',)
ROTTE_409_ATTESE = ('/selezioni',)


def _console(m):
    if m.type != 'error':
        return
    if 'Failed to load resource' in m.text:
        percorso = (m.location or {}).get('url', '').split('?')[0]
        if '401' in m.text and any(percorso.endswith(r) for r in ROTTE_401_ATTESE):
            return
        if '409' in m.text and any(percorso.endswith(r) for r in ROTTE_409_ATTESE):
            return
    errors.append(f'console.{m.type}: {m.text}')


def shot(pg, nome, titolo, nota):
    n = len(passi) + 1
    file = f'{n:02d}-{nome}.png'
    pg.screenshot(path=str(OUT / file), full_page=True)
    passi.append({'n': n, 'file': file, 'titolo': titolo, 'nota': nota})
    print(f'[{n:02d}] {titolo}', flush=True)


with sync_playwright() as pw:
    b = apri_chromium(pw, headless=not os.environ.get('GIRO_HEADED'))
    pg = b.new_page(viewport={'width': 1400, 'height': 950})
    pg.on('console', _console)
    pg.on('pageerror', lambda e: errors.append(f'pageerror: {e}'))

    # ============================================================ A. accesso
    pg.goto(BASE)
    pg.wait_for_selector('#login-user')
    shot(pg, 'login', "La porta d'ingresso",
         "L'app vive su /app/. Senza sessione mostra l'accesso: la porta "
         "principale e' Telegram, quella a password e' la riserva.")

    pg.fill('#login-user', UTENTE_PROVA)
    pg.fill('#login-pass', PASSWORD_PROVA)
    pg.click('[data-act="login-password"]')
    pg.wait_for_selector('.stats')
    shot(pg, 'dashboard', 'Dentro',
         'Il riepilogo dell\'account. Il parser gia\' presente e\' quello storico '
         'della produzione: il database di prova nasce coi dati veri, non vuoto.')

    # ============================================================ B. mercati
    pg.click('nav a[href="#/mercati"]')
    pg.wait_for_selector('[data-act="sport-new"]')
    assert pg.is_visible('.empty'), 'la libreria mercati non parte vuota'
    shot(pg, 'mercati-vuoto', 'Mercati: si parte da zero',
         'Nessun catalogo precaricato. La libreria e\' quella che costruisci, '
         'cosi\' i codici sono quelli che il TUO XTrader usa davvero.')

    pg.click('[data-act="sport-new"]')
    pg.wait_for_selector('#ns-nome')
    pg.fill('#ns-nome', 'Calcio')
    shot(pg, 'sport-modale', 'Nuovo sport',
         'Primo livello dei tre: sport -> mercato -> selezioni.')

    pg.click('[data-act="sport-create"]')
    pg.wait_for_selector('a.name:has-text("Calcio")')
    pg.click('a.name:has-text("Calcio")')
    pg.wait_for_selector('[data-act="mercato-new"]')
    shot(pg, 'sport-dentro', 'Dentro lo sport',
         'Calcio esiste e non ha ancora mercati.')

    pg.click('[data-act="mercato-new"]')
    pg.wait_for_selector('#nm-type')
    pg.fill('#nm-type', 'OVER_UNDER_15')
    pg.fill('#nm-name', 'Over/Under 1.5 Goals')
    shot(pg, 'mercato-modale', 'Nuovo mercato',
         'Due campi distinti: marketType e\' il CODICE su cui XTrader agisce, '
         'marketName e\' l\'etichetta leggibile. Nel CSV finiscono entrambi.')

    pg.click('[data-act="mercato-create"]')
    pg.wait_for_selector('a.name:has-text("OVER_UNDER_15")')
    pg.click('a.name:has-text("OVER_UNDER_15")')
    pg.wait_for_selector('#sel-nome')
    for s in ('Over 1,5 goal', 'Under 1,5 goal'):
        pg.fill('#sel-nome', s)
        pg.click('[data-act="sel-add"]')
        pg.wait_for_selector(f'.list-item .name:text-is("{s}")')
    shot(pg, 'selezioni', 'Le selezioni del mercato',
         'Sono le voci che il wizard offrira\' al passo 2. La virgola nei nomi '
         'e\' quella che XTrader legge.')

    # il doppione viene rifiutato col motivo (provoca il 409 filtrato sopra)
    pg.fill('#sel-nome', 'Over 1,5 goal')
    pg.click('[data-act="sel-add"]')
    pg.wait_for_selector('#sel-err:has-text("presente")')
    quante = pg.locator('.list-item [data-act="sel-del"]').count()
    assert quante == 2, f'il doppione ha cambiato la lista: {quante} selezioni'
    shot(pg, 'selezione-doppia', 'Il doppione viene rifiutato',
         'Non in silenzio: il messaggio dice perche\'. Un doppione esatto e\' '
         'sempre un errore di battitura.')

    pg.go_back()
    pg.wait_for_selector('[data-act="mercato-new"]')
    pg.click('[data-act="mercato-new"]')
    pg.wait_for_selector('#nm-type')
    pg.fill('#nm-type', 'CORRECT_SCORE')
    pg.fill('#nm-name', 'Risultato esatto')
    pg.click('[data-act="mercato-create"]')
    pg.wait_for_selector('a.name:has-text("CORRECT_SCORE")')
    pg.click('a.name:has-text("CORRECT_SCORE")')
    pg.wait_for_selector('#sel-nome')
    for s in ('2 - 0', '2 - 1', '3 - 0'):
        pg.fill('#sel-nome', s)
        pg.click('[data-act="sel-add"]')
        pg.wait_for_selector(f'.list-item .name:text-is("{s}")')
    pg.go_back()
    pg.wait_for_selector('[data-act="mercato-new"]')
    shot(pg, 'due-mercati', 'La libreria con due mercati',
         'OVER_UNDER_15 per la riga base, CORRECT_SCORE per i punteggi multipli.')

    # ============================================================ C. squadre
    pg.click('nav a[href="#/squadre"]')
    pg.wait_for_selector('[data-act="comp-new"]')
    shot(pg, 'squadre-vuoto', 'Sorgenti squadre: si parte da zero',
         'Qui vivono i nomi Betfair e gli alias con cui i canali Telegram '
         'chiamano le stesse squadre.')

    pg.click('[data-act="comp-new"]')
    pg.wait_for_selector('#nc-sport')
    pg.select_option('#nc-sport', label='Calcio')
    pg.fill('#nc-nome', 'Serie A')
    shot(pg, 'competizione-modale', 'Nuova competizione',
         'La competizione appartiene a uno sport: la tendina mostra quelli '
         'creati nella libreria mercati. I due pezzi sono collegati.')

    pg.click('[data-act="comp-create"]')
    pg.wait_for_selector('a.name:has-text("Serie A")')
    pg.click('a.name:has-text("Serie A")')
    pg.wait_for_selector('#sq-nome')
    for squadra in ('Juventus', 'AC Milan'):
        pg.fill('#sq-nome', squadra)
        pg.click('[data-act="sq-add"]')
        pg.wait_for_selector(f'.list-item .name:text-is("{squadra}")')
    shot(pg, 'squadre-betfair', 'I nomi Betfair',
         'Questa e\' la lista CANONICA: gli unici nomi che finiranno nel CSV. '
         'Gli alias vengono dopo, e stanno sopra questa lista.')

    pg.click('[data-act="src-new"]')
    pg.wait_for_selector('#nsrc-nome')
    pg.fill('#nsrc-nome', 'Canale di Marco')
    shot(pg, 'sorgente-modale', 'Nuova sorgente',
         'Una sorgente = un canale Telegram che chiama le squadre a modo suo. '
         'Ne servono tante quante sono i canali con nomi diversi.')

    pg.click('[data-act="src-create"]')
    pg.wait_for_selector('a.src-btn:has-text("Canale di Marco")')
    pg.click('a.src-btn:has-text("Canale di Marco")')
    pg.wait_for_selector('[data-act="alias-save"]')
    shot(pg, 'alias-vuoti', 'La colonna degli alias, vuota',
         'A sinistra i nomi Betfair, a destra come li scrive quel canale. '
         'Un alias per squadra, per sorgente.')

    pg.fill('[data-nome="Juventus"]', 'Juve')
    pg.fill('[data-nome="AC Milan"]', 'Milan')
    pg.click('[data-act="alias-save"]')
    pg.wait_for_selector('.toast:has-text("Alias salvati")')
    shot(pg, 'alias-salvati', 'Alias salvati',
         'Da adesso «Juve» diventa «Juventus» e «Milan» diventa «AC Milan» nel '
         'feed, ma solo per i parser che scelgono questa sorgente.')

    # ============================================================ D. parser
    pg.click('nav a[href="#/parsers"]')
    pg.wait_for_selector('[data-act="new-parser"]')
    pg.click('[data-act="new-parser"]')
    pg.wait_for_selector('#np-name')
    pg.fill('#np-name', 'Canale di Marco')
    shot(pg, 'parser-modale', 'Nuovo parser',
         'Il titolo e\' quello che vedi tu. Lo slug che ne deriva e\' l\'identita\' '
         'stabile, e finisce nell\'URL del feed.')

    pg.click('[data-act="create-parser"]')
    pg.wait_for_selector('#paste-msg')
    pg.fill('#paste-msg', MSG)
    shot(pg, 'incolla-messaggio', 'Si incolla un messaggio vero',
         'Il wizard non chiede di descrivere il formato: chiede un esempio. Da '
         'li\' propone la condizione e le regole.')

    pg.click('[data-act="ai-suggest"]')
    pg.wait_for_selector('.map-table', timeout=8000)
    shot(pg, 'suggerimento', 'Il suggeritore ha proposto la mappa',
         'Ha riconosciuto il marcatore emoji per l\'evento e la quota dopo @. '
         'Le colonne che non sa dedurre le lascia vuote, invece di inventarle.')

    def _colonna():
        return pg.inner_text('.bubble.ai strong.mono')

    def _avanti():
        prima = _colonna()
        pg.click('[data-act="wiz-next"]')
        pg.wait_for_function(
            "p => { const e = document.querySelector('.bubble.ai strong.mono');"
            " return e && e.textContent !== p; }", arg=prima)

    pg.click('[data-act="wiz-goto"][data-i="5"]')     # MarketType
    pg.wait_for_selector('[data-act="wiz-mode"][data-mode="betfair"]')
    pg.click('[data-act="wiz-mode"][data-mode="betfair"]')
    pg.wait_for_selector('[data-act="bf-market"]')
    shot(pg, 'wizard-betfair-1', 'MarketType: passo 1, il mercato',
         'La modalita\' «Da mercati Betfair» esiste solo su questa colonna, e '
         'mostra la libreria costruita all\'inizio.')

    pg.click('[data-act="bf-market"]:has-text("OVER_UNDER_15")')
    pg.wait_for_selector('[data-act="bf-selection"]')
    visibili = sorted(v.strip() for v in
                      pg.locator('[data-act="bf-selection"]').all_text_contents())
    assert visibili == ['Over 1,5 goal', 'Under 1,5 goal'], \
        f'la tendina non mostra le selezioni create: {visibili}'
    shot(pg, 'wizard-betfair-2', 'Passo 2, la selezione',
         'Solo le selezioni create per QUEL mercato. Niente testo libero: e\' il '
         'punto in cui i refusi smettono di essere possibili.')

    pg.click('[data-act="bf-selection"]:has-text("Over 1,5 goal")')
    pg.wait_for_selector('.banner.ok')
    shot(pg, 'wizard-betfair-scelto', 'Tre colonne compilate insieme',
         'MarketType, MarketName e SelectionName arrivano dalla libreria in un '
         'colpo solo, e restano coerenti fra loro.')

    _avanti()                                          # SelectionId
    _avanti()                                          # SelectionName
    for _ in range(6):                                 # Handicap..Points
        _avanti()
    pg.click('[data-act="wiz-next"]')                  # -> riepilogo
    pg.wait_for_selector('#test-msg')
    shot(pg, 'riepilogo', 'Il riepilogo della mappatura',
         'Le 14 colonne con la regola di ciascuna, in una tabella sola. E\' la '
         'vista da cui si controlla il lavoro prima di provarlo.')

    pg.wait_for_selector('#wiz-team-source')
    pg.select_option('#wiz-team-source', label='Canale di Marco')
    shot(pg, 'scelta-sorgente', 'Si aggancia la sorgente squadre',
         'La tendina elenca le sorgenti dell\'account. Senza sceglierne una, i '
         'nomi escono come li scrive il canale.')

    pg.fill('#test-msg', MSG)
    pg.click('[data-act="run-test"]')
    pg.wait_for_selector('#test-csv')
    csv = pg.inner_text('#test-csv')
    # Il valore dell'intero giro: la traduzione avviene SUL SERVER, non
    # nell'anteprima locale. Entrambe le squadre passano dagli alias ai nomi
    # Betfair, e la quota 1.42 esce localizzata come 1,42.
    assert 'Juventus - AC Milan' in csv, f'atteso l\'evento tradotto: {csv!r}'
    assert '"1,42"' in csv, f'atteso il separatore localizzato: {csv!r}'
    assert pg.locator('#test-avvisi').count() == 0, 'tutto mappato: niente avvisi'
    shot(pg, 'prova-sul-server', 'La prova a secco, sul server',
         'Stesso motore del webhook, nessuna scrittura nel feed. Nel CSV '
         'l\'evento e\' «Juventus - AC Milan»: la sorgente ha tradotto entrambe.')

    # ============================================================ E. feed
    pg.click('nav a[href="#/feed"]')
    pg.wait_for_selector('[data-act="ask-token"]')
    # Il giro presuppone un account SENZA token: solo allora `ask-token` va
    # dritto alla modale coi due `.secret` (app.js: `if (!hasToken())`). Con un
    # token gia' presente aprirebbe invece la conferma «Rigenerare?» — zero
    # `.secret`, e l'attesa sotto resterebbe appesa fino al timeout del wrapper.
    # Lo si asserisce qui, cosi' un cambio del seme di produzione da' un errore
    # CHIARO e immediato invece di un timeout di 300s.
    testo_bottone = pg.inner_text('[data-act="ask-token"]').strip().lower()
    assert 'genera' in testo_bottone and 'rigenera' not in testo_bottone, \
        f'il giro presuppone un account senza token, ma il bottone dice: {testo_bottone!r}'
    shot(pg, 'feed-senza-token', 'Il feed, prima del token',
         'L\'URL non esiste ancora: senza token il feed non si apre.')

    pg.click('[data-act="ask-token"]')
    pg.wait_for_selector('.modal .secret')
    segreti = pg.locator('.modal .secret').all_inner_texts()
    token, url_feed = segreti[0].strip(), segreti[1].strip()
    # I messaggi d'assert NON devono contenere il token: se un assert fallisse,
    # pytest lo stamperebbe nei log CI e il wrapper (`test_giro_web.py`) lo
    # ribalterebbe nell'output — la stessa regola «token mai nei log» che la
    # redazione dello screenshot rispetta. Si asserisce sulla FORMA, con
    # messaggi che descrivono senza esporre: solo il prefisso `xt_` (non
    # segreto) e la lunghezza. Segnalato dal gate finale Fable 5 sulla PR #79.
    assert token.startswith('xt_') and len(token) > 20, \
        f'token di forma inattesa (prefisso={token[:3]!r}, len={len(token)})'
    assert '/feed/' in url_feed and url_feed.endswith('?token=' + token), \
        'URL feed di forma inattesa: rotta /feed/ o suffisso ?token= non combaciano'
    # Il token va REDATTO prima di fotografarlo: e' la regola del repo — «i token
    # non compaiono mai nei log, nelle tabelle o negli screenshot» — e vale anche
    # per un token di test da un DB effimero. Si legge (sopra) per le asserzioni,
    # poi si oscura il valore nel DOM, cosi' lo screenshot mostra la modale «una
    # volta sola» senza il segreto. `.secret` sono due: il token e l'URL che lo
    # contiene. Segnalato da GPT-5.5 sulla PR #79.
    # Il token vive in DUE posti nella modale (`copyRow` in app.js): il testo del
    # `.secret` (visibile, cio' che lo screenshot cattura) E il `data-val` del
    # bottone «Copia» (attributo, invisibile nei pixel ma presente nel DOM). Si
    # redigono ENTRAMBI — il segnalatore GPT-5.5 aveva ragione: oscurare solo il
    # testo lasciava il token nell'attributo.
    # Il testo di redazione si costruisce in Python (`\u2022` e' il pallino (U+2022),
    # scritto per escape cosi' il sorgente resta ASCII) e si passa come
    # ARGOMENTO a evaluate: niente concatenazione JS-dentro-stringa con
    # virgolette annidate, che era corretta ma illeggibile — e infatti un
    # reviewer l'aveva letta come «`repeat` non esegue» (GPT-5.5, PR #79).
    redatto = 'xt_' + '\u2022' * 20 + '  (redatto nello screenshot)'
    pg.evaluate(
        "(t) => {"
        "  document.querySelectorAll('.modal .secret').forEach(e => e.textContent = t);"
        "  document.querySelectorAll('.modal [data-val]').forEach("
        "    e => e.setAttribute('data-val', t));"
        "}", redatto)
    # La redazione deve aver MORSO: si controlla l'`outerHTML` INTERO della
    # modale — testo E attributi — e si pretende che ne' il token ne' l'URL ci
    # siano piu'. `inner_text()` da solo non vedrebbe il `data-val`; `outerHTML`
    # copre ogni via con cui un segreto potrebbe restare nel DOM. Se un domani il
    # markup cambia e la redazione non morde, il test va ROSSO invece di lasciar
    # passare il segreto. Suggerito da GPT-5.5 sulla PR #79 (due giri).
    html_modale = pg.evaluate("document.querySelector('.modal').outerHTML")
    assert token not in html_modale and url_feed not in html_modale, \
        'la redazione non ha morso: il token e\' ancora nel DOM della modale'
    shot(pg, 'token', 'Il token, una volta sola',
         'Il server ne conserva solo l\'hash: questa schermata e\' l\'unico '
         'momento in cui il token esiste in chiaro. Rigenerarlo revoca il '
         'precedente. Nello screenshot il valore e\' redatto: un token, anche di '
         'test, non va fotografato.')

    b.close()

(OUT / 'passi.json').write_text(json.dumps(
    {'passi': passi, 'csv': csv, 'errori': errors}, ensure_ascii=False, indent=1),
    encoding='utf-8')

print('\nCSV della prova:\n' + csv)
if errors:
    print('\nERRORI IN CONSOLE:\n' + '\n'.join(errors))
    sys.exit(1)
# 26 passaggi esatti: se il giro cambia lunghezza, il documento va rifatto.
assert len(passi) == 26, f'attesi 26 passaggi, catturati {len(passi)}'
print(f'\n{len(passi)} passaggi, zero errori in console.')
