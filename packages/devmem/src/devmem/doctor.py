"""`devmem doctor`: is devmem usable by agents in this repo? Each check prints ok / FAIL with the fix, so one command tells a user
(or an agent) why a tool call or MCP connection does not work."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


def _mcp_roundtrip(root: Path, cmd: list[str]) -> str | None:
    """None when `cmd` starts an MCP server that lists tools and answers a find; else what went wrong."""
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "devmem_find", "arguments": {"query": "main", "limit": 1}}}]
    try:
        p = subprocess.run(cmd, cwd=root, input="\n".join(json.dumps(m) for m in msgs) + "\n", capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"could not start `{' '.join(cmd)}`: {e}"
    replies = {}
    for ln in p.stdout.splitlines():
        try:
            r = json.loads(ln)
            replies[r.get("id")] = r
        except ValueError:
            return f"non-JSON on stdout (breaks MCP): {ln[:80]}"
    if 1 not in replies or not replies[1].get("result", {}).get("tools"):
        return "server gave no tool list" + (f": {p.stderr.strip().splitlines()[-1][:160]}" if p.stderr.strip() else "")
    if 2 not in replies or "result" not in replies[2]:
        return "tools/call failed: " + json.dumps(replies.get(2, {}).get("error", "no reply"))[:160]
    return None


def checks(root: Path) -> list[tuple[bool | None, str, str]]:
    """[(ok, what, fix)]; ok None = warning (worth knowing, not a failure)."""
    from . import connect as cn
    out: list[tuple[bool, str, str]] = []
    out.append((sys.version_info >= (3, 10), f"Python {sys.version.split()[0]}", "devmem needs Python 3.10+"))
    try:
        import yaml  # noqa: F401
        out.append((True, "PyYAML available", ""))
    except ImportError:
        out.append((False, "PyYAML missing (lessons, rules and diffcheck need it)", "pip install pyyaml"))
    on_path = shutil.which("devmem")
    out.append((True if on_path else None, f"`devmem` on PATH{f' ({on_path})' if on_path else ': CLI-only agents and configs that run `devmem` need it'}",
                'install it: pip install "devmem @ git+https://github.com/rushikeshjaisur11/devmem.git#subdirectory=packages/devmem", '
                "or connect agents with `devmem connect --local`"))
    ip = root / ".devmem/index.json"
    if not ip.exists():
        out.append((False, "no .devmem/index.json", "run `devmem-app add <repo>` then `devmem-app publish <repo>`"))
        return out
    idx = json.loads(ip.read_text(encoding="utf-8"))
    r = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True)
    head = r.stdout.strip() if r.returncode == 0 else ""
    out.append((not head or idx.get("commit") == head, f"index built at {str(idx.get('commit'))[:8]}" + (f", HEAD is {head[:8]}" if head and idx.get("commit") != head else ""),
                "re-index: `devmem-app index <repo>` (or keep `devmem-app ui` running; it re-indexes when HEAD moves)"))
    ag = root / "AGENTS.md"
    has_block = ag.exists() and "<!-- devmem:start -->" in ag.read_text(encoding="utf-8")
    out.append((has_block, "AGENTS.md has the devmem block", "publish the repo: `devmem-app publish <repo>`"))
    err = _mcp_roundtrip(root, [sys.executable, "-m", "devmem.cli", "mcp"])
    out.append((err is None, "MCP server answers (tools/list + devmem_find)" if err is None else f"MCP server: {err}", "see the message; `devmem mcp` must print only JSON on stdout"))
    for agent, (rel, key, _) in cn.JSON_AGENTS.items():
        p = root / rel
        if not p.exists():
            continue
        try:
            entry = (json.loads(p.read_text(encoding="utf-8")).get(key) or {}).get(cn.SERVER)
        except ValueError as e:
            out.append((False, f"{agent}: {rel} is not valid JSON ({e})", f"fix the file, then `devmem connect {agent}`"))
            continue
        if not entry:
            continue
        cmd = entry["command"][0] if isinstance(entry.get("command"), list) else entry.get("command", "")
        found = shutil.which(cmd) or Path(cmd).is_file()
        out.append((bool(found), f"{agent}: {rel} starts `{cmd}`", f"`{cmd}` is not found here: install devmem on PATH, or `devmem connect {agent} --local`"))
    return out


def run(root: Path) -> int:
    bad = 0
    for ok, what, fix in checks(root):
        print(f"{'ok  ' if ok else 'warn' if ok is None else 'FAIL'}  {what}" + ("" if ok else f"\n      fix: {fix}"))
        bad += ok is False
    print("all good" if not bad else f"{bad} problem(s)")
    return 1 if bad else 0
