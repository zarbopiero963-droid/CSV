// Casi di prova del motore di parsing, eseguiti su web/engine.js REALE.
// Stampa JSON su stdout; il wrapper pytest asserisce caso per caso.
//
//   node tests/engine/engine_cases.mjs
//
// Il motore vive in JavaScript e non e' importabile da Python: si esegue in node,
// cosi' il test esercita il codice vero e non una riscrittura.

// Namespace, non named import: se un export manca il caso diventa rosso con un
// messaggio utile, invece di far fallire il caricamento del modulo.
import * as E from '../../web/engine.js';

const {
  COLUMNS, runParser, extractValue, matches,
  toCsv, headerOnlyCsv, suggestConfig, describeRule,
} = E;
const REQUIRED_COLUMNS = E.REQUIRED_COLUMNS;

const VS = '\u{1F19A}';           // 🆚 marcatore versus, codepoint esatto
const OROLOGIO = '⏰';        // ⏰

const casi = [];
const caso = (nome, fn) => {
  try {
    const dettaglio = fn();
    casi.push({ nome, ok: true, dettaglio: dettaglio ?? null });
  } catch (e) {
    casi.push({ nome, ok: false, errore: String(e && e.message || e) });
  }
};

const eq = (got, atteso, che) => {
  if (JSON.stringify(got) !== JSON.stringify(atteso)) {
    throw new Error(`${che}: atteso ${JSON.stringify(atteso)}, ottenuto ${JSON.stringify(got)}`);
  }
  return got;
};

// Config equivalente al parser oggi in produzione in main.py.
function configProduzione() {
  const columns = {};
  for (const c of COLUMNS) columns[c] = { source: 'empty' };
  columns.Provider = { source: 'constant', value: 'XTrader' };
  columns.EventName = {
    source: 'line', anchor: VS, part: 'after', marker: VS,
    transforms: [{ op: 'replace_last', from: ' v ', to: ' - ' }, { op: 'trim' }],
  };
  columns.MarketName = { source: 'constant', value: 'Over/Under 1,5 gol' };
  columns.MarketType = { source: 'constant', value: 'OVER_UNDER_15' };
  columns.SelectionName = { source: 'constant', value: 'Over 1,5 goal' };
  columns.Handicap = { source: 'constant', value: '0' };
  columns.BetType = { source: 'constant', value: 'PUNTA' };
  return { match: { type: 'contains', value: 'P.Bet. PREMACHT 0,5HT' }, columns };
}

const MSG_VALIDO = `P.Bet. PREMACHT 0,5HT\n${VS} Manchester City v Aston Villa\n${OROLOGIO} 20:45`;

/* ---------------------------------------------------- contratto CSV */

caso('csv: intestazione esatta e nell-ordine', () => {
  eq(COLUMNS, ['Provider', 'EventId', 'EventName', 'MarketId', 'MarketName', 'MarketType',
    'SelectionId', 'SelectionName', 'Handicap', 'Price', 'MinPrice', 'MaxPrice',
    'BetType', 'Points'], 'colonne');
  return COLUMNS.length;
});

caso('csv: tutti i campi quotati, CRLF, due righe', () => {
  const { row } = runParser(MSG_VALIDO, configProduzione());
  const csv = toCsv(row);
  eq(csv.split('\r\n').length - 1, 2, 'righe terminate da CRLF');
  eq((csv.match(/"/g) || []).length, 56, 'virgolette (14 colonne x 2 x 2 righe)');
  if (csv.includes('\n\n') || /[^\r]\n/.test(csv)) throw new Error('trovato LF non preceduto da CR');
  return csv.split('\r\n')[1];
});

caso('csv: il BOM c-e-, ed e- U+FEFF', () => {
  const csv = toCsv(runParser(MSG_VALIDO, configProduzione()).row);
  eq(csv.codePointAt(0), 0xfeff, 'primo codepoint del CSV di un segnale');
  eq(headerOnlyCsv().codePointAt(0), 0xfeff, 'primo codepoint del feed vuoto');
  // Il BOM sta PRIMA delle virgolette di "Provider", non dentro.
  eq(csv.slice(1, 12), '"Provider",', 'cosa segue il BOM');
  return 'ok';
});

// Esportata perche' test_engine_contract.py la confronti con main.py: e- il
// guardiano della regola 3, le due implementazioni dello stesso contratto.
caso('csv: CSV completo esportato per il confronto col motore Python', () => {
  // Non solo l'intestazione: una riga che contiene i tre caratteri capaci di
  // rompere un CSV — campi vuoti, una virgola, una virgoletta da raddoppiare.
  // Confrontare solo l'intestazione non vedrebbe una divergenza nel quoting.
  // Segnalato da CodeRabbit.
  const row = COLUMNS.map(() => '');
  row[COLUMNS.indexOf('Provider')] = 'XTrader';
  row[COLUMNS.indexOf('EventName')] = 'Squadra "A", Citta - Altra';
  row[COLUMNS.indexOf('BetType')] = 'PUNTA';
  return { csvCompleto: toCsv(row), soloIntestazione: headerOnlyCsv(), bom: headerOnlyCsv().codePointAt(0) };
});

caso('verifica: accetta il formato giusto', () => {
  eq(E.verifyCsv(toCsv(runParser(MSG_VALIDO, configProduzione()).row)), null, 'segnale valido');
  eq(E.verifyCsv(headerOnlyCsv()), null, 'feed vuoto');
  return 'ok';
});

caso('verifica: respinge le forme sbagliate', () => {
  const buono = headerOnlyCsv();
  const intestazione = buono.slice(1).split('\r\n')[0];
  const quattordici = Array(14).fill('"x"').join(',');
  const casi = {
    'BOM assente': buono.slice(1),
    // Intestazione a 11 colonne del vecchio prototipo del Bridge: un formato
    // che esiste davvero ed e- esattamente cio- che va respinto.
    'intestazione a 11 colonne': '\ufeffProvider,SelectionId,MarketId,SelectionName,MarketName,EventName,MarketType,BetType,Price,MinPrice,MaxPrice\r\n',
    'intestazione senza virgolette': '\ufeff' + COLUMNS.join(',') + '\r\n',
    'LF nudo': '\ufeff' + intestazione + '\n',
    'riga con 13 campi': '\ufeff' + intestazione + '\r\n' + Array(13).fill('"x"').join(',') + '\r\n',
    'due segnali': '\ufeff' + intestazione + '\r\n' + quattordici + '\r\n' + quattordici + '\r\n',
    'vuoto': '',
    // Segnalata da CodeRabbit: filtrare tutte le righe vuote le accettava.
    'riga vuota in mezzo': '\ufeff' + intestazione + '\r\n\r\n' + quattordici + '\r\n',
    'senza terminatore finale': '\ufeff' + intestazione,
    'CR isolato in un campo': '\ufeff' + intestazione + '\r\n"x\rx"' + ',"x"'.repeat(13) + '\r\n',
  };
  const passati = Object.entries(casi).filter(([, t]) => E.verifyCsv(t) === null).map(([n]) => n);
  eq(passati, [], 'forme sbagliate accettate per errore');
  return Object.keys(casi).length + ' forme respinte';
});

caso('csv: solo intestazione quando non c-e- segnale', () => {
  const h = headerOnlyCsv();
  eq(h.endsWith('\r\n'), true, 'terminatore');
  eq(h.split('\r\n')[0].split(',').length, 14, 'colonne nell-intestazione');
  return h.length;
});

caso('csv: virgole e virgolette nei nomi squadra sono escapate', () => {
  const row = COLUMNS.map(() => '');
  row[2] = 'Squadra "A", Citta\' v Altra';
  const csv = toCsv(row);
  if (!csv.includes('"Squadra ""A"", Citta\' v Altra"')) {
    throw new Error('quoting/escape non conforme: ' + csv.split('\r\n')[1]);
  }
  return csv.split('\r\n')[1].slice(0, 60);
});

/* ------------------------------------------------------------ parser */

caso('parser: messaggio valido riconosciuto', () => {
  const r = runParser(MSG_VALIDO, configProduzione());
  eq(r.matched, true, 'matched');
  eq(r.row[COLUMNS.indexOf('EventName')], 'Manchester City - Aston Villa', 'EventName');
  return r.row[2];
});

caso('parser: messaggio vuoto non riconosciuto e nessun errore', () => {
  const r = runParser('', configProduzione());
  eq(r.matched, false, 'matched');
  return 'ok';
});

caso('parser: messaggio non supportato ignorato', () => {
  const r = runParser('Buongiorno a tutti, nessun segnale', configProduzione());
  eq(r.matched, false, 'matched');
  return 'ok';
});

caso('parser: condizione vuota non riconosce nulla', () => {
  eq(matches('qualunque testo', { type: 'contains', value: '' }), false, 'condizione vuota');
  eq(matches('qualunque testo', null), false, 'condizione assente');
  return 'ok';
});

caso('parser: regex non valida non solleva e non riconosce', () => {
  eq(matches('Quota 1,5', { type: 'regex', value: 'Quota (1,5' }), false, 'regex rotta');
  eq(extractValue('Quota 1,5', { source: 'regex', pattern: '([0-9' }), '', 'estrazione con regex rotta');
  return 'ok';
});

caso('parser: quota con virgola e con punto', () => {
  const regola = { source: 'regex', pattern: '(?:@|quota)\\s*([0-9]+[.,][0-9]+)', group: 1,
                   transforms: [{ op: 'comma_to_dot' }] };
  eq(extractValue('@ 1.85', regola), '1.85', 'punto');
  eq(extractValue('Quota 1,42', regola), '1.42', 'virgola convertita');
  return 'ok';
});

caso('parser: sostituito l-ULTIMO " v ", non uno interno al nome squadra', () => {
  const cfg = configProduzione();
  // "Waasland v Beveren" contiene " v " dentro il nome della squadra di casa.
  const msg = `P.Bet. PREMACHT 0,5HT\n${VS} Waasland v Beveren v Anderlecht`;
  const r = runParser(msg, cfg);
  eq(r.row[2], 'Waasland v Beveren - Anderlecht', 'solo l-ultima occorrenza sostituita');
  return r.row[2];
});

caso('parser: marcatore emoji confrontato per codepoint', () => {
  const cfg = configProduzione();
  eq([...cfg.columns.EventName.marker].map(c => c.codePointAt(0).toString(16)), ['1f19a'],
     'il marcatore e- U+1F19A senza variation selector');
  // Un marcatore visivamente simile ma con variation selector NON deve combaciare.
  const conVS16 = `P.Bet. PREMACHT 0,5HT\n\u{1F19A}️ Roma v Lazio`;
  const r = runParser(conVS16, cfg);
  eq(r.row[2], '️ Roma - Lazio', 'il variation selector resta nel testo estratto');
  return 'ok';
});

caso('parser: campi obbligatori mancanti -> segnale incompleto', () => {
  const cfg = configProduzione();
  // Riconosciuto dalla condizione ma senza la riga dell-evento.
  const r = runParser('P.Bet. PREMACHT 0,5HT\nsolo chiacchiere', cfg);
  eq(r.matched, true, 'la condizione combacia');
  eq(Array.isArray(r.missing), true, 'runParser deve elencare le colonne mancanti');
  eq(r.missing.includes('EventName'), true, 'EventName mancante va segnalato');
  eq(r.complete, false, 'un segnale senza evento non e- completo');
  return r.missing;
});

caso('parser: segnale valido e- completo', () => {
  const r = runParser(MSG_VALIDO, configProduzione());
  eq(r.complete, true, 'complete');
  eq(r.missing, [], 'missing');
  return 'ok';
});

caso('parser: EventName di soli spazi vale come mancante (regola SENZA trim)', () => {
  const cfg = configProduzione();
  // La riga esiste, il marcatore combacia, ma dopo il marcatore c-e- solo spazio.
  //
  // La regola di produzione ha una trasformazione `trim` che azzera il valore e
  // maschera il difetto: il controllo delle colonne obbligatorie risulterebbe
  // corretto solo per merito della configurazione. Qui il `trim` viene TOLTO,
  // perche- l-utente che costruisce un parser dal wizard non e- obbligato ad
  // aggiungerlo, e senza normalizzazione " " e- truthy: `complete` diventa true e
  // api.js scrive una riga quotata con l-evento vuoto.
  cfg.columns.EventName = { source: 'line', anchor: VS, part: 'after', marker: VS };
  const r = runParser(`P.Bet. PREMACHT 0,5HT\n${VS}    `, cfg);
  eq(r.matched, true, 'la condizione combacia');
  eq(r.missing.includes('EventName'), true, 'EventName di soli spazi va elencato come mancante');
  eq(r.complete, false, 'una riga con evento di soli spazi non va scritta');
  return r.missing;
});

caso('parser: il trim della config non e- l-unica difesa (EventName con trim)', () => {
  // Il gemello del caso sopra: con la regola di produzione, che il `trim` ci sia
  // o no, l-esito deve essere lo stesso. Se un giorno il controllo tornasse a
  // fidarsi della trasformazione, questo caso resta verde e l-altro diventa rosso.
  const r = runParser(`P.Bet. PREMACHT 0,5HT\n${VS}    `, configProduzione());
  eq(r.complete, false, 'evento vuoto dopo il trim: nessuna riga');
  return r.missing;
});

caso('parser: marcatore senza evento, confronto col motore Python', () => {
  // Regola 3, sul caso corretto in main.py il 12/08/2026. Il motore JS era gia-
  // giusto e il relay no: `''.splitlines()[0]` sollevava IndexError, quindi 500 sul
  // webhook. Questo caso esporta gli esiti del JS sui quattro ingressi misurati, e
  // il wrapper pytest li confronta con `parse_message`: se una delle due torna ad
  // accettare un evento vuoto, la divergenza diventa rossa invece di restare
  // invisibile perche- ciascuna passa i propri test.
  const ingressi = [
    `P.Bet. PREMACHT 0,5HT\n${VS}`,
    `P.Bet. PREMACHT 0,5HT\n${VS}   `,
    `P.Bet. PREMACHT 0,5HT\n${VS}\t`,
    `P.Bet. PREMACHT 0,5HT\nSQUADRA-A v SQUADRA-B ${VS}`,
  ];
  const esiti = ingressi.map(m => {
    const r = runParser(m, configProduzione());
    return { messaggio: m, complete: r.complete, mancanti: r.missing };
  });
  for (const e of esiti) {
    eq(e.complete, false, `evento vuoto non deve essere completo: ${JSON.stringify(e.messaggio)}`);
  }
  return esiti;
});

caso('parser: una obbligatoria di soli spazi vale come mancante', () => {
  const cfg = configProduzione();
  // Stessa classe di difetto su una colonna alimentata da una costante: una
  // costante fatta di spazi e- una configurazione vuota, non un valore. Si prova
  // su `MarketType`, una delle QUATTRO obbligatorie (prima era `Provider`, che
  // obbligatoria non e- piu').
  cfg.columns.MarketType = { source: 'constant', value: '   ' };
  const r = runParser(MSG_VALIDO, cfg);
  eq(r.missing.includes('MarketType'), true, 'MarketType di soli spazi va elencato come mancante');
  eq(r.complete, false, 'nessuna riga con una obbligatoria di soli spazi');
  return r.missing;
});

caso('parser: Provider e Price NON sono obbligatorie', () => {
  if (!Array.isArray(REQUIRED_COLUMNS)) throw new Error('engine.js non esporta REQUIRED_COLUMNS');
  // `Provider` e' sempre la costante "XTrader" e non protegge da nulla; `Price` la
  // mette XTrader dal proprio book. Pretenderle bloccherebbe segnali validi.
  eq(REQUIRED_COLUMNS.includes('Provider'), false, 'Provider non e- obbligatoria');
  eq(REQUIRED_COLUMNS.includes('Price'), false, 'Price non e- obbligatoria');
  return REQUIRED_COLUMNS;
});

caso('parser: le QUATTRO obbligatorie sono quelle decise dal proprietario', () => {
  // La lista, verbatim: se qualcuno la cambia in un motore solo, questo caso lo
  // vede — e il confronto JS/Python la tiene identica all'altro motore.
  eq([...REQUIRED_COLUMNS].sort(),
     ['BetType', 'EventName', 'MarketType', 'SelectionName'],
     'le quattro colonne obbligatorie decise su #2/#25');
  return REQUIRED_COLUMNS;
});

/* -------------------------------------------------------- estrazione */

caso('estrazione: marcatore con maiuscole diverse dalla riga', () => {
  const regola = { source: 'line', anchor: 'quota:', part: 'after', marker: 'QUOTA:',
                   transforms: [{ op: 'trim' }] };
  eq(extractValue('Quota: 1.85', regola), '1.85',
     'findLine trova la riga senza distinzione di caso: anche il taglio deve essere coerente');
  return 'ok';
});

caso('estrazione: riga inesistente -> stringa vuota', () => {
  eq(extractValue('niente qui', { source: 'line', anchor: 'assente', part: 'whole' }), '', 'vuoto');
  return 'ok';
});

caso('estrazione: regola vuota o assente -> stringa vuota', () => {
  eq(extractValue('testo', { source: 'empty' }), '', 'empty');
  eq(extractValue('testo', undefined), '', 'undefined');
  return 'ok';
});

/* ------------------------------------------------------ suggeritore */

caso('suggeritore: ancora tagliata per codepoint, mai mezzo surrogato', () => {
  // Riga con "vs" (non l-emoji) e un emoji astrale a cavallo dell-indice 12.
  const msg = 'Segnale LIVE\nAtalanta \u{1F525}\u{1F525}\u{1F525} vs Napoli\n@ 1.90';
  const cfg = suggestConfig(msg);
  const ancore = [cfg.columns.EventName?.anchor, cfg.match.value].filter(Boolean);
  for (const a of ancore) {
    for (const ch of a) {
      const cp = ch.codePointAt(0);
      if (cp >= 0xD800 && cp <= 0xDFFF) {
        throw new Error(`ancora con surrogato spaiato: ${JSON.stringify(a)}`);
      }
    }
  }
  return ancore;
});

caso('suggeritore: sul messaggio reale propone Provider e EventName', () => {
  const cfg = suggestConfig(MSG_VALIDO);
  eq(cfg.columns.Provider.value, 'XTrader', 'Provider');
  eq(cfg.columns.EventName.marker, VS, 'marcatore versus');
  eq(extractValue(MSG_VALIDO, cfg.columns.EventName), 'Manchester City - Aston Villa', 'evento');
  return 'ok';
});

caso('descrizione regola: leggibile e non solleva', () => {
  eq(describeRule({ source: 'empty' }), '(vuoto)', 'vuoto');
  eq(typeof describeRule(configProduzione().columns.EventName), 'string', 'line');
  eq(describeRule(null), '(vuoto)', 'null');
  return 'ok';
});

/* --------------------------- confronto col gemello Python (esegui_parser) */

// I casi sono definiti QUI una volta sola e il loro output JS e' l'ORACOLO: il
// test Python legge ciascun (messaggio, config), fa girare `esegui_parser` e
// pretende lo stesso `runParser` — matched, row, missing, complete. Cosi' gli
// ingressi non sono ricopiati in Python (regola 3) e la divergenza, se c'e',
// diventa rossa nominando il caso.
function casiConfronto() {
  const casi = [];
  const aggiungi = (nome, message, config) =>
    casi.push({ nome, message, config, atteso: runParser(message, config) });

  const prod = configProduzione();
  aggiungi('prod: messaggio valido → completo', MSG_VALIDO, prod);
  aggiungi('prod: " v " dentro un nome squadra → sostituita solo l-ultima',
    `P.Bet. PREMACHT 0,5HT\n${VS} Man v City v Napoli\n@ 1.90`, prod);
  aggiungi('prod: header presente ma nessuna riga col marcatore → non completo',
    'P.Bet. PREMACHT 0,5HT\nnessun marcatore qui\n@ 1.85', prod);
  aggiungi('prod: header assente → non riconosciuto',
    `Altro canale\n${VS} A v B`, prod);
  aggiungi('prod: evento vuoto dopo il marcatore → non completo',
    `P.Bet. PREMACHT 0,5HT\n${VS}\n@ 1.85`, prod);

  // Ogni sorgente e trasformazione, isolati.
  const soloEmpty = {}; for (const c of COLUMNS) soloEmpty[c] = { source: 'empty' };

  aggiungi('sorgente message', 'riga uno\nriga due', {
    match: { type: 'contains', value: 'riga' },
    columns: { ...soloEmpty, EventName: { source: 'message' } },
  });
  aggiungi('sorgente regex con gruppo e comma_to_dot', 'Quota 1,85 sul match', {
    match: { type: 'contains', value: 'Quota' },
    columns: { ...soloEmpty,
      EventName: { source: 'constant', value: 'X' },
      Price: { source: 'regex', pattern: '(?:@|quota)\\s*([0-9]+[.,][0-9]+)', group: 1,
               transforms: [{ op: 'comma_to_dot' }] } },
  });
  aggiungi('sorgente line whole', 'testo\nEVENTO: Roma - Lazio\nfine', {
    match: { type: 'contains', value: 'EVENTO' },
    columns: { ...soloEmpty,
      Provider: { source: 'constant', value: 'XTrader' },
      EventName: { source: 'line', anchor: 'evento', part: 'whole',
                   transforms: [{ op: 'trim' }] } },
  });
  aggiungi('sorgente line con marcatore assente nella riga → vuoto poi trasformato',
    'testo\nEVENTO Roma - Lazio\nfine', {
    match: { type: 'contains', value: 'EVENTO' },
    columns: { ...soloEmpty,
      Provider: { source: 'constant', value: 'XTrader' },
      EventName: { source: 'line', anchor: 'evento', part: 'after', marker: 'XX',
                   transforms: [{ op: 'upper' }] } },
  });
  aggiungi('trasformazioni: digits_only, upper, lower, replace_all',
    'Provider: bet365\nvalore = ab-cd-ef\nnumero = tot 12.5x', {
    match: { type: 'contains', value: 'Provider' },
    columns: { ...soloEmpty,
      Provider: { source: 'line', anchor: 'provider', part: 'after', marker: 'provider:',
                  transforms: [{ op: 'trim' }, { op: 'upper' }] },
      EventName: { source: 'line', anchor: 'valore', part: 'after', marker: '=',
                   transforms: [{ op: 'trim' }, { op: 'replace_all', from: '-', to: ' ' }] },
      Points: { source: 'line', anchor: 'numero', part: 'whole',
                transforms: [{ op: 'digits_only' }] } },
  });
  // I separatori decimali si cambiano SOLO alla prima occorrenza in JS
  // (`String.replace` con argomento stringa): un valore con due virgole stana la
  // differenza con lo `str.replace` di Python, che di default le cambia tutte.
  aggiungi('trasformazioni: comma_to_dot tocca solo la PRIMA virgola', 'x', {
    match: { type: 'contains', value: 'x' },
    columns: { ...soloEmpty,
      Provider: { source: 'constant', value: 'XTrader' },
      EventName: { source: 'constant', value: '1,2,3',
                   transforms: [{ op: 'comma_to_dot' }] } },
  });
  aggiungi('trasformazioni: dot_to_comma tocca solo il PRIMO punto', 'x', {
    match: { type: 'contains', value: 'x' },
    columns: { ...soloEmpty,
      Provider: { source: 'constant', value: 'XTrader' },
      EventName: { source: 'constant', value: '1.2.3',
                   transforms: [{ op: 'dot_to_comma' }] } },
  });
  aggiungi('condizione regex', `P.Bet LIVE\n${VS} A v B`, {
    match: { type: 'regex', value: 'p\\.?bet' },
    columns: { ...soloEmpty,
      Provider: { source: 'constant', value: 'XTrader' },
      EventName: { source: 'line', anchor: VS, part: 'after', marker: VS,
                   transforms: [{ op: 'replace_last', from: ' v ', to: ' - ' }, { op: 'trim' }] } },
  });
  aggiungi('condizione assente → non riconosciuto', 'qualunque cosa', {
    match: null, columns: soloEmpty,
  });
  aggiungi('regex che non compila → colonna vuota, nessun errore', 'testo', {
    match: { type: 'contains', value: 'testo' },
    columns: { ...soloEmpty,
      Provider: { source: 'constant', value: 'XTrader' },
      EventName: { source: 'constant', value: 'X' },
      Points: { source: 'regex', pattern: '([', group: 1 } },
  });
  return casi;
}

caso('motore: casi di confronto per il gemello Python', () => casiConfronto());

process.stdout.write(JSON.stringify(casi, null, 1));
process.exit(casi.every(c => c.ok) ? 0 : 1);
