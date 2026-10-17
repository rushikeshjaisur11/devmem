import json
import shutil
from pathlib import Path

from devmem_app import graphify, indexer

FIX = Path(__file__).parent.parent / "fixtures"


def test_import_real_graphify_output_with_prefixed_paths(tmp_path):
    d = tmp_path / "py_app"
    shutil.copytree(FIX / "py_app", d)
    idx = indexer.index_repo(d, "py_app", graphify=FIX / "graphify/graph.json")   # graph was built one level up: paths start "py_app/"
    g = idx["stats"]["graphify"]
    assert g["edges"] >= 6 and g["stale_files"] == 0
    assert any(e["src"] == "app/service.py#load_user" and e["dst"] == "app/client.py#build_client" for e in idx["edges"])


def test_policy_upgrade_drop_add():
    idx = {"symbols": [{"id": f"{f}#{n}", "name": n, "qualname": n, "kind": "function", "file": f, "line": 1, "end_line": 9, "is_test": False}
                       for f, n in (("a.py", "run"), ("b.py", "go"), ("c.py", "go"), ("d.py", "extra"))],
           "files": {f: {} for f in ("a.py", "b.py", "c.py", "d.py")},
           "edges": [{"src": "a.py#run", "dst": "b.py#go", "type": "calls", "conf": "ambiguous"},
                     {"src": "a.py#run", "dst": "c.py#go", "type": "calls", "conf": "ambiguous"},
                     {"src": "a.py#run", "dst": "table:t", "type": "reads", "conf": "structural"}]}
    node = lambda i, f: {"id": i, "source_file": f, "source_location": "L2"}
    graph = {"nodes": [node("n1", "a.py"), node("n2", "b.py"), node("n3", "d.py")],
             "links": [{"source": "n1", "target": "n2", "relation": "calls", "confidence": "EXTRACTED"},
                       {"source": "n1", "target": "n3", "relation": "indirect_call", "confidence": "INFERRED"},
                       {"source": "n1", "target": "n2", "relation": "contains", "confidence": "EXTRACTED"}]}
    edges, covered = graphify.edges_from(idx, graph)
    stats = graphify.merge(idx, edges, covered)
    assert stats == {"edges": 2, "upgraded": 1, "dropped_ambiguous": 1, "added": 1}
    got = {(e["src"], e["dst"]): e["conf"] for e in idx["edges"]}
    assert got[("a.py#run", "b.py#go")] == "structural" and ("a.py#run", "c.py#go") not in got
    assert got[("a.py#run", "d.py#extra")] == "ambiguous" and got[("a.py#run", "table:t")] == "structural"


def test_graphify_missing_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(graphify.shutil, "which", lambda n: None)
    d = tmp_path / "py_app"
    shutil.copytree(FIX / "py_app", d)
    idx = indexer.index_repo(d, "py_app", graphify=True)
    assert "pip install graphifyy" in idx["stats"]["graphify_message"]


def test_common_method_names_do_not_resolve_by_name(tmp_path):
    d = tmp_path / "r"; d.mkdir()
    (d / "a.py").write_text("class Jar:\n    def items(self):\n        return 1\n    def special(self):\n        return 2\n")
    (d / "b.py").write_text("def f(x):\n    return x.items()\n\ndef g(x):\n    return x.special()\n")
    idx = indexer.index_repo(d, "r", write=False)
    dst = {(e["src"], e["dst"]) for e in idx["edges"]}
    assert ("b.py#f", "a.py#Jar.items") not in dst and ("b.py#g", "a.py#Jar.special") in dst
