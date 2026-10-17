"""Execute a plan that a human wrote. The plan is the authority: the agent does not redesign, swap libraries or "improve" things.
devmem's job is to hand the agent, step by step, everything it would otherwise go looking for: the current library docs to follow,
the organisation's existing solution (with a note when a library now provides it built in), files this repo already has, and
protected paths. Progress is kept in `.devmem/plans/<plan>.progress.json` so a session can stop and resume.

Plan format: plain markdown. Steps are `## Step N: ...` headings, a numbered list, or `- [ ]` items. Optional lines anywhere:
    Docs: https://example.dev/llms.txt pypi:somelib npm:other      (what `devmem-app plan prepare` fetches for offline use)
    Constraints: no new dependencies        /  Do not touch the billing module"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .index import Index

PLANS = ".devmem/plans"
DOC_LINE = re.compile(r"^\s*(?:[-*]\s*)?(?:docs?|references?|libraries|library|libs?)\s*:\s*(.+)$", re.I)
CONSTRAINT = re.compile(r"^\s*(?:[-*]\s*)?(?:(?:constraints?|rules?)\s*:\s*(.+)|((?:do not|don't|never|must not|only)\b.*))$", re.I)
SOURCE = re.compile(r"(https?://[^\s,)>\]]+|(?:pypi|npm):[\w@./-]+)")


def slug(path: str | Path) -> str:
    return re.sub(r"[^a-z0-9]+", "-", Path(path).stem.lower()).strip("-") or "plan"


def parse(text: str) -> dict:
    lines = text.splitlines()
    title = next((ln.lstrip("# ").strip() for ln in lines if ln.startswith("# ")), "plan")
    sources: list[str] = []
    constraints: list[str] = []
    for ln in lines:
        m = DOC_LINE.match(ln)
        if m:
            sources += SOURCE.findall(m.group(1))
        c = CONSTRAINT.match(ln)
        if c:
            constraints.append((c.group(1) or c.group(2)).strip())
    heads = [i for i, ln in enumerate(lines) if re.match(r"^#{2,4}\s+(?:step\s*)?\d+\s*[.):\-]?", ln, re.I)]
    kind = "heading"
    if not heads:
        heads = [i for i, ln in enumerate(lines) if re.match(r"^\d+[.)]\s+\S", ln)]
        kind = "numbered"
    if not heads:
        heads = [i for i, ln in enumerate(lines) if re.match(r"^\s*[-*]\s+\[[ xX]\]\s+\S", ln)]
        kind = "checkbox"
    if not heads:
        heads = [i for i, ln in enumerate(lines) if re.match(r"^##\s+\S", ln)]
        kind = "heading"
    steps = []
    for k, i in enumerate(heads):
        end = heads[k + 1] if k + 1 < len(heads) else len(lines)
        first = re.sub(r"^(?:#{2,4}\s+|\d+[.)]\s+|\s*[-*]\s+\[[ xX]\]\s+)", "", lines[i]).strip()
        first = re.sub(r"^(?:step\s*)?\d+\s*[.):\-]?\s*", "", first, flags=re.I).strip() or first
        steps.append({"n": k + 1, "title": first, "body": "\n".join(lines[i + 1:end]).strip()})
    seen = set()
    return {"title": title, "sources": [s for s in sources if not (s in seen or seen.add(s))], "constraints": constraints, "steps": steps, "kind": kind}


def progress_path(root: Path, plan_file: str | Path) -> Path:
    return Path(root) / PLANS / f"{slug(plan_file)}.progress.json"


def progress(root: Path, plan_file: str | Path) -> dict:
    p = progress_path(root, plan_file)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"done": [], "notes": {}}


def mark(root: Path, plan_file: str | Path, n: int, note: str = "") -> dict:
    pr = progress(root, plan_file)
    if n not in pr["done"]:
        pr["done"] = sorted(pr["done"] + [n])
    if note:
        pr["notes"][str(n)] = note
    p = progress_path(root, plan_file)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(pr, indent=1), encoding="utf-8")
    return pr


def status(root: Path, plan_file: str | Path, plan: dict) -> str:
    pr = progress(root, plan_file)
    out = [f"{plan['title']}: {len(pr['done'])}/{len(plan['steps'])} steps done"]
    nxt = next((s for s in plan["steps"] if s["n"] not in pr["done"]), None)
    for s in plan["steps"]:
        out.append(f"  [{'x' if s['n'] in pr['done'] else ' '}] {s['n']}. {s['title']}" + (f"  ({pr['notes'][str(s['n'])]})" if str(s["n"]) in pr["notes"] else ""))
    out.append(f"next: step {nxt['n']}" if nxt else "all steps done: run `devmem check`, `devmem diffcheck`, `devmem report`")
    return "\n".join(out)


def brief(ix: Index, plan: dict, n: int, with_code: bool = False) -> str:
    from . import docs, notouch, reuse
    step = next((s for s in plan["steps"] if s["n"] == n), None)
    if step is None:
        return f"no step {n}: the plan has {len(plan['steps'])} steps"
    text = f"{step['title']}\n{step['body']}".strip()
    q = (step["title"] + " " + step["body"])[:600]
    out = [f"STEP {n}/{len(plan['steps'])}: {step['title']}", "",
           "Execute exactly what this step says. The plan overrides everything below, and your own preferences: do not redesign, do not swap",
           "libraries, do not add scope. If the step cannot be done as written (missing symbol, conflicting code), STOP and report it.", ""]
    if step["body"]:
        out += ["STEP TEXT", *("  " + ln for ln in step["body"].splitlines()[:40]), ""]
    if plan["constraints"]:
        out += ["CONSTRAINTS (apply to every step)", *(f"  - {c}" for c in plan["constraints"]), ""]
    hits = docs.search(ix.root, q, 3)
    if hits:
        out += ["CURRENT LIBRARY DOCS (follow these APIs; they are newer than the org's older code)"]
        out += [docs.render(h, 900) for h in hits] + [""]
    elif docs.libraries(ix.root):
        out += ["CURRENT LIBRARY DOCS: nothing on file matches this step (do not guess an API; say so if you need one)", ""]
    entries, _ = reuse.all_entries(ix.root, auto_sync=False)
    org = [h for h in reuse.reuse(ix, q, entries, 2) if h["score"] >= 0.5] if entries else []
    if org:
        out += ["EXISTING SOLUTION IN OTHER ORG REPOS (read first; use it only where it matches the plan and the docs above)"]
        for h in org:
            e = h["entry"]
            out.append(f"  {e['repo']}:{e['file']}:{e['line']}  {e['sig'] or e['name']}   [effort to reuse: {h['fit']['effort']}]")
            out += [f"    ~ MODERNIZE: {m}" for m in h["fit"].get("modernize", [])]
            out += [f"    ! {m}" for m in h["fit"]["notes"][:4]]
            if with_code and e.get("code"):
                out += ["    " + ln for ln in e["code"].splitlines()[:30]]
        out.append("")
    local = [s for _, s in ix.find(step["title"], 4) if not s["is_test"]]
    if local:
        out += ["ALREADY IN THIS REPO (extend rather than duplicate)"] + [f"  {s['file']}:{s['line']}  {s['kind']} {s['qualname']}" for s in local] + [""]
    pats = notouch.patterns(ix.root)
    prot = sorted({p for p in re.findall(r"[\w./-]+\.\w{1,5}\b", text) if notouch.matches(p, pats)})
    if prot:
        out += ["PROTECTED PATHS MENTIONED (diffcheck will reject edits): " + ", ".join(prot) + ". Stop and report instead of editing them.", ""]
    out += ["WHEN DONE: `devmem impact <symbol>` for anything you changed, `devmem check`, `devmem diffcheck`, commit, then "
            f"`devmem plan <plan-file> --done {n}`."]
    return "\n".join(out)
