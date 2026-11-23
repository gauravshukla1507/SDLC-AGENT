/**
 * Runs the bundled Python orchestration engine as a child process and translates its
 * line protocol into typed callbacks. No VS Code imports, so it can be tested with plain Node.
 *
 * Protocol (stdout, one item per line):
 *   @@EVENT <json>             orchestrator event (plan, agent, tool, route, human_gate, started, finished, error)
 *   @@APPROVAL_REQUEST <json>  the run is paused at the human gate; answer with respond()
 *   anything else              human-readable log line
 */
import { ChildProcessWithoutNullStreams, spawn } from "child_process";
import * as path from "path";

export interface RunOptions {
  python: string;
  engineDir: string;
  workspace: string;
  story: string;
  apiKey?: string;
  model?: string;
  targetOrg?: string;
  minCoverage?: number;
  maxFixIterations?: number;
  pmdPath?: string;
  replayFile?: string;
  recordFile?: string;
  autoApprove?: string;
  extraEnv?: Record<string, string>;
}

export interface RunCallbacks {
  onEvent(ev: any): void;
  onApprovalRequest(ctx: any): void;
  onLog(line: string): void;
  onExit(code: number | null): void;
}

export interface ApprovalDecision {
  approved: boolean;
  approver: string;
  comment?: string;
}

export function buildArgs(o: RunOptions, storyFile: string): string[] {
  const args = ["-u", path.join(o.engineDir, "run.py"),
    "--story-file", storyFile, "--workspace", o.workspace, "--events", "--approval", "stdin"];
  if (o.model) { args.push("--model", o.model); }
  if (o.targetOrg) { args.push("--target-org", o.targetOrg); }
  if (o.minCoverage) { args.push("--min-coverage", String(o.minCoverage)); }
  if (o.maxFixIterations) { args.push("--max-fix-iterations", String(o.maxFixIterations)); }
  if (o.replayFile) { args.push("--replay", o.replayFile); }
  if (o.recordFile && !o.replayFile) { args.push("--record", o.recordFile); }
  if (o.autoApprove) { args.push("--auto-approve", o.autoApprove); }
  return args;
}

export class EngineRun {
  private proc?: ChildProcessWithoutNullStreams;
  private buffer = "";
  private awaitingApproval = false;
  private exited = false;

  constructor(private readonly opts: RunOptions, private readonly cb: RunCallbacks) {}

  start(storyFile: string): void {
    const env: NodeJS.ProcessEnv = { ...process.env, PYTHONIOENCODING: "utf-8", ...(this.opts.extraEnv || {}) };
    if (this.opts.apiKey) { env.ANTHROPIC_API_KEY = this.opts.apiKey; }
    if (this.opts.pmdPath) { env.PMD_BIN = this.opts.pmdPath; }
    if (this.opts.targetOrg) { env.SF_TARGET_ORG = this.opts.targetOrg; }
    this.proc = spawn(this.opts.python, buildArgs(this.opts, storyFile), { cwd: this.opts.engineDir, env });
    this.proc.stdout.setEncoding("utf8");
    this.proc.stderr.setEncoding("utf8");
    this.proc.stdout.on("data", (chunk: string) => this.consume(chunk));
    this.proc.stderr.on("data", (chunk: string) =>
      chunk.split(/\r?\n/).filter(l => l && !l.startsWith("Picked up ")).forEach(l => this.cb.onLog(l)));
    this.proc.on("error", err => {
      this.cb.onEvent({ kind: "error", message: `Could not start Python (${this.opts.python}): ${err.message}` });
    });
    this.proc.on("close", code => {
      if (this.buffer) { this.handleLine(this.buffer); this.buffer = ""; }
      this.exited = true;
      this.cb.onExit(code);
    });
  }

  private consume(chunk: string): void {
    this.buffer += chunk;
    let idx: number;
    while ((idx = this.buffer.indexOf("\n")) >= 0) {
      const line = this.buffer.slice(0, idx).replace(/\r$/, "");
      this.buffer = this.buffer.slice(idx + 1);
      this.handleLine(line);
    }
  }

  private handleLine(line: string): void {
    if (line.startsWith("@@EVENT ")) {
      try { this.cb.onEvent(JSON.parse(line.slice(8))); } catch { this.cb.onLog(line); }
    } else if (line.startsWith("@@APPROVAL_REQUEST ")) {
      this.awaitingApproval = true;
      try { this.cb.onApprovalRequest(JSON.parse(line.slice(19))); } catch { this.cb.onApprovalRequest({}); }
    } else if (line.trim()) {
      this.cb.onLog(line);
    }
  }

  get isAwaitingApproval(): boolean { return this.awaitingApproval; }
  get isRunning(): boolean { return !!this.proc && !this.exited; }

  /** Answer the human gate. Returns false if no approval was pending (e.g. already answered). */
  respond(decision: ApprovalDecision): boolean {
    if (!this.proc || !this.awaitingApproval) { return false; }
    this.awaitingApproval = false;
    this.proc.stdin.write(JSON.stringify(decision) + "\n");
    return true;
  }

  cancel(): void {
    if (this.proc && !this.exited) { this.proc.kill(); }
  }
}
