"""New-implementation help: turn a requirement (or a plan the user wrote) into a compact prompt for ANOTHER agent (Devin, Claude
Code, Copilot ...). Read-only: it searches the onboarded repos and the library-docs store, and writes nothing into any repo and runs
no code. Everything an agent would otherwise spend tokens looking for goes into the prompt: the current docs to follow, the
existing org solution (flagged when a library now provides it built in), what the target repo already has, protected paths."""
from __future__ import annotations

import re
from pathlib import Path

from devmem import docs as core_docs
from devmem import notouch
from devmem import plan as core_plan
from devmem import reuse as core
from devmem.index import Index

from . import docs as docs_mod
from .reuse import _entry, _internal_names, _peers, build_entries, rank

RULES = """## Rules
- Follow, in this order: the task above > the current library docs below > existing org code > your own preferences.
- Smallest possible diff. Reuse what already exists. No redesign, renames, library swaps, new dependencies or extra scope unless the task says so.
- Use the library APIs shown in the docs below (they are the current versions). Older code in the repos may predate them.
- Do not edit protected paths. If a step cannot be done as written, or the docs or code contradict the task: stop and report it. Never guess an API."""

HOW = """## How to work (do these in order)
1. Read the task and the "What to use" section. Open only the files named there.
2. Before editing an existing function, run `devmem impact <path#name>` and keep its callers working.
3. Make the change. One step at a time if there are several steps.
4. Run `devmem check` (lint and only the affected tests). Fix what fails; do not edit a test to make it pass.
5. Run `devmem diffcheck`. If it blocks, make the change smaller.
6. Write the report described at the end."""

FINISH = """## Done when
- [ ] Every step of the task is implemented, or named in the report with the reason it was not.
- [ ] `devmem check` and `devmem diffcheck` pass.
- [ ] No protected path and no unrelated file is changed.

## Report back
- Per step: what you changed (file and function), which docs and existing code you used.
- The commands you ran and whether they passed.
- Anything that deviated from the task. There should be nothing."""


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _step_findings(tix: Index, pool: list[dict], ixs: dict[str, Index], q: str, detail: str, provider=None) -> dict:
    extra = [docs_mod.docs_dir()]
    docs_hits = [h for h in core_docs.search(None, q, 3, extra=extra) if h["cover"] >= 0.25][:2]
    org = []
    for score, e in rank(pool, q, 2, provider):
        if score < 0.5:
            continue
        full = _entry(ixs[e["repo"]], ixs[e["repo"]].symbols[e["id"]], _internal_names(ixs[e["repo"]]), 30 if detail == "code" else 0)
        org.append({"score": score, "entry": full, "fit": core.fit(full, tix, extra)})
    # code the task names outright (`Request.body`, `load_user`, `RetryPolicy`) comes first, then what search finds
    named = []
    for ref in dict.fromkeys(re.findall(r"`([^`]+)`|\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+|[a-z]+_[a-z_]+|[A-Z][a-z]+[A-Z]\w*)\b", q)):
        ref = next(r for r in ref if r)
        hits = [x for x in tix.resolve(ref.strip("()")) if not x["is_test"]]
        if 1 <= len(hits) <= 2:
            named += hits
    found = [s for sc, s in tix.find(q[:200], 4) if sc >= 3 and not s["is_test"]]   # >= 3: a word of the task is in the symbol's name
    local = []
    for s in named + found:
        cls = f"{s['file']}#{s['qualname'].split('.')[0]}"
        have = {x["id"] for x in local}
        if s["id"] in have or ("." in s["qualname"] and cls in have) or (s["kind"] == "class" and any(h.startswith(s["id"] + ".") for h in have)):
            continue                     # already listed; its class is (the class's code shows it); or one of its methods is (more specific)
        local.append(s)
    local = local[:4]
    pats = notouch.patterns(tix.root)
    protected = sorted({p for p in re.findall(r"[\w./-]+\.\w{1,5}\b", q) if notouch.matches(p, pats)})
    return {"docs": docs_hits, "org": org, "local": local, "protected": protected, "tix": tix}


def _render_findings(f: dict, detail: str, doc_chars: int) -> list[str]:
    out = []
    for h in f["docs"]:
        body = " ".join(h["text"].split())[:doc_chars]
        out.append(f"- Current docs, {h['lib']} {h['version']}".rstrip() + f" — \"{h['heading'] or h['title']}\" ({h['url']}): {body}")
    for h in f["org"]:
        e = h["entry"]
        out.append(f"- Already solved in `{e['repo']}`: {e['file']}:{e['line']} `{e['sig'] or e['name']}` (effort to reuse here: {h['fit']['effort']})"
                   + (f" — {e['doc']}" if e.get("doc") else ""))
        out += [f"  - MODERNIZE: {m}" for m in h["fit"].get("modernize", [])]
        out += [f"  - {m}" for m in h["fit"]["notes"][:3]]
        if detail == "code" and e.get("code"):
            out += ["  ```", *("  " + ln for ln in e["code"].splitlines()[:25]), "  ```"]
    if f["local"] and detail == "code" and f.get("tix") is not None:
        from .prompts import symbol_block
        out.append("- Already in this repo. Start here: extend this code, do not write a second version of it.")
        for s in f["local"][:2]:
            out += ["  " + ln for ln in symbol_block(f["tix"], s, 40)]
        if f["local"][2:]:
            out.append("  Also related: " + "; ".join(f"{s['file']}:{s['line']} {s['qualname']}" for s in f["local"][2:]))
    elif f["local"]:
        out.append("- Already in this repo (extend, do not duplicate): " + "; ".join(f"{s['file']}:{s['line']} {s['qualname']}" for s in f["local"]))
    if f["protected"]:
        out.append("- PROTECTED, do not edit: " + ", ".join(f["protected"]))
    return out


def build(target: str, repos: dict[str, str | Path], requirement: str = "", plan_text: str = "", detail: str = "pointers",
          fetch_docs: bool = True, provider=None) -> dict:
    """detail: 'pointers' (locations and notes: for an agent that explores well on its own) or 'code' (step by step for a small
    agent: also the source of the code to start from, its callers and the existing solutions, so it need not look for them)."""
    root = Path(repos[target])
    tix = Index.load(root)
    fetched = []
    pl = core_plan.parse(plan_text) if plan_text.strip() else None
    if pl is not None and not pl["steps"]:
        requirement, pl = (requirement + "\n" + plan_text).strip(), None
    if pl is None:
        text = requirement.strip()
        if not text:
            raise ValueError("describe the requirement or paste a plan")
        pl = {"title": text.splitlines()[0][:90], "constraints": [], "sources": [], "steps": [{"n": 1, "title": text.splitlines()[0][:200], "body": "\n".join(text.splitlines()[1:]).strip()}], "single": True}
    if fetch_docs:
        for src in pl["sources"]:
            try:
                m = docs_mod.add(src)
                fetched.append({"source": src, "status": "fetched", "name": m["name"], "version": m["version"], "pages": len(m["pages"])})
            except Exception as e:
                guess = docs_mod._norm(src.split(":", 1)[-1]) if src.startswith(("pypi:", "npm:")) else docs_mod._default_name(src)
                have = any(l["name"] == guess for l in docs_mod.list_libs())
                fetched.append({"source": src, "status": "cached" if have else "failed", "error": str(e)[:160], "name": guess if have else None})
    pool, ixs = [], {}
    for pname, ix in _peers(target, root, repos):
        ixs[pname] = ix
        pool += build_entries(ix, None, 0)
    doc_chars = 420 if detail == "pointers" else 700
    lines = [f"# Task: {pl['title']}", f"Repository: `{target}`", "", HOW, "", RULES, ""]
    if pl["constraints"]:
        lines += ["## Constraints", *(f"- {c}" for c in pl["constraints"]), ""]
    steps_out = []
    single = pl.get("single")
    if single:
        st = pl["steps"][0]
        full_text = (st["title"] + "\n" + st["body"]).strip()
        lines += ["## Task", full_text, ""]
        f = _step_findings(tix, pool, ixs, full_text, detail, provider)
        body = _render_findings(f, detail, doc_chars)
        lines += (["## What to use (found for you)", *body, ""] if body else ["## What to use", "- Nothing matching was found in the other repos or the docs on file: build it from the task alone and say so in your summary.", ""])
        steps_out.append({"n": 1, "title": st["title"], "findings": f})
    else:
        lines.append("## Steps (do them in order)")
        for st in pl["steps"]:
            q = (st["title"] + "\n" + st["body"]).strip()
            f = _step_findings(tix, pool, ixs, q[:600], detail, provider)
            lines += ["", f"### Step {st['n']}: {st['title']}"]
            if st["body"]:
                lines.append(st["body"])
            lines += _render_findings(f, detail, doc_chars)
            steps_out.append({"n": st["n"], "title": st["title"], "findings": f})
        lines.append("")
    lines.append(FINISH)
    prompt = "\n".join(lines).strip() + "\n"
    n_org = sum(len(s["findings"]["org"]) for s in steps_out)
    n_mod = sum(len(h["fit"].get("modernize", [])) for s in steps_out for h in s["findings"]["org"])
    n_docs = sum(len(s["findings"]["docs"]) for s in steps_out)
    return {"prompt": prompt, "tokens": _tokens(prompt), "title": pl["title"], "steps": len(pl["steps"]), "mode": "task" if single else "plan",
            "counts": {"org_solutions": n_org, "modernize": n_mod, "doc_excerpts": n_docs},
            "docs_fetched": fetched, "summary": [
                {"n": s["n"], "title": s["title"], "docs": [f"{h['lib']} {h['version']}: {h['heading']}" for h in s["findings"]["docs"]],
                 "org": [f"{h['entry']['repo']}:{h['entry']['file']}:{h['entry']['line']} {h['entry']['name']} ({h['fit']['effort']})" for h in s["findings"]["org"]],
                 "modernize": [m for h in s["findings"]["org"] for m in h["fit"].get("modernize", [])],
                 "local": [f"{x['file']}:{x['line']} {x['qualname']}" for x in s["findings"]["local"]],
                 "protected": s["findings"]["protected"]} for s in steps_out]}
