"""`devmem check` and `devmem tests-for`: targeted verification with compact output."""
from __future__ import annotations

import json
from pathlib import Path

from .index import Index
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


def tests_for(ix: Index, paths: list[str]) -> tuple[list[str], str | None]:
    """(pytest node ids, ready-to-run command or None)."""
    ids = Index.pytest_ids(ix.tests_for_files(paths))
    cmds = load_commands(ix.root)
    tmpl = (cmds.get("test_one") or {}).get("cmd") or "python -m pytest -q {tests}"
    return ids, (tmpl.replace("{tests}", quote_all(ids)) if ids else None)


def run_check(ix: Index, paths: list[str]) -> int:
    root, cmds, flaky = ix.root, load_commands(ix.root), load_flaky(ix.root)
    py = [p for p in paths if p.endswith(".py") and (root / p).exists()]
    failed = False
    steps = []
    for name in ("lint", "types"):
        spec = cmds.get(name)
        if spec and py:
            steps.append((name, spec["cmd"].replace("{files}", quote_all(py))))
    ids, tcmd = tests_for(ix, paths)
    if tcmd:
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
            real = [l for l in lines if l.startswith(("FAILED", "ERROR")) and not is_flaky(l, flaky)]
            known = [l for l in lines if l.startswith(("FAILED", "ERROR")) and is_flaky(l, flaky)]
            if not real and known and r["code"] == 1:
                print(f"ok   test ({len(known)} known flaky failure(s) ignored: " + "; ".join(k[:80] for k in known) + ")")
                continue
        failed = True
        print(f"FAIL {kind} ({r['secs']}s): {cmd[:120]}")
        for l in lines:
            print("   " + l)
    return 1 if failed else 0
