"""The orchestrator: plans the SDLC task graph, routes work between agents,
runs real tools (pytest), loops on failures, enforces a human approval gate
and writes a full audit trace (trace.json)."""
from __future__ import annotations

import json
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import agents as A


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Orchestrator:
    def __init__(self, llm, workspace: Path, max_fix_iterations: int = 3,
                 approver=None, log=print):
        self.llm = llm
        self.ws = Path(workspace)
        self.ws.mkdir(parents=True, exist_ok=True)
        self.max_fix = max_fix_iterations
        self.approver = approver or cli_approver
        self.log = log
        self.trace: dict = {"run_id": uuid.uuid4().hex[:8], "started": _now(),
                            "model": getattr(llm, "model", None), "events": []}
        self.files: dict[str, str] = {}

    # ---------- helpers -------------------------------------------------
    def _event(self, kind: str, **data) -> dict:
        ev = {"seq": len(self.trace["events"]) + 1, "time": _now(), "kind": kind, **data}
        self.trace["events"].append(ev)
        return ev

    def _write(self, files: dict[str, str]):
        for rel, content in files.items():
            if ".." in Path(rel).parts or Path(rel).is_absolute():
                raise ValueError(f"Agent tried to write outside workspace: {rel}")
            p = self.ws / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            self.files[rel] = content

    def _bundle(self, prefixes: tuple[str, ...]) -> str:
        parts = [f'<file path="{k}">\n{v}</file>' for k, v in sorted(self.files.items())
                 if k.startswith(prefixes)]
        return "\n".join(parts)

    def _call(self, agent: A.Agent, prompt: str, step: str) -> A.AgentOutput:
        self.log(f"  -> {agent.name}: {step}")
        t0 = time.time()
        out, res = agent.run(self.llm, prompt)
        self._write(out.files)
        self._event("agent", agent=agent.key, agent_name=agent.name, step=step,
                    summary=out.summary, files=sorted(out.files), file_contents=out.files,
                    verdict=out.verdict, findings=out.findings,
                    duration_s=round(time.time() - t0, 2),
                    input_tokens=res.input_tokens, output_tokens=res.output_tokens)
        self.log(f"     {out.summary[:140]}")
        return out

    def run_tests(self) -> dict:
        t0 = time.time()
        ini = self.ws / "pytest.ini"
        if not ini.exists():  # code lives in src/, tests in tests/
            ini.write_text("[pytest]\npythonpath = src\ntestpaths = tests\n", encoding="utf-8")
        proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                               "--tb=short", "tests"], cwd=self.ws, capture_output=True, text=True)
        output = (proc.stdout + proc.stderr).strip()
        summary = output.splitlines()[-1] if output else ""
        result = {"passed": proc.returncode == 0, "summary": summary, "output": output,
                  "duration_s": round(time.time() - t0, 2)}
        self._event("tool", tool="pytest", **result)
        self.log(f"  [pytest] {summary}")
        return result

    # ---------- the pipeline -------------------------------------------
    def run(self, story: str) -> dict:
        self.trace["story"] = story
        self._event("plan", graph=[
            {"id": "requirements", "after": []},
            {"id": "architect", "after": ["requirements"]},
            {"id": "developer", "after": ["architect"]},
            {"id": "tester", "after": ["architect"]},
            {"id": "pytest", "after": ["developer", "tester"]},
            {"id": "reviewer", "after": ["pytest"]},
            {"id": "security", "after": ["pytest"]},
            {"id": "human_gate", "after": ["reviewer", "security"]},
            {"id": "release", "after": ["human_gate"]},
        ])

        self.log("[1] Requirements")
        self._call(A.REQUIREMENTS, f"Business request from the client:\n\n{story}", "Write user story & acceptance criteria")

        self.log("[2] Architecture")
        self._call(A.ARCHITECT, self._bundle(("docs/",)), "Design module & interface")

        self.log("[3] Build + tests in parallel")
        ctx = self._bundle(("docs/",))
        with ThreadPoolExecutor(max_workers=2) as pool:
            dev = pool.submit(self._call, A.DEVELOPER, ctx, "Implement design")
            tst = pool.submit(self._call, A.TESTER, ctx, "Write tests from acceptance criteria")
            dev.result(); tst.result()

        self.log("[4] Verify (feedback loop)")
        result = self.run_tests()
        it = 0
        while not result["passed"] and it < self.max_fix:
            it += 1
            self._event("route", reason="tests_failed", to="developer", iteration=it)
            prompt = (self._bundle(("docs/", "src/", "tests/")) +
                      f"\n\nThe test suite FAILED (iteration {it}). Fix the implementation.\n\n{result['output'][-6000:]}")
            self._call(A.DEVELOPER, prompt, f"Fix failing tests (iteration {it})")
            result = self.run_tests()
        if not result["passed"]:
            self._event("escalate", reason=f"tests still failing after {self.max_fix} iterations")
            return self._finish("escalated")

        self.log("[5] Review + security in parallel")
        ctx = self._bundle(("docs/", "src/", "tests/")) + f"\n\nTest result: {result['summary']}"
        with ThreadPoolExecutor(max_workers=2) as pool:
            rv = pool.submit(self._call, A.REVIEWER, ctx, "Code review")
            sc = pool.submit(self._call, A.SECURITY, ctx, "Security review")
            review, sec = rv.result(), sc.result()
        if review.verdict != "APPROVE" or sec.verdict != "PASS":
            self._event("escalate", reason="review or security blocked", review=review.verdict, security=sec.verdict)
            return self._finish("escalated")

        self.log("[6] Human approval gate")
        decision = self.approver({"tests": result["summary"], "review": review.verdict,
                                  "security": sec.verdict,
                                  "findings": review.findings + sec.findings})
        self._event("human_gate", **decision)
        if not decision["approved"]:
            return self._finish("rejected")

        self.log("[7] Release")
        approval = f"Approved by {decision['approver']} at {decision['time']}: {decision.get('comment', '')}"
        self._call(A.RELEASE, self._bundle(("docs/", "src/", "tests/")) +
                   f"\n\nTest result: {result['summary']}\nReview: {review.verdict}\nSecurity: {sec.verdict}\n"
                   f"Findings: {json.dumps(review.findings + sec.findings)}\nHuman approval: {approval}",
                   "Prepare CI pipeline & release notes")
        return self._finish("released")

    def _finish(self, status: str) -> dict:
        self.trace.update(status=status, finished=_now())
        (self.ws / "trace.json").write_text(json.dumps(self.trace, indent=2), encoding="utf-8")
        self.log(f"Run {self.trace['run_id']} finished: {status.upper()}  (trace: {self.ws / 'trace.json'})")
        return self.trace


def cli_approver(context: dict) -> dict:
    print("\n=== HUMAN APPROVAL REQUIRED ===")
    print(f"Tests: {context['tests']} | Review: {context['review']} | Security: {context['security']}")
    for f in context["findings"]:
        print(f"  - [{f['severity']}] {f['text'][:160]}")
    name = input("Your name: ").strip() or "reviewer"
    ok = input("Approve release? [y/N]: ").strip().lower() == "y"
    comment = input("Comment (optional): ").strip()
    return {"approved": ok, "approver": name, "comment": comment, "time": _now()}


def auto_approver(name: str, comment: str = "Auto-approved (demo mode)"):
    def _approve(context: dict) -> dict:
        return {"approved": True, "approver": name, "comment": comment, "time": _now()}
    return _approve
