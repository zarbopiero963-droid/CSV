// Il layer di accesso al backend VERO (#32). Le viste parlano solo con questo
// modulo: la copia dimostrativa a file unico usa invece `api_finta.js`, che
// espone la stessa superficie su localStorage — e' il motivo per cui i nomi
// qui sotto non si cambiano senza cambiare anche quella.
//
// Due regole di forma, entrambe eredita' del backend:
//
// - la CACHE e' sincrona. Le viste leggono `me()`, `settings()` e `getParser()`
//   senza `await`, perche' ridisegnare una vista non deve rifare un giro di
//   rete; la cache si riempie in `boot()` e si aggiorna a ogni scrittura,
//   che restituisce sempre lo stato nuovo del server, mai una copia locale
//   «ottimista» che potrebbe divergere.
// - nessun token viene MAI conservato qui. Il token del feed esiste in chiaro
//   solo nella risposta di `POST /api/me/token`, passa al chiamante e sparisce:
//   in cache resta il prefisso, che il server stesso considera pubblicabile.

import { suggestConfig } from './engine.js';

const stato = {
  me: null,        // la risposta di GET /api/me, o null senza sessione
  settings: null,  // la risposta di GET /api/settings (pubblica, sempre presente)
  parsers: [],     // la risposta di GET /api/me/parsers
};

/* ------------------------------------------------------------------ http */

// Un errore HTTP diventa un Error col `detail` del server: e' gia' una frase
// in italiano pensata per l'utente (401 «sessione assente», 422 coi tetti...).
async function http(metodo, percorso, corpo) {
  let risposta;
  try {
    risposta = await fetch(percorso, {
      method: metodo,
      headers: corpo === undefined ? {} : { 'Content-Type': 'application/json' },
      body: corpo === undefined ? undefined : JSON.stringify(corpo),
    });
  } catch {
    throw new Error('il server non risponde: controlla la connessione');
  }
  if (!risposta.ok) {
    let dettaglio = '';
    try { dettaglio = (await risposta.json()).detail || ''; } catch { /* non JSON */ }
    const errore = new Error(dettaglio || `errore ${risposta.status}`);
    errore.status = risposta.status;
    throw errore;
  }
  return risposta.json();
}

/* ------------------------------------------------------------------ boot */

// Il ritorno dal login Telegram in modalita' redirect: oauth.telegram.org
// rimanda qui con `#tgAuthResult=<base64url del JSON firmato>`. Si consuma
// PRIMA di leggere la sessione, e il frammento si toglie dall'URL comunque
// vada: un hash firmato che resta nella barra finirebbe nella cronologia e
// in ogni copia-incolla dell'indirizzo.
async function consumaRitornoTelegram() {
  // Il `=` nella classe: Telegram manda il valore senza padding, ma un valore
  // paddato troncato al primo `=` diventerebbe «non leggibile» su un login
  // valido. La catena replace+atob gestisce entrambe le forme (CodeRabbit, #50).
  const pezzo = (location.hash.match(/tgAuthResult=([A-Za-z0-9_=-]+)/) || [])[1];
  if (!pezzo) return null;
  history.replaceState(null, '', location.pathname + location.search);
  let campi;
  try {
    const base64 = pezzo.replace(/-/g, '+').replace(/_/g, '/');
    // `atob` restituisce una stringa di BYTE (un carattere per byte): passarla
    // a JSON.parse trasforma «Pièro» in mojibake, il server ricalcola l'HMAC
    // su valori alterati e OGNI login con un nome non ASCII fallisce. I byte
    // vanno decodificati come UTF-8. [REAL_FINDING] di GPT-5.6 Sol, PR #50.
    const byte = Uint8Array.from(atob(base64), c => c.charCodeAt(0));
    campi = JSON.parse(new TextDecoder().decode(byte));
  } catch {
    return 'risposta di Telegram non leggibile: riprova';
  }
  try {
    await http('POST', '/api/login/telegram', campi);
    return null;
  } catch (e) {
    return e.message;
  }
}

// Riempie la cache prima del primo render. Restituisce l'eventuale errore del
// ritorno da Telegram, che la pagina di login deve mostrare: un login fallito
// in silenzio e' il sintomo che la #23 e' esistita per chiudere.
export async function boot() {
  const erroreLogin = await consumaRitornoTelegram();
  stato.settings = await http('GET', '/api/settings');
  await ricaricaSessione();
  return erroreLogin;
}

async function ricaricaSessione() {
  try {
    stato.me = await http('GET', '/api/me');
  } catch {
    stato.me = null;
    stato.parsers = [];
    return;
  }
  stato.parsers = await http('GET', '/api/me/parsers');
}

/* --------------------------------------------------------------- sessione */

export function me() { return stato.me; }
export function settings() { return stato.settings; }

export async function loginPassword(username, password) {
  await http('POST', '/api/login/password', { username, password });
  await ricaricaSessione();
}

// Il link «Accedi con Telegram» in modalita' redirect: e' l'unica modalita'
// del login ufficiale che non richiede lo script di telegram.org, che le
// regole del repository vietano (nessun CDN, nessuna dipendenza esterna).
// Vuole il `bot_id` NUMERICO — non lo username — e un `origin` che combaci
// col dominio configurato sul bot via BotFather (/setdomain).
export function telegramAuthUrl() {
  const s = stato.settings;
  if (!s || !s.bot_id) return null;
  const ritorno = location.origin + location.pathname;
  return 'https://oauth.telegram.org/auth'
    + '?bot_id=' + encodeURIComponent(s.bot_id)
    + '&origin=' + encodeURIComponent(location.origin)
    + '&request_access=write'
    + '&return_to=' + encodeURIComponent(ritorno);
}

export async function logout() {
  await http('POST', '/api/logout');
  stato.me = null;
  stato.parsers = [];
}

/* --------------------------------------------------- accesso su approvazione */

// «Richiedi accesso» (#7): nessun modulo, il server sa gia' chi sei dalla
// sessione. La risposta porta il DEEP LINK del bot (`t.me/<bot>?start=...`),
// che non e' un abbellimento: il bot non puo' scrivere per primo, e aprirlo
// e' cio' che rende il cliente raggiungibile per il messaggio di approvazione
// (trappola 1 della Issue #7). Dopo il POST lo stato si rilegge dal server.
export async function requestAccess() {
  const r = await http('POST', '/api/access/request');
  try { stato.me = await http('GET', '/api/me'); } catch { /* cache invariata */ }
  return r;
}

// Il deep link del bot ricostruito dai settings pubblici, per chi torna sulla
// pagina quando la risposta del POST non c'e' piu'. Il payload `accesso` e' lo
// stesso che il server mette in `link_del_bot('accesso')`.
export function botAccessoUrl() {
  const s = stato.settings;
  if (!s || !s.bot_username) return null;
  return 'https://t.me/' + s.bot_username + '?start=accesso';
}

/* ----------------------------------------------------------- pannello admin */

// Le rotte del pannello (#7, PR 10). Rispondono 404 — non 403 — a chi non e'
// l'amministratore: dall'esterno il pannello non esiste. Le viste le chiamano
// solo quando `me().admin` e' vero, quindi un 404 qui e' un guasto, non un
// percorso previsto.

export async function adminRequests() {
  const r = await http('GET', '/api/admin/requests');
  // `|| []`: durante un deploy parziale una risposta 200 senza il campo non
  // deve rompere la vista su `.map` (rilievo di GPT-5.5 sulla PR #53).
  return r.richieste || [];
}

// L'approvazione porta i giorni scritti dall'amministratore (campo libero) e
// la risposta dice ANCHE se l'avviso Telegram e' partito: `notificato: false`
// col motivo non va mai ingoiato — e' la trappola 1 della Issue #7.
export function adminApprove(richiesta, giorni) {
  return http('POST', `/api/admin/requests/${encodeURIComponent(richiesta)}/approva`,
              { giorni });
}

export function adminReject(richiesta) {
  return http('POST', `/api/admin/requests/${encodeURIComponent(richiesta)}/rifiuta`);
}

// Il giro dei promemoria di scadenza: non c'e' uno scheduler, il giro parte
// quando l'amministratore lo chiama. Risposta: {avvisati: [...], falliti: [...]}.
export function adminReminder() {
  return http('POST', '/api/admin/promemoria');
}

/* ----------------------------------------------------------------- parser */

// La lista va sempre al server: e' il modo in cui un secondo dispositivo
// dello stesso account vede i parser creati sul primo.
export async function listParsers() {
  stato.parsers = await http('GET', '/api/me/parsers');
  return stato.parsers;
}

export function getParser(slug) {
  return stato.parsers.find(p => p.slug === slug) || null;
}

export async function createParser(titolo) {
  const parser = await http('POST', '/api/me/parsers',
                            { titolo, config: {}, active: true });
  stato.parsers.push(parser);
  return parser;
}

// La PUT del server vuole il parser INTERO (titolo, config, active): la
// patch parziale si completa qui dalla cache, cosi' le viste continuano a
// mandare solo cio' che cambia.
export async function updateParser(slug, patch) {
  const attuale = getParser(slug);
  if (!attuale) throw new Error('parser non trovato');
  const nuovo = await http('PUT', `/api/me/parsers/${encodeURIComponent(slug)}`, {
    titolo: patch.titolo ?? attuale.titolo,
    config: patch.config ?? attuale.config,
    active: patch.active ?? attuale.active,
  });
  const i = stato.parsers.findIndex(p => p.slug === slug);
  if (i >= 0) stato.parsers[i] = nuovo;
  return nuovo;
}

export async function deleteParser(slug) {
  await http('DELETE', `/api/me/parsers/${encodeURIComponent(slug)}`);
  stato.parsers = stato.parsers.filter(p => p.slug !== slug);
}

// La prova A SECCO sul server: stessa `esegui_parser` del webhook, nessuna
// scrittura nel feed. La risposta porta anche `scarti` — il PERCHE' un valore
// non ha raggiunto il feed (#39/#41) — che la vista deve mostrare.
export function testParser(slug, message) {
  return http('POST', `/api/me/parsers/${encodeURIComponent(slug)}/test`, { message });
}

// Il suggeritore resta nel browser: il motore JS e' gia' qui, e il messaggio
// di esempio non ha motivo di viaggiare prima che l'utente salvi qualcosa.
export function suggest(message) {
  return suggestConfig(message);
}

/* ------------------------------------------------- messaggio di esempio */

// Il messaggio di esempio del wizard vive nel browser: il server non lo
// conserva (non e' un dato del contratto, e' un appunto di lavoro).
const CHIAVE_CAMPIONE = 'xtrelay:campione:';

// La chiave porta l'UTENTE della sessione oltre allo slug. Lo slug dei parser
// e' unico PER UTENTE (`UNIQUE (user_id, slug)` nello schema): su un browser
// condiviso due account possono avere lo stesso slug, e una chiave per solo
// slug faceva leggere al secondo il messaggio Telegram del primo.
// [REAL_FINDING] di GPT-5.6 Sol sulla PR #50. Senza sessione non si legge e
// non si scrive niente: l'appunto appartiene a qualcuno, o non esiste.
function chiaveCampione(slug) {
  if (!stato.me) return null;
  return CHIAVE_CAMPIONE + stato.me.utente + ':' + slug;
}

export function sampleMessage(slug) {
  const chiave = chiaveCampione(slug);
  if (!chiave) return '';
  try { return localStorage.getItem(chiave) || ''; }
  catch { return ''; }
}

export function saveSampleMessage(slug, testo) {
  const chiave = chiaveCampione(slug);
  if (!chiave) return;
  try { localStorage.setItem(chiave, testo); }
  catch { /* modalita' privata: il wizard funziona, l'appunto non sopravvive */ }
}

/* ------------------------------------------------------- feed e token */

// Il token e' DELL'UTENTE, non del parser: un solo URL da incollare in
// XTrader, tutti i parser scrivono li'. Il token in chiaro esiste solo in
// questa risposta: si passa al chiamante e non si conserva.
export async function generateToken() {
  const r = await http('POST', '/api/me/token');
  // Lo stato nuovo (slug appena creato, prefisso) si RILEGGE da /api/me: e' il
  // server la fonte, non un parsing del path `feed` — che accoppierebbe questo
  // layer alla forma dell'URL e si romperebbe in silenzio al primo cambio
  // (bloccante di Claude Fable 5 sulla PR #50). Se la rilettura fallisce, la
  // cache resta com'era: il token appena coniato viene comunque consegnato.
  try { stato.me = await http('GET', '/api/me'); } catch { /* cache invariata */ }
  return {
    token: r.token,
    url: location.origin + r.feed + '?token=' + encodeURIComponent(r.token),
  };
}

// L'URL del feed mostrato quando il token NON e' in mano: il percorso e' vero,
// il token e' il prefisso piu' un segnaposto, e la vista deve dirlo.
export function feedUrl() {
  if (!stato.me || !stato.me.slug) return null;
  const prefisso = stato.me.token_prefix || '';
  return location.origin + '/feed/' + stato.me.slug + '.csv?token='
    + (prefisso ? prefisso + '…' : '…');
}

export function hasToken() {
  return Boolean(stato.me && stato.me.token_prefix);
}

/* -------------------------------------------------- mercati Betfair (#33) */

// La libreria e' PER-UTENTE e vive sul server: qui c'e' solo la cache sincrona
// che il wizard legge dentro un render (che non puo' aspettare la rete).
// `sports()`/`mercatiOf()` restituiscono `null` finche' il rispettivo `load*`
// non e' passato: e' il segnale «mostra Caricamento…», diverso da «vuoto».
stato.sports = null;
stato.mercati = {};   // slug sport -> lista mercati con selezioni annidate

export async function loadSports() {
  stato.sports = (await http('GET', '/api/me/sports')).sports;
  return stato.sports;
}

export function sports() { return stato.sports; }

export async function loadMercati(sport) {
  const r = await http('GET', `/api/me/sports/${encodeURIComponent(sport)}/mercati`);
  stato.mercati[sport] = r.mercati;
  return r.mercati;
}

export function mercatiOf(sport) {
  return Object.prototype.hasOwnProperty.call(stato.mercati, sport)
    ? stato.mercati[sport] : null;
}

export async function createSport(nome) {
  const sport = await http('POST', '/api/me/sports', { nome });
  await loadSports();
  return sport;
}

export async function deleteSport(slug) {
  await http('DELETE', `/api/me/sports/${encodeURIComponent(slug)}`);
  delete stato.mercati[slug];
  await loadSports();
}

export async function createMercato(sport, dati) {
  const mercato = await http(
    'POST', `/api/me/sports/${encodeURIComponent(sport)}/mercati`, dati);
  await loadMercati(sport);
  return mercato;
}

export async function deleteMercato(sport, id) {
  await http('DELETE',
             `/api/me/sports/${encodeURIComponent(sport)}/mercati/${id}`);
  await loadMercati(sport);
}

export async function createSelezione(sport, mercato, selectionName) {
  const selezione = await http(
    'POST',
    `/api/me/sports/${encodeURIComponent(sport)}/mercati/${mercato}/selezioni`,
    { selectionName });
  await loadMercati(sport);
  return selezione;
}

export async function deleteSelezione(sport, mercato, id) {
  await http(
    'DELETE',
    `/api/me/sports/${encodeURIComponent(sport)}/mercati/${mercato}/selezioni/${id}`);
  await loadMercati(sport);
}

/* ------------------------------------------------ sorgenti squadre (#34) */

// Stessa forma della libreria mercati: cache sincrona per le viste, `null`
// finche' il rispettivo `load*` non e' passato («Caricamento…», non «vuoto»).
// Le mutazioni ricaricano cio' che le viste leggono, cosi' un render dopo la
// scrittura trova gia' lo stato vero.
stato.competizioni = null;
stato.dettagliCompetizioni = {};   // id -> {id, nome, sport, squadre, sorgenti}
stato.aliasSorgenti = {};          // `${cid}:${sorgente}` -> righe della tabella

export async function loadCompetizioni() {
  stato.competizioni = (await http('GET', '/api/me/competizioni')).competizioni;
  return stato.competizioni;
}

export function competizioni() { return stato.competizioni; }

export async function loadCompetizione(cid) {
  stato.dettagliCompetizioni[cid] = await http('GET', `/api/me/competizioni/${cid}`);
  return stato.dettagliCompetizioni[cid];
}

export function competizione(cid) {
  return Object.prototype.hasOwnProperty.call(stato.dettagliCompetizioni, cid)
    ? stato.dettagliCompetizioni[cid] : null;
}

export async function loadAlias(cid, sorgente) {
  const r = await http('GET', `/api/me/competizioni/${cid}/alias/${sorgente}`);
  stato.aliasSorgenti[`${cid}:${sorgente}`] = r.alias;
  return r.alias;
}

export function aliasOf(cid, sorgente) {
  const chiave = `${cid}:${sorgente}`;
  return Object.prototype.hasOwnProperty.call(stato.aliasSorgenti, chiave)
    ? stato.aliasSorgenti[chiave] : null;
}

function _scordaAlias(cid) {
  // Le squadre sono cambiate: ogni tabella alias di quella competizione e'
  // stantia, qualunque sorgente mostrasse.
  for (const chiave of Object.keys(stato.aliasSorgenti)) {
    if (chiave.startsWith(`${cid}:`)) delete stato.aliasSorgenti[chiave];
  }
}

export async function createCompetizione(sport, nome) {
  const creata = await http('POST', '/api/me/competizioni', { sport, nome });
  await loadCompetizioni();
  return creata;
}

export async function deleteCompetizione(cid) {
  await http('DELETE', `/api/me/competizioni/${cid}`);
  delete stato.dettagliCompetizioni[cid];
  _scordaAlias(cid);
  await loadCompetizioni();
}

export async function createSquadra(cid, nome) {
  const squadra = await http('POST', `/api/me/competizioni/${cid}/squadre`, { nome });
  _scordaAlias(cid);
  await loadCompetizione(cid);
  return squadra;
}

export async function deleteSquadra(cid, id) {
  await http('DELETE', `/api/me/competizioni/${cid}/squadre/${id}`);
  _scordaAlias(cid);
  await loadCompetizione(cid);
}

export async function createSorgente(nome) {
  return await http('POST', '/api/me/sorgenti-squadre', { nome });
}

export async function renameSorgente(id, nome) {
  return await http('PATCH', `/api/me/sorgenti-squadre/${id}`, { nome });
}

export async function deleteSorgente(id) {
  await http('DELETE', `/api/me/sorgenti-squadre/${id}`);
  // Gli alias di quella sorgente sono spariti ovunque: via le cache che
  // potrebbero mostrarli.
  for (const chiave of Object.keys(stato.aliasSorgenti)) {
    if (chiave.endsWith(`:${id}`)) delete stato.aliasSorgenti[chiave];
  }
}

export async function saveAlias(cid, sorgente, coppie) {
  await http('PUT', `/api/me/competizioni/${cid}/alias/${sorgente}`,
             { alias: coppie });
  await loadAlias(cid, sorgente);
  await loadCompetizione(cid);   // il badge «compilati» e' cambiato
}
