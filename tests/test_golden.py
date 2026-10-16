"""Golden tests: fixture repos with known answers for symbols, edges, impact, tables."""
import shutil
from pathlib import Path

import pytest
from devmem.index import Index
from devmem.ignore import Ignore
from devmem_app.indexer import index_repo

FIX = Path(__file__).parent.parent / "fixtures"


@pytest.fixture()
def py_repo(tmp_path):
    dst = tmp_path / "py_app"
    shutil.copytree(FIX / "py_app", dst)
    return dst


@pytest.fixture()
def spark_repo(tmp_path):
    dst = tmp_path / "spark_sql"
    shutil.copytree(FIX / "spark_sql", dst)
    return dst


def ids(idx):
    return {s["id"] for s in idx["symbols"]}


def edges(idx, typ="calls"):
    return {(e["src"], e["dst"]) for e in idx["edges"] if e["type"] == typ}


def test_symbols(py_repo):
    idx = index_repo(py_repo)
    assert {"app/retry.py#retry", "app/client.py#ApiClient.get", "app/service.py#load_user",
            "app/client.py#build_client", "tests/test_service.py#test_load_user"} <= ids(idx)
    t = next(s for s in idx["symbols"] if s["id"] == "tests/test_service.py#test_load_user")
    assert t["is_test"]


def test_call_edges(py_repo):
    idx = index_repo(py_repo)
    e = edges(idx)
    assert ("app/client.py#ApiClient.get", "app/retry.py#retry") in e          # from-import
    assert ("app/service.py#load_user", "app/retry.py#retry") in e             # module-alias import
    assert ("app/service.py#load_user", "app/client.py#build_client") in e
    assert ("app/service.py#load_user", "app/client.py#ApiClient.get") in e    # obj.method, unique
    assert ("tests/test_service.py#test_load_user", "app/service.py#load_user") in edges(idx, "tests")


def test_impact(py_repo):
    index_repo(py_repo)
    r = Index.load(py_repo).impact("retry")
    h1 = {i["id"] for i in r["hop1"]}
    assert h1 == {"app/client.py#ApiClient.get", "app/service.py#load_user"}
    assert "tests/test_service.py#test_load_user" in r["tests"]
    assert "app/service.py" in r["files"]


def test_find_show_outline_helpers(py_repo):
    index_repo(py_repo)
    ix = Index.load(py_repo)
    assert ix.find("retry")[0][1]["id"] == "app/retry.py#retry"
    assert "def retry" in ix.source(ix.resolve("retry")[0])
    assert [s["name"] for s in ix.callees(ix.resolve("load_user")[0])]
    assert [s["name"] for s in ix.outline("app/client.py")] == ["ApiClient", "__init__", "get", "_raw", "build_client"]
    assert [s["name"] for s, _ in ix.helpers()] == ["retry"]


def test_incremental(py_repo):
    first = index_repo(py_repo)
    assert first["stats"]["reused"] == 0
    (py_repo / "app" / "retry.py").write_text("def retry(fn):\n    return fn()\n")
    second = index_repo(py_repo)
    assert second["stats"] == {"parsed": 1, "reused": first["stats"]["parsed"] - 1}


def test_tables(spark_repo):
    idx = index_repo(spark_repo)
    ix = Index.load(spark_repo)
    r = ix.impact("raw.orders")
    assert r["readers"] == ["jobs/daily_sales.py#build_daily"]
    w = ix.impact("mart.sales_daily")
    assert w["writers"] == ["jobs/daily_sales.py#build_daily"]
    assert set(w["readers"]) == {"jobs/daily_sales.py#enrich", "sql/report.sql"}
    assert ix.impact("mart.sales_report")["writers"] == ["sql/report.sql"]
    assert ix.impact("dim.customers")["readers"] == ["jobs/daily_sales.py#enrich", "sql/report.sql"]


def test_ignore(tmp_path):
    ig = Ignore(["generated/", "*.lock", "a/b/"])
    assert ig.matches("generated/x.py") and ig.matches("src/generated/x.py") and ig.matches("poetry.lock")
    assert ig.matches("a/b/c.py") and not ig.matches("src/app.py")


def test_cli_runs(py_repo, monkeypatch, capsys):
    index_repo(py_repo)
    monkeypatch.chdir(py_repo)
    from devmem.cli import main
    assert main(["find", "retry"]) == 0
    assert "app/retry.py:5" in capsys.readouterr().out
    assert main(["impact", "retry"]) == 0
    assert "hop 1" in capsys.readouterr().out
