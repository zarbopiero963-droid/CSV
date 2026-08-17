"""Il multi-riga dal messaggio al feed (#35 pezzo 2): webhook e prova.

Il motore genera N righe (base + override, `tests/engine/`); qui si vincola il
PERCORSO: il webhook scrive nel feed la lista dei documenti delle righe
complete (il contratto d'ingresso di `store_signal`, #35 pezzo 1), la riga
rotta non ferma le altre e lascia il motivo in `message_logs`, e la prova
HTTP risponde le righe col conteggio «k/N».
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from pathlib import Path

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE))

import pytest  # noqa: E402

import main  # noqa: E402
from tests.ambiente import CHIAVI_PERICOLOSE, TOKEN_DI_PROVA  # noqa: E402
from tests.dati import relay_in_processo  # noqa: E402
from tests.servizio import relay_avviato  # noqa: E402
from tests.relay.test_mercati import (  # noqa: E402
    BOT_FINTO, ADMIN_FINTO, PROXY_MORTO, _chiama, _login)
from tests.relay.test_webhook import CHAT, RichiestaFinta  # noqa: E402


@pytest.fixture(autouse=True)
def _ambiente_pulito(monkeypatch):
    for chiave in CHIAVI_PERICOLOSE:
        monkeypatch.delenv(chiave, raising=False)


def _config_multi(price_rotta=False):
    """Base Over 1,5 + due mercati; il secondo puo' avere la quota rotta."""
    return {
        'match': {'type': 'contains', 'value': 'P.Bet.'},
        'columns': {
            'EventName': {'source': 'line', 'anchor': ' v ', 'part': 'whole',
                          'transforms': [
                              {'op': 'replace_last', 'from': ' v ', 'to': ' - '},
                              {'op': 'trim'}]},
            'MarketType': {'source': 'constant', 'value': 'OVER_UNDER_15'},
            'SelectionName': {'source': 'constant', 'value': 'Over 1,5'},
            'BetType': {'source': 'constant', 'value': 'PUNTA'},
            'Price': {'source': 'constant', 'value': '1.85'}},
        'multi': {
            'markets': [
                {'market_type': 'OVER_UNDER_25', 'selection_name': 'Over 2,5'},
                {'market_type': 'OVER_UNDER_05', 'selection_name': 'Over 0,5',
                 **({'price': 'abc'} if price_rotta else {'price': '1.20'})}],
            'selections': [
                {'selection_name': 'Under 1,5', 'bet_type': 'BANCA'}]},
    }


def _webhook(monkeypatch, tmp_path, config):
    percorso = relay_in_processo(monkeypatch, tmp_path / 'multi.db',
                                 chat_ids=CHAT)
    monkeypatch.setattr(main, 'SEGRETO_WEBHOOK', main.webhook_secret(BOT_FINTO))
    c = sqlite3.connect(percorso)
    c.execute('UPDATE parsers SET config_json=? WHERE name=?',
              (json.dumps(config), main.DEFAULT_PARSER))
    c.commit()
    c.close()
    payload = {'message': {'chat': {'id': int(CHAT)},
                           'text': 'P.Bet.\nJuve v Milan\n@ 1.85'}}
    risposta = asyncio.run(main.telegram_webhook(RichiestaFinta(
        {'X-Telegram-Bot-Api-Secret-Token': main.webhook_secret(BOT_FINTO)},
        payload)))
    return percorso, risposta


def test_il_webhook_scrive_TRE_righe_nel_feed(tmp_path, monkeypatch):
    """3 righe attive → 3 record in signals e un feed composto: BOM e
    intestazione UNA volta, tre data line, byte per byte."""
    percorso, risposta = _webhook(monkeypatch, tmp_path, _config_multi())
    assert risposta.get('event') == 'Juve - Milan', risposta
    c = sqlite3.connect(percorso)
    documenti = [r[0] for r in c.execute(
        'SELECT csv FROM signals ORDER BY id').fetchall()]
    c.close()
    assert len(documenti) == 3, f'attesi 3 record, trovati {len(documenti)}'
    composto = main.componi_feed(documenti)
    assert composto.count('\ufeff') == 1
    assert composto.count('"Provider"') == 1
    assert composto.count('\r\n') == 4      # intestazione + 3 righe
    assert '"OVER_UNDER_25"' in composto and '"OVER_UNDER_05"' in composto
    assert '"Under 1,5"' in composto and '"BANCA"' in composto
    assert '"1,20"' in composto, 'la quota della riga, localizzata'
    main.verify_csv(composto)


def test_la_riga_rotta_non_ferma_le_altre_e_lascia_il_motivo(tmp_path, monkeypatch):
    """Tranello 1 sul percorso vivo: quota 'abc' su una riga → le altre DUE
    righe arrivano nel feed, e il motivo della rotta sta in message_logs."""
    percorso, risposta = _webhook(monkeypatch, tmp_path,
                                  _config_multi(price_rotta=True))
    assert risposta.get('event') == 'Juve - Milan', risposta
    c = sqlite3.connect(percorso)
    documenti = [r[0] for r in c.execute('SELECT csv FROM signals').fetchall()]
    esiti = [r[0] for r in c.execute('SELECT esito FROM message_logs').fetchall()]
    c.close()
    assert len(documenti) == 2, f'attese 2 righe buone, trovate {len(documenti)}'
    assert not any('OVER_UNDER_05' in d for d in documenti), 'la rotta non entra'
    assert any(e.startswith('avviso:') and 'abc' in e for e in esiti), (
        f'il motivo della riga rotta deve stare nei log: {esiti}')


def test_la_riga_con_obbligatoria_mancante_lascia_il_motivo_nei_log(tmp_path, monkeypatch):
    """Una riga puo' fallire per `missing`, senza scarti: anche quel motivo va
    nei log del k/N, o la riga sparisce in silenzio (rischio segnalato da
    GPT-5.5 sulla PR #69). Base SENZA selezione: i mercati la portano, la
    MultiSelection che non la sovrascrive eredita il vuoto e cade per
    missing, non per una guardia."""
    config = _config_multi()
    del config['columns']['SelectionName']
    config['multi'] = {
        'markets': [
            {'market_type': 'OVER_UNDER_25', 'selection_name': 'Over 2,5'}],
        'selections': [{'bet_type': 'BANCA'}]}
    percorso, risposta = _webhook(monkeypatch, tmp_path, config)
    assert risposta.get('event') == 'Juve - Milan', risposta
    c = sqlite3.connect(percorso)
    documenti = [r[0] for r in c.execute('SELECT csv FROM signals').fetchall()]
    esiti = [r[0] for r in c.execute('SELECT esito FROM message_logs').fetchall()]
    c.close()
    assert len(documenti) == 1, f'solo la riga col mercato: {len(documenti)}'
    assert any(e.startswith('avviso:') and 'riga 2' in e
               and 'SelectionName' in e for e in esiti), (
        f'la riga caduta per missing deve lasciare il motivo nei log: {esiti}')


def test_nessuna_riga_completa_e_nessun_segnale(tmp_path, monkeypatch):
    """Tutte le righe rotte → niente nel feed, motivo nei log, non un 500."""
    config = _config_multi()
    config['multi'] = {'markets': [
        {'market_type': 'OVER_UNDER_25', 'selection_name': 'Over 2,5',
         'price': 'abc'}], 'selections': []}
    percorso, risposta = _webhook(monkeypatch, tmp_path, config)
    assert 'event' not in risposta, risposta
    c = sqlite3.connect(percorso)
    documenti = c.execute('SELECT csv FROM signals').fetchall()
    c.close()
    assert not documenti, 'nessuna riga completa = nessun segnale scritto'


def _respinta(config):
    with pytest.raises(main.HTTPException) as e:
        main._valida_config_parser(config)
    assert e.value.status_code == 422
    return e.value.detail


def test_la_config_multi_storta_e_respinta_alla_scrittura():
    """Il confine di scrittura (#35 pezzo 2): la forma sbagliata da' un 422 col
    motivo, non un comportamento muto a runtime — stessa lezione della #41."""
    buona = _config_multi()
    main._valida_config_parser(buona)          # la buona passa

    rotta = dict(buona, multi=[])
    assert 'multi' in _respinta(rotta)

    rotta = dict(buona, multi={'markets': {}})
    assert 'markets' in _respinta(rotta)

    # Chiave inventata al livello di multi: `markes` verrebbe ignorata dal
    # motore — righe salvate, zero generate, nessun messaggio. Stesso caso
    # muto delle chiavi di riga (segnalato da CodeRabbit sulla PR #69).
    rotta = dict(buona, multi={'markes': []})
    dettaglio = _respinta(rotta)
    assert 'markes' in dettaglio and 'markets' in dettaglio, dettaglio

    rotta = dict(buona, multi={'markets': ['x']})
    assert 'oggetto' in _respinta(rotta)

    # Chiave inventata: `pricce` verrebbe ignorata in silenzio dal motore —
    # il caso muto della #41, identico sulle righe.
    rotta = dict(buona, multi={'markets': [{'pricce': '2.5'}]})
    dettaglio = _respinta(rotta)
    assert 'pricce' in dettaglio and 'price' in dettaglio, dettaglio

    # `enabled` stringa: in JS `'false' !== false` e la riga resterebbe
    # ATTIVA — l'opposto di cio' che l'utente credeva di scrivere.
    rotta = dict(buona, multi={'markets': [{'enabled': 'false'}]})
    assert 'enabled' in _respinta(rotta)

    # Un valore non scalare non ha una forma canonica condivisa dai due motori.
    rotta = dict(buona, multi={'markets': [{'price': ['2.5']}]})
    assert 'price' in _respinta(rotta)


def test_il_tetto_delle_righe_multi():
    """Piu' righe del tetto = 422 col limite nel messaggio, non un feed gonfio."""
    config = _config_multi()
    config['multi'] = {'markets': [
        {'market_type': f'M_{i}', 'selection_name': f'S {i}'}
        for i in range(main.MAX_RIGHE_MULTI + 1)], 'selections': []}
    dettaglio = _respinta(config)
    assert str(main.MAX_RIGHE_MULTI) in dettaglio, dettaglio


AMBIENTE_DEL_SERVIZIO = {
    'CSV_ACCESS_TOKEN': TOKEN_DI_PROVA,
    'TELEGRAM_BOT_TOKEN': BOT_FINTO,
    'TELEGRAM_ADMIN_ID': ADMIN_FINTO,
    'PUBLIC_URL': 'https://non-esiste.invalid',
    'HTTPS_PROXY': PROXY_MORTO,
    'https_proxy': PROXY_MORTO,
}


@pytest.fixture(scope='module')
def servizio(tmp_path_factory):
    with relay_avviato(tmp_path_factory.mktemp('multiriga'),
                       **AMBIENTE_DEL_SERVIZIO) as base:
        yield base


def test_la_prova_HTTP_risponde_le_righe_col_conteggio(servizio):
    """La prova mostra il «k/N» del tranello 1: righe con esito PER RIGA e il
    CSV composto delle sole complete — gli stessi byte del feed."""
    cookie = _login(servizio, '777000333')
    stato, corpo, _ = _chiama(servizio, 'POST', '/api/me/parsers',
                              corpo={'titolo': 'Multi',
                                     'config': _config_multi(price_rotta=True)},
                              cookie=cookie)
    assert stato == 200, corpo
    slug = json.loads(corpo)['slug']
    stato, corpo, _ = _chiama(servizio, 'POST', f'/api/me/parsers/{slug}/test',
                              corpo={'message': 'P.Bet.\nJuve v Milan\n@ 1.85'},
                              cookie=cookie)
    assert stato == 200, corpo
    esito = json.loads(corpo)
    assert len(esito['righe']) == 3, esito
    piazzabili = [r for r in esito['righe'] if r['complete']]
    assert len(piazzabili) == 2, 'k/N: 2 piazzabili su 3'
    rotta = next(r for r in esito['righe'] if not r['complete'])
    assert any('abc' in s for s in rotta['scarti']), rotta
    assert esito['csv'].count('"Provider"') == 1, 'CSV composto, header unico'
    assert esito['csv'].count('\r\n') == 3, 'intestazione + 2 righe buone'
