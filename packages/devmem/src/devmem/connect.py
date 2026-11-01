"""`devmem connect`: register the devmem MCP server (and the shared AGENTS.md instructions) with coding agents, per repo.

Writes each agent's project-level config, merging into what is there (other servers and settings are kept). Agents that read
AGENTS.md natively (Codex, Cursor, Copilot, OpenCode, Devin, ...) need no instruction file; Claude Code only reads AGENTS.md when
there is no CLAUDE.md, so CLAUDE.md gets an `@AGENTS.md` import; Gemini CLI is told to load AGENTS.md; Aider gets `read: AGENTS.md`.
Paths and formats follow each tool's docs as of 2026-10; agents with only a user-level config (Windsurf) get a snippet to paste."""
from __future__ import annotations

import json
from pathlib import Path

SERVER = "devmem"

# agent -> (config file, top-level key, entry builder). The entry builder gets (command, args).
JSON_AGENTS = {
    "claude": (".mcp.json", "mcpServers", lambda c, a: {"command": c, "args": a}),
    "cursor": (".cursor/mcp.json", "mcpServers", lambda c, a: {"command": c, "args": a}),
    "vscode": (".vscode/mcp.json", "servers", lambda c, a: {"type": "stdio", "command": c, "args": a}),        # Copilot in VS Code
    "gemini": (".gemini/settings.json", "mcpServers", lambda c, a: {"command": c, "args": a}),
    "kiro": (".kiro/settings/mcp.json", "mcpServers", lambda c, a: {"command": c, "args": a}),
    "opencode": ("opencode.json", "mcp", lambda c, a: {"type": "local", "command": [c, *a], "enabled": True}),
}
USER_ONLY = {"windsurf": "~/.codeium/windsurf/mcp_config.json"}
AGENTS = [*JSON_AGENTS, "codex", "aider", *USER_ONLY]


def _merge_json(p: Path, update) -> bool:
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() and p.read_text(encoding="utf-8").strip() else {}
    before = json.dumps(data, sort_keys=True)
    update(data)
    if json.dumps(data, sort_keys=True) == before:
        return False
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return True


def _append_once(p: Path, marker: str, text: str) -> bool:
    cur = p.read_text(encoding="utf-8") if p.exists() else ""
    if marker in cur:
        return False
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(cur + ("\n" if cur and not cur.endswith("\n") else "") + ("\n" if cur else "") + text, encoding="utf-8")
    return True


def connect(root: Path, agents: list[str], command: str = "devmem", args: list[str] | None = None) -> list[dict]:
    """[{agent, file, changed, note}] for each requested agent. Raises ValueError for an unknown agent name."""
    root, args, out = Path(root), args or ["mcp"], []
    bad = [a for a in agents if a not in AGENTS]
    if bad:
        raise ValueError(f"unknown agent(s): {', '.join(bad)}; known: {', '.join(AGENTS)}")
    for agent in agents:
        if agent in JSON_AGENTS:
            rel, key, entry = JSON_AGENTS[agent]
            changed = _merge_json(root / rel, lambda d: d.setdefault(key, {}).__setitem__(SERVER, entry(command, args)))
            out.append({"agent": agent, "file": rel, "changed": changed, "note": "MCP server"})
        if agent == "claude":   # only reads AGENTS.md without a CLAUDE.md (2.1.277+); the import works with both and older versions
            out.append({"agent": agent, "file": "CLAUDE.md", "changed": _append_once(root / "CLAUDE.md", "@AGENTS.md", "@AGENTS.md\n"),
                        "note": "imports AGENTS.md"})
        elif agent == "gemini":
            def ctx(d):
                names = d.setdefault("context", {}).get("fileName") or ["GEMINI.md"]
                names = [names] if isinstance(names, str) else names
                d["context"]["fileName"] = names if "AGENTS.md" in names else ["AGENTS.md", *names]
            out.append({"agent": agent, "file": ".gemini/settings.json", "changed": _merge_json(root / ".gemini/settings.json", ctx),
                        "note": "loads AGENTS.md"})
        elif agent == "codex":   # trusted projects only; Codex reads AGENTS.md itself
            table = f'[mcp_servers.{SERVER}]\ncommand = {json.dumps(command)}\nargs = {json.dumps(args)}\n'
            out.append({"agent": agent, "file": ".codex/config.toml", "changed": _append_once(root / ".codex/config.toml", f"[mcp_servers.{SERVER}]", table),
                        "note": "MCP server (Codex loads project config for trusted projects)"})
        elif agent == "aider":   # no MCP: the agent uses the devmem CLI, guided by AGENTS.md
            out.append({"agent": agent, "file": ".aider.conf.yml", "changed": _append_once(root / ".aider.conf.yml", "AGENTS.md", "read: AGENTS.md\n"),
                        "note": "reads AGENTS.md; uses the devmem CLI (no MCP)"})
        elif agent in USER_ONLY:
            snippet = json.dumps({"mcpServers": {SERVER: {"command": command, "args": args}}}, indent=2)
            out.append({"agent": agent, "file": USER_ONLY[agent], "changed": False, "note": f"user-level only: add this to {USER_ONLY[agent]}\n{snippet}"})
    return out


def status(root: Path) -> dict[str, bool]:
    """Which agents this repo is already connected for (their project config names the devmem server)."""
    root, out = Path(root), {}
    for agent, (rel, key, _) in JSON_AGENTS.items():
        p = root / rel
        try:
            out[agent] = SERVER in (json.loads(p.read_text(encoding="utf-8")).get(key) or {}) if p.exists() else False
        except ValueError:
            out[agent] = False
    out["codex"] = f"[mcp_servers.{SERVER}]" in ((root / ".codex/config.toml").read_text(encoding="utf-8") if (root / ".codex/config.toml").exists() else "")
    out["aider"] = "AGENTS.md" in ((root / ".aider.conf.yml").read_text(encoding="utf-8") if (root / ".aider.conf.yml").exists() else "")
    return out


if __name__ == "__main__":   # self-check: merging keeps what was there, a second run changes nothing
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        r = Path(d)
        (r / ".cursor").mkdir()
        (r / ".cursor/mcp.json").write_text('{"mcpServers": {"other": {"command": "x"}}}', encoding="utf-8")
        (r / "CLAUDE.md").write_text("# Mine\n", encoding="utf-8")
        first = connect(r, AGENTS)
        assert all(x["changed"] for x in first if x["agent"] not in USER_ONLY)
        assert set(json.loads((r / ".cursor/mcp.json").read_text())["mcpServers"]) == {"other", "devmem"}
        assert (r / "CLAUDE.md").read_text().startswith("# Mine\n") and "@AGENTS.md" in (r / "CLAUDE.md").read_text()
        assert json.loads((r / ".gemini/settings.json").read_text())["context"]["fileName"] == ["AGENTS.md", "GEMINI.md"]
        assert json.loads((r / "opencode.json").read_text())["mcp"]["devmem"]["command"] == ["devmem", "mcp"]
        assert not any(x["changed"] for x in connect(r, AGENTS))
        print("ok")
