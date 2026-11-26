"""Companies: the org chart, goals, skills and routines that agents work inside.

A company is defined in a JSON spec (see companies/*.json) and materialised in the store:

  mission ─ goals ─ projects            every task links to a goal, and agents see the
                                        whole goal ancestry as context for their work
  org chart                             seats with title, reports_to, adapter, model,
                                        budget, skills, status (pending_approval/active/paused)
  skills                                markdown playbooks loaded into agents at runtime
  routines                              recurring work (e.g. nightly security sweep)
"""
from __future__ import annotations

import json
from pathlib import Path

from .store import Store, new_id, now

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"


def load_spec(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_skill(name: str, extra_dirs: list[Path] | None = None) -> str:
    for d in [*(extra_dirs or []), SKILLS_DIR]:
        p = Path(d) / f"{name}.md"
        if p.exists():
            return p.read_text(encoding="utf-8")
    raise FileNotFoundError(f"skill '{name}' not found")


def create_company(store: Store, spec: dict) -> dict:
    """Create (or return) a company from its spec. Idempotent on company name."""
    existing = store.one("SELECT * FROM companies WHERE name=?", spec["name"])
    if existing:
        return existing
    company = {"id": new_id("co"), "name": spec["name"], "mission": spec.get("mission", ""), "spec": spec,
               "created": now()}
    store.insert("companies", company)
    store.log(company["id"], "system", "company.created", f"company:{company['name']}")

    # Mission and standing goals
    mission_id = new_id("goal")
    store.insert("goals", {"id": mission_id, "company_id": company["id"], "parent_id": None, "level": "mission",
                           "title": spec.get("mission", spec["name"]), "description": "", "created": now()})

    def add_goals(items, parent):
        for g in items or []:
            gid = new_id("goal")
            store.insert("goals", {"id": gid, "company_id": company["id"], "parent_id": parent,
                                   "level": g.get("level", "goal"), "title": g["title"],
                                   "description": g.get("description", ""), "budget_usd": g.get("budget_usd"),
                                   "created": now()})
            add_goals(g.get("children"), gid)
    add_goals(spec.get("goals"), mission_id)

    # Org chart
    for a in spec.get("agents", []):
        store.insert("agents", {
            "id": new_id("agt"), "company_id": company["id"], "key": a["key"], "name": a.get("name", a["key"]),
            "title": a.get("title", ""), "reports_to": a.get("reports_to"),
            "adapter": json.dumps(a.get("adapter", "claude_api")) if not isinstance(a.get("adapter"), str)
            else a.get("adapter", "claude_api"),
            "status": "active" if a.get("adapter") == "human" else "pending_approval",
            "budget_usd": a.get("budget_usd"), "skills": a.get("skills", []), "config": a.get("config", {})})
        store.log(company["id"], "system", "agent.proposed", f"agent:{a['key']}", title=a.get("title"))

    for r in spec.get("routines", []):
        store.insert("routines", {"id": new_id("rtn"), "company_id": company["id"], "key": r["key"],
                                  "schedule": r["schedule"], "spec": r, "enabled": 1})
    return store.one("SELECT * FROM companies WHERE id=?", company["id"])


def hire_agents(store: Store, company: dict, governance) -> list[dict]:
    """Send every proposed seat through hiring approval (or activate directly if hiring is ungoverned)."""
    for a in store.all("SELECT * FROM agents WHERE company_id=? AND status='pending_approval'", company["id"]):
        if governance.approve_hires:
            governance.request("hire", f"Hire {a['name']} ({a['title']})",
                               {"agent_key": a["key"], "title": a["title"], "reports_to": a["reports_to"],
                                "adapter": a["adapter"], "budget_usd": a["budget_usd"], "skills": a["skills"]})
        else:
            with store.lock:
                store.db.execute("UPDATE agents SET status='active' WHERE id=?", (a["id"],))
    return store.all("SELECT * FROM agents WHERE company_id=?", company["id"])


def goal_ancestry(store: Store, goal_id: str | None) -> list[dict]:
    chain = []
    while goal_id:
        g = store.one("SELECT * FROM goals WHERE id=?", goal_id)
        if not g:
            break
        chain.append(g)
        goal_id = g["parent_id"]
    return list(reversed(chain))


def org_chart(store: Store, company_id: str) -> list[dict]:
    """Agents ordered as a tree (depth-first from the top), each with a 'depth'."""
    agents = store.all("SELECT * FROM agents WHERE company_id=?", company_id)
    by_parent: dict = {}
    for a in agents:
        by_parent.setdefault(a["reports_to"], []).append(a)
    out = []

    def walk(parent, depth):
        for a in sorted(by_parent.get(parent, []), key=lambda x: x["key"]):
            out.append({**a, "depth": depth})
            walk(a["key"], depth + 1)
    walk(None, 0)
    seen = {a["key"] for a in out}
    out += [{**a, "depth": 0} for a in agents if a["key"] not in seen]  # orphans
    return out
