"""Tests de structure : chaque format doit survivre à un aller-retour extraction → reconstruction.

Aucun appel réseau : on remplace la traduction par un transformateur local
(majuscules + marqueur), ce qui suffit à vérifier l'alignement des unités.
"""

import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import formats as fmt  # noqa: E402

FAILURES = []
CHECKS = 0


def check(name, condition, detail=""):
    global CHECKS
    CHECKS += 1
    if not condition:
        FAILURES.append(f"{name} — {detail}")
        print(f"  ✗ {name}  {detail}")
    else:
        print(f"  ✓ {name}")


def roundtrip(filename, text, transform=str.upper, options=None):
    doc = fmt.load(filename, text.encode("utf-8") if isinstance(text, str) else text, options or {})
    translations = []
    for unit in doc.units:
        translations.append(transform(unit) if unit else "")
    data, name, preview_in, preview_out = doc.rebuild(translations)
    return doc, data, name, preview_out


def decode(data):
    return data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else data


# --------------------------------------------------------------------------- #
print("\n· texte brut")
doc, data, name, body = roundtrip("note.txt", "Première ligne.\nDeuxième ligne.\n\nSecond paragraphe.")
check("deux paragraphes reconnus", doc.unit_count == 3, f"{doc.unit_count}")
check("sauts doubles conservés", "\n\n" in decode(data), repr(decode(data)))
check("nom de sortie", name == "note_traduit.txt", name)
lines = decode(data).split("\n")
check("paragraphes fusionnés, blanc conservé", lines == ["PREMIÈRE LIGNE. DEUXIÈME LIGNE.", "", "SECOND PARAGRAPHE."], repr(lines))

doc, data, name, body = roundtrip("dur.txt", "Un tout premier paragraphe.\nSur trois lignes\nqui doivent être regroupées.", options={"keep_lines": True})
check("mode lignes : 3 unités", doc.unit_count == 3, str(doc.unit_count))
check("mode lignes : ordre conservé", decode(data).split("\n")[1] == "SUR TROIS LIGNES", repr(decode(data)))

print("\n· markdown")
md = "# Titre du projet\n\nTexte introductif.\n\n```python\nprint('ne pas traduire')\n```\n\n- puce une\n- puce deux\n"
doc, data, name, body = roundtrip("lu.md", md)
out = decode(data)
check("bloc de code intact", "print('ne pas traduire')" in out, repr(out))
check("code non traduit", "NE PAS TRADUIRE" not in out)
check("titre traduit", out.startswith("# TITRE DU PROJET"), repr(out[:40]))
check("liste conservée", "- PUCE UNE" in out, repr(out))

print("\n· sous-titres srt")
srt = "1\n00:00:01,000 --> 00:00:04,000\nBonjour à tous.\nDeuxième ligne du sous-titre.\n\n2\n00:00:04,500 --> 00:00:08,000\nAu revoir.\n"
doc, data, name, body = roundtrip("film.srt", srt)
out = decode(data)
check("horodatages conservés", out.count("00:00:") == 4, repr(out))
check("numérotation conservée", "1\n" in out and "2\n" in out, repr(out))
check("texte traduit", "BONJOUR À TOUS. DEUXIÈME LIGNE DU SOUS-TITRE." in out, repr(out))
check("deux blocs", out.strip().count("\n\n") >= 1, repr(out))

print("\n· webvtt")
vtt = "WEBVTT\n\n00:00.000 --> 00:02.000\nSalut\n\nNOTE un commentaire\n\n00:02.000 --> 00:04.000\nCoucou\n"
doc, data, name, body = roundtrip("cap.vtt", vtt)
out = decode(data)
check("en-tête WEBVTT conservé", out.startswith("WEBVTT"), repr(out[:20]))
check("note conservée", "NOTE un commentaire" in out, repr(out))
check("dialogue traduit", "SALUT" in out and "COUCOU" in out, repr(out))

print("\n· json")
payload = {"title": "Bonjour", "nested": {"body": "Une phrase.\nDeuxième ligne.", "count": 3, "ok": True}, "list": [{"label": "Un"}]}
doc, data, name, body = roundtrip("t.json", json.dumps(payload, ensure_ascii=False, indent=2))
back = json.loads(decode(data))
check("structure intacte", back["nested"]["count"] == 3 and back["nested"]["ok"] is True, json.dumps(back))
check("valeur traduite", back["title"] == "BONJOUR", json.dumps(back))
check("multiligne préservée", "\n" in back["nested"]["body"], repr(back["nested"]["body"]))
check("liste traduite", back["list"][0]["label"] == "UN", json.dumps(back["list"]))

doc, data, name, body = roundtrip("filtre.json", json.dumps({"title": "Bonjour le monde", "id": "keep-me", "code": "FR"}))
back2 = json.loads(decode(data))
check("jetons techniques laissés", back2["id"] == "keep-me" and back2["code"] == "FR", json.dumps(back2, ensure_ascii=False))
check("phrase voisine traduite", back2["title"] == "BONJOUR LE MONDE", json.dumps(back2))
doc, data, name, body = roundtrip("scoping.json", json.dumps({"title": "Bonjour", "notes": "Non traduit ici"}), options={"json_keys": ["title"]})
back3 = json.loads(decode(data))
check("portée par clé respectée", back3["title"] == "BONJOUR" and back3["notes"] == "Non traduit ici", json.dumps(back3, ensure_ascii=False))

print("\n· csv")
csv_text = 'col_a;col_b\n"Bon, jour";42\n"Deuxième; ligne";7\n'
doc, data, name, body = roundtrip("table.csv", csv_text)
out = decode(data)
check("en-tête non traduit", "col_a;col_b" in out, repr(out))
check("point-virgule conservé", ";" in out.split("\n")[1], repr(out))
check("cellule traduite", "BON, JOUR" in out, repr(out))
check("nombre intact", ";42" in out, repr(out))

doc, data, name, body = roundtrip("h.csv", "a,b\n1,deux\n", options={"translate_header": True})
check("en-tête traduit sur demande", "A,B".upper() in decode(data), repr(decode(data)))

print("\n· html")
html = '<!doctype html><html lang="fr"><head><title>Ma page</title><style>p{color:red}</style></head><body><h1>Bonjour</h1><p>Ceci est <strong>un test</strong>.</p><script>var x="no touch";</script></body></html>'
doc, data, name, body = roundtrip("page.html", html)
out = decode(data)
check("balises intactes", "<h1>" in out and "<strong>" in out, repr(out[:80]))
check("style non traduit", "p{color:red}" in out, repr(out))
check("script non traduit", 'var x="no touch"' in out, repr(out))
check("titre traduit", ">MA PAGE<" in out, repr(out))
check("paragraphe traduit", "UN TEST" in out, repr(out))

print("\n· yaml / toml")
yml = "title: Bonjour le monde\nslug: keep-this\nnav:\n  - label: Accueil\n    url: /fr/\n"
doc, data, name, body = roundtrip("conf.yml", yml)
out = decode(data)
check("clé yaml conservée", "title:" in out, repr(out))
check("valeur yaml traduite", "BONJOUR LE MONDE" in out, repr(out))
check("url non altérée", "/fr/" in out, repr(out))

print("\n· gettext po")
po = 'msgid ""\nmsgstr ""\n"Project-Id-Version: 1\\n"\n"Language: fr\\n"\n\nmsgid "Hello"\nmsgstr "Bonjour"\n\nmsgid "Bye"\nmsgstr "Au revoir"\n'
doc, data, name, body = roundtrip("fr.po", po)
out = decode(data)
check("en-tête po intact", "Project-Id-Version" in out, repr(out))
check("msgid intact", 'msgid "Hello"' in out, repr(out))
check("msgstr traduit", 'msgstr "BONJOUR"' in out, repr(out))

print("\n· document Word")
try:
    import docx

    buffer = io.BytesIO()
    document = docx.Document()
    document.add_heading("Titre du document", level=1)
    document.add_paragraph("Premier paragraphe.")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Colonne une"
    data_in = None
    document.save(buffer)
    blob = buffer.getvalue()
    doc, data, name, body = roundtrip("note.docx", blob)
    out_doc = docx.Document(io.BytesIO(data))
    texts = [p.text for p in out_doc.paragraphs]
    check("docx relu", len(texts) >= 2, repr(texts))
    check("docx traduit", any(t == "PREMIER PARAGRAPHE." for t in texts), repr(texts))
    check("docx table traduite", out_doc.tables[0].rows[0].cells[0].text == "COLONNE UNE", repr(out_doc.tables[0].rows[0].cells[0].text))
    check("docx chargé", data[:2] == b"PK", "signature zip")
except ImportError:
    print("  (python-docx absent : test Word ignoré)")

print("\n· classeur Excel")
try:
    import openpyxl

    book = openpyxl.Workbook()
    sheet = book.active
    sheet["A1"] = "En-tête"
    sheet["B1"] = 12.5
    sheet["A2"] = "Valeur texte"
    sheet["B2"] = "=SUM(B1)"
    buffer = io.BytesIO()
    book.save(buffer)
    doc, data, name, body = roundtrip("budget.xlsx", buffer.getvalue())
    reloaded = openpyxl.load_workbook(io.BytesIO(data)).active
    check("xlsx cellule texte traduite", reloaded["A2"].value == "VALEUR TEXTE", repr(reloaded["A2"].value))
    check("xlsx nombre intact", reloaded["B1"].value == 12.5, repr(reloaded["B1"].value))
    check("xlsx formule intacte", reloaded["B2"].value == "=SUM(B1)", repr(reloaded["B2"].value))
except ImportError:
    print("  (openpyxl absent : test Excel ignoré)")

print("\n· rejets et cas limites")
try:
    roundtrip("vide.txt", "   ")
    check("fichier vide rejeté", False, "aucune exception")
except fmt.Unsupported as exc:
    check("fichier vide rejeté", "vide" in str(exc).lower(), str(exc))
try:
    roundtrip("binaire.bin", bytes(range(200)))
    check("binaire rejeté", False, "aucune exception")
except fmt.Unsupported:
    check("binaire rejeté", True)
try:
    roundtrip("mauvais.json", "{ceci n'est pas du json")
    check("json invalide rejeté", False)
except fmt.Unsupported:
    check("json invalide rejeté", True)
doc, data, name, body = roundtrip("accents.txt, avec (parenthèses) et espaces.txt", "Test")
check("nom de sortie conservé", name.endswith(".txt"), name)

print("\n· latin-1")
doc = fmt.load("ancien.txt", "Accélérateur déjà là.".encode("cp1252"), {})
check("repli d'encodage", doc.units[0].startswith("ACCELERATEUR") or "cc" in doc.units[0], repr(doc.units))

print("\n· PDF : mise en page et extension conservées")
try:
    import pymupdf as _m
except Exception:  # noqa: BLE001
    _m = None
if _m is None:
    print("    (PyMuPDF absent : checks de repli uniquement)")
    try:
        fmt.load("x.pdf", ("%PDF-1.4\nfaux corps\n%%EOF").encode("utf-8"), {})
        check("pdf invalide rejeté", False)
    except fmt.Unsupported:
        check("pdf invalide rejeté", True)
else:
    _doc = _m.open()
    for _t, _c in [("Rapport du port", "Le port ferme à vingt heures.\nLe gardien allume le phare."),
                   ("Annexe", "Les bateaux rentrent quand le vent tourne.")]:
        _pg = _doc.new_page()
        _pg.insert_text((72, 90), _t, fontsize=18, fontname="hebo")
        _pg.insert_text((72, 140), _c, fontsize=11)
        _pg.draw_line((72, 105), (523, 105), color=(0.4, 0.5, 0.4), width=1.2)
    _pdf = _doc.tobytes(deflate=True)
    _doc.close()

    _d = fmt.load("rapport.pdf", _pdf, {})
    check("le PDF se lit en blocs paginés", len(_d.todo) == 4 and len(_d.blocs) == 4, _d.todo)
    check(" chaque bloc porte sa page et son rectangle",
          all(b["p"] in (0, 1) and b["r"][2] > b["r"][0] for b in _d.blocs), _d.blocs[:1])
    TRAD = {"Rapport du port": "Port Report",
            "Le port ferme à vingt heures. Le gardien allume le phare.": "The port closes at eight. The keeper lights the beacon.",
            "Annexe": "Appendix",
            "Les bateaux rentrent quand le vent tourne.": "Boats come back when the wind turns."}
    _full = [TRAD.get(_d.units[i], "") if _d.units[i] else "" for i in range(_d.unit_count)]
    _out, _nom, _pin, _pout = _d.rebuild(_full)
    check("le fichier rendu est un PDF, pas un texte", _out[:5] == b"%PDF-" and _nom.endswith(".pdf"), _nom)
    check("l’extension est conservée, le nom marqué « _traduit »", _nom == "rapport_traduit.pdf", _nom)
    _r = _m.open(stream=_out, filetype="pdf")
    check("la pagination est conservée", _r.page_count == 2, _r.page_count)
    check("le format de page est conservé", tuple(round(v) for v in _r[0].rect) == (0, 0, 595, 842), tuple(_r[0].rect))
    _txt = " ".join(p.get_text() for p in _r)
    check("l’écriture a changé, la forme est restée", "Port Report" in _txt and "The port closes at eight" in _txt, _txt[:120])
    check("plus aucune trace du texte d’origine", "vingt heures" not in _txt and "vent tourne" not in _txt, _txt[:120])
    check("chaque page garde son bloc de titre en haut", all("72" not in p.get_text() for p in _r) and _r[1].get_text().strip().startswith("Appendix"), _r[1].get_text()[:40])
    check("le tracé d’origine (ligne) n’est pas effacé", len(_r[0].get_drawings()) >= 1, len(_r[0].get_drawings()))
    check("la note d’avertissement décrit le vrai comportement", "mise en page" in _d.note and "pas reproduite" not in _d.note, _d.note)
    _r.close()

    # un bloc non traduit ne doit pas laisser de trou dans le document
    _d2 = fmt.load("trou.pdf", _pdf, {})
    _partiel = ["" for _ in range(_d2.unit_count)]
    _partiel[0] = "Port Report"
    _o2, _n2, _i2, _x2 = _d2.rebuild(_partiel)
    _r2 = _m.open(stream=_o2, filetype="pdf")
    _t2 = _r2[0].get_text()
    check("un passage non traduit reste en place plutôt que de créer un trou",
          "Port Report" in _t2 and "Le gardien allume le phare" in _t2, repr(_t2[:120]))
    _r2.close()

    _scan = _m.open()
    _scan.new_page().insert_text((72, 90), " ".join(["x"] * 3), fontsize=1)
    _scan[0].get_pixmap()
    _vide = _scan.tobytes()
    _scan.close()
    try:
        fmt.load("scan.pdf", _vide, {})
        check("PDF sans texte sélectionnable refusé avec un mot clair", False)
    except fmt.Unsupported as exc:
        check("PDF sans texte sélectionnable refusé avec un mot clair", "scan" in str(exc), str(exc))


print()
if FAILURES:
    print(f"{len(FAILURES)} échec(s) sur {CHECKS} vérifications :")
    for f in FAILURES:
        print("  -", f)
    sys.exit(1)
print(f"Tous les tests de structure passent ({CHECKS} vérifications).")
