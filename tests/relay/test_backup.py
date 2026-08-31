"""Backup del database: copia consistente, scaricabile solo dall'amministratore (#56).

Tutti i dati del servizio stanno in un file (`signals.db`): se si perde, si perde
tutto. Due cose vincolate qui:

- la copia si prende con l'**API di backup di SQLite**, non con un `cp` del file a
  caldo — una copia grezza mentre il servizio scrive puo' uscire a meta' di una
  transazione, e un backup corrotto e' peggio di nessuno perche' lo si scopre solo
  il giorno in cui serve. Il test lo verifica con `PRAGMA integrity_check`;
- il file contiene i dati dei clienti (token solo come hash), quindi lo scarica
  **solo** il proprietario: **404** per chiunque altro, come il resto di
  `/api/admin/*` (un 403 confermerebbe a un estraneo che la rotta esiste).
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
# `_admin` (proprietario con sessione + cliente) sta in test_accesso: fonte unica,
# non la si riscrive qui.
from tests.relay.test_accesso import _admin  # noqa: E402


def test_il_backup_e_una_copia_CONSISTENTE_e_scaricabile(tmp_path, monkeypatch):
    """Si apre come SQLite valido, passa integrity_check e porta i dati veri."""
    _percorso, admin_s, _cliente_s, _cliente = _admin(tmp_path, monkeypatch, 'scarica.db')

    risposta = asyncio.run(main.scarica_backup(admin_s))
    dati = bytes(risposta.body)
    assert dati[:16] == b'SQLite format 3\x00', f'non e- un file SQLite: {dati[:16]!r}'

    cd = risposta.headers.get('content-disposition') or ''
    assert 'attachment' in cd and 'betrelay-backup-' in cd and cd.rstrip().endswith('.db"'), \
        f'Content-Disposition inatteso: {cd!r}'

    # La rotta legge la sessione, quindi RINNOVA il cookie come ogni rotta autenticata
    # (senza, la sessione dell'admin scadrebbe dal login e non dall'inattivita').
    assert main.NOME_COOKIE in (risposta.headers.get('set-cookie') or ''), \
        'la risposta del backup non rinnova il cookie di sessione'

    copia = str(tmp_path / 'copia.db')
    with open(copia, 'wb') as f:
        f.write(dati)
    c = sqlite3.connect(copia)
    integrita = c.execute('PRAGMA integrity_check').fetchone()[0]
    utenti = c.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    c.close()
    assert integrita == 'ok', f'la copia non e- integra: {integrita!r}'
    assert utenti >= 1, 'il backup non contiene gli utenti del database vivo'


def test_un_NON_admin_riceve_404(tmp_path, monkeypatch):
    """404 e non 403: un estraneo non deve nemmeno sapere che il backup esiste."""
    from fastapi import HTTPException
    _p, _admin_s, cliente_s, _cliente = _admin(tmp_path, monkeypatch, 'estraneo.db')
    with pytest.raises(HTTPException) as errore:
        asyncio.run(main.scarica_backup(cliente_s))
    assert errore.value.status_code == 404, f'{errore.value.status_code} invece di 404'


def test_il_download_e_tracciato_in_audit(tmp_path, monkeypatch):
    """Chi si porta via l'intero database lascia una riga in admin_audit."""
    percorso, admin_s, _c, _u = _admin(tmp_path, monkeypatch, 'audit.db')
    asyncio.run(main.scarica_backup(admin_s))
    c = sqlite3.connect(percorso)
    quante = c.execute("SELECT COUNT(*) FROM admin_audit"
                       " WHERE action='scarica_backup'").fetchone()[0]
    c.close()
    assert quante == 1, f'download non tracciato: {quante} righe scarica_backup'


def test_la_copia_riflette_lo_stato_CORRENTE(tmp_path, monkeypatch):
    """La copia e' dello stato committato adesso: la riga di audit del download
    precedente compare nella copia del download successivo."""
    _percorso, admin_s, _c, _u = _admin(tmp_path, monkeypatch, 'stato.db')
    asyncio.run(main.scarica_backup(admin_s))            # scrive una riga scarica_backup
    dati = bytes(asyncio.run(main.scarica_backup(admin_s)).body)   # la copia deve vederla
    copia = str(tmp_path / 'stato_copia.db')
    with open(copia, 'wb') as f:
        f.write(dati)
    c = sqlite3.connect(copia)
    trovato = c.execute("SELECT COUNT(*) FROM admin_audit"
                        " WHERE action='scarica_backup'").fetchone()[0]
    c.close()
    assert trovato >= 1, 'la copia non riflette una scrittura gia- committata'
