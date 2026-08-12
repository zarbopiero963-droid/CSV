"""La migrazione dello schema: nessun dato perso, e rieseguibile.

E' il cambiamento piu' rischioso fatto finora su questo servizio, e la ragione non
e' la complessita': in produzione il database sta su un volume (`/data/signals.db`,
misurato il 12/08/2026) e contiene i parser veri del proprietario. Un difetto qui
non da' un 500 che si nota: cancella configurazione che nessuno ha altrove.

Da cui la forma della migrazione, che questi test vincolano:

- **non cancella e non rinomina niente.** Le tre tabelle originali — `signals`,
  `parsers`, `profiles` — restano con la loro forma, e ogni endpoint continua a
  leggerle. Se la migrazione avesse un difetto il servizio funziona comunque;
- le nove tabelle nuove si CREANO, le due che esistono si ESTENDONO con `ALTER`
  additivo. `parsers` e `signals` esistono gia' con una forma diversa, e SQLite non
  ammette due tabelle con lo stesso nome: creare `parsers_v2` accanto a `parsers`
  avrebbe lasciato due fonti per la stessa cosa, che e' cio' che la regola 3 vieta;
- gira **una volta per processo**, non a ogni connessione.

Su quest'ultimo punto: prima la migrazione stava dentro `db()`, quindi ogni
richiesta eseguiva tre CREATE TABLE, due ALTER, due INSERT OR IGNORE, una UPDATE e
un COMMIT — una transazione di **scrittura** anche sulle letture del feed, che
`README.txt` dice essere interrogato «a raffica» da XTrader. Funzionava perche' era
idempotente, non perche' fosse progettato. Con undici tabelle quel costo si
moltiplicava sul percorso piu' caldo del servizio.

**Cosa questi test NON coprono, e non e' una dimenticanza.** Le tabelle
`webhook_seen`, `message_logs` e `feed_reads` esistono ma **nessun codice le usa
ancora**: il dedup degli `update_id` e la cancellazione oltre i 7 giorni sono
comportamenti dei PR successivi. Qui si verifica che i vincoli reggano — un
`update_id` ripetuto viene rifiutato dalla chiave primaria — non che il webhook li
consulti, perche' non lo fa. Dichiararlo coperto sarebbe la copertura finta che
`CLAUDE.md` vieta.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE))

import main  # noqa: E402 - dopo l'inserimento del percorso
from tests.ambiente import CHIAVI_PERICOLOSE  # noqa: E402

# Il formato con cui il servizio e' nato, scritto a mano invece di importato: e' il
# database che sta in produzione ADESSO, e un test che lo costruisse chiamando il
# codice nuovo non proverebbe niente sulla migrazione.
SCHEMA_DI_PRODUZIONE = (
    'CREATE TABLE signals (id INTEGER PRIMARY KEY AUTOINCREMENT, csv TEXT NOT NULL,'
    ' parser TEXT, profile TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP,'
    ' expires_at INTEGER)',
    'CREATE TABLE parsers (name TEXT PRIMARY KEY, header TEXT NOT NULL, market_name TEXT,'
    ' market_type TEXT, selection_name TEXT, handicap TEXT, bet_type TEXT)',
    'CREATE TABLE profiles (name TEXT PRIMARY KEY, chat_ids TEXT NOT NULL, parser TEXT NOT NULL)',
)

CHAT_A, CHAT_B = '-1001111111111', '-1002222222222'


@pytest.fixture(autouse=True)
def _ambiente_pulito(monkeypatch):
    """Nessuna variabile della macchina entra in questi test.

    In particolare `TELEGRAM_ALLOWED_CHAT_IDS`: la migrazione la legge per creare il
    profilo PIERO, quindi con il `.env` del proprietario caricato questi test
    scriverebbero i suoi chat_id reali dentro il database di prova.
    """
    for chiave in CHIAVI_PERICOLOSE:
        monkeypatch.delenv(chiave, raising=False)


def _database_di_produzione(percorso: Path) -> sqlite3.Connection:
    """Un database nel formato VECCHIO, con dentro dati come quelli veri."""
    c = sqlite3.connect(percorso)
    for istruzione in SCHEMA_DI_PRODUZIONE:
        c.execute(istruzione)
    c.execute('INSERT INTO parsers VALUES (?,?,?,?,?,?,?)',
              ('Parser_Telegram_XTrader_v1', 'P.Bet. PREMACHT 0,5HT', 'Over/Under 1,5 gol',
               'OVER_UNDER_15', 'Over 1,5 goal', '0', 'PUNTA'))
    c.execute('INSERT INTO parsers VALUES (?,?,?,?,?,?,?)',
              ('Secondo_Parser', 'ALTRO HEADER', 'Match Odds', 'MATCH_ODDS',
               'Over 2,5 goal', '0', 'BANCA'))
    c.execute('INSERT INTO profiles VALUES (?,?,?)',
              ('PIERO', f'{CHAT_A},{CHAT_B}', 'Parser_Telegram_XTrader_v1'))
    c.execute('INSERT INTO signals(csv, parser, profile, expires_at) VALUES (?,?,?,?)',
              ('"Provider"\r\n', 'Parser_Telegram_XTrader_v1', 'PIERO', 9_999_999_999))
    c.commit()
    return c


CLAUSOLA_ORIGIN = ' origin_profile TEXT UNIQUE,'


def _crea_users(c, con_origin_profile: bool):
    """La tabella `users` come la crea `main`, meno cio- che il test vuole assente.

    DERIVATA dallo schema vero invece di ricopiata, e la ragione l-ha segnalata GPT-5.5:
    tre test costruivano a mano una `users` di tredici colonne per rappresentare lo
    stato di un database migrato da una versione intermedia di questo ramo. Il giorno
    che lo schema vero guadagna una colonna, quelle copie restano indietro e i test
    continuano a passare misurando una tabella che non esiste piu- da nessuna parte —
    un falso verde, cioe- il difetto peggiore che un test possa avere.

    `SCHEMA_DI_PRODUZIONE` invece resta scritto a mano, e non e- un-incoerenza: quello
    e- un formato STORICO e congelato — il database che sta in produzione adesso — e
    derivarlo dal codice nuovo non proverebbe niente sulla migrazione. Questo e- lo
    stato transitorio di uno schema VIVO, che deve seguirlo.

    `con_origin_profile=False` da- lo stato precedente alla colonna;
    `True` da- lo stato dell-ALTER: colonna presente, vincolo UNIQUE assente, che
    `ALTER TABLE ADD COLUMN` non sa aggiungere.
    """
    vero = next(x for x in main.SCHEMA_MULTIUTENTE
                if 'CREATE TABLE IF NOT EXISTS users' in x)
    assert CLAUSOLA_ORIGIN in vero, (
        f'la clausola {CLAUSOLA_ORIGIN!r} non e- piu- nello schema di `users`: e- stata '
        'rinominata o riformattata, e questo aiutante starebbe costruendo una tabella '
        'sbagliata in silenzio. Aggiornare CLAUSOLA_ORIGIN')
    sostituto = ' origin_profile TEXT,' if con_origin_profile else ''
    istruzione = vero.replace(CLAUSOLA_ORIGIN, sostituto)
    assert istruzione != vero, 'la sostituzione non ha cambiato niente'
    c.execute(istruzione)


def _fotografia(c: sqlite3.Connection) -> dict:
    """Tutto il contenuto del database, tabella per tabella, per confrontarlo dopo."""
    # `sqlite_sequence` e' escluso: e' il contatore interno dell-AUTOINCREMENT, e
    # avanza anche su un `INSERT OR IGNORE` che NON inserisce niente. Non e' un dato,
    # e includerlo faceva fallire il confronto per una ragione che non riguarda
    # l-idempotenza. L-esclusione e' motivata, non comoda: il difetto vero che questo
    # test ha trovato — le chat duplicate — sta nelle tabelle, non nel contatore.
    tabelle = [r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
        " AND name != 'sqlite_sequence' ORDER BY name").fetchall()]
    return {t: sorted(map(repr, c.execute(f'SELECT * FROM {t}').fetchall())) for t in tabelle}


# ------------------------------------------------ il caso della produzione

def test_un_database_nel_formato_VECCHIO_conserva_tutto(tmp_path):
    """Il test che conta: i dati che esistono adesso sopravvivono alla migrazione.

    Non «esistono ancora delle righe»: si confrontano i VALORI, perche' una
    migrazione che azzerasse `header` lascerebbe il conteggio intatto.
    """
    percorso = tmp_path / 'signals.db'
    c = _database_di_produzione(percorso)
    prima = {
        'parsers': sorted(c.execute('SELECT name, header, market_type, bet_type FROM parsers')),
        'profiles': sorted(c.execute('SELECT name, chat_ids, parser FROM profiles')),
        'signals': sorted(c.execute('SELECT csv, parser, profile, expires_at FROM signals')),
    }

    main.migra(c)

    dopo = {
        'parsers': sorted(c.execute('SELECT name, header, market_type, bet_type FROM parsers')),
        'profiles': sorted(c.execute('SELECT name, chat_ids, parser FROM profiles')),
        'signals': sorted(c.execute('SELECT csv, parser, profile, expires_at FROM signals')),
    }
    assert dopo == prima, (
        'la migrazione ha alterato i dati delle tabelle originali:\n'
        f'  prima: {prima}\n  dopo : {dopo}'
    )


def test_la_migrazione_e_IDEMPOTENTE_su_un_database_popolato(tmp_path):
    """Rieseguirla non cambia niente: e' il requisito scritto in #2.

    Serve perche' `migra()` viene chiamata da `db()` al primo accesso di ogni
    processo, e i processi si riavviano a ogni deploy. Una migrazione che duplicasse
    righe al secondo giro riempirebbe il database di copie senza mai dare errore.
    """
    c = _database_di_produzione(tmp_path / 'signals.db')
    main.migra(c)
    dopo_una = _fotografia(c)

    main.migra(c)
    dopo_due = _fotografia(c)

    assert dopo_due == dopo_una, 'la seconda migrazione ha cambiato il database'


def test_le_nove_tabelle_nuove_esistono_dopo_la_migrazione(tmp_path):
    c = _database_di_produzione(tmp_path / 'signals.db')
    main.migra(c)
    presenti = {r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    attese = {'users', 'chats', 'parser_chats', 'message_logs', 'chat_verifications',
              'access_requests', 'admin_audit', 'feed_reads', 'webhook_seen',
              'signals', 'parsers', 'profiles'}
    assert attese <= presenti, f'tabelle mancanti: {sorted(attese - presenti)}'


# --------------------------------------------------------- il travaso

def test_il_profilo_PIERO_diventa_un_utente_amministratore(tmp_path):
    c = _database_di_produzione(tmp_path / 'signals.db')
    main.migra(c)
    riga = c.execute('SELECT slug, first_name, status, is_admin, session_version'
                     ' FROM users WHERE slug=?', ('piero',)).fetchone()
    assert riga is not None, 'nessun utente creato dal profilo PIERO'
    assert riga == ('piero', 'PIERO', 'attivo', 1, 1), riga


def test_il_travaso_NON_inventa_un_telegram_id_ne_un_token(tmp_path):
    """Due campi restano NULL di proposito, e non e' incompletezza.

    `telegram_id`: il proprietario non ha ancora fatto login Telegram. Inventarne
    uno creerebbe un utente che il login non riconoscerebbe, e il vincolo UNIQUE
    renderebbe impossibile il login vero quando arrivera'.

    `token_hash`: oggi il feed e' protetto da `CSV_ACCESS_TOKEN`, uno per tutto il
    servizio. Generare qui un token per utente vorrebbe dire scriverlo da qualche
    parte — un segreto in piu' che nessuno usa — e i token nascono in chiaro una
    volta sola, alla generazione, quando c'e' qualcuno a cui mostrarli.
    """
    c = _database_di_produzione(tmp_path / 'signals.db')
    main.migra(c)
    telegram_id, token_hash, token_prefix = c.execute(
        'SELECT telegram_id, token_hash, token_prefix FROM users WHERE slug=?',
        ('piero',)).fetchone()
    assert telegram_id is None, f'telegram_id inventato: {telegram_id!r}'
    assert token_hash is None, 'token_hash generato senza nessuno a cui mostrarlo'
    assert token_prefix is None, f'token_prefix inventato: {token_prefix!r}'


def test_i_chat_id_diventano_righe_della_tabella_chats(tmp_path):
    """Da stringa separata da virgole a righe, senza perderne nessuna."""
    c = _database_di_produzione(tmp_path / 'signals.db')
    main.migra(c)
    chat = {r[0] for r in c.execute('SELECT telegram_chat_id FROM chats').fetchall()}
    assert chat == {CHAT_A, CHAT_B}, chat
    proprietari = {r[0] for r in c.execute('SELECT owner_user_id FROM chats').fetchall()}
    utente = c.execute('SELECT id FROM users WHERE slug=?', ('piero',)).fetchone()[0]
    assert proprietari == {utente}, f'chat non attribuite all-utente: {proprietari}'


def test_ogni_parser_riceve_utente_slug_e_ordine(tmp_path):
    """`ordine` deve essere deterministico: decide chi vince fra due parser.

    E' il punto di #2 «Ordine dei parser: deterministico, ORDER BY esplicito». Due
    parser con lo stesso `ordine` renderebbero la collisione decisa dal caso.
    """
    c = _database_di_produzione(tmp_path / 'signals.db')
    main.migra(c)
    utente = c.execute('SELECT id FROM users WHERE slug=?', ('piero',)).fetchone()[0]
    righe = c.execute('SELECT name, user_id, slug, ordine FROM parsers ORDER BY name').fetchall()
    assert [r[1] for r in righe] == [utente, utente], righe
    assert [r[2] for r in righe] == ['parser_telegram_xtrader_v1', 'secondo_parser'], righe
    ordini = [r[3] for r in righe]
    assert len(set(ordini)) == len(ordini), f'ordine non univoco: {righe}'


def test_i_segnali_esistenti_vengono_attribuiti_all_utente(tmp_path):
    c = _database_di_produzione(tmp_path / 'signals.db')
    main.migra(c)
    utente = c.execute('SELECT id FROM users WHERE slug=?', ('piero',)).fetchone()[0]
    assert c.execute('SELECT user_id FROM signals').fetchall() == [(utente,)]


# ----------------------------------------------------------- i vincoli

def test_un_update_id_ripetuto_viene_RIFIUTATO(tmp_path):
    """Il vincolo del dedup dei webhook, non il dedup.

    La tabella esiste e la chiave primaria regge; **nessun codice la consulta
    ancora**. Il dedup vero — «un `update_id` ripetuto viene elaborato una volta
    sola» — e' comportamento del PR che tocchera' il webhook, e dichiararlo coperto
    qui sarebbe copertura finta.
    """
    c = _database_di_produzione(tmp_path / 'signals.db')
    main.migra(c)
    c.execute('INSERT INTO webhook_seen(update_id) VALUES (?)', ('123',))
    with pytest.raises(sqlite3.IntegrityError):
        c.execute('INSERT INTO webhook_seen(update_id) VALUES (?)', ('123',))


def test_due_parser_dello_stesso_utente_non_possono_avere_lo_stesso_slug(tmp_path):
    """`UNIQUE (user_id, slug)` di #2, espresso come indice.

    Su una tabella che esiste gia' un vincolo UNIQUE non si aggiunge con `ALTER`:
    va creato come indice, ed e' quello che la migrazione fa.
    """
    c = _database_di_produzione(tmp_path / 'signals.db')
    main.migra(c)
    utente = c.execute('SELECT id FROM users WHERE slug=?', ('piero',)).fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        c.execute('INSERT INTO parsers(name, header, user_id, slug) VALUES (?,?,?,?)',
                  ('Terzo', 'H', utente, 'secondo_parser'))


def test_la_stessa_chat_non_entra_due_volte(tmp_path):
    c = _database_di_produzione(tmp_path / 'signals.db')
    main.migra(c)
    with pytest.raises(sqlite3.IntegrityError):
        c.execute('INSERT INTO chats(telegram_chat_id) VALUES (?)', (CHAT_A,))


# ------------------------------------------- la migrazione una volta sola

def test_db_migra_UNA_VOLTA_per_processo(tmp_path, monkeypatch):
    """La correzione del costo: prima girava a ogni connessione.

    Ogni richiesta apriva una transazione di SCRITTURA — anche le letture del feed,
    che XTrader interroga a raffica. Qui si contano le esecuzioni su dieci `db()`.
    """
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'contate.db'))
    monkeypatch.setattr(main, '_PERCORSI_MIGRATI', set())
    chiamate = []
    vera = main.migra
    monkeypatch.setattr(main, 'migra', lambda c: (chiamate.append(1), vera(c))[1])

    for _ in range(10):
        main.db().close()

    assert len(chiamate) == 1, f'migrazione eseguita {len(chiamate)} volte su 10 connessioni'


def test_due_database_diversi_nello_stesso_processo_sono_migrati_entrambi(tmp_path, monkeypatch):
    """Perche' e' un insieme di percorsi e non un booleano.

    Con un flag globale il secondo database resterebbe senza schema, e i test che
    usano un database per test — cioe' quasi tutti — vedrebbero «no such table».
    """
    monkeypatch.setattr(main, '_PERCORSI_MIGRATI', set())
    for nome in ('uno.db', 'due.db'):
        monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / nome))
        c = main.db()
        assert c.execute("SELECT name FROM sqlite_master WHERE name='users'").fetchone(), nome
        c.close()


class _ConnessioneCheRompeGliALTER:
    """Una connessione vera che fa fallire i soli `ALTER TABLE`, col messaggio dato.

    Serve perche' l'`except` di `migra()` va esercitato DA `migra()`. La prima
    versione di questo test apriva una connessione nuda e verificava che *sqlite*
    sollevasse «no such table»: misurava sqlite, non il nostro codice. Provato per
    sabotaggio — rimettendo l'`except` nudo il test restava VERDE, cioe' non
    proteggeva niente. E' la quarta volta in questa sessione che un test dice di
    coprire una cosa e ne copre un'altra, e ogni volta l'ha rivelato il sabotaggio,
    non la lettura.
    """

    def __init__(self, vera, messaggio):
        self._vera = vera
        self._messaggio = messaggio

    def execute(self, sql, *args):
        if sql.lstrip().upper().startswith('ALTER TABLE'):
            raise sqlite3.OperationalError(self._messaggio)
        return self._vera.execute(sql, *args)

    def __getattr__(self, nome):
        return getattr(self._vera, nome)


def test_l_ALTER_NON_ingoia_un_errore_diverso_da_colonna_duplicata(tmp_path):
    """«no such table» deve PROPAGARE: uno schema mancante non e' silenzio.

    Con l'`except` nudo, un database in cui le tabelle non esistono avrebbe
    attraversato la migrazione senza un errore, e il servizio sarebbe partito
    dichiarando successo su un database vuoto.

    **Il ramo opposto — «duplicate column name» va ingoiato» — e' coperto dal test di
    idempotenza**, non da un gemello di questo. Ci ho provato con un gemello e non
    reggeva: bloccare ogni `ALTER` sulla connessione finta impedisce alle colonne di
    esistere, quindi le `UPDATE` del travaso cadono per una ragione che non c'entra
    con cio' che il test voleva misurare. La copertura vera e' misurata: sostituendo
    la condizione con un `raise` incondizionato, `..._e_IDEMPOTENTE_...` diventa
    rosso, perche' la seconda migrazione trova le colonne gia' presenti — che e'
    esattamente il caso normale di ogni riavvio.
    """
    vera = _database_di_produzione(tmp_path / 'signals.db')
    finta = _ConnessioneCheRompeGliALTER(vera, 'no such table: parsers')
    with pytest.raises(sqlite3.OperationalError, match='no such table'):
        main.migra(finta)


# ------------------------------- i casi che avrebbero spento il servizio

def test_due_parser_che_differiscono_SOLO_per_maiuscole_non_bloccano_l_avvio(tmp_path):
    """Il bloccante di Fable 5 e GPT-5.5 sulla PR #22, misurato.

    `slug = lower(name)` mandava `Over15` e `over15` sullo stesso slug, l'indice
    UNIQUE non si creava, `migra()` sollevava — e `migra()` sta sul percorso di
    `db()`, cioe' di OGNI richiesta. Il feed che XTrader interroga avrebbe iniziato a
    dare 500 e non avrebbe piu' smesso: un guasto permanente, peggiore del difetto
    che la #21 ha corretto.

    Misurato prima della correzione:
        IntegrityError: UNIQUE constraint failed: parsers.user_id, parsers.slug

    La disambiguazione e' deterministica e per nome, non casuale: rieseguire la
    migrazione deve dare gli stessi slug, altrimenti l'idempotenza cade.
    """
    c = sqlite3.connect(tmp_path / 'collisione.db')
    for istruzione in SCHEMA_DI_PRODUZIONE:
        c.execute(istruzione)
    for nome in ('Over15', 'over15', 'OVER15'):
        c.execute('INSERT INTO parsers VALUES (?,?,?,?,?,?,?)',
                  (nome, 'H', '', '', '', '0', 'PUNTA'))
    c.execute('INSERT INTO profiles VALUES (?,?,?)', ('PIERO', '', 'Over15'))

    main.migra(c)  # non deve sollevare

    slug = dict(c.execute('SELECT name, slug FROM parsers').fetchall())
    assert len(set(slug.values())) == len(slug), f'slug ancora in collisione: {slug}'
    assert 'over15' in slug.values(), slug


def test_la_disambiguazione_degli_slug_e_STABILE_fra_due_esecuzioni(tmp_path):
    """Deterministica, altrimenti ogni riavvio rinomina gli slug dei clienti."""
    c = sqlite3.connect(tmp_path / 'stabile.db')
    for istruzione in SCHEMA_DI_PRODUZIONE:
        c.execute(istruzione)
    for nome in ('Uno', 'UNO', 'uno'):
        c.execute('INSERT INTO parsers VALUES (?,?,?,?,?,?,?)',
                  (nome, 'H', '', '', '', '0', 'PUNTA'))
    c.execute('INSERT INTO profiles VALUES (?,?,?)', ('PIERO', '', 'Uno'))
    main.migra(c)
    prima = dict(c.execute('SELECT name, slug FROM parsers').fetchall())
    main.migra(c)
    assert dict(c.execute('SELECT name, slug FROM parsers').fetchall()) == prima


def test_due_profili_che_differiscono_solo_per_maiuscole_non_bloccano_l_avvio(tmp_path):
    """Stessa classe su `users.slug`, cercata perche' era la stessa forma.

    Segnalato da GPT-5.5 come rischio manuale; l'ho trattato come il gemello del
    bloccante invece di aspettare che diventasse un incidente.
    """
    c = sqlite3.connect(tmp_path / 'profili.db')
    for istruzione in SCHEMA_DI_PRODUZIONE:
        c.execute(istruzione)
    for nome in ('PIERO', 'piero'):
        c.execute('INSERT INTO profiles VALUES (?,?,?)', (nome, '', 'X'))
    main.migra(c)
    slug = [r[0] for r in c.execute('SELECT slug FROM users').fetchall()]
    assert len(set(slug)) == len(slug) == 2, slug


def test_parsers_ha_una_colonna_id_a_cui_parser_chats_puo_puntare(tmp_path):
    """`parser_chats.parser_id` e' INTEGER e `parsers` aveva solo `name` TEXT.

    Segnalato da GPT-5.5: lo schema nuovo nasceva incoerente e `parser_chats` era
    una tabella morta — nessuna colonna a cui il suo `parser_id` potesse riferirsi.
    #2 prevede `parsers id`, e mancava perche' un PRIMARY KEY non si aggiunge con
    ALTER. Si aggiunge come colonna con indice UNIQUE, riempita dal `rowid`.
    """
    c = _database_di_produzione(tmp_path / 'signals.db')
    main.migra(c)
    colonne = [r[1] for r in c.execute('PRAGMA table_info(parsers)').fetchall()]
    assert 'id' in colonne, colonne
    ids = [r[0] for r in c.execute('SELECT id FROM parsers').fetchall()]
    assert all(i is not None for i in ids), f'id non riempiti: {ids}'
    assert len(set(ids)) == len(ids), f'id non univoci: {ids}'
    # e il collegamento diventa possibile
    chat = c.execute('SELECT id FROM chats LIMIT 1').fetchone()[0]
    c.execute('INSERT INTO parser_chats(parser_id, chat_id) VALUES (?,?)', (ids[0], chat))


def test_una_migrazione_fallita_non_lascia_la_connessione_aperta(tmp_path, monkeypatch):
    """Il terzo bloccante di Fable: `db()` perdeva una connessione per richiesta.

    Se `migra()` solleva, `db()` deve chiudere e rilanciare. Altrimenti ogni
    richiesta successiva ritenta, sbaglia, e lascia dietro un'altra connessione: un
    guasto che peggiora da solo mentre il traffico continua.

    Si misura la proprieta- vera — la connessione e- chiusa — usandola: una
    connessione sqlite chiusa solleva `ProgrammingError`. La prima versione di questo
    test provava a monkeypatchare `Connection.close`, e non si puo-: il tipo e-
    immutabile. Un test che non gira non e- una copertura.
    """
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'rotta.db'))
    monkeypatch.setattr(main, '_PERCORSI_MIGRATI', set())
    aperte = []
    vero_connect = sqlite3.connect
    monkeypatch.setattr(main.sqlite3, 'connect',
                        lambda *a, **k: (aperte.append(vero_connect(*a, **k)), aperte[-1])[1])
    monkeypatch.setattr(main, 'migra',
                        lambda c: (_ for _ in ()).throw(
                            sqlite3.OperationalError('finta rottura della migrazione')))

    with pytest.raises(sqlite3.OperationalError, match='finta rottura'):
        main.db()

    assert aperte, 'nessuna connessione aperta: il test non ha misurato niente'
    with pytest.raises(sqlite3.ProgrammingError):
        aperte[0].execute('SELECT 1')
    assert str(tmp_path / 'rotta.db') not in main._PERCORSI_MIGRATI, \
        'percorso marcato migrato nonostante il fallimento'


def test_la_connessione_ha_un_busy_timeout(tmp_path, monkeypatch):
    """Secondo bloccante di Fable: DDL concorrente fra processi.

    Il lock e- per-processo. Con piu- worker due processi eseguono la migrazione sullo
    stesso file, e senza `busy_timeout` il secondo riceve subito «database is locked»
    invece di aspettare: un deploy che parte rotto.
    """
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'timeout.db'))
    monkeypatch.setattr(main, '_PERCORSI_MIGRATI', set())
    c = main.db()
    try:
        assert c.execute('PRAGMA busy_timeout').fetchone()[0] >= 1000, 'busy_timeout non impostato'
    finally:
        c.close()


def test_l_ordine_resta_univoco_quando_un_parser_ne_ha_gia_uno(tmp_path):
    """Il Major di CodeRabbit sulla PR #22, e il difetto era reale nella prima forma.

    La versione precedente enumerava TUTTI i parser e scriveva solo quelli con
    `ordine IS NULL`: con `Alfa` a 1 e un `Beta` nuovo, l'enumerazione dava a `Beta`
    l'indice 1 e i due finivano pari. Il pareggio rende non deterministico
    esattamente cio- per cui questa colonna esiste — chi vince fra due parser dello
    stesso utente.

    La correzione parte da `MAX(ordine) + 1`. Misurato: `Alfa=0`, default`=1`,
    `Beta` nuovo -> `2`.
    """
    c = _database_di_produzione(tmp_path / 'ordine.db')
    main.migra(c)
    c.execute('INSERT INTO parsers(name, header) VALUES (?,?)', ('Beta', 'H'))
    main.migra(c)
    ordini = [r[0] for r in c.execute('SELECT ordine FROM parsers').fetchall()]
    assert all(o is not None for o in ordini), ordini
    assert len(set(ordini)) == len(ordini), f'ordine in pareggio: {ordini}'


def test_dieci_thread_insieme_migrano_UNA_volta_sola(tmp_path, monkeypatch):
    """Il doppio controllo dentro il lock, esercitato da thread veri.

    Chiesto da CodeRabbit, e `CLAUDE.md` lo pretende fra gli scenari di resilienza
    («richieste concorrenti»). I test precedenti percorrevano solo la via
    sequenziale: togliendo il secondo controllo dentro il lock restavano verdi.
    """
    import threading
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'concorrenti.db'))
    monkeypatch.setattr(main, '_PERCORSI_MIGRATI', set())
    chiamate = []
    vera = main.migra
    conta = threading.Lock()

    def contata(c):
        with conta:
            chiamate.append(1)
        return vera(c)

    monkeypatch.setattr(main, 'migra', contata)
    via = threading.Event()
    errori = []

    def apri():
        via.wait()  # partenza simultanea: senza, i thread si serializzano da soli
        try:
            main.db().close()
        except Exception as e:  # noqa: BLE001 - l'errore E' il risultato in prova
            errori.append(e)

    thread = [threading.Thread(target=apri) for _ in range(10)]
    for t in thread:
        t.start()
    via.set()
    for t in thread:
        t.join(timeout=15)

    assert not errori, f'connessioni fallite in concorrenza: {errori}'
    assert len(chiamate) == 1, f'migrazione eseguita {len(chiamate)} volte su 10 thread'


def test_due_profili_che_condividono_una_chat_non_la_duplicano(tmp_path):
    """Il comportamento della chat condivisa, FISSATO invece che accidentale.

    Segnalato da CodeRabbit come Major sull'isolamento: con `INSERT OR IGNORE` e
    l'unicita- globale della chat, se due profili elencano lo stesso `chat_id` il
    secondo non ottiene una riga e la chat resta attribuita al primo.

    Verificato: oggi **non produce dati sbagliati**, perche' esiste un solo profilo.
    E la regola che scrive e' quella REGISTRATA in #2 — «chats … owner_user_id …
    UNIQUE (telegram_chat_id, message_thread_id)», cioe- una riga per chat con un
    solo proprietario. Cambiarla in unicita- per utente sarebbe contraddire una
    decisione chiusa, e la decisione vera — a chi appartiene una chat che due utenti
    dichiarano — e' del PR sul dispatch multi-parser, dove il webhook deve sceglierne
    uno.

    Questo test non approva quel comportamento: lo **fissa**, cosi' quando la
    decisione arrivera- si vedra- cosa cambia. Se il PR sul dispatch rendera-
    l'unicita- per utente, questo test diventera- rosso, che e' il punto.
    """
    c = sqlite3.connect(tmp_path / 'condivisa.db')
    for istruzione in SCHEMA_DI_PRODUZIONE:
        c.execute(istruzione)
    c.execute('INSERT INTO profiles VALUES (?,?,?)', ('PIERO', CHAT_A, 'X'))
    c.execute('INSERT INTO profiles VALUES (?,?,?)', ('ALTRO', CHAT_A, 'X'))
    main.migra(c)

    righe = c.execute('SELECT telegram_chat_id, owner_user_id FROM chats').fetchall()
    assert len(righe) == 1, f'la chat condivisa e- stata duplicata: {righe}'
    utenti = {r[0]: r[1] for r in c.execute('SELECT first_name, id FROM users').fetchall()}
    assert len(utenti) == 2, utenti
    assert righe[0][1] in utenti.values(), righe


def _con_chat_duplicate(percorso: Path) -> sqlite3.Connection:
    """Un database che contiene GIA' due righe per la stessa chat senza topic.

    Lo stato si costruisce a mano perche' e' proprio quello che la migrazione non
    sapeva attraversare: le due righe si inseriscono PRIMA che l'indice esista,
    esattamente come faceva la versione che ha prodotto il difetto.
    """
    c = _database_di_produzione(percorso)
    for istruzione in main.SCHEMA_MULTIUTENTE:
        c.execute(istruzione)
    c.execute('INSERT INTO chats(telegram_chat_id, owner_user_id) VALUES (?,?)', (CHAT_A, 1))
    c.execute('INSERT INTO chats(telegram_chat_id, owner_user_id) VALUES (?,?)', (CHAT_A, 2))
    return c


def test_un_database_con_chat_GIA_duplicate_resta_attraversabile(tmp_path):
    """La migrazione non solleva su duplicati che trova, li unifica.

    Misurato sul codice precedente, e la riga che sollevava non era l'INSERT:

        c.execute('CREATE UNIQUE INDEX IF NOT EXISTS chats_chat_topic ...')
        sqlite3.IntegrityError: UNIQUE constraint failed: index 'chats_chat_topic'

    Cioe- l'indice che serve a impedire i duplicati non si poteva creare **a causa
    dei duplicati**, e nessun riavvio successivo lo avrebbe cambiato. `migra()` sta
    sul percorso di `db()`, quindi ogni richiesta — feed di XTrader compreso —
    avrebbe preso 500 per sempre. Stessa classe della collisione di slug: una
    migrazione sul percorso di ogni richiesta non puo- sollevare per dati che
    esistono.
    """
    c = _con_chat_duplicate(tmp_path / 'duplicate.db')

    main.migra(c)  # non deve sollevare

    righe = c.execute('SELECT telegram_chat_id, owner_user_id FROM chats'
                      ' WHERE telegram_chat_id=?', (CHAT_A,)).fetchall()
    assert len(righe) == 1, f'i duplicati non sono stati unificati: {righe}'
    assert righe[0][1] == 1, (
        f'ha tenuto il proprietario sbagliato ({righe[0][1]}): sopravvive la riga piu- '
        'vecchia, cioe- il primo che ha dichiarato la chat')
    # E adesso l'indice esiste davvero, altrimenti la deduplica avrebbe solo
    # nascosto il problema fino al prossimo inserimento.
    with pytest.raises(sqlite3.IntegrityError):
        c.execute('INSERT INTO chats(telegram_chat_id) VALUES (?)', (CHAT_A,))


def test_la_deduplica_non_ORFANA_le_associazioni_parser_chat(tmp_path):
    """Chi puntava alla riga cancellata punta alla sopravvissuta.

    `parser_chats.chat_id` riferisce `chats.id`. Cancellare il duplicato senza
    ripuntare le associazioni non solleva niente: lascia una riga che riferisce un
    `id` che non esiste piu-, e il parser smette di ricevere da quella chat **in
    silenzio**. E' la perdita di segnale che questo servizio esiste per evitare.

    Oggi nessun codice scrive in `parser_chats` (verificato: nessun `INSERT INTO
    parser_chats` in `main.py`) — quindi questo non e- un bug attivo, e- il PR sul
    dispatch multi-parser che lo renderebbe attivo trovandoselo gia- chiuso.
    """
    c = _con_chat_duplicate(tmp_path / 'orfane.db')
    perdente = c.execute('SELECT MAX(id) FROM chats WHERE telegram_chat_id=?',
                         (CHAT_A,)).fetchone()[0]
    vincente = c.execute('SELECT MIN(id) FROM chats WHERE telegram_chat_id=?',
                         (CHAT_A,)).fetchone()[0]
    assert perdente != vincente
    c.execute('INSERT INTO parser_chats(parser_id, chat_id) VALUES (?,?)', (7, perdente))

    main.migra(c)

    righe = c.execute('SELECT parser_id, chat_id FROM parser_chats').fetchall()
    assert righe == [(7, vincente)], (
        f'associazione orfana o persa: {righe}, la chat sopravvissuta e- {vincente}')


def test_una_seconda_migrazione_non_TENTA_di_reinserire_le_chat(tmp_path):
    """Lo stato stabile della migrazione e- un no-op, non una scrittura ripetuta.

    Distingue le DUE forme che producono lo stesso contenuto finale, e serve perche'
    senza di esso il controllo di esistenza nel travaso sarebbe codice che nessun
    test protegge — la deduplica piu- sotto ripulisce comunque cio- che un
    `INSERT OR IGNORE` sporca, quindi ogni assert sul CONTENUTO passa in entrambi i
    casi. Misurato su tre migrazioni di fila dello stesso database:

        controllo di esistenza:  sqlite_sequence(chats) = 1, 1, 1
        INSERT OR IGNORE:        sqlite_sequence(chats) = 1, 2, 3

    La riga sopravvissuta ha `id` 1 in entrambi, quindi nessun riferimento si rompe:
    cio- che cambia e- che la seconda forma **tenta** un inserimento a ogni avvio del
    processo e brucia un `id` anche quando viene ignorato. Su un database su volume,
    riavviato a ogni deploy, e- una scrittura per chat per deploy per sempre.

    `sqlite_sequence` e- escluso da `_fotografia` (con la sua motivazione scritta), e
    proprio per questo va guardato QUI: e- l-unica traccia osservabile di un
    inserimento che non ha lasciato righe.
    """
    # Il profilo PIERO con le sue due chat lo mette gia- il database di produzione.
    c = _database_di_produzione(tmp_path / 'nooop.db')
    main.migra(c)

    def _sequenza():
        riga = c.execute("SELECT seq FROM sqlite_sequence WHERE name='chats'").fetchone()
        return riga[0] if riga else None

    prima = _sequenza()
    assert prima is not None, 'nessuna chat inserita: il test non misura niente'
    main.migra(c)
    dopo = _sequenza()

    assert dopo == prima, (
        f'la seconda migrazione ha bruciato un id ({prima} -> {dopo}): sta tentando '
        'di reinserire una chat che esiste gia-, a ogni avvio del processo')


# ------------------------------------- i CONSUMATORI delle tabelle che ho esteso
#
# Regola 2-bis, e questa volta contro di me. Il messaggio del commit precedente
# diceva che le colonne nuove «non cambiano nessuna SELECT esistente, perche' tutte
# nominano le colonne che leggono». Vero, e insufficiente: ho cercato le SELECT e
# non le INSERT. `POST /api/parsers` inseriva SETTE valori senza elencare le
# colonne, e con quattordici colonne quell'INSERT non e' piu' valido.
#
# Trovato da Claude Fable 5 come «`parsers.id` NULL sui parser nuovi» — cioe' il
# sintomo piu' lieve dei due. Misurando il piu' grave si e' visto per primo:
#
#     OperationalError: table parsers has 14 columns but 7 values were supplied

@pytest.fixture
def servizio(tmp_path, monkeypatch):
    """Il relay in processo, con un database vuoto solo suo e il token noto."""
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'api.db'))
    monkeypatch.setattr(main, '_PERCORSI_MIGRATI', set())
    monkeypatch.setattr(main, 'TOKEN', 'token-di-prova')
    return 'token-di-prova'


def _parser_salvato(nome, header='UN HEADER'):
    return main.ParserIn(name=nome, header=header)


def test_creare_un_parser_via_API_FUNZIONA_ancora(servizio):
    """La regressione piu- grave di questo PR, introdotta da me.

    `INSERT OR REPLACE INTO parsers VALUES (?,?,?,?,?,?,?)` non elencava le colonne,
    quindi dipendeva dal loro NUMERO. Aggiungerne sette lo ha rotto: ogni creazione
    o modifica di parser dalla API rispondeva 500. Misurato sul codice precedente:

        OperationalError: table parsers has 14 columns but 7 values were supplied

    E- un endpoint che funzionava prima di questo PR: senza questo test la
    migrazione sarebbe arrivata in produzione rompendolo.
    """
    main.save_parser(_parser_salvato('Nuovo_Parser'), x_admin_token=servizio)

    c = main.db()
    riga = c.execute('SELECT header FROM parsers WHERE name=?', ('Nuovo_Parser',)).fetchone()
    c.close()
    assert riga == ('UN HEADER',), riga


def test_un_parser_creato_via_API_ha_SUBITO_utente_slug_ordine_e_id(servizio):
    """Il secondo bloccante di Fable 5, e non e- cosmetico.

    `UPDATE parsers SET id=rowid` gira in `migra()`, cioe- una volta per processo. Un
    parser creato dopo l'avvio restava con `id`, `user_id`, `slug` e `ordine` a NULL
    fino al riavvio successivo. Conseguenze reali, non estetiche:

    - `parser_chats.chat_id` riferisce `parsers.id`: con `id` NULL quel parser non
      puo- essere associato a nessuna chat;
    - l'indice `UNIQUE (user_id, slug)` non vincola le righe con `user_id` NULL
      (`NULL != NULL`, la stessa semantica di `TOPIC_CHAT`), quindi la riga sfugge
      al vincolo che dovrebbe proteggere l'isolamento. Osservato anche da GPT-5.5.
    """
    main.save_parser(_parser_salvato('Parser_Nuovo'), x_admin_token=servizio)

    c = main.db()
    riga = c.execute('SELECT user_id, slug, ordine, id FROM parsers WHERE name=?',
                     ('Parser_Nuovo',)).fetchone()
    c.close()
    utente, slug, ordine, identificativo = riga
    assert utente is not None, 'user_id NULL: la riga sfugge a UNIQUE (user_id, slug)'
    assert slug == 'parser_nuovo', slug
    assert ordine is not None, 'ordine NULL: il tie-break fra parser resta indeciso'
    assert identificativo is not None, 'id NULL: parser_chats non puo- riferirlo'


def test_modificare_un_parser_NON_lo_stacca_dal_suo_utente(servizio):
    """`INSERT OR REPLACE` cancella la riga e la reinserisce: le colonne non
    nominate tornano al default.

    Misurato sul codice precedente, sul parser di default dopo la migrazione:

        prima : ('Parser_Telegram_XTrader_v1', 1, 'parser_telegram_xtrader_v1', 0, 1)
        dopo  : ('Parser_Telegram_XTrader_v1', None, None, None, None)

    Cioe- cambiare l'header di un parser dalla API lo staccava dal suo proprietario
    e ne azzerava l'`id`. Su un servizio multiutente e- una perdita di isolamento
    provocata da una modifica di routine.
    """
    c = main.db()
    prima = c.execute('SELECT user_id, slug, ordine, id FROM parsers WHERE name=?',
                      (main.DEFAULT_PARSER,)).fetchone()
    c.close()
    assert all(v is not None for v in prima), f'il test non parte da uno stato utile: {prima}'

    main.save_parser(_parser_salvato(main.DEFAULT_PARSER, header='HEADER CAMBIATO'),
                     x_admin_token=servizio)

    c = main.db()
    dopo = c.execute('SELECT user_id, slug, ordine, id FROM parsers WHERE name=?',
                     (main.DEFAULT_PARSER,)).fetchone()
    header = c.execute('SELECT header FROM parsers WHERE name=?',
                       (main.DEFAULT_PARSER,)).fetchone()[0]
    c.close()
    assert header == 'HEADER CAMBIATO', 'la modifica non e- stata applicata'
    assert dopo == prima, (
        f'la modifica ha azzerato le colonne nuove: {prima} -> {dopo}')


def test_il_travaso_NON_attribuisce_un_profilo_a_un_utente_Telegram_omonimo(tmp_path):
    """Il primo bloccante di Fable 5: `first_name` non e- univoco.

    Il travaso cercava l'utente del profilo con `SELECT id FROM users WHERE
    first_name=?`. I nomi Telegram non sono univoci per niente: al primo login di un
    utente che si chiama come un profilo, chat, `signals.user_id` e parser sarebbero
    stati attribuiti a LUI. Violazione di isolamento silenziosa, e la peggiore
    specie: nessun errore, dati che finiscono nell'account sbagliato.

    Il lookup passa ora da `origin_profile`, che e- il profilo di provenienza e non
    cambia quando l'utente fa login — `first_name` invece verrebbe sovrascritto dal
    nome vero di Telegram, che e- esattamente cio- che rende quel lookup instabile.

    **L'ORDINE di questo test e- la sua sostanza, e la prima versione ce l'aveva
    sbagliato.** Inseriva l'omonimo DOPO l'utente del profilo, e cosi- il lookup per
    `first_name` trovava comunque quello giusto — la riga legittima ha il `rowid` piu-
    basso e la scansione la incontra per prima. Rimettendo `first_name` il test
    restava VERDE: copertura zero, e la quinta volta in questa sessione che un
    sabotaggio smentisce un test che sembrava buono.

    L'ordine che rompe davvero e- quello opposto, e non e- artificioso: l'omonimo
    esiste GIA- — ha fatto login prima — e il profilo nasce dopo, da
    `POST /api/profiles`. Al riavvio successivo il travaso incontra un profilo nuovo e
    un utente omonimo piu- vecchio, e con `first_name` gli consegna le chat.
    """
    c = _database_di_produzione(tmp_path / 'omonimi.db')
    main.migra(c)

    # 1. Un utente qualunque entra dal login Telegram. Si chiama MARCO.
    c.execute("INSERT INTO users(telegram_id, first_name, slug, status)"
              " VALUES ('99999', 'MARCO', 'marco', 'registrato')")
    estraneo = c.execute('SELECT id FROM users WHERE telegram_id=?', ('99999',)).fetchone()[0]
    # 2. Solo DOPO nasce un profilo con quel nome, e con una chat sua.
    c.execute('INSERT INTO profiles VALUES (?,?,?)', ('MARCO', '-1003333333333', 'X'))

    main.migra(c)  # 3. il riavvio successivo

    proprietario = c.execute('SELECT owner_user_id FROM chats WHERE telegram_chat_id=?',
                             ('-1003333333333',)).fetchone()[0]
    assert proprietario != estraneo, (
        f'la chat del profilo MARCO e- stata consegnata all-utente Telegram omonimo '
        f'(id {estraneo}): e- una perdita di isolamento, silenziosa')
    suo = c.execute('SELECT id FROM users WHERE origin_profile=?', ('MARCO',)).fetchone()
    assert suo is not None, 'nessun utente creato per il profilo MARCO'
    assert proprietario == suo[0], (proprietario, suo[0])


def test_ogni_campo_di_ParserIn_e_una_colonna_di_parsers(tmp_path, monkeypatch):
    """Il guardiano della lista derivata in `save_parser`.

    Quella funzione costruisce l'INSERT da `ParserIn.model_fields`, cioe- dal modello,
    per non tenere una seconda lista che divergerebbe. Il patto che regge quella scelta
    e- che ogni campo del modello sia anche una colonna della tabella: un campo nuovo
    senza colonna darebbe «no such column» a runtime, sull'endpoint, in produzione.

    Qui diventa rosso in CI, che e- il posto giusto.
    """
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'campi.db'))
    monkeypatch.setattr(main, '_PERCORSI_MIGRATI', set())
    c = main.db()
    colonne = {r[1] for r in c.execute('PRAGMA table_info(parsers)')}
    c.close()

    campi = set(main.ParserIn.model_fields)
    assert campi <= colonne, (
        f'campi di ParserIn senza colonna in parsers: {sorted(campi - colonne)}. '
        'save_parser costruisce l-INSERT dal modello, quindi questi campi darebbero '
        '«no such column» sull-endpoint')


def test_origin_profile_e_UNICO_anche_sui_database_gia_migrati(tmp_path):
    """`ALTER TABLE ADD COLUMN` non porta il vincolo UNIQUE con se-.

    Segnalato da GPT-5.5, ed e- esatto: `users` creata da zero ha
    `origin_profile TEXT UNIQUE`, ma un database creato da una versione intermedia di
    questo ramo riceve la colonna dall'ALTER — e l'ALTER non sa aggiungere vincoli. I
    due percorsi finivano quindi con garanzie DIVERSE, e quello senza garanzia e-
    proprio il percorso dei database che esistono gia-: due utenti con lo stesso
    profilo di provenienza, cioe- il lookup ambiguo che `origin_profile` esiste per
    chiudere.

    Il vincolo si esprime come indice, la stessa forma usata per
    `UNIQUE (user_id, slug)` sui parser e per lo stesso motivo.
    """
    c = _database_di_produzione(tmp_path / 'alterata.db')
    # Lo stato intermedio: `users` esiste, senza `origin_profile`.
    _crea_users(c, con_origin_profile=False)
    colonne = {r[1] for r in c.execute('PRAGMA table_info(users)')}
    assert 'origin_profile' not in colonne, 'il test non parte dallo stato che vuole'

    main.migra(c)

    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO users(origin_profile, first_name) VALUES ('PIERO', 'x')")
    # E i NULL restano ammessi in piu- copie: chi non viene da un profilo e- il caso
    # normale di tutti i prossimi utenti, e un vincolo che li rifiutasse bloccherebbe
    # il login Telegram.
    c.execute("INSERT INTO users(first_name, slug) VALUES ('Tizio', 'tizio')")
    c.execute("INSERT INTO users(first_name, slug) VALUES ('Caio', 'caio')")


def test_il_proprietario_dei_parser_senza_utente_e_quello_CHIESTO(tmp_path):
    """`_completa_colonne_nuove` non cabla Piero: obbedisce al chiamante.

    Il guard che Claude Fable 5 chiede per il bloccante «ogni parser senza utente
    finisce sotto Piero». Oggi Piero e- l'unico utente e l'assegnazione e- corretta;
    il rischio e- il PR che rendera- l'endpoint multiutente e non si accorgera- di una
    costante cablata in fondo a una funzione di migrazione.

    Con il profilo come argomento obbligatorio la decisione sta nei due chiamanti, e
    questo test dimostra che la funzione la RISPETTA invece di ignorarla — cioe- che
    passare il proprietario giusto bastera-.
    """
    c = _database_di_produzione(tmp_path / 'proprietario.db')
    c.execute('INSERT INTO profiles VALUES (?,?,?)', ('ALTRO', '-1004444444444', 'X'))
    main.migra(c)
    altro = c.execute('SELECT id FROM users WHERE origin_profile=?', ('ALTRO',)).fetchone()[0]
    piero = c.execute('SELECT id FROM users WHERE origin_profile=?',
                      (main.PIERO_PROFILE,)).fetchone()[0]

    c.execute('INSERT INTO parsers(name, header) VALUES (?,?)', ('Di_Altro', 'H'))
    main._completa_colonne_nuove(c, 'ALTRO')

    proprietario = c.execute('SELECT user_id FROM parsers WHERE name=?',
                             ('Di_Altro',)).fetchone()[0]
    assert proprietario == altro, (
        f'assegnato a {proprietario} invece che a ALTRO ({altro}); Piero e- {piero}')


def test_un_database_con_origin_profile_GIA_duplicato_resta_attraversabile(tmp_path):
    """La stessa classe della chat duplicata, e l'ho reintrodotta io un commit dopo.

    Segnalato insieme da Claude Fable 5 e GPT-5.5 sul commit che aggiungeva l'indice:
    `CREATE UNIQUE INDEX users_origin_profile` **solleva** su un database che contiene
    gia- due righe con lo stesso `origin_profile` — cioe- esattamente lo stato che il
    vincolo mancante permetteva, e che l'indice serve a chiudere. `migra()` sta sul
    percorso di `db()`: il deploy andrebbe in crash a ogni richiesta.

    **La deduplica qui NON cancella righe**, e la differenza con `chats` e- sostanziale:
    una riga di `users` puo- possedere chat, parser e segnali, e cancellarla
    perderebbe dati di un cliente. Si azzera invece `origin_profile` sulle righe
    perdenti — l'unica cosa che puo- essere ambigua — tenendo l'`id` piu- basso come
    portatore dell'etichetta. Nessun utente sparisce, nessuna proprieta- cambia, e
    l'indice diventa creabile perche- i NULL multipli sono ammessi.
    """
    c = _database_di_produzione(tmp_path / 'utenti_doppi.db')
    # Lo stato senza vincolo: la colonna c'e-, l'indice no. E- il database migrato da
    # una versione intermedia di questo ramo.
    _crea_users(c, con_origin_profile=True)
    c.execute("INSERT INTO users(origin_profile, first_name, slug) VALUES ('PIERO','PIERO','piero')")
    c.execute("INSERT INTO users(origin_profile, first_name, slug) VALUES ('PIERO','PIERO','piero-2')")
    doppi = [r[0] for r in c.execute(
        "SELECT id FROM users WHERE origin_profile='PIERO' ORDER BY id").fetchall()]
    assert len(doppi) == 2, 'il test non parte dallo stato che vuole'

    main.migra(c)  # non deve sollevare

    etichettati = [r[0] for r in c.execute(
        "SELECT id FROM users WHERE origin_profile='PIERO'").fetchall()]
    assert etichettati == [doppi[0]], (
        f'l-etichetta doveva restare al solo id piu- basso {doppi[0]}: {etichettati}')
    superstiti = {r[0] for r in c.execute('SELECT id FROM users').fetchall()}
    assert set(doppi) <= superstiti, (
        f'un utente e- stato CANCELLATO: {sorted(doppi)} -> {sorted(superstiti)}. '
        'Una riga di users possiede chat, parser e segnali: qui non si cancella')
    # E l'indice adesso c'e- davvero.
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("UPDATE users SET origin_profile='PIERO' WHERE id=?", (doppi[1],))


def test_parsers_name_e_ancora_una_chiave_GLOBALE(tmp_path, monkeypatch):
    """Il test-guardia che Claude Fable 5 chiede per `save_parser`.

    L'`UPDATE ... WHERE name=?` di quell'endpoint non filtra per `user_id`, e oggi e-
    corretto **proprio perche-** `parsers.name` e- PRIMARY KEY globale: esiste una
    sola riga per nome, quindi non c'e- nessun parser di un altro utente da
    sovrascrivere. La correttezza dipende quindi da una proprieta- dello SCHEMA, non
    da qualcosa scritto nell'endpoint.

    Il giorno che i nomi diventeranno unici PER UTENTE — il PR sul login, dove due
    clienti possono chiamare «Over 1,5» il proprio parser — quella `WHERE` colpirebbe
    la riga di un altro. Questo test diventa rosso in quel momento e dice dove
    guardare, che e- l'unica forma di guardia possibile per un difetto che non esiste
    ancora.
    """
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'chiave.db'))
    monkeypatch.setattr(main, '_PERCORSI_MIGRATI', set())
    c = main.db()
    chiavi = [r[1] for r in c.execute('PRAGMA table_info(parsers)') if r[5]]
    c.close()

    assert chiavi == ['name'], (
        f'la chiave primaria di parsers e- cambiata: {chiavi}. `save_parser` fa '
        '`UPDATE parsers SET ... WHERE name=?` senza filtrare per `user_id`, e questo '
        'e- corretto solo se `name` identifica UNA riga in tutto il servizio. Con nomi '
        'unici per utente quella WHERE sovrascrive il parser di un altro: aggiungere '
        '`AND user_id=?` prima di cambiare la chiave')


def test_la_deduplica_di_origin_profile_e_PER_PROFILO_non_globale(tmp_path):
    """Due gruppi di duplicati distinti: ciascuno tiene il suo.

    Suggerito da GPT-5.5, e misurando si e- visto che serve. Con un solo gruppo di
    duplicati `SELECT MIN(id) ... GROUP BY origin_profile` e `SELECT MIN(id) ...` senza
    GROUP BY si comportano in modo IDENTICO: il test a un gruppo non distingue la
    deduplica per profilo da una deduplica globale, che invece lascerebbe
    l'etichetta a UN SOLO utente in tutto il servizio e spoglierebbe tutti gli altri.

    Togliendo il `GROUP BY` due test diventano rossi — quello sugli omonimi e quello
    sul proprietario dei parser — ma per ragioni che non hanno niente a che vedere col
    loro nome: coprono questo invariante per caso, perche' costruiscono due profili.
    Riscrivere uno di quei due lo lascerebbe scoperto in silenzio. Qui l'invariante ha
    un nome, che e- l'unico modo perche- resti protetto.

    I NULL multipli restano intatti: non appartengono a nessun gruppo.
    """
    c = _database_di_produzione(tmp_path / 'due_gruppi.db')
    _crea_users(c, con_origin_profile=True)
    for profilo, slug in (('PIERO', 'piero'), ('PIERO', 'piero-2'),
                          ('ALTRO', 'altro'), ('ALTRO', 'altro-2')):
        c.execute('INSERT INTO users(origin_profile, first_name, slug) VALUES (?,?,?)',
                  (profilo, profilo, slug))
    # Due utenti senza profilo: il caso normale di chi arrivera- dal login.
    c.execute("INSERT INTO users(first_name, slug) VALUES ('Tizio','tizio')")
    c.execute("INSERT INTO users(first_name, slug) VALUES ('Caio','caio')")
    attesi = {profilo: identificativo for profilo, identificativo in c.execute(
        'SELECT origin_profile, MIN(id) FROM users WHERE origin_profile IS NOT NULL'
        ' GROUP BY origin_profile').fetchall()}
    assert len(attesi) == 2, attesi

    main.migra(c)

    restano = dict(c.execute('SELECT origin_profile, id FROM users'
                             ' WHERE origin_profile IS NOT NULL').fetchall())
    assert restano == attesi, (
        f'la deduplica non e- per profilo: atteso {attesi}, trovato {restano}. Con una '
        'deduplica GLOBALE un solo utente in tutto il servizio resta associato al suo '
        'profilo e tutti gli altri perdono l-etichetta')
    senza = c.execute('SELECT COUNT(*) FROM users WHERE origin_profile IS NULL').fetchone()[0]
    assert senza == 4, (
        f'{senza} righe senza profilo invece di 4: due erano NULL dall-inizio e due '
        'sono le perdenti azzerate, e nessuna delle prime due va toccata')


def test_la_deduplica_regge_un_parser_associato_a_ENTRAMBE_le_duplicate(tmp_path):
    """Il ri-puntamento non puo- collidere con l-associazione che esiste gia-.

    `[REAL_FINDING]` di GPT-5.6 Sol al gate finale, ed e- la QUARTA volta in questo PR
    che compare la stessa classe — questa volta **dentro la correzione** che chiudeva la
    terza. `parser_chats` ha `PRIMARY KEY (parser_id, chat_id)`: se un parser era
    associato a entrambe le righe duplicate, spostare la seconda sulla prima crea una
    riga che c'e- gia-. Misurato sul codice precedente:

        sqlite3.IntegrityError: UNIQUE constraint failed:
            parser_chats.parser_id, parser_chats.chat_id

    E siccome `migra()` sta sul percorso di `db()`, di nuovo: 500 su ogni richiesta,
    per sempre. La lezione e- che aggiungere una scrittura a una migrazione significa
    chiedersi di nuovo «e se i dati la rendessero impossibile?», anche quando la
    scrittura serve a rendere possibile un'altra cosa.

    `UPDATE OR IGNORE` sposta cio- che puo- spostarsi; la `DELETE` che segue toglie le
    righe che non si sono mosse perche- la destinazione esisteva gia-. Niente va perso:
    l'associazione `(parser, vincente)` c'e- in entrambi i casi.
    """
    c = _database_di_produzione(tmp_path / 'doppia_associazione.db')
    for istruzione in main.SCHEMA_MULTIUTENTE:
        c.execute(istruzione)
    c.execute('INSERT INTO chats(telegram_chat_id, owner_user_id) VALUES (?,?)', (CHAT_A, 1))
    c.execute('INSERT INTO chats(telegram_chat_id, owner_user_id) VALUES (?,?)', (CHAT_A, 2))
    vincente, perdente = [r[0] for r in c.execute(
        'SELECT id FROM chats WHERE telegram_chat_id=? ORDER BY id', (CHAT_A,)).fetchall()]
    c.execute('INSERT INTO parser_chats(parser_id, chat_id) VALUES (7,?)', (vincente,))
    c.execute('INSERT INTO parser_chats(parser_id, chat_id) VALUES (7,?)', (perdente,))

    main.migra(c)  # non deve sollevare

    righe = c.execute('SELECT parser_id, chat_id FROM parser_chats').fetchall()
    assert righe == [(7, vincente)], (
        f'atteso la sola associazione (7, {vincente}): {righe}')
    superstiti = c.execute('SELECT COUNT(*) FROM chats WHERE telegram_chat_id=?',
                           (CHAT_A,)).fetchone()[0]
    assert superstiti == 1, superstiti


def test_il_parser_di_un_profilo_appartiene_a_QUEL_profilo(tmp_path):
    """`profiles.parser` dice di chi e- il parser: la migrazione deve leggerlo.

    `[REAL_FINDING]` di GPT-5.6 Sol, piu- preciso della versione di Fable 5 sullo stesso
    punto: non «un giorno potrebbe assegnare male», ma «assegna male ADESSO, e
    l'informazione giusta e- nella tabella che il ciclo sta gia- leggendo».

    Misurato sul codice precedente, con due profili e un parser per ciascuno:

        profilo -> utente: {'PIERO': 1, 'ALTRO': 2}
        parser  -> utente: {'Parser_Telegram_XTrader_v1': 1, 'Parser_Di_Altro': 1}
                                                                             ^ ALTRO e- 2

    Il parser del profilo ALTRO finiva a Piero. Un secondo profilo si crea da
    `POST /api/profiles`, quindi non e- uno stato ipotetico.

    Il proprietario per difetto resta per i parser che NESSUN profilo nomina — quelli
    non hanno un'appartenenza da leggere, e lasciarli senza utente li terrebbe fuori
    dall'indice `UNIQUE (user_id, slug)`.
    """
    c = _database_di_produzione(tmp_path / 'proprieta_parser.db')
    c.execute('INSERT INTO parsers(name, header) VALUES (?,?)', ('Parser_Di_Altro', 'H'))
    c.execute('INSERT INTO profiles VALUES (?,?,?)', ('ALTRO', CHAT_B, 'Parser_Di_Altro'))

    main.migra(c)

    utenti = dict(c.execute('SELECT origin_profile, id FROM users').fetchall())
    parser = dict(c.execute('SELECT name, user_id FROM parsers').fetchall())
    assert parser['Parser_Di_Altro'] == utenti['ALTRO'], (
        f"Parser_Di_Altro e- del profilo ALTRO (utente {utenti['ALTRO']}) ma e- stato "
        f"assegnato all-utente {parser['Parser_Di_Altro']}")
    assert parser[main.DEFAULT_PARSER] == utenti['PIERO'], parser
    # `Secondo_Parser` non e- nominato da nessun profilo: resta al proprietario per
    # difetto, e soprattutto NON resta senza utente.
    assert parser['Secondo_Parser'] == utenti['PIERO'], parser
