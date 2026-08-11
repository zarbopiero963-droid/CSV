"""Guardia su cosa il servizio espone pubblicamente sotto `/app`.

`main.py` monta la cartella `web/` con `StaticFiles`: **tutto** quello che sta
lì dentro è scaricabile da chiunque conosca l'URL, senza token e senza sessione.
Non c'è una allowlist di estensioni in `StaticFiles`, quindi la sola difesa è
che in quella cartella non finisca niente che non sia un asset del browser.

Il difetto che ha motivato questo file: `web/build_single_file.py`, il
generatore del bundle, era servito su `/app/build_single_file.py`. Non conteneva
segreti, ma era sorgente lato server pubblicato senza motivo, e il suo output di
default finiva in `web/dist/`, cioè ancora sotto il mount.

Segnalato da Claude Fable 5 sulla PR #1 («verificare che nessun file sensibile
finisca sotto lo StaticFiles»).

Il test non guarda quel singolo file: guarda la **classe**. Chiunque rimetta uno
script, un `.env`, un `.db`, un dump o un CSV in `web/` fa fallire la suite.
"""

from __future__ import annotations

import re
from pathlib import Path

RADICE = Path(__file__).resolve().parents[2]
WEB = RADICE / 'web'
MAIN = RADICE / 'main.py'

# Estensioni che un browser scarica per far funzionare il prototipo. Aggiungerne
# una è una decisione consapevole, e per questo va fatta qui.
ESTENSIONI_STATICHE = {'.html', '.css', '.js', '.svg', '.png', '.jpg', '.jpeg',
                       '.webp', '.gif', '.ico', '.woff', '.woff2', '.map', '.json'}


# Tollerante alla forma, severo sul comportamento: apici singoli o doppi e
# spaziatura libera vanno bene, perche' non cambiano cosa viene servito.
# Un confronto su stringa esatta diventerebbe rosso a una riformattazione di
# `main.py` senza che nulla sia cambiato, e una guardia che grida al lupo per
# uno stile diverso insegna a ignorare la suite. Segnalato da GPT-5.5 sulla PR #1.
DEFINIZIONE_WEB_DIR = re.compile(
    r"""WEB_DIR\s*=\s*Path\(__file__\)\.parent\s*/\s*['"]web['"]"""
)
MOUNT_APP = re.compile(
    r"""app\.mount\(\s*['"]/app['"]\s*,\s*StaticFiles\(\s*directory\s*=\s*WEB_DIR"""
)


def test_main_monta_ancora_la_cartella_web():
    """Se il mount cambia nome o cartella, questa guardia va aggiornata con lui.

    Senza questo controllo il file resterebbe verde per sempre nel caso in cui
    `main.py` iniziasse a servire un'altra cartella: proteggerebbe un percorso
    che non è più esposto, dando una sicurezza che non c'è.
    """
    sorgente = MAIN.read_text(encoding='utf-8')
    assert DEFINIZIONE_WEB_DIR.search(sorgente), \
        'main.py non definisce piu- WEB_DIR su web/: aggiorna questa guardia'
    assert MOUNT_APP.search(sorgente), \
        'main.py non monta piu- WEB_DIR su /app: aggiorna questa guardia'


def test_la_guardia_non_dipende_dallo_stile_delle_stringhe():
    """La guardia sopra riconosce le due forme equivalenti, e solo quelle.

    Verifica il riconoscitore, non `main.py`: un falso rosso su un cambio di
    apici renderebbe rumorosa proprio la guardia che deve restare credibile.
    """
    for variante in (
        "WEB_DIR = Path(__file__).parent / 'web'",
        'WEB_DIR = Path(__file__).parent / "web"',
        'WEB_DIR=Path(__file__).parent/"web"',
    ):
        assert DEFINIZIONE_WEB_DIR.search(variante), f'forma non riconosciuta: {variante}'
    # Ma una cartella diversa deve restare NON riconosciuta, altrimenti la
    # tolleranza avrebbe mangiato il controllo.
    assert not DEFINIZIONE_WEB_DIR.search("WEB_DIR = Path(__file__).parent / 'static'")
    for variante in (
        "app.mount('/app', StaticFiles(directory=WEB_DIR, html=True), name='app')",
        'app.mount("/app", StaticFiles( directory = WEB_DIR, html=True), name="app")',
    ):
        assert MOUNT_APP.search(variante), f'forma di mount non riconosciuta: {variante}'
    assert not MOUNT_APP.search("app.mount('/altro', StaticFiles(directory=WEB_DIR))")


def test_nessun_file_non_statico_sotto_il_mount():
    assert WEB.is_dir(), 'la cartella web/ non esiste'
    intrusi = [
        p.relative_to(RADICE).as_posix()
        for p in sorted(WEB.rglob('*'))
        if p.is_file() and p.suffix.lower() not in ESTENSIONI_STATICHE
    ]
    assert not intrusi, (
        'file non statici sotto il mount pubblico /app, scaricabili senza token: '
        + ', '.join(intrusi)
    )


def test_nessun_sorgente_python_sotto_il_mount():
    """Ridondante col test sopra, e voluto: nomina il caso concreto.

    Se qualcuno allargasse `ESTENSIONI_STATICHE` per far passare un file, questo
    resterebbe rosso su `.py` e costringerebbe a motivare la scelta invece di
    farla scivolare dentro una lista.
    """
    python = [p.relative_to(RADICE).as_posix() for p in WEB.rglob('*.py')]
    assert not python, f'sorgente Python servito pubblicamente su /app: {python}'


# ---------------------------------------------------------------- il CONTENUTO
#
# I test sopra guardano il TIPO dei file; questi guardano cosa c'e' dentro. La
# distinzione non e' accademica: `.js` e' un'estensione legittima sotto il mount,
# ed e' esattamente il file in cui i dati finti del prototipo vivono — quindi e'
# il posto dove qualcuno sostituisce il chat_id finto col proprio mentre prova
# qualcosa, o incolla un token per vedere se l'API risponde. Il tipo resterebbe
# valido e il segreto sarebbe scaricabile da chiunque conosca l'URL.
#
# Chiesto da Claude Fable 5 e da GPT-5.5 sulla PR #12, entrambi come verifica
# manuale («confermare che web/ non contenga configurazioni, dump, token o dati
# utente»). Una verifica manuale vale per il giorno in cui la si fa: qui diventa
# un test.
#
# Le forme si COMPONGONO a runtime: scritte per esteso, questo file finirebbe nel
# payload di una review e il redattore dei workflow le maciullerebbe (e' la
# lezione della PR #9, dove il file di test del redattore veniva mangiato dal
# redattore stesso).
_SK = 'sk' + '-'
_GH = 'gh' + 'p_'
_XT = 'xt' + '_'
FORME_DI_SEGRETO = (
    ('chiave OpenAI/Anthropic', re.compile(_SK + r'[A-Za-z0-9_\-]{20,}')),
    ('token GitHub', re.compile(_GH + r'[A-Za-z0-9_]{30,}')),
    ('token di feed BetRelay', re.compile(_XT + r'[A-Za-z0-9_\-]{12,}')),
    ('token di bot Telegram', re.compile(r'\b\d{8,12}:[A-Za-z0-9_\-]{30,}\b')),
)

# I chat_id che il prototipo mostra di proposito, dichiarati uno per uno. Un
# chat_id NUOVO sotto il mount fa fallire il test finche' qualcuno non lo aggiunge
# qui: e' l'unico modo di distinguere un valore finto da uno vero, perche' dalla
# forma sono identici.
CHAT_ID_FINTI_DICHIARATI = {'-1001987654321'}
FORMA_CHAT_ID = re.compile(r'-100\d{7,}')


def _file_serviti():
    return [p for p in sorted(WEB.rglob('*')) if p.is_file()]


def test_nessun_valore_dalla_forma_di_un_segreto_sotto_il_mount():
    """Un token sotto `/app` e- scaricabile da chiunque, senza token e senza sessione."""
    trovati = []
    for percorso in _file_serviti():
        testo = percorso.read_text(encoding='utf-8', errors='replace')
        for nome, forma in FORME_DI_SEGRETO:
            for m in forma.finditer(testo):
                riga = testo[:m.start()].count('\n') + 1
                trovati.append(f'{percorso.relative_to(RADICE).as_posix()}:{riga}: {nome}')
    assert not trovati, (
        'valori dalla forma di un segreto sotto il mount pubblico /app:\n  '
        + '\n  '.join(trovati)
    )


def test_i_chat_id_sotto_il_mount_sono_dichiarati_finti():
    """Dalla forma un chat_id finto e uno vero sono identici: serve una dichiarazione.

    Il prototipo ne mostra uno di proposito (`-1001987654321`, dentro il modulo dei
    dati finti). Uno nuovo — per esempio il canale vero del proprietario, incollato
    durante una prova — sarebbe indistinguibile, quindi il test pretende che sia
    elencato qui sopra.
    """
    non_dichiarati = []
    for percorso in _file_serviti():
        testo = percorso.read_text(encoding='utf-8', errors='replace')
        for m in FORMA_CHAT_ID.finditer(testo):
            if m.group(0) not in CHAT_ID_FINTI_DICHIARATI:
                riga = testo[:m.start()].count('\n') + 1
                non_dichiarati.append(
                    f'{percorso.relative_to(RADICE).as_posix()}:{riga}: {m.group(0)}')
    assert not non_dichiarati, (
        'chat_id non dichiarati sotto il mount pubblico /app — se sono finti '
        'aggiungerli a CHAT_ID_FINTI_DICHIARATI, se sono veri togliere il valore:\n  '
        + '\n  '.join(non_dichiarati)
    )


def test_i_riconoscitori_riconoscono_davvero():
    """Senza questo, una regex che non combacia mai darebbe una guardia verde a vita.

    E- la lezione del sabotaggio a vuoto della PR #9: un controllo che non puo-
    fallire non e- un controllo, e la sua innocuita- e- indistinguibile dal suo
    funzionamento. I campioni si compongono, come le forme.
    """
    campioni = {
        'chiave OpenAI/Anthropic': _SK + 'ant-api03-' + 'A' * 24,
        'token GitHub': _GH + 'B' * 36,
        'token di feed BetRelay': _XT + 'C' * 20,
        'token di bot Telegram': '123456789' + ':' + 'D' * 35,
    }
    for nome, forma in FORME_DI_SEGRETO:
        assert forma.search(campioni[nome]), f'la forma «{nome}» non riconosce il suo campione'
    assert FORMA_CHAT_ID.search('-1009876543210'), 'la forma del chat_id non riconosce niente'
    # E non deve combaciare con del testo innocuo, o sarebbe rumore.
    innocuo = 'const colore = "#1e88e5"; // 2026-08-11, versione 1.0.0'
    for nome, forma in FORME_DI_SEGRETO:
        assert not forma.search(innocuo), f'la forma «{nome}» combacia con testo innocuo'
    assert not FORMA_CHAT_ID.search(innocuo)
