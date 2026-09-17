"""Adaptateur d'hébergement sans état (Vercel, AWS Lambda…).

Vercel ne sait lancer qu'une fonction par requête ; les routes de Passerelle
vivent dans une classe BaseHTTPRequestHandler. Plutôt que de les réécrire, on
fabrique un gestionnaire factice : sa « socket » est deux Buffer en mémoire et
on relit les octets qu'il émet. Ainsi `server.py`, `formats.py`, `engines.py`
et `stateless.py` restent LE chemin de code, en local comme en ligne.

L'environnement est forcé ici plutôt que dans un tableau de bord : c'est le seul
endroit où l'on est sûr qu'il s'applique à chaque instance éphémère.
"""

import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for _d in (HERE, os.path.dirname(HERE)):
    if _d not in sys.path:
        sys.path.insert(0, _d)

# /tmp est le seul répertoire inscriptible d'une fonction Vercel ; le cache de
# paires n'est qu'une optimisation, sa perte ne casse rien.
os.environ.setdefault("PASSERELLE_DIR", "/tmp")
os.environ.setdefault("PASSERELLE_STATELESS", "1")
# Le corps de requête est plafonné côté hébergeur (4,5 Mo) et le fichier y
# voyage en base64 (+33 %) : on descend la limite, l'interface la lit.
os.environ.setdefault("PASSERELLE_MAX_BODY", str(3 * 1024 * 1024))
# Une fonction est tuée au-delà de maxDuration : on borne le travail par requête.
os.environ.setdefault("PASSERELLE_WINDOW", "30")
os.environ.setdefault("PASSERELLE_WINDOW_CHARS", "6000")

import server as core  # noqa: E402  (importé après les variables d'environnement)
from flask import Flask, Response, request  # noqa: E402

app = Flask(__name__)

# Les noms que le serveur connaît sous /api/ ; sert à retaper un chemin que
# l'hébergeur aurait reçu sans son préfixe.
API_NAMES = ("meta", "jobs", "open", "translate", "build", "zip", "cache", "engine", "detect")


def invoke_path():
    """Le chemin voulu par le navigateur, quel que soit le montage de la fonction."""
    raw = request.headers.get("x-invoke-path") or request.path or "/"
    raw = re.sub(r"^/api/index(?=/|$)", "", raw) or "/"
    if not raw.startswith("/"):
        raw = "/" + raw
    if raw == "/api" or raw.startswith("/api/"):
        return raw
    if raw.strip("/").split("/")[0] in API_NAMES:
        return "/api" + raw
    return raw


class _Headers:
    """En-têtes HTTP en lecture insensible à la casse, sans dépendre de werkzeug."""

    def __init__(self, mapping):
        self._low = {str(k).lower(): v for k, v in mapping.items()}

    def get(self, key, default=None):
        return self._low.get(str(key).lower(), default)

    def __getitem__(self, key):
        found = self.get(key)
        if found is None:
            raise KeyError(key)
        return found

    def __contains__(self, key):
        return self.get(key) is not None


class FunctionHandler(core.Handler):
    """Le vrai gestionnaire du serveur, avec la socket remplacée par des Buffer."""

    def __init__(self, method, target, headers, body):
        self.command = method
        self.path = target
        self.requestline = f"{method} {target} HTTP/1.1"
        self.raw_requestline = self.requestline.encode("latin-1")
        self.headers = _Headers(headers)
        self.request_version = "HTTP/1.1"
        self.protocol_version = "HTTP/1.1"
        self.client_address = ("0.0.0.0", 0)
        self.server = None
        self.close_connection = True
        self.rfile = io.BytesIO(body or b"")
        self.wfile = io.BytesIO()
        self._headers_buffer = []

    # ce que la classe mère attend d'une connexion : rien ici
    def setup(self):
        pass

    def finish(self):
        pass

    def handle(self):
        pass

    def log_message(self, fmt, *args):
        pass

    def connection_closed(self):
        return True

    def address_string(self):
        return "0.0.0.0"


def dispatch(method, target, headers, body):
    """Une requête HTTP,aller-retour, sans serveur : (statut, en-têtes, corps)."""
    handler = FunctionHandler(method, target, headers, body)
    try:
        getattr(handler, "do_" + method if method in ("GET", "HEAD", "POST") else "do_GET")()
    except Exception as exc:  # noqa: BLE001 - une fonction doit répondre, pas exploser
        return (500, {"Content-Type": "text/plain; charset=utf-8"},
                f"erreur interne : {type(exc).__name__} : {exc}".encode("utf-8"))
    raw = handler.wfile.getvalue()
    head, _, out = raw.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    status = 200
    if lines and b" " in lines[0]:
        try:
            status = int(lines[0].split(b" ")[1])
        except (IndexError, ValueError):
            status = 200
    out_headers = {}
    for line in lines[1:]:
        key, sep, value = line.partition(b":")
        if sep:
            name = key.decode("latin-1").strip()
            if name.lower() not in ("transfer-encoding", "connection", "content-length"):
                out_headers[name] = value.decode("latin-1").strip()
    return status, out_headers, out


@app.route("/", defaults={"tail": ""}, methods=["GET", "HEAD", "POST"])
@app.route("/<path:tail>", methods=["GET", "HEAD", "POST"])
def any_path(tail):
    status, headers, body = dispatch(request.method, invoke_path(), dict(request.headers), request.get_data())
    return Response(body, status=status, headers=headers)
