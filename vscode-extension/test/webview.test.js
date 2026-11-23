// Renders media/panel.html in Chromium with a stubbed VS Code webview API and replays the
// recorded events as live messages, including the approval round-trip.
const { chromium } = require("playwright");
const fs = require("fs"); const path = require("path");
const events = JSON.parse(fs.readFileSync(path.join(__dirname, "last-events.json"), "utf8"));
const story = fs.readFileSync(path.join(__dirname, "..", "engine", "samples", "closed_won_onboarding_story.md"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "..", "media", "panel.html"), "utf8")
  .replace(/{{NONCE}}/g, "n").replace(/{{CSP_SOURCE}}/g, "'self'")
  .replace('<script nonce="n">', '<script nonce="n">window.__sent=[];window.acquireVsCodeApi=()=>({postMessage:m=>window.__sent.push(m)});');
(async () => {
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1280, height: 900 } });
  const errs = []; p.on("pageerror", e => errs.push(e.message));
  await p.setContent(html); await p.waitForTimeout(200);
  const send = m => p.evaluate(m => window.postMessage(m, "*"), m);
  await send({ type: "reset", story, mode: "live", branch: "agentic/closed-won-onboarding-202611231015" });
  const gateIdx = events.findIndex(e => e.kind === "human_gate");
  for (const ev of events.slice(0, gateIdx)) { await send({ type: "event", ev }); }
  await p.waitForTimeout(150); await p.screenshot({ path: "test/w-before-approval.png" });
  await send({ type: "approval", ctx: { gates: "Skipped: no Salesforce org connected", review: "APPROVE", security: "PASS",
    findings: [{ severity: "medium", text: "Add 'Sales Handoff' to Case Origin" }] } });
  await p.waitForTimeout(150); await p.screenshot({ path: "test/w-approval.png" });
  await p.fill("#apComment", "Approved for sandbox"); await p.click("#apYes");
  const sent = await p.evaluate(() => window.__sent);
  if (!sent.some(m => m.type === "approve" && m.approved && m.comment === "Approved for sandbox")) throw new Error("approve not posted");
  await send({ type: "approvalDone" });
  for (const ev of events.slice(gateIdx)) { await send({ type: "event", ev }); }
  await p.waitForTimeout(200); await p.screenshot({ path: "test/w-finished.png" });
  await p.click("text=Open force-app/main/default/classes/OnboardingService.cls in editor ↗").catch(()=>{});
  // click a violation file link on the failing gate
  await p.click("#logBody tr:nth-child(6)"); await p.waitForTimeout(100);
  await p.click("[data-vfile]");
  const sent2 = await p.evaluate(() => window.__sent);
  if (!sent2.some(m => m.type === "openFile")) throw new Error("openFile not posted");
  await p.screenshot({ path: "test/w-gate.png" });
  console.log(errs.length ? "PAGE ERRORS " + errs.join("; ") : "PASS webview: live events, approval round-trip, open-file messages", sent2.map(m=>m.type).join(","));
  await b.close(); if (errs.length) process.exit(1);
})();
