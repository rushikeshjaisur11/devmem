"""The watcher re-indexes a repo once its HEAD moves past the indexed commit, and does not retry a HEAD that failed."""
import shutil
import subprocess
from pathlib import Path

from devmem_app import watch
from devmem_app.indexer import AccuracyError, index_repo

FIX = Path(__file__).parent.parent / "fixtures"
GIT = ["git", "-c", "user.email=a@b", "-c", "user.name=a"]


def test_watch_reindexes_when_head_moves(tmp_path, monkeypatch):
    repo = tmp_path / "py_app"
    shutil.copytree(FIX / "py_app", repo)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(GIT + ["add", "-A"], cwd=repo, check=True)
    subprocess.run(GIT + ["commit", "-qm", "one"], cwd=repo, check=True)
    index_repo(repo, "py_app")
    repos, failed, log = {"py_app": str(repo)}, {}, []
    assert watch.stale(repo) is None and watch.tick(repos, False, failed, log.append) == []
    (repo / "app" / "extra.py").write_text("def brand_new():\n    return 1\n", encoding="utf-8")
    subprocess.run(GIT + ["add", "-A"], cwd=repo, check=True)
    subprocess.run(GIT + ["commit", "-qm", "two"], cwd=repo, check=True)
    assert watch.stale(repo)
    assert watch.tick(repos, False, failed, log.append) == ["py_app"] and watch.stale(repo) is None
    assert "brand_new" in (repo / ".devmem/index.json").read_text(encoding="utf-8")

    subprocess.run(GIT + ["commit", "-qm", "three", "--allow-empty"], cwd=repo, check=True)
    calls = []
    def boom(*a, **k):
        calls.append(1)
        raise AccuracyError("scip missing")
    monkeypatch.setattr(watch, "index_repo", boom)
    assert watch.tick(repos, False, failed, log.append) == [] and watch.tick(repos, False, failed, log.append) == []
    assert len(calls) == 1 and "not re-indexed" in log[-1]                     # same failing HEAD is not retried
