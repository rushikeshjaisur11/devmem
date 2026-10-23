import os
import shutil
import subprocess
from pathlib import Path

import pytest
from devmem_app import indexer, scip

FIX = Path(__file__).parent.parent / "fixtures"


def _v(n: int) -> bytes:
    out = b""
    while True:
        b = n & 0x7F; n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def _ld(f: int, payload: bytes) -> bytes:
    return _v(f << 3 | 2) + _v(len(payload)) + payload


def occ(line: int, sym: str, roles: int = 0) -> bytes:
    rng = b"".join(_v(x) for x in (line, 0, 4))
    return _ld(2, _ld(1, rng) + _ld(2, sym.encode()) + (_v(3 << 3) + _v(roles) if roles else b""))


def doc(path: str, *occs: bytes) -> bytes:
    return _ld(2, _ld(1, path.encode()) + b"".join(occs))


def test_decoder_matches_real_scip_python_output(tmp_path):
    d = tmp_path / "py_app"
    shutil.copytree(FIX / "py_app", d)
    idx = indexer.index_repo(d, "py_app", write=False)
    edges, covered = scip.edges_from(idx, scip.parse((FIX / "scip/py_app.scip").read_bytes()))
    assert "app/client.py" in covered
    got = {(e["src"], e["dst"], e["type"]) for e in edges}
    assert ("app/service.py#load_user", "app/client.py#ApiClient.get", "calls") in got
    assert ("tests/test_service.py#test_load_user", "app/service.py#load_user", "tests") in got
    assert all(e["conf"] == "precise" for e in edges) and len(edges) == 8


def test_index_with_scip_file_upgrades_edges_and_caches(tmp_path):
    d = tmp_path / "py_app"
    shutil.copytree(FIX / "py_app", d)
    idx = indexer.index_repo(d, "py_app", scip=[FIX / "scip/py_app.scip"])
    assert idx["stats"]["scip"]["upgraded"] == 8
    assert {e["conf"] for e in idx["edges"] if e["type"] == "calls"} == {"precise"}
    assert (d / ".devmem/scip-edges.json").exists()
    # re-index without SCIP: cached edges still apply while files are unchanged
    again = indexer.index_repo(d, "py_app")
    assert again["stats"]["scip"]["stale_files"] == 0 and {e["conf"] for e in again["edges"] if e["type"] == "calls"} == {"precise"}
    # edit a covered file: its SCIP edges are no longer trusted, structural ones remain
    p = d / "app/service.py"
    p.write_text(p.read_text() + "\n# touched\n")
    third = indexer.index_repo(d, "py_app")
    assert third["stats"]["scip"]["stale_files"] == 1
    assert any(e["conf"] == "structural" and e["src"].startswith("app/service.py") for e in third["edges"])


def test_ambiguous_edges_resolved_by_scip(tmp_path):
    idx = {"symbols": [
        {"id": "a.py#run", "name": "run", "qualname": "run", "kind": "function", "file": "a.py", "line": 1, "end_line": 5, "is_test": False},
        {"id": "b.py#go", "name": "go", "qualname": "go", "kind": "function", "file": "b.py", "line": 1, "end_line": 2, "is_test": False},
        {"id": "c.py#go", "name": "go", "qualname": "go", "kind": "function", "file": "c.py", "line": 1, "end_line": 2, "is_test": False}],
        "edges": [{"src": "a.py#run", "dst": "b.py#go", "type": "calls", "conf": "ambiguous"},
                  {"src": "a.py#run", "dst": "c.py#go", "type": "calls", "conf": "ambiguous"}], "files": {}}
    data = doc("a.py", occ(3, "pkg/b.py/go().")) + doc("b.py", occ(0, "pkg/b.py/go().", 1)) + doc("c.py", occ(0, "pkg/c.py/go().", 1)) \
        + doc("a.py", occ(0, "pkg/a.py/run().", 1))
    edges, covered = scip.edges_from(idx, scip.parse(data))
    assert [(e["src"], e["dst"]) for e in edges] == [("a.py#run", "b.py#go")]
    stats = scip.merge(idx, edges, covered)
    assert stats == {"edges": 1, "upgraded": 1, "dropped_ambiguous": 1}
    assert [(e["dst"], e["conf"]) for e in idx["edges"]] == [("b.py#go", "precise")]


def test_run_indexer_without_node(monkeypatch, tmp_path):
    monkeypatch.setattr(scip.shutil, "which", lambda n: None)
    files, msgs = scip.run_indexer(tmp_path, {"files": {"a.py": {"lang": "python"}}})
    assert files == [] and "npx not found" in msgs[0]


def test_run_indexer_patches_windows_scip_python_path_regex(monkeypatch, tmp_path):
    if os.name != "nt":
        pytest.skip("SCIP Python's path separator bug only affects Windows")
    monkeypatch.setenv("NODE_OPTIONS", "--max-old-space-size=1024")
    monkeypatch.setattr(scip.shutil, "which", lambda n: "npx")
    captured = {}

    def run(args, **kwargs):
        captured["env"] = kwargs["env"]
        options = kwargs["env"]["NODE_OPTIONS"]
        preload = Path(options.split('--require="', 1)[1].split('"', 1)[0])
        captured["preload"] = preload
        captured["source"] = preload.read_text(encoding="utf-8")
        Path(args[args.index("--output") + 1]).write_bytes(b"x" * 61)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(scip.subprocess, "run", run)

    files, messages = scip.run_indexer(tmp_path, {"files": {"a.py": {"lang": "python"}}})

    assert files and messages == []
    assert captured["env"]["NODE_OPTIONS"].startswith("--max-old-space-size=1024 ")
    assert "process.argv[1]" in captured["source"] and "path.sep += path.sep" in captured["source"]
    assert not captured["preload"].exists()
