"""Step 10: column lineage, cross-repo column impact, git history, no-touch zones."""
import shutil
import subprocess
from pathlib import Path

import pytest
from devmem import diffcheck as dc
from devmem import lineage as agent_lineage
from devmem.index import Index
from devmem.knowledge import check_knowledge
from devmem_app import history, lineage, publisher
from devmem_app.indexer import index_repo

FIX = Path(__file__).parent.parent / "fixtures"


@pytest.fixture()
def lin_repo(tmp_path):
    r = tmp_path / "lineage_app"
    shutil.copytree(FIX / "lineage_app", r)
    return r


@pytest.fixture()
def consumer(tmp_path):
    r = tmp_path / "consumer_app"
    shutil.copytree(FIX / "consumer_app", r)
    return r


def edge_set(lin):
    return {(e["src"], e["dst"]) for e in lin["column_edges"]}


def read_set(lin):
    return {r["col"] for r in lin["reads"]}


def test_column_lineage_with_schema_snapshot(lin_repo):
    assert lineage.import_schema_csv(lin_repo, lin_repo / "schema.csv") == 5
    lin = lineage.build(index_repo(lin_repo), lin_repo)
    assert {("mart.sales_daily.day", "mart.sales_report.day"), ("dim.customers.name", "mart.sales_report.cust"),
            ("mart.sales_daily.cnt", "mart.sales_report.dbl")} <= edge_set(lin)
    assert {"dim.customers.id", "mart.sales_daily.cid", "mart.sales_daily.day"} <= read_set(lin)  # join/filter reads
    assert lin["schemas"]["mart.sales_report"] == ["cust", "dbl", "day"] or set(lin["schemas"]["mart.sales_report"]) == {"day", "cust", "dbl"}


def test_lineage_without_schema_still_resolves_qualified_columns(lin_repo):
    lin = lineage.build(index_repo(lin_repo), lin_repo)
    assert ("dim.customers.name", "mart.sales_report.cust") in edge_set(lin)
    assert "dim.customers.id" in read_set(lin)


def test_dataframe_heuristic_reads(lin_repo):
    lin = lineage.build(index_repo(lin_repo), lin_repo)
    r = [x for x in lin["reads"] if x["col"] == "raw.orders.day"]
    assert r and r[0]["conf"] == "heuristic" and r[0]["job"] == "jobs/daily.py#build_daily"


def test_column_impact_single_repo_and_cli(lin_repo, monkeypatch, capsys):
    lineage.import_schema_csv(lin_repo, lin_repo / "schema.csv")
    idx = index_repo(lin_repo)
    publisher.publish(lin_repo, idx)
    lin = agent_lineage.load(lin_repo)
    r = agent_lineage.column_impact(lin, "mart.sales_daily", "cnt")
    assert any(x["job"] == "sql/report.sql" for x in r["readers"])
    assert [d["column"] for d in r["derived"]] == ["mart.sales_report.dbl"]
    from devmem.cli import main
    monkeypatch.chdir(lin_repo)
    assert main(["impact", "mart.sales_daily.cnt"]) == 0
    out = capsys.readouterr().out
    assert "-> mart.sales_report.dbl" in out and "sql/report.sql" in out
    assert main(["impact", "mart.sales_daily"]) == 0                       # still a table impact
    assert "readers:" in capsys.readouterr().out


def test_cross_repo_column_impact_and_lineage_md(lin_repo, consumer):
    lineage.import_schema_csv(lin_repo, lin_repo / "schema.csv")
    cidx, lidx = index_repo(consumer), index_repo(lin_repo)
    publisher.publish(consumer, cidx)                          # writes consumer lineage.json
    peers = {"lineage_app": lin_repo, "consumer_app": consumer}
    publisher.publish(lin_repo, lidx, peers)
    combined = lineage.combine(peers)
    r = agent_lineage.column_impact(combined, "mart.sales_daily", "cnt")
    derived = {d["column"]: d for d in r["derived"]}
    assert any(x["job"].startswith("consumer_app:") for x in derived["mart.sales_report.dbl"]["readers"])
    md = (lin_repo / ".devmem/knowledge/lineage.md").read_text()
    assert "## Used by other repos" in md and "consumer_app:" in md and "`mart.sales_report`" in md
    assert check_knowledge(lin_repo) == []


# ---------------- git history ----------------
def git(root, *a):
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True, capture_output=True)


@pytest.fixture()
def hist_repo(tmp_path):
    r = tmp_path / "h"
    (r / "app").mkdir(parents=True)
    (r / "migrations").mkdir()
    (r / "app/gen.py").write_text("# DO NOT EDIT - generated\nX = 1\n")
    (r / "migrations/0001.py").write_text("x = 1\n")
    for name in ("a", "b", "c"):
        (r / f"app/{name}.py").write_text(f"def {name}():\n    return 0\n")
    git(r, "init", "-q"); git(r, "add", "-A"); git(r, "commit", "-qm", "init")
    for i in range(4):
        for name in ("a", "b"):
            (r / f"app/{name}.py").write_text(f"def {name}():\n    return {i + 1}\n")
        git(r, "add", "-A"); git(r, "commit", "-qm", "fix bug in a and b" if i < 3 else "tweak")
    (r / "app/c.py").write_text("def c():\n    return 9\n")
    git(r, "add", "-A"); git(r, "commit", "-qm", "c change")
    return r


def test_mine_hotspots_cochange_no_touch(hist_repo):
    idx = index_repo(hist_repo)
    h = history.mine(hist_repo, idx)
    assert h["hotspots"][0]["file"] in ("app/a.py", "app/b.py") and h["hotspots"][0]["bugfix"] == 3
    assert "app/c.py" not in [x["file"] for x in h["hotspots"]]                # only 2 commits
    pair = h["cochange"][0]
    assert (pair["a"], pair["b"], pair["count"], pair["conf"]) == ("app/a.py", "app/b.py", 5, 1.0)
    assert "migrations/" in h["no_touch"] and "app/gen.py" in h["no_touch"]
    md = history.render_history_md(h)
    assert "## Hotspots" in md and "`app/a.py` + `app/b.py`" in md


def test_publish_history_and_impact_cochange(hist_repo):
    idx = index_repo(hist_repo)
    publisher.publish(hist_repo, idx)
    assert (hist_repo / ".devmem/knowledge/history.md").exists()
    nt = (hist_repo / ".devmem/knowledge/no-touch.md").read_text()
    assert "`migrations/`" in nt and "`app/gen.py`" in nt
    ix = Index.load(hist_repo)
    assert ix.impact("a")["cochange"] == ["app/b.py"]
    assert check_knowledge(hist_repo) == []


def test_diffcheck_no_touch_and_cochange_scope(hist_repo):
    idx = index_repo(hist_repo)
    publisher.publish(hist_repo, idx)
    git(hist_repo, "add", "-A"); git(hist_repo, "commit", "-qm", "publish knowledge")
    (hist_repo / "migrations/0001.py").write_text("x = 2\n")
    (hist_repo / "app/gen.py").write_text("# DO NOT EDIT - generated\nX = 2\n")
    cfg = dc.load_config(hist_repo)
    v = dc.run_checks(hist_repo, dc.collect_diff(hist_repo, None, False), cfg)
    assert {x.path for x in v if x.check == "no_touch"} == {"migrations/0001.py", "app/gen.py"}
    git(hist_repo, "checkout", "-q", ".")
    # a.py and b.py are unrelated in the call graph but co-change: editing both is not "out of scope"
    for n in ("a", "b"):
        (hist_repo / f"app/{n}.py").write_text(f"def {n}():\n    return 100\n")
    assert not [x for x in dc.run_checks(hist_repo, dc.collect_diff(hist_repo, None, False), cfg) if x.check == "out_of_scope"]
