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


def _con_sito(sessione, sito):
    """La stessa sessione, ma con l'header Sec-Fetch-Site di una navigazione.

    Il browser lo impone e la pagina non lo puo' falsificare: e' cio' che distingue il
    click sul pulsante del pannello (`same-origin`) da un innesco da un altro sito.
    """
    class Richiesta:
        cookies = sessione.cookies
        headers = {'sec-fetch-site': sito}
    return Richiesta()


def test_una_navigazione_cross_site_e_rifiutata(tmp_path, monkeypatch):
    """Anti-CSRF: col cookie SameSite=Lax una navigazione top-level da un altro sito si
    porterebbe dietro la sessione e indurrebbe un backup costoso. Sec-Fetch-Site la
    smaschera: `cross-site` e `same-site` → 403, senza generare la copia."""
    from fastapi import HTTPException
    _p, admin_s, _cliente_s, _cliente = _admin(tmp_path, monkeypatch, 'csrf.db')
    for sito in ('cross-site', 'same-site'):
        with pytest.raises(HTTPException) as errore:
            asyncio.run(main.scarica_backup(_con_sito(admin_s, sito)))
        assert errore.value.status_code == 403, \
            f'{sito}: {errore.value.status_code} invece di 403'


def test_la_navigazione_legittima_scarica(tmp_path, monkeypatch):
    """`same-origin` (il pulsante del pannello), `none` (indirizzo digitato o segnalibro)
    e l'header assente (client che non lo manda) scaricano tutti la copia."""
    _p, admin_s, _cliente_s, _cliente = _admin(tmp_path, monkeypatch, 'legit.db')
    for richiesta in (_con_sito(admin_s, 'same-origin'),
                      _con_sito(admin_s, 'none'),
                      admin_s):  # admin_s: headers vuoti, sec-fetch-site assente
        risposta = asyncio.run(main.scarica_backup(richiesta))
        assert bytes(risposta.body)[:16] == b'SQLite format 3\x00', \
            'una navigazione legittima non ha scaricato un file SQLite valido'


def test_un_NON_admin_con_cross_site_vede_404_non_403(tmp_path, monkeypatch):
    """L'ordine dei controlli conta: `_solo_amministratore` PRIMA dell'anti-CSRF, cosi'
    un estraneo vede 404 (rotta inesistente) e non 403 (rotta esistente, contesto
    vietato) nemmeno con Sec-Fetch-Site cross-site. Fail-first: invertendo l'ordine il
    non-admin riceverebbe 403. Suggerito da GPT-5.5 al gate finale (#56)."""
    from fastapi import HTTPException
    _p, _admin_s, cliente_s, _cliente = _admin(tmp_path, monkeypatch, 'ordine.db')
    with pytest.raises(HTTPException) as errore:
        asyncio.run(main.scarica_backup(_con_sito(cliente_s, 'cross-site')))
    assert errore.value.status_code == 404, \
        f'{errore.value.status_code}: un non-admin non deve mai vedere che la rotta esiste'


def test_un_solo_backup_alla_volta_sotto_il_lucchetto(tmp_path, monkeypatch):
    """Due download concorrenti non materializzano il DB in memoria insieme.

    Si sostituisce la sola serializzazione con una lenta che conta i thread simultanei;
    il lucchetto VERO dentro `copia_backup_db` resta in gioco. Fail-first: senza il
    lucchetto i due `to_thread` girano insieme e il massimo osservato e' 2."""
    import threading
    import time as _time
    _p, admin_s, _cliente_s, _cliente = _admin(tmp_path, monkeypatch, 'concorrenza.db')

    stato = {'attivi': 0, 'massimo': 0}
    guardia = threading.Lock()

    def serializza_lento():
        with guardia:
            stato['attivi'] += 1
            stato['massimo'] = max(stato['massimo'], stato['attivi'])
        _time.sleep(0.2)
        with guardia:
            stato['attivi'] -= 1
        return b'SQLite format 3\x00' + b'\x00' * 64

    monkeypatch.setattr(main, '_serializza_db', serializza_lento)

    async def due_insieme():
        await asyncio.gather(main.scarica_backup(admin_s),
                             main.scarica_backup(admin_s))

    asyncio.run(due_insieme())
    assert stato['massimo'] == 1, \
        f"{stato['massimo']} copie in RAM insieme: il lucchetto non serializza i backup"


def test_una_copia_fallita_non_lascia_audit(tmp_path, monkeypatch):
    """La riga di audit va scritta DOPO la copia riuscita: se la copia fallisce non
    deve restare la traccia di un download mai avvenuto (nota di Fable 5). Fail-first:
    con l'audit committato prima della copia, una copia fallita lascia comunque la riga."""
    percorso, admin_s, _cliente_s, _cliente = _admin(tmp_path, monkeypatch, 'fallita.db')

    def esplode():
        raise RuntimeError('backup fallito di proposito')

    monkeypatch.setattr(main, '_serializza_db', esplode)
    with pytest.raises(RuntimeError):
        asyncio.run(main.scarica_backup(admin_s))

    c = sqlite3.connect(percorso)
    quante = c.execute("SELECT COUNT(*) FROM admin_audit"
                       " WHERE action='scarica_backup'").fetchone()[0]
    c.close()
    assert quante == 0, f'{quante} righe di audit per un download mai avvenuto'


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
