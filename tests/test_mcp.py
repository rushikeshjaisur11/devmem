"""`devmem mcp` over a real stdio pipe, and the usage log that feeds `devmem report`."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

from devmem_app.indexer import index_repo

FIX = Path(__file__).parent.parent / "fixtures"


def _repo(tmp_path):
    d = tmp_path / "py_app"
    shutil.copytree(FIX / "py_app", d)
    index_repo(d)
    return d


def test_mcp_lists_and_calls_tools_without_a_session(tmp_path):
    repo = _repo(tmp_path)
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},                       # stateless: no initialize first
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "devmem_find", "arguments": {"query": "retry"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 3, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},   # older clients still connect
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "devmem_show", "arguments": {"symbol": "nope"}}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "devmem_impact", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 6, "method": "resources/list"},
    ]
    p = subprocess.run([sys.executable, "-m", "devmem.cli", "mcp"], cwd=repo, input="\n".join(json.dumps(m) for m in msgs) + "\n",
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    out = {r["id"]: r for r in map(json.loads, p.stdout.splitlines())}
    assert set(out) == {1, 2, 3, 4, 5, 6}                                           # the notification got no reply
    names = {t["name"] for t in out[1]["result"]["tools"]}
    assert {"devmem_find", "devmem_show", "devmem_impact", "devmem_recall", "devmem_docs"} <= names
    assert all(t["inputSchema"]["type"] == "object" for t in out[1]["result"]["tools"])
    assert "app/retry.py:5" in out[2]["result"]["content"][0]["text"] and not out[2]["result"]["isError"]
    assert out[3]["result"]["protocolVersion"] == "2025-06-18"
    assert out[4]["result"]["isError"] and "devmem find" in out[4]["result"]["content"][0]["text"]
    assert out[5]["result"]["isError"]                                              # missing argument, reported not crashed
    assert out[6]["error"]["code"] == -32601
    log = [json.loads(ln) for ln in (repo / ".devmem/usage.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["via"] for r in log] == ["mcp", "mcp"] and log[0]["cmd"] == "find" and log[0]["file_tokens"] > 0
    assert "usage.jsonl" in (repo / ".devmem/.gitignore").read_text(encoding="utf-8")


def test_report_folds_in_devmem_usage_since_the_last_record(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qam", "x", "--allow-empty"], cwd=repo, check=True)
    monkeypatch.chdir(repo)
    from devmem.cli import main
    assert main(["show", "retry"]) == 0 and main(["impact", "retry"]) == 0
    capsys.readouterr()
    assert main(["report", "--task", "t"]) == 0
    assert "devmem queries this run: 2" in capsys.readouterr().out
    rec = json.loads(next((repo / ".devmem/metrics/runs").glob("*.json")).read_text(encoding="utf-8"))
    assert rec["devmem"]["calls"] == 2 and rec["devmem"]["file_tokens"] > rec["devmem"]["out_tokens"] > 0
