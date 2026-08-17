"""La facciata: l'apex di betrelay.net deve servire un sito, non un oggetto JSON.

Perche' questo file esiste. Fino a questa PR `GET /` rispondeva
`{'service': 'xtrader-signal-relay', ...}`: corretto per una sonda, inutile per
una persona. Il proprietario ha aperto betrelay.net col telefono, ha visto quel
JSON, e la domanda era una sola — «questo me lo chiami sito web?».

Ma la parte che va vincolata non e' «c'e' l'HTML»: e' che **mettere un sito
sull'apex non si mangi il relay**. La forma pericolosa e' il catch-all in stile
SPA — `@app.get('/{resto:path}')` che restituisce la pagina — perche' trasforma
ogni percorso sconosciuto in una risposta valida: `/feed/{utente}.csv`, la rotta
del feed per utente che ancora non esiste, smetterebbe di essere un 404 e
diventerebbe `text/html` con stato 200. XTrader riceverebbe una pagina web al
posto di un CSV, senza un errore da nessuna parte.

**Misurato, sostituendo la rotta esplicita con quel catch-all:**

    E  AssertionError: /feed/PIERO.csv risponde 200 invece di 404
    E  assert 200 == 404
    ... idem /feed/, /qualcosa-che-non-esiste, /api/parsers/inventato/inventato
    4 failed, 10 passed

**E misurata anche la variante che NON e' pericolosa,** perche' avevo scritto qui
il contrario prima di provarla: `app.mount('/', StaticFiles(..., html=True))`
aggiunto in fondo al modulo lascia questi test **verdi** (14 passed). Due ragioni,
entrambe fragili da sole: le rotte esplicite sono registrate prima del mount e
vincono, e `StaticFiles` senza un `404.html` nella cartella risponde 404 sui
percorsi sconosciuti invece di servire l'indice. Il giorno che qualcuno mette un
`web/404.html`, un mount sulla radice diventerebbe il catch-all che qui non e' —
e a coprire quel caso c'e' l'asserzione sul `Content-Type`, che resta rossa anche
quando lo stato e' 404.

Per questo il catch-all e' testato qui e non «guardato» nel codice.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE))

import main  # noqa: E402 - dopo l'inserimento del percorso
from tests.ambiente import TOKEN_DI_PROVA  # noqa: E402
from tests.servizio import relay_avviato  # noqa: E402

# Il marchio, come stringa unica: se un domani cambia, un solo posto da toccare.
MARCHIO = 'BetRelay'


@pytest.fixture(scope='module')
def servizio(tmp_path_factory):
    """Il relay vero, con un token configurato.

    Il token serve a due test opposti: che il feed continui a funzionare, e che
    il suo valore NON compaia nella pagina pubblica.
    """
    with relay_avviato(tmp_path_factory.mktemp('facciata'),
                       CSV_ACCESS_TOKEN=TOKEN_DI_PROVA) as base:
        yield base


def _prendi(url):
    """Restituisce (stato, header, byte) senza sollevare sui 4xx.

    Gli header restano l'oggetto `email.message.Message`, che confronta i nomi
    **senza** distinguere le maiuscole. Convertirlo in `dict` sembra innocuo e
    non lo e': i server HTTP/1.1 mandano `content-type` in minuscolo, quindi
    `dict(r.headers).get('Content-Type')` restituisce `None` con l'header
    presente. Misurato scrivendo questo file: due test che dovevano essere verdi
    sul codice vecchio erano rossi per colpa dell'helper, non del relay — cioe'
    un test che accusa il codice sbagliato.
    """
    try:
        with urllib.request.urlopen(url, timeout=10) as r:  # noqa: S310 - loopback
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


# ---------------------------------------------------------------- la facciata

def test_l_apex_serve_una_pagina_HTML_e_non_JSON(servizio):
    stato, header, corpo = _prendi(f'{servizio}/')
    assert stato == 200
    tipo = header.get('Content-Type', '')
    assert tipo.startswith('text/html'), \
        f'l\'apex risponde {tipo!r}: un JSON non e- un sito web'
    assert not corpo.lstrip().startswith(b'{'), \
        'l\'apex restituisce ancora un oggetto JSON'


def test_la_facciata_porta_il_marchio_e_dice_cosa_fa(servizio):
    """Il nome non basta: una landing che non spiega il servizio non e' una landing."""
    testo = _prendi(f'{servizio}/')[2].decode('utf-8')
    assert MARCHIO in testo, f'la pagina non nomina {MARCHIO}'
    for parola in ('Telegram', 'XTrader', 'CSV'):
        assert parola in testo, f'la pagina non dice cosa fa il servizio: manca {parola!r}'


def test_la_facciata_dichiara_la_codifica_in_DUE_posti(servizio):
    """Header HTTP **e** meta tag, perche' il testo italiano ha accenti.

    In questo repository la codifica non e' un dettaglio estetico: i marcatori dei
    parser sono emoji, e un confronto sbagliato non solleva un errore, restituisce
    «non riconosciuto». La stessa classe di difetto su una pagina si vede come
    «perche' c'e- scritto perchÃ¨» — visibile, ma solo a chi guarda.
    """
    stato, header, corpo = _prendi(f'{servizio}/')
    assert stato == 200
    assert 'charset=utf-8' in header.get('Content-Type', '').lower(), \
        'la risposta non dichiara utf-8: il browser indovinerebbe la codifica'
    testo = corpo.decode('utf-8')  # solleva se i byte non sono utf-8 validi
    assert '<meta charset="utf-8">' in testo.lower(), \
        'la pagina non dichiara la codifica: salvata su disco perderebbe gli accenti'


def test_la_facciata_manda_al_prototipo_e_quel_percorso_ESISTE(servizio):
    """Il pulsante deve puntare a qualcosa che risponde 200.

    Un test che cercasse solo `href="/app/"` nel sorgente passerebbe anche con
    l'app spostata altrove: verificherebbe il testo del link, non il link.
    """
    testo = _prendi(f'{servizio}/')[2].decode('utf-8')
    assert '/app/' in testo, 'la facciata non porta all\'applicazione'
    stato, header, _ = _prendi(f'{servizio}/app/')
    assert stato == 200, f'il pulsante della facciata porta a un {stato}'
    assert header.get('Content-Type', '').startswith('text/html')


def test_la_facciata_NON_contiene_il_token_del_feed(servizio):
    """La pagina e' pubblica e senza sessione: qualunque token qui e' pubblicato.

    Il servizio di questa fixture ha `CSV_ACCESS_TOKEN` configurato, quindi il
    valore esiste davvero nel processo che compone la risposta — che e' la
    condizione in cui un `format` distratto lo farebbe uscire.
    """
    testo = _prendi(f'{servizio}/')[2].decode('utf-8')
    assert TOKEN_DI_PROVA not in testo
    for sospetto in ('CSV_ACCESS_TOKEN', 'TELEGRAM_BOT_TOKEN'):
        assert sospetto not in testo, f'la pagina pubblica nomina {sospetto}'


def test_la_facciata_e_TROVABILE_mentre_l_applicazione_resta_noindex(servizio):
    """Le due pagine hanno scopi opposti, e la differenza va vincolata.

    Una landing con `noindex` non e' un sito web: e' una pagina che nessuno
    trovera'. L'applicazione invece non ha niente da indicizzare, e continua a
    dichiararlo. Copiare l'`index.html` del prototipo per fretta porterebbe
    dietro il suo `noindex` e il difetto sarebbe invisibile: la pagina si vede
    benissimo: semplicemente non entra in nessun motore di ricerca.
    """
    facciata = _prendi(f'{servizio}/')[2].decode('utf-8')
    assert 'noindex' not in facciata.lower(), \
        'la facciata si dichiara non indicizzabile: non sarebbe mai trovata'
    app = _prendi(f'{servizio}/app/')[2].decode('utf-8')
    assert 'noindex' in app.lower(), \
        'il prototipo ha perso il proprio noindex'


# --------------------------------------- la facciata definitiva (#37 / #38)

def test_la_facciata_usa_il_logo_relay_e_la_favicon_ico(servizio):
    """Le icone sono ASSET COMMITTATI e serviti, non un segnaposto disegnato.

    Il quadrato «BR» era un segnaposto dichiarato tale nella #37: la facciata
    definitiva usa l'icona relay fornita dal proprietario. Il test chiede tre
    cose: la pagina li referenzia, i file rispondono davvero (un `src` che
    punta a un 404 e' un logo rotto che i byte della pagina non mostrano), e
    i byte sono del formato giusto — firma PNG e firma ICO, non un HTML di
    errore salvato col nome dell'immagine.
    """
    testo = _prendi(f'{servizio}/')[2].decode('utf-8')
    assert 'betrelay-icona-256.png' in testo, 'la pagina non usa il logo relay'
    assert 'betrelay-favicon-sito.ico' in testo, 'la pagina non usa la favicon .ico'
    assert '>BR<' not in testo, 'il quadrato segnaposto «BR» e\' ancora nella pagina'

    stato, header, corpo = _prendi(f'{servizio}/app/betrelay-icona-256.png')
    assert stato == 200, f'il logo relay risponde {stato}'
    assert corpo[:8] == b'\x89PNG\r\n\x1a\n', f'il logo non e\' un PNG: {corpo[:8]!r}'

    stato, header, corpo = _prendi(f'{servizio}/app/betrelay-favicon-sito.ico')
    assert stato == 200, f'la favicon risponde {stato}'
    assert corpo[:4] == b'\x00\x00\x01\x00', f'la favicon non e\' un ICO: {corpo[:4]!r}'


def test_la_facciata_mostra_la_famiglia_XTrader_first(servizio):
    """La decisione della #37: la famiglia si vede, ma il servizio copre XTrader.

    «Attivo» su XTrader e «In arrivo» sugli altri non sono decorazione: sono la
    promessa commerciale — un cliente di BETTINGTOOLKIT.ES che si registra oggi
    deve averlo letto PRIMA, non scoprirlo dal parser che non funziona.
    """
    testo = _prendi(f'{servizio}/')[2].decode('utf-8')
    assert 'Oggi la guida copre XTrader' in testo

    # L'associazione scheda -> stato, non due substring sull'intera pagina:
    # con le pillole SCAMBIATE (XTrader «In arrivo», Toolkit «Attivo») un
    # controllo globale resterebbe verde. Le schede sono i blocchi
    # `<div class="prod">`: ognuna deve contenere il SUO stato e non l'altro.
    # Segnalato da CodeRabbit sulla PR #54.
    schede = testo.split('<div class="prod">')[1:]
    assert len(schede) == 4, f'attese 4 schede prodotto, trovate {len(schede)}'
    attesi = {'XTrader': 'Attivo', 'BETTINGTOOLKIT.COM': 'In arrivo',
              'BETTINGTOOLKIT.ES': 'In arrivo', 'BETTINGTOOLKIT.LAT': 'In arrivo'}
    for nome, stato in attesi.items():
        scheda = next((s for s in schede if nome in s), None)
        assert scheda is not None, f'manca la scheda del prodotto {nome}'
        assert stato in scheda, f'{nome}: la scheda non dice «{stato}»'
        altro = 'In arrivo' if stato == 'Attivo' else 'Attivo'
        assert altro not in scheda, f'{nome}: la scheda dice anche «{altro}»'


def test_la_facciata_dichiara_cosa_NON_fa_e_il_disclaimer(servizio):
    """La pagina dice che BetRelay non piazza scommesse, e di chi NON e'.

    Il disclaimer di non affiliazione (TradingSportivo / Betting Toolkit) e il
    «18+» stanno nel footer dello sketch approvato (#38) e sono la parte legale
    della facciata: se un restyle futuro li perde, questo test lo dice.
    """
    testo = _prendi(f'{servizio}/')[2].decode('utf-8')
    assert 'non piazza scommesse' in testo
    assert '18+' in testo, 'il footer ha perso l\'avvertenza 18+'
    assert 'non è affiliato' in testo, 'il footer ha perso il disclaimer di affiliazione'


def test_la_facciata_ha_la_card_di_flusso(servizio):
    """La catena Telegram → parser → CSV → software → svuotamento, come card.

    E' il pezzo di design che la #37 chiama «card di flusso»: il riassunto del
    servizio in cinque nodi. I marcatori qui sotto sono le etichette che solo
    quella card contiene.
    """
    testo = _prendi(f'{servizio}/')[2].decode('utf-8')
    # I nodi si cercano DENTRO la card (`class="flow"` fino alla sezione
    # successiva), non nell'intera pagina: «Custom Parser» compare anche
    # altrove, e un test globale non direbbe niente sulla card. Segnalato da
    # CodeRabbit sulla PR #54.
    assert 'class="flow"' in testo, 'manca la card di flusso'
    card = testo[testo.index('class="flow"'):testo.index('<section id="come"')]
    for nodo in ('Telegram', 'Custom Parser', 'segnali.csv',
                 'XTrader / Betting Toolkit', 'CSV pulito — mai segnali vecchi'):
        assert nodo in card, f'manca il nodo {nodo!r} nella card di flusso'


def test_la_scorciatoia_admin_e_solo_un_redirect_al_pannello(servizio):
    """`/admin` (#57): la porta di servizio del proprietario, senza serratura propria.

    E' un REDIRECT e basta: la protezione resta il login piu' il 404 server-side
    di `/api/admin/*` per chi non e' amministratore. Il test NON segue il
    redirect: seguirlo verificherebbe la pagina di destinazione, non il
    comportamento della rotta — e un redirect sbagliato verso una pagina
    comunque valida passerebbe.
    """
    class _SenzaRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(_SenzaRedirect)
    try:
        with opener.open(f'{servizio}/admin', timeout=10) as r:  # noqa: S310 - loopback
            stato, intestazioni = r.status, r.headers
    except urllib.error.HTTPError as e:
        stato, intestazioni = e.code, e.headers
    assert stato in (302, 307), f'/admin risponde {stato} invece di un redirect'
    assert intestazioni.get('Location') == '/app/#/richieste', \
        f'/admin manda a {intestazioni.get("Location")!r}'


# ------------------------------------------------- e adesso: niente catch-all

def test_il_feed_di_XTrader_NON_e_intercettato_dalla_facciata(servizio):
    """La regressione che conta: `/xtrader.csv` resta un CSV, con il BOM.

    E' l'URL gia' configurato in XTrader dal profilo PIERO. Se la facciata lo
    intercettasse, il sintomo dal lato del cliente sarebbe «non arrivano piu' i
    segnali», senza un errore da nessuna parte.
    """
    stato, header, corpo = _prendi(f'{servizio}/xtrader.csv?token={TOKEN_DI_PROVA}')
    assert stato == 200
    assert header.get('Content-Type', '').startswith('text/csv'), \
        f'il feed risponde {header.get("Content-Type")!r}'
    assert corpo.startswith(b'\xef\xbb\xbf"Provider"'), \
        f'il feed ha perso il BOM o l\'intestazione: {corpo[:32]!r}'


@pytest.mark.parametrize('percorso', [
    '/feed/PIERO.csv',      # la rotta del feed per utente: non esiste ANCORA
    '/feed/',
    '/qualcosa-che-non-esiste',
    '/api/parsers/inventato/inventato',
])
def test_un_percorso_sconosciuto_resta_404_e_NON_diventa_la_facciata(servizio, percorso):
    """Il test che il proprietario ha chiesto per nome.

    `/feed/{utente}.csv` e' la rotta che nascera' nella PR del feed per utente.
    Oggi non esiste, e deve restare un 404: il giorno che esiste, un catch-all
    l'avrebbe silenziosamente coperta prima ancora che qualcuno la scrivesse.
    """
    stato, header, corpo = _prendi(f'{servizio}{percorso}')
    assert stato == 404, f'{percorso} risponde {stato} invece di 404'
    assert MARCHIO not in corpo.decode('utf-8', 'replace'), (
        f'{percorso} NON deve cadere nella facciata: la rotta del feed per utente '
        'riceverebbe una pagina HTML al posto di un CSV'
    )
    assert not header.get('Content-Type', '').startswith('text/html'), \
        f'{percorso} risponde con una pagina: e- un catch-all'


def test_health_resta_una_risposta_PER_MACCHINE(servizio):
    """Chi sonda il servizio non vuole una pagina.

    Con la facciata sull'apex, `/health` diventa l'unico posto dove leggere lo
    stato in modo automatico: se diventasse HTML anche lui, non resterebbe niente.
    """
    stato, header, corpo = _prendi(f'{servizio}/health')
    assert stato == 200
    assert header.get('Content-Type', '').startswith('application/json')
    assert 'status' in json.loads(corpo)


# --------------------------------------------------------- il caso di riserva

def test_senza_il_file_la_facciata_TORNA_al_JSON_di_cortesia(monkeypatch):
    """Un deploy senza `web/` non deve rispondere 500 sull'apex.

    In processo di proposito: serve sostituire il percorso del file, che dal
    sottoprocesso non si puo' fare. `/` e' la rotta che le sonde e le persone
    provano per prime quando qualcosa non va, ed e' la peggiore su cui rispondere
    con un errore del server.
    """
    monkeypatch.setattr(main, 'SITO', Path('/non/esiste/sito.html'))
    risposta = main.root()
    assert risposta.status_code == 200
    assert risposta.media_type == 'application/json'
    assert json.loads(risposta.body)['status'] == 'online'


def test_la_facciata_e_un_file_SERVITO_non_una_stringa_nel_codice():
    """Il percorso deve derivare da `WEB_DIR`, non essere ricomposto a mano.

    `WEB_DIR` e' gia' la fonte unica della cartella pubblica — la usa il mount di
    `/app` e la vincola `tests/safety/test_static_mount.py`. Una seconda
    espressione `Path(__file__).parent / 'web'` accanto sarebbe la duplicazione
    che la regola 3 vieta: due copie corrette oggi, due copie divergenti domani.
    """
    sorgente = (RADICE / 'main.py').read_text(encoding='utf-8')
    assert 'SITO = WEB_DIR /' in sorgente, \
        'il percorso della facciata non deriva da WEB_DIR'

    # Solo le righe di CODICE. Il conteggio sul sorgente grezzo era rosso appena
    # scritto, e per un motivo che vale la pena tenere: il commento di `main.py`
    # che spiega di NON ricomporre quel percorso contiene l'espressione stessa.
    # Una guardia che confonde una spiegazione con una violazione insegna a
    # ignorarla. Qui si scartano le righe interamente di commento — che e' la
    # forma dei commenti di `main.py`, verificata, non supposta.
    codice = [r for r in sorgente.splitlines() if not r.lstrip().startswith('#')]
    quante = sum(r.count("Path(__file__).parent / 'web'") for r in codice)
    assert quante == 1, \
        f'la cartella web/ e- ricomposta in {quante} posti nel codice, non in uno'
    assert main.SITO.is_file(), f'la facciata non esiste su disco: {main.SITO}'
