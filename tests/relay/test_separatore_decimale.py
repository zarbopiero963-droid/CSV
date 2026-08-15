"""Il separatore decimale e' una proprieta' del contratto, non una scelta (#40).

Per XTrader la quota si scrive con la **virgola**: `"1,85"`. Non e' una
preferenza — e' misurato tre volte, e le tre misure concordano:

- la guida ufficiale del produttore (`Guida_XTrader_Completa_del13.08.2026.pdf`,
  p. 169) mostra `Price` valorizzata `"1,23"`, unico campo numerico
  valorizzato in 315 pagine;
- il Bridge, che gira in produzione con XTrader italiano, scrive `"1,85"`
  (guida «Come il bridge scrive il CSV», eseguita su tutte e tre le lingue);
- il proprietario ha confermato: «noi usiamo IT per XTrader».

Prima di questo PR il separatore era un **incidente**: nel feed usciva cio' che
la regola dell'utente produceva, e il suggeritore spingeva `comma_to_dot` su
`Price` — cioe' verso il punto, l'opposto dell'implementazione che funziona. Il
caso pericoloso non e' «non funziona»: e' `"1.85"` letto col punto come
separatore delle **migliaia**, cioe' quota 185 — dentro i tetti della #39,
invisibile a ogni guardia, e su una giocata in BANCA un disastro.

La struttura e' quella del Bridge: internamente il punto, la localizzazione al
confine di scrittura, la lingua in una tabella con una sola voce oggi
(`IT → virgola`; EN/ES arriveranno con la famiglia Betting Toolkit, e saranno
una riga, non un refactor). Le trasformazioni dell'utente restano davanti: chi
ha gia' `comma_to_dot` continua a normalizzare al punto e la serializzazione
riporta la virgola — nessun parser esistente si rompe.
"""

from __future__ import annotations

import json
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


MESSAGGIO_PUNTO = 'P.Bet. Juventus - Palermo\nquota 1.85'
MESSAGGIO_VIRGOLA = 'P.Bet. Juventus - Palermo\nquota 1,85'

ESTRAZIONE_QUOTA = {'source': 'regex',
                    'pattern': r'quota\s*([0-9]+[.,][0-9]+)', 'group': 1}


def _config(price=None, **colonne):
    base = {
        'EventName': {'source': 'line', 'contains': 'P.Bet.'},
        'MarketType': {'source': 'constant', 'value': 'OVER_UNDER_15'},
        'SelectionName': {'source': 'constant', 'value': 'Over 1,5 goal'},
        'BetType': {'source': 'constant', 'value': 'PUNTA'},
    }
    if price is not None:
        base['Price'] = price
    base.update({c: {'source': 'constant', 'value': v} for c, v in colonne.items()})
    return {'match': {'type': 'contains', 'value': 'P.Bet.'}, 'columns': base}


def _campo(csv_testo, colonna):
    """Il valore del campo nella riga del segnale, coi byte del feed."""
    import csv as modulo_csv
    import io
    riga = csv_testo.split('\r\n')[1]
    campi = next(modulo_csv.reader(io.StringIO(riga)))
    return campi[main.HEADERS.index(colonna)]


def _feed(message, config):
    parsed, motivi = main.esito_messaggio(
        message, {'config_json': json.dumps(config)})
    assert parsed, f'nessuna riga prodotta: {motivi}'
    return parsed['csv']


# ------------------------------------------------------- il confine di scrittura

def test_la_quota_col_PUNTO_esce_con_la_virgola():
    """Caso 2 della #40, il fail-first principale: canale che scrive `1.85`.

    Oggi il punto finirebbe verbatim nel feed, e in contesto italiano `"1.85"`
    rischia la lettura come migliaia: quota 185, dentro i tetti, invisibile.
    """
    csv_testo = _feed(MESSAGGIO_PUNTO, _config(price=ESTRAZIONE_QUOTA))
    assert _campo(csv_testo, 'Price') == '1,85', (
        f'il feed non e\' localizzato: Price = {_campo(csv_testo, "Price")!r}')
    assert '"1.85"' not in csv_testo


def test_la_quota_con_la_VIRGOLA_resta_con_la_virgola():
    """Caso 1: il canale italiano tipico. Anti-regressione, non fail-first."""
    csv_testo = _feed(MESSAGGIO_VIRGOLA, _config(price=ESTRAZIONE_QUOTA))
    assert _campo(csv_testo, 'Price') == '1,85'


def test_un_parser_con_comma_to_dot_GIA_configurato_produce_lo_stesso_feed():
    """Caso 3, la retrocompatibilita': i parser esistenti non si toccano.

    Chi ha seguito il vecchio suggeritore ha `comma_to_dot` su `Price`: la
    trasformazione normalizza al punto, la localizzazione riporta la virgola.
    Stesso feed di chi non ce l'ha — e' il vincolo che permette di NON migrare
    a mano le config esistenti.
    """
    regola = dict(ESTRAZIONE_QUOTA, transforms=[{'op': 'comma_to_dot'}])
    csv_testo = _feed(MESSAGGIO_VIRGOLA, _config(price=regola))
    assert _campo(csv_testo, 'Price') == '1,85', (
        'comma_to_dot ha vinto sul confine di scrittura: '
        + repr(_campo(csv_testo, 'Price')))


def test_le_altre_colonne_numeriche_seguono_la_stessa_regola():
    """Caso 4: Handicap e Points (e Min/MaxPrice) sono lo stesso confine."""
    csv_testo = _feed(MESSAGGIO_VIRGOLA, _config(
        price=ESTRAZIONE_QUOTA,
        Handicap='-1.5', Points='0.5', MinPrice='1.5', MaxPrice='3.5'))
    for colonna, atteso in (('Handicap', '-1,5'), ('Points', '0,5'),
                            ('MinPrice', '1,5'), ('MaxPrice', '3,5')):
        assert _campo(csv_testo, colonna) == atteso, (
            f'{colonna} = {_campo(csv_testo, colonna)!r}, atteso {atteso!r}')


def test_la_quota_vuota_resta_vuota():
    """Caso 6: nessuna localizzazione applicata al nulla."""
    csv_testo = _feed(MESSAGGIO_VIRGOLA, _config())
    assert _campo(csv_testo, 'Price') == ''


def test_il_verificatore_respinge_il_feed_col_punto():
    """Caso del punto 3 della decisione: «dichiarato sano» = test eseguibile.

    Un feed con `"1.85"` in Price non deve poter essere scritto: senza questo
    controllo la decisione della #40 fra sei mesi varrebbe quanto la riga
    «senza BOM» — un'affermazione mai misurata.
    """
    riga = [''] * len(main.HEADERS)
    riga[main.HEADERS.index('Provider')] = 'XTrader'
    riga[main.HEADERS.index('EventName')] = 'Juventus - Palermo'
    riga[main.HEADERS.index('MarketType')] = 'OVER_UNDER_15'
    riga[main.HEADERS.index('SelectionName')] = 'Over'
    riga[main.HEADERS.index('BetType')] = 'PUNTA'
    riga[main.HEADERS.index('Price')] = '1.85'
    with pytest.raises(ValueError):
        main.verify_csv(main.make_csv(riga))
    # E la forma giusta passa — senza questo verso, un verificatore sempre
    # rosso passerebbe il test qui sopra.
    riga[main.HEADERS.index('Price')] = '1,85'
    main.verify_csv(main.make_csv(riga))


def test_un_nome_squadra_con_virgole_non_confonde_il_verificatore():
    """Il parsing dei campi deve reggere virgole e virgolette NEI valori.

    `EventName` sta prima delle colonne numeriche: un nome con virgole dentro
    le virgolette non deve spostare gli indici e far giudicare la colonna
    sbagliata.
    """
    riga = [''] * len(main.HEADERS)
    riga[main.HEADERS.index('Provider')] = 'XTrader'
    riga[main.HEADERS.index('EventName')] = 'Squadra "A", Citta - Altra, ancora'
    riga[main.HEADERS.index('BetType')] = 'PUNTA'
    riga[main.HEADERS.index('Price')] = '1,85'
    main.verify_csv(main.make_csv(riga))


def test_il_feed_legacy_di_PIERO_resta_byte_identico():
    """Regola 5: il percorso legacy non passa dal confine e non cambia.

    `parse_message` lascia Price vuota e Handicap `'0'`: nessun separatore,
    nessuna localizzazione, stessi byte di prima. E' il vincolo che permette
    di NON toccare `make_csv`, condiviso col legacy.
    """
    cfg = {'name': 'PIERO', 'header': 'P.Bet. PREMACHT 0,5HT',
           'market_name': 'Over/Under 1,5 gol', 'market_type': 'OVER_UNDER_15',
           'selection_name': 'Over 1,5 goal', 'handicap': '0',
           'bet_type': 'PUNTA', 'config_json': None}
    parsed = main.elabora_messaggio(
        'P.Bet. PREMACHT 0,5HT\n\U0001f19a Juve v Milan\n@ 1.85', cfg)
    assert parsed is not None
    assert _campo(parsed['csv'], 'Handicap') == '0'
    assert _campo(parsed['csv'], 'Price') == ''
    main.verify_csv(parsed['csv'])
