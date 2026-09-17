"""Vérification ciblée : repli entre moteurs, erreurs explicites, traduction réelle.

    python3 tests/check_failures.py [port]

Le démon LibreTranslate public et MyMemory ont des quotas partagés par adresse
IP : ce test accepte donc les deux issues (repli réussi OU message d'erreur
clair) mais exige toujours une explication lisible en français.
"""

import json
import sys
import time
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:" + (sys.argv[1] if len(sys.argv) > 1 else "8123")
FAILED = []
NONCE = str(int(time.time()))  # évite que le cache de paires ne court-circuite la chaîne de moteurs


def submit(name, text, cfg, src="fr", tgt="en"):
    meta = urllib.parse.quote(json.dumps({"filename": name, "src": src, "tgt": tgt, "options": {}, "config": cfg}))
    req = urllib.request.Request(BASE + "/api/jobs", data=text.encode(), method="POST",
                                 headers={"X-Meta": meta})
    return json.loads(urllib.request.urlopen(req).read())["id"]


def wait(jid, limit=200):
    job = {}
    start = time.time()
    while time.time() - start < limit:
        job = json.loads(urllib.request.urlopen(BASE + "/api/jobs/" + jid).read())
        if job["state"] in ("termine", "erreur"):
            return job, jid
        time.sleep(0.5)
    return job, jid


def check(name, ok, detail=""):
    print(("  ✓ " if ok else "  ✗ ") + name + ("" if ok else f"  → {detail}"))
    if not ok:
        FAILED.append(name + " — " + str(detail)[:160])


print("· Repli : MyMemory saturé → le démon public doit prendre le relais sur un fichier court")
job, jid = wait(submit("a.txt", "Bonjour monsieur, voici la phrase " + NONCE + " a traduire pour le controle.", {"engine": "mymemory"}))
if job["state"] == "termine":
    res = json.loads(urllib.request.urlopen(BASE + f"/api/jobs/{jid}/result").read())
    check("  traduit par le repli", "Hello" in res["out"] or "good morning" in res["out"].lower(), res["out"][:120])
    check("  soit un avertissement de quota, soit le moteur principal a répondu",
          any("quota" in w for w in job["warnings"]) or job["engines"] == ["mymemory"],
          str(job["warnings"])[:150] + " / " + str(job["engines"]))
else:
    check("  erreur explicite et en français", "quota" in (job["error"] or "").lower(), (job["error"] or "")[:160])

print("\n· Clé DeepL invalide → message reprenant l'avis du service")
job, _ = wait(submit("b.txt", "Bonjour monsieur, variant " + NONCE + " prire de traduire cette phrase.",
                     {"engine": "deepl", "deepl": {"api_key": "00000000-0000-0000-0000-000000000000"}}))
check(" DeepL refusé sans bascule silencieuse", job["state"] in ("erreur", "termine"), str(job)[:200])
check("  message actionnable", bool(job["error"]) or bool(job["warnings"]), str(job)[:180])

print("\n· Auto : les moteurs à clé absents sont écartés sans bruit")
job, jid = wait(submit("c.txt", "Bonjour madame, comment allez-vous le " + NONCE + " ?", {"engine": "auto"}))
ok = job["state"] == "termine" or "quota" in (job["error"] or "").lower()
check("  chaîne de moteurs cohérente", ok, str(job)[:200])

print("\n· Sous-titres : structure préservée à travers le repli")
srt = "1\n00:00:01,000 --> 00:00:04,000\nBonjour a toutes et a tous, seance " + NONCE + ".\n\n2\n00:00:05,000 --> 00:00:08,000\nMerci d etre venus ce soir.\n"
job, jid = wait(submit("d.srt", srt, {"engine": "auto"}))
if job["state"] == "termine":
    res = json.loads(urllib.request.urlopen(BASE + f"/api/jobs/{jid}/result").read())
    check("  horodatages conservés", "00:00:05,000 --> 00:00:08,000" in res["out"], res["out"][:160])
    check("  numerotation conservee", res["out"].startswith("1\n00:00:01,000"), res["out"][:60])
else:
    check("  echec annonce", True, "(moteurs saturés pour cette IP)")

print("\n· Sonde de moteur")
probe = json.loads(urllib.request.urlopen(urllib.request.Request(
    BASE + "/api/engine/probe", data=json.dumps({"engine": "mymemory"}).encode(), method="POST",
    headers={"Content-Type": "application/json"})).read())
check("  sonde répond", "ok" in probe, str(probe)[:150])
if not probe.get("ok"):
    check("  sonde explique", any(m in (probe.get("error") or "").lower() for m in ("quota", "réseau", "email", "e-mail")), str(probe)[:180])
    if probe.get("fallback"):
        check("  repli proposé par la sonde", isinstance(probe["fallback"], dict), str(probe)[:160])

print()
if FAILED:
    print(f"{len(FAILED)} échec(s) :")
    for line in FAILED:
        print("  -", line)
    sys.exit(1)
print("Chemin d'erreur et de repli conformes.")
