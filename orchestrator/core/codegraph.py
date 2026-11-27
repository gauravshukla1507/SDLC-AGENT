"""Codebase knowledge graph for existing products, built with Graphify (https://github.com/Graphify-Labs/graphify).

Agents working on an existing product need to know how it is put together before they change it. Graphify
parses the repository with tree-sitter (about 40 languages) into a queryable graph of files, classes, functions and
their relations (calls, imports, inherits, …). The orchestrator uses it in three ways:

  map       build or refresh `graphify-out/` at the start of a project (`graphify update <repo>`, offline: code
            only, no LLM, no API key, nothing leaves the machine)
  context   per task, a graph query scoped to the task, plus the architecture summary (core abstractions,
            communities), so agents work with the real structure instead of a file list
  impact    for review, the "blast radius": everything that depends on the existing code the change touched

Graphify is optional. If the `graphify` CLI is missing or the repository is new (greenfield), every function
returns a clear "not available" result and delivery continues without it. Install: `uv tool install graphifyy`
(or `pip install graphifyy`).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

OUT_DIR = "graphify-out"


def cli() -> str | None:
    return os.environ.get("GRAPHIFY_BIN") or shutil.which("graphify")


def enabled() -> bool:
    return os.environ.get("AGENTIC_GRAPHIFY", "auto").lower() not in ("off", "0", "false", "no")


def graph_file(ws: Path) -> Path:
    return Path(ws) / OUT_DIR / "graph.json"


def _env() -> dict:
    # code-only, fully local: never send repository content to an LLM backend from graph building
    return {**os.environ, "GRAPHIFY_BACKEND": "none"}


def _run(args: list[str], ws: Path, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run([cli(), *args], cwd=ws, capture_output=True, text=True, timeout=timeout, env=_env())


def status(ws: Path) -> dict:
    ws = Path(ws)
    g = graph_file(ws)
    info = {"enabled": enabled(), "installed": bool(cli()), "built": g.exists(), "path": str(g.parent)}
    if g.exists():
        try:
            data = json.loads(g.read_text(encoding="utf-8"))
            info.update(nodes=len(data.get("nodes", [])), edges=len(data.get("links", data.get("edges", []))),
                        html=str(g.parent / "graph.html") if (g.parent / "graph.html").exists() else None)
        except (json.JSONDecodeError, OSError):
            info["built"] = False
    return info


def build(ws: Path, has_code: bool) -> dict:
    """Build or incrementally refresh the graph. Returns a gate-style result (passed / skipped)."""
    ws = Path(ws)
    if not enabled():
        return {"skipped": True, "passed": None, "summary": "Skipped: codebase graph disabled (AGENTIC_GRAPHIFY=off)",
                "output": ""}
    if not has_code:
        return {"skipped": True, "passed": None, "summary": "Skipped: new application (nothing to map yet)",
                "output": "The codebase graph is built for existing products."}
    if not cli():
        return {"skipped": True, "passed": None,
                "summary": "Skipped: Graphify not installed (uv tool install graphifyy)",
                "output": "Install Graphify to give the agents a knowledge graph of the existing code: "
                          "`uv tool install graphifyy` or `pip install graphifyy`."}
    try:
        proc = _run(["update", str(ws)], ws, timeout=1800)
    except subprocess.TimeoutExpired:
        return {"skipped": True, "passed": None, "summary": "Skipped: graph build timed out", "output": ""}
    st = status(ws)
    if not st.get("built"):
        return {"skipped": True, "passed": None, "summary": "Skipped: Graphify could not build the graph",
                "output": (proc.stdout + proc.stderr)[-3000:]}
    _ensure_ignored(ws)
    return {"passed": True, "summary": f"Codebase graph ready: {st['nodes']} nodes, {st['edges']} edges",
            "output": (proc.stdout + proc.stderr)[-2000:], "graph": st}


def _ensure_ignored(ws: Path):
    """graphify-out/ is a cache the orchestrator rebuilds; keep it out of the change set unless the team commits it."""
    ignore = ws / ".gitignore"
    try:
        text = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
        if OUT_DIR not in text and (ws / ".git").exists():
            ignore.write_text(text + ("" if text.endswith("\n") or not text else "\n") + f"{OUT_DIR}/\n", encoding="utf-8")
    except OSError:
        pass


def architecture_summary(ws: Path, max_chars: int = 3500) -> str:
    """The parts of GRAPH_REPORT.md that orient an agent: size, core abstractions, communities, cycles."""
    report = Path(ws) / OUT_DIR / "GRAPH_REPORT.md"
    if not report.exists():
        return ""
    text = report.read_text(encoding="utf-8", errors="ignore")
    keep, current = [], None
    wanted = ("Summary", "God Nodes", "Surprising Connections", "Import Cycles", "Communities")
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
        if current and current.startswith(wanted):
            keep.append(line)
    out = "\n".join(keep).strip()
    return out[:max_chars] + ("\n…" if len(out) > max_chars else "")


def query(ws: Path, question: str, budget: int = 1500) -> str:
    """Graph traversal for a natural-language question; returns Graphify's compact node/edge listing."""
    if not (cli() and graph_file(ws).exists() and question.strip()):
        return ""
    try:
        proc = _run(["query", question[:400], "--budget", str(budget)], Path(ws), timeout=120)
    except subprocess.TimeoutExpired:
        return ""
    out = proc.stdout.strip()
    return "" if proc.returncode not in (0,) or "No matching nodes" in out else out


def _nodes_by_file(ws: Path) -> dict[str, list[dict]]:
    try:
        data = json.loads(graph_file(ws).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    degree: dict[str, int] = {}
    for e in data.get("links", data.get("edges", [])):
        for k in ("source", "target"):
            degree[e[k]] = degree.get(e[k], 0) + 1
    by_file: dict[str, list[dict]] = {}
    for n in data.get("nodes", []):
        src = (n.get("source_file") or "").replace("\\", "/")
        if src and n.get("file_type") == "code" and n.get("label"):
            by_file.setdefault(src, []).append({**n, "degree": degree.get(n["id"], 0)})
    return by_file


def blast_radius(ws: Path, changed_files: list[str], max_files: int = 8, per_file: int = 3) -> str:
    """For existing code the change touched: what else depends on it (reverse traversal), to focus review."""
    if not (cli() and graph_file(ws).exists()):
        return ""
    by_file = _nodes_by_file(ws)
    sections = []
    for f in [f for f in changed_files if f in by_file][:max_files]:
        top = sorted(by_file[f], key=lambda n: -n["degree"])[:per_file]
        for node in top:
            label = node["label"]
            try:
                proc = _run(["affected", label], Path(ws), timeout=60)
            except subprocess.TimeoutExpired:
                continue
            lines = [l for l in proc.stdout.splitlines() if l.startswith("- ")]
            if lines:
                sections.append(f"`{label}` ({f}) is used by {len(lines)} node(s):\n" + "\n".join(lines[:15]))
    if not sections:
        return ""
    return "Blast radius of the change (from the codebase graph built before the change):\n\n" + "\n\n".join(sections)


def keywords(text: str, limit: int = 12) -> str:
    """Domain terms from a spec/request to steer the graph query."""
    words = re.findall(r"[A-Za-z][A-Za-z0-9_]{3,}", text)
    stop = {"that", "this", "with", "from", "have", "when", "then", "given", "should", "must", "will", "shall",
            "user", "users", "story", "acceptance", "criteria", "system", "each", "into", "only", "able", "their"}
    seen, out = set(), []
    for w in words:
        lw = w.lower()
        if lw not in stop and lw not in seen:
            seen.add(lw)
            out.append(w)
        if len(out) >= limit:
            break
    return " ".join(out)
