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

// Il valore ASSOLUTO dei flag, non solo la parita' col gemello: il confronto
// JS/Python vede solo le DIVERGENZE, quindi un cambio di comportamento
// COORDINATO (entrambi i motori che passano a case-insensitive) gli
// sfuggirebbe. Qui si pinna la case sul lato JS; il gemello e'
// `test_flag_regex_...` in Python. Pattern con maiuscole/minuscole distinte per
// distinguere case-sensitive da insensitive (audit #81 E2, bloccante Fable 5 #85).
caso('estrazione: i flag scartati NON cambiano la case (x/gy restano sensitive)', () => {
  const ex = (flags) => extractValue('xyzABCabc',
    { source: 'regex', pattern: '(abc)', flags, group: 1 });
  // flag ASSENTI -> default 'i' (case-insensitive): prima occorrenza = 'ABC'.
  eq(ex(undefined), 'ABC', 'niente flag: default i');
  eq(ex(''), 'ABC', 'flag vuoti: default i');
  // flag PRESENTI ma scartati -> case-SENSITIVE (niente default 'i'): 'abc'.
  eq(ex('x'), 'abc', 'x scartato ma la case resta sensitive');
  eq(ex('gy'), 'abc', 'gy scartati ma la case resta sensitive');
  // flag comuni onorati.
  eq(ex('i'), 'ABC', 'i onorato');
  eq(ex('iiu'), 'ABC', 'iiu deduplicato e onorato');
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

caso('suggeritore: sul messaggio reale propone EventName e Provider vuota', () => {
  const cfg = suggestConfig(MSG_VALIDO);
  // Provider VUOTA dalla #42: il vecchio default 'XTrader' era il valore del
  // CSV misurato in #5, che vale li' perche' quel file l'ha scritto XTrader.
  eq(cfg.columns.Provider.source, 'empty', 'Provider');
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
  // Nome distinto dall'array `casi` a livello di modulo (in cui `caso` inserisce):
  // ombreggiarlo funzionava, ma una modifica futura qui dentro rischierebbe l-array
  // sbagliato. Segnalato da CodeRabbit sulla PR #28.
  const confronti = [];
  // `aliasMap` e' il quarto argomento OPZIONALE (#34 pezzo 3): quando c'e',
  // il gemello Python riceve la stessa mappa e deve produrre lo stesso
  // EventName tradotto e gli stessi `avvisi`.
  const aggiungi = (nome, message, config, aliasMap) =>
    confronti.push({ nome, message, config, aliasMap,
                     atteso: runParser(message, config, aliasMap) });

  const prod = configProduzione();
  // --- Sorgente squadre (#34 pezzo 3): la traduzione deve essere identica ---
  aggiungi('sorgente: entrambe le squadre tradotte', MSG_VALIDO, prod,
    { 'Manchester City': 'Man City FC', 'Aston Villa': 'Aston Villa FC' });
  aggiungi('sorgente: squadra sconosciuta → verbatim + stesso avviso',
    MSG_VALIDO, prod, { 'Manchester City': 'Man City FC' });
  aggiungi('sorgente: " v " dentro un nome squadra, mappa con identita\'',
    `P.Bet. PREMACHT 0,5HT\n${VS} Man v City v Napoli\n@ 1.90`, prod,
    { 'Man v City': 'Manchester City', Napoli: 'SSC Napoli' });
  aggiungi('sorgente: mappa vuota → tutto verbatim con due avvisi',
    MSG_VALIDO, prod, {});
  // Chiavi che in JS vivono anche sul prototype: costruite con JSON.parse,
  // che crea proprieta' PROPRIE anche per __proto__ — un literal {__proto__}
  // imposterebbe il prototipo e la chiave sparirebbe (Sol, PR #67).
  aggiungi('sorgente: chiavi da prototype (__proto__, toString) non ingannano',
    MSG_VALIDO, prod,
    JSON.parse('{"__proto__":"Ghost","toString":"X",'
      + '"Manchester City":"Man City","Aston Villa":"Villa"}'));
  // --- Multi-riga (#35 pezzo 2): le N righe devono uscire IDENTICHE ---------
  const vuoteMulti = {};
  for (const c of COLUMNS) vuoteMulti[c] = { source: 'empty' };
  const multiBase = {
    match: { type: 'contains', value: 'P.Bet.' },
    columns: { ...vuoteMulti,
      EventName: { source: 'line', anchor: ' v ', part: 'whole',
        transforms: [{ op: 'replace_last', from: ' v ', to: ' - ' }, { op: 'trim' }] },
      MarketType: { source: 'constant', value: 'OVER_UNDER_15' },
      SelectionName: { source: 'constant', value: 'Over 1,5' },
      BetType: { source: 'constant', value: 'PUNTA' },
      Price: { source: 'constant', value: '1.85' } },
  };
  const MSG_MULTI = 'P.Bet.\nJuve v Milan\nRisultati: 1-0 2-1; @ 1.85';
  aggiungi('multi: somma con eredita e bet_type per riga', MSG_MULTI, {
    ...multiBase, multi: {
      markets: [
        { market_type: 'OVER_UNDER_25', selection_name: 'Over 2,5' },
        { enabled: false, market_type: 'MATCH_ODDS', selection_name: 'Mai' }],
      selections: [{ selection_name: 'Under 1,5', bet_type: 'BANCA' }] },
  });
  aggiungi('multi: la riga rotta non ferma le altre (k su N)', MSG_MULTI, {
    ...multiBase, multi: { markets: [
      { market_type: 'OVER_UNDER_25', selection_name: 'Over 2,5' },
      { market_type: 'OVER_UNDER_05', selection_name: 'Over 0,5', price: 'abc' }],
      selections: [] },
  });
  aggiungi('multi: punteggi dinamici sul mercato dei risultati', MSG_MULTI, {
    ...multiBase, multi: { markets: [
      { market_type: 'CORRECT_SCORE', selection_name: '',
        start_after: 'Risultati:', end_before: ';' }], selections: [] },
  });
  aggiungi('multi: selezione vuota fuori dai mercati punteggio', MSG_MULTI, {
    ...multiBase, multi: { markets: [
      { market_type: 'OVER_UNDER_25', selection_name: '',
        start_after: 'Risultati:', end_before: ';' }], selections: [] },
  });
  aggiungi('multi: gate #41 sulla riga generata da base tutta costante',
    'P.Bet.\nRisultati: 1-0; @ 1.85', {
    ...multiBase, columns: { ...vuoteMulti,
      EventName: { source: 'constant', value: 'Juve - Milan' },
      MarketType: { source: 'constant', value: 'OVER_UNDER_15' },
      SelectionName: { source: 'constant', value: 'Over 1,5' },
      BetType: { source: 'constant', value: 'PUNTA' } },
    multi: { markets: [
      { market_type: 'OVER_UNDER_25', selection_name: 'Over 2,5' }],
      selections: [] },
  });
  aggiungi('multi: gate #41 quando la riga sovrascrive l\'unica estratta',
    'P.Bet.\nOver 1,5\n@ 1.85', {
    ...multiBase, columns: { ...vuoteMulti,
      EventName: { source: 'constant', value: 'Juve - Milan' },
      MarketType: { source: 'constant', value: 'OVER_UNDER_15' },
      SelectionName: { source: 'regex', pattern: '(Over [0-9],[0-9])', group: 1 },
      BetType: { source: 'constant', value: 'PUNTA' } },
    multi: { markets: [
      { market_type: 'OVER_UNDER_25', selection_name: 'Over 9,9' }],
      selections: [{ bet_type: 'BANCA' }] },
  });
  aggiungi('multi: markets non-lista = nessuna riga di override, nei due motori',
    MSG_MULTI, {
    ...multiBase, multi: { markets: { a: 1 }, selections: [
      { selection_name: 'Under 1,5', bet_type: 'BANCA' }] },
  });
  aggiungi('multi: la riga vuota non genera, in NESSUNO dei due motori',
    MSG_MULTI, {
    ...multiBase, multi: { markets: [
      {}, { market_type: 'OVER_UNDER_25', selection_name: 'Over 2,5' }],
      selections: [] },
  });
  aggiungi('multi: troppi punteggi = stesso scarto del tetto nei due motori',
    'P.Bet.\nJuve v Milan\nRisultati: '
      + Array.from({ length: 40 }, (_, i) => `${i}-${i}`).join(' ') + '; fine', {
    ...multiBase, multi: { markets: [
      { market_type: 'CORRECT_SCORE', selection_name: '',
        start_after: 'Risultati:', end_before: ';' }], selections: [] },
  });
  aggiungi('multi: le cifre unicode NON sono punteggi, in nessuno dei due',
    'P.Bet.\nJuve v Milan\nRisultati: ١-٢ 2-1; fine', {
    ...multiBase, multi: { markets: [
      { market_type: 'CORRECT_SCORE', selection_name: '',
        start_after: 'Risultati:', end_before: ';' }], selections: [] },
  });
  aggiungi('multi: delimitatore NUMERICO canonicalizzato come in Python',
    'P.Bet.\nJuve v Milan\nquota5 2.10; x', {
    ...multiBase, multi: { markets: [
      { market_type: 'OVER_UNDER_25', selection_name: 'Over 2,5',
        start_after: 5, end_before: ';' }], selections: [] },
  });
  aggiungi('multi: delimitatori CON selezione estraggono la quota',
    'P.Bet.\nJuve v Milan\nquota: 2.10 fine', {
    ...multiBase, multi: { markets: [
      { market_type: 'OVER_UNDER_25', selection_name: 'Over 2,5',
        start_after: 'quota:', end_before: 'fine' }], selections: [] },
  });
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
  // Una obbligatoria valorizzata con la COSTANTE 0 (JSON valido). In JS
  // `String(0 ?? '')` e' '0' → colonna PRESENTE; con lo `str(0 or '')` di Python
  // sarebbe '' → colonna MANCANTE, e i due motori divergerebbero su `missing` e
  // `complete`. E' la divergenza che il gemello Python deve replicare: la riga
  // resta `0` in entrambi, e il valore obbligatorio non e' vuoto. Il motore Python
  // usa quindi `?? ''` (solo None/undefined), non `or ''`. CodeRabbit, PR #28.
  aggiungi('obbligatoria = costante 0 → PRESENTE, non mancante (0 e- valorizzato)', 'x', {
    match: { type: 'contains', value: 'x' },
    columns: { ...soloEmpty,
      EventName: { source: 'constant', value: 0 },
      MarketType: { source: 'constant', value: 'OVER_UNDER_15' },
      SelectionName: { source: 'constant', value: 'Over 1,5' },
      BetType: { source: 'constant', value: 'PUNTA' } },
  });
  // --- Guardie sui valori estratti (#39) e sulla config (#41), PR 5 ---------
  //
  // I casi vivono qui perche' l'output JS e' l'ORACOLO del gemello Python: se le
  // due implementazioni divergessero su un tetto o su un motivo, l'utente
  // vedrebbe «completo» nel browser e feed scartato in produzione — o il
  // contrario, che e' peggio.
  const conNumeri = (colonna, valore) => ({
    match: { type: 'contains', value: 'P.Bet.' },
    columns: { ...soloEmpty,
      EventName: { source: 'line', contains: 'P.Bet.' },
      MarketType: { source: 'constant', value: 'OVER_UNDER_15' },
      SelectionName: { source: 'constant', value: 'Over 1,5 goal' },
      BetType: { source: 'constant', value: 'PUNTA' },
      [colonna]: { source: 'constant', value: valore } },
  });
  const MSG = 'P.Bet. Juventus - Palermo';
  aggiungi('guardie: Price vuota → PASSA (la quota la mette XTrader)',
    MSG, conNumeri('Price', ''));
  aggiungi('guardie: Price col separatore migliaia → scartato',
    MSG, conNumeri('Price', '1.000.000'));
  aggiungi('guardie: Price sotto la scala Betfair → fuori intervallo',
    MSG, conNumeri('Price', '0.5'));
  aggiungi('guardie: Price non numerica → motivo distinto',
    MSG, conNumeri('Price', 'abc'));
  aggiungi('guardie: Price in cifre arabo-indiane → scartata (solo ASCII)',
    MSG, conNumeri('Price', '\u0661\u0669'));
  aggiungi('guardie: Handicap patologico → scartato', MSG, conNumeri('Handicap', '999999'));
  aggiungi('guardie: Handicap 0 e -1.5 → passano', MSG, conNumeri('Handicap', '-1.5'));
  aggiungi('guardie: Points moltiplicatore assurdo → scartato',
    MSG, conNumeri('Points', '1.000.000'));
  aggiungi('guardie: Points non finito (400 cifre) → caso a se',
    MSG, conNumeri('Points', '9'.repeat(400)));
  aggiungi('guardie: Points 2 → passa', MSG, conNumeri('Points', '2'));
  // Valori JSON NON stringa: `String()` e `str()` non concordano (`true` contro
  // `True`, `1` contro `1.0`), e il motivo mostrato citerebbe due valori diversi
  // nei due motori. Il verdetto sarebbe lo stesso, la diagnosi no.
  aggiungi('guardie: Price = true (JSON) → stesso motivo nei due motori',
    MSG, conNumeri('Price', true));
  aggiungi('guardie: Price = 1000000 (JSON numero) → stesso motivo',
    MSG, conNumeri('Price', 1000000));
  aggiungi('guardie: Points = 0.5 (JSON numero) → passa in entrambi',
    MSG, conNumeri('Points', 0.5));
  // Il confine dell'esponenziale: JS scrive le cifre per esteso fino a 1e21
  // ESCLUSO e passa a `1e+21` da li'. Python fa il contrario su entrambi i lati
  // se lasciato a se stesso, quindi i due casi vincolano il confine.
  aggiungi('guardie: Price = 1e20 (sotto il confine) → cifre per esteso in entrambi',
    MSG, conNumeri('Price', 1e20));
  aggiungi('guardie: Price = 1e21 (sopra il confine) → esponenziale in entrambi',
    MSG, conNumeri('Price', 1e21));
  aggiungi('guardie: valore lunghissimo → citato TAGLIATO uguale nei due motori',
    MSG, conNumeri('Points', '9'.repeat(400)));
  aggiungi('guardie: valore multilinea → citato SENZA a capo, uguale nei due motori',
    MSG, conNumeri('Price', 'prima riga\nseconda\triga\n\nterza'));
  // Emoji astrali oltre il 60esimo carattere: `slice` di JS conta unita' UTF-16 e
  // spezzerebbe la coppia surrogata, lo slice di Python conta codepoint. Il caso
  // vincola che i due motori citino la STESSA stringa.
  aggiungi('guardie: emoji astrali al confine del taglio → stesso citato',
    MSG, conNumeri('Price', 'x'.repeat(55) + '\u{1F19A}\u{1F19A}\u{1F19A}\u{1F19A}\u{1F19A}'));
  // I tre gruppi di spazi su cui i default dei due linguaggi NON coincidono:
  // `\x1c-\x1f` e `\x85` li normalizza solo Python, `\ufeff` solo JavaScript.
  // Con la classe esplicita condivisa i due motori citano la stessa stringa.
  aggiungi('guardie: separatori di controllo → citato uguale nei due motori',
    MSG, conNumeri('Price', 'a\u001cb\u001dc\u001ed\u001fe\u0085f'));
  aggiungi('guardie: BOM dentro il valore → citato uguale nei due motori',
    MSG, conNumeri('Price', 'a\ufeffb\u00a0c\u2028d'));
  // I default di `strip()`/`trim()` divergevano anche sul VERDETTO, non solo sul
  // citato: `'\ufeff2'` passava in JS (il `trim` toglie il BOM) ed era «non
  // numerico» in Python — anteprima verde nel browser, feed vuoto in
  // produzione. Con `'\x1c2'` i ruoli si invertono. Il verdetto corre sul
  // valore normalizzato dalla classe condivisa, in entrambi i motori.
  // [REAL_FINDING] di Claude Fable 5 e GPT-5.6 Sol al gate finale della PR #47.
  aggiungi('guardie: BOM ai bordi del valore → stesso verdetto nei due motori',
    MSG, conNumeri('Price', '\ufeff2'));
  aggiungi('guardie: separatore di controllo ai bordi → stesso verdetto',
    MSG, conNumeri('Price', '\u001c2'));
  aggiungi('guardie: valore di soli spazi uniformi → vuoto in entrambi',
    MSG, conNumeri('Price', '\ufeff\u00a0'));
  aggiungi('guardie: spazio uniforme DENTRO il numero → scartato in entrambi',
    MSG, conNumeri('Price', '1\u00a05'));
  // L'emptiness delle obbligatorie aveva la stessa coppia divergente: una
  // SelectionName di solo BOM era «mancante» in JS e «valorizzata» in Python.
  aggiungi('guardie: obbligatoria di solo BOM → mancante in entrambi i motori',
    MSG, conNumeri('SelectionName', '\ufeff'));
  // E la trasformazione `trim` e' la stessa coppia sul VALORE estratto, cioe'
  // sui byte della riga CSV: i due motori devono produrre la stessa riga.
  aggiungi('guardie: trasformazione trim con spazi esotici ai bordi → stessa riga',
    MSG, { ...conNumeri('Price', ''),
           columns: { ...conNumeri('Price', '').columns,
                      Price: { source: 'constant', value: '\ufeff1.9\u001c',
                               transforms: [{ op: 'trim' }] } } });
  // Condizione NON soddisfatta → NESSUNO scarto, in nessuno dei due motori: o il
  // dispatch loggherebbe «scartato» un messaggio mai riconosciuto, attribuendolo
  // a un parser che non c'entra e conservando testo estraneo in `message_logs`.
  // [REAL_FINDING] di GPT-5.6 Sol al gate finale della PR #47.
  aggiungi('guardie: condizione non soddisfatta → nessuno scarto',
    'oggi si parla di altro', conNumeri('Points', '999999'));
  // Le SOGLIE dell'esponenziale divergono anche sui float NON interi, non
  // solo sugli interi gia' vincolati da 1e20/1e21: `str()` di Python passa
  // all'esponenziale sotto 1e-4 (`0.000001` -> `1e-06`, scartato come non
  // numerico), `String()` di JS solo sotto 1e-6 (`0.000001`, accettato) --
  // e dove entrambi scrivono l'esponenziale, il FORMATO diverge (`1e-07`
  // contro `1e-7`). [REAL_FINDING] di GPT-5.6 Sol al gate finale, PR #47.
  aggiungi('guardie: Points = 0.000001 (JSON) -> stesso verdetto nei due motori',
    MSG, conNumeri('Points', 0.000001));
  aggiungi('guardie: Points = 0.00001 (JSON) -> stesso verdetto',
    MSG, conNumeri('Points', 0.00001));
  aggiungi('guardie: Points = 1e-7 (JSON) -> stesso motivo, stesso formato esponente',
    MSG, conNumeri('Points', 1e-7));
  aggiungi('guardie: Price = 123.456 (JSON float) -> stessa riga in entrambi',
    MSG, conNumeri('Price', 123.456));
  // Il confine di scrittura (#40): per XTrader il separatore decimale e' la
  // VIRGOLA (guida ufficiale p.169, il Bridge in produzione, conferma del
  // proprietario). I valori numerici accettati escono localizzati in ENTRAMBI
  // i motori; le trasformazioni dell'utente restano davanti, e chi ha gia'
  // `comma_to_dot` produce lo stesso feed di chi non ce l'ha.
  const conQuota = (regola) => ({
    match: { type: 'contains', value: 'P.Bet.' },
    columns: { ...soloEmpty,
      EventName: { source: 'line', contains: 'P.Bet.' },
      MarketType: { source: 'constant', value: 'OVER_UNDER_15' },
      SelectionName: { source: 'constant', value: 'Over 1,5 goal' },
      BetType: { source: 'constant', value: 'PUNTA' },
      Price: regola },
  });
  const REGOLA_QUOTA = { source: 'regex',
    pattern: 'quota\\s*([0-9]+[.,][0-9]+)', group: 1 };
  aggiungi('localizzazione: quota col PUNTO nel messaggio -> virgola nella riga',
    'P.Bet. Juventus - Palermo\nquota 1.85', conQuota(REGOLA_QUOTA));
  aggiungi('localizzazione: quota con la VIRGOLA resta con la virgola',
    'P.Bet. Juventus - Palermo\nquota 1,85', conQuota(REGOLA_QUOTA));
  aggiungi('localizzazione: comma_to_dot preesistente -> stessa riga',
    'P.Bet. Juventus - Palermo\nquota 1,85',
    conQuota({ ...REGOLA_QUOTA, transforms: [{ op: 'comma_to_dot' }] }));
  aggiungi('localizzazione: Handicap e Points seguono la stessa regola',
    MSG, conNumeri('Handicap', '-1.5'));
  aggiungi('localizzazione: valore RIFIUTATO resta in forma giudicata',
    MSG, conNumeri('Price', '1.000.000'));
  // Niente emoji nei VALORI (#42): XTrader marcherebbe il segnale non valido,
  // senza errore di ritorno. Il caso reale e' la regola 'riga intera' su una
  // riga che comincia col marcatore: il valore si porta dentro l'emoji e il
  // feed esce formalmente valido. Si scarta (regola della #39), con parita'
  // JS/Python sul motivo. I nomi normali (accenti, virgole, ' v ') passano.
  aggiungi('emoji: riga intera col marcatore dentro -> scartato in entrambi',
    'P.Bet. LIVE\n\u{1F19A} Juventus - Palermo', {
      match: { type: 'contains', value: 'P.Bet.' },
      columns: { ...soloEmpty,
        EventName: { source: 'line', anchor: '\u{1F19A}' },
        MarketType: { source: 'constant', value: 'OVER_UNDER_15' },
        SelectionName: { source: 'constant', value: 'Over' },
        BetType: { source: 'constant', value: 'PUNTA' } },
    });
  aggiungi('emoji: spunta in un valore facoltativo -> scartato in entrambi',
    MSG, conNumeri('SelectionName', 'Over \u2705 1,5'));
  aggiungi('emoji: orologio e stella -> stessa classe nei due motori',
    MSG, conNumeri('MarketName', 'ore \u23F0 20:45 \u2B50'));
  // Emoji in una colonna NUMERICA: scatta la guardia numerica («non un
  // numero»), NON quella emoji — il contratto della delega, chiesto da
  // Sourcery sulla PR #49, vincolato dal confronto dei motivi fra i motori.
  aggiungi('emoji: dentro una colonna numerica decide la guardia NUMERICA',
    MSG, conNumeri('Handicap', '-1.5\u{1F4A9}'));
  // Il keycap U+20E3 senza FE0F (forma minimamente qualificata) era il buco
  // della classe: esiste SOLO per le sequenze emoji. I simboli text-default
  // ((c), TM, !!) restano testo: la loro forma emoji richiede FE0F, gia'
  // intercettato. [REAL_FINDING] di GPT-5.6 Sol al gate della PR #49.
  aggiungi('emoji: keycap minimale U+20E3 -> scartato in entrambi',
    MSG, conNumeri('MarketName', 'posizione 1\u20e3'));
  aggiungi('emoji: simboli text-default (c) TM !! -> NESSUNO scarto',
    MSG, conNumeri('MarketName', 'marchio \u00ae nota\u203c brand \u2122'));
  aggiungi('emoji: nomi con accenti, virgole e v -> NESSUNO scarto',
    MSG, conNumeri('MarketName', 'Citta\u0300 "A", U\u0308ber v Lo\u0301v'));
  aggiungi('guardie: sole costanti sulle obbligatorie → nessuna riga', 'ciao a tutti', {
    match: { type: 'contains', value: 'a' },
    columns: { ...soloEmpty,
      EventName: { source: 'constant', value: 'X' },
      MarketType: { source: 'constant', value: 'OVER_UNDER_15' },
      SelectionName: { source: 'constant', value: 'Over' },
      BetType: { source: 'constant', value: 'PUNTA' } },
  });
  // --- Parita' dei due motori: replace_all e flag regex (audit #81 E1/E2) -----
  //
  // Config a quattro obbligatorie costanti + il caso in una colonna facoltativa
  // (MarketName), cosi' `complete` e' true e la `row` porta il valore da
  // confrontare direttamente. L'oracolo e' `runParser`: il gemello Python deve
  // produrre la stessa `row`.
  const conMercato = (regola, message) => [message, {
    match: { type: 'contains', value: 'P.Bet.' },
    columns: { ...soloEmpty,
      EventName: { source: 'constant', value: 'Juve - Milan' },
      MarketType: { source: 'constant', value: 'OVER_UNDER_15' },
      SelectionName: { source: 'constant', value: 'Over' },
      BetType: { source: 'constant', value: 'PUNTA' },
      MarketName: regola },
  }];
  // E1 (P1): `replace_all` con `from` vuoto. In JS `''.split('')` esplode il
  // valore carattere per carattere e vi INTERCALA `to` ("abc" -> "aXbXc"); il
  // gemello Python lo tratta gia' come no-op (`if t.get('from') else v`). Senza
  // il guard in JS le due `row` divergono. Misurato: JS "aXbXc", Python "abc".
  aggiungi('E1: replace_all con from VUOTO e- no-op nei due motori',
    ...conMercato({ source: 'constant', value: 'abc',
      transforms: [{ op: 'replace_all', from: '', to: 'X' }] }, 'P.Bet.'));
  // E2 (P3): flag `x` (verbose). JS lo passa a `new RegExp` che SOLLEVA
  // ("Invalid flags") -> l'estrazione cade a '' ; Python lo mappa su `regex.X`
  // e la modalita' verbose ignora gli spazi del pattern, quindi combacia. Il
  // pattern chiede spazi che il messaggio non ha: con `x` onorato solo da un
  // lato, i due motori divergono. Misurato: JS '', Python "123".
  aggiungi('E2: flag x (verbose) non onorato in nessuno dei due',
    ...conMercato({ source: 'regex', pattern: '( [0-9]+ )', flags: 'x', group: 1 },
      'P.Bet. val123end'));
  // E2 (P3): flag `y` (sticky). JS ancora il match all'indice 0 e non trova la
  // cifra dopo "P.Bet. " -> '' ; Python ignora `y` e trova "123". Misurato: JS
  // '', Python "123".
  aggiungi('E2: flag y (sticky) non onorato in nessuno dei due',
    ...conMercato({ source: 'regex', pattern: '([0-9]+)', flags: 'y', group: 1 },
      'P.Bet. abc123'));
  // E2 CONTROLLO: `u` (unicode) e' l'unico flag oltre {i,m,s} che i due motori
  // devono continuare a onorare IDENTICO — Python e' gia' codepoint-native e
  // JS ha bisogno di `u` per far combaciare `.` su un carattere astrale. Verde
  // prima e dopo la patch: e' la guardia che vieta di "normalizzare via" anche
  // `u`, che RIAPRIREBBE la divergenza sui codepoint astrali. Misurato: "🆚"
  // in entrambi.
  aggiungi('E2: flag u (unicode) onorato IDENTICO nei due motori',
    ...conMercato({ source: 'regex', pattern: '(.)', flags: 'u', group: 1 },
      'P.Bet. \u{1F19A}X'));
  // E2, flag DUPLICATI: `new RegExp(_, 'ii')` in JS SOLLEVA "Invalid flags", ed
  // e' il motivo per cui `flagRegex` deduplica con un Set. Senza la dedup questo
  // caso cadrebbe a '' in JS e divergerebbe da Python. Con `iiu` -> `iu` in JS e
  // `I` in Python: entrambi case-insensitive, "ABC" combacia. Chiesto da GPT-5.5.
  aggiungi('E2: flag duplicati (iiu) deduplicati, non un errore',
    ...conMercato({ source: 'regex', pattern: '(ABC)', flags: 'iiu', group: 1 },
      'P.Bet. xyzABCabc'));
  // E2, flag DUPLICATI PURI: `new RegExp(_, 'ii')`/`'uu'` in JS SOLLEVANO
  // "Invalid flags", ma `flagRegex` deduplica col Set PRIMA di `new RegExp`, e
  // Python li combina; entrambi danno lo stesso valore. GPT-5.6 Sol (PR #87) li
  // temeva divergenti: qui si misura che NON lo sono. `ii`/`imsi` -> `i`
  // (case-insensitive) -> "ABC"; `uu` -> nessun bit -> case-sensitive -> "abc".
  aggiungi('E2: flag duplicato i (ii) uguale a i nei due motori',
    ...conMercato({ source: 'regex', pattern: '(abc)', flags: 'ii', group: 1 },
      'P.Bet. ABCabc'));
  aggiungi('E2: flag duplicato u (uu) uguale a u (case-sensitive) nei due motori',
    ...conMercato({ source: 'regex', pattern: '(abc)', flags: 'uu', group: 1 },
      'P.Bet. ABCabc'));
  aggiungi('E2: flag misto duplicato (imsi) uguale a ims nei due motori',
    ...conMercato({ source: 'regex', pattern: '(abc)', flags: 'imsi', group: 1 },
      'P.Bet. ABCabc'));
  // E2, flag MISTI validi+scartati: `xiu` tiene `iu` (case-insensitive unicode)
  // e butta `x`. I due motori devono estrarre lo stesso valore. Chiesto da
  // GPT-5.5 come guardia dell'allineamento reale anteprima/feed.
  aggiungi('E2: flag misti (xiu) tengono solo i validi comuni',
    ...conMercato({ source: 'regex', pattern: '(ABC)', flags: 'xiu', group: 1 },
      'P.Bet. xyzABCabc'));
  // E2, flag NON-STRINGA (config_json non attendibile): una regola con `flags`
  // non-stringa e' MALFORMATA → colonna VUOTA (fail-closed) in ENTRAMBI, come un
  // pattern che non compila. Cosi': niente `TypeError` (Python su `main` faceva
  // `for f in 5` → crash, Fable), niente fail-open (un config malformato non
  // produce piu' un segnale che prima non usciva, Sol), niente dipendenza dalla
  // forma della coercizione a stringa (che diverge fra `str()` e `String()`,
  // Sol). Qui MarketName e' facoltativa, quindi la riga resta completa ma con la
  // colonna vuota; l'oracolo e' `runParser` e Python deve dare la stessa riga.
  aggiungi('E2: flag non-stringa (numero) → colonna vuota, non solleva',
    ...conMercato({ source: 'regex', pattern: '(abc)', flags: 5, group: 1 },
      'P.Bet. abcABC'));
  aggiungi('E2: flag non-stringa (lista di oggetti) → colonna vuota',
    ...conMercato({ source: 'regex', pattern: '(abc)', flags: [{ a: 1 }], group: 1 },
      'P.Bet. abcABC'));
  aggiungi('E2: flag false (non-stringa) → colonna vuota in entrambi',
    ...conMercato({ source: 'regex', pattern: '(a.b)', flags: false, group: 1 },
      'P.Bet. a\nb'));
  aggiungi('E2: flag true (non-stringa) → colonna vuota in entrambi',
    ...conMercato({ source: 'regex', pattern: '(abc)', flags: true, group: 1 },
      'P.Bet. ABCabc'));
  // Un OGGETTO come flags: era la divergenza residua (Python raccoglieva la `i`
  // della chiave, JS no). Malformato → colonna vuota in entrambi, niente divergenza.
  aggiungi('E2: flag oggetto {i:1} (non-stringa) → colonna vuota, niente divergenza',
    ...conMercato({ source: 'regex', pattern: '(abc)', flags: { i: 1 }, group: 1 },
      'P.Bet. ABCabc'));
  return confronti;
}

// La classe degli spazi vive in DUE file e non si puo' condividere: qui si
// esporta, codepoint per codepoint, cosa normalizza il motore JS, e il gemello
// Python confronta con la propria. Senza, una modifica a una sola delle due
// copie resterebbe invisibile finche' un cliente non vede due motivi diversi.
caso('motore: quali codepoint sono spazio per il motore JS', () => {
  const codici = [0x20, 0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x1c, 0x1d, 0x1e, 0x1f,
                  0x85, 0xa0, 0x1680, 0x2000, 0x2009, 0x200a, 0x2028, 0x2029,
                  0x202f, 0x205f, 0x3000, 0x200b, 0xfeff, 0x41, 0x2d];
  // Si misura il motore VERO attraverso `numericReason`, non una copia della
  // classe tenuta qui: una copia misurerebbe se stessa, e la guardia sarebbe
  // circolare. Il valore non e' numerico, quindi il motivo cita il valore
  // normalizzato, ed e' li' che si legge se il carattere e' diventato spazio.
  return codici.map(c => ({
    codepoint: c,
    spazio: (E.numericReason('Price', 'a' + String.fromCodePoint(c) + 'b') || '')
      .includes('«a b»'),
  }));
});

// Divergenze note fra i due motori (Issue #88), esportate perche' il gemello
// Python le confronti: la condizione `match` IGNORA i flag (come `_regex.I`
// cablato in Python), e `\w`/`\d` in JS sono ASCII (in Python unicode).
caso('divergenze #88: match ignora flags, e le classi \\w/\\d in JS sono ASCII', () => {
  const m = (flags) => E.matches('PBET LIVE', { type: 'regex', value: 'pbet', flags });
  const senza = E.matches('PBET LIVE', { type: 'regex', value: 'pbet' });
  return {
    // match con qualunque flag == match senza flag (i cablato): flag ignorati.
    matchIgnoraFlags: ['x', 'ims', 'u', 'gy'].every(f => m(f) === senza) && senza === true,
    // \w ASCII in JS: su 'café' prende 'caf' (Python prende 'café').
    wSuCafe: E.extractValue('café', { source: 'regex', pattern: '(\\w+)', group: 1 }),
    // \d ASCII in JS: le cifre arabo-indiane non sono \d (Python si').
    dSuArabo: E.extractValue('numero ٤٢ qui', { source: 'regex', pattern: '(\\d+)', group: 1 }),
  };
});

// La guardia numerica validava il testo CANONICO, ma il CSV del relay
// serializzava il valore Python originale: `Points=0.000001` (JSON) passava
// la guardia e usciva `1e-06` nel feed e `0.000001` nell'anteprima — e un
// booleano usciva `True` contro `true`. Qui si esporta il CSV che scrive il
// motore JS e il gemello Python deve produrre gli STESSI byte dalla stessa
// config. [REAL_FINDING] di GPT-5.6 Sol al gate finale della PR #47.
caso('csv: costanti JSON non stringa nel CSV, come le scrive String()', () => {
  const columns = {}; for (const c of COLUMNS) columns[c] = { source: 'empty' };
  columns.EventName = { source: 'line', contains: 'P.Bet.' };
  columns.MarketType = { source: 'constant', value: 'OVER_UNDER_15' };
  columns.SelectionName = { source: 'constant', value: 'Over' };
  columns.BetType = { source: 'constant', value: 'PUNTA' };
  columns.Points = { source: 'constant', value: 0.000001 };
  columns.EventId = { source: 'constant', value: true };
  const config = { match: { type: 'contains', value: 'P.Bet.' }, columns };
  const message = 'P.Bet. Juventus - Palermo';
  const r = runParser(message, config);
  eq(r.complete, true, 'il caso deve produrre una riga');
  return { config, message, csv: toCsv(r.row) };
});

// La guardia PERDONA gli spazi uniformi ai bordi ai fini del verdetto, ma il
// valore emesso nel CSV deve essere il testo GIUDICATO, non quello grezzo:
// un Price '\ufeff2' passava come numerico e usciva nel feed ancora col BOM
// davanti — XTrader riceve il byte che la guardia aveva perdonato solo ai
// fini del giudizio. [REAL_FINDING] di GPT-5.6 Sol al gate finale, PR #47.
caso('csv: il valore numerico esce nella forma giudicata, senza i bordi', () => {
  const columns = {}; for (const c of COLUMNS) columns[c] = { source: 'empty' };
  columns.EventName = { source: 'line', contains: 'P.Bet.' };
  columns.MarketType = { source: 'constant', value: 'OVER_UNDER_15' };
  columns.SelectionName = { source: 'constant', value: 'Over' };
  columns.BetType = { source: 'constant', value: 'PUNTA' };
  columns.Price = { source: 'constant', value: '\ufeff2\u00a0' };
  const config = { match: { type: 'contains', value: 'P.Bet.' }, columns };
  const message = 'P.Bet. Juventus - Palermo';
  const r = runParser(message, config);
  eq(r.complete, true, 'il valore perdonato ai bordi deve passare');
  const csv = toCsv(r.row);
  eq(csv.includes('"2"'), true, 'il CSV deve contenere il testo giudicato');
  eq(csv.slice(1).includes('\ufeff'), false,
    'nessun BOM oltre quello del contratto in testa al file');
  return { config, message, csv };
});

// Il verificatore vincola la forma localizzata (#40): un punto in un campo
// numerico e' una localizzazione mancata e va respinto, la virgola passa.
caso('verifica: il feed col PUNTO nella quota viene respinto', () => {
  const row = COLUMNS.map(() => '');
  row[COLUMNS.indexOf('Provider')] = 'XTrader';
  row[COLUMNS.indexOf('EventName')] = 'Squadra "A", Citta - Altra';
  row[COLUMNS.indexOf('BetType')] = 'PUNTA';
  row[COLUMNS.indexOf('Price')] = '1.85';
  const conPunto = E.verifyCsv(toCsv(row));
  eq(conPunto === null, false, 'il punto nella quota deve essere respinto');
  eq(String(conPunto).includes('Price'), true, 'il motivo deve nominare la colonna');
  row[COLUMNS.indexOf('Price')] = '1,85';
  eq(E.verifyCsv(toCsv(row)), null,
    'la forma localizzata deve passare, anche con virgole nel nome squadra');
  return 'ok';
});

// Il suggeritore non deve piu' proporre 'XTrader' come Provider (#42):
// Provider e' il nome di CHI MANDA, non di chi legge — nel CSV misurato in #5
// vale 'XTrader' perche' quel file l'ha scritto XTrader. Default: vuota,
// campo dell'utente (XTrader la usa come filtro e discriminante).
caso('suggeritore: Provider proposta VUOTA, campo dell-utente', () => {
  const cfg = suggestConfig('P.Bet. LIVE\n\u{1F19A} Juve v Milan\n@ 1.85');
  eq(cfg.columns.Provider.source, 'empty', 'Provider suggerita');
  return 'ok';
});

// Il verificatore vincola il contratto 'niente emoji in nessuna colonna':
// un campo con l'emoji va respinto, il nome normale passa.
caso('verifica: un campo con l-emoji viene respinto', () => {
  const row = COLUMNS.map(() => '');
  row[COLUMNS.indexOf('EventName')] = '\u{1F19A} Juventus - Palermo';
  row[COLUMNS.indexOf('BetType')] = 'PUNTA';
  const respinto = E.verifyCsv(toCsv(row));
  eq(respinto === null, false, 'l-emoji nel campo deve essere respinta');
  eq(String(respinto).includes('EventName'), true, 'il motivo nomina la colonna');
  row[COLUMNS.indexOf('EventName')] = 'Juventus - Palermo';
  eq(E.verifyCsv(toCsv(row)), null, 'il nome normale passa');
  return 'ok';
});

// La sorgente squadre (#34 pezzo 3, decisioni del proprietario 17/08/2026):
// con la mappa alias→Betfair il motore traduce le due meta' di EventName
// (spezzato sull'ULTIMO ' - '), AVVISA senza bloccare sulla squadra
// sconosciuta (verbatim nel feed), e senza mappa non tocca niente.
caso('sorgente squadre: tradotto, avviso non bloccante, verbatim senza mappa', () => {
  const columns = {}; for (const c of COLUMNS) columns[c] = { source: 'empty' };
  columns.EventName = { source: 'line', anchor: ' v ', part: 'whole',
    transforms: [{ op: 'replace_last', from: ' v ', to: ' - ' }, { op: 'trim' }] };
  columns.MarketType = { source: 'constant', value: 'OVER_UNDER_15' };
  columns.SelectionName = { source: 'constant', value: 'Over 1,5' };
  columns.BetType = { source: 'constant', value: 'PUNTA' };
  const config = { match: { type: 'contains', value: 'P.Bet.' }, columns };
  const msg = 'P.Bet.\nJuve v Milan';
  const i = COLUMNS.indexOf('EventName');

  const piena = runParser(msg, config, { Juve: 'Juventus', Milan: 'AC Milan' });
  eq(piena.row[i], 'Juventus - AC Milan', 'entrambe tradotte');
  eq(piena.avvisi.length, 0, 'nessun avviso quando tutto mappa');
  eq(piena.complete, true, 'la traduzione non blocca');

  const mezza = runParser(msg, config, { Juve: 'Juventus' });
  eq(mezza.row[i], 'Juventus - Milan', 'la sconosciuta resta verbatim');
  eq(mezza.avvisi.length, 1, 'un avviso per la sconosciuta');
  eq(mezza.avvisi[0].includes('Milan'), true, 'l-avviso nomina la squadra');
  eq(mezza.complete, true, 'l-avviso NON blocca: complete resta true');

  const senza = runParser(msg, config);
  eq(senza.row[i], 'Juve - Milan', 'senza mappa: verbatim');
  eq(senza.avvisi.length, 0, 'senza mappa niente avvisi');

  // Un nome squadra che contiene ' v ' non va spezzato: la sostituzione
  // dell'ULTIMO ' v ' e' l'invariante storica, la traduzione la rispetta.
  const interno = runParser('P.Bet.\nManchester v City v Napoli', config,
    { 'Manchester v City': 'Man City', Napoli: 'Napoli' });
  eq(interno.row[i], 'Man City - Napoli', 'il v interno non spezza il nome');
  eq(interno.avvisi.length, 0, 'l-identita' + ' in mappa non produce avvisi');
  return 'ok';
});

// Il feed a N segnali vivi (#35 pezzo 1): il verificatore JS deve accettare
// un documento con piu' data line — come il gemello Python, che lo esporta
// qui per il confronto — e continuare a respingere le righe malformate.
caso('csv: il feed multi-riga (#35) passa il verificatore JS', () => {
  const riga1 = COLUMNS.map(() => '');
  riga1[COLUMNS.indexOf('EventName')] = 'Juventus - Palermo';
  riga1[COLUMNS.indexOf('BetType')] = 'PUNTA';
  const riga2 = [...riga1];
  riga2[COLUMNS.indexOf('MarketType')] = 'OVER_UNDER_25';
  const d1 = toCsv(riga1);
  const d2 = toCsv(riga2);
  // `componiFeed` (#35 pezzo 2): la composizione e' la stessa del relay
  // (`componi_feed`), fonte unica anche in JS — la usa la demo per mostrare
  // gli stessi byte della prova del server.
  const multi = E.componiFeed([d1, d2]);
  eq(multi, d1 + d2.slice(d2.indexOf('\r\n') + 2),
    'primo documento intero, del secondo la sola data line');
  eq(E.componiFeed([d1]), d1, 'un documento passa intatto');
  eq(E.componiFeed([]), headerOnlyCsv(), 'vuoto = la sola intestazione');
  eq(E.verifyCsv(multi), null, 'due segnali vivi devono passare');
  const rotta = multi + 'solo,tre,campi\r\n';
  eq(E.verifyCsv(rotta) === null, false, 'la riga malformata resta respinta');
  return { multi, documenti: [d1, d2] };
});

// Il motore base+override (#35 pezzo 2): un messaggio → N righe. La riga BASE
// e' il modello; ogni riga multi dice SOLO cosa cambia e il resto EREDITA
// (tranello 3: vuoto = eredita, mai azzera). Somma, non prodotto (tranello
// della UI); enabled=false resta salvata e non genera (tranello 2); ogni riga
// e' giudicata DA SOLA e una rotta non ferma le altre (tranello 1).
caso('multi: somma, eredita, enabled, riga rotta isolata, punteggi dinamici', () => {
  const columns = {}; for (const c of COLUMNS) columns[c] = { source: 'empty' };
  columns.EventName = { source: 'line', anchor: ' v ', part: 'whole',
    transforms: [{ op: 'replace_last', from: ' v ', to: ' - ' }, { op: 'trim' }] };
  columns.MarketType = { source: 'constant', value: 'OVER_UNDER_15' };
  columns.SelectionName = { source: 'constant', value: 'Over 1,5' };
  columns.BetType = { source: 'constant', value: 'PUNTA' };
  columns.Price = { source: 'constant', value: '1.85' };
  const base = { match: { type: 'contains', value: 'P.Bet.' }, columns };
  const msg = 'P.Bet.\nJuve v Milan\n@ 1.85';
  const iEv = COLUMNS.indexOf('EventName');
  const iMt = COLUMNS.indexOf('MarketType');
  const iSel = COLUMNS.indexOf('SelectionName');
  const iBt = COLUMNS.indexOf('BetType');
  const iPr = COLUMNS.indexOf('Price');

  // Senza multi: una riga sola, la base — il comportamento storico.
  const solo = runParser(msg, base);
  eq(solo.righe.length, 1, 'senza multi la lista porta la sola base');
  eq(solo.righe[0].complete, true, 'la base resta completa');
  eq(solo.righe[0].row[iSel], 'Over 1,5', 'la base non cambia');

  // 2 mercati + 1 selezione ATTIVI (+1 spenta) = 3 righe, non 4 e non prodotto.
  const config = { ...base, multi: {
    markets: [
      { market_type: 'OVER_UNDER_25', selection_name: 'Over 2,5' },
      { market_type: 'OVER_UNDER_05', selection_name: 'Over 0,5', price: '1.20' },
      { enabled: false, market_type: 'MATCH_ODDS', selection_name: 'Mai' },
    ],
    selections: [
      { selection_name: 'Under 1,5', bet_type: 'BANCA' },
    ],
  } };
  const r = runParser(msg, config, { Juve: 'Juventus', Milan: 'AC Milan' });
  eq(r.righe.length, 3, 'somma: 2 mercati attivi + 1 selezione');
  eq(r.righe.every(x => x.complete), true, 'tutte piazzabili');
  // Eredita: evento (tradotto UNA volta), BetType e Price della base dove non
  // sovrascritti; la riga mercato porta il SUO mercato.
  eq(r.righe[0].row[iEv], 'Juventus - AC Milan', 'l-evento eredita, gia- tradotto');
  eq(r.righe[0].row[iMt], 'OVER_UNDER_25', 'il mercato della riga');
  eq(r.righe[0].row[iPr], '1,85', 'la quota eredita dalla base, localizzata');
  eq(r.righe[1].row[iPr], '1,20', 'la quota della riga vince sulla base');
  // La selezione resta sul mercato BASE e cambia direzione.
  eq(r.righe[2].row[iMt], 'OVER_UNDER_15', 'MultiSelection: mercato della base');
  eq(r.righe[2].row[iSel], 'Under 1,5', 'la selezione della riga');
  eq(r.righe[2].row[iBt], 'BANCA', 'bet_type per riga');

  // Tranello 1: una riga con quota rotta e' SCARTATA, le altre passano.
  const rotta = { ...base, multi: { markets: [
    { market_type: 'OVER_UNDER_25', selection_name: 'Over 2,5' },
    { market_type: 'OVER_UNDER_05', selection_name: 'Over 0,5', price: 'abc' },
  ], selections: [] } };
  const k = runParser(msg, rotta);
  eq(k.righe.length, 2, 'la riga rotta resta nel conteggio');
  eq(k.righe[0].complete, true, 'la sana passa');
  eq(k.righe[1].complete, false, 'la rotta no');
  eq(k.righe[1].scarti.length >= 1, true, 'col motivo della guardia');

  // Tranello 4: selezione VUOTA + delimitatori = punteggi dinamici, SOLO su
  // CORRECT_SCORE/HALF_TIME_SCORE — una riga per N-N trovato fra i delimitatori.
  const punteggi = { ...base, multi: { markets: [
    { market_type: 'CORRECT_SCORE', selection_name: '',
      start_after: 'Risultati:', end_before: ';' },
  ], selections: [] } };
  const p = runParser('P.Bet.\nJuve v Milan\nRisultati: 1-0 2-1; @ 1.85', punteggi);
  eq(p.righe.length, 2, 'un punteggio, una riga');
  eq(p.righe[0].row[iSel], '1-0', 'primo punteggio');
  eq(p.righe[1].row[iSel], '2-1', 'secondo punteggio');
  eq(p.righe[0].row[iMt], 'CORRECT_SCORE', 'sul mercato della riga');

  // Fuori da quei due mercati e' un ERRORE DI CONFIG, segnalato — non una riga.
  const sbagliata = { ...base, multi: { markets: [
    { market_type: 'OVER_UNDER_25', selection_name: '',
      start_after: 'Risultati:', end_before: ';' },
  ], selections: [] } };
  const s = runParser('P.Bet.\nJuve v Milan\nRisultati: 1-0; @ 1.85', sbagliata);
  eq(s.righe.length, 1, 'la riga di config sbagliata resta nel conteggio');
  eq(s.righe[0].complete, false, 'ma non genera niente di piazzabile');
  eq(s.righe[0].scarti.some(x => x.includes('CORRECT_SCORE')), true,
    'il motivo dice DOVE la selezione vuota e- ammessa');
  return 'ok';
});

caso('multi: riga attiva = oggetto NON vuoto; i punteggi hanno un tetto', () => {
  const columns = {}; for (const c of COLUMNS) columns[c] = { source: 'empty' };
  columns.EventName = { source: 'line', anchor: ' v ', part: 'whole',
    transforms: [{ op: 'replace_last', from: ' v ', to: ' - ' }, { op: 'trim' }] };
  columns.MarketType = { source: 'constant', value: 'OVER_UNDER_15' };
  columns.SelectionName = { source: 'constant', value: 'Over 1,5' };
  columns.BetType = { source: 'constant', value: 'PUNTA' };
  const base = { match: { type: 'contains', value: 'P.Bet.' }, columns };

  // Una riga VUOTA ({}) non e' una riga: non genera un clone della base.
  // Era la divergenza misurata sul relay: {} e' falsy in Python e truthy in
  // JS, quindi JS generava 2 righe e Python 1 dalla stessa config.
  const conVuota = runParser('P.Bet.\nJuve v Milan', { ...base, multi: {
    markets: [{}, { market_type: 'X_MKT', selection_name: 'S' }],
    selections: [] } });
  eq(conVuota.righe.length, 1, 'la riga vuota non genera; resta la sola piena');
  eq(conVuota.righe[0].row[COLUMNS.indexOf('MarketType')], 'X_MKT',
    'ed e- quella con i campi');

  // I punteggi dinamici hanno un TETTO per riga: oltre, la riga e' un errore
  // di config segnalato (delimitatori che prendono troppo), non migliaia di
  // documenti nel feed. Bloccante di Claude Fable 5 sulla PR #69.
  const troppi = Array.from({ length: 40 }, (_, i) => `${i}-${i}`).join(' ');
  const capped = runParser(`P.Bet.\nJuve v Milan\nRisultati: ${troppi}; fine`, {
    ...base, multi: { markets: [
      { market_type: 'CORRECT_SCORE', selection_name: '',
        start_after: 'Risultati:', end_before: ';' }], selections: [] } });
  eq(capped.righe.length, 1, 'una riga di errore, non 40 righe');
  eq(capped.righe[0].complete, false, 'e non e- piazzabile');
  eq(capped.righe[0].scarti.some(x => x.includes('massimo')), true,
    'il motivo dice il tetto');

  // Sotto il tetto tutto invariato: 36 punteggi = 36 righe.
  const giusti = Array.from({ length: 36 }, (_, i) => `${i}-${i}`).join(' ');
  const ok = runParser(`P.Bet.\nJuve v Milan\nRisultati: ${giusti}; fine`, {
    ...base, multi: { markets: [
      { market_type: 'CORRECT_SCORE', selection_name: '',
        start_after: 'Risultati:', end_before: ';' }], selections: [] } });
  eq(ok.righe.length, 36, 'al tetto esatto si genera tutto');
  return 'ok';
});

caso('multi: il gate di contenuto (#41) vale anche sulle righe generate', () => {
  // Base con TUTTE le obbligatorie costanti: il gate #41 scarta la base — e
  // deve scartare anche ogni riga di override, che aggiunge solo altre
  // costanti. Senza, `multi` sarebbe la porta sul retro del gate: la stessa
  // scommessa fissa scritta N volte per qualunque messaggio riconosciuto.
  const columns = {}; for (const c of COLUMNS) columns[c] = { source: 'empty' };
  columns.EventName = { source: 'constant', value: 'Juve - Milan' };
  columns.MarketType = { source: 'constant', value: 'OVER_UNDER_15' };
  columns.SelectionName = { source: 'constant', value: 'Over 1,5' };
  columns.BetType = { source: 'constant', value: 'PUNTA' };
  columns.Price = { source: 'constant', value: '1.85' };
  const base = { match: { type: 'contains', value: 'P.Bet.' }, columns };
  const msg = 'P.Bet.\nRisultati: 1-0 2-1; @ 1.85';

  const fissa = runParser(msg, { ...base, multi: { markets: [
    { market_type: 'OVER_UNDER_25', selection_name: 'Over 2,5' },
  ], selections: [] } });
  eq(fissa.complete, false, 'la base fissa resta scartata dal gate');
  eq(fissa.righe.length, 1, 'la riga di override resta nel conteggio');
  eq(fissa.righe[0].complete, false, 'ma il gate la scarta come la base');
  eq(fissa.righe[0].scarti.some(x => x.includes('nessuna colonna obbligatoria')),
    true, 'con lo stesso motivo del gate #41');

  // L'unica estrazione reale della base e' la SELEZIONE: la riga che la
  // sovrascrive con una costante torna tutta fissa, e il gate vale PER RIGA.
  const estrae = { ...base, columns: { ...columns,
    SelectionName: { source: 'regex', pattern: '(Over [0-9],[0-9])', group: 1 } } };
  const perRiga = runParser('P.Bet.\nOver 1,5\n@ 1.85', { ...estrae, multi: {
    markets: [{ market_type: 'OVER_UNDER_25', selection_name: 'Over 9,9' }],
    selections: [{ bet_type: 'BANCA' }] } });
  eq(perRiga.righe.length, 2, 'due righe generate');
  eq(perRiga.righe[0].complete, false, 'selezione sovrascritta = tutta fissa');
  eq(perRiga.righe[1].complete, true, 'la selezione estratta ereditata passa');

  // I punteggi dinamici sono ESENTI: la selezione viene dal messaggio per
  // costruzione (dai delimitatori), quindi la riga varia col messaggio.
  const punteggi = runParser(msg, { ...base, multi: { markets: [
    { market_type: 'CORRECT_SCORE', selection_name: '',
      start_after: 'Risultati:', end_before: ';' },
  ], selections: [] } });
  eq(punteggi.righe.length, 2, 'un punteggio, una riga');
  eq(punteggi.righe.every(x => x.complete), true,
    'i punteggi estratti dal messaggio non sono una scommessa fissa');
  return 'ok';
});

caso('motore: casi di confronto per il gemello Python', () => casiConfronto());

process.stdout.write(JSON.stringify(casi, null, 1));
// `exitCode`, NON `process.exit()`. Su una pipe la scrittura di stdout e'
// asincrona e `exit()` scarta cio' che non e' ancora stato scaricato: l'output
// arrivava TRONCATO a esattamente 65536 byte, e il wrapper pytest riceveva un
// JSON tagliato a meta'. Il difetto era latente finche' i casi stavano sotto i
// 64 KiB — cioe' invisibile fino al giro in cui il payload cresce, e allora il
// sintomo (`JSONDecodeError` su una riga qualunque) non somiglia alla causa.
// Con `exitCode` node esce da solo quando ha finito di scrivere.
process.exitCode = casi.every(c => c.ok) ? 0 : 1;
