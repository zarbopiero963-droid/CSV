# XTrader Signal Relay — architettura SaaS

Documento di riferimento per la trasformazione del relay mono-utente in servizio
multiutente. Descrive il modello dati, il contratto API e le regole di isolamento
concordate. Il prototipo della web app in `web/` implementa già questo contratto
con dati finti.

## Modello concettuale

L'entità centrale è il **parser**, non il profilo. È il parser che possiede
configurazione, token, feed CSV, timer dei 90 secondi e log.

```text
utente (login Telegram)
 └── parser  (slug, token proprio, config propria, feed proprio, timer proprio)
      └── N chat Telegram   ←→   una chat può alimentare N parser dello stesso utente
```

Il "profilo" si riduce allo slug dell'utente nell'URL del feed:

```text
/profiles/PIERO/over-15-premacht.csv?token=...
/profiles/PIERO/over-25-live.csv?token=...
/profiles/MARCO/handicap-asiatico.csv?token=...
```

## Modello dati

```text
users
  id, telegram_id (unique), username, first_name, slug (unique), status, created_at

parsers
  id, user_id, slug, name, config_json, token_hash, token_prefix, active, created_at
  unique (user_id, slug)

chats
  id, telegram_chat_id, message_thread_id, title, type, owner_user_id, verified_at
  unique (telegram_chat_id, message_thread_id)

parser_chats
  parser_id, chat_id
  primary key (parser_id, chat_id)

signals
  id, parser_id, csv, expires_at, created_at
  una sola riga viva per parser

message_logs
  id, parser_id, chat_id, text, matched, created_at
  mai token, mai segreti

chat_verifications
  code (unique), user_id, expires_at, consumed_at
```

## Regole di isolamento

1. Ogni parser appartiene obbligatoriamente a un utente. `user_id` viene sempre
   preso dalla sessione, mai da un parametro della richiesta.
2. Ogni endpoint che riceve un `parser_id` verifica la proprietà prima di leggere
   o scrivere. Un parser di un altro utente risponde 404, non 403: non si rivela
   nemmeno l'esistenza.
3. **Una chat appartiene a un solo utente**, ma può essere assegnata a più parser
   di quell'utente. L'unicità sta su `chats.telegram_chat_id`, non su
   `parser_chats`. Senza questo vincolo due account potrebbero leggere i segnali
   della stessa chat.
4. La rivendicazione di una chat si prova con un codice usa-e-getta che l'utente
   incolla nel gruppo. Il webhook accetta messaggi da chat sconosciute solo se
   contengono un codice di verifica valido; tutto il resto viene scartato. Il
   codice **scade** (10 minuti): è l'unica eccezione prevista al filtro delle
   chat, e un'eccezione che non si chiude non è un'eccezione. Lo stato
   `expired` fa parte del contratto, non è un dettaglio del prototipo.
5. Un parser non può cancellare, modificare o sostituire il segnale di un altro
   parser. Il `DELETE` che precede l'inserimento di un nuovo segnale è sempre
   filtrato per `parser_id`.
6. Ogni parser ha il proprio timer di 90 secondi, indipendente.
7. Stesso messaggio, stessa chat, parser diversi: due elaborazioni indipendenti e
   due CSV distinti. Chi non riconosce il messaggio lo ignora senza toccare nulla.

## Token dei feed

- Generati con almeno 18 byte casuali, prefisso `xt_`.
- Sul server si conserva **solo** `sha256(token)`. I token hanno entropia alta:
  non serve un KDF lento, serve non salvarli in chiaro.
- Il token in chiaro esiste in una sola risposta HTTP, quella della generazione.
- Si conserva un `token_prefix` di 9 caratteri per identificarlo nella UI.
- Rigenerare invalida immediatamente il precedente.
- Il token globale `CSV_ACCESS_TOKEN` oggi in uso è già circolato: va ruotato
  prima dell'uso commerciale.

## Motore di parsing

La configurazione di un parser è un JSON con una condizione di riconoscimento e
una regola per ciascuna delle 14 colonne XTrader. La specifica eseguibile è in
`web/engine.js`; il motore Python deve produrre output identici.

```json
{
  "match": { "type": "contains", "value": "P.Bet. PREMACHT 0,5HT" },
  "columns": {
    "Provider":  { "source": "constant", "value": "XTrader" },
    "EventName": { "source": "line", "anchor": "🆚", "part": "after", "marker": "🆚",
                   "transforms": [ { "op": "replace_last", "from": " v ", "to": " - " },
                                   { "op": "trim" } ] },
    "Price":     { "source": "regex", "pattern": "@\\s*([0-9.,]+)", "group": 1,
                   "transforms": [ { "op": "comma_to_dot" } ] }
  }
}
```

Sorgenti supportate: `empty`, `constant`, `message`, `line` (con `part` `whole` o
`after`), `regex`. Trasformazioni: `trim`, `replace_last`, `replace_all`, `upper`,
`lower`, `comma_to_dot`, `dot_to_comma`, `digits_only`.

Il confronto della riga e il taglio del marcatore ignorano entrambi maiuscole e
minuscole. Le ancore vengono tagliate per **codepoint**, non per unità UTF-16:
un emoji astrale a cavallo del taglio lascerebbe un surrogato spaiato, e
un'ancora così non combacerebbe più con nessuna riga, in silenzio.

`runParser` restituisce quattro campi: `matched` (la condizione combacia), `row`
(le 14 colonne), `missing` (le colonne obbligatorie risultate vuote) e `complete`.
**Chi scrive il feed deve guardare `complete`, non `matched`:** un messaggio
riconosciuto ma privo dell'evento produrrebbe una riga formalmente valida e priva
di senso per XTrader. Le colonne obbligatorie sono in `REQUIRED_COLUMNS` e oggi
sono `Provider` ed `EventName`. **`Price` non è obbligatoria:** il parser in
produzione (`main.py`) la lascia vuota perché la quota la mette XTrader dal
proprio book, quindi pretenderla bloccherebbe i segnali reali.

Il valore di una colonna obbligatoria viene **normalizzato** prima del controllo:
uno spazio è un carattere, quindi `" "` senza `trim` risulterebbe valorizzato e
`complete` diverrebbe vero su una riga priva di evento. La normalizzazione sta
nel motore e non fra le trasformazioni della regola, perché il `trim` della
regola è opzionale e lo decide l'utente nel wizard: se il pavimento dipendesse
dalla configurazione, sarebbe corretto solo per i parser configurati bene. **Il
motore Python deve normalizzare allo stesso modo**, o le due implementazioni
divergono sul caso limite invece che sul caso normale, cioè dove nessuno guarda.

Il contratto con XTrader: 14 colonne, tutti i campi tra virgolette, separatore
virgola, terminatore CRLF, **UTF-8 con BOM**.

Colonne, ordine, quoting e terminatore sono sempre stati questi e non cambiano.
**L'encoding invece è cambiato:** fino all'11/08/2026 il feed usciva senza BOM,
e XTrader lo pretende. Non era un'aggiunta opzionale, era un difetto.

## Verifica del formato

Il contratto non è affidato a una convenzione: c'è una funzione che lo controlla,
in entrambe le implementazioni. `verify_csv()` in `main.py` e `verifyCsv()` in
`web/engine.js` verificano BOM, intestazione esatta nell'ordine, CRLF senza LF
nudi, tutti i campi fra virgolette, 14 campi per riga, e al massimo due righe.

È agganciata in **tre** punti, e il numero conta più della funzione:

1. **`store_signal()`**, fail-closed: un CSV che non passa non viene memorizzato,
   quindi una riga malformata non esiste nemmeno per i 90 secondi del TTL.
2. **`GET /health`**, che restituisce
   `{"status": …, "csv": "ok", "auth": "ok", "feed_scartati": 0}` e diventa
   `degraded` con il motivo quando il controllo fallisce. `auth` risponde a una
   domanda diversa — se il token è configurato — e sta lì per la stessa ragione:
   un controllo che nessuno legge non è un controllo.
3. **La vista Feed CSV del prototipo**, con l'indicatore «formato valido per
   XTrader» / «formato non valido» e il motivo scritto sotto.

Sul percorso di consegna del feed c'è **una sola** verifica, e non può produrre un
errore: se la riga letta dal database non passa il controllo, si serve il feed
vuoto invece del contenuto sospetto. Serve per le righe scritte da una versione
precedente, che stanno già nel database e uscirebbero così come sono per i
secondi che restano loro. Degrada a «nessun segnale», mai a `500`: un difetto del
verificatore non deve diventare un errore verso XTrader.

**E lascia una traccia.** Degradare in silenzio ha il difetto opposto al `500`:
un bug in `verify_csv()` azzererebbe *ogni* feed di *ogni* cliente, e dall'esterno
si vedrebbe solo «nessun segnale» — indistinguibile da un giorno senza partite.
Quindi ogni scarto incrementa un contatore in memoria e scrive una riga di log col
nome del profilo e il motivo, **mai** il contenuto del CSV; `/health` espone
`feed_scartati` e, se diverso da zero, `ultimo_scarto`.

Conta le **righe distinte**, non le richieste. La differenza è tutta l'utilità del
contatore: XTrader interroga il feed a raffica e la risposta è `no-store`, quindi
una sola riga vecchia resterebbe guasta per tutti i 90 secondi del TTL e
produrrebbe decine di scarti per un unico evento benigno — cioè un contatore che
sale in fretta, che è esattamente il segnale con cui si dovrebbe riconoscere il
guasto vero. La riga si riconosce da un **digest**, così due righe diverse si
distinguono senza conservare il segnale di un cliente in una variabile del
processo. Il log segue la stessa regola, altrimenti 90 secondi di righe identiche
renderebbero illeggibile proprio il log che serve a capire.

La chiave della deduplica è la **coppia profilo + riga**, non la riga sola, e la
distinzione è nata da un bloccante di Fable 5 confermato da GPT-5.5: con un digest
unico per processo il contatore sbagliava in due modi opposti, entrambi misurati e
ora fissati da test. Due profili con la **stessa** riga guasta contavano 1 invece
di 2 — un guasto che colpisce due clienti si leggeva come se ne avesse colpito
uno; due profili con righe guaste **diverse** contavano 12 richieste su 12, perché
l'impronta globale cambiava a ogni hit essendo quella dell'altro profilo, cioè di
nuovo la raffica che la deduplica doveva eliminare. In un servizio multiutente su
una sola istanza è lo scenario normale, non un caso limite.

Sul singolo profilo la voce è l'ultima riga scartata, non un insieme: due righe
guaste alternate sullo stesso feed contano a ogni cambio, ed è voluto — un feed che
oscilla fra due righe invalide è un guasto, non un evento unico. La mappa cresce di
una voce per profilo con un feed guasto, quindi è limitata dal numero di profili.

Il contatore **non** fa scattare `degraded`, di proposito: lo scarto atteso — la
riga della versione precedente, subito dopo un deploy — è benigno e si risolve da
sé col TTL, quindi marcarlo `degraded` terrebbe il processo «malato» per tutta la
sua vita dopo ogni deploy normale, cioè un allarme sempre acceso. Il segnale utile
è il **ritmo**: un contatore che continua a salire è il bug che azzera i feed, e
si vede confrontando due letture. È il pannello Salute dell'admin a leggerlo.

Due limiti da tenere presenti quando il pannello lo mostrerà: il valore è **per
processo** e si azzera al riavvio, quindi con più worker o più istanze su Railway
ogni risposta riporta solo la propria quota e **non** un totale globale; e
l'incremento è protetto da un lock perché gli handler sono sincroni e girano nel
threadpool, dove `+= 1` non è atomico e si perderebbero proprio gli incrementi
sotto il carico in cui contano di più.

Il terzo punto è la lezione del Bridge. Là la funzione equivalente esisteva già
ed era usata altrove, ma nessun semaforo del pannello la consultava: l'unico
avviso era una riga di log all'avvio, e un CSV inservibile è rimasto tale per
mesi. **Un controllo che nessuno legge non è un controllo.**

## Contratto API

Implementato con dati finti in `web/api.js`, un commento per endpoint.

```text
POST   /api/auth/telegram/widget      verifica HMAC del Login Widget → sessione
POST   /api/auth/telegram/code        login con codice usa-e-getta dal bot
GET    /api/me
POST   /api/auth/logout
GET    /api/settings                  bot_url e base_url impostati dall'admin

GET    /api/parsers                   solo i parser dell'utente autenticato
POST   /api/parsers                   { name }
GET    /api/parsers/:id
PATCH  /api/parsers/:id               { name?, active?, config?, sample_message? }
                                      solo queste chiavi: id, slug, token_prefix e
                                      has_token non sono modificabili dal client
DELETE /api/parsers/:id
POST   /api/parsers/:id/token/rotate  → { token, url }  il token solo qui
DELETE /api/parsers/:id/token
POST   /api/parsers/:id/test          { message } → { matched, complete, missing, row, csv }
POST   /api/parsers/:id/suggest       { message } → config proposta dal modello
PUT    /api/parsers/:id/chats         { chat_ids }

GET    /api/chats
POST   /api/chats/verify/start        → { code, expires_at }
GET    /api/chats/verify/status       polling: none | waiting | verified | expired
DELETE /api/chats/:id

GET    /api/logs?parser_id=

GET    /profiles/:user_slug/:parser_slug.csv?token=...    feed per parser
GET    /xtrader.csv?token=...                             alias legacy di PIERO
POST   /telegram/webhook                                  unico bot, dispatch per chat_id
```

## Autenticazione

Telegram Login Widget come percorso principale: la firma HMAC-SHA256 del
data-check-string si verifica lato server con chiave `sha256(bot_token)`.
Fallback con deep-link al bot e codice a 6 cifre, per chi arriva da mobile.
La sessione è un cookie httpOnly firmato. Il token del bot resta sul server: la
web app non lo riceve e non lo conserva mai.

### Il token del relay oggi: obbligatorio, e fail-closed

Il servizio in produzione usa ancora `CSV_ACCESS_TOKEN`, un token unico che
protegge dieci rotte: i due feed CSV e le otto API di gestione — quattro in
lettura, sei in scrittura. Non è il modello
finale — quello sono i token per-feed con hash descritti in «Token dei feed» — ma
finché non esiste, quella variabile è **l'unica serratura del servizio**.

`auth()` **rifiuta quando il token non è configurato**, con `503 servizio non
configurato`. Non è una scelta di stile: fino all'11/08/2026 la funzione era
`if TOKEN and token != TOKEN`, cioè un no-op a variabile assente, e il modo di
rendere il servizio scrivibile da Internet era cancellare una variabile dalla
dashboard di Railway. Misurato sul percorso HTTP reale: `GET /xtrader.csv` su un
servizio senza token configurato rispondeva **200 con il feed**.

`/health` riporta `auth: ok` oppure `auth: non configurato`, e in quel secondo
caso `status` diventa `degraded`. A differenza degli scarti di consegna — che
scadono da sé col TTL e quindi non fanno scattare `degraded` — una variabile
mancante non si ripara da sola.

### Pensionamento di `TELEGRAM_ALLOWED_CHAT_IDS`

La variabile popola i `chat_ids` del **solo** profilo `PIERO`, cablato in
`main.py`, tramite un `INSERT OR IGNORE` all'inizializzazione del database. Non
è una via che scala male: è una via che non scala affatto — non esiste alcun
meccanismo per cui possa servire un secondo utente, e una variabile per cliente
imporrebbe un rideploy, cioè un'interruzione per tutti gli altri, per farne
entrare uno.

Tre stadi, e oggi siamo al secondo:

| stadio | come entra un `chat_id` | serve un deploy? | serve il proprietario? |
|---|---|---|---|
| pre-SaaS | variabile d'ambiente, seed del profilo `PIERO` | sì | sì |
| **oggi** | `POST /api/profiles` col token admin | no | sì |
| M1 | l'utente lo registra da sé col codice di verifica | no | no |

**Sequenza sicura per rimuoverla**, e l'ordine conta:

1. volume montato e `DB_PATH` che punta al suo interno;
2. deploy: il seed scrive i `chat_id` dalla variabile sul disco **persistente**;
3. verifica che i `chat_ids` di `PIERO` siano quelli attesi, con
   `GET /api/profiles` **e l'header** `X-Admin-Token: <CSV_ACCESS_TOKEN>` — è una
   rotta protetta, e senza header risponde 401 (o 503 se il token non è
   configurato): il passo scritto senza header non verificherebbe niente;
4. **solo allora** si rimuove la variabile.

Invertendo 1 e 4 il seed scrive una riga **vuota** su disco persistente, e
`INSERT OR IGNORE` non la correggerà più: il guasto passa da temporaneo —
si autoriparava al deploy successivo rimettendo la variabile — a permanente,
riparabile solo via `POST /api/profiles`.

Dopo il passo 1 la variabile è comunque **inerte**: la riga `PIERO` esiste già e
`INSERT OR IGNORE` non la aggiorna, quindi modificarla non cambia più nulla. Per
cambiare i `chat_id` si usa l'API.

### Autenticazione del webhook

Il webhook non può usare `CSV_ACCESS_TOKEN` — a chiamarlo è Telegram — ma non è
per questo aperto: pretende l'header `X-Telegram-Bot-Api-Secret-Token` e risponde
`403` senza.

**Il filtro dei `chat_id` non è la protezione**, e la distinzione è il difetto che
questa parte chiude: quel filtro fa *instradamento* — decide a quale feed
appartiene un messaggio — e non può autenticare, perché il `chat_id` arriva nel
corpo della richiesta e quindi lo scrive il mittente. Prima del `secret_token`,
`POST /telegram/webhook` era un percorso di **scrittura non autenticato** verso i
segnali che XTrader legge: misurato, un POST forgiato senza alcun token rispondeva
`200` e la riga entrava nel feed, mentre leggere lo stesso feed dava `401`.
Segnalato da Fugu Ultra sulla PR #12, Issue #13.

Il segreto è **derivato** da `TELEGRAM_BOT_TOKEN` con un digest, non è una
variabile a sé. Una variabile nuova lascerebbe una finestra fra il deploy e la sua
configurazione, e in quella finestra bisognerebbe scegliere fra un webhook muto e
un webhook aperto: due modi di sbagliare. Derivandolo, il valore esiste sempre
dove esiste il bot, non sta nel repository, e Telegram lo riceve alla
registrazione all'avvio.

**Senza bot il webhook rifiuta tutto**, non accetta. Senza `TELEGRAM_BOT_TOKEN` non
c'è modo di validare nessuna consegna, quindi **questa istanza non ne accetta
nessuna**. Non che non possano arrivarne: Telegram può consegnare attraverso una
registrazione fatta da un deploy precedente, e la prima versione di questa frase
diceva il contrario — segnalato da CodeRabbit. Ma un'istanza che non sa
riconoscerle non ha niente da guadagnare ad accettarle. La prima versione accettava, e quello
riapriva il difetto in un ramo: `TELEGRAM_ALLOWED_CHAT_IDS` popola il profilo
`PIERO` **indipendentemente** dal bot, quindi un'istanza senza bot ma con i
`chat_id` configurati restava iniettabile. Nessuna variabile di override per lo
sviluppo locale, di proposito: sarebbe una scorciatoia che un domani finisce
impostata in produzione.

### Il blackout, e perché non si risolve rinunciando all'enforcement

Il guasto da temere è preciso: se `setWebhook` fallisce e Telegram conserva una
registrazione vecchia **senza** segreto, continua a consegnare senza header e il
relay rifiuta tutto — i segnali si fermano in silenzio, che è peggio del difetto
che l'enforcement chiude. È lo stato del **primo deploy** dopo l'introduzione del
segreto, quando la registrazione precedente non ne aveva uno: scenario concreto,
non teorico.

La soluzione **non** è condizionare l'enforcement all'esito della registrazione:
quello riaprirebbe la scrittura non autenticata ogni volta che la rete fa i
capricci, in silenzio, cioè il difetto originale. La soluzione è **ritentare**:

1. all'avvio, tre tentativi;
2. e poi da **ogni consegna rifiutata**.

**Cosa dimostra una consegna rifiutata**, perché la prima versione di questa
sezione diceva di più di quello che si sa: dimostra **solo** che la validazione
dell'header è fallita. Non che la richiesta venga da Telegram, e non che Telegram
non conosca il segreto — può essere un POST forgiato da chiunque. Segnalato da
CodeRabbit.

Sono due ipotesi, e il ritentativo le copre entrambe senza doverle distinguere: se
la registrazione era **stantia** la rimette a posto e il segnale arriva col giro
dopo, perché Telegram ritenta le consegne; se la richiesta era **forgiata** costa
un tentativo. In entrambi i casi la richiesta viene rifiutata.

Il ritentativo da richiesta ha un freno di 60 secondi, perché quel percorso lo
raggiunge chiunque: senza freno una raffica di POST forgiati diventerebbe una
raffica di chiamate verso `api.telegram.org` fatte da noi.

**Quello che limita la frequenza è il freno, non il flag.** La prima versione
usciva subito quando `webhook_registrato` era già `true`, col ragionamento
«Telegram sa il segreto, non c'è niente da riparare» — e così l'autoriparazione
era morta esattamente nel caso in cui una registrazione riuscita può diventare
stantia: qualcuno chiama `setWebhook` sullo stesso bot **senza** segreto (un altro
strumento, un deploy vecchio) e da quel momento Telegram consegna senza header.
Segnali fermi, `/health` che dice `true`, e nessun posto dove vederlo. Una consegna
rifiutata che arriva mentre il flag dice `true` è l'unica informazione che
**contraddice** il valore in cache, e veniva buttata via. Segnalato da Fugu Ultra.

Le chiamate a `setWebhook` girano sempre **in un thread**, sia all'avvio sia dalla
richiesta: hanno un timeout di dieci secondi e l'avvio le ripete tre volte, quindi
eseguite sull'event loop una rete lenta terrebbe fermo il servizio per decine di
secondi. Segnalato da Fable 5 e Fugu Ultra.

E all'avvio la registrazione parte **dietro**, non davanti: l'handler di `startup`
crea un task e termina subito. Il thread non basta, ed è la distinzione che la prima
correzione aveva mancato — `asyncio.to_thread` libera l'**event loop**, ma un handler
di `startup` ASGI deve *terminare* perché uvicorn cominci a servire. Finché non
termina il processo non è pronto e `/health` non risponde affatto: non lentamente,
per niente. Con tre tentativi da dieci secondi sono oltre trenta secondi di
indisponibilità a ogni deploy con la rete lenta. Segnalato da Fugu Ultra.

Conseguenza operativa, scritta anche in `README.txt`: nei primi secondi dopo un
deploy lo `status` è `degraded` perché la registrazione è ancora in corso — e in
quella finestra il relay davvero non può ricevere niente, quindi è la risposta
onesta. Non è un guasto; lo diventa se non passa a `ok` entro **un minuto**.

Un minuto e non trenta secondi, perché nel caso peggiore i tre tentativi durano
**~33 secondi** — 10 di timeout, 1 di pausa, 10, 2 di pausa, 10 — più l'avvio del
processo: a trenta secondi il terzo tentativo può essere ancora in volo, e un
`degraded` a quel punto non dice ancora niente. La prima versione di questa frase
diceva «mezzo minuto», che è un errore di aritmetica sulla sequenza di attese
descritta qui sopra e avrebbe fatto rincorrere un non-guasto. Segnalato da
CodeRabbit.

`sano` chiede `webhook_registrato is True`, non `is not False`: `None` con un bot
configurato significa **«non ancora»**, non «sano», e un'istanza col bot che non ha
mai completato la registrazione non riceve nessun segnale. Dichiararla sana a tempo
indeterminato era la metà non corretta della stessa classe chiusa per il caso
«nessun bot». Segnalato da GPT-5.5.

Il riferimento al task è tenuto in una variabile di modulo: un `Task` senza
riferimenti può essere raccolto dal garbage collector prima di finire, e la
registrazione non avverrebbe in silenzio. E se il task muore per un'eccezione
inattesa il fallimento viene **registrato** (`webhook_registrato: false`), perché
muore fuori dal flusso di avvio dove nessuno lo vedrebbe: «non tentato» e «tentato e
fallito» sono stati diversi, e solo il secondo dice che c'è un guasto da guardare.
Il fallimento del task non sovrascrive il `true` di un tentativo riuscito.

Il valore di `webhook_registrato` si legge **una volta e sotto lock**
(`_stato_registrazione`). Con tre letture separate e fuori dal lock — com'era — una
registrazione che completa nel mezzo faceva uscire `status: ok` accanto a
`webhook_registrato: false`: un endpoint diagnostico che si contraddice non è
diagnostico. Segnalato da Fable 5.

Una registrazione conta come riuscita solo se **entrambe** le condizioni valgono:
la risposta è arrivata **e** contiene `{"ok": true}`. Il codice HTTP da solo non
basta, e non basta in due direzioni diverse: Telegram segnala parte dei rifiuti con
`HTTP 200` e `{"ok": false, "description": ...}` nel corpo, mentre altri arrivano
come errore HTTP — un token inesistente dà `404`, un `secret_token` con caratteri
non ammessi dà `400`. La prima versione di questa frase attribuiva a Telegram un
`HTTP 200` per tutti i rifiuti: segnalato da CodeRabbit, e nel codice non cambia
niente perché un errore HTTP arriva come eccezione e produce comunque `false`.

Il `secret_token` viaggia nel **corpo** del POST verso `api.telegram.org`, non in
un parametro di query: un URL non è un posto riservato, finisce nei log di ogni
intermediario che lo tocca, e questa chiamata si ripete a ogni deploy e a ogni
autoriparazione. Il token del bot resta nel percorso perché l'API di Telegram lo
mette lì e non c'è modo di spostarlo.

Poiché la chiamata di rete avviene **fuori** dal lock — deve, o una `setWebhook`
lenta bloccherebbe ogni consegna — i tentativi sono numerati e l'esito ricorda da
quale tentativo viene: vince il più **recente**, non l'ultimo a finire. Senza, un
tentativo partito prima e andato in timeout scriveva `false` sopra il `true` di uno
partito dopo e riuscito. Il rimedio non è rendere `true` appiccicoso: un
fallimento vero — bot cambiato, registrazione sovrascritta da un altro deploy —
diventerebbe invisibile per sempre, e questo flag non deve mentire in quella
direzione.

`/health` espone i due assi separatamente: `webhook` dice se l'enforcement è
attivo, `webhook_registrato` l'esito dell'**ultimo tentativo** di registrazione —
all'avvio o da una consegna rifiutata. **Entrambi** fanno scattare `degraded`,
perché resti diagnosticabile — non perché governino l'enforcement.

Che `webhook: chiuso senza bot` degradi lo `status` è una correzione: prima
`status` restava `ok`, perché `webhook_registrato` vale `None` quando non c'è bot e
la condizione chiedeva `is not False`. Un'istanza che rifiuta **ogni** consegna con
403 appariva sana, e su Railway sarebbe stata una spia verde su un servizio
incapace di ricevere segnali. Era il fratello non corretto della classe che `auth`
aveva già chiuso: `TELEGRAM_BOT_TOKEN` mancante è una variabile mancante, non si
ripara da sé, e va trattata come `CSV_ACCESS_TOKEN` mancante. Segnalato da Fugu
Ultra.

`status` è `ok` solo con **tutti e tre** gli assi a posto — formato CSV, token del
feed, webhook protetto e registrato — e quando è `degraded` il campo che lo spiega è
sempre nella risposta. L'altra faccia è vincolata da un test: una spia che resta
accesa anche quando tutto va bene insegna a ignorarla, ed è il rischio di ogni asse
aggiunto alla condizione.

Nel modello multiutente questo non cambia: **un solo bot serve tutti gli utenti**,
quindi c'è un solo segreto, derivato dallo stesso token. Ciò che cambia per utente
è l'instradamento — `chat_id` → parser — che resta la funzione del filtro, ora
senza doppio ruolo.

## Note operative su Telegram

- Nei **gruppi** il bot con privacy mode attiva vede solo i comandi. Va
  disattivata in BotFather con `/setprivacy → Disable`, oppure il bot va reso
  amministratore. Senza questo la verifica funziona e poi i segnali non arrivano.
- Nei **canali** il bot deve essere amministratore per ricevere i `channel_post`.
- Un solo bot serve tutti gli utenti: il dispatch avviene per `chat_id`.
- Per i topic dei supergruppi la chiave è `chat_id + message_thread_id`.

## Stato dei lavori

| | |
|---|---|
| Fatto | Prototipo web app in `web/`, servito su `/app`, con motore di parsing e contratto API |
| Fatto | Test hard del motore e del contratto CSV (`tests/engine/`), guardia sui workflow di review (`tests/safety/`) |
| M1 | Postgres, tabelle utenti/parser/chat, token hashati, verifica chat, feed per parser, compatibilità `/xtrader.csv` |
| M2 | Motore di parsing generico in Python, endpoint di test, dispatch multi-parser nel webhook |
| M3 | Login Telegram reale, sessioni, la web app collegata al backend |
| M4 | Log persistenti, sospensione, suggerimento AI lato server, abbonamenti |

## Facciata pubblica

`GET /` serve `web/sito.html`: la pagina che vede chi scrive `betrelay.net`. Fino
all'11/08/2026 l'apex restituiva `{"service": "xtrader-signal-relay", ...}` — corretto
per una sonda, inutile per una persona.

**Cosa contiene**, nell'ordine, perché la documentazione UI deve descrivere la pagina
com'è e non come sarebbe comoda:

| Fascia | Contenuto, verbatim dove è una scritta |
|---|---|
| Barra | marchio `BR` + «BetRelay», pulsante «Entra» → `/app/` |
| Stato | pastiglia con pallino verde: «Servizio in avviamento · accesso su approvazione» |
| Apertura | titolo «I segnali del tuo canale Telegram, **dentro XTrader**.», sommario, «Entra con Telegram» → `/app/` e «Come funziona» → `#come` |
| Come funziona | tre schede numerate: «Colleghi la chat», «Descrivi il messaggio», «Incolli l'indirizzo in XTrader» |
| Dal messaggio alla riga | il messaggio Telegram d'esempio accanto alle quattro colonne obbligatorie del CSV, e la nota dei 90 secondi |
| Cosa ottieni | «Un feed tuo», «Più parser insieme», «Log dei messaggi», «Niente da installare» |
| Come si entra | l'accesso su approvazione, e «Entra con Telegram» → `/app/` |
| Chiusura | «BetRelay · betrelay.net», «Applicazione» → `/app/`, «Stato del servizio» → `/health` |

**Invarianti che vincolano questa pagina**, non preferenze estetiche:

- è servita da una **rotta esplicita**. Un catch-all `@app.get('/{resto:path}')`
  coprirebbe `/feed/{utente}.csv` prima che quella rotta esista, e XTrader riceverebbe
  `text/html` con stato 200 al posto di un CSV;
- **nessun token** compare nella pagina: è pubblica e senza sessione, quindi qualunque
  valore scritto lì è pubblicato;
- **nessun `noindex`**, al contrario di `/app`: una landing che si esclude dai motori di
  ricerca non è una landing. La differenza fra le due pagine è vincolata da un test;
- **una pagina sola**: stile incorporato, nessun CDN, nessuna richiesta di rete oltre al
  documento. Gli stessi token di colore di `web/styles.css`, ricopiati di proposito;
- `.dentro` porta i margini laterali e **sta sempre da sola sul proprio elemento**. La
  prima versione la combinava con `.apertura`, la cui forma breve `padding: 72px 0 56px`
  azzerava il margine laterale: titolo e pulsanti attaccati al bordo del telefono.
  Misurato a 390 px, non guardato a occhio.

Il testo dice quello che il servizio fa **oggi**. La pastiglia «Servizio in avviamento»
e la sezione «Come si entra» esistono perché l'accesso su approvazione non è ancora
costruito: quando lo sarà, quella pastiglia va cambiata, non lasciata lì.

## Prototipo

```sh
uvicorn main:app --reload
```

Poi `http://127.0.0.1:8000/app/`, oppure `http://127.0.0.1:8000/` e il pulsante «Entra».
I dati vivono in `localStorage`, si azzerano da Impostazioni.

### Vista «Feed CSV»

Accanto al titolo «Contenuto attuale del feed» c'è l'indicatore del formato, con
due soli stati:

| Etichetta | Quando |
|---|---|
| `formato valido per XTrader` | `verifyCsv()` non trova niente da segnalare |
| `formato non valido` | qualunque violazione del contratto |

Nel secondo caso, sotto il CSV compare il motivo in chiaro — «manca il BOM:
XTrader non leggerebbe la prima colonna», «intestazione diversa dal contratto
(11 colonne)» — perché un indicatore rosso senza spiegazione trasforma un difetto
diagnosticabile in una telefonata.

La nota sotto il CSV dice, verbatim: «Un segnale resta nel feed 90 secondi, poi il
CSV torna alla sola intestazione. Il timer di questo parser è indipendente da
tutti gli altri. Il feed è UTF-8 con BOM, come XTrader lo pretende.»

Il BOM è un carattere a larghezza zero: nel blocco del CSV non si vede, ed è
corretto che non si veda. Chi vuole verificarlo guarda l'indicatore, non il testo.
