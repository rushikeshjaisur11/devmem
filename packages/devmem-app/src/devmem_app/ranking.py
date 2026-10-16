"""Aider-style repo map: personalized PageRank over the call graph, then fit signatures to a token budget."""
from __future__ import annotations

from collections import defaultdict

from devmem.knowledge import MAP_BUDGET, est_tokens

CONF_W = {"precise": 1.0, "structural": 1.0, "ambiguous": 0.5, "co-change": 0.3}


def pagerank(idx: dict, personalize: set[str] | None = None, d: float = 0.85, iters: int = 40) -> dict[str, float]:
    nodes = [s["id"] for s in idx["symbols"] if s["kind"] != "module" and not s["is_test"]]
    if not nodes:
        return {}
    ns = set(nodes)
    out = defaultdict(list)
    for e in idx["edges"]:
        if e["type"] == "calls" and e["src"] in ns and e["dst"] in ns:
            out[e["src"]].append((e["dst"], CONF_W.get(e["conf"], 0.5)))
    n = len(nodes)
    if personalize:
        p = {k: (1.0 / len(personalize) if k in personalize else 0.0) for k in nodes}
    else:
        p = {k: 1.0 / n for k in nodes}
    r = dict(p)
    for _ in range(iters):
        nr = {k: (1 - d) * p[k] for k in nodes}
        dangling = 0.0
        for k in nodes:
            targets = out.get(k)
            if not targets:
                dangling += r[k]
                continue
            tot = sum(w for _, w in targets)
            for t, w in targets:
                nr[t] += d * r[k] * w / tot
        for k in nodes:  # dangling mass follows the personalization vector
            nr[k] += d * dangling * p[k]
        r = nr
    return r


def render_map(idx: dict, budget: int = MAP_BUDGET, personalize: set[str] | None = None) -> tuple[str, list[str]]:
    """Return (markdown, symbol ids included). Binary search for the most symbols that fit the budget."""
    rank = pagerank(idx, personalize)
    by_id = {s["id"]: s for s in idx["symbols"]}
    order = sorted(rank, key=lambda k: (-rank[k], k))

    def render(k: int) -> str:
        chosen = [by_id[i] for i in order[:k]]
        chosen_ids = {s["id"] for s in chosen}
        # a method needs its class line for context
        for s in list(chosen):
            if s["kind"] == "method":
                cls = by_id.get(f"{s['file']}#{s['qualname'].split('.')[0]}")
                if cls and cls["id"] not in chosen_ids:
                    chosen.append(cls); chosen_ids.add(cls["id"])
        files = defaultdict(list)
        for s in chosen:
            files[s["file"]].append(s)
        lines = []
        for f in sorted(files):
            lines.append(f"{f}:")
            for s in sorted(files[f], key=lambda s: s["line"]):
                lines.append(f"{'    ' if s['kind'] == 'method' else '  '}{s['line']}: {s['sig']}")
        return "\n".join(lines)

    lo, hi, best = 0, len(order), 0
    while lo <= hi:
        mid = (lo + hi) // 2
        if est_tokens(render(mid)) <= budget:
            best, lo = mid, mid + 1
        else:
            hi = mid - 1
    return render(best), order[:best]
