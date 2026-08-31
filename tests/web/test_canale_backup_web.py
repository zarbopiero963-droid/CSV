"""Avvia il relay e pilota la card «Canale di backup» del pannello admin (#56 pezzo 2).

Il lavoro browser lo fa `canale_backup_flow.py`; qui si prepara il mondo: il cookie
dell'amministratore (la riga migrata dal profilo PIERO, `is_admin=1`), firmato con la formula
vera del servizio come in `test_pannello_admin.py`. Le rotte del canale sono stubbate nel
flusso — il backend vero e' gia' coperto da `tests/relay/test_canale_backup.py`.
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

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE))

import main  # noqa: E402 - dopo l'inserimento del percorso
from tests.ambiente import ambiente_di_supporto  # noqa: E402
from tests.relay.test_login import AMBIENTE_DEL_SERVIZIO, BOT_FINTO  # noqa: E402
from tests.runtime import esigi_browser  # noqa: E402
from tests.servizio import relay_avviato  # noqa: E402

esigi_browser()

# La stessa formula del servizio, ricalcolata e non importata (vedi tests/relay/test_login.py).
SEGRETO_ATTESO = hashlib.sha256(('betrelay-sessione-v1:' + BOT_FINTO).encode()).hexdigest()


def test_la_card_canale_backup_pilotata_dal_browser(tmp_path, monkeypatch):
    """La card mostra candidato → configurato → vuoto, la conferma manda l'chat_id mostrato,
    prova e rimozione funzionano, zero errori in console."""
    monkeypatch.setattr(main, 'SEGRETO_SESSIONE', SEGRETO_ATTESO)
    ambiente = dict(AMBIENTE_DEL_SERVIZIO, TELEGRAM_BOT_USERNAME='BetRelayBot')
    with relay_avviato(tmp_path, **ambiente) as base:
        # `migra()` gira alla prima richiesta che tocca il database: una chiamata a vuoto
        # completa lo schema (come in test_pannello_admin.py).
        try:
            urllib.request.urlopen(f'{base}/feed/inesistente.csv?token=x', timeout=10)  # noqa: S310
        except urllib.error.HTTPError:
            pass

        c = sqlite3.connect(tmp_path / 'signals.db')
        riga = c.execute("SELECT id FROM users WHERE origin_profile=?",
                         (main.PIERO_PROFILE,)).fetchone()
        c.close()
        assert riga, 'la riga migrata del proprietario non esiste'
        dati = {'nome_cookie': main.NOME_COOKIE,
                'admin_cookie': main.firma_sessione(riga[0], 1)}
        percorso_dati = tmp_path / 'dati.json'
        percorso_dati.write_text(json.dumps(dati), encoding='utf-8')

        proc = subprocess.run(
            [sys.executable, str(Path(__file__).with_name('canale_backup_flow.py')),
             f'{base}/app/', str(tmp_path / 'shots'), str(percorso_dati)],
            cwd=RADICE, capture_output=True, text=True, timeout=300,
            env=ambiente_di_supporto(PYTHONUNBUFFERED='1'),
        )
        if proc.returncode != 0:
            raise AssertionError(
                f'canale_backup_flow.py fallito (exit {proc.returncode})\n'
                f'--- stdout ---\n{proc.stdout[-4000:]}\n'
                f'--- stderr ---\n{proc.stderr[-4000:]}'
            )
