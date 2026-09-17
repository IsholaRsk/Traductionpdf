/* TradFilez — logique du poste de traduction. Vanilla JS, aucune dépendance. */
(function () {
  "use strict";

  // ------------------------------------------------------------------ langues
  const LANGS = [
    ["fr", "français", "français"], ["en", "anglais", "English"], ["es", "espagnol", "español"],
    ["de", "allemand", "Deutsch"], ["it", "italien", "italiano"], ["pt", "portugais", "português"],
    ["nl", "néerlandais", "Nederlands"], ["pl", "polonais", "polski"], ["ru", "russe", "русский"],
    ["uk", "ukrainien", "українська"], ["tr", "turc", "Türkçe"], ["ar", "arabe", "العربية"],
    ["he", "hébreu", "עברית"], ["fa", "perse", "فارسی"], ["hi", "hindi", "हिन्दी"],
    ["bn", "bengali", "বাংলা"], ["ur", "ourdou", "اردو"], ["zh", "chinois", "中文"],
    ["ja", "japonais", "日本語"], ["ko", "coréen", "한국어"], ["vi", "vietnamien", "Tiếng Việt"],
    ["th", "thaï", "ไทย"], ["id", "indonésien", "Bahasa Indonesia"], ["ms", "malais", "Bahasa Melayu"],
    ["tl", "tagalog", "Tagalog"], ["sv", "suédois", "svenska"], ["no", "norvégien", "norsk"],
    ["da", "danois", "dansk"], ["fi", "finnois", "suomi"], ["is", "islandais", "íslenska"],
    ["et", "estonien", "eesti"], ["lv", "letton", "latviešu"], ["lt", "lituanien", "lietuvių"],
    ["el", "grec", "ελληνικά"], ["cs", "tchèque", "čeština"], ["sk", "slovaque", "slovenčina"],
    ["hu", "hongrois", "magyar"], ["ro", "roumain", "română"], ["bg", "bulgare", "български"],
    ["hr", "croate", "hrvatski"], ["sr", "serbe", "српски"], ["sl", "slovène", "slovenščina"],
    ["sq", "albanais", "shqip"], ["mk", "macédonien", "македонски"], ["ca", "catalan", "català"],
    ["gl", "galicien", "galego"], ["eu", "basque", "euskara"], ["ga", "irlandais", "Gaeilge"],
    ["cy", "gallois", "Cymraeg"], ["mt", "maltais", "Malti"], ["af", "afrikaans", "Afrikaans"],
    ["sw", "swahili", "Kiswahili"], ["am", "amharique", "አማርኛ"], ["yo", "yoruba", "Yorùbá"],
    ["zu", "zoulou", "isiZulu"], ["ne", "népalais", "नेपाली"], ["si", "cinghalais", "සිංහල"],
    ["km", "khmer", "ខ្មែរ"], ["lo", "laotien", "ລາວ"], ["my", "birman", "မြန်မာ"],
    ["ka", "géorgien", "ქართული"], ["hy", "arménien", "հայերեն"], ["az", "azerbaïdjanais", "Azərbaycan"],
    ["kk", "kazakh", "қазақ тілі"], ["uz", "ouszbek", "oʻzbek"], ["mn", "mongol", "Монгол"],
    ["la", "latin", "latina"], ["eo", "espéranto", "Esperanto"],
  ];
  const RTL = new Set(["ar", "he", "fa", "ur", "ps", "sd", "yi"]);
  const NAME = { fr: "français", auto: "Détection automatique" };
  LANGS.forEach(([c, f]) => (NAME[c] = f));
  const label = (code) => NAME[code] || code;

  const EXT_OK = new Set(["txt", "text", "md", "markdown", "rst", "srt", "vtt", "sub", "csv", "tsv",
    "json", "jsonl", "html", "htm", "xml", "yml", "yaml", "toml", "ini", "cfg", "po", "docx", "xlsx",
    "xlsm", "pdf", "log"]);

  // taux de débit observés (caractères traduits par seconde) pour estimer la durée
  const RATE = { auto: 320, mymemory: 380, libretranslate: 55, deepl: 2600, llm: 780 };

  // ------------------------------------------------------------------ état
  const LS = "tradfilez.settings.v1";
  const LS_ANCIEN = "passerelle.settings.v1";   // clé de l'ancien nom, reprise une fois
  const state = {
    src: "auto",
    tgt: "en",
    tone: "fluide",
    files: [],
    busy: false,
    engine: "auto",
    mode: "files",
    engines: [],
    cache: {},
  };
  let config = load();
  try {
    if (localStorage.getItem(LS + ".mode") === "stateless") state.mode = "stateless";
  } catch (e) { /* stockage indisponible */ }
  let uid = 0;

  function load() {
    try {
      let raw = localStorage.getItem(LS);
      if (raw === null && localStorage.getItem(LS_ANCIEN)) {
        // le site s'appelait Passerelle : on reprend une fois les réglages de l'ancien nom
        // (clé API comprises), puis on écrit sous le nom courant.
        raw = localStorage.getItem(LS_ANCIEN);
        try { localStorage.setItem(LS, raw); } catch (e) { /* stockage privé */ }
      }
      const cfg = JSON.parse(raw || "{}");
      return Object.assign({
        engine: "auto",
        keepLines: false, translateHeader: false, parallel: false, glossary: "",
        mymemory: { email: "" },
        libretranslate: { base_url: "", api_key: "", min_interval: "" },
        deepl: { api_key: "", base_url: "" },
        llm: { base_url: "", api_key: "", model: "", temperature: "" },
      }, cfg);
    } catch (e) {
      return { engine: "auto", mymemory: {}, libretranslate: {}, deepl: {}, llm: {} };
    }
  }
  function save() {
    try { localStorage.setItem(LS, JSON.stringify(config)); } catch (e) { /* stockage privé */ }
  }

  // ------------------------------------------------------------------ DOM
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const el = (tag, cls, html) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (html !== undefined) node.innerHTML = html;
    return node;
  };
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const bytes = (n) => n < 1024 ? n + " o" : n < 1048576 ? (n / 1024).toFixed(n < 10240 ? 1 : 0) + " Ko" : (n / 1048576).toFixed(1) + " Mo";
  const num = (n) => (n || 0).toLocaleString("fr-FR");
  const dur = (s) => s < 60 ? Math.round(s) + " s" : Math.floor(s / 60) + " min " + String(Math.round(s % 60)).padStart(2, "0");

  const dom = {
    drop: $("#drop"), fileInput: $("#fileInput"), list: $("#fileList"), note: $("#fileNote"),
    paneFiles: $("#paneFiles"), paneText: $("#paneText"), tabFiles: $("#tabFiles"), tabText: $("#tabText"),
    paste: $("#pasteArea"), pasteName: $("#pasteName"), pasteCount: $("#pasteCount"),
    srcBtn: $("#langSrc [data-role=lang-btn]"), tgtBtn: $("#langTgt [data-role=lang-btn]"),
    swap: $("#swapBtn"), tones: $("#tones"),
    optKeep: $("#optKeepLines"), optHeader: $("#optHeader"), optParallel: $("#optParallel"),
    glossary: $("#glossary"),
    run: $("#translateBtn"), runLabel: $("#translateLabel"), cancel: $("#cancelBtn"), clear: $("#clearBtn"),
    hint: $("#runHint"), detect: $("#detectHint"),
    results: $("#results"), resultsBody: $("#resultsBody"), zip: $("#zipBtn"), collapse: $("#collapseAll"),
    settings: $("#settings"), engineList: $("#engineList"), probe: $("#probeBtn"), probeOut: $("#probeOut"),
    chip: $("#engineChip"), chipLabel: $("#engineChipLabel"), chipSwatch: $("#engineSwatch"),
    toast: $("#toast"), cacheStat: $("#cacheStat"),
  };

  let toastTimer = null;
  function toast(msg, ms) {
    dom.toast.textContent = msg;
    dom.toast.hidden = false;
    requestAnimationFrame(() => dom.toast.classList.add("is-on"));
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      dom.toast.classList.remove("is-on");
      setTimeout(() => (dom.toast.hidden = true), 260);
    }, ms || 2600);
  }

  // ------------------------------------------------------------------ onglets
  function setTab(which) {
    const files = which === "files";
    dom.paneFiles.hidden = !files;
    dom.paneText.hidden = files;
    dom.tabFiles.classList.toggle("is-on", files);
    dom.tabText.classList.toggle("is-on", !files);
    dom.tabFiles.setAttribute("aria-selected", String(files));
    dom.tabText.setAttribute("aria-selected", String(!files));
    state.tab = files ? "files" : "text";
    refreshRun();
  }
  dom.tabFiles.addEventListener("click", () => setTab("files"));
  dom.tabText.addEventListener("click", () => setTab("text"));
  setTab("files");

  // ------------------------------------------------------------------ sélection de langue
  function openCombo(anchor, current, onPick, allowAuto) {
    closeCombo();
    const box = el("div", "combo");
    const search = el("input");
    search.placeholder = "Rechercher une langue…";
    const ul = el("ul");
    box.append(search, ul);
    box._anchor = anchor;
    anchor.parentNode.appendChild(box);
    anchor.dataset.open = "1";
    search.value = current === "auto" && allowAuto ? "" : "";
    let sel = 0, items = [];

    function draw() {
      const q = search.value.trim().toLowerCase();
      items = [];
      if (allowAuto && (!q || "auto détection detection automatique".includes(q))) items.push(["auto", "Détection automatique", "laisse TradFilez deviner"]);
      LANGS.forEach(([c, f, n]) => {
        if (!q || c.includes(q) || f.includes(q) || (n || "").toLowerCase().includes(q)) items.push([c, f, n]);
      });
      sel = Math.max(0, items.findIndex((it) => it[0] === current));
      ul.innerHTML = items.map((it, i) =>
        `<li data-i="${i}" class="${i === sel ? "is-sel" : ""}" dir="${RTL.has(it[0]) ? "rtl" : "ltr"}">
           <b>${esc(it[1][0].toUpperCase() + it[1].slice(1))}</b><span>${esc(it[2] || "")}</span><i>${esc(it[0])}</i></li>`).join("");
      const li = ul.children[Math.max(0, sel)];
      if (li && li.scrollIntoView) li.scrollIntoView({ block: "nearest" });
    }
    function pick(i) {
      if (items[i]) { onPick(items[i][0]); closeCombo(); }
    }
    ul.addEventListener("mousedown", (e) => {
      const li = e.target.closest("li");
      if (li) { e.preventDefault(); pick(+li.dataset.i); }
    });
    search.addEventListener("input", () => { sel = 0; draw(); });
    search.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { e.preventDefault(); sel = Math.min(sel + 1, items.length - 1); draw(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); sel = Math.max(sel - 1, 0); draw(); }
      else if (e.key === "Enter") { e.preventDefault(); pick(sel); }
      else if (e.key === "Escape") { closeCombo(); anchor.focus(); }
    });
    box.addEventListener("focusout", (e) => {
      if (!document.contains(box)) return;
      if (!box.contains(e.relatedTarget) && e.relatedTarget !== anchor) closeCombo();
    });
    draw();
    setTimeout(() => search.focus(), 10);
  }
  let closingCombo = false;
  function closeCombo() {
    if (closingCombo) return;
    closingCombo = true;
    try {
      // on sort le focus du panneau avant de le détacher : sinon le focusout
      // déclenché par remove() rappelle closeCombo en plein milieu (Chrome lève
      // alors un NotFoundError)
      const active = document.activeElement;
      if (active && active.closest && active.closest(".combo")) active.blur();
      $$(".combo").forEach((n) => { if (n.parentNode) n.remove(); });
      $$(".lang-btn").forEach((b) => { if (b.dataset) delete b.dataset.open; });
    } finally {
      closingCombo = false;
    }
  }
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeCombo(); });

  function paintLang(which) {
    const code = which === "src" ? state.src : state.tgt;
    const btn = which === "src" ? dom.srcBtn : dom.tgtBtn;
    $(".lang-name", btn).textContent = label(code);
    $(".lang-code", btn).textContent = code;
    btn.dir = RTL.has(code) ? "rtl" : "ltr";
  }
  dom.srcBtn.addEventListener("click", () => {
    if (dom.srcBtn.dataset.open) return closeCombo();
    openCombo(dom.srcBtn, state.src, (c) => { state.src = c; paintLang("src"); refreshRun(); }, true);
  });
  dom.tgtBtn.addEventListener("click", () => {
    if (dom.tgtBtn.dataset.open) return closeCombo();
    openCombo(dom.tgtBtn, state.tgt, (c) => {
      state.tgt = c === "auto" ? "fr" : c; paintLang("tgt"); refreshRun();
    }, false);
  });
  dom.swap.addEventListener("click", () => {
    if (state.src === "auto") { toast("Choisissez une langue source explicite pour inverser."); return; }
    const a = state.src; state.src = state.tgt; state.tgt = a;
    paintLang("src"); paintLang("tgt"); refreshRun();
  });

  // ------------------------------------------------------------------ tons & options
  dom.tones.addEventListener("change", (e) => {
    const input = e.target.closest("input");
    if (!input) return;
    state.tone = input.value;
    $$(".tone", dom.tones).forEach((l) => l.classList.toggle("is-on", $("input", l).checked));
  });
  [dom.optKeep, dom.optHeader, dom.optParallel].forEach((cb) => cb.addEventListener("change", () => {
    config.keepLines = dom.optKeep.checked;
    config.translateHeader = dom.optHeader.checked;
    config.parallel = dom.optParallel.checked;
    save();
  }));
  config.keepLines = !!config.keepLines; dom.optKeep.checked = config.keepLines;
  config.translateHeader = !!config.translateHeader; dom.optHeader.checked = config.translateHeader;
  config.parallel = !!config.parallel; dom.optParallel.checked = config.parallel;
  dom.glossary.value = config.glossary || "";
  dom.glossary.addEventListener("input", () => {
    config.glossary = dom.glossary.value; save(); refreshRun();
  });

  // ------------------------------------------------------------------ dépôt de fichiers
  function extOf(name) { return (name.split(".").pop() || "").toLowerCase(); }
  function addFiles(list) {
    const incoming = Array.from(list || []);
    if (!incoming.length) return;
    incoming.forEach((file) => {
      if (state.files.some((f) => f.name === file.name && f.size === file.size)) return;
      const known = EXT_OK.has(extOf(file.name));
      state.files.push({
        id: "f" + ++uid, file, name: file.name, size: file.size,
        status: known ? "prêt" : "à vérifier", jobId: null, error: null, warnings: [], job: null,
      });
    });
    renderList();
    refreshRun();
  }
  dom.drop.addEventListener("click", () => dom.fileInput.click());
  dom.drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); dom.fileInput.click(); } });
  dom.fileInput.addEventListener("change", (e) => { addFiles(e.target.files); dom.fileInput.value = ""; });
  ["dragenter", "dragover"].forEach((ev) => dom.drop.addEventListener(ev, (e) => { e.preventDefault(); dom.drop.classList.add("is-over"); }));
  ["dragleave", "drop"].forEach((ev) => dom.drop.addEventListener(ev, (e) => { e.preventDefault(); dom.drop.classList.remove("is-over"); }));
  dom.drop.addEventListener("drop", (e) => addFiles(e.dataTransfer && e.dataTransfer.files));
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => e.preventDefault());

  function removeFile(id) {
    const f = state.files.find((x) => x.id === id);
    if (f && f.jobId && f.status === "traduction") cancelJob(f.jobId);
    state.files = state.files.filter((x) => x.id !== id);
    renderList(); refreshRun();
  }

  const ICON = {
    file: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5Z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M14 3v5h5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>',
    done: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 13 4 4 10-10" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    err: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 8v5m0 3h.01M12 3 2.5 20h19L12 3Z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>',
  };

  function renderList() {
    dom.list.innerHTML = "";
    state.files.forEach((f) => {
      const li = el("li");
      li.dataset.id = f.id;
      if (f.status === "traduction") li.classList.add("is-run");
      if (f.status === "erreur") li.classList.add("is-err");
      const pct = f.total ? Math.round((f.done / f.total) * 100) : 0;
      const stateText = {
        "prêt": bytes(f.size) + " · prêt",
        "à vérifier": bytes(f.size) + " · format inconnu, on tentera",
        "attente": "en file",
        "traduction": esc(f.stage || "traduction") + (f.total ? ` · ${num(f.done)}/${num(f.total)} passages` : ""),
        "termine": "traduit · " + bytes(f.outSize || f.size) + (f.elapsed >= 1 ? " · " + dur(f.elapsed) : " · instantané"),
        "erreur": "échec",
        "annule": "interrompu",
      }[f.status] || esc(f.status);
      const bar = f.status === "traduction"
        ? `<div class="f-bar ${f.total ? "" : "indet"}"><i style="width:${pct}%"></i></div>` : "";
      li.innerHTML = `
        <span class="f-icon">${f.status === "termine" ? ICON.done : f.status === "erreur" ? ICON.err : ICON.file}</span>
        <div class="f-main">
          <div class="f-name">${esc(f.name)}</div>
          <div class="f-meta"><span>${stateText}</span>${f.kindLabel ? `<span class="kind">${esc(f.kindLabel)}</span>` : ""}${f.eta ? `<span>≈ ${dur(f.eta)}</span>` : ""}</div>
          ${bar}
        </div>
        <div class="f-state">
          ${f.status === "traduction" ? '<span class="spin"></span>' : ""}
          ${f.status === "termine" ? `<a class="btn small" href="${f.href || ('/api/jobs/' + f.jobId + '/file')}" download>Télécharger</a>` : ""}
          <button class="f-x" title="Retirer" data-x="${f.id}">×</button>
        </div>
        ${f.error ? `<p class="f-err">${esc(f.error)}</p>` : ""}
        ${!f.error && f.warnings && f.warnings.length ? `<p class="f-warn">${esc(f.warnings[0])}</p>` : ""}`;
      dom.list.appendChild(li);
    });
    $$("[data-x]", dom.list).forEach((b) => b.addEventListener("click", () => removeFile(b.dataset.x)));
    const ready = state.files.filter((f) => ["prêt", "erreur", "annule"].includes(f.status)).length;
    dom.note.hidden = state.files.length > 0;
    if (ready > 1) dom.note.textContent = ready + " fichiers en file.";
  }

  // texte collé ---------------------------------------------------------- #
  dom.paste.addEventListener("input", () => {
    const n = dom.paste.value.length;
    dom.pasteCount.textContent = num(n) + " caractère" + (n > 1 ? "s" : "") + (n > 3000 ? " · moteur gratuit : comptez " + dur(n / (RATE[state.engine] || RATE.auto)) : "");
  });

  // ------------------------------------------------------------------ bouton
  function pendingFiles() {
    const fromPaste = state.tab === "text" && dom.paste.value.trim().length > 0;
    return state.files.filter((f) => f.status === "prêt" || f.status === "erreur" || f.status === "annule").concat(fromPaste ? [{ id: "paste", paste: true }] : []);
  }
  function totalChars() {
    let chars = 0;
    state.files.forEach((f) => { if (f.status === "prêt" || f.status === "erreur") chars += Math.min(f.size, 4000000); });
    if (state.tab === "text") chars += dom.paste.value.length;
    return Math.round(chars * 1.05);
  }
  function refreshRun() {
    const n = pendingFiles().length;
    dom.run.disabled = !n || state.busy;
    dom.runLabel.textContent = n > 1 ? `Traduire ${n} fichiers` : n === 1 ? "Traduire" : "Ajoutez un fichier";
    dom.clear.hidden = state.files.length === 0 || state.busy;
    const chars = totalChars();
    const queued = state.files.filter((f) => ["prêt", "erreur", "annule"].includes(f.status)).length;
    const extra = state.tab === "text" && queued
      ? ` · ${queued} fichier${queued > 1 ? "s" : ""} encore en file, onglet Fichiers` : "";
    if (!n) { dom.hint.textContent = ""; }
    else {
      const rate = RATE[config.engine] || RATE.auto;
      const secs = chars / rate;
      const engineName = (state.engines.find((e) => e.id === config.engine) || { label: "Automatique" }).label;
      dom.hint.innerHTML = `<b>${num(chars)}</b> caractères environ · ${esc(engineName)} · ${
        secs < 25 ? "quelques secondes" : "environ " + dur(secs)
      }${state.src === "auto" ? " · langue source devinée" : ""}${extra}`;
    }
    if (state.src !== "auto") {
      dom.detect.textContent = label(state.src) + " → " + label(state.tgt);
    } else {
      dom.detect.textContent = "source devinée · visé : " + label(state.tgt);
    }
  }
  dom.clear.addEventListener("click", () => {
    state.files = state.files.filter((f) => f.status === "traduction");
    renderList(); refreshRun();
  });

  // ------------------------------------------------------------------ exécution
  function metaHeader() {
    const cfg = JSON.parse(JSON.stringify(config));
    cfg.engine = config.engine;
    cfg.tone = state.tone;
    Object.keys(cfg).forEach((k) => {
      if (cfg[k] && typeof cfg[k] === "object") {
        cfg[k].tone = state.tone;
        if (dom.glossary.value.trim()) cfg[k].glossary = dom.glossary.value.trim();
      }
    });
    return cfg;
  }
  function entryTarget(entry) {
    if (entry.paste) {
      const clean = (dom.pasteName.value.trim() || "texte-colle").replace(/[^\w\-.À-ÿ ]+/g, "");
      return { blob: new Blob([dom.paste.value], { type: "text/plain;charset=utf-8" }), name: clean + ".txt" };
    }
    return { blob: entry.file, name: entry.name };
  }
  function metaFor(name) {
    return {
      filename: name, src: state.src, tgt: state.tgt,
      options: { keep_lines: !!config.keepLines, translate_header: !!config.translateHeader },
      config: metaHeader(),
    };
  }

  async function startJob(entry) {
    const { blob, name } = entryTarget(entry);
    const meta = encodeURIComponent(JSON.stringify(metaFor(name)));
    const res = await fetch("/api/jobs", { method: "POST", headers: { "X-Meta": meta }, body: blob, signal: state.abort && state.abort.signal });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || "HTTP " + res.status);
    return data.id;
  }

  async function poll(jobId, onTick) {
    const started = Date.now();
    while (true) {
      await new Promise((r) => setTimeout(r, Date.now() - started < 6000 ? 520 : 1100));
      let job;
      try { job = await (await fetch("/api/jobs/" + jobId, { signal: state.abort && state.abort.signal })).json(); }
      catch (e) { if (state.abort && state.abort.signal.aborted) throw e; continue; }
      onTick(job);
      if (job.state === "termine" || job.state === "erreur" || job.state === "annule") return job;
    }
  }
  async function cancelJob(jobId) {
    try { await fetch("/api/jobs/" + jobId + "/cancel", { method: "POST" }); } catch (e) { /* déjà fini */ }
  }

  // ------------------------------------------------- aller-retours du mode sans état
  // Sur un hébergeur « serverless », rien ne survit entre deux requêtes : c'est donc
  // le navigateur qui tient les morceaux et rejoue le fichier à chaque appel.
  async function postJson(url, obj, signal) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(obj),
      signal,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.error) throw new Error(data.error || "HTTP " + res.status);
    return data;
  }
  function toB64(blob) {
    return new Promise((resolve, reject) => {
      const fr = new FileReader();
      fr.onload = () => resolve(String(fr.result).slice(String(fr.result).indexOf(",") + 1));
      fr.onerror = () => reject(new Error("Lecture du fichier impossible."));
      fr.readAsDataURL(blob);
    });
  }
  function fromB64(b64, type) {
    const bin = atob(b64 || "");
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
    return new Blob([bytes], { type: type || "application/octet-stream" });
  }
  const mimeOf = (name) => ({
    txt: "text/plain", md: "text/markdown", srt: "application/x-subrip", vtt: "text/vtt",
    csv: "text/csv", json: "application/json", html: "text/html", xml: "text/xml",
  }[String(name || "").split(".").pop()] || "application/octet-stream");

  // ZIP sans compression : en-tête local, table CRC-32, répertoire central. Écrit à
  // la main pour que « tout télécharger » marche sans serveur pour le faire.
  const CRC_T = (() => {
    const t = new Uint32Array(256);
    for (let n = 0; n < 256; n += 1) {
      let c = n;
      for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      t[n] = c >>> 0;
    }
    return t;
  })();
  function crc32(bytes) {
    let c = 0xffffffff;
    for (let i = 0; i < bytes.length; i += 1) c = CRC_T[(c ^ bytes[i]) & 255] ^ (c >>> 8);
    return (c ^ 0xffffffff) >>> 0;
  }
  const cat = (list) => {
    const total = list.reduce((a, b) => a + b.length, 0);
    const out = new Uint8Array(total);
    let at = 0;
    list.forEach((b) => { out.set(b, at); at += b.length; });
    return out;
  };
  function zipStore(files) {
    const enc = new TextEncoder();
    const le = (n) => new Uint8Array([n & 255, (n >>> 8) & 255, (n >>> 16) & 255, (n >>> 24) & 255]);
    const sh = (n) => new Uint8Array([n & 255, (n >>> 8) & 255]);
    const now = new Date();
    const dosTime = (now.getHours() << 11) | (now.getMinutes() << 5) | (now.getSeconds() >> 1);
    const dosDate = ((now.getFullYear() - 1980) << 9) | ((now.getMonth() + 1) << 5) | now.getDate();
    const locals = [];
    const centrals = [];
    let offset = 0;
    files.forEach((f) => {
      const name = enc.encode(f.name);
      const body = f.bytes;
      const crc = crc32(body);
      const head = cat([
        le(0x04034b50), sh(20), sh(0x0800), sh(0), sh(dosTime), sh(dosDate),
        le(crc), le(body.length), le(body.length), sh(name.length), sh(0),
      ]);
      locals.push(cat([head, name, body]));
      centrals.push(cat([
        le(0x02014b50), sh(20), sh(20), sh(0x0800), sh(0), sh(dosTime), sh(dosDate),
        le(crc), le(body.length), le(body.length), sh(name.length),
        sh(0), sh(0), sh(0), sh(0), le(0), le(offset), name,
      ]));
      offset += head.length + name.length + body.length;
    });
    const cd = cat(centrals);
    const tail = cat([
      le(0x06054b50), sh(0), sh(0), sh(files.length), sh(files.length),
      le(cd.length), le(offset), sh(0),
    ]);
    return new Blob([cat(locals), cd, tail], { type: "application/zip" });
  }

  async function runStateless(entry, token, tick) {
    const t0 = Date.now();
    const { blob, name } = entryTarget(entry);
    const meta = {
      filename: name, src: state.src, tgt: state.tgt,
      options: { keep_lines: !!config.keepLines, translate_header: !!config.translateHeader },
      config: metaHeader(),
    };
    const file = await toB64(blob);
    const plan = await postJson("/api/open", { meta, file }, state.abort && state.abort.signal);
    const total = plan.count || 1;
    const translations = Object.assign({}, plan.cached || {});
    const used = [];
    const warnings = [];
    const win = plan.window || { items: 40, chars: 9000 };
    const stopped = () => runToken !== token;
    tick({ stage: plan.cached && !plan.pending.length ? "cache" : "traduction", total, done: Object.keys(translations).length });

    let i = 0;
    let chars = 0;
    while (i < plan.pending.length) {
      if (stopped()) return Object.assign(entry, { status: "annule", stage: "annulé" });
      const group = [];
      let room = 0;
      while (i < plan.pending.length && group.length < win.items
             && room + (plan.pending[i].t || "").length <= win.chars) {
        room += (plan.pending[i].t || "").length;
        group.push(plan.pending[i]);
        i += 1;
      }
      const res = await postJson("/api/translate", { meta, items: group.map((g) => g.t) }, state.abort && state.abort.signal);
      chars += room;
      res.translations.forEach((v, n) => { translations[String(group[n].i)] = v; });
      (res.engines || []).forEach((e) => { if (!used.includes(e)) used.push(e); });
      (res.errors || []).forEach((w) => { if (!warnings.includes(w)) warnings.push(w); });
      const engineName = (state.engines.find((e) => e.id === res.engine) || { label: res.engine }).label;
      tick({
        stage: "traduction · " + (engineName || ""),
        total, done: Math.min(total, Object.keys(translations).length), warnings: warnings.slice(),
      });
    }

    const out = await postJson("/api/build", { meta, file, translations }, state.abort && state.abort.signal);
    const built = fromB64(out.b64, mimeOf(out.name));
    Object.assign(entry, {
      status: "termine", stage: "prêt", total, done: total, chars,
      warnings, engines: used, elapsed: (Date.now() - t0) / 1000,
      outSize: out.size, outName: out.name, kindLabel: out.kindLabel,
      detected: plan.src,
      result: {
        name: plan.name, kind: out.kind, kindLabel: out.kindLabel, note: out.note,
        in: out.in, out: out.out, outName: out.name, size: out.size,
      },
      href: URL.createObjectURL(built),
      outBlob: built,
    });
    return undefined;
  }

  let runToken = 0;
  async function runAll() {
    const queue = pendingFiles();
    if (!queue.length) return;
    if (!state.metaOk) {
      // une instance qui démarre peut mettre une seconde à répondre : mieux vaut
      // patienter un peu que traduire dans le mode que l'hébergeur ne sait pas tenir
      await Promise.race([loadMeta(), new Promise((r) => setTimeout(r, 2500))]);
    }
    const token = ++runToken;
    state.abort = new AbortController();
    state.busy = true;
    dom.run.disabled = true;
    dom.cancel.hidden = false;
    dom.results.hidden = true;
    queue.forEach((f) => { if (!f.paste) { f.status = "attente"; f.error = null; f.warnings = []; } });
    renderList();

    const workers = config.parallel ? Math.min(3, queue.length) : 1;
    const cursor = { i: 0 };
    async function worker() {
      while (cursor.i < queue.length) {
        if (runToken !== token) return;
        const entry = queue[cursor.i++];
        if (!entry.paste) entry.status = "traduction";
        renderList();
        const tick = (p) => {
          if (runToken !== token) return;
          Object.assign(entry, p);
          renderList();
        };
        try {
          if (state.mode === "stateless") {
            await runStateless(entry, token, tick);
          } else {
            const jobId = await startJob(entry);
            entry.jobId = jobId;
            const final = await poll(jobId, (job) => tick({
              status: job.state === "attente" ? "attente" : "traduction",
              stage: job.stage, total: job.total, done: job.done, eta: job.eta,
              kindLabel: job.kindLabel, warnings: job.warnings || [],
            }));
            Object.assign(entry, {
              status: final.state, total: final.total, done: final.done, stage: final.stage,
              warnings: final.warnings || [], error: final.error, kindLabel: final.kindLabel,
              outSize: final.outSize, outName: final.outName, elapsed: final.elapsed,
              engines: final.engines, chars: final.chars, detected: final.src,
            });
            if (final.state === "termine") {
              entry.result = await (await fetch("/api/jobs/" + jobId + "/result")).json();
            }
          }
        } catch (err) {
          const cut = runToken !== token || (err && err.name === "AbortError");
          Object.assign(entry, cut
            ? { status: "annule", stage: "annulé" }
            : { status: "erreur", error: (err && err.message) || "erreur réseau" });
        }
        renderList();
      }
    }
    await Promise.all(Array.from({ length: workers }, worker));
    state.abort = null;
    if (runToken !== token) return;
    state.busy = false;
    dom.cancel.hidden = true;
    if (state.detectedFromServer === undefined) { /* noop */ }
    const ok = queue.filter((q) => q.status === "termine");
    if (ok.length) {
      state.detected = ok[0].detected;
      if (state.src === "auto" && ok[0].detected) {
        dom.detect.textContent = "détecté : " + label(ok[0].detected) + " → " + label(state.tgt);
      }
    }
    renderResults(queue);
    refreshRun();
    loadMeta();
    const errs = queue.filter((q) => q.status === "erreur");
    if (errs.length && !ok.length) toast(errs[0].error || "Échec de la traduction.", 5000);
    else if (ok.length) toast(ok.length + " fichier" + (ok.length > 1 ? "s" : "") + " traduit" + (ok.length > 1 ? "s" : "") + " · " + dur(ok.reduce((a, b) => a + (b.elapsed || 0), 0)), 3400);
  }
  dom.run.addEventListener("click", runAll);
  dom.cancel.addEventListener("click", async () => {
    runToken++;
    if (state.abort) state.abort.abort();   // coupe sur-le-champ les requêtes en vol
    state.busy = false;
    dom.cancel.hidden = true;
    const ids = state.files.filter((f) => f.status === "traduction" && f.jobId).map((f) => f.jobId);
    await Promise.all(ids.map(cancelJob));
    state.files.forEach((f) => { if (f.status === "traduction") { f.status = "annule"; } });
    renderList(); refreshRun();
    toast("Interrompu.");
  });
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && !dom.run.disabled) { e.preventDefault(); runAll(); }
  });

  // ------------------------------------------------------------------ résultats
  function renderResults(list) {
    const done = (list || []).filter((f) => f.status === "termine" && f.result);
    if (!done.length) { dom.results.hidden = true; return; }
    dom.results.hidden = false;
    dom.resultsBody.innerHTML = "";
    done.forEach((f) => {
      const box = el("div", "result is-open");
      const r = f.result;
      const words = (r.out || "").trim().split(/\s+/).filter(Boolean).length;
      const engine = (f.engines || []).map((e) => (state.engines.find((x) => x.id === e) || { label: e }).label).join(" · ");
      box.innerHTML = `
        <div class="result-head">
          <h3>${esc(r.name)}</h3>
          <span class="stat">${esc(r.kindLabel || "")} · ${num((r.out || "").length)} car. · ${num(words)} mots${f.elapsed ? " · rendu en " + dur(Math.max(f.elapsed, 0.4)) : ""}${engine ? " · " + esc(engine) : ""}</span>
          <div class="result-actions">
            <button class="btn small quiet" data-copy>Copier</button>
            <a class="btn small" href="${f.href || '/api/jobs/' + f.jobId + '/file'}" download>${esc(r.outName || "traduction")}</a>
          </div>
        </div>
        ${f.warnings && f.warnings.length ? `<p class="f-warn" style="grid-column:auto">${esc(f.warnings[0])}</p>` : ""}
        <div class="diff">
          <div class="pane"><header><span>Original — ${esc(label(f.detected || state.src))}</span></header><pre>${esc(crop(r.in))}</pre></div>
          <div class="pane out"><header><span>Traduction — ${esc(label(state.tgt))}</span><button class="toggle" data-t></button></header><pre>${esc(crop(r.out))}</pre></div>
        </div>
        ${r.size > 3000000 ? "" : ""}
        ${r.note ? `<p class="bin-note"><b>${esc(r.kindLabel || "Fichier")} :</b> ${esc(r.note)}</p>`
          : (["xlsx", "docx"].includes(r.kind) ? `<p class="bin-note">Fichier <b>${esc(r.kindLabel)}</b> reconstruit : l’aperçu texte sert de contrôle, le document complet s’obtient par le téléchargement.</p>` : "")}`;
      $$("[data-t]", box).forEach((b) => b.addEventListener("click", () => box.classList.toggle("is-open")));
      $("[data-copy]", box).addEventListener("click", async () => {
        try { await navigator.clipboard.writeText(r.out || ""); toast("Traduction copiée."); }
        catch (e) {
          const ta = el("textarea"); ta.value = r.out || ""; document.body.appendChild(ta); ta.select();
          document.execCommand("copy"); ta.remove(); toast("Traduction copiée.");
        }
      });
      dom.resultsBody.appendChild(box);
    });
    const ids = done.map((f) => f.jobId).filter(Boolean);
    dom.zip.hidden = done.length < 2;
    dom.zip.dataset.ids = ids.join(",");
    dom.zip.dataset.local = done.every((f) => f.outBlob) ? "1" : "";
  }
  function crop(text) {
    const t = String(text || "");
    return t.length > 9000 ? t.slice(0, 9000) + "\n\n… (aperçu tronqué — le fichier complet tient dans le téléchargement)" : t;
  }
  dom.zip.addEventListener("click", async () => {
    if (dom.zip.dataset.local) {
      const enc = new TextEncoder();
      const seen = {};
      const files = [];
      for (const f of state.files.filter((x) => x.status === "termine" && x.outBlob)) {
        let name = f.result ? f.result.outName : "traduction.txt";
        if (seen[name]) { const dot = name.lastIndexOf("."); name = name.slice(0, dot) + "-" + (++seen[name]) + name.slice(dot); }
        else seen[name] = 1;
        files.push({ name, bytes: enc.encode(await f.outBlob.text()) });
      }
      const url = URL.createObjectURL(zipStore(files));
      const a = document.createElement("a");
      a.href = url; a.download = "tradfilez-traductions.zip"; a.click();
      setTimeout(() => URL.revokeObjectURL(url), 8000);
      return;
    }
    window.location.href = "/api/zip?ids=" + dom.zip.dataset.ids;
  });
  dom.collapse.addEventListener("click", () => $$(".result", dom.resultsBody).forEach((b) => b.classList.remove("is-open")));

  // ------------------------------------------------------------------ réglages
  function renderEngines() {
    dom.engineList.innerHTML = "";
    if (!state.engines.length) {
      dom.engineList.innerHTML = '<p class="engine-hint" style="margin-left:0">Lecture du catalogue des moteurs…</p>';
      return;
    }
    state.engines.forEach((e) => {
      const box = el("div", "engine" + (config.engine === e.id ? " is-on" : ""));
      box.dataset.id = e.id;
      const needsKey = e.needsKey && !e.ready;
      const fields = e.fields || [];
      box.innerHTML = `
        <label class="engine-top">
          <input type="radio" name="enginePick" value="${e.id}" ${config.engine === e.id ? "checked" : ""}>
          <b>${esc(e.label)}</b>
          <span class="badge ${e.ready ? "on" : ""}">${e.id === "auto" ? "défaut" : e.ready ? (e.needsKey ? "clé reconnue" : "sans clé") : "clé requise"}</span>
        </label>
        <p class="engine-hint">${esc(e.hint || "")}</p>
        ${config.engine === e.id && fields.length ? `<div class="engine-fields">${fields.map((fd) => fieldHtml(e.id, fd)).join("")}</div>` : ""}`;
      dom.engineList.appendChild(box);
    });
    $$("input[name=enginePick]", dom.engineList).forEach((input) => input.addEventListener("change", () => {
      config.engine = input.value; save(); renderEngines(); refreshRun(); updateChip();
    }));
    $$(".field input", dom.engineList).forEach((input) => input.addEventListener("input", () => {
      const eng = input.dataset.eng, key = input.dataset.key;
      config[eng] = config[eng] || {};
      config[eng][key] = input.value;
    }));
  }
  function fieldHtml(eng, fd) {
    const val = (config[eng] && config[eng][fd.name]) || "";
    return `<div class="field"><label for="fld-${eng}-${fd.name}">${esc(fd.label)}</label>
      <input id="fld-${eng}-${fd.name}" data-eng="${eng}" data-key="${fd.name}" type="${fd.type}"
        value="${esc(val)}" placeholder="${esc(fd.placeholder || "")}" ${fd.type === "password" ? 'autocomplete="off" spellcheck="false"' : ""}></div>`;
  }
  function updateChip() {
    const e = state.engines.find((x) => x.id === config.engine) || {};
    dom.chipLabel.textContent = e.id === "auto" ? "Automatique" : (e.label || config.engine);
    dom.chipSwatch.className = "swatch " + (e.ready ? (e.needsKey ? "key" : "free") : "");
  }
  let metaRetry = 0;
  async function loadMeta() {
    try {
      const res = await fetch("/api/meta", { headers: { "X-Config": encodeURIComponent(JSON.stringify(config)) } });
      const meta = await res.json();
      state.engines = meta.engines || [];
      state.cache = meta.cache || {};
      state.mode = (meta.mode === "stateless") ? "stateless" : "files";
      state.maxUpload = (meta.limits || {}).maxUpload || 8388608;
      state.metaOk = true;
      try { localStorage.setItem(LS + ".mode", state.mode); } catch (e) { /* stockage privé */ }
      metaRetry = 0;
      try { localStorage.setItem(LS + ".catalogue", JSON.stringify(state.engines)); } catch (e) {}
      dom.cacheStat.textContent = state.cache.pairs ? `cache : ${num(state.cache.pairs)} correspondances gardées en mémoire locale` : "cache : rien de conservé pour l’instant";
      updateChip();
      if (dom.settings.hidden === false) renderEngines();
    } catch (e) {
      // le serveur redémarre peut-être : on retente une fois, en douceur
      if (metaRetry < 2) { metaRetry += 1; setTimeout(loadMeta, 1500 * metaRetry); }
    }
  }
  const openSheet = () => {
    dom.settings.hidden = false;
    renderEngines();
    dom.probeOut.textContent = "";
    loadMeta().then(renderEngines);
  };
  dom.chip.addEventListener("click", openSheet);
  $("#openSettings").addEventListener("click", openSheet);
  $("#closeSettings").addEventListener("click", () => (dom.settings.hidden = true));
  $("#settingsCancel").addEventListener("click", () => { save(); dom.settings.hidden = true; });
  $("#settingsSave").addEventListener("click", async () => {
    save();
    await loadMeta();
    dom.settings.hidden = true;
    renderEngines();
    toast("Réglages enregistrés dans ce navigateur.");
  });
  dom.settings.addEventListener("click", (e) => { if (e.target === dom.settings) dom.settings.hidden = true; });
  dom.probe.addEventListener("click", async () => {
    dom.probeOut.className = "probe-out";
    dom.probeOut.textContent = "test en cours…";
    save();
    try {
      const res = await fetch("/api/engine/probe", { method: "POST", headers: { "X-Config": encodeURIComponent(JSON.stringify(config)) }, body: JSON.stringify(config) });
      const data = await res.json();
      if (data.ok) {
        dom.probeOut.className = "probe-out ok";
        dom.probeOut.textContent = (data.label || "") + " ✓ — " + (data.sample || "").slice(0, 70);
      } else {
        dom.probeOut.className = "probe-out err";
        dom.probeOut.textContent = (data.label || "moteur") + " ✗ " + (data.error || "erreur");
        if (data.fallback && data.fallback.ok) dom.probeOut.textContent += " · repli " + data.fallback.label + " ✓";
      }
    } catch (e) {
      dom.probeOut.className = "probe-out err";
      dom.probeOut.textContent = "serveur injoignable";
    }
  });
  $("#clearCache").addEventListener("click", async () => {
    await fetch("/api/cache/clear", { method: "POST" });
    await loadMeta();
    toast("Cache de correspondances vidé.");
  });

  // ------------------------------------------------------------------ démo / amorçage
  try {
    const seeded = JSON.parse(localStorage.getItem(LS + ".catalogue") || "[]");
    if (Array.isArray(seeded) && seeded.length) state.engines = seeded;
  } catch (e) { /* premier lancement */ }
  paintLang("src"); paintLang("tgt");
  renderList(); refreshRun(); loadMeta();
  window.addEventListener("focus", loadMeta);
})();
