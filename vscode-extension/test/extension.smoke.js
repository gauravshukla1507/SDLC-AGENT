// Smoke test of out/extension.js against a mocked VS Code API: activation, contributed commands,
// tree views, a full demo with hiring + release approvals decided "in the IDE", auto-resume, dashboard.
const Module = require("module"); const path = require("path"); const fs = require("fs"); const assert = require("assert");
const commands = {}, posted = [], infos = [], providers = {}, contexts = {};
let webviewHandler, dashboardHtml = "";
const Emitter = class { constructor() { this.l = []; this.event = cb => (this.l.push(cb), { dispose() {} }); } fire(x) { this.l.forEach(c => c(x)); } };
class TreeItem { constructor(label, state) { this.label = label; this.collapsibleState = state; } }
const vscode = {
  StatusBarAlignment: { Left: 1 }, ViewColumn: { One: 1, Active: -1, Beside: -2 }, DiagnosticSeverity: { Error: 0, Warning: 1 },
  TreeItemCollapsibleState: { None: 0, Collapsed: 1, Expanded: 2 }, TreeItem, EventEmitter: Emitter,
  ThemeIcon: class { constructor(id, color) { this.id = id; this.color = color; } }, ThemeColor: class { constructor(id) { this.id = id; } },
  MarkdownString: class { constructor(v) { this.value = v; } },
  Range: class { constructor(...a) { this.a = a; } },
  Diagnostic: class { constructor(r, m, s) { Object.assign(this, { r, message: m, severity: s }); } },
  Uri: { file: p => ({ fsPath: p, toString: () => p }), parse: s => ({ s }) },
  window: {
    createOutputChannel: () => ({ appendLine() {}, dispose() {} }),
    createStatusBarItem: () => ({ show() {}, hide() {}, dispose() {} }),
    createTreeView: (id, o) => { providers[id] = o.treeDataProvider; return { dispose() {} }; },
    showInformationMessage: async (m, ...a) => {
      infos.push(m);
      if (a.includes("Approve all hires")) return "Approve all hires";
      if (/decision\(s\) waiting/.test(m)) {            // a release is waiting: decide in the run panel
        const pending = posted.filter(p => p.type === "approvals").pop().items;
        setTimeout(() => webviewHandler({ type: "decide", id: pending[0].id, approved: true, comment: "Approved in panel" }), 10);
      }
      if (/built, verified and approved/.test(m)) return "Company dashboard";
      return undefined;
    },
    showWarningMessage: async () => undefined,
    showErrorMessage: async m => { throw new Error("showErrorMessage: " + m); },
    showInputBox: async (o) => (o && /Business request/.test(o.title || "") ? process.env.__REQUEST || "" : ""),
    showQuickPick: async items => items[0],
    createWebviewPanel: (type) => ({
      webview: { cspSource: "vscode-resource:", set html(h) { if (type === "agenticSdlcDashboard") dashboardHtml = h; },
        postMessage: m => { posted.push(m); return Promise.resolve(true); },
        onDidReceiveMessage: cb => { if (type === "agenticSdlcRun") webviewHandler = cb; } },
      reveal() {}, onDidDispose() {}, iconPath: null,
    }),
    activeTextEditor: undefined, createTerminal: () => ({ show() {}, sendText() {} }),
  },
  languages: { createDiagnosticCollection: () => ({ clear() {}, set() {}, dispose() {} }) },
  commands: { registerCommand: (id, fn) => (commands[id] = fn, { dispose() {} }),
              executeCommand: async (id, k, v) => { if (id === "setContext") contexts[k] = v; } },
  workspace: { getConfiguration: () => ({ get: (k, d) => d, update: async () => {} }), workspaceFolders: undefined,
               onDidChangeWorkspaceFolders: () => ({ dispose() {} }), getWorkspaceFolder: () => undefined },
  env: { openExternal: async () => true },
};
const orig = Module._load;
Module._load = function (req, ...a) { return req === "vscode" ? vscode : orig.call(this, req, ...a); };
const ext = require("../out/extension");
const context = { extensionPath: path.join(__dirname, ".."), subscriptions: [],
  secrets: { get: async () => undefined, store: async () => {}, delete: async () => {} } };
ext.activate(context);
const pkg = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "package.json")));
assert.deepStrictEqual(Object.keys(commands).sort(), pkg.contributes.commands.map(c => c.command).sort(), "commands registered");
assert.deepStrictEqual(Object.keys(providers).sort(), pkg.contributes.views.agenticSdlc.map(v => v.id).sort(), "views registered");

const wait = async (pred, ms = 120000) => { const t = Date.now(); while (!pred()) { if (Date.now() - t > ms) throw new Error("timeout"); await new Promise(r => setTimeout(r, 200)); } };
(async () => {
  await commands["agenticSdlc.runDemo"](process.env.SAMPLE || "node");
  await wait(() => infos.some(m => /built, verified and approved/.test(m)));
  await wait(() => dashboardHtml.includes("Org chart"));
  const evts = posted.filter(m => m.type === "event").map(m => m.ev);
  const gate = evts.find(e => e.kind === "human_gate");
  assert.ok(gate && gate.approved && gate.comment === "Approved in panel", "release approved from the panel");
  assert.ok(infos.some(m => /waiting to be hired/.test(m)), "hiring requested before work");
  const projects = providers["agenticSdlc.projects"].getChildren();
  assert.strictEqual(projects.length, 1);
  assert.match(projects[0].description, /^released · /);
  const tasks = providers["agenticSdlc.projects"].getChildren(projects[0]);
  assert.ok(tasks.length >= 11 && tasks.every(t => t.iconPath.id === "pass-filled"));
  const org = providers["agenticSdlc.company"].getChildren();
  assert.ok(org[0].label === "ZenLabs Delivery" && org.length >= 2);
  const stacks = providers["agenticSdlc.stacks"].getChildren().map(i => i.label);
  assert.ok(stacks.length >= 3, "stack + checks listed: " + stacks);
  const approvals = providers["agenticSdlc.approvals"].getChildren();
  assert.ok(approvals.some(a => a.label === "Recent decisions"));
  assert.strictEqual(contexts["agenticSdlc.hasCompany"], true);
  console.log(`PASS extension smoke (demo): ${Object.keys(commands).length} commands, 4 views, demo released after hire + release approvals; stacks view: ${stacks.slice(1).join(" | ")}`);

  // ---- live run on the Claude subscription runtime (default), with a stand-in Claude Code CLI
  const os = require("os");
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), "agentic-live-"));
  const orchestrator = path.join(__dirname, "..", "..", "orchestrator");
  Object.assign(process.env, {
    CLAUDE_CODE_BIN: path.join(orchestrator, "tests", "fakes", "fake_claude.py"),
    FAKE_CLAUDE_STATE: path.join(repo, "..", path.basename(repo) + "-fake.json"),
    FAKE_CLAUDE_RECORDING: path.join(orchestrator, "samples", "python-promo-codes", "recording.json"),
    ANTHROPIC_API_KEY: "sk-should-be-stripped",
    __REQUEST: fs.readFileSync(path.join(orchestrator, "samples", "python-promo-codes", "story.md"), "utf8"),
  });
  vscode.workspace.workspaceFolders = [{ uri: { fsPath: repo } }];
  infos.length = 0; posted.length = 0;
  await commands["agenticSdlc.runRequest"]();
  await wait(() => infos.some(m => /built, verified and approved/.test(m)));
  const live = providers["agenticSdlc.projects"].getChildren();
  assert.match(live[0].description, /^released · python/);
  const head = providers["agenticSdlc.company"].getChildren()[0];
  assert.ok(/\$0\.\d+/.test(head.description), "plan usage shown on the company node");
  const dbRuns = require("child_process").execFileSync("python3", ["-c",
    `import sqlite3;print(set(r[0] for r in sqlite3.connect('${repo}/.agentic-sdlc/company.db').execute('select adapter from runs')))`]).toString();
  assert.match(dbRuns, /ClaudeCodeAdapter\/subscription/);
  assert.ok(!/ClaudeApiAdapter/.test(dbRuns));
  console.log(`PASS extension smoke (live, Claude subscription runtime): project released; runs billed as ${dbRuns.trim()}`);
  process.exit(0);
})().catch(e => { console.error("FAIL", e); process.exit(1); });
