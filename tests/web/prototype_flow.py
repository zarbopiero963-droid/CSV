"""Flusso completo della web app REALE, pilotato da un browser vero (#32).

Dalla PR dell'aggancio la pagina su `/app` non e' piu' un prototipo a dati
finti: parla col relay. Questo script esegue il percorso del cliente contro il
servizio vero — login a password, dashboard, creazione parser, wizard, prova
messaggio SUL SERVER, salvataggio persistente, token del feed una-volta-sola,
lettura del feed via HTTP col token appena coniato, logout — e asserisce il CSV
prodotto, i byte del feed e l'assenza di errori in console. Esce diverso da
zero al primo problema.

Lo avvia `test_prototype_flow.py`, che tira su il relay con
`ADMIN_PASSWORD_HASH` derivato da `credenziali_prova.py`. A mano:

    ADMIN_PASSWORD_HASH=... TELEGRAM_BOT_TOKEN=123456789:AAFinto \\
        uvicorn main:app --port 8099
    python tests/web/prototype_flow.py http://127.0.0.1:8099/app/ /tmp/shots
"""

import base64, csv, io, json, re, sys, pathlib, tempfile, urllib.error, urllib.request
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from playwright.sync_api import sync_playwright

# Fonte unica dell-avvio del browser: il percorso pinnato in questo
# ambiente, quello di Playwright in CI.
from tests.runtime import apri_chromium  # noqa: E402
from tests.web.credenziali_prova import PASSWORD_PROVA, UTENTE_PROVA  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8099/app/'
OUT = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else pathlib.Path(tempfile.mkdtemp())
OUT.mkdir(parents=True, exist_ok=True)
MSG = "P.Bet. LIVE 2,5\n\U0001F19A Inter v Milan\n⏰ 20:45\n@ 1.85"

errors = []

def shot(page, name):
    page.screenshot(path=str(OUT / f'{name}.png'), full_page=True)
    print('shot', name)

# Le SOLE rotte su cui un 401 e' atteso in questo flusso: la sonda di sessione
# del boot senza cookie e i login volutamente sbagliati (password e Telegram).
# Chromium li logga da se' come «Failed to load resource»: non sono errori
# della pagina. Un 401 su QUALUNQUE altra rotta — e ogni altro codice — resta
# un fallimento del test: un filtro sul solo codice l'avrebbe nascosto
# (segnalato da CodeRabbit sulla PR #50).
ROTTE_401_ATTESE = ('/api/me', '/api/login/password', '/api/login/telegram')

def _console(m):
    if m.type != 'error':
        return
    if 'Failed to load resource' in m.text and '401' in m.text:
        url = (m.location or {}).get('url', '')
        if any(url.split('?')[0].endswith(rotta) for rotta in ROTTE_401_ATTESE):
            return
    errors.append(f'console.{m.type}: {m.text}')

with sync_playwright() as pw:
    b = apri_chromium(pw)
    pg = b.new_page(viewport={'width': 1420, 'height': 1000})
    pg.on('console', _console)
    pg.on('pageerror', lambda e: errors.append(f'pageerror: {e}'))

    # Un hash storto (`%` non seguito da due esadecimali) non deve lasciare la
    # pagina bianca: `decodeURIComponent` solleva URIError e, senza la guardia
    # in parseHash, l'errore scappava da render(). Segnalato da CodeRabbit
    # sulla PR #50: qui si pretende che la pagina si disegni comunque.
    pg.goto(BASE + '#/parsers/%')
    pg.wait_for_selector('#login-pass')

    pg.goto('about:blank')
    pg.goto(BASE)
    pg.wait_for_selector('#login-pass')
    shot(pg, '01-login')

    # La porta Telegram: un LINK verso oauth.telegram.org (modalita' redirect,
    # nessuno script esterno), costruito col bot_id numerico di /api/settings.
    # Non si clicca — navigherebbe verso Telegram — se ne asserisce la forma.
    href = pg.get_attribute('a.tg-btn', 'href')
    assert href and href.startswith('https://oauth.telegram.org/auth?bot_id=123456789'), href
    assert 'AAFinto' not in href, 'il token del bot e\' finito nel link di login'

    # Il ritorno da oauth.telegram.org e' base64url SENZA padding, e la lunghezza
    # puo' avere resto 2 o resto 3 (il resto 1 non esiste in un base64 valido).
    # Il decoder deve arrivare fino al SERVER — che rifiuta la firma finta con
    # «login non valido» — e non fermarsi prima con «risposta di Telegram non
    # leggibile». E' la misura chiesta da GPT-5.5 sulla PR #50: atob in Chromium
    # e' un forgiving-base64 e accetta l'input non paddato, questo test lo
    # inchioda contro una futura riscrittura del decoder.
    def _tg_auth_result(resto):
        campi = {'id': 1, 'auth_date': 1, 'hash': 'x'}
        while True:
            grezzo = json.dumps(campi, separators=(',', ':')).encode()
            testo = base64.urlsafe_b64encode(grezzo).rstrip(b'=').decode()
            if len(testo) % 4 == resto:
                return testo
            campi['hash'] += 'x'

    for resto in (2, 3):
        # Passaggio da about:blank: un goto che cambia solo il frammento e' una
        # navigazione nello STESSO documento, il modulo non si ricarica e il
        # boot — che e' cio' che si sta misurando — non girerebbe affatto.
        pg.goto('about:blank')
        pg.goto(BASE + '#tgAuthResult=' + _tg_auth_result(resto))
        pg.wait_for_selector('#login-pass')
        banner = pg.inner_text('.login')
        assert 'login non valido' in banner, (
            f'resto {resto}: atteso il rifiuto del SERVER, pagina: {banner[:200]!r}')
        assert 'non leggibile' not in banner, (
            f'resto {resto}: il decoder si e\' fermato prima del server')
        assert 'tgAuthResult' not in pg.url, 'il frammento firmato e\' rimasto nell\'URL'

    # Password sbagliata: l'errore del server compare nella pagina, non in console.
    pg.fill('#login-user', UTENTE_PROVA)
    pg.fill('#login-pass', 'password-sbagliata')
    pg.click('[data-act="login-password"]')
    pg.wait_for_function("document.getElementById('login-err')"
                         " && document.getElementById('login-err').textContent.length > 0")
    err = pg.inner_text('#login-err')
    assert 'credenziali' in err.lower(), f'errore di login inatteso: {err!r}'
    shot(pg, '02-login-sbagliato')

    # Password giusta: sessione vera (cookie firmato dal server) e dashboard.
    pg.fill('#login-pass', PASSWORD_PROVA)
    pg.click('[data-act="login-password"]')
    pg.wait_for_selector('.stats')
    assert 'amministratore' in pg.inner_text('.head'), 'la pillola admin non compare'
    shot(pg, '03-dashboard')

    # nuovo parser: viene creato SUL SERVER (POST /api/me/parsers)
    pg.click('[data-act="new-parser"]')
    pg.fill('#np-name', 'Over 2,5 LIVE')
    pg.click('[data-act="create-parser"]')
    pg.wait_for_selector('#paste-msg')
    shot(pg, '04-wizard-incolla')

    # suggeritore (client-side: il motore JS e' gia' nel browser)
    pg.fill('#paste-msg', MSG)
    pg.click('[data-act="ai-suggest"]')
    pg.wait_for_selector('.map-table', timeout=8000)
    shot(pg, '05-suggerimento')

    # Il messaggio campione appena salvato deve stare sotto una chiave che porta
    # l'UTENTE della sessione, non il solo slug: lo slug dei parser e' unico PER
    # UTENTE (`UNIQUE (user_id, slug)` nello schema), quindi su un browser
    # condiviso due account possono avere lo stesso slug e una chiave per solo
    # slug fa leggere al secondo il messaggio Telegram del primo.
    # [REAL_FINDING] di GPT-5.6 Sol al gate della PR #50.
    chiavi_campione = pg.evaluate(
        "Object.keys(localStorage).filter(k => k.startsWith('xtrelay:campione:'))")
    assert chiavi_campione, 'nessun messaggio campione in localStorage dopo il wizard'
    for chiave in chiavi_campione:
        assert re.match(r'xtrelay:campione:\d+:', chiave), (
            f'chiave del campione senza utente: {chiave!r} — su un browser '
            'condiviso un altro account con lo stesso slug la leggerebbe')

    # riparti dal messaggio e vai a mano: condizione
    pg.click('[data-act="wiz-restart"]')
    pg.wait_for_selector('#paste-msg')
    pg.click('[data-act="start-wizard"]')
    pg.wait_for_selector('#match-val')
    pg.click('.frag >> nth=0')
    pg.click('[data-act="save-match"]')
    pg.wait_for_selector('[data-act="wiz-next"]')
    shot(pg, '06-colonna-provider')

    # Regressione del finding Critical di CodeRabbit: partendo da una regola SENZA
    # trasformazioni, readCurrentRule() in modalita' regex crea un oggetto nuovo con
    # un array vuoto SUO. Se il gestore cattura la regola prima della rilettura,
    # spinge la trasformazione in una copia orfana e la perde.
    pg.click('[data-act="wiz-mode"][data-mode="regex"]')
    pg.wait_for_selector('#rule-pattern')
    # click SINGOLO, non check(): check() ritenta e il secondo tentativo maschera
    # il difetto — colpisce la PRIMA attivazione.
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

    # Provider resta VUOTA: e' il nome di chi manda, la scrive chi legge (#42).
    pg.click('[data-act="rule-empty"]')       # Provider vuota -> EventId
    pg.click('[data-act="rule-empty"]')       # EventId vuoto -> EventName
    pg.wait_for_selector('.frag')
    pg.click('.frag >> nth=1')                 # riga con l'emoji versus
    pg.wait_for_selector('#rule-anchor')
    shot(pg, '07-eventname-frammento')
    ev = pg.inner_text('.xt tbody td >> nth=2')
    assert ev == 'Inter v Milan', f'EventName grezzo={ev!r}'

    # attiva la sostituzione dell'ultimo " v "
    pg.check('[data-act="toggle-transform"][data-op="replace_last"]')
    pg.wait_for_timeout(200)
    ev2 = pg.inner_text('.xt tbody td >> nth=2')
    assert ev2 == 'Inter - Milan', f'EventName trasformato={ev2!r}'
    shot(pg, '08-eventname-trasformato')

    # Le colonne obbligatorie (MarketType, SelectionName, BetType) come costanti,
    # com'e' un parser vero. `_costante` asserisce la colonna PRIMA di scrivere:
    # un riordino silenzioso degli step non deve passare (chiesto da Fable 5, PR #28).
    def _colonna_corrente():
        return pg.inner_text('.bubble.ai strong.mono')

    def _avanti():
        prima = _colonna_corrente()
        pg.click('[data-act="wiz-next"]')
        pg.wait_for_function(
            "p => { const e = document.querySelector('.bubble.ai strong.mono');"
            " return e && e.textContent !== p; }", arg=prima)

    def _costante(colonna, valore):
        corrente = _colonna_corrente()
        assert corrente == colonna, f'wizard atteso su {colonna!r}, invece su {corrente!r}'
        pg.click('[data-act="wiz-mode"][data-mode="constant"]')
        pg.wait_for_selector('#rule-const')
        pg.fill('#rule-const', valore)

    def _al_riepilogo():
        pg.click('[data-act="wiz-next"]')
        pg.wait_for_selector('#test-msg')

    _avanti()                       # MarketId
    _avanti()                       # MarketName
    _avanti()                       # MarketType (obbligatoria)
    _costante('MarketType', 'OVER_UNDER_15')
    _avanti()                       # SelectionId
    _avanti()                       # SelectionName (obbligatoria)
    _costante('SelectionName', 'Over 1,5 goal')
    _avanti()                       # Handicap
    _avanti()                       # Price
    _avanti()                       # MinPrice
    _avanti()                       # MaxPrice
    _avanti()                       # BetType (obbligatoria)
    _costante('BetType', 'PUNTA')
    _avanti()                       # Points
    _al_riepilogo()
    shot(pg, '09-riepilogo')

    # prova messaggio: gira SUL SERVER (POST /api/me/parsers/{slug}/test), lo
    # stesso `esegui_parser` del webhook, a secco. Il CSV mostrato e' quello del
    # server, byte per byte.
    pg.click('[data-act="run-test"]')
    pg.wait_for_selector('#test-csv')
    csv_txt = pg.inner_text('#test-result')
    assert 'Riconosciuto' in csv_txt, f'atteso esito riconosciuto: {csv_txt!r}'
    csv_txt = pg.inner_text('#test-csv')
    assert 'Inter - Milan' in csv_txt, f'CSV senza evento: {csv_txt!r}'
    assert csv_txt.count('"') == 56, f'campi quotati attesi 56, trovati {csv_txt.count(chr(34))}'
    assert csv_txt.strip().count(chr(10)) == 1, 'attese 2 righe: intestazione + segnale'
    # Asserzione POSIZIONALE sul CSV del SERVER. Indici da HEADERS: Provider=0,
    # EventName=2, MarketType=5, SelectionName=7, BetType=12.
    righe = list(csv.reader(io.StringIO(csv_txt.lstrip('\ufeff'))))
    assert len(righe) >= 2, f'CSV senza riga segnale: {csv_txt!r}'
    segnale = righe[1]
    assert segnale[0] == '', f'Provider doveva restare vuota (#42): {segnale!r}'
    assert segnale[2] == 'Inter - Milan', f'EventName in colonna sbagliata: {segnale!r}'
    assert segnale[5] == 'OVER_UNDER_15', f'MarketType in colonna sbagliata: {segnale!r}'
    assert segnale[7] == 'Over 1,5 goal', f'SelectionName in colonna sbagliata: {segnale!r}'
    assert segnale[12] == 'PUNTA', f'BetType in colonna sbagliata: {segnale!r}'
    shot(pg, '10-prova-ok')
    print('CSV riconosciuto:', repr(csv_txt))

    # prova messaggio: caso da ignorare (la condizione non corrisponde)
    pg.fill('#test-msg', 'Buongiorno ragazzi, nessun segnale adesso')
    pg.click('[data-act="run-test"]')
    pg.wait_for_function("document.querySelector('#test-result')"
                         " && document.querySelector('#test-result').innerText.includes('Ignorato')")
    only_head = pg.inner_text('#test-csv')
    assert 'Inter' not in only_head, f'il feed non doveva cambiare: {only_head!r}'
    assert only_head.strip().count(chr(10)) == 0, 'atteso solo header'
    shot(pg, '11-prova-ignorata')

    # ripristina il messaggio buono, rilancia e salva
    pg.fill('#test-msg', MSG)
    pg.click('[data-act="run-test"]')
    pg.wait_for_function("document.querySelector('#test-result')"
                         " && document.querySelector('#test-result').innerText.includes('Riconosciuto')")
    pg.click('[data-act="wiz-save"]')
    pg.wait_for_selector('.toast')

    # PERSISTENZA: ricarica la pagina. La sessione sopravvive (cookie), la
    # config sopravvive (e' sul server), e il riepilogo mostra il valore
    # estratto dalla mappatura salvata. Con la vecchia demo a localStorage
    # questo passo non dimostrava niente; adesso e' il punto della PR.
    pg.reload()
    pg.wait_for_selector('.map-table')
    riepilogo = pg.inner_text('.map-table')
    assert 'Inter - Milan' in riepilogo, (
        f'la mappatura salvata non e\' tornata dal server: {riepilogo[:300]!r}')
    shot(pg, '12-dopo-reload')

    # token del feed: DELL'UTENTE, mostrato una volta sola
    pg.click('nav a[href="#/feed"]')
    pg.wait_for_selector('[data-act="ask-token"]')
    shot(pg, '13-feed-senza-token')
    pg.click('[data-act="ask-token"]')
    pg.wait_for_selector('.modal .secret')
    segreti = pg.locator('.modal .secret').all_inner_texts()
    token = segreti[0]
    url_feed = segreti[1]
    assert token.startswith('xt_') and len(token) > 20, f'token strano: {token!r}'
    assert url_feed.endswith('?token=' + token) and '/feed/' in url_feed, url_feed
    shot(pg, '14-token')
    pg.click('[data-act="after-token"]')
    pg.wait_for_selector('[data-act="ask-token"]')
    # la pagina ora mostra solo il PREFISSO, mai il token intero — e l'URL
    # mascherato NON ha un bottone Copia: copiato in XTrader darebbe 404 con
    # la faccia di un guasto del servizio (CodeRabbit, PR #50)
    visibile = pg.inner_text('.main')
    assert token not in visibile, 'il token intero e\' rimasto visibile dopo il modale'
    assert token[:9] in visibile, 'il prefisso del token non compare'
    assert pg.locator('.main .copy-row').count() == 0, (
        'l\'URL mascherato offre un bottone Copia: incollato in XTrader da\' 404')

    # IL FEED VERO: l'URL appena coniato risponde 200 con la sola intestazione,
    # UTF-8 con BOM — sono i byte che leggera' XTrader.
    with urllib.request.urlopen(url_feed, timeout=10) as r:  # noqa: S310 - loopback
        corpo = r.read()
    assert corpo.startswith(('\ufeff"Provider"').encode('utf-8')), corpo[:30]
    assert corpo.decode('utf-8').strip().count('\n') == 0, 'feed atteso a sola intestazione'
    # e con un token sbagliato risponde 404, senza confermare che lo slug esiste
    try:
        urllib.request.urlopen(url_feed[:url_feed.rindex('=')] + '=xt_sbagliato', timeout=10)  # noqa: S310
        raise AssertionError('un token sbagliato ha aperto il feed')
    except urllib.error.HTTPError as e:
        assert e.code == 404, f'atteso 404 sul token sbagliato, avuto {e.code}'

    # chat e log: dichiarati «prossimamente», non finti
    pg.click('nav a[href="#/chats"]')
    pg.wait_for_selector('.empty')
    assert 'prossimamente' in pg.inner_text('.main')
    pg.click('nav a[href="#/logs"]')
    pg.wait_for_selector('.empty')
    assert 'prossimamente' in pg.inner_text('.main')
    shot(pg, '15-prossimamente')

    # impostazioni: prefisso del token e profilo, mai il token
    pg.click('nav a[href="#/settings"]')
    pg.wait_for_selector('table')
    impostazioni = pg.inner_text('.main')
    assert token[:9] in impostazioni and token not in impostazioni
    shot(pg, '16-impostazioni')

    # logout: si torna al login, e la sessione e' davvero chiusa
    pg.click('[data-act="logout"]')
    pg.wait_for_selector('#login-pass')
    stato_me = pg.evaluate("fetch('/api/me').then(r => r.status)")
    assert stato_me == 401, f'/api/me dopo il logout: atteso 401, avuto {stato_me}'
    shot(pg, '17-logout')

    b.close()

print('\nERRORI JS:', len(errors))
for e in errors:
    print(' -', e)
sys.exit(1 if errors else 0)
