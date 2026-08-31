"""Configurazione del canale privato di backup (#56 pezzo 2).

Il backup del database (#56 pezzo 1) va a finire da qualche parte: il proprietario
decide un **canale Telegram privato** e ci aggiunge il bot come amministratore. Qui si
verifica la parte backend di quella configurazione:

- la **cattura**: il webhook riconosce il canale — dal bot promosso amministratore
  (`my_chat_member`) o da un messaggio inoltrato (`forward_from_chat`) — ma **solo** se
  l'azione viene dall'amministratore, e scrive solo un CANDIDATO. Non tocca `chats`: il
  canale di backup e' una destinazione, non una sorgente di segnali, e finire in `chats`
  lo iscriverebbe all'instradamento del webhook;
- la **conferma**: promuove il candidato a canale configurato **solo dopo** un invio di
  prova riuscito; se la prova fallisce non salva niente e l'errore torna visibile;
- l'**isolamento**: tutte le rotte sono 404 per chi non e' l'amministratore.
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE))

import main  # noqa: E402
from tests.relay.test_accesso import _admin  # noqa: E402
from tests.relay.test_login import ADMIN_FINTO, BOT_FINTO  # noqa: E402

CANALE = -1001234567890          # i canali hanno id negativi -100…
ALTRO_CANALE = -1009876543210


def _abilita_webhook(monkeypatch):
    """Il webhook accetta consegne (segreto derivabile) e conosce l'amministratore."""
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', ADMIN_FINTO)
    monkeypatch.setattr(main, 'SEGRETO_WEBHOOK', main.webhook_secret(BOT_FINTO))


def _webhook(payload):
    """Una consegna del webhook in processo, col segreto giusto."""
    class Richiesta:
        headers = {'X-Telegram-Bot-Api-Secret-Token': main.webhook_secret(BOT_FINTO)}

        async def json(self):
            return payload

    return asyncio.run(main.telegram_webhook(Richiesta()))


def _impostazione(percorso, chiave):
    c = sqlite3.connect(percorso)
    try:
        riga = c.execute('SELECT valore FROM impostazioni WHERE chiave=?', (chiave,)).fetchone()
    finally:
        c.close()
    return riga[0] if riga else None


def _corpo(risposta):
    import json
    return json.loads(bytes(risposta.body).decode())


# ---------------------------------------------------------------- la cattura

def test_il_bot_promosso_amministratore_dal_proprietario_diventa_candidato(tmp_path, monkeypatch):
    """`my_chat_member` con status administrator, dall'amministratore → candidato scritto.

    E NON crea una riga in `chats`: il canale di backup e' una destinazione, non una
    sorgente di segnali."""
    percorso, _admin_s, _cliente_s, _cliente = _admin(tmp_path, monkeypatch, 'cattura.db')
    _abilita_webhook(monkeypatch)

    esito = _webhook({'my_chat_member': {
        'from': {'id': int(ADMIN_FINTO)},
        'chat': {'id': CANALE, 'type': 'channel', 'title': 'Backup BetRelay'},
        'new_chat_member': {'status': 'administrator'}}})

    assert esito.get('canale_backup_candidato') is True, esito
    assert _impostazione(percorso, main.CHIAVE_CANALE_CANDIDATO_ID) == str(CANALE)
    assert _impostazione(percorso, main.CHIAVE_CANALE_CANDIDATO_TITOLO) == 'Backup BetRelay'

    c = sqlite3.connect(percorso)
    in_chats = c.execute('SELECT COUNT(*) FROM chats WHERE telegram_chat_id=?',
                         (str(CANALE),)).fetchone()[0]
    c.close()
    assert in_chats == 0, 'il canale di backup e- finito in chats: verrebbe instradato come segnale'


def test_un_messaggio_del_canale_inoltrato_dal_proprietario_diventa_candidato(tmp_path, monkeypatch):
    """`forward_from_chat` in un messaggio inoltrato dall'amministratore → candidato."""
    percorso, _a, _c, _u = _admin(tmp_path, monkeypatch, 'inoltro.db')
    _abilita_webhook(monkeypatch)

    esito = _webhook({'message': {
        'from': {'id': int(ADMIN_FINTO)},
        'chat': {'id': int(ADMIN_FINTO), 'type': 'private'},
        'text': 'guarda questo',
        'forward_from_chat': {'id': ALTRO_CANALE, 'type': 'channel', 'title': 'Il canale'}}})

    assert esito.get('canale_backup_candidato') is True, esito
    assert _impostazione(percorso, main.CHIAVE_CANALE_CANDIDATO_ID) == str(ALTRO_CANALE)


def test_un_ESTRANEO_non_puo_proporre_un_canale(tmp_path, monkeypatch):
    """Stessi update, ma `from.id` non e' l'amministratore → nessun candidato.

    Senza questa guardia il canale di CHIUNQUE aggiunga il bot comparirebbe come
    proposta nel pannello del proprietario."""
    percorso, _a, _c, _u = _admin(tmp_path, monkeypatch, 'estraneo.db')
    _abilita_webhook(monkeypatch)

    estraneo = '111222333'
    assert estraneo != ADMIN_FINTO
    _webhook({'my_chat_member': {
        'from': {'id': int(estraneo)},
        'chat': {'id': CANALE, 'type': 'channel', 'title': 'Canale altrui'},
        'new_chat_member': {'status': 'administrator'}}})
    _webhook({'message': {
        'from': {'id': int(estraneo)},
        'chat': {'id': int(estraneo), 'type': 'private'},
        'text': 'ciao',
        'forward_from_chat': {'id': ALTRO_CANALE, 'type': 'channel', 'title': 'Altro'}}})

    assert _impostazione(percorso, main.CHIAVE_CANALE_CANDIDATO_ID) is None, \
        'un estraneo ha potuto proporre un canale di backup'


def test_la_cattura_non_disturba_un_segnale_normale(tmp_path, monkeypatch):
    """Un `channel_post` normale (nessun my_chat_member, nessun forward) prosegue verso
    il dispatch: la cattura non intercetta i messaggi dei canali sorgente."""
    percorso, _a, _c, _u = _admin(tmp_path, monkeypatch, 'passa.db')
    _abilita_webhook(monkeypatch)
    esito = _webhook({'channel_post': {'chat': {'id': -100555, 'type': 'channel'},
                                       'text': 'un messaggio qualunque'}})
    # Non e' un candidato; il dispatch lo ignora perche' la chat non e' associata.
    assert 'canale_backup_candidato' not in esito
    assert _impostazione(percorso, main.CHIAVE_CANALE_CANDIDATO_ID) is None


# ---------------------------------------------------------------- le rotte

def _metti_candidato(chat_id, titolo):
    c = main.db()
    try:
        main.scrivi_impostazione(c, main.CHIAVE_CANALE_CANDIDATO_ID, str(chat_id))
        main.scrivi_impostazione(c, main.CHIAVE_CANALE_CANDIDATO_TITOLO, titolo)
        c.commit()
    finally:
        c.close()


def test_stato_vuoto_all_inizio(tmp_path, monkeypatch):
    _p, admin_s, _c, _u = _admin(tmp_path, monkeypatch, 'stato.db')
    corpo = _corpo(main.stato_canale_backup(admin_s))
    assert corpo == {'configurato': None, 'candidato': None}, corpo


def test_conferma_con_prova_riuscita_configura_e_traccia(tmp_path, monkeypatch):
    """Invio di prova OK → il candidato diventa configurato, il candidato si azzera,
    e resta una riga in admin_audit."""
    percorso, admin_s, _c, _u = _admin(tmp_path, monkeypatch, 'conferma.db')
    _metti_candidato(CANALE, 'Backup BetRelay')
    inviato = {}

    def finto_invio(chat_id, testo, bot_token=None):
        inviato['chat_id'] = chat_id
        return True, None

    monkeypatch.setattr(main, 'invia_messaggio_telegram', finto_invio)
    corpo = _corpo(main.conferma_canale_backup(admin_s))

    assert inviato['chat_id'] == str(CANALE), 'la prova non e- andata al canale candidato'
    assert corpo['configurato'] == {'chat_id': str(CANALE), 'titolo': 'Backup BetRelay'}
    assert corpo['candidato'] is None
    assert _impostazione(percorso, main.CHIAVE_CANALE_BACKUP_ID) == str(CANALE)
    assert _impostazione(percorso, main.CHIAVE_CANALE_CANDIDATO_ID) is None
    c = sqlite3.connect(percorso)
    quante = c.execute("SELECT COUNT(*) FROM admin_audit"
                       " WHERE action='canale_backup_configurato'").fetchone()[0]
    c.close()
    assert quante == 1, f'configurazione non tracciata: {quante} righe'


def test_conferma_con_prova_FALLITA_non_salva_e_mostra_errore(tmp_path, monkeypatch):
    """Invio di prova KO → 400 col motivo VISIBILE, niente configurato, candidato intatto."""
    from fastapi import HTTPException
    percorso, admin_s, _c, _u = _admin(tmp_path, monkeypatch, 'conferma_ko.db')
    _metti_candidato(CANALE, 'Backup BetRelay')

    def finto_invio(chat_id, testo, bot_token=None):
        return False, 'Telegram ha rifiutato la consegna'

    monkeypatch.setattr(main, 'invia_messaggio_telegram', finto_invio)
    with pytest.raises(HTTPException) as errore:
        main.conferma_canale_backup(admin_s)
    assert errore.value.status_code == 400
    assert 'Telegram ha rifiutato la consegna' in str(errore.value.detail), \
        'il motivo dell-invio fallito non e- visibile nella risposta'

    assert _impostazione(percorso, main.CHIAVE_CANALE_BACKUP_ID) is None, \
        'un canale e- stato configurato nonostante la prova fallita'
    assert _impostazione(percorso, main.CHIAVE_CANALE_CANDIDATO_ID) == str(CANALE), \
        'il candidato e- andato perso dopo una prova fallita'
    c = sqlite3.connect(percorso)
    quante = c.execute("SELECT COUNT(*) FROM admin_audit"
                       " WHERE action='canale_backup_configurato'").fetchone()[0]
    c.close()
    assert quante == 0, 'una prova fallita ha lasciato una traccia di configurazione'


def test_conferma_senza_candidato_e_400(tmp_path, monkeypatch):
    from fastapi import HTTPException
    _p, admin_s, _c, _u = _admin(tmp_path, monkeypatch, 'senza.db')
    with pytest.raises(HTTPException) as errore:
        main.conferma_canale_backup(admin_s)
    assert errore.value.status_code == 400


def test_prova_sul_configurato_e_rimozione(tmp_path, monkeypatch):
    percorso, admin_s, _c, _u = _admin(tmp_path, monkeypatch, 'prova.db')
    # niente configurato → 400
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as errore:
        main.prova_canale_backup(admin_s)
    assert errore.value.status_code == 400

    # configura via conferma
    _metti_candidato(CANALE, 'Backup')
    monkeypatch.setattr(main, 'invia_messaggio_telegram',
                        lambda chat_id, testo, bot_token=None: (True, None))
    main.conferma_canale_backup(admin_s)
    assert _corpo(main.prova_canale_backup(admin_s)) == {'inviato': True}

    # rimozione
    corpo = _corpo(main.rimuovi_canale_backup(admin_s))
    assert corpo == {'configurato': None, 'candidato': None}
    assert _impostazione(percorso, main.CHIAVE_CANALE_BACKUP_ID) is None
    c = sqlite3.connect(percorso)
    quante = c.execute("SELECT COUNT(*) FROM admin_audit"
                       " WHERE action='canale_backup_rimosso'").fetchone()[0]
    c.close()
    assert quante == 1, 'rimozione non tracciata'


def test_un_NON_admin_non_vede_il_canale_di_backup(tmp_path, monkeypatch):
    """404 e non 403 su ogni rotta, come tutto /api/admin/*."""
    from fastapi import HTTPException
    _p, _admin_s, cliente_s, _cliente = _admin(tmp_path, monkeypatch, 'estraneo_rotte.db')
    for chiamata in (lambda: main.stato_canale_backup(cliente_s),
                     lambda: main.conferma_canale_backup(cliente_s),
                     lambda: main.prova_canale_backup(cliente_s),
                     lambda: main.rimuovi_canale_backup(cliente_s)):
        with pytest.raises(HTTPException) as errore:
            chiamata()
        assert errore.value.status_code == 404, f'{errore.value.status_code} invece di 404'
