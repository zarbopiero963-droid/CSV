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
