"""How far to trust a repo's index, measured on that repo: a number per repo instead of the one benchmark in docs/ACCURACY.md.

  edges   how much of the call graph is independently confirmed: type-resolved by SCIP ("precise"), found by Graphify as
          well ("confirmed"), resolved by imports only ("structural"), or a guess between same-named candidates ("ambiguous");
          per language, because Go/Java/C#/Kotlin/Rust are name-resolved unless their SCIP indexer is installed.
  sites   a sample of call edges re-checked against the code as it is now: does the calling function still mention the
          callee? Catches stale or wrong edges.
  search  can `find` and `context` locate a function from a description of it? Queries are each documented function's
          docstring with the function's own name words removed (so the name does not give it away), plus any pairs you
          write in `.devmem/search-eval.txt` as `query => path#qualname`. Reported as top-1 / top-5 hit rates.
Results go to `.devmem/selfcheck.json`, keeping the last 30 runs so a drop after a change is visible."""
from __future__ import annotations

import datetime as dt
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

from devmem import context as ctx_mod
from devmem.index import Index, _tokens
from devmem.schema import atomic_write

PATH = ".devmem/selfcheck.json"
EVAL_PATH = ".devmem/search-eval.txt"
NAME_RESOLVED = {"go", "java", "csharp", "kotlin", "rust"}


def _graphify_pairs(root: Path) -> set[tuple[str, str]]:
    try:
        c = json.loads((root / ".devmem/graphify-edges.json").read_text(encoding="utf-8"))
        return {(e["src"], e["dst"]) for e in c.get("edges", [])}
    except (OSError, ValueError):
        return set()


def edge_report(ix: Index) -> dict:
    gp = _graphify_pairs(Path(ix.root))
    by_lang: dict[str, Counter] = defaultdict(Counter)
    total = Counter()
    for e in ix.data["edges"]:
        if e["type"] not in ("calls", "tests") or e["dst"].startswith("table:") or e["src"] not in ix.symbols:
            continue
        lang = (ix.data["files"].get(ix.symbols[e["src"]]["file"]) or {}).get("lang", "other")
        kind = ("precise" if e["conf"] == "precise" else "confirmed" if (e["src"], e["dst"]) in gp
                else "ambiguous" if e["conf"] == "ambiguous" else "structural")
        by_lang[lang][kind] += 1
        total[kind] += 1
    n = sum(total.values()) or 1
    langs = {}
    for lang, c in sorted(by_lang.items(), key=lambda kv: -sum(kv[1].values())):
        m = sum(c.values())
        langs[lang] = {"edges": m, **{k: c.get(k, 0) for k in ("precise", "confirmed", "structural", "ambiguous")},
                       "independent": round((c.get("precise", 0) + c.get("confirmed", 0)) / m, 3),
                       "name_resolved": lang in NAME_RESOLVED and not c.get("precise")}
    return {"edges": sum(total.values()), **{k: total.get(k, 0) for k in ("precise", "confirmed", "structural", "ambiguous")},
            "independent": round((total.get("precise", 0) + total.get("confirmed", 0)) / n, 3), "ambiguous_share": round(total.get("ambiguous", 0) / n, 3),
            "languages": langs}


def site_check(ix: Index, sample: int = 300, seed: int = 7) -> dict:
    edges = [e for e in ix.data["edges"] if e["type"] in ("calls", "tests") and e["src"] in ix.symbols and e["dst"] in ix.symbols
             and ix.symbols[e["src"]]["kind"] != "module"]
    random.Random(seed).shuffle(edges)
    ok, missing, changed, examples = 0, 0, 0, []
    for e in edges[:sample]:
        src, dst = ix.symbols[e["src"]], ix.symbols[e["dst"]]
        try:
            text = ix.read(src)[0]
        except LookupError:
            changed += 1
            continue
        name = dst["name"]
        if name in ("__init__", "constructor"):
            name = dst["qualname"].split(".")[0]          # a constructor is called by its class name
        names = {name} | {i["alias"] for i in (ix.data["files"].get(src["file"]) or {}).get("imports", [])
                          if i.get("name") == name and i.get("alias")}      # `from x import Cache as LRU`
        if any(re.search(rf"(?<![\w$]){re.escape(n)}(?![\w$])", text) for n in names):
            ok += 1
        else:
            missing += 1
            if len(examples) < 5:
                examples.append(f"{e['src']} -> {e['dst']}")
    checked = ok + missing
    return {"checked": checked, "present": ok, "missing": missing, "unreadable": changed,
            "rate": round(ok / checked, 3) if checked else None, "examples": examples}


def _queries(ix: Index, limit: int) -> list[tuple[str, str, str]]:
    """(query, expected id, origin). A docstring minus the function's own name words, so the answer is not in the question."""
    out = []
    p = Path(ix.root) / EVAL_PATH
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            if "=>" in ln and not ln.lstrip().startswith("#"):
                q, _, want = ln.partition("=>")
                if want.strip() in ix.symbols:
                    out.append((q.strip(), want.strip(), "yours"))
    cands = [s for s in ix.symbols.values() if s["kind"] in ("function", "method") and not s.get("is_test") and len((s.get("doc") or "").split()) >= 5]
    random.Random(11).shuffle(cands)
    for s in cands:
        if len(out) >= limit:
            break
        own = _tokens(s["qualname"])
        first = re.split(r"(?<=[.!?])\s|\n\n", s["doc"].strip())[0]
        words = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9]*", first) if not (_tokens(w) & own)]
        if len(words) >= 4:
            out.append((" ".join(words[:14]), s["id"], "docstring"))
    return out


def search_eval(ix: Index, limit: int = 150) -> dict:
    qs = _queries(ix, limit)
    if not qs:
        return {"queries": 0}
    top1 = top5 = focus = 0
    misses = []
    for q, want, _ in qs:
        ids = [s["id"] for s, _tw in ctx_mod.distinct(ix, ix.find(q, 40), 5)]
        twins = {t["id"] for s, tw in ctx_mod.distinct(ix, ix.find(q, 40), 5) for t in tw}
        hit = want in ids or want in twins
        top1 += bool(ids and (ids[0] == want))
        top5 += hit
        out = ctx_mod.build(ix, q, budget=400)
        focus += f"FOCUS {want} " in out
        if not hit and len(misses) < 8:
            misses.append({"query": q, "expected": want, "got": ids[:3]})
    n = len(qs)
    return {"queries": n, "yours": sum(1 for x in qs if x[2] == "yours"), "top1": round(top1 / n, 3), "top5": round(top5 / n, 3),
            "context_focus": round(focus / n, 3), "misses": misses}


def run(ix: Index) -> dict:
    res = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "commit": ix.data.get("commit"),
           "accuracy_mode": (ix.data.get("stats") or {}).get("accuracy"), "edges": edge_report(ix), "sites": site_check(ix), "search": search_eval(ix)}
    p = Path(ix.root) / PATH
    try:
        hist = json.loads(p.read_text(encoding="utf-8")).get("history", [])
    except (OSError, ValueError):
        hist = []
    hist = (hist + [{"ts": res["ts"], "independent": res["edges"]["independent"], "sites": res["sites"]["rate"], "top5": res["search"].get("top5"),
                     "focus": res["search"].get("context_focus")}])[-30:]
    atomic_write(p, json.dumps({**res, "history": hist}, indent=1))
    return {**res, "history": hist}


def load(root: Path) -> dict | None:
    try:
        return json.loads((Path(root) / PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
