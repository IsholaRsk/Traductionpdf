#!/usr/bin/env python3
"""Fabrique `deploy/vercel/`, l'arborescence réellement envoyée chez l'hébergeur.

    python3 deploy/build_vercel.py

Le dossier de sortie n'est pas une copie à surveiller à la main : il est régénéré
à chaque fois, à partir de `server.py`, `formats.py`, `engines.py`, `stateless.py`
et `web/`. C'est ce qui évite que le site en ligne et le site local divergent.
"""

from __future__ import annotations

import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "deploy", "vercel")
MODULES = ("server.py", "formats.py", "engines.py", "stateless.py")
WEB = ("index.html", "app.css", "app.js")

REQUIREMENTS = """# Socle : Flask sert seulement d'adaptateur HTTP pour l'hébergeur sans état.
flask>=3.0

# Les mêmes bibliothèques optionnelles qu'en local : sans elles, le site tourne
# mais .docx, .xlsx, .pdf et .html retombent sur des parseurs plus simples.
python-docx>=1.1
openpyxl>=3.1
pypdf>=4.2
beautifulsoup4>=4.12
lxml>=5.2
"""

VERCEL_JSON = {
    "$schema": "https://openapi.vercel.sh/vercel.json",
    "framework": None,
    # L'alias suit le déploiement : pas de `vercel alias set` à rejouer à chaque poussee.
    "alias": "passerelle-vert.vercel.app",
    # Une seule fonction, un seul point d'entrée : tout ce qui n'est pas la page
    # statique (public/) lui est passé, chemins /api/* compris.
    "functions": {"api/index.py": {"maxDuration": 60}},
    "regions": ["cdg1"],
    "rewrites": [{"source": "/api/(.*)", "destination": "/api/index"}],
}


def inline_page():
    """La page, autonome : CSS et JS collés dedans, comme le fait le serveur local."""
    with open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8") as fh:
        html = fh.read()
    with open(os.path.join(ROOT, "web", "app.css"), encoding="utf-8") as fh:
        css = fh.read()
    with open(os.path.join(ROOT, "web", "app.js"), encoding="utf-8") as fh:
        js = fh.read()
    return (html.replace("<!--CSS-->", "<style>\n" + css + "\n</style>")
                .replace("<!--JS-->", "<script>\n" + js + "\n</script>")
                .replace("<!--META-->", '<meta name="description" content="Traduction de fichiers, sobre et locale.">'))


def build():
    api = os.path.join(OUT, "api")
    pub = os.path.join(OUT, "public")
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(api, exist_ok=True)
    os.makedirs(pub, exist_ok=True)

    # Vercel interdit deux fichiers du même nom dans api/ : la fonction unique y
    # reste seule, les modules du site vivent à la racine et elle les importe.
    for name in MODULES:
        shutil.copy2(os.path.join(ROOT, name), os.path.join(OUT, name))
    shutil.copy2(os.path.join(ROOT, "deploy", "shim.py"), os.path.join(api, "index.py"))
    with open(os.path.join(pub, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(inline_page())

    with open(os.path.join(OUT, "requirements.txt"), "w", encoding="utf-8") as fh:
        fh.write(REQUIREMENTS)
    with open(os.path.join(OUT, "vercel.json"), "w", encoding="utf-8") as fh:
        json.dump(VERCEL_JSON, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    with open(os.path.join(OUT, ".vercelignore"), "w", encoding="utf-8") as fh:
        fh.write("__pycache__/\n*.pyc\ndata/\n")
    with open(os.path.join(OUT, "README.md"), "w", encoding="utf-8") as fh:
        fh.write(DOC)

    print(f"dossier écrit : {OUT}")
    print("  racine :", ", ".join(sorted(n for n in os.listdir(OUT) if os.path.isfile(os.path.join(OUT, n)))))
    print("  api/   :", ", ".join(sorted(os.listdir(api))))
    print("  public/: page de", os.path.getsize(os.path.join(pub, "index.html")), "octets (CSS et JS inlinés)")
    return OUT


DOC = """# Déploiement Vercel (arborescence générée)

Ne pas éditer ici : `python3 deploy/build_vercel.py` régénère tout ce dossier à
partir des fichiers de `app/`. Le code applicatif reste identique au serveur
local ; seul `api/index.py` diffère, et il ne fait que brancher les mêmes routes
sur Flask, la forme que Vercel sait exécuter.

Trois conséquences de l'hébergement sans état, toutes voulues :

* le navigateur tient la file de traduction (trois allers-retours par fichier :
  `open`, `translate`, `build`) — voir `PASSERELLE_STATELESS=1` ;
* le cache de paires vit dans `/tmp` d'une instance : il accélère, il ne compte pas ;
* un fichier est limité à 3 Mo (le corps de requête de l'hébergeur est plafonné
  à 4,5 Mo et le fichier y voyage encodé en base64).

## Mettre en ligne

```bash
python3 deploy/build_vercel.py
cd deploy/vercel && npx vercel deploy --prod --token "$VERCEL_TOKEN"
```

Ou, si le dépôt GitHub est relié à un projet Vercel : régler le
« Root Directory » sur `app/deploy/vercel` et pousser sur `main`.
"""


if __name__ == "__main__":
    build()
    sys.exit(0)
