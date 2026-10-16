"""Step 7: test map, tests-for, commands detection/verification, check, flaky."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from devmem import check as chk
from devmem.index import Index
from devmem_app import commands, publisher
from devmem_app.indexer import index_repo

FIX = Path(__file__).parent.parent / "fixtures" / "py_app"


@pytest.fixture()
def repo(tmp_path):
    r = tmp_path / "r"
    shutil.copytree(FIX, r)
    (r / ".gitignore").write_text(".devmem/\n")
    subprocess.run(["git", "-C", str(r), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(r), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(r), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "b"], check=True)
    index_repo(r)
    return r


def test_tests_for_files(repo):
    ix = Index.load(repo)
    assert ix.tests_for_files(["app/retry.py"]) == {"tests/test_service.py": ["test_load_user"]}
    assert ix.tests_for_files(["tests/test_service.py"]) == {"tests/test_service.py": ["test_load_user"]}
    ids, cmd = chk.tests_for(ix, ["app/client.py"])
    assert ids == ["tests/test_service.py::test_load_user"] and cmd.endswith("tests/test_service.py::test_load_user")


def test_import_fallback(repo):
    (repo / "app/lonely.py").write_text("def lonely():\n    return 1\n")
    (repo / "tests/test_lonely.py").write_text("from app.lonely import lonely\n\n\ndef test_a():\n    assert lonely() == 1\n")
    # test_a does call lonely -> direct; use module import without calls to exercise fallback
    (repo / "app/quiet.py").write_text("X = 1\n\n\ndef q():\n    return X\n")
    (repo / "tests/test_quiet.py").write_text("import app.quiet\n\n\ndef test_b():\n    assert app.quiet.X == 1\n")
    index_repo(repo)
    ix = Index.load(repo)
    assert ix.tests_for_files(["app/quiet.py"]) == {"tests/test_quiet.py": ["test_b"]}


def test_detect_and_verify(repo):
    (repo / "pyproject.toml").write_text("[tool.pytest.ini_options]\ntestpaths=['tests']\n")
    d = commands.detect(repo)
    assert d["test"]["cmd"] == "python -m pytest -q" and "lint" not in d
    (repo / "pyproject.toml").write_text("[tool.ruff]\nline-length=100\n")
    assert commands.detect(repo)["lint"]["cmd"] == "ruff check {files}"
    idx = index_repo(repo)
    res = commands.verify(repo, idx)
    assert res["test"]["verified"] is True and res["test_one"]["verified"] is True
    assert "install" not in res
    saved = json.loads((repo / ".devmem/commands.json").read_text())
    assert saved["test"]["verified"] is True


def test_publish_writes_commands_and_tests_md(repo):
    idx = index_repo(repo)
    commands.verify(repo, idx)
    publisher.publish(repo, idx)
    cm = (repo / ".devmem/knowledge/commands.md").read_text()
    assert "python -m pytest -q" in cm and "| yes |" in cm
    tm = (repo / ".devmem/knowledge/tests.md").read_text()
    assert "`app/retry.py` <-" in tm and "tests/test_service.py::test_load_user" in tm


def test_check_ok_and_failure_summary(repo, capsys):
    ix = Index.load(repo)
    assert chk.run_check(ix, ["app/retry.py"]) == 0
    assert "ok   test" in capsys.readouterr().out
    (repo / "tests/test_service.py").write_text(
        "from app.service import load_user\n\n\ndef test_load_user():\n    assert 'nope' in load_user(1)\n")
    ix = Index.load(repo)
    assert chk.run_check(ix, ["app/retry.py"]) == 1
    out = capsys.readouterr().out
    assert "FAIL test" in out and "FAILED tests/test_service.py::test_load_user" in out and len(out.splitlines()) < 12


def test_known_flaky_failure_is_ignored(repo, capsys):
    (repo / "tests/test_service.py").write_text(
        "from app.service import load_user\n\n\ndef test_load_user():\n    assert 'nope' in load_user(1)\n")
    (repo / ".devmem/flaky.txt").write_text("tests/test_service.py::test_load_user\n")
    assert chk.run_check(Index.load(repo), ["app/retry.py"]) == 0
    assert "known flaky" in capsys.readouterr().out


def test_detect_flaky(repo):
    (repo / "tests/test_flaky.py").write_text(
        "import pathlib\n\n\ndef test_odd():\n    p = pathlib.Path(__file__).with_name('n.txt')\n"
        "    n = int(p.read_text()) + 1 if p.exists() else 1\n    p.write_text(str(n))\n    assert n % 2 == 0\n")
    flaky = commands.detect_flaky(repo, runs=3)
    assert flaky == ["tests/test_flaky.py::test_odd"]
    assert "test_odd" in (repo / ".devmem/flaky.txt").read_text()


def test_cli_tests_for(repo, monkeypatch, capsys):
    from devmem.cli import main
    monkeypatch.chdir(repo)
    assert main(["tests-for", "app/retry.py"]) == 0
    assert "tests/test_service.py::test_load_user" in capsys.readouterr().out
