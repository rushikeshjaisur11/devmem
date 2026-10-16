"""Lessons, rules, feedback and recall. Everything is a file in the repo (.devmem/lessons, .devmem/rules,
.devmem/feedback.jsonl); no server. Stdlib + PyYAML."""
from __future__ import annotations

import datetime as dt
import fnmatch
import hashlib
import json
import re
from pathlib import Path

import yaml

from .index import Index, _tokens

LESSONS_DIR = ".devmem/lessons"
RULES_DIR = ".devmem/rules"
FEEDBACK = ".devmem/feedback.jsonl"
STATS = ".devmem/lessons-stats.json"
CROSS_REPO = ".devmem/cross-repo.json"
MAX_LEN = 1500
STOP = {"the", "and", "for", "with", "how", "to", "in", "of", "on", "is", "it", "we", "do", "a", "an", "this", "that",
        "when", "from", "into", "not", "are", "be", "use", "using", "add", "make", "new"}


def toks(text: str) -> set[str]:
    return {t for t in _tokens(text) if t not in STOP}


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "lesson"


# ---------- file format ----------
def parse_front(text: str) -> tuple[dict, str]:
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            return (yaml.safe_load(text[4:end]) or {}), text[end + 4:].lstrip("\n")
    return {}, text


def dump_front(meta: dict, body: str) -> str:
    return "---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).strip() + "\n---\n" + body.strip() + "\n"


def section(body: str, name: str) -> str:
    m = re.search(rf"^## {name}\s*\n(.*?)(?=^## |\Z)", body, re.S | re.M)
    return m.group(1).strip() if m else ""


# ---------- lessons ----------
def load_lessons(root: Path) -> list[dict]:
    out = []
    for f in sorted((Path(root) / LESSONS_DIR).glob("*.md")):
        meta, body = parse_front(f.read_text(encoding="utf-8"))
        if meta.get("id"):
            meta.update({"problem": section(body, "Problem"), "fix": section(body, "Fix"), "path": f.as_posix()})
            out.append(meta)
    return out


def write_lesson(root: Path, title: str, problem: str, fix: str, repo: str, tags=(), refs=(), commands=(),
                 source: str = "agent", force: bool = False) -> tuple[str, str | None]:
    """-> (status, id): 'created' | 'duplicate' (id of the similar one) | raises ValueError on bad input."""
    root = Path(root)
    title, problem, fix = title.strip(), problem.strip(), fix.strip()
    if not (title and problem and fix):
        raise ValueError("title, problem and fix are all required")
    if len(problem) + len(fix) > MAX_LEN:
        raise ValueError(f"lesson too long ({len(problem) + len(fix)} > {MAX_LEN} chars): keep it to the point")
    tt = toks(title)
    lid = f"{slug(title)}-{hashlib.sha1((title + problem + fix).encode()).hexdigest()[:6]}"
    if not force:
        for l in load_lessons(root):
            lt = toks(l["title"])
            if l["id"] == lid or (tt and lt and len(tt & lt) / len(tt | lt) >= 0.8):
                return "duplicate", l["id"]
    meta = {"id": lid, "title": title, "repos": [repo], "tags": sorted({t.lower() for t in tags}), "refs": list(refs),
            "commands": list(commands), "source": source, "created": str(dt.date.today())}
    p = root / LESSONS_DIR / f"{lid}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dump_front(meta, f"## Problem\n{problem}\n\n## Fix\n{fix}"), encoding="utf-8")
    return "created", lid


def normalize_ref(ref: str, repo: str, commit: str | None) -> str:
    """'path#symbol' -> 'repo:path#symbol@commit'."""
    if ":" in ref.split("#")[0] and "@" in ref:
        return ref
    return f"{repo}:{ref}" + (f"@{commit[:10]}" if commit and "@" not in ref else "")


# ---------- feedback ----------
def add_feedback(root: Path, lesson_id: str, verdict: str, note: str = "") -> None:
    if verdict not in ("useful", "useless"):
        raise ValueError("verdict must be 'useful' or 'useless'")
    p = Path(root) / FEEDBACK
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "lesson": lesson_id,
                             "verdict": verdict, "note": note}) + "\n")


def usefulness(root: Path) -> dict[str, tuple[int, int]]:
    """{lesson_id: (useful, useless)} from the local append-only log plus published hub stats."""
    stats: dict[str, list[int]] = {}
    p = Path(root) / STATS
    if p.exists():
        for k, v in json.loads(p.read_text(encoding="utf-8")).items():
            stats[k] = [v.get("useful", 0), v.get("useless", 0)]
    f = Path(root) / FEEDBACK
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            s = stats.setdefault(d["lesson"], [0, 0])
            s[0 if d["verdict"] == "useful" else 1] += 1
    return {k: (v[0], v[1]) for k, v in stats.items()}


def usefulness_factor(h: int, n: int) -> float:
    return 0.5 + (h + 1) / (h + n + 2)   # 0.5..1.5, neutral 1.0 with no feedback


# ---------- rules ----------
def load_rules(root: Path) -> list[dict]:
    root = Path(root)
    out = []
    for f in sorted((root / RULES_DIR).glob("*.md")):
        meta, body = parse_front(f.read_text(encoding="utf-8"))
        if meta.get("id"):
            meta["text"] = body.strip()
            meta.setdefault("scope", "repo"); meta.setdefault("applies_to", "**"); meta.setdefault("severity", "rule")
            out.append(meta)
    free = root / ".devmem/rules.md"
    if free.exists():
        for i, line in enumerate(l for l in free.read_text(encoding="utf-8").splitlines() if l.startswith("- ")):
            out.append({"id": f"rules.md#{i + 1}", "scope": "repo", "applies_to": "**", "severity": "rule",
                        "text": line[2:].strip()})
    return out


def write_rule(root: Path, rid: str, text: str, applies_to: str = "**", scope: str = "repo", severity: str = "rule") -> Path:
    p = Path(root) / RULES_DIR / f"{slug(rid)}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dump_front({"id": slug(rid), "scope": scope, "applies_to": applies_to, "severity": severity,
                             "created": str(dt.date.today())}, text), encoding="utf-8")
    return p


def rule_applies(rule: dict, files: list[str]) -> bool:
    pat = rule.get("applies_to", "**")
    if pat in ("**", "*"):
        return False   # global-to-repo rules are always listed in rules.md; recall only surfaces targeted ones
    return any(fnmatch.fnmatch(f, pat) or fnmatch.fnmatch(f, pat.replace("**/", "")) for f in files)


# ---------- recall ----------
def recall(ix: Index, task: str, files: list[str] | None = None, limit: int = 3) -> dict:
    root = ix.root
    q = toks(task)
    hits = [s for _, s in ix.find(task, 5)]
    files = list(files or []) + [s["file"] for s in hits]
    use = usefulness(root)

    lessons = []
    for l in load_lessons(root):
        ref_paths = " ".join(r.split(":", 1)[-1].split("#")[0] for r in l.get("refs", []))
        rel = (3 * len(q & toks(l["title"])) + 2 * len(q & toks(" ".join(l.get("tags", [])))) +
               1 * len(q & toks(l["problem"] + " " + l["fix"])) + 1.5 * len(q & toks(ref_paths)))
        rel += 2 * sum(1 for f in set(files) if f in ref_paths)
        if rel >= 2:
            h, n = use.get(l["id"], (0, 0))
            lessons.append((rel * usefulness_factor(h, n), l))
    lessons.sort(key=lambda x: (-x[0], x[1]["id"]))

    rules = []
    for r in load_rules(root):
        if rule_applies(r, files) or len(q & toks(r["text"])) >= 2:
            rules.append(r)
    rules.sort(key=lambda r: (r["severity"] != "rule", r["id"]))

    cross = []
    cp = root / CROSS_REPO
    if cp.exists():
        for e in json.loads(cp.read_text(encoding="utf-8")):
            s = 3 * len(q & toks(e["name"])) + len(q & toks(e.get("doc", "")))
            if s >= 3:
                cross.append((s, e))
        cross.sort(key=lambda x: (-x[0], x[1]["id"]))
    return {"lessons": [l for _, l in lessons[:limit]], "rules": rules[:5], "cross_repo": [e for _, e in cross[:3]]}


def format_recall(r: dict) -> str:
    out = []
    if r["lessons"]:
        out.append("LESSONS (already solved; use before debugging)")
        for l in r["lessons"]:
            out.append(f" [{l['id']}] {l['title']}" + (f"  ({', '.join(l['tags'])})" if l.get("tags") else ""))
            out.append(f"   problem: {l['problem'].splitlines()[0][:160]}")
            out.append(f"   fix: {l['fix'].splitlines()[0][:200]}")
            for c in l.get("commands", [])[:2]:
                out.append(f"   run: {c}")
            for ref in l.get("refs", [])[:2]:
                out.append(f"   see: {ref}")
    if r["rules"]:
        out.append("RULES")
        out += [f" [{x['severity']}] {x['applies_to']}: {x['text'][:200]}" for x in r["rules"]]
    if r["cross_repo"]:
        out.append("ALSO SOLVED IN OTHER REPOS (reuse or copy)")
        out += [f" {e['repo']}:{e['file']}:{e['line']}  {e['sig']}" + (f" — {e['doc']}" if e.get("doc") else "")
                for e in r["cross_repo"]]
    return "\n".join(out) or "nothing relevant recorded"
