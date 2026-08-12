"""Nessun U+FEFF letterale nei sorgenti: il BOM si scrive con l'escape.

La regola sta in `CLAUDE.md` («REGOLA CODIFICA») ed e' scritta cosi':

    Il BOM va scritto con l'escape `\\ufeff`, in Python come in JavaScript, **mai**
    come carattere letterale nel sorgente: un U+FEFF letterale e' invisibile in un
    editor ed e' esattamente il tipo di carattere che questa sezione dice di non
    lasciare in giro.

Fino a questo file nessun test la teneva, e la regola l'ho violata **io** nella PR
#21: `tests/relay/test_parse_message.py` conteneva un U+FEFF letterale in
`'\\ufeff'.encode('utf-8')`, e me ne sono accorto solo perche' una `str.replace` su
quella riga non trovava niente. Un carattere invisibile che rompe una ricerca
testuale e non solleva mai: la classe di difetto per cui la sezione esiste.

**Cosa fa male davvero**, ed e' peggio dell'estetica. Un U+FEFF all'INIZIO di un
file Python e' un BOM e l'interprete lo tollera; in mezzo al sorgente e' un
carattere invisibile che puo':

- stare dentro una stringa e cambiarne il confronto senza che si veda — lo stesso
  meccanismo per cui il marcatore `\U0001F19A` va confrontato per codepoint;
- rompere una modifica automatica, come e' successo qui;
- entrare nel CSV servito a XTrader se finisce in un valore, dove un BOM in mezzo
  non e' previsto da nessuna parte del contratto.

Il BOM del feed resta obbligatorio: quello lo scrive `main.CSV_BOM` con l'escape, e
il suo posto e' davanti a `"Provider"`, non nel sorgente.
"""

from __future__ import annotations

from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[2]

# Le cartelle di sorgenti del progetto. Non tutto il repository: `.git`, le cache e
# gli artefatti non sono sorgenti e un BOM li' non e' una nostra scelta.
SORGENTI = ('main.py', 'web', 'tools', 'tests')

# `.md` e `.txt` sono esclusi: la documentazione non viene eseguita ne' confrontata,
# e questo test parla di codice. `CLAUDE.md` inoltre CITA la regola, quindi
# includerlo renderebbe il test rosso per il documento che lo prescrive — la stessa
# trappola del commento che nomina la stringa cercata.
ESTENSIONI = ('.py', '.js', '.mjs', '.html', '.css', '.json', '.yml', '.yaml')

BOM = '\ufeff'


def _per_voce() -> dict:
    """I file scansionati, RAGGRUPPATI per voce di `SORGENTI`.

    Raggruppati e non appiattiti perche' il totale non basta: `Path.rglob` su un
    percorso inesistente non solleva, restituisce zero file. Una voce sbagliata
    sparirebbe quindi in silenzio dentro la somma.
    """
    per_voce = {}
    for voce in SORGENTI:
        percorso = RADICE / voce
        if percorso.is_file():
            per_voce[voce] = [percorso]
            continue
        per_voce[voce] = [p for p in percorso.rglob('*')
                          if p.is_file() and p.suffix in ESTENSIONI
                          and '__pycache__' not in p.parts]
    return per_voce


def _sorgenti() -> list[Path]:
    return [p for file in _per_voce().values() for p in file]


@pytest.mark.parametrize('voce', SORGENTI)
def test_ogni_voce_di_SORGENTI_esiste_e_produce_file(voce):
    """Il guardiano del guardiano — e la prima versione non lo era.

    Chiedeva solo `len(trovati) >= 15` su TUTTE le voci insieme. `Path.rglob` su un
    percorso inesistente non solleva e non stampa niente: restituisce zero file.
    Rinominare una cartella non faceva quindi diventare rosso nulla, perche' la somma
    restava sopra la soglia grazie alle altre.

    Misurato, e il numero e' la ragione per cui questa versione esiste: fingendo che
    `web/` fosse rinominata, la scansione passava da 29 a 23 file e il controllo
    aggregato **passava ancora**. I 6 file che smettevano di essere controllati in
    silenzio erano `api.js`, `app.js`, **`engine.js`**, `index.html`, `sito.html`,
    `styles.css` — cioe' il motore di parsing e la facciata del prodotto.

    Segnalato da CodeRabbit come Major sulla PR #21, ed era Major: un guardiano che
    smette di guardare senza dirlo e' peggio di nessun guardiano, perche' il verde
    continua ad arrivare.
    """
    file = _per_voce()[voce]
    assert file, (
        f'la voce {voce!r} di SORGENTI non produce nessun file: il percorso non '
        'esiste piu- (rinominato? spostato?), oppure nessun file ha un suffisso di '
        f'ESTENSIONI {ESTENSIONI}. Quei file NON vengono piu- controllati, e senza '
        'questo test la cosa non si vedrebbe'
    )


def test_ci_sono_sorgenti_da_controllare():
    """La soglia complessiva, che resta utile accanto al controllo per voce.

    Il controllo per voce non vedrebbe un `ESTENSIONI` svuotato a meta': ogni voce
    continuerebbe a produrre qualche file e la copertura calerebbe comunque. Questo
    misura l'ordine di grandezza.
    """
    trovati = _sorgenti()
    assert len(trovati) >= 15, (
        f'solo {len(trovati)} sorgenti trovati: i percorsi in SORGENTI/ESTENSIONI '
        'non descrivono piu- il repository e questo test non controlla piu- nulla'
    )


@pytest.mark.parametrize('percorso', _sorgenti(), ids=lambda p: str(p.relative_to(RADICE)))
def test_nessun_BOM_letterale_nel_sorgente(percorso):
    """U+FEFF nel sorgente: dove, su quale riga, e come si scrive invece."""
    testo = percorso.read_text(encoding='utf-8')
    if BOM not in testo:
        return
    # Un record per OCCORRENZA e non per riga: `r.index(BOM)` restituisce solo la
    # PRIMA colonna, quindi due U+FEFF sulla stessa riga venivano contati come uno e
    # la seconda posizione non veniva detta. In un messaggio che serve a TROVARE un
    # carattere invisibile, la posizione e' tutto il contenuto utile.
    # Segnalato da CodeRabbit sulla PR #21.
    occorrenze = [(n, r, c) for n, r in enumerate(testo.splitlines(), 1)
                  for c, carattere in enumerate(r, 1) if carattere == BOM]
    dettaglio = '\n'.join(
        f'    riga {n}, colonna {c}: {r.strip()[:70]!r}' for n, r, c in occorrenze)
    raise AssertionError(
        f'{percorso.relative_to(RADICE)} contiene {len(occorrenze)} U+FEFF letterali.\n'
        'Un U+FEFF e- invisibile in un editor: si scrive con l-escape.\n'
        "    sbagliato:  '<carattere invisibile>'.encode('utf-8')\n"
        "    giusto   :  '\\ufeff'.encode('utf-8')\n"
        f'{dettaglio}'
    )
