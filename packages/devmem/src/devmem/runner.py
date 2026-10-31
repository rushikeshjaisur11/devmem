"""Run repo commands (lint/types/tests) and squeeze their output into a few lines an agent can afford."""
from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import sys
import time

TIMEOUT = 600


def _quote(s: str) -> str:
    """Quote one shell argument for the shell `run_cmd` uses (cmd.exe has no single quotes)."""
    if sys.platform != "win32":
        return shlex.quote(s)
    q = subprocess.list2cmdline([s])   # quotes only for spaces; cmd.exe also treats ^ & | < > ( ) as operators outside quotes
    return f'"{q}"' if not q.startswith('"') and any(ch in s for ch in "^&|<>()") else q


def _fix_python(cmd: str) -> str:
    """Leading `python`/`python3` runs with the interpreter devmem itself runs in (override: DEVMEM_PYTHON)."""
    import os
    parts = cmd.split(None, 1)
    if parts and parts[0] in ("python", "python3"):
        py = os.environ.get("DEVMEM_PYTHON") or sys.executable
        return _quote(py) + (" " + parts[1] if len(parts) > 1 else "")
    return cmd


def run_cmd(cmd: str, cwd, timeout: int = TIMEOUT) -> dict:
    t0 = time.time()
    try:
        r = subprocess.run(_fix_python(cmd), shell=True, cwd=str(cwd), capture_output=True, text=True,
                           errors="replace", timeout=timeout)
        return {"code": r.returncode, "out": (r.stdout or "") + (r.stderr or ""), "secs": round(time.time() - t0, 1)}
    except subprocess.TimeoutExpired as e:
        return {"code": 124, "out": f"timeout after {timeout}s", "secs": timeout}


def quote_all(items) -> str:
    return " ".join(_quote(i) for i in items)


def summarize(kind: str, out: str, limit: int = 15) -> list[str]:
    lines = [l.rstrip() for l in out.splitlines() if l.strip()]
    if kind == "test":
        fails = [l for l in lines if re.match(r"(FAILED|ERROR|FAIL) ", l)]
        last = [l for l in lines if re.search(r"\d+ (passed|failed|error)", l)][-1:]
        if fails or last:
            return [l[:200] for l in fails[:limit]] + (["…"] if len(fails) > limit else []) + last
    if kind in ("lint", "types"):
        hits = [l for l in lines if re.search(r"(error|warning|[A-Z]+\d{2,4})\b", l)] or lines
        return [l[:200] for l in hits[:limit]] + ([f"… {len(hits) - limit} more"] if len(hits) > limit else [])
    return [l[:200] for l in lines[-limit:]]


def find_tool(name: str) -> list[str] | None:
    """argv prefix to launch a python-installed tool: its binary next to this interpreter, on PATH, or `-m`."""
    import importlib.util
    import os
    from pathlib import Path
    bindir = Path(sys.executable).parent
    for cand in (bindir / name, bindir / f"{name}.exe"):
        if cand.exists():
            return [str(cand)]
    found = shutil.which(name)
    if found:
        return [found]
    if importlib.util.find_spec(name):
        return [sys.executable, "-m", name]
    return None


def run_argv(argv: list[str], cwd, input: str | None = None, timeout: int = TIMEOUT) -> dict:
    try:
        r = subprocess.run(argv, cwd=str(cwd), input=input, capture_output=True, text=True, errors="replace",
                           timeout=timeout)
        return {"code": r.returncode, "out": r.stdout or "", "err": r.stderr or ""}
    except (subprocess.TimeoutExpired, OSError) as e:
        return {"code": 124, "out": "", "err": str(e)}
