"""Il token del feed NON deve comparire nell'access-log di uvicorn (audit #81, A1).

Il feed di XTrader e' `GET /feed/{slug}.csv?token=<segreto>` (e l'alias
`/xtrader.csv?token=<CSV_ACCESS_TOKEN>`): il token viaggia nel query string, e
l'access-log di uvicorn formatta `full_path` — cioe' `args[2]` del record — che
lo contiene. Senza redazione, ogni poll di XTrader scrive il token in chiaro nei
log del container (viola la priorita' #9 di CLAUDE.md e «Non stampare token di
feed nei log»). Misurato avviando il relay reale nell'audit #81.

Questi test esercitano le funzioni REALI di `main` (`_redigi_token_log` e
`installa_redazione_access_log`) e, per l'integrazione, il vero `AccessFormatter`
di uvicorn — lo stesso che gira in produzione.
"""

from __future__ import annotations

import io
import logging
import sys
from pathlib import Path

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE))

import main  # noqa: E402 - dopo l'inserimento del percorso

from uvicorn.logging import AccessFormatter  # noqa: E402


def _formatta_come_uvicorn(logger, full_path):
    """Emette un record identico a quello di uvicorn e ne cattura la riga.

    uvicorn costruisce `record.args = (client_addr, method, full_path,
    http_version, status_code)`; `AccessFormatter` ne ricava `request_line`.
    """
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s'))
    logger.addHandler(handler)
    try:
        record = logger.makeRecord(
            'uvicorn.access', logging.INFO, __file__, 0,
            '%s - "%s %s HTTP/%s" %d',
            ('127.0.0.1:5000', 'GET', full_path, '1.1', 200), None)
        logger.handle(record)
    finally:
        logger.removeHandler(handler)
    return buf.getvalue()


def test_la_redazione_toglie_solo_il_valore_del_token():
    """Unita' pura: `token=<valore>` -> `token=[REDACTED]`, il resto intatto."""
    assert main._redigi_token_log(
        '/feed/piero.csv?token=xt_SEGRETISSIMO_123') == '/feed/piero.csv?token=[REDACTED]'
    # il valore sparisce ma i parametri successivi restano
    assert main._redigi_token_log(
        '/xtrader.csv?token=abc999&x=1') == '/xtrader.csv?token=[REDACTED]&x=1'
    # nessun token: la stringa non cambia
    assert main._redigi_token_log('/health') == '/health'
    # non stringa (status int fra gli args): passa intatto
    assert main._redigi_token_log(200) == 200


def test_l_access_log_di_uvicorn_non_espone_il_token_del_feed():
    """Integrazione col vero AccessFormatter: dopo l'installazione della
    redazione, la riga di access-log del feed non contiene piu' il token."""
    logger = logging.getLogger('uvicorn.access')
    filtri_prima = list(logger.filters)
    main.installa_redazione_access_log()
    try:
        riga = _formatta_come_uvicorn(logger, '/feed/piero.csv?token=xt_SEGRETISSIMO_123')
        assert 'xt_SEGRETISSIMO_123' not in riga, (
            'il token del feed e\' finito in chiaro nell\'access-log: ' + riga)
        assert 'token=[REDACTED]' in riga, riga
        # l'alias legacy condivide lo stesso parametro
        riga2 = _formatta_come_uvicorn(logger, '/xtrader.csv?token=CSV_TOKEN_REALE')
        assert 'CSV_TOKEN_REALE' not in riga2 and 'token=[REDACTED]' in riga2, riga2
        # una richiesta senza token resta leggibile per intero
        riga3 = _formatta_come_uvicorn(logger, '/health')
        assert '/health' in riga3 and '[REDACTED]' not in riga3, riga3
    finally:
        logger.filters[:] = filtri_prima


def test_l_import_di_main_installa_gia_la_redazione():
    """La redazione e' attiva gia' a IMPORT del modulo, non solo allo startup.

    Segnalato da Claude Fable 5 sulla PR #82: un filtro legato al solo handler di
    startup sarebbe saltabile se un handler registrato prima sollevasse, o se l'app
    venisse montata senza eseguire il lifespan — uvicorn potrebbe loggare una
    richiesta col token prima che il filtro esista. `main.py` chiama
    `installa_redazione_access_log()` anche a livello di modulo: importare `main`
    (gia' fatto in cima a questo file) deve bastare.
    """
    nostri = [f for f in logging.getLogger('uvicorn.access').filters
              if type(f).__name__ == 'RedazioneTokenAccessLog']
    assert nostri, 'importare main deve gia\' aver installato la redazione, senza startup'


def test_la_redazione_sopravvive_alla_config_logging_di_uvicorn():
    """La redazione, installata a import, deve sopravvivere al `dictConfig` che
    uvicorn applica al bootstrap.

    E' la proprieta' su cui poggia l'installazione a import-time: se il
    `LOGGING_CONFIG` di uvicorn azzerasse i filtri di `uvicorn.access`, un token
    potrebbe passare in chiaro prima che lo startup reinstalli il filtro. Se un
    domani uvicorn cambiasse quel comportamento, questo test diventa rosso invece
    di lasciare il difetto silenzioso. Scenario segnalato da GPT-5.5 su #82.
    """
    import logging.config

    import uvicorn.config

    logger = logging.getLogger('uvicorn.access')
    main.installa_redazione_access_log()
    filtri, handler = list(logger.filters), list(logger.handlers)
    try:
        logging.config.dictConfig(uvicorn.config.LOGGING_CONFIG)
        nostri = [f for f in logger.filters
                  if type(f).__name__ == 'RedazioneTokenAccessLog']
        assert nostri, ("il dictConfig di uvicorn ha cancellato la redazione: "
                        "l'installazione a import non sarebbe piu' sufficiente")
    finally:
        logger.filters[:], logger.handlers[:] = filtri, handler


def test_installa_redazione_e_idempotente():
    """Chiamarla due volte non impila due filtri sullo stesso logger."""
    logger = logging.getLogger('uvicorn.access')
    filtri_prima = list(logger.filters)
    try:
        main.installa_redazione_access_log()
        main.installa_redazione_access_log()
        nostri = [f for f in logger.filters
                  if type(f).__name__ == 'RedazioneTokenAccessLog']
        assert len(nostri) == 1
    finally:
        logger.filters[:] = filtri_prima
