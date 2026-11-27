"""Tests for the Universal Orchestrator (no API key needed: recorded agent responses, real tools)."""
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import stacks  # noqa: E402
from core import governance as gov  # noqa: E402
from core.budget import run_cost  # noqa: E402
from core.company import create_company, hire_agents, load_spec, org_chart  # noqa: E402
from core.dashboard import render_dashboard  # noqa: E402
from core.engine import Engine  # noqa: E402
from core.llm import ReplayLLM  # noqa: E402
from core.playbook import get_playbook  # noqa: E402
from core.portability import export_company, import_company  # noqa: E402
from core.routines import is_due  # noqa: E402
from core.store import Store  # noqa: E402
from stacks import salesforce_tools as sf_tools  # noqa: E402

SPEC = ROOT / "companies" / "default-company.json"
SAMPLES = ROOT / "tests" / "fixtures"     # recorded agent answers used as test fixtures
SF, PY, NODE = "salesforce-closed-won", "python-promo-codes", "node-late-fees"
needs_pmd = pytest.mark.skipif(sf_tools._pmd_binary() is None, reason="PMD not installed")
needs_npm = pytest.mark.skipif(not shutil.which("npm"), reason="npm not installed")
quiet = lambda *a, **k: None  # noqa: E731


def setup(tmp_path, sample, approver, spec_patch=None, store=None, ws=None):
    spec = load_spec(SPEC)
    if spec_patch:
        spec_patch(spec)
    store = store or Store(tmp_path / "company.db")
    company = create_company(store, spec)
    d = SAMPLES / sample
    engine = Engine(store, company, get_playbook(), ws or (tmp_path / "ws"), approver=approver,
                    replay=ReplayLLM(d / "recording.json"), log=quiet)
    hire_agents(store, company, engine.governance)
    engine.start_project((d / "story.md").read_text(), title=json.loads((d / "meta.json").read_text())["title"])
    return store, company, engine


# ------------------------------------------------------------------ one playbook, any stack
@needs_pmd
def test_salesforce_end_to_end_detected_not_chosen(tmp_path):
    store, company, engine = setup(tmp_path, SF, gov.auto_approver("Lead"))
    assert engine.run() == "released"
    tasks = {t["key"]: t for t in engine.tasks()}
    assert tasks["verify"]["attempts"] == 1                   # PMD failed once, fixed, passed
    assert tasks["fix-verify-1"]["assignee"] == "developer"
    assert engine.goal_stacks == ["salesforce"]
    assert (tmp_path / "ws" / "force-app/main/default/classes/OnboardingService.cls").exists()
    assert not (tmp_path / "ws" / "docs").exists()             # docs go to the project artifacts folder
    acts = {r["action"] for r in store.all("SELECT action FROM activity")}
    assert {"heartbeat.wakeup", "task.completed", "gate.failed", "task.routed", "approval.approved",
            "stack.detected"} <= acts


def test_python_end_to_end(tmp_path):
    store, company, engine = setup(tmp_path, PY, gov.auto_approver("Lead"))
    assert engine.run() == "released"
    verify = [e for e in engine.events if e["kind"] == "tool" and e["tool"] == "verify"]
    assert [g["passed"] for g in verify] == [False, True]
    assert {t["key"]: t for t in engine.tasks()}["fix-verify-1"]["assignee"] == "developer"  # tests are the spec
    assert "python" in engine.goal_stacks


@needs_npm
def test_node_end_to_end(tmp_path):
    store, company, engine = setup(tmp_path, NODE, gov.auto_approver("Lead"))
    assert engine.run() == "released"
    assert engine.goal_stacks == ["node"]
    assert (tmp_path / "ws" / "src" / "lateFees.js").exists()
    assert not (tmp_path / "ws" / ".github").exists()          # CI proposal goes to artifacts, not over the repo's CI


@needs_pmd
def test_one_company_many_stacks_same_playbook(tmp_path):
    store = Store(tmp_path / "company.db")
    for sample in (SF, PY):
        _, _, engine = setup(tmp_path, sample, gov.auto_approver("Lead"), store=store, ws=tmp_path / sample)
        assert engine.run() == "released"
    assert len(store.all("SELECT * FROM goals WHERE level='project'")) == 2
    assert len(store.all("SELECT * FROM companies")) == 1


# ------------------------------------------------------------------ stack detection
def _repo(tmp_path, files: dict) -> Path:
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return tmp_path


@pytest.mark.parametrize("files, expected, check", [
    ({"pom.xml": "<project/>", "src/main/java/A.java": ""}, "java-maven", "maven verify"),
    ({"build.gradle.kts": "", "gradlew": ""}, "java-gradle", "gradle check"),
    ({"App.sln": "", "src/App/App.csproj": ""}, "dotnet", "dotnet test"),
    ({"go.mod": "module x"}, "go", "go test"),
    ({"Cargo.toml": "[package]"}, "rust", "cargo test"),
    ({"package.json": '{"scripts": {"test": "jest", "build": "tsc"}, "devDependencies": {"jest": "1"}}',
      "tsconfig.json": "{}"}, "typescript", "tests"),
    ({"Gemfile": "", "spec/a_spec.rb": ""}, "ruby", "tests"),
    ({"composer.json": '{"scripts": {"test": "phpunit"}}'}, "php", "tests"),
    ({"dbt_project.yml": ""}, "dbt", "dbt parse"),
    ({"main.tf": ""}, "terraform", "terraform validate"),
    ({"sfdx-project.json": "{}", "force-app/main/default/classes/A.cls": ""}, "salesforce", "PMD Apex rules"),
    ({"pyproject.toml": "", "tests/test_a.py": ""}, "python", "pytest"),
])
def test_stack_detection(tmp_path, files, expected, check):
    ws = _repo(tmp_path, files)
    detected, checks = stacks.checks_for(ws)
    assert expected in [s.name for s in detected]
    assert check in [c.name for c in checks]


def test_polyglot_repo_and_operator_overrides(tmp_path):
    ws = _repo(tmp_path, {
        "sfdx-project.json": "{}", "force-app/main/default/classes/A.cls": "", "go.mod": "module x",
        "package.json": '{"scripts": {"test": "node --test", "lint": "eslint ."}}',
        ".agentic-sdlc/verify.json": json.dumps({"checks": [{"name": "contract tests", "cmd": "make contract"}],
                                                 "environment": [{"name": "staging smoke", "cmd": "./smoke.sh"}]}),
    })
    detected, checks = stacks.checks_for(ws)
    assert {"salesforce", "go", "node"} <= {s.name for s in detected}
    names = [c.name for c in checks]
    assert "contract tests" in names and names.index("lint") > names.index("tests")   # advisory lint runs last
    assert [c.blocking for c in checks if c.name == "lint"] == [False]
    _, env = stacks.checks_for(ws, "environment")
    assert [c.name for c in env] == ["Salesforce org validation (deploy + Apex tests)", "staging smoke"]


def test_greenfield_has_no_stack_until_agents_write_one(tmp_path):
    assert stacks.detect(tmp_path) == []
    assert "greenfield" in stacks.describe([])


def test_missing_tool_is_skipped_not_passed(tmp_path):
    c = stacks.Check("x", ["definitely-not-a-real-tool-xyz"], stack="go")
    r = stacks.run_check(c, tmp_path, set())
    assert r["skipped"] and r["passed"] is None


def test_fix_routing_rules():
    pack = get_playbook()
    failing_tests = {"checks": [{"blocking": True, "passed": False, "kind": "test", "test_code_broken": False}],
                     "violations": [{"file": "tests/test_a.py"}]}
    broken_tests = {"checks": [{"blocking": True, "passed": False, "kind": "test", "test_code_broken": True}],
                    "violations": [{"file": "tests/test_a.py"}]}
    lint_in_tests = {"checks": [{"blocking": True, "passed": False, "kind": "static"}],
                     "violations": [{"file": "force-app/main/default/classes/ATest.cls"}]}
    assert pack.route_fix(failing_tests, {}) == "developer"
    assert pack.route_fix(broken_tests, {}) == "tester"
    assert pack.route_fix(lint_in_tests, {}) == "tester"


# ------------------------------------------------------------------ company control plane
def test_hiring_and_release_wait_for_humans_across_processes(tmp_path):
    store, company, engine = setup(tmp_path, PY, gov.deferred_approver)
    assert engine.run() == "waiting_approval"                 # nobody hired yet
    assert all(a["status"] in ("pending_approval", "active") for a in org_chart(store, company["id"]))
    g = gov.Governance(store, company, quiet)
    for a in g.pending():
        g.resolve(a["id"], True, "Gaurav")
    e2 = Engine(store, company, get_playbook(), tmp_path / "ws", approver=gov.deferred_approver,
                replay=engine.replay, log=quiet)
    e2.attach(engine.goal["id"])
    assert e2.run() == "waiting_approval"                     # now waiting on the release gate
    [rel] = g.pending()
    assert rel["type"] == "release" and rel["payload"]["verdicts"]["reviewer"] == "APPROVE"
    g.resolve(rel["id"], True, "Gaurav", "ship it")
    assert e2.run() == "released"
    gate = [e for e in e2.events if e["kind"] == "human_gate"][0]
    assert gate["approver"] == "Gaurav" and gate["comment"] == "ship it"


def test_rejected_release_cancels_remaining_work(tmp_path):
    def approver(a):
        return {"approved": a["type"] != "release", "approver": "Lead", "comment": "not this sprint"}
    store, company, engine = setup(tmp_path, PY, approver)
    assert engine.run() == "rejected"
    assert {t["key"]: t["status"] for t in engine.tasks()}["release"] == "cancelled"


def test_budget_hard_stop_then_override(tmp_path):
    def tiny(spec):
        for a in spec["agents"]:
            if a["key"] == "developer":
                a["budget_usd"] = 0.0001
    store, company, engine = setup(tmp_path, PY, gov.auto_approver("Lead", types={"hire", "release"}), tiny)
    assert engine.run() == "waiting_approval"
    assert store.one("SELECT * FROM agents WHERE key='developer'")["status"] == "paused"
    [b] = engine.governance.pending()
    assert b["type"] == "budget_override" and b["payload"]["scope"] == "agent"
    assert any(r["action"] == "budget.hard_stop" for r in store.all("SELECT action FROM activity"))
    engine.governance.resolve(b["id"], True, "Finance", "raise for pilot")
    assert store.one("SELECT status FROM agents WHERE key='developer'")["status"] == "active"
    assert engine.run() == "released"


def test_gateway_ask_first(tmp_path):
    def ask(spec):
        spec["governance"]["gateway"]["environment.validate"] = "ask_first"
    store, company, engine = setup(tmp_path, PY, gov.auto_approver("Lead", types={"hire", "release"}), ask)
    assert engine.run() == "waiting_approval"
    [g] = engine.governance.pending()
    assert g["type"] == "gateway"
    engine.governance.resolve(g["id"], True, "Lead")
    assert engine.run() == "released"


def test_escalation_after_max_fix_iterations(tmp_path):
    store, company, engine = setup(tmp_path, PY, gov.auto_approver("Lead", types={"hire"}))
    engine.max_fix = 0
    assert engine.run() == "escalated"
    assert engine.governance.pending()[0]["type"] == "escalation"


def test_require_verification_escalates_when_nothing_can_verify(tmp_path):
    def strict(spec):
        spec["governance"]["require_verification"] = True
    store, company, engine = setup(tmp_path, PY, gov.auto_approver("Lead", types={"hire"}), strict)
    engine.playbook.run_gate = lambda key, *a, **k: {
        "skipped": True, "passed": None, "summary": "Skipped: no automated verification available", "output": "",
        "violations": [], "require_human": key == "verify"}
    assert engine.run() == "escalated"


@needs_pmd
def test_goal_ancestry_skills_and_stack_reach_the_agents(tmp_path):
    seen = {}

    class Spy:
        model = "claude-sonnet-4-5"

        def __init__(self, inner):
            self.inner = inner

        def run(self, key, system, prompt, cwd=None):
            seen.setdefault(key, (system, prompt))
            return self.inner.run(key, system, prompt, cwd)

    store, company, engine = setup(tmp_path, SF, gov.auto_approver("Lead"))
    orig = engine._adapter
    engine._adapter = lambda agent: Spy(orig(agent))
    engine.run()
    system, prompt = seen["developer"]
    assert "[mission] Deliver reliable software changes" in prompt
    assert "[project] Closed Won" in prompt
    assert "Skill: team-conventions" in system
    # stack conventions are attached automatically once the stack exists in the repo (after the build step)
    assert "Stack skill: salesforce" in seen["reviewer"][0]
    assert "salesforce (evidence:" in seen["reviewer"][1]


def test_atomic_checkout(tmp_path):
    store = Store(tmp_path / "x.db")
    store.insert("tasks", {"id": "t1", "company_id": "c", "status": "todo", "deps": []})
    assert store.checkout("t1", "a") is True
    assert store.checkout("t1", "b") is False


def test_routines_schedule():
    at = datetime(2026, 11, 26, 10, 0, tzinfo=timezone.utc)
    assert is_due("@daily 02:00", None, at)
    assert not is_due("@daily 02:00", "2026-11-26T02:00:05+00:00", at)
    assert is_due("@every 6h", "2026-11-26T03:00:00+00:00", at)
    assert is_due("@weekly mon 03:00", "2026-11-20T00:00:00+00:00", at)
    assert not is_due("@monthly 1 03:00", "2026-11-01T03:00:01+00:00", at)


def test_export_import_roundtrip_strips_secrets(tmp_path):
    spec = load_spec(SPEC)
    spec["agents"][1]["adapter"] = {"type": "http", "url": "https://bot.example", "api_key": "sk-secret"}
    store = Store(tmp_path / "a.db")
    company = create_company(store, spec)
    data = export_company(store, company)
    assert "sk-secret" not in json.dumps(data)
    assert "secure-coding" in data["skills_bundle"]
    (tmp_path / "export.json").write_text(json.dumps(data))
    spec2 = import_company(tmp_path / "export.json", tmp_path / "skills")
    c2 = create_company(Store(tmp_path / "b.db"), spec2)
    assert c2["name"] == spec["name"] and (tmp_path / "skills" / "secure-coding.md").exists()


def test_cost_and_dashboard(tmp_path):
    assert run_cost("claude-sonnet-4-5", 1_000_000, 0) == 3.0
    store, company, engine = setup(tmp_path, PY, gov.auto_approver("Lead"))
    engine.run()
    page = render_dashboard(store, company)
    assert "<!DOCTYPE html>" in page and "Org chart" in page and "Promo codes at checkout" in page
    assert "python" in page


# ------------------------------------------------------------------ CLI (what the VS Code extension calls)
def _cli(*args):
    import subprocess
    p = subprocess.run([sys.executable, str(ROOT / "agentic.py"), *args], capture_output=True, text=True)
    return p.returncode, p.stdout


def test_cli_build_from_requirements_document_with_deferred_approvals(tmp_path):
    ws = str(tmp_path / "new-app")                                       # a new, empty folder: greenfield
    fx = SAMPLES / PY
    code, outp = _cli("run", "--requirements", str(fx / "story.md"), "-w", ws, "--approver", "deferred",
                      "--events", "--replay", str(fx / "recording.json"))
    assert code == 2 and "@@EVENT" in outp                              # waiting for hiring approvals
    code, outp = _cli("approvals", "-w", ws, "--json")
    assert {a["type"] for a in json.loads(outp)} == {"hire"}
    _cli("approve", "all", "--type", "hire", "--as", "Gaurav", "-w", ws)
    assert _cli("resume", "-w", ws, "--approver", "deferred")[0] == 2   # now waiting for release
    [rel] = json.loads(_cli("approvals", "-w", ws, "--json")[1])
    assert rel["type"] == "release"
    _cli("approve", rel["id"], "--as", "Gaurav", "--comment", "ok", "-w", ws)
    assert _cli("resume", "-w", ws, "--approver", "deferred")[0] == 0
    state = json.loads(_cli("state", "-w", ws, "--json")[1])
    assert state["projects"][0]["status"] == "released" and "python" in state["projects"][0]["stacks"]
    assert len(state["agents"]) == 8 and state["spend"]["total_usd"] > 0
    assert json.loads(_cli("stacks", "-w", ws, "--json")[1])["stacks"][0]["name"] == "python"
    trace = state["projects"][0]["trace"]
    assert _cli("replay-page", trace, "--out", str(tmp_path / "replay.html"))[0] == 0
    assert "Promo codes" in (tmp_path / "replay.html").read_text()


def test_cli_has_no_demo_command():
    code, outp = _cli("demo", "--help")
    assert code != 0


# ------------------------------------------------------------------ requirement documents
def _docx(path: Path, paragraphs: list, table: list):
    import zipfile
    w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    def para(text, style=None, numbered=False):
        ppr = ""
        if style or numbered:
            ppr = "<w:pPr>" + (f'<w:pStyle w:val="{style}"/>' if style else "") + \
                  ("<w:numPr><w:ilvl w:val=\"0\"/></w:numPr>" if numbered else "") + "</w:pPr>"
        return f"<w:p>{ppr}<w:r><w:t>{text}</w:t></w:r></w:p>"
    body = "".join(para(*p) for p in paragraphs)
    rows = "".join("<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>" for c in r) + "</w:tr>"
                   for r in table)
    xml = f'<?xml version="1.0" encoding="UTF-8"?><w:document {w}><w:body>{body}<w:tbl>{rows}</w:tbl></w:body></w:document>'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", xml)


def test_read_requirements_docx_keeps_headings_lists_and_tables(tmp_path):
    from core.documents import read_requirements, title_of
    doc = tmp_path / "BRD.docx"
    _docx(doc, [("Leave Management Service", "Title"), ("Business rules", "Heading1"),
                ("Employees accrue 1.5 days of leave per month.",), ("Carry-forward is capped at 30 days", None, True)],
          [["ID", "Criterion"], ["AC-1", "Accrual is 1.5 days per full month"], ["AC-2", "Cap is 30 days"]])
    text = read_requirements(doc)
    assert "# Leave Management Service" in text and "## Business rules" in text
    assert "- Carry-forward is capped at 30 days" in text
    assert "| AC-1 | Accrual is 1.5 days per full month |" in text
    assert title_of(text) == "Leave Management Service"


def test_read_requirements_markdown_and_errors(tmp_path):
    from core.documents import DocumentError, read_requirements, title_of
    md = tmp_path / "brd.md"
    md.write_text("# Business Requirements Document: Expense Claim Policy Engine\n\n" + "Rule text. " * 10)
    assert title_of(read_requirements(md)) == "Expense Claim Policy Engine"
    with pytest.raises(DocumentError):
        read_requirements(tmp_path / "missing.docx")
    (tmp_path / "x.pptx").write_text("x")
    with pytest.raises(DocumentError):
        read_requirements(tmp_path / "x.pptx")
    (tmp_path / "empty.txt").write_text("hi")
    with pytest.raises(DocumentError, match="almost no text"):
        read_requirements(tmp_path / "empty.txt")


def test_bundled_brd_reads_cleanly():
    from core.documents import read_requirements, title_of
    brd = ROOT.parent / "examples" / "BRD-expense-claim-policy-engine.md"
    if brd.exists():
        assert title_of(read_requirements(brd)) == "Expense Claim Policy Engine"


# ------------------------------------------------------------------ Claude subscription runtime (Claude Code)
def _fake_claude(monkeypatch, tmp_path, mode=None, recording=PY):
    monkeypatch.setenv("CLAUDE_CODE_BIN", str(ROOT / "tests" / "fakes" / "fake_claude.py"))
    monkeypatch.setenv("FAKE_CLAUDE_STATE", str(tmp_path / "fake_state.json"))
    monkeypatch.setenv("FAKE_CLAUDE_RECORDING", str(SAMPLES / recording / "recording.json"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-must-not-be-used")      # must be stripped for subscription runs
    if mode:
        monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)
    else:
        monkeypatch.delenv("FAKE_CLAUDE_MODE", raising=False)


def _live_engine(tmp_path, approver, runtime="claude-subscription", store=None):
    store = store or Store(tmp_path / "company.db")
    company = create_company(store, load_spec(SPEC))
    engine = Engine(store, company, get_playbook(), tmp_path / "ws", approver=approver, log=quiet, runtime=runtime)
    hire_agents(store, company, engine.governance)
    return store, company, engine


def test_subscription_runtime_end_to_end(tmp_path, monkeypatch):
    _fake_claude(monkeypatch, tmp_path)
    store, company, engine = _live_engine(tmp_path, gov.auto_approver("Lead"))
    engine.start_project((SAMPLES / PY / "story.md").read_text(), title="Promo codes (subscription)")
    assert engine.run() == "released"
    runs = store.all("SELECT * FROM runs")
    assert runs and all(r["adapter"] == "ClaudeCodeAdapter/subscription" for r in runs)
    assert all(r["cost_usd"] == 0.0123 and r["input_tokens"] == 1200 and not r["tokens_estimated"] for r in runs)
    assert engine.concurrency <= 2
    from core.state import company_state
    assert company_state(store, company)["spend"]["billing"] == "subscription"


def test_subscription_not_signed_in_pauses_then_resumes(tmp_path, monkeypatch):
    _fake_claude(monkeypatch, tmp_path, mode="auth_error")
    store, company, engine = _live_engine(tmp_path, gov.auto_approver("Lead"))
    engine.start_project((SAMPLES / PY / "story.md").read_text(), title="Promo codes")
    assert engine.run() == "paused"
    assert "not signed in" in engine.paused_reason
    from core.state import company_state
    st = company_state(store, company)["projects"][0]
    assert st["status"] == "paused" and "not signed in" in st["paused_reason"]
    assert all(t["status"] in ("todo",) for t in engine.tasks())      # nothing lost, nothing half-done
    _fake_claude(monkeypatch, tmp_path)                                # user signs in
    e2 = Engine(store, company, get_playbook(), tmp_path / "ws", approver=gov.auto_approver("Lead"), log=quiet,
                runtime="claude-subscription")
    e2.attach(engine.goal["id"])
    assert e2.run() == "released"


def test_api_runtime_without_key_pauses_with_hint(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    store, company, engine = _live_engine(tmp_path, gov.auto_approver("Lead"), runtime="api")
    engine.start_project("Add a hello endpoint", title="Hello")
    assert engine.run() == "paused"
    assert "claude-subscription" in engine.paused_reason
