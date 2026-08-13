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

# I documenti, per il solo controllo sugli omografi. Il test sul BOM li esclude di
# proposito (vedi `ESTENSIONI`), ma un omografo in un documento va trovato lo stesso:
# e' li' che l'ho commesso.
DOCUMENTI = ('README.txt', 'README.MD', 'SAAS.md', 'CLAUDE.md')

# Le lettere che si SCRIVONO come quelle latine e non lo sono. Solo alfabeti, non le
# cifre: `tests/relay/test_login.py` contiene cifre arabo-indiane come dato di prova
# deliberato, ed e' il test che dimostra perche' il controllo su
# `TELEGRAM_ADMIN_ID` non usa `.isdigit()`.
ALFABETI_OMOGRAFI = ('CYRILLIC', 'GREEK', 'ARMENIAN', 'CHEROKEE')


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


def _parole_miste(testo: str):
    """Le parole che mescolano lettere latine e lettere di un altro alfabeto.

    Il criterio e' la MESCOLANZA e non la presenza: `'токен'` in
    `tests/relay/test_autenticazione.py` e' una parola russa intera, usata come token
    esotico di prova, e va bene. Quello che non va bene e' una lettera cirillica **dentro**
    una parola latina, perche' li' e' invisibile: si legge come la latina che imita.
    """
    import unicodedata

    parole, corrente, prima_riga, riga = [], '', 1, 1
    for carattere in testo + '\n':
        if carattere == '\n':
            riga += 1
        if carattere.isalpha():
            if not corrente:
                prima_riga = riga
            corrente += carattere
            continue
        if corrente:
            parole.append((prima_riga, corrente))
            corrente = ''
    misti = []
    for numero, parola in parole:
        latine = any(unicodedata.name(c, '').startswith('LATIN') for c in parola)
        altre = [c for c in parola
                 if unicodedata.name(c, '').startswith(ALFABETI_OMOGRAFI)]
        if latine and altre:
            misti.append((numero, parola, altre))
    return misti


def _file_da_controllare_per_omografi() -> list[Path]:
    return _sorgenti() + [RADICE / nome for nome in DOCUMENTI if (RADICE / nome).is_file()]


@pytest.mark.parametrize('nome', DOCUMENTI)
def test_ogni_voce_di_DOCUMENTI_esiste(nome):
    """Il guardiano del guardiano, come per `SORGENTI`.

    Un documento rinominato uscirebbe altrimenti dalla scansione in silenzio, ed e'
    esattamente il difetto che CodeRabbit ha alzato come Major sulla PR #21 per l'altra
    lista: un controllo che smette di controllare senza dirlo.
    """
    assert (RADICE / nome).is_file(), (
        f'{nome} non esiste piu- (rinominato? spostato?): non viene piu- controllato per '
        'gli omografi, e senza questo test la cosa non si vedrebbe'
    )


@pytest.mark.parametrize('percorso', _file_da_controllare_per_omografi(),
                         ids=lambda p: str(p.relative_to(RADICE)))
def test_nessun_OMOGRAFO_dentro_una_parola_latina(percorso):
    """Una lettera cirillica in mezzo a una parola latina non si vede e non solleva.

    Ho commesso esattamente questo errore nella PR #24, scrivendo in `README.txt` la parola
    «scioglieva» con la quinta lettera sostituita da U+0435 (CYRILLIC SMALL LETTER IE) al
    posto della `e` latina. Qui la parola sbagliata **non** e' riportata letterale, per la
    stessa ragione per cui il BOM si scrive con l'escape: un test che contiene il difetto che
    cerca diventa rosso su se stesso, e allora si finisce per escluderlo dalla scansione —
    cioe' per spegnere il guardiano invece di correggere il codice.
    In un editor e in un browser le due lettere sono indistinguibili, e nessuno strumento si
    lamenta: `git diff` la mostra come una `e`, `python -m py_compile` non la vede perche' e'
    dentro una stringa o un commento, e una ricerca testuale di «scioglieva» non la trova.

    E' la stessa classe di difetto per cui esiste il test sul BOM qui sopra, e la stessa per
    cui «REGOLA CODIFICA» pretende che i marcatori emoji si confrontino per **codepoint**:
    un carattere che si legge come un altro rompe un confronto in silenzio. Se capitasse
    dentro `web/engine.js`, in un marcatore o in un nome di campo, il segnale non arriverebbe
    mai a XTrader e il servizio non segnalerebbe niente.

    Il messaggio dice riga, parola e codepoint, perche' in un difetto invisibile la posizione
    e' tutto il contenuto utile.
    """
    misti = _parole_miste(percorso.read_text(encoding='utf-8'))
    if not misti:
        return
    dettaglio = '\n'.join(
        f'    riga {numero}: {parola!r} contiene '
        + ', '.join(f'{c!r} (U+{ord(c):04X})' for c in altre)
        for numero, parola, altre in misti)
    raise AssertionError(
        f'{percorso.relative_to(RADICE)} contiene {len(misti)} parole che mescolano '
        'lettere latine e lettere di un altro alfabeto.\n'
        'Si leggono come parole normali e non lo sono: va riscritta la lettera latina.\n'
        f'{dettaglio}'
    )
