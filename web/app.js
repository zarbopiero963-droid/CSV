// La web app multiutente, agganciata al backend VERO (#32). Le viste parlano
// solo con api.js: la copia dimostrativa a file unico concatena api_finta.js,
// che espone la stessa superficie su localStorage, e queste viste non se ne
// accorgono. Nessun build step: moduli ES nativi.

import * as api from './api.js';
import { COLUMNS, TRANSFORMS, describeRule, runParser, extractValue, toCsv,
         headerOnlyCsv, cutByCodePoint } from './engine.js';

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
  setTimeout(() => t.remove(), 2600);
}

// Un errore di API finisce qui. Il 401 non si tostifica: la sessione e' scaduta
// (venti minuti di inattivita') e l'unica azione sensata e' tornare al login —
// il reload rifa' il boot, che senza sessione mostra la pagina d'accesso.
function fallita(e) {
  if (e && e.status === 401) { location.reload(); return; }
  toast(e && e.message ? e.message : 'operazione fallita');
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
  if (parts[0] === 'parsers' && parts[1]) {
    // `decodeURIComponent` solleva URIError su una sequenza percento storta
    // (es. `#/parsers/%`): senza la guardia l'errore scappava da render() e la
    // pagina restava bianca. Un hash storto vale come slug inesistente: la
    // vista risponde «Parser non trovato». Segnalato da CodeRabbit, PR #50.
    let id = parts[1];
    try { id = decodeURIComponent(parts[1]); } catch { /* hash storto: si usa il grezzo */ }
    return { name: 'parser', id, tab: parts[2] || 'config' };
  }
  if (parts[0] === 'mercati' && parts[1]) {
    // #/mercati/<sport>[/<mercato>]: `id` e' lo slug dello sport, `tab` l'id
    // numerico del mercato aperto sulle selezioni. Stessa guardia dei parser
    // sul percento storto.
    let id = parts[1];
    try { id = decodeURIComponent(parts[1]); } catch { /* hash storto: si usa il grezzo */ }
    return { name: 'mercati', id, tab: parts[2] || null };
  }
  if (parts[0] === 'squadre' && parts[1]) {
    // #/squadre/<competizione>[/<sorgente>]: entrambi id numerici del server,
    // niente da decodificare.
    return { name: 'squadre', id: parts[1], tab: parts[2] || null };
  }
  return { name: parts[0] };
}

function go(hash) { location.hash = hash; }

// La generazione delle viste: render() la incrementa a ogni passaggio, le
// viste async la fotografano prima dell'await e scartano risposta ED errore
// se nel frattempo un altro render e' partito. Il confronto sull'HASH non
// bastava: uscire e RIENTRARE nella stessa pagina lascia l'hash identico, e
// una risposta della prima visita arrivata fuori ordine ridisegnava sopra
// quella fresca — race ABA, bloccante di GPT-5.6 Sol sulla PR #53. (Il
// confronto sul NOME della rotta era stato scartato prima: viewOverview fa da
// fallback per rotte altrui e il nome non combaciava mai, lasciando la pagina
// al «Caricamento» — misurato nel test del pannello.)
let generazione = 0;

window.addEventListener('hashchange', render);

/* ------------------------------------------------------------------ login */

// L'errore del ritorno da oauth.telegram.org (o di boot): la pagina di login
// deve dirlo, non lasciare l'utente davanti a un login «tornato indietro».
let erroreLogin = null;

function viewLogin() {
  const urlTelegram = api.telegramAuthUrl();
  app.innerHTML = `
  <div class="login-wrap"><div class="login">
    <img class="logo" src="betrelay-icona-256.png" alt="">
    <h1>BetRelay</h1>
    <p class="muted small">Trasforma i segnali dei tuoi canali Telegram in un feed CSV pronto per XTrader.</p>
    ${erroreLogin ? `<div class="banner warn" style="text-align:left">${esc(erroreLogin)}</div>` : ''}

    ${urlTelegram ? `
      <a class="tg-btn" href="${esc(urlTelegram)}">Accedi con Telegram</a>
      <p class="dim small" style="margin-top:10px">Nessuna password. Non chiediamo mai il token del tuo bot.</p>
      <div class="sep">oppure</div>` : ''}

    <div class="stack" style="text-align:left">
      <div>
        <label for="login-user">Username</label>
        <input id="login-user" autocomplete="username">
      </div>
      <div>
        <label for="login-pass">Password</label>
        <input id="login-pass" type="password" autocomplete="current-password">
      </div>
      <button class="primary" data-act="login-password">Entra</button>
      <div id="login-err" class="small" style="color:var(--err)"></div>
      <p class="dim small" style="margin:0">
        L'accesso con password è la porta di riserva dell'amministratore.
        I clienti entrano con Telegram.
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
      <div class="brand"><img src="betrelay-icona-256.png" alt=""> BetRelay</div>
      <nav>
        ${item('#/', '◱', 'Dashboard', route.name === 'overview')}
        ${u.admin ? item('#/richieste', '▤', 'Richieste', route.name === 'richieste') : ''}
        ${item('#/parsers', '⌗', 'Parser', route.name === 'parsers' || route.name === 'parser')}
        ${item('#/mercati', '◎', 'Mercati Betfair', route.name === 'mercati')}
        ${item('#/squadre', '⇄', 'Sorgenti squadre', route.name === 'squadre')}
        ${item('#/feed', '⇩', 'Feed CSV', route.name === 'feed')}
        ${item('#/chats', '✆', 'Chat Telegram', route.name === 'chats')}
        ${item('#/logs', '☰', 'Log messaggi', route.name === 'logs')}
        ${item('#/settings', '⚙', 'Impostazioni', route.name === 'settings')}
      </nav>
      <div class="me">
        <div>${esc(u.nome || 'Utente')}</div>
        <div class="dim small" style="margin:2px 0 10px">
          ${u.slug ? `Profilo <span class="mono">${esc(u.slug)}</span>` : 'Profilo non ancora creato'}
        </div>
        <button class="ghost small" data-act="logout">Esci</button>
      </div>
    </aside>
    <main class="main">${inner}</main>
  </div>`;
}

// La pillola dello stato dell'accesso, unica per dashboard e impostazioni.
// Sotto i 5 giorni diventa GIALLA (soglia della Issue #7): l'accesso e' vivo,
// il messaggio e' «rinnova», e deve distinguersi sia dal verde sia dal rosso.
function pillStato(u) {
  if (u.admin) return '<span class="pill on">amministratore</span>';
  if (u.stato === 'attivo') {
    const giorni = u.giorni_rimasti;
    if (giorni != null && giorni <= 5) {
      return `<span class="pill warn">attivo, ${esc(giorni)} giorni rimasti — pensa al rinnovo</span>`;
    }
    return `<span class="pill on">attivo${giorni == null ? '' : `, ${esc(giorni)} giorni rimasti`}</span>`;
  }
  return `<span class="pill off">${esc(u.stato)}</span>`;
}

/* -------------------------------------------------- accesso su approvazione */

// Il deep link mostrato nella schermata d'attesa: appena chiesto l'accesso e'
// quello della risposta del server; a una visita successiva si ricostruisce
// dai settings pubblici. Nullo solo se il servizio non ha un bot configurato.
let botAccesso = null;

// Le schermate degli stati (#7): chi non e' `attivo` non vede l'app, vede a
// che punto e' il suo accesso e cosa puo' fare. L'amministratore non passa
// mai di qui: entra sempre, e' lui che approva.
function viewAccesso(u) {
  const bot = botAccesso || api.botAccessoUrl();
  const linkBot = bot
    ? `<a class="tg-btn" data-ruolo="bot-link" href="${esc(bot)}" target="_blank" rel="noopener">
         Apri il bot e premi Start</a>
       <p class="dim small" style="margin-top:10px">
         Serve davvero: il bot non può scriverti per primo. Se non apri la chat,
         il messaggio di approvazione non potrà raggiungerti.
       </p>`
    : `<p class="muted small">Quando l'amministratore approva, lo vedrai qui al prossimo accesso.</p>`;

  let corpo = '';
  if (u.stato === 'in_attesa') {
    corpo = `
      <h1>Richiesta inviata</h1>
      <p class="muted small">L'amministratore la vede già. Riceverai un messaggio
      Telegram all'approvazione, con la durata del tuo accesso.</p>
      ${linkBot}`;
  } else if (u.stato === 'scaduto') {
    corpo = `
      <h1>Accesso scaduto</h1>
      <p class="muted small">Il tuo feed risponde con la sola intestazione e i
      messaggi delle tue chat non vengono più elaborati. La configurazione dei
      parser è ancora qui: si riparte con un rinnovo.</p>
      <button class="primary" data-act="request-access" style="width:100%">Richiedi il rinnovo</button>`;
  } else if (u.stato === 'sospeso') {
    corpo = `
      <h1>Accesso sospeso</h1>
      <p class="muted small">La sospensione è una decisione dell'amministratore:
      per chiarirla, contattalo. Da qui non si può richiedere l'accesso.</p>`;
  } else {
    corpo = `
      <h1>Ti manca solo l'accesso</h1>
      <p class="muted small">Il tuo account esiste ma il servizio è su
      approvazione: premi il pulsante e l'amministratore vedrà la tua richiesta.
      Nessun modulo da compilare.</p>
      <button class="primary" data-act="request-access" style="width:100%">Richiedi accesso</button>`;
  }

  app.innerHTML = `
  <div class="login-wrap"><div class="login accesso">
    <img class="logo" src="betrelay-icona-256.png" alt="">
    ${corpo}
    <div id="accesso-err" class="small" style="color:var(--err);margin-top:10px"></div>
    <div class="sep"></div>
    <button class="ghost small" data-act="logout">Esci</button>
  </div></div>`;
}

/* ---------------------------------------------------------- pannello admin */

// L'esito dell'ultima decisione, mostrato sopra l'elenco delle richieste:
// sopravvive al re-render della vista e si azzera cambiando pagina. Serve
// SOPRATTUTTO per l'avviso Telegram fallito: la Issue #7 pretende che un
// invio non partito sia visibile, mai ingoiato.
let esitoRichieste = null;

async function viewRichieste() {
  shell('<div class="dim">Caricamento…</div>');
  // La guardia anti-stantio: vedi `generazione` accanto al router.
  const invocazione = generazione;
  let richieste;
  try { richieste = await api.adminRequests(); }
  catch (e) { if (invocazione === generazione) fallita(e); return; }
  if (invocazione !== generazione) return;

  const righe = richieste.map(r => `
    <div class="list-item">
      <div class="grow">
        <span class="name">${esc(r.nome || 'Senza nome')}</span>
        ${r.username ? `<span class="dim small mono"> @${esc(r.username)}</span>` : ''}
        <div class="dim small">chiesto il <span class="mono">${esc(String(r.chiesto_il || '').slice(0, 16))}</span>
          · stato ${esc(r.stato)}</div>
        ${r.raggiungibile ? '' : `<div class="small" style="color:var(--warn);margin-top:4px">
          Non ha ancora aperto il bot: il messaggio di approvazione non potrà raggiungerlo.
        </div>`}
      </div>
      <input type="number" min="1" max="3650" placeholder="giorni"
             id="giorni-${esc(r.richiesta)}" style="width:90px;padding:6px 8px">
      <button class="primary small" data-act="approva-richiesta" data-id="${esc(r.richiesta)}">Attiva</button>
      <button class="danger small" data-act="rifiuta-richiesta" data-id="${esc(r.richiesta)}">Rifiuta</button>
    </div>`).join('');

  shell(`
    <div class="head"><div>
      <h1>Richieste</h1>
      <p class="muted small">Chi chiede l'accesso. I giorni sono un campo libero: 7, 30, 90 — decidi tu.</p>
    </div></div>
    ${esitoRichieste || ''}
    ${richieste.length ? `<div class="card stack">${righe}</div>`
      : '<div class="empty">Nessuna richiesta in attesa.</div>'}
    <div class="card stack" style="margin-top:18px">
      <strong class="small">Promemoria di scadenza</strong>
      <p class="dim small" style="margin:0">
        Non c'è uno scheduler: il giro parte quando lo lanci da qui. Avvisa su Telegram
        chi è a 5 giorni o meno dalla scadenza, una volta per scadenza.
      </p>
      <div class="row"><button data-act="giro-promemoria">Manda il giro di promemoria</button>
        <span class="small dim" id="esito-promemoria"></span></div>
    </div>`);
}

/* --------------------------------------------------------------- overview */

async function viewOverview() {
  shell('<div class="dim">Caricamento…</div>');
  const invocazione = generazione;  // guardia anti-stantio: vedi il router
  let parsers;
  try { parsers = await api.listParsers(); }
  catch (e) { if (invocazione === generazione) fallita(e); return; }
  if (invocazione !== generazione) return;
  const u = api.me();
  const stat = (n, l) => `<div class="card"><div style="font-size:26px">${n}</div>
    <div class="muted small">${l}</div></div>`;

  shell(`
    <div class="head"><div>
      <h1>Dashboard</h1>
      <p>${pillStato(u)}</p>
    </div><div class="spacer"></div>
    <button class="primary" data-act="new-parser">Crea nuovo parser</button></div>

    <div class="stats">
      ${stat(parsers.length, 'Parser')}
      ${stat(parsers.filter(p => p.active).length, 'Parser attivi')}
      ${stat(u.token_prefix ? 'sì' : 'no', 'Token del feed generato')}
      ${stat(u.giorni_rimasti == null ? '—' : u.giorni_rimasti, 'Giorni di accesso rimasti')}
    </div>

    <div class="card stack">
      <div class="row"><strong>I tuoi parser</strong><div class="spacer"></div>
        <a href="#/parsers" class="small">Vedi tutti</a></div>
      ${parsers.length ? parsers.map(parserRow).join('') :
        '<div class="empty">Nessun parser. Creane uno per iniziare.</div>'}
    </div>`);
}

function parserRow(p) {
  const done = COLUMNS.filter(c => p.config.columns && p.config.columns[c]
                                   && p.config.columns[c].source !== 'empty').length;
  return `<div class="list-item">
    <div class="grow">
      <a class="name" href="#/parsers/${encodeURIComponent(p.slug)}">${esc(p.titolo)}</a>
      <div class="dim small mono">${esc(p.slug)}</div>
    </div>
    <span class="pill">${done}/14 colonne</span>
    <span class="pill ${p.active ? 'on' : 'off'}">${p.active ? 'attivo' : 'sospeso'}</span>
  </div>`;
}

/* ---------------------------------------------------------------- parsers */

async function viewParsers() {
  shell('<div class="dim">Caricamento…</div>');
  const invocazione = generazione;  // guardia anti-stantio: vedi il router
  let parsers;
  try { parsers = await api.listParsers(); }
  catch (e) { if (invocazione === generazione) fallita(e); return; }
  if (invocazione !== generazione) return;
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

/* ------------------------------------------------- mercati Betfair (#33) */

// I segnaposto squadra dei mercati handicap. Oggi NON sono spendibili nel
// wizard (la risoluzione e' la sorgente squadre, #34): la libreria li accetta,
// il passo due li mostra spenti col motivo, e il server li rifiuta comunque.
const SEGNAPOSTO_SQUADRE = ['{HOME_TEAM}', '{AWAY_TEAM}'];

function haSegnaposto(testo) {
  return SEGNAPOSTO_SQUADRE.some(t => String(testo).includes(t));
}

async function viewMercati() {
  shell('<div class="dim">Caricamento…</div>');
  const invocazione = generazione;  // guardia anti-stantio: vedi il router
  try {
    await api.loadSports();
    if (route.id) await api.loadMercati(route.id);
  } catch (e) {
    if (invocazione !== generazione) return;
    // Uno sport sparito (eliminato da un altro dispositivo) non e' un errore
    // di sessione: si torna all'elenco invece di piantarsi sul 404.
    if (e.status === 404) { go('#/mercati'); return; }
    fallita(e);
    return;
  }
  if (invocazione !== generazione) return;
  if (!route.id) return mercatiElenco();
  if (!route.tab) return mercatiSport();
  return mercatiSelezioni();
}

function mercatiElenco() {
  const sports = api.sports() || [];
  shell(`
    <div class="head"><div>
      <h1>Mercati Betfair</h1>
      <p class="muted small">La tua libreria: sport, mercati e selezioni che crei una volta
        e riusi in ogni parser. Parte vuota: qui non c'è nessun catalogo precompilato.</p>
    </div><div class="spacer"></div>
    <button class="primary" data-act="sport-new">Nuovo sport</button></div>
    ${sports.length ? sports.map(s => `
      <div class="list-item">
        <div class="grow">
          <a class="name" href="#/mercati/${encodeURIComponent(s.slug)}">${esc(s.nome)}</a>
          <div class="dim small">${s.mercati} mercat${s.mercati === 1 ? 'o' : 'i'}</div>
        </div>
        <button class="danger small" data-act="sport-del" data-id="${esc(s.slug)}"
                data-nome="${esc(s.nome)}">× elimina</button>
      </div>`).join('') : `
      <div class="empty"><p>Non hai ancora sport: si parte da zero, come deve essere.</p>
       <button class="primary" data-act="sport-new">Crea il primo sport</button></div>`}`);
}

function mercatiSport() {
  const sport = (api.sports() || []).find(s => s.slug === route.id);
  if (!sport) { go('#/mercati'); return; }
  const mercati = api.mercatiOf(route.id) || [];
  shell(`
    <div class="crumb"><a href="#/mercati">Mercati Betfair</a> / ${esc(sport.nome)}</div>
    <div class="head"><div>
      <h1>${esc(sport.nome)}</h1>
      <p class="muted small">Un mercato è <span class="mono">MarketType</span> +
        <span class="mono">MarketName</span>; le selezioni le apri cliccandolo.</p>
    </div><div class="spacer"></div>
    <button class="primary" data-act="mercato-new">Crea mercato</button></div>
    ${mercati.length ? mercati.map(m => `
      <div class="list-item">
        <div class="grow">
          <a class="name mono" href="#/mercati/${encodeURIComponent(route.id)}/${m.id}">${esc(m.marketType)}</a>
          <div class="dim small">${esc(m.marketName)} · ${m.selezioni.length}
            selezion${m.selezioni.length === 1 ? 'e' : 'i'}</div>
        </div>
        <button class="danger small" data-act="mercato-del" data-id="${m.id}"
                data-nome="${esc(m.marketType)}">× elimina</button>
      </div>`).join('') : `
      <div class="empty"><p>Nessun mercato in questo sport.</p>
       <button class="primary" data-act="mercato-new">Crea il primo mercato</button></div>`}`);
}

function mercatiSelezioni() {
  const mercati = api.mercatiOf(route.id) || [];
  const mercato = mercati.find(m => m.id === Number(route.tab));
  if (!mercato) { go(`#/mercati/${encodeURIComponent(route.id)}`); return; }
  // Il NOME dello sport nella briciola, come nella vista dei mercati: lo slug
  // e' l'indirizzo, non l'etichetta. CodeRabbit, PR #55.
  const sport = (api.sports() || []).find(s => s.slug === route.id);
  shell(`
    <div class="crumb"><a href="#/mercati">Mercati Betfair</a> /
      <a href="#/mercati/${encodeURIComponent(route.id)}">${esc(sport ? sport.nome : route.id)}</a> /
      <span class="mono">${esc(mercato.marketType)}</span></div>
    <div class="head"><div>
      <h1><span class="mono">${esc(mercato.marketType)}</span></h1>
      <p class="muted small">${esc(mercato.marketName)} — crea tutte le selezioni che ti
        servono: due per un Over/Under, tante per un Risultato esatto.</p>
    </div></div>
    <div class="card stack">
      ${mercato.selezioni.length ? mercato.selezioni.map(s => `
        <div class="list-item">
          <div class="grow"><span class="name">${esc(s.selectionName)}</span>
            ${haSegnaposto(s.selectionName) ? `<div class="dim small">usa i segnaposto
              squadra: spendibile nel parser quando arriverà la sorgente squadre (#34)</div>` : ''}
          </div>
          <button class="danger small" data-act="sel-del" data-id="${s.id}">×</button>
        </div>`).join('') : '<div class="empty">Nessuna selezione ancora.</div>'}
      <div class="row">
        <input id="sel-nome" class="grow" placeholder="es. Over 0,5 goal" maxlength="120">
        <button class="primary" data-act="sel-add">Aggiungi</button>
      </div>
      <div id="sel-err" class="small" style="color:var(--err)"></div>
    </div>`);
}

function modalNewSport() {
  openModal(`
    <h2>Nuovo sport</h2>
    <div style="margin-top:16px">
      <label>Nome dello sport</label>
      <input id="ns-nome" placeholder="Calcio" maxlength="120">
      <div id="ns-err" class="small" style="color:var(--err);margin-top:8px"></div>
    </div>
    <div class="foot">
      <button data-act="close">Annulla</button>
      <button class="primary" data-act="sport-create">Salva</button>
    </div>`);
}

function modalNewMercato() {
  openModal(`
    <h2>Crea mercato</h2>
    <p class="muted small">A inserimento libero: scrivi il codice e il nome come li vuole
      XTrader. Le selezioni le aggiungi dopo, dentro il mercato.</p>
    <div class="stack" style="margin-top:16px">
      <div><label>MarketType (codice)</label>
        <input id="nm-type" class="mono" placeholder="OVER_UNDER_05HT" maxlength="120"></div>
      <div><label>MarketName (etichetta)</label>
        <input id="nm-name" placeholder="Over/Under 0.5 Goals HT" maxlength="120"></div>
      <div id="nm-err" class="small" style="color:var(--err)"></div>
    </div>
    <div class="foot">
      <button data-act="close">Annulla</button>
      <button class="primary" data-act="mercato-create">Salva</button>
    </div>`);
}

/* ------------------------------------------------ sorgenti squadre (#34) */

async function viewSquadre() {
  shell('<div class="dim">Caricamento…</div>');
  const invocazione = generazione;  // guardia anti-stantio: vedi il router
  try {
    await api.loadCompetizioni();
    await api.loadSports();   // la modale «Nuova competizione» sceglie lo sport
    if (route.id) await api.loadCompetizione(route.id);
    if (route.id && route.tab) await api.loadAlias(route.id, route.tab);
  } catch (e) {
    if (invocazione !== generazione) return;
    // Una competizione o una sorgente sparita (eliminata altrove) non e' un
    // errore di sessione: si risale di un livello invece di piantarsi sul 404.
    if (e.status === 404) { go(route.tab ? `#/squadre/${route.id}` : '#/squadre'); return; }
    fallita(e);
    return;
  }
  if (invocazione !== generazione) return;
  if (!route.id) return squadreElenco();
  if (!route.tab) return squadreCompetizione();
  return squadreAlias();
}

function squadreElenco() {
  const competizioni = api.competizioni() || [];
  shell(`
    <div class="head"><div>
      <h1>Sorgenti squadre</h1>
      <p class="muted small">I nomi Betfair li salvi UNA volta per competizione; ogni
        sorgente è una colonna di alias sopra la stessa lista — come scrive le squadre
        quel canale. La traduzione nel parser arriva col prossimo passo della #34.</p>
    </div><div class="spacer"></div>
    <button class="primary" data-act="comp-new">Nuova competizione</button></div>
    ${competizioni.length ? competizioni.map(k => `
      <div class="list-item">
        <div class="grow">
          <a class="name" href="#/squadre/${k.id}">${esc(k.nome)}</a>
          <div class="dim small">${esc(k.sportNome)} · ${k.squadre}
            squadr${k.squadre === 1 ? 'a' : 'e'} Betfair</div>
        </div>
        <button class="danger small" data-act="comp-del" data-id="${k.id}"
                data-nome="${esc(k.nome)}">× elimina</button>
      </div>`).join('') : `
      <div class="empty"><p>Non hai ancora competizioni: si parte da zero, come per i
        mercati.</p>
       <button class="primary" data-act="comp-new">Crea la prima competizione</button></div>`}`);
}

function squadreCompetizione() {
  const k = api.competizione(route.id);
  if (!k) { go('#/squadre'); return; }
  shell(`
    <div class="crumb"><a href="#/squadre">Sorgenti squadre</a> / ${esc(k.nome)}</div>
    <div class="head"><div>
      <h1>${esc(k.nome)}</h1>
      <p class="muted small">La colonna Betfair è salvata QUI, condivisa da tutte le
        sorgenti: aggiungere una squadra la rende disponibile ovunque, con alias vuoto.</p>
    </div></div>
    <div class="card stack">
      <h3>Squadre Betfair</h3>
      ${k.squadre.length ? k.squadre.map(q => `
        <div class="list-item">
          <div class="grow"><span class="name">${esc(q.nome)}</span></div>
          <button class="danger small" data-act="sq-del" data-id="${q.id}"
                  data-nome="${esc(q.nome)}">× squadra</button>
        </div>`).join('') : '<div class="empty">Nessuna squadra ancora.</div>'}
      <div class="row">
        <input id="sq-nome" class="grow" placeholder="es. Juventus (nome Betfair)"
               maxlength="120">
        <button class="primary" data-act="sq-add">Aggiungi</button>
      </div>
      <div id="sq-err" class="small" style="color:var(--err)"></div>
    </div>
    <div class="card stack">
      <h3>Sorgenti</h3>
      <p class="muted small">Clicca una sorgente per compilare i suoi alias su questa
        competizione. Il numero dice quante squadre hanno già l'alias.</p>
      <div class="row" style="flex-wrap:wrap">
        ${k.sorgenti.map(g => `
          <a class="src-btn" href="#/squadre/${k.id}/${g.id}">${esc(g.nome)}
            <span class="badge">${g.compilati}/${k.squadre.length}</span></a>`).join('')}
        <button data-act="src-new">+ Aggiungi sorgente</button>
      </div>
    </div>`);
}

function squadreAlias() {
  const k = api.competizione(route.id);
  if (!k) { go('#/squadre'); return; }
  const sorgente = k.sorgenti.find(g => g.id === Number(route.tab));
  if (!sorgente) { go(`#/squadre/${route.id}`); return; }
  const righe = api.aliasOf(route.id, route.tab) || [];
  shell(`
    <div class="crumb"><a href="#/squadre">Sorgenti squadre</a> /
      <a href="#/squadre/${k.id}">${esc(k.nome)}</a> / ${esc(sorgente.nome)}</div>
    <div class="head"><div>
      <h1>${esc(sorgente.nome)}</h1>
      <p class="muted small">Come questa sorgente scrive le squadre di ${esc(k.nome)}.
        La «⌫» svuota l'alias SOLO qui; la squadra Betfair resta, e resta nelle altre
        sorgenti.</p>
    </div><div class="spacer"></div>
    <button data-act="src-ren" data-nome="${esc(sorgente.nome)}">Rinomina</button>
    <button class="danger" data-act="src-del" data-nome="${esc(sorgente.nome)}">Elimina sorgente</button></div>
    <div class="card stack">
      ${righe.length ? righe.map(r => {
        // Normalizzato UNA volta: `data-vuoto` e `value` devono raccontare lo
        // stesso alias, qualunque forma arrivi dal layer (CodeRabbit, PR #66).
        const alias = r.alias || '';
        return `
        <div class="list-item">
          <div class="grow"><span class="name">${esc(r.squadra)}</span></div>
          <input data-squadra="${r.squadra_id}" data-nome="${esc(r.squadra)}"
                 ${alias === '' ? 'data-vuoto="1"' : ''}
                 value="${esc(alias)}" placeholder="alias della sorgente"
                 maxlength="120" style="width:min(46%,260px)">
          <button class="small" title="Svuota l'alias solo in questa sorgente"
                  data-act="alias-clear" data-id="${r.squadra_id}"
                  data-nome="${esc(r.squadra)}">⌫</button>
        </div>`;
      }).join('') : `<div class="empty">Questa competizione non ha ancora squadre
          Betfair: <a href="#/squadre/${k.id}">aggiungile prima</a>.</div>`}
      ${righe.length ? `<div class="row"><div class="spacer"></div>
        <button class="primary" data-act="alias-save">Salva alias</button></div>` : ''}
      <div id="alias-err" class="small" style="color:var(--err)"></div>
    </div>`);
}

function modalNewCompetizione() {
  const sports = api.sports() || [];
  if (!sports.length) {
    openModal(`
      <h2>Prima serve uno sport</h2>
      <p class="muted small">Le competizioni vivono sotto uno sport della tua libreria.
        Crealo in <a href="#/mercati">Mercati Betfair</a>, poi torna qui.</p>
      <div class="foot"><button data-act="close">Ho capito</button></div>`);
    return;
  }
  openModal(`
    <h2>Nuova competizione</h2>
    <div class="stack" style="margin-top:16px">
      <div><label>Sport</label>
        <select id="nc-sport">${sports.map(s =>
          `<option value="${esc(s.slug)}">${esc(s.nome)}</option>`).join('')}</select></div>
      <div><label>Nome della competizione</label>
        <input id="nc-nome" placeholder="Serie A" maxlength="120"></div>
      <div id="nc-err" class="small" style="color:var(--err)"></div>
    </div>
    <div class="foot">
      <button data-act="close">Annulla</button>
      <button class="primary" data-act="comp-create">Salva</button>
    </div>`);
}

function modalNewSorgente() {
  openModal(`
    <h2>Aggiungi sorgente</h2>
    <p class="muted small">Il nome del canale o della fonte, es. <em>test 1</em>. Vale per
      tutte le competizioni: qui compilerai solo gli alias di questa.</p>
    <div style="margin-top:16px">
      <label>Nome della sorgente</label>
      <input id="nsrc-nome" placeholder="test 1" maxlength="120">
      <div id="nsrc-err" class="small" style="color:var(--err);margin-top:8px"></div>
    </div>
    <div class="foot">
      <button data-act="close">Annulla</button>
      <button class="primary" data-act="src-create">Salva</button>
    </div>`);
}

/* ---------------------------------------------- dettaglio parser: wizard */

// Stato del wizard, vivo solo in memoria: step 0 = condizione, 1..14 = colonne, 15 = riepilogo.
let wiz = null;

function initWiz(p) {
  if (wiz && wiz.parserId === p.slug) return;
  const colonne = p.config.columns || {};
  const configured = COLUMNS.some(c => colonne[c] && colonne[c].source !== 'empty');
  const campione = api.sampleMessage(p.slug);
  // Una config appena nata non ha né condizione né colonne: il draft parte
  // dalla forma piena, così ogni passo del wizard trova la sua regola.
  const draft = {
    match: p.config.match || { type: 'contains', value: '' },
    columns: Object.fromEntries(COLUMNS.map(c => [c, colonne[c] || { source: 'empty' }])),
  };
  // La provenienza «Da mercati Betfair» (#33) viaggia nella config e si
  // conserva: riaprire il parser non deve far perdere il riferimento che il
  // server ha gia' validato.
  if (p.config.betfair) draft.betfair = p.config.betfair;
  wiz = {
    parserId: p.slug,
    step: campione ? (configured ? 15 : 0) : 0,
    started: !!campione,
    draft: JSON.parse(JSON.stringify(draft)),
    message: campione,
    mode: 'message',
    pick: null,
    test: null,
    bfSport: null,   // sport aperto nel passo «Da mercati Betfair»
    bfMarket: null,  // mercato scelto al passo ①
    // I valori scelti dalla libreria, per la verifica di coerenza al
    // salvataggio: al riapri, sono le costanti gia' validate dal server.
    bfValori: p.config.betfair ? {
      MarketType: (colonne.MarketType || {}).value,
      MarketName: (colonne.MarketName || {}).value,
      SelectionName: (colonne.SelectionName || {}).value,
    } : null,
  };
}

// Le tre colonne che la scelta dalla libreria compila insieme.
const TRIO_BETFAIR = ['MarketType', 'MarketName', 'SelectionName'];

// Il riferimento `betfair` resta nella config SOLO se le tre costanti sono
// ancora i valori scelti dalla libreria: qualunque strada abbia preso la
// modifica (valore fisso riscritto, frammento, regex, svuota), qui c'e' il
// punto di passaggio unico — il salvataggio — e un riferimento non piu' vero
// si toglie invece di farsi rifiutare dal server con un 422 criptico.
function coerenzaBetfair() {
  if (!wiz || !wiz.draft.betfair) return;
  const valori = wiz.bfValori || {};
  const coerente = TRIO_BETFAIR.every(c => {
    const r = wiz.draft.columns[c] || {};
    return r.source === 'constant' && r.value === valori[c];
  });
  if (!coerente) { delete wiz.draft.betfair; wiz.bfValori = null; }
}

// Lo sport corrente del passo «Da mercati Betfair»: quello scelto, o il primo.
// Fonte unica (regola 3): chi CARICA i mercati e chi li LEGGE devono decidere
// allo stesso modo, o il wizard mostra la lista di uno sport mai caricato —
// in silenzio. Prima l'espressione viveva in tre siti; CodeRabbit, PR #55.
function sportDelWizard() {
  const sports = api.sports() || [];
  return wiz.bfSport || (sports[0] && sports[0].slug) || null;
}

// Carica sport e mercati per il passo «Da mercati Betfair» del wizard, poi
// ridisegna — se nel frattempo non e' partito un altro render (guardia
// `generazione`, come le viste async).
async function caricaLibreriaWizard() {
  const invocazione = generazione;
  try {
    await api.loadSports();
    const slug = sportDelWizard();
    if (slug) await api.loadMercati(slug);
  } catch (e) {
    // La sessione scaduta si tratta come ovunque: `fallita` ricarica al login.
    // Ogni altro errore lascia la vista com'e' — libreria vuota o caricamento
    // fermo — senza esplodere in console. CodeRabbit, PR #55.
    if (e && e.status === 401) { fallita(e); return; }
  }
  if (invocazione === generazione) render();
}

const HINTS = {
  Provider: 'Chi manda il segnale. Di norma resta vuota: la scrive chi legge, non chi invia.',
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
  Points: 'Moltiplicatore dello stake di XTrader. Con 2, una puntata da 1 € diventa 2 €. '
        + 'Vuoto se non lo usi.',
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
  // `complete`, non `matched`: un segnale riconosciuto ma senza le colonne
  // obbligatorie non viene scritto, e l'anteprima non deve promettere il contrario.
  const { complete, row } = runParser(wiz.message, wiz.draft);
  return complete ? toCsv(row) : headerOnlyCsv();
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
  return { source: 'line', anchor: cutByCodePoint(line, 14), part: 'whole',
           transforms: [{ op: 'trim' }] };
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
             placeholder="es. OVER_UNDER_15"></div>`;
  } else if (mode === 'betfair') {
    // Il flusso a DUE PASSI della #33: ① mercato (MarketName si compila da
    // solo) → ② risultato, scelto fra le SOLE selezioni che l'utente ha
    // creato. Tutto dalla cache sincrona: il caricamento parte da `wiz-mode`.
    const sports = api.sports();
    if (sports === null) {
      body = '<div class="dim">Caricamento della tua libreria…</div>';
    } else if (!sports.length) {
      body = `<div class="empty"><p>La tua libreria è vuota.</p>
        <p class="dim small">Crea sport e mercati in
        <a href="#/mercati">Mercati Betfair</a>, poi torna qui: li sceglierai
        dalla lista invece di riscriverli in ogni parser.</p></div>`;
    } else {
      const sportScelto = sportDelWizard();
      const mercati = api.mercatiOf(sportScelto);
      const mercato = (mercati || []).find(m => m.id === wiz.bfMarket) || null;
      const rif = wiz.draft.betfair || null;
      const frag = (attivo, dati, testo) =>
        `<button class="frag ${attivo ? 'picked' : ''}" ${dati}>${testo}</button>`;
      body = `<div class="card stack">
        ${sports.length > 1 ? `<div><label>Sport</label>
          <select id="bf-sport">${sports.map(s => `<option value="${esc(s.slug)}"
            ${s.slug === sportScelto ? 'selected' : ''}>${esc(s.nome)}</option>`).join('')}
          </select></div>` : ''}
        <div><label>① Scegli il mercato — MarketName si compila da solo</label>
          <div class="frag-list">${mercati === null
            ? '<div class="dim small">Caricamento…</div>'
            : mercati.map(m => frag(mercato && mercato.id === m.id,
                `data-act="bf-market" data-id="${m.id}"`,
                `<span class="mono">${esc(m.marketType)}</span> · ${esc(m.marketName)}`)).join('')
              || '<div class="dim small">Nessun mercato in questo sport.</div>'}</div></div>
        ${mercato ? `<div><label>② Scegli il risultato — solo le selezioni che hai creato</label>
          <div class="frag-list">${mercato.selezioni.map(s => haSegnaposto(s.selectionName)
            ? `<button class="frag" disabled>${esc(s.selectionName)}
                <span class="dim small">— spendibile con la sorgente squadre (#34)</span></button>`
            : frag(rif && rif.market_id === mercato.id && rif.selection_id === s.id,
                   `data-act="bf-selection" data-id="${s.id}"`,
                   esc(s.selectionName))).join('')
            || '<div class="dim small">Questo mercato non ha ancora selezioni.</div>'}</div></div>` : ''}
        ${rif ? `<div class="banner ok">Scelto dalla libreria: le colonne
          <span class="mono">MarketType</span>, <span class="mono">MarketName</span> e
          <span class="mono">SelectionName</span> sono compilate. Prosegui con Avanti.</div>` : ''}
      </div>`;
    }
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
      ${col === 'MarketType' ? tab('betfair', 'Da mercati Betfair') : ''}
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

function stepReview() {
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
      La prova gira <strong>sul server</strong>, con lo stesso motore del webhook,
      e non scrive nulla nel feed.
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
        <div class="row"><span class="pill ${t.complete ? 'on' : 'no'}">${
          t.errore ? esc(t.errore)
          : t.complete ? 'Riconosciuto'
          : !t.matched ? 'Ignorato: la condizione non corrisponde'
          : `Riconosciuto ma incompleto: manca ${(t.missing || []).join(', ')}`
        }</span></div>
        ${(t.scarti || []).length ? `<div class="banner warn" id="test-scarti">
          ${t.scarti.map(esc).join('<br>')}
        </div>` : ''}
        ${t.matched && !t.complete && (t.missing || []).length ? `<div class="banner warn">
          Nessuna riga scritta nel feed: senza
          <span class="mono">${esc((t.missing || []).join(', '))}</span>
          la riga sarebbe formalmente valida e priva di senso per XTrader.
        </div>` : ''}
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
        <button data-act="ai-suggest">Suggerisci mappatura</button>
        <div class="spacer"></div>
      </div>
      <p class="dim small" style="margin:0">
        "Suggerisci mappatura" propone una configurazione di partenza analizzando il messaggio.
        Puoi sempre correggere ogni colonna a mano.
      </p>
    </div>`;
}

function wizardPane() {
  if (!wiz.started) return stepPaste();
  if (wiz.step === 0) return stepMatch();
  if (wiz.step === 15) return stepReview();
  return stepColumn(wiz.step - 1);
}

function viewParser() {
  const p = api.getParser(route.id);
  if (!p) { shell('<div class="empty">Parser non trovato.</div>'); return; }
  initWiz(p);

  const tab = (t, l) => `<a href="#/parsers/${encodeURIComponent(p.slug)}/${t}"
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
        <div class="chat">${wizardPane()}</div>
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
              L'anteprima è indicativa: fa fede la prova sul server, nel riepilogo.
            </p>
          </div>
          <div class="card">
            <strong class="small">CSV che riceverà XTrader</strong>
            <pre class="csv-out" id="live-csv" style="margin-top:10px">${esc(livePreviewCsv())}</pre>
          </div>
        </div>
      </div>`;
  } else if (route.tab === 'chats') {
    body = paneProssimamente('Le chat Telegram si assegnano ai parser da qui',
      'L\'associazione chat → parser arriva con uno dei prossimi aggiornamenti. '
      + 'Oggi i messaggi delle chat autorizzate vengono valutati da tutti i tuoi parser attivi.');
  } else {
    body = paneProssimamente('Il registro dei messaggi di questo parser',
      'Qui vedrai ogni messaggio ricevuto e il motivo per cui ha prodotto — o non ha '
      + 'prodotto — una riga nel feed.');
  }

  shell(`
    <div class="crumb"><a href="#/parsers">Parser</a> / ${esc(p.titolo)}</div>
    <div class="head"><div>
      <h1>${esc(p.titolo)}</h1>
      <p class="mono">${esc(p.slug)}</p>
    </div><div class="spacer"></div>
      <span class="pill ${p.active ? 'on' : 'off'}">${p.active ? 'attivo' : 'sospeso'}</span>
      <button class="small" data-act="toggle-active" data-id="${esc(p.slug)}">
        ${p.active ? 'Sospendi' : 'Riattiva'}</button>
      <button class="small danger" data-act="del-parser" data-id="${esc(p.slug)}">Elimina</button>
    </div>
    <div class="row" style="gap:4px;margin-bottom:18px;border-bottom:1px solid var(--line);padding-bottom:12px">
      ${tab('config', 'Configurazione')}${tab('chats', 'Chat assegnate')}${tab('logs', 'Log')}
    </div>
    ${body}`);
}

function paneProssimamente(titolo, testo) {
  return `<div class="empty"><p><strong>${esc(titolo)}</strong></p>
    <p class="muted small">${esc(testo)}</p>
    <span class="pill">prossimamente</span></div>`;
}

/* ------------------------------------------------------------------- feed */

// Il feed e il suo token sono DELL'UTENTE, non del singolo parser: un solo URL
// da incollare in XTrader, e ogni parser attivo scrive la propria riga lì.
function viewFeed() {
  const u = api.me();
  shell(`
    <div class="head"><div><h1>Feed CSV</h1></div></div>
    <div class="stack">
      <div class="card stack">
        <strong class="small">URL del feed per XTrader</strong>
        ${api.hasToken() ? `
          <div class="mono small" style="word-break:break-all">${esc(api.feedUrl() || '')}</div>
          <p class="dim small" style="margin:0">
            Il token completo è visibile solo al momento della generazione: qui vedi soltanto
            il prefisso <span class="mono">${esc(u.token_prefix)}</span>. L'URL vero è quello
            copiato quando hai generato il token.
          </p>
          <div class="row">
            <button data-act="ask-token">Rigenera token</button>
          </div>
          <p class="dim small" style="margin:0">
            Rigenerare revoca il token precedente: XTrader smette di leggere finché non
            incolli l'URL nuovo.
          </p>` : `
          <p class="muted small" style="margin:0">
            Non hai ancora un token: il feed non è raggiungibile. Generane uno e incolla
            l'URL completo in XTrader.
          </p>
          <div class="row"><button class="primary" data-act="ask-token">Genera token</button></div>`}
      </div>
      <div class="card">
        <strong class="small">Come funziona</strong>
        <p class="dim small" style="margin:10px 0 0">
          Ogni segnale resta nel feed 90 secondi, poi il CSV torna alla sola intestazione.
          Ogni parser ha la propria riga e il proprio timer, indipendente dagli altri.
          Il feed è UTF-8 con BOM, come XTrader lo pretende.
        </p>
      </div>
    </div>`);
}

/* --------------------------------------------------- chat e log (globali) */

function viewChats() {
  shell(`
    <div class="head"><div><h1>Chat Telegram</h1></div></div>
    ${paneProssimamente('Collega qui i tuoi gruppi e canali',
      'La verifica delle chat con il codice usa-e-getta arriva con uno dei prossimi '
      + 'aggiornamenti. Oggi le chat autorizzate le collega l\'amministratore.')}`);
}

function viewLogs() {
  shell(`
    <div class="head"><div><h1>Log messaggi</h1></div></div>
    ${paneProssimamente('Il registro dei messaggi ricevuti',
      'Qui vedrai ogni messaggio arrivato dalle tue chat, con l\'esito: riconosciuto, '
      + 'ignorato, o scartato con il motivo.')}`);
}

/* -------------------------------------------------------------- settings */

function viewSettings() {
  const u = api.me();
  const s = api.settings();
  const bot = s && s.bot_username ? `https://t.me/${s.bot_username}` : null;
  shell(`
    <div class="head"><div><h1>Impostazioni</h1></div></div>
    <div class="stack">
      <div class="card stack">
        <strong class="small">Account</strong>
        <table><tbody>
          <tr><td class="muted">Nome</td><td>${esc(u.nome || '—')}</td></tr>
          <tr><td class="muted">Stato</td><td>${pillStato(u)}</td></tr>
          <tr><td class="muted">Profilo negli URL</td><td class="mono">${esc(u.slug || 'non ancora creato')}</td></tr>
          <tr><td class="muted">Token del feed</td><td class="mono">${
            u.token_prefix ? esc(u.token_prefix) + '… (attivo)' : 'non generato'}</td></tr>
        </tbody></table>
      </div>
      <div class="card stack">
        <strong class="small">Bot del servizio</strong>
        ${bot ? copyRow(bot, 'copy') : '<p class="muted small" style="margin:0">Nessun bot configurato.</p>'}
        <p class="dim small" style="margin:0">
          Impostato dall'amministratore e uguale per tutti gli utenti.
          Il token del bot resta sul server: la web app non lo riceve e non lo conserva.
        </p>
      </div>
    </div>`);
}

/* ------------------------------------------------------------- azioni UI */

const actions = {
  async 'login-password'() {
    const errBox = document.getElementById('login-err');
    errBox.textContent = '';
    try {
      await api.loginPassword(document.getElementById('login-user').value.trim(),
                              document.getElementById('login-pass').value);
      erroreLogin = null;
      go('#/');
      render();
    } catch (e) { errBox.textContent = e.message; }
  },
  async logout() {
    try { await api.logout(); } catch { /* il cookie muore comunque col reload */ }
    wiz = null;
    botAccesso = null;
    go('#/');
    render();
  },

  async 'request-access'() {
    const errBox = document.getElementById('accesso-err');
    try {
      const r = await api.requestAccess();
      botAccesso = r.bot;
      render();
    } catch (e) {
      // Il 409 («richiesta gia' in corso» / «gia' attivo») non e' un guasto:
      // lo stato vero si rilegge e la vista giusta si disegna da sola.
      if (e.status === 409) { location.reload(); return; }
      if (errBox) errBox.textContent = e.message;
      else fallita(e);
    }
  },

  copy(el) { copy(el.dataset.val); },
  close() { closeModal(); },

  'new-parser'() { modalNewParser(); },
  async 'create-parser'() {
    const titolo = document.getElementById('np-name').value;
    try {
      const p = await api.createParser(titolo);
      closeModal();
      wiz = null;
      go(`#/parsers/${encodeURIComponent(p.slug)}/config`);
      render();
    } catch (e) { document.getElementById('np-err').textContent = e.message; }
  },
  async 'toggle-active'(el) {
    const p = api.getParser(el.dataset.id);
    try { await api.updateParser(p.slug, { active: !p.active }); } catch (e) { fallita(e); return; }
    render();
  },
  async 'del-parser'(el) {
    const p = api.getParser(el.dataset.id);
    openModal(`<h2>Eliminare "${esc(p.titolo)}"?</h2>
      <p class="muted small">La configurazione sparisce dal server. L'operazione non è reversibile.</p>
      <div class="foot"><button data-act="close">Annulla</button>
        <button class="danger" data-act="del-parser-ok" data-id="${esc(p.slug)}">Elimina</button></div>`);
  },
  async 'del-parser-ok'(el) {
    try { await api.deleteParser(el.dataset.id); } catch (e) { fallita(e); return; }
    closeModal(); wiz = null; go('#/parsers'); render();
  },

  // ------- mercati Betfair (#33)
  'sport-new'() { modalNewSport(); },
  async 'sport-create'() {
    const nome = document.getElementById('ns-nome').value;
    try { await api.createSport(nome); }
    catch (e) { document.getElementById('ns-err').textContent = e.message; return; }
    closeModal(); render();
  },
  'sport-del'(el) {
    openModal(`<h2>Eliminare lo sport?</h2>
      <p class="muted small">«${esc(el.dataset.nome)}» sparisce con tutti i suoi mercati
        e le selezioni. I parser già salvati non cambiano: le loro regole restano.</p>
      <div class="foot"><button data-act="close">Annulla</button>
        <button class="danger" data-act="sport-del-ok" data-id="${esc(el.dataset.id)}">Elimina</button></div>`);
  },
  async 'sport-del-ok'(el) {
    try { await api.deleteSport(el.dataset.id); } catch (e) { fallita(e); return; }
    closeModal(); go('#/mercati'); render();
  },
  'mercato-new'() { modalNewMercato(); },
  async 'mercato-create'() {
    const marketType = document.getElementById('nm-type').value;
    const marketName = document.getElementById('nm-name').value;
    try { await api.createMercato(route.id, { marketType, marketName, selections: [] }); }
    catch (e) { document.getElementById('nm-err').textContent = e.message; return; }
    closeModal(); render();
  },
  'mercato-del'(el) {
    openModal(`<h2>Eliminare il mercato?</h2>
      <p class="muted small"><span class="mono">${esc(el.dataset.nome)}</span> e le sue
        selezioni. I parser già salvati non cambiano.</p>
      <div class="foot"><button data-act="close">Annulla</button>
        <button class="danger" data-act="mercato-del-ok" data-id="${esc(el.dataset.id)}">Elimina</button></div>`);
  },
  async 'mercato-del-ok'(el) {
    try { await api.deleteMercato(route.id, el.dataset.id); }
    catch (e) { fallita(e); return; }
    closeModal(); go(`#/mercati/${encodeURIComponent(route.id)}`); render();
  },
  async 'sel-add'() {
    const campo = document.getElementById('sel-nome');
    try { await api.createSelezione(route.id, route.tab, campo.value); }
    catch (e) { document.getElementById('sel-err').textContent = e.message; return; }
    render();
  },
  async 'sel-del'(el) {
    try { await api.deleteSelezione(route.id, route.tab, el.dataset.id); }
    catch (e) { fallita(e); return; }
    render();
  },

  // ------- sorgenti squadre (#34)
  'comp-new'() { modalNewCompetizione(); },
  async 'comp-create'() {
    const sport = document.getElementById('nc-sport').value;
    const nome = document.getElementById('nc-nome').value;
    try { await api.createCompetizione(sport, nome); }
    catch (e) { document.getElementById('nc-err').textContent = e.message; return; }
    closeModal(); render();
  },
  'comp-del'(el) {
    openModal(`<h2>Eliminare la competizione?</h2>
      <p class="muted small">«${esc(el.dataset.nome)}» sparisce con le sue squadre Betfair
        e gli alias relativi in tutte le sorgenti. Le sorgenti restano.</p>
      <div class="foot"><button data-act="close">Annulla</button>
        <button class="danger" data-act="comp-del-ok" data-id="${esc(el.dataset.id)}">Elimina</button></div>`);
  },
  async 'comp-del-ok'(el) {
    try { await api.deleteCompetizione(el.dataset.id); } catch (e) { fallita(e); return; }
    closeModal(); go('#/squadre'); render();
  },
  async 'sq-add'() {
    const campo = document.getElementById('sq-nome');
    try { await api.createSquadra(route.id, campo.value); }
    catch (e) { document.getElementById('sq-err').textContent = e.message; return; }
    render();
  },
  'sq-del'(el) {
    // La «× squadra» e' l'azione CONDIVISA (deciso 13/08): tocca tutte le
    // sorgenti, quindi chiede conferma — la «⌫» invece e' locale e non la chiede.
    openModal(`<h2>Eliminare la squadra?</h2>
      <p class="muted small">«${esc(el.dataset.nome)}» sparisce dalla competizione e dai
        suoi alias in <strong>tutte le sorgenti</strong>.</p>
      <div class="foot"><button data-act="close">Annulla</button>
        <button class="danger" data-act="sq-del-ok" data-id="${esc(el.dataset.id)}">Elimina</button></div>`);
  },
  async 'sq-del-ok'(el) {
    try { await api.deleteSquadra(route.id, el.dataset.id); }
    catch (e) { fallita(e); return; }
    closeModal(); render();
  },
  'src-new'() { modalNewSorgente(); },
  async 'src-create'() {
    const nome = document.getElementById('nsrc-nome').value;
    try { await api.createSorgente(nome); }
    catch (e) { document.getElementById('nsrc-err').textContent = e.message; return; }
    closeModal(); render();
  },
  'src-ren'(el) {
    openModal(`<h2>Rinomina sorgente</h2>
      <div style="margin-top:16px">
        <label>Nuovo nome</label>
        <input id="rsrc-nome" value="${esc(el.dataset.nome)}" maxlength="120">
        <div id="rsrc-err" class="small" style="color:var(--err);margin-top:8px"></div>
      </div>
      <div class="foot"><button data-act="close">Annulla</button>
        <button class="primary" data-act="src-ren-ok">Salva</button></div>`);
  },
  async 'src-ren-ok'() {
    const nome = document.getElementById('rsrc-nome').value;
    try { await api.renameSorgente(route.tab, nome); }
    catch (e) { document.getElementById('rsrc-err').textContent = e.message; return; }
    closeModal(); render();
  },
  'src-del'(el) {
    openModal(`<h2>Eliminare la sorgente?</h2>
      <p class="muted small">«${esc(el.dataset.nome)}» sparisce con i SUOI alias, in tutte
        le competizioni. Le squadre Betfair restano dove sono.</p>
      <div class="foot"><button data-act="close">Annulla</button>
        <button class="danger" data-act="src-del-ok">Elimina</button></div>`);
  },
  async 'src-del-ok'() {
    try { await api.deleteSorgente(route.tab); } catch (e) { fallita(e); return; }
    closeModal(); go(`#/squadre/${route.id}`); render();
  },
  async 'alias-save'() {
    const coppie = {};
    document.querySelectorAll('[data-squadra]').forEach(el => {
      coppie[el.dataset.squadra] = el.value;
    });
    try { await api.saveAlias(route.id, route.tab, coppie); }
    catch (e) { document.getElementById('alias-err').textContent = e.message; return; }
    toast('Alias salvati.');
    render();
  },
  async 'alias-clear'(el) {
    // La ⌫ salva la tabella COME LA VEDI, con questa riga svuotata: mandare la
    // sola coppia svuotata farebbe perdere al re-render le modifiche digitate
    // e non ancora salvate nelle altre righe (GPT-5.5, PR #66).
    const coppie = {};
    document.querySelectorAll('[data-squadra]').forEach(i => {
      coppie[i.dataset.squadra] = i.value;
    });
    coppie[el.dataset.id] = '';
    try { await api.saveAlias(route.id, route.tab, coppie); }
    catch (e) { fallita(e); return; }
    render();
  },

  // ------- wizard
  'start-wizard'() {
    const msg = document.getElementById('paste-msg').value;
    if (!msg.trim()) { toast('Incolla prima un messaggio.'); return; }
    wiz.message = msg;
    wiz.started = true;
    wiz.step = 0;
    api.saveSampleMessage(wiz.parserId, msg);
    render();
  },
  'ai-suggest'() {
    const box = document.getElementById('paste-msg');
    const msg = box ? box.value : wiz.message;
    if (!msg.trim()) { toast('Incolla prima un messaggio.'); return; }
    wiz.message = msg;
    wiz.draft = api.suggest(msg);
    wiz.started = true;
    wiz.step = 15;
    api.saveSampleMessage(wiz.parserId, msg);
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
  'wiz-mode'(el) {
    wiz.mode = el.dataset.mode;
    // PRIMA il render, POI il caricamento: il loader fotografa `generazione`,
    // e col render dopo la fotografia il contatore avanzava e il loader
    // scartava il proprio render di completamento — a cache fredda il passo
    // restava su «Caricamento…» per sempre. Trovato da CodeRabbit (PR #55),
    // riprodotto dal caso «cache fredda» di mercati_flow.py.
    render();
    if (wiz.mode === 'betfair') caricaLibreriaWizard();
  },
  'bf-market'(el) { wiz.bfMarket = Number(el.dataset.id); render(); },
  'bf-selection'(el) {
    const mercato = (api.mercatiOf(sportDelWizard()) || [])
      .find(m => m.id === wiz.bfMarket);
    const selezione = mercato
      && mercato.selezioni.find(s => s.id === Number(el.dataset.id));
    if (!selezione) return;
    wiz.draft.columns.MarketType = { source: 'constant', value: mercato.marketType };
    wiz.draft.columns.MarketName = { source: 'constant', value: mercato.marketName };
    wiz.draft.columns.SelectionName = { source: 'constant', value: selezione.selectionName };
    wiz.draft.betfair = { market_id: mercato.id, selection_id: selezione.id };
    wiz.bfValori = { MarketType: mercato.marketType, MarketName: mercato.marketName,
                     SelectionName: selezione.selectionName };
    toast('MarketType, MarketName e SelectionName compilate dalla libreria.');
    render();
  },
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
    // readCurrentRule() puo' assegnare un OGGETTO NUOVO a wiz.draft.columns[col]
    // (modalita' regex e valore fisso): la regola va riletta dopo, altrimenti si
    // muta una copia orfana e la modifica si perde al render successivo.
    readCurrentRule();
    const rule = wiz.draft.columns[COLUMNS[wiz.step - 1]];
    rule.part = rule.part === 'after' ? 'whole' : 'after';
    if (rule.part === 'after' && !rule.marker) rule.marker = rule.anchor;
    render();
  },
  'toggle-transform'(el) {
    readCurrentRule();
    const rule = wiz.draft.columns[COLUMNS[wiz.step - 1]];
    rule.transforms = rule.transforms || [];
    const op = el.dataset.op;
    const i = rule.transforms.findIndex(t => t.op === op);
    if (i >= 0) rule.transforms.splice(i, 1);
    else rule.transforms.push(op === 'replace_last' || op === 'replace_all'
      ? { op, from: ' v ', to: ' - ' } : { op });
    render();
  },
  async 'wiz-save'() {
    const msg = document.getElementById('test-msg')?.value ?? wiz.message;
    api.saveSampleMessage(wiz.parserId, msg);
    coerenzaBetfair();
    try { await api.updateParser(wiz.parserId, { config: wiz.draft }); }
    catch (e) { fallita(e); return; }
    toast('Configurazione salvata sul server.');
    render();
  },
  async 'run-test'() {
    const msg = document.getElementById('test-msg').value;
    wiz.message = msg;
    api.saveSampleMessage(wiz.parserId, msg);
    coerenzaBetfair();
    // Prima si salva la config, poi si prova: la prova gira sul server, che
    // conosce solo cio' che e' stato salvato — provare un draft non salvato
    // mostrerebbe l'esito di un'altra configurazione.
    try {
      await api.updateParser(wiz.parserId, { config: wiz.draft });
      wiz.test = await api.testParser(wiz.parserId, msg);
    } catch (e) { fallita(e); return; }
    render();
  },

  // ------- token del feed (dell'utente)
  // ------- pannello admin
  async 'approva-richiesta'(el) {
    const campo = document.getElementById(`giorni-${el.dataset.id}`);
    const giorni = Number(campo && campo.value);
    if (!Number.isInteger(giorni) || giorni < 1) {
      toast('Scrivi i giorni da concedere (un numero intero).');
      if (campo) campo.focus();
      return;
    }
    let r;
    try { r = await api.adminApprove(el.dataset.id, giorni); }
    catch (e) { fallita(e); return; }
    // L'invio fallito NON si ingoia (Issue #7): l'accesso resta concesso —
    // e' una decisione — ma il proprietario deve sapere che l'avviso non e'
    // arrivato, o crede di aver avvisato un cliente che non sa niente.
    esitoRichieste = r.notificato
      ? `<div class="banner" id="esito-decisione">Accesso attivato: ${esc(r.giorni_rimasti)}
           giorni. Il cliente è stato avvisato su Telegram.</div>`
      : `<div class="banner warn" id="esito-decisione">Accesso attivato
           (${esc(r.giorni_rimasti)} giorni), <strong>ma l'avviso Telegram NON è
           partito</strong>${r.motivo ? ` — ${esc(r.motivo)}` : ''}. Contatta il
           cliente a mano: per lui non è cambiato niente finché non lo sa.</div>`;
    render();
  },
  'rifiuta-richiesta'(el) {
    openModal(`<h2>Rifiutare la richiesta?</h2>
      <p class="muted small">Il cliente torna «registrato» e potrà chiedere di nuovo:
      un rifiuto non è una sospensione.</p>
      <div class="foot"><button data-act="close">Annulla</button>
        <button class="danger" data-act="rifiuta-richiesta-ok" data-id="${esc(el.dataset.id)}">Rifiuta</button></div>`);
  },
  async 'rifiuta-richiesta-ok'(el) {
    closeModal();
    try { await api.adminReject(el.dataset.id); } catch (e) { fallita(e); return; }
    esitoRichieste = '<div class="banner" id="esito-decisione">Richiesta rifiutata: il cliente può richiedere di nuovo.</div>';
    render();
  },
  async 'giro-promemoria'() {
    const dove = document.getElementById('esito-promemoria');
    try {
      const r = await api.adminReminder();
      if (dove) {
        // I motivi dei falliti si mostrano, non solo il conteggio: un giro
        // con 3 falliti senza il perche' obbligherebbe ad andare nei log
        // (nota di Claude Fable 5 sulla PR #53). Il motivo e' il TIPO
        // dell'errore, mai il token: e' il contratto di invia_messaggio.
        const falliti = r.falliti || [];
        dove.textContent = 'avvisati: ' + (r.avvisati || []).length
          + ' · falliti: ' + falliti.length
          + (falliti.length
             ? ' — ' + falliti.map(f => f.motivo || 'motivo ignoto').join('; ')
             : '');
      }
    } catch (e) { fallita(e); }
  },

  'ask-token'() {
    if (!api.hasToken()) { actions['generate-token'](); return; }
    openModal(`<h2>Rigenerare il token?</h2>
      <p class="muted small">Il token attuale smette subito di funzionare: XTrader non
      leggerà più il feed finché non incolli l'URL nuovo.</p>
      <div class="foot"><button data-act="close">Annulla</button>
        <button class="primary" data-act="generate-token">Rigenera</button></div>`);
  },
  async 'generate-token'() {
    let r;
    try { r = await api.generateToken(); } catch (e) { fallita(e); return; }
    openModal(`<h2>Token generato</h2>
      <p class="muted small">Copialo ora: per sicurezza non sarà più mostrato.
      Sul server conserviamo solo il suo hash.</p>
      <div class="stack" style="margin-top:14px">
        <div><label>Token</label>${copyRow(r.token, 'copy')}</div>
        <div><label>URL completo del feed, da incollare in XTrader</label>${copyRow(r.url, 'copy')}</div>
      </div>
      <div class="foot"><button class="primary" data-act="after-token">Ho copiato</button></div>`,
      { wide: true, sticky: true });
  },
  'after-token'() { closeModal(); render(); },
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

  // In modalita' «Da mercati Betfair» le regole le scrive la scelta della
  // selezione (azione bf-selection), non il DOM: qui non c'e' niente da leggere.
  if (wiz.mode === 'betfair') return;
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
  if (e.target.matches('#bf-sport')) {
    // Cambio sport nel passo «Da mercati Betfair»: si azzera il mercato scelto
    // (era dell'altro sport) e si carica la libreria di quello nuovo.
    wiz.bfSport = e.target.value;
    wiz.bfMarket = null;
    api.loadMercati(wiz.bfSport).then(() => render())
      .catch(err => { if (err && err.status === 401) fallita(err); else render(); });
  }
});

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeModal();
});

/* ----------------------------------------------------------------- render */

function render() {
  generazione += 1;
  Object.assign(route, { id: null, tab: 'config' }, parseHash());
  if (!api.me()) { viewLogin(); return; }
  // Il cancello degli stati (#7): chi non e' attivo vede a che punto e' il suo
  // accesso, non un'app vuota. L'amministratore entra sempre — e' lui che
  // approva — e il suo caso sta PRIMA del controllo sullo stato.
  const u = api.me();
  if (!u.admin && u.stato !== 'attivo') { viewAccesso(u); return; }
  // L'esito di una decisione vive solo dentro «Richieste»: cambiando pagina
  // si azzera, o un banner vecchio tornerebbe alla prossima visita.
  if (route.name !== 'richieste') esitoRichieste = null;
  if (route.name === 'richieste') {
    // Il server risponde comunque 404 a chi non e' admin: questo e' solo il
    // riflesso in UI — un cliente che digita l'hash vede la dashboard.
    return u.admin ? viewRichieste() : viewOverview();
  }
  if (route.name === 'parsers') return viewParsers();
  if (route.name === 'parser') return viewParser();
  if (route.name === 'mercati') return viewMercati();
  if (route.name === 'squadre') return viewSquadre();
  if (route.name === 'feed') return viewFeed();
  if (route.name === 'chats') return viewChats();
  if (route.name === 'logs') return viewLogs();
  if (route.name === 'settings') return viewSettings();
  return viewOverview();
}

// Il primo render aspetta il boot: prima la cache (settings, sessione, parser),
// poi le viste. Un errore di boot — il server non risponde, il ritorno da
// Telegram non valido — finisce sulla pagina di login, non in console.
(async () => {
  try {
    erroreLogin = await api.boot();
  } catch (e) {
    erroreLogin = e.message;
  }
  render();
})();
