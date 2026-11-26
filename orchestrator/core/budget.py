"""Cost tracking and budget enforcement.

Spend is recorded per run and rolled up to the agent, the goal (project) and the company.
Each scope can have a budget: crossing `warn_at` logs a warning; reaching 100% is a hard stop.
The agent (or the whole company) is paused and the orchestrator opens a budget-override approval.
"""
from __future__ import annotations

# USD per million tokens (input, output). Override per company in spec["pricing"].
DEFAULT_PRICING = {
    "default": (3.00, 15.00),
    "claude-sonnet": (3.00, 15.00),
    "claude-opus": (15.00, 75.00),
    "claude-haiku": (1.00, 5.00),
}


class BudgetExceeded(Exception):
    def __init__(self, scope: str, name: str, spent: float, limit: float):
        super().__init__(f"{scope} budget exhausted for {name}: ${spent:.4f} of ${limit:.2f}")
        self.scope, self.name, self.spent, self.limit = scope, name, spent, limit


def price_for(model: str | None, pricing: dict | None = None) -> tuple[float, float]:
    table = {**DEFAULT_PRICING, **(pricing or {})}
    m = (model or "").lower()
    for prefix, rate in sorted(table.items(), key=lambda kv: -len(kv[0])):
        if prefix != "default" and m.startswith(prefix):
            return tuple(rate)
    return tuple(table["default"])


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def run_cost(model: str | None, input_tokens: int, output_tokens: int, pricing: dict | None = None) -> float:
    pin, pout = price_for(model, pricing)
    return round(input_tokens / 1e6 * pin + output_tokens / 1e6 * pout, 6)


class BudgetGuard:
    def __init__(self, store, company: dict, log):
        self.store = store
        self.company = company
        self.spec = company.get("spec") or {}
        self.log = log
        b = self.spec.get("budgets", {})
        self.company_limit = b.get("company_usd")
        self.warn_at = b.get("warn_at", 0.8)

    def _company_spent(self) -> float:
        row = self.store.one("SELECT COALESCE(SUM(cost_usd),0) AS s FROM runs WHERE company_id=?", self.company["id"])
        return row["s"]

    def check(self, agent: dict, goal: dict | None):
        """Raise BudgetExceeded before a run if any scope is already exhausted."""
        if self.company_limit is not None and self._company_spent() >= self.company_limit:
            raise BudgetExceeded("company", self.company["name"], self._company_spent(), self.company_limit)
        if agent.get("budget_usd") is not None and agent["spent_usd"] >= agent["budget_usd"]:
            raise BudgetExceeded("agent", agent["name"], agent["spent_usd"], agent["budget_usd"])
        if goal and goal.get("budget_usd") is not None and goal["spent_usd"] >= goal["budget_usd"]:
            raise BudgetExceeded("goal", goal["title"], goal["spent_usd"], goal["budget_usd"])

    def record(self, agent: dict, goal: dict | None, cost: float):
        """Roll spend up to agent and goal, and emit warnings when a scope crosses the threshold."""
        store = self.store
        with store.lock:
            store.db.execute("UPDATE agents SET spent_usd = spent_usd + ? WHERE id=?", (cost, agent["id"]))
            if goal:
                store.db.execute("UPDATE goals SET spent_usd = spent_usd + ? WHERE id=?", (cost, goal["id"]))
        a = store.one("SELECT * FROM agents WHERE id=?", agent["id"])
        self._warn("agent", a["name"], a["spent_usd"], a["budget_usd"], cost)
        if goal:
            g = store.one("SELECT * FROM goals WHERE id=?", goal["id"])
            self._warn("goal", g["title"], g["spent_usd"], g["budget_usd"], cost)
        if self.company_limit is not None:
            self._warn("company", self.company["name"], self._company_spent(), self.company_limit, cost)

    def _warn(self, scope, name, spent, limit, cost):
        if limit and spent >= self.warn_at * limit and spent - cost < self.warn_at * limit:
            self.log("budget", "budget.warning", f"{scope}:{name}", spent=round(spent, 4), limit=limit,
                     threshold=self.warn_at)
