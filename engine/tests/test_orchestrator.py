"""Tests for the Salesforce orchestrator itself (no API key or org needed)."""
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator import tools
from orchestrator.agents import parse_output
from orchestrator.llm import ReplayLLM
from orchestrator.orchestrator import Orchestrator, auto_approver

ROOT = Path(__file__).resolve().parents[1]
STORY = (ROOT / "samples" / "closed_won_onboarding_story.md").read_text(encoding="utf-8")
RECORDING = ROOT / "samples" / "demo_recording.json"
needs_pmd = pytest.mark.skipif(tools._pmd_binary() is None, reason="PMD not installed")
quiet = {"log": lambda *_: None}


def test_parse_output_extracts_tags():
    out = parse_output('<summary>s</summary>\n<file path="force-app/a.cls">\nx\n</file>'
                       '<finding severity="medium">f</finding><verdict>PASS</verdict>')
    assert out.files == {"force-app/a.cls": "x\n"} and out.verdict == "PASS"
    assert out.findings == [{"severity": "medium", "text": "f"}]


@needs_pmd
def test_full_pipeline_catches_soql_in_loop_and_fixes_it(tmp_path):
    trace = Orchestrator(ReplayLLM(RECORDING), tmp_path, approver=auto_approver("Lead"), **quiet).run(STORY)
    gates = [e for e in trace["events"] if e["kind"] == "tool"]
    assert trace["status"] == "released"
    assert [g["passed"] for g in gates if g["tool"] == "static_analysis"] == [False, True]
    rules = {v["rule"] for v in gates[0]["violations"]}
    assert "OperationWithLimitsInLoop" in rules
    assert gates[-1]["tool"] == "org_validation" and gates[-1]["skipped"]
    art = tmp_path / trace["artifacts_dir"]
    assert (art / "manifest" / "package.xml").exists() and (art / "trace.json").exists()
    assert (tmp_path / "sfdx-project.json").exists()
    assert not (tmp_path / "manifest").exists()  # nothing outside force-app written into the project root


@needs_pmd
def test_existing_project_is_not_overwritten_and_old_code_not_scanned(tmp_path):
    (tmp_path / "sfdx-project.json").write_text('{"custom": true}')
    legacy = tmp_path / tools.SRC / "classes" / "Legacy.cls"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("public class Legacy { void x(){ for(Account a:[SELECT Id FROM Account]){ update a; } } }")
    (legacy.parent / "Legacy.cls-meta.xml").write_text("<ApexClass><apiVersion>50.0</apiVersion></ApexClass>")
    trace = Orchestrator(ReplayLLM(RECORDING), tmp_path, approver=auto_approver("Lead"), **quiet).run(STORY)
    assert trace["status"] == "released"
    assert (tmp_path / "sfdx-project.json").read_text() == '{"custom": true}'
    assert "Legacy" in trace["project_context"]
    first_gate = next(e for e in trace["events"] if e["kind"] == "tool")
    assert all("Legacy" not in v["file"] for v in first_gate["violations"])


@needs_pmd
def test_events_are_streamed(tmp_path):
    seen = []
    Orchestrator(ReplayLLM(RECORDING), tmp_path, approver=auto_approver("Lead"),
                 on_event=seen.append, **quiet).run(STORY)
    assert seen[0]["kind"] == "plan" and seen[-1]["kind"] == "finished"


def test_metadata_check_flags_missing_meta_and_undefined_field(tmp_path):
    cls = tmp_path / tools.SRC / "classes" / "Foo.cls"
    cls.parent.mkdir(parents=True)
    cls.write_text("public with sharing class Foo { void x(){ Account a; a.Missing__c = 1; } }")
    rules = {i["rule"] for i in tools.metadata_check(tmp_path)}
    assert {"MetaXmlPresent", "FieldDefined", "TestClassPresent"} <= rules


def test_org_validation_skips_without_org(tmp_path):
    res = tools.org_validate(tmp_path, None, ["T"])
    assert res["skipped"] and res["passed"] is None


def test_org_validation_parses_sf_json(tmp_path, monkeypatch):
    payload = {"status": 0, "result": {"success": True, "details": {"runTestResult": {
        "numTestsRun": 9, "failures": [],
        "codeCoverage": [{"numLocations": 40, "numLocationsNotCovered": 2}]}}}}
    monkeypatch.setattr(tools.shutil, "which", lambda name: "/usr/bin/sf")
    monkeypatch.setattr(tools.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(stdout=json.dumps(payload), stderr="", returncode=0))
    res = tools.org_validate(tmp_path, "sandbox", ["OnboardingServiceTest"], 75)
    assert res["passed"] and res["coverage"] == 95.0


def test_agent_cannot_write_outside_workspace(tmp_path):
    with pytest.raises(ValueError):
        Orchestrator(None, tmp_path, **quiet)._write({"../evil.cls": "x"})
