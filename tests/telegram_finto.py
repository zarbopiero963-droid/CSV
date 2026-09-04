"""Un finto `api.telegram.org` sul loopback, per i test che devono vedere una
chiamata in uscita **riuscire**.

**Perche' non esisteva prima.** I test avviano il relay in un sottoprocesso con
`HTTPS_PROXY` su una porta morta: qualunque chiamata verso Telegram fallisce, il
che va benissimo per misurare il percorso d'errore ed e' l'unico percorso che i
test hanno mai potuto misurare. Con la prova di ruolo del #115 non basta piu': un
cancello che si limita a fallire non e' un cancello, e un test non saprebbe
distinguere «rifiuta perche' non e' amministratore» da «rifiuta perche' la rete e'
giu'».

**Come si aggancia.** `main.url_telegram` legge `TELEGRAM_API_BASE`, quindi basta
passare al relay l'indirizzo di questo server. E' `http://`, non `https://`,
quindi `HTTPS_PROXY` non lo intercetta: le due cose convivono, e un test puo'
avere insieme un Telegram raggiungibile per `getChatMember` e il resto del mondo
irraggiungibile.

**Conta le chiamate**, e serve: una parte del contratto del #115 e' che Telegram
NON venga chiamato per un codice gia' morto — senza quel filtro, chiunque possa
scrivere in una chat dove il bot e' presente potrebbe farci fare una raffica di
chiamate in uscita scrivendo stringhe della forma giusta.
"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit


class TelegramFinto:
    """Le risposte da dare e le chiamate ricevute. Condiviso col thread del server."""

    def __init__(self):
        # {metodo: (codice_http, corpo_json)} — il metodo e' l'ultimo pezzo del
        # percorso, es. 'getChatMember'. Un metodo non configurato da 404, che il
        # relay tratta come fallimento: il difetto e' rumoroso, non silenzioso.
        self.risposte: dict[str, tuple[int, dict]] = {}
        # [(metodo, {parametro: valore}), ...] in ordine di arrivo.
        self.chiamate: list[tuple[str, dict]] = []

    def rispondi(self, metodo: str, risultato, ok: bool = True, http: int = 200):
        """Configura la risposta di un metodo dell'API."""
        self.risposte[metodo] = (http, {'ok': ok, 'result': risultato})

    def ruolo(self, stato: str):
        """Scorciatoia: `getChatMember` risponde con quel `status`."""
        self.rispondi('getChatMember', {'status': stato})

    def quante(self, metodo: str) -> int:
        return sum(1 for m, _ in self.chiamate if m == metodo)


def _fabbrica(stato: TelegramFinto):
    class Manico(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - nome imposto da BaseHTTPRequestHandler
            pezzi = urlsplit(self.path)
            metodo = pezzi.path.rsplit('/', 1)[-1]
            parametri = {k: v[0] for k, v in parse_qs(pezzi.query).items()}
            stato.chiamate.append((metodo, parametri))
            http, corpo = stato.risposte.get(
                metodo, (404, {'ok': False, 'description': 'metodo non configurato'}))
            grezzo = json.dumps(corpo).encode('utf-8')
            self.send_response(http)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(grezzo)))
            self.end_headers()
            self.wfile.write(grezzo)

        do_POST = do_GET  # noqa: N815 - stessa risposta, il corpo qui non serve

        def log_message(self, *_):
            """Silenzio: l'output del server sporcherebbe quello dei test."""

    return Manico


@contextmanager
def telegram_finto():
    """Avvia il finto Telegram. Restituisce `(base_url, stato)`.

    `base_url` va passato al relay come `TELEGRAM_API_BASE`; `stato` serve al test
    per configurare le risposte e leggere le chiamate ricevute.
    """
    stato = TelegramFinto()
    server = HTTPServer(('127.0.0.1', 0), _fabbrica(stato))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', stato
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
