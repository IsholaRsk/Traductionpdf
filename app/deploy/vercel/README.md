# Déploiement Vercel (arborescence générée)

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
