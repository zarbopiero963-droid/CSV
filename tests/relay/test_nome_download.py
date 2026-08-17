"""Il nome del file scaricato e' betrelay, non xtrader (#60).

Chi incolla l'URL del feed in un browser scarica un file, e quel file si
chiamava «xtrader.csv»: il nome lo decideva il browser dall'ultimo segmento
dell'URL. La decisione del proprietario (#60): il download si chiama
`betrelay.csv` sull'alias storico, `betrelay-{slug}.csv` sul feed per-utente,
`betrelay-{profilo}.csv` sui profili nominati. SOLO il nome: URL e byte del
corpo non si muovono — XTrader interroga l'URL e legge il corpo, e un header
in piu' non gli cambia niente. Il corpo e' asserito qui accanto all'header
proprio per questo: se il nome del download costasse un byte del contratto,
questi test diventerebbero rossi.

E il nome viaggia in un header HTTP (latin-1): un nome profilo con virgolette,
CRLF o caratteri non-ASCII deve uscire RIPULITO, non produrre un header rotto
o un 500 sul percorso di consegna — che e' quello che XTrader interroga a
raffica.
"""

from __future__ import annotations

import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE))

import main  # noqa: E402 - dopo l'inserimento del percorso
from tests.ambiente import CHIAVI_PERICOLOSE, TOKEN_DI_PROVA  # noqa: E402
from tests.dati import relay_in_processo  # noqa: E402
from tests.servizio import relay_avviato  # noqa: E402

# Le fixture del feed per-utente e l'ambiente del servizio NON vengono
# ricopiati (regola 3): `_utente_con_feed` scrive la riga di `users` con la
# formula d'hash attesa, `AMBIENTE_DEL_SERVIZIO` e il flusso di login sono gli
# stessi di ogni test che parla col servizio vero.
from tests.relay.test_feed_utente import (  # noqa: E402
    GIORNO, _utente_con_feed)
from tests.relay.test_login import (  # noqa: E402
    AMBIENTE_DEL_SERVIZIO, _chiama, _cookie_dalla_risposta, _dati_login)

import json  # noqa: E402


@pytest.fixture(autouse=True)
def _ambiente_pulito(monkeypatch):
    """Token noto e nessuna variabile della macchina, come nelle suite gemelle."""
    monkeypatch.setattr(main, 'TOKEN', TOKEN_DI_PROVA)
    for chiave in CHIAVI_PERICOLOSE:
        monkeypatch.delenv(chiave, raising=False)


# ------------------------------------------------------ le intestazioni pure

def test_le_intestazioni_della_consegna_sono_una_funzione_sola():
    """Quattro siti di risposta, UNA costruzione (regola 3): niente cache e il
    nome del download, insieme."""
    intestazioni = main._intestazioni_feed('betrelay-piero.csv')
    assert intestazioni['Cache-Control'] == 'no-store'
    assert intestazioni['Content-Disposition'] == \
        'attachment; filename="betrelay-piero.csv"'


def test_un_nome_ostile_esce_ripulito_dalla_funzione():
    """Virgolette, CRLF e non-ASCII collassano in `-`: il valore resta un
    header latin-1 valido e non puo' iniettare ne' chiudere niente."""
    valore = main._intestazioni_feed('betrelay-Cli"ente\r\nà.csv')['Content-Disposition']
    assert valore == 'attachment; filename="betrelay-Cli-ente-.csv"'
    valore.encode('ascii')  # consegnabile in un header senza errori di codifica


# ------------------------------------------------------------- le tre rotte

def test_l_alias_storico_scarica_come_betrelay_csv(tmp_path, monkeypatch):
    """`/xtrader.csv` — l'URL configurato in XTrader non si muove (regola 5):
    cambia solo il nome che un BROWSER da' al file scaricato."""
    relay_in_processo(monkeypatch, tmp_path / 'alias.db')
    r = main.xtrader_csv(token=TOKEN_DI_PROVA)
    assert r.headers.get('content-disposition') == \
        'attachment; filename="betrelay.csv"'
    assert r.headers.get('cache-control') == 'no-store', \
        'il nome del download non deve costare la politica di cache'
    assert bytes(r.body) == main.empty_csv().encode('utf-8'), \
        'il corpo deve restare byte-identico: XTrader legge quello'


def test_il_profilo_nominato_scarica_col_suo_nome(tmp_path, monkeypatch):
    relay_in_processo(monkeypatch, tmp_path / 'profilo.db')
    r = main.named_profile_csv(main.PIERO_PROFILE, token=TOKEN_DI_PROVA)
    assert r.headers.get('content-disposition') == \
        f'attachment; filename="betrelay-{main.PIERO_PROFILE}.csv"'
    assert r.headers.get('cache-control') == 'no-store'


def test_il_feed_utente_scarica_con_lo_slug(tmp_path, monkeypatch):
    percorso = relay_in_processo(monkeypatch, tmp_path / 'feed.db')
    _utente_con_feed(percorso)  # slug='marco'
    r = main.feed_utente_csv('marco', token='xt_token-di-prova-abcdef')
    assert r.headers.get('content-disposition') == \
        'attachment; filename="betrelay-marco.csv"'
    assert r.headers.get('cache-control') == 'no-store'


def test_anche_il_feed_scaduto_porta_il_nome(tmp_path, monkeypatch):
    """Il ramo bloccato serve sola intestazione con `200` (contratto Issue #2):
    il file scaricato deve chiamarsi betrelay anche li', o il nome dipenderebbe
    dallo stato dell'abbonamento."""
    percorso = relay_in_processo(monkeypatch, tmp_path / 'scaduto.db')
    _utente_con_feed(percorso, slug='resto', token='xt_token-scaduto-000000',
                     scadenza=int(time.time()) - GIORNO)
    r = main.feed_utente_csv('resto', token='xt_token-scaduto-000000')
    assert bytes(r.body) == main.empty_csv().encode('utf-8')
    assert r.headers.get('content-disposition') == \
        'attachment; filename="betrelay-resto.csv"'
    assert r.headers.get('cache-control') == 'no-store', \
        'il ramo bloccato non deve perdere la politica di cache (Sourcery, PR #63)'


def test_il_default_del_nome_copre_i_chiamanti_che_non_lo_passano(tmp_path, monkeypatch):
    """`profile_csv` senza `nome_scaricato` (la firma retrocompatibile): il
    default e' `betrelay-{profilo}.csv`, non un header assente (CodeRabbit,
    PR #63)."""
    relay_in_processo(monkeypatch, tmp_path / 'default.db')
    r = main.profile_csv(main.PIERO_PROFILE, TOKEN_DI_PROVA)
    assert r.headers.get('content-disposition') == \
        f'attachment; filename="betrelay-{main.PIERO_PROFILE}.csv"'
    assert r.headers.get('cache-control') == 'no-store'
    assert bytes(r.body) == main.empty_csv().encode('utf-8')


def test_anche_il_profilo_bloccato_porta_il_nome(tmp_path, monkeypatch):
    """Il ramo bloccato di `profile_csv` (accesso scaduto → sola intestazione,
    `200`): stesso nome del download, o il nome dipenderebbe dallo stato
    dell'abbonamento (CodeRabbit, PR #63)."""
    percorso = relay_in_processo(monkeypatch, tmp_path / 'bloccato.db')
    c = sqlite3.connect(percorso)
    c.execute("UPDATE users SET status='attivo', access_expires_at=? "
              'WHERE origin_profile=?',
              (int(time.time()) - GIORNO, main.PIERO_PROFILE))
    assert c.total_changes == 1, 'la migrazione non ha creato l\'utente ponte del profilo'
    c.commit()
    c.close()
    r = main.profile_csv(main.PIERO_PROFILE, TOKEN_DI_PROVA)
    assert bytes(r.body) == main.empty_csv().encode('utf-8'), \
        'l\'accesso scaduto deve degradare a sola intestazione'
    assert r.headers.get('content-disposition') == \
        f'attachment; filename="betrelay-{main.PIERO_PROFILE}.csv"'
    assert r.headers.get('cache-control') == 'no-store'


def test_un_nome_profilo_ostile_esce_ripulito_dalla_rotta(tmp_path, monkeypatch):
    """Il consumatore reale della ripulitura (regola 2-bis): un profilo con
    virgolette e CRLF nel nome attraversa `named_profile_csv` senza produrre
    un header rotto ne' un 500 sul percorso di consegna."""
    percorso = relay_in_processo(monkeypatch, tmp_path / 'ostile.db')
    c = sqlite3.connect(percorso)
    c.execute('INSERT INTO profiles(name,chat_ids,parser) VALUES (?,?,?)',
              ('Cli"ente\r\nà', '', main.DEFAULT_PARSER))
    c.commit()
    c.close()
    r = main.named_profile_csv('Cli"ente\r\nà', token=TOKEN_DI_PROVA)
    assert r.headers.get('content-disposition') == \
        'attachment; filename="betrelay-Cli-ente-.csv"'
    assert bytes(r.body) == main.empty_csv().encode('utf-8')


# ------------------------------------------------------------ i byte HTTP veri

def test_i_byte_http_portano_il_nome_e_il_contratto_intatto(tmp_path):
    """Sul servizio VERO in sottoprocesso: l'header arriva al client com'e'
    scritto, e i byte del corpo cominciano ancora con BOM+intestazione — cioe'
    il nome del download non e' costato niente del contratto CSV."""
    with relay_avviato(tmp_path, **AMBIENTE_DEL_SERVIZIO) as base:
        stato, _, headers = _chiama(base, 'POST', '/api/login/telegram',
                                    corpo=_dati_login(id='998000998'))
        assert stato == 200
        cookie = _cookie_dalla_risposta(headers)
        stato, corpo, _ = _chiama(base, 'POST', '/api/me/token', cookie=cookie)
        assert stato == 200, f'POST /api/me/token risponde {stato}: {corpo[:200]!r}'
        dati = json.loads(corpo)

        nome_feed = dati['feed'].rsplit('/', 1)[-1]          # '{slug}.csv'
        slug = nome_feed[:-len('.csv')]
        url = f"{base}/feed/{nome_feed}?token={dati['token']}"
        with urllib.request.urlopen(url, timeout=10) as r:  # noqa: S310 - loopback
            assert r.status == 200
            assert r.headers.get('Content-Disposition') == \
                f'attachment; filename="betrelay-{slug}.csv"'
            assert r.headers.get('Content-Type', '').startswith('text/csv')
            byte = r.read()
        assert byte.startswith(b'\xef\xbb\xbf"Provider"'), (
            f'i byte HTTP non cominciano con BOM+intestazione: {byte[:24]!r}')

        # E l'alias storico, sullo stesso servizio vero (Sourcery, PR #63):
        # `/xtrader.csv` e' l'URL configurato in XTrader, e la sua serratura e'
        # `CSV_ACCESS_TOKEN` — non il token utente coniato sopra, che su questa
        # rotta non apre niente.
        url_alias = f'{base}/xtrader.csv?token={AMBIENTE_DEL_SERVIZIO["CSV_ACCESS_TOKEN"]}'
        with urllib.request.urlopen(url_alias, timeout=10) as r:  # noqa: S310 - loopback
            assert r.status == 200
            assert r.headers.get('Content-Disposition') == \
                'attachment; filename="betrelay.csv"'
            assert r.headers.get('Content-Type', '').startswith('text/csv')
            byte_alias = r.read()
        assert byte_alias.startswith(b'\xef\xbb\xbf"Provider"'), (
            f'i byte HTTP dell\'alias non cominciano con BOM+intestazione: {byte_alias[:24]!r}')
