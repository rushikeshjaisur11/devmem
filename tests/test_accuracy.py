"""Accuracy is not optional: strict indexing runs SCIP and Graphify automatically, only when files changed, and refuses to continue
without them. The external tools are replaced by the fixtures this repo ships (real scip-python / graphify output for py_app)."""
import shutil
from pathlib import Path

import pytest

from devmem_app import graphify as graphify_mod
from devmem_app import indexer, jsparse, scip as scip_mod
from devmem_app.indexer import AccuracyError, index_repo

FIX = Path(__file__).parent.parent / "fixtures"


@pytest.fixture()
def strict(monkeypatch):
    monkeypatch.setenv("DEVMEM_ACCURACY", "strict")


@pytest.fixture()
def tools(monkeypatch):
    calls = {"scip": 0, "graphify": 0}

    def fake_scip(root, idx, timeout=900):
        calls["scip"] += 1
        return [FIX / "scip/py_app.scip"], []

    def fake_graphify(root, timeout=900):
        calls["graphify"] += 1
        return FIX / "graphify/graph.json", ""
    monkeypatch.setattr(scip_mod, "run_indexer", fake_scip)
    monkeypatch.setattr(graphify_mod, "run_graphify", fake_graphify)
    return calls


def _repo(tmp_path, fx="py_app"):
    d = tmp_path / fx
    shutil.copytree(FIX / fx, d)
    return d


def test_strict_is_the_default_and_runs_both_tools(strict, tools, tmp_path, monkeypatch):
    monkeypatch.delenv("DEVMEM_ACCURACY")                        # unset means strict
    idx = index_repo(_repo(tmp_path), "py_app")
    assert tools == {"scip": 1, "graphify": 1} and idx["stats"]["accuracy"] == "full"
    assert "scip" in idx["stats"] and "graphify" in idx["stats"]


def test_tools_rerun_only_when_files_change(strict, tools, tmp_path):
    d = _repo(tmp_path)
    index_repo(d, "py_app")
    index_repo(d, "py_app")
    assert tools == {"scip": 1, "graphify": 1}                    # unchanged repo: served from the per-file cache
    f = d / "app/service.py"
    f.write_text(f.read_text() + "\n\ndef another():\n    return 1\n")
    index_repo(d, "py_app")
    assert tools == {"scip": 2, "graphify": 2}


def test_missing_tool_stops_indexing_with_an_actionable_message(strict, tmp_path, monkeypatch):
    monkeypatch.setattr(scip_mod.shutil, "which", lambda n: None)                 # no npx
    with pytest.raises(AccuracyError, match="SCIP.*Node.js.*--fast"):
        index_repo(_repo(tmp_path), "py_app")
    monkeypatch.setattr(scip_mod, "run_indexer", lambda *a, **k: ([FIX / "scip/py_app.scip"], []))
    monkeypatch.setattr(graphify_mod.shutil, "which", lambda n: None)             # no graphify executable
    with pytest.raises(AccuracyError, match="Graphify"):
        index_repo(_repo(tmp_path / "again"), "py_app")


def test_failure_leaves_no_index_behind(strict, tmp_path, monkeypatch):
    monkeypatch.setattr(scip_mod, "run_indexer", lambda *a, **k: ([], ["scip-python failed: boom"]))
    d = _repo(tmp_path)
    with pytest.raises(AccuracyError):
        index_repo(d, "py_app")
    assert not (d / ".devmem/index.json").exists()


def test_fast_mode_is_an_explicit_recorded_opt_out(tools, tmp_path):
    idx = index_repo(_repo(tmp_path), "py_app", accuracy="fast")
    assert tools == {"scip": 0, "graphify": 0} and idx["stats"]["accuracy"] == "fast"


def test_repo_without_scip_or_graphify_languages_needs_neither(strict, tools, tmp_path):
    d = tmp_path / "sqlonly"; d.mkdir()
    (d / "q.sql").write_text("select 1 as x from t")
    assert index_repo(d, "sqlonly")["stats"]["accuracy"] == "full" and tools == {"scip": 0, "graphify": 0}


def test_js_without_tree_sitter_is_refused_in_strict_mode(strict, tools, tmp_path, monkeypatch):
    monkeypatch.setattr(jsparse, "available", lambda: False)
    with pytest.raises(AccuracyError, match="tree-sitter"):
        index_repo(_repo(tmp_path, "js_app"), "js_app")
    assert index_repo(_repo(tmp_path / "x", "js_app"), "js_app", accuracy="fast")["stats"]["js_skipped"] >= 1


def test_api_rejects_onboarding_when_a_tool_is_missing_and_registers_nothing(strict, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from devmem_app.server import create_app
    monkeypatch.setenv("DEVMEM_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(scip_mod.shutil, "which", lambda n: None)
    c = TestClient(create_app())
    r = c.post("/api/repos", json={"path": str(_repo(tmp_path))}, headers={"X-Devmem": "1"})
    assert r.status_code == 422 and "accuracy requirement not met" in r.json()["detail"]
    assert c.get("/api/repos").json() == []
