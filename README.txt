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

CSV: 14 colonne, virgola, campi tra virgolette, UTF-8 senza BOM, colonna finale Points.

VARIABILI RAILWAY
CSV_ACCESS_TOKEN: token segreto per proteggere CSV e inserimento.
PARSER_HEADER: header da riconoscere.
MARKET_NAME, MARKET_TYPE, SELECTION_NAME, HANDICAP, BET_TYPE: valori personalizzabili.
DB_PATH: facoltativo; su Railway usare un volume per conservare i dati tra riavvii.

NON INSERIRE TOKEN, PASSWORD, APP KEY O CERTIFICATI NEL REPOSITORY.
