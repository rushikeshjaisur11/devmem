"""Upload onboarding, deployed-mode sign-in, AI settings, descriptions for low-confidence definitions, and the
module-resolution speed-up (same answers as the scan it replaced)."""
import io
import json
import shutil
import tarfile
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from devmem.index import Index
from devmem.jsresolve import module_files
from devmem_app import chat, definitions, indexer, settings, uploads
from devmem_app.server import create_app

FIX = Path(__file__).parent.parent / "fixtures"
H = {"X-Devmem": "1"}


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVMEM_HOME", str(tmp_path / "home"))
    for v in ("DEVMEM_TOKEN", "DEVMEM_AI_PROVIDER", "DEVMEM_AI_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
              "DEVMEM_VERTEX_API_KEY", "DEVMEM_VERTEX_PROJECT", "GOOGLE_API_KEY", "DEVMEM_LOCAL_EMBED"):
        monkeypatch.delenv(v, raising=False)
    return TestClient(create_app())


def _zip(files: dict[str, str]) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return b.getvalue()


def _fixture_zip(name: str) -> bytes:
    return _zip({f"{name}/{f.relative_to(FIX / name).as_posix()}": f.read_text() for f in (FIX / name).rglob("*") if f.is_file()})


# ---------- upload ----------
def test_upload_zip_onboards_and_reupload_keeps_devmem_state(api, tmp_path):
    r = api.post("/api/repos/upload?filename=py_app.zip", content=_fixture_zip("py_app"), headers=H)
    assert r.status_code == 200, r.text
    d = r.json()
    root = Path(d["path"])
    assert d["name"] == "py_app" and d["uploaded"] and d["symbols"] >= 6
    assert (root / "app/retry.py").exists() and not (root / "py_app").exists()      # the single top-level folder is stripped
    assert root.parent == tmp_path / "home/uploads"
    (root / ".devmem/rules.md").write_text("keep me")
    r = api.post("/api/repos/upload?name=py_app", content=_zip({"only.py": "def only():\n    return 1\n"}), headers=H)
    assert r.status_code == 200 and r.json()["symbols"] == 1
    assert (root / ".devmem/rules.md").read_text() == "keep me" and not (root / "app").exists()
    assert api.delete("/api/repos/py_app", headers=H).status_code == 200 and not root.exists()   # an upload is devmem's copy


def test_upload_drops_unsafe_and_unwanted_entries(api, tmp_path):
    body = _zip({"../../evil.py": "x = 1", "/abs.py": "x = 1", "C:/win.py": "x = 1", "p/a.py": "def a():\n    return 1\n",
                 "p/.git/config": "[core]\nfsmonitor = touch /tmp/pwned", "p/node_modules/x/index.js": "1"})
    r = api.post("/api/repos/upload?name=safe", content=body, headers=H)
    assert r.status_code == 200, r.text
    root = tmp_path / "home/uploads/safe"
    kept = sorted(f.relative_to(root).as_posix() for f in root.rglob("*") if f.is_file() and ".devmem" not in f.parts)
    assert kept == ["a.py"]
    assert not list(tmp_path.rglob("evil.py")) and not list(tmp_path.rglob("abs.py"))


def test_upload_tar_skips_links_and_rejects_junk(api, tmp_path):
    b = io.BytesIO()
    with tarfile.open(fileobj=b, mode="w:gz") as t:
        data = b"def f():\n    return 1\n"
        info = tarfile.TarInfo("proj/f.py"); info.size = len(data); t.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("proj/passwd"); link.type = tarfile.SYMTYPE; link.linkname = "/etc/passwd"; t.addfile(link)
    r = api.post("/api/repos/upload?filename=proj.tar.gz", content=b.getvalue(), headers=H)
    assert r.status_code == 200 and r.json()["name"] == "proj"
    assert not (tmp_path / "home/uploads/proj/passwd").exists()
    assert api.post("/api/repos/upload?name=junk", content=b"not an archive", headers=H).status_code == 400
    assert api.post("/api/repos/upload?name=../x", content=_zip({"a.py": "x=1"}), headers=H).json()["name"] == "x"   # name is sanitised
    assert api.post("/api/repos/upload", content=_zip({"a.py": "x=1"}), headers=H).status_code == 400          # no name at all


def test_upload_size_limits(api, monkeypatch):
    monkeypatch.setenv("DEVMEM_MAX_UNPACKED_MB", "1")
    big = io.BytesIO()
    with zipfile.ZipFile(big, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("p/big.py", "#" * (3 * 1024 * 1024))    # 3 MB of one character compresses to a few KB
    r = api.post("/api/repos/upload?name=bomb", content=big.getvalue(), headers=H)
    assert r.status_code == 400 and "unpacks to more than" in r.json()["detail"]
    assert "bomb" not in [x["name"] for x in api.get("/api/repos").json()]
    monkeypatch.setenv("DEVMEM_MAX_UPLOAD_MB", "0")
    assert api.post("/api/repos/upload?name=big", content=_zip({"a.py": "x=1"}), headers=H).status_code == 413


def test_upload_does_not_take_over_a_path_repo(api, tmp_path):
    shutil.copytree(FIX / "py_app", tmp_path / "py_app")
    assert api.post("/api/repos", json={"path": str(tmp_path / "py_app")}, headers=H).status_code == 200
    r = api.post("/api/repos/upload?name=py_app", content=_zip({"a.py": "x=1"}), headers=H)
    assert r.status_code == 409 and (tmp_path / "py_app/app/retry.py").exists()


def test_knowledge_zip_after_publish(api):
    api.post("/api/repos/upload?filename=py_app.zip", content=_fixture_zip("py_app"), headers=H)
    assert api.get("/api/repos/py_app/knowledge.zip").status_code == 409            # nothing published yet
    assert api.post("/api/repos/py_app/publish", json={}, headers=H).status_code == 200
    r = api.get("/api/repos/py_app/knowledge.zip")
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert "AGENTS.md" in names and ".devmem/index.json" in names and ".devmem/knowledge/map.md" in names
    assert not any(n.endswith(("index.db", "usage.jsonl")) for n in names)


# ---------- deployed mode ----------
def test_deployed_mode_needs_the_token_and_refuses_server_paths(api, monkeypatch, tmp_path):
    assert api.get("/api/config").json() == {**api.get("/api/config").json(), "deployed": False, "signed_in": True, "paths_allowed": True}
    monkeypatch.setenv("DEVMEM_TOKEN", "tok-123")
    cfg = api.get("/api/config").json()
    assert cfg["deployed"] and not cfg["signed_in"] and not cfg["paths_allowed"]
    assert api.get("/api/repos").status_code == 401
    assert api.get("/").status_code == 200                                           # the UI itself loads, to show sign-in
    assert api.post("/api/login", json={"token": "tok-123"}).status_code == 403      # still needs the anti-CSRF header
    monkeypatch.setattr("devmem_app.server.time.sleep", lambda s: None)
    assert api.post("/api/login", json={"token": "wrong"}, headers=H).status_code == 401
    assert api.get("/api/repos", headers={"Authorization": "Bearer tok-123"}).status_code == 200
    assert api.get("/api/repos", headers={"Authorization": "Bearer tok-123", "Host": "devmem.example.com"}).status_code == 200
    r = api.post("/api/login", json={"token": "tok-123"}, headers=H)
    assert r.status_code == 200 and "httponly" in r.headers["set-cookie"].lower() and "samesite=strict" in r.headers["set-cookie"].lower()
    assert api.get("/api/repos").status_code == 200 and api.get("/api/config").json()["signed_in"]    # the cookie is kept
    shutil.copytree(FIX / "py_app", tmp_path / "py_app")
    assert api.post("/api/repos", json={"path": str(tmp_path / "py_app")}, headers=H).status_code == 403
    monkeypatch.setenv("DEVMEM_ALLOW_PATHS", "1")
    assert api.post("/api/repos", json={"path": str(tmp_path / "py_app")}, headers=H).status_code == 200
    monkeypatch.setenv("DEVMEM_ALLOWED_HOSTS", "devmem.example.com")
    assert api.get("/api/repos", headers={"Host": "other.example.com"}).status_code == 403
    assert api.get("/api/repos", headers={"Host": "devmem.example.com"}).status_code == 200
    api.post("/api/logout", headers=H)
    assert api.get("/api/repos", headers={"Host": "devmem.example.com"}).status_code == 401


def test_local_mode_still_rejects_foreign_hosts(api):
    assert api.get("/api/repos", headers={"Host": "evil.example.com"}).status_code == 403


# ---------- AI settings ----------
def test_ai_settings_are_saved_without_exposing_the_key(api, tmp_path, monkeypatch):
    s = api.get("/api/settings/ai").json()
    assert not s["configured"] and {p["id"] for p in s["providers"]} == {"anthropic", "openai", "gemini", "vertex"}
    assert api.post("/api/settings/ai/test", headers=H).json()["ok"] is False
    assert api.put("/api/settings/ai", json={"provider": "nope"}, headers=H).status_code == 400
    assert api.put("/api/settings/ai", json={"provider": "openai", "base_url": "ftp://x"}, headers=H).status_code == 400
    s = api.put("/api/settings/ai", json={"provider": "anthropic", "api_key": "sk-ant-secret-1234"}, headers=H).json()
    assert s["configured"] and s["has_key"] and s["key_hint"] == "…1234" and s["model"] == "claude-haiku-5-5"
    assert "sk-ant-secret" not in json.dumps(s)
    f = tmp_path / "home/settings.json"
    assert json.loads(f.read_text())["ai"]["api_key"] == "sk-ant-secret-1234" and (f.stat().st_mode & 0o077) == 0
    s = api.put("/api/settings/ai", json={"provider": "anthropic", "model": "claude-sonnet-5-5"}, headers=H).json()   # key kept
    assert s["has_key"] and s["model"] == "claude-sonnet-5-5"
    assert isinstance(chat.provider_from_env(), chat.HttpProvider) and chat.llm_status()["configured"]
    monkeypatch.setenv("DEVMEM_AI_MODEL", "pinned-model")                                # the environment wins and is reported
    s = api.get("/api/settings/ai").json()
    assert s["model"] == "pinned-model" and "model" in s["from_environment"]
    s = api.put("/api/settings/ai", json={"provider": "", "clear_key": True}, headers=H).json()
    assert not s["configured"] and not s["has_key"] and chat.provider_from_env() is None


def test_provider_keys_in_the_environment_are_picked_up(api, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    cfg = settings.effective()
    assert cfg["provider"] == "openai" and cfg["api_key"] == "sk-env" and settings.configured(cfg)
    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.setenv("DEVMEM_VERTEX_PROJECT", "proj")                                  # the older variables keep working
    cfg = settings.effective()
    assert cfg["provider"] == "vertex" and cfg["project"] == "proj" and settings.configured(cfg)


def test_http_provider_wire_formats(monkeypatch):
    sent = []

    def fake_post(self, path, body, headers):
        sent.append((self.base + path, body, headers))
        return {"content": [{"type": "text", "text": "hi"}], "choices": [{"message": {"content": "hi"}}],
                "candidates": [{"content": {"parts": [{"text": "hi"}]}}], "data": [{"index": 1, "embedding": [2.0]}, {"index": 0, "embedding": [1.0]}]}
    monkeypatch.setattr(chat.HttpProvider, "_post", fake_post)
    assert chat.HttpProvider("anthropic", "k", "claude-haiku-5-5").generate("q") == "hi"
    url, body, headers = sent[-1]
    assert url == "https://api.anthropic.com/v1/messages" and headers["x-api-key"] == "k" and body["messages"][0]["content"] == "q" and body["max_tokens"]
    p = chat.HttpProvider("openai", "", "m", "http://localhost:11434/v1/", "e")
    assert p.generate("q") == "hi" and sent[-1][0] == "http://localhost:11434/v1/chat/completions" and "Authorization" not in sent[-1][2]
    assert p.embed(["a", "b"]) == [[1.0], [2.0]]                                         # returned in input order
    assert chat.HttpProvider("gemini", "k", "gemini-2.5-flash").generate("q") == "hi" and sent[-1][0].endswith("/models/gemini-2.5-flash:generateContent")


# ---------- descriptions ----------
class FakeLLM:
    name = "fake"

    def __init__(self, fail=False):
        self.fail, self.prompts = fail, []

    def generate(self, prompt):
        self.prompts.append(prompt)
        if self.fail:
            raise RuntimeError("boom")
        n = prompt.count("\n### ")
        return "Sure:\n" + json.dumps([{"i": i, "text": f"Sends the nightly ledger digest {i}. Words: email, report"} for i in range(n)])


def test_low_confidence_definitions_are_rated_described_and_searched(tmp_path, monkeypatch, api):
    root = tmp_path / "r"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg/a.py").write_text('def run(x):\n    return x\n\n\ndef retry_with_backoff(fn):\n    return fn()\n\n\n'
                                   'def load(path):\n    """Read the settings file and return it as a dictionary."""\n    return {}\n\n\n'
                                   'def caller():\n    return run(1)\n')
    (root / "tests").mkdir()
    (root / "tests/conftest.py").write_text("def key():\n    return 1\n")
    indexer.index_repo(root, "r", accuracy="fast")
    ix = Index.load(root)
    rv = definitions.review(ix, "medium")
    by = {i["name"]: i for i in rv["items"]}
    assert by["run"]["level"] == "low" and by["run"]["callers"] == 1 and "no docstring" in by["run"]["reasons"]
    assert by["retry_with_backoff"]["level"] == "medium" and "load" not in by and "key" not in by     # documented / test support
    assert rv["items"][0]["name"] == "run" and rv["counts"] == {"low": 2, "medium": 1, "high": 1}
    assert [i["name"] for i in definitions.review(ix, "low")["items"]] == ["run", "caller"]

    assert not ix.find("ledger digest")
    out = definitions.describe_with_ai(ix, ["pkg/a.py#run", "nope"], FakeLLM())
    assert list(out["written"]) == ["pkg/a.py#run"] and not out["failed"]
    saved = json.loads((root / ".devmem/descriptions.json").read_text())["pkg/a.py#run"]
    assert saved["by"] == "ai" and saved["code"]
    assert [s["id"] for _, s in Index.load(root).find("ledger digest")] == ["pkg/a.py#run"]            # searchable like a docstring
    rv = definitions.review(Index.load(root), "described")
    assert [(i["name"], i["by"], i["level"]) for i in rv["items"]] == [("run", "ai", "high")]

    (root / "pkg/a.py").write_text((root / "pkg/a.py").read_text().replace("return x\n", "return x * 2\n", 1))   # code moved on
    indexer.index_repo(root, "r", accuracy="fast")
    it = definitions.review(Index.load(root), "low")["items"][0]
    assert it["name"] == "run" and it["stale"] and it["level"] == "low"

    bad = definitions.describe_with_ai(Index.load(root), ["pkg/a.py#caller"], FakeLLM(fail=True))
    assert bad["failed"] == ["pkg/a.py#caller"] and "boom" in bad["error"] and not bad["written"]

    # the same through the API, written by a person
    assert api.post("/api/repos", json={"path": str(root)}, headers=H).status_code == 200
    assert api.get("/api/repos/r/definitions?level=low").json()["llm"]["configured"] is False
    assert api.post("/api/repos/r/definitions/ai", json={"ids": ["pkg/a.py#caller"]}, headers=H).status_code == 409   # no model set up
    r = api.put("/api/repos/r/definitions", json={"id": "pkg/a.py#caller", "text": "  Doubles one through   run.  "}, headers=H)
    assert r.json()["saved"] == {"text": "Doubles one through run.", "by": "human", "code": r.json()["saved"]["code"]}
    assert api.get("/api/repos/r/search?q=doubles").json()[0]["id"] == "pkg/a.py#caller"
    assert api.put("/api/repos/r/definitions", json={"id": "missing", "text": "x"}, headers=H).status_code == 404
    api.put("/api/repos/r/definitions", json={"id": "pkg/a.py#caller", "text": ""}, headers=H)
    assert "pkg/a.py#caller" not in json.loads((root / ".devmem/descriptions.json").read_text())


# ---------- speed-ups keep the same answers ----------
def test_module_files_matches_the_scan_it_replaced():
    files = {f: {} for f in ["src/pkg/__init__.py", "src/pkg/mod.py", "pkg/mod.py", "other/pkg/mod/__init__.py", "mod.py", "pkg/mod.txt", "xpkg/mod.py"]}

    def scan(module):
        suf = (module.replace(".", "/") + ".py", module.replace(".", "/") + "/__init__.py")
        return [f for f in files if any(f == s or f.endswith("/" + s) for s in suf)]
    for m in ("pkg.mod", "pkg", "mod", "src.pkg.mod", "nope", "other.pkg.mod", ""):
        assert module_files(files, m) == (scan(m) if m else [])
    files["late/pkg/mod.py"] = {}                                                        # a changed file set is noticed
    assert module_files(files, "pkg.mod") == scan("pkg.mod")


def test_index_is_cached_for_the_mcp_server_until_it_changes(tmp_path):
    root = tmp_path / "py_app"
    shutil.copytree(FIX / "py_app", root)
    indexer.index_repo(root, "py_app", accuracy="fast")
    a = Index.cached(root)
    assert Index.cached(root) is a
    assert [s["id"] for s in a.resolve("retry")] == ["app/retry.py#retry"] and a.resolve("app/retry.py:retry")[0]["name"] == "retry"
    assert [s["name"] for s in a.outline("app/retry.py")] == ["retry"]
    (root / "app/extra.py").write_text("def extra():\n    return 1\n")
    indexer.index_repo(root, "py_app", accuracy="fast")
    b = Index.cached(root)
    assert b is not a and b.resolve("extra")


# ---------- AI analysis ----------
class AnalystLLM:
    name = "fake:analyst"

    def __init__(self):
        self.prompts = []

    def generate(self, prompt):
        self.prompts.append(prompt)
        if "short paragraph" in prompt:
            return "A small payments service: the app package charges cards through a retry helper."
        n = prompt.count("\n### ")
        return json.dumps([{"i": i, "text": f"Handles ledger reconciliation part {i}."} for i in range(n)])


def test_ai_analysis_writes_once_resumes_and_feeds_publish_and_chat(tmp_path, api, monkeypatch):
    from devmem_app import analysis
    from devmem_app.publisher import publish
    root = tmp_path / "conv_app"
    shutil.copytree(FIX / "conv_app", root)
    idx = indexer.index_repo(root, "conv_app", accuracy="fast")
    ix = Index.load(root)
    st = analysis.status(ix)
    assert st["describe"]["todo"] > 0 and st["modules"]["todo"] == st["modules"]["total"] == 1 and st["overview"]["todo"] == 1
    assert [m["name"] for m in st["modules"]["items"]] == ["app"] and st["last_run"] is None        # the tests folder is not summarised
    assert st["describe"]["requests"] >= 1 and st["describe"]["tokens"] > 0

    llm = AnalystLLM()
    job = analysis.run(ix, llm, list(analysis.STEPS), None, analysis.Job(list(analysis.STEPS)))
    assert job.state == "done" and job.failed == 0 and job.done == job.total
    assert job.written == {"describe": st["describe"]["todo"], "modules": 1, "overview": 1}
    assert "Handles ledger reconciliation" in llm.prompts[-1] and "Handles ledger reconciliation" in llm.prompts[-2]   # each step reads the one before
    after = analysis.status(Index.load(root))
    assert (after["describe"]["todo"], after["modules"]["todo"], after["overview"]["todo"]) == (0, 0, 0)
    assert after["last_run"]["model"] == "fake:analyst" and after["overview"]["by"] == "ai"

    n = len(llm.prompts)                                                    # nothing changed: a second run sends nothing
    again = analysis.run(Index.load(root), llm, list(analysis.STEPS), None, analysis.Job(list(analysis.STEPS)))
    assert again.state == "done" and len(llm.prompts) == n and again.total == 1 and sum(again.written.values()) == 0

    publish(root, idx, {})
    ov = (root / ".devmem/knowledge/overview.md").read_text()
    assert "## What this repo does\nA small payments service" in ov and "modules/app.md — Handles ledger reconciliation part 0." in ov
    assert "Handles ledger reconciliation part 0." in (root / ".devmem/knowledge/modules/app.md").read_text().splitlines()[2]
    hits = chat.retrieve("what does this repo do", {"conv_app": root})
    assert hits and hits[0]["kind"] == "overview" and "payments service" in hits[0]["snippet"]

    # a person's correction survives code changes; an AI text is redone when its code changes
    analysis.set_text(Index.load(root), "app", "  Service layer and  config. ")
    (root / "app/extra.py").write_text("def settle_ledger(batch):\n    return batch\n")
    indexer.index_repo(root, "conv_app", accuracy="fast")
    st = analysis.status(Index.load(root))
    assert st["modules"]["items"][0] == {"name": "app", "text": "Service layer and config.", "by": "human", "stale": True}
    assert st["modules"]["todo"] == 0 and st["overview"]["todo"] == 1 and st["describe"]["todo"] == 1     # the new, undocumented function

    class Down:
        name = "down"

        def generate(self, prompt):
            raise RuntimeError("cannot reach the model")
    (root / "app/more.py").write_text("def run(x):\n    return x\n")
    indexer.index_repo(root, "conv_app", accuracy="fast")
    bad = analysis.run(Index.load(root), Down(), ["describe", "overview"], None, analysis.Job(["describe", "overview"]))
    assert bad.state == "failed" and "cannot reach" in bad.error
    assert analysis.load(root)["overview"]["text"].startswith("A small payments")                     # earlier results are kept

    # API: needs a model, reports progress, accepts corrections
    assert api.post("/api/repos", json={"path": str(root)}, headers=H).status_code == 200
    assert api.post("/api/repos/conv_app/analysis", json={}, headers=H).status_code == 409
    assert api.post("/api/repos/conv_app/analysis", json={"steps": ["nope"]}, headers=H).status_code == 400
    monkeypatch.setattr(chat, "provider_from_env", lambda: AnalystLLM())
    r = api.post("/api/repos/conv_app/analysis", json={"steps": ["describe"], "limit": 50}, headers=H)
    assert r.status_code == 200 and r.json()["steps"] == ["describe"]
    import time
    for _ in range(100):
        s = api.get("/api/repos/conv_app/analysis").json()
        if s["job"]["state"] != "running":
            break
        time.sleep(0.05)
    assert s["job"]["state"] == "done" and s["job"]["written"]["describe"] == 2 and s["describe"]["todo"] == 0
    assert api.put("/api/repos/conv_app/analysis", json={"text": "Payments service."}, headers=H).status_code == 200
    assert api.get("/api/repos/conv_app/analysis").json()["overview"] == {**s["overview"], "text": "Payments service.", "by": "human", "stale": False, "todo": 0}
    assert api.put("/api/repos/conv_app/analysis", json={"module": "ghost", "text": "x"}, headers=H).status_code == 404
