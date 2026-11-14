"""Measured coverage: a real coverage.py run decides which tests reach a function, including through fixtures."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from devmem import check as chk
from devmem import cli
from devmem.index import Index
from devmem.review import gaps
from devmem_app import coverage_import as ci
from devmem_app import health, indexer

STORE = "def save(x):\n    y = x * 2\n    return y\n\n\ndef load(x):\n    return x\n\n\ndef never_run(x):\n    z = x + 1\n    return z\n"
CONFTEST = "import pytest\n\nfrom app.store import save\n\n\n@pytest.fixture\ndef saved():\n    return save(2)\n"
TESTS = "from app.store import load\n\n\ndef test_saved(saved):\n    assert saved == 4\n\n\ndef test_load():\n    assert load(1) == 1\n"


def _repo(tmp_path):
    root = tmp_path / "store"
    (root / "app").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "app/__init__.py").write_text("")
    (root / "app/store.py").write_text(STORE)
    (root / "tests/conftest.py").write_text(CONFTEST)
    (root / "tests/test_store.py").write_text(TESTS)
    indexer.index_repo(root, "store", accuracy="fast")
    return root


def _measure(root):
    pytest.importorskip("pytest_cov")
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--cov=app", "--cov-context=test", "tests"], cwd=root, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    subprocess.run([sys.executable, "-m", "coverage", "json", "--show-contexts", "-o", "coverage.json"], cwd=root, check=True, capture_output=True)
    return root / "coverage.json"


def test_fixture_reached_code_is_selected_only_with_measured_coverage(tmp_path, capsys):
    root = _repo(tmp_path)
    ix = Index.load(root)
    assert "tests/test_store.py#test_saved" not in ix.impact("app/store.py#save")["tests"]       # the call graph cannot see the fixture
    capsys.readouterr()
    cli.run(["tests-for", "app/store.py"], root=root)
    out = capsys.readouterr().out
    assert "test_saved" not in out and "# tests chosen from the call graph" in out and "Run the full suite before merging" in out

    d = ci.import_file(ix, _measure(root))
    assert d["format"] == "coverage.py" and d["per_test"] and d["functions"]["app/store.py#save"] == [2, 2] and d["functions"]["app/store.py#never_run"] == [0, 2]
    assert d["tests"]["app/store.py#save"] == ["tests/test_store.py#test_saved"]                  # recorded during the fixture's setup
    ix = Index.load(root)
    r = ix.impact("app/store.py#save")
    assert "tests/test_store.py#test_saved" in r["tests"] and r["tests_measured"] == ["tests/test_store.py#test_saved"]
    assert ix.tests_for_files(["app/store.py"]) == {"tests/test_store.py": ["test_load", "test_saved"]}
    capsys.readouterr()
    cli.run(["tests-for", "app/store.py"], root=root)
    out = capsys.readouterr().out.splitlines()
    assert "tests/test_store.py::test_saved" in out[0] and out[1].startswith("# tests chosen from measured coverage (coverage.json")
    cli.run(["impact", "never_run"], root=root)
    assert "coverage of never_run: 0 of 2 body lines ran in the last measured test run: NOT TESTED" in capsys.readouterr().out
    assert [g["id"] for g in gaps(ix)] == [] and "app/store.py#never_run" not in health._tested(ix) and "app/store.py#save" in health._tested(ix)

    (root / "app/store.py").write_text("# moved\n" + STORE)                                      # code changed after the test run
    indexer.index_repo(root, "store", accuracy="fast")
    assert "app/store.py changed since that test run" in Index.load(root).coverage_note(["app/store.py"])


def test_reports_without_per_test_data_still_mark_tested_code(tmp_path):
    root = _repo(tmp_path)
    ix = Index.load(root)
    lcov = "TN:\nSF:" + str(root / "app/store.py") + "\nDA:1,1\nDA:2,3\nDA:3,3\nDA:6,1\nDA:7,0\nDA:11,0\nend_of_record\nSF:/elsewhere/other.py\nDA:1,1\nend_of_record\n"
    (tmp_path / "lcov.info").write_text(lcov)
    d = ci.import_file(ix, tmp_path / "lcov.info")
    assert d["format"] == "lcov" and not d["per_test"] and d["stats"]["unmatched_files"] == 1          # absolute paths matched, foreign file skipped
    assert d["functions"] == {"app/store.py#save": [2, 2], "app/store.py#load": [0, 1], "app/store.py#never_run": [0, 2]}
    assert "import a coverage report" not in Index.load(root).coverage_note(["app/store.py"])           # line data only: still the call graph for test choice

    cob = '<?xml version="1.0" ?><coverage><packages><package name="app"><classes><class filename="app/store.py"><lines>' \
          '<line number="2" hits="1"/><line number="3" hits="1"/><line number="7" hits="2"/><line number="11" hits="0"/></lines></class></classes></package></packages></coverage>'
    (tmp_path / "cov.xml").write_text(cob)
    assert ci.import_file(ix, tmp_path / "cov.xml")["functions"]["app/store.py#load"] == [1, 1]
    jac = '<report name="x"><package name="app"><sourcefile name="store.py"><line nr="2" mi="0" ci="3"/><line nr="12" mi="1" ci="0"/></sourcefile></package></report>'
    (tmp_path / "jacoco.xml").write_text(jac)
    d = ci.import_file(ix, tmp_path / "jacoco.xml")
    assert d["format"] == "JaCoCo" and d["functions"]["app/store.py#save"] == [1, 2] and d["functions"]["app/store.py#never_run"] == [0, 2]

    (tmp_path / "bad.json").write_text('{"meta": {}}')
    with pytest.raises(ci.CoverageError, match="coverage json --show-contexts"):
        ci.import_file(ix, tmp_path / "bad.json")
    (tmp_path / "other.info").write_text("SF:/x/unrelated.py\nDA:1,1\nend_of_record\n")
    with pytest.raises(ci.CoverageError, match="was it produced from this repo"):
        ci.import_file(ix, tmp_path / "other.info")


def test_report_in_the_repo_is_imported_on_index_and_by_upload(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    _measure(root)                                                                                  # leaves coverage.json at the repo root
    indexer.index_repo(root, "store", accuracy="fast")
    cov = json.loads((root / ".devmem/coverage.json").read_text())
    assert cov["per_test"] and cov["source"] == "coverage.json" and cov["mtime"]
    from fastapi.testclient import TestClient
    from devmem_app.server import create_app
    monkeypatch.setenv("DEVMEM_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("DEVMEM_TOKEN", raising=False)
    api, H = TestClient(create_app()), {"X-Devmem": "1"}
    assert api.post("/api/repos", json={"path": str(root)}, headers=H).status_code == 200
    (root / ".devmem/coverage.json").unlink()
    assert api.get("/api/repos/store/coverage").json() == {"available": False}
    r = api.post("/api/repos/store/coverage?filename=lcov.info", content=b"SF:app/store.py\nDA:2,1\nend_of_record\n", headers=H)
    assert r.status_code == 200 and r.json()["format"] == "lcov" and r.json()["stats"]["covered"] == 1
    assert api.get("/api/repos/store/coverage").json()["available"] is True
    assert api.post("/api/repos/store/coverage", content=b"nonsense", headers=H).status_code == 400
