"""Il webhook: chi puo' scrivere nel feed che XTrader legge.

Perche' questo file esiste, misurato sul servizio vero prima della patch:

    GET  /xtrader.csv        senza token  ->  401, rifiutato
    POST /telegram/webhook   senza token  ->  200, e la riga entra nel feed

Leggere il feed era protetto, scriverci no. Il filtro dei `chat_id` decide *a
quale* feed appartiene un messaggio — instradamento — e non puo' autenticare,
perche' il `chat_id` arriva nel corpo della richiesta e quindi lo scrive il
mittente. Per sfruttarlo servivano tre cose pubbliche o note: l'URL del servizio,
il testo di riconoscimento del parser (sta in `README.txt`) e il `chat_id` del
canale (lo conosce chi e' nel canale). Il danno non e' una perdita di
informazione: e' una puntata su un segnale che nessuno ha inviato.

Segnalato da Fugu Ultra sulla review finale della PR #12, Issue #13.

La correzione e' il meccanismo di Telegram: `setWebhook` accetta un
`secret_token`, e da quel momento ogni consegna porta l'header
`X-Telegram-Bot-Api-Secret-Token`.

**Il pericolo della correzione e' maggiore del difetto, se scritta male.** Se il
servizio pretendesse l'header senza essere sicuro di averlo comunicato a
Telegram, e la registrazione fallisse — l'handler di avvio ingoia ogni eccezione —
Telegram continuerebbe a consegnare senza header e il relay rifiuterebbe TUTTO:
i segnali si fermerebbero in silenzio, che e' peggio del difetto che si vuole
chiudere. Per questo l'enforcement e' condizionato ad aver **davvero** registrato
il segreto, e `/health` lo dice.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE))

import main  # noqa: E402 - dopo l'inserimento del percorso
from tests.ambiente import CHIAVI_PERICOLOSE, TOKEN_DI_PROVA  # noqa: E402
from tests.servizio import relay_avviato  # noqa: E402

# Il bot finto dei test: la forma e' quella di un token Telegram (cifre, due punti,
# 35 caratteri) perche' il segreto ne viene derivato, ma non e' un bot che esiste.
BOT_FINTO = '000000000:' + 'FINTO-NON-ESISTE-NON-E-UN-BOT-VERO-0'
CHAT = '-1002000000001'

MESSAGGIO_VALIDO = ('P.Bet. PREMACHT 0,5HT\n'
                    '\U0001F19A SQUADRA-A v SQUADRA-B\n'
                    '@ 1.85')


@pytest.fixture(autouse=True)
def _ambiente_pulito(monkeypatch):
    for chiave in CHIAVI_PERICOLOSE:
        monkeypatch.delenv(chiave, raising=False)
    monkeypatch.setattr(main, 'TOKEN', TOKEN_DI_PROVA)


# ------------------------------------------------- il segreto, come funzione

def test_il_segreto_viene_derivato_dal_token_del_bot():
    """Derivato e non configurato a mano, e la scelta ha una ragione precisa.

    Una variabile nuova da impostare sulla dashboard lascerebbe una finestra in
    cui il webhook e- muto o aperto, a seconda di come si tratta il caso «segreto
    assente». Derivandolo dal token del bot il valore **esiste sempre** dove
    esiste il bot, non sta nel repository, e Telegram lo riceve alla
    registrazione senza che nessuno faccia niente.
    """
    segreto = main.webhook_secret(BOT_FINTO)
    assert segreto, 'nessun segreto derivato da un token presente'
    # Forma accettata da Telegram: 1-256 caratteri fra A-Z a-z 0-9 _ -
    assert 1 <= len(segreto) <= 256
    assert all(c.isalnum() or c in '_-' for c in segreto), segreto
    # Deterministico: due avvii dello stesso servizio devono concordare, o
    # Telegram avrebbe un segreto e il relay ne pretenderebbe un altro.
    assert segreto == main.webhook_secret(BOT_FINTO)
    # E diverso per un bot diverso, altrimenti non sarebbe un segreto.
    assert segreto != main.webhook_secret('111111111:' + 'ALTRO-BOT-ALTRO-SEGRETO-0000000000')


def test_il_segreto_non_e_il_token_del_bot():
    """Non deve essere derivabile all'indietro ne- contenere il token.

    Se il segreto fosse il token, o lo contenesse, ogni consegna di Telegram
    porterebbe il token del bot in un header — e da li- nei log di qualunque
    proxy davanti al servizio.
    """
    segreto = main.webhook_secret(BOT_FINTO)
    assert BOT_FINTO not in segreto
    assert BOT_FINTO.split(':', 1)[1] not in segreto
    assert segreto not in BOT_FINTO


def test_senza_token_del_bot_non_c_e_segreto():
    """E- il caso che decide se il rimedio e- peggiore del male.

    Senza bot non esiste webhook, quindi non c'e' niente da proteggere: qui il
    segreto e- vuoto e l'enforcement non si attiva. Se invece si pretendesse
    l'header comunque, un'istanza senza `TELEGRAM_BOT_TOKEN` — cioe- ogni test e
    ogni sviluppo locale — non potrebbe piu- esercitare il webhook.
    """
    assert main.webhook_secret('') == ''
    assert main.webhook_secret(None) == ''


# --------------------------------------------- il rifiuto, sul servizio vero

def _consegna(base, testo=MESSAGGIO_VALIDO, segreto=None, chat=CHAT):
    """Simula una consegna di Telegram; restituisce (stato, corpo)."""
    payload = {'message': {'chat': {'id': int(chat)}, 'text': testo}}
    intestazioni = {'Content-Type': 'application/json'}
    if segreto is not None:
        intestazioni['X-Telegram-Bot-Api-Secret-Token'] = segreto
    req = urllib.request.Request(f'{base}/telegram/webhook',
                                 data=json.dumps(payload).encode('utf-8'),
                                 headers=intestazioni, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 - loopback
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _feed(base):
    req = urllib.request.Request(f'{base}/xtrader.csv?token={TOKEN_DI_PROVA}')
    with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 - loopback
        return r.read()


@pytest.fixture(scope='module')
def servizio_con_bot(tmp_path_factory):
    """Il relay come in produzione: il bot c'e-, quindi il segreto esiste.

    `PUBLIC_URL` punta a un host inesistente di proposito: la registrazione del
    webhook fallisce, e serve che fallisca — cosi- questi test verificano anche
    che un fallimento di rete non blocchi l'avvio. Il segreto e- comunque
    derivabile, che e- la condizione che governa l'enforcement.
    """
    with relay_avviato(tmp_path_factory.mktemp('webhook'),
                       CSV_ACCESS_TOKEN=TOKEN_DI_PROVA,
                       TELEGRAM_BOT_TOKEN=BOT_FINTO,
                       TELEGRAM_ALLOWED_CHAT_IDS=CHAT,
                       PUBLIC_URL='https://non-esiste.invalid') as base:
        yield base


def test_una_consegna_SENZA_header_viene_rifiutata_e_non_scrive(servizio_con_bot):
    """Il test fail-first: prima della patch questa richiesta dava 200 e scriveva.

    Si verificano due cose, e la seconda e- quella che conta: non basta il codice
    di stato, il feed non deve essere stato toccato. Un rifiuto che rifiuta e
    scrive comunque non sarebbe un rifiuto.
    """
    prima = _feed(servizio_con_bot)
    stato, corpo = _consegna(servizio_con_bot, segreto=None)
    assert stato == 403, (
        f'una consegna senza header ha risposto {stato}: il webhook accetta ancora '
        f'scritture da chiunque. Corpo: {corpo[:200]!r}'
    )
    assert _feed(servizio_con_bot) == prima, (
        'il feed e- cambiato dopo una consegna rifiutata: la riga e- stata scritta '
        'prima del controllo'
    )
    assert b'SQUADRA-A' not in _feed(servizio_con_bot)


def test_una_consegna_col_segreto_SBAGLIATO_viene_rifiutata(servizio_con_bot):
    prima = _feed(servizio_con_bot)
    stato, _ = _consegna(servizio_con_bot, segreto='non-e-il-segreto-giusto')
    assert stato == 403
    assert _feed(servizio_con_bot) == prima


def test_una_consegna_col_segreto_GIUSTO_funziona_come_prima(servizio_con_bot):
    """L'altra faccia: la protezione non deve chiudere il percorso legittimo.

    Senza questo, tutto quanto sopra passerebbe anche con un webhook che rifiuta
    SEMPRE — e un relay che non riceve piu- segnali e- rotto, non sicuro. E- la
    stessa coppia di test che protegge `auth()`.
    """
    segreto = main.webhook_secret(BOT_FINTO)
    stato, corpo = _consegna(servizio_con_bot, segreto=segreto)
    assert stato == 200, f'la consegna legittima e- stata rifiutata: {corpo[:200]!r}'
    dati = json.loads(corpo)
    assert dati.get('profile') == 'PIERO', dati
    assert dati.get('event') == 'SQUADRA-A - SQUADRA-B', dati
    assert 'SQUADRA-A - SQUADRA-B'.encode('utf-8') in _feed(servizio_con_bot)


def test_il_rifiuto_non_rivela_il_segreto(servizio_con_bot):
    """Il messaggio d'errore non deve insegnare niente a chi lo riceve."""
    segreto = main.webhook_secret(BOT_FINTO)
    _, corpo = _consegna(servizio_con_bot, segreto='tentativo')
    assert segreto.encode() not in corpo
    assert BOT_FINTO.encode() not in corpo
    assert b'tentativo' not in corpo


def test_il_confronto_del_segreto_e_a_tempo_costante():
    """Guardia strutturale, come per `auth()`: non c'e- comportamento da osservare.

    Il segreto viaggia su ogni consegna di Telegram, quindi e- il valore piu-
    frequentemente confrontato dell'intero servizio.
    """
    import inspect
    sorgente = inspect.getsource(main.telegram_webhook)
    assert 'compare_digest' in sorgente, (
        'il confronto del segreto del webhook non usa secrets.compare_digest'
    )


# ------------------------- il caso che rende il rimedio peggiore del male

@pytest.fixture(scope='module')
def servizio_senza_bot(tmp_path_factory):
    """Nessun `TELEGRAM_BOT_TOKEN`: nessun bot, nessun segreto, nessun enforcement.

    E- lo stato di ogni sviluppo locale e di ogni test che non passa da qui.
    """
    with relay_avviato(tmp_path_factory.mktemp('webhook-senza'),
                       CSV_ACCESS_TOKEN=TOKEN_DI_PROVA,
                       TELEGRAM_ALLOWED_CHAT_IDS=CHAT) as base:
        yield base


def test_senza_bot_il_webhook_resta_utilizzabile(servizio_senza_bot):
    """Senza bot non c'e- niente da proteggere, e chiudere sarebbe un danno netto.

    Un'istanza senza `TELEGRAM_BOT_TOKEN` non ha un webhook registrato: nessuna
    consegna vera potra- mai arrivarle. Pretendere l'header renderebbe solo
    impossibile provare il relay in locale, senza chiudere alcuna superficie.
    """
    stato, corpo = _consegna(servizio_senza_bot, segreto=None)
    assert stato == 200, f'{stato}: {corpo[:200]!r}'
    assert b'SQUADRA-A' in _feed(servizio_senza_bot)


def test_health_dichiara_lo_stato_del_webhook(servizio_con_bot, servizio_senza_bot):
    """Come per `auth`: un controllo che nessuno legge non e- un controllo.

    Senza questa riga, un'istanza in cui l'enforcement non e- attivo — perche- il
    bot non c'e- — sarebbe indistinguibile da una protetta, e la differenza e- se
    chiunque puo- scrivere nel feed.
    """
    for base, atteso in ((servizio_con_bot, 'protetto'), (servizio_senza_bot, 'nessun bot')):
        with urllib.request.urlopen(f'{base}/health', timeout=10) as r:  # noqa: S310
            dati = json.loads(r.read())
        assert dati.get('webhook') == atteso, f'{base}: {dati}'


def test_health_non_contiene_il_segreto(servizio_con_bot):
    """`/health` e- senza autenticazione: dice lo stato, mai il valore."""
    with urllib.request.urlopen(f'{servizio_con_bot}/health', timeout=10) as r:  # noqa: S310
        corpo = r.read()
    assert main.webhook_secret(BOT_FINTO).encode() not in corpo
    assert BOT_FINTO.encode() not in corpo
