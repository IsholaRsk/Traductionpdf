#!/usr/bin/env python3
"""Contrôle le site en ligne sur les deux points demandés : le nom du fichier rendu
est exactement celui fourni, et le PDF ressort avec ses polices et sa mise en page.

    python3 verif_prod.py [https://tradfilez.vercel.app]

Le cycle passe par le protocole sans état, donc par le moteur par défaut (MyMemory).
"""

import base64
import json
import sys
import time
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "https://tradfilez.vercel.app").rstrip("/")
ECHECS = []


def check(attendu, ok, detail=""):
    print(("  ✓ " if ok else "  ✗ ") + attendu + ("" if ok else f"   → {detail}"))
    if not ok:
        ECHECS.append(f"{attendu} — {detail}")


def get(chemin):
    with urllib.request.urlopen(BASE + chemin, timeout=90) as r:
        return r.read().decode("utf-8", "replace")


def post(chemin, objet):
    req = urllib.request.Request(BASE + chemin, data=json.dumps(objet).encode(), method="POST",
                                headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=240) as r:
        return json.loads(r.read().decode("utf-8"))


def pdf_exemple():
    """Deux pages A4, un titre gras, un paragraphe, un trait — de quoi mesurer la fidélité."""
    try:
        import pymupdf as m
    except ImportError:
        return None
    doc = m.open()
    for titre, corps in [("Rapport du port de Cotonou",
                          "Le port ferme a vingt heures et le gardien allume le phare au crepuscule."),
                         ("Annexe", "Les bateaux rentrent quand le vent tourne.")]:
        page = doc.new_page()
        page.insert_text((72, 90), titre, fontsize=18, fontname="hebo")
        page.insert_text((72, 140), corps, fontsize=11)
        page.draw_line((72, 105), (523, 105), color=(0.4, 0.5, 0.4))
    blob = doc.tobytes()
    doc.close()
    return blob


def main():
    print(f"\n· {BASE}")
    page = get("/")
    check("la page répond", "Trad" in page and "Filez" in page, page[:80])
    check("favicon collé dans la page (rien à charger)", 'rel="icon" href="data:image/svg+xml,' in page)
    check("marque redessinée dans l’en-tête", 'id="brandMark"' in page and "c6-12 22-12" not in page)
    check("aucun suffixe « _traduit » dans le code livré", "_traduit" not in page)

    blob = pdf_exemple()
    if blob is None:
        print("  · (PyMuPDF absent : contrôle PDF sauté — pip install pymupdf)")
        return
    nom = "rapport-port.pdf"
    meta = {"filename": nom, "src": "fr", "tgt": "de", "options": {}, "config": {}}
    fichier = base64.b64encode(blob).decode()
    ouverture = post("/api/open", {"meta": meta, "file": fichier})
    recu = {str(k): v for k, v in (ouverture.get("cached") or {}).items()}
    attendu = [p["t"] for p in ouverture["pending"]]
    for tentative in range(1, 4):  # un moteur public peut être à sec une minute : on réessaie
        if not attendu:
            break
        traduit = post("/api/translate", {"meta": meta, "items": attendu})
        pour_l_instant = {str(p["i"]): v for p, v in zip(ouverture["pending"], traduit.get("translations") or [])}
        recu.update({k: v for k, v in pour_l_instant.items() if (v or "").strip()})
        attendu = [ouverture["pending"][int(k)]["t"] for k in
                   [str(p["i"]) for p in ouverture["pending"] if str(p["i"]) not in pour_l_instant or not (pour_l_instant[str(p["i"])] or "").strip()]]
        if recu:
            break
        if tentative < 3:
            print(f"  · moteur muet (tentative {tentative}/3) : {', '.join(traduit.get('errors') or ['—'])}")
            time.sleep(6)
    if not recu:
        check("au moins un moteur répond", False,
              "trois lots renvoyés vides — le moteur public est peut-être à sec ; "
              "réessayez ou indiquez une clé dans les réglages")
        print("\n" + f"ÉCHECS {len(ECHECS)} : " + ", ".join(ECHECS))
        sys.exit(1)
    rendu = post("/api/build", {"meta": meta, "file": fichier, "translations": recu})
    check("le nom rendu est exactement le nom fourni", rendu["name"] == nom, rendu["name"])
    check("l’extension est conservée", nom.rsplit(".", 1)[-1] == rendu["name"].rsplit(".", 1)[-1])
    check("tout a été traduit", rendu["missing"] == 0 and rendu["total"] == rendu["count"] > 0,
          (rendu.get("missing"), rendu.get("total")))

    import io
    import pymupdf as m
    avant = m.open(stream=blob, filetype="pdf")
    apres = m.open(stream=io.BytesIO(base64.b64decode(rendu["b64"])), filetype="pdf")
    check("même nombre de pages", len(avant) == len(apres), (len(avant), len(apres)))
    check("même format de page", tuple(round(v) for v in avant[0].rect) == tuple(round(v) for v in apres[0].rect))

    def style(doc):
        out = []
        for p in doc:
            for b in p.get_text("dict")["blocks"]:
                if b.get("type") != 0:
                    continue
                for ln in b["lines"]:
                    for sp in ln["spans"]:
                        out.append((sp["font"], round(sp["size"], 1)))
        return out

    check("mêmes polices et mêmes corps qu’à l’entrée", set(style(avant)) == set(style(apres)),
          (sorted(set(style(avant))), sorted(set(style(apres)))))
    check("aucun tracé perdu", sum(len(p.get_drawings()) for p in avant) == sum(len(p.get_drawings()) for p in apres))
    check("la page ne porte plus que la langue d’arrivée",
          "Rapport du port" not in apres[0].get_text() and apres[0].get_text().strip() != "",
          repr(apres[0].get_text().strip()[:60]))
    print(f"\n  note du serveur : {rendu['note']}")
    print("\n" + (f"ÉCHECS {len(ECHECS)} : " + ", ".join(ECHECS) if ECHECS else "site en ligne conforme"))
    sys.exit(1 if ECHECS else 0)


if __name__ == "__main__":
    try:
        main()
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        corps = ""
        if isinstance(exc, urllib.error.HTTPError):
            corps = f"HTTP {exc.code} : " + exc.read().decode("utf-8", "replace")[:220]
        else:
            corps = f"jointure impossible vers {BASE} : {exc.reason}"
        print(f"\n  ✗ {corps}")
        sys.exit(1)
