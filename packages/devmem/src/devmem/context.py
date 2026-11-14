"""`devmem context "<task>"`: everything an agent needs to start a task, in one call and inside a token budget.

Starting a task used to take four calls (recall, find, show, impact), each with its own request and reply overhead, and
each printing things the next one repeats. This packs them: rules and lessons that apply, the best matches, the body of
the most likely symbol, who calls it, which tests cover it and what is already known to be wrong there. Sections are
added in that order until the budget is spent, so the most useful part always fits. Tokens are estimated as chars / 4."""
from __future__ import annotations

import json
from collections import defaultdict

from .index import Index

DEFAULT_BUDGET = 1200
MIN_BODY_LINES = 12
# words that say what to do, not where: left out when looking for the code a task is about
TASK_WORDS = {"fix", "fixes", "fixing", "bug", "issue", "error", "add", "adding", "change", "update", "implement", "make", "support", "improve",
              "refactor", "remove", "handle", "allow", "ensure", "should", "when", "with", "for", "the", "and", "not", "that", "this", "from",
              "into", "does", "doesn", "work", "working", "broken", "wrong", "correctly", "properly", "new", "use", "using", "code", "function", "method"}


def code_words(task: str) -> str:
    kept = [w for w in task.replace("`", " ").split() if w.lower().strip(".,:;()\"'") not in TASK_WORDS and len(w) > 2]
    return " ".join(kept) or task


def est(text: str) -> int:
    return len(text) // 4


def grouped(ids: list[str], cap: int = 0, tags: dict[str, str] | None = None) -> str:
    """`a.py#f, a.py#g, b.py#h` -> `a.py: f, g; b.py: h`. The path is printed once per file, which is where most of the
    characters of a caller list go. `tags` marks single items (e.g. ambiguous edges); `cap` limits the items shown."""
    if not ids:
        return "-"
    more = len(ids) - cap if cap and len(ids) > cap else 0
    by_file: dict[str, list[str]] = defaultdict(list)
    for i in (ids[:cap] if more else ids):
        f, _, q = i.partition("#")
        by_file[f].append((q or f) + (f" [{tags[i]}]" if tags and i in tags else ""))
    out = "; ".join(f"{f}: {', '.join(qs)}" if qs != [f] else f for f, qs in by_file.items())
    return out + (f" (+{more} more: --all)" if more else "")


def distinct(ix: Index, hits: list[tuple[float, dict]], limit: int) -> list[tuple[dict, list[dict]]]:
    """Collapse hits that are the same definition repeated across files (same kind and qualified name): one line for the
    best, with the others listed after it. A query then returns `limit` different things instead of ten copies of one."""
    seen: dict[tuple, int] = {}
    out: list[tuple[dict, list[dict]]] = []
    for _, s in hits:
        key = (s["kind"], s["qualname"])
        if key in seen:
            out[seen[key]][1].append(s)
        elif len(out) < limit:
            seen[key] = len(out)
            out.append((s, []))
    return out


def hit_line(ix: Index, s: dict, twins: list[dict], doc_chars: int = 100) -> str:
    said = s.get("doc") or ix.desc.get(s["id"], "")
    line = f"{s['file']}:{s['line']}  {s['kind']} {s['qualname']}" + (f" — {said[:doc_chars]}" if said else "")
    if twins:
        line += f"  (+{len(twins)} same name: " + ", ".join(f"{t['file']}:{t['line']}" for t in twins[:3]) + (", …" if len(twins) > 3 else "") + ")"
    return line


def known_issues(ix: Index, s: dict, cap: int = 3) -> list[str]:
    """Findings recorded for this symbol's lines by the code health checks or SonarQube, most severe first."""
    rank = {"high": 0, "medium": 1, "low": 2}
    found = []
    for rel, keys in ((".devmem/sonar.json", ("issues", "hotspots")), (".devmem/hygiene.json", ("issues",))):
        try:
            d = json.loads((ix.root / rel).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for k in keys:
            for i in d.get(k, []):
                if i.get("path") == s["file"] and s["line"] <= (i.get("line") or 0) <= s.get("end_line", s["line"]):
                    found.append(i)
    found.sort(key=lambda i: (rank.get(i.get("severity"), 2), i.get("line") or 0))
    return [f"{i['tool']} {i['code']} line {i['line']}: {i['msg'][:110]}" for i in found[:cap]]


def build(ix: Index, task: str, files: list[str] | None = None, budget: int = DEFAULT_BUDGET, symbol: str | None = None) -> str:
    from . import memory
    out: list[str] = []
    used = 0

    def add(block: str, force: bool = False) -> bool:
        nonlocal used
        cost = est(block) + 1
        if not force and used + cost > budget:
            return False
        out.append(block)
        used += cost
        return True

    rec = memory.recall(ix, task, files)
    rec["cross_repo"] = []                       # pointers only; `devmem reuse` has the real answer when it is needed
    text = memory.format_recall(rec)
    if text != "nothing relevant recorded":
        add(text, force=True)                    # rules and lessons are the part that must never be cut

    focus = ix.resolve(symbol) if symbol else []
    raw = ix.find(code_words(task), 40)
    hits = distinct(ix, raw, 6)
    if not focus and not hits:
        add('MATCHES: none. Try other words: `devmem find "<2-4 words>"`, or `devmem outline <file>`.', force=True)
        return "\n".join(out)
    score = {s["id"]: sc for sc, s in raw}
    # Keyword search cannot always tell which match the task means. Printing the body of a wrong guess is the most expensive
    # mistake here, so a body is shown only when one match is clearly ahead (or the agent named the symbol).
    clear = bool(focus) or len(hits) == 1 or score[hits[0][0]["id"]] >= 1.15 * score[hits[1][0]["id"]]
    if not clear:
        rows = []
        for s, tw in hits[:4]:
            callers = len({e["src"] for e in ix.inc.get(s["id"], []) if e["type"] == "calls"})
            tests = sum(1 for e in ix.inc.get(s["id"], []) if e["type"] == "tests")
            said = s.get("doc") or ix.desc.get(s["id"], "")
            rows.append(f" {s['id']}:{s['line']}  {(s.get('sig') or s['qualname'])[:110]}  [{callers} callers, {tests} tests]"
                        + (f"\n   {said[:110]}" if said else "") + (f"\n   (+{len(tw)} same name in other files)" if tw else ""))
        add("CANDIDATES (several fit; none is clearly the one)\n" + "\n".join(rows), force=True)
        more = [hit_line(ix, s, tw, 60) for s, tw in hits[4:]]
        if more:
            add("ALSO\n" + "\n".join(" " + m for m in more))
        add('NEXT: `devmem context "<task>" --symbol <path#name>` for the one you mean (body, callers, tests), or `devmem find` with other words.', force=True)
        return "\n".join(out)
    s = focus[0] if focus else hits[0][0]
    others = [h for h, _ in hits if h["id"] != s["id"]][:3]
    if others:   # one line: enough to notice the focus is the wrong one, and to name the right one with --symbol
        add("OTHER MATCHES: " + "; ".join(f"{h['id']}:{h['line']}" for h in others))

    imp = ix.impact(s["id"])
    tags = {i["id"]: i["conf"] for i in imp["hop1"] if i["conf"] in ("ambiguous", "co-change")}
    facts = [f"FOCUS {s['id']} (lines {s['line']}-{s.get('end_line', s['line'])})",
             " callers (must still work): " + grouped([i["id"] for i in imp["hop1"]], 8, tags)]
    if imp["hop2"]:
        facts.append(f" their callers: {len(imp['hop2'])} (`devmem impact {s['id']}`)")
    for c in imp["capped"]:
        facts.append(f" {c['symbol'].split('#')[-1]} has {c['callers']} callers: widely used, keep its signature")
    facts.append(" tests: " + (grouped(imp["tests"], 5) if imp["tests"] else "none reach it: add one, or run the suite"))
    issues = known_issues(ix, s)
    if issues:
        facts.append(" known issues here: " + " | ".join(issues))
    if imp.get("cochange"):
        facts.append(" usually changes with: " + ", ".join(imp["cochange"][:3]))
    add("\n".join(facts), force=True)

    try:
        body = ix.source(s).splitlines()
    except OSError:
        body = []
    if body:
        room = max(budget - used - 40, 0) * 4       # characters left for the body, keeping room for the last line
        keep, size = 0, 0
        for line in body:
            if size + len(line) + 1 > room and keep >= MIN_BODY_LINES:
                break
            size += len(line) + 1
            keep += 1
            if size > room and keep >= MIN_BODY_LINES:
                break
        shown = body[:keep]
        tail = f"\n… {len(body) - keep} more lines: `devmem show {s['id']} --full`" if keep < len(body) else ""
        add("BODY\n" + "\n".join(shown) + tail, force=True)
    add("NEXT: edit, `devmem check`, `devmem diffcheck`. Wrong symbol: add `--symbol <path#name>`.")
    return "\n".join(out)
