"""Core formats shared by the indexer (app) and the agent CLI.

index.json layout (INDEX_VERSION 1):
{
  "version": 1, "repo": str, "commit": str|None, "generated": iso-date,
  "files":   {path: {"hash": str, "lang": str, "imports": [...], "tables": [...], "calls": [...]}},
  "symbols": [{"id": "path#qualname", "name", "qualname", "kind", "file", "line", "end_line",
               "sig", "doc", "is_test": bool}],
  "edges":   [{"src": id, "dst": id, "type": "calls"|"tests"|"reads"|"writes", "conf": str}]
}
For table edges, dst is "table:<name>".
"""
from __future__ import annotations

import datetime as _dt

INDEX_VERSION = 1
INDEX_PATH = ".devmem/index.json"
DESC_PATH = ".devmem/descriptions.json"   # {symbol id: {text, by: ai|human, code: hash of the source it describes}}
DB_PATH = ".devmem/index.db"  # derived FTS5 cache of index.json; rebuilt when index.json changes
IGNORE_FILE = ".devmemignore"

CONF_PRECISE = "precise"        # type-resolved (SCIP, later)
CONF_STRUCTURAL = "structural"  # name/import resolved
CONF_AMBIGUOUS = "ambiguous"    # several candidates matched
CONF_COCHANGE = "co-change"

# Impact rules (code-review-graph style): hop 1 required, hop 2 contract check, nothing beyond.
MAX_HOPS = 2
UTILITY_CALLER_CAP = 20


def atomic_write(path, text: str) -> None:
    """Write a file so that readers see either the old content or the new, never half of it: write a temp file next to it,
    then rename over the target (atomic on the same filesystem). A crash mid-write leaves the previous file intact."""
    import os
    import tempfile
    from pathlib import Path
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def knowledge_header(commit: str | None, tokens: int) -> str:
    """Freshness/budget stamp for generated knowledge files."""
    return f"<!-- devmem: commit={commit or 'unknown'} generated={_dt.date.today()} tokens={tokens} -->"


def new_index(repo: str, commit: str | None) -> dict:
    return {
        "version": INDEX_VERSION,
        "repo": repo,
        "commit": commit,
        "generated": str(_dt.date.today()),
        "files": {},
        "symbols": [],
        "edges": [],
    }
