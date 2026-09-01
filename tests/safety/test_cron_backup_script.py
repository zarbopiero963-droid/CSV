"""Guardia sullo script cron d'esempio `deploy/cron-backup/invia_backup.sh` (#56 pezzo 3b).

Lo script e' l'esempio che il proprietario mette su Railway per l'invio notturno del
backup. Non e' servito da nessuna parte (sta in `deploy/`, non in `web/`), ma e'
comportamento spedito: se e' rotto, il cron notturno non manda mai il backup e nessuno
se ne accorge — il tipo di guasto silenzioso che questo repository combatte.

Due invarianti, ESEGUENDO lo script (non leggendone il testo):

- **fail-closed**: senza `BACKUP_CRON_TOKEN` (o senza `BACKUP_BASE_URL`) lo script esce
  non-zero e NON chiama curl. E' il `:?` di `${VAR:?...}`, non un default `:=`;
- **header giusto**: con le variabili impostate, curl riceve l'header
  `X-Backup-Cron-Token: <valore reale>` (due punti, sintassi header di curl) e l'URL
  `.../api/admin/backup/invia`, col token preso dalla variabile e MAI nell'URL.

Questo test nasce anche per fissare per sempre un falso positivo: i reviewer AL gate
leggono un diff REDATTO, dove `-H "X-Backup-Cron-Token: ${...}"` diventa
`X-Backup-Cron-Token=[REDACTED]` (con `=`), e concludono — correttamente, dato quel che
vedono — che l'header e' malformato. Qui si esercita il file VERO, dove il difetto non
c'e'.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

RADICE = Path(__file__).resolve().parents[2]
SCRIPT = RADICE / 'deploy' / 'cron-backup' / 'invia_backup.sh'


def _esegui(env, fake_curl_dir=None):
    """Esegue lo script con un ambiente MINIMO e restituisce (returncode, stdout+stderr).

    Con `fake_curl_dir` un finto `curl` che stampa i suoi argomenti precede il PATH, cosi'
    si osserva cosa lo script passerebbe a curl senza toccare la rete."""
    ambiente = {'PATH': '/usr/bin:/bin'}
    if fake_curl_dir is not None:
        ambiente['PATH'] = f'{fake_curl_dir}:{ambiente["PATH"]}'
    ambiente.update(env)
    proc = subprocess.run(['sh', str(SCRIPT)], env=ambiente,
                          capture_output=True, text=True, timeout=30)
    return proc.returncode, proc.stdout + proc.stderr


def test_lo_script_esiste_ed_e_eseguibile():
    assert SCRIPT.is_file(), f'script cron assente: {SCRIPT}'
    assert os.access(SCRIPT, os.X_OK), 'lo script cron non ha il bit di esecuzione'


def test_fail_closed_senza_token(tmp_path):
    """Senza `BACKUP_CRON_TOKEN` lo script esce non-zero e non invia niente."""
    fake = tmp_path / 'bin'
    fake.mkdir()
    sentinella = tmp_path / 'curl_chiamato'
    (fake / 'curl').write_text('#!/usr/bin/env sh\ntouch "%s"\n' % sentinella, encoding='utf-8')
    (fake / 'curl').chmod(0o755)

    rc, out = _esegui({'BACKUP_BASE_URL': 'https://example.test'}, fake_curl_dir=str(fake))
    assert rc != 0, f'lo script non e- fallito senza il token (fail-closed): {out!r}'
    assert not sentinella.exists(), 'curl e- stato chiamato pur mancando il token'
    assert 'BACKUP_CRON_TOKEN' in out, f'il motivo non nomina la variabile mancante: {out!r}'


def test_fail_closed_senza_base_url(tmp_path):
    """Senza `BACKUP_BASE_URL` lo script esce non-zero e non invia niente."""
    fake = tmp_path / 'bin'
    fake.mkdir()
    sentinella = tmp_path / 'curl_chiamato'
    (fake / 'curl').write_text('#!/usr/bin/env sh\ntouch "%s"\n' % sentinella, encoding='utf-8')
    (fake / 'curl').chmod(0o755)

    rc, out = _esegui({'BACKUP_CRON_TOKEN': 'x'}, fake_curl_dir=str(fake))
    assert rc != 0, f'lo script non e- fallito senza BASE_URL: {out!r}'
    assert not sentinella.exists(), 'curl e- stato chiamato pur mancando BASE_URL'


def test_header_col_valore_reale_e_due_punti(tmp_path):
    """Con le variabili impostate, curl riceve l'header con i DUE PUNTI e il valore VERO del
    token, e l'URL dell'endpoint di invio. Il token non compare nell'URL."""
    fake = tmp_path / 'bin'
    fake.mkdir()
    dump = tmp_path / 'args'
    # Il finto curl scrive i suoi argomenti, uno per riga, su un file.
    (fake / 'curl').write_text(
        '#!/usr/bin/env sh\n: > "%s"\nfor a in "$@"; do printf "%%s\\n" "$a" >> "%s"; done\n'
        % (dump, dump), encoding='utf-8')
    (fake / 'curl').chmod(0o755)

    # Valore fittizio (vive solo in questo test), ma i messaggi degli assert NON lo stampano
    # comunque: un header con un segreto non deve mai finire nell'output CI, anche finto — il
    # pattern non va copiato su un valore vero (nota di GPT-5.5, PR #102).
    valore_finto = 'PROVA-9f8e7d'
    rc, out = _esegui({'BACKUP_BASE_URL': 'https://example.test/',
                       'BACKUP_CRON_TOKEN': valore_finto}, fake_curl_dir=str(fake))
    assert rc == 0, 'lo script e- fallito con le variabili giuste'
    args = dump.read_text(encoding='utf-8').splitlines()

    assert 'https://example.test/api/admin/backup/invia' in args, \
        'URL dell-endpoint assente o sbagliato fra gli argomenti di curl'
    assert f'X-Backup-Cron-Token: {valore_finto}' in args, \
        'header col valore reale e i due punti assente fra gli argomenti di curl'
    # Il valore NON deve finire nell'URL (gli URL finiscono nei log).
    url = next(a for a in args if a.startswith('http'))
    assert valore_finto not in url, 'il valore del token e- finito nell-URL'
