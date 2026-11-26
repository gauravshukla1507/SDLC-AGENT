"""Company dashboard: one self-contained HTML page rendered from the store.

Shows the company the way a manager sees it: mission and goals, the org chart with each agent's
status, adapter and budget, the task board per project, approvals, spend and the activity log.
"""
from __future__ import annotations

import html
import json

from .company import org_chart

STATUSES = ["todo", "in_progress", "blocked", "done", "cancelled", "failed"]


def _e(v) -> str:
    return html.escape("" if v is None else str(v))


def _adapter_label(raw) -> str:
    try:
        raw = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        pass
    if isinstance(raw, dict):
        return f"{raw.get('type')} · {raw.get('model') or ''}".strip(" ·")
    return str(raw)


def render_dashboard(store, company: dict) -> str:
    cid = company["id"]
    spec = company.get("spec") or {}
    agents = org_chart(store, cid)
    goals = store.all("SELECT * FROM goals WHERE company_id=? ORDER BY created", cid)
    projects = [g for g in goals if g["level"] == "project"]
    tasks = store.all("SELECT * FROM tasks WHERE company_id=? ORDER BY created", cid)
    approvals = store.all("SELECT * FROM approvals WHERE company_id=? ORDER BY created DESC", cid)
    runs = store.all("SELECT * FROM runs WHERE company_id=? ORDER BY started", cid)
    activity = store.all("SELECT * FROM activity WHERE company_id=? ORDER BY seq DESC LIMIT 120", cid)
    routines = store.all("SELECT * FROM routines WHERE company_id=?", cid)
    spend = sum(r["cost_usd"] or 0 for r in runs)
    limit = spec.get("budgets", {}).get("company_usd")
    estimated = any(r["tokens_estimated"] for r in runs)
    subscription = any("/subscription" in (r["adapter"] or "") for r in runs)
    pending = [a for a in approvals if a["status"] == "pending"]

    def bar(spent, lim):
        if not lim:
            return ""
        pct = min(100, spent / lim * 100)
        col = "var(--red)" if pct >= 100 else "var(--yellow)" if pct >= 80 else "var(--green)"
        return f'<div class="bar"><div style="width:{pct:.1f}%;background:{col}"></div></div>'

    # org chart
    org = []
    for a in agents:
        human = a["adapter"] == "human"
        st = a["status"]
        cls = {"active": "ok", "paused": "warn", "pending_approval": "info", "rejected": "bad"}.get(st, "")
        org.append(f'''<div class="seat" style="margin-left:{a['depth'] * 28}px">
          <div class="av {'human' if human else ''}">{_e(''.join(w[0] for w in a['name'].split()[:2]))}</div>
          <div class="who"><b>{_e(a['name'])}</b><span>{_e(a['title'])}</span>
            <span class="mono">{'human' if human else _e(_adapter_label(a['adapter']))}
            {(' · skills: ' + _e(', '.join(a['skills']))) if a['skills'] else ''}</span></div>
          <div class="right"><span class="pill {cls}">{_e(st.replace('_', ' '))}</span>
            {f"<span class='mono small'>${a['spent_usd']:.4f} / ${a['budget_usd']:.2f}</span>{bar(a['spent_usd'], a['budget_usd'])}" if a['budget_usd'] else ''}
          </div></div>''')

    # goals tree
    def goal_tree(parent, depth=0):
        rows = []
        for g in [x for x in goals if x["parent_id"] == parent]:
            extra = ""
            if g["level"] == "project":
                ts = [t for t in tasks if t["goal_id"] == g["id"]]
                det = store.one("SELECT details FROM activity WHERE company_id=? AND action='stack.detected' AND entity=? "
                                "ORDER BY seq DESC LIMIT 1", cid, f"goal:{g['id']}")
                stk = (det or {}).get("details", {}).get("stacks", []) if det else []
                extra += "".join(f' <span class="pill info">{_e(x)}</span>' for x in stk)
                done = sum(t["status"] == "done" for t in ts)
                extra = f'<span class="small mono">{done}/{len(ts)} tasks · ${g["spent_usd"]:.4f}' + \
                        (f' of ${g["budget_usd"]:.2f}' if g["budget_usd"] else "") + "</span>" + extra
            rows.append(f'<div class="goal" style="margin-left:{depth * 22}px"><span class="lvl">{_e(g["level"])}</span>'
                        f'{_e(g["title"])} {extra}</div>')
            rows += goal_tree(g["id"], depth + 1)
        return rows

    # task boards: status strip + cards in creation order (works for live and finished projects)
    boards = []
    pill = {"done": "ok", "in_progress": "info", "blocked": "warn", "cancelled": "bad", "failed": "bad", "todo": ""}
    for p in projects:
        ts = [t for t in tasks if t["goal_id"] == p["id"]]
        strip = " ".join(f'<span class="pill {pill[s]}">{_e(s.replace("_", " "))}: {sum(t["status"] == s for t in ts)}</span>'
                         for s in STATUSES if any(t["status"] == s for t in ts))
        cards = "".join(
            f'''<div class="card {t['kind']}"><div class="cm"><span class="pill {pill.get(t['status'], '')}">{_e(t['status'].replace('_', ' '))}</span>
            <span>{_e(t['kind'])}{f" · try {t['attempts']}" if t['attempts'] else ''}</span></div>
            <div class="ct">{_e(t['title'])}</div><div class="cm"><span>{_e(t['assignee'])}</span></div>
            {f"<div class='cr'>{_e(((t['result'] or {}).get('summary') or '')[:170])}</div>" if t['result'] and (t['result'] or {}).get('summary') else ''}
            {f"<div class='cr warn'>{_e((t['spec'] or {}).get('blocked_reason'))}</div>" if t['status'] == 'blocked' else ''}
            </div>''' for t in ts)
        det = store.one("SELECT details FROM activity WHERE company_id=? AND action='stack.detected' AND entity=? "
                        "ORDER BY seq DESC LIMIT 1", cid, f"goal:{p['id']}")
        stk = ", ".join((det or {}).get("details", {}).get("stacks", [])) if det else ""
        boards.append(f'<section class="panel"><div class="ph"><h2>Project tasks: {_e(p["title"])}'
                      f'{f" <span class=small>· stack detected: {_e(stk)}</span>" if stk else ""}</h2>'
                      f'<span>{strip}</span></div><div class="board">{cards}</div></section>')

    apr_rows = "".join(
        f'''<tr><td><span class="pill {'warn' if a['status'] == 'pending' else 'ok' if a['status'] == 'approved' else 'bad'}">{_e(a['status'])}</span></td>
        <td>{_e(a['type'])}</td><td>{_e(a['subject'])}</td><td>{_e(a['decided_by'] or '')}</td>
        <td>{_e(a['comment'] or '')}</td><td class="mono">{_e((a['decided'] or a['created'])[11:19])}</td></tr>''' for a in approvals)
    act_rows = "".join(
        f'<tr><td class="mono">{_e(r["ts"][11:19])}</td><td>{_e(r["actor"])}</td><td class="mono">{_e(r["action"])}</td>'
        f'<td>{_e(r["entity"])}</td></tr>' for r in activity)
    by_agent: dict = {}
    for r in runs:
        b = by_agent.setdefault(r["agent_key"], {"runs": 0, "tin": 0, "tout": 0, "cost": 0.0})
        b["runs"] += 1
        b["tin"] += r["input_tokens"] or 0
        b["tout"] += r["output_tokens"] or 0
        b["cost"] += r["cost_usd"] or 0
    cost_rows = "".join(f'<tr><td>{_e(k)}</td><td>{v["runs"]}</td><td class="mono">{v["tin"]:,}</td>'
                        f'<td class="mono">{v["tout"]:,}</td><td class="mono">${v["cost"]:.4f}</td></tr>'
                        for k, v in by_agent.items())
    routine_rows = "".join(f'<tr><td>{_e(r["key"])}</td><td class="mono">{_e(r["schedule"])}</td>'
                           f'<td>{_e((r["spec"] or {}).get("title", ""))}</td><td class="mono">{_e(r["last_run"] or "never")}</td></tr>'
                           for r in routines)
    gw = spec.get("governance", {}).get("gateway", {})
    gw_rows = "".join(f'<tr><td class="mono">{_e(k)}</td><td><span class="pill {"ok" if v == "allowed" else "warn" if v == "ask_first" else "bad"}">{_e(v)}</span></td></tr>'
                      for k, v in gw.items())

    kpis = [
        (sum(a["status"] == "active" and a["adapter"] != "human" for a in agents), "AI agents active"),
        (len(projects), "Projects"),
        (f"{sum(t['status'] == 'done' for t in tasks)}/{len(tasks)}", "Tasks done"),
        (len(pending), "Pending approvals"),
        (f"${spend:.4f}" + (f" / ${limit:.0f}" if limit else ""),
         "Claude plan usage (API-equivalent)" if subscription else "Spend" + (" (est. tokens)" if estimated else "")),
    ]
    kpi_html = "".join(f'<div class="kpi"><div class="v">{_e(v)}</div><div class="l">{_e(l)}</div></div>' for v, l in kpis)

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(company['name'])} – Universal Orchestrator</title>
<style>
:root{{--navy:#0F1B2D;--navy2:#16263D;--navy3:#1E3150;--line:#2A3D5A;--paper:#F4F1EA;--muted:#A9B4C4;--orange:#F2864F;
--green:#4CC38A;--red:#F0616D;--yellow:#E8C35A;--blue:#6AA7FF}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--navy);color:var(--paper);font:14px/1.5 Inter,system-ui,sans-serif}}
.wrap{{max-width:1500px;margin:0 auto;padding:24px}}h1,h2,h4{{margin:0;font-family:"Space Grotesk",Inter,sans-serif}}
.kicker{{color:var(--orange);font-size:12px;font-weight:600;letter-spacing:.14em;text-transform:uppercase}}
h1{{font-size:28px;margin:4px 0}}.sub{{color:var(--muted);max-width:900px}}
.mono{{font-family:ui-monospace,Menlo,monospace}}.small{{font-size:12px;color:var(--muted)}}
.kpis{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin:18px 0}}
.kpi{{background:var(--navy2);border:1px solid var(--line);border-radius:12px;padding:12px 14px}}.kpi .v{{font-size:24px;font-weight:700}}.kpi .l{{color:var(--muted);font-size:12px}}
.grid{{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,1fr);gap:16px;align-items:start}}
.panel{{background:var(--navy2);border:1px solid var(--line);border-radius:14px;margin-bottom:16px;overflow:hidden}}
.ph{{display:flex;justify-content:space-between;align-items:center;padding:12px 16px;border-bottom:1px solid var(--line)}}.ph h2{{font-size:15px}}
.pb{{padding:12px 16px}}
.seat{{display:flex;gap:12px;align-items:center;padding:8px 10px;border:1px solid var(--line);border-radius:10px;background:var(--navy);margin-bottom:8px}}
.av{{width:34px;height:34px;border-radius:9px;background:var(--navy3);border:1px solid var(--line);display:flex;align-items:center;justify-content:center;font-weight:700;font-size:12px;flex:none}}
.av.human{{background:var(--orange);color:var(--navy);border-color:var(--orange)}}
.who{{display:flex;flex-direction:column;min-width:0}}.who span{{color:var(--muted);font-size:12px}}
.right{{margin-left:auto;display:flex;flex-direction:column;align-items:flex-end;gap:4px;min-width:150px}}
.bar{{width:150px;height:5px;background:var(--navy3);border-radius:4px;overflow:hidden}}.bar>div{{height:100%}}
.pill{{font-size:11px;font-weight:600;border-radius:999px;padding:2px 9px;border:1px solid var(--line);color:var(--muted);white-space:nowrap}}
.pill.ok{{color:var(--green);border-color:var(--green)}}.pill.warn{{color:var(--yellow);border-color:var(--yellow)}}
.pill.bad{{color:var(--red);border-color:var(--red)}}.pill.info{{color:var(--blue);border-color:var(--blue)}}
.goal{{padding:6px 0;border-bottom:1px solid #22344F}}.lvl{{display:inline-block;min-width:66px;font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--orange)}}
.board{{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:10px;padding:12px}}
.col h4{{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-bottom:8px}}.col h4 span{{color:var(--paper)}}
.card{{background:var(--navy);border:1px solid var(--line);border-left:3px solid var(--blue);border-radius:8px;padding:8px 10px}}
.card.gate{{border-left-color:var(--yellow)}}.card.approval{{border-left-color:var(--orange)}}
.ct{{font-weight:600;font-size:13px;margin-top:6px}}.cm{{display:flex;justify-content:space-between;color:var(--muted);font-size:11px;margin-top:2px}}
.cr{{color:#C9D2DE;font-size:12px;margin-top:6px}}.cr.warn{{color:var(--yellow)}}
table{{width:100%;border-collapse:collapse;font-size:13px}}th{{text-align:left;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.08em;padding:6px 10px;border-bottom:1px solid var(--line)}}
td{{padding:6px 10px;border-bottom:1px solid #22344F;vertical-align:top}}.scroll{{max-height:420px;overflow:auto}}
@media(max-width:1000px){{.grid{{grid-template-columns:1fr}}.kpis{{grid-template-columns:repeat(2,1fr)}}}}
</style></head><body><div class="wrap">
<div class="kicker">Universal Orchestrator · company dashboard</div>
<h1>{_e(company['name'])}</h1><div class="sub">Mission: {_e(company['mission'])}</div>
<div class="kpis">{kpi_html}</div>
<div class="grid"><div>
<section class="panel"><div class="ph"><h2>Org chart</h2><span class="small">agents are hired through approval; humans stay accountable</span></div><div class="pb">{''.join(org)}</div></section>
<section class="panel"><div class="ph"><h2>Approvals</h2><span class="small">{len(pending)} pending</span></div><div class="scroll"><table><tr><th>Status</th><th>Type</th><th>Subject</th><th>By</th><th>Comment</th><th>Time</th></tr>{apr_rows}</table></div></section>
</div><div>
<section class="panel"><div class="ph"><h2>Goals → projects</h2><span class="small">agents receive this ancestry with every task</span></div><div class="pb">{''.join(goal_tree(None))}</div></section>
<section class="panel"><div class="ph"><h2>Spend by agent</h2><span class="small">{'token counts estimated (recorded run)' if estimated else 'from API usage'}</span></div><table><tr><th>Agent</th><th>Runs</th><th>In tokens</th><th>Out tokens</th><th>Cost</th></tr>{cost_rows}</table></section>
<section class="panel"><div class="ph"><h2>Tool gateway</h2><span class="small">allowed · ask_first · off</span></div><table>{gw_rows or '<tr><td class="small">default: allowed</td></tr>'}</table></section>
<section class="panel"><div class="ph"><h2>Routines</h2></div><table><tr><th>Key</th><th>Schedule</th><th>Work</th><th>Last run</th></tr>{routine_rows}</table></section>
</div></div>
{''.join(boards)}
<section class="panel"><div class="ph"><h2>Activity log</h2><span class="small">append-only audit trail (latest first)</span></div><div class="scroll"><table><tr><th>Time</th><th>Actor</th><th>Action</th><th>Entity</th></tr>{act_rows}</table></div></section>
</div></body></html>"""
