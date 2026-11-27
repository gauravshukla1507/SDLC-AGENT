"""The universal delivery playbook: one team and one flow for every technology.

Nothing here is specific to a stack. The roles are technology-neutral; the stack profile, the stack
skills and the verification commands are discovered from the repository at run time (see stacks/).
A Maven repo gets `mvn verify`, a Node repo its own npm scripts, an Apex repo PMD rules and org validation,
a polyglot repo all of them, without anyone choosing a stack.
"""
from __future__ import annotations

from pathlib import Path

import stacks
from core.agents_contract import role
from core.playbook import Playbook, Step, register

GENERAL = """Engineering principles (apply them in the idiom of the detected stack):
- Follow the stack profile and the stack skills given to you; use the stack's standard layout, build tool and
  test framework so the orchestrator can verify the work automatically.
- Reuse and extend what already exists (see the existing-project context); never duplicate a component.
- Small, readable units; explicit types where the language supports them; no hard-coded secrets or ids.
- Enforce security in the data layer (authorisation, input validation, least privilege).
- Design for production volumes and limits (bulk operations, pagination, timeouts, governor/rate limits)."""

ROLES = {
    "requirements": role("requirements", "Requirements Agent", "Story, acceptance criteria, scope", "Business Analyst",
        """- Turn the requirements document into an epic and user stories (US-1, US-2 ...), each "As a / I want /
  So that", with numbered, testable acceptance criteria (AC-1, AC-2 ...) in Given/When/Then form.
- Stay faithful to the document: keep its IDs, rules, numbers and boundaries exactly (if it already has
  acceptance criteria, reuse them verbatim and assign each to a story). Never invent features it doesn't ask for.
- Add a traceability table (document rule/requirement -> story -> acceptance criteria), then assumptions,
  out-of-scope items and open questions for anything ambiguous or missing.
- Capture impacted components/data, personas and access, and volumes.
- Write docs/requirements.md.""", GENERAL),
    "architect": role("architect", "Architect Agent", "Design, interfaces, file plan", "Solution Architect",
        """- Use the detected stack. For a greenfield project, choose the stack that fits the request and state it,
  with its build/test tooling and standard layout.
- Specify the design: components, interfaces/signatures, data model, security, limits, test strategy.
- List the exact files the developer and the tester must create so they can work in parallel.
- Map every acceptance criterion to the design. Write docs/design.md.""", GENERAL),
    "developer": role("developer", "Developer Agent", "Implements the design", "Senior Software Engineer",
        """- Implement the design in the detected stack, including build/config files and metadata the stack needs.
  Do not write tests (the Test Agent owns them).
- When given failing verification output, fix the root cause and return the full corrected files.""", GENERAL),
    "tester": role("tester", "Test Agent", "Tests from acceptance criteria", "Test Engineer",
        """- Write automated tests in the stack's standard test framework, derived ONLY from the requirements and the
  design (you do not see the implementation). At least one test per acceptance criterion; reference AC ids.
- Include boundary, negative and volume cases. When failures are in your tests, fix the tests.""", GENERAL),
    "reviewer": role("reviewer", "Code Review Agent", "Correctness & maintainability", "Technical Lead",
        """- Review code, configuration and tests against the requirements, the design and the stack conventions.
- Report issues as <finding severity="low|medium|high">...</finding>.
- End with <verdict>APPROVE</verdict> or <verdict>CHANGES_REQUESTED</verdict> (only for high severity).
- Do not output files.""", GENERAL),
    "security": role("security", "Security Agent", "Vulnerabilities & data exposure", "Application Security Engineer",
        """- Review authorisation, input validation, injection, secrets, data exposure, dependency and abuse risks
  in the idiom of the stack. Report each as <finding severity="low|medium|high">...</finding>.
- End with <verdict>PASS</verdict> or <verdict>FAIL</verdict> (only for high severity). Do not output files.""",
        GENERAL),
    "release": role("release", "Release Agent", "CI/CD, manifest, release notes", "DevOps / Release Engineer",
        """- Produce a CI workflow (e.g. .github/workflows/ci.yml) that runs the same verification commands the
  orchestrator ran for this stack, plus any deployment manifest the stack needs.
- Produce RELEASE_NOTES.md: what changed, acceptance criteria covered, verification results (including skipped
  checks and why), review and security outcomes, deployment steps and the human approval record.""", GENERAL),
}

ARTIFACT_PREFIXES = ("docs/", ".github/", "manifest/", "RELEASE_NOTES.md")


@register
class UniversalDelivery(Playbook):
    name = "universal"
    description = "Any stack: detects the technology, its conventions and its verification tools from the repo"
    roles = ROLES

    def steps(self, ws: Path | None = None) -> list[Step]:
        return [
            Step("requirements", "Write user story & acceptance criteria", "agent", "requirements", with_context=True),
            Step("design", "Design solution & file plan", "agent", "architect", deps=["requirements"],
                 inputs=("docs/",), with_context=True),
            Step("build", "Implement the design", "agent", "developer", deps=["design"], inputs=("docs/",),
                 with_context=True),
            Step("tests", "Write tests from acceptance criteria", "agent", "tester", deps=["design"],
                 inputs=("docs/",), with_context=True),
            Step("verify", "Verify (build, static analysis, tests)", "gate", deps=["build", "tests"]),
            Step("environment", "Environment validation", "gate", deps=["verify"],
                 gateway_action="environment.validate"),
            Step("review", "Code review", "agent", "reviewer", deps=["environment"], inputs=("*",), with_context=True),
            Step("security", "Security review", "agent", "security", deps=["environment"], inputs=("*",),
                 with_context=True),
            Step("release_approval", "Release approval", "approval", deps=["review", "security"]),
            Step("release", "Prepare CI pipeline & release notes", "agent", "release", deps=["release_approval"],
                 inputs=("*",), with_context=True),
        ]

    # ------------------------------------------------------------ files
    def is_source(self, rel: str) -> bool:
        return not rel.startswith(ARTIFACT_PREFIXES)

    def physical_path(self, rel: str, ws: Path, artifacts: Path) -> Path:
        return ws / rel if self.is_source(rel) else artifacts / rel

    def fix_inputs(self) -> tuple[str, ...]:
        return ("*",)

    # ------------------------------------------------------------ stack knowledge
    def project_context(self, ws: Path) -> str:
        return stacks.project_context(ws)

    def stack_skills(self, ws: Path) -> list[str]:
        return [s.skill for s in stacks.detect(ws) if s.skill]

    def detected(self, ws: Path) -> list[str]:
        return [s.name for s in stacks.detect(ws)]

    # ------------------------------------------------------------ gates
    def run_gate(self, key, ws, run_files, files, target_org=None, min_coverage=75, require_verification=False, **_):
        if key == "verify":
            detected, checks = stacks.checks_for(ws, "verify")
        elif key == "environment":
            detected, checks = stacks.checks_for(ws, "environment")
        else:
            raise KeyError(key)

        results = [stacks.run_check(c, ws, run_files, target_org=target_org, min_coverage=min_coverage)
                   for c in checks]
        blocking_failed = [r for r in results if r["blocking"] and r.get("passed") is False]
        blocking_ran = [r for r in results if r["blocking"] and not r.get("skipped")]
        advisory_failed = [r for r in results if not r["blocking"] and r.get("passed") is False]
        stack_names = ", ".join(s.name for s in detected) or "none detected"

        def mark(r):
            return "skipped" if r.get("skipped") else "pass" if r.get("passed") else \
                ("FAIL" if r["blocking"] else "warn")
        lines = [f"Stacks: {stack_names}"]
        for r in results:
            lines.append(f"\n== {r['name']} [{r['stack']}, {'blocking' if r['blocking'] else 'advisory'}]: "
                         f"{mark(r)} - {r['summary']}")
            if r.get("passed") is not True and r.get("output"):
                lines.append(r["output"][-4000:])
        summary = "; ".join(f"{r['name']}: {mark(r)}" for r in results) or "no checks"

        if blocking_failed:
            return {"passed": False, "summary": f"{len(blocking_failed)} blocking check(s) failed ({summary})",
                    "output": "\n".join(lines), "violations": [v for r in blocking_failed for v in r["violations"]],
                    "checks": [_slim(r) for r in results], "stacks": [s.name for s in detected]}
        if not blocking_ran:
            msg = ("no automated verification available" if key == "verify" else
                   "no environment checks configured (add 'environment' to .agentic-sdlc/verify.json)")
            if any(r.get("skipped") for r in results):
                msg += " - " + "; ".join(f"{r['name']}: {r['summary']}" for r in results if r.get("skipped"))
            return {"skipped": True, "passed": None, "summary": f"Skipped: {msg}", "output": "\n".join(lines),
                    "violations": [], "checks": [_slim(r) for r in results], "stacks": [s.name for s in detected],
                    "require_human": key == "verify" and require_verification}
        return {"passed": True, "summary": f"All blocking checks passed ({summary})" +
                (f"; {len(advisory_failed)} advisory warning(s)" if advisory_failed else ""),
                "output": "\n".join(lines), "violations": [], "checks": [_slim(r) for r in results],
                "stacks": [s.name for s in detected]}

    def route_fix(self, gate: dict, files: dict[str, str]) -> str:
        """Failed test runs go to the developer (tests are the spec) unless the test code itself is broken;
        static/lint findings go to whoever owns the flagged files."""
        failed = [c for c in gate.get("checks", []) if c.get("blocking") and c.get("passed") is False]
        if any(c.get("kind") == "test" and not c.get("test_code_broken") for c in failed):
            return "developer"
        bad = {v["file"] for v in gate.get("violations", []) if v.get("file")}
        return "tester" if bad and all(stacks.is_test_path(f) for f in bad) else "developer"


def _slim(r: dict) -> dict:
    return {k: v for k, v in r.items() if k not in ("output",)}
