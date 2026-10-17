"""Central knowledge hub, agent side. One git repo holds every onboarded repo's knowledge:

    catalog/<repo>.json     reusable symbols (what `devmem reuse` searches)
    repos/<repo>/           that repo's published knowledge (overview.md, map.md, ...) + meta.json
    lessons/ rules/         shared memory
    INDEX.md                one line per repo

The agent points at it with `.devmem/hub.txt` (written by `devmem-app publish`) or the DEVMEM_HUB variable: a git URL, or a
local path (e.g. when the hub repo is checked out next to the working repo). Everything degrades quietly: no hub, no network,
no access => `devmem reuse` falls back to the catalog committed in the repo."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

HUB_FILE = ".devmem/hub.txt"
STALE_AFTER = 6 * 3600


def spec(root: Path) -> str | None:
    env = os.environ.get("DEVMEM_HUB", "").strip()
    if env:
        return env
    p = Path(root) / HUB_FILE
    if p.exists():
        lines = [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")]
        return lines[0] if lines else None
    return None


def cache_dir(s: str) -> Path:
    base = Path(os.environ.get("DEVMEM_CACHE") or Path.home() / ".cache" / "devmem")
    return base / "hub" / hashlib.sha1(s.encode()).hexdigest()[:12]


def local_dir(s: str) -> Path | None:
    p = Path(s).expanduser()
    if p.is_dir():
        return p
    c = cache_dir(s)
    return c if (c / ".git").exists() or (c / "catalog").exists() else None


def sync(s: str, timeout: int = 90) -> tuple[Path | None, str]:
    """Shallow clone or fast-forward the hub into the cache. Never prompts for credentials."""
    p = Path(s).expanduser()
    if p.is_dir():
        return p, "local hub, nothing to sync"
    c = cache_dir(s)
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        if (c / ".git").exists():
            r = subprocess.run(["git", "-C", str(c), "pull", "--ff-only", "--depth", "1", "-q"], capture_output=True, text=True, env=env, timeout=timeout)
        else:
            c.parent.mkdir(parents=True, exist_ok=True)
            r = subprocess.run(["git", "clone", "--depth", "1", "-q", "--", s, str(c)], capture_output=True, text=True, env=env, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as e:
        return local_dir(s), f"hub sync failed ({e}); using cached copy" if local_dir(s) else f"hub sync failed ({e})"
    if r.returncode:
        why = (r.stderr or r.stdout).strip().splitlines()[-1:] or ["unknown error"]
        return local_dir(s), f"hub sync failed ({why[0][:120]})" + ("; using cached copy" if local_dir(s) else "")
    (c / ".devmem-synced").write_text(str(time.time()))
    return c, "synced"


def age(d: Path) -> float | None:
    m = d / ".devmem-synced"
    return time.time() - float(m.read_text()) if m.exists() else None


def resolve(root: Path, auto_sync: bool = True) -> tuple[Path | None, str]:
    """The hub directory to read, syncing it first when it is a URL whose cache is missing or stale."""
    s = spec(root)
    if not s:
        return None, ""
    d = local_dir(s)
    if Path(s).expanduser().is_dir():
        return d, ""
    a = age(d) if d else None
    if auto_sync and (d is None or a is None or a > STALE_AFTER):
        return sync(s)
    return d, ""


def entries(d: Path) -> list[dict]:
    out: list[dict] = []
    for f in sorted((d / "catalog").glob("*.json")):
        try:
            out += json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return out


def repos(d: Path) -> list[dict]:
    out = []
    for m in sorted((d / "repos").glob("*/meta.json")):
        try:
            out.append(json.loads(m.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out
