# TradFilez — traduire ses fichiers, sobrement

Un petit site autonome qui traduit les fichiers que l'on dépose dans une autre langue,
en **conservant la structure** du document : sous-titres, tableaux, JSON, Markdown,
HTML, Word, Excel, PDF (texte extrait).

Design doux : papier chaud, un seul accent vert sauge, typographie système, aucune image,
aucun tracker, aucun compte. Clair le jour, sombre la nuit (`prefers-color-scheme`).

---

> Le site s'appelait **Passerelle**. Seuls les visuels ont bougé, une exception :
> les variables d'environnement ont un nouveau préfixe, `TRADFILEZ_`, et l'ancien
> `PASSERELLE_` reste lu pour ne casser aucun démarrage existant. De même, les réglages
> enregistrés dans le navigateur sous l'ancienne clé `passerelle.settings.v1` sont repris
> une fois sous `tradfilez.settings.v1` (les clés d'API ne se perdent pas au changement de nom).

## Démarrer

```bash
cd app
python3 server.py --port 8000        # ouvre http://localhost:8000
```

C'est tout : le serveur n'utilise que la bibliothèque standard de Python 3.9+.
Cinq modules complémentaires améliorent la prise en charge des formats, **s'ils sont
présents ils sont utilisés, sinon TradFilez les ignore poliment** (`pip install
python-docx openpyxl pymupdf pypdf beautifulsoup4`) :

| module | apport |
|---|---|
| `python-docx` | documents Word (.docx) |
| `openpyxl` | classeurs Excel (.xlsx) |
| `PyMuPDF` | PDF fidèle à la mise en page (blocs repositionnés) |
| `pypdf` (déjà dans `vendor/`) | PDF, repli texte seul si PyMuPDF manque |
| `beautifulsoup4` | traduction nœud par nœud du HTML |

## Comment ça marche

```
navigateur ──POST /api/jobs (fichier + X-Meta)──▶  serveur
                                                     │ 1. formats.py : extraction en « unités »
                                                     │    (paragraphe, bloc de sous-titre, cellule…)
                                                     │ 2. server.py : lots ≤ limite du moteur,
                                                     │    parallélisme borné, cache de paires
                                                     │ 3. engines.py : MyMemory / DeepL / IA /
                                                     │    LibreTranslate, avec repli automatique
                                                     │ 4. formats.py : reconstruction du fichier
 ◀── GET /api/jobs/{id} (progression) ───────────────┤
 ◀── GET /api/jobs/{id}/file  (téléchargement) ──────┘   GET /api/zip?ids=… pour tout récupérer
```

* **Unités, pas lignes brutes.** Un fichier est découpé en unités de sens (paragraphe,
  bloc de sous-titre, valeur JSON, cellule). Les unités sont regroupées en lots pour le
  moteur, traduites ligne à ligne, puis réinjectées exactement à leur place.
* **Alignement vérifié.** Si la sortie d'un moteur ne contient pas le bon nombre de
  lignes, le lot est retenté unité par unité ; un échec reste visible dans les avertissements.
* **Jetons protégés.** Blocs de code, URL, `id`, `slug`, `sku`, nombres, formules Excel,
  horodatages SRT/VTT, `msgid` Gettext, balises et attributs HTML ne sont pas traduits.
* **Cache** (`data/pairs.sqlite3`) : deux passages sur le même document ne repaient pas
  le moteur. Bouton « Vider le cache » en pied de page.
* **Confidentialité** : les fichiers restent en mémoire du serveur jusqu'au téléchargement
  (TTL 3 h), aucune base de documents ; les clés d'API vivent dans le `localStorage` du
  navigateur et ne sont transmises qu'au moteur choisi, à la volée.

## Les moteurs

| moteur | réglage | remarque |
|---|---|---|
| **Automatique** (défaut) | — | chaîne DeepL → IA → MyMemory |
| MyMemory | e-mail facultatif | gratuit, sans clé ; quota quotidien par IP, ~460 caractères par requête |
| DeepL | clé API | meilleure qualité MT, lots de 50 lignes, forfait gratuit sans carte (quota réel affiché par « Tester ») |
| IA compatible OpenAI | URL + clé + modèle | OpenAI, Mistral, Groq, OpenRouter, Ollama… respecte le glossaire et le ton demandé |
| LibreTranslate | URL (+ clé) | auto-hébergé = sans limite ; le démon public est bridé à 3 requêtes/minute |

Le **ton** (fluide, fidèle, soutenu, simple) part chez DeepL sous forme de `formality`
(valeurs admises seulement sur FR, DE, ES, IT, NL, PL, RU, PT-BR : le champ n'est envoyé
que pour ces langues cibles) et dans la consigne du moteur IA. Le **glossaire** — une
phrase libre du type « « pipeline » se dit « flux » ; vouvoyer le lecteur » — n'est lu que
par le moteur IA, qui est le seul à pouvoir obéir à une consigne en texte libre : la case
le dit explicitement. Les moteurs purement statistiques ignorent les deux.

## Quelle API gratuite fournir ?

À coller dans ⚙ **Réglages** du site — la clé reste dans le navigateur de celui qui la
saisit, rien n'est écrit sur le serveur. Par ordre d'intérêt :

| fournisseur | ce qu'on obtient | où | réglage à saisir |
|---|---|---|---|
| **DeepL API Free** | 1 000 000 caractères/mois sur le compte testé (sans carte) | deepl.com/pro-api → « API Free » | clé seule ; `api-free.deepl.com` est déduit de la clé qui finit par `:fx` ; « Tester » affiche le quota réel |
| **Google Gemini** (niveau gratuit) | quelques dizaines de requêtes/jour, sans carte | aistudio.google.com → « Get API key » | base `https://generativelanguage.googleapis.com/v1beta/openai/` · modèle `gemini-2.0-flash` |
| **Groq** | très rapide, quota quotidien gratuit, sans carte | console.groq.com/keys | base `https://api.groq.com/openai/v1` · modèle `llama-3.3-70b-versatile` |
| **OpenRouter** | catalogue de modèles portant `:free`, sans carte | openrouter.ai/keys | base `https://openrouter.ai/api/v1` · n'importe quel id `…:free` |
| **MyMemory** (déjà actif) | ~5 000 mots/jour par IP, ~50 000 avec un e-mail | — | aucun, juste l'e-mail pour élargir |
| **LibreTranslate auto-hébergé** | illimité, privé | `docker run -p 5000:5000 libretranslate/libretranslate` | base `http://127.0.0.1:5000` |

Un compte DeepL API Free réellement branché sur le site (clé collée dans Réglages) :
PDF de 2 pages traduit en anglais, en allemand avec le ton « soutenu » (donc avec
`formality=more`, accepté) et en arabe (blocs alignés à droite), sous-titres SRT en
deux lignes restés en deux lignes — moteur `deepl` annoncé par le serveur sur chaque
tâche, et facturation DeepL exactement égale au texte envoyé (mesure : 193 caractères
envoyés, 193 comptés sur `/v2/usage`).

Vérifié le jour de la rédaction de ce README : `POST https://api-free.deepl.com/v2/translate`
répond `403 Missing Authorization header`, `POST
https://generativelanguage.googleapis.com/v1beta/openai/chat/completions` répond
`400 Missing or invalid Authorization header`, `GET https://api.groq.com/openai/v1/models`
répond `401 Invalid API Key` — trois hôtes vivants qui n'attendent qu'une clé.

Les API « gratuites » des gros nuages (Google Cloud Translation, Azure Translator, Amazon
Translate) ont de vrais quotas mensuels gratuits mais **exigent une carte bancaire** : à
réserver à un usage professionnel.

Moteur IA ou moteur de traduction ? Pour un fichier de travail (rapport, sous-titres,
courriel) DeepL suffit et coûte moins cher en quota. Pour un fichier où la mise en page
et la fidélité du ton importent (littérature, texte marketing, PDF dense), le moteur IA
Gemini/Groq est meilleur — il reçoit les consignes de ton et le glossaire.

## Formats acceptés

`.txt .md .rst .srt .vtt .csv .tsv .json .html .xml .yml .yaml .toml .ini .po .docx .xlsx .pdf`

(plus toute extension texte inconnue, traitée comme du `.txt`).

Un aller-retour garde **la forme du fichier** : extension, pagination, horodatages,
feuilles, balises et mise en page — le PDF est caviardé puis recompillé à la même
place, jamais converti en `.txt`. Seule l'écriture change.

Refus explicite et en français pour `.doc .odt .rtf .pptx .epub` et les binaires — avec la
suggestion du contournement (enregistrer en `.docx`, ou coller dans l'onglet Texte).

## Déployer

Le site tourne déjà en ligne sur **https://tradfilez.vercel.app** (projet Vercel
`tradfilez` ; l'ancien lien `passerelle-vert.vercel.app` suit les déploiements
lui aussi). Le revoir chez soi tient en une commande ; le publier ailleurs aussi.

### Vercel — l'hébergement sans état

Un hébergeur serverless tue l'instance entre deux requêtes : la file de tâches en
mémoire du serveur local n'y a aucun sens. Le site bascule donc sur une API à trois
appels (`open` → `translate` → `build`) où **le navigateur tient les morceaux** :
même page, mêmes formats, et le fichier rendu est identique octet pour octet à celui
du mode local (`tests/test_stateless.py` le vérifie).

```bash
python3 deploy/build_vercel.py                    # régénère deploy/vercel/ depuis les sources
cd deploy/vercel && npx vercel deploy --prod      # ou : vercel link --project tradfilez
```

`deploy/shim.py` n'ajoute aucune logique métier : il branche les routes de `server.py`
sur Flask, la seule forme que Vercel sait exécuter. Trois choses en découlent :

* **fichiers ≤ 3 Mo** — le corps de requête de l'hébergeur est plafonné à 4,5 Mo et le
  fichier y voyage encodé en base64 (+33 %) ;
* **60 s par requête** — d'où `TRADFILEZ_WINDOW=30` passages traduits par lot, le reste
  à l'appel suivant ;
* **cache dans `/tmp`** d'une instance : il accélère les répétitions, il ne compte pas.

Et le quota des moteurs gratuits étant compté par **IP de sortie du datacenter**, la
première chose à faire sur un lien public est d'indiquer une clé DeepL ou IA dans
⚙ Réglages — sinon les visiteurs se disputent le quota du jour.

### Docker, fly.io — avec file d'attente

```bash
docker build -t tradfilez app
docker run --rm -p 8000:8000 -v tradfilez-cache:/data -e TRADFILEZ_DIR=/data tradfilez

cd app && fly launch && fly deploy        # fly.toml fourni : volume, arrêt la nuit
# Render / Railway / Koyeb : image Docker, ou build `pip install -r app/requirements.txt`
# et démarrage `cd app && python3 server.py` — le serveur lit $PORT tout seul.
```

Le serveur n'a besoin que de `PORT` et d'un répertoire inscriptible (`TRADFILEZ_DIR`).
Derrière un proxy (nginx, Cloudflare), relever `client_max_body_size` à 12 Mo pour
laissés passer les .docx lourds. Les clés d'API voyagent du navigateur vers ce serveur :
en public, mettre du HTTPS, et les saisir dans l'onglet Réglages de *son* navigateur
plutôt que de les embarquer dans l'image.

## Tests

Cinq suites, du plus proche du métal au plus proche du visiteur :

```bash
cd app
python3 tests/test_formats.py              # 74 garde-fous de structure, hors-ligne
python3 tests/test_engines.py              # 35 vérifications hors-ligne : requêtes, codes de
                                           # langue, hôtes, file de replis (aucun appel réseau)
python3 server.py --port 8123 &            # serveur de test (mode file d'attente)
python3 tests/test_api.py mymemory fr en   # 103 vérifications : cycle complet, formats réels
python3 tests/check_failures.py 8123       # repli entre moteurs, quota, erreurs, annulation

TRADFILEZ_STATELESS=1 TRADFILEZ_DIR=/tmp/pl python3 server.py --port 8011 &
python3 tests/test_stateless.py http://127.0.0.1:8011   # 31 : API sans état + parité avec le job
```

Les deux suites d'interface pilotent la vraie page (nécessitent `npm install jsdom`, et
Chrome headless pour les captures) :

```bash
cd tests/navigateur && npm install jsdom
node ui.test.mjs http://127.0.0.1:8123          # 41 : parcours complet, réglages, langues RTL, reprise des clés déjà saisies
node stateless.test.mjs http://127.0.0.1:8011   # 33 : mode sans état, ZIP du navigateur, annulation
node shots.mjs http://127.0.0.1:8000             # captures PNG dans tests/navigateur/rendu/
```

`stateless.test.mjs` passe aussi contre le site en ligne — c'est le test qui prouve qu'un
visiteur, chez lui, obtient bien un fichier traduit :

```bash
node stateless.test.mjs https://tradfilez.vercel.app
```

## Limites assumées

* Le moteur gratuit a un quota par IP : en cas de dépassement, TradFilez bascule sur le
  démon public pour les fichiers courts, sinon s'arrête en le disant (plutôt que de rendre
  un fichier cru pour avoir l'air d'avoir traduit).
* Un PDF devient un fichier texte : la mise en page d'origine n'est pas reproduite.
* Dans un `.docx`, le formatage *à l'intérieur* d'un paragraphe (italique d'un seul mot)
  est simplifié : le paragraphe est traduit d'un bloc pour garder son sens complet.
* Les fichiers chiffrés, scannés (PDF image) ou volumineux (> 8 Mo) sont refusés clairement.
