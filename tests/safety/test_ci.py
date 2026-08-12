"""Guardia sul workflow che esegue i test: che li esegua, e che non possa mentire.

Fino a questa PR `.github/workflows/` conteneva solo i tre reviewer AI, e nessun
workflow eseguiva `pytest`. Il limite era dichiarato in `CLAUDE.md`, ed era
onesto — ma significava che un check verde su una PR non diceva niente sui test.

Il difetto da evitare adesso e' peggiore dell'assenza, ed e' lo stesso che la PR
#16 ha chiuso sui reviewer: **una CI che esce verde senza aver eseguito.** Basta
che l'installazione di Chromium non riesca, o che node manchi, e pytest riporta
`229 passed, 5 skipped`, esce 0, e la spunta verde compare identica. Nessuno legge
il conteggio degli skip di una CI che passa.

Da qui i controlli di questo file. Non verificano che il YAML sia *scritto* in un
certo modo: verificano le tre proprieta' da cui dipende il fatto che quella spunta
significhi qualcosa.

1. il workflow esegue davvero `pytest` sull'intera suite;
2. impone `TEST_RUNTIME_OBBLIGATORIO`, cioe' vieta a se stesso di saltare i test
   dei runtime esterni — e il nome della variabile e' letto da `tests/runtime.py`,
   non ricopiato qui, o le due copie divergerebbero in silenzio (regola 3);
3. installa il browser che quei test pretendono, altrimenti il punto 2 lo fa
   fallire sempre invece che passare davvero;
4. non riceve **nessun** Secret: la suite non ne ha bisogno, e un token del bot
   nell'ambiente farebbe registrare il webhook di produzione verso `PUBLIC_URL`
   (e' la ragione per cui esiste `tests/ambiente.py`).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE))

from tests.runtime import VARIABILE_SEVERA  # noqa: E402

WORKFLOW = RADICE / '.github' / 'workflows' / 'test.yml'


@pytest.fixture(scope='module')
def testo() -> str:
    assert WORKFLOW.is_file(), (
        f'{WORKFLOW.relative_to(RADICE)} non esiste: nessun workflow esegue i test, '
        'quindi un check verde su una PR non dice niente sul loro esito'
    )
    return WORKFLOW.read_text(encoding='utf-8')


@pytest.fixture(scope='module')
def dati(testo):
    """Il workflow come struttura, non come stringa.

    `yaml` e' una dipendenza dei soli test. Se manca, questo file salta con motivo
    scritto invece di fingere di aver controllato.
    """
    yaml = pytest.importorskip('yaml', reason='pyyaml non installato')
    return yaml.safe_load(testo)


def _comandi(dati):
    """Tutti i `run:` del workflow, dalla struttura.

    Letti dal YAML e non dal testo: cosi' un commento che nomina un comando non
    conta come comando, e un cambio di indentazione non rompe la guardia. La prima
    versione scansionava le righe e contava anche il nome del job e i commenti —
    Sourcery ha segnalato la fragilita', e sabotando l'invocazione l'avevo trovata
    prima: `pytest tests/relay` la lasciava verde.
    """
    fuori = []
    for job in (dati.get('jobs') or {}).values():
        for passo in job.get('steps') or []:
            if passo.get('run'):
                fuori.append(passo['run'])
    return fuori


def test_il_workflow_esegue_pytest(dati):
    """Il minimo: che il comando ci sia, e sull'intera suite.

    Un `pytest tests/relay` verificherebbe un quarto della suite lasciando la
    spunta identica, e questo controllo esiste perche' quella differenza non si
    vede guardando il colore del check.
    """
    comandi = [c for c in _comandi(dati) if 'pytest' in c]
    assert comandi, 'il workflow non esegue pytest da nessuna parte'

    interi = [c for c in comandi if 'tests/' not in c]
    assert interi, (
        'pytest e- invocato solo su sottocartelle: verificherebbe una parte della '
        f'suite lasciando la spunta verde identica. Comandi trovati: {comandi}'
    )


def _passi_con_pytest(dati):
    """I passi che eseguono pytest, con l'ambiente che vale per ognuno.

    L'ambiente e' quello del passo unito a quello del job e del workflow, perche'
    GitHub li sovrappone in quest'ordine e la variabile puo' stare in uno
    qualunque dei tre.
    """
    trovati = []
    globale = dati.get('env') or {}
    for job in (dati.get('jobs') or {}).values():
        del_job = job.get('env') or {}
        for passo in job.get('steps') or []:
            comando = passo.get('run') or ''
            if 'pytest' in comando:
                ambiente = {**globale, **del_job, **(passo.get('env') or {})}
                trovati.append((comando, ambiente))
    return trovati


def test_il_workflow_VIETA_a_se_stesso_di_saltare_i_runtime(dati):
    """Il controllo che conta: la modalita' severa e' imposta sul passo giusto.

    Senza, un guasto nell'installazione del browser non rende rossa la CI — la
    rende verde con cinque test in meno, che e' il modo peggiore di fallire.

    **Letto dalla STRUTTURA, non dal testo**, e la differenza e' stata misurata.
    La prima versione faceva `VARIABILE_SEVERA in testo` e restava verde dopo aver
    tolto la variabile dal passo: il commento in cima al workflow la nomina, e
    quello bastava a soddisfare la ricerca. Una guardia ingannata dalla propria
    documentazione e' peggio di nessuna guardia, perche' la si crede.
    """
    passi = _passi_con_pytest(dati)
    assert passi, 'nessun passo del workflow esegue pytest'
    for comando, ambiente in passi:
        valore = str(ambiente.get(VARIABILE_SEVERA, ''))
        assert valore not in ('', '0', 'false', 'no'), (
            f'il passo `{comando.strip()}` non imposta {VARIABILE_SEVERA}: uno skip '
            'per runtime mancante lo lascerebbe verde senza aver eseguito i test '
            f'browser. Ambiente visto dal passo: {sorted(ambiente)}'
        )


def test_il_workflow_installa_il_browser_che_i_test_pretendono(dati):
    """Punto 3, e senza di lui il punto 2 renderebbe la CI rossa per sempre.

    Il percorso pinnato di `tests/runtime.py` e' dell'immagine locale: in CI non
    esiste, quindi il browser va installato, o `esigi_browser()` fallisce in
    modalita' severa a ogni esecuzione.
    """
    installa = [c for c in _comandi(dati) if 'playwright install' in c]
    assert installa, \
        'il workflow non installa Chromium: i test browser fallirebbero sempre'
    assert any('chromium' in c for c in installa), \
        f'l-installazione non nomina chromium: {installa}'


def test_il_workflow_NON_riceve_segreti(dati):
    """Nessun `secrets.` nel file, e nessun permesso di scrittura.

    La suite non usa segreti. Uno qui non servirebbe a nulla e potrebbe fare
    danno: con `TELEGRAM_BOT_TOKEN` nell'ambiente, avviare il servizio chiama
    `setWebhook` verso `PUBLIC_URL` — cioe' **ripunterebbe il webhook del bot
    vero** dal runner della CI. E' lo stesso difetto da cui nasce la whitelist di
    `tests/ambiente.py`, un livello piu' in alto.
    """
    testo_intero = WORKFLOW.read_text(encoding='utf-8')
    assert 'secrets.' not in testo_intero, (
        'il workflow dei test riferisce un Secret: la suite non ne usa, e un '
        'TELEGRAM_BOT_TOKEN nell-ambiente ripunterebbe il webhook di produzione'
    )
    permessi = dati.get('permissions')
    assert permessi is not None, 'il workflow non dichiara `permissions`'
    for chiave, valore in (permessi or {}).items():
        assert valore == 'read', \
            f'permesso di scrittura non necessario: {chiave}: {valore}'


def test_il_workflow_installa_le_dipendenze_DEI_TEST(dati):
    """`requirements.txt` non basta: pytest e playwright stanno in quello dei test.

    Senza, la CI fallirebbe all'import — rosso onesto, ma per il motivo sbagliato,
    e chi lo legge perde tempo su un guasto di configurazione credendo a un test.
    """
    assert any('requirements-dev.txt' in c for c in _comandi(dati)), (
        'il workflow non installa requirements-dev.txt: pytest e playwright stanno '
        'la- dentro, e senza la CI fallirebbe all-import'
    )


def test_il_workflow_parte_sulle_PR_e_sui_push(dati):
    """Un workflow che non si attiva e' una CI che non esiste.

    `on:` con la sola `workflow_dispatch` darebbe un file che sembra una CI e non
    gira mai: nessun check comparirebbe sulle PR, e l'assenza di un check e' molto
    piu' facile da non notare di un check rosso.
    """
    # In YAML `on:` senza virgolette diventa la chiave booleana True.
    attivazione = dati.get('on', dati.get(True))
    assert attivazione, 'il workflow non dichiara quando parte'
    assert 'pull_request' in attivazione, 'il workflow non parte sulle pull request'
    assert 'push' in attivazione, 'il workflow non parte sui push'
