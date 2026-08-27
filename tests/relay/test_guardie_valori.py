"""Guardie sui valori estratti e sulla config (#39 + #41, PR 5 della sequenza #2).

Fino a questo PR in `main.py` non esisteva **nessun** `float()`: qualunque cosa la
regola estraesse per `Price`, `Handicap`, `Points`, `MinPrice`, `MaxPrice` finiva
verbatim nel CSV servito a XTrader. `verify_csv()` controlla il **formato** (14
colonne, virgolette, CRLF, BOM), non il **senso** dei valori.

Il caso reale da cui nasce la #39 e' il **separatore delle migliaia letto come
decimale**: un canale che scrive `1.000` produce oggi una riga formalmente
perfetta con dentro un numero assurdo, e nessuno solleva niente. Su `Points` —
che e' il **moltiplicatore dello stake** di XTrader — quel numero non rende la
scommessa impossibile: la moltiplica. E' l'unica delle cinque colonne dove il bug
costa soldi al primo messaggio invece che al primo controllo.

Decisioni del proprietario (commenti della #39), che questi test vincolano:

- **fail-closed**: valore storto → si scarta il **messaggio intero**, non si
  svuota la colonna. Svuotare fabbricherebbe una riga che il messaggio non dice —
  `Price` vuota significa «la quota la mette XTrader», `Handicap` vuoto significa
  una linea diversa, `Points` vuoto significa 1×;
- **vuoto resta legale**: e' il caso normale di `Price`, ed e' il test che
  protegge dall'eccesso di zelo;
- **tre motivi distinti**, mai confusi: non-ASCII, non numerico, fuori intervallo.
  Un controllo che scatta col motivo sbagliato e' peggio di nessun controllo,
  perche' manda l'utente sulla pista sbagliata (difetto registrato del Bridge);
- **tetti**: `Price`/`MinPrice`/`MaxPrice` `1.01–1000` (scala reale Betfair),
  `|Handicap| <= 1000`, `Points` `0–1000`.

E dalla #41, due guardie che il Bridge ha e noi no:

- una **chiave di colonna inventata** veniva accettata e poi ignorata in silenzio;
- un parser di **sole costanti** scriveva la stessa scommessa per qualunque
  messaggio riconosciuto.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE))

import main  # noqa: E402 - dopo l'inserimento del percorso
from tests.ambiente import CHIAVI_PERICOLOSE, TOKEN_DI_PROVA  # noqa: E402


@pytest.fixture(autouse=True)
def _ambiente_pulito(monkeypatch):
    for chiave in CHIAVI_PERICOLOSE:
        monkeypatch.delenv(chiave, raising=False)
    monkeypatch.setattr(main, 'TOKEN', TOKEN_DI_PROVA)


MESSAGGIO = 'P.Bet. Juventus - Palermo'


def _config(**colonne):
    """Un parser che ESTRAE l'evento dal messaggio, piu' le colonne date.

    L'estrazione vera serve: con tutte costanti scatterebbe il gate di contenuto
    della #41 e i test dei numeri misurerebbero l'altra guardia.
    """
    base = {
        'EventName': {'source': 'line', 'contains': 'P.Bet.'},
        'MarketType': {'source': 'constant', 'value': 'OVER_UNDER_15'},
        'SelectionName': {'source': 'constant', 'value': 'Over 1,5 goal'},
        'BetType': {'source': 'constant', 'value': 'PUNTA'},
    }
    base.update({c: {'source': 'constant', 'value': v} for c, v in colonne.items()})
    return {'match': {'type': 'contains', 'value': 'P.Bet.'}, 'columns': base}


def _esegui(**colonne):
    return main.esegui_parser(MESSAGGIO, _config(**colonne))


def _motivi(risultato):
    return ' | '.join(risultato.get('scarti') or [])


# ------------------------------------------------- i cinque casi numerici (#39)

def test_una_quota_col_separatore_delle_MIGLIAIA_scarta_il_messaggio():
    """Caso 1 della #39: oggi `1.000.000` finisce verbatim nel CSV.

    Il motivo deve nominare il **separatore**, non mandare a controllare la
    regola in generale: e' l'unica pista che porta alla causa vera.
    """
    r = _esegui(Price='1.000.000')
    assert r['complete'] is False, 'una quota da un milione ha raggiunto il feed'
    assert 'separatore' in _motivi(r).lower(), (
        f'il motivo non nomina il separatore: {_motivi(r)!r}')


def test_una_quota_VUOTA_continua_a_passare():
    """Caso 2 della #39, il piu' importante: e' la protezione dall'eccesso di zelo.

    `Price` vuota e' il caso NORMALE — la quota la mette XTrader dal proprio book
    (documentato in `web/engine.js`). Una guardia troppo severa romperebbe il
    comportamento di tutti i giorni per chiudere un caso di bordo.
    """
    r = _esegui(Price='')
    assert r['complete'] is True, f'una quota vuota e\' stata scartata: {_motivi(r)}'


def test_una_quota_SOTTO_il_minimo_dice_fuori_intervallo_non_non_numerico():
    """Caso 3: `0.5` e' un numero, ed e' fuori dalla scala delle quote.

    Il motivo deve dirlo: nel Bridge «fuori intervallo» veniva annunciato come
    «non numerico» e mandava l'utente su due piste entrambe sbagliate.
    """
    r = _esegui(Price='0.5')
    assert r['complete'] is False
    motivo = _motivi(r)
    assert 'intervallo' in motivo, f'motivo sbagliato: {motivo!r}'
    assert 'non e\' un numero.' not in motivo, (
        f'un numero valido fuori scala e\' annunciato come non numerico: {motivo!r}')


def test_un_valore_NON_numerico_lo_dice_ed_e_un_caso_distinto():
    """Caso 4: `abc` non e' un numero, e il motivo e' un ALTRO."""
    r = _esegui(Price='abc')
    assert r['complete'] is False
    motivo = _motivi(r)
    assert 'non e\'' in motivo and 'numero' in motivo, motivo
    assert 'intervallo' not in motivo, (
        f'un valore non numerico e\' annunciato come fuori intervallo: {motivo!r}')


def test_l_handicap_patologico_scarta_ma_zero_e_meno_uno_e_mezzo_passano():
    """Caso 5: il tetto intercetta l'assurdo e non tocca le linee reali.

    `0` e' un valore, non un'assenza — XTrader lo scrive esplicitamente nel suo
    CSV — e `-1.5` e' una linea normalissima.
    """
    assert _esegui(Handicap='999999')['complete'] is False
    for buono in ('0', '-1.5', '2.5'):
        r = _esegui(Handicap=buono)
        assert r['complete'] is True, f'handicap {buono} scartato: {_motivi(r)}'


def test_il_moltiplicatore_dello_stake_col_separatore_scarta_il_messaggio():
    """Caso 7: `Points` e' la colonna piu' pericolosa delle cinque.

    Un valore storto sulle altre rende la scommessa impossibile; qui moltiplica
    lo stake per un milione, la scommessa parte e il danno e' immediato in euro.
    """
    r = _esegui(Points='1.000.000')
    assert r['complete'] is False, 'un moltiplicatore da un milione ha raggiunto il feed'
    assert 'separatore' in _motivi(r).lower(), _motivi(r)
    for buono in ('2', '0.5', '0', ''):
        assert _esegui(Points=buono)['complete'] is True, f'Points {buono!r} scartato'


def test_un_valore_NON_FINITO_e_un_caso_a_se_prima_dei_tetti():
    """Caso 8: `float('9'*400)` e' `inf`, e l'infinito supera i confronti nel verso
    sbagliato — un `Points > 0` senza tetto superiore direbbe «valido» a un
    moltiplicatore infinito. Bug misurato nel Bridge, non ereditato."""
    r = _esegui(Points='9' * 400)
    assert r['complete'] is False
    assert 'finito' in _motivi(r), f'il motivo non dice «non finito»: {_motivi(r)!r}'


def test_le_cifre_NON_ASCII_sono_scartate_anche_se_python_le_leggerebbe():
    """Caso 9: `float('١٩')` in Python da' `19.0`, ma XTrader legge solo ASCII.

    Senza questo controllo il valore passerebbe i tetti e finirebbe verbatim nel
    CSV: la colonna sembra piena e il consumatore non la capisce — il peggiore
    dei due mondi.
    """
    for esotico in ('١٩', '１９'):
        r = _esegui(Price=esotico)
        assert r['complete'] is False, f'{esotico!r} accettato come numero'
        assert 'ASCII' in _motivi(r), _motivi(r)


def test_il_predicato_e_UNO_e_lo_stesso_per_tutti_i_chiamanti():
    """Caso 10: un predicato, un posto.

    Nel Bridge erano due controlli scritti a mano e identici, e aggiungere il
    tetto a uno solo aveva lasciato aperto il percorso multi-riga. Qui il motore
    chiama `motivo_valore_numerico`, e chiamarlo direttamente deve dare lo stesso
    verdetto: e' cio' che rende sicuro riusarlo per le righe di override (#35).
    """
    assert main.motivo_valore_numerico('Price', '1000000') is not None
    assert main.motivo_valore_numerico('Price', '') is None
    assert main.motivo_valore_numerico('EventName', 'qualunque cosa') is None, (
        'il predicato giudica una colonna che non e\' numerica'
    )
    diretto = main.motivo_valore_numerico('Price', '0.5')
    dal_motore = _motivi(_esegui(Price='0.5'))
    assert diretto == dal_motore, (
        f'il motore non usa il predicato: diretto={diretto!r} motore={dal_motore!r}')


# ------------------------------------------- le due guardie della #41

def test_una_colonna_INVENTATA_viene_respinta_con_il_suggerimento():
    """Gap 1: `EventNmae` veniva salvata e poi ignorata dal motore.

    Fail-closed sul feed, ma la DIAGNOSI era falsa: il messaggio diceva «manca
    EventName» e mandava a controllare i delimitatori, mentre la causa e' un
    refuso di due lettere.
    """
    cfg = {'match': {'type': 'contains', 'value': 'x'},
           'columns': {'EventNmae': {'source': 'constant', 'value': 'X'}}}
    with pytest.raises(main.HTTPException) as e:
        main._valida_config_parser(cfg)
    assert e.value.status_code == 422
    assert 'EventNmae' in e.value.detail and 'EventName' in e.value.detail, (
        f'il messaggio non nomina la chiave sbagliata e quella giusta: {e.value.detail!r}')


def test_una_colonna_FACOLTATIVA_col_refuso_e_il_caso_MUTO_e_va_respinta():
    """Gap 1, il caso peggiore: `Prcie` non produce nemmeno una diagnosi falsa.

    La quota resta vuota per sempre, `missing` e' vuota perche' `Price` non e'
    obbligatoria, `complete` e' `True`: il segnale parte senza quota e niente lo
    segnala.
    """
    cfg = {'match': {'type': 'contains', 'value': 'x'},
           'columns': {'Prcie': {'source': 'constant', 'value': '2.5'}}}
    with pytest.raises(main.HTTPException) as e:
        main._valida_config_parser(cfg)
    assert e.value.status_code == 422
    assert 'Price' in e.value.detail, e.value.detail


def test_le_14_colonne_VERE_restano_accettate():
    """La guardia sopra non deve diventare un muro: la config normale passa."""
    cfg = {'match': {'type': 'contains', 'value': 'P.Bet.'},
           'columns': {c: {'source': 'constant', 'value': 'X'} for c in main.HEADERS}}
    main._valida_config_parser(cfg)


def _cfg_flags(flags):
    """Una config valida con una colonna `regex` e i `flags` dati (Issue #86)."""
    regola = {'source': 'regex', 'pattern': '(x)', 'group': 1}
    if flags is not None:
        regola['flags'] = flags
    return {'match': {'type': 'contains', 'value': 'x'},
            'columns': {'EventName': regola,
                        'MarketType': {'source': 'constant', 'value': 'OVER_UNDER_15'},
                        'SelectionName': {'source': 'constant', 'value': 'Over'},
                        'BetType': {'source': 'constant', 'value': 'PUNTA'}}}


@pytest.mark.parametrize('flags', ['x', 'gy', 'g', 'y', 'ix', 5, [{'a': 1}], {'i': 1}, True])
def test_flags_regex_fuori_dal_comune_RIFIUTATI_al_salvataggio(flags):
    """Issue #86: un `flags` fuori da `{i,m,s,u}` o non-stringa → 422 al salvataggio.

    Prima venivano ACCETTATI e degradavano a runtime (case-sensitive per `x`/`y`,
    fail-closed per i non-stringa, #85): un parser poteva nascere con un flag che
    l'anteprima e il feed trattavano in modo diverso da come l'utente credeva. Ora
    il confine di scrittura li rifiuta con un motivo chiaro, cosi' nessun NUOVO
    parser puo' averli. Fail-first: sul codice vecchio `_valida_config_parser` non
    sollevava (misurato: `flags:'x'` accettato).
    """
    with pytest.raises(main.HTTPException) as e:
        main._valida_config_parser(_cfg_flags(flags))
    assert e.value.status_code == 422
    assert 'flag regex' in e.value.detail and 'EventName' in e.value.detail, e.value.detail


@pytest.mark.parametrize('flags', [None, '', 'i', 'ms', 'ims', 'u', 'iu', 'imsu',
                                   'ii', 'uu', 'imsi'])
def test_flags_regex_del_comune_ACCETTATI_al_salvataggio(flags):
    """Il rovescio: i flag onorati da entrambi i motori passano, e l'assenza pure.

    Senza questo caso un `return 422` fisso passerebbe il test sopra e murerebbe
    ogni parser con una regola regex.

    I DUPLICATI (`ii`, `uu`, `imsi`) sono ACCETTATI di proposito: `flagRegex` in JS
    deduplica col Set prima di `new RegExp` (che su `'ii'` solleverebbe) e Python li
    combina — i due motori danno lo stesso valore (misurato in engine_cases.mjs).
    GPT-5.6 Sol (PR #87) li temeva divergenti: non lo sono, quindi rifiutarli
    sarebbe una restrizione senza motivo.
    """
    main._valida_config_parser(_cfg_flags(flags))  # non deve sollevare


def test_un_flags_NON_puo_entrare_da_una_riga_multi():
    """Review #87 (Fable): un `flags` esotico non passa da un override `multi`.

    Le righe di `config.multi` accettano solo valori SCALARI (str/int/float/bool),
    non oggetti-regola: una regola `regex` con `flags` in un override e' gia'
    rifiutata da `_valida_config_multi` (valore non scalare). Quindi non esiste un
    percorso `flags` nel multi da validare a parte, e l'invariante «nessun nuovo
    parser con flag fuori da imsu» regge anche li'.
    """
    cfg = {'match': {'type': 'contains', 'value': 'x'},
           'columns': {c: {'source': 'constant', 'value': 'X'}
                       for c in main.COLONNE_OBBLIGATORIE},
           'multi': {'markets': [
               {'selection_name': {'source': 'regex', 'pattern': '(x)', 'flags': 'x'}}]}}
    with pytest.raises(main.HTTPException) as e:
        main._valida_config_parser(cfg)
    assert e.value.status_code == 422
    assert 'selection_name' in e.value.detail, e.value.detail


def test_la_lista_dei_flag_e_FONTE_UNICA():
    """`FLAG_REGEX_COMUNI` e' l'insieme onorato da `_flag_regex`, non una copia.

    La validazione al salvataggio rifiuta i flag fuori da `FLAG_REGEX_COMUNI`; se
    quella lista divergesse da cio' che `_flag_regex` onora davvero, si
    rifiuterebbe un flag valido o si accetterebbe un flag che il motore ignora.
    Qui si pretende che ogni carattere della lista sia effettivamente onorato
    (i/m/s cambiano i bit, `u` e' un no-op riconosciuto) e che nessun altro lo sia.
    """
    R = main._regex
    assert set(main.FLAG_REGEX_COMUNI) == set('imsu')
    # i, m, s attivano il bit corrispondente.
    assert main._flag_regex('i') == R.I
    assert main._flag_regex('m') == R.M
    assert main._flag_regex('s') == R.S
    # u e' riconosciuto (accettato al salvataggio) ma no-op nel motore Python.
    assert main._flag_regex('u') == 0
    # un carattere fuori dalla lista NON e' onorato (case-sensitive) — ed e'
    # esattamente cio' che la validazione rifiuta al salvataggio.
    assert main._flag_regex('x') == 0


def test_un_parser_di_sole_COSTANTI_non_scrive_su_qualunque_messaggio():
    """Gap 2: quattro obbligatorie costanti + condizione larga = riga piazzabile
    per qualsiasi messaggio che contenga la condizione.

    Misurato nella #41: «ciao a tutti» e «oggi partita» davano `complete=True`.
    Il controllo `missing` non protegge, perche' una costante non e' mai vuota.
    """
    cfg = {'match': {'type': 'contains', 'value': 'a'},
           'columns': {c: {'source': 'constant', 'value': 'X'}
                       for c in main.COLONNE_OBBLIGATORIE}}
    for non_segnale in ('ciao a tutti', 'oggi partita'):
        r = main.esegui_parser(non_segnale, cfg)
        assert r['complete'] is False, (
            f'un messaggio che non e\' un segnale ha prodotto una riga: {non_segnale!r}')
        assert 'estratta' in _motivi(r), (
            f'il motivo non spiega la causa vera: {_motivi(r)!r}')


def test_UNA_obbligatoria_estratta_e_le_altre_costanti_continuano_a_funzionare():
    """Gap 2, la protezione dall'eccesso di zelo: e' la configurazione PIU' COMUNE.

    Il parser di produzione estrae l'evento e tiene fisse mercato, selezione e
    tipo di scommessa. Se questa diventasse invalida avremmo chiuso un caso di
    bordo rompendo l'uso normale.
    """
    r = _esegui()
    assert r['complete'] is True, f'la configurazione normale e\' stata scartata: {_motivi(r)}'
    assert r['row'][main.HEADERS.index('EventName')].strip()


def test_il_webhook_non_scrive_il_segnale_scartato(tmp_path, monkeypatch):
    """Il percorso vero: `elabora_messaggio` non deve produrre nessuna riga.

    E' il consumatore di `esegui_parser` (regola 2-bis): guarda `complete`, quindi
    un valore storto non arriva al CSV — ma va misurato, non dedotto.
    """
    import json
    cfg = {'config_json': json.dumps(_config(Points='1.000.000'))}
    assert main.elabora_messaggio(MESSAGGIO, cfg) is None, (
        'il messaggio con moltiplicatore assurdo ha prodotto una riga CSV')
    buono = {'config_json': json.dumps(_config(Points='2'))}
    assert main.elabora_messaggio(MESSAGGIO, buono) is not None, (
        'un moltiplicatore legittimo e\' stato scartato dal percorso del webhook')


def test_il_log_dice_PERCHE_il_messaggio_e_stato_scartato(tmp_path, monkeypatch):
    """Lo stop e' voluto; l'invisibilita' no.

    Segnalato come bloccante da Claude Fable 5 e come rischio da GPT-5.5 sulla PR
    #47: una config gia' salvata con le obbligatorie tutte costanti — o con un
    valore fuori scala — smette di scrivere, e nel log l'utente vedeva soltanto
    `parser_no_match`, cioe' il sintomo senza la causa. E' esattamente il difetto
    che queste due Issue esistono per chiudere: il motivo deve dire COSA FARE.

    Il gate resta a runtime e resta fail-closed: qui si misura che il motivo
    arrivi fino alla riga di `message_logs` che il cliente legge.
    """
    import json
    import sqlite3

    from tests.dati import relay_in_processo
    from tests.relay.test_webhook import BOT_FINTO, CHAT

    percorso = relay_in_processo(monkeypatch, tmp_path / 'motivi.db', chat_ids=CHAT)
    monkeypatch.setattr(main, 'SEGRETO_WEBHOOK', main.webhook_secret(BOT_FINTO))
    c = sqlite3.connect(percorso)
    c.execute('UPDATE parsers SET config_json=? WHERE name=?',
              (json.dumps(_config(Points='1.000.000')), main.DEFAULT_PARSER))
    c.commit()
    c.close()

    import asyncio

    from tests.relay.test_webhook import RichiestaFinta
    payload = {'message': {'chat': {'id': int(CHAT)}, 'text': MESSAGGIO}}
    asyncio.run(main.telegram_webhook(RichiestaFinta(
        {'X-Telegram-Bot-Api-Secret-Token': main.webhook_secret(BOT_FINTO)}, payload)))

    c = sqlite3.connect(percorso)
    esiti = [r[0] for r in c.execute('SELECT esito FROM message_logs').fetchall()]
    c.close()
    assert esiti, 'nessuna riga di log: il messaggio non e\' stato elaborato'
    assert any('separatore' in e.lower() or 'Points' in e for e in esiti), (
        f'il log non dice PERCHE\' il messaggio e\' stato scartato: {esiti}')


def test_la_rotta_di_prova_RESTITUISCE_i_motivi(tmp_path, monkeypatch):
    """`POST /api/me/parsers/{slug}/test` deve dire PERCHE', non solo «non completo».

    Chiesto da CodeRabbit sulla PR #47, ed e' il punto in cui il cliente scopre
    la causa: senza `scarti` la prova mostrerebbe `complete: false` con `missing`
    vuota — il sintomo senza la causa, che e' esattamente il difetto che queste
    due Issue chiudono.
    """
    import asyncio
    import json
    import sqlite3

    from tests.dati import relay_in_processo
    from tests.relay.test_login import BOT_FINTO, SEGRETO_ATTESO, _dati_login

    percorso = relay_in_processo(monkeypatch, tmp_path / 'prova.db')
    monkeypatch.setattr(main, 'BOT_TOKEN', BOT_FINTO)
    monkeypatch.setattr(main, 'SEGRETO_SESSIONE', SEGRETO_ATTESO)
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', '')
    risposta = main.login_telegram(main.LoginTelegramIn(**_dati_login(id='909000909')))
    cookie = None
    for pezzo in (risposta.headers.get('set-cookie') or '').split(';'):
        chiave, _, valore = pezzo.strip().partition('=')
        if chiave == main.NOME_COOKIE:
            cookie = valore
    c = sqlite3.connect(percorso)
    utente = c.execute("SELECT id FROM users WHERE telegram_id='909000909'").fetchone()[0]
    c.execute('INSERT INTO parsers(name, header, user_id, slug, config_json, id)'
              " VALUES ('u-prova','H',?,?,?,9001)",
              (utente, 'prova', json.dumps(_config(Price='1.000.000'))))
    c.commit()
    c.close()

    class Richiesta:
        cookies = {main.NOME_COOKIE: cookie}
        headers = {'content-length': '0'}

        async def stream(self):
            yield json.dumps({'message': MESSAGGIO}).encode()

    corpo = json.loads(bytes(asyncio.run(
        main.prova_parser_mio('prova', Richiesta())).body).decode())
    assert corpo['complete'] is False
    assert corpo.get('scarti'), f'la prova non dice perche\': {corpo}'
    assert 'separatore' in ' '.join(corpo['scarti']).lower(), corpo['scarti']


def test_il_log_dello_scarto_punta_al_parser_GIUSTO(tmp_path, monkeypatch):
    """Il motivo va attribuito a chi l'ha prodotto, non al primo della lista.

    Segnalato da GPT-5.5 sulla PR #47, ed e' un difetto che avevo introdotto io
    correggendo il precedente: con due parser dello stesso utente sulla stessa
    chat, il motivo del SECONDO veniva scritto sotto l'id del PRIMO. Una
    diagnosi che punta al parser sbagliato e' peggio di nessuna diagnosi — manda
    a correggere una regola che non ha nulla che non va, ed e' esattamente la
    classe di difetto che #39 e #41 esistono per chiudere.
    """
    import asyncio
    import json
    import sqlite3

    from tests.dati import relay_in_processo
    from tests.relay.test_webhook import BOT_FINTO, CHAT, RichiestaFinta

    percorso = relay_in_processo(monkeypatch, tmp_path / 'attribuzione.db', chat_ids=CHAT)
    monkeypatch.setattr(main, 'SEGRETO_WEBHOOK', main.webhook_secret(BOT_FINTO))
    c = sqlite3.connect(percorso)
    # Il parser storico non riconosce questo messaggio (nessuno scarto suo), il
    # secondo lo riconosce e viene scartato dalla guardia: il log deve nominare
    # il SECONDO.
    utente = c.execute('SELECT user_id FROM parsers WHERE name=?',
                       (main.DEFAULT_PARSER,)).fetchone()[0]
    c.execute('INSERT INTO parsers(name, header, user_id, slug, ordine, config_json, id)'
              " VALUES ('u-secondo','H',?,?,5,?,9101)",
              (utente, 'secondo', json.dumps(_config(Points='1.000.000'))))
    c.execute('INSERT INTO parser_chats(parser_id, chat_id)'
              ' SELECT 9101, id FROM chats WHERE telegram_chat_id=?', (CHAT,))
    c.commit()
    c.close()

    payload = {'message': {'chat': {'id': int(CHAT)}, 'text': MESSAGGIO}}
    asyncio.run(main.telegram_webhook(RichiestaFinta(
        {'X-Telegram-Bot-Api-Secret-Token': main.webhook_secret(BOT_FINTO)}, payload)))

    c = sqlite3.connect(percorso)
    righe = c.execute('SELECT parser_id, esito FROM message_logs').fetchall()
    c.close()
    scarti = [r for r in righe if 'scartato' in (r[1] or '')]
    assert scarti, f'nessuna riga di scarto: {righe}'
    assert all(r[0] == 9101 for r in scarti), (
        f'il motivo e\' attribuito al parser sbagliato: {scarti}')


def test_lo_scarto_del_PRIMO_parser_resta_attribuito_a_lui(tmp_path, monkeypatch):
    """L'ordine inverso del test precedente: il motivo viene dal PRIMO parser.

    Suggerito da GPT-5.5 sulla PR #47, ed e' la guardia che impedisce alla
    correzione dell'attribuzione di degenerare in «punta sempre all'ultimo».
    """
    import asyncio
    import json
    import sqlite3

    from tests.dati import relay_in_processo
    from tests.relay.test_webhook import BOT_FINTO, CHAT, RichiestaFinta

    percorso = relay_in_processo(monkeypatch, tmp_path / 'primo.db', chat_ids=CHAT)
    monkeypatch.setattr(main, 'SEGRETO_WEBHOOK', main.webhook_secret(BOT_FINTO))
    c = sqlite3.connect(percorso)
    riga = c.execute('SELECT id, user_id FROM parsers WHERE name=?',
                     (main.DEFAULT_PARSER,)).fetchone()
    storico, utente = riga[0], riga[1]
    # Il parser storico riconosce e viene scartato; il secondo non riconosce
    # nemmeno il messaggio (condizione che non combacia), quindi non ha motivi.
    c.execute('UPDATE parsers SET config_json=? WHERE name=?',
              (json.dumps(_config(Points='1.000.000')), main.DEFAULT_PARSER))
    c.execute('INSERT INTO parsers(name, header, user_id, slug, ordine, config_json, id)'
              " VALUES ('u-muto','H',?,?,9,?,9201)",
              (utente, 'muto', json.dumps({
                  'match': {'type': 'contains', 'value': 'NON-COMBACIA-MAI'},
                  'columns': {}})))
    c.execute('INSERT INTO parser_chats(parser_id, chat_id)'
              ' SELECT 9201, id FROM chats WHERE telegram_chat_id=?', (CHAT,))
    c.commit()
    c.close()

    payload = {'message': {'chat': {'id': int(CHAT)}, 'text': MESSAGGIO}}
    asyncio.run(main.telegram_webhook(RichiestaFinta(
        {'X-Telegram-Bot-Api-Secret-Token': main.webhook_secret(BOT_FINTO)}, payload)))

    c = sqlite3.connect(percorso)
    scarti = [r for r in c.execute('SELECT parser_id, esito FROM message_logs').fetchall()
              if 'scartato' in (r[1] or '')]
    c.close()
    assert scarti, 'nessuna riga di scarto'
    assert all(r[0] == storico for r in scarti), (
        f'il motivo del primo parser e\' attribuito a un altro: {scarti}')


# ------------------- il verdetto corre sul valore NORMALIZZATO (gate PR #47)

def test_il_BOM_ai_bordi_non_cambia_il_verdetto():
    """I default di `strip()` e `trim()` divergono anche sul VERDETTO.

    `'\\ufeff2'` era una quota valida nel browser (il `trim` di JS toglie il
    BOM) e «non un numero» in produzione (lo `strip` di Python no): anteprima
    verde, feed vuoto — la divergenza che le guardie esistono per chiudere. Con
    `'\\x1c2'` i ruoli si invertono. Il verdetto deve correre sul valore
    normalizzato dalla classe condivisa, come gia' il citato. [REAL_FINDING] di
    Claude Fable 5 e GPT-5.6 Sol al gate finale della PR #47.
    """
    assert main.motivo_valore_numerico('Price', '\ufeff2') is None, (
        'il BOM ai bordi cambia il verdetto: lo stesso valore in JS passa')
    assert main.motivo_valore_numerico('Price', '\x1c2') is None, (
        'il separatore di controllo ai bordi cambia il verdetto')


def test_un_valore_di_soli_spazi_uniformi_e_VUOTO():
    """`'\\ufeff\\xa0'`: vuoto per il `trim` di JS (ammesso), non-vuoto per lo
    `strip` di Python (scartato). Dopo la normalizzazione, vuoto per entrambi."""
    assert main.motivo_valore_numerico('Price', '\ufeff\xa0') is None


def test_lo_spazio_DENTRO_il_numero_resta_scartato():
    """La normalizzazione perdona i bordi, non il corpo: `'1 5'` non e' un numero."""
    assert main.motivo_valore_numerico('Price', '1\xa05') is not None


def test_un_parser_che_NON_riconosce_non_produce_scarti():
    """`scarti` senza riconoscimento: [REAL_FINDING] di GPT-5.6 Sol (PR #47).

    Un parser la cui condizione NON e' soddisfatta ma con una costante numerica
    invalida produceva scarti per qualunque messaggio della chat: il dispatch li
    scriveva in `message_logs` come «scartato», attribuiti a un parser che non
    c'entrava, conservando testo estraneo. Non riconosciuto = nessun motivo.
    """
    r = main.esegui_parser('oggi si parla di altro', _config(Points='999999'))
    assert r['matched'] is False
    assert r['scarti'] == [], (
        f'un parser che non riconosce ha prodotto scarti: {_motivi(r)}')


def test_nessun_log_da_un_parser_che_non_riconosce(tmp_path, monkeypatch):
    """Il percorso vero del dispatch: messaggio estraneo → NESSUNA riga di log.

    E' la promessa del commento in `_elabora_per_utente`: «i log sono una
    funzione del servizio, non un archivio dei messaggi». Senza il gate sul
    riconoscimento, ogni chiacchiera della chat finiva archiviata come
    «scartato» sotto un parser che non c'entrava.
    """
    import asyncio
    import json
    import sqlite3

    from tests.dati import relay_in_processo
    from tests.relay.test_webhook import BOT_FINTO, CHAT, RichiestaFinta

    percorso = relay_in_processo(monkeypatch, tmp_path / 'estranei.db', chat_ids=CHAT)
    monkeypatch.setattr(main, 'SEGRETO_WEBHOOK', main.webhook_secret(BOT_FINTO))
    c = sqlite3.connect(percorso)
    c.execute('UPDATE parsers SET config_json=? WHERE name=?',
              (json.dumps(_config(Points='999999')), main.DEFAULT_PARSER))
    c.commit()
    c.close()

    payload = {'message': {'chat': {'id': int(CHAT)},
                           'text': 'chiacchiere della chat, nessun segnale'}}
    asyncio.run(main.telegram_webhook(RichiestaFinta(
        {'X-Telegram-Bot-Api-Secret-Token': main.webhook_secret(BOT_FINTO)}, payload)))

    c = sqlite3.connect(percorso)
    righe = c.execute('SELECT parser_id, esito FROM message_logs').fetchall()
    c.close()
    assert not righe, (
        f'un messaggio mai riconosciuto e\' stato archiviato nei log: {righe}')


def test_i_float_piccoli_non_cambiano_notazione():
    """`str()` e `String()` divergono sulle SOGLIE dell'esponenziale.

    Python passa all'esponenziale sotto 1e-4 (`str(0.000001)` da' `'1e-06'`,
    che la regex scarta come non numerico), JavaScript solo sotto 1e-6
    (`'0.000001'`, accettato): un `Points` numerico JSON in quella zona era
    valido nel browser e scartato in produzione. E dove entrambi scrivono
    l'esponenziale, il formato diverge (`'1e-07'` contro `'1e-7'`): stesso
    verdetto, motivo diverso. Il ramo float di `_testo_canonico` segue ora la
    conversione di ECMAScript, misurata caso per caso dall'oracolo.
    [REAL_FINDING] di GPT-5.6 Sol al gate finale della PR #47.
    """
    assert main._testo_canonico(0.000001) == '0.000001'
    assert main._testo_canonico(0.00001) == '0.00001'
    assert main._testo_canonico(1e-7) == '1e-7'
    assert main._testo_canonico(1.5e16) == '15000000000000000'
    assert main._testo_canonico(1e20) == '100000000000000000000'
    assert main._testo_canonico(1e21) == '1e+21'
    assert main._testo_canonico(123.456) == '123.456'
    assert main.motivo_valore_numerico('Points', 0.000001) is None, (
        'un moltiplicatore minuscolo ma legale e\' accettato in JS e scartato qui')


def test_i_valori_non_finiti_seguono_la_conversione_di_JavaScript():
    """`NaN`, `Infinity` e `-0.0`: il testo citato e' quello di `String()`.

    Chiesto da CodeRabbit sulla PR #47: questi rami decidono il testo che
    finisce nei motivi, cioe' la diagnosi che il cliente legge. `json.loads`
    di Python accetta `NaN`/`Infinity` per default, quindi non sono
    irraggiungibili da una config.
    """
    import math
    assert main._numero_stile_js(float('nan')) == 'NaN'
    assert main._numero_stile_js(math.inf) == 'Infinity'
    assert main._numero_stile_js(-math.inf) == '-Infinity'
    assert main._numero_stile_js(-0.0) == '0'


def test_un_intero_JSON_oltre_il_double_diventa_Infinity():
    """`float()` di un intero enorme solleva `OverflowError`; JS legge Infinity.

    JavaScript non ha interi: un numero JSON oltre il massimo double diventa
    `Infinity` gia' nel parse, e `_testo_canonico` deve arrivare allo stesso
    testo — che poi la guardia scarta come non finito, in entrambi i motori.
    """
    assert main._testo_canonico(10 ** 400) == 'Infinity'
    assert main._testo_canonico(-(10 ** 400)) == '-Infinity'
    assert main.motivo_valore_numerico('Points', 10 ** 400) is not None


def test_il_feed_contiene_il_testo_GIUDICATO_non_quello_grezzo():
    """Il byte perdonato ai fini del verdetto non deve raggiungere XTrader.

    La guardia giudica la forma normalizzata (`_piatto`), quindi un Price
    `'\\ufeff2'` e' una quota valida — ma il CSV emetteva il valore grezzo,
    BOM compreso: XTrader riceveva il byte che la guardia aveva perdonato solo
    per giudicare. Le colonne numeriche viaggiano ora nella forma giudicata,
    in entrambi i motori. [REAL_FINDING] di GPT-5.6 Sol al gate finale, PR #47.
    """
    import json
    cfg = {'config_json': json.dumps(_config(Price='\ufeff2\xa0'))}
    parsed, motivi = main.esito_messaggio(MESSAGGIO, cfg)
    assert parsed, f'il valore perdonato ai bordi e\' stato scartato: {motivi}'
    assert '\ufeff2' not in parsed['csv'], 'il BOM perdonato e\' arrivato nel feed'
    assert '"2"' in parsed['csv'], parsed['csv']
    assert parsed['csv'].count('\ufeff') == 1, 'un solo BOM: quello del contratto'


def test_una_config_ROTTA_non_archivia_i_messaggi_estranei():
    """`config non eseguibile` esiste solo se il parser RICONOSCE il messaggio.

    Il fail-safe di `esito_messaggio` restituiva il motivo per QUALUNQUE
    messaggio: un parser con config rotta faceva archiviare in `message_logs`
    tutto il traffico della chat, attribuito a lui — la stessa classe chiusa
    in `esegui_parser` per gli scarti numerici, riaperta nel ramo d'errore.
    [REAL_FINDING] di Claude Fable 5 al gate finale della PR #47.
    """
    import json
    rotta = {'config_json': json.dumps({
        'match': {'type': 'contains', 'value': 'P.Bet.'},
        'columns': {'EventName': {'source': 'regex', 'pattern': 123}}})}
    parsed, motivi = main.esito_messaggio('chiacchiere qualunque', rotta)
    assert parsed is None and motivi == [], (
        f'una config rotta ha prodotto motivi per un messaggio estraneo: {motivi}')
    parsed, motivi = main.esito_messaggio(MESSAGGIO, rotta)
    assert parsed is None and motivi == ['config non eseguibile'], (
        'il messaggio RICONOSCIUTO deve conservare la diagnosi: ' + repr(motivi))
