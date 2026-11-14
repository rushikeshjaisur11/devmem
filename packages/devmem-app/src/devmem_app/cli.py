"""devmem-app — onboarding, indexing, cross-repo search (UI/publisher/chat come in later steps)."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from devmem.index import Index

from .indexer import AccuracyError, index_repo
from .paths import devmem_home
from devmem.schema import atomic_write


_GIT_URL = re.compile(r"^(https?://|ssh://|git://|git@[\w.-]+:)[^\s]+$")


def _workspace() -> Path:
    return devmem_home() / "workspace.json"


def _ws() -> dict:
    w = _workspace()
    return json.loads(w.read_text()) if w.exists() else {"repos": {}}


def _save(ws: dict):
    w = _workspace()
    w.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(w, json.dumps(ws, indent=1))


def _main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="devmem-app")
    p.add_argument("--fast", action="store_true", help="skip SCIP and Graphify (lower accuracy, recorded in the index); same as DEVMEM_ACCURACY=fast")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("add", help="onboard a repo (local path or Git URL)"); s.add_argument("path"); s.add_argument("--name")
    s = sub.add_parser("index", help="(re)index a path, or all onboarded repos"); s.add_argument("path", nargs="?")
    s.add_argument("--force-tools", action="store_true", help="re-run SCIP and Graphify even if their cache is current (they run automatically when files changed)")
    s.add_argument("--graphify-file", help="import an existing graphify-out/graph.json")
    s.add_argument("--scip-file", action="append", help="import a prebuilt index.scip instead of running the indexers")
    s = sub.add_parser("find", help="search across all onboarded repos"); s.add_argument("query")
    s = sub.add_parser("ask", help="ask a question across onboarded repos (retrieval; --llm adds a Vertex answer)")
    s.add_argument("question"); s.add_argument("--repo", action="append"); s.add_argument("--llm", action="store_true")
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
    s = sub.add_parser("metrics", help="PR metrics: show a repo's before/after summary, or ingest a Devin session export")
    s.add_argument("action", choices=["show", "ingest-devin"]); s.add_argument("arg", help="repo name (show) or JSON file (ingest-devin)")
    s = sub.add_parser("coverage", help="import a coverage report (coverage.py JSON, Cobertura or JaCoCo XML, lcov) so test selection uses measured coverage")
    s.add_argument("repo"); s.add_argument("file", nargs="?", help="report file (default: look in the usual places in the repo)")
    s = sub.add_parser("selfcheck", help="how far to trust a repo's index: confirmed edges, call sites re-checked, search hit rate")
    s.add_argument("repo")
    s = sub.add_parser("ui", help="start the local web app")
    s.add_argument("--port", type=int, default=int(os.environ.get("PORT") or os.environ.get("DEVMEM_PORT") or 8765))
    s.add_argument("--no-open", action="store_true")
    s.add_argument("--host", default=os.environ.get("DEVMEM_HOST", "127.0.0.1"),
                   help="address to listen on; anything but 127.0.0.1 (e.g. 0.0.0.0 on a server) turns on sign-in with an access token "
                        "(DEVMEM_TOKEN, or one is generated and printed)")
    s.add_argument("--no-watch", action="store_true", help="do not re-index repos whose HEAD moved while the app runs")
    s.add_argument("--publish", action="store_true", help="watcher also publishes after re-indexing")
    s = sub.add_parser("watch", help="re-index onboarded repos whenever their HEAD moves (pull, merge, commit, branch switch)")
    s.add_argument("--interval", type=int, default=60, help="seconds between checks"); s.add_argument("--publish", action="store_true")
    s.add_argument("--once", action="store_true", help="one pass, then exit (for cron or a git hook)")
    s = sub.add_parser("reuse", help="what other onboarded repos already built for a need, and the effort to adapt it into --into")
    s.add_argument("need"); s.add_argument("--into", required=True, help="target repo name"); s.add_argument("-n", "--limit", type=int, default=5)
    s.add_argument("--prompt", action="store_true"); s.add_argument("--json", action="store_true")
    s = sub.add_parser("hub", help="central knowledge repo for all onboarded repos: set <git-url|dir> | status | push")
    s.add_argument("action", choices=["set", "status", "push"]); s.add_argument("spec", nargs="?")
    s.add_argument("--push", action="store_true", help="also git push the hub to its remote")
    s.add_argument("--summarize", action="store_true", help="have the configured LLM (Vertex) describe each function so offline search matches by meaning")
    s.add_argument("--prune", action="store_true", help="remove repos from the hub that are no longer onboarded")
    s.add_argument("--per-repo", type=int, default=400)
    s = sub.add_parser("docs", help="library docs for offline agents: add <llms.txt|url|pypi:pkg|npm:pkg|local path> | list | refresh [name..] | remove <name>")
    s.add_argument("action", choices=["add", "list", "refresh", "remove", "upgrade-notes"]); s.add_argument("args", nargs="*")
    s.add_argument("--name"); s.add_argument("--no-follow", action="store_true", help="llms.txt: do not fetch the pages it links to")
    s.add_argument("--max-pages", type=int, default=40)
    s = sub.add_parser("libs", help="what a repo depends on vs the latest releases (needs internet); feeds knowledge/libraries.md on publish")
    s.add_argument("repo", help="onboarded repo name"); s.add_argument("--offline", action="store_true")
    s = sub.add_parser("prompt", help="build a prompt for another agent from a requirement or your plan (read-only; nothing is written to any repo)")
    s.add_argument("--into", required=True, help="repo the agent will work in"); s.add_argument("--need", help="the requirement, in words")
    s.add_argument("--plan", help="markdown plan file you wrote"); s.add_argument("--code", action="store_true", help="include source of existing solutions")
    s.add_argument("--no-fetch", action="store_true", help="do not fetch the plan's Docs: sources")
    s = sub.add_parser("clones", help="token-level duplicate code across all onboarded repos")
    a = p.parse_args(argv)

    if a.fast:
        os.environ["DEVMEM_ACCURACY"] = "fast"
    if a.cmd == "add":
        spec = a.path.strip()
        is_url = "://" in spec or spec.startswith("git@")
        if is_url:
            if not _GIT_URL.match(spec):
                sys.exit("devmem-app: use an https://, ssh://, git:// or git@host:path Git URL")
            name = a.name or re.sub(r"\.git$", "", spec.rstrip("/").split("/")[-1].split(":")[-1]) or "repo"
            if not re.fullmatch(r"[\w.-]+", name) or name in (".", ".."):
                sys.exit("devmem-app: bad repo name")
            root = devmem_home() / "clones" / name
            env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
            try:
                if (root / ".git").exists():
                    result = subprocess.run(
                        ["git", "-C", str(root), "fetch", "--all", "--prune"],
                        capture_output=True, text=True, env=env, timeout=600,
                    )
                else:
                    root.parent.mkdir(parents=True, exist_ok=True)
                    result = subprocess.run(
                        ["git", "clone", "--", spec, str(root)],
                        capture_output=True, text=True, env=env, timeout=900,
                    )
            except (subprocess.TimeoutExpired, OSError) as e:
                sys.exit(f"devmem-app: git: {e}")
            if result.returncode:
                sys.exit("devmem-app: git failed: " + (result.stderr or result.stdout).strip()[-300:])
        else:
            root = Path(spec).expanduser().resolve()
            name = a.name or root.name
        idx = index_repo(root, name)          # register only after a full-accuracy index succeeded
        ws = _ws()
        ws["repos"][name] = str(root)
        _save(ws)
        print(f"onboarded {name}: {len(idx['symbols'])} symbols, {len(idx['edges'])} edges {idx['stats']}")
    elif a.cmd == "index":
        targets = {Path(a.path).name: a.path} if a.path else _ws()["repos"]
        for name, path in targets.items():
            idx = index_repo(path, name, scip=a.scip_file or (True if a.force_tools else None),
                             graphify=a.graphify_file or (True if a.force_tools else None))
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
    elif a.cmd == "metrics":
        from . import metrics as mt
        if a.action == "ingest-devin":
            print(mt.ingest_devin(Path(a.arg)))
        else:
            r = mt.summary(Path(_ws()["repos"][a.arg]))
            print(f"devmem added: {r['split'] or 'unknown'}; {r['agent_commits']} agent commits of {r['commits_scanned']} scanned")
            for who in ("agent", "human"):
                for k, v in r[who].items():
                    print(f"  {who:6} {k:6} {v}")
            print("  runs:", {k: v for k, v in r["runs"].items() if k != "recent"})
            if r["devin"]:
                print("  devin:", r["devin"])
    elif a.cmd == "coverage":
        from . import coverage_import as ci
        root = Path(a.repo).resolve()
        rep = Path(a.file) if a.file else ci.find_report(root)
        if not rep:
            sys.exit("no coverage report found: run your tests with coverage and pass the report file "
                     "(coverage.py: `coverage json --show-contexts`, with `[run] dynamic_context = test_function` for per-test data)")
        try:
            d = ci.import_file(Index.load(root), rep)
        except ci.CoverageError as e:
            sys.exit(f"devmem-app: {rep}: {e}")
        st = d["stats"]
        print(f"{d['format']} report {rep.name}: {st['covered']} of {st['functions']} functions ran in tests"
              + (f"; per-test data for {st['with_tests']}" if d["per_test"] else "; no per-test data (record contexts to get it)")
              + (f"; {st['unmatched_files']} files not in this repo" if st["unmatched_files"] else ""))
    elif a.cmd == "selfcheck":
        from . import selfcheck
        r = selfcheck.run(Index.load(Path(a.repo).resolve()))
        e, st, se = r["edges"], r["sites"], r["search"]
        pct = lambda x: "-" if x is None else f"{x * 100:.0f}%"
        print(f"edges: {e['edges']:,}; confirmed by a second tool {pct(e['independent'])}; name-only guesses {pct(e['ambiguous_share'])}"
              + ("" if r.get("accuracy_mode") != "fast" else " (indexed with --fast: no Graphify or SCIP confirmation)"))
        for lang, l in e["languages"].items():
            print(f"  {lang}: {l['edges']:,} edges, {pct(l['independent'])} confirmed" + (" (names only: install its SCIP indexer for type-resolved edges)" if l["name_resolved"] else ""))
        print(f"call sites re-checked: {st['present']}/{st['checked']} still in the code" + (f"; e.g. {st['examples'][0]}" if st["examples"] else ""))
        if se.get("queries"):
            print(f"search ({se['queries']} queries{', ' + str(se['yours']) + ' of yours' if se.get('yours') else ''}): top-1 {pct(se['top1'])}, top-5 {pct(se['top5'])}, context focus {pct(se['context_focus'])}")
        else:
            print("search: no documented functions to test with; add `query => path#qualname` lines to .devmem/search-eval.txt")
    elif a.cmd == "ui":
        import threading
        import webbrowser

        import uvicorn
        local = a.host in ("127.0.0.1", "localhost", "::1")
        if not local and not os.environ.get("DEVMEM_TOKEN", "").strip():
            import secrets
            os.environ["DEVMEM_TOKEN"] = secrets.token_urlsafe(24)   # never serve a network address without sign-in
            print(f"access token (set DEVMEM_TOKEN to choose your own): {os.environ['DEVMEM_TOKEN']}", flush=True)
        if os.environ.get("DEVMEM_TOKEN", "").strip():
            print(f"deployed mode: sign in at http://{a.host}:{a.port} with the access token; onboard repos by upload or Git URL", flush=True)
        if local and not a.no_open:
            threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{a.port}")).start()
        if not a.no_watch:
            from . import watch
            watch.start(lambda: _ws()["repos"], publish_too=a.publish)
        def keep_docs_fresh():   # online library docs are re-fetched when older than the interval set on the Library docs page
            import time
            from . import docs as dm
            from .paths import diagnose
            while True:
                time.sleep(120)
                try:
                    done = dm.refresh_due(_ws()["repos"])
                    if done:
                        print("docs refreshed: " + "; ".join(f"{k}: {v}" for k, v in done.items()), flush=True)
                    for k, v in (done or {}).items():
                        if str(v).startswith("failed"):
                            diagnose("docs", f"{k} could not be refreshed: {v}")
                except Exception as e:
                    diagnose("docs", f"automatic refresh failed: {e}")
                    print(f"docs refresh failed: {e}", flush=True)
                time.sleep(6 * 3600)
        threading.Thread(target=keep_docs_fresh, daemon=True).start()
        uvicorn.run("devmem_app.server:app", host=a.host, port=a.port, log_level="warning",
                    proxy_headers=True, forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"))
    elif a.cmd == "watch":
        from . import watch
        if a.once:
            watch.tick(_ws()["repos"], a.publish, {})
        else:
            print(f"watching {len(_ws()['repos'])} repos every {a.interval}s (Ctrl+C to stop)")
            watch.run(lambda: _ws()["repos"], a.interval, a.publish)
    elif a.cmd == "prompt":
        from .implement import build
        repos = _ws()["repos"]
        if a.into not in repos:
            sys.exit(f"unknown repo {a.into}")
        if not a.need and not a.plan:
            sys.exit("give --need \"...\" or --plan file.md")
        r = build(a.into, repos, a.need or "", Path(a.plan).read_text(encoding="utf-8") if a.plan else "", "code" if a.code else "pointers", not a.no_fetch)
        print(r["prompt"])
        print(f"--- ~{r['tokens']} tokens; {r['counts']['org_solutions']} existing solution(s), {r['counts']['modernize']} modernize note(s), {r['counts']['doc_excerpts']} doc excerpt(s)", file=sys.stderr)
    elif a.cmd == "docs":
        from . import docs as dm
        if a.action == "add":
            if not a.args:
                sys.exit("usage: devmem-app docs add <llms.txt-url | page-url | pypi:pkg | npm:pkg | local file or folder>")
            for spec in a.args:
                m = dm.add(spec, a.name if len(a.args) == 1 else None, not a.no_follow, a.max_pages)
                print(f"{m['name']} {m['version']}: {len(m['pages'])} pages, {m['bytes'] // 1024} KB")
        elif a.action == "list":
            for m in dm.list_libs():
                print(f"{m['name']} {m['version'] or ''}  fetched {m['fetched']} ({m['age_days']}d)  {m['pages']} pages  <- {m['source']}")
        elif a.action == "refresh":
            for k, v in dm.refresh(a.args or None).items():
                print(f"{k}: {v}")
        elif a.action == "upgrade-notes":   # docs upgrade-notes <repo> [lib ...]
            if not a.args or a.args[0] not in _ws()["repos"]:
                sys.exit("usage: devmem-app docs upgrade-notes <repo name> [library ...]")
            for k, v in dm.add_upgrade_notes(Path(_ws()["repos"][a.args[0]]), set(a.args[1:]) or None).items():
                print(f"{k}: {v}")
            print("publish the repo to ship them to its agents")
        else:
            print("removed" if a.args and dm.remove(a.args[0]) else "not found")
    elif a.cmd == "libs":
        from . import docs as dm
        repos = _ws()["repos"]
        if a.repo not in repos:
            sys.exit(f"unknown repo {a.repo}")
        for r in dm.write_libs(Path(repos[a.repo]), not a.offline):
            print(f"{r['name']} ({r['eco']}) {r['spec'] or '-'} -> latest {r['latest'] or '?'}  [{r['behind']}]  docs:{'yes' if r['docs'] else 'no'}")
    elif a.cmd == "hub":
        from . import hub as hubmod
        if a.action == "set":
            if not a.spec:
                sys.exit("usage: devmem-app hub set <git-url|directory>")
            print(json.dumps(hubmod.configure(a.spec), indent=1))
        elif a.action == "status":
            print(json.dumps(hubmod.status(), indent=1))
        else:
            from .chat import provider_from_env
            prov = provider_from_env() if a.summarize else None
            if a.summarize and prov is None:
                sys.exit("--summarize: no AI model is set up: open AI setup in the web UI, or set DEVMEM_AI_PROVIDER and DEVMEM_AI_KEY")
            print(json.dumps(hubmod.push(_ws()["repos"], a.per_repo, prov, True, a.push, a.prune), indent=1))
    elif a.cmd == "reuse":
        from devmem import reuse as core
        from .reuse import live
        repos = _ws()["repos"]
        if a.into not in repos:
            sys.exit(f"unknown repo {a.into}; onboarded: {', '.join(repos)}")
        try:
            from .chat import provider_from_env
            prov = provider_from_env()
        except Exception:
            prov = None
        hits = live(a.need, a.into, repos, a.limit, provider=prov)
        if a.json:
            print(json.dumps(hits, indent=1))
        for h in ([] if a.json else hits):
            print(core.render_card(h["score"], h["entry"], h["fit"]) + "\n")
            if a.prompt:
                print(h["prompt"] + "\n")
        if not hits and not a.json:
            print("nothing in the other repos matches; write it fresh")
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
    elif a.cmd == "ask":
        from . import chat
        allr = _ws()["repos"]
        res = chat.ask(a.question, {n: Path(p) for n, p in allr.items() if not a.repo or n in a.repo}, use_llm=a.llm)
        print(res["answer"])
        if res.get("llm_error"):
            print("(LLM failed, showing retrieval:", res["llm_error"] + ")")
        if res["mode"] == "llm":
            print("\nSources:")
            for h in res["hits"]:
                print(f"  [{h['n']}] {h['pointer']}  {h['title']}")
    elif a.cmd == "find":
        for name, path in _ws()["repos"].items():
            try:
                ix = Index.load(Path(path))
            except FileNotFoundError:
                continue
            for score, sym in ix.find(a.query, 5):
                print(f"[{name}] {sym['file']}:{sym['line']}  {sym['qualname']}  ({score:.1f})")
    return 0


def main(argv=None) -> int:
    try:
        return _main(argv)
    except AccuracyError as e:
        sys.exit(f"devmem-app: accuracy requirement not met: {e}")


if __name__ == "__main__":
    sys.exit(main())
