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
    assert {k: second["stats"][k] for k in ("parsed", "reused")} == {"parsed": 1, "reused": first["stats"]["parsed"] - 1}


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


# ---- ranking + publisher ----
from devmem.knowledge import check_knowledge, est_tokens, managed_block, parse_header, replace_managed_block
from devmem_app.publisher import publish
from devmem_app.ranking import pagerank, render_map


def test_pagerank_prefers_widely_called(py_repo):
    idx = index_repo(py_repo)
    r = pagerank(idx)
    top3 = sorted(r, key=r.get, reverse=True)[:3]
    assert "app/retry.py#retry" in top3
    assert r["app/retry.py#retry"] > r["app/service.py#main"]
    assert abs(sum(r.values()) - 1) < 1e-6


def test_map_respects_budget(py_repo):
    idx = index_repo(py_repo)
    text, inc = render_map(idx, budget=30)
    assert est_tokens(text) <= 30 and 0 < len(inc) < 6
    full, inc2 = render_map(idx, budget=1500)
    assert "def retry" in full and len(inc2) == 8


def test_publish(py_repo):
    idx = index_repo(py_repo)
    (py_repo / "AGENTS.md").write_text("# Mine\nkeep this\n")
    files = publish(py_repo, idx)
    assert ".devmem/knowledge/map.md" in files and ".devmem/knowledge/modules/app.md" in files
    k = py_repo / ".devmem/knowledge"
    assert parse_header((k / "overview.md").read_text())["tokens"] > 0
    ag = (py_repo / "AGENTS.md").read_text()
    assert "keep this" in ag and "devmem find" in managed_block(ag)
    assert (py_repo / ".github/copilot-instructions.md").exists()
    assert check_knowledge(py_repo) == []
    # idempotent: second publish keeps a single block and does not clobber repo-owned config
    (py_repo / ".devmem/rules.md").write_text("custom\n")
    publish(py_repo, index_repo(py_repo))
    assert (py_repo / "AGENTS.md").read_text().count("<!-- devmem:start -->") == 1
    assert (py_repo / ".devmem/rules.md").read_text() == "custom\n"


def test_stale_detection(py_repo):
    idx = index_repo(py_repo)
    publish(py_repo, idx)
    idx_head = "deadbeef" * 5
    # stamps say 'unknown' (no git) so force one
    f = py_repo / ".devmem/knowledge/overview.md"
    f.write_text(f.read_text().replace("commit=unknown", "commit=aaaaaaaa11111111"))
    assert any("stale" in p for p in check_knowledge(py_repo, head=idx_head))


def test_replace_block_roundtrip():
    t = replace_managed_block("a\n", "X")
    assert replace_managed_block(t, "Y").count("Y") == 1 and "X" not in replace_managed_block(t, "Y")
