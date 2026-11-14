"""AI analysis: one optional pass over a repo that writes what parsers cannot, the way Graphify's first run does.

The index (symbols, calls, tests, tables) is built without AI and stays that way. This pass adds meaning on top of it:
  1. describe   one sentence for each definition whose name and docstring say too little (see definitions.py);
  2. modules    two sentences on what each top-level module is responsible for;
  3. overview   a short paragraph on what the repo does and how its parts fit.
Everything is cached against a hash of what it was written from, so a second run only sends what is new or changed, and
every text can be corrected by hand afterwards. Results: `.devmem/descriptions.json` and `.devmem/analysis.json`, both
committed with the rest of `.devmem/`; publish puts them into overview.md and the module cards, and search uses them."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import threading
from collections import Counter, defaultdict
from pathlib import Path

from devmem.index import Index

from . import definitions as defs
from .publisher import _module_of
from devmem.schema import atomic_write

PATH = ".devmem/analysis.json"
STEPS = ("describe", "modules", "overview")
MODULE_CHARS, OVERVIEW_CHARS = 320, 700
BATCH = 8


def load(root: Path) -> dict:
    try:
        d = json.loads((Path(root) / PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    if not isinstance(d, dict):
        d = {}
    d.setdefault("modules", {})
    return d


def save(root: Path, data: dict) -> None:
    p = Path(root) / PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(p, json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n")


def _h(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:16]


def _est(text: str) -> int:
    return len(text) // 4


# ---------- what each step would send ----------
def weak_symbols(ix: Index, limit: int | None) -> list[dict]:
    """Definitions rated low or medium that have no current description, most used first."""
    rows = [r for r in defs.review(ix, "medium", limit=10 ** 9)["items"] if not r["description"] or r["stale"]]
    return rows[:limit] if limit else rows


class _Input(str):
    """The text sent for a module; `.key` identifies the code it was built from. Docstrings and descriptions are part of the
    text but not of the key, so correcting a description does not mark every module summary as out of date."""
    key = ""


def module_inputs(ix: Index) -> dict[str, str]:
    """module -> the text a summary is written from: its files, its most-called definitions and its neighbours.
    Test folders are left out: nobody needs a summary of them."""
    mods, files = defaultdict(list), defaultdict(set)
    for s in ix.symbols.values():
        if s["kind"] != "module" and not s.get("is_test") and not defs.TEST_FILE.search(s["file"]):
            mods[_module_of(s["file"])].append(s)
    for path in ix.data["files"]:
        files[_module_of(path)].add(path)
    out_to, in_from = defaultdict(Counter), defaultdict(Counter)
    for e in ix.data["edges"]:
        a, b = ix.symbols.get(e["src"]), ix.symbols.get(e["dst"])
        if e["type"] == "calls" and a and b:
            ma, mb = _module_of(a["file"]), _module_of(b["file"])
            if ma != mb:
                out_to[ma][mb] += 1
                in_from[mb][ma] += 1
    res = {}
    for m, syms in sorted(mods.items()):
        top = sorted(syms, key=lambda s: (-len(ix.inc.get(s["id"], [])), s["id"]))[:14]
        head = [f"module: {m}", "files: " + ", ".join(sorted(files[m])[:15])]
        sigs = [f"- {s.get('sig') or s['qualname']}" for s in top]
        said = [(f"  # {(s.get('doc') or ix.desc.get(s['id'], ''))[:120]}" if s.get("doc") or s["id"] in ix.desc else "") for s in top]
        tail = []
        if out_to[m]:
            tail.append("calls into: " + ", ".join(x for x, _ in out_to[m].most_common(6)))
        if in_from[m]:
            tail.append("called from: " + ", ".join(x for x, _ in in_from[m].most_common(6)))
        res[m] = _Input("\n".join(head + [a + b for a, b in zip(sigs, said)] + tail))
        res[m].key = _h("\n".join(head + sigs + tail))
    return res


def overview_input(ix: Index, modules: dict[str, dict]) -> str:
    langs = Counter(f["lang"] for f in ix.data["files"].values() if f["lang"] != "other")
    tables = Counter(e["dst"][6:] for e in ix.data["edges"] if e["dst"].startswith("table:"))
    mains = [s["id"] for s in ix.symbols.values() if s["name"] == "main" and s["kind"] == "function"][:5]
    lines = [f"repository: {ix.data.get('repo', '')}", "languages: " + ", ".join(f"{l} {n} files" for l, n in langs.most_common())]
    if mains:
        lines.append("entry points: " + ", ".join(mains))
    if tables:
        lines.append("tables: " + ", ".join(t for t, _ in tables.most_common(12)))
    lines.append("modules:")
    names = sorted({_module_of(s["file"]) for s in ix.symbols.values()
                    if s["kind"] != "module" and not s.get("is_test") and not defs.TEST_FILE.search(s["file"])})
    lines += [f"- {m}: {(modules.get(m) or {}).get('text', '(no summary yet)')}" for m in names]
    return "\n".join(lines)


def status(ix: Index, limit: int | None = 200) -> dict:
    """What has been analysed, what a run would do now, and roughly what it would send."""
    data = load(ix.root)
    rv = defs.review(ix, "medium", limit=10 ** 9)
    weak_all = [r for r in rv["items"] if not r["description"] or r["stale"]]
    weak = weak_all[:limit] if limit else weak_all
    inputs = module_inputs(ix)
    mods = [{"name": m, "text": (data["modules"].get(m) or {}).get("text", ""), "by": (data["modules"].get(m) or {}).get("by", ""),
             "stale": bool(data["modules"].get(m)) and data["modules"][m].get("code") != inp.key} for m, inp in inputs.items()]
    todo_mods = [m for m in mods if not m["text"] or (m["stale"] and m["by"] != "human")]
    ov = data.get("overview") or {}
    ov_in = overview_input(ix, data["modules"])
    ov_stale = bool(ov) and ov.get("code") != _h(ov_in)
    send_desc = sum(_est(defs._context(ix, ix.symbols[r["id"]])) for r in weak[:400]) * (len(weak) / max(1, min(len(weak), 400)))
    return {
        "describe": {"weak": len(weak_all), "todo": len(weak), "described": rv["described"], "total": rv["total"],
                     "stale": sum(1 for r in rv["items"] if r["stale"]), "requests": -(-len(weak) // BATCH), "tokens": int(send_desc)},
        "modules": {"total": len(mods), "written": sum(1 for m in mods if m["text"]), "todo": len(todo_mods), "items": mods,
                    "requests": -(-len(todo_mods) // BATCH), "tokens": sum(_est(inputs[m["name"]]) for m in todo_mods)},
        "overview": {"text": ov.get("text", ""), "by": ov.get("by", ""), "stale": ov_stale,
                     "todo": int(not ov or (ov_stale and ov.get("by") != "human")), "requests": 1, "tokens": _est(ov_in)},
        "last_run": data.get("last_run"),
    }


# ---------- the run ----------
class Job:
    """Progress of one run, read by the UI while the worker thread fills it in."""
    def __init__(self, steps: list[str]):
        self.steps, self.state, self.step = steps, "running", ""
        self.done = self.total = 0
        self.written = {"describe": 0, "modules": 0, "overview": 0}
        self.failed, self.error, self.model = 0, "", ""
        self.cancel = threading.Event()

    def public(self) -> dict:
        return {"state": self.state, "step": self.step, "steps": self.steps, "done": self.done, "total": self.total,
                "written": self.written, "failed": self.failed, "error": self.error, "model": self.model}


def _json_list(reply: str) -> list:
    return json.loads(reply[reply.index("["): reply.rindex("]") + 1])


def run(ix: Index, provider, steps: list[str], limit: int | None, job: Job) -> Job:
    job.model = getattr(provider, "name", "")
    weak = weak_symbols(ix, limit) if "describe" in steps else []
    inputs = module_inputs(ix) if "modules" in steps else {}
    data = load(ix.root)
    todo_mods = [m for m, inp in inputs.items()
                 if m not in data["modules"] or (data["modules"][m].get("code") != inp.key and data["modules"][m].get("by") != "human")]
    job.total = len(weak) + len(todo_mods) + ("overview" in steps)
    try:
        job.step = "describe"
        for n in range(0, len(weak), BATCH):
            if job.cancel.is_set():
                break
            ids = [r["id"] for r in weak[n:n + BATCH]]
            out = defs.describe_with_ai(ix, ids, provider, BATCH)
            job.written["describe"] += len(out["written"])
            job.failed += len(out["failed"])
            job.error = out["error"] or job.error
            job.done += len(ids)
            if out["error"] and not out["written"] and n == 0:
                raise RuntimeError(out["error"])    # the very first request failed: the model is unreachable, stop here

        job.step = "modules"
        if todo_mods:
            inputs = module_inputs(ix)   # again: the summaries should read the descriptions the first step just wrote
        for n in range(0, len(todo_mods), BATCH):
            if job.cancel.is_set():
                break
            chunk = todo_mods[n:n + BATCH]
            prompt = ("Below are modules of one code repository, each with its files, its most used definitions and the modules it "
                      "talks to. For each, write at most two plain sentences on what the module is responsible for and when a "
                      "developer would change it. Use only what is shown; do not guess at behaviour that is not visible. "
                      "Reply with ONLY a JSON list: [{\"i\": 0, \"text\": \"...\"}, ...]\n\n"
                      + "\n\n".join(f"### {i}\n{inputs[m]}" for i, m in enumerate(chunk)))
            try:
                for item in _json_list(provider.generate(prompt)):
                    m = chunk[int(item["i"])]
                    text = " ".join(str(item["text"]).split())[:MODULE_CHARS]
                    if text:
                        data["modules"][m] = {"text": text, "by": "ai", "code": inputs[m].key}
                        job.written["modules"] += 1
            except Exception as e:
                job.failed += len(chunk)
                job.error = str(e)[:300]
            job.done += len(chunk)
            save(ix.root, data)

        if "overview" in steps and not job.cancel.is_set():
            job.step = "overview"
            ov = data.get("overview") or {}
            ov_in = overview_input(ix, data["modules"])
            if not ov or (ov.get("code") != _h(ov_in) and ov.get("by") != "human"):
                prompt = ("Write one short paragraph (at most 90 words) for a developer or coding agent new to this repository: what "
                          "it does, its main parts and how they fit together. Use only the facts below; no marketing words, no "
                          "guesses. Reply with the paragraph only.\n\n" + ov_in)
                try:
                    text = " ".join(provider.generate(prompt).split())[:OVERVIEW_CHARS]
                    if text:
                        data["overview"] = {"text": text, "by": "ai", "code": _h(ov_in)}
                        job.written["overview"] = 1
                    else:
                        job.failed += 1
                except Exception as e:
                    job.failed += 1
                    job.error = str(e)[:300]
            job.done += 1
        job.state = "cancelled" if job.cancel.is_set() else "done"
    except Exception as e:
        job.state, job.error = "failed", str(e)[:300]
    data = load(ix.root) if job.state == "failed" else data
    data["last_run"] = {"at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "model": job.model, "state": job.state,
                        "written": job.written, "failed": job.failed}
    save(ix.root, data)
    return job


def set_text(ix: Index, module: str | None, text: str) -> dict:
    """A person's correction of the overview (module None) or of one module summary. Empty text removes it."""
    data = load(ix.root)
    text = " ".join(text.split())
    if module is None:
        if text:
            data["overview"] = {"text": text[:OVERVIEW_CHARS], "by": "human", "code": _h(overview_input(ix, data["modules"]))}
        else:
            data.pop("overview", None)
    else:
        inputs = module_inputs(ix)
        if module not in inputs:
            raise KeyError(module)
        if text:
            data["modules"][module] = {"text": text[:MODULE_CHARS], "by": "human", "code": inputs[module].key}
        else:
            data["modules"].pop(module, None)
    save(ix.root, data)
    return data
