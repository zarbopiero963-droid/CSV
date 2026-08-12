# XTrader Signal Relay — architettura SaaS

Documento di riferimento per la trasformazione del relay mono-utente in servizio
multiutente. Descrive il modello dati, il contratto API e le regole di isolamento
concordate. Il prototipo della web app in `web/` implementa già questo contratto
con dati finti.

## Modello concettuale

L'entità centrale è il **parser**, non il profilo. È il parser che possiede
configurazione, feed CSV, timer dei 90 secondi e log.

**Il token no, e questa riga è stata corretta il 12/08/2026 perché diceva il
contrario.** Qui c'era scritto che il parser possiede anche il token; nel «Modello
dati» più sotto `token_hash` e `token_prefix` stanno su `users`, ed è lì che il codice
li ha creati. Lo stesso documento affermava quindi due cose incompatibili, e il PR
sull'autenticazione avrebbe implementato quella che leggeva per prima. La versione
giusta è **il token appartiene all'utente**: è la correzione del modello sbagliato del
prototipo, registrata in #2. Segnalato da CodeRabbit sulla PR #22, ed è la stessa forma
del difetto raccontato in `CLAUDE.md` — una documentazione che contiene l'affermazione
e la sua smentita.

```text
utente (login Telegram)   ← il TOKEN del feed sta qui: `users.token_hash`
 └── parser  (slug, config propria, feed proprio, timer proprio)
      └── N chat Telegram   ←→   una chat può alimentare N parser dello stesso utente
```

Il "profilo" si riduce allo slug dell'utente nell'URL del feed:

```text
/profiles/PIERO/over-15-premacht.csv?token=...
/profiles/PIERO/over-25-live.csv?token=...
/profiles/MARCO/handicap-asiatico.csv?token=...
```

## Modello dati

Questo elenco è **misurato su `PRAGMA table_info`** dopo `migra()`, non copiato dal
disegno: fino al 12/08/2026 descriveva un modello che il codice non aveva ancora, e
due voci erano diventate false — `token_hash` risultava su `parsers` mentre sta su
`users`, e `signals` risultava con `parser_id` mentre ha `profile` e `user_id`. Un
modello dati scritto a mano accanto a un modello dati eseguito è la duplicazione che
la regola 3 vieta; qui vive perché il codice non lo genera, e per questo va
ri-misurato ogni volta che lo schema cambia.

```text
users
  id, origin_profile (unique), telegram_id (unique), username, first_name,
  slug (unique), token_hash, token_prefix, status, access_expires_at,
  telegram_reachable, session_version, is_admin, created_at
                           `origin_profile` è il profilo da cui la migrazione ha
                           creato l'utente, e serve come chiave stabile per
                           ritrovarlo ai riavvii. NULL per chi non viene da un
                           profilo, che è il caso di tutti i prossimi utenti.
                           Non `first_name`: non è univoco (i nomi Telegram non lo
                           sono affatto) e il login lo sovrascrive col nome vero.
                           L'unicità è anche un indice, perché un database che riceve
                           la colonna dall'ALTER non porta il vincolo con sé

parsers            ← tabella PREESISTENTE, estesa con ALTER additivo
  name (primary key), header, market_name, market_type, selection_name,
  handicap, bet_type,                       ← le colonne del formato originale
  user_id, slug, config_json, active, ordine, created_at, id
  unique (user_id, slug)   come indice: su una tabella esistente UNIQUE non si
  unique (id)              aggiunge con ALTER, e un PRIMARY KEY nemmeno. `id` è
                           riempito dal `rowid`, che `parser_chats` può riferire

chats
  id, telegram_chat_id, message_thread_id, title, type, owner_user_id, verified_at
  unique (telegram_chat_id, IFNULL(message_thread_id, ''))
                           l'`IFNULL` NON è cosmetico: in SQL `NULL != NULL`, quindi
                           un UNIQUE sulla coppia non deduplica le chat senza topic

parser_chats
  parser_id, chat_id
  primary key (parser_id, chat_id)

signals            ← tabella PREESISTENTE, estesa con ALTER additivo
  id, csv, parser, profile, created_at, expires_at,   ← formato originale
  user_id                                             ← destinazione nuova
  una sola riga viva per parser. `profile` continua a governare il feed: il
  passaggio a `user_id` è del PR sul feed per utente, non di questo

message_logs
  id, user_id, parser_id, chat_id, text, esito, created_at
  mai token, mai segreti

chat_verifications
  code (primary key), user_id, expires_at, consumed_at

access_requests
  id, user_id, created_at, decided_at, decided_by, granted_days, outcome

admin_audit
  id, admin_user_id, target_user_id, action, created_at

feed_reads
  token_id, giorno, ip_hash
  primary key (token_id, giorno, ip_hash)

webhook_seen
  update_id (primary key), created_at
  il dedup delle riconsegne di Telegram: senza, una riconsegna riscrive il
  segnale e fa ripartire il TTL

profiles           ← tabella PREESISTENTE, invariata
  name (primary key), chat_ids, parser
  resta la fonte del filtro delle chat finché il dispatch multi-parser non arriva
```

`webhook_seen`, `message_logs` e `feed_reads` **esistono ma nessun codice le legge
ancora**: il dedup degli `update_id`, i log persistenti e il conteggio delle letture
sono comportamenti dei PR successivi. Le tabelle nascono adesso perché aggiungerle
dopo vorrebbe dire una seconda migrazione su un database con dati dei clienti;
dichiararle funzionanti sarebbe la copertura finta che `CLAUDE.md` vieta.

### Cosa fa la migrazione con i dati che trova

`migra()` **non cancella e non rinomina nessuna tabella, e non perde nessuna
associazione**, gira **una volta per processo** (non a ogni connessione) e sta sul
percorso di `db()`.

La formulazione è più precisa di quella che stava qui («non cancella niente»), che dalla
deduplica in poi era falsa: le righe `chats` **duplicate** vengono rimosse, dopo aver
ri-puntato su quella sopravvissuta chi le riferiva. Nessuna informazione se ne va —
la riga rimossa era una copia — ma «niente» era la parola sbagliata, ed è il tipo di
imprecisione che questo documento esiste per non avere. Segnalato da CodeRabbit.
Le righe di `users` invece non si cancellano mai, perché possiedono dati: vedi sotto.

Dal fatto che `migra()` sta sul percorso di `db()` viene il vincolo che ne governa la
forma: **non può sollevare per dati che esistono**, perché sollevare lì significa 500 su
ogni richiesta, feed di XTrader compreso, e nessun riavvio lo cambierebbe. **Cinque**
stati dei dati veri lo avrebbero fatto, tutti trovati da review o da test e tutti chiusi
con una disambiguazione deterministica invece di un errore:

- due parser i cui nomi differiscono solo per maiuscole → `slug` uguale → `-2`, `-3`;
- due profili nella stessa condizione → stesso trattamento su `users.slug`;
- **due profili che elencano la stessa chat** → una sola riga in `chats`, e la
  proprietà resta al primo che l'ha dichiarata (`id` più basso). È la regola 3 di
  «Regole di isolamento» applicata: una chat appartiene a un solo utente. Chi
  puntava alla riga scartata viene **ri-puntato** su quella sopravvissuta, perché
  cancellare senza ri-puntare lascerebbe un `parser_chats.chat_id` che riferisce un
  `id` inesistente — un parser che smette di ricevere in silenzio. Il ri-puntamento è
  `UPDATE OR IGNORE` seguito da una `DELETE`, perché `parser_chats` ha
  `PRIMARY KEY (parser_id, chat_id)`: un parser associato a **entrambe** le righe
  duplicate produrrebbe altrimenti una riga che esiste già, cioè un altro modo di
  rendere la migrazione non attraversabile, dentro la correzione che ne chiudeva uno;
- **due utenti con lo stesso `origin_profile`**, cioè lo stato che l'assenza di indice
  sui database già migrati permetteva → l'etichetta resta a **una sola** riga e sulle
  altre diventa NULL. Qui **non si cancella nessuna riga**, a differenza delle chat:
  una riga di `users` possiede chat, parser e segnali, e cancellarla perderebbe dati di
  un cliente. Ma azzerare l'etichetta **non basta**: ciò che la riga perdente possiede
  — chat, segnali, parser — viene **trasferito al superstite** prima di togliergliela,
  altrimenti quei dati restano su un utente che non risulta più quel profilo, cioè
  nessuno li rivendica e per il codice multiutente sono di un altro. Nel trasferimento
  dei parser lo **slug che collide** viene ri-disambiguato: `UNIQUE (user_id, slug)`
  vieta la coppia, e due parser di utenti diversi con lo stesso slug sono uno stato
  legale sotto quel vincolo — quindi esiste, e uno `UPDATE` in blocco vi sbatterebbe
  contro. A cambiare nome è chi arriva, non chi era già del destinatario, che potrebbe
  avere quello slug in un URL già in uso.

  **Chi vince** fra due righe con la stessa etichetta è chi ha un `telegram_id`, e solo a
  parità l'`id` più basso: quella riga è l'identità con cui l'utente **accede**, mentre
  la riga creata dal travaso è un segnaposto. Tenere l'id minimo a prescindere
  sposterebbe i dati sul segnaposto e lascerebbe vuoto l'account del login, cioè
  separerebbe proprietà e identità.

  Ciò che si sposta è **tutto** ciò che riferisce l'utente, elencato in
  `RIFERIMENTI_UTENTE`: `chats.owner_user_id`, `signals.user_id`,
  `message_logs.user_id`, `chat_verifications.user_id`, `access_requests.user_id` e
  `.decided_by`, `admin_audit.admin_user_id` e `.target_user_id` — più i parser, che
  passano dalla funzione dedicata perché devono anche ri-disambiguare lo slug. Un test
  confronta quell'elenco con lo schema reale, così una colonna nuova che riferisce un
  utente non può restare fuori in silenzio.

Il ri-puntamento di `parser_chats` sposta **solo** le associazioni dei parser che
appartengono al proprietario della chat sopravvissuta. Con la stessa chat rivendicata da
due utenti — la riga del primo sopravvive, quella del secondo viene scartata — spostare
tutto aggancerebbe un parser del secondo alla chat del primo: un legame fra utenti
diversi, che nel dispatch significa i segnali di una chat consegnati al feed sbagliato.
La correzione non è spostare meglio, è **non spostare**: la chat appartiene a un solo
utente, quindi l'associazione di un parser altrui è illegittima e viene rimossa.

Un indice UNIQUE non si crea su una tabella che contiene già duplicati: ogni vincolo
nuovo va quindi preceduto dalla deduplica di ciò che esiste, o la migrazione muore
proprio sul database che doveva proteggere. È la stessa classe tre volte in questo PR,
e la terza l'ho reintrodotta subito dopo aver chiuso la seconda.

Chi vince, quando due profili rivendicano la stessa cosa — una chat o un parser — è
deciso da un **`ORDER BY name` esplicito** sul ciclo dei profili. Senza, «il primo»
significa «il primo che la tabella restituisce», cioè l'ordine di inserimento: due
database con gli stessi profili creati in ordine diverso davano proprietari diversi.

E `COLONNE_MULTIUTENTE` porta anche le **due colonne legacy** `signals.profile` e
`signals.expires_at`, che non sono nuove: su un database creato prima dei profili il
`CREATE TABLE IF NOT EXISTS` non fa nulla, la tabella esiste senza di esse, e la
`UPDATE signals SET profile=?` muore con «no such column: profile». Erano due `ALTER`
del codice precedente che questa riscrittura aveva perso.

Le colonne aggiunte a `parsers` sono riempite da `_completa_colonne_nuove()`, che la
migrazione chiama **e** che chiama `POST /api/parsers`. Le due chiamate non sono un
duplicato: `migra()` gira una volta per processo, quindi senza la seconda un parser
creato dopo l'avvio resterebbe senza `user_id`, `slug`, `ordine` e `id` fino al riavvio
successivo — cioè fuori dall'indice `UNIQUE (user_id, slug)`, che con `user_id` NULL
non vincola niente, e non riferibile da `parser_chats`.

**L'appartenenza di un parser si legge da `profiles.parser`**, non si assume: il
travaso assegna il parser di ciascun profilo all'utente di quel profilo. Solo i parser
che nessun profilo nomina finiscono al proprietario per difetto — quelli non hanno
un'appartenenza da leggere, e lasciarli senza `user_id` li terrebbe fuori dall'indice
`UNIQUE (user_id, slug)`. Due profili che nominano lo stesso parser lo lasciano al
primo, come per le chat condivise e per la stessa ragione.

Il **proprietario per difetto è un argomento obbligatorio** di quella funzione, non una
costante al suo interno. Oggi entrambi i chiamanti passano `PIERO`, che è l'unico utente; il giorno
che l'endpoint servirà più utenti va passato il proprietario della sessione, e la
decisione è visibile nei due punti che la prendono invece di essere sepolta in una
funzione di migrazione. **Resta aperto**, e appartiene al PR sul login: a chi
appartiene un parser creato da un amministratore per conto di un altro utente.

Per la stessa ragione quell'endpoint non fa più `INSERT OR REPLACE`: `REPLACE` cancella
la riga e la reinserisce, quindi cambiare l'header di un parser lo staccherebbe dal suo
utente azzerandone l'`id`. Fa invece `INSERT OR IGNORE` seguito da `UPDATE`, e non
`ON CONFLICT DO UPDATE`, che sarebbe più compatto: l'UPSERT richiede SQLite ≥ 3.24 e la
versione in produzione non è misurabile da qui, quindi la dipendenza è stata rimossa
anziché documentata.

A quale dei due utenti debba appartenere una chat che entrambi rivendicano *davvero*
è una decisione del PR sul dispatch multi-parser, dove il webhook deve sceglierne uno;
qui è fissata come «il primo», e un test la tiene ferma perché il giorno che cambia si
veda.

## Regole di isolamento

1. Ogni parser appartiene obbligatoriamente a un utente. `user_id` viene sempre
   preso dalla sessione, mai da un parametro della richiesta. Dal PR 6 quella
   sessione esiste, e il solo posto da cui leggerla è `utente_dalla_sessione()`
   in `main.py` — vedi «Il cookie di sessione».
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

Il **disegno**, implementato con dati finti in `web/api.js`, un commento per endpoint.
Non è ciò che il server espone: le rotte che esistono davvero sono elencate in
«Le rotte di sessione che esistono davvero» sotto «Autenticazione», e i **nomi non
coincidono** — il disegno dice `/api/auth/telegram/widget`, il server risponde su
`/api/login/telegram`. Riconciliare i due è il PR che collega la web app al backend,
non questo: fino a quel momento va letta la sezione sotto, non questo blocco.

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

### Le rotte di sessione che esistono davvero

Dal PR 6. Quattro rotte, e sono le **uniche** del servizio con un'autenticazione
propria: tutte le altre sono o pubbliche, o protette da `auth()` col token unico
descritto sotto. La distinzione non è narrativa — è una delle tre categorie che
`tests/relay/test_autenticazione.py` verifica coprano **ogni** rotta dichiarata
dall'app, così una rotta nuova senza serratura fa fallire quel test invece di
passare inosservata.

| Rotta | Cosa fa | Rifiuta con |
|---|---|---|
| `POST /api/login/telegram` | firma del Login Widget → sessione | `401 login non valido` |
| `POST /api/login/password` | `administrator` + password → sessione | `401`, `429` se frenato, `503` se la variabile manca |
| `POST /api/logout` | cancella il cookie | niente: è pubblica di proposito |
| `GET /api/me` | chi è l'utente della sessione | `401 sessione assente o scaduta` |

`POST /api/logout` è pubblica **per scelta**, non per dimenticanza: cancella un cookie
e non legge nulla. Metterle una serratura significherebbe che chi ha un cookie
malformato non riesce a liberarsene, cioè resta incastrato in uno stato da cui
l'unica uscita è svuotare i cookie a mano.

`GET /api/me` non restituisce mai un token, né l'hash della password, né il
`telegram_id`: i primi due sono segreti, il terzo non serve al browser e finirebbe nei
log di qualunque proxy davanti al servizio. Restituisce `utente`, `nome`, `stato`,
`admin`, `accesso_scade`.

**Il messaggio d'errore non distingue «firma sbagliata» da «firma scaduta».** Per
differenza si impara, e chi prova non deve sapere quale dei due muri ha toccato.

### Le due porte, e perché non sono ridondanza

Il proprietario entra in due modi, e il secondo non è un vezzo:

1. **Login Telegram** (`TELEGRAM_ADMIN_ID`). Il suo account nel database esiste già —
   è la riga con `origin_profile = 'PIERO'` — e possiede i suoi parser, ma **non ha
   `telegram_id`**, perché nessuno lo aveva mai saputo. Al primo login la variabile
   dice «questo ID è il proprietario», e il codice **attacca** il `telegram_id` a quella
   riga invece di crearne una nuova. Senza la variabile resterebbero due account: uno
   con tutta la sua roba e nessun modo di entrarci, e uno vuoto in cui entra.
2. **Password** (`ADMIN_PASSWORD_HASH`), utente fisso `administrator`. Esiste perché
   con il solo login Telegram un guasto di Telegram, o la perdita di quell'account,
   chiuderebbero il proprietario fuori dal proprio pannello.

Nella variabile va **l'hash**, non la password. La dashboard di Railway è leggibile da
chi ha accesso al progetto, e con la password in chiaro chi la legge entra nel pannello
— da cui si cancellano parser e si inietta un segnale nel feed che XTrader legge. Si
cambia password cambiando la variabile; il comando che genera l'hash è in `README.txt`.

Il percorso a password ha un **freno**: cinque tentativi falliti e si chiude per cinque
minuti, con `429` e non `401`, perché chi legge deve sapere che il muro è il freno e non
la password. Il freno è **globale e non per IP**, con un baratto dichiarato: per IP non
frenerebbe nulla, perché chi prova password in automatico cambia indirizzo; globale sì,
al prezzo che un estraneo può tenere occupato quel percorso per qualche minuto. Il
prezzo è accettabile **proprio perché le porte sono due** — il proprietario entra col
login Telegram mentre quella a password è frenata. È la ragione tecnica per cui averne
due non è ridondanza.

Ogni accesso riuscito con password scrive una riga in `admin_audit`: è l'unico modo per
cui un accesso non suo sia visibile, e per cui «non sono stato io» sia dimostrabile.

### Il cookie di sessione

`utente.versione.emessa.firma`, firmato HMAC-SHA256. Un cookie lo scrive il browser:
senza firma `utente=7` diventa `utente=8` con un editor di testo, e la firma è ciò che
lo trasforma da dichiarazione in credenziale.

Il segreto è **derivato dal token del bot**, `sha256('betrelay-sessione-v1:' + token)`,
come `webhook_secret` e per la stessa ragione: una variabile a sé lascerebbe una
finestra fra il deploy e la sua configurazione, e in quella finestra bisognerebbe
scegliere fra un login rotto e un login che accetta cookie non firmati — due modi di
sbagliare. Il prefisso lo separa dal segreto del webhook, così un valore rubato da un
canale non serve nell'altro. **Senza bot il segreto è vuoto e nessuna sessione è
valida**: fail-closed, come `auth()` dopo la correzione di luglio.

Attributi: `HttpOnly` (un cookie leggibile da JavaScript è un cookie che un XSS porta
via), `SameSite=Lax` (una POST da un altro sito non deve portarsi dietro la sessione),
`Secure` (il servizio è dietro TLS, quindi non c'è niente da perdere).

Tre controlli, e ognuno serve per un motivo diverso:

- la **firma**, contro un cookie riscritto;
- la **scadenza per inattività**, 20 minuti dal momento in `emessa`. È «di inattività» e
  non «di sessione» perché il cookie viene riemesso a ogni risposta che apre una
  sessione;
- **`session_version` confrontata con quella nel database**, che è il modo di invalidare
  una sessione **subito** senza aspettare i venti minuti: serve per «entra come cliente»
  e per buttare fuori un accesso sospetto. Senza quel confronto un cookie rubato
  resterebbe valido fino alla scadenza naturale e non ci sarebbe niente da fare. Il
  meccanismo era completamente scoperto dai test finché un sabotaggio non l'ha mostrato:
  toltogli il confronto, la suite restava verde.

`POST /api/logout` **non** incrementa `session_version`: quello butterebbe fuori tutte
le sessioni di quell'utente da tutti i dispositivi, e non è ciò che chiede chi preme
«esci» su un computer.

### La sessione e il feed non si toccano

`user_id` viene **sempre** dalla sessione (`utente_dalla_sessione`) e **mai** da un
parametro della richiesta: un `user_id` letto da un parametro o da un header è un
`user_id` scelto dal mittente.

Il verso opposto conta altrettanto, ed è una **NON-relazione** che va misurata via HTTP
perché non si vede leggendo il codice: `/xtrader.csv` e `/profiles/…` **non consultano
la sessione**. Non la leggono oggi e non devono cominciare. XTrader non ha cookie, non
fa login e interroga il feed a raffica: il giorno che una scadenza di sessione potesse
svuotare un feed, il segnale morirebbe in silenzio mentre il pannello funziona
perfettamente. Il test `test_la_sessione_scaduta_NON_tocca_il_feed` presenta un cookie
scaduto, verifica il `401` su `/api/me`, e asserisce che i **byte** del feed sono
identici a prima.

### Cosa il PR 6 non fa

Un account nuovo nasce con `status = 'registrato'` e non può fare niente: **concedere
l'accesso è il PR sull'approvazione**, non questo. Le rotte `/api/*` di gestione restano
protette da `auth()` col token unico: passarle alla sessione è il PR sul feed per
utente. E la web app in `web/` continua a girare sui dati finti di `web/api.js` — i suoi
nomi di rotta non sono ancora quelli del server, vedi «Contratto API».

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
| Fatto | Lo **schema** di questa sezione, creato in place su SQLite da `migra()`, con i dati esistenti travasati e i test in `tests/relay/test_schema.py`. Le tabelle esistono e i vincoli reggono; il *comportamento* che le usa è dei PR sotto |
| Fatto | **Login Telegram reale e sessioni** (PR 6): le quattro rotte di «Le rotte di sessione che esistono davvero», il cookie firmato, le due porte del proprietario, e la NON-relazione fra sessione e feed. Manca il resto di M3: la web app è ancora sui dati finti |
| M1 | Token hashati, verifica chat, feed per utente, compatibilità `/xtrader.csv`. **Postgres differito** e non più urgente: i dati persistono già, `DB_PATH` in produzione è `/data/signals.db` dentro il volume (misurato il 12/08/2026) |
| M2 | Motore di parsing generico in Python, endpoint di test, dispatch multi-parser nel webhook |
| M3 | Accesso su approvazione, la web app collegata al backend |
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
