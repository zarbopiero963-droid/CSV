"""Test del meccanismo su cui poggia l'onestà della CI.

`tests/runtime.py` decide una cosa sola, e da quella dipende se la spunta verde
della CI significhi qualcosa: **un runtime mancante salta o fallisce?** In locale
deve saltare — chi non ha Chromium non puo' eseguire i test browser, e CLAUDE.md
vieta di dichiarare coperto cio' che non e' stato eseguito. In CI deve fallire, o
un'installazione del browser andata male produce «229 passed, 5 skipped», exit 0, e
una spunta identica a quella di una suite completa.

Un meccanismo che sceglie fra «rosso» e «verde silenzioso» e non e' testato e' il
posto peggiore in cui avere un difetto: quando sbaglia, sbaglia verso il verde.

Qui si esercitano le funzioni reali, non la loro forma.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from _pytest.outcomes import Failed, Skipped

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE))

from tests import runtime  # noqa: E402

INESISTENTE = Path('/non/esiste/chromium')


# --------------------------------------------------------- lettura della variabile

@pytest.mark.parametrize('valore', ['', '0', 'false', 'no'])
def test_i_valori_che_NON_accendono_la_modalita_severa(monkeypatch, valore):
    """`0` e `false` devono spegnerla, non accenderla.

    Il caso limite che conta: `TEST_RUNTIME_OBBLIGATORIO=0` scritto per
    disattivarla. Una lettura ingenua — «la variabile e' presente» — la
    accenderebbe, e chi l'ha messa a zero otterrebbe l'opposto di quel che chiedeva.
    """
    monkeypatch.setenv(runtime.VARIABILE_SEVERA, valore)
    assert runtime.severa() is False


@pytest.mark.parametrize('valore', ['1', 'true', 'si', 'qualunque-cosa'])
def test_i_valori_che_la_accendono(monkeypatch, valore):
    monkeypatch.setenv(runtime.VARIABILE_SEVERA, valore)
    assert runtime.severa() is True


def test_senza_la_variabile_e_spenta(monkeypatch):
    """Il default e' il comportamento locale: si salta.

    Deve restare cosi': se il default fosse severo, l'intera suite diventerebbe
    rossa su qualunque macchina senza Chromium, e la reazione naturale sarebbe
    smettere di eseguirla.
    """
    monkeypatch.delenv(runtime.VARIABILE_SEVERA, raising=False)
    assert runtime.severa() is False


# ------------------------------------------------------- le due uscite di `_manca`

def test_in_locale_un_runtime_mancante_SALTA(monkeypatch):
    monkeypatch.delenv(runtime.VARIABILE_SEVERA, raising=False)
    with pytest.raises(Skipped) as esito:
        runtime._manca('cosa-finta', 'motivo di prova')
    assert 'cosa-finta' in str(esito.value)
    assert 'motivo di prova' in str(esito.value)


def test_in_CI_lo_stesso_runtime_mancante_FALLISCE(monkeypatch):
    """La riga per cui esiste tutto il resto.

    E il messaggio deve nominare la variabile: chi vede questo fallimento su un
    runner deve capire in una lettura che il test non e' rotto — manca un runtime,
    e la CI ha deciso di non nasconderlo.
    """
    monkeypatch.setenv(runtime.VARIABILE_SEVERA, '1')
    with pytest.raises(Failed) as esito:
        runtime._manca('cosa-finta', 'motivo di prova')
    messaggio = str(esito.value)
    assert 'cosa-finta' in messaggio
    assert runtime.VARIABILE_SEVERA in messaggio, \
        'il fallimento non dice quale variabile lo ha reso obbligatorio'


# ---------------------------------------------------------------- node e il browser

def test_esigi_node_restituisce_un_percorso_dove_node_esiste():
    """Non `True`: il percorso, perche' i chiamanti lo passano a `subprocess`.

    Se un giorno tornasse un booleano, `subprocess.run([True, ...])` fallirebbe
    lontano da qui con un errore che non nomina node (regola 2-bis: il valore di
    ritorno ha un consumatore).
    """
    if runtime.shutil.which('node') is None:
        pytest.skip('node non installato qui: questo caso non e\' esercitabile')
    percorso = runtime.esigi_node()
    assert isinstance(percorso, str) and Path(percorso).exists()


def test_senza_node_e_in_CI_esigi_node_FALLISCE(monkeypatch):
    monkeypatch.setenv(runtime.VARIABILE_SEVERA, '1')
    monkeypatch.setattr(runtime.shutil, 'which', lambda _: None)
    with pytest.raises(Failed):
        runtime.esigi_node()


def test_senza_chromium_pinnato_NE_avviabile_e_in_CI_FALLISCE(monkeypatch):
    """Il caso del runner con l'installazione del browser andata male.

    Si finge che il percorso pinnato non ci sia e che l'avvio di Playwright
    sollevi: e' quello che succede su una macchina dove `playwright install` non
    e' passato. In modalita' severa deve diventare rosso.
    """
    monkeypatch.setenv(runtime.VARIABILE_SEVERA, '1')
    monkeypatch.setattr(runtime, 'CHROMIUM_PINNATO', INESISTENTE)

    class ChromiumRotto:
        def launch(self, **_):
            raise RuntimeError('eseguibile non trovato')

    class ContestoFinto:
        chromium = ChromiumRotto()

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    import playwright.sync_api as vero
    monkeypatch.setattr(vero, 'sync_playwright', lambda: ContestoFinto())

    with pytest.raises(Failed) as esito:
        runtime.esigi_browser()
    assert 'Chromium' in str(esito.value)


# ----------------------------------------------- il ramo locale e il ramo della CI

class PlaywrightFinto:
    """Registra come `apri_chromium` ha chiamato `launch`, e niente altro."""

    def __init__(self):
        self.chiamate = []
        madre = self

        class Chromium:
            def launch(self, **kwargs):
                madre.chiamate.append(kwargs)
                return 'browser-finto'

        self.chromium = Chromium()


def test_col_pinnaggio_locale_passa_il_percorso(monkeypatch, tmp_path):
    finto_binario = tmp_path / 'chrome'
    finto_binario.write_text('non e- un eseguibile, basta che esista')
    monkeypatch.setattr(runtime, 'CHROMIUM_PINNATO', finto_binario)

    pw = PlaywrightFinto()
    assert runtime.apri_chromium(pw) == 'browser-finto'
    assert pw.chiamate == [{'executable_path': str(finto_binario)}]


def test_senza_pinnaggio_NON_passa_executable_path(monkeypatch):
    """Il ramo della CI, ed e' quello che nessuno noterebbe rompersi.

    In CI il browser lo installa Playwright, quindi il percorso pinnato non
    esiste e `launch()` va chiamata **senza** `executable_path`. Se il codice
    passasse `executable_path=None` oppure la stringa `'None'`, il guasto
    comparirebbe soltanto su un runner — cioe' dove non si sta guardando.
    """
    monkeypatch.setattr(runtime, 'CHROMIUM_PINNATO', INESISTENTE)

    pw = PlaywrightFinto()
    runtime.apri_chromium(pw)
    assert pw.chiamate == [{}], \
        f'in CI launch() non deve ricevere executable_path: ha ricevuto {pw.chiamate}'


def test_gli_argomenti_extra_arrivano_sempre(monkeypatch, tmp_path):
    """`apri_chromium(pw, headless=False)` deve funzionare in entrambi i rami.

    Due rami che accettano argomenti diversi sono due funzioni con lo stesso nome,
    e la divergenza si scoprirebbe solo nel ramo meno percorso.
    """
    finto = tmp_path / 'chrome'
    finto.write_text('x')
    for pinnato, atteso in ((finto, {'executable_path': str(finto)}), (INESISTENTE, {})):
        monkeypatch.setattr(runtime, 'CHROMIUM_PINNATO', pinnato)
        pw = PlaywrightFinto()
        runtime.apri_chromium(pw, headless=True)
        assert pw.chiamate == [{**atteso, 'headless': True}]
