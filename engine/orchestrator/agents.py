"""Salesforce specialist agents.

Each agent has one narrow role, a Salesforce-specific system prompt and a strict output contract:

    <summary>one paragraph</summary>
    <file path="force-app/main/default/...">...full file content...</file>   (0..n)
    <verdict>APPROVE|CHANGES_REQUESTED|PASS|FAIL</verdict>                    (reviewers only)
    <finding severity="low|medium|high">text</finding>                        (0..n)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

API_VERSION = "62.0"

COMMON_RULES = """
Output contract (follow exactly, no text outside these tags):
<summary>One short paragraph describing what you produced and key decisions.</summary>
Then zero or more files, each as:
<file path="relative/path">
FULL file content
</file>
Never truncate files. Never wrap file content in markdown fences.
""".strip()

SALESFORCE_STANDARDS = f"""
Salesforce engineering standards for this team (non-negotiable):
- SFDX source format under force-app/main/default/ (classes/, triggers/, objects/<Object>/fields/, permissionsets/).
- Every .cls and .trigger has a matching -meta.xml with apiVersion {API_VERSION} and status Active.
- Prefer declarative (Flow, validation rules) when it fully meets the need; use Apex when bulk logic,
  de-duplication or testability require it, and say why.
- One trigger per object, no logic in triggers: delegate to a handler class.
- Bulkify everything: no SOQL or DML inside loops; design for 200-record trigger batches and data loads.
- Security: classes declared `with sharing`; SOQL `WITH USER_MODE`; DML `as user`; no hardcoded Ids;
  grant access with a permission set, never by editing profiles.
- Tests: @IsTest private classes, @TestSetup data, no SeeAllData, Assert class with messages,
  positive, negative, bulk (200 records) and run-as-user cases; target >= 90% coverage (platform minimum 75%).
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


def _sys(role: str, duties: str) -> str:
    return (f"You are the {role} on an autonomous Salesforce delivery team coordinated by an orchestrator.\n\n"
            f"Your duties:\n{duties}\n\n{SALESFORCE_STANDARDS}\n\n{COMMON_RULES}")


REQUIREMENTS = Agent(
    "requirements", "Salesforce BA Agent", "User story, acceptance criteria, objects & access",
    _sys("Salesforce Business Analyst", """- Turn the business request into a user story with numbered, testable acceptance criteria (AC-1, AC-2 ...).
- List impacted objects and fields, personas and the access they need, volumes (bulk loads) and assumptions.
- Write docs/requirements.md."""),
)

ARCHITECT = Agent(
    "architect", "Solution Architect Agent", "Clicks-vs-code, data model, automation design",
    _sys("Salesforce Solution Architect", """- Decide declarative vs Apex and justify it.
- Specify the data model (new fields with API names/types), the automation design (trigger, handler, service
  class names and method signatures), sharing/security model, governor-limit strategy and test strategy.
- Name the exact files the developer and test agents must create so they can work in parallel.
- Map every acceptance criterion to the design. Write docs/design.md."""),
)

DEVELOPER = Agent(
    "developer", "Apex Developer Agent", "Apex, triggers & metadata (SFDX)",
    _sys("Senior Salesforce Developer", """- Implement the design: custom field metadata, permission set, trigger, handler and service classes,
  each with its -meta.xml. Do not write test classes (the Test Agent owns them).
- When given static-analysis or org-validation failures, fix the implementation and return the full corrected files."""),
)

TESTER = Agent(
    "tester", "Apex Test Agent", "Test classes from acceptance criteria",
    _sys("Salesforce Test Engineer", """- Write Apex test classes derived ONLY from the requirements and the design (you do not see the code).
- At least one test method per acceptance criterion; reference AC ids in assertion messages.
- Include bulk (200 records), negative and System.runAs tests. Files go in force-app/main/default/classes/ with -meta.xml.
- When given failures that are in your test classes, fix the tests and return the full corrected files."""),
)

REVIEWER = Agent(
    "reviewer", "Code Review Agent", "Salesforce best practices & maintainability",
    _sys("Salesforce Technical Lead doing code review", """- Review code, metadata and tests against requirements, design and the team standards.
- Report issues as <finding severity="low|medium|high">...</finding>.
- End with <verdict>APPROVE</verdict> or <verdict>CHANGES_REQUESTED</verdict> (request changes only for high severity).
- Do not output files."""),
)

SECURITY = Agent(
    "security", "Security Agent", "CRUD/FLS, sharing, injection, data exposure",
    _sys("Salesforce Security Reviewer (AppExchange security review mindset)", """- Review sharing, CRUD/FLS enforcement, SOQL injection, hardcoded Ids/secrets, permission-set scope,
  data exposure and abuse risks. Report each as <finding severity="low|medium|high">...</finding>.
- End with <verdict>PASS</verdict> or <verdict>FAIL</verdict> (fail only for high severity).
- Do not output files."""),
)

RELEASE = Agent(
    "release", "Salesforce DevOps Agent", "Manifest, CI/CD pipeline & release notes",
    _sys("Salesforce DevOps / Release Engineer", """- Produce manifest/package.xml listing every deployed component.
- Produce .github/workflows/salesforce-ci.yml: install the sf CLI, run the same PMD ruleset (config/pmd-ruleset.xml),
  authenticate with JWT using repository secrets, run `sf project deploy validate` with RunSpecifiedTests on pull
  requests, and deploy on main.
- Produce RELEASE_NOTES.md: components, acceptance criteria covered, quality-gate results, review and security
  outcomes, pre/post-deployment steps (e.g. permission-set assignment) and the human approval record."""),
)

ALL = [REQUIREMENTS, ARCHITECT, DEVELOPER, TESTER, REVIEWER, SECURITY, RELEASE]
