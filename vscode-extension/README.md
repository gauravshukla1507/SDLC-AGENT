# Agentic SDLC for Salesforce (VS Code)

Turn a business request into **verified Salesforce source** inside VS Code. An orchestrator coordinates seven
Claude-powered agents through real quality gates, and **you approve** before anything is final.

| Agent | Produces |
|---|---|
| Salesforce BA | User story, acceptance criteria, objects/fields, access |
| Solution Architect | Clicks vs code, data model, trigger/handler/service design, security, limits budget |
| Apex Developer | Custom fields, permission set, trigger, handler, service (+ meta.xml) |
| Apex Test | `@IsTest` classes from the acceptance criteria: bulk (200), negative, `System.runAs` |
| Code Review / Security | Findings + verdicts (best practices, CRUD/FLS, sharing, injection) |
| Salesforce DevOps | `package.xml`, GitHub Actions pipeline, release notes |

**Quality gates:** metadata checks → **PMD Apex rules** (the engine behind Salesforce Code Analyzer) →
**org validation** (`sf project deploy validate` + Apex tests + coverage, when a sandbox/scratch org is configured).
A failing gate is routed back to the agent that owns the failing files, at most 3 times before it escalates to you.

## Features

- **Live run panel**: pipeline status, every agent's output with code diffs, gate results, and the audit trail.
- **Human approval in VS Code**: approve or reject (with a comment) from the panel or the notification. The decision is logged.
- **Problems panel**: PMD and metadata findings appear as diagnostics on the generated files.
- **Safe for existing projects**:
  - Each live run starts on a new git branch.
  - Agents see the classes, triggers and fields that already exist (so they extend your trigger framework instead of adding a second trigger).
  - Gates only check the files written in the run.
  - Nothing outside `force-app/` is overwritten. Docs, manifest, CI files and the trace go to `.agentic-sdlc/runs/<id>/`.
- **Client demo export**: one click produces a self-contained HTML replay of any run.
- **Demo mode**: replays a recorded run with real gates. No API key needed.

## Requirements

- Python 3.11+, then run **Agentic SDLC: Install engine dependencies**
- Java 17+ and **PMD 7** on PATH (or set `agenticSdlc.pmdPath`)
- An Anthropic API key: **Agentic SDLC: Set Anthropic API key** (stored in the OS keychain via SecretStorage)
- Optional: Salesforce CLI (`sf`) and an authenticated **sandbox or scratch org** alias in `agenticSdlc.targetOrg`

## Use

1. Open your SFDX project.
2. Command Palette → **Agentic SDLC: Run on a business request…** Or right-click a `.md`/`.txt` story → **Run on this file**,
   or select text → **Run on selected text**.
3. Watch the run panel. Fix nothing by hand; the agents loop on gate failures.
4. Approve or reject at the human gate.
5. Review the diff in Source Control, then commit and open a PR as usual. Your CI and release process stay in charge.

Try **Agentic SDLC: Run demo** first. It runs in a temporary folder and needs no key.

## Settings

| Setting | Default | |
|---|---|---|
| `agenticSdlc.model` | `claude-sonnet-4-5` | Claude model id |
| `agenticSdlc.targetOrg` | – | sf alias for the org gate (never production) |
| `agenticSdlc.minCoverage` | 75 | Apex coverage threshold |
| `agenticSdlc.maxFixIterations` | 3 | Gate retries before escalation |
| `agenticSdlc.createGitBranch` | true | New branch per live run |
| `agenticSdlc.pmdPath` / `pythonPath` | auto | Tool locations |
| `agenticSdlc.approverName` | OS user | Name in the audit trail |
| `agenticSdlc.recordRuns` | false | Save runs for offline replay demos |

## Data and security

- The agents send the business request, the generated docs and the names of your existing components to the
  Anthropic API, plus source of the files they write. They do not read org data.
- The org gate deploys in **validate-only** mode with the run's components. Point it at a sandbox or scratch org.
- Every agent output, gate result and approval is recorded in `.agentic-sdlc/runs/<id>/trace.json`.
  Add `.agentic-sdlc/` to `.gitignore` if you don't want traces in the repo.
