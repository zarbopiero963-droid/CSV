"""Avvia il relay, semina le richieste e pilota il pannello admin (#7).

Il lavoro browser lo fa `pannello_admin_flow.py`; qui si prepara il mondo:
due clienti `in_attesa` con la loro riga in `access_requests`, il cookie
dell'amministratore (la riga migrata dal profilo PIERO, `is_admin=1`) e quello
del primo cliente — firmati con la formula vera del servizio, come in
`test_schermate_accesso.py`. Dopo il flusso si verifica SUL DATABASE che le
decisioni esistano: outcome, stato degli utenti, righe di `admin_audit`.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE))

import main  # noqa: E402 - dopo l'inserimento del percorso
from tests.ambiente import ambiente_di_supporto  # noqa: E402
from tests.relay.test_login import AMBIENTE_DEL_SERVIZIO, BOT_FINTO  # noqa: E402
from tests.runtime import esigi_browser  # noqa: E402
from tests.servizio import relay_avviato  # noqa: E402

esigi_browser()

# La stessa formula del servizio, ricalcolata e non importata (vedi
# tests/relay/test_login.py per il motivo).
SEGRETO_ATTESO = hashlib.sha256(('betrelay-sessione-v1:' + BOT_FINTO).encode()).hexdigest()

GIORNI_CONCESSI = 30


def test_il_pannello_richieste_decide_davvero(tmp_path, monkeypatch):
    """Elenco, Attiva col campo libero (avviso fallito VISIBILE), Rifiuta,
    promemoria, e il cliente approvato che vede i giorni. Poi il database."""
    monkeypatch.setattr(main, 'SEGRETO_SESSIONE', SEGRETO_ATTESO)
    ambiente = dict(AMBIENTE_DEL_SERVIZIO, TELEGRAM_BOT_USERNAME='BetRelayBot')
    with relay_avviato(tmp_path, **ambiente) as base:
        # `migra()` gira alla prima richiesta che tocca il database (vedi
        # test_schermate_accesso.py): una chiamata a vuoto completa lo schema.
        try:
            urllib.request.urlopen(f'{base}/feed/inesistente.csv?token=x', timeout=10)  # noqa: S310
        except urllib.error.HTTPError:
            pass

        percorso_db = tmp_path / 'signals.db'
        c = sqlite3.connect(percorso_db)
        riga = c.execute("SELECT id FROM users WHERE origin_profile=?",
                         (main.PIERO_PROFILE,)).fetchone()
        assert riga, 'la riga migrata del proprietario non esiste'
        admin = riga[0]
        clienti = {}
        for nome in ('ClienteUno', 'ClienteDue'):
            cur = c.execute(
                "INSERT INTO users(first_name, status) VALUES (?, 'in_attesa')", (nome,))
            clienti[nome] = cur.lastrowid
            c.execute('INSERT INTO access_requests(user_id) VALUES (?)', (cur.lastrowid,))
        c.commit()
        c.close()

        dati = {'nome_cookie': main.NOME_COOKIE,
                'admin_cookie': main.firma_sessione(admin, 1),
                'cliente_cookie': main.firma_sessione(clienti['ClienteUno'], 1),
                'nome_uno': 'ClienteUno', 'nome_due': 'ClienteDue',
                'giorni': GIORNI_CONCESSI}
        percorso_dati = tmp_path / 'dati.json'
        percorso_dati.write_text(json.dumps(dati), encoding='utf-8')
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).with_name('pannello_admin_flow.py')),
             f'{base}/app/', str(tmp_path / 'shots'), str(percorso_dati)],
            cwd=RADICE, capture_output=True, text=True, timeout=300,
            env=ambiente_di_supporto(PYTHONUNBUFFERED='1'),
        )
        if proc.returncode != 0:
            raise AssertionError(
                f'pannello_admin_flow.py fallito (exit {proc.returncode})\n'
                f'--- stdout ---\n{proc.stdout[-4000:]}\n'
                f'--- stderr ---\n{proc.stderr[-4000:]}'
            )

        # Il database racconta le decisioni, non solo la UI.
        c = sqlite3.connect(percorso_db)
        esiti = dict(c.execute(
            'SELECT u.first_name, r.outcome FROM access_requests r'
            ' JOIN users u ON u.id = r.user_id').fetchall())
        stati = dict(c.execute(
            'SELECT first_name, status FROM users WHERE first_name IN (?,?)',
            ('ClienteUno', 'ClienteDue')).fetchall())
        azioni = [r[0] for r in c.execute('SELECT action FROM admin_audit').fetchall()]
        scadenza = c.execute('SELECT access_expires_at FROM users WHERE id=?',
                             (clienti['ClienteUno'],)).fetchone()[0]
        c.close()
        assert esiti == {'ClienteUno': 'approvata', 'ClienteDue': 'rifiutata'}, esiti
        assert stati == {'ClienteUno': 'attivo', 'ClienteDue': 'registrato'}, stati
        assert 'accesso_approvato' in azioni and 'accesso_rifiutato' in azioni, azioni
        assert 'promemoria_inviati' in azioni, azioni
        assert main.giorni_rimasti(scadenza) == GIORNI_CONCESSI, scadenza
