// Renders media/panel.html in Chromium with a stubbed webview API: live events, hiring and release
// approval cards, decisions posted back to the extension, and file-open messages.
const { chromium } = require("playwright");
const fs = require("fs"); const path = require("path");
const trace = JSON.parse(fs.readFileSync(path.join(__dirname, "last-trace.json"), "utf8"));
const html = fs.readFileSync(path.join(__dirname, "..", "media", "panel.html"), "utf8")
  .replace(/{{NONCE}}/g, "n").replace(/{{CSP_SOURCE}}/g, "'self'")
  .replace('<script nonce="n">', '<script nonce="n">window.__sent=[];window.acquireVsCodeApi=()=>({postMessage:m=>window.__sent.push(m)});');
(async () => {
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1280, height: 900 } });
  const errs = []; p.on("pageerror", e => errs.push(e.message));
  await p.setContent(html); await p.waitForTimeout(200);
  const send = m => p.evaluate(m => window.postMessage(m, "*"), m);
  await send({ type: "reset", story: trace.story, title: trace.title, mode: "replay" });
  const hires = ["architect", "developer"].map((k, i) => ({ id: "h" + i, type: "hire", subject: `Hire ${k} Agent`, payload: { agent_key: k, reports_to: "director", budget_usd: 2, skills: ["team-conventions"] } }));
  await send({ type: "event", ev: trace.events[0] });
  await send({ type: "approvals", items: hires });
  await p.waitForTimeout(150); await p.screenshot({ path: "test/w-hire.png" });
  await p.click("#hireAll");
  const gateIdx = trace.events.findIndex(e => e.kind === "human_gate");
  await send({ type: "approvals", items: [] }); await send({ type: "continue" });
  for (const ev of trace.events.slice(1, gateIdx)) await send({ type: "event", ev });
  await send({ type: "approvals", items: [{ id: "r1", type: "release", subject: `Release '${trace.title}'`, payload: {
    gates: "All blocking checks passed (tests: pass); Skipped: no environment checks configured",
    verdicts: { reviewer: "APPROVE", security: "PASS" }, spend_usd: 0.11,
    findings: [{ severity: "low", text: "Move fee terms to configuration" }] } }] });
  await p.waitForTimeout(150); await p.screenshot({ path: "test/w-release.png" });
  await p.fill('[data-c="r1"]', "Approved for sandbox"); await p.click('[data-yes="r1"]');
  await send({ type: "approvals", items: [] }); await send({ type: "continue" });
  for (const ev of trace.events.slice(gateIdx)) await send({ type: "event", ev });
  await send({ type: "event", ev: { kind: "finished", status: "released" } });
  await p.waitForTimeout(200); await p.screenshot({ path: "test/w-finished.png", fullPage: true });
  await p.click("#logBody tr:nth-child(6)"); await p.waitForTimeout(100); await p.screenshot({ path: "test/w-gate.png" });
  const sent = await p.evaluate(() => window.__sent);
  if (!sent.some(m => m.type === "approveHires")) throw new Error("approveHires not posted");
  if (!sent.some(m => m.type === "decide" && m.id === "r1" && m.approved && m.comment === "Approved for sandbox")) throw new Error("decide not posted");
  const nodes = await p.$$eval("#pipe .node .nm", n => n.map(x => x.textContent));
  console.log(errs.length ? "PAGE ERRORS " + errs.join("; ") : `PASS webview: nodes=${nodes.length} (${nodes.slice(0, 3).join(", ")}…), messages=${[...new Set(sent.map(m => m.type))].join(",")}`);
  await b.close(); if (errs.length) process.exit(1);
})();
