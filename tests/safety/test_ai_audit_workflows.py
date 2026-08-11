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
    'web/build_single_file.py',
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
