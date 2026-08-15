"""I dati che una produzione GIA' ESISTENTE ha nel database — fonte unica dei test.

Dal PR «rimozione del seme» (#25, lavoro E) `migra()` non semina piu' niente: il
parser storico e il profilo PIERO sono **righe del database di produzione** su
`/data`, non codice che le ricrea a ogni avvio. Le fixture che modellano quel
servizio devono quindi inserirle da se', PRIMA del primo avvio del relay, cosi'
la migrazione le trova esattamente come troverebbe il volume in produzione.

Una fonte unica (regola 3): prima del seme queste righe vivevano in `migra()`;
copiarle in ogni file di test sarebbe reintrodurre in N copie cio' che la
rimozione ha tolto da una.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

RADICE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RADICE))

import main  # noqa: E402 - dopo l'inserimento del percorso

# Gli stessi valori che il seme scriveva: sono i dati storici del servizio reale.
PARSER_LEGACY = (main.DEFAULT_PARSER, 'P.Bet. PREMACHT 0,5HT', 'Over/Under 1,5 gol',
                 'OVER_UNDER_15', 'Over 1,5 goal', '0', 'PUNTA')


def semina_produzione(percorso, chat_ids=''):
    """Il database come lo lascia la produzione esistente: parser + profilo PIERO.

    Crea le sole tabelle originali (bastano alle due INSERT) e inserisce le righe;
    tutto il resto dello schema lo completa `migra()` al primo avvio, idempotente,
    come farebbe su `/data`. `chat_ids` e' la stringa a virgole del profilo — in
    produzione arrivava da `TELEGRAM_ALLOWED_CHAT_IDS`, nei test la sceglie chi
    chiama.
    """
    c = sqlite3.connect(percorso)
    for istruzione in main.SCHEMA_ORIGINALE:
        c.execute(istruzione)
    c.execute('INSERT OR IGNORE INTO parsers(name,header,market_name,market_type,'
              'selection_name,handicap,bet_type) VALUES (?,?,?,?,?,?,?)', PARSER_LEGACY)
    c.execute('INSERT OR IGNORE INTO profiles(name,chat_ids,parser) VALUES (?,?,?)',
              (main.PIERO_PROFILE, chat_ids, main.DEFAULT_PARSER))
    c.commit()
    c.close()


def relay_in_processo(monkeypatch, percorso, chat_ids='', vergine=False):
    """Il relay IN PROCESSO su un database di produzione simulato.

    Fa le tre cose che ogni test in processo ripeteva a mano — `DB_PATH`,
    l'azzeramento dei percorsi gia' migrati, la prima migrazione — piu' la
    semina, che dalla rimozione del seme non arriva piu' da `migra()`.
    Restituisce il percorso, cosi' il chiamante puo' aprirci una connessione.
    """
    percorso = str(percorso)
    monkeypatch.setattr(main, 'DB_PATH', percorso)
    monkeypatch.setattr(main, '_PERCORSI_MIGRATI', set())
    if not vergine:
        semina_produzione(percorso, chat_ids)
    main.db().close()
    return percorso
