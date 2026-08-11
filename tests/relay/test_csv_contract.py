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

import asyncio
import os
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
from tests.ambiente import CHIAVI_PERICOLOSE, TOKEN_DI_PROVA  # noqa: E402
from tests.servizio import relay_avviato  # noqa: E402


# Intestazione attesa, costruita dalle colonne reali e non ricopiata a mano:
# una copia a mano si allineerebbe da sola a un ordine sbagliato.
INTESTAZIONE = ','.join('"%s"' % c for c in main.HEADERS)

RIGA_VALIDA = ['XTrader', '', 'Juventus - Palermo', '', 'Over/Under 1,5 gol',
               'OVER_UNDER_15', '', 'Over 1,5 goal', '0', '', '', '', 'PUNTA', '']


@pytest.fixture(autouse=True)
def _token_noto(monkeypatch):
    """Impone un token NOTO per le chiamate IN PROCESSO.

    La whitelist di `tests.ambiente` protegge il sottoprocesso uvicorn, ma non
    puo' fare niente per `import main`: `main.TOKEN` viene letto da `os.environ`
    al momento dell'import, quindi su una macchina dove quella variabile e'
    impostata i test che chiamano `profile_csv()` direttamente riceverebbero un
    401, e la suite passerebbe o fallirebbe a seconda di chi la esegue. Misurato:
    senza questa fixture, `CSV_ACCESS_TOKEN=... pytest tests/relay` fallisce.

    Fino al fail-closed di `auth()` questa fixture AZZERAVA il token, e quella era
    la scelta sbagliata per un motivo che allora non si vedeva: azzerandolo,
    l'intera suite girava con l'autenticazione spenta. Misurato: sostituendo tutte
    e otto le chiamate ad `auth()` con `pass` — cioe' togliendo la serratura da
    dieci rotte — si otteneva **144 passed**. La suite non poteva accorgersi
    dell'assenza di cio' che non esercitava mai.

    Ora il token c'e' e vale `TOKEN_DI_PROVA`, che sta in `tests/ambiente.py`
    insieme alla whitelist: le due cose rispondono alla stessa domanda — quale
    ambiente i test danno al servizio — e separarle le farebbe divergere.

    Autouse di proposito: e' la classe del difetto, non i siti di oggi. Un test
    che voglia verificare il rifiuto imposta `main.TOKEN` da se', e il suo
    monkeypatch vince perche' arriva dopo.

    E non basta `main.TOKEN`: segnalato da Fugu Ultra nella review finale.
    L'handler di startup legge `os.environ` DIRETTAMENTE, non le costanti del
    modulo, quindi qualunque test che un domani faccia partire l'app in processo
    (`TestClient`, un lifespan manager, o una chiamata diretta alla coroutine)
    chiamerebbe `setWebhook` verso il `PUBLIC_URL` di produzione con il token
    vero. Le variabili pericolose vengono quindi rimosse dall'ambiente di questo
    processo, con lo stesso elenco che protegge i sottoprocessi: una seconda
    lista divergerebbe.
    """
    monkeypatch.setattr(main, 'TOKEN', TOKEN_DI_PROVA)
    for chiave in CHIAVI_PERICOLOSE:
        monkeypatch.delenv(chiave, raising=False)


def _feed(profilo=main.PIERO_PROFILE):
    """Il feed servito, col token di prova.

    Esiste perche' `auth()` e' fail-closed: `profile_csv(profilo, None)` ora e' un
    401, e questi test parlano del CONTRATTO CSV, non dell'autenticazione — quella
    ha il suo file. Il token compare in un posto solo: se cambia, cambia qui.
    """
    return main.profile_csv(profilo, TOKEN_DI_PROVA)


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

    risposta = _feed()
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
    _feed()

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
        risposta = _feed()
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

    _feed()
    assert main._SCARTI_CONSEGNA['n'] == 2, (
        'una riga guasta diversa e- un evento nuovo: se non viene contata, il bug '
        'che azzera i feed resterebbe invisibile'
    )


def test_due_profili_guasti_non_fanno_salire_il_contatore_a_raffica(tmp_path, monkeypatch):
    """Bloccante di Fable 5, confermato da GPT-5.5: la deduplica era GLOBALE.

    Con una sola impronta per tutto il processo, due clienti che hanno ciascuno
    una riga guasta — lo scenario normale del post-deploy su un'istanza con piu-
    profili — bastano a rompere tutto: XTrader interroga i due feed alternandoli,
    l-impronta cambia a ogni richiesta perche- e- quella dell-ALTRO profilo, e il
    contatore sale a raffica. Cioe- esattamente il falso positivo che la deduplica
    doveva eliminare, ricomparso dalla porta di servizio in scenario multiutente.

    La chiave e- quindi la coppia profilo+riga: due profili guasti valgono due, e
    restano due per quante volte li si interroghi.
    """
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'test.db'))
    monkeypatch.setattr(main, '_SCARTI_CONSEGNA', main._scarti_azzerati())
    vecchia = main.make_csv(RIGA_VALIDA)[len(main.CSV_BOM):]

    c = main.db()
    try:
        c.execute('INSERT OR IGNORE INTO profiles(name,chat_ids,parser) VALUES (?,?,?)',
                  ('SECONDO', '', main.DEFAULT_PARSER))
        for profilo in ('PIERO', 'SECONDO'):
            c.execute('INSERT INTO signals(csv,parser,profile,expires_at) VALUES (?,?,?,?)',
                      (vecchia, 'parser-finto', profilo, 2 ** 31 - 1))
        c.commit()
    finally:
        c.close()

    # XTrader alterna i due feed, come fa in produzione.
    for _ in range(6):
        for profilo in ('PIERO', 'SECONDO'):
            _feed(profilo)

    assert main._SCARTI_CONSEGNA['n'] == 2, (
        f'due profili con una riga guasta IDENTICA hanno prodotto '
        f'{main._SCARTI_CONSEGNA["n"]} scarti invece di 2: con una sola impronta '
        f'globale il secondo profilo risulta «gia- visto», e un guasto che colpisce '
        f'due clienti si legge come se ne avesse colpito uno'
    )


def test_due_profili_con_righe_guaste_DIVERSE_non_salgono_a_raffica(tmp_path, monkeypatch):
    """L'altra faccia dello stesso bloccante, ed e- quella che Fable 5 descrive.

    Con righe guaste DIVERSE su due profili, l-impronta globale cambiava a ogni
    richiesta perche- era quella dell-altro profilo: ogni singola hit contava, e il
    contatore saliva a raffica. Due difetti opposti — sottostima con righe uguali,
    sovrastima con righe diverse — dalla stessa causa: la chiave senza il profilo.
    """
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'test.db'))
    monkeypatch.setattr(main, '_SCARTI_CONSEGNA', main._scarti_azzerati())

    c = main.db()
    try:
        c.execute('INSERT OR IGNORE INTO profiles(name,chat_ids,parser) VALUES (?,?,?)',
                  ('SECONDO', '', main.DEFAULT_PARSER))
        # Due righe guaste diverse: una senza BOM, una con troppi pochi campi.
        guaste = {
            'PIERO': main.make_csv(RIGA_VALIDA)[len(main.CSV_BOM):],
            'SECONDO': main.CSV_BOM + INTESTAZIONE + '\r\n' + ','.join('"z"' for _ in range(13)) + '\r\n',
        }
        for profilo, riga in guaste.items():
            c.execute('INSERT INTO signals(csv,parser,profile,expires_at) VALUES (?,?,?,?)',
                      (riga, 'parser-finto', profilo, 2 ** 31 - 1))
        c.commit()
    finally:
        c.close()

    for _ in range(6):
        for profilo in ('PIERO', 'SECONDO'):
            _feed(profilo)

    assert main._SCARTI_CONSEGNA['n'] == 2, (
        f'dodici richieste alternate su due profili con righe guaste diverse hanno '
        f'prodotto {main._SCARTI_CONSEGNA["n"]} scarti invece di 2: il contatore sale '
        f'a raffica ed e- il falso positivo che la deduplica doveva eliminare'
    )


def test_richieste_CONCORRENTI_sullo_stesso_feed_contano_una_volta(tmp_path, monkeypatch):
    """Il lock esercitato davvero, non solo dichiarato.

    Chiesto da GPT-5.5, e CLAUDE.md elenca «richieste concorrenti sullo stesso
    parser» fra gli scenari di resilienza obbligatori. Gli handler di FastAPI sono
    sincroni, quindi girano nel threadpool: senza lock la lettura dell-impronta e
    l-incremento sono una race.

    Questo test copre il percorso COMPLETO e prova che sotto 60 richieste
    concorrenti nessuna solleva, nessun thread si blocca e la deduplica regge.
    **Non** prova il lock, e va detto invece di lasciarlo credere: misurato, con il
    lock sostituito da un `nullcontext` questo test resta verde 8 volte su 8,
    perche- l-apertura del database precede la sezione critica e serializza i
    thread molto prima che possano incrociarsi. Il lock e- esercitato dal test
    accanto, che chiama `_registra_scarto()` direttamente.
    """
    import threading

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

    errori = []
    pronti = threading.Barrier(12)

    def tempesta():
        try:
            pronti.wait(timeout=10)  # tutti insieme, per massimizzare la race
            for _ in range(5):
                risposta = _feed()
                assert risposta.body.decode('utf-8').startswith('\ufeff')
        except Exception as exc:  # noqa: BLE001 - va riportato, non ingoiato
            errori.append(exc)

    fili = [threading.Thread(target=tempesta) for _ in range(12)]
    for f in fili:
        f.start()
    for f in fili:
        f.join(timeout=30)

    assert not errori, f'il percorso di consegna ha sollevato sotto concorrenza: {errori}'
    assert all(not f.is_alive() for f in fili), 'un thread e- rimasto bloccato: sospetto deadlock'
    assert main._SCARTI_CONSEGNA['n'] == 1, (
        f'60 richieste concorrenti sulla stessa riga guasta hanno prodotto '
        f'{main._SCARTI_CONSEGNA["n"]} scarti invece di 1'
    )


def test_il_lock_protegge_davvero_l_incremento(monkeypatch):
    """Il lock, esercitato sulla sezione critica e non attraverso il database.

    Sul percorso completo la race non e- riproducibile: l-apertura del database
    serializza i thread prima che arrivino qui, e un test di concorrenza su
    `profile_csv()` resta verde anche senza lock (misurato, 8 su 8). Chiamando
    `_registra_scarto()` direttamente la sezione critica e- raggiungibile, e la
    finestra fra la lettura dell-impronta e l-incremento si allarga sostituendo il
    dizionario con uno che dorme dentro `get`.

    Col lock i thread si serializzano: il primo registra, gli altri trovano
    l-impronta gia- scritta, il totale resta 1. Senza lock leggono tutti `None`
    insieme e incrementano tutti. Verificato in entrambe le direzioni.
    """
    import threading

    class ImpronteLente(dict):
        """Allarga la finestra fra lettura e scrittura dell'impronta.

        L'ordine conta e la prima versione l'aveva sbagliato: dormendo PRIMA di
        leggere, i thread in ritardo trovavano il valore che il primo aveva appena
        scritto e il test restava verde anche senza lock. Il valore va catturato
        per primo, poi si dorme: cosi- tutti tornano con lo stesso `None` che
        avevano letto, che e- esattamente la race da riprodurre.
        """

        def get(self, chiave, default=None):
            valore = super().get(chiave, default)
            time.sleep(0.02)
            return valore

    stato = main._scarti_azzerati()
    stato['impronte'] = ImpronteLente()
    monkeypatch.setattr(main, '_SCARTI_CONSEGNA', stato)

    guasto = main.make_csv(RIGA_VALIDA)[len(main.CSV_BOM):]
    pronti = threading.Barrier(10)
    errori = []

    def registra():
        try:
            pronti.wait(timeout=10)
            main._registra_scarto('PIERO', guasto, 'motivo finto')
        except Exception as exc:  # noqa: BLE001 - va riportato, non ingoiato
            errori.append(exc)

    fili = [threading.Thread(target=registra) for _ in range(10)]
    for f in fili:
        f.start()
    for f in fili:
        f.join(timeout=30)

    assert not errori, f'_registra_scarto ha sollevato sotto concorrenza: {errori}'
    assert all(not f.is_alive() for f in fili), 'un thread e- rimasto bloccato: sospetto deadlock'
    assert main._SCARTI_CONSEGNA['n'] == 1, (
        f'dieci thread sulla stessa riga guasta hanno prodotto '
        f'{main._SCARTI_CONSEGNA["n"]} scarti invece di 1: la sezione critica non e- '
        f'protetta e la lettura dell-impronta corre con l-incremento'
    )


def test_registra_scarto_distingue_i_profili(monkeypatch):
    """La chiave e- la coppia profilo+riga, verificata sulla funzione stessa.

    Il test sul percorso completo lo prova end-to-end; questo lo fissa sull-unita-,
    cosi- un fallimento dice subito se il difetto e- nella chiave o nel giro attorno.
    """
    monkeypatch.setattr(main, '_SCARTI_CONSEGNA', main._scarti_azzerati())
    guasto = main.make_csv(RIGA_VALIDA)[len(main.CSV_BOM):]

    assert main._registra_scarto('PIERO', guasto, 'x') is True, 'la prima riga e- nuova'
    assert main._registra_scarto('PIERO', guasto, 'x') is False, 'la stessa riga non e- nuova'
    # Stessa riga, profilo diverso: e- un secondo cliente colpito, va contato.
    assert main._registra_scarto('SECONDO', guasto, 'x') is True, \
        'la stessa riga su un altro profilo e- un altro cliente colpito'
    assert main._SCARTI_CONSEGNA['n'] == 2
    assert set(main._SCARTI_CONSEGNA['impronte']) == {'PIERO', 'SECONDO'}


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

    _feed()
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

    _feed()
    motivo = main._SCARTI_CONSEGNA['ultimo']
    assert motivo, 'nessun motivo registrato'
    assert 'Juventus' not in motivo, f'il motivo espone il contenuto del segnale: {motivo!r}'
    assert 'Segreta' not in motivo, f'il motivo espone il contenuto del segnale: {motivo!r}'
    assert 'Juventus' not in str(main.health()), 'il segnale e- uscito da /health'


def test_lo_startup_in_processo_non_puo_chiamare_telegram(monkeypatch):
    """Bloccante di Fugu Ultra: la fixture deve coprire anche `os.environ`.

    `register_telegram_webhook()` legge `os.environ` direttamente, non
    `main.TOKEN`: neutralizzare la costante del modulo non basta. Se un test fa
    partire l-app in processo con un `TELEGRAM_BOT_TOKEN` vero nell-ambiente,
    `setWebhook` parte verso il `PUBLIC_URL` di produzione e ripunta il bot reale
    — e non fallisce niente, perche- l-handler ingoia ogni eccezione.

    Qui la coroutine di startup viene chiamata davvero, con `urlopen` sostituito
    da una spia: se qualcosa tentasse la rete, il test lo direbbe.
    """
    chiamate = []

    def spia(url, *a, **k):
        chiamate.append(url)
        raise AssertionError('nessuna chiamata di rete attesa dai test')

    # Via monkeypatch e non con un salvataggio a mano: segnalato da GPT-5.5.
    # Sostituire `urlopen` sul modulo e ripristinarlo nel `finally` funziona in
    # sequenza, ma resta una mutazione globale che con i test in parallelo
    # toccherebbe anche gli altri. monkeypatch la annulla per test.
    monkeypatch.setattr(urllib.request, 'urlopen', spia)
    asyncio.run(main.register_telegram_webhook())

    assert not chiamate, f'lo startup ha tentato una chiamata di rete: {chiamate}'
    # E la ragione per cui non l'ha tentata: l'ambiente e' stato ripulito.
    for chiave in CHIAVI_PERICOLOSE:
        assert not os.getenv(chiave), \
            f'{chiave} e- ancora nell-ambiente del processo di test'


def test_l_handler_di_startup_INGOIA_gli_errori(monkeypatch):
    """Il presupposto del test sopra, verificato invece che assunto.

    L'handler cattura ogni eccezione: e- per questo che un webhook ripuntato per
    sbaglio non fa fallire nulla, ed e- per questo che la spia del test sopra deve
    guardare le CHIAMATE e non aspettarsi un errore. Se un domani l-handler
    smettesse di ingoiare, quel test andrebbe riscritto e questo lo segnala.
    """
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', '000000:FINTO-NON-ESISTE')
    monkeypatch.setenv('PUBLIC_URL', 'https://esempio-non-esiste.invalid')
    tentativi = []

    def esplode(url, *a, **k):
        tentativi.append(url)
        raise OSError('rete non disponibile nei test')

    monkeypatch.setattr(urllib.request, 'urlopen', esplode)
    asyncio.run(main.register_telegram_webhook())  # non deve sollevare

    assert tentativi, 'con un token nell-ambiente lo startup DEVE tentare la chiamata'
    assert 'setWebhook' in tentativi[0]
    # Il token non deve comparire in un posto diverso dall'URL di Telegram.
    assert tentativi[0].startswith('https://api.telegram.org/bot')


# ------------------------------------------------------------------- HTTP

@pytest.fixture(scope='module')
def servizio(tmp_path_factory):
    """Il servizio vero su una porta libera: i byte contano, non le stringhe.

    L'avvio passa da `tests.servizio`, fonte unica delle tre fixture che tirano su
    il relay: prima erano tre copie, e portavano tutte lo stesso difetto (la pipe
    di stdout mai letta, che appende i test invece di farli fallire).

    Il token di prova si passa DI PROPOSITO — `auth()` e- fail-closed, quindi un
    servizio senza token risponde 503 su ogni rotta protetta e questi test non
    potrebbero nemmeno leggere il feed. Quello del proprietario resta fuori: lo
    toglie la whitelist, questo lo mettiamo noi e vale solo qui.
    """
    with relay_avviato(tmp_path_factory.mktemp('relay'),
                       CSV_ACCESS_TOKEN=TOKEN_DI_PROVA) as base:
        yield base


def _get(base, path):
    """GET col token di prova, aggiunto qui invece che in ogni chiamata.

    Da quando `auth()` e' fail-closed le rotte del feed pretendono il token: senza,
    ogni asserzione sui byte fallirebbe con un 401 e il messaggio parlerebbe di
    autenticazione in un file che verifica il contratto CSV. `/health` e `/` lo
    ignorano — un parametro di query in piu' non li disturba — quindi qui non serve
    distinguere.
    """
    separatore = '&' if '?' in path else '?'
    url = f'{base}{path}{separatore}token={TOKEN_DI_PROVA}'
    with urllib.request.urlopen(url, timeout=10) as r:
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
        headers={'Content-Type': 'application/json', 'X-Admin-Token': TOKEN_DI_PROVA}, method='POST',
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
        headers={'Content-Type': 'application/json', 'X-Admin-Token': TOKEN_DI_PROVA},
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
