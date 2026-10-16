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
    assert api.get("/api/repos/py_app/file", params={"path": "../../etc/passwd"}).status_code == 404
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
    assert api.get("/api/repos/nope/tree").status_code == 404


def test_explorer(api):
    t = api.get("/api/repos/py_app/tree").json()
    app_dir = next(c for c in t["children"] if c["name"] == "app")
    assert {"retry.py", "client.py", "service.py"} <= {c["name"] for c in app_dir["children"]}
    f = api.get("/api/repos/py_app/file", params={"path": "app/retry.py"}).json()
    assert "def retry" in f["text"] and f["symbols"][0]["name"] == "retry"
    hits = api.get("/api/repos/py_app/search", params={"q": "retry"}).json()
    assert hits[0]["id"] == "app/retry.py#retry"
    s = api.get("/api/repos/py_app/symbol", params={"id": "app/retry.py#retry"}).json()
    assert {c["id"] for c in s["callers"] if c["type"] == "calls"} == {"app/client.py#ApiClient.get", "app/service.py#load_user"}
    assert api.get("/api/repos/py_app/symbol", params={"id": "zzz"}).status_code == 404


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
    assert m["symbols"] > 0 and m["pr_metrics"] is None and m["hygiene_trend"]


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


def test_share_code_between_repos(api):
    p = api.get("/api/share/preview", params={"repo": "conv_app", "file": "app/util.py", "symbol": "app/util.py#slug"}).json()
    assert p["text"].startswith("def slug")
    body = {"from_repo": "conv_app", "file": "app/util.py", "symbol": "app/util.py#slug", "to_repo": "py_app", "dest": "app/slug.py"}
    r = api.post("/api/share", json=body, headers=H).json()
    assert r["staged"] is False and r["origin"].startswith("conv_app:app/util.py#slug@")
    text = (api.repos["py_app"] / "app/slug.py").read_text()
    assert text.startswith("# devmem: copied from conv_app:app/util.py#slug@") and "def slug" in text
    assert api.post("/api/share", json=body, headers=H).status_code == 409                      # no overwrite
    assert api.post("/api/share", json={**body, "append": True}, headers=H).status_code == 200
    assert (api.repos["py_app"] / "app/slug.py").read_text().count("def slug") == 2
    bad = {**body, "dest": "../escape.py"}
    assert api.post("/api/share", json=bad, headers=H).status_code == 400
