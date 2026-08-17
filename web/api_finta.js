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

import { COLUMNS, EMOJI, runParser, toCsv, suggestConfig } from './engine.js';

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
// account gia' dentro (409, ANCHE nello status: app.js decide su quello),
// cosi' la superficie resta identica a api.js.
export async function requestAccess() {
  const errore = new Error("accesso gia' attivo");
  errore.status = 409;
  throw errore;
}

export function botAccessoUrl() {
  return null;
}

/* ----------------------------------------------------------- pannello admin */

// La demo non ha un amministratore (me().admin e' false): il pannello non
// compare mai e queste funzioni esistono per la parita' di superficie. Se
// qualcosa le chiamasse comunque, rispondono come il server a un non-admin.
function _non_admin() {
  const errore = new Error('not found');
  errore.status = 404;
  throw errore;
}

export async function adminRequests() { _non_admin(); }
export async function adminApprove() { _non_admin(); }
export async function adminReject() { _non_admin(); }
export async function adminReminder() { _non_admin(); }

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

/* -------------------------------------------------- mercati Betfair (#33) */

// La libreria mercati nella demo e' VERA (localStorage), non uno stub 404: il
// file unico deve poter mostrare il flusso completo sport → mercato →
// selezioni → wizard, che e' il punto della #33. Le forme delle risposte sono
// quelle del server; gli errori portano lo stesso messaggio che darebbe lui.

function _sportsDemo() {
  stato.dati.sports = stato.dati.sports || [];
  return stato.dati.sports;
}

function _sportODemo(slug) {
  const sport = _sportsDemo().find(s => s.slug === slug);
  if (!sport) {
    const errore = new Error('sport non trovato');
    errore.status = 404;
    throw errore;
  }
  return sport;
}

function _mercatoODemo(sport, id) {
  const mercato = sport.mercati.find(m => m.id === Number(id));
  if (!mercato) {
    const errore = new Error('mercato non trovato');
    errore.status = 404;
    throw errore;
  }
  return mercato;
}

function _campoDemo(nome, valore) {
  const pulito = String(valore || '').trim();
  if (!pulito) throw new Error(nome + ' mancante');
  if (pulito.length > 120) throw new Error(nome + ' troppo lungo: massimo 120 caratteri');
  // Stesso verdetto del server (#42): un'emoji creata nella demo verrebbe
  // rifiutata dal relay vero con un 422 — meglio che la demo lo dica subito.
  // La classe e' quella del motore (EMOJI), non una seconda copia.
  if (EMOJI.test(pulito)) {
    throw new Error(nome + ' contiene un simbolo che XTrader non accetta: solo testo');
  }
  return pulito;
}

export async function loadSports() { return sports(); }

export function sports() {
  return _sportsDemo().map(s => ({ slug: s.slug, nome: s.nome,
                                   mercati: s.mercati.length }));
}

export async function loadMercati(sport) { return mercatiOf(sport); }

export function mercatiOf(sport) {
  const trovato = _sportsDemo().find(s => s.slug === sport);
  return trovato ? trovato.mercati : null;
}

export async function createSport(nome) {
  const pulito = _campoDemo('nome', nome);
  const base = pulito.toLowerCase().replace(/[^a-z0-9-]+/g, '-')
    .replace(/^-+|-+$/g, '') || 'sport';
  const presi = new Set(_sportsDemo().map(s => s.slug));
  let slug = base;
  let n = 2;
  while (presi.has(slug)) { slug = `${base}-${n}`; n += 1; }
  _sportsDemo().push({ slug, nome: pulito, mercati: [], prossimoId: 1 });
  salva();
  return { slug, nome: pulito };
}

export async function deleteSport(slug) {
  _sportODemo(slug);
  stato.dati.sports = _sportsDemo().filter(s => s.slug !== slug);
  // La cascata del server (#34): via anche le competizioni di questo sport,
  // con le loro squadre e gli alias relativi in tutte le sorgenti. Senza,
  // la demo terrebbe competizioni orfane che il relay vero non avrebbe
  // (CodeRabbit, PR #66).
  const orfane = _competizioniDemo().filter(k => k.sport === slug);
  orfane.forEach(k => {
    // `|| []`: un localStorage vecchio o ritoccato a mano potrebbe non avere
    // l'array, e la cascata non deve morire di TypeError (Fable, PR #66).
    (k.squadre || []).forEach(q => {
      _sorgentiDemo().forEach(g => { delete _aliasDemo()[`${g.id}:${q.id}`]; });
    });
  });
  stato.dati.competizioni = _competizioniDemo().filter(k => k.sport !== slug);
  salva();
}

export async function createMercato(sportSlug, dati) {
  const sport = _sportODemo(sportSlug);
  const tipo = _campoDemo('marketType', dati.marketType);
  const nome = _campoDemo('marketName', dati.marketName);
  if (sport.mercati.some(m => m.marketType === tipo && m.marketName === nome)) {
    const errore = new Error("mercato gia' presente in questo sport");
    errore.status = 409;
    throw errore;
  }
  const mercato = { id: sport.prossimoId || 1, marketType: tipo, marketName: nome,
                    selezioni: [] };
  sport.prossimoId = mercato.id + 1;
  (dati.selections || []).forEach(s => {
    mercato.selezioni.push({ id: mercato.selezioni.length + 1,
                             selectionName: _campoDemo('selectionName', s) });
  });
  sport.mercati.push(mercato);
  salva();
  return mercato;
}

export async function deleteMercato(sportSlug, id) {
  const sport = _sportODemo(sportSlug);
  _mercatoODemo(sport, id);
  sport.mercati = sport.mercati.filter(m => m.id !== Number(id));
  salva();
}

export async function createSelezione(sportSlug, mercatoId, selectionName) {
  const sport = _sportODemo(sportSlug);
  const mercato = _mercatoODemo(sport, mercatoId);
  const pulita = _campoDemo('selectionName', selectionName);
  if (mercato.selezioni.some(s => s.selectionName === pulita)) {
    const errore = new Error("selezione gia' presente in questo mercato");
    errore.status = 409;
    throw errore;
  }
  const massimo = mercato.selezioni.reduce((m, s) => Math.max(m, s.id), 0);
  const selezione = { id: massimo + 1, selectionName: pulita };
  mercato.selezioni.push(selezione);
  salva();
  return selezione;
}

export async function deleteSelezione(sportSlug, mercatoId, id) {
  const sport = _sportODemo(sportSlug);
  const mercato = _mercatoODemo(sport, mercatoId);
  mercato.selezioni = mercato.selezioni.filter(s => s.id !== Number(id));
  salva();
}

/* ------------------------------------------------ sorgenti squadre (#34) */

// Stessa scelta della libreria mercati: la demo e' VERA (localStorage), con le
// forme delle risposte del server e gli stessi messaggi d'errore.

function _competizioniDemo() {
  stato.dati.competizioni = stato.dati.competizioni || [];
  return stato.dati.competizioni;
}

function _sorgentiDemo() {
  stato.dati.sorgenti = stato.dati.sorgenti || [];
  return stato.dati.sorgenti;
}

function _aliasDemo() {
  stato.dati.alias = stato.dati.alias || {};   // `${sorgente}:${squadra}` -> alias
  return stato.dati.alias;
}

function _competizioneODemo(cid) {
  const trovata = _competizioniDemo().find(k => k.id === Number(cid));
  if (!trovata) {
    const errore = new Error('competizione non trovata');
    errore.status = 404;
    throw errore;
  }
  return trovata;
}

function _sorgenteODemo(id) {
  const trovata = _sorgentiDemo().find(g => g.id === Number(id));
  if (!trovata) {
    const errore = new Error('sorgente non trovata');
    errore.status = 404;
    throw errore;
  }
  return trovata;
}

function _compilatiDemo(competizione, sorgente) {
  return competizione.squadre.filter(
    q => (_aliasDemo()[`${sorgente.id}:${q.id}`] || '') !== '').length;
}

export async function loadCompetizioni() { return competizioni(); }

export function competizioni() {
  return _competizioniDemo().map(k => ({
    id: k.id, sport: k.sport, sportNome: k.sportNome, nome: k.nome,
    squadre: k.squadre.length }));
}

export async function loadCompetizione(cid) { return competizione(cid); }

export function competizione(cid) {
  const k = _competizioniDemo().find(x => x.id === Number(cid));
  if (!k) return null;
  return {
    id: k.id, nome: k.nome, sport: k.sport,
    squadre: k.squadre.map(q => ({ id: q.id, nome: q.nome })),
    sorgenti: _sorgentiDemo().map(g => ({
      id: g.id, nome: g.nome, compilati: _compilatiDemo(k, g) })),
  };
}

export async function loadAlias(cid, sorgente) { return aliasOf(cid, sorgente); }

export function aliasOf(cid, sorgente) {
  const k = _competizioniDemo().find(x => x.id === Number(cid));
  const g = _sorgentiDemo().find(x => x.id === Number(sorgente));
  if (!k || !g) return null;
  return k.squadre.map(q => ({
    squadra_id: q.id, squadra: q.nome,
    alias: _aliasDemo()[`${g.id}:${q.id}`] || '' }));
}

export async function createCompetizione(sportSlug, nome) {
  const sport = _sportODemo(sportSlug);
  const pulito = _campoDemo('nome', nome);
  if (_competizioniDemo().some(k => k.sport === sportSlug && k.nome === pulito)) {
    const errore = new Error("hai gia' una competizione con questo nome in questo sport");
    errore.status = 409;
    throw errore;
  }
  const massimo = _competizioniDemo().reduce((m, k) => Math.max(m, k.id), 0);
  const creata = { id: massimo + 1, sport: sport.slug, sportNome: sport.nome,
                   nome: pulito, squadre: [], prossimoId: 1 };
  _competizioniDemo().push(creata);
  salva();
  return { id: creata.id, sport: sport.slug, nome: pulito };
}

export async function deleteCompetizione(cid) {
  const k = _competizioneODemo(cid);
  k.squadre.forEach(q => {
    _sorgentiDemo().forEach(g => { delete _aliasDemo()[`${g.id}:${q.id}`]; });
  });
  stato.dati.competizioni = _competizioniDemo().filter(x => x.id !== k.id);
  salva();
}

export async function createSquadra(cid, nome) {
  const k = _competizioneODemo(cid);
  const pulito = _campoDemo('nome', nome);
  if (k.squadre.some(q => q.nome === pulito)) {
    const errore = new Error("squadra gia' presente in questa competizione");
    errore.status = 409;
    throw errore;
  }
  const squadra = { id: k.prossimoId || 1, nome: pulito };
  k.prossimoId = squadra.id + 1;
  k.squadre.push(squadra);
  salva();
  return squadra;
}

export async function deleteSquadra(cid, id) {
  const k = _competizioneODemo(cid);
  const squadra = k.squadre.find(q => q.id === Number(id));
  if (!squadra) {
    const errore = new Error('squadra non trovata');
    errore.status = 404;
    throw errore;
  }
  _sorgentiDemo().forEach(g => { delete _aliasDemo()[`${g.id}:${squadra.id}`]; });
  k.squadre = k.squadre.filter(q => q.id !== squadra.id);
  salva();
}

export async function createSorgente(nome) {
  const pulito = _campoDemo('nome', nome);
  if (_sorgentiDemo().some(g => g.nome === pulito)) {
    const errore = new Error("hai gia' una sorgente con questo nome");
    errore.status = 409;
    throw errore;
  }
  const massimo = _sorgentiDemo().reduce((m, g) => Math.max(m, g.id), 0);
  const creata = { id: massimo + 1, nome: pulito };
  _sorgentiDemo().push(creata);
  salva();
  return creata;
}

export async function renameSorgente(id, nome) {
  const sorgente = _sorgenteODemo(id);
  const pulito = _campoDemo('nome', nome);
  if (_sorgentiDemo().some(g => g.nome === pulito && g.id !== sorgente.id)) {
    const errore = new Error("hai gia' una sorgente con questo nome");
    errore.status = 409;
    throw errore;
  }
  sorgente.nome = pulito;
  salva();
  return { id: sorgente.id, nome: pulito };
}

export async function deleteSorgente(id) {
  const sorgente = _sorgenteODemo(id);
  for (const chiave of Object.keys(_aliasDemo())) {
    if (chiave.startsWith(`${sorgente.id}:`)) delete _aliasDemo()[chiave];
  }
  stato.dati.sorgenti = _sorgentiDemo().filter(g => g.id !== sorgente.id);
  salva();
}

export async function saveAlias(cid, sorgenteId, coppie) {
  const k = _competizioneODemo(cid);
  const sorgente = _sorgenteODemo(sorgenteId);
  const valide = new Set(k.squadre.map(q => q.id));
  // PRIMA tutta la validazione, POI le scritture (Fable, PR #66): mutando nel
  // loop, una coppia invalida a meta' lasciava in memoria le coppie gia'
  // applicate — il server invece chiude senza commit e non scrive niente.
  const pulite = [];
  for (const [chiave, valore] of Object.entries(coppie || {})) {
    const squadra = Number(chiave);
    if (!valide.has(squadra)) {
      throw new Error(`squadra ${chiave} non in questa competizione`);
    }
    const pulito = String(valore || '').trim();
    // RIFIUTATO come dal server (422), non troncato: uno slice a meta' di una
    // coppia surrogata lascerebbe un lone surrogate nel localStorage
    // (CodeRabbit, PR #66). Stesso messaggio del relay vero.
    if (pulito.length > 120) {
      throw new Error('alias troppo lungo: massimo 120 caratteri');
    }
    pulite.push([squadra, pulito]);
  }
  for (const [squadra, pulito] of pulite) {
    if (pulito === '') delete _aliasDemo()[`${sorgente.id}:${squadra}`];
    else _aliasDemo()[`${sorgente.id}:${squadra}`] = pulito;
  }
  salva();
}
