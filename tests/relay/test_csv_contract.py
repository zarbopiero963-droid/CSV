"""Primi test del relay: il CSV servito a XTrader, byte per byte.

Fino a questo file `main.py` non aveva test propri, e il contratto CSV era
protetto da una frase in CLAUDE.md che diceva «già verificato byte per byte»
senza che nessuno l'avesse fatto. Il feed usciva **senza BOM** mentre XTrader
lo pretende, e nessun controllo se ne accorgeva.

Qui il BOM e la forma della riga sono verificati due volte, a due livelli
diversi, perché servono a cose diverse:

- sulle funzioni pure (`make_csv`, `empty_csv`, `verify_csv`), per dire *cosa*
  è sbagliato quando si rompe;
- sui **byte della risposta HTTP**, perché è quello che XTrader legge davvero e
  perché una funzione corretta con una risposta codificata male darebbe un test
  verde e un cliente senza segnali.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE))

import main  # noqa: E402 - dopo l'inserimento del percorso


# Intestazione attesa, costruita dalle colonne reali e non ricopiata a mano:
# una copia a mano si allineerebbe da sola a un ordine sbagliato.
INTESTAZIONE = ','.join('"%s"' % c for c in main.HEADERS)

RIGA_VALIDA = ['XTrader', '', 'Juventus - Palermo', '', 'Over/Under 1,5 gol',
               'OVER_UNDER_15', '', 'Over 1,5 goal', '0', '', '', '', 'PUNTA', '']


# ------------------------------------------------------------ funzioni pure

def test_make_csv_comincia_con_il_bom():
    testo = main.make_csv(RIGA_VALIDA)
    assert testo.startswith('﻿'), 'il CSV di un segnale non ha il BOM'
    assert testo.encode('utf-8').startswith(b'\xef\xbb\xbf')


def test_empty_csv_comincia_con_il_bom():
    """Anche il feed vuoto: e- la forma in cui XTrader lo trova per 90 secondi su 90."""
    testo = main.empty_csv()
    assert testo.startswith('﻿'), 'il CSV a sola intestazione non ha il BOM'


def test_l_intestazione_e_esatta_e_nell_ordine():
    corpo = main.empty_csv().lstrip('﻿')
    assert corpo.split('\r\n')[0] == INTESTAZIONE
    assert len(main.HEADERS) == 14


def test_verify_csv_accetta_il_formato_giusto():
    main.verify_csv(main.make_csv(RIGA_VALIDA))
    main.verify_csv(main.empty_csv())


# L'intestazione a 11 colonne del vecchio prototipo del Bridge: un formato che
# esiste davvero, documentato nell'archivio di quel progetto, ed esattamente il
# tipo di file che un verificatore deve respingere.
INTESTAZIONE_VECCHIA = ('Provider,SelectionId,MarketId,SelectionName,MarketName,'
                        'EventName,MarketType,BetType,Price,MinPrice,MaxPrice')


@pytest.mark.parametrize('nome,testo', [
    ('BOM assente', INTESTAZIONE + '\r\n'),
    ('intestazione a 11 colonne del vecchio prototipo', '﻿' + INTESTAZIONE_VECCHIA + '\r\n'),
    ('intestazione senza virgolette', '﻿' + ','.join(main.HEADERS) + '\r\n'),
    ('LF nudo invece di CRLF', '﻿' + INTESTAZIONE + '\n'),
    ('riga con 13 campi', '﻿' + INTESTAZIONE + '\r\n' + ','.join('"x"' for _ in range(13)) + '\r\n'),
    ('riga con 15 campi', '﻿' + INTESTAZIONE + '\r\n' + ','.join('"x"' for _ in range(15)) + '\r\n'),
    ('due segnali invece di uno', '﻿' + INTESTAZIONE + '\r\n'
     + ','.join('"x"' for _ in range(14)) + '\r\n'
     + ','.join('"y"' for _ in range(14)) + '\r\n'),
    ('vuoto', ''),
])
def test_verify_csv_respinge(nome, testo):
    with pytest.raises(ValueError):
        main.verify_csv(testo)


def test_una_virgola_dentro_un_campo_sopravvive():
    """`Over/Under 1,5 gol` ha una virgola: e- il motivo per cui QUOTE_ALL non e- estetica."""
    testo = main.verify_csv(main.make_csv(RIGA_VALIDA))
    assert '"Over/Under 1,5 gol"' in testo
    corpo = testo.lstrip('﻿')
    assert len(corpo.split('\r\n')[1].split('","')) == 14, 'la virgola ha spezzato la riga'


# --------------------------------------------------------------- scrittura

def test_store_signal_rifiuta_un_csv_malformato(tmp_path, monkeypatch):
    """Fail-closed nel punto in cui il dato nasce, non in quello in cui viene servito.

    Cosi- una riga malformata non esiste nemmeno per i 90 secondi del TTL.
    """
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'test.db'))
    c = main.db()
    try:
        with pytest.raises(ValueError):
            main.store_signal(c, 'Provider,EventId\r\n', 'parser-finto', 'PIERO')
        righe = c.execute('SELECT COUNT(*) FROM signals').fetchone()[0]
        assert righe == 0, 'il CSV malformato e- stato memorizzato'
    finally:
        c.close()


def test_store_signal_accetta_un_csv_valido(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'test.db'))
    c = main.db()
    try:
        main.store_signal(c, main.make_csv(RIGA_VALIDA), 'parser-finto', 'PIERO')
        salvato = c.execute('SELECT csv FROM signals WHERE profile=?', ('PIERO',)).fetchone()[0]
        assert salvato.startswith('﻿'), 'il BOM non e- arrivato fino al database'
    finally:
        c.close()


# ------------------------------------------------------------------- HTTP

def _porta_libera() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@pytest.fixture(scope='module')
def servizio(tmp_path_factory):
    """Il servizio vero su una porta libera: i byte contano, non le stringhe."""
    porta = _porta_libera()
    db = tmp_path_factory.mktemp('relay') / 'signals.db'
    proc = subprocess.Popen(
        [sys.executable, '-m', 'uvicorn', 'main:app', '--host', '127.0.0.1',
         '--port', str(porta), '--log-level', 'warning'],
        cwd=RADICE, env={'PATH': '/usr/bin:/bin:/usr/local/bin', 'DB_PATH': str(db)},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    base = f'http://127.0.0.1:{porta}'
    try:
        scaduto = time.monotonic() + 30
        while time.monotonic() < scaduto:
            if proc.poll() is not None:
                pytest.fail(f'uvicorn e- morto durante l-avvio:\n{proc.stdout.read()[-2000:]}')
            try:
                with urllib.request.urlopen(f'{base}/health', timeout=1) as r:
                    if r.status == 200:
                        break
            except (urllib.error.URLError, OSError):
                time.sleep(0.2)
        else:
            pytest.fail('uvicorn non ha risposto su /health entro 30 s')
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def _get(base, path):
    with urllib.request.urlopen(f'{base}{path}', timeout=10) as r:
        return r.status, r.headers, r.read()


def test_il_feed_http_comincia_con_i_byte_del_bom(servizio):
    """Il test che il difetto originale avrebbe fatto fallire.

    Asserito sui BYTE della risposta, non sulla stringa: una funzione corretta
    con una risposta codificata male darebbe comunque verde.
    """
    stato, intestazioni, corpo = _get(servizio, '/xtrader.csv')
    assert stato == 200
    assert corpo.startswith(b'\xef\xbb\xbf"Provider","EventId"'), \
        f'il feed non comincia col BOM: {corpo[:20]!r}'
    assert intestazioni.get('content-type', '').startswith('text/csv')


def test_il_feed_vuoto_ha_il_bom_e_una_riga_sola(servizio):
    """Su un profilo appena creato, quindi senza dipendere dall'ordine dei test.

    Usare `/xtrader.csv` qui lo renderebbe fragile: un altro test che scrive un
    segnale lo farebbe fallire, e il TTL di 90 secondi tiene quel segnale vivo
    per tutto il modulo. Un test che si rompe riordinando il file non e- un test.
    """
    import json
    req = urllib.request.Request(
        f'{servizio}/api/profiles',
        data=json.dumps({'name': 'VUOTO', 'chat_ids': '', 'parser': main.DEFAULT_PARSER}).encode(),
        headers={'Content-Type': 'application/json'}, method='POST',
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        assert r.status == 200

    _, _, corpo = _get(servizio, '/profiles/VUOTO.csv')
    righe = [r for r in corpo.split(b'\r\n') if r]
    assert len(righe) == 1, f'senza segnale il feed deve avere solo l-intestazione: {righe}'
    assert corpo.startswith(b'\xef\xbb\xbf'), 'il feed vuoto non ha il BOM'
    assert b'\n' not in corpo.replace(b'\r\n', b''), 'trovato un LF nudo'


def test_l_alias_legacy_per_profilo_ha_lo_stesso_formato(servizio):
    """`/profiles/PIERO.csv` e- documentato in README.txt: non deve divergere."""
    _, _, corpo = _get(servizio, '/profiles/PIERO.csv')
    assert corpo.startswith(b'\xef\xbb\xbf"Provider"')


def test_l_api_di_prova_restituisce_il_csv_col_bom(servizio):
    """Regola 2-bis: il test sta sul CHIAMANTE, non solo sulla funzione.

    `make_csv()` ora restituisce testo col BOM, e questo endpoint quel testo lo
    rimanda dentro il JSON. Chi legge la risposta riceve un U+FEFF in testa alla
    stringa: e- corretto, perche- quello E- il CSV, ma e- un cambiamento visibile
    del contratto dell'API e va fissato da un test invece di essere scoperto.
    """
    import json
    messaggio = 'P.Bet. PREMACHT 0,5HT\n\U0001F19A Juventus v Palermo\n@ 1.85'
    req = urllib.request.Request(
        f'{servizio}/api/test-message',
        data=json.dumps({'message': messaggio}).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        dati = json.loads(r.read())
    assert dati['ok'] is True, dati
    assert dati['csv'].startswith('﻿'), 'il CSV nel JSON non ha il BOM'
    assert dati['event'] == 'Juventus - Palermo', dati['event']
    # E il segnale appena scritto esce dal feed con gli stessi byte.
    _, _, corpo = _get(servizio, '/xtrader.csv')
    assert corpo.startswith(b'\xef\xbb\xbf"Provider"')
    assert 'Juventus - Palermo'.encode('utf-8') in corpo


def test_health_riporta_l_esito_del_verificatore(servizio):
    """Il controllo deve stare dove si guarda, non solo esistere nel codice.

    E- la lezione del Bridge: la- `is_bridge_csv` esisteva ed era usata altrove,
    ma nessuno l-aveva agganciata al pannello, e l-unico avviso era una riga di
    log all-avvio.
    """
    import json
    _, _, corpo = _get(servizio, '/health')
    dati = json.loads(corpo)
    assert 'csv' in dati, f'/health non dice niente sul formato CSV: {dati}'
    assert dati['csv'] == 'ok', dati
