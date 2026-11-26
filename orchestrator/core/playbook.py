"""Playbooks. The built-in one (delivery.UniversalDelivery) works for every stack; the abstraction
stays so an organisation can register a different *process* (e.g. a hotfix or data-migration flow),
not a different technology. A playbook supplies:
  roles        agent role prompts (keys match org-chart seats)
  steps        the task graph for a delivery (agent steps, quality gates, approval steps)
  gates        real tools that verify work (tests, static analysis, deployments)
  routing      which role fixes a failed gate
  file layout  which outputs are source (written in place) vs run artifacts (docs, CI, notes)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Step:
    key: str
    title: str
    kind: str                       # "agent" | "gate" | "approval"
    role: str | None = None         # org-chart seat for agent steps
    deps: list[str] = field(default_factory=list)
    inputs: tuple[str, ...] = ()    # work-product path prefixes given to the agent
    with_context: bool = False      # include existing-project context
    gateway_action: str | None = None  # tool action name checked against governance gateway policy


class Playbook:
    name = "base"
    description = ""
    source_prefixes: tuple[str, ...] = ()   # written into the workspace in place
    roles: dict = {}

    def steps(self) -> list[Step]:
        raise NotImplementedError

    def scaffold(self, ws: Path) -> list[str]:
        return []

    def project_context(self, ws: Path) -> str:
        return ""

    def run_gate(self, key: str, ws: Path, run_files: set[str], files: dict[str, str], **options) -> dict:
        raise NotImplementedError

    def route_fix(self, gate: dict, files: dict[str, str]) -> str:
        return "developer"

    def fix_inputs(self) -> tuple[str, ...]:
        return ("docs/", *self.source_prefixes)

    def is_source(self, rel: str) -> bool:
        return rel.startswith(self.source_prefixes)

    def physical_path(self, rel: str, ws: Path, artifacts: Path) -> Path:
        return ws / rel if self.is_source(rel) else artifacts / rel

    def stack_skills(self, ws: Path) -> list[str]:
        return []

    def detected(self, ws: Path) -> list[str]:
        return []


_REGISTRY: dict[str, type[Playbook]] = {}


def register(cls):
    _REGISTRY[cls.name] = cls
    return cls


def get_playbook(name: str = "universal") -> Playbook:
    import delivery  # noqa: F401  (registers the universal playbook)
    if name not in _REGISTRY:
        raise KeyError(f"Unknown playbook '{name}'. Available: {', '.join(sorted(_REGISTRY))}")
    return _REGISTRY[name]()


def available_playbooks() -> list[str]:
    import delivery  # noqa: F401
    return sorted(_REGISTRY)
