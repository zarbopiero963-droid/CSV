import asyncio, base64, binascii, csv, hashlib, hmac, io, json, logging, os, re, secrets, sqlite3, threading, time
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

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
    ('parsers', 'user_id', 'INTEGER'),
    ('parsers', 'slug', 'TEXT'),
    ('parsers', 'config_json', 'TEXT'),
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
    for tabella, colonna, tipo in COLONNE_MULTIUTENTE:
        try:
            c.execute(f'ALTER TABLE {tabella} ADD COLUMN {colonna} {tipo}')
        except sqlite3.OperationalError as e:
            # SOLO la colonna che esiste gia'. Prima questo `except` era nudo e
            # avrebbe ingoiato anche «no such table», cioe' uno schema mancante.
            if 'duplicate column name' not in str(e).lower():
                raise
    c.execute('INSERT OR IGNORE INTO parsers(name,header,market_name,market_type,'
              'selection_name,handicap,bet_type) VALUES (?,?,?,?,?,?,?)',
              (DEFAULT_PARSER, 'P.Bet. PREMACHT 0,5HT', 'Over/Under 1,5 gol',
               'OVER_UNDER_15', 'Over 1,5 goal', '0', 'PUNTA'))
    # Preserve the existing Telegram setup as the default PIERO feed.
    c.execute('INSERT OR IGNORE INTO profiles(name,chat_ids,parser) VALUES (?,?,?)',
              (PIERO_PROFILE, os.getenv('TELEGRAM_ALLOWED_CHAT_IDS', ''), DEFAULT_PARSER))
    c.execute('UPDATE signals SET profile=? WHERE profile IS NULL', (PIERO_PROFILE,))
    _travasa_nel_multiutente(c)
    c.commit()


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


def riconcilia_su_utente(c, da_utente, a_utente):
    """Travasa tutto da `da_utente` a `a_utente` e libera il suo `telegram_id`.

    Esiste perche' il collegamento del proprietario deve essere **idempotente**: se una
    riga sbagliata possiede il suo `telegram_id`, non basta scrivere quel valore sulla riga
    giusta — `users.telegram_id` e' UNIQUE, quindi va prima liberato. E cio' che quella
    riga avesse accumulato non va perso, da cui il travaso invece di una `DELETE`.

    Riusa `RIFERIMENTI_UTENTE` e `_trasferisci_parser` della migrazione (regola 3): sono le
    stesse otto coppie tabella/colonna, e tenerne due elenchi sarebbe la duplicazione che
    diverge al primo `ALTER TABLE`. Il test che verifica la completezza di
    `RIFERIMENTI_UTENTE` copre quindi anche questa funzione.

    La riga perdente **non** viene cancellata: le si azzera `telegram_id` e resta un
    account senza niente. Una `DELETE` sarebbe irreversibile e potrebbe orfanare una
    colonna che nessuno ha ancora aggiunto a `RIFERIMENTI_UTENTE`; un `NULL` no. E' la
    scelta prudente in una funzione che nasce per riparare, non per fare pulizia.

    Non fa `commit`: la libera chi chiama, perche' questa operazione deve stare nella
    stessa transazione della scrittura che segue.
    """
    for tabella, colonna in RIFERIMENTI_UTENTE:
        c.execute(f'UPDATE {tabella} SET {colonna}=? WHERE {colonna}=?',
                  (a_utente, da_utente))
    _trasferisci_parser(c, da_utente, a_utente)
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
    # Oggi nessun codice scrive in `parser_chats`, quindi il ripuntamento non ha
    # ancora niente da salvare; il PR sul dispatch lo trovera' fatto invece di
    # scoprirlo su dati di un cliente.
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
    if cfg['header'].lower() not in message.lower():
        return None
    line = next((x.strip() for x in message.splitlines() if '🆚' in x), '')
    if not line:
        return None
    event = line.split('🆚', 1)[1].strip()
    # Nessun evento dopo il marcatore: non si inventa un nome squadra vuoto.
    if not event:
        return None
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
    riga = c.execute('SELECT id, session_version, status, is_admin, first_name,'
                     ' access_expires_at FROM users WHERE id=?',
                     (sessione['utente'],)).fetchone()
    c.close()
    if not riga or riga[1] != sessione['versione']:
        return None
    return {'id': riga[0], 'versione': riga[1], 'status': riga[2],
            'is_admin': bool(riga[3]), 'first_name': riga[4],
            'access_expires_at': riga[5]}


def _rispondi_con_sessione(utente, versione, corpo):
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
    riga = c.execute('SELECT id, session_version FROM users WHERE telegram_id=?',
                     (data.id,)).fetchone()
    # Il proprietario: il suo account esiste gia' e possiede i suoi parser, ma non ha
    # `telegram_id` perche' nessuno lo aveva mai saputo. Si ATTACCA a quella riga invece
    # di crearne una nuova, altrimenti resterebbero due account — uno con tutta la sua
    # roba e nessun modo di entrarci, e uno vuoto in cui entra.
    #
    # E' un'INVARIANTE e non un ramo, e la differenza e' tutto: «se chi fa login e' l'ID
    # dell'amministratore, la riga PIERO possiede quel telegram_id», qualunque cosa ci sia
    # adesso. Prima il collegamento viveva dentro `if riga is None`, quindi funzionava solo
    # se la variabile era impostata PRIMA del primo login: un login fatto troppo presto
    # creava un account vuoto con quel telegram_id, e da quel momento ogni login successivo
    # prendeva il ramo `else` e la riga PIERO non veniva collegata mai piu'. Senza via di
    # ritorno — la riconciliazione della migrazione raggruppa per `origin_profile` e a
    # quella riga e' cieca, nessun endpoint ripara, un riavvio non ripara — e senza nessun
    # errore: solo una dashboard vuota. Su Railway lo stato «variabile impostata ma non
    # arrivata nel processo» si produce da se' quando un build fallisce dopo averla
    # cambiata, quindi il caso non era ipotetico.
    #
    # Idempotente significa che l'ordine non conta e che il login successivo RIPARA quello
    # precedente.
    proprietario = None
    if TELEGRAM_ADMIN_ID and data.id == TELEGRAM_ADMIN_ID:
        # `telegram_id` nella SELECT perche' serve a sapere se l'identita' CAMBIA: vedi sotto.
        proprietario = c.execute('SELECT id, session_version, telegram_id FROM users'
                                 ' WHERE origin_profile=?', (PIERO_PROFILE,)).fetchone()

    if proprietario and (riga is None or riga[0] != proprietario[0]):
        if riga is not None:
            # Un'altra riga possiede il telegram_id dell'amministratore: e' l'account nato
            # da un login fatto troppo presto. Va travasata prima di riscrivere il valore,
            # perche' la colonna e' UNIQUE.
            riconcilia_su_utente(c, da_utente=riga[0], a_utente=proprietario[0])
            # In `admin_audit`, perche' una riparazione silenziosa e' indistinguibile da
            # un'appropriazione di account: se un giorno questo ramo scattasse quando non
            # deve, deve restare la traccia di quando e su chi.
            _annota_admin(c, proprietario[0], 'riconciliato_account_duplicato',
                          bersaglio=riga[0])
        # `telegram_reachable` non compare: prima c'era `telegram_reachable=
        # telegram_reachable`, che riscrive la colonna col proprio valore e non fa
        # niente. Suggeriva un'intenzione che il codice non porta. Segnalato da
        # CodeRabbit sulla PR #23.
        # Se l'identita' Telegram del proprietario CAMBIA, le sessioni aperte con quella
        # vecchia vanno revocate. Il cookie e' legato all'`id` della riga e a
        # `session_version`, non al `telegram_id`: senza l'incremento, chi era entrato con
        # l'identita' precedente conserva **accesso amministrativo** sulla riga che possiede
        # i parser. E non scade: `/api/me` rinnova il cookie a ogni richiesta valida, quindi
        # una sessione tenuta attiva e' immortale.
        #
        # Il caso che fa paura non e' ipotetico: se in quella variabile fosse finito l'ID
        # sbagliato — un estraneo, o un account compromesso — correggerla non gli toglierebbe
        # il pannello. Cambiare identita' e' esattamente il caso per cui `session_version`
        # esiste. Bloccante di GPT-5.6 Sol sulla PR #24.
        #
        # Solo al CAMBIO, non a ogni login: incrementarla sempre chiuderebbe al proprietario
        # la sessione sul telefono ogni volta che entra dal computer.
        cambia_identita = proprietario[2] is not None and proprietario[2] != data.id
        c.execute('UPDATE users SET telegram_id=?, username=?, first_name=?, is_admin=1'
                  + (', session_version=session_version+1' if cambia_identita else '')
                  + ' WHERE id=?',
                  (data.id, data.username or None, data.first_name or None,
                   proprietario[0]))
        if cambia_identita:
            _annota_admin(c, proprietario[0], 'identita_telegram_sostituita')
        # RILETTA dal database, non calcolata: il cookie che sta per essere emesso viene
        # firmato con questa versione, e se non fosse quella scritta il login riuscirebbe
        # emettendo una sessione morta all'istante. Una riga in piu' invece di un `+ 1`
        # a mano, perche' il valore giusto e' quello che il database ha.
        riga = c.execute('SELECT id, session_version FROM users WHERE id=?',
                         (proprietario[0],)).fetchone()
    elif riga is None:
        # Un cliente nuovo: l'account nasce e non puo' fare niente. L'accesso lo
        # concede il PR sull'approvazione (#7), non questo.
        #
        # `OR IGNORE` piu' la rilettura qui sotto perche' fra il SELECT e questo
        # INSERT c'e' spazio per un'altra richiesta, e `telegram_id` e' UNIQUE: due
        # login simultanei di un utente nuovo non davano una riga doppia, davano un
        # `IntegrityError`, cioe' un 500 a chi perdeva la corsa — al PRIMO accesso di
        # un cliente, l'unico momento in cui puo' capitare. Il Login Widget in una
        # pagina ricaricata, o due schede aperte, bastano. Segnalato da Claude
        # Fable 5 sulla PR #23; la rilettura fa si' che il perdente si attacchi alla
        # riga del vincitore invece di crearne un'altra.
        c.execute('INSERT OR IGNORE INTO users(telegram_id, username, first_name,'
                  " status) VALUES (?,?,?,'registrato')",
                  (data.id, data.username or None, data.first_name or None))
        riga = c.execute('SELECT id, session_version FROM users WHERE telegram_id=?',
                         (data.id,)).fetchone()
        if riga is None:
            # Non deve accadere: l'inserimento e' andato o la riga c'era. Se accade,
            # e' meglio un 503 che un `TypeError` sull'indice di `None`.
            c.close()
            raise HTTPException(503, 'account non creato: riprova')
    else:
        # Login successivi: il nome su Telegram puo' essere cambiato.
        c.execute('UPDATE users SET username=?, first_name=? WHERE id=?',
                  (data.username or None, data.first_name or None, riga[0]))
    c.commit()
    utente, versione = riga[0], riga[1]
    c.close()
    return _rispondi_con_sessione(utente, versione, {'ok': True, 'utente': utente})


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
    utente = utente_dalla_sessione(request)
    if not utente:
        raise HTTPException(401, 'sessione assente o scaduta')
    return _rispondi_con_sessione(utente['id'], utente['versione'], {
        'utente': utente['id'], 'nome': utente['first_name'],
        'stato': utente['status'], 'admin': utente['is_admin'],
        'accesso_scade': utente['access_expires_at']})


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
# Montato per ultimo per non intercettare gli endpoint del relay. `WEB_DIR` e'
# definita in cima al modulo, insieme alle altre costanti, perche' la legge anche
# la facciata su `/`.
if WEB_DIR.is_dir():
    app.mount('/app', StaticFiles(directory=WEB_DIR, html=True), name='app')
