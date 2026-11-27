"""Spec-driven development (SDD) playbook, the default way the agent team develops.

The specification is the source of truth; code is derived from it and checked against it. Layout follows
GitHub Spec Kit, so specs are readable by other SDD tools and live in the repository next to the code:

    .specify/memory/constitution.md      project principles every spec, plan and change must respect
    specs/NNN-feature/spec.md            WHAT and WHY: user stories, acceptance criteria, no technology
    specs/NNN-feature/plan.md            HOW: architecture, reuse of existing components, contracts, risks
    specs/NNN-feature/tasks.md           ordered, test-first tasks (T001…), [P] = can run in parallel

Flow (each step is a task in the company; humans hold the review gates):

    map codebase (Graphify, existing products) → constitution (once per repo) → specify → SPEC REVIEW
    → plan + tasks → PLAN REVIEW → tests first ∥ implementation → verify (stack tools) → spec coverage
    (every acceptance criterion has a test) → environment → code review (with blast radius) ∥ security
    → RELEASE APPROVAL → release notes

"Request changes" on a spec or plan sends it back to its author with your comment and asks again; an
approval comment is appended to the document as a clarification.
"""
from __future__ import annotations

import re
from pathlib import Path

import stacks
from core import codegraph
from core.agents_contract import role
from core.playbook import Step, register
from delivery import GENERAL, ROLES as CLASSIC_ROLES, UniversalDelivery

CONSTITUTION = ".specify/memory/constitution.md"

SDD = """Spec-driven development rules for this team:
- The spec is the source of truth. Code, tests and docs follow it; when they disagree, the spec wins or is
  explicitly revised.
- Specs say WHAT and WHY (no technology). Plans say HOW. Tasks are small, ordered and test-first.
- <spec_dir> is the feature folder given as "spec_dir" in the task context (e.g. specs/003-late-fees/).
- Every acceptance criterion has an id (AC-n) and at least one automated test that references that id.
- Respect the constitution (.specify/memory/constitution.md). Reuse existing components named in the plan."""

ROLES = {
    "requirements": role("requirements", "Requirements Agent", "Writes the feature spec", "Business Analyst",
        """- Write <spec_dir>spec.md from the requirements document. Sections: Overview (what and why, no technology),
  User stories (US-1… "As a / I want / So that", priority P1/P2/P3, how to test it independently), Acceptance
  criteria (AC-1… in Given/When/Then, each linked to a story), Functional requirements (FR-1…), Key entities,
  Success criteria (measurable), Assumptions, Out of scope, Traceability (document rule/requirement → US → AC).
- Be faithful to the document: keep its IDs, rules, numbers and boundaries exactly; reuse its acceptance
  criteria verbatim when it has them. Never invent features.
- Mark genuine ambiguities inline as [NEEDS CLARIFICATION: question] (at most 5); the reviewer answers them.
- For an existing product, use the codebase context only to name affected capabilities; still no technology.
- When the reviewer requests changes, apply their comment and return the full revised spec.md.""", SDD + "\n\n" + GENERAL),
    "architect": role("architect", "Architect Agent", "Constitution, plan and tasks", "Solution Architect",
        """- Task "Write the project constitution": write .specify/memory/constitution.md with the project's
  non-negotiable principles (architecture boundaries, testing standards, security, error handling, naming,
  dependency policy, how to change public interfaces). Derive them from the existing codebase (graph summary
  and code) and the team and stack conventions. At most 60 lines; each principle checkable in a review.
- Task "Plan": write <spec_dir>plan.md and <spec_dir>tasks.md.
  plan.md: Technical context (stack, versions, test framework, from the detected stack or chosen for a new
  application), Constitution check (table: principle → how the plan complies), Existing components to reuse or
  extend (from the codebase graph, with file paths), Design (components, interfaces/signatures, data model,
  contracts), Files to create / modify, Risks and blast radius, Test strategy per AC.
  tasks.md: numbered tasks T001… grouped Setup → Tests (written first) → Core → Integration → Polish; each task
  names its files and the AC ids it satisfies; mark tasks that can run in parallel with [P].
- When the reviewer requests changes to the plan, revise plan.md and tasks.md together and return both.""",
        SDD + "\n\n" + GENERAL),
    "tester": role("tester", "Test Agent", "Tests first, from the spec", "Test Engineer",
        """- Write the test tasks from <spec_dir>tasks.md: automated tests for EVERY acceptance criterion in
  <spec_dir>spec.md, in the stack's standard test framework. Put the AC id in each test's name, docstring or
  assertion message (the spec-coverage gate checks this).
- Derive tests only from the spec and the plan's interfaces (you don't see the implementation). Include
  boundaries, negative and volume cases. When failures are in your test code, fix the tests.""", SDD + "\n\n" + GENERAL),
    "developer": role("developer", "Developer Agent", "Implements tasks.md", "Senior Software Engineer",
        """- Implement the non-test tasks of <spec_dir>tasks.md in order, following plan.md and the constitution.
  Reuse and extend the existing components the plan names; keep public interfaces backward compatible unless
  the spec says otherwise. Do not write tests (the Test Agent owns them).
- Also return <spec_dir>tasks.md with the tasks you completed checked ([x]).
- When given failing verification output, fix the root cause and return the full corrected files.""",
        SDD + "\n\n" + GENERAL),
    "reviewer": role("reviewer", "Code Review Agent", "Reviews against spec, plan, constitution", "Technical Lead",
        """- Review the change against <spec_dir>spec.md (every acceptance criterion), plan.md, tasks.md and the
  constitution. Use the blast radius from the codebase graph to check impacted callers of changed code.
- Report issues as <finding severity="low|medium|high">...</finding>, naming the AC or principle at stake.
- End with <verdict>APPROVE</verdict> or <verdict>CHANGES_REQUESTED</verdict> (only for high severity).
- Do not output files.""", SDD + "\n\n" + GENERAL),
    "security": CLASSIC_ROLES["security"],
    "release": role("release", "Release Agent", "CI, release notes, spec status", "DevOps / Release Engineer",
        """- Produce RELEASE_NOTES.md: feature (link to <spec_dir>spec.md), acceptance criteria covered, verification
  and spec-coverage results (including skipped checks and why), review and security outcomes, impacted areas
  (blast radius), deployment steps and the human approval record.
- Propose a CI workflow (e.g. .github/workflows/ci.yml) running the same verification commands.""", GENERAL),
}

AC_ID = re.compile(r"\bAC-\d+(?:\.\d+)?\b")


def slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48].strip("-") or "feature"


def has_code(ws: Path) -> bool:
    return any(not f.startswith(("specs/", ".specify/")) and Path(f).suffix in stacks.CODE_SUFFIXES
               for f in stacks.walk_files(ws))


@register
class SpecDrivenDelivery(UniversalDelivery):
    name = "spec-driven"
    description = ("Spec-driven development: constitution → spec (reviewed) → plan + tasks (reviewed) → tests first "
                   "→ implementation → spec coverage; Graphify codebase map for existing products")
    roles = ROLES

    def steps(self, ws: Path | None = None) -> list[Step]:
        need_constitution = ws is None or not (Path(ws) / CONSTITUTION).exists()
        before_spec = ["constitution"] if need_constitution else ["map"]
        steps = [Step("map", "Map existing codebase (Graphify)", "gate")]
        if need_constitution:
            steps.append(Step("constitution", "Write the project constitution", "agent", "architect", deps=["map"],
                              inputs=(".specify/",), with_context=True))
        steps += [
            Step("specify", "Specify: write spec.md (what and why)", "agent", "requirements", deps=before_spec,
                 inputs=(".specify/",), with_context=True, receives_request=True),
            Step("spec_review", "Spec review", "approval", deps=["specify"], approval_type="spec",
                 document="{spec_dir}spec.md", revise_step="specify"),
            Step("plan", "Plan: write plan.md and tasks.md (how)", "agent", "architect", deps=["spec_review"],
                 inputs=("{spec_dir}", ".specify/"), with_context=True),
            Step("plan_review", "Plan review", "approval", deps=["plan"], approval_type="plan",
                 document="{spec_dir}plan.md", revise_step="plan"),
            Step("tests", "Write tests first, from the spec", "agent", "tester", deps=["plan_review"],
                 inputs=("{spec_dir}", ".specify/"), with_context=True),
            Step("build", "Implement tasks.md", "agent", "developer", deps=["plan_review"],
                 inputs=("{spec_dir}", ".specify/"), with_context=True),
            Step("verify", "Verify (build, static analysis, tests)", "gate", deps=["build", "tests"]),
            Step("spec_coverage", "Spec coverage (every AC has a test)", "gate", deps=["verify"]),
            Step("environment", "Environment validation", "gate", deps=["spec_coverage"],
                 gateway_action="environment.validate"),
            Step("review", "Code review against spec", "agent", "reviewer", deps=["environment"], inputs=("*",),
                 with_context=True),
            Step("security", "Security review", "agent", "security", deps=["environment"], inputs=("*",),
                 with_context=True),
            Step("release_approval", "Release approval", "approval", deps=["review", "security"]),
            Step("release", "Release notes & CI", "agent", "release", deps=["release_approval"], inputs=("*",),
                 with_context=True),
        ]
        return steps

    def feature_vars(self, ws: Path, title: str) -> dict:
        specs = Path(ws) / "specs"
        nums = [int(m.group(1)) for d in (specs.iterdir() if specs.exists() else [])
                if d.is_dir() and (m := re.match(r"(\d{3})-", d.name))]
        return {"spec_dir": f"specs/{(max(nums) + 1) if nums else 1:03d}-{slugify(title)}/"}

    # ------------------------------------------------------------ context from the codebase graph
    def task_context(self, ws: Path, task: dict, goal: dict, files: dict[str, str]) -> str:
        parts = []
        const = Path(ws) / CONSTITUTION
        if const.exists() and CONSTITUTION not in files and task["key"] != "constitution":
            parts.append(f'# Project constitution (existing)\n<file path="{CONSTITUTION}">\n'
                         f'{const.read_text(encoding="utf-8", errors="replace")[:6000]}</file>')
        if not codegraph.status(ws).get("built"):
            return "\n\n".join(parts)
        spec_dir = ((task.get("spec") or {}).get("vars") or {}).get("spec_dir", "")
        spec_text = files.get(f"{spec_dir}spec.md", "") or (goal or {}).get("description", "")
        if task["key"] in ("constitution", "plan", "review") or task["key"].startswith("revise-plan_review"):
            summary = codegraph.architecture_summary(ws)
            if summary:
                parts.append("# Architecture of the existing product (codebase graph)\n" + summary)
        if task["key"] != "constitution":
            question = f"{(goal or {}).get('title', '')} {codegraph.keywords(spec_text)}"
            found = codegraph.query(ws, question, budget=1800 if task["key"] in ("plan", "build") else 1200)
            if found:
                parts.append("# Existing code related to this feature (codebase graph query)\n" + found)
        if task["key"] in ("review", "security"):
            changed = [f for f in files if self.is_source(f) and not f.startswith(("specs/", ".specify/"))]
            radius = codegraph.blast_radius(ws, changed)
            if radius:
                parts.append("# " + radius)
        return "\n\n".join(parts)

    # ------------------------------------------------------------ gates
    def run_gate(self, key, ws, run_files, files, **options):
        if key == "map":
            result = codegraph.build(ws, has_code(ws))
            return {**result, "violations": []}
        if key == "spec_coverage":
            return self._spec_coverage(ws, files)
        return super().run_gate(key, ws, run_files, files, **options)

    def _spec_coverage(self, ws: Path, files: dict[str, str]) -> dict:
        spec_path = next((k for k in files if k.startswith("specs/") and k.endswith("/spec.md")), None)
        if not spec_path:
            return {"passed": False, "summary": "No spec.md was produced", "output": "", "violations": []}
        spec_dir = spec_path[: -len("spec.md")]
        acs = sorted(set(AC_ID.findall(files[spec_path])), key=lambda a: [int(x) for x in a[3:].split(".")])
        if not acs:
            return {"skipped": True, "passed": None, "summary": "Skipped: the spec has no AC ids", "output": "",
                    "violations": []}
        tests = {k: v for k, v in files.items() if stacks.is_test_path(k) and not k.startswith(("specs/", ".specify/"))
                 and not k.endswith((".md", ".txt"))}
        covered = {ac for ac in acs for body in tests.values() if re.search(rf"\b{re.escape(ac)}\b", body)}
        missing = [ac for ac in acs if ac not in covered]
        tasks_md = files.get(f"{spec_dir}tasks.md", "")
        unplanned = [ac for ac in acs if tasks_md and ac not in tasks_md]
        lines = [f"Acceptance criteria in {spec_path}: {len(acs)}; covered by tests: {len(covered)}"]
        lines += [f"MISSING test for {ac}" for ac in missing]
        if unplanned:
            lines.append(f"(advisory) not referenced in tasks.md: {', '.join(unplanned)}")
        if missing:
            return {"passed": False, "summary": f"{len(missing)} acceptance criteria without a test: {', '.join(missing)}",
                    "output": "\n".join(lines),
                    "violations": [{"file": spec_path, "line": None, "rule": "SpecCoverage",
                                    "message": f"{ac} has no test referencing it", "engine": "spec"} for ac in missing],
                    "checks": [{"name": "spec coverage", "blocking": True, "passed": False, "kind": "coverage"}]}
        return {"passed": True, "summary": f"All {len(acs)} acceptance criteria have tests", "output": "\n".join(lines),
                "violations": []}

    def route_fix(self, gate: dict, files: dict[str, str]) -> str:
        if gate.get("tool") == "spec_coverage":
            return "tester"
        return super().route_fix(gate, files)
