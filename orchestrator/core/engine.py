"""The Universal Orchestrator engine: a task-and-heartbeat runtime for an AI company.

A delivery request becomes a *project goal* under the company's goals, and the playbook
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
from .adapters import AgentUnavailable, build_adapter
from .budget import BudgetExceeded, BudgetGuard, estimate_tokens, run_cost
from .company import goal_ancestry, load_skill
from .governance import Governance
from .playbook import Playbook, Step
from .store import Store, new_id, now

TERMINAL = {"done", "cancelled", "failed"}


def node_id(step) -> str:
    """UI node for a step: the agent's seat, the gate, or the human review (release approval = human_gate)."""
    if step.kind == "agent":
        return step.role
    if step.kind == "approval":
        return "human_gate" if step.approval_type == "release" else step.key
    return step.key


class Engine:
    def __init__(self, store: Store, company: dict, playbook: Playbook, workspace: Path, *, approver=None,
                 replay=None, max_fix_iterations: int = 3, log=print, on_event=None, gate_options: dict | None = None,
                 runtime: str = "api"):
        self.store, self.company, self.playbook = store, company, playbook
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
        self.runtime = runtime
        self.concurrency = (company.get("spec") or {}).get("concurrency", 4)
        if runtime == "claude-subscription":   # plans have usage limits: keep parallelism modest
            self.concurrency = min(self.concurrency, (company.get("spec") or {}).get("subscription_concurrency", 2))
        self.paused_reason: str | None = None
        self._adapters: dict[str, object] = {}
        self.goal: dict | None = None
        self.artifacts: Path | None = None
        self.events: list[dict] = []     # trace events (run panel, replay page)
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
                                    else spec.get("budgets", {}).get("project_usd"),
                                    "workspace": str(self.ws.resolve()), "created": now()})
        self.goal = self.store.one("SELECT * FROM goals WHERE id=?", gid)
        self.artifacts = self.ws / ".agentic-sdlc" / "projects" / gid
        self.artifacts.mkdir(parents=True, exist_ok=True)
        scaffolded = self.playbook.scaffold(self.ws)
        self._log("orchestrator", "project.created", f"goal:{gid}", title=title, playbook=self.playbook.name,
                  scaffolded=scaffolded)

        steps = self.playbook.steps(self.ws)
        fvars = self.playbook.feature_vars(self.ws, title)
        if fvars:
            self._log("orchestrator", "project.vars", f"goal:{gid}", **fvars)
        fmt = (lambda v: v.format(**fvars) if isinstance(v, str) else v) if fvars else (lambda v: v)  # noqa: E731
        first_agent = next((s.key for s in steps if s.receives_request), None) or \
            next(s.key for s in steps if s.kind == "agent")
        ids = {s.key: new_id("task") for s in steps}
        for s in steps:
            self.store.insert("tasks", {
                "id": ids[s.key], "company_id": self.company["id"], "goal_id": gid, "parent_id": None,
                "key": s.key, "title": fmt(s.title), "kind": s.kind, "assignee": s.role or "orchestrator",
                "status": "todo", "deps": [ids[d] for d in s.deps],
                "spec": {"inputs": [fmt(i) for i in s.inputs], "with_context": s.with_context,
                         "gateway_action": s.gateway_action, "vars": fvars,
                         "approval_type": s.approval_type, "document": fmt(s.document),
                         "revise_role": next((t.role for t in steps if t.key == s.revise_step), None),
                         "request": request if s.key == first_agent else None},
                "created": now(), "updated": now()})
        graph = {}
        for s in steps:   # one node per agent/gate; an agent with several steps keeps all their dependencies
            node = graph.setdefault(node_id(s), {"id": node_id(s), "after": []})
            node["after"] += [n for n in (node_id(_step(steps, d)) for d in s.deps)
                              if n not in node["after"] and n != node["id"]]
        self._event("plan", graph=list(graph.values()),
                    nodes=self.nodes(), title=title, project_id=gid)
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
        # A task left "in_progress" belongs to a process that ended (closed IDE, crash, cancel). Only one
        # engine drives a project at a time, so on resume such tasks are safely handed back to the queue.
        for t in self.store.all("SELECT * FROM tasks WHERE goal_id=? AND status='in_progress'", goal_id):
            self.store.release(t["id"], "todo")
            self._log("orchestrator", "task.recovered", f"task:{t['key']}", previous_owner=t["checked_out_by"])
        if self.replay is not None and hasattr(self.replay, "skip"):   # recorded runs continue where they stopped
            for r in self.store.all("SELECT agent_key, COUNT(*) AS n FROM runs WHERE task_id IN "
                                    "(SELECT id FROM tasks WHERE goal_id=?) GROUP BY agent_key", goal_id):
                self.replay.skip(r["agent_key"], r["n"])

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
            if self.paused_reason:
                self.out(f"  || paused: {self.paused_reason}")
                return self._finish("paused")

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
        except AgentUnavailable as exc:
            self.store.release(task["id"], "todo")
            self.paused_reason = str(exc)
            self._log("orchestrator", "agent.unavailable", f"task:{task['key']}", reason=str(exc))
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
            self._adapters[agent["key"]] = build_adapter(raw, self.replay, self.runtime)
        return self._adapters[agent["key"]]

    def _system_prompt(self, agent: dict) -> str:
        role = self.playbook.roles[agent["key"]]
        skills = []
        dirs = [self.ws / ".agentic-sdlc" / "skills"]
        for name in agent.get("skills") or []:
            try:
                skills.append(f"## Skill: {name}\n{load_skill(name, dirs)}")
            except FileNotFoundError:
                self._log("orchestrator", "skill.missing", f"agent:{agent['key']}", skill=name)
        for name in self.playbook.stack_skills(self.ws):   # conventions of every stack detected in the repo
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
        for k, v in ((task.get("spec") or {}).get("vars") or {}).items():
            lines.append(f"{k}: {v}")
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
            parts.append(self.playbook.project_context(self.ws))
            extra = self.playbook.task_context(self.ws, task, self.goal, bundle or self._files(("*",)))
            if extra:
                parts.append(extra)
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
        model = res.model if res.model and res.model not in ("recorded",) else \
            (getattr(adapter, "model", None) if getattr(adapter, "model", None) != "recorded" else None) or \
            _model_of(agent) or "claude-sonnet"
        # Claude Code reports its own cost; on a subscription it is the API-equivalent value used for budgets
        cost = round(res.cost_usd, 6) if res.cost_usd is not None else run_cost(model, tin, tout, self.pricing)

        paths = {}
        for rel, content in out.files.items():
            if ".." in Path(rel).parts or Path(rel).is_absolute():
                raise ValueError(f"{agent['name']} tried to write outside the workspace: {rel}")
            p = self.playbook.physical_path(rel, self.ws, self.artifacts)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            paths[rel] = self._rel(p)
            self.store.insert("work_products", {"id": new_id("wp"), "company_id": self.company["id"],
                                                "task_id": task["id"], "path": rel, "physical_path": paths[rel],
                                                "content": content, "created": now()})
        self.store.insert("runs", {"id": new_id("run"), "company_id": self.company["id"], "task_id": task["id"],
                                   "agent_key": agent["key"], "model": model,
                                   "adapter": f"{type(adapter).__name__}/{getattr(res, 'billing', 'api')}",
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
        run_files = {k for k in files if self.playbook.is_source(k)}
        opts = {**self.gate_options, "require_verification":
                bool((self.company.get("spec") or {}).get("governance", {}).get("require_verification"))}
        result = self.playbook.run_gate(task["key"], self.ws, run_files, files, **opts)
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

        role = self.playbook.route_fix(result, files)
        fix_id = new_id("task")
        agent = self._agent(role)
        self.store.insert("tasks", {
            "id": fix_id, "company_id": self.company["id"], "goal_id": task["goal_id"], "parent_id": task["id"],
            "key": f"fix-{task['key']}-{attempts}", "title": f"Fix {task['title'].split(' (')[0].lower()} findings "
            f"(iteration {attempts})", "kind": "agent", "assignee": role, "status": "todo", "deps": [],
            "spec": {"inputs": list(self.playbook.fix_inputs()), "with_context": False,
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
    def _run_document_approval(self, task: dict):
        """Human review of a document (spec, plan): approve, or request changes that the author revises."""
        spec = task["spec"] or {}
        kind, doc = spec.get("approval_type"), spec.get("document")
        if not self.governance.spec.get(f"{kind}_approval", True):
            self.store.release(task["id"], "done", {"approved": True, "approver": "policy", "comment": "auto"})
            return
        content = self._files(("*",)).get(doc, "")
        questions = [ln.strip() for ln in content.splitlines() if "NEEDS CLARIFICATION" in ln.upper()]
        author = next((t for t in reversed(self.tasks()) if t["status"] == "done" and t["kind"] == "agent"
                       and doc in ((t["result"] or {}).get("files") or [])), None)
        payload = {"project": self.goal["title"], "document": doc,
                   "document_path": str(self.playbook.physical_path(doc, self.ws, self.artifacts)) if doc else None,
                   "summary": ((author or {}).get("result") or {}).get("summary"),
                   "author": (self._agent(author["assignee"]) or {}).get("name") if author else None,
                   "questions": questions, "revision": task["attempts"],
                   "excerpt": content[:6000]}
        apr = self.governance.request(kind, f"Review {kind}: '{self.goal['title']}'"
                                      + (f" (revision {task['attempts']})" if task["attempts"] else ""),
                                      payload, task_id=task["id"])
        self._apply_approval_to_task(task, apr)

    def _run_approval_task(self, task: dict):
        if (task["spec"] or {}).get("approval_type", "release") != "release":
            return self._run_document_approval(task)
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
        if task["kind"] == "approval" and apr["type"] in ("spec", "plan"):
            return self._apply_document_decision(task, apr, approved)
        if task["kind"] == "approval":
            self.store.release(task["id"], "done" if approved else "cancelled",
                               {"approved": approved, "approver": apr["decided_by"], "comment": apr["comment"]})
            self._event("human_gate", approved=approved, approver=apr["decided_by"], comment=apr["comment"],
                        time=apr["decided"])
            if not approved:
                self._cancel_remaining()

    def _apply_document_decision(self, task: dict, apr: dict, approved: bool):
        spec = task["spec"] or {}
        doc = spec.get("document")
        if approved:
            if apr.get("comment") and doc:   # the approver's answers to open questions become part of the document
                path = self.playbook.physical_path(doc, self.ws, self.artifacts)
                if path.exists() and "NEEDS CLARIFICATION" in path.read_text(encoding="utf-8"):
                    text = path.read_text(encoding="utf-8").rstrip() + \
                        f"\n\n## Clarifications\n\n- {apr['decided'][:10]} ({apr['decided_by']}): {apr['comment']}\n"
                    path.write_text(text, encoding="utf-8")
                    self.store.insert("work_products", {"id": new_id("wp"), "company_id": self.company["id"],
                                                        "task_id": task["id"], "path": doc,
                                                        "physical_path": self._rel(path), "content": text,
                                                        "created": now()})
            self.store.release(task["id"], "done", {"approved": True, "approver": apr["decided_by"],
                                                    "comment": apr["comment"], "document": doc})
            self._event("human_gate", approved=True, approver=apr["decided_by"], comment=apr["comment"],
                        time=apr["decided"], document=doc, review=apr["type"], node=task["key"])
            return
        # changes requested: the author revises, then the same approval is asked again
        role = spec.get("revise_role")
        attempts = task["attempts"] + 1
        if not role or attempts > self.max_fix + 2:
            self.store.release(task["id"], "cancelled", {"approved": False, "approver": apr["decided_by"],
                                                         "comment": apr["comment"]})
            self._event("human_gate", approved=False, approver=apr["decided_by"], comment=apr["comment"],
                        time=apr["decided"], document=doc, review=apr["type"], node=task["key"])
            return self._cancel_remaining()
        revise_id = new_id("task")
        self.store.insert("tasks", {
            "id": revise_id, "company_id": self.company["id"], "goal_id": task["goal_id"], "parent_id": task["id"],
            "key": f"revise-{task['key']}-{attempts}", "title": f"Revise {doc} (review round {attempts})",
            "kind": "agent", "assignee": role, "status": "todo", "deps": [],
            "spec": {"inputs": ["*"], "with_context": True, "vars": spec.get("vars"),
                     "feedback": f"The human reviewer requested changes to {doc}:\n\n{apr['comment'] or '(no comment)'}"
                                 f"\n\nRevise {doc} (and any document that depends on it) and return the full files."},
            "created": now(), "updated": now()})
        self.store.release(task["id"], "todo", spec={**spec, "approval_id": None, "blocked_reason": None},
                           attempts=attempts, deps=task["deps"] + [revise_id])
        self._event("route", reason=f"{apr['type']}_changes_requested", to=role,
                    to_name=(self._agent(role) or {}).get("name", role), iteration=attempts,
                    gate=f"{apr['type'].title()} review")
        self._log(f"human:{apr['decided_by']}", "document.changes_requested", f"task:{task['key']}",
                  document=doc, comment=apr["comment"])

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

    def nodes(self) -> list[dict]:
        """Pipeline description for UIs (run panel, replay page)."""
        steps = self.playbook.steps(self.ws)
        siblings = {s.key for s in steps if sum(1 for t in steps if t.deps == s.deps) > 1}
        agent = self._agent
        seen, unique = set(), []
        for s in steps:
            if node_id(s) not in seen:
                seen.add(node_id(s))
                unique.append(s)
        steps = unique
        return [{"id": node_id(s),
                 "ic": {"requirements": "RQ", "architect": "AR", "developer": "DV", "tester": "TS", "reviewer": "CR",
                        "security": "SC", "release": "RL", "verify": "VF", "environment": "EN", "map": "GR",
                        "spec_coverage": "AC"}.get(
                     s.role or s.key, "HU" if s.kind == "approval" else (s.role or s.key)[:2].upper()),
                 "nm": (agent(s.role) or {}).get("name") or self.playbook.roles[s.role].name if s.kind == "agent"
                 else s.title.split(" (")[0],
                 "rl": self.playbook.roles[s.role].description if s.kind == "agent" else
                 ({"map": "Codebase knowledge graph (Graphify)", "spec_coverage": "Every acceptance criterion has a test"}.get(
                     s.key, "Runs the stack's own tools") if s.kind == "gate" else
                  "Accountable human decides"),
                 "para": s.key in siblings} for s in steps]

    def trace(self, status: str) -> dict:
        runs = self.store.all("SELECT * FROM runs WHERE task_id IN (SELECT id FROM tasks WHERE goal_id=?)",
                              self.goal["id"])
        nodes = self.nodes()
        return {"run_id": self.goal["id"], "company": self.company["name"], "playbook": self.playbook.name,
                "platform": ", ".join(self.goal_stacks or self.playbook.detected(self.ws)) or "universal",
                "stacks": self.goal_stacks or self.playbook.detected(self.ws), "nodes": nodes, "story": self.goal["description"], "title": self.goal["title"],
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
