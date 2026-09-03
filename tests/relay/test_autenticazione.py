"""La serratura del relay, e la prova che c'e' davvero.

Perche' questo file esiste, misurato prima della patch che lo accompagna:
sostituendo tutte e otto le chiamate ad `auth()` in `main.py` con `pass` — cioe'
rimuovendo del tutto l'autenticazione da dieci rotte, sei delle quali scrivono —
la suite dava **144 passed**. Nessun test si accorgeva della serratura mancante.

Non era una lacuna casuale, era strutturale. La fixture autouse di
`test_csv_contract.py` azzerava `main.TOKEN` per tutta la suite e la whitelist di
`tests/ambiente.py` toglie `CSV_ACCESS_TOKEN` ai sottoprocessi: entrambe scelte
GIUSTE, perche' con la variabile del proprietario nell'ambiente l'esito
dipenderebbe da chi esegue i test. Ma la conseguenza era che ogni test girava
nello stato fail-open, e la serratura non veniva mai girata da nessuno.

La correzione non e' ereditare il token del proprietario: e' portarne uno NOTO.
Quello di questo file e' una costante scritta nel sorgente — non e' un segreto,
non apre niente, e serve solo a rendere il rifiuto osservabile.

Il secondo difetto che questi test vincolano e' `auth()` che falliva in
APERTURA: `if TOKEN and token != TOKEN` non fa nulla quando `CSV_ACCESS_TOKEN`
manca, quindi il modo di rendere pubblico il servizio era cancellare una
variabile dalla dashboard di Railway. Nessun errore, nessun log, nessun check.
"""

from __future__ import annotations

import inspect
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
from pydantic import BaseModel

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE))

import main  # noqa: E402 - dopo l'inserimento del percorso
from tests.ambiente import CHIAVI_PERICOLOSE, TOKEN_DI_PROVA  # noqa: E402
from tests.servizio import relay_avviato  # noqa: E402

# Le rotte che sono pubbliche PER PROGETTO, elencate una per una. E' la parte
# importante del test sulle rotte: una rotta nuova non compare qui, quindi se
# dimentica `auth()` il test diventa rosso finche' qualcuno non dichiara
# esplicitamente che vuole esporla. Un allowlist per prefisso avrebbe coperto in
# silenzio tutto cio' che nasce sotto quel prefisso.
ROTTE_PUBBLICHE = {
    ('GET', '/'),                    # pagina di cortesia
    # La scorciatoia del proprietario (#57): un REDIRECT verso /app/#/richieste,
    # senza autenticazione propria — la serratura e' il login, piu' il 404
    # server-side di /api/admin/* per chi non e' amministratore.
    ('GET', '/admin'),
    ('GET', '/health'),              # deve rispondere anche a servizio guasto
    # La chiama Telegram, quindi non puo' pretendere `CSV_ACCESS_TOKEN`. Ma
    # «pubblica» qui vuol dire NON AUTENTICATA, non «protetta da altro»: il filtro
    # dei chat_id fa instradamento, non autenticazione, perche' il chat_id arriva
    # dal corpo della richiesta. Misurato: un POST forgiato senza alcun token
    # inserisce una riga nel feed che XTrader legge, mentre leggerlo senza token
    # da- 401. La correzione e- il secret_token di Telegram — Issue #13 — e finche-
    # non c'e- questa voce elenca un rischio noto, non una difesa.
    ('POST', '/telegram/webhook'),
    # Chiudere una sessione che non esiste e' un no-op, e deve riuscire sempre: chi
    # premesse «esci» con un cookie gia' scaduto vedrebbe altrimenti un errore per
    # un'operazione che ha ottenuto ciò che voleva. Non tocca il database e non
    # rivela niente — cancella un cookie.
    ('POST', '/api/logout'),
    # I valori pubblici che la pagina di login conosce PRIMA della sessione (#32):
    # bot_username, bot_id, base_url. Il bot_id e' il prefisso del token del bot
    # prima dei due punti — pubblico per costruzione, compare in ogni embed del
    # widget — e serve al link di oauth.telegram.org in modalita' redirect. Che il
    # TOKEN non esca da qui lo vincola tests/relay/test_settings.py.
    ('GET', '/api/settings'),
}

# La TERZA categoria, nata col PR 6: rotte che non usano `CSV_ACCESS_TOKEN` ma non
# sono pubbliche — hanno un'autenticazione **propria**.
#
# Esistono perche' `CSV_ACCESS_TOKEN` e' un segreto unico per tutto il servizio, e un
# login non puo' pretenderlo: chi fa login e' proprio chi non ha ancora niente in mano.
#
# Metterle in `ROTTE_PUBBLICHE` sarebbe stato piu' rapido e avrebbe **spento la
# guardia** su di loro: la guardia dice «questa rotta rifiuta chi non ha il token», e
# per queste la frase giusta e' «rifiuta chi non ha la propria credenziale». Il valore
# atteso e' scritto qui accanto a ciascuna, e il test lo verifica invece di saltarle.
ROTTE_CON_AUTENTICAZIONE_PROPRIA = {
    # Firma HMAC del Login Widget assente o non valida → 401.
    ('POST', '/api/login/telegram'): 401,
    # `ADMIN_PASSWORD_HASH` non configurato → 503, come `auth()` senza token: chi lo
    # vede deve andare a configurare, non a cercare la password giusta. Il servizio dei
    # test non porta quella variabile, quindi qui l'atteso e' 503.
    ('POST', '/api/login/password'): 503,
    # Cookie di sessione assente, non firmato o scaduto → 401.
    ('GET', '/api/me'): 401,
    # Il cliente chiede l'accesso: e' la sua sessione a dire chi e', quindi senza sessione
    # 401 come `/api/me`.
    ('POST', '/api/access/request'): 401,
    # Le rotte del pannello rispondono **404** a chi non e' l'amministratore, non 403: un 403
    # confermerebbe a un estraneo che il pannello sta li'. Le due POST leggono il corpo a mano
    # DOPO il controllo della sessione, altrimenti FastAPI validerebbe il corpo per primo e
    # risponderebbe 422 — cioe' la stessa conferma, per un'altra via. L'ha trovato questa
    # guardia sulla PR #26.
    ('GET', '/api/admin/requests'): 404,
    ('POST', '/api/admin/requests/{richiesta}/approva'): 404,
    ('POST', '/api/admin/requests/{richiesta}/rifiuta'): 404,
    ('POST', '/api/admin/promemoria'): 404,
    # Scarica l'intero database (#56): copia dei dati dei clienti, quindi **404** a
    # chi non e' l'amministratore, come il resto di `/api/admin/*`.
    ('GET', '/api/admin/backup'): 404,
    # Canale di backup (#56 pezzo 2): configurazione GLOBALE del proprietario — stato,
    # conferma del candidato con invio di prova, prova sul configurato, rimozione. **404**
    # a chi non e' l'amministratore, come tutto `/api/admin/*`.
    ('GET', '/api/admin/canale-backup'): 404,
    ('POST', '/api/admin/canale-backup/conferma'): 404,
    ('POST', '/api/admin/canale-backup/prova'): 404,
    ('DELETE', '/api/admin/canale-backup'): 404,
    # Invio del backup al canale (#56 pezzo 3): sessione admin OPPURE token del cron. Senza
    # nessuno dei due (e senza token configurato nell'ambiente di test) → **404**, come il resto.
    ('POST', '/api/admin/backup/invia'): 404,
    # I parser dell'utente: autenticazione a SESSIONE, non col token del feed. Senza
    # cookie valido → 401 come `/api/me`. Le due rotte col corpo (POST, PUT) lo leggono
    # a mano DOPO il controllo della sessione, o FastAPI risponderebbe 422 al corpo
    # finto prima di arrivare al 401 — la stessa conferma «questa rotta esiste» che le
    # `/api/admin/*` evitano.
    ('GET', '/api/me/parsers'): 401,
    ('POST', '/api/me/parsers'): 401,
    ('PUT', '/api/me/parsers/{slug}'): 401,
    ('DELETE', '/api/me/parsers/{slug}'): 401,
    ('POST', '/api/me/parsers/{slug}/test'): 401,
    # Le chat verificate dall'utente (#32, pezzo 3.2): stessa serratura dei parser —
    # sessione, non token del feed e non admin token. Il `chat_id` di percorso non
    # esiste, e la risposta e' comunque **401**: chi non ha sessione non deve
    # nemmeno sapere se quella chat esista, che e' la stessa ragione per cui le
    # `/api/admin/*` rispondono 404.
    #
    # `PUT /api/me/parsers/{slug}/chats` legge il corpo a mano DOPO il controllo
    # della sessione, come le altre due rotte col corpo: con un modello Pydantic
    # FastAPI validerebbe prima e il corpo finto di questo test riceverebbe 422,
    # cioe' la conferma «questa rotta esiste» a un estraneo.
    ('GET', '/api/chats'): 401,
    ('POST', '/api/chats/verify/start'): 401,
    ('GET', '/api/chats/verify/status'): 401,
    ('DELETE', '/api/chats/{chat_id}'): 401,
    ('GET', '/api/me/parsers/{slug}/chats'): 401,
    ('PUT', '/api/me/parsers/{slug}/chats'): 401,
    # Il feed per utente: la serratura e' il token DELL'UTENTE (hash su `users`),
    # non `CSV_ACCESS_TOKEN`. Ogni fallimento e' **404 uniforme** — slug
    # inesistente, token assente o sbagliato, token altrui — perche' un 401 su
    # uno slug esistente direbbe a chi enumera «questo cliente esiste». I casi
    # positivi e l'isolamento stanno in `test_feed_utente.py`.
    ('GET', '/feed/{slug}.csv'): 404,
    # Conia il token del feed: autenticazione a sessione, come `/api/me`.
    ('POST', '/api/me/token'): 401,
    # La libreria mercati Betfair (#33): sessione come i parser, corpo letto a mano
    # DOPO il controllo (401 prima del 422, stessa regola e stessa guardia).
    ('GET', '/api/me/sports'): 401,
    ('POST', '/api/me/sports'): 401,
    ('DELETE', '/api/me/sports/{slug}'): 401,
    ('GET', '/api/me/sports/{slug}/mercati'): 401,
    ('POST', '/api/me/sports/{slug}/mercati'): 401,
    ('DELETE', '/api/me/sports/{slug}/mercati/{mid}'): 401,
    ('GET', '/api/me/sports/{slug}/mercati/{mid}/selezioni'): 401,
    ('POST', '/api/me/sports/{slug}/mercati/{mid}/selezioni'): 401,
    ('DELETE', '/api/me/sports/{slug}/mercati/{mid}/selezioni/{sid}'): 401,
    # Le sorgenti squadre (#34, pezzo 1): sessione come mercati e parser, corpo
    # letto a mano DOPO il controllo (401 prima del 422, stessa guardia).
    ('GET', '/api/me/sorgenti-squadre'): 401,
    ('POST', '/api/me/sorgenti-squadre'): 401,
    ('PATCH', '/api/me/sorgenti-squadre/{sid}'): 401,
    ('DELETE', '/api/me/sorgenti-squadre/{sid}'): 401,
    ('GET', '/api/me/competizioni'): 401,
    ('POST', '/api/me/competizioni'): 401,
    ('GET', '/api/me/competizioni/{cid}'): 401,
    ('DELETE', '/api/me/competizioni/{cid}'): 401,
    ('POST', '/api/me/competizioni/{cid}/squadre'): 401,
    ('DELETE', '/api/me/competizioni/{cid}/squadre/{sid}'): 401,
    ('GET', '/api/me/competizioni/{cid}/alias/{sid}'): 401,
    ('PUT', '/api/me/competizioni/{cid}/alias/{sid}'): 401,
}

# I MOUNT sono un'altra cosa dalle rotte, e vanno dichiarati a parte: non hanno
# `methods`, quindi l'enumeratore sotto li salterebbe — e li saltava. Segnalato da
# CodeRabbit, ed era un buco vero nella guardia: `/app` serve file statici senza
# autenticazione e nessun test lo verificava, ne' in un senso ne' nell'altro.
#
# Elencati per percorso esatto, con la stessa logica delle rotte: un mount nuovo
# non compare qui e il test resta rosso finche' qualcuno non dichiara che vuole
# esporlo. Un mount e' proprio il caso in cui esporre per sbaglio e' facile —
# basta puntare a una cartella sbagliata.
MOUNT_PUBBLICI = {'/app'}


@pytest.fixture(autouse=True)
def _ambiente_pulito(monkeypatch):
    """Stesso motivo della fixture gemella in `test_csv_contract.py`.

    L'handler di startup legge `os.environ` DIRETTAMENTE, quindi le variabili
    pericolose vanno rimosse da questo processo e non solo dalle costanti del
    modulo. La lista e' quella di `tests/ambiente.py`: una seconda divergerebbe.
    """
    for chiave in CHIAVI_PERICOLOSE:
        monkeypatch.delenv(chiave, raising=False)
    monkeypatch.setattr(main, 'TOKEN', TOKEN_DI_PROVA)


# ------------------------------------------------------- la funzione, da sola

def test_auth_accetta_il_token_giusto():
    main.auth(TOKEN_DI_PROVA)  # non deve sollevare


@pytest.mark.parametrize('sbagliato', [
    'token-diverso',
    TOKEN_DI_PROVA + 'x',        # prefisso giusto, non basta
    TOKEN_DI_PROVA.upper(),      # il confronto e' sensibile alle maiuscole
    '',
    None,
])
def test_auth_rifiuta_tutto_cio_che_non_e_il_token(sbagliato):
    with pytest.raises(main.HTTPException) as e:
        main.auth(sbagliato)
    assert e.value.status_code == 401, e.value.detail


def test_auth_RIFIUTA_quando_il_token_non_e_configurato(monkeypatch):
    """Il fail-closed: e- il test che era rosso prima della patch.

    Misurato sul codice vecchio: con `main.TOKEN = ''` sia una richiesta senza
    token sia una con un token inventato PASSAVANO senza sollevare. Dieci rotte
    pubbliche, e il modo di arrivarci era cancellare una variabile.

    503 e non 401 perche- le due condizioni sono diverse e vanno distinte in un
    log: 401 dice «la tua chiave e- sbagliata», 503 dice «questo servizio non e-
    configurato». Chi legge il secondo deve andare su Railway, non cercare il
    token giusto.
    """
    monkeypatch.setattr(main, 'TOKEN', '')
    for token in (None, '', 'qualunque-cosa', TOKEN_DI_PROVA):
        with pytest.raises(main.HTTPException) as e:
            main.auth(token)
        assert e.value.status_code == 503, (
            f'con CSV_ACCESS_TOKEN assente, il token {token!r} e- stato ACCETTATO '
            f'(oppure rifiutato con {e.value.status_code} invece di 503)'
        )


@pytest.mark.parametrize('configurato', [True, False], ids=['401', '503'])
def test_il_messaggio_di_errore_non_contiene_il_token(configurato, monkeypatch):
    """Il rifiuto non deve insegnare niente a chi lo riceve.

    `detail` non puo- contenere ne- il token atteso ne- quello ricevuto: per
    differenza si impara, e la risposta finisce nei log di chiunque stia in mezzo.

    Verificato su ENTRAMBI i rami. La prima versione copriva solo il 401, e il 503
    e- il ramo nuovo di questa patch — cioe- proprio quello dove una regressione
    non avrebbe incontrato nessun test. Segnalato da Sourcery.
    """
    if not configurato:
        monkeypatch.setattr(main, 'TOKEN', '')
    with pytest.raises(main.HTTPException) as e:
        main.auth('tentativo-di-indovinare')
    atteso = 401 if configurato else 503
    assert e.value.status_code == atteso
    detail = str(e.value.detail)
    assert TOKEN_DI_PROVA not in detail, f'il token atteso e- nel messaggio: {detail!r}'
    assert 'tentativo-di-indovinare' not in detail, f'il token ricevuto e- nel messaggio: {detail!r}'


def test_un_token_non_ASCII_da_401_e_non_un_500():
    """Il confronto a tempo costante lavora sui BYTE, e non e- un dettaglio.

    `secrets.compare_digest` solleva `TypeError` su una stringa non ASCII. Passando
    le stringhe cosi- come arrivano, un token con un accento — che un utente puo-
    inviare per errore o di proposito — diventerebbe un `TypeError` non gestito,
    cioe- un **500** invece di un 401: un modo per far scrivere una traccia nei log
    del server con un solo parametro di query.

    E- il caso limite introdotto dalla correzione del rilievo di Fable 5 sul
    timing, quindi il test nasce insieme a quella correzione.
    """
    for esotico in ('tokèn-con-accento', 'токен', '🔑'):
        with pytest.raises(main.HTTPException) as e:
            main.auth(esotico)
        assert e.value.status_code == 401, (
            f'il token {esotico!r} ha prodotto {e.value.status_code} invece di 401: '
            f'il confronto non sta lavorando sui byte'
        )


def test_il_confronto_del_token_e_a_tempo_costante():
    """Guardia STRUTTURALE, e va detto che lo e-.

    Un confronto a tempo costante non ha un comportamento osservabile diverso da
    `!=`: qualunque test sui valori passa con entrambi. L'unica verifica possibile
    e- sul sorgente, e serve a impedire che qualcuno «semplifichi» tornando a `!=`
    — che e- la forma in cui il codice e- vissuto finora.
    """
    import inspect as _i
    sorgente = _i.getsource(main.auth)
    assert 'compare_digest' in sorgente, (
        'auth() non usa piu- secrets.compare_digest: il confronto e- tornato a '
        'uscire al primo carattere diverso, e il tempo di risposta racconta quanti '
        'caratteri iniziali erano giusti'
    )


# --------------------------------------------- ogni rotta protetta, via HTTP

@pytest.fixture(scope='module')
def servizio_con_token(tmp_path_factory):
    """Il relay come in produzione: il token c'e-, e le richieste senza vanno rifiutate.

    Il token si passa DI PROPOSITO: `ambiente_di_servizio` toglie quello del
    proprietario per eredita-, questo e- il nostro e vale solo qui.
    """
    with relay_avviato(tmp_path_factory.mktemp('auth-con'),
                       CSV_ACCESS_TOKEN=TOKEN_DI_PROVA) as base:
        yield base


@pytest.fixture(scope='module')
def servizio_senza_token(tmp_path_factory):
    """Il relay mal configurato: la variabile manca del tutto.

    E- lo stato in cui si arriva cancellando `CSV_ACCESS_TOKEN` dalla dashboard, e
    fino a questa patch era lo stato in cui tutto era scrivibile da chiunque.
    """
    with relay_avviato(tmp_path_factory.mktemp('auth-senza')) as base:
        yield base


def _valore_finto(campo):
    """Un valore del tipo giusto per il campo, cosi- il body resta valido.

    Oggi i tre modelli di `main.py` hanno solo campi `str`, quindi `'x'` bastava.
    Ma questa guardia esiste per gli endpoint FUTURI — e' il suo scopo dichiarato —
    e un modello con un campo `int` renderebbe il body invalido: FastAPI
    risponderebbe 422 e il test fallirebbe per validazione invece che per
    autenticazione. Segnalato da Sourcery.
    """
    tipo = campo.annotation
    for atteso, valore in ((bool, True), (int, 1), (float, 1.0), (str, 'x')):
        if tipo is atteso:
            return valore
    return 'x'  # tipo non previsto: ci pensa il controllo sul 422


def _corpo_finto(endpoint):
    """Un body VALIDO per l'endpoint, se ne vuole uno.

    Serve perche- FastAPI valida il body PRIMA di eseguire la funzione: un POST
    senza body darebbe 422, e un 422 mascherebbe l'assenza del controllo sul
    token facendo passare il test per il motivo sbagliato.
    """
    for p in inspect.signature(endpoint).parameters.values():
        ann = p.annotation
        if inspect.isclass(ann) and issubclass(ann, BaseModel):
            return {nome: _valore_finto(campo) for nome, campo in ann.model_fields.items()}
    return None


def _tutte_le_rotte():
    """Ogni `(metodo, path, endpoint)` dell'app, senza filtri.

    Estratta perche' la usano in tre: `_rotte_protette`, il test che verifica le rotte
    con autenticazione propria, e quello che conta le tre categorie. Tenerne tre copie
    e- la duplicazione che la regola 3 vieta, e la divergenza sarebbe silenziosa —
    ognuna salterebbe rotte diverse.
    """
    for r in main.app.routes:
        metodi = getattr(r, 'methods', None)
        endpoint = getattr(r, 'endpoint', None)
        if not metodi or endpoint is None:
            continue  # Mount di StaticFiles e simili: nessun metodo
        if r.path.startswith(('/openapi', '/docs', '/redoc')):
            continue  # generate da FastAPI, non nostre
        for metodo in sorted(metodi - {'HEAD', 'OPTIONS'}):
            yield metodo, r.path, endpoint


def _rotte_protette():
    """Ogni rotta dell'app che non e- dichiarata pubblica in `ROTTE_PUBBLICHE`.

    Enumerata da `main.app.routes`, non scritta a mano: un endpoint NUOVO che si
    dimentica `auth()` fa diventare rosso questo test, che e- la meta- del suo
    valore. Un elenco a mano coprirebbe solo i dodici di oggi.
    """
    for metodo, path, endpoint in _tutte_le_rotte():
        if (metodo, path) in ROTTE_PUBBLICHE:
            continue
        if (metodo, path) in ROTTE_CON_AUTENTICAZIONE_PROPRIA:
            continue  # verificate dal test dedicato, con il loro codice atteso
        yield metodo, path, endpoint


def _chiama(base, metodo, path, endpoint, token=None):
    """Esegue la richiesta e restituisce `(stato, corpo, intestazioni)`, senza sollevare.

    Le intestazioni servono perche' un cookie di sessione sta in `Set-Cookie` e non nel
    corpo: senza, l'asserzione che verifica «una risposta di rifiuto non emette cookie»
    cercava nel posto sbagliato e passava sempre.
    """
    # I parametri di percorso non esistono: `auth()` viene prima della ricerca nel
    # database, quindi un nome inventato deve dare 401 e non 404 — e il fatto che
    # dia 401 dimostra proprio quell'ordine, cioe- che il servizio non rivela
    # quali profili esistono a chi non e- autenticato.
    concreto = re.sub(r'\{[^}]+\}', 'NON-ESISTE', path)
    url = f'{base}{concreto}'
    if token is not None:
        # Percent-encoding: `TOKEN_DI_PROVA` non ha caratteri speciali, ma un token
        # con `&`, `#`, `+` o uno spazio arriverebbe diverso nella query e uguale
        # nell'header, e i due percorsi di autenticazione non concorderebbero.
        # Segnalato da CodeRabbit.
        url += '?token=' + urllib.parse.quote(token, safe='')
    corpo = _corpo_finto(endpoint)
    intestazioni = {}
    dati = None
    if corpo is not None:
        dati = json.dumps(corpo).encode('utf-8')
        intestazioni['Content-Type'] = 'application/json'
    if token is not None:
        intestazioni['X-Admin-Token'] = token
    req = urllib.request.Request(url, data=dati, headers=intestazioni, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 - loopback
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def _e_errore_di_validazione(corpo: bytes) -> bool:
    """Distingue il 422 di FastAPI da un 422 dell'applicazione.

    Servono entrambi i casi e sono cose opposte:

    - FastAPI risponde 422 quando il BODY non passa la validazione, prima di
      eseguire la funzione: la rotta non e- stata esercitata e un test che lo
      ignorasse passerebbe senza controllare niente. `detail` e- una LISTA di
      errori strutturati;
    - `main.py` risponde 422 quando il messaggio non e- riconosciuto dal parser
      (riga «Messaggio non riconosciuto da questo parser»). E- logica applicativa
      raggiunta DOPO `auth()`, quindi e- la prova che l'autenticazione e- passata.
      `detail` e- una STRINGA.

    Il primo giro di questo controllo li confondeva e segnalava
    `POST /api/test-message` come body invalido: il body era valido, era il
    messaggio finto `'x'` a non combaciare col parser predefinito.
    """
    try:
        dettaglio = json.loads(corpo).get('detail')
    except (ValueError, AttributeError):
        return True  # non e- JSON: non sappiamo dire, meglio rumoroso
    return isinstance(dettaglio, list)


def test_ogni_rotta_protetta_rifiuta_una_richiesta_senza_token(servizio_con_token):
    """La guardia che la suite non aveva.

    Criterio di accettazione, che e- la misura del difetto al contrario: se
    qualcuno rimuove `auth()` da un endpoint, questo test deve diventare rosso.
    Prima esisteva solo la misura diretta — otto `auth()` sostituite con `pass`,
    144 passed — cioe- la dimostrazione che nessuna guardia esisteva.
    """
    rotte = list(_rotte_protette())
    assert len(rotte) >= 10, (
        f'trovate solo {len(rotte)} rotte da proteggere: l-enumerazione non sta '
        f'guardando l-app vera, e il test non starebbe controllando niente'
    )
    aperte = []
    for metodo, path, endpoint in rotte:
        stato, _, _ = _chiama(servizio_con_token, metodo, path, endpoint)
        if stato != 401:
            aperte.append(f'{metodo} {path} -> {stato}')
    assert not aperte, (
        'rotte che NON rifiutano una richiesta senza token (atteso 401):\n  '
        + '\n  '.join(aperte)
    )


def test_le_rotte_protette_accettano_il_token_giusto(servizio_con_token):
    """L'altra faccia: il fail-closed non deve chiudere anche cio- che funziona.

    Senza questo, la guardia qui sopra passerebbe anche con un `auth()` che
    rifiuta SEMPRE — e un servizio che rifiuta tutto e- rotto, non sicuro.
    Non si asserisce un 200: `DELETE /api/parsers/NON-ESISTE` da- 404 ed e-
    corretto. Si asserisce che il rifiuto per autenticazione non c'e- piu-.
    """
    negati, invalidi = [], []
    for metodo, path, endpoint in _rotte_protette():
        stato, corpo, _ = _chiama(servizio_con_token, metodo, path, endpoint,
                                  token=TOKEN_DI_PROVA)
        if stato in (401, 503):
            negati.append(f'{metodo} {path} -> {stato}')
        elif stato == 422 and _e_errore_di_validazione(corpo):
            invalidi.append(f'{metodo} {path}')
    # Il 422 va nominato invece di essere trattato come «non rifiutato»: FastAPI lo
    # restituisce quando il body non passa la validazione, cioe- PRIMA di eseguire
    # la funzione — quindi qui passerebbe in silenzio mentre la rotta non e- stata
    # esercitata affatto. E- il punto sollevato da Sourcery su `_corpo_finto`, e la
    # difesa vera non e- indovinare i tipi: e- accorgersi che il body non e- valido.
    assert not invalidi, (
        'body finto rifiutato con 422: queste rotte non sono state esercitate e il '
        'test starebbe passando senza controllare niente. Aggiornare _valore_finto '
        f'per i tipi del modello:\n  ' + '\n  '.join(invalidi)
    )
    assert not negati, (
        'rotte che rifiutano ANCHE il token giusto:\n  ' + '\n  '.join(negati)
    )


@pytest.mark.parametrize('nome_fixture', ['servizio_con_token', 'servizio_senza_token'])
def test_le_rotte_pubbliche_restano_pubbliche(nome_fixture, request):
    """`/health` deve rispondere anche a servizio MAL CONFIGURATO.

    E- il canale con cui il proprietario scopre il guasto: se il fail-closed
    chiudesse anche questo, la diagnosi diventerebbe impossibile proprio quando
    serve.

    Girava sul solo servizio configurato, cioe- non verificava la frase che il suo
    docstring dichiarava. Segnalato da CodeRabbit, ed e- la classe «il test dice
    una cosa e ne controlla un'altra»: ora gira su entrambe le configurazioni.
    """
    base = request.getfixturevalue(nome_fixture)
    for path in ('/', '/health'):
        with urllib.request.urlopen(f'{base}{path}', timeout=10) as r:  # noqa: S310 - loopback
            assert r.status == 200, f'{path} non risponde piu- senza token ({nome_fixture})'


@pytest.mark.parametrize('nome_fixture', ['servizio_con_token', 'servizio_senza_token'])
def test_i_mount_sono_dichiarati_e_app_resta_pubblico(nome_fixture, request):
    """I mount non hanno `methods`, quindi l'enumeratore delle rotte li salta.

    Buco vero nella guardia, segnalato da CodeRabbit: `/app` serve `web/` senza
    autenticazione e nessun test lo verificava. Qui si verificano due cose opposte,
    ed e- il punto:

    1. ogni mount dell'app e- DICHIARATO in `MOUNT_PUBBLICI` — un mount nuovo non
       compare e il test resta rosso finche- qualcuno non dice che vuole esporlo;
    2. `/app` risponde davvero senza token, anche a servizio mal configurato,
       perche- e- il prototipo che si mostra ai clienti e chiuderlo per sbaglio
       sarebbe una regressione silenziosa del prodotto.
    """
    montati = {r.path for r in main.app.routes
               if getattr(r, 'methods', None) is None and hasattr(r, 'app')}
    assert montati, (
        'nessun mount trovato: `web/` non esiste in questo checkout oppure il '
        'criterio di scoperta non funziona piu-, e il test non controlla niente'
    )
    assert montati == MOUNT_PUBBLICI, (
        f'mount non dichiarati: {sorted(montati - MOUNT_PUBBLICI)}; '
        f'dichiarati e non piu- presenti: {sorted(MOUNT_PUBBLICI - montati)}'
    )

    base = request.getfixturevalue(nome_fixture)
    with urllib.request.urlopen(f'{base}/app/', timeout=10) as r:  # noqa: S310 - loopback
        assert r.status == 200, '/app non risponde piu- senza token'


# ------------------------------------- il servizio senza token configurato

def test_senza_token_configurato_il_feed_e_CHIUSO(servizio_senza_token):
    """Fail-closed sul percorso reale, non solo nella funzione.

    Prima della patch questa richiesta restituiva 200 con il CSV: il feed di
    XTrader era leggibile da chiunque conoscesse l'URL. Ora e- 503, e il 503 dice
    la verita- — non «non sei autorizzato», ma «questo servizio non e-
    configurato».
    """
    for url in ('/xtrader.csv', '/xtrader.csv?token=qualunque-cosa'):
        stato = None
        try:
            with urllib.request.urlopen(f'{servizio_senza_token}{url}', timeout=10) as r:  # noqa: S310 - loopback
                stato = r.status
        except urllib.error.HTTPError as e:
            stato = e.code
        assert stato == 503, f'{url} risponde {stato} invece di 503: il feed e- APERTO'


def test_senza_token_configurato_anche_le_api_di_scrittura_sono_chiuse(servizio_senza_token):
    """Le sei rotte che SCRIVONO sono la parte grave del difetto.

    Un feed leggibile e- una perdita di informazione; un `POST /api/profiles`
    aperto lascia sovrascrivere il profilo di chiunque, e un
    `POST /api/test-message` aperto inietta un segnale nel CSV che XTrader legge.
    """
    for metodo, path, endpoint in _rotte_protette():
        stato, _, _ = _chiama(servizio_senza_token, metodo, path, endpoint,
                              token='qualunque-cosa')
        assert stato == 503, f'{metodo} {path} risponde {stato} invece di 503'


def test_health_dichiara_che_il_token_non_e_configurato(servizio_senza_token):
    """Un guasto silenzioso e- il difetto, non l'assenza del token.

    `main.py` lo scrive gia- a proposito del verificatore CSV: «un controllo che
    nessuno legge non e- un controllo». Vale identico qui — senza questa riga la
    configurazione mancante si scoprirebbe solo notando che tutto risponde 503.

    E `status` diventa `degraded`, a differenza degli scarti di consegna: quelli
    si risolvono da se- col TTL, questo no. Non si ripara senza un intervento.
    """
    with urllib.request.urlopen(f'{servizio_senza_token}/health', timeout=10) as r:  # noqa: S310 - loopback
        dati = json.loads(r.read())
    assert dati.get('auth') == 'non configurato', f'/health non lo segnala: {dati}'
    assert dati.get('status') == 'degraded', f'status dovrebbe essere degraded: {dati}'


def test_health_non_accusa_l_auth_quando_il_token_c_e(servizio_con_token):
    """L'altra faccia: la spia non deve accendersi per un asse che sta bene.

    Questo test asseriva `status == 'ok'`. Non lo puo- piu- fare, e la ragione e-
    corretta: da quando «nessun bot» fa scattare `degraded` — segnalato da Fugu
    Ultra, perche- un'istanza che rifiuta ogni consegna non e- sana — un servizio
    di test non ha un bot e quindi e- degradato **per quel motivo**. L'aggregato
    «tutti gli assi a posto» richiede una `setWebhook` riuscita verso Telegram, che
    un sottoprocesso di test non ha e non deve avere: quell'asserzione vive in
    processo, in `test_con_tutti_e_tre_gli_assi_a_posto_health_dice_ok`.

    Quello che questo test puo- e deve ancora dimostrare e- che l'asse `auth` non
    viene accusato quando il token c'e-. Riscriverlo cosi- e- piu- preciso di
    prima, non meno: guarda l'asse di cui parla il file, invece dell'aggregato.
    """
    with urllib.request.urlopen(f'{servizio_con_token}/health', timeout=10) as r:  # noqa: S310 - loopback
        dati = json.loads(r.read())
    assert dati.get('auth') == 'ok', dati
    # E il motivo della degradazione, se c'e-, non deve essere l'autenticazione.
    if dati.get('status') == 'degraded':
        assert dati.get('webhook') == 'chiuso senza bot', (
            f'degradato per un motivo che non e- il bot mancante: {dati}'
        )


def test_health_non_contiene_il_token(servizio_con_token):
    """`/health` e- senza autenticazione: non puo- dire piu- di «configurato o no»."""
    with urllib.request.urlopen(f'{servizio_con_token}/health', timeout=10) as r:  # noqa: S310 - loopback
        corpo = r.read()
    assert TOKEN_DI_PROVA.encode() not in corpo, f'il token e- in /health: {corpo!r}'


# ------------------------------- le rotte con autenticazione propria (PR 6)

@pytest.mark.parametrize(('metodo', 'path'), sorted(ROTTE_CON_AUTENTICAZIONE_PROPRIA))
def test_le_rotte_di_login_rifiutano_chi_non_ha_la_PROPRIA_credenziale(
        metodo, path, servizio_con_token):
    """Non usano `CSV_ACCESS_TOKEN`, ma non per questo sono aperte.

    Il rischio che questo test copre e- l'abbreviazione comoda: dichiarare una rotta di
    login «pubblica» perche' non pretende il token del feed, e con quel gesto spegnere
    la guardia su di lei. Qui l'atteso e- scritto per ciascuna, e il fatto che una rotta
    di login **rifiuti** e- verificato, non assunto.

    La richiesta parte con il token del feed VALIDO, cosi- l'eventuale successo non
    potrebbe essere spiegato con «mancava il token»: se una di queste rispondesse 200,
    starebbe aprendo una sessione a chi non ha ne' firma ne' password.
    """
    atteso = ROTTE_CON_AUTENTICAZIONE_PROPRIA[(metodo, path)]
    endpoint = next(e for m, p, e in _tutte_le_rotte() if (m, p) == (metodo, path))
    stato, corpo, intestazioni = _chiama(servizio_con_token, metodo, path, endpoint,
                                        token=TOKEN_DI_PROVA)
    assert stato == atteso, (
        f'{metodo} {path} risponde {stato} invece di {atteso}. Se e- 200, questa rotta '
        f'apre una sessione a chi non ha nessuna credenziale; il corpo era: {corpo[:200]!r}')
    # Sull'header `Set-Cookie`, non sul corpo: il cookie vive la-, quindi cercarlo nel
    # corpo passava anche se la rotta lo avesse impostato davvero. Era la mia asserzione
    # che non asseriva niente, segnalata da CodeRabbit sulla PR #23 — e una guardia
    # vacua e- peggio di nessuna guardia, perche- si legge come copertura.
    emessi = intestazioni.get_all('Set-Cookie') or []
    assert not any(main.NOME_COOKIE in c for c in emessi), (
        f'{metodo} {path} ha messo un cookie di sessione in una risposta di rifiuto: '
        f'{emessi}')


def test_le_tre_categorie_di_rotte_coprono_TUTTE_le_rotte():
    """Nessuna rotta puo' sfuggire alla classificazione.

    E- il guardiano del guardiano: `_rotte_protette()` salta ciò che e' dichiarato
    pubblico o con autenticazione propria, quindi una voce aggiunta per comodita- a uno
    dei due insiemi **toglie** una rotta dal controllo. Questo test conta, e il totale
    deve tornare.

    Serve anche al caso opposto: una voce rimasta nei due insiemi dopo che la rotta e-
    stata cancellata farebbe credere coperta una cosa che non esiste piu-.
    """
    tutte = {(m, p) for m, p, _ in _tutte_le_rotte()}
    protette = {(m, p) for m, p, _ in _rotte_protette()}
    pubbliche = set(ROTTE_PUBBLICHE)
    proprie = set(ROTTE_CON_AUTENTICAZIONE_PROPRIA)

    assert protette | pubbliche | proprie == tutte, (
        'classificazione incompleta:\n'
        f'  rotte non classificate: {sorted(tutte - protette - pubbliche - proprie)}\n'
        f'  dichiarate ma inesistenti: {sorted((pubbliche | proprie) - tutte)}')
    doppie = (pubbliche & proprie) | (pubbliche & protette) | (proprie & protette)
    assert not doppie, f'rotte in due categorie insieme: {sorted(doppie)}'
