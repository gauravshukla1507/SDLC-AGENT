# Orchestrator (engine and CLI)

**One AI delivery company, one playbook, any technology.** You don't choose a stack. The orchestrator reads
the repository, works out what it is (Salesforce, Java, .NET, Node/TypeScript, Python, Go, Rust, Ruby, PHP,
dbt, Terraform, or several at once), gives the agents that stack's conventions, and verifies their work with the
stack's own build, analysis and test tools.

Company concepts (org chart, goals, tasks and heartbeats, budgets, governance, audit, bring-your-own-agent) follow
[Paperclip](https://github.com/paperclipai/paperclip); see [../docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md). The VS Code
extension in `../vscode-extension` is a client of this CLI (it uses `--events`, `--json` and deferred approvals).

```
 request ─▶ project goal ─▶ one playbook (same for every stack)
                              Requirements → Architect → Developer ∥ Tester → VERIFY → ENVIRONMENT
                              → Reviewer ∥ Security → human release approval → Release
                                                        ▲
 repository ─▶ stack detection ─────────────────────────┘
               ├─ stack profile + existing-code inventory → every agent's prompt
               ├─ stack skills (skills/stacks/<stack>.md)  → every agent's prompt
               └─ verification plan: install → build → static → tests (blocking), lint (advisory)
                  + operator checks from .agentic-sdlc/verify.json
```

## How it works across stacks

| Step | What happens |
|---|---|
| **Detect** | Marker files decide the stack: `sfdx-project.json`/`force-app/`, `pom.xml`, `build.gradle(.kts)`, `*.sln`/`*.csproj`, `package.json` (+`tsconfig.json`), `pyproject.toml`/`*.py`, `go.mod`, `Cargo.toml`, `Gemfile`, `composer.json`, `dbt_project.yml`, `*.tf`. Detection runs again after the agents build, so **greenfield** projects are detected from what the architect chose. Polyglot repos get every matching stack. |
| **Brief the agents** | Every agent gets the stack profile, a map of the existing code (reuse it, don't duplicate it) and the stack's skill file. Stack knowledge lives in skills; the role prompts are technology-neutral. |
| **Verify** | The **verify** gate runs the detected checks in order: install, build, static analysis, tests, then advisory lint. A check whose tool isn't installed is **skipped and reported**, never treated as passed. |
| **Environment** | The **environment** gate runs sandbox-level checks contributed by the detected stacks (for example Apex org deploy-validate with `--target-org`) plus anything you add under `environment` in `verify.json` (e.g. a staging smoke test). It is controlled by the gateway action `environment.validate`. |
| **Fix routing** | A failing test run goes to the **developer**, because the tests are the spec. If the test code itself is broken (collection, syntax or import errors), it goes to the **tester**. Static findings go to whoever owns the flagged files. After N attempts the failure escalates to a human. |
| **No verification?** | If nothing can verify a stack (no tests, tools missing), the gate says so in the release approval. With `governance.require_verification: true`, the project escalates to a human instead of continuing. |

### Built-in verification

| Stack | Blocking checks | Advisory |
|---|---|---|
| Salesforce | metadata checks, PMD Apex rules; env: `sf project deploy validate` + Apex tests + coverage | – |
| Java (Maven / Gradle) | `mvn -q -B verify` / `gradle check` | – |
| .NET | `dotnet build`, `dotnet test` | – |
| Node / TypeScript | install (if deps), `build`, `typecheck`, `test` npm scripts (npm, pnpm or yarn) | `lint` |
| Python | `pytest` (with `src/` layout support) | `ruff` |
| Go | `go vet`, `go test ./...` | – |
| Rust | `cargo test` | `clippy` |
| Ruby / PHP | `rspec` or `rake test` / `composer test` or `phpunit` | – |
| dbt / Terraform | `dbt parse` / `terraform validate` | `terraform fmt -check` |
| Anything else | your commands in `.agentic-sdlc/verify.json` | |

```json
// .agentic-sdlc/verify.json (optional, committed in the client repo)
{
  "checks": [{"name": "contract tests", "cmd": "make contract-test", "blocking": true, "kind": "test"}],
  "replace_detected": false,
  "environment": [{"name": "staging smoke", "cmd": "./scripts/smoke.sh staging", "blocking": true}]
}
```

## The company

| Concept | What it does here |
|---|---|
| **Company and org chart** | One spec, `companies/default-company.json`, with technology-neutral seats (Requirements, Architect, Developer, Tester, Reviewer, Security, Release) and a human Delivery Director. New seats are **hired through approval**. |
| **Goals → projects** | Every request becomes a project under a goal under the mission. Agents receive this ancestry. |
| **Tasks and heartbeats** | Each playbook step is a task with dependencies and one assignee. Checkout is atomic; independent tasks run in parallel. |
| **Approvals** | `hire`, `release`, `budget_override`, `gateway` and `escalation`. They can be **deferred**: the run pauses, someone runs `agentic.py approve`, then `agentic.py resume`, even from another machine sharing the store. |
| **Tool gateway** | `allowed` / `ask_first` / `off` per action (`environment.validate`, `deploy.production`, `git.push`). |
| **Budgets** | Cost per run, rolled up to agent, project and company. A warning at 80%; at 100% the agent is paused and a `budget_override` approval opens. |
| **Adapters and runtimes** | Per seat: `claude_api`, `claude_code`, `http` (any bot) or `replay`. Use `--runtime claude-subscription` (or `$AGENTIC_RUNTIME`) to run every Claude seat through Claude Code on your **Claude subscription**. Agents get read-only tools, the API key is stripped, parallelism is limited to 2, and the project pauses if you aren't signed in or hit a limit. `agentic.py doctor --probe` checks the setup. |
| **Skills** | Company skills (`team-conventions`, `secure-coding`, `release-notes-style`) plus automatic stack skills. |
| **Routines, export/import, audit, dashboard** | Recurring projects, portable company files without secrets, an append-only activity log, and a one-page HTML dashboard. |

One company can serve many repositories on different stacks. Share one store with `--store` or `$AGENTIC_STORE`.

## Quick start

```bash
cd orchestrator
pip install -r requirements.txt        # plus the tools of the stacks you use (PMD+Java for Salesforce, node, mvn, …)

# Recorded demos (agent answers replayed, verification runs for real), all in one company
for s in salesforce python node; do python agentic.py demo --sample $s -w /tmp/agentic-$s --store /tmp/agentic-company.db; done
python agentic.py dashboard -w /tmp --store /tmp/agentic-company.db --out /tmp/agentic-dashboard.html && open /tmp/agentic-dashboard.html

# What would it do with my repo?
python agentic.py stacks -w ~/work/any-repo

# Live
export AGENTIC_RUNTIME=claude-subscription   # your Claude plan via Claude Code (or: export ANTHROPIC_API_KEY=...)
python agentic.py run --company companies/default-company.json --request-file story.md -w ~/work/any-repo --approver deferred
python agentic.py approvals -w ~/work/any-repo
python agentic.py approve all --as "Gaurav" --comment "Pilot approved" -w ~/work/any-repo
python agentic.py resume -w ~/work/any-repo --approver deferred
```

Other commands: `state --json`, `org`, `board`, `budget`, `activity`, `dashboard`, `demo-page <trace>`, `init`, `routines [--run-due]`, `export --out f.json`, `import f.json`, `playbook`.

## Where outputs go

Source, config and tests are written into the repository in place, so you review them with `git diff`.
Docs, proposed CI, deployment manifests and release notes go to `.agentic-sdlc/projects/<id>/`, so they never
overwrite the client's existing CI or docs.

## Tests

`python -m pytest -q` runs 35 tests:

- **Delivery:** Salesforce, Python and Node delivered end to end through the same playbook, and one company serving several stacks.
- **Detection:** 12 detection cases (Java Maven and Gradle, .NET, Go, Rust, TypeScript, Ruby, PHP, dbt, Terraform, Salesforce, Python), a polyglot repo with operator overrides, a greenfield repo, and missing tools reported as skipped.
- **Fix routing:** the rules for who fixes a failed check.
- **CLI:** the deferred-approval flow the IDE uses, with JSON output.
- **Runtime:** the Claude subscription runtime against a stand-in Claude Code CLI: billing, pause on sign-in errors, and resume.
- **Company control plane:** approvals deferred across separate runs, rejection, budget hard stop and override, `ask_first`, escalation, `require_verification`, goal ancestry and stack skills in prompts, atomic checkout, routines, export without secrets, and the dashboard.

## Limits

- Only Salesforce, Python and Node have end-to-end demos. The other stacks are covered by detection tests; their
  commands are the standard ones and should be confirmed on a real repository of each kind.
- The subscription runtime is tested against a stand-in Claude Code CLI that uses the real JSON format; confirm with `doctor --probe` on your machine. The `http` adapter hasn't been run against a live service yet.
