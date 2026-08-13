"""Accesso su approvazione: stato, scadenza, richiesta, decisione del proprietario.

E' il PR 7 della Issue #2, e la sua sostanza non e' «aggiungere una schermata»: e' decidere
**chi puo' ricevere segnali**, che e' la stessa domanda dell'autenticazione vista dall'altro
lato. Un difetto qui non da' un errore: da' il feed a chi non ha pagato, o lo toglie a chi ha
pagato senza dirglielo.

Cosa vincolano questi test, e perche' ognuno esiste:

- **la scadenza e' un istante, non un evento.** Nessun processo riscrive `status` a
  mezzanotte, quindi la colonna resta `'attivo'` per sempre e chi la legge direttamente
  mente. Misurato prima della correzione: `GET /api/me` di un cliente scaduto il giorno
  prima rispondeva `stato: attivo`. La conversione vive in `stato_effettivo()`, una sola
  volta, e la usano `/api/me`, il feed e il webhook — se ognuno decidesse da se', il
  pannello direbbe una cosa e il feed un'altra;
- **il caso limite dei rinnovi.** Prorogare di 30 giorni un cliente scaduto da due mesi
  deve dargli 30 giorni **da oggi**, non una scadenza nel passato. E' scritto nella Issue #2
  come trappola da non riscoprire, e senza il secondo ramo di `nuova_scadenza()` il sintomo
  e' di nuovo «pannello attivo, feed vuoto»;
- **la scadenza NON revoca il token.** «Scaduto» e «token revocato» sono stati diversi:
  revocare costringerebbe il cliente a riconfigurare XTrader a ogni rinnovo. Alla scadenza
  il feed risponde `200` con la **sola intestazione**, come «nessun segnale», e **non**
  `401`;
- **il proprietario non e' un cliente.** Il suo accesso non dipende da un'approvazione, e
  un difetto che lo trattasse come tale spegnerebbe il feed che XTrader interroga in
  produzione.
"""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE))

import main  # noqa: E402 - dopo l'inserimento del percorso
from tests.ambiente import CHIAVI_PERICOLOSE  # noqa: E402

# Importati da `test_login.py` e NON ricopiati: la firma del Login Widget, il bot finto e il
# segreto atteso sono la stessa cosa in tutti i test che hanno bisogno di una sessione, e due
# copie divergono al primo cambio del formato (regola 3).
from tests.relay.test_login import (  # noqa: E402
    BOT_FINTO, SEGRETO_ATTESO, _dati_login)

GIORNO = 86400


@pytest.fixture(autouse=True)
def _ambiente_pulito(monkeypatch):
    """Nessuna variabile della macchina entra in questi test.

    Stessa ragione delle fixture gemelle in `tests/relay/`: con il `.env` del proprietario
    caricato l'esito dipenderebbe dalla macchina invece che dal codice, e un test che avvia
    l'app ripunterebbe il webhook del bot vero.
    """
    for chiave in CHIAVI_PERICOLOSE:
        monkeypatch.delenv(chiave, raising=False)
    monkeypatch.setattr(main, 'TOKEN', '')


# ------------------------------------------------------- lo stato che conta adesso

def test_un_cliente_ATTIVO_ma_SCADUTO_non_risulta_attivo():
    """Il difetto misurato prima di `stato_effettivo()`, ridotto alla sua unita'.

    La colonna dice `attivo` perche' nessuno l'ha riscritta: la scadenza e' passata da se'.
    Chi legge la colonna vede un cliente attivo, chi legge la scadenza vede un cliente
    scaduto, e i due pezzi del servizio decidono in modo diverso.
    """
    adesso = 1_000_000
    assert main.stato_effettivo('attivo', adesso - GIORNO, adesso=adesso) == 'scaduto'
    assert main.stato_effettivo('attivo', adesso + GIORNO, adesso=adesso) == 'attivo'


def test_lo_stato_DECISO_a_mano_non_viene_riscritto_dalla_scadenza():
    """`sospeso` e `registrato` non diventano `scaduto` per il passare del tempo.

    Sono decisioni del proprietario, non conseguenze del calendario: se la scadenza le
    sovrascrivesse, un cliente sospeso a mano risulterebbe «scaduto» e il pannello
    perderebbe la sola informazione che diceva perche' e' fuori.
    """
    adesso = 1_000_000
    for stato in ('registrato', 'in_attesa', 'sospeso'):
        assert main.stato_effettivo(stato, adesso - GIORNO, adesso=adesso) == stato
    # E `attivo` senza scadenza resta attivo: e' il proprietario, che non ha un abbonamento.
    assert main.stato_effettivo('attivo', None, adesso=adesso) == 'attivo'


def test_i_giorni_rimasti_arrotondano_per_ECCESSO():
    """A 30 ore dalla scadenza restano 2 giorni, non 1.

    Troncando, l'ultimo giorno di accesso il cliente leggerebbe «0 giorni rimasti» mentre il
    feed funziona ancora: un numero che dice zero su un accesso vivo insegna a non fidarsi
    del numero. E scaduto da' `0`, mai un negativo — «-3 giorni rimasti» non e'
    un'informazione.
    """
    adesso = 1_000_000
    assert main.giorni_rimasti(adesso + 30 * 3600, adesso=adesso) == 2
    assert main.giorni_rimasti(adesso + GIORNO, adesso=adesso) == 1
    assert main.giorni_rimasti(adesso + 60, adesso=adesso) == 1
    assert main.giorni_rimasti(adesso - 3 * GIORNO, adesso=adesso) == 0
    assert main.giorni_rimasti(None, adesso=adesso) is None


# --------------------------------------------------------------- la regola dei rinnovi

def test_i_rinnovi_si_SOMMANO_se_la_scadenza_e_nel_futuro():
    """5 giorni residui + 30 concessi = 35 giorni, non 30.

    Chi rinnova in anticipo non deve perdere i giorni che gli restano, altrimenti il
    cliente impara ad aspettare la scadenza per non essere derubato — e nel frattempo il
    feed gli si spegne.
    """
    adesso = 1_000_000
    fra_cinque = adesso + 5 * GIORNO
    assert main.nuova_scadenza(fra_cinque, 30, adesso=adesso) == adesso + 35 * GIORNO


def test_un_cliente_SCADUTO_riparte_da_OGGI_non_dal_passato():
    """Scaduto da 60 giorni + 30 concessi = 30 giorni **da oggi**.

    Il caso limite della Issue #2: sommando alla scadenza vecchia si otterrebbe un istante
    ancora nel passato, quindi il pannello direbbe «attivo» e il feed sarebbe vuoto. E' il
    ramo che si dimentica, perche' il caso normale funziona senza.
    """
    adesso = 1_000_000
    scaduto_da_due_mesi = adesso - 60 * GIORNO
    nuova = main.nuova_scadenza(scaduto_da_due_mesi, 30, adesso=adesso)
    assert nuova == adesso + 30 * GIORNO, (
        f'la nuova scadenza e- {nuova - adesso} secondi da adesso: sommando alla scadenza '
        'vecchia resta nel passato, e il cliente risulta attivo con il feed vuoto')
    assert main.stato_effettivo('attivo', nuova, adesso=adesso) == 'attivo'


def test_senza_scadenza_precedente_si_parte_da_OGGI():
    """Il primo accesso di un cliente nuovo: `access_expires_at` e' `NULL`."""
    adesso = 1_000_000
    assert main.nuova_scadenza(None, 7, adesso=adesso) == adesso + 7 * GIORNO


# ------------------------------------------------- lo stato arriva davvero all'API

def _cliente(tmp_path, monkeypatch, nome='accesso.db'):
    """Un relay in processo con un cliente registrato, e il suo cookie di sessione."""
    percorso = str(tmp_path / nome)
    monkeypatch.setattr(main, 'DB_PATH', percorso)
    monkeypatch.setattr(main, '_PERCORSI_MIGRATI', set())
    monkeypatch.setattr(main, 'BOT_TOKEN', BOT_FINTO)
    monkeypatch.setattr(main, 'SEGRETO_SESSIONE', SEGRETO_ATTESO)
    monkeypatch.setattr(main, 'TELEGRAM_ADMIN_ID', '')
    risposta = main.login_telegram(main.LoginTelegramIn(**_dati_login(id='555000555')))
    cookie = None
    for pezzo in (risposta.headers.get('set-cookie') or '').split(';'):
        chiave, _, valore = pezzo.strip().partition('=')
        if chiave == main.NOME_COOKIE:
            cookie = valore
    c = sqlite3.connect(percorso)
    utente = c.execute("SELECT id FROM users WHERE telegram_id='555000555'").fetchone()[0]
    c.close()

    class Richiesta:
        cookies = {main.NOME_COOKIE: cookie}

    return percorso, utente, Richiesta()


def test_API_ME_dice_SCADUTO_e_ZERO_giorni_a_un_cliente_scaduto(tmp_path, monkeypatch):
    """Il difetto misurato sull'API vera, non sulla funzione da sola.

    E' la regola 2-bis di `CLAUDE.md`: la funzione giusta non serve a niente se il
    chiamante continua a leggere la colonna. Prima della correzione questa rotta rispondeva
    letteralmente `{'stato': 'attivo', 'accesso_scade': <ieri>}`.
    """
    import json
    percorso, utente, richiesta = _cliente(tmp_path, monkeypatch, 'me_scaduto.db')
    c = sqlite3.connect(percorso)
    c.execute("UPDATE users SET status='attivo', access_expires_at=? WHERE id=?",
              (int(time.time()) - GIORNO, utente))
    c.commit()
    c.close()

    corpo = json.loads(bytes(main.chi_sono(richiesta).body).decode())
    assert corpo['stato'] == 'scaduto', (
        f"la rotta dice stato={corpo['stato']!r} con una scadenza nel passato: il cliente "
        'legge «attivo» e il feed e- vuoto')
    assert corpo['giorni_rimasti'] == 0, (
        f"giorni_rimasti={corpo['giorni_rimasti']!r} invece di 0")


def test_API_ME_riporta_i_giorni_a_un_cliente_ATTIVO(tmp_path, monkeypatch):
    """Il verso opposto, che e' cio' che il cliente vede in dashboard."""
    import json
    percorso, utente, richiesta = _cliente(tmp_path, monkeypatch, 'me_attivo.db')
    c = sqlite3.connect(percorso)
    c.execute("UPDATE users SET status='attivo', access_expires_at=? WHERE id=?",
              (int(time.time()) + 10 * GIORNO, utente))
    c.commit()
    c.close()

    corpo = json.loads(bytes(main.chi_sono(richiesta).body).decode())
    assert corpo['stato'] == 'attivo'
    assert corpo['giorni_rimasti'] == 10, (
        f"giorni_rimasti={corpo['giorni_rimasti']!r} invece di 10")
