import asyncio, base64, binascii, csv, decimal, difflib, hashlib, hmac, io, json, logging, math, os, re, secrets, sqlite3, threading, time
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
import regex as _regex  # come `re`, ma con `timeout=` sui match: vedi _cerca_regex_utente

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
    versione accettava — riaprendo il difetto in un ramo, perche' un'istanza senza
    bot ma con le chat gia' a database era iniettabile da chiunque. Segnalato da
    CodeRabbit. (Le chat allora ce le metteva `TELEGRAM_ALLOWED_CHAT_IDS` tramite
    il seme; da quando il seme non c'e' piu' — #25 lavoro E — ce le mette l'API,
    ma la conclusione non cambia: le chat esistono comunque, il bot no.)
    """
    if not bot_token:
        return ''
    return hashlib.sha256(('betrelay-webhook-v1:' + bot_token).encode('utf-8')).hexdigest()


def campi_firmati(dati):
    """I campi nella forma TESTUALE su cui Telegram calcola la firma.

    Serve perche' i valori arrivano come JSON, quindi possono essere numeri o booleani, e
    la `data_check_string` e' fatta di testo. La conversione non e' `str()` di Python:
    `str(True)` da' `'True'`, mentre JSON — e quindi Telegram — scrive `true`. Una
    divergenza di quattro caratteri fa fallire la verifica di una firma **valida**, in
    silenzio e per sempre. Segnalato da Claude Fable 5 sulla PR #23.

    I campi vuoti restano **esclusi**: Telegram firma soltanto quelli che manda, e il
    modello Pydantic riempie di stringhe vuote quelli assenti. Includerli cambierebbe la
    stringa e farebbe fallire ogni login vero — un difetto che si vedrebbe solo in
    produzione, perche' un test che costruisce i campi a mano li manderebbe tutti.

    **Assunzione dichiarata:** i campi del Login Widget sono **piatti** — stringhe e
    numeri, nessun oggetto e nessuna lista. La serializzazione dei valori strutturati usa
    il JSON compatto per il caso in cui quell'assunzione cadesse, e un test la vincola,
    ma nessun campo di Telegram e' strutturato oggi.

    Fonte unica di proposito (regola 3): la usano l'endpoint e i test, e due conversioni
    corrette oggi sono due conversioni divergenti domani.
    """
    fuori = {}
    for chiave, valore in dati.items():
        if valore is None:
            continue
        if isinstance(valore, str):
            testo = valore
        elif isinstance(valore, bool):
            # Prima di `int`: in Python `bool` E' un `int`, e senza questo ramo
            # `True` diventerebbe `1`.
            testo = 'true' if valore else 'false'
        else:
            # `separators` compatti: il default di Python mette uno spazio dopo `:` e `,`,
            # quindi un campo strutturato diventerebbe `{"a": 1}` invece di `{"a":1}` e
            # divergerebbe dalla forma canonica che un firmatario usa — una firma VALIDA
            # rifiutata in silenzio. Per gli scalari, che sono tutto cio' che il widget
            # manda oggi, le due forme sono identiche (misurato), quindi la correzione non
            # cambia nessun comportamento reale: chiude solo il caso futuro.
            # Segnalato da Claude Fable 5 sulla PR #23.
            testo = json.dumps(valore, separators=(',', ':'))
        if testo != '':
            fuori[chiave] = testo
    return fuori


def verifica_login_telegram(dati, bot_token):
    """Vero se questi campi vengono davvero dal Login Widget di Telegram.

    Telegram non manda un token da confrontare: manda i campi **in chiaro** piu' un
    `hash` HMAC-SHA256 calcolato con una chiave derivata dal token del bot. Chi non
    verifica quella firma accetta un `id` scritto da chiunque — cioe' accetta «sono
    Piero» da un estraneo, e non c'e' nessun altro controllo dietro.

    L'algoritmo e' quello della documentazione Telegram:

        secret_key        = SHA256(bot_token)
        data_check_string = campi ordinati per chiave, 'k=v' uniti da \\n, senza `hash`
        hash              = HMAC_SHA256(data_check_string, secret_key)

    La firma copre **tutti** i campi, non solo l'identificativo, e i campi entrano
    nella stringa cosi' come arrivano: un campo aggiunto dopo la firma la invalida, ed
    e' cio' che serve — altrimenti chi intercetta un login potrebbe aggiungerne uno che
    il codice futuro leggera'.

    `auth_date` va guardato in entrambe le direzioni. Troppo vecchio: una firma valida
    resterebbe valida per sempre. Nel futuro: un `auth_date` messo a mano in avanti
    sarebbe accettato per sempre.

    Senza bot restituisce `False` e non «accetta tutto»: senza il token non esiste la
    chiave con cui validare, quindi non c'e' niente da validare. E' la stessa forma di
    `webhook_secret`, e il difetto opposto — una serratura che si apre quando le togli
    la chiave — e' quello corretto su `auth()` a luglio.

    Non solleva mai: i dati arrivano da fuori, e un'eccezione su una rotta di login
    sarebbe un 500 pilotabile dall'esterno.
    """
    if not bot_token:
        return False
    atteso = dati.get('hash')
    if not atteso:
        return False
    try:
        eta = time.time() - int(dati['auth_date'])
    except (KeyError, TypeError, ValueError):
        return False
    if abs(eta) > ETA_MASSIMA_LOGIN:
        return False
    stringa = '\n'.join(f'{k}={dati[k]}' for k in sorted(dati) if k != 'hash')
    chiave = hashlib.sha256(bot_token.encode('utf-8')).digest()
    calcolato = hmac.new(chiave, stringa.encode('utf-8'), hashlib.sha256).hexdigest()
    # Confronto a tempo costante: la durata della risposta non deve raccontare quanti
    # caratteri iniziali erano giusti. Stesso motivo per cui `auth()` usa
    # `compare_digest` sul token del feed.
    #
    # E sui BYTE, non sulle stringhe: `compare_digest` su `str` **solleva** `TypeError`
    # se un lato non e' ASCII, e `atteso` e' il campo `hash` che scrive chi chiama. Con
    # le stringhe, un `hash` con un accento faceva rispondere 500 invece di 401 —
    # misurato. Il docstring di `auth()` descrive questa trappola da luglio e io l'ho
    # reintrodotta qui: regola 2, avevo corretto il sito e non cercato la classe.
    # Segnalato da Claude Fable 5 e da CodeRabbit sulla PR #23.
    return hmac.compare_digest(calcolato.encode('utf-8'), str(atteso).encode('utf-8'))


def hash_password(password):
    """L'hash da mettere in `ADMIN_PASSWORD_HASH`, con sale casuale.

    `scrypt` dalla libreria standard: nessuna dipendenza nuova, e un costo di calcolo
    che rende inutile provare le password in serie. I parametri sono quelli consigliati
    per un uso interattivo (`n=16384, r=8, p=1`).

    Il sale sta DENTRO il valore salvato, quindi due hash della stessa password sono
    diversi: senza sale, hash uguali rivelerebbero password uguali e un hash sarebbe
    riconoscibile da una tabella precalcolata.

    Il formato nomina l'algoritmo (`scrypt$sale$derivata`) perche' il giorno che se ne
    cambiasse uno, un valore vecchio deve restare riconoscibile invece di sembrare
    corrotto.
    """
    sale = secrets.token_bytes(16)
    derivata = hashlib.scrypt(password.encode('utf-8'), salt=sale, n=16384, r=8, p=1, dklen=32)
    return 'scrypt$' + base64.b64encode(sale).decode() + '$' + base64.b64encode(derivata).decode()


def verifica_password_admin(password, salvato):
    """Vero se `password` corrisponde all'hash salvato. Fail-closed su tutto il resto.

    Restituisce `False` — non solleva, e non accetta — quando:

    - `salvato` e' vuoto o `None`: la variabile non e' configurata, quindi il percorso
      a password e' **disabilitato**. E' la regola che `auth()` ha imparato a luglio,
      dove `if TOKEN and token != TOKEN` non faceva niente con la variabile vuota e
      dieci rotte diventavano pubbliche cancellandola dalla dashboard;
    - `salvato` e' malformato: e' un errore di configurazione, e un `raise` qui
      diventerebbe un 500 su una rotta di login, cioe' un modo di scoprire dall'esterno
      che la variabile e' scritta male.

    Confronto a tempo costante, per la ragione di sempre: per differenza si impara.
    """
    if not salvato:
        return False
    try:
        algoritmo, sale_b64, derivata_b64 = salvato.split('$')
        if algoritmo != 'scrypt':
            return False
        sale = base64.b64decode(sale_b64, validate=True)
        derivata = base64.b64decode(derivata_b64, validate=True)
        if not sale or not derivata:
            return False
    except (ValueError, TypeError, AttributeError, binascii.Error):
        return False
    calcolata = hashlib.scrypt(password.encode('utf-8'), salt=sale,
                               n=16384, r=8, p=1, dklen=len(derivata))
    return hmac.compare_digest(calcolata, derivata)


def firma_sessione(utente, versione, emessa=None):
    """Il valore del cookie di sessione: chi sei, quale versione, e da quando.

    Un cookie lo scrive il browser, quindi senza firma `utente=7` diventa `utente=8`
    con un editor di testo. La firma HMAC lo trasforma da dichiarazione in credenziale.

    `versione` e' `users.session_version`: incrementarla nel database invalida tutti i
    cookie emessi prima, che e' il modo di buttare fuori una sessione senza aspettare
    la scadenza — serve per «entra come cliente» e per un accesso sospetto.

    `emessa` e' il momento dell'emissione, e il cookie va riemesso a ogni richiesta
    valida: e' cosi' che venti minuti diventano «di inattivita'» e non «di sessione».

    Senza segreto restituisce stringa vuota: non si firma cio' che non si puo'
    verificare.
    """
    if not SEGRETO_SESSIONE:
        return ''
    if emessa is None:
        emessa = time.time()
    corpo = f'{int(utente)}.{int(versione)}.{int(emessa)}'
    firma = hmac.new(SEGRETO_SESSIONE.encode('utf-8'), corpo.encode('utf-8'),
                     hashlib.sha256).hexdigest()
    return f'{corpo}.{firma}'


def leggi_sessione(cookie):
    """`{'utente': …, 'versione': …}` se il cookie e' valido e non scaduto, altrimenti `None`.

    Non solleva su nessun ingresso: il valore arriva dal browser, quindi lo scrive il
    mittente, e un'eccezione non gestita su una rotta autenticata sarebbe un 500
    pilotabile dall'esterno.

    Non consulta il database — e' pura — perche' chi la chiama deve poi confrontare
    `versione` con `users.session_version`: la firma dice che il cookie e' nostro, non
    che la sessione e' ancora buona.
    """
    if not SEGRETO_SESSIONE or not cookie:
        return None
    pezzi = str(cookie).split('.')
    if len(pezzi) != 4:
        return None
    utente, versione, emessa, firma = pezzi
    corpo = f'{utente}.{versione}.{emessa}'
    atteso = hmac.new(SEGRETO_SESSIONE.encode('utf-8'), corpo.encode('utf-8'),
                      hashlib.sha256).hexdigest()
    # Sui byte: `firma` viene dal cookie, cioe' la scrive il browser. Sulle stringhe,
    # `compare_digest` solleva `TypeError` su un carattere non ASCII, e questa funzione
    # sta dietro OGNI rotta autenticata — quindi non era un 500 su una rotta, era un 500
    # su tutte, scatenabile cambiando un cookie. E' il piu' grave dei tre siti.
    if not hmac.compare_digest(atteso.encode('utf-8'), firma.encode('utf-8')):
        return None
    try:
        utente, versione, emessa = int(utente), int(versione), int(emessa)
    except ValueError:
        return None
    if time.time() - emessa > INATTIVITA_MASSIMA:
        return None
    return {'utente': utente, 'versione': versione}


# Esito dell'ultimo tentativo di registrazione: None = non tentato (nessun bot),
# True = Telegram conosce il segreto, False = tentativo fallito.
#
# Serve perche' l'handler INGOIA le eccezioni, e senza questo stato un fallimento
# sarebbe invisibile. Sotto lock insieme al momento dell'ultimo tentativo, perche'
# da quando la ri-registrazione avviene anche da una richiesta (vedi
# `assicura_registrazione`) due consegne concorrenti scriverebbero entrambe:
# segnalato da Sourcery. Stessa forma del lock sugli scarti di consegna.
_WEBHOOK_REGISTRATO = None

# Sentinella «nessun tentativo e' mai avvenuto». Non `0.0`, e la differenza non e'
# stilistica: `time.monotonic()` conta dall'avvio dell'HOST, non dall'epoca, quindi
# su un container appena partito vale pochi secondi. Con `0.0` la sottrazione
# `adesso - _ULTIMO_TENTATIVO` restava sotto i 60 secondi del freno, e il freno si
# comportava come se un tentativo fosse appena avvenuto quando non ne era avvenuto
# nessuno: la prima autoriparazione da consegna rifiutata era soppressa per il primo
# minuto di vita del processo. Cioe' proprio nella finestra in cui una registrazione
# stantia e' piu' probabile — subito dopo un deploy — il rimedio era muto.
# Segnalato da Claude Fable 5 sulla review finale della PR #14.
MAI_TENTATO = None
_ULTIMO_TENTATIVO = MAI_TENTATO
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


def public_url_configurata():
    """L'URL pubblico del servizio, con il default di produzione.

    `os.getenv(chiave, default)` NON usa il default quando la variabile esiste
    vuota: una `PUBLIC_URL` presente ma vuota (o di soli spazi) usciva cosi'
    com'era — una `setWebhook` verso `/telegram/webhook` senza host, e una
    `base_url` inservibile da `/api/settings`. Trovato da CodeRabbit sulla
    PR #50 sul secondo sito; il primo e' la stessa classe (regola 2), e la
    fonte unica e' la regola 3.
    """
    return (os.getenv('PUBLIC_URL', '').strip()
            or 'https://csv-production-b04e.up.railway.app')


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
        if (not forza and _ULTIMO_TENTATIVO is not MAI_TENTATO
                and adesso - _ULTIMO_TENTATIVO < ATTESA_FRA_TENTATIVI_S):
            return _WEBHOOK_REGISTRATO
        _ULTIMO_TENTATIVO = adesso
        _TENTATIVI_EMESSI += 1
        mio = _TENTATIVI_EMESSI
    esito = _chiama_set_webhook(token, public_url_configurata())
    with _WEBHOOK_LOCK:
        # Solo se nessun tentativo piu' recente ha gia' scritto il suo esito: vedi
        # `_TENTATIVI_EMESSI`. Senza questo confronto un tentativo lento e fallito
        # sovrascrive un successo piu' recente.
        if mio >= _TENTATIVO_DELL_ESITO:
            _TENTATIVO_DELL_ESITO = mio
            _WEBHOOK_REGISTRATO = esito
        return _WEBHOOK_REGISTRATO


_COMPITO_REGISTRAZIONE = None


# Redazione del token nell'access-log di uvicorn (audit #81, A1).
# Il feed di XTrader e' `GET /feed/{slug}.csv?token=<segreto>` (e l'alias
# `/xtrader.csv?token=<CSV_ACCESS_TOKEN>`): il token viaggia nel query string, e
# l'access-log formatta `full_path` — `args[2]` del record — che lo contiene.
# Senza redazione, ogni poll di XTrader scriverebbe il token in chiaro nei log del
# container, che e' esattamente cio' che «Non stampare token di feed nei log» e la
# priorita' #9 vietano. Si REDIGE il valore, non si sopprime la riga: l'access-log
# resta utile. Il parametro si chiama `token` sia sul feed per-utente sia sull'alias
# legacy, quindi una sola regola copre entrambi.
_RE_TOKEN_LOG = re.compile(r'(token=)[^&\s"\']+', re.IGNORECASE)


def _redigi_token_log(valore):
    """Sostituisce il valore di `token=` con `[REDACTED]` in una stringa.

    Gli argomenti non-stringa del record (client_addr e' str, ma lo status e' int)
    passano intatti: cosi' la funzione si applica a ogni elemento di `record.args`
    senza doverne conoscere la posizione.
    """
    if not isinstance(valore, str):
        return valore
    return _RE_TOKEN_LOG.sub(r'\1[REDACTED]', valore)


class RedazioneTokenAccessLog(logging.Filter):
    """Toglie il valore di `token=` dalle righe dell'access-log di uvicorn.

    uvicorn costruisce `record.args = (client_addr, method, full_path,
    http_version, status_code)` e formatta `full_path`, che per il feed contiene
    `?token=<segreto>`. Il filtro redige ogni argomento stringa (e il messaggio,
    per sicurezza se un giorno il record cambiasse forma). Restituisce sempre
    `True`: e' una redazione, non un filtro che scarta righe.
    """

    def filter(self, record):
        if isinstance(record.args, tuple):
            record.args = tuple(_redigi_token_log(a) for a in record.args)
        record.msg = _redigi_token_log(record.msg)
        return True


def installa_redazione_access_log():
    """Attacca la redazione al logger `uvicorn.access`, una volta sola.

    Idempotente: chiamata due volte non impila due filtri. Va invocata all'avvio,
    dopo che uvicorn ha configurato i suoi logger — vedi l'handler di `startup`.
    """
    logger = logging.getLogger('uvicorn.access')
    if not any(isinstance(f, RedazioneTokenAccessLog) for f in logger.filters):
        logger.addFilter(RedazioneTokenAccessLog())


# Installata QUI, a import del modulo, oltre che nello startup. La ragione la ha vista
# Claude Fable 5 sulla PR #82: legare la redazione al solo handler di `startup` la rende
# saltabile — se un giorno un handler registrato prima sollevasse, o l'app venisse montata
# senza eseguire il lifespan, uvicorn potrebbe loggare una richiesta col token prima che il
# filtro esista. All'import il filtro c'e' comunque. E' sicuro: verificato che sopravvive al
# `dictConfig` di uvicorn (il suo `LOGGING_CONFIG` non azzera i filtri di `uvicorn.access`),
# e la chiamata e' idempotente con quella nello startup — vedi test_redazione_log.py.
installa_redazione_access_log()


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
    # Prima di servire qualunque richiesta: togli il token del feed dall'access-log
    # di uvicorn (audit #81, A1). Qui, non a import del modulo, perche' uvicorn
    # configura i suoi logger appena prima di far partire l'app — un filtro aggiunto
    # troppo presto verrebbe scavalcato dalla sua configurazione.
    installa_redazione_access_log()

    if admin_id_malformato():
        # Non solleva: un avvio che muore per una variabile scritta male sarebbe peggio del
        # difetto che segnala. Ma lo dice, perche' l'alternativa e' un proprietario che
        # ottiene un account vuoto e non ha nessun posto dove leggere il perche'.
        # `logging.getLogger(...)` e non un `log` di modulo, perche' un `log` di modulo in
        # questo file NON ESISTE: la prima versione di questa riga lo usava, e sarebbe stata
        # un `NameError` dentro l'handler di avvio — cioe' un servizio che NON PARTE per una
        # variabile scritta male. Avrei trasformato un guasto silenzioso in un guasto totale,
        # dentro la correzione che serviva a renderlo visibile. Misurato prima del commit.
        logging.getLogger('xtrader.relay').error(
            "TELEGRAM_ADMIN_ID non e' una sequenza di sole cifre: il login del proprietario "
            "NON verra' collegato al suo account, e la riparazione idempotente non "
            "scattera'. Correggere la variabile: solo cifre, senza virgolette, spazi o segni.")

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
# La cartella pubblica, definita qui e non in fondo al file perche' adesso la
# leggono in due: il mount di `/app` (ultima riga del modulo, dove deve restare
# per non intercettare le rotte del relay) e la facciata su `/`. Ricomporre
# `Path(__file__).parent / 'web'` una seconda volta sarebbe la duplicazione che
# la regola 3 vieta, e `tests/safety/test_static_mount.py` conta le occorrenze.
WEB_DIR = Path(__file__).parent / 'web'
SITO = WEB_DIR / 'sito.html'
TOKEN = os.getenv('CSV_ACCESS_TOKEN', '')
# Il segreto del webhook, calcolato una volta all'import come `TOKEN`: `health()` e
# l'handler del webhook lo leggevano entrambi da `os.environ` a ogni chiamata, e
# due letture separate possono divergere. Segnalato da Sourcery.
SEGRETO_WEBHOOK = webhook_secret(os.getenv('TELEGRAM_BOT_TOKEN', ''))
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')

# Il segreto che firma i cookie di sessione, DERIVATO dal token del bot invece di
# essere una variabile a se'. Stessa ragione di `webhook_secret`: una variabile nuova
# lascerebbe una finestra fra il deploy e la sua configurazione, e in quella finestra
# bisognerebbe scegliere fra un login rotto e un login che accetta cookie non firmati
# — due modi di sbagliare. Derivandolo, il valore esiste sempre dove esiste il bot.
#
# Il prefisso lo separa dal segreto del webhook: due usi dello stesso token non devono
# produrre lo stesso segreto, altrimenti un valore rubato da un canale servirebbe
# nell'altro. Non contiene il token e non lo rivela: e' un digest.
#
# Senza bot il segreto e' vuoto, e in quel caso NESSUNA sessione e' valida — vedi
# `leggi_sessione`. Fail-closed, come `auth()` dopo la correzione di luglio.
SEGRETO_SESSIONE = (hashlib.sha256(('betrelay-sessione-v1:' + BOT_TOKEN).encode('utf-8')).hexdigest()
                    if BOT_TOKEN else '')

# L'ID Telegram del proprietario. Non e' un segreto — chiunque riceva un suo messaggio
# lo conosce — ma decide CHI e' l'amministratore, e serve a collegare il suo login
# all'utente che possiede i suoi parser: quella riga ha `origin_profile = 'PIERO'` e
# nessun `telegram_id`, perche' nessuno lo aveva mai saputo. Senza questa variabile il
# login creerebbe un secondo account vuoto e la dashboard del proprietario sarebbe
# vuota, senza nessun errore da nessuna parte. Scelta A del proprietario, 12/08/2026;
# scartata «il primo login vince» perche' il sito e' pubblico e il primo estraneo
# erediterebbe parser e feed.
TELEGRAM_ADMIN_ID = os.getenv('TELEGRAM_ADMIN_ID', '').strip()

# Il CONSENSO del proprietario ad assorbire la riga che possiede quell'ID. Assente il
# servizio rifiuta con `409` invece di travasare: vedi `riconciliazione_autorizzata()` per
# il motivo, che e' l'unico caso in cui un dato non puo' distinguere due situazioni.
TELEGRAM_ADMIN_RECONCILE = os.getenv('TELEGRAM_ADMIN_RECONCILE', '').strip()

# Il token che autorizza il job NOTTURNO di backup a chiamare `/api/admin/backup/invia` senza
# una sessione: un cron su Railway lo porta in un header e il servizio manda il backup al canale
# configurato. E' un secret del deploy, come `CSV_ACCESS_TOKEN`; non entra mai nei log ne' nelle
# risposte. Assente, la rotta accetta SOLO la sessione dell'amministratore (il bottone «Invia
# backup ora»): il percorso automatico resta spento fail-closed finche' il proprietario non lo
# configura, esattamente come i promemoria che «aspettano» di essere chiamati. Scelta del
# proprietario (#56 pezzo 3): cron Railway + token dedicato.
BACKUP_CRON_TOKEN = os.getenv('BACKUP_CRON_TOKEN', '').strip()


# Un `TELEGRAM_ADMIN_ID` malformato non solleva e non collega: il confronto con l'`id` che
# Telegram manda non combacia mai, quindi il proprietario ottiene un account vuoto e la
# riparazione idempotente non scatta nemmeno — il ramo che ripara e' dietro quel confronto.
# Le forme sbagliate sono tutte silenziose: virgolette o apici incollati col valore, un `+`
# davanti, uno spazio in mezzo. Lo `strip()` perdona solo i bordi.
#
# Quindi almeno lo si dice all'avvio, con una regex e NON con `.isdigit()`: quel
# metodo accetta anche le cifre di altri alfabeti — `'\u0669\u0668\u0667'.isdigit()` e' `True` — che
# non combaciano con nessun id Telegram, cioe' proprio il caso che questo controllo esiste
# per nominare. Segnalato da GPT-5.5 sulla PR #24.
#
# Questo commento nominava `[0-9]+`, cioe' la regex di UN GIRO PRIMA: la forma effettiva
# e' `[1-9][0-9]*` e il docstring qui sotto spiega perche'. Un commento che nomina un
# codice diverso da quello che gli sta accanto e' la forma piu' piccola del difetto che
# `CLAUDE.md` racconta due volte — l'affermazione e la sua smentita nello stesso file — e
# resta un difetto anche quando e' piccola. Segnalato da Claude Fable 5 sulla PR #24.
def admin_id_malformato():
    """Vero se `TELEGRAM_ADMIN_ID` e' impostato ma non puo' combaciare con un id Telegram.

    `[1-9][0-9]*` e non `[0-9]+`: uno zero iniziale passa come cifra ma Telegram manda
    `1234`, non `001234`, quindi il confronto fra stringhe non combacia **mai** — un valore
    accettato dal controllo e inutile in produzione, cioe' il caso peggiore per un controllo.
    Esclude anche `0`, che non e' l'id di nessuno. Segnalato da GPT-5.5 sulla PR #24.
    """
    return bool(TELEGRAM_ADMIN_ID) and re.fullmatch(r'[1-9][0-9]*', TELEGRAM_ADMIN_ID) is None



# L'hash della password dell'accesso di emergenza, nella forma
# `scrypt$<sale base64>$<derivata base64>`. Nella variabile va l'HASH e non la
# password: la dashboard di Railway e' leggibile da chi ha accesso al progetto, e con
# la password in chiaro chi la legge entra nel pannello — da cui si cancellano parser,
# si cambiano le chat autorizzate e si inietta un segnale nel feed che XTrader legge.
# Assente → il percorso a password e' DISABILITATO, non aperto.
ADMIN_PASSWORD_HASH = os.getenv('ADMIN_PASSWORD_HASH', '').strip()

# L'utente dell'accesso di emergenza. Fisso e pubblico: non aggiunge sicurezza, e non
# ne toglie, ed e' quello che il proprietario ha chiesto.
ADMIN_USERNAME = 'administrator'

# Quanto puo' essere vecchia la firma di un Login Widget. Senza questo limite una
# firma valida resterebbe valida per sempre, e l'URL di ritorno di un login riuscito
# sarebbe una credenziale a tempo indeterminato — copiabile da una cronologia, da un
# log del browser, da uno screenshot. Il limite vale in ENTRAMBE le direzioni: anche
# una firma dal futuro va rifiutata, o un `auth_date` messo a mano nel 2030 sarebbe
# accettato per sempre.
ETA_MASSIMA_LOGIN = 300

# I venti minuti di inattivita' della Issue #7, in secondi. Riguardano la SESSIONE DEL
# SITO e non hanno niente a che fare con il token del feed: XTrader non ha una
# sessione, non fa login e non «resta attivo». Collegare le due cose farebbe perdere i
# segnali a ogni cliente venti minuti dopo aver chiuso il browser, e nessun test di
# login lo troverebbe perche' il login funzionerebbe benissimo.
INATTIVITA_MASSIMA = 20 * 60

HEADERS = ['Provider','EventId','EventName','MarketId','MarketName','MarketType','SelectionId','SelectionName','Handicap','Price','MinPrice','MaxPrice','BetType','Points']
DEFAULT_PARSER = 'Parser_Telegram_XTrader_v1'
PIERO_PROFILE = 'PIERO'

# I tetti per-tenant del CRUD parser (#31 B2). La regola che servono: «tutto
# personale del cliente, non deve bloccare gli altri» — senza, una sessione
# approvata puo' gonfiare lo SQLite e il volume Railway CONDIVISO. Il numero
# massimo si regola da variabile (Railway) senza deploy; i tetti di dimensione
# sono generosi rispetto all'uso reale (una config tipica sta sotto i 2k) e
# stretti rispetto all'abuso.
def _intero_da_env(nome, default):
    """Un intero da una variabile d'ambiente, che NON butta giu' l'avvio.

    `int(os.getenv(...))` nudo crasha all'import con una variabile vuota o non
    numerica — un refuso nel pannello Railway diventerebbe un servizio che non
    parte. Stessa classe del fail-closed di `auth()`: un errore di configurazione
    va assorbito con un default dichiarato nel log, non trasformato in un boot
    rotto. Segnalato da GPT-5.5 sulla PR #45.
    """
    grezzo = os.getenv(nome)
    if grezzo is None or not grezzo.strip():
        return default
    try:
        valore = int(grezzo)
    except ValueError:
        logging.getLogger('xtrader.relay').warning(
            '%s=%r non e\' un intero: uso il default %d', nome, grezzo, default)
        return default
    if valore < 1:
        logging.getLogger('xtrader.relay').warning(
            '%s=%d non e\' positivo: uso il default %d', nome, valore, default)
        return default
    return valore


MAX_PARSER_PER_UTENTE = _intero_da_env('MAX_PARSER_PER_UTENTE', 20)
MAX_TITOLO_PARSER = 80
MAX_CONFIG_PARSER = 20_000
# La libreria mercati Betfair (#33) e' per-utente e a inserimento libero: i tetti
# esistono perche' un utente non deve poter gonfiare lo storage condiviso
# (hardening per-tenant, #31). Regolabili da variabile su Railway senza deploy.
# Le righe di override del multi-riga (#35 pezzo 2): il tetto e' sul TOTALE
# dichiarato in config (mercati + selezioni, anche spente) — ogni riga attiva
# diventa un documento nel feed a ogni segnale.
MAX_RIGHE_MULTI = _intero_da_env('MAX_RIGHE_MULTI', 20)
MAX_SPORT_PER_UTENTE = _intero_da_env('MAX_SPORT_PER_UTENTE', 20)
MAX_MERCATI_PER_SPORT = _intero_da_env('MAX_MERCATI_PER_SPORT', 200)
MAX_SELEZIONI_PER_MERCATO = _intero_da_env('MAX_SELEZIONI_PER_MERCATO', 200)
MAX_CAMPO_MERCATO = 120
# Sorgenti squadre (#34): stessi motivi, stessi ordini di grandezza. Le squadre
# hanno un tetto PER COMPETIZIONE (un campionato reale ne ha ~20; 100 lascia
# margine a chi archivia stagioni) e gli alias non hanno un tetto proprio:
# esistono solo per squadre esistenti di sorgenti esistenti, quindi sono gia'
# limitati da squadre x sorgenti.
MAX_SORGENTI_PER_UTENTE = _intero_da_env('MAX_SORGENTI_PER_UTENTE', 20)
MAX_COMPETIZIONI_PER_UTENTE = _intero_da_env('MAX_COMPETIZIONI_PER_UTENTE', 50)
MAX_SQUADRE_PER_COMPETIZIONE = _intero_da_env('MAX_SQUADRE_PER_COMPETIZIONE', 100)
# In BYTE, sul corpo HTTP grezzo: i tetti sui campi qui sopra si misurano solo DOPO la
# deserializzazione, e a quel punto il corpo e' gia' tutto in RAM. 64 KiB lasciano oltre
# 3x di margine a un corpo legittimo (config 20k + titolo 80, serializzati ASCII).
MAX_CORPO_JSON = 65_536

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

class ParserMioIn(BaseModel):
    # L'input di un parser creato/modificato dall'utente della sessione. NON contiene
    # `user_id`: il proprietario viene SEMPRE dalla sessione, mai dal corpo della
    # richiesta (isolamento fra utenti). `config` e' la config del motore
    # (`esegui_parser`); `titolo` e' l'etichetta che il cliente vede.
    titolo: str
    config: dict = Field(default_factory=dict)
    active: bool = True
    # La precondizione della PUT (#51): la versione che il client ha LETTO.
    # `None` = scrittura incondizionata (compat coi chiamanti storici); con un
    # valore, il salvataggio riesce solo se il parser e' ancora a quella
    # versione — altrimenti 409, e il lost update diventa visibile.
    versione: int | None = None
    # L'altra precondizione (#75): l'IDENTITA' della riga che il client ha letto.
    # `versione` chiede «e' cambiato mentre lo modificavo?», `uid` chiede «e'
    # ancora lo STESSO parser?» — e sono domande diverse, perche' un
    # elimina+ricrea dello stesso slug produce una riga nuova che riparte da
    # `versione = 1`, cioe' proprio il valore che la scheda vecchia porta con
    # se'. Misurato sulla PR #74: senza `uid` la scheda vecchia sovrascriveva
    # `config_json` del parser ricreato, e con esso le regole che generano il
    # CSV. `None` = incondizionata, come per `versione`.
    uid: str | None = None


# Le tre tabelle con cui il servizio e' nato. Restano con QUESTI nomi e questa
# forma: gli endpoint le leggono, e questa migrazione non cambia il comportamento
# di nessuna rotta. Lo scambio delle letture verso lo schema multiutente e' un
# lavoro successivo (PR 8/9/12 della roadmap in #2).
SCHEMA_ORIGINALE = (
    'CREATE TABLE IF NOT EXISTS signals (id INTEGER PRIMARY KEY AUTOINCREMENT,'
    ' csv TEXT NOT NULL, parser TEXT, profile TEXT,'
    ' created_at DATETIME DEFAULT CURRENT_TIMESTAMP, expires_at INTEGER)',
    'CREATE TABLE IF NOT EXISTS parsers (name TEXT PRIMARY KEY, header TEXT NOT NULL,'
    ' market_name TEXT, market_type TEXT, selection_name TEXT, handicap TEXT, bet_type TEXT)',
    'CREATE TABLE IF NOT EXISTS profiles (name TEXT PRIMARY KEY, chat_ids TEXT NOT NULL,'
    ' parser TEXT NOT NULL)',
)

# Lo schema multiutente deciso in #2. NOVE tabelle nuove: `users` e `chats` e le
# altre non esistevano, quindi si creano.
#
# `parsers` e `signals` NON sono qui, e la ragione e' un vincolo reale: esistono
# gia' con una forma diversa, e SQLite non ammette due tabelle con lo stesso nome.
# Creare `parsers_v2` accanto a `parsers` avrebbe lasciato due fonti per la stessa
# cosa — esattamente cio' che la regola 3 vieta — e rinominare le vecchie avrebbe
# rotto ogni endpoint. Si estendono invece con ALTER additivo, vedi
# `COLONNE_MULTIUTENTE`.
#
# `token_hash` e `token_prefix` stanno su `users` e non sui parser: il feed e il
# timer appartengono all'utente, il parser possiede solo configurazione e log. E'
# la correzione del modello sbagliato del prototipo, registrata in #2.
SCHEMA_MULTIUTENTE = (
    # `origin_profile` e' il profilo da cui la migrazione ha creato questo utente, e
    # serve come CHIAVE STABILE per ritrovarlo ai riavvii successivi. Prima il
    # travaso cercava per `first_name`, che non e' univoco: al primo login Telegram di
    # un omonimo, chat, segnali e parser sarebbero passati a lui. `first_name` non va
    # nemmeno bene come chiave in se', perche' il login lo SOVRASCRIVE col nome vero.
    # NULL per chi non viene da un profilo, ed e' il caso normale dei prossimi utenti.
    # Segnalato da Claude Fable 5 sulla PR #22.
    'CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT,'
    ' origin_profile TEXT UNIQUE,'
    ' telegram_id TEXT UNIQUE, username TEXT, first_name TEXT, slug TEXT UNIQUE,'
    ' token_hash TEXT, token_prefix TEXT,'
    " status TEXT NOT NULL DEFAULT 'registrato', access_expires_at INTEGER,"
    ' telegram_reachable INTEGER NOT NULL DEFAULT 0,'
    # Per quale scadenza e' gia' stato mandato il promemoria. Non un booleano: con un
    # booleano il secondo rinnovo non avvertirebbe piu', perche' resterebbe «gia' avvisato».
    ' promemoria_per INTEGER,'
    ' session_version INTEGER NOT NULL DEFAULT 1,'
    ' is_admin INTEGER NOT NULL DEFAULT 0,'
    ' created_at DATETIME DEFAULT CURRENT_TIMESTAMP)',
    'CREATE TABLE IF NOT EXISTS chats (id INTEGER PRIMARY KEY AUTOINCREMENT,'
    ' telegram_chat_id TEXT NOT NULL, message_thread_id TEXT, title TEXT, type TEXT,'
    ' owner_user_id INTEGER, verified_at INTEGER,'
    ' UNIQUE (telegram_chat_id, message_thread_id))',
    'CREATE TABLE IF NOT EXISTS parser_chats (parser_id INTEGER NOT NULL,'
    ' chat_id INTEGER NOT NULL, PRIMARY KEY (parser_id, chat_id))',
    'CREATE TABLE IF NOT EXISTS message_logs (id INTEGER PRIMARY KEY AUTOINCREMENT,'
    ' user_id INTEGER, parser_id INTEGER, chat_id INTEGER, text TEXT, esito TEXT,'
    ' created_at DATETIME DEFAULT CURRENT_TIMESTAMP)',
    'CREATE TABLE IF NOT EXISTS chat_verifications (code TEXT PRIMARY KEY,'
    ' user_id INTEGER, expires_at INTEGER, consumed_at INTEGER)',
    'CREATE TABLE IF NOT EXISTS access_requests (id INTEGER PRIMARY KEY AUTOINCREMENT,'
    ' user_id INTEGER, created_at DATETIME DEFAULT CURRENT_TIMESTAMP,'
    ' decided_at INTEGER, decided_by INTEGER, granted_days INTEGER, outcome TEXT)',
    'CREATE TABLE IF NOT EXISTS admin_audit (id INTEGER PRIMARY KEY AUTOINCREMENT,'
    ' admin_user_id INTEGER, target_user_id INTEGER, action TEXT,'
    ' created_at DATETIME DEFAULT CURRENT_TIMESTAMP)',
    'CREATE TABLE IF NOT EXISTS feed_reads (token_id INTEGER, giorno TEXT,'
    ' ip_hash TEXT, PRIMARY KEY (token_id, giorno, ip_hash))',
    # `update_id` UNIQUE e' il dedup dei webhook duplicati: Telegram riconsegna, e
    # senza questa tabella una riconsegna riscrive il segnale e fa ripartire il TTL.
    'CREATE TABLE IF NOT EXISTS webhook_seen (update_id TEXT PRIMARY KEY,'
    ' created_at DATETIME DEFAULT CURRENT_TIMESTAMP)',
    # La libreria mercati Betfair (#33): sport → mercato → selezioni, TUTTO
    # per-utente. Nessun catalogo incorporato — correzione del proprietario del
    # 13/08/2026: questi dati li crea ogni utente, e un database vergine resta
    # vergine. `user_id` vive solo su `sports`: mercati e selezioni ereditano la
    # proprieta' per join, cosi' non esistono due copie del proprietario che
    # possano divergere. Le cascate di eliminazione sono ESPLICITE nelle rotte
    # (sqlite non ha le FK attive qui), dentro una sola transazione.
    'CREATE TABLE IF NOT EXISTS sports (id INTEGER PRIMARY KEY AUTOINCREMENT,'
    ' user_id INTEGER NOT NULL, slug TEXT NOT NULL, nome TEXT NOT NULL,'
    ' created_at DATETIME DEFAULT CURRENT_TIMESTAMP,'
    ' UNIQUE (user_id, slug))',
    # UNIQUE su (sport, type, name): blocca il doppione ESATTO, non la variante —
    # due nomi diversi sullo stesso MarketType sono una scelta legittima
    # dell'utente, un doppione identico e' sempre un errore di battitura.
    'CREATE TABLE IF NOT EXISTS betfair_markets (id INTEGER PRIMARY KEY AUTOINCREMENT,'
    ' sport_id INTEGER NOT NULL, market_type TEXT NOT NULL, market_name TEXT NOT NULL,'
    ' created_at DATETIME DEFAULT CURRENT_TIMESTAMP,'
    ' UNIQUE (sport_id, market_type, market_name))',
    'CREATE TABLE IF NOT EXISTS betfair_selections (id INTEGER PRIMARY KEY AUTOINCREMENT,'
    ' market_id INTEGER NOT NULL, selection_name TEXT NOT NULL,'
    ' UNIQUE (market_id, selection_name))',
    # Sorgenti squadre (#34, pezzo 1). La COMPETIZIONE (sport → «Serie A»)
    # possiede la lista canonica dei nomi Betfair: salvata una volta, e' l'unica
    # colonna che finira' nel CSV. La SORGENTE e' una colonna di alias sopra
    # quella stessa lista: UNIQUE (sorgente, squadra) = un solo alias per
    # squadra per sorgente (deciso dal proprietario). `alias_squadre` non ha
    # user_id: la proprieta' si risolve per join dai due lati, sorgente e
    # squadra→competizione, e le rotte verificano ENTRAMBI. Cascate esplicite
    # nelle rotte, come per la libreria mercati qui sopra.
    'CREATE TABLE IF NOT EXISTS competizioni (id INTEGER PRIMARY KEY AUTOINCREMENT,'
    ' user_id INTEGER NOT NULL, sport_id INTEGER NOT NULL, nome TEXT NOT NULL,'
    ' created_at DATETIME DEFAULT CURRENT_TIMESTAMP,'
    ' UNIQUE (user_id, sport_id, nome))',
    'CREATE TABLE IF NOT EXISTS squadre_betfair (id INTEGER PRIMARY KEY AUTOINCREMENT,'
    ' competizione_id INTEGER NOT NULL, nome TEXT NOT NULL,'
    ' created_at DATETIME DEFAULT CURRENT_TIMESTAMP,'
    ' UNIQUE (competizione_id, nome))',
    'CREATE TABLE IF NOT EXISTS sorgenti_squadre (id INTEGER PRIMARY KEY AUTOINCREMENT,'
    ' user_id INTEGER NOT NULL, nome TEXT NOT NULL,'
    ' created_at DATETIME DEFAULT CURRENT_TIMESTAMP,'
    ' UNIQUE (user_id, nome))',
    'CREATE TABLE IF NOT EXISTS alias_squadre (id INTEGER PRIMARY KEY AUTOINCREMENT,'
    ' sorgente_id INTEGER NOT NULL, squadra_id INTEGER NOT NULL, alias TEXT NOT NULL,'
    ' UNIQUE (sorgente_id, squadra_id))',
    # Gli indici che le cascate usano davvero (CodeRabbit, PR #64): gli UNIQUE
    # qui sopra hanno user_id/sorgente_id come colonna piu' a sinistra, quindi
    # `DELETE ... WHERE sport_id=?` e `DELETE ... WHERE squadra_id IN (...)`
    # non li possono usare. Additivi e idempotenti come le tabelle.
    'CREATE INDEX IF NOT EXISTS competizioni_per_sport ON competizioni (sport_id)',
    'CREATE INDEX IF NOT EXISTS alias_per_squadra ON alias_squadre (squadra_id)',
    # Le migrazioni che vanno fatte UNA VOLTA SOLA per database, non a ogni avvio.
    # Nasce con la rimozione del seme (#25 lavoro E): il travaso dei link
    # `parser_chats` dai profili legacy e' una conversione di dati, e una
    # conversione ripetuta a ogni riavvio e' indistinguibile da un seme — rimette
    # in piedi cio' che il proprietario ha cancellato. Il marcatore si scrive
    # nella STESSA transazione del lavoro: se la migrazione muore a meta', non
    # viene committato niente e il prossimo avvio riprova.
    'CREATE TABLE IF NOT EXISTS migrazioni (nome TEXT PRIMARY KEY,'
    ' created_at DATETIME DEFAULT CURRENT_TIMESTAMP)',
    # Impostazioni GLOBALI del servizio, chiave→valore. Nasce col canale di backup
    # (#56 pezzo 2): la destinazione dei backup e' una sola, del proprietario, e non
    # appartiene a nessun utente ne' a `chats` — che sono le SORGENTI dei segnali, e
    # mettercela dentro la iscriverebbe all'instradamento del webhook (regola del
    # filtro chat). Tabella separata, additiva e idempotente come le altre.
    'CREATE TABLE IF NOT EXISTS impostazioni (chiave TEXT PRIMARY KEY, valore TEXT)',
    # Idempotenza persistente dell'invio notturno (#56, pezzo idempotenza): un periodo
    # (la data UTC del giro) e' PRIMARY KEY, prenotato PRIMA dell'invio dal solo percorso
    # cron. Due repliche o un retry non mandano due copie: la seconda INSERT va in conflitto.
    # Su invio fallito la riga si cancella, cosi' un retry della stessa notte riprova.
    'CREATE TABLE IF NOT EXISTS backup_inviato (periodo TEXT PRIMARY KEY,'
    ' created_at DATETIME DEFAULT CURRENT_TIMESTAMP)',
)

# «Nessun topic» si scrive NULL, e in SQL `NULL != NULL`: ogni confronto fra chat
# deve quindi passare da questa espressione, o due righe identiche si sfuggono a
# vicenda. Sta qui, in UNA forma, perche' serve in tre punti — l'indice UNIQUE, il
# controllo di esistenza del travaso, la deduplica — e ricopiarla sarebbe tre
# occasioni di divergere su una sottigliezza che non solleva quando sbagli.
# Ogni colonna che riferisce `users.id`, in UNA lista. Serve alla riconciliazione di due
# utenti duplicati, che deve spostare TUTTO cio' che punta al perdente: dimenticarne una
# lascia dati agganciati a un utente che non e' piu' quel profilo, cioe' dati che nessuno
# rivendica. `[REAL_FINDING]` di GPT-5.6 Sol, che ne aveva viste quattro mancanti.
#
# `parsers.user_id` non e' qui perche' passa da `_trasferisci_parser`, che deve anche
# ri-disambiguare lo slug. La convenzione di nome e' vincolata da un test: una colonna
# nuova che si chiama come queste e non entra nell'elenco fa diventare rosso quel test,
# che e' l'unico modo perche' la lista non resti indietro.
RIFERIMENTI_UTENTE = (
    ('chats', 'owner_user_id'),
    ('signals', 'user_id'),
    ('message_logs', 'user_id'),
    ('chat_verifications', 'user_id'),
    ('access_requests', 'user_id'),
    ('access_requests', 'decided_by'),
    ('admin_audit', 'admin_user_id'),
    ('admin_audit', 'target_user_id'),
)

# Le colonne di `admin_audit` NON si travasano quando si RIPARA un account: le altre sono
# **dati** dell'utente e vanno dove vanno i dati, quelle due sono **storia**. Riscritte, un
# `collegamento_admin_rifiutato` registrato contro la riga X diventa un rifiuto del
# proprietario contro se stesso — auto-referenziale, cioe' inutile proprio nel momento in cui
# serve, perche' `admin_audit` e' l'unico posto dove il proprietario legge PERCHE' un login e'
# stato rifiutato. La MIGRAZIONE invece deve riscriverle: la' la riga perdente perde
# `origin_profile` e i suoi riferimenti resterebbero orfani. Segnalato da CodeRabbit sulla
# PR #24. Derivato e non riscritto a mano: una colonna nuova entra da `RIFERIMENTI_UTENTE`,
# che e' vincolato dal test dello schema, e arriva qui da se'.
RIFERIMENTI_DATI_UTENTE = tuple((tabella, colonna)
                                for tabella, colonna in RIFERIMENTI_UTENTE
                                if tabella != 'admin_audit')

# I nomi che per convenzione riferiscono un utente. Il test li usa per trovare le colonne
# che DOVREBBERO essere in `RIFERIMENTI_UTENTE`.
NOMI_DI_RIFERIMENTO_UTENTE = ('user_id', 'owner_user_id', 'admin_user_id',
                              'target_user_id', 'decided_by')

TOPIC_CHAT = "IFNULL(message_thread_id, '')"
CHIAVE_CHAT = f'telegram_chat_id, {TOPIC_CHAT}'

# Colonne aggiunte alle due tabelle che esistono gia'. Additive e nullable: una
# colonna in piu' non cambia nessuna `SELECT` esistente, perche' tutte nominano le
# colonne che leggono invece di usare `SELECT *`. Verificato prima di scriverle.
COLONNE_MULTIUTENTE = (
    # `users` esiste gia' nei database creati dalla PR #22: le colonne che nascono dopo vanno
    # aggiunte con ALTER, o il servizio le cerca e non le trova.
    ('users', 'promemoria_per', 'INTEGER'),
    ('parsers', 'user_id', 'INTEGER'),
    ('parsers', 'slug', 'TEXT'),
    ('parsers', 'config_json', 'TEXT'),
    # Il titolo che il cliente vede e sceglie. `name` (PRIMARY KEY globale, eredita'
    # dello schema legacy) deve restare univoco fra TUTTI gli utenti, quindi non puo'
    # essere il titolo: due clienti possono chiamare «Test 1» il proprio parser. Il
    # titolo sta qui; l'identita' interna e' `(user_id, slug)`.
    ('parsers', 'titolo', 'TEXT'),
    ('parsers', 'active', 'INTEGER DEFAULT 1'),
    # `ordine` decide chi vince quando due parser dello stesso utente riconoscono
    # lo stesso messaggio. Serve un ORDER BY esplicito, e va mostrato in UI.
    ('parsers', 'ordine', 'INTEGER'),
    ('parsers', 'created_at', 'DATETIME'),
    # `parser_chats.parser_id` e' INTEGER e `parsers` aveva solo `name` TEXT: quella
    # tabella nasceva MORTA, nessuna colonna a cui riferirsi. #2 prevede `parsers id`
    # e mancava perche' un PRIMARY KEY non si aggiunge con ALTER — si aggiunge come
    # colonna con indice UNIQUE, riempita dal `rowid`. Segnalato da GPT-5.5.
    ('parsers', 'id', 'INTEGER'),
    # La precondizione della PUT (#51): incrementata a ogni modifica, e' cio'
    # che smaschera il lost update fra due sessioni dello stesso account. Le
    # righe esistenti partono da 1 col DEFAULT dell'ALTER.
    ('parsers', 'versione', 'INTEGER NOT NULL DEFAULT 1'),
    # L'identita' NON RIUSABILE della riga (issue #73). `id` viene dal `rowid`, e
    # `parsers` e' la tabella originale SENZA AUTOINCREMENT: sqlite riusa il rowid
    # massimo, quindi un parser eliminato e ricreato con lo stesso slug produce una
    # riga indistinguibile dalla vecchia — stesso id, stesso user_id, stesso slug,
    # stesso name. Una richiesta rimasta in volo colpiva quella nuova: misurato, la
    # DELETE la cancellava e la PUT le SOVRASCRIVEVA `config_json`, cioe' le regole
    # che generano il CSV, senza nessun sintomo. `uid` non si riusa mai, quindi la
    # richiesta stantia non trova niente e la rotta risponde 404.
    # AUTOINCREMENT avrebbe chiuso lo stesso caso ma non si aggiunge con un ALTER:
    # andrebbe ricreata la tabella che porta i parser di produzione. Deciso dal
    # proprietario il 18/08: colonna, come gia' fatto per `id`.
    ('parsers', 'uid', 'TEXT'),
    # Le DUE colonne legacy, e stanno qui perche' le avevo PERSE riscrivendo la
    # migrazione. Il codice precedente le aggiungeva con due `try/except` e il commento
    # «Migrate databases created before profile support»: su un database creato prima
    # dei profili il `CREATE TABLE IF NOT EXISTS` non fa niente — la tabella esiste — e
    # senza questi ALTER la `UPDATE signals SET profile=?` piu' sotto muore con
    # «no such column: profile», cioe' 500 su ogni richiesta per sempre.
    # `[REAL_FINDING]` di GPT-5.6 Sol: e' la regola 5 violata da me, e la regola 2-bis
    # — ho riscritto una funzione senza cercare tutto cio' che faceva.
    ('signals', 'profile', 'TEXT'),
    ('signals', 'expires_at', 'INTEGER'),
    # I segnali passano da per-PROFILO a per-UTENTE. La colonna vecchia `profile`
    # resta e continua a governare il feed: qui si aggiunge solo la destinazione.
    ('signals', 'user_id', 'INTEGER'),
    # Per i database creati da una versione intermedia di QUESTO ramo, dove `users`
    # esiste gia' senza `origin_profile`. Sulla tabella creata da zero l'ALTER trova
    # la colonna e l'errore «duplicate column name» viene ingoiato, come per le altre.
    ('users', 'origin_profile', 'TEXT'),
)

# I percorsi gia' migrati in QUESTO processo. Prima la migrazione girava a ogni
# `db()`, cioe' a ogni richiesta: tre CREATE TABLE, due ALTER, due INSERT OR
# IGNORE, una UPDATE e un COMMIT — una transazione di SCRITTURA anche sulle
# letture del feed, che XTrader interroga a raffica. Funzionava perche' e'
# idempotente, non perche' fosse progettato. Con undici tabelle quel costo si
# moltiplicherebbe sul percorso piu' caldo del servizio.
#
# Un insieme di percorsi e non un booleano: i test usano un database per test
# nello stesso processo, e un flag globale li lascerebbe senza schema.
_PERCORSI_MIGRATI: set = set()
_LOCK_MIGRAZIONE = threading.Lock()


def _chat_della_stringa(chat_ids):
    """`'-100,  -200 , '` → `{'-100', '-200'}`. La forma legacy in UNA funzione."""
    return {x.strip() for x in (chat_ids or '').split(',') if x.strip()}


def _riga_chat(c, chat):
    """L'`id` della riga di `chats` per questo `telegram_chat_id`, o `None`."""
    riga = c.execute(f'SELECT id FROM chats WHERE telegram_chat_id=?'
                     f' AND {TOPIC_CHAT}=?', (chat, '')).fetchone()
    return riga[0] if riga else None


def _stacca_link_del_profilo(c, nome, chat_ids, parser_nome):
    """Toglie i link fra il parser NOMINATO da un profilo e le chat elencate.

    Toglie **solo** quella coppia, mai «tutti i link di quelle chat»: sulla
    stessa chat vivono anche i link degli altri parser dello stesso utente
    (dispatch multi-parser, PR #44) e quelli degli altri profili. Una pulizia
    per chat li porterebbe via tutti, e il sintomo sarebbe un parser che smette
    di girare perche' qualcun altro ha salvato il proprio profilo.

    **Il filtro sul proprietario e' lo stesso di `_attacca_link_del_profilo`**,
    e l'asimmetria era un difetto: due profili possono NOMINARE lo stesso
    parser, l'attach salta quello del non-proprietario (isolamento), ma il
    detach cancellava per nome e portava via il link che il proprietario aveva
    legittimamente — il suo parser smetteva di girare su quella chat, in
    silenzio. Segnalato da Claude Fable 5 sulla PR #46. (Il meccanismo che la
    review nominava — parser omonimi di utenti diversi — non esiste:
    `parsers.name` e' PRIMARY KEY globale. La conclusione valeva lo stesso.)

    Si disfa quindi **esattamente** cio' che l'attach potrebbe aver fatto.
    """
    for chat in _chat_della_stringa(chat_ids):
        cid = _riga_chat(c, chat)
        if cid is None:
            continue
        c.execute('DELETE FROM parser_chats WHERE chat_id=? AND parser_id IN'
                  ' (SELECT p.id FROM parsers p JOIN users u ON u.id = p.user_id'
                  '  WHERE p.name=? AND u.origin_profile=?)',
                  (cid, parser_nome, nome))


def _attacca_link_del_profilo(c, nome, chat_ids, parser_nome):
    """Crea le chat mancanti e i link fra il parser del profilo e le sue chat.

    Il link nasce solo se il parser appartiene all'UTENTE di questo profilo.
    Due profili possono nominare lo stesso parser, ma il parser ha UN
    proprietario: un link creato dall'altro profilo manderebbe i segnali della
    sua chat al feed del proprietario del parser — il legame fra utenti diversi
    che il test cross-tenant della deduplica vieta. Il profilo il cui parser e'
    altrui resta sul percorso legacy (fallback), che scrive per profilo e
    quindi nel feed giusto.

    La riga di `chats` si crea qui se manca, con proprietario l'utente del
    profilo: prima la creava solo il travaso, quindi una chat AGGIUNTA via API
    non aveva riga e il link non poteva nascere fino al riavvio successivo. Una
    chat gia' esistente **non cambia proprietario**: appartiene a chi l'ha
    rivendicata per primo, e riassegnarla su un salvataggio sposterebbe i
    messaggi di un utente nel feed di un altro.
    """
    riga = c.execute('SELECT id FROM users WHERE origin_profile=?', (nome,)).fetchone()
    if not riga:
        return
    utente = riga[0]
    pid = c.execute(
        'SELECT p.id FROM parsers p JOIN users u ON u.id = p.user_id'
        ' WHERE p.name=? AND p.id IS NOT NULL AND u.origin_profile=?',
        (parser_nome, nome)).fetchone()
    if not pid:
        return
    for chat in _chat_della_stringa(chat_ids):
        cid = _riga_chat(c, chat)
        if cid is None:
            c.execute('INSERT INTO chats(telegram_chat_id, owner_user_id)'
                      ' VALUES (?,?)', (chat, utente))
            cid = _riga_chat(c, chat)
        if cid is not None:
            c.execute('INSERT OR IGNORE INTO parser_chats(parser_id, chat_id)'
                      ' VALUES (?,?)', (pid[0], cid))


def _riconcilia_link_del_profilo(c, nome, chat_ids, parser_nome, prima=None):
    """I link di UN profilo allineati a com'e' adesso. Fonte unica (regola 3).

    `prima` e' la riga `(chat_ids, parser)` del profilo **prima** della scrittura,
    o `None` per un profilo nuovo: senza, cambiare il parser di un profilo
    lascerebbe vivo il link del parser vecchio e quella chat continuerebbe a far
    girare un parser che il proprietario ha appena sostituito — il limite
    dichiarato e rimesso al proprietario sulla PR #44, che qui si chiude.

    Non committa: la decide il chiamante, cosi' la riconciliazione e la
    scrittura del profilo stanno nella stessa transazione.
    """
    if prima is not None:
        _stacca_link_del_profilo(c, nome, prima[0], prima[1])
    _attacca_link_del_profilo(c, nome, chat_ids, parser_nome)


def _collega_parser_alle_chat(c):
    """Il TRAVASO dei link dai profili legacy — una volta sola per database.

    E' il ponte fra il mondo legacy (profili con `chat_ids` a virgole) e il
    dispatch per-utente: ogni riga di `parser_chats` dice «questo parser elabora
    i messaggi di questa chat», e il webhook la legge al posto del
    primo-profilo-alfabetico. Due profili sulla stessa chat producono DUE link —
    ognuno al proprio parser, ognuno verso il feed del proprio utente — ed e'
    cosi' che il pericolo 1 della #25 si chiude: nessuno «vince» la chat,
    ognuno riceve il suo.

    **Girava a ogni avvio, adesso no**, ed e' la sostanza di questo PR: una
    conversione ripetuta e' indistinguibile da un seme, perche' rimette in piedi
    ogni link che il proprietario ha cancellato. Da qui in avanti i link li
    tiene aggiornati `_riconcilia_link_del_profilo`, chiamata dalle scritture
    dei profili. Il chiamante gestisce il marcatore in `migrazioni`.

    **E riconcilia invece di aggiungere soltanto**, che e' la differenza fra un
    upgrade sano e un difetto ereditato: la vecchia semina era solo-aggiunta, e
    fino a questo PR nessuna scrittura toglieva i link: un profilo eliminato, o
    un parser sostituito via API, lasciava vivo il link vecchio. Quel link
    continuerebbe a elaborare la chat **per sempre** — i detach nuovi conoscono
    solo la configurazione corrente, e questo travaso non gira mai piu'. Al
    momento in cui gira, ogni riga di `parser_chats` viene dalla vecchia semina
    (era l'unico codice che le scrivesse) e nessuna richiesta e' ancora stata
    servita: tenere cio' che i profili giustificano e togliere il resto e'
    esattamente la conversione giusta. `[REAL_FINDING]` di GPT-5.6 Sol, gate
    finale della PR #46.
    """
    profili = c.execute(
        'SELECT name, chat_ids, parser FROM profiles ORDER BY name').fetchall()
    giustificati = set()
    for nome, chat_ids, parser_nome in profili:
        pid = c.execute(
            'SELECT p.id FROM parsers p JOIN users u ON u.id = p.user_id'
            ' WHERE p.name=? AND p.id IS NOT NULL AND u.origin_profile=?',
            (parser_nome, nome)).fetchone()
        if not pid:
            continue
        for chat in _chat_della_stringa(chat_ids):
            cid = _riga_chat(c, chat)
            if cid is not None:
                giustificati.add((pid[0], cid))
    for parser_id, chat_id in c.execute(
            'SELECT parser_id, chat_id FROM parser_chats').fetchall():
        if (parser_id, chat_id) not in giustificati:
            c.execute('DELETE FROM parser_chats WHERE parser_id=? AND chat_id=?',
                      (parser_id, chat_id))
    for nome, chat_ids, parser_nome in profili:
        _attacca_link_del_profilo(c, nome, chat_ids, parser_nome)


def _una_tantum(c, nome):
    """Vero la PRIMA volta che questa migrazione gira su questo database.

    Il marcatore si inserisce subito e viene committato dal chiamante insieme al
    lavoro: una migrazione che muore a meta' non lascia il marcatore, quindi il
    prossimo avvio riprova invece di saltarla credendola fatta.
    """
    if c.execute('SELECT 1 FROM migrazioni WHERE nome=?', (nome,)).fetchone():
        return False
    c.execute('INSERT INTO migrazioni(nome) VALUES (?)', (nome,))
    return True


def migra(c):
    """Porta il database allo schema corrente. Idempotente: si puo' rieseguire.

    Non cancella e non rinomina niente. Le tre tabelle originali restano con la
    loro forma, gli endpoint continuano a leggerle, e nessuna rotta cambia
    comportamento — se questa migrazione avesse un difetto il servizio funziona
    comunque e si corregge senza aver perso dati. E' la scelta deliberata per il
    cambiamento piu' rischioso del progetto: in produzione il database sta su un
    volume e contiene i parser veri del proprietario.

    L'idempotenza non e' un'aspirazione: `CREATE TABLE IF NOT EXISTS`, `ALTER`
    dentro un `try` che ingoia solo «duplicate column name», e `INSERT OR IGNORE`.
    Il test la esegue due volte di fila su un database popolato e confronta.
    """
    for istruzione in SCHEMA_ORIGINALE + SCHEMA_MULTIUTENTE:
        c.execute(istruzione)
    _chiudi_richieste_duplicate(c)
    # L'indice si crea DOPO la deduplica, e l'ordine e' la sostanza: `CREATE UNIQUE INDEX` su
    # dati che lo violano **solleva**, e sollevare qui significa che `db()` non torna piu' —
    # cioe' il servizio non risponde a nessuna richiesta, feed compreso. Un vincolo aggiunto
    # per correggere un difetto non deve poter uccidere il servizio proprio sui database che
    # quel difetto ha prodotto. Bloccante di Claude Fable 5 sulla PR #26.
    #
    # Una sola richiesta APERTA per utente, imposta dal database e non dal codice: la rilettura
    # dentro `BEGIN IMMEDIATE` copre le richieste che passano da questo processo, l'indice copre
    # anche quelle che non ci passano — un secondo worker, o una scrittura fatta a mano. Le
    # richieste gia' decise possono essere quante si vuole, perche' sono storia: da qui l'indice
    # **parziale**. Chiesto da GPT-5.5.
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS richiesta_aperta_unica'
              ' ON access_requests(user_id) WHERE decided_at IS NULL')
    for tabella, colonna, tipo in COLONNE_MULTIUTENTE:
        try:
            c.execute(f'ALTER TABLE {tabella} ADD COLUMN {colonna} {tipo}')
        except sqlite3.OperationalError as e:
            # SOLO la colonna che esiste gia'. Prima questo `except` era nudo e
            # avrebbe ingoiato anche «no such table», cioe' uno schema mancante.
            if 'duplicate column name' not in str(e).lower():
                raise
    # Il SEME non c'e' piu' (#25 lavoro E, PR 4 della sequenza #2). Qui due
    # `INSERT OR IGNORE` reinserivano a ogni avvio `DEFAULT_PARSER` e il profilo
    # PIERO: `OR IGNORE` protegge dal duplicato, NON dalla resurrezione — su riga
    # assente inserisce. Conseguenze misurate nella #25: cancellare non era
    # durevole, e rinominare produceva un doppione garantito col profilo che
    # continuava a nominare il vecchio. In produzione quelle righe esistono gia'
    # sul volume (`/data`): sono DATI, e la migrazione li trova senza doverli
    # ricreare. Un database vergine resta vergine: il primo parser lo crea chi
    # fa login, non il codice.
    c.execute('UPDATE signals SET profile=? WHERE profile IS NULL', (PIERO_PROFILE,))
    _travasa_nel_multiutente(c)
    c.commit()


def _chiudi_richieste_duplicate(c):
    """Lascia UNA richiesta aperta per utente, chiudendo le altre come `duplicata`.

    Serve prima di creare l'indice UNIQUE parziale: su un database che contiene gia' i
    duplicati — cioe' proprio quello prodotto dal difetto che l'indice previene — un
    `CREATE UNIQUE INDEX` solleva, e sollevare dentro `migra()` significa che `db()` non torna
    piu' e il servizio non risponde a nessuna richiesta.

    **Chiude e non cancella**, e tiene la piu' VECCHIA: quella e' la richiesta che l'utente ha
    fatto davvero, le altre sono i clic ripetuti. Le chiuse restano in tabella con
    `outcome='duplicata'`, cosi' il proprietario vede cosa e' successo invece di trovare righe
    scomparse.
    """
    duplicati = c.execute(
        'SELECT user_id FROM access_requests WHERE decided_at IS NULL'
        ' GROUP BY user_id HAVING COUNT(*) > 1').fetchall()
    for (utente,) in duplicati:
        tenuta = c.execute('SELECT MIN(id) FROM access_requests'
                           ' WHERE user_id=? AND decided_at IS NULL', (utente,)).fetchone()[0]
        c.execute("UPDATE access_requests SET decided_at=strftime('%s','now'),"
                  " outcome='duplicata'"
                  ' WHERE user_id=? AND decided_at IS NULL AND id != ?', (utente, tenuta))
    if duplicati:
        logging.getLogger('xtrader.relay').warning(
            'migrazione: chiuse le richieste di accesso duplicate di %d utenti',
            len(duplicati))


def _slug_libero(base, presi):
    """Uno slug non ancora usato, derivato da `base` in modo DETERMINISTICO.

    Non casuale, e non e' un dettaglio: la migrazione rigira a ogni riavvio, e uno
    slug casuale rinominerebbe le cose dei clienti ogni volta. Con l'ordine di
    partenza fisso, `Uno`/`UNO`/`uno` danno sempre `uno`, `uno-2`, `uno-3`.
    """
    if base not in presi:
        return base
    for n in range(2, 10_000):
        candidato = f'{base}-{n}'
        if candidato not in presi:
            return candidato
    raise RuntimeError(f'impossibile disambiguare lo slug {base!r}')


def _assegna_slug_e_ordine(c):
    """Slug univoci e `ordine` deterministico ai parser che ne sono senza.

    Esiste per un bloccante misurato sulla PR #22, ed era il piu' grave introdotto
    finora: `slug = lower(name)` mandava `Over15` e `over15` sullo stesso slug,
    l'indice UNIQUE non si creava, `migra()` sollevava — e `migra()` sta sul percorso
    di `db()`, cioe' di OGNI richiesta. Il feed avrebbe iniziato a dare 500 e non
    avrebbe piu' smesso.

        IntegrityError: UNIQUE constraint failed: parsers.user_id, parsers.slug

    Segnalato insieme da Claude Fable 5 e GPT-5.5. La lezione e' che una migrazione
    sul percorso di ogni richiesta non puo' sollevare per dati che esistono: qualunque
    stato del database deve poter essere attraversato.

    Si assegna solo a chi ha `slug`/`ordine` a NULL: chi ne ha gia' uno lo tiene, cosi'
    un ordine scelto dall'utente non viene sovrascritto al riavvio successivo.
    """
    presi = {r[0] for r in c.execute(
        'SELECT slug FROM parsers WHERE slug IS NOT NULL').fetchall()}
    massimo = c.execute('SELECT MAX(ordine) FROM parsers').fetchone()[0]
    prossimo = (massimo + 1) if massimo is not None else 0
    # `ORDER BY name` e' cio' che rende stabile la disambiguazione: due esecuzioni
    # incontrano gli stessi nomi nello stesso ordine.
    for (nome,) in c.execute(
            'SELECT name FROM parsers WHERE slug IS NULL OR ordine IS NULL'
            ' ORDER BY name').fetchall():
        riga = c.execute('SELECT slug, ordine FROM parsers WHERE name=?', (nome,)).fetchone()
        slug, ordine = riga
        if slug is None:
            slug = _slug_libero(nome.lower(), presi)
            presi.add(slug)
            c.execute('UPDATE parsers SET slug=? WHERE name=?', (slug, nome))
        if ordine is None:
            c.execute('UPDATE parsers SET ordine=? WHERE name=?', (prossimo, nome))
            prossimo += 1


def riconciliazione_autorizzata(utente):
    """Vero se il proprietario ha AUTORIZZATO l'assorbimento di QUESTA riga vuota.

    Esiste perche' `possiede_qualcosa()` non basta, e il perche' e' una constatazione:
    distingue un account **pieno** da uno vuoto, non un **cliente** da una riga nata per
    errore. Un cliente appena registrato non possiede ancora niente — e' lo stato normale di
    chi si iscrive — quindi la sua riga e' indistinguibile dalla riga vuota che nasce quando
    il proprietario fa login prima che `TELEGRAM_ADMIN_ID` arrivi nel processo: due righe di
    `users` con un'identita' Telegram e nient'altro. Misurato con la variabile che per un
    refuso contiene l'ID di un cliente: al suo login la sua riga veniva svuotata e la sua
    identita' finiva sulla riga del proprietario con `is_admin=1`, cioe' **il cliente entrava
    nella dashboard del proprietario**. Bloccante di GPT-5.6 Sol sulla PR #24.

    Quando nessun dato distingue i due casi, il solo marcatore affidabile e' il consenso di
    chi sa: il proprietario legge il `409`, riconosce quella riga come sua, imposta
    `TELEGRAM_ADMIN_RECONCILE` e rifa' login. Il costo e' una variabile impostata una volta;
    il guadagno e' che con la variabile sbagliata il servizio **non assorbe niente**, perche'
    con un ID sbagliato anche la fonte dell'identita' e' sbagliata e non si puo' dedurre nulla
    da essa. Assente → si fallisce chiusi, che sull'isolamento fra utenti e' il verso
    obbligato (priorita' 7 di `CLAUDE.md`).

    Il valore e' l'**identificativo della riga** da assorbire, non un `1`. La prima versione
    era un interruttore globale, e GPT-5.5 ha alzato il rischio giusto: la documentazione
    diceva di togliere la variabile dopo l'uso, ma una variabile che va ricordata di togliere
    e' una variabile che resta — e da quel momento un refuso futuro in `TELEGRAM_ADMIN_ID`
    verso una riga vuota veniva assorbito di nuovo, cioe' il fail-closed non c'era piu'.
    Legato alla riga, un consenso dimenticato e' innocuo, e non per prudenza: la riga
    assorbita **non viene cancellata**, quindi il suo id non viene mai riusato da un utente
    nuovo, e il consenso vecchio non puo' combaciare con un caso nuovo.
    """
    return TELEGRAM_ADMIN_RECONCILE != '' and TELEGRAM_ADMIN_RECONCILE == str(utente)


# Quanti giorni prima della scadenza si avvisa il cliente. Dalla Issue #2.
GIORNI_PROMEMORIA = 5

# Lo username del bot, senza `@`. Serve **solo** per costruire il deep link: il bot Telegram
# non puo' scrivere per primo, quindi il cliente deve aprire una conversazione, e per aprirla
# gli si da' un link. Assente → il link non c'e' e la risposta lo dice, invece di inventare un
# indirizzo che porta a un bot di qualcun altro.
TELEGRAM_BOT_USERNAME = os.getenv('TELEGRAM_BOT_USERNAME', '').strip().lstrip('@')


def link_del_bot(payload=None):
    """Il deep link che apre il bot, o `None` se lo username non e' configurato.

    `None` e non una stringa a caso: un link costruito con uno username vuoto
    (`https://t.me/?start=...`) porta alla home di Telegram, e il cliente che lo segue crede
    di aver fatto la sua parte mentre il bot continua a non poterlo raggiungere. Meglio dire
    «non ce l'ho» e far comparire l'istruzione manuale.
    """
    if not TELEGRAM_BOT_USERNAME:
        return None
    base = f'https://t.me/{TELEGRAM_BOT_USERNAME}'
    return f'{base}?start={payload}' if payload else base


def invia_messaggio_telegram(chat_id, testo, bot_token=None):
    """Manda un messaggio. Restituisce `(riuscito, motivo)` e **non solleva**.

    Non solleva perche' i suoi chiamanti stanno su percorsi che devono concludere comunque:
    approvare un accesso e' una decisione, e non si annulla una decisione perche' l'avviso non
    e' partito. Ma il motivo torna a chi chiama, e li' **non va ingoiato**: la Issue #2 lo
    chiede esplicitamente, perche' un invio fallito in silenzio produce lo stato peggiore — il
    proprietario crede di aver avvisato, il cliente non sa di essere attivo, e nessuno dei due
    ha modo di accorgersene.

    Il caso che rende tutto questo necessario e' la trappola 1: `sendMessage` **falisce** se la
    persona non ha mai scritto al bot. Non e' un errore di rete, e' lo stato normale di chi
    entra col Login Widget e non apre mai la conversazione.

    Il `motivo` non viene loggato da qui e non contiene il token: la `description` di Telegram
    fa eco all'URL inviato, che porta il token del bot nel percorso.
    """
    import urllib.error
    import urllib.parse
    import urllib.request
    token = bot_token or BOT_TOKEN
    if not token:
        return False, 'bot non configurato'
    if not chat_id:
        return False, 'destinatario sconosciuto'
    parametri = urllib.parse.urlencode({'chat_id': chat_id, 'text': testo}).encode('utf-8')
    richiesta = urllib.request.Request(
        f'https://api.telegram.org/bot{token}/sendMessage', data=parametri, method='POST',
        headers={'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with urllib.request.urlopen(richiesta, timeout=10) as risposta:  # noqa: S310
            corpo = json.loads(risposta.read().decode('utf-8'))
    except Exception as e:
        # Il TIPO dell'eccezione, non il suo testo: un `HTTPError` di Telegram porta l'URL, e
        # l'URL porta il token del bot. Il tipo dice al proprietario se e' rete o rifiuto.
        return False, f'invio fallito ({type(e).__name__})'
    # Come per `setWebhook`: il codice HTTP non basta, Telegram segnala parte dei rifiuti con
    # `200` e `ok: false` — ed e' proprio il caso di «non puo' scrivere per primo».
    if not corpo.get('ok'):
        return False, 'Telegram ha rifiutato la consegna'
    return True, None

# Gli stati dell'accesso, in un posto solo. `in_attesa` e' fra `registrato` e `attivo`:
# ha chiesto, il proprietario non ha ancora deciso.
STATI_ACCESSO = ('registrato', 'in_attesa', 'attivo', 'scaduto', 'sospeso')


# Gli stati in cui il feed NON consegna segnali e il webhook non elabora. Sono i due in cui
# l'accesso c'era e non c'e' piu': `scaduto` per il tempo, `sospeso` per decisione.
#
# `registrato` e `in_attesa` **non** sono qui, ed e' una scelta di scopo dichiarata: i feed
# per profilo esistono da prima del flusso di approvazione, e i loro utenti sono nati
# `registrato` dalla migrazione (`_travasa_nel_multiutente`). Bloccarli adesso spegnerebbe in
# silenzio un feed che oggi funziona — cioe' romperei la produzione per applicare una regola
# che riguarda clienti che ancora non esistono. La Issue #2 per la PR 7 parla di **scaduto**;
# «un utente nuovo non puo' fare nulla» diventa vincolante nella PR 8, quando il feed passa
# all'utente e nessun feed legacy dipende piu' da questo.
ACCESSI_BLOCCATI = ('scaduto', 'sospeso')


def _istante(valore):
    """Un istante letto dal database, o `None` se quel valore non e' un istante.

    SQLite ha tipi dinamici: la colonna e' `INTEGER` ma niente vieta che una versione futura
    ci scriva `''`. `int('')` solleva, e su `/api/me` diventerebbe un **500** su una rotta che
    dovrebbe solo dire chi sei. Rischio alzato da GPT-5.5 sulla PR #26.

    Chi legge decide cosa fare del `None`, e le due decisioni sono opposte per costruzione:
    `stato_effettivo()` tratta un valore illeggibile come **scaduto** (fail-closed: un accesso
    che non si sa quando finisce non e' un accesso infinito), `giorni_rimasti()` restituisce
    `None` perche' non ha un numero da dare.
    """
    if valore is None:
        return None
    try:
        return int(valore)
    except (TypeError, ValueError):
        return None


def stato_effettivo(status, access_expires_at, adesso=None):
    """Lo stato che CONTA adesso, che non e' sempre quello scritto nella colonna.

    **La scadenza e' un istante, non un evento.** Nessun processo passa a mezzanotte a
    riscrivere `status` delle righe scadute: la colonna resta `'attivo'` per sempre, e chi
    la legge direttamente racconta una bugia. Misurato prima di questa funzione, su
    `GET /api/me` di un cliente scaduto il giorno prima:

        {'stato': 'attivo', 'accesso_scade': <ieri>}

    E' il sintomo che la Issue #2 nomina esplicitamente — «pannello che dice attivo e feed
    vuoto» — e la sua forma peggiore non e' l'etichetta sbagliata nella dashboard: e' che
    ogni pezzo del servizio decida per conto proprio se una scadenza conta. Il feed direbbe
    una cosa, il webhook un'altra, il pannello una terza. Per questo la conversione vive qui
    e in un posto solo (regola 3), e la usano `/api/me`, il feed e il webhook.

    Non tocca il database: `status` resta cio' che il proprietario ha DECISO (`attivo`,
    `sospeso`), `access_expires_at` resta quando quella decisione finisce. Riscrivere la
    colonna a ogni lettura sarebbe una scrittura su ogni richiesta, e renderebbe impossibile
    distinguere «scaduto» da «sospeso a mano».
    """
    if status != 'attivo' or access_expires_at is None:
        return status
    scadenza = _istante(access_expires_at)
    if scadenza is None:
        # Illeggibile: `scaduto`, non `attivo`. Un accesso di cui non si sa quando finisce non
        # e' un accesso senza fine — quello e' `NULL`, e **solo** `NULL`.
        #
        # La prima versione trattava anche la stringa vuota come «nessuna scadenza», cioe'
        # come `NULL`: un `attivo` con `''` restava attivo **per sempre**. Fail-open, e nella
        # stessa funzione il cui commento diceva il contrario — la forma di difetto che
        # `CLAUDE.md` racconta come «l'affermazione e la sua smentita nello stesso posto».
        # Peggio: l'avevo cementata in un test che pretendeva quel comportamento. Bloccante
        # di Claude Fable 5 sulla PR #26.
        return 'scaduto'
    adesso = int(time.time()) if adesso is None else adesso
    return 'attivo' if scadenza > adesso else 'scaduto'


def giorni_rimasti(access_expires_at, adesso=None):
    """I giorni interi che restano, o `None` se non c'e' una scadenza.

    Arrotonda per ECCESSO: a 30 ore dalla scadenza restano «2 giorni» e non «1», perche'
    troncando, l'ultimo giorno di accesso il cliente leggerebbe «0 giorni rimasti» mentre il
    feed funziona ancora. Un numero che dice zero su un accesso vivo insegna a non fidarsi
    del numero. Scaduto → `0`, mai un negativo.
    """
    scadenza = _istante(access_expires_at)
    if scadenza is None:
        return None
    adesso = int(time.time()) if adesso is None else adesso
    mancano = scadenza - adesso
    if mancano <= 0:
        return 0
    return -(-mancano // 86400)


def nuova_scadenza(access_expires_at, giorni, adesso=None):
    """La scadenza dopo aver concesso `giorni`, con il caso limite che conta.

    Due rami, e il secondo e' la ragione per cui questa e' una funzione:

        se la scadenza attuale e' nel FUTURO:  si somma a quella (i rinnovi si sommano)
        altrimenti:                            si riparte da ADESSO

    Senza il secondo ramo, prorogare di 30 giorni un cliente scaduto da due mesi gli
    darebbe una scadenza **nel passato**: il pannello direbbe «attivo» e il feed sarebbe
    vuoto, cioe' esattamente lo stato che `stato_effettivo()` esiste per non produrre. E'
    scritto nella Issue #2 come caso limite da non riscoprire.
    """
    adesso = int(time.time()) if adesso is None else adesso
    attuale = _istante(access_expires_at)
    base = attuale if attuale is not None and attuale > adesso else adesso
    return base + int(giorni) * 86400


def accesso_bloccato_del_profilo(c, profilo):
    """Lo stato che BLOCCA il feed di questo profilo, o `None` se non c'e' niente da bloccare.

    Il profilo e' l'unita' del feed di oggi; l'utente e' l'unita' dell'abbonamento. Il ponte
    fra i due e' `users.origin_profile`, scritto dalla migrazione. Passa all'utente nella
    PR 8 della Issue #2, e allora questa funzione cambia sorgente ma non significato — per
    questo la decisione sta qui e non dentro il feed.

    Due casi restituiscono `None` di proposito:

    - **nessun utente collegato al profilo.** Un profilo senza riga in `users` non ha un
      abbonamento da far scadere, e sospendergli il feed per assenza di dati sarebbe una
      regressione provocata dalla mancanza di un'informazione;
    - **il proprietario** (`is_admin`). Il suo accesso non e' un abbonamento: se dipendesse da
      una scadenza, una riga sbagliata nel database spegnerebbe il feed che XTrader interroga
      in produzione. E' la stessa ragione per cui `is_admin` non viene toccato dalla revoca
      dell'identita' Telegram.
    """
    riga = c.execute('SELECT status, access_expires_at, is_admin FROM users'
                     ' WHERE origin_profile=?', (profilo,)).fetchone()
    if riga is None:
        return None
    return _blocco_della_riga(riga[0], riga[1], riga[2])


def _blocco_della_riga(status, access_expires_at, is_admin):
    """La stessa decisione, sui valori di una riga di `users` gia' in mano.

    Fonte unica (regola 3) per `accesso_bloccato_del_profilo`, per il feed
    per-utente e per il dispatch del webhook: tre lettori, una decisione.
    Il proprietario (`is_admin`) non e' un abbonamento — vedi sopra.
    """
    if is_admin:
        return None
    stato = stato_effettivo(status, access_expires_at)
    return stato if stato in ACCESSI_BLOCCATI else None


# Le tabelle il cui possesso rende una riga di `users` l'account di QUALCUNO: cose che
# l'utente COSTRUISCE — un parser, una chat rivendicata, la sua libreria mercati (#33), le
# sue sorgenti squadre (#34) — non tracce di passaggio (segnali, log) ne' artefatti di
# processo (verifiche, richieste). Ogni voce e' `(tabella, colonna)`.
#
# DEVE restare allineata a cio' che `riconcilia_su_utente` travasa come DATI dell'utente
# (`_trasferisci_parser`/`_trasferisci_sport`/`_trasferisci_sorgenti_squadre` + le chat in
# `RIFERIMENTI_DATI_UTENTE`): se il guard elenca meno di cio' che la riconciliazione sposta,
# un utente con SOLO la parte mancante viene classificato vuoto e assorbito — la libreria o
# le sorgenti di un cliente finiscono sull'account del proprietario (audit #81, I1). Le
# `competizioni` non hanno una voce a se': riferiscono uno `sport` dello stesso utente
# (`sport_id`), quindi chi ne possiede una possiede gia' uno sport ed e' coperto — come i
# mercati, le selezioni, le squadre e gli alias, che seguono i loro genitori. Fonte unica:
# la usa `possiede_qualcosa`.
TABELLE_POSSEDUTE = (
    ('parsers', 'user_id'),
    ('chats', 'owner_user_id'),
    ('sports', 'user_id'),
    ('sorgenti_squadre', 'user_id'),
)


def possiede_qualcosa(c, utente):
    """Vero se questa riga di `users` e' l'account di QUALCUNO, non una riga nata per errore.

    Il criterio sono i **dati costruiti** dall'utente — un parser, una chat rivendicata, la
    libreria mercati (#33), le sorgenti squadre (#34): vedi `TABELLE_POSSEDUTE`. Segnali e
    log non contano: sono tracce di passaggio, e nella riconciliazione seguono l'utente senza
    dire di chi sia l'account.

    Serve perche' la riparazione del collegamento dell'amministratore presumeva che la riga
    da assorbire fosse **sempre** quella nata per errore. Non lo e': basta che
    `TELEGRAM_ADMIN_ID` contenga per sbaglio l'ID di un cliente, e al suo login il servizio
    gli travasava i dati sulla riga del proprietario, gli azzerava il `telegram_id` e gli
    dava `is_admin=1`. Il cliente perdeva tutto **e** otteneva la dashboard di un altro: la
    violazione dell'isolamento fra utenti che e' la priorita' 7 di `CLAUDE.md`, senza nessun
    errore a segnalarla. Misurato prima di correggerlo. Bloccante di Claude Fable 5 sulla PR
    #24; esteso a libreria e sorgenti dall'audit #81 (I1), che aveva trovato lo stesso buco
    per un cliente con SOLO la libreria — `possiede_qualcosa` guardava allora solo parser e
    chat, mentre `riconcilia_su_utente` travasava anche sport e sorgenti.
    """
    for tabella, colonna in TABELLE_POSSEDUTE:
        if c.execute(f'SELECT 1 FROM {tabella} WHERE {colonna}=? LIMIT 1',
                     (utente,)).fetchone():
            return True
    return False


def riconcilia_su_utente(c, da_utente, a_utente):
    """Travasa tutto da `da_utente` a `a_utente` e libera il suo `telegram_id`.

    Esiste perche' il collegamento del proprietario deve essere **idempotente**: se una
    riga sbagliata possiede il suo `telegram_id`, non basta scrivere quel valore sulla riga
    giusta — `users.telegram_id` e' UNIQUE, quindi va prima liberato. E cio' che quella
    riga avesse accumulato non va perso, da cui il travaso invece di una `DELETE`.

    Riusa `RIFERIMENTI_DATI_UTENTE` e `_trasferisci_parser` della migrazione (regola 3):
    `RIFERIMENTI_DATI_UTENTE` e' *derivato* da `RIFERIMENTI_UTENTE` togliendo le due colonne
    di `admin_audit`, che sono storia e non dati (vedi il commento sulla costante). Derivato e
    non ricopiato: il test che verifica la completezza di `RIFERIMENTI_UTENTE` copre quindi
    anche questa funzione, e una colonna nuova arriva qui da se'.

    La riga perdente **non** viene cancellata: le si azzera `telegram_id` e resta un
    account senza niente. Una `DELETE` sarebbe irreversibile e potrebbe orfanare una
    colonna che nessuno ha ancora aggiunto a `RIFERIMENTI_UTENTE`; un `NULL` no. E' la
    scelta prudente in una funzione che nasce per riparare, non per fare pulizia.

    Non fa `commit`: la libera chi chiama, perche' questa operazione deve stare nella
    stessa transazione della scrittura che segue.
    """
    for tabella, colonna in RIFERIMENTI_DATI_UTENTE:
        c.execute(f'UPDATE {tabella} SET {colonna}=? WHERE {colonna}=?',
                  (a_utente, da_utente))
    _trasferisci_parser(c, da_utente, a_utente)
    _trasferisci_sport(c, da_utente, a_utente)
    _trasferisci_sorgenti_squadre(c, da_utente, a_utente)
    # `session_version` incrementata sulla riga svuotata: i cookie emessi per quell'account
    # restano altrimenti validi, e da quel momento aprono una sessione su un utente che non
    # possiede piu' niente. Appartengono comunque al proprietario, quindi non e' un buco di
    # sicurezza — e' igiene: `session_version` esiste per invalidare SUBITO, e un account
    # riconciliato via e' esattamente il caso in cui una sessione va chiusa invece di
    # scadere da se'. Segnalato come punto da sorvegliare da Claude Fable 5 sulla PR #24.
    c.execute('UPDATE users SET telegram_id=NULL, session_version=session_version+1'
              ' WHERE id=?', (da_utente,))


def _trasferisci_parser(c, da_utente, a_utente):
    """Passa i parser di un utente a un altro, ri-disambiguando gli slug che collidono.

    Un `UPDATE parsers SET user_id=?` in blocco solleverebbe se i due utenti hanno un
    parser con lo stesso slug: `UNIQUE (user_id, slug)` vieta la coppia, e sotto quel
    vincolo due parser di due utenti diversi con lo stesso slug sono uno stato legale —
    quindi lo stato esiste e la scrittura lo incontra. Misurato:
    `IntegrityError: UNIQUE constraint failed: parsers.user_id, parsers.slug`, cioe'
    `migra()` che solleva sul percorso di ogni richiesta. Bloccante di GPT-5.5.

    Chi era GIA' del destinatario tiene il suo slug: a cambiare nome e' chi arriva, che
    e' l'unico ordine sensato — l'altro potrebbe essere in un URL che qualcuno usa.
    `ORDER BY name` per lo stesso motivo di `_assegna_slug_e_ordine`: due esecuzioni
    devono disambiguare allo stesso modo.
    """
    for nome, slug in c.execute(
            'SELECT name, slug FROM parsers WHERE user_id=? ORDER BY name',
            (da_utente,)).fetchall():
        if slug is not None and c.execute(
                'SELECT 1 FROM parsers WHERE user_id=? AND slug=?',
                (a_utente, slug)).fetchone():
            presi = {r[0] for r in c.execute(
                'SELECT slug FROM parsers WHERE slug IS NOT NULL').fetchall()}
            c.execute('UPDATE parsers SET slug=? WHERE name=?',
                      (_slug_libero(slug, presi), nome))
        c.execute('UPDATE parsers SET user_id=? WHERE name=?', (a_utente, nome))


def _trasferisci_sport(c, da_utente, a_utente):
    """Passa gli sport (#33) di un utente a un altro, come `_trasferisci_parser`.

    Stessa classe, stesso vincolo: `UNIQUE (user_id, slug)` rende legale lo stesso
    slug su due utenti diversi, quindi il travaso cieco lo incontra e solleva.
    Chi era gia' del destinatario tiene il suo slug; a cambiare nome e' chi
    arriva. Mercati e selezioni riferiscono `sport_id`, che non cambia: seguono
    il loro sport senza una riga di codice. `sports.user_id` e' quindi fuori da
    `RIFERIMENTI_UTENTE` per lo stesso motivo di `parsers.user_id` — la guardia
    dello schema lo dichiara esplicitamente.
    """
    for sport_id, slug in c.execute(
            'SELECT id, slug FROM sports WHERE user_id=? ORDER BY id',
            (da_utente,)).fetchall():
        if c.execute('SELECT 1 FROM sports WHERE user_id=? AND slug=?',
                     (a_utente, slug)).fetchone():
            presi = {r[0] for r in c.execute('SELECT slug FROM sports').fetchall()}
            c.execute('UPDATE sports SET slug=? WHERE id=?',
                      (_slug_libero(slug, presi), sport_id))
        c.execute('UPDATE sports SET user_id=? WHERE id=?', (a_utente, sport_id))


def _trasferisci_sorgenti_squadre(c, da_utente, a_utente):
    """Passa sorgenti squadre e competizioni (#34) di un utente a un altro.

    Stessa classe di `_trasferisci_parser`/`_trasferisci_sport`: `UNIQUE
    (user_id, nome)` rende legale «test 1» su due utenti diversi, quindi il
    travaso cieco collide e solleva — chi era gia' del destinatario tiene il
    suo nome, chi arriva viene rinominato con un suffisso numerico. Le
    competizioni riferiscono `sport_id`, che il travaso degli sport non cambia:
    seguono i loro sport, ma il loro `user_id` va comunque riscritto o
    resterebbero appese all'account svuotato. Squadre e alias riferiscono
    `competizione_id`/`sorgente_id` e non si toccano. `competizioni.user_id` e
    `sorgenti_squadre.user_id` sono percio' fuori da `RIFERIMENTI_UTENTE`,
    come `sports.user_id`, e la guardia dello schema lo dichiara.
    """
    for sorgente_id, nome in c.execute(
            'SELECT id, nome FROM sorgenti_squadre WHERE user_id=? ORDER BY id',
            (da_utente,)).fetchall():
        nuovo = nome
        if c.execute('SELECT 1 FROM sorgenti_squadre WHERE user_id=? AND nome=?',
                     (a_utente, nome)).fetchone():
            presi = {r[0] for r in c.execute(
                'SELECT nome FROM sorgenti_squadre').fetchall()}
            progressivo = 2
            while f'{nome} ({progressivo})' in presi:
                progressivo += 1
            nuovo = f'{nome} ({progressivo})'
            c.execute('UPDATE sorgenti_squadre SET nome=? WHERE id=?',
                      (nuovo, sorgente_id))
        c.execute('UPDATE sorgenti_squadre SET user_id=? WHERE id=?',
                  (a_utente, sorgente_id))
    # Le competizioni non hanno lo stesso rischio di collisione: `UNIQUE
    # (user_id, sport_id, nome)` include `sport_id`, e gli sport restano righe
    # DISTINTE anche dopo il travaso (cambia il loro user_id, mai il loro id).
    c.execute('UPDATE competizioni SET user_id=? WHERE user_id=?',
              (a_utente, da_utente))


def _completa_colonne_nuove(c, profilo_proprietario):
    """Riempie `user_id`, `id`, `slug` e `ordine` di ogni parser che ne e' senza.

    `profilo_proprietario` e' OBBLIGATORIO e senza default, ed e' una scelta contro un
    difetto futuro: la funzione assegna a quell'utente ogni parser senza proprietario,
    e con `PIERO_PROFILE` cablato dentro il giorno in cui l'endpoint servira' piu'
    utenti un parser creato per un altro finirebbe **in silenzio** sotto Piero.
    Segnalato da Claude Fable 5 sulla PR #22. Come argomento, la decisione sta nei due
    chiamanti — dove chi la cambiera' la vede — invece che nascosta qui dentro.

    Chiamata dalla migrazione **e** dal salvataggio di un parser, e la ragione di
    quest'ultimo e' un bloccante di Claude Fable 5 sulla PR #22: `migra()` gira una
    volta per PROCESSO, quindi un parser creato via API dopo l'avvio restava con
    quelle quattro colonne a NULL fino al riavvio successivo. Non e' cosmetico —
    `parser_chats.chat_id` riferisce `parsers.id`, e l'indice `UNIQUE (user_id, slug)`
    non vincola le righe con `user_id` NULL, perche' in SQL `NULL != NULL`: la riga
    sfuggiva al vincolo che protegge l'isolamento (visto anche da GPT-5.5).

    Una fonte unica e non due chiamate copiate: la regola 3, sulla parte del codice
    dove una divergenza fra i due percorsi sarebbe invisibile.
    """
    proprietario = c.execute('SELECT id FROM users WHERE origin_profile=?',
                             (profilo_proprietario,)).fetchone()
    if proprietario:
        c.execute('UPDATE parsers SET user_id=? WHERE user_id IS NULL', (proprietario[0],))
    # `id` dal `rowid`, in una colonna vera: il `rowid` puo' cambiare con un VACUUM,
    # quindi memorizzarlo e' l'unico modo perche' un riferimento resti valido.
    c.execute('UPDATE parsers SET id=rowid WHERE id IS NULL')
    # `uid`, l'identita' non riusabile (#73): un valore DISTINTO per riga, non un
    # default costante — con lo stesso uid su tutti i parser la colonna non
    # distinguerebbe niente, che e' il difetto che esiste per chiudere. `randomblob`
    # e' valutato per riga da sqlite (misurato dal test sull'unicita'). Solo le
    # righe a NULL: la migrazione gira a ogni avvio e non deve ribattere gli uid
    # gia' assegnati, o un riferimento in volo diventerebbe stantio a ogni deploy.
    c.execute("UPDATE parsers SET uid = lower(hex(randomblob(16))) WHERE uid IS NULL")
    # `titolo` retrocompilato dal `name` per le righe legacy (schema pre-`titolo`): la
    # colonna e' additiva e nullable, ma il contratto API dichiara `titolo: str` e il
    # proprietario loggato vedrebbe `titolo: null` sul parser PIERO di default. Il `name`
    # e' un'etichetta onesta e non inventa dati; solo le righe a NULL, quindi idempotente
    # e mai sovrascrive un titolo scelto. Bloccante di GPT-5.6 Sol sulla PR #30.
    c.execute('UPDATE parsers SET titolo = name WHERE titolo IS NULL')
    _assegna_slug_e_ordine(c)


def _travasa_nel_multiutente(c):
    """I dati esistenti nello schema nuovo, senza toccare quelli vecchi.

    `telegram_id` resta NULL: il proprietario non ha ancora fatto login Telegram e
    inventarne uno creerebbe un utente che il login non riconoscerebbe. SQLite
    ammette piu' NULL in una colonna UNIQUE, quindi il vincolo regge.

    `token_hash` resta NULL per la stessa ragione: oggi il feed e' protetto da
    `CSV_ACCESS_TOKEN`, uno per tutto il servizio. I token per utente nascono con
    il feed per utente, e generarne uno qui vorrebbe dire scriverlo da qualche
    parte — cioe' un segreto in piu' senza nessuno che lo usi.
    """
    # La deduplica di `origin_profile` viene PRIMA di tutto, e l'ordine e' il punto.
    # Sta qui e non accanto al suo indice perche' i lookup del ciclo sotto risolvono
    # l'utente PER `origin_profile`: con due righe duplicate il lookup ne pesca una
    # arbitrariamente, e se pesca quella che poi perde l'etichetta, chat, segnali e
    # parser finiscono attribuiti a un utente che non risulta piu' quel profilo.
    # Deduplicando prima, ogni lookup risolve sulla riga sopravvissuta.
    #
    # Misurato con `PRAGMA reverse_unordered_selects = ON`, che inverte le scansioni
    # non ordinate: utente superstite 1, proprietario della chat 2, segnale 2 —
    # incoerente. Segnalato da CodeRabbit, marcato Trivial, ma e' attribuzione
    # sbagliata fra utenti.
    #
    # NON si cancella nessuna riga, e la differenza con `chats` e' sostanziale: una
    # riga di `users` possiede chat, parser e segnali, quindi cancellarla perderebbe
    # dati di un cliente. L'etichetta resta all'`id` piu' basso. Serve anche perche' un
    # indice UNIQUE non si crea su una tabella con duplicati: segnalato prima da Claude
    # Fable 5 e GPT-5.5.
    #
    # Ma azzerare l'etichetta NON BASTA, e la prima versione si fermava li': la riga
    # perdente restava proprietaria di cio' che possedeva, quindi quei dati finivano su
    # un utente che non risulta piu' quel profilo — nessuno li rivendica, e per il
    # codice multiutente sono di un altro. Misurato: superstite 1, chat 2, segnale 2.
    # `[REAL_FINDING]` di GPT-5.6 Sol. Cio' che la perdente possiede si TRASFERISCE
    # al superstite prima di togliere l'etichetta.
    #
    # Il trasferimento dei parser passa da `_trasferisci_parser`, che ri-disambigua lo
    # slug quando collide. Qui c'era scritto che gli slug sono univoci GLOBALMENTE e che
    # quindi spostare un parser non poteva violare `UNIQUE (user_id, slug)`: vero per il
    # codice che li assegna, ma non e' un vincolo — il vincolo e' sulla COPPIA, e sotto
    # quel vincolo due parser di due utenti con lo stesso slug sono uno stato legale.
    # Misurato: `IntegrityError: UNIQUE constraint failed: parsers.user_id, parsers.slug`.
    # Bloccante di GPT-5.5, ed era una regola appoggiata a una prova che non esiste.
    for (etichetta,) in c.execute(
            'SELECT origin_profile FROM users WHERE origin_profile IS NOT NULL'
            ' GROUP BY origin_profile HAVING COUNT(*) > 1').fetchall():
        # Vince chi ha un `telegram_id`, e solo a parita' l'`id` piu' basso. La riga con
        # `telegram_id` e' quella con cui l'utente ACCEDE: tenere l'id minimo a
        # prescindere sposterebbe i dati su un segnaposto senza identita', staccandoli
        # dall'account con cui il proprietario fa login. `[REAL_FINDING]` di GPT-5.6 Sol.
        # `(telegram_id IS NULL)` vale 0 per chi ce l'ha e 1 per chi no, quindi
        # l'ordinamento mette davanti l'identita' vera e resta deterministico.
        utenti = [r[0] for r in c.execute(
            'SELECT id FROM users WHERE origin_profile=?'
            ' ORDER BY (telegram_id IS NULL), id', (etichetta,)).fetchall()]
        superstite, perdenti = utenti[0], utenti[1:]
        for perdente in perdenti:
            for tabella, colonna in RIFERIMENTI_UTENTE:
                c.execute(f'UPDATE {tabella} SET {colonna}=? WHERE {colonna}=?',
                          (superstite, perdente))
            _trasferisci_parser(c, perdente, superstite)
            _trasferisci_sport(c, perdente, superstite)
            _trasferisci_sorgenti_squadre(c, perdente, superstite)
            c.execute('UPDATE users SET origin_profile=NULL WHERE id=?', (perdente,))
    # `ORDER BY name` non e' decorazione: decide CHI vince quando due profili
    # rivendicano la stessa cosa — una chat o un parser. Senza, «il primo» significa
    # «il primo che la tabella restituisce», cioe' l'ordine di inserimento: due database
    # con gli stessi profili creati in ordine diverso davano proprietari diversi.
    # `[REAL_FINDING]` di GPT-5.6 Sol, e lo stesso difetto che avevo gia' corretto per
    # gli slug — non trovato allora perche' avevo cercato il sito e non la classe.
    for profilo, chat_ids, parser_del_profilo in c.execute(
            'SELECT name, chat_ids, parser FROM profiles ORDER BY name').fetchall():
        # Lo slug dell'utente ha la stessa collisione dei parser — due profili che
        # differiscono solo per maiuscole — e la stessa conseguenza: `users.slug` e'
        # UNIQUE, quindi l'INSERT solleverebbe e il servizio non partirebbe. Cercata
        # perche' era la stessa forma, non perche' qualcuno l'avesse segnalata come
        # bloccante (GPT-5.5 l'aveva vista come rischio manuale).
        riga = c.execute('SELECT id FROM users WHERE origin_profile=?', (profilo,)).fetchone()
        if riga is None:
            presi = {r[0] for r in c.execute('SELECT slug FROM users').fetchall()}
            c.execute('INSERT INTO users(origin_profile, slug, first_name, status, is_admin)'
                      ' VALUES (?,?,?,?,?)',
                      (profilo, _slug_libero(profilo.lower(), presi), profilo,
                       'attivo' if profilo == PIERO_PROFILE else 'registrato',
                       1 if profilo == PIERO_PROFILE else 0))
            riga = c.execute('SELECT id FROM users WHERE origin_profile=?', (profilo,)).fetchone()
        if not riga:
            continue
        utente = riga[0]
        # Le chat: da stringa separata da virgole a righe. `message_thread_id` resta
        # NULL — i topic dei gruppi non sono ancora gestiti.
        #
        # Il controllo di esistenza e' ESPLICITO, e prima era un `INSERT OR IGNORE`
        # che non ignorava niente: il vincolo UNIQUE sulla tabella e' sulla coppia
        # `(telegram_chat_id, message_thread_id)`, e con `message_thread_id` NULL non
        # deduplica (vedi `TOPIC_CHAT`). Due profili che elencano la stessa chat
        # inserivano quindi due righe, l'indice sull'espressione qui sotto non si
        # poteva piu' creare, e `migra()` sollevava a ogni richiesta.
        for chat in sorted({x.strip() for x in (chat_ids or '').split(',') if x.strip()}):
            gia = c.execute(f'SELECT id FROM chats WHERE telegram_chat_id=?'
                            f' AND {TOPIC_CHAT}=?', (chat, '')).fetchone()
            if gia is None:
                c.execute('INSERT INTO chats(telegram_chat_id, owner_user_id)'
                          ' VALUES (?,?)', (chat, utente))
        c.execute('UPDATE signals SET user_id=? WHERE profile=? AND user_id IS NULL',
                  (utente, profilo))
        # Il parser che questo profilo usa appartiene a QUESTO utente. Prima tutti i
        # parser senza proprietario finivano al proprietario per difetto, quindi con due
        # profili il parser del secondo passava a Piero — e l'informazione giusta era
        # nella riga che questo ciclo stava gia' leggendo. `[REAL_FINDING]` di GPT-5.6
        # Sol, piu' preciso della segnalazione gemella di Fable 5: non «un giorno
        # assegnera' male» ma «assegna male adesso», e un secondo profilo si crea da
        # `POST /api/profiles`.
        #
        # `AND user_id IS NULL` perche' un'attribuzione gia' fatta non si sovrascrive:
        # due profili che nominano lo stesso parser lo lasciano al primo, come per le
        # chat condivise e per la stessa ragione.
        if parser_del_profilo:
            c.execute('UPDATE parsers SET user_id=? WHERE name=? AND user_id IS NULL',
                      (utente, parser_del_profilo))
    # Utente, `id`, `slug` e `ordine` dei parser: vedi `_completa_colonne_nuove`, che
    # e' la stessa funzione chiamata dal salvataggio di un parser. Il proprietario e'
    # PIERO perche' oggi i parser esistenti sono i suoi, ed e' l'unico utente.
    _completa_colonne_nuove(c, PIERO_PROFILE)
    # I parser di uno stesso utente non possono avere due volte lo stesso slug:
    # e' il vincolo `UNIQUE (user_id, slug)` di #2, che su una tabella esistente
    # non si puo' aggiungere con ALTER e si esprime come indice.
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS parsers_utente_slug'
              ' ON parsers (user_id, slug)')
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS parsers_id ON parsers (id)')
    # `uid` e' UNIQUE come `id`, e per lo stesso motivo: e' un identificatore, e un
    # duplicato lo renderebbe ambiguo proprio dove serve non esserlo (#73).
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS parsers_uid ON parsers (uid)')
    # I link chat→parser, travasati dai profili UNA VOLTA SOLA. Sta DOPO
    # `_completa_colonne_nuove` perche' ha bisogno di `parsers.id`, che e' quella
    # funzione a riempire. Il marcatore evita che il travaso diventi un seme: da
    # qui in avanti i link seguono le scritture dei profili.
    if _una_tantum(c, 'link_dai_profili'):
        _collega_parser_alle_chat(c)
    # `users.origin_profile` e' UNIQUE nel CREATE TABLE, ma un database che riceve la
    # colonna dall'ALTER non ha il vincolo: SQLite non sa aggiungerne con ADD COLUMN.
    # I due percorsi finivano quindi con garanzie diverse, e quello senza garanzia era
    # proprio quello dei database che esistono gia' — dove due righe con lo stesso
    # profilo renderebbero ambiguo il lookup che `origin_profile` esiste per rendere
    # certo. Segnalato in modo indipendente da GPT-5.5 e Claude Fable 5.
    # I NULL multipli restano ammessi, ed e' cio' che serve: chi non viene da un
    # profilo — tutti i prossimi utenti — ha questa colonna vuota.
    #
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS users_origin_profile'
              ' ON users (origin_profile)')
    # `UNIQUE (telegram_chat_id, message_thread_id)` sulla tabella NON deduplica le
    # chat senza topic, e non e' un dettaglio: in SQL `NULL != NULL`, quindi due
    # righe con la stessa chat e `message_thread_id` NULL sono entrambe ammesse.
    # Misurato: la seconda esecuzione della migrazione duplicava tutte le chat, e il
    # test sul duplicato non sollevava. Un indice sull'ESPRESSIONE chiude il buco
    # senza rendere la colonna obbligatoria — «nessun topic» resta NULL, che e' il
    # suo significato, invece di diventare una stringa vuota che sembra un valore.
    #
    # Prima dell'indice, la deduplica di cio' che esiste GIA'. Non e' una cintura in
    # piu': l'indice UNIQUE non si puo' creare su una tabella che contiene duplicati,
    # quindi su un database in quello stato `migra()` sollevava — e `migra()` sta sul
    # percorso di `db()`, cioe' di ogni richiesta, feed di XTrader compreso. L'indice
    # che serve a impedire i duplicati non si poteva creare a causa dei duplicati, e
    # nessun riavvio lo avrebbe cambiato.
    #
    # Sopravvive la riga con l'`id` piu' basso, cioe' il primo che ha dichiarato la
    # chat. Le associazioni che puntavano alle altre vengono RIPUNTATE prima della
    # cancellazione: senza, resterebbe una riga di `parser_chats` che riferisce un
    # `id` inesistente e il parser smetterebbe di ricevere da quella chat in silenzio.
    # Il ripuntamento ha qualcosa da salvare: `_attacca_link_del_profilo` scrive in
    # `parser_chats` (la conversione dei profili legacy), e il webhook ci legge il
    # dispatch per-utente. Fino al 01/09/2026 qui c'era scritto «oggi nessun codice
    # scrive in `parser_chats`»: era vero quando il commento fu scritto, e falso da
    # quando il dispatch e' nato — a una funzione di distanza nello stesso file. Un
    # commento che afferma il falso non e' innocuo: e' la forma con cui in questo
    # repository sono sopravvissuti il BOM mancante e il «verificato byte per byte»
    # che nessuno aveva verificato. Se un domani la scrittura sparisse davvero, e'
    # questa riga a dover cambiare per prima.
    for chat, topic in c.execute(f'SELECT {CHIAVE_CHAT} FROM chats'
                                 f' GROUP BY {CHIAVE_CHAT} HAVING COUNT(*) > 1').fetchall():
        identificativi = [r[0] for r in c.execute(
            f'SELECT id FROM chats WHERE telegram_chat_id=? AND {TOPIC_CHAT}=?'
            ' ORDER BY id', (chat, topic)).fetchall()]
        vincente, perdenti = identificativi[0], identificativi[1:]
        for perdente in perdenti:
            # `OR IGNORE` piu' la DELETE, e non una UPDATE nuda: `parser_chats` ha
            # `PRIMARY KEY (parser_id, chat_id)`, quindi se un parser era associato a
            # ENTRAMBE le righe duplicate lo spostamento creerebbe una riga che esiste
            # gia' e solleverebbe — un altro modo di rendere `migra()` impossibile da
            # attraversare, dentro la correzione che ne chiudeva un altro.
            # `[REAL_FINDING]` di GPT-5.6 Sol al gate finale della PR #22.
            #
            # Niente va perso: `OR IGNORE` sposta cio' che puo' spostarsi, la DELETE
            # toglie le righe rimaste indietro proprio perche' la loro destinazione
            # c'era gia'. L'associazione `(parser, vincente)` esiste in entrambi i casi.
            # Si spostano SOLO le associazioni dei parser che appartengono al
            # proprietario della chat sopravvissuta. Prima si spostavano tutte, e con la
            # stessa chat rivendicata da due utenti — la riga di ALFA sopravvive, quella
            # di BETA viene scartata — un parser di BETA finiva agganciato alla chat di
            # ALFA. Misurato: `parser 1 (utente 2) -> chat 1 (utente 1)`. Nel PR sul
            # dispatch quel legame significa i segnali di una chat consegnati al feed di
            # un altro utente. `[REAL_FINDING]` di GPT-5.6 Sol, il piu' grave dei suoi.
            #
            # La correzione non e' spostare meglio, e' NON spostare: la chat appartiene a
            # un solo utente, quindi l'associazione di un parser altrui e' illegittima e
            # la `DELETE` sotto la porta via insieme al resto. `IS` e non `=` perche' un
            # proprietario NULL deve combaciare con un `user_id` NULL, e con `=` no.
            c.execute('UPDATE OR IGNORE parser_chats SET chat_id=? WHERE chat_id=?'
                      ' AND parser_id IN (SELECT id FROM parsers WHERE user_id IS'
                      ' (SELECT owner_user_id FROM chats WHERE id=?))',
                      (vincente, perdente, vincente))
            c.execute('DELETE FROM parser_chats WHERE chat_id=?', (perdente,))
            c.execute('DELETE FROM chats WHERE id=?', (perdente,))
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS chats_chat_topic'
              f' ON chats ({CHIAVE_CHAT})')


# Un solo backup per volta nel processo. La copia materializza l'intero DB in memoria
# (snapshot `:memory:` + byte di `serialize()`): download concorrenti ne terrebbero N
# copie insieme e su Railway il picco di RAM potrebbe abbattere il servizio. Il
# lucchetto serializza le copie — operazione rara, amministratore unico — tenendo il
# picco a una copia sola. Segnalato da GPT-5.6 Sol al gate finale (#56).
#
# E' un `threading.Lock`, non un `asyncio.Lock`, di proposito: la copia gira in un
# thread via `asyncio.to_thread`, quindi il lucchetto va preso li' (un `asyncio.Lock`
# si legherebbe per giunta a un solo event loop, rompendo i test che ne creano uno
# nuovo per chiamata). Un thread in attesa non blocca il loop: la coroutine chiamante
# resta parcheggiata sul future di `to_thread` e il servizio continua a rispondere.
_lucchetto_backup = threading.Lock()

# Un solo INVIO del backup al canale per volta (#56 pezzo 3). `_lucchetto_backup` serializza la
# COPIA, ma non l'orchestrazione intera: due invii simultanei (il bottone admin e il giro del cron
# che si sovrappongono) copierebbero uno alla volta ma poi caricherebbero DUE documenti sul canale
# e scriverebbero DUE righe di audit. Questo lucchetto, preso in modo NON bloccante, fa passare il
# primo e fa rispondere «gia' in corso» al secondo, senza consegne ne' audit doppi. Segnalato da
# GPT-5.5 sulla PR #101.
_lucchetto_invio_backup = threading.Lock()


def _serializza_db():
    """I byte di `signals.db` via l'API di backup di SQLite — senza politica di accesso.

    Presa con `Connection.backup`, NON con una copia grezza del file: mentre il
    servizio scrive, `cp signals.db` puo' catturare il file a meta' di una transazione,
    e un backup corrotto e' peggio di nessun backup — lo si scopre solo il giorno in
    cui serve. L'API copia pagina per pagina prendendo il lock giusto, quindi lo
    snapshot e' coerente anche sotto scrittura. `serialize()` restituisce i byte del
    formato su disco (Python 3.11+).
    """
    sorgente = sqlite3.connect(DB_PATH)
    sorgente.execute('PRAGMA busy_timeout = 5000')
    try:
        istantanea = sqlite3.connect(':memory:')
        try:
            sorgente.backup(istantanea)
            return istantanea.serialize()
        finally:
            istantanea.close()
    finally:
        sorgente.close()


def copia_backup_db():
    """Copia CONSISTENTE del database, come byte del file `.db`, UNA per volta (#56).

    Il `_serializza_db` sotto lucchetto: la copia e' pesante in memoria, e il lucchetto
    la tiene a una sola alla volta (vedi il commento su `_lucchetto_backup`).

    Sincrona (apre connessioni sqlite e materializza tutto il file in memoria): i
    chiamanti `async` la passano a `asyncio.to_thread`, come per ogni I/O bloccante che
    altrimenti fermerebbe il loop — feed compreso — ed e' li' che il lucchetto va preso.
    """
    with _lucchetto_backup:
        return _serializza_db()


def copia_backup_su_file(percorso):
    """Copia CONSISTENTE del database su un FILE, non in memoria (#56 pezzo 3).

    `scarica_backup` puo' materializzare i byte (`copia_backup_db`) perche' li passa subito a una
    risposta HTTP. L'invio notturno al canale invece li carica su Telegram con `sendDocument`, e
    tenere in RAM l'intero `.db` mentre lo si carica raddoppierebbe la memoria sul container
    condiviso — il rischio OOM sollevato al gate del pezzo 1. Qui la copia va su un file temporaneo
    con l'API di backup di SQLite (coerente sotto scrittura, come `_serializza_db`, mai un `cp`
    grezzo a caldo), che poi si manda in streaming e si cancella.

    Sincrona (I/O su disco e lock sqlite): i chiamanti `async` la passano a `asyncio.to_thread`.
    Sotto lo stesso `_lucchetto_backup` di `copia_backup_db`: una copia pesante alla volta.
    """
    with _lucchetto_backup:
        sorgente = sqlite3.connect(DB_PATH)
        sorgente.execute('PRAGMA busy_timeout = 5000')
        try:
            destinazione = sqlite3.connect(percorso)
            try:
                sorgente.backup(destinazione)
            finally:
                destinazione.close()
        finally:
            sorgente.close()


def leggi_chat_telegram(chat_id, bot_token=None):
    """`getChat` di un canale: `(ok, dati_o_motivo)`. Non solleva, senza token nel motivo.

    Serve a RIVERIFICARE la privacy prima di ogni invio: la cattura garantisce un canale privato,
    ma un canale reso pubblico DOPO la conferma esporrebbe i dati dei clienti, e la sola assenza di
    `username` nel vecchio `my_chat_member` non lo direbbe. Come `invia_messaggio_telegram`, il
    motivo e' il TIPO dell'eccezione — mai il testo, che porterebbe l'URL col token del bot.
    """
    import urllib.error
    import urllib.parse
    import urllib.request
    token = bot_token or BOT_TOKEN
    if not token:
        return False, 'bot non configurato'
    if not chat_id:
        return False, 'destinatario sconosciuto'
    query = urllib.parse.urlencode({'chat_id': chat_id})
    try:
        with urllib.request.urlopen(  # noqa: S310
                f'https://api.telegram.org/bot{token}/getChat?{query}', timeout=10) as risposta:
            esito = json.loads(risposta.read().decode('utf-8'))
    except Exception as e:
        return False, f'getChat fallito ({type(e).__name__})'
    if not esito.get('ok'):
        return False, 'Telegram ha rifiutato getChat'
    return True, esito.get('result') or {}


def invia_documento_telegram(chat_id, percorso, nome_file, didascalia=None, bot_token=None):
    """Manda un FILE al canale via `sendDocument`. `(riuscito, motivo)`, non solleva, senza token.

    Stessa forma di `invia_messaggio_telegram`: il `motivo` e' il TIPO dell'eccezione (mai il testo,
    che porterebbe l'URL col token del bot), e `ok: false` di Telegram e' un rifiuto anche con HTTP
    200. Il corpo multipart si costruisce su un FILE temporaneo con `copyfileobj` (il `.db` passa a
    pezzi, non tutto in RAM) e si manda in STREAMING passando il file aperto a `urlopen` con un
    `Content-Length` preciso: e' il senso del pezzo 3 — non tenere l'intero database in memoria
    durante l'upload. `nome_file` e' generato dal servizio (mai input dell'utente), quindi non puo'
    iniettare intestazioni nel multipart.
    """
    import shutil
    import tempfile
    import urllib.error
    import urllib.request
    token = bot_token or BOT_TOKEN
    if not token:
        return False, 'bot non configurato'
    if not chat_id:
        return False, 'destinatario sconosciuto'
    confine = '----betrelay' + secrets.token_hex(16)

    def campo(nome, valore):
        return (f'--{confine}\r\nContent-Disposition: form-data; name="{nome}"\r\n\r\n'
                f'{valore}\r\n').encode('utf-8')

    preambolo = campo('chat_id', str(chat_id))
    if didascalia:
        preambolo += campo('caption', didascalia)
    preambolo += (
        f'--{confine}\r\nContent-Disposition: form-data; name="document"; '
        f'filename="{nome_file}"\r\nContent-Type: application/octet-stream\r\n\r\n'
    ).encode('utf-8')
    epilogo = f'\r\n--{confine}--\r\n'.encode('utf-8')
    fd, corpo_path = tempfile.mkstemp(prefix='betrelay-mp-')
    os.close(fd)
    try:
        with open(corpo_path, 'wb') as out:
            out.write(preambolo)
            with open(percorso, 'rb') as src:
                shutil.copyfileobj(src, out)   # il .db passa a pezzi, non tutto in RAM
            out.write(epilogo)
        dimensione = os.path.getsize(corpo_path)
        with open(corpo_path, 'rb') as corpo:
            richiesta = urllib.request.Request(
                f'https://api.telegram.org/bot{token}/sendDocument', data=corpo, method='POST',
                headers={'Content-Type': f'multipart/form-data; boundary={confine}',
                         'Content-Length': str(dimensione)})
            with urllib.request.urlopen(richiesta, timeout=30) as risposta:  # noqa: S310
                esito = json.loads(risposta.read().decode('utf-8'))
    except Exception as e:
        return False, f'invio fallito ({type(e).__name__})'
    finally:
        try:
            os.unlink(corpo_path)
        except OSError:
            pass
    if not esito.get('ok'):
        return False, 'Telegram ha rifiutato il documento'
    return True, None


def db():
    c = sqlite3.connect(DB_PATH)
    # `busy_timeout` prima di ogni altra cosa. Il lock qui sotto e' PER PROCESSO: con
    # piu' worker due processi eseguono la migrazione sullo stesso file, e senza
    # timeout il secondo riceve subito «database is locked» invece di aspettare — un
    # deploy che parte rotto. Segnalato da Claude Fable 5 sulla PR #22.
    c.execute('PRAGMA busy_timeout = 5000')
    if DB_PATH not in _PERCORSI_MIGRATI:
        with _LOCK_MIGRAZIONE:
            # Riletto DENTRO il lock: due richieste possono arrivare qui insieme, e
            # senza il secondo controllo entrambe migrerebbero.
            if DB_PATH not in _PERCORSI_MIGRATI:
                try:
                    migra(c)
                except Exception:
                    # Chiudere e rilanciare. Senza, ogni richiesta ritenta, sbaglia, e
                    # lascia dietro un'altra connessione: un guasto che PEGGIORA da
                    # solo mentre il traffico continua. Il percorso non viene marcato
                    # migrato, quindi il tentativo successivo riprova — che e' giusto,
                    # perche' la causa piu' probabile e' un lock momentaneo.
                    c.close()
                    raise
                _PERCORSI_MIGRATI.add(DB_PATH)
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
    # Dal #35 (pezzo 1) il feed puo' portare N segnali vivi: niente piu' tetto a
    # una riga — ogni data line passa comunque, UNA PER UNA, dal controllo qui
    # sotto (14 campi quotati, numerici localizzati). Il gemello JS `verifyCsv`
    # segue la stessa regola (parita' in test_engine_contract.py).
    for n, line in enumerate(lines[1:], start=2):
        if not _ROW.match(line):
            raise ValueError('riga %d non ha %d campi tutti fra virgolette' % (n, len(HEADERS)))
        # Il formato dei campi NUMERICI e' parte del contratto (#40): per
        # XTrader il separatore decimale e' la virgola, e un punto qui e' una
        # localizzazione mancata — il caso pericoloso non e' «non funziona»,
        # e' `"1.85"` letto come migliaia. Senza questo controllo la decisione
        # della #40 varrebbe quanto valeva la riga «senza BOM»: un'affermazione
        # mai misurata. Il parsing passa dal modulo csv, non da uno split:
        # virgole e virgolette DENTRO i valori non devono spostare gli indici.
        campi = next(csv.reader(io.StringIO(line)))
        for colonna in INTERVALLI_NUMERICI:
            valore = campi[HEADERS.index(colonna)]
            if valore and not _NUMERO_FEED.match(valore):
                raise ValueError(
                    '%s nel feed non e\' nella forma localizzata del contratto '
                    '(virgola decimale): %r' % (colonna, valore))
        # Niente emoji in NESSUNA colonna (#42): XTrader marcherebbe il
        # segnale non valido, senza errore di ritorno. Regola di contratto,
        # vincolata qui perche' «dichiarato sano» significhi qualcosa.
        for colonna in HEADERS:
            if _EMOJI.search(campi[HEADERS.index(colonna)]):
                raise ValueError(
                    '%s nel feed contiene un\'emoji: XTrader marcherebbe il '
                    'segnale non valido, senza errore di ritorno' % colonna)
    return text


def make_csv(row):
    return csv_text(HEADERS, row)


def store_signal(c, csv_text_value, parser, profile=PIERO_PROFILE, utente=None):
    # One message produces one row; the next message only replaces THIS OWNER's row.
    # Fail closed: a CSV that does not pass verification is never stored.
    #
    # Due modi di nominare il proprietario, UNA riga: i chiamanti legacy passano
    # `profile` e l'utente si risolve dal ponte `origin_profile`; il dispatch
    # multi-parser passa `utente` e il profilo si risolve al contrario, cosi' gli
    # alias legacy continuano a leggere. Con entrambi, comandano quelli passati.
    #
    # La chiave del segnale e' l'UTENTE (Issue #2: feed, token e timer stanno
    # sull'utente), risolto dal profilo via `origin_profile` — lo stesso ponte di
    # `accesso_bloccato_del_profilo`. La colonna `profile` continua a essere
    # scritta perche' gli alias legacy (`/xtrader.csv`, `/profiles/{p}.csv`)
    # leggono per profilo: due chiavi sulla stessa riga, non due righe.
    #
    # Il DELETE copre ENTRAMBE le chiavi. Solo `user_id` lascerebbe viva una riga
    # legacy dello stesso profilo scritta prima di questa versione, e il percorso
    # vecchio — che legge `ORDER BY id DESC` — la scavalcherebbe da solo, ma la
    # pulizia del TTL per profilo non la troverebbe piu' sua; solo `profile`
    # lascerebbe viva la riga di un ALTRO profilo dello stesso utente, cioe' due
    # segnali vivi per lo stesso feed utente: un segnale stantio accanto a quello
    # nuovo, che e' esattamente cio' che «una riga viva per utente» vieta.
    # Multi-riga (#35 pezzo 1): un messaggio puo' produrre N righe CSV, una per
    # mercato/selezione. `csv_text_value` accetta la LISTA di documenti (uno per
    # riga) accanto alla stringa storica; la verifica gira su TUTTI i documenti
    # PRIMA di toccare il database — meta' segnale nel feed (2 mercati su 3)
    # e' peggio di nessun segnale, il cliente crederebbe di aver piazzato tutto.
    documenti = ([csv_text_value] if isinstance(csv_text_value, str)
                 else list(csv_text_value))
    if not documenti:
        raise ValueError('nessun documento CSV da scrivere')
    for documento in documenti:
        verify_csv(documento)
        # UN record = UNA riga (CodeRabbit, PR #68): `verify_csv` accetta anche
        # i documenti COMPOSTI — quelli che il feed serve — ma come ingresso di
        # scrittura ogni documento porta esattamente una data line, o le sue
        # righe condividerebbero un record e una scadenza sola e non potrebbero
        # morire una per una. Zero righe (sola intestazione) non e' un segnale.
        righe_dati = documento[len(CSV_BOM):].count('\r\n') - 1
        if righe_dati != 1:
            raise ValueError(
                'un documento per riga: questo ne porta %d' % righe_dati)
    if utente is None and profile is not None:
        riga = c.execute('SELECT id FROM users WHERE origin_profile=?', (profile,)).fetchone()
        utente = riga[0] if riga else None
    elif utente is not None and profile is None:
        # Il ponte al contrario: il dispatch per-utente conosce l'utente, e se
        # quell'utente viene da un profilo la colonna legacy va scritta comunque —
        # e' quella che `/xtrader.csv` e `/profiles/{p}.csv` leggono.
        riga = c.execute('SELECT origin_profile FROM users WHERE id=?', (utente,)).fetchone()
        profile = riga[0] if riga else None
    if utente is None:
        # Profilo senza utente collegato: la chiave utente non esiste, resta il
        # comportamento storico. Non e' un errore — un profilo puo' nascere prima
        # del suo utente — e inventare una riga in `users` da qui violerebbe la
        # regola per cui l'identita' non si crea su un percorso di scrittura segnali.
        c.execute('DELETE FROM signals WHERE profile=?', (profile,))
    elif profile is None:
        c.execute('DELETE FROM signals WHERE user_id=?', (utente,))
    else:
        c.execute('DELETE FROM signals WHERE user_id=? OR profile=?', (utente, profile))
    scadenza = int(time.time()) + 90
    for documento in documenti:
        c.execute('INSERT INTO signals(csv,parser,profile,user_id,expires_at)'
                  ' VALUES (?,?,?,?,?)',
                  (documento, parser, profile, utente, scadenza))


def componi_feed(documenti):
    """UN documento CSV dalle righe vive del feed (#35 pezzo 1), byte-exact.

    Ogni riga di `signals` e' un documento completo (BOM + intestazione + una
    data line), verificato da `store_signal` alla scrittura. Qui si compone il
    feed servito: il PRIMO documento intero, dei successivi solo cio' che segue
    la prima CRLF (la data line) — il BOM e l'intestazione devono comparire una
    volta sola, in testa: XTrader leggerebbe un header ripetuto come una riga
    dati malformata. Lista vuota = feed vuoto, la sola intestazione di sempre.
    Fonte unica (regola 3): la usano entrambe le rotte del feed.
    """
    validi = [d for d in documenti if d]
    if not validi:
        return empty_csv()
    pezzi = [validi[0]]
    for documento in validi[1:]:
        _, _, coda = documento.partition('\r\n')
        if coda:
            pezzi.append(coda)
    return ''.join(pezzi)


def parse_message(message, cfg):
    """Il segnale letto dal messaggio, o `None` se non e' riconoscibile.

    `None` significa «non riconosciuto» e non e' un errore: chi chiama risponde 200
    con `parser_no_match` sul webhook e 422 sulla rotta di prova. Questa funzione
    non solleva su un messaggio storto, e la ragione e' che il suo chiamante
    principale e' pubblico e Telegram RITENTA le consegne fallite.

    *Storia, perche' non si ripeta.* Qui c'era `event.splitlines()[0]`, e su un
    evento vuoto `''.splitlines()` e' `[]`: `IndexError`, quindi 500, quindi
    Telegram che riconsegna lo stesso messaggio e solleva di nuovo — un segnale
    perso e i log pieni di tracce identiche. Quella riga non serviva a niente:
    `line` viene da `message.splitlines()`, quindi non contiene interruzioni e
    riestrarne la prima era l'identita-. Non faceva nulla nel caso normale e faceva
    cadere il servizio nel caso vuoto.

    Il caso raggiungibile non e' il marcatore isolato ma il marcatore in **coda**
    alla riga (`SQUADRA-A v SQUADRA-B 🆚`): un canale che scrive le squadre prima
    del marcatore faceva cadere il webhook al primo messaggio.
    """
    return _giudica_legacy(message, cfg)[0]


def _giudica_legacy(message, cfg):
    """`(parsed, motivi)` per il percorso legacy, giudicato come il motore.

    Costruisce la riga dai campi COSTANTI del parser + l'evento estratto dal
    messaggio, poi la passa a `_giudica_riga` — la STESSA del motore configurabile
    (regola 3). Cosi' il legacy eredita: la localizzazione decimale (#40), le
    guardie sui valori (#39) e la diagnosi dell'emoji nell'evento (#42). Prima
    scriveva la riga grezza e `verify_csv` la scartava in SILENZIO se l'handicap
    aveva il punto o l'evento un'emoji (audit #81, C1/C2): il segnale spariva senza
    una riga di causa, perche' il ramo legacy restituiva sempre `motivi=[]`.

    Quando il giudizio trova un motivo non c'e' segnale (`None`), ma il PERCHE'
    viaggia nel secondo valore, che il dispatch scrive in `message_logs` — come per
    il motore. Su un messaggio valido i byte non cambiano: `_giudica_riga` localizza
    solo i numerici e non tocca un valore gia' in forma corretta, quindi il feed di
    PIERO resta identico.
    """
    if cfg['header'].lower() not in message.lower():
        return None, []
    line = next((x.strip() for x in message.splitlines() if '🆚' in x), '')
    if not line:
        return None, []
    event = line.split('🆚', 1)[1].strip()
    # Nessun evento dopo il marcatore: non si inventa un nome squadra vuoto.
    if not event:
        return None, []
    # The final " v " is the separator; earlier occurrences remain in a team name.
    ms = list(re.finditer(r'\s+v\s+', event, flags=re.I))
    if ms:
        s = ms[-1]
        event = event[:s.start()].strip() + ' - ' + event[s.end():].strip()
    row = ['XTrader', '', event, '', cfg['market_name'], cfg['market_type'], '',
           cfg['selection_name'], cfg['handicap'], '', '', '', cfg['bet_type'], '']
    giudizio = _giudica_riga(row, True)
    motivi = list(giudizio['scarti'])
    motivi += ['%s: colonna obbligatoria vuota nel segnale' % c
               for c in giudizio['missing']]
    if motivi:
        return None, motivi
    return {'event': event, 'csv': make_csv(giudizio['row'])}, []


# ============================================================================
# MOTORE DI PARSING CONFIGURABILE — gemello Python di web/engine.js
# ============================================================================
#
# Un parser e' descritto da una config JSON: una condizione di riconoscimento
# (`match`) e una regola per ciascuna delle 14 colonne (`columns`). Questo e' il
# motore che ESEGUE quella config, e deve produrre gli STESSI output di
# `web/engine.js` — sono due implementazioni dello stesso contratto (regola 3 di
# `CLAUDE.md`), e `tests/engine/test_engine_contract.py` fa girare entrambi sugli
# stessi casi e pretende che coincidano.
#
# Perche' esiste: oggi il parser vive nel CODICE (`parse_message` qui sopra, con
# header e mappatura cablati). Questo motore lo sostituira' con una config che
# l'utente compone dalla web app, senza toccare il codice. Per ora e' solo
# aggiunto — nessuna rotta lo usa ancora; il collegamento e' il PR successivo, e
# PIERO verra' seminato con una config byte-identica al comportamento cablato.
#
# Le colonne sono `HEADERS`, non una lista nuova: una seconda copia si
# allineerebbe da sola a un ordine sbagliato.
#
# **Limite dichiarato, perche' non sia una sorpresa:** il motore JS e quello
# Python NON possono essere identici su ogni input immaginabile — i due motori di
# espressioni regolari differiscono (gruppi unicode, classi, flag) e gli insiemi
# di spazio-bianco di `trim`/`strip` differiscono ai bordi. Il confronto non e'
# una dimostrazione universale: e' un guardiano su un insieme di casi, ed e' li'
# che va aggiunto ogni formato reale nuovo. Per i sorgenti costante/riga, che sono
# quelli del parser di produzione, la corrispondenza e' esatta.

# Colonne senza le quali la riga sarebbe pericolosamente incompleta per XTrader.
# QUATTRO, decise dal proprietario il 13/08/2026 (Issue #2, riconfermate su #25):
# l'evento, il TIPO di mercato su cui XTrader decide, la selezione, e se puntare o
# bancare. `Provider` non e' obbligatoria (e' sempre la costante "XTrader", quindi
# pretenderla non protegge da nulla); `Price` non lo e' (la quota la mette XTrader);
# `MarketName` non lo e' (e' l'etichetta, non il codice — l'obbligatoria e'
# `MarketType`).
#
# La stessa lista di `REQUIRED_COLUMNS` in `web/engine.js`, cambiata nello stesso
# momento: `tests/engine/test_engine_contract.py` fa girare i due motori sugli
# stessi casi e le due liste devono coincidere, o un utente vedrebbe «completo» nel
# browser e feed vuoto in produzione.
COLONNE_OBBLIGATORIE = ['EventName', 'MarketType', 'SelectionName', 'BetType']

# Le colonne che XTrader legge come NUMERI, con l'intervallo ammesso. Decisi nella
# Issue #39 e non copiati dal Bridge:
#
# - `Price`/`MinPrice`/`MaxPrice` in `1.01–1000` e' la scala reale delle quote
#   Betfair, non una convenzione nostra: sotto 1.01 non esiste quota, sopra 1000
#   non esiste mercato, quindi fuori da li' non c'e' informazione da salvare;
# - `|Handicap| <= 1000` e' un inviluppo volutamente largo: deve coprire ogni linea
#   reale, comprese quelle grandi dei mercati a punti, e intercettare solo il
#   patologico;
# - `Points` in `0–1000`: e' il MOLTIPLICATORE dello stake di XTrader (risposta del
#   proprietario: «points 2 e su XTrader 1 significa 2 euro»), quindi il tetto non
#   chiede «e' troppo?» — quanto punta il cliente non ci riguarda — ma «puo' averlo
#   scritto una persona?». Il `100` del Bridge NON si copia: scarterebbe segnali
#   veri di chi usa un moltiplicatore alto.
#
# `Points` e' anche la piu' pericolosa delle cinque, ed e' il motivo per cui la
# Issue esiste: un valore storto sulle altre rende la scommessa impossibile
# (`Price` assurdo non si abbina), qui la moltiplica.
INTERVALLI_NUMERICI = {
    'Price': (1.01, 1000.0),
    'MinPrice': (1.01, 1000.0),
    'MaxPrice': (1.01, 1000.0),
    'Handicap': (-1000.0, 1000.0),
    'Points': (0.0, 1000.0),
}

# Cifre ASCII, un solo separatore decimale, segno facoltativo. `float()` di Python
# leggerebbe anche `١٩` (arabo-indiane) e `１９` (fullwidth), ma XTrader legge solo
# ASCII: un valore cosi' passerebbe i tetti e finirebbe verbatim nel CSV, dove il
# consumatore non lo capisce — la colonna sembra piena e non lo e'.
#
# `[0-9]` scritto per esteso e NON `\d`, che in Python matcha le cifre Unicode: con
# `\d` questa riga sarebbe stata una guardia che non guarda. Misurato scrivendola
# sbagliata: `motivo_valore_numerico('Price', '١٩')` restituiva `None`, cioe'
# «accettabile», ed era esattamente il caso che la regola esiste per fermare.
# Gli spazi uniformi fra i due motori: classe esplicita, gemella di
# `SPAZI_CLASSE` in `web/engine.js`. Vedi `motivo_valore_numerico` per la
# tabella delle divergenze fra i default dei due linguaggi. Non serve solo al
# valore citato nei motivi: e' la classe su cui corrono il VERDETTO numerico,
# l'emptiness delle obbligatorie e la trasformazione `trim` — ovunque i default
# di `strip()`/`trim()` farebbero divergere i due motori.
_SPAZI_CLASSE = ('[\t\n\v\f\r \x1c-\x1f\x85\u00a0\u1680\u2000-\u200a'
                 '\u2028\u2029\u202f\u205f\u3000\ufeff]')
SPAZI_UNIFORMI = re.compile(_SPAZI_CLASSE + '+')
BORDI_UNIFORMI = re.compile('^' + _SPAZI_CLASSE + '+|' + _SPAZI_CLASSE + '+$')


def _piatto(testo):
    """Spazi uniformi → ' ', bordi tolti. Gemella di `piatto` in engine.js.

    E' la normalizzazione che PRECEDE ogni verdetto dei motori: `strip()` di
    Python non toglie `\\ufeff`, `trim()` di JS non toglie `\\x1c-\\x1f`/`\\x85`,
    e un verdetto preso sul testo grezzo diverge fra browser e produzione —
    anteprima «completa» e feed vuoto. [REAL_FINDING] di Claude Fable 5 e
    GPT-5.6 Sol al gate finale della PR #47.
    """
    return SPAZI_UNIFORMI.sub(' ', testo).strip()

_NUMERO_ASCII = re.compile(r'^[+-]?(?:[0-9]+(?:[.,][0-9]*)?|[.,][0-9]+)$')

# Il separatore decimale del feed e' una proprieta' del CONTRATTO, non una
# scelta dell'utente (#40). Per XTrader si scrive la VIRGOLA: e' misurato tre
# volte — l'esempio della guida ufficiale (p. 169, `"1,23"`, unico campo
# numerico valorizzato in 315 pagine), il Bridge che gira in produzione con
# XTrader italiano, e la conferma del proprietario («noi usiamo IT»). La
# tabella ha UNA voce oggi: EN → punto ed ES → virgola entreranno con la
# famiglia Betting Toolkit (#37), e saranno una riga, non un refactor — e' la
# ragione per cui il confine si costruisce adesso. Gemella di
# `DECIMAL_SEPARATORS` in `web/engine.js`.
SEPARATORI_DECIMALI = {'IT': ','}
LINGUA_FEED = 'IT'
SEPARATORE_DECIMALE = SEPARATORI_DECIMALI[LINGUA_FEED]

# La forma che un campo numerico NON vuoto deve avere nel feed: la stessa
# grammatica della guardia (`_NUMERO_ASCII`), col SOLO separatore localizzato.
# Un punto qui dentro e' una localizzazione mancata, e in contesto italiano
# `"1.85"` rischia la lettura come migliaia: quota 185, dentro i tetti della
# #39, invisibile a ogni guardia a valle. DERIVATA dal separatore, non
# ricopiata: quando Betting Toolkit aggiungera' una lingua, verificatore e
# confine di scrittura non potranno divergere (suggerito da GPT-5.5 sulla
# PR #48). Gemella di `FEED_NUMBER` in JS.
_NUMERO_FEED = re.compile(
    r'^[+-]?(?:[0-9]+(?:%(s)s[0-9]*)?|%(s)s[0-9]+)$'
    % {'s': re.escape(SEPARATORE_DECIMALE)})

# Niente emoji nei VALORI del feed (#42): «solo testo. Emoji non li accetta
# XTrader, lo marcherebbe non valido come segnale» (il proprietario) — e come
# tutto in XTrader senza errore di ritorno, solo l'icona rossa che l'utente
# deve notare. Le emoji stanno IN ENTRATA (i marcatori dei parser), mai in
# uscita. Classe ESPLICITA, gemella di `EMOJI` in web/engine.js: i blocchi dei
# simboli (misc technical per l'orologio, misc symbols e dingbats per la
# spunta, frecce e stelle, il piano astrale dei simboli) piu' i caratteri di
# composizione (ZWJ, variation selector) che da soli tradiscono un'emoji
# spezzata dal taglio di una regola.
# Dentro c'e' anche il keycap combinante U+20E3: esiste SOLO per le
# sequenze emoji, e nella forma minimamente qualificata ('1' + U+20E3,
# senza FE0F) era l'unico modo di portare un'emoji nel feed senza toccare
# la classe ([REAL_FINDING] di GPT-5.6 Sol, PR #49). Fuori restano
# DELIBERATAMENTE i simboli text-default ((c), (r), TM, !!, le frecce): da
# soli sono testo, e la loro presentazione emoji richiede FE0F, che
# intercettiamo gia'.
_EMOJI = re.compile('[\u200d\u20e3\u2300-\u23ff\u2600-\u27bf'
                    '\u2b00-\u2bff\ufe0f\U0001F000-\U0001FAFF]')


def motivo_valore_numerico(colonna, valore):
    """`None` se il valore e' accettabile per quella colonna, altrimenti il MOTIVO.

    Predicato unico (regola 3): lo chiamano il motore, il verificatore e — quando
    esistera' il multi-riga (#35) — anche le righe di override. Nel Bridge erano
    due controlli scritti a mano e identici, e aggiungere il tetto a uno solo aveva
    lasciato aperto l'altro percorso.

    L'ordine dei controlli e' parte della decisione (#39), non un dettaglio:

    - **vuoto → ammesso.** E' il caso normale di `Price`: la quota la mette XTrader
      dal proprio book. E' anche il test che protegge dall'eccesso di zelo;
    - **cifre non ASCII → scartato**, prima di `float()`, che le leggerebbe;
    - **non convertibile → scartato**, e se ci sono PIU' separatori il motivo nomina
      il separatore delle migliaia: `1.000.000` e' il caso reale da cui nasce la
      Issue, e mandare l'utente su «controlla la regola» sarebbe la pista sbagliata;
    - **non finito → scartato**, e prima dei tetti: `float('9'*400)` e' `inf`, e
      l'infinito supera i confronti nel verso sbagliato — un `Points > 0` senza
      tetto superiore direbbe «valido» a un moltiplicatore infinito (bug misurato
      nel Bridge);
    - **fuori intervallo → scartato**, con il tetto nel messaggio e il separatore
      decimale come causa probabile.

    Il motivo deve dire **cosa fare**, non solo cosa non va: e' il vincolo della
    #25, e la ragione per cui nel Bridge «fuori intervallo» annunciato come «non
    numerico» mandava su due piste entrambe sbagliate.
    """
    intervallo = INTERVALLI_NUMERICI.get(colonna)
    if intervallo is None:
        return None
    # Niente `strip()` qui: il verdetto corre sul valore NORMALIZZATO dalla
    # classe condivisa (`piano`, sotto), non sul testo grezzo. Lo `strip()` di
    # Python non toglie `\ufeff` e il `trim()` di JS non toglie `\x1c-\x1f`:
    # `'\ufeff2'` era una quota valida nel browser e «non un numero» in
    # produzione — anteprima verde, feed vuoto. [REAL_FINDING] di Claude
    # Fable 5 e GPT-5.6 Sol al gate finale della PR #47.
    testo = _testo_canonico(valore)
    # Il valore citato nel motivo si taglia: finisce in `message_logs` e nella
    # UI, e un'estrazione sbagliata puo' portarsi dietro una riga intera — il
    # caso dell'infinito ne cita 400 cifre. Il taglio e' identico in JS, o i due
    # motori tornerebbero a scrivere motivi diversi. Rischio segnalato da GPT-5.5.
    # Gli a capo e i caratteri di controllo diventano spazi PRIMA del taglio: il
    # valore estratto puo' contenere una riga intera, e un motivo multilinea
    # spezzerebbe la riga di log e la tabella della UI. Segnalato da GPT-5.5.
    #
    # La classe e' ESPLICITA e non `str.split()`, perche' i default dei due
    # linguaggi non coincidono — misurato, e le divergenze vanno in DUE versi:
    #
    #     carattere        Python `split()`   JS `/\s+/`
    #     \x1c-\x1f, \x85    normalizza        NO
    #     \ufeff (BOM)       NO                normalizza
    #
    # Il BOM e' il caso che conta di piu' qui dentro: e' un carattere portante
    # del contratto CSV, e i due motori lo trattavano al contrario. Segnalato da
    # Claude Fable 5 sulla PR #47, che ne aveva visto due su tre.
    piano = _piatto(testo)
    if not piano:
        return None
    citato = piano if len(piano) <= 60 else _taglia_codepoint(piano, 60) + '…'
    if not _NUMERO_ASCII.match(piano):
        if sum(piano.count(s) for s in '.,') > 1:
            return (f'{colonna}: «{citato}» non e\' un numero. Probabile causa: il '
                    'separatore delle migliaia — controlla le trasformazioni della regola.')
        return (f'{colonna}: «{citato}» non e\' un numero valido. XTrader legge solo '
                'cifre ASCII: controlla la regola, sta leggendo la parte sbagliata '
                'del messaggio.')
    try:
        numero = float(piano.replace(',', '.'))
    except ValueError:
        return f'{colonna}: «{citato}» non e\' un numero.'
    if not math.isfinite(numero):
        return (f'{colonna}: «{citato}» non e\' un numero finito. Il valore estratto e\' '
                'troppo lungo per essere un numero reale: controlla la regola.')
    minimo, massimo = intervallo
    if not (minimo <= numero <= massimo):
        return (f'{colonna}: {citato} e\' fuori dall\'intervallo ammesso '
                f'({_numero_leggibile(minimo)}–{_numero_leggibile(massimo)}). Probabile '
                'causa: il separatore delle migliaia letto come decimale — controlla le '
                'trasformazioni «Virgola decimale → punto» e «Solo cifre e separatori» '
                'nella regola.')
    return None


def _numero_leggibile(x):
    """`1.01` resta `1.01`, `1000.0` diventa `1000`: il messaggio lo legge una persona."""
    return f'{x:g}'


def _testo_canonico(valore):
    """Il valore come TESTO, nella forma JSON — la stessa che scrive JavaScript.

    `str()` di Python e `String()` di JS non concordano sui valori JSON non
    stringa: `True` contro `true`, `1.0` contro `1`. Il verdetto sarebbe lo stesso
    (un booleano non e' un numero comunque), ma il MOTIVO mostrato all'utente
    citerebbe due valori diversi nei due motori — e i motivi sono la cosa che
    questa PR esiste per rendere affidabile. Segnalato da CodeRabbit sulla PR #47.

    `None` → '' come nel resto del motore (`?? ''` in JS, mai `or ''`: una
    costante `0` o `False` e' valorizzata, non vuota).
    """
    if valore is None:
        return ''
    if isinstance(valore, bool):
        return 'true' if valore else 'false'
    if isinstance(valore, (int, float)):
        # TUTTI i numeri passano dalla conversione di JavaScript, non solo i
        # float interi sotto 1e21 del primo confine (Claude Fable 5): le soglie
        # dell'esponenziale divergono anche sui float NON interi — `str()` di
        # Python passa all'esponenziale sotto 1e-4 (`0.000001` → `'1e-06'`,
        # scartato come non numerico), `String()` di JS solo sotto 1e-6 — e
        # dove entrambi scrivono l'esponenziale il formato diverge (`'1e-07'`
        # contro `'1e-7'`). Un `Points` JSON in quella zona era valido nel
        # browser e scartato in produzione. Gli `int` passano da `float()`
        # perche' e' cio' che fa JS, che non ha interi: un intero JSON oltre la
        # precisione double deve perdere le stesse cifre che perde nel browser.
        # [REAL_FINDING] di GPT-5.6 Sol al gate finale della PR #47.
        try:
            return _numero_stile_js(float(valore))
        except OverflowError:
            # `float()` di un int oltre il massimo double: JS leggerebbe Infinity.
            return 'Infinity' if valore > 0 else '-Infinity'
    return str(valore)


def _numero_stile_js(valore):
    """`String(numero)` di JavaScript per un float, cifra per cifra (ECMA-262).

    L'algoritmo e' Number::toString(10): le cifre significative sono quelle
    della rappresentazione piu' corta che fa round-trip — le stesse che sceglie
    `repr()` di Python, quindi qui si decide solo la NOTAZIONE. Con `k` cifre
    `s` e il punto decimale in posizione `n` (valore = 0.s × 10^n):

    - `k <= n <= 21`  → cifre e zeri per esteso (`15000000000000000`);
    - `0 < n <= 21`   → punto dentro le cifre (`123.456`);
    - `-6 < n <= 0`   → zeri davanti (`0.000001`);
    - altrimenti      → esponenziale `d.ddde±E` con `E = n-1` SENZA zeri di
      riempimento: `1e-7` e `1e+21`, non `1e-07` (il segno c'e' sempre, lo
      zero di padding mai — e' la differenza con `str()` di Python).

    Ogni caso qui sopra e' vincolato dall'oracolo, che confronta col vero
    `String()` eseguito in node.
    """
    if math.isnan(valore):
        return 'NaN'
    if valore == math.inf:
        return 'Infinity'
    if valore == -math.inf:
        return '-Infinity'
    if valore == 0:
        return '0'  # anche per -0.0: String(-0) in JS e' '0'
    segno = '-' if valore < 0 else ''
    _, cifre, esp = decimal.Decimal(repr(abs(valore))).as_tuple()
    grezze = ''.join(map(str, cifre))
    # `repr` puo' portare zeri finali dentro la mantissa (`100.0` → cifre 1000,
    # esp -1): non sono significativi e vanno tolti PRIMA di contare, tenendo
    # fermo `n` (togliere uno zero accorcia `s` e alza `esp` di uno: si compensano).
    s = grezze.rstrip('0')
    n = esp + len(grezze)
    k = len(s)
    if k <= n <= 21:
        corpo = s + '0' * (n - k)
    elif 0 < n <= 21:
        corpo = s[:n] + '.' + s[n:]
    elif -6 < n <= 0:
        corpo = '0.' + '0' * -n + s
    else:
        e = n - 1
        mantissa = s if k == 1 else s[0] + '.' + s[1:]
        corpo = mantissa + 'e' + ('+' if e >= 0 else '-') + str(abs(e))
    return segno + corpo


def _citato(testo, tetto=60):
    """Un frammento della CONFIG citato dentro un motivo, in forma sicura (#25).

    Appiattito con `_piatto` (niente a capo dentro una cella di tabella) e tagliato
    per CODEPOINT, mai per unita' UTF-16: un pattern che finisce con un emoji
    astrale a cavallo del taglio lascerebbe un surrogato spaiato, e la stessa
    stringa uscirebbe diversa nei due motori. Gemella di `citato` in engine.js.
    """
    piano = _piatto(str('' if testo is None else testo))
    return piano if len(piano) <= tetto else _taglia_codepoint(piano, tetto) + '…'


def _taglia_codepoint(testo, n):
    """Primi `n` CODEPOINT, non le prime `n` unita' UTF-16 (`cutByCodePoint`).

    In Python 3 una stringa e' gia' una sequenza di codepoint, quindi `str[:n]`
    fa esattamente cio' che in JS richiede lo spread `[...s].slice(0, n)`: senza
    quello spread, `slice` conterebbe le unita' UTF-16 e un emoji astrale a
    cavallo del taglio lascerebbe un surrogato spaiato.
    """
    return str('' if testo is None else testo)[:n]


def _sostituisci_ultima(testo, da, a):
    """`replaceLast`: sostituisce l'ULTIMA occorrenza di `da` con `a`."""
    if not da:
        return testo
    i = testo.rfind(da)
    return testo if i < 0 else testo[:i] + a + testo[i + len(da):]


# Le trasformazioni, gemelle di `TRANSFORMS` in engine.js. Ogni voce prende il
# valore grezzo e la regola di trasformazione e restituisce il valore trasformato.
# Le due che sostituiscono un separatore decimale toccano SOLO la prima
# occorrenza (`count=1`): e' cio' che fa `String.replace` con un argomento
# stringa in JavaScript, e replicarlo con lo `str.replace` di Python — che di
# default le cambia TUTTE — sarebbe una divergenza silenziosa fra i due motori.
TRASFORMAZIONI_MOTORE = {
    # `BORDI_UNIFORMI`, non `v.strip()`: il `trim` tocca il VALORE estratto,
    # cioe' i byte della riga CSV, e i default di `strip()`/`trim()` divergono
    # su `\ufeff` e `\x1c-\x1f` — la stessa riga usciva diversa fra anteprima
    # e produzione (classe del [REAL_FINDING] dei gate, PR #47).
    'trim': lambda v, t: BORDI_UNIFORMI.sub('', v),
    'replace_last': lambda v, t: _sostituisci_ultima(v, t.get('from', ''), t.get('to', '')),
    'replace_all': lambda v, t: (t.get('to', '').join(v.split(t.get('from')))
                                 if t.get('from') else v),
    'upper': lambda v, t: v.upper(),
    'lower': lambda v, t: v.lower(),
    'comma_to_dot': lambda v, t: v.replace(',', '.', 1),
    'dot_to_comma': lambda v, t: v.replace('.', ',', 1),
    'digits_only': lambda v, t: (lambda m: m.group(0) if m else '')(
        re.search(r'[0-9.,]+', v)),
}


def _riga_con_ancora(message, ancora):
    """La prima riga che CONTIENE l'ancora, ignorando il caso (`findLine`).

    Ancora vuota → ogni riga la contiene → la PRIMA riga, come in JS
    (`''.includes` e' sempre vero). Restituisce `None` se nessuna riga combacia:
    e' l'`undefined` di JS, e chi chiama lo distingue dal valore vuoto.
    """
    needle = (ancora or '').lower()
    for riga in re.split(r'\r?\n', message):
        if needle in riga.lower():
            return riga
    return None


# Timeout duro (secondi) per OGNI match di una regex FORNITA DALL'UTENTE. Il worker
# Railway e' condiviso fra tutti i clienti: senza un deadline, un pattern con
# backtracking catastrofico scritto da un cliente bloccherebbe il parsing di TUTTI,
# non solo il suo — il rischio di isolamento segnalato da Claude Fable 5 e GPT-5.6
# Sol, e la richiesta esplicita del proprietario («non deve bloccare a tutti, deve
# essere tutto personale del cliente»). Il modulo `regex` interrompe il match allo
# scadere del deadline. Misurato: `(a|aa)+$` su ~60 'a' scade qui invece di
# appendere il processo; `re` di stdlib non ha timeout e per questo non si usa sui
# pattern dell'utente. 0.1s e' ampio per una regex sana e stretto per una malata.
REGEX_TIMEOUT_UTENTE = 0.1

# Budget TOTALE di tempo-regex per UNA esecuzione del parser (una condizione + 14
# colonne). Il solo timeout per-match non basta: un parser con molte regex
# catastrofiche sommerebbe 15 × 0.1s = 1.5s, bloccando l'event loop per QUEL
# messaggio (misurato). Con un budget di parser condiviso fra tutti i match della
# stessa esecuzione, l'intera `esegui_parser` resta ~0.1s comunque siano scritte le
# regole. Il caso «molti messaggi» resta il rate-limit per-utente, rimandato per
# decisione del proprietario. Segnalato da CodeRabbit sulla PR #29.
REGEX_BUDGET_PARSER_S = 0.1


def _cerca_regex_utente(pattern, message, flags, scadenza=None):
    """`regex.search` su un pattern dell'UTENTE, con timeout duro e fail-safe.

    Un pattern catastrofico di un cliente scade e restituisce None: il SUO
    messaggio non produce segnale, ma il worker resta libero per gli altri clienti
    — l'isolamento che il proprietario ha chiesto. Sia un errore di compilazione sia
    il timeout danno None, mai un'eccezione che risalga all'handler: la
    condizione/colonna risulta semplicemente non soddisfatta, esattamente come
    faceva il vecchio `except re.error` piu' stretto.

    `scadenza` (un istante `time.monotonic`) è il **budget di parser**: se passato,
    il timeout di questo match è il minimo fra `REGEX_TIMEOUT_UTENTE` e il tempo che
    resta, e se il budget è già esaurito il match non parte nemmeno. Così una
    condizione più 14 colonne tutte catastrofiche non sommano i loro timeout: la
    somma resta il budget. Senza `scadenza` vale il solo timeout per-match — è la
    chiamata diretta della funzione, fuori da `esegui_parser`.
    """
    if scadenza is not None:
        rimanente = scadenza - time.monotonic()
        if rimanente <= 0:
            return None
        timeout = min(REGEX_TIMEOUT_UTENTE, rimanente)
    else:
        timeout = REGEX_TIMEOUT_UTENTE
    try:
        return _regex.search(pattern, message, flags=flags, timeout=timeout)
    except (_regex.error, TimeoutError):
        return None


# I flag regex onorati IDENTICI dai due motori: `i`, `m`, `s` traducono al bit di
# `_regex`; `u` e' riconosciuto ma no-op (`_regex` e' gia' codepoint-native). Fonte
# UNICA: `_valida_config_parser` rifiuta al salvataggio ogni flag fuori da queste
# chiavi (Issue #86), e `flagRegex` in web/engine.js tiene lo stesso insieme
# (`FLAG_REGEX_COMUNI = 'imsu'`). Il confronto in engine_cases.mjs li allinea.
_MAPPA_FLAG_REGEX = {'i': _regex.I, 'm': _regex.M, 's': _regex.S, 'u': 0}
FLAG_REGEX_COMUNI = ''.join(_MAPPA_FLAG_REGEX)


# I costrutti regex che i due motori interpretano DIVERSAMENTE, a prescindere dai
# flag (Issue #88): `\w`/`\d`/`\b` e le loro negazioni sono unicode-aware nel modulo
# `regex` di Python (produzione) e ASCII in `RegExp` di JavaScript (anteprima). Su
# testo non-ASCII l'anteprima e il feed estraggono valori diversi per la STESSA
# regola — `(\d+)` su `٤٢` da' `٤٢` nel feed e `''` in anteprima. Al salvataggio si
# rifiutano, come i flag esotici (#86): un NUOVO parser non puo' nascere divergente;
# le config gia' salvate restano gestite a runtime (grandfathering). NON sono qui:
# `.` (allineato da `u`, pinnato dal contratto #89) e `\s`/`\S` (i due motori
# concordano in pratica). Opzione B scelta dal proprietario al gate finale #89.
COSTRUTTI_REGEX_DIVERGENTI = (r'\w', r'\W', r'\d', r'\D', r'\b', r'\B')
# Le lettere-classe si DERIVANO dalla lista sopra (fonte unica): 'wWdDbB'. Un
# backslash DISPARI davanti a una di esse e' il costrutto; uno PARI e' un backslash
# letterale + la lettera (`\\d` = backslash + 'd', non la classe cifra). Il
# lookbehind + le coppie `(?:\\\\)*` isolano il backslash dispari, ovunque nel
# pattern (anche dentro una char-class, dove `\d` resta la classe cifra).
_LETTERE_REGEX_DIVERGENTI = ''.join(c[1] for c in COSTRUTTI_REGEX_DIVERGENTI)
_RE_COSTRUTTO_DIVERGENTE = _regex.compile(
    r'(?<!\\)(?:\\\\)*(\\[' + _LETTERE_REGEX_DIVERGENTI + '])')
# Classe POSIX `[[:alpha:]]`/`[[:^digit:]]`: Python `regex` la interpreta, JS la
# legge come una char-class letterale — quindi diverge SEMPRE, a prescindere dai
# flag (Sol #90).
_RE_POSIX = _regex.compile(r'\[:\^?[a-z]+:\]')
# Proprieta' unicode `\p{...}`/`\P{...}` (backslash dispari): divergono SENZA `u`
# (in JS `\p` senza `u` e' la lettera `p`) e si ALLINEANO con `u`. Il capture e' il
# solo `\p`/`\P`, per il messaggio.
_RE_PROP_UNICODE = _regex.compile(r'(?<!\\)(?:\\\\)*(\\[pP])\{')
# La forma BREVE senza graffe `\pL`/`\PN`: Python la interpreta, ma in JS senza `u`
# e' `pL` letterale e CON `u` SOLLEVA (le graffe sono obbligatorie li'). Quindi
# diverge SEMPRE — `u` non la salva — a differenza della forma con graffe.
_RE_PROP_UNICODE_BREVE = _regex.compile(r'(?<!\\)(?:\\\\)*(\\[pP][A-Za-z])')


def _costrutto_regex_divergente(pattern, unicode_ok=False):
    """Il primo costrutto NON-escapato in `pattern` che i due motori interpretano
    DIVERSAMENTE, oppure `None`. Fonte UNICA del giudizio di `_valida_config_parser`.

    Sempre divergenti, a prescindere dai flag:
    - `\\w`/`\\d`/`\\b` (e negazioni): unicode in Python `regex`, ASCII in JS. Conta
      i backslash per non confondere la classe con un letterale (`\\d` classe,
      `\\\\d` letterale, `\\\\\\d` letterale+classe);
    - le classi POSIX `[[:name:]]`: JS non le supporta.

    Divergente SOLO senza `u` (con `u` i due motori si allineano):
    - `\\p{}`/`\\P{}` proprieta' unicode con GRAFFE. Con `unicode_ok=True` (il flag
      `u` c'e') NON sono segnalate. La condizione `match` non legge i flag, quindi
      la' e' sempre `unicode_ok=False`. La forma BREVE senza graffe `\\pL` invece
      diverge SEMPRE (in JS con `u` solleva, senza `u` e' letterale) → segnalata a
      prescindere da `unicode_ok`.

    NON e' esaustivo, e non pretende di esserlo: restano fuori i costrutti davvero
    esotici (`\\h`, `\\R`, `\\X`, quantificatori possessivi…). `[\\b]` (backspace,
    in realta' allineato) e' rifiutato in modo CONSERVATIVO insieme al `\\b` confine.
    """
    if not isinstance(pattern, str):
        return None
    m = _RE_COSTRUTTO_DIVERGENTE.search(pattern)
    if m:
        return m.group(1)
    m = _RE_POSIX.search(pattern)
    if m:
        return m.group(0)
    # La forma breve `\pL` diverge SEMPRE (u non la allinea): prima del gate `u`.
    m = _RE_PROP_UNICODE_BREVE.search(pattern)
    if m:
        return m.group(1)
    if not unicode_ok:
        m = _RE_PROP_UNICODE.search(pattern)
        if m:
            return m.group(1)
    return None


def _vieta_costrutto_regex_divergente(dove, pattern, unicode_ok=False):
    """Rifiuta al salvataggio un pattern con un costrutto divergente (#88, #90), con
    un 422 che nomina il costrutto e la via d'uscita.

    Fonte UNICA per i due punti che compilano un pattern dell'utente: le colonne
    `source: regex` (dove `unicode_ok` dipende dal flag `u`) e la condizione `match`
    di tipo `regex` (che i flag non li legge, quindi `unicode_ok=False`). Non tocca
    il runtime: le config gia' salvate continuano a girare (grandfathering).
    """
    costrutto = _costrutto_regex_divergente(pattern, unicode_ok=unicode_ok)
    if costrutto is None:
        return
    classe = {r'\d': '[0-9]', r'\D': '[^0-9]',
              r'\w': '[A-Za-z0-9_]', r'\W': '[^A-Za-z0-9_]'}.get(costrutto)
    if classe:
        consiglio = (f' Usa una classe esplicita ({classe}), che i due motori '
                     'trattano identica.')
    elif costrutto[:2] in (r'\p', r'\P'):
        consiglio = (" Usa una classe esplicita, oppure la forma con graffe `\\p{…}` "
                     "su una colonna regex col flag 'u', che allinea i due motori "
                     '(il match non legge i flag; la forma breve `\\pL` non si allinea '
                     'mai).')
    elif costrutto.startswith('[:'):
        consiglio = (' Le classi POSIX non esistono in JavaScript: usa una classe '
                     'esplicita.')
    else:
        consiglio = (' Ancora la condizione con delimitatori espliciti invece del '
                     'confine di parola.')
    raise HTTPException(
        422, f'{dove}: il costrutto regex {costrutto!r} e\' interpretato '
             f'diversamente fra anteprima (JS) e feed (Python) sul testo '
             f'non-ASCII (#88).' + consiglio)


def _flag_regex(flags):
    """I flag JS (`'i'`, `'im'`, …) tradotti nei flag del modulo `regex`.

    Onora il solo insieme che i due motori condividono IDENTICO — `i`, `m`, `s` —
    col default `'i'` (in engine.js `rule.flags || 'i'`). Gli altri sono ignorati,
    non tradotti in errore, e il perche' e' MISURATO, non temuto (audit #81 E2):

    - `x` (verbose): qui lo mappava `regex.X`, ma `web/engine.js` passa i flag a
      `new RegExp`, che su `'x'` SOLLEVA "Invalid flags" e fa cadere l'estrazione
      a '' — cioe' lo stesso parser con `flags:'x'` scartava gli spazi del pattern
      di qua e non di la'. Tolto da entrambi i lati per parita';
    - `u` (unicode): JS ne ha bisogno perche' `.` e `\\p{}` combacino coi
      codepoint; qui `_regex` e' gia' codepoint-native, quindi `u` e' un no-op
      innocuo e i due motori restano allineati;
    - `g` (globale), `y` (sticky): la ricerca non e' globale e non e' ancorata.

    I flag sono quelli di `_regex` perche' e' `_regex.search` a riceverli (vedi
    `_cerca_regex_utente`), non lo `re` di stdlib. Il gemello e' `flagRegex` in
    `web/engine.js`, e il confronto in `engine_cases.mjs` tiene i due allineati.

    Il DEFAULT combacia ESATTO con `rule.flags || 'i'` di JS: `'i'` si applica
    solo quando i flag sono ASSENTI (None o stringa vuota). Un insieme PRESENTE
    ma con soli flag fuori dal comune (`'x'`, `'gy'`) NON ricade su `'i'`: tiene
    zero flag, cioe' resta CASE-SENSITIVE — che e' il comportamento che aveva
    prima di questo fix (pre-fix `_flag_regex('gy')` era `0`, e `'x'` era
    `regex.X`, entrambi senza `I`). Il fix toglie solo il verbose (`x`) e lo
    sticky (`y`), gia' divergenti fra i motori; la case NON cambia, cosi' un
    parser gia' salvato con quei flag non muta i suoi valori nel feed. Cambiarla
    sarebbe la regressione silenziosa segnalata come bloccante da Claude Fable 5
    sulla PR #85. `'u'` e' riconosciuto ma no-op (`_regex` e' gia'
    codepoint-native) e resta case-sensitive, come `new RegExp(_, 'u')` in JS.

    `flags` arriva qui GIA' validato come stringa (o assente): un `flags` presente
    ma non-stringa viene scartato PRIMA, in `_estrai_valore`, che tratta la regola
    come malformata e restituisce colonna vuota (fail-closed) — cosi' un config
    con `flags: 5`/`[{...}]`/`{...}` non fa uscire un segnale che su `main` non
    usciva, e non si dipende dalla forma della coercizione a stringa (che diverge
    da `String()` di JS). Bloccanti Claude Fable 5 (niente `TypeError`) e GPT-5.6
    Sol (niente fail-open), PR #85. Il `isinstance` qui e' una rete difensiva
    ridondante: tiene la funzione totale se un domani un chiamante la invoca senza
    passare dal guard. Una STRINGA presente ma con soli flag scartati resta
    case-sensitive.
    """
    if not isinstance(flags, str) or flags == '':
        return _regex.I
    risultato = 0
    for f in flags:
        risultato |= _MAPPA_FLAG_REGEX.get(f, 0)
    return risultato


def _estrai_valore(message, regola, scadenza=None, diario=None):
    """Il valore grezzo di una colonna dal messaggio, poi le trasformazioni
    (`extractValue`).

    `scadenza` è il budget di parser condiviso, passato solo al match regex (vedi
    `_cerca_regex_utente`); le altre sorgenti non eseguono regex dell'utente.

    `diario` (#25) è un dict facoltativo in cui il ramo che produce un valore VUOTO
    scrive `motivo`: perche' quella regola non ha estratto niente. Nasce QUI e non
    in una funzione che ri-ispeziona la regola dopo, e la differenza non e'
    stilistica — un secondo posto che rifa' le stesse verifiche diverge dal runtime
    al primo ramo che cambia, che e' esattamente il difetto C del Bridge («la prova
    piu' pessimista del runtime») che la #25 dice di non ereditare. Il motivo lo
    scrive il ramo che il vuoto lo ha appena prodotto: non puo' sbagliarsi.

    Chi non passa `diario` non paga niente e non cambia comportamento.

    I casi che restituiscono PRIMA delle trasformazioni sono deliberati e copiati
    da JS: riga non trovata e regex che non combacia (o non compila) danno `''`
    senza passare dalle trasformazioni. Una riga TROVATA ma senza il marcatore
    da' `''` e POI applica le trasformazioni, perche' in JS non c'e' un ritorno
    anticipato in quel ramo.
    """
    def _annota(motivo):
        # `is not None`: un dict vuoto e' un diario valido, non «nessun diario».
        if diario is not None:
            diario['motivo'] = motivo

    if not regola:
        return ''
    sorgente = regola.get('source')
    if sorgente == 'empty':
        return ''
    if sorgente == 'constant':
        v = regola.get('value')
        v = '' if v is None else v
        if not _piatto(str(v)):
            _annota('la costante impostata nella regola e\' vuota.')
    elif sorgente == 'message':
        v = message
        if not _piatto(str(v)):
            _annota('la regola prende il messaggio intero, e il messaggio e\' vuoto.')
    elif sorgente == 'line':
        riga = _riga_con_ancora(message, regola.get('anchor'))
        if riga is None:
            _annota(f'nel messaggio non c\'e\' nessuna riga che contiene '
                    f'«{_citato(regola.get("anchor"))}»: controlla il testo da cercare, '
                    'o se il messaggio di prova e\' quello giusto.')
            return ''
        marcatore = regola.get('marker')
        if regola.get('part') == 'after' and marcatore:
            i = riga.lower().find(marcatore.lower())
            if i < 0:
                _annota(f'la riga con «{_citato(regola.get("anchor"))}» c\'e\', ma non '
                        f'contiene «{_citato(marcatore)}», il marcatore dopo cui la '
                        'regola legge il valore.')
            v = '' if i < 0 else riga[i + len(marcatore):]
        else:
            v = riga
    elif sorgente == 'regex':
        # `flags` presente ma NON stringa (dal config_json non attendibile): regola
        # malformata. Come un pattern che non compila, la colonna resta VUOTA
        # (fail-closed) invece di girare col default: su una colonna obbligatoria
        # il segnale non esce, come su `main` (dove un flags non-stringa sollevava
        # TypeError e il segnale non usciva) — ma senza il crash e senza fail-open.
        # Simmetrico a `extractValue` in engine.js. [REAL_FINDING] di GPT-5.6 Sol,
        # PR #85.
        flags = regola.get('flags')
        if flags is not None and not isinstance(flags, str):
            _annota('la regola ha un campo «flags» che non e\' testo: e\' malformata '
                    'e la colonna resta vuota. Risalvala dal wizard.')
            return ''
        # Pattern dell'utente → timeout duro (worker condiviso), dentro il budget di
        # parser. Errore o scadenza danno None, cioe' colonna vuota, come prima
        # faceva `except re.error`.
        m = _cerca_regex_utente(regola.get('pattern', ''), message,
                                _flag_regex(flags), scadenza)
        if not m:
            # Un solo motivo per «non compila» e «non combacia»: qui i due casi
            # sono indistinguibili per costruzione — `_cerca_regex_utente`
            # restituisce None per entrambi, e anche per il timeout. Distinguerli
            # richiederebbe di ricompilare il pattern nella diagnosi, cioe' un
            # secondo posto che puo' divergere. In pratica non si perde nulla: una
            # regex che non compila non e' salvabile, `_valida_config_parser` la
            # rifiuta con 422 alla scrittura.
            _annota(f'l\'espressione regolare «{_citato(regola.get("pattern", ""))}» '
                    'non ha trovato corrispondenza in questo messaggio.')
            return ''
        gruppo = regola.get('group', 1)
        try:
            catturato = m.group(gruppo)
        except IndexError:
            # Gruppo fuori portata: in JS `m[n]` sarebbe `undefined`, e `?? m[0]`
            # ricade sulla corrispondenza intera. Qui ci si ricade allo stesso modo.
            catturato = None
        v = catturato if catturato is not None else m.group(0)
    else:
        return ''
    prima_delle_trasformazioni = _piatto(str(v))
    for t in regola.get('transforms') or []:
        fn = TRASFORMAZIONI_MOTORE.get(t.get('op'))
        if fn:
            v = fn(v, t)
    # Il caso insidioso: l'estrazione HA funzionato, sono le trasformazioni ad aver
    # svuotato il campo. Senza questo motivo l'utente cerca il guasto nella
    # sorgente, che invece e' corretta.
    if prima_delle_trasformazioni and not _piatto(str(v)):
        _annota('la sorgente ha estratto un valore, ma le trasformazioni della '
                'regola lo hanno svuotato: controlla la catena di trasformazioni.')
    return v


def condizione_soddisfatta(message, cond, scadenza=None):
    """Il messaggio appartiene a questo parser? (`matches`).

    Senza condizione o senza valore → `False`: un parser senza condizione non
    rivendica ogni messaggio, non rivendica nessuno. `scadenza` è il budget di
    parser condiviso (vedi `_cerca_regex_utente`).
    """
    if not cond or not cond.get('value'):
        return False
    if cond.get('type') == 'regex':
        # Pattern dell'utente → timeout duro (worker condiviso), dentro il budget di
        # parser. Errore o scadenza danno None → False: messaggio non rivendicato.
        return _cerca_regex_utente(cond['value'], message, _regex.I, scadenza) is not None
    return cond['value'].lower() in message.lower()


def esegui_parser(message, config, mappa_alias=None):
    """Esegue la config sul messaggio (`runParser`).

    Restituisce `matched` (soddisfa la condizione), `row` (le 14 colonne, sempre
    presenti, vuote dove non mappate), `missing` (le obbligatorie risultate vuote)
    e `complete` (matched e nessuna obbligatoria mancante).

    Chi scrive il feed guarda `complete`, non `matched`: un messaggio riconosciuto
    ma senza evento produrrebbe una riga quotata e priva di senso. Il `.strip()`
    su `missing` e' un pavimento che non dipende dalla configurazione — una colonna
    obbligatoria fatta di soli spazi e' vuota, anche se l'utente non ha messo un
    `trim` fra le trasformazioni.
    """
    # Un solo budget di tempo-regex per l'INTERA esecuzione (condizione + 14
    # colonne): senza, un parser con molte regex catastrofiche sommerebbe i timeout
    # per-match (misurato 1.5s su 15 regex) e bloccherebbe l'event loop per quel
    # messaggio. Ogni `_cerca_regex_utente` sotto riceve questa scadenza condivisa.
    scadenza = time.monotonic() + REGEX_BUDGET_PARSER_S
    colonne = config.get('columns') or {}
    matched = condizione_soddisfatta(message, config.get('match'), scadenza)
    # Il `diario` per colonna (#25): il motivo per cui una regola non ha estratto
    # niente, scritto dal ramo che il vuoto lo ha prodotto. Un dict per colonna,
    # raccolto solo quando c'e' davvero un motivo.
    row = []
    motivi_regola = {}
    for c in HEADERS:
        diario = {}
        row.append(_estrai_valore(message, colonne.get(c), scadenza, diario))
        if diario.get('motivo'):
            motivi_regola[c] = diario['motivo']
    # La sorgente squadre (#34 pezzo 3): con una mappa alias→Betfair l'evento
    # si traduce QUI, sul valore finale di EventName — spezzato sull'ULTIMO
    # ' - ' (il separatore che il transform del wizard produce), ogni meta'
    # normalizzata con `_piatto` (la classe di spazi gemellata, PR #47) e
    # cercata per confronto ESATTO. Squadra sconosciuta = verbatim + AVVISO
    # non bloccante (deciso dal proprietario il 17/08/2026): la mappa porta
    # anche le identita' Betfair→Betfair, quindi l'avviso scatta solo sui nomi
    # davvero estranei. Senza mappa (nessuna sorgente nel parser) non si tocca
    # niente. Stesso blocco in `runParser`, vincolato dai casi di parita'.
    avvisi = []
    # `is not None`, NON la verita' del dict: una mappa VUOTA e' una sorgente
    # selezionata senza alias compilati, e deve tradurre (cioe' avvisare) come
    # in JS, dove `{}` e' truthy. Con `and mappa_alias` i due motori
    # divergevano esattamente li' — misurato dal caso di parita'.
    if matched and mappa_alias is not None:
        i_evento = HEADERS.index('EventName')
        evento = str('' if row[i_evento] is None else row[i_evento])
        if _piatto(evento):
            sep = evento.rfind(' - ')
            parti = [evento] if sep < 0 else [evento[:sep], evento[sep + 3:]]
            tradotte = []
            for parte in parti:
                nome = _piatto(parte)
                if nome and nome in mappa_alias:
                    tradotte.append(mappa_alias[nome])
                    continue
                if nome:
                    avvisi.append(
                        f'EventName: «{nome}» non ha un alias in questa sorgente '
                        "squadre: nel feed esce verbatim, e XTrader lo trovera' "
                        'solo se coincide col nome Betfair.')
                tradotte.append(nome)
            row[i_evento] = ' - '.join(tradotte)

    giudizio = _giudica_riga(row, matched)
    row = giudizio['row']
    mancanti = giudizio['missing']
    scarti = giudizio['scarti']
    # Il gate di CONTENUTO (#41): almeno una colonna obbligatoria deve venire da
    # un'estrazione REALE che ha prodotto qualcosa. Un parser con tutte e quattro
    # le obbligatorie costanti produce una riga piazzabile per QUALSIASI messaggio
    # che soddisfi la condizione — misurato: «ciao a tutti» e «oggi partita»
    # davano `complete=True`. Oggi va bene per accidente, perche' nell'uso normale
    # almeno `EventName` si estrae; il caso reale e' chi prova il parser con valori
    # fissi per vedere il CSV uscire e poi lo lascia attivo.
    if matched and not mancanti:
        gate = _scarto_estrazione(colonne, row)
        if gate:
            scarti.append(gate)
    completo = matched and not mancanti and not scarti
    # La diagnosi per colonna (#25) si costruisce QUI, coi motivi definitivi
    # della riga base — gate #41 compreso — e con gli `avvisi`, che nascono nel
    # chiamante (la sorgente squadre) come livello `segnala`: la riga esce lo
    # stesso. Costruirla prima del gate la faceva mentire (vedi `_diagnosi_colonne`).
    diagnosi = _diagnosi_colonne(row, matched, mancanti, scarti, avvisi,
                                motivi_regola)
    # Il multi-riga (#35 pezzo 2): `righe` e' l'elenco delle righe GENERATE —
    # senza `config.multi` e' la sola base (comportamento storico), con le
    # righe di override e' la loro somma. I campi storici continuano a
    # descrivere la BASE: i consumatori esistenti non cambiano.
    # Gli `avvisi` scendono nelle righe generate perche' le righe EREDITANO le
    # colonne della base (EventName incluso, che e' l'unica sorgente di avvisi
    # oggi): un avviso vero sulla base e' vero anche sulle sue derivate.
    righe = _genera_righe(message, config, matched,
                          {'row': row, 'missing': mancanti, 'scarti': scarti,
                           'complete': completo, 'diagnosi': diagnosi}, avvisi)
    return {'matched': matched, 'row': row, 'missing': mancanti, 'scarti': scarti,
            'avvisi': avvisi, 'diagnosi': diagnosi, 'righe': righe,
            'complete': completo}


def _giudica_riga(row_grezza, matched):
    """Giudica UNA riga gia' estratta, come `giudicaRiga` in engine.js.

    Appiattisce i numerici (`_piatto` del testo canonico — il CSV non deve
    emettere il byte che la guardia ha solo perdonato, PR #47), calcola le
    obbligatorie mancanti (`None`→'' e basta, NON `or ''`: una costante `0`
    e' valorizzata, PR #28), gli scarti (guardie numeriche + emoji #42, solo
    a messaggio riconosciuto, PR #47) e localizza gli accettati (#40, la
    virgola di XTrader; i rifiutati restano in forma giudicata). Fonte unica
    (#35 pezzo 2): la usano la riga base e ogni riga generata dagli override —
    un giudizio scritto due volte sarebbe due giudizi.
    """
    row = list(row_grezza)
    for colonna in INTERVALLI_NUMERICI:
        indice = HEADERS.index(colonna)
        row[indice] = _piatto(_testo_canonico(row[indice]))

    def _vuota(valore):
        return not _piatto(str('' if valore is None else valore))

    mancanti = [c for c in COLONNE_OBBLIGATORIE if _vuota(row[HEADERS.index(c)])]
    motivi_numerici = {c: motivo_valore_numerico(c, row[HEADERS.index(c)])
                       for c in INTERVALLI_NUMERICI}
    scarti = [m for m in motivi_numerici.values() if m] if matched else []
    # Il motivo dell'emoji si tiene anche PER COLONNA (#25): la diagnosi per
    # colonna deve poter dire quale valore ha il problema, non solo che esiste.
    if matched:
        for colonna in HEADERS:
            if colonna in INTERVALLI_NUMERICI:
                continue
            testo = str('' if row[HEADERS.index(colonna)] is None
                        else row[HEADERS.index(colonna)])
            if _EMOJI.search(testo):
                piano = _piatto(testo)
                citato = (piano if len(piano) <= 60
                          else _taglia_codepoint(piano, 60) + '…')
                scarti.append(
                    f'{colonna}: il valore contiene un\'emoji («{citato}»). '
                    'XTrader marcherebbe il segnale non valido, senza nessun '
                    'errore di ritorno: estrai il testo DOPO il marcatore, '
                    'non la riga intera.')
    for colonna, motivo in motivi_numerici.items():
        indice = HEADERS.index(colonna)
        if motivo is None and row[indice]:
            row[indice] = row[indice].replace('.', SEPARATORE_DECIMALE, 1)
    return {'row': row, 'missing': mancanti, 'scarti': scarti}


# Il motivo dell'obbligatoria vuota: l'UNICA stringa nuova della diagnosi per
# colonna (#25). Tutte le altre riusano i motivi che gia' finiscono in `scarti`
# e `avvisi` — che sono gia' azionabili — cosi' non nasce un secondo catalogo da
# tenere allineato fra i due motori. Identica a `MOTIVO_OBBLIGATORIA_VUOTA` in
# engine.js, e il caso di parita' la confronta.
def _motivo_obbligatoria_vuota(colonna, motivo_regola=''):
    """Perche' questa obbligatoria e' vuota — e sono DUE cose diverse (#25).

    Senza `motivo_regola` la colonna non e' mappata su nessuna sorgente, e il
    consiglio giusto e' mapparla. CON `motivo_regola` la colonna e' mappata e la
    regola non ha estratto niente: consigliare di mapparla sarebbe consigliare una
    cosa gia' fatta, e la causa vera resterebbe taciuta. Era il difetto misurato
    sulla #25 dopo il merge della PR #104, ed e' la forma della #328 del Bridge —
    la causa formale al posto dell'azione.

    Identica a `motivoObbligatoriaVuota` in engine.js; i casi di parita' le
    confrontano per intero.
    """
    if motivo_regola:
        return (f"{colonna}: e' obbligatoria ed e' rimasta vuota — {motivo_regola} "
                "Finche' resta vuota, nessuna riga verra' scritta nel feed.")
    return (f"{colonna}: e' obbligatoria e non e' mappata su nessuna sorgente. "
            "Scegli una regola che legga dal messaggio, o nessuna riga verra' "
            "scritta nel feed.")


def _motivi_di(messaggi, colonna):
    """I motivi che nominano `colonna` in testa (`EventName: ...`).

    UNICA regola d'attribuzione della diagnosi (#25): la usano gli `scarti` e gli
    `avvisi`, in Python come in JS (`motiviDi`). Scritta una volta perche' due
    regole d'aggancio sarebbero due comportamenti alla prima divergenza.
    """
    return [m for m in (messaggi or []) if str(m).split(':', 1)[0] == colonna]


def cause_di_riga(scarti):
    """Gli scarti che NON nominano nessuna delle 14 colonne (#25).

    Sono le cause di RIGA, non di colonna: oggi il gate di contenuto (#41), che
    parla del parser nel suo insieme («nessuna colonna obbligatoria viene estratta
    dal messaggio»). La diagnosi per colonna non puo' ospitarle senza mentire su
    quale colonna sia il problema, e perderle sarebbe peggio: la tabella direbbe
    «nessuna colonna blocca» mentre la riga non esce. Il pannello le mostra sotto
    la tabella. Gemella di `causeDiRiga` in engine.js.
    """
    return [s for s in (scarti or [])
            if not any(str(s).split(':', 1)[0] == c for c in HEADERS)]


def _diagnosi_colonne(row, matched, mancanti, scarti, avvisi, motivi_regola=None):
    """La diagnosi PER COLONNA: 14 voci `{colonna, stato, motivo, valore}` (#25).

    Risponde alla domanda «perche' questa colonna e' cosi'» per **ognuna** delle
    14, non solo per quelle problematiche. Gemella di `diagnosiColonne` in
    engine.js, confrontata dal caso di parita'.

    **Si costruisce sui motivi FINALI di UNA riga** — i suoi `mancanti`, i suoi
    `scarti`, i suoi `avvisi` — non su un sottoinsieme calcolato a meta' strada.
    Era il difetto delle PR #104 prima di questa correzione, e i reviewer l'hanno
    fermato: la diagnosi nasceva dentro `_giudica_riga`, quindi il gate #41
    (aggiunto agli scarti DOPO) e le cause delle righe di override (#35) non
    comparivano — misurato: `complete=False` con «0 colonne bloccano», cioe'
    esattamente il contrario di cio' per cui la tabella esiste. Ogni chiamante che
    possiede un verdetto di riga la costruisce, e ogni riga generata ha la sua.

    **Due livelli di gravita', deliberatamente distinti** (vincolo del commento
    del 14/08 sulla #25, che nasce da un difetto del Bridge: la' un rosso che
    blocca e un rosso su campo facoltativo avevano lo stesso aspetto):

    - `blocca` — senza questa colonna la riga NON esce: un'obbligatoria vuota,
      oppure un valore scartato (guardia numerica, emoji, delimitatori del
      multi-riga). Sono esattamente le cause che rendono `complete` falso;
    - `segnala` — c'e' qualcosa da sapere ma la riga ESCE lo stesso (gli
      `avvisi`);
    - `ok` — valorizzata e senza problemi;
    - `vuota` — vuota ma facoltativa e senza problemi: **non e' un errore**
      (`Price` vuota e' il caso normale, la quota la mette XTrader).

    A messaggio NON riconosciuto nessuna colonna `blocca`: la riga non esce
    perche' la condizione non ha combaciato, e attribuire quel rifiuto alle
    obbligatorie vuote indicherebbe all'utente la causa sbagliata (segnalato da
    Claude Fable 5 sulla PR #104). Il pannello dice gia' «Ignorato: la condizione
    non corrisponde», e la tabella resta il referto di cio' che si estrarrebbe.

    Il `valore` e' quello FINALE, gia' localizzato: la tabella mostra cio' che
    verrebbe scritto, non una forma intermedia.
    """
    voci = []
    for colonna in HEADERS:
        valore = str('' if row[HEADERS.index(colonna)] is None
                     else row[HEADERS.index(colonna)])
        suoi_scarti = _motivi_di(scarti, colonna)
        suoi_avvisi = _motivi_di(avvisi, colonna)
        # Il motivo della REGOLA che non ha estratto (#25): lo ha scritto il ramo
        # di `_estrai_valore` che ha prodotto il vuoto. Vale solo su una colonna
        # VUOTA — su un valore scartato non c'e' nessun vuoto da spiegare, e i due
        # rami sotto hanno la precedenza.
        della_regola = (motivi_regola or {}).get(colonna, '')
        if suoi_scarti:
            stato, motivo = 'blocca', ' '.join(suoi_scarti)
        elif matched and colonna in (mancanti or []):
            stato = 'blocca'
            motivo = _motivo_obbligatoria_vuota(colonna, della_regola)
        elif suoi_avvisi:
            # Non declassa mai un bloccante: i due rami sopra hanno gia' deciso.
            stato, motivo = 'segnala', ' '.join(suoi_avvisi)
        elif not _piatto(valore):
            # `vuota` NON diventa un errore — una facoltativa vuota e' il caso
            # normale (Price la mette XTrader) — ma se una regola c'era e non ha
            # estratto, la cella «Motivo» lo dice invece di restare muta.
            stato = 'vuota'
            motivo = f'{colonna}: {della_regola}' if della_regola else ''
        else:
            stato, motivo = 'ok', ''
        voci.append({'colonna': colonna, 'stato': stato,
                     'motivo': motivo, 'valore': valore})
    return voci


# Gli override del multi-riga (#35): campo della riga → colonna del CSV.
CAMPI_MULTI = {
    'market_type': 'MarketType', 'market_name': 'MarketName',
    'selection_name': 'SelectionName', 'price': 'Price',
    'min_price': 'MinPrice', 'max_price': 'MaxPrice', 'bet_type': 'BetType',
    'handicap': 'Handicap', 'points': 'Points',
}

# I soli mercati dove la selezione VUOTA + delimitatori estrae i punteggi.
MERCATI_PUNTEGGI = ('CORRECT_SCORE', 'HALF_TIME_SCORE')


def _segmento(message, dopo, prima):
    """Il testo del messaggio fra i due delimitatori della riga, come
    `segmento` in engine.js: delimitatore assente = dal principio / fino alla
    fine; delimitatore NON TROVATO = ''."""
    inizio = 0
    fine = len(message)
    if dopo:
        i = message.find(dopo)
        if i < 0:
            return ''
        inizio = i + len(dopo)
    if prima:
        j = message.find(prima, inizio)
        if j < 0:
            return ''
        fine = j
    return message[inizio:fine]


# Tetto dei punteggi ESTRATTI da una riga: 36 copre 0-0..5-5, cioe' ogni
# mercato dei risultati reale. Oltre non e' un mercato: sono delimitatori che
# prendono mezzo messaggio, e senza tetto un messaggio pieno di N-N per 20
# righe genererebbe migliaia di documenti nel feed — lo storage e' condiviso
# (#31). Bloccante di Claude Fable 5 sulla PR #69. Il caso e' segnalato come
# errore di config della riga, non troncato in silenzio.
MAX_PUNTEGGI_RIGA = 36


def _riga_multi(voce):
    """Vero se la voce di `multi.markets`/`multi.selections` e' una RIGA.

    Solo un oggetto NON vuoto: `{}` (e qualunque altra cosa) non genera un
    clone della base. In Python `{}` e' falsy e in JS truthy: senza questo
    predicato comune i due motori divergevano — misurato: 2 righe in JS, 1 in
    Python, dalla stessa config. Gemella di `rigaMulti` in engine.js.
    """
    return isinstance(voce, dict) and bool(voce)


def _genera_righe(message, config, matched, base, avvisi=None):
    """Le righe GENERATE dal parser (#35 pezzo 2), come `generaRighe` in JS.

    La base e' il modello; ogni riga di `config.multi` dice solo cosa cambia e
    il resto EREDITA (tranello 3: campo vuoto = quello della base, mai
    «nessuno»). Somma, non prodotto: mercati attivi + selezioni attive.
    `enabled: false` resta salvata e non genera (tranello 2). Ogni riga e'
    giudicata DA SOLA: una rotta non ferma le altre (tranello 1). Le
    MultiSelection restano sul mercato base per contratto: un loro
    `market_type` viene ignorato. Selezione VUOTA + delimitatori = punteggi
    dinamici, SOLO su CORRECT_SCORE/HALF_TIME_SCORE (tranello 4): altrove e'
    un errore di config segnalato, non una riga.

    Ogni riga porta la PROPRIA `diagnosi` per colonna (#25): con gli override il
    verdetto e' per riga, quindi una diagnosi sola — quella della base — spiegava
    la riga sbagliata. Anche le righe rifiutate prima del giudizio (delimitatori)
    ne hanno una, o la loro tabella sarebbe l'unica vuota proprio dove serve.
    """
    if not matched:
        return []
    multi = config.get('multi') if isinstance(config.get('multi'), dict) else {}
    attive = []
    # `isinstance(..., list)`, non `or []`: un `markets` non-lista qui veniva
    # ITERATO (le chiavi di un dict, i caratteri di una stringa) mentre in JS
    # il for..of sollevava — due esiti diversi dalla stessa config. Non-lista
    # = nessuna riga, in entrambi (segnalato da CodeRabbit sulla PR #69).
    mercati = multi.get('markets')
    selezioni = multi.get('selections')
    for m in (mercati if isinstance(mercati, list) else []):
        if _riga_multi(m) and m.get('enabled') is not False:
            attive.append((m, True))
    for s in (selezioni if isinstance(selezioni, list) else []):
        if _riga_multi(s) and s.get('enabled') is not False:
            attive.append((s, False))
    if not attive:
        return [base]
    i_sel = HEADERS.index('SelectionName')
    i_prezzo = HEADERS.index('Price')
    i_mercato = HEADERS.index('MarketType')
    righe = []

    def _esito(row, mancanti, scarti):
        """L'esito di UNA riga generata, diagnosi compresa: forma unica per tutti
        i rami (giudicata, rifiutata dai delimitatori, punteggio)."""
        return {'row': row, 'missing': mancanti, 'scarti': scarti,
                'complete': not mancanti and not scarti,
                'diagnosi': _diagnosi_colonne(row, True, mancanti, scarti, avvisi)}

    def _rifiuto(row, motivo):
        """Una riga rifiutata dai delimitatori: il motivo del ramo SOMMATO al
        giudizio pieno della riga.

        Non basta il motivo del ramo. [REAL_FINDING] di GPT-5.6 Sol al gate finale
        della PR #104: con `missing=[]` e il solo scarto proprio, le altre cause
        della stessa riga sparivano dalla diagnosi — e non solo sparivano, la
        colonna veniva dichiarata SANA. Misurato: una riga rifiutata dai
        delimitatori con quota `0.5` mostrava «Price: ok», cioe' un'affermazione
        falsa nella tabella che l'utente legge per correggere. Il verdetto della
        riga era gia' giusto: a mentire era la spiegazione, che e' esattamente
        cio' per cui la #25 esiste.
        """
        giudizio = _giudica_riga(row, True)
        return _esito(giudizio['row'], giudizio['missing'],
                      [motivo] + giudizio['scarti'])

    for riga, mercato in attive:
        derivata = list(base['row'])
        # Le colonne SOVRASCRITTE dalla riga: per il gate #41 non contano come
        # estratte — il valore che portano e' una costante della riga,
        # qualunque cosa dica la regola della base.
        sovrascritte = []
        for campo, colonna in CAMPI_MULTI.items():
            # Le MultiSelection non toccano il mercato: e' il contratto della
            # somma — per le combinazioni si elencano righe MultiMarket.
            if not mercato and campo in ('market_type', 'market_name'):
                continue
            valore = riga.get(campo)
            if valore is not None and _testo_canonico(valore) != '':
                derivata[HEADERS.index(colonna)] = _testo_canonico(valore)
                sovrascritte.append(colonna)
        sel = riga.get('selection_name')
        sel_esplicita = _piatto(_testo_canonico('' if sel is None else sel))
        dopo_grezzo = riga.get('start_after')
        prima_grezzo = riga.get('end_before')
        con_delimitatori = bool(dopo_grezzo or prima_grezzo)
        dopo = _testo_canonico(dopo_grezzo) if dopo_grezzo else ''
        prima = _testo_canonico(prima_grezzo) if prima_grezzo else ''
        if con_delimitatori and sel_esplicita:
            # Delimitatori con selezione: estraggono la QUOTA propria della riga.
            derivata[i_prezzo] = _segmento(message, dopo, prima)
        if con_delimitatori and not sel_esplicita:
            if derivata[i_mercato] not in MERCATI_PUNTEGGI:
                righe.append(_rifiuto(derivata,
                    'SelectionName: la selezione vuota con i delimitatori '
                    'estrae i punteggi ed e\' ammessa solo su CORRECT_SCORE e '
                    'HALF_TIME_SCORE: questa riga non genera nulla.'))
                continue
            # `[0-9]`, non `\\d`: in JS `\\d` e' solo ASCII, in Python
            # prenderebbe anche le cifre unicode — i due motori divergerebbero.
            punteggi = re.findall(r'[0-9]+-[0-9]+',
                                  _segmento(message, dopo, prima))
            if not punteggi:
                righe.append(_rifiuto(derivata,
                    'SelectionName: nessun punteggio N-N fra i delimitatori '
                    'della riga.'))
                continue
            if len(punteggi) > MAX_PUNTEGGI_RIGA:
                righe.append(_rifiuto(derivata,
                    'SelectionName: troppi punteggi fra i delimitatori della '
                    f'riga ({len(punteggi)}, massimo {MAX_PUNTEGGI_RIGA}): '
                    'controlla i delimitatori.'))
                continue
            for punteggio in punteggi:
                per_punteggio = list(derivata)
                per_punteggio[i_sel] = punteggio
                # Niente gate #41 qui: il punteggio VIENE dal messaggio per
                # costruzione, quindi la riga varia col messaggio.
                giudizio = _giudica_riga(per_punteggio, True)
                righe.append(_esito(giudizio['row'], giudizio['missing'],
                                    giudizio['scarti']))
            continue
        giudizio = _giudica_riga(derivata, True)
        if not giudizio['missing']:
            # Il gate #41 vale PER RIGA: le colonne sovrascritte non contano
            # come estratte, quindi la regola della base si toglie dal conto.
            colonne = dict(config.get('columns') or {})
            for colonna in sovrascritte:
                colonne.pop(colonna, None)
            gate = _scarto_estrazione(colonne, giudizio['row'])
            if gate:
                giudizio['scarti'].append(gate)
        righe.append(_esito(giudizio['row'], giudizio['missing'],
                            giudizio['scarti']))
    return righe


def _scarto_estrazione(colonne, row):
    """Il gate di CONTENUTO (#41) come fonte unica: None o il testo dello scarto.

    Lo usano la riga base e ogni riga di override (#35 pezzo 2): senza il
    secondo uso, `config.multi` era la porta sul retro del gate — base tutta
    costante, una riga di override, e la stessa scommessa fissa usciva per
    qualunque messaggio riconosciuto. Gemella di `scartoEstrazione` in
    engine.js.
    """
    if _estrazione_reale(colonne, row):
        return None
    return ('nessuna colonna obbligatoria viene estratta dal messaggio: con soli '
            'valori fissi questo parser scriverebbe la stessa scommessa per '
            'qualunque messaggio. Almeno una fra '
            + ', '.join(COLONNE_OBBLIGATORIE) + ' deve leggere dal messaggio.')


def _estrazione_reale(colonne, row):
    """Vero se almeno una colonna OBBLIGATORIA legge dal messaggio e ha prodotto valore.

    Non basta che la regola dichiari una sorgente d'estrazione: deve anche aver
    estratto qualcosa. Una regola `line` che non trova la riga lascia il campo
    vuoto, e in quel caso la riga cade gia' per `missing` — ma se l'utente ha messo
    una costante su quella colonna e l'estrazione su un'altra, il conto va fatto
    sui valori, non sulle intenzioni.
    """
    for colonna in COLONNE_OBBLIGATORIE:
        regola = (colonne or {}).get(colonna) or {}
        if not isinstance(regola, dict):
            continue
        if regola.get('source') in ('line', 'regex', 'message'):
            # `_piatto`, non `strip()`: stessa emptiness di `_vuota`, o i due
            # motori divergerebbero sui caratteri che i default non coprono.
            if _piatto(str('' if row[HEADERS.index(colonna)] is None
                           else row[HEADERS.index(colonna)])):
                return True
    return False


def elabora_messaggio(message, cfg):
    """Dispatcher unico: `config_json` → motore configurabile; altrimenti legacy.

    PIERO e ogni parser SENZA `config_json` restano su `parse_message`, byte per
    byte com'erano: il feed di produzione (`/xtrader.csv`) non cambia — e nessun
    test di questo repository sul contratto CSV di PIERO deve muoversi. I parser
    creati dalla web app scrivono `config_json` e girano su `esegui_parser`.

    Un solo punto d'ingresso per il webhook e per la rotta di prova, cosi' la
    regola «guarda `complete`, non `matched`» (un messaggio riconosciuto ma senza
    evento non deve produrre una riga) vive in un posto solo.

    Restituisce `{'event', 'csv'}` come `parse_message`, oppure None quando non
    c'e' segnale: condizione non soddisfatta, obbligatoria mancante, o
    `config_json` non decodificabile. None non solleva: e' l'`ignored:
    parser_no_match` dell'handler, non un 500. Dal #35 pezzo 2 `csv` puo'
    essere la LISTA dei documenti (piu' righe generate da `config.multi`):
    ogni chiamante la passa a `store_signal`, che accetta entrambe le forme.
    """
    return esito_messaggio(message, cfg)[0]


def esito_messaggio(message, cfg, risolvi_mappa=None):
    """`(parsed, motivi)`: il segnale se c'e', e PERCHE' no quando non c'e'.

    `elabora_messaggio` e' questa funzione senza il secondo valore, e i suoi tre
    chiamanti restano com'erano. Il motivo serve al dispatch, che lo scrive in
    `message_logs`: prima quella riga diceva soltanto `parser_no_match`, cioe' il
    sintomo senza la causa — un parser che dalla PR 5 smette di scrivere perche'
    le sue obbligatorie sono tutte costanti, o perche' una quota e' fuori scala,
    si sarebbe fermato in silenzio. Segnalato come bloccante da Claude Fable 5 e
    come rischio da GPT-5.5 sulla PR #47: lo stop e' voluto, l'invisibilita' no.
    """
    grezzo = cfg.get('config_json')
    if not grezzo:
        # Legacy: dal giudizio comune arrivano sia il segnale sia i motivi (audit
        # #81, C1/C2) — prima qui c'era `[]` fisso e lo scarto era muto.
        return _giudica_legacy(message, cfg)
    # TUTTO il percorso del motore sta sotto un solo try, e la cattura e' larga di
    # proposito. `config_json` la scrive l'utente (dalla web app): un JSON valido ma
    # malformato per il motore — senza `columns`, con `match` non-dict, un `pattern`
    # o un valore non-stringa, una struttura che fa sollevare `esegui_parser` — non
    # deve MAI diventare un 500. Telegram ritenta i 500 in loop, e — la ragione che
    # conta — la config storta di UN cliente non deve poter rompere l'elaborazione
    # per TUTTI gli altri profili dello stesso bot. Vale «parser_no_match»: nessun
    # segnale per quel cliente, il worker libero per gli altri. Segnalato da GPT-5.5
    # e Claude Fable 5 sulla PR #29 (isolamento «non deve bloccare a tutti»).
    try:
        config = json.loads(grezzo)
        # La sorgente squadre (#34 pezzo 3): il riferimento sta DENTRO la
        # config, quindi la mappa si risolve qui — ma con un RISOLUTORE del
        # chiamante, che e' chi possiede la connessione e l'utente. Senza
        # risolutore (i chiamanti legacy) o senza riferimento: verbatim.
        # Un risolutore che restituisce None (sorgente eliminata) idem.
        mappa = None
        riferimento = config.get('team_source') if isinstance(config, dict) else None
        if riferimento is not None and risolvi_mappa is not None:
            mappa = risolvi_mappa(riferimento)
        risultato = esegui_parser(message, config, mappa)
        # Il multi-riga (#35 pezzo 2): l'autorita' sono le righe GENERATE, non
        # la base — senza `config.multi` la lista E' la base (una riga) e il
        # percorso resta byte per byte quello storico. Il segnale c'e' se
        # almeno una riga e' piazzabile; le rotte con lo scarto non lo
        # fermano (tranello 1) e il loro motivo viaggia negli avvisi, che il
        # dispatch scrive in `message_logs` — k su N, con la causa visibile.
        righe = risultato.get('righe') or []
        complete = [r for r in righe if r['complete']]
        if not complete:
            # Legacy (la sola base): i motivi sono i suoi scarti, senza
            # prefisso — il testo dei log non si muove. Con le righe di
            # override il motivo dice QUALE riga, o la diagnosi manderebbe
            # a correggere la regola sbagliata.
            motivi = list(risultato.get('scarti') or [])
            if not (len(righe) == 1 and (righe[0].get('scarti') or []) == motivi):
                motivi = [f'riga {i}: {s}' for i, r in enumerate(righe, 1)
                          for s in (r.get('scarti') or [])]
            # Se il segnale cade per obbligatorie MANCANTI (non per uno scarto), il
            # motivo restava vuoto e il dispatch scriveva il generico
            # `parser_no_match`: la perdita del segnale era silenziosa (Issue #86,
            # dalla review #85). Come il percorso legacy (#84 C1/C2), qui si nomina
            # QUALE colonna manca, con la STESSA formula, cosi' la causa arriva in
            # `message_logs`. Solo quando non c'e' gia' uno scarto a spiegarlo.
            if not motivi:
                for i, r in enumerate(righe, 1):
                    prefisso = '' if len(righe) == 1 else f'riga {i}: '
                    motivi += [f'{prefisso}{c}: colonna obbligatoria vuota nel segnale'
                               for c in (r.get('missing') or [])]
            return None, motivi
        # `esegui_parser` puo' lasciare valori non-stringa nella riga (una costante
        # JSON `0`/`False`): nel CSV vanno nella forma CANONICA (`_testo_canonico`,
        # cioe' come li scrive `String()` in `toCsv`), non con `str()` di Python —
        # una costante `0.000001` usciva `1e-06` nel feed e `0.000001`
        # nell'anteprima, un booleano `True` contro `true`: XTrader legge il
        # feed, il cliente giudica l'anteprima, e i byte devono coincidere.
        # E' anche il testo su cui la guardia numerica ha dato il verdetto.
        # [REAL_FINDING] di GPT-5.6 Sol al gate finale della PR #47.
        documenti = []
        for r in complete:
            row = [_testo_canonico(v) for v in r['row']]
            documenti.append(make_csv(row))
        evento = ([_testo_canonico(v) for v in complete[0]['row']]
                  [HEADERS.index('EventName')])
        # Una riga sola = la stringa di sempre (i consumatori storici non
        # cambiano); piu' righe = la LISTA, il contratto d'ingresso di
        # `store_signal` dal #35 pezzo 1.
        csv_riga = documenti[0] if len(documenti) == 1 else documenti
        # Ogni riga NON piazzabile di un segnale scritto lascia il SUO motivo:
        # gli scarti delle guardie, oppure — quando cade per `missing`, che
        # scarti non produce — le obbligatorie mancanti. Senza il secondo
        # ramo la riga spariva in silenzio (rischio segnalato da GPT-5.5
        # sulla PR #69).
        avvisi_righe = []
        for i, r in enumerate(righe, 1):
            if r['complete']:
                continue
            avvisi_righe.extend(f'riga {i}: {s}' for s in (r.get('scarti') or []))
            if not (r.get('scarti') or []) and (r.get('missing') or []):
                avvisi_righe.append(
                    f'riga {i}: colonne obbligatorie mancanti: '
                    + ', '.join(r['missing']))
    except Exception:  # noqa: BLE001 - fail-safe deliberato, vedi commento sopra
        logging.getLogger('xtrader.relay').warning(
            'config parser non elaborabile: nessun segnale prodotto')
        # Il motivo esiste solo se il parser RICONOSCE il messaggio: senza
        # questo gate una config rotta produceva «config non eseguibile» per
        # QUALUNQUE messaggio della chat, e il dispatch archiviava tutto il
        # traffico in `message_logs` attribuendolo a quel parser — la stessa
        # classe chiusa in `esegui_parser` per gli scarti numerici, riaperta
        # nel ramo d'errore. La condizione si rivaluta in un try a parte:
        # potrebbe essere proprio lei a non essere eseguibile, e in quel caso
        # vale il silenzio, come per il JSON illeggibile — la diagnosi resta
        # sulla rotta di prova, che risponde `errore: config non eseguibile`
        # comunque. [REAL_FINDING] di Claude Fable 5 al gate finale, PR #47.
        try:
            riconosciuto = condizione_soddisfatta(
                message, json.loads(grezzo).get('match'),
                time.monotonic() + REGEX_BUDGET_PARSER_S)
        except Exception:  # noqa: BLE001 - config illeggibile: silenzio
            riconosciuto = False
        return None, (['config non eseguibile'] if riconosciuto else [])
    # `avvisi` viaggia nel segnale (#34 pezzo 3): e' il «verbatim + avviso»
    # della squadra senza alias, e il dispatch lo scrive in `message_logs` —
    # l'unico posto dove il cliente lo vede sul traffico vero. Dal #35
    # pezzo 2 porta anche le righe SCARTATE di un segnale scritto (k su N):
    # il feed esce con le k buone, e le altre non spariscono in silenzio.
    return {'event': evento, 'csv': csv_riga,
            'avvisi': list(risultato.get('avvisi') or []) + avvisi_righe}, []


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


CAMPI_PARSER = ('name', 'header', 'market_name', 'market_type', 'selection_name',
                'handicap', 'bet_type', 'config_json')


def parser_se_esiste(c, name):
    """La configurazione di un parser, o `None` se non c'e'. Non solleva.

    Fonte unica della lettura (regola 3): `get_parser` ci aggiunge solo il 404, e
    il percorso di consegna la usa **al posto** di un controllo di esistenza
    seguito dalla lettura vera. Quelle erano due query, e fra le due c'era una
    finestra: una cancellazione concorrente le passava in mezzo e la seconda
    sollevava 404 lo stesso — cioe' il retry-loop di Telegram che la guardia
    esiste per chiudere. `[REAL_FINDING]` di GPT-5.6 Sol, PR #46.
    """
    r = c.execute(f'SELECT {",".join(CAMPI_PARSER)} FROM parsers WHERE name=?',
                  (name,)).fetchone()
    return dict(zip(CAMPI_PARSER, r)) if r else None


def get_parser(c, name):
    cfg = parser_se_esiste(c, name)
    if cfg is None:
        raise HTTPException(404, 'Parser non trovato')
    return cfg


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


def _intestazioni_feed(nome_scaricato):
    """Le intestazioni della consegna CSV: niente cache, e il nome del download.

    `Content-Disposition: attachment; filename=…` decide SOLO come un browser
    chiama il file salvato (#60: betrelay, non xtrader): XTrader interroga
    l'URL e legge i byte del corpo, un header in piu' non gli cambia niente —
    URL, status, content-type e corpo restano identici, vincolati dai test.

    Il nome viene RIPULITO a `[A-Za-z0-9._-]` prima di entrare nell'header, e
    non e' pignoleria: il valore viaggia in latin-1, e un nome profilo con
    virgolette, CRLF o caratteri non-ASCII produrrebbe un header rotto — o un
    500 proprio sul percorso di consegna che XTrader interroga a raffica.
    Fonte unica (regola 3) per i quattro siti di risposta del feed: due qui
    sotto in `profile_csv`, due in `feed_utente_csv`.
    """
    pulito = re.sub(r'[^A-Za-z0-9._-]+', '-', nome_scaricato)
    return {'Cache-Control': 'no-store',
            'Content-Disposition': f'attachment; filename="{pulito}"'}


def profile_csv(profile, token, nome_scaricato=None):
    """Il feed di un profilo. `nome_scaricato` e' il nome che un browser da' al
    file (#60): lo passa la rotta, perche' `/xtrader.csv` scarica `betrelay.csv`
    mentre `/profiles/{p}.csv` scarica `betrelay-{p}.csv` — stessa funzione,
    nomi diversi. Il default copre i chiamanti che non se ne curano."""
    if nome_scaricato is None:
        nome_scaricato = f'betrelay-{profile}.csv'
    auth(token)
    c = db()
    get_profile(c, profile)
    # Accesso scaduto o sospeso: **sola intestazione**, e `200`. Non `401`, che per XTrader
    # e' un guasto da segnalare invece di «nessun segnale»; non un feed vuoto senza BOM, che
    # e' un CSV rotto. Il token **non** viene revocato: «scaduto» e «revocato» sono stati
    # diversi, e revocare costringerebbe il cliente a riconfigurare XTrader a ogni rinnovo
    # (Issue #2). Cosi' il rinnovo e' istantaneo: cambia una data e il feed riprende.
    bloccato = accesso_bloccato_del_profilo(c, profile)
    if bloccato:
        c.close()
        return Response(empty_csv(), media_type='text/csv',
                        headers=_intestazioni_feed(nome_scaricato))
    # Stessa forma del feed per utente, per la stessa ragione (regola 2): il poll
    # e' una lettura, il TTL sta nel filtro, la pulizia la fa `store_signal` alla
    # scrittura successiva. Prima qui c'era DELETE + commit a ogni interrogazione.
    # TUTTE le righe vive, in ordine di scrittura (#35 pezzo 1): un messaggio
    # multi-riga ne inserisce N nello stesso commit, e il feed le serve tutte,
    # composte da `componi_feed` in un documento solo.
    righe = c.execute("SELECT csv FROM signals WHERE profile=?"
                      " AND (expires_at IS NULL OR expires_at > strftime('%s','now'))"
                      ' ORDER BY id', (profile,)).fetchall()
    c.close()
    # store_signal() verifica cio' che SCRIVE, ma una riga finita nel database da
    # una versione precedente e' gia' la' e uscirebbe cosi' com'e' — senza BOM,
    # per i secondi che le restano. Qui si serve il feed vuoto invece del
    # contenuto sospetto: e' sempre un CSV valido e XTrader non va in errore.
    #
    # E' l'UNICA verifica sul percorso di consegna, ed e' innocua per costruzione
    # perche' non puo' produrre un errore: al massimo degrada a «nessun segnale».
    # Un raise qui diventerebbe un 500 verso XTrader.
    validi = []
    for (documento,) in righe:
        try:
            validi.append(verify_csv(documento))
        except ValueError as e:
            # Il motivo, mai il contenuto: i messaggi di verify_csv() sono
            # strutturali (conteggi, posizioni, numeri di riga) e /health e' un
            # endpoint senza token. Il nome del profilo resta nel log del server,
            # dove serve per la diagnosi e dove non e' un segreto: e' gia' nell'URL.
            # Il log segue il contatore: righe identiche a ogni richiesta per 90
            # secondi renderebbero illeggibile proprio il log che serve a capire.
            # Il log sta fuori dal lock di proposito: non si tiene un lock durante
            # l'I/O. Una riga guasta degrada SOLO se stessa (#35): le altre
            # righe vive del feed continuano a uscire.
            if _registra_scarto(profile, documento, e):
                logging.getLogger('xtrader.relay').warning(
                    'riga del feed del profilo %s scartata dalla verifica: %s', profile, e)
    return Response(componi_feed(validi), media_type='text/csv',
                    headers=_intestazioni_feed(nome_scaricato))


@app.get('/', include_in_schema=False)
def root():
    """La facciata di BetRelay: una pagina, non un oggetto JSON.

    Fino a questa versione l'apex rispondeva
    `{'service': 'xtrader-signal-relay', ...}`. Corretto per una sonda, inutile
    per una persona: chi apriva betrelay.net vedeva il JSON e non un sito.

    **Rotta esplicita, e non un catch-all** `@app.get('/{resto:path}')` — la forma
    che si scrive di solito per servire un sito. Quella trasforma ogni percorso
    sconosciuto in una risposta valida: il giorno che nasce `/feed/{utente}.csv`,
    XTrader riceverebbe `text/html` con stato 200 al posto di un CSV, senza un
    errore da nessuna parte. Misurato: con quel catch-all al posto di questa
    rotta, quattro casi di `tests/relay/test_facciata.py` diventano rossi con
    «risponde 200 invece di 404».

    Se il file manca — un deploy senza `web/` — si torna al JSON di prima invece
    di rispondere 500: `/` e' la prima cosa che si prova quando qualcosa non va,
    ed e' la peggiore su cui restituire un errore del server.

    `no-store` perche' il sito e' in avviamento e cambia a ogni deploy: una cache
    di pochi minuti qui si paga in «ho pubblicato e non vedo la modifica», che e'
    il modo piu' rapido di inseguire un guasto che non esiste.
    """
    if SITO.is_file():
        return FileResponse(SITO, media_type='text/html',
                            headers={'Cache-Control': 'no-store'})
    return JSONResponse({'service': 'xtrader-signal-relay', 'status': 'online',
                         'csv': '/xtrader.csv'})


@app.get('/admin')
def scorciatoia_admin():
    """La porta di servizio del proprietario (#57): betrelay.net/admin → il pannello.

    SOLO un redirect, senza serratura propria: la protezione resta il login piu'
    il **404 server-side** di `/api/admin/*` per chi non e' amministratore — un
    estraneo che segue il link atterra sul login o sulla propria dashboard e non
    vede nulla. Trade-off dichiarato al proprietario e accettato: `/admin` e' il
    primo percorso che i bot tentano, e la sua esistenza conferma che un'area
    admin c'e'; la sicurezza non dipende dal segreto dell'URL.

    Rotta esplicita come la facciata, mai un catch-all (stessa guardia:
    `tests/relay/test_facciata.py`).
    """
    return RedirectResponse('/app/#/richieste')


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
    # Una quota nella forma localizzata (#40): cosi' /health esercita anche il
    # controllo del separatore, non solo struttura e quoting.
    sample[HEADERS.index('Price')] = '1,85'
    try:
        verify_csv(empty_csv())
        verify_csv(make_csv(sample))
        # Il contratto multi-riga (#35 pezzo 1): due segnali vivi composti da
        # `componi_feed` devono passare come un documento solo — /health
        # esercita anche la composizione, non solo la riga singola.
        verify_csv(componi_feed([make_csv(sample), make_csv(sample)]))
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
    # Il nome del download e' `betrelay.csv` senza suffisso (#60): questo alias
    # E' il feed storico del servizio, non «il profilo PIERO visto da un URL».
    return profile_csv(PIERO_PROFILE, token, nome_scaricato='betrelay.csv')

@app.get('/profiles/{profile}.csv')
def named_profile_csv(profile: str, token: str | None = Query(None)):
    return profile_csv(profile, token, nome_scaricato=f'betrelay-{profile}.csv')


def hash_token_feed(token):
    """L'impronta con cui un token di feed vive sul server: sha256 esadecimale.

    Fonte unica per chi CONIA (`genera_token_feed`) e per chi VERIFICA
    (`feed_utente_csv`): due formule divergerebbero al primo ritocco, e il
    sintomo sarebbe «nessun token apre piu' nessun feed» senza un errore.

    sha256 semplice e non scrypt, ed e' una scelta con un motivo: il token e'
    generato dal server con `secrets.token_urlsafe(24)` — 192 bit di caso — non
    scelto da una persona. Un derivatore lento serve contro i dizionari, e su
    192 bit casuali un dizionario non esiste; qui conta che la verifica sia
    economica, perche' XTrader interroga il feed a raffica.
    """
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


@app.get('/feed/{slug}.csv')
def feed_utente_csv(slug: str, token: str | None = Query(None)):
    """Il feed di UN utente: `/feed/{slug}.csv?token=xt_…` (Issue #2, PR «un feed per utente»).

    Ogni fallimento e' **404**, sempre lo stesso: slug inesistente, token
    assente, token sbagliato, token di un altro utente, feed non ancora armato.
    Un 401 su uno slug esistente direbbe a chi enumera «questo cliente esiste,
    cerca il token»; il 404 uniforme non conferma niente. E' la stessa ragione
    del 404 sui parser altrui, applicata all'unica rotta pubblica per-utente.

    Il token e' dell'UTENTE, non il `CSV_ACCESS_TOKEN` condiviso: qui `auth()`
    non c'entra, e un servizio senza quella variabile serve comunque i feed
    per-utente — il fail-closed di `auth()` protegge le rotte che usano il
    token condiviso, questa ha la sua serratura per riga di `users`.

    Alla scadenza dell'accesso: `200` con sola intestazione, il token NON si
    revoca — identico a `profile_csv`, ed e' contratto (Issue #2): per XTrader
    un errore HTTP e' un guasto, «nessun segnale» e' uno stato normale.
    """
    non_trovato = HTTPException(404, 'Not Found')
    if not token:
        raise non_trovato
    # `try/finally` e non chiusure per ramo: questo e' il percorso che XTrader
    # interroga a raffica, e un'eccezione fra l'apertura e la chiusura — un
    # `database is locked` sulla DELETE, per dire — lascerebbe una connessione
    # aperta A OGNI poll. Segnalato da CodeRabbit sulla PR #43; `genera_token_feed`
    # aveva gia' la stessa forma per la stessa ragione.
    c = db()
    try:
        riga = c.execute('SELECT id, token_hash, status, access_expires_at, is_admin'
                         ' FROM users WHERE slug=?', (slug,)).fetchone()
        if riga is None or not riga[1]:
            raise non_trovato
        utente, atteso, status, scadenza, admin = riga
        if not secrets.compare_digest(hash_token_feed(token).encode('utf-8'),
                                      atteso.encode('utf-8')):
            raise non_trovato
        # L'abbonamento: stessa decisione di `accesso_bloccato_del_profilo`, via
        # la stessa `_blocco_della_riga` — qui l'utente C'E', non serve il ponte.
        if _blocco_della_riga(status, scadenza, admin):
            return Response(empty_csv(), media_type='text/csv',
                            headers=_intestazioni_feed(f'betrelay-{slug}.csv'))
        # La consegna e' una LETTURA: il TTL vive nel filtro, non in una DELETE.
        # La prima versione cancellava le righe scadute qui, come `profile_csv` di
        # allora — cioe' una transazione di SCRITTURA per ogni poll, anche a vuoto,
        # e XTrader interroga a raffica: su N clienti sono N scritture al secondo
        # che serializzano sul write-lock di SQLite, in contesa con il webhook.
        # Bloccante di GPT-5.6 Sol sulla PR #43. La pulizia spetta a
        # `store_signal`, che cancella per entrambe le chiavi alla scrittura
        # successiva: resta al piu' una riga scaduta per utente, invisibile a
        # questo filtro. Vincolato dal test sul database di sola lettura.
        # TUTTE le righe vive in ordine di scrittura (#35 pezzo 1), come in
        # `profile_csv`: un messaggio multi-riga ne inserisce N nello stesso
        # commit e il feed le compone in un documento solo.
        righe = c.execute("SELECT csv FROM signals WHERE user_id=?"
                          " AND (expires_at IS NULL OR expires_at > strftime('%s','now'))"
                          ' ORDER BY id', (utente,)).fetchall()
    finally:
        c.close()
    # Stesso fallback di `profile_csv`, per la stessa ragione: una riga scritta
    # da una versione precedente non deve uscire rotta, e un raise sul percorso
    # di consegna sarebbe un 500 verso XTrader. Lo slug come etichetta del log:
    # e' gia' nell'URL, non e' un segreto. Una riga guasta degrada SOLO se
    # stessa (#35): le altre righe vive continuano a uscire.
    validi = []
    for (documento,) in righe:
        try:
            validi.append(verify_csv(documento))
        except ValueError as e:
            if _registra_scarto(slug, documento, e):
                logging.getLogger('xtrader.relay').warning(
                    'riga del feed dell\'utente %s scartata dalla verifica: %s', slug, e)
    return Response(componi_feed(validi), media_type='text/csv',
                    headers=_intestazioni_feed(f'betrelay-{slug}.csv'))

# Il nome del cookie di sessione, in un posto solo perche' lo leggono in quattro.
NOME_COOKIE = 'betrelay_sessione'

# I tentativi falliti sul percorso a password, con il momento dell'ultimo. In memoria
# e non nel database: e' un freno, non un dato, e il servizio gira in un processo solo
# (`Procfile` senza `--workers`, misurato).
#
# Il freno e' GLOBALE e non per IP, ed e' una scelta con un baratto dichiarato. Per IP
# non frenerebbe nulla: chi prova una password in automatico cambia indirizzo. Globale
# invece si', al prezzo che un estraneo puo' tenere occupato il percorso a password per
# qualche minuto. Il prezzo e' accettabile **perche' esistono due porte**: il
# proprietario entra col login Telegram mentre quella a password e' frenata. E' la
# ragione tecnica per cui averne due non e' ridondanza.
_TENTATIVI_PASSWORD = {'falliti': 0, 'ultimo': 0.0}
_LOCK_TENTATIVI = threading.Lock()

# Dopo quanti tentativi falliti il percorso si chiude, e per quanto.
TENTATIVI_PRIMA_DEL_FRENO = 5
DURATA_FRENO = 300


def _prenota_tentativo():
    """`0` se il tentativo puo' partire — e in quel caso lo **conta** — altrimenti i secondi.

    Controllo e conteggio sono **un solo gesto**, dentro un solo lock, e questa e' la
    correzione: prima il controllo prendeva il lock, leggeva e lo rilasciava, poi arrivava
    `scrypt` — ~100 ms di CPU — e solo alla fine il conteggio. Fra la lettura e
    l'incremento passava tutto il calcolo, quindi N richieste concorrenti vedevano tutte
    «zero tentativi falliti» e passavano tutte.

    Due conseguenze, e la seconda e' peggiore: il limite di cinque tentativi si aggirava
    mandando le richieste **insieme** invece che in fila, e ogni richiesta che passava
    accendeva uno `scrypt` — cioe' il freno che doveva proteggere la password diventava un
    amplificatore di carico, con un `for` di shell a metterlo in ginocchio. Segnalato da
    GPT-5.6 Sol sulla PR #23.

    Il tentativo si conta **prima** della verifica, ottimisticamente, e si azzera solo in
    caso di successo (`_azzera_tentativi`): e' la forma «consuma un gettone prima di
    lavorare», e mette un tetto anche al numero di `scrypt` concorrenti.
    """
    with _LOCK_TENTATIVI:
        if _TENTATIVI_PASSWORD['falliti'] >= TENTATIVI_PRIMA_DEL_FRENO:
            resto = DURATA_FRENO - (time.time() - _TENTATIVI_PASSWORD['ultimo'])
            if resto > 0:
                return int(resto) + 1
            # Finestra scaduta: si ricomincia a contare, altrimenti il primo blocco
            # sarebbe definitivo e servirebbe un riavvio per rientrare.
            _TENTATIVI_PASSWORD['falliti'] = 0
        _TENTATIVI_PASSWORD['falliti'] += 1
        _TENTATIVI_PASSWORD['ultimo'] = time.time()
        return 0


def _azzera_tentativi():
    """Dopo un accesso riuscito il contatore torna a zero."""
    with _LOCK_TENTATIVI:
        _TENTATIVI_PASSWORD['falliti'] = 0


def _annota_admin(c, chi, azione, bersaglio=None):
    """Una riga in `admin_audit`. Senza, «non sono stato io» non e' dimostrabile."""
    c.execute('INSERT INTO admin_audit(admin_user_id, target_user_id, action)'
              ' VALUES (?,?,?)', (chi, bersaglio, azione))


# Le chiavi delle impostazioni globali. Il canale di backup (#56 pezzo 2) e' salvato
# in due tempi: prima un CANDIDATO — proposto dalla cattura via webhook — poi il
# canale CONFIGURATO, che la conferma scrive solo dopo un invio di prova riuscito.
CHIAVE_CANALE_BACKUP_ID = 'canale_backup_chat_id'
CHIAVE_CANALE_BACKUP_TITOLO = 'canale_backup_titolo'
CHIAVE_CANALE_CANDIDATO_ID = 'canale_backup_candidato_chat_id'
CHIAVE_CANALE_CANDIDATO_TITOLO = 'canale_backup_candidato_titolo'
# PREFISSO della chiave dell'`update_id` piu' alto gia' processato, tenuto PER CANALE come
# `canale_backup_ultimo_update_id:<chat_id>` (#56, pezzo idempotenza). Gli `update_id` di
# Telegram crescono in modo monotono, quindi un evento con id <= a quello del suo canale e'
# una riconsegna o un fuori-ordine (una promozione tardiva dopo una rimozione piu' nuova dello
# STESSO canale) e va ignorato. Per-canale e non globale: l'ordine di un canale non deve
# sopprimere gli eventi di un altro (bloccante di Fable, gate finale #56).
CHIAVE_CANALE_ULTIMO_UPDATE = 'canale_backup_ultimo_update_id'


def leggi_impostazione(c, chiave, default=None):
    """Il valore di un'impostazione globale, o `default` se non c'e'."""
    riga = c.execute('SELECT valore FROM impostazioni WHERE chiave=?', (chiave,)).fetchone()
    return riga[0] if riga else default


def scrivi_impostazione(c, chiave, valore):
    """Imposta (o sovrascrive) un'impostazione globale. Non committa: lo fa il chiamante."""
    c.execute('INSERT INTO impostazioni(chiave, valore) VALUES (?,?)'
              ' ON CONFLICT(chiave) DO UPDATE SET valore=excluded.valore', (chiave, valore))


def cancella_impostazione(c, chiave):
    """Rimuove un'impostazione globale. Non committa: lo fa il chiamante."""
    c.execute('DELETE FROM impostazioni WHERE chiave=?', (chiave,))


def utente_dalla_sessione(request):
    """L'utente della sessione, o `None`. **`user_id` viene DA QUI e mai dalla richiesta.**

    E' la regola non negoziabile di `CLAUDE.md` sull'isolamento, e questa funzione e' il
    solo posto da cui deve arrivare l'identita' di chi chiama: un `user_id` letto da un
    parametro o da un header e' un `user_id` scelto dal mittente.

    Tre controlli, e ognuno serve:

    - la **firma** del cookie (in `leggi_sessione`): senza, `utente=7` diventa
      `utente=8` con un editor di testo;
    - la **scadenza** per inattivita' (anche in `leggi_sessione`);
    - `session_version` confrontata con quella nel database: e' il modo di invalidare
      una sessione **subito**, senza aspettare i venti minuti. Serve per «entra come
      cliente» e per buttare fuori un accesso sospetto. Senza questo confronto, un
      cookie rubato resterebbe valido fino alla scadenza naturale e non ci sarebbe
      niente da fare.

    Restituisce anche `versione`, che serve a chi deve **rinnovare** il cookie: vedi
    `_rispondi_con_sessione`.
    """
    sessione = leggi_sessione(request.cookies.get(NOME_COOKIE))
    if not sessione:
        return None
    c = db()
    # L'invariante dell'amministratore vale ANCHE qui, e non solo al login: senza, cambiare
    # `TELEGRAM_ADMIN_ID` non toglieva niente a chi aveva gia' una sessione aperta, che non
    # scade perche' ogni richiesta valida rinnova il cookie. Ultimo bloccante di GPT-5.6 Sol
    # sulla PR #24. Va PRIMA della lettura della riga, cosi' la `session_version` letta e'
    # quella incrementata e questa stessa richiesta cade.
    if revoca_identita_stantia(c) is not None:
        c.commit()
    riga = c.execute('SELECT id, session_version, status, is_admin, first_name,'
                     ' access_expires_at, slug, token_prefix FROM users WHERE id=?',
                     (sessione['utente'],)).fetchone()
    c.close()
    if not riga or riga[1] != sessione['versione']:
        return None
    # `slug` e `token_prefix` servono a `/api/me` e a `genera_token_feed`: il
    # prefisso NON e' il token — sono i primi 9 caratteri, quelli che la UI
    # mostra per riconoscere quale token e' armato — e lo slug e' gia' nell'URL
    # del feed, quindi nessuno dei due e' un segreto.
    return {'id': riga[0], 'versione': riga[1], 'status': riga[2],
            'is_admin': bool(riga[3]), 'first_name': riga[4],
            'access_expires_at': riga[5], 'slug': riga[6], 'token_prefix': riga[7]}


def _rispondi_con_sessione(utente, versione, corpo=None, risposta=None):
    """La risposta che apre **o rinnova** una sessione, col cookie impostato come si deve.

    `httponly` perche' un cookie leggibile da JavaScript e' un cookie che un XSS porta
    via. `samesite='lax'` perche' una POST da un altro sito non deve portarsi dietro la
    sessione. `secure` perche' il servizio sta su HTTPS e il cookie non deve mai
    viaggiare in chiaro — betrelay.net e' dietro TLS, quindi non c'e' niente da perdere.

    **Il rinnovo non e' un dettaglio: senza, i venti minuti non sono di inattivita'.**
    `firma_sessione` mette dentro il momento dell'emissione, quindi un cookie mai
    riemesso scade venti minuti dopo il LOGIN — e il proprietario si troverebbe buttato
    fuori ogni venti minuti mentre sta lavorando. Fino al 12/08/2026 questa funzione era
    chiamata solo dalle due rotte di login, mentre il docstring di `firma_sessione` e
    `SAAS.md` promettevano il contrario: segnalato indipendentemente da GPT-5.5 e da
    Claude Fable 5 sulla PR #23. Una promessa scritta e non mantenuta e' peggio di una
    funzione mancante, perche' chi legge la doc smette di cercare.

    Quindi ogni rotta che valida una sessione la richiama. **Per-rotta e non un
    middleware, di proposito:** un middleware girerebbe anche su `/xtrader.csv`, cioe'
    metterebbe codice di sessione sul percorso del feed — esattamente la NON-relazione
    che questo PR esiste per garantire (`test_la_sessione_scaduta_NON_tocca_il_feed`).
    """
    valore = firma_sessione(utente, versione)
    if not valore:
        # Senza bot token il segreto e' vuoto e `firma_sessione` non firma niente. Prima
        # questa funzione usciva comunque `200 {'ok': true}` con `betrelay_sessione=""`:
        # il login SEMBRAVA riuscito e ogni richiesta successiva rispondeva 401, senza
        # niente da nessuna parte che dicesse perche'. E' la forma peggiore del
        # fail-open — non apre una porta, apre una porta finta — e coglie chi ha appena
        # configurato la password per entrare, cioe' l'emergenza per cui esiste quel
        # percorso. Segnalato da CodeRabbit sulla PR #23.
        raise HTTPException(503, 'sessioni non configurate: manca TELEGRAM_BOT_TOKEN')
    # `risposta` esplicita per rinnovare il cookie su una risposta gia' costruita che
    # NON e' JSON — un download (#56), per esempio: la guardia
    # `test_ogni_rotta_che_usa_la_SESSIONE_rinnova_anche_il_cookie` pretende che ANCHE
    # quelle rotte passino da qui, e la sessione dell'admin non deve scadere solo perche'
    # l'ultima cosa che ha fatto e' scaricare un file. Senza, si torna a `JSONResponse`.
    if risposta is None:
        risposta = JSONResponse(corpo)
    risposta.set_cookie(NOME_COOKIE, valore,
                        httponly=True, samesite='lax', secure=True,
                        max_age=INATTIVITA_MASSIMA, path='/')
    return risposta


class LoginTelegramIn(BaseModel):
    """I campi che il Login Widget consegna. `hash` e' la firma, non un dato.

    `extra='allow'` non e' permissivita': e' l'unico modo di restare compatibili.
    Telegram firma **tutti** i campi che manda, compresi quelli che noi non conosciamo,
    e Pydantic per default li scarta — quindi la `data_check_string` che ricostruiamo
    sarebbe piu' corta di quella firmata e la firma non combacerebbe. Il giorno che
    Telegram aggiunge un campo (l'ha gia' fatto con `photo_url`), **tutti i login veri
    verrebbero rifiutati**, e il sintomo sarebbe «il login non funziona piu'» senza un
    errore da nessuna parte. Segnalato da CodeRabbit sulla PR #23.

    Il verso opposto resta chiuso: accettare i campi sconosciuti non apre niente, perche'
    entrano nella stringa firmata. Un campo aggiunto **dopo** la firma la invalida — lo
    verifica `test_un_campo_AGGIUNTO_dopo_la_firma_viene_rifiutato`.

    `coerce_numbers_to_str` perche' il widget consegna `id` e `auth_date` come **numeri**
    JSON, e Pydantic v2 non converte un numero in stringa: misurato, `id=12345678`
    solleva `ValidationError`, quindi la rotta rispondeva `422` a **ogni** login reale —
    non a qualcuno, a tutti, dal primo. Nessuno dei 519 test lo raggiungeva, perche' li' i
    campi li costruisco io e li costruisco come stringhe: il codice era coerente con se
    stesso e sbagliato rispetto al mondo. Alzato dai due gate finali — Fable 5 e
    GPT-5.6 Sol — contemporaneamente, sulla PR #23.
    """

    model_config = ConfigDict(extra='allow', coerce_numbers_to_str=True)

    id: str
    auth_date: str
    hash: str
    first_name: str = ''
    last_name: str = ''
    username: str = ''
    photo_url: str = ''


class LoginPasswordIn(BaseModel):
    username: str
    password: str


@app.post('/api/login/telegram')
def login_telegram(data: LoginTelegramIn):
    """Apre una sessione a chi presenta una firma valida del Login Widget.

    I campi vuoti vengono **esclusi** dal calcolo: Telegram firma soltanto quelli che
    manda, e il modello Pydantic riempie di stringhe vuote quelli assenti. Includerli
    cambierebbe la `data_check_string` e farebbe fallire ogni login vero — un difetto
    che si vedrebbe solo in produzione, perche' un test che costruisce i campi a mano
    li manderebbe tutti.
    """
    if not verifica_login_telegram(campi_firmati(data.model_dump()), BOT_TOKEN):
        # Il messaggio non distingue «firma sbagliata» da «scaduta»: per differenza si
        # impara, e chi prova non deve sapere quale dei due muri ha toccato.
        raise HTTPException(401, 'login non valido')

    c = db()
    # `BEGIN IMMEDIATE`: da qui alla `commit()` si legge e si scrive dentro UNA transazione,
    # e SQLite serializza chi arriva insieme. Senza, fra la `SELECT` e le `UPDATE` c'e' spazio
    # per un altro login: misurato, due login concorrenti che cambiano identita' incrementano
    # `session_version` due volte, e **un cookie su sei nasce morto** — il login «riesce», la
    # richiesta successiva risponde 401, e chi lo subisce non ha modo di capire perche'.
    # Alzato da Claude Fable 5 e da GPT-5.6 Sol indipendentemente sulla PR #24.
    # `BEGIN IMMEDIATE` sta DENTRO il `try`: sotto contesa quel comando stesso solleva
    # `database is locked`, e da fuori nessuno chiuderebbe la connessione — ogni login perso
    # per lock perderebbe un descrittore, su un container che non riparte mai. Rilievo di
    # Claude Fable 5 sulla PR #24.
    try:
        c.execute('BEGIN IMMEDIATE')
        utente, versione = _decidi_identita(c, data)
        c.commit()
    except HTTPException:
        # Un `HTTPException` da qui e' una DECISIONE, non un guasto: il rifiuto per
        # configurazione incoerente, o il 503 dell'account non creato. Si conferma, perche'
        # dentro la transazione c'e' la riga di `admin_audit` che traccia il rifiuto — e la
        # prima versione di questo blocco faceva `rollback` su tutto, quindi **cancellava la
        # traccia della propria decisione**: il proprietario riceveva un login fallito e
        # nessun posto dove leggere perche'. Misurato da un test che pretendeva quella riga.
        c.commit()
        c.close()
        raise
    except Exception:
        # Tutto il resto e' un guasto: la transazione tocca l'identita' di due utenti, e uno
        # stato scritto a meta' sarebbe peggio di un login rifiutato.
        c.rollback()
        c.close()
        raise
    c.close()
    return _rispondi_con_sessione(utente, versione, {'ok': True, 'utente': utente})


def revoca_identita_stantia(c):
    """Scioglie il collegamento fra la riga del proprietario e un'identita' non piu' quella
    configurata. Restituisce l'id della riga toccata, o `None` se non c'era niente da fare.

    E' l'invariante dell'amministratore ridotta a una funzione sola, perche' va applicata in
    **due** posti e la regola 3 di `CLAUDE.md` vale anche qui:

    - al login (`_decidi_identita`, CASO 1);
    - **su ogni richiesta autenticata del sito** (`utente_dalla_sessione`).

    Il secondo posto e' l'ultimo bloccante di GPT-5.6 Sol sulla PR #24, e il difetto era
    questo: applicandola solo al login, dopo aver cambiato `TELEGRAM_ADMIN_ID` la vecchia
    identita' perdeva l'accesso al prossimo login **di chiunque**, ma il cookie che aveva
    gia' in mano restava valido fino a quel momento — e non scadeva, perche' ogni richiesta
    valida rinnova il cookie, quindi una sessione tenuta aperta e' immortale. Nel caso per cui
    la revoca esiste — nella variabile e' finito l'ID di un estraneo, o l'account e'
    compromesso — l'estraneo col pannello aperto non ha nessun motivo di rifare login, quindi
    non perdeva niente. Ora la prima richiesta autenticata dopo il cambio scioglie il
    collegamento, e quella richiesta puo' essere proprio la sua: si chiude da se'.

    **Perche' una scrittura sul percorso di lettura non e' un problema qui.** Scatta solo
    quando la condizione e' vera, e dopo lo scioglimento `telegram_id` e' NULL: la condizione
    diventa falsa e non si ripete piu' (misurato in
    `test_la_revoca_dalla_SESSIONE_non_si_ripete_a_ogni_richiesta`). Riguarda solo le rotte
    del sito, mai `/xtrader.csv`, che non ha sessione — la NON-relazione fra sessione e feed
    resta intatta.

    **Ed e' sicura in corsa senza transazione:** l'`UPDATE` porta il valore stantio nella
    `WHERE`, quindi fra due richieste concorrenti solo una tocca una riga, e la riga di audit
    la scrive solo chi ha vinto. Senza quel `WHERE`, due richieste avrebbero incrementato
    `session_version` due volte e scritto due revoche per la stessa revoca — e la seconda
    butterebbe fuori anche una sessione nata DOPO la revoca. Misurato in
    `test_la_WHERE_anti_corsa_serve_DAVVERO`, che impone l'interleaving a mano: il test a sei
    thread passava anche **senza** la `WHERE`, perche' sei thread si serializzano abbastanza
    che il secondo rilegga `telegram_id` gia' NULL. Un docstring che afferma una proprieta'
    non misurata e' il difetto che questo repository ha gia' pagato una volta.
    """
    if not (TELEGRAM_ADMIN_ID and not admin_id_malformato()):
        return None
    riga = c.execute('SELECT id, telegram_id FROM users WHERE origin_profile=?',
                     (PIERO_PROFILE,)).fetchone()
    if not riga or not riga[1] or riga[1] == TELEGRAM_ADMIN_ID:
        return None
    cursore = c.execute('UPDATE users SET telegram_id=NULL,'
                        ' session_version=session_version+1'
                        ' WHERE id=? AND telegram_id=?', (riga[0], riga[1]))
    if not cursore.rowcount:
        return None
    # `is_admin` NON si tocca: quella riga resta l'account del proprietario, ed e' da
    # `is_admin` che dipende il suo accesso con la password. Cio' che si revoca e' il legame
    # con un'identita' Telegram, non la proprieta' dell'account.
    _annota_admin(c, riga[0], 'identita_telegram_revocata')
    return riga[0]


def _decidi_identita(c, data):
    """Chi e' chi sta entrando, applicando l'invariante dell'amministratore.

    Restituisce `(utente, versione)` da firmare nel cookie. Gira **dentro** la transazione
    aperta da chi chiama: legge, scrive, e non fa `commit` — cosi' un rifiuto a meta' non
    lascia niente.

    L'invariante e' una frase sola: **se `TELEGRAM_ADMIN_ID` e' configurato, la riga del
    proprietario porta QUEL `telegram_id`, o nessuno.** Da lei discendono tre casi, e i primi
    due chiudono un difetto misurato che i gate finali hanno trovato sulla PR #24:

    1. la riga porta un'identita' **diversa** da quella configurata → il collegamento e'
       stantio e si scioglie, **chiunque** stia entrando. Senza, cambiare la variabile non
       toglieva niente: la vecchia identita' continuava a entrare nell'account del
       proprietario finche' la nuova non faceva login, e se la nuova non entrava mai, per
       sempre. Cambiare `TELEGRAM_ADMIN_ID` e' il gesto con cui si toglie l'accesso a
       un'identita': se non lo toglie, quel gesto e' teatro. Bloccante di GPT-5.6 Sol;
    2. sta entrando l'identita' configurata e la sua riga non e' quella del proprietario →
       si collega, riconciliando la riga precedente **solo se non e' l'account di qualcuno**
       (vedi `possiede_qualcosa`), altrimenti **rifiuta**. Bloccante di Claude Fable 5;
    3. tutto il resto: cliente nuovo, oppure login successivo.
    """
    riga = c.execute('SELECT id, session_version FROM users WHERE telegram_id=?',
                     (data.id,)).fetchone()
    proprietario = c.execute('SELECT id, session_version, telegram_id FROM users'
                             ' WHERE origin_profile=?', (PIERO_PROFILE,)).fetchone()

    # Un valore malformato non descrive NESSUNA identita': applicare l'invariante su di esso
    # scioglie un collegamento buono senza poterne creare uno nuovo, perche' CASO 2 confronta
    # `data.id` con lo stesso valore e non combacia mai — quindi il proprietario resta fuori
    # dal proprio account e gliene nasce uno vuoto, per un refuso nel pannello Railway.
    # `admin_id_malformato()` esisteva gia' e segnalava soltanto, all'avvio: un controllo che
    # nomina il problema ma non lo previene. Trovato indipendentemente da GPT-5.6 Sol e da
    # CodeRabbit sulla PR #24, e misurato in
    # `test_un_ADMIN_ID_malformato_NON_scioglie_un_collegamento_BUONO`.
    admin_configurato = bool(TELEGRAM_ADMIN_ID) and not admin_id_malformato()

    # CASO 1 — il collegamento stantio si scioglie. La decisione e la scrittura stanno in
    # `revoca_identita_stantia()`, perche' la stessa invariante va applicata anche su ogni
    # richiesta autenticata del sito: due copie sarebbero due copie che divergono (regola 3).
    if revoca_identita_stantia(c) is not None:
        proprietario = c.execute('SELECT id, session_version, telegram_id FROM users'
                                 ' WHERE origin_profile=?', (PIERO_PROFILE,)).fetchone()
        # Chi stava entrando poteva essere proprio la vecchia identita', e in quel caso la
        # sua riga era quella del proprietario: adesso non lo e' piu'.
        riga = c.execute('SELECT id, session_version FROM users WHERE telegram_id=?',
                         (data.id,)).fetchone()

    e_amministratore = admin_configurato and data.id == TELEGRAM_ADMIN_ID

    # CASO 2 — l'identita' configurata si collega alla riga del proprietario.
    if e_amministratore and proprietario and (riga is None or riga[0] != proprietario[0]):
        if riga is not None:
            # Rifiuta invece di scegliere quale dei due utenti derubare. Il messaggio verso
            # chi chiama non nomina utenti ne' identificativi: chi lo riceve e' un cliente
            # qualunque, e il dettaglio va nel log e in `admin_audit`, che legge il
            # proprietario. Due motivi distinti, tracciati distinti, perche' il rimedio
            # differisce: correggere la variabile, oppure autorizzare l'assorbimento.
            if possiede_qualcosa(c, riga[0]):
                logging.getLogger('xtrader.relay').error(
                    "TELEGRAM_ADMIN_ID punta a un account che possiede dati (parser, chat,"
                    " libreria mercati o sorgenti squadre): collegamento RIFIUTATO per non"
                    " fondere due utenti. Correggere la variabile con l'ID Telegram del"
                    " proprietario."
                    " TELEGRAM_ADMIN_RECONCILE non autorizza questo caso: il consenso"
                    " riguarda una riga VUOTA, non i dati di un altro utente.")
                _annota_admin(c, proprietario[0], 'collegamento_admin_rifiutato',
                              bersaglio=riga[0])
                raise HTTPException(409, 'configurazione dell\'amministratore incoerente')
            if not riconciliazione_autorizzata(riga[0]):
                # La riga e' vuota, ma vuota non significa «nata per errore»: un cliente
                # appena registrato e' vuoto anche lui. Senza consenso non si assorbe.
                #
                # Il log stampa l'identificativo della riga, che e' il valore da mettere
                # nella variabile: e' un intero interno, non un token ne' un telegram_id,
                # e senza di lui il proprietario non avrebbe modo di dare un consenso
                # LEGATO — quindi il consenso tornerebbe globale per forza di cose.
                logging.getLogger('xtrader.relay').error(
                    "TELEGRAM_ADMIN_ID e' posseduto da un altro account, VUOTO"
                    " (riga %s). Assorbimento NON autorizzato, quindi rifiutato: se quella"
                    " riga e' la tua (login fatto prima che la variabile arrivasse nel"
                    " processo), imposta TELEGRAM_ADMIN_RECONCILE=%s e rifai login. Se non"
                    " lo e', quella riga e' di un cliente e va corretta la variabile.",
                    riga[0], riga[0])
                _annota_admin(c, proprietario[0], 'riconciliazione_non_autorizzata',
                              bersaglio=riga[0])
                raise HTTPException(409, 'configurazione dell\'amministratore incoerente')
            riconcilia_su_utente(c, da_utente=riga[0], a_utente=proprietario[0])
            _annota_admin(c, proprietario[0], 'riconciliato_account_duplicato',
                          bersaglio=riga[0])
        c.execute('UPDATE users SET telegram_id=?, username=?, first_name=?, is_admin=1'
                  ' WHERE id=?',
                  (data.id, data.username or None, data.first_name or None,
                   proprietario[0]))
        # Riletta DOPO le scritture: la versione da firmare e' quella che il database ha
        # adesso. Firmare quella letta prima produce un cookie che nasce invalido.
        riga = c.execute('SELECT id, session_version FROM users WHERE id=?',
                         (proprietario[0],)).fetchone()
    elif riga is None:
        # Un cliente nuovo: l'account nasce e non puo' fare niente. L'accesso lo concede il
        # PR sull'approvazione (#7), non questo.
        #
        # `OR IGNORE` piu' la rilettura perche' `telegram_id` e' UNIQUE: due login
        # simultanei di un utente nuovo davano `IntegrityError`, cioe' un 500 a chi perdeva
        # la corsa, al PRIMO accesso di un cliente. Segnalato da Claude Fable 5 sulla #23.
        c.execute('INSERT OR IGNORE INTO users(telegram_id, username, first_name,'
                  " status) VALUES (?,?,?,'registrato')",
                  (data.id, data.username or None, data.first_name or None))
        riga = c.execute('SELECT id, session_version FROM users WHERE telegram_id=?',
                         (data.id,)).fetchone()
        if riga is None:
            # Non deve accadere: l'inserimento e' andato o la riga c'era. Se accade, e'
            # meglio un 503 che un `TypeError` sull'indice di `None`. La connessione la
            # chiude chi ha aperto la transazione, e un `HTTPException` prende il ramo che
            # CONFERMA: e' una decisione, non un guasto. Il commento diceva «dopo il
            # rollback» ed era rimasto indietro di un commit — segnalato da CodeRabbit.
            raise HTTPException(503, 'account non creato: riprova')
    else:
        # Login successivi: il nome su Telegram puo' essere cambiato.
        c.execute('UPDATE users SET username=?, first_name=? WHERE id=?',
                  (data.username or None, data.first_name or None, riga[0]))
    return riga[0], riga[1]

@app.post('/api/login/password')
def login_password(data: LoginPasswordIn):
    """L'accesso di emergenza: `administrator` piu' la password il cui hash sta nella
    variabile.

    Esiste perche' con il solo login Telegram un guasto di Telegram, o la perdita di
    quell'account, chiuderebbero il proprietario fuori dal proprio pannello.

    Ogni accesso riuscito finisce in `admin_audit`: e' l'unico modo per cui un accesso
    non suo sia visibile, e per cui «non sono stato io» sia dimostrabile.
    """
    # PRIMA si guarda se la porta esiste, POI si consuma il gettone. L'ordine inverso —
    # che e' quello che avevo scritto io correggendo l'aggiramento per concorrenza — fa
    # consumare il freno a richieste su un percorso DISABILITATO: cinque richieste senza
    # nessuna credenziale lo bruciavano per cinque minuti, a costo zero per chi le manda.
    # E dopo quelle cinque la risposta diventava `429 troppi tentativi` invece di `503
    # manca ADMIN_PASSWORD_HASH`, cioe' il proprietario che arriva a configurare la
    # variabile — nell'emergenza per cui questo percorso esiste — leggeva «hai fatto troppi
    # tentativi» e andava a cercare la password invece della configurazione mancante. Un
    # messaggio che manda dalla parte sbagliata e' peggio di nessun messaggio.
    # Segnalato da Claude Fable 5 sulla PR #23.
    if not ADMIN_PASSWORD_HASH:
        # Variabile assente → percorso disabilitato. 503 e non 401, per la stessa
        # ragione di `auth()`: chi lo vede deve andare a configurare, non a cercare la
        # password giusta.
        raise HTTPException(503, 'accesso con password non configurato: manca ADMIN_PASSWORD_HASH')
    attesa = _prenota_tentativo()
    if attesa:
        # 429 e non 401: chi legge deve sapere che il muro e' il freno e non la
        # password, altrimenti prova a cambiare password quando deve solo aspettare.
        raise HTTPException(429, f'troppi tentativi: riprova fra {attesa} secondi')
    # Sui byte, come negli altri due siti e per la stessa ragione: lo username lo scrive
    # chi chiama, e `administratör` faceva rispondere 500 invece di 401.
    giusta = (hmac.compare_digest(data.username.encode('utf-8'),
                                 ADMIN_USERNAME.encode('utf-8'))
              and verifica_password_admin(data.password, ADMIN_PASSWORD_HASH))
    if not giusta:
        # Il tentativo e- gia- contato da `_prenota_tentativo`: qui non si conta due volte.
        raise HTTPException(401, 'credenziali non valide')
    _azzera_tentativi()

    c = db()
    riga = c.execute('SELECT id, session_version FROM users WHERE origin_profile=?',
                     (PIERO_PROFILE,)).fetchone()
    if riga is None:
        c.close()
        raise HTTPException(503, 'nessun utente amministratore nel database')
    _annota_admin(c, riga[0], 'accesso_con_password')
    c.commit()
    utente, versione = riga[0], riga[1]
    c.close()
    return _rispondi_con_sessione(utente, versione, {'ok': True, 'utente': utente})


@app.post('/api/logout')
def logout():
    """Chiude la sessione cancellando il cookie.

    Non incrementa `session_version`: quello butta fuori **tutte** le sessioni di
    quell'utente da tutti i dispositivi, e non e' ciò che chiede chi preme «esci» su un
    computer. L'incremento serve altrove — accesso sospetto, «entra come cliente» — e
    arriva col PR sull'amministrazione.
    """
    risposta = JSONResponse({'ok': True})
    risposta.delete_cookie(NOME_COOKIE, path='/')
    return risposta


@app.get('/api/me')
def chi_sono(request: Request):
    """Chi e' l'utente della sessione. `401` se non c'e' una sessione valida.

    Non restituisce mai un token, ne' l'hash della password, ne' il `telegram_id`: il
    primo e' un segreto, il secondo pure, il terzo non serve al browser e finirebbe nei
    log di qualunque proxy davanti al servizio.

    **E rinnova il cookie**, che e' cio' che rende i venti minuti «di inattivita'» invece
    che «di sessione»: il rinnovo va DOPO la validazione, altrimenti un cookie scaduto
    tornerebbe buono al primo tentativo e la scadenza si annullerebbe da se'. Ogni rotta
    autenticata futura deve fare lo stesso — vedi `_rispondi_con_sessione` per il perche'
    non e' un middleware.
    """
    # `_sessione_valida` e non due righe ricopiate: e' lo stesso controllo delle rotte nuove, e
    # due copie divergono al primo cambio del messaggio o del codice (regola 3). Segnalato da
    # CodeRabbit sulla PR #26.
    utente = _sessione_valida(request)
    # `stato_effettivo` e non `utente['status']`: la colonna dice cio' che il proprietario ha
    # deciso, non se quella decisione e' ancora valida adesso. Misurato prima: un cliente
    # scaduto ieri leggeva `stato: attivo` con una scadenza nel passato accanto.
    # `slug` e `token_prefix`, MAI il token: il prefisso serve alla UI per dire
    # «il token armato e' xt_Ab12…» senza poterlo ricostruire, e lo slug e' il
    # pezzo pubblico dell'URL del feed. Il token in chiaro esiste solo nella
    # risposta di `genera_token_feed`, una volta.
    return _rispondi_con_sessione(utente['id'], utente['versione'], {
        'utente': utente['id'], 'nome': utente['first_name'],
        'stato': stato_effettivo(utente['status'], utente['access_expires_at']),
        'admin': utente['is_admin'],
        'accesso_scade': utente['access_expires_at'],
        'giorni_rimasti': giorni_rimasti(utente['access_expires_at']),
        'slug': utente['slug'], 'token_prefix': utente['token_prefix']})


@app.get('/api/settings')
def impostazioni_pubbliche():
    """I valori PUBBLICI che la pagina di login conosce PRIMA della sessione (#32).

    Servono al prototipo reale per costruire il link «Accedi con Telegram» nella
    modalita' redirect di oauth.telegram.org — l'unica senza script esterni, che
    CLAUDE.md vieta — la quale vuole il `bot_id` NUMERICO, non lo username. Il
    `bot_id` e' il prefisso del token prima dei due punti ed e' pubblico per
    costruzione (compare in ogni embed del widget); il token NO, e da questa
    rotta non esce niente che non sia gia' visibile a chiunque apra il bot.
    Nessuna autenticazione, deliberatamente: senza questi valori la pagina di
    login non puo' nemmeno offrire la porta Telegram.
    """
    prefisso = BOT_TOKEN.split(':', 1)[0] if ':' in BOT_TOKEN else ''
    return {
        'bot_username': TELEGRAM_BOT_USERNAME,
        'bot_id': prefisso if prefisso.isdigit() else None,
        'base_url': public_url_configurata(),
    }


@app.post('/api/me/token')
def genera_token_feed(request: Request):
    """Conia (o rigenera) il token del feed dell'utente della sessione.

    Il token esiste in chiaro **in questa risposta e mai piu'**: il server salva
    `sha256` (vedi `hash_token_feed`) e da quel momento puo' solo verificare,
    non mostrare. `/api/me` restituisce il prefisso, che non e' il token.

    Rigenerare SOVRASCRIVE l'hash: il token precedente smette di aprire il feed
    alla richiesta successiva. E' il gesto con cui un cliente che ha esposto il
    proprio URL se ne libera — per questo non c'e' un percorso di «disarmo»
    separato: rigenerare e' la revoca.

    La forma: `xt_` + `token_urlsafe(24)` — 24 byte da CSPRNG, sopra il minimo
    di 18 fissato nella Issue #2. `token_prefix` sono i primi 9 caratteri
    (`xt_` + 6), abbastanza per riconoscere il token in UI e troppo pochi per
    indovinarlo: ne mancano 26 di alfabeto urlsafe.

    Chi nasce dal login Telegram non ha uno slug (lo assegna solo la migrazione,
    ai profili): il primo token glielo crea, derivandolo dal nome con la stessa
    `_slug_libero` deterministica della migrazione. Minuscolo e senza sorprese,
    perche' finisce in un URL che il cliente incolla in XTrader.
    """
    utente = _sessione_valida(request)
    token = 'xt_' + secrets.token_urlsafe(24)
    c = db()
    try:
        slug = utente['slug']
        base = slug
        if not slug:
            base = re.sub(r'[^a-z0-9-]+', '-',
                          (utente['first_name'] or 'utente').lower()).strip('-') or 'utente'
            presi = {r[0] for r in c.execute(
                'SELECT slug FROM users WHERE slug IS NOT NULL').fetchall()}
            slug = _slug_libero(base, presi)
        try:
            c.execute('UPDATE users SET slug=?, token_hash=?, token_prefix=? WHERE id=?',
                      (slug, hash_token_feed(token), token[:9], utente['id']))
        except sqlite3.IntegrityError:
            # La corsa sul vincolo UNIQUE di `slug`: due primi-token simultanei di
            # utenti con lo stesso nome. Chi perde NON riceve un 500 con traccia:
            # riprova una volta rileggendo gli slug presi, e se perde anche quella
            # e' un 503 con l'invito a riprovare — la stessa forma della corsa
            # gemella sul CRUD dei parser.
            #
            # Il retry riparte dalla BASE, non dal candidato appena perso: da
            # `_slug_libero(candidato_perso)` una collisione su `base-2`
            # produrrebbe `base-2-2`, uno slug che finisce nell'URL del cliente.
            # Segnalato da CodeRabbit sulla PR #43, vincolato da
            # `test_la_corsa_sullo_slug_RIPARTE_dalla_base_non_dal_candidato`.
            presi = {r[0] for r in c.execute(
                'SELECT slug FROM users WHERE slug IS NOT NULL').fetchall()}
            slug = _slug_libero(base, presi)
            try:
                c.execute('UPDATE users SET slug=?, token_hash=?, token_prefix=? WHERE id=?',
                          (slug, hash_token_feed(token), token[:9], utente['id']))
            except sqlite3.IntegrityError:
                # `from None`: la traccia dell'IntegrityError racconta lo schema del
                # database a chi riceve un errore HTTP, e il 503 e' gia' la decisione.
                raise HTTPException(503, 'slug conteso: riprova') from None
        c.commit()
    finally:
        c.close()
    return _rispondi_con_sessione(utente['id'], utente['versione'], {
        'token': token, 'token_prefix': token[:9], 'feed': f'/feed/{slug}.csv'})


def _sessione_valida(request):
    """L'utente della sessione, o `401`. Rinnova il cookie chi risponde, non questa."""
    utente = utente_dalla_sessione(request)
    if not utente:
        raise HTTPException(401, 'sessione assente o scaduta')
    return utente


def _solo_amministratore(request):
    """L'utente della sessione se e' l'amministratore, altrimenti **404**.

    `404` e non `403`, e la differenza non e' stilistica: un `403` conferma che quella rotta
    esiste, cioe' dice a un estraneo dove sta il pannello. La Issue #2 lo scrive esplicitamente
    per `/admin/*`, ed e' la stessa regola con cui un utente non vede i parser di un altro.
    """
    utente = utente_dalla_sessione(request)
    if not utente or not utente['is_admin']:
        raise HTTPException(404, 'not found')
    return utente


def _amministratore_o_none(request):
    """L'utente della sessione se e' l'amministratore, altrimenti `None` — SENZA sollevare.

    Per le rotte con un secondo modo di autenticarsi oltre alla sessione (il token del cron di
    backup): si prova prima l'admin, e se non c'e' si prova l'altro. `_solo_amministratore`
    solleverebbe 404 e non lascerebbe provare il token."""
    utente = utente_dalla_sessione(request)
    return utente if utente and utente['is_admin'] else None


def _identificativo_o_404(valore):
    """L'identificativo numerico di una richiesta, o `404`.

    `richiesta: str` nella firma e la conversione qui, e non `richiesta: int`, per la stessa
    ragione per cui il corpo si legge a mano: FastAPI valida i parametri di percorso **prima**
    di entrare nell'handler, quindi `/api/admin/requests/NON-ESISTE/approva` rispondeva `422` a
    un estraneo senza sessione — cioe' confermava che la rotta esiste, che e' cio' che il `404`
    invece del `403` serve a non dare. Trovato dalla guardia sulle rotte in
    `tests/relay/test_autenticazione.py`, che prova ogni rotta con un percorso finto.

    E `404` e' anche la risposta giusta nel merito: una richiesta il cui identificativo non e'
    un numero non esiste.
    """
    try:
        return int(valore)
    except (TypeError, ValueError):
        raise HTTPException(404, 'richiesta non trovata')


class GiorniIn(BaseModel):
    """I giorni concessi da un'approvazione. Campo libero, come chiesto nella Issue #2."""

    giorni: int


@app.post('/api/access/request')
def chiedi_accesso(request: Request):
    """Il cliente chiede l'accesso. Idempotente per stato, non per chiamata.

    Chi ha gia' una richiesta aperta o l'accesso attivo riceve `409`: senza, un doppio clic
    riempirebbe il pannello del proprietario di richieste identiche, e con esse la decisione
    diventerebbe «quale di queste tre approvo?».

    **Restituisce il deep link del bot**, e non e' un abbellimento: il bot Telegram **non puo'
    scrivere per primo**. `sendMessage` verso chi non ha mai aperto una conversazione col bot
    falisce, quindi un cliente che entra col Login Widget e non apre mai il bot non ricevera'
    mai l'approvazione — **in silenzio**. E' la trappola 1 della Issue #2. Il link porta a
    `t.me/<bot>?start=...`: quando il cliente preme Start, la consegna arriva al webhook e
    `users.telegram_reachable` diventa 1.
    """
    utente = _sessione_valida(request)
    c = db()
    # Lo stato si rilegge **dentro** `BEGIN IMMEDIATE`, e non basta quello che la sessione ha
    # letto un istante prima: fra la lettura e l'inserimento c'e' spazio per un altro clic, e
    # due richieste concorrenti passavano entrambe il controllo e inserivano due righe aperte —
    # cioe' esattamente il caso che il 409 esiste per impedire. E' la stessa corsa
    # SELECT-poi-INSERT del login, che la PR #24 ha chiuso con lo stesso strumento, e l'ho
    # ripetuta qui. Alzata indipendentemente da Claude Fable 5 e GPT-5.5 sulla PR #26.
    try:
        # `BEGIN IMMEDIATE` sta DENTRO il `try`: sotto contesa quel comando stesso solleva
        # `database is locked`, e da fuori nessun `except` gira e la connessione non viene
        # chiusa. E' la stessa correzione che `login_telegram` ha ricevuto sulla PR #24 — dove
        # la regola e' anche scritta — e l'ho riprodotta in tre rotte nuove: la classe non era
        # stata cercata. Segnalato da CodeRabbit come Major sulla PR #26.
        c.execute('BEGIN IMMEDIATE')
        riga = c.execute('SELECT status, access_expires_at, telegram_reachable FROM users'
                         ' WHERE id=?', (utente['id'],)).fetchone()
        if riga is None:
            raise HTTPException(401, 'sessione assente o scaduta')
        stato = stato_effettivo(riga[0], riga[1])
        if stato == 'attivo':
            raise HTTPException(409, 'accesso gia\' attivo')
        if stato == 'in_attesa':
            raise HTTPException(409, 'richiesta gia\' in corso')
        if stato == 'sospeso':
            # Un sospeso non rientra da se' chiedendo di nuovo: la sospensione e' una decisione
            # del proprietario, e una richiesta che la aggirasse la renderebbe inutile.
            raise HTTPException(403, 'accesso sospeso')
        try:
            c.execute('INSERT INTO access_requests(user_id) VALUES (?)', (utente['id'],))
        except sqlite3.IntegrityError:
            # L'indice `richiesta_aperta_unica` ha detto no: un ALTRO processo ha inserito la
            # richiesta fra la nostra rilettura e questo `INSERT`. Non e' un guasto, e' la
            # corsa persa — e la risposta giusta e' la stessa che daremmo avendola vista noi.
            # Senza questo ramo il perdente riceveva un **500** su un doppio clic. Bloccante di
            # Claude Fable 5 sulla PR #26: la rilettura dentro la transazione copre un processo
            # solo, e con due worker l'indice diventa l'unico arbitro.
            raise HTTPException(409, 'richiesta gia\' in corso')
        c.execute("UPDATE users SET status='in_attesa' WHERE id=?", (utente['id'],))
        raggiungibile = bool(riga[2])
        c.commit()
    except Exception:
        # Anche sugli `HTTPException`: qui, a differenza di `_decidi_identita`, nessuna
        # scrittura precede il rifiuto, quindi non c'e' nessuna traccia da salvare — e
        # confermare una transazione vuota su un percorso d'errore e' l'abitudine che il giorno
        # in cui una scrittura ci finisce davanti la rende persistente. Rilievo di Fable 5.
        c.rollback()
        c.close()
        raise
    c.close()
    return _rispondi_con_sessione(utente['id'], utente['versione'], {
        'ok': True, 'stato': 'in_attesa',
        'raggiungibile': raggiungibile, 'bot': link_del_bot('accesso')})


@app.get('/api/admin/requests')
def elenco_richieste(request: Request):
    """Le richieste da decidere. Nessun token e nessun `telegram_id` nella risposta.

    Il pannello mostra chi chiede e da quando; il `telegram_id` non serve a decidere e
    finirebbe nei log di ogni proxy davanti al servizio, e i token dei clienti non compaiono
    in nessuna risposta admin (Issue #2, test della PR 11).
    """
    utente = _solo_amministratore(request)
    c = db()
    # `try/finally`: un guasto inatteso qui lascerebbe la connessione aperta, e su un processo
    # che non riparte le connessioni perse si accumulano. Segnalato da CodeRabbit sulla PR #26.
    try:
        righe = c.execute(
            'SELECT r.id, r.user_id, r.created_at, u.first_name, u.username, u.status,'
            ' u.access_expires_at, u.telegram_reachable'
            ' FROM access_requests r JOIN users u ON u.id = r.user_id'
            ' WHERE r.decided_at IS NULL ORDER BY r.id').fetchall()
    finally:
        c.close()
    return _rispondi_con_sessione(utente['id'], utente['versione'], {'richieste': [
        {'richiesta': r[0], 'utente': r[1], 'chiesto_il': r[2], 'nome': r[3],
         'username': r[4], 'stato': stato_effettivo(r[5], r[6]),
         'giorni_rimasti': giorni_rimasti(r[6]), 'raggiungibile': bool(r[7])}
        for r in righe]})


@app.post('/api/admin/requests/{richiesta}/approva')
async def approva_richiesta(richiesta: str, request: Request):
    """Concede `giorni` di accesso, registra la decisione, avvisa il cliente.

    **Il corpo si legge a mano, dopo il controllo della sessione**, e non come parametro
    tipizzato. Con `dati: GiorniIn` nella firma, FastAPI valida il corpo **prima** di entrare
    qui: un estraneo senza sessione riceveva `422` invece di `404`, cioe' la conferma che
    questa rotta esiste — che e' esattamente cio' che il `404` invece del `403` serve a non
    dare. L'ha trovato la guardia sulle rotte in `tests/relay/test_autenticazione.py`, che
    enumera le rotte del servizio e pretende che ognuna rifiuti chi non e' autenticato: la
    stessa guardia che a suo tempo ha scoperto il webhook non autenticato.

    I giorni sono un campo libero, com'e' chiesto: nessun listino nel codice. Il limite
    superiore esiste solo per fermare un refuso — `3650` sono dieci anni, e uno zero di troppo
    su una tastiera e' piu' probabile di un abbonamento decennale.

    **L'errore di invio non viene ingoiato.** La risposta porta `notificato: false` con il
    motivo, e `telegram_reachable` va a 0: la Issue #2 lo chiede esplicitamente, perche' un
    invio fallito in silenzio produce lo stato peggiore — il proprietario crede di aver
    avvisato il cliente, il cliente non sa di essere stato attivato, e nessuno dei due ha modo
    di accorgersene. L'accesso invece **resta concesso**: e' stato deciso, e non si annulla una
    decisione perche' l'avviso non e' arrivato.
    """
    amministratore = _solo_amministratore(request)
    numero = _identificativo_o_404(richiesta)
    try:
        dati = GiorniIn(**(await _json_dal_corpo(request)))
    except HTTPException:
        raise
    except Exception:
        # Corpo assente, non JSON, o senza `giorni`: `422`, come farebbe FastAPI. Il
        # messaggio non riporta il corpo ricevuto.
        raise HTTPException(422, 'corpo non valido: serve {"giorni": <numero>}')
    if not 1 <= dati.giorni <= 3650:
        raise HTTPException(422, 'giorni fuori intervallo')
    c = db()
    try:
        # `BEGIN IMMEDIATE` sta DENTRO il `try`: sotto contesa quel comando stesso solleva
        # `database is locked`, e da fuori nessun `except` gira e la connessione non viene
        # chiusa. E' la stessa correzione che `login_telegram` ha ricevuto sulla PR #24 — dove
        # la regola e' anche scritta — e l'ho riprodotta in tre rotte nuove: la classe non era
        # stata cercata. Segnalato da CodeRabbit come Major sulla PR #26.
        c.execute('BEGIN IMMEDIATE')
        riga = c.execute('SELECT r.user_id, u.access_expires_at, u.telegram_id'
                         ' FROM access_requests r JOIN users u ON u.id = r.user_id'
                         ' WHERE r.id=? AND r.decided_at IS NULL', (numero,)).fetchone()
        if riga is None:
            # 404 anche per una richiesta gia' decisa: dall'esterno «non esiste» e «l'hai gia'
            # decisa» sono lo stesso stato — non c'e' niente da decidere.
            raise HTTPException(404, 'richiesta non trovata')
        scadenza = nuova_scadenza(riga[1], dati.giorni)
        c.execute("UPDATE users SET status='attivo', access_expires_at=?,"
                  ' promemoria_per=NULL WHERE id=?', (scadenza, riga[0]))
        c.execute("UPDATE access_requests SET decided_at=strftime('%s','now'), decided_by=?,"
                  " granted_days=?, outcome='approvata' WHERE id=?",
                  (amministratore['id'], dati.giorni, numero))
        _annota_admin(c, amministratore['id'], 'accesso_approvato', bersaglio=riga[0])
        c.commit()
    except HTTPException:
        # `rollback` e non `commit`: il solo `HTTPException` che nasce qui dentro e' il 404, e
        # arriva **prima** di qualunque scrittura. Confermare una transazione vuota non fa
        # danno oggi e lo farebbe il giorno in cui una scrittura finisse prima del controllo:
        # e' la differenza con `_decidi_identita`, dove il `commit` serve perche' li' il
        # rifiuto ha gia' scritto la propria riga di audit. Rilievo di Claude Fable 5.
        c.rollback()
        c.close()
        raise
    except Exception:
        c.rollback()
        c.close()
        raise

    # In un thread: questa rotta e' `async`, quindi gira sul loop, e `invia_messaggio_telegram`
    # aspetta la rete fino a dieci secondi. Sul loop quei dieci secondi fermerebbero **tutte**
    # le richieste del processo, feed compreso. Segnalato da GPT-5.5 sulla PR #26; e' lo stesso
    # motivo per cui il webhook chiama `assicura_registrazione` con `to_thread`.
    notificato, motivo = await asyncio.to_thread(
        invia_messaggio_telegram, riga[2],
        f'Accesso attivato: {dati.giorni} giorni. Buon lavoro.')
    if not notificato:
        c.execute('UPDATE users SET telegram_reachable=0 WHERE id=?', (riga[0],))
        c.commit()
    c.close()
    return _rispondi_con_sessione(amministratore['id'], amministratore['versione'], {
        'ok': True, 'utente': riga[0], 'scade': scadenza,
        'giorni_rimasti': giorni_rimasti(scadenza),
        'notificato': notificato, 'motivo': None if notificato else motivo})


@app.post('/api/admin/requests/{richiesta}/rifiuta')
def rifiuta_richiesta(richiesta: str, request: Request):
    """Rifiuta una richiesta: torna `registrato`, cosi' puo' richiedere.

    Non `sospeso`: un rifiuto non e' una punizione, e chi viene rifiutato deve poter chiedere
    di nuovo — magari dopo aver pagato. La sospensione resta un gesto separato.
    """
    amministratore = _solo_amministratore(request)
    numero = _identificativo_o_404(richiesta)
    c = db()
    try:
        # `BEGIN IMMEDIATE` sta DENTRO il `try`: sotto contesa quel comando stesso solleva
        # `database is locked`, e da fuori nessun `except` gira e la connessione non viene
        # chiusa. E' la stessa correzione che `login_telegram` ha ricevuto sulla PR #24 — dove
        # la regola e' anche scritta — e l'ho riprodotta in tre rotte nuove: la classe non era
        # stata cercata. Segnalato da CodeRabbit come Major sulla PR #26.
        c.execute('BEGIN IMMEDIATE')
        riga = c.execute('SELECT user_id FROM access_requests'
                         ' WHERE id=? AND decided_at IS NULL', (numero,)).fetchone()
        if riga is None:
            raise HTTPException(404, 'richiesta non trovata')
        c.execute("UPDATE users SET status='registrato' WHERE id=? AND status='in_attesa'",
                  (riga[0],))
        c.execute("UPDATE access_requests SET decided_at=strftime('%s','now'), decided_by=?,"
                  " outcome='rifiutata' WHERE id=?", (amministratore['id'], numero))
        _annota_admin(c, amministratore['id'], 'accesso_rifiutato', bersaglio=riga[0])
        c.commit()
    except HTTPException:
        # `rollback` come nella gemella `approva_richiesta`: il solo `HTTPException` che nasce
        # qui e' il 404, e arriva prima di ogni scrittura. Avevo corretto una delle due rotte e
        # lasciato l'altra col `commit` — due percorsi identici con due comportamenti diversi
        # sono il posto in cui uno dei due diventera' sbagliato. Segnalato da CodeRabbit.
        c.rollback()
        c.close()
        raise
    except Exception:
        c.rollback()
        c.close()
        raise
    c.close()
    return _rispondi_con_sessione(amministratore['id'], amministratore['versione'],
                                  {'ok': True, 'utente': riga[0], 'stato': 'registrato'})


@app.post('/api/admin/promemoria')
def manda_promemoria(request: Request):
    """Avvisa chi sta per scadere. **Una volta per scadenza**, non una volta per sempre.

    `users.promemoria_per` conserva **quale** scadenza e' stata annunciata, e non un booleano:
    con un booleano il secondo rinnovo non avviserebbe mai piu', perche' quella riga
    resterebbe «gia' avvisata» per il resto della vita del cliente. Cosi' invece
    l'approvazione azzera il campo e il promemoria del ciclo successivo parte da se'.

    **Limite dichiarato, e non e' un dettaglio da scoprire in produzione: non c'e' uno
    scheduler.** Questo servizio non ha un processo che si sveglia a ogni ora, e i due percorsi
    che girano da soli sono il feed (che XTrader interroga a raffica: metterci un invio
    Telegram lo renderebbe lento e fragile) e il webhook (che dipende dai messaggi dei canali).
    Quindi questa rotta va **chiamata**, dal proprietario o da un job programmato su Railway.
    Finche' non viene chiamata, nessun promemoria parte: e' un compito che aspetta, non un
    compito perso. La riga in `admin_audit` dice quando e' stato fatto l'ultimo giro.

    Un invio fallito **non** consuma il promemoria: la prenotazione viene rilasciata, cosi' il
    giro successivo riprova. E' il contrario di quello che si fa con il freno del login, dove il
    tentativo si consuma prima: la' il rischio e' che qualcuno provi troppe volte, qui il rischio
    e' che il cliente non sappia che scade.

    **Baratto dichiarato: at-most-once.** La prenotazione si conferma **prima** dell'invio, e
    questo lascia una finestra — un crash del processo fra il `commit` e la chiamata a Telegram
    consuma il promemoria senza averlo mandato, e nessuno riprova per quel ciclo. La scelta
    opposta (inviare e poi scrivere) sposterebbe la finestra sull'altro lato e produrrebbe
    avvisi **doppi** allo stesso cliente. Fra i due, un promemoria di cortesia perso vale meno di
    un cliente che riceve due volte lo stesso messaggio, e il costo e' limitato: la scadenza la
    vede comunque in dashboard, e il feed gli si spegne solo alla data. Chiesto da Claude Fable 5
    sulla PR #26, e la richiesta era giusta — «accettabile solo se documentato».
    """
    amministratore = _solo_amministratore(request)
    adesso = int(time.time())
    c = db()
    try:
        avvisati, falliti = _giro_di_promemoria(c, adesso)
        _annota_admin(c, amministratore['id'], 'promemoria_inviati')
        c.commit()
    finally:
        # Il giro fa rete e scritture: un guasto a meta' non deve lasciare la connessione
        # aperta. Segnalato da CodeRabbit sulla PR #26.
        c.close()
    return _rispondi_con_sessione(amministratore['id'], amministratore['versione'],
                                  {'avvisati': avvisati, 'falliti': falliti})


@app.get('/api/admin/backup')
async def scarica_backup(request: Request):
    """Scarica una copia CONSISTENTE del database — solo amministratore (#56).

    Tutti i dati del servizio vivono in un file (`signals.db`): utenti, parser,
    libreria mercati, hash dei token, log, richieste di accesso. Questa rotta ne
    consegna una copia coerente (`copia_backup_db`, via l'API di backup di SQLite)
    come download `betrelay-backup-AAAA-MM-GG-HHMM.db`.

    `_solo_amministratore` risponde **404** a chiunque non sia il proprietario: la
    copia contiene i dati dei clienti — i token solo come hash — e non e' una
    risorsa di cui un utente qualsiasi debba nemmeno vedere l'esistenza (stessa
    regola del resto di `/api/admin/*`). Il download viene **tracciato** in
    `admin_audit`: chi si porta via l'intero database e' esattamente cio' che una
    traccia di audit deve registrare.

    `to_thread`: la copia legge tutto il file e la rotta e' `async`; eseguirla sul
    loop fermerebbe ogni altra richiesta del processo, feed compreso — lo stesso
    motivo per cui l'invio Telegram dell'approvazione gira in un thread.

    **Anti-CSRF.** E' una GET che avvia un'operazione costosa, e il cookie di sessione
    e' `SameSite=Lax`: una navigazione top-level da un altro sito se lo porterebbe
    dietro, quindi una pagina ostile potrebbe indurre il browser dell'amministratore a
    generare backup a ripetizione (amplificando il picco di RAM di sopra) e a sporcare
    l'audit. `Sec-Fetch-Site` lo impostano i browser e la pagina non lo puo'
    falsificare: la navigazione del pulsante e' `same-origin`, l'indirizzo digitato o un
    segnalibro `none`, un innesco da un altro sito `cross-site`/`same-site` — che
    rifiutiamo con 403. Il controllo sta DOPO `_solo_amministratore`, cosi' un estraneo
    continua a vedere 404 e non l'esistenza della rotta. Segnalato da GPT-5.6 Sol (#56).
    """
    amministratore = _solo_amministratore(request)
    sito = request.headers.get('sec-fetch-site')
    if sito in ('cross-site', 'same-site'):
        raise HTTPException(403, 'richiesta di backup da un contesto non consentito')
    # La copia gira in un thread (un solo backup per volta: il lucchetto e' dentro
    # `copia_backup_db`) per non fermare il loop mentre legge tutto il file.
    dati = await asyncio.to_thread(copia_backup_db)
    # Traccia DOPO la copia riuscita: una copia fallita non deve lasciare in
    # `admin_audit` la riga di un download mai avvenuto (nota di Claude Fable 5, #56).
    c = db()
    try:
        c.execute('BEGIN IMMEDIATE')
        _annota_admin(c, amministratore['id'], 'scarica_backup')
        c.commit()
    except Exception:
        c.rollback()
        c.close()
        raise
    c.close()
    nome = 'betrelay-backup-' + time.strftime('%Y-%m-%d-%H%M', time.gmtime()) + '.db'
    risposta = Response(content=dati, media_type='application/x-sqlite3', headers={
        'Content-Disposition': f'attachment; filename="{nome}"',
        'Cache-Control': 'no-store'})
    # Rinnova il cookie di sessione sulla risposta-file, come ogni rotta autenticata.
    return _rispondi_con_sessione(amministratore['id'], amministratore['versione'],
                                  risposta=risposta)


# Il testo del messaggio di prova verso il canale di backup. ASCII puro di proposito:
# e' una stringa che parte verso Telegram, non un marcatore di parser, e tenerla senza
# non-ASCII evita ogni rischio di codifica (vedi «REGOLA CODIFICA»).
MESSAGGIO_PROVA_BACKUP = ('BetRelay: canale di backup collegato. Qui arriveranno i backup'
                          ' del database del servizio.')


def _canale_configurato(c):
    """Il canale di backup CONFIGURATO come `{chat_id, titolo}`, o `None`."""
    cid = leggi_impostazione(c, CHIAVE_CANALE_BACKUP_ID)
    if not cid:
        return None
    return {'chat_id': cid, 'titolo': leggi_impostazione(c, CHIAVE_CANALE_BACKUP_TITOLO, '')}


def _canale_candidato(c):
    """Il canale CANDIDATO (catturato dal webhook, da confermare) come `{chat_id, titolo}`."""
    cid = leggi_impostazione(c, CHIAVE_CANALE_CANDIDATO_ID)
    if not cid:
        return None
    return {'chat_id': cid, 'titolo': leggi_impostazione(c, CHIAVE_CANALE_CANDIDATO_TITOLO, '')}


@app.get('/api/admin/canale-backup')
def stato_canale_backup(request: Request):
    """Lo stato del canale di backup: il configurato e l'eventuale candidato (#56 pezzo 2).

    Solo amministratore (404 altrimenti). Il `chat_id` torna al proprietario — e' il suo
    canale, e i canali hanno id negativi che l'app Telegram non mostra, quindi vederlo
    aiuta a riconoscerlo — mai a nessun altro, perche' la rotta e' 404 fuori dall'admin.
    """
    amministratore = _solo_amministratore(request)
    c = db()
    try:
        corpo = {'configurato': _canale_configurato(c), 'candidato': _canale_candidato(c)}
    finally:
        c.close()
    return _rispondi_con_sessione(amministratore['id'], amministratore['versione'], corpo)


class ConfermaCanaleIn(BaseModel):
    """L'`chat_id` del candidato che il pannello ha mostrato all'amministratore.

    E' una precondizione dal client, come `uid` sui parser (#75): fra il GET che mostra il
    candidato e questo POST una riconsegna del webhook puo' aver cambiato il candidato
    server-side, e senza l'id la conferma configurerebbe una destinazione DIVERSA da quella
    approvata. Bloccante di GPT-5.6 Sol al gate finale del pezzo 2a (#56)."""
    chat_id: str


@app.post('/api/admin/canale-backup/conferma')
async def conferma_canale_backup(request: Request):
    """Conferma il canale CANDIDATO come canale di backup, dopo un invio di PROVA riuscito.

    **Il corpo porta l'`chat_id` del candidato che il pannello ha mostrato** — una precondizione
    dal client, come `uid` sui parser (#75). Fra il GET che mostra il candidato e questo POST una
    riconsegna puo' averlo cambiato server-side; confermare l'id vecchio configurerebbe una
    destinazione che l'amministratore non ha approvato. Se l'id non combacia col candidato
    corrente → 409, e il pannello rilegge e rimostra. Bloccante di GPT-5.6 Sol (#56).

    **Il corpo si legge DOPO `_solo_amministratore`**, non nella firma: con un parametro tipizzato
    FastAPI validerebbe il corpo prima del controllo di sessione, e un estraneo riceverebbe 422
    invece di 404 — la stessa ragione di `approva_richiesta`.

    Il messaggio di prova E' la verifica: se il bot non riesce a scrivere nel canale (non e'
    amministratore, canale sbagliato, rete giu'), NON si salva niente e l'errore torna VISIBILE
    (400 col motivo, che `invia_messaggio_telegram` garantisce senza token). Solo su invio riuscito
    il candidato diventa il canale configurato e il candidato si azzera — un solo canale di backup
    alla volta. Tracciato in `admin_audit`.

    L'invio (rete) sta FUORI dalla transazione, come nel giro dei promemoria: non si tiene un lock
    del database mentre si aspetta Telegram.
    """
    amministratore = _solo_amministratore(request)
    try:
        dati = ConfermaCanaleIn(**(await _json_dal_corpo(request)))
    except HTTPException:
        raise
    except Exception:
        # Corpo assente, non JSON, o senza `chat_id`: 422, come farebbe FastAPI. Il messaggio
        # non riporta il corpo ricevuto.
        raise HTTPException(422, 'corpo non valido: serve {"chat_id": "<id del candidato>"}')
    c = db()
    try:
        candidato = _canale_candidato(c)
    finally:
        c.close()
    if not candidato:
        raise HTTPException(400, 'nessun canale candidato: aggiungi il bot come amministratore'
                                 ' del canale privato')
    if candidato['chat_id'] != dati.chat_id:
        # Il candidato che l'admin ha approvato non e' piu' quello corrente: una riconsegna l'ha
        # cambiato fra il GET e questo POST. Non si conferma una destinazione non approvata — il
        # pannello rilegge e rimostra quella nuova. Precondizione dal client (Sol, #56).
        raise HTTPException(409, 'il candidato e- cambiato: ricontrolla il canale proposto e riprova')
    # In un thread: questa rotta e' `async`, quindi gira sul loop, e `invia_messaggio_telegram`
    # e' un I/O di rete SINCRONO — chiamarlo direttamente terrebbe l'event loop bloccato fino al
    # timeout di Telegram, fermando webhook, feed CSV e le richieste di tutti gli utenti. E' lo
    # stesso motivo e la stessa forma di `approva_richiesta`. Bloccante di GPT-5.6 Sol (#56).
    riuscito, motivo = await asyncio.to_thread(
        invia_messaggio_telegram, candidato['chat_id'], MESSAGGIO_PROVA_BACKUP)
    if not riuscito:
        raise HTTPException(400, f'invio di prova fallito: {motivo}')
    c = db()
    try:
        c.execute('BEGIN IMMEDIATE')
        # Il candidato si rilegge DENTRO la transazione: la prova (rete) e' avvenuta fuori,
        # e una cattura concorrente puo' averlo cambiato nel frattempo. Se e' cambiato NON
        # si configura quello vecchio — cancellerebbe il nuovo candidato senza traccia:
        # si abbandona con 409 e l'amministratore riconferma. Segnalato da GPT-5.5 e Fable 5.
        if leggi_impostazione(c, CHIAVE_CANALE_CANDIDATO_ID) != candidato['chat_id']:
            raise HTTPException(409, 'il candidato e- cambiato durante la verifica: riprova')
        scrivi_impostazione(c, CHIAVE_CANALE_BACKUP_ID, candidato['chat_id'])
        scrivi_impostazione(c, CHIAVE_CANALE_BACKUP_TITOLO, candidato['titolo'])
        cancella_impostazione(c, CHIAVE_CANALE_CANDIDATO_ID)
        cancella_impostazione(c, CHIAVE_CANALE_CANDIDATO_TITOLO)
        _annota_admin(c, amministratore['id'], 'canale_backup_configurato')
        c.commit()
        corpo = {'configurato': _canale_configurato(c), 'candidato': None}
    except Exception:
        # Rollback e close ESPLICITI (come `rimuovi_canale_backup`): sotto contesa
        # `BEGIN IMMEDIATE` stesso puo' sollevare `database is locked`, e senza questo ramo
        # la connessione resterebbe aperta con una transazione mai chiusa. Include il 409
        # qui sopra, che annulla la transazione non committata. Nota di Claude Fable 5 (#56).
        c.rollback()
        c.close()
        raise
    c.close()
    return _rispondi_con_sessione(amministratore['id'], amministratore['versione'], corpo)


@app.post('/api/admin/canale-backup/prova')
def prova_canale_backup(request: Request):
    """Manda un messaggio di prova al canale GIA' configurato, per riverificarlo.

    Non cambia la configurazione. Se non c'e' un canale → 400; un invio fallito torna
    visibile col motivo, mai ingoiato.
    """
    amministratore = _solo_amministratore(request)
    c = db()
    try:
        configurato = _canale_configurato(c)
    finally:
        c.close()
    if not configurato:
        raise HTTPException(400, 'nessun canale di backup configurato')
    riuscito, motivo = invia_messaggio_telegram(configurato['chat_id'], MESSAGGIO_PROVA_BACKUP)
    if not riuscito:
        raise HTTPException(400, f'invio di prova fallito: {motivo}')
    return _rispondi_con_sessione(amministratore['id'], amministratore['versione'],
                                  {'inviato': True})


@app.delete('/api/admin/canale-backup')
def rimuovi_canale_backup(request: Request):
    """Rimuove il canale di backup configurato (e l'eventuale candidato). Tracciato."""
    amministratore = _solo_amministratore(request)
    c = db()
    try:
        c.execute('BEGIN IMMEDIATE')
        for chiave in (CHIAVE_CANALE_BACKUP_ID, CHIAVE_CANALE_BACKUP_TITOLO,
                       CHIAVE_CANALE_CANDIDATO_ID, CHIAVE_CANALE_CANDIDATO_TITOLO):
            cancella_impostazione(c, chiave)
        _annota_admin(c, amministratore['id'], 'canale_backup_rimosso')
        c.commit()
    except Exception:
        c.rollback()
        c.close()
        raise
    c.close()
    return _rispondi_con_sessione(amministratore['id'], amministratore['versione'],
                                  {'configurato': None, 'candidato': None})


def _prenota_periodo_backup(periodo):
    """Prenota `periodo` in `backup_inviato` sotto `BEGIN IMMEDIATE`. `True` se appena
    prenotato, `False` se gia' presente (backup gia' inviato per quel periodo).

    E' l'idempotenza PERSISTENTE del giro notturno (#56): la prenotazione avviene PRIMA
    dell'invio, quindi due repliche o un retry non mandano due copie — la seconda trova il
    periodo gia' preso. Crash-safe fra le repliche: l'unica INSERT che vince e' quella
    committata per prima."""
    c = db()
    try:
        c.execute('BEGIN IMMEDIATE')
        if c.execute('SELECT 1 FROM backup_inviato WHERE periodo=?', (periodo,)).fetchone():
            c.rollback()
            return False
        c.execute('INSERT INTO backup_inviato(periodo) VALUES (?)', (periodo,))
        c.commit()
        return True
    finally:
        c.close()


def _libera_periodo_backup(periodo):
    """Cancella la prenotazione di `periodo` (best-effort). Serve quando l'invio prenotato
    FALLISCE: senza, la notte resterebbe segnata come fatta e un retry non ripartirebbe."""
    try:
        c = db()
        try:
            c.execute('DELETE FROM backup_inviato WHERE periodo=?', (periodo,))
            c.commit()
        finally:
            c.close()
    except Exception:
        logging.exception('impossibile liberare la prenotazione del backup per il periodo')


def _invia_backup_al_canale(amministratore_id=None, periodo=None):
    """Manda il backup al canale CONFIGURATO. `(riuscito, motivo)`, non solleva.

    Sincrona (I/O su disco, rete): il chiamante `async` la passa a `asyncio.to_thread`, come
    ogni carico bloccante che altrimenti fermerebbe il loop — feed e webhook compresi.

    `periodo` non-None = percorso CRON (#56, idempotenza persistente): si PRENOTA il periodo
    prima di inviare, e se e' gia' preso si esce come no-op idempotente `(True, None)` — cosi'
    un retry del cron o una seconda replica non mandano un secondo backup. Su invio fallito la
    prenotazione si LIBERA, per permettere un nuovo tentativo della stessa notte. `periodo`
    None = il BOTTONE dell'amministratore: intento umano esplicito, invia SEMPRE, senza toccare
    la prenotazione del cron.

    Riverifica la privacy con `getChat` PRIMA di inviare (Sol #56): la cattura garantisce un canale
    privato, ma un canale reso pubblico DOPO la conferma esporrebbe i dati dei clienti; se ora
    riporta uno `username` NON si invia. Poi la copia va su un FILE temporaneo (non in RAM,
    `copia_backup_su_file`) e si manda in streaming; il file si cancella comunque vada. Traccia in
    `admin_audit` con l'admin che ha premuto il bottone, o `NULL` per il giro del cron.
    """
    import tempfile
    # Un solo invio alla volta: se un altro e' in corso (bottone e cron sovrapposti) si esce senza
    # consegnare ne' tracciare due volte. NON bloccante di proposito — il secondo non deve
    # accodarsi e mandare un secondo backup, deve semplicemente saltare. GPT-5.5, PR #101.
    if not _lucchetto_invio_backup.acquire(blocking=False):
        return False, 'un backup e- gia- in corso: riprova fra poco'
    try:
        c = db()
        try:
            configurato = _canale_configurato(c)
        finally:
            c.close()
        if not configurato:
            return False, 'nessun canale di backup configurato'
        chat_id = configurato['chat_id']
        # Idempotenza persistente del giro notturno: si PRENOTA il periodo prima di inviare.
        # Se e' gia' preso (retry del cron, o una seconda replica) si esce come no-op
        # idempotente — nessun secondo backup. Il bottone dell'amministratore (`periodo` None)
        # non passa di qui: invia sempre. Bloccante di GPT-5.6 Sol (#56).
        if periodo is not None and not _prenota_periodo_backup(periodo):
            return True, None
        inviato_ok = False
        try:
            fd, percorso = tempfile.mkstemp(prefix='betrelay-backup-', suffix='.db')
            os.close(fd)
            try:
                try:
                    copia_backup_su_file(percorso)
                except Exception as e:
                    # `copia_backup_su_file` puo' sollevare (disco pieno, errore sqlite). Il contratto
                    # e' «non solleva»: l'eccezione diventa `(False, motivo)` e la rotta risponde 400,
                    # non 500. Il motivo e' il TIPO, mai un percorso o dato del DB. Fable 5 (#101).
                    return False, f'copia del backup fallita ({type(e).__name__})'
                # La riverifica della privacy sta SUBITO PRIMA dell'invio, DOPO la copia: cosi' la
                # finestra fra «il canale e' privato» e il `sendDocument` e' minima — la copia, che
                # puo' durare, non la allarga piu'. Deve essere ancora un canale PRIVATO
                # (`type == 'channel'` e senza `username`): un canale reso pubblico o convertito in
                # gruppo non riceve il backup coi dati dei clienti. Una TOCTOU residua sub-secondo e'
                # inevitabile con un'API remota senza invio condizionato. Sol e Fable (#101).
                ok, dati = leggi_chat_telegram(chat_id)
                if not ok:
                    return False, f'verifica del canale fallita: {dati}'
                if (dati.get('type') or '') != 'channel' or dati.get('username'):
                    return False, ('il canale non e- piu- un canale privato: rimuovilo e configura un'
                                   ' canale privato prima di inviare il backup')
                nome = f'betrelay-backup-{time.strftime("%Y-%m-%d-%H%M", time.gmtime())}.db'
                riuscito, motivo = invia_documento_telegram(
                    chat_id, percorso, nome, didascalia='BetRelay: backup automatico del database.')
            finally:
                try:
                    os.unlink(percorso)
                except OSError:
                    pass
            if not riuscito:
                return False, f'invio del documento fallito: {motivo}'
            # L'audit e' BEST-EFFORT: il documento E' gia' partito, e un errore SQLite qui NON deve
            # tornare «fallito» — con la prenotazione persistente un fallimento farebbe LIBERARE il
            # periodo e il cron rimanderebbe un SECONDO backup identico. Si logga e si ritorna
            # successo: un backup senza la sua riga di audit e' meglio di due backup. Sol (#101).
            try:
                c = db()
                try:
                    _annota_admin(c, amministratore_id, 'backup_inviato')
                    c.commit()
                finally:
                    c.close()
            except Exception:
                logging.exception("backup inviato ma la traccia in admin_audit e' fallita")
            inviato_ok = True
            return True, None
        finally:
            # Invio non riuscito con un periodo prenotato: si LIBERA, o la notte resterebbe
            # segnata come fatta e nessun retry ripartirebbe. Su successo la prenotazione resta,
            # ed e' cio' che rende idempotente il retry del cron.
            if periodo is not None and not inviato_ok:
                _libera_periodo_backup(periodo)
    finally:
        _lucchetto_invio_backup.release()


@app.post('/api/admin/backup/invia')
async def invia_backup(request: Request):
    """Manda il backup al canale configurato. Due modi di autorizzarsi (#56 pezzo 3):

    - la **sessione dell'amministratore** — il bottone «Invia backup ora» nel pannello;
    - il **token del cron** (`BACKUP_CRON_TOKEN`) nell'header `X-Backup-Cron-Token`, per il job
      notturno di Railway che gira senza sessione.

    L'admin si prova per PRIMO e senza sollevare (`_amministratore_o_none`), cosi' il token puo'
    fare da secondo tentativo. Senza nessuno dei due → **404**, come tutto `/api/admin/*`: un 403
    confermerebbe a un estraneo che la rotta esiste. Il confronto del token e' a tempo costante e
    solo se il token e' configurato (stringa vuota non autorizza nessuno — fail-closed).

    L'invio (rete + I/O) gira in un thread (`asyncio.to_thread`): questa rotta e' `async`, e
    tenerlo sul loop bloccherebbe webhook e feed di tutti gli utenti. Un fallimento torna
    **400** col motivo, mai ingoiato e mai con il token dentro.
    """
    amministratore = _amministratore_o_none(request)
    cron = bool(BACKUP_CRON_TOKEN) and secrets.compare_digest(
        request.headers.get('X-Backup-Cron-Token', '').encode('utf-8'),
        BACKUP_CRON_TOKEN.encode('utf-8'))
    if not amministratore and not cron:
        raise HTTPException(404, 'not found')
    # Idempotenza persistente (#56): solo il CRON passa un `periodo` (la data UTC del giro),
    # cosi' un retry o una seconda replica non mandano due backup. Il BOTTONE dell'admin
    # invia sempre (periodo None): e' un'azione umana esplicita «invia ora».
    periodo = None if amministratore else time.strftime('%Y-%m-%d', time.gmtime())
    riuscito, motivo = await asyncio.to_thread(
        _invia_backup_al_canale, amministratore['id'] if amministratore else None, periodo)
    if not riuscito:
        raise HTTPException(400, f'invio del backup fallito: {motivo}')
    if amministratore:
        return _rispondi_con_sessione(amministratore['id'], amministratore['versione'],
                                      {'inviato': True})
    return {'inviato': True}


def _giro_di_promemoria(c, adesso):
    """Il giro vero e proprio: candidati, prenotazione, invio. Restituisce `(avvisati, falliti)`.

    Separato dalla rotta perche' la rotta deve solo garantire che la connessione si chiuda: un
    corpo lungo dentro un `try/finally` nasconde quale riga puo' sollevare.
    """
    avvisati, falliti = [], []
    candidati = c.execute(
        "SELECT id, telegram_id, access_expires_at FROM users WHERE status='attivo'"
        ' AND access_expires_at IS NOT NULL AND is_admin=0'
        ' AND (promemoria_per IS NULL OR promemoria_per != access_expires_at)').fetchall()
    for utente, telegram_id, scadenza in candidati:
        giorni = giorni_rimasti(scadenza, adesso=adesso)
        if giorni is None or not 0 < giorni <= GIORNI_PROMEMORIA:
            continue
        # **Si PRENOTA prima di inviare**, con una scrittura atomica e un `commit` immediato.
        # Due ragioni, entrambe alzate sulla PR #26:
        #
        # 1. due chiamate concorrenti leggevano gli stessi candidati e mandavano due avvisi per
        #    la stessa scadenza (Fable 5 e GPT-5.5). La `WHERE` porta il valore che ci si
        #    aspetta di trovare, quindi solo una delle due tocca una riga: chi trova
        #    `rowcount == 0` ha perso la corsa e passa oltre. E' la stessa forma della `WHERE`
        #    anti-corsa di `revoca_identita_stantia`;
        # 2. la transazione di scrittura **non deve restare aperta durante la rete**. La prima
        #    versione scriveva dentro il ciclo e faceva un `commit` solo alla fine: con dieci
        #    secondi di timeout per utente, SQLite teneva il lock di scrittura per tutta la
        #    durata del giro, e in quel tempo webhook e feed rispondevano «database is locked».
        #    Un promemoria che congela il feed e' molto peggio di un promemoria mancato.
        #    Bloccante di Fable 5.
        prenotata = c.execute(
            'UPDATE users SET promemoria_per=? WHERE id=?'
            ' AND (promemoria_per IS NULL OR promemoria_per != ?)',
            (scadenza, utente, scadenza))
        c.commit()
        if not prenotata.rowcount:
            continue
        riuscito, motivo = invia_messaggio_telegram(
            telegram_id,
            f'Il tuo accesso scade fra {giorni} giorni. Scrivi per rinnovare.')
        if riuscito:
            avvisati.append(utente)
        else:
            # La prenotazione si RILASCIA: un invio fallito non deve consumare il promemoria,
            # o il cliente non saprebbe mai di stare scadendo proprio nel caso in cui il canale
            # e' rotto. E si registra che non e' raggiungibile, perche' il proprietario deve
            # poterlo contattare a mano.
            # **Due scritture, e separarle e' la sostanza.** Sono due fatti diversi con due
            # condizioni diverse, e unirli in una sola istruzione ne perde uno:
            #
            # - «questo canale non funziona» e' vero SEMPRE: l'invio e' appena fallito. Va
            #   scritto senza condizioni, perche' e' l'unico modo in cui il proprietario scopre
            #   che quel cliente va contattato a mano;
            # - «rilascia la prenotazione» vale solo se la prenotazione e' ancora **la nostra**:
            #   se nel frattempo il proprietario ha rinnovato, `promemoria_per` porta ormai il
            #   ciclo nuovo, e cancellarla farebbe rimandare un avviso gia' mandato.
            #
            # La prima versione le aveva unite, quindi la condizione governava anche il flag: un
            # rinnovo durante l'invio fallito lasciava `telegram_reachable` a 1 e il proprietario
            # non sapeva del canale rotto — e il commento accanto affermava il contrario.
            # Rilievo di GPT-5.5 (la condizione) e poi di Claude Fable 5 (la regressione che la
            # mia correzione aveva introdotto), PR #26.
            c.execute('UPDATE users SET telegram_reachable=0 WHERE id=?', (utente,))
            c.execute('UPDATE users SET promemoria_per=NULL'
                      ' WHERE id=? AND promemoria_per=?', (utente, scadenza))
            c.commit()
            falliti.append({'utente': utente, 'motivo': motivo})
    return avvisati, falliti


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
    # Le colonne sono ELENCATE, e l'aggiornamento e' un UPSERT invece di un `INSERT OR
    # REPLACE`. Le due cose chiudono due difetti distinti introdotti dalla migrazione
    # dello schema, entrambi misurati:
    #
    # 1. senza elenco, l'INSERT dipendeva dal NUMERO di colonne, e con le sette
    #    aggiunte da `COLONNE_MULTIUTENTE` non era piu' valido:
    #    «table parsers has 14 columns but 7 values were supplied». Cioe' questo
    #    endpoint rispondeva 500 a ogni creazione o modifica di parser;
    # 2. `REPLACE` cancella la riga e la reinserisce, quindi le colonne non nominate
    #    tornano a NULL: cambiare l'header di un parser lo STACCAVA dal suo utente e
    #    ne azzerava l'`id`. Misurato: (1, 'parser_...', 0, 1) -> (None, None, None, None).
    #
    # `ON CONFLICT` nomina solo i campi del modello: tutto il resto della riga resta
    # com'era, che e' esattamente cio' che serve.
    #
    # I nomi vengono DAL MODELLO invece di essere ricopiati qui: una seconda lista
    # sarebbe una lista che divergera', e la divergenza sarebbe silenziosa nel verso
    # peggiore — un campo aggiunto a `ParserIn` e non qui verrebbe accettato dalla API
    # e non salvato. Che i nomi siano anche colonne di `parsers` e' vincolato da un
    # test, cosi' un campo nuovo senza colonna diventa rosso invece di dare 500.
    #
    # `INSERT OR IGNORE` seguito da `UPDATE` invece di `ON CONFLICT DO UPDATE`, che
    # sarebbe la forma piu' compatta: l'UPSERT esiste solo da SQLite 3.24 (2018), e la
    # versione di SQLite in produzione non e' una cosa che posso misurare da qui. Un
    # endpoint che funziona in locale e solleva su Railway e' peggio di una riga in
    # piu'. Le due istruzioni stanno nella stessa transazione, quindi il commit e'
    # unico. Rischio segnalato da GPT-5.5, e rimosso invece che documentato.
    campi = tuple(ParserIn.model_fields)
    aggiornabili = [x for x in campi if x != 'name']
    c.execute(f'INSERT OR IGNORE INTO parsers({", ".join(campi)})'
              f' VALUES ({", ".join("?" * len(campi))})',
              tuple(getattr(data, x) for x in campi))
    c.execute(f'UPDATE parsers SET {", ".join(f"{x}=?" for x in aggiornabili)}'
              ' WHERE name=?',
              tuple(getattr(data, x) for x in aggiornabili) + (data.name,))
    # Le colonne del multiutente subito, non al prossimo riavvio: vedi
    # `_completa_colonne_nuove`. Il proprietario e' PIERO perche' oggi e' l'unico
    # utente e questo endpoint e' protetto dal suo token di amministrazione: quando
    # servira' piu' utenti, qui va passato il proprietario della sessione.
    _completa_colonne_nuove(c, PIERO_PROFILE)
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
    try:
        # `BEGIN IMMEDIATE` PRIMA della lettura, e non e' pignoleria: in SQLite una
        # `SELECT` non apre nessuna transazione di scrittura, quindi due POST
        # concorrenti sullo stesso profilo leggerebbero entrambi lo stato di
        # partenza, staccherebbero lo stesso link vecchio e attaccherebbero
        # ciascuno il proprio — il profilo finisce con UN parser e i link con DUE,
        # cioe' il parser sostituito continua a girare su quella chat. E' la stessa
        # corsa SELECT-poi-scrittura della quota (PR #45) e della richiesta di
        # accesso (PR #26). Bloccante di Claude Fable 5 sul gate finale della #46,
        # riprodotto dal test dei due salvataggi simultanei.
        #
        # Sta DENTRO il `try`: sotto contesa il comando stesso solleva «database is
        # locked», e da fuori la connessione non verrebbe chiusa.
        c.execute('BEGIN IMMEDIATE')
        # La validazione del parser sta DENTRO la transazione, non prima: fuori
        # era un TOCTOU: un `DELETE /api/parsers` concorrente poteva cancellare
        # il parser fra il controllo e la scrittura, e il profilo veniva salvato
        # lo stesso — a nominare un parser che non esiste, senza nessun link. I
        # segnali di quella chat sparivano in silenzio: nessun errore, nessun
        # 4xx, il feed semplicemente fermo. `[REAL_FINDING]` di GPT-5.6 Sol al
        # gate finale della PR #46.
        get_parser(c, data.parser)
        # La riga PRIMA della scrittura: serve a togliere i link del parser che il
        # profilo nominava fino a un istante fa. Senza, cambiare parser lascerebbe
        # vivo il vecchio link e quella chat continuerebbe a far girare il parser
        # sostituito — il limite dichiarato sulla PR #44.
        prima = c.execute('SELECT chat_ids, parser FROM profiles WHERE name=?',
                          (data.name,)).fetchone()
        c.execute('INSERT OR REPLACE INTO profiles(name,chat_ids,parser) VALUES (?,?,?)',
                  (data.name, data.chat_ids, data.parser))
        _riconcilia_link_del_profilo(c, data.name, data.chat_ids, data.parser, prima)
        c.commit()
    finally:
        c.close()
    return {'ok': True, 'profile': data.name}

@app.delete('/api/profiles/{name}')
def delete_profile(name: str, x_admin_token: str | None = Header(None)):
    auth(x_admin_token)
    c = db()
    try:
        # `BEGIN IMMEDIATE` come in `save_profile`, e per la stessa ragione: fra
        # la lettura del profilo e la sua cancellazione un salvataggio
        # concorrente puo' attaccare un link NUOVO, che questa eliminazione non
        # conosce e quindi non toglie. Il profilo sparisce, il link resta — e col
        # travaso una-tantum quel parser elabora la chat per sempre, senza piu'
        # nessun giro che lo tolga. `[REAL_FINDING]` di Claude Fable 5 e di
        # GPT-5.6 Sol, indipendentemente, al gate finale della PR #46: e' la
        # regola 2 mancata da me, che avevo chiuso la corsa sul fratello
        # `save_profile` senza cercarla qui.
        c.execute('BEGIN IMMEDIATE')
        # I link prima della riga: letti dal profilo che sta per sparire, o non
        # ci sarebbe piu' modo di sapere quali erano i suoi. Senza, un profilo
        # eliminato lascerebbe la sua chat a far girare il suo parser — con il
        # travaso non piu' ripetuto, per sempre.
        riga = c.execute('SELECT chat_ids, parser FROM profiles WHERE name=?',
                         (name,)).fetchone()
        if riga:
            _stacca_link_del_profilo(c, name, riga[0], riga[1])
        c.execute('DELETE FROM profiles WHERE name=?', (name,))
        c.execute('DELETE FROM signals WHERE profile=?', (name,))
        c.commit()
    finally:
        c.close()
    return {'ok': True}

@app.post('/api/parsers/{name}/test')
def test_parser(name: str, data: MessageIn, x_admin_token: str | None = Header(None)):
    auth(x_admin_token)
    c = db()
    cfg = get_parser(c, name)
    parsed = elabora_messaggio(data.message, cfg)
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


# --------------------------------------------------------------------------- #
#  Parser dell'utente: CRUD legato alla SESSIONE, non al token admin.
#
#  Le rotte `/api/parsers*` qui sopra sono admin-token e globali (le usa il
#  proprietario). Queste `/api/me/parsers*` sono la faccia per-utente: ogni cliente
#  vede e tocca SOLO i propri parser (`user_id` dalla sessione, 404 sui parser di un
#  altro). Sono la base che la web app chiamera' al posto dei dati finti.
# --------------------------------------------------------------------------- #

def _slugifica(testo):
    """Un titolo → uno slug base: minuscolo, solo `a-z0-9-`, senza `-` ai bordi.

    Vuoto o fatto di soli simboli → `'parser'`, cosi' `_slug_libero` ha sempre una
    base da disambiguare invece di uno slug vuoto che collide con se stesso.
    """
    base = re.sub(r'[^a-z0-9]+', '-', (testo or '').lower()).strip('-')
    # Un tetto alla lunghezza: un titolo lunghissimo non deve generare uno slug (e
    # quindi un `name`) senza limite. 60 caratteri bastano a restare leggibile.
    return (base[:60].strip('-')) or 'parser'


def _vista_parser(riga):
    """La vista pubblica di un parser: mai il `name` interno, mai `user_id`.

    `riga` = (id, slug, titolo, active, config_json, ordine, versione, uid).
    `versione` e' la precondizione della PUT (#51): il client la rimanda al
    salvataggio e chi ce l'ha vecchia riceve 409 invece di sovrascrivere.
    `uid` e' l'altra precondizione (#75): l'identita' della RIGA, che il client
    rimanda su PUT e DELETE — senza, una scheda rimasta aperta sovrascriveva un
    parser eliminato e ricreato con lo stesso slug, perche' il ricreato riparte
    da `versione = 1` e il contatore da solo non li distingue.
    """
    return {'id': riga[0], 'slug': riga[1], 'titolo': riga[2], 'active': bool(riga[3]),
            'config': json.loads(riga[4]) if riga[4] else {}, 'ordine': riga[5],
            'versione': riga[6], 'uid': riga[7]}


def _valida_tetti_parser(titolo, config):
    """I tetti di DIMENSIONE, al confine di scrittura: creazione E modifica.

    Fonte unica (regola 3): un tetto controllato solo alla creazione lascerebbe
    gonfiare con la PUT un parser gia' dentro. Il messaggio dice il limite e non
    nomina niente di altrui: e' un errore dell'utente sul suo input.
    """
    if len(titolo) > MAX_TITOLO_PARSER:
        raise HTTPException(422, f'titolo troppo lungo: massimo {MAX_TITOLO_PARSER} caratteri')
    if len(json.dumps(config)) > MAX_CONFIG_PARSER:
        raise HTTPException(422, f'config troppo grande: massimo {MAX_CONFIG_PARSER} caratteri')


def _valida_config_parser(config):
    """Controlla una config al confine di SCRITTURA, con un errore chiaro all'utente.

    Due livelli. **Struttura:** deve essere un oggetto, con `match`/`columns` oggetti e
    ogni regola di colonna un oggetto — cosi' `null`, `[]`, `columns: []` danno un 422
    esplicito invece di un `AttributeError`. **Esecuzione:** un dry-run su un messaggio
    di prova, che scova i valori storti che la struttura non vede (un `pattern` o un
    valore non-stringa che farebbe sollevare `esegui_parser`).

    E' la validazione che CodeRabbit ha chiesto sulla PR #29, messa nel posto giusto:
    la creazione, dove l'utente riceve il motivo. Il fail-safe largo di
    `elabora_messaggio` resta come seconda rete sul percorso del webhook.
    """
    if not isinstance(config, dict):
        raise HTTPException(422, 'config deve essere un oggetto')
    match = config.get('match')
    if match is not None and not isinstance(match, dict):
        raise HTTPException(422, 'config.match deve essere un oggetto')
    # La condizione `match` di tipo `regex` compila il pattern come le colonne,
    # quindi un costrutto divergente (#88) va rifiutato anche qui — non solo nelle
    # colonne. `contains` e' una sottostringa letterale: non e' regex, non diverge.
    # Il match NON legge i flag (`i` cablato), quindi `\p{}` non puo' allinearsi con
    # `u` qui: `unicode_ok` resta False.
    if isinstance(match, dict) and match.get('type') == 'regex':
        _vieta_costrutto_regex_divergente('la condizione match', match.get('value') or '')
    colonne = config.get('columns')
    if colonne is not None and not isinstance(colonne, dict):
        raise HTTPException(422, 'config.columns deve essere un oggetto')
    for nome, regola in (colonne or {}).items():
        if not isinstance(regola, dict):
            raise HTTPException(422, f'la regola della colonna {nome!r} deve essere un oggetto')
        # Una chiave che non e' una delle 14 colonne veniva ACCETTATA e poi ignorata
        # in silenzio: `esegui_parser` itera su `HEADERS`, quindi la regola non
        # esisteva per il motore. Il caso peggiore e' completamente muto — `Prcie`
        # invece di `Price` lascia la quota vuota per sempre, `missing` non se ne
        # accorge (non e' obbligatoria) e `complete` resta `True`. Sulle
        # obbligatorie il difetto e' fail-closed ma la DIAGNOSI e' falsa: il
        # messaggio dice «manca EventName» e manda a controllare i delimitatori,
        # mentre la causa e' un refuso di due lettere. Issue #41, gap 1.
        #
        # La lista si DERIVA da `HEADERS`, non si ricopia: aggiungere una colonna al
        # CSV la rende disponibile al parser, e non esiste il caso «colonna che il
        # parser conosce e il CSV no».
        if nome not in HEADERS:
            vicine = difflib.get_close_matches(str(nome), HEADERS, n=1)
            suggerimento = f' Forse intendevi {vicine[0]!r}?' if vicine else ''
            raise HTTPException(
                422, f'{nome!r} non e\' una colonna del CSV.{suggerimento}')
        # I flag di una regola `regex` (Issue #86): al salvataggio si accetta solo
        # l'insieme che i due motori onorano IDENTICO (`FLAG_REGEX_COMUNI`), come
        # STRINGA. Un `flags` con `x`/`y`/`g` (che divergevano fra anteprima e feed,
        # #85) o non-stringa viene RIFIUTATO qui con un 422 chiaro, invece di
        # degradare in silenzio a runtime: cosi' nessun nuovo parser puo' nascere
        # con flag esotici. Le config gia' salvate restano gestite a runtime
        # (case-sensitive per `x`/`y`, fail-closed per i non-stringa; vedi #85).
        if regola.get('source') == 'regex':
            flags = regola.get('flags')
            if flags is not None and flags != '' and (
                    not isinstance(flags, str)
                    or any(c not in FLAG_REGEX_COMUNI for c in flags)):
                raise HTTPException(
                    422, f'la colonna {nome!r}: flag regex non supportato '
                         f'({flags!r}). Usa solo una stringa con i caratteri '
                         f'{", ".join(FLAG_REGEX_COMUNI)}.')
            # Costrutti `\w`/`\d`/`\b`/POSIX/`\p{}` divergenti fra i due motori
            # (#88, #90): rifiutati al Salva come i flag esotici, cosi' l'anteprima
            # non mente al feed. `\p{}` con `u` in flags si allinea → `unicode_ok`.
            unicode_ok = isinstance(flags, str) and 'u' in flags
            _vieta_costrutto_regex_divergente(
                f'la colonna {nome!r}', regola.get('pattern') or '', unicode_ok=unicode_ok)
    _valida_config_multi(config.get('multi'))
    # Numeri JSON non-finiti (`NaN`, `Infinity`): `request.json()` li accetta e
    # `json.dumps` di default li serializza come JSON NON standard. Verrebbero scritti,
    # ma `JSONResponse` li rifiuta quando riserializza `config` a ogni lista/creazione:
    # l'utente si troverebbe un **500 su OGNI risposta** che include quel parser — e
    # `esegui_parser('probe', ...)` non li tocca, perche' un campo inutilizzato non viene
    # mai letto dal motore. Si rifiutano qui, alla SCRITTURA, con un 422. Bloccante Major
    # di CodeRabbit sulla PR #30.
    try:
        json.dumps(config, allow_nan=False)
    except (ValueError, TypeError):
        raise HTTPException(422, 'config contiene numeri non validi: NaN e Infinity non sono ammessi') from None
    # Dry-run: la config deve ESEGUIRE senza sollevare. Il tempo-regex e' limitato dal
    # budget di `esegui_parser`, quindi anche un pattern cattivo qui e' innocuo.
    try:
        esegui_parser('probe', config)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, 'config non eseguibile: controlla la condizione e le regole delle colonne') from None


def _valida_config_multi(multi):
    """`config.multi` al confine di scrittura (#35 pezzo 2): 422 col motivo.

    La forma sbagliata non deve arrivare al motore, dove diventerebbe un
    comportamento MUTO: una chiave col refuso (`pricce`) verrebbe ignorata e
    la riga uscirebbe senza la modifica voluta — il caso `Prcie` della #41,
    identico sulle righe. E `enabled` non-booleano e' il piu' subdolo:
    `'false' !== false` in JS, quindi la riga resterebbe ATTIVA, l'opposto di
    cio' che l'utente credeva di scrivere. Il tetto conta TUTTE le righe
    dichiarate, anche le spente: sono storage e superficie, non solo feed.
    """
    if multi is None:
        return
    if not isinstance(multi, dict):
        raise HTTPException(422, 'config.multi deve essere un oggetto')
    # Anche il LIVELLO di multi rifiuta le chiavi sconosciute: `markes` con
    # un refuso passerebbe e il motore la ignorerebbe — righe salvate, zero
    # generate, nessun messaggio (segnalato da CodeRabbit sulla PR #69).
    elenchi = ('markets', 'selections')
    for chiave in multi:
        if chiave not in elenchi:
            vicine = difflib.get_close_matches(str(chiave), elenchi, n=1)
            suggerimento = f' Forse intendevi {vicine[0]!r}?' if vicine else ''
            raise HTTPException(
                422, f'{chiave!r} non e\' un elenco di config.multi.'
                + suggerimento)
    ammesse = set(CAMPI_MULTI) | {'enabled', 'start_after', 'end_before'}
    totale = 0
    for elenco in ('markets', 'selections'):
        righe = multi.get(elenco)
        if righe is None:
            continue
        if not isinstance(righe, list):
            raise HTTPException(422, f'config.multi.{elenco} deve essere una lista')
        for riga in righe:
            if not isinstance(riga, dict):
                raise HTTPException(
                    422, f'ogni riga di config.multi.{elenco} deve essere un oggetto')
            totale += 1
            for chiave, valore in riga.items():
                if chiave not in ammesse:
                    vicine = difflib.get_close_matches(
                        str(chiave), sorted(ammesse), n=1)
                    suggerimento = f' Forse intendevi {vicine[0]!r}?' if vicine else ''
                    raise HTTPException(
                        422, f'{chiave!r} non e\' un campo delle righe multi.'
                        + suggerimento)
                if chiave == 'enabled':
                    if not isinstance(valore, bool):
                        raise HTTPException(
                            422, 'enabled deve essere true o false')
                elif not (valore is None
                          or isinstance(valore, (str, int, float, bool))):
                    raise HTTPException(
                        422, f'{chiave} deve essere un testo o un numero')
    if totale > MAX_RIGHE_MULTI:
        raise HTTPException(
            422, f'troppe righe multi: massimo {MAX_RIGHE_MULTI} fra mercati '
            'e selezioni')


def _uid_parser(c):
    """Un identificatore di riga NON RIUSABILE per un parser (#73).

    Lo conia sqlite con `randomblob(16)`, la stessa fonte con cui la migrazione
    riempie le righe esistenti: una funzione sola perche' i due percorsi non
    possano divergere (regola 3). Sono 128 bit — la collisione e' fuori scala, e
    se accadesse l'indice UNIQUE `parsers_uid` la fermerebbe con un
    `IntegrityError`, che il retry della creazione tratta come una qualunque
    contesa sullo slug.

    Non e' `id`: `id` viene dal `rowid` e sqlite lo RIUSA (la tabella e' quella
    originale, senza AUTOINCREMENT), quindi non identifica una riga nel tempo —
    solo adesso.
    """
    return c.execute('SELECT lower(hex(randomblob(16)))').fetchone()[0]


def _crea_parser_utente(c, user_id, titolo, config, active):
    """Crea un parser di proprieta' di `user_id` e ne restituisce la vista pubblica.

    Il `name` (PRIMARY KEY globale, eredita' dello schema legacy) va reso univoco fra
    TUTTI gli utenti. Si deriva da `(user_id, slug)`, che l'indice UNIQUE garantisce
    univoco per utente, quindi `u{user_id}-{slug}` e' univoco globale. Lo `slug` e'
    l'identita' STABILE: non cambia se il titolo viene modificato, cosi' un riferimento
    allo slug non si rompe con una rinomina. `header` e' '' (NOT NULL dello schema
    legacy), inutile per un parser `config_json`.
    """
    # `_slug_libero` e `MAX(ordine)` sono letti con SELECT separate dall'INSERT: due
    # POST concorrenti dello stesso utente con lo stesso titolo calcolerebbero lo
    # stesso slug e il secondo INSERT violerebbe `UNIQUE (user_id, slug)`. Non e' un
    # 500: si RITENTA, ricalcolando lo slug — la seconda volta la riga dell'altro c'e'
    # gia' e la disambiguazione da' `-2`. Segnalato da GPT-5.5 e Claude Fable 5 sulla
    # PR #30; e' la stessa classe della corsa di login, qui risolta col retry perche'
    # il costo di un secondo tentativo e' trascurabile.
    nome = None
    falliti = set()   # slug gia' bruciati: da NON ricalcolare
    for _ in range(8):
        presi = falliti | {r[0] for r in c.execute(
            'SELECT slug FROM parsers WHERE user_id=?', (user_id,)).fetchall()}
        slug = _slug_libero(_slugifica(titolo), presi)
        candidato = f'u{user_id}-{slug}'
        try:
            # `ordine` calcolato NELLA INSERT, non con un SELECT separato: due POST
            # concorrenti leggerebbero lo stesso `MAX(ordine)` e salverebbero ordini
            # duplicati (precedenza ambigua fra i parser dello stesso utente). La
            # sottoquery gira dentro il lock di scrittura dell'INSERT, quindi e'
            # atomica. Segnalato da GPT-5.6 Sol sulla PR #30.
            c.execute(
                'INSERT INTO parsers(name, header, user_id, slug, titolo, config_json,'
                ' active, ordine, uid)'
                ' VALUES (?,?,?,?,?,?,?,'
                ' (SELECT COALESCE(MAX(ordine), -1) + 1 FROM parsers WHERE user_id=?), ?)',
                (candidato, '', user_id, slug, titolo, json.dumps(config),
                 1 if active else 0, user_id, _uid_parser(c)))
            nome = candidato
            break
        except sqlite3.IntegrityError:
            # Puo' essere la corsa su `UNIQUE(user_id, slug)` OPPURE una collisione sul
            # `name` (PRIMARY KEY globale): un parser legacy o admin chiamato gia'
            # `u{user_id}-{slug}` non comparirebbe fra gli slug dell'utente, quindi
            # ricalcolare dagli SOLI suoi slug darebbe all'infinito lo stesso nome. Si
            # segna lo slug come bruciato: il giro dopo `_slug_libero` ne prende un
            # altro e il nome cambia. Segnalato da Claude Fable 5 sulla PR #30.
            falliti.add(slug)
            continue
    if nome is not None:
        # La quota si misura DOPO l'INSERT, dentro il write-lock che l'INSERT ha
        # preso: misurata prima, il COUNT e' una SELECT che non apre la
        # transazione, e due POST concorrenti sull'ultimo posto leggevano
        # entrambi «uno sotto quota» e la bucavano — riprodotto dal test della
        # corsa. Cosi' il perdente conta a riga gia' inserita, riceve il 409 e il
        # rollback (la close senza commit del chiamante) toglie la sua riga.
        # `409` e non `422`: non e' l'input a essere storto, e' lo stato — la
        # quota, che si alza da variabile su Railway senza deploy.
        quanti = c.execute('SELECT COUNT(*) FROM parsers WHERE user_id=?',
                           (user_id,)).fetchone()[0]
        if quanti > MAX_PARSER_PER_UTENTE:
            raise HTTPException(
                409, f'quota parser esaurita: massimo {MAX_PARSER_PER_UTENTE} per utente')
    if nome is None:
        raise HTTPException(409, 'creazione non riuscita per contesa, riprova')
    # `id` = `rowid`, come fa la migrazione: e' il surrogato stabile a cui punta
    # `parser_chats.parser_id` (dispatch per-utente, PR successivo).
    c.execute('UPDATE parsers SET id=rowid WHERE name=? AND id IS NULL', (nome,))
    riga = c.execute('SELECT id, slug, titolo, active, config_json, ordine, versione, uid'
                     ' FROM parsers WHERE name=?', (nome,)).fetchone()
    return _vista_parser(riga)


@app.get('/api/me/parsers')
def lista_parser_miei(request: Request):
    """I parser dell'utente della sessione — MAI quelli di un altro."""
    utente = _sessione_valida(request)
    c = db()
    righe = c.execute(
        'SELECT id, slug, titolo, active, config_json, ordine, versione, uid'
        ' FROM parsers'
        ' WHERE user_id=? ORDER BY ordine, slug', (utente['id'],)).fetchall()
    c.close()
    return _rispondi_con_sessione(utente['id'], utente['versione'],
                                  [_vista_parser(r) for r in righe])


async def _json_dal_corpo(request):
    """Il corpo JSON della richiesta, con un tetto sui BYTE misurato PRIMA del parsing.

    `request.json()` deserializza qualunque cosa arrivi: un tenant autenticato
    poteva mandare un corpo da centinaia di megabyte e farlo materializzare in RAM
    sul container condiviso prima che i tetti sui CAMPI (`_valida_tetti_parser`)
    rispondessero 422 — a danno gia' fatto. Bloccante di GPT-5.6 Sol sulla PR #45.

    Due misure, perche' nessuna basta da sola: il `Content-Length` dichiarato ferma
    il client onesto senza leggere un byte; la lettura a pezzi ferma chi mente
    sull'intestazione o usa il chunked encoding, interrompendo lo stream appena il
    totale supera il tetto. Oltre il tetto → **413** col limite nel messaggio.

    Un corpo che non e' JSON valido solleva `ValueError` dal `json.loads`: i
    chiamanti lo trasformano nel loro 422, come facevano con `request.json()`.

    Il webhook Telegram NON passa di qui, deliberatamente: il suo 403 sul secret
    scatta prima di leggere il corpo (l'estraneo non arriva al parsing), i payload
    di Telegram sono piccoli per costruzione, e quel percorso e' area dichiarata
    sana (regola 5) — non si tocca dentro una correzione sul CRUD.
    """
    dichiarati = request.headers.get('content-length')
    if dichiarati is not None:
        try:
            annunciati = int(dichiarati)
        except ValueError:
            annunciati = None
        if annunciati is not None and annunciati > MAX_CORPO_JSON:
            raise HTTPException(
                413, f'corpo troppo grande: massimo {MAX_CORPO_JSON} byte')
    pezzi = []
    totale = 0
    async for pezzo in request.stream():
        totale += len(pezzo)
        if totale > MAX_CORPO_JSON:
            raise HTTPException(
                413, f'corpo troppo grande: massimo {MAX_CORPO_JSON} byte')
        pezzi.append(pezzo)
    return json.loads(b''.join(pezzi))


async def _parser_in_dal_corpo(request):
    """`ParserMioIn` dal corpo JSON, o `422` — senza mai riportare il corpo ricevuto.

    Letto qui e non nella firma della rotta: con `data: ParserMioIn` FastAPI
    validerebbe il corpo PRIMA del controllo di sessione, e un estraneo riceverebbe
    422 invece di 401 — la stessa conferma «questa rotta esiste» che il 401 serve a non
    dare. Stesso motivo, e stessa guardia (`test_autenticazione.py`), delle rotte
    `/api/admin/*`. `user_id`/`id` nel corpo vengono ignorati: il proprietario viene
    dalla sessione (Pydantic scarta i campi non dichiarati). Un corpo assente o non
    JSON → 422, come farebbe FastAPI.
    """
    try:
        return ParserMioIn(**(await _json_dal_corpo(request)))
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, 'corpo non valido: serve {"titolo": ..., "config": {...}}')


@app.post('/api/me/parsers')
async def crea_parser_mio(request: Request):
    """Crea un parser per l'utente della sessione. `user_id` viene DALLA SESSIONE."""
    utente = _sessione_valida(request)
    dati = await _parser_in_dal_corpo(request)
    titolo = dati.titolo.strip()
    if not titolo:
        raise HTTPException(422, 'titolo mancante')
    _valida_tetti_parser(titolo, dati.config)
    _valida_config_parser(dati.config)
    c = db()
    # `try/finally`: `_crea_parser_utente` puo' sollevare 409 (contesa esaurita), e
    # senza il `finally` la connessione resterebbe aperta con la transazione in corso,
    # rischiando un lock sulle richieste successive. Segnalato da Claude Fable 5, PR #30.
    try:
        # La modalita' «Da mercati Betfair» (#33) si valida DENTRO la connessione:
        # serve leggere la libreria dell'utente, e serve la sessione gia' accertata.
        _valida_betfair(c, utente['id'], dati.config)
        _valida_team_source(c, utente['id'], dati.config)
        parser = _crea_parser_utente(c, utente['id'], titolo, dati.config, dati.active)
        c.commit()
    finally:
        c.close()
    return _rispondi_con_sessione(utente['id'], utente['versione'], parser)


@app.put('/api/me/parsers/{slug}')
async def modifica_parser_mio(slug: str, request: Request):
    """Modifica un proprio parser. **404** (non 403) se lo slug non e' dell'utente.

    Lo `slug` non cambia con una rinomina del titolo: e' l'identita' stabile.
    """
    utente = _sessione_valida(request)
    dati = await _parser_in_dal_corpo(request)
    titolo = dati.titolo.strip()
    if not titolo:
        raise HTTPException(422, 'titolo mancante')
    _valida_tetti_parser(titolo, dati.config)
    _valida_config_parser(dati.config)
    c = db()
    try:
        # `uid` oltre a `id`: e' l'identita' NON RIUSABILE della riga (#73), ed
        # e' cio' che l'UPDATE usera' per essere sicuro di scrivere su QUESTO
        # parser e non su un omonimo ricreato nel frattempo.
        riga = c.execute('SELECT id, uid FROM parsers WHERE user_id=? AND slug=?',
                         (utente['id'], slug)).fetchone()
        if not riga:
            raise HTTPException(404, 'parser non trovato')
        # La precondizione di IDENTITA' (#75): il client rimanda l'`uid` che ha
        # letto, e se lo slug ora appartiene a un'ALTRA riga il salvataggio si
        # ferma qui. E' il caso delle due schede — una elimina e ricrea, l'altra
        # salva — che `versione` non intercetta, perche' il ricreato riparte da
        # 1. Il 409 dice «ricreato» e non «modificato altrove»: sono due cose
        # diverse per l'utente, e il rimedio e' lo stesso (ricarica), ma la
        # ragione va detta giusta.
        _controlla_identita(dati.uid, riga[1])
        # Stessa guardia della creazione (regola 3, fonte unica): un PUT che
        # spendesse una selezione altrui aggirerebbe il confine del POST.
        _valida_betfair(c, utente['id'], dati.config)
        _valida_team_source(c, utente['id'], dati.config)
        # La precondizione (#51) sta DENTRO l'UPDATE, non in un SELECT prima:
        # un solo statement e' atomico sotto il write-lock di SQLite, quindi
        # niente TOCTOU — il controllo client-side puo' solo restringere la
        # finestra, questo la chiude. `versione` avanza a OGNI scrittura,
        # anche incondizionata: e' cio' che fa perdere in modo visibile la
        # sessione rimasta indietro. Il vincolo su `uid` che l'accompagna
        # risponde a un'altra domanda — «e' ancora lo stesso parser?» — e non
        # sostituisce la versione: vedi `_aggiorna_parser`.
        scritto = _aggiorna_parser(c, utente['id'], riga[1], slug, titolo,
                                   json.dumps(dati.config), dati.active,
                                   dati.versione)
        if scritto is None and dati.versione is not None:
            # Il parser c'era al SELECT iniziale: se l'UPDATE non ha toccato
            # righe e' la versione a non combaciare — qualcun altro ha salvato
            # nel frattempo. (Se invece una DELETE concorrente l'ha portato
            # via, o l'ha sostituito un omonimo ricreato, lo dice la rilettura
            # qui sotto col suo 404: quella cerca lo STESSO `uid`.)
            ancora = c.execute('SELECT 1 FROM parsers WHERE user_id=? AND uid=?',
                               (utente['id'], riga[1])).fetchone()
            if ancora:
                raise HTTPException(
                    409, "ricarica il parser: e' stato modificato altrove")
        nuova = c.execute('SELECT id, slug, titolo, active, config_json, ordine, versione, uid'
                          ' FROM parsers WHERE user_id=? AND uid=?',
                          (utente['id'], riga[1])).fetchone()
        if nuova is None:
            # Una DELETE concorrente (rotta sync, threadpool anyio) ha svuotato la riga
            # fra il SELECT iniziale e l'UPDATE: la corsa l'ha vinta la cancellazione.
            # **404**, come se lo slug non ci fosse — non un 500 da `_vista_parser(None)`.
            # Bloccante di GPT-5.6 Sol sulla PR #30. La rilettura cerca per `uid` e non
            # per slug (#73): con lo slug tornerebbe il parser OMONIMO ricreato nel
            # frattempo, e la risposta direbbe «salvato» mostrando i dati di un altro.
            raise HTTPException(404, 'parser non trovato')
        c.commit()
    finally:
        c.close()
    return _rispondi_con_sessione(utente['id'], utente['versione'], _vista_parser(nuova))


def _controlla_identita(atteso, effettivo):
    """La precondizione di identita' di PUT e DELETE (#75).

    `atteso` e' l'`uid` che il client ha letto, `effettivo` quello che lo slug
    identifica adesso. Se differiscono, quello slug e' passato a un'altra riga:
    il parser che il client intendeva e' stato eliminato, e ne esiste un altro
    con lo stesso nome. Non e' un 404 — un parser con quel nome c'e' — ed e'
    diverso dal 409 di `versione`, che invece dice «la stessa riga e' cambiata».

    `atteso` None = richiesta incondizionata, come per `versione`: i chiamanti
    storici e le rotte chiamate a mano continuano a funzionare.
    """
    if atteso is not None and atteso != effettivo:
        raise HTTPException(
            409, "ricarica il parser: e' stato eliminato e ricreato altrove")


def _aggiorna_parser(c, user_id, uid, slug, titolo, config_json, active, versione):
    """L'UPDATE della PUT, vincolato all'IDENTITA' della riga letta (#73).

    Filtrava per `(user_id, slug)`, e `slug` torna libero appena il parser viene
    eliminato: un elimina+ricrea concorrente produce una riga nuova che quel
    filtro non distingue dalla vecchia. Misurato sul codice precedente — la PUT
    stantia toccava 1 riga e SOVRASCRIVEVA titolo e `config_json` del parser
    appena ricreato. E' peggio della DELETE gemella: il parser resta, continua a
    girare, e produce righe CSV con le regole vecchie senza nessun sintomo.

    La precondizione di `versione` (#51) NON copre questo caso e resta per il
    suo: `versione` parte da 1, il ricreato ha 1, ed e' proprio il valore che la
    richiesta stantia porta con se' (misurato). Le due guardie rispondono a
    domande diverse — «e' cambiato mentre lo modificavo?» e «e' ancora lo stesso
    parser?» — e vivono nello stesso statement senza sostituirsi.

    Restituisce True se ha scritto, None se nessuna riga combacia: il chiamante
    distingue il 409 (versione vecchia, riga ancora li') dal 404 (riga sparita).

    **Quale finestra chiude, e quale no.** Questo statement chiude quella
    DENTRO la richiesta: fra il `SELECT` con cui la rotta legge `uid` e la
    scrittura che ne segue. Non puo' chiudere quella client->server — se
    l'elimina+ricrea avviene PRIMA che la rotta legga, la rotta legge l'uid
    NUOVO, e da qui dentro le due cose sono indistinguibili. Misurato via HTTP
    al gate della PR #74 (bloccante di GPT-5.6 Sol): `PUT` con `versione: 1`
    dopo un elimina+ricrea rispondeva 200 e sovrascriveva il ricreato, perche'
    anche lui riparte da `versione = 1`.

    Quella seconda finestra e' chiusa dalla #75, **fuori di qui**: e' il client
    a nominare la riga che intendeva, e `_controlla_identita` confronta il suo
    `uid` con quello che lo slug identifica adesso, prima di arrivare a questo
    statement. Le due guardie sono in serie e non si sostituiscono: chi chiama
    senza `uid` (chiamanti storici, chiamate a mano) resta coperto solo da
    questa. Dettagli in SAAS.md.
    """
    parametri = [titolo, config_json, 1 if active else 0, user_id, uid, slug]
    condizione = ''
    if versione is not None:
        condizione = ' AND versione=?'
        parametri.append(versione)
    toccate = c.execute(
        'UPDATE parsers SET titolo=?, config_json=?, active=?, versione=versione+1'
        ' WHERE user_id=? AND uid=? AND slug=?' + condizione, parametri).rowcount
    return True if toccate else None


def _elimina_parser(c, user_id, uid):
    """La cascata del parser (link chat→parser → riga), col proprietario
    ripetuto DENTRO ogni statement (issue #65, stesso pattern della PR #64).

    Le associazioni chat→parser puntano a `parsers.id`: si rimuovono col
    parser, o resterebbero orfane e il dispatch per-utente le seguirebbe
    (segnalato da GPT-5.5 e Claude Fable 5 sulla PR #30). Misurato prima
    della correzione: il check di proprieta' della rotta e' una lettura, e
    con un travaso concorrente gia' committato la DELETE dei link per solo
    `parser_id` distruggeva i link dell'account superstite — il suo parser
    smetteva di ricevere dalla sua chat — mentre il parser sopravviveva e la
    rotta rispondeva `ok: true`. None = per QUESTO utente non c'e' nessun
    parser da eliminare → la rotta risponde 404.

    L'`id` sta anche nella DELETE del parser, non solo in quella dei link
    ([REAL_FINDING] di GPT-5.6 Sol al gate finale della PR #72, race ABA):
    con la sola coppia `(user_id, slug)` un elimina+ricrea concorrente dello
    stesso slug faceva divergere i due statement — la cascata puntava all'id
    vecchio (no-op), la DELETE colpiva il parser RICREATO, e i suoi link
    restavano orfani. E `parsers` e' la tabella originale SENZA AUTOINCREMENT
    (misurato dal test della race): sqlite riusa il rowid massimo, quindi un
    link orfano puo' venire EREDITATO da un parser futuro che riceve quello
    stesso id — segnali della chat altrui nel suo feed, peggio di una riga
    morta. I due statement devono parlare dello STESSO parser: quello che la
    rotta ha letto.

    **Il caso col rowid riusato, chiuso dalla #73.** La PR #72 aveva reso
    coerenti i due statement legandoli all'`id`, ma `id` viene dal `rowid` e
    sqlite lo riusa: un parser eliminato e ricreato con lo stesso slug riceveva
    lo STESSO id, e la richiesta stantia lo cancellava. Adesso entrambi gli
    statement passano da `uid`, che non si riusa mai: la riga ricreata ne ha uno
    nuovo, la richiesta stantia ne porta uno che non esiste piu', tocca zero
    righe e la rotta risponde 404 — come se la sostituzione fosse arrivata prima.
    La stessa colonna vincola l'UPDATE della PUT (`_aggiorna_parser`), che aveva
    il difetto gemello.
    """
    c.execute('DELETE FROM parser_chats WHERE parser_id IN'
              ' (SELECT id FROM parsers WHERE uid=? AND user_id=?)',
              (uid, user_id))
    tolte = c.execute('DELETE FROM parsers WHERE uid=? AND user_id=?',
                      (uid, user_id)).rowcount
    return True if tolte else None


@app.delete('/api/me/parsers/{slug}')
def elimina_parser_mio(slug: str, request: Request, uid: str | None = None):
    """Elimina un proprio parser. **404** se non e' dell'utente.

    `?uid=` e' la precondizione di identita' (#75), come il campo omonimo nel
    corpo della PUT: sta nella query perche' una DELETE non porta corpo. Con un
    valore, l'eliminazione riesce solo se lo slug identifica ANCORA quella riga
    — altrimenti **409**, e la scheda rimasta aperta non porta via il parser che
    l'utente ha appena ricreato. Senza, incondizionata come prima.
    """
    utente = _sessione_valida(request)
    c = db()
    try:
        riga = c.execute('SELECT uid FROM parsers WHERE user_id=? AND slug=?',
                         (utente['id'], slug)).fetchone()
        if not riga:
            raise HTTPException(404, 'parser non trovato')
        _controlla_identita(uid, riga[0])
        if _elimina_parser(c, utente['id'], riga[0]) is None:
            # Il parser e' sparito (DELETE o travaso concorrente), oppure e'
            # stato sostituito da un omonimo ricreato — che ha un `uid` nuovo e
            # non e' quello che questa richiesta voleva eliminare (#73). In
            # entrambi i casi: 404, come se fosse successo prima.
            raise HTTPException(404, 'parser non trovato')
        c.commit()
    finally:
        c.close()
    return _rispondi_con_sessione(utente['id'], utente['versione'], {'ok': True})


@app.post('/api/me/parsers/{slug}/test')
async def prova_parser_mio(slug: str, request: Request):
    """Prova un messaggio contro un proprio parser, A SECCO: niente scrittura nel feed.

    Restituisce `matched`, `missing`, `complete`, la `diagnosi` PER COLONNA (#25:
    stato/motivo/valore per ognuna delle 14) e — se completo — il `csv` e l'`event`.
    E' la base del motore diagnostico «perche' non ha fatto il parser»: il cliente vede
    se la condizione ha combaciato e quali colonne obbligatorie mancano, senza toccare
    il feed di nessuno. `esegui_parser` e' avvolto: una config che sollevasse da' un
    esito diagnostico, non un 500.

    Il corpo si legge a mano DOPO il controllo di sessione — come POST/PUT — o
    FastAPI validerebbe `MessageIn` prima e un estraneo con corpo malformato
    riceverebbe 422 invece di 401, rivelando che la rotta esiste. Segnalato da
    Claude Fable 5 e GPT-5.6 Sol sulla PR #30.
    """
    utente = _sessione_valida(request)
    try:
        dati = MessageIn(**(await _json_dal_corpo(request)))
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, 'corpo non valido: serve {"message": "..."}')
    c = db()
    try:
        riga = c.execute('SELECT config_json FROM parsers WHERE user_id=? AND slug=?',
                         (utente['id'], slug)).fetchone()
    finally:
        c.close()
    if not riga:
        raise HTTPException(404, 'parser non trovato')
    try:
        config = json.loads(riga[0]) if riga[0] else {}
        # La prova deve mostrare cio' che farebbe il webhook (#34 pezzo 3):
        # stessa risoluzione della mappa, per l'utente della sessione.
        mappa = None
        if isinstance(config, dict) and config.get('team_source') is not None:
            c2 = db()
            try:
                mappa = _mappa_team_source(c2, utente['id'], config['team_source'])
            finally:
                c2.close()
        risultato = esegui_parser(dati.message, config, mappa)
    except Exception:
        return _rispondi_con_sessione(utente['id'], utente['versione'],
                                      {'matched': False, 'missing': [], 'complete': False,
                                       # `diagnosi` vuota, non assente: la forma
                                       # della risposta e' una sola, e il pannello
                                       # mostra `errore` invece della tabella.
                                       'diagnosi': [],
                                       'errore': 'config non eseguibile'})
    # `scarti` nella risposta, non solo nel log: e' la diagnosi che dice all'utente
    # PERCHE' il messaggio non produce riga, e senza di essa la prova mostrerebbe
    # «non completo» con `missing` vuota — cioe' il sintomo senza la causa, che e'
    # il difetto che la #39 e la #41 esistono per chiudere.
    # Il multi-riga (#35 pezzo 2): la prova mostra il «k su N» del tranello 1 —
    # ogni riga generata col SUO esito, e il CSV composto delle sole complete,
    # gli stessi byte che scriverebbe il webhook. Senza `config.multi` la
    # lista porta la sola base e `csv` resta la riga di sempre
    # (`componi_feed` di un documento e' il documento).
    righe = risultato.get('righe') or []
    complete = [r for r in righe if r['complete']]
    # `diagnosi` (#25): la risposta PER COLONNA — stato, motivo e valore per
    # ognuna delle 14 — che il pannello mostra come tabella. E' la differenza fra
    # «non completo» e «SelectionName e' obbligatoria ed e' vuota: mappala su…»:
    # il sintomo contro la causa, con due livelli distinti (blocca / segnala).
    corpo = {'matched': risultato['matched'], 'missing': risultato['missing'],
             'scarti': risultato.get('scarti') or [],
             'avvisi': risultato.get('avvisi') or [],
             'diagnosi': risultato.get('diagnosi') or [],
             'complete': bool(complete),
             'righe': [{'row': [_testo_canonico(v) for v in r['row']],
                        'missing': r['missing'], 'scarti': r['scarti'],
                        'complete': r['complete'],
                        # La diagnosi PER RIGA (#25): col multi-riga il verdetto
                        # e' di ogni riga, quindi la tabella del pannello e' di
                        # ogni riga. Il `diagnosi` di primo livello resta quello
                        # della BASE, e con `config.multi` la base non e' una
                        # riga del feed: mostrarlo da solo spiegherebbe una riga
                        # che non esiste (Fable 5 e GPT-5.5, PR #104).
                        'diagnosi': r.get('diagnosi') or []} for r in righe]}
    if complete:
        # `_testo_canonico` come nel webhook (`esito_messaggio`): l'anteprima
        # della prova deve mostrare gli STESSI byte che uscirebbero nel feed.
        documenti = [make_csv([_testo_canonico(v) for v in r['row']])
                     for r in complete]
        corpo['event'] = ([_testo_canonico(v) for v in complete[0]['row']]
                          [HEADERS.index('EventName')])
        corpo['csv'] = componi_feed(documenti)
    return _rispondi_con_sessione(utente['id'], utente['versione'], corpo)


# ------------------------------------------------------------------------------
#  Verifica delle chat col codice usa-e-getta (#32, pezzo 3.2).
#
#  Cosa risolve: fino a qui l'unico modo di autorizzare un canale era
#  `/api/profiles` con l'admin token, cioe' il proprietario a mano — e la web app
#  lo dichiarava all'utente («arriva con uno dei prossimi aggiornamenti»). Un
#  cliente non poteva collegare da solo il canale da cui arrivano i suoi segnali.
#
#  Il meccanismo: l'utente chiede un codice, lo incolla NEL CANALE, il webhook lo
#  riconosce e registra la chat come sua. **Incollarlo nel canale e' la prova**:
#  chi non puo' scrivere li' dentro non puo' autorizzarlo, e non esiste altro
#  modo di dimostrare quel controllo senza far passare il proprietario.
#
#  Perche' NON indebolisce il filtro delle chat, che `CLAUDE.md` elenca fra le
#  aree da non toccare: il ramo del codice nel webhook e' l'eccezione che quello
#  stesso file gia' prevede, ed e' *tutta* l'eccezione. Registra una riga in
#  `chats` e consuma il codice; non tocca `signals`, non cerca parser, non
#  scrive nel feed. Vincolato da `test_il_codice_non_apre_il_feed`.
#
#  Il modello dati esisteva gia' — `chat_verifications(code, user_id,
#  expires_at, consumed_at)` e `chats.owner_user_id/verified_at` — creato da chi
#  progetto' lo schema e mai usato da nessuno: `grep` prima di questo PR dava
#  zero letture e zero scritture. Mancava il comportamento, non le tabelle.
# ------------------------------------------------------------------------------

# Dieci minuti: il tempo di passare dalla web app a Telegram e incollare. Piu'
# lungo allargherebbe la finestra in cui un codice letto da un estraneo nel
# canale resta spendibile; piu' corto farebbe scadere chi si distrae.
TTL_CODICE_VERIFICA_S = 600

# Alfabeto senza i caratteri che si confondono a leggerli (0/O, 1/I/l): il codice
# si trascrive a mano da uno schermo a una chat, e una «O» letta «0» produce un
# fallimento che l'utente non sa spiegarsi.
ALFABETO_CODICE = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'
PREFISSO_CODICE = 'BETRELAY-'
LUNGHEZZA_CODICE = 8


def _nuovo_codice_verifica() -> str:
    """Un codice con un prefisso RICONOSCIBILE, e il prefisso non e' estetica.

    Il webhook deve decidere, su ogni messaggio da una chat sconosciuta, se e' un
    tentativo di verifica o traffico da ignorare. Col prefisso quella decisione e'
    una forma, non una ricerca a database su ogni consegna: senza, ogni messaggio
    di ogni canale sconosciuto costerebbe una query su `chat_verifications`.

    `secrets.choice` e non `random`: e' un valore che autorizza qualcosa.
    """
    coda = ''.join(secrets.choice(ALFABETO_CODICE) for _ in range(LUNGHEZZA_CODICE))
    return PREFISSO_CODICE + coda


def _e_codice_di_verifica(testo: str) -> str | None:
    """Il codice se il testo ne e' uno, altrimenti `None`. Non tocca il database."""
    candidato = (testo or '').strip().upper()
    if not candidato.startswith(PREFISSO_CODICE):
        return None
    coda = candidato[len(PREFISSO_CODICE):]
    if len(coda) != LUNGHEZZA_CODICE or not all(ch in ALFABETO_CODICE for ch in coda):
        return None
    return candidato


def _vista_chat(riga):
    """La forma che la web app legge. Nessun campo che non le serva."""
    return {'id': riga[0], 'telegram_chat_id': riga[1], 'titolo': riga[2],
            'tipo': riga[3], 'verified_at': riga[4]}


CAMPI_CHAT = ('id', 'telegram_chat_id', 'title', 'type', 'verified_at')


def _accesso_attivo_o_403(utente):
    """Solo un accesso **attivo** (o l'amministratore) puo' collegare una chat.

    Il cancello non e' `ACCESSI_BLOCCATI`, ed e' la ragione per cui questa funzione
    esiste invece di riusare `_blocco_della_riga`: quella lista contiene solo
    `scaduto` e `sospeso`, quindi un utente **`registrato`** — appena entrato col
    Login Widget, mai approvato da nessuno — non e' bloccato da nessuna parte.

    Prima di questo PR quello stato non aveva conseguenze: un tale utente poteva
    creare parser, ma i parser senza chat non producono niente, e collegare una
    chat lo faceva **solo l'amministratore**. La verifica col codice apre quel
    passaggio a chiunque abbia una sessione — cioe' rende raggiungibile, con
    questa modifica, la catena «mi registro → creo un parser → verifico un canale
    → ricevo segnali» senza che nessuno mi abbia attivato.

    Misurato: senza questo cancello, un utente `registrato` scrive nel feed —
    `assert 1 == 0` in `test_un_utente_REGISTRATO_non_produce_segnali...`.
    Segnalato da CodeRabbit sulla PR #112, e l'avevo creduto gia' coperto: il
    primo end-to-end dava zero segnali per un utente `registrato`, ma il motivo
    era un altro (una config di prova tutta costante, scartata dal motore) e
    avevo attribuito la misura alla causa sbagliata.

    Il cancello sta **qui e non in `ACCESSI_BLOCCATI`**: aggiungere `registrato`
    a quella lista cambierebbe `/api/me`, il feed e il dispatch in un colpo solo,
    e toglierebbe il feed agli utenti legacy migrati con quello stato. La falla
    e' di questa capacita' nuova, e si chiude dove e' stata aperta.

    **403 e non 404**: la rotta esiste e l'utente e' autenticato, quindi non c'e'
    niente da nascondere; c'e' invece qualcosa da dirgli, perche' senza il motivo
    non saprebbe che deve chiedere l'attivazione.
    """
    if utente['is_admin']:
        return
    stato = stato_effettivo(utente['status'], utente['access_expires_at'])
    if stato != 'attivo':
        raise HTTPException(403, 'accesso non attivo: chiedi l\'attivazione'
                                 ' prima di collegare una chat')


def _chat_posseduta(c, user_id, chat_id):
    """La riga della chat se e' di quell'utente, altrimenti **404**.

    404 e non 403: un 403 confermerebbe che quella chat esiste, cioe' direbbe a un
    estraneo che il canale e' registrato sul servizio. Stessa regola dei parser.
    """
    riga = c.execute(
        f'SELECT {", ".join(CAMPI_CHAT)} FROM chats WHERE id=? AND owner_user_id=?',
        (chat_id, user_id)).fetchone()
    if not riga:
        raise HTTPException(404, 'chat non trovata')
    return riga


@app.get('/api/chats')
def lista_chat_mie(request: Request):
    """Le chat verificate dall'utente della sessione — MAI quelle di un altro."""
    utente = _sessione_valida(request)
    c = db()
    try:
        righe = c.execute(
            f'SELECT {", ".join(CAMPI_CHAT)} FROM chats WHERE owner_user_id=?'
            ' ORDER BY id', (utente['id'],)).fetchall()
    finally:
        c.close()
    return _rispondi_con_sessione(utente['id'], utente['versione'],
                                  [_vista_chat(r) for r in righe])


@app.post('/api/chats/verify/start')
def avvia_verifica_chat(request: Request):
    """Emette un codice usa-e-getta. **In chiaro una volta sola**, come il token del feed.

    Ogni chiamata **cancella ogni riga precedente** dello stesso utente, consumata
    o no. Due effetti, entrambi voluti: un codice dimenticato in una chat smette
    di valere appena se ne chiede un altro, e resta **una sola riga per utente** —
    che e' cio' che rende non ambigua la domanda «com'e' andata l'ultima verifica»
    in `verify/status`.

    La riga unica non e' un dettaglio implementativo: `chat_verifications` non ha
    un `created_at`, quindi senza di essa «l'ultimo codice» andrebbe dedotto
    dall'ordinamento su `expires_at` — che e' monotono con la creazione **solo**
    finche' nessuno lo riscrive, cioe' una premessa che nessun test puo' tenere.
    Meglio non avere la domanda che rispondere con un'euristica.
    """
    utente = _sessione_valida(request)
    _accesso_attivo_o_403(utente)
    codice = _nuovo_codice_verifica()
    adesso = int(time.time())
    c = db()
    try:
        c.execute('BEGIN IMMEDIATE')
        c.execute('DELETE FROM chat_verifications WHERE user_id=?', (utente['id'],))
        c.execute('INSERT INTO chat_verifications(code, user_id, expires_at)'
                  ' VALUES (?,?,?)', (codice, utente['id'], adesso + TTL_CODICE_VERIFICA_S))
        c.commit()
    finally:
        c.close()
    # `no-store`: questo corpo porta un valore che autorizza qualcosa, e non deve
    # restare in nessuna cache intermedia. Precedente in questo file: il download
    # del backup (#56). La POST non e' cacheabile per difetto, quindi e' cintura
    # oltre alle bretelle — ma su una risposta che porta un segreto la cintura si
    # mette. Nota: `/api/me/token` ha la stessa forma e NON lo imposta; e' una
    # rotta fuori da questo PR, e va guardata a parte.
    return _rispondi_con_sessione(
        utente['id'], utente['versione'],
        risposta=JSONResponse({'codice': codice,
                               'scade_fra_s': TTL_CODICE_VERIFICA_S},
                              headers={'Cache-Control': 'no-store'}))


@app.get('/api/chats/verify/status')
def stato_verifica_chat(request: Request):
    """«E' arrivato?» — per il sondaggio della web app mentre l'utente incolla.

    **Non ripete il codice.** Chi l'ha chiesto ce l'ha gia', e rimandarlo a ogni
    sondaggio lo moltiplicherebbe nei log del server e nella cronologia del
    browser: un valore che autorizza qualcosa esiste in chiaro una volta sola.
    """
    utente = _sessione_valida(request)
    adesso = int(time.time())
    c = db()
    try:
        # L'unica riga dell'utente: `verify/start` cancella le precedenti, quindi
        # questa E' l'ultima verifica — senza dedurlo da un ordinamento.
        riga = c.execute(
            'SELECT expires_at, consumed_at FROM chat_verifications'
            ' WHERE user_id=?', (utente['id'],)).fetchone()
        chat = None
        if riga and riga[1]:
            # La chat verificata DA QUESTO codice, correlata sul momento del
            # consumo: `_consuma_codice_di_verifica` scrive `verified_at` e
            # `consumed_at` con lo stesso valore, nella stessa transazione.
            chat = c.execute(
                f'SELECT {", ".join(CAMPI_CHAT)} FROM chats'
                ' WHERE owner_user_id=? AND verified_at=?',
                (utente['id'], riga[1])).fetchone()
    finally:
        c.close()
    in_attesa = bool(riga) and not riga[1] and riga[0] > adesso
    scaduto = bool(riga) and not riga[1] and riga[0] <= adesso
    return _rispondi_con_sessione(
        utente['id'], utente['versione'],
        {'in_attesa': in_attesa,
         'scaduto': scaduto,
         'scade_fra_s': max(0, riga[0] - adesso) if in_attesa else 0,
         'chat': _vista_chat(chat) if chat else None})


@app.delete('/api/chats/{chat_id}')
def elimina_chat_mia(chat_id: str, request: Request):
    """Toglie una propria chat e **i propri** link. 404 se non e' dell'utente.

    I link vanno via nella stessa transazione: una riga di `parser_chats` che
    riferisce una chat cancellata non sarebbe visibile da nessuna UI e il
    dispatch la leggerebbe ancora — la stessa classe del link orfano che la
    PR #4 chiuse sull'altro lato.

    **Ma solo i propri link, e questa e' la parte che si sbaglia facilmente.**
    Una chat posseduta da A puo' portare link a parser di B: il percorso legacy
    dei profili scrive `chats` e `parser_chats` separatamente, e nulla impone che
    il proprietario della chat sia il proprietario dei parser collegati. Un
    `DELETE FROM parser_chats WHERE chat_id=?` nudo fermerebbe i segnali di B, in
    silenzio e per mano di A. `[REAL_FINDING]` di OpenRouter Sol sulla PR #112.

    Quando restano link altrui la riga di `chats` **non** si cancella, o quei
    link diventerebbero orfani: il chiamante la **disconosce** — sparisce dalla
    sua lista e torna allo stato legacy, senza proprietario. Da li' non e'
    riadottabile con un codice, per la stessa ragione.
    """
    utente = _sessione_valida(request)
    numerico = _intero_o_404(chat_id, 'chat')
    c = db()
    try:
        c.execute('BEGIN IMMEDIATE')
        _chat_posseduta(c, utente['id'], numerico)
        c.execute('DELETE FROM parser_chats WHERE chat_id=? AND parser_id IN'
                  ' (SELECT id FROM parsers WHERE user_id=?)',
                  (numerico, utente['id']))
        altrui = c.execute('SELECT 1 FROM parser_chats WHERE chat_id=? LIMIT 1',
                           (numerico,)).fetchone()
        if altrui:
            c.execute('UPDATE chats SET owner_user_id=NULL, verified_at=NULL'
                      ' WHERE id=? AND owner_user_id=?', (numerico, utente['id']))
        else:
            c.execute('DELETE FROM chats WHERE id=? AND owner_user_id=?',
                      (numerico, utente['id']))
        c.commit()
    finally:
        c.close()
    return _rispondi_con_sessione(utente['id'], utente['versione'], {'ok': True})


def _parser_posseduto_id(c, user_id, slug):
    """L'id del parser se e' dell'utente, altrimenti **404**."""
    riga = c.execute('SELECT id FROM parsers WHERE user_id=? AND slug=?',
                     (user_id, slug)).fetchone()
    if not riga:
        raise HTTPException(404, 'parser non trovato')
    return riga[0]


@app.get('/api/me/parsers/{slug}/chats')
def chat_del_parser_mio(slug: str, request: Request):
    """Le chat collegate a un proprio parser."""
    utente = _sessione_valida(request)
    c = db()
    try:
        parser_id = _parser_posseduto_id(c, utente['id'], slug)
        righe = c.execute(
            'SELECT pc.chat_id FROM parser_chats pc'
            ' JOIN chats ch ON ch.id = pc.chat_id'
            ' WHERE pc.parser_id=? AND ch.owner_user_id=? ORDER BY pc.chat_id',
            (parser_id, utente['id'])).fetchall()
    finally:
        c.close()
    return _rispondi_con_sessione(utente['id'], utente['versione'],
                                  {'chat_ids': [r[0] for r in righe]})


@app.put('/api/me/parsers/{slug}/chats')
async def collega_chat_al_parser_mio(slug: str, request: Request):
    """Sostituisce l'insieme delle chat di un proprio parser.

    **I `chat_id` arrivano dal CORPO**, ed e' l'unico posto in cui un utente puo'
    nominare una risorsa che non e' sua: la proprieta' di OGNI id si verifica qui,
    dentro la stessa transazione della scrittura. Verificarla prima sarebbe un
    TOCTOU — una DELETE concorrente fra il controllo e l'INSERT lascerebbe un link
    a una chat sparita (stessa forma del difetto chiuso sulla PR #46).

    Il corpo si legge DOPO il controllo di sessione: con un modello Pydantic
    FastAPI validerebbe prima, e un estraneo con corpo malformato riceverebbe 422
    invece di 401 — cioe' saprebbe che la rotta esiste.
    """
    utente = _sessione_valida(request)
    try:
        dati = await _json_dal_corpo(request)
    except ValueError:
        raise HTTPException(422, 'corpo non valido') from None
    if not isinstance(dati, dict):
        # Un JSON valido che non e' un oggetto — una lista, una stringa, un numero,
        # `null` — arriva fin qui e fa esplodere `.get()` con `AttributeError`,
        # cioe' **500** a un utente autenticato per un corpo malformato. Segnalato
        # da CodeRabbit sulla PR #112.
        raise HTTPException(422, 'corpo non valido')
    _accesso_attivo_o_403(utente)
    grezzi = dati.get('chat_ids')
    if not isinstance(grezzi, list):
        raise HTTPException(422, 'chat_ids deve essere una lista')
    voluti = sorted({_intero_o_404(v, 'chat') for v in grezzi})
    c = db()
    try:
        c.execute('BEGIN IMMEDIATE')
        parser_id = _parser_posseduto_id(c, utente['id'], slug)
        for chat_id in voluti:
            _chat_posseduta(c, utente['id'], chat_id)
        c.execute('DELETE FROM parser_chats WHERE parser_id=?', (parser_id,))
        for chat_id in voluti:
            c.execute('INSERT OR IGNORE INTO parser_chats(parser_id, chat_id)'
                      ' VALUES (?,?)', (parser_id, chat_id))
        c.commit()
    finally:
        c.close()
    return _rispondi_con_sessione(utente['id'], utente['versione'],
                                  {'chat_ids': voluti})


def _consuma_codice_di_verifica(chat_id, codice):
    """Il ramo del webhook: registra la chat se il codice e' buono. **Niente altro.**

    Restituisce l'esito per il chiamante, e non solleva: una consegna di Telegram
    non deve fallire perche' un codice era scaduto.

    Tutto in **una** transazione, e il codice si consuma nello stesso commit in
    cui la chat viene scritta: o entrambi, o nessuno. Un consumo committato prima
    della scrittura brucerebbe il codice senza registrare niente, e l'utente si
    ritroverebbe a ricominciare senza sapere perche' — la stessa lezione del
    marker di `webhook_seen` sulla PR #44.

    **Una chat gia' di un altro utente non e' rubabile.** Chi puo' scrivere in un
    canale altrui potrebbe altrimenti portarselo via, e con esso i segnali che ci
    passano. Una chat SENZA proprietario (quelle create dal percorso legacy dei
    profili) viene invece adottata: l'utente ha dimostrato di controllare quel
    canale, e i link esistenti non si toccano.
    """
    adesso = int(time.time())
    c = db()
    try:
        c.execute('BEGIN IMMEDIATE')
        riga = c.execute(
            'SELECT user_id FROM chat_verifications'
            ' WHERE code=? AND consumed_at IS NULL AND expires_at > ?',
            (codice, adesso)).fetchone()
        if not riga:
            return {'ok': True, 'ignored': 'codice_non_valido'}
        utente_id = riga[0]
        esistente = c.execute(
            f'SELECT id, owner_user_id FROM chats WHERE telegram_chat_id=?'
            f' AND {TOPIC_CHAT}=?', (chat_id, '')).fetchone()
        if esistente and esistente[1] != utente_id:
            # Non e' di chi presenta il codice: non si tocca. Vale anche per la
            # chat SENZA proprietario, e quel caso e' il piu' insidioso dei due —
            # una riga legacy puo' portare link ai parser di ALTRI utenti, perche'
            # `_attacca_link_del_profilo` scrive `chats` e `parser_chats` in modo
            # indipendente e nulla impone che i due proprietari coincidano.
            # Adottarla darebbe al nuovo proprietario una chat che alimenta i
            # parser di qualcun altro. `[REAL_FINDING]` di OpenRouter Sol e
            # rilievo convergente di Claude Fable 5.1 sulla PR #112.
            #
            # Il codice NON si consuma: non e' colpa di chi l'ha chiesto se il
            # canale e' di un altro, e bruciarglielo lo costringerebbe a
            # ricominciare senza capire.
            return {'ok': True, 'ignored': 'chat_non_disponibile'}
        if esistente:
            c.execute('UPDATE chats SET owner_user_id=?, verified_at=? WHERE id=?',
                      (utente_id, adesso, esistente[0]))
        else:
            c.execute('INSERT INTO chats(telegram_chat_id, owner_user_id, verified_at)'
                      ' VALUES (?,?,?)', (chat_id, utente_id, adesso))
        c.execute('UPDATE chat_verifications SET consumed_at=? WHERE code=?',
                  (adesso, codice))
        c.commit()
    finally:
        c.close()
    return {'ok': True, 'verified': True}


# ------------------------------------------------------------------------------
#  Mercati Betfair per-utente (#33). Sport → mercato (MarketType + MarketName) →
#  selezioni (SelectionName), tutto creato dall'utente: NESSUN catalogo
#  incorporato. Il wizard del parser consuma questi dati come regole costanti
#  (`{source:'constant'}`), quindi il motore e il contratto CSV non cambiano.
# ------------------------------------------------------------------------------

def _campo_mercato(nome, valore, vieta_emoji=True):
    """Un campo della libreria: testo pulito, entro il tetto, senza emoji.

    L'emoji si vieta sui campi che FINISCONO nel CSV (MarketType, MarketName,
    SelectionName): XTrader li scarterebbe in silenzio (#42), e la guardia di
    `verify_csv` li fermerebbe comunque a valle — meglio il 422 alla creazione,
    dove l'utente riceve il motivo. Il nome dello sport invece resta libero: e'
    un'etichetta della sua UI e non tocca mai il feed.
    """
    if not isinstance(valore, str):
        raise HTTPException(422, f'{nome} deve essere una stringa')
    valore = valore.strip()
    if not valore:
        raise HTTPException(422, f'{nome} mancante')
    if len(valore) > MAX_CAMPO_MERCATO:
        raise HTTPException(422, f'{nome} troppo lungo: massimo {MAX_CAMPO_MERCATO} caratteri')
    if vieta_emoji and _EMOJI.search(valore):
        raise HTTPException(422, f'{nome} contiene un simbolo che XTrader non accetta: solo testo')
    return valore


def _intero_o_404(valore, cosa):
    """Un id di percorso non numerico e' un 404, non un 422 (vedi `_identificativo_o_404`)."""
    try:
        return int(valore)
    except (TypeError, ValueError):
        raise HTTPException(404, f'{cosa} non trovato') from None


def _sport_o_404(c, user_id, slug):
    """Lo sport DELL'UTENTE, o 404: uno slug altrui non esiste, per definizione."""
    riga = c.execute('SELECT id, slug, nome FROM sports WHERE user_id=? AND slug=?',
                     (user_id, slug)).fetchone()
    if not riga:
        raise HTTPException(404, 'sport non trovato')
    return riga


def _mercato_o_404(c, sport_id, mid):
    mid = _intero_o_404(mid, 'mercato')
    riga = c.execute('SELECT id, market_type, market_name FROM betfair_markets'
                     ' WHERE id=? AND sport_id=?', (mid, sport_id)).fetchone()
    if not riga:
        raise HTTPException(404, 'mercato non trovato')
    return riga


def _mercati_di(c, user_id, sport_id):
    """I mercati di uno sport, col proprietario ripetuto NELLA stessa SELECT.

    Il check di proprieta' della rotta e' una lettura che puo' invecchiare
    (issue #65, variante lettura): un travaso concorrente sposta lo sport fra
    il check e questa SELECT, e filtrare per solo `sport_id` farebbe leggere
    alla richiesta in volo i mercati ormai dell'account superstite.
    """
    return c.execute('SELECT m.id, m.market_type, m.market_name'
                     ' FROM betfair_markets m JOIN sports s ON s.id = m.sport_id'
                     ' WHERE m.sport_id=? AND s.user_id=?'
                     ' ORDER BY m.market_type, m.market_name',
                     (sport_id, user_id)).fetchall()


def _selezioni_di(c, user_id, market_id):
    """Le selezioni di un mercato, vincolate al proprietario come `_mercati_di`."""
    return [{'id': r[0], 'selectionName': r[1]} for r in c.execute(
        'SELECT b.id, b.selection_name FROM betfair_selections b'
        ' JOIN betfair_markets m ON m.id = b.market_id'
        ' JOIN sports s ON s.id = m.sport_id'
        ' WHERE b.market_id=? AND s.user_id=? ORDER BY b.id',
        (market_id, user_id)).fetchall()]


def _inserisci_mercato(c, user_id, sport_id, tipo, nome):
    """INSERT del mercato CONDIZIONATO allo sport ANCORA dell'utente, in UNO statement.

    Il controllo di proprieta' delle rotte e' una LETTURA, e fra quella lettura e
    l'INSERT una DELETE concorrente dello sport puo' committare: l'INSERT diretto
    scriveva una riga orfana — invisibile alle API (la proprieta' si risolve per
    join, e uno sport sparito non si joina piu') e non piu' eliminabile, storage
    perso. Il `WHERE EXISTS` gira dentro il write-lock dell'INSERT, quindi vede
    lo stato vero: sport sparito → zero righe inserite → il chiamante risponde
    404, come se la lettura fosse arrivata dopo la DELETE.
    [REAL_FINDING] di Claude Fable 5 sulla PR #55; misurato prima di correggere:
    1 riga orfana con l'INSERT diretto.

    Il `user_id` nella subquery e' l'estensione della #65: la stessa lettura
    puo' invecchiare per un TRAVASO concorrente (`riconcilia_su_utente`), e
    l'EXISTS sul solo id avrebbe scritto nella libreria dell'account
    superstite (misurato prima di correggere: riga scritta).

    Restituisce l'id del mercato, o None se lo sport non esiste piu' o non e'
    (piu') dell'utente. Un doppione solleva `sqlite3.IntegrityError`, come
    l'INSERT diretto.
    """
    c.execute('INSERT INTO betfair_markets(sport_id, market_type, market_name)'
              ' SELECT ?,?,? WHERE EXISTS'
              ' (SELECT 1 FROM sports WHERE id=? AND user_id=?)',
              (sport_id, tipo, nome, sport_id, user_id))
    if not c.execute('SELECT changes()').fetchone()[0]:
        return None
    return c.execute('SELECT last_insert_rowid()').fetchone()[0]


def _inserisci_selezione(c, user_id, market_id, selezione):
    """Come `_inserisci_mercato`, per la selezione: il padre e' il mercato.

    Copre anche lo sport eliminato nel frattempo: la sua DELETE porta via i
    mercati nella stessa transazione, quindi il mercato sparito e' il sintomo
    unico da controllare. Stessa classe, stesso rimedio (regola 2). Il JOIN su
    `sports.user_id` copre il travaso concorrente (#65), come sopra.
    """
    c.execute('INSERT INTO betfair_selections(market_id, selection_name)'
              ' SELECT ?,? WHERE EXISTS'
              ' (SELECT 1 FROM betfair_markets m JOIN sports s ON s.id = m.sport_id'
              '  WHERE m.id=? AND s.user_id=?)',
              (market_id, selezione, market_id, user_id))
    if not c.execute('SELECT changes()').fetchone()[0]:
        return None
    return c.execute('SELECT last_insert_rowid()').fetchone()[0]


def _elimina_mercato(c, user_id, market_id):
    """La cascata del mercato (selezioni → riga), vincolata al proprietario
    DENTRO ogni statement, come `_elimina_competizione` (PR #64).

    Misurato prima della correzione (#65): col travaso gia' committato, i due
    DELETE per solo id distruggevano mercato e selezioni ormai dell'account
    superstite. None = per QUESTO utente non c'e' niente da eliminare → 404.
    """
    c.execute('DELETE FROM betfair_selections WHERE market_id IN'
              ' (SELECT m.id FROM betfair_markets m JOIN sports s'
              '  ON s.id = m.sport_id WHERE m.id=? AND s.user_id=?)',
              (market_id, user_id))
    tolte = c.execute('DELETE FROM betfair_markets WHERE id=? AND sport_id IN'
                      ' (SELECT id FROM sports WHERE user_id=?)',
                      (market_id, user_id)).rowcount
    return True if tolte else None


def _elimina_selezione(c, user_id, market_id, selezione_id):
    """La singola selezione, vincolata come sopra. None = niente da eliminare."""
    tolte = c.execute('DELETE FROM betfair_selections WHERE id=? AND market_id IN'
                      ' (SELECT m.id FROM betfair_markets m JOIN sports s'
                      '  ON s.id = m.sport_id WHERE m.id=? AND s.user_id=?)',
                      (selezione_id, market_id, user_id)).rowcount
    return True if tolte else None


async def _oggetto_dal_corpo(request, forma):
    """Il corpo JSON come dict, o 422 con la forma attesa. Dopo la sessione, sempre."""
    try:
        corpo = await _json_dal_corpo(request)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, f'corpo non valido: serve {forma}') from None
    if not isinstance(corpo, dict):
        raise HTTPException(422, f'corpo non valido: serve {forma}')
    return corpo


@app.get('/api/me/sports')
def sport_miei(request: Request):
    """Gli sport dell'utente della sessione. Al primo login: VUOTO, per progetto."""
    utente = _sessione_valida(request)
    c = db()
    try:
        righe = c.execute(
            'SELECT s.slug, s.nome,'
            ' (SELECT COUNT(*) FROM betfair_markets m WHERE m.sport_id = s.id)'
            ' FROM sports s WHERE s.user_id=? ORDER BY s.nome', (utente['id'],)).fetchall()
    finally:
        c.close()
    return _rispondi_con_sessione(utente['id'], utente['versione'], {'sports': [
        {'slug': r[0], 'nome': r[1], 'mercati': r[2]} for r in righe]})


@app.post('/api/me/sports')
async def crea_sport_mio(request: Request):
    """Crea uno sport. Lo slug si disambigua col retry, come i parser (PR #30)."""
    utente = _sessione_valida(request)
    corpo = await _oggetto_dal_corpo(request, '{"nome": "..."}')
    nome = _campo_mercato('nome', corpo.get('nome', ''), vieta_emoji=False)
    c = db()
    try:
        scelto = None
        falliti = set()
        for _ in range(8):
            presi = falliti | {r[0] for r in c.execute(
                'SELECT slug FROM sports WHERE user_id=?', (utente['id'],)).fetchall()}
            slug = _slug_libero(_slugifica(nome), presi)
            try:
                c.execute('INSERT INTO sports(user_id, slug, nome) VALUES (?,?,?)',
                          (utente['id'], slug, nome))
                scelto = slug
                break
            except sqlite3.IntegrityError:
                falliti.add(slug)
                continue
        if scelto is None:
            raise HTTPException(409, 'creazione non riuscita per contesa, riprova')
        # La quota si misura DOPO l'INSERT, dentro il suo write-lock: misurata
        # prima, due POST concorrenti sull'ultimo posto la bucherebbero entrambi
        # (stessa corsa della quota parser, PR #30). Il perdente riceve 409 e la
        # close senza commit toglie la sua riga.
        quanti = c.execute('SELECT COUNT(*) FROM sports WHERE user_id=?',
                           (utente['id'],)).fetchone()[0]
        if quanti > MAX_SPORT_PER_UTENTE:
            raise HTTPException(
                409, f'quota sport esaurita: massimo {MAX_SPORT_PER_UTENTE} per utente')
        c.commit()
    finally:
        c.close()
    return _rispondi_con_sessione(utente['id'], utente['versione'],
                                  {'slug': scelto, 'nome': nome})


@app.delete('/api/me/sports/{slug}')
def elimina_sport_mio(slug: str, request: Request):
    """Elimina uno sport e la sua libreria. La cascata e' esplicita e in UNA transazione.

    I parser gia' salvati NON si toccano: le loro regole sono costanti, la
    libreria e' provenienza e non dipendenza viva (vedi `_valida_betfair`).
    """
    utente = _sessione_valida(request)
    c = db()
    try:
        sport = _sport_o_404(c, utente['id'], slug)
        # La cascata intera — mercati, selezioni, competizioni (#34), squadre,
        # alias, sport — vive in `_elimina_sport`, con OGNI statement vincolato
        # al proprietario: la proprieta' letta qui sopra e' una lettura e puo'
        # invecchiare prima del write-lock (gate di Sol, PR #64). Le sorgenti
        # NON si toccano: sono dell'utente, non dello sport.
        if _elimina_sport(c, utente['id'], sport[0]) is None:
            raise HTTPException(404, 'sport non trovato')
        c.commit()
    finally:
        c.close()
    return _rispondi_con_sessione(utente['id'], utente['versione'], {'ok': True})


@app.get('/api/me/sports/{slug}/mercati')
def mercati_miei(slug: str, request: Request):
    """I mercati dello sport, con le selezioni ANNIDATE: il wizard legge tutto in una chiamata."""
    utente = _sessione_valida(request)
    c = db()
    try:
        sport = _sport_o_404(c, utente['id'], slug)
        mercati = []
        for r in _mercati_di(c, utente['id'], sport[0]):
            mercati.append({'id': r[0], 'marketType': r[1], 'marketName': r[2],
                            'selezioni': _selezioni_di(c, utente['id'], r[0])})
    finally:
        c.close()
    return _rispondi_con_sessione(utente['id'], utente['versione'], {'mercati': mercati})


@app.post('/api/me/sports/{slug}/mercati')
async def crea_mercato_mio(slug: str, request: Request):
    """Crea un mercato con le sue selezioni iniziali. Doppione esatto → 409."""
    utente = _sessione_valida(request)
    corpo = await _oggetto_dal_corpo(
        request, '{"marketType": ..., "marketName": ..., "selections": [...]}')
    tipo = _campo_mercato('marketType', corpo.get('marketType', ''))
    nome = _campo_mercato('marketName', corpo.get('marketName', ''))
    grezze = corpo.get('selections', [])
    if not isinstance(grezze, list):
        raise HTTPException(422, 'selections deve essere una lista di stringhe')
    if len(grezze) > MAX_SELEZIONI_PER_MERCATO:
        raise HTTPException(
            422, f'troppe selezioni: massimo {MAX_SELEZIONI_PER_MERCATO} per mercato')
    selezioni = [_campo_mercato('selectionName', s) for s in grezze]
    c = db()
    try:
        sport = _sport_o_404(c, utente['id'], slug)
        try:
            mid = _inserisci_mercato(c, utente['id'], sport[0], tipo, nome)
        except sqlite3.IntegrityError:
            raise HTTPException(409, 'mercato gia\' presente in questo sport') from None
        if mid is None:
            # Lo sport e' morto fra il controllo e l'INSERT (DELETE concorrente).
            raise HTTPException(404, 'sport non trovato')
        quanti = c.execute('SELECT COUNT(*) FROM betfair_markets WHERE sport_id=?',
                           (sport[0],)).fetchone()[0]
        if quanti > MAX_MERCATI_PER_SPORT:
            raise HTTPException(
                409, f'quota mercati esaurita: massimo {MAX_MERCATI_PER_SPORT} per sport')
        for selezione in selezioni:
            try:
                c.execute('INSERT INTO betfair_selections(market_id, selection_name)'
                          ' VALUES (?,?)', (mid, selezione))
            except sqlite3.IntegrityError:
                # Un doppione NELLA stessa richiesta: si rifiuta tutto, e la close
                # senza commit non lascia il mercato mezzo scritto.
                raise HTTPException(
                    409, f'selezione duplicata: {selezione!r}') from None
        c.commit()
        creato = {'id': mid, 'marketType': tipo, 'marketName': nome,
                  'selezioni': _selezioni_di(c, utente['id'], mid)}
    finally:
        c.close()
    return _rispondi_con_sessione(utente['id'], utente['versione'], creato)


@app.delete('/api/me/sports/{slug}/mercati/{mid}')
def elimina_mercato_mio(slug: str, mid: str, request: Request):
    utente = _sessione_valida(request)
    c = db()
    try:
        sport = _sport_o_404(c, utente['id'], slug)
        mercato = _mercato_o_404(c, sport[0], mid)
        # Il vincolo di proprieta' si ripete DENTRO gli statement (#65): il
        # check qui sopra e' una lettura, e un travaso concorrente puo'
        # invecchiarla prima del write-lock.
        if _elimina_mercato(c, utente['id'], mercato[0]) is None:
            raise HTTPException(404, 'mercato non trovato')
        c.commit()
    finally:
        c.close()
    return _rispondi_con_sessione(utente['id'], utente['versione'], {'ok': True})


@app.get('/api/me/sports/{slug}/mercati/{mid}/selezioni')
def selezioni_mie(slug: str, mid: str, request: Request):
    """Le selezioni di UN mercato, da sole.

    Il wizard NON passa di qui: legge quelle annidate nella lista dei mercati
    (una chiamata sola). La rotta esiste perche' la #33 la elenca e per chi
    vuole solo la lista corta — descriverla come «la tendina del wizard»
    mandava a cercare un chiamante che non esiste (CodeRabbit, PR #55).
    """
    utente = _sessione_valida(request)
    c = db()
    try:
        sport = _sport_o_404(c, utente['id'], slug)
        mercato = _mercato_o_404(c, sport[0], mid)
        selezioni = _selezioni_di(c, utente['id'], mercato[0])
    finally:
        c.close()
    return _rispondi_con_sessione(utente['id'], utente['versione'],
                                  {'selezioni': selezioni})


@app.post('/api/me/sports/{slug}/mercati/{mid}/selezioni')
async def crea_selezione_mia(slug: str, mid: str, request: Request):
    """«Aggiungi» dello sketch: una selezione in piu', quante ne servono."""
    utente = _sessione_valida(request)
    corpo = await _oggetto_dal_corpo(request, '{"selectionName": "..."}')
    selezione = _campo_mercato('selectionName', corpo.get('selectionName', ''))
    c = db()
    try:
        sport = _sport_o_404(c, utente['id'], slug)
        mercato = _mercato_o_404(c, sport[0], mid)
        try:
            sid = _inserisci_selezione(c, utente['id'], mercato[0], selezione)
        except sqlite3.IntegrityError:
            raise HTTPException(409, 'selezione gia\' presente in questo mercato') from None
        if sid is None:
            # Il mercato (o il suo sport) e' morto fra il controllo e l'INSERT.
            raise HTTPException(404, 'mercato non trovato')
        quante = c.execute('SELECT COUNT(*) FROM betfair_selections WHERE market_id=?',
                           (mercato[0],)).fetchone()[0]
        if quante > MAX_SELEZIONI_PER_MERCATO:
            raise HTTPException(
                409, f'quota selezioni esaurita: massimo {MAX_SELEZIONI_PER_MERCATO} per mercato')
        c.commit()
    finally:
        c.close()
    return _rispondi_con_sessione(utente['id'], utente['versione'],
                                  {'id': sid, 'selectionName': selezione})


@app.delete('/api/me/sports/{slug}/mercati/{mid}/selezioni/{sid}')
def elimina_selezione_mia(slug: str, mid: str, sid: str, request: Request):
    utente = _sessione_valida(request)
    c = db()
    try:
        sport = _sport_o_404(c, utente['id'], slug)
        mercato = _mercato_o_404(c, sport[0], mid)
        sid = _intero_o_404(sid, 'selezione')
        # `user_id` DENTRO lo statement (#65): la proprieta' letta sopra puo'
        # invecchiare per un travaso concorrente prima del write-lock.
        if _elimina_selezione(c, utente['id'], mercato[0], sid) is None:
            raise HTTPException(404, 'selezione non trovata')
        c.commit()
    finally:
        c.close()
    return _rispondi_con_sessione(utente['id'], utente['versione'], {'ok': True})


# --------------------------------------------------- sorgenti squadre (#34)

def _sorgente_o_404(c, user_id, sid):
    """La sorgente DELL'UTENTE, o 404: un id altrui non esiste, per definizione."""
    sid = _intero_o_404(sid, 'sorgente')
    riga = c.execute('SELECT id, nome FROM sorgenti_squadre WHERE id=? AND user_id=?',
                     (sid, user_id)).fetchone()
    if not riga:
        raise HTTPException(404, 'sorgente non trovata')
    return riga


def _competizione_o_404(c, user_id, cid):
    cid = _intero_o_404(cid, 'competizione')
    riga = c.execute(
        'SELECT k.id, k.nome, s.slug FROM competizioni k JOIN sports s'
        ' ON s.id = k.sport_id WHERE k.id=? AND k.user_id=?',
        (cid, user_id)).fetchone()
    if not riga:
        raise HTTPException(404, 'competizione non trovata')
    return riga


def _inserisci_competizione(c, user_id, sport_id, nome):
    """INSERT condizionato all'esistenza dello sport, in UNO statement.

    Stessa classe TOCTOU di `_inserisci_mercato` ([REAL_FINDING] della PR #55),
    applicata da subito: fra il controllo di proprieta' (una lettura) e l'INSERT
    una DELETE concorrente dello sport puo' committare, e l'INSERT diretto
    scriverebbe una competizione orfana — invisibile alle API e non piu'
    eliminabile. None = sport sparito, il chiamante risponde 404.
    """
    c.execute('INSERT INTO competizioni(user_id, sport_id, nome)'
              ' SELECT ?,?,? WHERE EXISTS'
              ' (SELECT 1 FROM sports WHERE id=? AND user_id=?)',
              (user_id, sport_id, nome, sport_id, user_id))
    if not c.execute('SELECT changes()').fetchone()[0]:
        return None
    return c.execute('SELECT last_insert_rowid()').fetchone()[0]


def _inserisci_squadra(c, user_id, competizione_id, nome):
    """Come `_inserisci_competizione`, per la squadra: il padre e' la competizione.

    Copre anche lo sport eliminato nel frattempo: la sua DELETE porta via le
    competizioni nella stessa transazione (vedi `elimina_sport_mio`), quindi la
    competizione sparita e' il sintomo unico da controllare. Il vincolo
    `user_id` nella subquery e' il [REAL_FINDING] di GPT-5.6 Sol al gate della
    PR #64: la proprieta' letta prima del write-lock puo' invecchiare — un
    travaso concorrente della riconciliazione sposta il padre fra il check e
    la scrittura — quindi ogni statement la ripete DENTRO il write-lock.
    """
    c.execute('INSERT INTO squadre_betfair(competizione_id, nome)'
              ' SELECT ?,? WHERE EXISTS'
              ' (SELECT 1 FROM competizioni WHERE id=? AND user_id=?)',
              (competizione_id, nome, competizione_id, user_id))
    if not c.execute('SELECT changes()').fetchone()[0]:
        return None
    return c.execute('SELECT last_insert_rowid()').fetchone()[0]


def _elimina_squadra(c, user_id, competizione_id, squadra_id):
    """La «× squadra», vincolata al proprietario DENTRO ogni statement.

    Stessa ragione del vincolo in `_inserisci_squadra` (Sol, PR #64): il
    controllo di proprieta' della rotta e' una lettura, e questi statement
    girano nel write-lock — la subquery su `competizioni.user_id` vede lo
    stato vero. None = niente da eliminare per QUESTO utente: la rotta
    risponde 404, identico al padre gia' sparito.
    """
    c.execute('DELETE FROM alias_squadre WHERE squadra_id IN'
              ' (SELECT q.id FROM squadre_betfair q JOIN competizioni k'
              '  ON k.id = q.competizione_id'
              '  WHERE q.id=? AND q.competizione_id=? AND k.user_id=?)',
              (squadra_id, competizione_id, user_id))
    tolte = c.execute('DELETE FROM squadre_betfair WHERE id=? AND competizione_id IN'
                      ' (SELECT id FROM competizioni WHERE id=? AND user_id=?)',
                      (squadra_id, competizione_id, user_id)).rowcount
    return True if tolte else None


def _elimina_competizione(c, user_id, competizione_id):
    """Cascata della competizione (alias → squadre → riga), tutta vincolata."""
    c.execute('DELETE FROM alias_squadre WHERE squadra_id IN'
              ' (SELECT q.id FROM squadre_betfair q WHERE q.competizione_id IN'
              '  (SELECT id FROM competizioni WHERE id=? AND user_id=?))',
              (competizione_id, user_id))
    c.execute('DELETE FROM squadre_betfair WHERE competizione_id IN'
              ' (SELECT id FROM competizioni WHERE id=? AND user_id=?)',
              (competizione_id, user_id))
    tolte = c.execute('DELETE FROM competizioni WHERE id=? AND user_id=?',
                      (competizione_id, user_id)).rowcount
    return True if tolte else None


def _elimina_sorgente(c, user_id, sorgente_id):
    """Cascata della sorgente (i SUOI alias → la riga), vincolata."""
    c.execute('DELETE FROM alias_squadre WHERE sorgente_id IN'
              ' (SELECT id FROM sorgenti_squadre WHERE id=? AND user_id=?)',
              (sorgente_id, user_id))
    tolte = c.execute('DELETE FROM sorgenti_squadre WHERE id=? AND user_id=?',
                      (sorgente_id, user_id)).rowcount
    return True if tolte else None


def _rinomina_sorgente(c, user_id, sorgente_id, nome):
    """La rinomina, vincolata. Il doppione solleva `IntegrityError` come prima."""
    toccate = c.execute('UPDATE sorgenti_squadre SET nome=? WHERE id=? AND user_id=?',
                        (nome, sorgente_id, user_id)).rowcount
    return True if toccate else None


def _cancella_alias(c, user_id, sorgente_id, squadra_id):
    """La «⌫ alias», vincolata nel write-lock (secondo gate di Sol, PR #64).

    La DELETE diretta filtrava solo per (sorgente, squadra): una richiesta
    invecchiata da un travaso concorrente avrebbe cancellato l'alias del nuovo
    proprietario. Col vincolo, per il proprietario sbagliato e' un no-op — e
    il no-op e' il contratto anche per l'alias gia' assente: svuotare il vuoto
    non e' un errore.
    """
    return c.execute('DELETE FROM alias_squadre WHERE squadra_id=?'
                     ' AND sorgente_id IN'
                     ' (SELECT id FROM sorgenti_squadre WHERE id=? AND user_id=?)',
                     (squadra_id, sorgente_id, user_id)).rowcount


def _elimina_sport(c, user_id, sport_id):
    """La cascata INTERA dello sport, ogni statement vincolato al proprietario.

    Mercati e selezioni (#33) compresi: filtravano solo per `sport_id`, e al
    secondo gate della PR #64 Sol ha mostrato la conseguenza — una richiesta
    invecchiata da un travaso concorrente avrebbe distrutto i mercati ormai
    del nuovo proprietario, mentre il resto della funzione era gia' vincolato.
    None = per QUESTO utente non c'e' nessuno sport da eliminare.
    """
    del_sport_mio = '(SELECT id FROM sports WHERE id=? AND user_id=?)'
    c.execute('DELETE FROM betfair_selections WHERE market_id IN'
              ' (SELECT id FROM betfair_markets WHERE sport_id IN ' + del_sport_mio + ')',
              (sport_id, user_id))
    c.execute('DELETE FROM betfair_markets WHERE sport_id IN ' + del_sport_mio,
              (sport_id, user_id))
    c.execute('DELETE FROM alias_squadre WHERE squadra_id IN'
              ' (SELECT q.id FROM squadre_betfair q JOIN competizioni k'
              '  ON q.competizione_id = k.id WHERE k.sport_id=? AND k.user_id=?)',
              (sport_id, user_id))
    c.execute('DELETE FROM squadre_betfair WHERE competizione_id IN'
              ' (SELECT id FROM competizioni WHERE sport_id=? AND user_id=?)',
              (sport_id, user_id))
    c.execute('DELETE FROM competizioni WHERE sport_id=? AND user_id=?',
              (sport_id, user_id))
    tolte = c.execute('DELETE FROM sports WHERE id=? AND user_id=?',
                      (sport_id, user_id)).rowcount
    return True if tolte else None


def _scrivi_alias(c, user_id, sorgente_id, squadra_id, competizione_id, alias):
    """Upsert dell'alias CONDIZIONATO ai suoi DUE padri, in UNO statement.

    Il TERZO sito della classe TOCTOU della PR #55, mancato al primo giro di
    questa PR e trovato da Claude Fable 5 ([REAL_FINDING], PR #64): fra la
    lettura delle squadre valide nella rotta e questa scrittura, una DELETE
    concorrente della squadra (o dello sport, con la sua cascata) puo'
    committare — l'upsert diretto scriveva un alias orfano, invisibile alle
    letture (che joinano `squadre_betfair`) e rimovibile solo eliminando la
    sorgente. Le DUE guardie girano dentro il write-lock dell'INSERT e vedono
    lo stato vero, e ognuna vincola piu' del semplice «l'id esiste»:

    - la squadra deve esistere **dentro questa competizione**
      (`competizione_id` nella subquery — la forma di CodeRabbit), e la
      competizione dev'essere ANCORA dell'utente (il JOIN su
      `competizioni.user_id`, dal terzo gate di Fable): il controllo della
      rotta e' una lettura, e un travaso concorrente puo' invecchiarla;
    - la sorgente deve esistere ancora (GPT-5.5: la sua DELETE concorrente
      lascerebbe una riga con `sorgente_id` pendente, mai letta e non piu'
      eliminabile) **ed essere dell'utente** (`user_id` nella subquery).

    Il vincolo di proprieta' e' difesa in profondita', non la chiusura di una
    falla viva: le tabelle usano AUTOINCREMENT e sqlite NON riusa mai quegli id
    dopo una DELETE (misurato, secondo giro di Fable sulla PR #64) — ma cosi'
    l'isolamento non dipende da quella proprieta' sottile dello schema, che un
    ALTER futuro potrebbe perdere in silenzio.

    La sovrascrittura passa dall'`ON CONFLICT` e conta come cambiamento anche a
    valore identico (misurato, e vincolato dal test): None significa SOLO che
    uno dei due padri non esiste piu' — o non e' dell'utente.
    """
    c.execute('INSERT INTO alias_squadre(sorgente_id, squadra_id, alias)'
              ' SELECT ?,?,? WHERE EXISTS (SELECT 1 FROM squadre_betfair q'
              '  JOIN competizioni k ON k.id = q.competizione_id'
              '  WHERE q.id=? AND q.competizione_id=? AND k.user_id=?)'
              ' AND EXISTS (SELECT 1 FROM sorgenti_squadre WHERE id=? AND user_id=?)'
              ' ON CONFLICT(sorgente_id, squadra_id) DO UPDATE SET alias=excluded.alias',
              (sorgente_id, squadra_id, alias, squadra_id, competizione_id, user_id,
               sorgente_id, user_id))
    if not c.execute('SELECT changes()').fetchone()[0]:
        return None
    return True


def _squadre_di(c, user_id, competizione_id):
    """Le squadre Betfair della competizione, col proprietario ripetuto NELLA
    stessa SELECT (issue #65, variante lettura — quarto gate della PR #64):
    il check di proprieta' della rotta e' una lettura che puo' invecchiare, e
    filtrare per solo `competizione_id` farebbe leggere alla richiesta in volo
    le squadre ormai dell'account superstite dopo un travaso concorrente.
    """
    return c.execute('SELECT q.id, q.nome FROM squadre_betfair q'
                     ' JOIN competizioni k ON k.id = q.competizione_id'
                     ' WHERE q.competizione_id=? AND k.user_id=?'
                     ' ORDER BY q.nome', (competizione_id, user_id)).fetchall()


def _compilati_di(c, user_id, competizione_id, sorgente_id):
    """Quante squadre della competizione hanno gia' un alias in quella sorgente.

    E' il badge `compilati` della schermata competizione, e ripete il vincolo
    di proprieta' come `_squadre_di`: contava per sola `competizione_id`,
    quindi dopo un travaso concorrente il numero continuava a contare squadre
    ormai dell'account superstite — mentre nella STESSA risposta l'elenco
    squadre era gia' vuoto. Un badge che dice «3» sopra una lista vuota.
    [REAL_FINDING] di GPT-5.6 Sol al gate finale della PR #72.
    """
    return c.execute(
        'SELECT COUNT(*) FROM alias_squadre a'
        ' JOIN squadre_betfair q ON q.id = a.squadra_id'
        ' JOIN competizioni k ON k.id = q.competizione_id'
        " WHERE a.sorgente_id=? AND a.alias != '' AND q.competizione_id=?"
        ' AND k.user_id=?',
        (sorgente_id, competizione_id, user_id)).fetchone()[0]


def _alias_di(c, user_id, competizione_id, sorgente_id):
    """La tabella squadra→alias di una sorgente, vincolata come `_squadre_di`
    su ENTRAMBI i padri: competizione e sorgente."""
    return c.execute(
        'SELECT q.id, q.nome, IFNULL(a.alias, \'\')'
        ' FROM squadre_betfair q'
        ' JOIN competizioni k ON k.id = q.competizione_id'
        ' LEFT JOIN alias_squadre a ON a.squadra_id = q.id AND a.sorgente_id IN'
        '  (SELECT id FROM sorgenti_squadre WHERE id=? AND user_id=?)'
        ' WHERE q.competizione_id=? AND k.user_id=? ORDER BY q.nome',
        (sorgente_id, user_id, competizione_id, user_id)).fetchall()


@app.get('/api/me/sorgenti-squadre')
def sorgenti_mie(request: Request):
    """Le sorgenti squadre dell'utente. Al primo login: VUOTO, per progetto."""
    utente = _sessione_valida(request)
    c = db()
    try:
        righe = c.execute('SELECT id, nome FROM sorgenti_squadre WHERE user_id=?'
                          ' ORDER BY nome', (utente['id'],)).fetchall()
    finally:
        c.close()
    return _rispondi_con_sessione(utente['id'], utente['versione'], {'sorgenti': [
        {'id': r[0], 'nome': r[1]} for r in righe]})


@app.post('/api/me/sorgenti-squadre')
async def crea_sorgente_mia(request: Request):
    """Crea una sorgente. Il nome e' un'etichetta della UI: emoji libere."""
    utente = _sessione_valida(request)
    corpo = await _oggetto_dal_corpo(request, '{"nome": "..."}')
    nome = _campo_mercato('nome', corpo.get('nome', ''), vieta_emoji=False)
    c = db()
    try:
        try:
            c.execute('INSERT INTO sorgenti_squadre(user_id, nome) VALUES (?,?)',
                      (utente['id'], nome))
        except sqlite3.IntegrityError:
            raise HTTPException(409, 'hai gia\' una sorgente con questo nome') from None
        # Quota DOPO l'INSERT, dentro il suo write-lock: misurata prima, due
        # POST concorrenti sull'ultimo posto la bucherebbero entrambi (stessa
        # corsa della quota parser, PR #30). Il perdente riceve 409 e la close
        # senza commit toglie la sua riga.
        quante = c.execute('SELECT COUNT(*) FROM sorgenti_squadre WHERE user_id=?',
                           (utente['id'],)).fetchone()[0]
        if quante > MAX_SORGENTI_PER_UTENTE:
            raise HTTPException(
                409, f'quota sorgenti esaurita: massimo {MAX_SORGENTI_PER_UTENTE} per utente')
        nuovo = c.execute('SELECT last_insert_rowid()').fetchone()[0]
        c.commit()
    finally:
        c.close()
    return _rispondi_con_sessione(utente['id'], utente['versione'],
                                  {'id': nuovo, 'nome': nome})


@app.patch('/api/me/sorgenti-squadre/{sid}')
async def rinomina_sorgente_mia(sid: str, request: Request):
    """Rinomina una sorgente (decisione della issue: rinominabile, non solo eliminabile)."""
    utente = _sessione_valida(request)
    corpo = await _oggetto_dal_corpo(request, '{"nome": "..."}')
    nome = _campo_mercato('nome', corpo.get('nome', ''), vieta_emoji=False)
    c = db()
    try:
        sorgente = _sorgente_o_404(c, utente['id'], sid)
        try:
            if _rinomina_sorgente(c, utente['id'], sorgente[0], nome) is None:
                raise HTTPException(404, 'sorgente non trovata')
        except sqlite3.IntegrityError:
            raise HTTPException(409, 'hai gia\' una sorgente con questo nome') from None
        c.commit()
    finally:
        c.close()
    return _rispondi_con_sessione(utente['id'], utente['versione'],
                                  {'id': sorgente[0], 'nome': nome})


@app.delete('/api/me/sorgenti-squadre/{sid}')
def elimina_sorgente_mia(sid: str, request: Request):
    """Elimina la sorgente e i SUOI alias. Le squadre Betfair restano: sono
    della competizione, condivise da tutte le sorgenti (deciso, 13/08)."""
    utente = _sessione_valida(request)
    c = db()
    try:
        sorgente = _sorgente_o_404(c, utente['id'], sid)
        if _elimina_sorgente(c, utente['id'], sorgente[0]) is None:
            raise HTTPException(404, 'sorgente non trovata')
        c.commit()
    finally:
        c.close()
    return _rispondi_con_sessione(utente['id'], utente['versione'], {'ok': True})


@app.get('/api/me/competizioni')
def competizioni_mie(request: Request):
    utente = _sessione_valida(request)
    c = db()
    try:
        righe = c.execute(
            'SELECT k.id, s.slug, s.nome, k.nome,'
            ' (SELECT COUNT(*) FROM squadre_betfair q WHERE q.competizione_id = k.id)'
            ' FROM competizioni k JOIN sports s ON s.id = k.sport_id'
            ' WHERE k.user_id=? ORDER BY s.nome, k.nome', (utente['id'],)).fetchall()
    finally:
        c.close()
    return _rispondi_con_sessione(utente['id'], utente['versione'], {'competizioni': [
        {'id': r[0], 'sport': r[1], 'sportNome': r[2], 'nome': r[3], 'squadre': r[4]}
        for r in righe]})


@app.post('/api/me/competizioni')
async def crea_competizione_mia(request: Request):
    """Crea una competizione sotto uno sport DELL'UTENTE (riferito per slug, #33)."""
    utente = _sessione_valida(request)
    corpo = await _oggetto_dal_corpo(request, '{"sport": "slug", "nome": "..."}')
    nome = _campo_mercato('nome', corpo.get('nome', ''), vieta_emoji=False)
    c = db()
    try:
        sport = _sport_o_404(c, utente['id'], str(corpo.get('sport', '')))
        try:
            nuovo = _inserisci_competizione(c, utente['id'], sport[0], nome)
        except sqlite3.IntegrityError:
            raise HTTPException(
                409, 'hai gia\' una competizione con questo nome in questo sport') from None
        if nuovo is None:
            # Lo sport e' morto fra il controllo e l'INSERT.
            raise HTTPException(404, 'sport non trovato')
        quante = c.execute('SELECT COUNT(*) FROM competizioni WHERE user_id=?',
                           (utente['id'],)).fetchone()[0]
        if quante > MAX_COMPETIZIONI_PER_UTENTE:
            raise HTTPException(
                409, f'quota competizioni esaurita: massimo {MAX_COMPETIZIONI_PER_UTENTE} per utente')
        c.commit()
    finally:
        c.close()
    return _rispondi_con_sessione(utente['id'], utente['versione'],
                                  {'id': nuovo, 'sport': sport[1], 'nome': nome})


@app.get('/api/me/competizioni/{cid}')
def competizione_mia(cid: str, request: Request):
    """Il dettaglio che il pezzo 2 disegna in una schermata: le squadre Betfair
    della competizione e i pulsanti delle sorgenti col badge `compilati`
    (quante squadre hanno gia' l'alias in quella sorgente)."""
    utente = _sessione_valida(request)
    c = db()
    try:
        competizione = _competizione_o_404(c, utente['id'], cid)
        squadre = _squadre_di(c, utente['id'], competizione[0])
        # Il badge `compilati` passa da `_compilati_di`, che vincola il conteggio
        # alla competizione ANCORA dell'utente (#65): senza, dopo un travaso
        # concorrente il numero contava squadre ormai altrui sopra un elenco
        # squadre gia' vuoto.
        sorgenti = [(r[0], r[1], _compilati_di(c, utente['id'], competizione[0], r[0]))
                    for r in c.execute(
                        'SELECT g.id, g.nome FROM sorgenti_squadre g'
                        ' WHERE g.user_id=? ORDER BY g.nome',
                        (utente['id'],)).fetchall()]
    finally:
        c.close()
    return _rispondi_con_sessione(utente['id'], utente['versione'], {
        'id': competizione[0], 'nome': competizione[1], 'sport': competizione[2],
        'squadre': [{'id': r[0], 'nome': r[1]} for r in squadre],
        'sorgenti': [{'id': r[0], 'nome': r[1], 'compilati': r[2]} for r in sorgenti]})


@app.delete('/api/me/competizioni/{cid}')
def elimina_competizione_mia(cid: str, request: Request):
    """Elimina la competizione, le sue squadre e gli alias relativi in TUTTE le
    sorgenti. Cascata esplicita in una transazione, come per gli sport."""
    utente = _sessione_valida(request)
    c = db()
    try:
        competizione = _competizione_o_404(c, utente['id'], cid)
        if _elimina_competizione(c, utente['id'], competizione[0]) is None:
            raise HTTPException(404, 'competizione non trovata')
        c.commit()
    finally:
        c.close()
    return _rispondi_con_sessione(utente['id'], utente['versione'], {'ok': True})


@app.post('/api/me/competizioni/{cid}/squadre')
async def crea_squadra_mia(cid: str, request: Request):
    """Aggiunge un nome Betfair alla competizione. E' l'unica colonna che
    finira' nel CSV (EventName): emoji VIETATA, come per i mercati (#42)."""
    utente = _sessione_valida(request)
    corpo = await _oggetto_dal_corpo(request, '{"nome": "..."}')
    nome = _campo_mercato('nome', corpo.get('nome', ''))
    c = db()
    try:
        competizione = _competizione_o_404(c, utente['id'], cid)
        try:
            nuovo = _inserisci_squadra(c, utente['id'], competizione[0], nome)
        except sqlite3.IntegrityError:
            raise HTTPException(
                409, 'squadra gia\' presente in questa competizione') from None
        if nuovo is None:
            raise HTTPException(404, 'competizione non trovata')
        quante = c.execute('SELECT COUNT(*) FROM squadre_betfair WHERE competizione_id=?',
                           (competizione[0],)).fetchone()[0]
        if quante > MAX_SQUADRE_PER_COMPETIZIONE:
            raise HTTPException(
                409, f'quota squadre esaurita: massimo {MAX_SQUADRE_PER_COMPETIZIONE} per competizione')
        c.commit()
    finally:
        c.close()
    return _rispondi_con_sessione(utente['id'], utente['versione'],
                                  {'id': nuovo, 'nome': nome})


@app.delete('/api/me/competizioni/{cid}/squadre/{sid}')
def elimina_squadra_mia(cid: str, sid: str, request: Request):
    """«× squadra» (deciso 13/08): via dalla competizione e, a cascata, dai
    suoi alias in TUTTE le sorgenti. E' l'azione condivisa — la conferma la
    chiede la UI (pezzo 2), il server esegue."""
    utente = _sessione_valida(request)
    c = db()
    try:
        competizione = _competizione_o_404(c, utente['id'], cid)
        sid = _intero_o_404(sid, 'squadra')
        if _elimina_squadra(c, utente['id'], competizione[0], sid) is None:
            raise HTTPException(404, 'squadra non trovata')
        c.commit()
    finally:
        c.close()
    return _rispondi_con_sessione(utente['id'], utente['versione'], {'ok': True})


@app.get('/api/me/competizioni/{cid}/alias/{sid}')
def alias_miei(cid: str, sid: str, request: Request):
    """La tabella a due colonne del pezzo 2: ogni squadra Betfair della
    competizione con l'alias che QUESTA sorgente le da' (vuoto se non c'e')."""
    utente = _sessione_valida(request)
    c = db()
    try:
        competizione = _competizione_o_404(c, utente['id'], cid)
        sorgente = _sorgente_o_404(c, utente['id'], sid)
        righe = _alias_di(c, utente['id'], competizione[0], sorgente[0])
    finally:
        c.close()
    return _rispondi_con_sessione(utente['id'], utente['versione'], {'alias': [
        {'squadra_id': r[0], 'squadra': r[1], 'alias': r[2]} for r in righe]})


@app.put('/api/me/competizioni/{cid}/alias/{sid}')
async def scrivi_alias_miei(cid: str, sid: str, request: Request):
    """Scrive gli alias di una sorgente per una competizione, a coppie
    `{squadra_id: alias}`. Tocca SOLO le coppie presenti nel corpo; alias
    vuoto = «⌫ alias», svuota solo qui (la squadra resta, e resta altrove).
    UN alias per squadra per sorgente: la scrittura successiva sostituisce.

    La proprieta' si verifica su ENTRAMBI i lati — competizione E sorgente —
    perche' `alias_squadre` non ha `user_id`: senza il secondo controllo un PUT
    scriverebbe dentro la sorgente di un altro utente.
    """
    utente = _sessione_valida(request)
    corpo = await _oggetto_dal_corpo(request, '{"alias": {"<squadra_id>": "..."}}')
    coppie = corpo.get('alias')
    if not isinstance(coppie, dict):
        raise HTTPException(422, 'alias deve essere un oggetto {"<squadra_id>": "..."}')
    c = db()
    try:
        competizione = _competizione_o_404(c, utente['id'], cid)
        sorgente = _sorgente_o_404(c, utente['id'], sid)
        valide = {r[0] for r in c.execute(
            'SELECT id FROM squadre_betfair WHERE competizione_id=?',
            (competizione[0],)).fetchall()}
        # PRIMA tutta la validazione, POI le scritture: il 422 non deve
        # lasciare coppie gia' scritte (stessa forma della demo).
        pulite = []
        for chiave, valore in coppie.items():
            try:
                squadra = int(chiave)
            except (TypeError, ValueError):
                raise HTTPException(422, f'squadra_id non numerico: {chiave!r}') from None
            if squadra not in valide:
                raise HTTPException(422, f'squadra {squadra} non in questa competizione')
            if not isinstance(valore, str):
                raise HTTPException(422, 'ogni alias deve essere una stringa')
            valore = valore.strip()
            if len(valore) > MAX_CAMPO_MERCATO:
                raise HTTPException(
                    422, f'alias troppo lungo: massimo {MAX_CAMPO_MERCATO} caratteri')
            pulite.append((squadra, valore))
        # Alias ambiguo vietato (#34 pezzo 3, deciso dal proprietario il
        # 17/08/2026): a parse-time la ricerca corre su TUTTA la sorgente,
        # quindi lo stesso testo su due squadre non deve poter essere salvato.
        # Il controllo e' sullo STATO FINALE — la mappa della sorgente con il
        # corpo sovrapposto — cosi' spostare un alias da una squadra all'altra
        # in un solo PUT resta lecito in qualunque ordine arrivino le coppie.
        finale = {r[0]: r[1] for r in c.execute(
            'SELECT squadra_id, alias FROM alias_squadre WHERE sorgente_id=?',
            (sorgente[0],)).fetchall()}
        for squadra, valore in pulite:
            if valore == '':
                finale.pop(squadra, None)
            else:
                finale[squadra] = valore
        occupanti = {}
        for squadra, valore in finale.items():
            # La chiave dell'ambiguita' e' quella con cui il PARSER cerca —
            # `_piatto`, la stessa di `_mappa_team_source` — non il testo
            # com'e' scritto: «Juve  FC» e «Juve FC» sarebbero due alias al
            # PUT e UNO a parse-time, e la mappa ne perderebbe uno.
            # [REAL_FINDING] di GPT-5.5 sulla PR #67.
            chiave = _piatto(valore)
            if not chiave:
                continue
            if chiave in occupanti and occupanti[chiave] != squadra:
                raise HTTPException(
                    422, f"alias «{valore}» gia' usato per un'altra squadra "
                         'in questa sorgente')
            occupanti[chiave] = squadra
        # E l'alias non puo' OMBREGGIARE il nome Betfair di un'ALTRA squadra
        # dell'utente (qualunque competizione: l'identita' e' sua, non della
        # competizione): l'alias vince sull'identita' nella mappa, quindi quel
        # testo tradurrebbe un nome canonico nella squadra sbagliata — dentro
        # EventName, cioe' dentro il CSV. Stessa classe dell'ambiguita' qui
        # sopra, stessa cura: non deve poter nascere. Il nome della squadra
        # STESSA resta lecito: e' un'identita' innocua.
        # [REAL_FINDING] di GPT-5.6 Sol al gate finale della PR #67.
        nomi_betfair = {}
        for id_q, nome_q in c.execute(
                'SELECT q.id, q.nome FROM squadre_betfair q'
                ' JOIN competizioni k ON k.id = q.competizione_id'
                ' WHERE k.user_id=? ORDER BY q.id',
                (utente['id'],)).fetchall():
            chiave_q = _piatto(nome_q)
            if chiave_q:
                nomi_betfair.setdefault(chiave_q, id_q)
        for chiave, squadra in occupanti.items():
            if chiave in nomi_betfair and nomi_betfair[chiave] != squadra:
                raise HTTPException(
                    422, f"alias «{chiave}» e' il nome Betfair di un'altra "
                         'squadra: tradurrebbe quel nome nella squadra sbagliata')
        scritte = 0
        for squadra, valore in pulite:
            if valore == '':
                _cancella_alias(c, utente['id'], sorgente[0], squadra)
            else:
                if _scrivi_alias(c, utente['id'], sorgente[0], squadra,
                                 competizione[0], valore) is None:
                    # Un padre e' morto fra la lettura e la scrittura: 404
                    # come se la DELETE fosse arrivata prima.
                    raise HTTPException(404, 'squadra non trovata')
            scritte += 1
        c.commit()
    finally:
        c.close()
    return _rispondi_con_sessione(utente['id'], utente['versione'], {'scritte': scritte})


def _valida_team_source(c, user_id, config):
    """Il riferimento «Sorgente squadre» del parser (#34 pezzo 3), al confine di
    scrittura, come `_valida_betfair`: deve essere l'id di una sorgente che
    esiste ED e' dell'utente — quella di un altro e' indistinguibile da una
    inesistente. `None`/assente = nessuna sorgente, passthrough verbatim.
    """
    sorgente = (config or {}).get('team_source') if isinstance(config, dict) else None
    if sorgente is None:
        return
    if isinstance(sorgente, bool) or not isinstance(sorgente, int):
        raise HTTPException(
            422, 'team_source deve essere l\'id di una sorgente squadre')
    if not c.execute('SELECT 1 FROM sorgenti_squadre WHERE id=? AND user_id=?',
                     (sorgente, user_id)).fetchone():
        raise HTTPException(422, 'sorgente squadre inesistente')


def _mappa_team_source(c, user_id, sorgente_id):
    """La mappa alias→Betfair di una sorgente, pronta per i motori.

    Porta anche le IDENTITA' Betfair→Betfair di TUTTE le squadre dell'utente
    (deciso il 17/08/2026): chi scrive gia' il nome Betfair non deve mappare
    niente e non riceve avvisi — l'avviso scatta solo sui nomi davvero
    estranei. Gli alias espliciti vincono sulle identita'. Le chiavi passano
    da `_piatto`, la stessa normalizzazione con cui i motori cercano.

    None se la sorgente non esiste (piu') o non e' dell'utente: il parser che
    la riferisce torna al passthrough puro, come «nessuna sorgente» — una
    mezza traduzione fatta di sole identita' sembrerebbe funzionare e non
    avvertirebbe che la sorgente e' sparita.
    """
    if isinstance(sorgente_id, bool) or not isinstance(sorgente_id, int):
        return None
    if not c.execute('SELECT 1 FROM sorgenti_squadre WHERE id=? AND user_id=?',
                     (sorgente_id, user_id)).fetchone():
        return None
    mappa = {}
    # ORDER BY: senza, una collisione di chiavi normalizzate si risolverebbe
    # nell'ordine in cui SQLite decide di restituire le righe — cioe' in modo
    # diverso fra ambienti. Il PUT vieta i doppioni normalizzati DENTRO una
    # sorgente, ma le identita' (nomi squadra) possono ancora collidere fra
    # competizioni: vince deterministicamente l'id piu' alto (l'ultima
    # scritta). GPT-5.5 sulla PR #67.
    for (nome,) in c.execute(
            'SELECT q.nome FROM squadre_betfair q'
            ' JOIN competizioni k ON k.id = q.competizione_id'
            ' WHERE k.user_id=? ORDER BY q.id', (user_id,)).fetchall():
        chiave = _piatto(nome)
        if chiave:
            mappa[chiave] = nome
    for alias, nome in c.execute(
            'SELECT a.alias, q.nome FROM alias_squadre a'
            ' JOIN squadre_betfair q ON q.id = a.squadra_id'
            ' JOIN competizioni k ON k.id = q.competizione_id'
            ' WHERE a.sorgente_id=? AND k.user_id=? ORDER BY a.id',
            (sorgente_id, user_id)).fetchall():
        chiave = _piatto(alias)
        if chiave:
            mappa[chiave] = nome
    return mappa


def _valida_betfair(c, user_id, config):
    """La modalita' «Da mercati Betfair» (#33): riferimento E byte devono combaciare.

    Il wizard scrive tre regole costanti e il riferimento `betfair`
    `{market_id, selection_id}`. Qui si verifica, al confine di scrittura, che:
    la selezione esista fra quelle create DALL'UTENTE per quel mercato (una
    selezione arbitraria via HTTP → 422, e' il test che la #33 chiede per nome);
    i valori costanti coincidano con la libreria (il riferimento da solo non
    basta: nel CSV finiscono i byte delle costanti); nessun campo usi i
    segnaposto `{HOME_TEAM}`/`{AWAY_TEAM}` — la loro risoluzione e' la sorgente
    squadre (#34), e finche' non esiste il token uscirebbe LETTERALE nel feed,
    che XTrader scarterebbe in silenzio. Fail-closed, col motivo.

    Un parser senza `betfair` non passa di qui: le costanti scritte a mano
    restano libere come sono sempre state. E un mercato eliminato DOPO il
    salvataggio non rompe il parser: la validazione avviene solo alla scrittura,
    le costanti salvate restano valide — la libreria e' provenienza, non
    dipendenza viva.
    """
    riferimento = config.get('betfair')
    if riferimento is None:
        return
    if not isinstance(riferimento, dict):
        raise HTTPException(422, 'betfair deve essere un oggetto {market_id, selection_id}')
    try:
        market_id = int(riferimento.get('market_id'))
        selection_id = int(riferimento.get('selection_id'))
    except (TypeError, ValueError):
        raise HTTPException(
            422, 'betfair.market_id e betfair.selection_id devono essere numeri') from None
    riga = c.execute(
        'SELECT m.market_type, m.market_name, sel.selection_name'
        ' FROM betfair_selections sel'
        ' JOIN betfair_markets m ON m.id = sel.market_id'
        ' JOIN sports s ON s.id = m.sport_id'
        ' WHERE sel.id=? AND m.id=? AND s.user_id=?',
        (selection_id, market_id, user_id)).fetchone()
    if not riga:
        raise HTTPException(
            422, 'selezione non trovata fra i tuoi mercati Betfair: scegli dal tuo elenco')
    tipo, nome, selezione = riga
    if any(token in campo for token in ('{HOME_TEAM}', '{AWAY_TEAM}')
           for campo in (tipo, nome, selezione)):
        raise HTTPException(
            422, 'questa selezione usa i segnaposto squadra {HOME_TEAM}/{AWAY_TEAM}:'
                 ' serve la sorgente squadre (#34), non ancora disponibile')
    colonne = config.get('columns') or {}
    for colonna, atteso in (('MarketType', tipo), ('MarketName', nome),
                            ('SelectionName', selezione)):
        regola = colonne.get(colonna) or {}
        if regola.get('source') != 'constant' or regola.get('value') != atteso:
            raise HTTPException(
                422, f'{colonna} non coincide con il mercato scelto: rifai la scelta dal wizard')


def _cattura_canale_backup(payload):
    """Gestisce i `my_chat_member` che riguardano il canale di backup (#56 pezzi 2 e 3b).

    Due eventi, un solo handler perche' arrivano sullo stesso tipo di update e vanno
    serializzati fra loro e con la conferma:

    - **promozione** — il bot promosso amministratore/creatore di un canale PRIVATO, e
      **solo se e' l'amministratore ad averlo promosso** (`from.id == TELEGRAM_ADMIN_ID`):
      un canale altrui non deve poter comparire come proposta nel pannello del proprietario.
      Scrive **solo un candidato** — nessun backup parte da qui, la conferma nel pannello
      manda un messaggio di prova prima di salvarlo. SOLO canali PRIVATI: un canale pubblico
      ha uno `username` (e' cosi' che lo si raggiunge) e il backup, che contiene i dati dei
      clienti, non deve poter finire dove chiunque lo legge (Sol, #56);
    - **rimozione** (#56 pezzo 3b, Fable) — il bot esce (`left`/`kicked`) dal canale
      CONFIGURATO o dal CANDIDATO: la config va azzerata, o il pannello continuerebbe a
      mostrare una destinazione dove il bot non puo' piu' postare e ogni backup fallirebbe
      in silenzio. Un `my_chat_member` e' sempre sulla membership del bot, quindi lo stato
      basta a saperlo; e agiamo SOLO se il canale e' il nostro (match su `chat_id`), cosi'
      un estraneo che spinge il bot fuori da un suo canale non spegne il backup altrui.

    In nessun caso tocca `signals`, cerca parser o scrive in `chats`: il canale di backup e'
    una DESTINAZIONE, non una sorgente di segnali, e finire in `chats` lo iscriverebbe
    all'instradamento del webhook — la regola non negoziabile del filtro delle chat.

    **Dedup per `update_id`** (Sol B1, #56 pezzo 3b): come il percorso dei segnali, l'update
    gia' visto esce come `duplicate` e non riscrive niente. Chiude la riconsegna che
    farebbe risorgere un candidato di un canale gia' abbandonato. Il marker viaggia nella
    STESSA transazione dell'effetto (crash-safe), come in `_elabora_consegna`.

    Restituisce un dict da consegnare a Telegram se ha gestito l'update, altrimenti `None`
    e il webhook prosegue col suo percorso normale.

    SINCRONA e da chiamare FUORI dall'event loop (`asyncio.to_thread`): prende il lock di
    scrittura con `BEGIN IMMEDIATE`, e sull'event loop l'attesa del lock sotto contesa
    fermerebbe tutte le consegne (vedi il chiamante in `telegram_webhook`).
    """
    if not TELEGRAM_ADMIN_ID:
        return None
    aggiornamento = payload.get('my_chat_member') or {}
    attore = str((aggiornamento.get('from') or {}).get('id') or '')
    stato = (aggiornamento.get('new_chat_member') or {}).get('status') or ''
    ch = aggiornamento.get('chat') or {}
    chat_id = str(ch.get('id') or '')
    if not chat_id:
        return None
    update_id = str(payload.get('update_id') or '')
    promozione = (attore == TELEGRAM_ADMIN_ID and (ch.get('type') or '') == 'channel'
                  and stato in ('administrator', 'creator') and not ch.get('username'))
    # Il bot non puo' piu' pubblicare nel canale: uscito (`left`/`kicked`) o RETROCESSO da
    # amministratore a semplice `member`/`restricted` (in un canale solo gli admin postano).
    # Tutti e quattro vanno trattati come rimozione, o un canale configurato resterebbe
    # tale mentre i backup falliscono in silenzio. Bloccante di GPT-5.6 Sol al gate finale (#56).
    rimozione = stato in ('left', 'kicked', 'member', 'restricted')
    if not (promozione or rimozione):
        return None
    titolo = (ch.get('title') or '').strip()
    c = db()
    try:
        # Controllo, scrittura e marker del dedup ATOMICI, sotto `BEGIN IMMEDIATE`. Senza, la
        # coppia «leggi il configurato / scrivi» ha una finestra: questo handler gira PRIMA del
        # dedup del percorso segnali, mentre la conferma gira in un thread del pool — e sqlite
        # rilascia il GIL durante l'I/O. Una riconsegna poteva leggere «non configurato», la
        # conferma configurare nel frattempo, e la riconsegna riscrivere il candidato gia'
        # consumato. BEGIN IMMEDIATE prende il lock di scrittura e serializza con la conferma
        # (anch'essa BEGIN IMMEDIATE): o l'una o l'altra, mai intrecciate. Bloccante di GPT-5.6
        # Sol; la guardia sequenziale l'aveva vista Fable.
        c.execute('BEGIN IMMEDIATE')
        if update_id and c.execute('SELECT 1 FROM webhook_seen WHERE update_id=?',
                                   (update_id,)).fetchone():
            c.rollback()
            return {'ok': True, 'ignored': 'duplicate'}
        # Ordinamento (#56, pezzo idempotenza — Sol B1): gli `update_id` di Telegram crescono
        # monotoni, ma l'offload su thread puo' elaborarli fuori ordine. Un evento con id <= a
        # quello piu' alto gia' processato PER QUESTO CANALE e' una promozione tardiva dopo una
        # rimozione piu' nuova dello stesso canale: applicarla farebbe risorgere un candidato
        # ormai invalido. Si ignora.
        #
        # L'high-water-mark e' PER `chat_id`, non globale: altrimenti la promozione di un ALTRO
        # canale (un secondo candidato) alzerebbe il contatore e sopprimerebbe come `out_of_order`
        # una rimozione LEGITTIMA — con id inferiore ma di un canale diverso — del canale
        # configurato, lasciandolo puntato a un canale da cui il bot e' uscito. Bloccante di
        # Claude Fable 5 al gate finale (#56). Si aggiorna solo quando agiamo sul canale (in
        # `_segna_update_visto`), quindi eventi di canali estranei non lasciano traccia.
        ultimo = leggi_impostazione(c, CHIAVE_CANALE_ULTIMO_UPDATE + ':' + chat_id)
        if update_id.isdigit() and (ultimo or '').isdigit() and int(update_id) <= int(ultimo):
            c.rollback()
            return {'ok': True, 'ignored': 'out_of_order'}
        if rimozione:
            # Pulizia SOLO del canale nostro: se il `left`/`kicked` non riguarda ne' il
            # configurato ne' il candidato, non e' affar nostro — rollback e passthrough.
            tocca_conf = leggi_impostazione(c, CHIAVE_CANALE_BACKUP_ID) == chat_id
            tocca_cand = leggi_impostazione(c, CHIAVE_CANALE_CANDIDATO_ID) == chat_id
            if not (tocca_conf or tocca_cand):
                c.rollback()
                return None
            if tocca_conf:
                cancella_impostazione(c, CHIAVE_CANALE_BACKUP_ID)
                cancella_impostazione(c, CHIAVE_CANALE_BACKUP_TITOLO)
            if tocca_cand:
                cancella_impostazione(c, CHIAVE_CANALE_CANDIDATO_ID)
                cancella_impostazione(c, CHIAVE_CANALE_CANDIDATO_TITOLO)
            _segna_update_visto(c, update_id, chat_id)
            c.commit()
            return {'ok': True, 'canale_backup_rimosso': True}
        # promozione: se il canale e' GIA' quello configurato, una riconsegna non ripropone
        # una proposta gia' consumata.
        if leggi_impostazione(c, CHIAVE_CANALE_BACKUP_ID) == chat_id:
            _segna_update_visto(c, update_id, chat_id)
            c.commit()
            return {'ok': True, 'canale_backup_gia_configurato': True}
        scrivi_impostazione(c, CHIAVE_CANALE_CANDIDATO_ID, chat_id)
        scrivi_impostazione(c, CHIAVE_CANALE_CANDIDATO_TITOLO, titolo)
        _segna_update_visto(c, update_id, chat_id)
        c.commit()
    finally:
        c.close()
    return {'ok': True, 'canale_backup_candidato': True}


def _segna_update_visto(c, update_id, chat_id):
    """Marca un `update_id` come elaborato per `chat_id`, nella transazione in corso. No-op se
    l'`update_id` e' vuoto.

    Due registri, entrambi nel commit dell'effetto:
    - `webhook_seen` (INSERT OR IGNORE) — il dedup della riconsegna esatta, come nel percorso
      segnali; un `update_id` e' unico per update in tutto Telegram, quindi un `my_chat_member`
      e un messaggio non collidono mai;
    - l'high-water-mark `CHIAVE_CANALE_ULTIMO_UPDATE:<chat_id>` — l'ordinamento (#56, Sol B1),
      tenuto PER CANALE cosi' l'ordine di un canale non sopprime gli eventi di un altro (Fable).
      Si scrive SOLO qui, cioe' solo quando abbiamo agito su QUEL canale. Chi arriva col
      controllo di ordine ha gia' `update_id > ultimo`, quindi sovrascrivere col valore corrente
      lo tiene monotono."""
    if update_id:
        c.execute('INSERT OR IGNORE INTO webhook_seen(update_id) VALUES (?)', (update_id,))
        scrivi_impostazione(c, CHIAVE_CANALE_ULTIMO_UPDATE + ':' + chat_id, update_id)


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
        # in un ramo: le chat autorizzate esistono a database indipendentemente
        # dal bot, quindi un'istanza senza bot ma con le chat configurate era
        # iniettabile da chiunque. Segnalato da CodeRabbit.
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
    # Cattura del canale di backup (#56 pezzo 2): il bot promosso amministratore di un
    # canale dall'amministratore. Registra solo un CANDIDATO da confermare nel pannello —
    # non e' un percorso verso i segnali.
    #
    # Gira SOLO sugli aggiornamenti `my_chat_member` (gli altri non la riguardano, e cosi' la
    # stragrande maggioranza delle consegne — i messaggi dei canali — non paga nemmeno un salto
    # di thread) e FUORI dall'event loop, come `_processa_messaggio_canale` piu' sotto: il suo
    # `BEGIN IMMEDIATE` prende il lock di scrittura e, sotto contesa con la conferma (anch'essa
    # `BEGIN IMMEDIATE`), attenderebbe fino al busy_timeout. Sull'event loop quell'attesa fermerebbe
    # TUTTE le consegne webhook, non solo questa. Bloccante di Claude Fable 5 al gate finale (#56).
    if 'my_chat_member' in payload:
        catturato = await asyncio.to_thread(_cattura_canale_backup, payload)
        if catturato is not None:
            return catturato
    msg = payload.get('message') or payload.get('channel_post') or {}
    chat = msg.get('chat') or {}
    chat_id = str(chat.get('id', ''))
    text = msg.get('text') or msg.get('caption') or ''
    if not text:
        return {'ok': True, 'ignored': 'no_text'}
    # `/start` da una chat PRIVATA: l'unico modo di sapere che il bot puo' scrivere a qualcuno.
    #
    # `sendMessage` falisce verso chi non ha mai aperto una conversazione col bot (trappola 1
    # della Issue #2), e Telegram non offre nessun modo di CHIEDERE se puo': lo si scopre
    # provando, o lo si registra quando la persona scrive. Questo ramo registra.
    #
    # **Perche' non indebolisce il filtro delle chat**, che e' una regola non negoziabile di
    # `CLAUDE.md`: non e' un percorso di scrittura verso i segnali. Non tocca `signals`, non
    # cerca parser, non guarda `profiles`. Scrive **un booleano** su una riga di `users`
    # trovata per `telegram_id`, che e' l'identita' che Telegram stessa attesta nella
    # consegna — la stessa che il Login Widget firma. Un estraneo che forgiasse questa
    # consegna (e non puo': serve il segreto del webhook) otterrebbe di marcare raggiungibile
    # un utente che lo e' davvero.
    #
    # Il messaggio NON finisce in `message_logs`: quel log e' dei messaggi dei canali, cioe'
    # dei segnali. Una conversazione privata col bot non c'entra, e mettercela dentro
    # significherebbe conservare testo privato in un archivio che serve a un'altra cosa.
    if text.startswith('/start') and (chat.get('type') or '') == 'private':
        mittente = str((msg.get('from') or {}).get('id') or '')
        c = db()
        try:
            if mittente:
                c.execute('UPDATE users SET telegram_reachable=1 WHERE telegram_id=?',
                          (mittente,))
                c.commit()
        finally:
            c.close()
        return {'ok': True, 'start': True}
    # Il CODICE DI VERIFICA: l'unica eccezione al filtro delle chat, e tutta
    # l'eccezione (#32, pezzo 3.2).
    #
    # **Perche' non indebolisce il filtro**, che e' una regola non negoziabile di
    # `CLAUDE.md`: questo ramo non e' un percorso di scrittura verso i segnali.
    # Non tocca `signals`, non cerca parser, non guarda `profiles`, non scrive in
    # `message_logs`. Registra una riga in `chats` e consuma un codice che il
    # servizio stesso ha emesso pochi minuti prima a una sessione autenticata.
    # Chi forgiasse questa consegna (e non puo': serve il segreto del webhook)
    # dovrebbe comunque indovinare un codice vivo.
    #
    # Il riconoscimento e' sulla FORMA, prima del database: senza il prefisso,
    # ogni messaggio di ogni canale sconosciuto costerebbe una query.
    #
    # Il testo NON finisce in `message_logs`: quel log e' dei segnali, e un
    # codice conservato li' sarebbe rileggibile da chi apre la vista dei log.
    codice = _e_codice_di_verifica(text)
    if codice:
        return await asyncio.to_thread(_consuma_codice_di_verifica, chat_id, codice)
    # L'elaborazione sta FUORI dall'event loop (asyncio.to_thread): SQLite e il
    # motore di parsing sono sincroni, e sul percorso che riceve OGNI messaggio di
    # OGNI canale un parser lento non deve fermare le altre richieste del servizio
    # (#31 B1). La regex utente resta comunque capata da REGEX_BUDGET_PARSER_S.
    # Il dedup dell'`update_id` vive DENTRO l'elaborazione, non qui: il marker va
    # committato nella stessa transazione del segnale (vedi il docstring sotto).
    return await asyncio.to_thread(_processa_messaggio_canale, chat_id, text,
                                   payload.get('update_id'))


# Le consegne in volo IN QUESTO processo, per il dedup delle simultanee: il
# marker su `webhook_seen` viaggia nella transazione del segnale (crash-safe) ma
# non puo' fermare due consegne identiche ARRIVATE INSIEME — il marker dell'una
# non e' ancora committato quando l'altra controlla. Il servizio e' un processo
# solo (`Procfile` senza `--workers`, misurato): una prenotazione in memoria
# chiude la finestra senza indebolire la garanzia sul crash. Bloccante di
# GPT-5.5 e Fable sulla PR #44.
_CONSEGNE_IN_VOLO = set()
_LOCK_CONSEGNE = threading.Lock()

# L'ordine di arrivo e' l'ordine di scrittura. Con l'offload su to_thread due
# consegne diverse elaborano in thread paralleli, e un messaggio VECCHIO dal
# parse lento puo' finire dopo uno NUOVO, sovrascrivendone segnale e TTL — un
# feed che torna indietro nel tempo ([REAL_FINDING] di GPT-5.6 Sol, PR #44).
# Prima dell'offload il codice sincrono sull'event loop serializzava di fatto;
# questo lock rende la proprieta' DELIBERATA senza rimettere il carico sul
# loop: le altre rotte restano libere, le consegne del webhook si accodano.
# (Sul percorso dei link la DELETE di pulizia apriva gia' la transazione prima
# del parse e SQLite accodava il secondo scrittore: vera, ma accidentale.)
_LOCK_ELABORAZIONE = threading.Lock()


def _processa_messaggio_canale(chat_id, text, update_id=None):
    """Il dispatch: chat → i parser collegati, ognuno verso il feed del SUO utente.

    Modello della Issue #2: la chat si collega ai PARSER (`parser_chats`, travasata
    dai profili una volta sola e poi tenuta aggiornata dalle scritture dei profili,
    `_riconcilia_link_del_profilo`); ogni parser attivo elabora in
    modo indipendente e scrive nel feed del proprio utente. Fra i parser dello
    STESSO utente che riconoscono lo stesso messaggio vince l'ULTIMO nell'ordine
    dichiarato (`parsers.ordine`), e i battuti restano in `message_logs` come
    «riconosciuto, sostituito da» — e' cio' che la UI promette di mostrare.

    Chiude il pericolo 1 della #25: nessun profilo «vince» piu' la chat in ordine
    alfabetico. Due profili sulla stessa chat sono due link, due utenti, due feed.

    **Il dedup e la scrittura sono UNA transazione.** Il marker di `webhook_seen`
    si controlla in testa (una riconsegna esce subito come `duplicate`, senza
    riarmare il TTL) ma si SCRIVE in coda, nello stesso commit del segnale: o
    entrambi, o nessuno. La prima versione lo committava PRIMA di elaborare, e un
    guasto fra il marker e `store_signal` perdeva il segnale per sempre — il
    retry di Telegram usciva come duplicato con niente nel feed. Bloccante di
    Claude Fable 5 e rischio di GPT-5.5 sulla PR #44, convergenti. Il baratto
    residuo e' dichiarato: due consegne IDENTICHE e simultanee possono elaborare
    entrambe (stesso contenuto, stesso segnale — innocuo); un guasto a meta' non
    perde mai il segnale, che per un relay di puntate e' il verso giusto.

    Il percorso legacy per profili serve due casi: la chat senza nessun link, e
    il profilo creato a caldo il cui UTENTE non e' rappresentato nei link della
    chat (bloccante 2 di Fable: prima restava muto fino al riavvio se la chat
    aveva gia' i link di qualcun altro). I link arrivano alla prossima
    migrazione; fino ad allora quel profilo passa da qui.
    """
    chiave = str(update_id) if update_id is not None else None
    if chiave is not None:
        with _LOCK_CONSEGNE:
            if chiave in _CONSEGNE_IN_VOLO:
                return {'ok': True, 'ignored': 'duplicate'}
            _CONSEGNE_IN_VOLO.add(chiave)
    try:
        with _LOCK_ELABORAZIONE:
            return _elabora_consegna(chat_id, text, chiave)
    finally:
        if chiave is not None:
            with _LOCK_CONSEGNE:
                _CONSEGNE_IN_VOLO.discard(chiave)


def _elabora_consegna(chat_id, text, chiave):
    """Il corpo della consegna, con la prenotazione in-flight gia' presa."""
    c = db()
    try:
        if chiave is not None and c.execute(
                'SELECT 1 FROM webhook_seen WHERE update_id=?',
                (chiave,)).fetchone():
            return {'ok': True, 'ignored': 'duplicate'}
        riga_chat = c.execute(f'SELECT id FROM chats WHERE telegram_chat_id=?'
                              f' AND {TOPIC_CHAT}=?', (chat_id, '')).fetchone()
        link = []
        if riga_chat:
            # `p.user_id IS NOT NULL`: un parser senza proprietario non ha un
            # feed in cui scrivere — `store_signal` con entrambe le chiavi NULL
            # accumulerebbe righe orfane che nessuna DELETE toglie (rischio di
            # GPT-5.5, bloccante 3 di Fable). La semina non puo' crearlo (esige
            # il JOIN con users); una riga scritta a mano si', e resta esclusa.
            link = c.execute(
                'SELECT p.id, p.name, p.header, p.market_name, p.market_type,'
                ' p.selection_name, p.handicap, p.bet_type, p.config_json,'
                ' p.user_id, p.slug, u.origin_profile, u.status,'
                ' u.access_expires_at, u.is_admin'
                ' FROM parser_chats pc JOIN parsers p ON p.id = pc.parser_id'
                ' JOIN users u ON u.id = p.user_id'
                ' WHERE pc.chat_id=? AND IFNULL(p.active, 1)=1'
                ' AND p.user_id IS NOT NULL'
                ' ORDER BY p.user_id, p.ordine, p.name',
                (riga_chat[0],)).fetchall()
        esiti = {}
        if link:
            for utente_id, righe in _raggruppa(link, chiave=lambda r: r[9]):
                etichetta = righe[0][11] or righe[0][10] or f'utente-{utente_id}'
                esiti[etichetta] = _elabora_per_utente(c, riga_chat[0], utente_id,
                                                       righe, text)
        # TUTTI i profili legacy non rappresentati nei link, non il primo
        # alfabetico: con due profili scoperti sulla stessa chat il secondo
        # restava muto — bloccante di GPT-5.5 e Fable sul fix precedente, ed
        # era l'immagine speculare del difetto che il fix chiudeva.
        for profilo in _profili_della_chat(c, chat_id):
            if riga_chat and _utente_del_profilo_nei_link(c, profilo['name'],
                                                          riga_chat[0]):
                continue
            esiti[profilo['name']] = _elabora_profilo(c, profilo, text)
        if chiave is not None:
            c.execute('INSERT OR IGNORE INTO webhook_seen(update_id) VALUES (?)',
                      (chiave,))
            c.execute("DELETE FROM webhook_seen WHERE created_at < datetime('now', '-7 days')")
        # La pulizia dei log sta QUI e non nel ramo con link: la promessa dei 7
        # giorni vale anche per un servizio coi parser tutti disattivati o solo
        # su fallback, dove quel ramo non gira mai — [REAL_FINDING] di GPT-5.6
        # Sol al gate finale. Viaggia nel commit che la consegna fa comunque.
        c.execute("DELETE FROM message_logs WHERE created_at < datetime('now', '-7 days')")
        c.commit()
    finally:
        c.close()
    if not esiti:
        return {'ok': True, 'ignored': 'chat_not_allowed'}
    # La forma della risposta: identica a prima quando la chat serve UN utente —
    # e' il contratto che i test del webhook vincolano — aggregata quando sono
    # di piu'. Telegram la ignora; serve ai log e ai test.
    if len(esiti) == 1:
        etichetta, esito = next(iter(esiti.items()))
        if isinstance(esito, dict):
            return {'ok': True, 'profile': etichetta, 'event': esito['event']}
        return {'ok': True, 'ignored': esito}
    scritti = {k: v['event'] for k, v in esiti.items() if isinstance(v, dict)}
    if scritti:
        return {'ok': True, 'utenti': scritti}
    return {'ok': True, 'ignored': 'parser_no_match'}


def _elabora_per_utente(c, chat_riga_id, utente_id, righe, text):
    """I parser di UN utente su un messaggio: vince l'ultimo, i battuti nei log.

    Restituisce il valore dell'esito: `{'event': ...}` se il segnale e' scritto,
    altrimenti la stringa del motivo (`parser_no_match`, `csv_non_valido`,
    `access_<stato>`). Non committa: la transazione e' del chiamante, insieme al
    marker del dedup.
    """
    # Accesso scaduto o sospeso: non si elabora e non si logga — il log e' una
    # funzione del servizio, non un archivio (vedi PR #26).
    bloccato = _blocco_della_riga(righe[0][12], righe[0][13], righe[0][14])
    if bloccato:
        return f'access_{bloccato}'
    riconosciuti = []
    motivi = []
    for r in righe:
        cfg = dict(zip(['name', 'header', 'market_name', 'market_type',
                        'selection_name', 'handicap', 'bet_type',
                        'config_json'], (r[1], r[2], r[3], r[4], r[5],
                                         r[6], r[7], r[8]), strict=True))
        # Il risolutore della mappa (#34 pezzo 3): la connessione e l'utente
        # sono QUI, `esito_messaggio` sa solo se la config porta il riferimento.
        parsed, scarti = esito_messaggio(
            text, cfg, lambda sid: _mappa_team_source(c, utente_id, sid))
        if parsed:
            riconosciuti.append((r, parsed))
        motivi.extend((r, m) for m in scarti)
    if not riconosciuti:
        if not motivi:
            # Nessun parser ha riconosciuto il messaggio: e' il caso normale di
            # una chat dove passa anche altro traffico, e non si logga — i log
            # sono una funzione del servizio, non un archivio dei messaggi.
            return 'parser_no_match'
        # Riconosciuto e SCARTATO da una guardia: qui il log serve, ed e' l'unico
        # posto dove l'utente puo' vedere la causa. Senza, un parser che smette di
        # scrivere perche' una quota e' fuori scala o perche' le sue obbligatorie
        # sono tutte costanti si fermerebbe in silenzio: nel feed niente, nei log
        # niente, e la causa visibile solo rilanciando la prova a mano. Bloccante
        # di Claude Fable 5 e rischio segnalato da GPT-5.5 sulla PR #47.
        # Il motivo va attribuito al parser CHE L'HA PRODOTTO: `motivi` porta con
        # se' la riga di origine, perche' scriverlo sotto il primo parser
        # dell'elenco manderebbe a correggere una regola che non ha nulla che non
        # va — una diagnosi che punta al posto sbagliato e' peggio di nessuna
        # diagnosi. Segnalato da GPT-5.5 e Claude Fable 5 sulla PR #47, ed era un
        # difetto che avevo introdotto correggendo il precedente.
        riga_origine, primo_motivo = motivi[0]
        esito = f'scartato: {primo_motivo}'
        c.execute('INSERT INTO message_logs(user_id, parser_id, chat_id, text,'
                  ' esito) VALUES (?,?,?,?,?)',
                  (utente_id, riga_origine[0], chat_riga_id, text, esito))
        return esito
    # Vince l'ULTIMO nell'ordine dichiarato; i battuti nei log, col nome
    # visibile del vincente (slug, o name per i parser legacy).
    vincente, parsed = riconosciuti[-1]
    nome_vincente = vincente[10] or vincente[1]
    try:
        store_signal(c, parsed['csv'], vincente[1], profile=None, utente=utente_id)
    except ValueError:
        # Deterministico: lo stesso messaggio produrrebbe lo stesso CSV rotto.
        # 200, cosi' Telegram non ritenta; il motivo resta su /health, non nella
        # risposta di un endpoint pubblico. E NIENTE log dei battuti: si scrivono
        # dopo il vincente, o racconterebbero una sostituzione mai avvenuta
        # ([REAL_FINDING] di Fable al gate finale della PR #44).
        return 'csv_non_valido'
    for battuto, _ in riconosciuti[:-1]:
        c.execute('INSERT INTO message_logs(user_id, parser_id, chat_id,'
                  ' text, esito) VALUES (?,?,?,?,?)',
                  (utente_id, battuto[0], chat_riga_id, text,
                   f'riconosciuto, sostituito da {nome_vincente}'))
    c.execute('INSERT INTO message_logs(user_id, parser_id, chat_id, text,'
              ' esito) VALUES (?,?,?,?,?)',
              (utente_id, vincente[0], chat_riga_id, text,
               f'segnale scritto ({nome_vincente})'))
    # Gli avvisi della sorgente squadre (#34 pezzo 3): il segnale E' scritto —
    # verbatim, deciso dal proprietario — ma la squadra senza alias va detta
    # QUI, l'unico posto dove il cliente la vede sul traffico vero. Al piu'
    # due righe per messaggio (le due meta' di EventName).
    for avviso in parsed.get('avvisi') or []:
        c.execute('INSERT INTO message_logs(user_id, parser_id, chat_id, text,'
                  ' esito) VALUES (?,?,?,?,?)',
                  (utente_id, vincente[0], chat_riga_id, text,
                   f'avviso: {avviso}'))
    return {'event': parsed['event']}


def _raggruppa(righe, chiave):
    """Gruppi contigui per chiave, nell'ordine d'arrivo (le righe sono gia' ORDER BY)."""
    gruppi = []
    for r in righe:
        if gruppi and chiave(gruppi[-1][1][0]) == chiave(r):
            gruppi[-1][1].append(r)
        else:
            gruppi.append((chiave(r), [r]))
    return gruppi


def _profili_della_chat(c, chat_id):
    """TUTTI i profili (in ordine di nome) che elencano questa chat.

    Tutti e non il primo, ed e' la differenza col webhook storico: nel dispatch
    per link nessuno «vince» la chat, e questo lookup serve a trovare OGNI
    profilo che i link non rappresentano ancora — con due profili scoperti,
    fermarsi al primo lasciava muto il secondo.
    """
    profiles = c.execute('SELECT name,chat_ids,parser FROM profiles ORDER BY name').fetchall()
    return [dict(zip(['name', 'chat_ids', 'parser'], row)) for row in profiles
            if chat_id in {x.strip() for x in row[1].split(',') if x.strip()}]


def _utente_del_profilo_nei_link(c, nome_profilo, chat_riga_id):
    """Vero se l'utente di questo profilo e' RAPPRESENTATO dai link della chat.

    La rappresentanza si misura su TUTTI i link dell'utente, SENZA il filtro
    `active`: se l'utente ha un link su questa chat, il sistema dei link
    possiede il suo dispatch — e `active=0` significa silenzio, non «torna al
    fallback». Misurarla sui soli link attivi faceva rieseguire dal fallback il
    parser che l'utente aveva appena disattivato ([REAL_FINDING] di GPT-5.6 Sol
    al gate finale della PR #44).
    """
    riga = c.execute('SELECT id FROM users WHERE origin_profile=?',
                     (nome_profilo,)).fetchone()
    if riga is None:
        return False
    return c.execute(
        'SELECT 1 FROM parser_chats pc JOIN parsers p ON p.id = pc.parser_id'
        ' WHERE pc.chat_id=? AND p.user_id=?',
        (chat_riga_id, riga[0])).fetchone() is not None


def _elabora_profilo(c, profile, text):
    """Il percorso legacy per UN profilo. Restituisce l'esito, non committa."""
    # `active` vale anche qui: un parser disattivato non gira da nessun percorso.
    attivo = c.execute('SELECT IFNULL(active, 1) FROM parsers WHERE name=?',
                       (profile['parser'],)).fetchone()
    if attivo is not None and not attivo[0]:
        return 'parser_no_match'
    bloccato = accesso_bloccato_del_profilo(c, profile['name'])
    if bloccato:
        return f'access_{bloccato}'
    # Il parser che il profilo nomina puo' NON esistere: `DELETE /api/parsers`
    # non guarda i profili, ed e' il pericolo 2 della #25. Finche' il seme lo
    # ricreava a ogni avvio il buco durava fino al riavvio; senza seme durerebbe
    # per sempre — `get_parser` solleva 404, il webhook risponde 404 e Telegram
    # ritenta in ciclo. Qui la consegna si IGNORA: il segnale non arriva
    # comunque, ma il retry si esaurisce e gli altri profili della chat
    # continuano a essere elaborati.
    #
    # UNA lettura sola, non un controllo di esistenza seguito dalla lettura: fra
    # due query ci sarebbe una finestra, e una cancellazione concorrente che ci
    # passa in mezzo farebbe sollevare 404 lo stesso.
    cfg = parser_se_esiste(c, profile['parser'])
    if cfg is None:
        return 'parser_mancante'
    # `esito_messaggio`, non `elabora_messaggio`: il secondo scarterebbe i `motivi` e
    # il profilo tornerebbe al generico `parser_no_match`. Con i motivi, un segnale
    # fermato dal giudizio (handicap fuori scala, emoji nell'evento — audit #81 C2)
    # dice il PERCHE' nel `message_logs` del profilo, come sul percorso per-utente,
    # invece di sparire in silenzio.
    parsed, motivi = esito_messaggio(text, cfg)
    if not parsed:
        return f'scartato: {motivi[0]}' if motivi else 'parser_no_match'
    try:
        store_signal(c, parsed['csv'], profile['parser'], profile['name'])
    except ValueError:
        return 'csv_non_valido'
    return {'event': parsed['event']}


# Prototipo della web app SaaS: file statici, nessuna dipendenza aggiuntiva.
# Montato per ultimo per non intercettare gli endpoint del relay. `WEB_DIR` e'
# definita in cima al modulo, insieme alle altre costanti, perche' la legge anche
# la facciata su `/`.
if WEB_DIR.is_dir():
    app.mount('/app', StaticFiles(directory=WEB_DIR, html=True), name='app')
