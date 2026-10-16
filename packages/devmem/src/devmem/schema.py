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
IGNORE_FILE = ".devmemignore"

CONF_PRECISE = "precise"        # type-resolved (SCIP, later)
CONF_STRUCTURAL = "structural"  # name/import resolved
CONF_AMBIGUOUS = "ambiguous"    # several candidates matched
CONF_COCHANGE = "co-change"

# Impact rules (code-review-graph style): hop 1 required, hop 2 contract check, nothing beyond.
MAX_HOPS = 2
UTILITY_CALLER_CAP = 20


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
