import * as vscode from "vscode";
import * as cp from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { EngineRun, RunOptions } from "./runner";

const SECRET_KEY = "agenticSdlc.anthropicApiKey";
let current: EngineRun | undefined;
let panel: vscode.WebviewPanel | undefined;
let panelWorkspace = "";
let output: vscode.OutputChannel;
let diagnostics: vscode.DiagnosticCollection;
let status: vscode.StatusBarItem;
let ctx: vscode.ExtensionContext;
const runEvents: any[] = [];

// ------------------------------------------------------------------ activation
export function activate(context: vscode.ExtensionContext) {
  ctx = context;
  output = vscode.window.createOutputChannel("Agentic SDLC");
  diagnostics = vscode.languages.createDiagnosticCollection("agentic-sdlc");
  status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
  status.command = "agenticSdlc.showPanel";
  context.subscriptions.push(output, diagnostics, status);

  const reg = (id: string, fn: (...a: any[]) => any) =>
    context.subscriptions.push(vscode.commands.registerCommand(id, fn));
  reg("agenticSdlc.runStory", () => runFromPrompt());
  reg("agenticSdlc.runFile", (uri?: vscode.Uri) => runFromFile(uri));
  reg("agenticSdlc.runSelection", () => runFromSelection());
  reg("agenticSdlc.runDemo", () => runDemo());
  reg("agenticSdlc.setApiKey", () => setApiKey());
  reg("agenticSdlc.clearApiKey", async () => {
    await context.secrets.delete(SECRET_KEY);
    vscode.window.showInformationMessage("Agentic SDLC: API key removed.");
  });
  reg("agenticSdlc.openRun", () => openRun());
  reg("agenticSdlc.exportDemo", (tracePath?: string) => exportDemo(tracePath));
  reg("agenticSdlc.installDependencies", () => installDependencies());
  reg("agenticSdlc.cancel", () => cancelRun());
  reg("agenticSdlc.showPanel", () => ensurePanel(panelWorkspace).reveal());
}

export function deactivate() {
  current?.cancel();
}

// ------------------------------------------------------------------ settings & environment
function cfg<T>(key: string, fallback: T): T {
  return vscode.workspace.getConfiguration("agenticSdlc").get<T>(key, fallback);
}

const engineDir = () => path.join(ctx.extensionPath, "engine");

function which(cmd: string): boolean {
  try {
    cp.execFileSync(cmd, ["--version"], { stdio: "ignore", timeout: 10000 });
    return true;
  } catch { return false; }
}

function pythonPath(): string | undefined {
  const configured = cfg<string>("pythonPath", "");
  if (configured) { return configured; }
  const candidates = process.platform === "win32" ? ["py", "python", "python3"] : ["python3", "python"];
  return candidates.find(which);
}

async function apiKey(required: boolean): Promise<string | undefined> {
  let key = await ctx.secrets.get(SECRET_KEY) || process.env.ANTHROPIC_API_KEY;
  if (!key && required) {
    const pick = await vscode.window.showWarningMessage(
      "Agentic SDLC needs an Anthropic API key to run the agents live.", "Set API key", "Run demo instead");
    if (pick === "Set API key") { key = await setApiKey(); }
    if (pick === "Run demo instead") { await runDemo(); return undefined; }
  }
  return key;
}

async function setApiKey(): Promise<string | undefined> {
  const key = await vscode.window.showInputBox({
    title: "Anthropic API key", prompt: "Stored in VS Code SecretStorage (OS keychain), never in settings files.",
    password: true, ignoreFocusOut: true, placeHolder: "sk-ant-...",
    validateInput: v => v.trim().length < 20 ? "That doesn't look like an API key" : undefined,
  });
  if (key) {
    await ctx.secrets.store(SECRET_KEY, key.trim());
    vscode.window.showInformationMessage("Agentic SDLC: API key saved securely.");
  }
  return key?.trim();
}

function workspaceRoot(): string | undefined {
  const folders = vscode.workspace.workspaceFolders;
  if (!folders?.length) { return undefined; }
  const active = vscode.window.activeTextEditor?.document.uri;
  const owner = active && vscode.workspace.getWorkspaceFolder(active);
  return (owner || folders[0]).uri.fsPath;
}

// ------------------------------------------------------------------ entry points
async function runFromPrompt() {
  const story = await vscode.window.showInputBox({
    title: "Business request for the agent team",
    prompt: "Describe the Salesforce change (or use 'Run on file' / 'Run on selection' for longer requests).",
    ignoreFocusOut: true,
    placeHolder: "When an Opportunity is Closed Won, create an onboarding Case for the Account…",
  });
  if (story?.trim()) { await startRun(story.trim()); }
}

async function runFromFile(uri?: vscode.Uri) {
  const target = uri || vscode.window.activeTextEditor?.document.uri;
  if (!target) { return vscode.window.showErrorMessage("Open or select a .md/.txt file with the business request."); }
  const text = (await vscode.workspace.fs.readFile(target)).toString();
  if (!text.trim()) { return vscode.window.showErrorMessage("That file is empty."); }
  await startRun(text);
}

async function runFromSelection() {
  const ed = vscode.window.activeTextEditor;
  const text = ed && ed.document.getText(ed.selection);
  if (!text?.trim()) { return vscode.window.showErrorMessage("Select the business request text first."); }
  await startRun(text);
}

async function runDemo() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "agentic-sdlc-demo-"));
  const story = fs.readFileSync(path.join(engineDir(), "samples", "closed_won_onboarding_story.md"), "utf8");
  await startRun(story, {
    workspace: dir,
    replayFile: path.join(engineDir(), "samples", "demo_recording.json"),
    demo: true,
  });
}

// ------------------------------------------------------------------ git safety
function git(cwd: string, args: string[]): string {
  return cp.execFileSync("git", args, { cwd, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }).trim();
}

async function prepareGitBranch(root: string, story: string): Promise<string | undefined | false> {
  if (!cfg<boolean>("createGitBranch", true)) { return undefined; }
  try { git(root, ["rev-parse", "--is-inside-work-tree"]); } catch { return undefined; }
  const dirty = git(root, ["status", "--porcelain"]);
  if (dirty) {
    const go = await vscode.window.showWarningMessage(
      "You have uncommitted changes. The agents' changes will be mixed with yours on the new branch.",
      { modal: true }, "Continue anyway");
    if (go !== "Continue anyway") { return false; }
  }
  const slug = story.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40) || "change";
  const branch = `agentic/${slug}-${new Date().toISOString().slice(0, 16).replace(/[-:T]/g, "")}`;
  try {
    git(root, ["checkout", "-b", branch]);
    output.appendLine(`Created git branch ${branch}`);
    return branch;
  } catch (e: any) {
    vscode.window.showWarningMessage(`Could not create a git branch (${e.message}). Continuing on the current branch.`);
    return undefined;
  }
}

// ------------------------------------------------------------------ run orchestration
interface StartOptions { workspace?: string; replayFile?: string; demo?: boolean; }

async function startRun(story: string, opts: StartOptions = {}) {
  if (current?.isRunning) {
    const pick = await vscode.window.showWarningMessage("A run is already in progress.", "Show it", "Cancel it");
    if (pick === "Show it") { ensurePanel(panelWorkspace).reveal(); }
    if (pick === "Cancel it") { cancelRun(); }
    return;
  }
  const python = pythonPath();
  if (!python) {
    return vscode.window.showErrorMessage("Python 3.11+ not found. Install it or set 'agenticSdlc.pythonPath'.");
  }
  const root = opts.workspace || workspaceRoot();
  if (!root) { return vscode.window.showErrorMessage("Open your Salesforce (SFDX) project folder first."); }

  let key: string | undefined;
  let branch: string | undefined | false;
  if (!opts.replayFile) {
    if (!fs.existsSync(path.join(root, "sfdx-project.json"))) {
      const go = await vscode.window.showWarningMessage(
        "This folder is not an SFDX project (no sfdx-project.json). Create a new SFDX project here?",
        { modal: true }, "Create and continue");
      if (go !== "Create and continue") { return; }
    }
    key = await apiKey(true);
    if (!key) { return; }
    branch = await prepareGitBranch(root, story);
    if (branch === false) { return; }
  }

  const storyFile = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "agentic-story-")), "story.md");
  fs.writeFileSync(storyFile, story, "utf8");
  const recordDir = path.join(root, ".agentic-sdlc", "recordings");
  const recordFile = cfg<boolean>("recordRuns", false) && !opts.replayFile
    ? (fs.mkdirSync(recordDir, { recursive: true }), path.join(recordDir, `${Date.now()}.json`)) : undefined;

  const runOpts: RunOptions = {
    python, engineDir: engineDir(), workspace: root, story, apiKey: key,
    model: cfg<string>("model", "") || undefined,
    targetOrg: opts.replayFile ? undefined : (cfg<string>("targetOrg", "") || undefined),
    minCoverage: cfg<number>("minCoverage", 75),
    maxFixIterations: cfg<number>("maxFixIterations", 3),
    pmdPath: cfg<string>("pmdPath", "") || undefined,
    replayFile: opts.replayFile, recordFile,
  };

  runEvents.length = 0;
  diagnostics.clear();
  panelWorkspace = root;
  const p = ensurePanel(root);
  p.reveal(vscode.ViewColumn.Beside, true);
  post({ type: "reset", story, mode: opts.replayFile ? "replay" : "live", branch: branch || null,
         workspace: root, demo: !!opts.demo });
  output.appendLine(`\n=== Run started in ${root}${opts.replayFile ? " (recorded demo)" : ""} ===`);
  setStatus("$(sync~spin) Agentic SDLC: starting…");

  current = new EngineRun(runOpts, {
    onLog: line => output.appendLine(line),
    onEvent: ev => handleEvent(ev, root),
    onApprovalRequest: c => requestApproval(c),
    onExit: code => {
      output.appendLine(`=== Engine exited (${code}) ===`);
      if (code !== 0 && !runEvents.some(e => e.kind === "finished")) {
        post({ type: "event", ev: { kind: "error", message: `Engine exited with code ${code}. See Output › Agentic SDLC.` } });
        setStatus("$(error) Agentic SDLC: failed", 15000);
      }
    },
  });
  current.start(storyFile);
}

function handleEvent(ev: any, root: string) {
  runEvents.push(ev);
  post({ type: "event", ev });
  switch (ev.kind) {
    case "agent":
      setStatus(`$(sync~spin) Agentic SDLC: ${ev.agent_name} done`);
      break;
    case "tool":
      setStatus(`$(sync~spin) Agentic SDLC: ${ev.tool_name?.split(" (")[0]}: ${ev.summary}`);
      if (ev.tool === "static_analysis") { publishDiagnostics(ev, root); }
      break;
    case "route":
      setStatus(`$(sync~spin) Agentic SDLC: fixing (iteration ${ev.iteration})`);
      break;
    case "finished": {
      const ok = ev.status === "released";
      setStatus(ok ? "$(check) Agentic SDLC: ready for review" : `$(warning) Agentic SDLC: ${ev.status}`, 30000);
      const traceAbs = path.join(root, ev.trace);
      const actions = ok ? ["Review changes", "Open release notes", "Export client demo"] : ["Show run"];
      vscode.window.showInformationMessage(
        ok ? "Agentic SDLC: change is built, verified and approved. Review the diff before you commit."
           : `Agentic SDLC: run ${ev.status}. See the run panel for details.`, ...actions)
        .then(async pick => {
          if (pick === "Review changes") { await vscode.commands.executeCommand("workbench.view.scm"); }
          if (pick === "Open release notes") {
            const notes = path.join(path.dirname(traceAbs), "RELEASE_NOTES.md");
            if (fs.existsSync(notes)) { await vscode.commands.executeCommand("markdown.showPreview", vscode.Uri.file(notes)); }
          }
          if (pick === "Export client demo") { await exportDemo(traceAbs); }
          if (pick === "Show run") { ensurePanel(root).reveal(); }
        });
      break;
    }
    case "error":
      vscode.window.showErrorMessage(`Agentic SDLC: ${ev.message}`);
      break;
    default:
      if (ev.kind === "plan") { setStatus("$(sync~spin) Agentic SDLC: agents working…"); }
  }
}

function publishDiagnostics(ev: any, root: string) {
  diagnostics.clear();
  const byFile = new Map<string, vscode.Diagnostic[]>();
  for (const v of ev.violations || []) {
    const line = Math.max(0, (v.line || 1) - 1);
    const endLine = Math.max(line, (v.end_line || v.line || 1) - 1);
    const d = new vscode.Diagnostic(new vscode.Range(line, 0, endLine, Number.MAX_SAFE_INTEGER),
      v.message, (v.priority ?? 3) <= 2 || /Loop|CRUD|Sharing|Injection/.test(v.rule)
        ? vscode.DiagnosticSeverity.Error : vscode.DiagnosticSeverity.Warning);
    d.source = v.engine === "pmd" ? "Agentic SDLC · PMD" : "Agentic SDLC · metadata";
    d.code = v.rule;
    const list = byFile.get(v.file) || [];
    list.push(d);
    byFile.set(v.file, list);
  }
  for (const [file, list] of byFile) { diagnostics.set(vscode.Uri.file(path.join(root, file)), list); }
}

async function requestApproval(c: any) {
  post({ type: "approval", ctx: c });
  setStatus("$(bell) Agentic SDLC: approval needed");
  const findings = (c.findings || []).length;
  const pick = await vscode.window.showWarningMessage(
    `Release approval needed. Review: ${c.review}, Security: ${c.security}, ${findings} finding(s). Gates: ${c.gates}`,
    "Approve", "Reject", "Review in panel");
  if (pick === "Review in panel") { ensurePanel(panelWorkspace).reveal(); return; }
  if (pick === "Approve" || pick === "Reject") {
    const comment = await vscode.window.showInputBox({ prompt: `${pick} comment (optional)`, ignoreFocusOut: true });
    decide(pick === "Approve", comment || "");
  }
}

function approverName(): string {
  return cfg<string>("approverName", "") || os.userInfo().username;
}

function decide(approved: boolean, comment: string) {
  if (current?.respond({ approved, approver: approverName(), comment })) {
    post({ type: "approvalDone" });
    setStatus(`$(sync~spin) Agentic SDLC: ${approved ? "approved, preparing release" : "rejected"}`);
  }
}

function cancelRun() {
  if (current?.isRunning) {
    current.cancel();
    post({ type: "event", ev: { kind: "error", message: "Run cancelled by user." } });
    setStatus("$(circle-slash) Agentic SDLC: cancelled", 10000);
  }
}

function setStatus(text: string, hideAfterMs?: number) {
  status.text = text;
  status.tooltip = "Agentic SDLC – click to show the run panel";
  status.show();
  if (hideAfterMs) { setTimeout(() => { if (status.text === text) { status.hide(); } }, hideAfterMs); }
}

// ------------------------------------------------------------------ webview panel
function post(msg: any) { panel?.webview.postMessage(msg); }

function ensurePanel(root: string): vscode.WebviewPanel {
  if (panel) { return panel; }
  panel = vscode.window.createWebviewPanel("agenticSdlcRun", "Agentic SDLC", vscode.ViewColumn.Beside, {
    enableScripts: true, retainContextWhenHidden: true,
    localResourceRoots: [vscode.Uri.file(path.join(ctx.extensionPath, "media"))],
  });
  panel.iconPath = vscode.Uri.file(path.join(ctx.extensionPath, "media", "icon.png"));
  const nonce = Array.from({ length: 32 }, () => Math.floor(Math.random() * 36).toString(36)).join("");
  panel.webview.html = fs.readFileSync(path.join(ctx.extensionPath, "media", "panel.html"), "utf8")
    .replace(/{{NONCE}}/g, nonce).replace(/{{CSP_SOURCE}}/g, panel.webview.cspSource);
  panel.onDidDispose(() => { panel = undefined; });
  panel.webview.onDidReceiveMessage(async msg => {
    const base = panelWorkspace || root;
    switch (msg.type) {
      case "ready":
        if (runEvents.length) { post({ type: "replayEvents", events: runEvents }); }
        break;
      case "approve": decide(!!msg.approved, msg.comment || ""); break;
      case "cancel": cancelRun(); break;
      case "openFile": {
        const abs = path.isAbsolute(msg.path) ? msg.path : path.join(base, msg.path);
        if (fs.existsSync(abs)) {
          const doc = await vscode.workspace.openTextDocument(abs);
          await vscode.window.showTextDocument(doc, { viewColumn: vscode.ViewColumn.One, preview: false });
        } else { vscode.window.showWarningMessage(`File not found: ${abs}`); }
        break;
      }
      case "exportDemo": {
        const fin = [...runEvents].reverse().find(e => e.kind === "finished");
        await exportDemo(fin ? path.join(base, fin.trace) : undefined);
        break;
      }
      case "reviewChanges": await vscode.commands.executeCommand("workbench.view.scm"); break;
    }
  });
  return panel;
}

// ------------------------------------------------------------------ past runs & demo export
async function pickTrace(): Promise<string | undefined> {
  const found = await vscode.workspace.findFiles(".agentic-sdlc/runs/*/trace.json", undefined, 50);
  if (!found.length) {
    const pick = await vscode.window.showOpenDialog({ filters: { "Run trace": ["json"] }, canSelectMany: false });
    return pick?.[0]?.fsPath;
  }
  const items = found.map(u => {
    let label = path.basename(path.dirname(u.fsPath)); let detail = "";
    try {
      const t = JSON.parse(fs.readFileSync(u.fsPath, "utf8"));
      label = `${t.status?.toUpperCase() || "?"} · run ${t.run_id}`;
      detail = `${t.started} · ${(t.story || "").replace(/\s+/g, " ").slice(0, 100)}`;
    } catch { /* ignore */ }
    return { label, detail, fsPath: u.fsPath };
  }).sort((a, b) => b.detail.localeCompare(a.detail));
  return (await vscode.window.showQuickPick(items, { title: "Agentic SDLC runs" }))?.fsPath;
}

async function openRun() {
  const tracePath = await pickTrace();
  if (!tracePath) { return; }
  const trace = JSON.parse(fs.readFileSync(tracePath, "utf8"));
  const root = trace.workspace && fs.existsSync(trace.workspace) ? trace.workspace : (workspaceRoot() || "");
  runEvents.length = 0;
  runEvents.push(...trace.events, { kind: "finished", status: trace.status, run_id: trace.run_id,
    trace: path.relative(root, tracePath) });
  panelWorkspace = root;
  ensurePanel(root).reveal();
  post({ type: "loadTrace", trace });
}

async function exportDemo(tracePath?: string) {
  tracePath = tracePath || await pickTrace();
  if (!tracePath) { return; }
  const python = pythonPath();
  if (!python) { return vscode.window.showErrorMessage("Python not found."); }
  const out = path.join(path.dirname(tracePath), "client_demo.html");
  try {
    cp.execFileSync(python, [path.join(engineDir(), "demo", "build_demo.py"), tracePath, out], { cwd: engineDir() });
  } catch (e: any) { return vscode.window.showErrorMessage(`Export failed: ${e.message}`); }
  const pick = await vscode.window.showInformationMessage(
    `Client demo page saved: ${out}`, "Open in browser", "Reveal file");
  if (pick === "Open in browser") { await vscode.env.openExternal(vscode.Uri.file(out)); }
  if (pick === "Reveal file") { await vscode.commands.executeCommand("revealFileInOS", vscode.Uri.file(out)); }
}

async function installDependencies() {
  const python = pythonPath();
  if (!python) { return vscode.window.showErrorMessage("Python 3.11+ not found. Install it or set 'agenticSdlc.pythonPath'."); }
  const term = vscode.window.createTerminal("Agentic SDLC setup");
  term.show();
  term.sendText(`"${python}" -m pip install -r "${path.join(engineDir(), "requirements.txt")}"`);
  const pmd = cfg<string>("pmdPath", "") || (which("pmd") ? "pmd" : "");
  if (!pmd) {
    vscode.window.showInformationMessage(
      "Also install PMD 7 (needs Java 17+) for the Apex static-analysis gate, then set 'agenticSdlc.pmdPath' if it is not on PATH.",
      "PMD download page").then(p => p && vscode.env.openExternal(vscode.Uri.parse("https://pmd.github.io/")));
  }
}
