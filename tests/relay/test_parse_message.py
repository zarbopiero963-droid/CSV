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
@pytest.mark.parametrize('coda', ('', '   ', '\t', ' \t ', '\xa0'), ids=(
    'nudo', 'spazi', 'tab', 'spazi_e_tab', 'spazio_insecabile'))
def test_un_marcatore_senza_evento_da_None_e_non_solleva(coda):
    """Il caso registrato: nessun evento dopo il marcatore.

    `\xa0` (spazio insecabile) e' nell'elenco perche' arriva davvero dai messaggi
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


def test_legacy_localizza_l_handicap_col_punto():
    """Audit #81 (C1): il percorso legacy non localizzava l'handicap.

    Un parser legacy con `handicap` scritto col punto («0.5») costruiva una riga
    con «0.5», e `verify_csv` la scartava in SILENZIO perche' il contratto vuole la
    virgola (#40). Ora `parse_message` passa dallo stesso `_giudica_riga` del motore,
    che localizza: la riga esce con «0,5» e supera il verificatore.

    Fail-first: sul codice vecchio la riga contiene «0.5» e `verify_csv` SOLLEVA.
    """
    cfg = {**CFG, 'handicap': '0.5', 'selection_name': 'Over',
           'market_name': 'M', 'market_type': 'OVER_UNDER_05'}
    testo = f'{HEADER}\n{MARCATORE} SQUADRA-A v SQUADRA-B'
    parsed = main.parse_message(testo, cfg)
    assert parsed is not None, 'un messaggio valido non deve sparire'
    documento = parsed['csv']
    testo_csv = documento.decode('utf-8') if isinstance(documento, bytes) else documento
    assert '"0,5"' in testo_csv and '"0.5"' not in testo_csv, testo_csv
    main.verify_csv(documento)  # non deve sollevare: era il difetto C1


def test_legacy_diagnostica_l_emoji_invece_di_scartare_muto():
    """Audit #81 (C2): un'emoji decorativa nell'evento non deve sparire in silenzio.

    Il percorso legacy estraeva l'evento dopo il marcatore senza togliere le emoji
    decorative finali; `verify_csv` rifiutava la riga e il segnale spariva SENZA una
    riga di diagnosi (il legacy restituiva sempre `motivi=[]`). Ora `esito_messaggio`
    passa dal giudizio comune: nessun segnale, ma il PERCHE' viaggia nei `motivi`,
    che il dispatch scrive in `message_logs`.

    Fail-first: sul codice vecchio `motivi` e' vuoto e `parsed` non e' None (la riga
    con l'emoji viene costruita e poi scartata a valle).
    """
    cfg = {**CFG, 'handicap': '0', 'selection_name': 'Over',
           'market_name': 'M', 'market_type': 'OVER_UNDER_05'}
    testo = f'{HEADER}\n{MARCATORE} SQUADRA-A v SQUADRA-B ✅'
    parsed, motivi = main.esito_messaggio(testo, cfg)
    assert parsed is None, 'un evento con emoji non deve produrre un segnale valido'
    assert any('emoji' in m.lower() for m in motivi), (
        f'il motivo dell\'emoji non e- stato diagnosticato: {motivi!r}')


def test_il_profilo_legacy_scrive_la_diagnosi_non_il_generico(tmp_path, monkeypatch):
    """Audit #81 (C2), verso dispatch del profilo: la diagnosi arriva in fondo.

    `_elabora_profilo` usava `elabora_messaggio`, che scarta i motivi, e tornava
    `parser_no_match` — il generico, non il perche'. Ora usa `esito_messaggio` e
    restituisce «scartato: ...emoji...», che il chiamante scrive in `message_logs`.
    Il verso end-to-end del ramo legacy, oltre all'unita' di `esito_messaggio`.
    """
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'profilo.db'))
    c = main.db()
    main.migra(c)
    c.execute("INSERT INTO parsers(name, header, market_name, market_type,"
              " selection_name, handicap, bet_type)"
              " VALUES ('leg', ?, 'M', 'OVER_UNDER_05', 'Over', '0', 'PUNTA')", (HEADER,))
    c.commit()
    profilo = {'parser': 'leg', 'name': 'PIERO'}

    # Emoji nell'evento: nessun segnale, ma la CAUSA e' esplicita, non parser_no_match.
    esito = main._elabora_profilo(c, profilo, f'{HEADER}\n{MARCATORE} A v B ✅')
    assert isinstance(esito, str) and esito.startswith('scartato:'), esito
    assert 'emoji' in esito.lower(), esito

    # Un messaggio pulito continua a produrre il segnale (non e- diventato tutto «scartato»).
    ok = main._elabora_profilo(c, profilo, f'{HEADER}\n{MARCATORE} A v B')
    assert isinstance(ok, dict) and ok.get('event') == 'A - B', ok
    c.close()


def test_legacy_un_handicap_GIA_localizzato_resta_byte_identico():
    """Il rovescio di C1: passare dal giudizio comune non deve TOCCARE cio' che e' gia' corretto.

    Il feed di PIERO usa `handicap='0'`, ma un parser legacy puo' avere un handicap
    gia' scritto con la virgola («0,5»). `_giudica_riga` localizza `.`->`,`: se lo
    facesse anche su un valore gia' localizzato — o rimpiazzasse in modo ingenuo —
    «0,5» diventerebbe «0,,5» o peggio, e il segnale di PIERO cambierebbe di byte
    sotto una riga che «non doveva muoversi». Questo e' il guard che GPT-5.5 ha
    chiesto sulla PR #84: byte-invarianza sul percorso gia- corretto.

    Non e' coperto dal test C1 sopra (quello parte da «0.5» e verifica che DIVENTI
    «0,5»); qui il valore entra gia- «0,5» e deve USCIRE identico.
    """
    cfg = {**CFG, 'handicap': '0,5', 'selection_name': 'Over',
           'market_name': 'M', 'market_type': 'OVER_UNDER_05'}
    testo = f'{HEADER}\n{MARCATORE} SQUADRA-A v SQUADRA-B'
    parsed = main.parse_message(testo, cfg)
    assert parsed is not None, 'un messaggio valido non deve sparire'
    documento = parsed['csv']

    # Byte per byte, non «contiene 0,5»: la riga attesa e' scritta A MANO qui —
    # indipendente da `_giudica_riga` — quindi l'uguaglianza dimostra che il
    # giudizio e' un no-op su un input gia' corretto, non solo che l'handicap
    # sopravvive. GPT-5.5 sulla PR #84: il nome prometteva piu' dell'asserzione.
    riga_attesa = ['XTrader', '', 'SQUADRA-A - SQUADRA-B', '', 'M', 'OVER_UNDER_05',
                   '', 'Over', '0,5', '', '', '', 'PUNTA', '']
    atteso = main.make_csv(riga_attesa)
    assert documento == atteso, (
        'il CSV non e- byte-identico alla riga attesa:\n'
        f'  atteso   : {atteso!r}\n'
        f'  ottenuto : {documento!r}')
    main.verify_csv(documento)  # il contratto regge


def test_il_profilo_legacy_col_MOTORE_scrive_ancora_il_segnale(tmp_path, monkeypatch):
    """Regola 2-bis su `_elabora_profilo`: il ramo `config_json` non e' regredito.

    La patch #84 ha riscritto `_elabora_profilo` per usare `esito_messaggio` invece
    di `elabora_messaggio`. Un profilo il cui parser ha una `config_json` passa dal
    MOTORE, non dal percorso legacy: questo test — chiesto da GPT-5.5 sulla PR #84 —
    dimostra che quel ramo continua a produrre il segnale dopo il cambio di dispatch,
    non solo il ramo legacy coperto sopra.
    """
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'motore.db'))
    c = main.db()
    main.migra(c)
    config = {
        'match': {'type': 'contains', 'value': 'SEGNALE'},
        'columns': {
            'EventName': {'source': 'line', 'anchor': 'evento', 'part': 'after',
                          'marker': ':', 'transforms': [{'op': 'trim'}]},
            'MarketType': {'source': 'constant', 'value': 'OVER_UNDER_15'},
            'SelectionName': {'source': 'constant', 'value': 'Over 1,5 goal'},
            'BetType': {'source': 'constant', 'value': 'PUNTA'},
        },
    }
    c.execute("INSERT INTO parsers(name, header, market_name, market_type,"
              " selection_name, handicap, bet_type, config_json)"
              " VALUES ('mot', '', '', '', '', '', '', ?)", (json.dumps(config),))
    c.commit()
    profilo = {'parser': 'mot', 'name': 'PIERO'}

    esito = main._elabora_profilo(c, profilo, 'SEGNALE\nevento: Roma v Lazio\n@ 2.10')
    assert isinstance(esito, dict), f'il ramo motore non ha prodotto il segnale: {esito!r}'
    assert esito.get('event') == 'Roma v Lazio', esito
    c.close()


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


# Un proxy che punta a una porta CHIUSA sul loopback. Serve a impedire che l'avvio
# del servizio raggiunga `api.telegram.org`, e la ragione e' misurata, non temuta:
# `_chiama_set_webhook` costruisce l'URL con l'host di Telegram CABLATO nel sorgente
# e `PUBLIC_URL` finisce solo nel corpo del POST. Puntare `PUBLIC_URL` a un host
# inesistente quindi NON evita la chiamata — misurato: `HTTPError` in 0,83 s, cioe'
# Telegram ha risposto. Con questo proxy la richiesta non lascia la macchina.
# Segnalato da `gpt-5.6-sol` sulla PR #21; la prima versione di questa fixture
# affermava «senza toccare nulla di reale» ed era falsa.
PROXY_MORTO = 'http://127.0.0.1:1'

# Fonte unica dell'ambiente del servizio: la fixture lo usa e un test lo ISPEZIONA.
# Due copie — una nella fixture e una nell'asserzione — sarebbero due copie
# divergenti domani (regola 3).
AMBIENTE_DEL_SERVIZIO = {
    'CSV_ACCESS_TOKEN': TOKEN_DI_PROVA,
    # Serve il bot: SENZA `TELEGRAM_BOT_TOKEN` il webhook rifiuta ogni consegna con
    # 403, perche' il segreto non e' derivabile e la #14 e' fail-closed. Misurato:
    # senza, tutti i test HTTP di questo file davano 403 e nessuno arrivava al parser.
    'TELEGRAM_BOT_TOKEN': BOT_FINTO,
    'PUBLIC_URL': 'https://non-esiste.invalid',
    'HTTPS_PROXY': PROXY_MORTO,
    'https_proxy': PROXY_MORTO,
}


@pytest.fixture
def servizio(tmp_path):
    """Un relay PER TEST, col bot, e senza uscite verso l'esterno.

    Non piu' `scope='module'`, e la ragione e' un difetto misurato: con un servizio
    condiviso un test scriveva nel feed e un altro pretendeva la sola intestazione,
    quindi l'esito dipendeva dall'ORDINE. Verificato invertendo i due:

        pytest ...::test_dopo_un_messaggio_storto... ...::test_un_messaggio_storto_non_scrive...
        -> 1 failed  «scritta una riga che non doveva esistere»

    Era verde solo per l'ordine del file, e sarebbe diventato rosso al primo
    `-p randomly` o al primo `--lf`. Segnalato da `gpt-5.6-sol` sulla PR #21.

    Un sottoprocesso per test costa ~1 s: il prezzo giusto per un test che misura
    quello che dice invece di quello che il vicino ha lasciato.
    """
    # La chat del profilo PIERO arriva dalla SEMINA, non piu' da
    # `TELEGRAM_ALLOWED_CHAT_IDS`: quella variabile la leggeva il seme di
    # `migra()`, rimosso col lavoro E della #25, e da allora e' inerte.
    with relay_avviato(tmp_path, chat_ids=CHAT, **AMBIENTE_DEL_SERVIZIO) as base:
        yield base


def _attendi_righe_del_log(log: Path, cosa: str, secondi: float = 15.0) -> list:
    """Aspetta che nel log compaia almeno una riga con `cosa`, e le restituisce.

    Serve perche' la registrazione del webhook e' ASINCRONA rispetto all'avvio: la
    fixture attende `/health`, che risponde prima che il tentativo sia finito. Il
    limite e' generoso di proposito — nel caso peggiore documentato in `README.txt`
    i tre tentativi durano ~33 s, ma col proxy morto ogni rifiuto e' immediato,
    quindi la prima riga arriva in millesimi e i 15 s non vengono mai avvicinati.
    """
    import time
    scadenza = time.monotonic() + secondi
    ultimo = ''
    while time.monotonic() < scadenza:
        ultimo = log.read_text(encoding='utf-8') if log.exists() else ''
        righe = [r for r in ultimo.splitlines() if cosa in r]
        if righe:
            return righe
        time.sleep(0.05)
    raise AssertionError(
        f'nessuna riga con {cosa!r} entro {secondi} s: il servizio non ha nemmeno '
        f'tentato la registrazione.\nUltimo log:\n{ultimo[-800:]}')


def test_il_servizio_NON_ha_raggiunto_telegram(servizio, tmp_path):
    """La prova end-to-end: il log dice `URLError`, non `HTTPError`.

    Il test qui sotto verifica che la porta del proxy sia chiusa, che e- una
    condizione necessaria e non sufficiente: non dice che il processo usi davvero
    quel proxy, ne- che non abbia altre vie di rete. Segnalato da GPT-5.5 sulla PR
    #21, ed era una lacuna reale — la garanzia si appoggiava al fatto che `urllib`
    onori `HTTPS_PROXY`, che e- vero e non era vincolato da niente.

    Qui si legge l'esito vero. `_chiama_set_webhook` registra il solo NOME del tipo
    di eccezione (mai il messaggio, perche- conterrebbe il token del bot nell'URL), e
    quel nome distingue esattamente i due mondi:

        HTTPError  ->  Telegram HA risposto: la richiesta e- uscita dalla macchina
        URLError   ->  connessione rifiutata dal proxy morto: non e- uscita

    Misurato senza il proxy: `HTTPError` in 0,83 s. Con il proxy: `URLError`.

    **Si ASPETTA la riga invece di leggerla una volta**, e questa non e' prudenza
    generica: `README.txt` dice che la registrazione parte DIETRO l'avvio, e la
    fixture attende `/health`, che risponde prima. Leggere il log subito era quindi
    una corsa — vinta quasi sempre, perche' col proxy morto il rifiuto e' immediato,
    e persa a caso in CI. Segnalato da GPT-5.5 sulla PR #21, che ha chiesto di
    verificare proprio questo. Un test intermittente e' peggio di nessun test:
    insegna a rieseguire invece di leggere.
    """
    righe = _attendi_righe_del_log(tmp_path / 'uvicorn.log', 'registrazione webhook')
    assert any('URLError' in r for r in righe), (
        'atteso URLError (connessione rifiutata dal proxy morto). '
        'Righe trovate:\n' + '\n'.join(f'  {r}' for r in righe))
    assert not any('HTTPError' in r for r in righe), (
        'HTTPError significa che TELEGRAM HA RISPOSTO: la richiesta e- uscita dalla '
        'macchina e il proxy non ha protetto niente.\n' + '\n'.join(f'  {r}' for r in righe))


def test_il_servizio_di_prova_non_puo_chiamare_telegram():
    """La porta del proxy deve essere CHIUSA, non solo scritta nell'ambiente.

    Senza questa verifica «il proxy impedisce la chiamata» sarebbe un'affermazione:
    se un domani quella porta fosse in ascolto, le richieste ci passerebbero e la
    garanzia cadrebbe in silenzio. Qui si misura che una connessione viene rifiutata.
    """
    import socket
    porta = int(PROXY_MORTO.rsplit(':', 1)[1])
    with socket.socket() as s:
        s.settimeout(2)
        with pytest.raises(OSError):
            s.connect(('127.0.0.1', porta))


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
    """Il feed PRIMA e DOPO: il messaggio storto non lo cambia.

    Si confronta il DELTA invece di pretendere la sola intestazione in assoluto, e
    non e' pignoleria: la versione precedente asseriva `count(b'\\r\\n') == 1`, cioe'
    uno stato globale, e con un servizio condiviso passava solo se nessun altro test
    aveva scritto prima. Misurando il delta il test dice quello che vuole dire — «quel
    messaggio non ha scritto» — e non dipende da chi lo precede.

    Il BOM e' scritto `\\ufeff` e non come carattere letterale, come prescrive la
    regola di codifica: un U+FEFF nel sorgente e' invisibile in un editor. La prima
    versione di questa riga ne conteneva uno, e l'ho visto solo guardando i byte.
    """
    prima = _feed(servizio)
    assert prima.startswith('\ufeff'.encode('utf-8') + b'"Provider","EventId"'), prima[:40]
    assert prima.count(b'\r\n') == 1, f'il feed non parte vuoto: {prima!r}'

    _consegna(servizio, f'{HEADER_DI_DEFAULT}\n{MARCATORE}')

    dopo = _feed(servizio)
    assert dopo == prima, (
        'il messaggio storto ha cambiato il feed:\n'
        f'  prima : {prima!r}\n'
        f'  dopo  : {dopo!r}'
    )


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
