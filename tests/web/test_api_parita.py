"""`api.js` e `api_finta.js`: stessa superficie, o le viste si rompono in silenzio.

`app.js` parla con `api.js` (il backend vero); la copia dimostrativa a file
unico concatena invece `api_finta.js`. Sono due implementazioni della stessa
superficie — la regola 3 di CLAUDE.md applicata al layer di accesso — e la
divergenza ha un modo solo di manifestarsi: la demo chiama una funzione che
non esiste, `TypeError` in console, pagina mezza morta. Nessun test di
`main.py` la vedrebbe mai.

Questi controlli sono STATICI (regex sui sorgenti, niente browser) perche' il
punto di rottura e' statico: `tools/build_single_file.py` ricostruisce il
namespace `api` leggendo le righe `export function`, quindi e' quella forma
che va vincolata — nome per nome, e senza `async` sulle funzioni che le viste
usano senza `await`.
"""

from __future__ import annotations

import re
from pathlib import Path

RADICE = Path(__file__).resolve().parents[2]
WEB = RADICE / 'web'

RIGA_EXPORT = re.compile(r'^export\s+(async\s+)?function\s+(\w+)', re.M)


def _esportate(nome: str) -> dict[str, bool]:
    """{nome funzione: e' dichiarata async} per un modulo di web/."""
    testo = (WEB / nome).read_text(encoding='utf-8')
    return {m.group(2): bool(m.group(1)) for m in RIGA_EXPORT.finditer(testo)}


def test_i_due_layer_esportano_le_stesse_funzioni():
    vere = set(_esportate('api.js'))
    finte = set(_esportate('api_finta.js'))
    assert vere == finte, (
        f'solo in api.js: {sorted(vere - finte)} — '
        f'solo in api_finta.js: {sorted(finte - vere)}')


def test_le_viste_chiamano_solo_funzioni_che_esistono():
    """Ogni `api.<nome>(` in app.js deve esistere in ENTRAMBI i layer."""
    app = (WEB / 'app.js').read_text(encoding='utf-8')
    usate = set(re.findall(r'\bapi\.(\w+)\(', app))
    for layer in ('api.js', 'api_finta.js'):
        mancanti = usate - set(_esportate(layer))
        assert not mancanti, f'app.js chiama funzioni assenti da {layer}: {sorted(mancanti)}'


# Le funzioni che app.js usa SENZA await: la cache sincrona del layer. Se una
# diventasse async, le viste riceverebbero una Promise al posto del valore e
# `u.nome` diventerebbe `undefined` — senza errore, che e' il modo peggiore.
SINCRONE = {'me', 'settings', 'getParser', 'telegramAuthUrl', 'suggest',
            'sampleMessage', 'saveSampleMessage', 'feedUrl', 'hasToken',
            # La libreria mercati (#33): il wizard le legge dentro un render.
            'sports', 'mercatiOf',
            # Le sorgenti squadre (#34): stesse ragioni, stesse viste.
            # `sorgenti` e' del pezzo 3: la legge la card del wizard.
            'competizioni', 'competizione', 'aliasOf', 'sorgenti'}


def test_la_cache_sincrona_resta_sincrona():
    for layer in ('api.js', 'api_finta.js'):
        dichiarate = _esportate(layer)
        for nome in SINCRONE:
            assert nome in dichiarate, f'{layer} non esporta {nome}'
            assert dichiarate[nome] is False, (
                f'{nome} in {layer} e\' diventata async: le viste la usano '
                'senza await e riceverebbero una Promise')


def test_il_bundle_usa_il_layer_finto_e_non_quello_vero():
    """Il file unico si apre da file://, dove fetch non esiste: deve
    concatenare `api_finta.js`. Se tornasse ad `api.js`, la demo mostrerebbe
    per sempre «il server non risponde»."""
    build = (RADICE / 'tools' / 'build_single_file.py').read_text(encoding='utf-8')
    assert "'api_finta.js'" in build
    assert re.search(r"WEB\s*/\s*'api\.js'", build) is None, (
        'tools/build_single_file.py legge api.js: la demo non puo\' fare fetch')
