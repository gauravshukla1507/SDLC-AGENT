"""The Universal Orchestrator engine: a task-and-heartbeat runtime for an AI company.

A delivery request becomes a *project goal* under the company's goals, and the pack's playbook
becomes *tasks* with dependencies, each assigned to an org-chart seat. The scheduler wakes the
assigned agent (a heartbeat) whenever one of its tasks becomes ready. The agent atomically checks
the task out, gets the goal ancestry plus its inputs, runs through its adapter, and hands back work
products. Budgets are checked before every run and charged after it.

Quality gates are tasks too. A failed gate spawns a *fix task* for the owning role and re-queues
itself behind it; past the iteration limit it escalates to a human. Approvals (release, hires,
budget overrides, gateway actions, escalations) pause the affected task until a person decides,
and the run resumes from the store, even in a later process.
"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import agents_contract as contract
from .adapters import build_adapter
from .budget import BudgetExceeded, BudgetGuard, estimate_tokens, run_cost
from .company import goal_ancestry, load_skill
from .governance import Governance
from .pack import Pack, Step
from .store import Store, new_id, now

TERMINAL = {"done", "cancelled", "failed"}


class Engine:
    def __init__(self, store: Store, company: dict, pack: Pack, workspace: Path, *, approver=None,
                 replay=None, max_fix_iterations: int = 3, log=print, on_event=None, gate_options: dict | None = None):
        self.store, self.company, self.pack = store, company, pack
        self.ws = Path(workspace)
        self.ws.mkdir(parents=True, exist_ok=True)
        self.replay = replay
        self.max_fix = max_fix_iterations
        self.out = log
        self.on_event = on_event
        self.gate_options = gate_options or {}
        self.governance = Governance(store, company, self._log, approver)
        self.budget = BudgetGuard(store, company, self._log)
        self.pricing = (company.get("spec") or {}).get("pricing")
        self.concurrency = (company.get("spec") or {}).get("concurrency", 4)
        self._adapters: dict[str, object] = {}
        self.goal: dict | None = None
        self.artifacts: Path | None = None
        self.events: list[dict] = []     # demo/IDE-compatible trace events
        self.goal_stacks: list[str] = []

    # ------------------------------------------------------------ logging & events
    def _log(self, actor, action, entity="", **details):
        self.store.log(self.company["id"], actor, action, entity, **details)

    def _event(self, kind: str, **data) -> dict:
        with self.store.lock:
            ev = {"seq": len(self.events) + 1, "time": now(), "kind": kind, **data}
            self.events.append(ev)
        if self.on_event:
            self.on_event(ev)
        return ev

    # ------------------------------------------------------------ project setup
    def start_project(self, request: str, title: str | None = None, budget_usd: float | None = None,
                      parent_goal: str | None = None) -> dict:
        spec = self.company.get("spec") or {}
        if parent_goal is None:
            standing = self.store.one("SELECT * FROM goals WHERE company_id=? AND level='goal' ORDER BY created LIMIT 1",
                                      self.company["id"])
            parent_goal = standing["id"] if standing else self.store.one(
                "SELECT id FROM goals WHERE company_id=? AND level='mission'", self.company["id"])["id"]
        title = title or _title_from(request)
        gid = new_id("goal")
        self.store.insert("goals", {"id": gid, "company_id": self.company["id"], "parent_id": parent_goal,
                                    "level": "project", "title": title, "description": request,
                                    "budget_usd": budget_usd if budget_usd is not None
                                    else spec.get("budgets", {}).get("project_usd"), "created": now()})
        self.goal = self.store.one("SELECT * FROM goals WHERE id=?", gid)
        self.artifacts = self.ws / ".agentic-sdlc" / "projects" / gid
        self.artifacts.mkdir(parents=True, exist_ok=True)
        scaffolded = self.pack.scaffold(self.ws)
        self._log("orchestrator", "project.created", f"goal:{gid}", title=title, pack=self.pack.name,
                  scaffolded=scaffolded)

        steps = self.pack.playbook()
        ids = {s.key: new_id("task") for s in steps}
        for s in steps:
            self.store.insert("tasks", {
                "id": ids[s.key], "company_id": self.company["id"], "goal_id": gid, "parent_id": None,
                "key": s.key, "title": s.title, "kind": s.kind, "assignee": s.role or "orchestrator",
                "status": "todo", "deps": [ids[d] for d in s.deps],
                "spec": {"inputs": list(s.inputs), "with_context": s.with_context,
                         "gateway_action": s.gateway_action, "request": request if s.key == steps[0].key else None},
                "created": now(), "updated": now()})
        self._event("plan", graph=[{"id": s.role if s.kind == "agent" else s.key,
                                    "after": [(_step(steps, d).role if _step(steps, d).kind == "agent" else d)
                                              for d in s.deps]} for s in steps])
        self._log("orchestrator", "plan.created", f"goal:{gid}", tasks=[s.key for s in steps])
        return self.goal

    def attach(self, goal_id: str):
        """Resume an existing project (e.g. after approvals were decided in another process)."""
        self.goal = self.store.one("SELECT * FROM goals WHERE id=?", goal_id)
        self.artifacts = self.ws / ".agentic-sdlc" / "projects" / goal_id
        self.artifacts.mkdir(parents=True, exist_ok=True)
        previous = self.artifacts / "trace.json"
        if previous.exists():  # keep one continuous trace across pauses and resumes
            self.events = json.loads(previous.read_text(encoding="utf-8")).get("events", [])

    # ------------------------------------------------------------ scheduler
    def tasks(self) -> list[dict]:
        return self.store.all("SELECT * FROM tasks WHERE goal_id=? ORDER BY created", self.goal["id"])

    def run(self, poll_s: float = 0.0) -> str:
        """Run heartbeats until the project completes or waits on a human. Returns the project status."""
        while True:
            self._reconcile_blocked()
            tasks = self.tasks()
            done = {t["id"] for t in tasks if t["status"] == "done"}
            ready = [t for t in tasks if t["status"] == "todo" and all(d in done for d in t["deps"])]
            ready = [t for t in ready if t["kind"] != "agent" or self._agent_ready(t)]
            if not ready:
                status = self._project_status(tasks)
                if status != "running" or not poll_s:
                    return self._finish(status)
                time.sleep(poll_s)
                continue
            with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
                list(pool.map(self.heartbeat, ready))

    def _agent_ready(self, task) -> bool:
        a = self._agent(task["assignee"])
        if a is None:
            self._block(task, "no such seat in the org chart")
            return False
        return a["status"] == "active"

    def _project_status(self, tasks) -> str:
        if any(t["status"] == "cancelled" for t in tasks):
            return "rejected"
        if all(t["status"] == "done" for t in tasks):
            return "released"
        if any(t["status"] == "failed" for t in tasks):
            return "failed"
        pending = self.governance.pending()
        if pending:
            return "escalated" if any(p["type"] == "escalation" for p in pending) else "waiting_approval"
        if any(t["status"] == "todo" and self._agent(t["assignee"]) and
               self._agent(t["assignee"])["status"] in ("pending_approval", "paused") for t in tasks):
            return "waiting_approval"
        return "running" if any(t["status"] == "in_progress" for t in tasks) else "stalled"

    def _finish(self, status: str) -> str:
        trace = self.trace(status)
        (self.artifacts / "trace.json").write_text(json.dumps(trace, indent=2), encoding="utf-8")
        self._log("orchestrator", f"project.{status}", f"goal:{self.goal['id']}")
        if self.on_event:
            self.on_event({"kind": "finished", "status": status, "run_id": self.goal["id"],
                           "trace": self._rel(self.artifacts / "trace.json")})
        self.out(f"Project {self.goal['title']!r}: {status.upper()}  (trace: {self.artifacts / 'trace.json'})")
        return status

    # ------------------------------------------------------------ heartbeat
    def heartbeat(self, task: dict):
        actor = task["assignee"]
        self._log(actor, "heartbeat.wakeup", f"task:{task['key']}", reason="assigned_ready")
        if not self.store.checkout(task["id"], actor):
            return  # someone else got it
        try:
            if task["kind"] == "agent":
                self._run_agent_task(task)
            elif task["kind"] == "gate":
                self._run_gate_task(task)
            elif task["kind"] == "approval":
                self._run_approval_task(task)
        except BudgetExceeded as exc:
            self._on_budget(task, exc)
        except Exception as exc:  # noqa: BLE001
            self.store.release(task["id"], "failed", {"error": f"{type(exc).__name__}: {exc}"})
            self._log(actor, "task.failed", f"task:{task['key']}", error=str(exc))
            self.out(f"  !! {task['title']}: {exc}")

    # ---- agent tasks
    def _agent(self, key: str) -> dict | None:
        return self.store.one("SELECT * FROM agents WHERE company_id=? AND key=?", self.company["id"], key)

    def _adapter(self, agent: dict):
        if agent["key"] not in self._adapters:
            raw = agent["adapter"]
            try:
                raw = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                pass
            self._adapters[agent["key"]] = build_adapter(raw, self.replay)
        return self._adapters[agent["key"]]

    def _system_prompt(self, agent: dict) -> str:
        role = self.pack.roles[agent["key"]]
        skills = []
        dirs = [self.ws / ".agentic-sdlc" / "skills"]
        for name in agent.get("skills") or []:
            try:
                skills.append(f"## Skill: {name}\n{load_skill(name, dirs)}")
            except FileNotFoundError:
                self._log("orchestrator", "skill.missing", f"agent:{agent['key']}", skill=name)
        for name in self.pack.stack_skills(self.ws):   # conventions of every stack detected in the repo
            try:
                skills.append(f"## Stack skill: {name}\n{load_skill('stacks/' + name, dirs)}")
            except FileNotFoundError:
                pass
        return role.system + ("\n\n# Company skills\n" + "\n\n".join(skills) if skills else "")

    def _context_header(self, task: dict, agent: dict) -> str:
        chain = goal_ancestry(self.store, task["goal_id"])
        lines = ["# Why this work matters (goal ancestry)"]
        lines += [f"{'  ' * i}- [{g['level']}] {g['title']}" for i, g in enumerate(chain)]
        lines += [f"\n# Your task\n{task['title']} (task {task['key']}, you are {agent['name']}, {agent['title']})"]
        return "\n".join(lines)

    def _files(self, prefixes: tuple[str, ...] | list[str]) -> dict[str, str]:
        rows = self.store.all("SELECT path, content FROM work_products WHERE company_id=? AND task_id IN "
                              "(SELECT id FROM tasks WHERE goal_id=?) ORDER BY created", self.company["id"],
                              self.goal["id"])
        latest = {}
        for r in rows:
            latest[r["path"]] = r["content"]
        if not prefixes or "*" in prefixes:
            return dict(sorted(latest.items()))
        return {k: v for k, v in sorted(latest.items()) if k.startswith(tuple(prefixes))}

    def _run_agent_task(self, task: dict):
        agent = self._agent(task["assignee"])
        spec = task["spec"] or {}
        self.budget.check(agent, self.store.one("SELECT * FROM goals WHERE id=?", task["goal_id"]))
        parts = [self._context_header(task, agent)]
        if spec.get("request"):
            parts.append(f"# Business request from the client\n{spec['request']}")
        bundle = self._files(spec.get("inputs") or [])
        if bundle and spec.get("inputs"):
            parts.append("# Inputs from other agents\n" +
                         "\n".join(f'<file path="{k}">\n{v}</file>' for k, v in bundle.items()))
        if spec.get("with_context"):
            parts.append(self.pack.project_context(self.ws))
        if spec.get("feedback"):
            parts.append(spec["feedback"])
        prompt = "\n\n".join(p for p in parts if p)
        system = self._system_prompt(agent)

        self.out(f"  -> {agent['name']}: {task['title']}")
        t0 = time.time()
        adapter = self._adapter(agent)
        res = adapter.run(agent["key"], system, prompt, cwd=str(self.ws))
        out = contract.parse_output(res.text)
        estimated = res.input_tokens is None
        tin = res.input_tokens if not estimated else estimate_tokens(system + prompt)
        tout = res.output_tokens if not estimated else estimate_tokens(res.text)
        model = getattr(adapter, "model", None) if getattr(adapter, "model", None) != "recorded" else \
            _model_of(agent) or "claude-sonnet"
        cost = run_cost(model, tin, tout, self.pricing)

        paths = {}
        for rel, content in out.files.items():
            if ".." in Path(rel).parts or Path(rel).is_absolute():
                raise ValueError(f"{agent['name']} tried to write outside the workspace: {rel}")
            p = self.pack.physical_path(rel, self.ws, self.artifacts)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            paths[rel] = self._rel(p)
            self.store.insert("work_products", {"id": new_id("wp"), "company_id": self.company["id"],
                                                "task_id": task["id"], "path": rel, "physical_path": paths[rel],
                                                "content": content, "created": now()})
        self.store.insert("runs", {"id": new_id("run"), "company_id": self.company["id"], "task_id": task["id"],
                                   "agent_key": agent["key"], "adapter": type(adapter).__name__, "model": model,
                                   "started": now(), "finished": now(), "status": "succeeded",
                                   "input_tokens": tin, "output_tokens": tout, "tokens_estimated": int(estimated),
                                   "cost_usd": cost, "summary": out.summary[:500]})
        self.budget.record(agent, self.store.one("SELECT * FROM goals WHERE id=?", task["goal_id"]), cost)
        result = {"summary": out.summary, "files": sorted(out.files), "verdict": out.verdict, "findings": out.findings}
        self.store.release(task["id"], "done", result)
        self._log(agent["key"], "task.completed", f"task:{task['key']}", files=sorted(out.files),
                  verdict=out.verdict, cost_usd=cost, tokens_estimated=estimated)
        self._event("agent", agent=agent["key"], agent_name=agent["name"], step=task["title"], summary=out.summary,
                    files=sorted(out.files), file_contents=out.files, file_paths=paths, verdict=out.verdict,
                    findings=out.findings, duration_s=round(time.time() - t0, 2), input_tokens=tin,
                    output_tokens=tout, cost_usd=cost)
        self.out(f"     {out.summary[:140]}")

    # ---- gates
    def _run_gate_task(self, task: dict):
        spec = task["spec"] or {}
        action = spec.get("gateway_action")
        if action:
            policy = self.governance.gateway(action)
            if policy == "off":
                result = {"tool": task["key"], "tool_name": task["title"], "skipped": True, "passed": None,
                          "summary": f"Skipped: gateway policy for {action} is off", "output": ""}
                return self._gate_done(task, result)
            if policy == "ask_first" and not spec.get("gateway_approved"):
                apr = self.governance.request("gateway", f"Allow {action} for '{self.goal['title']}'",
                                              {"action": action, "task": task["key"]}, task_id=task["id"])
                if apr["status"] == "pending":
                    return self._block(task, f"waiting for gateway approval {apr['id']}", approval=apr["id"])
                if apr["status"] == "rejected":
                    result = {"tool": task["key"], "tool_name": task["title"], "skipped": True, "passed": None,
                              "summary": f"Skipped: {action} was not approved", "output": apr.get("comment") or ""}
                    return self._gate_done(task, result)

        files = self._files(())
        run_files = {k for k in files if self.pack.is_source(k)}
        opts = {**self.gate_options, "require_verification":
                bool((self.company.get("spec") or {}).get("governance", {}).get("require_verification"))}
        result = self.pack.run_gate(task["key"], self.ws, run_files, files, **opts)
        result = {"tool": task["key"], "tool_name": task["title"], **result}
        if result.get("stacks") and result["stacks"] != self.goal_stacks:
            self.goal_stacks = result["stacks"]
            self._log("orchestrator", "stack.detected", f"goal:{self.goal['id']}", stacks=result["stacks"])
        self._event("tool", **result)
        self._log("gate", "gate.passed" if (result.get("passed") or result.get("skipped")) else "gate.failed",
                  f"task:{task['key']}", summary=result["summary"])
        self.out(f"  [{task['title']}] {result['summary']}")
        if result.get("require_human"):
            apr = self.governance.request("escalation", f"No automated verification for '{self.goal['title']}'",
                                          {"gate": task["key"], "summary": result["summary"]}, task_id=task["id"])
            if apr["status"] == "pending":
                return self._block(task, "no automated verification; human sign-off required", approval=apr["id"])
        if result.get("passed") or result.get("skipped"):
            self.store.release(task["id"], "done", _slim(result))
            return

        attempts = task["attempts"] + 1
        if attempts > self.max_fix:
            apr = self.governance.request("escalation", f"{task['title']} still failing after {self.max_fix} fixes",
                                          {"gate": task["key"], "summary": result["summary"],
                                           "output": result.get("output", "")[-3000:]}, task_id=task["id"])
            return self._block(task, "escalated to a human", approval=apr["id"], attempts=attempts)

        role = self.pack.route_fix(result, files)
        fix_id = new_id("task")
        agent = self._agent(role)
        self.store.insert("tasks", {
            "id": fix_id, "company_id": self.company["id"], "goal_id": task["goal_id"], "parent_id": task["id"],
            "key": f"fix-{task['key']}-{attempts}", "title": f"Fix {task['title'].split(' (')[0].lower()} findings "
            f"(iteration {attempts})", "kind": "agent", "assignee": role, "status": "todo", "deps": [],
            "spec": {"inputs": list(self.pack.fix_inputs()), "with_context": False,
                     "feedback": f"Quality gate '{task['title']}' FAILED (iteration {attempts}). Fix the root cause "
                                 f"and return full corrected files.\n\n{result.get('output', '')[-6000:]}"},
            "created": now(), "updated": now()})
        self.store.release(task["id"], "todo", _slim(result), attempts=attempts, deps=task["deps"] + [fix_id])
        self._log("orchestrator", "task.routed", f"task:{task['key']}", to=role, iteration=attempts)
        self._event("route", reason=f"{task['key']}_failed", to=role, to_name=agent["name"] if agent else role,
                    iteration=attempts, gate=task["title"])

    def _gate_done(self, task, result):
        self._event("tool", **result)
        self.store.release(task["id"], "done", _slim(result))

    # ---- approvals
    def _run_approval_task(self, task: dict):
        done = [t for t in self.tasks() if t["status"] == "done" and t["result"]]
        gates = [t["result"] for t in done if t["kind"] == "gate"]
        reviews = {t["assignee"]: t["result"].get("verdict") for t in done if t["kind"] == "agent"
                   and t["result"].get("verdict")}
        findings = [f for t in done if t["kind"] == "agent" for f in (t["result"].get("findings") or [])]
        blocking = [k for k, v in reviews.items() if v in ("CHANGES_REQUESTED", "FAIL")]
        spend = self.store.one("SELECT COALESCE(SUM(cost_usd),0) s FROM runs WHERE task_id IN "
                               "(SELECT id FROM tasks WHERE goal_id=?)", self.goal["id"])["s"]
        payload = {"project": self.goal["title"], "gates": "; ".join(g.get("summary", "") for g in gates),
                   "verdicts": reviews, "blocking": blocking, "findings": findings, "spend_usd": round(spend, 4),
                   # IDE-compatible keys
                   "review": reviews.get("reviewer"), "security": reviews.get("security")}
        if not self.governance.release_approval and not blocking:
            self.store.release(task["id"], "done", {"approved": True, "approver": "policy", "comment": "auto"})
            return
        apr = self.governance.request("release" if not blocking else "escalation",
                                      f"Release '{self.goal['title']}'", payload, task_id=task["id"])
        self._apply_approval_to_task(task, apr)

    def _apply_approval_to_task(self, task: dict, apr: dict):
        if apr["status"] == "pending":
            return self._block(task, f"waiting for approval {apr['id']}", approval=apr["id"])
        approved = apr["status"] == "approved"
        if task["kind"] == "approval":
            self.store.release(task["id"], "done" if approved else "cancelled",
                               {"approved": approved, "approver": apr["decided_by"], "comment": apr["comment"]})
            self._event("human_gate", approved=approved, approver=apr["decided_by"], comment=apr["comment"],
                        time=apr["decided"])
            if not approved:
                self._cancel_remaining()

    def _block(self, task, reason, approval=None, **extra):
        spec = {**(task["spec"] or {}), "blocked_reason": reason, "approval_id": approval}
        self.store.release(task["id"], "blocked", spec=spec, **extra)
        self._log("orchestrator", "task.blocked", f"task:{task['key']}", reason=reason)

    def _on_budget(self, task, exc: BudgetExceeded):
        key = {"agent": task["assignee"], "goal": task["goal_id"], "company": self.company["id"]}[exc.scope]
        if exc.scope == "agent":
            with self.store.lock:
                self.store.db.execute("UPDATE agents SET status='paused' WHERE company_id=? AND key=?",
                                      (self.company["id"], task["assignee"]))
        self._log("budget", "budget.hard_stop", f"{exc.scope}:{exc.name}", spent=exc.spent, limit=exc.limit)
        self.out(f"  $$ {exc}")
        apr = self.governance.request("budget_override", f"{exc.scope.title()} budget exhausted: {exc.name}",
                                      {"scope": exc.scope, "key": key, "spent_usd": round(exc.spent, 4),
                                       "limit_usd": exc.limit, "increase_usd": max(exc.limit, 1.0)},
                                      task_id=task["id"])
        self._block(task, str(exc), approval=apr["id"])

    def _reconcile_blocked(self):
        """Unblock tasks whose approvals have been decided (possibly by another process)."""
        for t in self.store.all("SELECT * FROM tasks WHERE goal_id=? AND status='blocked'", self.goal["id"]):
            apr_id = (t["spec"] or {}).get("approval_id")
            apr = apr_id and self.store.one("SELECT * FROM approvals WHERE id=?", apr_id)
            if not apr or apr["status"] == "pending":
                continue
            spec = {**t["spec"], "approval_id": None, "blocked_reason": None}
            if t["kind"] == "approval":
                self.store.update("tasks", "id", t["id"], status="in_progress", spec=spec)
                self._apply_approval_to_task({**t, "spec": spec}, apr)
            elif apr["status"] == "approved":
                if apr["type"] == "gateway":
                    spec["gateway_approved"] = True
                if apr["type"] == "escalation":
                    self.store.update("tasks", "id", t["id"], attempts=0)
                self.store.update("tasks", "id", t["id"], status="todo", spec=spec)
                self._log("orchestrator", "task.unblocked", f"task:{t['key']}", approval=apr["id"])
            else:
                if apr["type"] == "gateway":
                    spec["gateway_approved"] = False
                    self.store.update("tasks", "id", t["id"], status="todo", spec=spec)
                else:
                    self.store.update("tasks", "id", t["id"], status="cancelled", spec=spec)
                    self._cancel_remaining()

    def _cancel_remaining(self):
        for t in self.tasks():
            if t["status"] in ("todo", "blocked"):
                self.store.update("tasks", "id", t["id"], status="cancelled", updated=now())

    # ------------------------------------------------------------ trace
    def _rel(self, p: Path) -> str:
        try:
            return Path(p).resolve().relative_to(self.ws.resolve()).as_posix()
        except ValueError:
            return str(p)

    def trace(self, status: str) -> dict:
        runs = self.store.all("SELECT * FROM runs WHERE task_id IN (SELECT id FROM tasks WHERE goal_id=?)",
                              self.goal["id"])
        steps = self.pack.playbook()
        nodes = [{"id": s.role if s.kind == "agent" else "human_gate" if s.kind == "approval" else s.key,
                  "ic": (s.role or s.key)[:2].upper(),
                  "nm": self.pack.roles[s.role].name if s.kind == "agent" else s.title.split(" (")[0],
                  "rl": self.pack.roles[s.role].description if s.kind == "agent" else
                  ("Real tools" if s.kind == "gate" else "Human decision"),
                  "para": s.key in ("build", "tests", "review", "security")} for s in steps]
        return {"run_id": self.goal["id"], "company": self.company["name"], "pack": self.pack.name,
                "platform": ", ".join(self.goal_stacks or self.pack.detected(self.ws)) or "universal",
                "stacks": self.goal_stacks or self.pack.detected(self.ws), "nodes": nodes, "story": self.goal["description"], "title": self.goal["title"],
                "status": status, "mode": "replay" if self.replay else "live",
                "model": "recorded" if self.replay else None, "workspace": str(self.ws.resolve()),
                "artifacts_dir": self._rel(self.artifacts),
                "spend_usd": round(sum(r["cost_usd"] or 0 for r in runs), 6),
                "tokens_estimated": any(r["tokens_estimated"] for r in runs),
                "started": self.goal["created"], "finished": now(), "events": self.events}


def _slim(result: dict) -> dict:
    return {k: v for k, v in result.items() if k != "output"} | {"output_tail": (result.get("output") or "")[-2000:]}


def _step(steps: list[Step], key: str) -> Step:
    return next(s for s in steps if s.key == key)


def _title_from(request: str) -> str:
    for line in request.splitlines():
        line = line.strip().strip("#").strip()
        if line and not re.match(r"^(request|feature request) from", line, re.I):
            return line[:90]
    return "Delivery"


def _model_of(agent: dict) -> str | None:
    raw = agent.get("adapter")
    try:
        raw = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return raw.get("model") if isinstance(raw, dict) else None
