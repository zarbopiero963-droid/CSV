// Il gemello DIMOSTRATIVO di api.js: stessa superficie, nessun server.
// Lo usa soltanto la copia a file unico generata da tools/build_single_file.py,
// che si apre da file:// e non puo' fare fetch: tutto vive in localStorage.
//
// La regola che tiene in piedi il baratto: OGNI funzione esportata qui deve
// esistere in api.js con la stessa firma, e viceversa — le viste di app.js
// sono le stesse e non sanno quale dei due layer le serve. La parita' e'
// vincolata da tests/web/test_api_parita.py.
//
// Attenzione build: tools/build_single_file.py ricostruisce il namespace
// leggendo le RIGHE `export function` / `export async function` — qui non si
// esportano costanti, e le funzioni si dichiarano una per riga.

import { COLUMNS, runParser, toCsv, suggestConfig } from './engine.js';

const CHIAVE = 'xtrelay:demo';

const stato = {
  dati: null,   // {loggato, slug, token_prefix, parsers: [...], campioni: {}}
};

function carica() {
  try {
    stato.dati = JSON.parse(localStorage.getItem(CHIAVE)) || null;
  } catch {
    stato.dati = null;
  }
  if (!stato.dati) {
    stato.dati = { loggato: false, slug: '', token_prefix: '', parsers: [], campioni: {} };
  }
}

function salva() {
  try { localStorage.setItem(CHIAVE, JSON.stringify(stato.dati)); }
  catch { /* modalita' privata: la demo vive solo in memoria */ }
}

function slugDa(titolo) {
  const base = titolo.toLowerCase().replace(/[^a-z0-9-]+/g, '-')
    .replace(/^-+|-+$/g, '') || 'parser';
  const presi = new Set(stato.dati.parsers.map(p => p.slug));
  if (!presi.has(base)) return base;
  let n = 2;
  while (presi.has(`${base}-${n}`)) n += 1;
  return `${base}-${n}`;
}

/* ------------------------------------------------------------------ boot */

export async function boot() {
  carica();
  return null;
}

/* --------------------------------------------------------------- sessione */

export function me() {
  if (!stato.dati || !stato.dati.loggato) return null;
  return {
    utente: 1, nome: 'Demo', stato: 'attivo', admin: false,
    accesso_scade: null, giorni_rimasti: 30,
    slug: stato.dati.slug || null, token_prefix: stato.dati.token_prefix || null,
  };
}

export function settings() {
  // Nessun bot: la pagina di login mostra la sola porta a password, che nella
  // demo accetta qualunque coppia non vuota — e' una vetrina, non una serratura.
  return { bot_username: '', bot_id: null, base_url: '' };
}

export async function loginPassword(username, password) {
  if (!username || !password) throw new Error('scrivi utente e password (demo: qualunque coppia)');
  stato.dati.loggato = true;
  salva();
}

export function telegramAuthUrl() {
  return null;
}

export async function logout() {
  stato.dati.loggato = false;
  salva();
}

/* --------------------------------------------------- accesso su approvazione */

// La demo e' sempre attiva: la richiesta risponde come farebbe il server a un
// account gia' dentro (409), cosi' la superficie resta identica a api.js.
export async function requestAccess() {
  throw new Error("accesso gia' attivo");
}

export function botAccessoUrl() {
  return null;
}

/* ----------------------------------------------------------------- parser */

export async function listParsers() {
  return stato.dati.parsers;
}

export function getParser(slug) {
  return stato.dati.parsers.find(p => p.slug === slug) || null;
}

export async function createParser(titolo) {
  const pulito = String(titolo || '').trim();
  if (!pulito) throw new Error('titolo mancante');
  const parser = { id: stato.dati.parsers.length + 1, slug: slugDa(pulito),
                   titolo: pulito, active: true, config: {}, ordine: 0 };
  stato.dati.parsers.push(parser);
  salva();
  return parser;
}

export async function updateParser(slug, patch) {
  const p = getParser(slug);
  if (!p) throw new Error('parser non trovato');
  if (patch.titolo !== undefined) p.titolo = patch.titolo;
  if (patch.config !== undefined) p.config = patch.config;
  if (patch.active !== undefined) p.active = patch.active;
  salva();
  return p;
}

export async function deleteParser(slug) {
  stato.dati.parsers = stato.dati.parsers.filter(p => p.slug !== slug);
  salva();
}

// Stessa forma della risposta del server: matched, missing, scarti, complete,
// e — se completo — csv ed event. Qui gira il motore JS, che per contratto
// produce gli stessi byte di quello Python (test_engine_contract.py).
export async function testParser(slug, message) {
  const p = getParser(slug);
  if (!p) throw new Error('parser non trovato');
  let r;
  try {
    r = runParser(message, p.config);
  } catch {
    return { matched: false, missing: [], scarti: [], complete: false,
             errore: 'config non eseguibile' };
  }
  const corpo = { matched: r.matched, missing: r.missing,
                  scarti: r.scarti || [], complete: r.complete };
  if (r.complete) {
    corpo.event = r.row[COLUMNS.indexOf('EventName')];
    corpo.csv = toCsv(r.row);
  }
  return corpo;
}

export function suggest(message) {
  return suggestConfig(message);
}

/* ------------------------------------------------- messaggio di esempio */

export function sampleMessage(slug) {
  return (stato.dati.campioni || {})[slug] || '';
}

export function saveSampleMessage(slug, testo) {
  stato.dati.campioni = stato.dati.campioni || {};
  stato.dati.campioni[slug] = testo;
  salva();
}

/* ------------------------------------------------------- feed e token */

export async function generateToken() {
  const alfabeto = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  let token = 'xt_';
  for (let i = 0; i < 32; i += 1) {
    token += alfabeto[Math.floor(Math.random() * alfabeto.length)];
  }
  stato.dati.slug = stato.dati.slug || 'demo';
  stato.dati.token_prefix = token.slice(0, 9);
  salva();
  return {
    token,
    url: 'https://demo.invalid/feed/' + stato.dati.slug + '.csv?token=' + token,
  };
}

export function feedUrl() {
  if (!stato.dati.slug) return null;
  const prefisso = stato.dati.token_prefix || '';
  return 'https://demo.invalid/feed/' + stato.dati.slug + '.csv?token='
    + (prefisso ? prefisso + '…' : '…');
}

export function hasToken() {
  return Boolean(stato.dati.token_prefix);
}
