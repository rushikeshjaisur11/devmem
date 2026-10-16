"""devmem — agent-facing CLI. Reads .devmem/index.json; no network, no LLM."""
from __future__ import annotations

import argparse
import sys

from .index import Index


def _line(s: dict) -> str:
    return f"{s['file']}:{s['line']}  {s['kind']} {s['qualname']}" + (f" — {s['doc']}" if s["doc"] else "")


def cmd_find(ix: Index, a) -> int:
    hits = ix.find(a.query, a.limit)
    for _, s in hits:
        print(_line(s))
    if not hits:
        print("no matches")
    return 0


def cmd_show(ix: Index, a) -> int:
    syms = ix.resolve(a.symbol)
    if not syms:
        print(f"symbol not found: {a.symbol}")
        return 1
    if len(syms) > 1:
        print("ambiguous, use one of:")
        for s in syms:
            print(f"  {s['id']}")
        return 1
    s = syms[0]
    print(f"# {s['file']}:{s['line']}-{s['end_line']}")
    print(ix.source(s))
    callees = ix.callees(s)
    if callees:
        print("\n# calls")
        for c in callees:
            print(f"{c['file']}:{c['line']}  {c['sig']}")
    return 0


def cmd_outline(ix: Index, a) -> int:
    syms = ix.outline(a.file)
    if not syms:
        print(f"no symbols indexed for {a.file}")
        return 1
    for s in syms:
        indent = "    " if s["kind"] == "method" else ""
        print(f"{indent}{s['line']}: {s['sig']}" + (f"  # {s['doc']}" if s["doc"] else ""))
    return 0


def cmd_impact(ix: Index, a) -> int:
    from . import lineage
    lin = lineage.load(ix.root)
    if lin and not ix.resolve(a.target) and a.target not in lineage.known_tables(lin):
        sc = lineage.split_column(lin, a.target)
        if sc:
            print(lineage.format_impact(lineage.column_impact(lin, *sc)))
            return 0
    r = ix.impact(a.target)
    if "table" in r:
        print(f"{r['table']}\n  readers: {', '.join(r['readers']) or '-'}\n  writers: {', '.join(r['writers']) or '-'}")
        return 0
    print("targets: " + ", ".join(r["targets"]))
    print("hop 1 (must check): " + (", ".join(f"{i['id']} [{i['conf']}]" for i in r["hop1"]) or "-"))
    print("hop 2 (contract check): " + (", ".join(i["id"] for i in r["hop2"]) or "-"))
    print("tests: " + (", ".join(r["tests"]) or "-"))
    for c in r["capped"]:
        print(f"capped: {c['symbol']} has {c['callers']} callers (widely used utility)")
    print("files: " + ", ".join(r["files"]))
    if r.get("cochange"):
        print("usually changes together with: " + ", ".join(r["cochange"]))
    return 0


def cmd_helpers(ix: Index, a) -> int:
    for s, n in ix.helpers(a.limit):
        print(f"{s['file']}:{s['line']}  {s['sig']}  [used in {n} files]" + (f" — {s['doc']}" if s["doc"] else ""))
    return 0


def cmd_diffcheck(ix, a) -> int:
    import os
    from . import diffcheck as dc
    root = dc.repo_root()
    cfg = dc.load_config(root)
    files = dc.collect_diff(root, a.base, a.staged)
    text = os.environ.get("DEVMEM_PR_BODY", "")
    for fpath in a.allow_file or []:
        text += "\n" + open(fpath, encoding="utf-8").read()
    if a.base:
        text += "\n" + dc._git(root, "log", f"{a.base}..HEAD", "--format=%B", check=False)
    blocked, passed = dc.apply_allow(dc.run_checks(root, files, cfg, a.base), dc.allowances(text))
    for x in passed:
        print(f"ALLOWED [{x.check}] {x.path}: {x.msg}  (devmem-allow: {x.allowed})")
    for x in blocked:
        print(f"FAIL    [{x.check}] {x.path}: {x.msg}")
    if blocked:
        print(f"\n{len(blocked)} violation(s). Fix them, or if the larger change is truly required, add a line "
              "`devmem-allow: <check> <reason>` to the PR description.")
        return 1
    print(f"diffcheck ok: {len(files)} files, {sum(f.added + f.removed for f in files)} changed lines")
    return 0


def cmd_report(ix, a) -> int:
    from . import diffcheck as dc
    from .report import build_record, write_record
    root = dc.repo_root()
    rec = build_record(root, a.base, a.task or "", a.tokens, a.recalls, a.rounds)
    path = write_record(root, rec)
    v = rec["violations"]
    print(f"recorded {path.relative_to(root).as_posix()}: {rec['files']} files, +{rec['added']}/-{rec['removed']}, "
          f"{len(v['blocked'])} blocked, {len(v['allowed'])} allowed. Commit it with the PR.")
    return 0


def _changed_paths(a) -> list[str]:
    if a.files:
        return a.files
    from . import diffcheck as dc
    return [f.path for f in dc.collect_diff(dc.repo_root(), a.base, False) if f.status != "D"]


def cmd_tests_for(ix: Index, a) -> int:
    from .check import tests_for
    ids, cmd = tests_for(ix, _changed_paths(a))
    if not ids:
        print("no mapped tests; run the full suite (see .devmem/knowledge/commands.md)")
        return 0
    print(cmd)
    return 0


def cmd_check(ix: Index, a) -> int:
    from .check import run_check
    paths = _changed_paths(a)
    if not paths:
        print("no changed files")
        return 0
    return run_check(ix, paths)


def cmd_hygiene(ix: Index, a) -> int:
    import json
    p = ix.root / ".devmem/hygiene.json"
    if not p.exists():
        print("no hygiene data (run `devmem-app hygiene <repo>`)")
        return 0
    issues = [i for i in json.loads(p.read_text(encoding="utf-8"))["issues"] if i["path"] == a.file]
    for i in sorted(issues, key=lambda i: i["line"]):
        print(f"{i['path']}:{i['line']}  {i['tool']} {i['code']} {i['msg']}")
    if not issues:
        print("no known hygiene issues")
    return 0


def cmd_recall(ix: Index, a) -> int:
    from . import memory
    print(memory.format_recall(memory.recall(ix, a.task, a.files, a.limit)))
    return 0


def cmd_remember(ix: Index, a) -> int:
    import sys as _sys
    from . import memory
    problem, fix = a.problem, a.fix
    if (not problem or not fix) and not _sys.stdin.isatty():
        data = _sys.stdin.read()
        problem = problem or memory.section("## Problem\n" + data.split("## Fix")[0].replace("## Problem", ""), "Problem")
        fix = fix or (data.split("## Fix", 1)[1].strip() if "## Fix" in data else "")
    refs = [memory.normalize_ref(r, ix.data["repo"], ix.data.get("commit")) for r in a.ref or []]
    try:
        status, lid = memory.write_lesson(ix.root, a.title, problem or "", fix or "", ix.data["repo"], a.tag or [], refs,
                                          a.cmd or [], a.source, a.force)
    except ValueError as e:
        print(f"not saved: {e}")
        return 2
    if status == "duplicate":
        print(f"similar lesson already exists: {lid} (use --force to add anyway)")
        return 0
    print(f"saved .devmem/lessons/{lid}.md; commit it with your PR")
    return 0


def cmd_feedback(ix: Index, a) -> int:
    from . import memory
    memory.add_feedback(ix.root, a.lesson, a.verdict, a.note or "")
    print("recorded")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="devmem", description="Code knowledge for coding agents")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("find", help="ranked path:line hits for a concept")
    s.add_argument("query"); s.add_argument("-n", "--limit", type=int, default=10); s.set_defaults(fn=cmd_find)
    s = sub.add_parser("show", help="one symbol's body + signatures of what it calls")
    s.add_argument("symbol"); s.set_defaults(fn=cmd_show)
    s = sub.add_parser("outline", help="signatures and docstrings of a file")
    s.add_argument("file"); s.set_defaults(fn=cmd_outline)
    s = sub.add_parser("impact", help="callers, dependents, tests for a symbol or table")
    s.add_argument("target"); s.set_defaults(fn=cmd_impact)
    s = sub.add_parser("helpers", help="reusable utilities already in the repo")
    s.add_argument("-n", "--limit", type=int, default=30); s.set_defaults(fn=cmd_helpers)
    for nm, fn, hp in (("tests-for", cmd_tests_for, "test command for tests affected by changed files"),
                       ("check", cmd_check, "lint/types/affected tests on changed files, compact output")):
        s = sub.add_parser(nm, help=hp)
        s.add_argument("files", nargs="*", help="default: files changed vs HEAD")
        s.add_argument("--base", help="changed files in BASE...HEAD instead")
        s.set_defaults(fn=fn)
    s = sub.add_parser("recall", help="lessons, rules and cross-repo pointers for a task")
    s.add_argument("task"); s.add_argument("--files", nargs="*"); s.add_argument("-n", "--limit", type=int, default=3)
    s.set_defaults(fn=cmd_recall)
    s = sub.add_parser("remember", help="save a lesson (problem + fix) as a file for your PR")
    s.add_argument("title"); s.add_argument("--problem"); s.add_argument("--fix")
    s.add_argument("--tag", action="append"); s.add_argument("--ref", action="append", help="path#symbol")
    s.add_argument("--cmd", action="append", help="command that verifies or applies the fix")
    s.add_argument("--source", default="agent"); s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_remember)
    s = sub.add_parser("feedback", help="was a recalled lesson useful?")
    s.add_argument("lesson"); s.add_argument("verdict", choices=["useful", "useless"]); s.add_argument("--note")
    s.set_defaults(fn=cmd_feedback)
    s = sub.add_parser("hygiene", help="known lint/dead-code/complexity issues in a file")
    s.add_argument("file"); s.set_defaults(fn=cmd_hygiene)
    s = sub.add_parser("diffcheck", help="minimal-change violations in the current diff")
    s.add_argument("--staged", action="store_true", help="check staged changes only (pre-commit)")
    s.add_argument("--base", help="compare BASE...HEAD (CI)")
    s.add_argument("--allow-file", action="append", help="file whose devmem-allow: lines apply (e.g. PR body)")
    s.set_defaults(fn=cmd_diffcheck, nolog=True)
    s = sub.add_parser("report", help="record this PR's diff size and diffcheck result in .devmem/metrics/runs (commit it)")
    s.add_argument("--base"); s.add_argument("--task"); s.add_argument("--tokens", type=int, help="tokens/ACUs the agent knows it used")
    s.add_argument("--recalls", type=int); s.add_argument("--rounds", type=int, help="review rounds so far")
    s.set_defaults(fn=cmd_report, nolog=True)
    a = p.parse_args(argv)
    if a.cmd in ("diffcheck", "report"):
        try:
            ix = Index.load()
        except FileNotFoundError:
            ix = None
        return a.fn(ix, a)
    try:
        ix = Index.load()
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 2
    return a.fn(ix, a)


if __name__ == "__main__":
    sys.exit(main())
