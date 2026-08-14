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


# Ogni forma con cui uvicorn/gunicorn accettano i worker: `--workers 4`,
# `--workers=4`, `-w 4`, `-w4`, `-w=4`. La prima versione cercava `'-w '` con lo
# spazio e `-w4` passava — segnalato da GPT-5.5: una guardia che non morde e'
# peggio di nessuna guardia, perche' si crede coperto cio' che non lo e'.
_OPZIONE_WORKER = re.compile(r'(--workers|-w)[=\s]*\d')


def test_il_procfile_non_avvia_piu_processi():
    comando = _comando_di_avvio()
    assert not _OPZIONE_WORKER.search(comando), (
        f'il Procfile avvia piu\' worker: {comando!r}. Il dedup delle consegne '
        'simultanee e il freno password sono PER PROCESSO — prima di scalare '
        'vanno spostati su una base condivisa (vedi il docstring di questo file)')


def test_la_guardia_morde_su_TUTTE_le_forme_dell_opzione():
    """La guardia sulle forme vere, inclusa quella che la prima versione mancava."""
    for forma in ('--workers 4', '--workers=4', '-w 4', '-w4', '-w=4'):
        assert _OPZIONE_WORKER.search(f'uvicorn main:app {forma}'), forma
    assert not _OPZIONE_WORKER.search('uvicorn main:app --host 0.0.0.0 --port 8000')


def test_railway_json_non_scavalca_il_procfile_con_piu_worker():
    """`railway.json` puo' portare un suo startCommand: stessa regola anche la'.

    Si guarda il `startCommand` del JSON, non il testo grezzo del file: sul
    testo intero un commento o una stringa non operativa darebbe falsi
    positivi (segnalato da GPT-5.5). Se il campo non c'e', comanda il Procfile.
    """
    import json as _json

    percorso = RADICE / 'railway.json'
    if not percorso.is_file():
        return
    dati = _json.loads(percorso.read_text(encoding='utf-8'))
    comandi = []

    def _raccogli(nodo):
        if isinstance(nodo, dict):
            for chiave, valore in nodo.items():
                if chiave == 'startCommand' and isinstance(valore, str):
                    comandi.append(valore)
                else:
                    _raccogli(valore)
        elif isinstance(nodo, list):
            for voce in nodo:
                _raccogli(voce)

    _raccogli(dati)
    for comando in comandi:
        assert not _OPZIONE_WORKER.search(comando), (
            f'railway.json avvia piu\' worker: {comando!r} — il dedup in memoria '
            'non regge processi multipli')
