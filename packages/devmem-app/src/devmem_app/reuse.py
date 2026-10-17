"""Cross-repo reuse catalog: the reusable symbols of every OTHER onboarded repo, with the source, what it calls, the
third-party packages it needs, the tables it touches and the tests that show how to use it. The fit check against the
target repo lives in `devmem.reuse` (pure stdlib) so agents can run it offline from the committed catalog."""
from __future__ import annotations

import json
from pathlib import Path

from devmem import reuse as core
from devmem.index import Index

from .ranking import pagerank

NODE_BUILTINS = {"fs", "path", "os", "http", "https", "url", "util", "crypto", "stream", "events", "child_process", "zlib", "net", "buffer", "assert"}
MAX_DEPS = 8


def _internal_names(ix: Index) -> set[str]:
    names = set()
    for p in ix.data["files"]:
        parts = p.split("/")
        names.add(parts[0].rsplit(".", 1)[0])
        names.add(parts[-1].rsplit(".", 1)[0])
    return names


def _ext_imports(ix: Index, s: dict, code: str, internal: set[str]) -> list[str]:
    out = set()
    for i in ix.data["files"].get(s["file"], {}).get("imports", []):
        mod = i.get("module") or ""
        top = core._top(mod)
        if not top or mod.startswith(".") or top in core.STDLIB or top in NODE_BUILTINS or top in internal:
            continue
        used = i.get("alias") or i.get("name") or mod.split(".")[-1]
        if used and used in code:
            out.add(top)
    return sorted(out)


def _entry(ix: Index, s: dict, internal: set[str], code_lines: int) -> dict:
    code = ix.source(s)
    lines = code.splitlines()
    tables = sorted({e["dst"][6:] for e in ix.out.get(s["id"], []) if e["type"] in ("reads", "writes") and e["dst"].startswith("table:")})
    tests = [ix.symbols[e["src"]] for e in ix.inc.get(s["id"], []) if e["type"] == "tests" and e["src"] in ix.symbols]
    deps = [{"name": d["qualname"], "sig": d["sig"], "file": d["file"], "line": d["line"], "same_file": d["file"] == s["file"]}
            for d in ix.callees(s) if not d["is_test"] and d["id"] != s["id"]][:MAX_DEPS]
    return {"repo": ix.data["repo"], "id": s["id"], "name": s["qualname"], "kind": s["kind"], "sig": s["sig"], "doc": s["doc"],
            "file": s["file"], "line": s["line"], "end_line": s["end_line"], "lang": ix.data["files"].get(s["file"], {}).get("lang", ""),
            "code": "\n".join(lines[:code_lines]) if code_lines else "", "truncated": len(lines) > code_lines > 0,
            "deps": deps, "ext_imports": _ext_imports(ix, s, code, internal), "tables": tables,
            "tests": [{"name": t["qualname"], "file": t["file"], "line": t["line"]} for t in tests[:3]]}


def build_entries(ix: Index, per_peer: int | None = 150, code_lines: int = 60) -> list[dict]:
    """Public functions/classes (not tests, not trivial one-liners), best-connected and documented first."""
    rank = pagerank(ix.data)
    cands = [s for s in ix.symbols.values()
             if s["kind"] in ("function", "class") and not s["is_test"] and not s["name"].startswith("_")
             and s["end_line"] - s["line"] >= 2]
    cands.sort(key=lambda s: (-(rank.get(s["id"], 0) * 1000 + (5 if s["doc"] else 0)), s["id"]))
    internal = _internal_names(ix)
    return [_entry(ix, s, internal, code_lines) for s in (cands[:per_peer] if per_peer else cands)]


def _peers(name: str, root: Path, peers: dict[str, str | Path]):
    for pname, proot in peers.items():
        if pname == name or Path(proot).resolve() == Path(root).resolve():
            continue
        try:
            yield pname, Index.load(Path(proot))
        except FileNotFoundError:
            continue


def write_catalog(name: str, root: Path, peers: dict[str, str | Path], per_peer: int = 150, code_lines: int = 60,
                  max_bytes: int = 1_500_000) -> list[dict]:
    """Committed to the repo so agents have it offline. Trimmed to max_bytes by dropping the lowest-ranked entries."""
    entries: list[dict] = []
    for pname, ix in _peers(name, root, peers):
        entries += build_entries(ix, per_peer, code_lines)
    while entries and len(json.dumps(entries)) > max_bytes:
        entries = entries[: int(len(entries) * 0.85)]
    p = Path(root) / core.CATALOG
    p.parent.mkdir(parents=True, exist_ok=True)
    if entries:
        p.write_text(json.dumps(entries), encoding="utf-8")
    elif p.exists():
        p.unlink()
    return entries


def live(need: str, target: str, repos: dict[str, str | Path], limit: int = 5, code_lines: int = 60) -> list[dict]:
    """Search every other repo's current index (no catalog needed) and fit the best hits against `target`."""
    tix = Index.load(Path(repos[target]))
    pool, ixs = [], {}
    for pname, ix in _peers(target, Path(repos[target]), repos):
        ixs[pname] = ix
        pool += build_entries(ix, None, 0)
    out = []
    for score, e in core.search(pool, need, limit):
        ix = ixs[e["repo"]]
        full = _entry(ix, ix.symbols[e["id"]], _internal_names(ix), code_lines)
        out.append({"score": score, "entry": full, "fit": core.fit(full, tix), "prompt": core.port_prompt(full, core.fit(full, tix), target)})
    return out
