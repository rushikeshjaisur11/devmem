"""Token accounting for agent queries. Each read command logs what devmem printed and what reading the whole files its answer
points to would have cost, to `.devmem/usage.jsonl` (gitignored, local). `devmem report` folds the totals since the previous
report into the PR record, so the numbers reach the repo with the PR. Tokens are estimated as chars / 4 on both sides."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

LOG = ".devmem/usage.jsonl"
LOGGED = {"find", "show", "outline", "impact", "recall", "reuse", "docs", "helpers", "tests-for", "hygiene", "plan"}
_PATH = re.compile(r"[\w@.\-/]+\.[A-Za-z0-9]+")


def record(ix, cmd: str, out: str, via: str = "cli") -> None:
    files = ix.data["files"]
    refs = {m for m in _PATH.findall(out) if m in files}
    base = sum((ix.root / f).stat().st_size for f in refs if (ix.root / f).exists()) // 4
    log = ix.root / LOG
    gi = ix.root / ".devmem/.gitignore"
    if "usage.jsonl" not in (gi.read_text(encoding="utf-8") if gi.exists() else ""):
        with gi.open("a", encoding="utf-8") as f:
            f.write("usage.jsonl\n")
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "cmd": cmd, "via": via,
                            "out_tokens": len(out) // 4, "file_tokens": base, "files": len(refs)}) + "\n")


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
