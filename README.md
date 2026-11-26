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
For real work, set your key (**Agentic SDLC: Set Anthropic API key**) and use **Run a business request…** in any repository.

### Command line
```bash
cd orchestrator && pip install -r requirements.txt
python agentic.py demo --sample node -w /tmp/demo              # or python | salesforce
python agentic.py stacks -w ~/work/any-repo                     # detected stacks + verification plan
python agentic.py run --request-file story.md -w ~/work/any-repo --approver deferred
python agentic.py approvals -w ~/work/any-repo && python agentic.py approve all --as "Name" -w ~/work/any-repo
python agentic.py resume -w ~/work/any-repo --approver deferred
python agentic.py dashboard -w ~/work/any-repo
```

## Development

| Component | Test |
|---|---|
| orchestrator | `cd orchestrator && python -m pytest -q` (32 tests) |
| vscode-extension | `cd vscode-extension && npm ci && npm test` (runner protocol and extension smoke test); `npm run test:webview` (needs Playwright) |

CI (`.github/workflows/ci.yml`) runs both and publishes the `.vsix` as a build artifact.

## Status

- **Recorded demos:** the bundled demos (Salesforce, Python, Node) replay recorded agent answers; the verification
  tools run for real. Live runs need an Anthropic API key.
- **Verified so far:** stacks without a demo (Java, .NET, Go, Rust, Ruby, PHP, dbt, Terraform) are covered by
  detection tests. Confirm their verification commands on a real repository before relying on them.
- **Adapters:** the `claude_code` and `http` adapters haven't been run against live services yet.
