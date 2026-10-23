import subprocess
from pathlib import Path
from types import SimpleNamespace

from devmem_app import cli


def test_add_repo_by_git_url(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("DEVMEM_HOME", str(tmp_path / "home"))
    calls = []
    indexed = {}

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    def index_repo(root, name):
        indexed.update(root=Path(root), name=name)
        return {"symbols": [], "edges": [], "stats": {}}

    monkeypatch.setattr(cli.subprocess, "run", run)
    monkeypatch.setattr(cli, "index_repo", index_repo)

    assert cli._main(["add", "https://github.com/rushikeshjaisur11/architectlens"]) == 0

    cloned = tmp_path / "home" / "clones" / "architectlens"
    assert calls[0][0] == ["git", "clone", "--", "https://github.com/rushikeshjaisur11/architectlens", str(cloned)]
    assert calls[0][1]["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert indexed == {"root": cloned, "name": "architectlens"}
    assert cli._ws()["repos"] == {"architectlens": str(cloned)}
    assert "onboarded architectlens" in capsys.readouterr().out


def test_add_repo_by_local_path(monkeypatch, tmp_path):
    monkeypatch.setenv("DEVMEM_HOME", str(tmp_path / "home"))
    repo = tmp_path / "local-repo"
    repo.mkdir()
    indexed = {}

    def index_repo(root, name):
        indexed.update(root=Path(root), name=name)
        return {"symbols": [], "edges": [], "stats": {}}

    monkeypatch.setattr(cli, "index_repo", index_repo)

    assert cli._main(["add", str(repo), "--name", "local"]) == 0
    assert indexed == {"root": repo, "name": "local"}
    assert cli._ws()["repos"] == {"local": str(repo)}
