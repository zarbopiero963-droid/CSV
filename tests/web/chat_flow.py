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

Viewport a 390px per tutta la durata: la pagina non deve sfondare in orizzontale
(regola 2 di CLAUDE.md). Zero errori in console, come tutti i flussi web di qui.

Argomenti: base_url (con /app/), cartella screenshot.
"""

import json
import pathlib
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

# Deve combaciare con l'ambiente della fixture: il segreto del webhook ne deriva.
BOT = '123456789:AAFinto'
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


def consegna(testo):
    """Una consegna di Telegram autentica, col segreto derivato dal bot.

    E' il pezzo che rende questo flusso un end-to-end vero: il codice fa lo stesso
    giro che fara' in produzione — schermo, canale, webhook — invece di essere
    scritto a mano nel database o inventato da uno stub.
    """
    payload = {'message': {'chat': {'id': CANALE, 'title': 'Canale segnali',
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
    shot(pg, '09-guasto-persistente')
    guasto['perenne'] = False

    ctx.close()
    b.close()

print('\nERRORI JS:', len(errors))
for e in errors:
    print(' -', e)
sys.exit(1 if errors else 0)
