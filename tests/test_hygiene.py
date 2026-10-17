"""Step 8: hygiene runners, clones, hygiene.md, lint-only diffcheck rule."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from devmem import diffcheck as dc
from devmem.knowledge import check_knowledge
from devmem_app import hygiene, publisher
from devmem_app.clones import find_clones
from devmem_app.indexer import index_repo

FIX = Path(__file__).parent.parent / "fixtures" / "messy"


@pytest.fixture()
def messy(tmp_path):
    r = tmp_path / "messy"
    shutil.copytree(FIX, r)
    return r


@pytest.fixture()
def result(messy):
    idx = index_repo(messy)
    return hygiene.run_hygiene(messy, idx, history=False), idx


def codes(res, tool):
    return {i["code"] for i in res["issues"] if i["tool"] == tool}


def test_all_tools_ran(result):
    res, _ = result
    assert all(v in ("ok", "-") for v in res["tools"].values()), res["tools"]


def test_ruff_vulture_radon_deptry_bandit_sqlfluff(result):
    res, _ = result
    assert {"F401"} <= codes(res, "ruff")                       # unused imports (os, sys)
    dead = {i["msg"] for i in res["issues"] if i["tool"] == "vulture"}
    assert "unused function 'never_called'" in dead and "unused import 'os'" in dead
    assert not any("'used'" in m for m in dead)                  # used() is called
    assert not any("'main'" in m for m in dead)                  # entry points are not reported
    cx = [i for i in res["issues"] if i["tool"] == "radon"]
    assert cx and cx[0]["msg"].startswith("tangled") and cx[0]["complexity"] > 10
    assert "DEP002" in codes(res, "deptry")                      # requests declared, never imported
    assert "B602" in codes(res, "bandit")                        # shell=True
    assert any(i["path"] == "sql/q.sql" for i in res["issues"] if i["tool"] == "sqlfluff")


def test_clones_detect_renamed_copy(result):
    res, _ = result
    pairs = {(c["a"], c["b"]) for c in res["clones"]}
    assert ("app/a.py", "app/b.py") in pairs
    c = next(c for c in res["clones"] if (c["a"], c["b"]) == ("app/a.py", "app/b.py"))
    assert c["a_lines"][1] - c["a_lines"][0] >= 8


def test_clones_cross_repo_labels():
    src = (FIX / "app/a.py").read_text()
    out = find_clones({"r1:a.py": src, "r2:b.py": (FIX / "app/b.py").read_text()})
    assert out and {out[0]["a"], out[0]["b"]} == {"r1:a.py", "r2:b.py"}


def test_hygiene_md_published_within_budget(messy, result):
    res, idx = result
    publisher.publish(messy, idx)
    md = (messy / ".devmem/knowledge/hygiene.md").read_text()
    assert "never_called" in md and "tangled" in md and "DEP002" in md and "B602" in md
    assert check_knowledge(messy) == []
    # second run reports deltas
    res2 = hygiene.run_hygiene(messy, idx, history=False)
    assert res2["prev_counts"] == res["counts"]


def test_agent_cli_hygiene_file(messy, result, monkeypatch, capsys):
    from devmem.cli import main
    monkeypatch.chdir(messy)
    assert main(["hygiene", "app/util.py"]) == 0
    out = capsys.readouterr().out
    assert "F401" in out and "never_called" in out


# ---- diffcheck: lint-only edits ----
def git(root, *a):
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True, capture_output=True)


def test_lint_only_edit_flagged_next_to_real_change(messy):
    (messy / ".gitignore").write_text(".devmem/\n")
    git(messy, "init", "-q"); git(messy, "add", "-A"); git(messy, "commit", "-qm", "b")
    index_repo(messy)
    u = messy / "app/util.py"
    t = u.read_text().replace("def used(x):\n    return x + 1", "def used(x):\n    return x + 2")  # real change
    t = t.replace("import os\n", "")                                                                 # lint-only fix
    u.write_text(t)
    cfg = dc.load_config(messy)
    v = dc.run_checks(messy, dc.collect_diff(messy, None, False), cfg)
    assert [x.check for x in v] == ["lint_fix"]
    # a pure lint cleanup (no substantive change) is the task itself: allowed
    git(messy, "checkout", "-q", "."); u.write_text(u.read_text().replace("import os\n", ""))
    assert dc.run_checks(messy, dc.collect_diff(messy, None, False), cfg) == []
