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
