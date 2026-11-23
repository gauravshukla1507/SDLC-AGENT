"""The orchestrator: plans the Salesforce delivery task graph, routes work between agents,
runs real quality gates (metadata checks, PMD Apex rules, org validation with Apex tests),
loops on failures, enforces a human approval gate and writes a full audit trace (trace.json)."""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import agents as A
from . import tools


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Orchestrator:
    def __init__(self, llm, workspace: Path, max_fix_iterations: int = 3, approver=None,
                 target_org: str | None = None, min_coverage: int = 75, log=print, on_event=None,
                 artifacts_dir: Path | None = None):
        self.llm = llm
        self.ws = Path(workspace)
        self.ws.mkdir(parents=True, exist_ok=True)
        self.max_fix = max_fix_iterations
        self.approver = approver or cli_approver
        self.target_org = target_org
        self.min_coverage = min_coverage
        self.log = log
        self.on_event = on_event
        self._lock = threading.Lock()
        run_id = uuid.uuid4().hex[:8]
        # Source goes into force-app/ in place; docs, manifest, CI and the trace go to a per-run folder
        # so an existing project's files outside force-app are never overwritten.
        self.artifacts = Path(artifacts_dir) if artifacts_dir else self.ws / ".agentic-sdlc" / "runs" / run_id
        self.trace: dict = {"run_id": run_id, "started": _now(), "platform": "salesforce",
                            "model": getattr(llm, "model", None), "workspace": str(self.ws.resolve()),
                            "artifacts_dir": self._relpath(self.artifacts), "events": []}
        self.files: dict[str, str] = {}
        self.run_files: set[str] = set()

    # ---------- helpers -------------------------------------------------
    def _relpath(self, p: Path) -> str:
        try:
            return Path(p).resolve().relative_to(self.ws.resolve()).as_posix()
        except ValueError:
            return str(p)

    def physical_path(self, rel: str) -> Path:
        return self.ws / rel if rel.startswith("force-app/") else self.artifacts / rel

    def _event(self, kind: str, **data) -> dict:
        with self._lock:
            ev = {"seq": len(self.trace["events"]) + 1, "time": _now(), "kind": kind, **data}
            self.trace["events"].append(ev)
            if self.on_event:
                self.on_event(ev)
        return ev

    def _write(self, files: dict[str, str]):
        for rel, content in files.items():
            if ".." in Path(rel).parts or Path(rel).is_absolute():
                raise ValueError(f"Agent tried to write outside workspace: {rel}")
            p = self.physical_path(rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            self.files[rel] = content
            if rel.startswith("force-app/"):
                self.run_files.add(rel)

    def _bundle(self, prefixes: tuple[str, ...]) -> str:
        return "\n".join(f'<file path="{k}">\n{v}</file>' for k, v in sorted(self.files.items())
                         if k.startswith(prefixes))

    def _call(self, agent: A.Agent, prompt: str, step: str) -> A.AgentOutput:
        self.log(f"  -> {agent.name}: {step}")
        t0 = time.time()
        out, res = agent.run(self.llm, prompt)
        self._write(out.files)
        self._event("agent", agent=agent.key, agent_name=agent.name, step=step,
                    summary=out.summary, files=sorted(out.files), file_contents=out.files,
                    file_paths={k: self._relpath(self.physical_path(k)) for k in out.files},
                    verdict=out.verdict, findings=out.findings,
                    duration_s=round(time.time() - t0, 2),
                    input_tokens=res.input_tokens, output_tokens=res.output_tokens)
        self.log(f"     {out.summary[:140]}")
        return out

    def _test_classes(self) -> list[str]:
        return sorted(Path(k).stem for k, v in self.files.items()
                      if k.endswith(".cls") and re.search(r"@IsTest", v, re.I))

    # ---------- quality gates ------------------------------------------
    def static_analysis(self) -> dict:
        t0 = time.time()
        meta = tools.metadata_check(self.ws, only=self.run_files)
        pmd = tools.pmd_check(self.ws, only=self.run_files)
        problems = [{"file": i["file"], "line": None, "rule": i["rule"], "message": i["message"], "engine": "metadata"}
                    for i in meta]
        problems += [{**v, "engine": "pmd"} for v in pmd["violations"]]
        lines = [f"Metadata checks: {len(meta)} issue(s)",
                 f"PMD Apex rules ({len([l for l in tools.RULESET.read_text().splitlines() if '<rule ref' in l])} rules): "
                 + (f"{len(pmd['violations'])} violation(s)" if pmd["available"] else pmd.get("note", "not available"))]
        lines += [f"{p['file']}:{p['line'] or '-'}  {p['rule']}  {p['message']}" for p in problems]
        passed = not problems and pmd["available"]
        files = sorted({p["file"] for p in problems})
        summary = "No violations" if not problems else f"{len(problems)} violation(s) in {len(files)} file(s)"
        result = {"tool": "static_analysis", "tool_name": "Static analysis (metadata + PMD Apex)",
                  "passed": passed, "summary": summary, "output": "\n".join(lines),
                  "violations": problems, "duration_s": round(time.time() - t0, 2)}
        self._event("tool", **result)
        self.log(f"  [static analysis] {summary}")
        return result

    def org_validation(self) -> dict:
        t0 = time.time()
        res = tools.org_validate(self.ws, self.target_org, self._test_classes(), self.min_coverage,
                                 run_files=sorted(self.run_files))
        result = {"tool": "org_validation", "tool_name": "Org validation (deploy + Apex tests)",
                  **res, "duration_s": round(time.time() - t0, 2)}
        self._event("tool", **result)
        self.log(f"  [org validation] {res['summary']}")
        return result

    def _fix(self, gate: dict, iteration: int):
        """Route a failing gate to the agent that owns the failing files."""
        bad = {v["file"] for v in gate.get("violations", [])}
        tests = {f for f in bad if re.search(r"@IsTest", self.files.get(f, ""), re.I)}
        target = A.TESTER if bad and bad == tests else A.DEVELOPER
        self._event("route", reason=f"{gate['tool']}_failed", to=target.key, to_name=target.name,
                    iteration=iteration, gate=gate["tool_name"])
        prompt = (self._bundle(("docs/", "force-app/")) +
                  f"\n\nQuality gate '{gate['tool_name']}' FAILED (iteration {iteration}). "
                  f"Fix the root cause and return full corrected files.\n\n{gate['output'][-6000:]}")
        self._call(target, prompt, f"Fix {gate['tool_name'].split(' (')[0].lower()} findings (iteration {iteration})")

    # ---------- the pipeline -------------------------------------------
    def run(self, story: str) -> dict:
        self.trace["story"] = story
        self.trace["target_org"] = self.target_org
        self.trace["scaffolded"] = tools.scaffold(self.ws)
        self.artifacts.mkdir(parents=True, exist_ok=True)
        (self.artifacts / "config").mkdir(exist_ok=True)
        (self.artifacts / "config" / "pmd-ruleset.xml").write_text(tools.RULESET.read_text(encoding="utf-8"),
                                                                   encoding="utf-8")
        context = tools.project_context(self.ws)
        self.trace["project_context"] = context
        self._event("plan", graph=[
            {"id": "requirements", "after": []},
            {"id": "architect", "after": ["requirements"]},
            {"id": "developer", "after": ["architect"]},
            {"id": "tester", "after": ["architect"]},
            {"id": "static_analysis", "after": ["developer", "tester"]},
            {"id": "org_validation", "after": ["static_analysis"]},
            {"id": "reviewer", "after": ["org_validation"]},
            {"id": "security", "after": ["org_validation"]},
            {"id": "human_gate", "after": ["reviewer", "security"]},
            {"id": "release", "after": ["human_gate"]},
        ])

        self.log("[1] Requirements")
        self._call(A.REQUIREMENTS, f"Business request from the client:\n\n{story}", "Write user story & acceptance criteria")
        self.log("[2] Solution design")
        self._call(A.ARCHITECT, self._bundle(("docs/",)) + "\n\n" + context, "Design data model, automation & security")

        self.log("[3] Build + tests in parallel")
        ctx = self._bundle(("docs/",)) + "\n\n" + context
        with ThreadPoolExecutor(max_workers=2) as pool:
            dev = pool.submit(self._call, A.DEVELOPER, ctx, "Build Apex, trigger & metadata")
            tst = pool.submit(self._call, A.TESTER, ctx, "Write Apex test class from acceptance criteria")
            dev.result(); tst.result()

        self.log("[4] Quality gates (feedback loop)")
        iteration = 0
        while True:
            gate = self.static_analysis()
            if gate["passed"]:
                gate = self.org_validation()
                if gate["passed"] or gate.get("skipped"):
                    break
            if iteration >= self.max_fix:
                self._event("escalate", reason=f"{gate['tool_name']} still failing after {self.max_fix} iterations")
                return self._finish("escalated")
            iteration += 1
            self._fix(gate, iteration)

        self.log("[5] Review + security in parallel")
        ctx = self._bundle(("docs/", "force-app/")) + "\n\nQuality gates: static analysis clean; " \
            f"org validation: {gate['summary']}"
        with ThreadPoolExecutor(max_workers=2) as pool:
            rv = pool.submit(self._call, A.REVIEWER, ctx, "Code review")
            sc = pool.submit(self._call, A.SECURITY, ctx, "Security review")
            review, sec = rv.result(), sc.result()
        if review.verdict != "APPROVE" or sec.verdict != "PASS":
            self._event("escalate", reason="review or security blocked", review=review.verdict, security=sec.verdict)
            return self._finish("escalated")

        self.log("[6] Human approval gate")
        decision = self.approver({"gates": gate["summary"], "review": review.verdict, "security": sec.verdict,
                                  "findings": review.findings + sec.findings})
        self._event("human_gate", **decision)
        if not decision["approved"]:
            return self._finish("rejected")

        self.log("[7] Release")
        approval = f"Approved by {decision['approver']} at {decision['time']}: {decision.get('comment', '')}"
        self._call(A.RELEASE, self._bundle(("docs/", "force-app/")) +
                   f"\n\nStatic analysis: clean (iterations needed: {iteration})\nOrg validation: {gate['summary']}\n"
                   f"Review: {review.verdict}\nSecurity: {sec.verdict}\n"
                   f"Findings: {json.dumps(review.findings + sec.findings)}\nHuman approval: {approval}",
                   "Prepare manifest, CI/CD pipeline & release notes")
        return self._finish("released")

    def _finish(self, status: str) -> dict:
        self.trace.update(status=status, finished=_now())
        self.artifacts.mkdir(parents=True, exist_ok=True)
        (self.artifacts / "trace.json").write_text(json.dumps(self.trace, indent=2), encoding="utf-8")
        if self.on_event:
            self.on_event({"kind": "finished", "status": status, "run_id": self.trace["run_id"],
                           "trace": self._relpath(self.artifacts / "trace.json")})
        self.log(f"Run {self.trace['run_id']} finished: {status.upper()}  (trace: {self.artifacts / 'trace.json'})")
        return self.trace


def cli_approver(context: dict) -> dict:
    print("\n=== HUMAN APPROVAL REQUIRED ===")
    print(f"Gates: {context['gates']} | Review: {context['review']} | Security: {context['security']}")
    for f in context["findings"]:
        print(f"  - [{f['severity']}] {f['text'][:160]}")
    name = input("Your name: ").strip() or "reviewer"
    ok = input("Approve release? [y/N]: ").strip().lower() == "y"
    comment = input("Comment (optional): ").strip()
    return {"approved": ok, "approver": name, "comment": comment, "time": _now()}


def stdin_approver(context: dict) -> dict:
    """Approval protocol for IDE integrations: emit a request line, read one JSON decision line from stdin."""
    import sys
    sys.stdout.write("@@APPROVAL_REQUEST " + json.dumps(context) + "\n")
    sys.stdout.flush()
    line = sys.stdin.readline()
    try:
        d = json.loads(line)
    except json.JSONDecodeError:
        d = {}
    return {"approved": bool(d.get("approved")), "approver": d.get("approver") or "unknown",
            "comment": d.get("comment", ""), "time": _now()}


def auto_approver(name: str, comment: str = "Auto-approved (demo mode)"):
    def _approve(context: dict) -> dict:
        return {"approved": True, "approver": name, "comment": comment, "time": _now()}
    return _approve
