/**
 * Agentic SDLC for VS Code: an AI delivery company inside the IDE, for any technology.
 *
 * The extension is a thin client over the bundled orchestrator (orchestrator/agentic.py):
 *  - Activity-bar views: Org chart, Projects (tasks), Approvals, Stack & verification
 *  - Live run panel with every agent output, code diffs, gate results and the audit trail
 *  - Asynchronous governance: runs pause for hiring / release / budget / gateway / escalation approvals;
 *    you decide in the IDE and the project resumes
 *  - Problems panel diagnostics from the stack's own verification tools
 *  - Company dashboard and step-through replay pages for stakeholders
 */
import * as vscode from "vscode";
import * as cp from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { EngineEnv, EngineRun, engineCall, engineJson } from "./runner";

const SECRET_KEY = "agenticSdlc.anthropicApiKey";

let ctx: vscode.ExtensionContext;
let output: vscode.OutputChannel;
let diagnostics: vscode.DiagnosticCollection;
let status: vscode.StatusBarItem;
let panel: vscode.WebviewPanel | undefined;
let current: EngineRun | undefined;
let activeRoot: string | undefined;          // folder whose company/projects the views show
let state: any = { company: null, agents: [], projects: [], approvals: [] };
const liveEvents: any[] = [];
let trees: { company: CompanyTree; projects: ProjectsTree; approvals: ApprovalsTree; stacks: StacksTree };
let stackInfo: any = { stacks: [], checks: [] };
let graphInfo: any = { enabled: true, installed: false, built: false };

// ====================================================================== activation
export function activate(context: vscode.ExtensionContext) {
  ctx = context;
  output = vscode.window.createOutputChannel("Agentic SDLC");
  diagnostics = vscode.languages.createDiagnosticCollection("agentic-sdlc");
  status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
  status.command = "agenticSdlc.showRunPanel";
  context.subscriptions.push(output, diagnostics, status);

  trees = { company: new CompanyTree(), projects: new ProjectsTree(), approvals: new ApprovalsTree(),
            stacks: new StacksTree() };
  const views = [
    vscode.window.createTreeView("agenticSdlc.company", { treeDataProvider: trees.company }),
    vscode.window.createTreeView("agenticSdlc.projects", { treeDataProvider: trees.projects }),
    vscode.window.createTreeView("agenticSdlc.approvals", { treeDataProvider: trees.approvals }),
    vscode.window.createTreeView("agenticSdlc.stacks", { treeDataProvider: trees.stacks }),
  ];
  context.subscriptions.push(...views);
  const approvalsView = views[2];

  const reg = (id: string, fn: (...a: any[]) => any) =>
    context.subscriptions.push(vscode.commands.registerCommand(id, fn));
  reg("agenticSdlc.buildFromDocument", (uri?: vscode.Uri) => buildFromDocument(uri));
  reg("agenticSdlc.runRequest", () => runFromPrompt());
  reg("agenticSdlc.runSelection", () => runFromSelection());
  reg("agenticSdlc.resume", () => resume());
  reg("agenticSdlc.approve", (item?: ApprovalItem) => decideItem(item, true));
  reg("agenticSdlc.reject", (item?: ApprovalItem) => decideItem(item, false));
  reg("agenticSdlc.approveAllHires", () => approveAllHires());
  reg("agenticSdlc.refresh", () => refreshAll());
  reg("agenticSdlc.useWorkspace", () => { activeRoot = workspaceRoot(); refreshAll(); });
  reg("agenticSdlc.showRunPanel", () => ensurePanel().reveal());
  reg("agenticSdlc.openProject", (item?: ProjectItem) => openProject(item?.project));
  reg("agenticSdlc.showDashboard", () => showDashboard());
  reg("agenticSdlc.openReplayPage", (item?: ProjectItem) => openReplayPage(item?.project));
  reg("agenticSdlc.revealArtifacts", (item?: ProjectItem) => revealArtifacts(item?.project));
  reg("agenticSdlc.initCompany", () => initCompanyConfig());
  reg("agenticSdlc.setApiKey", () => setApiKey());
  reg("agenticSdlc.signInClaude", () => signInToClaude());
  reg("agenticSdlc.checkSetup", () => checkSetup());
  reg("agenticSdlc.clearApiKey", async () => {
    await ctx.secrets.delete(SECRET_KEY);
    vscode.window.showInformationMessage("Agentic SDLC: API key removed.");
  });
  reg("agenticSdlc.installDependencies", () => installDependencies());
  reg("agenticSdlc.cancel", () => cancelRun());
  reg("agenticSdlc.buildGraph", () => buildGraph());
  reg("agenticSdlc.openGraph", () => openGraph());

  context.subscriptions.push(vscode.workspace.onDidChangeWorkspaceFolders(() => {
    activeRoot = workspaceRoot();
    refreshAll();
  }));
  activeRoot = workspaceRoot();
  void approvalsView;
  refreshAll();
}

export function deactivate() {
  current?.cancel();
}

// ====================================================================== environment
function cfg<T>(key: string, fallback: T): T {
  return vscode.workspace.getConfiguration("agenticSdlc").get<T>(key, fallback);
}

function engineDir(): string {
  return path.join(ctx.extensionPath, "orchestrator");
}

function canRun(cmd: string): boolean {
  try { cp.execFileSync(cmd, ["--version"], { stdio: "ignore", timeout: 10000 }); return true; } catch { return false; }
}

let cachedPython: string | undefined;
function pythonPath(): string | undefined {
  const configured = cfg<string>("pythonPath", "");
  if (configured) { return configured; }
  if (cachedPython) { return cachedPython; }
  const candidates = process.platform === "win32" ? ["py", "python", "python3"] : ["python3", "python"];
  cachedPython = candidates.find(canRun);
  return cachedPython;
}

function runtime(): "claude-subscription" | "api" {
  return cfg<string>("agentRuntime", "claudeSubscription") === "apiKey" ? "api" : "claude-subscription";
}

async function env(withKey: boolean): Promise<EngineEnv | undefined> {
  const python = pythonPath();
  if (!python) {
    vscode.window.showErrorMessage("Agentic SDLC needs Python 3.11+. Install it or set 'agenticSdlc.pythonPath'.");
    return undefined;
  }
  return {
    python, engineDir: engineDir(),
    runtime: runtime(),
    apiKey: withKey && runtime() === "api" ? (await ctx.secrets.get(SECRET_KEY) || process.env.ANTHROPIC_API_KEY) : undefined,
    pmdPath: cfg<string>("pmdPath", "") || undefined,
    store: cfg<string>("companyStore", "") || undefined,
    targetOrg: cfg<string>("salesforceTargetOrg", "") || undefined,
    playbook: cfg<string>("playbook", "specDriven") === "classic" ? "classic" : "spec-driven",
    codebaseGraph: cfg<string>("codebaseGraph", "auto") === "off" ? "off" : "auto",
  };
}

function claudeCli(): string | undefined {
  const configured = cfg<string>("claudeCodePath", "");
  if (configured) { return configured; }
  return canRun("claude") ? "claude" : undefined;
}

/** Make sure the chosen agent runtime is usable: the Claude subscription (Claude Code) or an API key. */
async function requireKey(): Promise<boolean> {
  if (runtime() === "claude-subscription") {
    if (claudeCli()) { return true; }
    const pick = await vscode.window.showWarningMessage(
      "Agentic SDLC runs the agents on your Claude subscription through Claude Code, which isn't installed yet.",
      "Install Claude Code", "Use an API key instead");
    if (pick === "Install Claude Code") { signInToClaude(true); }
    if (pick === "Use an API key instead") {
      await vscode.workspace.getConfiguration("agenticSdlc").update("agentRuntime", "apiKey", vscode.ConfigurationTarget.Global);
      return !!(await setApiKey());
    }
    return false;
  }
  if (await ctx.secrets.get(SECRET_KEY) || process.env.ANTHROPIC_API_KEY) { return true; }
  const pick = await vscode.window.showWarningMessage(
    "Agentic SDLC is set to use an Anthropic API key (setting: agentRuntime = apiKey), and none is set.", "Set API key",
    "Use my Claude subscription");
  if (pick === "Set API key") { return !!(await setApiKey()); }
  if (pick === "Use my Claude subscription") {
    await vscode.workspace.getConfiguration("agenticSdlc").update("agentRuntime", "claudeSubscription", vscode.ConfigurationTarget.Global);
    return requireKey();
  }
  return false;
}

function signInToClaude(install = false) {
  const term = vscode.window.createTerminal("Claude sign-in");
  term.show();
  if (install || !claudeCli()) { term.sendText("npm install -g @anthropic-ai/claude-code"); }
  term.sendText(cfg<string>("claudeCodePath", "") || "claude");
  vscode.window.showInformationMessage(
    "In the Claude terminal, type /login and choose your Claude account (Pro, Max, Team or Enterprise). "
    + "Then close it and run 'Agentic SDLC: Check setup'.");
}

async function checkSetup() {
  const e = await env(false);
  if (!e) { return; }
  const r = await engineCall({ ...e, runtime: runtime() }, ["doctor", "--json", "--probe", "-w", activeRoot || os.tmpdir(),
                                                           "--runtime", runtime()], 240000);
  let data: any;
  try { data = JSON.parse(r.stdout); } catch { return vscode.window.showErrorMessage(`Check failed: ${r.stderr.slice(-300)}`); }
  output.appendLine(`\n=== Agentic SDLC setup (${data.runtime}) ===`);
  for (const c of data.checks) { output.appendLine(`${c.ok ? "OK " : c.ok === null ? "?? " : "!! "} ${c.check}: ${c.detail}`); }
  output.show(true);
  const bad = data.checks.filter((c: any) => c.ok === false && !String(c.check).startsWith("tool for"));
  if (!bad.length) {
    vscode.window.showInformationMessage(runtime() === "claude-subscription"
      ? "Agentic SDLC is ready: agents will run on your Claude subscription." : "Agentic SDLC is ready (API key).");
  } else {
    const pick = await vscode.window.showWarningMessage(`Setup needs attention: ${bad.map((c: any) => c.detail).join("; ")}`,
      ...(runtime() === "claude-subscription" ? ["Sign in to Claude"] : ["Set API key"]));
    if (pick === "Sign in to Claude") { signInToClaude(); }
    if (pick === "Set API key") { await setApiKey(); }
  }
}

async function setApiKey(): Promise<string | undefined> {
  const key = await vscode.window.showInputBox({
    title: "Anthropic API key", prompt: "Stored in VS Code SecretStorage (your OS keychain), never in settings files.",
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

/** Internal: projects started from recorded agent answers (used by the test suite) resume without sign-in. */
function isRecordedRun(root: string): boolean {
  return fs.existsSync(path.join(root, ".agentic-sdlc", "replay.json"));
}

function approverName(): string {
  return cfg<string>("approverName", "") || os.userInfo().username;
}

function commonRunArgs(): string[] {
  const a = ["--approver", "deferred", "--events",
             "--max-fix-iterations", String(cfg<number>("maxFixIterations", 3)),
             "--min-coverage", String(cfg<number>("minCoverage", 75))];
  const org = cfg<string>("salesforceTargetOrg", "");
  if (org) { a.push("--target-org", org); }
  return a;
}

// ====================================================================== entry points
const DOC_FILTERS = { "Requirements document": ["md", "markdown", "txt", "docx", "pdf"] };

/** Main flow: a requirements document in, a built and verified application out. */
async function buildFromDocument(uri?: vscode.Uri) {
  let doc = uri?.fsPath;
  if (!doc) {
    const picked = await vscode.window.showOpenDialog({ canSelectMany: false, filters: DOC_FILTERS,
      title: "Select the requirements document (BRD, PRD, user stories…)", openLabel: "Use this document" });
    doc = picked?.[0]?.fsPath;
  }
  if (!doc) { return; }
  const target = await chooseTarget(path.basename(doc));
  if (!target) { return; }
  await startProject(target, ["--requirements", doc], path.basename(doc));
}

async function chooseTarget(docName: string): Promise<string | undefined> {
  const ws = workspaceRoot();
  const items: (vscode.QuickPickItem & { target: string })[] = [];
  if (ws) {
    items.push({ label: `$(repo) Build in this workspace: ${path.basename(ws)}`, target: "workspace",
      description: "extend the existing code base (the stack is detected from it)" });
  }
  items.push({ label: "$(new-folder) Create a new application in a new folder…", target: "new",
    description: "greenfield: the architect chooses the stack from the document" });
  const pick = items.length === 1 ? items[0] : await vscode.window.showQuickPick(items,
    { title: `Where should the agents build ${docName}?` });
  if (!pick) { return undefined; }
  if (pick.target === "workspace") { return ws; }
  const folder = await vscode.window.showOpenDialog({ canSelectFiles: false, canSelectFolders: true,
    canSelectMany: false, openLabel: "Create the application here", title: "Choose or create an empty folder" });
  const dir = folder?.[0]?.fsPath;
  if (!dir) { return undefined; }
  const entries = fs.existsSync(dir) ? fs.readdirSync(dir).filter(f => !f.startsWith(".")) : [];
  if (entries.length) {
    const go = await vscode.window.showWarningMessage(
      `${path.basename(dir)} is not empty (${entries.length} item(s)). The agents will treat it as an existing code base.`,
      { modal: true }, "Continue");
    if (go !== "Continue") { return undefined; }
  }
  return dir;
}

async function runFromPrompt() {
  const root = workspaceRoot();
  if (!root) { return buildFromDocument(); }
  const story = await vscode.window.showInputBox({
    title: "Business request for the agent team",
    prompt: "Describe the change. For a full requirements document use 'Build application from requirements document'.",
    ignoreFocusOut: true, placeHolder: "Customers who pay late should be charged the late fee in our terms…",
  });
  if (story?.trim()) { await startProject(root, ["--requirements", writeTemp(story)], "request"); }
}

async function runFromSelection() {
  const ed = vscode.window.activeTextEditor;
  const text = ed && ed.document.getText(ed.selection);
  const root = workspaceRoot();
  if (!text?.trim() || !root) { return vscode.window.showErrorMessage("Open the repository and select the requirement text first."); }
  await startProject(root, ["--requirements", writeTemp(text)], "selection");
}

function writeTemp(text: string): string {
  const f = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "agentic-request-")), "request.md");
  fs.writeFileSync(f, text, "utf8");
  return f;
}

async function startProject(root: string, requestArgs: string[], label: string) {
  if (!(await requireKey())) { return; }
  fs.mkdirSync(root, { recursive: true });
  ensureGitRepo(root);
  const branch = await prepareGitBranch(root, label);
  if (branch === false) { return; }
  await launch(["run", ...requestArgs, "-w", root, ...commonRunArgs()], root,
    { story: `Requirements: ${label}`, mode: "live", branch: branch || undefined, fresh: true });
}

async function resume(root = activeRoot) {
  if (!root) { return; }
  if (!isRecordedRun(root) && !(await requireKey())) { return; }
  await launch(["resume", "-w", root, ...commonRunArgs()], root, { mode: isRecordedRun(root) ? "replay" : "live", fresh: false });
}

// ====================================================================== git safety
/** New application folders become git repositories so every change can be reviewed as a diff. */
function ensureGitRepo(root: string) {
  try { git(root, ["rev-parse", "--is-inside-work-tree"]); return; } catch { /* not a repo yet */ }
  try {
    git(root, ["init", "-q"]);
    const ignore = path.join(root, ".gitignore");
    if (!fs.existsSync(ignore)) { fs.writeFileSync(ignore, ".agentic-sdlc/\nnode_modules/\n__pycache__/\n.venv/\ntarget/\nbin/\nobj/\n"); }
    output.appendLine(`Initialised a git repository in ${root}`);
  } catch (e: any) { output.appendLine(`git init skipped: ${e.message}`); }
}

function git(cwd: string, args: string[]): string {
  return cp.execFileSync("git", args, { cwd, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }).trim();
}

async function prepareGitBranch(root: string, story: string): Promise<string | undefined | false> {
  if (!cfg<boolean>("createGitBranch", true)) { return undefined; }
  try { git(root, ["rev-parse", "--is-inside-work-tree"]); git(root, ["rev-parse", "HEAD"]); } catch { return undefined; }
  if (git(root, ["status", "--porcelain"])) {
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

// ====================================================================== runs
interface LaunchInfo { story?: string; title?: string; mode: "live" | "replay"; branch?: string; fresh: boolean; }

async function launch(args: string[], root: string, info: LaunchInfo) {
  if (current?.isRunning) {
    const pick = await vscode.window.showWarningMessage("A run is already in progress.", "Show it", "Cancel it");
    if (pick === "Show it") { ensurePanel().reveal(); }
    if (pick === "Cancel it") { cancelRun(); }
    return;
  }
  const e = await env(info.mode === "live");
  if (!e) { return; }
  activeRoot = root;
  diagnostics.clear();
  const p = ensurePanel();
  p.reveal(vscode.ViewColumn.Beside, true);
  if (info.fresh) {
    liveEvents.length = 0;
    post({ type: "reset", story: info.story, title: info.title, mode: info.mode, branch: info.branch });
  } else {
    post({ type: "continue" });
  }
  post({ type: "approvals", items: [] });
  output.appendLine(`\n=== agentic.py ${args.join(" ")} ===`);
  setStatus("$(sync~spin) Agentic SDLC: agents working…");

  let finished: any;
  current = new EngineRun(e, {
    onLog: line => output.appendLine(line),
    onEvent: ev => {
      if (ev.kind === "finished") { finished = ev; }
      handleEvent(ev, root);
    },
    onExit: async code => {
      output.appendLine(`=== exit ${code} ===`);
      await refreshAll();
      if (!finished && code !== 0) {
        post({ type: "event", ev: { kind: "error", message: `The orchestrator stopped (exit ${code}). See Output › Agentic SDLC.` } });
        setStatus("$(error) Agentic SDLC: failed", 15000);
        return;
      }
      await afterRun(root, finished?.status);
    },
  });
  current.start(args);
}

function handleEvent(ev: any, root: string) {
  liveEvents.push(ev);
  post({ type: "event", ev });
  if (ev.kind === "agent") {
    setStatus(`$(sync~spin) Agentic SDLC: ${ev.agent_name} finished`);
    void refreshAll();
    if (ev.agent === "requirements" && ev.file_paths?.["docs/requirements.md"]) {
      const file = path.join(root, ev.file_paths["docs/requirements.md"]);
      vscode.window.showInformationMessage("Agentic SDLC: user stories and acceptance criteria are ready.", "Open stories")
        .then(pick => { if (pick) { void vscode.window.showTextDocument(vscode.Uri.file(file), { preview: false,
          viewColumn: vscode.ViewColumn.One }); } });
    }
  }
  if (ev.kind === "tool") {
    setStatus(`$(sync~spin) Agentic SDLC: ${String(ev.tool_name || "").split(" (")[0]}: ${ev.passed ? "passed" : ev.skipped ? "skipped" : "failed"}`);
    if (ev.tool === "verify") { publishDiagnostics(ev, root); }
  }
  if (ev.kind === "route") { setStatus(`$(sync~spin) Agentic SDLC: ${ev.to_name} fixing (iteration ${ev.iteration})`); }
  if (ev.kind === "plan") { void refreshAll(); }
  if (ev.kind === "error") { vscode.window.showErrorMessage(`Agentic SDLC: ${ev.message}`); }
}

async function afterRun(root: string, status?: string) {
  const pending = state.approvals.filter((a: any) => a.status === "pending");
  post({ type: "approvals", items: pending });
  if (status === "released") {
    setStatus("$(check) Agentic SDLC: ready for review", 30000);
    const elsewhere = root !== workspaceRoot();
    const pick = await vscode.window.showInformationMessage(
      "Agentic SDLC: the change is built, verified and approved. Review the diff before you commit.",
      ...(elsewhere ? ["Open application folder"] : ["Review changes"]), "Company dashboard", "Replay page");
    if (pick === "Open application folder") {
      await vscode.commands.executeCommand("vscode.openFolder", vscode.Uri.file(root), { forceNewWindow: true });
    }
    if (pick === "Review changes") { await vscode.commands.executeCommand("workbench.view.scm"); }
    if (pick === "Company dashboard") { await showDashboard(); }
    if (pick === "Replay page") { await openReplayPage(state.projects[0]); }
  } else if (pending.length) {
    setStatus(`$(bell) Agentic SDLC: ${pending.length} approval(s) waiting`);
    const hires = pending.filter((a: any) => a.type === "hire");
    const actions = hires.length ? ["Approve all hires", "Review"] : ["Review"];
    const msg = hires.length && hires.length === pending.length
      ? `Agentic SDLC: ${hires.length} agent(s) are waiting to be hired before work can start.`
      : `Agentic SDLC: ${pending.length} decision(s) waiting for you (${[...new Set(pending.map((a: any) => a.type))].join(", ")}).`;
    const docReview = pending.find((a: any) => (a.type === "spec" || a.type === "plan") && a.payload?.document_path);
    if (docReview) { actions.push(`Open ${docReview.type}`); }
    const pick = await vscode.window.showInformationMessage(
      docReview ? `${msg} Review the ${docReview.type} (${docReview.payload.document}) and approve it or request changes.` : msg,
      ...actions);
    if (pick === "Approve all hires") { await approveAllHires(); }
    if (docReview && pick === `Open ${docReview.type}`) {
      ensurePanel().reveal();
      await vscode.window.showTextDocument(vscode.Uri.file(docReview.payload.document_path), { preview: false });
    }
    if (pick === "Review") { ensurePanel().reveal(); await vscode.commands.executeCommand("agenticSdlc.approvals.focus"); }
  } else if (status === "paused") {
    const reason = state.projects.find((p: any) => p.status === "paused")?.paused_reason || "the agent runtime is unavailable";
    setStatus("$(debug-pause) Agentic SDLC: paused", 30000);
    const pick = await vscode.window.showWarningMessage(`Agentic SDLC paused: ${reason}`,
      ...(runtime() === "claude-subscription" ? ["Sign in to Claude", "Resume"] : ["Set API key", "Resume"]));
    if (pick === "Sign in to Claude") { signInToClaude(); }
    if (pick === "Set API key") { await setApiKey(); }
    if (pick === "Resume") { await resume(root); }
  } else if (status) {
    setStatus(`$(warning) Agentic SDLC: ${status}`, 30000);
    vscode.window.showWarningMessage(`Agentic SDLC: project ${status}. See the run panel.`);
  }
}

function publishDiagnostics(ev: any, root: string) {
  diagnostics.clear();
  const byFile = new Map<string, vscode.Diagnostic[]>();
  for (const v of ev.violations || []) {
    if (!v.file) { continue; }
    const line = Math.max(0, (v.line || 1) - 1);
    const d = new vscode.Diagnostic(new vscode.Range(line, 0, Math.max(line, (v.end_line || v.line || 1) - 1), 10000),
      `${v.rule}: ${v.message}`, vscode.DiagnosticSeverity.Error);
    d.source = `Agentic SDLC · ${v.engine || "verify"}`;
    const list = byFile.get(v.file) || [];
    list.push(d);
    byFile.set(v.file, list);
  }
  for (const [file, list] of byFile) { diagnostics.set(vscode.Uri.file(path.join(root, file)), list); }
}

function cancelRun() {
  if (current?.isRunning) {
    current.cancel();
    post({ type: "event", ev: { kind: "error", message: "Run cancelled. Resume it any time from the Projects view." } });
    setStatus("$(circle-slash) Agentic SDLC: cancelled", 10000);
  }
}

function setStatus(text: string, hideAfterMs?: number) {
  status.text = text;
  status.tooltip = "Agentic SDLC: show the run panel";
  status.show();
  if (hideAfterMs) { setTimeout(() => { if (status.text === text) { status.hide(); } }, hideAfterMs); }
}

// ====================================================================== codebase graph
async function buildGraph() {
  const root = activeRoot;
  const e = await env(false);
  if (!root || !e) { return; }
  const r = await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification,
    title: "Agentic SDLC: mapping the codebase (Graphify)…" }, () => engineCall(e, ["graph", "build", "-w", root], 600000));
  const msg = (r.stdout || r.stderr).trim().split("\n").pop() || "";
  if (r.code !== 0) { vscode.window.showErrorMessage(`Agentic SDLC: ${msg}`); } else {
    vscode.window.showInformationMessage(`Agentic SDLC: ${msg}`, "Open graph").then(p => { if (p) { openGraph(); } });
  }
  await refreshAll();
}

function openGraph() {
  const html = path.join(activeRoot || "", "graphify-out", "graph.html");
  if (!fs.existsSync(html)) { return vscode.window.showWarningMessage("No graph yet: run 'Build codebase graph'."); }
  vscode.env.openExternal(vscode.Uri.file(html));
}

// ====================================================================== approvals
async function decide(id: string, approved: boolean, comment?: string) {
  const root = activeRoot;
  const e = await env(false);
  if (!root || !e) { return; }
  const kind = (state.approvals || []).find((a: any) => a.id === id)?.type;
  if (!approved && (kind === "spec" || kind === "plan") && !comment) {
    comment = await vscode.window.showInputBox({
      prompt: `What should change in the ${kind}? The author revises it from your comment, then asks you again.`,
      ignoreFocusOut: true, validateInput: v => v.trim() ? undefined : "A comment is required to request changes" });
    if (!comment) { return; }
  }
  if (comment === undefined) {
    comment = await vscode.window.showInputBox({
      prompt: approved ? "Approval comment (optional)" : "Why are you rejecting? (recorded in the audit trail)",
      ignoreFocusOut: true }) ?? undefined;
    if (comment === undefined && !approved) { return; }
  }
  const args = ["approve", id, "--as", approverName(), "-w", root];
  if (!approved) { args.push("--reject"); }
  if (comment) { args.push("--comment", comment); }
  const r = await engineCall(e, args);
  if (r.code !== 0) { return vscode.window.showErrorMessage(`Agentic SDLC: could not record the decision: ${r.stderr.slice(-300)}`); }
  await afterDecision(root);
}

async function decideItem(item: ApprovalItem | undefined, approved: boolean) {
  if (item) { await decide(item.approval.id, approved); }
}

async function approveAllHires() {
  const root = activeRoot;
  const e = await env(false);
  if (!root || !e) { return; }
  const r = await engineCall(e, ["approve", "all", "--type", "hire", "--as", approverName(), "--comment",
                                 "Hiring approved in VS Code", "-w", root]);
  if (r.code !== 0) { return vscode.window.showErrorMessage(`Agentic SDLC: ${r.stderr.slice(-300)}`); }
  await afterDecision(root);
}

async function afterDecision(root: string) {
  await refreshAll();
  const pending = state.approvals.filter((a: any) => a.status === "pending");
  post({ type: "approvals", items: pending });
  const open = state.projects.some((p: any) => !["released", "rejected"].includes(p.status));
  if (!pending.length && open && cfg<boolean>("autoResume", true)) { await resume(root); }
  else if (!pending.length && open) {
    const pick = await vscode.window.showInformationMessage("All decisions recorded.", "Resume project");
    if (pick) { await resume(root); }
  }
}

// ====================================================================== state & views
async function refreshAll() {
  const root = activeRoot;
  const e = await env(false);
  if (!root || !e) {
    state = { company: null, agents: [], projects: [], approvals: [] };
  } else {
    try { state = await engineJson(e, ["state", "-w", root]); } catch (err: any) {
      output.appendLine(`state: ${err.message}`);
    }
    try { stackInfo = await engineJson(e, ["stacks", "-w", root]); } catch (err: any) {
      output.appendLine(`stacks: ${err.message}`);
    }
    try { graphInfo = await engineJson(e, ["graph", "status", "-w", root]); } catch (err: any) {
      output.appendLine(`graph: ${err.message}`);
    }
  }
  vscode.commands.executeCommand("setContext", "agenticSdlc.hasCompany", !!state.company);
  vscode.commands.executeCommand("setContext", "agenticSdlc.pendingApprovals",
    state.approvals.filter((a: any) => a.status === "pending").length);
  Object.values(trees).forEach(t => t.refresh());
}

const STATUS_ICON: Record<string, [string, string?]> = {
  done: ["pass-filled", "testing.iconPassed"], in_progress: ["sync~spin"], todo: ["circle-large-outline"],
  blocked: ["bell", "list.warningForeground"], cancelled: ["circle-slash", "disabledForeground"],
  failed: ["error", "testing.iconFailed"], released: ["rocket", "testing.iconPassed"],
  waiting_approval: ["bell", "list.warningForeground"], escalated: ["warning", "list.errorForeground"],
  rejected: ["circle-slash"], running: ["sync~spin"], queued: ["clock"], paused: ["debug-pause", "list.warningForeground"],
};

function icon(key: string): vscode.ThemeIcon {
  const [id, color] = STATUS_ICON[key] || ["circle-outline"];
  return new vscode.ThemeIcon(id, color ? new vscode.ThemeColor(color) : undefined);
}

abstract class BaseTree<T extends vscode.TreeItem> implements vscode.TreeDataProvider<T> {
  private emitter = new vscode.EventEmitter<T | undefined>();
  readonly onDidChangeTreeData = this.emitter.event;
  refresh() { this.emitter.fire(undefined); }
  getTreeItem(el: T) { return el; }
  abstract getChildren(el?: T): T[];
}

class InfoItem extends vscode.TreeItem {
  constructor(label: string, description?: string, iconId?: string, tooltip?: string) {
    super(label, vscode.TreeItemCollapsibleState.None);
    this.description = description;
    if (iconId) { this.iconPath = new vscode.ThemeIcon(iconId); }
    this.tooltip = tooltip;
  }
}

class AgentItem extends vscode.TreeItem {
  constructor(public readonly agent: any, hasChildren: boolean) {
    super(agent.name, hasChildren ? vscode.TreeItemCollapsibleState.Expanded : vscode.TreeItemCollapsibleState.None);
    const human = agent.adapter?.type === "human";
    const budget = agent.budget_usd ? ` · $${agent.spent_usd.toFixed(4)} / $${agent.budget_usd}` : "";
    this.description = `${agent.status.replace("_", " ")}${budget}`;
    this.iconPath = new vscode.ThemeIcon(human ? "account" : "hubot",
      new vscode.ThemeColor(agent.status === "active" ? "testing.iconPassed" :
        agent.status === "paused" ? "list.warningForeground" : "disabledForeground"));
    this.tooltip = new vscode.MarkdownString(`**${agent.name}**: ${agent.title}\n\n` +
      (human ? "Human" : `Adapter: \`${agent.adapter?.type}\`${agent.adapter?.model ? ` · model \`${agent.adapter.model}\`` : ""}`) +
      (agent.skills?.length ? `\n\nSkills: ${agent.skills.join(", ")}` : ""));
    this.contextValue = "agent";
  }
}

class CompanyTree extends BaseTree<vscode.TreeItem> {
  getChildren(el?: vscode.TreeItem): vscode.TreeItem[] {
    if (!state.company) { return []; }
    const kids = (key: string | null) => state.agents.filter((a: any) => (a.reports_to || null) === key)
      .map((a: any) => new AgentItem(a, state.agents.some((b: any) => b.reports_to === a.key)));
    if (!el) {
      const s = state.spend || {};
      const head = new InfoItem(state.company.name,
        `$${(s.total_usd || 0).toFixed(4)}${s.limit_usd ? ` of $${s.limit_usd}` : ""}${s.estimated ? " (est.)" : ""}`,
        "organization", state.company.mission);
      return [head, ...kids(null)];
    }
    return el instanceof AgentItem ? kids(el.agent.key) : [];
  }
}

class ProjectItem extends vscode.TreeItem {
  constructor(public readonly project: any) {
    super(project.title, vscode.TreeItemCollapsibleState.Collapsed);
    this.description = `${project.status.replace("_", " ")}${project.stacks?.length ? " · " + project.stacks.join(", ") : ""}${
      project.playbook === "spec-driven" ? " · SDD" : ""}`;
    this.iconPath = icon(project.status);
    this.contextValue = project.trace ? "project.withTrace" : "project";
    this.tooltip = new vscode.MarkdownString(`**${project.title}**\n\nStatus: ${project.status}\n\nStacks: ${
      project.stacks?.join(", ") || "not detected yet"}\n\nSpend: $${project.spent_usd}${project.budget_usd ? ` of $${project.budget_usd}` : ""}`);
    this.command = { command: "agenticSdlc.openProject", title: "Open", arguments: [this] };
  }
}

class TaskItem extends vscode.TreeItem {
  constructor(t: any) {
    super(t.title, vscode.TreeItemCollapsibleState.None);
    this.description = `${t.assignee}${t.attempts ? ` · attempt ${t.attempts}` : ""}`;
    this.iconPath = icon(t.status);
    this.tooltip = new vscode.MarkdownString(`**${t.title}** (${t.kind})\n\n${t.status}${t.blocked_reason ? `: ${t.blocked_reason}` : ""}\n\n${t.summary || ""}`);
  }
}

class DocItem extends vscode.TreeItem {
  constructor(file: string, artifactsRoot: string) {
    const inArtifacts = !!artifactsRoot && !path.relative(artifactsRoot, file).startsWith("..");
    super(inArtifacts ? path.relative(artifactsRoot, file) : path.basename(file), vscode.TreeItemCollapsibleState.None);
    const name = path.basename(file);
    const specDir = /[\\/]specs[\\/]([^\\/]+)[\\/]/.exec(file)?.[1];
    this.description = name === "requirements.md" ? "epic, user stories, acceptance criteria"
      : name === "spec.md" ? `spec · ${specDir}` : name === "plan.md" ? `plan · ${specDir}`
      : name === "tasks.md" ? `tasks · ${specDir}` : name === "constitution.md" ? "project principles"
      : name === "design.md" ? "solution design" : name === "RELEASE_NOTES.md" ? "release notes" : undefined;
    this.iconPath = new vscode.ThemeIcon(name === "requirements.md" || name === "spec.md" ? "checklist"
      : name === "tasks.md" ? "tasklist" : name === "constitution.md" ? "law" : "file-text");
    this.command = { command: "vscode.open", title: "Open", arguments: [vscode.Uri.file(file)] };
    this.tooltip = file;
  }
}

class DocsGroup extends vscode.TreeItem {
  constructor(public readonly project: any) {
    super("Documents", vscode.TreeItemCollapsibleState.Expanded);
    this.iconPath = new vscode.ThemeIcon("files");
    this.description = `${project.documents.length} file(s)`;
  }
}

class ProjectsTree extends BaseTree<vscode.TreeItem> {
  getChildren(el?: vscode.TreeItem): vscode.TreeItem[] {
    if (!el) { return (state.projects || []).map((p: any) => new ProjectItem(p)); }
    if (el instanceof ProjectItem) {
      const docs = el.project.documents?.length ? [new DocsGroup(el.project)] : [];
      return [...docs, ...el.project.tasks.map((t: any) => new TaskItem(t))];
    }
    if (el instanceof DocsGroup) {
      const root = el.project.trace ? path.dirname(el.project.trace) : "";
      return el.project.documents.map((f: string) => new DocItem(f, root));
    }
    return [];
  }
}

class ApprovalItem extends vscode.TreeItem {
  constructor(public readonly approval: any) {
    super(approval.subject, vscode.TreeItemCollapsibleState.None);
    const pending = approval.status === "pending";
    this.description = pending ? approval.type.replace("_", " ") :
      `${approval.status} by ${approval.decided_by}${approval.comment ? `: ${approval.comment}` : ""}`;
    this.iconPath = pending ? new vscode.ThemeIcon("bell", new vscode.ThemeColor("list.warningForeground"))
      : new vscode.ThemeIcon(approval.status === "approved" ? "check" : "close");
    this.contextValue = pending ? "approval.pending" : "approval.decided";
    const p = approval.payload || {};
    this.tooltip = new vscode.MarkdownString(`**${approval.subject}**\n\n` + Object.entries(p)
      .filter(([, v]) => v !== null && v !== "" && !(Array.isArray(v) && !v.length))
      .map(([k, v]) => `- ${k}: ${typeof v === "object" ? "`" + JSON.stringify(v).slice(0, 200) + "`" : v}`).join("\n"));
  }
}

class ApprovalsTree extends BaseTree<vscode.TreeItem> {
  getChildren(el?: vscode.TreeItem): vscode.TreeItem[] {
    if (el) { return []; }
    const all = state.approvals || [];
    const pending = all.filter((a: any) => a.status === "pending").map((a: any) => new ApprovalItem(a));
    const recent = all.filter((a: any) => a.status !== "pending").slice(0, 10).map((a: any) => new ApprovalItem(a));
    return [...pending, ...(recent.length ? [new InfoItem("Recent decisions", "", "history"), ...recent] : [])];
  }
}

class StacksTree extends BaseTree<vscode.TreeItem> {
  getChildren(el?: vscode.TreeItem): vscode.TreeItem[] {
    if (el) { return []; }
    const items: vscode.TreeItem[] = [];
    const root = activeRoot ? path.basename(activeRoot) : "no folder";
    items.push(new InfoItem(root, isRecordedRun(activeRoot || "") ? "recorded run" : "workspace", "folder",
      activeRoot));
    if (!stackInfo.stacks?.length) {
      items.push(new InfoItem("Greenfield", "the architect will choose the stack", "sparkle",
        "No stack detected yet. Detection runs again after the agents build."));
    }
    for (const s of stackInfo.stacks || []) {
      items.push(new InfoItem(s.name, s.evidence.join(", "), "layers", s.skill ? `Stack skill: ${s.skill}` : undefined));
    }
    const g = graphInfo || {};
    const graph = new InfoItem("Codebase graph (Graphify)",
      !g.enabled ? "off (setting)" : !g.installed ? "not installed · pip install graphifyy"
        : g.built ? `${g.nodes} nodes · ${g.edges} edges` : "not built yet",
      g.built ? "type-hierarchy" : "circuit-board",
      "Existing products are mapped into a knowledge graph so agents reuse existing code and reviewers see the blast radius.");
    graph.contextValue = g.built ? "graph.built" : "graph";
    graph.command = g.built ? { command: "agenticSdlc.openGraph", title: "Open graph" }
      : g.installed && g.enabled ? { command: "agenticSdlc.buildGraph", title: "Build graph" } : undefined;
    items.push(graph);
    for (const c of stackInfo.checks || []) {
      const it = new InfoItem(c.name, `${c.kind} · ${c.blocking ? "blocking" : "advisory"}${c.installed ? "" : " · not installed"}`,
        c.installed ? "beaker" : "warning", c.command);
      items.push(it);
    }
    return items;
  }
}

// ====================================================================== panel, dashboard, replay
function post(msg: any) { panel?.webview.postMessage(msg); }

function ensurePanel(): vscode.WebviewPanel {
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
    switch (msg.type) {
      case "ready":
        if (liveEvents.length) { post({ type: "replayEvents", events: liveEvents }); }
        post({ type: "approvals", items: (state.approvals || []).filter((a: any) => a.status === "pending") });
        break;
      case "decide": await decide(msg.id, !!msg.approved, msg.comment || ""); break;
      case "approveHires": await approveAllHires(); break;
      case "cancel": cancelRun(); break;
      case "openFile": {
        const abs = path.isAbsolute(msg.path) ? msg.path : path.join(activeRoot || "", msg.path);
        if (fs.existsSync(abs)) {
          await vscode.window.showTextDocument(await vscode.workspace.openTextDocument(abs),
            { viewColumn: vscode.ViewColumn.One, preview: false });
        } else { vscode.window.showWarningMessage(`File not found: ${abs}`); }
        break;
      }
      case "replayPage": await openReplayPage(state.projects?.[0]); break;
      case "reviewChanges": await vscode.commands.executeCommand("workbench.view.scm"); break;
    }
  });
  return panel;
}

async function openProject(project?: any) {
  if (!project) { return; }
  const p = ensurePanel();
  p.reveal();
  if (project.trace && fs.existsSync(project.trace)) {
    const trace = JSON.parse(fs.readFileSync(project.trace, "utf8"));
    liveEvents.length = 0;
    liveEvents.push(...trace.events);
    post({ type: "loadTrace", trace });
  }
  post({ type: "approvals", items: (state.approvals || []).filter((a: any) => a.status === "pending") });
}

async function showDashboard() {
  const root = activeRoot;
  const e = await env(false);
  if (!root || !e || !state.company) { return vscode.window.showInformationMessage("No company yet. Build from a requirements document first."); }
  const out = path.join(os.tmpdir(), `agentic-dashboard-${Date.now()}.html`);
  const r = await engineCall(e, ["dashboard", "-w", root, "--out", out]);
  if (r.code !== 0) { return vscode.window.showErrorMessage(`Dashboard failed: ${r.stderr.slice(-300)}`); }
  const view = vscode.window.createWebviewPanel("agenticSdlcDashboard", `Company: ${state.company.name}`,
    vscode.ViewColumn.Active, { enableScripts: false });
  view.webview.html = fs.readFileSync(out, "utf8").replace("<head>",
    `<head><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:;">`);
}

async function openReplayPage(project?: any) {
  const e = await env(false);
  if (!e || !project?.trace) { return vscode.window.showInformationMessage("This project has no trace yet."); }
  const out = path.join(path.dirname(project.trace), "run_replay.html");
  const r = await engineCall(e, ["replay-page", project.trace, "--out", out]);
  if (r.code !== 0) { return vscode.window.showErrorMessage(`Replay page failed: ${r.stderr.slice(-300)}`); }
  const pick = await vscode.window.showInformationMessage(`Replay page saved: ${out}`, "Open in browser", "Reveal file");
  if (pick === "Open in browser") { await vscode.env.openExternal(vscode.Uri.file(out)); }
  if (pick === "Reveal file") { await vscode.commands.executeCommand("revealFileInOS", vscode.Uri.file(out)); }
}

async function revealArtifacts(project?: any) {
  if (project?.trace) { await vscode.commands.executeCommand("revealFileInOS", vscode.Uri.file(project.trace)); }
}

async function initCompanyConfig() {
  const root = workspaceRoot();
  if (!root) { return vscode.window.showErrorMessage("Open a folder first."); }
  const target = path.join(root, ".agentic-sdlc", "company.json");
  if (!fs.existsSync(target)) {
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.copyFileSync(path.join(engineDir(), "companies", "default-company.json"), target);
  }
  await vscode.window.showTextDocument(await vscode.workspace.openTextDocument(target));
  vscode.window.showInformationMessage(
    "Edit your company: org chart, adapters and models per seat, budgets, governance and gateway rules, routines. "
    + "It's used for the next run in this repository.");
}

async function installDependencies() {
  const python = pythonPath();
  if (!python) { return vscode.window.showErrorMessage("Python 3.11+ not found. Install it or set 'agenticSdlc.pythonPath'."); }
  const term = vscode.window.createTerminal("Agentic SDLC setup");
  term.show();
  term.sendText(`"${python}" -m pip install -r "${path.join(engineDir(), "requirements.txt")}"`);
  vscode.window.showInformationMessage(
    "Verification uses each stack's own tools (npm, mvn/gradle, dotnet, pytest, go, cargo, PMD for Apex…). "
    + "Missing tools are reported as skipped. The Stack view shows what's installed.");
}
