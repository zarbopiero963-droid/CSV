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
    percorso = str(tmp_path / nome)
    monkeypatch.setattr(main, 'DB_PATH', percorso)
    monkeypatch.setattr(main, '_PERCORSI_MIGRATI', set())
    monkeypatch.setattr(main, 'SEGRETO_WEBHOOK', main.webhook_secret(BOT_FINTO))
    main.db().close()
    c = sqlite3.connect(percorso)
    c.execute("UPDATE profiles SET chat_ids=? WHERE name='PIERO'", (CHAT,))
    c.commit()
    c.close()
    return percorso


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
        'SELECT COALESCE(u.origin_profile, u.slug), s.csv, s.expires_at'
        ' FROM signals s LEFT JOIN users u ON u.id = s.user_id').fetchall()
    c.close()
    return righe


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


def test_update_id_DIVERSO_si_elabora_normalmente(tmp_path, monkeypatch):
    """Il verso opposto: il dedup ferma i duplicati, non le consegne nuove."""
    percorso = _relay(tmp_path, monkeypatch, 'non_duplicato.db')
    _riavvio(monkeypatch)
    e1 = _consegna(update_id=1)
    e2 = _consegna(update_id=2)
    assert 'ignored' not in e1 and 'ignored' not in e2, (e1, e2)


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
