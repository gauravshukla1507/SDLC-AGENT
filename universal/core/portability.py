"""Export / import a company (org chart, goals, governance, budgets, routines, skills) as one JSON file.

Secrets are never exported: adapter URLs keep their value, but tokens come from the environment
(e.g. UO_HTTP_AGENT_TOKEN), and any adapter field named like a secret is dropped. Review exports
before sharing, because local paths and plain configuration values remain.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .company import load_skill

SECRET_KEYS = re.compile(r"(token|secret|password|api[_-]?key)", re.I)


def _clean(value):
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items() if not SECRET_KEYS.search(k)}
    return value


def export_company(store, company: dict, extra_skill_dirs: list[Path] | None = None) -> dict:
    spec = dict(company.get("spec") or {})
    agents = []
    skill_names = set()
    for a in store.all("SELECT * FROM agents WHERE company_id=?", company["id"]):
        adapter = a["adapter"]
        try:
            adapter = json.loads(adapter)
        except (json.JSONDecodeError, TypeError):
            pass
        agents.append({k: v for k, v in {
            "key": a["key"], "name": a["name"], "title": a["title"], "reports_to": a["reports_to"],
            "adapter": _clean(adapter), "budget_usd": a["budget_usd"], "skills": a["skills"] or []}.items()
            if v is not None})
        skill_names.update(a["skills"] or [])
    spec["agents"] = agents
    spec["skills_bundle"] = {}
    for name in sorted(skill_names):
        try:
            spec["skills_bundle"][name] = load_skill(name, extra_skill_dirs)
        except FileNotFoundError:
            pass
    spec["routines"] = [r["spec"] for r in store.all("SELECT * FROM routines WHERE company_id=?", company["id"])]
    return {"format": "universal-orchestrator/company@1", **_clean(spec)}


def import_company(path: Path, skills_dir: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("format") != "universal-orchestrator/company@1":
        raise ValueError("Not a Universal Orchestrator company export")
    skills_dir.mkdir(parents=True, exist_ok=True)
    for name, content in (data.pop("skills_bundle", None) or {}).items():
        target = skills_dir / f"{name}.md"
        if not target.exists():
            target.write_text(content, encoding="utf-8")
    data.pop("format", None)
    return data
