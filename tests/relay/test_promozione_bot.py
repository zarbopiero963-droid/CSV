"""Collegare una chat promuovendo il bot ad amministratore — Issue #116.

**Perche' questo meccanismo esiste, e perche' e' migliore di quello che sostituisce.**
Il codice usa-e-getta (#112) dimostra che chi lo presenta **puo' scrivere** in quella
chat. In un canale coincide col controllarlo — su Telegram in un canale scrivono solo
gli amministratori — ma in un **gruppo** scrive qualunque membro, quindi un membro
ordinario puo' rivendicare il gruppo del titolare (`[REAL_FINDING]` di OpenRouter Sol,
Issue #115).

Promuovere un bot ad amministratore, invece, Telegram lo consente **solo a chi e' gia'
amministratore** con il diritto di nominarne altri. E `my_chat_member` porta `from`,
cioe' l'identita' di chi l'ha fatto, attestata da Telegram. La prova di ruolo che la
#115 chiedeva di costruire con `getChatMember` arriva quindi **gratis**, senza nessuna
chiamata in uscita e senza i suoi modi di fallire.

**Il pericolo che questi test tengono.** Questa diventa la **seconda** eccezione al
filtro delle chat, che `CLAUDE.md` elenca fra le aree da non indebolire. I test
verificano che l'eccezione sia esattamente questa e niente di piu':

- registra una riga in `chats` e aggiorna `bot_stato`; non tocca `signals`, non cerca
  parser, non scrive in `message_logs`;
- chi promuove deve essere un utente del servizio **e** avere l'accesso attivo;
- una chat gia' di un altro utente **non e' rubabile**, come col codice;
- una chat **privata** non registra niente: li' il ruolo di amministratore non esiste;
- la retrocessione **non cancella** la chat ne' i suoi link — li marca, cosi' rimettere
  il bot fa tornare tutto senza riconfigurare niente (decisione del proprietario).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE))

import main  # noqa: E402 - dopo l'inserimento del percorso
from tests.ambiente import CHIAVI_PERICOLOSE, TOKEN_DI_PROVA  # noqa: E402
from tests.servizio import relay_avviato  # noqa: E402

BOT_FINTO = '123456789:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'
ADMIN_FINTO = '987654321'
CLIENTE_A = '555000111'
CLIENTE_B = '555000222'
ESTRANEO = '555000999'
PROXY_MORTO = 'http://127.0.0.1:1'

CANALE_A = '-1002000000101'
CANALE_B = '-1002000000102'

SEGRETO_ATTESO = hashlib.sha256(('betrelay-sessione-v1:' + BOT_FINTO).encode()).hexdigest()

AMBIENTE_DEL_SERVIZIO = {
    'CSV_ACCESS_TOKEN': TOKEN_DI_PROVA,
    'TELEGRAM_BOT_TOKEN': BOT_FINTO,
    'TELEGRAM_ADMIN_ID': ADMIN_FINTO,
    'PUBLIC_URL': 'https://non-esiste.invalid',
    'HTTPS_PROXY': PROXY_MORTO,
    'https_proxy': PROXY_MORTO,
}


@pytest.fixture(autouse=True)
def _ambiente_pulito(monkeypatch):
    for chiave in CHIAVI_PERICOLOSE:
        monkeypatch.delenv(chiave, raising=False)
    monkeypatch.setattr(main, 'TOKEN', TOKEN_DI_PROVA)


@pytest.fixture
def servizio(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'SEGRETO_SESSIONE', SEGRETO_ATTESO)
    with relay_avviato(tmp_path, **AMBIENTE_DEL_SERVIZIO) as base:
        yield base, tmp_path / 'signals.db'


# ------------------------------------------------------------------- utilita'

def _firma_telegram(campi: dict) -> str:
    stringa = '\n'.join(f'{k}={campi[k]}' for k in sorted(campi) if k != 'hash')
    chiave = hashlib.sha256(BOT_FINTO.encode()).digest()
    return hmac.new(chiave, stringa.encode(), hashlib.sha256).hexdigest()


def _login(base, telegram_id, nome):
    campi = {'id': telegram_id, 'first_name': nome, 'username': nome.lower(),
             'auth_date': str(int(time.time()))}
    campi['hash'] = _firma_telegram(campi)
    req = urllib.request.Request(
        f'{base}/api/login/telegram', data=json.dumps(campi).encode(),
        headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 - loopback
        corpo = json.loads(r.read())
        intestazioni = r.headers
    cookie = None
    for grezzo in (intestazioni.get_all('Set-Cookie') or []):
        for pezzo in grezzo.split(';'):
            k, _, v = pezzo.strip().partition('=')
            if k == main.NOME_COOKIE:
                cookie = v
    return cookie, corpo['utente']


def _attiva(percorso_db, telegram_id):
    c = sqlite3.connect(percorso_db)
    c.execute("UPDATE users SET status='attivo', access_expires_at=NULL"
              ' WHERE telegram_id=?', (telegram_id,))
    c.commit()
    c.close()


def _utente_attivo(base, percorso_db, telegram_id, nome):
    esito = _login(base, telegram_id, nome)
    _attiva(percorso_db, telegram_id)
    return esito


def _promozione(base, chat_id, attore, stato='administrator', titolo='Segnali Serie A',
                tipo='channel', update_id=None):
    """Un `my_chat_member` autentico: `from` e' CHI ha cambiato lo stato del bot."""
    aggiornamento = {
        'from': {'id': int(attore)},
        'chat': {'id': int(chat_id), 'title': titolo, 'type': tipo},
        'new_chat_member': {'status': stato},
    }
    payload = {'my_chat_member': aggiornamento}
    if update_id is not None:
        payload['update_id'] = update_id
    req = urllib.request.Request(
        f'{base}/telegram/webhook', data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json',
                 'X-Telegram-Bot-Api-Secret-Token': main.webhook_secret(BOT_FINTO)},
        method='POST')
    with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 - loopback
        return r.status, json.loads(r.read())


def _chats(base, cookie):
    req = urllib.request.Request(
        f'{base}/api/chats', headers={'Cookie': f'{main.NOME_COOKIE}={cookie}'})
    with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 - loopback
        return json.loads(r.read())


def _righe_chat(percorso_db):
    c = sqlite3.connect(percorso_db)
    righe = c.execute('SELECT telegram_chat_id, owner_user_id, title, type, bot_stato'
                      ' FROM chats').fetchall()
    c.close()
    return righe


# ------------------------------------------------------- il giro che deve funzionare

def test_promuovere_il_bot_collega_la_chat_a_chi_lo_ha_promosso(servizio):
    """Il percorso felice, e la prova di ruolo che il codice usa-e-getta non ha.

    Nessun codice da copiare, nessun incollaggio: l'utente aggiunge il bot come
    amministratore e la chat compare fra le sue. Telegram attesta chi l'ha fatto.
    """
    base, percorso_db = servizio
    cookie, _ = _utente_attivo(base, percorso_db, CLIENTE_A, 'ClienteA')

    stato, corpo = _promozione(base, CANALE_A, CLIENTE_A)
    assert stato == 200, corpo

    chat = _chats(base, cookie)
    assert len(chat) == 1, chat
    assert chat[0]['telegram_chat_id'] == CANALE_A
    assert chat[0]['titolo'] == 'Segnali Serie A'
    assert chat[0]['tipo'] == 'channel'


def test_funziona_anche_in_un_GRUPPO_ed_e_il_motivo_per_cui_esiste(servizio):
    """Il caso che il codice usa-e-getta non sa provare (#115).

    In un gruppo scrive qualunque membro, quindi incollare un codice non dimostra
    niente sul controllo. Promuovere il bot invece si': Telegram lo consente solo a
    un amministratore.
    """
    base, percorso_db = servizio
    cookie, _ = _utente_attivo(base, percorso_db, CLIENTE_A, 'ClienteA')

    _promozione(base, CANALE_A, CLIENTE_A, titolo='Gruppo segnali', tipo='supergroup')

    chat = _chats(base, cookie)
    assert len(chat) == 1, chat
    assert chat[0]['tipo'] == 'supergroup'


def test_il_creatore_vale_come_amministratore(servizio):
    base, percorso_db = servizio
    cookie, _ = _utente_attivo(base, percorso_db, CLIENTE_A, 'ClienteA')

    _promozione(base, CANALE_A, CLIENTE_A, stato='creator')

    assert len(_chats(base, cookie)) == 1


# --------------------------------------------------------- chi NON puo' collegare

def test_chi_non_e_un_utente_del_servizio_non_collega_niente(servizio):
    """Un estraneo promuove il bot in una chat sua: non c'e' nessun proprietario da
    scrivere, e non si inventa. Va ignorato in silenzio."""
    base, percorso_db = servizio
    _utente_attivo(base, percorso_db, CLIENTE_A, 'ClienteA')

    stato, corpo = _promozione(base, CANALE_A, ESTRANEO)
    assert stato == 200, corpo

    assert _righe_chat(percorso_db) == [], _righe_chat(percorso_db)


def test_un_utente_REGISTRATO_non_collega_niente(servizio):
    """Stesso cancello del codice usa-e-getta: collegare una chat richiede un accesso
    ATTIVO. Senza, la catena «mi registro → creo un parser → collego un canale →
    ricevo segnali» sarebbe percorribile senza che nessuno mi abbia attivato."""
    base, percorso_db = servizio
    _login(base, CLIENTE_A, 'ClienteA')   # niente `_attiva`: resta `registrato`

    _promozione(base, CANALE_A, CLIENTE_A)

    assert _righe_chat(percorso_db) == []


def test_in_una_chat_privata_non_si_collega_niente(servizio):
    """In una privata il ruolo di amministratore non esiste, quindi non c'e' nessuna
    prova da raccogliere."""
    base, percorso_db = servizio
    _utente_attivo(base, percorso_db, CLIENTE_A, 'ClienteA')

    _promozione(base, CLIENTE_A, CLIENTE_A, tipo='private', titolo=None)

    assert _righe_chat(percorso_db) == []


def test_una_chat_di_un_altro_utente_non_e_rubabile(servizio):
    """Stessa regola del codice: chi promuove il bot in un canale gia' registrato da
    un altro non se lo porta via. Vale anche perche' due persone possono essere
    entrambe amministratrici della stessa chat."""
    base, percorso_db = servizio
    cookie_a, _ = _utente_attivo(base, percorso_db, CLIENTE_A, 'ClienteA')
    cookie_b, _ = _utente_attivo(base, percorso_db, CLIENTE_B, 'ClienteB')

    _promozione(base, CANALE_A, CLIENTE_A)
    _promozione(base, CANALE_A, CLIENTE_B)

    assert len(_chats(base, cookie_a)) == 1, 'A ha perso la sua chat'
    assert _chats(base, cookie_b) == [], 'B si e- preso la chat di A'


# ------------------------------------------------------------- la retrocessione

def test_la_retrocessione_marca_la_chat_e_non_la_cancella(servizio):
    """Il bot tolto o retrocesso: il servizio non legge piu' quella chat.

    La riga NON si cancella e i link ai parser restano: rimettere il bot deve far
    tornare tutto senza riconfigurare niente. Cancellarla butterebbe via il lavoro
    di configurazione per una retrocessione magari temporanea, o fatta da un altro
    amministratore. Decisione del proprietario sulla Issue #116.
    """
    base, percorso_db = servizio
    cookie, _ = _utente_attivo(base, percorso_db, CLIENTE_A, 'ClienteA')
    _promozione(base, CANALE_A, CLIENTE_A)

    _promozione(base, CANALE_A, CLIENTE_A, stato='member')

    righe = _righe_chat(percorso_db)
    assert len(righe) == 1, righe
    assert righe[0][4] == 'member', f'bot_stato non aggiornato: {righe}'
    assert len(_chats(base, cookie)) == 1, 'la chat e- sparita dalla lista'


@pytest.mark.parametrize('stato', ['left', 'kicked', 'restricted'])
def test_ogni_modo_di_perdere_il_bot_viene_registrato(servizio, stato):
    base, percorso_db = servizio
    _utente_attivo(base, percorso_db, CLIENTE_A, 'ClienteA')
    _promozione(base, CANALE_A, CLIENTE_A)

    _promozione(base, CANALE_A, CLIENTE_A, stato=stato)

    assert _righe_chat(percorso_db)[0][4] == stato


def test_rimettere_il_bot_riporta_la_chat_operativa(servizio):
    base, percorso_db = servizio
    _utente_attivo(base, percorso_db, CLIENTE_A, 'ClienteA')
    _promozione(base, CANALE_A, CLIENTE_A)
    _promozione(base, CANALE_A, CLIENTE_A, stato='kicked')

    _promozione(base, CANALE_A, CLIENTE_A, stato='administrator')

    assert _righe_chat(percorso_db)[0][4] == 'administrator'


def test_la_retrocessione_di_una_chat_che_non_conosciamo_non_crea_righe(servizio):
    """Il bot tolto da una chat mai registrata: non c'e' niente da marcare, e non si
    crea una riga per dire che non c'e' il bot."""
    base, percorso_db = servizio
    _utente_attivo(base, percorso_db, CLIENTE_A, 'ClienteA')

    _promozione(base, CANALE_B, CLIENTE_A, stato='kicked')

    assert _righe_chat(percorso_db) == []


# ------------------------------------------- l'eccezione e' esattamente questa

def test_la_promozione_non_apre_il_feed(servizio):
    """`CLAUDE.md`: il filtro delle chat non si indebolisce. Questo ramo registra una
    riga in `chats` e niente altro — non tocca `signals`, non cerca parser, non
    scrive in `message_logs`."""
    base, percorso_db = servizio
    _utente_attivo(base, percorso_db, CLIENTE_A, 'ClienteA')

    _promozione(base, CANALE_A, CLIENTE_A)

    c = sqlite3.connect(percorso_db)
    segnali = c.execute('SELECT COUNT(*) FROM signals').fetchone()[0]
    log = c.execute('SELECT COUNT(*) FROM message_logs').fetchone()[0]
    link = c.execute('SELECT COUNT(*) FROM parser_chats').fetchone()[0]
    c.close()
    assert (segnali, log, link) == (0, 0, 0), (segnali, log, link)


# ------------------------------------ il conflitto col canale di backup (#56)

def test_il_canale_privato_dell_admin_si_collega_E_resta_candidato_backup(servizio):
    """Il conflitto trovato in Phase 0, e la decisione del proprietario.

    `_cattura_canale_backup` intercetta i `my_chat_member` e si ferma quando chi
    promuove e' l'amministratore, la chat e' un canale privato e il bot diventa
    amministratore: esattamente il caso in cui il proprietario promuove il bot in un
    proprio canale di SEGNALI. Prima di questa Issue quel canale sarebbe diventato
    solo una proposta di backup, e la chat non si sarebbe collegata — cioe' il
    meccanismo nuovo non avrebbe funzionato proprio per l'unico utente attuale.

    Decisione: i due effetti non si escludono. La chat si collega **e** la proposta
    di backup resta, che e' solo una proposta e va confermata a mano nel pannello.
    """
    base, percorso_db = servizio
    cookie, _ = _utente_attivo(base, percorso_db, ADMIN_FINTO, 'Piero')

    _promozione(base, CANALE_A, ADMIN_FINTO, tipo='channel')

    assert len(_chats(base, cookie)) == 1, 'la chat dell-admin non si e- collegata'
    c = sqlite3.connect(percorso_db)
    candidato = c.execute('SELECT valore FROM impostazioni WHERE chiave=?',
                          (main.CHIAVE_CANALE_CANDIDATO_ID,)).fetchone()
    c.close()
    assert candidato and candidato[0] == CANALE_A, (
        f'la proposta di canale di backup e- andata persa: {candidato}')


# ------------------------------------------------- l'ordine delle consegne

def test_una_riconsegna_vecchia_non_resuscita_il_bot_amministratore(servizio):
    """Gli `update_id` di Telegram crescono, ma le consegne possono arrivare fuori ordine.

    Senza un ordinamento, una riconsegna tardiva della PROMOZIONE dopo la rimozione
    riscriverebbe `bot_stato` ad `administrator`: la chat risulterebbe operativa mentre
    il bot non c'e' piu'. `[REAL_FINDING]` di OpenRouter Sol sulla PR #117 — e il
    precedente esatto e' in questo stesso file, `_cattura_canale_backup`, dove
    l'high-water-mark per canale era stato aggiunto per la stessa ragione (#56, Sol B1).
    Averlo mancato e' regola 2: il difetto era gia' noto, in un'altra funzione.
    """
    base, percorso_db = servizio
    _utente_attivo(base, percorso_db, CLIENTE_A, 'ClienteA')
    _promozione(base, CANALE_A, CLIENTE_A, update_id=10)
    _promozione(base, CANALE_A, CLIENTE_A, stato='kicked', update_id=11)

    # La stessa promozione riconsegnata: e' vecchia, non deve tornare indietro.
    _promozione(base, CANALE_A, CLIENTE_A, update_id=10)

    assert _righe_chat(percorso_db)[0][4] == 'kicked', _righe_chat(percorso_db)


def test_una_promozione_tardiva_su_una_chat_GIA_NOSTRA_non_la_ripristina(servizio):
    """Stessa corsa, sull'altro effetto: dopo la rimozione, una promozione piu' vecchia
    non deve riportare la chat a operativa."""
    base, percorso_db = servizio
    _utente_attivo(base, percorso_db, CLIENTE_A, 'ClienteA')
    _promozione(base, CANALE_A, CLIENTE_A, update_id=18)
    _promozione(base, CANALE_A, CLIENTE_A, stato='kicked', update_id=20)

    _promozione(base, CANALE_A, CLIENTE_A, update_id=19)

    assert _righe_chat(percorso_db)[0][4] == 'kicked', _righe_chat(percorso_db)


def test_LIMITE_NOTO_una_rimozione_su_una_chat_mai_vista_non_lascia_segno(servizio):
    """Comportamento noto e accettato, scritto qui per non scoprirlo in produzione.

    L'high-water-mark si alza **solo quando abbiamo agito su quella chat**. Se il bot
    viene tolto da una chat che non abbiamo mai registrato, non c'e' nessuna riga da
    marcare e nessun segno resta: una promozione piu' VECCHIA che arrivasse dopo
    collegherebbe comunque quella chat, con `bot_stato` `administrator` mentre il bot
    non c'e' piu'.

    **Il baratto, e perche' e' stato scelto cosi'.** L'alternativa e' scrivere il segno
    anche per le chat che non conosciamo. Ma `my_chat_member` lo puo' provocare
    CHIUNQUE, aggiungendo e togliendo il bot da una chat qualsiasi: ogni giro
    lascerebbe una riga in `impostazioni` che non si cancella mai, cioe' una crescita
    illimitata a comando di un estraneo. Fra una staleness senza conseguenze — la chat
    non produce niente, perche' il bot non c'e' — e una tabella che chiunque puo' far
    crescere, si e' scelta la prima.

    Il caso che conta davvero, la chat GIA' registrata, e' invece coperto dai due test
    qui sopra.
    """
    base, percorso_db = servizio
    _utente_attivo(base, percorso_db, CLIENTE_A, 'ClienteA')
    _promozione(base, CANALE_A, CLIENTE_A, stato='kicked', update_id=20)

    _promozione(base, CANALE_A, CLIENTE_A, update_id=19)

    righe = _righe_chat(percorso_db)
    assert len(righe) == 1 and righe[0][4] == 'administrator', (
        f'il limite noto e- cambiato: rileggere il baratto nel docstring, {righe}')


def test_l_ordine_di_una_chat_non_sopprime_gli_eventi_di_un_ALTRA(servizio):
    """L'high-water-mark e' PER CHAT, non globale.

    Globale, la promozione di un canale con id alto sopprimerebbe come «fuori ordine»
    la rimozione LEGITTIMA di un altro canale con id piu' basso. E' il bloccante che
    Claude Fable 5 aveva alzato sul gemello della #56, e vale identico qui.
    """
    base, percorso_db = servizio
    _utente_attivo(base, percorso_db, CLIENTE_A, 'ClienteA')
    _promozione(base, CANALE_A, CLIENTE_A, update_id=5)
    _promozione(base, CANALE_B, CLIENTE_A, titolo='Secondo', update_id=50)

    # Rimozione del PRIMO canale, con un id piu' basso di quello del secondo.
    _promozione(base, CANALE_A, CLIENTE_A, stato='kicked', update_id=6)

    stati = {r[0]: r[4] for r in _righe_chat(percorso_db)}
    assert stati[CANALE_A] == 'kicked', f'la rimozione legittima e- stata soppressa: {stati}'
    assert stati[CANALE_B] == 'administrator', stati
