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
  trim:          { label: 'Rimuovi spazi ai lati',        fn: v => v.trim() },
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
// Price NON e' qui: il parser oggi in produzione (main.py) la lascia vuota perche'
// la quota la mette XTrader dal proprio book, quindi pretenderla bloccherebbe i
// segnali reali.
export const REQUIRED_COLUMNS = ['Provider', 'EventName'];

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
  const missing = REQUIRED_COLUMNS.filter(c => !row[COLUMNS.indexOf(c)]);
  return { matched, row, missing, complete: matched && missing.length === 0 };
}

// Serializza come XTrader lo pretende: 14 colonne, tutti i campi tra virgolette,
// separatore virgola, terminatore CRLF, UTF-8 senza BOM.
export function toCsv(row) {
  const q = f => '"' + String(f ?? '').replace(/"/g, '""') + '"';
  return [COLUMNS.map(q).join(','), row.map(q).join(',')].join('\r\n') + '\r\n';
}

export function headerOnlyCsv() {
  return COLUMNS.map(f => '"' + f + '"').join(',') + '\r\n';
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

  // Quota tipo "@1.85" o "quota 1,85".
  if (/@\s*\d|quota\s*\d/i.test(message)) {
    columns.Price = { source: 'regex', pattern: '(?:@|quota)\\s*([0-9]+[.,][0-9]+)', group: 1,
                      transforms: [{ op: 'comma_to_dot' }] };
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
