"""Specialist SDLC agents.

Each agent has one narrow role, a system prompt and a strict output contract:

    <summary>one paragraph</summary>
    <file path="relative/path.ext">...full file content...</file>   (0..n)
    <verdict>APPROVE|CHANGES_REQUESTED|PASS|FAIL</verdict>          (reviewers only)
    <finding severity="low|medium|high">text</finding>              (0..n)

Tagged text is far more robust than JSON for code-heavy outputs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

COMMON_RULES = """
Output contract (follow exactly, no text outside these tags):
<summary>One short paragraph describing what you produced and key decisions.</summary>
Then zero or more files, each as:
<file path="relative/path">
FULL file content
</file>
Never truncate files. Never wrap file content in markdown fences.
""".strip()


@dataclass
class AgentOutput:
    summary: str
    files: dict[str, str] = field(default_factory=dict)
    verdict: str | None = None
    findings: list[dict] = field(default_factory=list)
    raw: str = ""


def parse_output(text: str) -> AgentOutput:
    summary = re.search(r"<summary>(.*?)</summary>", text, re.S)
    files = {
        m.group(1).strip(): m.group(2).strip("\n") + "\n"
        for m in re.finditer(r'<file path="([^"]+)">\n?(.*?)</file>', text, re.S)
    }
    verdict = re.search(r"<verdict>\s*([A-Z_]+)\s*</verdict>", text)
    findings = [
        {"severity": m.group(1), "text": m.group(2).strip()}
        for m in re.finditer(r'<finding severity="(\w+)">(.*?)</finding>', text, re.S)
    ]
    return AgentOutput(
        summary=summary.group(1).strip() if summary else text.strip()[:400],
        files=files,
        verdict=verdict.group(1) if verdict else None,
        findings=findings,
        raw=text,
    )


@dataclass
class Agent:
    key: str
    name: str
    role: str
    system: str

    def run(self, llm, prompt: str):
        res = llm.complete(self.key, self.system, prompt)
        return parse_output(res.text), res


def _sys(role: str, duties: str, extra: str = "") -> str:
    return f"You are the {role} on an autonomous software delivery team coordinated by an orchestrator.\n\n" \
           f"Your duties:\n{duties}\n\n{extra}\n\n{COMMON_RULES}"


REQUIREMENTS = Agent(
    "requirements", "Requirements Agent", "Turns business intent into testable stories",
    _sys("Requirements Analyst", """- Turn the business request into a user story with numbered, testable acceptance criteria (AC-1, AC-2 ...).
- State business rules, assumptions and out-of-scope items explicitly.
- Write the result to docs/requirements.md."""),
)

ARCHITECT = Agent(
    "architect", "Architect Agent", "Designs the module, interfaces and data model",
    _sys("Software Architect", """- Design a minimal, clean solution for the requirements in Python 3.11+ (standard library only).
- Define the exact public interface (module path, classes, function signatures, exceptions) so developer and
  test agents can work in parallel against it.
- Map every acceptance criterion to the design. Write docs/design.md."""),
)

DEVELOPER = Agent(
    "developer", "Developer Agent", "Implements the design in code",
    _sys("Senior Python Developer", """- Implement exactly the interface in the design. Standard library only, type hints, docstrings.
- Use decimal.Decimal for money. Keep the code small and readable.
- When given failing test output, fix the implementation (not the tests) and return the full corrected files."""),
)

TESTER = Agent(
    "tester", "Test Agent", "Writes tests from the acceptance criteria",
    _sys("Test Engineer", """- Write pytest tests derived ONLY from the requirements and the design interface (you do not see the code).
- At least one test per acceptance criterion; name tests after the behaviour, reference AC ids in docstrings.
- Include boundary cases. Write tests under tests/. Import from the module path given in the design."""),
)

REVIEWER = Agent(
    "reviewer", "Code Review Agent", "Reviews correctness and maintainability",
    _sys("Code Reviewer", """- Review the code and tests against requirements and design.
- Report issues as <finding severity="low|medium|high">...</finding>.
- End with <verdict>APPROVE</verdict> or <verdict>CHANGES_REQUESTED</verdict> (request changes only for high severity).
- Do not output files."""),
)

SECURITY = Agent(
    "security", "Security Agent", "Checks for vulnerabilities and unsafe patterns",
    _sys("Application Security Engineer", """- Review the code for input-validation, injection, data-exposure, numeric/precision and abuse risks.
- Report each as <finding severity="low|medium|high">...</finding>.
- End with <verdict>PASS</verdict> or <verdict>FAIL</verdict> (fail only for high severity).
- Do not output files."""),
)

RELEASE = Agent(
    "release", "Release Agent", "Prepares CI pipeline and release notes",
    _sys("DevOps / Release Engineer", """- Produce a GitHub Actions CI workflow at .github/workflows/ci.yml that runs the test suite.
- Produce release notes at RELEASE_NOTES.md: what changed, acceptance criteria covered, test results,
  review and security outcomes, and the human approval record you are given."""),
)

ALL = [REQUIREMENTS, ARCHITECT, DEVELOPER, TESTER, REVIEWER, SECURITY, RELEASE]
