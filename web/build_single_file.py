#!/usr/bin/env python3
"""Genera una versione a file unico del prototipo, comoda da condividere.

    python3 web/build_single_file.py            # scrive web/dist/prototipo.html
    python3 web/build_single_file.py /tmp/x.html

Concatena engine.js, api.js e app.js in un solo modulo, ricostruendo l'oggetto
`api` che le viste usano come namespace, e incorpora il CSS.

Il JavaScript viene emesso in ASCII puro con escape \\uXXXX: in un file unico il
codice viene decodificato con la codifica del documento, non sempre UTF-8, e un
emoji come il separatore evento diventerebbe mojibake facendo fallire i confronti
di testo. La versione modulare in web/ non ha questo problema, perche' i moduli ES
sono UTF-8 per specifica.
"""

import re
import sys
from pathlib import Path

WEB = Path(__file__).parent

# Stessa favicon di index.html, incorporata come data URI.
FAVICON = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E"
    "%3Crect width='32' height='32' rx='7' fill='%232aabee'/%3E"
    "%3Ctext x='16' y='22' font-family='sans-serif' font-size='15' font-weight='700'"
    " fill='%2304121b' text-anchor='middle'%3EXT%3C/text%3E%3C/svg%3E"
)


def strip_module_syntax(src):
    """Rimuove gli import relativi e la parola chiave export."""
    lines = []
    for line in src.split('\n'):
        if re.match(r"\s*import\s.*from\s+'\./", line):
            continue
        lines.append(re.sub(r'^export\s+', '', line))
    return '\n'.join(lines)


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
    engine = (WEB / 'engine.js').read_text(encoding='utf-8')
    api = (WEB / 'api.js').read_text(encoding='utf-8')
    app = (WEB / 'app.js').read_text(encoding='utf-8')

    # app.js importa api.js come namespace: qui il namespace va ricostruito.
    api_names = re.findall(r'^export\s+(?:async\s+)?function\s+(\w+)', api, re.M)

    js = '\n'.join([
        '/* ===== engine.js ===== */', strip_module_syntax(engine),
        '/* ===== api.js ===== */', strip_module_syntax(api),
        'const api = { %s };' % ', '.join(api_names),
        '/* ===== app.js ===== */', strip_module_syntax(app),
    ])

    return (
        '<!doctype html>\n<html lang="it">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<title>Prototipo XTrader Signal Relay</title>\n'
        '<link rel="icon" href="' + FAVICON + '">\n'
        '<style>\n' + css + '\n</style>\n</head>\n<body>\n'
        '<div class="proto">PROTOTIPO &middot; DATI FINTI</div>\n'
        '<div id="app"></div>\n'
        '<script type="module">\n' + to_ascii(js) + '\n</script>\n'
        '</body>\n</html>\n'
    )


if __name__ == '__main__':
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else WEB / 'dist' / 'prototipo.html'
    dest.parent.mkdir(parents=True, exist_ok=True)
    html = build()
    dest.write_text(html, encoding='ascii')
    print('scritto %s (%d byte)' % (dest, len(html)))
