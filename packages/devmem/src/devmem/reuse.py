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

from .index import Index, _stem

CATALOG = ".devmem/reuse-catalog.json"
STDLIB = set(getattr(sys, "stdlib_module_names", ())) | {"__future__"}


def tokens(text: str) -> list[str]:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text or "")
    return [t.lower() for t in re.split(r"[^A-Za-z0-9]+", text) if len(t) > 1]


# words that say "I want to build something" or are plain grammar: they carry no information about WHICH code matches
STOP = {"how", "do", "does", "did", "an", "the", "to", "of", "for", "in", "on", "with", "and", "or", "is", "are", "it", "this", "that", "my",
        "we", "can", "should", "what", "which", "when", "where", "why", "use", "using", "from", "into", "be", "as", "at", "by", "our", "you",
        "your", "add", "create", "make", "implement", "build", "write", "support", "need", "want", "please", "new", "feature", "all", "any"}


def stem_tokens(text: str) -> list[str]:
    return [_stem(t) for t in tokens(text) if t not in STOP]


def _doc(e: dict) -> str:
    # name words are weighted by repetition: a function called `retry_with_backoff` is the strongest signal
    # `summary` is an optional plain-language description written at publish time (LLM): it lets offline keyword search
    # match a need phrased differently from the identifiers
    return f"{e['name']} {e['name']} {e['name']} {e.get('summary', '')} {e.get('summary', '')} {e.get('sig', '')} {e.get('doc', '')} {e['file']}"


def search(entries: list[dict], need: str, limit: int = 5) -> list[tuple[float, dict]]:
    """BM25 over the catalog. Scores are normalised so the best hit is 1.0."""
    q = set(stem_tokens(need))
    if not q or not entries:
        return []
    tfs = [Counter(stem_tokens(_doc(e))) for e in entries]
    lens = [sum(t.values()) or 1 for t in tfs]
    avg = sum(lens) / len(lens)
    df: Counter = Counter()
    for t in tfs:
        df.update(t.keys())
    n = len(entries)
    scored = []
    for e, tf, ln in zip(entries, tfs, lens):
        # evidence gate: a word of the need is in the symbol's NAME, or at least two words match elsewhere. A single generic word
        # in a docstring ("table", "return") is not enough, and BM25 alone would still rank such junk first (its top score is always 1.0)
        if not (q & set(stem_tokens(e["name"]))) and len(q & set(tf)) < 2:
            continue
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


def version_behind(spec: str, latest: str | None) -> str:
    """'major' | 'minor' | 'current' | 'unpinned' | 'unknown': declared version (any specifier) vs the latest release."""
    m = re.search(r"(\d+(?:\.\d+)*)", spec or "")
    if not latest:
        return "unknown"
    if not m:
        return "unpinned"
    v = lambda x: (tuple(int(t) for t in re.findall(r"\d+", x)[:3]) + (0, 0, 0))
    a, b = v(m.group(1)), v(latest)
    return "major" if b[0] > a[0] else "minor" if b[1] > a[1] else "current"


GENERIC = {"main", "init", "setup", "helper", "helpers", "util", "utils", "handler", "process", "parse", "build", "load", "save", "get", "run", "test", "data", "config"}


def modernize(entry: dict, root: Path | None, extra: list[Path] | None = None) -> list[str]:
    """Compare an org solution with the library docs on file: is it hand-rolling something a library now provides, or written
    against an older release than the docs describe? Needs docs (devmem-app docs add); returns nothing without them."""
    from . import docs
    libs = docs.libraries(root, extra)
    if not libs:
        return []
    norm = lambda n: re.sub(r"[-_.]+", "-", n).lower()
    by = {norm(m["name"]): m for m in libs}
    where = f"{entry['repo']}:{entry['file']}:{entry['line']}"
    out = []
    for lib in entry.get("ext_imports", []):
        m, spec = by.get(norm(lib)), (entry.get("ext_versions") or {}).get(lib, "")
        if m and m.get("version") and spec and version_behind(spec, m["version"]) in ("major", "minor"):
            out.append(f"{where} is written against {lib} {spec}, but the docs on file are for {m['version']}: newer releases may "
                       f"replace parts of this code (check `devmem docs --lib {m['name']}`)")
    name_t = {t for t in docs._toks(entry["name"].split(".")[-1]) if t not in GENERIC and len(t) >= 4}
    if name_t:
        for h in docs.search(root, f"{entry['name']} {entry.get('doc', '')}", 8, extra=extra):
            if h["cover"] < 0.3 or not (name_t & set(docs._toks(h["heading"] + " " + h["title"]))):
                continue
            uses = any(norm(l) == norm(h["lib"]) for l in entry.get("ext_imports", []))
            lib = f"{h['lib']} {h['version']}".strip()
            if uses:
                out.append(f"{where} uses {h['lib']} already, but {lib} now documents a built-in \"{h['heading']}\" ({h['url']}): the "
                           "hand-written part can probably go")
            else:
                out.append(f"{where} hand-rolls this: {lib} documents a built-in \"{h['heading']}\" ({h['url']}). Prefer the built-in and say so in the PR")
            if len(out) >= 3:
                break
    return out


def target_imports(ix: Index) -> set[str]:
    return {_top(i["module"]) for f in ix.data["files"].values() for i in f.get("imports", []) if i.get("module") and not i["module"].startswith(".")}


def fit(entry: dict, ix: Index, extra_docs: list[Path] | None = None) -> dict:
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
    modern = modernize(entry, ix.root, extra_docs)
    effort = "low" if points <= 1 else "medium" if points <= 4 else "high"
    return {"effort": effort, "points": points, "notes": notes, "modernize": modern, "missing_deps": missing, "clash": [s["id"] for s in clash],
            "existing": [s["id"] for s in existing], "lang_ok": lang_ok}


def render_card(score: float, e: dict, f: dict, code_lines: int = 40) -> str:
    out = [f"{e['repo']}:{e['file']}:{e['line']}  {e['sig'] or e['name']}   [match {score:.2f} · effort to reuse: {f['effort']}]"]
    if e.get("doc"):
        out.append(f"  doc: {e['doc']}")
    for n in f.get("modernize", []):
        out.append(f"  ~ MODERNIZE: {n}")
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
    if f.get("modernize"):
        steps.append("Before copying, prefer the library built-ins (the plan wins if it says otherwise):")
        steps += [f"- {n}" for n in f["modernize"]]
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


def reuse(ix: Index, need: str, entries: list[dict] | None = None, limit: int = 3, extra_docs: list[Path] | None = None) -> list[dict]:
    entries = all_entries(ix.root)[0] if entries is None else entries
    entries = [e for e in entries if e["repo"] != ix.data.get("repo")]
    return [{"score": s, "entry": e, "fit": fit(e, ix, extra_docs)} for s, e in search(entries, need, limit)]
