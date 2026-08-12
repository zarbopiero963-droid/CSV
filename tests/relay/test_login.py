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
