# Architecture

## Principles

1. **The company is the product.** Agents are employees with roles, managers, budgets and skills. Work is tasks
   tied to goals. Humans hold the decisions that matter: hiring, release, budget overrides, risky actions and
   escalations.
2. **One playbook for every technology.** Roles are technology-neutral. Stack knowledge is *data*: detection rules,
   verification commands and convention skills. It is never a separate code path or product.
3. **Verify with real tools.** An agent's claim is never accepted as proof. Every change goes through the stack's
   own build, analysis and test tools. A check that cannot run is reported as *skipped*, never as *passed*.
4. **Everything is recorded.** Every wakeup, output, gate result, routing decision, approval and cost lands in an
   append-only audit log and a per-project trace.

## Components

| Module | Responsibility |
|---|---|
| `orchestrator/core/store.py` | SQLite store: companies, agents, goals, tasks, runs, approvals, work products, activity, routines. Atomic task checkout. |
| `core/company.py` | Company spec → org chart, goal tree, routines. Hiring requests. Goal ancestry. |
| `core/engine.py` | Scheduler and heartbeats: ready tasks wake their assignee and run in parallel waves. Agent runs, gates, fix routing, approvals, budget enforcement, traces. |
| `core/governance.py` | Approvals (hire, release, budget_override, gateway, escalation), approver strategies (deferred, CLI, IDE, auto), tool-gateway policies. |
| `core/budget.py` | Cost per run (token pricing), roll-up to agent, project and company; 80% warning and 100% hard stop. |
| `core/adapters.py` | Bring-your-own-agent: `claude_api`, `claude_code` (headless CLI), `http` (any bot), `replay`. Runtime `claude-subscription` routes Claude seats through Claude Code on the user's plan; an unavailable runtime pauses the project (`AgentUnavailable`). |
| `core/playbook.py`, `delivery.py` | The playbook abstraction and the universal delivery playbook (roles, steps, gates, routing, file layout). |
| `stacks/` | Stack detection, verification plans (blocking vs advisory, install → build → static → test → lint), environment checks, project context, convention skills. Platform-specific helpers (e.g. Apex PMD and org validation) live here as plug-ins. |
| `core/state.py`, `core/dashboard.py`, `core/replay_page.html`, `core/documents.py` | Read model for UIs, company dashboard, step-through replay page, requirements-document intake (Markdown, text, Word, PDF). |
| `agentic.py` | CLI: delivery (`run --requirements <doc>`, `resume`), people (`approvals`, `approve`), insight (`state`, `stacks`, `org`, `board`, `budget`, `activity`, `dashboard`, `replay-page`, `doctor`), admin (`init`, `routines`, `export`, `import`). `--json` and `--events` for integrations. |
| `vscode-extension/` | IDE client over the CLI: build from a requirements document (existing repo or new folder), views, run panel, approvals, diagnostics, dashboard. |

## A project's life

```
requirements document (.md/.docx/.pdf) ─▶ text with headings and tables ─▶ project goal (under a company goal, under the mission)
        ─▶ tasks from the playbook, each assigned to a seat
        ─▶ hiring approvals for seats not yet hired               (run pauses; humans decide; resume)
        ─▶ heartbeat waves: Requirements → Architect → Developer ∥ Tester
        ─▶ VERIFY gate: detect stacks; run install/build/static/test (blocking), lint (advisory)
              fail ─▶ fix task for the owner (developer for failing tests, tester for broken test code,
                      file owner for static findings) ─▶ VERIFY again … max N ─▶ escalation approval
        ─▶ ENVIRONMENT gate (tool gateway `environment.validate`): stack environment checks
           (e.g. org deploy-validate) and operator checks from verify.json
        ─▶ Reviewer ∥ Security ─▶ release approval (gates, verdicts, findings, spend)
        ─▶ Release agent: CI proposal, manifests, release notes ─▶ trace.json
```

Source, tests and config are written into the repository in place, so you review them with `git diff`. Docs, CI
proposals, manifests and release notes go to `.agentic-sdlc/projects/<id>/`, so an existing setup is never
overwritten.

## Paperclip concepts in this system

| Paperclip | Agentic SDLC |
|---|---|
| Company (multi-company) | `companies` table; a spec per company; one store can serve many repositories (`--store`). |
| Org chart, roles, reporting lines | Seats with title, `reports_to`, adapter, model, budget and skills; shown in the IDE's **Org chart** view and the dashboard. |
| Hiring requires approval | Every seat starts `pending_approval`; a `hire` approval activates it. |
| Goals and goal ancestry | Mission → goal → project; every agent prompt starts with the ancestry. |
| Tasks with atomic checkout | `tasks` with dependencies, one assignee and `UPDATE … WHERE status='todo' AND checked_out_by IS NULL`. |
| Heartbeats and wakeups | The scheduler wakes the assignee when a task becomes ready; independent tasks run in parallel; wakeups are logged. |
| Budgets with warnings and hard stops | Per agent, project and company; a warning at 80%; at 100% the agent is paused and a `budget_override` approval opens. |
| Governance: approvals, pause/resume | Deferred approvals pause the project; deciding (CLI or IDE) and resuming continues it, even in another process. |
| Tool gateway (Allowed / Ask first / Off) | `governance.gateway` per action: `environment.validate`, `deploy.production`, `git.push`. |
| Bring your own agent (adapters) | `claude_api`, `claude_code`, `http`, `replay` per seat. |
| Skills | Company skills plus automatically attached stack skills, loaded at runtime. |
| Routines | `@daily`, `@weekly`, `@monthly`, `@every`; each due routine starts a project. |
| Activity log / audit | Append-only `activity` table, plus `trace.json` per project. |
| Company portability | `export` / `import` with skills bundled and secrets stripped. |
| Web UI | VS Code views and run panel, the company dashboard, and replay pages. |

What is specific to software delivery: the playbook, quality gates that run real tools, fix routing,
stack detection and verification plans, and IDE-native review (git branch per run, diagnostics, diffs).
