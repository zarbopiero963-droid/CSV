// La demo (web/api_finta.js) davanti a un localStorage VECCHIO o ritoccato a
// mano: una competizione senza l'array `squadre` non deve far morire di
// TypeError nessuna delle funzioni che lo leggono.
//
// La classe e' quella della guardia aggiunta in deleteSport() (Fable, PR #66):
// al giro dopo Fable e GPT-5.5 hanno chiesto la stessa guardia su saveAlias(),
// e la regola 2 dice di cercare la CLASSE — qui girano TUTTI i consumatori di
// `k.squadre`, cosi' la normalizzazione a fonte unica resta vincolata.
//
// Output: JSON [{nome, ok, errore}], come engine_cases.mjs. Il wrapper pytest
// asserisce caso per caso.

const CHIAVE = 'xtrelay:demo';

// localStorage finto, seminato PRIMA dell'import: `boot()` legge da qui.
const magazzino = new Map();
globalThis.localStorage = {
  getItem: (k) => (magazzino.has(k) ? magazzino.get(k) : null),
  setItem: (k, v) => { magazzino.set(k, String(v)); },
  removeItem: (k) => { magazzino.delete(k); },
};

// Il dato "rotto": la competizione 1 NON ha `squadre` ne' `prossimoId`, come
// la lascerebbe un localStorage di una versione precedente o un ritocco a
// mano. La 2 e' sana, per verificare che la normalizzazione non la tocchi.
magazzino.set(CHIAVE, JSON.stringify({
  loggato: true, slug: 'demo', token_prefix: '', parsers: [], campioni: {},
  sports: [{ slug: 'calcio', nome: 'Calcio', mercati: [], prossimoId: 1 }],
  competizioni: [
    { id: 1, sport: 'calcio', sportNome: 'Calcio', nome: 'Rotta' },
    { id: 2, sport: 'calcio', sportNome: 'Calcio', nome: 'Sana',
      squadre: [{ id: 1, nome: 'Juventus' }], prossimoId: 2 },
    // `squadre` truthy ma NON array: `|| []` non basta (GPT-5.5, PR #66).
    { id: 3, sport: 'calcio', sportNome: 'Calcio', nome: 'Corrotta', squadre: {} },
    // Una versione vecchia: squadre presenti, `prossimoId` mai scritto.
    { id: 4, sport: 'calcio', sportNome: 'Calcio', nome: 'Vecchia',
      squadre: [{ id: 5, nome: 'Inter' }] },
    // Un id manomesso: senza risanamento avvelena il massimo globale (NaN)
    // e le chiavi alias di TUTte le competizioni (Fable, PR #66).
    { id: 5, sport: 'calcio', sportNome: 'Calcio', nome: 'Manomessa',
      squadre: [{ id: 'x', nome: 'Fantasma' }], prossimoId: 1 },
  ],
  sorgenti: [{ id: 1, nome: 'canale' }],
  alias: { '1:1': 'Juve' },
}));

const api = await import('../../web/api_finta.js');
await api.boot();

const esiti = [];

async function caso(nome, fn) {
  try {
    await fn();
    esiti.push({ nome, ok: true });
  } catch (e) {
    esiti.push({ nome, ok: false, errore: `${e.name}: ${e.message}` });
  }
}

function esigi(condizione, messaggio) {
  if (!condizione) throw new Error(messaggio);
}

await caso('competizioni() elenca anche la competizione senza array', () => {
  const elenco = api.competizioni();
  const rotta = elenco.find(k => k.id === 1);
  esigi(rotta && rotta.squadre === 0, `attese 0 squadre, ho ${JSON.stringify(rotta)}`);
  const sana = elenco.find(k => k.id === 2);
  esigi(sana && sana.squadre === 1, `la competizione sana deve restare intatta: ${JSON.stringify(sana)}`);
});

await caso('competizione() apre il dettaglio con squadre vuote e badge 0', () => {
  const dettaglio = api.competizione(1);
  esigi(Array.isArray(dettaglio.squadre) && dettaglio.squadre.length === 0,
    `attese squadre [], ho ${JSON.stringify(dettaglio.squadre)}`);
  esigi(dettaglio.sorgenti[0].compilati === 0,
    `atteso badge 0, ho ${dettaglio.sorgenti[0].compilati}`);
});

await caso('aliasOf() restituisce la tabella vuota', () => {
  const righe = api.aliasOf(1, 1);
  esigi(Array.isArray(righe) && righe.length === 0,
    `attese 0 righe, ho ${JSON.stringify(righe)}`);
});

await caso('saveAlias() con coppie fuori competizione RIFIUTA senza TypeError', async () => {
  let rifiutata = false;
  try {
    await api.saveAlias(1, 1, { 7: 'Fantasma' });
  } catch (e) {
    rifiutata = true;
    esigi(!(e instanceof TypeError), `deve rifiutare, non morire: ${e.message}`);
  }
  esigi(rifiutata, 'una squadra inesistente deve essere rifiutata');
});

let idMilan = null;

await caso('createSquadra() ripara e scrive nella competizione senza array', async () => {
  const squadra = await api.createSquadra(1, 'AC Milan');
  // L'id e' GLOBALE come l'AUTOINCREMENT del server: sopra Juventus (1) e
  // Inter (5), quindi 6 — non un contatore per-competizione ripartito da 1.
  esigi(squadra.id === 6, `atteso id globale 6, ho ${squadra.id}`);
  esigi(api.competizione(1).squadre.length === 1, 'la squadra deve comparire nel dettaglio');
  idMilan = squadra.id;
});

await caso('un `squadre` non-array viene risanato, non lasciato esplodere', () => {
  const corrotta = api.competizioni().find(k => k.id === 3);
  esigi(corrotta && corrotta.squadre === 0,
    `atteso {} risanato in [], ho ${JSON.stringify(corrotta)}`);
  esigi(api.competizione(3).squadre.length === 0, 'il dettaglio deve rispondere');
});

await caso('una competizione vecchia senza prossimoId non riusa gli id', async () => {
  const creata = await api.createSquadra(4, 'Lazio');
  esigi(creata.id > 6, `id gia' visti riusati: ${creata.id}`);
  esigi(Number.isInteger(creata.id), `id non intero: ${creata.id}`);
});

await caso('un id squadra manomesso viene scartato, non propagato come NaN', async () => {
  // Il risanamento butta l'entrata invalida: la competizione risponde vuota...
  esigi(api.competizione(5).squadre.length === 0,
    `la squadra con id 'x' doveva sparire: ${JSON.stringify(api.competizione(5).squadre)}`);
  // ...e il massimo globale resta un numero: la prossima squadra ha un id vero.
  const creata = await api.createSquadra(5, 'Vera');
  esigi(Number.isInteger(creata.id), `id avvelenato dal dato manomesso: ${creata.id}`);
});

await caso('id squadra GLOBALI: gli alias di due competizioni non collidono', async () => {
  // Con id per-competizione ripartiti da 1, due squadre in competizioni
  // diverse condividerebbero la chiave alias `${sorgente}:1` e l'alias
  // scritto in una comparirebbe nell'altra. Il server non puo': AUTOINCREMENT.
  const a = await api.createCompetizione('calcio', 'Comp A');
  const b = await api.createCompetizione('calcio', 'Comp B');
  const qa = await api.createSquadra(a.id, 'Alfa');
  const qb = await api.createSquadra(b.id, 'Beta');
  esigi(qa.id !== qb.id, `id squadra riusato fra competizioni: ${qa.id}`);
  await api.saveAlias(a.id, 1, { [qa.id]: 'SOLO-A' });
  const inB = api.aliasOf(b.id, 1).find(r => r.squadra_id === qb.id);
  esigi(inB.alias === '', `l'alias di A e' trapelato in B: ${inB.alias}`);
  await api.deleteCompetizione(a.id);
  await api.deleteCompetizione(b.id);
});

// --- parita' di validazione col relay vero (CodeRabbit + regola 2, PR #66):
// il server rifiuta i non-stringa con 422, conta i 120 caratteri in CODE
// POINT (len() di Python), e vieta l'emoji SOLO dove il valore finisce nel
// CSV (squadra, campi mercato) — non su sport, competizioni e sorgenti.

await caso('saveAlias() rifiuta un alias non-stringa come il server', async () => {
  let errore = null;
  try { await api.saveAlias(2, 1, { 1: 42 }); } catch (e) { errore = e; }
  esigi(errore && errore.message === 'ogni alias deve essere una stringa',
    `atteso il 422 del server, ho ${errore && errore.message}`);
});

await caso('saveAlias() conta i 120 caratteri in code point, non code unit', async () => {
  // 61 caratteri astrali = 122 code unit ma 61 caratteri: il server ACCETTA
  // (len() Python conta i code point), quindi anche la demo deve accettare.
  await api.saveAlias(2, 1, { 1: '\u{1d54f}'.repeat(61) });
  esigi(api.aliasOf(2, 1)[0].alias === '\u{1d54f}'.repeat(61),
    'alias di 61 caratteri astrali non salvato');
  // E 121 caratteri veri restano rifiutati col messaggio del server.
  let errore = null;
  try { await api.saveAlias(2, 1, { 1: 'a'.repeat(121) }); } catch (e) { errore = e; }
  esigi(errore && errore.message === 'alias troppo lungo: massimo 120 caratteri',
    `attesi 121 caratteri rifiutati, ho ${errore && errore.message}`);
});

await caso('saveAlias() rifiuta lo stesso alias su due squadre della sorgente', async () => {
  const seconda = await api.createSquadra(2, 'Seconda');
  await api.saveAlias(2, 1, { 1: 'Doppione' });
  let errore = null;
  try { await api.saveAlias(2, 1, { [seconda.id]: 'Doppione' }); } catch (e) { errore = e; }
  esigi(errore && errore.message.includes("gia' usato per un'altra squadra"),
    `atteso il 422 del server, ho ${errore && errore.message}`);
  // L'ambiguita' si giudica NORMALIZZATA, come cerca il motore (GPT-5.5,
  // PR #67): 'Spazi  Doppi' e 'Spazi Doppi' sono LO STESSO alias a
  // parse-time, e il salvataggio non deve farli convivere.
  await api.saveAlias(2, 1, { 1: 'Spazi  Doppi' });
  errore = null;
  try { await api.saveAlias(2, 1, { [seconda.id]: 'Spazi Doppi' }); } catch (e) { errore = e; }
  esigi(errore && errore.message.includes("gia' usato"),
    `il doppione normalizzato deve fallire: ${errore && errore.message}`);
  await api.saveAlias(2, 1, { [seconda.id]: 'Spazi Tripli' });   // diverso: libero
  await api.saveAlias(2, 1, { [seconda.id]: '', 1: 'Doppione' });
  // Lo SPOSTAMENTO in un solo salvataggio resta lecito, in qualunque ordine.
  await api.saveAlias(2, 1, { [seconda.id]: 'Doppione', 1: '' });
  esigi(api.aliasOf(2, 1).find(r => r.squadra_id === seconda.id).alias === 'Doppione',
    'lo spostamento del testo fra due squadre deve passare');
  await api.saveAlias(2, 1, { [seconda.id]: '', 1: '' });
  await api.deleteSquadra(2, seconda.id);
});

await caso('createSorgente() accetta le emoji come il server (etichetta UI)', async () => {
  const creata = await api.createSorgente('canale \u{1f525}');
  esigi(creata.nome === 'canale \u{1f525}', 'il server accetta le emoji nelle sorgenti');
  await api.renameSorgente(creata.id, 'ancora \u{1f525}');
  await api.deleteSorgente(creata.id);
});

await caso('createCompetizione() accetta le emoji come il server', async () => {
  const creata = await api.createCompetizione('calcio', 'coppa \u{2b50}\u{fe0f}');
  esigi(creata.nome === 'coppa \u{2b50}\u{fe0f}', 'il server accetta le emoji nelle competizioni');
  await api.deleteCompetizione(creata.id);
});

await caso('createSquadra() RIFIUTA le emoji col messaggio del server', async () => {
  let errore = null;
  try { await api.createSquadra(2, 'Juve \u{1f525}'); } catch (e) { errore = e; }
  esigi(errore && errore.message.includes('XTrader non accetta'),
    `la squadra finisce nel CSV: emoji vietata, ho ${errore && errore.message}`);
});

await caso('_campoDemo rifiuta un nome non-stringa come il server', async () => {
  let errore = null;
  try { await api.createSorgente(42); } catch (e) { errore = e; }
  esigi(errore && errore.message === 'nome deve essere una stringa',
    `atteso il 422 del server, ho ${errore && errore.message}`);
});

await caso('deleteSquadra() e deleteCompetizione() chiudono senza TypeError', async () => {
  // Prerequisito esplicito: se il caso di createSquadra e' fallito, meglio
  // dirlo che mascherarlo con un `deleteSquadra(1, null)` fuorviante (Fable).
  esigi(idMilan !== null, 'prerequisito mancante: createSquadra non ha prodotto un id');
  await api.deleteSquadra(1, idMilan);
  await api.deleteCompetizione(1);
  esigi(api.competizione(1) === null, 'la competizione eliminata non deve tornare');
});

await caso('deleteSport() fa la cascata anche sul dato risanato', async () => {
  // La competizione sana (id 2) sta sotto `calcio`: la cascata deve portarsi
  // via lei e l'alias della sua squadra, come il relay vero.
  await api.deleteSport('calcio');
  esigi(api.competizioni().length === 0, 'le competizioni dello sport devono sparire');
  esigi(api.aliasOf(2, 1) === null, 'il dettaglio eliminato non deve rispondere');
});

process.stdout.write(JSON.stringify(esiti, null, 1) + '\n');
if (esiti.some(e => !e.ok)) process.exitCode = 1;
