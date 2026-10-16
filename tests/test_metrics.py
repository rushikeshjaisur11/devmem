import json
import os
import subprocess
from pathlib import Path

from devmem import report
from devmem_app import metrics as mt


def git(root, *a, **env):
    e = {**os.environ, **env}
    subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True, env=e)


def commit(root, name, lines, msg, when, files=1, author="Dev One <dev@x.com>"):
    for i in range(files):
        (root / f"{name}{i}.py").write_text("\n".join(f"x{j} = {name}{when}" for j in range(lines)) + "\n")
    git(root, "add", "-A")
    nm, em = author.rstrip(">").split(" <")
    git(root, "-c", f"user.name={nm}", "-c", f"user.email={em}", "commit", "-q", "-m", msg, GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)


def make_repo(tmp_path):
    r = tmp_path / "repo"; r.mkdir()
    git(r, "init", "-q")
    devin = "Devin AI <devin-ai-integration[bot]@users.noreply.github.com>"
    commit(r, "a", 400, "big agent change", "2026-01-05T10:00:00+00:00", files=6, author=devin)
    commit(r, "b", 5, "human tweak", "2026-01-10T10:00:00+00:00")
    commit(r, "c", 300, "another big agent change", "2026-01-12T10:00:00+00:00", files=5, author=devin)
    (r / ".devmem").mkdir(); (r / ".devmem/config.yaml").write_text("diff_budget:\n  max_changed_lines: 200\n")
    git(r, "add", "-A")
    git(r, "-c", "user.name=Dev", "-c", "user.email=d@x.com", "commit", "-q", "-m", "add devmem", GIT_AUTHOR_DATE="2026-02-01T10:00:00+00:00", GIT_COMMITTER_DATE="2026-02-01T10:00:00+00:00")
    commit(r, "d", 20, "small agent change", "2026-02-03T10:00:00+00:00", files=1, author=devin)
    commit(r, "e", 30, "small agent change 2", "2026-02-05T10:00:00+00:00", files=2, author=devin)
    return r


def test_before_after_and_split(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVMEM_HOME", str(tmp_path / "home"))
    r = make_repo(tmp_path)
    s = mt.summary(r)
    assert s["split"].startswith("2026-02-01") and s["agent_commits"] == 4
    b, a = s["agent"]["before"], s["agent"]["after"]
    assert b["n"] == 2 and a["n"] == 2
    assert a["median_lines"] < b["median_lines"] and b["within_budget"] == 0 and a["within_budget"] == 100
    assert s["human"]["before"]["n"] == 1                       # the "add devmem" commit only touches .devmem, so it is skipped
    assert all(c["files"] > 0 for c in s["recent"])


def test_report_record(tmp_path):
    r = make_repo(tmp_path)
    (r / "g.py").write_text("print(1)\n" * 3)
    git(r, "add", "-A")
    rec = report.build_record(r, None, "task x", 12345)
    assert rec["files"] == 1 and rec["tokens"] == 12345 and rec["violations"]["blocked"] == []
    p = report.write_record(r, rec)
    assert p.parent.name == "runs" and json.loads(p.read_text())["task"] == "task x"
    assert mt.summary(r)["runs"]["median_tokens"] == 12345


def test_devin_ingest_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVMEM_HOME", str(tmp_path / "home"))
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"sessions": [
        {"session_id": "s1", "created_at": "2026-01-03T00:00:00Z", "acus_consumed": 8, "pull_requests": [{"pr_url": "u1"}]},
        {"session_id": "s2", "created_at": "2026-02-03T00:00:00Z", "acus_consumed": 3, "title": "t"}]}))
    assert mt.ingest_devin(f) == {"new": 2, "total": 2}
    assert mt.ingest_devin(f)["new"] == 0
    d = mt.devin_summary("2026-02-01T00:00:00+00:00")
    assert d["before"]["acus_median"] == 8 and d["after"]["acus_median"] == 3 and d["before"]["with_pr"] == 1
