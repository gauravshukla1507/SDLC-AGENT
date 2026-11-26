"""The output contract every agent follows, whatever its adapter, and its parser."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

CONTRACT = """
Output contract (follow exactly, no text outside these tags):
<summary>One short paragraph describing what you produced and key decisions.</summary>
Then zero or more files, each as:
<file path="relative/path">
FULL file content
</file>
Reviewers add <finding severity="low|medium|high">...</finding> items and one <verdict>...</verdict>.
Never truncate files. Never wrap file content in markdown fences.
""".strip()


@dataclass
class Role:
    key: str
    name: str
    description: str
    system: str


@dataclass
class AgentOutput:
    summary: str
    files: dict[str, str] = field(default_factory=dict)
    verdict: str | None = None
    findings: list[dict] = field(default_factory=list)
    raw: str = ""


def role(key: str, name: str, description: str, title: str, duties: str, standards: str = "") -> Role:
    system = (f"You are the {title} on an autonomous delivery team run as a company. An orchestrator assigns you "
              f"tasks; you see the goal ancestry so you know why the work matters.\n\nYour duties:\n{duties}\n\n"
              f"{standards}\n\n{CONTRACT}")
    return Role(key, name, description, system)


def parse_output(text: str) -> AgentOutput:
    summary = re.search(r"<summary>(.*?)</summary>", text, re.S)
    files = {m.group(1).strip(): m.group(2).strip("\n") + "\n"
             for m in re.finditer(r'<file path="([^"]+)">\n?(.*?)</file>', text, re.S)}
    verdict = re.search(r"<verdict>\s*([A-Z_]+)\s*</verdict>", text)
    findings = [{"severity": m.group(1), "text": m.group(2).strip()}
                for m in re.finditer(r'<finding severity="(\w+)">(.*?)</finding>', text, re.S)]
    return AgentOutput(summary.group(1).strip() if summary else text.strip()[:400], files,
                       verdict.group(1) if verdict else None, findings, text)
