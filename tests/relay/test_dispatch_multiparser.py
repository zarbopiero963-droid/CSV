"""Dispatch multi-parser nel webhook: chat → N parser, ognuno al SUO feed.

E' la PR 2 della sequenza sincronizzata nella Issue #2 (PR 9 del piano
originale), e chiude il pericolo 1 della Issue #25 — misurato la': con i profili
`ALTRO` e `PIERO` sulla stessa chat, il webhook prendeva il primo in ordine
alfabetico e il feed di produzione (`/xtrader.csv`) restava a sola intestazione
PER SEMPRE, con 200 su ogni consegna e `/health` verde.

Il modello nuovo, deciso in #2: la chat si collega ai PARSER (`parser_chats`),
ogni parser elabora in modo indipendente e scrive nel feed del SUO utente;
fra i parser dello stesso utente che riconoscono lo stesso messaggio vince
l'ULTIMO nell'ordine dichiarato (`parsers.ordine`), e i battuti finiscono in
`message_logs` come «sostituito da». `active=0` non gira. Un webhook duplicato
(stesso `update_id`) si elabora una volta: senza dedup, la riconsegna riarmava
il TTL e un segnale «da 90 secondi» viveva piu' a lungo a ogni retry.

Setup dei test: il relay in processo, la migrazione VERA a seminare
`users`/`chats`/`parser_chats` dai profili — cioe' lo stesso percorso che
seguira' il database di produzione al primo avvio dopo il deploy.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE))

import main  # noqa: E402 - dopo l'inserimento del percorso
from tests.ambiente import CHIAVI_PERICOLOSE, TOKEN_DI_PROVA  # noqa: E402
from tests.dati import relay_in_processo  # noqa: E402

# Riusati e non ricopiati (regola 3): il bot finto, la chat, il messaggio che il
# parser di default riconosce, e la Request minima per l'handler in processo.
from tests.relay.test_webhook import (  # noqa: E402
    BOT_FINTO, CHAT, MESSAGGIO_VALIDO, RichiestaFinta)

# Una config del motore che riconosce lo STESSO messaggio del parser legacy:
# serve ai test «due parser, stesso messaggio». `P.Bet.` sta nell'header.
CONFIG_STESSO_MESSAGGIO = {
    'match': {'type': 'contains', 'value': 'P.Bet.'},
    'columns': {
        'EventName': {'source': 'constant', 'value': 'Evento Del Secondo Parser'},
        'MarketType': {'source': 'constant', 'value': 'OVER_UNDER_25'},
        'SelectionName': {'source': 'constant', 'value': 'Over 2,5 goal'},
        'BetType': {'source': 'constant', 'value': 'PUNTA'},
    },
}


@pytest.fixture(autouse=True)
def _ambiente_pulito(monkeypatch):
    """Nessuna variabile della macchina entra in questi test."""
    for chiave in CHIAVI_PERICOLOSE:
        monkeypatch.delenv(chiave, raising=False)
    monkeypatch.setattr(main, 'TOKEN', TOKEN_DI_PROVA)


def _relay(tmp_path, monkeypatch, nome):
    """Relay in processo col segreto del webhook armato; restituisce il percorso DB.

    Il profilo PIERO seminato nasce con `chat_ids` vuoto perche' in test
    `TELEGRAM_ALLOWED_CHAT_IDS` non c'e' (la whitelist la toglie): la chat di
    prova va scritta a mano, come farebbe la variabile in produzione. I test che
    vogliono i LINK in `parser_chats` chiamano `_riavvio` dopo, cosi' la
    migrazione la vede e semina — lo stesso percorso del primo avvio post-deploy.
    """
    monkeypatch.setattr(main, 'SEGRETO_WEBHOOK', main.webhook_secret(BOT_FINTO))
    # La chat del profilo PIERO arriva dalla semina, non da un UPDATE dopo la
    # migrazione: dalla rimozione del seme (#25 lavoro E) il travaso dei link
    # gira UNA VOLTA SOLA, quindi una chat scritta dopo la prima migrazione non
    # verrebbe piu' vista. E' anche piu' fedele: in produzione quelle righe
    # esistono PRIMA che il processo parta.
    return relay_in_processo(monkeypatch, tmp_path / nome, chat_ids=CHAT)


def _riavvio(monkeypatch):
    """Il riavvio del processo: la migrazione rigira sul database esistente."""
    monkeypatch.setattr(main, '_PERCORSI_MIGRATI', set())
    main.db().close()


def _consegna(testo=MESSAGGIO_VALIDO, chat=CHAT, update_id=None):
    """Una consegna di Telegram all'handler in processo; restituisce il corpo."""
    payload = {'message': {'chat': {'id': int(chat)}, 'text': testo}}
    if update_id is not None:
        payload['update_id'] = update_id
    richiesta = RichiestaFinta(
        {'X-Telegram-Bot-Api-Secret-Token': main.webhook_secret(BOT_FINTO)}, payload)
    return asyncio.run(main.telegram_webhook(richiesta))


def _secondo_profilo(percorso, nome='ALTRO', chat=CHAT, config=None):
    """Un secondo profilo sulla STESSA chat, col suo parser; come da POST /api/profiles."""
    c = sqlite3.connect(percorso)
    parser = f'Parser_{nome}'
    c.execute('INSERT OR IGNORE INTO parsers(name, header) VALUES (?,?)',
              (parser, f'HEADER-{nome}'))
    if config is not None:
        c.execute('UPDATE parsers SET config_json=? WHERE name=?',
                  (json.dumps(config), parser))
    c.execute('INSERT OR IGNORE INTO profiles(name, chat_ids, parser) VALUES (?,?,?)',
              (nome, chat, parser))
    c.commit()
    c.close()
    return parser


def _segnali(percorso):
    """Le righe vive di `signals`, per etichetta utente."""
    c = sqlite3.connect(percorso)
    righe = c.execute(
        'SELECT COALESCE(u.origin_profile, u.slug, s.profile), s.csv, s.expires_at'
        ' FROM signals s LEFT JOIN users u ON u.id = s.user_id').fetchall()
    c.close()
    return righe


def test_un_utente_BLOCCATO_non_ferma_gli_altri_sulla_stessa_chat(tmp_path, monkeypatch):
    """L'accesso scaduto di uno non tocca il feed dell'altro, e non si logga.

    Copre il ramo `access_<stato>` del dispatch (segnalato scoperto da
    CodeRabbit): l'utente bloccato viene saltato PRIMA del parsing — niente
    segnale, e niente riga in `message_logs`, che e' una funzione del servizio e
    non un archivio dei messaggi di chi non ha accesso (PR #26).
    """
    percorso = _relay(tmp_path, monkeypatch, 'bloccato_fra_due.db')
    _secondo_profilo(percorso, 'ALTRO', config=CONFIG_STESSO_MESSAGGIO)
    _riavvio(monkeypatch)
    c = sqlite3.connect(percorso)
    altro = c.execute("SELECT id FROM users WHERE origin_profile='ALTRO'").fetchone()[0]
    c.execute("UPDATE users SET status='attivo', access_expires_at=? WHERE id=?",
              (int(time.time()) - 86400, altro))
    c.commit()
    c.close()

    esito = _consegna()
    per_utente = {r[0]: r[1] for r in _segnali(percorso)}
    assert 'PIERO' in per_utente, f'l\'utente sano non ha ricevuto: {esito!r}'
    assert 'ALTRO' not in per_utente, 'l\'utente scaduto ha ricevuto un segnale'
    c = sqlite3.connect(percorso)
    log_altro = c.execute('SELECT COUNT(*) FROM message_logs WHERE user_id=?',
                          (altro,)).fetchone()[0]
    c.close()
    assert log_altro == 0, 'il messaggio di un utente bloccato e\' finito nei log'


def test_due_utenti_serviti_dalla_stessa_consegna_risposta_AGGREGATA(tmp_path, monkeypatch):
    """Quando la chat serve piu' utenti la risposta aggrega, con ok=True.

    Il contratto a chiave singola ({'profile','event'}) vale per la chat di UN
    utente — lo vincolano i 35 test legacy — e qui si vincola l'altra meta'.
    """
    percorso = _relay(tmp_path, monkeypatch, 'aggregata.db')
    _secondo_profilo(percorso, 'ALTRO', config=CONFIG_STESSO_MESSAGGIO)
    _riavvio(monkeypatch)

    esito = _consegna()
    assert esito.get('ok') is True, esito
    assert set(esito.get('utenti', {})) == {'PIERO', 'ALTRO'}, (
        f'attesa la mappa aggregata dei due utenti serviti: {esito!r}')


# ------------------------------------------------------ il pericolo 1 si chiude

def test_un_profilo_che_ordina_prima_NON_dirotta_piu_la_produzione(tmp_path, monkeypatch):
    """Il pericolo 1 della #25, come test invece che come avvertenza.

    `ALTRO` ordina prima di `PIERO` sulla stessa chat. Col dispatch alfabetico il
    segnale finiva TUTTO sotto ALTRO e `/xtrader.csv` restava vuoto per sempre.
    Col dispatch per `parser_chats` ogni parser scrive nel feed del SUO utente:
    il feed di produzione continua a consegnare, e ALTRO riceve il proprio.
    """
    percorso = _relay(tmp_path, monkeypatch, 'pericolo1.db')
    _secondo_profilo(percorso, 'ALTRO', config=CONFIG_STESSO_MESSAGGIO)
    _riavvio(monkeypatch)  # la migrazione crea utente ALTRO, chat e i link

    esito = _consegna()
    corpo = bytes(main.xtrader_csv(token=TOKEN_DI_PROVA).body)
    assert b'SQUADRA-A - SQUADRA-B' in corpo, (
        f'il feed di PRODUZIONE e\' rimasto vuoto con un secondo profilo sulla chat: '
        f'pericolo 1 ancora vivo. Esito webhook: {esito!r}')
    per_utente = {r[0]: r[1] for r in _segnali(percorso)}
    assert 'ALTRO' in per_utente and 'Evento Del Secondo Parser' in per_utente['ALTRO'], (
        f'ALTRO non ha ricevuto il SUO segnale: {sorted(per_utente)}')
    assert 'PIERO' in per_utente, f'PIERO senza segnale: {sorted(per_utente)}'


# ------------------------------------------- due parser dello stesso utente

def _secondo_parser_di_piero(percorso, ordine=99, active=1,
                             config=CONFIG_STESSO_MESSAGGIO):
    """Un secondo parser DELLO STESSO utente PIERO, collegato alla stessa chat."""
    c = sqlite3.connect(percorso)
    piero = c.execute("SELECT id FROM users WHERE origin_profile='PIERO'").fetchone()[0]
    c.execute('INSERT INTO parsers(name, header, user_id, slug, ordine, active,'
              ' config_json) VALUES (?,?,?,?,?,?,?)',
              ('Secondo_Di_Piero', 'inutilizzato', piero, 'secondo', ordine, active,
               json.dumps(config)))
    # `parsers.id` lo riempie la migrazione dal rowid: un INSERT diretto lo lascia
    # NULL, come farebbe una riga scritta da una versione vecchia.
    c.execute("UPDATE parsers SET id=rowid WHERE name='Secondo_Di_Piero'")
    pid = c.execute("SELECT id FROM parsers WHERE name='Secondo_Di_Piero'").fetchone()[0]
    cid = c.execute('SELECT id FROM chats WHERE telegram_chat_id=?', (CHAT,)).fetchone()[0]
    c.execute('INSERT INTO parser_chats(parser_id, chat_id) VALUES (?,?)', (pid, cid))
    c.commit()
    c.close()
    return pid


def test_fra_due_parser_dello_stesso_utente_vince_l_ULTIMO(tmp_path, monkeypatch):
    """`ordine` decide, vince l'ultimo che riconosce, il battuto finisce nei log."""
    percorso = _relay(tmp_path, monkeypatch, 'vince_ultimo.db')
    _riavvio(monkeypatch)
    _secondo_parser_di_piero(percorso, ordine=99)

    _consegna()
    per_utente = {r[0]: r[1] for r in _segnali(percorso)}
    assert list(per_utente) == ['PIERO'], f'attesa UNA riga di PIERO: {per_utente}'
    assert 'Evento Del Secondo Parser' in per_utente['PIERO'], (
        'non ha vinto l\'ULTIMO parser nell\'ordine dichiarato')
    c = sqlite3.connect(percorso)
    log = c.execute('SELECT esito FROM message_logs').fetchall()
    c.close()
    esiti = [r[0] for r in log]
    assert any('sostituito' in e for e in esiti), (
        f'il parser battuto non risulta nei log come sostituito: {esiti}')


def test_un_parser_DISATTIVO_non_gira(tmp_path, monkeypatch):
    """`active=0` esclude il parser dal dispatch, qualunque sia il suo ordine."""
    percorso = _relay(tmp_path, monkeypatch, 'disattivo.db')
    _riavvio(monkeypatch)
    _secondo_parser_di_piero(percorso, ordine=99, active=0)

    _consegna()
    per_utente = {r[0]: r[1] for r in _segnali(percorso)}
    assert 'SQUADRA-A - SQUADRA-B' in per_utente.get('PIERO', ''), (
        f'doveva vincere il parser legacy, l\'unico attivo: {per_utente}')


def test_chi_non_riconosce_NON_tocca_il_feed_nemmeno_per_svuotarlo(tmp_path, monkeypatch):
    percorso = _relay(tmp_path, monkeypatch, 'non_tocca.db')
    _riavvio(monkeypatch)
    _consegna()
    prima = _segnali(percorso)
    assert prima, 'setup: nessun segnale scritto'
    esito = _consegna(testo='niente da riconoscere qui')
    assert _segnali(percorso) == prima, (
        f'un messaggio non riconosciuto ha toccato il feed: {esito!r}')


def test_il_TTL_riparte_dalla_scrittura_VINCENTE(tmp_path, monkeypatch):
    percorso = _relay(tmp_path, monkeypatch, 'ttl_vincente.db')
    _riavvio(monkeypatch)
    _consegna()
    scadenza = _segnali(percorso)[0][2]
    assert scadenza == pytest.approx(int(time.time()) + 90, abs=5)


def test_disattivare_TUTTI_i_parser_spegne_davvero_il_feed(tmp_path, monkeypatch):
    """`active=0` su ogni parser dell'utente = silenzio, anche dal fallback.

    [REAL_FINDING] di GPT-5.6 Sol al gate finale della PR #44, verificato vero:
    il filtro `active=1` stava nella query dei link, quindi un utente con tutti
    i parser disattivati spariva dai link, risultava «non rappresentato», e il
    fallback legacy eseguiva comunque il suo parser — disattivato — scrivendo
    segnali. Disattivare deve significare disattivare: la rappresentanza si
    misura sui link SENZA il filtro active (il sistema dei link possiede il
    dispatch di quell'utente, e active=0 lo silenzia), e il fallback stesso
    rispetta `active` del parser del profilo.
    """
    percorso = _relay(tmp_path, monkeypatch, 'tutti_spenti.db')
    _riavvio(monkeypatch)
    c = sqlite3.connect(percorso)
    c.execute('UPDATE parsers SET active=0 WHERE name=?', (main.DEFAULT_PARSER,))
    c.commit()
    c.close()

    esito = _consegna()
    assert not _segnali(percorso), (
        f'un parser DISATTIVATO ha scritto un segnale via fallback: {esito!r}')


def test_un_messaggio_LENTO_non_sovrascrive_quello_arrivato_DOPO(tmp_path, monkeypatch):
    """L'ordine di arrivo e' l'ordine di scrittura, anche con un parse lento.

    [REAL_FINDING] di GPT-5.6 Sol al gate finale della PR #44, verificato vero
    ed era una regressione dell'offload su to_thread (#31 B1): prima il codice
    sincrono sull'event loop serializzava le consegne di fatto; col threadpool
    un messaggio VECCHIO dal parse lento poteva finire dopo uno NUOVO e
    sovrascriverne segnale e TTL — un feed che torna indietro nel tempo.
    Il lock di elaborazione ripristina l'ordine senza rimettere il carico
    sull'event loop: le ALTRE rotte restano libere, le consegne si accodano.

    La corsa si esercita sul percorso FALLBACK (profilo a caldo, chat senza
    link), dove nessuna scrittura precede il parse: sul percorso dei link la
    DELETE di pulizia apre la transazione PRIMA del parse e SQLite accoda gia'
    il secondo scrittore — una serializzazione vera ma ACCIDENTALE, appesa alla
    posizione di una pulizia. Il lock la rende deliberata su entrambi i percorsi.
    """
    import threading

    percorso = _relay(tmp_path, monkeypatch, 'ordine_arrivo.db')
    _riavvio(monkeypatch)
    _secondo_profilo(percorso, 'CALDO-ORDINE', chat='-1002000000077', config={
        'match': {'type': 'contains', 'value': 'SEGNALE'},
        'columns': {
            'EventName': {'source': 'line', 'anchor': 'evento', 'part': 'after',
                          'marker': ':', 'transforms': [{'op': 'trim'}]},
            'MarketType': {'source': 'constant', 'value': 'OVER_UNDER_15'},
            'SelectionName': {'source': 'constant', 'value': 'Over 1,5 goal'},
            'BetType': {'source': 'constant', 'value': 'PUNTA'},
        },
    })  # NIENTE riavvio: chat senza link, percorso fallback

    vero = main.elabora_messaggio

    def con_lentezza(text, cfg):
        risultato = vero(text, cfg)
        if 'LENTO' in text:
            time.sleep(0.6)
        return risultato

    monkeypatch.setattr(main, 'elabora_messaggio', con_lentezza)
    vecchio = 'SEGNALE\nEvento: Lento v Vecchio LENTO'
    nuovo = 'SEGNALE\nEvento: Fresco v Nuovo'

    t1 = threading.Thread(target=_consegna, args=(vecchio,),
                          kwargs={'chat': '-1002000000077', 'update_id': 1})
    t1.start()
    time.sleep(0.2)  # il messaggio NUOVO arriva mentre il vecchio sta ancora macinando
    t2 = threading.Thread(target=_consegna, args=(nuovo,),
                          kwargs={'chat': '-1002000000077', 'update_id': 2})
    t2.start()
    t1.join()
    t2.join()

    righe = _segnali(percorso)
    assert len(righe) == 1, righe
    assert 'Fresco v Nuovo' in righe[0][1] and 'LENTO' not in righe[0][1], (
        f'il messaggio vecchio e lento ha sovrascritto quello nuovo: {righe[0][1][:200]!r}')


def test_la_pulizia_dei_log_gira_anche_SENZA_link_attivi(tmp_path, monkeypatch):
    """La promessa dei 7 giorni vale anche quando nessun link e' attivo.

    [REAL_FINDING] di GPT-5.6 Sol al gate finale della PR #44, vero in un angolo
    reale: la DELETE dei log vecchi stava solo nel ramo con link, quindi un
    servizio coi parser tutti disattivati (o solo su fallback) conservava i
    testi gia' registrati OLTRE i 7 giorni dichiarati — una violazione di
    retention, non un'ipotesi. La pulizia ora viaggia col commit che ogni
    consegna fa comunque per il marker del dedup.
    """
    percorso = _relay(tmp_path, monkeypatch, 'pulizia_sempre.db')
    _riavvio(monkeypatch)
    c = sqlite3.connect(percorso)
    c.execute("INSERT INTO message_logs(user_id, parser_id, chat_id, text, esito,"
              " created_at) VALUES (1, 1, 1, 'testo vecchio', 'segnale',"
              " datetime('now', '-8 days'))")
    c.execute('UPDATE parsers SET active=0 WHERE name=?', (main.DEFAULT_PARSER,))
    c.commit()
    c.close()

    _consegna(update_id=55)  # consegna su servizio senza parser attivi
    c = sqlite3.connect(percorso)
    vecchi = c.execute("SELECT COUNT(*) FROM message_logs"
                       " WHERE created_at < datetime('now', '-7 days')").fetchone()[0]
    c.close()
    assert vecchi == 0, (
        f'{vecchi} log oltre i 7 giorni sopravvivono quando nessun link e\' attivo: '
        'la retention dichiarata non vale')


def test_se_il_vincente_fallisce_NIENTE_log_sostituito(tmp_path, monkeypatch):
    """I log dei battuti si scrivono solo se il vincente scrive davvero.

    [REAL_FINDING] di Fable al gate finale della PR #44: le righe «riconosciuto,
    sostituito da X» venivano inserite PRIMA di `store_signal` — se il CSV del
    vincente falliva (`csv_non_valido`), restavano log che raccontano una
    sostituzione mai avvenuta. Non una perdita di dati: un log bugiardo, che per
    la vista «perche' non ha fatto» e' comunque un difetto.
    """
    percorso = _relay(tmp_path, monkeypatch, 'log_veri.db')
    _riavvio(monkeypatch)
    _secondo_parser_di_piero(percorso, ordine=99)

    def sempre_rotto(*a, **k):
        raise ValueError('csv non valido (simulato)')

    monkeypatch.setattr(main, 'store_signal', sempre_rotto)
    esito = _consegna()
    assert esito.get('ignored') == 'csv_non_valido', esito
    c = sqlite3.connect(percorso)
    log = [r[0] for r in c.execute('SELECT esito FROM message_logs').fetchall()]
    c.close()
    assert not any('sostituito' in e for e in log), (
        f'log di una sostituzione mai avvenuta: {log}')


# ----------------------------------------------------------- webhook duplicato

def test_un_webhook_DUPLICATO_si_elabora_una_volta_sola(tmp_path, monkeypatch):
    """Stesso `update_id` due volte: la seconda non riarma il TTL.

    Telegram riconsegna le consegne che crede fallite. Senza dedup ogni
    riconsegna rifaceva DELETE+INSERT e il TTL ripartiva: un segnale «da 90
    secondi» viveva piu' a lungo a ogni retry — un segnale stantio e' una
    puntata che nessuno ha scelto.
    """
    percorso = _relay(tmp_path, monkeypatch, 'duplicato.db')
    _riavvio(monkeypatch)
    _consegna(update_id=777)
    prima = _segnali(percorso)

    time.sleep(1.1)  # se il TTL riparte, expires_at cambia di >= 1
    esito = _consegna(update_id=777)
    assert esito.get('ignored') == 'duplicate', (
        f'la riconsegna con lo stesso update_id e\' stata rielaborata: {esito!r}')
    assert _segnali(percorso) == prima, (
        'la riconsegna ha riscritto il segnale: il TTL e\' ripartito')


def test_un_fallimento_a_meta_NON_brucia_il_retry(tmp_path, monkeypatch):
    """Se l'elaborazione fallisce, la riconsegna di Telegram DEVE rielaborare.

    Bloccante di Claude Fable 5 e rischio di GPT-5.5 sulla PR #44, convergenti:
    la prima versione committava il marker di `webhook_seen` PRIMA di elaborare,
    quindi un crash fra il marker e `store_signal` perdeva il segnale per sempre
    — il retry usciva come `duplicate` con niente nel feed. Il marker va scritto
    nella STESSA transazione del segnale: o entrambi, o nessuno.
    """
    percorso = _relay(tmp_path, monkeypatch, 'retry_vivo.db')
    _riavvio(monkeypatch)

    vero = main.store_signal
    guasti = []

    def guasto_una_volta(*a, **k):
        if not guasti:
            guasti.append(True)
            raise sqlite3.OperationalError('database is locked (simulato)')
        return vero(*a, **k)

    monkeypatch.setattr(main, 'store_signal', guasto_una_volta)
    with pytest.raises(Exception):
        _consegna(update_id=909)  # il guasto DEVE propagarsi: 500 → Telegram ritenta
    assert not _segnali(percorso), 'il primo tentativo guasto ha scritto comunque'

    esito = _consegna(update_id=909)  # la riconsegna, stesso update_id
    assert esito.get('ignored') != 'duplicate', (
        'la riconsegna dopo un guasto esce come duplicate: segnale perso per sempre')
    assert _segnali(percorso), 'la riconsegna non ha scritto il segnale'


def test_update_id_DIVERSO_si_elabora_normalmente(tmp_path, monkeypatch):
    """Il verso opposto: il dedup ferma i duplicati, non le consegne nuove."""
    percorso = _relay(tmp_path, monkeypatch, 'non_duplicato.db')
    _riavvio(monkeypatch)
    e1 = _consegna(update_id=1)
    e2 = _consegna(update_id=2)
    assert 'ignored' not in e1 and 'ignored' not in e2, (e1, e2)


def test_un_link_con_parser_SENZA_utente_non_scrive_chiavi_nulle(tmp_path, monkeypatch):
    """Un parser orfano (user_id NULL) collegato a mano non inquina il feed.

    Rischio segnalato da GPT-5.5 sulla PR #44: la semina non puo' creare un link
    del genere (esige il JOIN con `users`), ma una riga scritta a mano nel
    database si'. Senza guardia, quel parser scriverebbe una riga di `signals`
    con user_id E profile NULL — e `DELETE WHERE profile=NULL` non cancella mai
    niente in SQL, quindi le righe si ACCUMULEREBBERO a ogni messaggio: un feed
    di nessuno che cresce per sempre. La guardia lo esclude dal dispatch; la
    chat resta servita dai parser con un proprietario.
    """
    percorso = _relay(tmp_path, monkeypatch, 'orfano.db')
    _riavvio(monkeypatch)
    c = sqlite3.connect(percorso)
    c.execute('INSERT INTO parsers(name, header, config_json) VALUES (?,?,?)',
              ('Orfano', 'inutilizzato', json.dumps(CONFIG_STESSO_MESSAGGIO)))
    c.execute("UPDATE parsers SET id=rowid WHERE name='Orfano'")
    pid = c.execute("SELECT id FROM parsers WHERE name='Orfano'").fetchone()[0]
    cid = c.execute('SELECT id FROM chats WHERE telegram_chat_id=?', (CHAT,)).fetchone()[0]
    c.execute('INSERT INTO parser_chats(parser_id, chat_id) VALUES (?,?)', (pid, cid))
    c.commit()
    c.close()

    _consegna()
    _consegna(update_id=2)  # una seconda consegna: le righe nulle si accumulerebbero
    c = sqlite3.connect(percorso)
    nulle = c.execute('SELECT COUNT(*) FROM signals WHERE user_id IS NULL'
                      ' AND profile IS NULL').fetchone()[0]
    di_piero = c.execute('SELECT COUNT(*) FROM signals WHERE user_id IS NOT NULL').fetchone()[0]
    c.close()
    assert nulle == 0, f'{nulle} righe di segnale senza proprietario: crescono per sempre'
    assert di_piero == 1, 'il parser CON proprietario doveva continuare a scrivere'


# ---------------------------------------------------- il riavvio non rimescola

def test_il_riavvio_non_DUPLICA_i_link_ne_cambia_l_ordine(tmp_path, monkeypatch):
    """La semina di `parser_chats` e' idempotente e l'ordine dei parser e' stabile."""
    percorso = _relay(tmp_path, monkeypatch, 'riavvio.db')
    _riavvio(monkeypatch)
    c = sqlite3.connect(percorso)
    link_prima = sorted(c.execute('SELECT parser_id, chat_id FROM parser_chats').fetchall())
    ordini_prima = sorted(c.execute('SELECT name, ordine FROM parsers').fetchall())
    c.close()
    assert link_prima, 'la migrazione non ha seminato parser_chats dai profili'

    _riavvio(monkeypatch)
    c = sqlite3.connect(percorso)
    link_dopo = sorted(c.execute('SELECT parser_id, chat_id FROM parser_chats').fetchall())
    ordini_dopo = sorted(c.execute('SELECT name, ordine FROM parsers').fetchall())
    c.close()
    assert link_dopo == link_prima, 'il riavvio ha duplicato o perso link chat-parser'
    assert ordini_dopo == ordini_prima, 'il riavvio ha rimescolato l\'ordine dei parser'


# ------------------------------------------------- il fallback legacy resiste

def test_un_profilo_creato_a_CALDO_dispatcha_senza_riavvio(tmp_path, monkeypatch):
    """Un profilo aggiunto via API dopo l'avvio non ha ancora link in
    `parser_chats` (li semina la migrazione, al riavvio): finche' non ce li ha,
    il percorso legacy per profili deve continuare a servirlo."""
    percorso = _relay(tmp_path, monkeypatch, 'a_caldo.db')
    _riavvio(monkeypatch)
    _secondo_profilo(percorso, 'CALDO', chat='-1002000000099',
                     config=CONFIG_STESSO_MESSAGGIO)  # NIENTE riavvio dopo

    esito = _consegna(chat='-1002000000099')
    assert 'ignored' not in esito, (
        f'la chat di un profilo creato a caldo e\' stata ignorata: {esito!r}')


def test_il_profilo_a_caldo_che_ordina_DOPO_quello_linkato_non_resta_muto(tmp_path, monkeypatch):
    """Il fallback deve integrare TUTTI i profili scoperti, non il primo alfabetico.

    Bloccante di GPT-5.5 sul fix precedente, ed era un bug vero DEL fix: il
    fallback guardava solo il primo profilo in ordine di nome che elenca la
    chat. Se quello e' gia' rappresentato dai link (PIERO), un profilo a caldo
    che ordina DOPO (`ZZZ-CALDO`) non veniva mai considerato e restava muto
    fino al riavvio — l'immagine speculare del caso gia' testato, dove il
    profilo a caldo ordinava prima.
    """
    percorso = _relay(tmp_path, monkeypatch, 'caldo_dopo.db')
    _riavvio(monkeypatch)  # PIERO ha i suoi link su CHAT
    _secondo_profilo(percorso, 'ZZZ-CALDO', chat=CHAT,
                     config=CONFIG_STESSO_MESSAGGIO)  # NIENTE riavvio

    _consegna()
    per_utente = {r[0]: r[1] for r in _segnali(percorso)}
    assert 'PIERO' in per_utente, f'PIERO (via link) senza segnale: {sorted(per_utente)}'
    assert 'ZZZ-CALDO' in per_utente, (
        f'il profilo a caldo che ordina DOPO quello linkato e\' rimasto muto: '
        f'{sorted(per_utente)}')


def test_due_consegne_SIMULTANEE_con_lo_stesso_update_id_elaborano_una_volta(tmp_path, monkeypatch):
    """Il dedup regge anche la corsa: due POST identici INSIEME, una elaborazione.

    Bloccante di GPT-5.5: il controllo in testa piu' il marker in coda lasciava
    passare due consegne identiche ARRIVATE INSIEME (il marker dell'una non e'
    ancora committato quando l'altra controlla). Il servizio e' un processo solo
    (Procfile senza --workers, misurato): una prenotazione in-flight per
    processo chiude la finestra senza toccare la garanzia sul crash — il marker
    resta nella transazione del segnale.
    """
    import threading

    percorso = _relay(tmp_path, monkeypatch, 'corsa_dedup.db')
    _riavvio(monkeypatch)

    esiti, via = [], threading.Barrier(2)

    def consegna():
        via.wait()
        esiti.append(_consegna(update_id=4242))

    fili = [threading.Thread(target=consegna) for _ in range(2)]
    for f in fili:
        f.start()
    for f in fili:
        f.join()

    duplicati = [e for e in esiti if e.get('ignored') == 'duplicate']
    elaborati = [e for e in esiti if 'ignored' not in e]
    assert len(elaborati) == 1 and len(duplicati) == 1, (
        f'attese una elaborazione e un duplicato: {esiti!r}')
    assert len(_segnali(percorso)) == 1


def test_un_profilo_a_caldo_su_una_chat_GIA_collegata_non_resta_muto(tmp_path, monkeypatch):
    """Il caso che il fallback semplice non copriva: bloccante di Fable sulla PR #44.

    La chat ha GIA' i link di PIERO; un profilo nuovo creato a caldo sulla STESSA
    chat non ha ancora i suoi (arrivano alla prossima migrazione). Col fallback
    «solo se la chat non ha nessun link», il profilo nuovo restava muto fino al
    riavvio — e PIERO, servito dai link, mascherava il silenzio. Il percorso
    legacy deve integrare il profilo il cui utente NON e' rappresentato nei link.
    """
    percorso = _relay(tmp_path, monkeypatch, 'caldo_su_collegata.db')
    _riavvio(monkeypatch)  # PIERO ha i suoi link su CHAT
    _secondo_profilo(percorso, 'AGGIUNTO', chat=CHAT,
                     config=CONFIG_STESSO_MESSAGGIO)  # NIENTE riavvio

    _consegna()
    per_utente = {r[0]: r[1] for r in _segnali(percorso)}
    assert 'PIERO' in per_utente, f'PIERO (via link) senza segnale: {sorted(per_utente)}'
    assert 'AGGIUNTO' in per_utente, (
        f'il profilo a caldo su una chat gia\' collegata e\' rimasto muto: '
        f'{sorted(per_utente)}')
