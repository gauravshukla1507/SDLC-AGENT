#!/usr/bin/env python3
"""Run the multi-agent Salesforce SDLC pipeline on a business request.

Live (Claude API, optionally validating against a real org):
    export ANTHROPIC_API_KEY=...
    export SF_TARGET_ORG=my-sandbox      # optional: alias authenticated with `sf org login web`
    python run.py --story-file samples/closed_won_onboarding_story.md --workspace runs/onboarding

Record a live run so it can be replayed in a client meeting without network:
    python run.py --story-file samples/closed_won_onboarding_story.md --workspace runs/onboarding --record runs/onboarding.recording.json

Replay a recorded run (agents replay their recorded answers; pytest really runs):
    python run.py --story-file samples/closed_won_onboarding_story.md --workspace runs/replay --replay samples/demo_recording.json --auto-approve "Demo Approver"
"""
import argparse
import os
import shutil
from pathlib import Path

from orchestrator.llm import ClaudeLLM, RecordingLLM, ReplayLLM
import json
import sys
import threading

_OUT = threading.Lock()


def _out(line: str):
    """Thread-safe, line-atomic stdout (agents run in parallel threads)."""
    with _OUT:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

from orchestrator.orchestrator import Orchestrator, auto_approver, cli_approver, stdin_approver


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--story", help="Business request text")
    src.add_argument("--story-file", type=Path, help="File containing the business request")
    ap.add_argument("--workspace", type=Path, required=True, help="Output folder for generated project + trace.json")
    ap.add_argument("--model", help="Claude model id (default: $POC_MODEL or claude-sonnet-4-5)")
    ap.add_argument("--max-fix-iterations", type=int, default=3)
    ap.add_argument("--auto-approve", metavar="NAME", help="Skip the interactive human gate, approve as NAME")
    ap.add_argument("--record", type=Path, help="Save live LLM responses for later replay")
    ap.add_argument("--replay", type=Path, help="Replay a recorded run instead of calling the API")
    ap.add_argument("--target-org", default=os.environ.get("SF_TARGET_ORG"),
                    help="sf CLI org alias for deploy validation + Apex tests (default: $SF_TARGET_ORG)")
    ap.add_argument("--min-coverage", type=int, default=75, help="Minimum Apex coverage %% for the org gate")
    ap.add_argument("--approval", choices=["cli", "stdin"], default="cli",
                    help="cli: interactive prompt; stdin: JSON protocol for IDE integrations")
    ap.add_argument("--events", action="store_true", help="Stream events as '@@EVENT <json>' lines (IDE integrations)")
    ap.add_argument("--artifacts-dir", type=Path, help="Where docs, manifest, CI files and trace go "
                    "(default: <workspace>/.agentic-sdlc/runs/<run_id>)")
    ap.add_argument("--clean", action="store_true", help="Delete the workspace first")
    args = ap.parse_args()

    story = args.story or args.story_file.read_text(encoding="utf-8")
    if args.clean and args.workspace.exists():
        shutil.rmtree(args.workspace)

    llm = ReplayLLM(args.replay) if args.replay else ClaudeLLM(model=args.model)
    if args.record and not args.replay:
        llm = RecordingLLM(llm)

    if args.auto_approve:
        approver = auto_approver(args.auto_approve)
    else:
        approver = stdin_approver if args.approval == "stdin" else cli_approver
    emit = (lambda ev: _out("@@EVENT " + json.dumps(ev))) if args.events else None
    orch = Orchestrator(llm, args.workspace, args.max_fix_iterations, approver,
                        target_org=args.target_org, min_coverage=args.min_coverage,
                        on_event=emit, artifacts_dir=args.artifacts_dir,
                        log=lambda *a: _out(" ".join(str(x) for x in a)))
    orch.trace["mode"] = "replay" if args.replay else "live"
    if args.events:
        _out("@@EVENT " + json.dumps({"kind": "started", "run_id": orch.trace["run_id"], "story": story,
                                       "mode": orch.trace["mode"], "model": orch.trace["model"],
                                       "artifacts_dir": orch.trace["artifacts_dir"]}))
    try:
        trace = orch.run(story)
    except Exception as exc:  # surface failures to IDEs as an event, then re-raise for the exit code
        if args.events:
            _out("@@EVENT " + json.dumps({"kind": "error", "message": f"{type(exc).__name__}: {exc}"}))
        raise

    if isinstance(llm, RecordingLLM):
        llm.save(args.record)
        print(f"Recording saved to {args.record}")
    return 0 if trace["status"] == "released" else 1


if __name__ == "__main__":
    raise SystemExit(main())
