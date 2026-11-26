/**
 * Bridge to the bundled orchestrator (Python). No VS Code imports, so it can be tested with plain Node.
 *
 *  - EngineRun streams a long-running command (run / demo / resume) and parses its line protocol:
 *      @@EVENT <json>   orchestrator event (plan, agent, tool, route, human_gate, finished, error)
 *      anything else    human-readable log line
 *  - engineJson runs a read command with --json (state, approvals, stacks) and returns the parsed result.
 *  - engineCall runs a short command (approve, dashboard, demo-page, init) and returns its output.
 *
 * Approvals are asynchronous (deferred): a run stops when it needs a human, the IDE shows what is waiting,
 * the user decides, and the extension resumes the project. Nothing blocks on stdin.
 */
import { ChildProcessWithoutNullStreams, execFile, spawn } from "child_process";
import * as path from "path";

export interface EngineEnv {
  python: string;
  engineDir: string;
  apiKey?: string;
  pmdPath?: string;
  store?: string;
  targetOrg?: string;
}

export interface RunCallbacks {
  onEvent(ev: any): void;
  onLog(line: string): void;
  onExit(code: number | null): void;
}

export function engineEnvVars(e: EngineEnv): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUNBUFFERED: "1" };
  if (e.apiKey) { env.ANTHROPIC_API_KEY = e.apiKey; }
  if (e.pmdPath) { env.PMD_BIN = e.pmdPath; }
  if (e.store) { env.AGENTIC_STORE = e.store; }
  if (e.targetOrg) { env.SF_TARGET_ORG = e.targetOrg; }
  return env;
}

export function cliPath(e: EngineEnv): string {
  return path.join(e.engineDir, "agentic.py");
}

export class EngineRun {
  private proc?: ChildProcessWithoutNullStreams;
  private buffer = "";
  private exited = false;

  constructor(private readonly env: EngineEnv, private readonly cb: RunCallbacks) {}

  start(args: string[]): void {
    this.proc = spawn(this.env.python, ["-u", cliPath(this.env), ...args],
      { cwd: this.env.engineDir, env: engineEnvVars(this.env) });
    this.proc.stdout.setEncoding("utf8");
    this.proc.stderr.setEncoding("utf8");
    this.proc.stdout.on("data", (chunk: string) => this.consume(chunk));
    this.proc.stderr.on("data", (chunk: string) =>
      chunk.split(/\r?\n/).filter(l => l && !l.startsWith("Picked up ")).forEach(l => this.cb.onLog(l)));
    this.proc.on("error", err =>
      this.cb.onEvent({ kind: "error", message: `Could not start Python (${this.env.python}): ${err.message}` }));
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
    } else if (line.trim()) {
      this.cb.onLog(line);
    }
  }

  get isRunning(): boolean { return !!this.proc && !this.exited; }

  cancel(): void {
    if (this.proc && !this.exited) { this.proc.kill(); }
  }
}

export function engineCall(e: EngineEnv, args: string[], timeoutMs = 120000): Promise<{ code: number; stdout: string; stderr: string }> {
  return new Promise(resolve => {
    execFile(e.python, [cliPath(e), ...args], { cwd: e.engineDir, env: engineEnvVars(e), timeout: timeoutMs,
      maxBuffer: 64 * 1024 * 1024 }, (err, stdout, stderr) => {
      const code = err ? (typeof (err as any).code === "number" ? (err as any).code : 1) : 0;
      resolve({ code, stdout: String(stdout), stderr: String(stderr) });
    });
  });
}

export async function engineJson<T = any>(e: EngineEnv, args: string[]): Promise<T> {
  const r = await engineCall(e, [...args, "--json"]);
  if (r.code !== 0) { throw new Error(r.stderr.split("\n").filter(l => l && !l.startsWith("Picked up")).slice(-3).join(" ") || `exit ${r.code}`); }
  return JSON.parse(r.stdout) as T;
}
