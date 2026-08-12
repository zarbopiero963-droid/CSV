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

# `pytester` lancia un pytest FIGLIO: e' l'unico modo di sapere come pytest
# tratta una chiamata avvenuta durante l'import di un modulo di test.
pytest_plugins = ['pytester']

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
    """Qualunque valore diverso dai quattro spenti la accende, non solo `1`.

    Deliberato: chi scrive `true` o `si` intende accenderla, e una lettura che
    accettasse solo `1` gliela lascerebbe spenta senza dirlo.
    """
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
    """E il motivo arriva intero nello skip: il nome del runtime e il dettaglio.

    Uno skip senza motivo scritto e' vietato da CLAUDE.md, e sarebbe indistinguibile
    da un test disabilitato per pigrizia.
    """
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
    """`which` finto a `None`: su un runner senza node i 24 casi del motore JS
    devono diventare rossi, non sparire dal conteggio."""
    monkeypatch.setenv(runtime.VARIABILE_SEVERA, '1')
    monkeypatch.setattr(runtime.shutil, 'which', lambda _: None)
    with pytest.raises(Failed):
        runtime.esigi_node()


def test_senza_node_e_in_locale_esigi_node_SALTA(monkeypatch):
    """Il rovescio del precedente, chiesto da Sourcery e giustamente.

    Senza questo caso, una modifica che trasformasse lo skip locale in un
    fallimento passerebbe inosservata: l'unico test che c'era esercitava solo il
    ramo severo, e i due rami sono l'intero contratto di questa funzione.
    """
    monkeypatch.delenv(runtime.VARIABILE_SEVERA, raising=False)
    monkeypatch.setattr(runtime.shutil, 'which', lambda _: None)
    with pytest.raises(Skipped):
        runtime.esigi_node()


# ------------------------------------------------- il ramo dell'import mancante

def _import_rotto(monkeypatch):
    """Fa sollevare `ImportError` all'import di playwright, e solo a quello."""
    import builtins
    vero = builtins.__import__

    def finto(nome, *resto, **chiavi):
        """Import di rimpiazzo: solleva su playwright, delega su tutto il resto."""
        if nome.startswith('playwright'):
            raise ImportError('playwright non installato (finto)')
        return vero(nome, *resto, **chiavi)

    monkeypatch.setattr(builtins, '__import__', finto)


def test_senza_playwright_e_in_locale_esigi_browser_SALTA(monkeypatch):
    """Il primo dei due rami d'errore di `esigi_browser`, non coperto prima.

    Segnalato da Sourcery: i test coprivano «Chromium non avviabile» e non
    «Playwright non importabile», che sono due guasti diversi e producono due
    messaggi diversi. Qui conta anche che sia uno **skip a livello di modulo**
    valido, perche' e' esattamente da li' che la funzione viene chiamata.
    """
    monkeypatch.delenv(runtime.VARIABILE_SEVERA, raising=False)
    _import_rotto(monkeypatch)
    with pytest.raises(Skipped) as esito:
        runtime.esigi_browser()
    assert 'playwright' in str(esito.value)


def test_senza_playwright_e_in_CI_esigi_browser_FALLISCE(monkeypatch):
    """Il gemello severo del precedente, e il messaggio deve nominare la variabile.

    Chi legge questo fallimento su un runner deve capire in una riga che il test
    non e' rotto: manca un pacchetto, e la CI ha scelto di non nasconderlo.
    """
    monkeypatch.setenv(runtime.VARIABILE_SEVERA, '1')
    _import_rotto(monkeypatch)
    with pytest.raises(Failed) as esito:
        runtime.esigi_browser()
    messaggio = str(esito.value)
    assert 'playwright' in messaggio
    assert runtime.VARIABILE_SEVERA in messaggio


# -------------------------------------- lo skip a LIVELLO DI MODULO, che era rotto

def test_uno_skip_a_livello_di_modulo_e_permesso_e_NON_interrompe_la_raccolta(
        monkeypatch, pytester):
    """Il difetto trovato da GPT-5.5 e Fable 5, riprodotto e chiuso.

    `pytest.skip()` fuori da un test, senza `allow_module_level=True`, **non
    salta**: pytest lo tratta come errore di raccolta e interrompe l'esecuzione.
    Misurato prima della correzione, su un modulo finto che chiama `_manca` al
    livello superiore:

        ERROR collecting test_livello_modulo.py
        !!!! Interrupted: 1 error during collection !!!!

    Cioe' su una macchina senza Chromium non si perdevano cinque test: si perdeva
    **tutta la suite**. Ed era una regressione rispetto al `pytest.mark.skipif`
    che `esigi_browser()` ha sostituito.

    Qui si esegue davvero un modulo di test finto — `pytester` lancia un pytest
    figlio — perche' l'unico modo di sapere come pytest tratta una chiamata
    all'import e' fargliela trattare.
    """
    monkeypatch.delenv(runtime.VARIABILE_SEVERA, raising=False)
    pytester.makepyfile(f"""
        import sys
        sys.path.insert(0, {str(RADICE)!r})
        from tests import runtime
        runtime._manca('finto', 'a livello di modulo', livello_modulo=True)

        def test_mai_raggiunto():
            raise AssertionError('non dovrebbe girare')
    """)
    esito = pytester.runpytest('-q')
    esito.assert_outcomes(skipped=1, errors=0, failed=0)


def test_senza_il_flag_lo_stesso_skip_diventa_un_ERRORE_di_raccolta(
        monkeypatch, pytester):
    """La controprova: e' il flag a fare la differenza, non altro.

    Se un domani qualcuno togliesse `allow_module_level`, questo test resterebbe
    verde e l'altro diventerebbe rosso — quindi la coppia dice *quale* delle due
    forme e' quella giusta, e non solo che una funziona.
    """
    monkeypatch.delenv(runtime.VARIABILE_SEVERA, raising=False)
    pytester.makepyfile(f"""
        import sys
        sys.path.insert(0, {str(RADICE)!r})
        from tests import runtime
        runtime._manca('finto', 'a livello di modulo')   # senza il flag

        def test_mai_raggiunto():
            pass
    """)
    esito = pytester.runpytest('-q')
    esito.assert_outcomes(errors=1)


def test_in_CI_lo_stesso_modulo_NON_esce_verde(pytester, monkeypatch):
    """La proprieta' che la CI compra: rosso, non verde silenzioso.

    A livello di modulo `pytest.fail` diventa un errore di raccolta invece di un
    fallimento pulito — GPT-5.5 lo ha notato. Non e' un difetto: l'esito e'
    comunque **rosso** e il messaggio resta leggibile, e quelle due sono le sole
    cose che quel ramo deve garantire. Qui si verificano entrambe, su un pytest
    figlio vero perche' la variabile deve arrivargli nell'ambiente.
    """
    monkeypatch.setenv(runtime.VARIABILE_SEVERA, '1')
    pytester.makepyfile(f"""
        import sys
        sys.path.insert(0, {str(RADICE)!r})
        from tests import runtime
        runtime._manca('finto', 'assente in CI', livello_modulo=True)

        def test_mai_raggiunto():
            pass
    """)
    esito = pytester.runpytest_subprocess('-q')
    assert esito.ret != 0, \
        'in modalita- severa un runtime mancante a livello di modulo deve dare ROSSO'
    esito.stdout.fnmatch_lines(['*assente in CI*'])


def test_senza_chromium_pinnato_NE_avviabile_e_in_CI_FALLISCE(monkeypatch):
    """Il caso del runner con l'installazione del browser andata male.

    Si finge che il percorso pinnato non ci sia e che l'avvio di Playwright
    sollevi: e' quello che succede su una macchina dove `playwright install` non
    e' passato. In modalita' severa deve diventare rosso.
    """
    monkeypatch.setenv(runtime.VARIABILE_SEVERA, '1')
    monkeypatch.setattr(runtime, 'CHROMIUM_PINNATO', INESISTENTE)

    class ChromiumRotto:
        """Il Chromium di un runner dove `playwright install` non e' passato."""

        def launch(self, **_):
            """Solleva come farebbe Playwright con l'eseguibile assente."""
            raise RuntimeError('eseguibile non trovato')

    class ContestoFinto:
        """Il gestore di contesto di `sync_playwright()`, ridotto all'osso."""

        chromium = ChromiumRotto()

        def __enter__(self):
            """Restituisce se stesso, come fa il vero `sync_playwright()`."""
            return self

        def __exit__(self, *_):
            """Non ingoia le eccezioni: `esigi_browser` deve poterle vedere."""
            return False

    # Senza questo, su una macchina senza Playwright il test ERRORE invece di
    # saltare — cioe' esattamente il difetto che questo file esiste per vincolare,
    # riprodotto nel file stesso. Segnalato da CodeRabbit.
    vero = pytest.importorskip(
        'playwright.sync_api',
        reason='playwright non installato: questo caso monkeypatcha il suo modulo')
    monkeypatch.setattr(vero, 'sync_playwright', lambda: ContestoFinto())

    with pytest.raises(Failed) as esito:
        runtime.esigi_browser()
    assert 'Chromium' in str(esito.value)


# ----------------------------------------------- il ramo locale e il ramo della CI

class PlaywrightFinto:
    """Registra come `apri_chromium` ha chiamato `launch`, e niente altro."""

    def __init__(self):
        """Prepara la lista delle chiamate osservate e il finto `chromium`."""
        self.chiamate = []
        madre = self

        class Chromium:
            """Registra gli argomenti di `launch` invece di aprire un browser."""

            def launch(self, **kwargs):
                """Annota come e' stata chiamata e restituisce un segnaposto."""
                madre.chiamate.append(kwargs)
                return 'browser-finto'

        self.chromium = Chromium()


def test_col_pinnaggio_locale_passa_il_percorso(monkeypatch, tmp_path):
    """Il ramo locale: se il binario pinnato esiste, `launch` lo riceve.

    Il file finto basta che esista — non viene mai eseguito, perche' qui si
    osserva COME `apri_chromium` chiama `launch`, non cosa fa il browser.
    """
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
