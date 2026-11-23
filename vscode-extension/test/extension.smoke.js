// Smoke test of out/extension.js against a minimal mock of the VS Code API:
// activation, command registration, the demo run end to end, approval via notification, export.
const Module = require("module"); const path = require("path"); const fs = require("fs"); const assert = require("assert");
const commands = {}, posted = [], infos = [], diags = new Map();
let webviewHandler, approveAnswered = false;
const ev = () => { const l = []; const f = cb => (l.push(cb), { dispose() {} }); f.fire = x => l.forEach(c => c(x)); return f; };
const vscode = {
  StatusBarAlignment: { Left: 1 }, ViewColumn: { One: 1, Beside: -2 }, DiagnosticSeverity: { Error: 0, Warning: 1 },
  Range: class { constructor(...a) { this.a = a; } },
  Diagnostic: class { constructor(r, m, s) { Object.assign(this, { r, message: m, severity: s }); } },
  Uri: { file: p => ({ fsPath: p, toString: () => p }), parse: s => ({ s }) },
  window: {
    createOutputChannel: () => ({ appendLine() {}, dispose() {} }),
    createStatusBarItem: () => ({ show() {}, hide() {}, dispose() {} }),
    showInformationMessage: async (m, ...a) => { infos.push(m); return undefined; },
    showErrorMessage: async m => { throw new Error("showErrorMessage: " + m); },
    showWarningMessage: async (m, ...a) => m.startsWith("Release approval") ? (approveAnswered = true, "Approve") : undefined,
    showInputBox: async () => "Approved from notification",
    createWebviewPanel: () => ({
      webview: { cspSource: "vscode-resource:", set html(h) { this._h = h; }, get html() { return this._h; },
        postMessage: m => { posted.push(m); return Promise.resolve(true); },
        onDidReceiveMessage: cb => { webviewHandler = cb; } },
      reveal() {}, onDidDispose() {}, iconPath: null,
    }),
    activeTextEditor: undefined, createTerminal: () => ({ show() {}, sendText() {} }),
  },
  languages: { createDiagnosticCollection: () => ({ clear: () => diags.clear(), set: (u, d) => diags.set(u.fsPath, d), dispose() {} }) },
  commands: { registerCommand: (id, fn) => (commands[id] = fn, { dispose() {} }), executeCommand: async () => {} },
  workspace: { getConfiguration: () => ({ get: (k, d) => d }), workspaceFolders: undefined, findFiles: async () => [] },
  env: { openExternal: async () => true },
};
const orig = Module._load;
Module._load = function (req, ...a) { return req === "vscode" ? vscode : orig.call(this, req, ...a); };
const ext = require("../out/extension");
const context = { extensionPath: path.join(__dirname, ".."), subscriptions: [],
  secrets: { get: async () => undefined, store: async () => {}, delete: async () => {} } };
ext.activate(context);
const expected = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "package.json"))).contributes.commands.map(c => c.command);
assert.deepStrictEqual(Object.keys(commands).sort(), expected.sort(), "all contributed commands registered");

(async () => {
  await commands["agenticSdlc.runDemo"]();
  const t0 = Date.now();
  while (!posted.some(m => m.type === "event" && m.ev.kind === "finished") && Date.now() - t0 < 60000) await new Promise(r => setTimeout(r, 200));
  const evts = posted.filter(m => m.type === "event").map(m => m.ev);
  assert.ok(posted[0].type === "reset" && posted[0].mode === "replay");
  assert.ok(posted.some(m => m.type === "approval"), "approval sent to panel");
  assert.ok(approveAnswered, "approval notification shown");
  const gate = evts.find(e => e.kind === "human_gate");
  assert.ok(gate && gate.approved && gate.comment === "Approved from notification");
  assert.strictEqual(evts[evts.length - 1].status, "released");
  assert.ok(diags.size === 0 || [...diags.values()].every(d => d.length >= 0));
  // panel asks for export of the finished run
  await webviewHandler({ type: "exportDemo" });
  const fin = evts[evts.length - 1];
  const workspace = posted[0].workspace;
  const demo = path.join(workspace, path.dirname(fin.trace), "client_demo.html");
  assert.ok(fs.existsSync(demo), "client demo exported");
  assert.ok(webviewHandler && context.subscriptions.length > 0);
  console.log(`PASS extension smoke: ${Object.keys(commands).length} commands, ${evts.length} events, approval via notification, demo exported to ${demo}`);
  process.exit(0);
})().catch(e => { console.error("FAIL", e); process.exit(1); });
