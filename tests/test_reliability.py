"""Answers must not be silently wrong: code that moved since indexing is found again or refused, state files are written
atomically under a lock, unreadable or newer indexes are rejected with the fix, and survived failures are recorded."""
import json
import shutil
import threading
import time
from pathlib import Path

import pytest

from devmem import cli, context
from devmem.index import Index
from devmem.schema import atomic_write
from devmem_app import indexer, prompts
from devmem_app.paths import diagnose, diagnostics, repo_lock

FIX = Path(__file__).parent.parent / "fixtures"


def _repo(tmp_path, name="py_app"):
    root = tmp_path / name
    shutil.copytree(FIX / name, root)
    indexer.index_repo(root, name, accuracy="fast")
    return root


def _run(capsys, root, *argv):
    capsys.readouterr()
    code = cli.run(list(argv), root=root)
    return code, capsys.readouterr().out


def test_show_finds_python_code_again_after_the_file_was_edited(tmp_path, capsys):
    root = _repo(tmp_path)
    ix = Index.load(root)
    s = ix.symbols["app/retry.py#retry"]
    assert ix.file_state("app/retry.py") == "fresh" and ix.read(s)[3] == ""
    f = root / "app/retry.py"
    f.write_text("# a new header comment\nimport logging\n\nlog = logging.getLogger(__name__)\n\n" + f.read_text())      # everything moves down 5 lines
    ix = Index.load(root)
    text, start, end, note = ix.read(s)
    assert ix.file_state("app/retry.py") == "changed" and start == s["line"] + 5 and end == s["end_line"] + 5
    assert text.startswith("def retry(") and "changed since it was indexed" in note and "estimate" not in note     # exact: re-parsed
    code, out = _run(capsys, root, "show", "retry")
    assert code == 0 and out.splitlines()[0].startswith("NOTE: app/retry.py changed since it was indexed: `retry` is now at lines")
    assert f"# app/retry.py:{start}-{end}" in out and "def retry(" in out and "log = logging" not in out          # not the stale line range
    code, out = _run(capsys, root, "context", "retry helper")
    assert "NOTE: app/retry.py changed since it was indexed" in out and "BODY\ndef retry(" in out
    assert f"{start:>5}  def retry(" in "\n".join(prompts.symbol_block(ix, s))                                     # prompts number the real lines


def test_renamed_or_deleted_code_is_refused_not_guessed(tmp_path, capsys):
    root = _repo(tmp_path)
    f = root / "app/retry.py"
    f.write_text(f.read_text().replace("def retry(", "def retry_call("))
    code, out = _run(capsys, root, "show", "retry")
    assert code == 1 and "retry is no longer in app/retry.py" in out and "re-index" in out and "def retry_call" not in out
    code, out = _run(capsys, root, "context", "retry helper")
    assert "NOTE: retry is no longer in app/retry.py" in out and "BODY" not in out                                # callers still shown, no wrong body
    f.unlink()
    assert Index.load(root).file_state("app/retry.py") == "missing"
    code, out = _run(capsys, root, "show", "retry")
    assert code == 1 and "app/retry.py no longer exists" in out


def test_impact_says_which_of_its_files_changed(tmp_path, capsys):
    root = _repo(tmp_path)
    _, out = _run(capsys, root, "impact", "retry")
    assert "NOTE" not in out
    (root / "app/client.py").write_text((root / "app/client.py").read_text() + "\n\ndef another_caller():\n    return retry(lambda: 1)\n")
    _, out = _run(capsys, root, "impact", "retry")
    assert "NOTE: changed since the index was built: app/client.py." in out and "not reflected above" in out


def test_other_languages_are_found_by_definition_and_block(tmp_path):
    root = _repo(tmp_path, "js_app")
    ix = Index.load(root)
    s = next(x for x in ix.symbols.values() if x["file"] == "src/retry.ts" and x["kind"] == "function")
    f = root / "src/retry.ts"
    before = ix.read(s)[0]
    f.write_text("// header\n// another line\n" + f.read_text())
    text, start, end, note = Index.load(root).read(s)
    assert start == s["line"] + 2 and text.splitlines()[0] == before.splitlines()[0] and text.rstrip().endswith("}")
    assert "found by its name; the end line is an estimate" in note


def test_unreadable_and_newer_indexes_are_rejected_with_the_fix(tmp_path, capsys):
    root = _repo(tmp_path)
    p = root / ".devmem/index.json"
    good = p.read_text()
    p.write_text(good[: len(good) // 2])                                             # a truncated file, as a crash mid-write used to leave
    assert cli.run(["find", "retry"], root=root) == 2 and "is not readable" in capsys.readouterr().err
    p.write_text(json.dumps({**json.loads(good), "version": 99}))
    assert cli.run(["find", "retry"], root=root) == 2
    assert "written by a newer devmem (format 99" in capsys.readouterr().err
    p.write_text("[]")
    with pytest.raises(FileNotFoundError, match="not a devmem index"):
        Index.load(root)


def test_atomic_write_never_leaves_a_partial_file(tmp_path):
    p = tmp_path / "state.json"
    atomic_write(p, '{"a": 1}')
    with pytest.raises(Exception):
        atomic_write(p, 5)                                                              # fails while writing
    assert p.read_text() == '{"a": 1}' and [x.name for x in tmp_path.iterdir()] == ["state.json"]     # old content intact, no temp file left
    atomic_write(p, '{"a": 2}')
    assert json.loads(p.read_text()) == {"a": 2}


def test_only_one_process_indexes_a_repo_at_a_time(tmp_path):
    root = _repo(tmp_path)
    order = []

    def hold():
        with repo_lock(root):
            order.append("first in"); time.sleep(0.5); order.append("first out")
    t = threading.Thread(target=hold); t.start(); time.sleep(0.1)
    with repo_lock(root):
        order.append("second in")
    t.join()
    assert order == ["first in", "first out", "second in"] and not (root / ".devmem/.index.lock").exists()
    (root / ".devmem/.index.lock").write_text("999999")                                # left behind by a killed process
    import os
    os.utime(root / ".devmem/.index.lock", (time.time() - 4000, time.time() - 4000))
    idx = indexer.index_repo(root, "py_app", accuracy="fast")                           # stale lock is replaced, indexing proceeds
    assert idx["symbols"] and not (root / ".devmem/.index.lock").exists()
    (root / ".devmem/.index.lock").write_text("1")
    with pytest.raises(TimeoutError, match="another devmem process"):
        with repo_lock(root, wait=0.3):
            pass


def test_survived_failures_are_recorded_and_shown(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from devmem_app import watch
    from devmem_app.server import create_app
    monkeypatch.setenv("DEVMEM_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("DEVMEM_TOKEN", raising=False)
    diagnose("docs", "requests could not be refreshed: failed: timed out")
    diagnose("docs", "requests could not be refreshed: failed: timed out")
    assert [d["area"] for d in diagnostics()] == ["docs", "docs"]
    monkeypatch.setattr(watch, "stale", lambda root: "abcdef1234")
    monkeypatch.setattr(watch, "index_repo", lambda root, name: (_ for _ in ()).throw(OSError("disk error")))
    assert watch.tick({"r": str(tmp_path)}, False, {}, log=lambda m: None) == []       # the watcher survives any failure
    assert diagnostics()[0] == {**diagnostics()[0], "area": "index", "repo": "r", "message": "re-index failed: OSError: disk error"}
    api = TestClient(create_app())
    texts = [a["text"] for a in api.get("/api/alerts").json()]
    assert texts.count("docs: requests could not be refreshed: failed: timed out") == 1 and "index: re-index failed: OSError: disk error" in texts
    assert len(api.get("/api/diagnostics").json()) == 3
