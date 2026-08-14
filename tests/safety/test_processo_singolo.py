"""Il servizio gira in UN processo: c'e' codice che ci conta, quindi si vincola.

Il dedup delle consegne simultanee (`_CONSEGNE_IN_VOLO` in `main.py`, PR #44) e
il freno dei tentativi password (`_TENTATIVI_PASSWORD`, PR #23) vivono in
memoria: valgono per processo. Con `--workers N` o repliche, due processi non
vedono l'uno il set dell'altro e le corse cross-process riappaiono in silenzio.

Fino a questo file il vincolo era una frase nei commenti («Procfile senza
--workers, misurato»): una frase non diventa rossa quando qualcuno aggiunge
`--workers 4` per far prima. Questa guardia si'. Suggerito da Claude Fable 5
sulla PR #44.

Se un giorno servisse scalare, questo test e' l'ELENCO di cosa va spostato su
una base condivisa prima di farlo — non un divieto eterno.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE))


def _comando_di_avvio():
    testo = (RADICE / 'Procfile').read_text(encoding='utf-8')
    return testo.strip()


def test_il_procfile_non_avvia_piu_processi():
    comando = _comando_di_avvio()
    assert '--workers' not in comando and '-w ' not in comando, (
        f'il Procfile avvia piu\' worker: {comando!r}. Il dedup delle consegne '
        'simultanee e il freno password sono PER PROCESSO — prima di scalare '
        'vanno spostati su una base condivisa (vedi il docstring di questo file)')


def test_railway_json_non_scavalca_il_procfile_con_piu_worker():
    """`railway.json` puo' portare un suo startCommand: stessa regola anche la'."""
    percorso = RADICE / 'railway.json'
    if not percorso.is_file():
        return
    testo = percorso.read_text(encoding='utf-8')
    assert not re.search(r'--workers|\-w\s+\d', testo), (
        f'railway.json configura piu\' worker: il dedup in memoria non regge '
        'processi multipli')
