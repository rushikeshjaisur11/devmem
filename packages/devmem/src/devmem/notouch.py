"""No-touch zones: generated/vendored/migration/lock/secret paths agents must not edit."""
from __future__ import annotations

import fnmatch
import json
from pathlib import Path

DEFAULTS = ["migrations/", "vendor/", "vendored/", "node_modules/", "dist/", "build/", "generated/", "__generated__/",
            "*_pb2.py", "*_pb2_grpc.py", "*.lock", "package-lock.json", "*.min.js", ".env", ".env.*", "*.pem", "*.key",
            "id_rsa*"]
EXTRA_FILE = ".devmem/no-touch.txt"
HISTORY_PATH = ".devmem/history.json"


def patterns(root: Path) -> list[str]:
    pats = list(DEFAULTS)
    f = Path(root) / EXTRA_FILE
    if f.exists():
        pats += [l.strip() for l in f.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
    h = Path(root) / HISTORY_PATH
    if h.exists():
        try:
            pats += json.loads(h.read_text(encoding="utf-8")).get("no_touch", [])
        except Exception:
            pass
    return list(dict.fromkeys(pats))


def matches(path: str, pats: list[str]) -> str | None:
    path = path.replace("\\", "/")
    parts = path.split("/")
    for pat in pats:
        if pat.endswith("/"):
            d = pat[:-1]
            if (path.startswith(d + "/") or f"/{d}/" in f"/{path}") if "/" in d else d in parts[:-1]:
                return pat
        elif fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(parts[-1], pat):
            return pat
    return None
