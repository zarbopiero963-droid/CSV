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
  const pezzo = (location.hash.match(/tgAuthResult=([A-Za-z0-9_-]+)/) || [])[1];
  if (!pezzo) return null;
  history.replaceState(null, '', location.pathname + location.search);
  let campi;
  try {
    const base64 = pezzo.replace(/-/g, '+').replace(/_/g, '/');
    campi = JSON.parse(atob(base64));
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

// Il messaggio di esempio del wizard vive nel browser, per slug: il server
// non lo conserva (non e' un dato del contratto, e' un appunto di lavoro).
const CHIAVE_CAMPIONE = 'xtrelay:campione:';

export function sampleMessage(slug) {
  try { return localStorage.getItem(CHIAVE_CAMPIONE + slug) || ''; }
  catch { return ''; }
}

export function saveSampleMessage(slug, testo) {
  try { localStorage.setItem(CHIAVE_CAMPIONE + slug, testo); }
  catch { /* modalita' privata: il wizard funziona, l'appunto non sopravvive */ }
}

/* ------------------------------------------------------- feed e token */

// Il token e' DELL'UTENTE, non del parser: un solo URL da incollare in
// XTrader, tutti i parser scrivono li'. Il token in chiaro esiste solo in
// questa risposta: si passa al chiamante e non si conserva.
export async function generateToken() {
  const r = await http('POST', '/api/me/token');
  if (stato.me) {
    stato.me.token_prefix = r.token_prefix;
    stato.me.slug = r.feed.replace(/^\/feed\//, '').replace(/\.csv$/, '');
  }
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
