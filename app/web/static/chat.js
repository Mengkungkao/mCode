/* mCode Studio: the AI Engineer chat, in every layout.
   It follows Claude Code's own chat in VS Code: each answer shows the steps
   Claude took (tools, thinking) as they happen, and the composer carries the
   mode (Manual, Auto, Auto + git, Plan), the model, how full the conversation
   is, and a send button that becomes Stop while Claude works. What Claude may
   do is decided by the server (app/ai/service.py); this file only shows it.
   Loaded before app.js: function declarations and constants only, so app.js's
   helpers ($, h, S, GET, POST …) are used when the functions run. */
"use strict";

const CHAT_ICON = {
  manual: '<path d="M9 6v12M15 6v12"/>',
  auto: '<path d="M4 6l6 6-6 6M12 6l6 6-6 6"/>',
  plan: '<path d="M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  up: '<path d="M12 19V6M6 11.5l6-6 6 6"/>',
  stop: '<rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor" stroke="none"/>',
  down: '<path d="M7 10l5 5 5-5"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  cross: '<path d="M7 7l10 10M17 7L7 17"/>',
};
const MODE_ICON = {ask: "manual", assisted: "auto", advanced: "auto", plan: "plan"};
const MODE_INFO = {
  ask: "Claude works on a copy. You see each change as a diff and approve it; nothing reaches the project before that. No builds.",
  assisted: "Claude edits the project, builds and runs the tests. Tests that run on this device run code Claude wrote. Undo is always there.",
  advanced: "Like Auto, plus static analysis and git commits through mCode.",
  plan: "Claude reads the project and writes a plan. It changes nothing and builds nothing; you carry the plan out afterwards.",
};
const WINDOW_LABEL = {five_hour: "Current session (5 hours)", seven_day: "This week, all models",
  seven_day_opus: "This week, Opus", seven_day_sonnet: "This week, Sonnet", seven_day_overage_included: "This week, with extra usage"};
const AI_PROMPTS = {
  performance: "Find the biggest speed, memory and power problems in this firmware (blocking delays, heap use, ISR work, busy loops) and propose fixes, most important first.",
  security: "Review this firmware for security problems: credentials or keys in the code, buffer overflows, unchecked input from serial, Wi-Fi or Bluetooth, OTA updates without verification, debug interfaces left open. List findings first, most serious first; change code only if I ask.",
};
const PLAN_MAX = 15000;                    // a plan sent back to Claude (requests stop at 20000)

function chatIcon(name) {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("aria-hidden", "true"); s.setAttribute("class", "ci");
  s.innerHTML = CHAT_ICON[name] || "";
  return s;
}

// ------------------------------------------------------------ small facts
function currentMode() { return S.settings["ai.mode"] || "ask"; }
function modeSlot(mode) { return mode === "ask" || mode === "plan" ? "stage" : "project"; }
function modeList() {
  if (S.ai && S.ai.modes) return S.ai.modes;
  const field = S.fields.find((f) => f.key === "ai.mode");
  return field ? field.choices.map((id) => ({id, label: field.choice_labels[id] || id, short: (field.choice_labels[id] || id).split(":")[0]})) : [];
}
function modeShort(mode = currentMode()) { const m = modeList().find((x) => x.id === mode); return m ? m.short : mode; }
/** "claude-opus-5-5" → "Opus 5.5", "sonnet" → "Sonnet", "opus[1m]" → "Opus (1M)". */
function modelName(id) {
  if (!id) return "";
  let s = String(id); let suffix = "";
  if (/\[1m\]$/i.test(s)) { s = s.replace(/\[1m\]$/i, ""); suffix = " (1M)"; }
  const cap = (w) => w.charAt(0).toUpperCase() + w.slice(1);
  const m = /^claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?(?:-\d{6,})?$/i.exec(s);
  if (m) return `${cap(m[1])} ${m[2]}${m[3] ? "." + m[3] : ""}${suffix}`;
  if (/^[a-z]+$/i.test(s)) return cap(s) + suffix;
  return s + suffix;
}
function modelChip() {
  const chosen = S.settings["ai.model"] || "";
  if (chosen) return modelName(chosen);
  const last = S.ai && S.ai.last_model;
  return last ? modelName(last) : "Default model";
}
function fmtTokens(n) {
  n = Number(n) || 0;
  if (n < 1000) return String(n);
  if (n < 1e6) return (n / 1000).toFixed(n < 10000 ? 1 : 0) + "k";
  return (n / 1e6).toFixed(n < 1e7 ? 2 : 1) + "M";
}
function fmtSeconds(s) { s = Math.max(0, Math.round(s || 0)); return s < 60 ? s + " s" : `${Math.floor(s / 60)} min ${s % 60} s`; }
function fmtResets(t) {
  if (!t) return "";
  const d = new Date(t * 1000); const now = new Date();
  const same = d.toDateString() === now.toDateString();
  return "resets " + d.toLocaleString([], same ? {hour: "numeric", minute: "2-digit"} : {weekday: "short", hour: "numeric", minute: "2-digit"});
}
function subscription() { const a = (S.ai && S.ai.auth) || {}; return !a.api_key_env && ["claude.ai", "claudeai", "oauth"].includes(a.method); }
function liveTurn() { const t = S.turn; return t && t.state === "working" && S.project && t.project_id === S.project.id ? t : null; }
/** How full the conversation of the current mode is: {tokens, window} or null. */
function contextInfo() {
  const live = liveTurn();
  if (live && live.slot === modeSlot(currentMode()) && live.context) return {tokens: live.context, window: live.context_window};
  const saved = (S.context || {})[modeSlot(currentMode())];
  return saved && saved.tokens ? {tokens: saved.tokens, window: saved.window} : null;
}
function aiJobs() {
  return Array.from(S.jobs.values()).filter((j) => (j.state === "running" || j.state === "queued") && j.meta && j.meta.source === "ai" && (!S.project || j.project_id === S.project.id));
}

// ------------------------------------------------------------- the chat
function aiStatusBlock() {
  const ai = S.ai || {};
  if (ai.state === "not-installed") return h("div", {class: "ai-setup"}, h("h3", {}, "Claude Code is not installed"), h("p", {}, "mCode uses your own Claude Code with your Claude account."), h("button", {class: "btn primary", onclick: () => installTool("claude")}, "Install Claude Code"));
  if (ai.state === "signed-out") return h("div", {class: "ai-setup"}, h("h3", {}, "Connect your Claude account"), h("p", {}, "Sign in with Claude Code's official browser login. mCode never sees your password or tokens."), h("button", {class: "btn primary", onclick: claudeDialog}, "Connect"));
  if (!S.project) return h("div", {class: "ai-setup"}, h("h3", {}, "No project open"), h("p", {}, "Claude works inside a project."), h("button", {class: "btn primary", onclick: () => newProjectWizard()}, "New project"));
  return null;
}
async function loadConversation() {
  if (!S.project || !S.ai || !S.ai.installed) { renderAI(); return; }
  try {
    const data = await GET(`ai/${S.project.id}/conversation`);
    S.conversation = data.conversation; S.review = data.review; S.context = data.context || {};
    S.turn = data.turn && data.turn.project_id === S.project.id ? data.turn : null;
  } catch (e) { S.conversation = []; S.context = {}; }
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
  renderComposer();
  const body = $("#ai-body"); body.innerHTML = "";
  const setup = aiStatusBlock();
  $("#ai-quick").classList.toggle("hidden", !!setup || !!liveTurn());
  if (setup) { body.appendChild(setup); $("#ai-review").classList.add("hidden"); return; }
  if (S.ai.memory_warning) body.appendChild(h("div", {class: "msg system warn"}, S.ai.memory_warning));
  if (S.ai.auth && S.ai.auth.note) body.appendChild(h("div", {class: "msg system warn"}, S.ai.auth.note));
  if (!S.conversation.length) body.appendChild(h("div", {class: "msg system"}, "Ask Claude to create firmware, explain code, fix the build, write tests or document the wiring. It works on this project's files. ", h("b", {}, modeShort()), " mode: " + MODE_INFO[currentMode()]));
  const working = !!liveTurn();
  S.conversation.forEach((m, i) => body.appendChild(m.role === "user" ? userMessage(m) : assistantMessage(m, i, working)));
  if (working) body.appendChild(liveMessage(S.turn));
  body.scrollTop = body.scrollHeight;
  renderReview();
}
function userMessage(m) {
  return h("div", {class: "msg user"}, h("div", {class: "text"}, m.text),
    h("div", {class: "meta"}, [m.mode ? modeShort(m.mode) : "", m.source && m.source !== "web" ? m.source : "", fmtTime(m.time)].filter(Boolean).join(" · ")));
}
function stepRow(step, live) {
  if (step.type === "thinking") {
    return h("details", {class: "step thinking"}, h("summary", {}, h("span", {class: "sdot"}), live ? "Thinking" : "Thought"), h("div", {class: "think"}, step.text || ""));
  }
  const state = step.state || (step.error ? "error" : "done");
  const icon = state === "running" ? h("span", {class: "spin"}) : h("span", {class: "sdot " + (state === "error" ? "err" : "ok")});
  const took = state === "running" && step.time ? h("span", {class: "took", "data-since": step.time}, fmtSeconds(Date.now() / 1000 - step.time)) :
    step.ended && step.time && step.ended - step.time >= 1 ? h("span", {class: "took"}, fmtSeconds(step.ended - step.time)) : null;
  const row = h("div", {class: "step tool " + state}, icon, h("span", {class: "label"}, step.label), took);
  const extra = [];
  if (state === "running" && /^mcp__mcode__(build|run_tests|static_check|clean)$/.test(step.name || "")) {
    for (const job of aiJobs()) {
      const pct = job.progress != null ? ` ${Math.round(job.progress * 100)}%` : "";
      extra.push(h("div", {class: "job"}, h("span", {class: "bar"}, h("i", {style: `width:${job.progress != null ? Math.round(job.progress * 100) : 25}%`})), `${job.phase || job.title}${pct}`));
    }
  }
  if (step.detail) extra.push(h("div", {class: "detail"}, step.detail));
  return extra.length ? h("div", {class: "step-wrap"}, row, extra) : row;
}
function stepsBlock(steps, open, total) {
  if (!steps.length) return null;
  const tools = steps.filter((s) => s.type === "tool");
  const failed = tools.filter((s) => s.state === "error").length;
  const label = `${tools.length} step${tools.length === 1 ? "" : "s"}${failed ? ` · ${failed} failed` : ""}${total ? " · " + total : ""}`;
  return h("details", {class: "steps", open: open}, h("summary", {}, label), h("div", {class: "list"}, steps.map((s) => stepRow(s, false))));
}
function assistantMessage(m, i, working) {
  const steps = m.steps || (m.activities || []).map((label) => ({type: "tool", label, state: "done"}));
  const last = i === S.conversation.length - 1;
  const asked = S.conversation.slice(0, i).reverse().find((x) => x.role === "user");
  const retry = last && !working && asked && ["error", "cancelled"].includes(m.state) ?
    h("button", {class: "btn small retry", onclick: () => aiAsk(asked.kind && asked.kind !== "chat" ? "" : asked.text, asked.kind || "chat")}, "↻ Try again") : null;
  const plan = m.mode === "plan" && m.state === "done" && last && !working && m.text ? h("div", {class: "plan-actions"},
    h("span", {class: "muted"}, "Carry out this plan:"),
    h("button", {class: "btn small", onclick: () => carryOut(m.text, "ask")}, "Manual"),
    h("button", {class: "btn small primary", onclick: () => carryOut(m.text, "assisted")}, "Auto")) : null;
  const u = m.usage || {};
  const meta = [m.state && !["done", "review"].includes(m.state) ? m.state : "", modelName(m.model), m.duration ? fmtSeconds(m.duration) : "",
    u.output ? `${fmtTokens(u.output)} tokens out` : "", m.changes && m.changes.length ? `${m.changes.length} file(s)` : "",
    !subscription() && m.cost_usd ? `$${Number(m.cost_usd).toFixed(4)}` : "", fmtTime(m.time)].filter(Boolean).join(" · ");
  return h("div", {class: "msg assistant" + (m.state === "error" ? " failed" : "")}, stepsBlock(steps, false),
    h("div", {class: "answer", html: md(m.text)}), m.summary && ["done", "review"].includes(m.state) ? h("div", {class: "summary"}, m.summary) : null,
    (m.notices || []).map((n) => h("div", {class: "notice"}, "⚠ " + n)), plan, retry, h("div", {class: "meta"}, meta));
}
function liveMessage(t) {
  const box = h("div", {class: "msg assistant live", id: "ai-live"});
  const steps = t.steps || [];
  // Earlier steps fold away; the last few stay open like Claude Code's own view.
  const keep = 8;
  const earlier = steps.slice(0, Math.max(0, steps.length - keep)).filter((s) => s.type !== "text");
  if (earlier.length) box.appendChild(stepsBlock(earlier, false));
  for (const step of steps.slice(-keep)) box.appendChild(step.type === "text" ? h("div", {class: "answer", html: md(step.text)}) : stepRow(step, true));
  box.appendChild(h("div", {class: "status", id: "ai-live-status"}, h("span", {class: "spin"}), h("span", {class: "what"}, liveStatusText(t))));
  return box;
}
function liveStatusText(t) {
  const elapsed = (t.started ? Date.now() / 1000 - t.started : t.elapsed) || 0;
  const out = t.usage && t.usage.output ? ` · ↓ ${fmtTokens(t.usage.output)} tokens` : "";
  return `${t.phase || "Working"}… ${fmtSeconds(elapsed)}${out}`;
}
function renderLive() {
  const body = $("#ai-body"); const t = liveTurn(); const old = $("#ai-live");
  if (!t) { if (old) loadConversation(); return; }
  const stick = body.scrollHeight - body.scrollTop - body.clientHeight < 90;
  const fresh = liveMessage(t);
  if (old) old.replaceWith(fresh); else body.appendChild(fresh);
  if (stick) body.scrollTop = body.scrollHeight;
  renderComposer();
}
/** Every second: the running clock of the live answer and its tools. */
function tickChat() {
  const t = liveTurn(); const el = $("#ai-live-status .what");
  if (t && el) el.textContent = liveStatusText(t);
  $$("#ai-live .took[data-since]").forEach((e) => { e.textContent = fmtSeconds(Date.now() / 1000 - Number(e.dataset.since)); });
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
  if (data.what === "progress") renderLive();
  if (data.what === "usage") { renderComposer(); if ($("#usage-panel")) renderUsage(); }
  if (["turn", "review", "undo", "conversation"].includes(data.what)) {
    if (data.what === "turn" && data.turn && data.turn.state !== "working") {
      const t = data.turn; toast(t.state === "done" ? "Claude: " + (t.summary || "done") : t.state === "review" ? "Claude has changes for you to review" : "Claude: " + (t.error || t.state), t.state === "error" ? "err" : t.state === "done" ? "ok" : "warn", 7000);
      reloadOpenFiles(); if (S.project) loadProject(S.project.id);
    }
    if (data.what === "undo" || data.what === "review") reloadOpenFiles();
    loadConversation();
  }
}

// ------------------------------------------------------------ composer
function renderComposer() {
  const mode = currentMode();
  const btn = $("#ai-mode-btn");
  if (btn) {
    btn.innerHTML = ""; btn.className = "cbtn mode m-" + mode;
    btn.append(chatIcon(MODE_ICON[mode] || "manual"), h("span", {}, modeShort(mode)));
    btn.title = `Mode: ${modeShort(mode)}. ${MODE_INFO[mode] || ""} (Shift+Tab switches)`;
  }
  const model = $("#ai-model-btn");
  if (model) { model.innerHTML = ""; model.append(h("span", {}, modelChip()), chatIcon("down")); model.title = "Model: " + modelChip(); }
  const ring = $("#ai-context");
  if (ring) {
    const info = contextInfo();
    const pct = info && info.window ? Math.min(100, Math.round(info.tokens / info.window * 100)) : null;
    ring.innerHTML = "";
    const r = 7; const c = 2 * Math.PI * r;
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 20 20"); svg.setAttribute("aria-hidden", "true");
    svg.innerHTML = `<circle cx="10" cy="10" r="${r}" class="track"/><circle cx="10" cy="10" r="${r}" class="fill" stroke-dasharray="${c}" stroke-dashoffset="${c * (1 - (pct || 0) / 100)}"/>`;
    ring.append(svg, h("span", {}, pct != null ? pct + "%" : info ? fmtTokens(info.tokens) : "Usage"));
    ring.classList.toggle("high", pct != null && pct >= 80);
    ring.title = info ? `${fmtTokens(info.tokens)} tokens in this conversation${info.window ? ` of ${fmtTokens(info.window)} (${pct}%)` : ""}. Click for your plan's usage.` : "Usage: this conversation and your Claude plan";
  }
  const working = !!liveTurn();
  $("#ai-send").classList.toggle("hidden", working);
  $("#ai-stop").classList.toggle("hidden", !working);
  $("#ai-quick").classList.toggle("hidden", working || !!aiStatusBlock());
  renderAttachments();
}
async function setAiMode(mode) {
  try {
    await POST("settings", {values: {"ai.mode": mode}}); S.settings["ai.mode"] = mode;
    if (S.ai) { S.ai.mode = mode; S.ai.mode_short = modeShort(mode); }
    renderComposer(); toast("Mode: " + modeShort(mode), "ok", 2500);
    if (isPhone()) renderHeader();
  } catch (e) { fail(e); }
}
function cycleMode() {
  const ids = modeList().map((m) => m.id);
  if (ids.length) setAiMode(ids[(ids.indexOf(currentMode()) + 1) % ids.length]);
}
async function setModel(id) {
  try {
    await POST("settings", {values: {"ai.model": id}}); S.settings["ai.model"] = id; if (S.ai) S.ai.model = id;
    renderComposer(); toast("Model: " + (id ? modelName(id) : "Claude Code's default") + " (from the next request)", "ok", 3000);
  } catch (e) { fail(e); }
}
async function aiSend() {
  const text = $("#ai-text").value.trim();
  if (!text && !S.attachments.length) return;
  await aiAsk(text);
}
async function aiAsk(text, kind = "chat", mode) {
  if (!S.project) { toast("Open a project first.", "warn"); return; }
  for (const path of S.files.keys()) if (S.files.get(path).dirty) await saveFile(path, true);
  try {
    const turn = await POST(`ai/${S.project.id}/ask`, {text, kind, mode, attachments: S.attachments.filter((a) => a.type === "file").map((a) => a.path), include_serial: S.attachments.some((a) => a.type === "serial")});
    S.turn = turn; $("#ai-text").value = ""; $("#ai-text").style.height = ""; S.attachments = []; renderAttachments(); loadConversation();
  } catch (e) { fail(e); }
}
async function carryOut(plan, mode) {
  const text = plan.length > PLAN_MAX ? plan.slice(0, PLAN_MAX) + "\n…" : plan;
  if (currentMode() !== mode) await setAiMode(mode);
  aiAsk("Carry out this plan:\n\n" + text, "chat", mode);
}
function aiKind(kind) {
  const texts = {"fix-build": "Fix the build errors.", "analyze-serial": "What is happening on the board?", review: "Review this project.", wiring: "Write the wiring document."};
  const typed = $("#ai-text").value.trim();
  if (kind === "create") {                     // the user says what to build
    const box = $("#ai-text");
    if (!typed) { box.value = "Create firmware that "; box.focus(); box.setSelectionRange(box.value.length, box.value.length); return; }
    aiAsk(typed); return;
  }
  if (kind === "explain") {
    if (S.active) attach({type: "file", path: S.active});
    aiAsk(typed || (S.active ? `Explain what ${S.active} does, step by step, for someone new to embedded code.` : "Explain how this project's code works, file by file, for someone new to embedded code."));
    return;
  }
  if (AI_PROMPTS[kind]) { aiAsk(typed ? typed + "\n\n" + AI_PROMPTS[kind] : AI_PROMPTS[kind]); return; }
  aiAsk(typed || texts[kind], kind);
}
function attach(a) { if (!S.attachments.some((x) => x.type === a.type && x.path === a.path)) S.attachments.push(a); renderAttachments(); }
function renderAttachments() {
  const box = $("#ai-attachments"); box.innerHTML = "";
  S.attachments.forEach((a, i) => box.appendChild(h("span", {class: "chip"}, a.type === "serial" ? "serial output" : a.path.split("/").pop(), h("button", {"aria-label": "Remove", onclick: () => { S.attachments.splice(i, 1); renderAttachments(); }}, "×"))));
  box.classList.toggle("hidden", !S.attachments.length);
}

// --------------------------------------------------------------- pickers
/** A list of choices: a popover above the button on a computer, a sheet on a phone. */
function chatPicker(anchor, title, rows, foot) {
  closePicker();
  const list = h("div", {class: "pick-list"}, rows.map((r) => {
    const row = h("button", {type: "button", class: "pick-row" + (r.checked ? " on" : ""), disabled: r.disabled},
      r.icon ? chatIcon(r.icon) : null, h("span", {class: "pick-main"}, h("span", {class: "t"}, r.title), r.sub ? h("span", {class: "s"}, r.sub) : null),
      r.checked ? chatIcon("check") : null);
    row.addEventListener("click", () => { closePicker(); if ($("#modal").contains(row)) closeModal(); r.run(); });
    return row;
  }));
  showPicker(anchor, h("div", {}, h("h2", {}, title), list, foot ? h("p", {class: "pick-foot"}, foot) : null));
}
function showPicker(anchor, content) {
  closePicker();
  if (isPhone() || !anchor) { content.appendChild(h("div", {class: "buttons"}, h("button", {class: "btn", onclick: closeModal}, "Close"))); openModal(content); return; }
  const pop = h("div", {class: "chat-pop", id: "chat-pop", role: "dialog"}, content);
  document.body.appendChild(pop);
  const r = anchor.getBoundingClientRect();
  pop.style.left = Math.max(8, Math.min(r.left, innerWidth - pop.offsetWidth - 8)) + "px";
  pop.style.bottom = Math.max(8, innerHeight - r.top + 6) + "px";
  setTimeout(() => document.addEventListener("pointerdown", outsidePicker), 0);
}
function outsidePicker(e) { const pop = $("#chat-pop"); if (pop && !pop.contains(e.target)) closePicker(); }
function closePicker() { const pop = $("#chat-pop"); if (pop) pop.remove(); document.removeEventListener("pointerdown", outsidePicker); }
function modePicker(anchor) {
  chatPicker(anchor, "Mode", modeList().map((m) => ({icon: MODE_ICON[m.id], title: m.short, sub: MODE_INFO[m.id] || m.label, checked: m.id === currentMode(), run: () => setAiMode(m.id)})),
    (coarse() ? "" : "Shift+Tab in the message box switches modes. ") + "In every mode, flashing, erasing and installing need your own confirmation; Claude has no tool for them.");
}
function modelPicker(anchor) {
  const chosen = S.settings["ai.model"] || "";
  const last = S.ai && S.ai.last_model;
  const models = (S.ai && S.ai.models) || [{id: "", label: "Default"}];
  const rows = models.map((m) => ({title: m.id ? m.label : "Default", sub: m.id ? "" : "Claude Code's choice" + (last ? ` · last used ${modelName(last)}` : ""), checked: chosen === m.id, run: () => setModel(m.id)}));
  if (chosen && !models.some((m) => m.id === chosen)) rows.push({title: modelName(chosen), sub: chosen, checked: true, run: () => {}});
  rows.push({title: "Other model…", sub: "A model id, e.g. claude-sonnet-5-5", run: () => { const v = prompt("Model id or alias for Claude Code:", chosen); if (v !== null) setModel(v.trim()); }});
  chatPicker(anchor, "Model", rows, "Your Claude plan decides which models you can use; Claude Code says so when one is not available. The change applies from the next request.");
}
function attachPicker(anchor) {
  const serial = S.monitor && S.monitor.lines && S.monitor.lines.length;
  chatPicker(anchor, "Attach", [
    {title: "Open file", sub: S.active || "No file is open", disabled: !S.active, run: () => attach({type: "file", path: S.active})},
    {title: "Serial monitor output", sub: serial ? "The latest lines, with times" : "No serial output yet", disabled: !serial, run: () => attach({type: "serial", path: "serial"})},
  ]);
}

// ----------------------------------------------------------------- usage
function usagePanel(anchor) {
  showPicker(anchor, h("div", {}, h("div", {id: "usage-panel"})));   // the sheet adds Close after it
  renderUsage();
}
function usageShort() {
  const w = ((S.ai && S.ai.limits) || {}).windows || {};
  const pct = (x) => (x.resets_at && x.resets_at * 1000 <= Date.now() ? 0 : Math.round(x.used * 100)) + "%";
  return [w.five_hour ? pct(w.five_hour) : "", w.seven_day ? "week " + pct(w.seven_day) : ""].filter(Boolean).join(" · ");
}
function usageBar(pct) { return h("div", {class: "ubar" + (pct >= 80 ? " high" : "")}, h("i", {style: `width:${Math.min(100, Math.max(0, pct))}%`})); }
function renderUsage() {
  const box = $("#usage-panel"); if (!box) return;
  box.innerHTML = "";
  box.appendChild(h("h2", {}, "Usage"));
  // the plan
  const limits = (S.ai && S.ai.limits) || {};
  const windows = Object.entries(limits.windows || {});
  box.appendChild(h("div", {class: "u-section"}, subscription() ? "Your Claude plan" : "Plan limits"));
  if (windows.length) {
    for (const [name, w] of windows) {
      const passed = w.resets_at && w.resets_at * 1000 <= Date.now();
      const pct = passed ? 0 : Math.round(w.used * 1000) / 10;
      box.appendChild(h("div", {class: "u-row"}, h("div", {class: "u-line"}, h("span", {}, WINDOW_LABEL[name] || name.replace(/_/g, " ")), h("b", {}, `${pct}% used`)),
        usageBar(pct), w.resets_at && !passed ? h("div", {class: "u-sub"}, fmtResets(w.resets_at)) : null));
    }
    if (limits.status === "rejected") box.appendChild(h("p", {class: "err"}, `Limit reached. ${fmtResets(limits.resets_at)}.`));
    box.appendChild(h("p", {class: "u-note"}, `As Claude Code reported it ${limits.time ? fmtTime(limits.time) : ""}; it reports with each request.`));
  } else {
    box.appendChild(h("p", {class: "u-note"}, subscription() ? "Claude Code reports your plan's limits with each request; there is no report yet." : "With an API key there are no plan limits: each request is billed by tokens."));
  }
  // this conversation
  const answers = S.conversation.filter((m) => m.role === "assistant");
  const sum = (key) => answers.reduce((n, m) => n + ((m.usage || {})[key] || 0), 0);
  const cost = answers.reduce((n, m) => n + (Number(m.cost_usd) || 0), 0);
  const info = contextInfo();
  box.appendChild(h("div", {class: "u-section"}, `This conversation${S.project ? " · " + S.project.name : ""} (${modeShort()})`));
  if (info) {
    const pct = info.window ? Math.round(info.tokens / info.window * 1000) / 10 : null;
    box.appendChild(h("div", {class: "u-row"}, h("div", {class: "u-line"}, h("span", {}, "Context"), h("b", {}, `${fmtTokens(info.tokens)}${info.window ? " of " + fmtTokens(info.window) : ""} tokens`)),
      pct != null ? usageBar(pct) : null, h("div", {class: "u-sub"}, "What Claude reads with each request. ＋ (New conversation) starts empty.")));
  }
  const kv = h("div", {class: "kv"});
  kv.append(h("span", {class: "k"}, "Requests"), h("span", {}, String(answers.length)),
    h("span", {class: "k"}, "Tokens"), h("span", {}, `${fmtTokens(sum("output"))} written · ${fmtTokens(sum("input") + sum("cache_read") + sum("cache_write"))} read`),
    h("span", {class: "k"}, "Cost"), h("span", {}, cost ? `$${cost.toFixed(4)}` + (subscription() ? " at API prices; your plan's limits apply instead" : " billed to the API key") : "—"),
    h("span", {class: "k"}, "Model"), h("span", {}, (S.settings["ai.model"] ? modelName(S.settings["ai.model"]) : "Default") + (S.ai && S.ai.last_model ? ` · last used ${modelName(S.ai.last_model)}` : "")));
  box.appendChild(kv);
}

// ---------------------------------------------------------------- wiring
function bindChat() {
  $("#ai-send").addEventListener("click", aiSend);
  $("#ai-stop").addEventListener("click", () => POST("ai/cancel").catch(fail));
  $("#ai-text").addEventListener("keydown", (e) => {
    if (e.isComposing) return;
    if (e.key === "Tab" && e.shiftKey) { e.preventDefault(); cycleMode(); return; }
    // Like Claude Code: Enter sends and Shift+Enter starts a new line on a computer;
    // on a touch screen Enter is a new line and the send button sends.
    if (e.key === "Enter" && ((e.ctrlKey || e.metaKey) || (!e.shiftKey && !coarse()))) { e.preventDefault(); aiSend(); }
  });
  // The message box grows with its text, up to a third of the window.
  const text = $("#ai-text");
  text.addEventListener("input", () => { text.style.height = "auto"; text.style.height = Math.min(text.scrollHeight + 2, window.innerHeight * 0.32) + "px"; });
  $("#ai-attach").addEventListener("click", (e) => { e.stopPropagation(); attachPicker(e.currentTarget); });
  $("#ai-mode-btn").addEventListener("click", (e) => { e.stopPropagation(); modePicker(e.currentTarget); });
  $("#ai-model-btn").addEventListener("click", (e) => { e.stopPropagation(); modelPicker(e.currentTarget); });
  $("#ai-context").addEventListener("click", (e) => { e.stopPropagation(); usagePanel(e.currentTarget); });
  $$("#ai-quick button").forEach((b) => b.addEventListener("click", () => aiKind(b.dataset.kind)));
  $("#ai-new").addEventListener("click", newConversation);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePicker(); });
}
function newConversation() {
  if (S.project) confirmDialog("New conversation?", "Claude forgets this project's conversation (the files stay).", "Start over", () => POST(`ai/${S.project.id}/clear`).then(loadConversation).catch(fail));
}
