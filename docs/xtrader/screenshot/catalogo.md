# Catalogo degli screenshot XTrader

Ogni immagine della cartella ha qui la sua descrizione a parole: **quale finestra è**, **quali
controlli contiene** (etichette verbatim), **a cosa serve** e l'esito del **controllo privacy**.

Serve a due lettori diversi:

- una persona che scrive una guida e deve trovare in fretta lo screenshot giusto;
- un **assistente AI** che deve sapere *dove guardare* per rispondere a «come imposto la fonte
  dei segnali?» — senza aprire le immagini, che non sa leggere.

La versione machine-readable è `catalogo.jsonl` (una riga JSON per immagine, stessi campi).
La guida operativa che le usa è [`docs/xtrader_integration.md`](../../../../docs/xtrader_integration.md).

**Rilevanza per il bridge**: 🔴 altissima = serve per collegare BetRelay · 🟠 alta = contorno utile
· 🟡 media = contesto · ⚪ bassa = riguarda il trading, non il bridge.

---

## Le immagini che contano davvero (🔴 altissima)

Se hai poco tempo, guarda queste.

| Immagine | Cos'è |
|---|---|
| `varie/02-20260708-170631.png` | Finestra Segnali |
| `varie/03-20260708-170658.png` | Dialog «Fonte Segnali» — menu Riconoscimento selezioni aperto |
| `varie/04-20260708-170716.png` | Dialog «Fonte Segnali» — menu Lingua Palinsesto aperto |
| `azioni-se-vero/01-20260708-165342.png` | Dialog «Nuova Azione» — menu «Tipo di Azione» aperto |
| `azioni-se-vero/04-20260708-165534.png` | Azione «Piazza Scommesse su Segnali» |
| `condizioni/13-20260706-170051.png` | Dialog «Modifica Regola» — scheda «Numero Esecuzioni» |
| `condizioni/15-20260706-170200.png` | Dialog «Nuovo Nodo Condizioni» |
| `condizioni/17-20260706-181819.png` | Dialog «Nuova Condizione» — elenco COMPLETO dei tipi di condizione |
| `condizioni/18-20260706-182041.png` | Condizione «Valore Quota della Selezione» — menu del criterio di selezione |
| `condizioni/20-20260706-185533.png` | Menu «tipo di mercato» — elenco MarketType, parte 1 (A–F) |
| `condizioni/21-20260706-185605.png` | Menu «tipo di mercato» — elenco MarketType, parte 2 (F–M) |
| `condizioni/22-20260706-185650.png` | Menu «tipo di mercato» — elenco MarketType, parte 3 (N–S) |
| `condizioni/42-20260706-192032.png` | Condizione «Conta scommesse» |

---

## `varie/` — Finestre di contorno — Segnali, Filtro Mercati, Processors

### 01-20260708-170559.png

**Finestra principale — menu «Funzioni» aperto** · rilevanza 🟠 alta

La barra dei menu di XTrader con il menu «Funzioni» aperto. Elenca tutte le finestre operative con le rispettive scorciatoie da tastiera: Monitor Mercati (ALT+M), Preferiti (F2), Stato Scommesse (ALT+B), Filtro Mercati (F3), Miei Mercati (F12), Automazione (ALT+S), Processors Mercati (ALT+MAIUSC+M), Segnali (F11), Eventi Terminati (ALT+R), Tabella Trader (CTRL+T), Money Management (CTRL+MAIUSC+M). Sullo sfondo la Scelta Rapida con l'elenco eventi di calcio.

*Usare per:* Dire all'utente come raggiungere una finestra. Per il bridge: Funzioni → Segnali, oppure il tasto F11.

*Privacy:* la barra del titolo è stata **oscurata prima del commit**: nell'originale mostrava due metadati dell'abbonamento del proprietario (data di scadenza e ultimo accesso), nell'immagine pubblicata al loro posto c'è un riquadro bianco con la dicitura «dati abbonamento rimossi» — misurato con l'OCR sull'immagine committata. I valori non compaiono né qui né nell'immagine.

### 02-20260708-170631.png

**Finestra Segnali** · rilevanza 🔴 altissima

La finestra Segnali (F11), divisa in due. In alto l'elenco FONTI con la barra comandi «Fonti» (nuova, modifica, elimina, aggiorna) e le colonne: Nome Servizio, Nome File, Url, Ricarica Automaticamente, Intervallo, Escludi N.V., Riconoscimento Sel, Ultimo Agg. Sono presenti le due fonti predefinite «Segnali Importati» e «Segnali Creati». In basso l'elenco SEGNALI con la sua barra comandi, il toggle «Solo Validi» e i filtri «Filtro Provider», «Filtro Nome Mercato», «Filtro Marketid»; a destra lo stato «Nessun segnale disponibile». Colonne: Fonte, [Provider], Data, Sport, [Handicap], [EventId], [MarketId], [SelectionId], [EventName], [MarketName], [SelectionName], [MarketType], Inizio, [BetType].

*Usare per:* Mostrare dove compaiono i segnali scritti da BetRelay e come verificarli. Le colonne fra parentesi quadre sono quelle LETTE dal CSV: corrispondono esattamente alle 14 colonne che scrive il bridge. Il manuale ufficiale descrive questa finestra a parole ma NON la mostra.

*Privacy:* pulito — nessun dato di conto, elenco segnali vuoto

### 03-20260708-170658.png

**Dialog «Fonte Segnali» — menu Riconoscimento selezioni aperto** · rilevanza 🔴 altissima

La finestra di creazione/modifica di una fonte segnali. Campi nell'ordine: «Nome Servizio» (testo libero); scelta esclusiva fra «URL» (con campo indirizzo) e «Nome File» (con campo percorso e pulsante sfoglia); casella «Aggiorna automaticamente ogni» con intervallo in formato hh:mm:ss; casella «Escludi automaticamente segnali non validati»; menu «Riconoscimento selezioni» qui aperto sulle due sole opzioni disponibili: «MarketId, SelectionId» e «EventName,MarketType,SelectionName». In fondo Annulla e Ok.

*Usare per:* È IL passo centrale del collegamento BetRelay→XTrader: si sceglie «URL», si incolla l'indirizzo personale del feed (`https://…/feed/<slug>.csv?token=xt_…`) e si imposta il refresh automatico. «Nome File» è la variante del Bridge locale, che scrive un file su disco: per il servizio web non funziona. Il manuale non mostra questa dialog.

*Privacy:* pulito — tutti i campi vuoti

### 04-20260708-170716.png

**Dialog «Fonte Segnali» — menu Lingua Palinsesto aperto** · rilevanza 🔴 altissima

La stessa dialog «Fonte Segnali», con in più il campo «Lingua Palinsesto» che compare sotto «Riconoscimento selezioni». Il menu è aperto sulle tre opzioni disponibili: EN, IT, ES (valore corrente EN).

*Usare per:* Spiegare l'allineamento della lingua: le tre lingue della fonte XTrader sono esattamente IT/EN/ES, le stesse che BetRelay espone come lingua CSV e lingua sorgente. È il campo che determina in che lingua XTrader cerca nomi evento/mercato/selezione quando il riconoscimento è per nomi.

*Privacy:* pulito

### 05-20260708-170856.png

**Filtro Mercati** · rilevanza 🟠 alta

La finestra Filtro Mercati (F3). A sinistra tre elenchi a caselle con i contatori: Nazioni [0 / 14 sel.], Competizioni [0 / 25 sel.], Tipi Mercato [0 / 25 sel.], ognuno coi pulsanti Tu/Ne (tutti/nessuno). In alto le schede Calcio, Filtro Nazioni, Filtro Tipi Mercato e il menu «Filtri Salvati» con i comandi salva/duplica/info/elimina. Al centro i riquadri «Operatività mercati» (Pre-match e live), «Orario inizio» (Prossime 2 ore, Prossime 6 ore, Oggi, Domani, Entro 7 giorni, Qualsiasi), «In gioco» (Sì/No/Entrambi), «Video Live», «Minimo Abbinate», «Bet Delay Models» (PASSIVE/DYNAMIC). A destra «Nome Evento», BSP (Tutti/Sì/No), «In ordine abbinate», «Mie Scommesse», la casella «Segnali» e il pulsante «Filtri Selezioni». Sotto la tabella dei risultati con Nazione, Orario, Live, Evento, Competizione, Mercato, Abbinata, N. Sel.; in basso «668 mercati elencati».

*Usare per:* Selezionare i mercati su cui applicare le strategie. La casella «Segnali» filtra i soli mercati che hanno un segnale: è il modo di isolare i mercati arrivati da BetRelay.

*Privacy:* pulito — solo palinsesto pubblico (squadre, competizioni, importi abbinati di mercato)

### 06-20260708-171008.png

**Filtro Mercati — selettore dello sport aperto** · rilevanza 🟡 media

La stessa finestra Filtro Mercati con il menu dello sport aperto in alto a sinistra sulle tre voci disponibili: Calcio, Tennis, Basket.

*Usare per:* Mostrare che il filtro lavora per sport e quali sport sono disponibili.

*Privacy:* pulito

### 07-20260708-171054.png

**Processors Mercati — vuota** · rilevanza 🟠 alta

La finestra Processors Mercati (ALT+MAIUSC+M) appena aperta. Barra comandi: «Nuovo Processor», «Elimina Processor», «Avvia», «Arresta», «Arresta Tutti» (gli ultimi tre disattivati). Tabella con le colonne Nome e Stato, vuota. In basso «0 Processors Mercati, 0 attivi».

*Usare per:* Punto di partenza per automatizzare l'applicazione di una strategia ai mercati filtrati.

*Privacy:* pulito

### 08-20260708-171117.png

**Processors Mercati — scheda Parametri di un nuovo processor** · rilevanza 🟠 alta

Un processor appena creato («Market Processor #1», Stato OFF) con la scheda «Parametri» aperta. Campi: Nome; «Tipo Evento» (<Selezionare>); «Filtro Mercati» (<Filtri Salvati>); «Frequenza di aggiornamento» (<Selezionare>); «Mercati che iniziano in» (2 ore); riquadro «Strategia» con un pulsante «>» per scegliere la strategia; «Durata dell'intervallo di riferimento»; «Numero massimo applicazioni nell'intervallo di riferimento» (0); casella «Sostituisci strategie già applicate ma non in esecuzione». Accanto a Parametri c'è la scheda «Log».

*Usare per:* Spiegare come si automatizza: il processor prende un filtro mercati salvato e applica una strategia ai mercati che entrano nel filtro, a cadenza.

*Privacy:* pulito — nome di default generato dal programma

### 09-20260708-171138.png

**Processors Mercati — scheda Log** · rilevanza 🟡 media

Lo stesso processor con la scheda «Log» aperta: tabella vuota con le colonne Data/ora e Descrizione.

*Usare per:* Dove leggere cosa ha fatto un processor (diagnosi: strategia applicata o no).

*Privacy:* pulito

### 10-20260708-171225.png

**Processors Mercati — scelta della strategia** · rilevanza 🟠 alta

La scheda Parametri con il menu «Strategia» aperto dal pulsante «>»: mostra l'unica strategia disponibile nell'installazione, «SIG_PREMATCH_BASE».

*Usare per:* Mostrare come si aggancia la strategia al processor. Il nome suggerisce una strategia pre-match basata sui segnali.

*Privacy:* ATTENZIONE: compare «SIG_PREMATCH_BASE», nome di una strategia personale del proprietario. Non è un dato sensibile ma è materiale suo.

## `azioni-se-vero/` — Azioni (ramo «se vero»)

### 01-20260708-165342.png

**Dialog «Nuova Azione» — menu «Tipo di Azione» aperto** · rilevanza 🔴 altissima

L'elenco COMPLETO dei tipi di azione disponibili in una regola di automazione, raggruppati per colore. Giallo (scommesse e notifiche): Piazza Scommessa · Piazza Scommesse su Segnali · Piazza Scommessa Condizionata · Cancella Scommessa/e · Sposta Scommesse · Dutching · Esegui Cash Out · Void Selezione · Mostra Messaggio · Riproduci Suono · Invia E-mail · Invia Messaggio Telegram. Verde (interfaccia): Aggiungi Mercato ai Preferiti · Apri Finestra Mercato · Evidenzia Selezione. Viola (controllo regole): Resetta Numero Esecuzioni Regola · Attiva / Disattiva Regola · Arresta Strategia. Azzurro (memoria): Imposta / Modifica Valore Memorizzato. In alto «Nome Azione» con pulsante «Crea Nome»; in basso «Rinomina Automaticamente», «Priorità esecuzione», «No log», Annulla/Ok.

*Usare per:* È l'INDICE delle azioni: da qui un assistente sa quali azioni esistono e quale proporre. Per il bridge quella giusta è «Piazza Scommesse su Segnali».

*Privacy:* pulito — nome di default «Nuova Azione #2»

### 02-20260708-165425.png

**Dialog «Nuova Azione» — menu «Priorità esecuzione» aperto** · rilevanza 🟡 media

La dialog senza tipo di azione scelto, col menu «Priorità esecuzione» aperto in basso: «Non assegnata» più i valori da 1 a 10.

*Usare per:* Spiegare l'ordine di esecuzione quando una regola ha più azioni.

*Privacy:* pulito

### 03-20260708-165503.png

**Azione «Piazza Scommessa»** · rilevanza 🟠 alta

Il piazzamento manuale-automatico classico. «Azione da eseguire su»: Questo mercato / Altro mercato dell'evento. «Selezione»: criterio per individuare la selezione. Riquadro «Piazza su»: Selezione indicata / Tutte le Selezioni / Tutte le Sel. tranne indicata. «Azione» PUNTA (o BANCA), «Tag», tipo di quota. Riga stake: Stake / Fisso / importo. «Persistenza» (NESSUNA), «Fill or Kill» con secondi, «FOK Istantaneo». Riquadro «Trading (Scommessa Banca)»: Trading, Smart, Offset in Ticks, «Ripeti se successo, numero ripetizioni». Riquadro «Stop Loss»: Attiva Stop Loss, «Attiva a» e «Piazza a» in Ticks, Smart, Insegui.

*Usare per:* Confronto con l'azione da segnali: qui la selezione la scegli tu, lì la porta il segnale.

*Privacy:* pulito

### 04-20260708-165534.png

**Azione «Piazza Scommesse su Segnali»** · rilevanza 🔴 altissima

L'azione che consuma i segnali di BetRelay. «Azione da eseguire su»: Questo mercato / Altro mercato dell'evento. Caselle: «Usa quota segnale se indicata», «Solo segnali mai utilizzati», «Modula lo Stake con dato Points del segnale, se disponibile». Campo «Provider» (filtro per nome provider, confronto non case-sensitive). A destra «Tag» e «Persistenza». Due riquadri simmetrici: «Segnali Punta» con «Piazza su Segnali Punta» e «Ignora limiti quote punta» + riga stake (Stake/Fisso/importo) e tipo di quota; «Segnali Banca» con gli equivalenti «Piazza su Segnali Banca» e «Ignora limiti quote banca». I controlli dello stake restano disattivati finché non si spunta il rispettivo «Piazza su Segnali…».

*Usare per:* È il punto in cui il segnale scritto da BetRelay diventa una scommessa. «Usa quota segnale» consuma la colonna Price; «Modula lo Stake con Points» consuma Points; «Ignora limiti quote» bypassa MinPrice/MaxPrice; «Provider» filtra per la colonna Provider; «Solo segnali mai utilizzati» evita di riusare un segnale già giocato. Punta=PUNTA, Banca=BANCA della colonna BetType.

*Privacy:* pulito — tutti i campi ai valori di default

### 05-20260708-165559.png

**Azione «Piazza Scommessa Condizionata»** · rilevanza 🟡 media

Piazzamento in due tempi. Oltre a «Azione da eseguire su» e «Selezione»: «Azione» (PUNTA/BANCA), «Quota di attivazione» (tipo di quota) e «Quota di piazzamento» (tipo di quota), riga stake Stake/Fisso/importo, caselle «Insegui miglior quota book» e «Mantieni se mercato sospeso».

*Usare per:* Spiegare la differenza fra quota che ARMA l'ordine e quota a cui viene piazzato.

*Privacy:* pulito

### 06-20260708-165641.png

**Azione «Cancella Scommessa/e»** · rilevanza 🟡 media

«Azione da eseguire su» qui ha tre opzioni: Questo mercato / Altro mercato dell'evento / Tutti i mercati monitorati. «Selezione» + casella «Tutte le selezioni del mercato». «Lato» PUNTA/BANCA. Riquadro «Tipo di scommesse da cancellare»: Non Abbinate, Stop Loss Trade, Chiusure Trade, Scommesse Condizionate. Riquadro «Scommesse Non Abbinate»: Tutte / Integre / Parzialmente Abbinate. Campo «Tag». Riquadro «Quote delle scommesse»: Qualsiasi / Singola quota / Range di quote.

*Usare per:* Ritirare ordini non abbinati. Il campo «Tag» è la leva per cancellare selettivamente le scommesse generate da una specifica azione.

*Privacy:* pulito

### 07-20260708-165714.png

**Azione «Sposta Scommesse»** · rilevanza ⚪ bassa

Sposta ordini già a mercato su una quota diversa. Campi: «Azione da eseguire su», «Selezione», riquadro «Sposta su» (Selezione indicata / Tutte le Selezioni / Tutte le Sel. tranne indicata), «Lato» PUNTA, «Quota Attuale» (tipo di quota) e «Nuova Quota» (tipo di quota).

*Usare per:* Gestione ordini, non pertinente al flusso dei segnali.

*Privacy:* pulito

### 08-20260708-165747.png

**Azione «Dutching»** · rilevanza ⚪ bassa

Ripartizione dello stake su più selezioni. Campi: «Lato» PUNTA, «Tipo» Stake Disponibile, «Quote» Quota disponibile con scostamento (più/meno, valore, Ticks), «Importo» Fisso in €, «Tag», «Persistenza». «Criterio di ordinamento delle selezioni» con casella «Inverti ordine». Elenco «Posizioni delle selezioni» numerato con «Seleziona tutte»/«Deseleziona tutte» e la nota: «eventuali posizioni al di fuori del numero delle selezioni del mercato non vengono considerate». In fondo «Cancella Scommesse del mercato» e «Fill Or Kill Istantaneo».

*Usare per:* Non pertinente al flusso dei segnali.

*Privacy:* pulito

### 09-20260708-165832.png

**Azione «Esegui Cash Out»** · rilevanza ⚪ bassa

«Azione da eseguire su» con tre opzioni (Questo mercato / Altro mercato dell'evento / Tutti i mercati monitorati) e «Selezione». Caselle «Cancella scommesse in attesa» e «Cash Out Totale». Riquadro «Tipo Cash Out»: Distribuito / Selezione. «Tipo di quota» (Quota disponibile). «Persistenza» NESSUNA.

*Usare per:* Chiusura anticipata di una posizione. Non pertinente al flusso dei segnali.

*Privacy:* pulito

### 10-20260708-165900.png

**Azione «Void Selezione»** · rilevanza ⚪ bassa

Layout quasi identico a Esegui Cash Out: «Azione da eseguire su» a tre opzioni, «Selezione», casella «Cancella scommesse in attesa», riquadro «Tipo Cash Out» (Distribuito / Selezione), «Persistenza». Manca la casella «Cash Out Totale» e il tipo di quota.

*Usare per:* Azzerare l'esposizione su una singola selezione.

*Privacy:* pulito

### 11-20260708-165927.png

**Azione «Mostra Messaggio»** · rilevanza 🟡 media

Area di testo «Messaggio:» ampia; casella «Mostra sintesi della valutazione delle condizioni della regola»; riquadro «Modalità di visualizzazione» con le quattro alternative: Come finestra di dialogo · In log automazione · In file di testo (con campo percorso e pulsante «Imposta…») · Nella Finestra di Output, più la casella «Con data / ora».

*Usare per:* Diagnosticare una strategia: «Mostra sintesi della valutazione delle condizioni» dice PERCHÉ una regola è scattata o no. Utile quando un segnale del bridge arriva ma la scommessa non parte.

*Privacy:* pulito

### 12-20260708-165948.png

**Azione «Riproduci Suono»** · rilevanza ⚪ bassa

Dialog minima: due pulsanti «Seleziona» e «Test» per scegliere e provare il file audio.

*Usare per:* Avviso acustico quando una regola scatta.

*Privacy:* pulito

### 13-20260708-170012.png

**Azione «Invia E-mail»** · rilevanza 🟡 media

Configurazione SMTP completa dentro l'azione: «SMTP Server» + «Porta» (25), «Username», «Password», «Da (Nome)», «Da (Indirizzo)», casella «Usa SSL/TLS», «A (Indirizzo)», «Oggetto», area «Messaggio» e casella «Allega sintesi della valutazione delle condizioni della regola».

*Usare per:* Notifiche via email. NOTA DI SICUREZZA per l'assistente: la password SMTP si inserisce in chiaro in questa dialog — non è un posto dove mettere credenziali importanti.

*Privacy:* pulito — tutti i campi vuoti, nessuna credenziale visibile

### 14-20260708-170037.png

**Azione «Invia Messaggio Telegram»** · rilevanza 🟠 alta

Dialog con la sola area «Testo Messaggio:» a piena altezza (la configurazione del bot Telegram sta altrove, non in questa dialog).

*Usare per:* È il lato XTrader del cerchio che si chiude: BetRelay scrive il segnale nel CSV, XTrader piazza e — con questa azione — rimanda un messaggio su Telegram. La tab «✅ Conferme» del bridge legge quei messaggi e rimuove subito il segnale attivo dal CSV senza aspettare il timeout.

*Privacy:* pulito

### 15-20260708-170103.png

**Azione «Aggiungi Mercato ai Preferiti»** · rilevanza ⚪ bassa

Azione del gruppo verde (interfaccia). Oltre a «Azione da eseguire su» c'è un solo campo: «Nota:».

*Usare per:* Marcare un mercato per riguardarlo dopo.

*Privacy:* pulito

### 16-20260708-170134.png

**Azione «Apri Finestra Mercato»** · rilevanza ⚪ bassa

Riquadro «Apri nella pagina:» con le tre interfacce di trading disponibili: Griglia · Ladder · Traderscopio.

*Usare per:* Portare a schermo il mercato quando una regola scatta. Le stesse tre interfacce in cui il manuale dice che i segnali compaiono con la lettera P (PUNTA) o B (BANCA).

*Privacy:* pulito

### 17-20260708-170228.png

**Azione «Evidenzia Selezione»** · rilevanza ⚪ bassa

«Selezione» + un pulsante «Colore» (giallo nell'esempio) e «Durata» in secondi (30000).

*Usare per:* Segnalare visivamente una selezione.

*Privacy:* pulito

### 18-20260708-170254.png

**Azione «Resetta Numero Esecuzioni Regola» — menu Regola aperto** · rilevanza 🟡 media

Un solo campo «Regola» col menu aperto: «<Selezionare la regola>» e «Nuova Regola #1», cioè le regole definite nella strategia corrente.

*Usare per:* Azzerare il contatore di esecuzioni di una regola, così può riscattare. Utile quando una regola è limitata a N esecuzioni.

*Privacy:* pulito — nome di default

### 19-20260708-170316.png

**Azione «Attiva / Disattiva Regola»** · rilevanza 🟡 media

Campo «Regola» più il riquadro «Stato Regola» con le due opzioni Attiva / Disattiva.

*Usare per:* Accendere o spegnere una regola da un'altra regola: è il modo di costruire strategie a stati.

*Privacy:* pulito

### 20-20260708-170340.png

**Azione «Arresta Strategia»** · rilevanza 🟡 media

Riquadro «Strategie da arrestare» con quattro portate crescenti: Questa Strategia · Tutte le strategie in esecuzione su questo mercato · Tutte le strategie in esecuzione sui mercati dell'evento · Tutte le strategie in esecuzione su qualunque mercato.

*Usare per:* Fermata d'emergenza. L'ultima opzione è l'interruttore generale: utile in una regola di sicurezza.

*Privacy:* pulito

### 21-20260708-170505.png

**Azione «Imposta / Modifica Valore Memorizzato»** · rilevanza 🟡 media

«Azione da eseguire su» (Questo mercato / Altro mercato dell'evento) e «Selezione» (disattivata finché non serve). «Nome del Valore Memorizzato» (testo libero). Riquadro «Dato da memorizzare»: «Valore del Mercato» (<Non definito>) oppure «Valore della Selezione» (<Non definito>) con la casella «Memorizza il valore in tutte le selezioni». Campo «Nota». Riquadro «Memorizza il valore a livello di»: Mercato o Selezione / Strategia / Applicazione, più la casella «Aggiungi al valore preesistente».

*Usare per:* Sono le variabili dell'automazione: si salva un dato a un certo istante e lo si rilegge in una condizione. Il livello «Applicazione» rende la variabile visibile a tutte le strategie.

*Privacy:* pulito

## `azioni-se-vero-se-falso/` — Gestore Strategie — rami «se vero» / «se falso»

### 01-20260706-194027.png

**Automazione — Gestore Strategie, nodo «azioni se vero» selezionato** · rilevanza 🟠 alta

La finestra principale dell'automazione (ALT+S), in tre riquadri. Barra comandi: Nuova Strategia · Elimina Strategia · Clona Strategia · Salva Tutte · Apri · Salva · Salva con nome… · Nuova Azione. A sinistra l'elenco «Nome Strategia» con le strategie dell'installazione. Al centro l'albero della strategia selezionata: «Copia di Nuova Strategia #1» → «Valori Predefiniti» e «Nuova Regola #1» → «0 Condizioni [AND]», «0 azioni se vero (0 attive)», «0 azioni se falso (0 attive)»; qui è selezionato il nodo «azioni se vero». A destra il pannello descrittivo: «Azioni della regola per condizioni verificate — Nuova Regola #1 — 0 azioni se vero (0 attive)». In basso la casella «Nomi Files» e lo stato «<file non salvato>».

*Usare per:* È la MAPPA STRUTTURALE dell'automazione, la prima cosa che un assistente deve saper spiegare: una strategia contiene regole; ogni regola ha un blocco di condizioni combinate in AND e due rami di azioni, «se vero» e «se falso». L'asterisco accanto a un nome segnala modifiche non salvate.

*Privacy:* ATTENZIONE: nell'elenco compare «SIG_PREMATCH_BASE», nome di una strategia personale del proprietario.

### 02-20260706-194058.png

**Automazione — Gestore Strategie, nodo «azioni se falso» selezionato** · rilevanza 🟡 media

La stessa finestra con selezionato il nodo «0 azioni se falso (0 attive)»; il pannello di destra cambia in «Azioni della regola per condizioni NON verificate — Nuova Regola #1 — 0 azioni se falso (0 attive)».

*Usare per:* Documenta il ramo «se falso». Nota del proprietario: i due rami offrono lo STESSO set di azioni — quello dell'immagine azioni-se-vero/01. Cambia solo quando vengono eseguite: «se vero» quando tutte le condizioni sono soddisfatte, «se falso» quando non lo sono.

*Privacy:* ATTENZIONE: stesso nome di strategia personale «SIG_PREMATCH_BASE».

## `condizioni/` — Condizioni e struttura delle strategie

### 01-20260706-165210.png

**Automazione — Gestore Strategie appena aperto** · rilevanza 🟠 alta

La finestra dell'automazione all'apertura: elenco «Nome Strategia» con la sola strategia esistente, pannello destro con l'invito «Selezionare una strategia o crearne una nuova», barra di stato «Nessuna strategia selezionata». Nella barra comandi sono attivi solo «Nuova Strategia» e «Apri»: tutti gli altri (Elimina, Clona, Salva Tutte, Salva, Salva con nome) sono disattivati finché non si seleziona qualcosa.

*Usare per:* Punto di partenza: da qui si crea o si apre una strategia.

*Privacy:* ATTENZIONE: nome di strategia personale «SIG_PREMATCH_BASE».

### 02-20260706-165253.png

**Gestore Strategie — nuova strategia creata, pannello riepilogo** · rilevanza 🟠 alta

Dopo «Nuova Strategia»: nell'albero compare «Nuova Strategia #1» con «Valori Predefiniti» e «Nuova Regola #1» (già creata in automatico) con i suoi tre nodi «0 Condizioni [AND]», «0 azioni se vero», «0 azioni se falso». Il pannello destro mostra il riepilogo della strategia: «Questa strategia non ha vincoli sul tipo di mercato di applicazione», «…non ha vincoli sulla nazione di applicazione», «Numero regole della strategia: 1», «Numero totale condizioni: 0 (0 attive)», «Numero totale azioni: 0 (0 attive)», «Livello massimo profondità condizioni: 1», «Numero massimo scommesse su selezione: 100000», «Numero massimo scommesse su mercato: 100000». L'asterisco nel nome a sinistra indica modifiche non salvate.

*Usare per:* Spiegare cosa contiene una strategia appena creata e dove leggere i suoi limiti. I due «Numero massimo scommesse» sono i paracadute anti-raffica a livello di strategia.

*Privacy:* ATTENZIONE: «SIG_PREMATCH_BASE» nell'elenco.

### 03-20260706-165321.png

**Gestore Strategie — conferma di cancellazione strategia** · rilevanza ⚪ bassa

Finestra «Avvertenza» con la domanda «Si conferma la cancellazione della strategia <Nuova Strategia #1> ?» e i pulsanti No / Sì.

*Usare per:* Mostrare che l'eliminazione di una strategia chiede conferma esplicita.

*Privacy:* ATTENZIONE: «SIG_PREMATCH_BASE» nell'elenco a sinistra.

### 04-20260706-165419.png

**Gestore Strategie — strategia clonata** · rilevanza 🟡 media

Ritaglio sull'albero e sul pannello riepilogo dopo «Clona Strategia»: la copia si chiama «Copia di Nuova Strategia #1» e riporta gli stessi contatori (1 regola, 0 condizioni, 0 azioni, profondità 1, 100000 scommesse max su selezione e su mercato).

*Usare per:* Il clone è il modo di partire da una strategia esistente senza toccarla.

*Privacy:* pulito

### 05-20260706-165445.png

**Gestore Strategie — nodo «Valori Predefiniti»** · rilevanza 🟡 media

Selezionando «Valori Predefiniti» il pannello destro mostra solo «Numero Valori Predefiniti: 0».

*Usare per:* I valori predefiniti sono le costanti della strategia, richiamabili poi dalle condizioni e dalle formule invece di ripetere numeri sparsi.

*Privacy:* pulito

### 06-20260706-165519.png

**Gestore Strategie — pannello descrittivo del nodo «Regola»** · rilevanza 🟠 alta

Il pannello destro con la scheda completa di «Nuova Regola #1». Stato ATTIVA. «Condizione di avvio non impostata → La regola sarà eseguita immediatamente.» «Condizione di stop non impostata → La regola sarà fermata solo su azione manuale o quando sarà raggiunto il numero di esecuzioni programmato.» «Numero esecuzioni richieste: 1». Sezione CONDIZIONI: «Non ci sono condizioni attive nella regola, eventuali azioni saranno eseguite subito. Utilizzare il pulsante 'Nuova condizione'… Legame tra le condizioni: AND. Tutte le condizioni devono essere verificate.» Sezione AZIONI: «Non ci sono azioni impostate nella regola…».

*Usare per:* È la spiegazione in chiaro del comportamento di una regola, scritta dal programma stesso. AVVERTENZA IMPORTANTE per l'assistente: una regola SENZA condizioni attive esegue le azioni SUBITO — è la trappola numero uno di chi costruisce la prima strategia.

*Privacy:* pulito

### 07-20260706-165543.png

**Gestore Strategie — pannello descrittivo del «Nodo Condizioni»** · rilevanza 🟠 alta

Scheda del nodo condizioni: stato ATTIVO, «Non ci sono nodi o condizioni attive nel nodo condizioni. Questo nodo se attivo risulterà sempre verificato.» «Legame tra le condizioni: AND. Tutte le condizioni devono essere verificate.» In grigio la nota: «Un nodo condizioni può contenere più condizioni o altri nodi condizioni. Tramite il comando modifica è possibile specificare il legame esistente tra le condizioni e i nodi in esso presenti.»

*Usare per:* Spiegare che le condizioni si annidano: un nodo può contenere altri nodi, e ogni nodo ha il suo legame (AND/OR). È così che si costruisce una logica composta. Nota: un nodo vuoto è SEMPRE verificato.

*Privacy:* pulito

### 08-20260706-165603.png

**Gestore Strategie — pannello del nodo «azioni se vero»** · rilevanza 🟡 media

Pannello destro: «Azioni della regola per condizioni verificate — Nuova Regola #1 — 0 azioni se vero (0 attive)».

*Usare per:* Identificare il ramo delle azioni eseguite quando le condizioni sono soddisfatte.

*Privacy:* pulito

### 09-20260706-165624.png

**Gestore Strategie — pannello del nodo «azioni se falso»** · rilevanza 🟡 media

Pannello destro: «Azioni della regola per condizioni NON verificate — Nuova Regola #1 — 0 azioni se falso (0 attive)».

*Usare per:* Identificare il ramo alternativo.

*Privacy:* pulito

### 10-20260706-165913.png

**Dialog «Nuovo Valore Predefinito» — tipo Quota** · rilevanza 🟡 media

Dialog modale con tre campi: «Nome» (segnaposto «Scegliere un nome per il valore predefinito»), «Tipo di Valore Predefinito» impostato su «Quota» (icona verde P), «Valore» con spinner (2). Annulla/Ok. Nella barra comandi è comparso il pulsante «Nuovo Valore Predefinito».

*Usare per:* Definire una costante riutilizzabile di tipo quota, da richiamare nelle condizioni invece di ripetere il numero.

*Privacy:* ATTENZIONE: «SIG_PREMATCH_BASE» nell'elenco a sinistra.

### 11-20260706-165935.png

**Dialog «Nuovo Valore Predefinito» — tipo Stake - P/L** · rilevanza 🟡 media

La stessa dialog con «Tipo di Valore Predefinito» impostato su «Stake - P/L» (icona rossa): il campo «Valore» diventa un importo in € (0,00).

*Usare per:* Definire una costante monetaria (stake o profitto/perdita di riferimento).

*Privacy:* ATTENZIONE: «SIG_PREMATCH_BASE» nell'elenco.

### 12-20260706-170011.png

**Dialog «Modifica Regola» — scheda «Condizioni temporali Avvio / Stop»** · rilevanza 🟠 alta

Tre schede: «Condizioni temporali Avvio / Stop», «Numero Esecuzioni», «Descrizione». Nella prima, due riquadri gemelli. «Condizione di avvio (In mancanza, regola attiva all'avvio strategia)» con sei alternative: Orario fisso (con data/ora e i pulsanti rapidi Adesso, +5 min, +1h) · Relativo a Inizio Evento · Relativo a stato mercato «In Gioco» · Relativo a stato mercato «Ritorna Aperto» · Relativo a prima esecuzione di una regola della strategia · Relativo ad avvio Strategia; sotto, un valore numerico e l'unità (Secondi). «Condizione di stop» ha esattamente le stesse sei alternative. In basso una casella «Colore» per marcare la regola.

*Usare per:* È il TEMPO della regola. Per una strategia da segnali pre-match il riferimento tipico è «Relativo a Inizio Evento» (es. parti 30 minuti prima del calcio d'inizio). Se non si imposta nulla, la regola è attiva dall'avvio della strategia.

*Privacy:* pulito — la data mostrata è quella della cattura

### 13-20260706-170051.png

**Dialog «Modifica Regola» — scheda «Numero Esecuzioni»** · rilevanza 🔴 altissima

Casella «Numero esecuzioni illimitato»; campo «Numero esecuzioni» (1); casella «Considera nel conteggio le regole con condizioni non verificate»; campo «Attesa dopo esecuzione» (10 sec.).

*Usare per:* È IL FRENO ANTI-RAFFICA, il parametro di sicurezza più importante di una regola. «Numero esecuzioni» = quante volte la regola può scattare; «Attesa dopo esecuzione» = quanto aspetta prima di poter riscattare. Per una strategia guidata dai segnali del bridge questi due campi sono ciò che impedisce di piazzare più scommesse sullo stesso segnale. Un assistente deve SEMPRE farli impostare consapevolmente e diffidare di «Numero esecuzioni illimitato».

*Privacy:* pulito

### 14-20260706-170129.png

**Dialog «Modifica Regola» — scheda «Descrizione»** · rilevanza ⚪ bassa

Terza scheda della dialog «Modifica Regola»: una sola grande area di testo libera, senza altri controlli, in cui annotare a cosa serve la regola. Il testo resta dentro la strategia e ricompare nel pannello descrittivo del Gestore Strategie.

*Usare per:* Documentare la regola dentro la strategia stessa.

*Privacy:* pulito

### 15-20260706-170200.png

**Dialog «Nuovo Nodo Condizioni»** · rilevanza 🔴 altissima

Dialog con il legame logico del nodo: «AND (Tutte le condizioni devono essere verificate)» · «OR (Almeno una condizione deve essere verificata)» · «Minimo / Massimo numero condizioni verificate» con i due campi «Numero minimo di condizioni verificate» e «Numero massimo di condizioni verificate». In fondo la casella «Negazione logica». Nella barra comandi sono comparsi «Nuova Condizione» e «Nuovo Nodo Condizioni».

*Usare per:* È la GRAMMATICA LOGICA dell'automazione: oltre ad AND e OR esiste il quantificatore «almeno N / al più M condizioni vere», e la «Negazione logica» inverte l'esito dell'intero nodo. Annidando nodi si costruisce qualunque espressione booleana.

*Privacy:* ATTENZIONE: «SIG_PREMATCH_BASE» nell'elenco a sinistra.

### 16-20260706-170233.png

**Dialog «Nuova Condizione» — vuota** · rilevanza 🟠 alta

La dialog di creazione di una condizione prima di scegliere il tipo: «Nome Condizione» («Nuova Condizione #1») con pulsante «Crea Nome», «Tipo di Condizione» su «<Selezionare il tipo di condizione>». In basso le caselle «Rinomina Automaticamente» e «Nega la condizione», più Annulla/Ok.

*Usare per:* Lo scheletro comune di TUTTE le condizioni: il corpo della dialog cambia in base al tipo scelto. «Nega la condizione» inverte la singola condizione (da non confondere con la «Negazione logica» del nodo, che inverte il gruppo).

*Privacy:* pulito

### 17-20260706-181819.png

**Dialog «Nuova Condizione» — elenco COMPLETO dei tipi di condizione** · rilevanza 🔴 altissima

L'indice di tutti i tipi di condizione, raggruppati per colore, con a destra una sigla di ambito (S = selezione, M = mercato, MS = entrambi). BIANCO — dati di mercato: Valore Quota della Selezione (S) · Ampiezza spread punta/banca del book (S) · Tempo non sospeso del Mercato (M) · Percentuale book del Mercato (M) · Stato del Mercato (M) · Stato Selezione (S) · Mercato in gioco (M) · Tipo Mercato (M) · Numero selezioni del Mercato (M) · Liquidità Disponibile (MS) · Volume Scambiato (MS) · Volume Percentuale Selezione (S) · Valore Scambiato Selezione (S) · Confronta Quote Selezioni (S) · Confronta Quote Storico Selezioni (S) · Confronta Volume Selezioni (S). VERDE — posizione e conto: Valore Cash Out (MS) · Conta scommesse (MS) · Profitto / Perdita potenziale (MS) · Profitto / Perdita Conto (MS) · Scommessa In Piazzamento (MS) · Posizione trading della Selezione (S) · Saldo Conto Betfair · Posizione mie scommesse rispetto al book (S) · Tempo da ultimo Abbinamento Scommessa (MS). ROSA/GIALLO — risultato sportivo, marcate [RIS]: Risultato Impossibile · Numero goals della partita · Risultato della partita · Relazione tra goals delle squadre · Eventi squadra · Tempo partita e tempo di gioco · Goal Segnato · Tennis Punteggio · Tennis Punteggio Relativo · Tennis Servizio · Tennis Punto Vinto. AZZURRO — corse: Stato Corsa · Cronometro Corsa. ROSA: Numero esecuzioni regola · Strategia Applicata (M). CIANO: Formula (M) · Valore Memorizzato (MS).

*Usare per:* È l'INDICE delle condizioni: da qui un assistente sa cosa si può chiedere a XTrader e con quale nome esatto. Il marcatore [RIS] indica le condizioni che richiedono il feed risultati.

*Privacy:* pulito

### 18-20260706-182041.png

**Condizione «Valore Quota della Selezione» — menu del criterio di selezione** · rilevanza 🔴 altissima

La dialog con «Tipo di Condizione» = «Valore Quota della Selezione» e il menu «Selezione» aperto sull'elenco completo dei criteri con cui individuare una selezione: Per Riga Betfair Crescente · Per Posizione Quote Punta Crescenti · Per Posizione Quote Banca Crescenti · Per Posizione Volume Decrescente · Per Posizione Ultima Quota Scambiata Crescente · Per nome che contiene · Per Posizione P/L se vince · Per nome esatto · Per Numero Sottosella · Per numero Stallo · Scelta in Esecuzione · Selezione di Applicazione Strategia Predefinita · Da Ultima Scommessa Abbinata · Qualsiasi Selezione. Sotto restano «Tipo Quota», «Condizione», «Valore di Riferimento» (Valore Fisso / Valore Predefinito / Formula) e, in fondo, «Rinomina Automaticamente» e «Nega la condizione».

*Usare per:* CRUCIALE per il bridge: fra i criteri c'è «Selezione di Applicazione Strategia Predefinita», cioè la selezione portata dal segnale. «Per nome esatto» e «Per nome che contiene» sono gli agganci alla colonna SelectionName del CSV.

*Privacy:* pulito

### 19-20260706-185503.png

**Condizione «Valore Quota della Selezione» — menu «Tipo Quota»** · rilevanza 🟠 alta

Menu «Tipo Quota» aperto sulle otto opzioni: Non definito · Migliore quota disp. per puntare · Migliore quota disp. per bancare · Ultima Quota Scambiata · Quota Massima Scambiata · Quota Minima Scambiata · Quota Più Scambiata · Quota Media Scommesse PUNTA · Quota Media Scommesse BANCA. In alto è selezionato «Altro mercato dell'evento» nel riquadro «Condizione basata sui dati di».

*Usare per:* Definire QUALE quota si sta confrontando. Per verificare che la quota del segnale sia ancora disponibile si usa «Migliore quota disp. per puntare» (o per bancare, a seconda del BetType).

*Privacy:* pulito

### 20-20260706-185533.png

**Menu «tipo di mercato» — elenco MarketType, parte 1 (A–F)** · rilevanza 🔴 altissima

Con «Altro mercato dell'evento» selezionato si apre l'elenco dei tipi di mercato Betfair, ognuno col CODICE seguito dalla descrizione italiana. Parte 1: ALT_TOTAL_GOALS · ANTEPOST_WIN · ANYTIME_ASSIST · ASIAN_HANDICAP · BOTH_TEAMS_TO_SCORE (Entrambe le squadre a segno) · CLEAN_SHEET (Reti Inviolate) · COMBINED_TOTAL (Punti Totali) · COMPETITION_SPECIALS · CORNER_ODDS (Numero di calci d'angolo) · CORRECT_SCORE (Risultato esatto) · CORRECT_SCORE_IT (Risultato esatto (IT)) · DAILY_SPECIALS · DAILY_WIN_DIST · DOUBLE_CHANCE (Doppia chance) · DRAW_NO_BET · EACH_WAY · EXTRA_TIME (Tempi Supplementari) · FASTEST_LAP · FIRST_GOAL_SCORER (Giocatore segna per primo) · FIRST_HALF_GOALS_05/15/25 (1° tempo - Totale goal 0,5/1,5/2,5) · FORECAST · FRAME_BY_FRAME_10…15.

*Usare per:* FONDAMENTALE per il bridge: questi codici sono ESATTAMENTE i valori ammessi nella colonna MarketType del CSV. Il manuale conferma che sono identici in tutte le versioni del software. Da notare CORRECT_SCORE_IT, variante italiana distinta da CORRECT_SCORE.

*Privacy:* pulito

### 21-20260706-185605.png

**Menu «tipo di mercato» — elenco MarketType, parte 2 (F–M)** · rilevanza 🔴 altissima

Seguito dell'elenco: FRAME_BY_FRAME_16…19, FRAME_BY_FRAME_9 · GOLDEN_BOOT · GOLDEN_GLOVE · GROUP_A…F_TO_QUALIFY e GROUP_A…F_WINNER · HALF_TIME (Primo tempo) · HALF_TIME_FULL_TIME (Fine primo tempo / Fine partita) · HALF_TIME_SCORE (1° tempo - Risultato esatto*) · HANDICAP (Handicap) · MAP_WINNER · MATCH_ODDS (Esito Finale) · MATCH_ODDS_AND_BTTS · MATCH_SHOTS (Totale tiri) · MATCH_SHOTS_TARGET (Totale tiri in porta) · METHOD_OF_VICTORY · MONEY_LINE · MOST_180S (Maggior numero di 180).

*Usare per:* Contiene MATCH_ODDS (Esito Finale), il mercato più usato dai canali di segnali.

*Privacy:* pulito

### 22-20260706-185650.png

**Menu «tipo di mercato» — elenco MarketType, parte 3 (N–S)** · rilevanza 🔴 altissima

Seguito: NAME_THE_FINALISTS · NONSPORT (Non Sport) · NUMBER_OF_TROPHIES · ODD_OR_EVEN (Totale Goal Pari/Dispari) · OTHER_PLACE (Altri piazzati) · OUTRIGHT_WINNER (Vincitore) · OVER_UNDER_05/15/25/35/45/55/65/75/85 (Over/Under 0,5 … 8,5 gol) · PENALTY_TAKEN · PLACE (Piazzato) · PLAYER_FOULS_1/2 · POINTS_FINISH · PROMOTION · PROMOTION_FOOTBALL · QUALIFYING_WINNER · RACE_WIN_DIST (Vincitore con distanza) · RELEGATION (Retrocessione) · REV_FORECAST · ROCK_BOTTOM · ROUND_BETTING · ROUND_LEADER · SCORE_CAST.

*Usare per:* Contiene tutta la famiglia OVER_UNDER_xx, l'altro gruppo di mercati tipico dei canali di segnali.

*Privacy:* pulito

### 23-20260706-185750.png

**Menu «tipo di mercato» — elenco MarketType, parte 4 (S–T)** · rilevanza 🟠 alta

Seguito: SENDING_OFF (Espulsione Sì/No) · SET_BETTING · SHOTS_ON_TARGET_P1/P2/P3 (Tiri in porta - 1 o più / 2 o più / 3 o più) · SPECIAL · SPECIALS_NEXT_MGR · STAGE_OF_ELIMINATION · TEAM_A_1/2/3 (Team A +1 …) · TEAM_A_TO_SCORE (Squadra di casa segna goal) · TEAM_A_WIN_TO_NIL · TEAM_B_1/2/3 · TEAM_B_TO_SCORE (Squadra ospite segna goal) · TEAM_B_WIN_TO_NIL · TEAM_WINNER_WITHOUT · TIED_MATCH · TO_BE_CLASSIFIED · TO_QUALIFY (Si Qualifica) · TO_REACH_FINAL (Arriva in finale) · TO_REACH_QUARTERS · TO_REACH_SEMIS · TO_SCORE (Giocatore segna) · TO_SCORE_2_OR_MORE · TO_SCORE_HATTRICK · TOP_10_FINISH · TOP_2_FINISH.

*Usare per:* Completamento dell'elenco dei MarketType ammessi nel CSV.

*Privacy:* pulito

### 24-20260706-185827.png

**Menu «tipo di mercato» — elenco MarketType, parte 5 (T–Y, fine)** · rilevanza 🟠 alta

Coda dell'elenco: TO_REACH_QUARTERS · TO_REACH_SEMIS · TO_SCORE · TO_SCORE_2_OR_MORE · TO_SCORE_HATTRICK · TOP_10_FINISH · TOP_2_FINISH · TOP_20_FINISH · TOP_3_FINISH · TOP_4_FINISH · TOP_4_FINISH_FT · TOP_5_FINISH · TOP_6_FINISH · TOP_7_FINISH · TOP_CONCACAF_TEAM · TOP_CONMEBOL_TEAM · TOP_EURO_TEAM · TOP_GOALSCORER · TOP_N_FINISH · TOTAL_GOALS (Goal Totali) · TOTAL_MATCH_POINTS (Total Game Score) · UNDIFFERENTIATED · WEEK_WINNER · WIN (Vincitore) · WINNER (Vincitore) · WINNER_WITHOUT · WINNING_MARGIN (Margine Vittoria) · WINNING_REGION · WITHOUT_FAV (Senza il favorito) · YOUNG_PLAYER.

*Usare per:* Chiude l'elenco completo dei MarketType. Con le parti 1-5 (immagini 20,21,22,23,24) un assistente ha il vocabolario COMPLETO della colonna MarketType del CSV.

*Privacy:* pulito

### 25-20260706-185905.png

**Condizione «Ampiezza spread punta/banca del book»** · rilevanza 🟡 media

Campi: «Condizione basata sui dati di» (Questo mercato / Altro mercato dell'evento), «Selezione», «Condizione» (operatore, qui «=»), «Valore di riferimento» espresso in TICKS, e la casella «Escludi mie scommesse nella valutazione».

*Usare per:* Misura la distanza fra migliore quota punta e migliore quota banca: uno spread largo segnala mercato illiquido. Utile come guardia prima di piazzare da segnale: se lo spread è largo, l'ordine rischia di restare non abbinato.

*Privacy:* pulito

### 26-20260706-185932.png

**Condizione «Tempo non sospeso del Mercato»** · rilevanza 🟡 media

Campi: «Condizione» (operatore), valore numerico e unità (Secondi), più la casella «Inibisci la regola se il mercato non viene trovato».

*Usare per:* Verificare che il mercato sia stabile da abbastanza tempo. La casella «Inibisci la regola se il mercato non viene trovato» è un fail-safe: senza di essa la regola potrebbe comportarsi in modo imprevisto quando il mercato di riferimento non esiste.

*Privacy:* pulito

### 27-20260706-190001.png

**Condizione «Percentuale book del Mercato» — menu «Lato del book»** · rilevanza 🟡 media

«Lato del book» col menu aperto sulle due opzioni: Book Punta / Book Banca. Poi «Condizione» (operatore) e un valore in percentuale (100,0 %).

*Usare per:* La percentuale di book misura quanto il mercato è «equo»: un book punta vicino al 100% indica un mercato ben formato. Guardia di liquidità prima di piazzare.

*Privacy:* pulito

### 28-20260706-190037.png

**Condizione «Stato del Mercato» — menu degli stati** · rilevanza 🟠 alta

«Stato del mercato» col menu aperto sui tre valori: APERTO · SOSPESO · CHIUSO. Sotto la casella «Inibisci la regola se il mercato non viene trovato».

*Usare per:* Guardia elementare e importante: piazzare solo se il mercato è APERTO. Da mettere in AND con la condizione sul segnale — un mercato sospeso non accetta scommesse.

*Privacy:* pulito

### 29-20260706-190129.png

**Condizione «Stato Selezione» — menu degli stati** · rilevanza 🟠 alta

«Stato Selezione» col menu aperto sui sette valori Betfair: ACTIVE · WINNER · LOSER · PLACED · REMOVED_VACANT · REMOVED · HIDDEN.

*Usare per:* Guardia sulla singola selezione: piazzare solo se è ACTIVE. Una selezione REMOVED (es. cavallo ritirato, giocatore fuori) non è scommettibile — importante quando la selezione arriva da un segnale scritto minuti prima.

*Privacy:* pulito

### 30-20260706-190432.png

**Condizione «Mercato in gioco»** · rilevanza 🟠 alta

Oltre a «Condizione basata sui dati di» (Questo mercato / Altro mercato dell'evento) il corpo contiene due sole alternative esclusive: «Mercato in gioco» / «Mercato NON in gioco». Nessun operatore, nessun valore: è un interruttore secco sullo stato in-play del mercato.

*Usare per:* Distinguere pre-match da live. Per una strategia da segnali pre-match si mette in AND «Mercato NON in gioco»: evita che un segnale vecchio venga giocato a partita iniziata, quando le quote sono tutt'altre.

*Privacy:* pulito

### 31-20260706-190517.png

**Condizione «Tipo Mercato»** · rilevanza 🟠 alta

Un solo campo: «Tipo di mercato è» con il menu dei MarketType (lo stesso elenco delle immagini 20-24).

*Usare per:* Vincolare la regola a un tipo di mercato preciso: es. applicarla solo su MATCH_ODDS. Corrisponde al confronto con la colonna MarketType del CSV.

*Privacy:* pulito

### 32-20260706-190550.png

**Condizione «Numero selezioni del Mercato»** · rilevanza ⚪ bassa

Casella «Solo selezioni attive», «Condizione» (operatore) e «Valore di riferimento» (1).

*Usare per:* Filtrare per numero di runner/esiti: distingue per esempio un 1X2 (3 selezioni) da un Over/Under (2).

*Privacy:* pulito

### 33-20260706-190945.png

**Condizione «Liquidità Disponibile» — menu «Lato»** · rilevanza 🟠 alta

Casella «Liquidità di tutte le selezioni»; «Lato» col menu aperto su PUNTA / BANCA; «Condizione» (operatore) e «Valore di riferimento» (1000); riquadro «Quote per il calcolo della Liquidità» con Qualsiasi / Singola quota / Range di quote.

*Usare per:* GUARDIA CHIAVE prima di piazzare da segnale: verifica che a mercato ci sia abbastanza denaro sul lato che ti serve. Senza liquidità l'ordine resta non abbinato — il manuale avverte proprio di questo quando si usa la quota del segnale.

*Privacy:* pulito

### 34-20260706-191403.png

**Condizione «Liquidità Disponibile» — stessa dialog, secondo scatto** · rilevanza 🟡 media

Ripetizione quasi identica dell'immagine 33 (menu «Lato» aperto su PUNTA/BANCA). Utile come conferma dei valori; nessun controllo nuovo.

*Usare per:* Doppione di 33.

*Privacy:* pulito

### 35-20260706-191442.png

**Condizione «Volume Scambiato»** · rilevanza 🟡 media

Casella «Volume complessivo del mercato»; «Condizione» (operatore) e «Valore di riferimento» (1000); riquadro «Quote per il calcolo del Volume» con Qualsiasi / Singola quota / Range di quote.

*Usare per:* Misura quanto è stato scambiato: un volume basso indica mercato poco affidabile. Da usare come soglia minima prima di dare seguito a un segnale.

*Privacy:* pulito

### 36-20260706-191515.png

**Condizione «Volume Percentuale Selezione»** · rilevanza ⚪ bassa

Corpo minimo: «Condizione» (operatore di confronto) e «Valore di riferimento» espresso in percentuale (1 %), oltre ai consueti selettori di mercato e selezione. Misura quanta parte del volume complessivo del mercato è concentrata sulla selezione indicata.

*Usare per:* Quota parte di volume concentrata su una selezione: segnala dove si sta muovendo il denaro.

*Privacy:* pulito

### 37-20260706-191632.png

**Condizione «Valore Scambiato Selezione» — menu «Valore»** · rilevanza 🟡 media

«Valore» col menu aperto su «Ultimo Valore Scambiato» / «Valore Scambiato Attuale». Sotto «Lato dello scambio» con tre caselle indipendenti PUNTA, BANCA, INDETERMINATO; «Condizione» (operatore); riquadro «Valore di Riferimento» con le tre modalità Valore Fisso (in €) / Valore Predefinito (menu) / Formula.

*Usare per:* Osservare il denaro appena scambiato e da che lato. Nota il pattern ricorrente: quasi ogni condizione numerica accetta un riferimento FISSO, un VALORE PREDEFINITO della strategia o una FORMULA — è la leva per rendere le strategie parametriche.

*Privacy:* pulito

### 38-20260706-191823.png

**Condizione «Confronta Quote Selezioni» — menu «Parametro Selezione A»** · rilevanza ⚪ bassa

Due selettori «Selezione A» e «Selezione B», poi «Parametro Selezione A» (menu aperto) e «Parametro Selezione B», entrambi con gli otto tipi di quota: Migliore quota disp. per puntare · Migliore quota disp. per bancare · Ultima Quota Scambiata · Quota Massima Scambiata · Quota Minima Scambiata · Quota Più Scambiata · Quota Media Scommesse PUNTA · Quota Media Scommesse BANCA.

*Usare per:* Confrontare due selezioni dello stesso mercato (es. il favorito rispetto al secondo).

*Privacy:* pulito

### 39-20260706-191858.png

**Condizione «Confronto Quote Storico Selezioni» — menu più/meno** · rilevanza 🟡 media

«Selezione» e «Selezione B», poi «Tipo Quota Selezione A» con un selettore temporale: «Adesso» oppure un valore + unità (Secondi) + «fa»; l'operatore di confronto; «Tipo Quota Selezione B» con lo stesso selettore temporale; infine uno scostamento con il menu aperto su «più / meno» e un valore in ticks.

*Usare per:* Confrontare la quota di ADESSO con quella di N secondi fa: è il modo di rilevare un movimento di quota (drift o steam). Utile per validare un segnale: se la quota si è mossa troppo dal momento in cui il segnale è stato scritto, meglio non piazzare.

*Privacy:* pulito

### 40-20260706-191927.png

**Condizione «Confronta Volume Selezioni»** · rilevanza ⚪ bassa

«Selezione A» e «Selezione B», poi «Volume Selezione A» (operatore) e «Volume Selezione B» con scostamento (più/meno) e importo in €.

*Usare per:* Confrontare il denaro scambiato su due selezioni.

*Privacy:* pulito

### 41-20260706-191955.png

**Condizione «Valore Cash Out» (gruppo verde)** · rilevanza ⚪ bassa

Caselle «Cash Out totale del mercato» e «Cash Out globale mercati monitorati». Tre modalità alternative: «Valore Cash Out» · «Valore Cash Out come percentuale della cassa» · «Valore Cash Out come percentuale dell'esposizione sul mercato». Poi «Condizione» e il riquadro «Valore di Riferimento» (Valore Fisso in € / Valore Predefinito / Formula).

*Usare per:* Chiudere in profitto o in perdita a soglia. Gestione della posizione, non del segnale.

*Privacy:* pulito

### 42-20260706-192032.png

**Condizione «Conta scommesse»** · rilevanza 🔴 altissima

Casella «Scommesse su tutto il mercato»; «Lato» (PUNTA); «Condizione» e «Valore di riferimento» (1). Tre riquadri di filtro: «Tipo Calcolo» (Numero Scommesse / Importo Scommesse Abbinato / Importo Scommesse Non Abbinato); «Stato di abbinamento» (Non abbinate / Parzialmente Abbinate / Totalmente Abbinate); «Altre Tipologie» (Chiusure Trading / Stop Loss / Scommesse Condizionate). In fondo il riquadro «Quote» (Qualsiasi / Singola quota / Range di quote).

*Usare per:* È LA GUARDIA ANTI-DOPPIONE lato XTrader, la più importante per chi piazza da segnali: «Conta scommesse = 0 sulla selezione» messa in AND impedisce di piazzare due volte sullo stesso mercato. Da consigliare SEMPRE insieme a «Solo segnali mai utilizzati» dell'azione: sono due reti diverse, una sul segnale e una sulle scommesse già a mercato.

*Privacy:* pulito

### 43-20260706-192116.png

**Condizione «Profitto / Perdita potenziale»** · rilevanza 🟡 media

Casella «Riferito al mercato» con le due opzioni Min / Max (attive solo se la casella è spuntata); «Profitto / Perdita potenziale» (operatore); riquadro «Valore di Riferimento» (Valore Fisso in € / Valore Predefinito / Formula).

*Usare per:* Limitare l'esposizione: non piazzare se la perdita potenziale supera una soglia. Rete di sicurezza sensata in una strategia che gioca in automatico.

*Privacy:* pulito

### 44-20260706-192155.png

**Condizione «Profitto / Perdita Conto»** · rilevanza 🟠 alta

«Il Profitto / Perdita Netto del conto è» con operatore e importo in €. Riquadro «Intervallo temporale»: Oggi / Ultime Ore / Ultimi Minuti (con valore). Riquadro «Tipo di Sport» con caselle: Calcio, Tennis, Basket, Rugby Union, Football Americano, Corse Cavalli, Hockey su Ghiaccio, Golf, Cricket, Rugby League, Freccette, Boxing, Pallavolo, Ciclismo, Corse di Levrieri, Motori, Sport Invernali, Politica, Scommesse speciali, Biliardo. Riquadro «Tipi di Mercato» con l'elenco a caselle dei MarketType e i pulsanti «Seleziona Nessuno» / «Seleziona Tutti».

*Usare per:* STOP LOSS GIORNALIERO: «se la perdita di oggi supera X, non piazzare più». È il paracadute che ogni strategia automatica dovrebbe avere, e va suggerito a chi collega il bridge in modalità reale.

*Privacy:* pulito — nessun importo reale, campo a 0,00

### 45-20260706-192218.png

**Condizione «Scommessa In Piazzamento»** · rilevanza 🟠 alta

Dialog minima: solo la casella «Scommesse su tutto il mercato» oltre a mercato e selezione.

*Usare per:* Evita la corsa critica: verifica se c'è già un ordine IN VIAGGIO verso Betfair non ancora confermato. Senza questa guardia una regola che riscatta subito può inviare un secondo ordine mentre il primo è ancora in volo. Da usare NEGATA («Nega la condizione») in AND: «nessuna scommessa in piazzamento».

*Privacy:* pulito

### 46-20260706-192238.png

**Condizione «Posizione trading della Selezione»** · rilevanza 🟡 media

Riquadro «Posizione trading è» con la formula esplicita «D = P/L se vince - P/L se perde» e tre caselle: Punta (D > soglia) · Equalizzata (D compreso tra +soglia e -soglia) · Banca (D < -soglia). Sotto il campo «Valore Soglia» (0,00).

*Usare per:* Sapere da che parte si è esposti su una selezione. Utile per non aprire una seconda posizione nello stesso verso quando arriva un altro segnale.

*Privacy:* pulito

### 47-20260706-192258.png

**Condizione «Saldo Conto Betfair»** · rilevanza 🟠 alta

Dialog senza riferimento a mercato o selezione: due alternative «Saldo» / «Esposizione», poi «Condizione» (operatore) e «Valore di riferimento» in € (0,00).

*Usare per:* Guardia di cassa: non piazzare se il saldo è sotto una soglia o se l'esposizione complessiva è già troppo alta. Da abbinare allo stop loss giornaliero (condizione 44) in una strategia che gira da sola.

*Privacy:* pulito — campo a 0,00, nessun saldo reale visibile

### 48-20260706-192319.png

**Condizione «Posizione mie scommesse rispetto al book»** · rilevanza 🟡 media

«Lato» (Scommesse Punta), «Condizione» (operatore) e «Valore di riferimento» (0).

*Usare per:* Sapere in che posizione della coda sta il proprio ordine: se è dietro a molto denaro, l'abbinamento è improbabile.

*Privacy:* pulito

### 49-20260706-192340.png

**Condizione «Tempo da ultimo Abbinamento Scommessa»** · rilevanza 🟡 media

«Condizione» (operatore), valore numerico e unità (Secondi); caselle «Tutto il mercato» e «Solo completamente abbinate».

*Usare per:* Distanziare le giocate nel tempo: «non piazzare se ho abbinato qualcosa negli ultimi N secondi». Complementare al campo «Attesa dopo esecuzione» della regola.

*Privacy:* pulito

### 50-20260706-192404.png

**Condizione «Risultato Impossibile [RIS]»** · rilevanza 🟡 media

Prima condizione del gruppo risultati sportivi. Corpo vuoto (lavora sulla selezione indicata) ma compare in fondo un riquadro nuovo, comune a tutte le [RIS]: «In assenza risultati disponibili» con le due alternative «La condizione non è verificata» / «Disattiva la regola».

*Usare per:* Riconoscere quando una selezione è ormai impossibile (es. un risultato esatto già superato). IMPORTANTE per l'assistente: il riquadro «In assenza risultati disponibili» è la scelta fail-safe di tutte le condizioni [RIS] — se il feed risultati non c'è, si decide se la condizione è falsa o se la regola si spegne.

*Privacy:* pulito

### 51-20260706-192425.png

**Condizione «Numero goals della partita [RIS]»** · rilevanza 🟡 media

«Condizione» (operatore) e «Valore di riferimento» (1); riquadro «Tempo» con Qualsiasi / Solo Primo Tempo / Solo Secondo Tempo; riquadro «In assenza risultati disponibili».

*Usare per:* Condizione live sui gol totali.

*Privacy:* pulito

### 52-20260706-192449.png

**Condizione «Risultato della partita [RIS]»** · rilevanza 🟡 media

«Risultato partita:» con due menu (casa - ospite) entrambi su «Qualsiasi»; riquadro «Riferito a» con Risultato attuale / Risultato fine primo tempo; riquadro «In assenza risultati disponibili».

*Usare per:* Condizionare al punteggio esatto.

*Privacy:* pulito

### 53-20260706-192509.png

**Condizione «Relazione tra goals delle squadre [RIS]»** · rilevanza ⚪ bassa

«Numero di goal della squadra di casa è» (operatore) rispetto a «Risultato della squadra ospite» con scostamento (più/meno) e valore in goal(s); riquadro «Riferito a» (Risultato attuale / fine primo tempo); riquadro «In assenza risultati disponibili».

*Usare per:* Esprimere vantaggi/svantaggi senza fissare il punteggio esatto (es. casa avanti di 2).

*Privacy:* pulito

### 54-20260706-192544.png

**Condizione «Eventi squadra [RIS]» — menu «Tipo di evento»** · rilevanza 🟡 media

«Squadra» (Casa/Ospite), «Tipo di evento» col menu aperto sulle statistiche disponibili: Cartellini Gialli · Cartellini Rossi · Calci d'angolo · Tiri in porta · Tiri fuori porta · Tiri totali · Possesso palla % · xG. Poi «Numero eventi» (0) e il riquadro «In assenza risultati disponibili».

*Usare per:* Condizioni su statistiche live, xG incluso.

*Privacy:* pulito

### 55-20260706-192604.png

**Condizione «Tempo partita e tempo di gioco [RIS]»** · rilevanza 🟡 media

Riquadro «Tempo Partita»: Primo Tempo · Intervallo · Secondo Tempo · Qualsiasi Tempo tra Primo o Secondo. Poi «Minuto Partita (da inizio tempo attuale)» con operatore e valore. Riquadro «In assenza risultati disponibili».

*Usare per:* Finestra temporale dentro la partita, es. giocare solo dopo il 60°.

*Privacy:* pulito

### 56-20260706-192624.png

**Condizione «Goal Segnato [RIS]»** · rilevanza 🟡 media

Riquadro «Tipo di evento»: Squadra di casa segna goal · Squadra ospite segna goal · Squadra qualsiasi segna goal. Poi «Secondi trascorsi da goal segnato» (0) e il riquadro «In assenza risultati disponibili».

*Usare per:* Reagire a un gol appena segnato entro una finestra di secondi.

*Privacy:* pulito

### 57-20260706-192653.png

**Condizione «Tennis Punteggio [RIS]» — vista d'insieme** · rilevanza ⚪ bassa

Due riquadri gemelli «Punteggio giocatore A (Pos. 1 Betfair)» e «Punteggio giocatore B (Pos. 2 Betfair)», ognuno con tre menu: Set vinti, Giochi vinti, Punti vinti (tutti su «Qualsiasi»). Riquadro «In assenza risultati disponibili».

*Usare per:* Condizioni sul punteggio tennis. Nota l'aggancio: A e B sono le POSIZIONI 1 e 2 del palinsesto Betfair, non i nomi dei giocatori.

*Privacy:* pulito

### 58-20260706-192712.png

**Tennis Punteggio — menu «Set vinti»** · rilevanza ⚪ bassa

Dettaglio della condizione Tennis Punteggio: il menu «Set vinti» del giocatore A aperto sui valori ammessi — Qualsiasi · 0 · 1 · 2 · 3 — cioè il massimo di un incontro al meglio dei cinque. Gli stessi valori valgono per il giocatore B.

*Usare per:* Valori ammessi per i set.

*Privacy:* pulito

### 59-20260706-192730.png

**Tennis Punteggio — menu «Giochi vinti»** · rilevanza ⚪ bassa

Dettaglio della condizione Tennis Punteggio: il menu «Giochi vinti» aperto su «Qualsiasi» più tutti i valori interi da 0 a 25, intervallo che copre anche i set lunghi senza tie-break.

*Usare per:* Valori ammessi per i giochi.

*Privacy:* pulito

### 60-20260706-192747.png

**Tennis Punteggio — menu «Punti vinti»** · rilevanza ⚪ bassa

Il menu «Punti vinti» aperto sui valori del tennis: Qualsiasi · 0 · 15 · 30 · 40 · Vantaggio.

*Usare per:* Valori ammessi per i punti, con la nomenclatura reale del tennis.

*Privacy:* pulito

### 61-20260706-192809.png

**Condizione «Tennis Servizio [RIS]»** · rilevanza ⚪ bassa

Due alternative: «Servizio giocatore A (Posizione 1 Betfair)» / «Servizio giocatore B (Posizione 2 Betfair)». Riquadro «In assenza risultati disponibili».

*Usare per:* Sapere chi è al servizio.

*Privacy:* pulito

### 62-20260706-192828.png

**Condizione «Tennis Punto Vinto [RIS]»** · rilevanza ⚪ bassa

Due alternative: «Giocatore A (Posizione 1 Betfair) Vince Punto» / «Giocatore B (Posizione 2 Betfair) Vince Punto». Riquadro «In assenza risultati disponibili».

*Usare per:* Reagire al singolo punto vinto.

*Privacy:* pulito

### 63-20260706-192850.png

**Condizione «Stato Corsa» — menu degli stati** · rilevanza ⚪ bassa

«Stato Corsa» col menu aperto sugli stati Betfair delle corse: DORMANT · DELAYED · PARADING · GOINGDOWN · GOINGBEHIND · APPROACHING · GOINGINTRAPS · HARERUNNING · ATTHEPOST · OFF · FINISHED · FINALRESULT · FALSESTART · PHOTOGRAPH · RESULT · WEIGHEDIN · RACEVOID · NORACE · MEETINGABANDONED · RERUN · ABANDONED.

*Usare per:* Condizioni su corse di cavalli e levrieri.

*Privacy:* pulito

### 64-20260706-192913.png

**Condizione «Cronometro Corsa» — menu del tipo di tempo** · rilevanza ⚪ bassa

Frase componibile: «Il [menu] della corsa è [operatore] [valore] Secondi», col menu aperto su: Tempo Trascorso · Tempo Rimanente · Tempo Trascorso % · Tempo Rimanente %.

*Usare per:* Finestra temporale dentro una corsa.

*Privacy:* pulito

### 65-20260706-192938.png

**Condizione «Numero esecuzioni regola»** · rilevanza 🟠 alta

«Regola» (menu delle regole della strategia), «Condizione» (operatore) e «Valore di riferimento» (1); riquadro «Stato delle condizioni durante l'esecuzione» con tre opzioni: Verificate o non verificate · Solo verificate (predefinito) · Solo non verificate.

*Usare per:* Una regola che guarda quante volte un'ALTRA regola è scattata. È il mattone per costruire sequenze e per un secondo livello di anti-doppione: «piazza solo se la regola di piazzamento non è ancora mai scattata».

*Privacy:* pulito

### 66-20260706-192959.png

**Condizione «Strategia Applicata»** · rilevanza 🟡 media

Campo «Nome strategia inizia per» (testo libero, confronto per prefisso) e casella «In esecuzione».

*Usare per:* Verificare se su quel mercato è già applicata un'altra strategia. Evita che due strategie si pestino i piedi sullo stesso mercato — utile se i segnali del bridge alimentano più strategie.

*Privacy:* pulito

### 67-20260706-193022.png

**Condizione «Formula»** · rilevanza 🟠 alta

Un campo di testo «Formula Condizione:» e, accanto, il pulsante «Guida Variabili».

*Usare per:* La via libera: qualsiasi espressione che le condizioni predefinite non coprono. Il pulsante «Guida Variabili» apre l'elenco delle variabili disponibili — è ESATTAMENTE il contenuto di condizioni/FORMULA.pdf, come annotato dal proprietario.

*Privacy:* pulito

### 68-20260706-193100.png

**Condizione «Valore Memorizzato»** · rilevanza 🟡 media

«Nome Valore Memorizzato» (testo). Riquadro «Valore Memorizzato di»: Mercato · Selezione · Strategia · Applicazione. Riquadro «Tipo»: Decimale · Intero · Testo · Quota · Data/Ora. Poi «Condizione» (operatore) e il valore di confronto.

*Usare per:* Rilegge le variabili scritte dall'azione «Imposta / Modifica Valore Memorizzato» (azioni-se-vero/21). I quattro livelli e i cinque tipi vanno fatti combaciare fra chi scrive e chi legge, altrimenti la variabile non viene trovata.

*Privacy:* pulito

### 69-20260706-193722.png

**Dialog «Nuovo Nodo Condizioni» (secondo scatto, finestra intera)** · rilevanza 🟡 media

Stessa dialog dell'immagine 15 ma catturata a finestra intera, con visibile il contesto: barra comandi con «Nuova Condizione» e «Nuovo Nodo Condizioni», albero della strategia e pannello descrittivo del nodo condizioni sullo sfondo.

*Usare per:* Mostrare DOVE stanno i comandi che aprono questa dialog.

*Privacy:* ATTENZIONE: «SIG_PREMATCH_BASE» nell'elenco a sinistra.
