# devmem

Cross-repo code knowledge for coding agents (Devin primary, Copilot). Plan: the "devmem — Full Application Plan" doc.

## Status (build steps 1-18)
- [x] 1. Core formats (`devmem.schema`), `.devmemignore`, fixture repos + golden tests
- [x] 2. Indexer: Python (stdlib `ast`), SQL + Spark table lineage (sqlglot), incremental by content hash
- [x] 3. Graph: call/test/table edges with confidence; impact (hop 1 required, hop 2 contract check, utility cap, tests separate)
- [x] 4. Agent CLI: `find`, `show`, `outline`, `impact`, `helpers` (stdlib + PyYAML only, no network)
- [x] 5. PageRank repo map + knowledge publisher (`devmem-app publish`): map/overview/module cards, AGENTS.md + Copilot blocks, freshness + budgets
- [x] 6. `devmem diffcheck`: size, out-of-scope, format-only, new deps, deleted/weakened tests, public API changes, duplicate helpers; `devmem-allow:` escape hatch; pre-commit + GitHub/Azure CI templates in `.devmem/ci/`
- [x] 7. Verification: test map (`tests.md`), `devmem tests-for`, `devmem check` (compact lint/types/affected-test summary, known-flaky aware), command detection + `devmem-app verify`, `devmem-app flaky`
- [x] 8. Hygiene: `devmem-app hygiene` runs ruff, vulture (graph-corroborated), radon, deptry, bandit, sqlfluff + token-level clone detection (`devmem-app clones` across repos) -> `hygiene.md`, trend history; `devmem hygiene <file>`; diffcheck `lint_fixes_outside_changed_lines`
- [x] 9. Conventions + helper catalog: detects logging, config, errors, Spark session, DB access, retries, tests; golden example + excerpt per pattern -> `conventions.md`; `helpers.md` (also feeds `devmem helpers`)
- [x] 10. Lineage + history: SQL/PySpark column lineage (sqlglot, schema snapshots via `devmem-app schema import`), DataFrame heuristic, `devmem impact <table.column>`, cross-repo `devmem-app impact-column`, `lineage.md`; git hotspots, co-change pairs (feed impact + diffcheck scope), no-touch zones + diffcheck `no_touch`
- [x] 11. Memory: lessons (`devmem remember`/`recall`/`feedback`, one file each, dedupe, usefulness-weighted ranking), rules (files + globs), hub sync/share (`devmem-app memory ...`), proposed rules from repeated review comments and lessons from recurring `check` failures, cross-repo pointers (`cross-repo.md`)
- [x] 12. Web UI: `devmem-app ui` serves a React/Cytoscape app (repos, explorer, impact, graph, lineage, hygiene, memory, share, metrics) from the FastAPI backend; source in `ui/` (`npm install && npm run build` rebuilds `ui_dist`)
- [x] 13. Chat: local BM25 retrieval over symbols, files, tables, lessons and rules across repos (`devmem-app ask`, `/api/chat`, Chat page); optional Vertex answer (`DEVMEM_VERTEX_PROJECT`, `pip install google-genai`; only retrieved snippets are sent, citations validated) and optional embedding rerank (`DEVMEM_VERTEX_EMBED_MODEL`)
- [x] 14. Metrics loop: `devmem report` writes a per-PR record (diff size, diffcheck result, agent-reported tokens) to `.devmem/metrics/runs/`; `devmem-app metrics show|ingest-devin` and the Metrics page compare agent commits before vs after devmem was added (from git history), summarize PR reports, and read an exported Devin sessions JSON (offline)
- [x] 15. JS/TS: tree-sitter parser (`pip install devmem-app[js]`) for functions, classes, methods, arrow consts, ES imports/require, jest/vitest tests as symbols; import-resolved call/test edges; `tests-for`/`check` run the right JS test files (vitest, jest or `npm test`), eslint and `tsc --noEmit`; eslint and knip in hygiene
- [x] 16. SCIP: `devmem-app index <repo> --scip` (needs Node; runs scip-python / scip-typescript) or `--scip-file x.scip`; precise edges upgrade structural ones and replace ambiguous guesses; cached per file hash in `.devmem/scip-edges.json`
- [x] 17. Graphify cross-check (`devmem-app index --graphify`), tightened name resolution (edge precision 0.81 -> 0.92 on psf/requests; see docs/ACCURACY.md), `scripts/accuracy.py`
- [x] 18. Plugin for Devin (and Claude Code / Copilot / Cursor): `plugins/devmem` with three skills and a bundled wheel; install steps in docs/INSTALL.md
- [ ] Not yet: SQLite/FTS5 store (index is JSON)

## Installation

### Requirements
- Python 3.10+ and git
- Optional: Node.js 18+ (for `--scip`, and eslint/knip/`tsc` checks on JS/TS repos)
- Optional: Graphify (`pip install graphifyy`) for `--graphify`

### 1. Get the code and install
    git clone https://github.com/rushikeshjaisur11/devmem.git
    cd devmem
    python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
    pip install -e packages/devmem -e "packages/devmem-app[all,test]"

Extras for `devmem-app`:

| Extra | Adds |
|---|---|
| `hygiene` | ruff, vulture, radon, deptry, bandit, sqlfluff (used by `devmem-app hygiene`) |
| `js` | tree-sitter parsers for JS/TS |
| `all` | `hygiene` + `js` |
| `test` | pytest, httpx |

Using uv instead: `uv sync` (add `--all-extras` for the optional tools).

The agent-side `devmem` CLI only needs PyYAML and makes no network calls, so it can be installed alone on an agent
machine: `pip install -e packages/devmem`.

### 2. Verify
    devmem --help && devmem-app --help
    pytest            # 92 tests; hygiene/JS tests need the `all` extra

### 3. Onboard a repo
    devmem-app add /path/to/repo      # onboard + index -> <repo>/.devmem/index.json
    devmem-app publish /path/to/repo --commit

### 4. Run the web UI
    devmem-app ui                      # http://127.0.0.1:8765, opens your browser (--port N, --no-open)

The built UI ships inside the package, so Node is not needed to use it. Pages: Repos, Explorer, Impact, Graph, Lineage,
Hygiene, Memory, Metrics, Share, Chat. Light/dark follows your OS; the sidebar button overrides it.

- **Onboard** a repo from a local path or a git URL (https, ssh or `git@host:path`; cloned into `~/.devmem/clones/<name>` with your
  machine's git credentials, never prompting). Switch its branch from the Repos table; devmem checks it out and re-indexes, and refuses
  if tracked files have uncommitted changes.
- **Graph**: overview of modules, calls and shared tables; search any symbol to see who calls it, what it calls and its tests. Filter by
  node/edge kind or repo, highlight by name, pick a layout, zoom, and export a PNG.

To change the UI (React + Vite, source in `ui/`):

    cd ui && npm ci
    devmem-app ui --no-open            # terminal 1: API on :8765
    npm run dev                        # terminal 2: hot-reload UI on :5173, proxies /api to :8765
    npm run build                      # writes packages/devmem-app/src/devmem_app/ui_dist (commit it)

### 5. Improve accuracy (optional)
Structural edges alone are about 92% precise (see [docs/ACCURACY.md](docs/ACCURACY.md)). Layer in independent sources:

    pip install graphifyy                                 # tree-sitter call graph, no LLM or key
    devmem-app index /path/to/repo --graphify             # drops unconfirmed ambiguous edges, adds missed ones
    devmem-app index /path/to/repo --scip                 # type-resolved edges (needs Node; downloads scip-python / scip-typescript via npx)
    python scripts/accuracy.py <repo> <index.scip> <graphify-out/graph.json>   # measure on your own repo

The flags combine, and results are cached per file hash so re-indexing stays incremental.

To wire it into Devin, Claude Code, Copilot or Cursor see [docs/INSTALL.md](docs/INSTALL.md).

## Use
    cd /path/to/repo && devmem find "retry logic" && devmem impact retry

## Notes
- Python >= 3.10 (plan said 3.11+; relaxed so it runs on the current machine). CLI uses argparse instead of Typer to keep the pip install tiny.
- Index is JSON for now; SQLite/FTS5 arrives with the hub (step 2 extension).
