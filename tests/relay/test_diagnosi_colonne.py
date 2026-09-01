"""La diagnosi PER COLONNA del motore (#25, commento del 14/08).

La #25 chiedeva di passare da una risposta a **tre livelli globali**
(`matched`/`missing`/`complete`) a una risposta a **quattordici righe**: per ogni
colonna del CSV, stato + motivo + valore estratto. Qui si vincola quel contratto.

Cosa e' vincolato, e perche':

- **una voce per ognuna** delle 14 colonne, nell'ordine dell'intestazione: la
  tabella deve rispondere anche sulle colonne che stanno bene, o non e' una
  diagnosi, e' un elenco di errori;
- **due livelli di gravita' distinti** — `blocca` (senza questa colonna la riga
  non esce) e `segnala` (c'e' qualcosa da sapere ma la riga esce lo stesso). E'
  il vincolo che il commento del 14/08 ha estratto da un difetto del Bridge, dove
  un rosso bloccante e un rosso su campo facoltativo avevano lo stesso aspetto;
- **`vuota` non e' un errore**: `Price` vuota e' il caso normale (la quota la
  mette XTrader), e non deve comparire come problema;
- **i motivi sono azionabili**: dicono cosa fare, non solo cosa non e' andato.

La parita' JS↔Python sulla `diagnosi` e' vincolata a parte, in
`tests/engine/test_engine_contract.py`, che confronta il campo per intero.
"""

from __future__ import annotations

import sys
from pathlib import Path

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE))

import main  # noqa: E402


def _config(**colonne):
    """Una config riconosciuta da «GOL», con le quattro obbligatorie costanti."""
    base = {'EventName': {'source': 'constant', 'value': 'Juventus - Milan'},
            'MarketType': {'source': 'constant', 'value': 'OVER_UNDER_15'},
            'SelectionName': {'source': 'constant', 'value': 'Over 1,5 goal'},
            'BetType': {'source': 'constant', 'value': 'PUNTA'}}
    base.update(colonne)
    return {'match': {'value': 'GOL'}, 'columns': base}


def _voce(esito, colonna):
    return next(v for v in esito['diagnosi'] if v['colonna'] == colonna)


# ------------------------------------------------------- forma della diagnosi

def test_c_e_una_voce_per_OGNI_colonna_nell_ordine_dell_intestazione():
    """Quattordici righe, nell'ordine dell'intestazione CSV: la diagnosi risponde
    anche sulle colonne che stanno bene."""
    esito = main.esegui_parser('GOL adesso', _config())
    assert [v['colonna'] for v in esito['diagnosi']] == main.HEADERS
    for voce in esito['diagnosi']:
        assert set(voce) == {'colonna', 'stato', 'motivo', 'valore'}, voce
        assert voce['stato'] in ('ok', 'vuota', 'blocca', 'segnala'), voce


def test_una_colonna_valorizzata_e_OK_e_porta_il_valore_finale():
    esito = main.esegui_parser('GOL adesso', _config())
    voce = _voce(esito, 'MarketType')
    assert voce['stato'] == 'ok', voce
    assert voce['valore'] == 'OVER_UNDER_15', voce
    assert voce['motivo'] == '', voce


def test_una_FACOLTATIVA_vuota_NON_e_un_errore():
    """`Price` vuota e' il caso normale — la quota la mette XTrader. Deve uscire
    `vuota`, senza motivo: se comparisse come problema, il pannello mostrerebbe
    un rosso su qualcosa che non lo e' (difetto B del Bridge)."""
    esito = main.esegui_parser('GOL adesso', _config())
    voce = _voce(esito, 'Price')
    assert voce['stato'] == 'vuota', voce
    assert voce['motivo'] == '', voce


# ------------------------------------------------------------ livello BLOCCA

def test_una_OBBLIGATORIA_vuota_BLOCCA_e_il_motivo_dice_cosa_fare():
    """Fail-first: senza la diagnosi per colonna, l'unica risposta era la lista
    globale `missing`, che nomina la colonna ma non dice cosa farne."""
    cfg = _config(SelectionName={'source': 'constant', 'value': ''})
    esito = main.esegui_parser('GOL adesso', cfg)
    voce = _voce(esito, 'SelectionName')
    assert voce['stato'] == 'blocca', voce
    assert 'SelectionName' in voce['motivo']
    # azionabile: dice COSA FARE, non solo cosa manca
    assert 'Mappala' in voce['motivo'], voce['motivo']
    assert 'SelectionName' in esito['missing']
    assert esito['complete'] is False


def test_un_valore_con_EMOJI_blocca_la_SUA_colonna_col_motivo_dello_scarto():
    """L'emoji nel valore e' gia' uno scarto (#42): la diagnosi lo attribuisce
    alla colonna che ce l'ha, con lo STESSO motivo — nessun secondo catalogo."""
    cfg = _config(SelectionName={'source': 'constant', 'value': 'Over 1,5 ⚽'})
    esito = main.esegui_parser('GOL adesso', cfg)
    voce = _voce(esito, 'SelectionName')
    assert voce['stato'] == 'blocca', voce
    assert 'emoji' in voce['motivo'], voce['motivo']
    assert voce['motivo'] in esito['scarti'], 'il motivo non e- lo stesso dello scarto'
    assert esito['complete'] is False


def test_una_QUOTA_fuori_intervallo_blocca_la_SUA_colonna():
    """La guardia numerica (#39) diventa il motivo della colonna `Price`."""
    cfg = _config(Price={'source': 'constant', 'value': '0,5'})
    esito = main.esegui_parser('GOL adesso', cfg)
    voce = _voce(esito, 'Price')
    assert voce['stato'] == 'blocca', voce
    assert voce['motivo'] in esito['scarti'], 'il motivo non e- lo stesso dello scarto'
    assert esito['complete'] is False


def test_una_colonna_SANA_resta_ok_mentre_un_altra_blocca():
    """Il rosso e' della colonna che ha il problema, non di tutta la tabella."""
    cfg = _config(Price={'source': 'constant', 'value': '0,5'})
    esito = main.esegui_parser('GOL adesso', cfg)
    assert _voce(esito, 'Price')['stato'] == 'blocca'
    assert _voce(esito, 'MarketType')['stato'] == 'ok'
    assert _voce(esito, 'BetType')['stato'] == 'ok'


# ----------------------------------------------------------- livello SEGNALA

def test_l_avviso_SEGNALA_e_la_riga_esce_lo_stesso():
    """Il **caso B** del commento del 14/08: un problema su cosa che NON blocca
    deve avere un livello DIVERSO dal bloccante, e il verdetto complessivo NON
    deve diventare rosso. Qui la squadra senza alias nella sorgente: il segnale
    esce verbatim (deciso dal proprietario) e la colonna lo segnala.

    Fail-first: con un livello solo, questa colonna sarebbe indistinguibile da
    una che ferma il segnale — e importeremmo il difetto B del Bridge."""
    cfg = {'match': {'value': 'GOL'},
           'columns': {
               'EventName': {'source': 'regex',
                             'pattern': r'([A-Za-z]+ - [A-Za-z]+)', 'group': 1},
               'MarketType': {'source': 'constant', 'value': 'OVER_UNDER_15'},
               'SelectionName': {'source': 'constant', 'value': 'Over 1,5 goal'},
               'BetType': {'source': 'constant', 'value': 'PUNTA'}}}
    esito = main.esegui_parser('Juventus - Milan GOL', cfg, mappa_alias={})
    voce = _voce(esito, 'EventName')
    assert esito['avvisi'], 'nessun avviso: il caso non e- stato esercitato'
    assert voce['stato'] == 'segnala', voce
    assert voce['motivo'], voce
    # il livello «segnala» NON ferma la riga: e' l'invariante del caso B
    assert esito['complete'] is True, esito['scarti']
    assert not esito['missing']


def test_un_problema_che_BLOCCA_non_viene_declassato_a_segnala():
    """Se la stessa colonna ha un avviso E un problema bloccante, resta `blocca`:
    un guasto che ferma il segnale non va mai mostrato come semplice nota."""
    cfg = {'match': {'value': 'GOL'},
           'columns': {
               'EventName': {'source': 'constant', 'value': ''},
               'MarketType': {'source': 'constant', 'value': 'OVER_UNDER_15'},
               'SelectionName': {'source': 'constant', 'value': 'Over 1,5 goal'},
               'BetType': {'source': 'constant', 'value': 'PUNTA'}}}
    esito = main.esegui_parser('Juventus - Milan GOL', cfg, mappa_alias={})
    voce = _voce(esito, 'EventName')
    assert voce['stato'] == 'blocca', voce
    assert 'Mappala' in voce['motivo'], voce['motivo']


# ------------------------------------------------- il messaggio non riconosciuto

def test_senza_riconoscimento_non_si_inventano_scarti_per_colonna():
    """Nessuno scarto senza `matched` (invariante gia' del motore): la diagnosi
    non deve accusare le colonne di un messaggio che il parser ha ignorato."""
    cfg = _config(Price={'source': 'constant', 'value': '0,5'})
    esito = main.esegui_parser('nessuna parola chiave', cfg)
    assert esito['matched'] is False
    assert _voce(esito, 'Price')['stato'] != 'blocca', _voce(esito, 'Price')
    assert esito['scarti'] == []
