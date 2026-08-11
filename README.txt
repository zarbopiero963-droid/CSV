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

CONTROLLO
GET /health

Il parser attuale riconosce un messaggio contenente "P.Bet. PREMACHT 0,5HT", cerca la riga con 🆚, prende il testo successivo e converte " v " in " - ".

CSV: 14 colonne, virgola, campi tra virgolette, terminatore CRLF, UTF-8 con BOM,
     colonna finale Points. Verificato da verify_csv() prima di ogni scrittura.

PROTOTIPO WEB APP
Il prototipo dell'interfaccia multiutente e' servito su /app (file statici in web/).
Usa dati finti nel browser, non tocca il relay. Architettura e contratto API in SAAS.md.

VARIABILI RAILWAY
CSV_ACCESS_TOKEN: token segreto per proteggere CSV e inserimento.
TELEGRAM_BOT_TOKEN: token del bot; se presente il webhook viene registrato all'avvio.
PUBLIC_URL: URL pubblico del servizio, usato per registrare il webhook.
TELEGRAM_ALLOWED_CHAT_IDS: chat_id iniziali del profilo PIERO, separati da virgola.
DB_PATH: facoltativo; su Railway usare un volume per conservare i dati tra riavvii.

I valori di mercato non sono piu' variabili d'ambiente: parser e profili vivono nel
database e si gestiscono dalle API /api/parsers e /api/profiles.

NON INSERIRE TOKEN, PASSWORD, APP KEY O CERTIFICATI NEL REPOSITORY.
