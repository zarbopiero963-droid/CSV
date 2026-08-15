// Motore di parsing generico, condiviso tra anteprima UI e (in futuro) backend.
// Ogni parser è descritto da una config JSON: una condizione di riconoscimento
// e una regola per ciascuna delle 14 colonne XTrader. Questo file è la specifica
// eseguibile del formato: il motore Python di M1 deve produrre gli stessi output.

export const COLUMNS = [
  'Provider', 'EventId', 'EventName', 'MarketId', 'MarketName', 'MarketType',
  'SelectionId', 'SelectionName', 'Handicap', 'Price', 'MinPrice', 'MaxPrice',
  'BetType', 'Points',
];

export const TRANSFORMS = {
  // `BORDI_UNIFORMI`, non `v.trim()`: il `trim` tocca il VALORE estratto, cioe'
  // i byte della riga CSV, e i default di `strip()`/`trim()` divergono su
  // `\ufeff` e `\x1c-\x1f` — la stessa riga usciva diversa fra anteprima e
  // produzione (classe del [REAL_FINDING] dei gate, PR #47). Definita piu'
  // sotto: qui e' solo catturata, e viene valutata alla chiamata.
  trim:          { label: 'Rimuovi spazi ai lati',        fn: v => v.replace(BORDI_UNIFORMI, '') },
  replace_last:  { label: 'Sostituisci ultima occorrenza', fn: (v, t) => replaceLast(v, t.from, t.to), args: ['from', 'to'] },
  replace_all:   { label: 'Sostituisci tutto',             fn: (v, t) => v.split(t.from).join(t.to), args: ['from', 'to'] },
  upper:         { label: 'MAIUSCOLO',                     fn: v => v.toUpperCase() },
  lower:         { label: 'minuscolo',                     fn: v => v.toLowerCase() },
  comma_to_dot:  { label: 'Virgola decimale → punto',      fn: v => v.replace(',', '.') },
  dot_to_comma:  { label: 'Punto decimale → virgola',      fn: v => v.replace('.', ',') },
  digits_only:   { label: 'Solo cifre e separatori',       fn: v => (v.match(/[0-9.,]+/) || [''])[0] },
};

// Taglia ai primi `n` CODEPOINT, non alle prime n unita' UTF-16: `slice` conta
// unita', quindi un emoji astrale a cavallo del taglio lascerebbe un surrogato
// spaiato. Un'ancora cosi' non combacia piu' con nessuna riga e la colonna resta
// vuota in silenzio. Fonte unica: la usano sia il motore sia la web app.
export function cutByCodePoint(text, n) {
  return [...String(text ?? '')].slice(0, n).join('');
}

function replaceLast(text, from, to) {
  if (!from) return text;
  const i = text.lastIndexOf(from);
  return i < 0 ? text : text.slice(0, i) + to + text.slice(i + from.length);
}

// Una regola vuota: la colonna resta vuota nel CSV.
export function emptyRule() {
  return { source: 'empty' };
}

// Descrizione leggibile di una regola, per la tabella di riepilogo.
export function describeRule(rule) {
  if (!rule || rule.source === 'empty') return '(vuoto)';
  const tr = (rule.transforms || []).length ? `  +${rule.transforms.length} trasf.` : '';
  if (rule.source === 'constant') return `"${rule.value}"${tr}`;
  if (rule.source === 'regex') return `regex ${rule.pattern} [${rule.group ?? 1}]${tr}`;
  if (rule.source === 'line') {
    const where = rule.part === 'after' ? `testo dopo "${rule.marker}"` : 'riga intera';
    return `riga con "${rule.anchor}" → ${where}${tr}`;
  }
  if (rule.source === 'message') return `messaggio intero${tr}`;
  return rule.source;
}

function findLine(message, anchor) {
  const needle = (anchor || '').toLowerCase();
  return message.split(/\r?\n/).find(l => l.toLowerCase().includes(needle));
}

// Estrae il valore grezzo di una colonna dal messaggio, poi applica le trasformazioni.
export function extractValue(message, rule) {
  let v = '';
  if (!rule) return '';
  switch (rule.source) {
    case 'empty':
      return '';
    case 'constant':
      v = rule.value ?? '';
      break;
    case 'message':
      v = message;
      break;
    case 'line': {
      const line = findLine(message, rule.anchor);
      if (line === undefined) return '';
      if (rule.part === 'after' && rule.marker) {
        // findLine trova la riga ignorando il caso: se il taglio fosse
        // sensibile al caso, un marcatore che differisce solo per maiuscole
        // darebbe indexOf === -1 e la colonna resterebbe vuota senza errore.
        const i = line.toLowerCase().indexOf(rule.marker.toLowerCase());
        v = i < 0 ? '' : line.slice(i + rule.marker.length);
      } else {
        v = line;
      }
      break;
    }
    case 'regex': {
      let m = null;
      try {
        m = new RegExp(rule.pattern, rule.flags || 'i').exec(message);
      } catch {
        return '';
      }
      if (!m) return '';
      v = m[rule.group ?? 1] ?? m[0];
      break;
    }
    default:
      return '';
  }
  for (const t of rule.transforms || []) {
    const def = TRANSFORMS[t.op];
    if (def) v = def.fn(v, t);
  }
  return v;
}

// Il messaggio appartiene a questo parser?
export function matches(message, cond) {
  if (!cond || !cond.value) return false;
  if (cond.type === 'regex') {
    try {
      return new RegExp(cond.value, 'i').test(message);
    } catch {
      return false;
    }
  }
  return message.toLowerCase().includes(cond.value.toLowerCase());
}

// Colonne senza le quali la riga sarebbe pericolosamente incompleta per XTrader.
// QUATTRO, decise dal proprietario il 13/08/2026 (Issue #2, riconfermate su #25):
// sono le colonne che rendono un segnale una scommessa eseguibile — l'evento, il
// TIPO di mercato su cui XTrader decide, la selezione, e se puntare o bancare.
//
// `Provider` NON e' qui, anche se prima lo era: e' sempre una costante ("XTrader")
// e pretenderla non protegge da nulla. `Price` non e' qui: la quota la mette
// XTrader dal proprio book, quindi pretenderla bloccherebbe i segnali reali.
// `MarketName` non e' qui: e' l'etichetta leggibile, mentre `MarketType` e' il
// codice su cui XTrader agisce — pretendere la prima invece del secondo era
// l'errore di trascrizione nella nota di #25.
//
// Cambiato in ENTRAMBI i motori (qui e `COLONNE_OBBLIGATORIE` in main.py) nello
// stesso momento, col test di confronto che li tiene identici: due liste che
// divergono darebbero a un utente «completo» nel browser e feed vuoto in produzione.
export const REQUIRED_COLUMNS = ['EventName', 'MarketType', 'SelectionName', 'BetType'];

// Le colonne che XTrader legge come NUMERI, con l'intervallo ammesso. Gemella di
// `INTERVALLI_NUMERICI` in main.py: due implementazioni dello stesso contratto,
// cambiate nello stesso momento o l'utente vedrebbe «completo» nel browser e feed
// vuoto in produzione. Decisi nella Issue #39 — `1.01-1000` e' la scala reale
// delle quote Betfair; l'handicap ha un inviluppo largo apposta; `Points` e' il
// moltiplicatore dello stake e il tetto non giudica quanto punta il cliente,
// chiede solo se puo' averlo scritto una persona.
export const NUMERIC_RANGES = {
  Price: [1.01, 1000], MinPrice: [1.01, 1000], MaxPrice: [1.01, 1000],
  Handicap: [-1000, 1000], Points: [0, 1000],
};

// Il separatore decimale del feed e' una proprieta' del CONTRATTO, non una
// scelta dell'utente (#40). Per XTrader si scrive la VIRGOLA: misurato tre
// volte — l'esempio della guida ufficiale (p. 169, `"1,23"`), il Bridge in
// produzione con XTrader italiano, la conferma del proprietario. Una voce
// oggi: EN/ES arriveranno con Betting Toolkit (#37) e saranno una riga, non
// un refactor. Gemella di `SEPARATORI_DECIMALI` in main.py.
export const DECIMAL_SEPARATORS = { IT: ',' };
const FEED_LANG = 'IT';
const DECIMAL_SEPARATOR = DECIMAL_SEPARATORS[FEED_LANG];

// La forma che un campo numerico NON vuoto deve avere nel feed: la grammatica
// della guardia (`ASCII_NUMBER`) col SOLO separatore localizzato. Un punto qui
// e' una localizzazione mancata, e in contesto italiano `"1.85"` rischia la
// lettura come migliaia. DERIVATA dal separatore, non ricopiata: quando
// Betting Toolkit aggiungera' una lingua, verificatore e confine di scrittura
// non potranno divergere (suggerito da GPT-5.5, PR #48). Il separatore odierno
// e' la virgola: nella classe regex non richiede escape, ma si escapa comunque
// per non lasciare una trappola alla lingua col punto. Gemella di
// `_NUMERO_FEED` in main.py.
const SEP_RE = DECIMAL_SEPARATOR.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const FEED_NUMBER = new RegExp('^[+-]?(?:[0-9]+(?:' + SEP_RE + '[0-9]*)?|' + SEP_RE + '[0-9]+)$');

// `[0-9]` e non `\d`: in JavaScript `\d` e' gia' solo ASCII, ma la riga gemella in
// Python con `\d` accetterebbe le cifre arabo-indiane — scritto per esteso in
// entrambi, cosi' le due non possono divergere su una sottigliezza che non solleva.
const ASCII_NUMBER = /^[+-]?(?:[0-9]+(?:[.,][0-9]*)?|[.,][0-9]+)$/;

// Gli spazi uniformi fra i due motori: classe ESPLICITA, gemella di
// `_SPAZI_CLASSE` in main.py. I default dei due linguaggi non coincidono, e le
// divergenze vanno in due versi: `\x1c-\x1f` e `\x85` li normalizza solo
// Python, `\ufeff` solo JavaScript — e il BOM e' un carattere portante del
// contratto CSV. Segnalato da Claude Fable 5, PR #47. Non serve solo al valore
// citato nei motivi: e' la classe su cui corrono il VERDETTO numerico,
// l'emptiness delle obbligatorie e la trasformazione `trim` — ovunque i
// default di `strip()`/`trim()` farebbero divergere i due motori.
const SPAZI_CLASSE = '\t\n\v\f\r \u001c-\u001f\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff';
const SPAZI_UNIFORMI = new RegExp('[' + SPAZI_CLASSE + ']+', 'g');
const BORDI_UNIFORMI = new RegExp('^[' + SPAZI_CLASSE + ']+|[' + SPAZI_CLASSE + ']+$', 'g');

// Gemella di `_piatto` in main.py: la normalizzazione che PRECEDE ogni verdetto
// dei motori. `strip()` di Python non toglie `\ufeff`, `trim()` di JS non
// toglie `\x1c-\x1f`/`\x85`: un verdetto preso sul testo grezzo diverge fra
// browser e produzione — anteprima «completa», feed vuoto. [REAL_FINDING] di
// Claude Fable 5 e GPT-5.6 Sol al gate finale della PR #47.
const piatto = t => String(t).replace(SPAZI_UNIFORMI, ' ').trim();

const readable = x => String(x);

// `null` se il valore e' accettabile per quella colonna, altrimenti il MOTIVO.
// Gemella di `motivo_valore_numerico` in main.py, stesso ordine dei controlli:
// vuoto ammesso (la quota la mette XTrader), cifre non ASCII, non convertibile
// (col separatore delle migliaia nominato quando ce n'e' piu' d'uno), non finito
// (prima dei tetti: l'infinito supera i confronti nel verso sbagliato), fuori
// intervallo. Il motivo dice cosa fare, non solo cosa non va.
export function numericReason(column, value) {
  const range = NUMERIC_RANGES[column];
  if (!range) return null;
  // `String()` qui e `_testo_canonico` in main.py devono dare lo STESSO testo: il
  // verdetto coinciderebbe comunque, ma il MOTIVO citerebbe due valori diversi
  // (`true` contro `True`, `1` contro `1.0`) e i motivi sono la cosa che queste
  // guardie esistono per rendere affidabile. Segnalato da CodeRabbit, PR #47.
  // Niente `trim()` qui: il verdetto corre sul valore NORMALIZZATO dalla
  // classe condivisa (`piano`, sotto), non sul testo grezzo. Vedi `piatto`.
  const text = String(value ?? '');
  // Il taglio e' identico a quello di main.py: il valore citato finisce nel log
  // e nella UI, e un'estrazione sbagliata puo' portarsi dietro una riga intera.
  // Gli a capo e i caratteri di controllo diventano spazi PRIMA del taglio, come
  // in main.py: un motivo multilinea spezzerebbe la riga di log e la tabella.
  const piano = piatto(text);
  if (!piano) return null;
  // `cutByCodePoint`, non `slice`: `slice` conta unita' UTF-16 e spezzerebbe un
  // emoji a meta' lasciando un surrogato spaiato, mentre lo slice di Python
  // conta codepoint — i due motori citerebbero stringhe diverse, cioe' la
  // divergenza che queste guardie esistono per chiudere. E' la stessa ragione
  // per cui questa funzione esiste per le ancore delle regole: la classe era
  // gia' nota, il sito no. Segnalato da Claude Fable 5 sulla PR #47.
  const citato = [...piano].length <= 60 ? piano : cutByCodePoint(piano, 60) + '…';
  if (!ASCII_NUMBER.test(piano)) {
    const separators = (piano.match(/[.,]/g) || []).length;
    if (separators > 1) {
      return `${column}: «${citato}» non e' un numero. Probabile causa: il separatore `
        + 'delle migliaia — controlla le trasformazioni della regola.';
    }
    return `${column}: «${citato}» non e' un numero valido. XTrader legge solo cifre `
      + "ASCII: controlla la regola, sta leggendo la parte sbagliata del messaggio.";
  }
  const n = Number(piano.replace(',', '.'));
  if (!Number.isFinite(n)) {
    return `${column}: «${citato}» non e' un numero finito. Il valore estratto e' `
      + 'troppo lungo per essere un numero reale: controlla la regola.';
  }
  const [min, max] = range;
  if (n < min || n > max) {
    return `${column}: ${citato} e' fuori dall'intervallo ammesso `
      + `(${readable(min)}–${readable(max)}). Probabile causa: il separatore delle `
      + 'migliaia letto come decimale — controlla le trasformazioni «Virgola '
      + 'decimale → punto» e «Solo cifre e separatori» nella regola.';
  }
  return null;
}

// Le chiavi di `columns` che non sono colonne del CSV. Gemella del controllo in
// `_valida_config_parser`: il wizard costruisce la config dalla lista canonica e
// non puo' inventarne, ma una config puo' arrivare da fuori (file unico, copia
// incollata), e una chiave con un refuso verrebbe ignorata in silenzio dal motore.
// La lista si deriva da `COLUMNS`, mai ricopiata.
export function unknownColumns(config) {
  return Object.keys((config || {}).columns || {}).filter(c => !COLUMNS.includes(c));
}

// Vero se almeno una colonna OBBLIGATORIA legge dal messaggio e ha prodotto un
// valore. Gemella di `_estrazione_reale`: senza, un parser di sole costanti
// scriverebbe la stessa scommessa per qualunque messaggio riconosciuto.
function realExtraction(columns, row) {
  return REQUIRED_COLUMNS.some(c => {
    const rule = (columns || {})[c];
    if (!rule || typeof rule !== 'object') return false;
    if (!['line', 'regex', 'message'].includes(rule.source)) return false;
    // `piatto`, non `trim()`: stessa emptiness di `missing`, o i due
    // motori divergerebbero sui caratteri che i default non coprono.
    return Boolean(piatto(row[COLUMNS.indexOf(c)] ?? ''));
  });
}

// Esegue il parser sul messaggio.
//
// Restituisce:
//   matched  - il messaggio soddisfa la condizione di riconoscimento;
//   row      - le 14 colonne, sempre presenti (vuote dove non mappate);
//   missing  - le colonne obbligatorie risultate vuote;
//   complete - matched e nessuna colonna obbligatoria mancante.
//
// Chi scrive il feed deve guardare `complete`, non `matched`: un messaggio
// riconosciuto ma senza evento produrrebbe una riga quotata e priva di senso.
export function runParser(message, config) {
  const matched = matches(message, config.match);
  const row = COLUMNS.map(c => extractValue(message, (config.columns || {})[c]));
  // Le colonne NUMERICHE viaggiano nella forma su cui la guardia da' il
  // verdetto (`piatto`): un Price BOM+`2` e' una quota valida — i bordi
  // uniformi sono perdonati — ma il CSV emetteva il valore grezzo, BOM
  // compreso: XTrader riceveva il byte che la guardia aveva perdonato solo
  // ai fini del giudizio. Stessa riga in `esegui_parser`, o i due motori
  // scriverebbero feed diversi. `String()` qui e `_testo_canonico` in
  // main.py danno lo stesso testo per ogni valore JSON (e' il contratto di
  // `_numero_stile_js`). [REAL_FINDING] di GPT-5.6 Sol al gate finale, PR #47.
  for (const c of Object.keys(NUMERIC_RANGES)) {
    const i = COLUMNS.indexOf(c);
    row[i] = piatto(String(row[i] ?? ''));
  }
  // Il valore va normalizzato prima del confronto: " " e' truthy, quindi senza
  // trim una colonna obbligatoria fatta di soli spazi passerebbe per valorizzata
  // e il feed riceverebbe una riga quotata e priva di senso. Non basta il `trim`
  // fra le trasformazioni della regola: quello e' opzionale e lo decide l'utente
  // nel wizard, mentre questo controllo e' il pavimento che non deve dipendere
  // dalla configurazione.
  // `piatto`, non `trim()`: una obbligatoria di solo BOM era "mancante" in
  // JS e "valorizzata" in Python (classe del [REAL_FINDING] dei gate, PR #47).
  const missing = REQUIRED_COLUMNS.filter(c => !piatto(row[COLUMNS.indexOf(c)] ?? ''));
  // Il motivo per colonna serve DUE volte: per gli scarti e per decidere
  // quali valori localizzare al confine di scrittura (solo gli accettati).
  const numericReasons = {};
  for (const c of Object.keys(NUMERIC_RANGES)) {
    numericReasons[c] = numericReason(c, row[COLUMNS.indexOf(c)]);
  }
  // Nessuno scarto senza riconoscimento: un parser mai riconosciuto ma con
  // una costante numerica invalida produrrebbe motivi per QUALUNQUE
  // messaggio, e il dispatch li archivierebbe sotto un parser che non
  // c'entra. [REAL_FINDING] di GPT-5.6 Sol al gate finale della PR #47.
  const scarti = !matched ? [] : Object.values(numericReasons).filter(Boolean);
  if (matched && missing.length === 0 && !realExtraction(config.columns, row)) {
    scarti.push("nessuna colonna obbligatoria viene estratta dal messaggio: con soli "
      + 'valori fissi questo parser scriverebbe la stessa scommessa per qualunque '
      + 'messaggio. Almeno una fra ' + REQUIRED_COLUMNS.join(', ')
      + ' deve leggere dal messaggio.');
  }
  // Il confine di scrittura (#40): i valori numerici ACCETTATI escono nella
  // forma localizzata — per XTrader la virgola. Prima il separatore era un
  // incidente: usciva cio' che la regola produceva, e il suggeritore spingeva
  // verso il punto — che in contesto italiano rischia la lettura come
  // migliaia: quota 185, dentro i tetti della #39, invisibile a ogni guardia.
  // La localizzazione sta QUI e non in `toCsv` ne' nei chiamanti (tre siti):
  // uno che dimentica = anteprima e feed diversi. Stessa riga in
  // `esegui_parser`. I valori RIFIUTATI restano in forma giudicata: non
  // vengono mai scritti, e il motivo li cita come la guardia li ha visti.
  for (const [c, reason] of Object.entries(numericReasons)) {
    const i = COLUMNS.indexOf(c);
    if (reason === null && row[i]) row[i] = row[i].replace('.', DECIMAL_SEPARATOR);
  }
  return { matched, row, missing, scarti,
           complete: matched && missing.length === 0 && scarti.length === 0 };
}

// XTrader legge il feed come UTF-8 CON BOM. Provato su x1.csv, il file che il
// Bridge scrive e XTrader consuma. Il repository affermava il contrario, e il
// feed usciva senza BOM: nessun errore da nessuna parte, il segnale semplicemente
// non arrivava. Il motore Python (main.py) usa la stessa costante: sono due
// implementazioni dello stesso contratto e devono coincidere byte per byte.
export const CSV_BOM = '\ufeff';

const quote = f => '"' + String(f ?? '').replace(/"/g, '""') + '"';

// Fonte unica del formato: 14 colonne, tutti i campi tra virgolette, separatore
// virgola, terminatore CRLF, UTF-8 con BOM. Intestazione e riga passano da qui,
// perche' due costruzioni separate sono due formati che divergono al primo
// cambiamento — ed e' esattamente cosi' che il BOM sarebbe finito in uno e non
// nell'altro.
function csvText(...rows) {
  return CSV_BOM + rows.map(r => r.map(quote).join(',')).join('\r\n') + '\r\n';
}

export function toCsv(row) {
  return csvText(COLUMNS, row);
}

export function headerOnlyCsv() {
  return csvText(COLUMNS);
}

// Intestazione attesa, derivata dalle colonne e non ricopiata: una copia a mano
// si allineerebbe da sola a un ordine sbagliato.
const HEADER_LINE = COLUMNS.map(quote).join(',');
const FIELD = '"(?:[^"]|"")*"';
const ROW_RE = new RegExp('^' + FIELD + '(?:,' + FIELD + '){' + (COLUMNS.length - 1) + '}$');

// Gemello di verify_csv() in main.py. Restituisce null se il CSV e' nella forma
// che XTrader legge, altrimenti il motivo, in italiano, da mostrare all'utente.
//
// Serve perche' il controllo stia DOVE SI GUARDA. Nel Bridge la funzione
// equivalente esisteva ed era usata altrove, ma nessun semaforo del pannello la
// consultava: l'unico avviso era una riga di log all'avvio, e un CSV inservibile
// e' rimasto tale per mesi.
export function verifyCsv(text) {
  const s = String(text ?? '');
  if (!s.startsWith(CSV_BOM)) return 'manca il BOM: XTrader non leggerebbe la prima colonna';
  const body = s.slice(CSV_BOM.length);
  if (!body.endsWith('\r\n')) return 'manca il terminatore CRLF finale';
  // Ogni CR seguito da LF e ogni LF preceduto da CR: il contratto dice CRLF.
  const residuo = body.replace(/\r\n/g, '');
  if (residuo.includes('\r') || residuo.includes('\n')) return 'c’e’ un CR o un LF non appaiati in CRLF';
  // L'ultimo elemento dello split e' vuoto per il CRLF finale: si scarta.
  // Ogni ALTRO elemento vuoto e' una riga in bianco e va respinta — filtrarli
  // tutti, come faceva la prima versione, le accettava in silenzio.
  const lines = body.split('\r\n').slice(0, -1);
  if (!lines.length) return 'CSV vuoto: manca anche l’intestazione';
  if (lines.includes('')) return `c’e’ una riga vuota alla posizione ${lines.indexOf('') + 1}`;
  if (lines[0] !== HEADER_LINE) return `intestazione diversa dal contratto (${lines[0].split(',').length} colonne)`;
  if (lines.length > 2) return `${lines.length} righe: attesa intestazione piu’ al massimo un segnale`;
  for (let i = 1; i < lines.length; i++) {
    if (!ROW_RE.test(lines[i])) return `la riga ${i + 1} non ha ${COLUMNS.length} campi tutti fra virgolette`;
    // Il formato dei campi NUMERICI e' parte del contratto (#40): un punto
    // e' una localizzazione mancata — il caso pericoloso non e' «non
    // funziona», e' `"1.85"` letto come migliaia. Il parsing regge virgole e
    // virgolette DENTRO i valori: non devono spostare gli indici.
    const campi = parseCsvRow(lines[i]);
    for (const c of Object.keys(NUMERIC_RANGES)) {
      const v = campi[COLUMNS.indexOf(c)];
      if (v && !FEED_NUMBER.test(v)) {
        return `${c} nel feed non e' nella forma localizzata del contratto (virgola decimale): ${v}`;
      }
    }
  }
  return null;
}

// I campi di una riga QUOTE_ALL: ogni campo fra virgolette, `""` = virgoletta
// letterale. Gemello del `csv.reader` usato da `verify_csv` in main.py.
function parseCsvRow(line) {
  const campi = [];
  let corrente = '';
  let dentro = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (dentro) {
      if (ch === '"') {
        if (line[i + 1] === '"') { corrente += '"'; i++; } else { dentro = false; }
      } else {
        corrente += ch;
      }
    } else if (ch === '"') {
      dentro = true;
    } else if (ch === ',') {
      campi.push(corrente); corrente = '';
    }
  }
  campi.push(corrente);
  return campi;
}

// Suggeritore euristico: è il segnaposto locale del pulsante "suggerisci mappatura".
// In produzione la stessa firma sarà servita da POST /api/parsers/:id/suggest,
// che gira lato server sul modello e restituisce la medesima struttura di config.
export function suggestConfig(message) {
  const lines = message.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
  const columns = {};
  for (const c of COLUMNS) columns[c] = emptyRule();

  columns.Provider = { source: 'constant', value: 'XTrader' };

  // Riga con 🆚 (o "vs"): è l'evento. L'ultimo " v " separa le due squadre.
  const vsLine = lines.find(l => l.includes('🆚')) || lines.find(l => /\bvs?\b/i.test(l));
  if (vsLine) {
    const marker = vsLine.includes('🆚') ? '🆚' : null;
    columns.EventName = marker
      ? { source: 'line', anchor: '🆚', part: 'after', marker: '🆚',
          transforms: [{ op: 'replace_last', from: ' v ', to: ' - ' }, { op: 'trim' }] }
      : { source: 'line', anchor: cutByCodePoint(vsLine, 12), part: 'whole',
          transforms: [{ op: 'trim' }] };
  }

  // Quota tipo "@1.85" o "quota 1,85". NIENTE `comma_to_dot`: spingeva verso
  // il punto, cioe' l'opposto di cio' che XTrader legge (#40) — ora il
  // separatore lo decide il confine di scrittura del motore, non la regola.
  if (/@\s*\d|quota\s*\d/i.test(message)) {
    columns.Price = { source: 'regex', pattern: '(?:@|quota)\\s*([0-9]+[.,][0-9]+)', group: 1 };
  }

  columns.BetType = { source: 'constant', value: 'PUNTA' };
  columns.Handicap = { source: 'constant', value: '0' };

  // La riga di intestazione del segnale è la firma del canale: usala come condizione.
  const headerLine = lines.find(l => /p\.?bet|segnale|signal|premacht|live/i.test(l)) || lines[0] || '';
  const match = {
    type: 'contains',
    value: cutByCodePoint(headerLine.replace(/[✅🔥⚽️📊🆚]/g, '').trim(), 40),
  };

  return { match, columns };
}
