"""`.devmemignore` handling: files agents and the indexer never read."""
from __future__ import annotations

import fnmatch
from pathlib import Path

DEFAULT_PATTERNS = [
    ".git/", ".devmem/", "graphify-out/", "node_modules/", "__pycache__/", ".venv/", "venv/", "dist/", "build/",
    "*.egg-info/", ".pytest_cache/", ".ruff_cache/", ".mypy_cache/",
    "*.lock", "package-lock.json", "poetry.lock", "uv.lock", "yarn.lock",
    "*.min.js", "*.pyc", "*.ipynb_checkpoints/", "data/samples/", "*.csv", "*.parquet",
    "*_pb2.py", "*_pb2_grpc.py", "vendor/", "vendored/", "migrations/",
]


class Ignore:
    def __init__(self, patterns: list[str]):
        self.patterns = [p.strip() for p in patterns if p.strip() and not p.startswith("#")]

    @classmethod
    def for_repo(cls, root: Path) -> "Ignore":
        pats = list(DEFAULT_PATTERNS)
        f = root / ".devmemignore"
        if f.exists():
            pats += f.read_text(encoding="utf-8").splitlines()
        return cls(pats)

    def matches(self, rel: str) -> bool:
        rel = rel.replace("\\", "/")
        parts = rel.split("/")
        for pat in self.patterns:
            if pat.endswith("/"):
                d = pat[:-1]
                if "/" in d:
                    if rel.startswith(d + "/") or f"/{d}/" in f"/{rel}":
                        return True
                elif any(fnmatch.fnmatch(p, d) for p in parts[:-1]):
                    return True
            else:
                if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(parts[-1], pat):
                    return True
        return False
