"""Detect install/lint/types/test commands, verify they run, and find flaky tests."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from devmem.check import COMMANDS_PATH, FLAKY_PATH, load_commands
from devmem.runner import quote_all, run_cmd


def _read(root: Path, name: str) -> str:
    p = root / name
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


def detect(root: str | Path) -> dict:
    root = Path(root)
    pyproject, reqs = _read(root, "pyproject.toml"), _read(root, "requirements.txt") + _read(root, "requirements-dev.txt")
    pkg, pkg_deps = {}, {}
    try:
        pj = json.loads(_read(root, "package.json") or "{}")
        pkg = pj.get("scripts", {})
        pkg_deps = {**pj.get("dependencies", {}), **pj.get("devDependencies", {})}
    except Exception:
        pass
    make = _read(root, "Makefile")
    has_tests = any((root / "tests").glob("test_*.py")) if (root / "tests").exists() else bool(list(root.glob("test_*.py")))
    out: dict = {}

    if (root / "uv.lock").exists():
        out["install"] = "uv sync"
    elif reqs:
        out["install"] = "pip install -r requirements.txt"
    elif "[project]" in pyproject or "[tool.poetry]" in pyproject:
        out["install"] = "pip install -e ."
    elif (root / "package-lock.json").exists():
        out["install"] = "npm ci"

    if (root / "pytest.ini").exists() or "[tool.pytest" in pyproject or "pytest" in reqs or has_tests:
        out["test"] = "python -m pytest -q"
        out["test_one"] = "python -m pytest -q {tests}"
    elif "test" in pkg:
        out["test"] = "npm test"
    elif re.search(r"^test:", make, re.M):
        out["test"] = "make test"

    if "[tool.ruff" in pyproject or (root / "ruff.toml").exists() or (root / ".ruff.toml").exists() or "ruff" in reqs:
        out["lint"] = "ruff check {files}"
    elif (root / ".flake8").exists() or "flake8" in reqs:
        out["lint"] = "flake8 {files}"
    elif "lint" in pkg:
        out["lint"] = "npm run lint"
    elif re.search(r"^lint:", make, re.M):
        out["lint"] = "make lint"

    if "[tool.mypy" in pyproject or (root / "mypy.ini").exists() or "mypy" in reqs:
        out["types"] = "mypy {files}"
    if pkg_deps or pkg:
        if "vitest" in pkg_deps:
            out["test_one_js"] = "npx vitest run {tests}"
        elif "jest" in pkg_deps:
            out["test_one_js"] = "npx jest {tests}"
        elif "test" in pkg:
            out["test_one_js"] = "npm test -- {tests}"
        if "eslint" in pkg_deps or any(root.glob("eslint.config.*")) or any(root.glob(".eslintrc*")):
            out["lint_js"] = "npx eslint {files}"
        if (root / "tsconfig.json").exists():
            out["types_js"] = "npx tsc --noEmit"
        if "install" not in out and (root / "package.json").exists():
            out["install"] = "npm install"
    return {k: {"cmd": v, "verified": None} for k, v in out.items()}


def merge_with_existing(root: Path, detected: dict) -> dict:
    old = load_commands(root)
    merged = dict(detected)
    for k, v in merged.items():
        if k in old and old[k].get("cmd") == v["cmd"]:
            v.update({x: y for x, y in old[k].items() if x != "cmd"})
    for k, v in old.items():  # keep hand-added commands
        merged.setdefault(k, v)
    return merged


def save_commands(root: Path, cmds: dict):
    p = root / COMMANDS_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cmds, indent=1, sort_keys=True), encoding="utf-8")


def verify(root: str | Path, idx: dict, include_install: bool = False, timeout: int = 300) -> dict:
    """Run each command for real and record the result in .devmem/commands.json."""
    root = Path(root)
    cmds = merge_with_existing(root, detect(root))
    py = [f for f in idx["files"] if f.endswith(".py") and not f.startswith("tests/")][:3]
    tests = sorted({s["file"] for s in idx["symbols"] if s["is_test"]})[:1]
    for name, spec in cmds.items():
        if name == "install" and not include_install:
            spec["verified"] = None
            spec["note"] = "not run (use --include-install)"
            continue
        cmd = spec["cmd"].replace("{files}", quote_all(py)).replace("{tests}", quote_all(tests))
        if ("{files}" in spec["cmd"] and not py) or ("{tests}" in spec["cmd"] and not tests):
            spec["verified"], spec["note"] = None, "nothing to run it on"
            continue
        r = run_cmd(cmd, root, timeout)
        # lint exiting 1 means "found issues", which still proves the tool runs
        ran = r["code"] == 0 or (name in ("lint", "types") and r["code"] == 1 and "not found" not in r["out"])
        spec["verified"] = bool(ran)
        spec["secs"] = r["secs"]
        spec["note"] = "" if ran else r["out"].strip().splitlines()[-1][:160] if r["out"].strip() else f"exit {r['code']}"
    save_commands(root, cmds)
    return cmds


def detect_flaky(root: str | Path, runs: int = 3, cmd: str = "python -m pytest -q -rA --tb=no -p no:cacheprovider") -> list[str]:
    """Run the suite several times; tests with mixed outcomes are appended to .devmem/flaky.txt."""
    root = Path(root)
    seen: dict[str, set[str]] = defaultdict(set)
    for _ in range(runs):
        out = run_cmd(cmd, root)["out"]
        for line in out.splitlines():
            m = re.match(r"(PASSED|FAILED|ERROR) (\S+)", line)
            if m:
                seen[m.group(2)].add("PASSED" if m.group(1) == "PASSED" else "FAILED")
    flaky = sorted(t for t, st in seen.items() if len(st) > 1)
    if flaky:
        p = root / FLAKY_PATH
        existing = p.read_text(encoding="utf-8").splitlines() if p.exists() else ["# known flaky tests (node id prefixes)"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(existing + [t for t in flaky if t not in existing]) + "\n", encoding="utf-8")
    return flaky


def render_commands_md(cmds: dict) -> str:
    lines = ["# Commands", "Run these instead of guessing. `verified` means the app ran it successfully.", "",
             "| Purpose | Command | Verified |", "| --- | --- | --- |"]
    label = {"install": "install", "lint": "lint (changed files)", "types": "type check", "test": "all tests",
             "test_one": "tests for given files/ids"}
    for k in ("install", "lint", "types", "test", "test_one"):
        if k in cmds:
            v = cmds[k]
            st = {True: "yes", False: f"FAILED: {v.get('note', '')}", None: "not run"}[v.get("verified")]
            lines.append(f"| {label[k]} | `{v['cmd']}` | {st} |")
    lines += ["", "Shortcuts: `devmem tests-for` prints the targeted test command; `devmem check` runs lint, types and",
              "the affected tests and prints only a summary."]
    return "\n".join(lines)


def render_tests_md(idx: dict, flaky: list[str], max_files: int = 25) -> str:
    """Source file -> tests that reach it (direct calls plus callers' tests, via impact)."""
    from devmem.index import Index
    ix = Index(idx, Path("."))
    src_files = sorted({s["file"] for s in idx["symbols"] if s["kind"] != "module" and not s["is_test"]})
    by_src = {}
    for f in src_files:
        sel = ix.tests_for_files([f])
        if sel:
            by_src[f] = Index.pytest_ids(sel)
    lines = ["# Test map", "Source file -> tests that reach it (direct and through callers). "
             "`devmem tests-for <files>` prints the exact command.", ""]
    ranked = sorted(by_src.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    for f, ts in sorted(ranked[:max_files]):
        lines.append(f"- `{f}` <- " + ", ".join(f"`{t}`" for t in ts[:4]) + (f" (+{len(ts) - 4} more)" if len(ts) > 4 else ""))
    if len(ranked) > max_files:
        lines.append(f"- ... {len(ranked) - max_files} more source files")
    untested = [f for f in src_files if f not in by_src]
    if untested:
        lines += ["", "No test reaches: " + ", ".join(f"`{f}`" for f in untested[:15]) + (" ..." if len(untested) > 15 else "")]
    if flaky:
        lines += ["", "## Known flaky (failures here are not caused by your change)"] + [f"- `{t}`" for t in flaky]
    return "\n".join(lines)
