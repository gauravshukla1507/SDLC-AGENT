"""Governance: approvals, hiring control and the tool-action gateway.

Approval types
  hire             a new agent seat must be approved before it can work
  release          the human gate before release work products are produced
  budget_override  a scope ran out of budget; approve to raise the limit and resume
  escalation       a quality gate could not be fixed within the iteration limit
  gateway          a tool action set to "ask_first" (e.g. deploying to an org)

Approvals are durable records. An approver strategy may decide immediately (auto, CLI prompt,
IDE over stdin) or leave them pending ("deferred"). The run then pauses and resumes once someone
decides with `uo.py approve`.
"""
from __future__ import annotations

import json
import sys
from typing import Callable

from .store import new_id, now

GATEWAY_VALUES = ("allowed", "ask_first", "off")


class Governance:
    def __init__(self, store, company: dict, log, approver: Callable[[dict], dict | None] | None = None):
        self.store = store
        self.company = company
        self.spec = (company.get("spec") or {}).get("governance", {})
        self.log = log
        self.approver = approver or deferred_approver

    # ------------------------------------------------------------ policy
    @property
    def approve_hires(self) -> bool:
        return bool(self.spec.get("approve_hires", True))

    @property
    def release_approval(self) -> bool:
        return bool(self.spec.get("release_approval", True))

    def gateway(self, action: str) -> str:
        value = self.spec.get("gateway", {}).get(action, self.spec.get("gateway_default", "allowed"))
        if value not in GATEWAY_VALUES:
            raise ValueError(f"gateway policy for {action} must be one of {GATEWAY_VALUES}")
        return value

    # ------------------------------------------------------------ approvals
    def request(self, type_: str, subject: str, payload: dict | None = None, task_id: str | None = None) -> dict:
        existing = self.store.one("SELECT * FROM approvals WHERE company_id=? AND type=? AND subject=? AND status='pending'",
                                  self.company["id"], type_, subject)
        if existing:
            return existing
        row = {"id": new_id("apr"), "company_id": self.company["id"], "type": type_, "subject": subject,
               "task_id": task_id, "payload": payload or {}, "status": "pending", "created": now()}
        self.store.insert("approvals", row)
        self.log("governance", "approval.requested", f"approval:{row['id']}", type=type_, subject=subject)
        decision = self.approver(self.store.one("SELECT * FROM approvals WHERE id=?", row["id"]))
        if decision:
            self.resolve(row["id"], decision["approved"], decision.get("approver", "unknown"),
                         decision.get("comment", ""))
        return self.store.one("SELECT * FROM approvals WHERE id=?", row["id"])

    def resolve(self, approval_id: str, approved: bool, by: str, comment: str = "") -> dict:
        apr = self.store.one("SELECT * FROM approvals WHERE id=?", approval_id)
        if not apr:
            raise KeyError(approval_id)
        if apr["status"] != "pending":
            return apr
        self.store.update("approvals", "id", approval_id, status="approved" if approved else "rejected",
                          decided_by=by, comment=comment, decided=now())
        self.log(f"human:{by}", "approval.approved" if approved else "approval.rejected",
                 f"approval:{approval_id}", type=apr["type"], subject=apr["subject"], comment=comment)
        self._apply(apr, approved)
        return self.store.one("SELECT * FROM approvals WHERE id=?", approval_id)

    def _apply(self, apr: dict, approved: bool):
        """Side effects of a decision on agents and budgets (tasks are handled by the engine)."""
        p = apr.get("payload") or {}
        if apr["type"] == "hire":
            with self.store.lock:
                self.store.db.execute("UPDATE agents SET status=? WHERE company_id=? AND key=?",
                                      ("active" if approved else "rejected", self.company["id"], p["agent_key"]))
        elif apr["type"] == "budget_override" and approved:
            extra = float(p.get("increase_usd", 0))
            self.store.lock.acquire()
            if p.get("scope") == "agent":
                self.store.db.execute("UPDATE agents SET budget_usd = budget_usd + ?, status='active' "
                                      "WHERE company_id=? AND key=?", (extra, self.company["id"], p["key"]))
            elif p.get("scope") == "goal":
                self.store.db.execute("UPDATE goals SET budget_usd = budget_usd + ? WHERE id=?", (extra, p["key"]))
            elif p.get("scope") == "company":
                spec = self.company.get("spec") or {}
                spec.setdefault("budgets", {})["company_usd"] = spec.get("budgets", {}).get("company_usd", 0) + extra
                self.store.update("companies", "id", self.company["id"], spec=spec)
            self.store.lock.release()

    def pending(self) -> list[dict]:
        return self.store.all("SELECT * FROM approvals WHERE company_id=? AND status='pending' ORDER BY created",
                              self.company["id"])


# ------------------------------------------------------------------ approver strategies
def deferred_approver(approval: dict) -> None:
    """Leave the approval pending; decide later with `uo.py approve <id>`."""
    return None


def auto_approver(name: str, comment: str = "Auto-approved (demo mode)", types: set[str] | None = None):
    def _approve(approval: dict):
        if types and approval["type"] not in types:
            return None
        return {"approved": True, "approver": name, "comment": comment}
    return _approve


def cli_approver(approval: dict) -> dict:
    print(f"\n=== APPROVAL REQUIRED ({approval['type']}) ===\n{approval['subject']}")
    for k, v in (approval.get("payload") or {}).items():
        print(f"  {k}: {json.dumps(v)[:300]}")
    name = input("Your name: ").strip() or "reviewer"
    ok = input("Approve? [y/N]: ").strip().lower() == "y"
    return {"approved": ok, "approver": name, "comment": input("Comment (optional): ").strip()}


def stdin_approver(approval: dict) -> dict:
    """IDE protocol: emit a request line, read one JSON decision line."""
    sys.stdout.write("@@APPROVAL_REQUEST " + json.dumps(approval, default=str) + "\n")
    sys.stdout.flush()
    try:
        d = json.loads(sys.stdin.readline())
    except json.JSONDecodeError:
        d = {}
    return {"approved": bool(d.get("approved")), "approver": d.get("approver") or "unknown",
            "comment": d.get("comment", "")}
