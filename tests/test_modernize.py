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
