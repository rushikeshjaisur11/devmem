# Installing devmem for your agents

devmem has two halves:

1. **The app (your machine)**: `devmem-app` indexes repos, publishes the `.devmem/` folder, `AGENTS.md` block and CI
   templates into each repo, and runs the web UI. Agents never need it.
2. **The agent side**: the small `devmem` CLI (PyYAML is its only dependency, no network at runtime), usable four ways:
   as a CLI the agent runs, as an MCP server, through the Claude Code / Devin plugin, or as a Gemini CLI extension.

After setting up, run `devmem doctor` in the repo (or **Agents -> Check setup** in the UI). It checks Python, the CLI on
PATH, the index, the `AGENTS.md` block, a real MCP round trip and every agent config, and prints the fix for anything wrong.

## One-time per repo (your machine)

```
pip install -e packages/devmem -e packages/devmem-app        # everything accuracy depends on is a hard dependency; also needs Node.js 18+
devmem-app add <path-to-repo-or-git-url>
devmem-app publish <repo> --commit                           # then push the devmem/knowledge-sync branch and merge it
```

Keep `devmem-app ui` running and it re-indexes repos whose HEAD moved; or run `devmem-app watch`.

## The CLI (any agent)

```
pip install "devmem @ git+https://github.com/rushikeshjaisur11/devmem.git#subdirectory=packages/devmem"
```

The PyPI name `devmem` belongs to another project; do not `pip install devmem`. Offline, install the bundled wheel:
`pip install plugins/devmem/skills/devmem/vendor/devmem-*.whl`. Agents that read `AGENTS.md` (Codex, Cursor, Copilot,
OpenCode, Devin, Claude Code without a CLAUDE.md, Aider via `read:`) then run the commands it lists.

## MCP (Claude Code, Cursor, VS Code / Copilot, Codex, Gemini CLI, Kiro, OpenCode, Windsurf)

In the repo:

```
devmem connect                       # every agent; or name them: devmem connect claude cursor codex
devmem connect --local               # use this Python's absolute path (no PATH needed; machine-specific, keep out of git)
```

| Agent | What `connect` writes | Notes |
|---|---|---|
| Claude Code | `.mcp.json`, and `@AGENTS.md` in `CLAUDE.md` | Claude asks you to approve project servers once. It reads AGENTS.md only when there is no CLAUDE.md, hence the import. |
| Cursor | `.cursor/mcp.json` | |
| VS Code / Copilot | `.vscode/mcp.json` (`servers`, `type: stdio`) | |
| Codex | `.codex/config.toml` `[mcp_servers.devmem]` | Codex loads project config for trusted projects only. |
| Gemini CLI | `.gemini/settings.json` (server + `context.fileName` incl. AGENTS.md) | |
| Kiro | `.kiro/settings/mcp.json` | |
| OpenCode | `opencode.json` (`mcp`, `type: local`) | |
| Aider | `.aider.conf.yml` `read: AGENTS.md` | No MCP; uses the CLI. |
| Windsurf | nothing (user-level config only) | `connect` prints the snippet for `~/.codeium/windsurf/mcp_config.json`. |

Existing settings in those files are kept. Verified on 2026-10-31: Claude Code 2.1.316 called the tools through the generated
`.mcp.json`, and Codex listed the server from `.codex/config.toml` in a trusted project.

## Claude Code plugin

```
/plugin marketplace add rushikeshjaisur11/devmem
/plugin install devmem@devmem
```

The plugin brings the skills and starts the MCP server itself: `node mcp_launch.mjs` finds a Python 3.10+ (`DEVMEM_PYTHON`, then
`py -3` / `python` / `python3` on Windows, `python3` / `python` elsewhere) and runs the server from the bundled wheel and a
bundled pure-Python PyYAML, so nothing needs installing. It needs Node.js and Python on the machine. Check it with
`claude mcp list` (the server shows as `plugin:devmem:devmem ... Connected`).

## Gemini CLI extension

```
gemini extensions install ./plugins/devmem        # from a clone of this repo
```

Same launcher as the Claude plugin; the extension's context file is `AGENTS.md`.

## Devin (web)

1. app.devin.ai -> **Customize** -> **Plugins** -> **Add plugin** -> **From repository**.
2. Repository: `<your-github-user>/devmem`, subdirectory `plugins/devmem`. Scope: Personal, or Organization to cover
   the team.
3. Start a session on a repo that has `.devmem/`. The skills appear as `/devmem:devmem`, `/devmem:devmem-impact`,
   `/devmem:devmem-remember`, and `AGENTS.md` in the repo (written by `publish`) makes Devin use them by default.

The skill installs the CLI itself (`scripts/bootstrap.sh`): bundled wheel first, with pip, pip `--user`, uv or pipx, and
without dependencies when there is no network. If the repo has no `.devmem/`, the agent works normally.

## Skills for other tools

The skills are plain `SKILL.md` files; copy `plugins/devmem/skills/` into `.github/skills/` (Copilot) or your tool's skills folder.

## Accuracy tools

SCIP (type-resolved edges via `npx @sourcegraph/scip-python` / `scip-typescript`), Graphify (`graphifyy`, an independent tree-sitter call
graph) and tree-sitter (JS/TS parsing) are not optional: `devmem-app add` / `index` / `publish` run them automatically whenever files
changed (cached per file hash) and stop with an error if one is missing or fails. Node.js 18+ and network access are needed the first
time (npx downloads the SCIP indexers). `--fast` or `DEVMEM_ACCURACY=fast` skips them for CI/dev; the index records it and the UI shows
"reduced accuracy". Go, Java, Kotlin, C# and Rust are parsed with tree-sitter; `scip-go` / `scip-java` run when installed. See
`docs/ACCURACY.md` for measurements.
