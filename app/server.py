#!/usr/bin/env python3
"""TradFilez — serveur de traduction de fichiers.

Ne dépend que de la bibliothèque standard (plus pypdf / python-docx /
openpyxl / beautifulsoup4 si présents). Rien n'est écrit sur disque hormis le
cache de paires de traduction, vidable depuis l'interface.

    python3 server.py --port 8000
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import mimetypes
import os
import re
import threading
import time
import traceback
import urllib.parse
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import engines as eng
import formats as fmt
import stateless as stl

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")
def _env_size(name, default):
    try:
        return max(1024, int(eng.env(name, default)))
    except ValueError:
        return default


# 8 Mo en local ; un hébergeur « serverless » limite le corps de requête, on
# l'y descend via TRADFILEZ_MAX_BODY (le fichier y voyage en base64, +33 %).
MAX_UPLOAD = _env_size("max_body", 8 * 1024 * 1024)
# Sur un hébergeur sans état, aucune instance ne garde la file de tâches : le site bascule sur
# l'API sans état (open → translate → build), le navigateur tenant les morceaux.
STATELESS = (eng.env("stateless", "") or "").lower() in ("1", "true", "oui")
JOB_TTL = 3 * 3600
MAX_JOBS = 60
PREVIEW_LIMIT = 24000

class EngineCancelled(Exception):
    """La tâche a été interrompue par l'utilisateur."""


class EngineExhausted(Exception):
    """Aucun moteur n'a répondu : rien à remettre à l'utilisateur."""


# --------------------------------------------------------------------------- #
# découpage fin des très longs paragraphes
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# tâches
# --------------------------------------------------------------------------- #


class Job:
    def __init__(self, filename, blob, src, tgt, options, config):
        self.id = uuid.uuid4().hex[:12]
        self.name = filename
        self.blob = blob
        self.src = src or "auto"
        self.tgt = tgt or "fr"
        self.options = options or {}
        self.config = config or {}
        self.state = "attente"
        self.stage = "lecture du fichier"
        self.error = None
        self.warnings = []
        self.started = time.time()
        self.finished = None
        self.cancelled = False
        self._lock = threading.Lock()
        self.doc = None
        self.translations = []
        self.output = None
        self.out_name = None
        self.preview_in = ""
        self.preview_out = ""
        self.engine_used = set()
        self.chars = 0
        self.done = 0
        self.total = 0
        self.batches = 0
        self.cache_hits = 0
        self.failed = 0

    # -- accesseurs thread-safe -------------------------------------------- #
    def set(self, **kwargs):
        with self._lock:
            for key, value in kwargs.items():
                setattr(self, key, value)

    def warn(self, message):
        with self._lock:
            message = str(message)[:300]
            if message not in self.warnings:
                self.warnings.append(message)
                del self.warnings[:-12]

    def hit_cache(self):
        with self._lock:
            self.cache_hits += 1

    def fail(self, count=1):
        with self._lock:
            self.failed += count

    def progress(self, count=1):
        with self._lock:
            self.done = min(self.done + count, self.total) if self.total else self.done

    def public(self):
        with self._lock:
            elapsed = (self.finished or time.time()) - self.started
            rate = self.done / elapsed if elapsed > 0.25 and self.done else 0
            eta = (self.total - self.done) / rate if rate > 0.05 and self.total else None
            return {
                "id": self.id,
                "name": self.name,
                "state": self.state,
                "stage": self.stage,
                "error": self.error,
                "warnings": list(self.warnings[-4:]),
                "kind": self.doc.kind if self.doc else None,
                "kindLabel": self.doc.label if self.doc else None,
                "note": self.doc.note if self.doc else "",
                "src": self.src,
                "tgt": self.tgt,
                "total": self.total,
                "done": self.done,
                "chars": self.chars,
                "engines": sorted(self.engine_used),
                "cacheHits": self.cache_hits,
                "failed": self.failed,
                "elapsed": round(elapsed, 1),
                "eta": round(eta) if eta and eta > 0 else None,
                "hasOutput": bool(self.output),
                "outName": self.out_name,
                "size": len(self.blob),
                "outSize": len(self.output) if self.output else 0,
            }


class JobStore:
    def __init__(self):
        self.jobs = {}
        self.lock = threading.Lock()

    def put(self, job):
        with self.lock:
            self.jobs[job.id] = job
            self._gc()

    def get(self, job_id):
        with self.lock:
            return self.jobs.get(job_id)

    def _gc(self):
        now = time.time()
        for key in [k for k, v in self.jobs.items() if now - (v.finished or now) > JOB_TTL]:
            self.jobs.pop(key, None)
        while len(self.jobs) > MAX_JOBS:
            oldest = min(self.jobs.items(), key=lambda kv: kv[1].started)[0]
            self.jobs.pop(oldest, None)


STORE = JobStore()


# --------------------------------------------------------------------------- #
# exécution d'un lot, avec repli entre moteurs
# --------------------------------------------------------------------------- #


def translate_batch(chain, lines, src, tgt, job):
    """Traduit chaque ligne de `lines` : cache d'abord, puis chaque moteur en repli.

    Renvoie une liste alignée sur `lines` (la ligne d'origine en dernier recours).
    """
    cache = eng.CACHE
    results = [None] * len(lines)
    pending = []
    for i, text in enumerate(lines):
        hit = cache.get("shared", src, tgt, text)
        if hit is not None:
            results[i] = hit
            job.hit_cache()
            job.progress()
        else:
            pending.append(i)

    for engine in chain:
        if not pending:
            break
        if job.cancelled:
            raise EngineCancelled()
        payload = [lines[i] for i in pending]
        try:
            out = engine.translate_lines(payload, src, tgt)
        except eng.EngineError as exc:
            job.warn(f"{engine.label} : {exc}")
            continue
        except Exception as exc:  # noqa: BLE001
            job.warn(f"{engine.label} : {type(exc).__name__} {exc}")
            continue
        if len(out) != len(pending):
            job.warn(f"{engine.label} : désaccord de alignement ({len(out)}/{len(pending)}), moteur écarté")
            continue
        with job._lock:
            job.engine_used.add(engine.id)
        for i, text, tr in zip(pending, payload, out):
            value = (tr or "").strip() or text
            results[i] = value
            cache.put("shared", src, tgt, text, value)
            job.progress()
        pending = [i for i in pending if results[i] is None]

    if pending:
        job.fail(len(pending))
        for i in pending:
            results[i] = lines[i]
        job.warn(
            f"{len(pending)} passage(s) laissés dans la langue d'origine : moteurs indisponibles ou quota atteint."
        )
    return results


def run_job(job):
    try:
        doc = fmt.load(job.name, job.blob, job.options)
        job.doc = doc
        chain = [e for e in eng.build_engines(job.config) if e is not None]
        if not chain:
            raise fmt.Unsupported("Aucun moteur disponible : vérifiez les réglages.")
        active = chain[0]
        limit = active.max_chars
        if len(chain) > 1 and chain[1].max_chars < limit:
            limit = min(limit, chain[1].max_chars)

        # 1. unités → éléments de travail (morceaux sous la limite du moteur)
        slots, items = {}, []
        for idx in doc.todo:
            pieces = eng.split_long(doc.units[idx], limit) or [""]
            slots[idx] = [len(items) + k for k in range(len(pieces))]
            items.extend(pieces)
        if not items:
            raise fmt.Unsupported("Aucun texte à traduire dans ce fichier.")

        # 1 bis. sur les tout petits fichiers, le démon public LibreTranslate (3 requêtes
        # par minute) reste admis en dernier recours : pratique quand le quota gratuit
        # du moteur principal est épuisé.
        if len(items) < 6 and all(e.id != "libretranslate" for e in chain):
            demo = eng.LibreTranslateEngine(dict(job.config.get("libretranslate") or {}))
            if not (job.config.get("libretranslate") or {}).get("base_url"):
                demo.min_interval = 21.0
            chain = chain + [demo]

        # 2. langue source : détection locale, instantanée, sans quota
        src = job.src
        if src == "auto":
            src = eng.detect_language(" ".join(items[:80])[:3000])
            job.set(src=src)
        job.set(
            src=src, stage=f"traduction · {active.label}", total=len(items), done=0,
            chars=sum(len(t) for t in items),
        )

        # 3. traduction : lots traités en parallèle (le moteur limite lui-même)
        results = [None] * len(items)
        groups = eng.pack_pairs(list(enumerate(items)), active.max_chars, active.max_lines)
        batches = [[i for i, _ in group] for group in groups]
        job.set(batches=len(batches))
        workers = max(1, min(active.concurrency, len(batches), 6))
        leftovers = set(range(len(items)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(translate_batch, chain, [items[i] for i in batch], src, job.tgt, job): batch
                for batch in batches
            }
            for future in list(futures):
                try:
                    out = future.result()
                except EngineCancelled:
                    job.set(state="annule", stage="annulé", finished=time.time())
                    return
                except Exception as exc:  # noqa: BLE001
                    job.warn(f"lot ignoré : {type(exc).__name__} {exc}")
                    continue
                for i, value in zip(futures[future], out):
                    results[i] = value
                    leftovers.discard(i)

        # 4. repli : les lots en échec passent un par un, puis la chaîne complète
        if leftovers:
            for i in sorted(leftovers):
                if job.cancelled:
                    job.set(state="annule", stage="annulé", finished=time.time())
                    return
                out = translate_batch(chain, [items[i]], src, job.tgt, job)
                results[i] = out[0]

        # 5. rien n'est traduit : on le dit, plutôt que de rendre le fichier tel quel
        if job.total and job.failed >= job.total:
            detail = " · ".join(job.warnings[:2]) or "moteurs indisponibles"
            raise EngineExhausted("Aucun passage traduit — " + detail)

        # 6. recollage des morceaux puis reconstruction du fichier
        translations = []
        for idx in range(doc.unit_count):
            if doc.units[idx] is None:
                translations.append("")
                continue
            members = slots.get(idx, [])
            text = " ".join((results[i] or items[i]) for i in members).strip()
            translations.append(text or doc.units[idx])
        data, name, preview_in, preview_out = doc.rebuild(translations)
        job.set(
            translations=translations, output=data, out_name=name,
            preview_in=preview_in[:PREVIEW_LIMIT], preview_out=preview_out[:PREVIEW_LIMIT],
            state="termine", stage="prêt", finished=time.time(), done=job.total,
        )
    except EngineCancelled:
        job.set(state="annule", stage="annulé", finished=time.time())
    except EngineExhausted as exc:
        job.set(state="erreur", error=str(exc), finished=time.time())
    except fmt.Unsupported as exc:
        job.set(state="erreur", error=str(exc), finished=time.time())
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        job.set(state="erreur", error=f"{type(exc).__name__} : {exc}", finished=time.time())


# --------------------------------------------------------------------------- #
# catalogues pour l'interface
# --------------------------------------------------------------------------- #

ENGINE_FIELDS = {
    "mymemory": [{"name": "email", "label": "E-mail (quota quotidien élargi)", "type": "text",
                  "placeholder": "vous@exemple.com"}],
    "libretranslate": [
        {"name": "base_url", "label": "URL de l'instance", "type": "text",
         "placeholder": "https://translate.disroot.org"},
        {"name": "api_key", "label": "Clé d'API (optionnelle)", "type": "password"},
        {"name": "min_interval", "label": "Délai entre requêtes (s)", "type": "number", "placeholder": "1.0"},
    ],
    "deepl": [
        {"name": "api_key", "label": "Clé DeepL API", "type": "password"},
        {"name": "base_url", "label": "URL de l'API (auto si vide)", "type": "text",
         "placeholder": "https://api-free.deepl.com"},
    ],
    "llm": [
        {"name": "base_url", "label": "Point d'entrée compatible OpenAI", "type": "text",
         "placeholder": "https://api.openai.com/v1"},
        {"name": "api_key", "label": "Clé", "type": "password"},
        {"name": "model", "label": "Modèle", "type": "text", "placeholder": "gpt-4o-mini"},
        {"name": "temperature", "label": "Température", "type": "number", "placeholder": "0.2"},
    ],
}


def engine_catalog(config):
    out = [{
        "id": "auto", "label": "Automatique", "needsKey": False, "ready": True,
        "hint": "Le meilleur moteur configuré, avec bascule automatique en cas de quota atteint.",
        "fields": [],
    }]
    for eid, cls in (("deepl", eng.DeepLEngine), ("llm", eng.OpenAICompatEngine),
                     ("mymemory", eng.MyMemoryEngine), ("libretranslate", eng.LibreTranslateEngine)):
        engine = cls(dict((config or {}).get(eid) or {}))
        out.append({
            "id": eid, "label": engine.label, "hint": engine.hint,
            "needsKey": engine.needs_key, "ready": engine.ready(),
            "maxChars": engine.max_chars, "fields": ENGINE_FIELDS.get(eid, []),
        })
    return out


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #


class Handler(BaseHTTPRequestHandler):
    server_version = "TradFilez/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt_str, *args):
        if os.environ.get("PASSERELLE_VERBOSE"):
            super().log_message(fmt_str, *args)

    # -- utilitaires -------------------------------------------------------- #
    def _send(self, status, body=b"", ctype="application/octet-stream", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except BrokenPipeError:
                pass

    def _json(self, payload, status=200):
        self._send(status, json.dumps(payload, ensure_ascii=False), "application/json; charset=utf-8")

    def _meta(self, header):
        raw = self.headers.get(header)
        if not raw:
            return {}
        try:
            return json.loads(urllib.parse.unquote(raw))
        except Exception:  # noqa: BLE001
            return {}

    def _read_body(self, limit=MAX_UPLOAD):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if length <= 0:
            return b""
        if length > limit:
            # on vide la requête pour répondre proprement (sinon réinitialisation côté client)
            remaining, cap = length, limit + 4 * 1024 * 1024
            while remaining > 0 and cap > 0:
                chunk = self.rfile.read(min(65536, remaining, cap))
                if not chunk:
                    break
                remaining -= len(chunk)
                cap -= len(chunk)
            self.close_connection = True
            return None
        return self.rfile.read(length)

    # -- routage ------------------------------------------------------------ #
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            return self.serve_index()
        if path.startswith("/api/"):
            return self.api_get(path, urllib.parse.parse_qs(parsed.query))
        if path in ("/app.css", "/app.js"):
            name = path.lstrip("/")
            return self.serve_static(os.path.join(WEB, name), name)
        if path in ("/favicon.svg",):
            return self.serve_static(os.path.join(WEB, "favicon.svg"), "favicon.svg")
        return self._send(404, "Introuvable", "text/plain; charset=utf-8")

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/api/"):
            return self.api_post(path, urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query))
        return self._send(404, "Introuvable", "text/plain; charset=utf-8")

    # -- statique ----------------------------------------------------------- #
    def serve_static(self, full, name):
        try:
            with open(full, "rb") as fh:
                data = fh.read()
        except OSError:
            return self._send(404, "Introuvable", "text/plain; charset=utf-8")
        ctype = ("text/css; charset=utf-8" if name.endswith(".css")
                 else "text/javascript; charset=utf-8" if name.endswith(".js")
                 else "image/svg+xml")
        return self._send(200, data, ctype)

    def serve_index(self):
        try:
            with open(os.path.join(WEB, "index.html"), encoding="utf-8") as fh:
                html = fh.read()
            with open(os.path.join(WEB, "app.css"), encoding="utf-8") as fh:
                css = fh.read()
            with open(os.path.join(WEB, "app.js"), encoding="utf-8") as fh:
                js = fh.read()
        except OSError as exc:
            return self._send(500, f"Fichiers d'interface manquants : {exc}", "text/plain; charset=utf-8")
        html = html.replace("<!--CSS-->", "<style>\n" + css + "\n</style>")
        html = html.replace("<!--JS-->", "<script>\n" + js + "\n</script>")
        html = html.replace("<!--META-->", f'<meta name="description" content="Traduction de fichiers, sobre et locale.">')
        return self._send(200, html, "text/html; charset=utf-8")

    # -- API ---------------------------------------------------------------- #
    def api_get(self, path, query):
        if path == "/api/meta":
            return self._json({
                "engines": engine_catalog(self._meta("X-Config")),
                "cache": eng.CACHE.stats(),
                "recent": eng.CACHE.recent(8),
                "limits": {"maxUpload": MAX_UPLOAD, "preview": PREVIEW_LIMIT},
                "mode": "stateless" if STATELESS else "files",
                "time": time.time(),
            })
        if path.startswith("/api/jobs/"):
            rest = path[len("/api/jobs/"):]
            job = STORE.get(rest.split("/")[0])
            if not job:
                return self._json({"error": "Tâche inconnue ou expirée."}, 404)
            if rest.endswith("/file"):
                if not job.output:
                    return self._json({"error": "Traduction non terminée."}, 409)
                quoted = urllib.parse.quote(job.out_name or "traduction.txt")
                return self._send(
                    200, job.output,
                    mimetypes.guess_type(job.out_name or "") [0] or "application/octet-stream",
                    {"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"},
                )
            if rest.endswith("/result"):
                return self._json({
                    "id": job.id, "name": job.name, "src": job.src, "tgt": job.tgt,
                    "kind": job.doc.kind if job.doc else None,
                    "kindLabel": job.doc.label if job.doc else None,
                    "note": job.doc.note if job.doc else "",
                    "in": job.preview_in, "out": job.preview_out,
                    "outName": job.out_name, "size": len(job.output or b""),
                })
            return self._json(job.public())
        if path == "/api/zip":
            ids = [i for i in (query.get("ids") or [""])[0].split(",") if i]
            buf = io.BytesIO()
            used = set()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for job_id in ids:
                    job = STORE.get(job_id)
                    if not job or not job.output:
                        continue
                    root, ext = os.path.splitext(job.out_name or "traduction.txt")
                    name, n = job.out_name, 1
                    while name in used:
                        name = f"{root}-{n}{ext}"
                        n += 1
                    used.add(name)
                    zf.writestr(name, job.output)
            return self._send(200, buf.getvalue(), "application/zip",
                              {"Content-Disposition": "attachment; filename*=UTF-8''tradfilez-traductions.zip"})
        return self._json({"error": "Route inconnue."}, 404)

    def api_stateless(self, path):
        """open / translate / build : tout passe par un corps JSON, rien ne reste ici."""
        body = self._read_body(MAX_UPLOAD * 2 + 65536)
        if body is None:
            return self._json({"error": f"Trop lourd : limite {MAX_UPLOAD // (1024 * 1024)} Mo."}, 413)
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except Exception:  # noqa: BLE001
            return self._json({"error": "Corps JSON illisible."}, 400)
        meta = payload.get("meta") or {}
        blob = b""
        if payload.get("file"):
            try:
                blob = base64.b64decode(payload["file"])
            except Exception:  # noqa: BLE001
                return self._json({"error": "Fichier mal encodé (base64)."}, 400)
            if len(blob) > MAX_UPLOAD:
                return self._json({"error": f"Fichier trop lourd : limite {MAX_UPLOAD // (1024 * 1024)} Mo."}, 413)
        try:
            if path == "/api/open":
                out = stl.open_file(meta, blob)
            elif path == "/api/translate":
                out = stl.translate(meta, payload.get("items") or [])
            else:
                out = stl.build(meta, blob, payload.get("translations") or {})
        except stl.StatelessError as exc:
            return self._json({"error": str(exc)}, 400)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return self._json({"error": f"{type(exc).__name__} : {exc}"}, 500)
        return self._json(out)

    def api_post(self, path, query):
        if path in ("/api/open", "/api/translate", "/api/build"):
            return self.api_stateless(path)
        if path == "/api/jobs":
            return self.create_job()
        if path.startswith("/api/jobs/") and path.endswith("/cancel"):
            job = STORE.get(path[len("/api/jobs/"):-len("/cancel")].strip("/"))
            if not job:
                return self._json({"error": "Tâche inconnue."}, 404)
            job.cancelled = True
            return self._json({"ok": True})
        if path == "/api/engine/probe":
            body = self._read_body(65536) or b"{}"
            try:
                config = json.loads(body.decode("utf-8") or "{}")
            except Exception:  # noqa: BLE001
                config = {}
            chain = eng.build_engines(config)
            primary = chain[0]
            result = primary.probe()
            result.update({"engine": primary.id, "label": primary.label})
            if not result.get("ok") and len(chain) > 1:
                alt = chain[1].probe()
                alt.update({"engine": chain[1].id, "label": chain[1].label, "note": "repli"})
                result["fallback"] = alt
            return self._json(result)
        if path == "/api/cache/clear":
            eng.CACHE.clear()
            return self._json({"ok": True, "cache": eng.CACHE.stats()})
        if path == "/api/detect":
            body = self._read_body(200000) or b""
            code = eng.detect_language(body.decode("utf-8", "replace"))
            return self._json({"lang": code, "label": eng.lang_label(code)})
        return self._json({"error": "Route inconnue."}, 404)

    def create_job(self):
        meta = self._meta("X-Meta")
        filename = str(meta.get("filename") or self.headers.get("X-Filename") or "document.txt")
        filename = re.sub(r"[/\\\x00-\x1f\"<>|:*?]", "_", filename).strip()[:180] or "document.txt"
        body = self._read_body()
        if body is None:
            return self._json({"error": f"Fichier trop lourd : limite {MAX_UPLOAD // (1024 * 1024)} Mo."}, 413)
        if not body:
            return self._json({"error": "Contenu vide."}, 400)
        job = Job(
            filename, body,
            src=meta.get("src", "auto"), tgt=meta.get("tgt", "fr"),
            options=meta.get("options") or {}, config=meta.get("config") or {},
        )
        STORE.put(job)
        threading.Thread(target=run_job, args=(job,), daemon=True).start()
        return self._json({"id": job.id, "job": job.public()}, 202)


class Server(ThreadingHTTPServer):
    """Serveur qui ne pleurniche pas quand un navigateur coupe la connexion."""

    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):  # noqa: D102
        import sys

        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError, TimeoutError)):
            return
        traceback.print_exc()


def main():
    parser = argparse.ArgumentParser(description="TradFilez — traduction de fichiers")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    httpd = Server((args.host, args.port), Handler)
    print(f"TradFilez à l'écoute sur http://{args.host}:{args.port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
