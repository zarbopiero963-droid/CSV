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
