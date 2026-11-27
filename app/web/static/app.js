/* mCode Studio desktop IDE (no build step, no framework: it runs on a Pi).
   Every action goes through mCode's API; nothing here builds, flashes or edits
   files on its own. Flashing always shows mCode's checks and asks first. */
"use strict";

// ------------------------------------------------------------------ helpers
const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => Array.from(el.querySelectorAll(sel));
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
  (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style") el.setAttribute("style", v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.appendChild(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const fmtTime = (t) => t ? new Date(t * 1000).toLocaleString([], {month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"}) : "";
const fmtBytes = (n) => n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : n > 1024 ? Math.round(n / 1024) + " KB" : n + " B";
const store = {
  get(k, d) { try { const v = localStorage.getItem("mcode." + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem("mcode." + k, JSON.stringify(v)); } catch (e) { /* private mode */ } },
};

async function api(method, path, body) {
  const options = {method, credentials: "same-origin", headers: {"X-mCode": "1"}};
  if (body !== undefined) { options.headers["Content-Type"] = "application/json"; options.body = JSON.stringify(body); }
  let response;
  try { response = await fetch("/api/" + path, options); }
  catch (e) { throw new Error("mCode is not reachable."); }
  if (response.status === 401) { location.href = "/"; throw new Error("Sign in again."); }
  const ctype = response.headers.get("Content-Type") || "";
  const data = ctype.includes("json") ? await response.json() : await response.blob();
  if (!response.ok) { const err = new Error(data.error || response.statusText); err.fix = data.fix; throw err; }
  return data;
}
const GET = (p) => api("GET", p);
const POST = (p, b) => api("POST", p, b || {});

function toast(text, tone = "", ms = 4000) {
  const el = h("div", {class: "toast " + tone}, text);
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), ms);
}
function fail(e) { toast(e.message || String(e), "err", 6000); if (e.fix) handleFix(e.fix); }

// ------------------------------------------------------------------- state
const S = {
  settings: {}, fields: [], state: null, project: null, tree: [], collapsed: new Set(store.get("collapsed", [])),
  files: new Map(), active: null, devices: [], selectedPort: "", ai: null, view: store.get("view", "dashboard"),
  jobs: new Map(), outputJob: null, diagnostics: [], monitor: {lines: [], connected: false, paused: false, port: "", baud: 115200, last: 0},
  terms: new Map(), activeTerm: null, attachments: [], boards: null, conversation: [], review: null, turn: null,
};
const BAUDS = [300, 1200, 2400, 4800, 9600, 19200, 38400, 57600, 74880, 115200, 230400, 460800, 921600, 1500000, 2000000];

// ------------------------------------------------------------------ editor
const ARDUINO_WORDS = ("setup loop pinMode digitalWrite digitalRead analogRead analogWrite analogReference " +
  "delay delayMicroseconds millis micros pulseIn pulseInLong shiftIn shiftOut tone noTone attachInterrupt " +
  "detachInterrupt interrupts noInterrupts map constrain random randomSeed bitRead bitWrite bitSet bitClear " +
  "Serial Serial1 Serial2 Wire SPI WiFi WiFiClient WiFiServer HTTPClient Preferences ledcAttach ledcWrite " +
  "ledcAttachPin ledcSetup touchRead hallRead esp_restart esp_deep_sleep_start ESP xTaskCreate " +
  "xTaskCreatePinnedToCore vTaskDelay pdMS_TO_TICKS portMAX_DELAY xQueueCreate xQueueSend xQueueReceive " +
  "xSemaphoreCreateMutex xSemaphoreTake xSemaphoreGive INPUT OUTPUT INPUT_PULLUP INPUT_PULLDOWN HIGH LOW " +
  "LED_BUILTIN A0 A1 A2 A3 A4 A5 F PROGMEM begin end print println printf available read write flush " +
  "beginTransmission endTransmission requestFrom setClock transfer beginTransaction endTransaction " +
  "ESP_LOGI ESP_LOGW ESP_LOGE ESP_LOGD gpio_set_level gpio_get_level gpio_config gpio_set_direction " +
  "i2c_master_write_to_device uart_write_bytes uart_read_bytes nvs_flash_init esp_err_t ESP_OK app_main").split(" ");
const CPP_WORDS = ("auto bool break case char class const constexpr continue default delete do double else enum " +
  "extern false float for if inline int long namespace new nullptr private protected public return short " +
  "signed sizeof static static_cast struct switch template this true typedef typename union unsigned " +
  "using virtual void volatile while uint8_t uint16_t uint32_t uint64_t int8_t int16_t int32_t int64_t " +
  "size_t String byte word boolean #include #define #ifdef #ifndef #endif #pragma").split(" ");
(function defineArduino() {
  const base = CodeMirror.mimeModes["text/x-c++src"];
  const builtin = {}; ARDUINO_WORDS.forEach((w) => { builtin[w] = true; });
  CodeMirror.defineMIME("text/x-arduino", Object.assign({}, base, {builtin}));
})();
const MODES = {c: "text/x-csrc", cpp: "text/x-c++src", arduino: "text/x-arduino", python: "python",
  json: {name: "javascript", json: true}, yaml: "yaml", xml: "xml", markdown: "markdown", ini: "text/x-properties",
  cmake: "cmake", shell: "shell", makefile: "text/plain", text: "text/plain"};

let cm = null;
function hint(editor) {
  const cur = editor.getCursor();
  const token = editor.getTokenAt(cur);
  const start = /[\w#]/.test(token.string) ? token.start : cur.ch;
  const prefix = editor.getLine(cur.line).slice(start, cur.ch);
  const words = new Set();
  const any = CodeMirror.hint.anyword(editor, {word: /[\w$]+/});
  (any && any.list || []).forEach((w) => words.add(w));
  ARDUINO_WORDS.concat(CPP_WORDS).forEach((w) => { if (w.startsWith(prefix) && w !== prefix) words.add(w); });
  const list = Array.from(words).filter((w) => w.startsWith(prefix) && w !== prefix).sort();
  return {list: list.slice(0, 80), from: CodeMirror.Pos(cur.line, start), to: cur};
}
function makeEditor() {
  cm = CodeMirror($("#editor-host"), {
    theme: "mcode", lineNumbers: true, foldGutter: true, gutters: ["CodeMirror-linenumbers", "diag", "CodeMirror-foldgutter"],
    matchBrackets: true, autoCloseBrackets: true, styleActiveLine: true, indentUnit: 2, tabSize: 2,
    extraKeys: {
      "Ctrl-S": () => saveFile(), "Cmd-S": () => saveFile(), "Shift-Ctrl-S": () => saveAs(),
      "Ctrl-Space": "autocomplete",
      "Ctrl-/": "toggleComment", "Cmd-/": "toggleComment", "Ctrl-H": "replace", "Ctrl-G": "jumpToLine",
      "Ctrl-B": () => runBuild("build"), "Ctrl-Q": (c) => c.foldCode(c.getCursor()),
      "Tab": (c) => c.somethingSelected() ? c.indentSelection("add") : c.replaceSelection("  "),
    },
    hintOptions: {hint, completeSingle: false},
  });
  cm.on("cursorActivity", () => { const p = cm.getCursor(); $("#sb-cursor").textContent = `Ln ${p.line + 1}, Col ${p.ch + 1}`; });
  cm.on("inputRead", (c, change) => { if (/[\w.]/.test(change.text[0]) && store.get("complete", true)) c.showHint({completeSingle: false}); });
  applyEditorSettings();
}
function applyEditorSettings() {
  document.documentElement.dataset.theme = S.settings["ui.theme"] || "dark";
  document.documentElement.style.setProperty("--font-size", (S.settings["ui.font_size"] || 14) + "px");
  const main = $("#main");
  const layout = S.settings["ui.layout"] || "ai-right";
  main.classList.toggle("ai-left", layout === "ai-left");
  const ai = $("#ai"); const tabs = $("#bottom-tabs");
  let tab = $('#bottom-tabs button[data-panel="ai"]');
  if (layout === "ai-bottom") {
    if (!tab) {
      tab = h("button", {"data-panel": "ai"}, "AI Engineer");
      tab.addEventListener("click", () => showPanel("ai"));
      tabs.insertBefore(tab, tabs.children[1]);
      const panel = h("div", {class: "panel hidden", "data-panel": "ai"});
      $("#bottom").appendChild(panel);
    }
    $('#bottom .panel[data-panel="ai"]').appendChild(ai);
    ai.classList.add("in-bottom");
    $("#ai-gutter").classList.add("hidden");
  } else if (ai.parentElement !== main) {
    main.insertBefore(ai, null);
    main.insertBefore($("#ai-gutter"), ai);
    ai.classList.remove("in-bottom");
    $("#ai-gutter").classList.remove("hidden");
    if (tab) { tab.remove(); $('#bottom .panel[data-panel="ai"]').remove(); }
  }
  if (cm) cm.refresh();
}

// ---------------------------------------------------------------- startup
async function init() {
  restoreLayout();
  makeEditor();
  bindChrome();
  await loadSettings();
  await refreshState();
  await loadBoards();
  connectEvents();
  setView(S.view);
  setInterval(tickJobStatus, 1000);
}

async function loadSettings() {
  const data = await GET("settings");
  S.fields = data.fields;
  S.settings = Object.fromEntries(data.fields.map((f) => [f.key, f.value]));
  const mode = $("#ai-mode");
  mode.innerHTML = "";
  const field = data.fields.find((f) => f.key === "ai.mode");
  for (const choice of field.choices) mode.appendChild(h("option", {value: choice, selected: choice === field.value}, field.choice_labels[choice]));
  applyEditorSettings();
}
async function loadBoards() { S.boards = await GET("boards"); }

async function refreshState() {
  try {
    S.state = await GET("state");
  } catch (e) { fail(e); return; }
  S.ai = S.state.claude;
  S.devices = S.state.devices || [];
  S.selectedPort = S.state.device ? S.state.device.port : "";
  const proj = S.state.project;
  if (proj && (!S.project || S.project.id !== proj.id)) await loadProject(proj.id);
  else if (!proj) { S.project = null; renderProjectPick(); }
  renderDevicePick(); renderClaudePill(); renderAI(); renderDashboard();
  if (S.view !== "dashboard" && S.view !== "explorer") renderSide();
}

async function loadProject(id) {
  try {
    const data = await GET("project/" + encodeURIComponent(id));
    const changed = !S.project || S.project.id !== data.id;
    S.project = data; S.tree = data.tree;
    if (changed) { closeAllTabs(); S.diagnostics = (data.last_build && data.last_build.diagnostics) || []; loadConversation(); }
    renderProjectPick(); renderSide(); renderProblems(); renderTests();
  } catch (e) { fail(e); }
}

// ------------------------------------------------------------------ events
let events = null;
function connectEvents() {
  events = new EventSource("/api/events");
  events.onopen = () => $("#sb-conn").classList.remove("off");
  events.onerror = () => $("#sb-conn").classList.add("off");
  events.onmessage = (e) => { try { const m = JSON.parse(e.data); onEvent(m.topic, m.data); } catch (err) { console.error(err); } };
}
const debounced = {};
function later(key, fn, ms = 250) { clearTimeout(debounced[key]); debounced[key] = setTimeout(fn, ms); }
function onEvent(topic, data) {
  switch (topic) {
    case "job": onJob(data); break;
    case "job.line": onJobLine(data); break;
    case "build": onBuild(data); break;
    case "upload": onUpload(data); break;
    case "devices": S.devices = data.devices || S.devices; if (data.selected) S.selectedPort = data.selected; renderDevicePick(); later("side", renderSide); later("dash", renderDashboard); renderSerialPorts(); break;
    case "ai": S.ai = data.status; if (data.turn) S.turn = data.turn; onAI(data); break;
    case "monitor": onMonitorLines(data); break;
    case "monitor.state": onMonitorState(data); break;
    case "monitor.cleared": S.monitor.lines = []; renderSerial(); break;
    case "projects": later("projects", onProjects); break;
    case "settings": later("settings", loadSettings); break;
    case "tools": case "tools.installed": later("dash", renderDashboard, 400); if (topic === "tools.installed") toast(data.state === "succeeded" ? "Installed: " + data.title.replace(/^Install /, "") : "Install failed: " + (data.error || ""), data.state === "succeeded" ? "ok" : "err"); later("state", refreshState, 600); break;
    case "terminal": onTerminal(data); break;
    case "toast": toast(data.text, data.tone === "error" ? "err" : data.tone === "success" ? "ok" : ""); break;
    case "libraries": if (S.view === "libraries") later("side", renderSide, 300); toast(`Library ${data.name}: ${data.state}`, data.state === "succeeded" ? "ok" : "err"); break;
  }
}
async function onProjects() {
  const data = await GET("projects");
  if (data.current && (!S.project || S.project.id !== data.current.id)) await loadProject(data.current.id);
  else if (S.project) { await loadProject(S.project.id); }
  renderDashboard();
}

// -------------------------------------------------------------------- jobs
function onJob(job) {
  S.jobs.set(job.id, job);
  if (S.project && job.project_id === S.project.id && ["build", "rebuild", "clean", "test", "check", "upload", "erase", "identify"].includes(job.kind)) {
    if (job.state === "running" && S.outputJob !== job.id) { S.outputJob = job.id; $("#output").textContent = ""; showPanel("output"); }
  } else if (["install", "install-lib", "uninstall-lib", "update-lib", "clone"].includes(job.kind) && job.state === "running" && S.outputJob !== job.id) {
    S.outputJob = job.id; $("#output").textContent = ""; showPanel("output");
  }
  tickJobStatus();
}
function onJobLine(d) {
  if (d.id !== S.outputJob) return;
  const out = $("#output");
  if (d.cr && out.lastChild && out.lastChild.dataset && out.lastChild.dataset.cr) out.lastChild.remove();
  const cls = /error|failed|fatal/i.test(d.line) ? "l-err" : /warning/i.test(d.line) ? "l-warn" : /^\$ /.test(d.line) ? "l-cmd" : /succe|verified|done/i.test(d.line) ? "l-ok" : "";
  const span = h("span", {class: cls}, d.line + "\n");
  if (d.cr) span.dataset.cr = "1";
  out.appendChild(span);
  while (out.childNodes.length > 4000) out.firstChild.remove();
  out.scrollTop = out.scrollHeight;
}
function tickJobStatus() {
  const running = Array.from(S.jobs.values()).filter((j) => j.state === "running" || j.state === "queued");
  const el = $("#job-status");
  if (!running.length) { el.textContent = ""; $("#job-cancel").classList.add("hidden"); $("#sb-left").textContent = S.project ? `${S.project.name} · ${S.project.board_name}` : "No project"; return; }
  const job = running[running.length - 1];
  const pct = job.progress != null ? ` ${Math.round(job.progress * 100)}%` : "";
  const elapsed = job.started ? Math.round(Date.now() / 1000 - job.started) : 0;
  el.textContent = `${job.title} · ${job.phase}${pct} · ${elapsed}s`;
  $("#sb-left").textContent = el.textContent;
  $("#job-cancel").classList.remove("hidden");
  $("#job-cancel").dataset.job = job.id;
}
function onBuild(rec) {
  if (!S.project || rec.project_id !== S.project.id) { renderDashboard(); return; }
  const ok = rec.state === "succeeded";
  const what = {build: "Build", rebuild: "Rebuild", test: "Tests", check: "Static analysis", clean: "Clean"}[rec.action] || rec.action;
  if (rec.action !== "clean") toast(ok ? `${what} succeeded${rec.size && rec.size.flash ? ` · flash ${rec.size.flash.percent}%` : ""}` : `${what} failed: ${rec.headline || ""}`, ok ? "ok" : "err", ok ? 4000 : 8000);
  loadProject(S.project.id).then(() => {
    S.diagnostics = (S.project.last_build && S.project.last_build.diagnostics) || [];
    renderProblems(); markDiagnostics(); renderTests();
    if (!ok && S.diagnostics.some((d) => d.severity === "error")) showPanel("problems");
  });
  renderDashboard();
}
function onUpload(rec) {
  const ok = rec.state === "succeeded";
  const what = {upload: "Flash", erase: "Erase", identify: "Chip detection"}[rec.action] || rec.action;
  toast(ok ? `${what} succeeded${rec.chip ? ": " + rec.chip : ""}${rec.verified ? " (verified)" : ""}` : `${what} failed: ${rec.error || ""}`, ok ? "ok" : "err", 8000);
  renderDashboard();
  if (S.view === "hardware") renderSide();
}

// ------------------------------------------------------------- top bar
function renderProjectPick() {
  $("#project-pick .pick-label").textContent = S.project ? S.project.name : "No project";
  $("#sb-left").textContent = S.project ? `${S.project.name} · ${S.project.board_name}` : "No project";
}
function deviceLabel(d) {
  // The esptool result first; core board names only where the USB ID names one board.
  if (d.chip) return d.chip;
  if (["arduino", "pico", "cdc"].includes(d.kind) && d.board_names && d.board_names.length) return d.board_names[0];
  return d.name || d.port;
}
function renderDevicePick() {
  const d = S.devices.find((x) => x.port === S.selectedPort);
  $("#device-pick .pick-label").textContent = d ? `${deviceLabel(d)} · ${d.short_port}` : S.devices.length ? `${S.devices.length} board(s): choose` : "No board";
  $("#device-dot").className = "dot " + (d ? (d.readable ? "ok" : "err") : S.devices.length ? "warn" : "");
}
function renderClaudePill() {
  const ai = S.ai || {};
  const pill = $("#claude-pill");
  const label = {ready: ai.auth && ai.auth.label || "Claude", working: "Claude is working", "signed-out": "Claude: sign in", "not-installed": "Claude: not installed", checking: "Claude…"}[ai.state] || "Claude";
  $(".pill-label", pill).textContent = label;
  $(".dot", pill).className = "dot " + ({ready: "ok", working: "warn", "signed-out": "warn", "not-installed": "err"}[ai.state] || "");
}

function showMenu(anchor, items) {
  const menu = $("#menu");
  menu.innerHTML = "";
  for (const item of items) {
    if (item === "-") { menu.appendChild(h("div", {class: "sep"})); continue; }
    menu.appendChild(h("div", {class: "mi", onclick: () => { hideMenu(); item.run(); }}, item.label, item.hint ? h("small", {}, item.hint) : null));
  }
  const r = anchor.getBoundingClientRect();
  menu.style.left = Math.min(r.left, innerWidth - 260) + "px";
  menu.style.top = r.bottom + 4 + "px";
  menu.classList.remove("hidden");
  setTimeout(() => document.addEventListener("click", hideMenu, {once: true}), 0);
}
function hideMenu() { $("#menu").classList.add("hidden"); }

async function projectMenu(anchor) {
  const data = await GET("projects");
  const items = data.projects.slice(0, 15).map((p) => ({label: p.name, hint: p.board_name, run: () => openProject(p.id)}));
  if (items.length) items.push("-");
  items.push({label: "Search projects…", run: searchProjects});
  items.push({label: "New project…", run: newProjectWizard});
  items.push({label: "Open a folder…", run: openFolderDialog});
  items.push({label: "Import (copy) a folder…", run: () => openFolderDialog(true)});
  items.push({label: "Clone from Git…", run: cloneDialog});
  items.push({label: "Import a zip file…", run: zipImport});
  if (S.active) items.push({label: "Save file as…", hint: "Ctrl+Shift+S", run: saveAs});
  if (S.project) { items.push("-"); items.push({label: "Project settings…", run: projectSettings}); items.push({label: "Rename…", run: renameProject}); items.push({label: "Duplicate", run: duplicateProject}); items.push({label: "Backups…", run: backupsDialog}); items.push({label: "Export as zip", run: () => { location.href = "/api/project/" + S.project.id + "/export"; }}); items.push({label: "Delete project…", run: deleteProject}); }
  showMenu(anchor, items);
}
async function openProject(id) {
  try { await POST(`project/${id}/current`); await loadProject(id); setView("explorer"); renderDashboard(); } catch (e) { fail(e); }
}
function deviceMenu(anchor) {
  const items = S.devices.map((d) => ({label: `${deviceLabel(d)} (${d.short_port})`, hint: d.usb_id, run: () => selectDevice(d.port)}));
  if (!items.length) items.push({label: "No board connected", run: () => {}});
  items.push("-");
  items.push({label: "Refresh", run: async () => { await POST("devices/refresh"); }});
  items.push({label: "Hardware manager", run: () => setView("hardware")});
  showMenu(anchor, items);
}
async function selectDevice(port) {
  try { await POST("devices/select", {port, project_id: S.project && S.project.id}); S.selectedPort = port; renderDevicePick(); toast("Board selected: " + port, "ok"); } catch (e) { fail(e); }
}

// ------------------------------------------------------------- side views
function setView(view) {
  S.view = view; store.set("view", view);
  $$("#activity button[data-view]").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  if (view === "dashboard") { $("#dashboard").classList.remove("hidden"); $("#editor-host").classList.add("hidden"); renderDashboard(); markTabs(); }
  else if (S.active) showEditor();
  renderSide();
}
function sideTools(buttons) {
  const tools = $("#side-tools"); tools.innerHTML = "";
  buttons.forEach(([label, title, run]) => tools.appendChild(h("button", {title, onclick: run}, label)));
}
function renderSide() {
  const body = $("#side-body");
  const view = S.view === "dashboard" ? "explorer" : S.view;
  $("#side-title").textContent = {explorer: "Explorer", hardware: "Hardware", libraries: "Libraries", git: "Version history"}[view];
  if (view === "explorer") return renderExplorer(body);
  if (view === "hardware") return renderHardware(body);
  if (view === "libraries") return renderLibraries(body);
  if (view === "git") return renderGit(body);
}

function renderExplorer(body) {
  sideTools([["＋", "New file", newFile], ["▣", "New folder", newFolder], ["⟳", "Refresh", () => S.project && loadProject(S.project.id)], ["⌕", "Search in project", searchProject]]);
  body.innerHTML = "";
  if (!S.project) { body.appendChild(h("div", {class: "ai-setup"}, h("p", {}, "No project open."), h("button", {class: "btn primary", onclick: newProjectWizard}, "New project"))); return; }
  const rows = [];
  for (const entry of S.tree) {
    const parts = entry.path.split("/");
    let hidden = false;
    for (let i = 1; i < parts.length; i++) if (S.collapsed.has(parts.slice(0, i).join("/"))) { hidden = true; break; }
    if (hidden) continue;
    const depth = parts.length - 1;
    const name = parts[parts.length - 1];
    const icon = entry.dir ? (S.collapsed.has(entry.path) ? "▸" : "▾") : fileIcon(name);
    const row = h("div", {class: "tree-row" + (S.active === entry.path ? " active" : ""), style: `padding-left:${10 + depth * 14}px`, title: entry.path},
      h("span", {class: "ico"}, icon), h("span", {class: "name"}, name));
    row.addEventListener("click", () => {
      if (entry.dir) { S.collapsed.has(entry.path) ? S.collapsed.delete(entry.path) : S.collapsed.add(entry.path); store.set("collapsed", Array.from(S.collapsed)); renderExplorer(body); }
      else openFile(entry.path);
    });
    row.addEventListener("contextmenu", (ev) => { ev.preventDefault(); fileMenu(row, entry); });
    rows.push(row);
  }
  rows.forEach((r) => body.appendChild(r));
}
function fileIcon(name) { return /\.(c|cpp|cc|ino|h|hpp)$/i.test(name) ? "◆" : /\.(md|txt)$/i.test(name) ? "¶" : /\.(json|ya?ml|ini|xml|csv|cfg)$/i.test(name) ? "◇" : "·"; }
function fileMenu(anchor, entry) {
  const items = [];
  if (!entry.dir) items.push({label: "Open", run: () => openFile(entry.path)}, {label: "Attach to AI request", run: () => attach({type: "file", path: entry.path})});
  items.push({label: "Rename / move…", run: () => moveEntry(entry.path)}, {label: "Delete…", run: () => deleteEntry(entry.path)});
  if (entry.dir) items.push({label: "New file here…", run: () => newFile(entry.path + "/")});
  showMenu(anchor, items);
}
async function newFile(prefix = "") {
  if (!S.project) return;
  const path = prompt("New file (path inside the project):", typeof prefix === "string" ? prefix : "src/");
  if (!path) return;
  try { await POST(`project/${S.project.id}/fs`, {op: "create", path, text: ""}); await loadProject(S.project.id); openFile(path); } catch (e) { fail(e); }
}
async function newFolder() {
  if (!S.project) return;
  const path = prompt("New folder:", "lib/");
  if (!path) return;
  try { await POST(`project/${S.project.id}/fs`, {op: "mkdir", path}); await loadProject(S.project.id); } catch (e) { fail(e); }
}
async function moveEntry(path) {
  const target = prompt("New path:", path);
  if (!target || target === path) return;
  try { await POST(`project/${S.project.id}/fs`, {op: "move", path, target}); closeTab(path, true); await loadProject(S.project.id); } catch (e) { fail(e); }
}
async function deleteEntry(path) {
  if (!confirm(`Move ${path} to the project trash?`)) return;
  try { await POST(`project/${S.project.id}/fs`, {op: "delete", path}); closeTab(path, true); await loadProject(S.project.id); toast("Moved to the trash", "ok"); } catch (e) { fail(e); }
}
async function searchProject() {
  if (!S.project) return;
  const q = prompt("Search in the project:");
  if (!q) return;
  const data = await GET(`project/${S.project.id}/search?q=${encodeURIComponent(q)}`);
  const box = h("div", {}, h("h2", {}, `“${q}” — ${data.hits.length} result(s)`));
  const list = h("div", {style: "max-height:60vh;overflow:auto"});
  data.hits.forEach((hit) => list.appendChild(h("div", {class: "problem", onclick: () => { closeModal(); openFile(hit.path, hit.line); }}, h("span", {class: "where"}, `${hit.path}:${hit.line}`), h("span", {}, hit.text))));
  box.appendChild(list);
  box.appendChild(h("div", {class: "buttons"}, h("button", {class: "btn", onclick: closeModal}, "Close")));
  openModal(box);
}

// ------------------------------------------------------------------- tabs
function markTabs() {
  const tabs = $("#tabs"); tabs.innerHTML = "";
  tabs.appendChild(h("div", {class: "tab" + (S.view === "dashboard" ? " active" : ""), onclick: () => setView("dashboard")}, h("span", {class: "name"}, "⌂ Dashboard")));
  for (const [path, f] of S.files) {
    const tab = h("div", {class: "tab" + (S.view !== "dashboard" && S.active === path ? " active" : "") + (f.dirty ? " dirty" : ""), title: path},
      h("span", {class: "name"}, path.split("/").pop()),
      h("button", {class: "close", title: "Close", onclick: (ev) => { ev.stopPropagation(); closeTab(path); }}, "×"));
    tab.addEventListener("click", () => switchTo(path));
    tabs.appendChild(tab);
  }
}
async function openFile(path, line) {
  if (!S.project) return;
  if (!S.files.has(path)) {
    let data;
    try { data = await GET(`project/${S.project.id}/file?path=${encodeURIComponent(path)}`); } catch (e) { fail(e); return; }
    const doc = CodeMirror.Doc(data.text, MODES[data.language] || "text/plain");
    const entry = {doc, sha: data.sha, dirty: false, language: data.language, path: data.path};
    doc.on("change", () => { if (!entry.dirty) { entry.dirty = true; markTabs(); } if (store.get("autosave", true)) later("save:" + path, () => saveFile(path, true), 1500); });
    S.files.set(path, entry);
  }
  switchTo(path);
  if (line) { cm.setCursor({line: line - 1, ch: 0}); cm.scrollIntoView({line: line - 1, ch: 0}, 120); }
}
function switchTo(path) {
  const f = S.files.get(path);
  if (!f) return;
  S.active = path;
  if (S.view === "dashboard") { S.view = "explorer"; $$("#activity button[data-view]").forEach((b) => b.classList.toggle("active", b.dataset.view === "explorer")); renderSide(); }
  showEditor();
  cm.swapDoc(f.doc);
  $("#sb-lang").textContent = f.language;
  markTabs(); markDiagnostics();
  $$(".tree-row").forEach((r) => r.classList.toggle("active", r.title === path));
  setTimeout(() => cm.refresh(), 0);
  cm.focus();
}
function showEditor() { $("#dashboard").classList.add("hidden"); $("#editor-host").classList.remove("hidden"); cm.refresh(); }
function closeTab(path, force) {
  const f = S.files.get(path);
  if (!f) return;
  if (f.dirty && !force && !confirm(`${path} has unsaved changes. Close without saving?`)) return;
  S.files.delete(path);
  if (S.active === path) {
    const next = Array.from(S.files.keys()).pop();
    S.active = null;
    if (next) switchTo(next); else { cm.swapDoc(CodeMirror.Doc("")); setView("dashboard"); }
  }
  markTabs();
}
function closeAllTabs() { S.files.clear(); S.active = null; if (cm) cm.swapDoc(CodeMirror.Doc("")); markTabs(); }
async function saveFile(path, auto) {
  path = path || S.active;
  const f = S.files.get(path);
  if (!f || !S.project) return;
  // One save per file at a time: the next one waits and sends the new checksum.
  const previous = f.saving || Promise.resolve();
  f.saving = previous.catch(() => {}).then(async () => {
    if (!f.dirty && auto) return;
    const text = f.doc.getValue();
    try {
      const res = await POST(`project/${S.project.id}/file`, {path, text, sha: f.sha});
      f.sha = res.sha;
      if (f.doc.getValue() === text) { f.dirty = false; markTabs(); }
      if (!auto) toast("Saved " + path.split("/").pop(), "ok", 1500);
    } catch (e) {
      if (/changed on disk/.test(e.message)) conflictDialog(path, f); else fail(e);
    }
  });
  return f.saving;
}
async function saveAs() {
  const f = S.files.get(S.active);
  if (!f || !S.project) return;
  const target = prompt("Save as (path inside the project):", S.active);
  if (!target || target === S.active) return;
  if (S.tree.some((e) => e.path === target) && !confirm(`${target} exists. Replace it?`)) return;
  try {
    await POST(`project/${S.project.id}/file`, {path: target, text: f.doc.getValue()});
    await loadProject(S.project.id);
    await openFile(target);
    toast("Saved as " + target, "ok");
  } catch (e) { fail(e); }
}
async function searchProjects() {
  const q = prompt("Search projects (name, board, tool):");
  if (q == null) return;
  const data = await GET("projects?q=" + encodeURIComponent(q));
  openModal(h("div", {}, h("h2", {}, `Projects matching “${q}”`),
    data.projects.length ? h("div", {}, data.projects.map((p) => h("div", {class: "problem", onclick: () => { closeModal(); openProject(p.id); }}, h("span", {class: "where"}, p.name), h("span", {}, `${p.board_name} · ${p.toolchain_name}`)))) : h("p", {class: "muted"}, "No project matches."),
    h("div", {class: "buttons"}, h("button", {class: "btn primary", onclick: closeModal}, "Close"))));
}
function conflictDialog(path, f) {
  openModal(h("div", {}, h("h2", {}, "File changed on disk"),
    h("p", {class: "desc"}, `${path} was changed outside the editor (by a build, Claude or another program).`),
    h("div", {class: "buttons"},
      h("button", {class: "btn", onclick: async () => { closeModal(); await reloadFile(path, true); }}, "Load the disk version"),
      h("button", {class: "btn danger", onclick: async () => { closeModal(); f.sha = null; await saveFile(path); }}, "Keep mine (overwrite)"))));
}
async function reloadFile(path, force) {
  const f = S.files.get(path);
  if (!f || (f.dirty && !force)) return;
  try {
    const data = await GET(`project/${S.project.id}/file?path=${encodeURIComponent(path)}`);
    if (data.sha === f.sha && !force) return;
    const cursor = f.doc.getCursor();
    f.doc.setValue(data.text); f.sha = data.sha; f.dirty = false; f.doc.setCursor(cursor); markTabs();
  } catch (e) { closeTab(path, true); }
}
function reloadOpenFiles() { for (const path of S.files.keys()) reloadFile(path); }

// ------------------------------------------------------------ diagnostics
function markDiagnostics() {
  if (!cm || !S.active) return;
  cm.clearGutter("diag");
  const doc = cm.getDoc();
  for (let i = 0; i < doc.lineCount(); i++) { doc.removeLineClass(i, "background", "line-error"); doc.removeLineClass(i, "background", "line-warning"); }
  for (const d of S.diagnostics) {
    if (!d.file || d.file !== S.active || !d.line || d.severity === "note") continue;
    const line = d.line - 1;
    if (line >= doc.lineCount()) continue;
    doc.addLineClass(line, "background", d.severity === "error" ? "line-error" : "line-warning");
    cm.setGutterMarker(line, "diag", h("span", {class: "gutter-mark " + (d.severity === "error" ? "mark-err" : "mark-warn"), title: d.message}, "●"));
  }
}
function renderProblems() {
  const box = $("#problems"); box.innerHTML = "";
  const list = S.diagnostics.filter((d) => d.severity !== "note");
  $("#problem-count").textContent = list.filter((d) => d.severity === "error").length || "";
  const last = S.project && S.project.last_build;
  if (last && last.missing_headers && last.missing_headers.length) {
    box.appendChild(h("div", {class: "problem"}, h("span", {class: "where err"}, "missing library"), h("span", {}, last.missing_headers.join(", ") + " — install it in Libraries, or ask the AI which library it is."), h("span", {class: "linkish", onclick: () => setView("libraries")}, "Libraries")));
  }
  if (!list.length) { box.appendChild(h("div", {class: "problem muted"}, last ? `Last ${last.action}: ${last.state}${last.headline ? " — " + last.headline : ""}` : "No problems.")); return; }
  for (const d of list) {
    const row = h("div", {class: "problem"}, h("span", {class: "where " + (d.severity === "error" ? "err" : "warn")}, d.file ? `${d.file}:${d.line}` : d.severity), h("span", {}, d.message));
    if (d.file && d.in_project) row.addEventListener("click", () => openFile(d.file, d.line));
    box.appendChild(row);
  }
  if (last && last.state !== "succeeded") box.appendChild(h("div", {style: "padding:8px 12px"}, h("button", {class: "btn primary small", onclick: () => aiKind("fix-build")}, "✦ Ask AI to fix")));
}
function renderTests() {
  const box = $("#tests"); box.innerHTML = "";
  if (!S.project) return;
  const last = S.project.last_build;
  box.appendChild(h("div", {class: "tests"},
    h("p", {}, h("button", {class: "btn small", onclick: () => runBuild("test")}, "Run tests"), " ", h("button", {class: "btn small", onclick: () => runBuild("check")}, "Static analysis")),
    last && last.action === "test" ? h("div", {}, h("p", {class: last.state === "succeeded" ? "ok" : "err"}, `Tests ${last.state}: ${(last.tests && last.tests.summary) || last.headline || ""}`)) : h("p", {class: "muted"}, "No test run yet in this session."),
    h("p", {class: "muted"}, "Hardware-in-the-loop tests are not run automatically: tests here run on this computer or only compile.")));
}

// ---------------------------------------------------------------- building
async function runBuild(action) {
  if (!S.project) { toast("Open a project first.", "warn"); return; }
  for (const path of S.files.keys()) if (S.files.get(path).dirty) await saveFile(path, true);
  try { const job = await POST(`project/${S.project.id}/build`, {action}); S.outputJob = job.id; $("#output").textContent = ""; showPanel("output"); onJob(job); }
  catch (e) { fail(e); }
}
function handleFix(fix) {
  if (!fix) return;
  if (fix.startsWith("install:")) installTool(fix.split(":")[1]);
  else if (fix.startsWith("install-core:")) installCore(fix.slice("install-core:".length));
  else if (fix === "login") claudeDialog();
  else if (fix === "review") toast("Review Claude's pending changes in the AI panel first.", "warn");
}
function installTool(tool) {
  const names = {claude: "Claude Code", "arduino-cli": "the Arduino CLI", platformio: "PlatformIO", esptool: "esptool"};
  confirmDialog(`Install ${names[tool] || tool}?`, "Downloads from the official source and installs it for this user (no sudo).", "Install", async () => {
    try { await POST("tools/install", {tool}); toast("Installing… see Build output", ""); } catch (e) { fail(e); }
  });
}
function installCore(core) {
  confirmDialog(`Install the ${core} board core?`, "The Arduino CLI downloads it from the official board index. This can be several hundred MB.", "Install", async () => {
    try { await POST("tools/core", {core}); toast("Installing the core… see Build output"); } catch (e) { fail(e); }
  });
}

// ---------------------------------------------------------------- flashing
async function flashDialog(action = "upload") {
  if (!S.project) { toast("Open a project first.", "warn"); return; }
  let plan;
  try { plan = await POST("upload/prepare", {project_id: S.project.id, port: S.selectedPort, action, speed: S.project.upload_speed || 0}); } catch (e) { fail(e); return; }
  const title = {upload: "Flash firmware?", erase: "Erase the whole flash?", identify: "Identify the chip"}[action];
  const icon = (c) => ({ok: "✓", info: "•", warning: "⚠", error: "✗"}[c.ok ? (c.level === "info" ? "info" : "ok") : c.level]);
  const box = h("div", {}, h("h2", {}, plan.ok ? title : "Can't " + (action === "upload" ? "flash" : action) + " yet"),
    h("div", {class: "kv"}, h("span", {class: "k"}, "Project"), h("span", {}, plan.project), h("span", {class: "k"}, "Board"), h("span", {}, plan.board), h("span", {class: "k"}, "Port"), h("span", {}, plan.port || "—"), h("span", {class: "k"}, "Device"), h("span", {}, plan.device || "—"), action === "upload" ? h("span", {class: "k"}, "Firmware") : null, action === "upload" ? h("span", {}, plan.firmware || "—") : null),
    h("ul", {class: "checks"}, plan.checks.map((c) => h("li", {}, h("span", {class: "i-" + (c.ok ? (c.level === "info" ? "info" : "ok") : c.level)}, icon(c)), h("span", {}, c.message)))));
  const buttons = h("div", {class: "buttons"}, h("button", {class: "btn", onclick: closeModal}, "Cancel"));
  if (plan.ok) {
    let agreed = !plan.warnings.length && action !== "erase";
    let typed = null;
    if (plan.warnings.length) {
      const check = h("input", {type: "checkbox"});
      check.addEventListener("change", () => { agreed = check.checked; go.disabled = !agreed; });
      box.appendChild(h("label", {}, check, " I checked: this is the right board and port."));
    }
    if (action === "erase") {
      typed = h("input", {placeholder: "type ERASE"});
      typed.addEventListener("input", () => { go.disabled = typed.value !== "ERASE"; });
      box.appendChild(h("p", {class: "warn"}, "Erasing removes the firmware and all saved data (Wi-Fi settings, NVS, files) from the board."));
      box.appendChild(typed);
    }
    const go = h("button", {class: action === "erase" ? "btn danger solid" : "btn primary", disabled: !agreed || action === "erase", onclick: async () => {
      closeModal();
      try { const job = await POST("upload/start", {token: plan.token, confirmed: true}); S.outputJob = job.id; $("#output").textContent = ""; showPanel("output"); onJob(job); }
      catch (e) { fail(e); }
    }}, {upload: "Flash firmware", erase: "Erase flash", identify: "Identify"}[action]);
    buttons.appendChild(go);
  } else {
    if (plan.checks.some((c) => c.id === "firmware" && !c.ok)) buttons.appendChild(h("button", {class: "btn primary", onclick: () => { closeModal(); runBuild("build"); }}, "Build now"));
    if (plan.checks.some((c) => ["device", "match", "port"].includes(c.id) && !c.ok)) buttons.appendChild(h("button", {class: "btn", onclick: () => { closeModal(); setView("hardware"); }}, "Hardware manager"));
  }
  box.appendChild(buttons);
  openModal(box);
}

// ---------------------------------------------------------------- dashboard
function renderDashboard() {
  if (S.view !== "dashboard" || !S.state) return;
  const st = S.state; const d = $("#dashboard"); d.innerHTML = "";
  const ai = S.ai || st.claude || {};
  d.appendChild(h("h2", {}, "Welcome to mCode Studio"));
  d.appendChild(h("p", {class: "muted"}, "Create firmware with Claude, build it with the real toolchains, and flash it to your board."));
  const grid = h("div", {class: "dash-grid"});
  const newCard = h("div", {class: "card"}, h("h3", {}, "New project"), h("div", {class: "shortcuts"},
    [["esp32", "ESP32", "ESP32 · S2 · S3 · C3 · C6 · H2"], ["arduino", "Arduino", "Uno · Mega · Nano · Leonardo…"], ["pico", "Raspberry Pi Pico", "RP2040"], ["generic", "Generic C/C++", "Makefile project"]]
      .map(([p, t, s]) => h("div", {class: "shortcut", onclick: () => newProjectWizard(p)}, h("b", {}, t), h("span", {}, s)))));
  grid.appendChild(newCard);
  const recent = h("div", {class: "card"}, h("h3", {}, "Recent projects"));
  (st.recent || []).forEach((p) => recent.appendChild(h("div", {class: "line"}, h("a", {onclick: () => openProject(p.id)}, p.name), h("span", {class: "meta"}, `${p.board_name} · ${p.toolchain_name}`))));
  if (!(st.recent || []).length) recent.appendChild(h("p", {class: "muted"}, "No projects yet."));
  grid.appendChild(recent);
  const boards = h("div", {class: "card"}, h("h3", {}, "Connected boards"));
  S.devices.forEach((dv) => boards.appendChild(h("div", {class: "line"}, h("span", {class: "dot " + (dv.readable ? "ok" : "err")}), h("span", {}, deviceLabel(dv)), h("span", {class: "meta"}, `${dv.short_port} · ${dv.usb_id}`))));
  if (!S.devices.length) boards.appendChild(h("p", {class: "muted"}, "No board connected. Plug one in by USB."));
  boards.appendChild(h("a", {onclick: () => setView("hardware")}, "Hardware manager →"));
  grid.appendChild(boards);
  const claude = h("div", {class: "card"}, h("h3", {}, "Claude Code"),
    h("div", {class: "line"}, h("span", {class: "dot " + (ai.state === "ready" ? "ok" : ai.state === "working" ? "warn" : "err")}), h("span", {}, (ai.auth && ai.auth.label) || ai.state), h("span", {class: "meta"}, ai.version ? "v" + ai.version : "")),
    h("div", {class: "line"}, h("span", {}, "Mode"), h("span", {class: "meta"}, ai.mode_label || "")),
    ai.memory_warning ? h("p", {class: "warn"}, ai.memory_warning) : null,
    h("a", {onclick: claudeDialog}, ai.state === "ready" ? "Account →" : ai.state === "not-installed" ? "Install Claude Code →" : "Connect your Claude account →"));
  grid.appendChild(claude);
  const tools = h("div", {class: "card"}, h("h3", {}, "Toolchains"));
  (st.tools || []).filter((t) => ["claude", "arduino-cli", "platformio", "esp-idf", "esptool", "make", "git"].includes(t.id)).forEach((t) => {
    tools.appendChild(h("div", {class: "line"}, h("span", {class: "dot " + (t.found ? "ok" : "warn")}), h("span", {}, t.name), h("span", {class: "meta"}, t.found ? t.version || "found" : (t.installable ? "" : "not installed")), !t.found && t.installable ? h("a", {onclick: () => installTool(t.id)}, "Install") : null));
  });
  grid.appendChild(tools);
  const builds = h("div", {class: "card"}, h("h3", {}, "Recent builds"));
  (st.recent_builds || []).slice(0, 6).forEach((b) => builds.appendChild(h("div", {class: "line"}, h("span", {class: "dot " + (b.state === "succeeded" ? "ok" : "err")}), h("span", {}, `${b.project} · ${b.action}`), h("span", {class: "meta"}, fmtTime(b.time)))));
  if (!(st.recent_builds || []).length) builds.appendChild(h("p", {class: "muted"}, "Nothing built yet."));
  grid.appendChild(builds);
  const ups = h("div", {class: "card"}, h("h3", {}, "Recent uploads"));
  (st.recent_uploads || []).slice(0, 6).forEach((u) => ups.appendChild(h("div", {class: "line"}, h("span", {class: "dot " + (u.state === "succeeded" ? "ok" : "err")}), h("span", {}, `${u.project} → ${(u.port || "").split("/").pop()}`), h("span", {class: "meta"}, `${u.action} · ${fmtTime(u.time)}`))));
  if (!(st.recent_uploads || []).length) ups.appendChild(h("p", {class: "muted"}, "No firmware flashed yet."));
  grid.appendChild(ups);
  const m = st.machine || {};
  grid.appendChild(h("div", {class: "card"}, h("h3", {}, "This device"), h("div", {class: "kv"},
    h("span", {class: "k"}, "Model"), h("span", {}, m.model || m.arch || ""), h("span", {class: "k"}, "Memory"), h("span", {}, m.memory_mb ? m.memory_mb + " MB" : "?"),
    h("span", {class: "k"}, "CPUs"), h("span", {}, String(m.cpus || "")), h("span", {class: "k"}, "Python"), h("span", {}, m.python || ""),
    h("span", {class: "k"}, "Desktop IDE"), h("span", {}, st.web && st.web.lan ? "network on" : "this device only"))));
  d.appendChild(grid);
  markTabs();
}

// --------------------------------------------------------------- hardware
function renderHardware(body) {
  sideTools([["⟳", "Refresh", async () => { await POST("devices/refresh"); }]]);
  body.innerHTML = "";
  if (!S.devices.length) {
    const why = h("p", {class: "muted", style: "white-space: pre-wrap"});
    body.appendChild(h("div", {class: "section"}, h("p", {class: "muted"}, "No USB serial device found. Connect a board, then refresh. mCode notices plug-in automatically."), why));
    GET("devices").then((r) => { if (r.help) why.textContent = r.help.detail; }).catch(() => {});
  }
  for (const d of S.devices) {
    const selected = d.port === S.selectedPort;
    const item = h("div", {class: "list-item"},
      h("div", {class: "title"}, h("span", {class: "dot " + (d.readable ? "ok" : "err")}), deviceLabel(d), selected ? h("span", {class: "ok"}, " (selected)") : null),
      h("div", {class: "kv"}, h("span", {class: "k"}, "Port"), h("span", {}, d.port), h("span", {class: "k"}, "USB ID"), h("span", {}, d.usb_id || "—"),
        h("span", {class: "k"}, "Chipset"), h("span", {}, d.bridge || (d.kind === "native-esp" ? "built-in USB" : d.kind)), h("span", {class: "k"}, "Driver"), h("span", {}, d.driver || "—"),
        h("span", {class: "k"}, "Identified"), h("span", {}, d.description), d.manufacturer ? h("span", {class: "k"}, "Maker") : null, d.manufacturer ? h("span", {}, d.manufacturer) : null,
        d.serial ? h("span", {class: "k"}, "Serial no.") : null, d.serial ? h("span", {}, d.serial) : null,
        d.board_names && d.board_names.length ? h("span", {class: "k"}, "Cores say") : null, d.board_names && d.board_names.length ? h("span", {}, d.board_names.join(", ")) : null),
      d.problem ? h("p", {class: "err"}, d.problem) : null,
      d.holders && d.holders.length ? h("p", {class: "warn"}, "Open in: " + d.holders.join(", ")) : null,
      h("div", {class: "row"},
        h("button", {class: "btn small", onclick: () => selectDevice(d.port)}, "Use"),
        (d.kind === "bridge" || d.family === "esp32") ? h("button", {class: "btn small", title: "Reads the chip with esptool; restarts the board", onclick: () => identify(d.port)}, "Identify chip") : null,
        h("button", {class: "btn small", onclick: () => { showPanel("serial"); $("#serial-port").value = d.port; }}, "Monitor")));
    body.appendChild(item);
  }
  if (S.project) {
    const speeds = [0, 115200, 230400, 460800, 921600];
    const sel = h("select", {}, speeds.map((s) => h("option", {value: s, selected: s === (S.project.upload_speed || 0)}, s ? s + " baud" : "Board default")));
    sel.addEventListener("change", async () => { try { await POST(`project/${S.project.id}/update`, {fields: {upload_speed: Number(sel.value)}}); await loadProject(S.project.id); } catch (e) { fail(e); } });
    const verify = h("input", {type: "checkbox", checked: !!S.settings["hw.verify_upload"]});
    verify.addEventListener("change", () => POST("settings", {values: {"hw.verify_upload": verify.checked}}).catch(fail));
    body.appendChild(h("div", {class: "section"}, h("h4", {}, "Upload settings"), h("div", {class: "form"}, h("span", {}, "Upload speed"), sel, h("span", {}, "Verify"), h("label", {}, verify, "after upload, where supported")),
      h("div", {class: "row", style: "margin-top:8px;display:flex;gap:6px"}, h("button", {class: "btn primary small", onclick: () => flashDialog("upload")}, "Flash firmware…"),
        S.project.platform === "esp32" ? h("button", {class: "btn danger small", onclick: () => flashDialog("erase")}, "Erase flash…") : null)));
  }
  GET("devices").then((data) => {
    const hist = h("div", {class: "section"}, h("h4", {}, "Upload history"));
    (data.history || []).forEach((u) => hist.appendChild(h("div", {class: "sub"}, `${fmtTime(u.time)} · ${u.action} · ${u.project} → ${(u.port || "").split("/").pop()} · `, h("span", {class: u.state === "succeeded" ? "ok" : "err"}, u.state))));
    if (!(data.history || []).length) hist.appendChild(h("p", {class: "muted"}, "Nothing yet."));
    if (!data.hotplug) hist.appendChild(h("p", {class: "muted"}, "Plug-in events are not available here: use Refresh."));
    body.appendChild(hist);
  }).catch(() => {});
}
async function identify(port) { try { const job = await POST("devices/identify", {port}); S.outputJob = job.id; $("#output").textContent = ""; showPanel("output"); } catch (e) { fail(e); } }

// --------------------------------------------------------------- libraries
async function renderLibraries(body) {
  sideTools([["⟳", "Refresh", () => renderSide()]]);
  body.innerHTML = "";
  if (!S.project) { body.appendChild(h("p", {class: "muted", style: "padding:12px"}, "Open a project first.")); return; }
  const input = h("input", {placeholder: S.project.toolchain === "platformio" ? "Search the PlatformIO Registry" : S.project.toolchain === "esp-idf" ? "Component name, e.g. espressif/led_strip" : "Search the Arduino Library Manager"});
  const results = h("div", {});
  const search = async () => {
    if (S.project.toolchain === "esp-idf") { installLibrary(input.value.trim(), ""); return; }
    results.innerHTML = "<p class='muted' style='padding:0 12px'>Searching…</p>";
    try {
      const data = await GET(`project/${S.project.id}/libraries/search?q=${encodeURIComponent(input.value)}`);
      results.innerHTML = "";
      data.results.forEach((lib) => results.appendChild(h("div", {class: "list-item"}, h("div", {class: "title"}, lib.name, h("span", {class: "muted"}, lib.version)), h("div", {class: "sub"}, lib.description), h("div", {class: "sub"}, `${lib.author || ""} · ${lib.source}`), h("div", {class: "row"}, h("button", {class: "btn small", onclick: () => installLibrary(lib.name, lib.version)}, "Install"), S.project.toolchain === "arduino-cli" ? h("button", {class: "btn small", onclick: () => libraryDeps(lib.name)}, "Dependencies") : null))));
      if (!data.results.length) results.appendChild(h("p", {class: "muted", style: "padding:0 12px"}, "Nothing found."));
    } catch (e) { results.innerHTML = ""; fail(e); }
  };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") search(); });
  body.appendChild(h("div", {class: "search-row"}, input, h("button", {class: "btn small", onclick: search}, S.project.toolchain === "esp-idf" ? "Add" : "Search")));
  body.appendChild(results);
  const installed = h("div", {class: "section"}, h("h4", {}, "Installed"));
  body.appendChild(installed);
  try {
    const data = await GET(`project/${S.project.id}/libraries`);
    data.installed.forEach((lib) => installed.appendChild(h("div", {class: "sub", style: "display:flex;gap:6px;align-items:center;padding:2px 0"}, h("span", {style: "flex:1"}, `${lib.name} ${lib.version || ""}`),
      S.project.toolchain !== "esp-idf" ? h("button", {class: "btn small", title: "Remove", onclick: () => confirmDialog(`Remove ${lib.name}?`, "", "Remove", () => POST(`project/${S.project.id}/libraries/uninstall`, {name: lib.name.split("@")[0]}).catch(fail))}, "−") : null)));
    if (!data.installed.length) installed.appendChild(h("p", {class: "muted"}, "None."));
  } catch (e) { installed.appendChild(h("p", {class: "err"}, e.message)); }
  const tools = h("div", {class: "section"}, h("h4", {}, "Dependencies"),
    h("div", {class: "row", style: "display:flex;gap:6px;flex-wrap:wrap"},
      h("button", {class: "btn small", onclick: checkMissing}, "Find missing"),
      h("button", {class: "btn small", onclick: () => confirmDialog("Update libraries?", "Updates installed libraries from their source.", "Update", () => POST(`project/${S.project.id}/libraries/update`, {}).catch(fail))}, "Update all"),
      h("button", {class: "btn small", onclick: showConflicts}, "Conflicts")));
  body.appendChild(tools);
}
async function libraryDeps(name) {
  try {
    const data = await GET(`project/${S.project.id}/libraries/deps?name=${encodeURIComponent(name)}`);
    openModal(h("div", {}, h("h2", {}, `${name}: dependencies`), data.dependencies.length ? h("div", {}, data.dependencies.map((d) => h("div", {class: "line", style: "display:flex;gap:8px;padding:3px 0"}, h("span", {style: "flex:1"}, d.name), h("span", {class: d.installed ? "ok" : "warn"}, d.installed ? "installed " + d.installed : "will be installed " + (d.required || ""))))) : h("p", {class: "muted"}, "None."),
      h("div", {class: "buttons"}, h("button", {class: "btn", onclick: closeModal}, "Close"), h("button", {class: "btn primary", onclick: () => { closeModal(); installLibrary(name, ""); }}, "Install"))));
  } catch (e) { fail(e); }
}
function installLibrary(name, version) {
  if (!name) return;
  const source = S.project.toolchain === "platformio" ? "the PlatformIO Registry" : S.project.toolchain === "esp-idf" ? "the Espressif Component Registry" : "the Arduino Library Manager";
  confirmDialog(`Install ${name}${version ? " " + version : ""}?`, `From ${source}.`, "Install", async () => {
    try { await POST(`project/${S.project.id}/libraries/install`, {name, version}); toast("Installing " + name + "… see Build output"); } catch (e) { fail(e); }
  });
}
async function checkMissing() {
  try {
    const data = await GET(`project/${S.project.id}/libraries/missing`);
    const msg = !data.checked ? "Missing-library detection works for Arduino CLI projects; PlatformIO and ESP-IDF report missing libraries when you build." : data.missing.length ? "Not provided by any installed library or the core: " + data.missing.join(", ") : "Every included header is provided.";
    openModal(h("div", {}, h("h2", {}, "Dependencies"), h("p", {}, msg), h("p", {class: "muted"}, "Includes found: " + (data.includes.join(", ") || "none")), h("div", {class: "buttons"}, data.missing && data.missing.length ? h("button", {class: "btn", onclick: () => { closeModal(); aiAsk("Which libraries provide these headers: " + data.missing.join(", ") + "? Name each library and its source; do not install anything."); }}, "Ask AI") : null, h("button", {class: "btn primary", onclick: closeModal}, "OK"))));
  } catch (e) { fail(e); }
}
async function showConflicts() {
  try {
    const data = await GET(`project/${S.project.id}/libraries/conflicts`);
    openModal(h("div", {}, h("h2", {}, "Library conflicts"), data.conflicts.length ? h("div", {}, data.conflicts.map((c) => h("div", {class: "list-item"}, h("div", {class: "title"}, c.header), h("div", {class: "sub"}, "Used: " + c.used), h("div", {class: "sub"}, "Not used: " + c.not_used.join(", "))))) : h("p", {class: "muted"}, "The last build reported no conflicts."), h("div", {class: "buttons"}, h("button", {class: "btn primary", onclick: closeModal}, "OK"))));
  } catch (e) { fail(e); }
}

// --------------------------------------------------------------------- git
async function renderGit(body) {
  sideTools([["⟳", "Refresh", () => renderSide()]]);
  body.innerHTML = "";
  if (!S.project) return;
  let st;
  try { st = await GET(`project/${S.project.id}/git`); } catch (e) { body.appendChild(h("p", {class: "err"}, e.message)); return; }
  if (!st.available) { body.appendChild(h("p", {class: "muted", style: "padding:12px"}, "git is not installed (sudo apt install git).")); return; }
  if (!st.repo) { body.appendChild(h("div", {class: "section"}, h("p", {}, "No version history yet."), h("button", {class: "btn small", onclick: async () => { await POST(`project/${S.project.id}/git/init`); renderSide(); }}, "Start history"))); return; }
  const msg = h("input", {placeholder: "Describe the change"});
  body.appendChild(h("div", {class: "section"}, h("h4", {}, `Branch ${st.branch || ""}`),
    st.changes.length ? h("div", {}, st.changes.map((c) => h("div", {class: "sub"}, `${c.status} ${c.path}`))) : h("p", {class: "muted"}, "No changes."),
    h("div", {class: "search-row", style: "padding:6px 0"}, msg, h("button", {class: "btn small", onclick: async () => { try { const r = await POST(`project/${S.project.id}/git/commit`, {message: msg.value}); toast(r.message, "ok"); renderSide(); } catch (e) { fail(e); } }}, "Commit")),
    h("button", {class: "btn small", onclick: async () => { const d = await GET(`project/${S.project.id}/git/diff`); openModal(h("div", {}, h("h2", {}, "Changes since the last commit"), renderDiff(d.diff || "No changes."), h("div", {class: "buttons"}, h("button", {class: "btn primary", onclick: closeModal}, "Close")))); }}, "Show diff")));
  const log = await GET(`project/${S.project.id}/git/log`);
  body.appendChild(h("div", {class: "section"}, h("h4", {}, "History"), log.log.map((c) => h("div", {class: "sub"}, `${c.hash} · ${fmtTime(c.time)} · ${c.subject}`))));
}

// ------------------------------------------------------------------- AI
function aiStatusBlock() {
  const ai = S.ai || {};
  if (ai.state === "not-installed") return h("div", {class: "ai-setup"}, h("h3", {}, "Claude Code is not installed"), h("p", {}, "mCode uses your own Claude Code with your Claude account."), h("button", {class: "btn primary", onclick: () => installTool("claude")}, "Install Claude Code"));
  if (ai.state === "signed-out") return h("div", {class: "ai-setup"}, h("h3", {}, "Connect your Claude account"), h("p", {}, "Sign in with Claude Code's official browser login. mCode never sees your password or tokens."), h("button", {class: "btn primary", onclick: claudeDialog}, "Connect"));
  if (!S.project) return h("div", {class: "ai-setup"}, h("h3", {}, "No project open"), h("p", {}, "Claude works inside a project."), h("button", {class: "btn primary", onclick: () => newProjectWizard()}, "New project"));
  return null;
}
async function loadConversation() {
  if (!S.project || !S.ai || !S.ai.installed) { renderAI(); return; }
  try { const data = await GET(`ai/${S.project.id}/conversation`); S.conversation = data.conversation; S.review = data.review; S.turn = data.turn && data.turn.project_id === S.project.id ? data.turn : null; } catch (e) { S.conversation = []; }
  renderAI();
}
function md(text) {
  let s = esc(text || "");
  const blocks = [];
  s = s.replace(/```[\w+-]*\n?([\s\S]*?)```/g, (_, code) => { blocks.push(code); return `\u0000${blocks.length - 1}\u0000`; });
  s = s.replace(/`([^`\n]+)`/g, "<code>$1</code>").replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>").replace(/^#{1,6} (.+)$/gm, "<b>$1</b>");
  s = s.split(/\n{2,}/).map((para) => {
    if (/^\s*[-*] /m.test(para)) return "<ul>" + para.split(/\n/).map((l) => l.replace(/^\s*[-*] (.*)$/, "<li>$1</li>")).join("") + "</ul>";
    if (/^\s*\d+\. /m.test(para)) return "<ol>" + para.split(/\n/).map((l) => l.replace(/^\s*\d+\. (.*)$/, "<li>$1</li>")).join("") + "</ol>";
    return "<p>" + para.replace(/\n/g, "<br>") + "</p>";
  }).join("");
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<pre><code>${blocks[Number(i)]}</code></pre>`);
}
function renderAI() {
  renderClaudePill();
  const body = $("#ai-body"); body.innerHTML = "";
  const setup = aiStatusBlock();
  $("#ai-mode").value = S.settings["ai.mode"] || "ask";
  if (setup) { body.appendChild(setup); $("#ai-review").classList.add("hidden"); $("#ai-turn").classList.add("hidden"); return; }
  if (S.ai.memory_warning) body.appendChild(h("div", {class: "msg system warn"}, S.ai.memory_warning));
  if (S.ai.auth && S.ai.auth.note) body.appendChild(h("div", {class: "msg system warn"}, S.ai.auth.note));
  if (!S.conversation.length) body.appendChild(h("div", {class: "msg system"}, "Ask Claude to create firmware, explain code, fix the build, write tests or document the wiring. It works on this project's files."));
  for (const m of S.conversation) {
    if (m.role === "user") body.appendChild(h("div", {class: "msg user"}, m.text, h("div", {class: "meta"}, `${m.source || ""} · ${fmtTime(m.time)}`)));
    else body.appendChild(h("div", {class: "msg assistant"}, h("div", {html: md(m.text)}), m.summary ? h("div", {class: "summary"}, m.summary) : null,
      h("div", {class: "meta"}, [m.state, m.changes && m.changes.length ? `${m.changes.length} file(s)` : "", m.cost_usd ? `$${Number(m.cost_usd).toFixed(4)}` : "", fmtTime(m.time)].filter(Boolean).join(" · "))));
  }
  body.scrollTop = body.scrollHeight;
  renderTurn(); renderReview();
}
function renderTurn() {
  const box = $("#ai-turn"); const t = S.turn;
  const working = t && t.state === "working" && S.project && t.project_id === S.project.id;
  $("#ai-send").classList.toggle("hidden", !!working);
  $("#ai-stop").classList.toggle("hidden", !working);
  if (!working) { box.classList.add("hidden"); return; }
  box.classList.remove("hidden"); box.innerHTML = "";
  box.appendChild(h("div", {class: "phase"}, h("span", {class: "spin"}), t.phase, h("span", {class: "muted"}, ` · ${Math.round(t.elapsed || 0)}s`)));
  (t.activities || []).slice(-8).forEach((a) => box.appendChild(h("div", {class: "step" + (a.error ? " err" : "")}, a.label)));
  if (t.text) box.appendChild(h("div", {class: "step"}, t.text.slice(-300)));
}
function renderDiff(text) {
  const pre = h("pre", {class: "diff"});
  for (const line of (text || "").split("\n")) pre.appendChild(h("span", {class: line.startsWith("+") && !line.startsWith("+++") ? "add" : line.startsWith("-") && !line.startsWith("---") ? "del" : line.startsWith("@@") ? "hunk" : ""}, line + "\n"));
  return pre;
}
function renderReview() {
  const box = $("#ai-review"); const r = S.review;
  if (!r || !r.changes) { box.classList.add("hidden"); return; }
  box.classList.remove("hidden"); box.innerHTML = "";
  const held = r.mode !== "ask";
  box.appendChild(h("h4", {}, held ? "⚠ Changes on hold — they need your OK" : `✦ Claude proposes ${r.changes.length} change(s)`));
  if (r.summary) box.appendChild(h("p", {class: "muted"}, r.summary));
  const picks = new Map();
  for (const c of r.changes) {
    const check = h("input", {type: "checkbox", checked: true});
    picks.set(c.path, check);
    box.appendChild(h("details", {class: "change"}, h("summary", {}, check, h("b", {}, c.path), h("span", {class: "muted"}, `${c.status} +${c.added} −${c.removed}`)),
      c.risky && c.risky.length ? h("div", {class: "risk"}, "⚠ " + c.risky.join("; ")) : null, renderDiff(c.diff || "(binary file)")));
  }
  const chosen = () => Array.from(picks).filter(([, el]) => el.checked).map(([p]) => p);
  box.appendChild(h("div", {class: "ai-input-row"},
    h("button", {class: "btn primary small", onclick: async () => { try { const res = await POST(`ai/${S.project.id}/approve`, {paths: chosen()}); toast(`Applied ${res.applied.length} file(s)`, "ok"); reloadOpenFiles(); loadConversation(); loadProject(S.project.id); } catch (e) { fail(e); } }}, held ? "Keep selected" : "Apply selected"),
    h("button", {class: "btn danger small", onclick: async () => { try { await POST(`ai/${S.project.id}/reject`); toast(held ? "Put back" : "Discarded"); loadConversation(); } catch (e) { fail(e); } }}, held ? "Put back" : "Discard all"),
    r.rebuild_after ? h("span", {class: "muted"}, "mCode rebuilds after applying.") : null));
}
function onAI(data) {
  renderClaudePill();
  if (data.what === "login") { renderLogin(); if (S.ai.login && S.ai.login.state === "done") { toast("Claude connected", "ok"); closeModal(); loadConversation(); } }
  if (data.what === "status") { renderAI(); renderDashboard(); }
  if (data.what === "progress") renderTurn();
  if (["turn", "review", "undo", "conversation"].includes(data.what)) {
    if (data.what === "turn" && data.turn && data.turn.state !== "working") {
      const t = data.turn; toast(t.state === "done" ? "Claude: " + (t.summary || "done") : t.state === "review" ? "Claude has changes for you to review" : "Claude: " + (t.error || t.state), t.state === "error" ? "err" : t.state === "done" ? "ok" : "warn", 7000);
      reloadOpenFiles(); if (S.project) loadProject(S.project.id);
    }
    if (data.what === "undo" || data.what === "review") reloadOpenFiles();
    loadConversation();
  }
}
async function aiSend() {
  const text = $("#ai-text").value.trim();
  if (!text && !S.attachments.length) return;
  await aiAsk(text);
}
async function aiAsk(text, kind = "chat") {
  if (!S.project) { toast("Open a project first.", "warn"); return; }
  for (const path of S.files.keys()) if (S.files.get(path).dirty) await saveFile(path, true);
  try {
    const turn = await POST(`ai/${S.project.id}/ask`, {text, kind, attachments: S.attachments.filter((a) => a.type === "file").map((a) => a.path), include_serial: S.attachments.some((a) => a.type === "serial")});
    S.turn = turn; $("#ai-text").value = ""; S.attachments = []; renderAttachments(); loadConversation();
  } catch (e) { fail(e); }
}
function aiKind(kind) {
  const texts = {"fix-build": "Fix the build errors.", "analyze-serial": "What is happening on the board?", review: "Review this project.", wiring: "Write the wiring document."};
  aiAsk($("#ai-text").value.trim() || texts[kind], kind);
}
function attach(a) { if (!S.attachments.some((x) => x.type === a.type && x.path === a.path)) S.attachments.push(a); renderAttachments(); }
function renderAttachments() {
  const box = $("#ai-attachments"); box.innerHTML = "";
  S.attachments.forEach((a, i) => box.appendChild(h("span", {class: "chip"}, a.type === "serial" ? "serial output" : a.path, h("button", {onclick: () => { S.attachments.splice(i, 1); renderAttachments(); }}, "×"))));
}

// ------------------------------------------------------------- Claude login
function claudeDialog() {
  const ai = S.ai || {};
  const box = h("div", {id: "claude-dialog"});
  openModal(box);
  renderLogin();
  if (ai.state !== "not-installed") POST("ai/refresh").then((st) => { S.ai = st; renderLogin(); renderAI(); }).catch(() => {});
}
function renderLogin() {
  const box = $("#claude-dialog");
  if (!box) return;
  const ai = S.ai || {}; const login = ai.login;
  box.innerHTML = "";
  box.appendChild(h("h2", {}, "Claude Code"));
  box.appendChild(h("div", {class: "kv"}, h("span", {class: "k"}, "Status"), h("span", {}, (ai.auth && ai.auth.label) || ai.state), h("span", {class: "k"}, "Version"), h("span", {}, ai.version || "—"), h("span", {class: "k"}, "Found"), h("span", {}, ai.source || "—")));
  if (ai.auth && ai.auth.note) box.appendChild(h("p", {class: "warn"}, ai.auth.note));
  if (ai.memory_warning) box.appendChild(h("p", {class: "warn"}, ai.memory_warning));
  const buttons = h("div", {class: "buttons"});
  if (ai.state === "not-installed") {
    box.appendChild(h("p", {class: "desc"}, ai.hint || ""));
    buttons.appendChild(h("button", {class: "btn primary", onclick: () => { closeModal(); installTool("claude"); }}, "Install Claude Code"));
  } else if (login && ["starting", "waiting", "checking"].includes(login.state)) {
    if (login.state === "starting") box.appendChild(h("p", {}, h("span", {class: "spin"}), " Starting Claude Code's sign-in…"));
    if (login.url) {
      box.appendChild(h("p", {}, "1. Open this link and sign in to your Claude account:"));
      box.appendChild(h("p", {}, h("a", {href: login.url, target: "_blank", rel: "noopener noreferrer"}, "Open the Claude sign-in page ↗")));
      box.appendChild(h("p", {}, "2. If the page shows a code, paste it here:"));
      const code = h("input", {placeholder: "Paste the code", style: "width:100%"});
      box.appendChild(code);
      buttons.appendChild(h("button", {class: "btn primary", onclick: async () => { try { const st = await POST("ai/login/code", {code: code.value}); S.ai.login = st; renderLogin(); } catch (e) { fail(e); } }}, "Send code"));
    }
    if (login.state === "checking") box.appendChild(h("p", {}, h("span", {class: "spin"}), " Checking…"));
    buttons.prepend(h("button", {class: "btn", onclick: async () => { await POST("ai/login/cancel"); closeModal(); }}, "Cancel"));
  } else if (ai.auth && ai.auth.logged_in) {
    box.appendChild(h("p", {class: "ok"}, "Signed in. mCode uses Claude Code with this account; usage counts against your Claude plan."));
    buttons.appendChild(h("button", {class: "btn danger", onclick: () => confirmDialog("Sign out of Claude Code?", "Claude Code on this device will need a new sign-in.", "Sign out", async () => { try { await POST("ai/logout"); toast("Signed out"); } catch (e) { fail(e); } })}, "Sign out"));
    buttons.appendChild(h("button", {class: "btn primary", onclick: closeModal}, "Close"));
  } else {
    if (login && login.state === "failed") box.appendChild(h("p", {class: "err"}, login.error || "Sign-in did not finish."));
    box.appendChild(h("p", {class: "desc"}, "Signing in uses Claude Code's official browser login (claude auth login). mCode never sees your password or tokens. A Claude subscription and an Anthropic API key are different: this signs in your Claude account."));
    buttons.appendChild(h("button", {class: "btn", onclick: closeModal}, "Close"));
    buttons.appendChild(h("button", {class: "btn primary", onclick: async () => { try { const st = await POST("ai/login"); S.ai.login = st; renderLogin(); } catch (e) { fail(e); } }}, "Sign in with Claude"));
  }
  box.appendChild(buttons);
}

// ------------------------------------------------------------- new project
function newProjectWizard(platform) {
  if (!S.boards) return;
  const w = {platform: typeof platform === "string" ? platform : "esp32", board: null, option: null, name: "", baud: 115200, cdc: false};
  const box = h("div", {});
  const draw = () => {
    box.innerHTML = "";
    box.appendChild(h("h2", {}, "New project"));
    box.appendChild(h("p", {class: "desc"}, "mCode generates the structure each toolchain expects. Board definitions come from the tools themselves."));
    const form = h("div", {class: "form"});
    form.appendChild(h("span", {}, "Platform"));
    form.appendChild(h("div", {class: "choices"}, Object.entries(S.boards.platforms).map(([k, v]) => h("div", {class: "choice" + (w.platform === k ? " active" : ""), onclick: () => { w.platform = k; w.board = null; w.option = null; draw(); }}, v))));
    const boards = S.boards.boards.filter((b) => b.platform === w.platform);
    if (!w.board) w.board = boards[0];
    form.appendChild(h("span", {}, "Board"));
    const bsel = h("select", {}, boards.map((b) => h("option", {value: b.id, selected: w.board && b.id === w.board.id}, `${b.name} (${b.mcu})`)));
    bsel.addEventListener("change", () => { w.board = boards.find((b) => b.id === bsel.value); w.option = null; draw(); });
    form.appendChild(bsel);
    if (w.board && w.board.notes) form.appendChild(h("span", {class: "help"}, w.board.notes));
    const options = w.board ? w.board.options : [];
    if (!w.option) w.option = options[0];
    form.appendChild(h("span", {}, "Framework"));
    form.appendChild(h("div", {class: "choices"}, options.map((o) => {
      const toolId = {"arduino-cli": "arduino-cli", platformio: "platformio", "esp-idf": "esp-idf", make: "make"}[o.toolchain];
      const tool = (S.state.tools || []).find((t) => t.id === toolId);
      return h("div", {class: "choice" + (w.option === o ? " active" : ""), onclick: () => { w.option = o; draw(); }}, o.label, h("small", {class: tool && tool.found ? "ok" : "warn"}, tool && tool.found ? "installed" : "not installed"));
    })));
    form.appendChild(h("span", {}, "Name"));
    const name = h("input", {value: w.name, placeholder: "e.g. ESP32 Robot"});
    name.addEventListener("input", () => { w.name = name.value; });
    form.appendChild(name);
    form.appendChild(h("span", {}, "Serial baud"));
    const baud = h("select", {}, [9600, 57600, 115200, 230400, 460800, 921600].map((b) => h("option", {value: b, selected: b === w.baud}, b)));
    baud.addEventListener("change", () => { w.baud = Number(baud.value); });
    form.appendChild(baud);
    if (w.board && w.board.native_usb && w.board.platform === "esp32" && w.option && w.option.framework === "arduino") {
      form.appendChild(h("span", {}, "USB serial"));
      const cdc = h("input", {type: "checkbox", checked: w.cdc});
      cdc.addEventListener("change", () => { w.cdc = cdc.checked; });
      form.appendChild(h("label", {}, cdc, "Serial over the chip's own USB port (USB CDC on boot)"));
    }
    form.appendChild(h("span", {}, "Location"));
    form.appendChild(h("span", {class: "muted"}, (S.state && S.state.project ? "" : "") + "Project folder from Settings (" + (S.settings["dev.projects_dir"] || "~/mcode-projects") + ")"));
    box.appendChild(form);
    box.appendChild(h("div", {class: "buttons"}, h("button", {class: "btn", onclick: closeModal}, "Cancel"),
      h("button", {class: "btn primary", onclick: async () => {
        try {
          const res = await POST("projects/create", {name: w.name, board_id: w.board.id, toolchain: w.option.toolchain, framework: w.option.framework, baud: w.baud, usb_cdc_on_boot: w.cdc});
          closeModal(); toast("Project ready: " + res.project.name, "ok");
          await loadProject(res.project.id); setView("explorer");
          const main = S.tree.find((e) => /\.(ino|cpp|c)$/.test(e.path) && !e.path.startsWith("test/"));
          if (main) openFile(main.path);
          if (res.requirements.length) { const r = res.requirements[0]; confirmDialog(`${r.label} is needed to build`, "Install it now? It is downloaded from the official source.", "Install", () => handleFix(r.fix)); }
        } catch (e) { fail(e); }
      }}, "Create project")));
  };
  draw(); openModal(box);
}
function openFolderDialog(copy) {
  const input = h("input", {placeholder: "/home/you/path/to/project", style: "width:100%"});
  openModal(h("div", {}, h("h2", {}, copy ? "Import a project (copy)" : "Open a project folder"), h("p", {class: "desc"}, "A PlatformIO, Arduino, ESP-IDF or Makefile project on this device."), input,
    h("div", {class: "buttons"}, h("button", {class: "btn", onclick: closeModal}, "Cancel"), h("button", {class: "btn primary", onclick: async () => { try { const r = await POST(copy ? "projects/import" : "projects/open", {path: input.value, copy: !!copy}); closeModal(); await loadProject(r.project.id); setView("explorer"); } catch (e) { fail(e); } }}, copy ? "Import" : "Open"))));
}
function cloneDialog() {
  const input = h("input", {placeholder: "https://github.com/owner/repo.git", style: "width:100%"});
  openModal(h("div", {}, h("h2", {}, "Clone from Git"), input, h("div", {class: "buttons"}, h("button", {class: "btn", onclick: closeModal}, "Cancel"), h("button", {class: "btn primary", onclick: async () => { try { const job = await POST("projects/clone", {url: input.value}); closeModal(); S.outputJob = job.id; showPanel("output"); toast("Cloning… see Build output"); } catch (e) { fail(e); } }}, "Clone"))));
}
function zipImport() {
  const file = h("input", {type: "file", accept: ".zip"});
  file.addEventListener("change", async () => {
    const f = file.files[0]; if (!f) return;
    try {
      const response = await fetch("/api/zip", {method: "POST", credentials: "same-origin", headers: {"X-mCode": "1", "X-Filename": f.name}, body: f});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error);
      await loadProject(data.project.id); setView("explorer"); toast("Imported " + data.project.name, "ok");
    } catch (e) { fail(e); }
  });
  file.click();
}
async function projectSettings() {
  const p = S.project; const box = h("div", {});
  const form = h("div", {class: "form"});
  const baud = h("select", {}, BAUDS.map((b) => h("option", {value: b, selected: b === p.baud}, b)));
  form.append(h("span", {}, "Serial baud"), baud, h("span", {}, "Toolchain"), h("span", {}, `${p.toolchain_name} (${p.framework})`), h("span", {}, "Folder"), h("span", {class: "muted"}, p.path));
  const boardBox = h("div", {class: "form"}, h("span", {class: "muted"}, "Loading board settings…"));
  box.append(h("h2", {}, "Project settings"), h("h3", {}, "Board"), boardBox, h("h3", {}, "Project"), form, h("div", {class: "buttons"}, h("button", {class: "btn", onclick: closeModal}, "Close"), h("button", {class: "btn primary", onclick: async () => { try { await POST(`project/${p.id}/update`, {fields: {baud: Number(baud.value)}}); closeModal(); await loadProject(p.id); toast("Saved", "ok"); } catch (e) { fail(e); } }}, "Save")));
  openModal(box);
  const show = (data) => {
    boardBox.innerHTML = "";
    if (data.connected && data.connected.id !== data.project.board_id) {
      boardBox.append(h("span", {class: "warn"}, "Connected"), h("span", {}, `${data.connected.name} is on the port, not ${data.project.board_name}. `, h("a", {onclick: () => change("board", data.connected.id)}, `Use ${data.connected.name}`)));
    }
    for (const st of data.settings) {
      let input;
      if (st.kind === "toggle") input = h("label", {}, h("input", {type: "checkbox", checked: st.value === "1", onchange: (e) => change(st.id, e.target.checked ? "1" : "0")}), st.note ? " " + st.note : "");
      else input = h("select", {title: st.note || "", onchange: (e) => change(st.id, e.target.value)}, st.choices.map((c) => h("option", {value: c.value, selected: c.value === st.value}, c.label)));
      boardBox.append(h("span", {}, st.label), input);
    }
    boardBox.append(h("span", {}, ""), h("span", {class: "muted"}, "Changes are saved at once. Build again before flashing."));
  };
  const change = async (id, value) => {
    try { const data = await POST(`project/${p.id}/board`, {id, value}); show(data); await loadProject(p.id); toast("Saved: build before flashing", "ok"); } catch (e) { fail(e); }
  };
  try { show(await GET(`project/${p.id}/board`)); } catch (e) { boardBox.innerHTML = ""; boardBox.append(h("span", {class: "err"}, e.message)); }
}
async function renameProject() { const name = prompt("New name:", S.project.name); if (!name) return; try { const r = await POST(`project/${S.project.id}/rename`, {name}); closeAllTabs(); await loadProject(r.project.id); } catch (e) { fail(e); } }
async function duplicateProject() { try { const r = await POST(`project/${S.project.id}/duplicate`, {}); toast("Created " + r.project.name, "ok"); } catch (e) { fail(e); } }
function deleteProject() {
  const input = h("input", {placeholder: S.project.name, style: "width:100%"});
  openModal(h("div", {}, h("h2", {}, "Delete project?"), h("p", {class: "desc"}, "The folder moves to the project trash (" + S.settings["dev.projects_dir"] + "/.trash). Type the project's name to confirm."), input,
    h("div", {class: "buttons"}, h("button", {class: "btn", onclick: closeModal}, "Cancel"), h("button", {class: "btn danger solid", onclick: async () => { try { await POST(`project/${S.project.id}/delete`, {confirm: input.value}); closeModal(); S.project = null; closeAllTabs(); refreshState(); setView("dashboard"); } catch (e) { fail(e); } }}, "Delete"))));
}
async function backupsDialog() {
  const p = S.project; const list = p.backups || [];
  openModal(h("div", {}, h("h2", {}, "Backups"), h("p", {class: "desc"}, "mCode backs up before AI changes, restores and renames. Restoring first backs up the current state."),
    list.length ? h("div", {}, list.map((b) => h("div", {class: "line", style: "display:flex;gap:8px;align-items:center;padding:4px 0"}, h("span", {style: "flex:1"}, `${fmtTime(b.time)} · ${b.reason}`), h("span", {class: "muted"}, fmtBytes(b.size)), h("button", {class: "btn small", onclick: () => confirmDialog("Restore this backup?", "The current files are backed up first.", "Restore", async () => { try { await POST(`project/${p.id}/restore`, {name: b.name}); closeAllTabs(); await loadProject(p.id); toast("Restored", "ok"); } catch (e) { fail(e); } })}, "Restore")))) : h("p", {class: "muted"}, "No backups yet."),
    h("div", {class: "buttons"}, h("button", {class: "btn", onclick: async () => { await POST(`project/${p.id}/backup`, {}); closeModal(); await loadProject(p.id); backupsDialog(); }}, "Back up now"), h("button", {class: "btn primary", onclick: closeModal}, "Close"))));
}

// ---------------------------------------------------------------- settings
function settingsDialog() {
  const box = h("div", {}, h("h2", {}, "Settings"));
  const values = {};
  let section = "";
  const form = h("div", {class: "form"});
  for (const f of S.fields) {
    if (f.section !== section) { section = f.section; form.appendChild(h("div", {class: "settings-section full"}, section)); }
    let input;
    if (f.kind === "bool") { input = h("input", {type: "checkbox", checked: f.value}); input.addEventListener("change", () => { values[f.key] = input.checked; }); input = h("label", {}, input, "on"); }
    else if (f.choices && f.choices.length) { input = h("select", {}, f.choices.map((c) => h("option", {value: c, selected: c === f.value}, f.choice_labels[c] || (c === 0 ? "Default" : c)))); input.addEventListener("change", () => { values[f.key] = f.kind === "int" ? Number(input.value) : input.value; }); }
    else { input = h("input", {value: f.value, type: f.kind === "int" ? "number" : "text"}); input.addEventListener("change", () => { values[f.key] = f.kind === "int" ? Number(input.value) : input.value; }); }
    form.appendChild(h("span", {}, f.label)); form.appendChild(input);
    if (f.help) form.appendChild(h("span", {class: "help"}, f.help));
  }
  const editor = h("div", {class: "form"}, h("div", {class: "settings-section full"}, "This browser"));
  const autosave = h("input", {type: "checkbox", checked: store.get("autosave", true)}); autosave.addEventListener("change", () => store.set("autosave", autosave.checked));
  const complete = h("input", {type: "checkbox", checked: store.get("complete", true)}); complete.addEventListener("change", () => store.set("complete", complete.checked));
  editor.append(h("span", {}, "Automatic saving"), h("label", {}, autosave, "save 1.5 s after typing stops"), h("span", {}, "Suggestions while typing"), h("label", {}, complete, "on"));
  box.append(form, editor, h("div", {class: "buttons"}, h("button", {class: "btn", onclick: closeModal}, "Cancel"), h("button", {class: "btn primary", onclick: async () => {
    try { const r = await POST("settings", {values}); const errs = Object.values(r.errors || {}); if (errs.length) toast(errs.join("; "), "err", 8000); else toast("Settings saved", "ok"); closeModal(); await loadSettings(); } catch (e) { fail(e); }
  }}, "Save")));
  openModal(box);
}

// ----------------------------------------------------------------- serial
function renderSerialPorts() {
  const sel = $("#serial-port"); const current = sel.value || S.monitor.port || S.selectedPort;
  sel.innerHTML = "";
  S.devices.forEach((d) => sel.appendChild(h("option", {value: d.port, selected: d.port === current}, `${d.short_port} · ${deviceLabel(d)}`)));
  if (!S.devices.length) sel.appendChild(h("option", {value: ""}, "No port"));
}
function onMonitorState(st) {
  Object.assign(S.monitor, st);
  $("#serial-connect").textContent = st.connected ? "Disconnect" : st.suspended ? "Waiting…" : "Connect";
  $("#serial-pause").textContent = st.paused ? "Resume" : "Pause";
  if (st.error && !st.connected) toast("Serial: " + st.error, "warn");
}
function onMonitorLines(d) {
  S.monitor.lines.push(...d.lines);
  if (S.monitor.lines.length > 5000) S.monitor.lines.splice(0, S.monitor.lines.length - 5000);
  if (!$("#serial-hex").checked) appendSerial(d.lines);
}
function serialLine(l) {
  const ts = $("#serial-ts").checked ? h("span", {class: "ts"}, new Date(l.t * 1000).toTimeString().slice(0, 8) + "." + String(Math.floor((l.t % 1) * 1000)).padStart(3, "0") + " ") : null;
  return h("span", {class: l.dir === "tx" ? "l-tx" : l.dir === "info" ? "l-info" : ""}, ts, (l.dir === "tx" ? "> " : l.dir === "info" ? "-- " : "") + l.text + "\n");
}
function appendSerial(lines) {
  const out = $("#serial-out");
  lines.forEach((l) => out.appendChild(serialLine(l)));
  while (out.childNodes.length > 5000) out.firstChild.remove();
  if ($("#serial-auto").checked) out.scrollTop = out.scrollHeight;
}
async function renderSerial() {
  const out = $("#serial-out"); out.innerHTML = "";
  if ($("#serial-hex").checked) { const data = await GET("monitor/hex?bytes=8192"); out.textContent = data.lines.join("\n"); }
  else appendSerial(S.monitor.lines);
}
async function serialSearch(q) {
  const out = $("#serial-out");
  if (!q) { renderSerial(); return; }
  const data = await GET("monitor/search?q=" + encodeURIComponent(q));
  out.innerHTML = "";
  data.hits.forEach((l) => out.appendChild(serialLine(l)));
  out.appendChild(h("span", {class: "l-info"}, `-- ${data.hits.length} line(s) match “${q}”\n`));
}
async function loadMonitor() {
  try { const st = await GET("monitor?count=1000"); S.monitor.lines = st.lines; onMonitorState(st); renderSerial(); } catch (e) { /* hardware off */ }
}

// --------------------------------------------------------------- terminal
async function newTerminal() {
  if (!S.project) { toast("Open a project first.", "warn"); return; }
  let session;
  try { session = await POST("term/open", {project_id: S.project.id}); } catch (e) { fail(e); return; }
  const host = h("div", {});
  $("#term-host").appendChild(host);
  const term = new Terminal({fontFamily: "DejaVu Sans Mono, monospace", fontSize: 13, cursorBlink: true, scrollback: S.settings["ui.terminal_scrollback"] || 5000, theme: {background: "#000000"}});
  const fit = new FitAddon.FitAddon();
  term.loadAddon(fit); term.open(host);
  let pending = ""; let timer = null;
  term.onData((d) => { pending += d; if (!timer) timer = setTimeout(() => { const data = pending; pending = ""; timer = null; POST(`term/${session.id}/input`, {data}).catch(() => {}); }, 15); });
  const resize = () => { try { fit.fit(); POST(`term/${session.id}/resize`, {cols: term.cols, rows: term.rows}).catch(() => {}); } catch (e) { /* hidden */ } };
  S.terms.set(session.id, {term, host, fit, resize});
  showTerm(session.id); resize();
  renderTermBar();
}
function showTerm(id) {
  S.activeTerm = id;
  for (const [tid, t] of S.terms) t.host.classList.toggle("hidden", tid !== id);
  renderTermBar();
  const t = S.terms.get(id); if (t) setTimeout(() => { t.resize(); t.term.focus(); }, 0);
}
function renderTermBar() {
  const bar = $("#term-bar"); bar.innerHTML = "";
  let n = 1;
  for (const [id] of S.terms) bar.appendChild(h("span", {class: "t" + (id === S.activeTerm ? " active" : ""), onclick: () => showTerm(id)}, `Terminal ${n++}`, " ", h("button", {class: "close", style: "border:0;background:none;color:inherit;cursor:pointer", onclick: (ev) => { ev.stopPropagation(); closeTerm(id); }}, "×")));
  bar.appendChild(h("button", {class: "btn small", onclick: newTerminal}, "+ New terminal"));
}
function closeTerm(id) {
  const t = S.terms.get(id); if (!t) return;
  POST(`term/${id}/close`).catch(() => {}); t.term.dispose(); t.host.remove(); S.terms.delete(id);
  if (S.activeTerm === id) { const next = Array.from(S.terms.keys()).pop(); if (next) showTerm(next); else S.activeTerm = null; }
  renderTermBar();
}
function onTerminal(d) {
  const t = S.terms.get(d.id); if (!t) return;
  if (d.closed) { t.term.write("\r\n[process ended]\r\n"); return; }
  const bin = atob(d.data); const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  t.term.write(bytes);
}

// ----------------------------------------------------------------- layout
function showPanel(name) {
  document.body.classList.remove("no-bottom");
  $$("#bottom-tabs button[data-panel]").forEach((b) => b.classList.toggle("active", b.dataset.panel === name));
  $$("#bottom .panel").forEach((p) => p.classList.toggle("hidden", p.dataset.panel !== name));
  if (name === "terminal" && !S.terms.size) newTerminal();
  if (name === "terminal" && S.activeTerm) setTimeout(() => S.terms.get(S.activeTerm).resize(), 0);
  if (name === "serial") { renderSerialPorts(); loadMonitor(); }
  if (name === "tests") renderTests();
  if (cm) cm.refresh();
}
function restoreLayout() {
  const sizes = store.get("sizes", {});
  if (sizes.side) $("#side").style.width = sizes.side + "px";
  if (sizes.ai) $("#ai").style.width = sizes.ai + "px";
  if (sizes.bottom) $("#bottom").style.height = sizes.bottom + "px";
  if (store.get("noai", false)) document.body.classList.add("no-ai");
  if (store.get("nobottom", false)) document.body.classList.add("no-bottom");
  $$(".gutter").forEach((g) => g.addEventListener("mousedown", (ev) => startResize(ev, g.dataset.resize)));
}
function startResize(ev, what) {
  ev.preventDefault();
  const el = what === "side" ? $("#side") : what === "ai" ? $("#ai") : $("#bottom");
  const start = what === "bottom" ? ev.clientY : ev.clientX;
  const size = what === "bottom" ? el.offsetHeight : el.offsetWidth;
  const aiLeft = $("#main").classList.contains("ai-left");
  const move = (e) => {
    const delta = (what === "bottom" ? e.clientY : e.clientX) - start;
    let next = what === "side" ? size + delta : what === "ai" ? size + (aiLeft ? delta : -delta) : size - delta;
    next = Math.max(what === "bottom" ? 80 : 160, Math.min(next, what === "bottom" ? innerHeight - 200 : innerWidth - 400));
    el.style[what === "bottom" ? "height" : "width"] = next + "px";
    if (cm) cm.refresh();
  };
  const up = () => { document.removeEventListener("mousemove", move); document.removeEventListener("mouseup", up); const sizes = store.get("sizes", {}); sizes[what] = what === "bottom" ? el.offsetHeight : el.offsetWidth; store.set("sizes", sizes); for (const t of S.terms.values()) t.resize(); };
  document.addEventListener("mousemove", move); document.addEventListener("mouseup", up);
}

// ------------------------------------------------------------------ modal
function openModal(content) { const m = $("#modal"); m.innerHTML = ""; m.appendChild(content); $("#modal-back").classList.remove("hidden"); const first = m.querySelector("input,select,textarea"); if (first) first.focus(); }
function closeModal() { $("#modal-back").classList.add("hidden"); $("#modal").innerHTML = ""; }
function confirmDialog(title, text, label, run) {
  openModal(h("div", {}, h("h2", {}, title), text ? h("p", {class: "desc"}, text) : null, h("div", {class: "buttons"}, h("button", {class: "btn", onclick: closeModal}, "Cancel"), h("button", {class: "btn primary", onclick: () => { closeModal(); run(); }}, label))));
}

// ---------------------------------------------------------------- binding
function bindChrome() {
  $("#project-pick").addEventListener("click", (e) => { e.stopPropagation(); projectMenu($("#project-pick")); });
  $("#device-pick").addEventListener("click", (e) => { e.stopPropagation(); deviceMenu($("#device-pick")); });
  $("#btn-build").addEventListener("click", () => runBuild("build"));
  $("#btn-test").addEventListener("click", () => runBuild("test"));
  $("#btn-upload").addEventListener("click", () => flashDialog("upload"));
  $("#btn-more").addEventListener("click", (e) => { e.stopPropagation(); showMenu($("#btn-more"), [
    {label: "Rebuild", run: () => runBuild("rebuild")}, {label: "Clean", run: () => runBuild("clean")},
    {label: "Static analysis", run: () => runBuild("check")}, "-",
    {label: "Identify the chip (ESP32)", run: () => S.selectedPort ? identify(S.selectedPort) : toast("Choose a board first.", "warn")},
    {label: "Erase flash (ESP32)…", run: () => flashDialog("erase")}, "-",
    {label: "Open the IDE on the HDMI desktop", run: async () => { const r = await POST("web/open-desktop"); toast(r.ok ? "Opening…" : r.error, r.ok ? "ok" : "warn"); }},
  ]); });
  $("#btn-settings").addEventListener("click", settingsDialog);
  $("#claude-pill").addEventListener("click", claudeDialog);
  $$("#activity button[data-view]").forEach((b) => b.addEventListener("click", () => setView(b.dataset.view)));
  $$("#activity button[data-toggle]").forEach((b) => b.addEventListener("click", () => {
    const cls = b.dataset.toggle === "ai" ? "no-ai" : "no-bottom";
    document.body.classList.toggle(cls); store.set(b.dataset.toggle === "ai" ? "noai" : "nobottom", document.body.classList.contains(cls)); if (cm) cm.refresh();
  }));
  $$("#bottom-tabs button[data-panel]").forEach((b) => b.addEventListener("click", () => showPanel(b.dataset.panel)));
  $("#job-cancel").addEventListener("click", () => { const id = $("#job-cancel").dataset.job; if (id) POST(`job/${id}/cancel`).catch(fail); });
  $("#ai-send").addEventListener("click", aiSend);
  $("#ai-stop").addEventListener("click", () => POST("ai/cancel").catch(fail));
  $("#ai-text").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); aiSend(); } });
  $("#ai-attach-file").addEventListener("click", () => S.active ? attach({type: "file", path: S.active}) : toast("Open a file first.", "warn"));
  $("#ai-attach-serial").addEventListener("click", () => attach({type: "serial", path: "serial"}));
  $$("#ai-quick button").forEach((b) => b.addEventListener("click", () => aiKind(b.dataset.kind)));
  $("#ai-mode").addEventListener("change", () => POST("settings", {values: {"ai.mode": $("#ai-mode").value}}).then(() => { S.settings["ai.mode"] = $("#ai-mode").value; toast("AI mode: " + $("#ai-mode").selectedOptions[0].textContent, "ok"); }).catch(fail));
  $("#ai-new").addEventListener("click", () => S.project && confirmDialog("New conversation?", "Claude forgets this project's conversation (the files stay).", "Start over", () => POST(`ai/${S.project.id}/clear`).then(loadConversation).catch(fail)));
  $("#modal-back").addEventListener("click", (e) => { if (e.target.id === "modal-back") closeModal(); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { closeModal(); hideMenu(); }
    if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); saveFile(); }
    if ((e.ctrlKey || e.metaKey) && e.key === "b") { e.preventDefault(); runBuild("build"); }
    if ((e.ctrlKey || e.metaKey) && e.key === "`") { e.preventDefault(); showPanel("terminal"); }
  });
  // serial
  const bauds = $("#serial-baud"); BAUDS.forEach((b) => bauds.appendChild(h("option", {value: b, selected: b === 115200}, b + " baud")));
  $("#serial-connect").addEventListener("click", async () => {
    try { if (S.monitor.connected || S.monitor.suspended) await POST("monitor/disconnect"); else await POST("monitor/connect", {port: $("#serial-port").value, baud: Number(bauds.value)}); } catch (e) { fail(e); }
  });
  bauds.addEventListener("change", async () => { if (S.monitor.connected) { try { await POST("monitor/connect", {port: S.monitor.port, baud: Number(bauds.value)}); } catch (e) { fail(e); } } });
  $("#serial-pause").addEventListener("click", () => POST("monitor/pause", {paused: !S.monitor.paused}).catch(fail));
  $("#serial-clear").addEventListener("click", () => POST("monitor/clear").catch(fail));
  $("#serial-save").addEventListener("click", async () => { try { const r = await POST("monitor/save", {project_id: S.project && S.project.id, to_project: !!S.project}); toast("Saved: " + r.path, "ok", 6000); } catch (e) { fail(e); } });
  $("#serial-reset").addEventListener("click", () => POST("monitor/reset").catch(fail));
  $("#serial-ai").addEventListener("click", () => aiKind("analyze-serial"));
  $("#serial-hex").addEventListener("change", renderSerial);
  $("#serial-ts").addEventListener("change", renderSerial);
  $("#serial-search").addEventListener("keydown", (e) => { if (e.key === "Enter") serialSearch($("#serial-search").value).catch(fail); });
  const send = () => POST("monitor/send", {text: $("#serial-input").value, ending: $("#serial-ending").value, hex: $("#serial-send-hex").checked}).then(() => { $("#serial-input").value = ""; }).catch(fail);
  $("#serial-send").addEventListener("click", send);
  $("#serial-input").addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });
  window.addEventListener("resize", () => { if (cm) cm.refresh(); for (const t of S.terms.values()) t.resize(); });
  window.addEventListener("beforeunload", (e) => { if (Array.from(S.files.values()).some((f) => f.dirty)) { e.preventDefault(); e.returnValue = ""; } });
}

init().catch(fail);
