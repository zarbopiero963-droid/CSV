import csv, hashlib, io, logging, os, re, sqlite3, threading
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(title='XTrader Signal Relay')

@app.on_event('startup')
async def register_telegram_webhook():
    token = os.getenv('TELEGRAM_BOT_TOKEN', '')
    public_url = os.getenv('PUBLIC_URL', 'https://csv-production-b04e.up.railway.app')
    if token:
        import urllib.request
        url = f'https://api.telegram.org/bot{token}/setWebhook?url={public_url}/telegram/webhook'
        try:
            urllib.request.urlopen(url, timeout=10).read()
        except Exception:
            pass

DB_PATH = os.getenv('DB_PATH', '/tmp/signals.db')
TOKEN = os.getenv('CSV_ACCESS_TOKEN', '')
HEADERS = ['Provider','EventId','EventName','MarketId','MarketName','MarketType','SelectionId','SelectionName','Handicap','Price','MinPrice','MaxPrice','BetType','Points']
DEFAULT_PARSER = 'Parser_Telegram_XTrader_v1'
PIERO_PROFILE = 'PIERO'

class MessageIn(BaseModel):
    message: str

class ParserIn(BaseModel):
    name: str
    header: str
    market_name: str = 'Over/Under 1,5 gol'
    market_type: str = 'OVER_UNDER_15'
    selection_name: str = 'Over 1,5 goal'
    handicap: str = '0'
    bet_type: str = 'PUNTA'

class ProfileIn(BaseModel):
    name: str
    chat_ids: str
    parser: str = DEFAULT_PARSER


def db():
    c = sqlite3.connect(DB_PATH)
    c.execute('CREATE TABLE IF NOT EXISTS signals (id INTEGER PRIMARY KEY AUTOINCREMENT, csv TEXT NOT NULL, parser TEXT, profile TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, expires_at INTEGER)')
    # Migrate databases created before profile support.
    try:
        c.execute('ALTER TABLE signals ADD COLUMN expires_at INTEGER')
    except sqlite3.OperationalError:
        pass
    try:
        c.execute('ALTER TABLE signals ADD COLUMN profile TEXT')
    except sqlite3.OperationalError:
        pass
    c.execute('CREATE TABLE IF NOT EXISTS parsers (name TEXT PRIMARY KEY, header TEXT NOT NULL, market_name TEXT, market_type TEXT, selection_name TEXT, handicap TEXT, bet_type TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS profiles (name TEXT PRIMARY KEY, chat_ids TEXT NOT NULL, parser TEXT NOT NULL)')
    c.execute('INSERT OR IGNORE INTO parsers VALUES (?,?,?,?,?,?,?)', (DEFAULT_PARSER, 'P.Bet. PREMACHT 0,5HT', 'Over/Under 1,5 gol', 'OVER_UNDER_15', 'Over 1,5 goal', '0', 'PUNTA'))
    # Preserve the existing Telegram setup as the default PIERO feed.
    c.execute('INSERT OR IGNORE INTO profiles(name,chat_ids,parser) VALUES (?,?,?)', (PIERO_PROFILE, os.getenv('TELEGRAM_ALLOWED_CHAT_IDS', ''), DEFAULT_PARSER))
    c.execute("UPDATE signals SET profile=? WHERE profile IS NULL", (PIERO_PROFILE,))
    c.commit()
    return c


# XTrader reads the feed as UTF-8 with a BOM. Proven on x1.csv, the file the
# Bridge writes and XTrader consumes: Notepad reports "UTF-8 con BOM" and the
# header is fully quoted. The repository used to claim the opposite, and the
# feed went out without a BOM: no error was raised anywhere, the signal simply
# never arrived.
CSV_BOM = '\ufeff'

# One quoted field, allowing the doubled quote that escapes a quote inside it.
_FIELD = r'"(?:[^"]|"")*"'
_ROW = re.compile('^%s(?:,%s){%d}$' % (_FIELD, _FIELD, len(HEADERS) - 1))
HEADER_LINE = ','.join('"%s"' % h for h in HEADERS)


def csv_text(*rows):
    """Serialise rows the way XTrader expects them. Single source of the format.

    14 quoted fields, comma separated, CRLF terminated, UTF-8 with a BOM. Both
    make_csv() and empty_csv() go through here so the format is defined once:
    the two used to configure the writer separately, which is how a BOM added
    to one and forgotten in the other would have gone unnoticed.
    """
    out = io.StringIO(newline='')
    csv.writer(out, quoting=csv.QUOTE_ALL, lineterminator='\r\n').writerows(rows)
    return CSV_BOM + out.getvalue()


def verify_csv(text):
    """Return the text if it is a CSV XTrader can read, raise ValueError if not.

    Checked at the point the data is produced rather than the point it is
    served: a malformed row must not exist even for the 90 seconds of the TTL.
    The feed path deliberately does not call this — a defect in the verifier
    must not turn into a 500 towards XTrader.
    """
    if not text.startswith(CSV_BOM):
        raise ValueError('CSV senza BOM: XTrader non leggerebbe la prima colonna')
    body = text[len(CSV_BOM):]
    if not body.endswith('\r\n'):
        raise ValueError('CSV senza terminatore CRLF finale')
    # Ogni CR seguito da LF e ogni LF preceduto da CR: il contratto dice CRLF, e
    # un verificatore che accetta un CR o un LF isolati non sta vincolando il
    # contratto che dichiara di vincolare.
    residuo = body.replace('\r\n', '')
    if '\r' in residuo or '\n' in residuo:
        raise ValueError('CSV con un CR o un LF non appaiati in CRLF')
    # Lo split su un corpo che finisce con CRLF lascia un ultimo elemento vuoto:
    # quello si scarta. Ogni ALTRO elemento vuoto e' una riga in bianco e va
    # respinta — filtrarli tutti, come faceva la prima versione, le accettava.
    lines = body.split('\r\n')[:-1]
    if not lines:
        raise ValueError('CSV vuoto: manca anche l\'intestazione')
    if '' in lines:
        raise ValueError('CSV con una riga vuota alla posizione %d' % (lines.index('') + 1))
    if lines[0] != HEADER_LINE:
        raise ValueError('intestazione diversa dal contratto (%d colonne rilevate)'
                         % len(lines[0].split(',')))
    if len(lines) > 2:
        raise ValueError('CSV con %d righe: atteso intestazione piu al massimo un segnale'
                         % len(lines))
    for n, line in enumerate(lines[1:], start=2):
        if not _ROW.match(line):
            raise ValueError('riga %d non ha %d campi tutti fra virgolette' % (n, len(HEADERS)))
    return text


def make_csv(row):
    return csv_text(HEADERS, row)


def store_signal(c, csv_text_value, parser, profile=PIERO_PROFILE):
    # One message produces one row; the next message only replaces this profile's row.
    # Fail closed: a CSV that does not pass verification is never stored.
    verify_csv(csv_text_value)
    c.execute('DELETE FROM signals WHERE profile=?', (profile,))
    c.execute('INSERT INTO signals(csv,parser,profile,expires_at) VALUES (?,?,?,?)', (csv_text_value, parser, profile, int(__import__('time').time()) + 90))


def parse_message(message, cfg):
    if cfg['header'].lower() not in message.lower():
        return None
    line = next((x.strip() for x in message.splitlines() if '🆚' in x), '')
    if not line:
        return None
    event = line.split('🆚', 1)[1].strip()
    event = event.splitlines()[0].strip()
    # The final " v " is the separator; earlier occurrences remain in a team name.
    ms = list(re.finditer(r'\s+v\s+', event, flags=re.I))
    if ms:
        s = ms[-1]
        event = event[:s.start()].strip() + ' - ' + event[s.end():].strip()
    row = ['XTrader', '', event, '', cfg['market_name'], cfg['market_type'], '', cfg['selection_name'], cfg['handicap'], '', '', '', cfg['bet_type'], '']
    return {'event': event, 'csv': make_csv(row)}


def auth(token):
    if TOKEN and token != TOKEN:
        raise HTTPException(401, 'Unauthorized')


def get_parser(c, name):
    r = c.execute('SELECT name,header,market_name,market_type,selection_name,handicap,bet_type FROM parsers WHERE name=?', (name,)).fetchone()
    if not r:
        raise HTTPException(404, 'Parser non trovato')
    return dict(zip(['name','header','market_name','market_type','selection_name','handicap','bet_type'], r))


def get_profile(c, name):
    r = c.execute('SELECT name,chat_ids,parser FROM profiles WHERE name=?', (name,)).fetchone()
    if not r:
        raise HTTPException(404, 'Profilo non trovato')
    return dict(zip(['name', 'chat_ids', 'parser'], r))


def empty_csv():
    return csv_text(HEADERS)


# Quante RIGHE guaste distinte il percorso di consegna ha degradato a feed vuoto,
# e per quale motivo l'ultima volta.
#
# Serve perche' quel fallback non puo' sollevare — un raise verso XTrader
# diventerebbe un 500 — ma degradare in silenzio ha il difetto opposto: un bug in
# verify_csv() azzererebbe OGNI feed di OGNI cliente, e dall'esterno si vedrebbe
# solo «nessun segnale», indistinguibile da un giorno senza partite. Il contatore
# rende visibile la differenza.
#
# Conta le RIGHE, non le richieste, e la distinzione e' tutta la sua utilita':
# XTrader interroga il feed a raffica e la risposta e' `no-store`, quindi una sola
# riga vecchia resterebbe guasta per tutti i 90 secondi del TTL e produrrebbe
# decine di «scarti» per un unico evento benigno — cioe' un contatore che sale in
# fretta, che e' esattamente il segnale con cui si dovrebbe riconoscere il guasto
# vero. Il riconoscimento passa da un digest: distingue due righe diverse senza
# conservare il segnale di un cliente in una variabile globale.
#
# La chiave della deduplica e' la COPPIA profilo+riga, non la riga sola. Con un
# digest globale il contatore sbagliava in due modi opposti, entrambi misurati su
# 32d00ae e segnalati da Fable 5 e GPT-5.5:
#
#   - due profili con la STESSA riga guasta contavano 1 invece di 2, perche' il
#     secondo risultava «gia' visto»: un guasto che colpisce due clienti si
#     leggeva come se ne avesse colpito uno;
#   - due profili con righe guaste DIVERSE contavano 12 richieste su 12, perche'
#     l'impronta globale cambiava a ogni hit essendo quella dell'altro profilo —
#     cioe' di nuovo la raffica che la deduplica doveva eliminare, ricomparsa in
#     scenario multiutente, che e' proprio quello verso cui va questo servizio.
#
# Per un singolo profilo la voce e' l'ultima riga scartata, non un insieme: due
# righe guaste alternate sullo stesso feed contano a ogni cambio, ed e' voluto —
# un feed che oscilla fra due righe invalide e' un guasto, non un evento unico.
#
# Vive in memoria di proposito: e' una spia di salute del processo, non un dato da
# conservare, e non deve aggiungere una scrittura sul percorso di consegna. Ne
# seguono due limiti da tenere presenti leggendo `/health`, segnalati da GPT-5.5:
# il valore e' PER PROCESSO, quindi con piu' worker o piu' istanze su Railway ogni
# risposta riporta solo la propria quota e non un totale; e si azzera a ogni
# riavvio. Il pannello Salute dell'admin non deve presentarlo come un totale
# globale.
def _scarti_azzerati():
    """Lo stato iniziale del contatore, in un posto solo.

    Esiste perche' anche i test devono azzerarlo: una copia del dizionario
    scritta a mano la' divergerebbe da questa al primo campo aggiunto.

    `impronte` e' una mappa profilo -> digest, non un digest solo: la chiave della
    deduplica e' la COPPIA profilo+riga. Una impronta globale sbagliava in due
    modi opposti, entrambi misurati e fissati da test — vedi il commento sotto.
    Cresce di una voce per profilo con un feed guasto, quindi e' limitata dal
    numero di profili e si azzera al riavvio.
    """
    return {'n': 0, 'ultimo': '', 'impronte': {}}


_SCARTI_CONSEGNA = _scarti_azzerati()
# Gli handler di FastAPI sono sincroni, quindi girano nel threadpool: due
# richieste possono incrementare insieme e `+= 1` non e' atomico. Segnalato da
# Fable 5. Il lock costa nulla su un percorso che tocca comunque il database, e
# senza di esso il contatore perderebbe proprio gli incrementi sotto il carico in
# cui conta di piu'.
_SCARTI_LOCK = threading.Lock()


def _registra_scarto(profile, csv_scartato, motivo):
    """Registra uno scarto di consegna. Restituisce True se la riga e' nuova.

    Sta in una funzione propria per due ragioni. La prima e' che la sezione
    critica diventa esercitabile da un test: chiamata attraverso `profile_csv()`
    e' preceduta dall'apertura del database, che serializza i thread e rende la
    race irriproducibile — misurato, il lock si puo' togliere e un test di
    concorrenza sul percorso completo resta verde. La seconda e' che chi domani
    aggiungera' un secondo punto di degradazione trova qui la logica, invece di
    ricopiarla.

    Non solleva: e' chiamata dal percorso di consegna, dove un errore
    diventerebbe un 500 verso XTrader.
    """
    impronta = hashlib.sha256(csv_scartato.encode('utf-8', 'replace')).hexdigest()[:16]
    with _SCARTI_LOCK:
        riga_nuova = _SCARTI_CONSEGNA['impronte'].get(profile) != impronta
        if riga_nuova:
            _SCARTI_CONSEGNA['n'] += 1
            _SCARTI_CONSEGNA['impronte'][profile] = impronta
        _SCARTI_CONSEGNA['ultimo'] = str(motivo)
    return riga_nuova


def profile_csv(profile, token):
    auth(token)
    c = db()
    get_profile(c, profile)
    c.execute("DELETE FROM signals WHERE profile=? AND expires_at IS NOT NULL AND expires_at <= strftime('%s','now')", (profile,))
    c.commit()
    r = c.execute('SELECT csv FROM signals WHERE profile=? ORDER BY id DESC LIMIT 1', (profile,)).fetchone()
    c.close()
    # store_signal() verifica cio' che SCRIVE, ma una riga finita nel database da
    # una versione precedente e' gia' la' e uscirebbe cosi' com'e' — senza BOM,
    # per i secondi che le restano. Qui si serve il feed vuoto invece del
    # contenuto sospetto: e' sempre un CSV valido e XTrader non va in errore.
    #
    # E' l'UNICA verifica sul percorso di consegna, ed e' innocua per costruzione
    # perche' non puo' produrre un errore: al massimo degrada a «nessun segnale».
    # Un raise qui diventerebbe un 500 verso XTrader.
    body = empty_csv()
    if r:
        try:
            body = verify_csv(r[0])
        except ValueError as e:
            # Il motivo, mai il contenuto: i messaggi di verify_csv() sono
            # strutturali (conteggi, posizioni, numeri di riga) e /health e' un
            # endpoint senza token. Il nome del profilo resta nel log del server,
            # dove serve per la diagnosi e dove non e' un segreto: e' gia' nell'URL.
            # Il log segue il contatore: righe identiche a ogni richiesta per 90
            # secondi renderebbero illeggibile proprio il log che serve a capire.
            # Il log sta fuori dal lock di proposito: non si tiene un lock durante
            # l'I/O.
            if _registra_scarto(profile, r[0], e):
                logging.getLogger('xtrader.relay').warning(
                    'feed del profilo %s degradato a sola intestazione: %s', profile, e)
            body = empty_csv()
    return Response(body, media_type='text/csv', headers={'Cache-Control': 'no-store'})


@app.get('/')
def root():
    return {'service': 'xtrader-signal-relay', 'status': 'online', 'csv': '/xtrader.csv'}

@app.get('/health')
def health():
    """Liveness plus the CSV format self-check.

    The check is wired here on purpose. In the Bridge the equivalent function
    existed and was used elsewhere, but nothing on the health panel looked at
    it: the only warning was a single log line at startup, and a CSV the program
    could not use sat there for months. A check nobody reads is not a check.
    """
    # Derived from HEADERS rather than hand-written, so it cannot drift if the
    # columns change. One field carries both a comma and an escaped quote: those
    # are the two characters that break a CSV, and the sample exists to exercise
    # them, not to look like a real signal.
    sample = [''] * len(HEADERS)
    sample[HEADERS.index('EventName')] = 'Squadra "A", Citta - Altra'
    try:
        verify_csv(empty_csv())
        verify_csv(make_csv(sample))
        csv_state = 'ok'
    except ValueError as e:
        csv_state = 'fault: %s' % e
    # Il contatore degli scarti di consegna e' esposto ma NON fa scattare
    # `degraded`, e la ragione non e' timidezza: lo scarto atteso — una riga
    # scritta dalla versione precedente, subito dopo un deploy — e' benigno e si
    # risolve da se' entro i 90 secondi del TTL. Farlo diventare `degraded`
    # lascerebbe questo processo «malato» per tutta la sua vita dopo ogni deploy
    # normale, cioe' un allarme sempre acceso, che e' il modo piu' rapido per
    # insegnare a ignorarlo.
    #
    # Cio' che conta e' il RITMO: un contatore che continua a salire e' il bug in
    # verify_csv() che azzera i feed, e si vede confrontando due letture. Chi
    # guarda e' il pannello Salute dell'admin, che legge questo numero.
    #
    # `status` resta la risposta a «il formato che produco e' valido?», che e'
    # esattamente quello che misura `csv`.
    # Una lettura sola sotto lock: senza, `n` e `ultimo_scarto` potrebbero venire
    # da due momenti diversi e la risposta descriverebbe uno stato mai esistito.
    with _SCARTI_LOCK:
        scarti, motivo = _SCARTI_CONSEGNA['n'], _SCARTI_CONSEGNA['ultimo']
    stato = {'status': 'ok' if csv_state == 'ok' else 'degraded',
             'csv': csv_state, 'feed_scartati': scarti}
    if scarti:
        stato['ultimo_scarto'] = motivo
    return stato

@app.get('/xtrader.csv')
def xtrader_csv(token: str | None = Query(None)):
    return profile_csv(PIERO_PROFILE, token)

@app.get('/profiles/{profile}.csv')
def named_profile_csv(profile: str, token: str | None = Query(None)):
    return profile_csv(profile, token)

@app.get('/api/parsers')
def list_parsers(x_admin_token: str | None = Header(None)):
    auth(x_admin_token)
    c = db()
    rows = c.execute('SELECT name,header,market_name,market_type,selection_name,handicap,bet_type FROM parsers ORDER BY name').fetchall()
    c.close()
    keys = ['name','header','market_name','market_type','selection_name','handicap','bet_type']
    return [dict(zip(keys, r)) for r in rows]

@app.post('/api/parsers')
def save_parser(data: ParserIn, x_admin_token: str | None = Header(None)):
    auth(x_admin_token)
    c = db()
    c.execute('INSERT OR REPLACE INTO parsers VALUES (?,?,?,?,?,?,?)', tuple(data.model_dump().values()))
    c.commit()
    c.close()
    return {'ok': True, 'parser': data.name}

@app.delete('/api/parsers/{name}')
def delete_parser(name: str, x_admin_token: str | None = Header(None)):
    auth(x_admin_token)
    c = db()
    c.execute('DELETE FROM parsers WHERE name=?', (name,))
    c.commit()
    c.close()
    return {'ok': True}

@app.get('/api/profiles')
def list_profiles(x_admin_token: str | None = Header(None)):
    auth(x_admin_token)
    c = db()
    rows = c.execute('SELECT name,chat_ids,parser FROM profiles ORDER BY name').fetchall()
    c.close()
    return [dict(zip(['name', 'chat_ids', 'parser'], r)) for r in rows]

@app.post('/api/profiles')
def save_profile(data: ProfileIn, x_admin_token: str | None = Header(None)):
    auth(x_admin_token)
    c = db()
    get_parser(c, data.parser)
    c.execute('INSERT OR REPLACE INTO profiles(name,chat_ids,parser) VALUES (?,?,?)', (data.name, data.chat_ids, data.parser))
    c.commit()
    c.close()
    return {'ok': True, 'profile': data.name}

@app.delete('/api/profiles/{name}')
def delete_profile(name: str, x_admin_token: str | None = Header(None)):
    auth(x_admin_token)
    c = db()
    c.execute('DELETE FROM profiles WHERE name=?', (name,))
    c.execute('DELETE FROM signals WHERE profile=?', (name,))
    c.commit()
    c.close()
    return {'ok': True}

@app.post('/api/parsers/{name}/test')
def test_parser(name: str, data: MessageIn, x_admin_token: str | None = Header(None)):
    auth(x_admin_token)
    c = db()
    cfg = get_parser(c, name)
    parsed = parse_message(data.message, cfg)
    if not parsed:
        c.close()
        raise HTTPException(422, 'Messaggio non riconosciuto da questo parser')
    store_signal(c, parsed['csv'], name, PIERO_PROFILE)
    c.commit()
    c.close()
    return {'ok': True, 'parser': name, 'event': parsed['event'], 'csv': parsed['csv']}

@app.post('/api/test-message')
def test_message(data: MessageIn, x_admin_token: str | None = Header(None), parser: str = Query(DEFAULT_PARSER)):
    return test_parser(parser, data, x_admin_token)

@app.post('/telegram/webhook')
async def telegram_webhook(request: Request):
    payload = await request.json()
    msg = payload.get('message') or payload.get('channel_post') or {}
    chat = msg.get('chat') or {}
    chat_id = str(chat.get('id', ''))
    text = msg.get('text') or msg.get('caption') or ''
    if not text:
        return {'ok': True, 'ignored': 'no_text'}
    c = db()
    profiles = c.execute('SELECT name,chat_ids,parser FROM profiles ORDER BY name').fetchall()
    profile = next((dict(zip(['name', 'chat_ids', 'parser'], row)) for row in profiles
                    if chat_id in {x.strip() for x in row[1].split(',') if x.strip()}), None)
    if not profile:
        c.close()
        return {'ok': True, 'ignored': 'chat_not_allowed'}
    cfg = get_parser(c, profile['parser'])
    parsed = parse_message(text, cfg)
    if not parsed:
        c.close()
        return {'ok': True, 'ignored': 'parser_no_match'}
    try:
        store_signal(c, parsed['csv'], profile['parser'], profile['name'])
    except ValueError:
        # Deterministic failure: the same message would produce the same broken
        # CSV, so answer 200 and let Telegram stop retrying.
        #
        # The reason does NOT leave in the response: this endpoint is public,
        # Telegram posts to it, and there is no reason to tell an arbitrary
        # caller how the CSV is built. The condition stays visible on /health,
        # which verifies the format on every call.
        c.close()
        return {'ok': True, 'ignored': 'csv_non_valido'}
    c.commit()
    c.close()
    return {'ok': True, 'profile': profile['name'], 'event': parsed['event']}

# Prototipo della web app SaaS: file statici, nessuna dipendenza aggiuntiva.
# Montato per ultimo per non intercettare gli endpoint del relay.
WEB_DIR = Path(__file__).parent / 'web'
if WEB_DIR.is_dir():
    app.mount('/app', StaticFiles(directory=WEB_DIR, html=True), name='app')
