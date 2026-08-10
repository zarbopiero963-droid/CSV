// Prototipo della web app SaaS. Nessun build step: moduli ES nativi.
// Le viste parlano solo con api.js, quindi sostituire il finto backend con
// quello reale non richiede modifiche qui.

import * as api from './api.js';
import { COLUMNS, TRANSFORMS, describeRule, runParser, extractValue, toCsv, headerOnlyCsv } from './engine.js';

const app = document.getElementById('app');

/* ------------------------------------------------------------- utilities */

const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function toast(msg) {
  document.querySelectorAll('.toast').forEach(t => t.remove());
  const t = document.createElement('div');
  t.className = 'toast';
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 2200);
}

async function copy(text, label = 'Copiato') {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    ta.remove();
  }
  toast(label);
}

const fmtDate = iso => {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleString('it-IT', { day: '2-digit', month: '2-digit', year: '2-digit',
                                    hour: '2-digit', minute: '2-digit' });
};

function copyRow(value, act) {
  return `<div class="copy-row">
    <div class="secret">${esc(value)}</div>
    <button data-act="${act}" data-val="${esc(value)}">Copia</button>
  </div>`;
}

/* ---------------------------------------------------------------- modali */

let modalEl = null;

function openModal(html, opts = {}) {
  closeModal();
  modalEl = document.createElement('div');
  modalEl.className = 'veil';
  modalEl.innerHTML = `<div class="modal ${opts.wide ? 'wide' : ''}">${html}</div>`;
  modalEl.addEventListener('click', e => { if (e.target === modalEl && !opts.sticky) closeModal(); });
  document.body.appendChild(modalEl);
  const f = modalEl.querySelector('input, textarea');
  if (f) f.focus();
}

function closeModal() {
  if (modalEl) modalEl.remove();
  modalEl = null;
}

/* ---------------------------------------------------------------- router */

const route = { name: 'overview', id: null, tab: 'config' };

function parseHash() {
  const parts = (location.hash || '#/').replace(/^#\/?/, '').split('/').filter(Boolean);
  if (!parts.length) return { name: 'overview' };
  if (parts[0] === 'parsers' && parts[1]) return { name: 'parser', id: parts[1], tab: parts[2] || 'config' };
  return { name: parts[0] };
}

function go(hash) { location.hash = hash; }

window.addEventListener('hashchange', render);

/* ------------------------------------------------------------------ login */

function viewLogin() {
  app.innerHTML = `
  <div class="login-wrap"><div class="login">
    <div class="logo">XT</div>
    <h1>XTrader Signal Relay</h1>
    <p class="muted small">Trasforma i segnali dei tuoi canali Telegram in un feed CSV pronto per XTrader.</p>

    <button class="tg-btn" data-act="login-widget">Accedi con Telegram</button>
    <p class="dim small" style="margin-top:10px">Nessuna password. Non chiediamo mai il token del tuo bot.</p>

    <div class="sep">oppure</div>
    <div class="stack" style="text-align:left">
      <div>
        <label>Codice dal bot</label>
        <input id="login-code" placeholder="123456" inputmode="numeric" maxlength="6">
      </div>
      <button data-act="login-code">Entra con il codice</button>
      <p class="dim small" style="margin:0">
        Apri <span class="mono">${esc(api.settings().bot_url)}</span>, invia <span class="mono">/login</span>
        e incolla qui il codice che ricevi. Utile da mobile.
      </p>
    </div>
  </div></div>`;
}

/* ------------------------------------------------------------------ shell */

function shell(inner) {
  const u = api.me();
  const item = (h, ic, label, on) =>
    `<a href="${h}" class="${on ? 'active' : ''}"><span class="ic">${ic}</span>${label}</a>`;
  app.innerHTML = `
  <div class="shell">
    <aside class="side">
      <div class="brand"><i>XT</i> Signal Relay</div>
      <nav>
        ${item('#/', '◱', 'Dashboard', route.name === 'overview')}
        ${item('#/parsers', '⌗', 'Parser', route.name === 'parsers' || route.name === 'parser')}
        ${item('#/chats', '✆', 'Chat Telegram', route.name === 'chats')}
        ${item('#/logs', '☰', 'Log messaggi', route.name === 'logs')}
        ${item('#/settings', '⚙', 'Impostazioni', route.name === 'settings')}
      </nav>
      <div class="me">
        <div>${esc(u.first_name)} <span class="dim">@${esc(u.username)}</span></div>
        <div class="dim small" style="margin:2px 0 10px">Profilo <span class="mono">${esc(u.slug)}</span></div>
        <button class="ghost small" data-act="logout">Esci</button>
      </div>
    </aside>
    <main class="main">${inner}</main>
  </div>`;
}

/* --------------------------------------------------------------- overview */

async function viewOverview() {
  shell('<div class="dim">Caricamento…</div>');
  const [parsers, chats, logs] = await Promise.all([api.listParsers(), api.listChats(), api.listLogs()]);
  const live = parsers.filter(p => api.signalState(p.id).live).length;
  const stat = (n, l) => `<div class="card"><div style="font-size:26px">${n}</div>
    <div class="muted small">${l}</div></div>`;

  shell(`
    <div class="head"><div>
      <h1>Dashboard</h1>
    </div><div class="spacer"></div>
    <button class="primary" data-act="new-parser">Crea nuovo parser</button></div>

    <div class="stats">
      ${stat(parsers.length, 'Parser')}
      ${stat(chats.length, 'Chat verificate')}
      ${stat(live, 'Segnali attivi ora')}
      ${stat(logs.filter(l => l.matched).length, 'Messaggi riconosciuti')}
    </div>

    <div class="card stack">
      <div class="row"><strong>I tuoi parser</strong><div class="spacer"></div>
        <a href="#/parsers" class="small">Vedi tutti</a></div>
      ${parsers.length ? parsers.map(parserRow).join('') :
        '<div class="empty">Nessun parser. Creane uno per iniziare.</div>'}
    </div>`);
}

function parserRow(p) {
  const done = COLUMNS.filter(c => p.config.columns[c] && p.config.columns[c].source !== 'empty').length;
  const s = api.signalState(p.id);
  return `<div class="list-item">
    <div class="grow">
      <a class="name" href="#/parsers/${p.id}">${esc(p.name)}</a>
      <div class="dim small mono">${esc(p.slug)}</div>
    </div>
    <span class="pill">${done}/14 colonne</span>
    <span class="pill">${p.chat_ids.length} chat</span>
    <span class="pill ${p.has_token ? 'on' : 'no'}">${p.has_token ? 'token attivo' : 'senza token'}</span>
    <span class="pill ${s.live ? 'on' : 'off'}">${s.live ? `segnale ${s.seconds_left}s` : 'feed vuoto'}</span>
    <span class="pill ${p.active ? 'on' : 'off'}">${p.active ? 'attivo' : 'sospeso'}</span>
  </div>`;
}

/* ---------------------------------------------------------------- parsers */

async function viewParsers() {
  shell('<div class="dim">Caricamento…</div>');
  const parsers = await api.listParsers();
  shell(`
    <div class="head"><div>
      <h1>Parser</h1>
    </div><div class="spacer"></div>
    <button class="primary" data-act="new-parser">Crea nuovo parser</button></div>
    ${parsers.length ? parsers.map(parserRow).join('') :
      `<div class="empty"><p>Non hai ancora parser.</p>
       <button class="primary" data-act="new-parser">Crea il primo parser</button></div>`}`);
}

function modalNewParser() {
  openModal(`
    <h2>Nuovo parser</h2>
    <p class="muted small">Dai un nome che ti ricordi il tipo di segnale, es. <em>Over 2,5 LIVE</em>.</p>
    <div style="margin-top:16px">
      <label>Nome del parser</label>
      <input id="np-name" placeholder="Over 2,5 LIVE" maxlength="60">
      <div id="np-err" class="small" style="color:var(--err);margin-top:8px"></div>
    </div>
    <div class="foot">
      <button data-act="close">Annulla</button>
      <button class="primary" data-act="create-parser">Salva e configura</button>
    </div>`);
}

/* ---------------------------------------------- dettaglio parser: wizard */

// Stato del wizard, vivo solo in memoria: step 0 = condizione, 1..14 = colonne, 15 = riepilogo.
let wiz = null;

function initWiz(p) {
  if (wiz && wiz.parserId === p.id) return;
  const configured = COLUMNS.some(c => p.config.columns[c].source !== 'empty');
  wiz = {
    parserId: p.id,
    step: p.sample_message ? (configured ? 15 : 0) : 0,
    started: !!p.sample_message,
    draft: JSON.parse(JSON.stringify(p.config)),
    message: p.sample_message || '',
    mode: 'message',
    pick: null,
    test: null,
    busy: false,
  };
}

const HINTS = {
  Provider: 'Chi fornisce il segnale. Di norma è un valore fisso: XTrader.',
  EventId: 'Id evento su XTrader. Se non è nel messaggio, lascialo vuoto.',
  EventName: 'Il nome dell\'evento, come "Squadra A - Squadra B".',
  MarketId: 'Id mercato. Di solito vuoto.',
  MarketName: 'Nome del mercato, es. "Over/Under 1,5 gol".',
  MarketType: 'Codice del mercato, es. "OVER_UNDER_15".',
  SelectionId: 'Id selezione. Di solito vuoto.',
  SelectionName: 'La giocata, es. "Over 1,5 goal".',
  Handicap: 'Handicap della giocata. Metti 0 se non previsto.',
  Price: 'La quota del segnale.',
  MinPrice: 'Quota minima accettabile. Vuoto se non la usi.',
  MaxPrice: 'Quota massima accettabile. Vuoto se non la usi.',
  BetType: 'PUNTA oppure BANCA.',
  Points: 'Punti o stake. È l\'ultima colonna del CSV.',
};

function currentRow() {
  return COLUMNS.map(c => extractValue(wiz.message, wiz.draft.columns[c]));
}

function previewTable(focusCol) {
  const row = currentRow();
  const hs = COLUMNS.map(c => {
    const set = wiz.draft.columns[c].source !== 'empty';
    return `<th class="${c === focusCol ? 'focus' : ''} ${set ? 'set' : ''}">${c}</th>`;
  }).join('');
  const ds = COLUMNS.map((c, i) => {
    const v = row[i];
    return `<td class="${c === focusCol ? 'focus' : ''} ${v ? '' : 'void'}">${v ? esc(v) : '—'}</td>`;
  }).join('');
  return `<div class="xt-scroll"><table class="xt">
    <thead><tr>${hs}</tr></thead><tbody><tr>${ds}</tr></tbody></table></div>`;
}

function mappedCount() {
  return COLUMNS.filter(c => wiz.draft.columns[c].source !== 'empty').length;
}

// Il CSV mostrato accanto al wizard: se la condizione non riconosce il messaggio
// di esempio, XTrader riceverebbe solo l'intestazione.
function livePreviewCsv() {
  const { matched, row } = runParser(wiz.message, wiz.draft);
  return matched ? toCsv(row) : headerOnlyCsv();
}

function fragments() {
  return wiz.message.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
}

function fragList(selected) {
  const fs = fragments();
  if (!fs.length) return '<div class="dim small">Nessuna riga nel messaggio.</div>';
  return `<div class="frag-list">${fs.map((f, i) =>
    `<button class="frag ${selected === i ? 'picked' : ''}" data-act="pick-frag" data-i="${i}">${esc(f)}</button>`
  ).join('')}</div>`;
}

// Regola proposta quando l'utente clicca una riga: se contiene un marcatore
// (emoji o ":"), prende il testo dopo il marcatore, altrimenti la riga intera.
function ruleFromFragment(line) {
  const emoji = (line.match(/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}]/u) || [])[0];
  if (emoji) return { source: 'line', anchor: emoji, part: 'after', marker: emoji, transforms: [{ op: 'trim' }] };
  const colon = line.indexOf(':');
  if (colon > 0 && colon < line.length - 1) {
    const anchor = line.slice(0, colon + 1);
    return { source: 'line', anchor, part: 'after', marker: anchor, transforms: [{ op: 'trim' }] };
  }
  return { source: 'line', anchor: line.slice(0, 14), part: 'whole', transforms: [{ op: 'trim' }] };
}

function transformEditor(rule) {
  const active = new Set((rule.transforms || []).map(t => t.op));
  const boxes = Object.entries(TRANSFORMS).map(([op, def]) => {
    const on = active.has(op);
    const args = (def.args || []).map(a => {
      const t = (rule.transforms || []).find(x => x.op === op) || {};
      return `<input class="small" style="width:90px;padding:4px 7px" data-targ="${op}:${a}"
                placeholder="${a === 'from' ? 'da' : 'a'}" value="${esc(t[a] ?? '')}">`;
    }).join(' ');
    return `<div class="row small" style="gap:8px">
      <label style="margin:0;display:flex;gap:7px;align-items:center;flex:1;color:var(--txt)">
        <input type="checkbox" style="width:auto" data-act="toggle-transform" data-op="${op}" ${on ? 'checked' : ''}>
        ${def.label}
      </label>${on ? args : ''}
    </div>`;
  }).join('');
  // Aperto per default: la sostituzione dell'ultimo " v " è l'operazione più usata.
  return `<details open style="margin-top:12px">
    <summary class="small muted" style="cursor:pointer">Trasformazioni sul valore</summary>
    <div class="stack" style="gap:7px;margin-top:10px">${boxes}</div>
  </details>`;
}

// Spiegazione della modalità di confronto scelta, mostrata sotto il selettore.
const MATCH_HELP = {
  contains: 'Cerca il testo così come lo hai scritto, in qualunque punto del messaggio. '
          + 'Maiuscole e minuscole non contano. È la scelta giusta quasi sempre: '
          + 'incolla la riga di intestazione e hai finito.',
  regex: 'Modello avanzato, per quando l\'intestazione cambia da un messaggio all\'altro. '
       + 'Esempi: "0,5|1,5 HT" riconosce entrambe le varianti, "Quota\\s+\\d" una quota '
       + 'con qualsiasi numero. Attenzione: caratteri come . ( ) [ ] hanno un significato '
       + 'speciale. Se non ti serve, scegli l\'altra opzione.',
};

// Il motore JavaScript spiega gli errori di regex in inglese e tecnico: qui
// diventano frasi utili, che dicono anche come rimediare.
const REGEX_ERRORS = [
  [/Unterminated group/i,           'manca una parentesi tonda di chiusura ")".'],
  [/Unmatched '\)'/i,               'c\'è una parentesi tonda ")" di troppo.'],
  [/Unterminated character class/i, 'manca una parentesi quadra di chiusura "]".'],
  [/Nothing to repeat/i,            'un simbolo di ripetizione come * + ? non ha nulla da ripetere prima.'],
  [/\\ at end of pattern/i,         'il modello finisce con una barra rovesciata "\\" incompleta.'],
  [/Invalid escape/i,               'una barra rovesciata "\\" è seguita da un carattere non valido.'],
  [/Invalid group/i,                'un gruppo tra parentesi è scritto male.'],
  [/Invalid quantifier|numbers out of order/i, 'una ripetizione tra graffe {} è scritta male.'],
];

// Un'espressione regolare non valida non riconoscerebbe nulla, in silenzio: va detto.
function regexError(pattern) {
  if (!pattern) return null;
  try {
    new RegExp(pattern, 'i');
    return null;
  } catch (e) {
    for (const [re, msg] of REGEX_ERRORS) if (re.test(e.message)) return msg;
    return 'il modello non è scritto correttamente.';
  }
}

function matchHelpHtml(cond) {
  const err = cond.type === 'regex' ? regexError(cond.value) : null;
  return `<p class="dim small" id="match-help" style="margin:8px 0 0">${esc(MATCH_HELP[cond.type])}</p>
    <p class="small" id="match-err" style="margin:6px 0 0;color:var(--err)">${
      err ? 'Espressione regolare non valida: ' + esc(err) : ''}</p>`;
}

function updateMatchHelp() {
  const t = document.getElementById('match-type');
  const v = document.getElementById('match-val');
  const help = document.getElementById('match-help');
  const errBox = document.getElementById('match-err');
  if (!t || !v || !help || !errBox) return;
  help.textContent = MATCH_HELP[t.value] || '';
  const err = t.value === 'regex' ? regexError(v.value) : null;
  errBox.textContent = err ? 'Espressione regolare non valida: ' + err : '';
}

function stepMatch() {
  const cond = wiz.draft.match;
  return `
    <div class="bubble ai"><div class="who">Assistente</div>
      Prima cosa: <strong>quale testo riconosce questo tipo di segnale?</strong>
      Se un messaggio non lo contiene, questo parser lo ignora e non scrive nel tuo CSV.
      Clicca la riga di intestazione, oppure scrivi il testo a mano.
    </div>
    ${fragList(null)}
    <div class="card stack" style="margin-top:14px">
      <div>
        <label>Testo che identifica il segnale</label>
        <input id="match-val" value="${esc(cond.value)}" placeholder="P.Bet. PREMACHT 0,5HT">
      </div>
      <div>
        <label>Tipo di confronto</label>
        <select id="match-type">
          <option value="contains" ${cond.type === 'contains' ? 'selected' : ''}>Il messaggio contiene questo testo</option>
          <option value="regex" ${cond.type === 'regex' ? 'selected' : ''}>Espressione regolare</option>
        </select>
        ${matchHelpHtml(cond)}
      </div>
      <div class="row"><div class="spacer"></div>
        <button class="primary" data-act="save-match">Continua</button></div>
    </div>`;
}

function stepColumn(idx) {
  const col = COLUMNS[idx];
  const rule = wiz.draft.columns[col];
  const mode = wiz.mode;
  const tab = (m, l) => `<button class="small ${mode === m ? 'primary' : ''}"
    data-act="wiz-mode" data-mode="${m}">${l}</button>`;

  let body = '';
  if (mode === 'message') {
    const sel = rule.source === 'line'
      ? fragments().findIndex(f => f.toLowerCase().includes((rule.anchor || '').toLowerCase()))
      : -1;
    body = `${fragList(wiz.pick != null ? wiz.pick : (sel >= 0 ? sel : null))}
      ${rule.source === 'line' ? `
      <div class="card stack" style="margin-top:12px;gap:10px">
        <div class="row">
          <label style="margin:0;display:flex;gap:7px;align-items:center;color:var(--txt)">
            <input type="checkbox" style="width:auto" data-act="toggle-part" ${rule.part === 'after' ? 'checked' : ''}>
            Prendi solo il testo dopo un marcatore
          </label>
        </div>
        ${rule.part === 'after' ? `<div><label>Marcatore</label>
          <input id="rule-marker" value="${esc(rule.marker || '')}"></div>` : ''}
        <div><label>Riga riconosciuta dal testo</label>
          <input id="rule-anchor" value="${esc(rule.anchor || '')}"></div>
        ${transformEditor(rule)}
      </div>` : ''}`;
  } else if (mode === 'constant') {
    body = `<div class="card"><label>Valore fisso, uguale in ogni segnale</label>
      <input id="rule-const" value="${esc(rule.source === 'constant' ? rule.value : '')}"
             placeholder="es. XTrader"></div>`;
  } else if (mode === 'regex') {
    const err = rule.source === 'regex' ? regexError(rule.pattern) : null;
    body = `<div class="card stack">
      <div><label>Espressione regolare</label>
        <input id="rule-pattern" class="mono" value="${esc(rule.source === 'regex' ? rule.pattern : '')}"
               placeholder="@\\s*([0-9.,]+)">
        <p class="dim small" id="rule-regex-help" style="margin:8px 0 0">
          Estrae un pezzo di testo che cambia a ogni messaggio. Le parentesi tonde
          delimitano la parte da tenere: <span class="mono">@\\s*([0-9.,]+)</span>
          prende il numero dopo la chiocciola, quindi da "@ 1.85" ricava 1.85.
        </p>
        <p class="small" id="rule-regex-err" style="margin:6px 0 0;color:var(--err)">${
          err ? 'Espressione non valida: ' + esc(err) : ''}</p>
      </div>
      <div><label>Gruppo di cattura</label>
        <input id="rule-group" type="number" min="0" value="${rule.source === 'regex' ? (rule.group ?? 1) : 1}">
        <p class="dim small" style="margin:8px 0 0">
          Quale coppia di parentesi usare, contando da sinistra. Con una sola coppia lascia 1.
        </p>
      </div>
      ${transformEditor(rule)}
    </div>`;
  }

  return `
    <div class="bubble ai"><div class="who">Assistente</div>
      Colonna <strong class="mono">${col}</strong>. ${esc(HINTS[col])}<br>
      Cosa ci metto?
    </div>
    <div class="row wrap" style="gap:7px;margin:2px 0 12px">
      ${tab('message', 'Dal messaggio')}${tab('constant', 'Valore fisso')}${tab('regex', 'Regex')}
      <button class="small" data-act="rule-empty">Lascia vuota</button>
    </div>
    ${body}
    <div class="row" style="margin-top:14px">
      ${idx > 0 ? '<button data-act="wiz-back">Indietro</button>' : '<button data-act="wiz-back">Condizione</button>'}
      <div class="spacer"></div>
      <span class="dim small mono">${idx + 1}/14</span>
      <button class="primary" data-act="wiz-next">
        ${idx === COLUMNS.length - 1 ? 'Vai al riepilogo' : 'Avanti'}</button>
    </div>`;
}

function stepReview(p) {
  const rows = COLUMNS.map(c => {
    const r = wiz.draft.columns[c];
    const v = extractValue(wiz.message, r);
    return `<tr>
      <td>${c}</td>
      <td class="rule-desc">${esc(describeRule(r))}</td>
      <td>${v ? esc(v) : '<span class="dim">—</span>'}</td>
      <td><button class="small ghost" data-act="wiz-goto" data-i="${COLUMNS.indexOf(c)}">Modifica</button></td>
    </tr>`;
  }).join('');

  const t = wiz.test;
  return `
    <div class="bubble ai"><div class="who">Assistente</div>
      Mappatura completa. Controlla la tabella, prova un messaggio reale e salva.
    </div>
    <div class="card">
      <div class="row" style="margin-bottom:10px">
        <strong class="small">Condizione di riconoscimento</strong><div class="spacer"></div>
        <button class="small ghost" data-act="wiz-goto" data-i="-1">Modifica</button>
      </div>
      <div class="rule-desc">${wiz.draft.match.value
        ? `${wiz.draft.match.type === 'regex' ? 'regex' : 'contiene'} "${esc(wiz.draft.match.value)}"`
        : '<span style="color:var(--err)">non impostata: il parser non riconoscerà nulla</span>'}</div>
    </div>
    <div class="tbl-scroll" style="margin-top:14px"><table class="map-table">
      <thead><tr><th>Colonna</th><th>Regola</th><th>Valore</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>
    <div class="card stack" style="margin-top:16px">
      <div><label>Messaggio di prova</label>
        <textarea id="test-msg" rows="5">${esc(wiz.message)}</textarea></div>
      <div class="row wrap">
        <button data-act="run-test">Prova messaggio</button>
        <button class="ghost" data-act="wiz-restart">Ricomincia dal messaggio</button>
        <div class="spacer"></div>
        <button class="primary" data-act="wiz-save">Salva configurazione</button>
      </div>
      ${t ? `<div class="stack" style="gap:10px" id="test-result">
        <div class="row"><span class="pill ${t.matched ? 'on' : 'no'}">
          ${t.matched ? 'Riconosciuto' : 'Ignorato: la condizione non corrisponde'}</span></div>
        <div><label>CSV inviato a XTrader</label>
          <pre class="csv-out" id="test-csv">${esc(t.csv || headerOnlyCsv())}</pre></div>
      </div>` : ''}
    </div>`;
}

function stepPaste() {
  return `
    <div class="bubble ai"><div class="who">Assistente</div>
      Ciao. Per configurare questo parser mi serve un esempio reale:
      <strong>incolla qui un messaggio Telegram</strong> di quelli che vuoi trasformare in segnale.
      Poi ti chiedo, colonna per colonna, cosa deve finire nel CSV di XTrader.
    </div>
    <div class="card stack">
      <div><label>Messaggio Telegram</label>
        <textarea id="paste-msg" rows="8" placeholder="P.Bet. PREMACHT 0,5HT&#10;🆚 Manchester City v Aston Villa&#10;⏰ 20:45&#10;@ 1.42">${esc(wiz.message)}</textarea></div>
      <div class="row">
        <button class="primary" data-act="start-wizard">Avvia configurazione</button>
        <button data-act="ai-suggest" ${wiz.busy ? 'disabled' : ''}>
          ${wiz.busy ? '<span class="spin"></span> Analizzo…' : 'Suggerisci mappatura'}</button>
        <div class="spacer"></div>
      </div>
      <p class="dim small" style="margin:0">
        "Suggerisci mappatura" propone una configurazione di partenza analizzando il messaggio.
        Puoi sempre correggere ogni colonna a mano.
      </p>
    </div>`;
}

function wizardPane(p) {
  if (!wiz.started) return stepPaste();
  if (wiz.step === 0) return stepMatch();
  if (wiz.step === 15) return stepReview(p);
  return stepColumn(wiz.step - 1);
}

async function viewParser() {
  const p = api.getParser(route.id);
  if (!p) { shell('<div class="empty">Parser non trovato.</div>'); return; }
  initWiz(p);

  const tab = (t, l) => `<a href="#/parsers/${p.id}/${t}"
    class="small" style="padding:6px 12px;border-radius:8px;text-decoration:none;
    background:${route.tab === t ? 'var(--bg-3)' : 'transparent'};
    color:${route.tab === t ? 'var(--txt)' : 'var(--txt-2)'}">${l}</a>`;

  let body = '';
  if (route.tab === 'config') {
    const focus = wiz.started && wiz.step >= 1 && wiz.step <= 14 ? COLUMNS[wiz.step - 1] : null;
    const pct = !wiz.started ? 0 : Math.round((wiz.step / 15) * 100);
    body = `
      <div class="progress"><i style="width:${pct}%"></i></div>
      <div class="wizard-grid">
        <div class="chat">${wizardPane(p)}</div>
        <div class="stack sticky-pane">
          <div class="card">
            <div class="row" style="margin-bottom:10px">
              <strong class="small">Anteprima riga XTrader</strong>
              <div class="spacer"></div>
              <span class="dim small" id="mapped-count">${mappedCount()}/14 colonne mappate</span>
            </div>
            ${previewTable(focus)}
            <p class="dim small" style="margin:10px 0 0">
              Così apparirà la riga nel CSV. Le colonne vuote restano vuote, non vengono omesse.
            </p>
          </div>
          <div class="card">
            <strong class="small">CSV che riceverà XTrader</strong>
            <pre class="csv-out" id="live-csv" style="margin-top:10px">${esc(livePreviewCsv())}</pre>
          </div>
        </div>
      </div>`;
  } else if (route.tab === 'chats') {
    body = await paneChats(p);
  } else if (route.tab === 'feed') {
    body = paneFeed(p);
  } else {
    body = await paneLogs(p);
  }

  shell(`
    <div class="crumb"><a href="#/parsers">Parser</a> / ${esc(p.name)}</div>
    <div class="head"><div>
      <h1>${esc(p.name)}</h1>
      <p class="mono">${esc(p.slug)} · creato il ${fmtDate(p.created_at)}</p>
    </div><div class="spacer"></div>
      <span class="pill ${p.active ? 'on' : 'off'}">${p.active ? 'attivo' : 'sospeso'}</span>
      <button class="small" data-act="toggle-active" data-id="${p.id}">
        ${p.active ? 'Sospendi' : 'Riattiva'}</button>
      <button class="small danger" data-act="del-parser" data-id="${p.id}">Elimina</button>
    </div>
    <div class="row" style="gap:4px;margin-bottom:18px;border-bottom:1px solid var(--line);padding-bottom:12px">
      ${tab('config', 'Configurazione')}${tab('chats', 'Chat assegnate')}
      ${tab('feed', 'Feed CSV')}${tab('logs', 'Log')}
    </div>
    ${body}`);
}

async function paneChats(p) {
  const chats = await api.listChats();
  const items = chats.map(c => `
    <div class="list-item">
      <label class="grow" style="margin:0;display:flex;gap:11px;align-items:center;color:var(--txt)">
        <input type="checkbox" style="width:auto" data-act="toggle-chat" data-cid="${c.id}"
               ${p.chat_ids.includes(c.id) ? 'checked' : ''}>
        <span><span class="name">${esc(c.title)}</span>
          <span class="dim small mono" style="display:block">${esc(c.telegram_chat_id)}</span></span>
      </label>
      <span class="pill on">verificata</span>
    </div>`).join('');

  return `
    <div class="banner" style="margin-bottom:16px">
      Una chat può alimentare più parser. Lo stesso messaggio viene valutato da ognuno
      in modo indipendente: chi lo riconosce scrive nel proprio CSV, gli altri lo ignorano.
      Nessun parser può sovrascrivere il feed di un altro.
    </div>
    ${chats.length ? items : `<div class="empty"><p>Nessuna chat verificata.</p>
      <button class="primary" data-act="verify-chat">Collega una chat Telegram</button></div>`}
    ${chats.length ? `<div class="row" style="margin-top:16px">
      <button data-act="verify-chat">Collega un'altra chat</button></div>` : ''}`;
}

function paneFeed(p) {
  const url = api.feedUrl(p);
  const s = api.signalState(p.id);
  return `
    <div class="stack">
      <div class="card stack">
        <div class="row"><strong class="small">URL del feed CSV</strong><div class="spacer"></div>
          <span class="pill ${s.live ? 'on' : 'off'}">${s.live ? `segnale vivo, ${s.seconds_left}s` : 'nessun segnale'}</span></div>
        ${p.has_token ? `
          ${copyRow(url, 'copy')}
          <p class="dim small" style="margin:0">
            Il token completo è visibile solo al momento della generazione: qui vedi soltanto
            il prefisso <span class="mono">${esc(p.token_prefix)}</span>. Incolla questo URL in XTrader.
          </p>
          <div class="row">
            <button data-act="rotate-token" data-id="${p.id}">Rigenera token</button>
            <button class="danger" data-act="revoke-token" data-id="${p.id}">Revoca</button>
          </div>` : `
          <p class="muted small" style="margin:0">Questo parser non ha ancora un token: il feed non è raggiungibile.</p>
          <div class="row"><button class="primary" data-act="rotate-token" data-id="${p.id}">Genera token</button></div>`}
      </div>
      <div class="card">
        <strong class="small">Contenuto attuale del feed</strong>
        <pre class="csv-out" style="margin-top:10px">${esc(s.live ? s.csv : headerOnlyCsv())}</pre>
        <p class="dim small" style="margin:10px 0 0">
          Un segnale resta nel feed 90 secondi, poi il CSV torna alla sola intestazione.
          Il timer di questo parser è indipendente da tutti gli altri.
        </p>
      </div>
    </div>`;
}

async function paneLogs(p) {
  const logs = await api.listLogs(p.id);
  if (!logs.length) return '<div class="empty">Nessun messaggio ricevuto da questo parser.</div>';
  return `<div class="card"><div class="tbl-scroll"><table>
    <thead><tr><th>Quando</th><th>Chat</th><th>Esito</th><th>Messaggio</th></tr></thead>
    <tbody>${logs.map(l => {
      const c = l.chat_id ? api.getChat(l.chat_id) : null;
      return `<tr>
        <td class="mono dim">${fmtDate(l.at)}</td>
        <td>${c ? esc(c.title) : '<span class="dim">prova manuale</span>'}</td>
        <td><span class="pill ${l.matched ? 'on' : 'off'}">${l.matched ? 'riconosciuto' : 'ignorato'}</span></td>
        <td class="mono" style="white-space:pre-wrap">${esc(l.text.slice(0, 160))}</td>
      </tr>`;
    }).join('')}</tbody></table></div></div>`;
}

/* ------------------------------------------------------------------ chats */

async function viewChats() {
  shell('<div class="dim">Caricamento…</div>');
  const [chats, parsers] = await Promise.all([api.listChats(), api.listParsers()]);
  const s = api.settings();

  const items = chats.map(c => {
    const used = parsers.filter(p => p.chat_ids.includes(c.id));
    return `<div class="list-item">
      <div class="grow">
        <div class="name">${esc(c.title)}</div>
        <div class="dim small mono">${esc(c.telegram_chat_id)} · ${esc(c.type)}</div>
      </div>
      <span class="pill">${used.length ? used.map(p => esc(p.name)).join(', ') : 'nessun parser'}</span>
      <span class="pill on">verificata</span>
      <button class="small danger" data-act="del-chat" data-id="${c.id}">Rimuovi</button>
    </div>`;
  }).join('');

  shell(`
    <div class="head"><div>
      <h1>Chat Telegram</h1>
    </div><div class="spacer"></div>
    <button class="primary" data-act="verify-chat">Collega una chat</button></div>

    <div class="card stack" style="margin-bottom:18px">
      <strong class="small">Il bot da aggiungere</strong>
      ${copyRow(s.bot_url, 'copy')}
      <p class="dim small" style="margin:0">
        Aggiungi questo bot al gruppo o al canale, poi verifica la chat qui sotto.
        Nei canali il bot deve essere amministratore.
      </p>
    </div>

    ${chats.length ? items : `<div class="empty"><p>Nessuna chat collegata.</p>
      <button class="primary" data-act="verify-chat">Collega la prima chat</button></div>`}`);
}

// Verifica chat: codice usa-e-getta da incollare nel gruppo, così la chat
// risulta rivendicata da questo account e non può esserlo da un altro.
async function modalVerify(step = 'intro') {
  const s = api.settings();
  if (step === 'intro') {
    openModal(`
      <h2>Collega una chat Telegram</h2>
      <div class="stack" style="margin-top:14px">
        <div class="banner">Passo 1 — aggiungi il bot al gruppo o al canale.</div>
        ${copyRow(s.bot_url, 'copy')}
        <div class="row"><a href="${esc(s.bot_add_to_group_url)}" target="_blank" rel="noopener"
          class="small">Apri Telegram e aggiungilo a un gruppo</a></div>
        <div class="banner warn">
          Nei gruppi il bot deve poter leggere i messaggi: la privacy mode va disattivata,
          oppure il bot va reso amministratore. Nei canali serve comunque l'amministrazione.
        </div>
      </div>
      <div class="foot">
        <button data-act="close">Annulla</button>
        <button class="primary" data-act="verify-step2">Ho aggiunto il bot</button>
      </div>`, { wide: true, sticky: true });
    return;
  }

  const pv = await api.startVerification();
  openModal(`
    <h2>Verifica la chat</h2>
    <p class="muted small">Passo 2 — copia questo codice e scrivilo nella chat. Il bot lo leggerà e collegherà la chat al tuo account.</p>
    <div class="code-big" style="margin:16px 0">${esc(pv.code)}</div>
    <div class="row"><button data-act="copy" data-val="${esc(pv.code)}">Copia codice</button>
      <div class="spacer"></div>
      <span class="small muted"><span class="spin"></span> In attesa del messaggio…</span></div>
    <div class="stack" style="margin-top:18px">
      <div><label>Nome della chat, solo per il prototipo</label>
        <input id="vf-title" placeholder="Segnali Calcio PRO"></div>
      <button data-act="simulate-verify">Simula: il bot ha letto il codice</button>
      <p class="dim small" style="margin:0">
        In produzione questo passaggio è automatico: il codice arriva su
        <span class="mono">/telegram/webhook</span> e la chat viene collegata da sola.
      </p>
    </div>
    <div class="foot"><button data-act="close">Chiudi</button></div>`, { wide: true, sticky: true });
}

/* ------------------------------------------------------------------- logs */

async function viewLogs() {
  shell('<div class="dim">Caricamento…</div>');
  const [logs, parsers] = await Promise.all([api.listLogs(), api.listParsers()]);
  const byId = Object.fromEntries(parsers.map(p => [p.id, p]));
  shell(`
    <div class="head"><div>
      <h1>Log messaggi</h1>
    </div></div>
    ${logs.length ? `<div class="card"><div class="tbl-scroll"><table>
      <thead><tr><th>Quando</th><th>Parser</th><th>Chat</th><th>Esito</th><th>Messaggio</th></tr></thead>
      <tbody>${logs.map(l => {
        const c = l.chat_id ? api.getChat(l.chat_id) : null;
        const p = byId[l.parser_id];
        return `<tr>
          <td class="mono dim">${fmtDate(l.at)}</td>
          <td>${p ? `<a href="#/parsers/${p.id}">${esc(p.name)}</a>` : '<span class="dim">eliminato</span>'}</td>
          <td>${c ? esc(c.title) : '<span class="dim">prova manuale</span>'}</td>
          <td><span class="pill ${l.matched ? 'on' : 'off'}">${l.matched ? 'riconosciuto' : 'ignorato'}</span></td>
          <td class="mono" style="white-space:pre-wrap">${esc(l.text.slice(0, 140))}</td>
        </tr>`;
      }).join('')}</tbody></table></div></div>` : '<div class="empty">Nessun messaggio ricevuto.</div>'}`);
}

/* -------------------------------------------------------------- settings */

function viewSettings() {
  const u = api.me();
  const s = api.settings();
  shell(`
    <div class="head"><div><h1>Impostazioni</h1></div></div>
    <div class="stack">
      <div class="card stack">
        <strong class="small">Account Telegram</strong>
        <table><tbody>
          <tr><td class="muted">Nome</td><td>${esc(u.first_name)}</td></tr>
          <tr><td class="muted">Username</td><td class="mono">@${esc(u.username)}</td></tr>
          <tr><td class="muted">Telegram ID</td><td class="mono">${esc(u.telegram_id)}</td></tr>
          <tr><td class="muted">Profilo negli URL</td><td class="mono">${esc(u.slug)}</td></tr>
        </tbody></table>
      </div>
      <div class="card stack">
        <strong class="small">Bot del servizio</strong>
        ${copyRow(s.bot_url, 'copy')}
        <p class="dim small" style="margin:0">
          Impostato dall'amministratore e uguale per tutti gli utenti.
          Il token del bot resta sul server: la web app non lo riceve e non lo conserva.
        </p>
      </div>
      <div class="card stack">
        <strong class="small">Prototipo</strong>
        <p class="muted small" style="margin:0">
          I dati di questa demo vivono nel browser. Azzerando torni allo stato iniziale.
        </p>
        <div class="row"><button class="danger" data-act="reset-proto">Azzera i dati della demo</button></div>
      </div>
    </div>`);
}

/* ------------------------------------------------------------- azioni UI */

const actions = {
  async 'login-widget'() { await api.loginWidget(); go('#/'); render(); },
  async 'login-code'() {
    try { await api.loginCode(document.getElementById('login-code').value); go('#/'); render(); }
    catch (e) { toast(e.message); }
  },
  async logout() { await api.logout(); wiz = null; render(); },

  copy(el) { copy(el.dataset.val); },
  close() { closeModal(); },

  'new-parser'() { modalNewParser(); },
  async 'create-parser'() {
    const name = document.getElementById('np-name').value;
    try {
      const p = await api.createParser(name);
      closeModal();
      wiz = null;
      go(`#/parsers/${p.id}/config`);
      render();
    } catch (e) { document.getElementById('np-err').textContent = e.message; }
  },
  async 'toggle-active'(el) {
    const p = api.getParser(el.dataset.id);
    await api.updateParser(p.id, { active: !p.active });
    render();
  },
  async 'del-parser'(el) {
    const p = api.getParser(el.dataset.id);
    openModal(`<h2>Eliminare "${esc(p.name)}"?</h2>
      <p class="muted small">Spariscono configurazione, token, feed e log. L'operazione non è reversibile.</p>
      <div class="foot"><button data-act="close">Annulla</button>
        <button class="danger" data-act="del-parser-ok" data-id="${p.id}">Elimina</button></div>`);
  },
  async 'del-parser-ok'(el) {
    await api.deleteParser(el.dataset.id);
    closeModal(); wiz = null; go('#/parsers'); render();
  },

  // ------- wizard
  async 'start-wizard'() {
    const msg = document.getElementById('paste-msg').value;
    if (!msg.trim()) { toast('Incolla prima un messaggio.'); return; }
    wiz.message = msg;
    wiz.started = true;
    wiz.step = 0;
    await api.updateParser(wiz.parserId, { sample_message: msg });
    render();
  },
  async 'ai-suggest'() {
    const box = document.getElementById('paste-msg');
    const msg = box ? box.value : wiz.message;
    if (!msg.trim()) { toast('Incolla prima un messaggio.'); return; }
    wiz.message = msg;
    wiz.busy = true;
    render();
    const cfg = await api.suggest(wiz.parserId, msg);
    wiz.draft = cfg;
    wiz.busy = false;
    wiz.started = true;
    wiz.step = 15;
    await api.updateParser(wiz.parserId, { sample_message: msg });
    toast('Mappatura proposta: controlla ogni colonna.');
    render();
  },
  'wiz-restart'() { wiz.started = false; wiz.step = 0; wiz.test = null; render(); },
  'save-match'() {
    wiz.draft.match = {
      type: document.getElementById('match-type').value,
      value: document.getElementById('match-val').value.trim(),
    };
    wiz.step = 1;
    wiz.mode = 'message';
    wiz.pick = null;
    render();
  },
  'wiz-mode'(el) { wiz.mode = el.dataset.mode; render(); },
  'rule-empty'() {
    wiz.draft.columns[COLUMNS[wiz.step - 1]] = { source: 'empty' };
    advance();
  },
  'wiz-next'() { readCurrentRule(); advance(); },
  'wiz-back'() {
    readCurrentRule();
    wiz.step = Math.max(0, wiz.step - 1);
    wiz.pick = null;
    render();
  },
  'wiz-goto'(el) {
    const i = Number(el.dataset.i);
    wiz.step = i < 0 ? 0 : i + 1;
    wiz.mode = modeOf(wiz.draft.columns[COLUMNS[i]] || {});
    wiz.pick = null;
    render();
  },
  'pick-frag'(el) {
    const i = Number(el.dataset.i);
    wiz.pick = i;
    const line = fragments()[i];
    if (wiz.step === 0) {
      document.getElementById('match-val').value = line;
      updateMatchHelp();
      return;
    }
    wiz.draft.columns[COLUMNS[wiz.step - 1]] = ruleFromFragment(line);
    wiz.mode = 'message';
    render();
  },
  'toggle-part'() {
    const rule = wiz.draft.columns[COLUMNS[wiz.step - 1]];
    readCurrentRule();
    rule.part = rule.part === 'after' ? 'whole' : 'after';
    if (rule.part === 'after' && !rule.marker) rule.marker = rule.anchor;
    render();
  },
  'toggle-transform'(el) {
    const rule = wiz.draft.columns[COLUMNS[wiz.step - 1]];
    readCurrentRule();
    rule.transforms = rule.transforms || [];
    const op = el.dataset.op;
    const i = rule.transforms.findIndex(t => t.op === op);
    if (i >= 0) rule.transforms.splice(i, 1);
    else rule.transforms.push(op === 'replace_last' || op === 'replace_all'
      ? { op, from: ' v ', to: ' - ' } : { op });
    render();
  },
  async 'wiz-save'() {
    await api.updateParser(wiz.parserId, {
      config: wiz.draft,
      sample_message: document.getElementById('test-msg')?.value ?? wiz.message,
    });
    toast('Configurazione salvata.');
    render();
  },
  async 'run-test'() {
    const msg = document.getElementById('test-msg').value;
    wiz.message = msg;
    await api.updateParser(wiz.parserId, { config: wiz.draft, sample_message: msg });
    wiz.test = await api.testParser(wiz.parserId, msg);
    render();
  },

  // ------- chat
  'verify-chat'() { modalVerify('intro'); },
  'verify-step2'() { modalVerify('code'); },
  async 'simulate-verify'() {
    const title = document.getElementById('vf-title').value;
    const chat = await api.simulateVerificationMessage(title);
    await api.finishVerification();
    openModal(`<h2>Chat collegata</h2>
      <p class="muted small">Il bot ha riconosciuto il codice in <strong>${esc(chat.title)}</strong>
      (<span class="mono">${esc(chat.telegram_chat_id)}</span>). La chat è ora rivendicata dal tuo account:
      nessun altro utente può collegarla.</p>
      <div class="foot"><button class="primary" data-act="after-verify">Continua</button></div>`);
  },
  'after-verify'() { closeModal(); render(); },
  async 'del-chat'(el) { await api.deleteChat(el.dataset.id); render(); },
  async 'toggle-chat'(el) {
    const p = api.getParser(route.id);
    const cid = el.dataset.cid;
    const next = p.chat_ids.includes(cid) ? p.chat_ids.filter(x => x !== cid) : [...p.chat_ids, cid];
    await api.setParserChats(p.id, next);
    toast(next.includes(cid) ? 'Chat assegnata al parser.' : 'Chat rimossa dal parser.');
    render();
  },

  // ------- token
  async 'rotate-token'(el) {
    const { token, url } = await api.rotateToken(el.dataset.id);
    openModal(`<h2>Token generato</h2>
      <p class="muted small">Copialo ora: per sicurezza non sarà più mostrato.
      Sul server conserviamo solo il suo hash.</p>
      <div class="stack" style="margin-top:14px">
        <div><label>Token</label>${copyRow(token, 'copy')}</div>
        <div><label>URL completo del feed, da incollare in XTrader</label>${copyRow(url, 'copy')}</div>
      </div>
      <div class="foot"><button class="primary" data-act="after-token">Ho copiato</button></div>`,
      { wide: true, sticky: true });
  },
  'after-token'() { closeModal(); render(); },
  async 'revoke-token'(el) { await api.revokeToken(el.dataset.id); toast('Token revocato.'); render(); },

  'reset-proto'() {
    api.resetAll();
    wiz = null;
    go('#/');
    render();
  },
};

function modeOf(rule) {
  if (rule.source === 'constant') return 'constant';
  if (rule.source === 'regex') return 'regex';
  return 'message';
}

// Legge dal DOM i campi del passo corrente prima di cambiare vista.
function readCurrentRule() {
  if (!wiz.started || wiz.step < 1 || wiz.step > 14) return;
  const col = COLUMNS[wiz.step - 1];
  const rule = wiz.draft.columns[col];

  if (wiz.mode === 'constant') {
    const v = document.getElementById('rule-const');
    wiz.draft.columns[col] = { source: 'constant', value: v ? v.value : (rule.value || '') };
    return;
  }
  if (wiz.mode === 'regex') {
    const p = document.getElementById('rule-pattern');
    const g = document.getElementById('rule-group');
    wiz.draft.columns[col] = {
      source: 'regex',
      pattern: p ? p.value : (rule.pattern || ''),
      group: g ? Number(g.value) : (rule.group ?? 1),
      transforms: rule.transforms || [],
    };
    readTransformArgs(wiz.draft.columns[col]);
    return;
  }
  if (rule.source === 'line') {
    const a = document.getElementById('rule-anchor');
    const m = document.getElementById('rule-marker');
    if (a) rule.anchor = a.value;
    if (m) rule.marker = m.value;
    readTransformArgs(rule);
  }
}

function readTransformArgs(rule) {
  for (const el of document.querySelectorAll('[data-targ]')) {
    const [op, arg] = el.dataset.targ.split(':');
    const t = (rule.transforms || []).find(x => x.op === op);
    if (t) t[arg] = el.value;
  }
}

function advance() {
  if (wiz.step >= 14) { wiz.step = 15; }
  else { wiz.step += 1; }
  wiz.pick = null;
  wiz.mode = modeOf(wiz.draft.columns[COLUMNS[wiz.step - 1]] || {});
  render();
}

document.addEventListener('click', e => {
  const el = e.target.closest('[data-act]');
  if (!el) return;
  const fn = actions[el.dataset.act];
  if (!fn) return;
  // I checkbox gestiscono lo stato da sé: non impedirne il click nativo.
  if (el.tagName !== 'INPUT') e.preventDefault();
  fn(el);
});

// Aggiorna solo l'anteprima mentre si digita, senza ridisegnare tutta la vista
// (un re-render completo farebbe perdere il focus al campo).
document.addEventListener('input', e => {
  if (!wiz || !wiz.started || route.name !== 'parser' || route.tab !== 'config') return;
  if (!e.target.matches('#rule-const, #rule-anchor, #rule-marker, #rule-pattern, #rule-group, [data-targ]')) return;
  readCurrentRule();
  const holder = document.querySelector('.xt-scroll');
  if (holder) holder.outerHTML = previewTable(COLUMNS[wiz.step - 1]);
  const live = document.getElementById('live-csv');
  if (live) live.textContent = livePreviewCsv();
  const count = document.getElementById('mapped-count');
  if (count) count.textContent = `${mappedCount()}/14 colonne mappate`;
  const rxErr = document.getElementById('rule-regex-err');
  if (rxErr) {
    const err = regexError(document.getElementById('rule-pattern')?.value);
    rxErr.textContent = err ? 'Espressione non valida: ' + err : '';
  }
});

// La spiegazione del confronto e l'avviso sulla regex si aggiornano subito,
// senza ridisegnare il passo (il campo di testo perderebbe il focus).
document.addEventListener('input', e => {
  if (e.target.matches('#match-val, #match-type')) updateMatchHelp();
});
document.addEventListener('change', e => {
  if (e.target.matches('#match-type')) updateMatchHelp();
});

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeModal();
});

/* ----------------------------------------------------------------- render */

async function render() {
  Object.assign(route, { id: null, tab: 'config' }, parseHash());
  if (!api.me()) { viewLogin(); return; }
  if (route.name === 'parsers') return viewParsers();
  if (route.name === 'parser') return viewParser();
  if (route.name === 'chats') return viewChats();
  if (route.name === 'logs') return viewLogs();
  if (route.name === 'settings') return viewSettings();
  return viewOverview();
}

// Il contatore dei 90 secondi va aggiornato da solo nella pagina del feed.
setInterval(() => {
  if (route.name === 'parser' && route.tab === 'feed') render();
}, 1000);

render();
