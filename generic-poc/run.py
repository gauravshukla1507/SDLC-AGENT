#!/usr/bin/env python3
"""Run the multi-agent SDLC pipeline on a business request.

Live (Claude API):
    export ANTHROPIC_API_KEY=...
    python run.py --story-file samples/promo_code_story.md --workspace runs/promo

Record a live run so it can be replayed in a client meeting without network:
    python run.py --story-file samples/promo_code_story.md --workspace runs/promo --record runs/promo.recording.json

Replay a recorded run (agents replay their recorded answers; pytest really runs):
    python run.py --story-file samples/promo_code_story.md --workspace runs/replay --replay samples/demo_recording.json --auto-approve "Demo Approver"
"""
import argparse
import shutil
from pathlib import Path

from orchestrator.llm import ClaudeLLM, RecordingLLM, ReplayLLM
from orchestrator.orchestrator import Orchestrator, auto_approver, cli_approver


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
    ap.add_argument("--clean", action="store_true", help="Delete the workspace first")
    args = ap.parse_args()

    story = args.story or args.story_file.read_text(encoding="utf-8")
    if args.clean and args.workspace.exists():
        shutil.rmtree(args.workspace)

    llm = ReplayLLM(args.replay) if args.replay else ClaudeLLM(model=args.model)
    if args.record and not args.replay:
        llm = RecordingLLM(llm)

    approver = auto_approver(args.auto_approve) if args.auto_approve else cli_approver
    orch = Orchestrator(llm, args.workspace, args.max_fix_iterations, approver)
    orch.trace["mode"] = "replay" if args.replay else "live"
    trace = orch.run(story)

    if isinstance(llm, RecordingLLM):
        llm.save(args.record)
        print(f"Recording saved to {args.record}")
    return 0 if trace["status"] == "released" else 1


if __name__ == "__main__":
    raise SystemExit(main())
