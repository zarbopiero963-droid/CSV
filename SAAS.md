# XTrader Signal Relay — architettura SaaS

Documento di riferimento per la trasformazione del relay mono-utente in servizio
multiutente. Descrive il modello dati, il contratto API e le regole di isolamento
concordate. Il prototipo della web app in `web/` implementa già questo contratto
con dati finti.

## Modello concettuale

L'entità centrale è il **parser**, non il profilo. È il parser che possiede
configurazione, token, feed CSV, timer dei 90 secondi e log.

```
utente (login Telegram)
 └── parser  (slug, token proprio, config propria, feed proprio, timer proprio)
      └── N chat Telegram   ←→   una chat può alimentare N parser dello stesso utente
```

Il "profilo" si riduce allo slug dell'utente nell'URL del feed:

```
/profiles/PIERO/over-15-premacht.csv?token=...
/profiles/PIERO/over-25-live.csv?token=...
/profiles/MARCO/handicap-asiatico.csv?token=...
```

## Modello dati

```
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

Il formato di uscita non cambia ed è il contratto con XTrader: 14 colonne, tutti
i campi tra virgolette, separatore virgola, terminatore CRLF, UTF-8 senza BOM.

## Contratto API

Implementato con dati finti in `web/api.js`, un commento per endpoint.

```
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

## Prototipo

```
uvicorn main:app --reload
```

Poi `http://127.0.0.1:8000/app/`. I dati vivono in `localStorage`, si azzerano da
Impostazioni. Gli endpoint del relay esistente non sono stati modificati.
