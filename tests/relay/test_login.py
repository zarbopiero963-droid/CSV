"""Login Telegram, accesso di emergenza con password, e sessioni.

Tre percorsi che decidono **chi sei** per il resto del servizio, quindi tre percorsi
dove un difetto non da' un errore: da' l'accesso a qualcun altro.

Cosa vincolano questi test, e perche' ognuno esiste:

- **la firma del Login Widget.** Telegram non manda un token: manda dei campi in
  chiaro piu' un `hash` HMAC-SHA256 calcolato con una chiave derivata dal token del
  bot. Chi non verifica quella firma accetta un `id` scritto da chiunque, cioe'
  accetta «sono Piero» da un estraneo. La verifica e' l'intero meccanismo;
- **l'eta' della firma.** Una firma valida resta valida per sempre se nessuno guarda
  `auth_date`: un URL di login copiato oggi funzionerebbe fra un anno;
- **la password di emergenza.** Sta come hash in `ADMIN_PASSWORD_HASH`, mai in
  chiaro, e se la variabile manca il percorso e' **disabilitato** — non aperto. E' la
  stessa regola fail-closed di `auth()`, scritta dopo aver misurato che dieci rotte
  diventavano pubbliche cancellando una variabile dalla dashboard;
- **la sessione.** Un cookie firmato che porta l'utente, la sua `session_version` e
  il momento di emissione. Venti minuti di inattivita' e scade; `session_version`
  incrementata nel database e i cookie vecchi non valgono piu'.

**E soprattutto**, il test che nessuno scriverebbe spontaneamente perche' non
protegge una funzione ma una NON-relazione: la sessione del sito non deve avere
niente a che fare con il token del feed. Se i due meccanismi venissero collegati,
ogni cliente perderebbe i segnali venti minuti dopo aver chiuso il browser — e
nessun test di login lo troverebbe, perche' il login funzionerebbe benissimo. E'
la trappola 2 della Issue #7, e vive in `test_la_sessione_scaduta_NON_tocca_il_feed`.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import sys
import time
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE))

import main  # noqa: E402 - dopo l'inserimento del percorso
from tests.ambiente import CHIAVI_PERICOLOSE, ambiente_di_supporto  # noqa: E402

# Un token di bot finto, con la forma di quelli veri (`<id>:<segreto>`) perche' la
# derivazione della chiave non deve dipendere dal contenuto.
BOT_FINTO = '123456789:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'

# L'ID Telegram del proprietario in queste prove. Non e' un segreto — chiunque
# riceva un messaggio lo conosce — ma decide chi e' l'amministratore.
ADMIN_FINTO = '987654321'


@pytest.fixture(autouse=True)
def _ambiente_pulito(monkeypatch):
    """Nessuna variabile della macchina entra in questi test.

    Stessa ragione delle fixture gemelle in `tests/relay/`: con il `.env` del
    proprietario caricato, un test che avvia il servizio ripunterebbe il webhook del
    bot vero, e l'esito dipenderebbe dalla macchina invece che dal codice.
    """
    for chiave in CHIAVI_PERICOLOSE:
        monkeypatch.delenv(chiave, raising=False)


def _firma_telegram(campi: dict, bot_token: str = BOT_FINTO) -> str:
    """La firma come la calcola Telegram, riscritta a mano invece che importata.

    Deliberatamente NON chiama la funzione del progetto: se la calcolasse con lo
    stesso codice che verifica, il test direbbe soltanto «la funzione e' coerente con
    se stessa» — e resterebbe verde anche se l'algoritmo fosse quello sbagliato.
    Questa e' la specifica di Telegram trascritta dalla documentazione:

        secret_key       = SHA256(bot_token)
        data_check_string = campi ordinati per chiave, 'k=v' uniti da \\n, senza `hash`
        hash             = HMAC_SHA256(data_check_string, secret_key)
    """
    stringa = '\n'.join(f'{k}={campi[k]}' for k in sorted(campi) if k != 'hash')
    chiave = hashlib.sha256(bot_token.encode()).digest()
    return hmac.new(chiave, stringa.encode(), hashlib.sha256).hexdigest()


def _dati_login(**extra) -> dict:
    """I campi che il Login Widget consegna, firmati correttamente."""
    campi = {
        'id': ADMIN_FINTO,
        'first_name': 'Piero',
        'username': 'piero',
        'auth_date': str(int(time.time())),
    }
    campi.update(extra)
    campi['hash'] = _firma_telegram(campi)
    return campi


# ---------------------------------------------------- la firma del Login Widget

def test_una_firma_VALIDA_viene_accettata(monkeypatch):
    monkeypatch.setattr(main, 'BOT_TOKEN', BOT_FINTO)
    assert main.verifica_login_telegram(_dati_login(), BOT_FINTO) is True


@pytest.mark.parametrize('manomissione', [
    {'id': '111'},                      # l'ID cambiato: «sono un altro»
    {'first_name': 'Marco'},            # un campo qualunque cambiato
    {'username': 'marco'},
])
def test_un_campo_CAMBIATO_dopo_la_firma_viene_rifiutato(manomissione):
    """La firma copre TUTTI i campi, non solo l'`id`.

    E- il punto del meccanismo: se coprisse solo l'identificativo, un estraneo
    potrebbe tenere la firma di un login vero e cambiarci il nome; se non coprisse
    l'identificativo, potrebbe dichiararsi chiunque.
    """
    dati = _dati_login()
    dati.update(manomissione)   # la firma resta quella di PRIMA
    assert main.verifica_login_telegram(dati, BOT_FINTO) is False


def test_una_firma_di_un_ALTRO_bot_viene_rifiutata():
    """La chiave deriva dal nostro token: la firma di un altro bot non vale."""
    campi = {'id': ADMIN_FINTO, 'first_name': 'Piero', 'auth_date': str(int(time.time()))}
    campi['hash'] = _firma_telegram(campi, bot_token='999:altro-bot-completamente')
    assert main.verifica_login_telegram(campi, BOT_FINTO) is False


def test_un_campo_AGGIUNTO_dopo_la_firma_viene_rifiutato():
    """Un campo in piu- cambia la `data_check_string`, quindi invalida la firma.

    Non e- pedanteria: se la verifica ignorasse i campi che non conosce, chi
    intercetta un login potrebbe aggiungerne uno che il codice futuro leggera- —
    `is_admin`, per esempio — tenendo una firma che resta valida.
    """
    dati = _dati_login()
    dati['photo_url'] = 'https://esempio/foto.jpg'
    assert main.verifica_login_telegram(dati, BOT_FINTO) is False


def test_senza_hash_viene_rifiutato():
    dati = _dati_login()
    del dati['hash']
    assert main.verifica_login_telegram(dati, BOT_FINTO) is False


def test_senza_bot_token_RIFIUTA_tutto():
    """Fail-closed: senza il token non c'e- modo di validare, quindi non si valida.

    La stessa forma di `webhook_secret`, che senza bot restituisce stringa vuota e
    fa rifiutare ogni consegna. Una serratura che si apre quando le togli la chiave
    e- il difetto corretto su `auth()` a luglio, e non va reintrodotto qui.
    """
    assert main.verifica_login_telegram(_dati_login(), '') is False


def test_una_firma_VECCHIA_viene_rifiutata():
    """`auth_date` fuori finestra: una firma valida non vale per sempre.

    Senza questo controllo l'URL di ritorno di un login riuscito resterebbe una
    credenziale valida a tempo indeterminato — copiabile da un log del browser, da
    una cronologia, da uno screenshot.
    """
    vecchio = str(int(time.time()) - main.ETA_MASSIMA_LOGIN - 60)
    assert main.verifica_login_telegram(_dati_login(auth_date=vecchio), BOT_FINTO) is False


def test_una_firma_del_FUTURO_viene_rifiutata():
    """`auth_date` in avanti: un orologio sbagliato non deve allargare la finestra.

    Accettare il futuro senza limiti significa accettare per sempre una firma con
    `auth_date` messo a mano nel 2030.
    """
    futuro = str(int(time.time()) + main.ETA_MASSIMA_LOGIN + 60)
    assert main.verifica_login_telegram(_dati_login(auth_date=futuro), BOT_FINTO) is False


def test_auth_date_NON_numerico_viene_rifiutato():
    """Un `auth_date` che non e- un numero non deve far sollevare: deve rifiutare."""
    assert main.verifica_login_telegram(_dati_login(auth_date='domani'), BOT_FINTO) is False


# -------------------------------------------- la password dell'accesso di emergenza

def test_la_password_giusta_viene_accettata():
    hash_salvato = main.hash_password('una password lunga e scelta da me')
    assert main.verifica_password_admin('una password lunga e scelta da me', hash_salvato) is True


@pytest.mark.parametrize('sbagliata', [
    'una password lunga e scelta da m',       # un carattere in meno
    'una password lunga e scelta da me ',     # uno spazio in piu-
    'Una password lunga e scelta da me',      # maiuscola diversa
    '',
])
def test_una_password_SBAGLIATA_viene_rifiutata(sbagliata):
    hash_salvato = main.hash_password('una password lunga e scelta da me')
    assert main.verifica_password_admin(sbagliata, hash_salvato) is False


def test_senza_ADMIN_PASSWORD_HASH_il_percorso_e_DISABILITATO():
    """Variabile assente → nessun accesso, non «qualunque accesso».

    E- la regola che `auth()` ha imparato a luglio: `if TOKEN and token != TOKEN` non
    fa niente quando la variabile e- vuota, e dieci rotte diventavano pubbliche
    cancellandola dalla dashboard. Qui la stessa forma non deve ricomparire.
    """
    for vuoto in ('', None):
        assert main.verifica_password_admin('qualunque cosa', vuoto) is False


def test_un_hash_MALFORMATO_rifiuta_invece_di_sollevare():
    """Una variabile scritta male e- un errore di configurazione, non un 500.

    Se il valore non ha la forma attesa la funzione deve dire «no», perche' un
    `raise` qui diventerebbe un 500 su una rotta di login — cioe' un modo di
    scoprire dall'esterno che la variabile e' malformata.
    """
    for rotto in ('non-un-hash', 'scrypt$soloUnPezzo', 'scrypt$$', 'md5$aaa$bbb'):
        assert main.verifica_password_admin('qualunque cosa', rotto) is False


def test_due_hash_della_STESSA_password_sono_DIVERSI():
    """Sale casuale: due hash della stessa password non coincidono.

    Senza sale, hash uguali rivelano password uguali, e un hash diventa
    riconoscibile da una tabella precalcolata.
    """
    uno = main.hash_password('la stessa password')
    due = main.hash_password('la stessa password')
    assert uno != due
    # Ed entrambi verificano quella password: il sale sta dentro il valore salvato.
    assert main.verifica_password_admin('la stessa password', uno) is True
    assert main.verifica_password_admin('la stessa password', due) is True


def test_l_hash_NON_contiene_la_password():
    """Il valore salvato non deve contenere la password in chiaro.

    Sembra ovvio e va vincolato: e- l'intera ragione per cui nella variabile va
    l'hash invece della password, cioe' che chi legge le Variables di Railway non
    possa entrare nel pannello.
    """
    salvato = main.hash_password('parolamagicasegreta')
    assert 'parolamagicasegreta' not in salvato


# ------------------------------------------------------------------ le sessioni

def test_una_sessione_APPENA_emessa_e_valida(monkeypatch):
    monkeypatch.setattr(main, 'SEGRETO_SESSIONE', 'un-segreto-di-prova')
    cookie = main.firma_sessione(utente=7, versione=1)
    assert main.leggi_sessione(cookie) == {'utente': 7, 'versione': 1}


def test_un_cookie_MANOMESSO_non_vale(monkeypatch):
    """Cambiare l'utente nel cookie non deve dare l'accesso a quell'utente.

    E- il difetto che rende un cookie di sessione una credenziale invece di una
    dichiarazione: senza firma, `utente=7` diventa `utente=8` con un editor.
    """
    monkeypatch.setattr(main, 'SEGRETO_SESSIONE', 'un-segreto-di-prova')
    cookie = main.firma_sessione(utente=7, versione=1)
    manomesso = cookie.replace('7', '8', 1)
    assert manomesso != cookie
    assert main.leggi_sessione(manomesso) is None


def test_un_cookie_firmato_con_un_ALTRO_segreto_non_vale(monkeypatch):
    monkeypatch.setattr(main, 'SEGRETO_SESSIONE', 'il-segreto-di-un-altro-servizio')
    estraneo = main.firma_sessione(utente=7, versione=1)
    monkeypatch.setattr(main, 'SEGRETO_SESSIONE', 'il-nostro-segreto')
    assert main.leggi_sessione(estraneo) is None


def test_una_sessione_INATTIVA_da_venti_minuti_scade(monkeypatch):
    """I venti minuti della Issue #7, misurati sul valore dentro il cookie."""
    monkeypatch.setattr(main, 'SEGRETO_SESSIONE', 'un-segreto-di-prova')
    scaduta = main.firma_sessione(utente=7, versione=1,
                                  emessa=time.time() - main.INATTIVITA_MASSIMA - 1)
    assert main.leggi_sessione(scaduta) is None
    # Un secondo prima del limite invece vale ancora: il confine e- dove dico.
    viva = main.firma_sessione(utente=7, versione=1,
                               emessa=time.time() - main.INATTIVITA_MASSIMA + 5)
    assert main.leggi_sessione(viva) == {'utente': 7, 'versione': 1}


def test_senza_SEGRETO_SESSIONE_nessuna_sessione_e_valida(monkeypatch):
    """Fail-closed anche qui: senza segreto non si firma e non si accetta niente."""
    monkeypatch.setattr(main, 'SEGRETO_SESSIONE', 'un-segreto-di-prova')
    cookie = main.firma_sessione(utente=7, versione=1)
    monkeypatch.setattr(main, 'SEGRETO_SESSIONE', '')
    assert main.leggi_sessione(cookie) is None
    assert main.firma_sessione(utente=7, versione=1) == ''


@pytest.mark.parametrize('spazzatura', ['', 'niente', 'a.b.c.d', '7.1.abc.zzz', '...'])
def test_un_cookie_SPAZZATURA_rifiuta_invece_di_sollevare(spazzatura, monkeypatch):
    """Un cookie arriva dal browser, quindi lo scrive il mittente.

    Deve restituire `None`, non far sollevare: un `ValueError` non gestito su una
    rotta autenticata e- un 500 pilotabile dall'esterno.
    """
    monkeypatch.setattr(main, 'SEGRETO_SESSIONE', 'un-segreto-di-prova')
    assert main.leggi_sessione(spazzatura) is None


# ============================================================ il flusso, via HTTP
#
# Da qui i test parlano al servizio vero attraverso HTTP, perche' cio' che verificano
# non e' una funzione ma il comportamento composto: il cookie che il browser riceve, la
# sessione che apre, e — soprattutto — cio' che la sessione **non** deve toccare.

import json          # noqa: E402 - gli import del blocco HTTP, tenuti accanto al blocco
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402

from tests.ambiente import TOKEN_DI_PROVA  # noqa: E402
from tests.servizio import relay_avviato  # noqa: E402

# Nessuna uscita verso Internet: con `TELEGRAM_BOT_TOKEN` nell'ambiente lo startup
# registra il webhook, e senza un proxy morto la chiamata partirebbe davvero verso
# `PUBLIC_URL`. Stesso accorgimento di `tests/relay/test_parse_message.py`, e lo stesso
# motivo: un test non deve toccare il bot vero di nessuno.
PROXY_MORTO = 'http://127.0.0.1:1'

AMBIENTE_DEL_SERVIZIO = {
    'CSV_ACCESS_TOKEN': TOKEN_DI_PROVA,
    'TELEGRAM_BOT_TOKEN': BOT_FINTO,
    'TELEGRAM_ADMIN_ID': ADMIN_FINTO,
    'PUBLIC_URL': 'https://non-esiste.invalid',
    'HTTPS_PROXY': PROXY_MORTO,
    'https_proxy': PROXY_MORTO,
}

# Il segreto delle sessioni ricalcolato con la stessa formula del servizio: serve per
# FIRMARE in-processo un cookie che il sottoprocesso accettera'. Ricalcolarlo qui invece
# di importarlo e' deliberato — se lo importassi, un cambio di formula resterebbe
# invisibile perche' i due lati cambierebbero insieme.
SEGRETO_ATTESO = hashlib.sha256(('betrelay-sessione-v1:' + BOT_FINTO).encode()).hexdigest()


@pytest.fixture
def servizio(tmp_path, monkeypatch):
    """Un relay per test, col bot e con l'ID admin, senza uscite verso l'esterno."""
    monkeypatch.setattr(main, 'SEGRETO_SESSIONE', SEGRETO_ATTESO)
    with relay_avviato(tmp_path, **AMBIENTE_DEL_SERVIZIO) as base:
        yield base


def _chiama(base, metodo, path, corpo=None, cookie=None, token=None):
    """Richiesta HTTP che restituisce `(stato, corpo, intestazioni)` senza sollevare."""
    url = f'{base}{path}'
    if token:
        url += ('&' if '?' in path else '?') + 'token=' + token
    dati = json.dumps(corpo).encode() if corpo is not None else None
    intestazioni = {}
    if dati:
        intestazioni['Content-Type'] = 'application/json'
    if token:
        # Le rotte `/api/` leggono `X-Admin-Token`, il feed legge la query string:
        # mandarlo in entrambi i posti evita di dover sapere quale rotta e- quale.
        intestazioni['X-Admin-Token'] = token
    if cookie:
        intestazioni['Cookie'] = f'{main.NOME_COOKIE}={cookie}'
    req = urllib.request.Request(url, data=dati, headers=intestazioni, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 - loopback
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def _cookie_dalla_risposta(intestazioni):
    """Il valore del cookie di sessione, letto da TUTTI gli header `Set-Cookie`.

    `get_all` e non `get`, e non `dict(headers)`: una risposta puo- portare piu-
    `Set-Cookie`, e `dict()` ne tiene uno solo — misurato, tiene il PRIMO e butta il
    resto. Un cookie di sessione emesso come secondo header sarebbe quindi invisibile a
    questo helper, e le asserzioni che si fidano di lui direbbero «nessun cookie»
    guardando nel posto sbagliato. Segnalato da Claude Fable 5 sulla PR #23.
    """
    for grezzo in (intestazioni.get_all('Set-Cookie') or []):
        for pezzo in grezzo.split(';'):
            chiave, _, valore = pezzo.strip().partition('=')
            if chiave == main.NOME_COOKIE:
                return valore
    return None


def test_il_login_telegram_apre_una_sessione_e_api_me_risponde(servizio):
    """Il flusso completo: firma valida → cookie → `/api/me` dice chi sono."""
    stato, corpo, intestazioni = _chiama(servizio, 'POST', '/api/login/telegram',
                                         corpo=_dati_login())
    assert stato == 200, corpo
    cookie = _cookie_dalla_risposta(intestazioni)
    assert cookie, f'nessun cookie di sessione nella risposta: {intestazioni}'

    stato, corpo, _ = _chiama(servizio, 'GET', '/api/me', cookie=cookie)
    assert stato == 200, corpo
    io = json.loads(corpo)
    assert io['nome'] == 'Piero'
    assert io['admin'] is True, (
        f'il proprietario non risulta admin: {io}. Con TELEGRAM_ADMIN_ID impostato, il '
        'suo login deve attaccarsi alla riga che possiede i suoi parser')


def test_il_login_del_proprietario_NON_crea_un_secondo_account(servizio, tmp_path):
    """Il difetto che TELEGRAM_ADMIN_ID esiste per evitare.

    Senza il collegamento, il login creerebbe un utente nuovo e vuoto: il proprietario
    entrerebbe in una dashboard senza i suoi parser, e nessun errore comparirebbe da
    nessuna parte. Qui si verifica che l'utente in cui entra sia **lo stesso** che
    possiede il parser di default.
    """
    stato, _, intestazioni = _chiama(servizio, 'POST', '/api/login/telegram',
                                     corpo=_dati_login())
    assert stato == 200
    cookie = _cookie_dalla_risposta(intestazioni)
    io = json.loads(_chiama(servizio, 'GET', '/api/me', cookie=cookie)[1])

    # Il proprietario del parser di default, letto dal database del servizio.
    import sqlite3
    c = sqlite3.connect(tmp_path / 'signals.db')
    proprietario = c.execute('SELECT user_id FROM parsers WHERE name=?',
                             (main.DEFAULT_PARSER,)).fetchone()[0]
    quanti = c.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    c.close()
    assert io['utente'] == proprietario, (
        f"il login e- entrato nell'utente {io['utente']} ma i parser sono "
        f'dell-utente {proprietario}: sono due account, e la dashboard sarebbe vuota')
    assert quanti == 1, f'{quanti} utenti nel database invece di 1: ne e- nato uno in piu-'


def test_una_firma_non_valida_NON_apre_nessuna_sessione(servizio):
    dati = _dati_login()
    dati['id'] = '111'      # cambiato dopo la firma
    stato, corpo, intestazioni = _chiama(servizio, 'POST', '/api/login/telegram', corpo=dati)
    assert stato == 401, corpo
    assert _cookie_dalla_risposta(intestazioni) is None, 'cookie emesso su un login rifiutato'


def test_un_cookie_MANOMESSO_non_apre_api_me(servizio):
    stato, _, intestazioni = _chiama(servizio, 'POST', '/api/login/telegram',
                                     corpo=_dati_login())
    cookie = _cookie_dalla_risposta(intestazioni)
    manomesso = cookie[:-4] + ('0000' if not cookie.endswith('0000') else '1111')
    stato, corpo, _ = _chiama(servizio, 'GET', '/api/me', cookie=manomesso)
    assert stato == 401, corpo


def test_il_cookie_e_HttpOnly_e_SameSite(servizio):
    """Un cookie leggibile da JavaScript e' un cookie che un XSS porta via."""
    _, _, intestazioni = _chiama(servizio, 'POST', '/api/login/telegram', corpo=_dati_login())
    grezzo = (intestazioni.get('set-cookie') or intestazioni.get('Set-Cookie') or '').lower()
    assert 'httponly' in grezzo, grezzo
    assert 'samesite=lax' in grezzo, grezzo
    assert 'secure' in grezzo, grezzo


def test_dopo_il_logout_il_cookie_non_vale_piu(servizio):
    _, _, intestazioni = _chiama(servizio, 'POST', '/api/login/telegram', corpo=_dati_login())
    cookie = _cookie_dalla_risposta(intestazioni)
    assert _chiama(servizio, 'GET', '/api/me', cookie=cookie)[0] == 200

    stato, _, uscita = _chiama(servizio, 'POST', '/api/logout', cookie=cookie)
    assert stato == 200
    grezzo = (uscita.get('set-cookie') or uscita.get('Set-Cookie') or '')
    assert main.NOME_COOKIE in grezzo, f'il logout non ha cancellato il cookie: {grezzo}'


def test_senza_ADMIN_PASSWORD_HASH_il_login_a_password_risponde_503(servizio):
    """L'ambiente del test non porta quella variabile: percorso disabilitato."""
    stato, corpo, intestazioni = _chiama(
        servizio, 'POST', '/api/login/password',
        corpo={'username': 'administrator', 'password': 'qualunque'})
    assert stato == 503, corpo
    assert b'ADMIN_PASSWORD_HASH' in corpo, corpo
    assert _cookie_dalla_risposta(intestazioni) is None


def test_il_login_a_password_funziona_e_finisce_in_admin_audit(tmp_path, monkeypatch):
    """Il percorso di emergenza, con la variabile configurata, e la sua traccia."""
    monkeypatch.setattr(main, 'SEGRETO_SESSIONE', SEGRETO_ATTESO)
    hash_salvato = main.hash_password('la password del proprietario')
    ambiente = dict(AMBIENTE_DEL_SERVIZIO, ADMIN_PASSWORD_HASH=hash_salvato)
    with relay_avviato(tmp_path, **ambiente) as base:
        sbagliata = _chiama(base, 'POST', '/api/login/password',
                            corpo={'username': 'administrator', 'password': 'sbagliata'})
        assert sbagliata[0] == 401, sbagliata[1]
        assert _cookie_dalla_risposta(sbagliata[2]) is None

        stato, corpo, intestazioni = _chiama(
            base, 'POST', '/api/login/password',
            corpo={'username': 'administrator', 'password': 'la password del proprietario'})
        assert stato == 200, corpo
        cookie = _cookie_dalla_risposta(intestazioni)
        assert cookie
        io = json.loads(_chiama(base, 'GET', '/api/me', cookie=cookie)[1])
        assert io['admin'] is True

        # La traccia: senza, «non sono stato io» non e- dimostrabile.
        import sqlite3
        c = sqlite3.connect(tmp_path / 'signals.db')
        azioni = [r[0] for r in c.execute('SELECT action FROM admin_audit').fetchall()]
        c.close()
        assert 'accesso_con_password' in azioni, azioni


def test_un_utente_SBAGLIATO_non_entra_nemmeno_con_la_password_giusta(tmp_path, monkeypatch):
    """`administrator` e' l'unico nome ammesso: la password sola non basta."""
    monkeypatch.setattr(main, 'SEGRETO_SESSIONE', SEGRETO_ATTESO)
    ambiente = dict(AMBIENTE_DEL_SERVIZIO,
                    ADMIN_PASSWORD_HASH=main.hash_password('giusta'))
    with relay_avviato(tmp_path, **ambiente) as base:
        stato, corpo, intestazioni = _chiama(base, 'POST', '/api/login/password',
                                             corpo={'username': 'piero', 'password': 'giusta'})
        assert stato == 401, corpo
        assert _cookie_dalla_risposta(intestazioni) is None


def test_dopo_troppi_tentativi_il_percorso_a_password_si_FRENA(tmp_path, monkeypatch):
    """Il freno: senza, una password scelta da un umano si rompe provandola in serie.

    E- globale e non per IP, e il baratto e' dichiarato in `main.py`: per IP non
    frenerebbe nulla, perche' chi prova in automatico cambia indirizzo. Il prezzo — un
    estraneo puo' tenere occupato il percorso a password per qualche minuto — e'
    accettabile **perche' esistono due porte**: il proprietario entra col login Telegram
    mentre quella a password e' frenata.
    """
    monkeypatch.setattr(main, 'SEGRETO_SESSIONE', SEGRETO_ATTESO)
    ambiente = dict(AMBIENTE_DEL_SERVIZIO,
                    ADMIN_PASSWORD_HASH=main.hash_password('giusta'))
    with relay_avviato(tmp_path, **ambiente) as base:
        stati = [_chiama(base, 'POST', '/api/login/password',
                         corpo={'username': 'administrator', 'password': 'sbagliata'})[0]
                 for _ in range(main.TENTATIVI_PRIMA_DEL_FRENO + 2)]
        assert stati[:main.TENTATIVI_PRIMA_DEL_FRENO] == [401] * main.TENTATIVI_PRIMA_DEL_FRENO, stati
        assert stati[-1] == 429, (
            f'dopo {main.TENTATIVI_PRIMA_DEL_FRENO} tentativi falliti il percorso non si '
            f'e- frenato: {stati}')
        # E il freno vale anche per la password GIUSTA: altrimenti non frenerebbe nulla,
        # perche' chi indovina al tentativo numero mille entrerebbe comunque.
        stato, corpo, _ = _chiama(base, 'POST', '/api/login/password',
                                  corpo={'username': 'administrator', 'password': 'giusta'})
        assert stato == 429, corpo


def test_la_sessione_scaduta_NON_tocca_il_feed(servizio):
    """**Il test per cui questo PR esiste.** Sessione e feed non si toccano.

    E- la trappola 2 della Issue #7, e non protegge una funzione: protegge una
    NON-relazione. XTrader interroga il feed con un token nell'URL — non ha una
    sessione, non fa login, non «resta attivo». Se i due meccanismi venissero collegati,
    ogni cliente perderebbe i segnali venti minuti dopo aver chiuso il browser, e
    **nessun test di login lo troverebbe**, perche' il login funzionerebbe benissimo.

    Nessuno scriverebbe spontaneamente un test che verifica che due cose NON sono
    collegate. E- l'unico motivo per cui va scritto adesso, mentre il collegamento non
    c'e': dopo, sarebbe un test che nasce da un guasto in produzione.

        sessione = cookie del sito, scade per inattivita' (20 min)
        token    = accesso al feed, scade solo se revocato
    """
    # Un segnale nel feed, messo dalla API con il token del feed.
    messo = _chiama(servizio, 'POST', f'/api/parsers/{main.DEFAULT_PARSER}/test',
                    corpo={'message': 'P.Bet. PREMACHT 0,5HT\nSQUADRA-A 🆚 SQUADRA-B'},
                    token=TOKEN_DI_PROVA)
    assert messo[0] == 200, messo[1]

    def feed():
        return _chiama(servizio, 'GET', '/xtrader.csv', token=TOKEN_DI_PROVA)

    stato, prima, _ = feed()
    assert stato == 200
    # Due righe: l'intestazione e il segnale. Non si asserisce il NOME della squadra,
    # perche' come il parser compone `EventName` e- l'argomento di
    # `tests/relay/test_parse_message.py`, non di questo: qui serve solo che nel feed ci
    # sia un segnale vivo, e che resti identico a se stesso.
    righe_prima = [r for r in prima.split(b'\r\n') if r]
    assert len(righe_prima) == 2, prima

    # Una sessione SCADUTA per inattivita', firmata correttamente ma vecchia.
    scaduta = main.firma_sessione(utente=1, versione=1,
                                  emessa=time.time() - main.INATTIVITA_MASSIMA - 60)
    assert _chiama(servizio, 'GET', '/api/me', cookie=scaduta)[0] == 401, (
        'la sessione scaduta e- stata accettata: il resto del test non misurerebbe niente')

    # E il feed risponde ANCORA, identico. Questa e' l'asserzione che conta.
    stato, dopo, _ = feed()
    assert stato == 200, (
        f'il feed risponde {stato} con una sessione scaduta: sessione e token sono stati '
        'collegati, e ogni cliente perdera- i segnali 20 minuti dopo aver chiuso il browser')
    assert dopo == prima, (
        'il contenuto del feed e- cambiato per via di una sessione scaduta:\n'
        f'  prima: {prima!r}\n  dopo : {dopo!r}')

    # E anche presentando il cookie scaduto direttamente al feed: il feed non deve
    # nemmeno GUARDARE il cookie.
    stato, con_cookie, _ = _chiama(servizio, 'GET', '/xtrader.csv',
                                   cookie=scaduta, token=TOKEN_DI_PROVA)
    assert stato == 200 and con_cookie == prima, (stato, con_cookie)


def test_incrementare_session_version_INVALIDA_i_cookie_gia_emessi(servizio, tmp_path):
    """L'unico modo di buttare fuori una sessione SUBITO, e non era protetto da niente.

    Trovato per sabotaggio mentre scrivevo questo file: togliendo il confronto fra la
    `versione` del cookie e `users.session_version` la suite dava **67 passed**. Il
    meccanismo c'era — la colonna nello schema, il valore firmato nel cookie, il
    confronto nel codice — e nessun test lo esercitava. E' la stessa forma di difetto che
    questa sessione ha incontrato dieci volte: copertura dichiarata e assente, e il
    sabotaggio l'unico modo di scoprirla.

    Perche' serve: senza, un cookie rubato resta valido fino alla scadenza naturale e non
    c'e- niente da fare per annullarlo. Con l'incremento, ogni sessione di quell'utente
    muore alla richiesta successiva. Il PR sull'amministrazione lo usera- per «entra come
    cliente» e per chiudere un accesso sospetto.
    """
    import sqlite3
    _, _, intestazioni = _chiama(servizio, 'POST', '/api/login/telegram', corpo=_dati_login())
    cookie = _cookie_dalla_risposta(intestazioni)
    assert _chiama(servizio, 'GET', '/api/me', cookie=cookie)[0] == 200, (
        'la sessione non e- valida nemmeno appena emessa: il resto non misura niente')

    # Il gesto che il pannello fara': incrementare la versione nel database.
    c = sqlite3.connect(tmp_path / 'signals.db')
    c.execute('UPDATE users SET session_version = session_version + 1')
    c.commit()
    c.close()

    stato, corpo, _ = _chiama(servizio, 'GET', '/api/me', cookie=cookie)
    assert stato == 401, (
        f'il cookie vale ancora dopo l-incremento di session_version (risposta {stato}): '
        'una sessione non si puo- piu- invalidare, e un cookie rubato resta buono fino '
        f'alla scadenza naturale. Corpo: {corpo[:200]!r}')

    # E un login nuovo funziona: l'incremento chiude le sessioni vecchie, non l'utente.
    _, _, nuove = _chiama(servizio, 'POST', '/api/login/telegram', corpo=_dati_login())
    assert _chiama(servizio, 'GET', '/api/me', cookie=_cookie_dalla_risposta(nuove))[0] == 200


def test_una_richiesta_valida_RINNOVA_la_scadenza(servizio):
    """Venti minuti di **inattivita'**, non di sessione. Erano di sessione.

    Segnalato indipendentemente da GPT-5.5 e da Claude Fable 5 sulla PR #23, e hanno
    ragione entrambi: `_rispondi_con_sessione` era usata solo dalle due rotte di login,
    quindi il cookie non veniva mai riemesso e i venti minuti partivano dal login e non
    dall'ultima richiesta. Il proprietario si sarebbe trovato buttato fuori ogni venti
    minuti **mentre stava lavorando**.

    Il difetto stava in `main.py`, ma la sua prova stava nel mio docstring di
    `firma_sessione` — «il cookie va riemesso a ogni richiesta valida: e' cosi' che venti
    minuti diventano di inattivita'» — e in `SAAS.md`. Una promessa scritta e non
    mantenuta e' peggio di una funzione mancante: chi legge la doc smette di cercare.

    Il rinnovo e' per-rotta e NON un middleware, di proposito: un middleware girerebbe
    anche su `/xtrader.csv`, cioe' metterebbe codice di sessione sul percorso del feed —
    esattamente la NON-relazione che questo PR esiste per garantire.
    """
    _, corpo, intestazioni = _chiama(servizio, 'POST', '/api/login/telegram',
                                     corpo=_dati_login())
    utente = json.loads(corpo)['utente']

    # Un cookie di un quarto d'ora fa: valido, ma con l'orologio a meta' strada.
    emessa_vecchia = int(time.time()) - 15 * 60
    vecchio = main.firma_sessione(utente=utente, versione=1, emessa=emessa_vecchia)

    stato, corpo, intestazioni = _chiama(servizio, 'GET', '/api/me', cookie=vecchio)
    assert stato == 200, (stato, corpo)

    rinnovato = _cookie_dalla_risposta(intestazioni)
    assert rinnovato, (
        'una richiesta valida non ha riemesso il cookie: i venti minuti partono dal '
        'login e non dall-ultima richiesta, quindi sono di SESSIONE e non di '
        f'INATTIVITA-, contro quanto dicono il docstring e SAAS.md. Intestazioni: {intestazioni}')

    emessa_nuova = int(rinnovato.split('.')[2])
    assert emessa_nuova > emessa_vecchia, (
        f'il cookie e- stato riemesso con la stessa emissione ({emessa_nuova} contro '
        f'{emessa_vecchia}): il rinnovo non sposta la scadenza e non serve a niente')
    # E l'identita- non cambia col rinnovo: sarebbe un modo di cambiare utente.
    assert main.leggi_sessione(rinnovato) == {'utente': utente, 'versione': 1}


def test_un_cookie_GIA_scaduto_non_viene_resuscitato_dal_rinnovo(servizio):
    """Il rinnovo vale per chi e' ancora dentro, non per chi e' gia' fuori.

    E' il rischio che il rinnovo introduce: se il cookie venisse riemesso **prima** di
    controllarne la validita', la scadenza per inattivita' non esisterebbe piu' — ogni
    cookie, per vecchio che sia, tornerebbe buono al primo tentativo. Un meccanismo di
    scadenza che si annulla da se' al primo uso.
    """
    _, corpo, _ = _chiama(servizio, 'POST', '/api/login/telegram', corpo=_dati_login())
    utente = json.loads(corpo)['utente']

    scaduto = main.firma_sessione(utente=utente, versione=1,
                                  emessa=time.time() - main.INATTIVITA_MASSIMA - 60)
    stato, corpo, intestazioni = _chiama(servizio, 'GET', '/api/me', cookie=scaduto)
    assert stato == 401, (stato, corpo)
    assert _cookie_dalla_risposta(intestazioni) is None, (
        'un cookie scaduto ha ricevuto un cookie nuovo: la scadenza per inattivita- non '
        'esiste piu-, perche- basta usarla per annullarla')


def _attendi_tutti(fili, esiti):
    """Aspetta ogni thread e **pretende** che abbia finito, con un esito per ciascuno.

    `Thread.join(timeout=...)` ritorna in silenzio anche quando il thread e' ancora vivo:
    senza questo controllo un test di concorrenza valuta le proprie assert su MENO esiti di
    quanti login ha lanciato, e passa verde senza aver misurato la corsa che descrive — la
    forma esatta del test finto che `CLAUDE.md` vieta. Segnalato da CodeRabbit sulla PR #24,
    su due siti: i due test di concorrenza lo usano entrambi invece di ricopiarlo.
    """
    for filo in fili:
        filo.join(timeout=30)
    vivi = [filo for filo in fili if filo.is_alive()]
    assert not vivi, (
        f'{len(vivi)} thread su {len(fili)} non hanno finito entro 30 s: le assert che '
        'seguono girerebbero su una corsa incompleta')
    assert len(esiti) == len(fili), (
        f'{len(esiti)} esiti per {len(fili)} thread: il test valuterebbe la corsa su meno '
        'login di quanti ne ha lanciati')


def test_due_PRIMI_login_dello_stesso_utente_nuovo_danno_UNA_riga(tmp_path, monkeypatch):
    """La corsa `SELECT`-poi-`INSERT` su `telegram_id` UNIQUE, chiusa strutturalmente.

    **Questo test e' stato riscritto, e il perche' conta piu' del test.** La versione
    precedente simulava la corsa con una connessione avvolta che inseriva la riga
    concorrente subito dopo il `SELECT`, perche' via HTTP la finestra non si apriva mai —
    30 richieste concorrenti, zero collisioni.

    Da quando `login_telegram` apre `BEGIN IMMEDIATE`, quella simulazione **non e' piu'
    possibile**: la connessione intrusa riceve `database is locked`, cioe' SQLite rifiuta di
    far entrare uno scrittore dentro la nostra transazione. La finestra non e' stata
    schermata da un controllo: e' stata chiusa dalla forma della transazione. Un test che
    inseriva a mano dentro quella finestra descriveva un mondo che non c'e' piu', e tenerlo
    verde con un `try/except` sarebbe stato mascherare la correzione invece di misurarla.

    Cio' che resta da vincolare e' l'esito, e questo test lo misura con thread veri: due o
    piu' primi login dello stesso utente nuovo devono dare **una** riga e nessun 500, senza
    dipendere da quale arriva prima. `INSERT OR IGNORE` piu' la rilettura restano la
    correzione della #23; `BEGIN IMMEDIATE` e' la cintura che rende la corsa irraggiungibile.
    """
    import sqlite3
    import threading

    percorso = _prepara(tmp_path, monkeypatch, 'primi_login.db')
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', '')
    main.db().close()  # la migrazione prima, o i thread la corrono insieme

    dati = _dati_login(id='555000222', first_name='Cliente')
    esiti = []
    porta = threading.Barrier(6)

    def prova():
        porta.wait()
        try:
            esiti.append(main.login_telegram(main.LoginTelegramIn(**dati)).status_code)
        except Exception as e:
            esiti.append(f'{type(e).__name__}:{getattr(e, "status_code", "")}')

    fili = [threading.Thread(target=prova) for _ in range(6)]
    for f in fili:
        f.start()
    _attendi_tutti(fili, esiti)

    assert set(esiti) == {200}, f'login concorrenti di un utente nuovo: {esiti}'
    c = sqlite3.connect(percorso)
    righe = c.execute('SELECT COUNT(*) FROM users WHERE telegram_id=?', ('555000222',)).fetchone()[0]
    c.close()
    assert righe == 1, f'{righe} righe per lo stesso telegram_id invece di una'


@pytest.mark.parametrize('valore', ['ö' * 64, 'firma-con-è', '🆚' * 8])
def test_un_hash_NON_ASCII_dal_widget_rifiuta_invece_di_SOLLEVARE(valore):
    """L'`hash` lo scrive chi chiama, e `verifica_login_telegram` promette di non sollevare.

    Il valore finisce in `hmac.compare_digest` come stringa: con un carattere non ASCII
    quella funzione **solleva** `TypeError`, quindi la rotta di login risponde 500 invece
    di 401. Chi chiama decide di far scrivere un traceback nei log con un campo di JSON.
    """
    dati = _dati_login()
    dati['hash'] = valore
    assert main.verifica_login_telegram(dati, BOT_FINTO) is False


@pytest.mark.parametrize('firma', ['ö' * 64, 'x' * 63 + 'é'])
def test_una_firma_di_cookie_NON_ASCII_rifiuta_invece_di_SOLLEVARE(firma, monkeypatch):
    """Il caso peggiore dei tre: il cookie lo scrive il browser, cioe' il mittente.

    `leggi_sessione` sta dietro **ogni** rotta autenticata, quindi un cookie con un
    accento non darebbe un 401 su una rotta: darebbe un 500 su tutte.
    """
    monkeypatch.setattr(main, 'SEGRETO_SESSIONE', SEGRETO_ATTESO)
    assert main.leggi_sessione(f'1.1.{int(time.time())}.{firma}') is None


def test_uno_username_NON_ASCII_non_diventa_un_500(tmp_path, monkeypatch):
    """Terzo sito: `login_password` confronta lo username come stringa.

    Il test gira con `ADMIN_PASSWORD_HASH` **impostato**, e non e' un dettaglio: senza,
    il `503` del percorso disabilitato arriva prima del confronto e il difetto non viene
    nemmeno sfiorato. La prima versione di questo test non lo impostava, passava verde,
    e non misurava niente — la trappola che questa sessione ha incontrato piu' volte.
    """
    ambiente = dict(AMBIENTE_DEL_SERVIZIO, ADMIN_PASSWORD_HASH=main.hash_password('giusta'))
    monkeypatch.setattr(main, 'SEGRETO_SESSIONE', SEGRETO_ATTESO)
    with relay_avviato(tmp_path, **ambiente) as base:
        stato, corpo, intestazioni = _chiama(base, 'POST', '/api/login/password',
                                             corpo={'username': 'administratör',
                                                    'password': 'giusta'})
    assert stato == 401, (
        f'uno username con una dieresi risponde {stato} invece di 401 (corpo '
        f'{corpo[:200]!r}): chi chiama la rotta di login decide di far sollevare il server')
    assert _cookie_dalla_risposta(intestazioni) is None


def test_senza_bot_token_il_login_a_password_NON_finge_di_riuscire(tmp_path, monkeypatch):
    """`ADMIN_PASSWORD_HASH` impostato e bot token assente: 503, non un 200 bugiardo.

    Segnalato da CodeRabbit sulla PR #23, e misurato: `firma_sessione` senza segreto
    restituisce stringa vuota, e la risposta uscendo diceva `200 {'ok': true}` con
    `betrelay_sessione=""`. Il login sembrava riuscito e **ogni richiesta successiva
    rispondeva 401**, senza niente da nessuna parte che dicesse perche'.

    E' la forma peggiore del fail-open: non apre una porta, apre una porta finta. Chi la
    attraversa non trova niente e non sa dove guardare — e in questo caso «chi» e' il
    proprietario che ha appena configurato la password per entrare, cioe' esattamente la
    situazione di emergenza per cui quel percorso esiste.
    """
    ambiente = dict(AMBIENTE_DEL_SERVIZIO, ADMIN_PASSWORD_HASH=main.hash_password('giusta'))
    ambiente.pop('TELEGRAM_BOT_TOKEN', None)
    monkeypatch.setattr(main, 'SEGRETO_SESSIONE', '')
    with relay_avviato(tmp_path, **ambiente) as base:
        stato, corpo, intestazioni = _chiama(base, 'POST', '/api/login/password',
                                             corpo={'username': 'administrator',
                                                    'password': 'giusta'})
    assert stato == 503, (
        f'senza bot token il login a password risponde {stato} (corpo {corpo[:200]!r}) '
        'invece di 503: emette un cookie vuoto e finge di essere riuscito')
    assert _cookie_dalla_risposta(intestazioni) in (None, ''), (
        'e- stato emesso un cookie che non potra- mai essere valido')


def test_un_campo_SCONOSCIUTO_ma_firmato_da_Telegram_viene_accettato():
    """Telegram firma i campi che manda, compresi quelli che noi non conosciamo ancora.

    Segnalato da CodeRabbit sulla PR #23. Pydantic **scarta** i campi non dichiarati,
    quindi la `data_check_string` che ricostruiamo sarebbe piu' corta di quella firmata e
    la firma non combacerebbe: il giorno che Telegram aggiunge un campo — l'ha gia' fatto
    con `photo_url` — **tutti i login veri comincerebbero a essere rifiutati**, e il
    sintomo sarebbe «il login non funziona piu'» senza nessun errore nei log.

    Il verso opposto resta chiuso e vale la pena dirlo: accettare i campi sconosciuti non
    apre niente, perche' entrano nella stringa firmata. Un campo aggiunto **dopo** la
    firma la invalida comunque — lo verifica
    `test_un_campo_AGGIUNTO_dopo_la_firma_viene_rifiutato`.
    """
    campi = {
        'id': ADMIN_FINTO,
        'first_name': 'Piero',
        'auth_date': str(int(time.time())),
        'campo_che_telegram_aggiungera': 'un valore',
    }
    campi['hash'] = _firma_telegram(campi)
    # La funzione pura lo accetta: e' il modello Pydantic il punto dove si perde.
    assert main.verifica_login_telegram(campi, BOT_FINTO) is True
    conservati = main.LoginTelegramIn(**campi).model_dump()
    assert 'campo_che_telegram_aggiungera' in conservati, (
        'il modello scarta i campi che non conosce: la data_check_string ricostruita e- '
        'piu- corta di quella firmata, quindi il giorno che Telegram aggiunge un campo '
        f'ogni login vero viene rifiutato. Conservati: {sorted(conservati)}')


def _funzioni_chiamate(funzione):
    """I nomi delle funzioni **chiamate** dentro `funzione`, letti dall'AST.

    Dall'albero sintattico e non dal testo del sorgente, e la differenza non e'
    accademica: cercando la sottostringa, un **commento** che nomina l'helper conta come
    se lo chiamasse. Misurato — una rotta che legge la sessione, non rinnova il cookie e
    porta il commento «qui non uso `_rispondi_con_sessione`» passava la guardia. Un
    falso negativo silenziato da un commento e' il difetto peggiore che una guardia possa
    avere, perche' si legge come copertura. Segnalato da GPT-5.5 sulla PR #23.

    Restituisce `None` se il sorgente non e' leggibile: chi chiama distingue «non usa la
    sessione» da «non ho potuto guardare».
    """
    import ast
    import inspect
    import textwrap

    try:
        sorgente = textwrap.dedent(inspect.getsource(funzione))
    except (OSError, TypeError):
        return None
    try:
        albero = ast.parse(sorgente)
    except SyntaxError:
        return None

    nomi = set()
    for nodo in ast.walk(albero):
        if isinstance(nodo, ast.Call):
            bersaglio = nodo.func
            if isinstance(bersaglio, ast.Name):
                nomi.add(bersaglio.id)
            elif isinstance(bersaglio, ast.Attribute):
                nomi.add(bersaglio.attr)
    return nomi


def test_ogni_rotta_che_usa_la_SESSIONE_rinnova_anche_il_cookie():
    """La guardia sulla disciplina, perche' il rinnovo e' per-rotta e non un middleware.

    Segnalata come rischio da Claude Fable 5 e da GPT-5.5 sulla PR #23, ed e' lo stesso
    rischio in due formulazioni: il rinnovo per-rotta funziona solo se **ogni** rotta
    autenticata futura si ricorda di farlo. Chi ne aggiunge una senza rinnovare non rompe
    niente in modo visibile — la rotta funziona — ma da quel momento la sessione di chi
    usa **quella** rotta scade venti minuti dopo il login. Una regressione silenziosa, e
    il PR sull'approvazione (#7) piu' quello sul feed per utente ne aggiungeranno diverse.

    Il middleware la eviterebbe e non si puo' usare: girerebbe anche su `/xtrader.csv`,
    cioe' metterebbe codice di sessione sul percorso del feed — la NON-relazione che
    questo PR esiste per garantire. Quindi la disciplina serve, e cio' che si puo' fare e'
    renderla **verificabile** invece di raccomandata.

    Il controllo guarda le **chiamate** e non il comportamento di proposito: una rotta
    futura puo' avere qualunque metodo, percorso e corpo, e un test che dovesse costruire
    una richiesta valida per ciascuna non riuscirebbe a coprirle tutte. Cosi' la copertura
    e' completa per costruzione. Che il rinnovo funzioni davvero, e che avvenga DOPO la
    validazione, lo misurano `test_una_richiesta_valida_RINNOVA_la_scadenza` e
    `test_un_cookie_GIA_scaduto_non_viene_resuscitato_dal_rinnovo`.

    **Il limite previsto si e' presentato, e la guardia e' stata estesa.** Questo paragrafo
    diceva: «una rotta che ottenesse l'utente per vie indirette — un helper che avvolge
    `utente_dalla_sessione` — non nominerebbe quella funzione fra le proprie chiamate e
    sfuggirebbe; il giorno che quella forma servira', questa guardia va estesa». Quel giorno
    e' la PR #26: le rotte dell'accesso su approvazione passano da `_sessione_valida` e
    `_solo_amministratore`, e `chi_sono` ha smesso di chiamare direttamente
    `utente_dalla_sessione` per non ricopiarne il controllo (regola 3).

    La guardia non ha taciuto: e' diventata **rossa** dicendo «nessuna rotta chiama
    `utente_dalla_sessione`: questa guardia non misura niente». Era il caso previsto
    dall'asserzione anti-vacuita', e ha funzionato — un controllo che si accorge di essere
    diventato inutile vale piu' di uno che continua a passare.

    Gli avvolgitori sono **derivati e non elencati a mano**: si cercano fra le funzioni del
    modulo quelle che chiamano `utente_dalla_sessione`, e una rotta che chiama una di loro
    conta come rotta di sessione. Un helper nuovo entra da se'. Il caso dei commenti e delle
    stringhe resta chiuso, perche' l'AST non li vede.

    Il caso «sorgente non leggibile» e' chiuso in un terzo modo: non viene scartato, viene
    **elencato e fatto fallire**. Oggi tutte le rotte sono ispezionabili, quindi
    l'asserzione e' silenziosa; il giorno che una non lo fosse, il calo di copertura si
    vede invece di succedere.
    """
    # Gli avvolgitori di `utente_dalla_sessione`, derivati dal modulo: qualunque funzione che
    # la chiami e' un modo di «usare la sessione», quindi una rotta che chiama quella conta.
    porte_di_sessione = {'utente_dalla_sessione'}
    for nome in dir(main):
        funzione = getattr(main, nome, None)
        if not callable(funzione) or getattr(funzione, '__module__', None) != 'main':
            continue
        chiamate = _funzioni_chiamate(funzione)
        if chiamate and 'utente_dalla_sessione' in chiamate:
            porte_di_sessione.add(nome)
    assert len(porte_di_sessione) > 1, (
        'nessun avvolgitore di utente_dalla_sessione trovato: la derivazione non funziona e '
        'la guardia tornerebbe a coprire solo le chiamate dirette'
    )

    inadempienti = []
    guardate = []
    non_ispezionabili = []
    for rotta in main.app.routes:
        funzione = getattr(rotta, 'endpoint', None)
        if funzione is None:
            continue
        chiamate = _funzioni_chiamate(funzione)
        if chiamate is None:
            # Tenuta a parte e NON scartata: `None` significa «non ho potuto guardare»,
            # non «non usa la sessione». Confonderli riduce la copertura senza far
            # fallire niente — la guardia resterebbe verde su una rotta che non ha
            # ispezionato. Segnalato da GPT-5.5 sulla PR #23, e il docstring di
            # `_funzioni_chiamate` prometteva gia' questa distinzione mentre il codice
            # qui la buttava via: la quarta promessa non mantenuta di questo PR.
            non_ispezionabili.append(f'{sorted(getattr(rotta, "methods", []))} {rotta.path}')
            continue
        if not (chiamate & porte_di_sessione):
            continue
        guardate.append(rotta.path)
        if '_rispondi_con_sessione' not in chiamate:
            inadempienti.append(f'{sorted(getattr(rotta, "methods", []))} {rotta.path}')

    assert not non_ispezionabili, (
        'il sorgente di queste rotte non e- leggibile, quindi questa guardia non puo- '
        f'dire se rinnovano il cookie: {non_ispezionabili}. Oggi tutte le rotte sono '
        'ispezionabili, e questa asserzione esiste perche- il giorno che una non lo fosse '
        '— un endpoint generato, avvolto da un decoratore che perde il sorgente, o '
        'importato da un modulo compilato — il calo di copertura sia RUMOROSO invece che '
        'silenzioso. Se la forma diventa legittima, va insegnato alla guardia come '
        'guardarla, non aggiunta all-elenco delle eccezioni.')

    assert not inadempienti, (
        'queste rotte leggono la sessione e NON riemettono il cookie, quindi per chi le '
        'usa i venti minuti tornano assoluti dal login invece che di inattivita-: '
        f'{inadempienti}. Il rinnovo si ottiene restituendo _rispondi_con_sessione(...) '
        'con utente["versione"], DOPO la validazione.')

    # E la guardia non deve essere vacua: se nessuna rotta usa la sessione, il ciclo qui
    # sopra non guarda niente e il test passa dicendo nulla.
    assert guardate, (
        'nessuna rotta chiama utente_dalla_sessione: questa guardia non misura niente')


def test_il_widget_manda_NUMERI_e_il_login_deve_accettarli(servizio):
    """**Il bloccante che rompeva ogni login reale.** Alzato da Fable 5 e da Sol, insieme.

    Il Login Widget di Telegram consegna a JavaScript un oggetto in cui `id` e `auth_date`
    sono **numeri**, non stringhe. Un client che lo passa a `JSON.stringify` — cioe'
    qualunque client scritto nel modo ovvio — manda `{"id": 987654321, ...}`.

    E Pydantic v2 **non** converte un numero in stringa: misurato,
    `LoginTelegramIn(id=12345678, ...)` solleva `ValidationError`, quindi la rotta
    risponde 422. Non un login su mille: **tutti**, sempre, dal primo.

    E' il difetto peggiore che questo PR poteva contenere, perche' non e' raggiungibile da
    nessuno dei 519 test scritti finora: li' i campi li costruisco io, e li costruisco
    come stringhe. Il codice era coerente con se stesso e sbagliato rispetto al mondo. Se
    ne sono accorti i due gate finali contemporaneamente, cioe' esattamente il punto per
    cui esistono e si pagano.

    La firma deve continuare a verificare: Telegram calcola l'HMAC sulle **forme testuali**
    dei valori, e un intero stringificato e' identico da entrambe le parti.
    """
    campi = {
        'id': 987654321,                    # NUMERO, come dal widget
        'first_name': 'Piero',
        'username': 'piero',
        'auth_date': int(time.time()),      # NUMERO
    }
    # La firma si calcola sulle forme testuali, che e' cio' che fa Telegram.
    campi['hash'] = _firma_telegram({k: str(v) for k, v in campi.items()})

    stato, corpo, intestazioni = _chiama(servizio, 'POST', '/api/login/telegram', corpo=campi)
    assert stato == 200, (
        f'un payload con id e auth_date NUMERICI — cioe- quello che il widget produce — '
        f'risponde {stato} invece di 200 (corpo {corpo[:300]!r}): il login Telegram e- '
        'inutilizzabile per ogni utente reale')
    assert _cookie_dalla_risposta(intestazioni), 'nessun cookie di sessione'


def test_un_campo_extra_BOOLEANO_firmato_da_Telegram_non_rompe_la_verifica():
    """`str(True)` in Python e' `'True'`, in JSON e' `'true'`. Non e' la stessa stringa.

    Secondo bloccante di Fable 5. I campi extra non sono dichiarati nel modello, quindi
    non passano da nessuna conversione: finiscono nella `data_check_string` con lo `str()`
    di Python. Per un campo booleano — o per un numero non intero — quella forma **diverge**
    da quella che Telegram ha firmato, e il login legittimo viene rifiutato.

    Oggi Telegram non manda booleani, quindi il difetto e' latente: e' la stessa forma del
    bloccante qui sopra, che era latente finche' nessun client vero esisteva. Ne ho appena
    pagato uno; il secondo lo chiudo insieme.
    """
    grezzi = {'id': 55, 'auth_date': int(time.time()), 'is_premium': True, 'saldo': 1.5}
    # Come firma Telegram: la serializzazione JSON dei valori, non il repr di Python.
    import json as _json
    atteso = {k: (_json.dumps(v) if not isinstance(v, str) else v) for k, v in grezzi.items()}
    grezzi['hash'] = _firma_telegram(atteso)

    assert main.verifica_login_telegram(main.campi_firmati(grezzi), BOT_FINTO) is True, (
        'un campo extra booleano o decimale fa fallire la verifica di una firma valida: '
        "str(True) e- 'True' e non 'true', e la data_check_string non combacia")


def test_il_freno_non_si_AGGIRA_con_richieste_concorrenti(monkeypatch):
    """Controllo del freno e conteggio del tentativo devono essere **un solo gesto**.

    Bloccante di GPT-5.6 Sol, e reale: `_freno_password()` prende il lock, legge, lo
    rilascia; poi arriva `scrypt`, che costa ~100 ms di CPU per progetto; solo alla fine
    `_registra_tentativo` incrementa. Fra la lettura e l'incremento passa tutto il calcolo,
    quindi N richieste concorrenti vedono tutte «zero tentativi falliti» e passano tutte.

    Due conseguenze, e la seconda e' peggiore della prima: il limite di cinque tentativi si
    aggira mandando le richieste insieme invece che in fila, e ogni richiesta che passa
    accende uno `scrypt` — cioe' il freno che doveva proteggere la password diventa un
    amplificatore di carico. Un `for` in una riga di shell mette il servizio in ginocchio.

    La correzione e' la forma «consuma un gettone prima di lavorare»: il tentativo si conta
    **dentro** il lock, prima della verifica, e si azzera solo in caso di successo.
    """
    quante = {'verifiche': 0}
    vera = main.verifica_password_admin

    def conta(password, salvato):
        quante['verifiche'] += 1
        time.sleep(0.05)          # scrypt costa: e- la finestra in cui gli altri entrano
        return vera(password, salvato)

    monkeypatch.setattr(main, 'verifica_password_admin', conta)
    monkeypatch.setattr(main, 'ADMIN_PASSWORD_HASH', main.hash_password('giusta'))
    monkeypatch.setattr(main, 'SEGRETO_SESSIONE', SEGRETO_ATTESO)
    main._TENTATIVI_PASSWORD.update({'falliti': 0, 'ultimo': 0.0})

    import threading
    esiti = []
    porta = threading.Barrier(12)

    def prova():
        porta.wait()
        try:
            main.login_password(main.LoginPasswordIn(username='administrator',
                                                     password='sbagliata'))
            esiti.append(200)
        except Exception as e:
            esiti.append(getattr(e, 'status_code', type(e).__name__))

    fili = [threading.Thread(target=prova) for _ in range(12)]
    for f in fili:
        f.start()
    for f in fili:
        f.join(timeout=30)

    assert quante['verifiche'] <= main.TENTATIVI_PRIMA_DEL_FRENO, (
        f"{quante['verifiche']} verifiche scrypt su 12 richieste concorrenti, con un freno "
        f'da {main.TENTATIVI_PRIMA_DEL_FRENO}: il limite si aggira mandandole insieme, e '
        'ogni richiesta che passa accende uno scrypt — il freno amplifica il carico invece '
        'di ridurlo')
    assert 429 in esiti, f'nessuna richiesta e- stata frenata: {esiti}'


def test_senza_ADMIN_PASSWORD_HASH_il_freno_non_si_CONSUMA(monkeypatch):
    """Una porta **chiusa** non deve poter consumare il freno della porta.

    Regressione introdotta dalla mia stessa correzione del giro precedente, trovata da
    Claude Fable 5 sulla PR #23: spostando il conteggio del tentativo prima della verifica
    — che era giusto, chiudeva l'aggiramento per concorrenza — l'ho messo anche **prima**
    del controllo su `ADMIN_PASSWORD_HASH`. Misurato:

        stati: [503, 503, 503, 503, 503, 429, 429]
        contatore del freno: 5

    Cinque richieste **senza nessuna credenziale**, su un percorso che e' *disabilitato*,
    bruciano il freno per cinque minuti. Un `for` di shell da un estraneo, a costo zero.

    E il danno peggiore e' il secondo: dopo quelle cinque, la risposta diventa `429
    troppi tentativi` invece di `503 manca ADMIN_PASSWORD_HASH`. Cioe' il proprietario che
    arriva a configurare la variabile — nell'emergenza per cui quel percorso esiste — legge
    «hai fatto troppi tentativi» e va a cercare la password giusta invece della
    configurazione mancante. Un messaggio d'errore che manda dalla parte sbagliata e' peggio
    di nessun messaggio.

    La correzione e' l'ordine: prima si guarda se la porta esiste, poi si consuma il gettone.
    """
    from fastapi import HTTPException

    monkeypatch.setattr(main, 'ADMIN_PASSWORD_HASH', '')
    main._TENTATIVI_PASSWORD.update({'falliti': 0, 'ultimo': 0.0})

    stati = []
    for _ in range(main.TENTATIVI_PRIMA_DEL_FRENO + 3):
        try:
            main.login_password(main.LoginPasswordIn(username='administrator',
                                                     password='qualunque'))
            stati.append(200)
        except HTTPException as e:
            stati.append(e.status_code)

    assert set(stati) == {503}, (
        f'con il percorso disabilitato le risposte sono {stati}: le richieste hanno '
        'consumato il freno, quindi un estraneo lo brucia a costo zero E il 503 che dice '
        'cosa configurare viene sostituito da un 429 che manda a cercare la password')
    assert main._TENTATIVI_PASSWORD['falliti'] == 0, (
        f"il contatore e- {main._TENTATIVI_PASSWORD['falliti']} invece di 0: una porta "
        'chiusa non deve poter consumare il freno della porta')


def test_un_campo_STRUTTURATO_usa_il_JSON_compatto(monkeypatch):
    """`json.dumps` di default mette uno spazio: `{"a": 1}`, non `{"a":1}`.

    Terzo bloccante di Claude Fable 5 sulla PR #23, ed e' l'unico dei suoi finding che
    riguarda un caso **ipotetico**: oggi il Login Widget manda solo scalari, e per uno
    scalare le due forme sono identiche — misurato, `json.dumps(5)` e la versione compatta
    danno entrambe `'5'`. La divergenza esiste solo per oggetti e liste.

    Lo correggo comunque perche' la correzione e' **a zero cambiamento di comportamento**
    per i campi che esistono davvero: un argomento in piu', nessun rischio, e toglie una
    scelta arbitraria — la spaziatura di default di Python — in favore della forma
    canonica che un firmatario usa. Se un giorno Telegram aggiungesse un campo strutturato,
    il difetto sarebbe una firma valida rifiutata **in silenzio**, cioe' la stessa forma
    del bloccante sui numeri che ha rotto ogni login: latente finche' nessun client vero
    esiste, e poi totale. Ne ho pagato uno in questo PR; il secondo non lo aspetto.

    L'assunzione resta scritta, come chiedeva il finding: i campi del widget sono
    **piatti**. Questo test vincola la serializzazione nel caso in cui smettano di esserlo.
    """
    import json as _json

    grezzi = {'id': 77, 'auth_date': int(time.time()), 'extra': {'a': 1}, 'lista': [1, 2]}
    # Come firmerebbe chi serializza in JSON compatto, che e- la forma canonica.
    atteso = {k: (v if isinstance(v, str) else _json.dumps(v, separators=(',', ':')))
              for k, v in grezzi.items()}
    grezzi['hash'] = _firma_telegram(atteso)

    assert main.verifica_login_telegram(main.campi_firmati(grezzi), BOT_FINTO) is True, (
        'un campo strutturato viene serializzato con gli spazi di default di Python, che '
        'divergono dal JSON compatto: una firma VALIDA verrebbe rifiutata in silenzio')

    # E per gli scalari nulla cambia: e- il motivo per cui questa correzione e- gratis.
    assert main.campi_firmati({'a': 5, 'b': 1.5, 'c': True}) == {'a': '5', 'b': '1.5', 'c': 'true'}


def _riga_utente(percorso, campo, valore):
    """Una riga di `users` come tupla `(id, telegram_id, origin_profile, is_admin)`."""
    import sqlite3
    c = sqlite3.connect(percorso)
    r = c.execute(f'SELECT id, telegram_id, origin_profile, is_admin FROM users'
                  f' WHERE {campo}=?', (valore,)).fetchone()
    c.close()
    return r


def test_il_collegamento_dell_admin_RIPARA_un_account_gia_sbagliato(tmp_path, monkeypatch):
    """**Il difetto per cui questo test esiste era IRREVERSIBILE.** Ordine-dipendente e muto.

    Il collegamento del proprietario alla riga che possiede i suoi parser viveva dentro
    `if riga is None`. Conseguenza misurata: se il primo login avveniva mentre
    `TELEGRAM_ADMIN_ID` non era ancora impostata — o non era ancora arrivata nel processo,
    che su Railway succede quando un build fallisce dopo aver cambiato una variabile —
    nasceva un utente **vuoto** con quel `telegram_id`. Da quel momento ogni login
    successivo trovava `riga is not None`, prendeva il ramo `else`, e la riga con
    `origin_profile='PIERO'` non veniva collegata **mai piu'**.

    Non c'era via di ritorno: la riconciliazione della migrazione raggruppa per
    `origin_profile` e quella riga ha `origin_profile` NULL, quindi le e' cieca; nessun
    endpoint ripara; un riavvio non ripara. Serviva scrivere a mano nel database di
    produzione — che dalla dashboard di Railway non si puo' fare. Per il proprietario era
    **irreversibile**, e l'unico avviso era una dashboard vuota senza errori.

    E' la forma di difetto peggiore fra quelle che questo repository ha collezionato: il
    codice e' corretto solo se le operazioni avvengono nell'ordine giusto, l'ordine
    sbagliato non da' nessun errore, e lo stato che ne risulta non si sistema.

    La correzione e' un'**invariante** invece di un ramo: se chi fa login e' l'ID
    dell'amministratore, la riga `PIERO` possiede quel `telegram_id`, indipendentemente da
    cosa c'e' adesso. Idempotente, quindi l'ordine non conta piu' e il login successivo
    ripara cio' che il precedente ha sbagliato.
    """
    import sqlite3

    percorso = str(tmp_path / 'ripara.db')
    monkeypatch.setattr(main, 'DB_PATH', percorso)
    monkeypatch.setattr(main, 'BOT_TOKEN', BOT_FINTO)
    monkeypatch.setattr(main, 'SEGRETO_SESSIONE', SEGRETO_ATTESO)

    # PRIMO login SENZA la variabile: e' lo stato in cui il proprietario si troverebbe
    # facendo login dopo un build fallito. Nasce l'account vuoto.
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', '')
    main.login_telegram(main.LoginTelegramIn(**_dati_login()))

    vuoto = _riga_utente(percorso, 'telegram_id', ADMIN_FINTO)
    piero = _riga_utente(percorso, 'origin_profile', main.PIERO_PROFILE)
    assert vuoto is not None and piero is not None
    assert vuoto[0] != piero[0], (
        'lo scenario non si e- prodotto: il primo login ha gia- collegato la riga PIERO, '
        'quindi il test non misura la riparazione')
    assert piero[1] is None, f'la riga PIERO ha gia- un telegram_id: {piero}'

    # I parser stanno sulla riga PIERO, ed e' il motivo per cui il collegamento conta.
    c = sqlite3.connect(percorso)
    quanti = c.execute('SELECT COUNT(*) FROM parsers WHERE user_id=?', (piero[0],)).fetchone()[0]
    c.close()
    assert quanti > 0, 'la riga PIERO non possiede parser: il test non misura la posta in gioco'

    # ORA la variabile arriva — il deploy e' passato — e il proprietario rifa' il login.
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', ADMIN_FINTO)
    # Il consenso all'assorbimento della riga vuota: dal fail-closed di GPT-5.6 Sol sulla
    # PR #24 e' un gesto DELIBERATO — senza, il servizio rifiuta con 409, perche' una riga
    # vuota puo' anche essere di un cliente appena iscritto — e dal rischio alzato da
    # GPT-5.5 il valore e' l'ID DELLA RIGA, non un `1` globale.
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_RECONCILE', str(vuoto[0]))
    risposta = main.login_telegram(main.LoginTelegramIn(**_dati_login()))
    assert risposta.status_code == 200

    dopo_piero = _riga_utente(percorso, 'origin_profile', main.PIERO_PROFILE)
    assert dopo_piero[1] == ADMIN_FINTO, (
        f'la riga PIERO non ha ricevuto il telegram_id (e- {dopo_piero[1]!r}): il '
        'collegamento non ripara, quindi il proprietario resta fuori dal proprio account '
        'per sempre e senza nessun errore')
    assert dopo_piero[3] == 1, 'la riga PIERO non risulta amministratore'

    # E il telegram_id e' UNICO: non puo- essere rimasto anche sulla riga vuota.
    c = sqlite3.connect(percorso)
    quante = c.execute('SELECT COUNT(*) FROM users WHERE telegram_id=?', (ADMIN_FINTO,)).fetchone()[0]
    c.close()
    assert quante == 1, f'{quante} righe con lo stesso telegram_id: il vincolo UNIQUE e- violato'

    # La sessione emessa deve essere quella del PROPRIETARIO, non dell'account vuoto:
    # altrimenti il login "riesce" e mostra ancora una dashboard vuota.
    cookie = None
    for grezzo in (risposta.headers.get('set-cookie') or '').split(';'):
        chiave, _, valore = grezzo.strip().partition('=')
        if chiave == main.NOME_COOKIE:
            cookie = valore
    assert cookie, 'nessun cookie nella risposta'
    assert main.leggi_sessione(cookie)['utente'] == dopo_piero[0], (
        'la sessione e- stata emessa per l-account sbagliato: il login riesce e la '
        'dashboard resta vuota')

    # TERZO login: la riparazione deve essere idempotente anche verso se stessa. Adesso
    # `riga[0] == proprietario[0]`, quindi il flusso prende l'ultimo ramo `else` e non
    # deve rifare niente. Chiesto da CodeRabbit sulla PR #24, e serve: senza, una modifica
    # futura che rieseguisse la riconciliazione a OGNI richiesta passerebbe i test.
    terza = main.login_telegram(main.LoginTelegramIn(**_dati_login()))
    assert terza.status_code == 200
    ancora = _riga_utente(percorso, 'origin_profile', main.PIERO_PROFILE)
    assert ancora == dopo_piero, (
        f'il terzo login ha cambiato lo stato: {dopo_piero} -> {ancora}. La riparazione '
        'non e- idempotente verso se stessa')
    c = sqlite3.connect(percorso)
    riconciliazioni = c.execute("SELECT COUNT(*) FROM admin_audit"
                                " WHERE action='riconciliato_account_duplicato'").fetchone()[0]
    c.close()
    assert riconciliazioni == 1, (
        f'{riconciliazioni} riparazioni registrate invece di una: il terzo login ha '
        'ri-riconciliato uno stato gia- a posto')


def test_la_riparazione_NON_perde_i_dati_dell_account_sbagliato(tmp_path, monkeypatch):
    """Cio' che l'account nato per errore avesse accumulato deve finire sul proprietario.

    Il caso non e' teorico: fra il login fatto troppo presto e la riparazione possono
    passare giorni, e in quel tempo quell'account e' **l'unico** in cui il proprietario
    riesce a entrare — quindi e' l'account su cui finirebbe qualunque cosa faccia.

    Per questo la riparazione travasa invece di cancellare, e riusa `RIFERIMENTI_UTENTE`
    della migrazione (regola 3): le stesse otto coppie tabella/colonna, un elenco solo.
    """
    import sqlite3

    percorso = str(tmp_path / 'travaso.db')
    monkeypatch.setattr(main, 'DB_PATH', percorso)
    monkeypatch.setattr(main, 'BOT_TOKEN', BOT_FINTO)
    monkeypatch.setattr(main, 'SEGRETO_SESSIONE', SEGRETO_ATTESO)

    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', '')
    main.login_telegram(main.LoginTelegramIn(**_dati_login()))
    vuoto = _riga_utente(percorso, 'telegram_id', ADMIN_FINTO)[0]
    piero = _riga_utente(percorso, 'origin_profile', main.PIERO_PROFILE)[0]

    # Tracce lasciate sull'account sbagliato: un segnale e una riga di log.
    #
    # NON una chat e NON un parser, e la distinzione e' il cuore della correzione arrivata
    # dopo: quelli sono cio' che si POSSIEDE, e una riga che li possiede e' l'account di
    # qualcuno — quindi oggi la riconciliazione la RIFIUTA invece di assorbirla (vedi
    # `test_un_account_con_PARSER_viene_RIFIUTATO_non_assorbito`). La prima versione di
    # questo test dava una chat all'account sbagliato, e con la semantica nuova finiva nel
    # rifiuto: l'ho corretto sulle tracce, che e' il caso che la riparazione deve servire.
    c = sqlite3.connect(percorso)
    c.execute("INSERT INTO signals(csv, parser, profile, user_id, expires_at)"
              " VALUES ('riga','p',?,?,?)",
              (main.PIERO_PROFILE, vuoto, 9999999999))
    c.execute('INSERT INTO message_logs(user_id, text, esito) VALUES (?,?,?)',
              (vuoto, 'un messaggio', 'ok'))
    c.commit()
    c.close()

    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', ADMIN_FINTO)
    # Il consenso all'assorbimento della riga vuota: dal fail-closed di GPT-5.6 Sol sulla
    # PR #24 e' un gesto DELIBERATO — senza, il servizio rifiuta con 409, perche' una riga
    # vuota puo' anche essere di un cliente appena iscritto — e dal rischio alzato da
    # GPT-5.5 il valore e' l'ID DELLA RIGA, non un `1` globale.
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_RECONCILE', str(vuoto))
    main.login_telegram(main.LoginTelegramIn(**_dati_login()))

    c = sqlite3.connect(percorso)
    segnale = c.execute("SELECT user_id FROM signals WHERE csv='riga'").fetchone()[0]
    log = c.execute('SELECT user_id FROM message_logs WHERE text=?', ('un messaggio',)).fetchone()[0]
    audit = c.execute("SELECT admin_user_id, target_user_id FROM admin_audit"
                      " WHERE action='riconciliato_account_duplicato'").fetchone()
    rimasto = c.execute('SELECT telegram_id FROM users WHERE id=?', (vuoto,)).fetchone()[0]
    c.close()

    assert segnale == piero, (
        f'il segnale e- rimasto all-account sbagliato (user {segnale}, atteso {piero})')
    assert log == piero, f'il log e- rimasto all-account sbagliato (user {log}, atteso {piero})'
    assert audit == (piero, vuoto), (
        f'la riparazione non e- tracciata in admin_audit: {audit}. Una riparazione '
        'silenziosa e- indistinguibile da un-appropriazione di account')
    assert rimasto is None, (
        'la riga perdente conserva il telegram_id: con UNIQUE su quella colonna lo stato '
        'non sarebbe nemmeno stato scrivibile')


def test_un_CLIENTE_non_fa_scattare_la_riparazione(tmp_path, monkeypatch):
    """Il ramo che ripara deve valere SOLO per l'ID dell'amministratore.

    E' il verso pericoloso: se scattasse per chiunque, il primo estraneo che fa login si
    prenderebbe i parser e il feed del proprietario. Il codice lo esclude con
    `data.id == TELEGRAM_ADMIN_ID`, e questo test lo vincola invece di fidarsi.
    """
    percorso = str(tmp_path / 'cliente.db')
    monkeypatch.setattr(main, 'DB_PATH', percorso)
    monkeypatch.setattr(main, 'BOT_TOKEN', BOT_FINTO)
    monkeypatch.setattr(main, 'SEGRETO_SESSIONE', SEGRETO_ATTESO)
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', ADMIN_FINTO)

    estraneo = '111222333'
    assert estraneo != ADMIN_FINTO
    main.login_telegram(main.LoginTelegramIn(**_dati_login(id=estraneo, first_name='Estraneo')))

    piero = _riga_utente(percorso, 'origin_profile', main.PIERO_PROFILE)
    suo = _riga_utente(percorso, 'telegram_id', estraneo)
    assert piero[1] is None, (
        f'il login di un estraneo ha collegato il suo telegram_id alla riga del '
        f'proprietario: {piero}. Si e- appena preso i suoi parser')
    assert suo[0] != piero[0], 'l-estraneo e- entrato nell-account del proprietario'
    assert suo[3] == 0, 'l-estraneo risulta amministratore'
    assert suo[2] is None, 'l-estraneo ha ereditato un origin_profile'


@pytest.mark.parametrize('valore', [
    '"987654321"',      # incollato con le virgolette
    "'987654321'",      # con gli apici
    '+987654321',        # col segno, come lo scrive chi pensa a un numero di telefono
    '987 654 321',       # con gli spazi dentro
    '٩٨٧',               # cifre arabe-indiane: `.isdigit()` le accetta, Telegram no
    '001234',            # zero iniziale: passa come cifra, ma Telegram manda 1234
    '0',                 # non e' l'id di nessuno
])
def test_un_TELEGRAM_ADMIN_ID_malformato_viene_RICONOSCIUTO(valore, monkeypatch):
    """Le sette forme sbagliate sono tutte silenziose, e due ingannano i controlli ovvi.

    Un valore malformato non solleva e non collega: il confronto con l'`id` che Telegram
    manda non combacia mai, quindi nessun collegamento nuovo puo' nascere e il proprietario
    resta un utente come gli altri. Da qui il valore di questo riconoscimento: il login lo
    consulta per NON applicare l'invariante su un valore che non descrive nessuno — senza,
    scioglieva il collegamento buono e faceva nascere un account vuoto (vedi
    `test_un_ADMIN_ID_malformato_NON_scioglie_un_collegamento_BUONO`), e l'avviso all'avvio
    era il solo segnale.

    I due che ingannano: le cifre arabo-indiane sono il motivo per cui il controllo usa una
    regex e non `.isdigit()`, perche' `'٩٨٧'.isdigit()` e' `True` e quelle cifre non
    combaciano con nessun id Telegram; lo zero iniziale e `0` sono il motivo per cui la regex
    e' `[1-9][0-9]*` e non `[0-9]+`, che li accetterebbe entrambi. Un controllo che accetta il
    valore sbagliato e' un controllo che non c'e'. Segnalato da GPT-5.5 sulla PR #24; il
    disallineamento fra questo docstring e la regex davvero implementata, da CodeRabbit.
    """
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', valore)
    assert main.admin_id_malformato() is True, (
        f'{valore!r} non viene riconosciuto come malformato: il proprietario non avra- '
        'nessun posto dove leggere perche- il suo account risulta vuoto')


@pytest.mark.parametrize('valore', ['987654321', '1', '10'])
def test_un_TELEGRAM_ADMIN_ID_BUONO_non_viene_segnalato(valore, monkeypatch):
    """Il verso opposto: un avviso che scatta sul valore giusto insegna a ignorarlo."""
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', valore)
    assert main.admin_id_malformato() is False, f'{valore!r} segnalato a torto'


def test_la_variabile_NON_configurata_non_e_un_errore(monkeypatch):
    """La stringa vuota non e' malformata: e' la variabile assente.

    Ha il suo comportamento documentato — nessun collegamento, il proprietario resta un
    utente come gli altri — e non e' uno stato da segnalare. Un avviso che scatta quando
    non c'e' niente di sbagliato e' un avviso che si impara a ignorare, e allora non
    servira' nemmeno il giorno in cui il valore E' sbagliato.
    """
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', '')
    assert main.admin_id_malformato() is False


def test_gli_spazi_ai_bordi_li_toglie_la_LETTURA_non_il_controllo(tmp_path):
    """Chiesto da Claude Fable 5 e da GPT-5.5 sulla PR #24, e avevano ragione a chiederlo.

    La versione precedente di questo caso stava dentro il test qui sopra come
    `'  987654321  '`, e faceva `monkeypatch.setattr(main, ..., valore.strip())`: **lo strip
    lo faceva il test**. Non dimostrava che la lettura normalizzi, dimostrava che
    `str.strip()` funziona — un test autoreferenziale, cioe' la forma di copertura finta che
    questo repository passa il tempo a smascherare.

    Qui la misura e' sul percorso reale: un processo separato con la variabile **sporca**
    nell'ambiente, e la costante di modulo letta da `os.getenv(...).strip()` come in
    produzione. Il sottoprocesso serve perche' quella costante si calcola all'import, e
    ricaricare `main` nel processo dei test rilegherebbe i suoi globali per tutti i test
    successivi.
    """
    import subprocess
    import sys

    # `ambiente_di_supporto` e non un dizionario scritto qui: e' la FONTE UNICA
    # dell'ambiente dei sottoprocessi di test (regola 3), e la prima versione di questo test
    # la aggirava con un dict inline. Non era pedanteria — quel dict passava solo `PATH` e
    # `PYTHONPATH`, mentre la whitelist passa anche `HOME`, `LANG` e `TMPDIR`, che su una
    # macchina di CI diversa dalla mia possono servire all'interprete. Un test che fallisce
    # per l'ambiente e non per il codice e' rumore che si impara a ignorare. Segnalato come
    # rischio da GPT-5.5 sulla PR #24.
    #
    # `TELEGRAM_ADMIN_ID` e' fra le CHIAVI_PERICOLOSE, quindi non arriva per eredita': qui
    # si passa DI PROPOSITO, che e' il caso che quel modulo documenta.
    esito = subprocess.run(
        [sys.executable, '-c',
         'import main; print(repr(main.TELEGRAM_ADMIN_ID)); print(main.admin_id_malformato())'],
        cwd=str(RADICE), capture_output=True, text=True, timeout=60,
        env=ambiente_di_supporto(PYTHONPATH=str(RADICE),
                                 TELEGRAM_ADMIN_ID='  987654321\n',
                                 DB_PATH=str(tmp_path / 'x.db')))
    assert esito.returncode == 0, esito.stderr
    righe = esito.stdout.strip().splitlines()
    assert righe[0] == "'987654321'", (
        f'la lettura non ripulisce i bordi: {righe[0]}. Chi incolla il valore con un ritorno '
        'a capo — cioe- chiunque copi da una pagina — otterrebbe un ID che non combacia mai')
    assert righe[1] == 'False', 'un valore buono con spazi ai bordi risulta malformato'


def test_un_account_con_PARSER_viene_RIFIUTATO_non_assorbito(tmp_path, monkeypatch):
    """Un account che possiede un parser e' di qualcuno: si rifiuta, non si assorbe.

    **Questo test ha preso il posto di uno che misurava la cosa sbagliata.** La versione
    precedente dava un parser all'account nato per errore e pretendeva che la riconciliazione
    ne ri-disambiguasse lo slug — cioe' dava per buono che assorbirlo fosse giusto. Poi il
    gate finale ha mostrato che quel presupposto e' la violazione dell'isolamento fra utenti:
    se `TELEGRAM_ADMIN_ID` contiene per sbaglio l'ID di un cliente, assorbire significa
    derubarlo. Ora un account con parser o chat **non si tocca**.

    La copertura sulla collisione degli slug non e' andata perduta: `_trasferisci_parser`
    resta esercitata sul percorso della migrazione da
    `tests/relay/test_schema.py::test_il_trasferimento_dei_parser_regge_uno_SLUG_in_collisione`
    e da `test_piu_slug_in_collisione_ricevono_suffissi_DETERMINISTICI`. Quello che cambia e'
    che dal percorso del **login** quel caso non si raggiunge piu', perche' viene rifiutato
    prima.
    """
    import sqlite3
    from fastapi import HTTPException

    percorso = _prepara(tmp_path, monkeypatch, 'con_parser.db')
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', '')
    main.login_telegram(main.LoginTelegramIn(**_dati_login()))

    c = sqlite3.connect(percorso)
    vuoto = c.execute('SELECT id FROM users WHERE telegram_id=?', (ADMIN_FINTO,)).fetchone()[0]
    piero = c.execute('SELECT id FROM users WHERE origin_profile=?',
                      (main.PIERO_PROFILE,)).fetchone()[0]
    c.execute("INSERT INTO parsers(name, header, user_id, slug, ordine, active)"
              " VALUES ('Parser_suo','P.Bet. SUO',?,'parser-suo',77,1)", (vuoto,))
    c.commit()
    c.close()

    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', ADMIN_FINTO)
    with pytest.raises(HTTPException) as scoppio:
        main.login_telegram(main.LoginTelegramIn(**_dati_login()))
    assert scoppio.value.status_code == 409, scoppio.value.status_code
    # Il messaggio non nomina utenti ne- identificativi: chi lo riceve puo- essere un
    # cliente qualunque, e il dettaglio sta nel log del proprietario.
    assert 'utente' not in str(scoppio.value.detail).lower()

    c = sqlite3.connect(percorso)
    assert c.execute('SELECT COUNT(*) FROM parsers WHERE user_id=?',
                     (vuoto,)).fetchone()[0] == 1, 'il parser e- stato travasato'
    assert c.execute('SELECT telegram_id FROM users WHERE id=?',
                     (vuoto,)).fetchone()[0] == ADMIN_FINTO, 'il telegram_id e- stato azzerato'
    assert c.execute('SELECT telegram_id FROM users WHERE id=?',
                     (piero,)).fetchone()[0] is None, 'la riga del proprietario si e- collegata'
    tracciato = c.execute("SELECT COUNT(*) FROM admin_audit"
                          " WHERE action='collegamento_admin_rifiutato'").fetchone()[0]
    c.close()
    assert tracciato == 1, (
        'il rifiuto non e- tracciato in admin_audit: il proprietario non ha modo di sapere '
        'che la sua variabile e- sbagliata, e il login del cliente fallisce senza spiegazione')


def test_le_sessioni_dell_account_SVUOTATO_muoiono_con_la_riparazione(tmp_path, monkeypatch):
    """Un cookie emesso prima della riparazione non deve restare valido dopo.

    Segnalato indipendentemente da Claude Fable 5 e da CodeRabbit sulla PR #24. Non e' un
    buco di sicurezza — quel cookie appartiene comunque al proprietario — ma e' il sintomo
    che questa PR esiste per chiudere: con il cookie vecchio continuerebbe a vedere la
    **dashboard vuota** fino alla scadenza per inattivita', mentre il suo account e' stato
    riparato. `session_version` esiste per invalidare SUBITO, e un account riconciliato via
    e' esattamente il caso.
    """
    percorso = str(tmp_path / 'sessioni.db')
    monkeypatch.setattr(main, 'DB_PATH', percorso)
    monkeypatch.setattr(main, 'BOT_TOKEN', BOT_FINTO)
    monkeypatch.setattr(main, 'SEGRETO_SESSIONE', SEGRETO_ATTESO)

    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', '')
    prima = main.login_telegram(main.LoginTelegramIn(**_dati_login()))
    cookie_vecchio = None
    for pezzo in (prima.headers.get('set-cookie') or '').split(';'):
        chiave, _, valore = pezzo.strip().partition('=')
        if chiave == main.NOME_COOKIE:
            cookie_vecchio = valore
    assert cookie_vecchio, 'nessun cookie dal primo login'

    sessione = main.leggi_sessione(cookie_vecchio)
    vuoto = _riga_utente(percorso, 'telegram_id', ADMIN_FINTO)
    assert sessione['utente'] == vuoto[0], 'il cookie non e- dell-account vuoto'

    class RichiestaFinta:
        cookies = {main.NOME_COOKIE: cookie_vecchio}

    assert main.utente_dalla_sessione(RichiestaFinta()) is not None, (
        'il cookie non era valido nemmeno prima: il test non misura la riparazione')

    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', ADMIN_FINTO)
    # Il consenso all'assorbimento della riga vuota: dal fail-closed di GPT-5.6 Sol sulla
    # PR #24 e' un gesto DELIBERATO — senza, il servizio rifiuta con 409, perche' una riga
    # vuota puo' anche essere di un cliente appena iscritto — e dal rischio alzato da
    # GPT-5.5 il valore e' l'ID DELLA RIGA, non un `1` globale.
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_RECONCILE', str(vuoto[0]))
    main.login_telegram(main.LoginTelegramIn(**_dati_login()))

    assert main.utente_dalla_sessione(RichiestaFinta()) is None, (
        'il cookie emesso per l-account svuotato vale ancora dopo la riparazione: chi lo '
        'presenta continua a vedere la dashboard vuota che questa PR chiude')


def test_cambiare_TELEGRAM_ADMIN_ID_REVOCA_le_sessioni_della_vecchia_identita(tmp_path, monkeypatch):
    """**Bloccante di GPT-5.6 Sol sulla PR #24**, ed e' il piu' grave di questa PR.

    Scenario: la riga `PIERO` possiede gia' `telegram_id = X`, il proprietario cambia
    `TELEGRAM_ADMIN_ID` in `Y`, e `Y` fa login. Il codice scriveva `Y` su quella riga e
    **non toccava `session_version`** — quindi le sessioni aperte come `X` restavano valide
    **con accesso amministrativo**, perche' il cookie e' legato all'`id` della riga e alla
    versione, non al `telegram_id`.

    E non scadevano nemmeno: `/api/me` **rinnova** il cookie a ogni richiesta valida, quindi
    una sessione tenuta attiva e' immortale. Il caso concreto e' quello che fa paura: se in
    quella variabile fosse finito l'ID sbagliato — un estraneo, o un account compromesso —
    quell'estraneo avrebbe una sessione da amministratore sulla riga che possiede i parser, e
    **correggere la variabile non gliela toglierebbe**.

    Cambiare l'identita' Telegram del proprietario e' esattamente il caso per cui
    `session_version` esiste: «invalidare subito, senza aspettare la scadenza».

    Il test misura anche il rovescio, che e' la trappola della correzione: il cookie **del
    login che sta avvenendo** deve restare valido. Incrementare la versione nel database
    senza firmare il cookie con quella nuova produrrebbe un login che riesce e una sessione
    morta all'istante.
    """
    percorso = str(tmp_path / 'identita.db')
    monkeypatch.setattr(main, 'DB_PATH', percorso)
    monkeypatch.setattr(main, 'BOT_TOKEN', BOT_FINTO)
    monkeypatch.setattr(main, 'SEGRETO_SESSIONE', SEGRETO_ATTESO)

    def cookie_di(risposta):
        for pezzo in (risposta.headers.get('set-cookie') or '').split(';'):
            chiave, _, valore = pezzo.strip().partition('=')
            if chiave == main.NOME_COOKIE:
                return valore
        return None

    class Richiesta:
        def __init__(self, cookie):
            self.cookies = {main.NOME_COOKIE: cookie}

    # L'identita' VECCHIA entra e ottiene una sessione da amministratore.
    vecchia = '111111111'
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', vecchia)
    prima = main.login_telegram(main.LoginTelegramIn(**_dati_login(id=vecchia)))
    cookie_vecchio = cookie_di(prima)
    assert cookie_vecchio
    io = main.utente_dalla_sessione(Richiesta(cookie_vecchio))
    assert io is not None and io['is_admin'] is True, (
        'la vecchia identita- non ha una sessione da amministratore: il test non misura la '
        'posta in gioco')
    piero = _riga_utente(percorso, 'origin_profile', main.PIERO_PROFILE)
    assert piero[1] == vecchia and io['id'] == piero[0]

    # Il proprietario corregge la variabile e la NUOVA identita' entra.
    nuova = '222222222'
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', nuova)
    dopo = main.login_telegram(main.LoginTelegramIn(**_dati_login(id=nuova)))
    assert dopo.status_code == 200

    dopo_piero = _riga_utente(percorso, 'origin_profile', main.PIERO_PROFILE)
    assert dopo_piero[1] == nuova, f'la riga PIERO non ha la nuova identita-: {dopo_piero}'

    assert main.utente_dalla_sessione(Richiesta(cookie_vecchio)) is None, (
        'la sessione della VECCHIA identita- vale ancora, con accesso amministrativo, dopo '
        'che il proprietario ha cambiato TELEGRAM_ADMIN_ID. Se in quella variabile era '
        'finito l-ID di un estraneo, correggerla non gli toglie il pannello — e siccome '
        '/api/me rinnova il cookie, la sua sessione non scade nemmeno')

    # Il rovescio: il cookie del login appena avvenuto deve funzionare.
    cookie_nuovo = cookie_di(dopo)
    assert cookie_nuovo, 'nessun cookie dal login della nuova identita-'
    adesso = main.utente_dalla_sessione(Richiesta(cookie_nuovo))
    assert adesso is not None, (
        'il cookie del login appena avvenuto e- gia- morto: la versione firmata non e- '
        'quella scritta nel database')
    assert adesso['id'] == dopo_piero[0] and adesso['is_admin'] is True


def test_un_login_RIPETUTO_con_lo_stesso_id_non_butta_fuori_gli_altri_dispositivi(tmp_path, monkeypatch):
    """La revoca deve scattare al CAMBIO di identita', non a ogni login.

    E' il verso opposto del test qui sopra, e serve: incrementare `session_version` a ogni
    login del proprietario gli chiuderebbe la sessione sul telefono ogni volta che entra dal
    computer. Sarebbe un difetto introdotto dalla correzione di un difetto — e questa PR ne
    ha gia' visti tre.
    """
    percorso = str(tmp_path / 'ripetuto.db')
    monkeypatch.setattr(main, 'DB_PATH', percorso)
    monkeypatch.setattr(main, 'BOT_TOKEN', BOT_FINTO)
    monkeypatch.setattr(main, 'SEGRETO_SESSIONE', SEGRETO_ATTESO)
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', ADMIN_FINTO)

    prima = main.login_telegram(main.LoginTelegramIn(**_dati_login()))
    cookie = None
    for pezzo in (prima.headers.get('set-cookie') or '').split(';'):
        chiave, _, valore = pezzo.strip().partition('=')
        if chiave == main.NOME_COOKIE:
            cookie = valore

    class Richiesta:
        cookies = {main.NOME_COOKIE: cookie}

    assert main.utente_dalla_sessione(Richiesta()) is not None

    # Secondo login, stesso ID: e' il proprietario che entra da un altro dispositivo.
    main.login_telegram(main.LoginTelegramIn(**_dati_login()))
    assert main.utente_dalla_sessione(Richiesta()) is not None, (
        'un secondo login con lo STESSO id ha invalidato la sessione precedente: il '
        'proprietario si troverebbe buttato fuori dal telefono ogni volta che entra dal '
        'computer')


def _prepara(tmp_path, monkeypatch, nome='iso.db'):
    """Un relay in processo con database proprio, bot e segreto finti."""
    percorso = str(tmp_path / nome)
    monkeypatch.setattr(main, 'DB_PATH', percorso)
    monkeypatch.setattr(main, '_PERCORSI_MIGRATI', set())
    monkeypatch.setattr(main, 'BOT_TOKEN', BOT_FINTO)
    monkeypatch.setattr(main, 'SEGRETO_SESSIONE', SEGRETO_ATTESO)
    return percorso


def test_l_id_di_un_CLIENTE_come_admin_non_ne_fonde_l_account(tmp_path, monkeypatch):
    """**Violazione dell'isolamento fra utenti**, la priorita' 7 di `CLAUDE.md`.

    Bloccante di Claude Fable 5 sulla PR #24, e misurato prima di correggerlo: bastava che
    il proprietario sbagliasse una cifra di `TELEGRAM_ADMIN_ID` e ci finisse l'ID di un
    cliente. Al login di quel cliente:

        parser rimasti a lui: 0 | passati a PIERO: 2
        la chat di lui appartiene a: PIERO
        riga PIERO: telegram_id del cliente, is_admin=1
        riga del cliente: telegram_id azzerato

    Il cliente perdeva tutto **e** otteneva la dashboard del proprietario. Irreversibile,
    e senza nessun errore: per il proprietario sembrava che il collegamento fosse andato.

    La riparazione idempotente presumeva che la riga da assorbire fosse **sempre**
    l'account nato per errore. Non lo e': un account che possiede parser o chat e' di
    qualcuno. Quindi ora la riconciliazione avviene solo se la riga non possiede niente di
    cio' che rende un account un account, e altrimenti **rifiuta** — senza toccare nessuna
    delle due righe, perche' fra sbagliare in un verso e sbagliare nell'altro c'e' la
    differenza fra un login rifiutato e un utente derubato.
    """
    import sqlite3
    percorso = _prepara(tmp_path, monkeypatch, 'cliente_come_admin.db')
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', '')

    cliente = _dati_login(id='555000777', first_name='Marco')
    main.login_telegram(main.LoginTelegramIn(**cliente))
    c = sqlite3.connect(percorso)
    marco = c.execute("SELECT id FROM users WHERE telegram_id='555000777'").fetchone()[0]
    piero = c.execute("SELECT id FROM users WHERE origin_profile=?",
                      (main.PIERO_PROFILE,)).fetchone()[0]
    c.execute("INSERT INTO parsers(name,header,user_id,slug,ordine,active)"
              " VALUES ('Parser_di_Marco','P.Bet. MARCO',?,'parser-di-marco',50,1)", (marco,))
    c.execute("INSERT INTO chats(telegram_chat_id,title,owner_user_id)"
              " VALUES ('-100777','Canale di Marco',?)", (marco,))
    c.commit()
    c.close()

    # Il proprietario sbaglia la variabile e ci mette l'ID di Marco.
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', '555000777')
    from fastapi import HTTPException
    try:
        main.login_telegram(main.LoginTelegramIn(**cliente))
        rifiutato = None
    except HTTPException as e:
        rifiutato = e.status_code

    c = sqlite3.connect(percorso)
    riga_piero = c.execute('SELECT telegram_id, is_admin FROM users WHERE id=?', (piero,)).fetchone()
    riga_marco = c.execute('SELECT telegram_id FROM users WHERE id=?', (marco,)).fetchone()
    suoi = c.execute('SELECT COUNT(*) FROM parsers WHERE user_id=?', (marco,)).fetchone()[0]
    sua_chat = c.execute("SELECT owner_user_id FROM chats WHERE telegram_chat_id='-100777'").fetchone()[0]
    c.close()

    assert riga_piero[0] is None, (
        f'la riga del proprietario ha preso il telegram_id del cliente: {riga_piero}. '
        'Il cliente entra nell-account del proprietario')
    assert riga_marco[0] == '555000777', (
        'il cliente ha perso il proprio telegram_id: non riesce piu- a entrare da nessuna parte')
    assert suoi == 1, f'{suoi} parser rimasti al cliente invece di 1: i suoi dati sono stati travasati'
    assert sua_chat == marco, 'la chat del cliente e- passata al proprietario'
    assert rifiutato is not None, (
        'il login e- riuscito: con una variabile sbagliata il servizio deve RIFIUTARE, non '
        'scegliere quale dei due utenti derubare')


def test_cambiare_la_variabile_TOGLIE_l_accesso_alla_vecchia_identita(tmp_path, monkeypatch):
    """La revoca non puo' dipendere dal fatto che il NUOVO id faccia login.

    Bloccante di GPT-5.6 Sol sulla PR #24, misurato prima di correggerlo: cambiata la
    variabile, il vecchio ID rifaceva login e otteneva ancora `utente: 1`, cioe' la riga
    del proprietario, con la sua dashboard e i suoi parser. La revoca introdotta nel commit
    precedente scattava solo quando il **nuovo** ID entrava — e se il nuovo non entra mai,
    il vecchio resta amministratore per sempre.

    Cambiare `TELEGRAM_ADMIN_ID` e' il gesto con cui si TOGLIE l'accesso a un'identita': se
    non lo toglie, quel gesto e' teatro. Ora l'invariante e' verificata a ogni login,
    chiunque lo faccia: se la riga del proprietario porta un `telegram_id` diverso da
    quello configurato, il collegamento e' stantio e viene sciolto.
    """
    import sqlite3
    percorso = _prepara(tmp_path, monkeypatch, 'revoca_vecchio.db')
    VECCHIO, NUOVO = '111000111', '222000222'

    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', VECCHIO)
    main.login_telegram(main.LoginTelegramIn(**_dati_login(id=VECCHIO)))
    c = sqlite3.connect(percorso)
    piero = c.execute('SELECT id, telegram_id FROM users WHERE origin_profile=?',
                      (main.PIERO_PROFILE,)).fetchone()
    c.close()
    assert piero[1] == VECCHIO, 'il primo login non ha collegato: il test non misura la revoca'

    # Il proprietario cambia la variabile PROPRIO per togliere l'accesso al vecchio ID.
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', NUOVO)

    from fastapi import HTTPException
    try:
        risposta = main.login_telegram(main.LoginTelegramIn(**_dati_login(id=VECCHIO)))
        utente = json.loads(bytes(risposta.body).decode()).get('utente')
    except HTTPException:
        utente = None

    c = sqlite3.connect(percorso)
    dopo = c.execute('SELECT telegram_id FROM users WHERE id=?', (piero[0],)).fetchone()[0]
    c.close()

    assert utente != piero[0], (
        f'il VECCHIO id entra ancora nell-account del proprietario (utente {utente}): '
        'cambiare la variabile non gli ha tolto niente')
    assert dopo != VECCHIO, (
        f'la riga del proprietario porta ancora il vecchio telegram_id ({dopo}): il '
        'collegamento stantio sopravvive, quindi la revoca non e- avvenuta')


def test_due_primi_login_CONCORRENTI_dell_admin_non_uccidono_il_cookie(tmp_path, monkeypatch):
    """`SELECT` e `UPDATE` non atomici sul percorso della riparazione.

    Alzato da Claude Fable 5 e da GPT-5.6 Sol indipendentemente sulla PR #24, e il sintomo
    che Sol nomina e' il piu' insidioso: due login concorrenti incrementano `session_version`
    **due volte**, quindi il cookie firmato dal primo nasce con la versione 2 mentre il
    database e' a 3 — il login «riesce» e il cookie e' morto. L'utente vede una schermata
    di accesso riuscito e la richiesta successiva risponde 401.

    Il test pretende che **ogni** cookie emesso da un login riuscito sia valido subito dopo.
    E' l'unica formulazione che coglie il difetto senza dipendere da quale thread arriva
    prima.
    """
    import threading
    percorso = _prepara(tmp_path, monkeypatch, 'concorrenti.db')

    # Lo scenario deve essere un CAMBIO di identita', non un primo login: `cambia_identita`
    # e' falsa quando la riga PIERO non ha ancora un telegram_id, quindi due primi login
    # concorrenti non incrementano niente e non misurerebbero il difetto. La prima versione
    # di questo test faceva esattamente quell'errore e passava verde.
    VECCHIO, NUOVO = '111000111', '222000222'
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', VECCHIO)
    main.login_telegram(main.LoginTelegramIn(**_dati_login(id=VECCHIO)))
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', NUOVO)

    dati = _dati_login(id=NUOVO)
    esiti = []
    porta = threading.Barrier(6)

    def prova():
        porta.wait()
        try:
            r = main.login_telegram(main.LoginTelegramIn(**dati))
            cookie = None
            for pezzo in (r.headers.get('set-cookie') or '').split(';'):
                chiave, _, valore = pezzo.strip().partition('=')
                if chiave == main.NOME_COOKIE:
                    cookie = valore
            esiti.append(('ok', cookie))
        except Exception as e:
            esiti.append((type(e).__name__, getattr(e, 'status_code', None)))

    fili = [threading.Thread(target=prova) for _ in range(6)]
    for f in fili:
        f.start()
    _attendi_tutti(fili, esiti)

    guasti = [e for e in esiti if e[0] != 'ok']
    assert not guasti, f'login concorrenti hanno sollevato: {guasti}'

    class Richiesta:
        cookies = {}

    morti = []
    for _, cookie in esiti:
        Richiesta.cookies = {main.NOME_COOKIE: cookie}
        if main.utente_dalla_sessione(Richiesta()) is None:
            morti.append(cookie)
    assert not morti, (
        f'{len(morti)} cookie su {len(esiti)} sono nati morti: session_version e- stata '
        'incrementata da un login concorrente dopo la firma, quindi il login «riesce» e la '
        'richiesta successiva risponde 401')


def test_un_ADMIN_ID_malformato_NON_scioglie_un_collegamento_BUONO(tmp_path, monkeypatch):
    """Bloccante di GPT-5.6 Sol e di CodeRabbit sulla PR #24, trovato indipendentemente.

    `admin_id_malformato()` esisteva gia' e **segnalava soltanto**, all'avvio. Il percorso
    di login non lo guardava: confrontava il `telegram_id` della riga del proprietario con
    il valore grezzo della variabile. Misurato prima di correggerlo, con
    `TELEGRAM_ADMIN_ID='"987654321"'` — le virgolette che si prendono incollando un valore
    nel pannello Railway — e la riga del proprietario collegata a `987654321`:

        CASO 1 combacia (`'987654321' != '"987654321"'`) -> telegram_id azzerato,
        session_version incrementata;
        `e_amministratore` e' falsa, perche' `data.id` non sara' mai `'"987654321"'`,
        quindi CASO 2 non puo' ricollegare;
        `riga` e' None -> nasce un secondo account `registrato`.

    Cioe': **un refuso nel pannello chiude il proprietario fuori dal proprio account**, e il
    solo segnale e' una riga di log all'avvio. Un valore malformato non descrive nessuna
    identita', quindi l'invariante non va applicata affatto: non si scioglie un collegamento
    buono in nome di un valore con cui nessun collegamento nuovo puo' nascere.
    """
    import sqlite3
    percorso = _prepara(tmp_path, monkeypatch, 'admin_malformato.db')
    BUONO = '987654321'

    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', BUONO)
    main.login_telegram(main.LoginTelegramIn(**_dati_login(id=BUONO)))
    c = sqlite3.connect(percorso)
    piero = c.execute('SELECT id, telegram_id FROM users WHERE origin_profile=?',
                      (main.PIERO_PROFILE,)).fetchone()
    c.close()
    assert piero[1] == BUONO, 'il primo login non ha collegato: il test non misura niente'

    # Il refuso: lo stesso id, con le virgolette che il pannello si porta dietro.
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', f'"{BUONO}"')
    assert main.admin_id_malformato() is True, 'il valore di prova non e- malformato'

    from fastapi import HTTPException
    try:
        risposta = main.login_telegram(main.LoginTelegramIn(**_dati_login(id=BUONO)))
        utente = json.loads(bytes(risposta.body).decode()).get('utente')
    except HTTPException:
        utente = None

    c = sqlite3.connect(percorso)
    dopo = c.execute('SELECT telegram_id FROM users WHERE id=?', (piero[0],)).fetchone()[0]
    quanti = c.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    c.close()

    assert dopo == BUONO, (
        f'il collegamento buono e- stato sciolto (telegram_id ora {dopo!r}) da una variabile '
        'malformata, con cui nessun collegamento nuovo puo- nascere')
    assert utente == piero[0], (
        f'il proprietario e- entrato in un account diverso dal suo (utente {utente}, atteso '
        f'{piero[0]}): un refuso nel pannello lo chiude fuori')
    assert quanti == 1, (
        f'{quanti} righe in users invece di una: la variabile malformata ha fatto nascere un '
        'secondo account per il proprietario')


def test_SVUOTARE_la_variabile_non_scioglie_il_collegamento(tmp_path, monkeypatch):
    """Secondo bloccante di GPT-5.6 Sol sulla PR #24, e qui la risposta e' NO, deliberata.

    Sol chiede che togliere `TELEGRAM_ADMIN_ID` revochi il collegamento. Misurato cosa
    produrrebbe: la riga del proprietario resterebbe senza `telegram_id`, e siccome con la
    variabile vuota `e_amministratore` e' falsa, il suo login successivo non la
    ricollegherebbe — **nascerebbe un secondo account**. E' esattamente il lockout del test
    qui sopra, con un'altra causa.

    La variabile vuota significa «nessuna invariante dichiarata», non «revoca». Il gesto per
    togliere l'accesso a un'identita' e' CAMBIARE il valore con quello nuovo, ed e' misurato
    da `test_cambiare_la_variabile_TOGLIE_l_accesso_alla_vecchia_identita`. Questo test
    esiste per impedire che la richiesta di Sol venga implementata per errore: il
    proprietario ha svuotato la variabile davvero, il 13/08/2026, per un motivo che non
    c'entrava niente con l'accesso (un fallimento di build su Railway).
    """
    import sqlite3
    percorso = _prepara(tmp_path, monkeypatch, 'variabile_svuotata.db')
    ID = '987654321'

    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', ID)
    main.login_telegram(main.LoginTelegramIn(**_dati_login(id=ID)))
    c = sqlite3.connect(percorso)
    piero = c.execute('SELECT id FROM users WHERE origin_profile=?',
                      (main.PIERO_PROFILE,)).fetchone()[0]
    c.close()

    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', '')
    risposta = main.login_telegram(main.LoginTelegramIn(**_dati_login(id=ID)))
    utente = json.loads(bytes(risposta.body).decode()).get('utente')

    c = sqlite3.connect(percorso)
    dopo = c.execute('SELECT telegram_id FROM users WHERE id=?', (piero,)).fetchone()[0]
    quanti = c.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    c.close()

    assert dopo == ID, f'svuotare la variabile ha sciolto il collegamento (ora {dopo!r})'
    assert utente == piero, f'il proprietario e- finito in un altro account (utente {utente})'
    assert quanti == 1, f'{quanti} righe in users invece di una'


def test_la_riparazione_non_RISCRIVE_la_storia_di_admin_audit(tmp_path, monkeypatch):
    """Segnalato da CodeRabbit sulla PR #24, e la conseguenza e' un audit che si smentisce.

    Scenario reale, in due tempi: `TELEGRAM_ADMIN_ID` punta all'account di un cliente che
    possiede parser, il login viene RIFIUTATO e la riga `collegamento_admin_rifiutato` dice
    «la decisione riguardava l'account X». Poi quell'account resta senza parser e senza chat,
    e la riparazione lo travasa nel proprietario. Se il travaso riscrive anche
    `admin_audit.target_user_id`, quella riga diventa «il proprietario ha rifiutato di
    collegarsi al proprietario»: auto-referenziale, cioe' inutile proprio nel momento in cui
    serve, perche' `admin_audit` e' l'unico posto dove il proprietario legge PERCHE' un login
    e' stato rifiutato.

    La distinzione e': le altre colonne sono **dati** dell'utente e vanno dove vanno i dati;
    quelle due sono **storia**, e la storia di una riga che non viene cancellata resta sua.
    """
    import sqlite3
    percorso = _prepara(tmp_path, monkeypatch, 'audit_storia.db')
    c = main.db()
    c.execute("INSERT INTO users(telegram_id, status) VALUES ('555000555','registrato')")
    perdente = c.execute("SELECT id FROM users WHERE telegram_id='555000555'").fetchone()[0]
    piero = c.execute('SELECT id FROM users WHERE origin_profile=?',
                      (main.PIERO_PROFILE,)).fetchone()[0]
    main._annota_admin(c, piero, 'collegamento_admin_rifiutato', bersaglio=perdente)
    c.commit()

    main.riconcilia_su_utente(c, da_utente=perdente, a_utente=piero)
    c.commit()
    c.close()

    c = sqlite3.connect(percorso)
    bersaglio = c.execute("SELECT target_user_id FROM admin_audit"
                          " WHERE action='collegamento_admin_rifiutato'").fetchone()[0]
    c.close()
    assert bersaglio == perdente, (
        f'la traccia del rifiuto punta ora a {bersaglio} invece di {perdente}: la riparazione '
        'ha riscritto la storia, e il proprietario legge un rifiuto contro se- stesso')


def test_un_LOCK_sul_database_non_LASCIA_APERTA_la_connessione(tmp_path, monkeypatch):
    """Rilievo minore di Claude Fable 5 sulla PR #24, e aveva ragione.

    `BEGIN IMMEDIATE` stava FUORI dal `try`: sotto contesa quel comando solleva
    `OperationalError: database is locked`, e da fuori dal `try` nessuno chiude la
    connessione. Ogni login perso sotto lock lasciava un descrittore aperto — su un container
    Railway che non riparte, la perdita si accumula.

    Si misura con una connessione finta che solleva su `BEGIN IMMEDIATE`, perche' e' l'unico
    modo di OSSERVARE la chiusura: un test che tiene un lock vero vede l'eccezione ma non
    puo' vedere se la connessione e' stata chiusa.
    """
    import sqlite3
    _prepara(tmp_path, monkeypatch, 'lock.db')
    vera = main.db()
    chiuse = []

    class ConnessioneCheSiRifiuta:
        def execute(self, sql, *args):
            if sql.startswith('BEGIN'):
                raise sqlite3.OperationalError('database is locked')
            return vera.execute(sql, *args)

        def commit(self):
            return vera.commit()

        def rollback(self):
            return vera.rollback()

        def close(self):
            chiuse.append(True)

    monkeypatch.setattr(main, 'db', lambda: ConnessioneCheSiRifiuta())
    with pytest.raises(sqlite3.OperationalError):
        main.login_telegram(main.LoginTelegramIn(**_dati_login()))
    vera.close()

    assert chiuse, (
        'la connessione non e- stata chiusa: un login perso per lock perde un descrittore, e '
        'sotto contesa la perdita si accumula fino a esaurire i descrittori del processo')


def _cliente_nudo(percorso, monkeypatch, id_cliente):
    """Un cliente registrato che non possiede ANCORA niente, e la riga del proprietario."""
    import sqlite3
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', '')
    main.login_telegram(main.LoginTelegramIn(**_dati_login(id=id_cliente)))
    c = sqlite3.connect(percorso)
    cliente = c.execute('SELECT id FROM users WHERE telegram_id=?', (id_cliente,)).fetchone()[0]
    piero = c.execute('SELECT id FROM users WHERE origin_profile=?',
                      (main.PIERO_PROFILE,)).fetchone()[0]
    parser = c.execute('SELECT COUNT(*) FROM parsers WHERE user_id=?', (cliente,)).fetchone()[0]
    chat = c.execute('SELECT COUNT(*) FROM chats WHERE owner_user_id=?',
                     (cliente,)).fetchone()[0]
    c.close()
    assert (parser, chat) == (0, 0), 'il cliente possiede qualcosa: non e- il caso in esame'
    return cliente, piero


def test_un_cliente_che_non_possiede_NIENTE_non_viene_assorbito(tmp_path, monkeypatch):
    """Bloccante di GPT-5.6 Sol sulla PR #24, ed e' reale: `possiede_qualcosa` non basta.

    Il criterio «possiede parser o chat» distingue un account pieno da uno vuoto, non un
    **cliente** da una riga nata per errore. Un cliente appena registrato non possiede ancora
    niente — e' lo stato normale di chi si iscrive — quindi era indistinguibile dalla riga
    vuota del proprietario. Misurato prima della correzione, con `TELEGRAM_ADMIN_ID` che per
    un refuso conteneva l'ID di quel cliente: al suo login la sua riga veniva svuotata del
    `telegram_id` e la sua identita' Telegram finiva sulla riga del proprietario con
    `is_admin=1`. **Il cliente entrava nella dashboard del proprietario**, che e' la
    violazione dell'isolamento fra utenti, priorita' 7 di `CLAUDE.md`.

    Nessun dato distingue i due casi: sono due righe di `users` con un'identita' e nient'altro.
    Quando nessun dato distingue, l'unico marcatore affidabile e' il **consenso del
    proprietario**, e in sua assenza si fallisce chiusi — vedi
    `test_col_CONSENSO_la_riparazione_funziona_ancora` per il verso opposto.
    """
    import sqlite3
    from fastapi import HTTPException
    percorso = _prepara(tmp_path, monkeypatch, 'cliente_nudo.db')
    CLIENTE = '777000777'
    cliente, piero = _cliente_nudo(percorso, monkeypatch, CLIENTE)

    # Il refuso: nella variabile finisce l'ID del cliente invece di quello del proprietario.
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', CLIENTE)
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_RECONCILE', '')

    with pytest.raises(HTTPException) as errore:
        main.login_telegram(main.LoginTelegramIn(**_dati_login(id=CLIENTE)))
    assert errore.value.status_code == 409, (
        f'il login del cliente ha risposto {errore.value.status_code} invece di 409')

    c = sqlite3.connect(percorso)
    suo = c.execute('SELECT telegram_id, is_admin FROM users WHERE id=?', (cliente,)).fetchone()
    del_proprietario = c.execute('SELECT telegram_id FROM users WHERE id=?',
                                 (piero,)).fetchone()[0]
    tracciato = c.execute("SELECT COUNT(*) FROM admin_audit WHERE target_user_id=?",
                          (cliente,)).fetchone()[0]
    c.close()

    assert suo[0] == CLIENTE, (
        f'la riga del cliente e- stata svuotata del suo telegram_id (ora {suo[0]!r}): '
        'un refuso del proprietario gli ha tolto il suo account')
    assert del_proprietario is None, (
        f'l-identita- del cliente e- finita sulla riga del proprietario ({del_proprietario!r}): '
        'al suo prossimo login il cliente entra nella dashboard di un altro con is_admin=1')
    assert tracciato == 1, (
        'il rifiuto non e- tracciato in admin_audit: il proprietario non ha modo di sapere '
        'che la sua variabile e- sbagliata')


def test_col_CONSENSO_la_riparazione_funziona_ancora(tmp_path, monkeypatch):
    """Il verso opposto, e senza di lui il fail-closed avrebbe rotto cio' che la PR ripara.

    Il caso legittimo esiste: il proprietario ha fatto login PRIMA che
    `TELEGRAM_ADMIN_ID` arrivasse nel processo, quindi una riga vuota possiede il suo
    `telegram_id` e i suoi parser stanno su un'altra. Quella riparazione deve restare
    possibile, altrimenti si torna al lockout irreversibile che questa PR chiude — si e'
    solo spostato il difetto invece di correggerlo.

    Il consenso e' `TELEGRAM_ADMIN_RECONCILE`, che porta l'identificativo della riga da
    assorbire e che il proprietario imposta quando LEGGE il
    409 e sa che quella riga e' la sua. Non e' burocrazia: e' l'unico dato che il servizio
    non puo' dedurre, perche' con la variabile sbagliata anche la fonte dell'identita' e'
    sbagliata.
    """
    import sqlite3
    percorso = _prepara(tmp_path, monkeypatch, 'con_consenso.db')
    SUO = '888000888'
    vuoto, piero = _cliente_nudo(percorso, monkeypatch, SUO)

    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', SUO)
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_RECONCILE', str(vuoto))
    risposta = main.login_telegram(main.LoginTelegramIn(**_dati_login(id=SUO)))
    utente = json.loads(bytes(risposta.body).decode()).get('utente')

    c = sqlite3.connect(percorso)
    del_proprietario = c.execute('SELECT telegram_id, is_admin FROM users WHERE id=?',
                                 (piero,)).fetchone()
    svuotata = c.execute('SELECT telegram_id FROM users WHERE id=?', (vuoto,)).fetchone()[0]
    c.close()

    assert utente == piero, (
        f'col consenso il proprietario non entra nel proprio account (utente {utente}, atteso '
        f'{piero}): il fail-closed ha rotto la riparazione invece di renderla deliberata')
    assert del_proprietario == (SUO, 1), (
        f'la riga del proprietario non e- stata collegata: {del_proprietario!r}')
    assert svuotata is None, 'la riga assorbita conserva il telegram_id, che e- UNIQUE'


def test_il_CONSENSO_non_autorizza_a_fondere_un_account_PIENO(tmp_path, monkeypatch):
    """Il consenso dice «quella riga vuota e' mia», non «prenditi i dati di chiunque».

    Un account che possiede parser o chat resta rifiutato **anche** col consenso: il
    proprietario che imposta la variabile non puo' avere inteso «travasa i parser di un
    cliente sul mio account», e fra le due letture possibili si prende quella che non deruba
    nessuno.
    """
    import sqlite3
    from fastapi import HTTPException
    percorso = _prepara(tmp_path, monkeypatch, 'consenso_pieno.db')
    CLIENTE = '999000999'
    cliente, piero = _cliente_nudo(percorso, monkeypatch, CLIENTE)

    c = sqlite3.connect(percorso)
    c.execute("INSERT INTO parsers(name,header,user_id,slug,ordine,active)"
              " VALUES ('Il_suo_parser','P.Bet. SUO',?,'il-suo-parser',50,1)", (cliente,))
    c.commit()
    c.close()

    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', CLIENTE)
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_RECONCILE', str(cliente))
    with pytest.raises(HTTPException) as errore:
        main.login_telegram(main.LoginTelegramIn(**_dati_login(id=CLIENTE)))
    assert errore.value.status_code == 409

    c = sqlite3.connect(percorso)
    suoi = c.execute('SELECT COUNT(*) FROM parsers WHERE user_id=?', (cliente,)).fetchone()[0]
    c.close()
    assert suoi == 1, f'il consenso ha travasato {1 - suoi} parser di un cliente'


def test_il_CONSENSO_vale_solo_per_LA_RIGA_indicata(tmp_path, monkeypatch):
    """Rischio alzato da GPT-5.5 sulla PR #24: un consenso globale che resta impostato.

    La prima versione del consenso era `TELEGRAM_ADMIN_RECONCILE=1`, cioe' un interruttore
    **globale**: autorizzava l'assorbimento di qualunque riga vuota, per sempre. La
    documentazione diceva di togliere la variabile dopo l'uso, ma una variabile che va
    ricordata di togliere e' una variabile che resta — e da quel momento il fail-closed non
    c'era piu': un futuro refuso in `TELEGRAM_ADMIN_ID` verso la riga di un cliente vuoto
    sarebbe stato assorbito di nuovo, che e' esattamente il bloccante che il consenso doveva
    chiudere.

    Ora il valore e' l'**identificativo della riga** da assorbire, che il proprietario legge
    nel `409` (log e `admin_audit`). Vincolarlo alla riga lo rende innocuo se resta
    impostato, e la ragione e' una proprieta' del codice e non una speranza: la riga assorbita
    **non viene cancellata**, quindi il suo id non viene mai riusato da un utente nuovo.

    I valori provati sono i modi di sbagliare: `'1'` e' l'interruttore globale della prima
    versione — su cui questo test era ROSSO — `id+100` e' il consenso dato una volta per
    un'altra riga e rimasto nell'ambiente, e gli ultimi tre sono valori che non indicano
    nessuna riga: una parola, un numero con una lettera attaccata, e soli spazi. Nessuno di
    loro deve autorizzare niente, e nessuno deve sollevare qualcosa di diverso da un `409`:
    un `ValueError` da una conversione sarebbe un 500 su un login. Chiesto da GPT-5.5.
    """
    import sqlite3
    from fastapi import HTTPException

    for numero, consenso in enumerate(('1', 'ALTRA_RIGA', 'due', '2x', '  ')):
        percorso = _prepara(tmp_path, monkeypatch, f'consenso_legato_{numero}.db')
        SUO = '444000444'
        vuoto, piero = _cliente_nudo(percorso, monkeypatch, SUO)

        monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', SUO)
        valore = str(vuoto + 100) if consenso == 'ALTRA_RIGA' else consenso
        monkeypatch.setattr(main, 'TELEGRAM_ADMIN_RECONCILE', valore)

        with pytest.raises(HTTPException) as errore:
            main.login_telegram(main.LoginTelegramIn(**_dati_login(id=SUO)))
        assert errore.value.status_code == 409, (
            f'consenso {valore!r}: {errore.value.status_code} invece di 409. Quel valore non '
            f'indica la riga {vuoto}, quindi non autorizza questo assorbimento')

        c = sqlite3.connect(percorso)
        suo = c.execute('SELECT telegram_id FROM users WHERE id=?', (vuoto,)).fetchone()[0]
        c.close()
        assert suo == SUO, (
            f'consenso {valore!r}: la riga e- stata assorbita comunque (telegram_id ora '
            f'{suo!r})')


def test_cambiare_la_variabile_UCCIDE_la_sessione_senza_aspettare_un_login(tmp_path,
                                                                          monkeypatch):
    """Bloccante di GPT-5.6 Sol sulla PR #24, terzo giro sullo stesso tema e ancora reale.

    La revoca del collegamento stantio viveva **solo** dentro `/api/login/telegram`. Quindi
    dopo aver cambiato `TELEGRAM_ADMIN_ID` la vecchia identita' perdeva l'accesso al
    **prossimo login** — di chiunque — ma il cookie che aveva **giaÌ€** in mano restava valido
    fino a quel momento. E non scadeva da se': `GET /api/me` rinnova il cookie a ogni
    richiesta valida, quindi una sessione tenuta attiva e' immortale.

    Il caso che conta e' quello per cui la revoca esiste: in quella variabile e' finito l'ID
    di un estraneo, o l'account del proprietario e' stato compromesso, e lui la corregge per
    tagliargli l'accesso. Se l'estraneo ha una sessione aperta, correggere la variabile non
    gli toglie niente finche' qualcuno non rifa' login — e chi ha il pannello aperto non ha
    nessun motivo di rifare login.

    Ora l'invariante e' verificata **anche sul percorso della sessione**: la prima richiesta
    autenticata che arriva dopo il cambio scioglie il collegamento e invalida il cookie. La
    prima richiesta puo' essere proprio quella dell'estranea, che quindi si chiude da se'.
    """
    import sqlite3
    percorso = _prepara(tmp_path, monkeypatch, 'revoca_senza_login.db')
    VECCHIO, NUOVO = '111000111', '222000222'

    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', VECCHIO)
    risposta = main.login_telegram(main.LoginTelegramIn(**_dati_login(id=VECCHIO)))
    cookie = None
    for pezzo in (risposta.headers.get('set-cookie') or '').split(';'):
        chiave, _, valore = pezzo.strip().partition('=')
        if chiave == main.NOME_COOKIE:
            cookie = valore
    assert cookie, 'nessun cookie dal login'

    class Richiesta:
        cookies = {main.NOME_COOKIE: cookie}

    assert main.utente_dalla_sessione(Richiesta()) is not None, (
        'il cookie non era valido nemmeno prima del cambio: il test non misura la revoca')

    # Il proprietario cambia la variabile PER tagliare l'accesso, e NESSUNO rifa' login.
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', NUOVO)

    assert main.utente_dalla_sessione(Richiesta()) is None, (
        'la sessione della vecchia identita- e- ancora valida dopo il cambio: chi ha il '
        'pannello aperto lo conserva, e non scade perche- ogni richiesta rinnova il cookie')

    c = sqlite3.connect(percorso)
    del_proprietario = c.execute('SELECT telegram_id FROM users WHERE origin_profile=?',
                                 (main.PIERO_PROFILE,)).fetchone()[0]
    revoche = c.execute("SELECT COUNT(*) FROM admin_audit"
                        " WHERE action='identita_telegram_revocata'").fetchone()[0]
    c.close()
    assert del_proprietario is None, (
        f'il collegamento stantio sopravvive (telegram_id {del_proprietario!r})')
    assert revoche == 1, f'{revoche} revoche tracciate invece di una'


def test_la_revoca_dalla_SESSIONE_non_si_ripete_a_ogni_richiesta(tmp_path, monkeypatch):
    """Una revoca che si ripete e' una scrittura a ogni richiesta, e un audit illeggibile.

    Il percorso della sessione e' quello di OGNI richiesta autenticata del sito: se la
    condizione restasse vera dopo aver sciolto, ogni richiesta incrementerebbe
    `session_version` e aggiungerebbe una riga in `admin_audit`. Il test tiene la proprieta'
    che rende sicuro metterci una scrittura: dopo lo scioglimento `telegram_id` e' NULL,
    quindi il ramo non scatta piu'.
    """
    import sqlite3
    percorso = _prepara(tmp_path, monkeypatch, 'revoca_una_volta.db')
    VECCHIO, NUOVO = '111000111', '222000222'

    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', VECCHIO)
    risposta = main.login_telegram(main.LoginTelegramIn(**_dati_login(id=VECCHIO)))
    cookie = None
    for pezzo in (risposta.headers.get('set-cookie') or '').split(';'):
        chiave, _, valore = pezzo.strip().partition('=')
        if chiave == main.NOME_COOKIE:
            cookie = valore

    class Richiesta:
        cookies = {main.NOME_COOKIE: cookie}

    def versione_del_proprietario():
        c = sqlite3.connect(percorso)
        valore = c.execute('SELECT session_version FROM users WHERE origin_profile=?',
                           (main.PIERO_PROFILE,)).fetchone()[0]
        c.close()
        return valore

    # Il valore ASSOLUTO non c'entra — `session_version` ha `DEFAULT 1` nello schema, e un
    # test che lo asserisce misura il default invece dell'incremento. Cio' che conta e' che
    # cinque richieste producano UN incremento, non cinque.
    prima = versione_del_proprietario()
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', NUOVO)
    for _ in range(5):
        main.utente_dalla_sessione(Richiesta())
    dopo = versione_del_proprietario()

    c = sqlite3.connect(percorso)
    revoche = c.execute("SELECT COUNT(*) FROM admin_audit"
                        " WHERE action='identita_telegram_revocata'").fetchone()[0]
    c.close()
    assert revoche == 1, (
        f'{revoche} righe di revoca dopo cinque richieste: il ramo scatta a ogni richiesta, '
        'quindi ogni pagina del sito scrive nel database e sporca admin_audit')
    assert dopo == prima + 1, (
        f'session_version e- passata da {prima} a {dopo} in cinque richieste: viene '
        'incrementata in loop, quindi ogni pagina del sito invalida la sessione successiva')


def test_la_revoca_dalla_SESSIONE_e_sicura_in_CORSA(tmp_path, monkeypatch):
    """La revoca sta sul percorso di OGNI richiesta, quindi arriva concorrente per costruzione.

    Il docstring di `revoca_identita_stantia()` afferma che l'`UPDATE` col valore stantio
    nella `WHERE` rende la revoca sicura senza transazione. Quell'affermazione non era
    misurata: togliendo la `WHERE` i due test sequenziali restavano **verdi**, perche' in
    sequenza il controllo precedente basta. Un'affermazione non misurata in un docstring e'
    esattamente cio' che `CLAUDE.md` racconta a proposito del BOM, quindi la misuro.

    Sei richieste insieme sulla stessa sessione: la revoca deve avvenire **una volta sola** —
    un incremento di `session_version` e una riga di audit. Senza la `WHERE`, sei richieste
    che leggono lo stesso valore stantio incrementano fino a sei volte e scrivono fino a sei
    revoche per la stessa revoca.
    """
    import sqlite3
    import threading
    percorso = _prepara(tmp_path, monkeypatch, 'revoca_in_corsa.db')
    VECCHIO, NUOVO = '111000111', '222000222'

    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', VECCHIO)
    risposta = main.login_telegram(main.LoginTelegramIn(**_dati_login(id=VECCHIO)))
    cookie = None
    for pezzo in (risposta.headers.get('set-cookie') or '').split(';'):
        chiave, _, valore = pezzo.strip().partition('=')
        if chiave == main.NOME_COOKIE:
            cookie = valore

    class Richiesta:
        cookies = {main.NOME_COOKIE: cookie}

    c = sqlite3.connect(percorso)
    prima = c.execute('SELECT session_version FROM users WHERE origin_profile=?',
                      (main.PIERO_PROFILE,)).fetchone()[0]
    c.close()

    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', NUOVO)
    esiti = []
    porta = threading.Barrier(6)

    def prova():
        porta.wait()
        try:
            esiti.append(('ok', main.utente_dalla_sessione(Richiesta())))
        except Exception as e:
            esiti.append((type(e).__name__, str(e)))

    fili = [threading.Thread(target=prova) for _ in range(6)]
    for f in fili:
        f.start()
    _attendi_tutti(fili, esiti)

    guasti = [e for e in esiti if e[0] != 'ok']
    assert not guasti, f'richieste concorrenti hanno sollevato: {guasti}'
    assert all(valore is None for _, valore in esiti), (
        f'una richiesta concorrente ha ottenuto una sessione valida dopo la revoca: {esiti}')

    c = sqlite3.connect(percorso)
    dopo = c.execute('SELECT session_version FROM users WHERE origin_profile=?',
                     (main.PIERO_PROFILE,)).fetchone()[0]
    revoche = c.execute("SELECT COUNT(*) FROM admin_audit"
                        " WHERE action='identita_telegram_revocata'").fetchone()[0]
    c.close()
    assert revoche == 1, (
        f'{revoche} revoche per una sola revoca: sei richieste concorrenti hanno letto lo '
        'stesso valore stantio e scritto ognuna la propria')
    assert dopo == prima + 1, (
        f'session_version e- passata da {prima} a {dopo}: incrementata piu- di una volta '
        'dalla stessa revoca')


def test_la_WHERE_anti_corsa_serve_DAVVERO(tmp_path, monkeypatch):
    """L'interleaving che il test a sei thread NON produce, imposto a mano.

    Storia di questo test, perche' e' il punto: il test concorrente qui sopra passava **anche
    togliendo** la `WHERE` col valore stantio. Quindi non misurava la proprieta' che il
    docstring di `revoca_identita_stantia()` afferma — sei thread in Python si serializzano
    abbastanza che il secondo rilegga `telegram_id` gia' NULL, e la corsa non si presenta.
    Un'affermazione non misurata resta un'affermazione, e questo file esiste per non averne.

    Qui l'interleaving e' imposto: la richiesta «lenta» ha letto il valore stantio e, nel
    momento esatto in cui sta per scrivere, un'altra richiesta completa la revoca. Con la
    `WHERE` la lenta aggiorna **zero righe** e non scrive niente; senza, incrementa
    `session_version` una seconda volta e aggiunge una seconda riga di audit per la stessa
    revoca — cioe' butta fuori anche la sessione nata dopo la revoca.
    """
    import sqlite3
    percorso = _prepara(tmp_path, monkeypatch, 'interleaving.db')
    VECCHIO, NUOVO = '111000111', '222000222'
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', VECCHIO)
    main.login_telegram(main.LoginTelegramIn(**_dati_login(id=VECCHIO)))
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', NUOVO)

    c = sqlite3.connect(percorso)
    prima = c.execute('SELECT session_version FROM users WHERE origin_profile=?',
                      (main.PIERO_PROFILE,)).fetchone()[0]
    c.close()

    vera = main.db()
    in_mezzo = []

    class Lenta:
        """La connessione di chi ha letto il valore stantio e scrive un attimo dopo."""

        def execute(self, sql, *args):
            if sql.startswith('UPDATE users SET telegram_id=NULL') and not in_mezzo:
                in_mezzo.append(True)
                altra = sqlite3.connect(percorso)
                assert main.revoca_identita_stantia(altra) is not None, (
                    'la richiesta che arriva in mezzo non ha revocato: il test non produce '
                    'l-interleaving che descrive')
                altra.commit()
                altra.close()
            return vera.execute(sql, *args)

    assert main.revoca_identita_stantia(Lenta()) is None, (
        'la richiesta lenta ha creduto di revocare una revoca GIA- avvenuta')
    vera.commit()
    vera.close()
    assert in_mezzo, 'l-interleaving non e- avvenuto: il test non misura niente'

    c = sqlite3.connect(percorso)
    dopo = c.execute('SELECT session_version FROM users WHERE origin_profile=?',
                     (main.PIERO_PROFILE,)).fetchone()[0]
    revoche = c.execute("SELECT COUNT(*) FROM admin_audit"
                        " WHERE action='identita_telegram_revocata'").fetchone()[0]
    c.close()
    assert revoche == 1, f'{revoche} revoche per una sola revoca'
    assert dopo == prima + 1, (
        f'session_version da {prima} a {dopo}: la richiesta lenta ha incrementato una seconda '
        'volta, quindi butta fuori anche una sessione nata DOPO la revoca')


def test_il_FEED_non_fa_scattare_la_revoca(tmp_path, monkeypatch):
    """La NON-relazione fra sessione e feed, ora che il percorso sessione SCRIVE.

    Chiesto da GPT-5.5 sulla PR #24, e la richiesta e' giusta: mettere una scrittura sul
    percorso di lettura della sessione ha senso solo se quel percorso resta separato dal feed.
    `/xtrader.csv` non ha sessione — XTrader interroga con un token nell'URL — quindi una sua
    richiesta non deve toccare `users`, nemmeno quando l'invariante dell'amministratore e'
    violata: il feed lo interroga un programma, a raffica, e una revoca fatta da li' sarebbe
    una scrittura per ogni interrogazione.

    E' lo stesso principio per cui `_rispondi_con_sessione` e' chiamata per-rotta e non da un
    middleware: un middleware girerebbe anche qui.
    """
    import sqlite3
    percorso = _prepara(tmp_path, monkeypatch, 'feed_e_revoca.db')
    VECCHIO, NUOVO = '111000111', '222000222'
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', VECCHIO)
    main.login_telegram(main.LoginTelegramIn(**_dati_login(id=VECCHIO)))

    # L'invariante e' violata: la variabile e' cambiata e la riga porta ancora la vecchia.
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', NUOVO)
    # `TOKEN_DI_PROVA` e non il letterale: il valore vive in `tests/ambiente.py`, che e' la
    # fonte unica per chiunque debba interrogare una rotta protetta (regola 3). Ricopiarlo qui
    # era una seconda copia dello stesso valore.
    monkeypatch.setattr(main, 'TOKEN', TOKEN_DI_PROVA)

    risposta = main.xtrader_csv(token=TOKEN_DI_PROVA)
    assert risposta.status_code == 200, f'il feed ha risposto {risposta.status_code}'

    c = sqlite3.connect(percorso)
    ancora = c.execute('SELECT telegram_id FROM users WHERE origin_profile=?',
                       (main.PIERO_PROFILE,)).fetchone()[0]
    revoche = c.execute("SELECT COUNT(*) FROM admin_audit"
                        " WHERE action='identita_telegram_revocata'").fetchone()[0]
    c.close()
    assert ancora == VECCHIO, (
        'una richiesta al FEED ha sciolto il collegamento: il percorso del feed ha toccato '
        '`users`, e XTrader lo interroga a raffica')
    assert revoche == 0, f'{revoche} revoche scritte da una richiesta al feed'


@pytest.mark.parametrize('valore', ['', '"222000222"', '  ', '022200022'])
def test_una_variabile_ASSENTE_o_MALFORMATA_non_revoca_dalla_sessione(valore, tmp_path,
                                                                     monkeypatch):
    """Il verso opposto sul percorso nuovo, chiesto da GPT-5.5 sulla PR #24.

    `revoca_identita_stantia()` e' dietro lo stesso controllo di validita' del login, ma non
    era misurato **da questo percorso** — e questo percorso e' quello di ogni richiesta del
    sito, quindi un errore qui scioglierebbe un collegamento buono a ogni pagina aperta,
    all'infinito, per un refuso nel pannello. E' il difetto peggiore fra tutti quelli di
    questa PR, se ci arrivasse da qui.
    """
    import sqlite3
    percorso = _prepara(tmp_path, monkeypatch, f'sessione_valore_{abs(hash(valore))}.db')
    BUONO = '111000111'
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', BUONO)
    risposta = main.login_telegram(main.LoginTelegramIn(**_dati_login(id=BUONO)))
    cookie = None
    for pezzo in (risposta.headers.get('set-cookie') or '').split(';'):
        chiave, _, v = pezzo.strip().partition('=')
        if chiave == main.NOME_COOKIE:
            cookie = v

    class Richiesta:
        cookies = {main.NOME_COOKIE: cookie}

    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', valore)
    assert main.utente_dalla_sessione(Richiesta()) is not None, (
        f'con TELEGRAM_ADMIN_ID={valore!r} la sessione buona e- stata invalidata')

    c = sqlite3.connect(percorso)
    ancora = c.execute('SELECT telegram_id FROM users WHERE origin_profile=?',
                       (main.PIERO_PROFILE,)).fetchone()[0]
    c.close()
    assert ancora == BUONO, (
        f'con TELEGRAM_ADMIN_ID={valore!r} il collegamento buono e- stato sciolto (ora '
        f'{ancora!r}): un refuso nel pannello scioglierebbe a ogni pagina aperta')
