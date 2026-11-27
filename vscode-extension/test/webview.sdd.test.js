// Renders the spec-driven flow in media/panel.html: the spec review card (open questions, excerpt, "Request
// changes" needs a comment), a revised spec, the plan review and the spec-coverage gate. Uses the trace written
// by test/sdd.test.js.
const { chromium } = require("playwright");
const fs = require("fs"); const path = require("path");
const trace = JSON.parse(fs.readFileSync(path.join(__dirname, "last-sdd-trace.json"), "utf8"));
const html = fs.readFileSync(path.join(__dirname, "..", "media", "panel.html"), "utf8")
  .replace(/{{NONCE}}/g, "n").replace(/{{CSP_SOURCE}}/g, "'self'")
  .replace('<script nonce="n">', '<script nonce="n">window.__sent=[];window.acquireVsCodeApi=()=>({postMessage:m=>window.__sent.push(m)});');
const spec = fs.readFileSync(path.join(__dirname, "..", "..", "orchestrator", "tests", "fixtures", "python-sdd-existing-shop",
  "recording.json"), "utf8");
const draft = JSON.parse(spec).responses.find(r => r.agent === "requirements").response.split('">\n')[1].split("</file>")[0];
(async () => {
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1280, height: 1000 } });
  const errs = []; p.on("pageerror", e => errs.push(e.message));
  await p.setContent(html); await p.waitForTimeout(200);
  const send = m => p.evaluate(m => window.postMessage(m, "*"), m);
  await send({ type: "reset", story: trace.story, title: trace.title, mode: "live" });
  const firstReview = trace.events.findIndex(e => e.kind === "route" || e.kind === "human_gate");
  for (const ev of trace.events.slice(0, firstReview)) await send({ type: "event", ev });
  const doc = "specs/001-promo-codes-at-checkout/spec.md";
  await send({ type: "approvals", items: [{ id: "s1", type: "spec", subject: `Review spec: '${trace.title}'`, payload: {
    document: doc, document_path: "/repo/" + doc, author: "Requirements Agent", revision: 0,
    summary: "Spec with 2 user stories and 9 Given/When/Then acceptance criteria; one open question.",
    questions: draft.split("\n").filter(l => l.includes("NEEDS CLARIFICATION")).map(l => l.trim()), excerpt: draft } }] });
  await p.waitForTimeout(200); await p.screenshot({ path: "test/w-spec-review.png" });
  await p.click('[data-no="s1"]');                               // no comment: blocked, hint shown
  if (await p.isHidden('[data-hint="s1"]')) throw new Error("hint not shown for request changes without comment");
  await p.fill('[data-c="s1"]', "Minimum uses the subtotal before discounts."); await p.click('[data-no="s1"]');
  await p.click('button[data-open]');
  await send({ type: "approvals", items: [] }); await send({ type: "continue" });
  for (const ev of trace.events.slice(firstReview)) await send({ type: "event", ev });
  await send({ type: "event", ev: { kind: "finished", status: "released" } });
  await p.waitForTimeout(200); await p.screenshot({ path: "test/w-sdd-finished.png", fullPage: true });
  const rows = await p.$$eval("#logBody tr", r => r.map(x => x.textContent));
  const sent = await p.evaluate(() => window.__sent);
  const decides = sent.filter(m => m.type === "decide");
  if (decides.length !== 1 || decides[0].approved || !decides[0].comment) throw new Error("request changes: " + JSON.stringify(decides));
  if (!sent.some(m => m.type === "openFile" && m.path === "/repo/" + doc)) throw new Error("open spec not posted");
  for (const want of ["Spec review", "Plan review", "Spec coverage", "Map codebase"]) {
    if (!rows.some(r => r.includes(want))) throw new Error(`log has no '${want}' row`);
  }
  const nodes = await p.$$eval("#pipe .node .nm", n => n.map(x => x.textContent));
  console.log(errs.length ? "PAGE ERRORS " + errs.join("; ") : `PASS webview (spec-driven): nodes=${nodes.join(", ")}`);
  await b.close(); if (errs.length) process.exit(1);
})();
