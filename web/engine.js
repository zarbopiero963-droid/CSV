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
  replace_all:   { label: 'Sostituisci tutto',             fn: (v, t) => (t.from ? v.split(t.from).join(t.to) : v), args: ['from', 'to'] },
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

// I soli flag regex che i due motori onorano IDENTICI. Fuori da questo insieme
// i motori divergono (audit #81 E2), e la divergenza e' misurata, non temuta:
//   x (verbose) -> Python lo onora (`regex.X`), in JS `new RegExp(_, 'x')`
//                  SOLLEVA "Invalid flags" e l'estrazione cade a '' ;
//   y (sticky)  -> in JS ancora il match all'indice 0, in Python e' ignorato;
//   g (globale) -> irrilevante su una singola estrazione, tolto per non
//                  dipendere da `lastIndex`.
// `u` RESTA: Python e' gia' codepoint-native (modulo `regex`) e JS ha bisogno
// di `u` perche' `.` e `\p{}` combacino con Python — toglierlo riaprirebbe la
// divergenza sui caratteri astrali. Il gemello e' `_flag_regex` in main.py.
//
// Solo una STRINGA e' un insieme di flag valido. Qualunque altro tipo — numero,
// lista, oggetto, booleano dal `config_json` non attendibile — e' malformato e
// vale come ASSENTE: default `'i'`. Cosi' i due motori non dipendono dalla forma
// della coercizione a stringa, che diverge (`String({i:1})` = "[object Object]"
// contro `str({'i':1})` = "{'i': 1}", uno senza `i` e uno con): niente eccezioni,
// niente divergenza. Bloccanti Claude Fable 5 e GPT-5.6 Sol, PR #85.
//
// Il default `'i'` si applica agli assenti (`rule.flags || 'i'` storico) E ai
// non-stringa. Una STRINGA presente ma con soli flag scartati (`'x'`, `'gy'`)
// tiene stringa vuota, cioe' resta CASE-SENSITIVE — non ricade su `'i'`: un
// parser gia' salvato con `flags:'x'` non cambia i suoi valori (regressione
// silenziosa, bloccante Fable 5), perde solo il verbose/sticky gia' divergenti.
// Il Set deduplica: `new RegExp(_, 'ii')` SOLLEVA "Invalid flags".
const FLAG_REGEX_COMUNI = 'imsu';
function flagRegex(flags) {
  if (typeof flags !== 'string' || flags === '') return 'i';
  return [...new Set(flags.split(''))]
    .filter(f => FLAG_REGEX_COMUNI.includes(f)).join('');
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
      // `flags` presente ma NON stringa (config_json non attendibile): regola
      // malformata → colonna VUOTA (fail-closed), come un pattern che non
      // compila. Su una colonna obbligatoria il segnale non esce, senza fail-open
      // e senza dipendere dalla coercizione. Simmetrico a `_estrai_valore` in
      // main.py. Bloccante GPT-5.6 Sol, PR #85.
      if (rule.flags != null && typeof rule.flags !== 'string') return '';
      let m = null;
      try {
        m = new RegExp(rule.pattern, flagRegex(rule.flags)).exec(message);
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

// Niente emoji nei VALORI del feed (#42): «solo testo. Emoji non li accetta
// XTrader, lo marcherebbe non valido come segnale» — e come tutto in
// XTrader senza errore di ritorno, solo l'icona rossa. Le emoji stanno IN
// ENTRATA (i marcatori dei parser), mai in uscita. Classe ESPLICITA, gemella
// di `_EMOJI` in main.py: blocchi dei simboli (misc technical per l'orologio,
// misc symbols e dingbats per la spunta, frecce e stelle, il piano astrale dei
// simboli) piu' ZWJ e variation selector, che da soli tradiscono un'emoji
// spezzata dal taglio di una regola.
// Dentro c'e' anche il keycap combinante U+20E3 (solo sequenze emoji; la
// forma minimale '1'+U+20E3 senza FE0F era il buco - GPT-5.6 Sol, PR #49).
// Fuori restano i simboli text-default ((c), TM, !!): da soli sono testo,
// la loro forma emoji richiede FE0F, gia' intercettato.
// Esportata dal PR dei mercati (#33): la demo a file unico (`api_finta.js`)
// deve dare lo STESSO verdetto del server sui campi della libreria, e una
// seconda copia della classe sarebbe la divergenza che la regola 3 vieta.
export const EMOJI = /[\u200d\u20e3\u2300-\u23ff\u2600-\u27bf\u2b00-\u2bff\ufe0f\u{1f000}-\u{1faff}]/u;

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

// La stessa normalizzazione, ESPORTATA per chi costruisce le mappe della
// sorgente squadre (#34 pezzo 3): le chiavi devono passare dalla stessa
// classe di spazi con cui `runParser` cerca, o un nome con uno spazio doppio
// matcherebbe sul server (`_piatto`) e non nella demo. `export function` e
// non `export const`: il generatore del file unico ricostruisce il namespace
// leggendo le righe `export function`.
export function normalizzaNome(testo) { return piatto(testo); }

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

// Il gate di CONTENUTO (#41) come fonte unica: null se almeno una obbligatoria
// e' estratta davvero, altrimenti lo scarto. Lo usano la riga base e ogni riga
// di override (#35 pezzo 2): senza il secondo uso, `multi` era la porta sul
// retro del gate — base tutta costante, una riga di override, e la stessa
// scommessa fissa usciva per qualunque messaggio riconosciuto.
// Gemella di `_scarto_estrazione` in main.py.
function scartoEstrazione(columns, row) {
  if (realExtraction(columns, row)) return null;
  return "nessuna colonna obbligatoria viene estratta dal messaggio: con soli "
    + 'valori fissi questo parser scriverebbe la stessa scommessa per qualunque '
    + 'messaggio. Almeno una fra ' + REQUIRED_COLUMNS.join(', ')
    + ' deve leggere dal messaggio.';
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
export function runParser(message, config, aliasMap) {
  const matched = matches(message, config.match);
  const row = COLUMNS.map(c => extractValue(message, (config.columns || {})[c]));
  // La sorgente squadre (#34 pezzo 3): con una mappa alias→Betfair l'evento
  // si traduce QUI, sul valore finale di EventName — spezzato sull'ULTIMO
  // ' - ' (il separatore che il transform del wizard produce), ogni meta'
  // normalizzata con `piatto` (la classe di spazi gemellata, PR #47) e
  // cercata per confronto ESATTO. Squadra sconosciuta = verbatim + AVVISO
  // non bloccante (deciso dal proprietario il 17/08/2026): la mappa porta
  // anche le identita' Betfair→Betfair, quindi l'avviso scatta solo sui nomi
  // davvero estranei. Senza mappa (nessuna sorgente nel parser) non si tocca
  // niente. Stesso blocco in `esegui_parser`, vincolato dai casi di parita'.
  const avvisi = [];
  if (matched && aliasMap) {
    const iEvento = COLUMNS.indexOf('EventName');
    const evento = String(row[iEvento] ?? '');
    if (piatto(evento)) {
      const sep = evento.lastIndexOf(' - ');
      const parti = sep < 0 ? [evento] : [evento.slice(0, sep), evento.slice(sep + 3)];
      row[iEvento] = parti.map(parte => {
        const nome = piatto(parte);
        if (!nome) return nome;
        if (Object.prototype.hasOwnProperty.call(aliasMap, nome)) return aliasMap[nome];
        avvisi.push(`EventName: «${nome}» non ha un alias in questa sorgente `
          + 'squadre: nel feed esce verbatim, e XTrader lo trovera' + "' "
          + 'solo se coincide col nome Betfair.');
        return nome;
      }).join(' - ');
    }
  }
  const giudizio = giudicaRiga(row, matched);
  const rowGiudicata = giudizio.row;
  const missing = giudizio.missing;
  const scarti = giudizio.scarti;
  if (matched && missing.length === 0) {
    const gate = scartoEstrazione(config.columns, rowGiudicata);
    if (gate) scarti.push(gate);
  }
  const complete = matched && missing.length === 0 && scarti.length === 0;
  // Il multi-riga (#35 pezzo 2): `righe` e' l'elenco delle righe GENERATE —
  // senza `config.multi` e' la sola base (comportamento storico), con le righe
  // di override e' la loro somma. I campi storici continuano a descrivere la
  // BASE: i consumatori esistenti non cambiano.
  const righe = generaRighe(message, config, matched,
    { row: rowGiudicata, missing, scarti, complete });
  return { matched, row: rowGiudicata, missing, scarti, avvisi, righe, complete };
}

// Giudica UNA riga gia' estratta: appiattisce i numerici, calcola le
// obbligatorie mancanti, gli scarti (guardie numeriche + emoji) e localizza
// gli accettati. Fonte unica (#35 pezzo 2): la usano la riga base e ogni riga
// generata dagli override — un giudizio scritto due volte sarebbe due giudizi.
// Gemella di `_giudica_riga` in main.py.
function giudicaRiga(rowGrezza, matched) {
  const row = rowGrezza.slice();
  // Le colonne NUMERICHE viaggiano nella forma su cui la guardia da' il
  // verdetto (`piatto`): un Price BOM+`2` e' una quota valida — i bordi
  // uniformi sono perdonati — ma il CSV emetteva il valore grezzo, BOM
  // compreso. Stessa riga in `esegui_parser`, o i due motori scriverebbero
  // feed diversi. [REAL_FINDING] di GPT-5.6 Sol al gate finale, PR #47.
  for (const c of Object.keys(NUMERIC_RANGES)) {
    const i = COLUMNS.indexOf(c);
    row[i] = piatto(String(row[i] ?? ''));
  }
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
  // messaggio. [REAL_FINDING] di GPT-5.6 Sol al gate finale della PR #47.
  const scarti = !matched ? [] : Object.values(numericReasons).filter(Boolean);
  if (matched) {
    // Niente emoji nei valori (#42): il feed uscirebbe formalmente valido e
    // XTrader lo scarterebbe in silenzio. Stesso blocco in `esegui_parser`.
    for (const c of COLUMNS) {
      if (NUMERIC_RANGES[c]) continue;
      const testo = String(row[COLUMNS.indexOf(c)] ?? '');
      if (EMOJI.test(testo)) {
        const piano = piatto(testo);
        const citato = [...piano].length <= 60 ? piano : cutByCodePoint(piano, 60) + '…';
        scarti.push(`${c}: il valore contiene un'emoji («${citato}»). XTrader `
          + 'marcherebbe il segnale non valido, senza nessun errore di ritorno: '
          + 'estrai il testo DOPO il marcatore, non la riga intera.');
      }
    }
  }
  // Il confine di scrittura (#40): i valori numerici ACCETTATI escono nella
  // forma localizzata — per XTrader la virgola. I RIFIUTATI restano in forma
  // giudicata: non vengono mai scritti, e il motivo li cita cosi'.
  for (const [c, reason] of Object.entries(numericReasons)) {
    const i = COLUMNS.indexOf(c);
    if (reason === null && row[i]) row[i] = row[i].replace('.', DECIMAL_SEPARATOR);
  }
  return { row, missing, scarti };
}

// Gli override del multi-riga (#35): campo della riga → colonna del CSV.
const MULTI_CAMPI = {
  market_type: 'MarketType', market_name: 'MarketName',
  selection_name: 'SelectionName', price: 'Price', min_price: 'MinPrice',
  max_price: 'MaxPrice', bet_type: 'BetType', handicap: 'Handicap',
  points: 'Points',
};

// I soli mercati dove la selezione VUOTA + delimitatori estrae i punteggi.
const MERCATI_PUNTEGGI = ['CORRECT_SCORE', 'HALF_TIME_SCORE'];

// Tetto dei punteggi ESTRATTI da una riga: 36 copre 0-0..5-5, cioe' ogni
// mercato dei risultati reale. Oltre non e' un mercato: sono delimitatori che
// prendono mezzo messaggio, e senza tetto un messaggio pieno di N-N per 20
// righe genererebbe migliaia di documenti nel feed — lo storage e' condiviso
// (#31). Bloccante di Claude Fable 5 sulla PR #69. Il caso e' segnalato come
// errore di config della riga, non troncato in silenzio.
const MAX_PUNTEGGI_RIGA = 36;

// Una voce di multi.markets/multi.selections e' una RIGA solo se e' un
// oggetto NON vuoto: {} (e qualunque altra cosa) non genera un clone della
// base. In Python {} e' falsy e in JS truthy: senza questo predicato comune
// i due motori divergevano — misurato: 2 righe in JS, 1 in Python, dalla
// stessa config. Gemella di `_riga_multi` in main.py.
function rigaMulti(voce) {
  return Boolean(voce) && typeof voce === 'object' && !Array.isArray(voce)
    && Object.keys(voce).length > 0;
}

// Il testo del messaggio fra i due delimitatori della riga. Delimitatore
// assente = dal principio / fino alla fine; delimitatore NON TROVATO = ''.
// Gemella di `_segmento` in main.py.
function segmento(message, dopo, prima) {
  let inizio = 0;
  let fine = message.length;
  if (dopo) {
    const i = message.indexOf(dopo);
    if (i < 0) return '';
    inizio = i + dopo.length;
  }
  if (prima) {
    const j = message.indexOf(prima, inizio);
    if (j < 0) return '';
    fine = j;
  }
  return message.slice(inizio, fine);
}

// Le righe GENERATE dal parser (#35 pezzo 2): la base e' il modello, ogni
// riga di `config.multi` dice solo cosa cambia e il resto EREDITA (tranello
// 3: campo vuoto = quello della base, mai «nessuno»). Somma, non prodotto:
// mercati attivi + selezioni attive. `enabled: false` resta salvata e non
// genera (tranello 2). Ogni riga e' giudicata DA SOLA: una rotta non ferma
// le altre (tranello 1). Le MultiSelection restano sul mercato base per
// contratto: un loro `market_type` viene ignorato. Gemella di
// `_genera_righe` in main.py, parita' vincolata sui casi.
function generaRighe(message, config, matched, base) {
  if (!matched) return [];
  const multi = config.multi || {};
  const attive = [];
  // `Array.isArray`, non `|| []`: un `markets` non-lista faceva SOLLEVARE il
  // for..of qui (config non eseguibile) mentre Python iterava le chiavi —
  // due esiti diversi dalla stessa config. Non-lista = nessuna riga, in
  // entrambi (segnalato da CodeRabbit sulla PR #69).
  for (const m of (Array.isArray(multi.markets) ? multi.markets : [])) {
    if (rigaMulti(m) && m.enabled !== false) attive.push({ riga: m, mercato: true });
  }
  for (const s of (Array.isArray(multi.selections) ? multi.selections : [])) {
    if (rigaMulti(s) && s.enabled !== false) attive.push({ riga: s, mercato: false });
  }
  if (!attive.length) return [base];
  const iSel = COLUMNS.indexOf('SelectionName');
  const iPrezzo = COLUMNS.indexOf('Price');
  const iMercato = COLUMNS.indexOf('MarketType');
  const righe = [];
  for (const { riga, mercato } of attive) {
    const derivata = base.row.slice();
    // Le colonne SOVRASCRITTE dalla riga: per il gate #41 non contano come
    // estratte — il valore che portano e' una costante della riga, qualunque
    // cosa dica la regola della base.
    const sovrascritte = [];
    for (const [campo, colonna] of Object.entries(MULTI_CAMPI)) {
      // Le MultiSelection non toccano il mercato: e' il contratto della
      // somma — per le combinazioni si elencano righe MultiMarket.
      if (!mercato && (campo === 'market_type' || campo === 'market_name')) continue;
      const valore = riga[campo];
      if (valore !== undefined && valore !== null && String(valore) !== '') {
        derivata[COLUMNS.indexOf(colonna)] = String(valore);
        sovrascritte.push(colonna);
      }
    }
    const selEsplicita = piatto(String(riga.selection_name ?? ''));
    // I delimitatori vanno in forma CANONICA (`String`), come `_testo_canonico`
    // in Python: la validazione ammette scalari, e un delimitatore NUMERICO
    // grezzo qui rompeva `segmento` — `(5).length` e' undefined, `inizio`
    // diventava NaN e `slice(NaN)` partiva da 0, delimitatore incluso, mentre
    // Python tagliava dopo. [REAL_FINDING] di Fable al gate finale, PR #69.
    const dopoGrezzo = riga.start_after;
    const primaGrezzo = riga.end_before;
    const conDelimitatori = Boolean(dopoGrezzo || primaGrezzo);
    const dopo = dopoGrezzo ? String(dopoGrezzo) : '';
    const prima = primaGrezzo ? String(primaGrezzo) : '';
    if (conDelimitatori && selEsplicita) {
      // Delimitatori con selezione: estraggono la QUOTA propria della riga.
      derivata[iPrezzo] = segmento(message, dopo, prima);
    }
    if (conDelimitatori && !selEsplicita) {
      // Selezione VUOTA + delimitatori = punteggi dinamici, SOLO sui due
      // mercati dei risultati esatti (tranello 4): fuori da li' e' un errore
      // di config SEGNALATO, non una scorciatoia e non una riga.
      if (!MERCATI_PUNTEGGI.includes(derivata[iMercato])) {
        righe.push({ row: derivata, missing: [], scarti: [
          'SelectionName: la selezione vuota con i delimitatori estrae i '
          + 'punteggi ed e\' ammessa solo su CORRECT_SCORE e HALF_TIME_SCORE: '
          + 'questa riga non genera nulla.'], complete: false });
        continue;
      }
      // `[0-9]`, come nel gemello Python: in JS `\d` E' gia' solo ASCII, ma
      // il contratto a due implementazioni si legge meglio quando i due testi
      // coincidono — e la parita' sulle cifre unicode lo blinda.
      const punteggi = segmento(message, dopo, prima)
        .match(/[0-9]+-[0-9]+/g) || [];
      if (!punteggi.length) {
        righe.push({ row: derivata, missing: [], scarti: [
          'SelectionName: nessun punteggio N-N fra i delimitatori della riga.'],
          complete: false });
        continue;
      }
      if (punteggi.length > MAX_PUNTEGGI_RIGA) {
        righe.push({ row: derivata, missing: [], scarti: [
          'SelectionName: troppi punteggi fra i delimitatori della riga ('
          + punteggi.length + ', massimo ' + MAX_PUNTEGGI_RIGA + '): '
          + 'controlla i delimitatori.'], complete: false });
        continue;
      }
      for (const punteggio of punteggi) {
        const perPunteggio = derivata.slice();
        perPunteggio[iSel] = punteggio;
        // Niente gate #41 qui: il punteggio VIENE dal messaggio per
        // costruzione, quindi la riga varia col messaggio — non e' fissa.
        const g = giudicaRiga(perPunteggio, true);
        righe.push({ row: g.row, missing: g.missing, scarti: g.scarti,
          complete: g.missing.length === 0 && g.scarti.length === 0 });
      }
      continue;
    }
    const g = giudicaRiga(derivata, true);
    if (g.missing.length === 0) {
      // Il gate #41 vale PER RIGA: le colonne sovrascritte non contano come
      // estratte (il loro valore e' una costante della riga), quindi la
      // regola della base si toglie dal conto prima di giudicare.
      const colonne = { ...(config.columns || {}) };
      for (const colonna of sovrascritte) delete colonne[colonna];
      const gate = scartoEstrazione(colonne, g.row);
      if (gate) g.scarti.push(gate);
    }
    righe.push({ row: g.row, missing: g.missing, scarti: g.scarti,
      complete: g.missing.length === 0 && g.scarti.length === 0 });
  }
  return righe;
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

// UN documento CSV da N documenti completi (#35): il primo intero, dei
// successivi la sola parte dopo la prima CRLF (la data line) — BOM e
// intestazione una volta sola, in testa. Lista vuota = la sola intestazione.
// Gemella di `componi_feed` in main.py: la usa la demo per mostrare gli
// STESSI byte che la prova del server comporrebbe.
export function componiFeed(documenti) {
  const validi = (documenti || []).filter(d => d);
  if (!validi.length) return headerOnlyCsv();
  const pezzi = [validi[0]];
  for (const documento of validi.slice(1)) {
    const i = documento.indexOf('\r\n');
    const coda = i < 0 ? '' : documento.slice(i + 2);
    if (coda) pezzi.push(coda);
  }
  return pezzi.join('');
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
  // Dal #35 (pezzo 1) il feed puo' portare N segnali vivi: niente piu' tetto a
  // una riga — ogni data line passa comunque, una per una, dal controllo del
  // ciclo. Gemello di `verify_csv` in main.py (parita' in test_engine_contract).
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
    // Niente emoji in NESSUNA colonna (#42): XTrader marcherebbe il segnale
    // non valido, senza errore di ritorno. Regola di contratto.
    for (const c of COLUMNS) {
      if (EMOJI.test(campi[COLUMNS.indexOf(c)])) {
        return `${c} nel feed contiene un'emoji: XTrader marcherebbe il segnale non valido, senza errore di ritorno`;
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

  // Provider VUOTA da contratto (#42): e' il nome di CHI MANDA, non di chi
  // legge. Nel CSV misurato in #5 vale 'XTrader' perche' quel file l'ha
  // scritto XTrader — un'osservazione corretta letta nel verso sbagliato,
  // come il BOM. Il campo resta dell'utente: XTrader lo usa come filtro e
  // come discriminante fra segnali altrimenti identici.

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
