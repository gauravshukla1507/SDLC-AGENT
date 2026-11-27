// Smoke test of out/extension.js against a mocked VS Code API: activation, contributed commands,
// tree views, building an application from a requirements document into a new folder and a change in the
// open workspace, with hiring + release approvals decided "in the IDE", auto-resume and the dashboard.
// A stand-in Claude Code CLI (orchestrator/tests/fakes) answers with recorded responses on the subscription runtime.
const Module = require("module"); const path = require("path"); const fs = require("fs"); const assert = require("assert");
const commands = {}, posted = [], infos = [], providers = {}, contexts = {};
const settings = { playbook: "classic" };     // parts 1-2 replay classic fixtures; part 3 is spec-driven
const reviews = [];
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
        const a = pending[0];
        const requestChanges = a.type === "spec" && !reviews.includes("spec:changes");   // first spec review
        reviews.push(a.type + (requestChanges ? ":changes" : ":approved"));
        setTimeout(() => webviewHandler(requestChanges
          ? { type: "decide", id: a.id, approved: false, comment: "Minimum order value uses the subtotal before discounts." }
          : { type: "decide", id: a.id, approved: true, comment: "Approved in panel" }), 10);
      }
      if (/built, verified and approved/.test(m)) return "Company dashboard";
      if (/stories and acceptance criteria are ready/.test(m)) return undefined;
      return undefined;
    },
    showWarningMessage: async () => undefined,
    showErrorMessage: async m => { throw new Error("showErrorMessage: " + m); },
    showInputBox: async (o) => (o && /Business request/.test(o.title || "") ? process.env.__REQUEST || "" : ""),
    showQuickPick: async items => items.find(i => i.target === "new") || items[0],
    showOpenDialog: async o => (o.canSelectFolders ? [{ fsPath: process.env.__NEW_APP_DIR }] : undefined),
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
  workspace: { getConfiguration: () => ({ get: (k, d) => (k in settings ? settings[k] : d), update: async () => {} }), workspaceFolders: undefined,
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
  const os = require("os");
  const orchestrator = path.join(__dirname, "..", "..", "orchestrator");
  const fixtures = path.join(orchestrator, "tests", "fixtures");
  const useFixture = (name, dir) => Object.assign(process.env, {
    CLAUDE_CODE_BIN: path.join(orchestrator, "tests", "fakes", "fake_claude.py"),
    FAKE_CLAUDE_STATE: path.join(os.tmpdir(), `fake-claude-${name}-${Date.now()}`),
    FAKE_CLAUDE_RECORDING: path.join(fixtures, name, "recording.json"),
    ANTHROPIC_API_KEY: "sk-should-be-stripped",
  });

  // ---- 1) requirements document -> new application in a new, empty folder (greenfield)
  const appDir = fs.mkdtempSync(path.join(os.tmpdir(), "agentic-new-app-"));
  process.env.__NEW_APP_DIR = appDir;
  useFixture("node-late-fees");
  await commands["agenticSdlc.buildFromDocument"]({ fsPath: path.join(fixtures, "node-late-fees", "story.md") });
  await wait(() => infos.some(m => /built, verified and approved/.test(m)));
  await wait(() => dashboardHtml.includes("Org chart"));
  const evts = posted.filter(m => m.type === "event").map(m => m.ev);
  const gate = evts.find(e => e.kind === "human_gate");
  assert.ok(gate && gate.approved && gate.comment === "Approved in panel", "release approved from the panel");
  assert.ok(infos.some(m => /waiting to be hired/.test(m)), "hiring requested before work");
  assert.ok(fs.existsSync(path.join(appDir, ".git")), "new application folder is a git repository");
  assert.ok(fs.existsSync(path.join(appDir, "src", "lateFees.js")), "application code written in the new folder");
  const projects = providers["agenticSdlc.projects"].getChildren();
  assert.strictEqual(projects.length, 1);
  assert.match(projects[0].description, /^released · node/);
  const children = providers["agenticSdlc.projects"].getChildren(projects[0]);
  const docsGroup = children.find(c => c.label === "Documents");
  assert.ok(docsGroup, "project shows its documents (stories)");
  const docs = providers["agenticSdlc.projects"].getChildren(docsGroup).map(d => d.label);
  assert.ok(docs.includes("docs/requirements.md") && docs.includes("docs/design.md"), "documents: " + docs);
  assert.ok(infos.some(m => /user stories and acceptance criteria are ready/.test(m)), "stories announced");
  const tasks = children.filter(c => c !== docsGroup);
  assert.ok(tasks.length >= 11 && tasks.every(t => t.iconPath.id === "pass-filled"));
  const org = providers["agenticSdlc.company"].getChildren();
  assert.ok(org[0].label === "ZenLabs Delivery" && org.length >= 2);
  const stacks = providers["agenticSdlc.stacks"].getChildren().map(i => i.label);
  assert.ok(stacks.includes("node"), "stack detected from what the agents built: " + stacks);
  const approvals = providers["agenticSdlc.approvals"].getChildren();
  assert.ok(approvals.some(a => a.label === "Recent decisions"));
  assert.strictEqual(contexts["agenticSdlc.hasCompany"], true);
  assert.ok(!commands["agenticSdlc.runDemo"], "no demo command");
  console.log(`PASS extension smoke (requirements document → new app): ${Object.keys(commands).length} commands, 4 views; stack: ${stacks.slice(1).join(" | ")}`);

  // ---- live run on the Claude subscription runtime (default), with a stand-in Claude Code CLI
  // ---- 2) "Describe a change" in the open workspace
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), "agentic-live-"));
  useFixture("python-promo-codes");
  process.env.__REQUEST = fs.readFileSync(path.join(fixtures, "python-promo-codes", "story.md"), "utf8");
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
  console.log(`PASS extension smoke (change in workspace, Claude subscription runtime): released; runs billed as ${dbRuns.trim()}`);

  // ---- 3) spec-driven development (default playbook) on an existing product, reviews decided in the run panel
  settings.playbook = "specDriven";
  const shop = fs.mkdtempSync(path.join(os.tmpdir(), "agentic-sdd-shop-"));
  fs.cpSync(path.join(fixtures, "python-sdd-existing-shop", "existing"), shop, { recursive: true });
  useFixture("python-sdd-existing-shop");
  process.env.__REQUEST = fs.readFileSync(path.join(fixtures, "python-sdd-existing-shop", "story.md"), "utf8");
  vscode.workspace.workspaceFolders = [{ uri: { fsPath: shop } }];
  infos.length = 0; posted.length = 0; reviews.length = 0;
  await commands["agenticSdlc.runRequest"]();
  await wait(() => infos.some(m => /built, verified and approved/.test(m)));
  assert.deepStrictEqual(reviews, ["spec:changes", "spec:approved", "plan:approved", "release:approved"], reviews.join());
  const sdd = providers["agenticSdlc.projects"].getChildren()[0];
  assert.match(sdd.description, /^released · python · SDD/);
  const sddDocs = providers["agenticSdlc.projects"].getChildren(
    providers["agenticSdlc.projects"].getChildren(sdd).find(c => c.label === "Documents"));
  const byName = Object.fromEntries(sddDocs.map(d => [d.label, d.description]));
  assert.ok(/^spec · 001-promo-codes-at-checkout/.test(byName["spec.md"]) && byName["tasks.md"] && byName["constitution.md"],
            JSON.stringify(byName));
  const spec = fs.readFileSync(path.join(shop, "specs", "001-promo-codes-at-checkout", "spec.md"), "utf8");
  assert.ok(spec.includes("Revised after review"), "the requirements agent revised the spec");
  const sddEvents = posted.filter(m => m.type === "event").map(m => m.ev);
  assert.ok(sddEvents.some(e => e.kind === "tool" && e.tool === "spec_coverage" && e.passed));
  const graphItem = providers["agenticSdlc.stacks"].getChildren().find(i => /Codebase graph/.test(i.label));
  assert.ok(graphItem, "codebase graph shown in Stack & verification");
  console.log(`PASS extension smoke (spec-driven, existing product): reviews ${reviews.join(" → ")}; graph: ${graphItem.description}`);
  process.exit(0);
})().catch(e => { console.error("FAIL", e); process.exit(1); });
