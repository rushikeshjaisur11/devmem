"""`devmem review`: what a diff touches, as one short Markdown summary (PR comment in CI, an agent's self-review, MCP).
`devmem gaps`: widely used code that no test reaches, riskiest first."""
from __future__ import annotations

from collections import deque

GAP_DEPTH = 3   # a test "reaches" code it calls within this many hops


def changed_symbols(ix, files) -> list[dict]:
    """Symbols whose lines the diff touches (new side), per FileDiff."""
    out = []
    for fd in files:
        if fd.status == "D" or fd.ignored:
            continue
        lines = set()
        for _, _, new_start, new_count, _, _ in fd.hunks:
            lines |= set(range(new_start, new_start + max(new_count, 1)))
        for s in ix.outline(fd.path):
            if s["kind"] != "module" and any(s["line"] <= ln <= s["end_line"] for ln in lines):
                out.append(s)
    return out


def risk(hop1: int, hop2: int, tests: int, capped: bool) -> str:
    if capped or hop1 >= 5 or (hop1 and not tests):
        return "high"
    if not tests or hop1:
        return "medium"
    return "low"


def build(ix, files, blocked=(), allowed=()) -> dict:
    rows = []
    for s in changed_symbols(ix, files):
        r = ix.impact(s["id"])
        rows.append({"id": s["id"], "line": s["line"], "is_test": s["is_test"], "hop1": len(r["hop1"]), "hop2": len(r["hop2"]),
                     "tests": len(r["tests"]), "capped": bool(r["capped"]),
                     "risk": "low" if s["is_test"] else risk(len(r["hop1"]), len(r["hop2"]), len(r["tests"]), bool(r["capped"]))})
    order = {"high": 0, "medium": 1, "low": 2}
    rows.sort(key=lambda x: (order[x["risk"]], -x["hop1"], x["id"]))
    from .check import tests_for
    _, cmd = tests_for(ix, [f.path for f in files if f.status != "D"])
    return {"files": len(files), "added": sum(f.added for f in files), "removed": sum(f.removed for f in files), "symbols": rows,
            "untested": sum(1 for x in rows if not x["tests"] and not x["is_test"]), "tests_cmd": cmd,
            "blocked": [f"[{x.check}] {x.path}: {x.msg}" for x in blocked], "allowed": [f"[{x.check}] {x.path}" for x in allowed]}


def render_md(r: dict, limit: int = 15) -> str:
    rows = r["symbols"]
    head = (f"**{r['files']} files, +{r['added']}/-{r['removed']} lines, {len(rows)} symbols changed.** "
            + (f"{r['untested']} changed symbol{'s' if r['untested'] != 1 else ''} with no test reaching it." if r["untested"] else "Every changed symbol is reached by a test."))
    out = ["### devmem review", head, ""]
    if rows:
        out += ["| Changed symbol | Callers (direct / indirect) | Tests reaching it | Risk |", "|---|---|---|---|"]
        out += [f"| `{x['id']}` | {x['hop1']}{'+' if x['capped'] else ''} / {x['hop2']} | {x['tests'] or '**none**'} | {x['risk']} |" for x in rows[:limit]]
        if len(rows) > limit:
            out.append(f"| +{len(rows) - limit} more | | | |")
        out.append("")
    out.append("**Diff guard:** " + ("passed" if not r["blocked"] else f"{len(r['blocked'])} blocked"))
    out += [f"- {b}" for b in r["blocked"][:10]] + [f"- allowed {a}" for a in r["allowed"][:5]]
    if r["tests_cmd"]:
        out += ["", f"**Affected tests:** `{r['tests_cmd']}`"]
    out += ["", "<sub>High risk: many callers, or callers but no test. Run `devmem impact <symbol>` for the full blast radius.</sub>"]
    return "\n".join(out)


def gaps(ix, limit: int = 20) -> list[dict]:
    """Non-test functions/methods no test reaches within GAP_DEPTH calls, ranked by how many places call them."""
    covered, frontier = set(), deque()
    for sid, s in ix.symbols.items():
        if s["is_test"]:
            frontier.append((sid, 0))
    seen = set(x for x, _ in frontier)
    while frontier:
        cur, d = frontier.popleft()
        if d >= GAP_DEPTH:
            continue
        for e in ix.out.get(cur, []):
            if e["type"] in ("calls", "tests") and e["dst"] not in seen:
                seen.add(e["dst"]); covered.add(e["dst"]); frontier.append((e["dst"], d + 1))
    out = []
    for sid, s in ix.symbols.items():
        if s["is_test"] or s["kind"] not in ("function", "method") or sid in covered:
            continue
        callers = {e["src"] for e in ix.inc.get(sid, []) if e["type"] == "calls"}
        if callers:
            out.append({"id": sid, "file": s["file"], "line": s["line"], "callers": len(callers),
                        "files": len({c.split("#")[0] for c in callers}), "doc": s["doc"]})
    out.sort(key=lambda x: (-x["callers"], -x["files"], x["id"]))
    return out[:limit]
