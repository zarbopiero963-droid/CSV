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

**Il pericolo della correzione e' maggiore del difetto, se scritta male.** Se
`setWebhook` fallisce e Telegram conserva una registrazione vecchia SENZA segreto,
continua a consegnare senza header e il relay rifiuta TUTTO: i segnali si fermano
in silenzio, che e' peggio del difetto che si vuole chiudere.

Come e' risolto — e qui la prima versione di questo file diceva una cosa che il
codice non faceva, segnalato insieme da GPT-5.5, Claude Fable 5 e CodeRabbit:

- l'enforcement **non** e' condizionato all'esito della registrazione. Legarlo a
  quello riaprirebbe la scrittura non autenticata ogni volta che la rete fa i
  capricci, cioe- il difetto originale, in silenzio;
- e' condizionato alla presenza del bot: `SEGRETO_WEBHOOK` derivabile;
- il blackout si evita RITENTANDO. All'avvio tre volte, e poi da ogni consegna
  rifiutata. Una consegna rifiutata dimostra **solo** che la validazione
  dell'header e- fallita: non che venga da Telegram, e non che Telegram non
  conosca il segreto — puo- essere un POST forgiato (segnalato da CodeRabbit).
  Sono due ipotesi e il ritentativo le copre entrambe senza distinguerle: se la
  registrazione era stantia la rimette a posto e il segnale arriva col giro dopo,
  perche- Telegram ritenta le consegne; se era forgiata costa un tentativo, che il
  freno limita a uno al minuto. In entrambi i casi si rifiuta.

`/health` riporta l'esito della registrazione perche- resti diagnosticabile, non
perche- governi l'enforcement.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
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
    """Nessun bot, nessun segreto — e nessun segreto significa RIFIUTARE tutto.

    Il valore vuoto non spegne l'enforcement: lo rende totale. Senza il token non
    c'e- modo di validare nessuna consegna, quindi questa istanza non ne accetta
    nessuna — non perche- non possano arrivarne (Telegram puo- consegnare
    attraverso una registrazione fatta da un deploy precedente), ma perche-
    un'istanza che non sa riconoscerle non guadagna niente ad accettarle. Il
    comportamento sul servizio e- verificato da
    `test_senza_bot_ogni_consegna_e_RIFIUTATA`.
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


def test_senza_bot_ogni_consegna_e_RIFIUTATA(servizio_senza_bot):
    """Senza bot non si sa validare nessuna consegna, quindi si rifiuta tutto.

    La prima versione di questa patch ACCETTAVA in questo caso, col ragionamento
    che senza bot non c'e- superficie da chiudere. Era sbagliato, e il controesempio
    e- preciso: `TELEGRAM_ALLOWED_CHAT_IDS` popola il profilo PIERO
    **indipendentemente** dal token del bot, quindi un'istanza senza bot ma coi
    chat_id configurati restava iniettabile da chiunque — il difetto riaperto in un
    ramo. Segnalato da CodeRabbit.

    Nessuna variabile di override per lo sviluppo locale: sarebbe una scorciatoia
    che un domani finisce impostata in produzione. Chi prova in locale imposta un
    token finto, come fa la fixture qui sopra.
    """
    prima = _feed(servizio_senza_bot)
    stato, corpo = _consegna(servizio_senza_bot, segreto=None)
    assert stato == 403, f'{stato}: {corpo[:200]!r}'
    assert _feed(servizio_senza_bot) == prima
    assert b'SQUADRA-A' not in _feed(servizio_senza_bot)


def test_health_dichiara_lo_stato_del_webhook(servizio_con_bot, servizio_senza_bot):
    """Come per `auth`: un controllo che nessuno legge non e- un controllo.

    Senza questa riga, un'istanza in cui l'enforcement non e- attivo — perche- il
    bot non c'e- — sarebbe indistinguibile da una protetta, e la differenza e- se
    chiunque puo- scrivere nel feed.
    """
    for base, atteso in ((servizio_con_bot, 'protetto'), (servizio_senza_bot, 'chiuso senza bot')):
        with urllib.request.urlopen(f'{base}/health', timeout=10) as r:  # noqa: S310
            dati = json.loads(r.read())
        assert dati.get('webhook') == atteso, f'{base}: {dati}'


def test_health_non_contiene_il_segreto(servizio_con_bot):
    """`/health` e- senza autenticazione: dice lo stato, mai il valore."""
    with urllib.request.urlopen(f'{servizio_con_bot}/health', timeout=10) as r:  # noqa: S310
        corpo = r.read()
    assert main.webhook_secret(BOT_FINTO).encode() not in corpo
    assert BOT_FINTO.encode() not in corpo


# ------------------------- la registrazione: cosa la fa risultare riuscita

def test_telegram_dice_200_ma_ok_false_NON_e_una_registrazione(monkeypatch):
    """Il codice HTTP non basta: parte dei rifiuti arriva con `200` e `ok: false`.

    Solo parte, ed e- la precisazione di CodeRabbit: un token inesistente da- `404`
    e un `secret_token` con caratteri non ammessi da- `400`, e quelli non arrivano
    come corpo ma come eccezione. Servono **entrambe** le condizioni — risposta
    ricevuta e `ok` vero — e questo test esercita tutte e tre le forme di rifiuto:
    `200` con `ok: false`, corpo non interpretabile, ed errore HTTP.

    Fidandosi del solo codice HTTP il flag direbbe «registrato» in un caso in cui
    non lo e-, cioe- mentirebbe nella direzione pericolosa — e il flag esiste per
    essere creduto. Segnalato da Sourcery.
    """
    class RispostaFinta:
        def __init__(self, corpo):
            self._corpo = corpo.encode('utf-8')
        def read(self):
            return self._corpo
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def risposta(corpo):
        return lambda *a, **k: RispostaFinta(corpo)

    def errore_http(codice, motivo):
        def solleva(*a, **k):
            raise urllib.error.HTTPError(
                'https://api.telegram.org/botFINTO/setWebhook', codice, motivo,
                {}, None)
        return solleva

    for descrizione, finta, atteso in (
        ('ok true', risposta('{"ok": true, "result": true}'), True),
        ('200 con ok false',
         risposta('{"ok": false, "description": "Bad Request: bad webhook: invalid URL"}'),
         False),
        ('200 con ok false e error_code',
         risposta('{"ok": false, "error_code": 401, "description": "Unauthorized"}'),
         False),
        ('corpo non interpretabile', risposta('non e- json'), False),
        # I due rifiuti che NON sono 200: token inesistente e segreto con
        # caratteri non ammessi. Arrivano come eccezione, non come corpo.
        ('404 token inesistente', errore_http(404, 'Not Found'), False),
        ('400 secret_token non valido', errore_http(400, 'Bad Request'), False),
    ):
        monkeypatch.setattr(urllib.request, 'urlopen', finta)
        esito = main._chiama_set_webhook(BOT_FINTO, 'https://esempio.invalid')
        assert esito is atteso, f'{descrizione} -> {esito}, atteso {atteso}'


def test_l_avvio_RITENTA_la_registrazione(monkeypatch):
    """Tre tentativi, perche- il caso piu- probabile e- un errore di rete transitorio.

    Senza ritentativi un blip mentre il container si avvia lascerebbe l'istanza con
    l'enforcement attivo e Telegram che non conosce il segreto — lo stato che il
    ritentativo da richiesta poi ripara, ma tardi.
    """
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', BOT_FINTO)
    monkeypatch.setattr(main, '_WEBHOOK_REGISTRATO', None)
    tentativi = []

    def finta(bot, url):
        tentativi.append(bot)
        return len(tentativi) >= 3   # i primi due falliscono

    monkeypatch.setattr(main, '_chiama_set_webhook', finta)
    monkeypatch.setattr(main.asyncio, 'sleep', _senza_attesa)
    import asyncio as _a
    _a.run(main.register_telegram_webhook())
    assert len(tentativi) == 3, f'tentativi: {len(tentativi)}'
    assert main._WEBHOOK_REGISTRATO is True


async def _senza_attesa(_secondi):
    """Sostituisce `asyncio.sleep` nei test: l'attesa vera renderebbe la suite lenta."""
    return None


def test_l_avvio_non_BLOCCA_il_loop_mentre_chiama_telegram():
    """La chiamata di rete dell'avvio non deve girare sull'event loop.

    `setWebhook` ha un timeout di dieci secondi e l'avvio la ritenta tre volte con
    pause in mezzo: eseguita sul loop, un container che parte con la rete lenta
    tiene il loop fermo fino a mezzo minuto. Su Railway l'healthcheck interroga
    `/health` in quella finestra, non riceve risposta, e il deploy risulta guasto
    per un webhook che sta soltanto ritentando. Segnalato da Claude Fable 5 come
    bloccante sulla review finale della PR #14 — verificato: la chiamata era sul
    loop, `main.py` la faceva diretta invece che in un thread come fa l'handler.

    Il test non guarda il tempo — sarebbe una misura fragile — ma **su quale thread
    gira la chiamata**: se e- il thread principale, e- il thread dell'event loop, e
    per i dieci secondi del timeout il servizio non risponde a nessuno. E- questa
    l'asserzione che decide l'esito, e sul codice difettoso e- quella che scatta.

    La finta si fa comunque sbloccare da un'altra coroutine, e `wait_for` sta a
    guardia: se un domani il blocco durasse oltre l'attesa della finta, il test
    fallirebbe invece di appendere la suite. Sono due reti per la stessa
    proprieta', non due asserzioni diverse.
    """
    import os as _os
    sbloccami = threading.Event()
    thread_della_finta = []

    def finta(bot, url):
        thread_della_finta.append(threading.current_thread())
        sbloccami.wait(10)
        return True

    async def prova():
        vecchio_chiama = main._chiama_set_webhook
        vecchio_token = _os.environ.get('TELEGRAM_BOT_TOKEN')
        main._chiama_set_webhook = finta
        _os.environ['TELEGRAM_BOT_TOKEN'] = BOT_FINTO
        try:
            avvio = asyncio.create_task(main.register_telegram_webhook())

            async def sblocca():
                # Gira solo se il loop e- libero: e- l'intera asserzione.
                sbloccami.set()

            await asyncio.wait_for(asyncio.gather(avvio, sblocca()), timeout=15)
        finally:
            main._chiama_set_webhook = vecchio_chiama
            if vecchio_token is None:
                _os.environ.pop('TELEGRAM_BOT_TOKEN', None)
            else:
                _os.environ['TELEGRAM_BOT_TOKEN'] = vecchio_token

    try:
        asyncio.run(prova())
    except (TimeoutError, asyncio.TimeoutError):
        sbloccami.set()
        raise AssertionError(
            'l-avvio ha bloccato l-event loop durante la chiamata a Telegram: '
            'nessun-altra coroutine ha potuto girare. Su Railway questo e- '
            'l-healthcheck che non riceve risposta mentre il webhook ritenta.'
        ) from None

    assert thread_della_finta, 'la finta non e- stata chiamata'
    assert thread_della_finta[0] is not threading.main_thread(), (
        f'la chiamata di rete e- girata su {thread_della_finta[0].name}, cioe- sul '
        f'thread dell-event loop'
    )


def test_una_consegna_rifiutata_RITENTA_la_registrazione_ma_col_freno(monkeypatch):
    """L'autoriparazione, e il suo freno.

    Una consegna senza header con l'enforcement attivo e- la prova che Telegram non
    conosce il segreto: si rifiuta e si ritenta la registrazione, cosi- il segnale
    arriva col giro dopo invece di non arrivare mai.

    Il freno esiste perche- quel percorso lo raggiunge CHIUNQUE: senza, una raffica
    di POST forgiati diventerebbe una raffica di chiamate verso api.telegram.org
    fatte da noi, cioe- un amplificatore offerto gratis.
    """
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', BOT_FINTO)
    monkeypatch.setattr(main, '_WEBHOOK_REGISTRATO', False)
    monkeypatch.setattr(main, '_ULTIMO_TENTATIVO', 0.0)
    tentativi = []
    monkeypatch.setattr(main, '_chiama_set_webhook',
                        lambda bot, url: tentativi.append(bot) or False)

    assert main.assicura_registrazione() is False
    assert len(tentativi) == 1, 'il primo tentativo deve partire'
    # Subito dopo, il freno tiene.
    for _ in range(5):
        main.assicura_registrazione()
    assert len(tentativi) == 1, f'il freno non ha tenuto: {len(tentativi)} tentativi'

    # Passato l'intervallo, si ritenta.
    monkeypatch.setattr(main, '_ULTIMO_TENTATIVO',
                        main.time.monotonic() - main.ATTESA_FRA_TENTATIVI_S - 1)
    main.assicura_registrazione()
    assert len(tentativi) == 2, 'scaduto il freno il ritentativo deve ripartire'


def test_una_raffica_di_consegne_rifiutate_produce_UN_tentativo(monkeypatch):
    """Non si richiama Telegram a ogni consegna — ma il limite e- il FRENO.

    Questo test asseriva prima che con `_WEBHOOK_REGISTRATO = True` non partisse
    **nessun** tentativo, perche- `assicura_registrazione` usciva subito sul flag.
    Era la formulazione sbagliata dell'invariante giusta: quello che serve e- che
    una raffica non diventi una raffica di chiamate verso Telegram, e a questo
    basta il freno. Uscire sul flag faceva di piu-, e quel di piu- era il difetto
    corretto in `test_una_registrazione_riuscita_NON_rende_morta_l_autoriparazione`.
    """
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', BOT_FINTO)
    monkeypatch.setattr(main, '_WEBHOOK_REGISTRATO', True)
    monkeypatch.setattr(main, '_ULTIMO_TENTATIVO', main.time.monotonic())
    tentativi = []
    monkeypatch.setattr(main, '_chiama_set_webhook',
                        lambda bot, url: tentativi.append(bot) or True)
    for _ in range(10):
        assert main.assicura_registrazione() is True
    assert tentativi == [], (
        f'col freno appena armato ha richiamato Telegram {len(tentativi)} volte'
    )


def test_una_registrazione_riuscita_NON_rende_morta_l_autoriparazione(monkeypatch):
    """Il bloccante: dopo un successo, il ritentativo non partiva **mai piu-**.

    `assicura_registrazione` usciva subito quando `_WEBHOOK_REGISTRATO` era `True`,
    con il ragionamento «Telegram sa il segreto, non c'e- niente da riparare». Ma
    una consegna RIFIUTATA che arriva mentre il flag dice `True` e- precisamente la
    prova che il flag non e- piu- vero: e- l'unica informazione che contraddice il
    valore in cache, e veniva buttata.

    Lo scenario e- concreto: qualcuno chiama `setWebhook` sullo stesso bot senza
    segreto — un altro strumento, un deploy vecchio, il Bridge — e da quel momento
    Telegram consegna senza header. Il relay rifiuta ogni consegna legittima,
    l'autoriparazione non parte perche- il flag dice `True`, e `/health` continua a
    dire `webhook_registrato: true`. Segnali fermi, e nessun posto dove vederlo.
    Cioe- il blackout che questa PR esiste per evitare, sopravvissuto nell'unico
    caso in cui il rimedio si spegneva. Segnalato da Fugu Ultra come bloccante
    sulla review finale.

    Il limite giusto e- il freno, non il flag: una raffica costa un tentativo al
    minuto (`test_una_raffica_di_consegne_rifiutate_produce_UN_tentativo`).
    """
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', BOT_FINTO)
    monkeypatch.setattr(main, '_WEBHOOK_REGISTRATO', True)
    # Freno scaduto: un tentativo DEVE poter partire.
    monkeypatch.setattr(main, '_ULTIMO_TENTATIVO',
                        main.time.monotonic() - main.ATTESA_FRA_TENTATIVI_S - 1)
    tentativi = []
    monkeypatch.setattr(main, '_chiama_set_webhook',
                        lambda bot, url: tentativi.append(bot) or True)

    main.assicura_registrazione()
    assert len(tentativi) == 1, (
        'con il flag a True e il freno scaduto, una consegna rifiutata non ha '
        'ritentato la registrazione: l-autoriparazione e- morta proprio dopo un '
        'successo, cioe- nel caso in cui una registrazione puo- diventare stantia'
    )


def test_il_segreto_non_finisce_nell_URL_ma_nel_CORPO(monkeypatch):
    """Dove viaggia il segreto quando lo mandiamo a Telegram.

    Un URL non e- un posto riservato: finisce nei log di ogni intermediario che
    lo tocca, e `setWebhook` chiamato con `?secret_token=...` scriverebbe il
    segreto in quei log a ogni registrazione — cioe- a ogni deploy e a ogni
    autoriparazione. Il corpo di un POST no. Segnalato da GPT-5.5 e Fable 5.

    Il token del BOT resta nel percorso perche- l'API di Telegram lo mette
    li- e non c'e- modo di spostarlo; il segreto invece si sposta, e questo test
    verifica che sia stato spostato davvero.
    """
    visti = []

    class RispostaFinta:
        def read(self):
            return b'{"ok": true, "result": true}'
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def finta_urlopen(richiesta, *a, **k):
        visti.append(richiesta)
        return RispostaFinta()

    monkeypatch.setattr(urllib.request, 'urlopen', finta_urlopen)
    assert main._chiama_set_webhook(BOT_FINTO, 'https://esempio.invalid') is True

    assert len(visti) == 1
    richiesta = visti[0]
    segreto = main.webhook_secret(BOT_FINTO)
    assert not isinstance(richiesta, str), (
        'urlopen e- stata chiamata con una stringa: il segreto puo- stare solo '
        'nell-URL'
    )
    assert segreto not in richiesta.full_url, (
        f'il segreto del webhook e- nell-URL della richiesta: {richiesta.full_url}'
    )
    assert richiesta.get_method() == 'POST'
    corpo = richiesta.data or b''
    assert segreto.encode('utf-8') in corpo, (
        'il segreto non e- nel corpo: se non e- ne- qui ne- nell-URL, Telegram non '
        'lo riceve affatto e ogni consegna verrebbe rifiutata'
    )
    # Il corpo deve essere DICHIARATO per come e- codificato, o Telegram non lo
    # interpreta e il segreto non arriva: la registrazione fallirebbe con la stessa
    # faccia di un problema di rete. Senza questa riga il test passerebbe anche
    # spedendo JSON senza intestazione. Segnalato da GPT-5.5.
    #
    # `urllib` metterebbe comunque questo valore da se- per un `data=` di byte, ma
    # lo fa dentro il proprio handler al momento dell-invio: non e- osservabile qui,
    # e quello che non e- osservabile non e- vincolato. Per questo `main.py` lo
    # dichiara esplicitamente — e- l-unico modo di avere questa asserzione.
    assert richiesta.get_header('Content-type') == 'application/x-www-form-urlencoded', (
        f'Content-Type: {richiesta.get_header("Content-type")!r}'
    )
    assert urllib.parse.parse_qs(corpo.decode('utf-8')) == {
        'url': ['https://esempio.invalid/telegram/webhook'],
        'secret_token': [segreto],
    }
    # E l-URL deve comunque contenere quello che serve per arrivare a destinazione.
    assert richiesta.full_url.endswith('/setWebhook')


# ------------------- l'autoriparazione, chiamata dall'handler vero

class RichiestaFinta:
    """Il minimo che `telegram_webhook` legge di una `Request`.

    Serve per esercitare l'handler in processo: i test sul servizio vero
    verificano lo stato HTTP, ma non possono osservare se l'autoriparazione e-
    stata chiamata, perche- avviene dentro l'altro processo.
    """

    def __init__(self, intestazioni=None, corpo=None):
        self.headers = dict(intestazioni or {})
        self._corpo = {} if corpo is None else corpo

    async def json(self):
        return self._corpo


def _handler(richiesta):
    """Esegue l'handler e restituisce (stato, spie): 200 oppure il codice HTTP."""
    try:
        risultato = asyncio.run(main.telegram_webhook(richiesta))
    except main.HTTPException as e:
        return e.status_code, None
    return 200, risultato


def test_una_consegna_rifiutata_CHIAMA_l_autoriparazione_dall_handler(monkeypatch):
    """Il pezzo che nessun test copriva: la chiamata dentro l'handler.

    Che `assicura_registrazione()` funzioni e- verificato altrove; che l'handler
    la CHIAMI davvero su una consegna rifiutata non lo era. Misurato: sostituendo
    quella riga con `pass` la suite restava tutta verde, cioe- il rimedio al
    blackout poteva essere cancellato per sbaglio senza che niente diventasse
    rosso. Segnalato da GPT-5.5 come test mancante sulla PR #14.

    Le due condizioni vanno insieme: 403 **e** ritentativo. Solo il 403 sarebbe
    il fail-closed senza la sua via d'uscita; solo il ritentativo sarebbe una
    scrittura accettata.
    """
    segreto = main.webhook_secret(BOT_FINTO)
    monkeypatch.setattr(main, 'SEGRETO_WEBHOOK', segreto)
    chiamate = []
    monkeypatch.setattr(main, 'assicura_registrazione',
                        lambda *a, **k: chiamate.append(True))

    # Senza header, e con l'header sbagliato: entrambe sono la prova che Telegram
    # non conosce il segreto, entrambe devono ritentare.
    for intestazioni in ({}, {'X-Telegram-Bot-Api-Secret-Token': 'non-e-quello'}):
        chiamate.clear()
        stato, _ = _handler(RichiestaFinta(intestazioni))
        assert stato == 403, f'{intestazioni} -> {stato}'
        assert len(chiamate) == 1, (
            f'{intestazioni}: l-handler non ha ritentato la registrazione '
            f'({len(chiamate)} chiamate). Una consegna rifiutata e- l-unico '
            f'segnale che abbiamo che Telegram non conosce il segreto: senza '
            f'questo ritentativo il blackout dura fino al prossimo deploy.'
        )

    # E la consegna legittima non ritenta niente: chiamare Telegram a ogni
    # segnale valido sarebbe una chiamata di rete per messaggio.
    chiamate.clear()
    stato, risultato = _handler(
        RichiestaFinta({'X-Telegram-Bot-Api-Secret-Token': segreto}, corpo={}))
    assert stato == 200, stato
    assert risultato == {'ok': True, 'ignored': 'no_text'}, risultato
    assert chiamate == [], 'una consegna accettata ha richiamato Telegram'


def test_senza_bot_il_rifiuto_NON_chiama_telegram(monkeypatch):
    """Senza bot non c-e- niente da riparare, e ritentare sarebbe un amplificatore.

    `assicura_registrazione()` esce da se- senza token, ma il rifiuto per
    «nessun bot» deve fermarsi prima di arrivarci: e- il ramo raggiungibile da
    chiunque su un-istanza mal configurata, e non deve nemmeno provare a
    entrare in un thread per ogni POST forgiato.
    """
    monkeypatch.setattr(main, 'SEGRETO_WEBHOOK', '')
    chiamate = []
    monkeypatch.setattr(main, 'assicura_registrazione',
                        lambda *a, **k: chiamate.append(True))
    stato, _ = _handler(RichiestaFinta({'X-Telegram-Bot-Api-Secret-Token': 'qualsiasi'}))
    assert stato == 403, stato
    assert chiamate == [], 'ha tentato una registrazione senza avere un bot'


def test_un_tentativo_lento_e_fallito_non_sovrascrive_un_esito_piu_recente(monkeypatch):
    """Due registrazioni sovrapposte: vince la piu- recente, non l-ultima a finire.

    La chiamata di rete avviene FUORI dal lock — deve, o una `setWebhook` lenta
    bloccherebbe ogni consegna — quindi l-ordine in cui i tentativi finiscono non
    e- l-ordine in cui sono partiti. Un tentativo partito prima, andato in timeout
    dopo dieci secondi e fallito, scriveva `False` sopra il `True` di un tentativo
    partito dopo e riuscito: `/health` avrebbe detto «non registrato» su un webhook
    registrato, e ogni consegna rifiutata avrebbe ritentato per niente. Segnalato
    da Claude Fable 5 sulla PR #14.

    Il rimedio non e- rendere `True` appiccicoso — cosi- un fallimento vero
    diventerebbe invisibile per sempre, che e- la direzione in cui questo flag non
    deve mentire — ma numerare i tentativi e ignorare la scrittura di uno vecchio.
    """
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', BOT_FINTO)
    monkeypatch.setattr(main, '_WEBHOOK_REGISTRATO', None)
    monkeypatch.setattr(main, '_ULTIMO_TENTATIVO', 0.0)
    monkeypatch.setattr(main, '_TENTATIVI_EMESSI', 0, raising=False)
    monkeypatch.setattr(main, '_TENTATIVO_DELL_ESITO', 0, raising=False)

    lento_e_dentro = threading.Event()
    veloce_ha_finito = threading.Event()

    def finta(bot, url):
        if threading.current_thread().name == 'LENTO':
            lento_e_dentro.set()
            assert veloce_ha_finito.wait(10), 'il tentativo veloce non e- finito'
            return False
        return True

    monkeypatch.setattr(main, '_chiama_set_webhook', finta)

    def corri_veloce():
        main.assicura_registrazione(forza=True)
        veloce_ha_finito.set()

    lento = threading.Thread(target=main.assicura_registrazione,
                             kwargs={'forza': True}, name='LENTO')
    veloce = threading.Thread(target=corri_veloce, name='VELOCE')

    lento.start()
    assert lento_e_dentro.wait(10), 'il tentativo lento non e- partito'
    veloce.start()
    veloce.join(10)
    assert main._WEBHOOK_REGISTRATO is True, 'il tentativo riuscito non ha scritto'
    lento.join(10)
    assert not lento.is_alive()

    assert main._WEBHOOK_REGISTRATO is True, (
        'un tentativo piu- VECCHIO e fallito ha sovrascritto l-esito riuscito di '
        'uno piu- recente'
    )


def test_un_fallimento_piu_recente_resta_visibile(monkeypatch):
    """L-altra faccia del test qui sopra, e la ragione per cui non basta «True appiccicoso».

    Se Telegram smette davvero di conoscere il segreto — la registrazione viene
    sovrascritta da un altro deploy, il bot cambia token — il tentativo che
    fallisce e- l-ultimo emesso, e `/health` deve dirlo. Una regola che vietasse
    di scrivere `False` dopo un `True` renderebbe questo caso invisibile.
    """
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', BOT_FINTO)
    monkeypatch.setattr(main, '_WEBHOOK_REGISTRATO', True)
    monkeypatch.setattr(main, '_ULTIMO_TENTATIVO', 0.0)
    monkeypatch.setattr(main, '_chiama_set_webhook', lambda bot, url: False)
    assert main.assicura_registrazione(forza=True) is False
    assert main._WEBHOOK_REGISTRATO is False, (
        'un fallimento successivo a una registrazione riuscita non e- piu- '
        'visibile su /health'
    )
