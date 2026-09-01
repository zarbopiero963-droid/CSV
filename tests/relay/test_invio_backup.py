"""Invio del backup al canale privato configurato (#56 pezzo 3).

Il backup del database (pezzo 1) e il canale privato di destinazione (pezzo 2) esistono; qui
si manda davvero il file al canale. Cosa e' vincolato:

- la copia va su un **file** temporaneo, non in RAM (`copia_backup_su_file`), e si carica in
  **streaming** via `sendDocument` — il rischio OOM sollevato al gate del pezzo 1;
- la privacy si **riverifica con `getChat` PRIMA di ogni invio**: la cattura garantisce un
  canale privato, ma un canale reso pubblico dopo la conferma esporrebbe i dati dei clienti,
  quindi se ora ha uno `username` NON si invia (Sol, gate del pezzo 2);
- la rotta ha **due modi di autenticarsi**: la sessione dell'amministratore (il bottone) o il
  token del cron (`BACKUP_CRON_TOKEN`) per il giro notturno di Railway; senza nessuno dei due
  → **404**, come tutto `/api/admin/*`;
- l'invio (rete + I/O) gira **fuori dall'event loop**, o bloccherebbe webhook e feed.
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE))

import main  # noqa: E402 - dopo l'inserimento del percorso
from tests.relay.test_accesso import _admin  # noqa: E402

CANALE = -1001234567890


def _configura_canale(chat_id=CANALE, titolo='Backup Piero'):
    c = main.db()
    try:
        main.scrivi_impostazione(c, main.CHIAVE_CANALE_BACKUP_ID, str(chat_id))
        main.scrivi_impostazione(c, main.CHIAVE_CANALE_BACKUP_TITOLO, titolo)
        c.commit()
    finally:
        c.close()


class _RichiestaCron:
    """Una richiesta senza sessione ma col token del cron nell'header."""

    def __init__(self, token):
        self.cookies = {}
        self.headers = {'X-Backup-Cron-Token': token}


def _corpo(risposta):
    import json
    if isinstance(risposta, dict):
        return risposta
    return json.loads(bytes(risposta.body).decode())


# ---------------------------------------------------------- copia su file

def test_copia_backup_su_file_e_una_copia_integra(tmp_path, monkeypatch):
    """`copia_backup_su_file` scrive un file SQLite valido, integro e coi dati veri — su DISCO,
    non materializzando i byte in RAM (differenza col `serialize()` di `copia_backup_db`)."""
    _p, _admin_s, _c, _u = _admin(tmp_path, monkeypatch, 'sufile.db')
    destinazione = str(tmp_path / 'copia_backup.db')
    main.copia_backup_su_file(destinazione)
    with open(destinazione, 'rb') as f:
        assert f.read(16) == b'SQLite format 3\x00', 'la copia non e- un file SQLite'
    c = sqlite3.connect(destinazione)
    integrita = c.execute('PRAGMA integrity_check').fetchone()[0]
    utenti = c.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    c.close()
    assert integrita == 'ok', f'copia non integra: {integrita!r}'
    assert utenti >= 1, 'la copia non contiene gli utenti del database vivo'


# ---------------------------------------------------- sendDocument multipart

class _FintaRisposta:
    def __init__(self, corpo):
        self._corpo = corpo

    def read(self):
        return self._corpo

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_invia_documento_costruisce_un_multipart_e_streama_il_file(tmp_path, monkeypatch):
    """Il multipart porta chat_id, filename e i byte del file; la risposta `ok:true` → riuscito."""
    percorso = str(tmp_path / 'x.db')
    with open(percorso, 'wb') as f:
        f.write(b'SQLite format 3\x00' + b'CONTENUTODB' * 4)
    catturato = {}

    def finto_urlopen(richiesta, timeout=None):
        catturato['url'] = richiesta.full_url
        catturato['ct'] = richiesta.get_header('Content-type')
        catturato['body'] = richiesta.data.read()   # lo stream aperto del multipart
        return _FintaRisposta(b'{"ok":true,"result":{"message_id":1}}')

    monkeypatch.setattr('urllib.request.urlopen', finto_urlopen)
    riuscito, motivo = main.invia_documento_telegram(
        CANALE, percorso, 'betrelay-backup-2026-08-31-0000.db',
        didascalia='BetRelay: backup', bot_token='TOKENFINTO')
    assert riuscito is True and motivo is None, (riuscito, motivo)
    assert catturato['url'].endswith('/sendDocument')
    assert 'multipart/form-data; boundary=' in catturato['ct']
    corpo = catturato['body']
    assert b'name="chat_id"' in corpo and str(CANALE).encode() in corpo
    assert b'filename="betrelay-backup-2026-08-31-0000.db"' in corpo
    assert b'CONTENUTODB' in corpo, 'i byte del file non sono finiti nel multipart'


def test_invia_documento_riporta_il_rifiuto_di_telegram(tmp_path, monkeypatch):
    percorso = str(tmp_path / 'y.db')
    with open(percorso, 'wb') as f:
        f.write(b'contenuto')
    monkeypatch.setattr('urllib.request.urlopen',
                        lambda richiesta, timeout=None: _FintaRisposta(b'{"ok":false}'))
    riuscito, motivo = main.invia_documento_telegram(CANALE, percorso, 'b.db', bot_token='T')
    assert riuscito is False and 'rifiutato' in motivo, (riuscito, motivo)


def test_invia_documento_non_mette_il_token_nel_motivo(tmp_path, monkeypatch):
    """Un errore di rete non deve far uscire il token (che sta nell'URL): il motivo e' il TIPO."""
    percorso = str(tmp_path / 'z.db')
    with open(percorso, 'wb') as f:
        f.write(b'contenuto')

    def esplode(richiesta, timeout=None):
        raise OSError('connessione rifiutata')

    monkeypatch.setattr('urllib.request.urlopen', esplode)
    riuscito, motivo = main.invia_documento_telegram(CANALE, percorso, 'b.db', bot_token='SEGRETO')
    assert riuscito is False
    assert 'SEGRETO' not in motivo and 'OSError' in motivo, motivo


# ------------------------------------------------------------- getChat

def test_leggi_chat_ritorna_il_result(monkeypatch):
    monkeypatch.setattr('urllib.request.urlopen', lambda url, timeout=None: _FintaRisposta(
        b'{"ok":true,"result":{"id":-100,"type":"channel","title":"X"}}'))
    ok, dati = main.leggi_chat_telegram(CANALE, bot_token='T')
    assert ok is True and dati.get('title') == 'X', (ok, dati)


# ------------------------------------------------- orchestrazione dell'invio

def test_backup_al_canale_senza_canale_configurato(tmp_path, monkeypatch):
    _p, _a, _c, _u = _admin(tmp_path, monkeypatch, 'nocanale.db')
    riuscito, motivo = main._invia_backup_al_canale()
    assert riuscito is False and 'nessun canale' in motivo, (riuscito, motivo)


def test_backup_al_canale_rifiuta_un_canale_diventato_PUBBLICO(tmp_path, monkeypatch):
    """Riverifica privacy con getChat: un canale reso pubblico (ha uno `username`) NON riceve il
    backup — i dati dei clienti non devono finire dove chiunque puo' leggerli. Fail-first: senza il
    controllo su `username` il documento partirebbe lo stesso. Sol, gate del pezzo 2."""
    _p, _a, _c, _u = _admin(tmp_path, monkeypatch, 'pubblico.db')
    _configura_canale()
    monkeypatch.setattr(main, 'leggi_chat_telegram', lambda chat_id, bot_token=None: (
        True, {'id': CANALE, 'type': 'channel', 'username': 'ora_pubblico'}))
    inviato = {}
    monkeypatch.setattr(main, 'invia_documento_telegram',
                        lambda *a, **k: inviato.setdefault('si', True) or (True, None))
    riuscito, motivo = main._invia_backup_al_canale()
    assert riuscito is False and 'privat' in motivo, (riuscito, motivo)
    assert 'si' not in inviato, 'il backup e- partito verso un canale diventato pubblico'


def test_backup_al_canale_rifiuta_una_destinazione_non_channel(tmp_path, monkeypatch):
    """Difesa in profondita' (GPT-5.5, PR #101): la destinazione deve essere `type == 'channel'`.
    Se getChat riporta un gruppo/privato (canale convertito, o config errata) NON si invia. Fail-
    first: senza il controllo su `type` una destinazione non-channel riceverebbe il backup."""
    _p, _a, _c, _u = _admin(tmp_path, monkeypatch, 'nonchannel.db')
    _configura_canale()
    monkeypatch.setattr(main, 'leggi_chat_telegram',
                        lambda chat_id, bot_token=None: (True, {'id': CANALE, 'type': 'group'}))
    inviato = {}
    monkeypatch.setattr(main, 'invia_documento_telegram',
                        lambda *a, **k: inviato.setdefault('si', True) or (True, None))
    riuscito, motivo = main._invia_backup_al_canale()
    assert riuscito is False and 'privat' in motivo, (riuscito, motivo)
    assert 'si' not in inviato, 'il backup e- partito verso una destinazione non-channel'


def test_backup_al_canale_non_solleva_se_la_copia_fallisce(tmp_path, monkeypatch):
    """`copia_backup_su_file` puo' sollevare (disco pieno, errore sqlite). Il contratto e' «non
    solleva»: torna `(False, motivo)` e la rotta risponde 400, non 500. Il motivo e' il TIPO
    dell'eccezione, mai un percorso o dato del DB. Fail-first: senza il try/except attorno alla copia
    l'eccezione risale e la chiamata solleva. Bloccante di Claude Fable 5 (#101)."""
    _p, _a, _c, _u = _admin(tmp_path, monkeypatch, 'copia_ko.db')
    _configura_canale()

    def esplode(percorso):
        raise sqlite3.OperationalError('disk I/O error')

    monkeypatch.setattr(main, 'copia_backup_su_file', esplode)
    riuscito, motivo = main._invia_backup_al_canale()
    assert riuscito is False, (riuscito, motivo)
    assert 'copia' in motivo and 'OperationalError' in motivo, motivo
    assert 'disk I/O' not in motivo, 'il motivo non deve riportare il testo dell-eccezione'


def test_backup_inviato_ma_audit_fallito_resta_successo(tmp_path, monkeypatch):
    """L'audit e' BEST-EFFORT: se il documento e' partito, un errore SQLite in `_annota_admin` NON
    deve tornare «fallito» — il cron ritenterebbe e manderebbe un SECONDO backup identico. Ritorna
    `(True, None)`. Fail-first: senza il try/except attorno all'audit l'eccezione risale. Bloccante
    di GPT-5.6 Sol (#101)."""
    _p, _a, _c, _u = _admin(tmp_path, monkeypatch, 'audit_ko.db')
    _configura_canale()
    monkeypatch.setattr(main, 'leggi_chat_telegram',
                        lambda chat_id, bot_token=None: (True, {'id': CANALE, 'type': 'channel'}))
    inviati = []
    monkeypatch.setattr(main, 'invia_documento_telegram',
                        lambda *a, **k: inviati.append(1) or (True, None))

    def audit_rotto(c, chi, azione, bersaglio=None):
        raise sqlite3.OperationalError('audit ko')

    monkeypatch.setattr(main, '_annota_admin', audit_rotto)
    esito = main._invia_backup_al_canale(amministratore_id=1)
    assert esito == (True, None), esito
    assert len(inviati) == 1, 'il documento non e- partito una volta sola'


def test_backup_al_canale_invia_e_traccia(tmp_path, monkeypatch):
    """Canale privato: si copia su file, si manda al chat_id giusto, e resta una riga di audit."""
    percorso, _a, _c, _u = _admin(tmp_path, monkeypatch, 'invia.db')
    _configura_canale()
    monkeypatch.setattr(main, 'leggi_chat_telegram',
                        lambda chat_id, bot_token=None: (True, {'id': CANALE, 'type': 'channel'}))
    visto = {}

    def finto_invio(chat_id, percorso_file, nome, didascalia=None, bot_token=None):
        visto['chat_id'] = chat_id
        with open(percorso_file, 'rb') as f:
            visto['magic'] = f.read(16)
        return True, None

    monkeypatch.setattr(main, 'invia_documento_telegram', finto_invio)
    riuscito, motivo = main._invia_backup_al_canale(amministratore_id=7)
    assert riuscito is True, motivo
    assert visto['chat_id'] == str(CANALE), 'il backup non e- andato al canale configurato'
    assert visto['magic'] == b'SQLite format 3\x00', 'il file inviato non e- una copia SQLite'
    c = sqlite3.connect(percorso)
    righe = c.execute("SELECT admin_user_id FROM admin_audit"
                      " WHERE action='backup_inviato'").fetchall()
    c.close()
    assert righe == [(7,)], f'audit inatteso per l-invio del backup: {righe}'


def test_un_solo_invio_di_backup_alla_volta(tmp_path, monkeypatch):
    """Due invii simultanei (bottone + cron sovrapposti) non devono consegnare due documenti ne'
    scrivere due audit: il lucchetto NON bloccante fa passare uno e fa saltare l'altro con «gia' in
    corso». Fail-first: senza il lucchetto entrambi inviano e restano due righe di audit.

    DETERMINISTICO, non a orologio (nota di Fable): il primo thread SEGNALA di essere entrato nel
    lucchetto (`dentro`) e ci resta finche' il thread principale non lo libera (`libera`); nel mezzo
    il principale prova il secondo invio, che DEVE trovare il lucchetto occupato. Nessuna `sleep`,
    nessuna finestra da indovinare."""
    percorso, _a, _c, _u = _admin(tmp_path, monkeypatch, 'concorrenza_invio.db')
    _configura_canale()
    monkeypatch.setattr(main, 'leggi_chat_telegram',
                        lambda chat_id, bot_token=None: (True, {'id': CANALE, 'type': 'channel'}))
    dentro = threading.Event()
    libera = threading.Event()
    invii = []

    def invio_bloccante(chat_id, percorso_file, nome, didascalia=None, bot_token=None):
        invii.append(1)
        dentro.set()          # sono dentro il lucchetto
        assert libera.wait(5), 'il thread principale non ha liberato il primo invio'
        return True, None

    monkeypatch.setattr(main, 'invia_documento_telegram', invio_bloccante)
    esiti = {}

    def primo():
        esiti['a'] = main._invia_backup_al_canale(amministratore_id=1)

    t1 = threading.Thread(target=primo)
    t1.start()
    assert dentro.wait(5), 'il primo invio non e- mai entrato nel lucchetto'
    # Ora t1 TIENE il lucchetto: il secondo invio deve trovarlo occupato e saltare.
    esiti['b'] = main._invia_backup_al_canale(amministratore_id=1)
    libera.set()
    t1.join()

    assert esiti['a'] == (True, None), esiti
    assert esiti['b'][0] is False and 'gia' in esiti['b'][1], esiti
    assert len(invii) == 1, f'{len(invii)} invii: il lucchetto non serializza l-orchestrazione'
    c = sqlite3.connect(percorso)
    righe = c.execute("SELECT COUNT(*) FROM admin_audit"
                      " WHERE action='backup_inviato'").fetchone()[0]
    c.close()
    assert righe == 1, f'{righe} righe di audit per due invii concorrenti'


# ------------------------------------ idempotenza persistente del giro notturno (#56)

def _finto_canale_ok(monkeypatch):
    monkeypatch.setattr(main, 'leggi_chat_telegram',
                        lambda chat_id, bot_token=None: (True, {'id': CANALE, 'type': 'channel'}))


def test_il_cron_non_reinvia_lo_stesso_periodo(tmp_path, monkeypatch):
    """Idempotenza persistente (#56, Sol B3): due giri del cron per lo STESSO periodo mandano UN
    solo backup — la prenotazione in `backup_inviato` fa uscire il secondo come no-op `(True,
    None)`. Chiude il retry notturno che rimandava un secondo file identico. Fail-first: senza la
    prenotazione (o forzandola a passare sempre) partono due invii."""
    percorso, _a, _c, _u = _admin(tmp_path, monkeypatch, 'cron_dedup.db')
    _configura_canale()
    _finto_canale_ok(monkeypatch)
    invii = []
    monkeypatch.setattr(main, 'invia_documento_telegram',
                        lambda *a, **k: invii.append(1) or (True, None))
    r1 = main._invia_backup_al_canale(periodo='2026-09-01')
    r2 = main._invia_backup_al_canale(periodo='2026-09-01')
    assert r1 == (True, None) and r2 == (True, None), (r1, r2)
    assert len(invii) == 1, f'{len(invii)} invii per lo stesso periodo: la prenotazione non deduplica'
    c = sqlite3.connect(percorso)
    n = c.execute("SELECT COUNT(*) FROM backup_inviato WHERE periodo='2026-09-01'").fetchone()[0]
    c.close()
    assert n == 1, f'prenotazione del periodo non registrata: {n}'


def test_il_cron_libera_il_periodo_se_l_invio_fallisce(tmp_path, monkeypatch):
    """Se l'invio prenotato FALLISCE, la prenotazione si LIBERA: la notte non resta segnata come
    fatta e un retry riparte. Fail-first: senza la liberazione il periodo resta preso e il retry
    esce come no-op senza mai mandare il backup."""
    percorso, _a, _c, _u = _admin(tmp_path, monkeypatch, 'cron_libera.db')
    _configura_canale()
    _finto_canale_ok(monkeypatch)
    tentativi = []

    def invio(*a, **k):
        tentativi.append(1)
        return (False, 'Telegram giu') if len(tentativi) == 1 else (True, None)

    monkeypatch.setattr(main, 'invia_documento_telegram', invio)
    r1 = main._invia_backup_al_canale(periodo='2026-09-02')
    assert r1[0] is False, r1
    c = sqlite3.connect(percorso)
    n = c.execute("SELECT COUNT(*) FROM backup_inviato WHERE periodo='2026-09-02'").fetchone()[0]
    c.close()
    assert n == 0, 'il periodo e- rimasto prenotato dopo un invio fallito: nessun retry ripartirebbe'
    r2 = main._invia_backup_al_canale(periodo='2026-09-02')
    assert r2 == (True, None), r2
    assert len(tentativi) == 2, 'il retry non ha rimandato il backup dopo il fallimento'


def test_il_bottone_admin_invia_anche_se_il_periodo_e_gia_fatto(tmp_path, monkeypatch):
    """Il bottone «Invia backup ora» e' un intento umano esplicito: invia SEMPRE, anche se il cron
    ha gia' mandato il backup di oggi. Solo il cron (`periodo` non-None) rispetta la prenotazione;
    l'admin (`periodo` None) la ignora."""
    percorso, _a, _c, _u = _admin(tmp_path, monkeypatch, 'admin_bypassa.db')
    _configura_canale()
    _finto_canale_ok(monkeypatch)
    invii = []
    monkeypatch.setattr(main, 'invia_documento_telegram',
                        lambda *a, **k: invii.append(1) or (True, None))
    r_cron = main._invia_backup_al_canale(periodo='2026-09-03')
    assert r_cron == (True, None) and len(invii) == 1, (r_cron, invii)
    r_admin = main._invia_backup_al_canale(amministratore_id=1, periodo=None)
    assert r_admin == (True, None), r_admin
    assert len(invii) == 2, 'il bottone admin non ha inviato perche- il periodo era gia- fatto'


# --------------------------------------------------------------- la rotta

def test_invia_backup_con_sessione_admin(tmp_path, monkeypatch):
    percorso, admin_s, _c, _u = _admin(tmp_path, monkeypatch, 'rotta_admin.db')
    _configura_canale()
    monkeypatch.setattr(main, '_invia_backup_al_canale',
                        lambda amministratore_id=None, periodo=None: (True, None))
    corpo = _corpo(asyncio.run(main.invia_backup(admin_s)))
    assert corpo == {'inviato': True}, corpo


def test_invia_backup_col_token_del_cron_traccia_senza_admin(tmp_path, monkeypatch):
    """Il giro notturno: nessuna sessione, ma il token del cron nell'header → invio, e l'audit
    ha `admin_user_id` NULL (non e' stato un amministratore a premere il bottone)."""
    percorso, _admin_s, _c, _u = _admin(tmp_path, monkeypatch, 'rotta_cron.db')
    _configura_canale()
    monkeypatch.setattr(main, 'BACKUP_CRON_TOKEN', 'segreto-del-cron')
    monkeypatch.setattr(main, 'leggi_chat_telegram',
                        lambda chat_id, bot_token=None: (True, {'id': CANALE, 'type': 'channel'}))
    monkeypatch.setattr(main, 'invia_documento_telegram',
                        lambda *a, **k: (True, None))
    corpo = _corpo(asyncio.run(main.invia_backup(_RichiestaCron('segreto-del-cron'))))
    assert corpo == {'inviato': True}, corpo
    c = sqlite3.connect(percorso)
    nulli = c.execute("SELECT COUNT(*) FROM admin_audit"
                      " WHERE action='backup_inviato' AND admin_user_id IS NULL").fetchone()[0]
    c.close()
    assert nulli == 1, f'il giro del cron non ha tracciato un audit senza admin: {nulli}'


def test_invia_backup_col_token_SBAGLIATO_e_404(tmp_path, monkeypatch):
    """Un token che non combacia, e nessuna sessione → 404, come un estraneo qualunque."""
    from fastapi import HTTPException
    _p, _admin_s, _c, _u = _admin(tmp_path, monkeypatch, 'token_ko.db')
    monkeypatch.setattr(main, 'BACKUP_CRON_TOKEN', 'quello-giusto')
    with pytest.raises(HTTPException) as errore:
        asyncio.run(main.invia_backup(_RichiestaCron('quello-sbagliato')))
    assert errore.value.status_code == 404, f'{errore.value.status_code} invece di 404'


def test_invia_backup_senza_token_configurato_il_cron_non_entra(tmp_path, monkeypatch):
    """Fail-closed: `BACKUP_CRON_TOKEN` vuoto non autorizza NESSUN token (nemmeno una stringa
    vuota che combacerebbe con `''`). Senza sessione → 404."""
    from fastapi import HTTPException
    _p, _admin_s, _c, _u = _admin(tmp_path, monkeypatch, 'token_vuoto.db')
    monkeypatch.setattr(main, 'BACKUP_CRON_TOKEN', '')
    for header in ('', 'qualcosa'):
        with pytest.raises(HTTPException) as errore:
            asyncio.run(main.invia_backup(_RichiestaCron(header)))
        assert errore.value.status_code == 404, f'token vuoto, header {header!r}: atteso 404'


def test_invia_backup_admin_senza_canale_e_400(tmp_path, monkeypatch):
    from fastapi import HTTPException
    _p, admin_s, _c, _u = _admin(tmp_path, monkeypatch, 'rotta_nocanale.db')
    with pytest.raises(HTTPException) as errore:
        asyncio.run(main.invia_backup(admin_s))
    assert errore.value.status_code == 400, f'{errore.value.status_code} invece di 400'


def test_invia_backup_gira_fuori_dall_event_loop(tmp_path, monkeypatch):
    """La rotta e' `async`: l'invio (rete + I/O) va off-loaded, o bloccherebbe webhook e feed di
    tutti fino al timeout. Deve girare su un thread ≠ da quello del loop. Fail-first: con la
    chiamata diretta i due identificativi di thread coincidono."""
    _p, admin_s, _c, _u = _admin(tmp_path, monkeypatch, 'rotta_offload.db')
    identita = {}

    def spia(amministratore_id=None, periodo=None):
        identita['invio'] = threading.get_ident()
        return True, None

    monkeypatch.setattr(main, '_invia_backup_al_canale', spia)

    async def guida():
        identita['loop'] = threading.get_ident()
        return await main.invia_backup(admin_s)

    _corpo(asyncio.run(guida()))
    assert identita.get('invio') is not None, 'l-invio non e- stato chiamato'
    assert identita['invio'] != identita['loop'], \
        'l-invio del backup ha girato sull-event loop invece che su un thread'
