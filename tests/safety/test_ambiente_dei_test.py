"""Guardia: i test non devono poter toccare la produzione.

Il relay registra il webhook Telegram **all'avvio** (`main.py`, evento startup):
se `TELEGRAM_BOT_TOKEN` e' presente nell'ambiente, chiama `setWebhook` verso
`PUBLIC_URL` — che ha un default cablato sull'URL Railway di produzione.

Conseguenza, che non e' teorica: far girare la suite su una macchina dove il
`.env` del proprietario e' caricato **ripunta il webhook del bot vero**. I
segnali dei clienti finirebbero verso l'istanza sbagliata, e nulla nei test
diventerebbe rosso — il fallimento sarebbe silenzioso e in produzione.

Lo stesso ambiente ereditato porta `CSV_ACCESS_TOKEN`, e quello rompe i test in
modo solo apparentemente innocuo: le asserzioni HTTP chiamano `/xtrader.csv`
senza token, quindi con la variabile impostata `auth()` risponde 401 e i test
falliscono su una macchina e passano su un'altra.

Qui si vincola la CLASSE del difetto, non il singolo sito: qualunque fixture che
avvia `main:app` deve passare da `tests.ambiente`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE))

from tests.ambiente import (  # noqa: E402
    CHIAVI_PERICOLOSE, ambiente_di_servizio, ambiente_di_supporto,
)


def test_le_chiavi_pericolose_non_arrivano_al_sottoprocesso():
    """Il cuore della guardia: le variabili di produzione vengono rimosse."""
    finto = {
        'TELEGRAM_BOT_TOKEN': '123456:FINTO-NON-USARE',
        'CSV_ACCESS_TOKEN': 'token-di-produzione-finto',
        'PUBLIC_URL': 'https://esempio-produzione.invalid',
        'TELEGRAM_ALLOWED_CHAT_IDS': 'CHAT-ID-FINTO-NON-REALE',
        'PATH': '/usr/bin:/bin',
        'HOME': '/home/utente',
    }
    env = ambiente_di_servizio(DB_PATH='/tmp/finto.db', origine=finto)

    for chiave in CHIAVI_PERICOLOSE:
        assert chiave not in env, (
            f'{chiave} e- arrivata al sottoprocesso: una suite di test puo- '
            f'ripuntare il webhook di produzione o far fallire l-auth'
        )

    # E il valore non deve comparire nemmeno sotto un altro nome.
    assert '123456:FINTO-NON-USARE' not in '\n'.join(env.values()), \
        'il token e- passato comunque, con un nome diverso'


def test_l_ambiente_resta_utilizzabile():
    """Una whitelist troppo stretta romperebbe l'avvio: PATH serve davvero."""
    env = ambiente_di_servizio(DB_PATH='/tmp/finto.db',
                               origine={'PATH': '/usr/bin:/bin', 'HOME': '/h'})
    assert env['PATH'] == '/usr/bin:/bin', 'senza PATH il sottoprocesso non parte'
    assert env['DB_PATH'] == '/tmp/finto.db', 'la variabile richiesta non e- passata'


def test_il_db_path_e_sempre_imposto():
    """Senza DB_PATH il servizio scrive in `/tmp/signals.db`, il default reale.

    Non e' un dettaglio: un test che scrive nel database di default lascia
    segnali veri dietro di se' e li fa leggere al test successivo.
    """
    import pytest
    with pytest.raises(ValueError, match='DB_PATH'):
        ambiente_di_servizio(origine={'PATH': '/usr/bin'})


def test_anche_l_ambiente_di_supporto_e_ripulito():
    """Gli script del browser non avviano il relay, ma non servono loro i token.

    Condividono la whitelist: una seconda lista da mantenere sarebbe la stessa
    divergenza che la regola 3 vieta.
    """
    finto = {'TELEGRAM_BOT_TOKEN': '123:FINTO', 'CSV_ACCESS_TOKEN': 'x',
             'PATH': '/usr/bin', 'PLAYWRIGHT_BROWSERS_PATH': '/opt/pw-browsers'}
    env = ambiente_di_supporto(origine=finto, PYTHONUNBUFFERED='1')

    for chiave in CHIAVI_PERICOLOSE:
        assert chiave not in env, f'{chiave} passata a un sottoprocesso di supporto'
    assert env['PLAYWRIGHT_BROWSERS_PATH'] == '/opt/pw-browsers', \
        'senza questa Playwright riscarica Chromium invece di usare il preinstallato'
    assert env['PYTHONUNBUFFERED'] == '1'


# --------------------------------------------------------- guardia sulla classe

FIXTURE_CHE_AVVIANO_IL_RELAY = [
    RADICE / 'tests' / 'relay' / 'test_csv_contract.py',
    RADICE / 'tests' / 'web' / 'test_prototype_flow.py',
]


def test_nessuna_fixture_passa_l_ambiente_intero():
    """Regola 2: cercata la classe, non il sito.

    Chi domani aggiunge una fixture che avvia il servizio ricopiando
    `env={**os.environ, ...}` reintroduce il difetto identico. Questo test lo
    ferma prima, e nomina il file.
    """
    colpevoli = []
    for percorso in FIXTURE_CHE_AVVIANO_IL_RELAY:
        testo = percorso.read_text('utf-8')
        for n, riga in enumerate(testo.splitlines(), 1):
            if re.search(r'env\s*=\s*\{\s*\*\*\s*os\.environ', riga):
                colpevoli.append(f'{percorso.relative_to(RADICE)}:{n}: {riga.strip()}')
    assert not colpevoli, (
        'una fixture passa l-ambiente intero al sottoprocesso invece di usare '
        'tests.ambiente:\n  ' + '\n  '.join(colpevoli)
    )


def test_chi_avvia_uvicorn_usa_la_fonte_unica():
    """L'altra faccia: non basta non passare os.environ, va usata la whitelist.

    Senza questo, togliere `env=` del tutto passerebbe il test precedente ed
    erediterebbe comunque l'ambiente intero — che e' esattamente lo stato in cui
    si trovava `tests/web/test_prototype_flow.py`.
    """
    for percorso in FIXTURE_CHE_AVVIANO_IL_RELAY:
        testo = percorso.read_text('utf-8')
        if 'uvicorn' not in testo:
            continue
        assert 'ambiente_di_servizio' in testo, (
            f'{percorso.relative_to(RADICE)} avvia uvicorn senza passare da '
            f'tests.ambiente: erediterebbe TELEGRAM_BOT_TOKEN'
        )


def test_il_relay_registra_il_webhook_all_avvio():
    """Il presupposto della guardia, verificato invece che assunto.

    Se un domani il webhook non venisse piu' registrato all'avvio, questo test
    diventa rosso e le motivazioni scritte qui sopra vanno riscritte — meglio
    che restino vere di quanto restino solo suggestive.
    """
    sorgente = (RADICE / 'main.py').read_text('utf-8')
    assert "@app.on_event('startup')" in sorgente
    assert 'setWebhook' in sorgente, \
        'il relay non registra piu- il webhook all-avvio: aggiornare questa guardia'
    assert 'TELEGRAM_BOT_TOKEN' in sorgente
