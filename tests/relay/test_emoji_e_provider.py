"""Niente emoji nei valori del feed, e Provider suggerita vuota (#42).

Le parole del proprietario: «L'importante: solo testo. Emoji non li accetta
XTrader, lo marcherebbe non valido come segnale» — e, come tutto in XTrader,
**senza un errore di ritorno**: solo un'icona rossa che l'utente deve notare.

Il caso che ci riguarda davvero e' `EventName`: i messaggi Telegram cominciano
quasi sempre col marcatore `\U0001F19A`, e una regola che prende la riga INTERA
invece del testo dopo il marcatore si porta l'emoji dentro il valore. Il feed
esce formalmente valido — 14 colonne, virgolette, CRLF, BOM — e XTrader lo
scarta in silenzio. Per la regola gia' adottata nella #39 si SCARTA, non si
avvisa: un valore che il consumatore rifiuta non e' un valore.

E `Provider` esce vuota da contratto: e' il nome di CHI MANDA, non di chi
legge. Il suggeritore proponeva `XTrader` perche' e' il valore del CSV misurato
in #5 — ma li' vale `XTrader` proprio perche' quel file l'ha scritto XTrader.
Un'osservazione corretta letta nel verso sbagliato, come il BOM.
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


MESSAGGIO = 'P.Bet. Juventus - Palermo'


def _config(**colonne):
    base = {
        'EventName': {'source': 'line', 'contains': 'P.Bet.'},
        'MarketType': {'source': 'constant', 'value': 'OVER_UNDER_15'},
        'SelectionName': {'source': 'constant', 'value': 'Over 1,5 goal'},
        'BetType': {'source': 'constant', 'value': 'PUNTA'},
    }
    base.update({c: {'source': 'constant', 'value': v} for c, v in colonne.items()})
    return {'match': {'type': 'contains', 'value': 'P.Bet.'}, 'columns': base}


# ------------------------------------------------ la guardia sui valori (scarto)

def test_un_evento_con_l_emoji_dentro_viene_scartato_con_il_motivo():
    """Il caso reale: regola «riga intera» su una riga che comincia col marcatore.

    Fail-first della #42: oggi il valore esce nel feed formalmente valido e
    XTrader lo scarta in silenzio. Il motivo deve nominare la COLONNA e dire
    COSA FARE (il testo dopo il marcatore, non la riga intera).
    """
    r = main.esegui_parser('P.Bet. LIVE\n\U0001F19A Juventus - Palermo', {
        'match': {'type': 'contains', 'value': 'P.Bet.'},
        'columns': {
            'EventName': {'source': 'line', 'anchor': '\U0001F19A'},
            'MarketType': {'source': 'constant', 'value': 'OVER_UNDER_15'},
            'SelectionName': {'source': 'constant', 'value': 'Over'},
            'BetType': {'source': 'constant', 'value': 'PUNTA'},
        }})
    assert r['complete'] is False, 'un evento con l\'emoji ha raggiunto il feed'
    motivi = ' | '.join(r['scarti'])
    assert 'EventName' in motivi and 'emoji' in motivi.lower(), motivi
    assert 'marcatore' in motivi.lower(), (
        f'il motivo non dice cosa fare: {motivi!r}')


def test_i_marcatori_tipici_sono_tutti_vietati_nei_valori():
    """\U0001F19A, l'orologio, la spunta, la fiamma: la classe copre i marcatori reali."""
    for emoji in ('\U0001F19A', '⏰', '✅', '\U0001F525', '⭐'):
        r = main.esegui_parser(MESSAGGIO, _config(MarketName=f'Over {emoji} 1,5'))
        assert r['complete'] is False, f'{emoji!r} accettato in un valore del feed'


def test_i_nomi_normali_non_scattano_la_guardia():
    """Anti-zelo: accenti, virgolette, virgole e ' v ' sono testo legittimo."""
    for nome in ('Città di Palermo', 'Squadra "A", Über - Løv', 'Real v Barça',
                 'Over/Under 1,5 gol', "L'Aquila - Est"):
        r = main.esegui_parser(MESSAGGIO, _config(MarketName=nome))
        assert r['complete'] is True, (
            f'{nome!r} scartato dalla guardia emoji: {r["scarti"]}')


def test_il_webhook_non_scrive_il_segnale_con_l_emoji(tmp_path, monkeypatch):
    """Il percorso vero: nessuna riga nel feed, e il log dice perche'."""
    import asyncio
    import sqlite3

    from tests.dati import relay_in_processo
    from tests.relay.test_webhook import BOT_FINTO, CHAT, RichiestaFinta

    percorso = relay_in_processo(monkeypatch, tmp_path / 'emoji.db', chat_ids=CHAT)
    monkeypatch.setattr(main, 'SEGRETO_WEBHOOK', main.webhook_secret(BOT_FINTO))
    c = sqlite3.connect(percorso)
    c.execute('UPDATE parsers SET config_json=? WHERE name=?',
              (json.dumps(_config(MarketName='Over ✅ 1,5')), main.DEFAULT_PARSER))
    c.commit()
    c.close()

    payload = {'message': {'chat': {'id': int(CHAT)}, 'text': MESSAGGIO}}
    asyncio.run(main.telegram_webhook(RichiestaFinta(
        {'X-Telegram-Bot-Api-Secret-Token': main.webhook_secret(BOT_FINTO)}, payload)))

    c = sqlite3.connect(percorso)
    segnali = c.execute('SELECT COUNT(*) FROM signals').fetchone()[0]
    esiti = [r[0] for r in c.execute('SELECT esito FROM message_logs').fetchall()]
    c.close()
    assert segnali == 0, 'un segnale con l\'emoji e\' stato scritto nel feed'
    assert any('emoji' in (e or '').lower() for e in esiti), esiti


# ------------------------------------------------- il tripwire nel verificatore

def test_il_verificatore_respinge_un_campo_con_l_emoji():
    """Il contratto dice «niente emoji, in nessuna colonna»: senza il test
    eseguibile la regola varrebbe quanto la riga «senza BOM»."""
    riga = [''] * len(main.HEADERS)
    riga[main.HEADERS.index('EventName')] = '\U0001F19A Juventus - Palermo'
    riga[main.HEADERS.index('BetType')] = 'PUNTA'
    with pytest.raises(ValueError):
        main.verify_csv(main.make_csv(riga))
    riga[main.HEADERS.index('EventName')] = 'Juventus - Palermo'
    main.verify_csv(main.make_csv(riga))


def test_il_feed_legacy_di_PIERO_resta_scrivibile():
    """Il parser storico estrae DOPO il marcatore: nessuna emoji nel valore,
    nessun impatto della guardia sul percorso di produzione."""
    cfg = {'name': 'PIERO', 'header': 'P.Bet. PREMACHT 0,5HT',
           'market_name': 'Over/Under 1,5 gol', 'market_type': 'OVER_UNDER_15',
           'selection_name': 'Over 1,5 goal', 'handicap': '0',
           'bet_type': 'PUNTA', 'config_json': None}
    parsed = main.elabora_messaggio(
        'P.Bet. PREMACHT 0,5HT\n\U0001F19A Juve v Milan\n@ 1.85', cfg)
    assert parsed is not None
    main.verify_csv(parsed['csv'])
