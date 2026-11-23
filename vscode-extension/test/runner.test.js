// Integration test: drives the bundled engine through out/runner.js exactly as the extension does.
const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { EngineRun } = require("../out/runner");

const engineDir = path.join(__dirname, "..", "engine");
const ws = fs.mkdtempSync(path.join(os.tmpdir(), "agentic-test-"));
const storyFile = path.join(engineDir, "samples", "closed_won_onboarding_story.md");
const events = [], logs = [];
let approvalCtx = null;

const run = new EngineRun({
  python: process.env.PYTHON || "python3", engineDir, workspace: ws, story: "",
  replayFile: path.join(engineDir, "samples", "demo_recording.json"),
}, {
  onEvent: ev => events.push(ev),
  onLog: l => logs.push(l),
  onApprovalRequest: ctx => {
    approvalCtx = ctx;
    assert.ok(run.isAwaitingApproval);
    setTimeout(() => assert.ok(run.respond({ approved: true, approver: "Test Lead", comment: "LGTM" })), 50);
  },
  onExit: code => {
    try {
      assert.strictEqual(code, 0, "engine exit code\n" + logs.join("\n"));
      const kinds = events.map(e => e.kind);
      assert.strictEqual(kinds[0], "started");
      assert.strictEqual(kinds[kinds.length - 1], "finished");
      assert.strictEqual(events[events.length - 1].status, "released");
      const gates = events.filter(e => e.kind === "tool" && e.tool === "static_analysis");
      assert.deepStrictEqual(gates.map(g => g.passed), [false, true], "PMD gate should fail then pass");
      assert.ok(gates[0].violations.some(v => v.rule === "OperationWithLimitsInLoop"));
      assert.ok(approvalCtx && approvalCtx.review === "APPROVE");
      const gate = events.find(e => e.kind === "human_gate");
      assert.strictEqual(gate.approver, "Test Lead");
      assert.ok(fs.existsSync(path.join(ws, "force-app/main/default/classes/OnboardingService.cls")));
      const trace = path.join(ws, events[events.length - 1].trace);
      assert.ok(fs.existsSync(trace), "trace written");
      assert.ok(!run.respond({ approved: true, approver: "x" }), "no second approval");
      fs.writeFileSync(path.join(__dirname, "last-events.json"), JSON.stringify(events));
      console.log(`PASS runner protocol: ${events.length} events, ${logs.length} log lines, workspace ${ws}`);
    } catch (e) { console.error("FAIL", e.message); process.exit(1); }
  },
});
run.start(storyFile);
