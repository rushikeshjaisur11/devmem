"""`devmem report`: one JSON record per PR/run, committed with the PR so metrics reach the repo without any egress.
Records diff size and diffcheck outcome; token use is whatever the agent reports (--tokens), never guessed."""
import json
import os
import time
from pathlib import Path

from . import diffcheck as dc

RUNS_DIR = ".devmem/metrics/runs"


def build_record(root: Path, base: str | None = None, task: str = "", tokens: int | None = None,
                 recalls: int | None = None, rounds: int | None = None) -> dict:
    cfg = dc.load_config(root)
    files = dc.collect_diff(root, base, False)
    text = os.environ.get("DEVMEM_PR_BODY", "")
    if base:
        text += "\n" + dc._git(root, "log", f"{base}..HEAD", "--format=%B", check=False)
    blocked, allowed = dc.apply_allow(dc.run_checks(root, files, cfg, base), dc.allowances(text))
    head = dc._git(root, "rev-parse", "--short", "HEAD", check=False).strip()
    return {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "head": head, "task": task[:200],
            "files": len(files), "added": sum(f.added for f in files), "removed": sum(f.removed for f in files),
            "violations": {"blocked": [x.check for x in blocked], "allowed": [x.check for x in allowed]},
            "tokens": tokens, "recalls": recalls, "review_rounds": rounds}


def write_record(root: Path, rec: dict) -> Path:
    d = Path(root) / RUNS_DIR
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{rec['ts'].replace(':', '')}-{rec['head'] or 'nohead'}.json"
    p.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return p
