"""Servidor local de la demo de Ynara.

Uso:  python serve.py [puerto]      (por defecto 3120)
Abre http://localhost:3120 en Chrome, Edge o Safari.

Sirve la carpeta sin caché para que los cambios se vean al recargar. Solo
escucha en este equipo (127.0.0.1): el micrófono del navegador funciona en
localhost sin necesidad de HTTPS.
"""

import http.server
import os
import sys
import webbrowser

PORT = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 3120
ROOT = os.path.dirname(os.path.abspath(__file__))


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    with http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler) as httpd:
        url = f"http://localhost:{PORT}"
        print(f"Ynara demo en {url}  (Ctrl+C para cerrar)")
        if "--abrir" in sys.argv:
            webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
