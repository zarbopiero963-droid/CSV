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

## Scadenza dell'accesso

Dal PR 7, e la regola che tiene tutto insieme è una: **la scadenza è un istante, non un
evento.** Nessun processo passa a riscrivere `status` quando una data passa, quindi la colonna
resta `'attivo'` per sempre e chi la legge direttamente mente. Misurato prima della correzione,
su `GET /api/me` di un cliente scaduto il giorno prima: `{'stato': 'attivo', 'accesso_scade':
<ieri>}` — il sintomo che questa Issue chiama «pannello che dice attivo e feed vuoto».

La conversione vive in `stato_effettivo()` e in un posto solo, e la usano `/api/me`, il feed e
il webhook. Non perché sia elegante: se ogni pezzo decidesse per conto proprio, il pannello
direbbe una cosa e il feed un'altra, e nessuno dei due sarebbe sbagliato in modo visibile.

| Cosa | Comportamento alla scadenza |
|---|---|
| Feed CSV | `200` con **sola intestazione** e BOM. **Non** `401`: per XTrader un errore HTTP è un guasto da segnalare, mentre «nessun segnale» è uno stato normale che gestisce da sé |
| Token del feed | **non revocato.** «Scaduto» e «revocato» sono stati diversi: revocare costringerebbe il cliente a riconfigurare XTrader a ogni rinnovo. Così il rinnovo è cambiare una data |
| Webhook | i messaggi delle sue chat non vengono elaborati **e non finiscono nei log**: quel log è una funzione del servizio, non un archivio |
| Il feed stesso | **non viene toccato** dal webhook: allo svuotamento ci pensa il TTL di 90 secondi, che è l'unico che deve toccarlo |
| Stato scritto a mano | `sospeso` e `registrato` **non** vengono riscritti dal tempo: sono decisioni del proprietario, e se la scadenza le sovrascrivesse il pannello perderebbe la sola informazione che dice perché quel cliente è fuori |

**Cosa blocca e cosa no, con la ragione.** Bloccano `scaduto` e `sospeso`. **Non**
`registrato`, ed è una linea di scopo dichiarata: i feed per profilo esistono da prima di
questo flusso e i loro utenti sono nati `registrato` dalla migrazione, quindi bloccarli adesso
spegnerebbe in silenzio un feed che oggi funziona — una regressione in produzione per applicare
una regola che riguarda clienti che ancora non esistono. E non è un buco: un cliente che si
registra col Login Widget nasce con `origin_profile` `NULL`, i profili li crea **solo** il
proprietario, quindi non ha nessun feed da bloccare. «Un utente nuovo non può fare nulla»
diventa vincolante nel PR 8, quando il feed passa all'utente.

Il **proprietario** (`is_admin`) non ha un abbonamento e il suo feed non dipende da nessuna
scadenza: è quello che XTrader interroga in produzione, e farlo dipendere da una data
significherebbe che una riga sbagliata nel database lo spegne senza un errore da nessuna parte.

**I rinnovi si sommano** se la scadenza è nel futuro, altrimenti ripartono da oggi. Senza il
secondo ramo, prorogare di 30 giorni un cliente scaduto da due mesi gli darebbe una scadenza
**nel passato**: di nuovo «attivo» nel pannello e feed vuoto.

**Il bot non può scrivere per primo**, ed è la trappola 1 di questa Issue: `sendMessage`
fallisce verso chi non ha mai aperto la conversazione col bot. Quindi la richiesta di accesso
restituisce un **deep link** (`t.me/<bot>?start=accesso`, da `TELEGRAM_BOT_USERNAME`), e quando
il cliente preme Start la consegna arriva al webhook, che mette `telegram_reachable` a 1. Quel
ramo del webhook **non indebolisce il filtro delle chat**: non tocca `signals`, non cerca
parser, non guarda `profiles` e non scrive nei log dei messaggi — scrive un booleano su una riga
di `users` trovata per il `telegram_id` che Telegram stessa attesta nella consegna.

E l'**errore di invio non viene ingoiato**: l'approvazione risponde `notificato: false` col
motivo e azzera `telegram_reachable`, perché un invio fallito in silenzio produce lo stato
peggiore — il proprietario crede di aver avvisato, il cliente non sa di essere attivo, e nessuno
dei due ha modo di accorgersene. L'accesso però **resta concesso**: è stato deciso, e una
decisione non si annulla perché l'avviso non è partito.

**Il promemoria ha un limite dichiarato:** non c'è uno scheduler. `POST /api/admin/promemoria`
va **chiamata** — dal proprietario o da un job programmato su Railway — e finché non viene
chiamata nessun promemoria parte. È un compito che aspetta, non un compito perso. I due percorsi
che girerebbero da soli sono il feed (che XTrader interroga a raffica: un invio Telegram lì lo
renderebbe lento e fragile) e il webhook (che dipende dai messaggi dei canali), e nessuno dei
due è il posto giusto. `users.promemoria_per` conserva **quale** scadenza è stata annunciata e
non un booleano: con un booleano il secondo rinnovo non avviserebbe mai più.

La prenotazione si conferma **prima** dell'invio, per due ragioni misurate sulla PR #26: due giri
concorrenti mandavano due avvisi per la stessa scadenza, e tenere aperta la transazione di
scrittura durante la rete faceva rispondere «database is locked» a feed e webhook per tutta la
durata del giro. **Il baratto è dichiarato: at-most-once.** Un crash fra la conferma e la chiamata
a Telegram consuma il promemoria senza averlo mandato, e nessuno riprova per quel ciclo; la scelta
opposta — inviare e poi scrivere — sposterebbe la finestra e produrrebbe avvisi **doppi**. Fra i
due, un promemoria di cortesia perso vale meno di un cliente che riceve due volte lo stesso
messaggio, e il costo è limitato perché la scadenza si vede comunque in dashboard. Un invio
fallito invece **rilascia** la prenotazione, e il rilascio porta nella `WHERE` la prenotazione
propria: se nel frattempo il proprietario ha rinnovato, non deve cancellare quella del ciclo
nuovo.

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
di senso per XTrader. Le colonne obbligatorie sono in `REQUIRED_COLUMNS` e dal
13/08/2026 (Issue #2, riconfermate su #25) sono **quattro**: `EventName`,
`MarketType`, `SelectionName`, `BetType` — l'evento, il tipo di mercato su cui
XTrader decide, la selezione, e se puntare o bancare. **`Provider` non è
obbligatoria:** è sempre la costante `"XTrader"` e pretenderla non protegge da
nulla. **`Price` non è obbligatoria:** il parser in produzione (`main.py`) la
lascia vuota perché la quota la mette XTrader dal proprio book, quindi pretenderla
bloccherebbe i segnali reali. **`MarketName` non è obbligatoria:** è l'etichetta
leggibile, mentre `MarketType` è il codice su cui XTrader agisce. La lista è
identica nei due motori (`REQUIRED_COLUMNS` in `web/engine.js`,
`COLONNE_OBBLIGATORIE` in `main.py`), tenuta allineata dal test di confronto: due
liste divergenti darebbero «completo» nel browser e feed vuoto in produzione.

Il valore di una colonna obbligatoria viene **normalizzato** prima del controllo:
uno spazio è un carattere, quindi `" "` senza `trim` risulterebbe valorizzato e
`complete` diverrebbe vero su una riga priva di evento. La normalizzazione sta
nel motore e non fra le trasformazioni della regola, perché il `trim` della
regola è opzionale e lo decide l'utente nel wizard: se il pavimento dipendesse
dalla configurazione, sarebbe corretto solo per i parser configurati bene. **Il
motore Python deve normalizzare allo stesso modo**, o le due implementazioni
divergono sul caso limite invece che sul caso normale, cioè dove nessuno guarda.

### Il server usa la config: il dispatcher `elabora_messaggio`

Il webhook e la rotta di prova (`POST /api/parsers/{name}/test`) non chiamano più
`parse_message` direttamente, ma passano da `elabora_messaggio(message, cfg)`:

- un parser **con** `config_json` gira sul motore configurabile (`esegui_parser`):
  se il risultato è `complete`, la sua `row` diventa il CSV del feed; altrimenti
  nessun segnale, come un `parse_message` che torna `None`;
- un parser **senza** `config_json` — PIERO e ogni parser legacy — resta su
  `parse_message`, **byte per byte com'era**. Il feed di produzione (`/xtrader.csv`)
  non cambia, ed è l'invariante che `tests/relay/test_dispatch_motore.py` vincola
  confrontando il dispatcher con `parse_message` sul parser legacy.

`config_json` era una colonna **morta** (creata dalla migrazione, mai letta né
scritta): da ora il server la legge. Le rotte che la **scrivono** — la creazione di
un parser dalla web app — arrivano col passo successivo (M3); finché non esistono,
in produzione nessun parser ha `config_json` e il dispatcher instrada tutto su
`parse_message`, quindi il comportamento è identico a prima.

### ReDoS: il timeout sulle regex dell'utente

Le regex dei parser (condizione `type: regex` e sorgente colonna `source: regex`)
le scrivono gli utenti e girano sul worker Railway **condiviso**. Un pattern con
backtracking catastrofico bloccherebbe il parsing di **tutti** i clienti, non solo
di chi l'ha scritto — il rischio di isolamento segnalato da Claude Fable 5 e GPT-5.6
Sol, e la richiesta esplicita del proprietario: «non deve bloccare a tutti, deve
essere tutto personale del cliente».

Lo `re` di stdlib non ha timeout, perciò le due chiamate su pattern dell'utente
passano da `_cerca_regex_utente`, che usa il modulo **`regex`** (dipendenza pinnata
in `requirements.txt`) con un deadline duro. Allo scadere, il match viene interrotto
e vale «nessuna corrispondenza»: il messaggio di **quel** cliente non produce
segnale, ma il worker resta libero per gli altri. Sia il timeout sia un errore di
compilazione danno lo stesso esito, mai un'eccezione che diventi un 500. È una
**asimmetria voluta** rispetto a `web/engine.js` (che gira nel browser del singolo
utente, sulla sua macchina, e non ha un worker condiviso da proteggere): la
anteprima non ha timeout, il server sì.

Il deadline è a **due livelli**, e serve il secondo: `REGEX_TIMEOUT_UTENTE = 0.1s`
è il tetto del **singolo match**, ma `esegui_parser` valuta una condizione **più 14
colonne** — fino a 15 regex. Col solo tetto per-match, un parser con 15 regex
catastrofiche sommerebbe **1,5s** (misurato) bloccando l'event loop per quel
messaggio. Perciò `esegui_parser` fissa un **budget di parser** condiviso
(`REGEX_BUDGET_PARSER_S = 0.1s`, un istante `time.monotonic`) e lo passa a ogni
match: ciascuno usa il minimo fra il tetto per-match e il tempo che resta, e quando
il budget è esaurito i match successivi non partono. L'intera esecuzione resta
quindi ~0,1s comunque siano scritte le regole.

Restano fuori due cose, entrambe dichiarate: la **config malformata** (JSON valido
ma non un oggetto-parser, `match`/`columns` storti, valori non-stringa) è gestita
dal fail-safe di `elabora_messaggio`, che la trasforma in «nessun segnale» invece
che in un 500; e il caso di un cliente che **inonda** di molti messaggi cattivi, che
il budget-per-messaggio non copre — lì servirà un **rate-limit per-utente**,
rimandato per decisione del proprietario.

Il contratto con XTrader: 14 colonne, tutti i campi tra virgolette, separatore
virgola, terminatore CRLF, **UTF-8 con BOM**.

Colonne, ordine, quoting e terminatore sono sempre stati questi e non cambiano.
**L'encoding invece è cambiato:** fino all'11/08/2026 il feed usciva senza BOM,
e XTrader lo pretende. Non era un'aggiunta opzionale, era un difetto.

## Come XTrader va configurato per leggere il feed

Il contratto CSV non basta: due impostazioni **della fonte dentro XTrader** decidono se
il feed produce scommesse o niente. Non sono consigli, sono condizioni — e XTrader non
segnala un errore quando mancano, mostra un'icona rossa accanto al segnale.

**Il riconoscimento deve essere per NOME, mai per ID.** XTrader individua la selezione in
due modi alternativi: dagli id Betfair (`MarketId` + `SelectionId`) oppure dai nomi
(`EventName` + `MarketType` + `SelectionName`). Il relay può usare **solo** il secondo, e
non per scelta: risolvere gli id richiede l'API di Betfair Exchange, che il servizio non
ha e non avrà. Per questo `EventId`, `MarketId` e `SelectionId` escono **sempre vuoti**
dal feed — è il progetto, non una lacuna, e nel CSV prodotto da XTrader stesso sono vuoti
anche lì.

Da qui discendono le colonne obbligatorie, e spiegano perché sono quelle quattro:
`EventName`, `MarketType`, `SelectionName` sono le tre che il riconoscimento per nome
pretende; `BetType` non serve a riconoscere ma dice se puntare o bancare, e senza di essa
la riga non è una scommessa. Sono `COLONNE_OBBLIGATORIE` in `main.py` e
`REQUIRED_COLUMNS` in `web/engine.js`.

**La lingua della fonte deve essere quella dei nomi che scriviamo.** Il confronto per nome
avviene contro il palinsesto Betfair *nella lingua impostata sulla fonte*: lingue diverse,
nessuna selezione trovata. Per XTrader Italia è `ITA`.

### `Provider` esce vuota, e non è una dimenticanza

`Provider` è il nome di **chi manda** il segnale, non di chi lo legge — XTrader è il
consumatore, quindi scriverci `"XTrader"` è semanticamente sbagliato. **Da contratto la
colonna esce vuota**, e l'utente la valorizza come vuole configurando il proprio parser:
serve a lui, perché XTrader la usa come **filtro** («solo i segnali di quel provider») e
come **discriminante** fra segnali altrimenti identici. Il confronto non distingue
maiuscole.

Va corretto un difetto nostro: `suggestConfig()` in `web/engine.js` propone oggi
`Provider = "XTrader"` come costante. È il valore che compare nel CSV **prodotto da
XTrader**, e per questo sembrava giusto — ma lì `Provider` vale `XTrader` proprio perché
il file l'ha scritto XTrader. Nel nostro feed il provider siamo noi, o il canale, o niente.

### Niente emoji nel CSV, in nessuna colonna

È una **regola di contratto**, non un'avvertenza: nel CSV servito a XTrader non deve
comparire nessuna emoji, in nessun campo. Un segnale che ne contiene viene marcato **non
valido**, e come sempre senza restituire un errore — solo un'icona rossa accanto al
segnale.

**Le emoji stanno in entrata, non in uscita.** È la distinzione che tiene insieme questa
regola con la «REGOLA CODIFICA» di `CLAUDE.md`, che sembra dire il contrario e non lo dice:
lì gli emoji sono **dati portanti** perché sono i marcatori con cui il parser *riconosce*
il messaggio e *individua* dove leggere il valore — `🆚`, `⏰`, `✅`. Servono sul lato
Telegram. Il valore che finisce nel CSV è il testo **dopo** il marcatore, mai il marcatore.

Il punto delicato è `EventName`: i messaggi cominciano quasi sempre col marcatore, e una
regola che prende la **riga intera** invece del testo **dopo** se lo porta dentro. Allora
succede la cosa peggiore — il feed esce **formalmente valido** (14 colonne, virgolette,
CRLF, BOM), `verify_csv()` lo accetta perché controlla la forma e non il contenuto, e
XTrader lo scarta **in silenzio**. Il parser di riferimento usa `part: 'after'` con
`marker: '🆚'` esattamente per questo, ma un utente può configurarlo altrimenti.

Ne segue dove va il controllo, e sono due punti come per il resto del contratto: **nel
motore**, per colonna, così la diagnosi (#25) può dire *quale* campo contiene l'emoji e
suggerire «testo dopo il marcatore» invece di «riga intera»; e in **`verify_csv()` /
`verifyCsv()`** come pavimento, perché un CSV con un'emoji non è un CSV che XTrader legge.
Con la regola già adottata nella #39 l'esito è lo **scarto**: un valore che il consumatore
rifiuta non è un valore.

### L'intervallo della fonte, e chi evita davvero la doppia scommessa

XTrader consente di impostare l'intervallo di ricarica **da 1 secondo in su**. Il TTL del
feed è 90 secondi, quindi qualunque intervallo realistico gli sta molto sotto: un segnale
non può nascere e morire fra due letture.

E la riga che resta nel feed per tutti i 90 secondi **non produce scommesse ripetute**: un
segnale già riconosciuto non viene riletto come nuovo. La protezione contro la doppia
scommessa è dunque **di XTrader**, non nostra — il nostro TTL impedisce che il segnale
venga *riproposto come nuovo* dopo essere stato cancellato, che è una cosa diversa e
complementare.

### Le forme localizzate

La lingua non governa solo il riconoscimento: governa **come si scrivono i valori**. Tre
cose dipendono dal prodotto che legge il feed, e oggi ne serviamo una sola.

| Prodotto | Lingua fonte | Separatore decimale | `BetType` |
|---|---|---|---|
| **XTrader Italia** *(oggi)* | `ITA` | virgola — `"1,85"` | `PUNTA` / `BANCA` |
| Betting Toolkit *(in futuro)* | `ENG` / `ES` | punto — `"1.85"` | `BACK` / `LAY` |

`BACK`/`LAY` è la nomenclatura **Betfair generica**, quella che compare nel manuale di
XTrader; il prodotto italiano scrive `PUNTA`/`BANCA`, ed è ciò che XTrader produce quando
esporta un CSV. Le due colonne di questa tabella sono **lo stesso asse**: quando nascerà
la localizzazione al confine di scrittura, porterà entrambe le forme, non solo il
separatore — e aggiungere una lingua sarà una riga di tabella.

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

### Cosa esiste DAVVERO: il CRUD dei parser per-utente

Il primo pezzo del disegno sopra realizzato sul server, con i nomi veri (che
riconcilieremo col disegno nel PR che collega la web app). Autenticazione a
**sessione** (il cookie firmato), non col token del feed:

```text
GET    /api/me/parsers                 i parser dell'utente della sessione
POST   /api/me/parsers                 { titolo, config, active? } → parser creato
PUT    /api/me/parsers/{slug}          { titolo, config, active? } aggiorna il proprio
DELETE /api/me/parsers/{slug}          elimina il proprio
POST   /api/me/parsers/{slug}/test     { message } → { matched, missing, complete, event?, csv? }
```

Regole, tutte vincolate da `tests/relay/test_parser_crud.py`:

- **`user_id` viene SEMPRE dalla sessione**, mai dal corpo: un `user_id` messo nel
  JSON viene ignorato (Pydantic scarta i campi non dichiarati). Un utente vede,
  modifica ed elimina **solo** i propri parser; su un parser di un altro la risposta
  è **404**, non 403 — un 403 confermerebbe che quel parser esiste.
- Il **`titolo`** è l'etichetta che il cliente sceglie (colonna nuova `parsers.titolo`);
  lo **`slug`** è l'identità **stabile**, derivata dal titolo e univoca per utente
  (`UNIQUE (user_id, slug)`), e **non cambia** con una rinomina, così un riferimento
  allo slug non si rompe. Il `name` (PRIMARY KEY globale, eredità legacy) è generato
  `u{user_id}-{slug}`, univoco fra tutti gli utenti: due clienti possono intitolare
  «Test 1» il proprio parser senza collidere. La colonna è additiva e nullable, ma la
  migrazione **retrocompila** `titolo` dal `name` per le righe legacy (schema
  pre-`titolo`, es. il parser PIERO di default): nessun parser esistente resta con
  `titolo: null`, così il contratto `titolo: str` vale anche per chi era già a database.
- La **`config`** viene validata alla scrittura: struttura (oggetto, `match`/`columns`
  oggetti, ogni regola un oggetto), **numeri finiti** (`NaN`/`Infinity` → 422: li
  accetterebbe `request.json()` ma `JSONResponse` li rifiuta poi in lettura, dando un
  500 su ogni lista/creazione che include quel parser) **e** un dry-run di
  `esegui_parser`, così i valori storti danno un **422** con il motivo invece di un
  parser che scarta in silenzio. È la validazione che CodeRabbit ha chiesto, al confine
  giusto; il fail-safe di `elabora_messaggio` resta come seconda rete sul webhook.
- `POST …/test` è **a secco**: non scrive nel feed di nessuno. Restituisce
  `matched`/`missing`/`complete` — la base del motore diagnostico «perché non ha fatto
  il parser» — e, se completo, l'`event` e il `csv`.
- Le rotte `/api/parsers*` (admin, token del feed) **restano** invariate: le usa il
  proprietario. `/api/me/parsers*` è la faccia per-utente, additiva.

## Autenticazione

Telegram Login Widget come percorso principale: la firma HMAC-SHA256 del
data-check-string si verifica lato server con chiave `sha256(bot_token)`.
Fallback con deep-link al bot e codice a 6 cifre, per chi arriva da mobile.
La sessione è un cookie httpOnly firmato. Il token del bot resta sul server: la
web app non lo riceve e non lo conserva mai.

### Le rotte di sessione che esistono davvero

Dal PR 6, piu' quelle del PR 7. Sono le **uniche** del servizio con un'autenticazione
propria: tutte le altre sono o pubbliche, o protette da `auth()` col token unico
descritto sotto. La distinzione non è narrativa — è una delle tre categorie che
`tests/relay/test_autenticazione.py` verifica coprano **ogni** rotta dichiarata
dall'app, così una rotta nuova senza serratura fa fallire quel test invece di
passare inosservata.

| Rotta | Cosa fa | Rifiuta con |
|---|---|---|
| `POST /api/login/telegram` | firma del Login Widget → sessione | `401 login non valido` |
| | i campi si mandano **come li consegna il widget**: `id` e `auth_date` sono numeri e vengono accettati tali. La firma si calcola sulle forme **testuali JSON** (`true`, non `True`) | |
| `POST /api/login/password` | `administrator` + password → sessione | `401`, `429` se frenato, `503` se la variabile manca |
| *entrambe* | | `503` anche se manca `TELEGRAM_BOT_TOKEN`: il segreto dei cookie deriva da lì, quindi non c'è nessuna sessione da emettere |
| `POST /api/logout` | cancella il cookie | niente: è pubblica di proposito |
| `GET /api/me` | chi è l'utente della sessione | `401 sessione assente o scaduta` |
| | restituisce `stato` **effettivo** e `giorni_rimasti`: dal PR 7 la colonna `status` non basta, perché la scadenza è un istante che nessun processo riscrive | |
| `POST /api/access/request` | il cliente chiede l'accesso | `401`; `409` se ha già una richiesta aperta o l'accesso attivo; `403` se è sospeso |
| `GET /api/admin/requests` | le richieste da decidere | **`404`** a chi non è l'amministratore |
| `POST /api/admin/requests/{id}/approva` | concede `{"giorni": n}` | **`404`**; `422` sui giorni fuori da `1..3650` o sul corpo malformato |
| `POST /api/admin/requests/{id}/rifiuta` | torna `registrato`, così può richiedere | **`404`** |
| `POST /api/admin/promemoria` | avvisa chi scade entro 5 giorni | **`404`** |
| *le quattro del pannello* | | `404` e non `403`, perché un `403` conferma a un estraneo che il pannello sta lì. Per la stessa ragione corpo e `id` di percorso si leggono **a mano dopo** il controllo della sessione: lasciati a FastAPI, un estraneo riceveva `422`, cioè la stessa conferma per un'altra via — trovato dalla guardia sulle rotte, PR #26 |

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

   **È un'invariante, non un ramo**, e la differenza è tutto: «se `TELEGRAM_ADMIN_ID` è
   configurato, la riga `PIERO` porta **quel** `telegram_id`, o nessuno», qualunque cosa ci
   sia adesso. La formulazione è stata corretta il 13/08/2026: diceva «se chi fa login è
   l'ID dell'amministratore…», e quel «se» era il difetto — legava l'invariante a **chi
   entra**, quindi la vecchia identità restava collegata fino all'ingresso della nuova.
   Verificata a **ogni** login, chiunque lo faccia, l'invariante produce due comportamenti
   che i gate finali hanno preteso sulla PR #24:

   - **cambiare la variabile toglie l'accesso alla vecchia identità** sciogliendo il
     collegamento stantio — `telegram_id` azzerato, `session_version` incrementata, riga in
     `admin_audit`. Prima la revoca scattava solo all'ingresso del **nuovo** ID, e se il
     nuovo non entrava mai il vecchio restava amministratore per sempre: cambiare la
     variabile era teatro. Bloccante di GPT-5.6 Sol. Precisione richiesta da CodeRabbit sulla
     PR #24, perché «subito» non era vero e la differenza è misurabile: cambiare la variabile
     **non scrive nel database** — il servizio la legge all'avvio, e la revoca viene applicata
     **alla prima richiesta autenticata che arriva dopo il cambio**, che sia un login o una
     qualunque pagina del sito. La formulazione precedente diceva «al primo login», ed era il
     residuo che GPT-5.6 Sol ha alzato al terzo giro: applicandola solo al login, chi aveva
     **già** un cookie amministrativo lo conservava — e non scadeva, perché ogni richiesta
     valida rinnova il cookie, quindi una sessione tenuta aperta è immortale. Nel caso per cui
     la revoca esiste (nella variabile è finito l'ID di un estraneo, o l'account è compromesso)
     l'estraneo col pannello aperto non ha nessun motivo di rifare login, quindi non perdeva
     niente. Ora la sua stessa prossima richiesta chiude la sua sessione: l'invariante vive in
     `revoca_identita_stantia()`, chiamata dal login **e** da `utente_dalla_sessione()` —
     una funzione sola, perché due copie divergono (regola 3). La scrittura sul percorso di
     lettura non si ripete, perché dopo lo scioglimento `telegram_id` è `NULL`, e non si
     duplica in corsa, perché l'`UPDATE` porta il valore stantio nella `WHERE`: entrambe le
     proprietà sono misurate, la seconda con un interleaving imposto a mano dopo aver
     constatato che sei thread non la producono. Il feed non è toccato: `/xtrader.csv` non ha
     sessione;
   - **se un altro account possiede già quell'ID, il login è rifiutato con `409` finché il
     proprietario non autorizza l'assorbimento** con `TELEGRAM_ADMIN_RECONCILE`, il cui valore
     è l'**identificativo della riga** da assorbire — non un `1`. Il motivo
     è una constatazione, non una cautela: «la riga è vuota» distingue un account pieno da uno
     vuoto, **non un cliente da una riga nata per errore**. Un cliente appena registrato è
     vuoto anche lui, quindi le due situazioni sono due righe di `users` con un'identità
     Telegram e nient'altro — indistinguibili. Assorbire d'ufficio significava che un refuso
     nella variabile, con dentro l'ID di un cliente, gli svuotava la riga e portava la sua
     identità sull'account del proprietario con `is_admin=1`: **il cliente entrava nella
     dashboard del proprietario**. Misurato. Quando nessun dato distingue, il solo marcatore
     affidabile è il consenso di chi sa, e in sua assenza si fallisce chiusi. Bloccante di
     GPT-5.6 Sol sulla PR #24. Il baratto è dichiarato: la riparazione resta possibile ma
     **deliberata** — il proprietario legge il `409` (log + `admin_audit`), imposta la
     variabile col numero che il log gli indica, rifà login. Alternativa scartata: rifiutare
     sempre, che riporterebbe al lockout irreversibile che questa PR chiude.
     Il valore è legato alla riga e non è un interruttore globale, per un rischio alzato da
     GPT-5.5: un `1` che la documentazione dice di togliere dopo l'uso è un `1` che resta, e da
     quel momento un refuso futuro verso un'altra riga vuota verrebbe assorbito di nuovo — il
     fail-closed sparirebbe in silenzio. Legato alla riga, un consenso dimenticato è innocuo, e
     per una proprietà del codice e non per prudenza: la riga assorbita **non viene
     cancellata**, quindi il suo identificativo non è mai riusato da un utente nuovo;
   - **se la variabile punta a un account che possiede parser o chat, il login è rifiutato**
     con `409` **anche col consenso** — che dice «quella riga vuota è mia», non «prenditi i
     dati di un altro utente». L'account bersaglio resta **intatto**, nessun dato viene travasato e nessun
     `telegram_id` viene spostato. Resta possibile che il collegamento stantio del
     proprietario sia già stato sciolto nello stesso login, perché quello è il punto
     precedente e riguarda un'altra riga (precisione richiesta da CodeRabbit sulla PR #24).
     Prima quell'account veniva assorbito: bastava
     sbagliare una cifra e metterci l'ID di un cliente, e al suo login i suoi parser e le
     sue chat passavano al proprietario, il suo `telegram_id` veniva azzerato e lui otteneva
     `is_admin=1` — **perdeva tutto e entrava nell'account di un altro**, senza un errore da
     nessuna parte. È la violazione dell'isolamento fra utenti, la priorità 7. Bloccante di
     Claude Fable 5. Segnali e log **non** contano come possesso: sono tracce, e seguono
     l'utente. Il criterio è in `possiede_qualcosa()`.

   Tutto il blocco gira dentro un `BEGIN IMMEDIATE`: senza, fra la `SELECT` e le `UPDATE`
   entra un altro login, e due login concorrenti che cambiano identità incrementano
   `session_version` due volte — misurato, **un cookie su sei nasce morto**, il login
   «riesce» e la richiesta dopo risponde `401`. Alzato da Fable 5 e Sol indipendentemente. Quindi il collegamento è **idempotente** e l'ordine fra impostare la
   variabile e fare il login **non conta**: il login successivo ripara quello precedente — con
   il consenso `TELEGRAM_ADMIN_RECONCILE` quando la riparazione deve assorbire un'altra riga,
   perché quella riga potrebbe essere di un cliente (vedi il punto sopra). Senza consenso il
   login non fallisce in silenzio e non consuma niente: risponde `409` e dice cosa fare.
   Autorizzata, `riconcilia_su_utente()` le travasa tutto —
   riusando `_trasferisci_parser` e `RIFERIMENTI_DATI_UTENTE`, che è *derivato* da
   `RIFERIMENTI_UTENTE` della migrazione e non ricopiato (regola 3): una colonna nuova entra
   dall'elenco vincolato dal test dello schema e arriva qui da sé. La differenza fra i due
   elenchi sono le due colonne di `admin_audit`, che la riparazione **non** riscrive: le altre
   sono dati dell'utente e seguono i dati, quelle sono storia, e una riga
   `collegamento_admin_rifiutato` riscritta diventerebbe un rifiuto del proprietario contro se
   stesso — inutile proprio dove serve, perché `admin_audit` è l'unico posto in cui il
   proprietario legge perché un login è stato rifiutato (segnalato da CodeRabbit sulla PR #24).
   La migrazione invece deve riscriverle, perché là la riga perdente perde `origin_profile` e i
   suoi riferimenti resterebbero orfani. Poi la riparazione le azzera il `telegram_id`, che è
   UNIQUE, e scrive una riga in
   `admin_audit`: una riparazione silenziosa sarebbe indistinguibile da
   un'appropriazione di account. La riga perdente **non** viene cancellata, perché un
   `NULL` è reversibile e una `DELETE` no, e le sue sessioni muoiono con un incremento di
   `session_version`.

   **E cambiare `TELEGRAM_ADMIN_ID` revoca le sessioni dell'identità precedente.** Il cookie
   è legato all'`id` della riga e a `session_version`, non al `telegram_id`: senza
   l'incremento, chi era entrato con l'identità vecchia conserverebbe **accesso
   amministrativo** sulla riga che possiede i parser — e non scadrebbe, perché `GET /api/me`
   rinnova il cookie a ogni richiesta valida, quindi una sessione tenuta attiva è immortale.
   Il caso non è ipotetico: se in quella variabile fosse finito l'ID sbagliato — un estraneo,
   o un account compromesso — correggerla non gli toglierebbe il pannello. La revoca la
   provoca il **cambio di valore**, non un login qualunque: è applicata al primo login
   successivo al cambio e non a ogni login, altrimenti entrare dal computer chiuderebbe la
   sessione sul telefono. Bloccante di GPT-5.6 Sol sulla PR #24.

   **Due cose che invece NON revocano, e sono deliberate.** *Svuotare* la variabile non
   scioglie niente: vuota significa «nessuna invariante dichiarata», non «revoca», e
   scioglierla lascerebbe la riga `PIERO` senza `telegram_id` senza poterla ricollegare — al
   login successivo nascerebbe un secondo account, cioè lo stesso lockout del punto qui
   sotto. Il gesto per togliere l'accesso a un'identità è **cambiare** il valore, non
   cancellarlo (chiesto da GPT-5.6 Sol sulla PR #24, e la risposta è misurata in
   `test_SVUOTARE_la_variabile_non_scioglie_il_collegamento`). Un valore **malformato** —
   virgolette prese incollando nel pannello, spazi interni, cifre non ASCII, zero iniziale —
   viene trattato come *non configurato*: `admin_id_malformato()` lo riconosce e il login
   **non applica l'invariante**. Prima la applicava sul valore grezzo, e il risultato era il
   peggiore possibile: il collegamento buono veniva sciolto, `CASO 2` non poteva ricrearlo
   perché confronta `data.id` con lo stesso valore malformato, e al proprietario nasceva un
   account vuoto — **un refuso nel pannello Railway lo chiudeva fuori dal proprio account**,
   col solo avviso di una riga di log all'avvio. Trovato indipendentemente da GPT-5.6 Sol e da
   CodeRabbit sulla PR #24.

   *Fino al 12/08/2026 il collegamento viveva dentro `if riga is None`, quindi valeva solo
   al primo login.* Un login fatto prima che la variabile fosse **arrivata nel processo** —
   stato che su Railway si produce da sé quando un build fallisce dopo un cambio di
   variabile — creava un account vuoto con quel `telegram_id`, e da lì ogni login successivo
   prendeva il ramo `else`: la riga `PIERO` non veniva collegata mai più. Irreversibile per
   il proprietario, e senza nessun errore: solo una dashboard vuota. La riconciliazione della
   migrazione non aiutava, perché raggruppa per `origin_profile` e quella riga ha
   `origin_profile` NULL.
2. **Password** (`ADMIN_PASSWORD_HASH`), utente fisso `administrator`. Esiste perché
   con il solo login Telegram un guasto di Telegram, o la perdita di quell'account,
   chiuderebbero il proprietario fuori dal proprio pannello.

Nella variabile va **l'hash**, non la password. La dashboard di Railway è leggibile da
chi ha accesso al progetto, e con la password in chiaro chi la legge entra nel pannello
— da cui si cancellano parser e si inietta un segnale nel feed che XTrader legge. Si
cambia password cambiando la variabile; il comando che genera l'hash è in `README.txt`.

Il percorso a password ha un **freno**: cinque tentativi falliti e si chiude per cinque
minuti, con `429` e non `401`, perché chi legge deve sapere che il muro è il freno e non
la password. Il tentativo si **conta prima** della verifica, dentro lo stesso lock che
controlla il freno, e si azzera solo in caso di successo — «consuma un gettone prima di
lavorare». Erano due gesti separati con `scrypt` in mezzo, quindi dodici richieste
concorrenti passavano tutte: il limite si aggirava mandandole insieme, e ogni richiesta
accendeva uno `scrypt`, cioè il freno amplificava il carico invece di ridurlo. Segnalato
da GPT-5.6 Sol sulla PR #23. Il freno è **globale e non per IP**, con un baratto dichiarato: per IP non
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
  non «di sessione» perché **ogni rotta che valida la sessione riemette il cookie** con
  un `emessa` nuovo — oggi `GET /api/me`, e ogni rotta autenticata futura deve fare lo
  stesso. **È verificato, non raccomandato:**
  `test_ogni_rotta_che_usa_la_SESSIONE_rinnova_anche_il_cookie` elenca le rotte il cui
  codice legge `utente_dalla_sessione` e pretende che riemettano il cookie — una rotta
  nuova che se ne dimentica fa fallire quel test invece di funzionare benissimo e far
  scadere la sessione di chi la usa. Il rischio è stato alzato da Fable 5 e GPT-5.5 sulla
  PR #23. Il rinnovo va **dopo** la validazione: prima, un cookie scaduto tornerebbe buono
  al primo tentativo e la scadenza si annullerebbe da sé. Ed è **per-rotta e non un
  middleware**, di proposito: un middleware girerebbe anche su `/xtrader.csv`, cioè
  metterebbe codice di sessione sul percorso del feed — esattamente la NON-relazione
  descritta sotto. *Fino al 12/08/2026 il rinnovo non esisteva e questa riga diceva
  comunque «di inattività»: la scadenza era assoluta dal login, quindi il proprietario si
  sarebbe trovato buttato fuori ogni venti minuti mentre lavorava. Segnalato
  indipendentemente da GPT-5.5, Claude Fable 5 e CodeRabbit sulla PR #23 — tre reviewer
  sullo stesso punto, perché la promessa era scritta qui e in `README.txt` e il codice non
  la manteneva;*
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
| Fatto | **Accesso su approvazione** (PR 7), lato server: `stato_effettivo` / `giorni_rimasti` / `nuova_scadenza` come fonte unica, la richiesta del cliente col deep link del bot, la decisione del proprietario con i giorni liberi e l'errore di invio **non ingoiato**, il promemoria a 5 giorni una volta per scadenza, e gli effetti della scadenza su feed e webhook. Il **token non viene revocato** alla scadenza |
| M3 | La web app collegata al backend: le schermate di questo flusso (richiesta, giorni rimasti, accesso scaduto) non esistono ancora — il prototipo è sui dati finti |
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
