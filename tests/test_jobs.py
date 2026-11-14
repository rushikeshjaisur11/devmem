"""Long operations run as background jobs when asked (?background=1): the request returns at once, the work continues,
and the result or the error is read from /api/jobs/{id}. Without the flag the endpoints behave as before."""
import io
import shutil
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from devmem_app import indexer
from devmem_app.server import create_app

FIX = Path(__file__).parent.parent / "fixtures"
H = {"X-Devmem": "1"}


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVMEM_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("DEVMEM_TOKEN", raising=False)
    monkeypatch.setenv("DEVMEM_ACCURACY", "fast")
    c = TestClient(create_app())
    c.root = tmp_path / "py_app"
    shutil.copytree(FIX / "py_app", c.root)
    return c


def _wait(api, r, seconds=30):
    assert r.status_code == 202, r.text
    jid = r.json()["job"]
    end = time.time() + seconds
    while time.time() < end:
        j = api.get(f"/api/jobs/{jid}").json()
        if j["state"] != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_onboard_index_publish_as_jobs(api):
    j = _wait(api, api.post("/api/repos?background=1", json={"path": str(api.root)}, headers=H))
    assert j["state"] == "done" and j["kind"] == "add" and j["result"]["name"] == "py_app" and j["result"]["symbols"] >= 6
    assert [r["name"] for r in api.get("/api/repos").json()] == ["py_app"]
    j = _wait(api, api.post("/api/repos/py_app/index?background=1", headers=H))
    assert j["state"] == "done" and "stats" in j["result"]
    j = _wait(api, api.post("/api/repos/py_app/publish?background=1", json={}, headers=H))
    assert j["state"] == "done" and ".devmem/knowledge/map.md" in j["result"]["written"]
    assert api.get("/api/jobs").json() == []                                                       # nothing left running
    assert len(api.get("/api/jobs?active=false").json()) == 3
    assert api.post("/api/repos/py_app/index", headers=H).json()["stats"]                         # without the flag: as before


def test_failures_keep_their_status_and_message(api):
    j = _wait(api, api.post("/api/repos?background=1", json={"path": str(api.root / "nope")}, headers=H))
    assert j["state"] == "failed" and j["status"] == 400 and j["error"] == "not a directory"
    api.post("/api/repos", json={"path": str(api.root)}, headers=H)
    j = _wait(api, api.post("/api/repos/py_app/branch?background=1", json={"branch": "no-such"}, headers=H))
    assert j["state"] == "failed" and j["status"] == 400 and j["error"] == "unknown branch no-such"
    assert api.get("/api/jobs/ffffffffffff").status_code == 404


def test_one_index_job_per_repo_and_the_repo_list_shows_it(api, monkeypatch):
    api.post("/api/repos", json={"path": str(api.root)}, headers=H)
    import devmem_app.server as srv
    real = srv._index_repo

    def slow(*a, **k):
        time.sleep(0.6)
        return real(*a, **k)
    monkeypatch.setattr(srv, "_index_repo", slow)
    first = api.post("/api/repos/py_app/index?background=1", headers=H)
    assert first.status_code == 202
    second = api.post("/api/repos/py_app/publish?background=1", json={}, headers=H)
    assert second.status_code == 409 and "already being worked on" in second.json()["detail"]
    row = api.get("/api/repos").json()[0]
    assert row["working"]["kind"] == "index" and row["working"]["job"] == first.json()["job"]
    assert _wait(api, first)["state"] == "done" and "working" not in api.get("/api/repos").json()[0]
    assert _wait(api, api.post("/api/repos/py_app/selfcheck?background=1", headers=H))["result"]["available"] is True


def test_upload_continues_as_a_job(api):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for f in (FIX / "py_app").rglob("*.py"):
            z.write(f, "py_app/" + f.relative_to(FIX / "py_app").as_posix())
    r = api.post("/api/repos/upload?filename=py_app.zip&background=1", content=b.getvalue(), headers=H)
    j = _wait(api, r)
    assert j["state"] == "done" and j["result"]["uploaded"] and j["result"]["symbols"] >= 6
    leftovers = [p for p in (Path(api.root).parent / "home/uploads").iterdir() if p.suffix == ".upload"]
    assert leftovers == []                                                                         # the uploaded archive was removed
    j = _wait(api, api.post("/api/repos/upload?name=bad&background=1", content=b"not a zip", headers=H))
    assert j["state"] == "failed" and j["status"] == 400 and "not a .zip" in j["error"]
