# Agentic SDLC – Multi-Agent Orchestration POC

One **orchestrator** coordinates seven **specialist AI agents** (Claude) to take a business request
all the way to tested, reviewed, approved and release-ready code, with a **human approval gate** and a
complete **audit trail**.

```
Requirements ─▶ Architect ─▶ ┌ Developer ┐ ─▶ pytest ─▶ ┌ Code review ┐ ─▶ Human gate ─▶ Release
                             └ Test      ┘     ▲  │      └ Security    ┘
                                               └──┘ failing tests routed back to Developer (max 3 iterations, then escalate)
```

| Agent | Output |
|---|---|
| Requirements | `docs/requirements.md` – user story + numbered acceptance criteria |
| Architect | `docs/design.md` – module layout, exact interface, AC traceability |
| Developer | `src/...` – implementation; fixes code when tests fail |
| Test | `tests/...` – pytest suite written from the ACs only (never sees the code) |
| Code Review | findings + `APPROVE` / `CHANGES_REQUESTED` |
| Security | findings + `PASS` / `FAIL` |
| Release | `.github/workflows/ci.yml`, `RELEASE_NOTES.md` |

The orchestrator also runs **real tools** (pytest), enforces workspace sandboxing for agent file writes,
runs independent agents **in parallel**, and writes `trace.json` (every prompt outcome, file, verdict,
tool run, routing decision and human approval, with timestamps and token usage).

## Run it live

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
export POC_MODEL=claude-sonnet-4-5          # optional; any current Claude model id

python run.py --story-file samples/promo_code_story.md --workspace runs/promo --clean
```

You will be asked to approve the release at the human gate. Add `--auto-approve "Your Name"` for unattended runs.
Use your own request with `--story "..."` or another `--story-file`.

## Build the client demo page from any run

```bash
python demo/build_demo.py runs/promo/trace.json demo/agentic_sdlc_demo.html
```

The page is a single self-contained HTML file: play/step through the run, inspect every agent's
output and code diffs, the real pytest output, the approval record and the audit trail.

## Record and replay (for meetings without network)

```bash
python run.py --story-file samples/promo_code_story.md --workspace runs/promo --record runs/promo.recording.json
python run.py --story-file samples/promo_code_story.md --workspace runs/replay --replay runs/promo.recording.json --auto-approve "Demo"
```

Replay feeds the recorded agent answers through the same orchestrator; pytest still executes for real.
`samples/demo_recording.json` is the recording behind the bundled demo (promo-code feature, including a
first-build defect that the feedback loop fixes).

## Layout

```
orchestrator/agents.py        agent roles, system prompts, output contract + parser
orchestrator/orchestrator.py  task graph, parallelism, feedback loop, human gate, audit trace
orchestrator/llm.py           Claude backend, recorder, replayer
run.py                        CLI
demo/                         demo page template + builder
samples/                      sample business request + demo recording
tests/                        tests for the orchestrator itself
```

## Extending toward production

- Add agents (e.g. data-migration, compliance, UI) by defining a role in `agents.py` and a node in `Orchestrator.run`.
- Swap pytest for your CI, SAST and coverage tools; connect Jira/Azure DevOps, Git and chat via MCP connectors.
- Open a pull request instead of writing to a local workspace; make the human gate a PR approval.
- Persist traces to your observability stack; add cost budgets per run.
