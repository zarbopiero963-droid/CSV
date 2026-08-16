"""Avvia il relay, semina gli stati dell'accesso e pilota le schermate (#7).

Il lavoro lo fa `accesso_flow.py`; qui si prepara il mondo: tre utenti nel
database del relay di test — un `registrato`, un `attivo` scaduto ieri, un
`attivo` a 3 giorni dalla scadenza — e le loro sessioni come cookie firmati
con la STESSA formula del servizio (il sottoprocesso deriva il segreto da
`TELEGRAM_BOT_TOKEN`, qui si firma in processo con `main.firma_sessione`
dopo aver impostato lo stesso segreto — pattern di `tests/relay/test_login.py`).

`TELEGRAM_BOT_USERNAME` e' nell'ambiente del relay perche' il deep link del
bot (`t.me/<bot>?start=...`) e' parte del contratto della schermata: senza
username il server risponde `bot: null` e la vista mostra l'istruzione
manuale — comportamento legittimo, ma non e' quello sotto test.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import time
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

# La stessa formula del servizio, ricalcolata e non importata: se la formula
# cambiasse solo da un lato, questo test diventa rosso invece di seguirla.
SEGRETO_ATTESO = hashlib.sha256(('betrelay-sessione-v1:' + BOT_FINTO).encode()).hexdigest()

GIORNO = 86400


def _semina_utente(percorso_db, nome, status, scadenza=None):
    c = sqlite3.connect(percorso_db)
    cur = c.execute(
        'INSERT INTO users(first_name, status, access_expires_at) VALUES (?,?,?)',
        (nome, status, scadenza))
    c.commit()
    utente = cur.lastrowid
    c.close()
    return utente


def test_le_schermate_degli_stati_di_accesso(tmp_path, monkeypatch):
    """registrato → richiedi → attesa col deep link; scaduto → richiedi di
    nuovo; attivo a 3 giorni → dashboard normale con la pillola gialla."""
    monkeypatch.setattr(main, 'SEGRETO_SESSIONE', SEGRETO_ATTESO)
    ambiente = dict(AMBIENTE_DEL_SERVIZIO, TELEGRAM_BOT_USERNAME='BetRelayBot')
    with relay_avviato(tmp_path, **ambiente) as base:
        # `migra()` gira alla prima richiesta che tocca il database, non
        # all'avvio: prima di seminare gli utenti serve UNA richiesta vera, o
        # `users` non esiste ancora. Il feed con token sbagliato risponde 404
        # e come effetto collaterale completa lo schema — e' cio' che serve.
        import urllib.error
        import urllib.request
        try:
            urllib.request.urlopen(f'{base}/feed/inesistente.csv?token=x', timeout=10)  # noqa: S310
        except urllib.error.HTTPError:
            pass

        percorso_db = tmp_path / 'signals.db'
        adesso = int(time.time())
        casi = []
        for nome, status, scadenza, atteso in (
                ('Registrato', 'registrato', None, 'richiedi'),
                ('InAttesa', 'in_attesa', None, 'in_attesa'),
                ('Scaduto', 'attivo', adesso - GIORNO, 'scaduto'),
                ('Sospeso', 'sospeso', None, 'sospeso'),
                ('QuasiScaduto', 'attivo', adesso + 3 * GIORNO, 'dashboard_gialla'),
                # il bordo ESATTO della soglia: 5 giorni sono inclusi
                # (giorni_rimasti <= 5), chiesto da CodeRabbit sulla PR #52
                ('CinqueGiorni', 'attivo', adesso + 5 * GIORNO, 'dashboard_gialla')):
            utente = _semina_utente(percorso_db, nome, status, scadenza)
            casi.append({'nome': nome.lower(),
                         'cookie': main.firma_sessione(utente, 1),
                         'atteso': atteso})

        percorso_casi = tmp_path / 'casi.json'
        percorso_casi.write_text(json.dumps(casi), encoding='utf-8')
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).with_name('accesso_flow.py')),
             f'{base}/app/', str(tmp_path / 'shots'), str(percorso_casi)],
            cwd=RADICE, capture_output=True, text=True, timeout=300,
            env=ambiente_di_supporto(PYTHONUNBUFFERED='1'),
        )
        if proc.returncode != 0:
            raise AssertionError(
                f'accesso_flow.py fallito (exit {proc.returncode})\n'
                f'--- stdout ---\n{proc.stdout[-4000:]}\n'
                f'--- stderr ---\n{proc.stderr[-4000:]}'
            )

        # E il lato server e' cambiato davvero: la richiesta del registrato
        # esiste nel database, aperta, e il suo stato e' in_attesa.
        c = sqlite3.connect(percorso_db)
        righe = c.execute(
            "SELECT u.first_name, u.status FROM users u"
            " JOIN access_requests r ON r.user_id = u.id"
            " WHERE r.decided_at IS NULL").fetchall()
        c.close()
        nomi = sorted(r[0] for r in righe)
        assert nomi == ['Registrato', 'Scaduto'], (
            f'richieste aperte attese per Registrato e Scaduto, trovate: {righe}')
        assert all(r[1] == 'in_attesa' for r in righe), righe
