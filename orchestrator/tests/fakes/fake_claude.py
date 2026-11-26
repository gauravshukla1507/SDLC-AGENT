#!/usr/bin/env python3
"""Stand-in for the Claude Code CLI (`claude -p --output-format json ...`) used by tests.

Answers with a recorded response for the agent identified from --append-system-prompt, and asserts the
contract the orchestrator relies on: prompt on stdin, read-only tools, no session persistence and,
for subscription runs, no ANTHROPIC_API_KEY in the environment. FAKE_CLAUDE_MODE=auth_error simulates
a Claude Code that is not signed in."""
import json
import os
import sys
from pathlib import Path

TITLES = {"Business Analyst": "requirements", "Solution Architect": "architect", "Senior Software Engineer": "developer",
          "Test Engineer": "tester", "Technical Lead": "reviewer", "Application Security Engineer": "security",
          "DevOps / Release Engineer": "release"}

args = sys.argv[1:]
if args == ["--version"]:
    print("9.9.9 (Fake Claude Code)")
    sys.exit(0)
assert "-p" in args and args[args.index("--output-format") + 1] == "json"
assert args[args.index("--tools") + 1] == "Read,Glob,Grep", "agents must be read-only"
assert "--no-session-persistence" in args
assert "ANTHROPIC_API_KEY" not in os.environ, "subscription runs must not carry an API key"
system = args[args.index("--append-system-prompt") + 1]
prompt = sys.stdin.read()
assert prompt.strip(), "prompt must arrive on stdin"

if os.environ.get("FAKE_CLAUDE_MODE") == "auth_error":
    print(json.dumps({"type": "result", "is_error": True, "result": "Authentication error · please run /login",
                      "terminal_reason": "api_error", "usage": {}, "total_cost_usd": 0}))
    sys.exit(0)

agent = next(v for k, v in TITLES.items() if f"You are the {k} " in system)
state_file = Path(os.environ["FAKE_CLAUDE_STATE"])
state = json.loads(state_file.read_text()) if state_file.exists() else {}
recording = json.loads(Path(os.environ["FAKE_CLAUDE_RECORDING"]).read_text())["responses"]
answers = [r["response"] for r in recording if r["agent"] == agent]
i = state.get(agent, 0)
state[agent] = i + 1
state_file.write_text(json.dumps(state))
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": answers[i],
                  "total_cost_usd": 0.0123, "usage": {"input_tokens": 1000, "output_tokens": 500,
                                                       "cache_read_input_tokens": 200},
                  "modelUsage": {"claude-sonnet-4-5": {}}, "num_turns": 1}))
