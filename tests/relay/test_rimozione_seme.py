"""La rimozione del seme (#25, lavoro E — PR 4 della sequenza #2).

Fino a questo PR `migra()` reinseriva a ogni avvio `Parser_Telegram_XTrader_v1`
e il profilo PIERO (`INSERT OR IGNORE`: protegge dal duplicato, non dalla
resurrezione). Conseguenze misurate nella #25: cancellare un parser non era
durevole; rinominarlo produceva un doppione garantito, e il doppione vecchio
era quello che il profilo continuava a nominare — il parser rinominato non
girava MAI. Con il seme muore anche la semina bulk di `parser_chats`, che
risuscitava i link a ogni avvio: il loro ciclo di vita passa alle scritture
dei profili (`POST/DELETE /api/profiles`), come annunciato nel docstring della
semina stessa e deferito dalla PR #44.

Cosa vincolano questi test:

- una cancellazione (parser o profilo) sopravvive al riavvio;
- una rinomina non lascia doppioni;
- un database vergine NON riceve nessun seme;
- il webhook non solleva piu' 404 su un profilo che punta a un parser
  cancellato (pericolo 2 della #25: senza seme il retry-loop di Telegram
  sarebbe diventato permanente);
- i link seguono il profilo: nascono al salvataggio, muoiono col profilo o
  quando il profilo cambia parser, e un link cancellato a mano NON risuscita;
- la riconciliazione e' PER-PROFILO: non tocca i link che il profilo non ha
  creato (i multi-parser della PR #44).
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE))

import main  # noqa: E402 - dopo l'inserimento del percorso
from tests.ambiente import CHIAVI_PERICOLOSE, TOKEN_DI_PROVA  # noqa: E402
from tests.dati import relay_in_processo  # noqa: E402
from tests.relay.test_webhook import (  # noqa: E402
    BOT_FINTO, CHAT, MESSAGGIO_VALIDO, RichiestaFinta)


@pytest.fixture(autouse=True)
def _ambiente_pulito(monkeypatch):
    """Nessuna variabile della macchina entra in questi test."""
    for chiave in CHIAVI_PERICOLOSE:
        monkeypatch.delenv(chiave, raising=False)
    monkeypatch.setattr(main, 'TOKEN', TOKEN_DI_PROVA)


def _relay(tmp_path, monkeypatch, nome, chat_ids='', vergine=False):
    """Il relay in processo su un database di produzione simulato (o vergine).

    Delega a `relay_in_processo` (fonte unica, `tests/dati.py`): qui resta solo
    il segreto del webhook, che e' l'unica cosa specifica di questo file.
    """
    monkeypatch.setattr(main, 'SEGRETO_WEBHOOK', main.webhook_secret(BOT_FINTO))
    return relay_in_processo(monkeypatch, tmp_path / nome,
                             chat_ids=chat_ids, vergine=vergine)


def _riavvio(monkeypatch):
    """Il riavvio del processo: la migrazione rigira sul database esistente."""
    monkeypatch.setattr(main, '_PERCORSI_MIGRATI', set())
    main.db().close()


def _consegna(testo=MESSAGGIO_VALIDO, chat=CHAT):
    payload = {'message': {'chat': {'id': int(chat)}, 'text': testo}}
    richiesta = RichiestaFinta(
        {'X-Telegram-Bot-Api-Secret-Token': main.webhook_secret(BOT_FINTO)}, payload)
    return asyncio.run(main.telegram_webhook(richiesta))


def _parsers(percorso):
    c = sqlite3.connect(percorso)
    nomi = sorted(r[0] for r in c.execute('SELECT name FROM parsers').fetchall())
    c.close()
    return nomi


def _profili(percorso):
    c = sqlite3.connect(percorso)
    nomi = sorted(r[0] for r in c.execute('SELECT name FROM profiles').fetchall())
    c.close()
    return nomi


def _link(percorso):
    c = sqlite3.connect(percorso)
    righe = sorted(c.execute(
        'SELECT p.name, ch.telegram_chat_id FROM parser_chats pc'
        ' JOIN parsers p ON p.id = pc.parser_id'
        ' JOIN chats ch ON ch.id = pc.chat_id').fetchall())
    c.close()
    return righe


def _salva_profilo(nome, chat_ids, parser):
    return main.save_profile(
        main.ProfileIn(name=nome, chat_ids=chat_ids, parser=parser), TOKEN_DI_PROVA)


# ------------------------------------------------------- le cancellazioni durano

def test_cancellare_il_parser_sopravvive_al_riavvio(tmp_path, monkeypatch):
    """Il seme non deve resuscitare un parser che il proprietario ha cancellato."""
    percorso = _relay(tmp_path, monkeypatch, 'dur1.db')
    # Prima il profilo che lo nomina, poi il parser: e' l'ordine con cui una
    # cancellazione vera non lascia il webhook su un parser fantasma.
    main.delete_profile(main.PIERO_PROFILE, TOKEN_DI_PROVA)
    main.delete_parser(main.DEFAULT_PARSER, TOKEN_DI_PROVA)
    assert main.DEFAULT_PARSER not in _parsers(percorso)
    _riavvio(monkeypatch)
    assert main.DEFAULT_PARSER not in _parsers(percorso), (
        'il riavvio ha RESUSCITATO il parser cancellato: il seme e\' ancora vivo')


def test_cancellare_il_profilo_sopravvive_al_riavvio(tmp_path, monkeypatch):
    percorso = _relay(tmp_path, monkeypatch, 'dur2.db')
    main.delete_profile(main.PIERO_PROFILE, TOKEN_DI_PROVA)
    assert main.PIERO_PROFILE not in _profili(percorso)
    _riavvio(monkeypatch)
    assert main.PIERO_PROFILE not in _profili(percorso), (
        'il riavvio ha RESUSCITATO il profilo cancellato: il seme e\' ancora vivo')


def test_rinominare_il_parser_non_lascia_doppioni(tmp_path, monkeypatch):
    """Lo scenario misurato nella #25: rinomina = crea nuovo + cancella vecchio.

    Col seme, al riavvio il vecchio tornava E restava quello nominato dal
    profilo: il parser rinominato non girava mai, in silenzio.
    """
    percorso = _relay(tmp_path, monkeypatch, 'rinomina.db')
    main.save_parser(main.ParserIn(name='Mio_Parser', header='P.Bet. PREMACHT 0,5HT'),
                     TOKEN_DI_PROVA)
    _salva_profilo(main.PIERO_PROFILE, '', 'Mio_Parser')
    main.delete_parser(main.DEFAULT_PARSER, TOKEN_DI_PROVA)
    _riavvio(monkeypatch)
    assert _parsers(percorso) == ['Mio_Parser'], (
        f'dopo la rinomina e il riavvio i parser sono {_parsers(percorso)}: '
        f'il doppione della #25 e\' ancora garantito')


def test_un_database_vergine_non_riceve_nessun_seme(tmp_path, monkeypatch):
    """Un deploy nuovo non deve inventarsi un parser e un profilo dal codice."""
    percorso = _relay(tmp_path, monkeypatch, 'vergine.db', vergine=True)
    assert _parsers(percorso) == [], f'il seme ha creato parser: {_parsers(percorso)}'
    assert _profili(percorso) == [], f'il seme ha creato profili: {_profili(percorso)}'


# ------------------------------------- il webhook regge un parser che non c'e'

def test_il_webhook_ignora_un_profilo_col_parser_mancante(tmp_path, monkeypatch):
    """Pericolo 2 della #25: profilo → parser cancellato. Senza guardia il
    webhook solleva 404 e Telegram ritenta in loop; col seme rimosso il loop
    sarebbe PERMANENTE. Atteso: consegna ignorata, nessuna eccezione."""
    percorso = _relay(tmp_path, monkeypatch, 'fantasma.db', chat_ids=CHAT)
    c = sqlite3.connect(percorso)
    c.execute('DELETE FROM parser_chats')
    c.execute('DELETE FROM parsers')
    c.commit()
    c.close()
    r = _consegna()
    assert r.get('ok') is True, f'la consegna non e\' ok: {r}'
    # Il motivo ESATTO, non un `ignored` qualunque: con `chat_not_allowed` il
    # test passerebbe senza che la guardia sul parser mancante sia stata
    # nemmeno raggiunta. Segnalato da CodeRabbit sulla PR #46.
    assert r.get('ignored') == 'parser_mancante', (
        f'atteso «parser_mancante», arrivato {r}: con un altro motivo la '
        f'guardia sul parser cancellato non e\' stata raggiunta')


# ----------------------------------------------- i link seguono il profilo

def test_il_link_nasce_quando_il_profilo_si_salva(tmp_path, monkeypatch):
    """Una chat AGGIUNTA via API deve funzionare subito, senza aspettare un
    riavvio: il salvataggio crea la riga in `chats` (come faceva `_travasa`)
    e il link del parser del profilo."""
    percorso = _relay(tmp_path, monkeypatch, 'link1.db', chat_ids='')
    _salva_profilo(main.PIERO_PROFILE, CHAT, main.DEFAULT_PARSER)
    assert (main.DEFAULT_PARSER, CHAT) in _link(percorso), (
        f'il salvataggio del profilo non ha creato il link: {_link(percorso)}')


def test_il_link_stantio_muore_quando_il_profilo_cambia_parser(tmp_path, monkeypatch):
    """Il limite dichiarato della PR #44: semina solo-aggiunta, link stantii.

    Qui si chiude: cambiare il parser del profilo toglie il link vecchio e mette
    il nuovo — il parser vecchio non deve continuare a girare su quella chat.
    """
    percorso = _relay(tmp_path, monkeypatch, 'link2.db', chat_ids=CHAT)
    _salva_profilo(main.PIERO_PROFILE, CHAT, main.DEFAULT_PARSER)
    main.save_parser(main.ParserIn(name='Parser_Nuovo', header='ALTRO-HEADER'),
                     TOKEN_DI_PROVA)
    _salva_profilo(main.PIERO_PROFILE, CHAT, 'Parser_Nuovo')
    link = _link(percorso)
    assert (main.DEFAULT_PARSER, CHAT) not in link, (
        f'il link del parser VECCHIO e\' ancora vivo: {link}')
    assert ('Parser_Nuovo', CHAT) in link, (
        f'il link del parser nuovo non e\' nato: {link}')


def test_il_profilo_eliminato_porta_via_i_suoi_link(tmp_path, monkeypatch):
    percorso = _relay(tmp_path, monkeypatch, 'link3.db', chat_ids=CHAT)
    _salva_profilo(main.PIERO_PROFILE, CHAT, main.DEFAULT_PARSER)
    assert (main.DEFAULT_PARSER, CHAT) in _link(percorso)
    main.delete_profile(main.PIERO_PROFILE, TOKEN_DI_PROVA)
    assert _link(percorso) == [], (
        f'i link del profilo eliminato sono ancora vivi: {_link(percorso)}')


def test_un_link_cancellato_a_mano_non_risuscita_al_riavvio(tmp_path, monkeypatch):
    percorso = _relay(tmp_path, monkeypatch, 'link4.db', chat_ids=CHAT)
    _salva_profilo(main.PIERO_PROFILE, CHAT, main.DEFAULT_PARSER)
    assert (main.DEFAULT_PARSER, CHAT) in _link(percorso)
    c = sqlite3.connect(percorso)
    c.execute('DELETE FROM parser_chats')
    c.commit()
    c.close()
    _riavvio(monkeypatch)
    assert _link(percorso) == [], (
        f'il riavvio ha risuscitato il link cancellato: {_link(percorso)}')


def test_la_riconciliazione_non_tocca_i_link_che_il_profilo_non_ha_creato(
        tmp_path, monkeypatch):
    """Guardia sul verso opposto: i link multi-parser della PR #44 (stesso
    utente, altro parser sulla stessa chat) sopravvivono al salvataggio del
    profilo — la riconciliazione e' per-profilo, non «tutti i link dell'utente»."""
    percorso = _relay(tmp_path, monkeypatch, 'link5.db', chat_ids=CHAT)
    _salva_profilo(main.PIERO_PROFILE, CHAT, main.DEFAULT_PARSER)
    main.save_parser(main.ParserIn(name='Parser_Extra', header='EXTRA'),
                     TOKEN_DI_PROVA)
    c = sqlite3.connect(percorso)
    c.execute('INSERT INTO parser_chats(parser_id, chat_id)'
              ' SELECT p.id, ch.id FROM parsers p, chats ch'
              " WHERE p.name='Parser_Extra' AND ch.telegram_chat_id=?", (CHAT,))
    c.commit()
    c.close()
    _salva_profilo(main.PIERO_PROFILE, CHAT, main.DEFAULT_PARSER)
    link = _link(percorso)
    assert ('Parser_Extra', CHAT) in link, (
        f'il salvataggio del profilo ha distrutto un link non suo: {link}')
    assert (main.DEFAULT_PARSER, CHAT) in link


def test_salvare_un_profilo_non_stacca_il_link_di_un_ALTRO_utente(tmp_path, monkeypatch):
    """L'attach filtra per proprietario, il detach deve filtrare uguale.

    Segnalato da Claude Fable 5 sulla PR #46 come asimmetria fra
    `_attacca_link_del_profilo` (che collega solo se il parser e' dell'utente del
    profilo) e `_stacca_link_del_profilo` (che cancellava per NOME). Il
    meccanismo che la review nominava — due parser omonimi di utenti diversi —
    non e' possibile, perche' `parsers.name` e' PRIMARY KEY **globale** (test
    `test_parsers_name_e_ancora_una_chiave_GLOBALE`). La conclusione pero' vale
    per un'altra via, ed e' questa: due profili possono NOMINARE lo stesso
    parser, e il non-proprietario, salvando il proprio profilo, staccava il link
    che il proprietario aveva legittimamente — il parser altrui smetteva di
    girare su quella chat, in silenzio.
    """
    percorso = _relay(tmp_path, monkeypatch, 'altrui.db', chat_ids=CHAT)
    # Il profilo PIERO possiede il parser e lo collega alla chat.
    _salva_profilo(main.PIERO_PROFILE, CHAT, main.DEFAULT_PARSER)
    assert (main.DEFAULT_PARSER, CHAT) in _link(percorso)

    # Un SECONDO profilo, di un altro utente, che nomina lo STESSO parser (che
    # non e' suo) sulla stessa chat: l'attach lo salta, e il fallback lo serve.
    c = sqlite3.connect(percorso)
    c.execute("INSERT INTO users(origin_profile, slug, first_name, status)"
              " VALUES ('ALTRO','altro','ALTRO','attivo')")
    c.commit()
    c.close()
    _salva_profilo('ALTRO', CHAT, main.DEFAULT_PARSER)
    assert (main.DEFAULT_PARSER, CHAT) in _link(percorso), (
        'il salvataggio del profilo di un ALTRO utente ha staccato il link del '
        'proprietario: il suo parser non gira piu\' su quella chat')

    # E nemmeno eliminando quel profilo: non erano suoi da togliere.
    main.delete_profile('ALTRO', TOKEN_DI_PROVA)
    assert (main.DEFAULT_PARSER, CHAT) in _link(percorso), (
        'eliminare il profilo di un ALTRO utente ha portato via il link del '
        'proprietario')


def test_due_salvataggi_SIMULTANEI_non_lasciano_vivo_il_parser_sostituito(
        tmp_path, monkeypatch):
    """La corsa read-modify-write su `save_profile`: la SELECT non apre la transazione.

    Bloccante di Claude Fable 5 sul gate finale della PR #46, ed e' la stessa
    classe della corsa sulla quota chiusa nella PR #45: in SQLite una `SELECT`
    non apre nessuna transazione di scrittura, quindi due POST concorrenti sullo
    stesso profilo leggono ENTRAMBI lo stato di partenza, staccano il link del
    parser vecchio (uno dei due lo trova gia' tolto) e attaccano ciascuno il
    proprio. Il profilo finisce con UN parser e i link con DUE: quello
    sostituito continua a girare su quella chat, che e' esattamente la
    regressione silenziosa che questo PR esiste per chiudere.

    Atteso: i link della chat sono ESATTAMENTE quelli del parser che il profilo
    nomina alla fine, qualunque delle due richieste abbia vinto.
    """
    import threading

    percorso = _relay(tmp_path, monkeypatch, 'corsa_profilo.db', chat_ids=CHAT)
    _salva_profilo(main.PIERO_PROFILE, CHAT, main.DEFAULT_PARSER)
    for nuovo in ('Parser_Uno', 'Parser_Due'):
        main.save_parser(main.ParserIn(name=nuovo, header=f'H-{nuovo}'), TOKEN_DI_PROVA)

    via = threading.Barrier(2)
    errori = []

    def concorrente(parser):
        via.wait()
        try:
            _salva_profilo(main.PIERO_PROFILE, CHAT, parser)
        except Exception as e:  # noqa: BLE001 - l'esito lo giudicano gli assert
            errori.append(e)

    fili = [threading.Thread(target=concorrente, args=(p,))
            for p in ('Parser_Uno', 'Parser_Due')]
    for f in fili:
        f.start()
    for f in fili:
        f.join()

    c = sqlite3.connect(percorso)
    vincente = c.execute('SELECT parser FROM profiles WHERE name=?',
                         (main.PIERO_PROFILE,)).fetchone()[0]
    c.close()
    collegati = sorted(nome for nome, chat in _link(percorso) if chat == CHAT)
    assert collegati == [vincente], (
        f'il profilo nomina {vincente!r} ma la chat e\' collegata a {collegati}: '
        f'un parser sostituito continua a girare. Errori: {errori}')


def test_il_travaso_PULISCE_i_link_stantii_lasciati_dalla_versione_precedente(
        tmp_path, monkeypatch):
    """L'upgrade non deve ereditare link che nessun profilo giustifica piu'.

    `[REAL_FINDING]` di GPT-5.6 Sol sul gate finale della PR #46. Prima di
    questo PR la semina era **solo-aggiunta** e girava a ogni avvio: un profilo
    eliminato, o un parser sostituito, lasciava il link vecchio vivo per sempre
    (era il limite dichiarato sulla PR #44). Da qui in avanti i detach lo
    impediscono — ma conoscono solo la configurazione corrente, e il travaso
    gira UNA VOLTA SOLA: se non pulisse, quei link resterebbero a elaborare
    chat e ad alimentare feed per sempre, senza piu' nessun giro che li tolga.

    Al momento del travaso ogni link a database viene dalla vecchia semina —
    e' l'unico codice che li scriveva — quindi riconciliare qui e' esatto:
    tiene cio' che i profili giustificano, toglie il resto.
    """
    percorso = _relay(tmp_path, monkeypatch, 'stantii.db', chat_ids=CHAT)
    # Lo stato che l'upgrade trova: un link della vecchia semina il cui parser
    # NON e' piu' quello del profilo (sostituito via API prima dell'upgrade).
    c = sqlite3.connect(percorso)
    c.execute('INSERT OR IGNORE INTO parsers(name, header) VALUES (?,?)',
              ('Parser_Sostituito', 'VECCHIO-HEADER'))
    c.execute('UPDATE parsers SET user_id=(SELECT id FROM users WHERE origin_profile=?)'
              ' WHERE name=?', (main.PIERO_PROFILE, 'Parser_Sostituito'))
    c.execute('UPDATE parsers SET id=rowid WHERE id IS NULL')
    c.execute('INSERT OR IGNORE INTO parser_chats(parser_id, chat_id)'
              ' SELECT p.id, ch.id FROM parsers p, chats ch'
              " WHERE p.name='Parser_Sostituito' AND ch.telegram_chat_id=?", (CHAT,))
    # E il marcatore del travaso NON c'e': e' il database che arriva dalla
    # versione precedente, dove quella tabella non esisteva.
    c.execute('DELETE FROM migrazioni')
    c.commit()
    c.close()
    assert ('Parser_Sostituito', CHAT) in _link(percorso), 'lo stato di partenza non c\'e\''

    _riavvio(monkeypatch)

    link = _link(percorso)
    assert ('Parser_Sostituito', CHAT) not in link, (
        f'il travaso ha ereditato un link che nessun profilo giustifica: {link}. '
        f'Quel parser continua a elaborare la chat, e nessun giro futuro lo toglie')
    assert (main.DEFAULT_PARSER, CHAT) in link, (
        f'il travaso ha portato via anche il link giusto: {link}')


def test_il_travaso_NON_tocca_i_link_legittimi_di_un_ALTRO_utente(tmp_path, monkeypatch):
    """La pulizia del travaso non deve mangiarsi i link giustificati altrui.

    Suggerito da GPT-5.5 sulla PR #46, ed e' la guardia sul verso opposto della
    riconciliazione: due utenti con il PROPRIO parser sulla stessa chat sono due
    link legittimi — e' il modello del dispatch multi-parser, dove nessuno
    «vince» la chat. Una pulizia che guardasse i profili in modo troppo largo li
    porterebbe via, e il sintomo sarebbe un cliente che smette di ricevere
    perche' un altro cliente esiste.
    """
    percorso = _relay(tmp_path, monkeypatch, 'travaso_altrui.db', chat_ids=CHAT)
    # Un secondo utente col SUO parser sulla stessa chat, come lo lascerebbe la
    # versione precedente: profilo, parser, e il link della vecchia semina.
    c = sqlite3.connect(percorso)
    c.execute("INSERT INTO users(origin_profile, slug, first_name, status)"
              " VALUES ('ALTRO','altro','ALTRO','attivo')")
    c.execute('INSERT INTO parsers(name, header) VALUES (?,?)',
              ('Parser_Di_Altro', 'HEADER-ALTRO'))
    c.execute('UPDATE parsers SET user_id=(SELECT id FROM users WHERE origin_profile=?),'
              ' id=rowid WHERE name=?', ('ALTRO', 'Parser_Di_Altro'))
    c.execute('INSERT INTO profiles(name, chat_ids, parser) VALUES (?,?,?)',
              ('ALTRO', CHAT, 'Parser_Di_Altro'))
    c.execute('INSERT OR IGNORE INTO parser_chats(parser_id, chat_id)'
              ' SELECT p.id, ch.id FROM parsers p, chats ch'
              " WHERE p.name='Parser_Di_Altro' AND ch.telegram_chat_id=?", (CHAT,))
    c.execute('DELETE FROM migrazioni')
    c.commit()
    c.close()

    _riavvio(monkeypatch)

    link = _link(percorso)
    assert ('Parser_Di_Altro', CHAT) in link, (
        f'il travaso ha tolto il link legittimo di un altro utente: {link}')
    assert (main.DEFAULT_PARSER, CHAT) in link, (
        f'il travaso ha tolto il link del proprietario: {link}')


def test_una_ELIMINAZIONE_concorrente_a_un_salvataggio_non_lascia_link_ORFANI(
        tmp_path, monkeypatch):
    """`delete_profile` legge e cancella: senza transazione e' la stessa corsa.

    `[REAL_FINDING]` di Claude Fable 5 sul gate finale della PR #46, ed e' la
    regola 2 mancata da me: avevo chiuso la corsa su `save_profile` e non avevo
    cercato il fratello. Se la lettura di `chat_ids`/`parser` sta fuori da una
    transazione di scrittura, un salvataggio concorrente puo' attaccare un link
    NUOVO dopo quella lettura: il profilo sparisce e il link sopravvive: col
    travaso ormai una-tantum, quel parser elabora la chat per sempre e nessun
    giro futuro lo toglie.

    La corsa e' forzata, non affidata alla fortuna (e' la tecnica gia' usata in
    `test_parser_crud.py`): una connessione avvolta esegue il salvataggio
    concorrente nel momento esatto in cui l'eliminazione legge il profilo. Col
    `BEGIN IMMEDIATE` quel salvataggio trova il database occupato e non passa —
    ed e' il punto: l'invariante che si misura non e' «chi vince», ma che alla
    fine **nessun link resti senza un profilo che lo giustifichi**.
    """
    import sqlite3 as _sq

    percorso = _relay(tmp_path, monkeypatch, 'orfani.db', chat_ids=CHAT)
    _salva_profilo(main.PIERO_PROFILE, CHAT, main.DEFAULT_PARSER)
    main.save_parser(main.ParserIn(name='Parser_Intruso', header='H-INTRUSO'),
                     TOKEN_DI_PROVA)

    reale = main.db

    class ConnCorsaSalvataggio:
        """Esegue UN salvataggio concorrente quando l'eliminazione legge il profilo."""

        def __init__(self, sotto):
            self._sotto = sotto
            self.fatta = False

        def execute(self, sql, params=()):
            # L'innesto sta DOPO la lettura del profilo e PRIMA della prima
            # scrittura, che e' l'unica finestra vera: iniettando prima della
            # lettura la SELECT vedrebbe gia' lo stato nuovo e non si
            # misurerebbe niente; iniettando dopo la prima `DELETE` la
            # transazione implicita e' gia' aperta e la corsa non esiste piu'.
            # Qui i dati letti dall'eliminazione diventano obsoleti.
            if not self.fatta and 'SELECT id FROM chats WHERE telegram_chat_id' in sql:
                self.fatta = True
                # Il salvataggio concorrente usa una connessione VERA — se usasse
                # questa avvolta si inietterebbe da solo, all'infinito — e con un
                # timeout corto, cosi' quando e' serializzato si arrende in fretta
                # invece di tenere fermo il test per cinque secondi.
                main.db = corta
                try:
                    _salva_profilo(main.PIERO_PROFILE, CHAT, 'Parser_Intruso')
                except (main.HTTPException, _sq.OperationalError):
                    # Serializzato: il salvataggio concorrente non e' passato.
                    pass
                finally:
                    main.db = db_con_corsa
            return self._sotto.execute(sql, params)

        def __getattr__(self, nome):
            return getattr(self._sotto, nome)

    def corta():
        c = reale()
        c.execute('PRAGMA busy_timeout = 200')
        return c

    def db_con_corsa():
        return ConnCorsaSalvataggio(reale())

    monkeypatch.setattr(main, 'db', db_con_corsa)
    try:
        main.delete_profile(main.PIERO_PROFILE, TOKEN_DI_PROVA)
    finally:
        monkeypatch.setattr(main, 'db', reale)

    c = _sq.connect(percorso)
    profili = {r[0]: (r[1], r[2]) for r in c.execute(
        'SELECT name, chat_ids, parser FROM profiles').fetchall()}
    c.close()
    orfani = [(parser, chat) for parser, chat in _link(percorso)
              if not any(parser == p and chat in _righe_chat(ch)
                         for ch, p in profili.values())]
    assert not orfani, (
        f'link senza un profilo che li giustifichi: {orfani}. Profili: {profili}. '
        f'Col travaso una-tantum quei parser elaborano la chat per sempre')


def _righe_chat(chat_ids):
    return {x.strip() for x in (chat_ids or '').split(',') if x.strip()}


def test_un_profilo_non_resta_MAI_a_nominare_un_parser_cancellato(tmp_path, monkeypatch):
    """La validazione del parser deve stare nella stessa transazione del salvataggio.

    `[REAL_FINDING]` di GPT-5.6 Sol al gate finale della PR #46: `get_parser`
    girava PRIMA di `BEGIN IMMEDIATE`, quindi un `DELETE /api/parsers`
    concorrente poteva cancellare il parser fra la validazione e la scrittura.
    Il profilo veniva salvato lo stesso, senza nessun link, e i segnali di
    quella chat sparivano in silenzio: nessun errore, nessun 4xx, il feed
    semplicemente fermo.

    L'invariante che si misura: **un profilo salvato nomina sempre un parser che
    esiste**. La corsa e' forzata sul punto esatto della validazione.
    """
    import sqlite3 as _sq

    percorso = _relay(tmp_path, monkeypatch, 'toctou_salva.db', chat_ids=CHAT)
    main.save_parser(main.ParserIn(name='Parser_Fragile', header='H-FRAGILE'),
                     TOKEN_DI_PROVA)
    reale = main.db

    class ConnCorsaCancellazione:
        """Cancella il parser nel momento in cui il salvataggio lo valida."""

        def __init__(self, sotto):
            self._sotto = sotto
            self.fatta = False

        def execute(self, sql, params=()):
            # L'innesto sta su `BEGIN IMMEDIATE`: a quel punto la validazione e'
            # gia' passata e la transazione non e' ancora aperta — l'unica
            # finestra vera. (Iniettando prima della validazione il `SELECT` di
            # `get_parser` vedrebbe gia' il parser sparito e risponderebbe 404,
            # che e' l'esito corretto: non si misurerebbe niente.)
            if not self.fatta and 'BEGIN IMMEDIATE' in sql:
                self.fatta = True
                altra = reale()
                altra.execute('PRAGMA busy_timeout = 200')
                try:
                    altra.execute('DELETE FROM parsers WHERE name=?', ('Parser_Fragile',))
                    altra.commit()
                except _sq.OperationalError:
                    pass  # Serializzato: la cancellazione non e' passata.
                finally:
                    altra.close()
            return self._sotto.execute(sql, params)

        def __getattr__(self, nome):
            return getattr(self._sotto, nome)

    monkeypatch.setattr(main, 'db', lambda: ConnCorsaCancellazione(reale()))
    try:
        _salva_profilo(main.PIERO_PROFILE, CHAT, 'Parser_Fragile')
    except main.HTTPException:
        pass  # Il 404 e' un esito legittimo: il parser non c'e' piu'.
    finally:
        monkeypatch.setattr(main, 'db', reale)

    c = _sq.connect(percorso)
    fantasmi = c.execute(
        'SELECT p.name, p.parser FROM profiles p'
        ' LEFT JOIN parsers x ON x.name = p.parser WHERE x.name IS NULL').fetchall()
    c.close()
    assert not fantasmi, (
        f'profili che nominano un parser inesistente: {fantasmi}. '
        f'Quella chat non produce piu\' segnali, senza nessun errore')


def test_il_webhook_non_solleva_se_il_parser_sparisce_A_META(tmp_path, monkeypatch):
    """Fra il controllo di esistenza e la lettura non deve esserci una finestra.

    `[REAL_FINDING]` di GPT-5.6 Sol: il controllo `SELECT 1` seguito da
    `get_parser` e' un TOCTOU — una cancellazione concorrente fra le due query
    fa sollevare 404 lo stesso, cioe' esattamente il retry-loop di Telegram che
    la guardia esiste per chiudere. Con una lettura sola la finestra non c'e'.

    Se qualcuno rimettesse la forma a due query, l'innesto qui sotto tornerebbe
    a scattare e questo test diventerebbe rosso.
    """
    import sqlite3 as _sq

    percorso = _relay(tmp_path, monkeypatch, 'toctou_webhook.db', chat_ids=CHAT)
    c = _sq.connect(percorso)
    c.execute('DELETE FROM parser_chats')  # solo il percorso legacy, per profilo
    c.commit()
    c.close()
    reale = main.db

    class ConnCorsaMeta:
        """Cancella il parser fra il controllo di esistenza e la lettura vera."""

        def __init__(self, sotto):
            self._sotto = sotto
            self.visto = False
            self.fatta = False

        def execute(self, sql, params=()):
            if 'SELECT 1 FROM parsers WHERE name=?' in sql:
                self.visto = True
            elif self.visto and not self.fatta and 'SELECT name,header' in sql:
                self.fatta = True
                altra = reale()
                altra.execute('PRAGMA busy_timeout = 200')
                try:
                    altra.execute('DELETE FROM parsers WHERE name=?',
                                  (main.DEFAULT_PARSER,))
                    altra.commit()
                except _sq.OperationalError:
                    pass
                finally:
                    altra.close()
            return self._sotto.execute(sql, params)

        def __getattr__(self, nome):
            return getattr(self._sotto, nome)

    monkeypatch.setattr(main, 'db', lambda: ConnCorsaMeta(reale()))
    try:
        esito = _consegna()
    finally:
        monkeypatch.setattr(main, 'db', reale)
    assert esito.get('ok') is True, (
        f'il webhook non ha risposto ok: {esito} — Telegram ritenterebbe in ciclo')
