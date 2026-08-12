XTRADER SIGNAL RELAY

Servizio HTTPS per produrre un CSV compatibile con XTrader.

ENDPOINT PUBBLICO CSV
GET /xtrader.csv?token=TOKEN
Restituisce l'ultimo segnale ricevuto. Se non ci sono segnali restituisce la sola intestazione.

GESTIONE MULTI-PARSER
I parser vengono salvati nel database SQLite e possono essere creati/modificati senza cambiare il codice.
GET /api/parsers
POST /api/parsers con Header X-Admin-Token: TOKEN e body JSON:
{"name":"Parser_LIVE_1","header":"P.Bet. LIVE","market_name":"Over/Under 2,5 gol","market_type":"OVER_UNDER_25","selection_name":"Over 2,5 goal","handicap":"0","bet_type":"PUNTA"}
DELETE /api/parsers/NOME con Header X-Admin-Token.

PROFILI E FEED SEPARATI
Ogni profilo ha i suoi chat_id, il suo parser e il suo CSV indipendente.
GET /profiles/NOME.csv?token=TOKEN
GET /api/profiles
POST /api/profiles con Header X-Admin-Token e body JSON:
{"name":"MARCO","chat_ids":"-1001234567890,-1009876543210","parser":"Parser_LIVE_1"}
DELETE /api/profiles/NOME con Header X-Admin-Token.
Il profilo PIERO esiste sempre ed e' quello servito da /xtrader.csv.

PROVA DI UN PARSER
POST /api/parsers/NOME/test
Header: X-Admin-Token: TOKEN
Body JSON: {"message":"testo completo del messaggio"}

INSERIMENTO MANUALE (usa il parser predefinito o quello indicato)
POST /api/test-message?parser=Parser_Telegram_XTrader_v1
Header: X-Admin-Token: TOKEN
Body JSON: {"message":"testo completo del messaggio"}

AUTENTICAZIONE
CSV_ACCESS_TOKEN protegge dieci rotte: i due feed CSV (/xtrader.csv e
/profiles/NOME.csv, col parametro ?token=) e le otto API di gestione (con
l'header X-Admin-Token). Quattro sono in lettura, sei in scrittura.
Restano pubbliche soltanto /, /health, /telegram/webhook e /app.

Il controllo e' FAIL-CLOSED: se CSV_ACCESS_TOKEN non e' configurato il servizio
risponde 503 "servizio non configurato" a tutte le rotte protette, e NON le
lascia aperte. Un token sbagliato o assente nella richiesta da' invece 401: i due
codici distinguono "la tua chiave e' sbagliata" da "questo servizio va
configurato", che richiedono azioni diverse.
Prima dell'11/08/2026 il controllo era fail-OPEN: senza la variabile ogni rotta
rispondeva 200, feed e API di scrittura compresi, senza alcun errore nei log.
Cancellare quella variabile dalla dashboard rendeva il servizio scrivibile da
Internet. Non farlo: si controlla su /health.

/telegram/webhook non usa CSV_ACCESS_TOKEN — la chiama Telegram, non un client
nostro — ma NON e' aperta: pretende l'header X-Telegram-Bot-Api-Secret-Token e
risponde 403 senza. Il filtro dei chat_id NON e' quella protezione: fa
instradamento, decide a quale feed appartiene un messaggio, e non puo' autenticare
perche' il chat_id arriva dal corpo e quindi lo scrive il mittente. Prima del
secret_token questo endpoint era un percorso di SCRITTURA non autenticato verso i
segnali: misurato, un POST forgiato senza alcun token rispondeva 200 e la riga
entrava nel feed, mentre leggere lo stesso feed dava 401.
Il segreto e' DERIVATO da TELEGRAM_BOT_TOKEN, non e' una variabile da impostare:
esiste sempre dove esiste il bot, non sta nel repository, e Telegram lo riceve
alla registrazione all'avvio.
Senza TELEGRAM_BOT_TOKEN il webhook RIFIUTA TUTTO: senza il token non c'e' modo di
validare nessuna consegna, quindi questa istanza non ne accetta nessuna. Non che
non possano arrivarne - Telegram puo' consegnare attraverso una registrazione fatta
da un deploy precedente - ma un'istanza che non sa riconoscerle non ha niente da
guadagnare ad accettarle. Non c'e' variabile di override per lo sviluppo locale,
di proposito: sarebbe una scorciatoia che un domani finisce impostata in
produzione. Chi prova in locale imposta un TELEGRAM_BOT_TOKEN finto.
Se la registrazione fallisce l'enforcement RESTA attivo - legarlo all'esito
riaprirebbe la scrittura non autenticata ogni volta che la rete fa i capricci - e
il blackout si evita ritentando: tre volte all'avvio, e poi a ogni consegna
rifiutata. Attenzione a cosa dimostra una consegna rifiutata: SOLO che la
validazione dell'header e' fallita, non che venga da Telegram. Puo' essere un POST
forgiato, oppure Telegram con una registrazione stantia, e il ritentativo copre il
secondo caso senza dover distinguere - se la registrazione era vecchia la rimette
a posto e il segnale arriva col giro dopo, perche' Telegram ritenta le consegne.
Il ritentativo da richiesta ha un freno di 60 secondi: quel percorso lo
raggiunge chiunque, e senza freno una raffica di POST forgiati diventerebbe una
raffica di chiamate verso Telegram fatte da noi.

/app serve i file statici del prototipo: e' un mount, non una rotta, e non ha ne'
puo' avere un token perche' e' la pagina che si apre nel browser. Nulla di
sensibile deve finire in web/: lo vincola la guardia
tests/safety/test_static_mount.py, che controlla il tipo dei file E il loro
contenuto (token dalla forma nota, chat_id non dichiarati finti).

CONTROLLO
GET /health
Risponde {"status","csv","auth","webhook","feed_scartati"}, piu' "ultimo_scarto"
se feed_scartati non e' zero e "webhook_registrato" se un tentativo di
registrazione c'e' stato. "csv" e' l'esito del verificatore di formato;
"auth" vale "ok" oppure "non configurato" e in quel caso "status" diventa
"degraded" — a differenza degli scarti, una variabile mancante non si ripara da
se'. /health non ha token, quindi dice se il token c'e', mai quale;
"webhook" vale "protetto" (l'header viene preteso) o "chiuso senza bot", e in
quel caso ogni consegna viene rifiutata e "status" diventa "degraded": un'istanza
che non puo' ricevere nessun segnale non e' sana, e TELEGRAM_BOT_TOKEN mancante e'
una variabile mancante come CSV_ACCESS_TOKEN - non si ripara da se'.
"webhook_registrato" e' l'esito dell'ULTIMO tentativo di registrazione - all'avvio
o da una consegna rifiutata - e se e' false "status" diventa "degraded".

"status" e' "ok" solo con TUTTI E TRE gli assi a posto: formato CSV valido, token
del feed configurato, webhook protetto e registrato. Quando e' "degraded", il
campo che lo spiega e' sempre presente: non serve indovinare quale asse e' rotto.

"feed_scartati" conta le RIGHE DISTINTE salvate che non hanno passato la verifica
e sono state servite come feed vuoto - non le richieste che le incontrano, perche'
XTrader interroga il feed a raffica e una sola riga guasta resterebbe tale per
tutti i 90 secondi del TTL. La chiave e' la coppia profilo+riga, non la riga sola:
altrimenti due clienti colpiti dalla stessa riga guasta conterebbero come uno, e
due clienti con righe guaste diverse farebbero salire il contatore a ogni richiesta.
Uno scarto subito dopo un deploy e' atteso (riga scritta dalla versione
precedente, scade col TTL); un numero che continua a salire e' un guasto.
Il valore e' PER PROCESSO e si azzera al riavvio: con piu' worker o piu' istanze
ogni risposta riporta solo la propria quota, non un totale globale.
Il motivo non contiene mai il contenuto del segnale, e nemmeno lo stato in memoria
lo conserva (la riga si riconosce da un digest): /health e' un endpoint senza token.

DA CONTROLLARE DOPO UN DEPLOY, e non e' una formalita': verifica che /health dica
status ok e webhook_registrato true, poi manda un segnale di prova dal canale.

ASPETTA QUALCHE SECONDO PRIMA DI GUARDARE. La registrazione del webhook parte
DIETRO l'avvio, non davanti: il servizio risponde subito e la registrazione
prosegue in parallelo, ritentando fino a tre volte. Nei primi secondi /health dice
quindi status degraded, senza la chiave webhook_registrato: non e' un guasto, e' una
registrazione ancora in corso, e in quella finestra il relay davvero non puo'
ricevere niente.
QUANTO ASPETTARE: fino a un MINUTO, non trenta secondi. Nel caso peggiore i tre
tentativi durano ~33 secondi - 10 di timeout, 1 di pausa, 10, 2 di pausa, 10 - piu'
l'avvio del processo. A trenta secondi il terzo tentativo puo' essere ancora in
volo, quindi un degraded a quel punto non dice ancora niente. Se dopo un minuto non
e' diventato ok, allora e' un guasto e sotto c'e' scritto dove guardare.
(Qui c'era scritto "mezzo minuto": era un errore di aritmetica su una sequenza di
attese scritta due paragrafi piu' su, e avrebbe fatto rincorrere un non-guasto.
Segnalato da CodeRabbit.)
Come distinguere i tre casi, guardando insieme "webhook" e "webhook_registrato":
  webhook "chiuso senza bot"            -> manca TELEGRAM_BOT_TOKEN
  webhook "protetto", chiave assente    -> registrazione in corso, o mai tentata
  webhook "protetto", registrato false  -> tentata e fallita: guarda PUBLIC_URL,
                                           la rete, il token del bot
Nel terzo caso i log del servizio dicono il TIPO del guasto - "registrazione
webhook: chiamata fallita (URLError)" e' rete o DNS, "(timeout)" e' Telegram che
non risponde, "(HTTPError)" e' Telegram che rifiuta token o URL, "(JSONDecodeError)"
e' una risposta non interpretabile. Il tipo e non il messaggio, di proposito: il
messaggio di un'eccezione di urllib puo' contenere l'URL, e l'URL contiene il token
del bot. Un token nei log a ogni guasto di rete non e' un compromesso accettabile
per una diagnosi piu' comoda.
"non ancora tentato" e "tentato e fallito" sono stati diversi, e la differenza dice
se aspettare o intervenire.
Prima questa attesa stava DAVANTI all'avvio: uvicorn non serviva finche' la
registrazione non era finita, quindi con la rete lenta /health non rispondeva per
oltre trenta secondi dopo ogni deploy. Un handler di startup ASGI deve terminare
perche' il processo sia pronto, e metterlo in un thread libera l'event loop ma non
la readiness: sono due cose diverse. Se fosse false,
Telegram puo' conservare una registrazione vecchia SENZA segreto e consegnare senza
header: il relay rifiuta, ritenta la registrazione da se' (freno di 60 secondi) e i
segnali riprendono col giro dopo. Se resta false, il problema e' PUBLIC_URL, la
rete, o il token del bot - e li' serve guardare.

Il parser attuale riconosce un messaggio contenente "P.Bet. PREMACHT 0,5HT", cerca la riga con 🆚, prende il testo successivo e converte " v " in " - ".

CSV: 14 colonne, virgola, campi tra virgolette, terminatore CRLF, UTF-8 con BOM,
     colonna finale Points. Verificato da verify_csv() prima di ogni scrittura.

FACCIATA DEL SITO
GET / restituisce la pagina pubblica di BetRelay (web/sito.html), non piu' l'oggetto
JSON {"service","status","csv"}. Chi sonda il servizio in automatico usa /health, che
resta JSON: la facciata e' per le persone. Se web/sito.html manca — un deploy senza la
cartella web/ — la rotta torna al JSON di prima invece di rispondere 500.

E' una ROTTA ESPLICITA, non un mount di file statici sulla radice, e non deve
diventarlo: un catch-all in stile SPA (@app.get('/{resto:path}')) trasformerebbe ogni
percorso sconosciuto in una pagina, e il giorno che nasce /feed/NOME.csv XTrader
riceverebbe text/html con stato 200 al posto di un CSV. Lo vincola
tests/relay/test_facciata.py, che prova anche la variante sbagliata.

La pagina e' indicizzabile di proposito (nessun noindex): un sito che nessuno trova
non e' un sito. /app resta noindex, perche' non ha niente da indicizzare.

PROTOTIPO WEB APP
Il prototipo dell'interfaccia multiutente e' servito su /app (file statici in web/).
Usa dati finti nel browser, non tocca il relay. Architettura e contratto API in SAAS.md.
Il pulsante «Entra» della facciata porta qui.

VARIABILI RAILWAY
CSV_ACCESS_TOKEN: token segreto che protegge i due feed CSV E tutte le API di
  gestione. OBBLIGATORIA: senza, il servizio risponde 503 a tutte le rotte
  protette (vedi AUTENTICAZIONE).
TELEGRAM_BOT_TOKEN: token del bot; se presente il webhook viene registrato all'avvio.
  OBBLIGATORIA per ricevere i segnali: da essa si deriva il segreto del webhook,
  quindi senza, /telegram/webhook rifiuta OGNI consegna con 403 (vedi
  AUTENTICAZIONE). Chi legge solo questo elenco non deve poterla credere
  facoltativa: segnalato da CodeRabbit.
PUBLIC_URL: URL pubblico del servizio, usato per registrare il webhook. Se manca,
  il codice usa un default cablato sull'URL Railway: impostarla sempre, perche' un
  secondo deploy senza questa variabile ripunterebbe il webhook del bot vero.
TELEGRAM_ALLOWED_CHAT_IDS: chat_id iniziali del profilo PIERO, separati da virgola.
  TRANSITORIA, e ha una scaletta di pensionamento in SAAS.md. Su un database
  persistente diventa INERTE: il seed usa INSERT OR IGNORE, quindi la riga PIERO
  esiste gia' e modificare la variabile non cambia piu' nulla — per cambiare i
  chat_id si usa POST /api/profiles. Non replicarla per altri utenti: popola solo
  il profilo PIERO, e una variabile per cliente imporrebbe un rideploy.
DB_PATH: facoltativo; su Railway usare un volume per conservare i dati tra riavvii.

I valori di mercato non sono piu' variabili d'ambiente: parser e profili vivono nel
database e si gestiscono dalle API /api/parsers e /api/profiles.

NON INSERIRE TOKEN, PASSWORD, APP KEY O CERTIFICATI NEL REPOSITORY.
