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
    """Confirmed guess -> structural; unconfirmed guess from a covered file -> dropped; Graphify-only edges join when Graphify
    resolved them (EXTRACTED), its own guesses (INFERRED) only from tests, and library code never gains an edge into a test."""
    def sym(f, n, test=False):
        return {"id": f"{f}#{n}", "name": n, "qualname": n, "kind": "function", "file": f, "line": 1, "end_line": 9, "is_test": test}
    files = ("a.py", "b.py", "c.py", "d.py", "e.py", "test_x.py")
    idx = {"symbols": [sym(f, n) for f, n in (("a.py", "run"), ("b.py", "go"), ("c.py", "go"), ("d.py", "extra"), ("e.py", "solid"))]
                      + [sym("test_x.py", "test_run", True)],
           "files": {f: {} for f in files},
           "edges": [{"src": "a.py#run", "dst": "b.py#go", "type": "calls", "conf": "ambiguous"},
                     {"src": "a.py#run", "dst": "c.py#go", "type": "calls", "conf": "ambiguous"},
                     {"src": "a.py#run", "dst": "table:t", "type": "reads", "conf": "structural"}]}
    node = lambda i, f: {"id": i, "source_file": f, "source_location": "L2"}
    link = lambda a, b, conf, rel="calls": {"source": a, "target": b, "relation": rel, "confidence": conf}
    graph = {"nodes": [node("n1", "a.py"), node("n2", "b.py"), node("n3", "d.py"), node("n4", "e.py"), node("t", "test_x.py")],
             "links": [link("n1", "n2", "EXTRACTED"),
                       link("n1", "n3", "INFERRED", "indirect_call"),     # library guess: not added
                       link("n1", "n4", "EXTRACTED"),                     # resolved by Graphify: added
                       link("t", "n3", "INFERRED"),                       # guess from a test: added as a guess
                       link("n1", "t", "EXTRACTED"),                      # library -> test: never
                       link("n1", "n2", "EXTRACTED", "contains")]}
    edges, covered = graphify.edges_from(idx, graph)
    stats = graphify.merge(idx, edges, covered)
    assert stats == {"edges": 5, "upgraded": 1, "dropped_ambiguous": 1, "added": 2}
    got = {(e["src"], e["dst"]): (e["type"], e["conf"]) for e in idx["edges"]}
    assert got[("a.py#run", "b.py#go")] == ("calls", "structural") and ("a.py#run", "c.py#go") not in got
    assert ("a.py#run", "d.py#extra") not in got and ("a.py#run", "test_x.py#test_run") not in got
    assert got[("a.py#run", "e.py#solid")] == ("calls", "structural")
    assert got[("test_x.py#test_run", "d.py#extra")] == ("tests", "ambiguous")
    assert got[("a.py#run", "table:t")] == ("reads", "structural")


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


def test_graphify_edge_from_a_class_to_its_own_method_is_not_added():
    sym = lambda q, k: {"id": f"q.ts#{q}", "name": q.split(".")[-1], "qualname": q, "kind": k, "file": "q.ts", "line": 1 if k == "class" else 3,
                        "end_line": 20 if k == "class" else 5, "is_test": False}
    idx = {"symbols": [sym("Queue", "class"), sym("Queue.add", "method")], "files": {"q.ts": {}}, "edges": []}
    graph = {"nodes": [{"id": "c", "source_file": "q.ts", "source_location": "L10"}, {"id": "m", "source_file": "q.ts", "source_location": "L4"}],
             "links": [{"source": "c", "target": "m", "relation": "calls", "confidence": "EXTRACTED"}]}
    edges, covered = graphify.edges_from(idx, graph)
    assert graphify.merge(idx, edges, covered)["added"] == 0 and idx["edges"] == []
