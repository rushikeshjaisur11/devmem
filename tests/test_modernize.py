"""Modernize: hand-written code the language or a library provides, with whether it can be used now."""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from devmem import cli as agent_cli
from devmem.index import Index
from devmem_app import indexer, modernize, prompts
from devmem_app.server import create_app

PY = '''import os, random, string, time, tempfile, shutil, re
from datetime import datetime

_cache = {}


def fetch_with_retry(fn, attempts=3):
    for i in range(attempts):
        try:
            return fn()
        except Exception:
            time.sleep(2 ** i)


def expensive(key):
    if key in _cache:
        return _cache[key]
    _cache[key] = key * 2
    return _cache[key]


def in_batches(items, n):
    return [items[i:i + n] for i in range(0, len(items), n)]


def neighbours(xs):
    return list(zip(xs, xs[1:]))


def strip_prefix(name, prefix):
    if name.startswith(prefix):
        return name[len(prefix):]
    return name


def tally(words):
    counts = {}
    for w in words:
        counts[w] = counts.get(w, 0) + 1
    return counts


def stamp():
    return datetime.utcnow().isoformat()


def new_token(n=16):
    return "".join(random.choice(string.ascii_letters) for _ in range(n))


def is_valid_email(addr):
    return re.match(r"[^@]+@[^@]+\\.[^@]+", addr) is not None


def already_modern(xs):
    """Uses the built-ins: nothing to report."""
    from itertools import batched
    return list(batched(xs, 3))


class Point:
    def __init__(self, x, y, z):
        self.x = x
        self.y = y
        self.z = z

    def __eq__(self, other):
        return (self.x, self.y, self.z) == (other.x, other.y, other.z)
'''
JS = '''export function clone(obj) {
  return JSON.parse(JSON.stringify(obj));
}

export function newId() {
  return Math.random().toString(36).slice(2);
}

export function toQuery(params) {
  return Object.keys(params).map((k) => encodeURIComponent(k) + "=" + encodeURIComponent(params[k])).join("&");
}

export function add(a, b) {
  return a + b;
}
'''


def _repo(tmp_path, requires="3.9", deps="requests>=2\n"):
    root = tmp_path / "shop"
    (root / "app").mkdir(parents=True)
    (root / "web").mkdir()
    (root / "tests").mkdir()
    (root / "app/util.py").write_text(PY)
    (root / "web/util.js").write_text(JS)
    (root / "tests/test_util.py").write_text("from app.util import fetch_with_retry\n\n\ndef test_retry():\n    assert fetch_with_retry(lambda: 1) == 1\n")
    (root / "pyproject.toml").write_text(f'[project]\nname = "shop"\nrequires-python = ">={requires}"\n')
    (root / "requirements.txt").write_text(deps)
    (root / "package.json").write_text('{"name": "shop-web", "engines": {"node": ">=18"}}')
    indexer.index_repo(root, "shop", accuracy="fast")
    return root


def test_scan_finds_hand_written_patterns_and_says_what_is_usable(tmp_path):
    root = _repo(tmp_path)
    d = modernize.scan(Index.load(root))
    by = {f["name"]: f for f in d["findings"]}
    assert d["runtime"] == {"python": "3.9", "node": "18"}
    assert set(by) == {"fetch_with_retry", "expensive", "in_batches", "neighbours", "strip_prefix", "tally", "stamp", "new_token", "is_valid_email", "Point",
                       "clone", "newId", "toQuery"}                                  # not already_modern, not add
    assert (by["in_batches"]["rule"], by["in_batches"]["status"]) == ("py-chunk", "upgrade") and "needs Python 3.12+; this repo declares 3.9" in by["in_batches"]["needs"]
    assert by["neighbours"]["status"] == "upgrade" and by["stamp"]["status"] == "upgrade"         # 3.10 and 3.11 features on a 3.9 repo
    assert by["strip_prefix"]["status"] == "ready" and by["tally"]["use"] == "`collections.Counter`" and by["expensive"]["status"] == "ready"
    assert by["fetch_with_retry"]["status"] == "new-dependency" and "tenacity" in by["fetch_with_retry"]["needs"]
    assert by["fetch_with_retry"]["callers"] == 0 and by["fetch_with_retry"]["evidence"] == "time.sleep(2 ** i)" and by["fetch_with_retry"]["line"] == 12
    assert by["Point"]["rule"] == "py-manual-dataclass" and by["new_token"]["use"].startswith("`secrets.token_urlsafe`")
    assert (by["clone"]["use"], by["clone"]["status"], by["clone"]["source"]) == ("`structuredClone(x)`", "ready", "the JavaScript runtime")
    assert by["newId"]["rule"] == "js-uuid" and by["toQuery"]["rule"] == "js-query-string"
    assert [f["status"] for f in d["findings"]] == sorted((f["status"] for f in d["findings"]), key=["ready", "upgrade", "new-dependency"].index)
    assert json.loads((root / ".devmem/modernize.json").read_text())["scanned"] == d["scanned"]


def test_declared_versions_and_dependencies_change_the_verdict(tmp_path):
    root = _repo(tmp_path, requires="3.12", deps="tenacity>=8\nemail-validator\n")
    by = {f["name"]: f for f in modernize.scan(Index.load(root))["findings"]}
    assert by["in_batches"]["status"] == "ready" and "this repo declares 3.12" in by["in_batches"]["needs"]
    assert by["fetch_with_retry"]["status"] == "ready" and by["fetch_with_retry"]["source"] == "a library you already use" and "tenacity >=8 is already a dependency" in by["fetch_with_retry"]["needs"]
    assert by["is_valid_email"]["status"] == "ready"
    (root / "pyproject.toml").unlink()                                                # no declared version: say so instead of guessing
    by = {f["name"]: f for f in modernize.scan(Index.load(root))["findings"]}
    assert by["in_batches"]["status"] == "ready" and "does not declare a minimum version" in by["in_batches"]["needs"]


def test_scan_stays_fast_on_long_functions(tmp_path):
    import time
    root = tmp_path / "r"
    root.mkdir()
    body = "".join(f"    for i{n} in range(3):\n        x = try_me({n})\n" for n in range(1500))
    (root / "big.py").write_text(f"def huge():\n{body}    return 1\n")
    indexer.index_repo(root, "r", accuracy="fast")
    t = time.time()
    assert modernize.scan(Index.load(root))["findings"] == []
    assert time.time() - t < 3


def test_ai_check_can_only_confirm_or_reject(tmp_path):
    root = _repo(tmp_path)
    ix = Index.load(root)
    d = modernize.scan(ix)

    class Model:
        def generate(self, prompt):
            assert "Claim: this is" in prompt and "def " in prompt
            return json.dumps([{"i": 0, "verdict": "no", "note": "It logs each attempt, which the built-in would not."}, {"i": 1, "verdict": "yes", "note": "nothing"}])
    keys = [f"{f['id']}|{f['rule']}" for f in d["findings"][:2]]
    assert modernize.verify_with_ai(ix, keys, Model()) == {"checked": 2, "error": ""}
    after = modernize.load(root)["findings"]
    assert len(after) == len(d["findings"]) and after[0]["ai"] == {"verdict": "no", "note": "It logs each attempt, which the built-in would not."} and after[1]["ai"]["verdict"] == "yes"


def test_prompt_agents_and_api(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("DEVMEM_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("DEVMEM_TOKEN", raising=False)
    root = _repo(tmp_path, requires="3.12")
    ix = Index.load(root)
    f = next(x for x in modernize.scan(ix)["findings"] if x["name"] == "in_batches")
    text = prompts.build(ix, "modernize", f)["prompt"]
    assert "`in_batches` in `app/util.py` hand-writes splitting a sequence into chunks" in text and "Replace that part with `itertools.batched`" in text
    assert "1. Confirm the version first" in text and "Write down every behaviour of the hand-written part" in text and "def in_batches(items, n):" in text
    new_dep = next(x for x in modernize.load(root)["findings"] if x["status"] == "new-dependency")
    assert "Stop and ask for approval before adding it" in prompts.build(ix, "modernize", new_dep)["prompt"]

    capsys.readouterr()
    assert agent_cli.run(["hygiene", "app/util.py"], root=root) == 0
    out = capsys.readouterr().out
    assert "modernize py-chunk hand-written splitting a sequence into chunks" in out and "use `itertools.batched`" in out
    assert "py-retry-loop" not in out                                                 # a new dependency is not pushed at agents

    api, H = TestClient(create_app()), {"X-Devmem": "1"}
    assert api.post("/api/repos", json={"path": str(root)}, headers=H).status_code == 200
    monkeypatch.setattr("devmem_app.docs.write_libs", lambda root, online=True: [])   # no network in tests
    (root / ".devmem/modernize.json").unlink()
    assert api.get("/api/repos/shop/modernize").json()["available"] is False
    r = api.post("/api/repos/shop/modernize", headers=H).json()
    assert r["available"] and r["counts"]["ready"] >= 8 and r["lines"] > 0 and r["llm"]["configured"] is False
    assert api.post("/api/repos/shop/modernize/ai", json={"keys": ["x|y"]}, headers=H).status_code == 409


def test_scan_compares_with_latest_releases_docs_and_release_notes(tmp_path, monkeypatch):
    from devmem_app import docs as dm
    monkeypatch.setenv("DEVMEM_HOME", str(tmp_path / "home"))
    root = _repo(tmp_path, requires="3.12", deps="requests==2.25.0\ntenacity>=8\npyyaml==6.0\n")
    (root / "app/net.py").write_text("import requests\nimport yaml\n\n\ndef get_json(url):\n    return requests.get(url).json()\n\n\ndef load_cfg(p):\n    return yaml.safe_load(open(p))\n")
    (root / "app/svc.py").write_text("from app.net import get_json\n\n\ndef user(i):\n    return get_json(f'/u/{i}')\n")
    indexer.index_repo(root, "shop", accuracy="fast")
    ix = Index.load(root)
    latest = {"requests": "2.34.2", "tenacity": "9.2.1", "pyyaml": "6.0"}
    monkeypatch.setattr(dm, "latest_version", lambda eco, name: latest.get(name))
    added = []

    def fake_add(spec, name=None, follow=True, max_pages=40, dest=None):
        added.append(spec)
        if "tenacity" in spec:
            raise RuntimeError("offline")
        return {"name": spec.split(":")[1], "version": latest[spec.split(":")[1]], "pages": [], "bytes": 0}
    monkeypatch.setattr(dm, "add", fake_add)
    monkeypatch.setattr(dm, "source_repo", lambda eco, name: f"org/{name}")
    monkeypatch.setattr(dm, "release_notes", lambda repo, frm, to: [("v2.30.0", "## What's Changed\n- Fixed a typo\n- Added built-in retry support for idempotent requests via `Retry`\n"),
                                                                    ("v2.34.0", "* New `Session.json()` helper replaces manual decoding\n* @someone made a change\n")])
    info = modernize.refresh_latest(ix)
    assert sorted(added) == ["pypi:pyyaml", "pypi:requests", "pypi:tenacity"]                       # docs fetched for libraries with none on file
    assert [d["name"] for d in info["docs_fetched"]] == ["requests", "pyyaml"] or {d["name"] for d in info["docs_fetched"]} == {"requests", "pyyaml"}
    assert info["docs_failed"] == [{"name": "tenacity", "version": "", "error": "offline"}] and info["latest"]["tenacity"] == "9.2.1"
    assert info["notes"]["requests"]["highlights"] == ["v2.34.0: New `Session.json()` helper replaces manual decoding",
                                                       "v2.30.0: Added built-in retry support for idempotent requests via `Retry`"]   # newest first, noise dropped
    assert "pyyaml" not in info["notes"]                                                            # not behind: no notes fetched

    d = modernize.scan(ix, info)
    by = {b["name"]: b for b in d["behind"]}
    assert set(by) == {"requests", "tenacity"} and by["requests"]["latest"] == "2.34.2" and by["requests"]["behind"] == "minor"
    assert by["requests"]["functions"] == 2 and by["requests"]["top"][0]["name"] == "get_json"       # the most called function that uses it comes first
    assert by["requests"]["highlights"][0].startswith("v2.34.0") and by["requests"]["notes_url"] == "https://github.com/org/requests/releases"
    retry = next(f for f in d["findings"] if f["name"] == "fetch_with_retry")
    assert retry["needs"] == "tenacity >=8 is already a dependency; the latest release is 9.2.1" and retry["url"].startswith("https://tenacity.readthedocs.io")
    assert next(f for f in d["findings"] if f["name"] == "tally")["url"] == "https://docs.python.org/3/library/collections.html#collections.Counter"
    assert all(f["url"] for f in d["findings"])                                                     # every candidate links to current documentation
    assert "Current documentation: https://tenacity.readthedocs.io" in prompts.build(ix, "modernize", retry)["prompt"]
    again = modernize.scan(ix)                                                                      # a scan without the network keeps what the last one fetched
    assert again["latest_info"]["latest"] == info["latest"] and {b["name"] for b in again["behind"]} == {"requests", "tenacity"}

    monkeypatch.setattr(dm, "release_notes", lambda repo, frm, to: (_ for _ in ()).throw(RuntimeError("HTTP Error 403: Forbidden")))
    assert "set GITHUB_TOKEN" in modernize.refresh_latest(ix)["notes_error"]                        # says why, and the scan still runs
    monkeypatch.setattr(dm, "write_libs", lambda root, online=True: (_ for _ in ()).throw(OSError("no network")))
    off = modernize.refresh_latest(ix)
    assert "could not check the package registries" in off["error"] and modernize.scan(ix, off)["findings"]


def test_import_names_map_to_package_names(tmp_path):
    root = _repo(tmp_path)
    (root / "app/cfg.py").write_text("import yaml\nfrom PIL import Image\n\n\ndef load(p):\n    return yaml.safe_load(p)\n")
    (root / "web/api.ts").write_text("import { z } from 'zod';\nimport x from '@scope/pkg/sub';\nimport y from './local';\n\nexport function parse(v: unknown) {\n  return z.string().parse(v);\n}\n")
    indexer.index_repo(root, "shop", accuracy="fast")
    used = modernize._usage(Index.load(root), [{"name": "PyYAML"}, {"name": "pillow"}, {"name": "zod"}, {"name": "@scope/pkg"}, {"name": "local"}])
    assert {k: [s["name"] for s in v] for k, v in used.items()} == {"pyyaml": ["load"], "pillow": ["load"], "zod": ["parse"], "@scope/pkg": ["parse"]}


def _behind_repo(tmp_path, monkeypatch):
    from devmem_app import docs as dm
    monkeypatch.setenv("DEVMEM_HOME", str(tmp_path / "home"))
    root = _repo(tmp_path, requires="3.12", deps="requests==2.25.0\n")
    (root / "app/net.py").write_text('import requests\n\n\ndef get_json(url):\n    """Fetch a URL and decode the JSON body."""\n    return requests.get(url).json()\n\n\n'
                                     'def send_with_retry(url):\n    for _ in range(3):\n        r = requests.get(url)\n        if r.ok:\n            return r\n\n\ndef unrelated(x):\n    return x + 1\n')
    indexer.index_repo(root, "shop", accuracy="fast")
    monkeypatch.setattr(dm, "latest_version", lambda eco, name: "2.34.2")
    monkeypatch.setattr(dm, "add", lambda spec, name=None, follow=True, max_pages=40, dest=None: {"name": "requests", "version": "2.34.2", "pages": [], "bytes": 0})
    monkeypatch.setattr(dm, "source_repo", lambda eco, name: "psf/requests")
    return root, dm


def test_features_of_newer_releases_are_matched_to_hand_written_code(tmp_path, monkeypatch):
    root, dm = _behind_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(dm, "release_notes", lambda repo, frm, to: [("v2.30.0", "- Added built-in `Retry` support: `Session.send` can retry idempotent requests\n- Fixed a crash on Windows\n"),
                                                                    ("v2.34.0", "- New `Session.json()` helper decodes the response body\n- Bump urllib3\n")])
    ix = Index.load(root)
    d = modernize.scan(ix, modernize.refresh_latest(ix))
    rel = {f["name"]: f for f in d["findings"] if f["rule"] == "new-release"}
    assert set(rel) == {"get_json", "send_with_retry"}                                          # not `unrelated`, not functions in other files
    g = rel["get_json"]
    assert g["status"] == "upgrade" and g["use"] == "requests v2.34.0: New `Session.json()` helper decodes the response body"
    assert "requires upgrading requests from 2.25.0 to 2.34.2" in g["needs"] and "json" in g["needs"] and g["url"] == "https://github.com/psf/requests/releases"
    assert rel["send_with_retry"]["use"].startswith("requests v2.30.0: Added built-in `Retry` support") and g["confidence"] in ("medium", "low")
    text = prompts.build(ix, "modernize", g)["prompt"]
    assert "hand-writes something requests v2.34.0 now does" in text and "def get_json(url):" in text


def test_changelog_file_is_read_when_the_releases_api_refuses(tmp_path, monkeypatch):
    root, dm = _behind_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(dm, "release_notes", lambda repo, frm, to: (_ for _ in ()).throw(RuntimeError("HTTP Error 403: Forbidden")))
    history = ("Release History\n===============\n\n2.34.0 (2026-05-01)\n-------------------\n\n**Improvements**\n- New `Session.json()` helper decodes the response body\n\n"
               "2.26.0 (2021-07-13)\n-------------------\n- Added support for Brotli decoding\n\n2.25.0 (2020-11-11)\n-------------------\n- Added support for NETRC\n\n2.20.0\n------\n- Old news\n")
    monkeypatch.setattr(dm, "_try", lambda url: history if url.endswith("/psf/requests/HEAD/HISTORY.md") else None)
    assert [v for v, _ in modernize.changelog_notes("psf/requests", "2.25.0", "2.34.2")] == ["2.26.0", "2.34.0"]      # newer than the version in use only
    info = modernize.refresh_latest(Index.load(root))
    assert info["notes"]["requests"]["highlights"] == ["2.34.0: New `Session.json()` helper decodes the response body", "2.26.0: Added support for Brotli decoding"]
    assert info["notes_error"] == ""
    monkeypatch.setattr(dm, "_try", lambda url: None)
    assert "no release notes or changelog could be read for requests" in modernize.refresh_latest(Index.load(root))["notes_error"]


def test_model_can_add_candidates_from_release_notes_marked_as_suggestions(tmp_path, monkeypatch):
    root, dm = _behind_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(dm, "release_notes", lambda repo, frm, to: [("v2.31.0", "- Added a connection pool size option to `HTTPAdapter`\n")])
    ix = Index.load(root)
    d = modernize.scan(ix, modernize.refresh_latest(ix))
    assert not [f for f in d["findings"] if f["rule"] == "new-release"]                          # the words do not match any function name

    class Model:
        def generate(self, prompt):
            assert "ADDED IN NEWER RELEASES\nN0. v2.31.0: Added a connection pool size option" in prompt and "### F0 " in prompt and "requests 2.25.0" in prompt
            i = prompt.split("send_with_retry")[0].count("### F")
            return f'Here you go: [{{"f": {i - 1}, "n": 0, "why": "It opens a new connection per attempt; the pooled adapter covers that."}}]'
    r = modernize.discover_with_ai(ix, Model())
    assert r == {"added": 1, "libraries": 1, "error": ""}
    f = next(x for x in modernize.load(root)["findings"] if x["rule"] == "ai-release")
    assert (f["name"], f["confidence"], f["status"]) == ("send_with_retry", "model", "upgrade") and "pooled adapter" in f["ai"]["note"] and "suggested by the model" in f["needs"]
    assert modernize.discover_with_ai(ix, Model())["added"] == 0                                # not reported twice
    kept = [x["name"] for x in modernize.scan(ix)["findings"] if x["rule"] == "ai-release"]
    assert kept == ["send_with_retry"]                                                          # survives a rescan while the function exists

    class Down:
        def generate(self, prompt):
            raise RuntimeError("model unreachable")
    assert modernize.discover_with_ai(ix, Down())["error"] == "model unreachable"


def test_online_docs_refresh_on_their_own(tmp_path, monkeypatch):
    from devmem_app import docs as dm
    monkeypatch.setenv("DEVMEM_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("DEVMEM_DOCS_REFRESH_DAYS", raising=False)
    monkeypatch.delenv("DEVMEM_TOKEN", raising=False)
    libs = [{"name": "requests", "kind": "pypi", "age_days": 9, "version": "2.30", "fetched": "x"}, {"name": "zod", "kind": "npm", "age_days": 2, "version": "3", "fetched": "x"},
            {"name": "guide", "kind": "upload", "age_days": 400, "version": "", "fetched": "x"}, {"name": "site", "kind": "url", "age_days": 30, "version": "", "fetched": "x"}]
    monkeypatch.setattr(dm, "list_libs", lambda base=None: libs)
    asked = []
    monkeypatch.setattr(dm, "refresh", lambda names=None: asked.append(names) or {n: "ok" for n in names})
    assert dm.refresh_days() == 7 and dm.refresh_due() == {"requests": "ok", "site": "ok"} and asked == [["requests", "site"]]   # only online docs past the interval
    assert dm.set_refresh_days(1) == 1 and set(dm.refresh_due()) == {"requests", "zod", "site"}
    assert dm.set_refresh_days(0) == 0 and dm.refresh_due() == {}                                 # never
    monkeypatch.setenv("DEVMEM_DOCS_REFRESH_DAYS", "30")
    assert dm.refresh_days() == 30 and set(dm.refresh_due()) == {"site"}
    monkeypatch.delenv("DEVMEM_DOCS_REFRESH_DAYS")
    api, H = TestClient(create_app()), {"X-Devmem": "1"}
    assert api.get("/api/docs/settings").json() == {"refresh_days": 0, "from_environment": False}
    assert api.put("/api/docs/settings", json={"refresh_days": 7}, headers=H).json()["refresh_days"] == 7
    assert api.post("/api/docs/refresh", json={"names": ["zod"]}, headers=H).json() == {"zod": "ok"} and asked[-1] == ["zod"]
