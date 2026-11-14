"""Keep onboarded repos fresh: when a repo's HEAD moves past the commit its index was built at, re-index it (and optionally
publish). Polls `git rev-parse HEAD`, which is cheap, so it also catches pulls, merges and branch switches made outside devmem."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Callable

from .indexer import AccuracyError, git_head, index_repo

ERRORS: dict[str, str] = {}   # repo -> why the last automatic re-index failed (shown as an alert in the UI)


def stale(root: Path) -> str | None:
    """The new HEAD when the index is behind it, else None."""
    ip = root / ".devmem/index.json"
    head = git_head(root)
    if not head or not ip.exists():
        return None
    return head if json.loads(ip.read_text(encoding="utf-8")).get("commit") != head else None


def tick(repos: dict[str, str], publish_too: bool, failed: dict[str, str], log: Callable[[str], None] = print) -> list[str]:
    """One pass: refresh every stale repo. A HEAD that failed once is not retried until HEAD moves again."""
    done = []
    for name, path in repos.items():
        root = Path(path)
        head = stale(root)
        if not head or failed.get(name) == head:
            continue
        try:
            idx = index_repo(root, name)
            if publish_too:
                from .publisher import publish
                publish(root, idx, repos)
        except AccuracyError as e:
            failed[name], ERRORS[name] = head, f"not re-indexed at {head[:8]}: {e}"
            log(f"{name}: not re-indexed at {head[:8]}: {e}")
            continue
        except Exception as e:   # anything else (a locked repo, a disk error) must not end the watcher: record it and move on
            from .paths import diagnose
            failed[name], ERRORS[name] = head, f"not re-indexed at {head[:8]}: {type(e).__name__}: {e}"
            diagnose("index", f"re-index failed: {type(e).__name__}: {e}", name)
            log(f"{name}: re-index failed: {e}")
            continue
        failed.pop(name, None)
        ERRORS.pop(name, None)
        done.append(name)
        log(f"{name}: re-indexed at {head[:8]} ({idx['stats']['parsed']} files parsed){', published' if publish_too else ''}")
    return done


def run(repos: Callable[[], dict[str, str]], interval: int = 60, publish_too: bool = False, stop: threading.Event | None = None) -> None:
    """Loop until `stop` is set. `repos` is re-read every pass so repos added later are watched too."""
    stop = stop or threading.Event()
    failed: dict[str, str] = {}
    while not stop.is_set():
        tick(repos(), publish_too, failed)
        stop.wait(interval)


def start(repos: Callable[[], dict[str, str]], interval: int = 60, publish_too: bool = False) -> threading.Event:
    """Background thread (on by default in `devmem-app ui`). Returns the event that stops it.
    ponytail: no lock against a Re-index pressed in the UI at the same moment; both write the same index.json, last one wins."""
    stop = threading.Event()
    threading.Thread(target=run, args=(repos, interval, publish_too, stop), daemon=True, name="devmem-watch").start()
    return stop
