#!/usr/bin/env python3
"""Agentic SDLC: run an AI delivery company on any repository, in any technology.

The stack is detected from the repository (Salesforce, Java, .NET, Node/TypeScript, Python, Go, Rust,
Ruby, PHP, dbt, Terraform, or several at once). Nothing to configure per technology.

Delivery
  agentic.py run      --requirements BRD.docx -w ./repo        build from a requirements document (.md .txt .docx .pdf)
                                                               into an existing repo or a new empty folder
  agentic.py resume   -w ./repo                                continue projects after approvals

People
  agentic.py approvals -w ./repo                               what is waiting for a human
  agentic.py approve  <id|all> --as "Name" [--reject] [--comment ...]

Insight
  agentic.py stacks | state | org | board | budget | activity | dashboard | replay-page | playbook | doctor
Company
  agentic.py init | routines [--run-due] | export --out f.json | import f.json

Agents run on your Claude subscription (Pro/Max/Team/Enterprise) through Claude Code with
--runtime claude-subscription (or $AGENTIC_RUNTIME), or per token with an API key (--runtime api).
`agentic.py doctor` checks the setup.

State lives in <workspace>/.agentic-sdlc/company.db, or in a shared store (--store / $AGENTIC_STORE)
so one company can serve many repositories. Add --json to read commands for machine output.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import stacks  # noqa: E402
from core import governance as gov  # noqa: E402
from core.adapters import RUNTIMES, claude_cli, subscription_env  # noqa: E402
from core.company import create_company, hire_agents, load_spec, org_chart  # noqa: E402
from core.dashboard import render_dashboard  # noqa: E402
from core.documents import DocumentError, read_requirements, title_of  # noqa: E402
from core.engine import Engine  # noqa: E402
from core.llm import ReplayLLM  # noqa: E402
from core.playbook import available_playbooks, get_playbook  # noqa: E402
from core.portability import export_company, import_company  # noqa: E402
from core.routines import due_routines, mark_run  # noqa: E402
from core.state import company_state  # noqa: E402
from core.store import Store  # noqa: E402

DEFAULT_SPEC = HERE / "companies" / "default-company.json"
_OUT = threading.Lock()
STORE_OVERRIDE: str | None = None


def out(line: str = ""):
    with _OUT:
        sys.stdout.write(str(line) + "\n")
        sys.stdout.flush()


def emit_json(data):
    out(json.dumps(data, indent=2, default=str))


# ------------------------------------------------------------------ company & store
def agentic_dir(ws: Path) -> Path:
    return Path(ws) / ".agentic-sdlc"


def db_path(ws: Path) -> Path:
    return Path(STORE_OVERRIDE or os.environ.get("AGENTIC_STORE") or agentic_dir(ws) / "company.db")


def spec_path_for(ws: Path, explicit: str | None) -> Path:
    """Company spec: --company, else the repo's .agentic-sdlc/company.json, else the built-in default."""
    if explicit:
        return Path(explicit)
    local = agentic_dir(ws) / "company.json"
    return local if local.exists() else DEFAULT_SPEC


def open_company(ws: Path, spec: str | None = None, name: str | None = None, create: bool = False):
    store = Store(db_path(ws))
    company = (store.one("SELECT * FROM companies WHERE name=?", name) if name
               else store.one("SELECT * FROM companies ORDER BY created LIMIT 1"))
    if company is None or (spec and create):
        if not create:
            sys.exit(f"No company in {db_path(ws)}. Run `agentic.py init` or `agentic.py run` first.")
        company = create_company(store, load_spec(spec_path_for(ws, spec)))
    return store, company


def make_approver(value: str):
    if value.startswith("auto:"):
        return gov.auto_approver(value.split(":", 1)[1])
    if value.startswith("hires:"):
        return gov.auto_approver(value.split(":", 1)[1], "Hire approved", types={"hire"})
    return {"cli": gov.cli_approver, "stdin": gov.stdin_approver, "deferred": gov.deferred_approver}[value]


def emitter(enabled: bool):
    return (lambda ev: out("@@EVENT " + json.dumps(ev, default=str))) if enabled else None


def gate_options(args) -> dict:
    return {"target_org": getattr(args, "target_org", None) or os.environ.get("SF_TARGET_ORG"),
            "min_coverage": getattr(args, "min_coverage", 75)}


def replay_for(ws: Path, explicit: str | None):
    """Internal (tests): a run started with a recording remembers it so `resume` replays it too."""
    if explicit:
        return ReplayLLM(explicit)
    marker = agentic_dir(ws) / "replay.json"
    if marker.exists():
        return ReplayLLM(json.loads(marker.read_text(encoding="utf-8"))["recording"])
    return None


DOC_REF = re.compile(r"[\w./\\ -]*?[\w-]+\.(?:md|markdown|txt|docx|pdf)\b", re.I)


def expand_file_reference(request: str, ws: Path) -> str:
    """A short request that only points at a document ("use brd.md") gets the document's content: agents
    cannot open files they are merely told about."""
    if len(request) > 300:
        return request
    for m in DOC_REF.finditer(request):
        name = Path(m.group(0).strip().split()[-1])
        candidates = [ws / name, Path.cwd() / name]
        if ws.exists():
            candidates += [ws / f for f in stacks.walk_files(ws) if Path(f).name.lower() == name.name.lower()]
        for cand in candidates:
            if Path(cand).is_file():
                out(f"Request refers to {Path(cand).name}: using the document's content as the requirements.")
                return read_requirements(cand)
        raise DocumentError(f"The request mentions '{name}' but no such file was found in {ws}. "
                            "Pass the document itself with --requirements <path>.")
    return request


def exit_code(status: str) -> int:
    return {"released": 0, "waiting_approval": 2, "escalated": 2, "paused": 3}.get(status, 1)


def playbook_for(args, store=None, goal_id=None):
    """The playbook a project was started with (resume keeps it); otherwise --playbook / AGENTIC_PLAYBOOK / default."""
    if store is not None and goal_id:
        row = store.one("SELECT details FROM activity WHERE action='project.created' AND entity=?", f"goal:{goal_id}")
        details = (row or {}).get("details") or {}
        if isinstance(details, str):
            try:
                details = json.loads(details)
            except ValueError:
                details = {}
        name = details.get("playbook")
        if name:
            return get_playbook(name)
    return get_playbook(getattr(args, "playbook", None))


def runtime_of(args) -> str:
    return getattr(args, "runtime", None) or os.environ.get("AGENTIC_RUNTIME") or "api"


# ------------------------------------------------------------------ delivery
def cmd_init(args):
    ws = Path(args.workspace)
    store, company = open_company(ws, args.company, args.company_name, create=True)
    hire_agents(store, company, gov.Governance(store, company, lambda *a, **k: store.log(company["id"], *a, **k),
                                               make_approver(args.approver)))
    out(f"Company '{company['name']}' ready in {db_path(ws)}")
    return 0


def _start(args, replay=None, request=None, title=None):
    ws = Path(args.workspace)
    if getattr(args, "clean", False) and ws.exists():
        shutil.rmtree(ws)
    ws.mkdir(parents=True, exist_ok=True)
    if replay is not None:
        agentic_dir(ws).mkdir(parents=True, exist_ok=True)
        (agentic_dir(ws) / "replay.json").write_text(json.dumps({"recording": str(replay.path)}), encoding="utf-8")
    store, company = open_company(ws, args.company, args.company_name, create=True)
    engine = Engine(store, company, playbook_for(args), ws, approver=make_approver(args.approver), replay=replay,
                    log=out, max_fix_iterations=args.max_fix_iterations, on_event=emitter(args.events),
                    gate_options=gate_options(args), runtime=runtime_of(args))
    hire_agents(store, company, engine.governance)
    if not request:
        if args.request:
            try:
                request = expand_file_reference(args.request, ws)
            except DocumentError as exc:
                out(f"Cannot read requirements: {exc}")
                return 1
        else:
            try:
                request = read_requirements(args.requirements)
            except DocumentError as exc:
                out(f"Cannot read requirements: {exc}")
                return 1
    goal = engine.start_project(request, title=title or args.title or title_of(request), budget_usd=args.budget)
    out(f"Project {goal['id']}: {goal['title']}  (company: {company['name']}; workspace: {ws})")
    status = engine.run()
    if status in ("waiting_approval", "escalated"):
        out(f"\nWaiting for a human. See: agentic.py approvals -w {ws}")
    if status == "paused":
        out(f"\nPaused: {engine.paused_reason}\nFix that, then: agentic.py resume -w {ws}")
    return exit_code(status)


def cmd_run(args):
    return _start(args, replay=ReplayLLM(args.replay) if args.replay else None)


def cmd_resume(args):
    ws = Path(args.workspace)
    store, company = open_company(ws, name=args.company_name)
    approver = make_approver(args.approver)
    code = 0
    for g in store.all("SELECT * FROM goals WHERE company_id=? AND level='project' ORDER BY created", company["id"]):
        if args.project and g["id"] != args.project:
            continue
        tasks = store.all("SELECT status FROM tasks WHERE goal_id=?", g["id"])
        if all(t["status"] in ("done", "cancelled") for t in tasks):
            continue
        pws = Path(g["workspace"] or ws)
        engine = Engine(store, company, playbook_for(args, store, g["id"]), pws, approver=approver, log=out,
                        on_event=emitter(args.events), gate_options=gate_options(args),
                        replay=replay_for(pws, args.replay), max_fix_iterations=args.max_fix_iterations,
                        runtime=runtime_of(args))
        engine.attach(g["id"])
        out(f"Resuming {g['title']} ({g['id']})")
        code = max(code, exit_code(engine.run()))
    return code


# ------------------------------------------------------------------ approvals
def cmd_approvals(args):
    store, company = open_company(Path(args.workspace), name=args.company_name)
    rows = gov.Governance(store, company, lambda *a, **k: None).pending()
    if args.json:
        return emit_json(rows) or 0
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
    pending = g.pending()
    if args.id == "all":
        ids = [r["id"] for r in pending if not args.type or r["type"] == args.type]
    else:
        ids = [args.id]
    if args.reject and not args.comment:
        kinds = {r["id"]: r["type"] for r in pending}
        if any(kinds.get(i) in ("spec", "plan") for i in ids):
            out("Requesting changes to a spec or plan needs --comment: it tells the author what to change.")
            return 2
    results = [g.resolve(i, not args.reject, args.as_name, args.comment or "") for i in ids]
    if args.json:
        return emit_json(results) or 0
    for r in results:
        out(f"{r['id']}: {r['status']} by {r['decided_by']}")
    out("Run `agentic.py resume` to continue.")
    return 0


# ------------------------------------------------------------------ insight
def cmd_stacks(args):
    ws = Path(args.workspace)
    detected, checks = stacks.checks_for(ws, "verify")
    _, env = stacks.checks_for(ws, "environment")
    plan = [{"kind": c.kind, "blocking": c.blocking, "name": c.name, "stack": c.stack,
             "command": "built-in" if c.runner else " ".join(c.cmd),
             "installed": True if c.runner else bool(shutil.which(c.tool) or (ws / c.tool).exists())}
            for c in checks + env]
    if args.json:
        return emit_json({"stacks": [{"name": s.name, "evidence": s.evidence, "skill": s.skill} for s in detected],
                          "checks": plan, "profile": stacks.describe(detected)}) or 0
    out(stacks.describe(detected))
    out("\nVerification plan:")
    for c in plan:
        flag = "" if c["installed"] else "   <- not installed"
        out(f"  {c['kind']:<11} {'blocking' if c['blocking'] else 'advisory':<9} {c['name']:<28} {c['command']}{flag}")
    return 0


def cmd_state(args):
    ws = Path(args.workspace)
    if not db_path(ws).exists():
        return emit_json({"company": None, "agents": [], "projects": [], "approvals": [], "spend": {},
                          "routines": [], "store": str(db_path(ws))}) or 0
    store, company = open_company(ws, name=args.company_name)
    data = company_state(store, company)
    data["store"] = str(db_path(ws))
    return emit_json(data) or 0


def cmd_org(args):
    store, company = open_company(Path(args.workspace), name=args.company_name)
    out(f"{company['name']}\nMission: {company['mission']}\n")
    for a in org_chart(store, company["id"]):
        budget = f"${a['spent_usd']:.4f}/{a['budget_usd']:.2f}" if a["budget_usd"] else ""
        out(f"{'    ' * a['depth']}{a['name']} - {a['title']}  [{a['status']}] {budget}")
    return 0


def cmd_board(args):
    store, company = open_company(Path(args.workspace), name=args.company_name)
    for p in company_state(store, company)["projects"]:
        out(f"\n{p['title']}  [{p['status']}]  stacks: {', '.join(p['stacks']) or '-'}  ({p['id']})")
        for t in p["tasks"]:
            out(f"  {t['status']:<12} {t['assignee']:<13} {t['title']}")
    return 0


def cmd_budget(args):
    store, company = open_company(Path(args.workspace), name=args.company_name)
    s = company_state(store, company)
    lim = s["spend"]["limit_usd"]
    out(f"Company spend: ${s['spend']['total_usd']:.4f}" + (f" of ${lim:.2f}" if lim else "") +
        ("  (includes estimated tokens)" if s["spend"]["estimated"] else ""))
    for a in s["agents"]:
        if a["budget_usd"]:
            out(f"  {a['name']:<28} ${a['spent_usd']:.4f} of ${a['budget_usd']:.2f}")
    return 0


def cmd_activity(args):
    store, company = open_company(Path(args.workspace), name=args.company_name)
    rows = store.all("SELECT * FROM activity WHERE company_id=? ORDER BY seq DESC LIMIT ?", company["id"], args.n)[::-1]
    if args.json:
        return emit_json(rows) or 0
    for r in rows:
        out(f"{r['ts'][11:19]}  {r['actor']:<22} {r['action']:<24} {r['entity']}")
    return 0


def cmd_dashboard(args):
    store, company = open_company(Path(args.workspace), name=args.company_name)
    target = Path(args.out or (agentic_dir(Path(args.workspace)) / "dashboard.html"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_dashboard(store, company), encoding="utf-8")
    out(f"Dashboard written to {target}")
    return 0


def cmd_replay_page(args):
    trace = json.loads(Path(args.trace).read_text(encoding="utf-8"))
    template = (HERE / "core" / "replay_page.html").read_text(encoding="utf-8")
    target = Path(args.out or Path(args.trace).with_name("run_replay.html"))
    target.write_text(template.replace("/*__TRACE__*/", json.dumps(trace).replace("</", "<\\/")), encoding="utf-8")
    out(f"Run replay page written to {target}")
    return 0


def cmd_doctor(args):
    """Check what the agents need: Python, the chosen runtime (subscription or API key), and stack tools."""
    import subprocess
    runtime = runtime_of(args)
    checks = [{"check": "python", "ok": sys.version_info >= (3, 11), "detail": sys.version.split()[0]}]
    cli = claude_cli()
    version = ""
    if cli:
        try:
            version = subprocess.run([cli, "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
        except Exception as exc:  # noqa: BLE001
            version = f"error: {exc}"
    if runtime == "claude-subscription":
        checks.append({"check": "claude code cli", "ok": bool(cli),
                       "detail": version or "not found: npm install -g @anthropic-ai/claude-code"})
        if cli and args.probe:
            p = subprocess.run([cli, "-p", "--output-format", "json", "--tools", "", "--no-session-persistence"],
                               input="Reply with exactly: OK", capture_output=True, text=True, timeout=180,
                               env=subscription_env())
            try:
                data = json.loads(p.stdout)
                ok = not data.get("is_error")
                detail = "signed in; subscription reachable" if ok else str(data.get("result"))
            except json.JSONDecodeError:
                ok, detail = False, (p.stdout + p.stderr)[-300:]
            checks.append({"check": "claude sign-in (probe)", "ok": ok, "detail": detail})
        elif cli:
            checks.append({"check": "claude sign-in", "ok": None,
                           "detail": "run with --probe to test (sends one tiny prompt), or run `claude` and /login"})
        checks.append({"check": "api key not forcing API billing", "ok": True,
                       "detail": "ANTHROPIC_API_KEY is removed for agent runs" if os.environ.get("ANTHROPIC_API_KEY")
                       else "no ANTHROPIC_API_KEY set"})
    else:
        checks.append({"check": "anthropic api key", "ok": bool(os.environ.get("ANTHROPIC_API_KEY")),
                       "detail": "set" if os.environ.get("ANTHROPIC_API_KEY") else
                       "not set (or use --runtime claude-subscription)"})
    ws = Path(args.workspace)
    detected, plan = stacks.checks_for(ws)
    for c in plan:
        if c.cmd:
            checks.append({"check": f"tool for {c.name}", "ok": bool(shutil.which(c.tool)),
                           "detail": c.tool if shutil.which(c.tool) else f"{c.tool} not installed (check will be skipped)"})
    if args.json:
        return emit_json({"runtime": runtime, "stacks": [s.name for s in detected], "checks": checks}) or 0
    out(f"Runtime: {runtime}    Stacks here: {', '.join(s.name for s in detected) or 'none (greenfield)'}")
    for c in checks:
        mark = "ok " if c["ok"] else "?? " if c["ok"] is None else "!! "
        out(f"  {mark} {c['check']:<34} {c['detail']}")
    return 0 if all(c["ok"] is not False for c in checks[:3]) else 1


def cmd_playbook(args):
    p = get_playbook(args.playbook)
    out(f"{p.name}: {p.description}")
    out(f"    (available: {', '.join(available_playbooks())})")
    for s in p.steps(Path(args.workspace)):
        out(f"    {s.kind:<9} {s.key:<18} {s.role or '':<13} after: {', '.join(s.deps) or '-'}")
    return 0


def cmd_graph(args):
    """Codebase knowledge graph (Graphify) used by the spec-driven playbook on existing products."""
    from core import codegraph
    ws = Path(args.workspace)
    if args.action == "build":
        r = codegraph.build(ws, has_code=True)
        out(r["summary"])
        return 0 if r.get("passed") or r.get("skipped") else 1
    if args.action == "status":
        st = codegraph.status(ws)
        if args.json:
            return emit_json(st) or 0
        for k, v in st.items():
            out(f"  {k:<10} {v}")
        return 0
    if not codegraph.status(ws).get("built"):
        out("No codebase graph yet. Run: agentic.py graph build")
        return 1
    if args.action == "query":
        out(codegraph.query(ws, " ".join(args.text)) or "No matching nodes found.")
    elif args.action == "affected":
        out(codegraph.blast_radius(ws, args.text) or "No dependants found.")
    elif args.action == "summary":
        out(codegraph.architecture_summary(ws) or "No report.")
    return 0


# ------------------------------------------------------------------ company admin
def cmd_routines(args):
    ws = Path(args.workspace)
    store, company = open_company(ws, name=args.company_name)
    due = due_routines(store, company["id"])
    for r in store.all("SELECT * FROM routines WHERE company_id=?", company["id"]):
        out(f"{r['key']:<26} {r['schedule']:<20} last run: {r['last_run'] or 'never'}  "
            f"{'DUE' if r['id'] in {d['id'] for d in due} else ''}")
    if args.run_due:
        for r in due:
            engine = Engine(store, company, playbook_for(args), ws, approver=make_approver(args.approver), log=out,
                            gate_options=gate_options(args), runtime=runtime_of(args))
            engine.start_project(r["spec"]["request"], title=r["spec"].get("title"))
            mark_run(store, r["id"])
            store.log(company["id"], "routine", "routine.fired", f"routine:{r['key']}")
            engine.run()
    return 0


def cmd_export(args):
    store, company = open_company(Path(args.workspace), name=args.company_name)
    data = export_company(store, company, [agentic_dir(Path(args.workspace)) / "skills"])
    Path(args.out).write_text(json.dumps(data, indent=2), encoding="utf-8")
    out(f"Exported {company['name']} to {args.out} (no secrets; review before sharing)")
    return 0


def cmd_import(args):
    ws = Path(args.workspace)
    spec = import_company(Path(args.file), agentic_dir(ws) / "skills")
    company = create_company(Store(db_path(ws)), spec)
    out(f"Imported {company['name']} into {db_path(ws)}")
    return 0


# ------------------------------------------------------------------ argument parsing
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("-w", "--workspace", default=".", help="Repository / project folder")
        p.add_argument("--company-name", help="Company to use if the store has several")
        p.add_argument("--store", help="Shared company database (default <workspace>/.agentic-sdlc/company.db)")
        p.add_argument("--json", action="store_true", help="Machine-readable output")
        return p

    def delivery(p):
        p.add_argument("--company", help="Company spec JSON (default: .agentic-sdlc/company.json or built-in)")
        p.add_argument("--approver", default="cli",
                       help="cli | stdin | deferred | auto:<name> | hires:<name> (auto-approve hiring only)")
        p.add_argument("--max-fix-iterations", type=int, default=3)
        p.add_argument("--target-org", help="Platform org/environment alias for stacks that validate against one "
                                            "(e.g. Apex: sf alias; also $SF_TARGET_ORG)")
        p.add_argument("--min-coverage", type=int, default=75)
        p.add_argument("--events", action="store_true", help="Stream @@EVENT lines (IDE integrations)")
        p.add_argument("--runtime", choices=RUNTIMES, help="claude-subscription: your Claude plan via Claude Code; "
                                                           "api: per-token API key (default $AGENTIC_RUNTIME or api)")
        p.add_argument("--title")
        p.add_argument("--budget", type=float, help="Project budget in USD (default from company spec)")
        p.add_argument("--clean", action="store_true", help="Delete the workspace first")
        p.add_argument("--playbook", help="spec-driven (default) or classic; resume keeps the project's playbook")
        return p

    p = delivery(common(sub.add_parser("run", help="Build from a requirements document (or inline request)")))
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--requirements", "--request-file", dest="requirements",
                   help="Requirements document: .md, .txt, .docx or .pdf")
    g.add_argument("--request", help="Inline request text")
    p.add_argument("--replay", help=argparse.SUPPRESS)          # internal: recorded agent answers (tests)
    p.set_defaults(fn=cmd_run)

    p = delivery(common(sub.add_parser("resume", help="Continue projects after approvals")))
    p.add_argument("--project", help="Only this project id")
    p.add_argument("--replay", help=argparse.SUPPRESS)
    p.set_defaults(fn=cmd_resume)

    p = delivery(common(sub.add_parser("init", help="Create the company and request hiring approvals")))
    p.set_defaults(fn=cmd_init, approver="deferred")

    common(sub.add_parser("approvals", help="List pending approvals")).set_defaults(fn=cmd_approvals)
    p = common(sub.add_parser("approve", help="Approve or reject (id or 'all')"))
    p.add_argument("id")
    p.add_argument("--as", dest="as_name", required=True)
    p.add_argument("--reject", action="store_true")
    p.add_argument("--comment")
    p.add_argument("--type", help="With 'all': only this approval type (e.g. hire)")
    p.set_defaults(fn=cmd_approve)

    common(sub.add_parser("stacks", help="Detected stacks and verification plan")).set_defaults(fn=cmd_stacks)
    common(sub.add_parser("state", help="Full company state as JSON")).set_defaults(fn=cmd_state)
    common(sub.add_parser("org", help="Org chart")).set_defaults(fn=cmd_org)
    common(sub.add_parser("board", help="Projects and tasks")).set_defaults(fn=cmd_board)
    common(sub.add_parser("budget", help="Spend vs budgets")).set_defaults(fn=cmd_budget)
    p = common(sub.add_parser("activity", help="Audit trail"))
    p.add_argument("-n", type=int, default=40)
    p.set_defaults(fn=cmd_activity)
    p = common(sub.add_parser("dashboard", help="Write the company dashboard HTML"))
    p.add_argument("--out")
    p.set_defaults(fn=cmd_dashboard)
    p = common(sub.add_parser("replay-page", help="Write a step-through replay page for a project trace"))
    p.add_argument("trace")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_replay_page)
    p = common(sub.add_parser("playbook", help="Show the delivery playbook"))
    p.add_argument("--playbook", help="spec-driven (default) or classic")
    p.set_defaults(fn=cmd_playbook)
    p = common(sub.add_parser("graph", help="Codebase knowledge graph (Graphify): build, status, query, affected, summary"))
    p.add_argument("action", choices=["build", "status", "query", "affected", "summary"])
    p.add_argument("text", nargs="*", help="Question (query) or changed files (affected)")
    p.set_defaults(fn=cmd_graph)
    p = common(sub.add_parser("doctor", help="Check runtime (subscription / API key) and stack tools"))
    p.add_argument("--runtime", choices=RUNTIMES)
    p.add_argument("--probe", action="store_true", help="Send one tiny prompt to confirm the Claude sign-in")
    p.set_defaults(fn=cmd_doctor)

    p = delivery(common(sub.add_parser("routines", help="List routines; --run-due starts due ones")))
    p.add_argument("--run-due", action="store_true")
    p.set_defaults(fn=cmd_routines)
    p = common(sub.add_parser("export", help="Export the company (no secrets)"))
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_export)
    p = common(sub.add_parser("import", help="Import a company export"))
    p.add_argument("file")
    p.set_defaults(fn=cmd_import)

    args = ap.parse_args(argv)
    global STORE_OVERRIDE
    STORE_OVERRIDE = args.store
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
