# Installing devmem for your agents

devmem has two halves:

1. **The app (your machine)**: `devmem-app` indexes repos, publishes the `.devmem/` folder, `AGENTS.md` block and CI
   templates into each repo, and runs the web UI. Agents never need it.
2. **The plugin (the agent)**: skills that tell the agent how to use the committed `.devmem/` data through the small
   `devmem` CLI. No MCP server, no network at runtime.

## One-time per repo (your machine)

```
pip install -e packages/devmem -e packages/devmem-app        # everything accuracy depends on is a hard dependency; also needs Node.js 18+
devmem-app add <path-to-repo-or-git-url>
devmem-app publish <repo> --commit                           # then push the devmem/knowledge-sync branch and merge it
```

Refresh after big changes with the CI template in `.devmem/ci/` or by re-running `publish`.

## Devin (web)

1. app.devin.ai -> **Customize** -> **Plugins** -> **Add plugin** -> **From repository**.
2. Repository: `<your-github-user>/devmem`, subdirectory `plugins/devmem`. Scope: Personal, or Organization to cover
   the team.
3. Start a session on a repo that has `.devmem/`. The skills appear as `/devmem:devmem`, `/devmem:devmem-impact`,
   `/devmem:devmem-remember`, and `AGENTS.md` in the repo (written by `publish`) makes Devin use them by default.

The skill installs the CLI itself from the wheel bundled in the plugin (`skills/devmem/vendor/`), so it needs no PyPI
access and no egress beyond what the plugin install already used. If the repo has no `.devmem/`, the agent works
normally.

## Claude Code

```
/plugin marketplace add <your-github-user>/devmem
/plugin install devmem@devmem
```

## GitHub Copilot, Cursor, Codex, others

They read `AGENTS.md` / `.github/copilot-instructions.md`, which `devmem-app publish` writes into every repo (a short
managed block, under 300 tokens). Install the CLI where the agent runs: `pip install plugins/devmem/skills/devmem/vendor/devmem-*.whl`.
The skills are plain `SKILL.md` files; copy `plugins/devmem/skills/` into `.github/skills/` (Copilot) or your tool's skills folder.

## Accuracy tools

SCIP (type-resolved edges via `npx @sourcegraph/scip-python` / `scip-typescript`), Graphify (`graphifyy`, an independent tree-sitter call
graph) and tree-sitter (JS/TS parsing) are not optional: `devmem-app add` / `index` / `publish` run them automatically whenever files
changed (cached per file hash) and stop with an error if one is missing or fails. Node.js 18+ and network access are needed the first
time (npx downloads the SCIP indexers). `--fast` or `DEVMEM_ACCURACY=fast` skips them for CI/dev; the index records it and the UI shows
"reduced accuracy". See `docs/ACCURACY.md` for measurements.
