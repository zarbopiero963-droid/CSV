# Screenshot XTrader — automazione (condizioni, azioni, strategie)

Materiale **sorgente** per le guide BetRelay, fornito dal proprietario del progetto:
102 screenshot reali dell'automazione di XTrader + il PDF `FORMULA.pdf` (103 file in tutto).

> ⚠️ Questi file **non sono ancora una guida**. Sono la materia prima da cui vengono scritte
> le pagine di `/documentazione`. Nessuna pagina del sito li linka al momento: ci finiranno
> quelli effettivamente usati, uno per uno, con testo nostro accanto.

## Cosa c'è

| Cartella | File | Contenuto |
|---|---|---|
| `condizioni/` | 70 (69 PNG + `FORMULA.pdf`) | dialog **Nuova Condizione**: i tipi di condizione, i criteri di selezione, i valori di riferimento, le formule |
| `azioni-se-vero/` | 21 PNG | dialog **Nuova Azione**: i tipi di azione disponibili nel ramo «se vero» |
| `azioni-se-vero-se-falso/` | 2 PNG | il ramo «se falso» |
| `varie/` | 10 PNG | finestre di contorno: **Filtro Mercati**, **Finestra Segnali**, monitor |

## Note del proprietario (dai due documenti allegati su Drive)

- *«per entrambi: azioni se vero, azioni se falso hanno le stesse azioni disponibili»* — cioè i
  due rami dell'automazione offrono lo stesso set di azioni; la cartella `azioni-se-vero-se-falso`
  serve solo a documentare il ramo «se falso», non un elenco diverso.
- *«il pulsante guida variabili all'interno di: condizione → formula, riportato nel PDF nominato
  FORMULA qui dentro stesso»* — `condizioni/FORMULA.pdf` è la guida alle variabili utilizzabili
  nelle condizioni di tipo formula.

## Lo screenshot più importante

`varie/02-20260708-170631.png` è la **Finestra Segnali** — la schermata che il manuale ufficiale
descrive solo a parole, senza immagine. Si vedono:

- l'elenco **Fonti** con le colonne reali: Nome Servizio · Nome File · Url · Ricarica
  Automaticamente · Intervallo · Escludi N.V. · Riconoscimento Sel · Ultimo Agg.;
- le due fonti predefinite **Segnali Importati** e **Segnali Creati**;
- l'elenco segnali con le colonne lette dal CSV fra parentesi quadre — `[Provider]`, `[EventId]`,
  `[MarketId]`, `[SelectionId]`, `[EventName]`, `[MarketName]`, `[SelectionName]`,
  `[MarketType]`, `[BetType]`, `[Handicap]` — accanto a quelle ricavate da XTrader (Fonte, Data,
  Sport, Inizio);
- i filtri **Solo Validi**, Provider, Nome Mercato, Marketid.

Le colonne fra parentesi quadre corrispondono a quelle che BetRelay scrive — ma la **fonte del
contratto CSV non è questo screenshot**: è `csv_writer.CSV_HEADER` nel codice, documentato in
[`docs/xtrader_csv_contract.md`](../../../../docs/xtrader_csv_contract.md) e verificato da un test
che confronta la tabella della documentazione con l'header reale. Uno screenshot mostra com'era
un giorno; se un domani divergessero, ha ragione il codice.

## Naming

I nomi originali (`Immagine 2026-07-06 165210.png`) sono stati normalizzati in
`NN-AAAAMMGG-HHMMSS.png`: niente spazi (URL-safe) e **ordine cronologico = ordine di lettura**,
che è anche l'ordine in cui il proprietario ha percorso i menu. La corrispondenza con i nomi
originali e gli id Drive è in `manifest.json`.

## Privacy — verifica esaustiva

**Tutte e 102 le immagini sono state aperte e controllate una per una** (non a campione). Nessuna
credenziale, nessun saldo, nessun token, nessun ID di conto Betfair. I dati di mercato visibili
(squadre, competizioni, quote, importi abbinati) sono palinsesto pubblico Betfair.

Due rilievi, entrambi non sensibili ma segnalati per correttezza:

| Cosa | Dove | Valutazione |
|---|---|---|
| Barra del titolo con due metadati dell'abbonamento del proprietario (data di scadenza e ultimo accesso) | `varie/01` | **oscurata prima del commit**: nell'immagine pubblicata al posto dei valori c'e' un riquadro bianco con la dicitura «dati abbonamento rimossi» (misurato con l'OCR sull'immagine committata), e i valori non compaiono nemmeno in questo catalogo (vedi «Cosa e' cambiato entrando nel repository») |
| Nome di strategia personale **`SIG_PREMATCH_BASE`** | `varie/10`, `azioni-se-vero-se-falso/01` e `02`, `condizioni/01`, `02`, `03`, `10`, `11`, `15`, `69` — 10 immagini | nome scelto dal proprietario, visibile nell'elenco strategie; lasciato per scelta consapevole |

Solo il primo conteneva dati d'account, ed e' stato oscurato. Il secondo non impedisce la
pubblicazione; volendo si puo' sfocare prima di usare quelle immagini in una guida pubblica.

Condizioni e azioni portano ovunque i **nomi di default generati dal programma**
(`Nuova Condizione #1`, `Nuova Azione #2`, `Nuova Regola #1`), quindi non rivelano nulla del
metodo del proprietario.

## Descrizioni

Ogni immagine ha una descrizione a parole in **`catalogo.md`** (leggibile) e **`catalogo.jsonl`**
(una riga JSON per immagine). Campi: `file`, `finestra`, `rilevanza_bridge`, `descrizione`,
`usare_per`, `privacy`.

Servono al futuro **assistente AI**: un modello non può guardare gli screenshot, ma leggendo il
catalogo sa *quale* immagine mostrare e *cosa* contiene. La guida operativa che le usa è
[`docs/xtrader_integration.md`](../../../../docs/xtrader_integration.md); il riferimento delle
formule è [`docs/xtrader_formule.md`](../../../../docs/xtrader_formule.md).

`catalogo.md` si apre con l'indice delle **13 immagini a rilevanza altissima** per il bridge: fonte segnali (`varie/02`, `03`, `04`), indice azioni e azione da segnali
(`azioni-se-vero/01`, `04`), numero esecuzioni e nodo condizioni (`condizioni/13`, `15`),
indice condizioni e criteri di selezione (`condizioni/17`, `18`), codici MarketType
(`condizioni/20`, `21`, `22`) e la guardia anti-doppione (`condizioni/42`).

## Cosa manca ancora

L'elenco degli screenshot **non ancora disponibili**, in ordine di utilità e con l'indicazione di
cosa è oggi ricostruito al loro posto, è in
[`docs/screenshot_xtrader_mancanti.md`](../../../../docs/screenshot_xtrader_mancanti.md).

## Diritti

Screenshot dell'interfaccia di XTrader, prodotto di **TradingSportivo**, catturati dal
proprietario del progetto e usati qui per documentare l'integrazione con BetRelay. BetRelay è un
progetto indipendente, non affiliato a TradingSportivo.

---

## Cosa e' cambiato entrando nel repository

Questo repository e' **pubblico**: prima del commit il materiale e' stato verificato, e due
cose sono state modificate rispetto al pacchetto originale. Sono scritte qui perche' una
redazione non dichiarata e' peggio di nessuna redazione.

**1 — Una immagine e' stata oscurata.** In `varie/01-20260708-170559.png` la barra del titolo
mostrava due dati dell'account del proprietario: data di scadenza dell'abbonamento e data/ora
dell'ultimo accesso precedente. La striscia del titolo e' stata coperta e sostituita con
`XTrader [dati abbonamento rimossi]`. Il resto dell'immagine — il menu «Funzioni», che e' la
parte utile — e' intatto.

**2 — `condizioni/FORMULA.pdf` NON e' stato incluso.** E' la stampa integrale di un articolo del
servizio assistenza del produttore di XTrader: ripubblicarlo in un repository pubblico e' una
questione di licenza, non di privacy, e non e' una decisione che spetta a chi committa. Il file
resta nel pacchetto originale del proprietario. Il `manifest.json` lo elenca ancora — e' l'unica
differenza fra manifesto e contenuto, ed e' voluta — ma **senza il link di download**: il
`drive_id` originario e' stato rimosso perche' CodeRabbit ha misurato (PR #43) che il file era
scaricabile senza autenticazione, e un link pubblico a contenuto sotto licenza in un repository
pubblico e' una ripubblicazione con un giro in piu'.

**Verifica eseguita, non dichiarata.** Tutte e 102 le immagini sono state passate all'OCR
(`tesseract`, italiano+inglese) cercando: abbonamento, scadenza, ultimo accesso, saldo, username,
licenza, API key, indirizzi e-mail e sequenze di 7+ cifre. Esito:

- **1 immagine** con dati di account reali → oscurata (sopra);
- **2 immagini** con la voce «Saldo Conto Betfair» → e' il **nome di un tipo di condizione** in
  una tendina, non un saldo: nessun valore;
- **1 immagine** con l'etichetta «Username:» → dialog «Invia E-mail» con **tutti i campi vuoti**,
  verificata a vista;
- **10 immagini** con `SIG_PREMATCH_BASE`, nome di una strategia del proprietario (l'OCR ne
  aveva lette 4; le altre 6 sono emerse dal controllo a vista, ed e' l'inventario della tabella
  sopra a fare fede): materiale suo, non un dato sensibile. Lasciato, ma segnalato qui perche'
  la scelta sia consapevole;
- le sequenze di 7+ cifre sono date e orari del palinsesto (`08/07/2026 17:30`) letti male
  dall'OCR, piu' nomi di squadre e competizioni: dati pubblici.

Il controllo e' ripetibile: l'elenco dei termini cercati e' quello qui sopra.
