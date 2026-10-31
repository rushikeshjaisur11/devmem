"""Offline library docs: fetch (llms.txt / html / package), store, search as an agent, freshness."""
import functools
import http.server
import json
import os
import shutil
import threading
from pathlib import Path

import pytest

from devmem import docs as agent_docs
from devmem.cli import main
from devmem.index import Index
from devmem_app import docs
from devmem_app.indexer import index_repo

FIX = Path(__file__).parent.parent / "fixtures"
LLMS = """# Fancylib

> Fancylib validates things.

## Docs
- [Validators](/validators.md): built-in validators
- [Migration](/migration.md): moving from v1 to v2
"""
PAGES = {
    "llms.txt": LLMS,
    "validators.md": "# Validators\n\nUse `fancy.validate_email` in v2. It replaces the hand written regex helpers; the `strict=True` flag "
                     "rejects display names.\n\n## Custom validators\nSubclass `Validator` and register with `@fancy.register`.\n",
    "migration.md": "# Migration\n\nv1 `check_email` was removed; call `validate_email` instead.\n",
    "page.html": "<html><head><title>Guide</title><style>x{}</style></head><body><nav>menu</nav><h1>Rate limiting</h1>"
                 "<p>Use the built-in <code>RateLimiter</code> with a token bucket.</p><pre>limiter = RateLimiter(10)</pre></body></html>",
}


@pytest.fixture()
def site(tmp_path, monkeypatch):
    root = tmp_path / "www"; root.mkdir()
    for k, v in PAGES.items():
        (root / k).write_text(v)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("DEVMEM_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("NO_PROXY", "127.0.0.1"); monkeypatch.setenv("no_proxy", "127.0.0.1")
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_llms_txt_is_followed_stored_and_searchable_by_an_agent(site, tmp_path, capsys):
    m = docs.add(f"{site}/llms.txt", "fancylib")
    assert m["kind"] == "llms" and len(m["pages"]) == 3            # index + 2 linked pages (served at <page>.md)
    repo = tmp_path / "repo"; shutil.copytree(FIX / "py_app", repo); index_repo(repo, "repo")
    assert docs.export(repo / ".devmem/docs") == ["fancylib"]
    hits = agent_docs.search(repo, "validate an email address")
    assert hits and all(h["lib"] == "fancylib" for h in hits)
    assert any(h["url"].endswith("validators.md") and "validate_email" in h["text"] for h in hits[:2])   # both pages are relevant
    cwd = os.getcwd(); os.chdir(repo)
    try:
        assert main(["docs", "validate email"]) == 0
        out = capsys.readouterr().out
        assert "[fancylib" in out and "validate_email" in out and "fetched" in out
        assert main(["docs", "--list"]) == 0 and "fancylib" in capsys.readouterr().out
        assert main(["docs", "quantum entanglement"]) == 0 and "no matching documentation" in capsys.readouterr().out
    finally:
        os.chdir(cwd)


def test_html_page_becomes_markdown_without_chrome(site):
    m = docs.add(f"{site}/page.html", "guide")
    text = (docs.docs_dir() / "guide" / m["pages"][0]["file"]).read_text()
    assert "# Rate limiting" in text and "RateLimiter" in text and "```" in text and "menu" not in text and "x{}" not in text


def test_refresh_and_staleness(site):
    docs.add(f"{site}/llms.txt", "fancylib")
    man = docs.docs_dir() / "fancylib/manifest.json"
    d = json.loads(man.read_text()); d["fetched"] = "2020-01-01"; man.write_text(json.dumps(d))
    assert docs.list_libs()[0]["age_days"] > 1000
    assert "fancylib" in docs.refresh()
    assert docs.list_libs()[0]["age_days"] == 0
    assert docs.remove("fancylib") and docs.list_libs() == []


def test_dependency_scan_and_behind(tmp_path):
    (tmp_path / "requirements.txt").write_text("requests==2.0.1\npydantic>=1.10,<3\nflask\n# c\n")
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"zod": "^3.1.0"}, "devDependencies": {"vite": "~4.0.0"}}))
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\ndependencies=["httpx>=0.24"]\n')
    got = {(d["eco"], d["name"]): d["spec"] for d in docs.scan_deps(tmp_path)}
    assert got[("pypi", "requests")] == "==2.0.1" and got[("pypi", "flask")] == "" and got[("npm", "zod")] == "^3.1.0" and ("pypi", "httpx") in got
    assert docs.behind("==2.0.1", "2.32.3") == "major" or docs.behind("==2.0.1", "2.32.3") == "minor"
    assert docs.behind("==2.32.3", "2.32.3") == "current" and docs.behind("", "1.0") == "unpinned" and docs.behind("^1.0", None) == "unknown"
    assert docs.behind("^3.1.0", "4.0.0") == "major"
    md = docs.render_libraries_md([{"name": "requests", "eco": "pypi", "spec": "==2.0.1", "latest": "2.32.3", "behind": "minor", "docs": False}])
    assert "devmem docs" in md and "requests" in md


def test_publish_copies_only_docs_for_libraries_the_repo_uses(site, tmp_path):
    from devmem_app.publisher import publish
    docs.add(f"{site}/llms.txt", "fancylib"); docs.add(f"{site}/page.html", "guide")
    repo = tmp_path / "r"; shutil.copytree(FIX / "py_app", repo)
    (repo / "requirements.txt").write_text("fancylib>=2\n")
    (repo / ".devmem").mkdir(exist_ok=True)
    (repo / ".devmem/libs.json").write_text(json.dumps([{"name": "fancylib", "eco": "pypi", "spec": ">=2", "latest": "2.1", "behind": "current", "docs": True}]))
    publish(repo, index_repo(repo, "r"), {"r": str(repo)})
    assert (repo / ".devmem/docs/fancylib/manifest.json").exists() and not (repo / ".devmem/docs/guide").exists()
    assert "fancylib" in (repo / ".devmem/knowledge/libraries.md").read_text()


def test_reuse_flags_handrolled_code_and_old_library_versions(site, tmp_path, monkeypatch):
    """The org already has a hand-written solution; the library docs on file describe a built-in for it."""
    from devmem import reuse as core
    from devmem_app import reuse as app_reuse
    pages = {"index.html": "<html><body><h1>Rate limiting</h1><p>RateLimiter is built in since 2.0: a token bucket limiter "
                           "that replaces hand written throttle code.</p><h2>Throttle</h2><p>throttle requests with RateLimiter.</p></body></html>"}
    www = Path(site.replace("http://", "")) if False else None
    # source repo: hand-rolled throttle, pinned to an old release of 'fancylib'
    src = tmp_path / "src"; src.mkdir()
    (src / "limits.py").write_text('import fancylib\n\ndef throttle(fn, per_second=5):\n    """Throttle calls to per_second using sleep."""\n'
                                   '    import time\n    last = [0.0]\n    def wrapped(*a, **k):\n        wait = 1 / per_second - (time.time() - last[0])\n'
                                   '        if wait > 0:\n            time.sleep(wait)\n        last[0] = time.time()\n        return fancylib.call(fn, *a, **k)\n    return wrapped\n')
    (src / "requirements.txt").write_text("fancylib==1.0.0\n")
    index_repo(src, "src")
    # target repo + docs on file
    tgt = tmp_path / "tgt"; shutil.copytree(FIX / "py_app", tgt); index_repo(tgt, "tgt")
    (tgt / ".devmem/docs/fancylib").mkdir(parents=True)
    (tgt / ".devmem/docs/fancylib/01-rate.md").write_text("<!-- source: https://docs.example/rate -->\n# Throttle\n\nUse the built-in `RateLimiter` to throttle calls: "
                                                          "a token bucket that replaces hand written throttle code.\n")
    (tgt / ".devmem/docs/fancylib/manifest.json").write_text(json.dumps({"name": "fancylib", "version": "2.3.0", "fetched": "2026-10-01", "source": "x",
        "pages": [{"file": "01-rate.md", "url": "https://docs.example/rate", "title": "Rate limiting"}]}))
    entries = app_reuse.build_entries(Index.load(src))
    e = next(x for x in entries if x["name"] == "throttle")
    assert e["ext_imports"] == ["fancylib"] and e["ext_versions"] == {"fancylib": "==1.0.0"}
    hits = core.reuse(Index.load(tgt), "throttle outgoing calls", entries)
    mod = hits[0]["fit"]["modernize"]
    assert any("built-in" in m and "Throttle" in m and "https://docs.example/rate" in m for m in mod)
    assert any("written against fancylib ==1.0.0" in m and "2.3.0" in m for m in mod)
    card = core.render_card(hits[0]["score"], hits[0]["entry"], hits[0]["fit"])
    assert "MODERNIZE" in card and "prefer the library built-ins" in core.port_prompt(hits[0]["entry"], hits[0]["fit"], "tgt")
    # no docs on file -> no modernize notes, nothing breaks
    shutil.rmtree(tgt / ".devmem/docs")
    assert core.reuse(Index.load(tgt), "throttle outgoing calls", entries)[0]["fit"]["modernize"] == []


def _repos(tmp_path, *names):
    out = {}
    for n in names:
        d = tmp_path / n
        shutil.copytree(FIX / n, d)
        index_repo(d, n)
        out[n] = str(d)
    return out


def _snapshot(path):
    return sorted((str(p.relative_to(path)), p.stat().st_mtime_ns) for p in Path(path).rglob("*") if p.is_file())


def test_implement_builds_agent_prompt_for_a_requirement_and_writes_nothing(site, tmp_path):
    from devmem_app.implement import build
    repos = _repos(tmp_path, "py_app", "conv_app")
    docs.add(f"{site}/llms.txt")
    before = {k: _snapshot(v) for k, v in repos.items()}
    r = build("py_app", repos, "retry failing calls and validate email addresses before sending")
    assert r["mode"] == "task" and r["counts"]["org_solutions"] >= 1 and r["counts"]["doc_excerpts"] >= 1
    p = r["prompt"]
    assert "# Task:" in p and "This prompt is final" in p and "conv_app" in p and "validate_email" in p and "## When done" in p
    assert "```" not in p                                              # pointers mode: no source
    assert r["tokens"] < 1200
    assert "```" in build("py_app", repos, "retry failing calls", detail="code")["prompt"]
    assert before == {k: _snapshot(v) for k, v in repos.items()}      # read-only: no file in any repo changed


def test_implement_from_a_plan_fetches_docs_and_has_a_section_per_step(site, tmp_path):
    from devmem_app.implement import build
    repos = _repos(tmp_path, "py_app", "conv_app")
    plan = (f"# Harden the client\n\nDocs: {site}/llms.txt {site}/missing-llms.txt\nConstraints: no new dependencies\nDo not touch the billing module\n\n"
            "## Step 1: Validate addresses\nUse validate_email from fancylib in app/client.py.\n\n"
            "## Step 2: Add retry\nRetry failing calls in app/service.py.\n")
    r = build("py_app", repos, plan_text=plan)
    assert r["mode"] == "plan" and r["steps"] == 2
    status = {d["source"].rsplit("/", 1)[-1]: d["status"] for d in r["docs_fetched"]}
    assert status == {"llms.txt": "fetched", "missing-llms.txt": "failed"}
    p = r["prompt"]
    assert "### Step 1: Validate addresses" in p and "### Step 2: Add retry" in p and "no new dependencies" in p
    assert "Current docs, fancylib" in p.split("### Step 2")[0] and "conv_app" in p.split("### Step 2")[1]
    assert build("py_app", repos, requirement="just words")["mode"] == "task"
    with pytest.raises(ValueError):
        build("py_app", repos, "", "")


def test_chat_decides_between_question_and_new_requirement(site, tmp_path):
    from fastapi.testclient import TestClient
    from devmem_app.server import create_app
    c, H = TestClient(create_app()), {"X-Devmem": "1"}
    repos = _repos(tmp_path, "py_app", "conv_app")
    for n, d in repos.items():
        assert c.post("/api/repos", json={"path": d}, headers=H).status_code == 200
    assert c.post("/api/docs", json={"spec": f"{site}/llms.txt"}, headers=H).json()["name"] == "fancylib"
    assert c.get("/api/docs").json()[0]["name"] == "fancylib"
    say = lambda text, **kw: c.post("/api/chat", json={"question": text, **kw}, headers=H)
    # a question is answered, and library docs are citable sources
    r = say("how do I validate an email address?").json()
    assert r["intent"] == "ask" and any(h["kind"] == "doc" and "validate_email" in h["snippet"] for h in r["hits"])
    # a requirement becomes an agent prompt; with two repos the user must say which one (or name it)
    need = "Add retry with backoff to the client calls in py_app"
    r = say(need).json()
    assert r["intent"] == "implement" and r["target"] == "py_app" and "conv_app" in r["prompt"] and r["tokens"] > 0
    r = say("We need to add retry with backoff to outgoing calls").json()
    assert r["intent"] == "implement" and sorted(r["needs_repo"]) == ["conv_app", "py_app"] and "prompt" not in r
    r = say("We need to add retry with backoff to outgoing calls", target="py_app").json()
    assert r["target"] == "py_app" and r["mode"] == "task"
    assert say("We need to add retry with backoff to outgoing calls", repos=["py_app"]).json()["target"] == "py_app"
    # a plan is recognised and split into steps
    plan = "# Plan\n\n## Step 1: Validate\nuse validate_email in app/client.py\n\n## Step 2: Retry\nretry calls\n"
    r = say(plan, repos=["py_app"]).json()
    assert r["intent"] == "implement" and r["mode"] == "plan" and r["steps"] == 2 and "### Step 2: Retry" in r["prompt"]
    # the user's correction wins over the guess
    assert say("Add retry with backoff", repos=["py_app"], force="ask").json()["intent"] == "ask"
    assert say("where is retry used?", repos=["py_app"], force="implement").json()["intent"] == "implement"
    assert say("   ").status_code in (200, 422)
    assert c.delete("/api/docs/fancylib", headers=H).json() == {"ok": True}


def test_intent_classifier():
    from devmem_app.intent import classify, pick_target
    ask = ["where do we retry failed requests?", "who reads mart.sales_report", "What does the export job do", "how does the cache invalidation work?",
           "is there a slug helper", "show me the retry logic", "which tables does billing write to?", "explain the lineage of orders"]
    impl = ["Add rate limiting to the API client", "We need to support Parquet exports", "implement retry with backoff for the export job",
            "how do I add pagination to the list endpoint", "Integrate Stripe webhooks into billing", "I want to migrate the cron jobs to Airflow",
            "Docs: pypi:foo\nadd a thing", "- [ ] one\n- [ ] two\n- [ ] three", "1. add a column\n2. backfill it\n3. update the report"]
    assert [t for t in ask if classify(t)["intent"] != "ask"] == []
    assert [t for t in impl if classify(t)["intent"] != "implement"] == []
    assert classify("add retry")["reason"]
    assert pick_target("x", ["a"], ["a", "b"]) == ["a"] and pick_target("x", [], ["a"]) == ["a"] and pick_target("fix b please", [], ["a", "b"]) == ["b"]
    assert pick_target("fix it", [], ["a", "b"]) == ["a", "b"]


def test_agent_side_plan_file_progress_still_works(tmp_path, capsys):
    """A plan file committed by the user can still be followed step by step with `devmem plan` (agent side, no app involved)."""
    from devmem import plan as core_plan
    from devmem.cli import main
    repo = tmp_path / "repo"; shutil.copytree(FIX / "py_app", repo); index_repo(repo, "repo")
    (repo / "plan.md").write_text("# T\nConstraints: no new deps\n\n## Step 1: a thing\nDo it in app/client.py\n\n## Step 2: next\nmore\n")
    cwd = os.getcwd(); os.chdir(repo)
    try:
        assert main(["plan", "plan.md", "--status"]) == 0 and "0/2 steps done" in capsys.readouterr().out
        assert main(["plan", "plan.md", "--step", "1"]) == 0 and "STEP 1/2" in capsys.readouterr().out
        assert main(["plan", "plan.md", "--done", "1", "--note", "ok"]) == 0 and "next: step 2" in capsys.readouterr().out
    finally:
        os.chdir(cwd)
    assert core_plan.parse("# t\nno steps here")["steps"] == []


def test_local_docs_folder_is_stored_and_refreshable(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVMEM_HOME", str(tmp_path / "home"))
    from devmem_app import docs as dm
    d = tmp_path / "Internal Guide"
    (d / "api").mkdir(parents=True)
    (d / "index.md").write_text("# Internal guide\nHow we call the billing service.", encoding="utf-8")
    (d / "api" / "auth.md").write_text("# Auth\nTokens expire after 1h.", encoding="utf-8")
    (d / "logo.png").write_bytes(b"\x89PNG")
    (d / ".git").mkdir(); (d / ".git" / "x.md").write_text("ignored", encoding="utf-8")
    m = dm.add(str(d))
    assert m["name"] == "internal-guide" and m["kind"] == "local" and len(m["pages"]) == 2
    (d / "api" / "auth.md").write_text("# Auth\nTokens expire after 2h.", encoding="utf-8")
    assert "internal-guide" in dm.refresh()
    stored = "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "home").rglob("*auth*.md"))
    assert "2h" in stored


def test_changing_code_surfaces_docs_for_the_libraries_it_uses(tmp_path, monkeypatch, capsys):
    from devmem_app.indexer import index_repo
    repo = tmp_path / "svc"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "signup.py").write_text(
        "import json\nfrom fancylib import validate_email\n\n\ndef signup(addr):\n    return validate_email(addr)\n\n\n"
        "def dump(x):\n    return json.dumps(x)\n", encoding="utf-8")
    lib = repo / ".devmem/docs/fancylib"
    lib.mkdir(parents=True)
    (lib / "01-email.md").write_text("# Validating email\nUse `validate_email(addr, check_deliverability=False)` since 3.0.\n\n"
                                     "# Parsing dates\nUse parse_date.\n", encoding="utf-8")
    (lib / "manifest.json").write_text(json.dumps({"name": "fancylib", "source": "pypi:fancylib", "version": "3.1", "fetched": "2026-10-30",
                                                   "pages": [{"file": "01-email.md", "url": "https://fancylib.dev/email", "title": "Email"}]}), encoding="utf-8")
    index_repo(repo)
    monkeypatch.chdir(repo)
    assert main(["show", "signup"]) == 0
    out = capsys.readouterr().out
    assert "# library docs for this code" in out and "fancylib 3.1" in out and "Validating email" in out
    assert main(["show", "dump"]) == 0 and "library docs" not in capsys.readouterr().out      # json is stdlib, no docs
    assert main(["recall", "change signup validation", "--files", "app/signup.py"]) == 0
    assert "LIBRARY DOCS FOR THIS CODE" in capsys.readouterr().out
    assert main(["docs", "--for", "app/signup.py"]) == 0 and "check_deliverability" in capsys.readouterr().out
