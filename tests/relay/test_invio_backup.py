"""Invio del backup al canale privato configurato (#56 pezzo 3).

Il backup del database (pezzo 1) e il canale privato di destinazione (pezzo 2) esistono; qui
si manda davvero il file al canale. Cosa e' vincolato:

- la copia va su un **file** temporaneo, non in RAM (`copia_backup_su_file`), e si carica in
  **streaming** via `sendDocument` — il rischio OOM sollevato al gate del pezzo 1;
- la privacy si **riverifica con `getChat` PRIMA di ogni invio**: la cattura garantisce un
  canale privato, ma un canale reso pubblico dopo la conferma esporrebbe i dati dei clienti,
  quindi se ora ha uno `username` NON si invia (Sol, gate del pezzo 2);
- la rotta ha **due modi di autenticarsi**: la sessione dell'amministratore (il bottone) o il
  token del cron (`BACKUP_CRON_TOKEN`) per il giro notturno di Railway; senza nessuno dei due
  → **404**, come tutto `/api/admin/*`;
- l'invio (rete + I/O) gira **fuori dall'event loop**, o bloccherebbe webhook e feed.
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE))

import main  # noqa: E402 - dopo l'inserimento del percorso
from tests.relay.test_accesso import _admin  # noqa: E402

CANALE = -1001234567890


def _configura_canale(chat_id=CANALE, titolo='Backup Piero'):
    c = main.db()
    try:
        main.scrivi_impostazione(c, main.CHIAVE_CANALE_BACKUP_ID, str(chat_id))
        main.scrivi_impostazione(c, main.CHIAVE_CANALE_BACKUP_TITOLO, titolo)
        c.commit()
    finally:
        c.close()


class _RichiestaCron:
    """Una richiesta senza sessione ma col token del cron nell'header."""

    def __init__(self, token):
        self.cookies = {}
        self.headers = {'X-Backup-Cron-Token': token}


def _corpo(risposta):
    import json
    if isinstance(risposta, dict):
        return risposta
    return json.loads(bytes(risposta.body).decode())


# ---------------------------------------------------------- copia su file

def test_copia_backup_su_file_e_una_copia_integra(tmp_path, monkeypatch):
    """`copia_backup_su_file` scrive un file SQLite valido, integro e coi dati veri — su DISCO,
    non materializzando i byte in RAM (differenza col `serialize()` di `copia_backup_db`)."""
    _p, _admin_s, _c, _u = _admin(tmp_path, monkeypatch, 'sufile.db')
    destinazione = str(tmp_path / 'copia_backup.db')
    main.copia_backup_su_file(destinazione)
    with open(destinazione, 'rb') as f:
        assert f.read(16) == b'SQLite format 3\x00', 'la copia non e- un file SQLite'
    c = sqlite3.connect(destinazione)
    integrita = c.execute('PRAGMA integrity_check').fetchone()[0]
    utenti = c.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    c.close()
    assert integrita == 'ok', f'copia non integra: {integrita!r}'
    assert utenti >= 1, 'la copia non contiene gli utenti del database vivo'


# ---------------------------------------------------- sendDocument multipart

class _FintaRisposta:
    def __init__(self, corpo):
        self._corpo = corpo

    def read(self):
        return self._corpo

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_invia_documento_costruisce_un_multipart_e_streama_il_file(tmp_path, monkeypatch):
    """Il multipart porta chat_id, filename e i byte del file; la risposta `ok:true` → riuscito."""
    percorso = str(tmp_path / 'x.db')
    with open(percorso, 'wb') as f:
        f.write(b'SQLite format 3\x00' + b'CONTENUTODB' * 4)
    catturato = {}

    def finto_urlopen(richiesta, timeout=None):
        catturato['url'] = richiesta.full_url
        catturato['ct'] = richiesta.get_header('Content-type')
        catturato['body'] = richiesta.data.read()   # lo stream aperto del multipart
        return _FintaRisposta(b'{"ok":true,"result":{"message_id":1}}')

    monkeypatch.setattr('urllib.request.urlopen', finto_urlopen)
    riuscito, motivo = main.invia_documento_telegram(
        CANALE, percorso, 'betrelay-backup-2026-08-31-0000.db',
        didascalia='BetRelay: backup', bot_token='TOKENFINTO')
    assert riuscito is True and motivo is None, (riuscito, motivo)
    assert catturato['url'].endswith('/sendDocument')
    assert 'multipart/form-data; boundary=' in catturato['ct']
    corpo = catturato['body']
    assert b'name="chat_id"' in corpo and str(CANALE).encode() in corpo
    assert b'filename="betrelay-backup-2026-08-31-0000.db"' in corpo
    assert b'CONTENUTODB' in corpo, 'i byte del file non sono finiti nel multipart'


def test_invia_documento_riporta_il_rifiuto_di_telegram(tmp_path, monkeypatch):
    percorso = str(tmp_path / 'y.db')
    with open(percorso, 'wb') as f:
        f.write(b'contenuto')
    monkeypatch.setattr('urllib.request.urlopen',
                        lambda richiesta, timeout=None: _FintaRisposta(b'{"ok":false}'))
    riuscito, motivo = main.invia_documento_telegram(CANALE, percorso, 'b.db', bot_token='T')
    assert riuscito is False and 'rifiutato' in motivo, (riuscito, motivo)


def test_invia_documento_non_mette_il_token_nel_motivo(tmp_path, monkeypatch):
    """Un errore di rete non deve far uscire il token (che sta nell'URL): il motivo e' il TIPO."""
    percorso = str(tmp_path / 'z.db')
    with open(percorso, 'wb') as f:
        f.write(b'contenuto')

    def esplode(richiesta, timeout=None):
        raise OSError('connessione rifiutata')

    monkeypatch.setattr('urllib.request.urlopen', esplode)
    riuscito, motivo = main.invia_documento_telegram(CANALE, percorso, 'b.db', bot_token='SEGRETO')
    assert riuscito is False
    assert 'SEGRETO' not in motivo and 'OSError' in motivo, motivo


# ------------------------------------------------------------- getChat

def test_leggi_chat_ritorna_il_result(monkeypatch):
    monkeypatch.setattr('urllib.request.urlopen', lambda url, timeout=None: _FintaRisposta(
        b'{"ok":true,"result":{"id":-100,"type":"channel","title":"X"}}'))
    ok, dati = main.leggi_chat_telegram(CANALE, bot_token='T')
    assert ok is True and dati.get('title') == 'X', (ok, dati)


# ------------------------------------------------- orchestrazione dell'invio

def test_backup_al_canale_senza_canale_configurato(tmp_path, monkeypatch):
    _p, _a, _c, _u = _admin(tmp_path, monkeypatch, 'nocanale.db')
    riuscito, motivo = main._invia_backup_al_canale()
    assert riuscito is False and 'nessun canale' in motivo, (riuscito, motivo)


def test_backup_al_canale_rifiuta_un_canale_diventato_PUBBLICO(tmp_path, monkeypatch):
    """Riverifica privacy con getChat: se il canale ora ha uno `username` (pubblico) NON si
    invia — il backup coi dati dei clienti non deve finire dove chiunque puo' leggerlo. Fail-first:
    senza il controllo su `username` il documento partirebbe lo stesso. Sol, gate del pezzo 2."""
    _p, _a, _c, _u = _admin(tmp_path, monkeypatch, 'pubblico.db')
    _configura_canale()
    monkeypatch.setattr(main, 'leggi_chat_telegram',
                        lambda chat_id, bot_token=None: (True, {'id': CANALE, 'username': 'ora_pubblico'}))
    inviato = {}
    monkeypatch.setattr(main, 'invia_documento_telegram',
                        lambda *a, **k: inviato.setdefault('si', True) or (True, None))
    riuscito, motivo = main._invia_backup_al_canale()
    assert riuscito is False and 'pubblico' in motivo, (riuscito, motivo)
    assert 'si' not in inviato, 'il backup e- partito verso un canale diventato pubblico'


def test_backup_al_canale_invia_e_traccia(tmp_path, monkeypatch):
    """Canale privato: si copia su file, si manda al chat_id giusto, e resta una riga di audit."""
    percorso, _a, _c, _u = _admin(tmp_path, monkeypatch, 'invia.db')
    _configura_canale()
    monkeypatch.setattr(main, 'leggi_chat_telegram',
                        lambda chat_id, bot_token=None: (True, {'id': CANALE, 'type': 'channel'}))
    visto = {}

    def finto_invio(chat_id, percorso_file, nome, didascalia=None, bot_token=None):
        visto['chat_id'] = chat_id
        with open(percorso_file, 'rb') as f:
            visto['magic'] = f.read(16)
        return True, None

    monkeypatch.setattr(main, 'invia_documento_telegram', finto_invio)
    riuscito, motivo = main._invia_backup_al_canale(amministratore_id=7)
    assert riuscito is True, motivo
    assert visto['chat_id'] == str(CANALE), 'il backup non e- andato al canale configurato'
    assert visto['magic'] == b'SQLite format 3\x00', 'il file inviato non e- una copia SQLite'
    c = sqlite3.connect(percorso)
    righe = c.execute("SELECT admin_user_id FROM admin_audit"
                      " WHERE action='backup_inviato'").fetchall()
    c.close()
    assert righe == [(7,)], f'audit inatteso per l-invio del backup: {righe}'


# --------------------------------------------------------------- la rotta

def test_invia_backup_con_sessione_admin(tmp_path, monkeypatch):
    percorso, admin_s, _c, _u = _admin(tmp_path, monkeypatch, 'rotta_admin.db')
    _configura_canale()
    monkeypatch.setattr(main, '_invia_backup_al_canale',
                        lambda amministratore_id=None: (True, None))
    corpo = _corpo(asyncio.run(main.invia_backup(admin_s)))
    assert corpo == {'inviato': True}, corpo


def test_invia_backup_col_token_del_cron_traccia_senza_admin(tmp_path, monkeypatch):
    """Il giro notturno: nessuna sessione, ma il token del cron nell'header → invio, e l'audit
    ha `admin_user_id` NULL (non e' stato un amministratore a premere il bottone)."""
    percorso, _admin_s, _c, _u = _admin(tmp_path, monkeypatch, 'rotta_cron.db')
    _configura_canale()
    monkeypatch.setattr(main, 'BACKUP_CRON_TOKEN', 'segreto-del-cron')
    monkeypatch.setattr(main, 'leggi_chat_telegram',
                        lambda chat_id, bot_token=None: (True, {'id': CANALE}))
    monkeypatch.setattr(main, 'invia_documento_telegram',
                        lambda *a, **k: (True, None))
    corpo = _corpo(asyncio.run(main.invia_backup(_RichiestaCron('segreto-del-cron'))))
    assert corpo == {'inviato': True}, corpo
    c = sqlite3.connect(percorso)
    nulli = c.execute("SELECT COUNT(*) FROM admin_audit"
                      " WHERE action='backup_inviato' AND admin_user_id IS NULL").fetchone()[0]
    c.close()
    assert nulli == 1, f'il giro del cron non ha tracciato un audit senza admin: {nulli}'


def test_invia_backup_col_token_SBAGLIATO_e_404(tmp_path, monkeypatch):
    """Un token che non combacia, e nessuna sessione → 404, come un estraneo qualunque."""
    from fastapi import HTTPException
    _p, _admin_s, _c, _u = _admin(tmp_path, monkeypatch, 'token_ko.db')
    monkeypatch.setattr(main, 'BACKUP_CRON_TOKEN', 'quello-giusto')
    with pytest.raises(HTTPException) as errore:
        asyncio.run(main.invia_backup(_RichiestaCron('quello-sbagliato')))
    assert errore.value.status_code == 404, f'{errore.value.status_code} invece di 404'


def test_invia_backup_senza_token_configurato_il_cron_non_entra(tmp_path, monkeypatch):
    """Fail-closed: `BACKUP_CRON_TOKEN` vuoto non autorizza NESSUN token (nemmeno una stringa
    vuota che combacerebbe con `''`). Senza sessione → 404."""
    from fastapi import HTTPException
    _p, _admin_s, _c, _u = _admin(tmp_path, monkeypatch, 'token_vuoto.db')
    monkeypatch.setattr(main, 'BACKUP_CRON_TOKEN', '')
    for header in ('', 'qualcosa'):
        with pytest.raises(HTTPException) as errore:
            asyncio.run(main.invia_backup(_RichiestaCron(header)))
        assert errore.value.status_code == 404, f'token vuoto, header {header!r}: atteso 404'


def test_invia_backup_admin_senza_canale_e_400(tmp_path, monkeypatch):
    from fastapi import HTTPException
    _p, admin_s, _c, _u = _admin(tmp_path, monkeypatch, 'rotta_nocanale.db')
    with pytest.raises(HTTPException) as errore:
        asyncio.run(main.invia_backup(admin_s))
    assert errore.value.status_code == 400, f'{errore.value.status_code} invece di 400'


def test_invia_backup_gira_fuori_dall_event_loop(tmp_path, monkeypatch):
    """La rotta e' `async`: l'invio (rete + I/O) va off-loaded, o bloccherebbe webhook e feed di
    tutti fino al timeout. Deve girare su un thread ≠ da quello del loop. Fail-first: con la
    chiamata diretta i due identificativi di thread coincidono."""
    _p, admin_s, _c, _u = _admin(tmp_path, monkeypatch, 'rotta_offload.db')
    identita = {}

    def spia(amministratore_id=None):
        identita['invio'] = threading.get_ident()
        return True, None

    monkeypatch.setattr(main, '_invia_backup_al_canale', spia)

    async def guida():
        identita['loop'] = threading.get_ident()
        return await main.invia_backup(admin_s)

    _corpo(asyncio.run(guida()))
    assert identita.get('invio') is not None, 'l-invio non e- stato chiamato'
    assert identita['invio'] != identita['loop'], \
        'l-invio del backup ha girato sull-event loop invece che su un thread'
