# Agentic SDLC for Salesforce: Multi-Agent Orchestration

AI agents work together to deliver Salesforce changes from start to finish. An **orchestrator** coordinates
seven specialist Claude agents through **real quality gates** to turn a business request into verified,
deployable SFDX source. A **human approves** before anything ships, and every step is recorded in an **audit trail**.

```
BA ─▶ Architect ─▶ ┌ Apex Developer ┐ ─▶ Static analysis ─▶ Org validation ─▶ ┌ Code review ┐ ─▶ Human gate ─▶ DevOps
                   └ Apex Test      ┘     (metadata+PMD)    (deploy+tests)     └ Security    ┘
                          ▲                      │                 │
                          └──── findings routed to the agent that owns the failing files (max 3, then escalate)
```

## Repository layout

| Folder | What it is |
|---|---|
| [`universal/`](universal/) | **Universal Orchestrator**: one AI delivery company and one playbook for any technology. It detects the stack (Salesforce, Java, .NET, Node/TS, Python, Go, Rust, Ruby, PHP, dbt, Terraform, polyglot), briefs the agents with that stack's conventions and verifies with its own tools. Includes org chart, goals, tasks and heartbeats, budgets, approvals, tool gateway, routines, audit and dashboard |
| [`engine/`](engine/) | Salesforce orchestration engine (Python): agents, gates (metadata checks, PMD Apex, `sf` org validation), CLI, audit trace, demo-page builder |
| [`vscode-extension/`](vscode-extension/) | **Agentic SDLC for Salesforce** VS Code extension: live run panel, approval in the IDE, diagnostics, git branch per run, demo export |
| [`generic-poc/`](generic-poc/) | Language-agnostic version of the orchestration (Python/pytest) used for the first client POC |
| [`docs/demos/`](docs/demos/) | Self-contained client demo pages (open in a browser) |

## Quick start

### Universal Orchestrator (company mode)
```bash
cd universal && pip install -r requirements.txt
for s in salesforce python node; do python uo.py demo --sample $s -w /tmp/uo-$s --store /tmp/uo-company.db; done
python uo.py dashboard -w /tmp --store /tmp/uo-company.db --out /tmp/uo-dashboard.html && open /tmp/uo-dashboard.html
python uo.py stacks -w ~/work/any-repo                    # detected stacks + verification plan
```

### VS Code extension
```bash
cd vscode-extension
npm ci
npm run package                       # syncs ../engine into the extension and builds the .vsix
code --install-extension agentic-sdlc-salesforce-*.vsix
```
Then run **Agentic SDLC: Run demo** from the Command Palette (no API key needed), or set your key with
**Agentic SDLC: Set Anthropic API key** and run **Agentic SDLC: Run on a business request…** in an SFDX project.

### Engine from the command line
```bash
cd engine
pip install -r requirements.txt       # plus Java 17+ and PMD 7 on PATH (or PMD_BIN)
export ANTHROPIC_API_KEY=sk-ant-...
export SF_TARGET_ORG=my-sandbox       # optional: deploy validation + Apex tests
python run.py --story-file samples/closed_won_onboarding_story.md --workspace /path/to/sfdx-project
```

## Development

| Component | Test |
|---|---|
| universal | `cd universal && python -m pytest -q` |
| engine | `cd engine && python -m pytest -q` |
| generic-poc | `cd generic-poc && python -m pytest -q` |
| vscode-extension | `cd vscode-extension && npm ci && npm test` (`npm run test:webview` needs Playwright) |

CI (`.github/workflows/ci.yml`) runs all four on every push and pull request and uploads the `.vsix` as a build artifact.
`vscode-extension/engine/` is generated from `engine/` at build time. Edit the engine only in `engine/`.

## Status and limits

- Proof of concept. The bundled demo recordings replay recorded agent answers; the quality gates run for real.
- The org-validation gate has been tested against mocked `sf` CLI output only. Verify it against a sandbox on the first live run.
- Point `SF_TARGET_ORG` / `agenticSdlc.targetOrg` at a sandbox or scratch org, never production.

## Security

- API keys: environment variable or VS Code SecretStorage. Never commit them (`.env` and `*.key` are git-ignored).
- Agents send the business request, generated docs and source, and the names of existing components to the Anthropic API.
  They do not read org data.
