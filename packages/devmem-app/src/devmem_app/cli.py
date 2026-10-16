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
    s = sub.add_parser("verify", help="run detected install/lint/test commands and record the result")
    s.add_argument("path"); s.add_argument("--include-install", action="store_true")
    s = sub.add_parser("flaky", help="rerun the suite to find flaky tests -> .devmem/flaky.txt")
    s.add_argument("path"); s.add_argument("--runs", type=int, default=3)
    s = sub.add_parser("hygiene", help="run ruff/vulture/radon/deptry/bandit/sqlfluff + clones, publish hygiene.md")
    s.add_argument("path")
    s = sub.add_parser("impact-column", help="who is affected if table.column changes, across all onboarded repos")
    s.add_argument("column", help="db.table.column")
    s = sub.add_parser("schema", help="schema snapshots"); s.add_argument("action", choices=["import"])
    s.add_argument("repo"); s.add_argument("csv", help="CSV with columns table,column[,type]")
    s = sub.add_parser("clones", help="token-level duplicate code across all onboarded repos")
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
        files = publish(a.path, idx, _ws()["repos"])
        print(f"published {len(files)} files")
        if a.commit:
            print("committed on", commit_branch(a.path))
    elif a.cmd == "verify":
        from . import commands
        idx = index_repo(a.path)
        for k, v in commands.verify(a.path, idx, a.include_install).items():
            print(f"{k}: {v['cmd']}  verified={v.get('verified')} {v.get('note', '')}")
    elif a.cmd == "flaky":
        from . import commands
        print("flaky:", commands.detect_flaky(a.path, a.runs) or "none")
    elif a.cmd == "hygiene":
        from . import hygiene
        from .publisher import publish
        idx = index_repo(a.path)
        h = hygiene.run_hygiene(a.path, idx)
        publish(a.path, idx, _ws()["repos"])
        print("hygiene:", h["counts"], {k: v for k, v in h["tools"].items() if v != "ok"} or "all tools ran")
    elif a.cmd == "impact-column":
        from devmem.lineage import column_impact, format_impact, split_column
        from .lineage import combine
        lin = combine({n: Path(p) for n, p in _ws()["repos"].items()})
        sc = split_column(lin, a.column)
        print(format_impact(column_impact(lin, *sc)) if sc else f"unknown column {a.column}")
    elif a.cmd == "schema":
        from .lineage import import_schema_csv
        print(f"imported {import_schema_csv(Path(a.repo), Path(a.csv))} columns")
    elif a.cmd == "clones":
        from .clones import clones_for_repo, find_clones
        srcs = {}
        for name, path in _ws()["repos"].items():
            idx = index_repo(path, name, write=False)
            for f in idx["files"]:
                if f.endswith(".py") and "/tests/" not in f"/{f}":
                    srcs[f"{name}:{f}"] = (Path(path) / f).read_text(encoding="utf-8", errors="replace")
        for c in find_clones(srcs):
            if c["a"].split(":")[0] != c["b"].split(":")[0]:
                print(f"{c['a']}:{c['a_lines'][0]}-{c['a_lines'][1]}  ~  {c['b']}:{c['b_lines'][0]}-{c['b_lines'][1]}  ({c['tokens']} tokens)")
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
