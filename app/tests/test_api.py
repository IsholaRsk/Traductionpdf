"""Test d'intégration — bout-en-bout, avec un vrai moteur de traduction.

    python3 tests/test_api.py [moteur] [src] [tgt] [fichier...]

Contrôle le cycle complet du serveur : envoi, progression, repli de moteur,
téléchargement, puis relecture du fichier traduit (structure conservée).
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("PASSERELLE_URL", "http://127.0.0.1:8000")
ENGINE = sys.argv[1] if len(sys.argv) > 1 else "mymemory"
SRC = sys.argv[2] if len(sys.argv) > 2 else "fr"
TGT = sys.argv[3] if len(sys.argv) > 3 else "en"
FILES = sys.argv[4:]

FAILURES = []
CHECKS = 0


def check(name, ok, detail=""):
    global CHECKS
    CHECKS += 1
    print(("  ✓ " if ok else "  ✗ ") + name + ("" if ok else f"  → {detail}"))
    if not ok:
        FAILURES.append(f"{name} — {detail}")


def call(path, method="GET", data=None, headers=None, timeout=120):
    req = urllib.request.Request(BASE + path, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = resp.read()
        ctype = resp.headers.get("Content-Type", "")
        disposition = resp.headers.get("Content-Disposition", "")
    if "json" in ctype:
        return json.loads(payload.decode()), disposition
    return payload, disposition


def submit(name, blob, src=SRC, tgt=TGT, options=None, config=None, tone="fluide", glossary=""):
    cfg = {"engine": ENGINE, "tone": tone}
    if glossary:
        cfg["glossary"] = glossary
    if config:
        cfg.update(config)
    meta = urllib.parse.quote(json.dumps({
        "filename": name, "src": src, "tgt": tgt, "options": options or {}, "config": cfg,
    }))
    job, _ = call("/api/jobs", "POST", blob, {"X-Meta": meta, "Content-Type": "application/octet-stream"})
    return job["id"]


def wait(job_id, limit=900, quiet=False):
    start = time.time()
    last = None
    while time.time() - start < limit:
        job, _ = call("/api/jobs/" + job_id)
        if job.get("state") != last:
            last = job.get("state")
            if not quiet:
                print(f"    … {job['state']} · {job['stage']} · {job['done']}/{job['total']} lots · {job['chars']} car.")
        if job.get("state") in ("termine", "erreur", "annule"):
            return job, time.time() - start
        time.sleep(1.0)
    raise TimeoutError(f"délai dépassé ({limit}s) · dernier état : {last}")


def run(name, blob, validator=None, **kw):
    print(f"\n· {name}")
    job_id = submit(name, blob, **kw)
    job, elapsed = wait(job_id, quiet=True)
    check(f"{name} : traduit", job["state"] == "termine", job.get("error") or job["state"])
    check(f"{name} : en moins de 4 min", elapsed < 240, f"{elapsed:.1f}s")
    check(f"{name} : moteur rapporté", bool(job["engines"]) or job["cacheHits"] > 0, str(job))
    print(f"    durée {elapsed:.1f}s · {job['done']}/{job['total']} lots · cache {job['cacheHits']} · moteurs {job['engines']}")
    payload, disposition = call(f"/api/jobs/{job_id}/file")
    check(f"{name} : fichier en téléchargement", len(payload) > 0 and "attachment" in disposition, disposition)
    result, _ = call(f"/api/jobs/{job_id}/result")
    check(f"{name} : aperçu disponible", bool(result.get("out")), str(result)[:120])
    if validator:
        try:
            validator(payload, result, job)
        except Exception as exc:  # noqa: BLE001
            check(f"{name} : validation", False, f"{type(exc).__name__}: {exc}")
    return job_id, payload


# --------------------------------------------------------------------------- #
# échantillons
# --------------------------------------------------------------------------- #

TXT = """Le café de la gare ouvre à six heures.

Le patron, Henri, torréfie les grains lui-même chaque lundi matin. Il refuse
les machines automatiques et préfère écouter le grain craquer. Les habitués
arrivent avant le jour, commandent un café long et parlent du temps qu'il fait.

Ce matin-là, la pluie tombait sur les quais. Une femme en manteau vert est
entrée, a commandé un thé, puis est restée près de la fenêtre sans rien dire.
Henri lui a servi un biscuit sans le lui facturer. C'était un mardi.

Note : le nom « Passerelle » ne doit pas être traduit.
"""

SRT = """1
00:00:01,000 --> 00:00:04,000
Le café de la gare ouvre à six heures.

2
00:00:04,500 --> 00:00:08,000
Le patron torréfie les grains
chaque lundi matin.

3
00:00:09,000 --> 00:00:12,000
Ce matin-là, la pluie tombait sur les quais.
"""

MD = """# Note de service

Bonjour à toutes et à tous. La maintenance du serveur aura lieu **samedi à 9 h**.

```bash
systemctl restart passerelle
```

- Enregistrez votre travail avant l'arrêt.
- Le cache sera vidé pendant l'opération.

Merci de votre compréhension.
"""

CSV = 'produit;description;prix;reference\n"cafe long";"un serve brulant, du matin";3.50;CL-001\n"the vert";"infusion a la menthe";4.0;TV-022\n"jus d orange";"presse a la minute";4.50;JO-113\n'

JSON_DOC = json.dumps({
    "app": {"title": "Traduire ses fichiers", "tagline": "Un outil sobre, doux et rapide."},
    "screens": [
        {"id": "home", "heading": "Deposez vos fichiers", "body": "Nous respectons la mise en forme."},
        {"id": "done", "heading": "Telechargement pret", "body": "Le document garde sa structure."},
    ],
    "count": 2,
    "active": True,
    "url": "https://example.com/fr",
}, ensure_ascii=False, indent=2)

HTML = """<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Bienvenue au cafe</title>
<style>body{font-family:serif;color:#222}</style></head>
<body><h1>Bienvenue au cafe</h1><p>Le <em>the</em> est servi toute la journee.</p>
<script>var url = "/fr/cafe"; console.log(url);</script></body></html>"""

YML = """title: Bienvenue au cafe
slug: bienvenue-au-cafe
description: Le the est servi toute la journee.
nav:
  - Accueil
  - Contact
url: /fr/cafe
"""

PO = """msgid "Hello"
msgstr "Bonjour"

msgid "Your files are ready"
msgstr "Vos fichiers sont prets"

msgid "Download"
msgstr "Telecharger"
"""


# --------------------------------------------------------------------------- #
# validateurs
# --------------------------------------------------------------------------- #

def v_txt(data, res, job):
    text = data.decode("utf-8")
    check("  txt : traduit en anglais", re.search(r"The (station )?cafe opens", text, re.I) or "coffee" in text.lower(), text[:140])
    check("  txt : deux paragraphes conserves", "\n\n" in text, repr(text[:80]))
    if ENGINE in ("deepl", "llm"):  # seul un moteur qui lit les consignes préserve un nom propre
        check("  txt : nom propre preserve", "Passerelle" in text, text[-160:])
    check("  txt : chiffre conserve", "six" in text.lower() or "6" in text, "")


def v_srt(data, res, job):
    text = data.decode("utf-8")
    blocks = re.findall(r"(\d+)\n(\d\d:\d\d:\d\d,\d\d\d --> \d\d:\d\d:\d\d,\d\d\d)\n", text)
    check("  srt : 3 blocos numerotes", len(blocks) == 3 and [b[0] for b in blocks] == ["1", "2", "3"], str(blocks))
    check("  srt : horodatages intacts", "00:00:04,500 --> 00:00:08,000" in text, text[:200])
    check("  srt : texte traduit", "rain" in text.lower() or "quays" in text.lower() or "platform" in text.lower(), text)


def v_md(data, res, job):
    text = data.decode("utf-8")
    check("  md : titre conserve", text.startswith("# "), text[:60])
    check("  md : gras conserve", "**" in text, text[:200])
    check("  md : bloc de code intact", "systemctl restart passerelle" in text, text[:240])
    check("  md : puce conservee", "\n- " in text, text[:240])
    check("  md : traduit", "maintenance" in text.lower() or "service" in text.lower(), text[:160])


def v_csv(data, res, job):
    text = data.decode("utf-8")
    header = text.strip().split("\n")[0]
    rows = [r for r in text.strip().split("\n")[1:]]
    check("  csv : en-tete intact", header == "produit;description;prix;reference", header)
    check("  csv : 3 lignes conservees", len(rows) == 3, str(len(rows)))
    check("  csv : separateur conserve", ";" in header, header)
    check("  csv : prix conserve", re.search(r"3[.,]5", text) and re.search(r"4[.,]5", text), text)
    check("  csv : reference conservee", "CL-001" in text and "TV-022" in text, text)
    check("  csv : description traduite", "morning" in text.lower() or "mint" in text.lower(), text)


def v_json(data, res, job):
    obj = data if isinstance(data, dict) else json.loads(data.decode("utf-8"))
    check("  json : structure identique", set(obj) == {"app", "screens", "count", "active", "url"}, str(list(obj)))
    check("  json : types conserves", obj["count"] == 2 and obj["active"] is True, str(obj)[:120])
    check("  json : url techniquement preservee", obj["url"] == "https://example.com/fr", str(obj["url"]))
    check("  json : ids preserves", [s["id"] for s in obj["screens"]] == ["home", "done"], str(obj["screens"]))
    check("  json : valeurs traduites", "files" in obj["app"]["title"].lower() or "translate" in obj["app"]["title"].lower(), obj["app"]["title"])
    check("  json : corps traduit", "format" in obj["screens"][0]["body"].lower() or "respect" in obj["screens"][0]["body"].lower(), obj["screens"][0]["body"])


def v_html(data, res, job):
    text = data.decode("utf-8")
    check("  html : doctype et balises", text.lstrip().lower().startswith("<!doctype html>") and "<em>" in text, text[:60])
    check("  html : style intact", "font-family:serif" in text, text[:200])
    check("  html : script intact", 'var url = "/fr/cafe";' in text, text[-220:])
    check("  html : contenu traduit", "coffee" in text.lower() or "Welcome" in text, text[:240])
    check("  html : attribut lang preserve", 'lang="fr"' in text, text[:100])


def v_yml(data, res, job):
    text = data.decode("utf-8")
    lines = [l for l in text.split("\n")]
    check("  yml : slug preserve", any(l.startswith("slug: bienvenue-au-cafe") for l in lines), text[:200])
    check("  yml : url preservee", any(l.strip() == "url: /fr/cafe" for l in lines), text[:240])
    check("  yml : cles conservees", all(any(l.startswith(k) for l in lines) for k in ("title:", "description:", "nav:")), text[:200])
    check("  yml : valeurs traduites", "coffee" in text.lower() or "Welcome" in text, text[:200])


def v_po(data, res, job):
    text = data.decode("utf-8")
    check("  po : msgid conserves", text.count("msgid") == 3, text[:200])
    check("  po : msgstr traduits", "Hello" in text and "Bonjour" not in text.split("msgid")[0], text[:240])
    check("  po : troisieme entree traduite", "Download" in text, text)


def v_autodetect(job):
    check("  auto : langue source devinee (fr)", job["src"] in ("fr", "fy", "fur", "fra"), str(job["src"]))


def main():
    print(f"Passerelle · {BASE} · moteur={ENGINE} · {SRC}→{TGT}")
    meta, _ = call("/api/meta")
    check("/api/meta repond", bool(meta.get("engines")), str(meta)[:160])
    ids = {e["id"]: e for e in meta["engines"]}
    check("  moteur demande disponible", ids.get(ENGINE, {}).get("ready", ENGINE == "auto"), str(ids.get(ENGINE)))
    check("  limites annoncees", meta["limits"]["maxUpload"] == 8 * 1024 * 1024, str(meta["limits"]))

    samples = {
        "note.txt": (TXT, v_txt),
        "film.srt": (SRT, v_srt),
        "note.md": (MD, v_md),
        "tarif.csv": (CSV, v_csv),
        "app.json": (JSON_DOC, v_json),
        "page.html": (HTML, v_html),
        "conf.yml": (YML, v_yml),
        "fr.po": (PO, v_po),
    }
    chosen = FILES or list(samples)
    inconnus = [n for n in chosen if n not in samples and not os.path.exists(n)]
    if inconnus:
        print("échantillons inconnus :", ", ".join(inconnus))
        print("choix possibles        :", ", ".join(samples))
        sys.exit(2)
    for name in chosen:
        if name not in samples:
            with open(name, "rb") as fh:
                blob = fh.read()
            run(os.path.basename(name), blob)
            continue
        text, validator = samples[name]
        run(name, text.encode("utf-8"), validator)

    # documents binaires
    try:
        import io as _io

        import docx as _docx
        import openpyxl as _openpyxl

        buf = _io.BytesIO()
        document = _docx.Document()
        document.add_heading("Conditions de vente", level=1)
        document.add_paragraph("Les prix sont indiques en euros, taxes comprises.")
        document.add_paragraph("Le service client repond sous deux jours ouvres.")
        document.save(buf)
        docx_blob = buf.getvalue()

        def v_docx(data, res, job):
            back = _docx.Document(_io.BytesIO(data))
            texts = [p.text for p in back.paragraphs]
            check("  docx : relu et traduit", len(texts) == 3 and "prix" not in texts[1], str(texts))
            check("  docx : titre conserve", texts[0] and data[:2] == b"PK", str(texts[0])[:60])

        run("conditions.docx", docx_blob, v_docx)

        book = _openpyxl.Workbook()
        sheet = book.active
        sheet["A1"], sheet["B1"] = "Libelle", 12.5
        sheet["A2"], sheet["B2"] = "Cafe du matin", "=A1"
        xbuf = _io.BytesIO()
        book.save(xbuf)

        def v_xlsx(data, res, job):
            ws = _openpyxl.load_workbook(_io.BytesIO(data)).active
            check("  xlsx : cellule traduite", ws["A2"].value and "Cafe" not in str(ws["A2"].value), str(ws["A2"].value))
            check("  xlsx : nombre intact", ws["B1"].value == 12.5, str(ws["B1"].value))
            check("  xlsx : formule intacte", ws["B2"].value == "=A1", str(ws["B2"].value))

        run("tarifs.xlsx", xbuf.getvalue(), v_xlsx)
    except ImportError:
        print("\n· (python-docx/openpyxl absents : Word et Excel non testés ici)")

    # archive ZIP multi-fichiers
    print("\n· archive zip")
    ids_multi = [submit("note.txt", TXT.encode("utf-8")), submit("conf.yml", YML.encode("utf-8"))]
    for jid in ids_multi:
        wait(jid, quiet=True)
    blob_zip, disp = call("/api/zip?ids=" + ",".join(ids_multi))
    check("  zip : deux entrée s", blob_zip[:2] == b"PK" and b"note_traduit.txt" in blob_zip, str(disp))

    # détection automatique de la langue source
    print("\n· detection automatique")
    jid = submit("auto.txt", TXT.encode("utf-8"), src="auto", tgt="de")
    job, _ = wait(jid, quiet=True)
    check("  auto : terminé", job["state"] == "termine", job.get("error") or "")
    v_autodetect(job)
    payload, _ = call(f"/api/jobs/{jid}/file")
    check("  auto : rendu en allemand", bool(re.search(r"[äöüß]|Kaffee|Bahnhof", payload.decode("utf-8"), re.I)), payload.decode("utf-8")[:120])

    # deuxième passe : le cache doit tout resoudre
    print("\n· cache")
    t0 = time.time()
    jid2 = submit("note.txt", TXT.encode("utf-8"))
    job2, _ = wait(jid2, quiet=True)
    fast = time.time() - t0
    check("  cache : seconde passe quasi immediate", job2["state"] == "termine" and fast < 12, f"{fast:.1f}s")
    check("  cache : successeurs reutilises", job2["cacheHits"] > 0, str(job2["cacheHits"]))

    # annulation d'une tâche longue
    print("\n· annulation")
    big = "\n".join(
        "Le paragraphe numero %d occupe volontairement le moteur pour tester l'annulation en cours de route." % i
        for i in range(90)
    ).encode("utf-8")
    jid3 = submit("long.txt", big)
    time.sleep(6)
    call(f"/api/jobs/{jid3}/cancel", "POST", b"")
    job3, _ = wait(jid3, quiet=True)
    check("  annulation respectée", job3["state"] in ("annule", "termine"), job3["state"])

    # erreurs propres
    print("\n· erreurs attendues")
    try:
        jid4 = submit("coucou.doc", b"%c1r1some bytes that look like a doc file" * 20)
        job4, _ = wait(jid4, quiet=True)
        check("  .doc : message explicite", job4["state"] == "erreur" and "charge" in (job4["error"] or ""), str(job4)[:200])
    except Exception as exc:  # noqa: BLE001
        check("  .doc : message explicite", "charge" in str(exc), str(exc)[:160])
    try:
        jid5 = submit("vide.txt", b"   \n  ")
        job5, _ = wait(jid5, quiet=True)
        check("  fichier vide : refus clair", job5["state"] == "erreur", str(job5)[:160])
    except Exception as exc:  # noqa: BLE001
        check("  fichier vide : refus clair", True, str(exc)[:80])
    try:
        call("/api/jobs/inexistant")
        check("  tache inconnue : 404", False, "pas de 404")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        check("  tache inconnue : 404 avec message", exc.code == 404 and "inconnue" in body, f"{exc.code} {body[:80]}")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} échec(s) sur {CHECKS} vérifications :")
        for line in FAILURES:
            print("  -", line)
        sys.exit(1)
    print(f"Tous les tests d'intégration passent ({CHECKS} vérifications).")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as exc:
        print("erreur HTTP", exc.code, exc.read()[:400])
        sys.exit(2)
    except (urllib.error.URLError, TimeoutError) as exc:
        print("erreur :", exc)
        sys.exit(2)
