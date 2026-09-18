"""Construction des requêtes moteur : vérifiée sans réseau.

Ce fichier ne traduit rien : il regarde ce que les moteurs *vont* envoyer
(adresses, codes de langue, limites de lot) et comment ils se choisissent.
Ces détails sont invisibles tant qu'un seul fournisseur répond, et ce sont
justement ceux qui cassent quand on en branche un autre.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engines as eng  # noqa: E402

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


print("\n· DeepL : l'hôte suit le type de clé")
d = eng.DeepLEngine({"api_key": "01234567-89ab-cdef-0123-456789abcdef:fx"})
check("clé « :fx » → api-free.deepl.com", d.base == "https://api-free.deepl.com", d.base)
check("clé payante → api.deepl.com",
      eng.DeepLEngine({"api_key": "01234567-cdef"}).base == "https://api.deepl.com",
      eng.DeepLEngine({"api_key": "01234567-cdef"}).base)
check("base_url explicite gagne, sans slash final",
      eng.DeepLEngine({"api_key": "x", "base_url": "https://deepl.example.corp/"}).base == "https://deepl.example.corp")
check("sans clé, le moteur se déclare inutilisable", not eng.DeepLEngine({}).ready())
check("avec clé, il est prêt", d.ready())

print("\n· DeepL : codes de langue, source et cible ne se ressemblent pas")
check("cible « en » → EN-US", d.code("en") == "EN-US", d.code("en"))
check("cible « pt » → PT-BR", d.code("pt") == "PT-BR", d.code("pt"))
check("source « en » → EN (jamais EN-US)", d.champs("en", "fr")["source_lang"] == "EN", d.champs("en", "fr"))
check("la source ne porte aucune région",
      all("-" not in d.champs(s, t).get("source_lang", "") for s in ("en", "pt", "zh", "gr") for t in ("fr", "de")),
      {s: d.champs(s, "fr").get("source_lang") for s in ("en", "pt", "zh", "gr")})
check("source « auto » : le paramètre est omis", "source_lang" not in d.champs("auto", "fr"), d.champs("auto", "fr"))
check("cible laissée à l'anglais par défaut", d.champs("fr", "en")["target_lang"] == "EN-US", d.champs("fr", "en"))
soutenu = eng.DeepLEngine({"api_key": "k:fx", "tone": "soutenu"})
check("ton soutenu → formality=more sur l'allemand", soutenu.champs("fr", "de").get("formality") == "more", soutenu.champs("fr", "de"))
check("ton soutenu ignoré sur l'anglais (DeepL refuse ailleurs)", "formality" not in soutenu.champs("fr", "en"),
      soutenu.champs("fr", "en"))
check("ton courant → formality=less", eng.DeepLEngine({"api_key": "k", "tone": "courant"}).champs("de", "fr").get("formality") == "less")
check("sans préférence de ton, rien n'est imposé", "formality" not in eng.DeepLEngine({"api_key": "k"}).champs("fr", "de"))

print("\n· lots et nettoyage")
check("DeepL traduit par lots de 50 lignes", d.max_lines == 50, d.max_lines)
check("DeepL accepte 4500 caractères par requête", d.max_chars == 4500, d.max_chars)
check("un retour à la ligne du moteur ne casse pas l'alignement",
      d.clean("deux  lignes\n  ici") == "deux lignes ici", repr(d.clean("deux  lignes\n  ici")))
long = "Une phrase assez longue pour être coupée. " * 40
morceaux = eng.split_long(long.strip(), 400)
check("split_long tient sous la limite", all(len(m) <= 400 for m in morceaux), max(len(m) for m in morceaux))
check("split_long ne perd aucun mot", " ".join(morceaux).split() == long.split(), len(morceaux))
check("split_long laisse intact ce qui tient déjà", eng.split_long("courte", 400) == ["courte"])

print("\n· autres moteurs")
llm = eng.OpenAICompatEngine({})
check("IA : défaut OpenAI si rien n'est réglé", llm.base == "https://api.openai.com/v1", llm.base)
check("IA : modèle par défaut gpt-4o-mini", llm.model == "gpt-4o-mini", llm.model)
libre = eng.LibreTranslateEngine({"min_interval": "0.5"})
check("LibreTranslate : délai entre requêtes lu", libre.min_interval == 0.5, libre.min_interval)
check("LibreTranslate : un délai illégal ne fait pas tomber le serveur",
      eng.LibreTranslateEngine({"min_interval": "trente"}).min_interval == 1.0)
check("MyMemory : limite courte connue", eng.MyMemoryEngine({}).max_chars == 460, eng.MyMemoryEngine({}).max_chars)

print("\n· file d'attente des moteurs")
chaine = eng.build_engines({"engine": "deepl", "deepl": {"api_key": "k:fx"}})
check("moteur choisi en tête", [e.id for e in chaine][:2] == ["deepl", "mymemory"], [e.id for e in chaine])
check("sans clé, on retombe sur un moteur utilisable",
      [e.id for e in eng.build_engines({"engine": "deepl"})] == ["mymemory"],
      [e.id for e in eng.build_engines({"engine": "deepl"})])
check("réglages vides : MyMemory suffit à démarrer",
      [e.id for e in eng.build_engines({})] == ["mymemory"], [e.id for e in eng.build_engines({})])
auto = eng.build_engines({"deepl": {"api_key": "k:fx"}, "llm": {"api_key": "sk-x", "model": "m"}})
check("automatique : DeepL avant IA, IA avant MyMemory",
      [e.id for e in auto] == ["deepl", "llm", "mymemory"], [e.id for e in auto])
partage = eng.build_engines({"engine": "llm", "llm": {"api_key": "sk"}, "tone": "soutenu", "glossary": "vouvoyer"})
check("ton et consignes transmis au moteur choisi",
      partage[0].config.get("tone") == "soutenu" and partage[0].config.get("glossary") == "vouvoyer",
      partage[0].config)

print("\n· clé posée par l'hébergeur (variables d'environnement du déploiement)")
os.environ["TRADFILEZ_DEEPL_KEY"] = "01234567-89ab-cdef-0123-456789abcdef:fx"
os.environ["TRADFILEZ_LLM_MODEL"] = "gemini-2.0-flash"
try:
    chaine = eng.build_engines({})
    check("la clé de l'hébergeur met DeepL dans la chaîne", "deepl" in [e.id for e in chaine],
          [e.id for e in chaine])
    check("et le choix automatique le place en tête", [e.id for e in chaine][0] == "deepl",
          [e.id for e in chaine])
    check("la clé rejoint bien le réglage du moteur",
          eng.with_defauts_env({})["deepl"]["api_key"].endswith(":fx"), eng.with_defauts_env({}))
    check("le modèle de l'IA se règle aussi par environnement",
          eng.OpenAICompatEngine(eng.with_defauts_env({})["llm"]).model == "gemini-2.0-flash")
    visiteur = eng.build_engines({"engine": "mymemory", "mymemory": {}})
    check("un visiteur qui choisit MyMemory garde son choix",
          [e.id for e in visiteur][0] == "mymemory", [e.id for e in visiteur])
    os.environ["TRADFILEZ_LLM_KEY"] = "sk-test"
    os.environ["TRADFILEZ_ENGINE"] = "llm"
    check("TRADFILEZ_ENGINE impose le moteur par défaut",
          [e.id for e in eng.build_engines({})][0] == "llm", [e.id for e in eng.build_engines({})])
    del os.environ["TRADFILEZ_LLM_KEY"]
    check("un moteur laissé à moitié configuré n'entre pas dans la chaîne",
          "llm" not in [e.id for e in eng.build_engines({})], [e.id for e in eng.build_engines({})])
finally:
    del os.environ["TRADFILEZ_DEEPL_KEY"], os.environ["TRADFILEZ_LLM_MODEL"]
check("environnement nettoyé, DeepL disparaît de la chaîne",
      "deepl" not in [e.id for e in eng.build_engines({})], [e.id for e in eng.build_engines({})])

print("\n· variables d'environnement, ancien nom compris")
os.environ["TRADFILEZ_WINDOW"] = "17"
os.environ["PASSERELLE_WINDOW_CHARS"] = "999"
check("TRADFILEZ_ lu", eng.env("window") == "17", eng.env("window"))
check("PASSERELLE_ toujours lu", eng.env("window_chars") == "999", eng.env("window_chars"))
os.environ["TRADFILEZ_WINDOW"] = "23"
check("le nouveau préfixe a la priorité", eng.env("window") == "23", eng.env("window"))
del os.environ["TRADFILEZ_WINDOW"], os.environ["PASSERELLE_WINDOW_CHARS"]
check("défaut rendu si rien n'est posé", eng.env("window", "60") == "60")

print()
if FAILURES:
    print(f"{len(FAILURES)} échec(s) sur {CHECKS} vérifications :")
    for f in FAILURES:
        print("  -", f)
    sys.exit(1)
print(f"Moteurs conformes ({CHECKS} vérifications).")
