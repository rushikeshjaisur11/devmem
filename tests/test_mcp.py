"""`devmem mcp` over a real stdio pipe, and the usage log that feeds `devmem report`."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from devmem_app.indexer import index_repo

FIX = Path(__file__).parent.parent / "fixtures"
SCRIPTS = Path(__file__).parent.parent / "plugins/devmem/skills/devmem/scripts"


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


def test_plugin_server_runs_from_bundled_wheel_and_pyyaml_alone(tmp_path):
    """No site-packages at all (-S): the server imports the vendored wheel and pure-Python PyYAML; recall needs yaml."""
    repo = _repo(tmp_path)
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "devmem_find", "arguments": {"query": "retry"}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "devmem_recall", "arguments": {"task": "retry the user loader"}}}]
    p = subprocess.run([sys.executable, "-S", str(SCRIPTS / "mcp_server.py")], cwd=repo, input="".join(json.dumps(m) + "\n" for m in msgs),
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    out = {r["id"]: r["result"] for r in map(json.loads, p.stdout.splitlines())}
    assert "app/retry.py:5" in out[1]["content"][0]["text"] and not out[2]["isError"], p.stderr


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_plugin_node_launcher_finds_python_and_passes_stdio_through(tmp_path):
    """What Claude Code / Gemini CLI run: `node mcp_launch.mjs` (their configs cannot choose python3 vs py per OS)."""
    repo = _repo(tmp_path)
    node, env = shutil.which("node"), {**os.environ, "DEVMEM_PYTHON": sys.executable}
    p = subprocess.run([node, str(SCRIPTS / "mcp_launch.mjs")], cwd=repo, input=json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/list"}) + "\n",
                       env=env, capture_output=True, text=True, encoding="utf-8", timeout=90)
    assert {"devmem_find", "devmem_review"} <= {t["name"] for t in json.loads(p.stdout)["result"]["tools"]}, p.stderr
    bad = subprocess.run([node, str(SCRIPTS / "mcp_launch.mjs")], cwd=repo, input="", capture_output=True, text=True,
                         env={**env, "DEVMEM_PYTHON": str(tmp_path / "nope.exe"), "PATH": ""}, timeout=60)
    assert bad.returncode == 1 and "no Python 3.10+ found" in bad.stderr


def test_doctor_reports_each_layer(tmp_path, monkeypatch, capsys):
    from devmem.cli import main
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    assert main(["doctor"]) == 1                                           # not published: no AGENTS.md block
    out = capsys.readouterr().out
    assert "ok    MCP server answers" in out and "FAIL  AGENTS.md" in out and "fix: publish the repo" in out
    (repo / "AGENTS.md").write_text("<!-- devmem:start -->\nx\n<!-- devmem:end -->\n", encoding="utf-8")
    (repo / ".cursor").mkdir()
    (repo / ".cursor/mcp.json").write_text('{"mcpServers": {"devmem": {"command": "definitely-not-a-command"}}}', encoding="utf-8")
    assert main(["doctor"]) == 1 and "cursor: .cursor/mcp.json starts `definitely-not-a-command`" in capsys.readouterr().out


def test_connect_registers_every_agent_and_keeps_existing_config(tmp_path, monkeypatch, capsys):
    from devmem.cli import main
    (tmp_path / ".vscode").mkdir()
    (tmp_path / ".vscode/mcp.json").write_text('{"servers": {"github": {"type": "http", "url": "u"}}}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert main(["connect"]) == 0
    out = capsys.readouterr().out
    assert "windsurf" in out and "manual" in out and "no .devmem/index.json" in out
    vs = json.loads((tmp_path / ".vscode/mcp.json").read_text(encoding="utf-8"))["servers"]
    assert set(vs) == {"github", "devmem"} and vs["devmem"] == {"type": "stdio", "command": "devmem", "args": ["mcp"]}
    assert '[mcp_servers.devmem]' in (tmp_path / ".codex/config.toml").read_text(encoding="utf-8")
    assert main(["connect", "cursor", "--local"]) == 0
    assert json.loads((tmp_path / ".cursor/mcp.json").read_text(encoding="utf-8"))["mcpServers"]["devmem"]["args"] == ["-m", "devmem.cli", "mcp"]
    assert main(["connect", "notanagent"]) == 2
