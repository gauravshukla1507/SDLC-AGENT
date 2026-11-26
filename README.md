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
   Requirements → Architect → Developer ∥ Tester → VERIFY → ENVIRONMENT → Reviewer ∥ Security → human approval → Release
                                              ▲
   repository → stack detection → stack profile + conventions (skills) + verification plan (the stack's own tools)
```

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
| [`orchestrator/`](orchestrator/) | The engine and CLI (`agentic.py`): company store, playbook, stack detection and verification, governance, budgets, adapters, dashboard, demo samples, tests |
| [`vscode-extension/`](vscode-extension/) | **Agentic SDLC** for VS Code: activity-bar views, live run panel, approvals in the IDE, diagnostics, dashboard. It bundles the orchestrator at build time. |
| [`docs/`](docs/) | Architecture and the Paperclip mapping, plus stakeholder demo pages (`docs/demos/`) |

## Quick start

### VS Code
```bash
cd vscode-extension && npm ci && npm run package
code --install-extension agentic-sdlc-*.vsix
```
Open the **Agentic SDLC** view in the activity bar → **Run a demo (no API key)** → pick Node, Python or Salesforce.
Approve the hires and the release when asked, and watch the same playbook deliver all three stacks.
For real work, run **Agentic SDLC: Sign in to Claude (use my subscription)** once (or set an API key), then **Check setup**, then **Run a business request…** in any repository.

### Command line
```bash
cd orchestrator && pip install -r requirements.txt
python agentic.py demo --sample node -w /tmp/demo              # or python | salesforce
python agentic.py stacks -w ~/work/any-repo                     # detected stacks + verification plan
python agentic.py run --runtime claude-subscription --request-file story.md -w ~/work/any-repo --approver deferred
python agentic.py approvals -w ~/work/any-repo && python agentic.py approve all --as "Name" -w ~/work/any-repo
python agentic.py resume --runtime claude-subscription -w ~/work/any-repo --approver deferred
python agentic.py dashboard -w ~/work/any-repo
```

## Development

| Component | Test |
|---|---|
| orchestrator | `cd orchestrator && python -m pytest -q` (35 tests, including the subscription runtime against a stand-in Claude Code CLI) |
| vscode-extension | `cd vscode-extension && npm ci && npm test` (runner protocol and extension smoke test); `npm run test:webview` (needs Playwright) |

CI (`.github/workflows/ci.yml`) runs both and publishes the `.vsix` as a build artifact.

## Status

- **Recorded demos:** the bundled demos (Salesforce, Python, Node) replay recorded agent answers; the verification
  tools run for real. Live runs need a Claude subscription signed in to Claude Code, or an Anthropic API key.
- **Verified so far:** stacks without a demo (Java, .NET, Go, Rust, Ruby, PHP, dbt, Terraform) are covered by
  detection tests. Confirm their verification commands on a real repository before relying on them.
- **Live Claude Code:** the subscription runtime is tested against a stand-in for the Claude Code CLI that uses the real JSON format. Do one probe (`doctor --probe`) and one small live run on your machine first. The `http` adapter hasn't been run against a live service yet.
