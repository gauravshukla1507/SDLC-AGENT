# Agentic SDLC: an AI delivery company for any technology

Agentic SDLC runs a **company of AI agents** that delivers software changes end to end, on any stack, with humans
accountable for every release. You describe a change. The orchestrator plans it, assigns it across an org chart of
Claude-powered agents, verifies every step with your stack's own tools, enforces budgets and approvals, and keeps a
full audit trail.

The company model (org chart, goals, task checkout and heartbeats, budgets, governance, bring-your-own-agent, audit)
follows the ideas of [Paperclip](https://github.com/paperclipai/paperclip), applied to the software delivery lifecycle.
See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

```
            VS Code extension (Projects · Approvals · Org chart · Stack views, run panel, dashboard)
                                              │
                                     orchestrator/agentic.py
 ┌───────────────────────────────────── the company ──────────────────────────────────────┐
 │ mission → goals → projects      org chart (hired via approval)       governance          │
 │                                 Delivery Director (human)            approvals, gateway   │
 │ tasks: atomic checkout,         ├ Architect ┬ Developer              budgets (hard stop)  │
 │ heartbeats, parallel waves      │           ├ Tester                 routines, skills     │
 │                                 │           └ Reviewer               append-only audit    │
 │                                 ├ Requirements, Security, Release                         │
 └───────────────────────────────────────────┬─────────────────────────────────────────────┘
                                              │ one playbook for every stack
   spec-driven (default):  MAP CODEBASE (Graphify) → constitution → SPEC → you review → PLAN + TASKS → you review
                           → Tester (tests first) ∥ Developer → VERIFY → SPEC COVERAGE → ENVIRONMENT
                           → Reviewer ∥ Security (with blast radius) → human approval → Release
   classic:                Requirements → Architect → Developer ∥ Tester → VERIFY → ENVIRONMENT → Reviewer ∥ Security → approval → Release
                                              ▲
   repository → stack detection → stack profile + conventions (skills) + verification plan (the stack's own tools)
```

## Spec-driven development (default)

The team develops **spec-first**: the specification is the source of truth, and code and tests are derived from it
and checked against it. Files follow the [GitHub Spec Kit](https://github.com/github/spec-kit) layout and are
committed in your repository:

| File | Written by | Content |
|---|---|---|
| `.specify/memory/constitution.md` | Architect (once per repository) | Non-negotiable principles derived from your code base and conventions |
| `specs/NNN-feature/spec.md` | Requirements agent | User stories, acceptance criteria `AC-n` (Given/When/Then), FRs, success criteria, `[NEEDS CLARIFICATION]` questions, traceability. No technology. |
| `specs/NNN-feature/plan.md` | Architect | Technical context, constitution check, existing components to reuse, design, files, risks/blast radius |
| `specs/NNN-feature/tasks.md` | Architect (Developer ticks them off) | Ordered, test-first tasks `T001…`, `[P]` = parallel, each linked to AC ids |

- **You review the spec and the plan** before any code is written. *Approve* (your comment answers the open
  questions and is added to the spec as a clarification) or *Request changes* (your comment goes back to the author,
  who revises the document; you're asked again).
- **Tests first:** the Test agent writes a test for every acceptance criterion from the spec alone, naming its AC id.
- **Spec coverage gate:** after verification, every `AC-n` in the spec must be referenced by a test; missing ones
  go back to the Test agent.
- Turn the reviews off per company with `"governance": {"spec_approval": false, "plan_approval": false}`.
- The previous flow is still available: `--playbook classic` (or the `agenticSdlc.playbook` setting).

## Existing products: codebase graph (Graphify)

When you work on an existing code base, the first step maps it into a knowledge graph with
[Graphify](https://pypi.org/project/graphifyy/) (`pip install graphifyy`; local AST extraction, no API calls, the
`graphify-out/` folder is added to `.gitignore`). The agents then get:

- the **architecture summary** (god nodes, communities, import cycles) for the constitution, plan and review;
- the **existing code related to the feature** (graph query from the spec) so the plan reuses and extends it;
- the **blast radius** of every changed existing file (callers and dependants) for code review and security.

New applications skip the step, and so does a machine without Graphify (shown as *skipped*, never as passed).
`AGENTIC_GRAPHIFY=off` (or the `agenticSdlc.codebaseGraph` setting) disables it. From the CLI:
`agentic.py graph build|status|query <question>|affected <files>|summary -w <repo>`.

## Use your Claude subscription (no API key)

The agents can run on your **Claude Pro, Max, Team or Enterprise plan** through
[Claude Code](https://docs.claude.com/en/docs/claude-code): install it once, sign in with `/login`, and select the
subscription runtime. This is the default in the VS Code extension.

```bash
npm install -g @anthropic-ai/claude-code && claude      # then type /login and pick your Claude account
python orchestrator/agentic.py doctor --runtime claude-subscription --probe
export AGENTIC_RUNTIME=claude-subscription               # or pass --runtime claude-subscription
```

- Each agent turn is one headless Claude Code call (`claude -p`). Agents may **read** the repository
  (Read/Glob/Grep) but cannot edit files or run commands; the orchestrator writes their outputs and runs verification.
- `ANTHROPIC_API_KEY` is removed from the agents' environment, so the run uses your plan rather than API billing.
- Your plan's usage limits apply. The orchestrator runs at most 2 agents in parallel on a subscription, and if
  you hit a limit or aren't signed in, the project **pauses** (nothing is lost) and continues with `resume`.
- Budgets and the dashboard show the API-equivalent value that Claude Code reports, labelled as plan usage.

Prefer per-token billing? Use `--runtime api` with `ANTHROPIC_API_KEY`, or set `agenticSdlc.agentRuntime` to `apiKey`.

## What it works with

The stack is **detected, never configured**: Salesforce (Apex/metadata), Java (Maven, Gradle), .NET, Node.js and
TypeScript, Python, Go, Rust, Ruby, PHP, dbt and Terraform, including polyglot repositories and greenfield projects.
Anything else can be verified with your own commands in `.agentic-sdlc/verify.json`.

## Repository layout

| Folder | What it is |
|---|---|
| [`orchestrator/`](orchestrator/) | The engine and CLI (`agentic.py`): company store, playbook, stack detection and verification, governance, budgets, adapters, dashboard, tests |
| [`vscode-extension/`](vscode-extension/) | **Agentic SDLC** for VS Code: activity-bar views, live run panel, approvals in the IDE, diagnostics, dashboard. It bundles the orchestrator at build time. |
| [`docs/`](docs/) | Architecture and the Paperclip mapping |
| [`examples/`](examples/) | A sample requirements document (BRD) to try it with |

## Quick start

### VS Code
```bash
cd vscode-extension && npm ci && npm run package
code --install-extension agentic-sdlc-*.vsix
```
1. **Agentic SDLC: Sign in to Claude (use my subscription)** (once), then **Agentic SDLC: Check setup**.
2. **Agentic SDLC: Build application from requirements document…**: pick your BRD/PRD (`.md`, `.docx`, `.pdf`),
   then choose *this workspace* (extend an existing code base) or *a new folder* (new application).
3. Approve the hires, follow the run panel, approve the release, then review the diff and commit.

### Command line
```bash
cd orchestrator && pip install -r requirements.txt
python agentic.py doctor --runtime claude-subscription --probe
python agentic.py run --runtime claude-subscription --requirements ../examples/BRD-expense-claim-policy-engine.md \
       -w ~/work/expense-engine --approver deferred            # new folder = new application
python agentic.py approvals -w ~/work/expense-engine && python agentic.py approve all --as "Name" -w ~/work/expense-engine
python agentic.py resume --runtime claude-subscription -w ~/work/expense-engine --approver deferred
python agentic.py dashboard -w ~/work/expense-engine
```

## Development

| Component | Test |
|---|---|
| orchestrator | `cd orchestrator && python -m pytest -q` (51 tests: classic and spec-driven playbooks, spec revision loop, spec coverage, Graphify, subscription runtime against a stand-in Claude Code CLI) |
| vscode-extension | `cd vscode-extension && npm ci && npm test` (runner protocol, spec-driven flow with *request changes*, extension smoke test); `npm run test:webview` (needs Playwright) |

CI (`.github/workflows/ci.yml`) runs both and publishes the `.vsix` as a build artifact.

## Status

- **Recorded test runs:** the automated tests replay recorded agent answers (Salesforce, Python and Node fixtures)
  while running the real verification tools. Production runs use your Claude subscription or an API key.
- **Other stacks:** stacks without an end-to-end fixture (Java, .NET, Go, Rust, Ruby, PHP, dbt, Terraform) are
  covered by detection tests. Confirm their verification commands on a real repository before relying on them.
- **Live Claude Code:** the subscription runtime is tested against a stand-in Claude Code CLI that uses the real JSON
  format. Do one `doctor --probe` and one small run on your machine first.
