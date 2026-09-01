"""Configurazione del canale privato di backup (#56 pezzo 2).

Il backup del database (#56 pezzo 1) va a finire da qualche parte: il proprietario
decide un **canale Telegram privato** e ci aggiunge il bot come amministratore. Qui si
verifica la parte backend di quella configurazione:

- la **cattura**: il webhook riconosce il canale quando il proprietario promuove il bot
  amministratore (`my_chat_member`) — ma **solo** se l'azione viene dall'amministratore,
  e scrive solo un CANDIDATO. Non tocca `chats`: il canale di backup e' una destinazione,
  non una sorgente di segnali, e finire in `chats` lo iscriverebbe all'instradamento
  del webhook;
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
from tests.relay.test_accesso import _CorpoFinto, _admin  # noqa: E402
from tests.relay.test_login import ADMIN_FINTO, BOT_FINTO  # noqa: E402

CANALE = -1001234567890          # i canali hanno id negativi -100…
ALTRO_CANALE = -1009876543210


def _conferma(sessione, chat_id):
    """Chiama la rotta async di conferma con la precondizione dal client (Sol #56).

    La conferma ora e' `async def` e legge `{chat_id}` dal corpo — l'id del candidato che il
    pannello ha mostrato. `_CorpoFinto` usa l'interfaccia reale (`headers` + `stream()`), come
    le altre rotte che leggono il corpo a mano."""
    return asyncio.run(
        main.conferma_canale_backup(_CorpoFinto(sessione, {'chat_id': str(chat_id)})))


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


def _promozione(chat_id, titolo, attore=None, username=None, update_id=None):
    """Un update `my_chat_member`: il bot promosso amministratore di un canale.

    `username` presente = canale PUBBLICO (i privati non ne hanno). `update_id`, se dato,
    e' la chiave con cui Telegram identifica l'update: due consegne con lo STESSO
    `update_id` sono la stessa riconsegna, e il dedup deve trattarle come una sola."""
    chat = {'id': chat_id, 'type': 'channel', 'title': titolo}
    if username:
        chat['username'] = username
    payload = {'my_chat_member': {
        'from': {'id': int(attore if attore is not None else ADMIN_FINTO)},
        'chat': chat,
        'new_chat_member': {'status': 'administrator'}}}
    if update_id is not None:
        payload['update_id'] = update_id
    return payload


def _rimozione(chat_id, titolo='X', stato='left', attore=None, update_id=None):
    """Un update `my_chat_member` che RIMUOVE il bot da un canale (`left`/`kicked`).

    `my_chat_member` e' sempre un aggiornamento sulla membership del bot stesso, quindi
    non serve identificare l'utente: se lo stato nuovo e' `left`/`kicked`, il bot non e'
    piu' nel canale e non ci puo' piu' pubblicare i backup."""
    payload = {'my_chat_member': {
        'from': {'id': int(attore if attore is not None else ADMIN_FINTO)},
        'chat': {'id': chat_id, 'type': 'channel', 'title': titolo},
        'new_chat_member': {'status': stato}}}
    if update_id is not None:
        payload['update_id'] = update_id
    return payload


def _configura(admin_s, monkeypatch, chat_id, titolo='Backup'):
    """Configura un canale come quello di backup, per la via reale (candidato + conferma)."""
    _metti_candidato(chat_id, titolo)
    monkeypatch.setattr(main, 'invia_messaggio_telegram',
                        lambda chat_id, testo, bot_token=None: (True, None))
    _conferma(admin_s, chat_id)


# ---------------------------------------------------------------- la cattura

def test_il_bot_promosso_amministratore_dal_proprietario_diventa_candidato(tmp_path, monkeypatch):
    """`my_chat_member` con status administrator, dall'amministratore → candidato scritto.

    E NON crea una riga in `chats`: il canale di backup e' una destinazione, non una
    sorgente di segnali."""
    percorso, _admin_s, _cliente_s, _cliente = _admin(tmp_path, monkeypatch, 'cattura.db')
    _abilita_webhook(monkeypatch)

    esito = _webhook(_promozione(CANALE, 'Backup BetRelay'))

    assert esito.get('canale_backup_candidato') is True, esito
    assert _impostazione(percorso, main.CHIAVE_CANALE_CANDIDATO_ID) == str(CANALE)
    assert _impostazione(percorso, main.CHIAVE_CANALE_CANDIDATO_TITOLO) == 'Backup BetRelay'

    c = sqlite3.connect(percorso)
    in_chats = c.execute('SELECT COUNT(*) FROM chats WHERE telegram_chat_id=?',
                         (str(CANALE),)).fetchone()[0]
    c.close()
    assert in_chats == 0, 'il canale di backup e- finito in chats: verrebbe instradato come segnale'


def test_un_ESTRANEO_non_puo_proporre_un_canale(tmp_path, monkeypatch):
    """`my_chat_member` con `from.id` diverso dall'amministratore → nessun candidato.

    Senza questa guardia il canale di CHIUNQUE aggiunga il bot comparirebbe come
    proposta nel pannello del proprietario."""
    percorso, _a, _c, _u = _admin(tmp_path, monkeypatch, 'estraneo.db')
    _abilita_webhook(monkeypatch)

    estraneo = '111222333'
    assert estraneo != ADMIN_FINTO
    _webhook(_promozione(CANALE, 'Canale altrui', attore=estraneo))

    assert _impostazione(percorso, main.CHIAVE_CANALE_CANDIDATO_ID) is None, \
        'un estraneo ha potuto proporre un canale di backup'


def test_un_canale_PUBBLICO_non_diventa_candidato(tmp_path, monkeypatch):
    """Solo canali PRIVATI: un canale pubblico (con `username`) esporrebbe il backup —
    dati dei clienti — a chiunque. Fail-first: senza il vincolo su `username` un canale
    pubblico diventerebbe candidato. Bloccante di GPT-5.6 Sol al gate finale (#56)."""
    percorso, _admin_s, _cliente_s, _cliente = _admin(tmp_path, monkeypatch, 'pubblico.db')
    _abilita_webhook(monkeypatch)
    _webhook(_promozione(CANALE, 'Canale pubblico', username='canale_pubblico'))
    assert _impostazione(percorso, main.CHIAVE_CANALE_CANDIDATO_ID) is None, \
        'un canale PUBBLICO e- diventato candidato: il backup finirebbe in chiaro'


def test_una_riconsegna_del_canale_gia_configurato_non_lo_ripropone(tmp_path, monkeypatch):
    """Telegram riconsegna gli update, e la cattura gira prima del dedup: una riconsegna
    del `my_chat_member` dopo la conferma NON deve riproporre un canale gia' configurato."""
    percorso, admin_s, _c, _u = _admin(tmp_path, monkeypatch, 'riconsegna.db')
    _abilita_webhook(monkeypatch)
    monkeypatch.setattr(main, 'invia_messaggio_telegram',
                        lambda chat_id, testo, bot_token=None: (True, None))
    _webhook(_promozione(CANALE, 'Backup'))
    _conferma(admin_s, CANALE)
    assert _impostazione(percorso, main.CHIAVE_CANALE_BACKUP_ID) == str(CANALE)

    esito = _webhook(_promozione(CANALE, 'Backup'))  # la riconsegna
    assert esito.get('canale_backup_gia_configurato') is True, esito
    assert _impostazione(percorso, main.CHIAVE_CANALE_CANDIDATO_ID) is None, \
        'una riconsegna ha riproposto un canale gia- configurato'


def test_la_cattura_e_atomica_con_la_conferma(tmp_path, monkeypatch):
    """Una riconsegna concorrente con la conferma non deve mai lasciare un candidato per
    un canale GIA' configurato: il check-e-scrivi della cattura e' atomico (BEGIN
    IMMEDIATE), quindi si serializza con la conferma. Si allarga la finestra fra check e
    scrittura (uno sleep durante la lettura del configurato) e si lanciano due thread.
    Fail-first: senza BEGIN IMMEDIATE la conferma si intreccia nella finestra e resta un
    candidato per il canale configurato. Bloccante di GPT-5.6 Sol al gate finale (#56)."""
    import threading
    import time as _time
    percorso, admin_s, _c, _u = _admin(tmp_path, monkeypatch, 'atomica.db')
    _abilita_webhook(monkeypatch)
    monkeypatch.setattr(main, 'invia_messaggio_telegram',
                        lambda chat_id, testo, bot_token=None: (True, None))
    _metti_candidato(CANALE, 'X')  # candidato presente, non ancora configurato

    reale = main.leggi_impostazione

    def lenta(c, chiave, default=None):
        valore = reale(c, chiave, default)
        if chiave == main.CHIAVE_CANALE_BACKUP_ID:
            _time.sleep(0.25)  # allarga la finestra fra il check e la scrittura
        return valore

    monkeypatch.setattr(main, 'leggi_impostazione', lenta)

    esiti = {}

    def riconsegna():
        _webhook(_promozione(CANALE, 'X'))

    def conferma():
        try:
            _conferma(admin_s, CANALE)
            esiti['conferma'] = 'ok'
        except Exception as e:  # noqa: BLE001
            esiti['conferma'] = repr(e)

    t_cap = threading.Thread(target=riconsegna)
    t_con = threading.Thread(target=conferma)
    t_cap.start()
    _time.sleep(0.05)  # la cattura entra per prima nella finestra
    t_con.start()
    t_cap.join()
    t_con.join()

    configurato = _impostazione(percorso, main.CHIAVE_CANALE_BACKUP_ID)
    candidato = _impostazione(percorso, main.CHIAVE_CANALE_CANDIDATO_ID)
    # La conferma DEVE essere riuscita, o l'assert sull'invariante passerebbe a vuoto:
    # senza configurazione `configurato` resterebbe None e la race non sarebbe esercitata.
    # Cosi' invece il test prova INSIEME che la conferma configura e che la cattura
    # concorrente non lascia un candidato per il canale configurato (nota di GPT-5.5).
    assert esiti.get('conferma') == 'ok', f'la conferma non e- riuscita: {esiti.get("conferma")}'
    assert configurato == str(CANALE), f'la conferma non ha configurato il canale: {configurato}'
    assert candidato is None, (
        'un canale CONFIGURATO ha ancora un candidato: la cattura non e- atomica con la conferma')


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


def test_la_cattura_del_canale_gira_fuori_dall_event_loop(tmp_path, monkeypatch):
    """Il `BEGIN IMMEDIATE` della cattura non deve girare sull'event loop: sotto contesa con la
    conferma attenderebbe il busy_timeout bloccando TUTTE le consegne webhook. Deve girare su un
    thread diverso da quello che serve la consegna. Fail-first: chiamata direttamente sul loop
    (senza `asyncio.to_thread`), i due identificativi di thread coincidono. Bloccante di Claude
    Fable 5 al gate finale (#56)."""
    import threading
    _p, _a, _c, _u = _admin(tmp_path, monkeypatch, 'offload.db')
    _abilita_webhook(monkeypatch)
    identita = {}
    reale = main._cattura_canale_backup

    def spia(payload):
        identita['cattura'] = threading.get_ident()
        return reale(payload)

    monkeypatch.setattr(main, '_cattura_canale_backup', spia)

    async def guida():
        identita['loop'] = threading.get_ident()

        class Richiesta:
            headers = {'X-Telegram-Bot-Api-Secret-Token': main.webhook_secret(BOT_FINTO)}

            async def json(self):
                return _promozione(CANALE, 'Backup')

        return await main.telegram_webhook(Richiesta())

    esito = asyncio.run(guida())
    assert esito.get('canale_backup_candidato') is True, esito
    assert identita.get('cattura') is not None, 'la cattura non e- stata chiamata'
    assert identita['cattura'] != identita['loop'], \
        'la cattura ha girato sull-event loop invece che su un thread separato'


def test_un_my_chat_member_ignorato_passa_senza_scrivere_nulla(tmp_path, monkeypatch):
    """Un `my_chat_member` che la cattura ignora (qui il bot RIMOSSO, status `left`) prosegue nel
    percorso normale con `msg` vuoto e si ferma su `no_text` PRIMA di ogni scrittura: niente
    candidato, e niente riga in `webhook_seen` (il dedup vive dentro `_processa_messaggio_canale`,
    mai raggiunto perche' `if not text` torna prima). Copre il passthrough segnalato da Claude
    Fable 5 al gate finale (#56)."""
    percorso, _a, _c, _u = _admin(tmp_path, monkeypatch, 'passthrough.db')
    _abilita_webhook(monkeypatch)
    rimozione = {'update_id': 777, 'my_chat_member': {
        'from': {'id': int(ADMIN_FINTO)},
        'chat': {'id': CANALE, 'type': 'channel', 'title': 'X'},
        'new_chat_member': {'status': 'left'}}}
    esito = _webhook(rimozione)
    assert esito == {'ok': True, 'ignored': 'no_text'}, esito
    assert _impostazione(percorso, main.CHIAVE_CANALE_CANDIDATO_ID) is None, \
        'un my_chat_member ignorato ha scritto un candidato'
    c = sqlite3.connect(percorso)
    try:
        n = c.execute('SELECT COUNT(*) FROM webhook_seen').fetchone()[0]
    finally:
        c.close()
    assert n == 0, 'un my_chat_member ignorato ha lasciato una riga in webhook_seen con chat_id vuoto'


# ------------------------------------------ dedup e pulizia del ciclo di vita (#56 pezzo 3b)

def test_una_riconsegna_con_lo_stesso_update_id_non_riscrive_il_candidato(tmp_path, monkeypatch):
    """Dedup della cattura per `update_id` (Sol B1, #56). Il bot viene promosso (candidato
    scritto), poi il candidato viene tolto (qui: il bot rimosso dal canale). Se Telegram
    RICONSEGNA la promozione ORIGINALE — stesso `update_id` — il candidato NON deve
    risorgere: quell'update e' gia' stato elaborato.

    Fail-first: senza il dedup su `webhook_seen` la riconsegna riscrive il candidato per un
    canale da cui il bot e' gia' uscito."""
    percorso, _admin_s, _c, _u = _admin(tmp_path, monkeypatch, 'dedup_cattura.db')
    _abilita_webhook(monkeypatch)

    esito = _webhook(_promozione(CANALE, 'Backup', update_id=4242))
    assert esito.get('canale_backup_candidato') is True, esito
    assert _impostazione(percorso, main.CHIAVE_CANALE_CANDIDATO_ID) == str(CANALE)

    # il bot esce dal canale: il candidato viene ripulito (update_id diverso)
    _webhook(_rimozione(CANALE, update_id=4243))
    assert _impostazione(percorso, main.CHIAVE_CANALE_CANDIDATO_ID) is None

    # Telegram riconsegna la promozione ORIGINALE (stesso update_id): deve essere un duplicato
    esito2 = _webhook(_promozione(CANALE, 'Backup', update_id=4242))
    assert esito2.get('ignored') == 'duplicate', esito2
    assert _impostazione(percorso, main.CHIAVE_CANALE_CANDIDATO_ID) is None, \
        'una riconsegna con lo stesso update_id ha fatto risorgere il candidato'


def test_il_bot_rimosso_dal_canale_CONFIGURATO_pulisce_la_config(tmp_path, monkeypatch):
    """Il bot rimosso (`left`/`kicked`) dal canale CONFIGURATO → la config si azzera (Fable, #56).

    Se non si pulisce, il pannello continua a mostrare un canale dove il bot non puo' piu'
    postare e ogni backup fallisce in silenzio finche' qualcuno non se ne accorge.

    Fail-first: senza la pulizia il canale resta configurato dopo che il bot ne e' uscito."""
    percorso, admin_s, _c, _u = _admin(tmp_path, monkeypatch, 'pulizia_conf.db')
    _abilita_webhook(monkeypatch)
    _configura(admin_s, monkeypatch, CANALE, 'Backup')
    assert _impostazione(percorso, main.CHIAVE_CANALE_BACKUP_ID) == str(CANALE)

    esito = _webhook(_rimozione(CANALE, stato='kicked'))
    assert esito.get('canale_backup_rimosso') is True, esito
    assert _impostazione(percorso, main.CHIAVE_CANALE_BACKUP_ID) is None, \
        'il canale e- rimasto configurato dopo che il bot ne e- uscito'
    assert _impostazione(percorso, main.CHIAVE_CANALE_BACKUP_TITOLO) is None


def test_il_bot_rimosso_dal_canale_CANDIDATO_pulisce_il_candidato(tmp_path, monkeypatch):
    """Speculare del precedente sul CANDIDATO non ancora confermato: se il bot esce, la
    proposta va tolta, o il pannello inviterebbe a confermare un canale gia' abbandonato."""
    percorso, _admin_s, _c, _u = _admin(tmp_path, monkeypatch, 'pulizia_cand.db')
    _abilita_webhook(monkeypatch)
    _webhook(_promozione(CANALE, 'Backup'))
    assert _impostazione(percorso, main.CHIAVE_CANALE_CANDIDATO_ID) == str(CANALE)

    esito = _webhook(_rimozione(CANALE, stato='left'))
    assert esito.get('canale_backup_rimosso') is True, esito
    assert _impostazione(percorso, main.CHIAVE_CANALE_CANDIDATO_ID) is None, \
        'il candidato e- rimasto dopo che il bot e- uscito dal canale'


def test_il_bot_rimosso_da_un_canale_ESTRANEO_non_tocca_la_config(tmp_path, monkeypatch):
    """La pulizia agisce SOLO sul canale nostro: un `left` da un canale diverso da quello
    configurato/candidato non deve azzerare niente, e prosegue nel percorso normale (`no_text`).
    Guardia contro l'eccesso di pulizia — un estraneo che spinge il bot fuori da un suo canale
    non deve poter spegnere il backup del proprietario."""
    percorso, admin_s, _c, _u = _admin(tmp_path, monkeypatch, 'estraneo_left.db')
    _abilita_webhook(monkeypatch)
    _configura(admin_s, monkeypatch, CANALE, 'Backup')

    esito = _webhook(_rimozione(ALTRO_CANALE, stato='left'))
    assert esito == {'ok': True, 'ignored': 'no_text'}, esito
    assert _impostazione(percorso, main.CHIAVE_CANALE_BACKUP_ID) == str(CANALE), \
        'un left da un canale estraneo ha azzerato la config del canale nostro'


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
    corpo = _corpo(_conferma(admin_s, CANALE))

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
        _conferma(admin_s, CANALE)
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


def test_conferma_abbandona_se_il_candidato_cambia_durante_la_prova(tmp_path, monkeypatch):
    """Race TOCTOU: la prova (rete) avviene fuori transazione. Se una cattura concorrente
    scrive un ALTRO candidato mentre la prova e' in volo, la conferma NON deve configurare
    quello vecchio (cancellerebbe il nuovo senza traccia): 409, niente configurato, il
    candidato NUOVO resta. Fail-first: senza la rilettura in transazione, il vecchio viene
    salvato e il nuovo cancellato. Segnalato da GPT-5.5 e Fable 5."""
    from fastapi import HTTPException
    percorso, admin_s, _c, _u = _admin(tmp_path, monkeypatch, 'race.db')
    _metti_candidato(CANALE, 'Canale A')

    def invio_che_cambia_candidato(chat_id, testo, bot_token=None):
        # simula una cattura concorrente che scrive un altro candidato durante la prova
        _metti_candidato(ALTRO_CANALE, 'Canale B')
        return True, None

    monkeypatch.setattr(main, 'invia_messaggio_telegram', invio_che_cambia_candidato)
    with pytest.raises(HTTPException) as errore:
        _conferma(admin_s, CANALE)
    assert errore.value.status_code == 409, f'{errore.value.status_code} invece di 409'
    assert _impostazione(percorso, main.CHIAVE_CANALE_BACKUP_ID) is None, \
        'e- stato configurato il candidato vecchio nonostante fosse cambiato'
    assert _impostazione(percorso, main.CHIAVE_CANALE_CANDIDATO_ID) == str(ALTRO_CANALE), \
        'il candidato NUOVO e- stato cancellato dalla conferma del vecchio'


def test_la_conferma_rifiuta_un_candidato_diverso_da_quello_mostrato(tmp_path, monkeypatch):
    """Precondizione dal client (Sol, gate finale #56): la conferma porta l'`chat_id` che il
    pannello ha mostrato. Se fra il GET e il POST una riconsegna ha cambiato il candidato,
    confermare l'id vecchio configurerebbe una destinazione che l'admin NON ha approvato → 409,
    nessun invio di prova, niente configurato, candidato corrente intatto. Fail-first: senza il
    confronto `candidato['chat_id'] != dati.chat_id` la conferma manderebbe la prova al candidato
    corrente e lo configurerebbe, ignorando l'id approvato."""
    from fastapi import HTTPException
    percorso, admin_s, _c, _u = _admin(tmp_path, monkeypatch, 'precond.db')
    # Il pannello aveva mostrato CANALE; una riconsegna ha poi cambiato il candidato corrente.
    _metti_candidato(ALTRO_CANALE, 'Canale nuovo')
    inviato = {}

    def finto_invio(chat_id, testo, bot_token=None):
        inviato['chat_id'] = chat_id
        return True, None

    monkeypatch.setattr(main, 'invia_messaggio_telegram', finto_invio)
    with pytest.raises(HTTPException) as errore:
        _conferma(admin_s, CANALE)   # l'admin conferma quello che aveva visto: CANALE
    assert errore.value.status_code == 409, f'{errore.value.status_code} invece di 409'
    assert 'chat_id' not in inviato, 'la prova e- partita verso un candidato non approvato'
    assert _impostazione(percorso, main.CHIAVE_CANALE_BACKUP_ID) is None, \
        'e- stato configurato un candidato diverso da quello approvato dall-admin'
    assert _impostazione(percorso, main.CHIAVE_CANALE_CANDIDATO_ID) == str(ALTRO_CANALE), \
        'il candidato corrente e- stato toccato'


def test_la_conferma_manda_la_prova_fuori_dall_event_loop(tmp_path, monkeypatch):
    """La rotta di conferma e' `async`, quindi gira sul loop: l'invio di prova e' un I/O di rete
    SINCRONO e va off-loaded, o l'attesa di Telegram bloccherebbe TUTTE le richieste del servizio
    (webhook, feed CSV) fino al timeout. Deve girare su un thread diverso da quello del loop.
    Fail-first: chiamando `invia_messaggio_telegram` direttamente, i due identificativi di thread
    coincidono. Bloccante di GPT-5.6 Sol al gate finale (#56)."""
    import threading
    _p, admin_s, _c, _u = _admin(tmp_path, monkeypatch, 'offload_conferma.db')
    _metti_candidato(CANALE, 'Backup')
    identita = {}

    def invio_spia(chat_id, testo, bot_token=None):
        identita['invio'] = threading.get_ident()
        return True, None

    monkeypatch.setattr(main, 'invia_messaggio_telegram', invio_spia)

    async def guida():
        identita['loop'] = threading.get_ident()
        return await main.conferma_canale_backup(_CorpoFinto(admin_s, {'chat_id': str(CANALE)}))

    esito = asyncio.run(guida())
    assert _corpo(esito)['configurato'] == {'chat_id': str(CANALE), 'titolo': 'Backup'}
    assert identita.get('invio') is not None, 'l-invio di prova non e- stato chiamato'
    assert identita['invio'] != identita['loop'], \
        'l-invio di prova ha girato sull-event loop invece che su un thread'


def test_conferma_senza_corpo_valido_e_422_non_404(tmp_path, monkeypatch):
    """La conferma legge `{chat_id}` dal corpo DOPO il controllo di sessione: un admin con un
    corpo senza `chat_id` riceve 422 (validazione), non 404 — il 404 e' la risposta al
    NON-admin, e leggere il corpo prima della sessione lo trasformerebbe nell'oracolo «questa
    rotta esiste» (stessa ragione di `approva_richiesta`). Segnalato da GPT-5.5 sulla PR #100."""
    from fastapi import HTTPException
    _p, admin_s, _c, _u = _admin(tmp_path, monkeypatch, 'corpo.db')
    with pytest.raises(HTTPException) as errore:
        asyncio.run(main.conferma_canale_backup(_CorpoFinto(admin_s, {'altro': 'x'})))
    assert errore.value.status_code == 422, f'{errore.value.status_code} invece di 422'


def test_conferma_senza_candidato_e_400(tmp_path, monkeypatch):
    from fastapi import HTTPException
    _p, admin_s, _c, _u = _admin(tmp_path, monkeypatch, 'senza.db')
    with pytest.raises(HTTPException) as errore:
        _conferma(admin_s, CANALE)
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
    _conferma(admin_s, CANALE)
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
                     lambda: _conferma(cliente_s, CANALE),
                     lambda: main.prova_canale_backup(cliente_s),
                     lambda: main.rimuovi_canale_backup(cliente_s)):
        with pytest.raises(HTTPException) as errore:
            chiamata()
        assert errore.value.status_code == 404, f'{errore.value.status_code} invece di 404'
