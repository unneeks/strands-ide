"use strict";
// Strands IDE frontend: Monaco editor + file tree + agent chat + run panel.

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const api = {
  tree: () => fetch("/api/tree").then((r) => r.json()),
  file: (path) => fetch("/api/file?path=" + encodeURIComponent(path)).then((r) => r.json()),
  writeFile: (path, content) =>
    fetch("/api/file", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, content }),
    }).then((r) => r.json()),
  deleteFile: (path) =>
    fetch("/api/file?path=" + encodeURIComponent(path), { method: "DELETE" }).then((r) => r.json()),
  chat: (prompt) =>
    fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt }),
    }).then(async (r) => {
      const body = await r.json();
      if (!r.ok) throw new Error(body.detail || ("HTTP " + r.status));
      return body;
    }),
  run: (command, timeout = 120) =>
    fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command, timeout }),
    }).then((r) => r.json()),
  agentHealth: () => fetch("/api/health/agent").then((r) => r.json()),
};

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
const state = {
  openTabs: new Map(), // path -> { model, viewState, dirty }
  activePath: null,
  tree: [], // flat entries from /api/tree
  editor: null,
  monaco: null,
};

const LANG_BY_EXT = {
  py: "python", js: "javascript", ts: "typescript", json: "json",
  md: "markdown", html: "html", css: "css", yml: "yaml", yaml: "yaml",
  toml: "toml", sh: "shell", txt: "plaintext", cfg: "ini", ini: "ini",
  sql: "sql", dockerfile: "dockerfile",
};

function langForPath(path) {
  const base = path.split("/").pop();
  if (base === "Dockerfile") return "dockerfile";
  const ext = base.split(".").pop().toLowerCase();
  return LANG_BY_EXT[ext] || "plaintext";
}

function basename(p) { return p.split("/").pop(); }

// ---------------------------------------------------------------------------
// Monaco
// ---------------------------------------------------------------------------
require.config({ paths: { vs: "https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs" } });
require(["vs/editor/editor.main"], function () {
  state.monaco = monaco;
  state.editor = monaco.editor.create($("#editor"), {
    value: "",
    language: "plaintext",
    theme: "vs-dark",
    automaticLayout: true,
    fontSize: 13,
    minimap: { enabled: false },
    scrollBeyondLastLine: false,
    tabSize: 4,
  });
  // Ctrl/Cmd+S saves the active file.
  state.editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, saveActive);
  init();
});

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
async function init() {
  await loadWorkspaceInfo();
  await refreshTree();
  await maybeOpenInitial();
  pollAgentHealth();
  setInterval(refreshTree, 4000); // pick up agent/external file changes
  wireEvents();
}

async function loadWorkspaceInfo() {
  try {
    const info = await api.tree(); // includes root
    $("#workspaceRoot").textContent = info.root || "";
  } catch (e) { /* ignore */ }
}

// ---------------------------------------------------------------------------
// File tree
// ---------------------------------------------------------------------------
function buildTreeNodes(flat) {
  // flat: [{path, type, size}] relative to workspace root. Build nested children.
  const root = { name: "", path: "", children: {}, isDir: true };
  for (const e of flat) {
    const parts = e.path.split("/");
    let node = root;
    let acc = "";
    for (let i = 0; i < parts.length; i++) {
      const part = parts[i];
      acc = acc ? acc + "/" + part : part;
      const isLast = i === parts.length - 1;
      if (!node.children[part]) {
        node.children[part] = {
          name: part,
          path: acc,
          children: {},
          isDir: isLast ? e.type === "dir" : true,
        };
      }
      node = node.children[part];
      if (isLast) node.isDir = e.type === "dir";
    }
  }
  return root;
}

function renderTreeClean(rootNode) {
  const el = $("#tree");
  el.innerHTML = "";
  const sortedChildren = (node) =>
    Object.values(node.children).sort((a, b) => {
      if (a.isDir !== b.isDir) return a.isDir ? -1 : 1;
      return a.name.localeCompare(b.name);
    });

  function draw(node, depth, container) {
    for (const child of sortedChildren(node)) {
      const row = document.createElement("div");
      row.className = "row" + (child.isDir ? " dir" : " file");
      row.style.paddingLeft = 8 + depth * 14 + "px";
      row.dataset.path = child.path;
      const ic = document.createElement("span");
      ic.className = "ic";
      ic.textContent = child.isDir ? "▸" : "•";
      const label = document.createElement("span");
      label.textContent = child.name;
      row.appendChild(ic);
      row.appendChild(label);
      if (state.activePath === child.path) row.classList.add("active");

      if (child.isDir) {
        const sub = document.createElement("div");
        sub.className = "sub";
        sub.classList.add("hidden");
        let open = false;
        row.addEventListener("click", () => {
          open = !open;
          ic.textContent = open ? "▾" : "▸";
          sub.classList.toggle("hidden", !open);
        });
        container.appendChild(row);
        container.appendChild(sub);
        draw(child, depth + 1, sub);
      } else {
        row.addEventListener("click", () => openFile(child.path));
        container.appendChild(row);
      }
    }
  }
  draw(rootNode, 0, el);
  // auto-expand first level for convenience
  el.querySelectorAll(":scope > .row.dir + .sub").forEach((s) => s.classList.remove("hidden"));
  el.querySelectorAll(":scope > .row.dir .ic").forEach((ic) => (ic.textContent = "▾"));
}

async function refreshTree() {
  try {
    const data = await api.tree();
    state.tree = data.entries || [];
    renderTreeClean(buildTreeNodes(state.tree));
  } catch (e) {
    console.error("tree error", e);
  }
}

async function maybeOpenInitial() {
  const preferred = ["app.py", "main.py", "README.md"];
  for (const p of preferred) {
    if (state.tree.some((e) => e.path === p && e.type === "file")) {
      await openFile(p);
      return;
    }
  }
  const firstFile = state.tree.find((e) => e.type === "file");
  if (firstFile) await openFile(firstFile.path);
}

// ---------------------------------------------------------------------------
// Tabs / editor
// ---------------------------------------------------------------------------
async function openFile(path) {
  if (state.activePath && state.activePath !== path) {
    const t = state.openTabs.get(state.activePath);
    if (t) t.viewState = state.editor.saveViewState();
  }
  if (!state.openTabs.has(path)) {
    let content = "";
    try {
      const f = await api.file(path);
      content = f.content ?? "";
    } catch (e) {
      addChatMessage("agent", "Could not open " + path + ": " + e.message, { error: true });
      return;
    }
    const model = state.monaco.editor.createModel(content, langForPath(path));
    model.onDidChangeContent(() => {
      const tab = state.openTabs.get(path);
      if (tab) tab.dirty = true;
      renderTabs();
    });
    state.openTabs.set(path, { model, viewState: null, dirty: false });
  }
  const tab = state.openTabs.get(path);
  state.editor.setModel(tab.model);
  if (tab.viewState) state.editor.restoreViewState(tab.viewState);
  state.activePath = path;
  renderTabs();
  highlightTree(path);
}

function renderTabs() {
  const el = $("#tabs");
  el.innerHTML = "";
  for (const [path, tab] of state.openTabs) {
    const t = document.createElement("div");
    t.className = "tab" + (path === state.activePath ? " active" : "") + (tab.dirty ? " dirty" : "");
    const name = document.createElement("span");
    name.textContent = basename(path);
    name.title = path;
    const x = document.createElement("span");
    x.className = "x";
    x.textContent = "×";
    x.addEventListener("click", (ev) => { ev.stopPropagation(); closeTab(path); });
    t.appendChild(name);
    t.appendChild(x);
    t.addEventListener("click", () => openFile(path));
    el.appendChild(t);
  }
}

async function closeTab(path) {
  const tab = state.openTabs.get(path);
  if (!tab) return;
  if (tab.dirty && !confirm("Close " + path + " with unsaved changes?")) return;
  tab.model.dispose();
  state.openTabs.delete(path);
  if (state.activePath === path) {
    state.activePath = null;
    const next = state.openTabs.keys().next();
    if (!next.done) await openFile(next.value);
    else state.editor.setModel(state.monaco.editor.createModel("", "plaintext"));
  }
  renderTabs();
}

async function saveActive() {
  if (!state.activePath) return;
  const tab = state.openTabs.get(state.activePath);
  if (!tab) return;
  try {
    await api.writeFile(state.activePath, tab.model.getValue());
    tab.dirty = false;
    renderTabs();
    flashStatus();
  } catch (e) {
    addChatMessage("agent", "Save failed: " + e.message, { error: true });
  }
}

function flashStatus() {
  const b = $("#agentStatus");
  const old = b.textContent;
  b.textContent = "saved ✓";
  setTimeout(() => (b.textContent = old), 1200);
}

function highlightTree(path) {
  $$("#tree .row").forEach((r) => r.classList.toggle("active", r.dataset.path === path));
}

// Reload an open tab from disk (e.g. after the agent edited it).
async function reloadOpenFile(path) {
  const tab = state.openTabs.get(path);
  if (!tab) return;
  try {
    const f = await api.file(path);
    const pos = state.editor.getPosition();
    tab.model.setValue(f.content ?? "");
    tab.dirty = false;
    if (state.activePath === path && pos) state.editor.setPosition(pos);
    renderTabs();
  } catch (e) { /* file may have been deleted */ }
}

// ---------------------------------------------------------------------------
// Chat
// ---------------------------------------------------------------------------
function addChatMessage(role, text, opts = {}) {
  const el = $("#messages");
  const msg = document.createElement("div");
  msg.className = "msg " + role + (opts.error ? " err" : "");
  const r = document.createElement("div");
  r.className = "role";
  r.textContent = role === "user" ? "you" : role === "agent" ? "agent" : role;
  const body = document.createElement("div");
  body.className = "body";
  if (opts.error) body.classList.add("error");
  body.textContent = text;
  msg.appendChild(r);
  msg.appendChild(body);
  if (opts.toolCalls && opts.toolCalls.length) {
    const tc = document.createElement("div");
    tc.className = "toolcalls";
    tc.appendChild(document.createTextNode(opts.toolCalls.length + " tool call(s):"));
    for (const call of opts.toolCalls) {
      const line = document.createElement("div");
      line.className = "tc";
      const nm = document.createElement("span");
      nm.className = "nm";
      nm.textContent = call.name;
      line.appendChild(nm);
      line.appendChild(document.createTextNode(" " + safeJSON(call.input)));
      tc.appendChild(line);
    }
    msg.appendChild(tc);
  }
  if (opts.filesChanged && opts.filesChanged.length) {
    const fc = document.createElement("div");
    fc.className = "files-changed";
    fc.textContent = "files changed: " + opts.filesChanged.join(", ");
    msg.appendChild(fc);
  }
  el.appendChild(msg);
  el.scrollTop = el.scrollHeight;
  return msg;
}

function safeJSON(obj) {
  try {
    let s = JSON.stringify(obj);
    if (s.length > 160) s = s.slice(0, 157) + "...";
    return s;
  } catch { return String(obj); }
}

async function sendChat() {
  const ta = $("#prompt");
  const prompt = ta.value.trim();
  if (!prompt) return;
  ta.value = "";
  addChatMessage("user", prompt);
  const busy = $("#busy");
  busy.classList.remove("hidden");
  const btn = $("#sendBtn");
  btn.disabled = true;
  const agentMsg = addChatMessage("agent", "…");
  try {
    const res = await api.chat(prompt);
    agentMsg.querySelector(".body").textContent = res.text || "(no response)";
    if (res.tool_calls && res.tool_calls.length) {
      const tc = document.createElement("div");
      tc.className = "toolcalls";
      tc.appendChild(document.createTextNode(res.tool_calls.length + " tool call(s):"));
      for (const call of res.tool_calls) {
        const line = document.createElement("div");
        line.className = "tc";
        const nm = document.createElement("span");
        nm.className = "nm";
        nm.textContent = call.name;
        line.appendChild(nm);
        line.appendChild(document.createTextNode(" " + safeJSON(call.input)));
        tc.appendChild(line);
      }
      agentMsg.appendChild(tc);
    }
    if (res.files_changed && res.files_changed.length) {
      const fc = document.createElement("div");
      fc.className = "files-changed";
      fc.textContent = "files changed: " + res.files_changed.join(", ");
      agentMsg.appendChild(fc);
      // Refresh tree and reload any open changed files.
      await refreshTree();
      await Promise.all(res.files_changed.map((p) => reloadOpenFile(p)));
    } else {
      await refreshTree();
    }
  } catch (e) {
    agentMsg.querySelector(".body").textContent = "";
    agentMsg.querySelector(".body").classList.add("error");
    agentMsg.querySelector(".body").textContent = "Error: " + e.message;
  } finally {
    busy.classList.add("hidden");
    btn.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// Run panel
// ---------------------------------------------------------------------------
async function runCommand() {
  const cmd = $("#runCmd").value.trim() || "python app.py";
  const out = $("#runOutput");
  out.textContent = "$ " + cmd + "\n";
  out.classList.remove("ok", "err");
  $("#runBtn").disabled = true;
  try {
    const res = await api.run(cmd);
    out.textContent = "$ " + res.command + "\n" + (res.stdout || "") + (res.stderr ? "\n" + res.stderr : "");
    out.classList.add(res.exit_code === 0 ? "ok" : "err");
  } catch (e) {
    out.textContent = "Error: " + e.message;
    out.classList.add("err");
  } finally {
    $("#runBtn").disabled = false;
  }
}

// ---------------------------------------------------------------------------
// Agent health
// ---------------------------------------------------------------------------
async function pollAgentHealth() {
  const el = $("#agentStatus");
  try {
    const h = await api.agentHealth();
    el.textContent = h.reachable ? "agent: online" : "agent: offline";
    el.classList.toggle("ok", !!h.reachable);
    el.classList.toggle("bad", !h.reachable);
  } catch {
    el.textContent = "agent: offline";
    el.classList.add("bad");
  }
  setTimeout(pollAgentHealth, 10000);
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------
function wireEvents() {
  $("#refreshTreeBtn").addEventListener("click", refreshTree);
  $("#sendBtn").addEventListener("click", sendChat);
  $("#prompt").addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      sendChat();
    }
  });
  $("#runBtn").addEventListener("click", runCommand);
  $("#runCmd").addEventListener("keydown", (e) => {
    if (e.key === "Enter") runCommand();
  });
  $("#clearRunBtn").addEventListener("click", () => ($("#runOutput").textContent = ""));
  $("#toggleRunBtn").addEventListener("click", () => {
    $("#runPanel").classList.toggle("collapsed");
    $("#toggleRunBtn").textContent = $("#runPanel").classList.contains("collapsed") ? "▸" : "▾";
  });
}
