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


def _sorgenti() -> list[Path]:
    trovati = []
    for voce in SORGENTI:
        percorso = RADICE / voce
        if percorso.is_file():
            trovati.append(percorso)
            continue
        for p in percorso.rglob('*'):
            if p.is_file() and p.suffix in ESTENSIONI and '__pycache__' not in p.parts:
                trovati.append(p)
    return trovati


def test_ci_sono_sorgenti_da_controllare():
    """Il guardiano del guardiano: se l'elenco si svuota, il test resta verde a vuoto.

    Senza questo caso un errore nei percorsi — una cartella rinominata, un suffisso
    cambiato — trasformerebbe la verifica in un ciclo su zero file, cioe' in un
    `assert True` travestito. E' la classe di difetto che questo repository ha gia'
    pagato piu' volte: un test verde che non misura niente.
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
    righe = [(n, r) for n, r in enumerate(testo.splitlines(), 1) if BOM in r]
    dettaglio = '\n'.join(
        f'    riga {n}, colonna {r.index(BOM) + 1}: {r.strip()[:70]!r}' for n, r in righe)
    raise AssertionError(
        f'{percorso.relative_to(RADICE)} contiene {len(righe)} U+FEFF letterali.\n'
        'Un U+FEFF e- invisibile in un editor: si scrive con l-escape.\n'
        "    sbagliato:  '<carattere invisibile>'.encode('utf-8')\n"
        "    giusto   :  '\\ufeff'.encode('utf-8')\n"
        f'{dettaglio}'
    )
