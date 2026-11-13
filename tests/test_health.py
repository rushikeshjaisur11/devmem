"""SonarQube import and the combined, blast-radius-ranked Code health view."""
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from devmem import cli as agent_cli
from devmem.index import Index
from devmem_app import health, indexer, sonar
from devmem_app.server import create_app

FIX = Path(__file__).parent.parent / "fixtures"
H = {"X-Devmem": "1"}
P = "shop"


def _issue(key, rule, sev, typ, path, line, msg):
    return {"key": key, "rule": rule, "severity": sev, "type": typ, "component": f"{P}:{path}", "line": line, "message": msg, "effort": "5min"}


class FakeSonar:
    """Answers the Web API calls sonar.py makes. `old=True` behaves like a server that predates the newer parameter names."""
    def __init__(self, old=False, token="good"):
        self.old, self.token, self.calls = old, token, []
        self.issues = [_issue("a", "python:S1481", "MINOR", "CODE_SMELL", "app/client.py", 3, "unused variable"),
                       _issue("b", "python:S5754", "CRITICAL", "BUG", "app/retry.py", 6, "bare except hides errors"),
                       _issue("c", "python:S2077", "BLOCKER", "VULNERABILITY", "app/service.py", 5, "sql built from input")]

    def get(self, client, path, **params):
        self.calls.append((path, params))
        if path == "/api/system/status":
            return {"status": "UP", "version": "9.9.1" if self.old else "2025.1"}
        if client.token != self.token:
            raise sonar.SonarError("SonarQube refused the token (401).")
        if path == "/api/authentication/validate":
            return {"valid": True}
        if path == "/api/components/search":
            return {"components": [{"key": P, "name": "Shop"}]}
        if path == "/api/issues/search":
            if self.old and "components" in params:
                e = sonar.SonarError("unknown parameter"); e.code, e.detail = 400, ""; raise e
            page = params["p"]
            chunk = self.issues[(page - 1) * 2: page * 2] if len(self.issues) <= 3 else self.issues
            return {"paging": {"total": len(self.issues)}, "issues": chunk,
                    "components": [{"key": i["component"], "path": "backend/" + i["component"].split(":", 1)[1]} for i in chunk]}
        if path == "/api/hotspots/search":
            return {"paging": {"total": 1}, "hotspots": [{"key": "h", "component": f"{P}:app/client.py", "line": 2, "message": "debug left on",
                                                          "vulnerabilityProbability": "HIGH", "ruleKey": "python:S4507"}]}
        if path == "/api/measures/component":
            if "sqale_index" in params["metricKeys"]:
                e = sonar.SonarError("not found"); e.code, e.detail = 404, "The following metric keys are not found: sqale_index, code_smells"; raise e
            return {"component": {"measures": [{"metric": "coverage", "value": "61.4"}, {"metric": "alert_status", "value": "ERROR"}]}}
        if path == "/api/qualitygates/project_status":
            return {"projectStatus": {"status": "ERROR", "conditions": [{"status": "ERROR", "metricKey": "new_coverage", "actualValue": "61", "errorThreshold": "80"},
                                                                        {"status": "OK", "metricKey": "x"}]}}
        if path == "/api/measures/component_tree":
            return {"paging": {"total": 1}, "components": [{"path": "backend/app/retry.py", "measures": [{"metric": "coverage", "value": "40.0"}]}]}
        raise AssertionError(path)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVMEM_HOME", str(tmp_path / "home"))
    for v in ("DEVMEM_TOKEN", "DEVMEM_SONAR_URL", "DEVMEM_SONAR_TOKEN", "SONAR_HOST_URL", "SONAR_TOKEN"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(health.hyg, "HISTORY_DIR", tmp_path / "hist")
    root = tmp_path / "py_app"
    shutil.copytree(FIX / "py_app", root)
    indexer.index_repo(root, "py_app", accuracy="fast")
    fake = FakeSonar()
    monkeypatch.setattr(sonar.Client, "get", lambda self, path, **p: fake.get(self, path, **{k: v for k, v in p.items() if v not in (None, "")}))
    monkeypatch.setattr(sonar.dt, "datetime", sonar.dt.datetime)
    return root, fake


def test_fetch_normalises_issues_pages_and_survives_version_differences(env):
    root, fake = env
    files = set(Index.load(root).data["files"])
    d = sonar.fetch("http://sq", "good", P, files=files)
    assert [(i["key"], i["severity"], i["category"], i["path"], i["line"]) for i in d["issues"]] == [
        ("a", "low", "maintainability", "app/client.py", 3), ("b", "high", "bug", "app/retry.py", 6), ("c", "high", "security", "app/service.py", 5)]
    assert [c[1]["p"] for c in fake.calls if c[0] == "/api/issues/search"] == [1, 2]            # paged until the total is read
    assert d["hotspots"][0]["severity"] == "high" and d["hotspots"][0]["hotspot"] and d["hotspots"][0]["path"] == "app/client.py"
    assert d["measures"] == {"coverage": "61.4", "alert_status": "ERROR"}                         # retried without the metrics the server lacks
    assert d["gate"] == {"status": "ERROR", "failed": [{"metric": "new_coverage", "actual": "61", "threshold": "80"}]}
    assert d["file_coverage"] == {"app/retry.py": 40.0} and not d["truncated"] and d["version"] == "2025.1"
    fake.old, fake.calls = True, []
    assert len(sonar.fetch("http://sq", "good", P, files=files)["issues"]) == 3                   # falls back to componentKeys
    assert {"componentKeys" in c[1] for c in fake.calls if c[0] == "/api/issues/search"} == {True, False}
    with pytest.raises(sonar.SonarError, match="refused the token"):
        sonar.fetch("http://sq", "bad", P)


def test_repo_paths_are_matched_whichever_side_has_the_extra_folder():
    files = {"app/a.py", "lib/b.py"}
    assert sonar._repo_path("svc/backend/app/a.py", files) == "app/a.py"      # Sonar scanned a parent folder
    assert sonar._repo_path("a.py", {"src/app/a.py"}) == "src/app/a.py"       # Sonar scanned a sub-folder
    assert sonar._repo_path("a.py", {"x/a.py", "y/a.py"}) == "a.py"           # ambiguous: left alone
    assert sonar._repo_path("gone.py", files) == "gone.py"


def test_project_key_is_detected_from_the_repo(tmp_path):
    (tmp_path / "sonar-project.properties").write_text("# c\nsonar.projectKey = acme_orders\nsonar.sources=.\n")
    assert sonar.detect_key(tmp_path) == "acme_orders" and sonar.detect_key(tmp_path / "none") == ""


def test_health_ranks_by_severity_callers_and_tests(env):
    root, fake = env
    ix = Index.load(root)
    assert health.build(ix, "py_app") == {"available": False, "sonar": None, "local": None}
    sonar.save_connection("http://sq/", "good")
    sonar.set_project("py_app", P)
    d = sonar.sync("py_app", root, set(ix.data["files"]))
    assert d["matched"] == 3 and json.loads((root / ".devmem/sonar.json").read_text())["project"] == P
    assert "good" not in (root / ".devmem/sonar.json").read_text()                              # the token never reaches the repo
    (root / ".devmem/hygiene.json").write_text(json.dumps({"ts": "2026-01-01T00:00:00+00:00", "tools": {"ruff": "ok", "bandit": "ok", "radon": "ok"},
        "counts": {"ruff": 2, "bandit": 1}, "clones": [], "issues": [
            {"tool": "ruff", "path": "app/retry.py", "line": 6, "code": "E501", "msg": "line too long"},
            {"tool": "ruff", "path": "app/service.py", "line": 5, "code": "F821", "msg": "undefined name"},
            {"tool": "bandit", "path": "app/client.py", "line": 3, "code": "B105", "msg": "hardcoded password", "severity": "HIGH"}]}))
    h = health.build(ix, "py_app")
    assert h["summary"] == {"total": 7, "high": 4, "medium": 1, "low": 2, "risky": h["summary"]["risky"]}
    top = h["findings"][0]
    # the high-severity bug inside retry(), which two functions call, outranks high findings in code nothing calls
    assert (top["code"], top["symbol"], top["callers"], top["source"]) == ("python:S5754", "retry", 2, "sonar")
    assert top["url"] == "http://sq/project/issues?id=shop&open=b" and top["score"] > h["findings"][1]["score"]
    by_code = {f["code"]: f for f in h["findings"]}
    assert by_code["F821"]["category"] == "bug" and by_code["F821"]["severity"] == "medium" and by_code["E501"]["severity"] == "low"
    assert by_code["B105"]["severity"] == "high" and by_code["B105"]["category"] == "security"
    assert "security hotspot" in by_code["python:S4507"]["msg"] and "security_hotspots?id=shop&hotspots=h" in by_code["python:S4507"]["url"]
    assert [f["severity"] for f in h["findings"]][-2:] == ["low", "low"]
    assert h["files"][0]["path"] == "app/retry.py" and h["files"][0]["coverage"] == 40.0 and h["files"][0]["used_by"] == 2
    assert {c["id"] for c in h["categories"]} == {"bug", "security", "maintainability", "style"}
    assert h["sonar"]["gate"]["status"] == "ERROR" and h["sonar"]["measures"]["coverage"] == "61.4" and h["local"]["tools"]["ruff"] == "ok"


def test_agents_see_sonar_findings_for_a_file(env, capsys, monkeypatch):
    root, fake = env
    sonar.save_connection("http://sq", "good"); sonar.set_project("py_app", P)
    sonar.sync("py_app", root, set(Index.load(root).data["files"]))
    capsys.readouterr()
    assert agent_cli.run(["hygiene", "app/client.py"], root=root) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == ["app/client.py:2  sonar python:S4507 [high] debug left on", "app/client.py:3  sonar python:S1481 [low] unused variable"]
    assert agent_cli.run(["hygiene", "app/__init__.py"], root=root) == 0 and "no known hygiene issues" in capsys.readouterr().out


def test_sonar_api_connection_mapping_and_sync(env, tmp_path):
    root, fake = env
    api = TestClient(create_app())
    assert api.post("/api/repos", json={"path": str(root)}, headers=H).status_code == 200
    s = api.get("/api/integrations/sonar").json()
    assert not s["configured"] and s["projects"] == [{"repo": "py_app", "key": "", "branch": "", "detected": "", "synced": None, "issues": None}]
    assert api.post("/api/repos/py_app/sonar/sync", headers=H).status_code == 422                # nothing connected yet
    assert api.put("/api/integrations/sonar", json={"url": "ftp://x"}, headers=H).status_code == 400
    s = api.put("/api/integrations/sonar", json={"url": "http://sq/", "token": "bad-token-123456"}, headers=H).json()
    assert s["configured"] and s["url"] == "http://sq" and s["has_token"] and s["token_hint"] == "…3456" and "bad-token" not in json.dumps(s)
    assert api.post("/api/integrations/sonar/test", headers=H).json()["ok"] is False
    fake.token = "bad-token-123456"
    assert api.post("/api/integrations/sonar/test", headers=H).json() == {"ok": True, "version": "2025.1", "status": "UP", "token_valid": True}
    assert api.get("/api/integrations/sonar/projects?q=sh").json() == [{"key": P, "name": "Shop"}]
    assert api.post("/api/repos/py_app/sonar/sync", headers=H).status_code == 422                # connected, but no project chosen
    assert api.put("/api/repos/py_app/sonar", json={"key": "bad key!"}, headers=H).status_code == 400
    assert api.put("/api/repos/py_app/sonar", json={"key": P, "branch": "main"}, headers=H).status_code == 200
    r = api.post("/api/repos/py_app/sonar/sync", headers=H).json()
    assert (r["issues"], r["hotspots"], r["matched"], r["truncated"], r["gate"]["status"]) == (3, 1, 3, False, "ERROR")
    assert [c[1].get("branch") for c in fake.calls if c[0] == "/api/issues/search"][-1] == "main"
    h = api.get("/api/repos/py_app/health").json()
    assert h["available"] and h["summary"]["total"] == 4 and h["sonar"]["project"] == P and h["local"] is None
    assert h["sonar_setup"]["configured"] and h["sonar_setup"]["project"]["key"] == P and h["findings"][0]["symbol"] == "retry"
    assert (tmp_path / "home/settings.json").stat().st_mode & 0o077 == 0
    s = api.put("/api/integrations/sonar", json={"url": "", "clear_token": True}, headers=H).json()
    assert not s["configured"] and not s["has_token"]
