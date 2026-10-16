"""Git history insights: hotspots, co-change pairs, no-touch zones -> .devmem/history.json."""
from __future__ import annotations

import fnmatch
import json
import re
import subprocess
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

from devmem import notouch
from devmem.ignore import Ignore

BUGFIX = re.compile(r"\b(fix|fixes|fixed|bug|hotfix|defect|regression|revert|patch)\b", re.I)
GENERATED = re.compile(r"(DO NOT EDIT|@generated|Code generated|auto-?generated|automatically generated)", re.I)
MAX_COMMITS = 2000
MAX_FILES_PER_COMMIT = 30
MIN_PAIR_COUNT = 3
MIN_PAIR_CONF = 0.5
GEN_DIRS = ["migrations", "vendor", "vendored", "node_modules", "dist", "build", "generated", "__generated__"]


def _git(root: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, errors="replace")
    return r.stdout if r.returncode == 0 else ""


def read_log(root: Path, max_commits: int = MAX_COMMITS) -> list[dict]:
    out = _git(root, "log", "--no-merges", f"-n{max_commits}", "--name-only", "--date=short",
               "--pretty=format:@@%H|%ad|%an|%s")
    commits, cur = [], None
    for line in out.splitlines():
        if line.startswith("@@"):
            h, d, a, subj = line[2:].split("|", 3)
            cur = {"hash": h, "date": d, "author": a, "subject": subj, "files": []}
            commits.append(cur)
        elif line.strip() and cur is not None:
            cur["files"].append(line.strip())
    return commits


def detect_no_touch(root: Path) -> list[str]:
    """Directories/files that exist in the repo and look generated, vendored, migration-like or secret."""
    tracked = [f for f in _git(root, "ls-files").splitlines() if f]
    found: list[str] = []
    dirs = set()
    for f in tracked:
        parts = f.split("/")
        for i, p in enumerate(parts[:-1]):
            if p in GEN_DIRS:
                dirs.add("/".join(parts[: i + 1]) + "/")
    found += sorted(dirs)
    for f in tracked:
        if any(f.startswith(d) for d in dirs) or not f.endswith((".py", ".js", ".ts", ".java", ".go", ".sql")):
            continue
        try:
            head = (root / f).read_bytes()[:600].decode("utf-8", "ignore")
        except OSError:
            continue
        if GENERATED.search("\n".join(head.splitlines()[:6])):
            found.append(f)
    return found


def mine(root: str | Path, idx: dict, max_commits: int = MAX_COMMITS) -> dict:
    root = Path(root)
    ig = Ignore.for_repo(root)
    existing = set(idx["files"])
    commits = read_log(root, max_commits)
    stats = defaultdict(lambda: {"commits": 0, "bugfix": 0, "authors": set(), "last": ""})
    pair, per_file = Counter(), Counter()
    for c in commits:
        files = [f for f in c["files"] if f in existing and not ig.matches(f)]
        bug = bool(BUGFIX.search(c["subject"]))
        for f in files:
            st = stats[f]
            st["commits"] += 1
            st["bugfix"] += bug
            st["authors"].add(c["author"])
            st["last"] = st["last"] or c["date"]
            per_file[f] += 1
        if 2 <= len(files) <= MAX_FILES_PER_COMMIT:
            for a, b in combinations(sorted(files), 2):
                pair[(a, b)] += 1

    # complexity (from hygiene) makes a churny file riskier
    cx = defaultdict(int)
    hp = root / ".devmem/hygiene.json"
    if hp.exists():
        for i in json.loads(hp.read_text()).get("issues", []):
            if i["tool"] == "radon":
                cx[i["path"]] = max(cx[i["path"]], i.get("complexity", 0))
    hot = []
    for f, st in stats.items():
        if st["commits"] < 3:
            continue
        score = st["commits"] + 3 * st["bugfix"] + (st["commits"] if cx[f] > 10 else 0)
        hot.append({"file": f, "commits": st["commits"], "bugfix": st["bugfix"], "authors": len(st["authors"]),
                    "last": st["last"], "complexity": cx[f] or None, "score": score})
    hot.sort(key=lambda h: (-h["score"], h["file"]))
    co = []
    for (a, b), n in pair.items():
        conf = n / min(per_file[a], per_file[b])
        if n >= MIN_PAIR_COUNT and conf >= MIN_PAIR_CONF:
            co.append({"a": a, "b": b, "count": n, "conf": round(conf, 2)})
    co.sort(key=lambda c: (-c["count"], -c["conf"], c["a"], c["b"]))
    res = {"commits_mined": len(commits), "hotspots": hot[:10], "cochange": co[:60], "no_touch": detect_no_touch(root)}
    p = root / ".devmem/history.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(res, indent=1), encoding="utf-8")
    return res


def render_history_md(h: dict) -> str:
    lines = ["# History insights", f"From the last {h['commits_mined']} commits. Hotspots are files that change a lot and "
             "attract bug fixes: change carefully, keep edits minimal, run their tests.", ""]
    if h["hotspots"]:
        lines += ["## Hotspots", "| File | Commits | Bug fixes | Authors | Last change |", "| --- | --- | --- | --- | --- |"]
        for x in h["hotspots"]:
            cxn = f" (complexity {x['complexity']})" if x["complexity"] else ""
            lines.append(f"| `{x['file']}`{cxn} | {x['commits']} | {x['bugfix']} | {x['authors']} | {x['last']} |")
    if h["cochange"]:
        lines += ["", "## Usually change together", "If you edit one, check whether the other needs the same change."]
        lines += [f"- `{c['a']}` + `{c['b']}` ({c['count']} commits)" for c in h["cochange"][:10]]
    if h["no_touch"]:
        lines += ["", "No-touch paths detected: " + ", ".join(f"`{p}`" for p in h["no_touch"][:10])]
    return "\n".join(lines)
