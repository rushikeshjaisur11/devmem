"""App-side memory: hub sync of lessons/rules/feedback, sharing, lesson->rule, proposals from review comments and
recurring failures, cross-repo pointers."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
from collections import Counter, defaultdict
from pathlib import Path

from devmem import memory as m
from devmem.index import Index, _tokens

from .paths import hub_dir
from .ranking import pagerank

MIN_CLUSTER = 3     # comments/failures needed before proposing
MIN_PRS = 2         # ... across at least this many PRs (reviews only)
SIM = 0.5


def _hub(*parts) -> Path:
    """Hub path; creates the directory (or the file's parent directory)."""
    p = hub_dir().joinpath(*parts)
    (p.parent if p.suffix else p).mkdir(parents=True, exist_ok=True)
    return p


# ---------- sync ----------
def sync(repos: dict[str, str | Path]) -> dict:
    """1) lessons + feedback from every repo -> hub  2) hub lessons/rules + usefulness stats -> each repo."""
    hub_l, hub_r = _hub("lessons"), _hub("rules")
    fb_path = _hub("metrics", "feedback.jsonl")
    seen_fb = set(fb_path.read_text(encoding="utf-8").splitlines()) if fb_path.exists() else set()
    new_l = new_fb = 0
    for name, root in repos.items():
        root = Path(root)
        for l in m.load_lessons(root):
            dst = hub_l / f"{l['id']}.md"
            if not dst.exists():
                meta = {k: v for k, v in l.items() if k not in ("problem", "fix", "path")}
                meta["origin"] = name
                dst.write_text(m.dump_front(meta, f"## Problem\n{l['problem']}\n\n## Fix\n{l['fix']}"), encoding="utf-8")
                new_l += 1
        fp = root / m.FEEDBACK
        if fp.exists():
            with open(fb_path, "a", encoding="utf-8") as out:
                for line in fp.read_text(encoding="utf-8").splitlines():
                    key = json.dumps({**json.loads(line), "repo": name}, sort_keys=True)
                    if key not in seen_fb:
                        seen_fb.add(key); out.write(key + "\n"); new_fb += 1
    stats = defaultdict(lambda: {"useful": 0, "useless": 0})
    for line in seen_fb:
        d = json.loads(line)
        stats[d["lesson"]]["useful" if d["verdict"] == "useful" else "useless"] += 1
    hub_lessons = {}
    for f in hub_l.glob("*.md"):
        meta, body = m.parse_front(f.read_text(encoding="utf-8"))
        meta.update({"problem": m.section(body, "Problem"), "fix": m.section(body, "Fix")})
        hub_lessons[meta["id"]] = meta
    pushed = 0
    for name, root in repos.items():
        root = Path(root)
        for lid, l in hub_lessons.items():
            if name in l.get("repos", []) or "*" in l.get("repos", []):
                dst = root / m.LESSONS_DIR / f"{lid}.md"
                if not dst.exists():
                    meta = {k: v for k, v in l.items() if k not in ("problem", "fix", "path")}
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.write_text(m.dump_front(meta, f"## Problem\n{l['problem']}\n\n## Fix\n{l['fix']}"), encoding="utf-8")
                    pushed += 1
        for f in hub_r.glob("*.md"):   # global rules: the hub is the source of truth
            dst = root / m.RULES_DIR / f.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f, dst)
        (root / m.STATS).write_text(json.dumps(stats, indent=1, sort_keys=True), encoding="utf-8")
    return {"new_lessons": new_l, "new_feedback": new_fb, "lessons_pushed": pushed, "hub_lessons": len(hub_lessons)}


def share(lesson_id: str, repos: list[str] | None = None, everywhere: bool = False) -> list[str]:
    f = _hub("lessons") / f"{lesson_id}.md"
    meta, body = m.parse_front(f.read_text(encoding="utf-8"))
    meta["repos"] = ["*"] if everywhere else sorted(set(meta.get("repos", [])) | set(repos or []))
    f.write_text(m.dump_front(meta, body), encoding="utf-8")
    return meta["repos"]


def lesson_to_rule(lesson_id: str, applies_to: str, target_repo: Path | None = None, severity: str = "rule") -> Path:
    f = _hub("lessons") / f"{lesson_id}.md"
    meta, body = m.parse_front(f.read_text(encoding="utf-8"))
    fix = m.section(body, "Fix")
    text = f"{meta['title']}: {fix.splitlines()[0]}"
    if target_repo:
        return m.write_rule(target_repo, meta["id"], text, applies_to, "repo", severity)
    return _write_global_rule(meta["id"], text, applies_to, severity)


def _write_global_rule(rid: str, text: str, applies_to: str, severity: str) -> Path:
    p = _hub("rules") / f"{m.slug(rid)}.md"
    p.write_text(m.dump_front({"id": m.slug(rid), "scope": "global", "applies_to": applies_to, "severity": severity,
                               "created": str(dt.date.today())}, text), encoding="utf-8")
    return p


# ---------- proposals ----------
_NOISE = re.compile(r"```.*?```|`[^`]*`|https?://\S+|\b\d+\b", re.S)


def _norm_tokens(text: str) -> set[str]:
    return m.toks(_NOISE.sub(" ", text))


def _cluster(items: list[tuple[set[str], dict]]) -> list[list[tuple[set[str], dict]]]:
    clusters: list[list] = []
    for toks_, item in items:
        for c in clusters:
            rep = c[0][0]
            if toks_ and rep and len(toks_ & rep) / len(toks_ | rep) >= SIM:
                c.append((toks_, item)); break
        else:
            clusters.append([(toks_, item)])
    return clusters


def _glob_for(paths: list[str]) -> str:
    paths = [p for p in paths if p]
    if not paths:
        return "**"
    dirs = {str(Path(p).parent) for p in paths}
    exts = {Path(p).suffix for p in paths}
    ext = exts.pop() if len(exts) == 1 else ".*"
    if len(dirs) == 1:
        d = dirs.pop()
        return (f"{d}/*{ext}" if d != "." else f"*{ext}")
    common = os.path.commonpath([str(Path(p).parent) for p in paths]) if all("/" in p for p in paths) else ""
    return f"{common}/**/*{ext}" if common else f"**/*{ext}"


def _save_proposal(p: dict) -> Path:
    f = _hub("proposals") / f"{p['id']}.json"
    if f.exists():
        return f
    f.write_text(json.dumps(p, indent=1), encoding="utf-8")
    return f


def propose_from_reviews(json_path: str | Path) -> list[dict]:
    """json: [{pr, path, body, author}] (e.g. exported with `gh api`). Repeated feedback -> proposed rule."""
    comments = json.loads(Path(json_path).read_text(encoding="utf-8"))
    items = [(_norm_tokens(c["body"]), c) for c in comments if c.get("body")]
    out = []
    for cl in _cluster(items):
        prs = {c["pr"] for _, c in cl}
        if len(cl) < MIN_CLUSTER or len(prs) < MIN_PRS:
            continue
        # most central comment as the rule text
        best = max(cl, key=lambda t: sum(len(t[0] & o[0]) / max(1, len(t[0] | o[0])) for o in cl))[1]
        text = re.split(r"(?<=[.!?])\s", best["body"].strip())[0][:240]
        pid = f"rule-{m.slug(text)[:30]}-{abs(hash(text)) % 10**5:05d}"
        prop = {"id": pid, "kind": "rule", "text": text, "applies_to": _glob_for([c.get("path", "") for _, c in cl]),
                "evidence": [{"pr": c["pr"], "path": c.get("path"), "body": c["body"][:160]} for _, c in cl[:5]],
                "count": len(cl), "prs": len(prs), "created": str(dt.date.today())}
        _save_proposal(prop); out.append(prop)
    return out


def propose_from_failures(repo: str | Path) -> list[dict]:
    """.devmem/failures.jsonl (written by `devmem check`): the same failure >= 3 times -> pending lesson."""
    f = Path(repo) / ".devmem/failures.jsonl"
    if not f.exists():
        return []
    norm = lambda s: re.sub(r"\d+", "N", re.sub(r"\S*/\S+", "<path>", s))[:120]
    counts, first = Counter(), {}
    for line in f.read_text(encoding="utf-8").splitlines():
        d = json.loads(line)
        k = (d["kind"], norm(d["line"]))
        counts[k] += 1
        first.setdefault(k, d["line"])
    out = []
    for (kind, key), n in counts.items():
        if n >= MIN_CLUSTER:
            pid = f"lesson-{kind}-{m.slug(key)[:30]}"
            prop = {"id": pid, "kind": "lesson", "text": f"Recurring {kind} failure: {first[(kind, key)][:160]}",
                    "problem": f"`devmem check` failed {n} times with: {first[(kind, key)][:200]}",
                    "fix": "(fill in how to fix or avoid this)", "count": n, "created": str(dt.date.today())}
            _save_proposal(prop); out.append(prop)
    return out


def list_proposals() -> list[dict]:
    return [json.loads(f.read_text(encoding="utf-8")) for f in sorted(_hub("proposals").glob("*.json"))]


def approve(pid: str, repo: Path | None = None, severity: str = "rule", fix: str | None = None) -> Path:
    f = _hub("proposals") / f"{pid}.json"
    p = json.loads(f.read_text(encoding="utf-8"))
    if p["kind"] == "rule":
        out = m.write_rule(repo, pid, p["text"], p["applies_to"], "repo", severity) if repo else \
            _write_global_rule(pid, p["text"], p["applies_to"], severity)
    else:
        if not fix:
            raise ValueError("a lesson proposal needs --fix text before it can be approved")
        status, lid = m.write_lesson(repo or _hub(), p["text"], p["problem"], fix, repo.name if repo else "*", [], [], [], "proposal", True)
        out = Path(repo or _hub()) / m.LESSONS_DIR / f"{lid}.md"
    f.unlink()
    return out


def reject(pid: str) -> None:
    (_hub("proposals") / f"{pid}.json").unlink()


# ---------- cross-repo pointers ----------
def build_cross_repo(name: str, root: Path, peers: dict[str, str | Path], per_peer: int = 15) -> list[dict]:
    entries = []
    for pname, proot in peers.items():
        if pname == name or Path(proot).resolve() == Path(root).resolve():
            continue
        try:
            ix = Index.load(Path(proot))
        except FileNotFoundError:
            continue
        rank = pagerank(ix.data)
        chosen = {s["id"]: s for s, _ in ix.helpers(per_peer)}
        for sid in sorted(rank, key=lambda k: -rank[k])[:per_peer]:
            s = ix.symbols[sid]
            if s["kind"] == "function" and not s["name"].startswith("_"):
                chosen.setdefault(sid, s)
        for s in chosen.values():
            entries.append({"repo": pname, "id": f"{pname}:{s['id']}", "file": s["file"], "line": s["line"],
                            "name": s["qualname"], "sig": s["sig"], "doc": s["doc"]})
    p = Path(root) / m.CROSS_REPO
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(entries, indent=1), encoding="utf-8")
    return entries


def render_cross_repo_md(entries: list[dict], limit: int = 30) -> str:
    lines = ["# Cross-repo pointers", "Code in other onboarded repos that already solves common things. Prefer reusing or "
             "copying it (keep the origin comment) over writing it again. `devmem recall \"<task>\"` searches these.", ""]
    for e in sorted(entries, key=lambda e: (e["repo"], e["file"], e["line"]))[:limit]:
        lines.append(f"- `{e['repo']}:{e['file']}:{e['line']}` `{e['sig'][4:] if e['sig'].startswith('def ') else e['sig']}`"
                     + (f" — {e['doc']}" if e["doc"] else ""))
    return "\n".join(lines)


# ---------- knowledge renders ----------
def render_lessons_md(root: Path, limit: int = 30) -> str:
    use = m.usefulness(root)
    ls = m.load_lessons(root)
    ls.sort(key=lambda l: (-m.usefulness_factor(*use.get(l["id"], (0, 0))), str(l.get("created", "")), l["id"]))
    lines = ["# Lessons", "Problems already solved. Run `devmem recall \"<task>\"` for details before debugging; "
             "`devmem remember` when you learn something new.", ""]
    for l in ls[:limit]:
        lines.append(f"- `{l['id']}` {l['title']}" + (f" [{', '.join(l['tags'])}]" if l.get("tags") else ""))
    if len(ls) > limit:
        lines.append(f"- ... {len(ls) - limit} more")
    return "\n".join(lines)


def render_rules_md(root: Path, limit: int = 30) -> str:
    rules = m.load_rules(root)
    rules.sort(key=lambda r: (r["severity"] != "rule", r["scope"] != "global", r["id"]))
    lines = ["# Rules", "`rule` must be followed; `guideline` is the default unless the task needs otherwise.", ""]
    for r in rules[:limit]:
        scope = "" if r["applies_to"] in ("**", "*") else f" ({r['applies_to']})"
        lines.append(f"- [{r['severity']}]{scope} {r['text'][:200]}")
    return "\n".join(lines)
