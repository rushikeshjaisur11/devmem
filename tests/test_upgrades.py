"""Docs from a library's git repository, the release watcher and upgrade prompts. Offline: the "library" is a local git
repository served over file://, the registry answer is stubbed, and web pages come from a local HTTP server."""
import functools
import http.server
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from devmem_app import docs, releases, sources, upgrade, websearch
from devmem_app.indexer import index_repo

CHANGELOG = """# Changelog

## 2.1.0
- Added `fancy.batch` helper for validating many records at once.

## 2.0.0
**Breaking changes:**
  - `old_call` was removed; use `new_call` instead.
- Fixed a typo in the readme.
- The deprecated `legacy_mode` argument has been removed.
- `Settings.parse_obj` is deprecated, use `Settings.model_validate`.

## 1.5.0
- `retry` now supports exponential backoff.

## 1.0.0
- First release.
"""


def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture()
def lib_repo(tmp_path):
    """A library's repository: README, docs (plus a translation and noise), migration guide, changelog; tags v1.0.0 .. v2.1.0."""
    r = tmp_path / "fancylib-src"
    (r / "docs/zh").mkdir(parents=True)
    _git(tmp_path, "init", "-q", "-b", "main", str(r))
    (r / "README.md").write_text("# Fancylib\n\nFancylib validates things. Version one.\n")
    (r / "CHANGELOG.md").write_text(CHANGELOG.split("## 2.0.0")[0].split("## 2.1.0")[0] + "## 1.0.0\n- First release.\n")
    _git(r, "add", "-A"); _git(r, "commit", "-qm", "v1"); _git(r, "tag", "v1.0.0")
    (r / "README.md").write_text("# Fancylib\n\nFancylib validates things. Call `fancy.new_call(settings)`.\n")
    (r / "docs/index.md").write_text("# Fancylib docs\n\nStart with `new_call`, which validates a settings object and returns it.\n")
    (r / "docs/migration.md").write_text("# Migrating to v2\n\n- `old_call` was removed: use `new_call` instead.\n"
                                         "- `legacy_mode` no longer exists; strict validation is always on.\n")
    (r / "docs/zh/index.md").write_text("# 中文文档\n\n翻译的文档内容，不应被选中。这是一段足够长的文字。\n")
    (r / "CONTRIBUTING.md").write_text("# Contributing\n\nOpen a pull request against main and add a test for it.\n")
    (r / "CHANGELOG.md").write_text(CHANGELOG)
    _git(r, "add", "-A"); _git(r, "commit", "-qm", "v2"); _git(r, "tag", "v2.0.0"); _git(r, "tag", "v2.1.0")
    return {"git": f"file://{r}", "web": "https://git.example.com/acme/fancylib", "host": "git.example.com", "path": "acme/fancylib", "subdir": ""}


@pytest.fixture()
def registry(monkeypatch, lib_repo):
    """Stub of the registry answer: versions per package (change them to simulate a release); fancylib lives in lib_repo."""
    versions = {"fancylib": "2.1.0"}

    def meta(eco, name, want=""):
        if name == "nosuchpkg":
            raise RuntimeError("404")
        return {"eco": eco, "name": name, "version": want or versions.get(name, "9.0.0"), "summary": f"{name} summary",
                "readme": "REGISTRY README: should not be used when the repository has docs", "repo": lib_repo if name == "fancylib" else None,
                "docs_url": None, "released": "2026-01-02", "registry_url": f"https://pypi.org/project/{name}/", "requires_python": ">=3.9"}
    monkeypatch.setattr(sources, "package_meta", meta)
    monkeypatch.setattr(docs, "latest_version", lambda eco, name: versions.get(name, "9.0.0"))
    return versions


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVMEM_HOME", str(tmp_path / "home"))
    for k in ("DEVMEM_SEARCH", "DEVMEM_SEARXNG_URL", "BRAVE_API_KEY", "TAVILY_API_KEY", "GOOGLE_CSE_KEY", "GOOGLE_CSE_ID", "DEVMEM_SEARCH_KEY",
              "DEVMEM_RELEASE_CHECK_HOURS", "DEVMEM_RELEASE_WEBHOOK"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path / "home"


@pytest.fixture()
def app_repo(tmp_path):
    """A repo on fancylib 1.0.0 that still calls the APIs 2.0 removed, plus a module one import away that uses the model."""
    r = tmp_path / "app"
    (r / "app").mkdir(parents=True); (r / "tests").mkdir()
    (r / "requirements.txt").write_text("fancylib==1.0.0\nrequests>=2\n")
    (r / "app/__init__.py").write_text("")
    (r / "app/core.py").write_text("import fancylib\nfrom fancylib import Settings\n\n\ndef load(raw):\n    s = Settings.parse_obj(raw)\n"
                                   "    return fancylib.old_call(s, legacy_mode=True)\n")
    (r / "app/use.py").write_text("from app.core import load\n\n\ndef run(model):\n    load({})\n    return model.parse_obj({})\n")
    (r / "tests/test_core.py").write_text("from app.core import load\n\n\ndef test_load():\n    assert load is not None\n")
    _git(tmp_path, "init", "-q", str(r)); _git(r, "add", "-A"); _git(r, "commit", "-qm", "init")
    index_repo(r, "app")
    return r


@pytest.fixture()
def www(tmp_path, monkeypatch):
    root = tmp_path / "www"; root.mkdir()
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("NO_PROXY", "127.0.0.1"); monkeypatch.setenv("no_proxy", "127.0.0.1")
    yield root, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


# ---------- sources ----------
def test_repository_urls_in_every_form_registries_use():
    n = sources.norm_repo
    assert n("git+https://github.com/encode/httpx.git")["git"] == "https://github.com/encode/httpx.git"
    assert n("git@github.com:colinhacks/zod.git")["web"] == "https://github.com/colinhacks/zod"
    assert n("github:owner/repo")["path"] == "owner/repo" and n("owner/repo")["host"] == "github.com"
    t = n("https://github.com/TanStack/query/tree/main/packages/react-query")
    assert t["path"] == "TanStack/query" and t["subdir"] == "packages/react-query"
    assert n("https://gitlab.com/group/sub/proj/-/tree/main/docs")["path"] == "group/sub/proj"
    assert n("https://github.com/psf/requests/issues") is not None and n("https://github.com/psf/requests/issues")["path"] == "psf/requests"
    assert n("https://requests.readthedocs.io") is None and n("") is None


def test_release_tags_are_matched_per_package():
    assert sources.tag_for(["v1.9.0", "v2.0.0", "2.0.0-rc1"], "2.0.0") == "v2.0.0"
    assert sources.tag_for(["rel_2_1_8", "rel_2_1_9"], "2.1.9", "sqlalchemy") == "rel_2_1_9"
    assert sources.tag_for(["@tanstack/preact-query@5.1.0", "@tanstack/react-query@5.1.0"], "5.1.0", "@tanstack/react-query") == "@tanstack/react-query@5.1.0"
    assert sources.tag_for(["dotenv-v16.0.0"], "16.0.0", "dotenv") == "dotenv-v16.0.0"
    assert sources.tag_for(["other-pkg@1.0.0"], "1.0.0", "mine") is None          # a sibling's tag is not ours
    assert sources.tag_for(["v1.0.0"], "") is None


def test_doc_files_are_chosen_like_a_reader_would():
    files = ["README.md", "README.zh-CN.md", "CONTRIBUTING.md", "CHANGELOG.md", "docs/index.md", "docs/zh/index.md", "docs/guide/start.md",
             "docs/requirements.txt", "docs/migration.md", "tests/README.md", "src/x.md", "node_modules/a/README.md", "docs/_build/x.md"]
    got = sources.select_doc_files(files, "", 50, "fancylib")
    assert got[0] == "README.md" and got[1] == "docs/migration.md" and got[-1] == "CHANGELOG.md"
    assert set(got) == {"README.md", "CHANGELOG.md", "docs/index.md", "docs/guide/start.md", "docs/migration.md"}
    mono = ["README.md", "packages/react-query/README.md", "packages/react-query/CHANGELOG.md", "docs/framework/react/overview.md",
            "docs/framework/svelte/overview.md", "docs/framework/react/guides/migrating-to-v5.md", "docs/reference/index.md"]
    got = sources.select_doc_files(mono, "packages/react-query", 50, "@tanstack/react-query")
    assert got[0] == "packages/react-query/README.md" and "docs/framework/svelte/overview.md" not in got
    assert "docs/framework/react/guides/migrating-to-v5.md" in got and "docs/reference/index.md" in got


def test_changelog_sections_and_rst():
    secs = dict(sources.parse_changelog(CHANGELOG + "\nv0.9 (2020-01-01)\n-----------------\n* old\n"))
    assert set(secs) == {"2.1.0", "2.0.0", "1.5.0", "1.0.0", "0.9"} and "old_call" in secs["2.0.0"]
    md = sources._rst_to_md("Title\n=====\n\nUse :func:`fancy.new_call` and ``x``::\n\n    code()\n\nSub\n---\n")
    assert "# Title" in md and "`fancy.new_call`" in md and "```" in md and "## Sub" in md


def test_git_docs_read_at_the_release_tag(lib_repo):
    got = sources.git_docs(lib_repo, "1.0.0", "fancylib")
    assert got["ref"] == "v1.0.0" and got["tagged"] and "Version one" in got["pages"][0][2]       # the v1 README, not the current one
    got = sources.git_docs(lib_repo, "2.1.0", "fancylib")
    paths = got["paths"]
    assert got["ref"] == "v2.1.0" and paths[0] == "README.md" and "docs/migration.md" in paths and "CHANGELOG.md" in paths
    assert not any(p.startswith("docs/zh") or p == "CONTRIBUTING.md" for p in paths)
    assert got["pages"][0][1] == "https://git.example.com/acme/fancylib/blob/v2.1.0/README.md"


def test_package_docs_come_from_the_repository_not_the_registry(home, registry, lib_repo):
    m = docs.add("pypi:fancylib")
    assert m["kind"] == "pypi" and m["version"] == "2.1.0" and m["ref"] == "v2.1.0" and m["repo"] == "https://git.example.com/acme/fancylib"
    assert m["sources"] == ["git https://git.example.com/acme/fancylib @ v2.1.0"]
    text = "".join((docs.docs_dir() / "fancylib" / p["file"]).read_text() for p in m["pages"])
    assert "REGISTRY README" not in text and "Migrating to v2" in text and "new_call" in text
    listed = docs.list_libs()[0]
    assert listed["repo"] and listed["sources"]
    m = docs.add(f"git:{lib_repo['git']}#v1.0.0", "fancy-v1")            # any git URL, at a tag
    assert m["kind"] == "git" and m["version"] == "1.0.0" and "Version one" in (docs.docs_dir() / "fancy-v1" / m["pages"][0]["file"]).read_text()


def test_without_a_repository_the_registry_readme_is_the_fallback(home, registry):
    m = docs.add("pypi:plainlib")
    assert m["sources"] == ["registry https://pypi.org/project/plainlib/"]
    assert "REGISTRY README" in (docs.docs_dir() / "plainlib" / m["pages"][0]["file"]).read_text()


def test_release_notes_from_the_changelog_in_git(lib_repo):
    got = sources.notes_between(lib_repo, "1.0.0", "2.1.0", "fancylib")
    assert [v for v, _ in got["notes"]] == ["1.5.0", "2.0.0", "2.1.0"] and got["source"] == "CHANGELOG.md"
    assert sources.notes_between(None, "1", "2")["error"]


# ---------- upgrade prompt ----------
def test_change_notes_are_classified():
    c = upgrade.classify([("2.0.0", CHANGELOG.split("## 2.0.0")[1].split("## 1.5.0")[0]), ("2.1.0", "- Added `fancy.batch` helper for many records.")])
    brk = [x["text"] for x in c["breaking"]]
    assert any("old_call" in t for t in brk) and any("legacy_mode" in t for t in brk)      # "the deprecated X has been removed" is breaking
    assert not any("typo" in t for t in brk)                                              # the bold label covers only the nested bullets
    assert [x["text"] for x in c["deprecated"]] == ["`Settings.parse_obj` is deprecated, use `Settings.model_validate`."]
    assert c["added"] and "parse_obj" in c["deprecated"][0]["idents"]
    sec = upgrade.classify([("3.0.1", "- Fix CVE-2026-1234 in the parser")])
    assert sec["security"]
    later = upgrade.classify([("0.26.0", "- The `proxies` argument is now deprecated. It will still work, but it will be removed in the future."),
                              ("0.28.0", "- The deprecated `proxies` argument has now been removed.")])
    assert [x["version"] for x in later["deprecated"]] == ["0.26.0"] and [x["version"] for x in later["breaking"]] == ["0.28.0"]
    assert upgrade.idents("Use Mozilla docs instead of `httpstatuses.com` and `setup.py`") == set()        # domains and files are not code


def test_module_names_and_string_literals_are_not_matches(tmp_path):
    from devmem.index import Index
    r = tmp_path / "r"; (r / "app").mkdir(parents=True)
    (r / "requirements.txt").write_text("httpx==0.23\n")
    (r / "app/__init__.py").write_text("")
    (r / "app/c.py").write_text('import httpx\n\n\ndef get():\n    with httpx.Client(proxies=None) as c:\n        return c.get("https://example.com/x")\n')
    index_repo(r, "r")
    ix = Index.load(r)
    use = upgrade.usage(ix, "pypi", "httpx")
    ch = upgrade.classify([("0.24.0", "- NetRC is no longer automatic: use `httpx.NetRCAuth()`.\n- Use Mozilla docs instead of `httpstatuses.com`."),
                           ("0.28.0", "- The deprecated `proxies` argument has now been removed.")])
    upgrade.note_sites(ix, use, ch)
    aff = upgrade.affected(ch, use)
    assert [(c["version"], c["hits"]) for c in aff] == [("0.28.0", ["proxies"])] and aff[0]["sites"][0]["line"] == 5


def test_dependency_lines_and_bumps(tmp_path):
    (tmp_path / "requirements.txt").write_text("# deps\nFancy_Lib[extra]>=1.0,<2 ; python_version>'3'\nother==1\n")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\ndependencies = ["fancy-lib>=1.0"]\n[tool.poetry.dependencies]\nfancy-lib = "^1.0"\n')
    got = upgrade.declared(tmp_path, "pypi", "fancy-lib")
    assert [(d["file"], d["line"]) for d in got] == [("requirements.txt", 2), ("pyproject.toml", 3), ("pyproject.toml", 5)]
    (tmp_path / "package.json").write_text('{\n  "dependencies": {\n    "zod": "^3.22.0",\n    "zodiac": "1"\n  }\n}\n')
    assert [d["line"] for d in upgrade.declared(tmp_path, "npm", "zod")] == [3]
    assert upgrade.new_spec("==1.0.0", "2.1.0") == "==2.1.0" and upgrade.new_spec("^3.22.0", "4.1.0") == "^4.1.0"
    assert upgrade.new_spec(">=1.0,<2", "2.1.0") == ">=2.1.0,<3" and upgrade.new_spec(">=1.0,<5", "2.1.0") == ">=2.1.0,<5"
    assert upgrade.new_spec("", "2.0") == ">=2.0" and upgrade.new_spec("~=1.4", "2.0") == "~=2.0"
    assert upgrade.level("1.4.2", "2.0.0") == "major" and upgrade.level("0.27", "0.28.1") == "major" and upgrade.level("1.4", "1.5") == "minor"
    assert upgrade.level("1.4.1", "1.4.2") == "patch" and upgrade.level("2.0", "2.0.0") == "none"
    (tmp_path / "uv.lock").write_text("")
    assert upgrade.lock_command(tmp_path, "pypi", "fancy-lib", "2.1.0").startswith("uv lock --upgrade-package")
    (tmp_path / "pnpm-lock.yaml").write_text("")
    assert upgrade.lock_command(tmp_path, "npm", "zod", "4.1.0") == "pnpm up zod@4.1.0"


def test_upgrade_prompt_end_to_end(home, registry, app_repo, www, monkeypatch, capsys):
    root, base = www
    (root / "blog.html").write_text("<html><body><h1>Fancylib 2 upgrade</h1><p>In fancylib 2.0 the old_call function was removed; "
                                    "call new_call instead and drop legacy_mode, which no longer exists.</p><p>Unrelated sidebar text "
                                    "about cooking and gardening that is long enough to be a paragraph but says nothing useful.</p></body></html>")
    calls = []
    monkeypatch.setattr(websearch, "search", lambda q, n=6, cfg=None: calls.append(q) or [{"title": "Fancylib 2 upgrade", "url": f"{base}/blog.html", "snippet": "migration"}])
    d = upgrade.plan(app_repo, "fancylib")
    assert (d["from"], d["to"], d["level"]) == ("1.0.0", "2.1.0", "major") and calls and "fancylib 1.0 to 2.1 migration guide" in calls[0]
    assert d["notes"]["source"] == "CHANGELOG.md" and d["notes"]["versions"] == ["1.5.0", "2.0.0", "2.1.0"]
    assert [g["title"] for g in d["guides"]] == ["Migrating to v2"] and d["web"]["pages"][0]["url"].endswith("blog.html")
    hits = {h for c in d["affected"] for h in c["hits"]}
    assert {"old_call", "legacy_mode", "parse_obj"} <= hits
    sites = {(s["file"], s["line"]) for c in d["affected"] for s in c["sites"]}
    assert ("app/core.py", 7) in sites and ("app/use.py", 6) in sites                      # app/use.py does not import fancylib itself
    p = d["prompt"]
    for want in ("Upgrade fancylib from 1.0.0 to 2.1.0", "`requirements.txt` line 1: `fancylib==1.0.0`", "`==1.0.0` to `==2.1.0`",
                 'pip install -U "fancylib==2.1.0"', "tests/test_core.py", "From the web: Fancylib 2 upgrade", "Migration guide: Migrating to v2",
                 "requires Python >=3.9", "devmem docs", "## Done when", "## Report back"):
        assert want in p, want
    assert (app_repo / ".devmem/upgrades/fancylib.md").read_text() == p
    page = [x for x in json.loads((docs.docs_dir() / "fancylib/manifest.json").read_text())["pages"] if x["file"].startswith("upgrade-")]
    assert page and page[0]["file"] == "upgrade-1-0-0-2-1-0.md"
    assert "From the web" in (docs.docs_dir() / "fancylib" / page[0]["file"]).read_text()
    assert upgrade.load(app_repo, "fancylib")["to"] == "2.1.0"

    cands = upgrade.candidates(upgrade.Index.load(app_repo), online=True)
    fancy = next(c for c in cands if c["name"] == "fancylib")
    assert fancy["level"] == "major" and fancy["files"] == 1 and fancy["plan"]["to"] == "2.1.0" and fancy["plan"]["affected"] >= 3
    with pytest.raises(LookupError):
        upgrade.plan(app_repo, "numpy")
    with pytest.raises(ValueError):
        upgrade.plan(app_repo, "fancylib", "0.5.0")

    from devmem.cli import main      # the agent, offline, reads what was prepared
    cwd = os.getcwd(); os.chdir(app_repo)
    try:
        assert main(["upgrade"]) == 0 and "fancylib: 1.0.0 -> 2.1.0 (major)" in capsys.readouterr().out
        assert main(["upgrade", "fancylib"]) == 0 and "Upgrade fancylib from 1.0.0 to 2.1.0" in capsys.readouterr().out
        assert main(["upgrade", "nothere"]) == 1 and "no upgrade plan for nothere" in capsys.readouterr().out
    finally:
        os.chdir(cwd)


def test_upgrade_without_search_or_network_for_search(home, registry, app_repo, monkeypatch):
    def boom(*a, **k):
        raise websearch.SearchError("blocked")
    monkeypatch.setattr(websearch, "search", boom)
    d = upgrade.plan(app_repo, "fancylib", search=True)
    assert d["web"]["error"] == "blocked" and d["affected"]                     # the search failing costs only the web part
    d = upgrade.plan(app_repo, "fancylib", search=False)
    assert d["web"]["error"] == "web search not requested"


def test_model_suggestions_are_added_when_asked(home, registry, app_repo):
    class Fake:
        def generate(self, prompt):
            assert "old_call" in prompt and "app/core.py" in prompt
            return '[{"step": "app/core.py: replace fancylib.old_call(s, legacy_mode=True) with fancylib.new_call(s)"}]'
    d = upgrade.plan(app_repo, "fancylib", search=False, provider=Fake())
    assert d["ai_steps"] and "A model read the notes" in d["prompt"]


# ---------- release watcher ----------
class _Hook(http.server.BaseHTTPRequestHandler):
    got: list = []

    def do_POST(self):
        _Hook.got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(200); self.end_headers()

    def log_message(self, *a):
        pass


def test_release_watcher(home, registry, app_repo, monkeypatch):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Hook)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("NO_PROXY", "127.0.0.1"); monkeypatch.setenv("no_proxy", "127.0.0.1")
    _Hook.got = []
    repos = {"app": str(app_repo)}
    releases.save_settings({"webhook": f"http://127.0.0.1:{srv.server_address[1]}/hook", "interval_hours": 6})
    docs.add("pypi:fancylib")
    docs.write_libs(app_repo, online=False)
    registry["fancylib"] = "2.0.0"
    first = releases.check(repos)
    assert first["new"] == [] and first["checked"] == 2 and not _Hook.got and not releases.due()   # the first sight is a baseline
    registry["fancylib"] = "2.1.0"
    r = releases.check(repos)
    (ev,) = r["new"]
    assert (ev["name"], ev["from"], ev["to"], ev["level"]) == ("fancylib", "2.0.0", "2.1.0", "minor")
    assert ev["repos"] == {"app": "1.0.0"} and ev["behind"] == {"app": "major"} and any("batch" in x for x in ev["summary"]["added"])
    assert "fancylib" in r["refreshed"] and r["webhook"] == "webhook 200"
    assert "fancylib 2.0.0 -> 2.1.0" in _Hook.got[0]["text"] and "app (1.0.0)" in _Hook.got[0]["text"]
    row = next(x for x in json.loads((app_repo / ".devmem/libs.json").read_text()) if x["name"] == "fancylib")
    assert row["latest"] == "2.1.0" and row["behind"] == "major"
    st = releases.status(repos)
    assert st["unread"] == 1 and st["libs"][0]["name"] == "fancylib" and st["libs"][0]["behind"] == {"app": "major"}
    assert releases.mark_read() == 1 and releases.status(repos)["unread"] == 0
    assert releases.check(repos)["new"] == []                                                       # nothing newer: no event
    srv.shutdown()
    with pytest.raises(ValueError):
        releases.save_settings({"webhook": "ftp://x"})


def test_release_watcher_can_prepare_plans(home, registry, app_repo):
    releases.save_settings({"auto_plan": True})
    repos = {"app": str(app_repo)}
    registry["fancylib"] = "2.0.0"
    releases.check(repos)
    registry["fancylib"] = "2.1.0"
    r = releases.check(repos)
    assert "app:fancylib" in r["planned"] and (app_repo / ".devmem/upgrades/fancylib.md").exists()
    assert "registry errors" not in r and releases.check(repos, ["nosuchpkg"])["checked"] == 0


def test_api(home, registry, app_repo, monkeypatch):
    from fastapi.testclient import TestClient
    from devmem_app.server import create_app
    monkeypatch.setattr(websearch, "search", lambda *a, **k: [])
    H = {"X-Devmem": "1"}
    c = TestClient(create_app())
    assert c.post("/api/repos", json={"path": str(app_repo)}, headers=H).status_code == 200
    registry["fancylib"] = "2.0.0"
    assert c.post("/api/releases/check", json={}, headers=H).json()["new"] == []
    registry["fancylib"] = "2.1.0"
    assert len(c.post("/api/releases/check", json={}, headers=H).json()["new"]) == 1
    st = c.get("/api/releases").json()
    assert st["unread"] == 1 and st["settings"]["interval_hours"] == 12
    al = [a for a in c.get("/api/alerts").json() if a["page"] == "upgrades"]
    assert al and "fancylib 2.1.0 released" in al[0]["text"]
    libs = c.get("/api/repos/app/upgrades", params={"refresh": True}).json()["libs"]
    assert libs[0]["name"] == "fancylib" and libs[0]["level"] == "major"
    r = c.post("/api/repos/app/upgrades", json={"lib": "fancylib", "search": False}, headers=H)
    assert r.status_code == 200 and "Upgrade fancylib" in r.json()["prompt"]
    assert c.get("/api/repos/app/upgrades/fancylib").json()["to"] == "2.1.0"
    assert c.get("/api/repos/app/upgrades/zod").status_code == 404
    assert c.post("/api/repos/app/upgrades", json={"lib": "numpy"}, headers=H).status_code == 422
    assert c.post("/api/repos/app/upgrades", json={"lib": "fancylib", "ai": True}, headers=H).status_code == 409   # no model set up
    assert c.post("/api/releases/read", json={}, headers=H).json()["marked"] == 1
    assert c.put("/api/releases/settings", json={"interval_hours": 24}, headers=H).json()["interval_hours"] == 24
    s = c.put("/api/settings/search", json={"provider": "brave", "api_key": "k"}, headers=H).json()
    assert s["provider"] == "brave" and s["key_set"] and s["ready"] and "api_key" not in s


# ---------- web search ----------
def test_search_settings_and_providers(home, www, monkeypatch):
    root, base = www
    assert websearch.effective()["provider"] == "duckduckgo"
    websearch.save({"provider": "google", "api_key": "k"})
    assert not websearch.public()["ready"]                                   # google also needs the engine id
    with pytest.raises(websearch.SearchError, match="needs cx"):
        websearch.search("x")
    websearch.save({"provider": "searxng", "url": base, "api_key": None})
    (root / "search").write_text(json.dumps({"results": [{"title": "Migrating", "url": "https://docs.example.dev/migrate", "content": "v2 guide"},
                                                         {"title": "bad", "url": "javascript:alert(1)"}]}))
    assert websearch.search("fancylib migration") == [{"title": "Migrating", "url": "https://docs.example.dev/migrate", "snippet": "v2 guide"}]
    monkeypatch.setenv("BRAVE_API_KEY", "env-key"); monkeypatch.setenv("DEVMEM_SEARCH", "brave")
    assert websearch.effective()["provider"] == "brave" and websearch.effective()["api_key"] == "env-key" and websearch.public()["from_environment"]
    monkeypatch.setenv("DEVMEM_SEARCH", "off")
    with pytest.raises(websearch.SearchError, match="turned off"):
        websearch.search("x")


def test_duckduckgo_results_are_parsed():
    page = ('<div><a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.example.dev%2Fmigrate&amp;rut=x">'
            'Fancylib <b>migration</b></a><a class="result__snippet" href="#">How to move to <b>v2</b></a></div>'
            '<div><a class="result__a" href="https://duckduckgo.com/y.js?ad=1">Ad</a></div>')
    assert websearch.parse_ddg(page) == [{"title": "Fancylib migration", "url": "https://docs.example.dev/migrate", "snippet": "How to move to v2"}]


def test_app_cli(home, registry, app_repo, monkeypatch, capsys):
    from devmem_app.cli import main
    monkeypatch.setattr(websearch, "search", lambda *a, **k: [])
    assert main(["add", str(app_repo)]) == 0
    capsys.readouterr()
    assert main(["releases", "check"]) == 0 and "checked 2 packages: 0 new" in capsys.readouterr().out
    assert main(["upgrade", "app"]) == 0 and "fancylib (pypi) 1.0.0 -> 2.1.0  [major]" in capsys.readouterr().out
    assert main(["upgrade", "app", "fancylib", "--no-search", "--quiet"]) == 0
    assert "Saved to .devmem/upgrades/" in capsys.readouterr().err
    assert main(["releases", "list"]) == 0 and "fancylib (pypi) latest 2.1.0  app@1.0.0 [major]" in capsys.readouterr().out
