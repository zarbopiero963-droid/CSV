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
  user_id, slug, config_json, titolo, active, ordine, created_at, id,
  versione, uid
  unique (user_id, slug), unique (id), unique (uid)
                           tutti e tre come indice: su una tabella esistente
                           UNIQUE non si aggiunge con ALTER, e un PRIMARY KEY
                           nemmeno.
                           `id` è riempito dal `rowid`, ed è quello che
                           `parser_chats` riferisce.
                           `titolo` è il nome che il cliente sceglie (`name`
                           resta l'identità interna, unica fra TUTTI gli utenti).
                           `versione` è la precondizione della PUT (#51).
                           `uid` è l'identità non riusabile di PUT e DELETE
                           (#73) e la loro seconda precondizione dal client
                           (#75), e **non** viene dal `rowid`: proprio perché
                           sqlite il `rowid` lo RIUSA, e quindi `id` non
                           identifica una riga nel tempo. È un valore casuale a
                           128 bit, ed è nullable perché `ALTER ADD COLUMN NOT
                           NULL` esigerebbe un default costante — cioè lo stesso
                           «identificatore» per tutte le righe. A riempirlo sono
                           la migrazione e i percorsi di creazione; che nessuno
                           lo dimentichi è vincolato da un test

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
  le righe vive del parser (dal #35: N per messaggio). `profile` continua a governare il feed: il
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
  dal dispatch multi-parser (PR 2 di #2) è la SORGENTE dei link di parser_chats,
  e il fallback del webhook per le chat/profili che i link non rappresentano.
  Dalla PR 4 i link si riconciliano a ogni POST/DELETE del profilo, non più a
  ogni migrazione

migrazioni         ← le conversioni da fare UNA VOLTA SOLA per database
  nome (primary key), created_at
  nasce con la rimozione del seme (PR 4): il travaso dei link dai profili
  legacy vi scrive `link_dai_profili` e non si ripete più. Ripetuta a ogni
  avvio, una conversione è indistinguibile da un seme — rimette in piedi ciò
  che il proprietario ha cancellato
```

`webhook_seen` e `message_logs` sono **vive dal dispatch multi-parser** (PR 2 di
#2): la prima fa il dedup degli `update_id` — il marker si scrive **nella stessa
transazione del segnale**, vedi «Il dispatch multi-parser» — la seconda riceve
gli esiti («segnale scritto», «riconosciuto, sostituito da …»), entrambe con
pulizia a 7 giorni sulla scrittura. `feed_reads` **esiste ma nessun codice la
legge ancora**: il conteggio delle letture è di un PR successivo. Le tabelle
sono nate insieme perché aggiungerle dopo avrebbe voluto dire una seconda
migrazione su un database con dati dei clienti.

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
  una riga di `users` possiede chat, parser, segnali, la libreria mercati (#33) e le
  sorgenti squadre (#34), e cancellarla perderebbe dati di
  un cliente. Ma azzerare l'etichetta **non basta**: ciò che la riga perdente possiede
  — chat, segnali, parser, sport/mercati/selezioni, sorgenti/competizioni/alias — viene
  **trasferito al superstite** prima di togliergliela,
  altrimenti quei dati restano su un utente che non risulta più quel profilo, cioè
  nessuno li rivendica e per il codice multiutente sono di un altro. La stessa lista
  vincola `possiede_qualcosa` (`TABELLE_POSSEDUTE`): il guard che, sul login
  dell'amministratore, rifiuta di **assorbire** una riga che possiede dati invece di una
  vuota. Dall'audit #81 (I1) conta anche libreria e sorgenti, non più solo parser e chat:
  un cliente che si costruisce prima la libreria — l'ordine naturale, il wizard la consuma —
  non deve essere scambiato per una riga vuota e travasato sull'account del proprietario.
  Nel trasferimento
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
2. Ogni endpoint che riceve un `parser_id` (o uno slug) verifica la proprietà
   prima di leggere o scrivere. Un parser di un altro utente risponde 404, non
   403: non si rivela nemmeno l'esistenza. **Vale per la risorsa singola** —
   `PUT`, `DELETE`, `POST …/test`, che nominano un parser preciso. Una rotta di
   **elenco** non può rispondere 404 perché non nomina niente: filtra per
   `user_id` e restituisce 200 con la lista dei soli parser propri, vuota
   compresa. Le due forme non si contraddicono, e la regola 8 dice la stessa
   cosa per tutte le altre tabelle (segnalato da CodeRabbit sulla PR #72, dove
   questa riga sembrava promettere 404 anche per gli elenchi).
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
   Dal #35 (pezzo 1) il feed può portare **N righe vive** prodotte dallo stesso
   messaggio, composte da `componi_feed` con BOM e intestazione unici in testa;
   il TTL è per riga nel filtro di lettura e `store_signal` verifica **tutti**
   i documenti prima di scrivere (uno rotto = niente scritto).
7. Stesso messaggio, stessa chat, parser diversi: due elaborazioni indipendenti e
   due CSV distinti. Chi non riconosce il messaggio lo ignora senza toccare nulla.
8. **La proprietà si ripete DENTRO ogni statement** (PR #64, estesa dalla #65 a
   parser, mercati e letture). Il controllo di proprietà delle rotte (`*_o_404`)
   è una lettura, e può invecchiare prima del write-lock: una riconciliazione
   concorrente (`riconcilia_su_utente`) travasa il padre fra il check e lo
   statement, e uno statement che filtra solo per id atterrerebbe sui dati ormai
   dell'account superstite. Perciò ogni INSERT/UPDATE/DELETE **e ogni SELECT dei
   dati** ripete il vincolo `user_id` nella stessa istruzione (JOIN/EXISTS fino a
   `sports.user_id`/`competizioni.user_id`/`parsers.user_id`). L'esito per il
   proprietario sbagliato **non è lo stesso sui due lati**, e la differenza è
   deliberata:

   - **scritture** (INSERT/UPDATE/DELETE): zero righe toccate, `None` al
     chiamante, **404** dalla rotta — come se il travaso fosse arrivato prima
     della richiesta;
   - **letture** (le SELECT dei dati): **lista vuota**, e la rotta risponde
     **200** con quella lista. Non un 404: una lista vuota è già il risultato
     legittimo di un padre che non ha figli, e distinguere «vuoto perché non è
     più mio» da «vuoto perché non c'è niente» richiederebbe una seconda
     lettura, che avrebbe la stessa finestra della prima. Quel che conta — ed è
     ciò che il vincolo garantisce — è che **nessun dato di un altro account
     compaia mai** nella risposta. (Segnalato da Claude Fable 5 al gate finale
     della PR #72: la versione precedente di questa regola diceva «404 dalla
     rotta» per tutti e due i lati, ed era il testo a essere sbagliato, non il
     codice.)

   Portata: il travaso avviene solo fra account della **stessa
   persona** (merge/riparazione) — è difesa in profondità, non una falla
   cross-persona. Restano fuori, per decisione da prendere dal proprietario, gli
   INSERT per-utente legati all'id di sessione (`crea_sport_mio`,
   `crea_sorgente_mia`, `crea_parser`, conio token): vincolarli a
   `users.session_version` dentro lo statement è un cambio architetturale
   (issue #65, terzo punto).

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

**Implementato: il feed e il token vivono sull'UTENTE**, non sul parser. Le
versioni precedenti di questo documento descrivevano un feed per parser: era il
disegno del prototipo, ed era sbagliato rispetto alla decisione della Issue #2 —
un utente ha **un** feed, `GET /feed/{slug}.csv?token=xt_…`, e i suoi parser si
contendono la riga viva di quel feed.

- Coniato da `POST /api/me/token` (sessione): `xt_` + 24 byte da CSPRNG
  (`secrets.token_urlsafe`), sopra il minimo di 18 della Issue #2.
- Sul server si conserva **solo** `sha256(token)` (`hash_token_feed` in
  `main.py`). I token hanno entropia alta: non serve un KDF lento, serve non
  salvarli in chiaro.
- Il token in chiaro esiste in una sola risposta HTTP, quella della
  generazione. `/api/me` restituisce `slug` e `token_prefix`, mai il token.
- `token_prefix` = primi 9 caratteri (`xt_` + 6), per riconoscerlo nella UI.
- Rigenerare sovrascrive l'hash: il precedente smette di aprire il feed alla
  richiesta successiva. Non esiste un «disarmo» separato: rigenerare è la revoca.
- Sul feed per utente ogni fallimento è **404 uniforme** — slug inesistente,
  token assente, sbagliato o di un altro utente: un 401 su uno slug esistente
  direbbe a chi enumera «questo cliente esiste».
- Alla **scadenza dell'accesso** il feed risponde `200` con sola intestazione e
  il token **non** viene revocato: «scaduto» e «revocato» sono stati diversi, e
  al rinnovo il cliente non deve riconfigurare XTrader.
- Chi nasce dal login Telegram non ha uno slug: glielo assegna il primo
  `POST /api/me/token`, con la stessa `_slug_libero` deterministica della
  migrazione.
- Il token globale `CSV_ACCESS_TOKEN` protegge ancora gli alias legacy
  (`/xtrader.csv`, `/profiles/{p}.csv`) ed è già circolato: va ruotato prima
  dell'uso commerciale.

**La chiave del segnale è l'utente.** `store_signal` risolve l'utente dal
profilo (`origin_profile`), scrive `signals.user_id` e sostituisce la riga viva
coprendo **entrambe** le chiavi (`user_id` e `profile`): una riga sola, leggibile
sia dal percorso nuovo (per `user_id`) sia dagli alias legacy (per `profile`).
È il prerequisito del dispatch multi-parser: due parser dello stesso utente si
contendono una riga senza toccare quelle altrui.

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
    "Price":     { "source": "regex", "pattern": "@\\s*([0-9.,]+)", "group": 1 }
  }
}
```

Sorgenti supportate: `empty`, `constant`, `message`, `line` (con `part` `whole` o
`after`), `regex`. Trasformazioni: `trim`, `replace_last`, `replace_all`, `upper`,
`lower`, `comma_to_dot`, `dot_to_comma`, `digits_only`. Sul separatore decimale
le trasformazioni non hanno piu' l'ultima parola: il **confine di scrittura**
(#40, sotto) localizza comunque i valori numerici accettati, quindi
`comma_to_dot` resta legale ma superfluo su quelle colonne — il suggeritore ha
smesso di proporlo su `Price`.

**I flag della sorgente `regex`** (campo opzionale `flags`) sono onorati solo per
l'insieme che i due motori — `web/engine.js` in anteprima e `main.py` in
produzione — trattano **identico**: `i`, `m`, `s`, piu' `u` (unicode). `x`
(verbose), `y` (sticky) e `g` (globale) sono **ignorati**. È il fix E2 dell'audit
#81: `new RegExp(_, 'x')` in JavaScript solleva mentre `regex.X` in Python
funziona, e `y` è sticky solo in JS — un flag onorato da un lato solo faceva
combaciare l'anteprima e il feed in modo diverso.

Il default `i` (case-insensitive) si applica **solo ai flag assenti** (`flags`
mancante o vuoto), come lo storico `rule.flags || 'i'` — così tutti i parser del
wizard, che non emettono `flags`, restano invariati. Un `flags` **presente** ma
con soli valori scartati (`'x'`, `'gy'`) tiene zero flag, cioè resta
**case-sensitive**, e non ricade su `i`: un parser già salvato con quei flag non
cambia i propri valori nel feed, perde solo il verbose/sticky (che comunque
divergevano fra i motori).

Un `flags` **malformato** (non stringa: numero, lista, oggetto, booleano — il
`config_json` è dato utente non attendibile) rende la regola `regex`
**malformata**: la colonna resta **vuota** (fail-closed), come per un pattern che
non compila, identico nei due motori. Su una colonna obbligatoria il segnale non
esce — nessun crash e nessun segnale prodotto «per default» da una config non
valida — e senza dipendere dalla forma della coercizione a stringa, che
divergerebbe fra `String()` di JS e `str()` di Python.

**Al salvataggio** (`_valida_config_parser`, Issue #86) un `flags` fuori da
`{i,m,s,u}` — un `x`/`y`/`g`, o un valore non-stringa — viene **rifiutato con un
422** e un motivo chiaro, così nessun nuovo parser può nascere con flag che
degraderebbero a runtime. La lista `{i,m,s,u}` è fonte unica (`FLAG_REGEX_COMUNI`
in `main.py`, riusata da `_flag_regex` e dalla validazione). Le config già
salvate restano gestite a runtime come sopra. E quando un segnale del motore cade
per una **colonna obbligatoria vuota**, il motivo nomina ora quella colonna in
`message_logs` (come già fa il percorso legacy dal #84), invece del generico
`parser_no_match`: la perdita non è mai silenziosa.

La **condizione** `match` di tipo `regex` **non** legge `flags`: usa `i` cablato
in entrambi i motori (`condizione_soddisfatta` in `main.py`, `matches` in
`web/engine.js`). Un `match.flags` è quindi un campo inerte, ignorato in modo
identico dai due lati — nessuna divergenza possibile.

> **Caveat — costrutti regex che divergono fra i due motori (Issue #88).**
> Oltre ai *flag*, alcuni **costrutti del linguaggio** regex si comportano in modo
> diverso fra il modulo `regex` di Python (produzione) e `RegExp` di JavaScript
> (anteprima), **a prescindere dai flag**:
>
> | Costrutto | Python `regex` | JS `RegExp` |
> |---|---|---|
> | `\w`, `\d`, `\b` | unicode-aware (`café`, cifre arabo-indiane…) | **ASCII** (anche con `u`) |
> | `.` su un carattere astrale | un codepoint | un'unità UTF-16 (surrogato) senza `u` |
>
> Esempio misurato: `(\w+)` su `café` estrae `café` nel feed (Python) e `caf`
> nell'anteprima (JS). È una differenza **pre-esistente** dei due motori, non dei
> flag né delle Issue #85/#86. Per i valori con **testo non-ASCII** (nomi con
> accenti) conviene usare **classi esplicite** — `[A-Za-zÀ-ÿ0-9]` — o `\p{L}`/`\p{N}`
> con `flags:'u'` (che allinea i due motori sulle proprietà unicode). Nei mercati
> ed eventi Betfair il testo è quasi sempre ASCII, quindi il caso è raro.
>
> **Al salvataggio** (`_valida_config_parser`) i costrutti **più comuni** che
> divergono — `\w`, `\W`, `\d`, `\D`, `\b`, `\B` — sono ora **rifiutati con un 422**
> in ogni regola `regex`: sia nelle colonne `source: regex`, sia nella condizione
> `match` di tipo `regex` (che compila il pattern come le colonne). Il motivo
> nomina il costrutto e l'equivalente esplicito (`\d` → `[0-9]`, `\w` →
> `[A-Za-z0-9_]`), così un **nuovo** parser non può nascere con **quei** costrutti,
> che sono quelli che un utente scrive per sbaglio pensando all'ASCII. È la stessa
> scelta dei flag esotici (#86), estesa al linguaggio del pattern; opzione B del
> proprietario al gate finale della #89. `.` **non** è rifiutato (è allineato da
> `u` e pinnato dal contratto in `test_engine_contract.py`), e nemmeno `\s`/`\S`
> (i due motori concordano in pratica sugli spazi tipici). Il giudice è fonte
> unica: `_costrutto_regex_divergente` in `main.py`, che conta i backslash per non
> confondere la classe `\d` con un backslash letterale `\\d`. Le config **già
> salvate** restano gestite a runtime (grandfathering): il rifiuto è solo al
> confine di scrittura, `_estrai_valore` e `condizione_soddisfatta` non cambiano.
>
> **Anche le classi POSIX e le proprietà unicode sono rifiutate** (estensione #90):
> `[[:alpha:]]` (che JS legge come una char-class letterale) diverge **sempre** ed
> è rifiutata ovunque; `\p{L}`/`\p{N}`/`\P{…}` divergono **senza** `u` (in JS un `\p`
> senza `u` è la lettera `p`) e si allineano con `u` **per le categorie generali**
> (`\p{L}`, `\p{N}`, `\p{Lu}`, `\p{Nd}`…) — perciò `\p{}` è rifiutato quando manca
> `u`, ma **accettato** su una colonna `source: regex` con `flags:'u'`, che è la
> forma consigliata per il testo non-ASCII. Nella condizione `match`, che
> non legge i flag, `\p{}` non può mai avere `u` e quindi è **sempre** rifiutato.
> **Attenzione al dialetto** (residuo noto): l'accettazione con `u` fida che le due
> sintassi coincidano, ma i **nomi di script** no — `\p{Latin}` è valido in Python
> e un errore in JS (che pretende `\p{Script=Latin}`). Misurato: `\p{Latin}+`+u su
> `café` dà `café` nel feed e `''` in anteprima. Non è intercettato: attieniti alle
> **categorie generali** o alle **classi esplicite** per il testo non-ASCII.
> La forma **breve** senza graffe `\pL` è rifiutata **sempre**, anche con `u`: in JS
> con `u` è un errore di sintassi (le graffe sono obbligatorie) e senza `u` è la
> lettera `p` — non si allinea mai; va usata `\p{L}`+`u`.
> Misurato: `([[:alpha:]]+)` su `abc123` dà `abc` nel feed e `''` in anteprima;
> match `\p{L}+` su `café` è `True` in Python e `false` in JS.
>
> **Il gate resta non esaustivo**, ed è onesto dirlo: la coda dei costrutti che
> differiscono fra i due motori non finisce — i nomi di script in `\p{…}` (sopra),
> `.` su un carattere astrale **senza** `u` (allineato con `u`, e il lato divergente
> è pinnato dal contratto in `test_engine_contract.py`), `\h`, `\R`, `\X`, i
> quantificatori possessivi, i gruppi atomici restano fuori. Una blocklist non può
> essere completa — quattro giri di review su questa PR lo hanno dimostrato: ogni
> costrutto chiuso ne scopre uno più profondo. Il gate cattura i casi che un utente
> scrive davvero (`\w`/`\d`/`\b`, POSIX, `\p{}` senza `u`); per il testo non-ASCII
> la via robusta resta la **classe esplicita**. Inoltre `[\b]`
> (backspace, ASCII, allineato) è rifiutato in modo **conservativo** insieme al
> `\b` confine-di-parola: il gate preferisce un raro falso positivo su un carattere
> patologico a un buco su un pattern reale.
> **Il perimetro di questo consiglio è l'estrazione**: `flags:'u'` ha effetto
> solo sulle regole di colonna `source: regex`, l'unico percorso che compila
> davvero i `flags` di una regola. **Sulla condizione `match` non serve e non fa
> nulla**: come detto sopra, `match` cabla `i` e ignora `flags`, quindi per
> allineare un `match` su testo non-ASCII vanno usate le **classi esplicite**,
> non un flag che quel percorso non legge. (Le trasformazioni `replace_all` e
> `replace_last` non usano regex: sono sostituzioni letterali, i `flags` non le
> toccano.)

Allo stesso modo `replace_all` con `from` vuoto è un **no-op** in entrambi (E1),
non l'esplosione carattere-per-carattere del vecchio `split('')`. Il confronto in
`tests/engine/engine_cases.mjs` tiene i due motori allineati.

### Il multi-riga: base + override (#35, pezzo 2)

Un messaggio può generare **N righe** dallo stesso parser. La riga **base** — le
14 regole di `columns` — è il modello; `config.multi` elenca le righe di
override, ciascuna delle quali dice **solo cosa cambia** e per il resto eredita
dalla base (campo vuoto o assente = eredita, mai «azzera»):

```json
{
  "multi": {
    "markets":    [ { "market_type": "OVER_UNDER_25", "selection_name": "Over 2,5",
                      "price": "1.20" } ],
    "selections": [ { "selection_name": "Under 1,5", "bet_type": "BANCA" } ]
  }
}
```

Regole, tutte vincolate dai casi in `tests/engine/engine_cases.mjs` e dalla
parità col gemello Python:

- **Somma, non prodotto**: le righe generate sono i mercati attivi più le
  selezioni attive. Con `multi` assente o senza righe attive la lista è la sola
  base — il comportamento storico, byte per byte.
- Campi di una riga: `market_type`, `market_name`, `selection_name`, `price`,
  `min_price`, `max_price`, `bet_type`, `handicap`, `points`, `start_after`,
  `end_before`, `enabled`. Le righe di `selections` **ignorano**
  `market_type`/`market_name`: restano sul mercato della base — per le
  combinazioni si elencano righe in `markets`.
- `enabled: false` resta salvata e **non genera** la riga. Una voce è una riga
  solo se è un **oggetto non vuoto** (`rigaMulti`/`_riga_multi`): `{}` non
  genera un clone della base — `{}` è falsy in Python e truthy in JS, e senza
  il predicato comune i due motori divergevano (misurato sulla PR #69: 2
  righe in JS, 1 in Python, dalla stessa config). Un `markets`/`selections`
  **non-lista** non genera righe, in entrambi i motori: prima JS sollevava
  sul `for..of` e Python iterava le chiavi — due esiti diversi dalla stessa
  config (CodeRabbit, PR #69).
- Ogni riga è **giudicata da sola** (`giudicaRiga`/`_giudica_riga`, la stessa
  fonte unica della base): una riga rotta non ferma le altre — il segnale esce
  con le k buone su N, e gli scarti delle altre restano visibili (sotto).
- `start_after`/`end_before` **con** `selection_name`: estraggono dal segmento
  del messaggio la **quota** della riga. Con `selection_name` **vuota**:
  estraggono i **punteggi** (`N-N`, una riga per punteggio trovato), ammesso
  **solo** su `CORRECT_SCORE`/`HALF_TIME_SCORE` — altrove è un errore di
  config segnalato come scarto della riga, non una riga. I punteggi estratti
  hanno un **tetto per riga** (`MAX_PUNTEGGI_RIGA`, 36 = 0-0..5-5): oltre non
  è un mercato ma delimitatori che prendono mezzo messaggio, e la riga è un
  errore di config segnalato, **non troncato in silenzio** — senza tetto un
  messaggio pieno di `N-N` per 20 righe genererebbe migliaia di documenti
  nello storage condiviso (bloccante di Claude Fable 5 sulla PR #69).
- Il **gate di contenuto (#41) vale per riga**: le colonne sovrascritte dalla
  riga sono costanti e non contano come estratte. Base tutta costante + una
  riga di override resta scartata — senza questa regola `multi` sarebbe la
  porta sul retro del gate. Le righe dei punteggi sono esenti: il punteggio
  viene dal messaggio per costruzione.
- **Confine di scrittura** (`_valida_config_multi`): forma sbagliata, chiave
  col refuso (con suggerimento — sia nelle righe sia al livello di `multi`,
  dove `markes` passerebbe muto), `enabled` non booleano o valore non scalare
  danno **422** al salvataggio; il tetto `MAX_RIGHE_MULTI` (default 20,
  regolabile da variabile) conta tutte le righe dichiarate, anche le spente.

Sul percorso vivo: `esito_messaggio` prende le righe **generate** come
autorità — il segnale c'è se almeno una è piazzabile; `csv` è la stringa di
sempre con una riga sola, la **lista** dei documenti con più righe (il
contratto d'ingresso di `store_signal`, #35 pezzo 1). Gli scarti delle righe
non piazzabili di un segnale scritto viaggiano negli `avvisi` come
`riga N: …` e finiscono in `message_logs` — e una riga caduta per `missing`
(che scarti non produce) lascia `riga N: colonne obbligatorie mancanti: …`,
così nessuna riga sparisce muta; se **nessuna** riga è piazzabile i
motivi arrivano al dispatch con lo stesso prefisso (senza prefisso nel caso
storico della sola base, dove il testo dei log non si muove). La rotta di
prova risponde `righe` (per ciascuna: `row`, `missing`, `scarti`, `complete`)
con `complete` vero se almeno una è piazzabile, e `csv` **composto** delle
sole complete — `componiFeed` in JS e `componi_feed` in Python sono lo stesso
contratto, byte per byte (`tests/engine/test_engine_contract.py`).

### Guardie sui valori estratti e sulla config (#39 + #41)

Il motore controlla il **senso** di alcuni valori, non solo la loro presenza:
`verify_csv()` guarda il formato (14 colonne, virgolette, CRLF, BOM) e non può
accorgersi di una quota da un milione. Fino alla PR 5 in `main.py` non esisteva
nessun `float()`: qualunque cosa la regola estraesse finiva verbatim nel CSV.

**Le cinque colonne numeriche e i loro intervalli** (`INTERVALLI_NUMERICI` in
`main.py`, `NUMERIC_RANGES` in `web/engine.js`):

| Colonna | Intervallo | Perché |
|---|---|---|
| `Price` · `MinPrice` · `MaxPrice` | `1.01 – 1000` | è la scala reale delle quote Betfair, non una convenzione: fuori da lì non c'è informazione da salvare |
| `Handicap` | `−1000 – +1000` | inviluppo volutamente largo: deve coprire ogni linea reale e intercettare solo il patologico |
| `Points` | `0 – 1000` | è il **moltiplicatore** dello stake di XTrader: il tetto non chiede «è troppo?» — quanto punta il cliente non ci riguarda — ma «può averlo scritto una persona?» |

**L'ordine dei controlli è parte del contratto**, non un dettaglio
implementativo:

```
vuoto            → ammesso   (è il caso normale di Price: la quota la mette XTrader)
cifre non ASCII  → scarta    (float() leggerebbe ١٩, XTrader no)
non convertibile → scarta    (con più separatori il motivo nomina le migliaia)
non finito       → scarta    (inf supera i confronti nel verso sbagliato)
fuori intervallo → scarta    (nomina il tetto e il separatore decimale)
```

**Il verdetto corre sul valore normalizzato, non sul testo grezzo.** Prima di
ogni controllo il valore passa dalla classe condivisa degli spazi uniformi
(`_piatto` in `main.py`, `piatto` in `web/engine.js`): gli spazi ai bordi —
inclusi BOM e separatori di controllo — vengono perdonati, quelli **dentro** il
numero no. I default dei due linguaggi divergono proprio lì (`strip()` non
toglie `\ufeff`, `trim()` non toglie `\x1c-\x1f`/`\x85`), e un verdetto preso
sul testo grezzo dava anteprima «completa» nel browser e feed vuoto in
produzione. La stessa classe governa l'**emptiness** delle colonne obbligatorie
e la trasformazione **`trim`**, che tocca il valore estratto e quindi i byte
della riga CSV. [REAL_FINDING] dei gate finali sulla PR #47.

**Nessuno scarto senza riconoscimento.** `scarti` viene calcolato solo se la
condizione del parser è soddisfatta (`matched`): un parser mai riconosciuto ma
con una costante numerica invalida produrrebbe motivi per qualunque messaggio
della chat, e il dispatch li archivierebbe in `message_logs` come «scartato»
sotto un parser che non c'entra, conservando testo estraneo. Non riconosciuto =
`parser_no_match`, senza riga di log. [REAL_FINDING] di GPT-5.6 Sol, PR #47.

**Si scarta il messaggio intero, non si svuota la colonna.** Svuotare fabbrica
una riga che il messaggio non dice: `Price` vuota significa «la quota la mette
XTrader», `Handicap` vuoto significa una linea diversa, `Points` vuoto significa
1×. La regola generale: *si può svuotare solo una colonna la cui assenza il
consumatore interpreta come «non specificato», mai una la cui assenza interpreta
come un valore.*

**Il gate di contenuto:** almeno una colonna **obbligatoria** deve venire da
un'estrazione reale (`line`, `regex`, `message`) e aver prodotto un valore. Un
parser con le quattro obbligatorie tutte costanti scriverebbe la stessa
scommessa per qualunque messaggio che soddisfi la condizione — misurato:
«ciao a tutti» produceva una riga piazzabile.

**Le chiavi di `columns` sono le 14 colonne del CSV**, derivate da `HEADERS`:
una chiave con un refuso viene respinta con **422** e il suggerimento della
colonna vicina. Prima veniva accettata e poi ignorata dal motore, che itera su
`HEADERS`: sulle obbligatorie la diagnosi era falsa («manca EventName», mentre
la causa era il refuso), sulle facoltative non c'era **nessun** sintomo.

I motivi compaiono in `scarti` e nella risposta di `POST /api/me/parsers/{slug}/test`:
devono dire **cosa fare**, non solo cosa non va.

**Le costanti JSON non stringa** (booleani, numeri) vengono rese come le rende
JavaScript (`true`, `0.000001`, `1e-7`, `1e+21`): il ramo numerico di
`_testo_canonico` in `main.py` implementa la conversione numero→testo di
ECMAScript, perché `str()` di Python sceglie soglie e formato dell'esponenziale
diversi e un `Points` numerico piccolo era valido nel browser e scartato in
produzione. Vincolato dall'oracolo caso per caso. [REAL_FINDING] di GPT-5.6
Sol, PR #47. La stessa forma canonica finisce nella **riga CSV** — del feed
(`esito_messaggio`) e dell'anteprima della prova: la guardia da' il verdetto su
quel testo, e i byte che XTrader legge devono essere quelli che il cliente ha
giudicato nell'anteprima (secondo [REAL_FINDING] dello stesso gate). E per le
**cinque colonne numeriche** la riga contiene proprio il testo giudicato, spazi
uniformi ai bordi tolti: un `Price` con un BOM davanti e' una quota valida — i
bordi sono perdonati — ma il byte perdonato non deve raggiungere XTrader
(terzo [REAL_FINDING], stesso gate).

**Il separatore decimale e' una proprieta' del contratto (#40).** Per XTrader
si scrive la **virgola**: misurato tre volte — l'esempio della guida ufficiale
(p. 169, `"1,23"`, unico campo numerico valorizzato in 315 pagine), il Bridge
che gira in produzione con XTrader italiano, la conferma del proprietario.
Prima il separatore era un incidente: usciva cio' che la regola produceva, e il
suggeritore spingeva `comma_to_dot` su `Price` — verso il punto, che in
contesto italiano rischia la lettura come **migliaia**: `"1.85"` → quota 185,
dentro i tetti della #39, invisibile a ogni guardia, un disastro su una BANCA.
Adesso: i valori numerici **accettati** escono localizzati dal confine di
scrittura dei motori (tabella `lingua → separatore`, oggi la sola voce
`IT → virgola`; EN/ES saranno una riga quando arrivera' Betting Toolkit); le
trasformazioni dell'utente restano davanti, quindi i parser con `comma_to_dot`
gia' configurato producono lo stesso feed senza ritocchi; e
`verify_csv()`/`verifyCsv()` respingono un campo numerico col punto — senza
test eseguibili questa decisione varrebbe quanto la riga «senza BOM».
Il percorso legacy di PIERO non passa dal confine e resta byte-identico
(`Price` vuota, `Handicap` `0`: niente da localizzare).

**Niente emoji nei valori (#42).** «Solo testo. Emoji non li accetta XTrader,
lo marcherebbe non valido come segnale» — e senza errore di ritorno, solo
l'icona rossa. Il caso reale è la regola «riga intera» su una riga che comincia
col marcatore: il valore si porta dentro l'emoji e il feed esce formalmente
valido. Un valore (non numerico: lì un'emoji è già «non un numero») che
contiene un'emoji **scarta il messaggio** col motivo che dice cosa fare —
estrarre il testo *dopo* il marcatore — e `verify_csv()`/`verifyCsv()`
respingono un feed con emoji in qualunque colonna. Classe esplicita gemella
nei due motori (`_EMOJI`/`EMOJI`). E il suggeritore propone **`Provider`
vuota**: è il nome di chi manda, campo dell'utente (XTrader la usa come filtro
case-insensitive e come discriminante); il vecchio default `XTrader` era il
valore del CSV misurato in #5, che vale lì perché quel file l'ha scritto
XTrader.

Il motivo `config non eseguibile` del fail-safe esiste **solo se la condizione
del parser riconosce il messaggio**: senza quel gate, un parser con la config
rotta faceva archiviare in `message_logs` tutto il traffico della chat. Se la
condizione stessa non e' valutabile vale il silenzio (`parser_no_match`); la
diagnosi resta sulla rotta di prova. [REAL_FINDING] di Claude Fable 5.

Il confronto della riga e il taglio del marcatore ignorano entrambi maiuscole e
minuscole. Le ancore vengono tagliate per **codepoint**, non per unità UTF-16:
un emoji astrale a cavallo del taglio lascerebbe un surrogato spaiato, e
un'ancora così non combacerebbe più con nessuna riga, in silenzio.

`runParser` restituisce cinque campi: `matched` (la condizione combacia), `row`
(le 14 colonne), `missing` (le colonne obbligatorie risultate vuote), `scarti` (i
motivi per cui il messaggio non deve produrre riga, vedi «Guardie sui valori
estratti e sulla config» sopra) e `complete`.
**Chi scrive il feed deve guardare `complete`, non `matched`:** un messaggio
riconosciuto ma privo dell'evento produrrebbe una riga formalmente valida e priva
di senso per XTrader. Le colonne obbligatorie sono in `REQUIRED_COLUMNS` e dal
13/08/2026 (Issue #2, riconfermate su #25) sono **quattro**: `EventName`,
`MarketType`, `SelectionName`, `BetType` — l'evento, il tipo di mercato su cui
XTrader decide, la selezione, e se puntare o bancare. **`Provider` non è
obbligatoria:** è il nome di chi *manda* il segnale, campo dell'utente (il
suggeritore la propone vuota dalla #42), e pretenderla non protegge da nulla. **`Price` non è obbligatoria:** il parser in produzione (`main.py`) la
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

### Il dispatch multi-parser: chat → N parser, ognuno al SUO feed

Dalla PR 2 della sequenza (#2, chiude il pericolo 1 di #25) il webhook non prende
più «il primo profilo in ordine alfabetico che contiene la chat»: legge
**`parser_chats`**, che collega ogni chat ai parser che la ascoltano. I link
nascono dal **travaso** dei profili legacy (`_collega_parser_alle_chat`, una
volta sola per database dalla PR 4) e da lì in avanti li tiene aggiornati
`_riconcilia_link_del_profilo` a ogni `POST`/`DELETE /api/profiles`.

Le regole, nell'ordine in cui il codice le applica (`_processa_messaggio_canale`):

- ogni consegna porta un `update_id`: la **prima** vince (`webhook_seen`,
  `INSERT OR IGNORE`), le riconsegne di Telegram escono come `duplicate` senza
  rielaborare — senza questo, ogni retry riarmava il TTL e un segnale «da 90
  secondi» viveva più a lungo a ogni riconsegna;
- l'elaborazione gira **fuori dall'event loop** (`asyncio.to_thread`, #31 B1):
  un parser lento non ferma le altre richieste del servizio;
- i parser collegati alla chat si raggruppano **per utente**; l'accesso
  scaduto/sospeso salta l'utente (stessa `_blocco_della_riga` del feed);
- ogni parser attivo (`active=1`) elabora **in modo indipendente**; fra i parser
  dello **stesso** utente che riconoscono lo stesso messaggio **vince l'ultimo**
  nell'ordine dichiarato (`parsers.ordine`, poi `name`), e i battuti restano in
  `message_logs` come «riconosciuto, sostituito da …»;
- il vincente scrive nel feed del **suo** utente (`store_signal` con `utente=`,
  che risolve anche il profilo legacy dal ponte `origin_profile`: gli alias
  `/xtrader.csv` e `/profiles/{p}.csv` continuano a leggere);
- utenti **diversi** sulla stessa chat non si toccano: due profili sulla stessa
  chat sono due link, due utenti, due feed — nessuno «vince» più la chat.

**Il fallback legacy esiste e ha un solo caso:** una chat senza alcun link in
`parser_chats` passa dal vecchio percorso per profili. Si collegano solo i
parser che **appartengono all'utente del profilo**: se due profili nominano lo
stesso parser, quello del non-proprietario resta sul fallback — un link creato
lì manderebbe i segnali al feed del proprietario del parser, il legame
cross-tenant che i test della deduplica vietano. Un profilo il cui utente non
esiste ancora (nessuna riga con quel `origin_profile`) resta anch'esso sul
fallback.

**Il ciclo di vita dei link, dalla PR 4** (chiude il limite dichiarato e rimesso
al proprietario sulla PR #44, dove la semina era solo-aggiunta):

| evento | cosa succede ai link |
|---|---|
| `POST /api/profiles` che **cambia parser** | il link del parser vecchio sparisce, nasce quello nuovo — o la chat continuerebbe a far girare il parser sostituito |
| `POST /api/profiles` che **aggiunge una chat** | la riga di `chats` nasce se manca (proprietario: l'utente del profilo) e il link è attivo **subito**, senza aspettare un riavvio |
| `POST /api/profiles` che **toglie una chat** | il link di quella chat sparisce |
| `DELETE /api/profiles` | spariscono i link del **suo** parser sulle **sue** chat |
| link cancellato a mano | **non risuscita**: il travaso non si ripete |

Si tocca sempre e solo la coppia (parser del profilo, sue chat): i link degli
**altri** parser dello stesso utente sulla stessa chat — cioè il multi-parser —
sopravvivono a qualunque salvataggio di profilo. Una chat già esistente **non
cambia proprietario**: appartiene a chi l'ha rivendicata per primo.

E il distacco porta lo **stesso filtro sul proprietario** dell'aggancio: due
profili possono nominare lo stesso parser, e il profilo del non-proprietario —
che l'aggancio salta per isolamento — non deve poter staccare il link che il
proprietario ha legittimamente. Il detach disfa esattamente ciò che l'attach
potrebbe aver fatto, mai di più (bloccante di Claude Fable 5 sulla PR #46).

Il salvataggio del profilo **e la sua eliminazione** aprono `BEGIN IMMEDIATE`
**prima di leggere** lo stato precedente: una `SELECT` non apre nessuna
transazione di scrittura, quindi due `POST` concorrenti sullo stesso profilo
leggerebbero entrambi lo stato di partenza e attaccherebbero ciascuno il proprio
link — il profilo con un parser, i link con due, e il parser sostituito che
continua a girare. Sulla `DELETE` la stessa corsa produce un **link orfano**: un
salvataggio concorrente attacca un link che l'eliminazione non conosce, il
profilo sparisce e il link resta a elaborare la chat per sempre. È la stessa
corsa SELECT-poi-scrittura della quota (PR #45) e della richiesta di accesso
(PR #26), riprodotta da due test che forzano l'interleaving invece di sperarlo.

**Il travaso riconcilia, non aggiunge soltanto.** Fino a questo PR nessuna
scrittura toglieva i link, quindi un database aggiornato può arrivare con link
che nessun profilo giustifica più (profilo eliminato, o parser sostituito prima
dell'upgrade). I detach nuovi conoscono solo la configurazione corrente e il
travaso non gira mai più: se non pulisse, quei link resterebbero a elaborare chat
per sempre. Al momento in cui gira, ogni riga di `parser_chats` viene dalla
vecchia semina e nessuna richiesta è ancora stata servita — tenere ciò che i
profili giustificano e togliere il resto è la conversione esatta
(`[REAL_FINDING]` di GPT-5.6 Sol sulla PR #46).

`message_logs` e `webhook_seen` — entrambe tabelle finora **morte** — ricevono le
prime scritture, e la pulizia oltre i **7 giorni** viaggia con la scrittura
stessa, non con un timer.

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
succedeva la cosa peggiore — il feed usciva **formalmente valido** (14 colonne,
virgolette, CRLF, BOM), `verify_csv()` lo accettava perché controllava la sola forma, e
XTrader lo scartava **in silenzio**. Dalla PR della #42 non può più accadere: il motore
scarta il messaggio col motivo per colonna e `verify_csv()`/`verifyCsv()` respingono un
feed con un'emoji in qualunque campo. Il parser di riferimento usa `part: 'after'` con
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
nudi, tutti i campi fra virgolette, 14 campi per riga — riga per riga, senza
tetto sul numero: dal #35 (pezzo 1) il feed composto porta N segnali vivi.

È agganciata in **due** punti, e il numero conta più della funzione:

1. **`store_signal()`**, fail-closed: un CSV che non passa non viene memorizzato,
   quindi una riga malformata non esiste nemmeno per i 90 secondi del TTL.
2. **`GET /health`**, che restituisce
   `{"status": …, "csv": "ok", "auth": "ok", "feed_scartati": 0}` e diventa
   `degraded` con il motivo quando il controllo fallisce. `auth` risponde a una
   domanda diversa — se il token è configurato — e sta lì per la stessa ragione:
   un controllo che nessuno legge non è un controllo.

Il terzo aggancio — l'indicatore «formato valido per XTrader» nella vista Feed del
vecchio prototipo a dati finti — **non esiste più** dall'aggancio al backend (#32):
l'app non può leggere il feed dell'utente, perché servirebbe il token, che il
server non rimostra mai. Tornerà quando esisterà un'anteprima autenticata lato
server; `verifyCsv()` in `web/engine.js` resta, vincolata dal confronto con
l'implementazione Python in `tests/engine/`.

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

> **Superato dal feed per utente.** Le righe `feed per parser` e
> `POST /api/parsers/:id/token/rotate` di questo disegno descrivono il modello
> vecchio: il feed reale è `GET /feed/{slug}.csv?token=xt_…` — uno per utente —
> e il token si conia con `POST /api/me/token`. Vedi «Token dei feed».

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

Dal #35 (pezzo 2) la risposta della prova porta anche `righe` — per ogni riga
generata: `row`, `missing`, `scarti`, `complete` — con `complete` al livello
alto vero se **almeno una** riga è piazzabile e `csv` composto delle sole
complete (vedi «Il multi-riga» sopra).

**La precondizione della PUT (#51).** La vista del parser porta `versione`
(parte da 1, avanza a **ogni** modifica), e la PUT accetta una `versione`
opzionale: con un valore, il salvataggio riesce solo se il parser è ancora a
quella versione — altrimenti **409** con `ricarica il parser: e' stato
modificato altrove`, e il salvataggio dell'altra sessione resta intatto. Il
controllo sta **dentro l'UPDATE** (`… AND versione=?`), un solo statement
atomico sotto il write-lock di SQLite: niente TOCTOU, che è il motivo per cui
il fix client-side della PR #50 non bastava. Senza `versione` nel corpo la PUT
resta incondizionata (compat coi chiamanti storici) ma la versione avanza
comunque, così le altre sessioni se ne accorgono. `web/api.js` manda sempre la
versione letta; sul 409 la web app **non** butta le modifiche: `ricaricaParser`
riallinea la cache e il toast dice verbatim «Modificato altrove: le tue
modifiche sono ancora qui — ricontrolla e salva di nuovo per sovrascrivere» —
il secondo salvataggio è una sovrascrittura deliberata, non un incidente.

**I due conflitti sono due toast diversi, e devono restarlo (#75).** Il rimedio
è lo stesso — ricaricare — ma la cosa da fare dopo no, e il testo lo dice:

| conflitto | toast, verbatim |
|---|---|
| `versione` (#51): la **stessa** riga è cambiata | «Modificato altrove: le tue modifiche sono ancora qui — ricontrolla e salva di nuovo per sovrascrivere.» |
| `uid` (#75): quel nome è ormai di un'**altra** riga | «Eliminato e ricreato altrove: questo nome ora è di un altro parser. Le tue modifiche sono ancora qui — controlla quello nuovo prima di salvare.» |

La differenza non è di tono: nel primo caso risalvare sovrascrive una versione
che l'utente ha già visto, ed è una scelta legittima; nel secondo cancellerebbe
il lavoro appena fatto nell'altra scheda, quindi il testo **non** invita a
farlo. `app.js` distingue i due sul `detail` del 409, e il flusso in browser
(`tests/web/conflitto_flow.py`) verifica che il toast giusto compaia nel caso
giusto — accoppiamento fra codice e copy che è **inchiodato**, non fragile:
cambiare il testo del 409 in `main.py` rende rosso quel flusso, misurato per
mutazione sulla PR #76.

**Vale per il salvataggio e per l'eliminazione.** Anche la conferma «Elimina»
può ricevere il conflitto di identità — la #75 ha dato la precondizione a
entrambe le rotte — e mostra lo **stesso** toast, non il `detail` grezzo del
server: dopo di esso la modale di conferma si chiude, perché si riferisce a una
riga che non esiste più, e la vista si ridisegna sul parser che c'è adesso. Fino
alla PR #76 quel percorso passava dal gestore d'errore generico, quindi la
DELETE stantia stampava il testo interno del server e non riallineava niente:
segnalato da CodeRabbit, e ora vincolato dallo scenario in browser.

**La conferma esplicita al secondo Salva (#77).** Dopo il 409 la cache viene
riallineata (senza, ogni salvataggio successivo fallirebbe per sempre — il bug
del toggle, CodeRabbit PR #71), quindi il click successivo porterebbe l'`uid`
nuovo e sovrascriverebbe il parser ricreato: il salvataggio silenzioso era
chiuso — c'è il 409 e c'è l'avviso — ma la sovrascrittura restava a un click.
La chiude una **conferma esplicita** (issue #77, opzione A). Dopo un conflitto di
**identità** (`eliminato e ricreato`) il salvataggio — e la **prova**, che salva
anche lei — non procede: apre una **modale** intitolata *«Questo nome ora è di un
altro parser»*, con due strade:

- **«Guarda quello nuovo»** — rilegge la versione vera dal server e riapre il
  wizard su di essa; il draft stantìo si perde. È la via consigliata (bottone
  primario).
- **«Sovrascrivi comunque»** — sovrascrive il parser ricreato con le proprie
  modifiche: resta possibile, ma è una scelta deliberata, non un click silenzioso
  (bottone `danger`).

Cliccare fuori dalla modale la chiude senza fare nulla (il flag resta, il
prossimo Salva la ripropone). Il conflitto di **versione** («modificato
altrove», #51) **non** apre la modale: lì risalvare è una sovrascrittura
legittima della propria riga, e il toast basta. Fotografato e ora **invertito**
dal flusso in browser (`tests/web/conflitto_flow.py`), che prova la modale e
tutte e due le strade.

**L'identità della riga: `uid` (#73).** `versione` risponde alla domanda «è
cambiato mentre lo modificavo?». Ne esiste una seconda, diversa: «è ancora lo
**stesso** parser?». Lo `slug` torna libero appena il parser viene eliminato, e
`id` viene dal `rowid` che sqlite **riusa** (`parsers` è la tabella originale,
senza `AUTOINCREMENT`): un elimina+ricrea dello stesso slug produce una riga
nuova identica alla vecchia in ogni colonna che la identifichi. Una richiesta
rimasta in volo colpiva quella nuova — misurato: la DELETE la cancellava, e la
PUT le **sovrascriveva `config_json`**, cioè le regole che generano il CSV,
senza nessun sintomo visibile.

`parsers.uid` è un identificatore casuale a 128 bit assegnato alla creazione e
**mai riusato**. La DELETE e l'UPDATE della PUT lo vincolano entrambi, insieme a
`user_id`: la richiesta stantia porta un `uid` che non esiste più, tocca zero
righe e la rotta risponde **404** — come se la sostituzione fosse arrivata
prima. Le due guardie **convivono e non si sostituiscono**: `versione` non
copre questo caso, perché parte da 1 e il parser ricreato ha 1, cioè proprio il
valore che la richiesta stantia porta con sé.

**Le due finestre, e come si sono chiuse in due passi.** La distinzione era
scritta male al primo giro (bloccante di GPT-5.6 Sol al gate della PR #74,
verificato via HTTP prima di accoglierlo), e il secondo passo è la #75:

- **dentro la richiesta** — fra il `SELECT` con cui la rotta legge il parser e
  la scrittura che ne segue. È lì che la concorrenza del threadpool mette un
  elimina+ricrea. **Chiusa dalla #73/PR #74**: gli statement passano da `uid`.
- **client→server** — due schede aperte, l'utente elimina e ricrea il parser in
  una, l'altra salva. Quella richiesta arriva **dopo**, quindi la rotta legge
  l'`uid` nuovo. Misurato allora: `PUT` con `versione: 1` → **200**, titolo e
  `config_json` del ricreato sovrascritti con quelli della scheda vecchia.
  **Chiusa dalla #75**: `uid` esce nella vista del parser e il client lo rimanda
  come **precondizione** — nel corpo della `PUT`, in `?uid=` sulla `DELETE`
  (che un corpo non ce l'ha). Se lo slug identifica ormai un'altra riga il
  server risponde **409** `ricarica il parser: e' stato eliminato e ricreato
  altrove`, e il parser nuovo non viene toccato.

**Perché servivano due precondizioni e non una.** Rispondono a domande diverse:
`versione` a «è cambiato mentre lo modificavo?», `uid` a «è ancora lo **stesso**
parser?». Un elimina+ricrea produce una riga che riparte da `versione = 1` —
cioè dal valore che la scheda rimasta indietro ha in cache — quindi il contatore
da solo non li distingue. Entrambe restano **opzionali**: senza, le rotte sono
incondizionate come per i chiamanti storici.

`uid` è quindi passato da identità *interna* (#73) a campo della vista (#75).
Non è un segreto: è un identificatore opaco delle **proprie** righe, che la
sessione già autorizza a leggere e modificare. Restano interni `name` (identità
globale fra tutti gli utenti) e `user_id`.

La migrazione lo assegna alle righe già esistenti con un valore distinto per
riga, una volta sola (`WHERE uid IS NULL`): rigenerarlo a ogni avvio renderebbe
stantio a ogni deploy ogni riferimento in volo.

*Storia, perché non si ripeta:* fino alla #75 qui sotto restava la frase della
#73 — «`uid` è identità **interna**: non compare nella vista del parser né
nell'API» — accanto al paragrafo che documentava il contrario. Due affermazioni
opposte sullo stesso campo, a cinque righe di distanza: la documentazione del
cambiamento aggiunta senza togliere quella del comportamento precedente, che è
la stessa forma dell'errore del BOM. Segnalata da Claude Fable 5 sulla PR #76.

La scelta è del proprietario (18/08): `AUTOINCREMENT` avrebbe chiuso lo stesso
caso, ma non si aggiunge con un `ALTER` — andrebbe ricreata la tabella che porta
i parser di produzione.
Vincolata da `tests/relay/test_parser_crud.py` (due PUT dalla stessa base) e
`tests/web/test_conflitto_web.py` (il conflitto visto dal browser).

**Quote e tetti per-tenant** (#31 B2, PR 3 della sequenza #2 — vincolati da
`tests/relay/test_quote_parser.py`): il database e il volume Railway sono
**condivisi**, quindi la creazione ha un tetto di `MAX_PARSER_PER_UTENTE` parser
per utente (default 20, si alza da variabile su Railway senza deploy) — oltre,
**409** col limite nel messaggio; `titolo` massimo 80 caratteri e `config`
massima 20.000 caratteri di JSON — oltre, **422**, su creazione **e** modifica.
La quota è misurata **dentro il write-lock dell'INSERT** (contata prima, due
creazioni simultanee sull'ultimo posto la bucavano — misurato dal test della
corsa): il perdente riceve il 409 e il rollback toglie la sua riga. I messaggi
non nominano risorse di altri utenti.

Il corpo HTTP stesso ha un tetto in **byte** (`MAX_CORPO_JSON`, 64 KiB), misurato
**prima** del parsing (`_json_dal_corpo`, bloccante di GPT-5.6 Sol sulla PR #45):
senza, un tenant autenticato poteva far materializzare in RAM un corpo arbitrario
sul container condiviso prima che i tetti sui campi rispondessero 422. Oltre →
**413**: il `Content-Length` dichiarato respinge senza leggere un byte, la lettura
a pezzi interrompe lo stream di chi mente sull'intestazione o usa il chunked.
Vale sulle rotte autenticate che leggono JSON a mano (CRUD parser, prova
messaggio, concessione giorni admin). Il webhook Telegram non passa di qui: il
403 sul secret scatta prima di leggere il corpo, e i payload di Telegram sono
piccoli per costruzione.

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

### I mercati Betfair per-utente (#33)

La libreria **sport → mercato → selezioni**, tutta per-utente e a inserimento libero.
**Nessun catalogo incorporato** — correzione del proprietario del 13/08/2026: questi
dati li crea ogni utente, e al primo login la sezione è vuota. Tre tabelle
(`sports`, `betfair_markets`, `betfair_selections`); `user_id` vive **solo** su
`sports`, mercati e selezioni ereditano la proprietà per join. La riparazione di un
account passa da `_trasferisci_sport` (gemello di `_trasferisci_parser`): stesso slug
su due utenti è uno stato legale, e il travaso lo ri-disambigua invece di sollevare.

```text
GET/POST /api/me/sports                          sport dell'utente ({nome} → {slug, nome})
DELETE   /api/me/sports/{slug}                   cascata esplicita su mercati e selezioni
GET/POST /api/me/sports/{slug}/mercati           {marketType, marketName, selections[]}
DELETE   /api/me/sports/{slug}/mercati/{mid}
GET/POST /api/me/sports/{slug}/mercati/{mid}/selezioni      {selectionName}
DELETE   /api/me/sports/{slug}/mercati/{mid}/selezioni/{sid}
```

Isolamento come i parser: sessione, `user_id` mai dal corpo, **404** sugli altrui,
401 prima del 422, cookie rinnovato da ogni rotta. Validazione: campi non vuoti,
massimo 120 caratteri, **niente emoji** nei tre campi che finiscono nel CSV (#42);
doppione esatto → **409**; quote per-tenant `MAX_SPORT_PER_UTENTE` (20),
`MAX_MERCATI_PER_SPORT` (200), `MAX_SELEZIONI_PER_MERCATO` (200), misurate dentro il
write-lock come la quota parser.

**Il riferimento `config.betfair`.** Il wizard, quando compila da libreria, salva
nella config del parser `betfair: {market_id, selection_id}` accanto alle tre regole
costanti. Alla scrittura (POST e PUT) il server verifica che la selezione esista fra
quelle **dell'utente** per quel mercato e che le tre costanti coincidano **byte per
byte** coi valori della libreria: una selezione arbitraria via HTTP → 422 (è il test
che la #33 chiede per nome). Una selezione coi segnaposto `{HOME_TEAM}`/`{AWAY_TEAM}`
si può **creare** (è un dato, la #34 la renderà spendibile) ma non usare nel parser:
il token uscirebbe letterale nel CSV e XTrader scarterebbe in silenzio — 422 col
motivo. Eliminare un mercato **non** rompe i parser già salvati: le regole sono
costanti, la libreria è provenienza, non dipendenza viva — la validazione avviene
solo alla scrittura. Il motore non cambia: `betfair` è un campo che
`esegui_parser`/`runParser` ignorano, e il contratto CSV resta intatto.

### Le sorgenti squadre per-utente (#34, pezzo 1: modello dati + rotte)

La normalizzazione dei nomi squadra (alias sorgente → nome Betfair), gemella della
libreria mercati. Il modello congelato dal proprietario (13-14/08): la
**competizione** (sotto uno sport della libreria #33: Calcio → «Serie A») possiede
la **lista canonica dei nomi Betfair**, salvata una volta — è l'unica colonna che
finirà nel CSV; ogni **sorgente** (nominata, **rinominabile**) è una colonna di
alias sopra quella stessa lista, **un alias per squadra per sorgente**
(`UNIQUE (sorgente, squadra)`). Le competizioni organizzano; la ricerca del
trasform (pezzo 3) sarà su tutta la sorgente. Quattro tabelle: `competizioni`
(con `user_id` e `sport_id`), `squadre_betfair`, `sorgenti_squadre` (con
`user_id`), `alias_squadre` — quest'ultima senza `user_id`: la proprietà si
risolve per join dai **due** lati, e le rotte alias verificano **entrambi**
(competizione E sorgente), o un PUT scriverebbe nella sorgente di un altro.
La riparazione di un account passa da `_trasferisci_sorgenti_squadre`: stesso
nome di sorgente su due utenti è legale, il travaso rinomina chi arriva.

```text
GET/POST     /api/me/sorgenti-squadre              {nome} → {id, nome}
PATCH/DELETE /api/me/sorgenti-squadre/{sid}        rinomina · elimina coi SUOI alias
GET/POST     /api/me/competizioni                  {sport: slug, nome} → {id, ...}
GET/DELETE   /api/me/competizioni/{cid}            dettaglio (squadre + sorgenti col
                                                   badge `compilati`) · cascata
POST         /api/me/competizioni/{cid}/squadre    {nome} — finirà nel CSV: no emoji
DELETE       /api/me/competizioni/{cid}/squadre/{sid}   «× squadra»: via da TUTTE le sorgenti
GET/PUT      /api/me/competizioni/{cid}/alias/{srcId}   {alias: {squadra_id: "Juve"}}
```

Semantica delle azioni di riga (decisa il 13/08): **⌫ alias** = alias vuoto nel
PUT, svuota solo quella sorgente, la squadra resta ovunque; **× squadra** = la
DELETE qui sopra, cascata su tutte le sorgenti. Il PUT tocca **solo** le coppie
presenti nel corpo. Eliminare lo **sport** (#33) ora cascata anche su
competizioni, squadre e alias. Isolamento, 401-prima-del-422, cookie rinnovato,
quote nel write-lock (`MAX_SORGENTI_PER_UTENTE` 20, `MAX_COMPETIZIONI_PER_UTENTE`
50, `MAX_SQUADRE_PER_COMPETIZIONE` 100) e inserimenti `WHERE EXISTS` contro le
righe orfane: tutto come i mercati. Il selettore nel parser, il separatore
squadre e il trasform nei due motori sono i pezzi 2-3 della #34: **il motore e
il contratto CSV in questo pezzo non cambiano**.

### Il trasform nel parser (#34, pezzo 3: selettore sorgente + traduzione)

Le tre decisioni del proprietario (17/08/2026): squadra **non mappata = verbatim
+ avviso** non bloccante; la traduzione tocca **solo `EventName`**; lo stesso
alias su **due squadre della stessa sorgente è vietato al salvataggio** — il PUT
alias risponde 422 (`alias «X» gia' usato per un'altra squadra in questa
sorgente`), controllato sullo **stato finale** (mappa della sorgente col corpo
sovrapposto), così spostare un alias fra due squadre in un solo PUT resta lecito.
La chiave del confronto è quella **normalizzata** (`_piatto`), la stessa con cui
il parser cerca. E la stessa classe copre l'**ombra dell'identità**: un alias che
coincide col nome Betfair di un'**altra** squadra dell'utente (qualunque
competizione) è 422 — nella mappa l'alias vince sull'identità e quel testo
tradurrebbe un nome canonico nella squadra sbagliata, dentro il CSV. Il nome
della squadra stessa resta lecito (identità innocua).

Il parser porta il riferimento nel config: `team_source` (id della sorgente),
validato al confine di scrittura come `betfair` — inesistente, altrui o non
intero = 422, e quella altrui è indistinguibile da una inesistente. La mappa si
risolve a parse-time (`_mappa_team_source`): **identità Betfair→Betfair di tutte
le squadre dell'utente** (chi scrive già il nome Betfair non mappa niente e non
riceve avvisi) sotto gli **alias della sorgente**, chiavi normalizzate con
`_piatto`. Sorgente eliminata = passthrough puro, come «nessuna sorgente».

I due motori (`esegui_parser` / `runParser`) accettano la mappa come argomento
opzionale e traducono il valore **finale** di `EventName`, spezzato
sull'**ultimo** ` - ` (il separatore del transform del wizard), con confronto
esatto dopo `_piatto`/`piatto`. Il nuovo campo **`avvisi`** (accanto a `scarti`,
ma NON blocca: `complete` resta vero) nomina la squadra senza alias; la parità
è vincolata dai casi in `engine_cases.mjs` col campo `avvisi` nel confronto.
La prova (`POST /api/me/parsers/{slug}/test`) risolve la stessa mappa e
restituisce `avvisi`; sul traffico vero il dispatch scrive una riga
`avviso: …` in `message_logs` per ogni squadra non mappata del segnale scritto.

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
| `POST /api/me/token` | conia (o rigenera) il token del feed; il token in chiaro esiste solo qui | `401` senza sessione |
| `GET /feed/{slug}.csv` | il feed dell'utente, autenticato dal **suo** token (`?token=xt_…`) | **`404` uniforme**: slug inesistente, token assente, sbagliato o altrui — un `401` su uno slug esistente direbbe a chi enumera che quel cliente esiste |

Le risposte CSV dei feed (`/feed/{slug}.csv`, `/xtrader.csv`, `/profiles/{p}.csv`)
portano `Content-Disposition: attachment` con nome `betrelay-{slug}.csv`,
`betrelay.csv` e `betrelay-{p}.csv` (#60): chi incolla l'URL in un browser scarica
un file che si chiama betrelay, non xtrader. Solo il nome del download — URL,
status e byte del corpo identici; XTrader legge il corpo e ignora l'intestazione.
Fonte unica `_intestazioni_feed()` in `main.py`, nome ripulito a `[A-Za-z0-9._-]`,
test in `tests/relay/test_nome_download.py`.

`POST /api/logout` è pubblica **per scelta**, non per dimenticanza: cancella un cookie
e non legge nulla. Metterle una serratura significherebbe che chi ha un cookie
malformato non riesce a liberarsene, cioè resta incastrato in uno stato da cui
l'unica uscita è svuotare i cookie a mano.

`GET /api/me` non restituisce mai un token, né l'hash della password, né il
`telegram_id`: i primi due sono segreti, il terzo non serve al browser e finirebbe nei
log di qualunque proxy davanti al servizio. Restituisce `utente`, `nome`, `stato`,
`admin`, `accesso_scade`, `giorni_rimasti`, `slug`, `token_prefix` — il prefisso
non è il token: sono i primi 9 caratteri, quelli che la UI mostra per dire quale
token è armato.

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

**Pensionamento completato (PR 4 della sequenza #2).** Con la rimozione del seme
(#25, lavoro E) `TELEGRAM_ALLOWED_CHAT_IDS` **non viene più letta da nessuna riga
di codice**: era già inerte su un database persistente, adesso è morta. La
scaletta qui sotto resta come registro di come ci si è arrivati — i suoi passi 1-3
erano stati fatti, e il 4 (togliere la variabile dalle Variables di Railway) è
un'azione facoltativa del proprietario, non più una precondizione di niente.

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
| Fatto | **Un feed per utente** (PR 1 del piano sincronizzato in #2): `GET /feed/{slug}.csv?token=xt_…`, token coniato da `POST /api/me/token` e conservato solo come hash, segnale a chiave `user_id`, 404 uniforme, alias `/xtrader.csv` e `/profiles/{p}.csv` intatti. Test in `tests/relay/test_feed_utente.py` |
| M1 | Verifica chat. **Postgres differito** e non più urgente: i dati persistono già, `DB_PATH` in produzione è `/data/signals.db` dentro il volume (misurato il 12/08/2026) |
| Fatto | **Dispatch multi-parser** (PR 2 del piano sincronizzato in #2): chat → N parser via `parser_chats`, `active` e `ordine` finalmente letti, vince l'ultimo con i battuti in `message_logs`, dedup `webhook_seen`, elaborazione fuori dall'event loop (#31 B1). Chiude il pericolo 1 di #25. Il motore Python e l'endpoint di test c'erano già (PR #28-#30). Test in `tests/relay/test_dispatch_multiparser.py` |
| Fatto | **Quote e tetti per-tenant** (PR 3 del piano sincronizzato in #2, #31 B2): `MAX_PARSER_PER_UTENTE` misurata dentro il write-lock dell'INSERT, tetti su titolo e config su creazione **e** modifica, tetto in byte sul corpo HTTP prima del parsing (`MAX_CORPO_JSON`). Test in `tests/relay/test_quote_parser.py` |
| Fatto | **Rimozione del seme** (PR 4 del piano sincronizzato in #2, #25 lavoro E): `migra()` non ricrea piu' `Parser_Telegram_XTrader_v1` ne' il profilo `PIERO` a ogni avvio — cancellare e' durevole, rinominare non lascia doppioni, un database vergine nasce vuoto. Il travaso dei link `parser_chats` gira **una volta sola** (tabella `migrazioni`) e da li' in avanti i link seguono le scritture dei profili. `TELEGRAM_ALLOWED_CHAT_IDS` e' pensionata. Test in `tests/relay/test_rimozione_seme.py` |
| Fatto | **Accesso su approvazione** (PR 7), lato server: `stato_effettivo` / `giorni_rimasti` / `nuova_scadenza` come fonte unica, la richiesta del cliente col deep link del bot, la decisione del proprietario con i giorni liberi e l'errore di invio **non ingoiato**, il promemoria a 5 giorni una volta per scadenza, e gli effetti della scadenza su feed e webhook. Il **token non viene revocato** alla scadenza |
| Fatto | **Aggancio web app → backend** (PR 8 del piano sincronizzato in #2, #32 · 3.3a): `web/api.js` parla col relay via `fetch` (login a password, login Telegram in modalità redirect di oauth.telegram.org costruito col `bot_id` di `GET /api/settings`, CRUD parser, prova sul server con gli `scarti`, token del feed a livello utente), il vecchio layer a `localStorage` vive in `web/api_finta.js` per la sola copia dimostrativa a file unico. Test browser end-to-end in `tests/web/` |
| Fatto | **Schermate dell'accesso su approvazione** (PR 9, #7 lato cliente): il gate sugli stati in `render()`, le quattro schermate (registrato/in_attesa/scaduto/sospeso) con «Richiedi accesso» sul `POST` vero e il deep link del bot, la pillola gialla con 5 giorni o meno. Test browser con utenti seminati e cookie firmati in `tests/web/test_schermate_accesso.py` |
| Fatto | **Pannello Richieste** (PR 10, #7 lato admin): la vista con l'elenco, «Attiva» col campo giorni libero e l'avviso Telegram fallito **visibile**, «Rifiuta» con conferma, il giro dei promemoria. Test browser end-to-end con decisioni verificate sul database in `tests/web/test_pannello_admin.py` |
| Fatto | **Mercati Betfair per-utente** (PR 12, #33): la libreria sport → mercato → selezioni a inserimento libero (nessun catalogo incorporato), le rotte `/api/me/sports*` isolate, la vista «Mercati Betfair» e il wizard «Da mercati Betfair» a due passi con `config.betfair` validato alla scrittura. I segnaposto handicap restano fail-closed fino alla #34. Test in `tests/relay/test_mercati.py` e `tests/web/test_mercati_web.py` |
| Fatto | **Il file scaricato si chiama betrelay** (#60): `Content-Disposition: attachment` sulle risposte CSV — `betrelay.csv`, `betrelay-{slug}.csv`, `betrelay-{p}.csv` — con nome ripulito e fonte unica `_intestazioni_feed()`. URL e byte del corpo intatti. Test in `tests/relay/test_nome_download.py` |
| Fatto | **Sorgenti squadre, pezzo 1** (#34): modello dati e rotte `/api/me/sorgenti-squadre*` + `/api/me/competizioni*` — competizioni con lista Betfair condivisa, sorgenti rinominabili, alias per (sorgente, squadra), azioni «⌫ alias»/«× squadra», cascate esplicite (sport compreso), travaso alla riconciliazione. Test in `tests/relay/test_sorgenti_squadre.py` |
| Fatto | **Sorgenti squadre, pezzo 2** (#34): la sezione web «Sorgenti squadre» — elenco competizioni, squadre Betfair salvate una volta, pulsanti sorgente col badge `compilati`, tabella a due colonne con ⌫/×, Rinomina/Elimina — su `api.js`/`api_finta.js` in parità. Test browser in `tests/web/test_squadre_web.py` |
| Fatto | **Sorgenti squadre, pezzo 3** (#34): selettore «Sorgente squadre» nel wizard + trasform alias→Betfair a parse-time in **entrambi** i motori (parità vincolata, campo `avvisi`), identità Betfair→Betfair nella mappa, `team_source` validato al confine di scrittura, avvisi nella prova e in `message_logs`, alias ambiguo vietato al salvataggio. Chiude la #34 e, col wizard «Da mercati Betfair» della #33, la coppia di issue gemelle |
| Fatto | **Multi-riga, pezzi 1–2** (#35): feed a N righe vive (`store_signal` a lista, `componi_feed`, TTL per riga) e motore base+override nei due motori (somma mercati+selezioni, eredità, `enabled`, punteggi dinamici col tetto, gate #41 per riga, `config.multi` validata col 422 e `MAX_RIGHE_MULTI`). Test in `tests/relay/test_multiriga.py` e `tests/engine/` |
| Fatto | **Multi-riga, pezzo 3** (#35): la card «Output e condizioni» nel riepilogo del wizard — righe di override editabili, prova col k su N per riga, CSV composto anche nell'anteprima locale (`componiFeed`), persistenza della `config.multi` nel draft. Chiude la #35. Test browser in `tests/web/test_multiriga_web.py` |
| M3 | «Entra come» e lo storico di `admin_audit` nel pannello: servono rotte nuove lato server |
| M4 | Log persistenti, sospensione, suggerimento AI lato server, abbonamenti |

## Facciata pubblica

`GET /` serve `web/sito.html`: la pagina che vede chi scrive `betrelay.net`. Fino
all'11/08/2026 l'apex restituiva `{"service": "xtrader-signal-relay", ...}` — corretto
per una sonda, inutile per una persona.

Dal 17/08/2026 la pagina è la **facciata definitiva** decisa in #37/#38: solo italiano,
menu con la sola voce «Home», niente Demo / Documentazione / FAQ / Contatti (differite
dal proprietario nei commenti della #37). Il widget chatbot arriverà con la #36.

**Cosa contiene**, nell'ordine, perché la documentazione UI deve descrivere la pagina
com'è e non come sarebbe comoda:

| Fascia | Contenuto, verbatim dove è una scritta |
|---|---|
| Barra | logo relay (`web/betrelay-icona-256.png`) + «BetRelay», menu «Home» (nascosto sul telefono: il marchio fa da Home), pulsante «Entra» → `/app/` |
| Stato | pastiglia con pallino verde: «Servizio in avviamento · accesso su approvazione» |
| Apertura | due colonne (una sul telefono): titolo «I segnali del tuo canale Telegram, **dentro il tuo software di betting**. Automaticamente.», sommario, «Entra con Telegram» → `/app/` e «Come funziona» → `#come`; accanto la **card di flusso** |
| Card di flusso | cinque nodi: «📲 Telegram» (solo chat configurate) → «🧩 Custom Parser» (estrae campi, non inventa dati) → «📄 segnali.csv (14 colonne)» (un solo segnale attivo per parser) → «🎯 XTrader / Betting Toolkit» (timeout / conferma) → «🧹 CSV pulito — mai segnali vecchi» |
| Come funziona | tre schede numerate: «Colleghi la chat», «Descrivi il messaggio», «Incolli l'indirizzo in XTrader» |
| Dal messaggio alla riga | il messaggio Telegram d'esempio accanto alle quattro colonne obbligatorie del CSV, e la nota dei 90 secondi |
| Oggi la guida copre XTrader | la famiglia prodotti, XTrader-first: «XTrader · TradingSportivo · Italia» con pillola verde «Attivo»; «BETTINGTOOLKIT.COM / .ES / .LAT» con pillola gialla «In arrivo» |
| Perché è diverso da un bot di scommesse | «BetRelay **non piazza scommesse** e non parla con Betfair»; sei schede con occhiello: «Legge il TUO canale», «Prova, poi vai live», «CSV sempre pulito», «Più chat sorgenti», «Isolamento fra clienti», «Log dei messaggi»; chiude la «Nota di trasparenza» (token in chiaro una volta sola, solo hash sul server, mai nei log) |
| Come si entra | l'accesso su approvazione, e «Entra con Telegram» → `/app/` |
| Chiusura | «BetRelay · betrelay.net», «Applicazione» → `/app/`, «Stato del servizio» → `/health`, «18+ · Gioca responsabilmente», e il disclaimer: progetto indipendente, non affiliato a TradingSportivo (XTrader) né a Betting Toolkit; i marchi citati solo per indicare la compatibilità |

**Le icone sono asset committati** (#37): `web/betrelay-favicon-sito.ico` (favicon,
16/32/48) e `web/betrelay-icona-256.png` (logo header e apple-touch-icon), serviti dal
mount statico `/app` già esistente — nessuna rotta nuova, nessun CDN. Non sono segreti:
la cartella `web/` è pubblica per progetto.

**Invarianti che vincolano questa pagina**, non preferenze estetiche:

- è servita da una **rotta esplicita**. Un catch-all `@app.get('/{resto:path}')`
  coprirebbe `/feed/{utente}.csv` prima che quella rotta esista, e XTrader riceverebbe
  `text/html` con stato 200 al posto di un CSV;
- **nessun token** compare nella pagina: è pubblica e senza sessione, quindi qualunque
  valore scritto lì è pubblicato;
- **nessun `noindex`**, al contrario di `/app`: una landing che si esclude dai motori di
  ricerca non è una landing. La differenza fra le due pagine è vincolata da un test;
- **stile incorporato, nessun CDN**: le uniche richieste oltre al documento sono i due
  asset icona committati nel repo. Gli stessi token di colore di `web/styles.css`,
  ricopiati di proposito;
- `.dentro` porta i margini laterali e **sta sempre da sola sul proprio elemento**. La
  prima versione la combinava con `.apertura`, la cui forma breve `padding: 72px 0 56px`
  azzerava il margine laterale: titolo e pulsanti attaccati al bordo del telefono.
  Misurato a 390 px, non guardato a occhio.

Il testo dice quello che il servizio fa **oggi**: XTrader attivo, Betting Toolkit «in
arrivo», accesso su approvazione. Due adattamenti dichiarati rispetto allo sketch della
#38, dove il copy descriveva il Bridge: «scrittura atomica» sotto `segnali.csv` è
diventato «un solo segnale attivo per parser» (qui il feed esce dal database, non da un
file su disco) e «un solo set di segnali attivo» è diventato «un solo segnale attivo per
parser» — il comportamento vero del relay. La pastiglia «Servizio in avviamento» va
cambiata quando il servizio sarà aperto, non lasciata lì.

## La web app su `/app`

```sh
uvicorn main:app --reload
```

Poi `http://127.0.0.1:8000/app/`, oppure `http://127.0.0.1:8000/` e il pulsante «Entra».
Dal PR dell'aggancio (#32 · 3.3a) **non è più un prototipo a dati finti**: le viste
(`web/app.js`) parlano col relay attraverso `web/api.js`, e ciò che si salva finisce
nel database del servizio. Il messaggio di esempio del wizard è l'unica cosa che resta
nel browser (`localStorage`, per slug): è un appunto di lavoro, non un dato del contratto.

### Login

Due porte, le stesse del backend (PR 6):

- **«Accedi con Telegram»**: un link — non un widget, non uno script esterno, che le
  regole del repository vietano — verso la modalità redirect di `oauth.telegram.org`,
  costruito col `bot_id` numerico di `GET /api/settings`. Al ritorno la pagina consuma
  il frammento `#tgAuthResult`, lo POSTa a `/api/login/telegram` e lo toglie dall'URL.
  Se il servizio non ha un bot configurato, la porta non compare;
- il modulo **Username / Password** («Entra»): la porta di riserva dell'amministratore
  (`POST /api/login/password`). L'errore del server compare sotto il pulsante, verbatim
  («credenziali non valide», il 429 del freno, il 503 della variabile assente). Dal
  17/08/2026 (#58) il campo Username **non ha segnaposto**: il vecchio
  `administrator` suggeriva a chiunque il probabile nome dell'amministratore.

La pagina porta il marchio **BetRelay** (#59): il logo relay committato con la
facciata (`betrelay-icona-256.png`) sopra il nome «BetRelay», e la favicon `.ico`
del sito anche su `/app` (`web/index.html`, `<title>BetRelay</title>`). «XTrader»
resta nel sottotitolo e ovunque si parli del software di betting di destinazione:
è il prodotto compatibile, non il marchio del servizio. Nel file unico demo il
logo è **incorporato** come data URI dal generatore (da `file://` un percorso
relativo sarebbe un'icona rotta senza errori), con un marcatore vincolato che
ferma il build se la sostituzione non può avvenire.

La sessione è il cookie firmato del server: al 401 (venti minuti di inattività) la
pagina si ricarica e torna al login, senza stati intermedi bugiardi.

### Stati dell'accesso (#7)

Dopo il login, chi **non** è `attivo` non vede l'app: vede una schermata a tutto
schermo (stessa cornice del login) che dice a che punto è il suo accesso. Il gate
sta in `render()`, prima di ogni vista; **l'amministratore non passa mai di qui** —
entra sempre, è lui che approva.

| Stato | Titolo, verbatim | Cosa può fare |
|---|---|---|
| `registrato` | «Ti manca solo l'accesso» | «Richiedi accesso» → `POST /api/access/request`, nessun modulo |
| `in_attesa` | «Richiesta inviata» | aprire il bot: «Apri il bot e premi Start» (deep link `t.me/<bot>?start=accesso`) |
| `scaduto` | «Accesso scaduto» | «Richiedi il rinnovo» — la config dei parser resta, lo dice la schermata |
| `sospeso` | «Accesso sospeso» | niente richiesta: la sospensione la scioglie l'amministratore |

Il deep link non è un abbellimento: il bot **non può scrivere per primo** (trappola 1
della Issue #7), e la schermata d'attesa lo spiega sotto il pulsante. Appena chiesto
l'accesso il link arriva dalla risposta del server; a una visita successiva si
ricostruisce dai settings pubblici (`bot_username`), con lo stesso payload `accesso`.
Senza bot configurato il link non compare e resta la frase d'attesa.

Un 409 su «Richiedi» (doppio clic, richiesta già in corso) non è un errore da
mostrare: la pagina si ricarica e la vista giusta si disegna da sola.

Nella **dashboard** di un attivo, quando restano **5 giorni o meno** la pillola
dello stato diventa **gialla**: «attivo, N giorni rimasti — pensa al rinnovo».
La soglia è inclusiva (`giorni_rimasti <= 5`), la stessa del promemoria
Telegram: i due avvisi devono raccontare la stessa storia.

### Struttura

Sidebar: in alto il marchio — logo relay + «BetRelay» (#59) — poi «Dashboard»,
«Parser», «Mercati Betfair», «Sorgenti squadre», «Feed CSV», «Chat Telegram»,
«Log messaggi», «Impostazioni», più nome utente, profilo (slug) e «Esci».
**Solo per l'amministratore** compare anche «Richieste», subito sotto la
Dashboard.

La scorciatoia **`/admin`** (#57) reindirizza a `/app/#/richieste`: solo un
redirect, la serratura resta il login più il 404 server-side delle rotte admin —
chi non è amministratore ci atterra sul login o sulla propria dashboard.

### La libreria «Mercati Betfair» (#33)

Tre livelli, con la briciola in alto per risalire:

- **Elenco sport** (`#/mercati`): parte **vuoto** — «Non hai ancora sport: si parte
  da zero, come deve essere» — con «Nuovo sport» (modale col solo nome) e, per ogni
  sport, il conteggio dei mercati e «× elimina» con conferma («I parser già salvati
  non cambiano: le loro regole restano»).
- **Mercati dello sport** (`#/mercati/{sport}`): «Crea mercato» apre la modale a
  **inserimento libero** — «MarketType (codice)» e «MarketName (etichetta)»: «scrivi
  il codice e il nome come li vuole XTrader. Le selezioni le aggiungi dopo, dentro il
  mercato». Ogni riga mostra il MarketType (mono, cliccabile), il MarketName e il
  conteggio selezioni, più «× elimina» con conferma.
- **Selezioni del mercato** (`#/mercati/{sport}/{id}`): la lista con «×» per riga, il
  campo «es. Over 0,5 goal» e «Aggiungi» — «crea tutte le selezioni che ti servono:
  due per un Over/Under, tante per un Risultato esatto». Una selezione coi segnaposto
  squadra porta la nota «spendibile nel parser quando arriverà la sorgente squadre
  (#34)». L'errore del server (doppione, tetto) compare sotto il campo, verbatim.

**Il wizard a due passi.** Sul passo `MarketType` compare il quarto tab «Da mercati
Betfair»: ① «Scegli il mercato — MarketName si compila da solo» (lista delle righe
`MarketType · MarketName` dell'utente, con la tendina Sport se ne ha più d'uno);
② «Scegli il risultato — solo le selezioni che hai creato». La scelta compila
MarketType, MarketName e SelectionName come costanti, salva il riferimento
`betfair` nella config e lo dice col banner «Scelto dalla libreria…». Le selezioni
coi segnaposto sono **spente** («— spendibile con la sorgente squadre (#34)»). Se
l'utente riscrive a mano una delle tre colonne, il riferimento si toglie da solo al
salvataggio (`coerenzaBetfair`): restano costanti libere, come sono sempre state.
Libreria vuota → «Crea sport e mercati in Mercati Betfair, poi torna qui».

### La sezione «Sorgenti squadre» (#34, pezzo 2)

Tre livelli sugli sketch approvati (13/08), con la briciola per risalire:

- **Elenco competizioni** (`#/squadre`): parte **vuoto** — «Non hai ancora
  competizioni: si parte da zero, come per i mercati» — con «Nuova competizione»
  e, per ogni competizione, il nome dello sport, il conteggio delle squadre
  Betfair e «× elimina» con conferma («sparisce con le sue squadre Betfair e gli
  alias relativi in tutte le sorgenti. Le sorgenti restano.»). La modale di
  creazione chiede **Sport** (tendina della libreria #33) e **Nome della
  competizione**; senza sport in libreria rimanda: «Crealo in Mercati Betfair,
  poi torna qui».
- **Competizione** (`#/squadre/{id}`): due riquadri. **«Squadre Betfair»** — la
  lista canonica, salvata QUI e condivisa da tutte le sorgenti, col campo «es.
  Juventus (nome Betfair)» + «Aggiungi» (errore del server verbatim sotto il
  campo) e la **«× squadra»** per riga, con conferma: «sparisce dalla
  competizione e dai suoi alias in **tutte le sorgenti**». **«Sorgenti»** — i
  pulsanti delle sorgenti già create, ognuno col badge `compilati/squadre`
  (quante squadre hanno già l'alias in quella sorgente), più «+ Aggiungi
  sorgente» (modale col solo nome: la sorgente vale per tutte le competizioni).
- **Tabella alias** (`#/squadre/{id}/{sorgente}`): in testa «Rinomina» ed
  «Elimina sorgente» («sparisce con i SUOI alias, in tutte le competizioni. Le
  squadre Betfair restano dove sono.»); poi una riga per squadra Betfair con
  l'input dell'alias accanto e la **«⌫»** che svuota l'alias **solo in questa
  sorgente**, subito e senza conferma (azione locale — la conferma è della «×»,
  che è condivisa). La ⌫ salva la tabella **come la vedi**: le altre righe,
  anche se digitate e non ancora salvate, restano quello che mostrano (deciso
  al giro di review della PR #66). «Salva alias» scrive le coppie e mostra il
  toast «Alias salvati.»; il badge nel livello sopra si aggiorna.

Le invarianti che la UI racconta: la colonna Betfair non si ridigita mai per
sorgente; ⌫ = locale e senza conferma, × = condivisa e con conferma; eliminare
una sorgente non tocca le squadre.

### Il selettore «Sorgente squadre» nel wizard del parser (#34, pezzo 3)

Nel **riepilogo** del wizard, fra la condizione di riconoscimento e la tabella
delle colonne, la card **«Sorgente squadre»**: una tendina con l'opzione
**«Nessuna — i nomi squadra passano come scritti»** più le sorgenti dell'utente,
e la microcopy verbatim: «Con una sorgente scelta, gli alias diventano i nomi
Betfair dentro EventName. Una squadra senza alias passa come scritta, con un
avviso qui nella prova e nei log dei messaggi.» Finché l'elenco non è arrivato
dal server la card mostra «Caricamento sorgenti…» e la tendina **non** si
disegna (una tendina vuota letta al salvataggio cancellerebbe una scelta già
salvata). La scelta si legge al **salvataggio** e alla **prova** — come il
messaggio di prova, niente salvataggio implicito al change — e riaprire il
parser la ritrova selezionata. Nella prova, gli **avvisi** delle squadre senza
alias compaiono in un banner giallo (`#test-avvisi`) accanto a quello degli
scarti: avvisano, non bloccano — il CSV c'è comunque.

### La card «Output e condizioni» nel riepilogo del wizard (#35, pezzo 3)

Sotto la card «Sorgente squadre», la card **«Output e condizioni»**: le righe
di override del multi-riga (`config.multi`), con il contatore **«N/20 righe»**
e la microcopy verbatim: «Un messaggio, più righe nel feed: la riga base è il
modello, ogni riga qui dice solo **cosa cambia** e il resto eredita. Una riga
con un valore scartato non ferma le altre. Selezione vuota + delimitatori =
una riga per punteggio N-N, solo su CORRECT_SCORE e HALF_TIME_SCORE.»

- **«Aggiungi mercato»** / **«Aggiungi selezione»**: ogni riga è una sotto-card
  («Mercato N» / «Selezione N») con il checkbox **«attiva»**, il pulsante
  **«Rimuovi»** e i campi con placeholder **«eredita»** — vuoto = il valore
  della base. Le righe selezione **non** mostrano MarketType/MarketName:
  restano sul mercato della base, com'è da contratto della somma. I due
  delimitatori sono etichettati «Quota/punteggi da (testo dopo)» e «fino a
  (testo prima)». Al tetto (20, il default del server) i pulsanti si
  disabilitano.
- Le righe si **leggono dal DOM prima di ogni azione** (`leggiMulti` nel
  dispatcher dei click: anche «Sospendi» o un'altra azione fuori dalla card
  ridisegnano il riepilogo, e senza la cattura cancellavano gli input non
  salvati — Fable, PR #70) — niente salvataggio implicito al change — e
  riaprire il parser le ritrova (la config.multi viaggia nel draft come
  `betfair`/`team_source`: senza, riaprire e salvare la cancellerebbe).
- **La prova col k su N**: col multi **attivo** (almeno una riga non vuota e
  non spenta — anche una sola: una riga singola rotta non deve nascondere il
  suo motivo, segnalato da CodeRabbit sulla PR #70) la pillola d'esito dice
  «Riconosciuto: k di N righe piazzabili» (o «Nessuna riga piazzabile: 0 di
  N…»), e sotto (`#test-righe`) ogni riga ha la sua pillola
  **«piazzabile»**/**«scartata»** con `MarketType · SelectionName` e, per le
  scartate, il motivo (gli scarti, o «manca …»). Il CSV della prova è quello
  **composto** dal server (header una volta, le sole righe piazzabili); anche
  l'anteprima locale compone le righe piazzabili con `componiFeed`, così —
  **senza una sorgente squadre selezionata** — i due riquadri coincidono byte
  per byte. Con una sorgente selezionata l'anteprima locale resta quella
  storica del #34: **non traduce gli alias** (la mappa vive sul server) ed è
  dichiarata indicativa — fa fede la prova sul server. Senza `config.multi`
  tutto resta com'era.

### Il pannello Richieste (#7, solo amministratore)

La voce e la vista esistono solo con `admin` vero; il server risponde comunque
**404** a chi non lo è, e un cliente che digita l'hash a mano vede la dashboard.

- **Elenco**: per ogni richiesta, nome, `@username`, «chiesto il» (timestamp del
  server), stato — e, se il cliente non ha mai aperto il bot, l'avvertimento in
  giallo verbatim: «Non ha ancora aperto il bot: il messaggio di approvazione non
  potrà raggiungerlo.» (trappola 1).
- **«Attiva»** con il campo **giorni libero** (placeholder `giorni`, 1–3650):
  senza un numero non parte nessun POST. L'esito resta sopra l'elenco:
  - avviso partito → «Accesso attivato: N giorni. Il cliente è stato avvisato su
    Telegram.»;
  - avviso **fallito** → banner giallo: «Accesso attivato (N giorni), **ma
    l'avviso Telegram NON è partito** — <motivo>. Contatta il cliente a mano: per
    lui non è cambiato niente finché non lo sa.» L'invio fallito non si ingoia
    mai; l'accesso resta concesso, com'è da contratto del server.
- **«Rifiuta»**, con conferma: la modale dice che il cliente torna «registrato»
  e potrà chiedere di nuovo — un rifiuto non è una sospensione.
- **Promemoria di scadenza**: la card spiega che non c'è uno scheduler e il giro
  parte dal pulsante «Manda il giro di promemoria»; l'esito è «avvisati: N ·
  falliti: M» accanto al pulsante.

**Non ancora nel pannello** (restano in M3): «Entra come» e lo storico di
`admin_audit` — servono rotte nuove, non esistono lato server.

- **Dashboard**: la pillola dello stato dell'accesso («amministratore», «attivo, N
  giorni rimasti», o lo stato grezzo), quattro contatori misurati («Parser», «Parser
  attivi», «Token del feed generato», «Giorni di accesso rimasti») e l'elenco dei
  parser. Nessun contatore finto: ciò che il backend non sa ancora dare non compare.
- **Parser**: elenco (titolo, slug, colonne mappate su 14, attivo/sospeso), «Crea
  nuovo parser» (POST `/api/me/parsers`), dettaglio con tab «Configurazione»,
  «Chat assegnate», «Log». Il wizard è quello di sempre (condizione → 14 colonne →
  riepilogo), ma **«Prova messaggio» gira sul server** (`POST
  /api/me/parsers/{slug}/test`, lo stesso `esegui_parser` del webhook, a secco): il
  CSV mostrato è quello del server byte per byte, e gli **`scarti`** — il perché un
  valore non ha raggiunto il feed (#39/#41/#42) — compaiono in un banner sotto
  l'esito. «Salva configurazione» fa la PUT; la prova salva prima di provare, perché
  il server conosce solo ciò che è salvato. L'anteprima locale accanto al wizard
  resta, dichiarata indicativa: «fa fede la prova sul server».
- **Feed CSV**: il token è **dell'utente**, non del parser — un solo URL da incollare
  in XTrader. «Genera token» / «Rigenera token» (`POST /api/me/token`) apre il modale
  una-volta-sola con token e URL completo; chiuso quello, la pagina mostra solo il
  prefisso. Rigenerare **è** la revoca, e la vista lo dice prima di farlo. La nota:
  ogni segnale resta 90 secondi, ogni parser ha riga e timer propri, il feed è UTF-8
  con BOM.
- **Chat Telegram** e **Log messaggi**: dichiarate «prossimamente», con la pillola e
  la spiegazione di cosa arriverà — non tabelle finte. Le rotte backend non esistono
  ancora (3.2 / 3.3c).
- **Impostazioni**: nome, stato, slug, prefisso del token (mai il token), e il link
  `t.me` del bot del servizio.

### La copia dimostrativa a file unico

`tools/build_single_file.py` concatena **`web/api_finta.js`** al posto di `api.js`:
la copia si apre da `file://`, dove `fetch` non esiste, e tutto vive in
`localStorage` (login demo con qualunque coppia, token finto). I due layer espongono
la stessa superficie, vincolata da `tests/web/test_api_parita.py`; il banner
«PROTOTIPO · DATI FINTI» resta solo lì.
