"""devmem-app — onboarding, indexing, cross-repo search (UI/publisher/chat come in later steps)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from devmem.index import Index

from .indexer import index_repo
from .paths import devmem_home



def _workspace() -> Path:
    return devmem_home() / "workspace.json"


def _ws() -> dict:
    w = _workspace()
    return json.loads(w.read_text()) if w.exists() else {"repos": {}}


def _save(ws: dict):
    w = _workspace()
    w.parent.mkdir(parents=True, exist_ok=True)
    w.write_text(json.dumps(ws, indent=1))


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
    s = sub.add_parser("memory", help="lessons/rules: sync, share, to-rule, ingest-reviews, ingest-failures, proposals, approve, reject")
    s.add_argument("action", choices=["sync", "share", "to-rule", "ingest-reviews", "ingest-failures", "proposals", "approve", "reject"])
    s.add_argument("arg", nargs="?", help="lesson id / proposal id / json file / repo path")
    s.add_argument("--repos", help="comma-separated repo names (share)"); s.add_argument("--all", action="store_true")
    s.add_argument("--applies-to", default="**"); s.add_argument("--repo", help="repo name to write the rule/lesson into")
    s.add_argument("--fix", help="fix text (approving a lesson proposal)"); s.add_argument("--severity", default="rule")
    s = sub.add_parser("ui", help="start the local web app")
    s.add_argument("--port", type=int, default=8765); s.add_argument("--no-open", action="store_true")
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
    elif a.cmd == "memory":
        from . import memory as mem
        repos = _ws()["repos"]
        tgt = Path(repos[a.repo]) if a.repo else None
        if a.action == "sync":
            print(mem.sync(repos))
        elif a.action == "share":
            print("shared with", mem.share(a.arg, (a.repos or "").split(",") if a.repos else None, a.all))
        elif a.action == "to-rule":
            print("rule written:", mem.lesson_to_rule(a.arg, a.applies_to, tgt, a.severity))
        elif a.action == "ingest-reviews":
            for p in mem.propose_from_reviews(a.arg):
                print(f"proposed {p['id']} ({p['count']} comments in {p['prs']} PRs, {p['applies_to']}): {p['text']}")
        elif a.action == "ingest-failures":
            for p in mem.propose_from_failures(a.arg):
                print(f"proposed {p['id']} ({p['count']}x): {p['text']}")
        elif a.action == "proposals":
            for p in mem.list_proposals():
                print(f"{p['id']}  [{p['kind']}] {p['text']}")
        elif a.action == "approve":
            print("written:", mem.approve(a.arg, tgt, a.severity, a.fix))
        elif a.action == "reject":
            mem.reject(a.arg); print("rejected")
    elif a.cmd == "ui":
        import threading
        import webbrowser

        import uvicorn
        if not a.no_open:
            threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{a.port}")).start()
        uvicorn.run("devmem_app.server:app", host="127.0.0.1", port=a.port, log_level="warning")
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
