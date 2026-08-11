"""Guardie sui workflow di review AI.

Questi workflow decidono quando spendere su un modello a pagamento e quando un
check verde significa «revisionato» invece di «uscito senza guardare». Sono
quindi codice safety-critical quanto il relay, e vanno vincolati da test.

Il test legge i workflow REALI da .github/workflows/: non contiene una copia
delle regole. Se qualcuno restringe il set di file core, o allarga la condizione
del gate a label, qui diventa rosso.

Equivalente del test omonimo nel repository Bridge, adattato ai file core di
questo servizio.
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / '.github' / 'workflows'

GPT = WORKFLOWS / 'pr-review-gpt55.yml'
FABLE = WORKFLOWS / 'pr-review-claude-fable5.yml'
FUGU = WORKFLOWS / 'pr-review-openrouter-fugu-ultra.yml'

# File il cui cambiamento DEVE far spendere il reviewer forte su un push.
# main.py e' il relay; web/ e' la superficie multiutente e ospita il motore di
# parsing; Procfile e railway.json espongono il servizio su Internet; le
# dipendenze possono introdurre codice di terzi.
ATTESI_CORE = [
    'main.py',
    'web/app.js',
    'web/api.js',
    'web/engine.js',
    'web/index.html',
    'web/styles.css',
    # Il generatore del bundle: sta in tools/ e non in web/ perche- web/ e- servita
    # pubblicamente, ma resta CORE. E- lui che emette il JavaScript in ASCII puro,
    # e un difetto li- fa fallire in silenzio il confronto sul marcatore emoji —
    # l-incidente documentato nella REGOLA CODIFICA. Spostarlo fuori da web/ senza
    # aggiungere tools/ ai pattern core gli avrebbe tolto la review forte.
    'tools/build_single_file.py',
    'Procfile',
    'railway.json',
    'requirements.txt',
    'requirements-dev.txt',
    'pyproject.toml',
    'poetry.lock',
]

# File il cui cambiamento NON deve far spendere: restano coperti dai reviewer
# che girano su ogni push.
ATTESI_NON_CORE = [
    'CLAUDE.md',
    'SAAS.md',
    'README.txt',
    'README.MD',
    '.gitignore',
    '.github/workflows/pr-review-gpt55.yml',
    '.github/workflows/pr-review-claude-fable5.yml',
    'tests/safety/test_ai_audit_workflows.py',
    'docs/design/design_handoff.md',
]


def _carica(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding='utf-8'))


def _trigger(path: Path) -> dict:
    """La sezione `on:` del workflow.

    In YAML 1.1 la chiave nuda ``on`` e' il booleano ``True``, quindi PyYAML la
    carica come ``doc[True]`` e ``doc['on']`` solleva KeyError. Si accettano
    entrambe le forme per non dipendere dalla versione del parser.
    """
    doc = _carica(path)
    sezione = doc.get('on', doc.get(True))
    assert sezione, f'{path.name}: sezione on: non trovata'
    return sezione


def _script(path: Path) -> str:
    """Lo script dello step di review, dove vivono gate e prompt."""
    doc = _carica(path)
    steps = doc['jobs']['review']['steps']
    return '\n'.join(s.get('run') or '' for s in steps)


def _core_patterns() -> list[re.Pattern]:
    """I pattern CORE veri, estratti dal workflow di Fable."""
    blocco = re.search(
        r'CORE_TRIGGER_PATTERNS = \[(.*?)\n\s*\]', _script(FABLE), re.S
    )
    assert blocco, 'CORE_TRIGGER_PATTERNS non trovato nel workflow di Fable'
    pattern = re.findall(r're\.compile\(r"([^"]+)"\)', blocco.group(1))
    assert pattern, 'nessun pattern dentro CORE_TRIGGER_PATTERNS'
    return [re.compile(p) for p in pattern]


def _tocca_core(nomi: list[str]) -> bool:
    pats = _core_patterns()
    return any(any(p.search(n) for p in pats) for n in nomi)


# --------------------------------------------------------------- esistenza

def test_i_tre_workflow_esistono():
    for path in (GPT, FABLE, FUGU):
        assert path.is_file(), f'workflow mancante: {path.name}'


def test_yaml_valido_e_nome_atteso():
    assert _carica(GPT)['name'] == 'PR Review GPT-5.5'
    assert _carica(FABLE)['name'] == 'PR Review Claude Fable 5'
    assert _carica(FUGU)['name'] == 'PR Review OpenRouter Fugu Ultra'


# ------------------------------------------------------------- gate costo

@pytest.mark.parametrize('nome', ATTESI_CORE)
def test_file_core_fanno_spendere(nome):
    assert _tocca_core([nome]), (
        f'{nome} non e\' nel set core: un push che lo tocca uscirebbe verde '
        f'senza che nessun reviewer forte lo abbia letto'
    )


@pytest.mark.parametrize('nome', ATTESI_NON_CORE)
def test_file_non_core_non_fanno_spendere(nome):
    assert not _tocca_core([nome]), f'{nome} non dovrebbe far spendere il reviewer forte'


def test_un_solo_file_core_in_un_push_misto_basta():
    assert _tocca_core(['CLAUDE.md', 'README.txt', 'main.py'])
    assert not _tocca_core(['CLAUDE.md', 'README.txt'])


def test_niente_percorsi_del_bridge_nel_set_core():
    """xtrader_bridge/ e license_manager/ non esistono qui: se restassero nel set
    sarebbero pattern morti che nascondono l'assenza di quelli veri."""
    sorgente = _script(FABLE)
    for morto in ('xtrader_bridge/', 'license_manager/'):
        assert morto not in sorgente, f'percorso del Bridge rimasto: {morto}'


# ------------------------------------------------ ESECUZIONE della redazione

def _funzione_redact(path: Path):
    """Estrae dal workflow reale la tabella REDACTIONS e la funzione `redact`.

    Come per il gate, il test non riscrive i pattern: li ESEGUE sul testo. Una
    tabella di redazione verificata solo per struttura ("esiste un pattern che
    contiene xt_") passerebbe anche con una regex che non combacia con niente.
    """
    sorgente = _script(path)
    tabella = re.search(r'^(\s*)REDACTIONS = \[.*?^\1\]', sorgente, re.S | re.M)
    assert tabella, f'{path.name}: tabella REDACTIONS non trovata'
    funzione = re.search(r'^(\s*)def redact\(.*?(?=\n\1[A-Za-z_]|\Z)', sorgente, re.S | re.M)
    assert funzione, f'{path.name}: funzione redact non trovata'
    spazio: dict = {'re': re}
    exec(textwrap.dedent(tabella.group(0)), spazio)  # noqa: S102 - sorgente del repo
    exec(textwrap.dedent(funzione.group(0)), spazio)  # noqa: S102 - sorgente del repo
    return spazio['redact']


# Forma reale di un token di feed: prefisso `xt_` piu' almeno 18 byte casuali
# (SAAS.md). Qui 36 caratteri esadecimali, come li genera web/api.js.
TOKEN_FEED = 'xt_' + '7f3a91' * 6

# Token piu' corto della specifica: non dovrebbe esistere, ma se esistesse — un
# residuo, una configurazione a mano, un token generato da una versione
# precedente — deve essere redatto comunque. Segnalato da GPT-5.5 sulla PR #1
# come lacuna del limite inferiore. 12 caratteri stanno comodamente sopra i 6
# del `token_prefix`, che va invece lasciato passare.
TOKEN_FEED_CORTO = 'xt_' + 'a1b2c3d4e5f6'


@pytest.mark.parametrize('path', (GPT, FABLE, FUGU), ids=lambda p: p.name)
def test_il_token_di_feed_nudo_viene_redatto(path):
    """Un token di feed senza la keyword `token=` accanto non deve uscire.

    La regola contestuale della tabella pretende `token`/`api_key`/`secret`
    vicino al valore. Un token NUDO dentro un diff — un test, un fixture, un
    URL spezzato su due righe — non la attiva, e finirebbe in chiaro verso tre
    modelli esterni. E' la stessa classe del seed Ed25519 documentata nella
    tabella: un segreto senza pattern proprio esce e nessuno se ne accorge.

    Segnalato da Fugu Ultra sulla PR #1 come bloccante, ed e' fondato.
    """
    redact = _funzione_redact(path)
    ripulito = redact(f'il feed risponde su /feed/piero.csv con {TOKEN_FEED} attivo')
    assert TOKEN_FEED not in ripulito, (
        f'{path.name}: il token di feed nudo NON viene redatto e uscirebbe in chiaro'
    )
    corto = redact(f'token residuo {TOKEN_FEED_CORTO} da una versione precedente')
    assert TOKEN_FEED_CORTO not in corto, (
        f'{path.name}: un token piu- corto della specifica NON viene redatto'
    )


@pytest.mark.parametrize('path', (GPT, FABLE, FUGU), ids=lambda p: p.name)
def test_il_prefisso_di_9_caratteri_sopravvive_alla_redazione(path):
    """`token_prefix` e' fatto per essere mostrato: redigerlo renderebbe muta la UI.

    Il gemello del test sopra. Serve a impedire che il pattern venga allargato
    fino a mangiare anche il prefisso di 9 caratteri che il contratto conserva
    proprio per identificare un token senza rivelarlo.
    """
    redact = _funzione_redact(path)
    assert 'xt_7f3a91' in redact('token_prefix mostrato in tabella: xt_7f3a91'), (
        f'{path.name}: il prefisso di 9 caratteri viene redatto, ma non e- un segreto'
    )


# --------------------------------------------------- ESECUZIONE del gate

def _funzione_gate(path: Path, nome: str = 'decisione_gate'):
    """Estrae dal workflow reale la funzione pura del gate e la ESEGUE.

    I test qui sotto non riscrivono la regola: la eseguono. Il gate decide se il
    job spende, esce verde o esce rosso su un head stantio, ed e' il
    comportamento safety-critical di questi workflow: verificarne solo la
    struttura lascerebbe la decisione senza copertura.
    """
    blocco = re.search(
        rf'^(\s*)def {re.escape(nome)}\(.*?(?=\n\1[A-Za-z_]|\Z)',
        _script(path), re.S | re.M,
    )
    assert blocco, f'{path.name}: {nome} non trovata'
    spazio: dict = {}
    exec(textwrap.dedent(blocco.group(0)), spazio)  # noqa: S102 - sorgente del repo, non input esterno
    assert nome in spazio, f'{path.name}: {nome} non definita dopo exec'
    return spazio[nome]


def test_fugu_la_label_finale_dell_evento_fa_revisionare():
    gate = _funzione_gate(FUGU)
    assert gate('labeled', [], 'final-fugu-review', 'final-fugu-review') == 'revisiona'


def test_fugu_una_label_qualsiasi_non_arma_il_gate():
    """Aggiungere manual-review-required a una PR gia' etichettata non deve
    rieseguire la review su un head gia' revisionato."""
    gate = _funzione_gate(FUGU)
    assert gate('labeled', ['final-fugu-review'], 'final-fugu-review',
                'manual-review-required') == 'salta'


def test_fugu_pr_aperta_con_label_gia_presente_revisiona():
    """GitHub non emette `labeled` per una PR aperta con la label applicata."""
    gate = _funzione_gate(FUGU)
    assert gate('opened', ['final-fugu-review'], 'final-fugu-review') == 'revisiona'


def test_fugu_push_dopo_armamento_e_stantio():
    """Il caso della #274: un verde su un head che nessuno ha letto."""
    gate = _funzione_gate(FUGU)
    assert gate('synchronize', ['final-fugu-review'], 'final-fugu-review') == 'stantio'


def test_fugu_push_senza_label_non_spende():
    gate = _funzione_gate(FUGU)
    assert gate('synchronize', [], 'final-fugu-review') == 'salta'


def test_fable_push_su_file_core_spende():
    gate = _funzione_gate(FABLE)
    assert gate('synchronize', [], 'final-fable-review', '', True, False) == 'core'


def test_fable_push_senza_file_core_non_spende():
    gate = _funzione_gate(FABLE)
    assert gate('synchronize', [], 'final-fable-review', '', False, False) == 'salta'


def test_fable_lista_file_troncata_non_fa_saltare_al_buio():
    """Con la Compare API troncata non si puo' escludere un file core oltre il
    limite: meglio spendere che perdere una PR core grande."""
    gate = _funzione_gate(FABLE)
    assert gate('synchronize', [], 'final-fable-review', '', False, True) == 'core'


def test_fable_label_finale_revisiona_intera_pr():
    gate = _funzione_gate(FABLE)
    assert gate('labeled', [], 'final-fable-review', 'final-fable-review',
                False, False) == 'revisiona'


def test_fable_push_dopo_armamento_e_stantio():
    gate = _funzione_gate(FABLE)
    assert gate('synchronize', ['final-fable-review'], 'final-fable-review', '',
                True, False) == 'stantio'


def test_entrambi_i_gate_coprono_i_quattro_esiti():
    """Nessun esito resta senza test: se un domani se ne aggiunge uno, qui si vede."""
    fugu = _funzione_gate(FUGU)
    fable = _funzione_gate(FABLE)
    esiti_fugu = {
        fugu('labeled', [], 'final-fugu-review', 'final-fugu-review'),
        fugu('synchronize', ['final-fugu-review'], 'final-fugu-review'),
        fugu('synchronize', [], 'final-fugu-review'),
    }
    assert esiti_fugu == {'revisiona', 'stantio', 'salta'}
    esiti_fable = {
        fable('labeled', [], 'final-fable-review', 'final-fable-review', False, False),
        fable('synchronize', [], 'final-fable-review', '', True, False),
        fable('synchronize', [], 'final-fable-review', '', False, False),
        fable('synchronize', ['final-fable-review'], 'final-fable-review', '', True, False),
    }
    assert esiti_fable == {'revisiona', 'core', 'salta', 'stantio'}


# ------------------------------------------------------------ gate label

def test_fable_e_fugu_reagiscono_alla_label():
    for path in (FABLE, FUGU):
        tipi = _trigger(path)['pull_request']['types']
        assert 'labeled' in tipi, f'{path.name} non reagisce agli eventi labeled'


def test_gpt_non_reagisce_alla_label():
    """GPT-5.5 gira su ogni push e non ha bisogno del gate a label."""
    assert 'labeled' not in _trigger(GPT)['pull_request']['types']


def test_il_gate_si_arma_solo_con_la_propria_label():
    """La condizione del job filtra sulla label DELL'EVENTO, non sulla presenza
    della label nell'elenco: altrimenti aggiungere una label qualsiasi a una PR
    gia' etichettata rieseguirebbe la review su un head gia' revisionato."""
    attese = {FABLE: 'final-fable-review', FUGU: 'final-fugu-review'}
    for path, label in attese.items():
        cond = _carica(path)['jobs']['review']['if']
        assert 'github.event.label.name' in cond, (
            f'{path.name}: la condizione non guarda la label dell\'evento'
        )
        assert label in cond, f'{path.name}: la condizione non cita {label}'


def test_le_due_label_finali_sono_distinte():
    assert 'final-fable-review' in _script(FABLE)
    assert 'final-fugu-review' in _script(FUGU)
    assert 'final-fugu-review' not in _carica(FABLE)['jobs']['review']['if']
    assert 'final-fable-review' not in _carica(FUGU)['jobs']['review']['if']


# --------------------------------------------------------------- segreti

def test_nessuna_api_key_in_chiaro():
    """Le chiavi arrivano dai Secret del repo: nel file non deve comparirne una."""
    perdite = [
        re.compile(r'sk-(?:proj-)?[A-Za-z0-9_\-]{20,}'),
        re.compile(r'sk-or-v1-[A-Za-z0-9_\-]{20,}'),
        re.compile(r'\b\d{8,12}:[A-Za-z0-9_\-]{30,}\b'),  # bot token Telegram
    ]
    for path in (GPT, FABLE, FUGU):
        testo = path.read_text(encoding='utf-8')
        for pat in perdite:
            # Le stesse regex compaiono nella tabella REDACTIONS del workflow:
            # quelle sono definizioni, non valori. Si controllano le righe che
            # assegnano una chiave, non quelle che la redigono.
            for riga in testo.splitlines():
                if 'REDACTED' in riga or 're.compile' in riga:
                    continue
                assert not pat.search(riga), f'{path.name}: possibile segreto in chiaro'


def test_le_chiavi_vengono_dai_secret():
    """I nomi sono quelli dei Secret di QUESTO repository, non quelli del Bridge.

    Il proprietario ha creato `BETRELAY_GPT`, `BETRELAY_FABLE` e `BETRELAY_FUGU`.
    Un workflow che leggesse `secrets.OPENAI_API_KEY` troverebbe una stringa
    vuota e uscirebbe verde senza revisionare: nessun errore, nessun check
    rosso, e una PR con tre spunte e zero righe lette.
    """
    coppie = {
        GPT: 'secrets.BETRELAY_GPT',
        FABLE: 'secrets.BETRELAY_FABLE',
        FUGU: 'secrets.BETRELAY_FUGU',
    }
    for path, atteso in coppie.items():
        assert atteso in path.read_text(encoding='utf-8'), f'{path.name}: manca {atteso}'


def test_nessun_riferimento_ai_secret_del_bridge():
    """Nessun residuo dei nomi vecchi, in nessuno dei tre workflow.

    Serve perche' un riferimento dimenticato non fallisce: legge vuoto e salta
    la review in silenzio. E' la stessa classe di difetto del punto sopra, per
    questo va cercata su tutto il file e non solo sulla riga `env:`.
    """
    vecchi = ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'OPENROUTER_API_KEY')
    for path in (GPT, FABLE, FUGU):
        testo = path.read_text(encoding='utf-8')
        for nome in vecchi:
            assert nome not in testo, (
                f'{path.name}: contiene ancora {nome}, nome di Secret del Bridge '
                f'non configurato in questo repository'
            )


# ------------------------------------------------------------- permessi

def test_permessi_minimi_sul_codice():
    """I workflow commentano le PR ma non devono poter scrivere il codice."""
    for path in (GPT, FABLE, FUGU):
        perm = _carica(path)['permissions']
        assert perm['contents'] == 'read', f'{path.name}: contents non e\' read'
        assert perm['pull-requests'] == 'write'


def test_nessun_auto_merge():
    """Il merge resta manuale del proprietario: nessun workflow lo automatizza."""
    for path in (GPT, FABLE, FUGU):
        testo = path.read_text(encoding='utf-8').lower()
        for vietato in ('enable-pull-request-automerge', 'gh pr merge', 'automerge'):
            assert vietato not in testo, f'{path.name}: contiene {vietato}'


# ---------------------------------------------- BLOCCANTI FANTASMA (Issue #6)
#
# Le review forti hanno alzato bloccanti su cose che il codice fa correttamente,
# e la causa non era il modello: era l'input che il workflow gli manda. Due
# meccanismi distinti, misurati sulla PR #8, che questi test vincolano.


def _funzione_payload(path: Path):
    """Estrae ed ESEGUE `build_patch_payload` dal workflow reale.

    Stessa tecnica di `_funzione_redact`: il test non riscrive la logica di
    budget, la esegue. Verificare per struttura («esiste un sort») passerebbe
    anche con un ordinamento che non mette i file core davanti.
    """
    sorgente = _script(path)
    nome = 'build_patch_payload' if 'build_patch_payload' in sorgente else 'costruisci_patch'
    pezzi = []
    for f in ('CRITICAL_PATTERNS = [', 'REDACTIONS = [', 'PRIORITA_PAYLOAD = ['):
        blocco = re.search(r'^(\s*)' + re.escape(f) + r'.*?^\1\]', sorgente, re.S | re.M)
        assert blocco, f'{path.name}: {f} non trovata'
        pezzi.append(blocco.group(0))
    for f in ('def redact(', 'def safe_display(', 'def is_critical(',
              'def priorita_payload(', f'def {nome}('):
        blocco = re.search(r'^(\s*)' + re.escape(f) + r'.*?(?=\n\1[A-Za-z_@]|\Z)', sorgente, re.S | re.M)
        assert blocco, f'{path.name}: {f} non trovata'
        pezzi.append(blocco.group(0))
    spazio: dict = {'re': re}
    for pezzo in pezzi:
        exec(textwrap.dedent(pezzo), spazio)  # noqa: S102 - sorgente del repo
    return spazio[nome]


def _file_finto(nome: str, righe: int = 60):
    """Un elemento della lista file come la restituisce l'API GitHub."""
    patch = '\n'.join(f'+riga {i} di {nome}' for i in range(righe))
    return {'filename': nome, 'status': 'modified', 'additions': righe,
            'deletions': 0, 'changes': righe, 'patch': patch}


# I 13 file della PR #8, nell'ordine in cui l'API GitHub li restituisce
# (alfabetico). `web/` e' ULTIMO, ed e' il punto di tutto questo blocco.
FILE_COME_LA_PR_8 = [
    'CLAUDE.md', 'README.txt', 'SAAS.md', 'main.py',
    'tests/ambiente.py', 'tests/engine/engine_cases.mjs',
    'tests/engine/test_engine_contract.py', 'tests/relay/test_csv_contract.py',
    'tests/safety/test_ambiente_dei_test.py', 'tests/web/prototype_flow.py',
    'tests/web/test_prototype_flow.py', 'web/app.js', 'web/engine.js',
    # File di dipendenze: la tabella CRITICAL_PATTERNS lo tratta come critico, quindi
    # deve essere tier-0 anche nel payload. Segnalato da CodeRabbit.
    'poetry.lock',
]


@pytest.mark.parametrize('path', (GPT, FABLE, FUGU), ids=lambda p: p.name)
def test_i_file_core_entrano_nel_payload_anche_col_budget_stretto(path):
    """Il motore non deve mai finire fra i file saltati per budget.

    Misurato sulla PR #8: `web/engine.js` e `web/app.js` sono stati saltati da
    TUTTE E QUATTRO le review finali, e i modelli hanno alzato tre bloccanti
    «parita' JS/Python non verificabile». Non era sfortuna: il budget viene
    consumato nell'ordine in cui l'API restituisce i file, cioe- ALFABETICO, e
    `web/` e- ultimo. Su qualsiasi PR abbastanza grande il motore e-
    strutturalmente garantito di essere il primo scartato, mentre `CLAUDE.md` —
    1300 righe di documentazione — viene mandato per intero.

    Il costo non e- solo il rumore: quei bloccanti fantasma sono arrivati da
    review finali da ~$0.36 e ~$0.70 l'una.
    """
    build = _funzione_payload(path)
    files = [_file_finto(n) for n in FILE_COME_LA_PR_8]

    # Budget deliberatamente insufficiente per tutti e 13: qualcosa DEVE essere
    # scartato. Il test non chiede di non scartare — chiede di scartare i file
    # giusti.
    testo, saltati, critici, troncato = build(files, 2000, 9000)

    assert 'web/engine.js' not in saltati, (
        'il motore e- stato scartato per budget mentre la documentazione entrava: '
        f'saltati={saltati}'
    )
    assert 'FILE: web/engine.js' in testo, 'il motore non e- nel payload mandato al modello'
    assert 'main.py' not in saltati, f'il relay e- stato scartato: saltati={saltati}'
    assert 'FILE: main.py' in testo, 'il relay non e- nel payload'
    assert 'poetry.lock' not in saltati, (
        f'il lockfile delle dipendenze e- stato scartato: saltati={saltati}'
    )

    # E i test sotto `tests/web/` NON sono core: contengono `/web/` nel percorso, e con
    # l'ancora larga prendevano rango 0 consumando il budget del codice. E- il difetto
    # che ha spinto fuori `poetry.lock`, trovato grazie al rilievo di CodeRabbit.
    inclusi = re.findall(r'FILE: (\S+)', testo)
    for finto_core in ('tests/web/prototype_flow.py', 'tests/web/test_prototype_flow.py'):
        assert finto_core not in inclusi[:4], (
            f'{finto_core} e- entrato fra i primi file come se fosse core: il pattern '
            f'delle cartelle non e- ancorato alla radice.\nordine: {inclusi}'
        )


@pytest.mark.parametrize('path', (GPT, FABLE, FUGU), ids=lambda p: p.name)
def test_i_file_core_precedono_i_documenti_nel_payload(path):
    """L'ordine, non solo la presenza: il core va letto prima.

    Con un budget appena sufficiente per pochi file, quelli che entrano devono
    essere il codice. Un test che guardasse solo la presenza passerebbe anche con
    un budget cosi- generoso da non scartare niente, cioe- senza esercitare la
    priorita-.
    """
    build = _funzione_payload(path)
    files = [_file_finto(n) for n in FILE_COME_LA_PR_8]
    testo, saltati, _, _ = build(files, 2000, 5000)

    posizione = {n: testo.find(f'FILE: {n}') for n in FILE_COME_LA_PR_8}
    core = {n: p for n, p in posizione.items() if p >= 0 and (n == 'main.py' or n.startswith('web/'))}
    docs = {n: p for n, p in posizione.items() if p >= 0 and n.endswith(('.md', '.txt'))}

    assert core, f'nessun file core nel payload: saltati={saltati}'
    if docs:
        assert max(core.values()) < min(docs.values()), (
            f'un documento precede un file core nel payload: core={core} docs={docs}'
        )


# ------------------------- comporre le stringhe di prova, invece di scriverle
#
# Un file che testa un REDATTORE non puo' contenere le stringhe che il redattore
# riconosce: quando questo file finisce nel payload di una review viene redatto
# come qualunque altro diff, e il reviewer riceve Python con literal non terminati.
#
# Misurato su 988bb6e: 32 righe di questo file arrivavano maciullate, e GPT-5.5 ne
# ha dedotto un bloccante per errore di sintassi. Non allucinava — descriveva
# accuratamente il testo rotto che gli era arrivato. Il file piu' critico della PR
# era diventato l'unico irrevisionabile.
#
# Quindi le parti sensibili si assemblano a runtime: nel SORGENTE non compare mai
# `<chiave>: <espressione>` contiguo, mentre il valore passato a `redact()` e'
# esattamente quello.
_D = '$'
_ESPR = _D + '{{ secrets.%s }' + '}'
_CHIAVE = 'API' '_KEY'
_CHIAVE_SECONDA = 'TOK' + 'EN'


def _espressione(nome: str = 'NOME') -> str:
    """L'espressione di GitHub Actions, composta e non scritta."""
    return _ESPR % nome


def _assegnazione(chiave: str, valore: str, sep: str = ': ') -> str:
    """Una riga `chiave<sep>valore`, con la chiave sensibile passata a pezzi."""
    return f'{chiave}{sep}{valore}\n'

# Riga reale di un workflow di questo repository: il nome del Secret e- proprio
# l'informazione che serve al reviewer per giudicare, e la #6 nasce dal fatto che
# veniva cancellata.
# Le assegnazioni del fixture, come LISTA: il test le itera invece di ritrovarle
# dentro una stringa con un filtro a sottostringhe. Un filtro del genere andava
# allungato a ogni chiave nuova (`SECRET`, `PASSWORD`, minuscole...) e ogni volta che
# qualcuno se ne dimenticava il fixture perdeva copertura in silenzio. Segnalato da
# GPT-5.5, e la lista rimuove la classe invece della singola omissione.
ASSEGNAZIONI_CON_SEGRETO = [
    _assegnazione('PROVIDER_' + _CHIAVE, _espressione('BETRELAY_FABLE')),
    _assegnazione('ALTRO_' + _CHIAVE, _espressione('BETRELAY_GPT')),
    _assegnazione('GITHUB_' + _CHIAVE_SECONDA, _espressione('GITHUB_TOKEN')),
    # Forma FRA VIRGOLETTE: la prima versione del lookahead la lasciava passare al
    # redattore, perche' guardava subito dopo i due punti e trovava la virgoletta
    # invece del dollaro. Segnalato da Sourcery, ed e' una forma YAML comunissima.
    _assegnazione('QUOTATO_' + _CHIAVE, '"' + _espressione('BETRELAY_FUGU') + '"'),
    _assegnazione('APICE_' + _CHIAVE, "'" + _espressione('BETRELAY_FUGU') + "'"),
]

YAML_CON_SEGRETO = (
    '      - name: Review\n'
    '        env:\n'
    + ''.join('          ' + r for r in ASSEGNAZIONI_CON_SEGRETO)
)


@pytest.mark.parametrize('path', (GPT, FABLE, FUGU), ids=lambda p: p.name)
def test_un_riferimento_a_secrets_non_viene_maciullato(path):
    """`${{ secrets.X }}` e- un PUNTATORE a un segreto, non un segreto.

    La regola contestuale della tabella cattura `api_key: <valore>` e sostituisce
    tutto con `[REDACTED]`. Su un YAML di workflow questo cancella il NOME del
    Secret, che e- esattamente cio- che il reviewer deve leggere per dire se e-
    quello giusto — e in questo repository i nomi giusti (`BETRELAY_*`) sono
    diversi da quelli del Bridge, quindi il rilievo e- reale e frequente.

    Misurato: e- il meccanismo con cui la PR #1 ha raccolto cinque bloccanti
    fantasma, ~$0.9 per giro, tutti refutabili leggendo il file.

    Il valore vero resta redatto — lo verifica il test accanto.
    """
    redact = _funzione_redact(path)
    ripulito = redact(YAML_CON_SEGRETO)

    for nome_secret in ('BETRELAY_FABLE', 'BETRELAY_GPT', 'BETRELAY_FUGU'):
        assert nome_secret in ripulito, (
            f'il nome del Secret {nome_secret} e- stato cancellato dalla redazione: '
            f'il reviewer non puo- giudicare se e- quello giusto.\n{ripulito}'
        )
    assert ripulito.count('${{') == YAML_CON_SEGRETO.count('${{'), (
        f'{ripulito.count("${{")} espressioni sopravvissute su '
        f'{YAML_CON_SEGRETO.count("${{")}: contarle e- l\'unico modo di accorgersi che '
        f'una FORMA (fra virgolette, con apici) viene ancora maciullata mentre le '
        f'altre passano.\n{ripulito}'
    )


@pytest.mark.parametrize('path', (GPT, FABLE, FUGU), ids=lambda p: p.name)
def test_l_esenzione_copre_solo_l_espressione_COMPLETA(path):
    """Segnalato da CodeRabbit, ed e- la direzione pericolosa dell'esenzione.

    Esentare qualunque valore che COMINCI con un'espressione lascia uscire cio-
    che le sta accanto: in `API_KEY: <espressione>segreto-letterale` il suffisso
    non e- un puntatore, e- un segreto, e nessun altro pattern della tabella lo
    riconosce. L'esenzione vale quindi solo se il valore e- INTERAMENTE una
    espressione — dopo la chiusura non deve restare altro che una virgoletta.

    Il test precedente (le forme fra virgolette) e questo tirano in direzioni
    opposte, ed e- il punto: servono entrambi perche- l'esenzione stia esattamente
    dove deve.
    """
    redact = _funzione_redact(path)

    # Il valore sensibile viene NOMINATO e cercato nell'output: la prima versione di
    # questo test si accontentava di trovare `[REDACTED]` da qualche parte, e passava
    # mentre la coda del segreto restava nel payload. Bloccante di Fugu Ultra, ed era
    # un test decorativo — quelli che CLAUDE.md vieta — scritto da me nella patch che
    # doveva chiudere proprio questa classe di falla.
    for riga, sensibile in (
        (_assegnazione(_CHIAVE, _espressione() + 'CODA-INCOLLATA'), 'CODA-INCOLLATA'),
        (_assegnazione(_CHIAVE_SECONDA, _espressione() + 'coda-sensibile', ' = '), 'coda-sensibile'),
        (_assegnazione(_CHIAVE, 'PREFISSO-SEGRETO' + _espressione()), 'PREFISSO-SEGRETO'),
        (_assegnazione(_CHIAVE, 'chiave-vera-scritta-a-mano'), 'chiave-vera-scritta-a-mano'),
        # Coda separata da uno SPAZIO: la regola del letterale incollato pretende un
        # carattere adiacente alle graffe di chiusura, quella contestuale si ferma al
        # primo spazio, e in mezzo passava il segreto. Bloccante di Fable 5 sul gate
        # finale, e la stessa classe che aveva trovato Fugu: un separatore di distanza.
        (_assegnazione(_CHIAVE, _espressione() + ' CODA-SPAZIO'), 'CODA-SPAZIO'),
        (_assegnazione(_CHIAVE_SECONDA, _espressione() + ' uno DUE tre', ' = '), 'DUE'),
        # Le varianti restanti della STESSA forma, enumerate invece di aspettare che il
        # prossimo reviewer trovi la successiva. Due volte di fila ho corretto
        # un'ISTANZA e due reviewer diversi hanno trovato quella dopo: e' la regola 2
        # mancata sullo stesso pattern.
        (_assegnazione(_CHIAVE, _espressione() + '\tCODA-TAB', ':\t'), 'CODA-TAB'),
        (_assegnazione(_CHIAVE, 'PREFISSO-SPAZIO ' + _espressione()), 'PREFISSO-SPAZIO'),
        (_assegnazione(_CHIAVE, _espressione('A') + _espressione('B') + 'DUE-ESPR'), 'DUE-ESPR'),
        (_assegnazione(_CHIAVE, '"' + _espressione() + ' IN-VIRGOLETTE"'), 'IN-VIRGOLETTE'),
        (_assegnazione(_CHIAVE, "'" + _espressione() + " IN-APICI'"), 'IN-APICI'),
        (_assegnazione(_CHIAVE, '"' + _espressione() + '"DOPO-VIRGOLETTA'), 'DOPO-VIRGOLETTA'),
        (_assegnazione('Authorization', 'Bearer ' + _espressione() + 'DOPO-BEARER'), 'DOPO-BEARER'),
    ):
        ripulito = redact(riga)
        assert sensibile not in ripulito, (
            f'{path.name}: il valore sensibile {sensibile!r} e- SOPRAVVISSUTO alla '
            f'redazione ed uscirebbe verso un modello esterno.\n'
            f'  in : {riga.strip()}\n  out: {ripulito.strip()}'
        )

    # La prosa senza una chiave sensibile non viene toccata dalla regola
    # sull'assegnazione: quella pretende `<chiave>: <espressione>`, quindi una frase che
    # cita un'espressione resta leggibile per il reviewer.
    prosa = 'usa ' + _espressione() + ' per autenticarti'
    assert redact(prosa) == prosa, (
        f'{path.name}: la regola sull-assegnazione ha mangiato della prosa: {redact(prosa)!r}'
    )

    # E le espressioni COMPLETE restano intatte, col nome del Secret leggibile: e- il
    # motivo per cui l'esenzione esiste, e va verificato nella stessa funzione perche-
    # le due proprieta- tirano in direzioni opposte.
    for riga in (
        _assegnazione(_CHIAVE, _espressione('BETRELAY_FABLE')),
        _assegnazione(_CHIAVE, '"' + _espressione('BETRELAY_FABLE') + '"'),
        _assegnazione(_CHIAVE, "'" + _espressione('BETRELAY_FABLE') + "'"),
    ):
        ripulito = redact(riga)
        assert 'BETRELAY_FABLE' in ripulito and '${{' in ripulito, (
            f'{path.name}: espressione completa maciullata: {ripulito.strip()!r}'
        )


@pytest.mark.parametrize('path', (GPT, FABLE, FUGU), ids=lambda p: p.name)
def test_un_valore_di_segreto_VERO_resta_redatto(path):
    """L'altra faccia: esentare `${{ … }}` non deve aprire un varco.

    Senza questo, la correzione del test sopra potrebbe essere fatta allentando
    la regola contestuale, e un segreto scritto a mano in un file uscirebbe verso
    tre modelli esterni.
    """
    redact = _funzione_redact(path)
    for riga in (
        _assegnazione('PROVIDER_' + _CHIAVE, 'sk-ant-api03-VALOREFINTOCHENONDEVEUSCIRE00'),
        _assegnazione('api' '_key', '"chiave-scritta-a-mano-per-errore"', ' = '),
        _assegnazione('CSV_ACCESS_' + _CHIAVE_SECONDA, 'FINTO-non-un-token-0000000000'),
    ):
        ripulito = redact(riga)
        assert '[REDACTED' in ripulito, f'{path.name}: valore NON redatto: {riga} -> {ripulito}'


def test_gpt55_ha_un_budget_di_output_sufficiente():
    """`MAX_OUTPUT_TOKENS: 1000` su un modello reasoning tronca e ripaga ogni giro.

    Segnalato da CodeRabbit sulla PR #1 (thread rimasto non risolto) e confermato
    sul campo: su PR #8 GPT-5.5 ha scritto «il diff dei test e- troncato» su due
    head diversi. I token di reasoning contano nel budget di output, quindi 1000
    e- il motivo del troncamento, non la protezione dal costo — il costo vero e-
    pagare piu- volte la stessa review incompleta.

    Gli altri due workflow usano 3000.
    """
    # Il valore che il job usa davvero, dal blocco `env:` dello step, non da una
    # regex sul testo grezzo: `_script()` restituisce solo i corpi `run:`.
    doc = _carica(GPT)
    env = {**(doc.get('env') or {}), **(doc['jobs']['review'].get('env') or {})}
    assert 'MAX_OUTPUT_TOKENS' in env, 'MAX_OUTPUT_TOKENS non dichiarato nel blocco env'
    dichiarato = int(str(env['MAX_OUTPUT_TOKENS']))
    assert dichiarato >= 3000, (
        f'MAX_OUTPUT_TOKENS={dichiarato} su un modello reasoning: tronca la review e la '
        f'fa ripagare a ogni giro. Gli altri due workflow usano 3000.'
    )

    # E il default DENTRO lo script deve concordare: se divergesse, togliere la riga
    # dall'`env:` farebbe regredire il budget senza che niente diventi rosso — la
    # stessa classe di difetto dei Secret col nome sbagliato.
    m = re.search(r'MAX_OUTPUT_TOKENS = int\(os\.environ\.get\("MAX_OUTPUT_TOKENS", "(\d+)"\)\)',
                  _script(GPT))
    assert m, 'default di MAX_OUTPUT_TOKENS non trovato nello script'
    assert int(m.group(1)) >= 3000, (
        f'il default nello script e- {m.group(1)} mentre env dichiara {dichiarato}: '
        f'togliere la riga da env farebbe tornare il troncamento in silenzio'
    )


@pytest.mark.parametrize('path', (GPT, FABLE, FUGU), ids=lambda p: p.name)
def test_ogni_workflow_ha_un_tier_di_escalation_del_budget(path):
    """Se il diff risulta troncato, il job deve poter ricostruire piu- largo.

    Fable e Fugu lo fanno; GPT-5.5 non aveva nessun tier, quindi un diff grande
    restava tagliato senza rimedio. Tre implementazioni dello stesso contratto:
    la regola 3 vale anche qui, e non possono condividere codice perche- i
    workflow non fanno checkout.
    """
    sorgente = _script(path)
    for chiave in ('MAX_TOTAL_PATCH_CHARS_ESCALATED', 'MAX_PATCH_PER_FILE_CHARS_ESCALATED'):
        assert chiave in sorgente, f'{path.name}: manca {chiave}'

    # La presenza non basta: un tier dichiarato ma piu' basso del riferimento
    # sarebbe un'escalation che non escala. Segnalato da Sourcery.
    def budget(p: Path, chiave: str) -> int:
        doc = _carica(p)
        env = {**(doc.get('env') or {}), **(doc['jobs']['review'].get('env') or {})}
        assert chiave in env, f'{p.name}: {chiave} non nel blocco env'
        return int(str(env[chiave]))

    for chiave in ('MAX_TOTAL_PATCH_CHARS_ESCALATED', 'MAX_PATCH_PER_FILE_CHARS_ESCALATED'):
        assert budget(path, chiave) >= budget(FABLE, chiave), (
            f'{path.name}: {chiave}={budget(path, chiave)} e- sotto il riferimento '
            f'{FABLE.name}={budget(FABLE, chiave)}: l\'escalation non escala'
        )
        assert budget(path, chiave) > budget(path, chiave.replace('_ESCALATED', '')), (
            f'{path.name}: {chiave} non e- maggiore del budget base: il tier alto '
            f'non aggiunge niente e la ricostruzione e- solo una spesa in piu-'
        )


# ------------------------------------------- SINTASSI dello script incorporato

def _script_python(path: Path) -> tuple[str, str]:
    """Estrae il corpo Python dall'heredoc dentro lo shell script dello step.

    I workflow non fanno checkout e non hanno un file .py: lo script vive in un
    `python3 <<'PY' … PY` dentro un `run:` bash. Nessuno lo compila mai, quindi un
    errore di sintassi si scoprirebbe solo a job rosso — e su un reviewer
    opzionale un job rosso somiglia molto a un reviewer che non ha niente da dire.
    """
    doc = _carica(path)
    trovati = []
    for step in doc['jobs']['review']['steps']:
        run = step.get('run') or ''
        for m in re.finditer(r"python3?\s+(?:-\s+)?<<\s*'?(\w+)'?\n(.*?)\n\1\s*$", run, re.S | re.M):
            trovati.append((m.group(2), step.get('name', '?')))
    assert trovati, f'{path.name}: nessuno script Python trovato nello step di review'
    # Deterministico, non "il primo che capita": segnalato da Sourcery. Se un
    # domani ne comparisse un secondo, prendere il primo in silenzio farebbe
    # compilare il pezzo sbagliato e il test direbbe verde sul file non letto.
    assert len(trovati) == 1, (
        f'{path.name}: {len(trovati)} script Python nello step di review '
        f'({", ".join(n for _, n in trovati)}): l\'estrattore non sa quale controllare, '
        f'va reso esplicito prima di fidarsi di questo test'
    )
    return trovati[0]


@pytest.mark.parametrize('path', (GPT, FABLE, FUGU), ids=lambda p: p.name)
def test_lo_script_del_workflow_ha_sintassi_valida(path):
    """Il Python incorporato deve compilare, e va compilato qui perche' non lo fa nessuno."""
    import ast
    corpo, nome_step = _script_python(path)
    assert len(corpo.splitlines()) > 200, (
        f'{path.name}: estratte solo {len(corpo.splitlines())} righe dallo step '
        f'"{nome_step}": l\'estrattore ha preso il pezzo sbagliato e il test non '
        f'starebbe controllando niente'
    )
    try:
        ast.parse(corpo)
    except SyntaxError as e:
        righe = corpo.splitlines()
        contesto = righe[e.lineno - 1].strip() if e.lineno and e.lineno <= len(righe) else '?'
        raise AssertionError(
            f'{path.name}: SyntaxError nello script dello step "{nome_step}" '
            f'alla riga {e.lineno}: {e.msg}\n    >>> {contesto}'
        ) from e


@pytest.mark.parametrize('path', (GPT, FABLE, FUGU), ids=lambda p: p.name)
def test_la_lista_di_priorita_e_identica_nei_tre_workflow(path):
    """Regola 3 dove una fonte unica non e' possibile.

    I workflow non fanno checkout, quindi non possono importare un modulo comune:
    la lista di priorita' e' per forza in tre copie. Tre copie corrette oggi sono
    tre copie divergenti domani, e la divergenza qui e' invisibile — un reviewer
    che legge i file sbagliati esce verde. Questo test le tiene allineate.
    """
    def estrai(p: Path) -> str:
        m = re.search(r'^(\s*)PRIORITA_PAYLOAD = \[.*?^\1\]', _script(p), re.S | re.M)
        assert m, f'{p.name}: PRIORITA_PAYLOAD non trovata'
        return textwrap.dedent(m.group(0))

    assert estrai(path) == estrai(FABLE), (
        f'{path.name}: la lista di priorita\' del payload differisce da quella di '
        f'{FABLE.name}. Tre copie che divergono sono tre reviewer che leggono file diversi.'
    )


@pytest.mark.parametrize('path', (GPT, FABLE, FUGU), ids=lambda p: p.name)
def test_lo_script_non_contiene_espressioni_di_actions_letterali(path):
    """Dentro lo script non si scrive la forma dollaro-graffa-graffa, nemmeno nei commenti.

    Actions interpola quelle espressioni in TUTTO il file di workflow, `run:`
    compreso: non sono testo inerte. Misurato scrivendone tre in un commento di
    questi stessi workflow — una puntava a un Secret vero, quindi il suo VALORE
    sarebbe finito nel sorgente dello script — e i tre workflow hanno smesso di
    caricare del tutto: tre run con `event: push`, zero job, e il nome mostrato
    come percorso del file invece del `name:` del workflow.

    Il modo giusto di parlarne in un commento e' descriverla, o scriverla nella
    forma escapata che la regex usa comunque.

    Nota sull'ambito: qui si guarda SOLO lo script incorporato. Nel resto del
    workflow le espressioni sono legittime e necessarie — `env:`, `if:`,
    `concurrency:` — e vietarle la' romperebbe tutto.
    """
    corpo, nome_step = _script_python(path)
    colpevoli = [
        (n, riga.strip()) for n, riga in enumerate(corpo.splitlines(), 1)
        if '${{' in riga
    ]
    assert not colpevoli, (
        f'{path.name}: espressione di Actions letterale nello script dello step '
        f'"{nome_step}" — verra- interpolata prima dell\'esecuzione:\n  '
        + '\n  '.join(f'riga {n}: {r[:100]}' for n, r in colpevoli)
    )


@pytest.mark.parametrize('path', (GPT, FABLE, FUGU), ids=lambda p: p.name)
def test_la_redazione_sovra_redige_la_punteggiatura_ed_e_una_SCELTA(path):
    """Documenta un compromesso, invece di lasciarlo scoprire a un futuro rilievo.

    Segnalato da GPT-5.5: un'espressione seguita da punteggiatura in prosa —
    `vedi <espressione>) nel testo` — viene redatta insieme alla punteggiatura.
    Non e' una fuga di segreti, e' payload di review alterato, e in un progetto che
    sta cercando di far leggere ai reviewer il file giusto e' un costo reale.

    Non viene corretto, e la ragione va scritta perche' non e' ovvia. Il rimedio
    naturale sarebbe far scattare la regola solo su caratteri plausibili come
    inizio di un segreto (`[A-Za-z0-9_+/=-]`), escludendo `)`, `,`, `.`, backtick.
    Ma quella esclusione riapre la falla: con `API_KEY: <espressione>.coda-segreta`
    la regola del letterale incollato non scatterebbe, quella contestuale si
    fermerebbe allo spazio, e `.coda-segreta` uscirebbe. Misurato.

    Fra sovra-redigere della prosa e lasciare uscire un segreto, un redattore
    sceglie il primo. Questo test fissa quella scelta: se un domani qualcuno
    restringesse il trigger, il test accanto sulla coda incollata diventa rosso, e
    questo diventa rosso al contrario — cioe' la coppia rende visibile il baratto.
    """
    redact = _funzione_redact(path)

    # Il comportamento attuale, dichiarato: la punteggiatura attaccata viene inghiottita.
    assert '[REDACTED_VALORE_INCOLLATO]' in redact('vedi ' + _espressione() + ') nel testo'), (
        f'{path.name}: comportamento cambiato. Se e- stato ristretto il trigger, '
        f'verificare che `<espressione>.coda-segreta` non esca piu- in chiaro.'
    )
    # E cio- che NON e' attaccato resta leggibile: la prosa attorno non si perde.
    prosa_punt = 'vedi ' + _espressione() + ') nel testo'
    ripulito = redact(prosa_punt)
    assert 'nel testo' in ripulito and ripulito.startswith('vedi ')


def test_la_tabella_di_redazione_e_identica_nei_tre_workflow():
    """Regola 3 sulla redazione, non solo sulle priorita'.

    Chiesto da GPT-5.5, e il rischio e' concreto: tre reviewer con policy di
    sanitizzazione diverse significa che un segreto redatto verso uno esce verso un
    altro, e nulla lo segnala. I workflow non fanno checkout, quindi le tre copie
    sono inevitabili — questo test le tiene allineate.
    """
    def tabella(p: Path) -> str:
        m = re.search(r'^(\s*)REDACTIONS = \[.*?^\1\]', _script(p), re.S | re.M)
        assert m, f'{p.name}: tabella REDACTIONS non trovata'
        return textwrap.dedent(m.group(0))

    riferimento = tabella(FABLE)
    for p in (GPT, FUGU):
        assert tabella(p) == riferimento, (
            f'{p.name}: la tabella di redazione differisce da {FABLE.name}. Tre policy '
            f'diverse sono un segreto redatto verso un modello e in chiaro verso un altro.'
        )


# Tetto minimo di output per workflow. Non uno solo per tutti, perche' i tre modelli
# consumano il budget in modo diverso e il numero deve seguire la MISURA, non la
# simmetria.
TETTO_MINIMO_OUTPUT = {
    'pr-review-gpt55.yml': 3000,
    'pr-review-claude-fable5.yml': 3000,
    # Fugu ragiona molto e non si puo' abbassare: `low` non e' fra i suoi
    # supported_efforts (`max`/`xhigh`/`high`), quindi `high` e- il minimo. Misurato
    # sulla PR #9: con il tetto a 3000 ha speso 3000 token di completion di cui
    # **3000 di reasoning (100%)** e ha prodotto ZERO righe di review, a $0.168.
    # Un tetto che non lascia spazio al testo dopo il ragionamento fa pagare una
    # review che non esiste — che e' peggio di una review piu' cara.
    'pr-review-openrouter-fugu-ultra.yml': 8000,
}


@pytest.mark.parametrize('path', (GPT, FABLE, FUGU), ids=lambda p: p.name)
def test_il_tetto_di_output_lascia_spazio_al_testo_dopo_il_reasoning(path):
    """I token di reasoning sono fatturati come output E contano nel tetto.

    E- la stessa classe di difetto in due posti diversi, misurata due volte su questa
    PR: GPT-5.5 a 1000 troncava a ogni giro, e Fugu a 3000 ha consumato il tetto
    INTERO in ragionamento senza scrivere niente. In entrambi i casi il commento nel
    file sosteneva che il tetto non fosse un problema perche- «il prompt controlla la
    lunghezza reale»: vero per il testo, falso per il reasoning.

    Il tetto non e- il costo — si pagano i token generati — quindi tenerlo basso non
    risparmia: fa pagare review incomplete e le fa ripagare al giro dopo.
    """
    doc = _carica(path)
    env = {**(doc.get('env') or {}), **(doc['jobs']['review'].get('env') or {})}
    assert 'MAX_OUTPUT_TOKENS' in env, f'{path.name}: MAX_OUTPUT_TOKENS non dichiarato'
    dichiarato = int(str(env['MAX_OUTPUT_TOKENS']))
    minimo = TETTO_MINIMO_OUTPUT[path.name]
    assert dichiarato >= minimo, (
        f'{path.name}: MAX_OUTPUT_TOKENS={dichiarato}, minimo {minimo}. '
        f'Un tetto che il reasoning esaurisce da solo produce una review vuota a '
        f'pagamento.'
    )


def test_le_helper_compongono_esattamente_le_forme_attese():
    """La guardia delle guardie: se una helper si rompe, i test passano a VUOTO.

    Le stringhe di prova sono assemblate a runtime per non farsi redigere (vedi
    sopra), e il prezzo di quella scelta e- che una helper sbagliata — un dollaro
    perso, una graffa in meno — produrrebbe vettori che il redattore non riconosce.
    Ogni test sulla redazione diventerebbe verde senza esercitare niente, ed e- il
    modo piu- silenzioso di perdere una suite.

    Chiesto da GPT-5.5, ed e- il rilievo giusto: la composizione e- codice, e il
    codice non testato si rompe.
    """
    assert _espressione() == '${' + '{ secrets.NOME }' + '}', repr(_espressione())
    assert _espressione('X').startswith('$'), 'manca il dollaro: il redattore non combacia'
    assert _espressione('X').count('{') == 2 and _espressione('X').count('}') == 2

    # La chiave deve essere riconosciuta dalla tabella come sensibile, altrimenti la
    # regola contestuale non scatta e i casi non provano niente.
    assert _CHIAVE == 'API_KEY' and _CHIAVE_SECONDA == 'TOKEN'

    # E la riga composta deve essere IDENTICA alla forma letterale che sostituisce.
    atteso = 'API_KEY: ${' + '{ secrets.NOME }' + '}CODA\n'
    assert _assegnazione(_CHIAVE, _espressione() + 'CODA') == atteso

    # Ogni assegnazione composta deve portare un'espressione. Si itera la LISTA, non
    # si cercano le righe dentro il testo: cosi- non c'e- nessun elenco di chiavi
    # sensibili da tenere aggiornato, e una riga aggiunta al fixture e- coperta subito.
    assert ASSEGNAZIONI_CON_SEGRETO, 'il fixture non contiene nessuna assegnazione'
    senza_espressione = [r.split(':', 1)[0].strip() for r in ASSEGNAZIONI_CON_SEGRETO
                         if '${' + '{' not in r]
    # Nel messaggio finiscono solo i NOMI delle chiavi, non le righe: un dump del
    # fixture nel log CI e- un'abitudine sbagliata in un file che parla di non far
    # uscire i segreti, e domani il fixture potrebbe contenerne uno vero.
    assert not senza_espressione, (
        f'{len(senza_espressione)} assegnazioni del fixture senza espressione: '
        f'{senza_espressione}'
    )

    # E ogni riga della lista deve finire davvero nel testo del fixture, altrimenti
    # la lista e- una decorazione e il YAML mandato al redattore e- un altro.
    for riga in ASSEGNAZIONI_CON_SEGRETO:
        assert riga.strip() in YAML_CON_SEGRETO, f'riga composta assente dal fixture: {riga!r}'
