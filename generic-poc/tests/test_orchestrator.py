"""Tests for the orchestrator itself (no API key needed: uses the recorded demo run)."""
import json
from pathlib import Path

import pytest

from orchestrator.agents import parse_output
from orchestrator.llm import ReplayLLM
from orchestrator.orchestrator import Orchestrator, auto_approver

ROOT = Path(__file__).resolve().parents[1]
STORY = (ROOT / "samples" / "promo_code_story.md").read_text(encoding="utf-8")
RECORDING = ROOT / "samples" / "demo_recording.json"


def test_parse_output_extracts_tags():
    out = parse_output('<summary>s</summary>\n<file path="a/b.py">\nx = 1\n</file>'
                       '<finding severity="low">f</finding><verdict>APPROVE</verdict>')
    assert out.summary == "s"
    assert out.files == {"a/b.py": "x = 1\n"}
    assert out.verdict == "APPROVE"
    assert out.findings == [{"severity": "low", "text": "f"}]


def test_full_pipeline_with_feedback_loop(tmp_path):
    trace = Orchestrator(ReplayLLM(RECORDING), tmp_path, approver=auto_approver("Tester"), log=lambda *_: None).run(STORY)
    kinds = [e["kind"] for e in trace["events"]]
    tool_runs = [e for e in trace["events"] if e["kind"] == "tool"]
    assert trace["status"] == "released"
    assert kinds.count("route") == 1
    assert [t["passed"] for t in tool_runs] == [False, True]
    assert (tmp_path / "RELEASE_NOTES.md").exists()
    assert json.loads((tmp_path / "trace.json").read_text())["run_id"] == trace["run_id"]


def test_rejected_at_human_gate(tmp_path):
    reject = lambda ctx: {"approved": False, "approver": "Lead", "comment": "not now", "time": "t"}
    trace = Orchestrator(ReplayLLM(RECORDING), tmp_path, approver=reject, log=lambda *_: None).run(STORY)
    assert trace["status"] == "rejected"
    assert not (tmp_path / "RELEASE_NOTES.md").exists()


def test_agent_cannot_write_outside_workspace(tmp_path):
    orch = Orchestrator(None, tmp_path, log=lambda *_: None)
    with pytest.raises(ValueError):
        orch._write({"../evil.py": "x"})
