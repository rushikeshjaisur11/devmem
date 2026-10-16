"""devmem-app — onboarding, indexing, cross-repo search (UI/publisher/chat come in later steps)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from devmem.index import Index

from .indexer import index_repo

WORKSPACE = Path.home() / ".devmem" / "workspace.json"


def _ws() -> dict:
    return json.loads(WORKSPACE.read_text()) if WORKSPACE.exists() else {"repos": {}}


def _save(ws: dict):
    WORKSPACE.parent.mkdir(parents=True, exist_ok=True)
    WORKSPACE.write_text(json.dumps(ws, indent=1))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="devmem-app")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("add", help="onboard a repo (local clone path)"); s.add_argument("path"); s.add_argument("--name")
    s = sub.add_parser("index", help="(re)index a path, or all onboarded repos"); s.add_argument("path", nargs="?")
    s = sub.add_parser("find", help="search across all onboarded repos"); s.add_argument("query")
    s = sub.add_parser("publish", help="write .devmem/knowledge + AGENTS.md block into a repo")
    s.add_argument("path"); s.add_argument("--commit", action="store_true", help="commit on branch devmem/knowledge-sync")
    a = p.parse_args(argv)

    if a.cmd == "add":
        root = Path(a.path).resolve()
        ws = _ws()
        ws["repos"][a.name or root.name] = str(root)
        _save(ws)
        idx = index_repo(root, a.name)
        print(f"onboarded {a.name or root.name}: {len(idx['symbols'])} symbols, {len(idx['edges'])} edges {idx['stats']}")
    elif a.cmd == "index":
        targets = {Path(a.path).name: a.path} if a.path else _ws()["repos"]
        for name, path in targets.items():
            idx = index_repo(path, name)
            print(f"{name}: {len(idx['symbols'])} symbols, {len(idx['edges'])} edges {idx['stats']}")
    elif a.cmd == "publish":
        from .publisher import commit_branch, publish
        idx = index_repo(a.path)
        files = publish(a.path, idx)
        print(f"published {len(files)} files")
        if a.commit:
            print("committed on", commit_branch(a.path))
    elif a.cmd == "find":
        for name, path in _ws()["repos"].items():
            try:
                ix = Index.load(Path(path))
            except FileNotFoundError:
                continue
            for score, sym in ix.find(a.query, 5):
                print(f"[{name}] {sym['file']}:{sym['line']}  {sym['qualname']}  ({score:.1f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
