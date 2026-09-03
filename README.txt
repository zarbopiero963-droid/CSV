XTRADER SIGNAL RELAY

Servizio HTTPS per produrre un CSV compatibile con XTrader.

COME VA CONFIGURATA LA FONTE IN XTRADER  (obbligatorio, altrimenti il feed non funziona)
XTrader legge il feed da Funzioni > Segnali (F11), tabella "Fonti": si aggiunge una fonte
con l'URL del feed, la ricarica automatica e il suo intervallo. Due impostazioni di quella
fonte non sono opzionali per noi.

1) RICONOSCIMENTO DELLE SELEZIONI: PER NOME, mai per ID.
   XTrader sa individuare la selezione in due modi: dagli id Betfair (MarketId +
   SelectionId) oppure dai nomi (EventName + MarketType + SelectionName). Noi possiamo
   usare SOLO il secondo, e non e' una preferenza: risolvere gli id richiede l'API di
   Betfair Exchange, che il relay non ha. Per questo EventId, MarketId e SelectionId
   escono SEMPRE vuoti dal nostro feed -- e' voluto, non incompleto.
   Conseguenza: con la fonte impostata sul riconoscimento per id il feed non produce
   nessuna scommessa, e XTrader non segnala un errore -- mostra un'icona rossa accanto al
   segnale. Va impostato il riconoscimento per nome.
   Ne segue anche quali colonne sono davvero obbligatorie: EventName, MarketType,
   SelectionName (piu' BetType, che non serve al riconoscimento ma dice se puntare o
   bancare). Sono le stesse quattro di COLONNE_OBBLIGATORIE in main.py.

2) LINGUA DELLA FONTE: la stessa dei nomi che scriviamo.
   Il riconoscimento per nome confronta i nostri EventName / MarketType / SelectionName
   con il palinsesto Betfair nella lingua impostata sulla fonte. Se le due lingue non
   coincidono, nessuna selezione viene trovata.
   Per XTrader Italia: ITA. La famiglia Betting Toolkit usa ENG o ES, ed e' lo stesso asse
   del separatore decimale e di BetType (vedi sotto): oggi il servizio scrive la sola
   forma italiana.

COLONNA PROVIDER: VUOTA DA CONTRATTO
Provider e' il nome di CHI MANDA il segnale, non di chi lo legge: XTrader e' il
consumatore, quindi scriverci "XTrader" e' sbagliato. Da contratto la colonna esce VUOTA.
L'utente puo' valorizzarla come vuole quando configura il proprio parser -- serve a lui,
perche' XTrader la usa come filtro ("solo i segnali di quel provider") e come
discriminante fra segnali altrimenti identici. Il confronto non distingue maiuscole.

NIENTE EMOJI NEL CSV -- IN NESSUNA COLONNA
Regola di contratto, non un'avvertenza: nel CSV servito a XTrader non deve comparire
nessuna emoji, in nessun campo. Un segnale che ne contiene viene marcato NON VALIDO, e --
come sempre -- senza restituire un errore: solo un'icona rossa accanto al segnale.

Dal PR della #42 la regola e' VINCOLATA, non solo dichiarata: un valore con emoji
viene SCARTATO dal motore col motivo che dice cosa fare (il messaggio intero non
produce riga, come per i valori numerici storti della #39), e verify_csv respinge
un feed che ne contenga in qualunque colonna. E il suggeritore propone Provider
VUOTA: e' il nome di chi MANDA il segnale, campo dell'utente.

Le emoji stanno IN ENTRATA, non in uscita. I marcatori dei parser (🆚, ⏰, ✅) servono a
riconoscere il messaggio e a dire DOVE leggere il dato: il valore estratto e' il testo
DOPO il marcatore, mai il marcatore. E' la ragione per cui il parser di riferimento usa
"testo dopo 🆚" e non "riga intera": una regola che prende la riga intera si porta
l'emoji dentro EventName. Prima della #42 quel feed usciva formalmente valido -- 14
colonne, virgolette, CRLF, BOM -- e XTrader lo scartava in silenzio; oggi la guardia
lo ferma a monte, col motivo che dice di estrarre il testo dopo il marcatore.

INTERVALLO DI RICARICA DELLA FONTE
XTrader consente di impostare l'intervallo da 1 secondo in su. Il TTL del feed e' 90
secondi, quindi qualunque intervallo ragionevole sta molto sotto: un segnale non puo'
nascere e morire fra due letture. Un segnale gia' riconosciuto NON viene riletto come
nuovo se la riga resta nel feed per tutti i 90 secondi: e' XTrader a evitare la doppia
scommessa, non lo svuotamento.

FEED A N RIGHE ATTIVE (#35 pezzo 1)
Un messaggio puo' produrre N righe CSV (multi-mercato/multi-selezione): il
feed le serve TUTTE, composte in un documento solo — BOM e intestazione UNA
volta in testa, poi le data line nell'ordine di scrittura, ciascuna con CRLF.
store_signal accetta la lista di documenti e li verifica TUTTI prima di
scrivere (uno rotto = niente scritto: mai mezzo segnale nel feed). Il TTL
resta 90 secondi ed e' PER RIGA nel filtro di lettura: alla scadenza di una
riga il feed perde solo quella. Una riga guasta nel database degrada solo se
stessa, le altre continuano a uscire. Feed vuoto = la sola intestazione,
come sempre.

IL MOTORE MULTI-RIGA (#35 pezzo 2)
Le N righe le genera config.multi del parser: la riga BASE (le 14 regole di
sempre) e' il modello, ogni riga di multi.markets/multi.selections dice solo
cosa cambia e il resto eredita. Somma, non prodotto; enabled=false non
genera; ogni riga e' giudicata DA SOLA, quindi una rotta non ferma le altre:
il segnale esce con le k buone su N e gli scarti delle altre finiscono nei
log messaggi come "riga N: motivo" (anche quando cade per obbligatorie
mancanti). Selezione vuota + delimitatori = una riga per punteggio N-N
trovato, solo su CORRECT_SCORE/HALF_TIME_SCORE e con un tetto di 36
punteggi per riga (0-0..5-5): oltre, la riga e' un errore di config
segnalato, non migliaia di documenti nel feed. Una voce di multi e' una
riga solo se e' un oggetto non vuoto. Il
gate "solo costanti" (#41) vale per riga: le colonne sovrascritte non
contano come estratte. La forma sbagliata di config.multi viene respinta al
salvataggio con 422; MAX_RIGHE_MULTI (default 20, si alza da variabile su
Railway) limita le righe dichiarate, anche le spente. La prova del parser
risponde le righe col loro esito e il CSV composto delle sole complete —
gli stessi byte che scriverebbe il webhook. Specifica: SAAS.md, «Il
multi-riga».

FORME LOCALIZZATE DEL CSV
Tre cose dipendono dalla lingua del prodotto che legge il feed, e oggi ne serviamo una.

   prodotto                     separatore decimale   BetType
   XTrader Italia  (oggi)       virgola  "1,85"       PUNTA / BANCA
   Betting Toolkit (in futuro)  punto    "1.85"       BACK / LAY

BACK/LAY e' la nomenclatura Betfair generica che compare nel manuale di XTrader; il
prodotto italiano scrive PUNTA/BANCA, ed e' cio' che XTrader stesso produce quando
esporta un CSV.
Dal PR della #40 il separatore e' imposto dal CONFINE DI SCRITTURA del motore, non
dalla regola dell'utente: i valori numerici accettati dalla guardia escono nella
forma localizzata (virgola), qualunque separatore avesse il messaggio o producesse
una trasformazione. verify_csv respinge un campo numerico col punto e il suggeritore
non propone piu' comma_to_dot su Price. Le config esistenti NON vanno migrate --
comma_to_dot resta legale e tutte le config producono lo stesso feed fra loro --
ma il CONTENUTO del feed cambia rispetto a prima della #40: dove usciva "1.85"
ora esce "1,85". E' il fix, non un effetto collaterale.

IL PERCORSO LEGACY GIUDICA COME IL MOTORE (audit #81, C1/C2)
I parser storici senza config_json (PIERO) passavano per parse_message, che
costruiva la riga grezza SENZA localizzare i numerici e SENZA le guardie: un
handicap col punto o un'emoji nell'evento venivano scartati da verify_csv in
silenzio, senza una riga di causa. Ora parse_message passa dalla STESSA
_giudica_riga del motore (regola 3): l'handicap col punto esce localizzato con la
virgola (come il motore, sopra), e un segnale fermato dal giudizio scrive il
PERCHE' in message_logs ("scartato: ...") invece di sparire. Su un messaggio
valido i byte non cambiano -- _giudica_riga non tocca un valore gia' corretto --
quindi il feed di PIERO resta identico.

ENDPOINT PUBBLICO CSV
GET /xtrader.csv?token=TOKEN
Restituisce TUTTE le righe vive (non scadute) in ordine di scrittura, composte
in un documento solo. Se non ci sono segnali restituisce la sola intestazione.

IL NOME DEL FILE SCARICATO (#60)
Chi incolla l'URL del feed in un browser scarica un file, e quel file si
chiama betrelay, non xtrader: le risposte CSV portano
Content-Disposition: attachment con filename betrelay.csv (/xtrader.csv),
betrelay-SLUG.csv (/feed/SLUG.csv) e betrelay-NOME.csv (/profiles/NOME.csv).
E' SOLO il nome del download: URL, status, content-type e byte del corpo non
cambiano — XTrader legge il corpo e ignora l'intestazione. Il nome viene
ripulito a [A-Za-z0-9._-] prima di entrare nell'header (un nome profilo con
virgolette o caratteri non-ASCII non deve rompere la consegna). Fonte unica:
_intestazioni_feed() in main.py, vincolata da tests/relay/test_nome_download.py.

IL TOKEN NON FINISCE NEI LOG (audit #81)
Il token del feed viaggia nel query string (?token=...), e l'access-log di
uvicorn formatta l'URL richiesto: senza precauzioni ogni poll di XTrader
scriverebbe il token in chiaro nei log del container. All'avvio il servizio
installa una redazione sul logger uvicorn.access (installa_redazione_access_log()
in main.py) che sostituisce il valore con token=[REDACTED] lasciando il resto
della riga leggibile. Vale anche per l'alias /xtrader.csv?token=... . Vincolata
da tests/relay/test_redazione_log.py, che esercita il vero AccessFormatter di
uvicorn. L'URL e il corpo non cambiano: e' solo cio' che finisce nei log.

IL FEED PER UTENTE (il percorso nuovo; /xtrader.csv resta l'alias del profilo PIERO)
GET /feed/SLUG.csv?token=xt_...
E' il feed di UN utente, autenticato dal SUO token, non da CSV_ACCESS_TOKEN.
Ogni fallimento risponde 404, sempre lo stesso: slug inesistente, token assente,
token sbagliato, token di un altro utente. Un 401 su uno slug esistente direbbe
a chi enumera "questo cliente esiste, cerca il token"; il 404 uniforme non
conferma niente. Alla scadenza dell'accesso il feed risponde 200 con la sola
intestazione (per XTrader e' "nessun segnale", non un guasto) e il token NON
viene revocato: al rinnovo il cliente non riconfigura XTrader.

POST /api/me/token          (autenticazione a sessione, come /api/me)
Conia o rigenera il token del feed. Il token in chiaro esiste SOLO in questa
risposta: il server salva sha256(token) e da quel momento puo' solo verificare.
La risposta porta {token, token_prefix, feed}; /api/me restituisce slug e
token_prefix (i primi 9 caratteri, per riconoscere il token in UI), mai il token.
Rigenerare sovrascrive l'hash: il token precedente smette di aprire il feed
alla richiesta successiva. Chi non ha ancora uno slug (utenti nati dal login
Telegram) lo riceve qui, derivato dal nome, minuscolo, stabile.

GESTIONE MULTI-PARSER (ADMIN)
I parser vengono salvati nel database SQLite e possono essere creati/modificati senza cambiare il codice.
GET /api/parsers
POST /api/parsers con Header X-Admin-Token: TOKEN e body JSON:
{"name":"Parser_LIVE_1","header":"P.Bet. LIVE","market_name":"Over/Under 2,5 gol","market_type":"OVER_UNDER_25","selection_name":"Over 2,5 goal","handicap":"0","bet_type":"PUNTA"}
DELETE /api/parsers/NOME con Header X-Admin-Token.

PARSER DELL'UTENTE (SESSIONE)
Ogni cliente crea/modifica/elimina SOLO i propri parser, autenticandosi con la
sessione (il cookie di login), non col token del feed. user_id viene dalla sessione,
mai dal corpo; su un parser di un altro la risposta e' 404 (non 403).
GET    /api/me/parsers                  i parser dell'utente
POST   /api/me/parsers                  body {"titolo":"Test 1","config":{...},"active":true}
PUT    /api/me/parsers/SLUG             aggiorna il proprio (lo slug non cambia con la rinomina).
                                        Con "versione" nel body (quella letta) la PUT e'
                                        CONDIZIONATA: se un'altra sessione ha salvato nel
                                        frattempo risponde 409 "ricarica il parser: e' stato
                                        modificato altrove" e non sovrascrive (#51). Senza
                                        "versione" resta incondizionata; la versione del
                                        parser avanza a ogni modifica e sta nella vista.
                                        PUT e DELETE identificano la riga con "uid" (#73),
                                        un id mai riusato: se il parser e' stato eliminato
                                        e RICREATO con lo stesso slug, il parser nuovo non
                                        viene toccato.
                                        "uid" sta nella vista del parser e il client lo
                                        rimanda come SECONDA precondizione (#75): nel body
                                        della PUT, in ?uid= sulla DELETE. Se lo slug ora
                                        e' di un'altra riga -> 409 "ricarica il parser:
                                        e' stato eliminato e ricreato altrove". Serve
                                        perche' il parser ricreato riparte da versione 1,
                                        cioe' proprio il valore che la scheda rimasta
                                        aperta ha in cache: il contatore da solo non
                                        distingue le due righe. Senza "uid" la richiesta
                                        resta incondizionata, come senza "versione".
DELETE /api/me/parsers/SLUG[?uid=UID]   elimina il proprio (uid = precondizione, #75)
POST   /api/me/parsers/SLUG/test        body {"message":"..."} -> {matched,missing,scarti,diagnosi,complete,event?,csv?}

CHAT VERIFICATE DALL'UTENTE (#32, pezzo 3.2)
Fino a qui l'unico modo di autorizzare un canale era POST /api/profiles con
X-Admin-Token, cioe' il proprietario a mano. Ora il cliente lo fa da solo:
chiede un codice, lo INCOLLA NEL CANALE, il webhook lo riconosce e registra la
chat come sua. Incollarlo nel canale e' la prova del controllo: chi non puo'
scrivere li' dentro non puo' autorizzarlo.
POST   /api/chats/verify/start          -> {"codice":"BETRELAY-XXXXXXXX","scade_fra_s":600}
                                        Il codice esiste IN CHIARO UNA VOLTA SOLA,
                                        come il token del feed. Ogni chiamata invalida
                                        il codice precedente dello stesso utente.
GET    /api/chats/verify/status          -> {"in_attesa":bool,"scaduto":bool,
                                            "scade_fra_s":N,"chat":{...}|null}
                                        Per il sondaggio della web app. NON ripete il
                                        codice: chi l'ha chiesto ce l'ha gia'.
                                        "chat" e' la chat verificata DA QUESTO codice,
                                        non l'ultima che l'utente possiede: altrimenti
                                        un codice scaduto mostrerebbe un canale vecchio
                                        come se fosse l'esito.
GET    /api/chats                        le chat verificate dall'utente
                                        -> [{"id":N,"telegram_chat_id":"-100...",
                                             "titolo":"Canale segnali","tipo":"channel",
                                             "verified_at":N}, ...]
                                        `id` e' la chiave del servizio (chats.id), NON
                                        il numero di Telegram: e' quello che vogliono
                                        DELETE /api/chats/ID e la PUT qui sotto.
DELETE /api/chats/ID                     toglie una propria chat E i suoi link
GET    /api/me/parsers/SLUG/chats        -> {"chat_ids":[...]}
PUT    /api/me/parsers/SLUG/chats        body {"chat_ids":[1,2]} sostituisce l'insieme
Il codice vive 600 secondi ed e' usa-e-getta (consumato alla prima consegna
valida). Un codice scaduto, gia' consumato o inventato non registra niente. Una
chat gia' di un altro utente NON e' rubabile: il codice in quel caso non viene
nemmeno consumato. Vale anche per una chat SENZA proprietario (quelle create dal
percorso legacy dei profili): non si adotta, perche' puo' portare link ai parser
di ALTRI utenti. Su chat o parser di un altro la risposta e' 404 (non 403).
Chiedere un codice e collegare una chat richiedono un accesso ATTIVO: un utente
solo `registrato` riceve 403. Lo stato si ricontrolla anche al CONSUMO del
codice: chi viene sospeso nei 600 secondi fra la richiesta e l'incollata non
registra piu' niente (e il codice non viene bruciato, cosi' resta valido se
l'accesso torna). Senza, la verifica sarebbe una porta di servizio
verso i segnali senza passare dall'attivazione del proprietario.
DELETE /api/chats/ID toglie SOLO i link dei parser di chi chiama: una chat puo'
portare link altrui, e cancellarli tutti fermerebbe i segnali di un altro utente
in silenzio. Se ne restano, la riga di `chats` non si cancella (lascerebbe
orfani) ma viene DISCONOSCIUTA: sparisce dalla lista di chi l'ha tolta.
NOME E TIPO DEL CANALE (PR 2 del 3.2). `chats.title` e `chats.type` esistevano
dalla prima migrazione e non li scriveva NESSUN percorso: finche' le chat le
collegava l'amministratore non si vedeva, era lui a sapere quale canale fosse
quale. Da quando le collega il cliente la lista e' la sua schermata, e una lista
di interi negativi non e' una schermata. Ora arrivano dalla STESSA consegna che
porta il codice (message.chat.title / .type), senza nessuna chiamata in piu' a
Telegram. Il titolo e' testo di un ESTRANEO — chi controlla il canale ne sceglie
il nome — quindi e' capato a MAX_TITOLO_CHAT (96 caratteri) e ripulito dei
caratteri non stampabili. Una riverifica lo AGGIORNA (un canale rinominato non
resta col nome vecchio); una consegna senza titolo non lo cancella. I due
percorsi legacy non possono passare di qui: l'amministratore scrive una lista di
id e un titolo non esiste, quindi per quelle righe la web app mostra il numero.
LIMITE NOTO, non introdotto qui: i topic dei forum Telegram non sono supportati.
`message_thread_id` non lo scrive nessun percorso e ogni ricerca usa la chat
radice, quindi verificare in un topic autorizza il gruppo intero.

SECONDO LIMITE NOTO, e la prova NON e' la stessa per tutte le chat. Il codice
dimostra che chi lo presenta PUO' SCRIVERE in quella chat. Per un CANALE questo
coincide col controllarlo (su Telegram in un canale scrivono solo gli
amministratori); per un GRUPPO no, perche' scrive qualunque membro: un membro
ordinario con un account BetRelay puo' rivendicare il gruppo, e da quel momento
nessun altro lo verifica piu' (`chat_non_disponibile`). Non e' un accesso ai
dati di un altro utente — nessun parser, feed o token diventa leggibile, e chi
rivendica vedeva gia' quei messaggi da membro — ma e' la possibilita' di soffiare
la verifica al titolare e di dirottare quel flusso nei propri parser.
CHIUSO dalla #116 per chi usa il percorso principale, e vale la pena dire come:
NON con `getChatMember`, che era la strada scritta qui prima. Promuovere il bot ad
amministratore Telegram lo consente solo a chi e' gia' amministratore, e
`my_chat_member` porta `from`, cioe' chi l'ha fatto, attestato da Telegram: la
prova di ruolo arriva GRATIS, senza chiamate in uscita e senza i loro modi di
fallire. Il codice usa-e-getta resta come RIPIEGO, e il ripiego si porta dietro
la sua prova debole: per questo l'avviso in schermata non va tolto, va spostato
sul ripiego. `[REAL_FINDING]` di OpenRouter Sol al gate della PR #114.

COLLEGARE UNA CHAT PROMUOVENDO IL BOT (#116). L'utente aggiunge @Betrelay_bot come
AMMINISTRATORE del canale o del gruppo; Telegram manda un `my_chat_member` con
`from` = chi l'ha promosso e `new_chat_member.status` = `administrator`/`creator`;
il servizio registra la chat a nome di quell'utente. Quattro cancelli, ognuno col
suo test in `tests/relay/test_promozione_bot.py`:
  - chat PRIVATA: niente, li' il ruolo di amministratore non esiste;
  - chi promuove dev'essere un utente del servizio (altrimenti non c'e' nessun
    proprietario da scrivere, e non si inventa);
  - e dev'essere ATTIVO, o l'amministratore: stesso cancello del codice;
  - una chat gia' di un ALTRO utente non e' rubabile — e qui il caso e' concreto,
    perche' due persone possono essere entrambe amministratrici della stessa chat.
Se il bot viene tolto o retrocesso (`left`/`kicked`/`member`/`restricted`) la riga
NON si cancella: si aggiorna `chats.bot_stato`. I link ai parser restano, cosi'
rimettere il bot fa tornare tutto senza riconfigurare. Cancellare butterebbe via
la configurazione per una retrocessione magari temporanea, o fatta da un altro
amministratore della chat.

CONFLITTO COL CANALE DI BACKUP (#56), e come e' stato risolto. `_cattura_canale_backup`
intercetta i `my_chat_member` e si ferma quando chi promuove e' l'amministratore, la
chat e' un canale PRIVATO e il bot diventa amministratore: cioe' esattamente quando il
proprietario promuove il bot in un proprio canale di SEGNALI. I due effetti ora NON si
escludono — il collegamento gira prima, la cattura del backup resta identica — quindi
la chat si collega E la proposta di backup resta.
Un canale pero' non puo' essere destinazione dei backup e sorgente di segnali insieme:
una riga in `chats` lo iscrive all'instradamento del webhook. L'invariante sta sulla
CONFIGURAZIONE, non sulla proposta, ed e' tenuta sui due versi:
  - una promozione sul canale GIA' configurato come backup non lo collega;
  - confermare un candidato che e' anche una chat collegata: con link ai parser → 409
    col motivo (sono lavoro dell'utente, non si cancellano da soli); senza link, la riga
    e' solo l'effetto automatico della promozione e si toglie.
Il secondo ramo non e' un dettaglio: col rifiuto secco, configurare un canale di backup
sarebbe diventato IMPOSSIBILE — la promozione collega, la conferma rifiuta.

I DUE rami del webhook (il codice e la promozione) sono le UNICHE eccezioni al filtro
delle chat, e sono tutta l'eccezione: registrano una riga in `chats` e niente altro —
non toccano `signals`, non cercano parser, non scrivono in `message_logs`.

ATTENZIONE, e vale anche per il percorso legacy: la verifica dimostra che
l'utente puo' SCRIVERE nel canale, non che il bot possa LEGGERNE i messaggi
futuri. Sono due cose diverse e la seconda dipende da Telegram:
  - canale: il bot deve esserne amministratore;
  - gruppo/supergruppo: con la privacy mode attiva (il default di BotFather) il
    bot riceve solo i messaggi che lo menzionano o i comandi. Serve
    /setprivacy Disable, oppure renderlo amministratore del gruppo.
Il sintomo di questa configurazione mancante e' una chat verificata e collegata
che non produce nessun segnale, senza errori da nessuna parte: se succede,
controllare qui prima del parser.
La config viene validata alla creazione (struttura + dry-run): una config storta da'
422 col motivo. La prova (/test) e' a secco: non scrive nel feed di nessuno, e dice
se la condizione ha combaciato e quali colonne obbligatorie mancano.
DIAGNOSI PER COLONNA (#25). `diagnosi` porta UNA voce per ognuna delle 14 colonne -
{colonna, stato, motivo, valore} - cosi' la domanda «perche' questo messaggio non ha
prodotto un segnale» ha una risposta a quattordici righe, non a tre livelli globali.
Gli stati sono quattro, su DUE livelli di gravita' deliberatamente distinti:
  blocca  - senza questa colonna la riga NON esce (obbligatoria vuota, o valore
            scartato dalle guardie #39/#41/#42). Sono le cause di complete=false;
  segnala - c'e' qualcosa da sapere ma la riga ESCE lo stesso (gli avvisi, es. la
            squadra senza alias nella sorgente);
  ok      - valorizzata e senza problemi;
  vuota   - vuota ma facoltativa: NON e' un errore (Price vuota e' il caso normale,
            la quota la mette XTrader).
I motivi sono gli STESSI che finiscono in scarti/avvisi (gia' azionabili: dicono cosa
fare), piu' uno solo nuovo per l'obbligatoria vuota - cosi' non esiste un secondo
catalogo da tenere allineato fra i due motori. La diagnosi nasce DENTRO il motore
(non accanto), quindi la prova non puo' divergere dal webhook, e il test di parita'
JS<->Python la confronta per intero.
COME SI COSTRUISCE, e perche' conta. La diagnosi si calcola sui motivi FINALI di UNA
riga - i suoi missing, i suoi scarti, i suoi avvisi - agganciati alla colonna che
nominano in testa ("Price: ..."). E' l'unica regola d'attribuzione, la stessa per
scarti e avvisi, in Python come in JS. Calcolarla a meta' strada la faceva mentire:
il gate #41 e le cause delle righe di override arrivano DOPO, e la tabella diceva
«0 colonne bloccano» mentre il CSV non usciva.
DIAGNOSI PER RIGA. Col multi-riga (#35) il verdetto e' di ogni riga, quindi ogni
elemento di `righe` porta la SUA `diagnosi`: la tabella top-level descrive la BASE,
che con config.multi non e' una riga del feed. Il pannello mostra una tabella per
riga generata, e la tabella unica solo quando il multi non e' attivo.
CAUSE DI RIGA. Uno scarto che non nomina nessuna colonna - oggi il gate di contenuto
#41, che parla del parser nel suo insieme - non finisce in nessuna voce: attribuirlo
a una colonna sarebbe falso. Non si perde: resta in `scarti`, il pannello lo mostra
sotto la tabella, e il conteggio del riepilogo lo include ("N sulla riga").
MESSAGGIO NON RICONOSCIUTO. Nessuna colonna «blocca»: la riga non esce perche' la
condizione non ha combaciato, e accusare le obbligatorie vuote manderebbe l'utente a
mappare colonne mentre il difetto e' nella condizione.
IL MOTIVO DELLA REGOLA CHE NON HA ESTRATTO. Una colonna vuota ha due cause diverse, e
il consiglio da dare e' opposto: se non e' mappata va mappata; se E' mappata e la
regola non ha trovato niente, dire «mappala» consiglia una cosa gia' fatta e tace la
causa vera. Il motivo lo scrive il ramo di _estrai_valore / extractValue che il vuoto
lo ha appena prodotto - non una funzione che ri-ispeziona la regola dopo, che
divergerebbe dal runtime al primo ramo che cambia. Per sorgente:
  non mappata / empty  -> «e' obbligatoria e non e' mappata su nessuna sorgente...»
                          (sulle FACOLTATIVE non mappate nessun motivo: dieci righe
                           di «non e' mappata» sarebbero rumore, non diagnosi)
  constant vuota       -> «la costante impostata nella regola e' vuota.»
  message              -> «la regola prende il messaggio intero, e il messaggio e' vuoto.»
  line, riga assente   -> «nel messaggio non c'e' nessuna riga che contiene «ANCORA»...»
  line, marcatore assente -> «la riga con «ANCORA» c'e', ma non contiene «MARCATORE»...»
  regex senza match    -> «l'espressione regolare «PATTERN» non ha trovato
                          corrispondenza in questo messaggio.» (stesso motivo anche per
                          un pattern che non compila: in Python i due casi sono
                          indistinguibili, e una regex non compilabile non e' salvabile)
  regex flags non testo -> «la regola ha un campo «flags» che non e' testo...»
  trasformazioni       -> «la sorgente ha estratto un valore, ma le trasformazioni
                          della regola lo hanno svuotato...»
Su una OBBLIGATORIA il motivo accompagna lo stato «blocca»; su una FACOLTATIVA riempie
la colonna Motivo senza toccare lo stato - «vuota» NON diventa mai un errore.

MERCATI BETFAIR DELL'UTENTE (#33, SESSIONE)
La libreria sport -> mercato (MarketType + MarketName) -> selezioni (SelectionName),
tutta PER-UTENTE e a inserimento libero: NESSUN catalogo incorporato, al primo login
e' vuota. Isolamento come i parser: 404 sugli altrui, user_id dalla sessione.
GET    /api/me/sports                                 gli sport dell'utente
POST   /api/me/sports                                 body {"nome":"Calcio"}
DELETE /api/me/sports/SLUG                            elimina sport + mercati + selezioni
GET    /api/me/sports/SLUG/mercati                    mercati con selezioni annidate
POST   /api/me/sports/SLUG/mercati                    body {"marketType","marketName","selections":[...]}
DELETE /api/me/sports/SLUG/mercati/ID
GET    /api/me/sports/SLUG/mercati/ID/selezioni       le selezioni di UN mercato (il
                                                      wizard non la usa: legge quelle
                                                      annidate in .../mercati)
POST   /api/me/sports/SLUG/mercati/ID/selezioni       body {"selectionName":"..."}
DELETE /api/me/sports/SLUG/mercati/ID/selezioni/ID
Campi obbligatori non vuoti, massimo 120 caratteri, niente emoji nei tre campi che
finiscono nel CSV (#42); doppione esatto -> 409. Quote: MAX_SPORT_PER_UTENTE (20),
MAX_MERCATI_PER_SPORT (200), MAX_SELEZIONI_PER_MERCATO (200), regolabili da variabile.
Il wizard puo' salvare nel parser il riferimento config.betfair {market_id,
selection_id}: il server verifica alla scrittura che la selezione esista fra quelle
DELL'UTENTE e che le tre costanti coincidano coi valori della libreria (422 se no);
una selezione coi segnaposto {HOME_TEAM}/{AWAY_TEAM} e' rifiutata finche' non esiste
la sorgente squadre (#34). Eliminare un mercato NON rompe i parser gia' salvati: le
regole sono costanti, la libreria e' provenienza, non dipendenza viva.

SORGENTI SQUADRE DELL'UTENTE (#34 pezzo 1, SESSIONE)
La normalizzazione dei nomi squadra: la COMPETIZIONE (sotto uno sport della
libreria #33) possiede la lista canonica dei nomi Betfair, salvata una volta —
e' l'unica colonna che finira' nel CSV; ogni SORGENTE (nominata, rinominabile)
e' una colonna di alias sopra quella stessa lista, UN alias per squadra per
sorgente. Al primo login e' tutto vuoto. Isolamento come sempre: 404 sugli
altrui, user_id dalla sessione. La UI e' il pezzo 2; il trasform e' il pezzo 3
(vedi sotto, TRASFORM NEL PARSER).
GET    /api/me/sorgenti-squadre                       le sorgenti dell'utente
POST   /api/me/sorgenti-squadre                       body {"nome":"test 1"}
PATCH  /api/me/sorgenti-squadre/ID                    rinomina, body {"nome":"..."}
DELETE /api/me/sorgenti-squadre/ID                    elimina sorgente + i SUOI alias
                                                      (le squadre Betfair restano)
GET    /api/me/competizioni                           con sport e conteggio squadre
POST   /api/me/competizioni                           body {"sport":"SLUG","nome":"Serie A"}
GET    /api/me/competizioni/ID                        squadre + sorgenti col badge
                                                      "compilati" (alias non vuoti)
DELETE /api/me/competizioni/ID                        cascata: squadre + alias
POST   /api/me/competizioni/ID/squadre                body {"nome":"Juventus"} — nel
                                                      CSV: niente emoji (#42)
DELETE /api/me/competizioni/ID/squadre/ID             la "x squadra": via dalla
                                                      competizione e dagli alias di
                                                      TUTTE le sorgenti
GET    /api/me/competizioni/ID/alias/ID_SORGENTE      la tabella Betfair <-> alias
PUT    /api/me/competizioni/ID/alias/ID_SORGENTE      body {"alias":{"ID_SQUADRA":"Juve"}}
                                                      tocca solo le coppie presenti;
                                                      alias vuoto = svuota SOLO qui
Doppioni -> 409; campi vuoti o troppo lunghi -> 422; 401 prima del 422. Quote:
MAX_SORGENTI_PER_UTENTE (20), MAX_COMPETIZIONI_PER_UTENTE (50),
MAX_SQUADRE_PER_COMPETIZIONE (100), regolabili da variabile. Eliminare lo SPORT
(#33) porta via a cascata anche competizioni, squadre e alias relativi.
Lo stesso alias su due squadre della stessa sorgente -> 422 (deciso 17/08:
a parse-time la ricerca corre su tutta la sorgente, l'ambiguita' non deve
poter nascere); in un'altra sorgente lo stesso testo e' libero. Il confronto
usa la chiave NORMALIZZATA del parser (spazi uniformi collassati). Stessa
regola per l'alias che coincide col nome Betfair di un'ALTRA squadra
dell'utente -> 422 (tradurrebbe un nome canonico nella squadra sbagliata);
il nome della squadra stessa resta lecito.

TRASFORM NEL PARSER (#34 pezzo 3)
Il parser puo' portare nel config il riferimento "team_source" (id di una
sorgente squadre): validato al salvataggio come "betfair" — inesistente,
altrui o non intero -> 422. A parse-time (webhook E prova) la mappa della
sorgente traduce le due meta' di EventName (spezzato sull'ULTIMO " - "):
alias -> nome Betfair, e il nome Betfair scritto diretto passa senza avvisi
(identita' in mappa). Squadra sconosciuta: nel feed va VERBATIM e il relay
scrive "avviso: ..." in message_logs (e la prova lo restituisce nel campo
"avvisi", accanto a "scarti" — avvisa, non blocca). Nessuna sorgente nel
parser, o sorgente eliminata dopo: passthrough puro, niente traduzione e
niente avvisi. POST /api/me/parsers/SLUG/test risponde ora anche "avvisi".

QUOTE E TETTI PER-TENANT (il database e il volume Railway sono CONDIVISI)
- massimo MAX_PARSER_PER_UTENTE parser per utente (default 20, si alza dalla
  variabile su Railway senza deploy): oltre, la creazione risponde 409 col limite.
  La quota e' misurata dentro il write-lock dell'INSERT: due creazioni simultanee
  sull'ultimo posto non la bucano, il perdente riceve il 409;
- titolo massimo 80 caratteri, config massima 20000 caratteri (JSON serializzato):
  oltre, 422 col limite — su creazione E modifica, o un parser gia' dentro si
  gonfierebbe con una PUT;
- corpo HTTP massimo 65536 byte sulle rotte autenticate che leggono JSON (CRUD
  parser, prova messaggio, concessione giorni admin): oltre, 413 PRIMA del
  parsing — il Content-Length dichiarato ferma il client onesto senza leggere,
  la lettura a pezzi ferma chi mente sull'intestazione o usa il chunked. Senza,
  un corpo enorme si materializzava tutto in RAM prima del 422 sui campi.
I messaggi dicono il limite e non nominano risorse di altri utenti.

PROFILI E FEED SEPARATI
Ogni profilo ha i suoi chat_id, il suo parser e il suo CSV indipendente.
GET /profiles/NOME.csv?token=TOKEN
GET /api/profiles
POST /api/profiles con Header X-Admin-Token e body JSON:
{"name":"MARCO","chat_ids":"-1001234567890,-1009876543210","parser":"Parser_LIVE_1"}
DELETE /api/profiles/NOME con Header X-Admin-Token.
Il profilo PIERO e' quello servito da /xtrader.csv. Esiste come RIGA del database
(sul volume), non piu' ricreato dal codice a ogni avvio: dalla rimozione del seme
(#25 lavoro E) cancellare un parser o un profilo e' DUREVOLE, e rinominare un
parser non lascia piu' il doppione vecchio che il profilo continuava a nominare.
Un database vergine nasce vuoto: nessun parser, nessun profilo.

Salvare un profilo (POST) e eliminarlo (DELETE) aggiornano anche i collegamenti
chat -> parser che il dispatch legge: cambiando il parser di un profilo il
collegamento vecchio sparisce e nasce quello nuovo, ed eliminando il profilo
spariscono i suoi. I collegamenti degli ALTRI parser sulla stessa chat (dispatch
multi-parser) non vengono toccati.

GUARDIE SUI VALORI ESTRATTI (parser configurabili)
Le colonne numeriche vengono lette come numeri, non copiate verbatim:
- Price, MinPrice, MaxPrice ammesse fra 1.01 e 1000 (scala reale delle quote);
- Handicap fra -1000 e +1000; Points (moltiplicatore dello stake) fra 0 e 1000.
Vuoto resta legale: Price vuota e' il caso normale, la quota la mette XTrader.
Un valore storto SCARTA il messaggio intero e non svuota la colonna, perche' una
colonna svuotata direbbe un'altra cosa (Price vuota = prezzo di mercato, Points
vuoto = 1x). Il motivo distingue tre casi: cifre non ASCII, non numerico, fuori
intervallo — e nomina il separatore delle migliaia, che e' la causa piu' comune.
Un parser le cui colonne obbligatorie sono TUTTE costanti non scrive: produrrebbe
la stessa scommessa per qualunque messaggio riconosciuto.
Una chiave di colonna che non e' una delle 14 da' 422 col suggerimento.
Il verdetto corre sul valore NORMALIZZATO dalla classe condivisa degli spazi
(identica nei due motori): spazi, BOM e separatori di controllo ai bordi vengono
perdonati, dentro il numero no. Senza, i default di strip() e trim() divergevano
e lo stesso valore era valido nel browser e scartato in produzione.
I motivi di scarto esistono solo per i messaggi RICONOSCIUTI dalla condizione:
un messaggio estraneo resta parser_no_match e non produce righe di log.

PROVA DI UN PARSER
POST /api/parsers/NOME/test
Header: X-Admin-Token: TOKEN
Body JSON: {"message":"testo completo del messaggio"}
Un messaggio che il parser non riconosce da' 422, non 500.

QUANDO UN MESSAGGIO NON VIENE RICONOSCIUTO
Il parser cerca l'header del parser nel testo e poi la riga che contiene il
marcatore. Se l'header manca, se nessuna riga ha il marcatore, o se dopo il
marcatore non c'e' niente, il messaggio e' NON RICONOSCIUTO: il feed non viene
toccato e il webhook risponde 200 con {"ignored":"parser_no_match"}.
Il 200 e' deliberato: Telegram RITENTA le consegne fallite, quindi rispondere con
un errore su un messaggio che sarebbe storto anche al secondo tentativo fa tornare
lo stesso messaggio in loop.
Fino al 12/08/2026 il terzo caso rispondeva 500. Un messaggio con il marcatore in
CODA alla riga - "SQUADRA-A v SQUADRA-B (marcatore)", che e' come scrive chi mette
le squadre prima - lasciava l'evento vuoto e faceva sollevare il parser. Se un
canale era scritto cosi', ogni suo messaggio dava 500 e nessun segnale arrivava
mai: il sintomo era il servizio che risponde e il feed che resta vuoto.

DUE MOTORI DI PARSING: LEGACY E CONFIGURABILE
Il webhook e la rotta di prova passano da elabora_messaggio, che sceglie:
- parser CON config_json  -> motore configurabile (esegui_parser): la condizione e
  la mappatura delle 14 colonne stanno nella config JSON, non nel codice. Se il
  risultato e' completo (le quattro colonne obbligatorie valorizzate) diventa il
  feed, altrimenti "parser_no_match".
- parser SENZA config_json -> parser storico (parse_message), byte per byte com'era.
  E' il caso di PIERO e di /xtrader.csv: NON cambia niente.
Le rotte che creano parser con config_json dalla web app arrivano piu' avanti;
finche' non esistono, in produzione tutti i parser sono "senza config_json".

IL DISPATCH: CHAT -> N PARSER, OGNUNO AL SUO FEED
Il webhook non prende piu' "il primo profilo in ordine alfabetico che contiene la
chat" (il difetto misurato nella Issue #25: un profilo dal nome che ordina prima
di PIERO dirottava la produzione in silenzio). Legge parser_chats, seminata dalla
migrazione a partire dai profili:
- ogni consegna porta un update_id: la PRIMA vince, le riconsegne di Telegram
  escono come {"ignored":"duplicate"} senza rielaborare ne' riarmare il TTL;
- l'elaborazione gira fuori dall'event loop (un parser lento non ferma le altre
  richieste del servizio);
- ogni parser ATTIVO collegato alla chat elabora in modo indipendente e scrive
  nel feed del SUO utente; fra i parser dello stesso utente che riconoscono lo
  stesso messaggio vince l'ULTIMO nell'ordine dichiarato (parsers.ordine), e i
  battuti restano in message_logs come "riconosciuto, sostituito da ...";
- utenti diversi sulla stessa chat non si toccano: due profili sulla stessa chat
  sono due feed indipendenti.
Una chat senza alcun link (un profilo creato a caldo via API: i link arrivano
alla prossima migrazione) passa dal percorso legacy per profili, identico a prima.
message_logs e webhook_seen si ripuliscono da sole oltre i 7 giorni, alla
scrittura successiva.

Le regex di un parser configurabile (condizione o colonna) le scrive l'utente e
girano sul worker condiviso: hanno un TIMEOUT DURO (modulo regex, che lo `re` di
stdlib non ha). Il deadline e' a due livelli: 0,1 s per singolo match, e un BUDGET DI
PARSER di 0,1 s condiviso fra la condizione e le 14 colonne, cosi' un parser con
molte regex catastrofiche non somma i timeout (misurato: 1,5 s su 15 regex senza il
budget) e l'intera elaborazione di un messaggio resta ~0,1 s. Un pattern
catastrofico di un cliente scade e non produce segnale PER LUI, ma non blocca il
parsing degli altri clienti. Una config malformata (JSON valido ma storto) da'
"parser_no_match", non un 500. Un cliente che INONDA di molti messaggi cattivi non
e' ancora coperto (servira' un rate-limit per-utente).

INSERIMENTO MANUALE (usa il parser predefinito o quello indicato)
POST /api/test-message?parser=Parser_Telegram_XTrader_v1
Header: X-Admin-Token: TOKEN
Body JSON: {"message":"testo completo del messaggio"}

LOGIN E SESSIONI (dal PR 6)
Due porte, e averne due non e' ridondanza: se Telegram non e' disponibile o se il
proprietario perde quell'account, la seconda lo fa entrare comunque.

POST /api/login/telegram
Body JSON: i campi del Login Widget COSI' COME LI CONSEGNA, "hash" compreso. id e
auth_date il widget li passa come NUMERI e il server li accetta tali: prima
rispondeva 422 a OGNI login reale, perche' Pydantic v2 non converte un numero in
stringa. La firma si calcola sulle forme testuali JSON dei valori, che e' cio' che
firma Telegram: per un booleano "true", non "True".
La firma HMAC-SHA256 viene verificata con una chiave derivata da TELEGRAM_BOT_TOKEN:
Telegram non manda un token, manda i campi in chiaro piu' la firma, quindi chi non la
verifica accetta un "id" scritto da chiunque. auth_date viene controllato in ENTRAMBE
le direzioni (max 300 secondi): troppo vecchio significa che una firma vale per
sempre, nel futuro significa che un auth_date messo a mano vale per sempre.
Firma non valida o scaduta: 401, senza distinguere i due casi.

POST /api/login/password
Body JSON: {"username":"administrator","password":"..."}
L'accesso di emergenza. Senza ADMIN_PASSWORD_HASH risponde 503 (percorso
DISABILITATO, non aperto). Dopo 5 tentativi falliti si frena per 300 secondi e
risponde 429 — anche alla password giusta, altrimenti non frenerebbe nulla.
Il tentativo viene CONTATO prima della verifica, dentro lo stesso lock che controlla
il freno, e azzerato solo in caso di successo. Prima erano due gesti separati con
scrypt in mezzo (~100 ms), quindi 12 richieste concorrenti passavano tutte: il limite
si aggirava mandandole insieme, e ogni richiesta accendeva uno scrypt — il freno
amplificava il carico invece di ridurlo. Il freno
e' globale e non per IP: per IP non fermerebbe chi prova in automatico, che cambia
indirizzo. Il prezzo — un estraneo puo' tenere occupato questo percorso per qualche
minuto — e' accettabile perche' il login Telegram resta disponibile.
Ogni accesso riuscito finisce in admin_audit.

ENTRAMBE le rotte di login rispondono 503 "sessioni non configurate" se manca
TELEGRAM_BOT_TOKEN: il segreto che firma i cookie deriva da quel token, quindi senza
non c'e' nessuna sessione da emettere. Prima uscivano 200 con un cookie VUOTO — il
login sembrava riuscito e ogni richiesta successiva rispondeva 401, senza niente da
nessuna parte che dicesse perche'. E' il caso di chi configura ADMIN_PASSWORD_HASH ma
non il bot, cioe' l'emergenza per cui quel percorso esiste.

GET /api/me
Chi e' l'utente della sessione: {"utente","nome","stato","admin","accesso_scade",
"giorni_rimasti","slug","token_prefix"}. Mai il token: il prefisso sono i primi
9 caratteri, per riconoscere in UI quale token e' armato.
401 se il cookie manca, non e' firmato, e' scaduto, o se session_version e' cambiata.
Non restituisce mai un token, ne' l'hash della password, ne' il telegram_id.
Rinnova il cookie: e' la rotta che rende i 20 minuti "di inattivita'" (vedi sotto).

POST /api/logout
Cancella il cookie. Riesce sempre, anche senza sessione: chiudere una sessione che
non esiste ha ottenuto cio' che voleva. NON incrementa session_version, che
butterebbe fuori tutti i dispositivi.

GET /api/settings
I valori PUBBLICI che la pagina di login conosce PRIMA della sessione:
{"bot_username","bot_id","base_url"}. Serve alla web app per costruire il link
"Accedi con Telegram" in modalita' redirect di oauth.telegram.org (che vuole il
bot_id NUMERICO, non lo username). bot_id e' il prefisso del token del bot prima
dei due punti: pubblico per costruzione, compare in ogni embed del widget. Il
token del bot NON esce mai da questa rotta. Nessuna autenticazione, di proposito.

IL COOKIE E IL FEED NON SI TOCCANO
Il cookie di sessione (betrelay_sessione) e' HttpOnly, Secure, SameSite=Lax, e scade
dopo 20 MINUTI DI INATTIVITA': ogni rotta che valida la sessione lo riemette con
un'emissione nuova (oggi GET /api/me). Senza quel rinnovo la scadenza sarebbe assoluta
dal login, e chi sta lavorando verrebbe buttato fuori ogni 20 minuti.
Quei 20 minuti riguardano SOLO il sito. Il feed CSV non ha sessione: XTrader lo
interroga con un token nell'URL, non fa login e non "resta attivo". Collegare le due
cose farebbe perdere i segnali a ogni cliente 20 minuti dopo aver chiuso il browser,
e nessun test di login lo troverebbe perche' il login funzionerebbe benissimo.
  sessione = cookie del sito, scade per inattivita'
  token    = accesso al feed, scade solo se revocato
Lo vincola tests/relay/test_login.py::test_la_sessione_scaduta_NON_tocca_il_feed.

Il cookie e' FIRMATO con un segreto derivato da TELEGRAM_BOT_TOKEN, come quello del
webhook e per la stessa ragione: esiste sempre dove esiste il bot, e non serve una
variabile in piu' con la sua finestra di configurazione. Senza bot nessuna sessione
e' valida. Il prefisso lo separa dal segreto del webhook, cosi' un valore rubato da
un canale non serve nell'altro.
session_version nel database invalida SUBITO tutti i cookie di un utente: e' il modo
di chiudere un accesso sospetto senza aspettare i 20 minuti.

ACCESSO SU APPROVAZIONE (dal PR 7)
Un cliente nuovo nasce 'registrato' e non ha accesso. Chiede, il proprietario concede
giorni, il cliente riceve un messaggio su Telegram. Alla scadenza il feed torna a sola
intestazione senza revocare il token.

POST /api/access/request
  Sessione del cliente. Crea la richiesta e mette lo stato 'in_attesa'.
  409 se ha gia' una richiesta aperta o l'accesso attivo, 403 se e' sospeso.
  Risponde con {"raggiungibile", "bot"}: "bot" e' il deep link t.me/<bot>?start=accesso.
  IL DEEP LINK NON E' UN ABBELLIMENTO: il bot Telegram non puo' scrivere per primo, quindi
  sendMessage verso chi non ha mai aperto la conversazione FALLISCE. Un cliente che entra
  col Login Widget e non apre mai il bot non riceverebbe mai l'approvazione, in silenzio.
  Quando preme Start, la consegna arriva al webhook e telegram_reachable diventa 1.

GET  /api/admin/requests
POST /api/admin/requests/<id>/approva   body {"giorni": <numero>}
POST /api/admin/requests/<id>/rifiuta
POST /api/admin/promemoria
  Tutte e quattro rispondono 404 a chi non e' l'amministratore — non 403, che
  confermerebbe a un estraneo che il pannello sta li'. Per la stessa ragione le rotte con
  un corpo o un id nel percorso li leggono A MANO dopo il controllo della sessione:
  lasciandoli a FastAPI, un estraneo riceveva 422 invece di 404, cioe' la stessa conferma
  per un'altra via.
  I giorni sono un campo libero (1..3650: il limite ferma un refuso, non fa da listino).
  I rinnovi SI SOMMANO se la scadenza e' nel futuro, altrimenti ripartono da oggi —
  senza il secondo ramo, prorogare un cliente scaduto da due mesi gli darebbe una
  scadenza nel passato, cioe' «attivo» nel pannello e feed vuoto.
  L'approvazione risponde con {"notificato", "motivo"}: se il messaggio non e' partito,
  notificato e' false col motivo, e telegram_reachable va a 0. L'ERRORE DI INVIO NON VIENE
  INGOIATO, perche' un invio fallito in silenzio produce lo stato peggiore — il
  proprietario crede di aver avvisato, il cliente non sa di essere attivo. L'accesso resta
  concesso: e' stato deciso, e non si annulla una decisione perche' l'avviso non e'
  arrivato.
  Il promemoria parte a 5 giorni dalla scadenza, UNA VOLTA PER SCADENZA (users.promemoria_per
  conserva QUALE scadenza e' stata annunciata: con un booleano il secondo rinnovo non
  avviserebbe mai piu'). Un invio fallito NON consuma il promemoria: il giro dopo riprova.
  LIMITE DICHIARATO: non c'e' uno scheduler. /api/admin/promemoria va CHIAMATA, dal
  proprietario o da un job programmato su Railway. Finche' non viene chiamata nessun
  promemoria parte — e' un compito che aspetta, non un compito perso.

EFFETTI DELLA SCADENZA
  Feed: 200 con SOLA INTESTAZIONE e BOM, non 401. Per XTrader un errore HTTP e' un guasto
  da segnalare, mentre «nessun segnale» e' uno stato normale che gestisce da se'.
  Token: NON revocato. «Scaduto» e «revocato» sono stati diversi — revocare costringerebbe
  il cliente a riconfigurare XTrader a ogni rinnovo. Cosi' il rinnovo e' istantaneo.
  Webhook: i messaggi delle sue chat non vengono elaborati e non finiscono nei log. Il feed
  non viene toccato: allo svuotamento ci pensa il TTL di 90 secondi.
  Bloccano 'scaduto' e 'sospeso'. NON 'registrato': i feed per profilo esistono da prima di
  questo flusso e i loro utenti sono nati 'registrato' dalla migrazione, quindi bloccarli
  spegnerebbe in silenzio un feed che oggi funziona. Un cliente che si registra col Login
  Widget non ha nessun profilo, quindi non ha nessun feed da bloccare.
  Il PROPRIETARIO (is_admin) non ha un abbonamento: il suo feed non dipende da nessuna
  scadenza, altrimenti una riga sbagliata nel database spegnerebbe quello che XTrader
  interroga in produzione.

COSA IL PR 6 NON FA
La web app in web/ resta con dati finti: collegarla al backend e' un PR successivo.
Questi endpoint si esercitano via HTTP, e i test lo fanno.
L'accesso su approvazione NON e' del PR 6: e' arrivato col PR 7, ed e' descritto qui
sopra in ACCESSO SU APPROVAZIONE. Questa riga diceva il contrario e contraddiceva la
sezione nuova nello stesso file — segnalato da CodeRabbit sulla PR #26. Cio' che manca
ancora sono le SCHERMATE di quel flusso: la web app e' sui dati finti fino al PR 12.

AUTENTICAZIONE
CSV_ACCESS_TOKEN protegge dieci rotte: i due feed CSV (/xtrader.csv e
/profiles/NOME.csv, col parametro ?token=) e le otto API di gestione (con
l'header X-Admin-Token). Quattro sono in lettura, sei in scrittura.
Restano pubbliche soltanto /, /health, /telegram/webhook, /app e /admin (che
e' solo un redirect verso l'app: vedi SCORCIATOIA /admin).

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

PRIMA DI UN DEPLOY
Un deploy NON tocca i dati: il database vive nel volume montato su /data
(DB_PATH=/data/signals.db) e il volume sopravvive al riavvio del container. Ma un
riavvio ferma il servizio per qualche secondo, e in quei secondi un segnale live
arriva in ritardo. Quindi, in ordine:
- SCEGLI UN ORARIO TRANQUILLO, mai nel pieno delle partite: un ritardo di pochi
  secondi su un segnale live puo' far perdere l'ingresso. Le ore piccole vanno bene.
- PRIMA DI UNA MODIFICA RISCHIOSA (migrazione di schema, cambio di variabili
  d'ambiente) scarica un backup fresco (vedi BACKUP): se qualcosa va storto, hai la
  copia di com'era prima.
- DOPO IL RIAVVIO verifica /health come scritto sopra (status ok, webhook_registrato
  true, aspettando fino a un minuto), poi manda un segnale di prova dal canale.

BACKUP
Tutti i dati del servizio stanno in UN file: signals.db, dentro il volume — utenti,
parser, libreria mercati, hash dei token, log, richieste di accesso. Le VARIABILI
D'AMBIENTE non stanno nel database ne' nel repository (bot token, CSV_ACCESS_TOKEN,
ADMIN_PASSWORD_HASH, DB_PATH: vedi VARIABILI RAILWAY): vanno salvate a parte dal
proprietario una volta sola, perche' un backup del database non le contiene.

COPIA MANUALE, DAL PANNELLO. Nella vista Richieste (solo amministratore) il pulsante
"Scarica backup" consegna una copia completa e CONSISTENTE del database, col nome
betrelay-backup-AAAA-MM-GG-HHMM.db. La copia e' presa con l'API di backup di SQLite,
non con un cp del file a caldo: una copia grezza mentre il servizio scrive puo'
uscire a meta' di una transazione, e un backup corrotto e' peggio di nessuno perche'
lo si scopre solo il giorno in cui serve. La rotta e' GET /api/admin/backup e
risponde 404 a chi non e' l'amministratore, come tutto /api/admin/*. Ogni download
lascia una riga in admin_audit, scritta DOPO che la copia e' riuscita (una copia
fallita non lascia la traccia di un download mai avvenuto). La copia e' pesante in
memoria, quindi ne gira UNA per volta: click ravvicinati o richieste concorrenti si
mettono in coda invece di moltiplicare il picco di RAM. E poiche' e' una GET che
avvia un lavoro costoso, la rotta rifiuta (403) le navigazioni che il browser marca
come provenienti da un altro sito (Sec-Fetch-Site cross-site/same-site): il pulsante
del pannello e' same-origin e continua a funzionare, ma una pagina ostile non puo'
indurre il browser dell'amministratore a generare backup a ripetizione.

RUNTIME. La copia consistente usa sqlite3.Connection.serialize(), comparso in Python
3.11: su un interprete piu' vecchio la rotta fallirebbe solo in produzione, al primo
download. Per questo il runtime e' pinnato: .python-version in radice dice 3.11, la
stessa versione su cui gira la CI, cosi' Railway/Nixpacks non deriva a una Python mai
testata. Una guardia (tests/safety/test_python_deploy.py) impedisce di abbassare il
pin sotto quella provata dai test.

CANALE DI BACKUP (destinazione). Il proprietario crea un canale Telegram privato e ci
aggiunge il bot come amministratore; da li' i backup automatici (in arrivo) verranno
consegnati. La configurazione vive nel pannello admin e passa da un CANDIDATO a un
canale CONFIGURATO:
  - CATTURA: quando il proprietario aggiunge il bot come amministratore del canale,
    Telegram manda un my_chat_member; il webhook lo riconosce e registra il canale come
    candidato — ma SOLO se e' l'amministratore ad averlo promosso (from.id ==
    TELEGRAM_ADMIN_ID), altrimenti il canale di chiunque comparirebbe come proposta, e
    SOLO se e' un canale PRIVATO. Un canale pubblico ha uno username e i backup —
    dati dei clienti — non devono finire dove chiunque puo' leggerli: quelli con username
    sono rifiutati gia' alla cattura.
    Registra solo un candidato: nessun backup parte da qui. I canali hanno id negativi
    (-100...) che l'app Telegram non mostra, ed e' il motivo per cui il chat_id si
    cattura cosi' invece di digitarlo. (L'inoltro di un messaggio, l'altra opzione della
    #56, e' stato scartato: qualunque post inoltrato al bot avrebbe riconfigurato il
    candidato di soppiatto.) Una riconsegna del my_chat_member dopo la conferma non
    ripropone un canale gia' configurato. Le riconsegne sono dedotte per update_id sullo
    stesso registro del percorso segnali (webhook_seen): lo stesso update elaborato due
    volte esce come duplicate e non riscrive niente (#56 pezzo 3b). E in piu' l'ORDINE
    (#56, Sol B1): gli update_id di Telegram crescono monotoni, ma l'offload su thread puo'
    elaborarli fuori ordine; si tiene l'update_id piu' alto gia' processato PER CANALE
    (canale_backup_ultimo_update_id:<chat_id>) e un evento con id inferiore per lo STESSO
    canale - una promozione tardiva dopo una rimozione piu' nuova - esce come out_of_order,
    cosi' non risorge un candidato ormai invalido. Per-canale e non globale: l'ordine di un
    canale non sopprime gli eventi di un altro (un altro candidato non blocca la pulizia del
    configurato).
  - RIMOZIONE (#56 pezzo 3b): se il bot non puo' piu' pubblicare nel canale — uscito
    (left/kicked) o RETROCESSO da amministratore a member/restricted (in un canale solo gli
    admin postano) — la configurazione si azzera, che sia il configurato o il candidato.
    Senza, il pannello continuerebbe a mostrare una destinazione dove il bot non puo' piu'
    postare e ogni backup fallirebbe in silenzio. Agisce SOLO sul canale nostro (match sul
    chat_id): un left da un canale estraneo non spegne il backup del proprietario.
  - CONFERMA: la card «Conferma» nel pannello manda al server, nel corpo, il chat_id del
    candidato che ha MOSTRATO ({chat_id}) — una precondizione dal client, come uid sui
    parser (#75): se fra la lettura dello stato e la conferma una riconsegna ha cambiato
    il candidato server-side, il server risponde 409 e la card rilegge, invece di
    configurare una destinazione che l'amministratore non ha approvato. Poi la conferma
    manda un MESSAGGIO DI PROVA al candidato; solo se l'invio riesce il candidato diventa
    il canale configurato. Se la prova fallisce non si salva niente e l'errore e' VISIBILE
    nel pannello (mai ingoiato). Il candidato si rilegge anche dentro la transazione: se e'
    cambiato durante la prova la conferma si ferma (409) invece di configurare quello
    vecchio. Un solo canale di backup alla volta.
  Rotte, tutte 404 fuori dall'amministratore come il resto di /api/admin/*:
    GET    /api/admin/canale-backup            stato: {configurato, candidato}
    POST   /api/admin/canale-backup/conferma   corpo {chat_id}; promuove il candidato dopo
                                               la prova (422 senza chat_id, 409 se non
                                               combacia col candidato corrente)
    POST   /api/admin/canale-backup/prova      riprova l'invio sul canale configurato
    DELETE /api/admin/canale-backup            rimuove il canale configurato
    POST   /api/admin/backup/invia             manda il backup al canale configurato (pezzo 3)
  Nel pannello admin la card «Canale di backup» (in fondo alla vista Richieste) mostra i tre
  stati (proposto / configurato / vuoto) con Conferma, Invia backup ora, Manda una prova e
  Rimuovi. «Invia backup ora» (pezzo 3b) e' l'invio manuale dalla sessione admin: chiede
  conferma nel modale e poi chiama lo stesso POST /api/admin/backup/invia del cron.
  Configurare, confermare e rimuovere lasciano una riga in admin_audit. Il canale sta
  nella tabella impostazioni (chiave→valore), NON in chats: chats sono le SORGENTI dei
  segnali, e mettere li' la destinazione dei backup la iscriverebbe all'instradamento
  del webhook (il filtro delle chat).

INVIO DEL BACKUP AL CANALE (#56 pezzo 3). `POST /api/admin/backup/invia` manda una copia
  del database al canale privato configurato, via sendDocument di Telegram. Due modi di
  autorizzarsi:
  - la SESSIONE dell'amministratore - il bottone «Invia backup ora» nel pannello (pezzo 3b);
  - il TOKEN del cron nell'header X-Backup-Cron-Token, per il giro NOTTURNO.
  Senza nessuno dei due -> 404, come tutto /api/admin/*. Il confronto del token e' a tempo
  costante e solo se il token e' configurato: BACKUP_CRON_TOKEN vuoto NON autorizza nessuno
  (fail-closed), quindi il percorso automatico resta spento finche' non lo imposti.
  Come funziona: la copia va su un FILE temporaneo (non in RAM, per non tenere l'intero .db
  in memoria durante l'upload) e si manda in streaming; il file si cancella comunque. Prima
  di inviare, un getChat RIVERIFICA che il canale sia ancora PRIVATO: se e' diventato
  pubblico (ha uno username) NON si invia - il backup coi dati dei clienti non deve finire
  dove chiunque puo' leggerlo. Un invio riuscito lascia una riga in admin_audit
  (admin_user_id NULL quando e' stato il cron). L'invio (rete + I/O) gira fuori dall'event
  loop, o bloccherebbe webhook e feed.
  IDEMPOTENZA (#56, chiude i bloccanti di Sol). Il percorso CRON prenota un PERIODO (la data
  UTC del giro) nella tabella backup_inviato PRIMA di inviare: due repliche o un retry non
  mandano due copie - la seconda trova il periodo gia' preso ed esce come no-op. Se l'invio
  fallisce la prenotazione si LIBERA, cosi' un retry della stessa notte riparte. Il BOTTONE
  dell'amministratore (nessun periodo) e' esente: e' un'azione umana esplicita e invia SEMPRE,
  anche se il cron ha gia' mandato il backup di oggi. (Baratto dichiarato: un crash fra la
  prenotazione e l'invio salta al massimo UNA notte - il giro dopo riparte, e il bottone
  resta disponibile - preferito a mandarne due.)
  IL GIRO NOTTURNO. Il servizio NON ha uno scheduler interno (come i promemoria): serve un
  job esterno che chiami l'endpoint. Configuralo cosi', una volta sola:
    1. imposta la variabile BACKUP_CRON_TOKEN su un valore lungo e casuale (e' un secret,
       come CSV_ACCESS_TOKEN: non committarlo, non stamparlo);
    2. crea un cron su Railway (o un pinger esterno) che ogni notte faccia:
         curl -fsS -X POST https://<il-tuo-dominio>/api/admin/backup/invia \
              -H "X-Backup-Cron-Token: $BACKUP_CRON_TOKEN"
       Il token va nell'HEADER, mai nell'URL (l'URL finisce nei log).
    Un servizio cron D'ESEMPIO gia' pronto sta in deploy/cron-backup/ (lo script
    invia_backup.sh + il suo README): nessun segreto dentro, legge BACKUP_BASE_URL e
    BACKUP_CRON_TOKEN dalle Variables del servizio cron su Railway.

SNAPSHOT DEL VOLUME SU RAILWAY. Railway offre snapshot del volume dal suo pannello:
sono una copia indipendente dal servizio, che un deploy non tocca. Attivali come
rete di sicurezza aggiuntiva - e' un'azione una-tantum del proprietario dal pannello
Railway, gia' possibile ora.

SICUREZZA DEL BACKUP. Il file contiene i dati dei clienti - i token solo come hash,
mai in chiaro, ma comunque dati da proteggere. Va custodito come il database stesso:
chi ha il backup ha i dati. Non lasciarlo in cartelle condivise o allegati non
protetti.

TEST E CI
python -m pytest -q esegue la suite. Le dipendenze dei soli test stanno in
requirements-dev.txt, separate da quelle del deploy:
  pip install -r requirements-dev.txt && python -m pytest -q

Dal 12/08/2026 li esegue anche la CI: .github/workflows/test.yml gira su ogni pull
request e su ogni push a main. Il workflow impone TEST_RUNTIME_OBBLIGATORIO=1, che
trasforma in FALLIMENTO ogni test saltato per un runtime mancante (node, Chromium).
Senza quella variabile un'installazione del browser andata male darebbe
"252 passed, 5 skipped", exit 0 e una spunta verde identica a quella di una suite
completa: un check che non prova niente. In locale la variabile resta spenta e i
test che richiedono node o Chromium si saltano con motivo scritto.

Il percorso di Chromium e la decisione salta/fallisce vivono in un punto solo,
tests/runtime.py. Le due guardie sono tests/safety/test_ci.py (legge il workflow) e
tests/safety/test_runtime_severo.py (esercita il meccanismo).

SCORCIATOIA /admin
GET /admin reindirizza a /app/#/richieste: la porta di servizio del proprietario.
Solo un redirect, nessuna autenticazione propria — la serratura resta il login e
il 404 delle rotte /api/admin/* per chi non e' amministratore. Chi non e' admin
ci atterra sul login o sulla propria dashboard e non vede nulla.

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

WEB APP
L'interfaccia multiutente e' servita su /app (file statici in web/). Dalla #32
parla col relay VERO: login (Telegram in modalita' redirect, o password), CRUD dei
propri parser, prova del messaggio sul server, token del feed a livello utente.
Il layer di rete e' web/api.js; web/api_finta.js e' il gemello a dati finti usato
SOLO dalla copia dimostrativa a file unico (tools/build_single_file.py), che si
apre da file:// e non puo' fare fetch. Architettura e schermate in SAAS.md.
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
TELEGRAM_ALLOWED_CHAT_IDS: PENSIONATA, non la legge piu' nessuno. La leggeva il
  seme di migra() per i chat_id iniziali del profilo PIERO; con la rimozione del
  seme (#25 lavoro E) quel codice non c'e' piu'. Era gia' inerte su un database
  persistente — la riga PIERO esisteva e INSERT OR IGNORE non la aggiornava — ed
  e' l'ultimo stadio della scaletta in SAAS.md. Si puo' togliere dalle Variables
  di Railway; i chat_id si cambiano con POST /api/profiles.
TELEGRAM_ADMIN_ID: l'ID Telegram numerico del proprietario. Collega il suo login
  all'utente che possiede i suoi parser: quella riga ha origin_profile='PIERO' e
  nessun telegram_id, perche' nessuno lo aveva mai saputo. Si trova scrivendo al bot e
  aprendo https://api.telegram.org/bot<TOKEN>/getUpdates: e' message.from.id.
  Non e' un segreto — chiunque riceva un suo messaggio lo conosce — ma decide chi e'
  l'amministratore. Deve contenere SOLO CIFRE: spazi e newline ai bordi sono tolti,
  ma virgolette, apici, un + o uno spazio interno lo rendono diverso dall'id che
  Telegram manda, e il confronto non combacia mai.
  SENZA la variabile il login del proprietario crea un account VUOTO e la sua
  dashboard risulta vuota senza errori. Il collegamento e' IDEMPOTENTE: impostando la
  variabile, il login SUCCESSIVO ripara — travasa le tracce che l'account sbagliato
  aveva accumulato, gli toglie il telegram_id e lo scrive sulla riga PIERO, con una riga
  in admin_audit. Quindi l'ordine fra variabile e login non conta — ma la riparazione
  va AUTORIZZATA, vedi TELEGRAM_ADMIN_RECONCILE qui sotto.
TELEGRAM_BOT_USERNAME: lo username del bot, senza @. Serve SOLO per costruire il deep
  link che il cliente segue per aprire la conversazione col bot — passaggio obbligato,
  perche' il bot non puo' scrivere per primo. Assente, il link non viene inventato: la
  risposta porta "bot": null e la web app deve mostrare l'istruzione manuale («cerca
  @<nome> su Telegram e premi Start»). Un link costruito con uno username vuoto porta alla
  home di Telegram, e il cliente crede di aver fatto la sua parte mentre il bot continua a
  non poterlo raggiungere.
TELEGRAM_ADMIN_RECONCILE: facoltativa, serve una volta sola. E' il CONSENSO ad
  assorbire la riga vuota che possiede TELEGRAM_ADMIN_ID, e il suo valore e'
  l'IDENTIFICATIVO DI QUELLA RIGA — non un 1. Il numero lo trovi nel messaggio di log
  che accompagna il 409: dice esattamente quale valore mettere.
  Perche' serve: «la riga e' vuota» distingue un account pieno da uno vuoto, non un
  CLIENTE da una riga nata per errore. Un cliente appena registrato e' vuoto anche lui,
  quindi le due situazioni sono indistinguibili — due righe di users con un'identita'
  Telegram e nient'altro. Assorbire d'ufficio significava: se nella variabile finisce per
  refuso l'ID di un cliente, al suo login la sua riga viene svuotata e la sua identita'
  finisce sulla riga del proprietario con is_admin=1, cioe' IL CLIENTE ENTRA NELLA
  DASHBOARD DEL PROPRIETARIO. Misurato. E' la violazione dell'isolamento fra utenti.
  Quando nessun dato distingue i due casi, l'unico marcatore affidabile e' il consenso
  di chi sa. Quindi: senza questa variabile il login risponde 409 e NON tocca niente,
  scrivendo nei log e in admin_audit cosa fare.
  Come si usa: si legge il 409 nei log, si riconosce quella riga come propria (login
  fatto prima che TELEGRAM_ADMIN_ID arrivasse nel processo), si imposta la variabile con
  l'identificativo che il log indica, si rifa' login. Se quella riga NON e' la propria,
  va corretta invece TELEGRAM_ADMIN_ID.
  Perche' l'ID della riga e non 1: un interruttore globale che la documentazione dice di
  togliere dopo l'uso e' un interruttore che resta, e da quel momento un refuso futuro
  in TELEGRAM_ADMIN_ID verso un'altra riga vuota verrebbe assorbito di nuovo — il
  fail-closed sparirebbe in silenzio. Legato alla riga, un consenso dimenticato e'
  innocuo: la riga assorbita non viene cancellata, quindi il suo identificativo non
  viene mai riusato da un utente nuovo. Rischio alzato da GPT-5.5.
  Puoi togliere la variabile dopo l'uso, ma non e' piu' una precauzione necessaria.
  Cosa NON autorizza: un account che possiede parser o chat resta rifiutato anche col
  consenso. Il consenso dice «quella riga vuota e' mia», non «prenditi i dati di un
  altro».
  Nota operativa Railway: aggiungere o togliere il NOME di una variabile invalida la
  cache di build (misurato), quindi conviene accorparla ad altre modifiche di variabili
  invece di fare due deploy.

  L'INVARIANTE, in una frase: se TELEGRAM_ADMIN_ID e' configurato, la riga del
  proprietario porta QUEL telegram_id, o nessuno. Da lei discendono due comportamenti che
  non sono dettagli:

  1. CAMBIARE la variabile TOGLIE l'accesso alla vecchia identita', e la revoca viene
     APPLICATA alla PRIMA RICHIESTA AUTENTICATA che arriva dopo il cambio: un login, o
     una qualunque pagina del sito, di CHIUNQUE. Cambiare la variabile non scrive nel
     database — il servizio la legge all'avvio — quindi fino a quella richiesta la riga
     porta ancora il vecchio telegram_id. Poi il collegamento stantio viene sciolto
     (telegram_id azzerato, session_version incrementata, riga in admin_audit) e la
     vecchia identita' torna un cliente qualunque.
     Due versioni precedenti erano insufficienti, ed e' utile sapere perche': prima la
     revoca scattava solo all'ingresso del NUOVO ID, quindi se il nuovo non entrava mai il
     vecchio restava amministratore per sempre; poi scattava a ogni LOGIN, ma chi aveva
     gia' un cookie amministrativo lo conservava — e non scadeva, perche' ogni richiesta
     valida rinnova il cookie, quindi una sessione tenuta aperta e' immortale. Chi ha il
     pannello aperto non ha nessun motivo di rifare login. Ora la sua stessa prossima
     richiesta chiude la sua sessione.
     Il feed non e' toccato: /xtrader.csv non ha sessione, quindi la revoca non lo
     riguarda.
     SVUOTARE la variabile invece NON scioglie niente, ed e' deliberato: vuota significa
     «nessuna invariante dichiarata», non «revoca». Sciogliendo, la riga del proprietario
     resterebbe senza telegram_id e nessun ramo potrebbe ricollegarla — al login dopo
     nascerebbe un secondo account. Un valore MALFORMATO (virgolette prese incollando
     nel pannello, spazi interni, cifre non ASCII, zero iniziale) e' trattato come NON
     configurato per la stessa ragione: applicare l'invariante su un valore con cui
     nessun collegamento nuovo puo' nascere scioglieva quello buono e faceva nascere un
     account vuoto, cioe' un refuso nel pannello chiudeva il proprietario fuori dal
     proprio account.
  2. Se la variabile punta a un account che POSSIEDE parser o chat, il login viene
     RIFIUTATO con 409: l'account bersaglio resta INTATTO, nessun dato viene travasato e
     nessun telegram_id viene spostato (il collegamento stantio del proprietario, se
     c'era, puo' essere stato sciolto al punto 1, che riguarda un'altra riga).
     Prima quell'account
     veniva assorbito: bastava sbagliare una cifra e mettere l'ID di un cliente, e al suo
     login i suoi parser e le sue chat passavano al proprietario, il suo telegram_id
     veniva azzerato e lui otteneva is_admin=1 — perdeva tutto E entrava nell'account di
     un altro. Il rifiuto e' tracciato in admin_audit e spiegato nei log; il messaggio
     verso chi chiama non nomina utenti ne' identificativi.
     Segnali e log NON contano come possesso: sono tracce, e seguono l'utente.
  E CAMBIARE il valore REVOCA le sessioni della vecchia identita': il cookie e' legato
  alla riga e a session_version, non al telegram_id, quindi senza la revoca chi era
  entrato con l'identita' precedente conserverebbe ACCESSO AMMINISTRATIVO — e non
  scadrebbe, perche' GET /api/me rinnova il cookie a ogni richiesta valida, quindi una
  sessione tenuta attiva e' immortale. Se in quella variabile fosse finito l'ID di un
  estraneo, correggerla ora glielo toglie. La revoca scatta al CAMBIO e non a ogni
  login: altrimenti entrare dal computer chiuderebbe la sessione sul telefono.
  Fino al 12/08/2026 non era cosi': il collegamento viveva dentro «if riga is None»,
  quindi valeva solo al PRIMO login, e un login fatto troppo presto lasciava il
  proprietario fuori dal proprio account IN MODO IRREVERSIBILE — nessun endpoint
  riparava, un riavvio non riparava, e serviva scrivere a mano nel database.
ADMIN_PASSWORD_HASH: facoltativa. L'accesso di emergenza, utente 'administrator',
  per entrare nel pannello quando Telegram non e' disponibile. Contiene l'HASH e mai
  la password: la dashboard di Railway e' leggibile da chi ha accesso al progetto, e
  con la password in chiaro chi la legge entra nel pannello — da cui si cancellano
  parser, si cambiano le chat autorizzate e si inietta un segnale nel feed che
  XTrader legge. Si genera con:
    python3 -c "import hashlib,os,base64,getpass; p=getpass.getpass('password: ').encode(); s=os.urandom(16); print('scrypt$'+base64.b64encode(s).decode()+'$'+base64.b64encode(hashlib.scrypt(p,salt=s,n=16384,r=8,p=1,dklen=32)).decode())"
  getpass e non input(): la password non deve comparire sullo schermo, e con input()
  resta scritta li' — davanti a chiunque guardi, o dentro la registrazione di una
  condivisione schermo. Il comando e' stato ESEGUITO in questa forma e stampa
  scrypt$sale$derivata: i $ dentro le doppie virgolette restano letterali perche'
  seguiti da un apice, e non serve nessun escape.
  Per cambiare password si rigenera l'hash e si sostituisce la variabile.
  ASSENTE = percorso a password DISABILITATO (503), non aperto.
DB_PATH: facoltativo; su Railway usare un volume per conservare i dati tra riavvii.
  Il default del codice e' sotto /tmp, che su Railway si perde a ogni deploy. In
  questo servizio vale /data/signals.db, dentro il volume montato su /data
  (misurato il 12/08/2026). Chi deploia altrove e non imposta la variabile perde
  parser e profili al primo riavvio.
  Lo schema viene portato al corrente all'avvio, alla prima connessione del
  processo: tabelle nuove create, colonne nuove aggiunte con ALTER additivo,
  nessuna tabella cancellata o rinominata e nessuna associazione persa. E'
  rieseguibile, quindi un riavvio non cambia nulla, e non solleva sui dati che
  trova. L'unica cosa che rimuove sono le righe DUPLICATE della tabella chats,
  dopo aver ripuntato su quella sopravvissuta i parser che le riferivano: se due
  profili elencano la stessa chat ne resta una sola riga, del primo in ordine
  alfabetico di profilo. Le righe degli utenti non vengono mai cancellate, perche'
  possiedono chat, parser e segnali. Dettaglio delle tabelle in SAAS.md, sezione
  «Modello dati».

I valori di mercato non sono piu' variabili d'ambiente: parser e profili vivono nel
database e si gestiscono dalle API /api/parsers e /api/profiles.

NON INSERIRE TOKEN, PASSWORD, APP KEY O CERTIFICATI NEL REPOSITORY.
