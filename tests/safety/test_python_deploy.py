"""Il runtime su cui il servizio si DEPLOYA non deve stare sotto quello che i test
esercitano — e in particolare non sotto Python 3.11.

`sqlite3.Connection.serialize()` e' comparso in Python 3.11. Il backup del database
(#56) lo usa in `copia_backup_db()`: su un interprete piu' vecchio la rotta
`GET /api/admin/backup` fallirebbe **solo in produzione**, al primo download, senza
che nessun test lo mostri — la CI gira su 3.11 e non se ne accorgerebbe.

Railway/Nixpacks sceglie l'interprete dal file `.python-version` in radice. Senza quel
pin la build puo' derivare a una versione qualunque. Questa guardia impone due cose:

1. il pin esiste;
2. non e' sotto la versione minima provata dalla CI (`.github/workflows/test.yml`) e
   comunque non sotto 3.11 — cosi' «testato» e «deployato» non divergono in silenzio.

La versione della CI e' la fonte unica del numero (regola 3): la si legge dalla matrice
del workflow, non la si ricopia qui.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE))

PIN = RADICE / '.python-version'
WORKFLOW = RADICE / '.github' / 'workflows' / 'test.yml'
# sqlite3.Connection.serialize(), usato da copia_backup_db() (#56), esiste da 3.11.
MINIMO_SERIALIZE = (3, 11)


def _versione(testo: str) -> tuple[int, int]:
    """Il primo `major.minor` nel testo, come coppia di interi da confrontare."""
    m = re.search(r'(\d+)\.(\d+)', testo)
    assert m, f'non trovo una versione major.minor in {testo!r}'
    return (int(m.group(1)), int(m.group(2)))


def _versioni_ci() -> list[tuple[int, int]]:
    """Le versioni Python della matrice della CI, dalla STRUTTURA del workflow.

    Lette dal YAML e non dal testo: un commento che nomina una versione non conta,
    e la fonte del numero resta una sola — il workflow che i test li esegue davvero.
    """
    yaml = pytest.importorskip('yaml', reason='pyyaml non installato')
    dati = yaml.safe_load(WORKFLOW.read_text(encoding='utf-8'))
    versioni: list[tuple[int, int]] = []
    for job in (dati.get('jobs') or {}).values():
        matrice = (job.get('strategy') or {}).get('matrix') or {}
        for chiave, valore in matrice.items():
            if 'python' in str(chiave).lower():
                elenco = valore if isinstance(valore, list) else [valore]
                versioni += [_versione(str(v)) for v in elenco]
    return versioni


def test_esiste_il_pin_di_deploy():
    """Senza `.python-version` Nixpacks sceglie l'interprete da solo."""
    assert PIN.is_file(), (
        '.python-version manca in radice: Railway/Nixpacks sceglierebbe l-interprete '
        'da solo e la build potrebbe derivare sotto 3.11, dove serialize() del backup '
        '(#56) non esiste e la rotta /api/admin/backup fallirebbe in produzione'
    )


def test_il_deploy_non_gira_sotto_la_python_testata():
    """Il pin non e' sotto la versione minima provata dalla CI, ne' sotto 3.11.

    Il primo confronto tiene allineati deploy e test (regola 3, fonte unica: la CI);
    il secondo e' il vincolo concreto che nasce dal backup — serialize() richiede 3.11.
    """
    pin = _versione(PIN.read_text(encoding='utf-8'))
    ci = _versioni_ci()
    assert ci, (
        'non trovo la matrice python nella CI: senza il minimo testato il confronto '
        'perderebbe senso'
    )
    minimo_testato = min(ci)
    assert pin >= minimo_testato, (
        f'il deploy e- pinnato a {pin[0]}.{pin[1]} ma la CI prova da '
        f'{minimo_testato[0]}.{minimo_testato[1]}: si spedirebbe una Python mai testata'
    )
    assert pin >= MINIMO_SERIALIZE, (
        f'il deploy e- pinnato a {pin[0]}.{pin[1]} < 3.11: serialize() del backup #56 '
        'non esiste, e /api/admin/backup fallirebbe in produzione al primo download'
    )
