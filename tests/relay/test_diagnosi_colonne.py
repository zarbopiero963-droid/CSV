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


# --------------------------------- i tre buchi trovati dai reviewer sulla PR #104
#
# Claude Fable 5 e GPT-5.5 hanno fermato la prima versione di questa funzione con
# lo stesso bloccante, da due angoli: la diagnosi nasceva dentro `_giudica_riga`,
# cioe' PRIMA che il verdetto della riga fosse completo. Misurato allora, sul
# codice di `ce6af4c`:
#
#   A) gate di contenuto (#41): `complete=False`, 1 scarto, «0 colonne bloccano»;
#   B) multi-riga: la sola riga generata scartata per `Price`, e la diagnosi —
#      quella della BASE, sana — diceva ancora «0 bloccano»; le righe non avevano
#      diagnosi propria;
#   C) messaggio NON riconosciuto: `EventName` marcata `blocca` benche' nessuna
#      riga fosse in gioco: la causa vera era la condizione, non la colonna.
#
# Sono la stessa classe: una tabella che esiste per dire «quale colonna blocca il
# CSV» non puo' essere calcolata su un verdetto parziale. Questi test la tengono.


def test_A_il_gate_di_contenuto_non_sparisce_dalla_tabella():
    """Il gate #41 e' una causa di RIGA — non nomina una colonna, perche' parla
    del parser nel suo insieme. Non puo' finire in una voce senza mentire su
    quale colonna sia il problema, e non puo' sparire: la tabella direbbe
    «nessuna colonna blocca» mentre la riga non esce.

    Fail-first (misurato su `ce6af4c`): `complete=False`, `blocca=0`, e NESSUN
    posto dove leggere il motivo — `cause_di_riga` non esisteva."""
    # tutte e quattro le obbligatorie costanti: il gate #41 scatta per definizione
    esito = main.esegui_parser('GOL adesso', _config())
    assert esito['complete'] is False
    assert not esito['missing']
    cause = main.cause_di_riga(esito['scarti'])
    assert len(cause) == 1, esito['scarti']
    assert 'nessuna colonna obbligatoria viene estratta' in cause[0]
    # e non e' stata attribuita per sbaglio a una colonna
    assert [v['colonna'] for v in esito['diagnosi'] if v['stato'] == 'blocca'] == []


def test_B_ogni_riga_generata_porta_la_PROPRIA_diagnosi():
    """Col multi-riga (#35) il verdetto e' PER RIGA: base sana, riga di override
    rotta. La diagnosi della riga deve accusare la colonna della RIGA.

    Fail-first (misurato su `ce6af4c`): `righe[0]` non aveva nessuna chiave
    `diagnosi`, e la sola diagnosi disponibile — quella della base — diceva
    «0 bloccano» mentre l'unica riga generata veniva scartata."""
    cfg = {'match': {'value': 'GOL'},
           'columns': {
               'EventName': {'source': 'regex',
                             'pattern': r'([A-Za-z]+ - [A-Za-z]+)', 'group': 1},
               'MarketType': {'source': 'constant', 'value': 'OVER_UNDER_15'},
               'SelectionName': {'source': 'constant', 'value': 'Over 1,5 goal'},
               'BetType': {'source': 'constant', 'value': 'PUNTA'}},
           # quota fuori intervallo SOLO sulla riga di override
           'multi': {'markets': [{'market_type': 'CORRECT_SCORE', 'price': '0.5'}]}}
    esito = main.esegui_parser('Juventus - Milan GOL', cfg)
    assert len(esito['righe']) == 1
    riga = esito['righe'][0]
    assert riga['complete'] is False, riga
    bloccanti = [v['colonna'] for v in riga['diagnosi'] if v['stato'] == 'blocca']
    assert bloccanti == ['Price'], riga['diagnosi']
    motivo = next(v for v in riga['diagnosi'] if v['colonna'] == 'Price')['motivo']
    assert "fuori dall'intervallo" in motivo, motivo
    # la BASE resta sana, ed e' giusto: il difetto era mostrare SOLO lei
    assert [v['colonna'] for v in esito['diagnosi'] if v['stato'] == 'blocca'] == []


def test_B_anche_la_riga_rifiutata_dai_delimitatori_ha_la_sua_diagnosi():
    """Le righe scartate PRIMA del giudizio (selezione vuota + delimitatori su un
    mercato che non e' di punteggio) sarebbero state le uniche senza tabella,
    proprio dove serve. Lo scarto nomina `SelectionName`, quindi la voce di quella
    colonna deve bloccare."""
    cfg = {'match': {'value': 'GOL'},
           'columns': {
               'EventName': {'source': 'regex',
                             'pattern': r'([A-Za-z]+ - [A-Za-z]+)', 'group': 1},
               'MarketType': {'source': 'constant', 'value': 'OVER_UNDER_15'},
               'SelectionName': {'source': 'constant', 'value': 'Over 1,5 goal'},
               'BetType': {'source': 'constant', 'value': 'PUNTA'}},
           'multi': {'markets': [{'market_type': 'MATCH_ODDS',
                                  'start_after': 'GOL', 'end_before': 'fine'}]}}
    esito = main.esegui_parser('Juventus - Milan GOL 1-0 fine', cfg)
    riga = esito['righe'][0]
    assert riga['complete'] is False
    assert riga['diagnosi'], 'riga senza diagnosi'
    voce = next(v for v in riga['diagnosi'] if v['colonna'] == 'SelectionName')
    assert voce['stato'] == 'blocca', voce
    assert 'CORRECT_SCORE' in voce['motivo'], voce['motivo']


def test_C_il_messaggio_ignorato_non_accusa_le_colonne_obbligatorie():
    """Se la condizione non combacia la riga non esce PER QUELLO. Marcare le
    obbligatorie vuote come `blocca` indicherebbe all'utente la causa sbagliata:
    andrebbe a mappare colonne mentre il difetto e' nella condizione.

    Fail-first (misurato su `ce6af4c`): `blocca = ['EventName']` su un messaggio
    che il parser aveva ignorato."""
    cfg = {'match': {'value': 'GOL'},
           'columns': {'EventName': {'source': 'regex', 'pattern': r'(GOL .+)',
                                     'group': 1}}}
    esito = main.esegui_parser('messaggio qualunque', cfg)
    assert esito['matched'] is False
    assert esito['missing'], 'il caso non e- stato esercitato: nessuna obbligatoria vuota'
    assert [v['stato'] for v in esito['diagnosi'] if v['stato'] == 'blocca'] == []
    assert _voce(esito, 'EventName')['stato'] == 'vuota'


# ------------------------------------------- la regola d'attribuzione, da sola


def test_l_attribuzione_e_per_nome_esatto_della_colonna_in_testa():
    """`_motivi_di` aggancia sul nome ESATTO prima dei due punti. `MinPrice` e
    `Price` condividono la coda, non la testa: un motivo di `MinPrice` non deve
    finire su `Price`, o la tabella accuserebbe la colonna sbagliata."""
    motivi = ['MinPrice: fuori intervallo', 'Price: fuori intervallo']
    assert main._motivi_di(motivi, 'Price') == ['Price: fuori intervallo']
    assert main._motivi_di(motivi, 'MinPrice') == ['MinPrice: fuori intervallo']


def test_uno_scarto_che_non_nomina_una_colonna_resta_visibile_come_causa_di_riga():
    """Il rischio segnalato da Claude Fable 5 sulla PR #104: un motivo il cui
    prefisso non e' esattamente una colonna verrebbe perso in silenzio. Non si
    perde: `cause_di_riga` lo raccoglie, e il pannello lo mostra sotto la
    tabella. Vale per QUALUNQUE motivo futuro, non solo per il gate #41."""
    scarti = ['Price: fuori intervallo', 'qualcosa di nuovo che non nomina colonne',
              'Prezzo: prefisso che NON e- una colonna del contratto']
    assert main.cause_di_riga(scarti) == [
        'qualcosa di nuovo che non nomina colonne',
        'Prezzo: prefisso che NON e- una colonna del contratto']
    # e nessuna delle 14 se lo prende
    riga = [''] * len(main.HEADERS)
    diagnosi = main._diagnosi_colonne(riga, True, [], scarti, [])
    assert [v['colonna'] for v in diagnosi if v['stato'] == 'blocca'] == ['Price']


def test_un_avviso_orfano_non_altera_nessuna_voce():
    """Stessa regola dal lato `segnala`: un avviso il cui prefisso non e' una
    colonna non deve marcare a caso una voce. Resta nella lista `avvisi`, che il
    pannello mostra sempre nel suo banner."""
    riga = ['x'] * len(main.HEADERS)
    diagnosi = main._diagnosi_colonne(riga, True, [], [], ['non una colonna: nota'])
    assert {v['stato'] for v in diagnosi} == {'ok'}


def test_i_rami_di_rifiuto_anticipato_giudicano_comunque_la_riga():
    """[REAL_FINDING] di GPT-5.6 Sol al gate finale della PR #104.

    Le righe rifiutate PRIMA del giudizio (delimitatori: mercato incompatibile,
    zero punteggi, troppi punteggi) costruivano il proprio esito con `missing=[]`
    e il solo scarto della riga. Le altre cause della STESSA riga sparivano dalla
    diagnosi — e non «sparivano» soltanto: la colonna veniva dichiarata sana.

    Misurato prima della correzione, su una riga rifiutata dai delimitatori che
    porta anche una obbligatoria vuota e una quota fuori intervallo:

        missing = []
        blocca  = ['SelectionName']
        Price   = '0.5' → stato «ok»

    `Price: ok` su un valore che XTrader non accetta e' peggio di un'omissione: e'
    un'affermazione falsa nella tabella che l'utente legge per correggere. Il
    verdetto della riga (`complete`) era gia' giusto — e' la SPIEGAZIONE a mentire,
    cioe' proprio cio' per cui la #25 esiste."""
    cfg = {'match': {'value': 'GOL'},
           'columns': {
               # regex che non trova nulla: EventName resta vuota nella riga
               'EventName': {'source': 'regex', 'pattern': '(NON C E)', 'group': 1},
               'MarketType': {'source': 'constant', 'value': 'MATCH_ODDS'},
               'SelectionName': {'source': 'constant', 'value': 'Inter'},
               'BetType': {'source': 'constant', 'value': 'PUNTA'}},
           # selezione vuota + delimitatori su un mercato NON di punteggio:
           # ramo di rifiuto anticipato. La riga porta anche una quota invalida.
           'multi': {'markets': [{'market_type': 'MATCH_ODDS', 'price': '0.5',
                                  'selection_name': '',
                                  'start_after': 'GOL', 'end_before': 'fine'}]}}
    riga = main.esegui_parser('GOL 1-0 fine', cfg)['righe'][0]
    assert riga['complete'] is False
    voci = {v['colonna']: v for v in riga['diagnosi']}
    # lo scarto del ramo resta
    assert voci['SelectionName']['stato'] == 'blocca', voci['SelectionName']
    assert 'CORRECT_SCORE' in voci['SelectionName']['motivo']
    # ...e le altre cause della stessa riga NON spariscono
    assert voci['Price']['stato'] == 'blocca', \
        f"Price 0.5 dichiarata sana: {voci['Price']}"
    assert "fuori dall'intervallo" in voci['Price']['motivo'], voci['Price']
    assert voci['EventName']['stato'] == 'blocca', voci['EventName']
    assert 'EventName' in riga['missing'], riga['missing']


def test_il_rifiuto_per_zero_punteggi_porta_le_altre_cause_della_riga():
    """Stesso difetto, secondo ramo: nessun punteggio N-N fra i delimitatori. La
    classe sono i tre rami di rifiuto anticipato, non il primo che si incontra."""
    cfg = {'match': {'value': 'GOL'},
           'columns': {
               'EventName': {'source': 'regex', 'pattern': r'(Inter - Milan)', 'group': 1},
               'MarketType': {'source': 'constant', 'value': 'CORRECT_SCORE'},
               'SelectionName': {'source': 'constant', 'value': 'Inter'},
               'BetType': {'source': 'constant', 'value': 'PUNTA'}},
           'multi': {'markets': [{'market_type': 'CORRECT_SCORE', 'price': '0.5',
                                  'selection_name': '',
                                  'start_after': 'GOL', 'end_before': 'fine'}]}}
    riga = main.esegui_parser('GOL Inter - Milan niente cifre fine', cfg)['righe'][0]
    voci = {v['colonna']: v for v in riga['diagnosi']}
    assert voci['SelectionName']['stato'] == 'blocca'
    assert 'nessun punteggio' in voci['SelectionName']['motivo']
    assert voci['Price']['stato'] == 'blocca', \
        f"Price 0.5 dichiarata sana nel ramo «zero punteggi»: {voci['Price']}"
