// Integration test: drives the bundled orchestrator through out/runner.js exactly as the extension does,
// including asynchronous approvals (hire -> resume -> release -> resume).
const assert = require("assert");
const fs = require("fs"); const os = require("os"); const path = require("path");
const { EngineRun, engineCall, engineJson } = require("../out/runner");

const env = { python: process.env.PYTHON || "python3", engineDir: path.join(__dirname, "..", "orchestrator") };
const ws = fs.mkdtempSync(path.join(os.tmpdir(), "agentic-runner-"));
const SAMPLE = process.env.SAMPLE || "node";

function stream(args) {
  return new Promise(resolve => {
    const events = [], logs = [];
    new EngineRun(env, { onEvent: e => events.push(e), onLog: l => logs.push(l),
      onExit: code => resolve({ code, events, logs }) }).start(args);
  });
}

(async () => {
  const run = ["--approver", "deferred", "--events"];
  let r = await stream(["demo", "--sample", SAMPLE, "-w", ws, ...run]);
  assert.strictEqual(r.code, 2, "waits for hiring\n" + r.logs.join("\n"));
  assert.strictEqual(r.events[0].kind, "plan");
  assert.ok(r.events[0].nodes.length >= 9, "plan carries pipeline nodes for the panel");
  let pending = await engineJson(env, ["approvals", "-w", ws]);
  assert.ok(pending.length === 7 && pending.every(a => a.type === "hire"));
  assert.strictEqual((await engineCall(env, ["approve", "all", "--type", "hire", "--as", "Tester", "-w", ws])).code, 0);

  r = await stream(["resume", "-w", ws, ...run]);
  assert.strictEqual(r.code, 2, "waits for release");
  const verify = r.events.filter(e => e.kind === "tool" && e.tool === "verify").map(e => e.passed);
  assert.deepStrictEqual(verify, [false, true], "verification fails once, agent fixes, passes");
  [pending] = [await engineJson(env, ["approvals", "-w", ws])];
  assert.strictEqual(pending.length, 1); assert.strictEqual(pending[0].type, "release");
  await engineCall(env, ["approve", pending[0].id, "--as", "Tester", "--comment", "LGTM", "-w", ws]);

  r = await stream(["resume", "-w", ws, ...run]);
  assert.strictEqual(r.code, 0);
  assert.strictEqual(r.events[r.events.length - 1].status, "released");
  const state = await engineJson(env, ["state", "-w", ws]);
  assert.strictEqual(state.projects[0].status, "released");
  assert.ok(state.projects[0].stacks.length, "stack detected");
  fs.writeFileSync(path.join(__dirname, "last-trace.json"), fs.readFileSync(state.projects[0].trace));
  console.log(`PASS runner: ${SAMPLE} demo with hire + release approvals, stacks=${state.projects[0].stacks}, ws=${ws}`);
})().catch(e => { console.error("FAIL", e); process.exit(1); });
