/* Test du mode « sans état » (celui du déploiement Vercel) : la page est chargée
   sur un serveur lancé avec TRADFILEZ_STATELESS=1, on vérifie le cycle complet
   open → translate → build, le téléchargement unitaire, le ZIP fait dans le
   navigateur et l'annulation en cours de route.

   node stateless.test.mjs [http://127.0.0.1:8011]
*/
import { JSDOM, VirtualConsole } from "jsdom";
import { writeFileSync, readFileSync } from "node:fs";

const BASE = process.argv[2] || "http://127.0.0.1:8011";
const failures = [];
let checks = 0;

function check(name, ok, detail = "") {
  checks += 1;
  console.log((ok ? "  ✓ " : "  ✗ ") + name + (ok ? "" : `  → ${String(detail).slice(0, 220)}`));
  if (!ok) failures.push(`${name} — ${String(detail).slice(0, 160)}`);
}
const until = async (fn, timeout = 240000, step = 150) => {
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

const calls = [];      // toutes les requêtes de la page
const blobs = [];      // tout ce que la page donne à télécharger
const clicked = [];    // les clics sur <a> (jsdom ne sait pas naviguer)

const dom = await JSDOM.fromURL(BASE + "/", {
  runScripts: "dangerously",
  pretendToBeVisual: true,
  virtualConsole: vc,
  beforeParse(window) {
    const real = async (input, init) => {
      let body = init && init.body;
      if (body && typeof body.arrayBuffer === "function") body = Buffer.from(await body.arrayBuffer());
      return globalThis.fetch(new URL(input, BASE).href, { ...init, body });
    };
    window.fetch = async (input, init) => {
      const path = String(typeof input === "string" ? input : input.url).replace(BASE, "");
      calls.push({ path: path.split("?")[0], method: (init && init.method) || "GET" });
      if (init && /TEST-CANCEL/.test(String(init.body || ""))) {
        await new Promise((r) => setTimeout(r, 2500));   // la requête doit être encore en vol au clic
      }
      const res = await real(input, init);
      if (path.startsWith("blob:")) {
        const buf = Buffer.from(await res.arrayBuffer());
        blobs.push(buf);
      }
      return res;
    };
    window.HTMLAnchorElement.prototype.click = function () { clicked.push(this); };
    window.URL.createObjectURL = (blob) => {
      blobs.push(blob);
      return "blob:capture-" + blobs.length;
    };
    window.URL.revokeObjectURL = () => {};
  },
});

const win = dom.window;
const doc = win.document;
const readBlob = async (b) => (typeof b.text === "function"
  ? await b.text()
  : new Promise((res) => { const fr = new win.FileReader(); fr.onload = () => res(fr.result); fr.readAsText(b); }));

await until(() => doc.querySelector("#translateBtn"));

console.log("· mode du serveur");
const meta = await (await globalThis.fetch(BASE + "/api/meta")).json();
check("le serveur annonce le mode sans état", meta.mode === "stateless", JSON.stringify(meta.mode));
check("aucune erreur de script au chargement", errors.length === 0, errors.join(" | "));

console.log("\n· deux fichiers déposés");
const mk = (text, name) => {
  const f = new win.File([text], name, { type: "text/plain" });
  return f;
};
const alphaText = "Le phare de la pointe ferme à vingt heures.\n\nLe gardien allume la lampe avant la nuit.\n\nLes bateaux rentrent quand le vent tourne.";
const betaText = "Note de service : la salle des archives ouvre a neuf heures.\n\nPrevoir un vetement chaud.";
const alpha = mk(alphaText, "alpha.txt");
const beta = mk(betaText, "beta.txt");
const drop = new win.Event("drop", { bubbles: true, cancelable: true });
drop.dataTransfer = { files: [alpha, beta] };
doc.querySelector("#drop").dispatchEvent(drop);
await until(() => doc.querySelectorAll("#fileList li").length === 2);
check("les deux fichiers sont dans la liste", doc.querySelectorAll("#fileList li").length === 2, doc.querySelector("#fileList").textContent);

console.log("\n· traduction (open → translate → build)");
const before = calls.length;
doc.querySelector("#translateBtn").dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
const ready = await until(() => (!doc.querySelector("#results").hidden && doc.querySelectorAll("#resultsBody .result").length === 2) ? true : null);
check("deux résultats affichés", !!ready, doc.querySelector("#fileList").textContent.slice(0, 160));
const api = calls.slice(before).map((c) => c.path);
check("le mode sans état n'a pas créé de tâche", !api.includes("/api/jobs"), api.join(", "));
check("le mode est connu avant le premier clic", !(doc.querySelector("#fileList .f-meta") || {}).textContent?.includes("file d'attente"), "");
check("open, translate et build appelés (translate seulement s'il reste des passages hors cache)",
      api.includes("/api/open") && api.includes("/api/build") && api.includes("/api/translate"), api.join(", "));
check("aucun aller-retour inutile quand tout sort du cache", !api.includes("/api/translate") || api.filter((p) => p === "/api/translate").length >= 1, api.join(", "));
check("un open et un build par fichier", api.filter((p) => p === "/api/open").length === 2 && api.filter((p) => p === "/api/build").length === 2, api.join(", "));
check("aucune erreur dans la console", errors.length === 0, errors.join(" | "));

const rows = Array.from(doc.querySelectorAll("#fileList li"));
check("ligne alpha marquée « traduit »", /traduit/.test(rows[0].textContent), rows[0].textContent);
check("ligne beta marquée « traduit »", /traduit/.test(rows[1].textContent), rows[1].textContent);

const outs = Array.from(doc.querySelectorAll("#resultsBody .pane.out pre")).map((p) => p.textContent);
check("aperçus traduits non vides", outs.length === 2 && outs.every((t) => t.trim().length > 10), JSON.stringify(outs));
check("l'original reste visible en regard", Array.from(doc.querySelectorAll("#resultsBody .pane:not(.out) pre")).every((p) => p.textContent.trim().length > 10));
console.log("    → " + JSON.stringify((outs[0] || "").trim().slice(0, 100)));

const links = Array.from(doc.querySelectorAll("#resultsBody a[download]"));
check("chaque résultat a un lien de téléchargement", links.length === 2, links.length);
check("le lien pointe sur le fichier fabriqué dans le navigateur", links.every((a) => a.getAttribute("href").startsWith("blob:capture")), links.map((a) => a.getAttribute("href")).join(" "));
const files = blobs.slice(0, 2);
const txts = await Promise.all(files.map(readBlob));
check("le fichier téléchargé porte la traduction", txts.every((t) => t.trim().length > 10) && txts[0] !== alphaText && txts[1] !== betaText, JSON.stringify(txts.map((t) => t.slice(0, 60))));
check("la structure du .txt est conservée (trois blocs)", txts[0].split(/\n\n+/).length === 3 && txts[1].split(/\n\n+/).length === 2, JSON.stringify(txts.map((t) => t.split(/\n\n+/).length)));
check("deux noms de fichiers distincts", /alpha_traduit\.txt/.test(links[0].textContent) && /beta_traduit\.txt/.test(links[1].textContent), links.map((l) => l.textContent).join(" "));

console.log("\n· ZIP fabriqué côté navigateur");
const zip = doc.querySelector("#zipBtn");
check("bouton « tout télécharger » visible", !!zip && !zip.hidden, zip && zip.className);
const nBefore = blobs.length;
zip.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
const gotZip = await until(() => blobs.length > nBefore ? blobs[blobs.length - 1] : null, 20000);
check("un blob ZIP a été produit", !!gotZip, "rien capturé");
if (gotZip) {
  const buf = Buffer.from(await gotZip.arrayBuffer());
  writeFileSync("/tmp/zip_navigateur.zip", buf);
  check("le clic sur « tout télécharger » a bien déclenché un lien", clicked.some((a) => (a.download || "").endsWith(".zip")), clicked.map((a) => a.download).join(", "));
  check("signature ZIP (PK)", buf[0] === 0x50 && buf[1] === 0x4b, buf.slice(0, 4).toString("hex"));
  check("taille cohérente", buf.length > 100, buf.length);
}

console.log("\n· annulation en cours de route");
const afterCancel = calls.length;
const rowOf = (name) => Array.from(doc.querySelectorAll("#fileList li")).find((li) => (li.querySelector(".f-name")?.textContent || "").includes(name));
const n = Date.now();
const slow = mk(["la cale " + n + " du port est fermée", "la grue " + n + " rouge attend le matin", "le chef " + n + " compte les caisses bleues"].map((t) => "TEST-CANCEL " + t).join("\n\n"), "long.txt");
const drop2 = new win.Event("drop", { bubbles: true, cancelable: true });
drop2.dataTransfer = { files: [slow] };
doc.querySelector("#drop").dispatchEvent(drop2);
await until(() => rowOf("long.txt"));
check("le troisième fichier rejoint la liste", !!rowOf("long.txt"), doc.querySelectorAll("#fileList li").length);

doc.querySelector("#translateBtn").dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
const counted = await until(() => (/\d+\/\d+ passages/.test(rowOf("long.txt")?.querySelector(".f-meta")?.textContent || "") ? rowOf("long.txt").querySelector(".f-meta").textContent : null), 25000);
check("la ligne affiche la progression par passages", !!counted, rowOf("long.txt")?.querySelector(".f-meta")?.textContent);
check("une barre de progression est dessinée", !!rowOf("long.txt").querySelector(".f-bar i[style]"), rowOf("long.txt")?.innerHTML.slice(0, 120));
doc.querySelector("#cancelBtn").dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
const stopped = await until(() => /interrompu/.test(rowOf("long.txt")?.querySelector(".f-meta")?.textContent || ""), 20000);
check("la ligne bascule en « interrompu »", !!stopped, rowOf("long.txt")?.querySelector(".f-meta")?.textContent);
check("le bouton d'annulation disparaît", doc.querySelector("#cancelBtn").hidden);
check("aucun fichier reconstruit pour la tâche interrompue", !calls.slice(afterCancel).some((c) => c.path === "/api/build"), calls.slice(afterCancel).map((c) => c.path).join(", "));
check("la reprise est proposée (bouton « Traduire » de nouveau actif)", !doc.querySelector("#translateBtn").disabled);

console.log("\n· après annulation : la reprise va au bout");
doc.querySelector("#translateBtn").dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
const again = await until(() => /traduit ·/.test(rowOf("long.txt")?.querySelector(".f-meta")?.textContent || ""), 60000);
check("le fichier repart et finit", !!again, rowOf("long.txt")?.querySelector(".f-meta")?.textContent);
check("aucune erreur de script sur tout le parcours", errors.length === 0, errors.join(" | "));

console.log("\n" + (failures.length ? "ÉCHECS (" + failures.length + "/" + checks + ")\n - " + failures.join("\n - ") : `tout est bon (${checks} vérifications)`));
win.close();
process.exit(failures.length ? 1 : 0);
