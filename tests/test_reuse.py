"""Cross-repo reuse: catalog, search, fit and the CLI/API surfaces."""
import json
import shutil
import subprocess
from pathlib import Path

from devmem import reuse as core
from devmem.index import Index
from devmem_app import reuse as app_reuse
from devmem_app.indexer import index_repo

FIX = Path(__file__).parent.parent / "fixtures"


def _repos(tmp_path):
    out = {}
    for fx in ("conv_app", "py_app"):
        d = tmp_path / fx
        shutil.copytree(FIX / fx, d)
        index_repo(d, fx)
        out[fx] = d
    return out


def test_live_reuse_finds_retry_in_other_repo(tmp_path):
    repos = _repos(tmp_path)
    hits = app_reuse.live("retry with backoff", "py_app", {k: str(v) for k, v in repos.items()} | {"py_app": str(repos["py_app"])})
    assert hits, "expected a hit from conv_app"
    e = hits[0]["entry"]
    assert e["repo"] == "conv_app" and "retry" in e["name"].lower() and e["code"]
    assert hits[0]["fit"]["effort"] in ("low", "medium", "high") and hits[0]["prompt"].startswith("Reuse `")


def test_fit_flags_clash_and_never_suggests_own_repo(tmp_path):
    repos = _repos(tmp_path)
    ix = Index.load(repos["py_app"])
    entries = app_reuse.build_entries(Index.load(repos["conv_app"]))
    assert all(e["repo"] == "conv_app" for e in entries)
    # py_app already defines a retry helper: same-name copy from conv_app must warn
    hits = core.reuse(ix, "retry", entries)
    assert hits and any("already exists" in n or "similar" in n for n in hits[0]["fit"]["notes"])
    assert core.reuse(Index.load(repos["conv_app"]), "retry", entries) == []   # own repo's entries are excluded


def test_publish_writes_catalog_and_agent_cli_uses_it(tmp_path, capsys):
    from devmem_app.publisher import publish
    from devmem.cli import main
    repos = _repos(tmp_path)
    peers = {k: str(v) for k, v in repos.items()}
    publish(repos["py_app"], index_repo(repos["py_app"], "py_app"), peers)
    cat = json.loads((repos["py_app"] / core.CATALOG).read_text())
    assert cat and all(e["repo"] == "conv_app" for e in cat)
    import os
    cwd = os.getcwd(); os.chdir(repos["py_app"])
    try:
        assert main(["reuse", "retry with backoff"]) == 0
        out = capsys.readouterr().out
        assert "conv_app:" in out and "effort to reuse" in out
        assert main(["recall", "retry with backoff"]) == 0
        assert "ALSO SOLVED IN OTHER REPOS" in capsys.readouterr().out
    finally:
        os.chdir(cwd)


def test_search_ranks_name_over_noise():
    es = [{"repo": "a", "name": "send_email", "sig": "", "doc": "", "file": "m.py", "line": 1},
          {"repo": "a", "name": "retry_with_backoff", "sig": "", "doc": "call again on failure", "file": "n.py", "line": 1}]
    assert core.search(es, "retry on failure")[0][1]["name"] == "retry_with_backoff"
    assert core.search(es, "zzzz") == []
