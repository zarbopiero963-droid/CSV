"""La vista «Chat Telegram» pilotata da un browser vero, sul relay vero (#32, 3.2).

Il backend e' del PR #112 e ha i suoi 28 test in `tests/relay/test_verifica_chat.py`.
Qui si verifica il LATO UI, e con un end-to-end che negli altri flussi web non era
possibile: **nessuno stub**. Il codice di verifica esce dal browser, questo script lo
consegna al webhook con un POST autentico (segreto derivato dal bot, come farebbe
Telegram), e il browser lo vede comparire da solo col suo sondaggio. Le tre parti —
`app.js`, `api.js` e `main.py` — sono tutte reali.

Cosa deve dimostrare, in ordine:

1. il posto del «prossimamente» e' preso da un percorso che si puo' seguire senza
   sapere niente: **copia questo → incollalo nel canale → aspetta**;
2. il codice esiste in chiaro **una volta sola**: si vede quando lo si chiede, e
   ricaricando la pagina non ricompare (il server non lo ripete, `verify/status`
   non lo porta) — la UI lo dice invece di mostrare una casella vuota;
3. la chat verificata compare **da sola**, senza che l'utente ricarichi;
4. si collega a un parser dal tab «Chat assegnate», e la scelta **sopravvive** a un
   ricaricamento (cioe' e' andata sul server, non in una variabile);
5. si elimina, con conferma esplicita.

Dal #116 (PR 2) il flusso copre anche la meta' che il codice usa-e-getta non tocca:

6. il percorso CONSIGLIATO e' in schermata — il link del bot da copiare, costruito
   dai settings pubblici del servizio e non scritto in pagina;
7. una retrocessione del bot si VEDE nella riga, e dice la cosa giusta: «non e' piu'
   amministratore» non e' «non legge piu'»;
8. un codice RIFIUTATO lo dice mentre l'utente guarda, senza ricaricare, e la coda
   del messaggio cambia col motivo (chat occupata / accesso non attivo) e con la
   scadenza — tre combinazioni misurate, non dedotte.

Viewport a 390px per tutta la durata: la pagina non deve sfondare in orizzontale
(regola 2 di CLAUDE.md). Zero errori in console, come tutti i flussi web di qui.

Argomenti: base_url (con /app/), cartella screenshot, percorso del database.
Il terzo serve solo per gli stati che dal browser non sono raggiungibili — un
secondo utente, una sospensione, una scadenza — e mai per cio' che si sta
verificando, che passa sempre dallo schermo e dal webhook veri.
"""

import json
import pathlib
import sqlite3
import sys
import tempfile
import urllib.request
from urllib.parse import urlsplit

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from playwright.sync_api import sync_playwright  # noqa: E402

import main  # noqa: E402 - dopo l'inserimento del percorso
from tests.runtime import apri_chromium  # noqa: E402
from tests.web.credenziali_prova import PASSWORD_PROVA, UTENTE_PROVA  # noqa: E402

BASE = sys.argv[1]
OUT = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else pathlib.Path(tempfile.mkdtemp())
OUT.mkdir(parents=True, exist_ok=True)
DB = sys.argv[3] if len(sys.argv) > 3 else None

# Il canale che appartiene a QUALCUN ALTRO, per provocare il rifiuto del codice.
CANALE_ALTRUI = -1002000000909

# Deve combaciare con l'ambiente della fixture: il segreto del webhook ne deriva.
BOT = '123456789:AAFinto'
# Deve combaciare con `TELEGRAM_BOT_USERNAME` dell'ambiente della fixture: la
# vista costruisce il link da copiare dai settings pubblici del servizio.
BOT_USERNAME = 'BetrelayProvaBot'
CANALE = -1002000000101

pezzi = urlsplit(BASE)
RADICE_SERVIZIO = f'{pezzi.scheme}://{pezzi.netloc}'

errors = []


# Il 401 su `/api/me` alla PRIMA apertura non e' un difetto: `boot()` chiede la
# sessione prima che esista, e il 401 e' la risposta giusta — Chromium la logga
# comunque come risorsa fallita. E' la stessa eccezione, con lo stesso motivo, di
# `mercati_flow.py`. Ogni altro 401 resta un errore: il filtro guarda il PERCORSO,
# non si limita a ignorare il codice.
ROTTE_401_ATTESE = ('/api/me',)

# Lo stato del tracciamento delle richieste, per due misure che dal solo DOM non
# si possono fare: l'ORDINE delle due chiamate d'apertura, e la sopravvivenza del
# sondaggio a una richiesta fallita. Il guasto e' PROVOCATO da questo script.
ordine_chiamate = []
guasto = {'attivo': False, 'fatto': False, 'perenne': False}


def _console(m):
    if m.type != 'error':
        return
    if 'Failed to load resource' in m.text:
        percorso = (m.location or {}).get('url', '').split('?')[0]
        if '401' in m.text and any(percorso.endswith(r) for r in ROTTE_401_ATTESE):
            return
        # Il fallimento che abbiamo provocato noi su `verify/status`: e' il
        # contratto che si sta verificando, non un difetto della pagina.
        if guasto['fatto'] and percorso.endswith('/api/chats/verify/status'):
            return
    errors.append(f'console.{m.type}: {m.text} @ {(m.location or {}).get("url", "")}')


def _traccia(route):
    """Registra l'ordine delle chiamate e, su richiesta, ne fa fallire UNA."""
    percorso = route.request.url.split('?')[0]
    if percorso.endswith('/api/chats/verify/status'):
        ordine_chiamate.append('stato')
        if guasto['attivo'] or guasto['perenne']:
            guasto['attivo'] = False
            guasto['fatto'] = True
            route.abort()
            return
    elif percorso.endswith('/api/chats'):
        ordine_chiamate.append('lista')
    route.continue_()


def shot(page, name):
    page.screenshot(path=str(OUT / f'{name}.png'), full_page=True)
    print('shot', name)


def non_sfonda(page, dove):
    assert page.evaluate(
        'document.documentElement.scrollWidth <= window.innerWidth + 1'), \
        f'la pagina sfonda in orizzontale su schermo stretto: {dove}'


def consegna(testo, chat=CANALE):
    """Una consegna di Telegram autentica, col segreto derivato dal bot.

    E' il pezzo che rende questo flusso un end-to-end vero: il codice fa lo stesso
    giro che fara' in produzione — schermo, canale, webhook — invece di essere
    scritto a mano nel database o inventato da uno stub.
    """
    titolo = 'Canale segnali' if chat == CANALE else 'Canale di un altro'
    payload = {'message': {'chat': {'id': chat, 'title': titolo,
                                    'type': 'channel'},
                           'text': testo}}
    req = urllib.request.Request(
        f'{RADICE_SERVIZIO}/telegram/webhook',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json',
                 'X-Telegram-Bot-Api-Secret-Token': main.webhook_secret(BOT)},
        method='POST')
    with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 - loopback
        return r.status, json.loads(r.read())


def chat_di_un_altro():
    """Un secondo utente con una chat sua: e' l'unico modo di far RIFIUTARE un codice.

    Si scrive nel database perche' dal browser un secondo utente non esiste — la
    fixture ha una sola porta a password. E' lo stesso mezzo che usano i test di
    `tests/relay/`, e la parte che conta resta reale: il codice esce dallo schermo,
    passa dal webhook col segreto vero, e il rifiuto lo decide `main.py`.
    """
    c = sqlite3.connect(DB)
    try:
        c.execute("INSERT INTO users(telegram_id, status) VALUES ('777000333','attivo')")
        altro = c.execute(
            "SELECT id FROM users WHERE telegram_id='777000333'").fetchone()[0]
        c.execute('INSERT INTO chats(telegram_chat_id, owner_user_id, verified_at,'
                  ' title, type) VALUES (?,?,?,?,?)',
                  (str(CANALE_ALTRUI), altro, 1, 'Canale di un altro', 'channel'))
        c.commit()
    finally:
        c.close()


def sospendi_utente():
    """Toglie l'accesso all'utente del flusso, DAL DATABASE e senza ricaricare.

    Serve `is_admin=0` oltre allo stato: l'amministratore e' esente dal cancello,
    sia nel relay (`_consuma_codice_di_verifica`) sia nella web app, quindi
    sospenderlo soltanto non produrrebbe nessun rifiuto.

    Restituisce l'**id** toccato, e `riattiva_utente` ripristina QUELLO. La prima
    versione ripristinava `WHERE status='sospeso'`, cioe' chiunque fosse sospeso
    nel database: bastava un altro utente in quello stato — dalla semina o da un
    passo precedente — perche' il test lo riattivasse per errore, silenziosamente
    e fuori dal proprio perimetro. Rilievo di GPT-5.5 sulla PR #120.
    """
    c = sqlite3.connect(DB)
    try:
        riga = c.execute(
            'SELECT MIN(id) FROM users WHERE is_admin=1').fetchone()
        assert riga and riga[0] is not None, 'nessun amministratore da sospendere'
        utente = riga[0]
        c.execute("UPDATE users SET is_admin=0, status='sospeso' WHERE id=?",
                  (utente,))
        c.commit()
        return utente
    finally:
        c.close()


def riattiva_utente(utente):
    """Rimette com'era l'utente sospeso da `sospendi_utente`, e SOLO quello.

    Sta in un `finally`: se l'asserzione fallisce, i passi successivi non devono
    ereditare un utente sospeso e sbagliare motivo di rifiuto.
    """
    c = sqlite3.connect(DB)
    try:
        c.execute("UPDATE users SET is_admin=1, status='attivo' WHERE id=?",
                  (utente,))
        c.commit()
    finally:
        c.close()


def scadi_il_codice(codice):
    """Manda a scadenza un codice ancora vivo, senza aspettare i 600 secondi."""
    c = sqlite3.connect(DB)
    try:
        c.execute('UPDATE chat_verifications SET expires_at=1 WHERE code=?',
                  (codice,))
        c.commit()
    finally:
        c.close()


def my_chat_member(stato_nuovo, attore=999000111):
    """Una consegna `my_chat_member` autentica: il bot cambia stato in quel canale.

    Serve per la meta' del #116 che il codice usa-e-getta non tocca: la riga resta
    elencata quando il bot viene retrocesso, e la vista deve dirlo.
    """
    payload = {'update_id': 500100,
               'my_chat_member': {
                   'chat': {'id': CANALE, 'title': 'Canale segnali',
                            'type': 'channel'},
                   'from': {'id': attore},
                   'new_chat_member': {'status': stato_nuovo}}}
    req = urllib.request.Request(
        f'{RADICE_SERVIZIO}/telegram/webhook',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json',
                 'X-Telegram-Bot-Api-Secret-Token': main.webhook_secret(BOT)},
        method='POST')
    with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 - loopback
        return r.status, json.loads(r.read())


with sync_playwright() as pw:
    b = apri_chromium(pw)
    ctx = b.new_context(viewport={'width': 390, 'height': 850})   # mobile stretto
    pg = ctx.new_page()
    pg.on('console', _console)
    pg.on('pageerror', lambda e: errors.append(f'pageerror: {e}'))
    pg.route('**/api/chats**', _traccia)

    # ---- login (porta a password, come gli altri flussi) ------------------
    pg.goto(BASE)
    pg.wait_for_selector('#login-pass')
    pg.fill('#login-user', UTENTE_PROVA)
    pg.fill('#login-pass', PASSWORD_PROVA)
    pg.click('[data-act="login-password"]')
    pg.wait_for_selector('nav a[href="#/chats"]')

    # ---- 1) la vista non e' piu' un segnaposto ---------------------------
    pg.click('nav a[href="#/chats"]')
    pg.wait_for_selector('[data-act="chat-verifica-start"]')
    testo = pg.inner_text('#app')
    assert 'prossimamente' not in testo.lower(), \
        'la vista Chat Telegram e- ancora il segnaposto'

    # ---- 1-bis) il percorso PRINCIPALE: aggiungere il bot (#116) ----------
    # La PR 1 ha portato il meccanismo nel relay; se la vista non lo dice, quel
    # meccanismo esiste e nessuno lo usa. Il link va COSTRUITO dai settings
    # pubblici, non scritto a mano: e' l'unico modo perche' cambiare bot non
    # lasci in schermata l'indirizzo di quello vecchio.
    assert pg.locator('#link-bot').count() == 1, (
        'la vista non offre il link del bot da copiare: il percorso principale '
        'del #116 non e- raggiungibile dalla schermata')
    link = pg.inner_text('#link-bot').strip()
    assert link == f'https://t.me/{BOT_USERNAME}', (
        f'il link del bot non viene dai settings del servizio: {link!r}')
    principale = pg.inner_text('.card:has(#link-bot)').lower()
    assert 'amministratore' in principale, (
        f'la card principale non dice di promuovere il bot: {principale!r}')
    # La promozione e' una PROVA, non una scorciatoia, e la schermata deve dire
    # perche': e' l'unica differenza sostanziale col codice usa-e-getta.
    assert 'solo a chi' in principale, (
        f'la card non spiega perche- la promozione dimostra qualcosa: {principale!r}')
    # L'avviso approvato dal proprietario: due amministratori della stessa chat,
    # e vince chi la collega per primo. Senza, il secondo vede solo una chat che
    # non compare e nessuna spiegazione.
    assert 'gia- collegata a un altro account' in principale.replace('à', 'a-'), (
        f'la card non avverte del caso «gia- collegata da un altro»: {principale!r}')
    # La frase del segnaposto, non la parola: la vista NUOVA deve poter dire che
    # per un canale il bot va aggiunto come amministratore — e' vero e serve.
    assert 'le collega l' not in testo.lower(), (
        'la vista dice ancora che le chat le collega l-amministratore: '
        f'{testo[:400]!r}')
    # Lo STATO va chiesto prima della LISTA. Se il codice viene consumato fra le
    # due chiamate e la lista e' letta per prima, quella chat non c'e', `in_attesa`
    # e' gia' falso — quindi il sondaggio non riparte — e il canale appena
    # verificato resta invisibile fino a un ricaricamento. E' una corsa, quindi si
    # misura l'ORDINE, che e' deterministico. Segnalato da CodeRabbit sulla PR #114.
    assert ordine_chiamate[:2] == ['stato', 'lista'], (
        f'la vista chiede la lista prima dello stato: {ordine_chiamate[:4]}')
    non_sfonda(pg, 'chat vuote')
    shot(pg, '01-chat-vuote')

    # ---- 2) il codice, e la direzione da seguire -------------------------
    pg.click('[data-act="chat-verifica-start"]')
    pg.wait_for_selector('#codice-verifica')
    codice = pg.inner_text('#codice-verifica').strip()
    assert codice.startswith('BETRELAY-'), f'codice inatteso: {codice!r}'
    istruzioni = pg.inner_text('.card:has(#codice-verifica)').lower()
    for parola in ('copia', 'incolla', 'canale'):
        assert parola in istruzioni, \
            f'le istruzioni non dicono «{parola}»: {istruzioni!r}'
    # La prova NON e' la stessa per canali e gruppi, e la schermata deve dirlo. In
    # un canale scrivono solo gli amministratori; in un gruppo scrive ogni membro,
    # quindi incollare il codice dimostra di poterci scrivere, non di gestirlo.
    # `[REAL_FINDING]` di OpenRouter Sol al gate della PR #114: il difetto e' del
    # meccanismo (PR #112), ma una schermata che promette «dimostra che quel canale
    # e' tuo» lo NASCONDE — e quella schermata e' di questo PR.
    assert 'gruppo' in istruzioni, f'la card non nomina i gruppi: {istruzioni!r}'
    assert 'qualunque membro' in istruzioni, (
        'la card non avverte che in un gruppo puo- scrivere ogni membro: '
        f'{istruzioni!r}')
    non_sfonda(pg, 'codice mostrato')
    shot(pg, '02-codice')

    # Il codice esiste in chiaro UNA VOLTA SOLA: dopo un ricaricamento il server
    # non lo ripete, e la UI deve dirlo invece di mostrare una casella vuota.
    pg.reload()
    pg.wait_for_selector('#verifica-in-corso')
    assert codice not in pg.inner_text('#app'), \
        'il codice e- ricomparso dopo il ricaricamento: non e- piu- usa-e-getta'
    shot(pg, '03-codice-non-ripetuto')

    # ---- 2-ter) il codice non sopravvive a un cambio di sessione ---------
    # Il codice e' legato all'UTENTE, non alla sola pagina: dopo «Esci» non deve
    # ricomparire nemmeno rientrando. Il percorso misurato qui e' quello
    # raggiungibile (logout esplicito); il legame `{utente, codice}` chiude anche
    # quello che oggi non lo e' — un cambio d'utente senza ricaricare — che oggi
    # e' impedito solo dal `location.reload()` di `fallita` sul 401, cioe' da una
    # difesa altrui. `[REAL_FINDING]` di OpenRouter Sol sulla PR #114.
    pg.click('[data-act="logout"]')
    pg.wait_for_selector('#login-pass')
    pg.fill('#login-user', UTENTE_PROVA)
    pg.fill('#login-pass', PASSWORD_PROVA)
    pg.click('[data-act="login-password"]')
    pg.wait_for_selector('nav a[href="#/chats"]')
    pg.click('nav a[href="#/chats"]')
    pg.wait_for_selector('#verifica-in-corso')
    assert codice not in pg.inner_text('#app'), \
        'il codice e- sopravvissuto al logout: non e- legato alla sessione'

    # ---- 3) incollato nel canale: la chat compare DA SOLA ----------------
    # Prima pero' si fa FALLIRE una richiesta del sondaggio. Se una sola richiesta
    # andata male lo ferma, la pagina resta «in attesa» per sempre — non si accorge
    # ne' della verifica ne' della scadenza — e l'utente non ha modo di saperlo:
    # il canale non compare piu' e nessun errore glielo dice. Segnalato da
    # CodeRabbit sulla PR #114, e il commento nel codice dichiarava proprio
    # l'intenzione che il codice non manteneva.
    guasto['attivo'] = True
    stato_webhook, corpo_webhook = consegna(codice)
    assert stato_webhook == 200, (stato_webhook, corpo_webhook)
    pg.wait_for_selector('.list-item:has-text("Canale segnali")', timeout=30000)
    assert guasto['fatto'], (
        'il guasto provocato non e- mai stato consegnato: il test non ha '
        'verificato niente sulla ripresa del sondaggio')
    riga = pg.inner_text('.list-item:has-text("Canale segnali")')
    assert str(CANALE) in riga, f'la riga non mostra l-id della chat: {riga!r}'
    # `chats.id` (la chiave del servizio) NON e' il numero di Telegram: sono due
    # colonne diverse, e le rotte vogliono la prima. Si legge dal DOM invece di
    # indovinarla, o il test passerebbe solo su un database appena nato.
    id_servizio = pg.get_attribute(
        '.list-item:has-text("Canale segnali") [data-act="chat-del"]', 'data-id')
    assert id_servizio and id_servizio != str(CANALE), (
        f'la riga espone il numero di Telegram al posto di chats.id: {id_servizio!r}')
    non_sfonda(pg, 'chat verificata')
    shot(pg, '04-chat-verificata')

    # ---- 3-bis) il bot retrocesso si VEDE nella riga (#116) ---------------
    # La PR 1 registra `chats.bot_stato` e non cancella la riga: la chat resta
    # elencata coi suoi link. Senza una pillola, l'utente ha in schermata una
    # chat «autorizzata» da cui non arriva piu' niente e nessuna spiegazione —
    # che e' il difetto che la colonna era nata per rendere dicibile.
    stato_bot, corpo_bot = my_chat_member('member')
    assert stato_bot == 200, (stato_bot, corpo_bot)
    pg.click('[data-act="ricarica"]')
    riga_ok = '.list-item:has-text("Canale segnali")'
    pg.wait_for_selector(f'{riga_ok} .pill.warn')
    pillola = pg.inner_text(f'{riga_ok} .pill.warn')
    assert 'amministratore' in pillola.lower(), (
        f'la pillola non dice cosa e- successo al bot: {pillola!r}')
    # E NON deve dire «non legge piu-»: in un gruppo un bot con la privacy mode
    # disattivata continua a leggere tutto anche da semplice `member`. E- la
    # stessa affermazione falsa che OpenRouter Sol ha fermato sul nome della
    # costante nel PR 1; qui sarebbe arrivata sullo schermo del cliente.
    assert 'legge' not in pillola.lower(), (
        f'la pillola afferma qualcosa che non sappiamo: {pillola!r}')
    non_sfonda(pg, 'bot retrocesso')
    shot(pg, '04b-bot-retrocesso')

    # ---- 4) il collegamento al parser, e la sua persistenza --------------
    pg.click('nav a[href="#/parsers"]')
    pg.wait_for_selector('[data-act="new-parser"]')
    pg.click('[data-act="new-parser"]')
    pg.fill('#np-name', 'Parser delle chat')
    pg.click('[data-act="create-parser"]')
    pg.wait_for_selector('#paste-msg')

    pg.click('a:has-text("Chat assegnate")')
    pg.wait_for_selector('#chat-assegnate')
    casella = pg.locator(f'#chat-assegnate input[data-chat-id="{id_servizio}"]')
    casella.wait_for()
    assert not casella.is_checked(), 'la chat risulta gia- collegata'
    casella.check()
    pg.click('[data-act="chat-assegna-salva"]')
    pg.wait_for_selector('.toast')
    non_sfonda(pg, 'chat assegnate')
    shot(pg, '05-chat-assegnata')

    # La prova che il salvataggio e' andato SUL SERVER: si ricarica tutto.
    pg.reload()
    pg.wait_for_selector('#chat-assegnate')
    assert pg.locator(f'#chat-assegnate input[data-chat-id="{id_servizio}"]').is_checked(), \
        'il collegamento non e- sopravvissuto al ricaricamento: non e- stato salvato'
    shot(pg, '06-collegamento-persistente')

    # ---- 5) eliminazione, con conferma esplicita -------------------------
    pg.click('nav a[href="#/chats"]')
    pg.wait_for_selector('.list-item:has-text("Canale segnali")')
    pg.click(f'[data-act="chat-del"][data-id="{id_servizio}"]')
    pg.wait_for_selector('[data-act="chat-del-ok"]')
    pg.click('[data-act="chat-del-ok"]')
    # Il TESTO dello stato vuoto, non il solo `.empty`: qui non si cambia pagina,
    # quindi la race di `prototype_flow.py` non c'e' — ma `.empty` da solo e' un
    # selettore che vive su piu' viste, e un'attesa cosi' e' fragile per costruzione.
    pg.wait_for_selector('.empty:has-text("Nessuna chat autorizzata")')
    assert 'Canale segnali' not in pg.inner_text('#app'), \
        'la chat eliminata e- ancora nella lista'
    shot(pg, '07-chat-eliminata')

    # E il tab del parser non deve restare con un collegamento fantasma: la
    # DELETE toglie i link nella stessa transazione (PR #112), quindi la
    # casella non c'e' proprio piu'.
    pg.click('nav a[href="#/parsers"]')
    pg.wait_for_selector('a.name:has-text("Parser delle chat")')
    pg.click('a.name:has-text("Parser delle chat")')
    pg.wait_for_selector('#paste-msg')
    pg.click('a:has-text("Chat assegnate")')
    pg.wait_for_selector('#chat-assegnate')
    assert pg.locator(f'#chat-assegnate input[data-chat-id="{id_servizio}"]').count() == 0, \
        'la chat eliminata compare ancora fra quelle assegnabili'
    shot(pg, '08-parser-senza-chat')

    # ---- 6) un guasto PERSISTENTE si dice, non si nasconde ---------------
    # La ripresa dopo l'errore, da sola, sposta il difetto invece di chiuderlo:
    # con la rete giu' il sondaggio ritenterebbe finche' la scheda resta aperta,
    # e la pagina continuerebbe a dire «In attesa del codice…» di una cosa che non
    # sta arrivando. Dopo cinque tentativi falliti DI FILA deve smettere e mostrare
    # il motivo. Rilievo di GPT-5.5 sulla PR #114, sul commit che aveva appena
    # corretto il difetto opposto.
    pg.click('nav a[href="#/chats"]')
    pg.wait_for_selector('[data-act="chat-verifica-start"]')
    pg.click('[data-act="chat-verifica-start"]')
    pg.wait_for_selector('#codice-verifica')
    guasto['perenne'] = True
    pg.wait_for_selector('.toast:has-text("server non risponde")', timeout=60000)
    # Il toast vive 2,6 secondi. Quello che conta e' cosa resta DOPO: senza una
    # scritta sulla pagina, chi guarda lo schermo un attimo piu' tardi trova un
    # «Caricamento…» che non finira' mai e nessuna spiegazione. Si aspetta quindi
    # che il toast sparisca e si controlla che la pagina parli ancora.
    pg.wait_for_selector('.toast', state='detached', timeout=15000)
    testo_guasto = pg.inner_text('#app')
    assert 'Non riesco a leggere le tue chat' in testo_guasto, (
        f'sparito il toast, la pagina non spiega piu- niente: {testo_guasto[:300]!r}')
    assert 'Caricamento' not in testo_guasto, (
        f'la pagina e- rimasta su «Caricamento…»: {testo_guasto[:300]!r}')
    assert pg.locator('[data-act="ricarica"]').count() == 1, \
        'manca il modo di riprovare senza ricaricare tutta la pagina'
    shot(pg, '09-guasto-persistente')
    guasto['perenne'] = False

    # ---- 7) il codice rifiutato lo DICE, senza ricaricare (#116) ----------
    # Il caso: due amministratori della stessa chat, e uno l'ha gia' collegata.
    # Il server rifiuta — giusto — ma `in_attesa` resta vero, perche' il codice
    # NON si consuma. Senza questo passo la pagina resterebbe sul conto alla
    # rovescia fino alla scadenza e poi direbbe «scaduto senza essere usato»,
    # cioe' il contrario di quello che e' successo.
    #
    # Non si ricarica NIENTE qui dentro, ed e' il punto: l'avviso deve arrivare
    # mentre l'utente sta guardando, o non serve a niente.
    assert DB, 'il flusso non ha ricevuto il percorso del database'
    chat_di_un_altro()
    # «Riprova» ridisegna, e il codice chiesto al passo 6 e' ancora in memoria:
    # non se ne genera un altro, o si perderebbe proprio lo stato da misurare.
    pg.click('[data-act="ricarica"]')
    pg.wait_for_selector('#codice-verifica')
    codice_rifiutato = pg.inner_text('#codice-verifica').strip()
    assert codice_rifiutato.startswith('BETRELAY-'), codice_rifiutato
    stato_rif, corpo_rif = consegna(codice_rifiutato, chat=CANALE_ALTRUI)
    assert stato_rif == 200, (stato_rif, corpo_rif)
    assert corpo_rif.get('ignored') == 'chat_non_disponibile', corpo_rif
    pg.wait_for_selector('#verifica-rifiuto', timeout=30000)
    avviso = pg.inner_text('#verifica-rifiuto').lower()
    assert 'un altro account' in avviso, (
        f'l-avviso non dice che la chat e- di un altro account: {avviso!r}')
    # E deve dire che il codice e' ancora buono: e' la differenza fra «riprova
    # altrove» e «ricomincia da capo», e il server non l'ha consumato davvero.
    assert "un'altra chat" in avviso, (
        f'l-avviso non dice DOVE il codice resta spendibile: {avviso!r}')
    assert 'Canale di un altro' not in pg.inner_text('#app'), \
        'la chat di un altro utente e- comparsa nella lista'
    non_sfonda(pg, 'codice rifiutato')
    shot(pg, '10-codice-rifiutato')

    # ---- 8) l'ALTRO motivo dice una cosa diversa, perche' LO E' ----------
    # Rilievo di CodeRabbit sulla PR #120, ed era vero: la coda «il codice non e'
    # stato consumato: puoi ancora usarlo» e' giusta per la chat occupata — si
    # reincolla altrove e funziona — e FALSA per l'accesso non attivo, dove lo
    # stesso codice viene rifiutato di nuovo dallo stesso cancello finche' il
    # proprietario non riattiva. Un messaggio che manda a riprovare una cosa che
    # non puo' riuscire e' esattamente la frase falsa che questo avviso esiste
    # per togliere.
    #
    # La schermata e' raggiungibile davvero, e va detto perche' non e' ovvio:
    # `stato.me` in `api.js` e' in cache dal boot e non si aggiorna a ogni
    # risposta, quindi chi viene sospeso MENTRE aspetta il codice resta su questa
    # vista invece di finire sulla schermata «Accesso sospeso». Per questo qui si
    # tocca solo il database e non si ricarica la pagina.
    utente_sospeso = sospendi_utente()
    try:
        stato_sosp, corpo_sosp = consegna(codice_rifiutato, chat=CANALE_ALTRUI)
        assert stato_sosp == 200, (stato_sosp, corpo_sosp)
        assert corpo_sosp.get('ignored') == 'accesso_non_attivo', corpo_sosp
        pg.wait_for_selector('#verifica-rifiuto:has-text("accesso")', timeout=30000)
        avviso_acc = pg.inner_text('#verifica-rifiuto').lower()
        assert 'finch' in avviso_acc, (
            f'l-avviso non dice CHE COSA si aspetta prima di riprovare: {avviso_acc!r}')
        assert "un'altra chat" not in avviso_acc, (
            'l-avviso manda a riprovare in un-altra chat, ma il cancello e- '
            f'l-accesso e rifiuterebbe di nuovo: {avviso_acc!r}')
        non_sfonda(pg, 'accesso non attivo')
        shot(pg, '11-accesso-non-attivo')
    finally:
        riattiva_utente(utente_sospeso)

    # ---- 9) motivo E scadenza insieme -----------------------------------
    # La coda ora si dirama su DUE dimensioni, e questo e' il ramo che nessuno
    # dei passi precedenti tocca: un codice rifiutato che POI scade. Dire
    # «riprova» di un codice morto e' la stessa frase falsa dei passi 7 e 8,
    # spostata di dieci minuti. Rilievo di GPT-5.5 sulla PR #120.
    #
    # Si copre UNA delle due combinazioni motivo x scaduto, e lo dico invece di
    # dichiarare la matrice piena: la coda della scadenza e' una costante sola,
    # condivisa dai due motivi, quindi provarla su uno prova il ramo.
    scadi_il_codice(codice_rifiutato)
    pg.wait_for_selector('#verifica-rifiuto:has-text("scaduto")', timeout=30000)
    avviso_scad = pg.inner_text('#verifica-rifiuto').lower()
    assert 'generane un altro' in avviso_scad, (
        f'l-avviso non dice cosa fare di un codice morto: {avviso_scad!r}')
    assert "un'altra chat" not in avviso_scad and 'finch' not in avviso_scad, (
        f'l-avviso manda ancora a riusare un codice scaduto: {avviso_scad!r}')
    non_sfonda(pg, 'rifiutato e scaduto')
    shot(pg, '12-rifiutato-e-scaduto')

    ctx.close()
    b.close()

print('\nERRORI JS:', len(errors))
for e in errors:
    print(' -', e)
sys.exit(1 if errors else 0)
