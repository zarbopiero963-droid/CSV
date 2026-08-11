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
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from pydantic import BaseModel

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE))

import main  # noqa: E402 - dopo l'inserimento del percorso
from tests.ambiente import (  # noqa: E402
    CHIAVI_PERICOLOSE, TOKEN_DI_PROVA, ambiente_di_servizio,
)

# Le rotte che sono pubbliche PER PROGETTO, elencate una per una. E' la parte
# importante del test sulle rotte: una rotta nuova non compare qui, quindi se
# dimentica `auth()` il test diventa rosso finche' qualcuno non dichiara
# esplicitamente che vuole esporla. Un allowlist per prefisso avrebbe coperto in
# silenzio tutto cio' che nasce sotto quel prefisso.
ROTTE_PUBBLICHE = {
    ('GET', '/'),                    # pagina di cortesia
    ('GET', '/health'),              # deve rispondere anche a servizio guasto
    ('POST', '/telegram/webhook'),   # la chiama Telegram: la protegge il filtro chat
}


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


def test_il_messaggio_di_errore_non_contiene_il_token():
    """Il rifiuto non deve insegnare niente a chi lo riceve.

    Vale per entrambi i rami: `detail` non puo- contenere ne- il token atteso ne-
    quello ricevuto, altrimenti un attaccante impara per differenza e il token
    finisce nei log di chiunque intercetti la risposta.
    """
    with pytest.raises(main.HTTPException) as e:
        main.auth('tentativo-di-indovinare')
    detail = str(e.value.detail)
    assert TOKEN_DI_PROVA not in detail, f'il token atteso e- nel messaggio: {detail!r}'
    assert 'tentativo-di-indovinare' not in detail, f'il token ricevuto e- nel messaggio: {detail!r}'


# --------------------------------------------- ogni rotta protetta, via HTTP

def _porta_libera() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _avvia(tmp_path_factory, nome, **extra):
    """Avvia il relay con l'ambiente ripulito, piu- le variabili chieste.

    `ambiente_di_servizio` toglie le chiavi pericolose per eredita-; passarne una
    di PROPOSITO resta possibile e va scritto, che e- esattamente il caso qui: il
    token di prova serve, quello del proprietario no.
    """
    porta = _porta_libera()
    db = tmp_path_factory.mktemp(nome) / 'signals.db'
    proc = subprocess.Popen(
        [sys.executable, '-m', 'uvicorn', 'main:app', '--host', '127.0.0.1',
         '--port', str(porta), '--log-level', 'warning'],
        cwd=RADICE, env=ambiente_di_servizio(DB_PATH=str(db), **extra),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    base = f'http://127.0.0.1:{porta}'
    scaduto = time.monotonic() + 30
    while time.monotonic() < scaduto:
        if proc.poll() is not None:
            proc_out = proc.stdout.read()[-2000:]
            pytest.fail(f'uvicorn e- morto durante l-avvio:\n{proc_out}')
        try:
            with urllib.request.urlopen(f'{base}/health', timeout=1) as r:
                if r.status == 200:
                    return proc, base
        except (urllib.error.URLError, OSError):
            time.sleep(0.2)
    proc.terminate()
    pytest.fail('uvicorn non ha risposto su /health entro 30 s')


def _spegni(proc):
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope='module')
def servizio_con_token(tmp_path_factory):
    """Il relay come in produzione: il token c'e-, e le richieste senza vanno rifiutate."""
    proc, base = _avvia(tmp_path_factory, 'auth-con', CSV_ACCESS_TOKEN=TOKEN_DI_PROVA)
    try:
        yield base
    finally:
        _spegni(proc)


@pytest.fixture(scope='module')
def servizio_senza_token(tmp_path_factory):
    """Il relay mal configurato: la variabile manca del tutto.

    E- lo stato in cui si arriva cancellando `CSV_ACCESS_TOKEN` dalla dashboard, e
    fino a questa patch era lo stato in cui tutto era scrivibile da chiunque.
    """
    proc, base = _avvia(tmp_path_factory, 'auth-senza')
    try:
        yield base
    finally:
        _spegni(proc)


def _corpo_finto(endpoint):
    """Un body VALIDO per l'endpoint, se ne vuole uno.

    Serve perche- FastAPI valida il body PRIMA di eseguire la funzione: un POST
    senza body darebbe 422, e un 422 mascherebbe l'assenza del controllo sul
    token facendo passare il test per il motivo sbagliato.
    """
    for p in inspect.signature(endpoint).parameters.values():
        ann = p.annotation
        if inspect.isclass(ann) and issubclass(ann, BaseModel):
            return {nome: 'x' for nome in ann.model_fields}
    return None


def _rotte_protette():
    """Ogni rotta dell'app che non e- dichiarata pubblica in `ROTTE_PUBBLICHE`.

    Enumerata da `main.app.routes`, non scritta a mano: un endpoint NUOVO che si
    dimentica `auth()` fa diventare rosso questo test, che e- la meta- del suo
    valore. Un elenco a mano coprirebbe solo i dodici di oggi.
    """
    for r in main.app.routes:
        metodi = getattr(r, 'methods', None)
        endpoint = getattr(r, 'endpoint', None)
        if not metodi or endpoint is None:
            continue  # Mount di StaticFiles e simili: nessun metodo
        if r.path.startswith(('/openapi', '/docs', '/redoc')):
            continue  # generate da FastAPI, non nostre
        for metodo in sorted(metodi - {'HEAD', 'OPTIONS'}):
            if (metodo, r.path) in ROTTE_PUBBLICHE:
                continue
            yield metodo, r.path, endpoint


def _chiama(base, metodo, path, endpoint, token=None):
    """Esegue la richiesta e restituisce il codice di stato, senza sollevare."""
    # I parametri di percorso non esistono: `auth()` viene prima della ricerca nel
    # database, quindi un nome inventato deve dare 401 e non 404 — e il fatto che
    # dia 401 dimostra proprio quell'ordine, cioe- che il servizio non rivela
    # quali profili esistono a chi non e- autenticato.
    concreto = re.sub(r'\{[^}]+\}', 'NON-ESISTE', path)
    url = f'{base}{concreto}'
    if token is not None:
        url += f'?token={token}'
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
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


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
        stato = _chiama(servizio_con_token, metodo, path, endpoint)
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
    negati = []
    for metodo, path, endpoint in _rotte_protette():
        stato = _chiama(servizio_con_token, metodo, path, endpoint, token=TOKEN_DI_PROVA)
        if stato in (401, 503):
            negati.append(f'{metodo} {path} -> {stato}')
    assert not negati, (
        'rotte che rifiutano ANCHE il token giusto:\n  ' + '\n  '.join(negati)
    )


def test_le_rotte_pubbliche_restano_pubbliche(servizio_con_token):
    """`/health` deve rispondere anche a servizio mal configurato.

    E- il canale con cui il proprietario scopre il guasto: se il fail-closed
    chiudesse anche questo, la diagnosi diventerebbe impossibile proprio quando
    serve.
    """
    for path in ('/', '/health'):
        with urllib.request.urlopen(f'{servizio_con_token}{path}', timeout=10) as r:
            assert r.status == 200, f'{path} non risponde piu- senza token'


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
            with urllib.request.urlopen(f'{servizio_senza_token}{url}', timeout=10) as r:
                stato = r.status
        except urllib.error.HTTPError as e:
            stato = e.code
        assert stato == 503, f'{url} risponde {stato} invece di 503: il feed e- APERTO'


def test_senza_token_configurato_anche_le_api_di_scrittura_sono_chiuse(servizio_senza_token):
    """Le sette rotte che SCRIVONO sono la parte grave del difetto.

    Un feed leggibile e- una perdita di informazione; un `POST /api/profiles`
    aperto lascia sovrascrivere il profilo di chiunque, e un
    `POST /api/test-message` aperto inietta un segnale nel CSV che XTrader legge.
    """
    for metodo, path, endpoint in _rotte_protette():
        stato = _chiama(servizio_senza_token, metodo, path, endpoint, token='qualunque-cosa')
        assert stato == 503, f'{metodo} {path} risponde {stato} invece di 503'


def test_health_dichiara_che_il_token_non_e_configurato(servizio_senza_token):
    """Un guasto silenzioso e- il difetto, non l'assenza del token.

    `main.py` lo scrive gia- a proposito del verificatore CSV: «un controllo che
    nessuno legge non e- un controllo». Vale identico qui — senza questa riga la
    configurazione mancante si scoprirebbe solo notando che tutto risponde 503.

    E `status` diventa `degraded`, a differenza degli scarti di consegna: quelli
    si risolvono da se- col TTL, questo no. Non si ripara senza un intervento.
    """
    with urllib.request.urlopen(f'{servizio_senza_token}/health', timeout=10) as r:
        dati = json.loads(r.read())
    assert dati.get('auth') == 'non configurato', f'/health non lo segnala: {dati}'
    assert dati.get('status') == 'degraded', f'status dovrebbe essere degraded: {dati}'


def test_health_dice_ok_quando_il_token_c_e(servizio_con_token):
    """L'altra faccia: la spia non deve essere sempre accesa.

    Una spia che sta accesa anche quando tutto va bene e- il modo piu- rapido per
    insegnare a ignorarla — la stessa ragione per cui gli scarti di consegna non
    fanno scattare `degraded`.
    """
    with urllib.request.urlopen(f'{servizio_con_token}/health', timeout=10) as r:
        dati = json.loads(r.read())
    assert dati.get('auth') == 'ok', dati
    assert dati.get('status') == 'ok', dati


def test_health_non_contiene_il_token(servizio_con_token):
    """`/health` e- senza autenticazione: non puo- dire piu- di «configurato o no»."""
    with urllib.request.urlopen(f'{servizio_con_token}/health', timeout=10) as r:
        corpo = r.read()
    assert TOKEN_DI_PROVA.encode() not in corpo, f'il token e- in /health: {corpo!r}'
