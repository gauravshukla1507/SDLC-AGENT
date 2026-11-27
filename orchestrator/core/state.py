"""Read model of a company for UIs and automation (`agentic.py state --json`, the VS Code views)."""
from __future__ import annotations

import json
from pathlib import Path

from .company import org_chart


def _adapter(raw) -> dict:
    try:
        raw = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        pass
    return raw if isinstance(raw, dict) else {"type": raw}


def project_status(tasks: list[dict], pending: list[dict], last_event: str | None = None) -> str:
    if any(t["status"] == "cancelled" for t in tasks):
        return "rejected"
    if tasks and all(t["status"] == "done" for t in tasks):
        return "released"
    if any(t["status"] == "failed" for t in tasks):
        return "failed"
    if any(p["type"] == "escalation" for p in pending):
        return "escalated"
    if pending or any(t["status"] == "blocked" for t in tasks):
        return "waiting_approval"
    if any(t["status"] == "in_progress" for t in tasks):
        return "running"
    if last_event == "project.paused":
        return "paused"
    return "queued"


def company_state(store, company: dict, recent_approvals: int = 25) -> dict:
    cid = company["id"]
    spec = company.get("spec") or {}
    pending_all = store.all("SELECT * FROM approvals WHERE company_id=? AND status='pending' ORDER BY created", cid)
    runs = store.all("SELECT * FROM runs WHERE company_id=?", cid)
    projects = []
    for g in store.all("SELECT * FROM goals WHERE company_id=? AND level='project' ORDER BY created DESC", cid):
        tasks = store.all("SELECT * FROM tasks WHERE goal_id=? ORDER BY created", g["id"])
        ids = {t["id"] for t in tasks}
        pending = [p for p in pending_all if p["task_id"] in ids or (p["type"] == "hire")]
        det = store.one("SELECT details FROM activity WHERE company_id=? AND action='stack.detected' AND entity=? "
                        "ORDER BY seq DESC LIMIT 1", cid, f"goal:{g['id']}")
        trace = Path(g["workspace"] or ".") / ".agentic-sdlc" / "projects" / g["id"] / "trace.json"
        last = store.one("SELECT action, details FROM activity WHERE company_id=? AND entity=? AND action LIKE 'project.%' "
                         "ORDER BY seq DESC LIMIT 1", cid, f"goal:{g['id']}")
        paused_reason = None
        if last and last["action"] == "project.paused":
            r = store.one("SELECT details FROM activity WHERE company_id=? AND action='agent.unavailable' "
                          "ORDER BY seq DESC LIMIT 1", cid)
            paused_reason = (r or {}).get("details", {}).get("reason") if r else None
        created = store.one("SELECT details FROM activity WHERE company_id=? AND action='project.created' AND entity=?",
                            cid, f"goal:{g['id']}")
        fvars = store.one("SELECT details FROM activity WHERE company_id=? AND action='project.vars' AND entity=?",
                          cid, f"goal:{g['id']}")
        spec_dir = ((fvars or {}).get("details") or {}).get("spec_dir")
        ws = Path(g["workspace"] or ".")
        spec_docs = []   # spec-driven: the feature's spec, plan and tasks, and the project constitution
        if spec_dir and (ws / spec_dir).is_dir():
            spec_docs = sorted(str(f) for f in (ws / spec_dir).rglob("*") if f.is_file())
        if spec_dir and (ws / ".specify/memory/constitution.md").exists():
            spec_docs.append(str(ws / ".specify/memory/constitution.md"))
        projects.append({
            "id": g["id"], "title": g["title"], "workspace": g["workspace"], "created": g["created"],
            "status": project_status(tasks, pending, last["action"] if last else None),
            "paused_reason": paused_reason, "stacks": (det or {}).get("details", {}).get("stacks", []),
            "spent_usd": round(g["spent_usd"] or 0, 6), "budget_usd": g["budget_usd"],
            "trace": str(trace) if trace.exists() else None,
            "playbook": ((created or {}).get("details") or {}).get("playbook"), "spec_dir": spec_dir,
            "documents": spec_docs + (sorted(str(f) for f in trace.parent.rglob("*") if f.is_file()
                                             and f.suffix in (".md", ".yml", ".yaml", ".xml")) if trace.parent.exists() else []),
            "tasks": [{"id": t["id"], "key": t["key"], "title": t["title"], "kind": t["kind"],
                       "assignee": t["assignee"], "status": t["status"], "attempts": t["attempts"],
                       "summary": ((t["result"] or {}).get("summary") or "")[:300],
                       "blocked_reason": (t["spec"] or {}).get("blocked_reason")} for t in tasks]})
    agents = [{"key": a["key"], "name": a["name"], "title": a["title"], "reports_to": a["reports_to"],
               "depth": a["depth"], "status": a["status"], "adapter": _adapter(a["adapter"]),
               "skills": a["skills"] or [], "spent_usd": round(a["spent_usd"] or 0, 6), "budget_usd": a["budget_usd"]}
              for a in org_chart(store, cid)]
    approvals = pending_all + store.all("SELECT * FROM approvals WHERE company_id=? AND status!='pending' "
                                        "ORDER BY decided DESC LIMIT ?", cid, recent_approvals)
    return {
        "company": {"id": cid, "name": company["name"], "mission": company["mission"],
                    "budgets": spec.get("budgets", {}), "governance": spec.get("governance", {})},
        "agents": agents,
        "projects": projects,
        "approvals": [{k: a[k] for k in ("id", "type", "subject", "status", "payload", "decided_by", "comment",
                                         "created", "decided", "task_id")} for a in approvals],
        "spend": {"total_usd": round(sum(r["cost_usd"] or 0 for r in runs), 6),
                  "billing": "subscription" if any("/subscription" in (r["adapter"] or "") for r in runs) else "api",
                  "limit_usd": spec.get("budgets", {}).get("company_usd"),
                  "estimated": any(r["tokens_estimated"] for r in runs)},
        "routines": [{"key": r["key"], "schedule": r["schedule"], "title": (r["spec"] or {}).get("title"),
                      "last_run": r["last_run"]} for r in store.all("SELECT * FROM routines WHERE company_id=?", cid)],
    }
