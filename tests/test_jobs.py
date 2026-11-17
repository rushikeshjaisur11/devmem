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


def test_sync_all_queues_every_repo_reports_progress_and_keeps_a_history(api, monkeypatch, tmp_path):
    other = tmp_path / "second"
    shutil.copytree(FIX / "py_app", other)
    gone = tmp_path / "gone"
    shutil.copytree(FIX / "py_app", gone)
    for p in (api.root, other, gone):
        assert api.post("/api/repos", json={"path": str(p)}, headers=H).status_code == 200
    shutil.rmtree(gone)
    import devmem_app.server as srv
    real = srv._index_repo
    seen = []

    def slow(root, name, progress=None):
        def spy(stage, done=0, total=0):
            seen.append(stage)
            progress(stage, done, total)
        time.sleep(0.3)
        return real(root, name, progress=spy)
    monkeypatch.setattr(srv, "_index_repo", slow)
    (api.root / "app/extra.py").write_text("def added():\n    return 1\n")
    r = api.post("/api/repos/sync", json={}, headers=H).json()
    assert sorted(x["repo"] for x in r["started"]) == ["py_app", "second"] and r["skipped"] == [{"repo": "gone", "why": "folder not found"}]
    rows = {x["name"]: x for x in api.get("/api/repos").json()}
    assert rows["py_app"]["working"]["kind"] == "index" and rows["second"]["working"]["kind"] == "index"       # both shown as working
    assert rows["gone"]["missing"] is True and "working" not in rows["gone"]
    again = api.post("/api/repos/sync", json={}, headers=H).json()                                              # pressed twice: nothing doubles
    assert again["started"] == [] and {x["why"] for x in again["skipped"]} == {"already being worked on", "folder not found"}
    assert api.delete("/api/repos/py_app", headers=H).status_code == 409                                        # not while it is being indexed
    done = [_wait(api, type("R", (), {"status_code": 202, "json": lambda self, j=x["job"]: {"job": j}, "text": ""})()) for x in r["started"]]
    assert all(j["state"] == "done" for j in done)
    assert next(j for j in done if j["repo"] == "py_app")["result"]["stats"]["parsed"] == 1
    assert {"listing files", "reading files", "linking calls", "writing the index"} <= set(seen)
    hist = api.get("/api/jobs/history").json()
    assert {(h["repo"], h["kind"], h["state"]) for h in hist[:2]} == {("py_app", "index", "done"), ("second", "index", "done")}
    assert any(h["summary"].startswith("read 1 changed file,") for h in hist[:2])
    assert api.get("/api/jobs/history?repo=second&limit=1").json()[0]["repo"] == "second"


def test_queued_jobs_wait_for_a_free_slot(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVMEM_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("DEVMEM_TOKEN", raising=False)
    monkeypatch.setenv("DEVMEM_ACCURACY", "fast")
    monkeypatch.setenv("DEVMEM_JOBS", "1")
    c = TestClient(create_app())
    for n in ("a", "b"):
        shutil.copytree(FIX / "py_app", tmp_path / n)
        c.post("/api/repos", json={"path": str(tmp_path / n)}, headers=H)
    import devmem_app.server as srv
    real = srv._index_repo
    running, most = [], []

    def slow(*a, **k):
        running.append(1); most.append(len(running))
        time.sleep(0.4)
        try:
            return real(*a, **k)
        finally:
            running.pop()
    monkeypatch.setattr(srv, "_index_repo", slow)
    started = c.post("/api/repos/sync", json={}, headers=H).json()["started"]
    time.sleep(0.15)
    jobs = c.get("/api/jobs").json()
    assert sorted(j["queued"] for j in jobs) == [False, True]                      # one works, one waits
    assert [r["working"]["queued"] for r in c.get("/api/repos").json()].count(True) == 1
    for x in started:
        end = time.time() + 20
        while c.get(f"/api/jobs/{x['job']}").json()["state"] == "running" and time.time() < end:
            time.sleep(0.05)
    assert max(most) == 1 and all(c.get(f"/api/jobs/{x['job']}").json()["state"] == "done" for x in started)


def test_a_failed_job_stays_on_the_repo_row_and_a_broken_repo_does_not_hide_the_others(api, monkeypatch):
    api.post("/api/repos", json={"path": str(api.root)}, headers=H)
    second = api.root.parent / "second"
    shutil.copytree(FIX / "py_app", second)
    api.post("/api/repos", json={"path": str(second)}, headers=H)
    import devmem_app.server as srv
    real = srv._index_repo

    def boom(root, name, progress=None):
        if name == "py_app":
            raise RuntimeError("disk full")
        return real(root, name, progress=progress)
    monkeypatch.setattr(srv, "_index_repo", boom)
    j = _wait(api, api.post("/api/repos/py_app/index?background=1", headers=H))
    assert j["state"] == "failed"
    row = api.get("/api/repos").json()[0]
    assert row["last_error"]["kind"] == "index" and "disk full" in row["last_error"]["error"]
    assert api.get("/api/jobs/history").json()[0]["state"] == "failed"
    monkeypatch.setattr(srv, "_index_repo", real)
    assert _wait(api, api.post("/api/repos/py_app/index?background=1", headers=H))["state"] == "done"
    assert "last_error" not in api.get("/api/repos").json()[0]                      # cleared by the next success
    (api.root / ".devmem/hygiene.json").write_text("{not json")                    # one repo's state file is damaged
    rows = {r["name"]: r for r in api.get("/api/repos").json()}
    assert "error" in rows["py_app"] and rows["second"]["indexed"] is True and "error" not in rows["second"]


def test_repos_added_at_the_same_time_are_all_kept(api):
    import threading
    roots = []
    for i in range(6):
        d = api.root.parent / f"r{i}"
        shutil.copytree(FIX / "py_app", d)
        roots.append(d)
    ts = [threading.Thread(target=lambda d=d: api.post("/api/repos", json={"path": str(d)}, headers=H)) for d in roots]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sorted(r["name"] for r in api.get("/api/repos").json()) == [f"r{i}" for i in range(6)]
