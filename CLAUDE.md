# CLAUDE.md

## REGOLA PRINCIPALE

Questo repository è **XTrader Signal Relay** (servizio HTTPS su Railway), non Pickfair e non
XTrader Signal Bridge. È il **fratello server** del Bridge: stesso contratto CSV verso XTrader,
architettura completamente diversa.

Questo file è autosufficiente. In questo repository `AGENTS.md` **non esiste**: se un domani
verrà aggiunto, va letto e seguito prima di questo file, come nel Bridge.

Qui il rischio principale non è un motore trading complesso né un EXE Windows, ma un bridge
**Telegram → webhook → CSV → XTrader** esposto su Internet e **multiutente**: una modifica
sbagliata può generare un CSV errato, duplicare un segnale, lasciare un vecchio segnale attivo,
far processare chat Telegram non previste, esporre un feed senza token — oppure, ora che il
servizio diventa SaaS, **mostrare a un utente i segnali di un altro**.

Il merge resta sempre manuale del repository owner.

---

## COS'È QUESTO REPOSITORY — MAPPA REALE

| Cosa | Dove |
|---|---|
| Servizio FastAPI (relay, API, webhook Telegram, feed CSV) | `main.py` |
| Facciata pubblica del sito, servita sull'apex | `web/sito.html` (icone: `web/betrelay-favicon-sito.ico`, `web/betrelay-icona-256.png`) |
| Web app multiutente agganciata al backend (moduli ES, nessun build step) | `web/index.html`, `web/app.js`, `web/api.js` (fetch verso il relay), `web/styles.css` |
| Gemello a dati finti di `api.js`, usato SOLO dal file unico demo | `web/api_finta.js` (parità vincolata da `tests/web/test_api_parita.py`) |
| Motore di parsing configurabile — specifica eseguibile | `web/engine.js` |
| Generatore della copia a file unico del prototipo | `tools/build_single_file.py` |
| Architettura SaaS, modello dati, contratto API | `SAAS.md` |
| Documentazione operativa endpoint e variabili | `README.txt` |
| Screenshot reali di XTrader + catalogo (materia prima per le guide e per il chatbot) | `docs/xtrader/screenshot/` |
| Deploy | `Procfile`, `railway.json`, `requirements.txt` |
| Workflow di review AI — **vivi**: Fable 5, OpenRouter GPT-5.5, OpenRouter Sol; **dormienti**: GPT-5.5, GPT-5.6 Sol (credito OpenAI esaurito) | `.github/workflows/pr-review-*.yml` |
| Guardia sui workflow di review | `tests/safety/test_ai_audit_workflows.py` |
| Workflow che esegue i test (ogni PR, e i push a `main`) | `.github/workflows/test.yml` |
| Runtime esterni dei test (node, Chromium) e modalita' severa | `tests/runtime.py` |
| Guardie sulla CI e sul suo meccanismo | `tests/safety/test_ci.py`, `tests/safety/test_runtime_severo.py` |
| Test del relay: contratto CSV sui byte della risposta HTTP | `tests/relay/test_csv_contract.py` |
| Test del relay: autenticazione, webhook, parser | `tests/relay/test_autenticazione.py`, `tests/relay/test_webhook.py`, `tests/relay/test_parse_message.py` |
| Test del relay: libreria mercati Betfair (#33) | `tests/relay/test_mercati.py` |
| Test del relay: sorgenti squadre, modello e rotte (#34 pezzo 1) | `tests/relay/test_sorgenti_squadre.py` |
| Test del relay: multi-riga dal messaggio al feed (#35 pezzo 2) | `tests/relay/test_multiriga.py` |
| Test web: il conflitto della PUT dei parser (#51) | `tests/web/conflitto_flow.py`, `tests/web/test_conflitto_web.py` |
| Schema multiutente e migrazione idempotente | `migra()` in `main.py`, `tests/relay/test_schema.py` |
| Test del motore e del contratto CSV (casi eseguiti in node) | `tests/engine/engine_cases.mjs`, `tests/engine/test_engine_contract.py` |
| Test della web app in browser, end-to-end sul relay vero (Playwright/Chromium) | `tests/web/prototype_flow.py`, `tests/web/mobile_layout.py`, `tests/web/mercati_flow.py`, `tests/web/squadre_flow.py`, `tests/web/test_prototype_flow.py`, `tests/web/test_file_unico.py`, `tests/web/test_api_parita.py`, `tests/web/test_schermate_accesso.py`, `tests/web/test_pannello_admin.py`, `tests/web/test_mercati_web.py`, `tests/web/test_squadre_web.py`, `tests/web/multiriga_flow.py`, `tests/web/test_multiriga_web.py`, `tests/web/test_api_finta_squadre.py` (demo in node su localStorage rotto) |
| Test della facciata: HTTP e browser | `tests/relay/test_facciata.py`, `tests/web/sito_flow.py`, `tests/web/test_sito.py` |
| Ambiente dei sottoprocessi di test (whitelist) + sua guardia | `tests/ambiente.py`, `tests/safety/test_ambiente_dei_test.py` |
| Dipendenze dei soli test | `requirements-dev.txt` |

**File core del bridge** (per i gate di review e per la soglia di attenzione):
`main.py`, `web/**`, `tools/**`, `requirements.txt`, `Procfile`, `railway.json`.

`tools/` è core benché non sia servito: ci vive il generatore del file unico, cioè la copia del
prototipo che si condivide con i clienti, e con esso la conversione del JavaScript in ASCII puro.
Un difetto lì non solleva un errore: fa fallire in silenzio il confronto sul marcatore emoji e il
segnale non arriva mai a XTrader (vedi «REGOLA CODIFICA»). **`web/` invece è servita
pubblicamente** da `StaticFiles` su `/app`: qualunque file in quella cartella è scaricabile senza
token, per questo il generatore sta in `tools/` e scrive in `dist/`, e per questo esiste la guardia
`tests/safety/test_static_mount.py`.

**Non esistono in questo repository** (e nessun task deve fingere il contrario):
GUI Tkinter, build Windows/EXE/PyInstaller, `xtrader_bridge/`, `license_manager/`,
assistente di configurazione (`config_agent`), listener con polling/backoff/epoch
(qui Telegram arriva via **webhook**, non via polling).

---

## QUANDO USARE QUESTO FILE

Usa queste regole per qualsiasi task che:

- modifica `main.py`;
- modifica il motore di parsing (`web/engine.js` o la futura versione Python);
- modifica il formato CSV o il contratto verso XTrader;
- modifica scrittura, sostituzione o svuotamento del CSV, o il TTL di 90 secondi;
- modifica il webhook Telegram, il filtro delle chat o l'associazione chat → profilo/parser;
- modifica token, autenticazione, profili o isolamento fra utenti;
- modifica lo schema del database o la sua migrazione;
- modifica la web app in `web/`;
- modifica deploy Railway (`Procfile`, `railway.json`, `requirements.txt`);
- richiede commit, push o PR;
- corregge review comments, check rossi, Codacy, DeepSource, CodeRabbit, Codex, Sourcery,
  Gitar o GitHub Actions.

Per domande, spiegazioni o analisi read-only non serve aprire PR.

---

## REGOLE NON NEGOZIABILI

- Non lavorare mai direttamente su `main`.
- Non fare mai merge.
- Non abilitare auto-merge.
- Non creare una seconda PR se esiste già una PR aperta non correlata.
- Non allargare lo scope.
- Non fare refactor generale se il task chiede una correzione specifica.
- Non committare token Telegram, `CSV_ACCESS_TOKEN`, chat ID reali, `.env`, `*.db`,
  CSV generati, log, cache, `dist/` o artifact.
- Non stampare token Telegram né token di feed nei log, nelle risposte API o nei messaggi
  d'errore. Nei log dei messaggi non deve comparire alcun token.
- Non trasformare il relay in bot di puntata diretta, Betfair API client o browser automation,
  salvo task esplicito del proprietario.
- Non modificare stake, quota, mercato, selezione, bet type, `MarketId` o `SelectionId` senza
  task esplicito.
- **Non indebolire il filtro delle chat.** Un messaggio da una chat non associata a nessun
  profilo/parser va ignorato. L'unica eccezione prevista è un codice di verifica valido
  (vedi `SAAS.md`).
- **Non indebolire l'isolamento fra utenti.** `user_id` viene sempre dalla sessione, mai da un
  parametro della richiesta. Ogni accesso a un parser verifica la proprietà.
- Non lasciare vecchi segnali nel CSV: il TTL di 90 secondi e lo svuotamento a sola
  intestazione sono parte del contratto.
- Non generare righe CSV parziali o ambigue.
- Non rompere `GET /xtrader.csv?token=…`: è l'URL già configurato in XTrader dal profilo PIERO.
- Non dichiarare DONE finale mentre i check GitHub sono ancora pending/running.
- Non risolvere review thread mentre i check sono ancora in corso.
- Non dichiarare test passati se non sono stati realmente eseguiti.
- Non creare test finti, decorativi o che non esercitano il codice reale.
- Ogni task che modifica codice **DEVE** generare test hard veritieri nuovi o aggiornati che
  esercitino il comportamento reale del cambiamento — inclusi, quando pertinenti, gli scenari
  di resilienza (riavvio del container e perdita del DB, richieste concorrenti sullo stesso
  parser, scadenza del TTL, webhook duplicato o fuori ordine, scrittura fallita, token revocato,
  codifica non UTF-8): un cambiamento di codice senza test hard corrispondenti è un PR
  incompleto e **NON** può dichiarare DONE.
- Se una modifica tocca l'aspetto design/UI/UX della web app, **DEVI** aggiornare la
  documentazione UI nello stesso PR (vedi «GATE DOCUMENTAZIONE UI»), o dichiarare N/A con
  motivazione.
- Non dichiarare READY_TO_MERGE: il merge resta sempre manuale.
- Rispetta sempre le **CINQUE REGOLE ANTI-REGRESSIONE**.

---

## LE CINQUE REGOLE ANTI-REGRESSIONE — OBBLIGATORIE

**Perché esistono.** Nascono dagli audit del repository Bridge (#186–#193, 30/07/2026): su sei
correzioni della #16, tre avevano lasciato *siblings* non allineati, e da quelle omissioni sono
nati i bug B6, B10 e B17. **I numeri di PR citati appartengono al Bridge, non a questo
repository** — non cercarli qui. La lezione invece vale identica, e questo repository ne ha già
prodotto due esempi propri, riportati qui sotto.

Valgono per ogni PR che tocca codice, in aggiunta a tutto il resto di questo file.

### 1. Test fail-first, sempre

Prima il test che riproduce il bug, verificato **rosso** sul codice attuale; poi la patch. Un
test scritto dopo la patch dimostra solo che la patch fa quello che fa, non che il bug è chiuso.
Nel report va scritto l'esito del test **prima** della correzione, non solo dopo.

### 2. Cerca la classe, non il sito

Prima di chiudere una PR, grep del pattern corretto su **tutto** il repository.

*Esempio reale di questo repository:* il layout della web app sfondava in orizzontale perché un
figlio di flex/grid ha `min-width: auto` per default. Correggerlo su `.wizard-grid` non è
bastato: lo stesso difetto era su `.shell`, su `.side nav` e sulle tabelle larghe. Quattro siti,
una sola classe di bug. Chi si fermava al primo avrebbe chiuso la PR con il bug ancora vivo.

**E cerca i CONSUMATORI, non solo i siti.** Il grep sopra trova i posti che hanno lo stesso
difetto. Non trova i posti che si fidavano del comportamento che stai cambiando.

Quindi, ogni volta che cambi il **valore di ritorno**, **dove una funzione scrive**, o una
**promessa scritta nel docstring** (non solleva / è best-effort / è idempotente):

- grep di **chi chiama** quella funzione — non del pattern del bug;
- per ciascun chiamante, leggi cosa fa del risultato: lo converte? si fida che non sollevi? si
  aspetta il file o la riga in quella posizione?
- il test fail-first va scritto **sul chiamante**, non solo sulla funzione.

*Esempio reale di questo repository:* `suggestConfig()` confronta il marcatore `🆚` con il testo
del messaggio. La funzione era corretta e i suoi test passavano. Serviva a nulla: nella copia a
file unico il documento veniva decodificato come latin-1, l'emoji nel sorgente diventava
mojibake e il confronto falliva sempre, in silenzio. Il difetto era invisibile testando la
funzione da sola e appariva solo passando dal contesto reale che la ospitava. Da qui la regola
sulla codifica più sotto.

### 3. Fonte unica dove esiste

Il motore di parsing, l'elenco delle 14 colonne, la serializzazione CSV, le regole di
autenticazione: se la correzione va scritta in due posti, il posto giusto è **zero** — va
estratta in una fonte unica prima di correggere. Due copie corrette oggi sono due copie
divergenti domani.

Attenzione particolare in questo repository: il motore di parsing esiste in JavaScript
(`web/engine.js`) e presto esisterà in Python lato server. **Sono due implementazioni dello
stesso contratto**: ogni modifica al comportamento va fatta su entrambe nello stesso PR, con un
test che confronta gli output. Il file unico generato da `tools/build_single_file.py` non è una
terza copia: è derivato, e va rigenerato, mai modificato a mano.

### 4. Una PR aperta alla volta

PR che toccano lo stesso modulo in parallelo si conflittano, e il merge risolto a mano è dove
nascono i bug nuovi. Sequenziali, sempre.

**Va dimostrata, non dichiarata.** A differenza delle altre quattro non è ispezionabile dal
diff, ma è verificabile. Nel FINAL_HARD_VERIFY va riportato l'**elenco effettivo** delle PR
aperte al momento del controllo (via API GitHub, tool MCP GitHub, o `gh pr list --state open`),
non un PASS asserito. Elenco vuoto o contenente solo questa PR → PASS; qualsiasi altra PR aperta
→ si dichiara quale e perché non è in conflitto, oppure FAIL.

Se l'elenco non è ottenibile (niente rete, niente credenziali, `gh` assente — questo ambiente
tipicamente **non ha `gh`** e usa i tool MCP GitHub) si scrive **UNKNOWN** con il motivo, mai
PASS. UNKNOWN blocca il DONE esattamente come un FAIL.

### 5. Non toccare ciò che è dichiarato sano

In questo repository l'area da non toccare senza task esplicito è il **contratto CSV verso
XTrader**: 14 colonne, quell'ordine, tutti i campi tra virgolette, terminatore CRLF, **UTF-8 con
BOM**, svuotamento a sola intestazione. Ogni riga cambiata lì è rischio puro senza guadagno.

**Cosa vincola questa area, e cosa non la vincolava.** Fino all'11/08/2026 qui c'era scritto
«funziona con XTrader in produzione ed è già stato verificato byte per byte». Era **falso**: il feed
usciva senza BOM, XTrader lo pretende, e nessuno aveva mai guardato i byte. Una regola che vieta di
toccare una cosa, appoggiata a una prova che non esiste, è il meccanismo con cui quel difetto è
sopravvissuto per mesi.

Adesso il contratto è vincolato da **test eseguibili**, non da un'affermazione: `verify_csv()` in
`main.py`, `verifyCsv()` in `web/engine.js`, i casi in `tests/relay/test_csv_contract.py` che
asseriscono i **byte** della risposta HTTP, e il confronto fra le due implementazioni in
`tests/engine/test_engine_contract.py`. Se cambi il contratto, quei test diventano rossi — che è il
solo modo in cui «dichiarato sano» significa qualcosa.

Vale lo stesso per il filtro delle chat e per l'alias legacy `/xtrader.csv`. Se un task sembra
richiederlo, fermati e chiedi invece di procedere.

---

## REGOLA CODIFICA — SPECIFICA DI QUESTO REPOSITORY

I marcatori dei parser sono **emoji** (`🆚`, `⏰`, `✅`): sono dati portanti, non decorazione.
Un confronto su emoji che fallisce non solleva un errore, restituisce semplicemente "non
riconosciuto", e il segnale non arriva mai a XTrader senza che nessuno se ne accorga.

Regole:

- I file sorgente sono UTF-8. `web/index.html` dichiara `<meta charset="utf-8">`; i moduli ES
  sono UTF-8 per specifica, quindi `web/*.js` è al sicuro.
- Un file unico generato **non** è al sicuro: eredita la codifica del documento. Per questo
  `tools/build_single_file.py` emette il JavaScript in ASCII puro con escape `\uXXXX`. Non
  rimuovere quella conversione, e non aggiungere al bundle testo non ASCII fuori dal `<script>`
  senza passare da entità HTML.
- Il CSV va scritto **UTF-8 con BOM**, e questo vale sia in Python (`main.py`) sia in JavaScript
  (`web/engine.js`): sono due implementazioni dello stesso contratto e devono coincidere byte per
  byte. Il BOM va scritto con l'escape `\ufeff`, in Python come in JavaScript, **mai**
  come carattere letterale nel sorgente:
  un U+FEFF letterale è invisibile in un editor ed è esattamente il tipo di carattere che questa
  sezione dice di non lasciare in giro.
  *Storia, perché non si ripeta:* qui c'era scritto il contrario — «senza BOM, un BOM davanti a
  `"Provider"` rompe la prima colonna» — e il feed usciva senza BOM. Il proprietario ha provato il
  contrario aprendo `x1.csv`, il file che il Bridge scrive e XTrader legge: la barra di stato di
  Blocco note dice «UTF-8 con BOM». Nessuna delle due affermazioni era stata misurata; una era falsa
  e ha tenuto in piedi il difetto.
- Ogni test che riguarda un parser con marcatore emoji deve confrontare i **codepoint**, non
  fidarsi dell'aspetto visivo: `🆚` e una sequenza con variation selector si vedono uguali e non
  sono uguali.

---

## ORDINE OPERATIVO OBBLIGATORIO

Per ogni task che modifica codice o PR, segui sempre questo ordine:

1. clean branch preflight
2. Phase 0 read-only
3. patch plan
4. patch stretta
5. post-fix micro-audit
6. test hard veritieri locali
7. commit/push
8. aspetta fine di tutti i check GitHub
9. leggi check result + annotations
10. leggi PR comments
11. leggi review bodies
12. leggi inline comments
13. leggi unresolved threads
14. triage finding
15. eventuale nuova patch
16. nuova Phase 0 se serve
17. nuovo micro-audit
18. nuovi test hard veritieri
19. nuovo push
20. aspetta di nuovo fine check
21. final hard verify
22. report finale

Non puoi saltare: Phase 0 · micro-audit · test hard veritieri · check completion gate ·
review/inline/thread triage · final hard verify.

---

## FINAL AI REVIEW BEFORE MERGE — CANCELLO LABEL

> **Stato in questo repository.** I workflow di review sono stati importati dal Bridge e vivono in
> `.github/workflows/`: **GPT-5.5** (`pr-review-gpt55.yml`), **Claude Fable 5**
> (`pr-review-claude-fable5.yml`) e **GPT-5.6 Sol** (`pr-review-gpt56-sol.yml`, che dal 12/08/2026
> ha sostituito OpenRouter Fugu Ultra). **GLM 5.2 non è stato importato**, quindi qui i reviewer
> a API key sono tre, non quattro.
>
> Perché funzionino servono due cose, **azione del proprietario una volta sola**:
>
> **AGGIORNAMENTO 02/09/2026 — il credito OpenAI si è esaurito, e due workflow sono dormienti.**
> Misurato sulle PR #104, #105 e #106: `credit_balance_exhausted` (HTTP 429) su ogni chiamata di
> GPT-5.5 e GPT-5.6 Sol, che leggono `BETRELAY_GPT`. Decisione del proprietario: quei due file
> restano **dormienti** (solo `workflow_dispatch`, riattivabili togliendo un commento) e il ruolo
> del reviewer forte passa a **`pr-review-openrouter-sol.yml`** — stesso modello `gpt-5.6-sol`,
> fornitore **OpenRouter**, endpoint `chat/completions`, e il Secret **`BETRELAY_FUGU`** che già
> esisteva per l'ex reviewer Fugu. Nessuna chiave nuova da creare.
> **La copertura dei push è ricostituita**, e va detto perché non è scontato: addormentare GPT-5.5
> aveva aperto un buco — Fable spende solo sui file core, il reviewer forte solo al gate, quindi un
> push su **soli test o documentazione** non veniva letto da nessuno fino al merge, e in questo
> repository i test sono metà del lavoro. Il proprietario ha scelto di chiuderlo con un **quarto
> workflow**, `pr-review-openrouter-gpt55.yml`: stesso ruolo del GPT-5.5 dormiente, su OpenRouter,
> stesso Secret `BETRELAY_FUGU`. È il più caro per giro perché è l'unico che spende su **ogni**
> push senza gate a file core — scelta consapevole, non un effetto collaterale.
>
> 1. i Secret del repo — **`BETRELAY_FUGU`** (l'unico dei tre letto da un workflow vivo: porta la
>    chiave OpenRouter) e **`BETRELAY_FABLE`**. **`BETRELAY_GPT`** resta configurato ma lo leggono
>    solo i due dormienti: non va eliminato, serve il giorno in cui quel credito torna. Sono i nomi scelti dal
>    proprietario per questo repository: **non** `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` /
>    `OPENROUTER_API_KEY`, che sono i nomi del Bridge. Un workflow che leggesse i nomi del Bridge
>    troverebbe una stringa vuota e uscirebbe **verde senza chiamare il modello** — nessun errore,
>    nessun check rosso, una PR con tre spunte e zero righe lette. Per questo
>    `tests/safety/test_ai_audit_workflows.py` vincola i nomi nuovi **e** vieta i vecchi come
>    residui. L'agente non vede mai le chiavi.
> 2. la **creazione** delle label `final-fable-review` e `final-fugu-review`. Senza, aggiungerle via
>    API dà 404 e il gate finale non si arma.
>
> **Stato al 12/08/2026.** Entrambe le azioni sono state fatte dal proprietario (l'11/08). Cosa è **misurato** e
> cosa è **riferito**, perché la differenza è il motivo per cui questo file esiste:
>
> | Cosa | Come lo so |
> |---|---|
> | Label `final-fable-review`, `final-fugu-review` | **misurato**: `GET /labels/{name}` risponde 200, non più 404 |
> | Secret `BETRELAY_GPT` | **misurato**: il job GPT-5.5 su `7e86517` ha chiamato il modello e riportato 4572 token di uso |
> | Secret `BETRELAY_FABLE` | **misurato dal 12/08/2026**: sulle PR #16, #17, #18 e #19 il job Fable ha riportato `Fonte token: Anthropic usage` con i conteggi e il costo — una review vera, non un `::notice`. Era «riferito dal proprietario» fino a quel giorno |
> | Secret `BETRELAY_FUGU` | **riusato dal 02/09/2026**: porta la chiave OpenRouter e lo leggono i **due** workflow su quel fornitore (`pr-review-openrouter-gpt55.yml` e `pr-review-openrouter-sol.yml`). Dal 12/08 al 02/09 non lo leggeva nessuno e la guardia lo vietava come residuo; ora la guardia è girata — lo **richiede** su quei due e continua a **vietarlo** sugli altri tre, dove sarebbe di nuovo una lettura a vuoto. **MISURATO il 02/09/2026** sulla PR #107: il job `OpenRouter GPT-5.5` ha chiamato il modello e riportato 12.410 token (`openai/gpt-5.5`, ~$0,0767). Quindi la chiave ha credito e l'id del modello esiste: non è più «riferito dal proprietario», è nel log |
>
> Regola che ne segue, e vale per chiunque legga: **un check verde non è prova di review.** Va letto il
> log e cercata la riga d'uso token. Se compare un `::notice` «non configurato», quel reviewer non ha
> letto niente. E non scrivere qui che un Secret esiste prima di aver visto il log che lo usa: è lo
> stesso errore per cui il contratto CSV è rimasto per mesi «verificato byte per byte» senza esserlo.
>
> **Non confondere queste due con il riarmo, che invece è ricorrente e spetta all'agente.** I Secret
> si configurano una volta e valgono per sempre; le label si creano una volta e restano nel repo. Ma
> *applicarle* va rifatto a ogni head stabile: rimuovere e riaggiungere `final-fable-review` e
> `final-fugu-review` dopo ogni push (vedi «Un push dopo l'armamento rende il gate STANTIO»). Il
> riarmo riguarda **solo** le due review finali Fable/Sol — GPT-5.5 gira comunque a ogni push, e i
> reviewer GitHub App non dipendono dalle label.
>
> Finché Secret o label mancano, l'agente lo dichiara invece di sostenere che le review finali sono
> state fatte. Il set di file core è vincolato da `tests/safety/test_ai_audit_workflows.py`.

Due reviewer AI forti e costosi (Claude Fable 5, GPT-5.6 Sol) non girano a ogni push come
GPT-5.5. Partono:

- automaticamente su un push che tocca **file core** di questo repository
  (`main.py`, `web/**`, `requirements.txt`, `Procfile`, `railway.json`) — analizza il
  push-range. Solo Claude Fable 5: GPT-5.6 Sol su un push esce senza spendere;
- oppure quando l'agente aggiunge la label finale (gate pre-merge sull'intera PR):
  - `final-fable-review` → PR Review Claude Fable 5
  - `final-fugu-review` → **PR Review OpenRouter Sol** (dal 02/09/2026; prima era `PR Review GPT-5.6 Sol`, ora dormiente) *(il nome della label non cambia col modello né col fornitore: è
    il nome del GATE, e rinominarla richiede che il proprietario crei quella nuova)*

Su push che toccano solo workflow/CI, docs o test, i due job partono ma escono senza chiamare il
modello (costo zero); quei cambiamenti restano coperti da GPT-5.5, che gira su ogni push.
L'agente non vede mai le API key: aggiunge solo la label.

**Il gate a label si arma solo con la SUA label.** Entrambi i workflow ricevono la label
dell'evento oltre all'elenco di quelle presenti, e su un evento `labeled` si armano solo se la
label appena aggiunta è quella finale — non basta che la finale sia da qualche parte
nell'elenco. Conseguenza pratica: aggiungere `manual-review-required` a una PR che ha già le
label finali **non** rilancia i due reviewer forti. Per rieseguirli su un head nuovo va rimossa
e riaggiunta la label finale.

**Un push dopo l'armamento rende il gate STANTIO.** Se la label finale è sulla PR e arriva un
nuovo commit, la review precedente si riferisce a un altro head: i due job escono rossi senza
chiamare il modello, col messaggio che dice di rimuovere e riaggiungere la label. Quel rosso non
è un guasto, è il gate che funziona: si risolve riarmando la label a head stabile, e il riarmo
produce una review dell'intera PR, cioè più copertura di quella del solo push-range.

La label resta il **gate finale pre-merge obbligatorio**: anche se una PR non ha toccato file
core, prima di dichiararla pronta l'agente deve far partire le review finali via label.

Fai partire le review finali solo dopo che: tutto il lavoro richiesto è completo; i check/test
locali sono stati tentati dove possibile; il branch è stato pushato; la PR non è draft. Le due
label vanno rimosse e riaggiunte, **una alla volta**:

```bash
PR_NUMBER="$(gh pr view --json number -q .number)"

gh pr edit "$PR_NUMBER" --remove-label final-fable-review || true
gh pr edit "$PR_NUMBER" --remove-label final-fugu-review || true

sleep 2

# UNA ALLA VOLTA, non in una sola chiamata.
gh pr edit "$PR_NUMBER" --add-label final-fable-review
gh pr edit "$PR_NUMBER" --add-label final-fugu-review
```

In questo ambiente `gh` **non è disponibile**: l'equivalente è rimuovere e riaggiungere le due
label via tool MCP GitHub / API (`issues/{n}/labels`), sempre una alla volta. Le due label
devono già esistere nel repo; crearle è un'azione una-tantum del proprietario.

Perché una alla volta: aggiungerne più d'una in una sola chiamata emette un evento `labeled` per
ciascuna, i job gatano su `github.event.label.name`, l'evento della label che non è la propria
viene rifiutato dalla condizione e — col gruppo di concorrenza della PR — i job buoni finiscono
`skipped`. Il sintomo è «ho messo le label e i reviewer non partono», e non è deducibile dai log.

Poi aspetta i workflow *PR Review Claude Fable 5* e *PR Review OpenRouter Sol* (rientrano
nel CHECK COMPLETION GATE). Se una delle due review segnala bloccanti, security issue, rischi
contratto CSV, rischi di isolamento fra utenti, rischi workflow, rischi gestione segreti o
`manual-review-required`, non dichiarare la PR pronta e non proporre merge automatico: lascia la
PR aperta e scrivi chiaramente:

```
AUTO-MERGE DISABILITATO: questa PR richiede merge manuale da parte di Piero.
```

Il merge resta sempre manuale del proprietario.

---

## CHECK COMPLETION GATE — OBBLIGATORIO

Prima del controllo finale della PR devi aspettare che **tutti** i check siano finiti.

Non puoi fare final review, evidence resolve, resolve thread, READY o DONE finale mentre ci sono
check ancora in corso.

Devi controllare il current-head della PR e leggere: GitHub Actions · `statusCheckRollup` ·
commit statuses · **Codacy · DeepSource · CodeRabbit · Codex** (installati sull'account, quindi
attivi anche qui) · Sourcery/Gitar se presenti · workflow di build/test.

Sono considerati **NON finiti** gli stati: `PENDING`, `QUEUED`, `IN_PROGRESS`, `WAITING`,
`REQUESTED`, `EXPECTED`, `UNKNOWN`, `null`, vuoto.

Se anche un solo check è ancora in corso, fermati e rispondi:

```
CHECKS_PENDING

Reason:
- I check della PR non sono ancora tutti finiti.

Pending checks:
- <nome check>
```

Quando i check sono pending: non dichiarare DONE/READY/READY_TO_MERGE, non risolvere thread, non
dire che i commenti sono coperti, non fare merge, non aprire un'altra PR, non fare patch casuali
solo perché stai aspettando.

**Attenzione a cosa manca e cosa no.** Codacy, DeepSource, CodeRabbit e Codex sono GitHub App
installate sull'account e compaiono su ogni PR: i loro check e commenti vanno attesi e letti come
su qualunque altra PR. I workflow di review a API key in `.github/workflows/` sono **cinque file,
tre vivi**: OpenRouter GPT-5.5, Claude Fable 5, OpenRouter Sol. Gli altri due
(`pr-review-gpt55.yml`, `pr-review-gpt56-sol.yml`) sono **dormienti dal 02/09/2026** e non
producono più alcun check: non aspettarli. **GLM 5.2 non è importato.**

**Quando un check verde non prova una review, e per quale dei tre.** Le due cose vanno tenute
distinte, perché confonderle fa scartare una review vera:

- **Secret mancante:** il workflow che legge quel Secret trova una stringa vuota ed esce verde
  con un `::notice` nei log, non un errore. Vale per tutti e tre, ciascuno per il proprio Secret.
- **Label finali:** riguardano **solo** i due workflow col gate finale (Fable e OpenRouter Sol).
  **OpenRouter GPT-5.5 gira su ogni evento `pull_request` e non dipende da quelle label**: un suo
  check verde, con la riga d'uso token nel log, è una review vera anche senza label.

Segnalato da CodeRabbit sulla PR #107: la versione precedente di questa riga diceva che senza le
label «i tre vivi escono verdi senza chiamare il modello», e un agente che la leggesse butterebbe
via l'unica review che ha.

**Zero check non è comunque un PASS.** Se una PR non mostra alcun check — app non ancora attive
su questo repository, outage, PR draft — si scrive esplicitamente «nessun check è girato: la
verifica è solo locale», e i test locali eseguiti diventano l'unica evidenza, con il limite
dichiarato. Non scrivere mai «GitHub checks: PASS» quando non è girato nulla.

Dopo ogni push ripeti il ciclo:
push → aspetta fine check → leggi risultati → leggi review/commenti/inline → triage → eventuale
patch → micro-audit → test → push → aspetta di nuovo.

Il controllo review/commenti/inline va fatto **dopo** che i check sono finiti, perché alcuni bot
pubblicano commenti o annotation solo a check completato.

---

## REVIEWER DISPONIBILI — chi aspettare davvero

> **Stato in questo repository.** Distingui due famiglie, perché si installano in posti diversi:
>
> - **GitHub App, installate sull'account e quindi attive anche qui**, benché
>   `.github/workflows/` non esista: **CodeRabbit · Codacy · DeepSource · Codex** (e Sourcery,
>   quando non è rate-limited). Aprendo una PR compaiono. I loro check vanno attesi e i loro
>   commenti letti, esattamente come nel Bridge.
> - **Workflow a API key, presenti in `.github/workflows/`:** sono **cinque file**, ma solo
>   **tre vivi**. Vivi: **OpenRouter GPT-5.5** (`pr-review-openrouter-gpt55.yml`),
>   **Claude Fable 5** (`pr-review-claude-fable5.yml`), **OpenRouter Sol**
>   (`pr-review-openrouter-sol.yml`). **Dormienti dal 02/09/2026**, credito OpenAI esaurito:
>   `pr-review-gpt55.yml` e `pr-review-gpt56-sol.yml` — hanno solo `workflow_dispatch`, quindi
>   **non compaiono più come check sulle PR**. Non aspettarli e non contarli.
>   **GLM 5.2 non è stato importato:** questa differenza rispetto al Bridge va detta, non taciuta.

I reviewer che coprono davvero una PR sono quindi i tre workflow **vivi** — OpenRouter GPT-5.5,
Claude Fable 5, OpenRouter Sol — più CodeRabbit. OpenRouter GPT-5.5 gira a ogni push, qualunque
file; Fable parte da solo sui push che toccano file core; OpenRouter Sol solo con la label
finale; entrambi partono con le label finali; CodeRabbit rivede l'intera PR dal suo base.

**La branch protection: nulla da fare, verificato dal proprietario.** CodeRabbit e GPT-5.5
avevano avvisato, sulla PR #107, che se `main` avesse richiesto ancora i check
`GPT-5.5 push-range review` o `GPT-5.6 Sol final review` — nomi di workflow ora dormienti, che
non producono più quei check — il merge sarebbe rimasto appeso in attesa di qualcosa che non
arriva. **Il proprietario ha controllato *Settings → Branches* dopo il merge della #107: quei due
check non figurano fra i required.** L'avviso era quindi teorico e qui non si applica: non c'è
niente da togliere. Lasciato scritto proprio perché non riemerga come falso todo — se un domani
si configureranno dei required check, chi li imposta sa già di non riferirsi ai due workflow
dormienti (`GPT-5.5 push-range review`, `GPT-5.6 Sol final review`), che non producono più nulla.

**Codex NON è un gate** — non aspettarlo. È installato e comparirà sulle PR, ma l'abbonamento
Codex del proprietario è scaduto: quando pubblica «You have reached your Codex usage limits» o
simili, trattalo come **assente**, non come pending. Non contarlo nel check-completion gate, non
bloccare il DONE su di lui, annota solo che non ha revisionato. Se invece pubblica una review
reale, leggila e fai il triage come per qualunque altro reviewer.

**Sourcery NON è un gate** per lo stesso motivo: ha un rate limit settimanale (500k caratteri di
diff) e quando lo dichiara va trattato come assente.

**Ogni push costa API.** Accorpa i fix di review in un solo push per giro invece di uno per
finding; non pushare mai per cleanup puramente cosmetici o per rincorrere falsi positivi da
diff-per-push (un reviewer che ha visto solo l'ultimo commit e crede «mancante» un'implementazione
che sta in un commit precedente della stessa PR) — a quelli rispondi nel thread con l'evidenza,
mai con un commit.

**Prima di giudicare un bloccante, leggi «File non inviati al modello».** Ogni review stampa in
fondo l'elenco dei file che il workflow **non** gli ha mandato. Se il file citato dal bloccante è in
quell'elenco, quella review **non poteva verificarlo** — e questo è tutto ciò che l'omissione
dimostra. Non dimostra che il difetto non esista: segnalato da CodeRabbit, e la distinzione è
esattamente quella fra un dubbio e una prova.

Quindi non si patcha e non si archivia sulla fiducia: si **verifica**, con i mezzi che ci sono —
ispezione diretta del file, i test che lo vincolano, i suoi chiamanti. Se la verifica conferma il
difetto è reale e va corretto, indipendentemente da chi l'ha visto. Se la verifica lo smentisce, si
risponde nel thread con quell'evidenza (comando eseguito, righe lette, esito), non con un commit. Solo
quando la verifica è **impossibile** si dichiara il limite e si lascia decidere al proprietario.

Sulla PR #8 sono stati tre bloccanti su tre, tutti su `web/engine.js`, e tutti smentiti — ma dal test
che confronta le due implementazioni byte per byte, non dal fatto che il file fosse nell'elenco.

**La seconda causa di bloccanti falsi è la nostra redazione**, e va conosciuta prima di patchare.
I tre workflow redigono il diff prima di mandarlo al modello, e una regola troppo larga produceva
codice *rotto*: `name: pytest (${{ matrix.python }})` arrivava come
`name: pytest ([REDACTED_VALORE_INCOLLATO]`, con la parentesi di apertura spaiata. Il modello legge
un file corrotto e conclude — correttamente, dato quello che vede — che qualcuno ha incollato un
segreto. Sulla PR #14 ha prodotto un `SyntaxError` inventato; sulla #18 **tre bloccanti su tre**, e
~$0,63 di Fugu spesi per revisionare la redazione del proprio input. Corretto il 12/08/2026
restringendo il trigger alle sole **chiusure**, con la coppia di test in
`tests/safety/test_ai_audit_workflows.py` che tiene i due lati: le chiusure sopravvivono, e ogni
coda che potrebbe essere materiale segreto resta redatta.

Da quella correzione viene una regola generale che vale oltre la redazione: **un baratto documentato
può essere presentato come binario e non esserlo.** Qui il test precedente affermava che escludere
`)`, `,` e `.` avrebbe riaperto una falla, e per questo non si toccava. Misurato regola per regola,
`)` e `.` non avevano lo stesso costo: la falla su `.` la chiudeva un'**altra** regola, e su `,`
non c'era protezione da perdere perché la classe di consumazione la escludeva già. Prima di
accettare un «non si corregge, ecco perché», si misura ogni ramo del perché.

Quell'elenco è ora molto più corto, ma non è vuoto per definizione. I tre workflow ordinano il
payload per **priorità** prima di consumare il budget — `PRIORITA_PAYLOAD`: prima il codice
(`main.py`, `web/`, `tools/`, deploy, workflow), poi i test, poi la documentazione. Prima non
ordinavano niente e consumavano il budget nell'ordine dell'API GitHub, cioè **alfabetico**: `web/` è
ultimo, quindi il motore di parsing era strutturalmente garantito di essere il primo file scartato,
mentre `CLAUDE.md` entrava per intero. La lista vive in tre copie identiche perché i workflow non
fanno checkout e non possono importare un modulo comune; `tests/safety/test_ai_audit_workflows.py` ne
verifica la parità ed **esegue** il costruttore del payload su una lista di file finti, invece di
controllarne la forma.

### Due stati diversi, e il peggiore non era «non inviato»

«Non inviato» non è l'unico modo di non vedere un file. Un file può arrivare al modello **tagliato a
metà** dal tetto per-file: lui riceve codice vero, incompleto, e conclude su ciò che manca senza
accorgersene. È il caso peggiore dei due, perché l'assenza totale almeno si nota.

*Misurato sulla PR #14:* la patch di `main.py` era **25.037 caratteri** contro un tetto per-file di
15.000 — il **60%** inviato, e il 40% invisibile era il corpo dell'handler del webhook. Su **nove**
bloccanti dei gate finali, **quattro erano falsi**, tutti su quel file, tutti nella forma «non
verificabile dal diff troncato». Il modello dichiarava il proprio limite come se fosse un difetto del
codice, e ogni giro così costa ~$1 più una correzione che non serve.

Da qui tre cambiamenti, tutti vincolati da guardie che **eseguono** il codice dei workflow:

1. **Tetto per-file a 30.000**, totale invariato a 60.000. Il collo di bottiglia era il per-file, non
   il totale: `main.py` entra primo per `PRIORITA_PAYLOAD`, quindi prende i suoi 25k e ne restano 35k
   per il resto dentro lo stesso totale. **Costo per giro identico, file critico completo.** Alzare il
   totale invece avrebbe alzato la bolletta.
2. Il prompt **dice al modello** cosa non ha visto — file incompleti e file non inviati, elencati
   separatamente — con la regola scritta: *l'assenza di codice dall'input non autorizza nessuna
   conclusione sulla sua esistenza o correttezza*.
3. Ogni bloccante va **etichettato**: `[REAL_FINDING]` se verificato sul codice ricevuto,
   `[INSUFFICIENT_CONTEXT]` se il file che servirebbe è fra quelli mancanti. Un
   `[INSUFFICIENT_CONTEXT]` **non è un difetto**: è una richiesta di verifica umana, e va triato come
   tale — si verifica col codice in mano, non si patcha e non si archivia.

Quello che **non** cambia: un `[INSUFFICIENT_CONTEXT]` non autorizza ad archiviare. Vale la regola qui
sopra — si verifica coi mezzi che ci sono. L'etichetta dice *da dove viene il dubbio*, non che il
dubbio sia infondato.

### E c'è un terzo stato: la review troncata in USCITA

I due stati sopra riguardano l'**input** — cosa il modello non ha visto. Ne esiste un terzo che
riguarda l'**output**: il modello si interrompe a metà della propria review. Quando accade, il
commento sulla PR comincia con il banner `Output troncato: …`, che riporta il motivo dichiarato
dall'API (`max_output_tokens`, `content_filter`, o «non dichiarato»).

Come si legge, ed è la parte che conta: **una review con quel banner non è una review completa.**
Nessuna delle sue omissioni prova niente — non ha finito di guardare. Il banner impedisce anche al
workflow di pubblicare il marcatore di completamento, così il giro successivo la rifà invece di
crederla fatta.

*Perché è scritto qui:* fino al 12/08/2026 i due workflow su `v1/responses` (GPT-5.5 e GPT-5.6 Sol)
riconoscevano il troncamento **solo** se il motivo era `max_output_tokens`. Qualunque altro motivo —
`content_filter` fra questi — lasciava passare una review parziale come completa, marcata come fatta
e mai più rifatta: il gate finale avrebbe detto «nessun bloccante» su una review interrotta a metà.
Segnalato da CodeRabbit sulla PR #20 e corretto in entrambi.

**E il sito segnalato non era l'unico.** Fable 5 sta su Anthropic, dove il campo si chiama
`stop_reason` e i valori sono altri, ma la condizione aveva la stessa forma sbagliata: nominava il
solo motivo atteso (`== "max_tokens"`), quindi un `refusal`, un `pause_turn` o una risposta senza
`stop_reason` producevano «review completa». È regola 2 applicata: il difetto è stato trovato in un
posto e cercato in tutti.

**Poi la correzione stessa è stata corretta, e questo pezzo è il motivo per cui vale la pena
leggerlo.** Qui c'era scritto che tutti e tre i workflow «dichiarano completa **solo** l'uscita che
significa "ho finito"», e fra parentesi: «`end_turn` su Anthropic, `status` diverso da `incomplete`
su OpenAI». La parentesi contraddice la frase che la contiene — «diverso da un valore» è una
*blacklist*, non una whitelist — e descriveva fedelmente il codice: su Fable avevo scritto una
whitelist vera, nei due workflow OpenAI era rimasta la forma debole. Una risposta `failed` o
`cancelled` **con del testo dentro** veniva quindi pubblicata come review completa e timbrata col
`done_marker`. L'ha trovato **`gpt-5.6-sol` alla sua primissima chiamata reale**, sul workflow che
quella chiamata serviva a collaudare.

Adesso è una whitelist in tutti e tre, davvero: completa **solo** `end_turn` su Anthropic e **solo**
`status == "completed"` su OpenAI. Ogni altro esito — inclusi quelli che non conosciamo e un campo
assente perché la forma è cambiata — porta il banner. I test eseguono `call_model` di ciascun
workflow su una risposta finta per ciascuno stato, e includono l'uscita normale: senza quel caso, un
`truncated` sempre vero passerebbe tutti gli altri e la review si ripagherebbe a ogni push.

**Lezione generale, perché non è un aneddoto:** una documentazione può contenere, nella stessa frase,
l'affermazione e la sua smentita. Qui la parentesi che spiegava la regola era il difetto. Quando
scrivi «solo X», controlla che il codice dica *solo X* e non *diverso da Y*.

### Il gate finale non è «l'evento si chiama labeled»

Secondo `[REAL_FINDING]` di `gpt-5.6-sol` alla stessa prima chiamata, e con la conseguenza più grave
delle due. I tre guard che tengono il gate **fail-closed** — Secret assente, errore del fornitore,
commento non pubblicato — chiedevano `EVENT_ACTION == "labeled"`. Ma **GitHub non emette `labeled`
per una PR aperta con la label già applicata**: lo dice la docstring di `decisione_gate` e lo
dimostrava un test verde da prima di quella PR. Su `opened`, `reopened` e `ready_for_review` con la
label finale presente, quindi, il job faceva il gate finale mentre i suoi guard credevano di essere
su un push opzionale: chiave mancante o errore API uscivano **verdi**, e il gate risultava superato
con **zero righe lette**.

È la classe di difetto per cui esiste la regola «un check verde non è prova di review» — arrivata
dentro il meccanismo che quella regola doveva imporre. I siti erano **sei**, in entrambi i workflow
con gate, non i tre segnalati.

La correzione è una nozione sola, `GATE_FINALE = esito_gate == "revisiona"`, derivata dalla funzione
che la decisione la prende già, invece di riderivarla dal nome dell'evento in ogni guard. Il guard in
bash gira prima di Python e non può chiamarla: la sua condizione è volutamente più **grossolana** —
«la label finale è nel quadro?» — e in ogni caso in cui le due divergono sbaglia verso il rosso, che
per una chiave mancante è il verso giusto.

**Cosa ne segue per chi legge un gate verde:** un check verde di Fable o Sol vale come review solo se
nel log c'è la riga d'uso token. Vale già per i Secret mancanti (`::notice`), e fino al 12/08/2026
valeva anche per i tre percorsi qui sopra.

**Quanto costano davvero.** Misurato sulla PR #8, sette head, 15 review addebitate: **$2,6247** in
totale. La distribuzione conta più del totale, perché decide dove risparmiare:

| Reviewer | Review | Costo | Note |
|---|---:|---:|---|
| Claude Fable 5 | 7 | $1,6477 | ~$0,36 per review finale sull'intera PR |
| OpenRouter Fugu Ultra | 1 | $0,7013 | **17,8× la media di GPT-5.5 per review**, e quella era troncata — **sostituito da `gpt-5.6-sol` il 12/08/2026**, vedi sotto |
| GPT-5.5 | 7 | $0,2757 | media $0,0394 per giro |
| **totale** | **15** | **$2,6247** | |

**Fugu Ultra è stato sostituito da `gpt-5.6-sol` il 12/08/2026** (punto 4 della Issue #10). Le
righe qui sopra restano perché sono misure di quello che è successo, non descrizioni di com'è
configurato il servizio adesso. Tre ragioni, tutte misurate su questo repository: il costo
($0,7013 per una review, troncata); i bloccanti su codice non visto — un `SyntaxError` inventato
sulla PR #14 e tre bloccanti falsi sulla #18, tutti dalla redazione del nostro payload; e una API
key in meno, perché Sol sta sulla stessa API di GPT-5.5 e legge `BETRELAY_GPT`. Il baratto
dichiarato: al gate finale restano due famiglie di modelli invece di tre, e Fable 5 è l'unica voce
non-OpenAI.

Ne seguono due regole pratiche. **Il gate a label è la voce grossa** — un armamento completo costa
~$1,06 (Fable finale + il gate forte) — quindi si arma **una volta sola**, quando il lavoro è davvero finito:
ogni push successivo lo rende stantio e il riarmo ripaga tutto. E **il gate forte si tiene per l'ultimo head
stabile**, non per i giri intermedi.

**CI minutes.** Ogni push e ogni re-run consumano minuti GitHub Actions. Questo repository non ha
runner Windows (nessuna build EXE qui), quindi il costo è inferiore a quello del Bridge, ma vale
lo stesso: accorpa i fix in un solo push; niente commit vuoti o re-trigger a raffica; niente churn
di label subito dopo un push (aspetta che i check si stabilizzino — questo non vieta il firing
deliberato del gate finale a head stabile); preferisci aspettare un check in corso piuttosto che
ri-triggerarlo. Se tutti i check falliscono in ~2 s senza log e `runner_id: 0`, è lo spending
limit: azione del proprietario su billing, non ripushare.

---

## FINESTRA DI REVIEW POST-COMMIT

L'attesa a timer fisso (i vecchi 16 minuti) è **abrogata** per decisione del proprietario. Lo
stato `REVIEW_WINDOW_PENDING` **non si usa più** e non si programma alcun self check-in di
attesa. Al suo posto vale un'attesa **event-driven**.

Motivo per cui non si merga appena rispondono i veloci: i reviewer sincroni rispondono in circa un
minuto, ma CodeRabbit pubblica i finding dettagliati (anche Major) minuti dopo. Saltarlo significa
perdere P1 reali pre-merge.

In questo repository i reviewer sincroni sono **tre** — OpenRouter GPT-5.5, Claude Fable 5,
OpenRouter Sol — perché GLM 5.2 non è importato e perché i due su OpenAI diretta sono dormienti.
Nel Bridge sono quattro: se leggi «quattro reviewer sincroni» da qualche parte, quella frase viene
da là.

Flusso pre-merge:

1. lavoro completo + check verdi + branch pushato + PR non draft;
2. far partire i due workflow finali via label — una volta, a head stabile. Se mancano i Secret o
   le label, il passo va dichiarato non eseguibile con quel motivo, non spuntato;
3. leggere gli esiti dei tre reviewer sincroni;
4. aspettare che **CodeRabbit abbia completato**: o pubblica commenti inline azionabili, o il
   riepilogo «No actionable comments». L'attesa è legata all'evento, non a un orologio, e non
   blocca il proprietario. **MISURATO il 02/09/2026 sulla PR #107: CodeRabbit NON revisiona
   automaticamente questo repository** — pubblica «This repository does not receive automatic
   reviews because it has fewer than 10 stars». Non è lentezza: non parte. Va **innescato a mano**
   con un commento `@coderabbitai review` sul head stabile, e solo allora l'attesa qui sopra ha
   senso. Prima di quel giorno l'agente lo aspettava e lo dichiarava assente per il cap dei 15
   minuti — cioè aspettava una cosa che non sarebbe mai arrivata;
5. solo con i tre reviewer + la review reale di CodeRabbit acquisiti → dire al proprietario
   merge sì/no.

I passi 4 e 5 valgono anche quando `.github/workflows/` non c'entra nulla, perché CodeRabbit è
installato sull'account e commenta comunque: l'attesa del suo completamento è in vigore, con il cap
anti-stallo qui sotto. Lo stesso per i check di Codacy e DeepSource e per un'eventuale review di
Codex.

- **Codex** = assente per usage limit: non posta mai, non è un gate.
- **CodeRabbit rate-limited → ASSENTE**, non si aspetta mai (decisione proprietario 2026-07-18):
  se pubblica «review limit reached», si prosegue col verdetto sui reviewer disponibili, e i suoi
  eventuali commenti tardivi li copre il tracciamento post-merge.
- **Tetto anti-stallo obbligatorio:** l'attesa event-driven di CodeRabbit ha un cap di ~15 minuti
  dall'ultimo push sul head. Scaduto il cap senza né commenti né riepilogo, trattalo come assente,
  segnalalo, e non restare in stallo. Il cap è un fallback, non il meccanismo primario.
- **Il gate vale per l'agente, non blocca il proprietario:** governa quando l'agente dichiara
  pronto, non il merge, che resta manuale e possibile in qualsiasi momento.

### Tracciamento post-merge dei commenti tardivi — IN VIGORE

Poiché non si attende più una finestra, i commenti-bot possono arrivare dopo il merge. Quando
arriva un evento review su una PR già mergiata/chiusa, rileggila e cerca: inline comment e review
bodies con `submitted_at` successivo all'ultimo push; thread non risolti; soprattutto i commenti
con `submitted_at` successivo al `merged_at`; annotazioni dei check.

- nulla di non risolto → chiudi, nessuna azione;
- rilevato qualcosa → **apri sempre una Issue** che registra ogni finding (numero PR, head SHA,
  `file:riga`, bot, severità, link al commento) così nulla si perde in una PR chiusa; e per i
  finding reali apri una **nuova PR** dedicata col fix, che parte dall'ultimo `main`, cita la
  Issue e segue tutto l'ordine operativo. La PR mergiata non si riusa e non ci si stacca sopra.
  Una sola Issue può aggregare più finding della stessa PR.

Questo non viola «una sola PR aperta alla volta»: la precedente è chiusa, quindi la fix PR è la
legittima continuazione.

### Sweep delle ultime 5 PR chiuse+mergiate

All'inizio di ogni task (dentro Phase 0) e prima del DONE finale, ispeziona le ultime 5 PR chiuse
e mergiate e cerca finding AI non risolti o post-merge mai indirizzati. Quello che trovi va
registrato e deduplicato in una Issue. L'apertura della fix PR è **differita** se un altro
task/PR è già attivo: lo sweep non apre mai una seconda PR parallela né devia il task corrente.

**Deduplica obbligatoria:** prima di aprire una Issue cerca le Issue esistenti (aperte e chiuse)
per quel finding/PR; se esiste già, collega il commento a quella invece di crearne una nuova.

---

## MINI PHASE 0 OBBLIGATORIA

Prima di patchare un task che tocca parser, CSV, webhook Telegram, token, isolamento, database,
web app, deploy o PR, devi fare Phase 0 **read-only**. Non modificare file durante Phase 0.

```
XTRADER_RELAY_PHASE_0

Task:
- <richiesta>

Detected mode:
- <New task / Current PR repair / Unknown>

Current branch:
- <branch>

File da ispezionare:
- <file>

Comportamento attuale:
- <cosa fa adesso>

Rischi:
- <CSV sbagliato / segnale stantio o duplicato nel feed / chat non autorizzata /
   token esposto o loggato / feed di un utente visibile a un altro /
   rottura compatibilità /xtrader.csv / perdita dati al riavvio del container>

Patch stretta:
- <cosa modificare e cosa non modificare>

Test hard veritieri:
- <py_compile / pytest / test mirati / verifica browser / smoke manuale>

Stop conditions:
- <quando fermarsi>
```

Se manca evidenza, se il comportamento è ambiguo o se la modifica può aumentare il rischio di
segnale duplicato o di leak fra utenti, fermati con:

```
NEEDS_MANUAL

Reason:
- Phase 0 could not determine safe scope.
```

---

## MICRO-AUDIT POST-FIX — OBBLIGATORIO

Dopo ogni patch e prima di test, commit, push, resolve thread o DONE finale, devi fare un
micro-audit. Non basta dire «ho modificato il file»: devi controllare il diff.

```bash
git status --short
git diff --stat
git diff
```

Il micro-audit deve verificare:

- hai modificato solo i file richiesti dal task;
- non hai toccato file fuori scope;
- non hai aggiunto token Telegram, token di feed o `CSV_ACCESS_TOKEN` reali;
- non hai aggiunto chat ID reali;
- non hai aggiunto `.env`, `*.db`, CSV generati, log, cache, `dist/` o artifact;
- non hai abilitato auto-merge;
- non hai introdotto betting diretto, Betfair API o automazione browser verso XTrader;
- non hai indebolito il filtro delle chat;
- non hai indebolito l'isolamento fra utenti né introdotto un `user_id` preso dalla richiesta;
- non hai rotto lo svuotamento del CSV né il TTL di 90 secondi;
- non hai aumentato il rischio di segnale duplicato;
- non hai cambiato l'intestazione o l'ordine delle colonne CSV senza richiesta;
- non hai rotto `/xtrader.csv`;
- non hai introdotto un token in chiaro nei log o nelle risposte;
- non hai fatto refactor largo non richiesto;
- hai aggiornato le docs per il cambiamento (`README.txt`, `SAAS.md`, docstring), o hai scritto
  perché non serviva;
- hai aggiornato la documentazione UI se la modifica tocca l'aspetto della web app, o hai
  scritto perché non ha impatto.

```
POST_FIX_MICRO_AUDIT

Scope:
- PASS / FAIL

Forbidden files:
- PASS / FAIL

Secrets:
- PASS / FAIL

CSV safety:
- PASS / FAIL

Telegram safety:
- PASS / FAIL

Multi-user isolation:
- PASS / FAIL

Config / DB safety:
- PASS / FAIL

Duplicate-signal risk:
- PASS / FAIL

Legacy /xtrader.csv preserved:
- PASS / FAIL

Manual merge preserved:
- PASS / FAIL

Docs updated:
- PASS / FAIL / N/A con motivazione scritta

UI docs updated:
- PASS / FAIL / N/A con motivazione scritta

Regola 1 — test fail-first (rosso PRIMA della patch):
- PASS / FAIL / N/A con motivo
  (PASS = ho eseguito il test sul codice VECCHIO e l'ho visto fallire, con l'output riportato)

Regola 2 — cercata la CLASSE, non il sito (grep su tutto il repo):
- PASS / FAIL / N/A con motivo
  (PASS = riporto il pattern cercato e quanti siti sono risultati)

Regola 2-bis — cercati i CONSUMATORI di ciò che ho cambiato:
- PASS / FAIL / N/A con motivo
  (obbligatoria se ho cambiato un valore di ritorno, dove una funzione scrive, o una promessa
   del docstring. PASS = elenco i chiamanti trovati e, per ciascuno, cosa fa del risultato)

Regola 3 — fonte unica (nessuna correzione duplicata in due posti,
motore JS e motore Python allineati):
- PASS / FAIL / N/A con motivo

Regola 5 — aree dichiarate sane NON toccate
(contratto CSV, filtro chat, alias /xtrader.csv):
- PASS / FAIL

Result:
- PASS / FAIL

Notes:
- <prove>
```

Se il micro-audit fallisce:

```
POST_FIX_AUDIT=FAIL

Reason:
- <motivo>

Action:
- no test
- no commit
- no push
- no resolve
- no DONE
```

Puoi continuare solo se `POST_FIX_AUDIT=PASS`.

---

## TEST HARD VERITIERI — OBBLIGATORIO

> **Stato in questo repository:** `tests/` contiene quattro cartelle —
> `tests/safety/` (guardia sui workflow di review e sul mount pubblico), `tests/engine/` (contratto
> CSV e motore di parsing, casi eseguiti in node sul vero `web/engine.js`, più il confronto fra il
> motore JS e il relay), `tests/relay/` (il CSV servito da `main.py`, asserito sui **byte** della
> risposta HTTP) e `tests/web/` (flusso del prototipo e layout mobile, pilotati da Playwright su
> Chromium). Le dipendenze dei test stanno in
> `requirements-dev.txt`, separate da quelle del deploy:
> `pip install -r requirements-dev.txt && python -m pytest -q`.
>
> **La CI li esegue, dal 12/08/2026:** `.github/workflows/test.yml` gira su ogni PR e sui push a
> **`main`** — non su ogni push, o ogni PR consumerebbe due corse di minuti Actions — e lancia
> `python -m pytest -q` sull'intera suite. Fino a quel giorno giravano solo in locale, e un
> check verde non diceva niente sul loro esito.
>
> **E non puo' passare saltandoli.** Il workflow impone `TEST_RUNTIME_OBBLIGATORIO=1`, che trasforma
> ogni skip per runtime mancante in un **fallimento**: senza, un'installazione di Chromium andata
> male produrrebbe «252 passed, 5 skipped», exit 0, e una spunta identica a quella di una suite
> completa — la stessa classe del check verde senza review chiusa dalla PR #16, e piu' difficile da
> notare, perche' nessuno legge il conteggio degli skip di una CI che passa. In locale la variabile
> resta spenta e gli skip restano skip, con motivo scritto: chi non ha Chromium non puo' eseguire i
> test browser, e dichiararli passati sarebbe la bugia che questo file vieta.
>
> La decisione vive in **`tests/runtime.py`**, fonte unica anche del percorso di Chromium — prima
> ricopiato a mano in **cinque** file di `tests/web/`. Il meccanismo e' testato da
> `tests/safety/test_runtime_severo.py` e il workflow da `tests/safety/test_ci.py`, che lo legge
> dalla **struttura** e non dal testo: la prima versione di quella guardia cercava il nome della
> variabile nel file e restava verde dopo averla tolta dal passo, perche' il commento in cima al
> workflow la nomina.
>
> Chi dichiara i test passati deve comunque aver eseguito il comando e riportato l'output: adesso c'e'
> anche il check, ma vale la regola di sempre — si legge l'esito, non il colore.
> Il relay (`main.py`) ha i suoi test da `tests/relay/`, nati col passaggio a UTF-8 con BOM: sono i
> primi test di `main.py` in questo repository e asseriscono i **byte** della risposta, non le stringhe.
>
> **Una fixture che avvia il relay NON deve ereditare `os.environ`.** `main.py` registra il webhook
> Telegram all'avvio: con `TELEGRAM_BOT_TOKEN` nell'ambiente, avviare il servizio chiama `setWebhook`
> verso `PUBLIC_URL`, il cui default è cablato sull'URL Railway di produzione. Far girare la suite su
> una macchina col `.env` del proprietario caricato **ripunterebbe il webhook del bot vero**, e niente
> diventerebbe rosso. Per lo stesso motivo va tolto `CSV_ACCESS_TOKEN`, che farebbe rispondere 401 alle
> richieste senza token e renderebbe l'esito dipendente dalla macchina. La whitelist è in
> `tests/ambiente.py` — fonte unica per entrambe le fixture che avviano `main:app` — ed è vincolata da
> `tests/safety/test_ambiente_dei_test.py`. La whitelist protegge il sottoprocesso, **non** `import
> main`: per le chiamate in processo `tests/relay/` ha una fixture autouse che azzera `main.TOKEN`
> **e** rimuove le variabili pericolose da `os.environ`. Servono entrambe le cose, e la seconda l'ha
> segnalata Fugu Ultra: l'handler di startup legge `os.environ` **direttamente**, non le costanti del
> modulo, quindi azzerare `main.TOKEN` non fermerebbe un test che facesse partire l'app in processo.
> Misurato senza la ripulitura: lo startup costruisce e tenta davvero
> `https://api.telegram.org/bot<token>/setWebhook?url=<PUBLIC_URL>/telegram/webhook`, e non fallisce
> niente perché l'handler ingoia ogni eccezione.

I test devono essere veri, mirati e verificabili. Non puoi dire che un test è passato se non hai
realmente eseguito il comando e visto esito positivo.

**Vietato:** inventare risultati; `assert True`; test che non chiamano funzioni reali del
progetto; «dovrebbe passare» come se fosse PASS; `|| true` per nascondere fallimenti; skip senza
motivo scritto; dichiarare copertura che il test non ha; dire che Telegram live, Railway o
XTrader live sono testati se non lo sono.

Minimo per modifiche Python:

```bash
python -m py_compile main.py
python -m pytest -q          # se esistono test
```

Per modifiche alla web app, il minimo è caricarla davvero in un browser e pilotarla
(Playwright/Chromium sono disponibili in questo ambiente), verificando **zero errori in console**
e l'output CSV atteso. Un `py_compile` non dice nulla su `web/`.

### Test hard obbligatori per comportamento safety-critical

Per ogni modifica che tocca webhook Telegram, associazione chat → parser, motore di parsing,
scrittura o svuotamento CSV, TTL, autenticazione, isolamento fra utenti, schema del database o
deploy, l'agente deve aggiungere o aggiornare test seri **prima** di dichiarare il task completo.
I test devono esercitare funzioni reali del progetto e coprire, dove praticabile offline:

- **contratto CSV:** intestazione esatta e nell'ordine, 14 colonne, tutti i campi tra virgolette,
  CRLF, UTF-8 **con BOM**, svuotamento a sola intestazione, nessun append incontrollato, nessuna
  riga parziale, virgolette e virgole nei nomi squadra correttamente escapate;
- **parser:** messaggio valido, vuoto, non supportato; quota con virgola e con punto; marcatore
  emoji confrontato per codepoint; `" v "` finale sostituito e non un `" v "` interno al nome
  squadra; campi obbligatori mancanti → nessuna riga scritta;
- **webhook:** chat non associata ignorata; `message` e `channel_post`; messaggio senza testo;
  payload malformato; stessa chat con più parser → ogni parser elabora in modo indipendente e
  nessuno sovrascrive il feed di un altro; codice di verifica come unica eccezione al filtro;
- **ciclo di vita del segnale:** il TTL di 90 secondi scade e il feed torna a sola intestazione;
  un nuovo segnale sostituisce solo la riga dello stesso parser; il timer di un parser è
  indipendente da quello degli altri;
- **autenticazione e isolamento:** feed senza token rifiutato; token errato rifiutato; token
  revocato rifiutato subito; token conservato solo come hash e mai restituito due volte; un
  utente non può leggere, elencare o modificare i parser di un altro (404, non 403);
  `/xtrader.csv` continua a funzionare;
- **persistenza:** riavvio del container senza volume perde i dati (comportamento noto da
  testare, non da scoprire in produzione); migrazione di schema idempotente; config esistente
  intatta dopo un salvataggio fallito;
- **codifica:** un messaggio con emoji resta riconoscibile; il file unico generato produce lo
  stesso output della versione modulare.

Se un rischio non è testabile automaticamente perché richiede Telegram live, Railway o XTrader
reale, aggiungi comunque un test deterministico sulla logica pura e documenta uno smoke test
manuale con passi esatti, risultato atteso e cosa resta non verificato. Non dichiarare coperto un
comportamento se il test non è stato realmente eseguito e riportato con evidenza vera.

```
HARD_TEST_EVIDENCE

Commands run:
- <comando esatto>: PASS / FAIL

Exit codes:
- <comando>: <exit code>

What was actually tested:
- <comportamento reale>

What was not tested:
- <Telegram live / Railway / XTrader live, con motivo>

Test quality:
- REAL / PARTIAL / MANUAL_ONLY

Notes:
- <prove>
```

Se non puoi eseguire test:

```
TESTS_SKIPPED

Reason:
- <motivo esatto>

Risk:
- <cosa resta non verificato>

Required owner action:
- <comando manuale o ambiente necessario>
```

Se i test sono finti, non eseguiti o solo teorici, non dichiarare DONE.

### Generazione automatica dei test hard

Per ogni task che aggiunge, modifica o rimuove codice, la creazione di test hard veritieri **non
è opzionale**: è parte della patch.

- Ogni funzione o ramo nuovo o modificato deve avere un test mirato che chiama il codice reale.
- Il test deve **fallire se il bug torna**, non solo passare.
- Per ogni fix che nasce da un finding o da una review, scrivi **prima** il test che riproduce il
  problema (rosso sul vecchio codice), poi la patch.

`POST_FIX_MICRO_AUDIT` e `FINAL_HARD_VERIFY` includono il controllo «test hard creati/aggiornati
per il cambiamento: PASS/FAIL». Se hai toccato il codice e nessun test, fermati: o aggiungi il
test, o scrivi la nota tecnica precisa del perché non serviva.

---

## REVIEW COMMENTS / INLINE COMMENTS — OBBLIGATORIO

Quando lavori su una PR esistente, non limitarti ai check rossi. Devi leggere e valutare:
commenti normali della PR · corpi delle review · inline review comments · review threads · thread
unresolved · thread outdated · annotazioni dei check · **Codacy · DeepSource · CodeRabbit ·
Codex** (attivi su questo repository) · Sourcery/Gitar se presenti · file modificati nella PR ·
current PR head SHA.

Non dire «nessun lavoro necessario» se esistono commenti review attivi, inline thread non
risolti, check rossi o annotazioni current-head non analizzate.

Il controllo finale va fatto solo **dopo** che tutti i check current-head sono finiti.

### Triage obbligatorio

Classifica ogni finding come: `PATCH_REQUIRED` · `TEST_REQUIRED` · `EVIDENCE_RESOLVE` ·
`SKIP_OUTDATED` · `SKIP_DUPLICATE` · `NEEDS_MANUAL`.

- **PATCH_REQUIRED**: patch stretta.
- **TEST_REQUIRED**: aggiungi o aggiorna test mirato.
- **EVIDENCE_RESOLVE**: dimostra che è già risolto.
- **SKIP_OUTDATED**: spiega perché è vecchio.
- **SKIP_DUPLICATE**: collega al finding principale.
- **NEEDS_MANUAL**: se è ambiguo, rischioso o fuori scope.

### Inline comments

Per ogni inline comment: apri il file indicato; controlla la riga attuale; verifica se il commento
vale ancora sul current head; se vale, fai la patch minima; se non vale più, prepara evidenza; se
non puoi verificarlo, fermati con `NEEDS_MANUAL`.

### Evidence resolve

Non risolvere mai un commento «a sensazione». Prima devi avere: commit SHA · file modificato o
ispezionato · test eseguito · risultato del test · motivo tecnico.

```
Fatto in commit <SHA>

Evidence:
- python -m py_compile main.py: PASS
- <test mirato>: PASS
- File: <path>
```

Se il commento è già coperto:

```
Already covered / skipped

Reason:
- <outdated / duplicate / already fixed / outside scope>

Evidence:
- <comando test o file ispezionato>
```

### Resolve thread

Puoi marcare un thread come risolto solo se: tutti i check current-head sono finiti; è
current-head; non è outdated; la patch è stata fatta o il problema è già coperto; i test/check
rilevanti passano; hai permesso/API per risolverlo; non serve decisione del proprietario.

Se non puoi risolvere via API, rispondi nel thread con evidenza ma non dichiarare falsamente che
è stato risolto. Il merge resta sempre manuale.

---

## PRIORITÀ TECNICHE DEL REPOSITORY

Preserva sempre:

1. Il webhook accetta solo messaggi da chat associate a un profilo/parser esistente.
2. Il parser non inventa dati mancanti.
3. Il CSV resta compatibile con XTrader: 14 colonne, quell'ordine, quel quoting, CRLF, UTF-8
   **con BOM**, verificato da `verify_csv()` prima di ogni scrittura e da `/health`.
4. Ogni feed contiene solo il segnale attivo del proprio parser — che dal #35
   (pezzo 1) può essere composto da **N righe vive** prodotte dallo stesso
   messaggio, con BOM e intestazione **unici** in testa (`componi_feed`).
5. Il segnale scade dopo 90 secondi e il feed torna a sola intestazione; il TTL
   è **per riga** nel filtro di lettura, e alla scadenza di una riga il feed
   perde solo quella.
6. Un parser non può cancellare, modificare o sostituire il segnale di un altro parser.
7. Un utente non può vedere né toccare i dati di un altro utente.
8. I token dei feed esistono in chiaro una sola volta, alla generazione; il server conserva solo
   l'hash.
9. Token e dati sensibili non finiscono nel repository né nei log.
10. `GET /xtrader.csv?token=…` continua a funzionare per il profilo PIERO.
11. Il servizio resta deployabile su Railway con `uvicorn main:app`.
12. Il merge rimane manuale.

---

## DOCUMENTAZIONE — AGGIORNAMENTO OBBLIGATORIO

Ogni volta che aggiungi, modifichi o elimini codice (funzione, endpoint, comportamento, chiave di
config, colonna CSV, regola del parser, sorgente o trasformazione del motore, schermata della web
app), **DEVI** aggiornare la documentazione corrispondente **nello stesso PR**. Una funzione nuova
senza doc, o una rimossa con la doc ancora presente, è un PR incompleto.

Documenti che esistono oggi in questo repository:

| Documento | Quando aggiornarlo |
|---|---|
| `README.txt` | endpoint, variabili d'ambiente, comportamento operativo, flusso principale |
| `SAAS.md` | modello dati, contratto API, regole di isolamento, note operative Telegram, stato dei lavori |
| `README.MD` | solo il titolo del progetto |
| `CLAUDE.md` | quando cambiano i file core, i reviewer attivi, i Secret o le label richieste |
| docstring e commenti tecnici | funzioni pubbliche e moduli non banali |

Documenti citati dal CLAUDE.md del Bridge che **non esistono qui** e che non vanno inventati:
`docs/custom_parser.md`, `docs/xtrader_csv_contract.md`, `docs/audit/roadmap.md`,
`docs/policy_lingue_sito.md`, `docs/internal/config_agent.md`, `docs/user/assistente.md`,
`docs/design/design_handoff.md`. Se un task ne rende utile uno, crealo — ma dichiaralo, non
fingere che esistesse.

`POST_FIX_MICRO_AUDIT` e `FINAL_HARD_VERIFY` includono «docs aggiornate: PASS/FAIL/N/A», dove
PASS = documentazione aggiornata nello stesso PR; FAIL = codice modificato ma documentazione
mancante; N/A = modifica puramente interna senza impatto documentale, **con motivazione scritta**.

---

## GATE DOCUMENTAZIONE UI — OBBLIGATORIO PRIMA DI PROPORRE IL MERGE

La web app in `web/` è la faccia del prodotto verso i clienti, e la sua descrizione non deve mai
restare disallineata da com'è davvero. Perciò:

- Prima di dire che la PR è pronta, **DEVI** aggiornare la documentazione UI nello stesso PR ogni
  volta che la modifica tocca l'aspetto design: schermate e viste, menu, tab, controlli, campi,
  pulsanti, stati e indicatori dinamici (token attivo, segnale vivo con i secondi, feed vuoto,
  attivo/sospeso, chat verificata), flussi di conferma (generazione token, verifica chat,
  eliminazione), palette e sua semantica, copy e microcopy, architettura dell'informazione, o le
  invarianti di sicurezza che vincolano la UI (il token mostrato una volta sola, il codice di
  verifica usa-e-getta).
- Oggi la documentazione UI di riferimento è in `SAAS.md`, in **due** sezioni: «Facciata
  pubblica» per la pagina servita sull'apex (`web/sito.html`) e «Prototipo» per
  l'applicazione servita su `/app`. Sono due facce con regole diverse — la prima è
  indicizzabile e senza sessione, la seconda è `noindex` — e il gate vale per entrambe. Se un task
  richiede un handoff design vero e proprio, si crea `docs/design/design_handoff.md` e da quel
  momento è quello il bersaglio del gate — dichiarandolo nel PR body.
- L'aggiornamento deve essere veritiero e coerente col codice: etichette **verbatim**, stati e
  flussi corretti.
- Se la modifica è puramente interna e non tocca nulla di ciò che la documentazione UI descrive,
  dichiara **N/A con motivazione scritta**. Mai saltare in silenzio.
- È un gate, non un consiglio: una PR che cambia l'aspetto ma lascia la documentazione stantia è
  incompleta e non può essere dichiarata pronta al merge.

---

## QUANDO TOCCHI IL PARSER

Il motore è configurabile: una condizione di riconoscimento più una regola per ciascuna delle 14
colonne (`web/engine.js`, specifica in `SAAS.md`). Devi verificare almeno:

- messaggio valido riconosciuto dalla condizione;
- messaggio vuoto o non supportato → nessuna riga scritta, nessun errore;
- messaggio che **non** soddisfa la condizione → ignorato, feed dell'altro parser intatto;
- quota con virgola e con punto;
- squadre nel formato `Home v Away`, con la sostituzione **dell'ultima** occorrenza di `" v "`:
  un nome squadra che contiene `" v "` non deve essere spezzato;
- marcatore emoji confrontato per codepoint (vedi «REGOLA CODIFICA»);
- espressione regolare non valida → segnalata all'utente, non silenziosamente inefficace;
- assenza di campi obbligatori → nessun CSV scritto se il segnale è pericolosamente incompleto.

Non inventare campionato, squadre, quota o mercato se non sono nel messaggio.

Se modifichi il comportamento del motore, aggiorna **entrambe** le implementazioni (JS e Python,
quando esisterà) nello stesso PR, con un test che ne confronta gli output. Vedi regola 3.

---

## QUANDO TOCCHI IL CSV

Devi verificare:

- intestazione esatta e nell'ordine:
  `Provider, EventId, EventName, MarketId, MarketName, MarketType, SelectionId, SelectionName,
  Handicap, Price, MinPrice, MaxPrice, BetType, Points`;
- tutti i campi tra virgolette (`QUOTE_ALL`), separatore virgola, terminatore `\r\n`, UTF-8
  **con BOM** (`\ufeff` davanti a `"Provider"`);
- compatibilità XTrader;
- le righe attive di un feed vengono dallo stesso messaggio del parser attivo
  (dal #35: **N righe vive**, composte con BOM e intestazione unici); nessuna
  intestazione ripetuta in mezzo al feed;
- svuotamento con sola intestazione allo scadere dei 90 secondi (TTL per riga);
- nessun append incontrollato;
- nessun file o risposta corrotta se arrivano due segnali ravvicinati;
- nessuna scrittura se il segnale non è valido;
- virgolette, virgole e ritorni a capo nei valori correttamente escapati.

Se cambi colonne o formato, scrivi chiaramente nel PR body che è una **breaking change** o che è
retrocompatibile. Cambiare il contratto CSV senza task esplicito viola la regola 5.

---

## QUANDO TOCCHI CONFIG, DATABASE O PERSISTENZA

Devi verificare:

- configurazione e dati esistenti caricati correttamente;
- migrazione di schema idempotente e sicura su un database già popolato;
- default sicuri: se `CSV_ACCESS_TOKEN` è vuoto l'autenticazione non deve diventare un
  colabrodo — dichiaralo e trattalo come rischio, non come comportamento accettabile;
- compatibilità con i dati già scritti dalla versione precedente;
- nessun token reale, chat ID reale o path locale committato;
- comportamento noto al riavvio, e qui va distinto il **default del codice** dalla **configurazione di
  questo servizio**, perché fino al 12/08/2026 questa riga li confondeva:
  - il default è ancora `DB_PATH=/tmp/signals.db`, e chi deployasse senza impostare la variabile
    **perderebbe i dati a ogni deploy**. Resta vero e resta un rischio;
  - **in produzione la variabile è impostata**: `DB_PATH=/data/signals.db`, dentro il volume montato
    su `/data` (`RAILWAY_VOLUME_MOUNT_PATH`). Misurato il 12/08/2026 sulle Variables del servizio, e
    confermato dal log di avvio che dice `Mounting volume on: …`. **I dati persistono.**

  Prima qui c'era scritto «su Railway senza volume i dati si perdono a ogni deploy» come se
  descrivesse la produzione, e nessuno l'aveva misurato: è la stessa forma dell'errore sul BOM, dove
  per mesi era scritto «verificato byte per byte» senza che nessuno avesse guardato i byte. Se il task
  riguarda la persistenza, va dichiarato nel PR body **quale dei due casi** si sta trattando.
- la migrazione dello schema vive in `migra()` e gira **una volta per processo**, non a ogni
  connessione: prima stava dentro `db()`, quindi ogni richiesta — incluse le letture del feed, che
  XTrader interroga a raffica — apriva una transazione di scrittura. Funzionava perché idempotente,
  non perché progettato. Chi aggiunge tabelle le aggiunge lì, e il test di idempotenza le copre.

---

## QUANDO TOCCHI LA WEB APP (`web/`)

Devi verificare:

- la pagina si carica con **zero errori in console** (Playwright/Chromium disponibili qui);
- login, creazione parser, wizard di mappatura, prova messaggio, generazione token, verifica chat
  e log funzionano davvero, pilotati da browser, non solo «a lettura del codice»;
- l'anteprima e il CSV mostrati coincidono con l'output del motore;
- nessuno scorrimento orizzontale della pagina: le tabelle larghe scorrono dentro il proprio
  contenitore (attenzione al `min-width: 0` sui figli di flex e grid — vedi regola 2);
- il layout tiene su schermo stretto (≈390 px) oltre che su desktop;
- i token non compaiono mai nei log, nelle tabelle o negli screenshot;
- il file unico generato da `tools/build_single_file.py` continua a comportarsi come la versione
  modulare, ed è **rigenerato**, non modificato a mano;
- nessuna dipendenza esterna, nessun CDN, nessun build step introdotto senza task esplicito.

Non rimuovere campi o controlli essenziali senza richiesta esplicita.

---

## QUANDO TOCCHI IL DEPLOY (Railway)

Devi verificare:

- `Procfile` e `railway.json` coerenti fra loro e con `uvicorn main:app`;
- dipendenze coerenti con `requirements.txt`, versioni pinnate;
- `python -m py_compile main.py` e l'import dell'app riescono;
- nessun segreto nel repository né nelle variabili committate;
- il deploy non fa push né merge automatico;
- gli endpoint esistenti rispondono ancora: `/health`, `/`, `/xtrader.csv`, `/feed/{slug}.csv`, `/profiles/{…}.csv`,
  `/api/*`, `/app/`;
- se il webhook Telegram viene registrato all'avvio, un `PUBLIC_URL` sbagliato non deve far
  crashare l'avvio.

Se non hai eseguito un deploy reale, scrivi:

```
Deploy not run in this environment.
```

Non dire che il servizio è stato deployato se non è vero.

---

## FINAL HARD VERIFY — OBBLIGATORIO

```
FINAL_HARD_VERIFY

Phase 0:
- PASS / FAIL

Post-fix micro-audit:
- PASS / FAIL

Hard truthful tests:
- PASS / FAIL / SKIPPED with reason

Hard tests created/updated for the change:
- PASS / FAIL / N/A con motivo

Docs updated for the change:
- PASS / FAIL / N/A con motivo

UI docs updated for the change:
- PASS / FAIL / N/A con motivo

Cinque regole anti-regressione rispettate:
- Regola 1 test fail-first (rosso prima della patch): PASS / FAIL / N/A con motivo
- Regola 2 cercata la classe, non il sito (grep su tutto il repo): PASS / FAIL / N/A con motivo
- Regola 2-bis cercati i CONSUMATORI di ciò che ho cambiato: PASS / FAIL / N/A con motivo
- Regola 3 fonte unica, motore JS e Python allineati: PASS / FAIL / N/A con motivo
- Regola 4 una sola PR aperta: PASS / FAIL / UNKNOWN con motivo
  (riporta l'ELENCO EFFETTIVO delle PR aperte, non un PASS asserito; elenco non ottenibile →
   UNKNOWN, mai PASS, e UNKNOWN blocca il DONE come un FAIL)
- Regola 5 contratto CSV, filtro chat e alias legacy non toccati: PASS / FAIL

GitHub checks completed:
- YES / NO / NONE RAN (i check di Codacy e DeepSource compaiono anche qui; NONE RAN significa che
  non è girato NULLA, e non è un PASS)

GitHub checks result:
- PASS / FAIL / PENDING / N/A con motivo

PR comments checked:
- YES / NO

Review bodies checked:
- YES / NO

Inline comments checked:
- YES / NO

Unresolved threads checked:
- YES / NO

Label finali fatte partire + i tre reviewer a API key hanno risposto
(GPT-5.5, Fable 5, GPT-5.6 Sol — GLM non è importato in questo repository):
- YES / NO / bloccato perché mancano i Secret o le label, con quale

CodeRabbit COMPLETATO (commenti azionabili o «No actionable comments»):
- YES / NO / ASSENTE per rate-limit / cap ~15 min scaduto
  (CodeRabbit è installato e commenta anche qui: questa riga non è mai N/A per assenza di workflow)

Codacy / DeepSource / Codex letti:
- YES / NO / Codex assente per usage limit

Last-5 PR post-merge sweep:
- YES / NO

Safety invariants:
- PASS / FAIL

Merge:
- MANUAL ONLY

Final status:
- DONE / PARTIAL / NOT DONE / CHECKS_PENDING / NEEDS_MANUAL
```

Se anche uno solo di questi punti manca, non dichiarare DONE: usa `PARTIAL`, `CHECKS_PENDING` o
`NEEDS_MANUAL` secondo il caso.

---

## BRANCH E PR

**Nuovo task:** crea branch dedicato · lavora solo sul branch · crea una sola PR · non fare merge.

**Fix PR esistente:** resta sul branch della PR · non creare nuova PR · pusha una sola fix mirata
quando possibile · non fare merge.

Se il proprietario ha indicato un branch designato per il task, usa quello e non pushare altrove
senza permesso esplicito.

Se push o PR non sono possibili:

```
NEEDS_MANUAL_UPDATE_BRANCH
```

---

## FORMATO RISPOSTA FINALE

```
DONE / PARTIAL / NOT DONE / CHECKS_PENDING / NEEDS_MANUAL

Summary:
- <cosa è stato cambiato>

Branch:
- <branch>

PR:
- <url o numero>

Commit:
- <sha>

Safety:
- <impatto su CSV / Telegram / token / isolamento fra utenti / segnale duplicato>

Phase 0:
- PASS / FAIL

Post-fix micro-audit:
- PASS / FAIL

Hard truthful tests:
- <comando>: pass/fail/skipped con motivo

GitHub checks:
- complete/pass/fail/pending/none-configured con motivo

Review comments handled:
- <thread/comment URL o summary>: fixed/skipped/needs manual con evidence

Files changed:
- <file>

Final hard verify:
- DONE / PARTIAL / NOT DONE / CHECKS_PENDING / NEEDS_MANUAL

Notes:
- <limiti, test manuali, cose da sapere>
```

Per check ancora pending:

```
CHECKS_PENDING

Reason:
- I check current-head della PR non sono ancora tutti finiti.

Current head:
- <SHA>

Pending checks:
- <check name>

Next allowed action:
- Aspettare la fine dei check, poi rileggere check, annotation, review bodies, commenti,
  inline comments e unresolved threads.
```

Per task bloccato:

```
BLOCKED / NEEDS_MANUAL

Reason:
- <motivo>

Detected mode:
- <New task / Current PR repair / Unknown>

Current state:
- Branch: <branch o unknown>
- Open PR: <numero o unknown>

Required owner action:
- <azione richiesta>
```

---

## COSA NON ESISTE ANCORA IN QUESTO REPOSITORY

Elenco esplicito, perché un gate senza impianto non va dichiarato soddisfatto.

**Cosa c'è già:** i tre workflow di review in `.github/workflows/`, più CodeRabbit, Codacy,
DeepSource e Codex, che sono GitHub App installate sull'account e compaiono sulle PR di questo
repository. Non dedurre l'assenza delle App dal contenuto di `.github/workflows/`: sono due cose
installate in posti diversi. Aspetta i loro check, leggi i loro commenti, fai il triage.

| Manca | Conseguenza per l'agente |
|---|---|
| ~~Secret delle API key~~ | **Configurati il 2026-08-11** come `BETRELAY_GPT`, `BETRELAY_FABLE`, `BETRELAY_FUGU`. Non erano configurati fino a quel giorno, e i workflow uscivano **verdi senza revisionare** (`::notice` nei log della PR #1): un check verde non prova che un modello abbia letto il diff. Attenzione ai nomi: sono quelli di questo repository, non quelli del Bridge. **Dal 02/09/2026** `BETRELAY_GPT` lo leggono solo i **due dormienti** (credito esaurito), e la chiave che alimenta due dei tre reviewer vivi è `BETRELAY_FUGU`, che ora porta la chiave **OpenRouter**. |
| ~~Label `final-fable-review`, `final-fugu-review`~~ | **Create il 2026-08-11.** La creazione era azione del proprietario, una volta sola. **Applicarle** è invece ricorrente e spetta all'agente: rimuovere e riaggiungere a ogni head stabile. |
| Workflow GLM 5.2 | non importato per scelta. Non contarlo né aspettarlo. |
| ~~Reviewer a API key su OpenAI diretta~~ | **Dormienti dal 02/09/2026**: `pr-review-gpt55.yml` e `pr-review-gpt56-sol.yml` hanno solo `workflow_dispatch` e **non producono più check sulle PR**. Il loro `workflow_dispatch` NON esegue la review — la `if:` del job pretende `github.event.pull_request`, assente su un avvio manuale, quindi il job viene saltato: un run verde con zero job. Per riprovarli si toglie il commento al trigger `pull_request`. |
| ~~Workflow di build/test propri del repo~~ | **Creato il 12/08/2026**: `.github/workflows/test.yml` esegue `pytest -q` su ogni PR e sui push a `main`, con `TEST_RUNTIME_OBBLIGATORIO=1` perche' uno skip per runtime mancante non possa lasciarlo verde. Prima non esisteva e i test giravano solo in locale. |
| ~~Test del relay (`main.py`)~~ | **Creati l'11/08/2026** in `tests/relay/test_csv_contract.py` col passaggio a UTF-8 con BOM: byte della risposta HTTP, `verify_csv()`, fail-closed di `store_signal`, esito del verificatore su `/health`. `tests/` ha ora quattro cartelle. |
| `docs/` | esiste dal 14/08/2026, ma contiene **solo** `docs/xtrader/screenshot/` — materia prima fornita dal proprietario, non una guida. I documenti del Bridge citati sopra continuano a non esistere qui e non vanno inventati. |
| `AGENTS.md` | questo file è autosufficiente; se AGENTS.md verrà aggiunto, ha precedenza. |
| Motore di parsing Python | oggi il motore vive solo in `web/engine.js`. Quando nascerà quello Python, la regola 3 diventa vincolante su entrambi. |

---

## REGOLA D'ORO

Non cercare di «fare tutto». Per questo repository è meglio una patch piccola, chiara e sicura
che una grande riscrittura.

Il relay deve restare prevedibile:

```
chat autorizzata → parser del proprietario → riga CSV corretta →
XTrader legge il feed giusto → il segnale scade dopo 90 secondi
```

Qualsiasi modifica che rompe questa catena, o che permette a un utente di vedere il feed di un
altro, deve essere bloccata o approvata esplicitamente dal proprietario.

Il merge resta sempre manuale.
