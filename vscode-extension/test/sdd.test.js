// Integration test: spec-driven development on an existing product, driven through out/runner.js as the
// extension does: hires → spec review (request changes) → revised spec → plan review → release.
const assert = require("assert");
const fs = require("fs"); const os = require("os"); const path = require("path");
const { EngineRun, engineCall, engineJson } = require("../out/runner");

const env = { python: process.env.PYTHON || "python3", engineDir: path.join(__dirname, "..", "orchestrator"),
              playbook: "spec-driven", codebaseGraph: "auto" };
const fx = path.join(__dirname, "..", "..", "orchestrator", "tests", "fixtures", "python-sdd-existing-shop");
const ws = fs.mkdtempSync(path.join(os.tmpdir(), "agentic-sdd-"));
fs.cpSync(path.join(fx, "existing"), ws, { recursive: true });
const SPEC = "specs/001-promo-codes-at-checkout/";

function stream(args) {
  return new Promise(resolve => {
    const events = [];
    new EngineRun(env, { onEvent: e => events.push(e), onLog: () => {}, onExit: code => resolve({ code, events }) })
      .start(args);
  });
}
const run = ["--approver", "deferred", "--events", "-w", ws];
const pending = () => engineJson(env, ["approvals", "-w", ws]);

(async () => {
  let r = await stream(["run", "--requirements", path.join(fx, "story.md"), "--title", "Promo codes at checkout",
                        "--replay", path.join(fx, "recording.json"), ...run]);
  assert.strictEqual(r.code, 2);
  const ids = r.events[0].nodes.map(n => n.id);
  assert.ok(ids.includes("spec_review") && ids.includes("plan_review") && ids.includes("spec_coverage"), ids.join());
  assert.strictEqual(new Set(ids).size, ids.length, "one node per seat/gate");
  await engineCall(env, ["approve", "all", "--type", "hire", "--as", "Tester", "-w", ws]);

  r = await stream(["resume", ...run]);
  let p = await pending();
  assert.deepStrictEqual(p.map(a => a.type), ["spec"]);
  assert.ok(p[0].payload.excerpt.includes("AC-9") && p[0].payload.questions.length === 1);
  const noComment = await engineCall(env, ["approve", p[0].id, "--reject", "--as", "PO", "-w", ws]);
  assert.strictEqual(noComment.code, 2, "requesting changes needs a comment");
  await engineCall(env, ["approve", p[0].id, "--reject", "--as", "PO", "--comment", "Minimum uses the subtotal.", "-w", ws]);

  r = await stream(["resume", ...run]);
  assert.ok(r.events.some(e => e.kind === "route" && e.reason === "spec_changes_requested" && e.to === "requirements"));
  p = await pending();
  assert.strictEqual(p[0].type, "spec"); assert.strictEqual(p[0].payload.revision, 1);
  for (const type of ["spec", "plan", "release"]) {
    p = await pending();
    assert.strictEqual(p[0].type, type);
    await engineCall(env, ["approve", p[0].id, "--as", "Tester", "-w", ws]);
    r = await stream(["resume", ...run]);
  }
  assert.strictEqual(r.code, 0);
  const state = await engineJson(env, ["state", "-w", ws]);
  const proj = state.projects[0];
  assert.strictEqual(proj.status, "released"); assert.strictEqual(proj.playbook, "spec-driven");
  assert.ok(proj.documents.some(d => d.endsWith(path.join(SPEC, "spec.md"))), "spec listed in Documents");
  assert.ok(fs.readFileSync(path.join(ws, SPEC, "spec.md"), "utf8").includes("Revised after review"));
  const trace = JSON.parse(fs.readFileSync(proj.trace, "utf8"));
  assert.ok(trace.events.some(e => e.kind === "tool" && e.tool === "spec_coverage" && e.passed), "spec coverage passed");
  assert.ok(trace.events.some(e => e.kind === "human_gate" && e.review === "spec" && e.node === "spec_review"));
  fs.writeFileSync(path.join(__dirname, "last-sdd-trace.json"), JSON.stringify(trace));
  console.log(`PASS sdd: existing product → spec (changes requested, revised) → plan → tests first → release, ws=${ws}`);
})().catch(e => { console.error("FAIL", e); process.exit(1); });
