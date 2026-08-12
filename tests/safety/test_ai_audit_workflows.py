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

import os
import re
import textwrap
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / '.github' / 'workflows'

GPT = WORKFLOWS / 'pr-review-gpt55.yml'
FABLE = WORKFLOWS / 'pr-review-claude-fable5.yml'
SOL = WORKFLOWS / 'pr-review-gpt56-sol.yml'

# I due workflow che parlano con l'endpoint OpenAI `v1/responses`, e che quindi
# leggono la stessa forma di risposta: `status`/`incomplete_details`, non
# `stop_reason` come Anthropic. Ogni difetto nella lettura di quella forma vive
# per costruzione in DUE posti, e i test che la riguardano vanno su entrambi
# (regola 2: la classe, non il sito).
SU_V1_RESPONSES = (GPT, SOL)

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
    for path in (GPT, FABLE, SOL):
        assert path.is_file(), f'workflow mancante: {path.name}'


def test_yaml_valido_e_nome_atteso():
    assert _carica(GPT)['name'] == 'PR Review GPT-5.5'
    assert _carica(FABLE)['name'] == 'PR Review Claude Fable 5'
    assert _carica(SOL)['name'] == 'PR Review GPT-5.6 Sol'


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


@pytest.mark.parametrize('path', (GPT, FABLE, SOL), ids=lambda p: p.name)
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


@pytest.mark.parametrize('path', (GPT, FABLE, SOL), ids=lambda p: p.name)
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
    gate = _funzione_gate(SOL)
    assert gate('labeled', [], 'final-fugu-review', 'final-fugu-review') == 'revisiona'


def test_fugu_una_label_qualsiasi_non_arma_il_gate():
    """Aggiungere manual-review-required a una PR gia' etichettata non deve
    rieseguire la review su un head gia' revisionato."""
    gate = _funzione_gate(SOL)
    assert gate('labeled', ['final-fugu-review'], 'final-fugu-review',
                'manual-review-required') == 'salta'


def test_fugu_pr_aperta_con_label_gia_presente_revisiona():
    """GitHub non emette `labeled` per una PR aperta con la label applicata."""
    gate = _funzione_gate(SOL)
    assert gate('opened', ['final-fugu-review'], 'final-fugu-review') == 'revisiona'


def test_fugu_push_dopo_armamento_e_stantio():
    """Il caso della #274: un verde su un head che nessuno ha letto."""
    gate = _funzione_gate(SOL)
    assert gate('synchronize', ['final-fugu-review'], 'final-fugu-review') == 'stantio'


def test_fugu_push_senza_label_non_spende():
    gate = _funzione_gate(SOL)
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
    fugu = _funzione_gate(SOL)
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
    for path in (FABLE, SOL):
        tipi = _trigger(path)['pull_request']['types']
        assert 'labeled' in tipi, f'{path.name} non reagisce agli eventi labeled'


def test_gpt_non_reagisce_alla_label():
    """GPT-5.5 gira su ogni push e non ha bisogno del gate a label."""
    assert 'labeled' not in _trigger(GPT)['pull_request']['types']


def test_il_gate_si_arma_solo_con_la_propria_label():
    """La condizione del job filtra sulla label DELL'EVENTO, non sulla presenza
    della label nell'elenco: altrimenti aggiungere una label qualsiasi a una PR
    gia' etichettata rieseguirebbe la review su un head gia' revisionato."""
    attese = {FABLE: 'final-fable-review', SOL: 'final-fugu-review'}
    for path, label in attese.items():
        cond = _carica(path)['jobs']['review']['if']
        assert 'github.event.label.name' in cond, (
            f'{path.name}: la condizione non guarda la label dell\'evento'
        )
        assert label in cond, f'{path.name}: la condizione non cita {label}'


def test_le_due_label_finali_sono_distinte():
    assert 'final-fable-review' in _script(FABLE)
    assert 'final-fugu-review' in _script(SOL)
    assert 'final-fugu-review' not in _carica(FABLE)['jobs']['review']['if']
    assert 'final-fable-review' not in _carica(SOL)['jobs']['review']['if']


# --------------------------------------------------------------- segreti

def test_nessuna_api_key_in_chiaro():
    """Le chiavi arrivano dai Secret del repo: nel file non deve comparirne una."""
    perdite = [
        re.compile(r'sk-(?:proj-)?[A-Za-z0-9_\-]{20,}'),
        re.compile(r'sk-or-v1-[A-Za-z0-9_\-]{20,}'),
        re.compile(r'\b\d{8,12}:[A-Za-z0-9_\-]{30,}\b'),  # bot token Telegram
    ]
    for path in (GPT, FABLE, SOL):
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

    **DUE workflow leggono `BETRELAY_GPT`,** e non e' un errore: dal 12/08/2026
    `gpt-5.6-sol` ha sostituito `sakana/fugu-ultra` al gate finale, e sta sulla
    stessa API di GPT-5.5. Una chiave in meno da gestire. `BETRELAY_FUGU` non e'
    piu' letto da nessuno, e il test accanto lo vieta come residuo.
    """
    coppie = {
        GPT: 'secrets.BETRELAY_GPT',
        FABLE: 'secrets.BETRELAY_FABLE',
        SOL: 'secrets.BETRELAY_GPT',
    }
    for path, atteso in coppie.items():
        assert atteso in path.read_text(encoding='utf-8'), f'{path.name}: manca {atteso}'


def test_nessun_riferimento_ai_secret_del_bridge():
    """Nessun residuo dei nomi vecchi, in nessuno dei tre workflow.

    Serve perche' un riferimento dimenticato non fallisce: legge vuoto e salta
    la review in silenzio. E' la stessa classe di difetto del punto sopra, per
    questo va cercata su tutto il file e non solo sulla riga `env:`.
    """
    # `BETRELAY_FUGU` e' nella lista dal 12/08/2026: dopo la sostituzione di Fugu con
    # `gpt-5.6-sol` non lo legge piu' nessun workflow, quindi un riferimento
    # dimenticato leggerebbe vuoto e salterebbe la review in silenzio — la stessa
    # classe di difetto dei nomi del Bridge. Un ritorno deliberato a Fugu dovrebbe
    # togliere questa voce, ed e' giusto che sia una scelta esplicita.
    vecchi = ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'OPENROUTER_API_KEY',
              'BETRELAY_FUGU')
    for path in (GPT, FABLE, SOL):
        testo = path.read_text(encoding='utf-8')
        for nome in vecchi:
            assert nome not in testo, (
                f'{path.name}: contiene ancora {nome}, nome di Secret del Bridge '
                f'non configurato in questo repository'
            )


# ------------------------------------------------------------- permessi

def test_permessi_minimi_sul_codice():
    """I workflow commentano le PR ma non devono poter scrivere il codice."""
    for path in (GPT, FABLE, SOL):
        perm = _carica(path)['permissions']
        assert perm['contents'] == 'read', f'{path.name}: contents non e\' read'
        assert perm['pull-requests'] == 'write'


def test_nessun_auto_merge():
    """Il merge resta manuale del proprietario: nessun workflow lo automatizza."""
    for path in (GPT, FABLE, SOL):
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


@pytest.mark.parametrize('path', (GPT, FABLE, SOL), ids=lambda p: p.name)
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
    testo, saltati, critici, troncato, tagliati = build(files, 2000, 9000)

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


@pytest.mark.parametrize('path', (GPT, FABLE, SOL), ids=lambda p: p.name)
def test_i_file_core_precedono_i_documenti_nel_payload(path):
    """L'ordine, non solo la presenza: il core va letto prima.

    Due budget, perche- le due proprieta- non sono verificabili con lo stesso.

    Col budget STRETTO si verifica CHI viene scartato: il codice entra, i documenti no.
    Col budget LARGO, dove non si scarta niente, si verifica l'ORDINE — ed e- l-unico
    modo di verificarlo davvero, perche- col budget stretto i documenti non arrivano
    mai nel payload e il confronto non ha operandi.

    Questa e- la correzione di un difetto vero, trovato cercando la CLASSE del rilievo
    di CodeRabbit sul test della PR mista invece del solo sito: qui l'ordine era dietro
    un `if docs:` e MISURATO come ramo morto — `docs` risultava vuoto su tutti e tre i
    workflow, quindi il test dichiarava nel docstring di verificare l'ordine e in
    realta- verificava solo la presenza del core. Un `if` che protegge un-asserzione
    dalla mancanza dei suoi operandi la spegne in silenzio proprio quando servirebbe.
    """
    build = _funzione_payload(path)
    files = [_file_finto(n) for n in FILE_COME_LA_PR_8]

    # 1. Budget stretto: si scarta, e si scartano i documenti.
    testo, saltati, _, _, _ = build(files, 2000, 5000)
    posizione = {n: testo.find(f'FILE: {n}') for n in FILE_COME_LA_PR_8}
    core = {n: p for n, p in posizione.items() if p >= 0 and (n == 'main.py' or n.startswith('web/'))}
    docs = {n: p for n, p in posizione.items() if p >= 0 and n.endswith(('.md', '.txt'))}

    assert core, f'nessun file core nel payload col budget stretto: saltati={saltati}'
    assert not docs, (
        f'col budget stretto un documento e- entrato mentre si scartava: docs={docs}, '
        f'saltati={saltati}'
    )

    # 2. Budget largo: entra tutto, e l'ordine si verifica senza condizioni.
    testo, saltati, _, _, _ = build(files, 2000, 40000)
    posizione = {n: testo.find(f'FILE: {n}') for n in FILE_COME_LA_PR_8}
    core = {n: p for n, p in posizione.items() if p >= 0 and (n == 'main.py' or n.startswith('web/'))}
    docs = {n: p for n, p in posizione.items() if p >= 0 and n.endswith(('.md', '.txt'))}

    assert not saltati, f'col budget largo non si deve scartare niente: saltati={saltati}'
    assert core and docs, (
        f'operandi mancanti per il confronto d-ordine: core={core}, docs={docs}'
    )
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


@pytest.mark.parametrize('path', (GPT, FABLE, SOL), ids=lambda p: p.name)
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


@pytest.mark.parametrize('path', (GPT, FABLE, SOL), ids=lambda p: p.name)
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
    #
    # L'asserzione e- l'UGUAGLIANZA con l'ingresso, non la presenza del nome e di
    # `${{`. Segnalato da CodeRabbit su questa PR, e misurato con una regola di
    # sabotaggio che normalizza `<chiave>: ` in `<chiave>=` — un carattere di distanza
    # dalla regola vera, che sostituisce proprio con `\1=[REDACTED]`:
    #   in  `API_KEY: <espressione>`      out `API_KEY=<espressione>`
    # Il nome del Secret c'e-, `${{` c'e-, e lo YAML e- rotto: il reviewer vede un
    # workflow mal configurato ed e- ESATTAMENTE il meccanismo dei bloccanti fantasma
    # che l'esenzione esiste per chiudere. Col vecchio assert: 1 passed.
    for riga in (
        _assegnazione(_CHIAVE, _espressione('BETRELAY_FABLE')),
        _assegnazione(_CHIAVE, '"' + _espressione('BETRELAY_FABLE') + '"'),
        _assegnazione(_CHIAVE, "'" + _espressione('BETRELAY_FABLE') + "'"),
    ):
        ripulito = redact(riga)
        assert ripulito == riga, (
            f'{path.name}: espressione completa alterata dal redattore.\n'
            f'  in : {riga!r}\n  out: {ripulito!r}'
        )


@pytest.mark.parametrize('path', (GPT, FABLE, SOL), ids=lambda p: p.name)
def test_un_valore_di_segreto_VERO_resta_redatto(path):
    """L'altra faccia: esentare `${{ … }}` non deve aprire un varco.

    Senza questo, la correzione del test sopra potrebbe essere fatta allentando
    la regola contestuale, e un segreto scritto a mano in un file uscirebbe verso
    tre modelli esterni.

    Ogni riga e- accoppiata al FRAMMENTO sensibile che non deve uscire, e si verificano
    entrambe le cose: il marcatore c'e- **e** il frammento no. Segnalato da CodeRabbit
    su questa PR: cercare solo `[REDACTED]` passa anche quando il marcatore compare
    ACCANTO al valore invece che al suo posto. Misurato togliendo il `+` dalla coda
    della regola contestuale — un typo di quantificatore:
      out `api_key=[REDACTED]hiave-scritta-a-mano-per-errore`
    Col vecchio assert: 1 passed.

    Il frammento non comprende il primo carattere del valore, perche- quel sabotaggio
    lo mangia: vietare la stringa intera lo lascerebbe passare di nuovo. Una perdita
    parziale resta una perdita.
    """
    redact = _funzione_redact(path)
    for riga, frammento in (
        (_assegnazione('PROVIDER_' + _CHIAVE, 'sk-ant-api03-VALOREFINTOCHENONDEVEUSCIRE00'),
         'ALOREFINTOCHENONDEVEUSCIRE00'),
        (_assegnazione('api' '_key', '"chiave-scritta-a-mano-per-errore"', ' = '),
         'hiave-scritta-a-mano-per-errore'),
        (_assegnazione('CSV_ACCESS_' + _CHIAVE_SECONDA, 'FINTO-non-un-token-0000000000'),
         'INTO-non-un-token-0000000000'),
    ):
        ripulito = redact(riga)
        assert '[REDACTED' in ripulito, f'{path.name}: valore NON redatto: {riga} -> {ripulito}'
        assert frammento not in ripulito, (
            f'{path.name}: il marcatore c-e- ma il valore e- USCITO accanto: '
            f'{frammento!r} sopravvive.\n  in : {riga!r}\n  out: {ripulito!r}'
        )


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


def test_i_token_dalla_cache_non_si_pagano_a_tariffa_piena():
    """Il rendiconto non deve dichiarare piu- del vero.

    Sull'API OpenAI il prompt caching e- AUTOMATICO sopra i ~1024 token e
    `input_tokens` LI INCLUDE. Il workflow li prezzava tutti a tariffa piena,
    quindi il costo stampato nel commento era piu- alto di quello addebitato — e
    su quei numeri il proprietario ha deciso di riordinare la roadmap e di
    smettere di armare il gate. Un rendiconto che sbaglia in eccesso e- meno
    pericoloso di uno che sbaglia in difetto, ma resta sbagliato.

    Il test ESEGUE `usage_note`: verificare che nel sorgente compaia
    `cached_tokens` passerebbe anche con la formula vecchia due righe sotto.
    """
    sorgente = _script(GPT)
    blocco = re.search(r'^(\s*)def usage_note\(.*?(?=\n\1[A-Za-z_@])', sorgente, re.S | re.M)
    assert blocco, 'usage_note non trovata'
    spazio: dict = {'PRICE_INPUT_PER_MILLION': 5.0,
                    'PRICE_OUTPUT_PER_MILLION': 30.0,
                    'PRICE_CACHE_READ_PER_MILLION': 0.5}
    exec(textwrap.dedent(blocco.group(0)), spazio)  # noqa: S102 - sorgente del repo
    usage_note = spazio['usage_note']

    # 100.000 input di cui 80.000 dalla cache, 1.000 output.
    #   giusto : 20.000*5 + 80.000*0.5 + 1.000*30 = 100 + 40 + 30 = $0.170 /M
    #   vecchio: 100.000*5           + 1.000*30 = 500 + 30       = $0.530 /M
    con_cache = usage_note(
        {'input_tokens': 100_000, 'output_tokens': 1_000,
         'input_tokens_details': {'cached_tokens': 80_000}},
        'sys', 'usr', 'review')
    assert '~$0.1700' in con_cache, f'aritmetica della cache sbagliata:\n{con_cache}'
    assert '~$0.5300' not in con_cache, 'sta ancora prezzando tutto a tariffa piena'
    # E i token cachati devono essere VISIBILI, non solo scontati.
    assert '80000' in con_cache, f'i token dalla cache non sono dichiarati:\n{con_cache}'

    # Senza cache il conto non cambia rispetto a prima: nessuna regressione.
    senza = usage_note({'input_tokens': 100_000, 'output_tokens': 1_000}, 'sys', 'usr', 'review')
    assert '~$0.5300' in senza, f'senza cache il costo e- cambiato:\n{senza}'
    assert 'dalla cache' not in senza, 'aggiunge la riga della cache anche quando e- zero'

    # Difesa contro un usage incoerente: cached > input non deve far scendere il
    # costo sotto il vero, che e- l'errore nella direzione che NASCONDE la spesa.
    assurdo = usage_note(
        {'input_tokens': 1_000, 'output_tokens': 0,
         'input_tokens_details': {'cached_tokens': 999_999}},
        'sys', 'usr', 'review')
    assert '-' not in assurdo.split('Costo stimato base')[1], (
        f'un cached_tokens assurdo ha prodotto un costo negativo:\n{assurdo}'
    )


def _funzione_contesto(path: Path):
    """Estrae ed ESEGUE `blocco_contesto_mancante` dal workflow reale."""
    sorgente = _script(path)
    blocco = re.search(r'^(\s*)def blocco_contesto_mancante\(.*?(?=\n\1[A-Za-z_@])',
                       sorgente, re.S | re.M)
    assert blocco, f'{path.name}: blocco_contesto_mancante non trovata'
    spazio: dict = {}
    exec(textwrap.dedent(blocco.group(0)), spazio)  # noqa: S102 - sorgente del repo
    return spazio['blocco_contesto_mancante']


@pytest.mark.parametrize('path', (GPT, FABLE, SOL), ids=lambda p: p.name)
def test_il_prompt_DICE_al_modello_cosa_non_ha_visto(path):
    """Il modello deve sapere di avere un contesto incompleto, non dedurlo.

    Il payload portava gia- il marcatore inline sui file tagliati e il commento
    elencava i non inviati — ma il PROMPT non diceva niente, quindi il modello
    riceveva codice incompleto senza saperlo. Misurato sulla PR #14: nove
    bloccanti dai gate finali, QUATTRO falsi, tutti nella forma «non verificabile
    dal diff troncato». Il modello dichiarava il proprio limite come se fosse un
    difetto del codice, e ogni giro cosi- costa ~$1 piu- una correzione inutile.

    Questo test ESEGUE la funzione invece di cercare una stringa: un blocco
    presente ma mai interpolato passerebbe un controllo strutturale.
    """
    blocco = _funzione_contesto(path)

    # Niente troncamento → nessun rumore nel prompt.
    assert blocco([], []) == '', 'aggiunge il preambolo anche quando non serve'

    testo = blocco(['main.py'], ['web/engine.js'])
    assert 'main.py' in testo and 'web/engine.js' in testo
    # I due stati vanno DISTINTI: «incompleto» e «non inviato» richiedono azioni
    # diverse a chi legge la review.
    assert 'INCOMPLET' in testo.upper(), testo
    assert 'NON inviati' in testo, testo
    # Il vocabolario che rende triabile un bloccante.
    for etichetta in ('[REAL_FINDING]', '[INSUFFICIENT_CONTEXT]'):
        assert etichetta in testo, f'{path.name}: manca {etichetta}'
    # E l'istruzione che impedisce di trasformare un'assenza in un difetto.
    assert 'NON autorizza' in testo, testo

    # La RESA, non solo il contenuto: GPT-5.5 ha sospettato che le etichette
    # finissero sulla stessa riga dell'istruzione, rendendo l'output non triabile.
    # Non era vero, ma un sospetto verificabile va verificato, non discusso.
    righe = [r for r in testo.split('\n') if r.strip()]
    etichette = [r for r in righe if r.startswith('- [')]
    assert len(etichette) == 2, f'le etichette non sono due righe proprie: {righe}'
    assert etichette[0].startswith('- [REAL_FINDING]'), etichette
    assert etichette[1].startswith('- [INSUFFICIENT_CONTEXT]'), etichette
    # E ogni file elencato sta su una riga sua, o il modello non li distingue.
    assert '- main.py' in righe, righe
    assert '- web/engine.js' in righe, righe


def test_le_tre_copie_del_preambolo_dicono_LA_STESSA_COSA():
    """Tre copie identiche per necessita-, quindi la parita- va vincolata.

    I workflow non fanno checkout e non possono importare un modulo comune: la
    regola 3 di `CLAUDE.md` lo riconosce e chiede in cambio che i test verifichino
    la parita-. Segnalato da Sourcery su questa PR come duplicazione — e- vero, ed
    e- deliberata: quello che non deve accadere e- che DIVERGANO.

    Confronto sull'OUTPUT e non sul sorgente: due implementazioni scritte diverse
    ma equivalenti vanno bene, due scritte uguali che si comportano diverso no.
    """
    casi = ([], ['solo/saltato.py']), (['solo/tagliato.py'], []), (['a.py'], ['b.py'])
    for tagliati, saltati in casi:
        uscite = {p.name: _funzione_contesto(p)(tagliati, saltati) for p in (GPT, FABLE, SOL)}
        distinte = set(uscite.values())
        assert len(distinte) == 1, (
            f'le tre copie divergono su tagliati={tagliati} saltati={saltati}:\n'
            + '\n'.join(f'--- {n} ---\n{t}' for n, t in uscite.items())
        )


@pytest.mark.parametrize('path', (GPT, FABLE, SOL), ids=lambda p: p.name)
def test_il_preambolo_del_contesto_e_davvero_nel_prompt(path):
    """Una funzione corretta e mai chiamata non serve a niente.

    E- la meta- che l'esecuzione della funzione non copre: va verificato che il
    valore finisca DENTRO l'f-string mandata al modello, prima del diff.
    """
    sorgente = _script(path)
    assert 'contesto_mancante = blocco_contesto_mancante(' in sorgente, (
        f'{path.name}: la funzione non viene chiamata'
    )
    prompt = sorgente[sorgente.index('user_prompt = f"""'):]
    assert '{contesto_mancante}' in prompt, (
        f'{path.name}: il preambolo non e- interpolato nel prompt utente'
    )
    assert prompt.index('{contesto_mancante}') < prompt.index('{diff_text}'), (
        f'{path.name}: il preambolo arriva DOPO il diff: va letto prima'
    )


@pytest.mark.parametrize('path', (GPT, FABLE, SOL), ids=lambda p: p.name)
def test_un_file_TAGLIATO_a_meta_non_si_confonde_con_uno_non_inviato(path):
    """Due stati diversi, e il peggiore dei due era invisibile.

    `saltati` elenca i file che il modello NON HA RICEVUTO. Un file tagliato a
    metà dal tetto per-file non stava da nessuna parte: il modello riceveva
    codice vero, incompleto, e concludeva su cio- che non poteva vedere senza
    accorgersene. E- il caso peggiore dei due, perche- l'assenza totale almeno si
    nota.

    Misurato sulla PR #14: la patch di `main.py` era 25.037 caratteri contro un
    tetto di 15.000 — il 60% inviato — e su NOVE bloccanti dei gate finali
    QUATTRO erano falsi, tutti su quel file, tutti dichiarati dai reviewer come
    «non verificabile dal diff troncato». Costo dei giri buttati: ~$1 l'uno.

    Da qui il quinto valore di ritorno: chi e- stato inviato INCOMPLETO. Serve al
    prompt, che lo dice al modello, e al commento, che lo dice a chi legge.
    """
    build = _funzione_payload(path)
    # Un file enorme e uno piccolo: il primo verra- tagliato, il secondo entra intero.
    files = [_file_finto('main.py', righe=4000), _file_finto('README.txt', righe=5)]

    testo, saltati, _, troncato, tagliati = build(files, 2000, 400000)

    assert troncato is True, 'il taglio per-file non ha marcato il troncamento'
    assert 'main.py' in tagliati, (
        f'il file tagliato a meta- non e- stato dichiarato: tagliati={tagliati}'
    )
    assert 'main.py' not in saltati, (
        'un file tagliato e- stato messo fra i NON INVIATI: sono stati diversi, e '
        'confonderli fa perdere il caso piu- pericoloso'
    )
    assert 'FILE: main.py' in testo, 'il file tagliato deve comunque essere nel payload'
    assert 'README.txt' not in tagliati, (
        f'un file che entra intero non e- tagliato: tagliati={tagliati}'
    )


@pytest.mark.parametrize('path', (GPT, FABLE, SOL), ids=lambda p: p.name)
def test_un_file_tagliato_e_POI_scartato_e_solo_NON_INVIATO(path):
    """I due stati sono esclusivi: «non inviato» vince su «inviato incompleto».

    Un file puo- essere tagliato dal tetto per-file e **poi** scartato dal tetto
    totale. Dichiararlo in entrambe le liste direbbe al modello «l'hai ricevuto
    incompleto» quando non l'ha ricevuto affatto — cioe- esattamente la confusione
    che il preambolo esiste per togliere, reintrodotta un livello piu- sotto.

    Segnalato da GPT-5.5 sulla review di questa PR, e misurato: con un totale da
    500 caratteri `main.py` risultava sia in `saltati` sia in `tagliati`.
    """
    build = _funzione_payload(path)
    # Tetto per-file piccolo (taglia) e totale ancora piu- piccolo (scarta).
    testo, saltati, _, _, tagliati = build([_file_finto('main.py', righe=4000)], 2000, 500)

    assert 'main.py' in saltati, f'il file non e- stato scartato: saltati={saltati}'
    assert 'FILE: main.py' not in testo, 'un file scartato non deve essere nel payload'
    assert 'main.py' not in tagliati, (
        'un file NON INVIATO e- dichiarato anche come «inviato incompleto»: il '
        'modello leggerebbe che ha ricevuto codice che non ha ricevuto'
    )


@pytest.mark.parametrize('path', (GPT, FABLE, SOL), ids=lambda p: p.name)
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


@pytest.mark.parametrize('path', (GPT, FABLE, SOL), ids=lambda p: p.name)
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


@pytest.mark.parametrize('path', (GPT, FABLE, SOL), ids=lambda p: p.name)
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


@pytest.mark.parametrize('path', (GPT, FABLE, SOL), ids=lambda p: p.name)
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


# I caratteri che CHIUDONO qualcosa: dopo un'espressione sono markup, non segreto.
# Un segreto non comincia con una parentesi chiusa.
CHIUSURE = (')', ']', '}', '>', ',', ';')

# I caratteri che POTREBBERO cominciare del materiale segreto incollato. `.` e `/`
# stanno qui e non fra le chiusure: un JWT contiene punti, il base64 contiene barre.
INIZI_PLAUSIBILI = ('CODA-INCOLLATA', '.coda-segreta', '/coda', '+coda', '=coda',
                    ':coda', '_coda', '9coda')


@pytest.mark.parametrize('path', (GPT, FABLE, SOL), ids=lambda p: p.name)
@pytest.mark.parametrize('chiusura', CHIUSURE)
def test_una_chiusura_dopo_un_espressione_NON_viene_mangiata(path, chiusura):
    """Il difetto che ha prodotto tre bloccanti falsi su una PR sola.

    `name: pytest (<espressione>)` diventava
    `name: pytest ([REDACTED_VALORE_INCOLLATO]` — con la parentesi di **apertura
    spaiata**. Fugu Ultra lo ha letto come «valore incollato redatto con parentesi
    non chiusa», ha chiesto di verificare che non fosse un token e ha bloccato il
    merge; poi ha bloccato altre due volte sulla stessa causa. Misurato sulla
    PR #18: tre bloccanti su tre, tutti generati dalla redazione del nostro stesso
    input, e ~$0.63 di Fugu spesi per produrli.

    **Perche' adesso si puo' restringere, e prima sembrava di no.** Il test che
    stava qui documentava il baratto come binario: «escludere `)`, `,`, `.`
    riaprirebbe la falla di `API_KEY: <espressione>.coda-segreta`». Misurato regola
    per regola, quel baratto non esiste come descritto:

    | caso | chi lo protegge |
    |---|---|
    | `API_KEY: <esp>.coda-segreta` | la regola dell'ASSEGNAZIONE, da sola |
    | `foo: <esp>.coda-segreta` | solo il letterale incollato → `.` resta trigger |
    | `foo: <esp>,abc` | **nessuno**: `,` fa scattare la regola ma la consumazione
      si ferma subito, e `,abc` esce in chiaro **gia' oggi** |
    | `name: pytest (<esp>)` | nessuno, e la regola lo rompe |

    Quindi `)` e `.` non hanno lo stesso costo, ed erano trattati come se lo
    avessero. Si escludono solo le **chiusure**: un segreto non comincia con una
    parentesi chiusa, e per la virgola l'esclusione non perde nemmeno protezione
    teorica, perche' non ce n'era.
    """
    redact = _funzione_redact(path)
    riga = 'name: pytest (' + _espressione() + chiusura
    fuori = redact(riga)
    assert '[REDACTED_VALORE_INCOLLATO]' not in fuori, (
        f'{path.name}: una {chiusura!r} dopo un-espressione viene inghiottita: il '
        f'reviewer riceve markup rotto e conclude che il file e- corrotto.\n'
        f'  in : {riga!r}\n  out: {fuori!r}'
    )
    assert fuori.endswith(chiusura), \
        f'{path.name}: la {chiusura!r} finale e- sparita: {fuori!r}'


@pytest.mark.parametrize('path', (GPT, FABLE, SOL), ids=lambda p: p.name)
@pytest.mark.parametrize('coda', INIZI_PLAUSIBILI)
def test_una_coda_che_POTREBBE_essere_un_segreto_resta_redatta(path, coda):
    """Il rovescio del test sopra, e senza di lui quello sarebbe un indebolimento.

    La coppia e' il punto: uno pretende che le chiusure sopravvivano, l'altro che
    tutto cio' che potrebbe essere materiale segreto incollato continui a sparire.
    Se un domani qualcuno allargasse le esclusioni a `.` o `/`, questo diventa
    rosso — che e' il solo modo in cui «abbiamo ristretto con giudizio» significa
    qualcosa.

    Nessuna chiave sensibile nella riga, di proposito: con `API_KEY:` scatterebbe
    la regola dell'assegnazione e questo test non misurerebbe piu' il letterale
    incollato ma un'altra regola.
    """
    redact = _funzione_redact(path)
    riga = 'foo: ' + _espressione() + coda
    fuori = redact(riga)
    sensibile = coda.lstrip('.:/+=_9')
    assert sensibile not in fuori, (
        f'{path.name}: la coda {coda!r} esce in chiaro dopo un-espressione: se e- '
        f'un segreto incollato, e- pubblicato.\n  in : {riga!r}\n  out: {fuori!r}'
    )


@pytest.mark.parametrize('path', (GPT, FABLE, SOL), ids=lambda p: p.name)
def test_la_prosa_attorno_a_un_espressione_resta_leggibile(path):
    """Quello che il reviewer deve poter leggere: la frase, non un buco.

    Segnalato in origine da GPT-5.5 come sovra-redazione della punteggiatura in
    prosa. Adesso e' corretto e non piu' soltanto documentato.
    """
    redact = _funzione_redact(path)
    prosa = 'vedi ' + _espressione() + ') nel testo'
    ripulito = redact(prosa)
    assert ripulito.startswith('vedi ') and 'nel testo' in ripulito
    assert '[REDACTED_VALORE_INCOLLATO]' not in ripulito, \
        f'{path.name}: la prosa e- ancora alterata: {ripulito!r}'


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
    for p in (GPT, SOL):
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
    # `gpt-5.6-sol`, dal 12/08/2026 al posto di Fugu. La soglia resta 10000 e va detto
    # cosa la sostiene e cosa no: la MISURA sotto e' di Fugu, non di Sol, perche' su
    # Sol non ho ancora un giro del gate. Su Sol l'effort va da `none` a `max` e il
    # workflow chiede `high`, quindi la stessa dinamica — reasoning che mangia il
    # budget prima del testo — e' possibile e non dimostrata. Tenere il tetto alto e'
    # la scelta prudente: il tetto non e' il costo, si pagano i token generati, quindi
    # abbassarlo non risparmia — fa pagare review incomplete. Le prime review del gate
    # stampano i token di reasoning a parte: da quelle si decide, non a occhio.
    'pr-review-gpt56-sol.yml': 10000,
    # Storia, dal reviewer precedente: Fugu ragionava molto e non si poteva abbassare,
    # perche' `low` non era fra i suoi supported_efforts (`max`/`xhigh`/`high`) e
    # `high` era il pavimento imposto dal modello. Misurato
    # sulla PR #9: con il tetto a 3000 ha speso 3000 token di completion di cui
    # **3000 di reasoning (100%)** e ha prodotto ZERO righe di review, a $0.168.
    # Un tetto che non lascia spazio al testo dopo il ragionamento fa pagare una
    # review che non esiste — che e' peggio di una review piu' cara.
    #
    # La soglia e- 10000, cioe- ESATTAMENTE il valore spedito, non un numero piu-
    # basso «con margine». Segnalato da CodeRabbit su questa PR: con la soglia a 8000
    # un ritorno del workflow a 8000 passava verde e rimetteva in piedi il guasto che
    # questo test esiste per impedire — misurato, 3 passed. Una soglia sotto il valore
    # spedito e- spazio per la regressione, non tolleranza.
    # Il 10000 e- confermato dalla misura successiva sullo stesso head: 7308 token di
    # completion di cui 2588 di reasoning (35%) e una review vera, a $0.5065. Con 8000
    # ci sarebbe stato ancora spazio, con 3000 no: il margine utile e- sopra 8000, e
    # abbassare la soglia sotto il valore misurato non compra niente.
}


@pytest.mark.parametrize('path', (GPT, FABLE, SOL), ids=lambda p: p.name)
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
    for indice, riga in enumerate(ASSEGNAZIONI_CON_SEGRETO):
        # Nel messaggio l'INDICE e la chiave, non la riga: e- la stessa ragione per cui
        # l'assert sopra non stampa il fixture, e questa riga l'avevo scritta nello
        # stesso commit dimenticandola. Segnalato da GPT-5.5.
        assert riga.strip() in YAML_CON_SEGRETO, (
            f'assegnazione {indice} ({riga.split(":", 1)[0].strip()}) assente dal fixture: '
            f'la lista e- scollegata dal testo mandato al redattore'
        )


# PR MISTA: tocca insieme il codice che gira in produzione e il proprio impianto di
# review. E- il caso che Fugu Ultra ha isolato, e non era esercitato da nessun test
# perche- questa PR tocca solo workflow.
FILE_PR_MISTA = [
    '.github/workflows/pr-review-claude-fable5.yml',
    '.github/workflows/pr-review-gpt55.yml',
    'main.py',
    'web/engine.js',
    'tests/relay/test_csv_contract.py',
    'CLAUDE.md',
]


@pytest.mark.parametrize('path', (GPT, FABLE, SOL), ids=lambda p: p.name)
def test_su_una_PR_mista_il_relay_precede_i_workflow(path):
    """Il codice che serve i clienti prima dell'impianto che lo revisiona.

    Segnalato da Fugu Ultra sul gate finale, ed e- la stessa classe del difetto che
    questa PR chiude, un livello dentro: dentro il tier-0 l'ordine resta quello
    dell-API, cioe- alfabetico, e `.github/workflows/` viene prima di `main.py` e di
    `web/`. I tre workflow sono file da ~48.000 caratteri l-uno: su una PR che tocca
    insieme un workflow e il relay si mangerebbero il budget del core prima che il
    motore arrivi al modello.

    Non era esercitato da nessun test perche- questa PR tocca solo workflow — cioe-
    esattamente il caso in cui il difetto non si vede.
    """
    build = _funzione_payload(path)
    files = [_file_finto(n) for n in FILE_PR_MISTA]
    testo, saltati, _, _, _ = build(files, 2000, 6000)

    posizione = {n: testo.find(f'FILE: {n}') for n in FILE_PR_MISTA}
    relay = {n: p for n, p in posizione.items() if p >= 0 and (n == 'main.py' or n.startswith('web/'))}
    workflow = {n: p for n, p in posizione.items() if p >= 0 and n.startswith('.github/')}

    assert 'main.py' not in saltati and 'web/engine.js' not in saltati, (
        f'il relay o il motore sono stati scartati per far posto ai workflow: '
        f'saltati={saltati}'
    )
    # L'ordinamento NON e- condizionato alla presenza dei workflow nel payload.
    # Segnalato da CodeRabbit su questa PR: con `if workflow:` una regressione di
    # priorita- che li scarta TUTTI rendeva il dizionario vuoto e il confronto veniva
    # saltato, cioe- il test passava proprio nel caso peggiore — il reviewer non vede
    # l'impianto che revisiona il codice. Misurato togliendo il tier `^\.github/`:
    # saltati=[fable5, gpt55, CLAUDE.md], 1 passed.
    #
    # Un workflow in coda al payload e- una scelta di priorita- legittima; sparire dal
    # payload no: sono file safety-critical, e restano dentro perche- i due tier di
    # budget bastano per tutti e sei i file di questa PR finta.
    assert workflow, (
        f'nessun workflow e- arrivato nel payload: l-ordine non e- verificabile e i file '
        f'safety-critical dell-impianto di review non sono stati inviati.\n'
        f'  saltati = {saltati}'
    )
    assert max(relay.values()) < min(workflow.values()), (
        f'un workflow precede il codice di produzione nel payload:\n'
        f'  relay    = {relay}\n  workflow = {workflow}'
    )


# ---------------------------------------------------- il gate finale su `v1/responses`
#
# Dal 12/08/2026 il gate finale e' `gpt-5.6-sol` sull'endpoint OpenAI `v1/responses`,
# al posto di `sakana/fugu-ultra` via OpenRouter. Cambia la FORMA di richiesta e
# risposta, e ogni pezzo lasciato indietro fallisce in un modo diverso e silenzioso.
# Questi test coprono i tre modi.

def _blocco(path: Path, nome: str) -> str:
    """Il sorgente di una funzione dello script incorporato, dedentato.

    `nome` passa da `re.escape` (chiesto da CodeRabbit sulla PR #20): oggi i nomi
    passati sono identificatori Python, quindi innocui, ma un nome con un carattere
    speciale non darebbe un errore — cambierebbe in silenzio cosa cerca la regex, e
    il modo in cui un test statico fallisce male e- sempre trovare il blocco sbagliato
    e asserire su quello.
    """
    sorgente = _script(path)
    trovato = re.search(rf'^(\s*)def {re.escape(nome)}\(.*?(?=\n\1[A-Za-z_@])',
                        sorgente, re.S | re.M)
    assert trovato, f'{path.name}: {nome} non trovata'
    return textwrap.dedent(trovato.group(0))


def _solo_codice(corpo: str) -> str:
    """Il blocco senza le righe di commento.

    Serve per asserire l'ASSENZA di un nome: un commento che spiega di non usare
    `finish_reason` contiene `finish_reason`, e cercarlo nel testo grezzo rende il
    test rosso proprio grazie alla documentazione che lo giustifica. Ci sono cascato
    scrivendolo — la prima versione faceva `corpo.replace('# ', '')`, che toglie il
    prefisso e lascia il testo.
    """
    return '\n'.join(r for r in corpo.splitlines() if not r.strip().startswith('#'))


def test_la_richiesta_del_gate_usa_la_forma_di_v1_responses():
    """`max_output_tokens`, e NIENTE `temperature`.

    I due errori possibili qui non si somigliano:

    - `max_tokens` invece di `max_output_tokens`: l'endpoint lo ignora, il tetto non
      viene applicato, e il primo diff grosso paga un output senza freno;
    - `temperature`: i modelli reasoning su `v1/responses` la RIFIUTANO. Sarebbe un
      400 al primo tentativo, cioe' un gate rosso per un parametro e non per un
      difetto del codice — e su un gate a label quel rosso costa un riarmo.

    Il secondo e' un residuo plausibile: il workflow di Fugu la mandava (`0.05`),
    perche' su chat/completions era legittima.
    """
    corpo = _blocco(SOL, 'call_model')
    codice = _solo_codice(corpo)
    # IL CAMPO CHE PORTA IL PROMPT, e sta per primo perche' e- quello che mi era
    # sfuggito. La prima versione di questo test asseriva `max_output_tokens`,
    # l'assenza di `temperature`, l'endpoint e `store` — cioe' i campi che AVEVO
    # cambiato — e non quello che doveva cambiare e non era cambiato: il payload
    # spediva ancora `messages`, il nome di chat/completions, e ogni armamento del
    # gate avrebbe dato 400. Trovato da GPT-5.5, non da qui. Un test che verifica le
    # proprie modifiche invece del contratto passa e non protegge.
    assert '"input"' in corpo, (
        'il prompt non e- nel campo `input`: su v1/responses e- quello il nome, e '
        '`messages` da- 400 sistematico'
    )
    assert '"messages"' not in codice, (
        'residuo di chat/completions: `messages` invece di `input` fa fallire OGNI '
        'chiamata con 400, cioe- il gate finale rosso a ogni armamento'
    )
    assert '"max_output_tokens"' in corpo, 'il tetto di output non e- nella forma di v1/responses'
    # Ogni assenza si cerca in `codice`, non in `corpo`: un commento che spiega di NON
    # usare un nome contiene quel nome, e cercarlo nel testo grezzo rende il test rosso
    # grazie alla documentazione che lo giustifica. Mi e- successo tre volte in un
    # giorno con questa classe di test, ed e- l'unica ragione per cui `_solo_codice`
    # esiste. Chiesto da CodeRabbit sulla PR #20 di applicarlo anche a queste tre.
    assert '"max_tokens"' not in codice, (
        'residuo di chat/completions: `max_tokens` su v1/responses viene ignorato e '
        'il tetto non viene applicato'
    )
    assert '"temperature"' not in codice, (
        '`temperature` su un modello reasoning di v1/responses da- 400: gate rosso '
        'per un parametro, non per un difetto'
    )
    assert 'api.openai.com/v1/responses' in corpo, 'endpoint non aggiornato'
    assert 'openrouter.ai' not in codice, 'chiama ancora OpenRouter'
    assert '"store": False' in corpo, (
        'la richiesta contiene il diff della PR: senza `store: False` resta '
        'memorizzato lato fornitore'
    )


@pytest.mark.parametrize('path', SU_V1_RESPONSES, ids=lambda p: p.name)
def test_il_gate_riconosce_il_troncamento_nella_forma_GIUSTA(path):
    """`status=incomplete` + `incomplete_details`, non `finish_reason`.

    E' il difetto piu' insidioso della sostituzione: `finish_reason` e' un campo di
    chat/completions e su `v1/responses` non esiste. Leggerlo ancora non solleva
    niente — restituisce `None`, `truncated` resta falso, e una review TRONCATA
    verrebbe pubblicata come completa, col `done_marker` che impedisce di rifarla.
    Cioe' il gate finale direbbe «nessun bloccante» su una review interrotta a meta'.

    Su entrambi i workflow di questo endpoint, non solo sul gate: la stessa lettura
    vive in due posti per costruzione.

    *Cosa e' cambiato qui, e perche' non e' un indebolimento.* Questo test pretendeva
    la stringa `"status") == "incomplete"`, cioe- vincolava la condizione ESATTA che
    `gpt-5.6-sol` ha poi mostrato essere sbagliata: una blacklist di un solo valore.
    Un test che pinna la forma difettosa la protegge, e infatti e- diventato rosso
    quando l'ho corretta — nel verso opposto a quello utile. Ora asserisce che i due
    campi giusti vengano LETTI, e la semantica — quali stati contano come completo —
    la vincola il test parametrizzato che esegue `call_model` su sei stati diversi.
    Il resto (nessun `finish_reason`) resta identico, perche- quello era e rimane il
    difetto silenzioso della sostituzione.
    """
    corpo = _blocco(path, 'call_model')
    assert 'incomplete_details' in corpo, (
        'il motivo del troncamento non viene piu- letto: il banner non puo- dire se '
        'alzare il tetto o guardare altro'
    )
    assert '"status"' in corpo, 'lo stato della risposta non viene piu- letto'
    assert 'finish_reason' not in _solo_codice(corpo), (
        'legge ancora `finish_reason`, che su v1/responses non esiste: una review '
        'troncata passerebbe per completa'
    )


def test_il_rendiconto_del_gate_legge_i_campi_di_v1_responses():
    """Esegue `usage_note` del gate: cache scontata e reasoning visibile.

    Due nomi cambiano fra i due endpoint, e sbagliarli da' sempre ZERO invece di un
    errore: `input_tokens_details.cached_tokens` per la cache e
    `output_tokens_details.reasoning_tokens` per il ragionamento. Uno zero silenzioso
    sul reasoning e' esattamente il modo in cui l'effort sbagliato di Fugu e'
    sopravvissuto per tre PR.
    """
    spazio: dict = {'PRICE_INPUT_PER_MILLION': 5.0,
                    'PRICE_OUTPUT_PER_MILLION': 30.0,
                    'PRICE_CACHE_READ_PER_MILLION': 0.5}
    exec(_blocco(SOL, 'usage_note'), spazio)  # noqa: S102 - sorgente del repo
    usage_note = spazio['usage_note']

    # 100.000 input di cui 80.000 dalla cache, 1.000 output di cui 700 di reasoning.
    #   20.000*5 + 80.000*0.5 + 1.000*30 = 100 + 40 + 30 = $0.170 /M
    fuori = usage_note(
        {'input_tokens': 100_000, 'output_tokens': 1_000,
         'input_tokens_details': {'cached_tokens': 80_000},
         'output_tokens_details': {'reasoning_tokens': 700}},
        'sys', 'usr', 'review')
    assert '~$0.1700' in fuori, f'aritmetica della cache sbagliata:\n{fuori}'
    assert '80000' in fuori, f'i token dalla cache non sono dichiarati:\n{fuori}'
    assert '700' in fuori and 'reasoning' in fuori, (
        f'i token di reasoning non compaiono: un ragionamento che esplode sarebbe '
        f'indistinguibile da una review lunga.\n{fuori}'
    )
    assert 'OpenAI usage' in fuori, f'la fonte dichiarata e- ancora quella vecchia:\n{fuori}'

    # Difesa: cached > input non deve far scendere il costo sotto il vero.
    assurdo = usage_note(
        {'input_tokens': 1_000, 'output_tokens': 0,
         'input_tokens_details': {'cached_tokens': 999_999}},
        'sys', 'usr', 'review')
    assert '-' not in assurdo.split('Costo stimato base')[1], \
        f'un cached_tokens assurdo ha prodotto un costo negativo:\n{assurdo}'


def test_il_gate_dichiara_il_modello_e_l_etichetta_separati():
    """`MODEL_ID` nella richiesta, `MODEL_LABEL` nel commento.

    Prima erano la stessa variabile, e un cambio di modello obbligava a scegliere fra
    una richiesta valida e un commento leggibile. La label del gate invece NON cambia
    con il modello: `final-fugu-review` esiste nel repo, e rinominarla senza che il
    proprietario crei la nuova renderebbe il gate non armabile (404 sull'API).
    """
    doc = _carica(SOL)
    env = {**(doc.get('env') or {}), **(doc['jobs']['review'].get('env') or {})}
    assert env.get('MODEL_ID') == 'gpt-5.6-sol', f'MODEL_ID inatteso: {env.get("MODEL_ID")}'
    assert env.get('MODEL_LABEL') == 'GPT-5.6 Sol'
    assert env.get('FINAL_LABEL') == 'final-fugu-review', (
        'la label del gate e- cambiata: se la nuova non esiste nel repository, '
        'aggiungerla via API da- 404 e il gate finale non e- armabile'
    )
    assert env.get('PRICE_CACHE_READ_PER_MILLION') == '0.50', \
        'il prezzo dei token dalla cache non e- dichiarato'


def _call_model_del_gate(monkeypatch, risposta=None, errore=None, path=SOL):
    """Esegue `call_model` di un workflow su `v1/responses` con una finta `urlopen`.

    Chiesto da GPT-5.5 nei «test minimi» della PR #20, e la richiesta era giusta: i
    test statici dicono che il payload ha la forma giusta, non che il codice sappia
    leggere una risposta o reagire a un errore. `risposta` e' il corpo JSON che la
    finta restituisce; `errore` un'eccezione da sollevare al suo posto.

    La sostituzione passa da `monkeypatch` e non da un `try/finally` scritto a mano.
    La prima versione lo faceva, in tre punti diversi, e GPT-5.5 ha segnalato la
    fragilita': un ripristino manuale non avviene se l'asserzione solleva prima di
    arrivarci, e tre copie della stessa riga di ripristino sono tre occasioni di
    scriverla male. `monkeypatch` ripristina da se', anche sul fallimento.

    `path` esiste perche' i workflow su questo endpoint sono DUE e leggono la stessa
    forma di risposta: quando la lettura di quella forma cambia, il test va su
    entrambi. Il nome della variabile del modello differisce fra i due sorgenti
    (`MODEL_ID` nel gate, `OPENAI_MODEL` in GPT-5.5), quindi lo spazio dei nomi
    contiene ambedue: un `NameError` qui sarebbe un test verde-per-caso mai.
    """
    import io
    import json as _json
    import urllib.error
    import urllib.request

    class RispostaFinta:
        def __init__(self, corpo):
            self._corpo = _json.dumps(corpo).encode('utf-8')

        def read(self):
            return self._corpo

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    chiamate = []

    def finta_urlopen(req, timeout=None):
        chiamate.append(req)
        if errore is not None:
            raise errore
        return RispostaFinta(risposta)

    spazio: dict = {
        'MODEL_ID': 'gpt-5.6-sol',
        'OPENAI_MODEL': 'gpt-5.5',
        'MAX_OUTPUT_TOKENS': 10_000,
        'BETRELAY_GPT': 'chiave-finta-non-un-segreto',
        'REVIEW_ID': 'gpt56-sol',
        'redact': lambda t: t,
        'json': _json,
        'time': __import__('time'),
        'urllib': urllib,
        'io': io,
    }
    monkeypatch.setattr(urllib.request, 'urlopen', finta_urlopen)
    exec(_blocco(path, 'call_model'), spazio)  # noqa: S102 - sorgente del repo
    return spazio['call_model'], chiamate


@pytest.mark.parametrize('path', SU_V1_RESPONSES, ids=lambda p: p.name)
def test_il_gate_legge_una_risposta_minimale_di_v1_responses(monkeypatch, path):
    """`output_text` e `usage` estratti da una risposta della forma nuova.

    E la richiesta spedita viene ISPEZIONATA: e' l'unico modo di sapere che il
    payload ha davvero `input` e non `messages`, perche' un `assert` sul sorgente
    non prova che quel dizionario finisca nel corpo del POST. Per lo stesso motivo
    ci si aspetta `store: False` nel corpo spedito e non solo nel sorgente: la
    richiesta contiene il diff della PR, e «non memorizzare» e' una promessa che
    vale solo se arriva davvero al fornitore. Chiesto da CodeRabbit sulla PR #20.
    """
    call_model, chiamate = _call_model_del_gate(monkeypatch, path=path, risposta={
        'output_text': 'nessun bloccante',
        'status': 'completed',
        'usage': {'input_tokens': 1_000, 'output_tokens': 10},
    })
    testo, usage = call_model('sistema', 'utente')

    assert testo == 'nessun bloccante', f'testo non estratto: {testo!r}'
    assert usage == {'input_tokens': 1_000, 'output_tokens': 10}

    corpo = _corpo_spedito(chiamate)
    assert 'input' in corpo, f'la richiesta spedita non ha `input`: {sorted(corpo)}'
    assert 'messages' not in corpo, 'la richiesta spedita ha ancora `messages`'

    env = {**(_carica(path).get('env') or {}),
           **(_carica(path)['jobs']['review'].get('env') or {})}
    atteso = env.get('MODEL_ID') or env.get('OPENAI_MODEL')
    assert corpo['model'] == atteso, (
        f'il modello spedito ({corpo["model"]!r}) non e- quello dichiarato '
        f'nell-env del workflow ({atteso!r})'
    )
    assert corpo['max_output_tokens'] == 10_000
    assert 'temperature' not in corpo
    assert corpo.get('store') is False, (
        'la richiesta spedita non porta `store: False`: il diff della PR resterebbe '
        f'memorizzato lato fornitore. Corpo: {sorted(corpo)}'
    )


# `max_output_tokens` e' il motivo ATTESO; `content_filter` e' un motivo che l'API
# puo' restituire e che la vecchia condizione lasciava passare per completo, perche'
# pretendeva `status == 'incomplete' AND reason == 'max_output_tokens'`. Il terzo caso
# e' un `incomplete` che non dichiara affatto il motivo: `incomplete_details` assente,
# cioe' esattamente la risposta su cui una condizione che legge `reason` inciampa.
@pytest.mark.parametrize('path', SU_V1_RESPONSES, ids=lambda p: p.name)
@pytest.mark.parametrize('dettagli', (
    {'reason': 'max_output_tokens'},
    {'reason': 'content_filter'},
    None,
), ids=('max_output_tokens', 'content_filter', 'senza_motivo'))
def test_una_risposta_TRONCATA_non_passa_per_completa(monkeypatch, path, dettagli):
    """`status=incomplete` deve mettere il banner, QUALUNQUE sia il motivo.

    Il banner in testa e' cio' che il chiamante controlla con `startswith` per non
    pubblicare il `done_marker`: senza, una review interrotta a meta' verrebbe
    deduplicata come completata e non rifatta mai piu'.

    Il caso `content_filter` e' il finding major di CodeRabbit sulla PR #20, e la
    condizione precedente lo lasciava passare: chiedeva `status == 'incomplete'`
    **e** `reason == 'max_output_tokens'`, quindi ogni altro motivo di interruzione
    produceva `truncated = False`, cioe' una review parziale pubblicata come
    completa e marcata come fatta. Vale su ENTRAMBI i workflow su questo endpoint,
    perche' la condizione era identica in due posti (regola 2).
    """
    risposta = {'output_text': 'meta- review', 'status': 'incomplete', 'usage': {}}
    if dettagli is not None:
        risposta['incomplete_details'] = dettagli
    call_model, _ = _call_model_del_gate(monkeypatch, path=path, risposta=risposta)
    testo, _ = call_model('sistema', 'utente')

    assert _e_marcata_non_completa(testo, path), (
        f'una review troncata non e- riconosciuta dal guard del chiamante '
        f'{_prefissi_guard(path)}, quindi passerebbe per completa:\n{testo!r}'
    )
    assert 'meta- review' in testo, 'il testo parziale e- stato buttato via'
    # Il motivo va DENTRO il banner: chi legge il commento sulla PR deve sapere se
    # alzare MAX_OUTPUT_TOKENS o guardare altro. Con un `incomplete` senza dettagli
    # il banner lo dichiara invece di tacere.
    atteso = (dettagli or {}).get('reason', 'non dichiarato')
    assert atteso in testo, f'il motivo {atteso!r} non compare nel banner:\n{testo!r}'


# Gli stati terminali di `v1/responses` non sono due. `completed` significa «ho
# finito»; `incomplete` era il solo che il codice riconosceva come non-finito; e
# `failed`, `cancelled` o uno `status` assente sono i modi in cui una risposta NON
# completa passava per completa — perche' la condizione era una blacklist di un solo
# valore invece di una whitelist.
@pytest.mark.parametrize('path', SU_V1_RESPONSES, ids=lambda p: p.name)
@pytest.mark.parametrize('stato, completa', (
    ('completed', True),
    ('incomplete', False),
    ('failed', False),
    ('cancelled', False),
    ('in_progress', False),
    (None, False),
), ids=('completed', 'incomplete', 'failed', 'cancelled', 'in_progress', 'assente'))
def test_solo_status_completed_conta_come_review_completa(monkeypatch, path, stato, completa):
    """Whitelist, non blacklist — e questa volta il difetto era mio.

    Su Fable ho corretto la classe scrivendo una whitelist (`end_turn` e nient'altro)
    e nei due workflow su `v1/responses` ho lasciato la forma debole,
    `status == "incomplete"`, cioe- una blacklist di un solo valore. Poi ho scritto in
    `CLAUDE.md` che tutti e tre «dichiarano completa solo l'uscita che significa ho
    finito», mettendo fra parentesi «`status` diverso da `incomplete` su OpenAI» — che
    e- l'opposto di una whitelist, nella stessa frase che afferma di esserlo. La
    documentazione conteneva la contraddizione che avrebbe dovuto impedire.

    Conseguenza concreta: una risposta `failed` o `cancelled` con del testo dentro
    veniva pubblicata come review completa e timbrata col `done_marker`, quindi mai
    piu- rifatta. Segnalato da `gpt-5.6-sol` come `[REAL_FINDING]` alla sua PRIMA
    chiamata reale, sul workflow che quella chiamata serviva a collaudare.

    `completed` sta nell'elenco per la ragione opposta: e- l'unico caso che NON deve
    portare il banner, e senza di lui un `truncated` sempre vero passerebbe tutto.
    """
    risposta = {'output_text': 'meta- review', 'usage': {}}
    if stato is not None:
        risposta['status'] = stato
    call_model, _ = _call_model_del_gate(monkeypatch, path=path, risposta=risposta)
    testo, _ = call_model('sistema', 'utente')

    assert _e_marcata_non_completa(testo, path) is not completa, (
        f'status={stato!r}: atteso completa={completa}. Una risposta non completa '
        f'senza banner riceve il done_marker e non viene mai piu- rifatta.\n{testo!r}'
    )


# I DUE workflow con gate finale. GPT-5.5 non ne ha uno: gira su ogni push come
# reviewer opzionale, e il suo fail-open e- voluto.
CON_GATE_FINALE = (FABLE, SOL)


@pytest.mark.parametrize('path', CON_GATE_FINALE, ids=lambda p: p.name)
def test_il_fail_closed_guarda_lo_STATO_del_gate_non_il_nome_dell_evento(path):
    """Un gate finale non e- «l'evento si chiama labeled».

    `decisione_gate` lo dice da se- nella sua docstring, e un test verde lo dimostra
    da prima di questa PR (`...pr_aperta_con_label_gia_presente_revisiona`): GitHub
    NON emette `labeled` per una PR aperta con la label gia- applicata, quindi su
    `opened`, `reopened` e `ready_for_review` il workflow si comporta da gate finale
    pur non avendo mai visto un evento `labeled`.

    I guard fail-closed invece chiedevano `EVENT_ACTION == "labeled"`. Su quei tre
    eventi, quindi, una chiave assente o un errore del fornitore uscivano **verdi**:
    il gate finale risultava superato senza che nessun modello avesse letto una riga.
    E- la classe di difetto per cui esiste la regola «un check verde non e- prova di
    review», arrivata fin dentro il meccanismo che quella regola doveva imporre.

    Segnalato da `gpt-5.6-sol` su se stesso; corretto in entrambi i workflow con gate,
    perche- i siti erano sei e non tre (regola 2).
    """
    sorgente = _script(path)
    assert 'GATE_FINALE = esito_gate == "revisiona"' in sorgente, (
        f'{path.name}: manca la nozione unica di «sto agendo da gate finale», derivata '
        'da decisione_gate invece di essere riderivata dal nome dell-evento'
    )
    colpevoli = [r.strip() for r in sorgente.splitlines()
                 if 'EVENT_ACTION == "labeled"' in r
                 and ('model_failed' in r or 'not published' in r)]
    assert not colpevoli, (
        f'{path.name}: {len(colpevoli)} guard fail-closed decidono ancora sul NOME '
        f'dell-evento invece che sullo stato del gate, quindi su opened/reopened/'
        f'ready_for_review con la label presente escono verdi senza review:\n'
        + '\n'.join(f'  {r}' for r in colpevoli)
    )


@pytest.mark.parametrize('path', CON_GATE_FINALE, ids=lambda p: p.name)
def test_anche_il_guard_in_bash_copre_la_pr_aperta_con_la_label(path):
    """Lo stesso difetto prima che Python parta.

    Il controllo «Secret assente» sta in bash, quindi non puo- chiamare
    `decisione_gate`. Non per questo puo- guardare il solo nome dell-evento: se la
    label finale e- nel quadro — nell-elenco della PR o come label dell-evento — una
    chiave mancante deve essere ROSSA, altrimenti il gate risulta superato con zero
    righe lette. Il test pretende che entrambe le variabili siano consultate.
    """
    passo = next(p for p in _carica(path)['jobs']['review']['steps'] if 'run' in p)
    prima_di_python = passo['run'].split('python3')[0]
    assert 'BETRELAY' in prima_di_python, 'il controllo del Secret non e- piu- qui'
    for variabile in ('LABELS_PRESENTI', 'LABEL_EVENTO'):
        assert variabile in prima_di_python, (
            f'{path.name}: il guard in bash sul Secret assente non consulta '
            f'{variabile}, quindi una PR aperta con la label finale gia- applicata '
            'esce VERDE senza review'
        )


def _esegui_guard_bash(path: Path, ambiente: dict) -> int:
    """ESEGUE il pezzo di bash che precede Python, e restituisce il suo exit code.

    Il guard «Secret assente» e' scritto in bash e finora nessun test lo eseguiva:
    era vincolato solo dalla presenza di certe stringhe nel sorgente, che non dice
    nulla su cosa faccia con un payload vero. Chiesto da GPT-5.5 sulla PR #20, che
    dubitava del formato di `LABELS_PRESENTI` prodotto da `toJSON` — un dubbio
    legittimo, perche' se quel formato non e' quello atteso il `grep` non trova la
    label e il gate torna a uscire verde senza review.

    Si taglia allo heredoc `python3` di proposito: eseguire anche quello farebbe
    partire il reviewer per davvero, chiamando GitHub e il fornitore.

    E il taglio va VERIFICATO, non solo fatto. Segnalato da GPT-5.5 sulla PR #20, e il
    rilievo non era di stile: `split('python3')[0]` dipende da un letterale, quindi se
    un domani quel comando cambiasse nome il prefisso diventerebbe **tutto lo script**
    e questo test eseguirebbe il reviewer vero — chiamate reali a GitHub e al
    fornitore, con la spesa relativa, da una corsa di `pytest`. Un test che di
    sorpresa spende soldi e scrive su una PR e- il tipo di guasto che non si vuole
    scoprire dal traffico di rete. Le asserzioni qui sotto lo rendono impossibile:
    se il prefisso contiene ancora l'heredoc o la chiamata al modello, il test
    FALLISCE invece di eseguirlo.
    """
    import subprocess
    import tempfile
    passo = next(p for p in _carica(path)['jobs']['review']['steps'] if 'run' in p)
    intero = passo['run']
    prefisso = intero.split('python3')[0]
    assert len(prefisso) < len(intero), (
        f'{path.name}: `python3` non compare piu- nel passo, quindi il taglio non ha '
        'tagliato NIENTE e questo test eseguirebbe il reviewer per davvero'
    )
    for pericolo in ("<<'PY'", 'urlopen', 'api.openai.com', 'api.anthropic.com',
                     'api.github.com'):
        assert pericolo not in prefisso, (
            f'{path.name}: il prefisso da eseguire contiene ancora {pericolo!r}: '
            'eseguirlo farebbe chiamate reali: test interrotto invece di spendere'
        )
    # TemporaryDirectory invece di delete=False: si ripulisce da se- anche se
    # l'asserzione del chiamante solleva. Chiesto da GPT-5.5 nello stesso giro.
    with tempfile.TemporaryDirectory() as cartella:
        script = os.path.join(cartella, 'guard.sh')
        with open(script, 'w', encoding='utf-8') as f:
            f.write(prefisso)
        # `env` pulito: nessuna variabile della macchina puo' influenzare l'esito.
        return subprocess.run(['bash', script], env=ambiente,
                              capture_output=True, text=True).returncode


# `toJSON` di GitHub stampa il JSON indentato su piu' righe, ma il formato non e'
# garantito dal contratto delle Actions: il test tiene ENTRAMBE le forme, cosi' il
# guard non dipende dall'una. E la lista vuota (`[]`, nessuna label) e' il caso in cui
# uscire verdi e' giusto.
COMPATTO = '["manual-review-required","{finale}"]'
INDENTATO = '[\n  "manual-review-required",\n  "{finale}"\n]'


@pytest.mark.parametrize('path', CON_GATE_FINALE, ids=lambda p: p.name)
@pytest.mark.parametrize('etichette, label_evento, atteso', (
    (COMPATTO, '', 1),
    (INDENTATO, '', 1),
    ('[]', '{finale}', 1),
    ('[]', '', 0),
    ('["manual-review-required"]', 'manual-review-required', 0),
), ids=('opened_label_compatta', 'opened_label_indentata',
        'labeled_senza_elenco', 'nessuna_label', 'altra_label'))
def test_il_guard_bash_ESEGUITO_su_un_payload_vero(path, etichette, label_evento, atteso):
    """Secret assente + gate armato = ROSSO, anche senza evento `labeled`.

    E- il test che GPT-5.5 ha chiesto con queste parole: «PR aperta con label finale
    gia- presente e secret mancante, atteso job rosso». Prima di questa PR quel caso
    usciva VERDE, e il gate finale risultava superato con zero righe lette.

    I due primi casi coprono il dubbio sul formato di `toJSON`: la label va trovata
    sia se il JSON e- compatto sia se e- indentato su piu- righe. Gli ultimi due sono
    il verso opposto, e servono a impedire la correzione pigra «esci sempre rosso»:
    senza label finale il reviewer e- OPZIONALE e un Secret assente non deve bloccare
    la PR — e- il comportamento che rende questi workflow innocui su un fork o su un
    repository senza chiavi.
    """
    finale = 'final-fable-review' if path is FABLE else 'final-fugu-review'
    ambiente = {
        'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
        'FINAL_LABEL': finale,
        'LABELS_PRESENTI': etichette.replace('{finale}', finale),
        'LABEL_EVENTO': label_evento.replace('{finale}', finale),
        'EVENT_ACTION': 'labeled' if label_evento else 'opened',
        # Il Secret NON c'e': e- il caso in prova.
    }
    codice = _esegui_guard_bash(path, ambiente)
    assert codice == atteso, (
        f'{path.name}: con etichette={ambiente["LABELS_PRESENTI"]!r} e '
        f'label_evento={ambiente["LABEL_EVENTO"]!r} il guard e- uscito {codice}, '
        f'atteso {atteso}. Un 0 dove serve 1 significa gate finale VERDE senza review'
    )


@pytest.mark.parametrize('path', SU_V1_RESPONSES, ids=lambda p: p.name)
def test_una_risposta_COMPLETA_non_porta_il_banner(monkeypatch, path):
    """Il contrario del test sopra, e serve.

    Senza, `truncated = True` costante passerebbe tutti i test sul troncamento: ogni
    review porterebbe il banner, nessun `done_marker` verrebbe mai pubblicato e ogni
    push rifarebbe la review da zero, pagandola. Un guard fail-closed che scatta
    sempre non e' un guard, e' un guasto.
    """
    call_model, _ = _call_model_del_gate(monkeypatch, path=path, risposta={
        'output_text': 'nessun bloccante', 'status': 'completed', 'usage': {},
    })
    testo, _ = call_model('sistema', 'utente')
    assert not _e_marcata_non_completa(testo, path), (
        f'una review COMPLETA e- stata marcata come non completa: nessun done_marker '
        f'verrebbe pubblicato e la review si ripaga a ogni push.\n{testo!r}'
    )


def _prefissi_guard(path: Path) -> list:
    """I prefissi che il CHIAMANTE cerca per dichiarare una review non completa.

    Produttore e consumatore del banner stanno in due punti distanti dello stesso
    file: `call_model` prepone «Output troncato: …», e centinaia di righe piu' sotto
    il chiamante fa `review.lstrip().startswith((...))` per NON pubblicare il
    `done_marker`. Sono due letterali indipendenti e nessuno dei due solleva se
    divergono: riformulare il banner in modo che il guard non lo riconosca piu'
    lascerebbe passare per completa ogni review troncata — la stessa conseguenza del
    difetto segnalato da CodeRabbit, per una causa diversa.

    Estrarre i prefissi dal sorgente e asserire su QUELLI, invece di ripetere
    «Output troncato» nei test, e' cio' che lega i due lati. La prima versione di
    questo controllo era statica — cercava i banner che iniziavano per «Output
    troncato» e verificava che il guard li riconoscesse — e passava anche dopo aver
    rinominato un banner, perche' il rinominato non veniva piu' trovato dalla ricerca
    stessa. Misurato per sabotaggio, non dedotto.
    """
    fuori = _script(path).replace(_blocco(path, 'call_model'), '')
    guardie = re.findall(r'startswith\(\s*\(?\s*(?:\n\s*)?"([^"]+)"', fuori)
    assert guardie, f'{path.name}: nessun guard startswith trovato fuori da call_model'
    return guardie


def _e_marcata_non_completa(testo: str, path: Path) -> bool:
    """Vero se il chiamante di quel workflow tratterebbe `testo` come review incompleta."""
    return any(testo.lstrip().startswith(g) for g in _prefissi_guard(path))


def _corpo_spedito(chiamate: list) -> dict:
    """Il corpo JSON del POST che `call_model` ha davvero spedito.

    Esiste per il messaggio d'errore, non per la riga di codice. `chiamate[0].data`
    scritto in linea da' `IndexError: list index out of range` se un domani
    `call_model` smette di chiamare il fornitore — un errore che dice dove si e'
    rotto ma non cosa e- successo, e in un file di guardie e- il difetto che qui si
    e- gia- pagato piu- volte: un test che fallisce senza spiegare si guarda per
    trenta secondi e si archivia come «flaky».

    Segnalato da GPT-5.5 sulla PR #20, ed era in DUE posti — questo e il gate su
    `v1/responses` — quindi la correzione sta in uno (regola 3).
    """
    import json as _json
    assert chiamate, (
        'call_model non ha spedito nessuna richiesta: non c-e- niente da ispezionare. '
        'Se il flusso e- cambiato e la chiamata al fornitore non avviene piu-, questo '
        'test non verifica piu- il payload e va riscritto, non aggiustato'
    )
    return _json.loads(chiamate[0].data.decode('utf-8'))


def test_la_premessa_della_whitelist_di_fable_resta_vera(monkeypatch):
    """Fable dichiara completo solo `end_turn`, e quella scelta ha una PREMESSA.

    Su Anthropic ci sono altri due modi legittimi di finire bene: `tool_use`, se al
    modello si danno strumenti, e `stop_sequence`, se si passano `stop_sequences`.
    Trattarli come troncamento e- corretto **solo** perche- questo workflow non fa
    ne- l'una ne- l'altra cosa: chiede una review di testo e nient'altro.

    Segnalato da GPT-5.5 come rischio manuale sulla PR #20, e la segnalazione era
    giusta: la premessa viveva in un commento, quindi aggiungere `tools` al payload
    un domani non avrebbe fatto diventare rosso niente — ogni review con
    `stop_reason=tool_use` sarebbe stata marcata troncata, il `done_marker` non
    sarebbe mai stato pubblicato, e la review si sarebbe ripagata a ogni push. Un
    guasto silenzioso e costoso, non un errore.

    L'asserzione e' sul corpo del POST **effettivamente spedito**, non sul sorgente.
    La prima versione cercava le stringhe `"tools"` e `"stop_sequences"` nel testo di
    `call_model`, e GPT-5.5 ha obiettato al giro dopo che una ricerca per stringa da-
    un falso negativo se il payload viene costruito indirettamente — via variabile,
    helper o costante esterna. Aveva ragione: il test sarebbe stato verde mentre il
    campo veniva spedito. Serializzare il payload e guardare le chiavi che arrivano
    al fornitore chiude quel buco, e non si rompe per un refactor equivalente.

    Che `end_turn` sia l'unico motivo accettato non e' asserito qui sul testo della
    whitelist: lo dimostra, sui cinque valori, il test parametrizzato qui sopra.
    """
    risposta = {'content': [{'type': 'text', 'text': 'ok'}],
                'stop_reason': 'end_turn', 'usage': {}}
    call_model, chiamate = _call_model_di_fable(monkeypatch, risposta)
    call_model('sistema', 'utente')

    corpo = _corpo_spedito(chiamate)
    assert 'tools' not in corpo, (
        'il payload di Fable ora spedisce `tools`: `stop_reason=tool_use` diventa un '
        'modo LEGITTIMO di finire, e la whitelist `MOTIVI_COMPLETI` va aggiornata o '
        f'ogni review verra- marcata troncata e ripagata a ogni push. Corpo: {sorted(corpo)}'
    )
    assert 'stop_sequences' not in corpo, (
        'il payload di Fable ora spedisce `stop_sequences`: `stop_reason=stop_sequence` '
        f'diventa un esito atteso e va aggiunto a `MOTIVI_COMPLETI`. Corpo: {sorted(corpo)}'
    )


def _call_model_di_fable(monkeypatch, risposta):
    """Come sopra, ma per Fable: Anthropic, quindi `stop_reason` e `content`.

    Esiste perche' la falla del troncamento sotto-rilevato non era solo dei due
    workflow su `v1/responses`: qui il campo si chiama diversamente e i valori sono
    altri, ma la condizione aveva la stessa forma sbagliata — nominare il solo
    motivo atteso. Cercare la CLASSE e non il sito significa arrivare anche qui.
    """
    import io
    import json as _json
    import urllib.error
    import urllib.request

    class RispostaFinta:
        def __init__(self, corpo):
            self._corpo = _json.dumps(corpo).encode('utf-8')

        def read(self):
            return self._corpo

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    chiamate = []

    def finta_urlopen(req, timeout=None):
        chiamate.append(req)
        return RispostaFinta(risposta)

    monkeypatch.setattr(urllib.request, 'urlopen', finta_urlopen)
    spazio: dict = {
        'ANTHROPIC_MODEL': 'claude-fable-5',
        'ANTHROPIC_VERSION': '2023-06-01',
        'MAX_OUTPUT_TOKENS': 3_000,
        'BETRELAY_FABLE': 'chiave-finta-non-un-segreto',
        'REVIEW_ID': 'claude-fable5',
        'redact': lambda t: t,
        'json': _json,
        'time': __import__('time'),
        'urllib': urllib,
        'io': io,
    }
    exec(_blocco(FABLE, 'call_model'), spazio)  # noqa: S102 - sorgente del repo
    return spazio['call_model'], chiamate


# `end_turn` e' l'UNICO motivo che significa «ho finito la review». `max_tokens` era
# l'unico che il codice riconosceva. `refusal` e un `stop_reason` assente sono i due
# modi in cui una review incompleta passava per completa.
@pytest.mark.parametrize('stop, troncata', (
    ('end_turn', False),
    ('max_tokens', True),
    ('refusal', True),
    ('pause_turn', True),
    (None, True),
), ids=('end_turn', 'max_tokens', 'refusal', 'pause_turn', 'assente'))
def test_fable_marca_troncata_ogni_uscita_che_non_sia_end_turn(monkeypatch, stop, troncata):
    """La stessa falla di CodeRabbit sulla PR #20, sull'altro fornitore.

    Su Anthropic il campo e' `stop_reason` e i valori sono altri, ma il difetto era
    identico: `truncated = body.get("stop_reason") == "max_tokens"` riconosceva un
    solo modo di non finire. Un `refusal` — o una risposta senza `stop_reason`, cioe'
    una forma che non conosciamo — produceva `truncated = False`, quindi una review
    parziale pubblicata come completa e marcata come fatta dal `done_marker`.

    Il caso `end_turn` e' nell'elenco per la ragione opposta: un `truncated` sempre
    vero passerebbe tutti gli altri, e vorrebbe dire nessun `done_marker` mai e la
    review pagata a ogni push.
    """
    risposta = {'content': [{'type': 'text', 'text': 'meta- review'}], 'usage': {}}
    if stop is not None:
        risposta['stop_reason'] = stop
    call_model, _ = _call_model_di_fable(monkeypatch, risposta)
    testo, _ = call_model('sistema', 'utente')

    assert _e_marcata_non_completa(testo, FABLE) is troncata, (
        f'stop_reason={stop!r}: atteso troncata={troncata}, ottenuto:\n{testo!r}'
    )
    assert 'meta- review' in testo, 'il testo del modello e- stato buttato via'
    if troncata:
        assert (stop or 'non dichiarato') in testo, \
            f'il motivo non compare nel banner:\n{testo!r}'


@pytest.mark.parametrize('path', SU_V1_RESPONSES, ids=lambda p: p.name)
def test_un_400_del_fornitore_fa_FALLIRE_e_non_restituisce_una_review(monkeypatch, path):
    """Chiesto da GPT-5.5: fail-closed sull'errore del fornitore.

    Un 400 non e' un caso da ritentare — la richiesta e' malformata, e ritentarla
    tre volte dara' tre volte 400. La cosa che conta e' che `call_model` **solleva**
    invece di restituire una stringa: se restituisse testo, quel testo diventerebbe
    la review, il gate a label la pubblicherebbe e uscirebbe verde su una chiamata
    mai andata a buon fine.

    E' il difetto che questa PR ha sfiorato per davvero: col payload sbagliato ogni
    armamento avrebbe preso un 400.
    """
    import io
    import urllib.error
    call_model, chiamate = _call_model_del_gate(monkeypatch, path=path, errore=urllib.error.HTTPError(
        'https://api.openai.com/v1/responses', 400, 'Bad Request', {},
        io.BytesIO(b'{"error": {"message": "Unknown parameter: messages."}}')))
    with pytest.raises(RuntimeError) as esito:
        call_model('sistema', 'utente')

    assert 'HTTP 400' in str(esito.value), f'il motivo non arriva al chiamante: {esito.value}'
    assert len(chiamate) == 1, (
        f'un 400 e- stato ritentato {len(chiamate)} volte: la richiesta e- malformata, '
        'ritentarla da- tre volte lo stesso errore e tre volte il costo'
    )
