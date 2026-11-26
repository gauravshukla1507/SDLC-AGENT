#!/usr/bin/env python3
"""Universal Orchestrator CLI: run AI agents as a company, on any technology.

The stack is detected from the repository (Salesforce, Java, .NET, Node/TypeScript, Python, Go, Rust,
Ruby, PHP, dbt, Terraform, or several at once). There is nothing to choose.

  uo.py run       --company companies/zenlabs-delivery.json --request-file story.md -w ./any-repo
  uo.py stacks    -w ./any-repo                             # what was detected and how it will be verified
  uo.py demo      --sample node -w /tmp/uo-demo             # recorded run (salesforce | python | node), no API key
  uo.py approvals -w ./my-sfdx                              # what is waiting for a human
  uo.py approve   <approval-id> --as "Gaurav" [--reject] [--comment ...]
  uo.py resume    -w ./my-sfdx                              # continue projects after approvals
  uo.py org | board | budget | activity | dashboard | routines | export | import

State lives in <workspace>/.agentic-sdlc/company.db (SQLite).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import governance as gov  # noqa: E402
from core.company import create_company, hire_agents, load_spec, org_chart  # noqa: E402
from core.dashboard import render_dashboard  # noqa: E402
from core.engine import Engine  # noqa: E402
from core.llm import ReplayLLM  # noqa: E402
from core.pack import get_pack  # noqa: E402
from core.portability import export_company, import_company  # noqa: E402
from core.routines import due_routines, mark_run  # noqa: E402
from core.store import Store  # noqa: E402

HERE = Path(__file__).resolve().parent
_OUT = threading.Lock()


def out(line: str = ""):
    with _OUT:
        sys.stdout.write(str(line) + "\n")
        sys.stdout.flush()


STORE_OVERRIDE: str | None = None


def db_path(ws: Path) -> Path:
    """Company state: --store / $UO_STORE (one company across many repos) or <workspace>/.agentic-sdlc/company.db."""
    return Path(STORE_OVERRIDE or os.environ.get("UO_STORE") or Path(ws) / ".agentic-sdlc" / "company.db")


def open_company(ws: Path, spec_path: str | None = None, name: str | None = None):
    store = Store(db_path(ws))
    if spec_path:
        company = create_company(store, load_spec(spec_path))
    else:
        company = (store.one("SELECT * FROM companies WHERE name=?", name) if name
                   else store.one("SELECT * FROM companies ORDER BY created LIMIT 1"))
        if not company:
            sys.exit(f"No company in {db_path(ws)}. Start with: uo.py run --company <spec.json> ...")
    return store, company


def make_approver(value: str):
    if value.startswith("auto:"):
        return gov.auto_approver(value.split(":", 1)[1])
    if value.startswith("hires:"):            # auto-approve hiring only, defer everything else
        return gov.auto_approver(value.split(":", 1)[1], "Hire approved", types={"hire"})
    return {"cli": gov.cli_approver, "stdin": gov.stdin_approver, "deferred": gov.deferred_approver}[value]


def emitter(enabled: bool):
    return (lambda ev: out("@@EVENT " + json.dumps(ev, default=str))) if enabled else None


def gate_options(args) -> dict:
    return {"target_org": getattr(args, "target_org", None) or os.environ.get("SF_TARGET_ORG"),
            "min_coverage": getattr(args, "min_coverage", 75)}


# ------------------------------------------------------------------ commands
def cmd_run(args, replay_path: Path | None = None):
    ws = Path(args.workspace)
    if getattr(args, "clean", False) and ws.exists():
        shutil.rmtree(ws)
    store, company = open_company(ws, args.company)
    pack = get_pack()
    request = args.request or Path(args.request_file).read_text(encoding="utf-8")
    approver = make_approver(args.approver)
    replay = ReplayLLM(replay_path) if replay_path else None
    engine = Engine(store, company, pack, ws, approver=approver, replay=replay, log=out,
                    max_fix_iterations=args.max_fix_iterations, on_event=emitter(args.events),
                    gate_options=gate_options(args))
    hire_agents(store, company, engine.governance)
    goal = engine.start_project(request, title=args.title, budget_usd=args.budget)
    out(f"Project {goal['id']}: {goal['title']}  (company: {company['name']}; stack: detected from {ws})")
    status = engine.run()
    if status in ("waiting_approval", "escalated"):
        out("\nWaiting for a human. See: uo.py approvals -w " + str(ws))
    return 0 if status == "released" else (2 if status in ("waiting_approval", "escalated") else 1)


SAMPLES = HERE / "samples"


def cmd_demo(args):
    sample = SAMPLES / {"salesforce": "salesforce-closed-won", "python": "python-promo-codes",
                        "node": "node-late-fees"}[args.sample]
    meta = json.loads((sample / "meta.json").read_text(encoding="utf-8"))
    args.company = str(HERE / "companies" / "zenlabs-delivery.json")
    args.request, args.request_file, args.title, args.budget = None, str(sample / "story.md"), meta["title"], None
    if args.clean_demo:
        args.clean = True
    else:
        args.clean = False
    return cmd_run(args, replay_path=sample / "recording.json")


def cmd_stacks(args):
    import stacks
    ws = Path(args.workspace)
    detected, checks = stacks.checks_for(ws, "verify")
    _, env = stacks.checks_for(ws, "environment")
    out(stacks.describe(detected))
    out("\nVerification plan:")
    for c in checks:
        tool = "built-in" if c.runner else (" ".join(c.cmd) + ("" if shutil.which(c.tool) else "   <- not installed"))
        out(f"  {c.kind:<8} {'blocking' if c.blocking else 'advisory':<9} {c.name:<26} {tool}")
    if any(s.name == "salesforce" for s in detected):
        out("  env      blocking  Salesforce org validation  sf project deploy validate (needs --target-org)")
    for c in env:
        out(f"  env      {'blocking' if c.blocking else 'advisory':<9} {c.name:<26} {' '.join(c.cmd)}")
    return 0


def cmd_resume(args):
    store, company = open_company(Path(args.workspace), name=args.company_name)
    code = 0
    projects = store.all("SELECT * FROM goals WHERE company_id=? AND level='project' ORDER BY created", company["id"])
    for g in projects:
        tasks = store.all("SELECT status FROM tasks WHERE goal_id=?", g["id"])
        if all(t["status"] in ("done", "cancelled") for t in tasks):
            continue
        engine = Engine(store, company, get_pack(), Path(args.workspace), approver=make_approver(args.approver), log=out,
                        on_event=emitter(args.events), gate_options=gate_options(args),
                        replay=ReplayLLM(args.replay) if args.replay else None)
        engine.attach(g["id"])
        out(f"Resuming {g['title']}")
        status = engine.run()
        code = max(code, 0 if status == "released" else 2)
    return code


def cmd_approvals(args):
    store, company = open_company(Path(args.workspace), name=args.company_name)
    rows = gov.Governance(store, company, lambda *a, **k: None).pending()
    if not rows:
        out("No pending approvals.")
    for r in rows:
        out(f"{r['id']}  [{r['type']}]  {r['subject']}")
        for k, v in (r["payload"] or {}).items():
            if v not in (None, "", [], {}):
                out(f"      {k}: {json.dumps(v, default=str)[:220]}")
    return 0


def cmd_approve(args):
    store, company = open_company(Path(args.workspace), name=args.company_name)
    g = gov.Governance(store, company, lambda *a, **k: store.log(company["id"], *a, **k))
    ids = [r["id"] for r in g.pending()] if args.id == "all" else [args.id]
    for i in ids:
        r = g.resolve(i, not args.reject, args.as_name, args.comment or "")
        out(f"{r['id']}: {r['status']} by {r['decided_by']}")
    out("Run `uo.py resume` to continue.")
    return 0


def cmd_org(args):
    store, company = open_company(Path(args.workspace), name=args.company_name)
    out(f"{company['name']}\nMission: {company['mission']}\n")
    for a in org_chart(store, company["id"]):
        budget = f"${a['spent_usd']:.4f}/{a['budget_usd']:.2f}" if a["budget_usd"] else ""
        out(f"{'    ' * a['depth']}{a['name']} - {a['title']}  [{a['status']}] {budget}")
    return 0


def cmd_board(args):
    store, company = open_company(Path(args.workspace), name=args.company_name)
    for g in store.all("SELECT * FROM goals WHERE company_id=? AND level='project' ORDER BY created", company["id"]):
        out(f"\n{g['title']}  ({g['id']})")
        for t in store.all("SELECT * FROM tasks WHERE goal_id=? ORDER BY created", g["id"]):
            out(f"  {t['status']:<12} {t['assignee']:<13} {t['title']}")
    return 0


def cmd_budget(args):
    store, company = open_company(Path(args.workspace), name=args.company_name)
    total = store.one("SELECT COALESCE(SUM(cost_usd),0) s, MAX(tokens_estimated) e FROM runs WHERE company_id=?",
                      company["id"])
    limit = (company["spec"] or {}).get("budgets", {}).get("company_usd")
    out(f"Company spend: ${total['s']:.4f}" + (f" of ${limit:.2f}" if limit else "") +
        ("  (includes estimated tokens)" if total["e"] else ""))
    for a in store.all("SELECT * FROM agents WHERE company_id=? AND budget_usd IS NOT NULL", company["id"]):
        out(f"  {a['name']:<28} ${a['spent_usd']:.4f} of ${a['budget_usd']:.2f}")
    return 0


def cmd_activity(args):
    store, company = open_company(Path(args.workspace), name=args.company_name)
    for r in store.all("SELECT * FROM activity WHERE company_id=? ORDER BY seq DESC LIMIT ?", company["id"], args.n)[::-1]:
        out(f"{r['ts'][11:19]}  {r['actor']:<22} {r['action']:<22} {r['entity']}")
    return 0


def cmd_dashboard(args):
    store, company = open_company(Path(args.workspace), name=args.company_name)
    target = Path(args.out or (Path(args.workspace) / ".agentic-sdlc" / "dashboard.html"))
    target.write_text(render_dashboard(store, company), encoding="utf-8")
    out(f"Dashboard written to {target}")
    return 0


def cmd_routines(args):
    store, company = open_company(Path(args.workspace), name=args.company_name)
    due = due_routines(store, company["id"])
    rows = store.all("SELECT * FROM routines WHERE company_id=?", company["id"])
    for r in rows:
        out(f"{r['key']:<26} {r['schedule']:<20} last run: {r['last_run'] or 'never'}  "
            f"{'DUE' if r in due else ''}")
    if args.run_due:
        for r in due:
            spec = r["spec"]
            engine = Engine(store, company, get_pack(), Path(args.workspace),
                            approver=make_approver(args.approver), log=out, gate_options=gate_options(args))
            engine.start_project(spec["request"], title=spec.get("title"))
            mark_run(store, r["id"])
            store.log(company["id"], "routine", "routine.fired", f"routine:{r['key']}")
            engine.run()
    return 0


def cmd_export(args):
    store, company = open_company(Path(args.workspace), name=args.company_name)
    data = export_company(store, company, [Path(args.workspace) / ".agentic-sdlc" / "skills"])
    Path(args.out).write_text(json.dumps(data, indent=2), encoding="utf-8")
    out(f"Exported {company['name']} to {args.out} (no secrets; review before sharing)")
    return 0


def cmd_import(args):
    ws = Path(args.workspace)
    spec = import_company(Path(args.file), ws / ".agentic-sdlc" / "skills")
    store = Store(db_path(ws))
    company = create_company(store, spec)
    out(f"Imported {company['name']} into {db_path(ws)}")
    return 0


def cmd_playbook(args):
    p = get_pack()
    out(f"{p.name}: {p.description}")
    for s in p.playbook():
        out(f"    {s.kind:<9} {s.key:<18} {s.role or '':<13} after: {', '.join(s.deps) or '-'}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, company_spec=False):
        p.add_argument("-w", "--workspace", default=".", help="Project folder (state in .agentic-sdlc/)")
        p.add_argument("--company-name", help="Company to use if the store has several")
        p.add_argument("--store", help="Company database shared across repos (default: <workspace>/.agentic-sdlc/company.db"
                                        " or $UO_STORE)")
        if company_spec:
            p.add_argument("--company", required=True, help="Company spec JSON (created on first use)")
        return p

    def run_opts(p):
        p.add_argument("--approver", default="cli",
                       help="cli | stdin | deferred | auto:<name> | hires:<name> (auto-approve hiring only)")
        p.add_argument("--max-fix-iterations", type=int, default=3)
        p.add_argument("--target-org", help="sf org alias for the Salesforce org gate (default $SF_TARGET_ORG)")
        p.add_argument("--min-coverage", type=int, default=75)
        p.add_argument("--events", action="store_true", help="Stream @@EVENT lines for IDE integrations")
        return p

    p = run_opts(common(sub.add_parser("run", help="Start a delivery project"), company_spec=True))
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--request")
    g.add_argument("--request-file")
    p.add_argument("--title")
    p.add_argument("--budget", type=float, help="Project budget in USD (default from company spec)")
    p.add_argument("--clean", action="store_true")
    p.set_defaults(fn=cmd_run)

    p = run_opts(common(sub.add_parser("demo", help="Recorded run of a sample (no API key)")))
    p.add_argument("--sample", choices=["salesforce", "python", "node"], default="salesforce")
    p.add_argument("--keep", dest="clean_demo", action="store_false",
                   help="Keep the workspace (run several samples into one company)")
    p.set_defaults(fn=cmd_demo, approver="auto:Demo Approver", clean_demo=True, title=None, budget=None)

    p = run_opts(common(sub.add_parser("resume", help="Continue projects after approvals")))
    p.add_argument("--replay", help="Recording to replay (demos)")
    p.set_defaults(fn=cmd_resume)

    common(sub.add_parser("approvals", help="List pending approvals")).set_defaults(fn=cmd_approvals)
    p = common(sub.add_parser("approve", help="Approve or reject (id or 'all')"))
    p.add_argument("id")
    p.add_argument("--as", dest="as_name", required=True)
    p.add_argument("--reject", action="store_true")
    p.add_argument("--comment")
    p.set_defaults(fn=cmd_approve)

    common(sub.add_parser("org", help="Org chart")).set_defaults(fn=cmd_org)
    common(sub.add_parser("board", help="Task board")).set_defaults(fn=cmd_board)
    common(sub.add_parser("budget", help="Spend vs budgets")).set_defaults(fn=cmd_budget)
    p = common(sub.add_parser("activity", help="Audit trail"))
    p.add_argument("-n", type=int, default=40)
    p.set_defaults(fn=cmd_activity)
    p = common(sub.add_parser("dashboard", help="Write the company dashboard HTML"))
    p.add_argument("--out")
    p.set_defaults(fn=cmd_dashboard)
    p = run_opts(common(sub.add_parser("routines", help="List routines; --run-due starts due ones")))
    p.add_argument("--run-due", action="store_true")
    p.set_defaults(fn=cmd_routines)
    p = common(sub.add_parser("export", help="Export the company (no secrets)"))
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_export)
    p = common(sub.add_parser("import", help="Import a company export"))
    p.add_argument("file")
    p.set_defaults(fn=cmd_import)
    common(sub.add_parser("stacks", help="Show detected stacks and the verification plan")).set_defaults(fn=cmd_stacks)
    sub.add_parser("playbook", help="Show the delivery playbook").set_defaults(fn=cmd_playbook, store=None)

    args = ap.parse_args(argv)
    global STORE_OVERRIDE
    STORE_OVERRIDE = getattr(args, "store", None)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
