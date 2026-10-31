"""Step 12 backend: the local API."""
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from devmem_app.server import create_app

FIX = Path(__file__).parent.parent / "fixtures"
H = {"X-Devmem": "1"}


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVMEM_HOME", str(tmp_path / "home"))
    c = TestClient(create_app())
    repos = {}
    for fx in ("py_app", "conv_app", "lineage_app", "consumer_app", "messy"):
        d = tmp_path / fx
        shutil.copytree(FIX / fx, d)
        r = c.post("/api/repos", json={"path": str(d)}, headers=H)
        assert r.status_code == 200, r.text
        repos[fx] = d
    c.repos = repos
    return c


def test_security_guards(api):
    assert api.post("/api/repos", json={"path": "/tmp"}).status_code == 403                       # no header
    assert api.get("/api/repos", headers={"Host": "evil.example.com"}).status_code == 403          # DNS rebinding
    assert api.post("/api/repos", json={"path": "/definitely/not/here"}, headers=H).status_code == 400


def test_repos_listing_and_actions(api):
    rs = {r["name"]: r for r in api.get("/api/repos").json()}
    assert set(rs) == {"py_app", "conv_app", "lineage_app", "consumer_app", "messy"}
    assert rs["py_app"]["indexed"] and rs["py_app"]["symbols"] >= 6 and rs["py_app"]["langs"]["python"] >= 5
    assert rs["py_app"]["knowledge_problems"] == ["not published"]
    r = api.post("/api/repos/py_app/publish", json={}, headers=H)
    assert r.status_code == 200 and ".devmem/knowledge/map.md" in r.json()["written"]
    again = {r["name"]: r for r in api.get("/api/repos").json()}["py_app"]
    assert again["knowledge_problems"] == []
    api.post("/api/repos/py_app/index", headers=H)    # picks up the files publish just wrote (AGENTS.md ...)
    assert api.post("/api/repos/py_app/index", headers=H).json()["stats"]["parsed"] == 0
    assert api.delete("/api/repos/consumer_app", headers=H).status_code == 200
    assert "consumer_app" not in {r["name"] for r in api.get("/api/repos").json()}
    assert api.get("/api/repos/nope/search", params={"q": "x"}).status_code == 404


def test_hygiene_returns_publish_errors_as_http_errors(api, monkeypatch):
    import devmem_app.server as server

    monkeypatch.setattr(server.hyg, "run_hygiene", lambda *args: {"counts": {}, "tools": {}})

    def fail_publish(*args):
        raise RuntimeError("knowledge budget exceeded")

    monkeypatch.setattr(server, "publish", fail_publish)
    response = api.post("/api/repos/py_app/hygiene", headers=H)

    assert response.status_code == 422
    assert response.json()["detail"] == "knowledge budget exceeded"


def test_search(api):
    hits = api.get("/api/repos/py_app/search", params={"q": "retry"}).json()
    assert hits[0]["id"] == "app/retry.py#retry"


def test_impact_graph(api):
    r = api.get("/api/repos/py_app/impact", params={"target": "retry"}).json()
    assert r["kind"] == "symbol" and len(r["hop1"]) == 2 and r["tests"]
    g = api.get("/api/repos/py_app/graph", params={"focus": "app/retry.py#retry", "depth": 2}).json()
    ids = {n["data"]["id"] for n in g["nodes"]}
    assert "app/retry.py#retry" in ids and "app/service.py#main" in ids
    assert any(e["data"]["target"] == "app/retry.py#retry" for e in g["edges"])


def test_lineage_and_overview_graph(api):
    from devmem_app.lineage import import_schema_csv
    import_schema_csv(api.repos["lineage_app"], api.repos["lineage_app"] / "schema.csv")
    for n in ("lineage_app", "consumer_app"):
        api.post(f"/api/repos/{n}/publish", json={}, headers=H)
    lg = api.get("/api/lineage").json()
    t = next(t for t in lg["tables"] if t["name"] == "mart.sales_report")
    assert {w["repo"] for w in t["writers"]} == {"lineage_app"} and {r["repo"] for r in t["readers"]} == {"consumer_app"}
    cols = api.get("/api/lineage", params={"table": "mart.sales_report"}).json()
    assert any(e["dst"] == "mart.sales_report.dbl" for e in cols["column_edges"])
    imp = api.get("/api/impact-column", params={"column": "mart.sales_daily.cnt"})
    assert imp.status_code == 200
    assert any(x["job"].startswith("consumer_app:") for d in imp.json()["derived"] for x in d["readers"])
    ov = api.get("/api/graph/overview").json()
    kinds = {n["data"]["kind"] for n in ov["nodes"]}
    assert {"repo", "module", "table"} <= kinds
    assert any(e["data"]["kind"] == "writes" for e in ov["edges"]) and any(e["data"]["kind"] == "reads" for e in ov["edges"])


def test_hygiene_and_metrics(api):
    assert api.get("/api/repos/messy/hygiene").json() == {"available": False}
    assert api.post("/api/repos/messy/hygiene", headers=H).status_code == 200
    h = api.get("/api/repos/messy/hygiene").json()
    assert h["available"] and h["counts"]["vulture"] >= 3 and h["clones"]
    m = api.get("/api/repos/messy/metrics").json()
    assert m["symbols"] > 0 and m["pr_metrics"]["commits_scanned"] == 0 and m["hygiene_trend"]


def test_memory_flow(api):
    r = api.post("/api/repos/conv_app/lessons", json={"title": "Pool size", "problem": "pool exhausted", "fix": "raise to 20", "tags": ["db"]}, headers=H)
    lid = r.json()["id"]
    assert api.put(f"/api/repos/conv_app/lessons/{lid}", json={"title": "Pool size", "problem": "pool exhausted under load",
                                                               "fix": "raise to 20", "tags": ["db", "perf"]}, headers=H).json()["ok"]
    assert api.post(f"/api/memory/lessons/{lid}/share", json={"repos": ["py_app"]}, headers=H).status_code == 404   # not in hub yet
    assert api.post("/api/memory/sync", headers=H).json()["new_lessons"] == 1
    assert api.post(f"/api/memory/lessons/{lid}/share", json={"repos": ["py_app"]}, headers=H).json()["repos"] == ["conv_app", "py_app"]
    api.post("/api/memory/sync", headers=H)
    mem = api.get("/api/memory").json()
    assert [l["id"] for l in mem["repos"]["py_app"]["lessons"]] == [lid]
    assert mem["repos"]["conv_app"]["lessons"][0]["tags"] == ["db", "perf"]
    assert api.post(f"/api/memory/lessons/{lid}/to-rule", json={"applies_to": "app/*.py"}, headers=H).status_code == 200
    api.post("/api/memory/sync", headers=H)
    assert api.get("/api/memory").json()["hub_rules"][0]["applies_to"] == "app/*.py"
    assert api.post("/api/memory/proposals/nope/reject", headers=H).status_code == 404


def test_chat_retrieval_and_llm(api, monkeypatch):
    from devmem_app import chat
    r = api.post("/api/chat", json={"question": "slug"}, headers=H).json()
    assert r["mode"] == "retrieval" and r["hits"][0]["pointer"].startswith("conv_app:app/util.py")
    assert r["hits"][0]["snippet"].startswith("def slug")
    r = api.post("/api/chat", json={"question": "who reads mart.sales_report table", "repos": ["consumer_app", "lineage_app"]}, headers=H).json()
    assert any(h["kind"] == "table" for h in r["hits"]) and {h["repo"] for h in r["hits"]} >= {"consumer_app", "lineage_app"}
    assert api.post("/api/chat", json={"question": "x", "repos": ["nope"]}, headers=H).status_code == 404
    assert api.post("/api/chat", json={"question": "x", "llm": True}, headers=H).status_code == 409   # not configured
    assert api.get("/api/chat/status").json()["configured"] is False
    assert api.post("/api/chat", json={"question": "zzzzqqq"}, headers=H).json()["hits"] == []

    class Fake:
        name = "fake"
        def generate(self, prompt):
            assert "Context:" in prompt and "slug" in prompt
            return "It is a slug helper [1], also see [99]."
        def embed(self, texts):
            return [[float("slug" in t), 1.0] for t in texts]
    res = chat.ask("slug", dict(api.repos), provider=Fake())
    assert res["mode"] == "llm" and res["cited"] == [1] and res["dropped_citations"] == [99] and "[99]" not in res["answer"]
    class Boom(Fake):
        def generate(self, prompt): raise RuntimeError("quota")
    res = chat.ask("slug", {"conv_app": api.repos["conv_app"]}, provider=Boom())
    assert res["mode"] == "retrieval" and "quota" in res["llm_error"]


def test_devin_upload_endpoint(api):
    body = json.dumps([{"id": "d1", "created_at": "2026-02-01T00:00:00Z", "acus": 4}])
    assert api.post("/api/metrics/devin", content=body, headers=H).json()["new"] == 1
    assert api.post("/api/metrics/devin", content="42", headers=H).status_code == 422


def test_switch_branch(api):
    import subprocess
    d = api.repos["py_app"]
    g = lambda *a: subprocess.run(["git", "-C", str(d), *a], check=True, capture_output=True, text=True)
    g("init", "-q", "-b", "main"); g("add", "-A"); g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    g("checkout", "-qb", "feature")
    (d / "app/extra.py").write_text("def only_on_feature():\n    return 1\n")
    g("add", "-A"); g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "feat")
    b = api.get("/api/repos/py_app/branches").json()
    assert b["current"] == "feature" and set(b["branches"]) == {"main", "feature"} and b["dirty"] == []
    assert api.post("/api/repos/py_app/branch", json={"branch": "nope"}, headers=H).status_code == 400
    r = api.post("/api/repos/py_app/branch", json={"branch": "main"}, headers=H)
    assert r.status_code == 200 and r.json()["branch"] == "main"
    assert not (d / "app/extra.py").exists()
    (d / "app/client.py").write_text((d / "app/client.py").read_text() + "\n# edit\n")
    assert api.post("/api/repos/py_app/branch", json={"branch": "feature"}, headers=H).status_code == 409


def test_add_repo_by_git_url(api, tmp_path):
    import subprocess
    src = tmp_path / "origin"
    shutil.copytree(FIX / "py_app", src)
    g = lambda *a: subprocess.run(["git", "-C", str(src), *a], check=True, capture_output=True)
    g("init", "-q", "-b", "main"); g("add", "-A"); g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i")
    g("branch", "dev")
    for bad in ("-oProxyCommand=x", "ext::sh -c id", "/etc/passwd", "file:///x"):
        assert api.post("/api/repos", json={"url": bad}, headers=H).status_code == 400
    assert api.post("/api/repos", json={}, headers=H).status_code == 400
    # file:// is deliberately not accepted, so exercise the clone path through the https/ssh validator with a bad host
    r = api.post("/api/repos", json={"url": "https://127.0.0.1:9/none.git", "name": "ghost"}, headers=H)
    assert r.status_code == 422 and "git failed" in r.json()["detail"]


def test_rules_can_be_added_and_deleted_per_repo_and_globally(api):
    r = api.post("/api/rules", json={"text": "Never call the payments API from tests", "applies_to": "tests/**", "repo": "py_app"}, headers=H)
    assert r.status_code == 200
    rid = r.json()["id"]
    rules = api.get("/api/memory").json()["repos"]["py_app"]["rules"]
    assert any(x["id"] == rid and x["applies_to"] == "tests/**" for x in rules)
    assert api.delete(f"/api/rules/{rid}", params={"repo": "py_app"}, headers=H).json()["ok"]
    assert api.post("/api/rules", json={"text": "Use UTC timestamps everywhere"}, headers=H).status_code == 200
    assert [x["text"] for x in api.get("/api/memory").json()["hub_rules"]] == ["Use UTC timestamps everywhere"]
    assert api.post("/api/rules", json={"text": "  "}, headers=H).status_code == 422


def test_connect_preview_activity_gaps_and_alerts(api):
    st = api.get("/api/repos/py_app/connect").json()
    assert "claude" in st["agents"] and not any(st["connected"].values())
    res = api.post("/api/repos/py_app/connect", json={"agents": ["claude", "cursor"]}, headers=H).json()["results"]
    assert {r["file"] for r in res} == {".mcp.json", ".cursor/mcp.json", "CLAUDE.md"}
    py = next(r for r in api.get("/api/repos").json() if r["name"] == "py_app")
    assert {"claude", "cursor"} <= set(py["connected"])
    assert api.post("/api/repos/py_app/connect", json={"agents": ["nope"]}, headers=H).status_code == 422
    v = api.get("/api/repos/py_app/agent-view", params={"cmd": "show", "arg": "retry"}).json()
    assert "def retry" in v["output"] and v["tokens"] > 0
    assert api.get("/api/repos/py_app/agent-view", params={"cmd": "diffcheck"}).status_code == 422
    assert not (api.repos["py_app"] / ".devmem/usage.jsonl").exists()             # previews are not agent usage
    assert api.get("/api/repos/py_app/activity").json()["sessions"] == []
    assert isinstance(api.get("/api/repos/py_app/gaps").json(), list)
    al = api.get("/api/alerts").json()
    assert any(a["repo"] == "conv_app" and "no agent connected" in a["text"] for a in al)
    assert not any(a["repo"] == "py_app" and "no agent connected" in a["text"] for a in al)
