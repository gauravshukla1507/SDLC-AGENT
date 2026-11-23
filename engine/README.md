# Agentic SDLC for Salesforce – Multi-Agent Orchestration POC

One **orchestrator** coordinates seven **Salesforce specialist AI agents** (Claude) to take a business
request to deployable SFDX source: metadata, Apex, test classes, a CI/CD pipeline and release notes.
Real quality gates run between the agents, a **human approval gate** comes before release, and the
whole run is logged in an **audit trail**.

```
BA ─▶ Architect ─▶ ┌ Apex Developer ┐ ─▶ Static analysis ─▶ Org validation ─▶ ┌ Code review ┐ ─▶ Human gate ─▶ DevOps
                   └ Apex Test      ┘     (metadata+PMD)    (deploy+tests)     └ Security    ┘
                          ▲                      │                 │
                          └──── findings routed to the agent that owns the failing files (max 3, then escalate)
```

| Agent | Output |
|---|---|
| Salesforce BA | `docs/requirements.md`: story, acceptance criteria, objects/fields, personas & access, volumes |
| Solution Architect | `docs/design.md`: clicks-vs-code decision, data model, trigger/handler/service design, security, limits budget |
| Apex Developer | `force-app/...`: custom fields, permission set, trigger, handler, service (+ meta.xml) |
| Apex Test | `@IsTest` classes from the acceptance criteria only: bulk (200), negative, `System.runAs` |
| Code Review | Salesforce best-practice findings + verdict |
| Security | CRUD/FLS, sharing, SOQL injection, permission scope + verdict |
| Salesforce DevOps | `manifest/package.xml`, `.github/workflows/salesforce-ci.yml`, `RELEASE_NOTES.md` |

## Quality gates (real tools)

1. **Metadata checks**: meta.xml present with API ≥ 58, well-formed XML, one trigger per object, every
   `__c` field used in Apex defined in the project, a test class present.
2. **PMD Apex rules** (`config/pmd-ruleset.xml`, the engine behind Salesforce Code Analyzer): SOQL/DML in loops,
   CRUD/sharing violations, SOQL injection, hardcoded Ids, logic in triggers, test asserts, complexity.
3. **Org validation** (when `SF_TARGET_ORG` is set): `sf project deploy validate` with `RunSpecifiedTests`
   for the generated test classes, and minimum Apex coverage (`--min-coverage`, default 75).

The same PMD ruleset runs in the generated GitHub Actions pipeline, so CI enforces what the agents were held to.

## Prerequisites

- Python 3.11+, `pip install -r requirements.txt`
- Java 17+ and [PMD 7](https://pmd.github.io/) (`pmd` on PATH, or set `PMD_BIN=/path/to/pmd`)
- Optional, for the org gate: Salesforce CLI (`npm i -g @salesforce/cli`) and an authenticated sandbox or
  scratch org: `sf org login web --alias poc-sandbox`

## Run it live

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export POC_MODEL=claude-sonnet-4-5        # optional, any current Claude model id
export SF_TARGET_ORG=poc-sandbox          # optional, enables deploy validation + Apex tests

python run.py --story-file samples/closed_won_onboarding_story.md --workspace runs/onboarding --clean
```

You approve the release at the human gate (or pass `--auto-approve "Name"`). Try your own request with `--story "..."`.
Deploy the generated project with `sf project deploy start --source-dir force-app --target-org <alias>` from the workspace.

Check the org gate's JSON parsing on your first live run against your sf CLI version (see `tools.org_validate`).

## Client demo page

```bash
python demo/build_demo.py runs/onboarding/trace.json demo/salesforce_demo.html
```

This builds a single self-contained HTML page that replays the run: the agent pipeline, every output with code diffs,
gate violations, the approval record and the audit trail.

## Record / replay

```bash
python run.py ... --record runs/onboarding.recording.json     # live run, responses saved
python run.py --story-file samples/closed_won_onboarding_story.md --workspace runs/replay \
  --replay samples/demo_recording.json --auto-approve "Demo"   # replay: gates still run for real
```

`samples/demo_recording.json` is the recording behind the bundled demo. Its first Apex build deliberately has
SOQL/DML inside a loop, so you can see PMD catch it and the feedback loop fix it.

## Layout

```
orchestrator/agents.py        Salesforce agent roles, standards, output contract
orchestrator/tools.py         SFDX scaffold, metadata checks, PMD, sf CLI org validation
orchestrator/orchestrator.py  task graph, parallel agents, gate loop & routing, human gate, audit trace
orchestrator/llm.py           Claude backend, recorder, replayer
config/pmd-ruleset.xml        Apex rules enforced by the gate and by CI
run.py · demo/ · samples/ · tests/
```

## Toward production

- Open a pull request with the change, and make the human gate the PR approval (GitHub, Copado or Gearset).
- Pull stories from Jira or Azure DevOps and post results back through MCP connectors.
- Add agents for LWC (with Jest), Flow (metadata review), data migration and Agentforce.
- Run a scratch org per run from a Dev Hub for full isolation.
