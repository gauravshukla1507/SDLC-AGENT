# Agentic SDLC: AI Delivery Company for VS Code

Run a **company of AI agents** inside VS Code that delivers changes to *your* repository, on any technology, with
you accountable for every release.

Give it a requirements document (BRD, PRD or user stories in Markdown, Word or PDF). The orchestrator plans it as tasks and assigns them to an org chart of Claude-powered agents:
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

1. **Use your Claude subscription:** run **Agentic SDLC: Sign in to Claude (use my subscription)**. It installs Claude
   Code if needed and opens it; type `/login` and choose your Pro, Max, Team or Enterprise account. Then run
   **Agentic SDLC: Check setup**. No API key is needed. (Prefer per-token billing? Set `agenticSdlc.agentRuntime`
   to `apiKey` and use **Set Anthropic API key**.)
2. **Build from your requirements:** run **Agentic SDLC: Build application from requirements document…**, or
   right-click a `.md`, `.docx` or `.pdf` in the Explorer. Pick where to build:
   - **This workspace:** extend the existing code base. Its stack is detected and its conventions followed.
   - **A new folder:** create a new application. The architect chooses the stack from the document, and the folder
     becomes a git repository.
3. Approve the hires (once per repository), follow the run panel, then approve the release.
4. Review the diff on the new `agentic/…` branch, commit and open a PR as usual. Your CI stays in charge.

For a small change in the current repository, use **Agentic SDLC: Describe a change…** or select text →
**Run on selected text**.

**Agentic SDLC: Set up company…** creates `.agentic-sdlc/company.json`. Use it to change seats, models and adapters
(`claude_api`, `claude_code`, `http`), budgets, governance (which actions need approval) and routines.

## Requirements

- Python 3.11+ (the orchestrator is bundled with the extension)
- A Claude subscription with [Claude Code](https://docs.claude.com/en/docs/claude-code) signed in, or an Anthropic API key
- The tools of the stacks you use (npm, mvn/gradle, dotnet, pytest, go, cargo, PMD for Apex, …). Missing tools are
  reported as *skipped*, never as *passed*.

## Settings

| Setting | Default | |
|---|---|---|
| `agenticSdlc.agentRuntime` | `claudeSubscription` | Run agents on your Claude plan via Claude Code, or `apiKey` |
| `agenticSdlc.claudeCodePath` | `claude` | Claude Code CLI location |
| `agenticSdlc.approverName` | OS user | Name in the audit trail |
| `agenticSdlc.autoResume` | `true` | Resume once nothing is waiting for you |
| `agenticSdlc.createGitBranch` | `true` | New branch per live run |
| `agenticSdlc.maxFixIterations` | `3` | Gate retries before escalation |
| `agenticSdlc.companyStore` | per repo | Shared company database across repositories |
| `agenticSdlc.pythonPath` / `pmdPath` | auto | Tool locations |
| `agenticSdlc.salesforceTargetOrg` | – | Only for Apex repos: sandbox/scratch alias for environment validation |

## Claude subscription details

- **One call per turn:** each agent turn is one headless Claude Code call. Agents can read your repository but
  can't edit files or run commands; the orchestrator writes their output and runs the verification tools.
- **Your plan, not an API key:** `ANTHROPIC_API_KEY` is removed from the agents' environment, so usage counts
  against your plan.
- **Usage limits:** at most 2 agents work in parallel. If you reach your plan's limit or aren't signed in, the
  project **pauses** and offers *Sign in to Claude* or *Resume*. Nothing is lost.
- **What spend shows:** the API-equivalent value Claude Code reports, labelled as plan usage.

## Data and security

- **What leaves your machine:** agents send the request, the generated docs and source, and a map of your
  existing code to the model provider configured per seat. They don't read production data.
- **Where outputs go:** source changes are written in place for review with git. Docs, CI proposals and release
  notes go to `.agentic-sdlc/projects/<id>/`, and the audit log lives in `.agentic-sdlc/company.db`. Add
  `.agentic-sdlc/` to `.gitignore` if you don't want them in the repository.
- **Risky actions:** the tool gateway (`allowed` / `ask_first` / `off`) controls them; production deployment is
  `off` by default.

The company model (org chart, goals, heartbeats, budgets, governance, audit) is inspired by Paperclip.
