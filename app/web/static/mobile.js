/* mCode Studio: phone and tablet layouts, and the Security page (all layouts).
   Same API, same state (S) and the same render functions as the desktop IDE
   (app.js); this file only arranges them for a small touch screen:
   phone = one view at a time with a bottom tab bar, tablet = the desktop
   layout with touch-sized controls. Nothing here builds, flashes or edits
   files on its own. */
"use strict";

const M = {layout: "", view: store.get("mview", "home"), back: null, keyboard: false, securityOpen: false};
// A phone held sideways is still a phone: short screens get the phone layout too.
const PHONE = window.matchMedia("(max-width: 760px), (max-height: 500px)");
const TABLET = window.matchMedia("(min-width: 761px) and (max-width: 1100px) and (min-height: 501px)");
const TAB_OF = {home: "home", projects: "projects", editor: "projects", build: "home", terminal: "projects",
  ai: "ai", devices: "devices", serial: "devices", settings: "settings"};
const ICON = {
  back: '<path d="M15 5l-7 7 7 7"/>', plus: '<path d="M12 5v14M5 12h14"/>', more: '<path d="M5 12h.01M12 12h.01M19 12h.01"/>',
  play: '<path d="M8 5l11 7-11 7z"/>', spark: '<path d="M12 3l1.9 5.6L19.5 10l-5.6 1.9L12 17.5l-1.9-5.6L4.5 10l5.6-1.4z"/>',
  refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/>', stop: '<path d="M7 7h10v10H7z"/>',
};
function svgIcon(name) {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("aria-hidden", "true");
  s.innerHTML = ICON[name] || "";
  return s;
}

// ------------------------------------------------------------------ layout
function applyLayout() {
  const layout = PHONE.matches ? "phone" : TABLET.matches ? "tablet" : "desktop";
  if (layout === M.layout) return;
  M.layout = layout;
  document.body.classList.remove("layout-phone", "layout-tablet", "layout-desktop");
  document.body.classList.add("layout-" + layout);
  if (typeof applyEditorSettings === "function") applyEditorSettings();
  if (cm) cm.setOption("lineWrapping", layout === "phone");
  if (typeof placeholder === "function") placeholder();
  if (layout === "phone") mShow(M.view in TAB_OF ? M.view : "home", {quiet: true});
  else { document.body.classList.remove("keyboard"); if (cm) setTimeout(() => cm.refresh(), 0); }
}
function trackViewport() {
  // Keyboard-aware: the app is as tall as what the keyboard leaves visible.
  // The keyboard counts as open only while something editable has the focus and
  // the visible height dropped well below the tallest seen at this width.
  const vv = window.visualViewport;
  let tallest = 0; let width = 0;
  const editing = () => {
    const el = document.activeElement;
    return !!el && (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || el.isContentEditable);
  };
  const update = () => {
    const height = vv ? vv.height : window.innerHeight;
    if (window.innerWidth !== width) { width = window.innerWidth; tallest = 0; }
    tallest = Math.max(tallest, height);
    document.documentElement.style.setProperty("--vvh", Math.round(height) + "px");
    const keyboard = editing() && tallest - height > 120;
    if (keyboard !== M.keyboard) { M.keyboard = keyboard; document.body.classList.toggle("keyboard", keyboard); }
    if (vv && isPhone() && vv.offsetTop) window.scrollTo(0, 0);
    if (cm && isPhone() && M.view === "editor") later("cm-refresh", () => cm.refresh(), 60);
  };
  if (vv) { vv.addEventListener("resize", update); vv.addEventListener("scroll", update); }
  window.addEventListener("resize", update);
  document.addEventListener("focusin", () => setTimeout(update, 50));
  document.addEventListener("focusout", () => setTimeout(update, 50));
  update();
}

// ------------------------------------------------------------------ views
function mShow(view, opts = {}) {
  if (!isPhone()) { M.view = view; return; }
  M.view = view;
  if (["home", "projects", "ai", "devices", "settings"].includes(view)) store.set("mview", view);
  document.body.dataset.mview = view;
  $$("#tabbar button").forEach((b) => b.classList.toggle("active", b.dataset.tab === TAB_OF[view]));
  if (view === "editor") { if (!S.active) { mShow("projects"); return; } showEditor(); setTimeout(() => cm.refresh(), 0); }
  if (view === "ai") { renderAI(); setTimeout(() => { const b = $("#ai-body"); b.scrollTop = b.scrollHeight; }, 0); }
  if (view === "build") showPanel(["output", "problems", "tests"].includes(currentPanel()) ? currentPanel() : "output");
  if (view === "serial") showPanel("serial");
  if (view === "terminal") showPanel("terminal");
  renderHeader();
  mRefresh(true);
  if (!opts.quiet) $("#m-pages").scrollTop = 0;
}
function currentPanel() { const b = $("#bottom-tabs button.active"); return b ? b.dataset.panel : "output"; }
function mRefresh(now) {
  if (!isPhone()) return;
  const run = () => {
    if (M.view === "home") renderMobileHome();
    else if (M.view === "projects") renderMobileProjects();
    else if (M.view === "devices") renderMobileDevices();
    else if (M.view === "settings") renderMobileSettings();
    else if (M.view === "editor") renderHeader();
    tickJobStatus();
  };
  if (now) run(); else later("mobile-refresh", run, 250);
}

function headerButton(label, run, opts = {}) {
  const b = h("button", {class: opts.strong ? "m-act-strong" : "", title: opts.title || label, "aria-label": opts.title || label, disabled: opts.disabled});
  if (opts.icon) b.appendChild(svgIcon(opts.icon)); else b.textContent = label;
  b.addEventListener("click", (e) => { e.stopPropagation(); run(e); });
  return b;
}
function renderHeader() {
  if (!isPhone()) return;
  const title = $("#m-title"); const sub = $("#m-subtitle"); const acts = $("#m-actions"); const back = $("#m-back");
  acts.innerHTML = ""; sub.textContent = ""; title.classList.remove("dirty");
  let backTo = null; let backLabel = "Back";
  const v = M.view;
  const proj = S.project;
  if (v === "home") {
    title.textContent = "mCode Studio";
    sub.textContent = proj ? proj.name : "";
    acts.appendChild(headerButton("Ask AI", () => mShow("ai"), {icon: "spark", title: "AI Engineer"}));
  } else if (v === "projects") {
    title.textContent = "Projects";
    acts.appendChild(headerButton("New", () => newProjectWizard(), {icon: "plus", title: "New project"}));
  } else if (v === "editor") {
    const f = S.files.get(S.active);
    title.textContent = S.active ? S.active.split("/").pop() : "Editor";
    if (f && f.dirty) title.classList.add("dirty");
    sub.textContent = proj ? proj.name : "";
    backTo = "projects"; backLabel = "Files";
    acts.appendChild(headerButton("Save", () => saveFile(), {strong: true, disabled: !(f && f.dirty)}));
    acts.appendChild(headerButton("More", (e) => editorMenu(e.currentTarget), {icon: "more", title: "More"}));
  } else if (v === "ai") {
    title.textContent = "AI Engineer";
    sub.textContent = proj ? proj.name : "";
    if (proj) acts.appendChild(headerButton("New", () => newConversation(), {icon: "plus", title: "New conversation"}));
  } else if (v === "build") {
    title.textContent = "Build"; sub.textContent = proj ? proj.name : "";
    backTo = "home"; backLabel = "Home";
    const running = runningJob();
    if (running) acts.appendChild(headerButton("Stop", () => POST(`job/${running.id}/cancel`).catch(fail)));
    else acts.appendChild(headerButton("Build", () => mBuild("build"), {strong: true}));
  } else if (v === "terminal") {
    title.textContent = "Terminal"; backTo = "projects";
  } else if (v === "devices") {
    title.textContent = "Devices";
    acts.appendChild(headerButton("Refresh", () => POST("devices/refresh").then(() => mRefresh(true)).catch(fail), {icon: "refresh", title: "Look for boards"}));
  } else if (v === "serial") {
    title.textContent = "Serial monitor"; backTo = "devices"; backLabel = "Devices";
    sub.textContent = S.monitor.connected ? `${(S.monitor.port || "").split("/").pop()} · ${S.monitor.baud} baud` : "Not connected";
  } else if (v === "settings") {
    title.textContent = "Settings";
  }
  M.back = backTo;
  back.classList.toggle("hidden", !backTo);
  $("#m-back-label").textContent = backLabel;
}
function runningJob() {
  const running = Array.from(S.jobs.values()).filter((j) => j.state === "running" || j.state === "queued");
  return running.length ? running[running.length - 1] : null;
}
function mBuild(action) { mShow("build"); runBuild(action); }

function editorMenu(anchor) {
  const items = [
    {label: "Build", run: () => mBuild("build")},
    {label: "Ask AI about this file", run: () => { if (S.active) attach({type: "file", path: S.active}); mShow("ai"); $("#ai-text").focus(); }},
    {label: "Find", run: () => cm.execCommand("find")}, {label: "Find and replace", run: () => cm.execCommand("replace")},
    {label: "Undo", run: () => cm.undo()}, {label: "Redo", run: () => cm.redo()},
  ];
  if (S.files.size > 1) items.push("-", ...Array.from(S.files.keys()).filter((p) => p !== S.active).map((p) => ({label: "Switch to " + p.split("/").pop(), run: () => { switchTo(p); mShow("editor"); }})));
  items.push("-", {label: "Close file", run: () => { closeTab(S.active); if (!S.active) mShow("projects"); else mShow("editor"); }});
  showMenu(anchor, items);
}
function modeSheet() { modePicker(null); }

// --------------------------------------------------------------- job bar
function renderJobBar() {
  const bar = $("#m-job");
  if (!bar) return;
  const job = isPhone() ? runningJob() : null;
  const inChat = job && M.view === "ai" && (job.kind === "ai" || (job.meta && job.meta.source === "ai"));
  if (!job || M.view === "build" || inChat) { bar.classList.add("hidden"); return; }
  bar.classList.remove("hidden"); bar.innerHTML = "";
  const elapsed = job.started ? Math.round(Date.now() / 1000 - job.started) : 0;
  const pct = job.progress != null ? Math.round(job.progress * 100) : null;
  const fill = h("i", {style: `width:${pct != null ? pct : 30}%`});
  bar.append(h("span", {class: "spin"}), h("span", {class: "what"}, `${job.title} · ${job.phase}${pct != null ? " " + pct + "%" : ""} · ${elapsed}s`), h("span", {class: "bar"}, fill),
    h("button", {onclick: (e) => { e.stopPropagation(); POST(`job/${job.id}/cancel`).catch(fail); }}, "Stop"));
}

// ------------------------------------------------------------------ home
function renderMobileHome() {
  const page = $("#m-home"); const st = S.state;
  if (!st) return;
  page.innerHTML = "";
  const proj = S.project;
  const last = proj && proj.last_build;
  if (proj) {
    const ok = last && last.state === "succeeded";
    const flash = last && last.size && last.size.flash ? ` · flash ${last.size.flash.percent}%` : "";
    const ram = last && last.size && last.size.ram ? ` · RAM ${last.size.ram.percent}%` : "";
    page.appendChild(h("div", {class: "m-card"},
      h("div", {class: "m-proj"}, h("div", {class: "plat " + proj.platform}, platformShort(proj.platform)),
        h("div", {style: "flex:1;min-width:0"}, h("h3", {}, proj.name), h("div", {class: "sub"}, `${proj.board_name} · ${proj.toolchain_name}`))),
      h("div", {class: "status"}, h("span", {class: "dot " + (!last ? "" : ok ? "ok" : "err")}),
        h("span", {}, !last ? "Not built yet" : `${last.action === "test" ? "Tests" : "Build"} ${ok ? "succeeded" : last.state}${flash}${ram}`),
        last && last.time ? h("span", {class: "muted", style: "margin-left:auto;font-size:13px"}, fmtTime(last.time)) : null),
      !ok && last && last.headline ? h("div", {class: "sub", style: "margin-top:6px"}, last.headline) : null,
      h("div", {class: "btns"}, h("button", {class: "m-btn filled", onclick: () => mBuild("build")}, "Build"),
        h("button", {class: "m-btn", onclick: () => flashDialog("upload")}, "Flash"),
        !ok && last ? h("button", {class: "m-btn", onclick: () => { mShow("ai"); aiKind("fix-build"); }}, "Fix with AI") : h("button", {class: "m-btn", onclick: () => mShow("editor")}, "Code"))));
  } else {
    page.appendChild(h("div", {class: "m-card m-empty"}, h("h3", {}, "Create your first project"),
      h("div", {}, "Choose a board (ESP32, Arduino, Pico…) and mCode sets up the project for the right tools."),
      h("button", {class: "m-btn filled", onclick: () => newProjectWizard()}, "New Project")));
  }
  const actions = [
    ["＋", "New Project", () => newProjectWizard()], ["▤", "Open Project", () => mShow("projects")], ["✦", "Ask AI", () => mShow("ai")],
    ["▶", "Build Firmware", () => mBuild("build")], ["⌁", "Connect Board", () => mShow("devices")], ["≋", "Serial Monitor", () => mShow("serial")],
  ];
  page.appendChild(h("div", {class: "m-section"}, "Quick actions"));
  page.appendChild(h("div", {class: "m-actions-grid"}, actions.map(([ico, label, run]) => h("button", {class: "m-action", onclick: run}, h("span", {class: "ico"}, ico), label))));
  // board and AI at a glance
  page.appendChild(h("div", {class: "m-section"}, "Status"));
  const group = h("div", {class: "m-group"});
  const board = S.devices.find((d) => d.port === S.selectedPort);
  group.appendChild(mRow({title: board ? deviceLabel(board) : S.devices.length ? `${S.devices.length} board(s) connected` : "No board connected",
    sub: board ? `${board.short_port}${board.readable ? "" : " · no permission"}` : S.devices.length ? "Tap to choose one" : "Plug one in by USB",
    dot: board ? (board.readable ? "ok" : "err") : S.devices.length ? "warn" : "", run: () => mShow("devices")}));
  const ai = S.ai || {};
  const aiText = {ready: (ai.auth && ai.auth.label) || "Ready", working: "Working…", "signed-out": "Not signed in", "not-installed": "Not installed", checking: "Checking…"}[ai.state] || "Unavailable";
  group.appendChild(mRow({title: "Claude", value: aiText, sub: modeShort(), dot: {ready: "ok", working: "warn", "signed-out": "warn", "not-installed": "err"}[ai.state] || "",
    run: () => (ai.state === "ready" || ai.state === "working") ? mShow("ai") : claudeDialog()}));
  page.appendChild(group);
  if (ai.memory_warning) page.appendChild(h("p", {class: "m-foot warn"}, ai.memory_warning));
  // recent activity: builds and uploads, newest first
  const activity = [].concat((st.recent_builds || []).map((b) => ({t: b.time, text: `${b.project} · ${({build: "build", rebuild: "rebuild", test: "tests", check: "analysis"})[b.action] || b.action}`, ok: b.state === "succeeded", state: b.state})),
    (st.recent_uploads || []).map((u) => ({t: u.time, text: `${u.project} → ${(u.port || "").split("/").pop()} (${u.action})`, ok: u.state === "succeeded", state: u.state}))).sort((a, b) => b.t - a.t).slice(0, 5);
  page.appendChild(h("div", {class: "m-section"}, "Recent activity"));
  const recent = h("div", {class: "m-group"});
  activity.forEach((a) => recent.appendChild(mRow({title: a.text, sub: fmtTime(a.t), dot: a.ok ? "ok" : "err", value: a.state})));
  if (!activity.length) recent.appendChild(mRow({title: "Nothing built or flashed yet", sub: "Builds and uploads appear here."}));
  page.appendChild(recent);
}
function platformShort(p) { return {esp32: "ESP", arduino: "AVR", pico: "PICO", generic: "C"}[p] || (p || "?").slice(0, 3).toUpperCase(); }
function mRow({title, sub, value, dot, run, icon, danger, chevron}) {
  const row = h("div", {class: "m-row" + (run ? " tap" : "") + (danger ? " danger" : "")},
    icon ? h("span", {class: "icon", style: icon.color ? `background:${icon.color}` : null}, icon.text) : null,
    dot != null && dot !== undefined ? h("span", {class: "dot " + dot}) : null,
    h("div", {class: "m-main"}, h("div", {class: "title"}, title), sub ? h("div", {class: "sub"}, sub) : null),
    value != null ? h("span", {class: "value"}, value) : null,
    run && chevron !== false ? h("span", {class: "chev"}) : null);
  if (run) row.addEventListener("click", run);
  return row;
}

// --------------------------------------------------------------- projects
let mProjectQuery = "";
async function renderMobileProjects() {
  const page = $("#m-projects");
  let data;
  try { data = await GET("projects?q=" + encodeURIComponent(mProjectQuery)); } catch (e) { fail(e); return; }
  page.innerHTML = "";
  const search = h("input", {class: "m-search", type: "search", placeholder: "Search projects", value: mProjectQuery, enterkeyhint: "search"});
  search.addEventListener("input", () => { mProjectQuery = search.value; later("proj-search", renderMobileProjects, 350); });
  page.appendChild(search);
  if (S.project) {
    page.appendChild(h("div", {class: "m-section"}, `Files · ${S.project.name}`));
    const files = h("div", {class: "m-group m-files"});
    const tree = h("div", {});
    renderExplorer(tree);
    files.appendChild(tree);
    files.appendChild(h("div", {class: "side-tools-row"},
      h("button", {class: "m-btn", onclick: () => newFile()}, "New file"),
      h("button", {class: "m-btn", onclick: () => searchProject()}, "Search"),
      h("button", {class: "m-btn", onclick: () => mProjectMenu(S.project)}, "More")));
    page.appendChild(files);
  }
  page.appendChild(h("div", {class: "m-section"}, "All projects"));
  const list = h("div", {class: "m-group"});
  for (const p of data.projects) {
    const build = (data.builds || {})[p.id];
    const current = S.project && S.project.id === p.id;
    const row = h("div", {class: "m-row tap"}, h("div", {class: "m-proj", style: "flex:1;min-width:0"},
      h("div", {class: "plat " + p.platform}, platformShort(p.platform)),
      h("div", {class: "m-main"}, h("div", {class: "title"}, p.name, current ? h("span", {class: "badge", style: "margin-left:6px"}, "open") : null),
        h("div", {class: "sub"}, `${p.board_name} · ${p.toolchain_name}`),
        h("div", {class: "sub"}, [build ? `${build.state === "succeeded" ? "✓ built" : "✗ " + build.state} ${fmtTime(build.time)}` : "not built", p.updated ? "edited " + fmtTime(p.updated) : ""].filter(Boolean).join(" · ")))),
      h("button", {class: "more", "aria-label": "Actions for " + p.name, onclick: (e) => { e.stopPropagation(); mProjectMenu(p); }}, "⋯"));
    row.addEventListener("click", async () => { await openProject(p.id); mRefresh(true); });
    list.appendChild(row);
  }
  if (!data.projects.length) list.appendChild(h("div", {class: "m-empty"}, mProjectQuery ? "No project matches." : "No projects yet."));
  page.appendChild(list);
  page.appendChild(h("div", {class: "m-section"}, "Add"));
  const add = h("div", {class: "m-group"});
  add.appendChild(mRow({title: "New project", icon: {text: "＋"}, run: () => newProjectWizard()}));
  add.appendChild(mRow({title: "Import a zip file", icon: {text: "⇪", color: "#5e5ce6"}, run: zipImport}));
  add.appendChild(mRow({title: "Clone from Git", icon: {text: "⑂", color: "#ff9f0a"}, run: cloneDialog}));
  if (isAdmin()) add.appendChild(mRow({title: "Open a folder on the device", icon: {text: "▣", color: "#8e8e93"}, run: () => openFolderDialog()}));
  page.appendChild(add);
  page.appendChild(h("p", {class: "m-foot"}, `Projects are kept in ${data.root}. Deleting moves a project to its trash.`));
}
async function withProject(p, fn) {
  if (!S.project || S.project.id !== p.id) { await openProject(p.id); }
  fn();
}
function mProjectMenu(p) {
  showMenu($("#m-actions"), [
    {label: "Open", run: async () => { await openProject(p.id); mRefresh(true); }},
    {label: "Project settings", run: () => withProject(p, projectSettings)},
    {label: "Rename", run: () => withProject(p, renameProject)},
    {label: "Duplicate", run: () => withProject(p, duplicateProject)},
    {label: "Backups", run: () => withProject(p, backupsDialog)},
    {label: "Export as zip", run: () => { location.href = "/api/project/" + p.id + "/export"; }},
    {label: "Delete…", run: () => withProject(p, deleteProject)},
  ]);
}

// ---------------------------------------------------------------- devices
function renderMobileDevices() {
  const page = $("#m-devices");
  page.innerHTML = "";
  const group = h("div", {class: "m-group"});
  group.appendChild(mRow({title: "Serial monitor", sub: S.monitor.connected ? `Connected · ${(S.monitor.port || "").split("/").pop()} · ${S.monitor.baud} baud` : "Not connected", icon: {text: "≋", color: "#30b0c7"}, run: () => mShow("serial")}));
  if (S.project) group.appendChild(mRow({title: "Flash firmware", sub: `${S.project.name} → ${S.project.board_name}`, icon: {text: "↯", color: "#ff9f0a"}, run: () => flashDialog("upload")}));
  page.appendChild(group);
  page.appendChild(h("div", {class: "m-section"}, "Boards"));
  const body = h("div", {class: "m-devices"});
  page.appendChild(body);
  renderHardware(body);
}

// --------------------------------------------------------------- settings
function renderMobileSettings() {
  const page = $("#m-settings");
  page.innerHTML = "";
  const ai = S.ai || {};
  const g1 = h("div", {class: "m-group"});
  g1.appendChild(mRow({title: "Claude Code", value: (ai.auth && ai.auth.label) || ai.state || "", icon: {text: "✦"}, run: claudeDialog}));
  g1.appendChild(mRow({title: "AI mode", value: modeShort(), icon: {text: "◐", color: "#5e5ce6"}, run: modeSheet}));
  g1.appendChild(mRow({title: "Claude model", value: modelChip(), icon: {text: "◆", color: "#ff9f0a"}, run: () => modelPicker(null)}));
  g1.appendChild(mRow({title: "Usage", value: usageShort(), icon: {text: "◔", color: "#30b0c7"}, run: () => usagePanel(null)}));
  page.appendChild(g1);
  page.appendChild(h("div", {class: "m-section"}, "Appearance"));
  const g2 = h("div", {class: "m-group"});
  const theme = S.fields.find((f) => f.key === "ui.theme");
  if (theme) for (const choice of theme.choices) {
    g2.appendChild(mRow({title: theme.choice_labels[choice] || choice, value: S.settings["ui.theme"] === choice ? "✓" : "", run: async () => {
      try { await POST("settings", {values: {"ui.theme": choice}}); await loadSettings(); mRefresh(true); } catch (e) { fail(e); }
    }, chevron: false}));
  }
  page.appendChild(g2);
  page.appendChild(h("div", {class: "m-section"}, "Development tools"));
  const g3 = h("div", {class: "m-group"});
  const tools = (S.state && S.state.tools || []).filter((t) => ["claude", "arduino-cli", "platformio", "esp-idf", "esptool", "make", "git"].includes(t.id));
  for (const t of tools) {
    const canInstall = !t.found && t.installable && isAdmin();
    g3.appendChild(mRow({title: t.name, value: t.found ? (t.version || "installed") : canInstall ? "Install" : "not installed", dot: t.found ? "ok" : "warn", run: canInstall ? () => installTool(t.id) : null}));
  }
  page.appendChild(g3);
  if (!isAdmin()) page.appendChild(h("p", {class: "m-foot"}, "Installing tools needs a browser with full access."));
  page.appendChild(h("div", {class: "m-section"}, "This device"));
  const g4 = h("div", {class: "m-group"});
  g4.appendChild(mRow({title: "All settings", icon: {text: "⚙", color: "#8e8e93"}, run: settingsDialog}));
  g4.appendChild(mRow({title: "Security and paired browsers", icon: {text: "⛨", color: "#34c759"}, run: securityDialog}));
  if (isAdmin()) g4.appendChild(mRow({title: "Terminal", sub: "A shell as the device user", icon: {text: "›_", color: "#1c1c1e"}, run: () => mShow("terminal")}));
  const m = (S.state && S.state.machine) || {};
  g4.appendChild(mRow({title: "Board", value: m.model || m.arch || "", sub: m.memory_mb ? `${m.memory_mb} MB memory · ${m.cpus} CPUs` : ""}));
  page.appendChild(g4);
  page.appendChild(h("div", {class: "m-section"}, "This browser"));
  const g5 = h("div", {class: "m-group"});
  const me = S.state && S.state.session;
  if (me) g5.appendChild(mRow({title: me.label, sub: me.role_label}));
  g5.appendChild(mRow({title: "Sign out this browser", danger: true, run: () => confirmDialog("Sign out this browser?", "It must be paired again from the device to come back.", "Sign out", async () => {
    try { await POST("session/logout"); } catch (e) { /* signed out anyway */ } location.href = "/"; })}));
  page.appendChild(g5);
  page.appendChild(h("p", {class: "m-foot"}, `mCode Studio ${S.state ? S.state.version : ""}`));
}

// --------------------------------------------------------------- security
function isAdmin() { return !!(S.state && S.state.session && S.state.session.role === "admin"); }
function qrSvg(rows) {
  const n = rows.length; const quiet = 2;
  let d = "";
  rows.forEach((row, y) => { for (let x = 0; x < row.length; x++) if (row[x] === "1") d += `M${x + quiet} ${y + quiet}h1v1h-1z`; });
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", `0 0 ${n + 2 * quiet} ${n + 2 * quiet}`); s.setAttribute("shape-rendering", "crispEdges");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", d); path.setAttribute("fill", "#000");
  s.appendChild(path);
  return s;
}
async function securityDialog() {
  const box = h("div", {id: "security-dialog"}, h("h2", {}, "Security"), h("p", {class: "desc"}, "Loading…"));
  openModal(box);
  M.securityOpen = true;
  await renderSecurity();
}
async function renderSecurity(action) {
  const box = $("#security-dialog");
  if (!box) { M.securityOpen = false; return; }
  let data;
  try { data = action ? await POST("security/" + action.path, action.body || {}) : await GET("security"); }
  catch (e) {
    box.innerHTML = "";
    const me = S.state && S.state.session;
    box.append(h("h2", {}, "Security"), h("p", {class: "desc"}, e.message),
      me ? h("div", {class: "m-group"}, mRow({title: me.label, sub: me.role_label})) : null,
      h("div", {class: "buttons"}, h("button", {class: "btn", onclick: closeModal}, "Close")));
    return;
  }
  box.innerHTML = "";
  box.appendChild(h("h2", {}, "Security"));
  box.appendChild(h("p", {class: "desc"}, "Who can use mCode from a browser. Paired browsers can edit projects and build; flashing always asks first. Only browsers with full access get the terminal and device settings."));
  // network
  const net = data.network;
  box.appendChild(h("div", {class: "m-section"}, "Network access"));
  const g1 = h("div", {class: "m-group"});
  const lan = h("input", {type: "checkbox", class: "switch", checked: net.lan, "aria-label": "Allow computers on the network"});
  lan.addEventListener("change", async () => {
    try { await POST("settings", {values: {"web.lan": lan.checked}}); toast(lan.checked ? "Computers on the network may pair now" : "Only this device's browser now", "ok"); setTimeout(() => renderSecurity(), 1200); } catch (e) { lan.checked = !lan.checked; fail(e); }
  });
  g1.appendChild(h("label", {class: "m-row", style: "color:var(--text)"}, h("div", {class: "m-main"}, h("div", {class: "title"}, "Allow computers on the network"), h("div", {class: "sub", style: "white-space:normal"}, net.lan ? net.urls.join("  ") : "Off: only this device's own browser.")), lan));
  box.appendChild(g1);
  box.appendChild(h("p", {class: "m-foot"}, "The IDE uses plain HTTP on the local network. Pair only on a network you trust, or reach mCode through an HTTPS tunnel (Settings › Other names of this device)." + (net.extra_hosts.length ? " Extra names: " + net.extra_hosts.join(", ") : "")));
  // pairing
  box.appendChild(h("div", {class: "m-section"}, "Pair a phone or computer"));
  const pair = h("div", {class: "m-card", style: "text-align:center"});
  const p = data.pairing;
  if (p.open) {
    if (p.qr && p.qr.length) { const q = h("div", {class: "qr"}); q.appendChild(qrSvg(p.qr)); pair.appendChild(q); }
    pair.appendChild(h("div", {class: "pair-code"}, p.code.slice(0, 3) + " " + p.code.slice(3)));
    pair.appendChild(h("div", {class: "sub"}, `Scan with the phone's camera, or open ${net.urls[0] || "mCode's address"} and type the code. ${p.role_label}. Open for ${Math.ceil(p.seconds_left / 60)} more minute(s); each code works once.`));
    if (p.paired && p.paired.length) pair.appendChild(h("div", {class: "ok", style: "margin-top:8px"}, "Paired: " + p.paired.join(", ")));
    pair.appendChild(h("div", {class: "btns"}, h("button", {class: "m-btn", onclick: () => renderSecurity({path: "pair/close"})}, "Close pairing")));
  } else {
    const role = h("select", {}, Object.entries(data.roles).map(([k, v]) => h("option", {value: k, selected: k === net.pair_role}, v)));
    pair.append(h("div", {class: "sub", style: "margin-bottom:10px"}, net.lan ? "Shows a QR code and a code for 5 minutes." : "Switch on network access first."), role,
      h("div", {class: "btns"}, h("button", {class: "m-btn filled", disabled: !net.lan, onclick: () => renderSecurity({path: "pair", body: {role: role.value}})}, "Show pairing code")));
  }
  box.appendChild(pair);
  // sessions
  box.appendChild(h("div", {class: "m-section"}, `Paired browsers (${data.sessions.length})`));
  const g2 = h("div", {class: "m-group sec-list"});
  for (const s of data.sessions) {
    const mine = s.id === data.me;
    const role = h("select", {"aria-label": "Access of " + s.label, disabled: mine}, Object.entries(data.roles).map(([k, v]) => h("option", {value: k, selected: k === s.role}, v.split(" (")[0])));
    role.addEventListener("change", () => renderSecurity({path: "role", body: {id: s.id, role: role.value}}));
    g2.appendChild(h("div", {class: "m-row sec-row"}, h("div", {class: "m-main"},
      h("div", {class: "title"}, s.label, mine ? h("span", {class: "badge", style: "margin-left:6px"}, "this browser") : null),
      h("div", {class: "sub"}, `last used ${fmtTime(s.last_seen)}${s.address ? " from " + s.address : ""} · paired ${fmtTime(s.created)}`)),
      h("div", {class: "controls"}, role, mine ? null : h("button", {class: "btn danger small", onclick: () => confirmRevoke(s)}, "Sign out"))));
  }
  box.appendChild(g2);
  if (data.sessions.length > 1) box.appendChild(h("div", {class: "btns", style: "display:flex;margin-top:10px"}, h("button", {class: "m-btn danger", onclick: () => renderSecurity({path: "revoke-others"})}, "Sign out all other browsers")));
  box.appendChild(h("p", {class: "m-foot"}, `A browser not used for ${net.session_days} days must pair again. On the device: Settings › Desktop IDE › Sign out all computers.`));
  // events
  box.appendChild(h("div", {class: "m-section"}, "Recent security events"));
  const g3 = h("div", {class: "m-group"});
  data.events.slice(0, 15).forEach((ev) => g3.appendChild(mRow({title: EVENT_TEXT[ev.event] || ev.event, sub: [fmtTime(ev.time), ev.browser, ev.address, ev.role].filter(Boolean).join(" · ")})));
  if (!data.events.length) g3.appendChild(mRow({title: "Nothing yet"}));
  box.appendChild(g3);
  box.appendChild(h("div", {class: "buttons"}, h("button", {class: "btn primary", onclick: () => { M.securityOpen = false; closeModal(); }}, "Done")));
}
function confirmRevoke(s) {
  if (confirm(`Sign out ${s.label}? It must pair again to come back.`)) renderSecurity({path: "revoke", body: {id: s.id}});
}
const EVENT_TEXT = {"session-created": "Browser paired or signed in", "session-revoked": "Browser signed out", "sessions-revoked-all": "All browsers signed out",
  "session-role": "Access changed", "session-expired": "Unused browser expired", "sessions-expired": "Unused browsers expired", "pairing-opened": "Pairing opened",
  "pairing-closed": "Pairing closed", "pairing-failed": "Wrong pairing code", "pairing-rate-limited": "Too many pairing tries", "link-refused": "Used or expired link refused"};

// ------------------------------------------------------------- code keys
function renderCodeKeys() {
  const bar = $("#m-keys");
  const keys = [["⇥", () => cm.execCommand("indentMore")], ["{", "{"], ["}", "}"], ["(", "("], [")", ")"], [";", ";"], ["=", "="], ["\"", "\""], ["<", "<"], [">", ">"],
    ["[", "["], ["]", "]"], ["&", "&"], ["|", "|"], ["#", "#"], ["/", "/"], ["_", "_"], ["Undo", () => cm.undo(), "word"], ["Redo", () => cm.redo(), "word"], ["Find", () => cm.execCommand("find"), "word"]];
  for (const [label, act, cls] of keys) {
    const b = h("button", {class: cls || "", type: "button", "aria-label": label});
    b.textContent = label;
    // pointerdown + preventDefault keeps the editor focused (the keyboard stays up)
    b.addEventListener("pointerdown", (e) => e.preventDefault());
    b.addEventListener("click", () => { if (typeof act === "function") act(); else cm.replaceSelection(act); cm.focus(); });
    bar.appendChild(b);
  }
}

// ------------------------------------------------------------------ wiring
function initMobile() {
  $$("#tabbar button").forEach((b) => b.addEventListener("click", () => mShow(b.dataset.tab)));
  $("#m-back").addEventListener("click", () => { if (M.back) mShow(M.back); });
  $("#m-job").addEventListener("click", () => mShow("build"));
  renderCodeKeys();
  trackViewport();
  PHONE.addEventListener ? PHONE.addEventListener("change", applyLayout) : PHONE.addListener(applyLayout);
  TABLET.addEventListener ? TABLET.addEventListener("change", applyLayout) : TABLET.addListener(applyLayout);
  // Text fields on the phone: keep the focused one in view above the keyboard.
  document.addEventListener("focusin", (e) => { if (isPhone() && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) setTimeout(() => e.target.scrollIntoView({block: "nearest"}), 300); });
  applyLayout();
  placeholder();
}
function placeholder() {
  $("#ai-text").placeholder = isPhone() ? "Message Claude" : "Ask Claude to create, explain, fix or improve firmware…";
}
initMobile();
