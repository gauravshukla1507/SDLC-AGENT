"""Stack knowledge: detection, verification and context for any repository.

The orchestrator does not ask which technology it is working on. It looks at the workspace (after
every build step too, so greenfield projects are detected from what the agents wrote) and assembles:

  profile    detected stacks with their evidence ("pom.xml", "force-app/", "package.json" …)
  checks     ordered verification commands: install → build → static → test (blocking) and
             lint / format / audit (advisory by default). A check whose tool is not installed is
             reported as skipped, never silently passed.
  skills     stacks/<name>.md conventions loaded into every agent's prompt
  context    what already exists, so agents extend the codebase instead of colliding with it

Operators can override or extend the checks with `.agentic-sdlc/verify.json` in the repo:
  {"checks": [{"name": "unit tests", "cmd": "make test", "blocking": true}],
   "replace_detected": false,
   "environment": [{"name": "staging smoke", "cmd": "./scripts/smoke.sh", "blocking": true}]}
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

# A failing test run normally means the implementation is wrong (tests are the executable spec).
# Only when the test code itself cannot load/compile is the Test Agent the owner of the fix.
TEST_CODE_BROKEN = re.compile(r"ERROR collecting|SyntaxError|ImportError while importing test|"
                              r"error TS\d+:.*\.(test|spec)\.|Cannot find module.*\.(test|spec)", re.I)
SKIP_DIRS = {".git", "node_modules", ".agentic-sdlc", "__pycache__", ".venv", "venv", "target", "build", "dist",
             ".gradle", "bin", "obj", ".idea", ".vscode", ".sfdx", ".sf", "vendor", ".pytest_cache"}
TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec|specs|src/test)/|(_test|\.test|\.spec|_spec)\.[a-z]+$|"
                       r"(Test|Tests|IT)\.(java|kt|cs|cls)$|(^|/)test_[^/]+\.py$", re.I)


@dataclass
class Check:
    name: str
    cmd: list[str] | None = None        # shell-free argv
    blocking: bool = True
    stack: str = ""
    kind: str = "test"                   # install | build | static | test | lint | environment
    runner: object = None                # python callable(ws, run_files, **opts) -> dict, instead of cmd
    timeout_s: int = 900
    env: dict = field(default_factory=dict)

    @property
    def tool(self) -> str | None:
        return self.cmd[0] if self.cmd else None


@dataclass
class Stack:
    name: str
    evidence: list[str]
    checks: list[Check]
    skill: str | None = None
    environment: list[Check] = field(default_factory=list)   # sandbox/deploy-level validation


def walk_files(ws: Path, limit: int = 20000) -> list[str]:
    out = []
    for root, dirs, files in os.walk(ws):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".") or d in (".github",)]
        for f in files:
            out.append(Path(root, f).relative_to(ws).as_posix())
            if len(out) >= limit:
                return out
    return out


def is_test_path(path: str) -> bool:
    return bool(TEST_PATH.search(path))


def _has(ws: Path, *names) -> list[str]:
    return [n for n in names if (ws / n).exists()]


def _json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


# ------------------------------------------------------------------ detectors
def _detect_salesforce(ws, files):
    ev = _has(ws, "sfdx-project.json") + (["force-app/"] if any(f.startswith("force-app/") for f in files) else [])
    if not ev:
        return None
    from . import salesforce_tools as sf
    return Stack("salesforce", ev, [
        Check("Metadata checks", runner=sf.metadata_runner, stack="salesforce", kind="static"),
        Check("PMD Apex rules", runner=sf.pmd_runner, stack="salesforce", kind="static"),
    ], skill="salesforce", environment=[
        Check("Salesforce org validation (deploy + Apex tests)", runner=sf.org_runner, stack="salesforce",
              kind="environment", timeout_s=3600),
    ])


def _detect_python(ws, files):
    py = [f for f in files if f.endswith(".py")]
    ev = _has(ws, "pyproject.toml", "requirements.txt", "setup.py", "setup.cfg") + (["*.py"] if py else [])
    if not ev:
        return None
    checks = []
    if any(is_test_path(f) for f in py):
        pp = ["-o", "pythonpath=src"] if (ws / "src").is_dir() else []
        checks.append(Check("pytest", ["python3" if shutil.which("python3") else "python", "-m", "pytest", "-q",
                                       "-p", "no:cacheprovider", "--tb=short", *pp], stack="python", kind="test"))
    checks.append(Check("ruff (lint)", ["ruff", "check", "--output-format", "concise", "."], blocking=False,
                        stack="python", kind="lint"))
    return Stack("python", ev, checks, skill="python")


def _detect_node(ws, files):
    if not (ws / "package.json").exists():
        return None
    pkg = _json(ws / "package.json")
    scripts = pkg.get("scripts", {})
    pm = "pnpm" if (ws / "pnpm-lock.yaml").exists() else "yarn" if (ws / "yarn.lock").exists() else "npm"
    checks = []
    if (pkg.get("dependencies") or pkg.get("devDependencies")) and not (ws / "node_modules").exists():
        checks.append(Check("install dependencies", [pm, "install"] + (["--no-audit", "--no-fund"] if pm == "npm" else []),
                            stack="node", kind="install"))
    if "build" in scripts:
        checks.append(Check("build", [pm, "run", "build"], stack="node", kind="build"))
    if "typecheck" in scripts:
        checks.append(Check("typecheck", [pm, "run", "typecheck"], stack="node", kind="static"))
    test = scripts.get("test", "")
    if test and "no test specified" not in test:
        checks.append(Check("tests", [pm, "test"] if pm != "npm" else ["npm", "test", "--silent"], stack="node"))
    if "lint" in scripts:
        checks.append(Check("lint", [pm, "run", "lint"], blocking=False, stack="node", kind="lint"))
    ts = (ws / "tsconfig.json").exists()
    return Stack("typescript" if ts else "node", ["package.json"] + (["tsconfig.json"] if ts else []), checks,
                 skill="node")


def _detect_java(ws, files):
    if (ws / "pom.xml").exists():
        return Stack("java-maven", ["pom.xml"], [Check("maven verify", ["mvn", "-q", "-B", "verify"], stack="java")],
                     skill="java")
    g = _has(ws, "build.gradle", "build.gradle.kts")
    if g:
        tool = ["./gradlew"] if (ws / "gradlew").exists() else ["gradle"]
        return Stack("java-gradle", g, [Check("gradle check", tool + ["check", "-q"], stack="java")], skill="java")
    return None


def _detect_dotnet(ws, files):
    ev = [f for f in files if f.endswith((".sln", ".csproj", ".fsproj"))][:3]
    if not ev:
        return None
    return Stack("dotnet", ev, [Check("dotnet build", ["dotnet", "build", "--nologo", "-v", "q"], stack="dotnet",
                                      kind="build"),
                                Check("dotnet test", ["dotnet", "test", "--nologo", "-v", "q"], stack="dotnet")],
                 skill="dotnet")


def _detect_go(ws, files):
    if not (ws / "go.mod").exists():
        return None
    return Stack("go", ["go.mod"], [Check("go vet", ["go", "vet", "./..."], stack="go", kind="static"),
                                    Check("go test", ["go", "test", "./..."], stack="go")], skill="go")


def _detect_rust(ws, files):
    if not (ws / "Cargo.toml").exists():
        return None
    return Stack("rust", ["Cargo.toml"], [Check("cargo test", ["cargo", "test", "-q"], stack="rust"),
                                          Check("clippy", ["cargo", "clippy", "-q"], blocking=False, stack="rust",
                                                kind="lint")], skill=None)


def _detect_ruby(ws, files):
    if not (ws / "Gemfile").exists():
        return None
    cmd = ["bundle", "exec", "rspec"] if (ws / "spec").is_dir() else ["bundle", "exec", "rake", "test"]
    return Stack("ruby", ["Gemfile"], [Check("tests", cmd, stack="ruby")], skill=None)


def _detect_php(ws, files):
    if not (ws / "composer.json").exists():
        return None
    scripts = _json(ws / "composer.json").get("scripts", {})
    cmd = ["composer", "test"] if "test" in scripts else ["vendor/bin/phpunit"]
    return Stack("php", ["composer.json"], [Check("tests", cmd, stack="php")], skill=None)


def _detect_dbt(ws, files):
    if not (ws / "dbt_project.yml").exists():
        return None
    return Stack("dbt", ["dbt_project.yml"], [Check("dbt parse", ["dbt", "parse"], stack="dbt", kind="static")],
                 skill=None)


def _detect_terraform(ws, files):
    if not any(f.endswith(".tf") for f in files):
        return None
    return Stack("terraform", ["*.tf"], [Check("terraform validate", ["terraform", "validate"], stack="terraform",
                                               kind="static"),
                                         Check("terraform fmt", ["terraform", "fmt", "-check", "-recursive"],
                                               blocking=False, stack="terraform", kind="lint")], skill=None)


DETECTORS = [_detect_salesforce, _detect_java, _detect_dotnet, _detect_node, _detect_python, _detect_go,
             _detect_rust, _detect_ruby, _detect_php, _detect_dbt, _detect_terraform]


# ------------------------------------------------------------------ profile
def detect(ws: Path) -> list[Stack]:
    ws = Path(ws)
    files = walk_files(ws)
    stacks = [s for s in (d(ws, files) for d in DETECTORS) if s]
    # A Salesforce repo usually ships a package.json for LWC/Jest tooling; don't treat that alone as a Node app.
    names = {s.name for s in stacks}
    if "salesforce" in names:
        stacks = [s for s in stacks if s.name not in ("node", "typescript") or s.checks and
                  any(c.kind == "test" for c in s.checks)]
    return stacks


def operator_config(ws: Path) -> dict:
    return _json(Path(ws) / ".agentic-sdlc" / "verify.json")


def checks_for(ws: Path, stage: str = "verify") -> tuple[list[Stack], list[Check]]:
    stacks = detect(ws)
    cfg = operator_config(ws)
    if stage == "environment":
        operator = [Check(c["name"], shlex.split(c["cmd"]), c.get("blocking", True), "operator", "environment",
                          timeout_s=c.get("timeout_s", 1800)) for c in cfg.get("environment", [])]
        return stacks, [c for s in stacks for c in s.environment] + operator
    detected = [] if cfg.get("replace_detected") else [c for s in stacks for c in s.checks]
    extra = [Check(c["name"], shlex.split(c["cmd"]), c.get("blocking", True), "operator", c.get("kind", "test"),
                   timeout_s=c.get("timeout_s", 900)) for c in cfg.get("checks", [])]
    order = {"install": 0, "build": 1, "static": 2, "test": 3, "lint": 4}
    return stacks, sorted(detected + extra, key=lambda c: order.get(c.kind, 3))


def describe(stacks: list[Stack]) -> str:
    if not stacks:
        return ("Stack profile: greenfield (no recognised stack yet). The architect chooses the stack in the design "
                "and every agent follows it. Use the stack's standard project layout, build tool and test framework "
                "so the orchestrator can detect and verify it automatically.")
    lines = ["Stack profile (detected from the repository):"]
    for s in stacks:
        checks = ", ".join(f"{c.name}{'' if c.blocking else ' (advisory)'}" for c in s.checks) or "none"
        lines.append(f"- {s.name} (evidence: {', '.join(s.evidence)}); verification: {checks}")
    return "\n".join(lines)


def project_context(ws: Path, limit: int = 120) -> str:
    """Repository map plus stack-specific inventory, so agents reuse what exists."""
    ws = Path(ws)
    stacks = detect(ws)
    files = [f for f in walk_files(ws) if not f.startswith(".agentic-sdlc")]
    if not files:
        return describe(stacks) + "\nExisting project: empty."
    by_ext: dict[str, int] = {}
    for f in files:
        by_ext[Path(f).suffix or Path(f).name] = by_ext.get(Path(f).suffix or Path(f).name, 0) + 1
    top = sorted({f.split("/")[0] + ("/" if "/" in f else "") for f in files})
    lines = [describe(stacks),
             f"Repository: {len(files)} files; top level: {', '.join(top[:40])}",
             "File types: " + ", ".join(f"{k}×{v}" for k, v in sorted(by_ext.items(), key=lambda kv: -kv[1])[:15]),
             "Existing source files (reuse and extend; don't duplicate):"]
    src = [f for f in files if Path(f).suffix in (".py", ".js", ".ts", ".tsx", ".java", ".kt", ".cs", ".go", ".rs",
                                                    ".rb", ".php", ".cls", ".trigger", ".sql", ".tf")]
    lines += [f"  {f}" for f in src[:limit]] + ([f"  … +{len(src) - limit} more"] if len(src) > limit else [])
    if any(s.name == "salesforce" for s in stacks):
        from . import salesforce_tools as sf
        lines.append(sf.project_context(ws))
    return "\n".join(lines)


# ------------------------------------------------------------------ running checks
def run_check(check: Check, ws: Path, run_files: set[str], **opts) -> dict:
    t0 = time.time()
    if check.runner:
        res = check.runner(ws, run_files, **opts)
        return {"name": check.name, "stack": check.stack, "kind": check.kind, "blocking": check.blocking,
                "duration_s": round(time.time() - t0, 2), **res}
    if not shutil.which(check.tool) and not (ws / check.tool).exists():
        return {"name": check.name, "stack": check.stack, "kind": check.kind, "blocking": check.blocking,
                "skipped": True, "passed": None, "summary": f"skipped: '{check.tool}' not installed",
                "output": f"Install {check.tool} to enable this check.", "violations": []}
    try:
        proc = subprocess.run(check.cmd, cwd=ws, capture_output=True, text=True, timeout=check.timeout_s,
                              env={**os.environ, **check.env, "CI": "1"})
        output = (proc.stdout + proc.stderr).strip()
        passed = proc.returncode == 0
    except subprocess.TimeoutExpired:
        output, passed = f"timed out after {check.timeout_s}s", False
    lines = [l.strip() for l in output.splitlines() if l.strip()]
    summary_line = re.compile(r"\b\d+ (passed|failed|failing|errors?)\b|^# (pass|fail) \d+|^Tests?:|BUILD (SUCCESS|FAILURE)|"
                              r"^(ok|FAIL)\s|Found \d+ errors?", re.I)
    picked = [l for l in lines if summary_line.search(l)]
    last = (" · ".join(picked[-2:]) if picked else (lines[-1] if lines else ""))
    return {"name": check.name, "stack": check.stack, "kind": check.kind, "blocking": check.blocking,
            "passed": passed, "summary": ("passed" if passed else "failed") + (f": {last[:120]}" if last else ""),
            "output": output, "duration_s": round(time.time() - t0, 2),
            "violations": [{"file": f, "line": None, "rule": check.name, "message": "referenced in failure output",
                            "engine": check.stack, "kind": check.kind} for f in sorted(_files_in(output, ws))]
            if not passed else [],
            "test_code_broken": bool(not passed and check.kind == "test" and TEST_CODE_BROKEN.search(output))}


def _files_in(output: str, ws: Path) -> set[str]:
    """Workspace files mentioned in tool output (used to route a failure to its owner)."""
    found = set()
    for m in re.finditer(r"([\w./\\-]+\.(?:py|js|mjs|cjs|ts|tsx|java|kt|cs|go|rs|rb|php|cls|trigger|xml|sql|tf))", output):
        p = m.group(1).replace("\\", "/")
        p = p.split(str(ws).replace("\\", "/") + "/")[-1].lstrip("./")
        if (ws / p).is_file():
            found.add(p)
    return found
