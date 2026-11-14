"""Definitions devmem is unsure about: code whose name and docstring say too little for search to find it by what it does.

Indexing never needs an LLM, so an undocumented `run()` or `handle(x)` is findable only by its name. This module rates
how well each function, method and class is described (no AI involved), lists the weakest ones most-used first, and
stores a plain-language description for each that a person writes or an LLM drafts. Descriptions live in
`.devmem/descriptions.json`, are reviewed and committed like the rest of `.devmem/`, and are searched like docstrings
by `devmem find`, Ask & plan and reuse. A description records a hash of the code it was written for, so it is flagged
when that code changes."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from devmem.index import Index, write_db
from devmem.schema import DESC_PATH
from devmem.schema import atomic_write

# names that say what kind of thing happens but not what it is done to
GENERIC = {"get", "set", "run", "do", "go", "main", "handle", "handler", "process", "execute", "exec", "call", "apply", "invoke",
           "helper", "helpers", "util", "utils", "common", "misc", "data", "info", "item", "items", "obj", "object", "value", "values",
           "result", "results", "manager", "service", "base", "impl", "wrapper", "inner", "func", "fn", "cb", "callback", "tmp", "temp",
           "foo", "bar", "test", "check", "update", "init", "setup", "start", "stop", "load", "save", "make", "build", "create", "new",
           "to", "from", "of", "for", "with", "and", "the", "is", "on", "in", "by", "x", "y", "a", "b"}
LEVELS = ("low", "medium", "high")
TEST_FILE = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)(conftest|test_[^/]*)\.py$|[._](test|spec)\.[jt]sx?$|_test\.go$")
MAX_TEXT = 300


def _words(name: str) -> list[str]:
    return [w.lower() for w in re.split(r"[^A-Za-z0-9]+", re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)) if w]


def code_hash(ix: Index, s: dict) -> str:
    try:
        return hashlib.sha1(ix.source(s).encode("utf-8", "replace")).hexdigest()[:16]
    except OSError:
        return ""


def load(root: Path) -> dict:
    try:
        d = json.loads((Path(root) / DESC_PATH).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save(ix: Index, entries: dict) -> None:
    """Write the file (sorted, so diffs stay small) and refresh the search cache so the new text is searchable at once."""
    p = Path(ix.root) / DESC_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(p, json.dumps(entries, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    ix.desc = {sid: d["text"] for sid, d in entries.items() if sid in ix.symbols and d.get("text")}
    try:
        write_db(ix)
    except Exception:
        pass   # the cache is optional: search rebuilds the same tables in memory when it is stale


def rate(s: dict, described: dict | None, stale: bool) -> tuple[str, list[str]]:
    """(level, reasons). High: search has a sentence about what the code does. Medium: only a telling name or a very short
    docstring. Low: no docstring and a name that does not say what the code does."""
    if described and not stale:
        return "high", []
    doc_words = len(re.findall(r"[A-Za-z]{2,}", s.get("doc") or ""))
    telling = [w for w in _words(s["name"]) if w not in GENERIC and len(w) > 2 and not w.isdigit()]
    reasons = []
    if stale:
        reasons.append("its description was written for an older version of this code")
    if doc_words >= 4:
        return ("medium" if stale else "high"), reasons
    reasons.append("no docstring" if doc_words == 0 else f"docstring is only {doc_words} word{'s' if doc_words != 1 else ''}")
    if len(telling) >= 2:
        return "medium", reasons + ["the name is the only description"]
    if doc_words:
        return "medium", reasons
    reasons.append(f"the name “{s['name']}” does not say what it does" if not telling else "the name is one general word")
    return "low", reasons


def review(ix: Index, level: str = "low", limit: int = 100, query: str = "") -> dict:
    """Symbols rated at or below `level`, the ones most code depends on first; level "described" lists the ones that have a
    written description instead (to review what a model drafted). Tests and dunder methods are left out: nobody searches
    for those by meaning."""
    entries = load(ix.root)
    want = LEVELS.index(level) if level in LEVELS else 0
    q = query.strip().lower()
    counts = {k: 0 for k in LEVELS}
    rows = []
    described = 0
    for s in ix.symbols.values():
        if s["kind"] == "module" or s.get("is_test") or re.fullmatch(r"__\w+__", s["name"]) or TEST_FILE.search(s["file"]):
            continue
        d = entries.get(s["id"])
        stale = bool(d and d.get("code") and d["code"] != code_hash(ix, s))
        lvl, reasons = rate(s, d, stale)
        counts[lvl] += 1
        described += bool(d)
        if (not d) if level == "described" else LEVELS.index(lvl) > want:
            continue
        if q and q not in s["id"].lower():
            continue
        callers = len({e["src"] for e in ix.inc.get(s["id"], []) if e["type"] == "calls"})
        rows.append({"id": s["id"], "name": s["qualname"], "kind": s["kind"], "file": s["file"], "line": s["line"], "sig": s.get("sig") or "",
                     "doc": s.get("doc") or "", "level": lvl, "reasons": reasons, "callers": callers,
                     "description": (d or {}).get("text", ""), "by": (d or {}).get("by", ""), "stale": stale})
    rows.sort(key=lambda r: ((r["by"] != "ai", not r["stale"]) if level == "described" else (LEVELS.index(r["level"]),),
                             -r["callers"], r["name"].startswith("_"), r["id"]))
    return {"counts": counts, "described": described, "total": sum(counts.values()), "matching": len(rows), "items": rows[:limit]}


def set_description(ix: Index, sid: str, text: str, by: str = "human") -> dict:
    if sid not in ix.symbols:
        raise KeyError(sid)
    entries = load(ix.root)
    text = " ".join(text.split())[:MAX_TEXT]
    if text:
        entries[sid] = {"text": text, "by": by, "code": code_hash(ix, ix.symbols[sid])}
    else:
        entries.pop(sid, None)
    save(ix, entries)
    return entries.get(sid, {})


def prune(ix: Index) -> int:
    """Drop descriptions of symbols that no longer exist (renamed or deleted code)."""
    entries = load(ix.root)
    gone = [k for k in entries if k not in ix.symbols]
    if gone:
        for k in gone:
            del entries[k]
        save(ix, entries)
    return len(gone)


def _context(ix: Index, s: dict, chars: int = 900) -> str:
    try:
        code = ix.source(s)[:chars]
    except OSError:
        code = s.get("sig") or ""
    callers = sorted({e["src"].split("#")[-1] for e in ix.inc.get(s["id"], []) if e["type"] == "calls"})[:5]
    callees = [c["qualname"] for c in ix.callees(s)][:6]
    tables = sorted({e["dst"][6:] for e in ix.out.get(s["id"], []) if e["dst"].startswith("table:")})[:5]
    extra = "".join(f"\n{k}: {', '.join(v)}" for k, v in (("called by", callers), ("calls", callees), ("tables", tables)) if v)
    return f"{s['file']} :: {s['qualname']}{extra}\n{code}"


def describe_with_ai(ix: Index, ids: list[str], provider, batch: int = 8) -> dict:
    """Ask the LLM for one sentence per symbol. Sends the symbol's source (first ~900 characters) and the names of what
    calls it and what it calls. Returns {written: {id: text}, failed: [ids]}; nothing is guessed for a failed batch."""
    todo = [ix.symbols[i] for i in dict.fromkeys(ids) if i in ix.symbols]
    entries = load(ix.root)
    written, failed, error = {}, [], ""
    for n in range(0, len(todo), batch):
        chunk = todo[n:n + batch]
        prompt = ("You document code for a search index. For each item below write ONE plain sentence (max 25 words) saying what "
                  "it does and why a developer would call it, then add up to 6 search words a person might use instead of the "
                  "identifiers (synonyms, the problem it solves). Describe only what the code shows; if it is unclear, say what "
                  "is visible rather than guessing. Reply with ONLY a JSON list: [{\"i\": 0, \"text\": \"sentence. Words: a, b, c\"}, ...]\n\n"
                  + "\n\n".join(f"### {i}\n{_context(ix, s)}" for i, s in enumerate(chunk)))
        try:
            reply = provider.generate(prompt).strip()
            got = json.loads(reply[reply.index("["): reply.rindex("]") + 1])
            seen = set()
            for item in got:
                s = chunk[int(item["i"])]
                text = " ".join(str(item["text"]).split())[:MAX_TEXT]
                if text:
                    entries[s["id"]] = {"text": text, "by": "ai", "code": code_hash(ix, s)}
                    written[s["id"]] = text
                    seen.add(s["id"])
            failed += [s["id"] for s in chunk if s["id"] not in seen]
        except Exception as e:   # a bad reply or a network error leaves this batch undescribed
            failed += [s["id"] for s in chunk]
            error = str(e)[:300]
    if written:
        save(ix, entries)
    return {"written": written, "failed": failed, "error": error}
