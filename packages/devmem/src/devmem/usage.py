"""Token accounting for agent queries. Each read command logs what devmem printed and what reading the whole files its answer
points to would have cost, to `.devmem/usage.jsonl` (gitignored, local). `devmem report` folds the totals since the previous
report into the PR record, so the numbers reach the repo with the PR. Tokens are estimated as chars / 4 on both sides."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

LOG = ".devmem/usage.jsonl"
LOGGED = {"find", "show", "outline", "impact", "recall", "reuse", "docs", "helpers", "tests-for", "hygiene", "plan", "gaps", "review"}
BASELINE = {"find", "show", "outline", "impact", "recall", "reuse", "docs", "helpers", "hygiene"}   # answers that replace reading files
_PATH = re.compile(r"[\w@.\-/]+\.[A-Za-z0-9]+")


def record(ix, cmd: str, out: str, via: str = "cli", args: str = "") -> None:
    files = ix.data["files"]
    refs = {m for m in _PATH.findall(out) if m in files}
    base = sum((ix.root / f).stat().st_size for f in refs if (ix.root / f).exists()) // 4 if cmd in BASELINE else 0
    log = ix.root / LOG
    gi = ix.root / ".devmem/.gitignore"
    if "usage.jsonl" not in (gi.read_text(encoding="utf-8") if gi.exists() else ""):
        with gi.open("a", encoding="utf-8") as f:
            f.write("usage.jsonl\n")
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "cmd": cmd, "via": via, "args": args[:160],
                            "out_tokens": len(out) // 4, "file_tokens": base, "files": len(refs)}) + "\n")


def sessions(root: Path, gap_min: int = 30, limit: int = 10) -> list[dict]:
    """Recent query sessions (a pause of `gap_min` minutes starts a new one), newest first, with waste flags:
    the same query repeated, and single answers over 2,000 tokens."""
    p = Path(root) / LOG
    rows = [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()] if p.exists() else []
    out, cur, last = [], None, None
    for r in rows:
        t = time.mktime(time.strptime(r["ts"], "%Y-%m-%dT%H:%M:%SZ"))
        if cur is None or t - last > gap_min * 60:
            cur = {"start": r["ts"], "end": r["ts"], "via": r.get("via", "cli"), "queries": []}
            out.append(cur)
        cur["end"], last = r["ts"], t
        cur["queries"].append(r)
    for s in out:
        seen: dict[str, int] = {}
        for q in s["queries"]:
            key = q["cmd"] + " " + q.get("args", "")
            seen[key] = seen.get(key, 0) + 1
            q["flags"] = (["repeated"] if q.get("args") and seen[key] > 1 else []) + (["large"] if q["out_tokens"] > 2000 else [])
        s["out_tokens"] = sum(q["out_tokens"] for q in s["queries"])
        s["file_tokens"] = sum(q["file_tokens"] for q in s["queries"])
        s["waste"] = sum(1 for q in s["queries"] if q["flags"])
    return out[::-1][:limit]


def summary(root: Path, since: str | None = None) -> dict:
    """Totals of the log (entries after `since`, an ISO timestamp). `saved` compares against reading the referenced files."""
    p = Path(root) / LOG
    rows = [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()] if p.exists() else []
    rows = [r for r in rows if not since or r["ts"] > since]
    out, files = sum(r["out_tokens"] for r in rows), sum(r["file_tokens"] for r in rows)
    by_cmd: dict[str, int] = {}
    for r in rows:
        by_cmd[r["cmd"]] = by_cmd.get(r["cmd"], 0) + 1
    return {"calls": len(rows), "out_tokens": out, "file_tokens": files, "saved": max(files - out, 0),
            "by_cmd": by_cmd, "first": rows[0]["ts"] if rows else None, "last": rows[-1]["ts"] if rows else None}


if __name__ == "__main__":   # self-check: summary math
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / ".devmem").mkdir()
        (Path(d) / LOG).write_text('{"ts":"2026-01-01T00:00:00Z","cmd":"show","out_tokens":100,"file_tokens":900}\n'
                                   '{"ts":"2026-01-02T00:00:00Z","cmd":"find","out_tokens":50,"file_tokens":20}\n', encoding="utf-8")
        s = summary(Path(d))
        assert (s["calls"], s["out_tokens"], s["file_tokens"], s["saved"]) == (2, 150, 920, 770)
        assert summary(Path(d), since="2026-01-01T12:00:00Z")["calls"] == 1
        print("ok")
