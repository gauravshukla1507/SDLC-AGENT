"""Spec-driven development playbook and the Graphify codebase graph (recorded agents, real tools)."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import codegraph  # noqa: E402
from core import governance as gov  # noqa: E402
from core.company import create_company, hire_agents, load_spec  # noqa: E402
from core.engine import Engine  # noqa: E402
from core.llm import ReplayLLM  # noqa: E402
from core.playbook import get_playbook  # noqa: E402
from core.store import Store  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "python-sdd-existing-shop"
SPEC_DIR = "specs/001-promo-codes-at-checkout/"
needs_graphify = pytest.mark.skipif(codegraph.cli() is None, reason="graphify not installed (pip install graphifyy)")
quiet = lambda *a, **k: None  # noqa: E731


def existing_product(tmp_path) -> Path:
    ws = tmp_path / "shop"
    shutil.copytree(FIX / "existing", ws)
    return ws


def reviewer(reject_spec_once=True, spec_comment=None):
    """Requests changes on the first spec review, then approves everything."""
    state = {"spec": 0}

    def decide(approval):
        if approval["type"] == "spec":
            state["spec"] += 1
            if reject_spec_once and state["spec"] == 1:
                return {"approved": False, "approver": "Priya (PO)",
                        "comment": "Minimum order value is checked on the subtotal before any discount."}
            return {"approved": True, "approver": "Priya (PO)", "comment": spec_comment or ""}
        return {"approved": True, "approver": "Lead", "comment": ""}
    return decide


def start(tmp_path, ws, approver, store=None, title="Promo codes at checkout"):
    store = store or Store(tmp_path / "company.db")
    company = store.one("SELECT * FROM companies") or create_company(store, load_spec(ROOT / "companies" / "default-company.json"))
    engine = Engine(store, company, get_playbook("spec-driven"), ws, approver=approver,
                    replay=ReplayLLM(FIX / "recording.json"), log=quiet)
    hire_agents(store, company, engine.governance)
    engine.start_project((FIX / "story.md").read_text(), title=title)
    return store, engine


def test_spec_driven_on_existing_product_with_spec_revision(tmp_path):
    ws = existing_product(tmp_path)
    store, engine = start(tmp_path, ws, reviewer())
    assert engine.run() == "released"
    tasks = {t["key"]: t for t in engine.tasks()}

    # constitution, spec, plan and tasks are committed in place, Spec Kit layout
    assert (ws / ".specify/memory/constitution.md").exists()
    spec = (ws / SPEC_DIR / "spec.md").read_text()
    assert "Revised after review" in spec and "NEEDS CLARIFICATION" not in spec
    assert (ws / SPEC_DIR / "plan.md").exists() and "[x] T004" in (ws / SPEC_DIR / "tasks.md").read_text()

    # "request changes" went back to the spec author, then the spec was reviewed again
    assert tasks["revise-spec_review-1"]["assignee"] == "requirements"
    assert tasks["revise-spec_review-1"]["status"] == "done"
    assert tasks["spec_review"]["status"] == "done"
    reviews = store.all("SELECT type, status FROM approvals WHERE type IN ('spec','plan') ORDER BY created")
    assert [(r["type"], r["status"]) for r in reviews] == [("spec", "rejected"), ("spec", "approved"),
                                                         ("plan", "approved")]
    routes = [e for e in engine.events if e["kind"] == "route" and e.get("reason") == "spec_changes_requested"]
    assert routes and routes[0]["to"] == "requirements"

    # tests first from the spec; every acceptance criterion has a test
    gates = {e["tool"]: e for e in engine.events if e["kind"] == "tool"}
    assert gates["verify"]["passed"] is True
    assert gates["spec_coverage"]["passed"] is True and "9" in gates["spec_coverage"]["summary"]
    # existing code is untouched and still tested
    assert (ws / "src/shop/cart.py").read_text() == (FIX / "existing/src/shop/cart.py").read_text()

    # the codebase map ran for the existing product (or was skipped with a reason, never "passed" silently)
    m = gates["map"]
    assert m["passed"] is True if codegraph.cli() else m.get("skipped")

    # the spec folder is visible to UIs as the project's documents
    vars_ = store.one("SELECT details FROM activity WHERE action='project.vars'")
    assert vars_["details"]["spec_dir"] == SPEC_DIR
    from core.state import company_state
    proj = company_state(store, engine.company)["projects"][0]
    assert proj["playbook"] == "spec-driven" and proj["spec_dir"] == SPEC_DIR
    assert {Path(d).name for d in proj["documents"]} >= {"spec.md", "plan.md", "tasks.md", "constitution.md"}


def test_second_feature_reuses_the_constitution_and_numbers_the_spec(tmp_path):
    ws = existing_product(tmp_path)
    (ws / ".specify/memory").mkdir(parents=True)
    (ws / ".specify/memory/constitution.md").write_text("# Constitution\n1. Money is Decimal.\n")
    (ws / "specs/001-cart").mkdir(parents=True)
    pb = get_playbook("spec-driven")
    assert "constitution" not in [s.key for s in pb.steps(ws)]
    assert pb.feature_vars(ws, "Gift cards & vouchers!") == {"spec_dir": "specs/002-gift-cards-vouchers/"}
    # the existing constitution reaches every agent
    ctx = pb.task_context(ws, {"key": "plan", "spec": {}}, {"title": "x"}, {})
    assert "Money is Decimal" in ctx


def test_approving_with_a_comment_answers_open_questions_in_the_spec(tmp_path):
    ws = existing_product(tmp_path)
    store, engine = start(tmp_path, ws, reviewer(reject_spec_once=False, spec_comment="Before discounts."))
    assert engine.run() == "released"
    spec = (ws / SPEC_DIR / "spec.md").read_text()
    assert "## Clarifications" in spec and "Before discounts." in spec


def test_spec_review_can_be_turned_off_by_governance(tmp_path):
    ws = existing_product(tmp_path)
    store = Store(tmp_path / "company.db")
    spec = load_spec(ROOT / "companies" / "default-company.json")
    spec.setdefault("governance", {}).update({"spec_approval": False, "plan_approval": False})
    company = create_company(store, spec)
    engine = Engine(store, company, get_playbook("spec-driven"), ws, approver=gov.auto_approver("Lead"),
                    replay=ReplayLLM(FIX / "recording.json"), log=quiet)
    hire_agents(store, company, engine.governance)
    engine.start_project((FIX / "story.md").read_text(), title="Promo codes at checkout")
    engine.run()
    assert not store.all("SELECT * FROM approvals WHERE type IN ('spec','plan')")


def test_spec_coverage_gate_names_missing_criteria_and_routes_to_tester(tmp_path):
    pb = get_playbook("spec-driven")
    files = {SPEC_DIR + "spec.md": "- **AC-1** a\n- **AC-2** b\n- **AC-10** c\n",
             SPEC_DIR + "tasks.md": "T001 (AC-1, AC-2)",
             "tests/test_x.py": "def test_a():\n    '''AC-1'''\n", "src/x.py": "# AC-2 is not a test"}
    r = pb.run_gate("spec_coverage", tmp_path, [], files)
    assert r["passed"] is False and "AC-2" in r["summary"] and "AC-10" in r["summary"]
    assert "AC-10" in r["output"] and "not referenced in tasks.md: AC-10" in r["output"]
    assert pb.route_fix({**r, "tool": "spec_coverage"}, files) == "tester"
    assert pb.run_gate("spec_coverage", tmp_path, [], {"src/x.py": ""})["passed"] is False  # no spec at all


def test_map_is_skipped_for_a_new_application(tmp_path):
    r = get_playbook("spec-driven").run_gate("map", tmp_path, [], {})
    assert r["skipped"] and r["passed"] is None


@needs_graphify
def test_codebase_graph_build_query_and_blast_radius(tmp_path):
    ws = existing_product(tmp_path)
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    r = codegraph.build(ws, has_code=True)
    assert r["passed"], r
    st = codegraph.status(ws)
    assert st["built"] and st["nodes"] > 0
    assert "graphify-out/" in (ws / ".gitignore").read_text()
    assert "to_money" in codegraph.query(ws, "money rounding to_money")
    radius = codegraph.blast_radius(ws, ["src/shop/money.py"])
    assert "cart" in radius.lower()
    assert codegraph.architecture_summary(ws)


def test_graphify_can_be_turned_off(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTIC_GRAPHIFY", "off")
    r = codegraph.build(existing_product(tmp_path), has_code=True)
    assert r["skipped"] and "off" in r["summary"].lower()


def test_cli_default_playbook_is_spec_driven_and_graph_command(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "AGENTIC_PLAYBOOK"}
    cli = lambda *a: subprocess.run([sys.executable, str(ROOT / "agentic.py"), *a], capture_output=True,  # noqa: E731
                                    text=True, env=env)
    p = cli("playbook", "-w", str(tmp_path))
    assert p.stdout.startswith("spec-driven") and "spec_review" in p.stdout
    assert cli("playbook", "--playbook", "classic", "-w", str(tmp_path)).stdout.startswith("universal")
    st = json.loads(cli("graph", "status", "--json", "-w", str(tmp_path)).stdout)
    assert st["built"] is False


def test_cli_deferred_spec_review_request_changes_then_resume(tmp_path):
    ws = existing_product(tmp_path)
    env = {k: v for k, v in os.environ.items() if k != "AGENTIC_PLAYBOOK"}
    cli = lambda *a: subprocess.run([sys.executable, str(ROOT / "agentic.py"), *a, "-w", str(ws)],  # noqa: E731
                                    capture_output=True, text=True, env=env)
    run = cli("run", "--requirements", str(FIX / "story.md"), "--title", "Promo codes at checkout",
              "--approver", "deferred", "--replay", str(FIX / "recording.json"))
    assert "Waiting for a human" in run.stdout, run.stdout + run.stderr
    cli("approve", "all", "--type", "hire", "--as", "Lead")
    cli("resume", "--approver", "deferred")
    pending = json.loads(cli("approvals", "--json").stdout)
    assert [p["type"] for p in pending] == ["spec"]
    assert pending[0]["payload"]["questions"] and pending[0]["payload"]["document"] == SPEC_DIR + "spec.md"
    assert pending[0]["payload"]["summary"] and pending[0]["payload"]["author"]
    assert cli("approve", pending[0]["id"], "--reject", "--as", "PO").returncode == 2   # comment required
    cli("approve", pending[0]["id"], "--reject", "--as", "PO", "--comment", "Minimum is on the subtotal.")
    cli("resume", "--approver", "deferred")
    pending = json.loads(cli("approvals", "--json").stdout)
    assert [p["type"] for p in pending] == ["spec"] and pending[0]["payload"]["revision"] == 1
    assert "Revised after review" in (ws / SPEC_DIR / "spec.md").read_text()
    for _ in range(3):    # spec, plan, release
        p = json.loads(cli("approvals", "--json").stdout)
        if not p:
            break
        cli("approve", "all", "--as", "Lead")
        cli("resume", "--approver", "deferred")
    state = json.loads(cli("state", "--json").stdout)
    assert state["projects"][0]["status"] == "released" and state["projects"][0]["playbook"] == "spec-driven"
