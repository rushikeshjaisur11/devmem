"""Step 9: conventions + helper catalog."""
import shutil
from pathlib import Path

import pytest
from devmem.index import Index
from devmem.knowledge import check_knowledge
from devmem_app import conventions, publisher
from devmem_app.indexer import index_repo

FIX = Path(__file__).parent.parent / "fixtures" / "conv_app"


@pytest.fixture()
def repo(tmp_path):
    r = tmp_path / "conv"
    shutil.copytree(FIX, r)
    return r


def test_detection(repo):
    idx = index_repo(repo)
    c = conventions.detect(idx, repo)
    assert set(c) == {"Logging", "Configuration", "Errors", "Database access", "Retries", "Tests", "Spark session"}
    assert c["Logging"]["golden"]["file"] == "app/svc.py"
    labels = [l for l, _ in c["Logging"]["styles"]]
    assert any("getLogger" in l for l in labels) and any("print" in l for l in labels)
    assert c["Configuration"]["golden"]["file"] == "app/conf.py"
    assert any("environment" in l for l, _ in c["Configuration"]["styles"])
    assert c["Errors"]["golden"]["file"] == "app/errors.py"
    assert any("`NotFound`" in l and "`AppError`" in l for l, _ in c["Errors"]["styles"])
    assert c["Database access"]["golden"]["file"] == "app/db.py"
    assert c["Spark session"]["golden"]["file"] == "app/spark.py"
    assert c["Retries"]["golden"]["file"] == "app/retry.py"
    assert any("called from 2 places" in l for l, _ in c["Retries"]["styles"])
    t = c["Tests"]
    assert t["golden"]["file"] == "tests/test_svc.py" and any("pytest" == l for l, _ in t["styles"])
    assert any("1 pytest fixtures" in l for l, _ in t["styles"])
    assert "getLogger" in c["Logging"]["golden"]["excerpt"]


def test_published_files_and_budgets(repo):
    idx = index_repo(repo)
    publisher.publish(repo, idx)
    cv = (repo / ".devmem/knowledge/conventions.md").read_text()
    assert "## Logging" in cv and "Golden example: `app/svc.py:" in cv and "```" in cv
    hp = (repo / ".devmem/knowledge/helpers.md").read_text()
    assert "`app/retry.py:" in hp and "`app/util.py:" in hp and "slug" in hp and "_private" not in hp
    assert check_knowledge(repo) == []


def test_helpers_cli_includes_utility_module(repo):
    index_repo(repo)
    names = [s["name"] for s, _ in Index.load(repo).helpers()]
    assert "retry" in names and "slug" in names


def test_reindex_after_parser_bump_reparses(repo):
    first = index_repo(repo)
    idx = index_repo(repo)
    assert idx["stats"]["parsed"] == 0 and idx["files"]["app/svc.py"]["features"]["raises"] == {"NotFound": 1}
