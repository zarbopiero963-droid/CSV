// Layer API del prototipo. Nessuna rete: i dati vivono in localStorage.
// Le firme di questo modulo SONO il contratto REST che il backend M1 deve esporre;
// per passare al backend reale si sostituisce il corpo di ogni funzione con una
// fetch verso l'endpoint indicato nel commento, senza toccare le viste.

import { COLUMNS, emptyRule, runParser, toCsv, suggestConfig } from './engine.js';

const KEY = 'xtrader-proto-v1';
const LAG = 220; // finta latenza, per vedere gli stati di caricamento

const wait = ms => new Promise(r => setTimeout(r, ms));
const uid = p => p + '_' + Math.random().toString(36).slice(2, 10);

function seed() {
  // Parser d'esempio: replica il parser oggi in produzione, così si vede
  // subito come appare una configurazione completa accanto a una vuota.
  const cols = {};
  for (const c of COLUMNS) cols[c] = emptyRule();
  cols.Provider = { source: 'constant', value: 'XTrader' };
  cols.EventName = { source: 'line', anchor: '🆚', part: 'after', marker: '🆚',
                     transforms: [{ op: 'replace_last', from: ' v ', to: ' - ' }, { op: 'trim' }] };
  cols.MarketName = { source: 'constant', value: 'Over/Under 1,5 gol' };
  cols.MarketType = { source: 'constant', value: 'OVER_UNDER_15' };
  cols.SelectionName = { source: 'constant', value: 'Over 1,5 goal' };
  cols.Handicap = { source: 'constant', value: '0' };
  cols.BetType = { source: 'constant', value: 'PUNTA' };

  return {
    user: null,
    settings: {
      bot_username: 'XTraderSignalBot',
      bot_url: 'https://t.me/XTraderSignalBot',
      bot_add_to_group_url: 'https://t.me/XTraderSignalBot?startgroup=true',
      base_url: 'https://csv-production-b04e.up.railway.app',
    },
    parsers: [{
      id: 'prs_demo01',
      slug: 'over-15-premacht',
      name: 'Over 1,5 PREMACHT',
      active: true,
      token_prefix: 'xt_7f3a91',
      has_token: true,
      chat_ids: ['cht_demo01'],
      sample_message: 'P.Bet. PREMACHT 0,5HT\n🆚 Manchester City v Aston Villa\n⏰ 20:45\n@ 1.42',
      config: { match: { type: 'contains', value: 'P.Bet. PREMACHT 0,5HT' }, columns: cols },
      created_at: '2026-07-02T10:14:00Z',
    }],
    chats: [{
      id: 'cht_demo01',
      telegram_chat_id: '-1001987654321',
      message_thread_id: null,
      title: 'Segnali Calcio PRO',
      type: 'supergroup',
      verified_at: '2026-07-02T10:02:00Z',
    }],
    logs: [
      { id: 'log_3', parser_id: 'prs_demo01', chat_id: 'cht_demo01', matched: true,
        text: 'P.Bet. PREMACHT 0,5HT\n🆚 Manchester City v Aston Villa', at: '2026-08-10T14:22:11Z' },
      { id: 'log_2', parser_id: 'prs_demo01', chat_id: 'cht_demo01', matched: false,
        text: 'Buongiorno a tutti, oggi 4 segnali in programma', at: '2026-08-10T13:58:03Z' },
      { id: 'log_1', parser_id: 'prs_demo01', chat_id: 'cht_demo01', matched: true,
        text: 'P.Bet. PREMACHT 0,5HT\n🆚 Roma v Lazio', at: '2026-08-10T12:31:47Z' },
    ],
    signals: {}, // parser_id -> { csv, expires_at }
    pending_verification: null,
  };
}

let db = load();

function load() {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) return JSON.parse(raw);
  } catch { /* storage non disponibile: si riparte dai dati iniziali */ }
  return seed();
}

function save() {
  try { localStorage.setItem(KEY, JSON.stringify(db)); } catch { /* ignora */ }
}

function clone(v) { return JSON.parse(JSON.stringify(v)); }

export function resetAll() {
  db = seed();
  save();
}

/* ------------------------------------------------------------------ auth */

// POST /api/auth/telegram/widget  (verifica lato server l'HMAC del widget)
export async function loginWidget() {
  await wait(LAG);
  db.user = { id: 'usr_piero', telegram_id: '482913771', username: 'piero_zar',
              first_name: 'Piero', slug: 'PIERO', status: 'active' };
  save();
  return clone(db.user);
}

// POST /api/auth/telegram/code  { code }
export async function loginCode(code) {
  await wait(LAG);
  if (!/^\d{6}$/.test((code || '').trim())) {
    throw new Error('Il codice deve essere di 6 cifre. Aprilo dal bot con /login.');
  }
  return loginWidget();
}

// GET /api/me
export function me() { return db.user ? clone(db.user) : null; }

// POST /api/auth/logout
export async function logout() { db.user = null; save(); }

// GET /api/settings  (valori impostati dall'admin, uguali per tutti gli utenti)
export function settings() { return clone(db.settings); }

/* --------------------------------------------------------------- parsers */

// GET /api/parsers  → solo i parser dell'utente autenticato
export async function listParsers() {
  await wait(LAG);
  return clone(db.parsers);
}

export function getParser(id) {
  const p = db.parsers.find(x => x.id === id);
  return p ? clone(p) : null;
}

// POST /api/parsers  { name }
export async function createParser(name) {
  await wait(LAG);
  const clean = (name || '').trim();
  if (!clean) throw new Error('Il nome è obbligatorio.');
  if (db.parsers.some(p => p.name.toLowerCase() === clean.toLowerCase())) {
    throw new Error('Hai già un parser con questo nome.');
  }
  const cols = {};
  for (const c of COLUMNS) cols[c] = emptyRule();
  const p = {
    id: uid('prs'),
    slug: slugify(clean),
    name: clean,
    active: true,
    token_prefix: null,
    has_token: false,
    chat_ids: [],
    sample_message: '',
    config: { match: { type: 'contains', value: '' }, columns: cols },
    created_at: new Date().toISOString(),
  };
  db.parsers.push(p);
  save();
  return clone(p);
}

// PATCH /api/parsers/:id  { name?, active?, config?, sample_message? }
export async function updateParser(id, patch) {
  await wait(80);
  const p = db.parsers.find(x => x.id === id);
  if (!p) throw new Error('Parser non trovato.');
  Object.assign(p, patch);
  save();
  return clone(p);
}

// DELETE /api/parsers/:id
export async function deleteParser(id) {
  await wait(LAG);
  db.parsers = db.parsers.filter(p => p.id !== id);
  db.logs = db.logs.filter(l => l.parser_id !== id);
  delete db.signals[id];
  save();
}

function slugify(s) {
  return s.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40) || 'parser';
}

/* ----------------------------------------------------------------- token */

// POST /api/parsers/:id/token/rotate
// Il token in chiaro esiste solo in questa risposta: il server ne conserva
// soltanto l'hash. Rigenerare invalida immediatamente quello precedente.
export async function rotateToken(id) {
  await wait(LAG);
  const p = db.parsers.find(x => x.id === id);
  if (!p) throw new Error('Parser non trovato.');
  const token = 'xt_' + Array.from(crypto.getRandomValues(new Uint8Array(18)))
    .map(b => b.toString(16).padStart(2, '0')).join('');
  p.token_prefix = token.slice(0, 9);
  p.has_token = true;
  save();
  return { token, url: feedUrl(p, token) };
}

// DELETE /api/parsers/:id/token
export async function revokeToken(id) {
  await wait(LAG);
  const p = db.parsers.find(x => x.id === id);
  if (!p) throw new Error('Parser non trovato.');
  p.token_prefix = null;
  p.has_token = false;
  save();
}

export function feedUrl(parser, token) {
  const t = token || (parser.has_token ? parser.token_prefix + '…' : 'TOKEN');
  return `${db.settings.base_url}/profiles/${db.user ? db.user.slug : 'PIERO'}/${parser.slug}.csv?token=${t}`;
}

/* ------------------------------------------------------------------ test */

// POST /api/parsers/:id/test  { message }
export async function testParser(id, message) {
  await wait(LAG);
  const p = db.parsers.find(x => x.id === id);
  if (!p) throw new Error('Parser non trovato.');
  const { matched, row } = runParser(message, p.config);
  if (matched) {
    // Il replace avviene solo dentro questo parser: gli altri feed non si toccano.
    db.signals[id] = { csv: toCsv(row), expires_at: Date.now() + 90_000 };
  }
  db.logs.unshift({ id: uid('log'), parser_id: id, chat_id: p.chat_ids[0] || null,
                    matched, text: message, at: new Date().toISOString() });
  db.logs = db.logs.slice(0, 100);
  save();
  return { matched, row, csv: matched ? toCsv(row) : null };
}

// POST /api/parsers/:id/suggest  { message }  → mappatura proposta dal modello
export async function suggest(id, message) {
  await wait(700); // il server reale interroga il modello: lo stato di attesa è reale
  return suggestConfig(message);
}

/* ------------------------------------------------------------------ chat */

// GET /api/chats
export async function listChats() {
  await wait(LAG);
  return clone(db.chats);
}

export function getChat(id) {
  const c = db.chats.find(x => x.id === id);
  return c ? clone(c) : null;
}

// POST /api/chats/verify/start → codice usa-e-getta da incollare nella chat
export async function startVerification() {
  await wait(LAG);
  const code = 'XT-' + Array.from(crypto.getRandomValues(new Uint8Array(3)))
    .map(b => b.toString(36).toUpperCase().padStart(2, '0')).join('').slice(0, 6);
  db.pending_verification = { code, expires_at: Date.now() + 600_000 };
  save();
  return clone(db.pending_verification);
}

// GET /api/chats/verify/status?code=…  (la UI fa polling finché il bot vede il codice)
export async function verificationStatus() {
  await wait(400);
  const pv = db.pending_verification;
  if (!pv) return { state: 'none' };
  if (pv.chat) return { state: 'verified', chat: clone(pv.chat) };
  return { state: 'waiting' };
}

// Solo nel prototipo: simula il bot che legge il codice nel gruppo.
// In produzione questo avviene dentro POST /telegram/webhook.
export async function simulateVerificationMessage(title) {
  const pv = db.pending_verification;
  if (!pv) throw new Error('Nessuna verifica in corso.');
  const chat = {
    id: uid('cht'),
    telegram_chat_id: '-100' + Math.floor(Math.random() * 9e9 + 1e9),
    message_thread_id: null,
    title: (title || '').trim() || 'Gruppo Telegram',
    type: 'supergroup',
    verified_at: new Date().toISOString(),
  };
  db.chats.push(chat);
  pv.chat = chat;
  save();
  return clone(chat);
}

export async function finishVerification() {
  db.pending_verification = null;
  save();
}

// DELETE /api/chats/:id
export async function deleteChat(id) {
  await wait(LAG);
  db.chats = db.chats.filter(c => c.id !== id);
  for (const p of db.parsers) p.chat_ids = p.chat_ids.filter(x => x !== id);
  save();
}

// PUT /api/parsers/:id/chats  { chat_ids }  → la molti-a-molti parser ↔ chat
export async function setParserChats(id, chatIds) {
  await wait(120);
  const p = db.parsers.find(x => x.id === id);
  if (!p) throw new Error('Parser non trovato.');
  p.chat_ids = [...chatIds];
  save();
  return clone(p);
}

/* ------------------------------------------------------------------- log */

// GET /api/logs?parser_id=…   (mai token nei log)
export async function listLogs(parserId) {
  await wait(LAG);
  const rows = parserId ? db.logs.filter(l => l.parser_id === parserId) : db.logs;
  return clone(rows);
}

// Stato del feed: c'è un segnale vivo entro i 90 secondi?
export function signalState(parserId) {
  const s = db.signals[parserId];
  if (!s) return { live: false };
  const left = Math.ceil((s.expires_at - Date.now()) / 1000);
  return left > 0 ? { live: true, seconds_left: left, csv: s.csv } : { live: false };
}
