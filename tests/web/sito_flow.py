"""Apre la facciata in un browser vero, a telefono e a schermo grande.

Un test HTTP dice che i byte sono giusti. Non dice che la pagina si vede: un
foglio di stile con un errore di sintassi, un contenuto che sfonda in orizzontale
sul telefono, un pulsante che porta a un 404 — tutte cose che passano un
controllo sui byte e si vedono solo aprendola.

Quello che questo script misura, e che va misurato perche' non si deduce:

- zero errori in console e zero risorse fallite (>= 400): una pagina che carica
  un file che non c'e' funziona per il test e non per il cliente;
- nessuno scorrimento orizzontale a 390 px — il difetto tornato quattro volte
  nella web app (regola 2 di CLAUDE.md);
- il pulsante principale porta davvero all'applicazione, cliccandolo;
- la riga larga del CSV scorre DENTRO il suo contenitore, che e' il modo giusto
  di non far scorrere la pagina.

Screenshot di ogni schermata in `<cartella>`, a 390 px e a 1280 px.

Uso:  python tests/web/sito_flow.py <url-di-base> [cartella-screenshot]
"""

import pathlib
import sys
import tempfile
from urllib.parse import urlparse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from playwright.sync_api import sync_playwright

# Fonte unica dell-avvio del browser: il percorso pinnato in questo
# ambiente, quello di Playwright in CI. Prima era ricopiato in cinque file.
from tests.runtime import apri_chromium  # noqa: E402


BASE = (sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8099/').rstrip('/') + '/'
OUT = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else pathlib.Path(tempfile.mkdtemp())
OUT.mkdir(parents=True, exist_ok=True)

MISURE = [('telefono', 390, 844), ('scrivania', 1280, 900)]

errori = []
risorse = []


def _sonda_di_sessione(testo):
    """Il 401 di GET /api/me e' ATTESO quando dalla facciata si entra in /app:
    dall'aggancio (#32) l'app reale sonda la sessione al boot, e chi arriva dal
    sito non ha un cookie. Non e' un errore della pagina. Ogni altro codice
    (404 di un asset, 500) resta un fallimento."""
    return '401' in testo and '/api/me' in testo


def _errore_console(m):
    if m.type != 'error':
        return
    # Stesso criterio del canale risorse, via `_sonda_di_sessione`: il testo di
    # Chromium non porta l'URL, che sta in `location`. Un 401 su qualunque
    # rotta che non sia la sonda resta un errore (CodeRabbit, PR #50).
    if 'Failed to load resource' in m.text and '401' in m.text:
        url = (m.location or {}).get('url', '')
        if _sonda_di_sessione(f'401 {url}'):
            return
    errori.append(f'console.{m.type}: {m.text}')


def _ascolta(pagina):
    pagina.on('pageerror', lambda e: errori.append(f'pageerror: {e}'))
    pagina.on('console', _errore_console)
    # Una risorsa mancante non e' un errore di console: si vede solo qui.
    pagina.on('response', lambda r: risorse.append(f'{r.status} {r.url}')
              if r.status >= 400 and not _sonda_di_sessione(f'{r.status} {r.url}')
              else None)


# Il margine laterale minimo che ogni blocco di testo deve avere dal bordo dello
# schermo. Vale 20 px nel foglio di stile; qui si chiede 16 per non rendere il test
# ostaggio di un ritocco di due pixel.
GUARDIA_PX = 16

# Un blocco per ogni fascia della pagina: se il margine si perde, si perde per
# fascia, non per elemento.
BLOCCHI = ['h1', '.sommario', '.stato', '.azioni a.bottone', 'h2',
           '.scheda', '.telaio', '.freccia', 'footer .righe > *']


def _dentro_i_margini(pagina, dove):
    """Ogni blocco sta staccato dai bordi. Misurato, non guardato a occhio.

    Il difetto che ha motivato questa funzione: l'apertura era
    `<div class="dentro apertura">`, cioe' due classi sullo STESSO elemento, e
    `.apertura { padding: 72px 0 56px }` azzerava il padding laterale di
    `.dentro`. Titolo, sommario e pulsanti finivano attaccati ai bordi del
    telefono mentre tutte le altre sezioni restavano rientrate — brutto, e
    invisibile a chi non confronta due fasce fra loro.

    Nessuno scorrimento orizzontale NON copre questo caso: la pagina era larga
    esattamente 390, quindi quel controllo era verde. Sono due difetti diversi.
    """
    larghezza = pagina.evaluate('innerWidth')
    for scelta in BLOCCHI:
        elemento = pagina.query_selector(scelta)
        assert elemento is not None, f'{dove}: manca {scelta}'
        sinistra, destra = elemento.evaluate(
            'e => { const b = e.getBoundingClientRect();'
            '       return [Math.round(b.left), Math.round(b.right)]; }')
        assert sinistra >= GUARDIA_PX, \
            f'{dove}: {scelta} tocca il bordo sinistro (left={sinistra})'
        assert destra <= larghezza - GUARDIA_PX, \
            f'{dove}: {scelta} tocca il bordo destro (right={destra} di {larghezza})'


def _senza_scorrimento(pagina, dove):
    largo = pagina.evaluate('document.documentElement.scrollWidth')
    visibile = pagina.evaluate('document.documentElement.clientWidth')
    print(f'  {dove}: scrollWidth {largo} / clientWidth {visibile}')
    assert largo <= visibile + 1, \
        f'{dove}: la pagina scorre in orizzontale ({largo} > {visibile})'


with sync_playwright() as pw:
    browser = apri_chromium(pw)
    for nome, larghezza, altezza in MISURE:
        pagina = browser.new_page(viewport={'width': larghezza, 'height': altezza})
        _ascolta(pagina)
        pagina.goto(BASE, wait_until='load')

        # La facciata c'e' davvero, e non e' il JSON di prima.
        pagina.wait_for_selector('h1')
        titolo = pagina.text_content('h1')
        assert 'XTrader' in titolo, f'{nome}: titolo inatteso {titolo!r}'
        assert pagina.is_visible('.marchio'), f'{nome}: il marchio non e- visibile'
        print(f'  {nome}: h1 = {" ".join(titolo.split())!r}')

        pagina.screenshot(path=str(OUT / f'{nome}-1-apertura.png'))
        pagina.screenshot(path=str(OUT / f'{nome}-2-intera.png'), full_page=True)
        _senza_scorrimento(pagina, nome)
        _dentro_i_margini(pagina, nome)

        # La riga del CSV e' larga: deve scorrere dentro il proprio contenitore.
        # Se scorresse la pagina, l'asserzione sopra sarebbe gia' rossa; qui si
        # verifica il rovescio, cioe' che il contenitore SIA quello che scorre.
        riquadro = pagina.query_selector('.telaio pre')
        assert riquadro is not None, f'{nome}: manca il riquadro del CSV'
        interno = riquadro.evaluate('e => e.scrollWidth')
        esterno = riquadro.evaluate('e => e.clientWidth')
        scorre = riquadro.evaluate(
            "e => getComputedStyle(e).overflowX === 'auto'"
            " || getComputedStyle(e).overflowX === 'scroll'")
        print(f'  {nome}: riquadro CSV {interno} in {esterno}, scorre={scorre}')
        assert scorre, f'{nome}: il riquadro del CSV non scorre: allargherebbe la pagina'
        # `overflow-x: auto` da solo non prova niente: e' vero anche su un
        # contenuto che sta tutto dentro, o che va a capo. A 390 px la riga del CSV
        # DEVE sporgere davvero, o questo controllo non sta guardando lo
        # scorrimento — sta guardando una regola CSS. Segnalato da CodeRabbit.
        # Solo a 390: a schermo largo il contenitore potrebbe legittimamente
        # bastare, e pretendere l'eccedenza li' sarebbe un test che chiede alla
        # pagina di stare scomoda.
        if larghezza == 390:
            assert interno > esterno, (
                f'{nome}: il CSV entra tutto ({interno} in {esterno}): il controllo '
                'sullo scorrimento non sta misurando nulla')

        # Il pulsante principale: cliccato, non letto.
        pagina.click('.azioni a.bottone')
        pagina.wait_for_load_state('load')
        assert '/app/' in pagina.url, \
            f'{nome}: il pulsante non porta all\'applicazione, sono su {pagina.url}'
        # Dall'aggancio (#32) /app e' l'app reale: si atterra sulla pagina di
        # login, non piu' sul banner «PROTOTIPO · DATI FINTI», che vive solo
        # nella copia dimostrativa a file unico.
        pagina.wait_for_selector('.login')
        pagina.screenshot(path=str(OUT / f'{nome}-3-applicazione.png'))
        print(f'  {nome}: il pulsante porta a {pagina.url}')

        # E il marchio riporta all'apex, come su qualunque sito.
        #
        # La prima versione di questo blocco era VACUA, ed e' il difetto peggiore
        # dei tre trovati da CodeRabbit: caricava `BASE`, cliccava `.marchio` e
        # aspettava `h1` — che era gia' li'. Misurato: cambiando l'href del marchio
        # in `#dovenonva`, il test passava comunque (`1 passed`). Un test che non
        # puo' fallire non e' un test.
        #
        # Adesso il click e' OSSERVABILE: si parte da un URL diverso — l'ancora
        # `#come`, raggiunta cliccando il secondo pulsante, che e' anch'essa
        # navigazione da verificare — e si pretende di tornare all'apex nudo.
        # I due selettori nominano il DESTINO, non la posizione: `a[href="#come"]`
        # invece del secondo pulsante della fila. Un selettore posizionale
        # continuerebbe a passare dopo uno scambio d'ordine dei due pulsanti,
        # verificando il comportamento sbagliato. Segnalato da GPT-5.5.
        pagina.goto(BASE, wait_until='load')
        pagina.click('.azioni a[href="#come"]')
        assert urlparse(pagina.url).fragment == 'come', \
            f'{nome}: «Come funziona» non porta all\'ancora, sono su {pagina.url}'

        # Il confronto e' sul PERCORSO, non sulla stringa intera: `== BASE`
        # dipenderebbe da come browser e server normalizzano lo slash finale, e
        # sarebbe un test che fallisce per un dettaglio di forma su un requisito
        # — «si torna all'apex» — che quel dettaglio non riguarda. Segnalato da
        # GPT-5.5 e Fable 5 nello stesso giro, sulla stessa riga.
        pagina.click('.marchio')
        pagina.wait_for_url(lambda u: urlparse(u).path == '/' and not urlparse(u).fragment)
        indirizzo = urlparse(pagina.url)
        assert indirizzo.path == '/' and not indirizzo.fragment, \
            f'{nome}: il marchio non riporta all\'apex, sono su {pagina.url}'
        pagina.wait_for_selector('h1')
        print(f'  {nome}: #come -> marchio -> {pagina.url}')
        pagina.close()

print('screenshot in', OUT)
print('errori in console:', errori)
print('risorse fallite:', risorse)
if errori or risorse:
    sys.exit(1)
