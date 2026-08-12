"""Il parser davanti a un messaggio storto: `None`, non un'eccezione.

Perche' questo file esiste, MISURATO sulla funzione vera prima della patch:

    parse_message('SEGNALE\\n\\U0001F19A',              cfg)  ->  IndexError
    parse_message('SEGNALE\\n\\U0001F19A   ',           cfg)  ->  IndexError
    parse_message('SEGNALE\\n\\U0001F19A\\t',            cfg)  ->  IndexError
    parse_message('SEGNALE\\nSQUADRA-A v SQUADRA-B \\U0001F19A', cfg)  ->  IndexError

La causa non e' dove sembrava. `line.split(marcatore, 1)[1]` non puo' sollevare:
`line` contiene il marcatore per costruzione — e' stata scelta filtrando su quello —
quindi la lista ha sempre due elementi e il secondo, al massimo, e' vuoto. Il punto
che solleva e' la riga dopo: `''.splitlines()` e' `[]`, e `[0]` su una lista vuota
solleva `IndexError`.

Quel `.splitlines()[0]` era inoltre **inutile**: `line` viene da
`message.splitlines()`, quindi non contiene interruzioni di riga e riestrarne la
prima non poteva cambiare niente. Non faceva nulla nel caso normale e faceva
cadere il servizio nel caso vuoto.

**Cosa costava, e a chi.** `parse_message` ha due chiamanti e nessuno dei due
protegge la chiamata:

- `POST /telegram/webhook` — pubblico. Un messaggio cosi- da una chat autorizzata
  faceva rispondere **500**, e Telegram *ritenta* le consegne fallite: lo stesso
  messaggio tornava, sollevava di nuovo, e quel segnale non arrivava mai a XTrader.
- `POST /api/parsers/{name}/test` — l'amministratore che prova un messaggio
  riceveva **500** invece del **422** documentato.

Il caso realistico non e' il marcatore nudo. E' l'ultimo dei quattro:
`SQUADRA-A v SQUADRA-B \U0001F19A`, cioe- il marcatore in **coda** alla riga. Un canale
che scrive le squadre prima del marcatore fa cadere il webhook al primo messaggio,
e non e' un formato esotico: e' una scelta di chi scrive il canale.

Il comportamento giusto e- scritto nel contratto del progetto — «messaggio vuoto o
non supportato -> nessuna riga scritta, nessun errore», e «il parser non inventa
dati mancanti». Quindi `None`: non riconosciuto.

Voce registrata nella Issue #2, PR 4, ultimo punto rimasto aperto.
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
from tests.ambiente import CHIAVI_PERICOLOSE, TOKEN_DI_PROVA  # noqa: E402
from tests.servizio import relay_avviato  # noqa: E402

# Il marcatore per CODEPOINT e non per aspetto: `\U0001F19A` e una sequenza con
# variation selector si vedono uguali e non sono uguali (vedi «REGOLA CODIFICA»).
MARCATORE = '\U0001F19A'
HEADER = 'SEGNALE-DI-PROVA'
CHAT = '-1000000000000'
# L'header del parser creato all'avvio, quello che il profilo PIERO usa davvero.
# Serve per raggiungere il punto che solleva passando dalla rotta di prova: senza,
# la richiesta si fermerebbe al primo gate.
HEADER_DI_DEFAULT = 'P.Bet. PREMACHT 0,5HT'
# Un token dalla forma giusta (cifre, due punti, 35 caratteri) perche' il segreto
# ne viene derivato, ma non e' un bot che esiste.
BOT_FINTO = '000000000:' + 'FINTO-NON-ESISTE-NON-E-UN-BOT-VERO-0'

CFG = {
    'name': 'prova',
    'header': HEADER,
    'market_name': 'Over/Under 1,5 gol',
    'market_type': 'OVER_UNDER_15',
    'selection_name': 'Over 1.5 gol',
    'handicap': '1.5',
    'bet_type': 'PUNTA',
}


@pytest.fixture(autouse=True)
def _ambiente_pulito(monkeypatch):
    """Nessuna variabile della macchina puo' entrare in questi test.

    Serve anche qui e non solo nella fixture del sottoprocesso: l'handler di avvio
    legge `os.environ` DIRETTAMENTE, quindi azzerare le costanti del modulo non
    basterebbe a impedire che un `.env` del proprietario ripunti il webhook del bot
    vero. Vedi `tests/ambiente.py`.
    """
    for chiave in CHIAVI_PERICOLOSE:
        monkeypatch.delenv(chiave, raising=False)
    monkeypatch.setattr(main, 'TOKEN', TOKEN_DI_PROVA)


# --------------------------------------------------- la funzione, da sola

# I quattro casi misurati rossi prima della patch, piu' le varianti di spazio
# vuoto che il contratto tratta come «mancante».
@pytest.mark.parametrize('coda', ('', '   ', '\t', ' \t ', ' '), ids=(
    'nudo', 'spazi', 'tab', 'spazi_e_tab', 'spazio_insecabile'))
def test_un_marcatore_senza_evento_da_None_e_non_solleva(coda):
    """Il caso registrato: nessun evento dopo il marcatore.

    ` ` (spazio insecabile) e' nell'elenco perche' arriva davvero dai messaggi
    Telegram copiati e incollati, e `str.strip()` lo rimuove: se un domani la
    normalizzazione cambiasse, questo caso tornerebbe a essere un evento fatto di
    un carattere invisibile — cioe- una riga CSV con un nome squadra vuoto.
    """
    assert main.parse_message(f'{HEADER}\n{MARCATORE}{coda}', CFG) is None


def test_il_marcatore_in_CODA_alla_riga_da_None():
    """Il caso realistico, e quello che rende il difetto raggiungibile in produzione.

    Un canale che scrive «SQUADRA-A v SQUADRA-B \U0001F19A» mette le squadre PRIMA del
    marcatore. Il parser legge cio- che viene dopo, che qui e- vuoto: non
    riconosciuto. Prima della patch era un `IndexError`, quindi un 500.
    """
    testo = f'{HEADER}\nSQUADRA-A v SQUADRA-B {MARCATORE}'
    assert main.parse_message(testo, CFG) is None


def test_un_messaggio_valido_continua_a_essere_riconosciuto():
    """Il controllo che impedisce la correzione pigra «restituisci sempre None».

    Senza questo caso, `return None` in cima alla funzione farebbe passare tutti
    gli altri test di questo file e spegnerebbe il servizio.
    """
    testo = f'{HEADER}\n{MARCATORE} SQUADRA-A v SQUADRA-B\n@ 1.85'
    parsed = main.parse_message(testo, CFG)
    assert parsed is not None, 'un messaggio valido non e- piu- riconosciuto'
    assert parsed['event'] == 'SQUADRA-A - SQUADRA-B', parsed['event']


def test_un_nome_squadra_che_contiene_v_non_viene_spezzato():
    """Si sostituisce l'ULTIMA occorrenza di « v », non la prima.

    Vincolato qui perche' la patch tocca le righe immediatamente sopra: se qualcuno
    riscrivesse questo blocco usando `split(' v ')` invece dell'ultima corrispondenza,
    «Real v Sociedad v Betis» diventerebbe «Real - Sociedad v Betis» e nessun altro
    test di questo file se ne accorgerebbe.
    """
    testo = f'{HEADER}\n{MARCATORE} Deportivo v Alaves v Osasuna'
    parsed = main.parse_message(testo, CFG)
    assert parsed is not None
    assert parsed['event'] == 'Deportivo v Alaves - Osasuna', parsed['event']


def test_senza_header_resta_None_come_prima():
    """Il primo gate non e' stato toccato: header assente -> non riconosciuto."""
    assert main.parse_message(f'ALTRO TESTO\n{MARCATORE} SQUADRA-A v SQUADRA-B', CFG) is None


# ------------------------------------------- il giro HTTP vero, sul servizio

def _consegna(base, testo):
    """Simula una consegna di Telegram e restituisce (stato, corpo)."""
    payload = {'message': {'chat': {'id': int(CHAT)}, 'text': testo}}
    req = urllib.request.Request(
        f'{base}/telegram/webhook',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json',
                 # Firmata come la firmerebbe Telegram: il segreto e' derivato dal
                 # token del bot, non configurato a mano (PR #14).
                 'X-Telegram-Bot-Api-Secret-Token': main.webhook_secret(BOT_FINTO)},
        method='POST')
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
def servizio(tmp_path_factory):
    """Il relay col bot, e le consegne firmate col segreto derivato.

    Serve il bot: SENZA `TELEGRAM_BOT_TOKEN` il webhook rifiuta **ogni** consegna
    con 403, perche' il segreto non e' derivabile e la #14 e' fail-closed. La prima
    versione di questa fixture non lo passava e questa docstring affermava
    l'opposto — «senza bot accetta senza segreto» — - misurato: 403 su tutto, e
    nessuno dei test HTTP raggiungeva il parser. Il fail-closed dell'header ha i
    suoi test in `tests/relay/test_webhook.py`: qui serve solo attraversarlo.

    `PUBLIC_URL` punta a un host inesistente di proposito, cosi' la registrazione
    del webhook fallisce senza toccare nulla di reale: il segreto resta derivabile,
    che e- la condizione che governa l'enforcement.
    """
    with relay_avviato(tmp_path_factory.mktemp('parse'),
                       CSV_ACCESS_TOKEN=TOKEN_DI_PROVA,
                       TELEGRAM_BOT_TOKEN=BOT_FINTO,
                       TELEGRAM_ALLOWED_CHAT_IDS=CHAT,
                       PUBLIC_URL='https://non-esiste.invalid') as base:
        yield base


def test_il_webhook_risponde_200_e_non_500(servizio):
    """Il fail-first sul percorso vero: prima della patch era 500.

    Il codice di stato e' la cosa che conta piu- del corpo, e non per pulizia
    formale: Telegram RITENTA una consegna fallita. Un 500 su un messaggio storto
    non e' un errore isolato, e' lo stesso messaggio che torna, solleva di nuovo, e
    fa perdere quel segnale — mentre i log si riempiono di tracce identiche.
    """
    stato, corpo = _consegna(servizio, f'{HEADER_DI_DEFAULT}\n{MARCATORE}')
    assert stato == 200, f'il webhook risponde {stato}: Telegram ritentera- in loop'
    assert json.loads(corpo).get('ignored') == 'parser_no_match', corpo


def test_un_messaggio_storto_non_scrive_niente_nel_feed(servizio):
    """E il feed resta a sola intestazione: nessuna riga parziale, nessun BOM perso."""
    _consegna(servizio, f'{HEADER_DI_DEFAULT}\n{MARCATORE}')
    byte = _feed(servizio)
    assert byte.startswith('﻿'.encode('utf-8') + b'"Provider","EventId"'), byte[:40]
    assert byte.count(b'\r\n') == 1, f'scritta una riga che non doveva esistere: {byte!r}'


def test_dopo_un_messaggio_storto_il_servizio_accetta_ancora_quelli_buoni(servizio):
    """L'ordine conta: prima quello che faceva cadere l'handler, poi quello valido.

    Se il difetto lasciasse il servizio in uno stato rotto — una connessione
    inutilizzabile, una transazione aperta — il messaggio buono che segue
    fallirebbe, e nessuno dei test qui sopra lo mostrerebbe.
    """
    _consegna(servizio, f'{HEADER_DI_DEFAULT}\n{MARCATORE}')
    stato, corpo = _consegna(servizio, f'{HEADER_DI_DEFAULT}\n{MARCATORE} SQUADRA-A v SQUADRA-B\n@ 1.85')
    assert stato == 200, corpo
    assert json.loads(corpo).get('ignored') is None, corpo
    assert b'SQUADRA-A - SQUADRA-B' in _feed(servizio)


def test_la_rotta_di_prova_del_parser_da_422_e_non_500(servizio):
    """L'altro chiamante, `POST /api/parsers/{name}/test`.

    Regola 2-bis: `parse_message` ha due chiamanti e la correzione riguarda
    entrambi. Qui il 422 e' il comportamento gia' documentato per «messaggio non
    riconosciuto» — prima della patch questo caso dava 500, cioe- un difetto del
    server al posto di una risposta prevista.

    Il messaggio deve contenere l'header del parser di default, altrimenti si ferma
    al PRIMO gate («header non presente -> None») e restituisce 422 senza mai
    arrivare al punto che solleva. La prima versione di questo test mandava il solo
    marcatore, passava sul codice difettoso, e non misurava niente: verificato
    eseguendolo prima della patch.
    """
    req = urllib.request.Request(
        f'{servizio}/api/parsers/{main.DEFAULT_PARSER}/test',
        data=json.dumps({'message': f'{HEADER_DI_DEFAULT}\n{MARCATORE}'}).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'X-Admin-Token': TOKEN_DI_PROVA},
        method='POST')
    try:
        with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 - loopback
            stato = r.status
    except urllib.error.HTTPError as e:
        stato = e.code
    assert stato == 422, f'atteso 422 «non riconosciuto», ricevuto {stato}'
