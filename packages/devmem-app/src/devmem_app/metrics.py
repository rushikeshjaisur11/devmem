"""PR/agent metrics from what a repo already contains: git history, `devmem report` records, and an optional Devin
session export. Compares agent changes before vs after devmem was added. No network."""
import json
import re
import statistics
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml
from devmem.report import RUNS_DIR

from .paths import devmem_home

AGENT_RE = re.compile(r"devin|copilot|\[bot\]|co-authored-by:.*(devin|copilot|claude)", re.I)
SKIP = re.compile(r"(^\.devmem/|(^|/)(package-lock\.json|poetry\.lock|uv\.lock|yarn\.lock|pnpm-lock\.yaml)$)")


def _git(root: Path, *a: str) -> str:
    r = subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout if r.returncode == 0 else ""


def _cfg(root: Path) -> dict:
    p = root / ".devmem/config.yaml"
    return ((yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("metrics") or {}) if p.exists() else {}


def collect(root: Path, limit: int = 300) -> list[dict]:
    """Newest-first commits with size and an `agent` flag."""
    cfg = _cfg(root)
    rx = re.compile(cfg["agent_pattern"], re.I) if cfg.get("agent_pattern") else AGENT_RE
    out = _git(root, "log", f"-{limit}", "--first-parent", "--no-merges", "--numstat",
               "--format=%x1e%H%x1f%an <%ae>%x1f%aI%x1f%s%x1f%b%x1f")
    recs = []
    for block in out.split("\x1e")[1:]:
        parts = block.split("\x1f")
        if len(parts) < 6:
            continue
        sha, author, when, subj, body, stat = parts[0], parts[1], parts[2], parts[3], parts[4], parts[5]
        files = lines = 0
        for ln in stat.strip().splitlines():
            a = ln.split("\t")
            if len(a) == 3 and not SKIP.search(a[2]):
                files += 1
                lines += (int(a[0]) if a[0].isdigit() else 0) + (int(a[1]) if a[1].isdigit() else 0)
        if files == 0:
            continue
        recs.append({"sha": sha[:8], "author": author, "when": when, "subject": subj[:100], "files": files,
                     "lines": lines, "agent": bool(rx.search(f"{author}\n{body}")),
                     "revert": subj.lower().startswith("revert")})
    return recs


def split_date(root: Path) -> str | None:
    s = _cfg(root).get("since")
    if s:
        return str(s)
    out = _git(root, "log", "--diff-filter=A", "--format=%aI", "--", ".devmem/config.yaml").strip().splitlines()
    return out[-1] if out else None


def _stats(rs: list[dict], budget: int) -> dict:
    if not rs:
        return {"n": 0}
    lines = sorted(r["lines"] for r in rs)
    return {"n": len(rs), "median_lines": statistics.median(lines), "p90_lines": lines[min(len(lines) - 1, int(len(lines) * 0.9))],
            "median_files": statistics.median(r["files"] for r in rs),
            "within_budget": round(100 * sum(1 for l in lines if l <= budget) / len(lines)),
            "reverts": sum(r["revert"] for r in rs)}


def _dt(s: str) -> datetime:
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def load_runs(root: Path) -> list[dict]:
    d = root / RUNS_DIR
    return [json.loads(f.read_text(encoding="utf-8")) for f in sorted(d.glob("*.json"))] if d.exists() else []


# ---- Devin export (offline): tolerant of field naming, since the export format may vary ----
def _first(d: dict, *keys):
    for k in keys:
        if d.get(k) is not None:
            return d[k]
    return None


def ingest_devin(path: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = data if isinstance(data, list) else (_first(data, "sessions", "items", "data") or [])
    f = devmem_home() / "hub/metrics/devin-sessions.jsonl"
    f.parent.mkdir(parents=True, exist_ok=True)
    seen = {json.loads(l)["id"] for l in f.read_text().splitlines() if l.strip()} if f.exists() else set()
    new = 0
    with f.open("a", encoding="utf-8") as fh:
        for it in items:
            sid = str(_first(it, "session_id", "id") or "")
            if not sid or sid in seen:
                continue
            prs = it.get("pull_requests") or ([it["pull_request"]] if it.get("pull_request") else [])
            pr = prs[0] if prs else {}
            pr_url = pr.get("pr_url") or pr.get("url") if isinstance(pr, dict) else pr
            rec = {"id": sid, "created": _first(it, "created_at", "createdAt", "created"), "status": _first(it, "status_enum", "status"),
                   "acus": _first(it, "acus_consumed", "acus", "acu"), "pr": pr_url, "title": (_first(it, "title", "task") or "")[:120]}
            fh.write(json.dumps(rec) + "\n"); seen.add(sid); new += 1
    return {"new": new, "total": len(seen)}


def devin_summary(split: str | None) -> dict | None:
    f = devmem_home() / "hub/metrics/devin-sessions.jsonl"
    if not f.exists():
        return None
    rows = [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    def agg(rs):
        a = [float(r["acus"]) for r in rs if r.get("acus") is not None]
        return {"sessions": len(rs), "with_pr": sum(1 for r in rs if r.get("pr")), "acus_median": statistics.median(a) if a else None,
                "acus_total": round(sum(a), 2) if a else None}
    if split:
        sd = _dt(split)
        def after(r):
            try: return _dt(r["created"]) >= sd
            except Exception: return False
        return {"all": agg(rows), "before": agg([r for r in rows if r.get("created") and not after(r)]),
                "after": agg([r for r in rows if after(r)])}
    return {"all": agg(rows)}


def summary(root: Path, recent: int = 30) -> dict:
    from devmem.diffcheck import load_config
    budget = load_config(root)["diff_budget"]["max_changed_lines"]
    recs = collect(root)
    split = split_date(root)
    agent = [r for r in recs if r["agent"]]
    out = {"split": split, "budget": budget, "commits_scanned": len(recs), "agent_commits": len(agent), "recent": recs[:recent]}
    if split:
        sd = _dt(split)
        for label, flag in (("agent", True), ("human", False)):
            rs = [r for r in recs if r["agent"] == flag]
            out[label] = {"before": _stats([r for r in rs if _dt(r["when"]) < sd], budget),
                          "after": _stats([r for r in rs if _dt(r["when"]) >= sd], budget)}
    else:
        out["agent"] = {"all": _stats(agent, budget)}
        out["human"] = {"all": _stats([r for r in recs if not r["agent"]], budget)}
    runs = load_runs(root)
    toks = [r["tokens"] for r in runs if r.get("tokens")]
    from collections import Counter
    blocked = Counter(c for r in runs for c in r["violations"]["blocked"])
    out["runs"] = {"n": len(runs), "median_tokens": statistics.median(toks) if toks else None, "blocked_checks": dict(blocked),
                   "clean": sum(1 for r in runs if not r["violations"]["blocked"]), "recent": runs[-15:][::-1]}
    out["devin"] = devin_summary(split)
    return out
