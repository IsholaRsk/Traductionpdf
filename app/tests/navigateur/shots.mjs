/* Captures du rendu final : pilote la vraie page dans Chrome headless.
   node shots.mjs [http://127.0.0.1:8000]
*/
import puppeteer from "puppeteer-core";
const chromium = puppeteer;
import fs from "node:fs";

const BASE = process.argv[2] || "http://127.0.0.1:8000";
const HERE = new URL(".", import.meta.url).pathname;
const OUT = process.env.SORTIE || HERE + "rendu";
const CHROME = process.env.CHROME ||
  "/home/user/.cache/browsers/chrome-headless-shell/linux-153.0.8010.47/chrome-headless-shell-linux64/chrome-headless-shell";
fs.mkdirSync(OUT, { recursive: true });

const TXT = [
  "Le café de la gare ouvre à six heures du matin.",
  "Le patron torréfie les grains lui-même, chaque lundi.",
  "Il refuse les machines automatiques et écoute craquer le grain.",
  "Les habitués arrivent avant le jour et commandent un café long.",
  "Ce matin-là, la pluie tombait doucement sur les quais.",
].join("\n\n");

const browser = await chromium.launch({ executablePath: CHROME, args: ["--no-sandbox", "--font-render-hinting=none", "--lang=fr"] });
const page = await browser.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e.message)));
page.on("console", (m) => { if (m.type() === "error") errors.push("console: " + m.text()); });

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const shot = async (name, opts = {}) => { await page.screenshot({ path: `${OUT}/${name}.png`, ...opts }); console.log("  ✓", name); };

await page.setViewport({ width: 1440, height: 1000, deviceScaleFactor: 2 });
await page.goto(BASE + "/", { waitUntil: "networkidle2" });
await wait(700);

console.log("· captures");
await shot("01-accueil");

// carte « langues » : combo ouvert sur une recherche
await page.click("#langTgt .lang-btn");
await wait(150);
await page.type("#langTgt .combo input", "port");
await wait(250);
await shot("02-choix-de-la-langue", { clip: await page.evaluate(() => {
  const r = document.querySelector("#langTgt").closest(".card").getBoundingClientRect();
  return { x: Math.max(0, r.x - 24), y: r.y - 24, width: Math.min(1440, r.width + 48), height: r.height + 48 };
}) });
await page.keyboard.press("Escape");

// carte « ton » : options + glossaire
await shot("03-ton-et-options", { clip: await page.evaluate(() => {
  const card = document.querySelectorAll(".card")[2];
  const r = card.getBoundingClientRect();
  return { x: Math.max(0, r.x - 24), y: r.y - 20, width: Math.min(1440, r.width + 48), height: r.height + 40 };
}) });

// feuille de réglages
await page.click("#openSettings");
await page.waitForSelector("#engineList .engine", { timeout: 8000 });
await wait(350);
await shot("04-moteurs-et-cles");
await page.click("#closeSettings");
await wait(250);

// dépôt d'un fichier (vrai DataTransfer), puis clic sur Traduire
await page.evaluate((content) => {
  const dt = new DataTransfer();
  dt.items.add(new File([content], "cafe-du-matin.txt", { type: "text/plain" }));
  document.querySelector("#drop").dispatchEvent(new DragEvent("drop", { dataTransfer: dt, bubbles: true, cancelable: true }));
}, TXT);
await wait(300);
await shot("05-fichier-prepare", { clip: await page.evaluate(() => {
  const r = document.querySelector("#cardInput").getBoundingClientRect();
  return { x: Math.max(0, r.x - 24), y: r.y - 20, width: Math.min(1440, r.width + 48), height: r.height + 60 };
}) });

await page.click("#translateBtn");
await wait(400);
await shot("06-en-cours", { clip: await page.evaluate(() => {
  const r = document.querySelector("#cardInput").getBoundingClientRect();
  return { x: Math.max(0, r.x - 24), y: r.y - 20, width: Math.min(1440, r.width + 48), height: r.height + 60 };
}) });

// résultat
let done = true;
try { await page.waitForFunction(() => !document.querySelector("#results").hidden, { timeout: 150000 }); }
catch (e) { done = false; }
await wait(500);
if (done) {
  await shot("07-resultat", { clip: await page.evaluate(() => {
    const r = document.querySelector("#results").getBoundingClientRect();
    return { x: Math.max(0, r.x - 24), y: r.y - 16, width: Math.min(1440, r.width + 48), height: Math.min(1000, r.height + 32) };
  }) });
  await shot("08-page-complete-avec-resultat", { fullPage: true });
} else {
  console.log("  ! pas de résultat (moteurs saturés) — capture de l'état d'erreur");
  await shot("07-erreur-moteur", { clip: await page.evaluate(() => {
    const r = document.querySelector("#cardInput").getBoundingClientRect();
    return { x: Math.max(0, r.x - 24), y: r.y - 20, width: Math.min(1440, r.width + 48), height: r.height + 80 };
  }) });
}

// téléchargement proposé (infobulle sur la ligne du fichier)
await page.evaluate(() => document.activeElement && document.activeElement.blur());
await wait(200);

// mobile
const mob = await browser.newPage();
await mob.setViewport({ width: 390, height: 844, deviceScaleFactor: 2, isMobile: true });
await mob.goto(BASE + "/", { waitUntil: "networkidle2" });
await wait(500);
await mob.evaluate((content) => {
  const dt = new DataTransfer();
  dt.items.add(new File([content], "cafe-du-matin.txt", { type: "text/plain" }));
  document.querySelector("#drop").dispatchEvent(new DragEvent("drop", { dataTransfer: dt, bubbles: true, cancelable: true }));
}, TXT);
await wait(400);
await mob.screenshot({ path: `${OUT}/09-mobile.png`, fullPage: true });
console.log("  ✓ 09-mobile");
await mob.close();

// mode sombre
await page.emulateMediaFeatures([{ name: "prefers-color-scheme", value: "dark" }]);
await wait(400);
await shot("10-mode-sombre");

const state = await page.evaluate(() => ({
  hint: document.querySelector("#runHint").textContent,
  file: document.querySelector("#fileList li .f-meta")?.textContent || "",
  result: document.querySelector("#resultsBody .pane.out pre")?.textContent || "",
  engines: document.querySelector("#engineChipLabel").textContent,
  cache: document.querySelector("#cacheStat").textContent,
}));
console.log("\n· état final de la page");
console.log(JSON.stringify(state, null, 1));
console.log("\n· erreurs de console :", errors.length ? errors : "aucune");
await browser.close();
