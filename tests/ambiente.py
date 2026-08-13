"""Fonte unica dell'ambiente passato ai sottoprocessi che avviano il relay.

Esiste perche' `main.py` registra il webhook Telegram all'avvio: con
`TELEGRAM_BOT_TOKEN` nell'ambiente, avviare il servizio chiama `setWebhook`
verso `PUBLIC_URL`, il cui default e' cablato sull'URL Railway di produzione.
Ereditare l'ambiente intero significa quindi che far girare i test su una
macchina con il `.env` del proprietario **ripunta il webhook del bot vero**.

Per lo stesso motivo va tolto `CSV_ACCESS_TOKEN`: le asserzioni HTTP chiamano
`/xtrader.csv` senza token, quindi con la variabile impostata `auth()` risponde
401 e la suite passa o fallisce a seconda della macchina.

Whitelist e non blacklist di proposito: una blacklist protegge solo dalle
variabili che qualcuno si e' ricordato di elencare, e la prossima variabile
sensibile la aggiungera' chi non sta leggendo questo file. Qui il default e'
«non passa», e ogni aggiunta e' una scelta.

Vive in `tests/` e non dentro un singolo file di test perche' due fixture
diverse avviano `main:app` (`tests/relay/` e `tests/web/`): due copie corrette
oggi sono due copie divergenti domani (regola 3).
"""

from __future__ import annotations

import os

# Variabili che non devono mai raggiungere un sottoprocesso di test. Elencate per
# nome perche' il test che le controlla deve poterle nominare in un fallimento.
CHIAVI_PERICOLOSE = (
    'TELEGRAM_BOT_TOKEN',        # ripunterebbe il webhook di produzione
    'TELEGRAM_ALLOWED_CHAT_IDS', # inietterebbe chat ID reali nel profilo PIERO
    'CSV_ACCESS_TOKEN',          # farebbe rispondere 401 alle richieste senza token
    'PUBLIC_URL',                # destinazione del setWebhook
    'DATABASE_URL',              # quando si passera' a Postgres
    # Le due del PR 6. Ereditarle non fa danno al bot vero, ma rende l'esito
    # dipendente dalla macchina: con `ADMIN_PASSWORD_HASH` del proprietario un test
    # sul percorso a password non potrebbe entrare — non conosce la sua password — e
    # verificherebbe la cosa sbagliata credendo di aver verificato quella giusta. Con
    # `TELEGRAM_ADMIN_ID` reale, un test che afferma «questo utente NON e' admin»
    # passerebbe o fallirebbe secondo chi lo esegue.
    'TELEGRAM_ADMIN_ID',
    'ADMIN_PASSWORD_HASH',
    # Il consenso all'assorbimento della riga vuota, dalla PR #24. Ereditarlo
    # capovolgerebbe l'esito di due test opposti — uno pretende il rifiuto senza
    # consenso, l'altro la riparazione con — quindi con la variabile nell'ambiente il
    # primo diventerebbe rosso su una macchina e verde su un'altra. Una variabile che
    # decide un ramo va portata dal test, non ereditata.
    'TELEGRAM_ADMIN_RECONCILE',
)

# Il token che i test PORTANO, invece di ereditare quello del proprietario.
#
# Da quando `auth()` e' fail-closed, «nessun token configurato» non e' piu' uno
# stato in cui il servizio funziona: e' un 503 su ogni rotta protetta. I test che
# parlano del contratto CSV hanno quindi bisogno di un token valido, e questo e'
# quello — una costante nel sorgente, non un segreto, che non apre nulla di reale.
#
# Vive qui e non in un file di test perche' lo usano sia `tests/relay/` sia chi
# avvia il servizio in sottoprocesso: due copie corrette oggi sono due copie
# divergenti domani (regola 3). E resta separato dalla whitelist: quella dice
# quali variabili NON passano per eredita', questo e' un valore che si passa di
# proposito.
TOKEN_DI_PROVA = 'token-di-prova-non-un-segreto'

# Il minimo perche' `python -m uvicorn` parta e trovi l'interprete e le
# librerie. Tutto il resto e' escluso per default.
CHIAVI_AMMESSE = (
    'PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR',
    'PYTHONPATH', 'PYTHONHOME', 'PYTHONUNBUFFERED',
    'VIRTUAL_ENV', 'SYSTEMROOT',
    # Servono agli script che pilotano il browser: senza, Playwright riscarica
    # Chromium invece di usare quello preinstallato.
    'PLAYWRIGHT_BROWSERS_PATH', 'PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD', 'DISPLAY',
)


def _ripulisci(sorgente):
    return {k: sorgente[k] for k in CHIAVI_AMMESSE if k in sorgente and sorgente[k]}


def ambiente_di_supporto(origine=None, **extra):
    """Come `ambiente_di_servizio`, per un sottoprocesso che NON avvia il relay.

    Gli script che pilotano il browser non registrano webhook, quindi qui non c'e'
    `DB_PATH` da imporre — ma non hanno nessun motivo per ricevere i token di
    produzione, e passano dalla stessa whitelist.
    """
    env = _ripulisci(os.environ if origine is None else origine)
    env.update({k: str(v) for k, v in extra.items()})
    return env


def ambiente_di_servizio(origine=None, **extra):
    """Ambiente ripulito per un sottoprocesso che avvia il relay.

    `origine` serve ai test di questa funzione per passare un ambiente finto;
    in uso normale si omette e si legge `os.environ`.

    `DB_PATH` e' obbligatorio: senza, il servizio scrive in `/tmp/signals.db` —
    il default di produzione — e i test si lasciano segnali dietro.

    Solleva `ValueError` se `DB_PATH` manca, o se un valore da passare e' vuoto.
    """
    env = _ripulisci(os.environ if origine is None else origine)

    if 'DB_PATH' not in extra or not extra['DB_PATH']:
        raise ValueError(
            'DB_PATH e obbligatorio: senza, il relay scrive in /tmp/signals.db, '
            'il database di default, e i test si contaminano fra loro'
        )

    for chiave, valore in extra.items():
        # Passare una chiave pericolosa DI PROPOSITO resta possibile, ma va
        # scritto nel test che lo fa: non deve succedere per eredita'.
        env[chiave] = str(valore)

    return env
