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
from tests.ambiente import ambiente_di_servizio  # noqa: E402


# Intestazione attesa, costruita dalle colonne reali e non ricopiata a mano:
# una copia a mano si allineerebbe da sola a un ordine sbagliato.
INTESTAZIONE = ','.join('"%s"' % c for c in main.HEADERS)

RIGA_VALIDA = ['XTrader', '', 'Juventus - Palermo', '', 'Over/Under 1,5 gol',
               'OVER_UNDER_15', '', 'Over 1,5 goal', '0', '', '', '', 'PUNTA', '']


@pytest.fixture(autouse=True)
def _senza_token_ambientale(monkeypatch):
    """Neutralizza `CSV_ACCESS_TOKEN` per le chiamate IN PROCESSO.

    La whitelist di `tests.ambiente` protegge il sottoprocesso uvicorn, ma non
    puo' fare niente per `import main`: `main.TOKEN` viene letto da `os.environ`
    al momento dell'import, quindi su una macchina dove quella variabile e'
    impostata i test che chiamano `profile_csv()` direttamente ricevono un 401 e
    la suite passa o fallisce a seconda di chi la esegue. Misurato: senza questa
    fixture, `CSV_ACCESS_TOKEN=... pytest tests/relay` fallisce tre test.

    Autouse di proposito: e' la classe del difetto, non i tre siti di oggi. Un
    test che voglia invece verificare il rifiuto per token errato imposta
    `main.TOKEN` da se', e il suo monkeypatch vince perche' arriva dopo.
    """
    monkeypatch.setattr(main, 'TOKEN', '')


# ------------------------------------------------------------ funzioni pure

def test_make_csv_comincia_con_il_bom():
    testo = main.make_csv(RIGA_VALIDA)
    assert testo.startswith('\ufeff'), 'il CSV di un segnale non ha il BOM'
    assert testo.encode('utf-8').startswith(b'\xef\xbb\xbf')


def test_empty_csv_comincia_con_il_bom():
    """Anche il feed vuoto: e- la forma in cui XTrader lo trova per 90 secondi su 90."""
    testo = main.empty_csv()
    assert testo.startswith('\ufeff'), 'il CSV a sola intestazione non ha il BOM'


def test_l_intestazione_e_esatta_e_nell_ordine():
    corpo = main.empty_csv().lstrip('\ufeff')
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
    ('intestazione a 11 colonne del vecchio prototipo', '\ufeff' + INTESTAZIONE_VECCHIA + '\r\n'),
    ('intestazione senza virgolette', '\ufeff' + ','.join(main.HEADERS) + '\r\n'),
    ('LF nudo invece di CRLF', '\ufeff' + INTESTAZIONE + '\n'),
    ('riga con 13 campi', '\ufeff' + INTESTAZIONE + '\r\n' + ','.join('"x"' for _ in range(13)) + '\r\n'),
    ('riga con 15 campi', '\ufeff' + INTESTAZIONE + '\r\n' + ','.join('"x"' for _ in range(15)) + '\r\n'),
    ('due segnali invece di uno', '\ufeff' + INTESTAZIONE + '\r\n'
     + ','.join('"x"' for _ in range(14)) + '\r\n'
     + ','.join('"y"' for _ in range(14)) + '\r\n'),
    ('vuoto', ''),
    # Segnalati da GPT-5.5 sulla PR #8: il contratto dichiara CRLF, e un
    # verificatore che accetta un CSV senza terminatore finale o con un CR
    # isolato non sta vincolando il contratto che dice di vincolare.
    ('senza terminatore finale', '\ufeff' + INTESTAZIONE),
    ('CR isolato dentro un campo', '\ufeff' + INTESTAZIONE + '\r\n'
     + '"x\rx"' + ',"x"' * 13 + '\r\n'),
    # Segnalata da CodeRabbit: filtrare tutte le righe vuote le accettava.
    ('riga vuota in mezzo', '\ufeff' + INTESTAZIONE + '\r\n\r\n'
     + ','.join('"x"' for _ in range(14)) + '\r\n'),
    ('riga vuota alla fine', '\ufeff' + INTESTAZIONE + '\r\n\r\n'),
])
def test_verify_csv_respinge(nome, testo):
    with pytest.raises(ValueError):
        main.verify_csv(testo)


def test_una_virgola_dentro_un_campo_sopravvive():
    """`Over/Under 1,5 gol` ha una virgola: e- il motivo per cui QUOTE_ALL non e- estetica."""
    testo = main.verify_csv(main.make_csv(RIGA_VALIDA))
    assert '"Over/Under 1,5 gol"' in testo
    corpo = testo.lstrip('\ufeff')
    assert len(corpo.split('\r\n')[1].split('","')) == 14, 'la virgola ha spezzato la riga'


# --------------------------------------------------------------- scrittura

def test_store_signal_rifiuta_un_csv_malformato(tmp_path, monkeypatch):
    """Fail-closed nel punto in cui il dato nasce, non in quello in cui viene servito.

    Cosi- una riga malformata non esiste nemmeno per i 90 secondi del TTL.
    """
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'test.db'))
    c = main.db()
    try:
        # Prima un segnale VALIDO: senza questo il test proverebbe solo che nulla
        # viene inserito in un database vuoto, non che la verifica gira PRIMA
        # della DELETE. Segnalato da CodeRabbit, ed e- la differenza fra
        # «non scrive» e «non distrugge quello che c-era».
        buono = main.make_csv(RIGA_VALIDA)
        main.store_signal(c, buono, 'parser-finto', 'PIERO')

        with pytest.raises(ValueError):
            main.store_signal(c, 'Provider,EventId\r\n', 'parser-finto', 'PIERO')

        righe = c.execute('SELECT csv FROM signals WHERE profile=?', ('PIERO',)).fetchall()
        assert len(righe) == 1, f'attesa una sola riga, trovate {len(righe)}'
        assert righe[0][0] == buono, 'il segnale valido precedente e- stato distrutto'
    finally:
        c.close()


def test_store_signal_accetta_un_csv_valido(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'test.db'))
    c = main.db()
    try:
        main.store_signal(c, main.make_csv(RIGA_VALIDA), 'parser-finto', 'PIERO')
        salvato = c.execute('SELECT csv FROM signals WHERE profile=?', ('PIERO',)).fetchone()[0]
        assert salvato.startswith('\ufeff'), 'il BOM non e- arrivato fino al database'
    finally:
        c.close()


def test_una_riga_vecchia_senza_bom_non_viene_servita(tmp_path, monkeypatch):
    """Il feed non serve cio- che non passa la verifica, nemmeno se e- gia- nel database.

    `store_signal()` verifica cio- che scrive, ma una riga finita nel database da
    una versione precedente e- gia- la- e uscirebbe cosi- com-e-. Segnalato da
    CodeRabbit: la finestra e- breve (90 secondi di TTL) ma esiste, e cade
    esattamente subito dopo un deploy.
    """
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'test.db'))
    c = main.db()
    try:
        # Una riga nel formato PRECEDENTE: valida in tutto tranne il BOM.
        vecchia = main.make_csv(RIGA_VALIDA)[len(main.CSV_BOM):]
        assert not vecchia.startswith('\ufeff')
        c.execute('INSERT INTO signals(csv,parser,profile,expires_at) VALUES (?,?,?,?)',
                  (vecchia, 'parser-finto', 'PIERO', 2 ** 31 - 1))
        c.commit()
    finally:
        c.close()

    risposta = main.profile_csv('PIERO', None)
    corpo = risposta.body.decode('utf-8')
    assert corpo.startswith('\ufeff'), 'il feed ha servito la riga vecchia senza BOM'
    assert 'Juventus' not in corpo, 'il contenuto sospetto e- uscito comunque'
    main.verify_csv(corpo)


def test_il_feed_degradato_lascia_una_traccia(tmp_path, monkeypatch):
    """Degradare a feed vuoto e- giusto; farlo in silenzio no.

    `profile_csv()` non puo- sollevare \u2014 un raise diventerebbe un 500 verso
    XTrader \u2014 quindi serve il feed vuoto quando la riga salvata non passa la
    verifica. Ma il fallback silenzioso ha il difetto opposto, segnalato
    indipendentemente da GPT-5.5 e da Fable 5 sulla stessa PR: un bug futuro in
    `verify_csv()` azzererebbe OGNI feed di OGNI cliente, e dall-esterno si
    vedrebbe solo \u00abnessun segnale oggi\u00bb \u2014 indistinguibile da un sabato senza
    partite. Il contatore e- la differenza fra un guasto visibile e un guasto
    che si scopre dal cliente che non punta piu-.
    """
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'test.db'))
    monkeypatch.setattr(main, '_SCARTI_CONSEGNA', main._scarti_azzerati())
    c = main.db()
    try:
        vecchia = main.make_csv(RIGA_VALIDA)[len(main.CSV_BOM):]
        c.execute('INSERT INTO signals(csv,parser,profile,expires_at) VALUES (?,?,?,?)',
                  (vecchia, 'parser-finto', 'PIERO', 2 ** 31 - 1))
        c.commit()
    finally:
        c.close()

    assert main._SCARTI_CONSEGNA['n'] == 0
    main.profile_csv('PIERO', None)

    assert main._SCARTI_CONSEGNA['n'] == 1, 'lo scarto non e- stato contato'
    assert 'BOM' in main._SCARTI_CONSEGNA['ultimo'], \
        f'il motivo dello scarto non e- stato registrato: {main._SCARTI_CONSEGNA["ultimo"]!r}'

    # E la traccia deve essere DOVE SI GUARDA, altrimenti e- la lezione del
    # Bridge da capo: un contatore che nessun pannello legge non e- un contatore.
    salute = main.health()
    assert salute['feed_scartati'] == 1, f'/health non riporta lo scarto: {salute}'
    assert salute['ultimo_scarto'], f'/health non riporta il motivo: {salute}'

    # E `status` deve restare `ok`: lo scarto atteso e- quello della riga scritta
    # dalla versione precedente, subito dopo un deploy, e si risolve da se- col
    # TTL. Farlo diventare `degraded` terrebbe il processo «malato» per tutta la
    # sua vita dopo ogni deploy normale — un allarme sempre acceso, che e- il modo
    # piu- rapido per insegnare a ignorarlo. Il segnale utile e- il RITMO del
    # contatore, non il fatto che sia diverso da zero.
    assert salute['status'] == 'ok', (
        f'uno scarto atteso e autorisolvente non deve marcare il processo come '
        f'degradato: {salute}'
    )
    assert salute['csv'] == 'ok', 'il formato prodotto da questo processo e- valido'


def test_la_stessa_riga_guasta_conta_UNO_non_uno_per_richiesta(tmp_path, monkeypatch):
    """Segnalato da GPT-5.5 su c90eb63, ed e- un difetto della semantica.

    XTrader interroga il feed a raffica e la risposta e- `no-store`, quindi una
    sola riga vecchia resta guasta per tutti i 90 secondi del TTL e verrebbe
    contata a ogni richiesta: decine di «scarti» per un unico evento benigno.

    Cio- distruggerebbe esattamente la lettura che il contatore promette. Il
    criterio dichiarato e- «un contatore che sale in fretta e- il bug che azzera i
    feed»: se la riga benigna del post-deploy produce da sola venti scarti, quel
    criterio indica un guasto dove non c-e-. Il contatore conta quindi le RIGHE
    guaste distinte, non le richieste che le incontrano.
    """
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'test.db'))
    monkeypatch.setattr(main, '_SCARTI_CONSEGNA', main._scarti_azzerati())
    c = main.db()
    try:
        vecchia = main.make_csv(RIGA_VALIDA)[len(main.CSV_BOM):]
        c.execute('INSERT INTO signals(csv,parser,profile,expires_at) VALUES (?,?,?,?)',
                  (vecchia, 'parser-finto', 'PIERO', 2 ** 31 - 1))
        c.commit()
    finally:
        c.close()

    for _ in range(5):
        risposta = main.profile_csv('PIERO', None)
        assert risposta.body.decode('utf-8').startswith('\ufeff')

    assert main._SCARTI_CONSEGNA['n'] == 1, (
        f'cinque richieste sulla STESSA riga guasta hanno prodotto '
        f'{main._SCARTI_CONSEGNA["n"]} scarti: il contatore misura le richieste '
        f'invece delle righe, e un singolo segnale vecchio sembrerebbe un guasto'
    )

    # Una riga guasta DIVERSA invece e- un evento nuovo e va contata.
    c = main.db()
    try:
        c.execute('DELETE FROM signals WHERE profile=?', ('PIERO',))
        altra = main.CSV_BOM + INTESTAZIONE + '\r\n' + ','.join('"z"' for _ in range(13)) + '\r\n'
        c.execute('INSERT INTO signals(csv,parser,profile,expires_at) VALUES (?,?,?,?)',
                  (altra, 'parser-finto', 'PIERO', 2 ** 31 - 1))
        c.commit()
    finally:
        c.close()

    main.profile_csv('PIERO', None)
    assert main._SCARTI_CONSEGNA['n'] == 2, (
        'una riga guasta diversa e- un evento nuovo: se non viene contata, il bug '
        'che azzera i feed resterebbe invisibile'
    )


def test_l_impronta_non_conserva_il_contenuto_del_segnale(tmp_path, monkeypatch):
    """La deduplica ha bisogno di riconoscere la riga, non di conservarla.

    Il criterio e- un digest: due righe diverse si distinguono, ma dallo stato in
    memoria non si risale al segnale. Conservare il CSV per confronto avrebbe
    messo il contenuto di un cliente in una variabile globale del processo.
    """
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'test.db'))
    monkeypatch.setattr(main, '_SCARTI_CONSEGNA', main._scarti_azzerati())
    c = main.db()
    try:
        sporca = main.CSV_BOM + INTESTAZIONE + '\r\n"XTrader","Juventus Segreta"\r\n'
        c.execute('INSERT INTO signals(csv,parser,profile,expires_at) VALUES (?,?,?,?)',
                  (sporca, 'parser-finto', 'PIERO', 2 ** 31 - 1))
        c.commit()
    finally:
        c.close()

    main.profile_csv('PIERO', None)
    stato = str(main._SCARTI_CONSEGNA)
    assert 'Juventus' not in stato, f'il contenuto del segnale e- rimasto in memoria: {stato}'
    assert 'Segreta' not in stato, f'il contenuto del segnale e- rimasto in memoria: {stato}'


def test_il_motivo_dello_scarto_non_contiene_il_segnale(tmp_path, monkeypatch):
    """`/health` e- pubblico: il motivo puo- dire COSA e- rotto, non cosa diceva.

    I messaggi di `verify_csv()` sono strutturali di proposito \u2014 conteggi,
    posizioni, numeri di riga \u2014 e questo test e- il guardiano di quella
    proprieta-: chi domani aggiungesse il valore del campo al messaggio d-errore
    esporrebbe il segnale di un cliente su un endpoint senza token.
    """
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'test.db'))
    monkeypatch.setattr(main, '_SCARTI_CONSEGNA', main._scarti_azzerati())
    c = main.db()
    try:
        # Una riga che contiene un nome squadra riconoscibile E che e- invalida.
        sporca = main.CSV_BOM + INTESTAZIONE + '\r\n"XTrader","Juventus Segreta"\r\n'
        c.execute('INSERT INTO signals(csv,parser,profile,expires_at) VALUES (?,?,?,?)',
                  (sporca, 'parser-finto', 'PIERO', 2 ** 31 - 1))
        c.commit()
    finally:
        c.close()

    main.profile_csv('PIERO', None)
    motivo = main._SCARTI_CONSEGNA['ultimo']
    assert motivo, 'nessun motivo registrato'
    assert 'Juventus' not in motivo, f'il motivo espone il contenuto del segnale: {motivo!r}'
    assert 'Segreta' not in motivo, f'il motivo espone il contenuto del segnale: {motivo!r}'
    assert 'Juventus' not in str(main.health()), 'il segnale e- uscito da /health'


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
        # Ambiente ripulito, non ereditato: con TELEGRAM_BOT_TOKEN in giro
        # l'avvio del relay ripunterebbe il webhook di produzione, e con
        # CSV_ACCESS_TOKEN le richieste senza token qui sotto darebbero 401.
        cwd=RADICE, env=ambiente_di_servizio(DB_PATH=str(db)),
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
    assert dati['csv'].startswith('\ufeff'), 'il CSV nel JSON non ha il BOM'
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
