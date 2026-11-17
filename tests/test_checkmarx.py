"""Checkmarx One (API) and SARIF imports into Code health, `devmem hygiene` and context.

The Checkmarx side runs against a small fake server over real HTTP, so the login (API key -> access token at the realm the
key names), paging, retry and the result mapping are all exercised as they would be against the service."""
import base64
import json
import shutil
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from devmem import cli as agent_cli
from devmem.index import Index
from devmem_app import checkmarx as cx
from devmem_app import health, indexer, sarif
from devmem_app.server import create_app

FIX = Path(__file__).parent.parent / "fixtures"
H = {"X-Devmem": "1"}
PID, SCAN = "0b9b3d4e-1111-4222-8333-444455556666", "9c0c1d2e-aaaa-4bbb-8ccc-ddddeeeeffff"


def _jwt(claims: dict) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'HS512'})}.{enc(claims)}.sig"


def _results():
    return [
        {"type": "sast", "id": "r1", "similarityId": "s1", "status": "NEW", "state": "TO_VERIFY", "severity": "HIGH",
         "description": "<p>The application builds a SQL query from user input.</p>",
         "data": {"queryName": "SQL_Injection", "group": "Python_High_Risk",
                  "nodes": [{"fileName": "/app/client.py", "line": 2, "method": "get"}, {"fileName": "/app/service.py", "line": 5, "method": "load"}]},
         "vulnerabilityDetails": {"cweId": 89}},
        {"type": "sast", "id": "r2", "status": "RECURRENT", "state": "NOT_EXPLOITABLE", "severity": "MEDIUM",
         "data": {"queryName": "Reflected_XSS", "nodes": [{"fileName": "/app/client.py", "line": 3}]}},
        {"type": "sca", "id": "CVE-2023-32681", "status": "NEW", "state": "TO_VERIFY", "severity": "MEDIUM",
         "data": {"packageIdentifier": "Python-requests-2.25.0", "recommendedVersion": "2.31.0"},
         "vulnerabilityDetails": {"cveName": "CVE-2023-32681", "cvssScore": 6.1, "cweId": "CWE-200"}},
        {"type": "kics", "id": "k1", "status": "NEW", "state": "TO_VERIFY", "severity": "LOW",
         "data": {"queryName": "Healthcheck Instruction Missing", "filename": "/Dockerfile", "line": 1, "expectedValue": "HEALTHCHECK set", "actualValue": "missing"}},
        {"type": "sast", "id": "r3", "status": "FIXED", "state": "TO_VERIFY", "severity": "HIGH", "data": {"queryName": "Old", "nodes": [{"fileName": "/app/client.py", "line": 1}]}},
    ]


class FakeCx:
    def __init__(self):
        self.calls, self.logins, self.fail_once = [], [], set()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.key = _jwt({"aud": f"{self.url}/auth/realms/acme", "azp": "ast-app", "typ": "Offline"})
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def _handler(fake):
        class Hd(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body):
                raw = json.dumps(body).encode()
                self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(raw))); self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                form = dict(urllib.parse.parse_qsl(self.rfile.read(int(self.headers["Content-Length"])).decode()))
                fake.logins.append((self.path, form))
                if self.path != "/auth/realms/acme/protocol/openid-connect/token" or form.get("refresh_token") != fake.key or form.get("grant_type") != "refresh_token":
                    return self._send(400, {"error": "invalid_grant"})
                self._send(200, {"access_token": "tok-1", "expires_in": 1800})

            def do_GET(self):
                u = urllib.parse.urlparse(self.path)
                q = dict(urllib.parse.parse_qsl(u.query))
                fake.calls.append((u.path, q))
                if self.headers.get("Authorization") != "Bearer tok-1":
                    return self._send(401, {"message": "unauthorized"})
                if u.path in fake.fail_once:
                    fake.fail_once.discard(u.path)
                    return self._send(504, {"message": "gateway timeout"})
                if u.path == "/api/projects":
                    ps = [{"id": PID, "name": "shop-api"}, {"id": "x", "name": "shop-api-legacy"}]
                    want = q.get("names") or q.get("name") or ""
                    return self._send(200, {"projects": [p for p in ps if want in p["name"]], "totalCount": 2})
                if u.path == "/api/scans":
                    assert q["project-id"] == PID and q["statuses"] == "Completed"
                    return self._send(200, {"scans": [{"id": SCAN, "branch": q.get("branch") or "main", "createdAt": "2026-11-16T10:00:00Z", "engines": ["sast", "sca", "kics"]}]})
                if u.path == "/api/results":
                    assert q["scan-id"] == SCAN
                    rs, off, lim = _results(), int(q["offset"]), int(q["limit"])
                    return self._send(200, {"results": rs[off:off + lim], "totalCount": len(rs)})
                self._send(404, {"message": "no such path"})
        return Hd


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVMEM_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("DEVMEM_TOKEN", raising=False)
    for k in ("DEVMEM_CX_URL", "CX_BASE_URI", "DEVMEM_CX_API_KEY", "CX_APIKEY", "DEVMEM_CX_TENANT", "CX_TENANT", "DEVMEM_CX_IAM_URL", "CX_BASE_AUTH_URI"):
        monkeypatch.delenv(k, raising=False)
    root = tmp_path / "py_app"
    shutil.copytree(FIX / "py_app", root)
    (root / "requirements.txt").write_text("requests==2.25.0\n")
    (root / "Dockerfile").write_text("FROM python:3.12\n")
    indexer.index_repo(root, "py_app", accuracy="fast")
    fake = FakeCx()
    monkeypatch.setattr(cx.time, "sleep", lambda s: None)
    yield root, fake
    fake.srv.shutdown()


def test_realm_comes_from_the_key_else_from_tenant_and_the_ast_address():
    assert cx.realm_url({"api_key": _jwt({"aud": "https://iam.checkmarx.net/auth/realms/acme"})}) == "https://iam.checkmarx.net/auth/realms/acme"
    assert cx.realm_url({"api_key": "plain", "tenant": "acme", "url": "https://eu.ast.checkmarx.net"}) == "https://eu.iam.checkmarx.net/auth/realms/acme"
    assert cx.realm_url({"api_key": "plain", "tenant": "acme", "url": "https://ast.checkmarx.net"}) == "https://iam.checkmarx.net/auth/realms/acme"
    assert cx.realm_url({"api_key": "plain", "tenant": "acme", "iam_url": "https://sso.corp/x"}) == "https://sso.corp/x/auth/realms/acme"
    with pytest.raises(cx.CheckmarxError, match="tenant"):
        cx.realm_url({"api_key": "plain", "url": "https://ast.checkmarx.net"})


def test_sync_logs_in_pages_retries_and_maps_every_scanner(env):
    root, fake = env
    cx.save_connection(fake.url, fake.key)
    cx.set_project("py_app", "shop-api", "main")
    cx.PAGE = 2                                                                  # force several pages
    fake.fail_once.add("/api/results")                                           # one 504 on the way: retried
    try:
        d = cx.sync("py_app", root, set(Index.load(root).data["files"]))
    finally:
        cx.PAGE = 500
    assert fake.logins[0][1]["client_id"] == "ast-app" and len(fake.logins) == 1   # one login for the whole sync
    assert [c[1]["offset"] for c in fake.calls if c[0] == "/api/results"] == ["0", "0", "2", "4"]
    assert d["project"] == {"id": PID, "name": "shop-api"} and d["scan"]["id"] == SCAN and d["scan"]["branch"] == "main"
    assert d["total"] == 5 and d["skipped"] == 2 and d["by_scanner"] == {"sast": 1, "sca": 1, "kics": 1}
    sql, dep, iac = d["issues"]
    assert (sql["path"], sql["line"], sql["code"], sql["severity"], sql["cwe"]) == ("app/service.py", 5, "SQL_Injection", "high", "CWE-89")
    assert sql["msg"] == "SQL Injection: data from app/client.py:2" and sql["about"]["what"] == "The application builds a SQL query from user input."
    assert (dep["path"], dep["code"], dep["severity"]) == ("requirements.txt", "CVE-2023-32681", "medium")
    assert dep["msg"] == "Python-requests-2.25.0 has CVE-2023-32681 (CVSS 6.1); fixed in 2.31.0"
    assert dep["cwe"] == "CWE-200" and "Python-requests-2.25.0" in dep["about"]["what"] and "2.31.0 or later" in dep["about"]["fix"]
    assert "infrastructure" in iac["about"]["what"]                                   # each engine explains its own kind of finding
    assert (iac["path"], iac["line"], iac["severity"]) == ("Dockerfile", 1, "low") and "expected HEALTHCHECK set, found missing" in iac["msg"]
    assert d["matched"] == 3 and json.loads((root / cx.PATH).read_text())["issues"] == d["issues"]


def test_a_refused_key_and_an_unknown_project_say_what_to_do(env):
    root, fake = env
    cx.save_connection(fake.url, _jwt({"aud": f"{fake.url}/auth/realms/acme"}))
    with pytest.raises(cx.CheckmarxError, match="refused the API key"):
        cx.test()
    cx.save_connection(api_key=fake.key)
    assert cx.test()["ok"] is True
    cx.set_project("py_app", "no-such-project")
    with pytest.raises(cx.CheckmarxError, match="no Checkmarx One project is called"):
        cx.sync("py_app", root, set())


SARIF = {
    "version": "2.1.0", "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
    "runs": [{
        "tool": {"driver": {"name": "Checkmarx AST CLI", "version": "2.3.1", "rules": [
            {"id": "SQL_Injection", "shortDescription": {"text": "SQL Injection"},
             "fullDescription": {"text": "Input reaches a SQL query without being parameterised."},
             "help": {"text": "Use parameterised queries."}, "helpUri": "https://docs.checkmarx.com/sql-injection",
             "properties": {"security-severity": "8.8", "tags": ["security", "external/cwe/cwe-89"]}},
            {"id": "Hardcoded_Password", "shortDescription": {"text": "Hardcoded password"}, "defaultConfiguration": {"level": "note"}}]}},
        "originalUriBaseIds": {"SRCROOT": {"uri": "file:///builds/ci/checkout/"}},
        "results": [
            {"ruleId": "SQL_Injection", "level": "error", "message": {"text": "query built from request.args"},
             "locations": [{"physicalLocation": {"artifactLocation": {"uri": "app/service.py", "uriBaseId": "SRCROOT"}, "region": {"startLine": 5}}}]},
            {"ruleIndex": 1, "message": {"text": "password in code"},
             "locations": [{"physicalLocation": {"artifactLocation": {"uri": "file:///builds/ci/checkout/app/client.py"}, "region": {"startLine": 3}}}]},
            {"ruleId": "SQL_Injection", "message": {"text": "suppressed one"}, "suppressions": [{"kind": "external", "status": "accepted"}],
             "locations": [{"physicalLocation": {"artifactLocation": {"uri": "app/client.py"}, "region": {"startLine": 1}}}]},
        ]}]}


def test_sarif_maps_paths_severity_rules_and_replaces_a_tools_earlier_import(env):
    root, _ = env
    files = set(Index.load(root).data["files"])
    r = sarif.import_file(root, json.dumps(SARIF).encode(), files, "cx_result.sarif")
    assert r["tools"] == ["checkmarx"] and r["issues"] == 2 and r["matched"] == 2 and not r["notes"]
    a, b = sarif.load(root)["issues"]
    assert (a["path"], a["line"], a["severity"], a["category"], a["url"]) == ("app/service.py", 5, "high", "security", "https://docs.checkmarx.com/sql-injection")
    assert (b["path"], b["line"], b["code"], b["severity"]) == ("app/client.py", 3, "Hardcoded_Password", "low")
    other = {"version": "2.1.0", "runs": [{"tool": {"driver": {"name": "CodeQL"}}, "results": [{"ruleId": "py/x", "level": "warning", "message": {"text": "m"},
             "locations": [{"physicalLocation": {"artifactLocation": {"uri": "app/client.py"}, "region": {"startLine": 2}}}]}]}]}
    sarif.import_file(root, json.dumps(other).encode(), files, "codeql.sarif")
    one = json.loads(json.dumps(SARIF)); one["runs"][0]["results"] = one["runs"][0]["results"][:1]
    sarif.import_file(root, json.dumps(one).encode(), files, "cx_result.sarif")       # newer Checkmarx report replaces only Checkmarx's
    assert sorted((t["tool"], t["issues"]) for t in sarif.summary(sarif.load(root))) == [("checkmarx", 1), ("codeql", 1)]
    with pytest.raises(sarif.SarifError, match="no `runs`"):
        sarif.import_file(root, b'{"version": "2.1.0"}', files, "x.sarif")


def test_findings_reach_code_health_agents_and_the_api(env, capsys):
    root, fake = env
    api = TestClient(create_app())
    assert api.post("/api/repos", json={"path": str(root)}, headers=H).status_code == 200
    assert api.put("/api/integrations/checkmarx", json={"url": fake.url, "api_key": fake.key}, headers=H).json()["configured"] is True
    pub = api.get("/api/integrations/checkmarx").json()
    assert pub["has_key"] and fake.key not in json.dumps(pub) and pub["realm"] == f"{fake.url}/auth/realms/acme"
    assert api.post("/api/integrations/checkmarx/test", headers=H).json()["ok"] is True
    assert [p["name"] for p in api.get("/api/integrations/checkmarx/projects?q=shop").json()] == ["shop-api", "shop-api-legacy"]
    assert api.put("/api/repos/py_app/checkmarx", json={"project": "shop-api"}, headers=H).status_code == 200
    r = api.post("/api/repos/py_app/checkmarx/sync", headers=H).json()
    assert r["issues"] == 3 and r["skipped"] == 2
    r = api.post("/api/repos/py_app/sarif?filename=cx_result.sarif", content=json.dumps(SARIF).encode(), headers=H).json()
    assert r["issues"] == 2 and api.get("/api/repos/py_app/sarif").json()[0]["file"] == "cx_result.sarif"
    h = api.get("/api/repos/py_app/health").json()
    by = {(f["source"], f["code"], f["path"]) for f in h["findings"]}
    assert ("checkmarx", "SQL_Injection", "app/service.py") in by and ("checkmarx", "CVE-2023-32681", "requirements.txt") in by
    assert ("checkmarx", "Hardcoded_Password", "app/client.py") in by
    assert sum(1 for f in h["findings"] if f["code"] == "SQL_Injection") == 1                   # same result from API and SARIF: shown once
    assert h["explain"]["checkmarx:SQL_Injection"]["what"] == "The application builds a SQL query from user input."   # the tool's own words
    assert h["explain"]["checkmarx:Hardcoded_Password"]["what"]                                 # from the SARIF rule
    assert h["checkmarx"]["scan"]["id"] == SCAN and h["sarif"][0]["tool"] == "checkmarx" and h["checkmarx_setup"]["configured"]
    capsys.readouterr()
    assert agent_cli.run(["hygiene", "app/service.py"], root=root) == 0
    out = capsys.readouterr().out
    assert "app/service.py:5  checkmarx SQL_Injection [high] SQL Injection: data from app/client.py:2" in out
    assert api.delete("/api/repos/py_app/sarif/checkmarx", headers=H).json() == []
    (root / "found.sarif").write_text(json.dumps(SARIF))                                         # left at the root by CI: picked up on re-index
    api.post("/api/repos/py_app/index", headers=H)
    assert [t["file"] for t in api.get("/api/repos/py_app/sarif").json()] == ["found.sarif"]
