"""Avviare il relay in un sottoprocesso: fonte unica delle fixture che lo fanno.

Prima esistevano tre copie quasi identiche — `tests/relay/test_csv_contract.py`,
`tests/relay/test_autenticazione.py`, `tests/web/test_prototype_flow.py` — e
portavano tutte e tre lo stesso difetto, che e' il motivo per cui questo file
esiste invece di essere una pulizia estetica (regola 3, e la duplicazione l'ho
introdotta io aggiungendo la terza copia).

**Il difetto: `stdout=PIPE` mai letta.** Sul percorso di successo nessuno legge
quella pipe. Se uvicorn scrive piu' del buffer del kernel, il figlio si blocca in
scrittura e i test si appendono — non falliscono, restano fermi. Con
`--log-level warning` uvicorn scrive poco, quindi non e' mai capitato: e'
esattamente la forma di guasto che non si manifesta finche' non si manifesta
male. Segnalato da CodeRabbit. Qui l'output va su un FILE: non si blocca mai, e
il messaggio d'errore all'avvio resta leggibile perche' il file si rilegge.

Due dettagli minori dello spegnimento, dalla stessa segnalazione: `wait()` dopo
`kill()` — senza, il figlio resta zombie — e la chiusura dell'handle del log.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

RADICE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RADICE))

from tests.ambiente import ambiente_di_servizio  # noqa: E402
from tests.dati import semina_produzione  # noqa: E402

# Quanto aspettare che il servizio risponda, e ogni quanto richiedere.
ATTESA_AVVIO_S = 30
INTERVALLO_S = 0.2


def porta_libera() -> int:
    """Una porta libera sul loopback.

    C'e' una finestra fra la chiusura del socket e il bind di uvicorn in cui un
    altro processo potrebbe prenderla. Il guasto che ne segue e' RUMOROSO — la
    fixture fallisce con l'output di uvicorn, che dice «address already in use» —
    quindi non si tenta di chiuderla: un retry silenzioso nasconderebbe anche i
    casi in cui la porta e' occupata per un motivo vero. Segnalato da CodeRabbit
    come rischio di intermittenza, e accettato come tale.
    """
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@contextmanager
def relay_avviato(cartella: Path, chat_ids='', vergine=False, **extra):
    """Avvia `main:app` su una porta libera; restituisce l'URL di base.

    `cartella` e' una directory di lavoro (tipicamente da `tmp_path_factory`):
    ci finiscono il database del test e il log di uvicorn.

    `extra` passa variabili d'ambiente al servizio. `DB_PATH` viene imposto qui
    perche' senza il relay scriverebbe in `/tmp/signals.db`, il database di
    default, e i test si lascerebbero segnali dietro. Le altre variabili
    pericolose non arrivano per eredita': le toglie `ambiente_di_servizio`, e
    passarne una di proposito resta possibile ma va scritto dal chiamante.

    Il database nasce con i **dati della produzione esistente** — il parser
    storico e il profilo PIERO — scritti da `tests.dati.semina_produzione`
    PRIMA dell'avvio. Prima ce li metteva il seme di `migra()`, rimosso col
    lavoro E della #25: da allora quelle righe sono dati, e un test che modella
    il servizio reale deve portarseli come se li porta il volume di Railway.
    `vergine=True` per chi invece vuole misurare un deploy nuovo, dove non deve
    esserci niente.
    """
    porta = porta_libera()
    db = cartella / 'signals.db'
    log = cartella / 'uvicorn.log'
    extra.setdefault('DB_PATH', str(db))
    if not vergine:
        semina_produzione(extra['DB_PATH'], chat_ids)
    with log.open('w', encoding='utf-8') as uscita:
        proc = subprocess.Popen(  # noqa: S603 - comando fisso, nessun input esterno
            [sys.executable, '-m', 'uvicorn', 'main:app', '--host', '127.0.0.1',
             '--port', str(porta), '--log-level', 'warning'],
            cwd=RADICE, env=ambiente_di_servizio(**extra),
            stdout=uscita, stderr=subprocess.STDOUT, text=True,
        )
        base = f'http://127.0.0.1:{porta}'
        try:
            _attendi(proc, base, log)
            yield base
        finally:
            _spegni(proc)


def _attendi(proc, base: str, log: Path) -> None:
    """Aspetta che `/health` risponda, o solleva col motivo.

    La sonda e' `/health` perche' e' l'unica rotta che risponde 200 in ogni
    configurazione: dal fail-closed di `auth()`, un servizio senza
    `CSV_ACCESS_TOKEN` risponde 503 sul feed, quindi sondare quello renderebbe
    impossibile avviare deliberatamente un servizio mal configurato — che e'
    proprio cio' che i test dell'autenticazione devono fare.
    """
    scaduto = time.monotonic() + ATTESA_AVVIO_S
    while time.monotonic() < scaduto:
        if proc.poll() is not None:
            raise RuntimeError(
                'uvicorn e\' morto durante l\'avvio:\n'
                + log.read_text(encoding='utf-8', errors='replace')[-2000:]
            )
        try:
            with urllib.request.urlopen(f'{base}/health', timeout=1) as r:  # noqa: S310 - loopback
                if r.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            time.sleep(INTERVALLO_S)
    _spegni(proc)
    raise RuntimeError(
        f'uvicorn non ha risposto su /health entro {ATTESA_AVVIO_S} s:\n'
        + log.read_text(encoding='utf-8', errors='replace')[-2000:]
    )


def _spegni(proc) -> None:
    """Spegne il processo, e non lascia nulla dietro in nessun caso.

    Esiste come funzione perche' serviva in DUE punti — l'uscita normale e il
    timeout d'avvio — e nella prima versione solo il primo aveva il `kill()` di
    riserva. Nel secondo un `wait()` che scade avrebbe sollevato
    `TimeoutExpired` **al posto** del `RuntimeError` che spiega il guasto, e
    lasciato il processo vivo: l'errore utile sostituito da uno inutile, piu' un
    uvicorn appeso. Segnalato da GPT-5.5, ed e- la stessa classe che avevo appena
    corretto nell'altro ramo (regola 2: la classe, non il sito).
    """
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
