# Agentic SDLC: AI Delivery Company for VS Code

Run a **company of AI agents** inside VS Code that delivers changes to *your* repository, on any technology, with
you accountable for every release.

Describe a change. The orchestrator plans it as tasks and assigns them to an org chart of Claude-powered agents:
Requirements, Architect, Developer, Tester, Code Review, Security and Release. It detects your stack, briefs every
agent with its conventions, and verifies each change with the stack's own build, analysis and test tools.
Failures are routed back to the agent that owns them. You approve hires, the release, budget overrides and risky
actions, and everything is recorded.

## Works with your stack, whatever it is

Detected automatically: **Java** (Maven, Gradle) · **.NET** · **Node.js / TypeScript** · **Python** · **Go** ·
**Rust** · **Ruby** · **PHP** · **Salesforce** (Apex, metadata) · **dbt** · **Terraform**, including polyglot repos
and greenfield projects. Add your own checks in `.agentic-sdlc/verify.json` for anything else.

## In the IDE

- **Projects**: every request is a project, broken into tasks with assignee, status and attempts. Open one in the
  run panel, create a replay page for stakeholders, or reveal its artifacts.
- **Approvals**: hiring, release, budget overrides, gateway actions and escalations. Approve or reject inline or
  in the run panel; the project resumes automatically.
- **Org chart**: seats, reporting lines, adapters and models, skills, and spend against budget.
- **Stack & verification**: what was detected in this repository and exactly which tools will verify the work,
  including tools that aren't installed.
- **Run panel**: live pipeline, each agent's output with code diffs, real tool output, fix loops and the audit trail.
- **Problems panel**: findings from the verification tools appear on the right lines.
- **Company dashboard**: the org chart, goals and projects, task cards, approvals, spend, gateway rules,
  routines and activity on one page.

## Getting started

1. Activity bar → **Agentic SDLC** → **Run a demo (no API key)** → pick Node, Python or Salesforce.
   The same playbook runs every stack. Approve the hires and then the release when asked.
2. **Agentic SDLC: Install engine dependencies**, then **Agentic SDLC: Set Anthropic API key** (stored in your OS keychain).
3. Open any repository and run **Run a business request…**, or right-click a `.md` story → **Run on this file**.
4. Review the diff on the new `agentic/…` branch, then commit and open a PR as usual. Your CI stays in charge.

**Agentic SDLC: Set up company…** creates `.agentic-sdlc/company.json`. Use it to change seats, models and adapters
(`claude_api`, `claude_code`, `http`), budgets, governance (which actions need approval) and routines.

## Requirements

- Python 3.11+ (the orchestrator is bundled with the extension)
- The tools of the stacks you use (npm, mvn/gradle, dotnet, pytest, go, cargo, PMD for Apex, …). Missing tools are
  reported as *skipped*, never as *passed*.
- An Anthropic API key for live runs; demos need none.

## Settings

| Setting | Default | |
|---|---|---|
| `agenticSdlc.approverName` | OS user | Name in the audit trail |
| `agenticSdlc.autoResume` | `true` | Resume once nothing is waiting for you |
| `agenticSdlc.createGitBranch` | `true` | New branch per live run |
| `agenticSdlc.maxFixIterations` | `3` | Gate retries before escalation |
| `agenticSdlc.companyStore` | per repo | Shared company database across repositories |
| `agenticSdlc.pythonPath` / `pmdPath` | auto | Tool locations |
| `agenticSdlc.salesforceTargetOrg` | – | Only for Apex repos: sandbox/scratch alias for environment validation |

## Data and security

- **What leaves your machine:** agents send the request, the generated docs and source, and a map of your
  existing code to the model provider configured per seat. They don't read production data.
- **Where outputs go:** source changes are written in place for review with git. Docs, CI proposals and release
  notes go to `.agentic-sdlc/projects/<id>/`, and the audit log lives in `.agentic-sdlc/company.db`. Add
  `.agentic-sdlc/` to `.gitignore` if you don't want them in the repository.
- **Risky actions:** the tool gateway (`allowed` / `ask_first` / `off`) controls them; production deployment is
  `off` by default.

The company model (org chart, goals, heartbeats, budgets, governance, audit) is inspired by Paperclip.
