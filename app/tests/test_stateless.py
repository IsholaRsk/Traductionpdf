#!/usr/bin/env python3
"""Tests de l'API sans état (`/api/open`, `/api/translate`, `/api/build`).

C'est le chemin qu'emprunte le site une fois déployé chez un hébergeur éphémère :
le navigateur tient la file, le serveur ne retient rien entre deux requêtes.
On vérifie donc surtout que trois appels séparés rendent EXACTEMENT le même
fichier qu'un job complet, et que les garde-fous répondent proprement.

    python3 server.py --port 8011 &     # (PASSERELLE_STATELESS=1 pour le client)
    python3 tests/test_stateless.py [http://127.0.0.1:8011]
"""

from __future__ import annotations

import base64
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8011").rstrip("/")
failures = []
checks = 0


def check(name, ok, detail=""):
    global checks
    checks += 1
    print(("  ✓ " if ok else "  ✗ ") + name + ("" if ok else f"  → {str(detail)[:200]}"))
    if not ok:
        failures.append(name)


def post(path, payload):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(raw)
        except Exception:  # noqa: BLE001
            return exc.code, {"error": raw[:200]}


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def job(filename, blob, src, tgt, config=None):
    """Le chemin « file d'attente », pour comparer."""
    meta = urllib.parse.quote(json.dumps({
        "filename": filename, "src": src, "tgt": tgt, "options": {},
        "config": config or {"engine": "mymemory", "mymemory": {}},
    }))
    req = urllib.request.Request(BASE + "/api/jobs", data=blob, method="POST", headers={"X-Meta": meta})
    jid = json.loads(urllib.request.urlopen(req, timeout=60).read())["id"]
    import time
    for _ in range(240):
        j = json.loads(urllib.request.urlopen(BASE + f"/api/jobs/{jid}", timeout=30).read())
        if j["state"] in ("termine", "erreur", "annule"):
            return jid, j
        time.sleep(0.5)
    return jid, {"state": "attente"}


TEXTE = (
    "Le port de Kerlouan ferme à vingt heures.\n\n"
    "Le gardien allume le phare avant la nuit.\n\n"
    "Les bateaux rentrent quand le vent tourne, et la cale attend."
)
META = {"filename": "port.txt", "src": "fr", "tgt": "en", "options": {},
        "config": {"engine": "mymemory", "mymemory": {}}}
B64 = base64.b64encode(TEXTE.encode("utf-8")).decode("ascii")

print(f"API sans état sur {BASE}")

print("· annonce du serveur")
meta_srv = get("/api/meta")
check("une route inconnue renvoie un 404 propre", post("/api/inconnu", {})[0] == 404, post("/api/inconnu", {}))
check("limite de corps de requête annoncée à l'interface", meta_srv["limits"]["maxUpload"] > 1024, meta_srv["limits"])
check("le mode est indiqué", meta_srv.get("mode") in ("files", "stateless"), meta_srv.get("mode"))

print("\n· open")
status, plan = post("/api/open", {"meta": META, "file": B64})
check("réponse 200", status == 200, plan)
check("les unités traduisibles sont comptées", plan.get("count") == 3, plan.get("count"))
check("le découpage en lots est proposé au client", plan.get("batches", 0) >= 1 and "window" in plan, plan.get("window"))
check("la source est résolue", plan.get("src") == "fr", plan.get("src"))
check("les textes à renvoyer sont listés", isinstance(plan.get("pending"), list) and all("i" in p and "t" in p for p in plan["pending"]), str(plan.get("pending"))[:80])

print("\n· translate (moteur réel)")
items = [p["t"] for p in plan["pending"]]
status, tr = post("/api/translate", {"meta": META, "items": items}) if items else (200, {"translations": []})
check("réponse 200", status == 200, tr)
check("un résultat par segment envoyé", len(tr.get("translations", [])) == len(items), f"{len(tr.get('translations', []))}/{len(items)}")
check("le texte a changé de langue", all(t != s for t, s in zip(tr.get("translations", []), items)) or not items, tr)
check("le moteur rendu est nommé", bool(tr.get("engine")) or not items, tr.get("engine"))
check("aucun retour à la ligne dans une unité", all("\n" not in t for t in tr.get("translations", [])), tr.get("translations"))

print("\n· build")
trans = dict(plan.get("cached") or {})
for n, v in enumerate(tr.get("translations", [])):
    trans[str(plan["pending"][n]["i"])] = v
status, built = post("/api/build", {"meta": META, "file": B64, "translations": trans})
check("réponse 200", status == 200, built)
out = base64.b64decode(built["b64"]).decode("utf-8") if "b64" in built else ""
check("le fichier rendu porte le nom d’origine", built.get("name") == "port.txt", built.get("name"))
check("la structure en trois blocs est conservée", len([b for b in out.split("\n\n") if b.strip()]) == 3, repr(out[:120]))
check("l'aperçu collé au fichier est fourni", built.get("out") and built.get("in"), str(built)[:120])

print("\n· parité avec la file d'attente")
jid, jobdone = job("port.txt", TEXTE.encode("utf-8"), "fr", "en")
check("le job complet termine", jobdone.get("state") == "termine", jobdone.get("state") or jobdone.get("error"))
if jobdone.get("state") == "termine":
    from_job = urllib.request.urlopen(BASE + f"/api/jobs/{jid}/file", timeout=60).read().decode("utf-8")
    check("mêmes octets des deux côtés", from_job.strip() == out.strip(), f"\n   job : {from_job.strip()[:90]!r}\n   sans état : {out.strip()[:90]!r}")

print("\n· garde-fous")
status, err = post("/api/open", {"meta": {"filename": "x.doc", "tgt": "en"}, "file": base64.b64encode(b"PK\x03\x04").decode()})
check("format refusé → 400 avec message", status == 400 and "docx" in err.get("error", ""), err)
status, err = post("/api/open", {"meta": {"filename": "x.txt", "tgt": ""}, "file": base64.b64encode(b"bonjour").decode()})
check("langue cible manquante → 400", status == 400 and "cible" in err.get("error", ""), err)
status, err = post("/api/open", {"meta": {"filename": "v.txt", "tgt": "en"}, "file": base64.b64encode(b"\x00\x01\x02").decode()})
check("binaire non pris en charge → 400", status == 400, err)
status, err = post("/api/translate", {"meta": META, "items": ["a"] * 500})
check("trop de segments d'un coup → 400", status == 400 and "segments" in err.get("error", ""), err)
status, err = post("/api/translate", {"meta": META, "items": []})
check("lot vide → réponse neutre", status == 200 and err.get("translations") == [], err)
status, err = post("/api/build", {"meta": META, "file": "pas-du-base64!!", "translations": {}})
check("fichier mal encodé → 400", status == 400, err)
status, err = post("/api/open", {"meta": META, "file": base64.b64encode(b"x" * (meta_srv["limits"]["maxUpload"] + 10)).decode()})
check("fichier trop lourd → 413", status == 413, err)

print("\n· un fichier rendu sans traduction est refusé, pas livré tel quel")
_T = base64.b64encode("Premier paragraphe.\n\nDeuxième paragraphe.\n".encode()).decode()
_M = {"filename": "garde.txt", "src": "fr", "tgt": "en", "config": {}, "options": {}}
status, err = post("/api/build", {"meta": _M, "file": _T, "translations": {}})
check("aucune traduction reçue → 400 expliqué",
      status == 400 and "traduction" in str(err.get("error", "")).lower(), err)
status, b = post("/api/build", {"meta": _M, "file": _T, "translations": {"0": "First paragraph."}})
check("partiel : les manquants sont comptés et renvoyés",
      status == 200 and b.get("missing") == 1 and b.get("total") == 2,
      b if status != 200 else (b.get("missing"), b.get("total")))
check("partiel : le passage manquant reste écrit, aucun trou",
      status == 200 and "Deuxi" in b.get("out", ""), b.get("out", "")[:80])

print("\n· rien n'est retenu entre deux appels")
plan2 = get("/api/meta")
check("aucune file de tâches côté serveur n'est nécessaire (cache seul)", "jobs" not in json.dumps(plan2), "surveillances")
status, again = post("/api/open", {"meta": META, "file": B64})
check("le même fichier rouvre à l'identique", again.get("count") == plan["count"], again.get("count"))

print("\n· entrées mal formées : un message clair, pas une exception qui fuit")
status, mauvais = post("/api/translate", {"meta": META, "items": [{"t": "Bonjour"}]})
check("un lot d'objets au lieu de textes est refusé", status == 400, (status, mauvais))
check("  et le message dit quoi envoyer", "liste de textes" in (mauvais.get("error") or ""), mauvais)

print("\n" + (f"ÉCHECS {len(failures)}/{checks} : " + ", ".join(failures) if failures else f"tout est bon ({checks} vérifications)"))
sys.exit(1 if failures else 0)
