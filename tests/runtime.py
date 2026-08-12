"""I runtime esterni dei test — node e Chromium — e la regola che vieta di saltarli.

Due problemi, e vivono insieme perche' sono lo stesso problema visto da due lati.

**Il primo: cinque copie dello stesso percorso.** Il binario di Chromium era
scritto a mano in `tests/web/mobile_layout.py`, `prototype_flow.py`,
`sito_flow.py`, `test_prototype_flow.py` e `test_sito.py` — e due di quelle le ho
aggiunte io con la facciata. Cinque copie corrette oggi sono cinque copie
divergenti domani (regola 3), e il percorso contiene un numero di versione
(`chromium-1194`) che cambia a ogni aggiornamento dell'immagine.

**Il secondo, piu' grave: uno skip e' indistinguibile da un successo.** Il
riepilogo di pytest dice `229 passed, 5 skipped` e il processo esce 0. In locale va
bene: chi non ha Chromium non puo' eseguire i test browser, e CLAUDE.md vieta di
dichiarare coperto cio' che non e' stato eseguito, quindi lo skip **con motivo
scritto** e' la scelta onesta.

In CI e' l'opposto. Un workflow che installa Chromium, non ci riesce, salta i test
browser ed esce verde e' esattamente il difetto che i tre workflow di review
avevano prima della PR #16: un check verde che non prova niente. E si nota ancora
meno, perche' nessuno legge il conteggio degli skip di una CI che passa.

Da qui la **modalita' severa**: con `TEST_RUNTIME_OBBLIGATORIO=1` nell'ambiente,
un runtime mancante non fa saltare il test — lo fa **fallire**, dicendo quale
manca. La CI la impone; in locale resta spenta.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

# La variabile che accende la modalita' severa. Il nome sta qui e non nel workflow
# perche' il workflow e' un consumatore: la guardia
# `tests/safety/test_ci.py` verifica che il valore nel YAML sia questo.
VARIABILE_SEVERA = 'TEST_RUNTIME_OBBLIGATORIO'

# Il Chromium preinstallato in questo ambiente di sviluppo. Pinnato perche'
# Playwright cerca `chromium_headless_shell-<altra versione>`, che qui non
# esiste: senza percorso esplicito `launch()` fallisce. In CI invece Chromium lo
# installa Playwright, quindi questa cartella non c'e' e si usa il suo default —
# ed e' giusto che sia cosi', perche' un percorso pinnato in CI vincolerebbe la
# versione all'immagine di un'altra macchina.
CHROMIUM_PINNATO = Path('/opt/pw-browsers/chromium-1194/chrome-linux/chrome')


def severa() -> bool:
    """Vero se un runtime mancante deve far FALLIRE invece di saltare."""
    return os.environ.get(VARIABILE_SEVERA, '') not in ('', '0', 'false', 'no')


def _manca(cosa: str, dettaglio: str, livello_modulo: bool = False):
    """Fallisce in modalita' severa, salta altrimenti. Il motivo e' lo stesso testo.

    Un solo punto in cui si decide, perche' la decisione e' una: se il messaggio
    fosse duplicato fra il ramo che salta e quello che falseisce, uno dei due
    diventerebbe meno preciso dell'altro alla prima modifica.

    `livello_modulo` va passato quando la chiamata avviene durante l'import di un
    file di test e non dentro una funzione. **Non e' un dettaglio di forma:**
    `pytest.skip()` fuori da un test, senza `allow_module_level=True`, non salta —
    solleva, e pytest lo tratta come errore di RACCOLTA. Misurato:

        ERROR collecting test_livello_modulo.py
        Using pytest.skip outside of a test will skip the entire module...
        !!!! Interrupted: 1 error during collection !!!!

    Cioe' su una macchina senza Chromium l'intera suite si **interrompe** invece
    di saltare cinque test: una regressione rispetto al `pytest.mark.skipif` che
    questa funzione ha sostituito. Il difetto e' sopravvissuto ai miei test perche'
    qui Chromium c'e' sempre e questo ramo non veniva mai percorso. Segnalato da
    GPT-5.5 e Claude Fable 5 sulla PR #18, indipendentemente.

    In modalita' severa `pytest.fail` a livello di modulo diventa anch'esso un
    errore di raccolta — con il messaggio in chiaro, quindi rosso e leggibile, che
    e' l'unica cosa che quel ramo deve garantire.
    """
    motivo = f'{cosa} non disponibile: {dettaglio}'
    if severa():
        pytest.fail(
            f'{motivo}\n'
            f'{VARIABILE_SEVERA} e- impostata, quindi questo test NON puo- essere '
            f'saltato: una CI che salta i test dei runtime esterni esce verde '
            f'senza aver eseguito niente. Installa il runtime o togli la variabile.'
        )
    pytest.skip(motivo, allow_module_level=livello_modulo)


def percorso_chromium() -> str | None:
    """Il binario da passare a `launch(executable_path=...)`, o `None`.

    `None` significa «lascia decidere a Playwright», che e' il caso della CI dove
    il browser lo ha installato lui. Non e' un fallimento: e' l'assenza del
    pinnaggio locale.
    """
    return str(CHROMIUM_PINNATO) if CHROMIUM_PINNATO.is_file() else None


def apri_chromium(playwright_sync, **extra):
    """Avvia Chromium, col binario pinnato se c'e', col suo se no.

    Fonte unica dell'avvio del browser: prima ogni script lo apriva da se' con il
    percorso ricopiato a mano.
    """
    percorso = percorso_chromium()
    if percorso:
        return playwright_sync.chromium.launch(executable_path=percorso, **extra)
    return playwright_sync.chromium.launch(**extra)


def esigi_browser() -> None:
    """Da chiamare a livello di modulo nei test che pilotano un browser.

    Salta (o fallisce, in modalita' severa) se Playwright non e' installato o se
    nessun Chromium e' raggiungibile. «Raggiungibile» non vuol dire «pinnato»:
    senza il pinnaggio si prova ad avviare quello di Playwright, perche' l'unico
    modo di sapere se un browser c'e' e' aprirlo.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _manca('playwright', 'il pacchetto non e\' installato', livello_modulo=True)
        return

    if percorso_chromium():
        return

    try:
        with sync_playwright() as pw:
            pw.chromium.launch().close()
    except Exception as e:
        _manca('Chromium', f'ne- pinnato in {CHROMIUM_PINNATO} ne- avviabile da '
                           f'Playwright ({type(e).__name__})', livello_modulo=True)


def esigi_node() -> str:
    """Il percorso di `node`, o salta/fallisce. Serve ai casi del motore JS."""
    node = shutil.which('node')
    if node is None:
        _manca('node', 'il motore JS di `web/engine.js` non e\' eseguibile')
    return node
