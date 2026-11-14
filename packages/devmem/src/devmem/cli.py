"""devmem — agent-facing CLI. Reads .devmem/index.json; no network, no LLM."""
from __future__ import annotations

import argparse
import sys

from .index import Index


SHOW_LINES = 80     # default body lines for `show`; longer bodies end with a hint instead of flooding the context
LIST_CAP = 12       # default items per impact list
OUTLINE_CAP = 60    # default lines for `outline`; a larger file lists its classes with a method count instead of every method
MAX_TOKENS = 2500   # no answer is longer than this unless the agent asked for everything (--full / --all); DEVMEM_MAX_TOKENS changes it


def _line(s: dict) -> str:
    return f"{s['file']}:{s['line']}  {s['kind']} {s['qualname']}" + (f" — {s['doc'][:100]}" if s["doc"] else "")


def cmd_find(ix: Index, a) -> int:
    from .context import distinct, hit_line
    hits = distinct(ix, ix.find(a.query, max(40, a.limit * 4)), a.limit)   # the same definition copied across files is one hit
    for s, twins in hits:   # code without a docstring shows its written description instead, when it has one
        print(hit_line(ix, s, twins))
    if not hits:
        print("no matches: try other or fewer words, or `devmem outline <file>`")
    return 0


def cmd_show(ix: Index, a) -> int:
    syms = ix.resolve(a.symbol)
    if not syms:
        print(f"symbol not found: {a.symbol}; `devmem find \"<words>\"` lists ids (path#qualname)")
        return 1
    if len(syms) > 1:
        print("ambiguous, use one of:")
        for s in syms:
            print(f"  {s['id']}")
        return 1
    s = syms[0]
    print(f"# {s['file']}:{s['line']}-{s['end_line']}")
    body = ix.source(s).splitlines()
    if not a.full and len(body) > SHOW_LINES:
        print("\n".join(body[:SHOW_LINES]))
        print(f"... {len(body) - SHOW_LINES} more lines: `devmem show {s['id']} --full`, or read {s['file']} "
              f"from line {s['line'] + SHOW_LINES}")
    else:
        print("\n".join(body))
    callees = ix.callees(s)
    if callees:
        print("\n# calls")
        for c in callees:
            print(f"{c['file']}:{c['line']}  {c['sig']}")
    from . import docs
    libs = docs.for_code(ix, [s["file"]], "\n".join(body), s["name"])
    if libs:
        print("\n# library docs for this code (latest on file: follow them over older patterns in the repo)")
        for d in libs:
            print(docs.render_pointer(d))
    return 0


def cmd_outline(ix: Index, a) -> int:
    syms = ix.outline(a.file)
    if not syms:
        print(f"no symbols indexed for {a.file}")
        return 1
    row = lambda s: f"{'    ' if s['kind'] == 'method' else ''}{s['line']}: {s['sig']}" + (f"  # {s['doc'][:90]}" if s["doc"] else "")
    inside = getattr(a, "inside", None)
    if inside:
        syms = [s for s in syms if s["qualname"] == inside or s["qualname"].startswith(inside + ".")]
        if not syms:
            print(f"no class or function named {inside} in {a.file}")
            return 1
    if a.all or len(syms) <= OUTLINE_CAP:
        print("\n".join(row(s) for s in syms))
        return 0
    # too long to print whole: top-level definitions only, each class with its method count; then cut at the cap
    methods: dict[str, int] = {}
    for s in syms:
        if s["kind"] == "method":
            methods[s["qualname"].split(".")[0]] = methods.get(s["qualname"].split(".")[0], 0) + 1
    top = [s for s in syms if s["kind"] != "method"] if not inside else syms
    for s in top[:OUTLINE_CAP]:
        n = methods.get(s["qualname"], 0) if not inside else 0
        print(row(s) + (f"  [{n} methods: `devmem outline {a.file} --in {s['qualname']}`]" if n else ""))
    if len(top) > OUTLINE_CAP:
        print(f"... {len(top) - OUTLINE_CAP} more: `devmem find \"<words>\"` to locate one, or `devmem outline {a.file} --all`")
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
    from .context import grouped
    print("targets: " + ", ".join(r["targets"]))
    cap = 0 if a.all else LIST_CAP
    # callers are grouped by file (the path once, not once per caller); only edges that need checking carry a tag
    unsure = {i["id"]: i["conf"] for i in r["hop1"] if i["conf"] in ("ambiguous", "co-change")}
    print("hop 1 (must check): " + grouped([i["id"] for i in r["hop1"]], cap, unsure))
    print("hop 2 (contract check): " + grouped([i["id"] for i in r["hop2"]], cap))
    print("tests: " + grouped(r["tests"], cap))
    for c in r["capped"]:
        print(f"capped: {c['symbol']} has {c['callers']} callers (widely used utility)")
    if r.get("cochange"):
        print("usually changes together with: " + ", ".join(r["cochange"]))
    return 0


def cmd_helpers(ix: Index, a) -> int:
    for s, n in ix.helpers(a.limit):
        print(f"{s['file']}:{s['line']}  {s['sig']}  [{n} files]" + (f" — {s['doc'][:80]}" if s["doc"] else ""))
    return 0


def cmd_context(ix: Index, a) -> int:
    from . import context
    print(context.build(ix, a.task, a.files, a.budget, a.symbol))
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


def cmd_review(ix, a) -> int:
    import json as _json
    import os
    from . import diffcheck as dc
    from . import review
    root = dc.repo_root()
    files = dc.collect_diff(root, a.base, False)
    text = os.environ.get("DEVMEM_PR_BODY", "") + ("\n" + dc._git(root, "log", f"{a.base}..HEAD", "--format=%B", check=False) if a.base else "")
    blocked, allowed = dc.apply_allow(dc.run_checks(root, files, dc.load_config(root), a.base), dc.allowances(text))
    r = review.build(ix, files, blocked, allowed)
    print(_json.dumps(r, indent=1) if a.json else review.render_md(r))
    return 0


def cmd_gaps(ix, a) -> int:
    from . import review
    rows = review.gaps(ix, a.limit)
    for g in rows:
        print(f"{g['file']}:{g['line']}  {g['id'].split('#', 1)[1]}  [{g['callers']} callers in {g['files']} file{'s' if g['files'] != 1 else ''}, no test reaches it]")
    print("no untested code with callers found" if not rows else "write a test for the top ones first: `devmem show <symbol>` shows the code")
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
    u = rec["devmem"]
    if u["calls"]:
        print(f"devmem queries this run: {u['calls']}, {u['out_tokens']} tokens of answers instead of ~{u['file_tokens']} tokens of the files they point to")
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
    """Known problems in a file: devmem's own checks, and SonarQube's findings when the repo is synced with it."""
    import json
    issues, seen_any = [], False
    for rel, keys in ((".devmem/hygiene.json", ("issues",)), (".devmem/sonar.json", ("issues", "hotspots"))):
        p = ix.root / rel
        if not p.exists():
            continue
        seen_any = True
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        issues += [i for k in keys for i in d.get(k, []) if i.get("path") == a.file]
    try:   # hand-written code the language or a library provides (Modernize page): only what can be used right now
        mz = json.loads((ix.root / ".devmem/modernize.json").read_text(encoding="utf-8"))
        seen_any = True
        issues += [{"tool": "modernize", "path": f["file"], "line": f["line"], "code": f["rule"], "msg": f"hand-written {f['what']}; use {f['use']}"}
                   for f in mz.get("findings", []) if f.get("file") == a.file and f.get("status") == "ready" and (f.get("ai") or {}).get("verdict") != "no"]
    except (OSError, ValueError):
        pass
    if not seen_any:
        print("no hygiene data (run `devmem-app hygiene <repo>`)")
        return 0
    order = {"high": 0, "medium": 1, "low": 2}
    for i in sorted(issues, key=lambda i: (order.get(i.get("severity"), 3) if i["tool"] == "sonar" else 3, i["line"])):
        sev = f" [{i['severity']}]" if i["tool"] == "sonar" and i.get("severity") else ""
        print(f"{i['path']}:{i['line']}  {i['tool']} {i['code']}{sev} {i['msg']}")
    if not issues:
        print("no known hygiene issues")
    return 0


def cmd_recall(ix: Index, a) -> int:
    from . import memory
    r = memory.recall(ix, a.task, a.files, a.limit)
    from . import reuse
    hits = [h for h in reuse.reuse(ix, a.task, limit=2) if h["score"] >= 0.5]
    if hits:   # catalog search is far better than the keyword pointers; show it instead
        r["cross_repo"] = []
    print(memory.format_recall(r))
    if hits:
        print("ALSO SOLVED IN OTHER REPOS (`devmem reuse \"<need>\"` for source and how to adapt)")
        for h in hits:
            e = h["entry"]
            print(f" {e['repo']}:{e['file']}:{e['line']}  {e['sig'] or e['name']}  [effort to reuse: {h['fit']['effort']}]")
    from . import docs
    files = list(dict.fromkeys((a.files or []) + [s["file"] for _, s in ix.find(a.task, 5)]))
    libs = docs.for_code(ix, files, query=a.task)
    if libs:
        print("LIBRARY DOCS FOR THIS CODE (latest versions; use these APIs, not older patterns copied from the repo)")
        for d in libs:
            print(" " + docs.render_pointer(d))
            if d["hit"]:
                print("   " + " ".join(d["hit"]["text"].split())[:240])
    return 0


def cmd_reuse(ix: Index, a) -> int:
    from . import reuse
    entries, note = reuse.all_entries(ix.root)
    if note:
        print(f"[{note}]")
    if not entries:
        print("no reuse catalog or hub: onboard the other repos in devmem-app, then `devmem-app publish` this one (or set up a hub)")
        return 0
    hits = reuse.reuse(ix, a.need, entries, limit=a.limit)
    for h in hits:
        print(reuse.render_card(h["score"], h["entry"], h["fit"], a.lines) + "\n")
        if a.prompt:
            print(reuse.port_prompt(h["entry"], h["fit"], ix.data["repo"]) + "\n")
    if not hits:
        print("nothing in other repos matches; write it fresh")
    return 0


def cmd_plan(ix: Index, a) -> int:
    from pathlib import Path as _P
    from . import plan
    f = _P(a.file)
    if not f.exists():
        f = ix.root / f
    if not f.exists():
        print(f"plan file not found: {a.file}")
        return 1
    pl = plan.parse(f.read_text(encoding="utf-8"))
    if not pl["steps"]:
        print("no steps found: use `## Step 1: ...` headings, a numbered list, or `- [ ]` items")
        return 1
    if a.done:
        plan.mark(ix.root, f, a.done, a.note or "")
        print(plan.status(ix.root, f, pl))
    elif a.status or (not a.step and not a.all):
        print(plan.status(ix.root, f, pl))
    else:
        steps = [s["n"] for s in pl["steps"]] if a.all else [a.step]
        for n in steps:
            print(plan.brief(ix, pl, n, a.code) + ("\n" + "-" * 60 + "\n" if len(steps) > 1 else ""))
    return 0


def cmd_docs(ix: Index, a) -> int:
    from . import docs
    libs = docs.libraries(ix.root)
    if a.for_:
        syms = ix.resolve(a.for_)
        files, text = ([syms[0]["file"]], ix.source(syms[0])) if len(syms) == 1 else ([a.for_], "")
        found = docs.for_code(ix, files, text, a.query or "")
        for d in found:
            print(docs.render(d["hit"], a.chars) + "\n" if d["hit"] else docs.render_pointer(d))
        if not found:
            print(f"no docs on file for the libraries {a.for_} uses (`devmem docs --list`); say so rather than guessing an API")
        return 0
    if a.list or not a.query:
        if not libs:
            print("no library docs here: run `devmem-app docs add ...` then `devmem-app publish` (or sync the hub)")
            return 0
        for m in libs:
            age = docs.age_days(m)
            stale = f"  STALE ({age} days old: treat with care)" if age is not None and age > docs.STALE_DAYS else ""
            print(f"{m['name']} {m.get('version', '')}  fetched {m.get('fetched', '?')}  {len(m.get('pages', []))} pages  <- {m.get('source', '')}{stale}")
        return 0
    hits = docs.search(ix.root, a.query, a.limit, a.lib)
    for h in hits:
        print(docs.render(h, a.chars) + "\n")
    if not hits:
        print("no matching documentation offline" + ("" if libs else " (none installed)") + ": say so in the PR rather than guessing an API")
    return 0


def cmd_hub(ix: Index, a) -> int:
    from . import hub
    s = hub.spec(ix.root)
    if not s:
        print("no hub configured (.devmem/hub.txt or DEVMEM_HUB): this repo only has its own committed catalog")
        return 0
    if a.action == "sync":
        d, note = hub.sync(s)
        print(note + (f": {d}" if d else ""))
        return 0 if d else 1
    d, note = hub.resolve(ix.root, auto_sync=a.action != "status")
    if note:
        print(f"[{note}]")
    if not d:
        print(f"hub {s}: not available here (run `devmem hub sync`; needs git access to it)")
        return 1
    if a.action == "status":
        ag = hub.age(d)
        print(f"hub {s}\n  at {d}" + (f"\n  synced {int(ag // 3600)}h ago" if ag is not None else ""))
    if a.action in ("status", "repos"):
        for m in hub.repos(d):
            print(f"  {m['name']}: {m.get('symbols', '?')} symbols, {', '.join(m.get('langs', {})) or '-'}"
                  + (f"  [{m['origin']}]" if m.get("origin") else "") + (f"  @{m['branch']}" if m.get("branch") else ""))
        print(f"  {len(hub.entries(d))} reusable symbols in catalog")
    elif a.action == "show":
        name = a.repo or ""
        for f in ("overview.md", "map.md"):
            p = d / "repos" / name / f
            if p.exists():
                print(p.read_text(encoding="utf-8")[: a.chars])
                return 0
        print(f"no knowledge for {name!r} in the hub; `devmem hub repos` lists what is there")
        return 1
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


def cmd_connect(a) -> int:
    from . import connect as cn
    import subprocess
    from pathlib import Path as _P
    from .diffcheck import repo_root
    try:
        root = repo_root()
    except subprocess.CalledProcessError:   # not a git repo: connect the current directory
        root = _P.cwd()
    cmd, args = (sys.executable, ["-m", "devmem.cli", "mcp"]) if a.local else (a.server_cmd, ["mcp"])
    try:
        res = cn.connect(root, a.agents or cn.AGENTS, cmd, args)
    except ValueError as e:
        print(e)
        return 2
    for r in res:
        state = "updated" if r["changed"] else "already set" if not r["note"].startswith("user-level") else "manual"
        print(f"{r['agent']:<9} {state:<11} {r['file']}  ({r['note']})")
    if a.local:
        print("note: --local wrote this machine's Python path; keep those files out of git")
    elif cmd == "devmem" and not __import__("shutil").which("devmem"):
        print("warning: `devmem` is not on PATH here, so agents cannot start it. Install the CLI, or rerun with --local")
    if not (root / ".devmem/index.json").exists():
        print("note: no .devmem/index.json yet: run `devmem-app publish <repo>` so the tools have data")
    return 0


def _version() -> str:
    try:
        from importlib.metadata import version
        return version("devmem")
    except Exception:
        return "unknown"


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):   # agents read piped output; Windows defaults pipes to cp1252 and crashes on any non-ASCII
        stream.reconfigure(encoding="utf-8", errors="replace")
    return run(argv)


def run(argv=None, via: str = "cli", root=None) -> int:
    """Parse and run one command. Read commands also log their token cost (see usage.py); `via` says who asked (cli | mcp)."""
    p = argparse.ArgumentParser(prog="devmem", description="Code knowledge for coding agents")
    p.add_argument("--version", action="version", version="devmem " + _version())
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("context", help="start of a task, one call: rules, matches, the likeliest symbol's body, callers, tests, known issues")
    s.add_argument("task"); s.add_argument("--files", nargs="*", help="files you expect to touch")
    s.add_argument("--symbol", help="focus on this symbol (path#qualname or name) instead of the best match")
    s.add_argument("--budget", type=int, default=1200, help="approximate token budget for the answer (default 1200)")
    s.set_defaults(fn=cmd_context)
    s = sub.add_parser("find", help="ranked path:line hits for a concept")
    s.add_argument("query"); s.add_argument("-n", "--limit", type=int, default=10); s.set_defaults(fn=cmd_find)
    s = sub.add_parser("show", help="one symbol's body + signatures of what it calls")
    s.add_argument("symbol"); s.add_argument("--full", action="store_true", help=f"whole body (default: first {SHOW_LINES} lines)")
    s.set_defaults(fn=cmd_show)
    s = sub.add_parser("outline", help="signatures and docstrings of a file")
    s.add_argument("file"); s.add_argument("--in", dest="inside", metavar="CLASS", help="only this class and its methods")
    s.add_argument("--all", action="store_true", help=f"every definition (default: {OUTLINE_CAP} lines, methods folded into their class)")
    s.set_defaults(fn=cmd_outline)
    s = sub.add_parser("impact", help="callers, dependents, tests for a symbol or table")
    s.add_argument("target"); s.add_argument("--all", action="store_true", help=f"every item (default: {LIST_CAP} per list)")
    s.set_defaults(fn=cmd_impact)
    s = sub.add_parser("helpers", help="reusable utilities already in the repo")
    s.add_argument("-n", "--limit", type=int, default=15); s.set_defaults(fn=cmd_helpers)
    for nm, fn, hp in (("tests-for", cmd_tests_for, "test command for tests affected by changed files"),
                       ("check", cmd_check, "lint/types/affected tests on changed files, compact output")):
        s = sub.add_parser(nm, help=hp)
        s.add_argument("files", nargs="*", help="default: files changed vs HEAD")
        s.add_argument("--base", help="changed files in BASE...HEAD instead")
        s.set_defaults(fn=fn)
    s = sub.add_parser("recall", help="lessons, rules and cross-repo pointers for a task")
    s.add_argument("task"); s.add_argument("--files", nargs="*"); s.add_argument("-n", "--limit", type=int, default=3)
    s.set_defaults(fn=cmd_recall)
    s = sub.add_parser("reuse", help="code other onboarded repos already built for a need, with how much work it is to adapt here")
    s.add_argument("need"); s.add_argument("-n", "--limit", type=int, default=3)
    s.add_argument("--lines", type=int, default=25, help="source lines to print per hit")
    s.add_argument("--prompt", action="store_true", help="also print a ready-to-use porting instruction")
    s.set_defaults(fn=cmd_reuse)
    s = sub.add_parser("plan", help="execute a human-written plan step by step: brief for a step, mark progress")
    s.add_argument("file", help="plan markdown file"); s.add_argument("--step", type=int, help="print the brief for this step")
    s.add_argument("--all", action="store_true", help="briefs for every step"); s.add_argument("--status", action="store_true")
    s.add_argument("--done", type=int, help="mark this step done"); s.add_argument("--note", help="with --done: what was done / deviations")
    s.add_argument("--code", action="store_true", help="include source of the org solutions"); s.set_defaults(fn=cmd_plan)
    s = sub.add_parser("docs", help="search the offline library documentation (latest versions, put there by devmem-app docs add)")
    s.add_argument("query", nargs="?"); s.add_argument("--lib", help="only this library"); s.add_argument("--list", action="store_true")
    s.add_argument("--for", dest="for_", metavar="FILE|SYMBOL", help="latest docs for the libraries this file or symbol uses")
    s.add_argument("-n", "--limit", type=int, default=4); s.add_argument("--chars", type=int, default=1100); s.set_defaults(fn=cmd_docs)
    s = sub.add_parser("hub", help="central knowledge repo of all onboarded repos: sync | status | repos | show <repo>")
    s.add_argument("action", choices=["sync", "status", "repos", "show"]); s.add_argument("repo", nargs="?")
    s.add_argument("--chars", type=int, default=6000); s.set_defaults(fn=cmd_hub)
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
    s = sub.add_parser("review", help="Markdown summary of the current diff: changed symbols, callers, tests reaching them, risk")
    s.add_argument("--base", help="compare BASE...HEAD (CI)"); s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_review)
    s = sub.add_parser("gaps", help="widely used code no test reaches, riskiest first")
    s.add_argument("-n", "--limit", type=int, default=15); s.set_defaults(fn=cmd_gaps)
    s = sub.add_parser("report", help="record this PR's diff size and diffcheck result in .devmem/metrics/runs (commit it)")
    s.add_argument("--base"); s.add_argument("--task"); s.add_argument("--tokens", type=int, help="tokens/ACUs the agent knows it used")
    s.add_argument("--recalls", type=int); s.add_argument("--rounds", type=int, help="review rounds so far")
    s.set_defaults(fn=cmd_report, nolog=True)
    s = sub.add_parser("mcp", help="serve these commands as MCP tools over stdio (for agents that speak MCP)")
    s.set_defaults(fn=None)
    s = sub.add_parser("doctor", help="check that agents can use devmem here (CLI, index, AGENTS.md, MCP round trip, agent configs)")
    s.set_defaults(fn=None)
    s = sub.add_parser("connect", help="register devmem with coding agents in this repo (MCP server + AGENTS.md)")
    s.add_argument("agents", nargs="*", help="claude cursor vscode codex gemini kiro opencode aider windsurf (default: all)")
    s.add_argument("--command", dest="server_cmd", default="devmem", help="how agents start devmem (default: devmem on PATH)")
    s.add_argument("--local", action="store_true", help="use this Python's absolute path (works without PATH; do not commit)")
    s.set_defaults(fn=None)
    a = p.parse_args(argv)
    if a.command == "mcp":
        from .mcp import serve
        return serve()
    if a.command == "connect":
        return cmd_connect(a)
    if a.command == "doctor":
        import subprocess as _sp
        from pathlib import Path as _P
        from . import doctor
        top = _sp.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
        return doctor.run(_P(top.stdout.strip()) if top.returncode == 0 else _P.cwd())
    if a.command in ("diffcheck", "report"):
        try:
            ix = Index.load()
        except FileNotFoundError:
            ix = None
        return a.fn(ix, a)
    try:
        ix = Index.cached(root) if via == "mcp" else Index.load(root)   # the MCP server stays up: parse the index once
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 2
    from . import usage
    if a.command not in usage.LOGGED:
        return a.fn(ix, a)
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = a.fn(ix, a)
    text = buf.getvalue()
    import os
    try:
        limit = int(os.environ.get("DEVMEM_MAX_TOKENS", MAX_TOKENS))
    except ValueError:
        limit = MAX_TOKENS
    asked_for_all = getattr(a, "full", False) or getattr(a, "all", False) or a.command == "context"   # context has its own budget
    if limit > 0 and not asked_for_all and len(text) > limit * 4:
        cut = text.rfind("\n", 0, limit * 4)
        text = text[: cut if cut > 0 else limit * 4] + (f"\n... cut at ~{limit} tokens ({len(text) // 4} in full). Narrow the request "
                                                         f"(a symbol instead of a file, fewer results), or set DEVMEM_MAX_TOKENS=0.\n")
    sys.stdout.write(text)
    if via != "ui":   # the web UI previews what agents see; that is not agent usage
        usage.record(ix, a.command, text, via, " ".join(argv if argv is not None else sys.argv[1:]))
    return code


if __name__ == "__main__":
    sys.exit(main())
