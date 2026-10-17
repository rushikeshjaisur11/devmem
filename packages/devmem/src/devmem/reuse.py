"""Cross-repo reuse: "another repo already built this, how much work is it to bring it here?"

Pure stdlib, offline. A catalog of entries (one per reusable symbol in the *other* onboarded repos) is committed to
`.devmem/reuse-catalog.json` by `devmem-app publish`; `search` ranks entries for a described need and `fit` checks one
against THIS repo's index: name clashes, third-party imports this repo does not use yet, tables it would touch,
language mismatch, and code this repo already has that does something similar."""
from __future__ import annotations

import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

from .index import Index

CATALOG = ".devmem/reuse-catalog.json"
STDLIB = set(getattr(sys, "stdlib_module_names", ())) | {"__future__"}


def tokens(text: str) -> list[str]:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text or "")
    return [t.lower() for t in re.split(r"[^A-Za-z0-9]+", text) if len(t) > 1]


def _doc(e: dict) -> str:
    # name words are weighted by repetition: a function called `retry_with_backoff` is the strongest signal
    # `summary` is an optional plain-language description written at publish time (LLM): it lets offline keyword search
    # match a need phrased differently from the identifiers
    return f"{e['name']} {e['name']} {e['name']} {e.get('summary', '')} {e.get('summary', '')} {e.get('sig', '')} {e.get('doc', '')} {e['file']}"


def search(entries: list[dict], need: str, limit: int = 5) -> list[tuple[float, dict]]:
    """BM25 over the catalog. Scores are normalised so the best hit is 1.0."""
    q = set(tokens(need))
    if not q or not entries:
        return []
    tfs = [Counter(tokens(_doc(e))) for e in entries]
    lens = [sum(t.values()) or 1 for t in tfs]
    avg = sum(lens) / len(lens)
    df: Counter = Counter()
    for t in tfs:
        df.update(t.keys())
    n = len(entries)
    scored = []
    for e, tf, ln in zip(entries, tfs, lens):
        s = 0.0
        for t in q:
            f = tf.get(t, 0)
            if f:
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * ln / avg))
        if s > 0:
            scored.append((s, e))
    scored.sort(key=lambda x: (-x[0], x[1]["repo"], x[1]["file"], x[1]["line"]))
    top = scored[0][0] if scored else 1.0
    return [(round(s / top, 3), e) for s, e in scored[:limit]]


def load_catalog(root: Path) -> list[dict]:
    p = Path(root) / CATALOG
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def _top(mod: str) -> str:
    return mod.split(".")[0].split("/")[0]


def target_imports(ix: Index) -> set[str]:
    return {_top(i["module"]) for f in ix.data["files"].values() for i in f.get("imports", []) if i.get("module") and not i["module"].startswith(".")}


def fit(entry: dict, ix: Index) -> dict:
    """How well does `entry` (from another repo) fit into the repo behind `ix`?"""
    notes, points = [], 0
    langs = Counter(f["lang"] for f in ix.data["files"].values() if f["lang"] != "other")
    lang_ok = entry.get("lang") in langs or not langs
    if not lang_ok:
        points += 5
        notes.append(f"language mismatch: source is {entry.get('lang')}, this repo is {', '.join(langs) or 'empty'}; treat it as a spec to port")
    have = target_imports(ix)
    missing = sorted(m for m in entry.get("ext_imports", []) if _top(m) not in have and _top(m) not in STDLIB)
    if missing:
        points += 2 * len(missing)
        notes.append("third-party packages not used in this repo yet (add to its dependencies): " + ", ".join(missing))
    clash = [s for s in ix.symbols.values() if s["kind"] != "module" and not s["is_test"] and s["name"] == entry["name"].split(".")[-1]]
    if clash:
        points += 1
        notes.append("a symbol with this name already exists here (rename on copy): " + ", ".join(f"{s['file']}:{s['line']}" for s in clash[:3]))
    same = [d for d in entry.get("deps", []) if d.get("same_file")]
    if same:
        notes.append("copy the helpers it calls in the same file too: " + ", ".join(d["name"] for d in same[:6]))
    outside = [d for d in entry.get("deps", []) if not d.get("same_file")]
    if outside:
        points += len(outside)
        notes.append("also copy or re-implement what it calls in other files: " + ", ".join(d["name"] for d in outside[:6]))
    if entry.get("tables"):
        points += 2
        notes.append("touches tables " + ", ".join(entry["tables"][:5]) + ": confirm they exist in this repo's data (or pass them in)")
    # only a name-level match counts: doc-word overlap alone ("table", "return") is mostly noise
    existing = [s for sc, s in ix.find(entry["name"].split(".")[-1].replace("_", " "), 3) if sc >= 3 and not s["is_test"]]
    if existing:
        notes.append("this repo already has something similar, maybe extend it instead: " + ", ".join(f"{s['file']}:{s['line']} {s['qualname']}" for s in existing[:2]))
    effort = "low" if points <= 1 else "medium" if points <= 4 else "high"
    return {"effort": effort, "points": points, "notes": notes, "missing_deps": missing, "clash": [s["id"] for s in clash],
            "existing": [s["id"] for s in existing], "lang_ok": lang_ok}


def render_card(score: float, e: dict, f: dict, code_lines: int = 40) -> str:
    out = [f"{e['repo']}:{e['file']}:{e['line']}  {e['sig'] or e['name']}   [match {score:.2f} · effort to reuse: {f['effort']}]"]
    if e.get("doc"):
        out.append(f"  doc: {e['doc']}")
    for n in f["notes"]:
        out.append(f"  ! {n}")
    if e.get("tests"):
        out.append("  usage in its tests: " + ", ".join(f"{t['file']}:{t['line']} {t['name']}" for t in e["tests"][:3]))
    if e.get("code"):
        body = e["code"].splitlines()
        out.append(f"  --- source ({len(body)} lines{', truncated' if e.get('truncated') else ''}) ---")
        out += ["  " + ln for ln in body[:code_lines]]
    return "\n".join(out)


def port_prompt(e: dict, f: dict, target: str) -> str:
    """A ready-to-paste instruction for an agent working in the target repo."""
    steps = [f"Reuse `{e['name']}` from repo `{e['repo']}` ({e['file']}:{e['line']}) in `{target}` with the smallest possible change.",
             "Copy it (keep a one-line comment naming the origin), follow this repo's conventions for logging, config and errors, and keep the diff minimal."]
    steps += [f"- {n}" for n in f["notes"]]
    if e.get("tests"):
        steps.append("- Port or adapt one of its tests: " + ", ".join(t["name"] for t in e["tests"][:2]))
    steps.append("Run `devmem find`/`devmem impact` for any existing code you touch, then `devmem check` and `devmem diffcheck`.")
    if e.get("code"):
        steps += ["", "Source to start from:", "```", e["code"], "```"]
    return "\n".join(steps)


def all_entries(root: Path, auto_sync: bool = True) -> tuple[list[dict], str]:
    """The catalog committed in the repo plus the central hub's catalogs (hub wins on duplicates)."""
    from . import hub
    merged = {(e["repo"], e["id"]): e for e in load_catalog(root)}
    d, note = hub.resolve(root, auto_sync)
    if d:
        merged.update({(e["repo"], e["id"]): e for e in hub.entries(d)})
    return list(merged.values()), note


def reuse(ix: Index, need: str, entries: list[dict] | None = None, limit: int = 3) -> list[dict]:
    entries = all_entries(ix.root)[0] if entries is None else entries
    entries = [e for e in entries if e["repo"] != ix.data.get("repo")]
    return [{"score": s, "entry": e, "fit": fit(e, ix)} for s, e in search(entries, need, limit)]
