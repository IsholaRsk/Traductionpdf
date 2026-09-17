# TradFilez

Traduire les fichiers que l'on télécharge dans une autre langue, sans compte et sans
historique. La structure du fichier est reconstruite après traduction — horodatages de
sous-titres, cellules Excel, clés JSON, styles Word — et non pas perdue.

- **En ligne :** https://tradfilez.vercel.app (l'ancien lien https://passerelle-vert.vercel.app
  suit les déploiements lui aussi)
- **Le code du site :** dossier `app/`. Un serveur Python 3.9+, bibliothèque standard seule,
  interface comprise. Tout le détail — formats, moteurs, confidentialité, variables
  d'environnement, tests — est dans [`app/README.md`](app/README.md).

## Lancer chez soi

```bash
cd app && python3 server.py --port 8000      # puis ouvrir http://localhost:8000
```

## Tester, publier

```bash
cd app
python3 tests/test_formats.py                          # 52 garde-fous de structure, hors-ligne
python3 server.py --port 8123 &
python3 tests/test_api.py mymemory fr en               # 103 vérifications, moteur réel
python3 tests/test_stateless.py http://127.0.0.1:8123   # 28, dont la parité avec le mode local

python3 deploy/build_vercel.py && cd deploy/vercel && npx vercel deploy --prod
```

## Le nom

Le dépôt s'appelait `Traductionpdf`, le site s'appelle désormais **TradFilez** — deux
moitiés de couleur différente, `Trad` à l'encre et `Filez` au vert profond. Les visuels
seuls ont bougé : les variables d'environnement prennent le préfixe `TRADFILEZ_`, avec
l'ancien `PASSERELLE_` toujours lu, et les réglages enregistrés dans le navigateur sont
repris sous la nouvelle clé. Le nom du dépôt GitHub n'a pas été changé (les liens déjà
partis continueraient de fonctionner) ; pour l'aligner : Settings → « Rename ».

L'ébauche initiale (`Index.html`, qui traduisait dans le seul navigateur, sans reconstruire
les formats) a été supprimée du dépôt : elle est remplacée par `app/`, et son historique
reste dans le git.
