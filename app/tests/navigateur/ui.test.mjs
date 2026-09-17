/* Smoke test de l'interface : jsdom charge la page servie, on pilote le DOM
   et on vérifie le cycle complet (fichier → traduction → résultat).

   node ui.test.mjs [http://127.0.0.1:8123]
*/
import { JSDOM, VirtualConsole } from "jsdom";

const BASE = process.argv[2] || "http://127.0.0.1:8123";
const failures = [];
let checks = 0;

function check(name, ok, detail = "") {
  checks += 1;
  console.log((ok ? "  ✓ " : "  ✗ ") + name + (ok ? "" : `  → ${String(detail).slice(0, 200)}`));
  if (!ok) failures.push(`${name} — ${String(detail).slice(0, 160)}`);
}

const until = async (fn, timeout = 120000, step = 200) => {
  const start = Date.now();
  for (;;) {
    let value;
    try { value = await fn(); } catch (e) { value = null; }
    if (value) return value;
    if (Date.now() - start > timeout) return null;
    await new Promise((r) => setTimeout(r, step));
  }
};

const errors = [];
const vc = new VirtualConsole();
vc.on("jsdomError", (e) => errors.push("jsdomError: " + (e && e.message)));
vc.on("error", (...a) => errors.push("console.error: " + a.join(" ")));

const dom = await JSDOM.fromURL(BASE + "/", {
  runScripts: "dangerously",
  pretendToBeVisual: true,
  virtualConsole: vc,
  beforeParse(window) {
    window.fetch = async (input, init) => {
      let body = init && init.body;
      if (body && typeof body.arrayBuffer === "function") {
        body = Buffer.from(await body.arrayBuffer());
      }
      return globalThis.fetch(new URL(input, BASE).href, { ...init, body });
    };
  },
});

const win = dom.window;
const doc = win.document;
await until(() => doc.querySelector("#translateBtn"));

console.log("· rendu initial");
check("page chargée", doc.title.includes("TradFilez"), doc.title);
const h1 = doc.querySelector(".brand-text h1");
check("marque bicolore en deux moitiés", /Trad/.test(h1.querySelector(".w-ink")?.textContent || "") && /Filez/.test(h1.querySelector(".w-sage")?.textContent || ""), h1.innerHTML);
const css = doc.querySelector("style").textContent;
const ci = (css.match(/\.w-ink \{ color: ([^;]+);/)||[])[1];
const cs = (css.match(/\.w-sage \{ color: ([^;]+);/)||[])[1];
check("les deux moitiés du nom ont deux couleurs distinctes", !!ci && !!cs && ci !== cs, `${ci} / ${cs}`);
check("CSS inliné", (doc.querySelector("style") || {}).textContent?.includes("--paper"), "pas de <style>");
check("aucune erreur de script au chargement", errors.length === 0, errors.join(" | "));
check("bouton désactivé sans fichier", doc.querySelector("#translateBtn").disabled);
check("moteur indiqué en tête", /Automatique|Moteur|MyMemory|DeepL|IA|LibreTranslate/.test(doc.querySelector("#engineChipLabel").textContent), doc.querySelector("#engineChipLabel").textContent);
check("cache signalé en pied", /cache/i.test(doc.querySelector("#cacheStat").textContent), doc.querySelector("#cacheStat").textContent);

console.log("\n· sélection de langue");
doc.querySelector("#langSrc .lang-btn").dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
const combo = await until(() => doc.querySelector(".combo"));
check("combobox ouvert", !!combo, "aucune liste");
combo.querySelector("input").value = "alle";
combo.querySelector("input").dispatchEvent(new win.Event("input", { bubbles: true }));
await until(() => combo.querySelectorAll("li").length === 1);
const options = combo.querySelectorAll("li");
check("recherche filtrée", options.length >= 1 && /allemand/i.test(options[0].textContent), Array.from(options).map((l) => l.textContent).join(", "));
options[0].dispatchEvent(new win.MouseEvent("mousedown", { bubbles: true }));
await until(() => doc.querySelector("#langSrc .lang-code").textContent === "de");
check("langue source appliquée", doc.querySelector("#langSrc .lang-code").textContent === "de", doc.querySelector("#langSrc .lang-code").textContent);
check("plus de liste ouverte", !doc.querySelector(".combo"));

doc.querySelector("#langTgt .lang-btn").dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
const combo2 = await until(() => doc.querySelector(".combo"));
combo2.querySelector("input").value = "espagnol";
combo2.querySelector("input").dispatchEvent(new win.Event("input", { bubbles: true }));
await until(() => Array.from(combo2.querySelectorAll("li")).some((l) => /espagnol/i.test(l.textContent)));
Array.from(combo2.querySelectorAll("li")).find((l) => /espagnol/i.test(l.textContent))
  .dispatchEvent(new win.MouseEvent("mousedown", { bubbles: true }));
await until(() => doc.querySelector("#langTgt .lang-code").textContent === "es");
check("langue cible appliquée", doc.querySelector("#langTgt .lang-code").textContent === "es", doc.querySelector("#langTgt .lang-code").textContent);

console.log("\n· onglet texte collé");
const area = doc.querySelector("#pasteArea");
area.value = "Bonjour à tous. Le café ouvre à six heures.\nDeuxième phrase du document.";
area.dispatchEvent(new win.Event("input", { bubbles: true }));
doc.querySelector("#tabText").dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
await until(() => !doc.querySelector("#paneText").hidden);
check("paneau texte visible", !doc.querySelector("#paneText").hidden);
check("compteur de caractères", /caractère/.test(doc.querySelector("#pasteCount").textContent), doc.querySelector("#pasteCount").textContent);
check("bouton activé", !doc.querySelector("#translateBtn").disabled);

console.log("\n· réglages");
doc.querySelector("#openSettings").dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
await until(() => !doc.querySelector("#settings").hidden);
check("feuille de réglages ouverte", !doc.querySelector("#settings").hidden);
await until(() => doc.querySelectorAll("#engineList .engine").length === 5, 8000);
const engineRows = doc.querySelectorAll("#engineList .engine");
check("cinq moteurs proposés", engineRows.length === 5, engineRows.length);
const deeplRow = Array.from(engineRows).find((r) => /DeepL/.test(r.textContent));
deeplRow.querySelector("input[name=enginePick]").checked = true;
deeplRow.querySelector("input[name=enginePick]").dispatchEvent(new win.Event("change", { bubbles: true }));
const deeplOn = await until(() => doc.querySelector('#engineList .engine[data-id=deepl].is-on') ? true : null, 8000);
check("moteur DeepL sélectionné", !!deeplOn, doc.querySelector("#engineList .engine.is-on")?.dataset.id);
check("champs de clé affichés", !!doc.querySelector('#fld-deepl-api_key'), "aucun champ");
const keyField = doc.querySelector('#fld-deepl-api_key');
keyField.value = "demo-key-sans-valeur";
keyField.dispatchEvent(new win.Event("input", { bubbles: true }));
doc.querySelector("#settingsSave").dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
await until(() => doc.querySelector("#settings").hidden);
check("réglages fermés après enregistrement", doc.querySelector("#settings").hidden);
check("puce du moteur mise à jour", /DeepL/.test(doc.querySelector("#engineChipLabel").textContent), doc.querySelector("#engineChipLabel").textContent);
check("clé conservée localement", JSON.parse(win.localStorage.getItem("tradfilez.settings.v1")).deepl.api_key === "demo-key-sans-valeur", win.localStorage.getItem("tradfilez.settings.v1"));

// on remet le moteur automatique pour la traduction
doc.querySelector("#openSettings").dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
await until(() => !doc.querySelector("#settings").hidden);
const autoRow = Array.from(doc.querySelectorAll("#engineList .engine")).find((r) => /Automatique/.test(r.textContent));
autoRow.querySelector("input[name=enginePick]").checked = true;
autoRow.querySelector("input[name=enginePick]").dispatchEvent(new win.Event("change", { bubbles: true }));
doc.querySelector("#settingsSave").dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
await until(() => doc.querySelector("#settings").hidden);

console.log("\n· dépôt d'un fichier (événement drop simulé)");
const file = new win.File(["Titre\n\nVoici un petit paragraphe a traduire, avec une deuxieme phrase.\n"], "demo.txt", { type: "text/plain" });
const drop = doc.querySelector("#drop");
const dropEvent = new win.Event("drop", { bubbles: true, cancelable: true });
dropEvent.dataTransfer = { files: [file] };
drop.dispatchEvent(dropEvent);
await until(() => doc.querySelectorAll("#fileList li").length === 1);
check("fichier ajouté à la liste", doc.querySelectorAll("#fileList li").length === 1, doc.querySelector("#fileList").textContent);
check("nom affiché", /demo\.txt/.test(doc.querySelector("#fileList").textContent), doc.querySelector("#fileList").textContent);
check("taille affichée", /o\b|Ko/.test(doc.querySelector("#fileList .f-meta").textContent), doc.querySelector("#fileList .f-meta").textContent);
check("bouton « Traduire » actif", !doc.querySelector("#translateBtn").disabled);
check("estimation affichée", /caractères environ/.test(doc.querySelector("#runHint").textContent), doc.querySelector("#runHint").textContent);

console.log("\n· traduction");
doc.querySelector("#translateBtn").dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
const ready = await until(() => {
  const box = doc.querySelector("#results");
  return box && !box.hidden ? box : null;
}, 300000);
check("zone de résultats affichée", !!ready, "aucun résultat au bout de 5 min");
if (ready) {
  const out = doc.querySelector("#resultsBody .pane.out pre");
  const inPane = doc.querySelector("#resultsBody .pane:not(.out) pre");
  check("aperçu original non vide", (inPane?.textContent || "").includes("paragraphe"), inPane?.textContent);
  check("aperçu traduit non vide", (out?.textContent || "").trim().length > 8, out?.textContent);
  check("boutique de téléchargement présente", !!doc.querySelector('#resultsBody a[download]'), doc.querySelector("#resultsBody").innerHTML.slice(0, 200));
  const rowState = doc.querySelector("#fileList li .f-meta")?.textContent || "";
  check("ligne du fichier marquée « traduit »", /traduit/.test(rowState), rowState);
  check("durée affichée sur la ligne", /instantan|\u00b7 \d|min \d\d/.test(rowState), rowState);
  console.log("    → traduit : " + JSON.stringify((out?.textContent || "").trim().slice(0, 90)));
} else {
  check("état de la ligne", false, doc.querySelector("#fileList li .f-meta")?.textContent + " | " + doc.querySelector(".f-err")?.textContent);
}

console.log("\n· options et boutons de résultat");
const toggle = doc.querySelector("#resultsBody [data-t]");
if (toggle) {
  toggle.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
  check("aperçu repliable", doc.querySelector("#resultsBody .result")?.className.includes("result"), doc.querySelector("#resultsBody .result")?.className);
}
doc.querySelector("#collapseAll")?.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
check("« replier tout » replie les aperçus", Array.from(doc.querySelectorAll("#resultsBody .result")).every((r) => !r.classList.contains("is-open")), doc.querySelector("#resultsBody .result")?.className);

console.log("\n· retrait du fichier");
area.value = "";
area.dispatchEvent(new win.Event("input", { bubbles: true }));
doc.querySelector("#fileList .f-x")?.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
await until(() => doc.querySelectorAll("#fileList li").length === 0);
check("liste vidée", doc.querySelectorAll("#fileList li").length === 0);
check("bouton désactivé à nouveau", doc.querySelector("#translateBtn").disabled);

console.log("\n· reprise des réglages de l'ancien nom du site");
const dom2 = await JSDOM.fromURL(BASE + "/", {
  runScripts: "dangerously", pretendToBeVisual: true, virtualConsole: new VirtualConsole(),
  beforeParse(window) {
    window.fetch = async (input, init) => {
      let body = init && init.body;
      if (body && typeof body.arrayBuffer === "function") body = Buffer.from(await body.arrayBuffer());
      return globalThis.fetch(new URL(input, BASE).href, { ...init, body });
    };
    // l'utilisateur avait enregistré ses clés sous « passerelle.settings.v1 »
    window.localStorage.setItem("passerelle.settings.v1", JSON.stringify({ engine: "mymemory", deepl: { api_key: "ancienne-cle" } }));
  },
});
const win2 = dom2.window, doc2 = win2.document;
await until(() => doc2.querySelector("#engineChipLabel"));
const vu = await until(() => (/MyMemory/.test(doc2.querySelector("#engineChipLabel").textContent) ? true : null), 25000);
check("le moteur de l'ancien enregistrement est retrouvé", !!vu, doc2.querySelector("#engineChipLabel").textContent);
await until(() => win2.localStorage.getItem("tradfilez.settings.v1"));
const migre = JSON.parse(win2.localStorage.getItem("tradfilez.settings.v1") || "{}");
check("la clé DeepL est reprise sous le nouveau nom", (migre.deepl || {}).api_key === "ancienne-cle", JSON.stringify(migre).slice(0, 120));
win2.close();

console.log("\n· erreurs de console");
check("aucune erreur JS pendant la session", errors.length === 0, errors.join(" | "));

console.log();
if (failures.length) {
  console.log(`${failures.length} échec(s) sur ${checks} vérifications :`);
  failures.forEach((f) => console.log("  - " + f));
  process.exit(1);
}
console.log(`Interface vérifiée (${checks} points de contrôle).`);
process.exit(0);
