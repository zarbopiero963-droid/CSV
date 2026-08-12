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
import sys
import time
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE))

import main  # noqa: E402 - dopo l'inserimento del percorso
from tests.ambiente import CHIAVI_PERICOLOSE  # noqa: E402

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
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def _cookie_dalla_risposta(intestazioni):
    """Il valore del cookie di sessione dall'header `Set-Cookie`."""
    grezzo = intestazioni.get('set-cookie') or intestazioni.get('Set-Cookie') or ''
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


def test_una_riga_CREATA_FRA_il_SELECT_e_l_INSERT_non_diventa_un_500(tmp_path, monkeypatch):
    """`SELECT` poi `INSERT` su una colonna UNIQUE: fra i due c'e' spazio per un altro.

    Segnalato da Claude Fable 5 sulla PR #23, e la finestra e' reale: `users.telegram_id`
    e' **UNIQUE** — misurato in `SCHEMA_MULTIUTENTE` — quindi due login simultanei di un
    utente **nuovo** non producono una riga doppia, producono un `IntegrityError`, cioe'
    un **500** a chi perde la corsa. Al primo accesso di un cliente, che e' l'unico
    momento in cui puo' accadere. Il caso non e' esotico: il Login Widget in una pagina
    ricaricata, o due schede aperte, danno due POST ravvicinate.

    **Come e' misurata, perche' la strada ovvia non funziona.** Prima ho provato con
    richieste HTTP davvero concorrenti (6 e 12 thread contro il servizio in
    sottoprocesso, allineati da una barriera): **30 richieste, tutte 200, zero
    collisioni**. La finestra c'e' ma non si apre da fuori — l'event loop di uvicorn
    distanzia gli handler piu' di quanto duri il lavoro su SQLite, che e' sotto il
    millisecondo. Un test a thread sarebbe rimasto verde prima e dopo la correzione,
    cioe' decorativo: e' esattamente cio' che `CLAUDE.md` vieta.

    Quindi la corsa la riproduco nel punto in cui esiste: una connessione avvolta che,
    **subito dopo** il `SELECT` su `telegram_id`, inserisce la riga concorrente. Il
    `fetchone()` restituisce `None` — al momento dell'`execute` la riga non c'era — e il
    codice entra nel ramo che inserisce trovandosi la chiave gia' occupata. E' la
    finestra, non una sua imitazione.
    """
    import sqlite3

    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'corsa.db'))
    monkeypatch.setattr(main, 'BOT_TOKEN', BOT_FINTO)
    monkeypatch.setattr(main, 'SEGRETO_SESSIONE', SEGRETO_ATTESO)
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', '')  # un CLIENTE, non il proprietario

    vera = main.db
    intruso = {'fatto': False}

    class Avvolta:
        """Una connessione normale, tranne che apre la finestra una volta sola."""

        def __init__(self, c):
            self._c = c

        def execute(self, sql, *resto):
            esito = self._c.execute(sql, *resto)
            if not intruso['fatto'] and 'FROM users WHERE telegram_id' in sql:
                intruso['fatto'] = True
                altra = vera()
                altra.execute('INSERT INTO users(telegram_id, status)'
                              " VALUES (?, 'registrato')", ('555000222',))
                altra.commit()
                altra.close()
            return esito

        def __getattr__(self, nome):
            return getattr(self._c, nome)

    monkeypatch.setattr(main, 'db', lambda: Avvolta(vera()))

    dati = _dati_login(id='555000222', first_name='Cliente')
    try:
        risposta = main.login_telegram(main.LoginTelegramIn(**dati))
    except sqlite3.IntegrityError as e:
        raise AssertionError(
            f'login concorrente -> IntegrityError ({e}), che via HTTP e- un 500: il '
            'SELECT-poi-INSERT su telegram_id UNIQUE non regge la corsa. Serve un '
            'inserimento idempotente piu- una rilettura'
        ) from None

    assert risposta.status_code == 200, risposta.status_code

    # E la riga resta UNA: il perdente si attacca a quella del vincitore, non ne crea
    # un'altra ne- sovrascrive la sua.
    c = sqlite3.connect(tmp_path / 'corsa.db')
    righe = c.execute('SELECT COUNT(*) FROM users WHERE telegram_id=?',
                      ('555000222',)).fetchone()[0]
    c.close()
    assert righe == 1, f'{righe} righe per lo stesso telegram_id invece di una'
    assert intruso['fatto'], 'la finestra non e- stata aperta: il test non misura niente'


# --------------------------------------------- `compare_digest` e le stringhe non ASCII
#
# Tre siti, tutti su input che arriva da fuori. Segnalati da Claude Fable 5 (uno) e da
# CodeRabbit (tutti tre) sulla PR #23.
#
# La cosa che rende questa classe grave non e' il difetto, e' che era GIA' SCRITTA in
# questo file. Il docstring di `auth()`, da luglio, dice: «passando le STRINGHE,
# compare_digest solleverebbe TypeError su una non ASCII, e un token con un accento
# diventerebbe un 500 invece di un 401 — un modo per far scrivere una traccia nei log con
# un solo parametro di query». Poi ho scritto tre confronti nuovi, tutti su stringhe, due
# dei quali su valori che li scrive l'attaccante. La lezione era imparata e documentata, e
# non ha impedito niente: e' esattamente il caso della regola 2 — trovato il sito, non
# cercata la classe.

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
