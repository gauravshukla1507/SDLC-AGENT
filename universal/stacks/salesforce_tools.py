"""Real tools the orchestrator runs between agent steps.

1. scaffold()          - SFDX project skeleton, only for files that don't exist yet (safe in existing projects)
2. project_context()   - summary of the existing project (classes, triggers, custom fields) for the agents
3. metadata_check()    - structural checks on the SFDX source written in this run
4. pmd_check()         - PMD Apex rules (the engine behind Salesforce Code Analyzer) on this run's files
5. org_validate()      - `sf project deploy validate` of this run's components + Apex tests + coverage
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import xml.dom.minidom
from pathlib import Path

API_VERSION = "62.0"

SRC = "force-app/main/default"
RULESET = Path(__file__).resolve().parent / "pmd-ruleset.xml"  # noqa: E501


def _rel(ws: Path, p: Path) -> str:
    return p.relative_to(ws).as_posix()


# ---------------------------------------------------------------- scaffold
def scaffold(ws: Path, name: str = "agentic-sfdx") -> list[str]:
    """Create a minimal SFDX project skeleton. Never overwrites existing files."""
    files = {
        "sfdx-project.json": json.dumps({
            "packageDirectories": [{"path": "force-app", "default": True}],
            "name": name, "namespace": "", "sfdcLoginUrl": "https://login.salesforce.com",
            "sourceApiVersion": API_VERSION}, indent=2) + "\n",
        "config/project-scratch-def.json": json.dumps({
            "orgName": "Agentic SDLC scratch org", "edition": "Developer",
            "features": ["EnableSetPasswordInApi"]}, indent=2) + "\n",
        ".forceignore": "**/jsconfig.json\n**/.eslintrc.json\n**/__tests__/**\n",
    }
    created = []
    for rel, content in files.items():
        p = ws / rel
        if p.exists():
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        created.append(rel)
    return created


# ---------------------------------------------------------------- project context
def project_context(ws: Path, limit: int = 400) -> str:
    """Names of what already exists, so agents reuse it instead of colliding with it."""
    root = ws / SRC
    if not root.exists():
        return "Existing project: empty (new SFDX project)."
    classes = sorted(p.stem for p in root.rglob("*.cls"))
    triggers = []
    for t in sorted(root.rglob("*.trigger")):
        m = re.search(r"trigger\s+\w+\s+on\s+(\w+)", t.read_text(encoding="utf-8", errors="ignore"), re.I)
        triggers.append(f"{t.stem} (on {m.group(1) if m else '?'})")
    fields = sorted(f"{p.parent.parent.name}.{p.name.replace('.field-meta.xml', '')}"
                    for p in root.rglob("*.field-meta.xml"))
    permsets = sorted(p.name.replace(".permissionset-meta.xml", "") for p in root.rglob("*.permissionset-meta.xml"))
    handler_hint = [c for c in classes if re.search(r"TriggerHandler|Handler$|Dispatcher", c)]

    def fmt(title, items):
        shown = items[:limit]
        more = f" … (+{len(items) - limit} more)" if len(items) > limit else ""
        return f"{title} ({len(items)}): " + (", ".join(shown) if shown else "none") + more

    lines = ["Existing project (reuse existing components and conventions; never create a second trigger on an object "
             "that already has one: extend its handler instead):",
             fmt("Apex classes", classes), fmt("Triggers", triggers), fmt("Custom fields", fields),
             fmt("Permission sets", permsets)]
    if handler_hint:
        lines.append("Likely trigger framework classes: " + ", ".join(handler_hint))
    return "\n".join(lines)


# ---------------------------------------------------------------- metadata
def _is_test_class(text: str) -> bool:
    return bool(re.search(r"@IsTest", text, re.I))


def metadata_check(ws: Path, only: set[str] | None = None) -> list[dict]:
    """Return {file, rule, message} issues. With `only`, report issues for those files only."""
    issues: list[dict] = []
    root = ws / SRC
    if not root.exists():
        return [{"file": SRC, "rule": "SfdxStructure", "message": "force-app/main/default not found"}]
    scope = (lambda rel: rel in only) if only is not None else (lambda rel: True)

    for xml_file in root.rglob("*-meta.xml"):
        rel = _rel(ws, xml_file)
        if not scope(rel):
            continue
        try:
            xml.dom.minidom.parse(str(xml_file))
        except Exception as exc:  # noqa: BLE001
            issues.append({"file": rel, "rule": "XmlWellFormed", "message": str(exc)})

    code_files = list(root.rglob("*.cls")) + list(root.rglob("*.trigger"))
    for code in code_files:
        rel = _rel(ws, code)
        if not scope(rel):
            continue
        meta = code.with_name(code.name + "-meta.xml")
        if not meta.exists():
            issues.append({"file": rel, "rule": "MetaXmlPresent", "message": f"missing {meta.name}"})
            continue
        m = re.search(r"<apiVersion>([\d.]+)</apiVersion>", meta.read_text(encoding="utf-8"))
        if not m or float(m.group(1)) < 58.0:
            issues.append({"file": rel, "rule": "ApiVersion", "message": "apiVersion missing or below 58.0"})

    # One trigger per object (reported against the file written in this run)
    by_object: dict[str, list[str]] = {}
    for trg in root.rglob("*.trigger"):
        m = re.search(r"trigger\s+\w+\s+on\s+(\w+)", trg.read_text(encoding="utf-8", errors="ignore"), re.I)
        if m:
            by_object.setdefault(m.group(1).lower(), []).append(_rel(ws, trg))
    for obj, paths in by_object.items():
        if len(paths) > 1:
            for rel in paths:
                if scope(rel):
                    others = ", ".join(Path(p).name for p in paths if p != rel)
                    issues.append({"file": rel, "rule": "OneTriggerPerObject",
                                   "message": f"another trigger on {obj} already exists ({others})"})

    # Custom fields used in Apex must exist as metadata in the project
    defined = {f.name.replace(".field-meta.xml", "").lower() for f in root.rglob("*.field-meta.xml")}
    for code in code_files:
        rel = _rel(ws, code)
        if not scope(rel):
            continue
        for fld in sorted(set(re.findall(r"\b(\w+__c)\b", code.read_text(encoding="utf-8", errors="ignore")))):
            if fld.lower() not in defined:
                issues.append({"file": rel, "rule": "FieldDefined",
                               "message": f"{fld} used in Apex but no field metadata in the project"})

    # The run must include at least one test class
    run_code = [c for c in code_files if scope(_rel(ws, c))]
    if run_code and not any(c.suffix == ".cls" and _is_test_class(c.read_text(encoding="utf-8")) for c in run_code):
        issues.append({"file": SRC + "/classes", "rule": "TestClassPresent", "message": "no @IsTest class in this change"})
    return issues


# ---------------------------------------------------------------- PMD
def _pmd_binary() -> str | None:
    if os.environ.get("PMD_BIN"):
        return os.environ["PMD_BIN"]
    for cand in ("pmd", "/opt/pmd-bin-7.8.0/bin/pmd"):
        if shutil.which(cand):
            return shutil.which(cand)
    return None


def pmd_check(ws: Path, only: set[str] | None = None) -> dict:
    """Run PMD Apex rules on this run's Apex files (or all of force-app when `only` is None)."""
    pmd = _pmd_binary()
    if not pmd:
        return {"available": False, "violations": [],
                "note": "PMD not found. Install PMD 7 (or set PMD_BIN) to enable the Apex static-analysis gate."}
    targets = [ws / p for p in sorted(only)] if only is not None else [ws / SRC]
    targets = [t for t in targets if t.exists() and (t.is_dir() or t.suffix in (".cls", ".trigger"))]
    if not targets:
        return {"available": True, "violations": []}
    with tempfile.TemporaryDirectory() as tmp:
        report, file_list = Path(tmp) / "report.json", Path(tmp) / "files.txt"
        file_list.write_text("\n".join(str(t) for t in targets), encoding="utf-8")
        proc = subprocess.run([pmd, "check", "--file-list", str(file_list), "-R", str(RULESET), "-f", "json",
                               "-r", str(report), "--no-cache", "--no-progress"],
                              capture_output=True, text=True)
        try:
            data = json.loads(report.read_text(encoding="utf-8") or "{}")
        except (json.JSONDecodeError, FileNotFoundError):
            return {"available": True, "violations": [], "error": proc.stderr[-2000:]}
    violations = []
    for f in data.get("files", []):
        rel = Path(os.path.relpath(f["filename"], ws)).as_posix()
        for v in f.get("violations", []):
            violations.append({"file": rel, "line": v["beginline"], "end_line": v.get("endline"),
                               "rule": v["rule"], "message": v["description"], "priority": v["priority"]})
    return {"available": True, "violations": violations}


# ---------------------------------------------------------------- org validation
def _deployable(paths: list[str]) -> list[str]:
    """Source paths for sf: code files (meta.xml is picked up automatically) and standalone metadata files."""
    out = []
    for p in sorted(paths):
        if not p.startswith("force-app/"):
            continue
        if p.endswith((".cls-meta.xml", ".trigger-meta.xml")):
            continue
        out.append(p)
    return out


def org_validate(ws: Path, target_org: str | None, test_classes: list[str], min_coverage: int = 75,
                 run_files: list[str] | None = None) -> dict:
    """Validate-deploy this run's components to a real org and run the given Apex tests."""
    if not target_org:
        return {"skipped": True, "passed": None,
                "summary": "Skipped: no Salesforce org connected (set SF_TARGET_ORG to enable)",
                "output": "Set SF_TARGET_ORG to a sandbox or scratch-org alias authenticated with the sf CLI.\n"
                          "The gate then runs: sf project deploy validate --test-level RunSpecifiedTests "
                          f"--tests {' '.join(test_classes)} and enforces >= {min_coverage}% coverage."}
    if not shutil.which("sf"):
        return {"skipped": True, "passed": None, "summary": "Skipped: sf CLI not installed",
                "output": "npm install -g @salesforce/cli"}
    cmd = ["sf", "project", "deploy", "validate", "--target-org", target_org,
           "--test-level", "RunSpecifiedTests", "--wait", "30", "--json"]
    sources = _deployable(run_files) if run_files else ["force-app"]
    for s in sources:
        cmd += ["--source-dir", s]
    for t in test_classes:
        cmd += ["--tests", t]
    proc = subprocess.run(cmd, cwd=ws, capture_output=True, text=True)
    try:
        res = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"skipped": False, "passed": False, "summary": "sf CLI error", "output": proc.stdout + proc.stderr}
    result = res.get("result", {}) or {}
    details = result.get("details", {}) or {}
    tests = details.get("runTestResult", {}) or {}
    as_list = lambda v: v if isinstance(v, list) else ([v] if v else [])  # noqa: E731
    failures, comp_fail, cov = (as_list(tests.get("failures")), as_list(details.get("componentFailures")),
                                as_list(tests.get("codeCoverage")))
    total = sum(int(c.get("numLocations", 0)) for c in cov)
    uncovered = sum(int(c.get("numLocationsNotCovered", 0)) for c in cov)
    coverage = round(100 * (total - uncovered) / total, 1) if total else 0.0
    ok = res.get("status") == 0 and result.get("success", False) and not failures and coverage >= min_coverage
    lines = [f"Deploy validation: {'success' if result.get('success') else 'failed'}",
             f"Tests run: {tests.get('numTestsRun', 0)}, failures: {len(failures)}, coverage: {coverage}% (min {min_coverage}%)"]
    lines += [f"COMPONENT {c.get('fullName')}: {c.get('problem')}" for c in comp_fail]
    lines += [f"FAILED {f.get('name')}.{f.get('methodName')}: {f.get('message')}" for f in failures]
    if res.get("status") not in (0, None) and not (failures or comp_fail):
        lines.append(str(res.get("message", ""))[:2000])
    return {"skipped": False, "passed": bool(ok), "coverage": coverage,
            "summary": f"{tests.get('numTestsRun', 0)} Apex tests, {len(failures)} failed, {coverage}% coverage",
            "output": "\n".join(lines)}


# ---------------------------------------------------------------- check runners (used by stacks.detect)
def _sf_files(run_files: set[str]) -> set[str]:
    return {f for f in run_files if f.startswith("force-app/")}


def metadata_runner(ws: Path, run_files: set[str], **_) -> dict:
    scaffold(ws)  # sfdx-project.json etc. if missing (never overwrites)
    issues = metadata_check(ws, only=_sf_files(run_files) or None)
    lines = [f"Metadata checks: {len(issues)} issue(s)"] + [f"{i['file']}:-  {i['rule']}  {i['message']}" for i in issues]
    return {"passed": not issues, "summary": "clean" if not issues else f"{len(issues)} issue(s)",
            "output": "\n".join(lines),
            "violations": [{"file": i["file"], "line": None, "rule": i["rule"], "message": i["message"],
                            "engine": "metadata"} for i in issues]}


def pmd_runner(ws: Path, run_files: set[str], **_) -> dict:
    res = pmd_check(ws, only=_sf_files(run_files) or None)
    if not res["available"]:
        return {"skipped": True, "passed": None, "summary": "skipped: PMD not installed", "output": res["note"],
                "violations": []}
    v = res["violations"]
    n_rules = sum("<rule ref" in line for line in RULESET.read_text().splitlines())
    lines = [f"PMD Apex rules ({n_rules} rules): {len(v)} violation(s)"]
    lines += [f"{x['file']}:{x['line']}  {x['rule']}  {x['message']}" for x in v]
    return {"passed": not v, "summary": "clean" if not v else f"{len(v)} violation(s) in "
            f"{len({x['file'] for x in v})} file(s)", "output": "\n".join(lines),
            "violations": [{**x, "engine": "pmd"} for x in v]}


def org_runner(ws: Path, run_files: set[str], target_org: str | None = None, min_coverage: int = 75, **_) -> dict:
    tests = sorted(Path(f).stem for f in run_files if f.endswith(".cls")
                   and re.search(r"@IsTest", (ws / f).read_text(encoding="utf-8", errors="ignore"), re.I))
    res = org_validate(ws, target_org, tests, min_coverage, run_files=sorted(_sf_files(run_files)))
    return {**res, "violations": []}
