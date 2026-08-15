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
from tests.dati import semina_produzione  # noqa: E402
from tests.relay.test_webhook import (  # noqa: E402
    BOT_FINTO, CHAT, MESSAGGIO_VALIDO, RichiestaFinta)


@pytest.fixture(autouse=True)
def _ambiente_pulito(monkeypatch):
    """Nessuna variabile della macchina entra in questi test."""
    for chiave in CHIAVI_PERICOLOSE:
        monkeypatch.delenv(chiave, raising=False)
    monkeypatch.setattr(main, 'TOKEN', TOKEN_DI_PROVA)


def _relay(tmp_path, monkeypatch, nome, chat_ids='', vergine=False):
    """Il relay in processo su un database di produzione simulato (o vergine)."""
    percorso = str(tmp_path / nome)
    monkeypatch.setattr(main, 'DB_PATH', percorso)
    monkeypatch.setattr(main, '_PERCORSI_MIGRATI', set())
    monkeypatch.setattr(main, 'SEGRETO_WEBHOOK', main.webhook_secret(BOT_FINTO))
    if not vergine:
        semina_produzione(percorso, chat_ids)
    main.db().close()
    return percorso


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
    assert 'ignored' in r, f'atteso un esito ignorato, arrivato {r}'


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
