#!/usr/bin/env python3
"""Genera una versione a file unico del prototipo, comoda da condividere.

    python3 tools/build_single_file.py          # scrive dist/prototipo.html
    python3 tools/build_single_file.py /tmp/x.html

Sta in tools/ e non in web/, e scrive fuori da web/, perche' `main.py` monta web/
con StaticFiles: qualunque file la- dentro e- scaricabile pubblicamente su /app
senza token. Un generatore lato server e il suo output non hanno motivo di essere
serviti a un browser. La guardia e- in tests/safety/test_static_mount.py.

Concatena engine.js, api_finta.js e app.js in un solo modulo, ricostruendo
l'oggetto `api` che le viste usano come namespace, e incorpora il CSS.
`api_finta.js` e NON `api.js`: la copia si apre da file://, dove `fetch` verso
il backend non esiste — il layer finto espone la stessa superficie su
localStorage, ed e' l'unico posto in cui viene usato.

Il JavaScript viene emesso in ASCII puro con escape \\uXXXX: in un file unico il
codice viene decodificato con la codifica del documento, non sempre UTF-8, e un
emoji come il separatore evento diventerebbe mojibake facendo fallire i confronti
di testo. La versione modulare in web/ non ha questo problema, perche' i moduli ES
sono UTF-8 per specifica.
"""

import base64
import re
import sys
from pathlib import Path

RADICE = Path(__file__).resolve().parents[1]
WEB = RADICE / 'web'

# Il marcatore del logo relay nelle viste (#59): l'app lo serve come file da
# /app/, ma il bundle si apre da file:// dove un percorso relativo e' un'icona
# rotta SENZA errori — la classe di guasto silenzioso della REGOLA CODIFICA,
# versione immagine. Il generatore lo sostituisce con un data URI base64, che
# e' ASCII per costruzione.
MARCATORE_LOGO = 'src="betrelay-icona-256.png"'


def strip_module_syntax(src):
    """Rimuove gli import relativi e la parola chiave export.

    Ragiona per ISTRUZIONI, non per righe: un `import { a, b }` spezzato su piu'
    righe e' JavaScript legittimo, e togliendo solo la riga col `from` il bundle
    resterebbe con un `import` mozzo. Il browser rifiuterebbe il modulo e la
    pagina resterebbe bianca senza spiegazioni.

    Un import NON relativo (una dipendenza esterna) e' un errore in questo
    progetto: il prototipo non ha CDN ne' build step, quindi si ferma a voce alta.
    """
    righe = src.split('\n')
    out = []
    i = 0
    while i < len(righe):
        riga = righe[i]
        if not re.match(r'\s*import[\s{*\'"]', riga):
            out.append(re.sub(r'^export\s+', '', riga))
            i += 1
            continue

        # Accumula finche' l'istruzione non contiene il proprio specificatore.
        blocco = [riga]
        while not re.search(r"""from\s+['"][^'"]+['"]|^\s*import\s+['"][^'"]+['"]""",
                            '\n'.join(blocco)):
            i += 1
            if i >= len(righe):
                raise SystemExit(
                    'import non terminato nel file: %s' % blocco[0].strip())
            blocco.append(righe[i])
        i += 1

        testo = '\n'.join(blocco)
        m = re.search(r"""['"]([^'"]+)['"]""", testo)
        spec = m.group(1) if m else ''
        if not spec.startswith('.'):
            raise SystemExit(
                'import non relativo non gestito dal bundle a file unico '
                '(nessuna dipendenza esterna e\' prevista): %s' % testo.strip())
        # Import relativo: il modulo e' gia' concatenato nel bundle, si scarta.

    return '\n'.join(out)


def to_ascii(text):
    """Escapa i caratteri non ASCII, con coppie surrogate per gli astrali."""
    out = []
    for ch in text:
        cp = ord(ch)
        if cp < 128:
            out.append(ch)
        elif cp <= 0xFFFF:
            out.append('\\u%04x' % cp)
        else:
            cp -= 0x10000
            out.append('\\u%04x\\u%04x' % (0xD800 + (cp >> 10), 0xDC00 + (cp & 0x3FF)))
    return ''.join(out)


def build():
    css = (WEB / 'styles.css').read_text(encoding='utf-8')
    # Il CSS finisce nel documento cosi' com'e': to_ascii protegge solo il
    # JavaScript. Con un carattere non ASCII nel foglio di stile la scrittura in
    # ASCII piu' sotto solleverebbe UnicodeEncodeError senza nominare la causa.
    non_ascii = sorted({c for c in css if ord(c) > 127})
    if non_ascii:
        raise SystemExit(
            'styles.css contiene caratteri non ASCII (%s): il file unico li '
            'romperebbe. Usa un escape CSS \\XXXXXX oppure togli il carattere.'
            % ' '.join('U+%04X' % ord(c) for c in non_ascii)
        )
    engine = (WEB / 'engine.js').read_text(encoding='utf-8')
    api = (WEB / 'api_finta.js').read_text(encoding='utf-8')
    app = (WEB / 'app.js').read_text(encoding='utf-8')

    # app.js importa api.js come namespace: qui il namespace va ricostruito
    # dal layer finto, che espone le stesse funzioni.
    api_names = re.findall(r'^export\s+(?:async\s+)?function\s+(\w+)', api, re.M)

    js = '\n'.join([
        '/* ===== engine.js ===== */', strip_module_syntax(engine),
        '/* ===== api_finta.js ===== */', strip_module_syntax(api),
        'const api = { %s };' % ', '.join(api_names),
        '/* ===== app.js ===== */', strip_module_syntax(app),
    ])

    # Il logo relay: la sostituzione DEVE avvenire davvero. Un marcatore sparito
    # (file rinominato, vista riscritta) non e' un dettaglio: il bundle uscirebbe
    # con l'icona rotta e nessun controllo sui byte se ne accorgerebbe — quindi
    # ci si ferma a voce alta, come per gli import esterni.
    if MARCATORE_LOGO not in js:
        raise SystemExit(
            'il marcatore del logo (%s) non e\' piu\' nelle viste: aggiorna '
            'MARCATORE_LOGO in questo generatore o ripristina l\'immagine.'
            % MARCATORE_LOGO)
    icona = base64.b64encode(
        (WEB / 'betrelay-icona-256.png').read_bytes()).decode('ascii')
    js = js.replace(MARCATORE_LOGO, 'src="data:image/png;base64,' + icona + '"')

    # La favicon del bundle e' lo stesso .ico dell'app, incorporato: da file://
    # non esiste un server che serva il file accanto.
    favicon = ('data:image/x-icon;base64,' + base64.b64encode(
        (WEB / 'betrelay-favicon-sito.ico').read_bytes()).decode('ascii'))

    return (
        '<!doctype html>\n<html lang="it">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<title>Prototipo BetRelay</title>\n'
        '<link rel="icon" href="' + favicon + '">\n'
        '<style>\n' + css + '\n</style>\n</head>\n<body>\n'
        '<div class="proto">PROTOTIPO &middot; DATI FINTI</div>\n'
        '<div id="app"></div>\n'
        '<script type="module">\n' + to_ascii(js) + '\n</script>\n'
        '</body>\n</html>\n'
    )


if __name__ == '__main__':
    # Fuori da web/: l'output sotto il mount sarebbe servito su /app/dist/.
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else RADICE / 'dist' / 'prototipo.html'
    dest.parent.mkdir(parents=True, exist_ok=True)
    html = build()
    dest.write_text(html, encoding='ascii')
    print('scritto %s (%d byte)' % (dest, len(html)))
