"""`devmem check` and `devmem tests-for`: targeted verification with compact output."""
from __future__ import annotations

import json
from pathlib import Path

from .index import Index
from .jsresolve import is_js
from .runner import quote_all, run_cmd, summarize

COMMANDS_PATH = ".devmem/commands.json"
FLAKY_PATH = ".devmem/flaky.txt"


def load_commands(root: Path) -> dict:
    p = root / COMMANDS_PATH
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def load_flaky(root: Path) -> list[str]:
    p = root / FLAKY_PATH
    return [l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")] if p.exists() else []


def is_flaky(failed_line: str, flaky: list[str]) -> bool:
    return any(f in failed_line for f in flaky)


def test_commands(ix: Index, paths: list[str]) -> tuple[list[str], list[str]]:
    """(selected test ids/files, ready-to-run commands): pytest ids for Python tests, test files for JS/TS runners."""
    sel = ix.tests_for_files(paths)
    py = {f: q for f, q in sel.items() if not is_js(f)}
    js = sorted(f for f in sel if is_js(f))
    cmds = load_commands(ix.root)
    ids, out = [], []
    if py:
        pids = Index.pytest_ids(py)
        tmpl = (cmds.get("test_one") or {}).get("cmd") or "python -m pytest -q {tests}"
        ids += pids
        out.append(tmpl.replace("{tests}", quote_all(pids)))
    if js:
        tmpl = (cmds.get("test_one_js") or {}).get("cmd") or "npm test -- {tests}"
        ids += js
        out.append(tmpl.replace("{tests}", quote_all(js)))
    return ids, out


def tests_for(ix: Index, paths: list[str]) -> tuple[list[str], str | None]:
    """(test ids, one ready-to-run command or None)."""
    ids, cmds = test_commands(ix, paths)
    return ids, (" && ".join(cmds) if cmds else None)


def _log_failure(root: Path, kind: str, lines: list[str]) -> None:
    import datetime as dt
    p = root / ".devmem/failures.jsonl"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            for l in lines[:5]:
                fh.write(json.dumps({"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                                     "kind": kind, "line": l[:200]}) + "\n")
    except OSError:
        pass


def run_check(ix: Index, paths: list[str]) -> int:
    root, cmds, flaky = ix.root, load_commands(ix.root), load_flaky(ix.root)
    py = [p for p in paths if p.endswith(".py") and (root / p).exists()]
    js = [p for p in paths if is_js(p) and (root / p).exists()]
    failed = False
    steps = []
    for name in ("lint", "types"):
        spec = cmds.get(name)
        if spec and py:
            steps.append((name, spec["cmd"].replace("{files}", quote_all(py))))
    if js:
        if cmds.get("lint_js"):
            steps.append(("lint", cmds["lint_js"]["cmd"].replace("{files}", quote_all(js))))
        if cmds.get("types_js"):
            steps.append(("types", cmds["types_js"]["cmd"]))
    ids, tcmds = test_commands(ix, paths)
    for tcmd in tcmds:
        steps.append(("test", tcmd))
    if not steps:
        print("nothing to check (no lint/types commands detected and no mapped tests)")
        return 0
    for kind, cmd in steps:
        r = run_cmd(cmd, root)
        lines = summarize(kind, r["out"])
        if r["code"] == 0:
            print(f"ok   {kind} ({r['secs']}s)")
            continue
        if kind == "test":
            real = [l for l in lines if l.startswith(("FAILED", "ERROR", "FAIL ")) and not is_flaky(l, flaky)]
            known = [l for l in lines if l.startswith(("FAILED", "ERROR", "FAIL ")) and is_flaky(l, flaky)]
            if not real and known and r["code"] == 1:
                print(f"ok   test ({len(known)} known flaky failure(s) ignored: " + "; ".join(k[:80] for k in known) + ")")
                continue
        failed = True
        _log_failure(root, kind, lines)
        print(f"FAIL {kind} ({r['secs']}s): {cmd[:120]}")
        for l in lines:
            print("   " + l)
    return 1 if failed else 0
