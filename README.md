# Traductionpdf

Passerelle — traduire les fichiers que l'on télécharge dans une autre langue.

- **En ligne :** https://passerelle-vert.vercel.app
- **Le site :** dossier `app/`. Un serveur Python 3.9+, bibliothèque standard seule,
  interface comprise. Tout le détail est dans [`app/README.md`](app/README.md).
- **L'ébauche existante** (`Index.html`, qui traduisait dans le seul navigateur) reste
  en place : rien n'a été écrasé ni renommé.

Lancer chez soi :

```bash
cd app && python3 server.py --port 8000     # puis ouvrir http://localhost:8000
```

Formats acceptés : `.txt .md .rst .srt .vtt .csv .tsv .json .html .xml .yml .toml .ini
.po .docx .xlsx .pdf`. La structure du fichier est reconstruite après traduction —
horodatages de sous-titres, cellules, clés JSON, styles Word — et non pas perdue.

Déployer ailleurs : `python3 app/deploy/build_vercel.py` puis `npx vercel deploy --prod`
(dossier `app/deploy/vercel/`, régénéré à chaque fois), ou `docker build -t passerelle app`.

Tests : `python3 app/tests/test_formats.py`, puis `tests/test_api.py`, `tests/test_stateless.py`,
et deux suites pilotant la vraie page dans jsdom (`app/tests/navigateur/`).
