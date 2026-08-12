import asyncio, csv, hashlib, io, json, logging, os, re, secrets, sqlite3, threading, time
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(title='XTrader Signal Relay')

def webhook_secret(bot_token):
    """Il segreto che prova che una consegna viene da Telegram.

    DERIVATO dal token del bot invece di essere una variabile a se'. La ragione e'
    che una variabile nuova lascerebbe una finestra fra il deploy e la sua
    configurazione, e in quella finestra bisognerebbe scegliere fra un webhook
    muto e un webhook aperto — due modi di sbagliare. Derivandolo, il valore
    esiste sempre dove esiste il bot, non sta nel repository, e Telegram lo
    riceve alla registrazione senza che nessuno faccia niente.

    Non contiene il token e non lo rivela: e' un digest. Se contenesse il token,
    ogni consegna di Telegram lo porterebbe in un header, e da li' nei log di
    qualunque proxy davanti al servizio.

    Senza bot restituisce stringa vuota, e in quel caso il webhook **rifiuta
    tutto**: senza il token non c'e' modo di validare nessuna consegna, quindi
    questa istanza non ne accetta nessuna. Non che non possano arrivarne — Telegram
    puo' consegnare attraverso una registrazione fatta da un deploy precedente, e
    la distinzione l'ha segnalata CodeRabbit — ma un'istanza che non sa
    riconoscerle non ha niente da guadagnare ad accettarle. La prima
    versione accettava — riaprendo il difetto in un ramo, perche'
    `TELEGRAM_ALLOWED_CHAT_IDS` popola il profilo indipendentemente dal bot e
    un'istanza senza bot ma coi chat_id era iniettabile. Segnalato da CodeRabbit.
    """
    if not bot_token:
        return ''
    return hashlib.sha256(('betrelay-webhook-v1:' + bot_token).encode('utf-8')).hexdigest()


# Esito dell'ultimo tentativo di registrazione: None = non tentato (nessun bot),
# True = Telegram conosce il segreto, False = tentativo fallito.
#
# Serve perche' l'handler INGOIA le eccezioni, e senza questo stato un fallimento
# sarebbe invisibile. Sotto lock insieme al momento dell'ultimo tentativo, perche'
# da quando la ri-registrazione avviene anche da una richiesta (vedi
# `assicura_registrazione`) due consegne concorrenti scriverebbero entrambe:
# segnalato da Sourcery. Stessa forma del lock sugli scarti di consegna.
_WEBHOOK_REGISTRATO = None
_ULTIMO_TENTATIVO = 0.0
_WEBHOOK_LOCK = threading.Lock()

# I tentativi sono NUMERATI, e l'esito ricorda da quale tentativo viene.
#
# Serve perche' la chiamata di rete avviene fuori dal lock — deve, o una
# `setWebhook` lenta bloccherebbe ogni consegna — quindi l'ordine in cui i
# tentativi FINISCONO non e' l'ordine in cui sono PARTITI. Un tentativo partito
# prima, andato in timeout dopo dieci secondi e fallito, scriveva `False` sopra il
# `True` di uno partito dopo e riuscito: `/health` avrebbe detto «non registrato»
# di un webhook registrato, e ogni consegna rifiutata avrebbe ritentato per niente.
# Segnalato da Claude Fable 5.
#
# Il rimedio non e' rendere `True` appiccicoso: un fallimento vero — bot cambiato,
# registrazione sovrascritta da un altro deploy — diventerebbe invisibile per
# sempre, e questo flag non deve mentire in quella direzione. Vince il tentativo
# piu' RECENTE, non l'ultimo a finire.
_TENTATIVI_EMESSI = 0
_TENTATIVO_DELL_ESITO = 0

# Quanto attendere prima di ritentare una registrazione a partire da una consegna
# rifiutata. Serve perche' quel percorso e' raggiungibile da CHIUNQUE: senza
# freno, una raffica di POST forgiati diventerebbe una raffica di chiamate verso
# api.telegram.org fatte da noi.
ATTESA_FRA_TENTATIVI_S = 60


def _chiama_set_webhook(bot_token, public_url):
    """Registra il webhook col segreto. True solo se Telegram dice `ok`.

    Il controllo su `ok` non e' pedanteria: **il codice HTTP non basta**. Telegram
    segnala parte dei rifiuti con `HTTP 200` e `{"ok": false, "description": ...}`
    nel corpo — non tutti: un token inesistente da' 404 e un `secret_token` con
    caratteri non ammessi da' 400, e quelli arrivano qui come eccezione. Servono
    entrambe le condizioni, risposta ricevuta **e** `ok` vero, o il flag direbbe
    «registrato» in un caso in cui non lo e', cioe' mentirebbe nella direzione
    pericolosa. Segnalato da Sourcery; la precisazione su quali rifiuti sono 200 e
    quali no e' di CodeRabbit.

    Il segreto viaggia nel CORPO del POST, non nell'URL: un URL non e' un posto
    riservato, finisce nei log di ogni intermediario che lo tocca, e questa
    chiamata si ripete a ogni deploy e a ogni autoriparazione. Il token del bot
    resta nel percorso perche' l'API di Telegram lo mette li' e non c'e' modo di
    spostarlo. Segnalato da GPT-5.5 e Fable 5; lo vincola
    `test_il_segreto_non_finisce_nell_URL_ma_nel_CORPO`.

    Niente viene loggato da qui — ne' la `description` di un errore, che Telegram
    fa eco all'URL inviato.
    """
    import urllib.parse
    import urllib.request
    parametri = urllib.parse.urlencode({
        'url': f'{public_url}/telegram/webhook',
        'secret_token': webhook_secret(bot_token),
    }).encode('utf-8')
    url = f'https://api.telegram.org/bot{bot_token}/setWebhook'
    # Il `Content-Type` e' dichiarato qui, non lasciato al default di `urllib`.
    # Non e' una correzione: `urllib` metterebbe comunque
    # `application/x-www-form-urlencoded` per un `data=` di byte, e l'invio
    # funzionava. E' che lo mette dentro il proprio handler al momento dell'invio,
    # quindi il valore non e' osservabile sulla richiesta e **nessun test puo'
    # vincolarlo**: e senza vincolo, il giorno in cui questo corpo diventasse JSON
    # senza intestazione, Telegram non lo interpreterebbe, il segreto non
    # arriverebbe, e la registrazione fallirebbe con la stessa faccia di un
    # problema di rete. Il test chiesto da GPT-5.5 esiste perche' questa riga
    # esiste; l'imprecisione della prima versione di questo commento — che
    # raccontava una correzione dove c'era un irrigidimento — l'ha vista Fable 5.
    richiesta = urllib.request.Request(
        url, data=parametri, method='POST',
        headers={'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with urllib.request.urlopen(richiesta, timeout=10) as r:
            risposta = json.loads(r.read().decode('utf-8'))
        return risposta.get('ok') is True
    except Exception as e:
        # Solo il NOME del tipo, mai il messaggio e mai il traceback. Il messaggio
        # di un'eccezione di `urllib` puo' contenere l'URL, e l'URL contiene il token
        # del bot nel percorso: `logging.exception` qui sarebbe un token nei log a
        # ogni guasto di rete. Il tipo basta per la diagnosi — `URLError` (rete o
        # DNS), `timeout`, `HTTPError` (token o URL rifiutati da Telegram),
        # `JSONDecodeError` (risposta non interpretabile) sono cause diverse e
        # richiedono azioni diverse. Che la causa andasse registrata l'ha segnalato
        # Claude Fable 5; che qui non possa esserlo per intero e' la regola sui token.
        logging.warning('registrazione webhook: chiamata fallita (%s)', type(e).__name__)
        return False


def _stato_registrazione():
    """L'esito dell'ultima registrazione, letto sotto lock e una volta sola.

    Esiste perche' `health()` lo usava TRE volte — per `sano`, per decidere se
    includere la chiave, per il valore — e fuori dal lock, mentre gli altri thread
    lo scrivono dentro. Su CPython non si legge un valore corrotto, ma fra la prima
    e la terza lettura una registrazione puo' completare, e la risposta uscirebbe
    con `status: ok` e `webhook_registrato: false`: un endpoint diagnostico che si
    contraddice non e' diagnostico. Era anche l'unico stato condiviso che `health()`
    leggeva senza il suo lock, mentre per gli scarti prendeva `_SCARTI_LOCK` poche
    righe sopra — un lock preso da tutte le scritture e da nessuna lettura e' una
    decorazione, non un modello. Segnalato da Claude Fable 5 sulla PR #14.
    """
    with _WEBHOOK_LOCK:
        return _WEBHOOK_REGISTRATO


def assicura_registrazione(forza=False):
    """Registra il webhook se non risulta registrato. Restituisce l'esito noto.

    Chiamata all'avvio e — questo e' il punto — anche da una consegna RIFIUTATA.
    Senza il secondo percorso il fail-closed avrebbe un guasto peggiore del
    difetto che chiude: se `setWebhook` fallisce all'avvio e Telegram conserva una
    registrazione vecchia SENZA segreto, ogni consegna legittima prende 403 e i
    segnali si fermano finche' qualcuno non rideploya. Scenario concreto, non
    teorico: e' esattamente lo stato del primo deploy dopo l'introduzione del
    segreto, quando la registrazione precedente non ne aveva uno.

    Il rimedio non e' rinunciare all'enforcement quando la registrazione
    fallisce — quello riaprirebbe la scrittura non autenticata, in silenzio, che
    e' il difetto originale. Il rimedio e' RITENTARE.

    Attenzione a cosa dimostra una consegna rifiutata, perche' la prima versione di
    questo docstring diceva di piu' di quello che si sa: dimostra **solo** che la
    validazione dell'header e' fallita. Non che venga da Telegram, e non che
    Telegram non conosca il segreto — puo' benissimo essere un POST forgiato.
    Segnalato da CodeRabbit. Sono due ipotesi e il ritentativo le copre entrambe
    senza doverle distinguere: se la registrazione era stantia la rimette a posto e
    il segnale arriva col giro dopo (Telegram ritenta le consegne); se la richiesta
    era forgiata costa un tentativo, che il freno di `ATTESA_FRA_TENTATIVI_S`
    limita a uno per minuto. Rifiutare, in entrambi i casi.

    **Un successo passato non spegne il ritentativo**, e la prima versione di questa
    funzione lo spegneva: usciva subito se `_WEBHOOK_REGISTRATO` era `True`, col
    ragionamento «Telegram sa il segreto, non c'e' niente da riparare». Ma una
    consegna rifiutata che arriva mentre il flag dice `True` e' l'unica informazione
    che CONTRADDICE il valore in cache, e veniva buttata via — cioe' l'autoriparazione
    era morta esattamente nel caso in cui una registrazione riuscita puo' diventare
    stantia: qualcuno chiama `setWebhook` sullo stesso bot senza segreto (un altro
    strumento, un deploy vecchio) e da quel momento Telegram consegna senza header.
    Segnali fermi, `/health` che dice `webhook_registrato: true`, e nessun posto dove
    vederlo. Segnalato da Fugu Ultra.

    Quello che deve limitare la frequenza e' il FRENO, non il flag: una raffica di
    POST forgiati costa un tentativo al minuto, che e' il motivo per cui il freno
    esiste. Il flag serve a `/health`, non a decidere se riprovare.

    Bloccante alzato insieme da GPT-5.5 e Claude Fable 5 sulla PR #14.
    """
    global _WEBHOOK_REGISTRATO, _ULTIMO_TENTATIVO
    global _TENTATIVI_EMESSI, _TENTATIVO_DELL_ESITO
    token = os.getenv('TELEGRAM_BOT_TOKEN', '')
    if not token:
        return None
    with _WEBHOOK_LOCK:
        adesso = time.monotonic()
        if not forza and adesso - _ULTIMO_TENTATIVO < ATTESA_FRA_TENTATIVI_S:
            return _WEBHOOK_REGISTRATO
        _ULTIMO_TENTATIVO = adesso
        _TENTATIVI_EMESSI += 1
        mio = _TENTATIVI_EMESSI
    public_url = os.getenv('PUBLIC_URL', 'https://csv-production-b04e.up.railway.app')
    esito = _chiama_set_webhook(token, public_url)
    with _WEBHOOK_LOCK:
        # Solo se nessun tentativo piu' recente ha gia' scritto il suo esito: vedi
        # `_TENTATIVI_EMESSI`. Senza questo confronto un tentativo lento e fallito
        # sovrascrive un successo piu' recente.
        if mio >= _TENTATIVO_DELL_ESITO:
            _TENTATIVO_DELL_ESITO = mio
            _WEBHOOK_REGISTRATO = esito
        return _WEBHOOK_REGISTRATO


_COMPITO_REGISTRAZIONE = None


@app.on_event('startup')
async def avvia_la_registrazione_del_webhook():
    """Fa partire la registrazione DIETRO l'avvio, e lascia completare l'avvio.

    Un handler di `startup` ASGI deve terminare prima che uvicorn cominci a
    servire: finche' non termina il processo non e' pronto, e `/health` non
    risponde affatto — non lentamente, per niente. `register_telegram_webhook`
    ritenta tre volte con timeout di dieci secondi e pause in mezzo, quindi
    attenderla qui significherebbe oltre trenta secondi di indisponibilita' a ogni
    deploy con la rete lenta.

    Metterla in un thread non basta: quello libera l'event loop, non la readiness
    del processo. Sono due cose diverse, e la prima correzione di questo bloccante
    aveva sistemato solo la prima. La distinzione l'ha vista Fugu Ultra sulla
    review finale della PR #14; lo vincola
    `test_l_avvio_non_RITARDA_la_disponibilita_del_servizio`.

    Il riferimento al compito e' tenuto in una variabile di modulo perche' un
    `Task` senza riferimenti puo' essere raccolto dal garbage collector prima di
    finire — e in quel caso la registrazione non avverrebbe, in silenzio, che e'
    il genere di guasto che questa PR passa il tempo a chiudere.
    """
    global _COMPITO_REGISTRAZIONE
    if not os.getenv('TELEGRAM_BOT_TOKEN', ''):
        return
    _COMPITO_REGISTRAZIONE = asyncio.create_task(register_telegram_webhook())


async def register_telegram_webhook():
    """Registra il webhook, ritentando qualche volta.

    I tentativi ripetuti coprono il caso banale e piu' probabile — un errore di
    rete momentaneo mentre il container si avvia — che senza ritentativi
    lascerebbe l'istanza con l'enforcement attivo e Telegram che non conosce il
    segreto. Un fallimento persistente non impedisce l'avvio: il servizio deve
    continuare a servire il feed, e `/health` dice com'e' andata.

    In un THREAD, non sul loop, come fa l'handler del webhook: `setWebhook` ha un
    timeout di dieci secondi e qui si ritenta tre volte con pause in mezzo, quindi
    eseguita sul loop una rete lenta terrebbe l'event loop fermo per decine di
    secondi. Su Railway l'healthcheck interroga `/health` proprio in quella
    finestra, non riceve risposta, e il deploy risulta guasto per un webhook che
    sta soltanto ritentando. Bloccante alzato da Claude Fable 5 e Fugu Ultra sulla
    review finale della PR #14; lo vincola
    `test_l_avvio_non_BLOCCA_il_loop_mentre_chiama_telegram`.
    """
    if not os.getenv('TELEGRAM_BOT_TOKEN', ''):
        return
    try:
        for tentativo in range(3):
            if await asyncio.to_thread(assicura_registrazione, True):
                return
            if tentativo < 2:
                await asyncio.sleep(1 + tentativo)
    except Exception:
        # Da quando questa coroutine gira come `Task` dietro l'avvio, un'eccezione
        # inattesa morirebbe FUORI dal flusso di avvio: nessuno la vedrebbe, e lo
        # stato resterebbe `None`, cioe' «non ancora tentato», per sempre. Il
        # fallimento va REGISTRATO: «non tentato» e «tentato e fallito» sono stati
        # diversi, e solo il secondo dice che c'e' un guasto da guardare.
        # Segnalato da GPT-5.5 come conseguenza dello spostamento in background.
        #
        # Qui il traceback INTERO si puo' registrare, a differenza di
        # `_chiama_set_webhook`: le eccezioni che arrivano fin qui vengono da
        # `asyncio.to_thread` o da `assicura_registrazione`, dove l'URL col token
        # del bot non entra mai. Un `webhook_registrato: false` senza causa non si
        # diagnostica — rete? token? `PUBLIC_URL`? — e la causa mancante l'ha
        # segnalata Claude Fable 5.
        logging.exception('registrazione webhook: il compito e\' terminato con un errore')
        #
        # Solo se nessun tentativo ha registrato un esito: un guasto qui non deve
        # cancellare il `True` di una registrazione riuscita.
        global _WEBHOOK_REGISTRATO
        with _WEBHOOK_LOCK:
            if _WEBHOOK_REGISTRATO is None:
                _WEBHOOK_REGISTRATO = False

DB_PATH = os.getenv('DB_PATH', '/tmp/signals.db')
TOKEN = os.getenv('CSV_ACCESS_TOKEN', '')
# Il segreto del webhook, calcolato una volta all'import come `TOKEN`: `health()` e
# l'handler del webhook lo leggevano entrambi da `os.environ` a ogni chiamata, e
# due letture separate possono divergere. Segnalato da Sourcery.
SEGRETO_WEBHOOK = webhook_secret(os.getenv('TELEGRAM_BOT_TOKEN', ''))
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
    """Rifiuta un token sbagliato — e rifiuta anche quando non ce n'e' uno da confrontare.

    Fail-CLOSED, e il perche' va scritto perche' la versione precedente sembrava
    innocua: `if TOKEN and token != TOKEN` non fa NIENTE quando `CSV_ACCESS_TOKEN`
    e' assente o vuoto. Dieci rotte diventavano pubbliche — 4 in lettura e 6 in
    scrittura, contate sulle rotte vere di `app.routes` e non a memoria: sovrascrivere un profilo, cancellare un parser, iniettare un segnale
    nel CSV che XTrader legge. Il modo di arrivarci non era esotico — bastava
    cancellare una variabile dalla dashboard di Railway. Misurato prima di questa
    correzione, sul percorso HTTP vero: `GET /xtrader.csv` su un servizio senza
    token configurato rispondeva **200 con il feed**, senza un errore da nessuna
    parte. Una serratura che si apre quando le togli la chiave.

    503 e non 401 perche' le due condizioni chiedono cose diverse a chi le legge:
    401 dice «la tua chiave e' sbagliata», 503 dice «questo servizio non e'
    configurato», e chi vede il secondo deve andare a mettere la variabile, non a
    cercare il token giusto. `/health` espone la stessa informazione, cosi' la
    diagnosi non richiede di indovinarla dai codici di stato.

    Il messaggio nomina la variabile — un'indicazione di configurazione, non un
    segreto — e non contiene mai un valore di token, ne' quello atteso ne' quello
    ricevuto: per differenza si impara, e la risposta finisce nei log di chiunque
    stia in mezzo.

    Il confronto usa `secrets.compare_digest` e non `!=`: segnalato da Claude
    Fable 5. `!=` sulle stringhe esce al primo carattere diverso, quindi il tempo
    di risposta racconta quanti caratteri iniziali erano giusti. Su un token unico
    e condiviso l'attacco e' poco praticabile attraverso Internet, ma il confronto
    a tempo costante e' gratuito e non richiede di stimare quanto sia praticabile.
    Il confronto avviene sui BYTE, e il perche' e' un «se» non un «e'»: passando
    le STRINGHE, `compare_digest` solleverebbe `TypeError` su una non ASCII, e un
    token con un accento diventerebbe un 500 invece di un 401 — un modo per far
    scrivere una traccia nei log con un solo parametro di query. Codificando
    entrambi i lati quel caso non esiste piu', e un test lo verifica. Riformulato
    perche' la versione precedente si poteva leggere come se il TypeError avvenisse
    ancora: segnalato da Fugu Ultra.
    """
    if not TOKEN:
        raise HTTPException(503, 'servizio non configurato: manca CSV_ACCESS_TOKEN')
    # Un token assente o vuoto si scarta prima: non c'e' niente da confrontare, e
    # l'unica cosa che questa uscita anticipata rivela e' che era vuoto, cosa che
    # chi l'ha inviato sa gia'.
    if not token or not secrets.compare_digest(token.encode('utf-8'), TOKEN.encode('utf-8')):
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
    # Lo stato dell'autenticazione, per lo stesso motivo per cui `csv` sta qui: un
    # controllo che nessuno legge non e' un controllo. Senza questa riga, un deploy
    # senza `CSV_ACCESS_TOKEN` si scoprirebbe solo notando che ogni rotta risponde
    # 503 — e prima del fail-closed non si scopriva affatto, perche' rispondevano
    # tutte 200.
    #
    # Questo SI' fa scattare `degraded`, a differenza degli scarti di consegna, e la
    # differenza e' se il guasto si ripara da se': una riga scartata scade col TTL
    # entro 90 secondi, una variabile mancante no. Una spia accesa per sempre dopo
    # ogni deploy normale insegna a ignorare la spia; una accesa finche' qualcuno
    # non agisce e' esattamente cio' che serve.
    #
    # Dice «configurato o no», non altro: `/health` e' senza token.
    auth_state = 'ok' if TOKEN else 'non configurato'
    # Lo stato del webhook, sulle stesse due domande di `auth`: l'enforcement e'
    # attivo? e Telegram sa il segreto? Sono cose diverse, e la seconda e' quella
    # che puo' fermare i segnali in silenzio (vedi `_WEBHOOK_REGISTRATO`).
    webhook_state = 'protetto' if SEGRETO_WEBHOOK else 'chiuso senza bot'
    # «Chiuso senza bot» fa scattare `degraded` come `auth`, e per la stessa
    # ragione scritta qui sopra: `TELEGRAM_BOT_TOKEN` mancante e' una variabile
    # mancante, non si ripara da se', e un'istanza senza bot RIFIUTA ogni consegna
    # con 403 — cioe' non riceve nessun segnale. Prima diceva `status: ok` perche'
    # `_WEBHOOK_REGISTRATO` vale `None` quando non c'e' bot, e `None is not False`:
    # su Railway un'istanza incapace di ricevere segnali sarebbe apparsa sana.
    # Segnalato da Fugu Ultra sulla review finale della PR #14, ed era il fratello
    # non corretto della classe che `auth` aveva gia' chiuso due righe sopra.
    #
    # Letto UNA volta e sotto lock (vedi `_stato_registrazione`): con tre letture
    # separate una registrazione che completa nel mezzo faceva uscire `status: ok`
    # accanto a `webhook_registrato: false`.
    registrato = _stato_registrazione()
    # `is True`, non `is not False`: con il bot configurato «sano» significa
    # REGISTRATO. `None` non e' una buona notizia, e' «non ancora» — e un'istanza
    # col bot che non ha mai completato la registrazione non riceve nessun segnale,
    # quindi dichiararla sana a tempo indeterminato era la meta- non corretta della
    # stessa classe chiusa per il caso «nessun bot». Segnalato da GPT-5.5.
    #
    # Conseguenza voluta: nei primi istanti dopo un deploy lo stato e' `degraded`,
    # perche' in quella finestra il relay davvero non puo' ricevere niente. Sta in
    # `README.txt`, accanto al controllo da fare dopo un deploy.
    sano = (csv_state == 'ok' and auth_state == 'ok'
            and webhook_state == 'protetto' and registrato is True)
    stato = {'status': 'ok' if sano else 'degraded',
             'csv': csv_state, 'auth': auth_state, 'webhook': webhook_state,
             'feed_scartati': scarti}
    if registrato is not None:
        # Solo quando un tentativo c'e' stato: su un'istanza senza bot la chiave
        # sarebbe rumore, e una chiave che c'e' sempre e non dice niente e' peggio
        # di una assente.
        stato['webhook_registrato'] = registrato
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
    """Riceve le consegne di Telegram, e SOLO quelle.

    Il filtro dei `chat_id` piu' sotto fa instradamento — decide a quale feed
    appartiene un messaggio — e non puo' autenticare, perche' il `chat_id` arriva
    nel corpo e quindi lo scrive il mittente. Senza il controllo qui sopra questo
    endpoint era un percorso di SCRITTURA non autenticato verso i segnali che
    XTrader legge: misurato, un POST forgiato senza alcun token rispondeva 200 e
    la riga entrava nel feed, mentre leggere lo stesso feed dava 401. Bastavano
    l'URL del servizio, il testo di riconoscimento del parser (che sta in
    `README.txt`) e il `chat_id` del canale, che conosce chi e' nel canale.
    Segnalato da Fugu Ultra, Issue #13.

    403 e non 401: non c'e' una credenziale da correggere, la richiesta non viene
    da chi dice di essere. Il messaggio non contiene mai il segreto — atteso o
    ricevuto — e il confronto e' a tempo costante, perche' questo valore arriva su
    OGNI consegna ed e' quindi il piu' confrontato del servizio.
    """
    if not SEGRETO_WEBHOOK:
        # Nessun bot configurato: non esiste una registrazione presso Telegram,
        # quindi NESSUNA consegna legittima puo' arrivare qui e rifiutare non
        # costa niente. La prima versione accettava, e quello riapriva il difetto
        # in un ramo: `TELEGRAM_ALLOWED_CHAT_IDS` popola il profilo PIERO
        # indipendentemente dal bot, quindi un'istanza senza bot ma con i chat_id
        # configurati era iniettabile da chiunque. Segnalato da CodeRabbit.
        #
        # Niente variabile di override per lo sviluppo locale: sarebbe una
        # scorciatoia che un domani finisce impostata in produzione. Chi prova in
        # locale imposta un `TELEGRAM_BOT_TOKEN` finto e calcola il segreto con
        # `webhook_secret()`, che e' quello che fanno i test.
        raise HTTPException(403, 'Forbidden')
    ricevuto = request.headers.get('X-Telegram-Bot-Api-Secret-Token', '')
    if not ricevuto or not secrets.compare_digest(
            ricevuto.encode('utf-8'), SEGRETO_WEBHOOK.encode('utf-8')):
        # Una consegna senza header (o con quello sbagliato) mentre l'enforcement
        # e' attivo e' essa stessa un indizio: o e' forgiata, o Telegram non
        # conosce il segreto. Nel dubbio si rifiuta E si rimette a posto la
        # registrazione, cosi' il caso «Telegram consegna senza header perche' la
        # registrazione era fallita» si autoripara invece di fermare i segnali
        # fino al prossimo deploy. Telegram ritenta le consegne: il segnale arriva
        # col giro dopo. In un thread per non bloccare il loop, e con il freno di
        # `ATTESA_FRA_TENTATIVI_S` perche' questo percorso lo raggiunge chiunque.
        try:
            await asyncio.to_thread(assicura_registrazione)
        except Exception:
            # Il 403 e' la DECISIONE: questa richiesta non e' autenticata. Il
            # ritentativo e' un rimedio opportunistico che non c'entra con quella
            # decisione, e un rimedio che rovescia il verdetto e' peggio di nessun
            # rimedio: senza questo `try`, un guasto inatteso qui farebbe rispondere
            # 500 invece di 403 — un errore del server, provocabile da un estraneo
            # con un POST, che nasconde i guasti veri nel rumore. Trovato cercando il
            # fratello del `try` intorno al compito di avvio, non da una review.
            logging.exception('webhook: il ritentativo di registrazione e\' fallito')
        raise HTTPException(403, 'Forbidden')
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
