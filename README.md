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

The built UI ships inside the package, so Node is not needed to use it. Pages: Repos, Explorer, Reuse, Impact, Graph, Lineage,
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

### 5. Cross-repo reuse
Onboard the repos you want to borrow from, then ask for what you need:

    devmem-app reuse "retry a failing call with backoff" --into my-service --prompt
    # or just describe it in Chat (the UI) and it works out you want an agent prompt

Each hit shows the source, its tests, and an *effort to reuse* rating for the target repo: packages the target doesn't use yet, name
clashes, helpers to copy along, tables it touches, language mismatch, and similar code the target already has.

**How agents see it (Devin, Claude Code, Copilot):** agents only read what is committed in the repo they work in, so
`devmem-app publish <repo>` writes `.devmem/reuse-catalog.json` (the best ~150 public functions/classes of every *other* onboarded
repo, source trimmed to 60 lines, file capped at 1.5 MB). The plugin's skill then runs `devmem reuse "<need>"` and `devmem recall`
offline against it. Re-publish after the source repos change; it is a snapshot. It copies code from your other repos into this one, so
only onboard repos that are fine to share with each other.

#### Central knowledge hub (recommended for teams and Devin)
Instead of copying code from every repo into every other repo, keep everything in one git repo that agents read:

    devmem-app hub set git@github.com:acme/devmem-hub.git     # an empty repo you own; or the Knowledge hub page
    devmem-app hub push --push                                 # all onboarded repos -> catalog/, repos/<name>/, lessons/, INDEX.md

`devmem-app publish` then writes `.devmem/hub.txt` (the URL) into each repo. In a session the skill runs `devmem hub sync` (a shallow
clone into `~/.cache/devmem`, never prompting for credentials) and `devmem reuse` / `devmem hub repos` / `devmem hub show <repo>` read it.
Or skip the file and set `DEVMEM_HUB=<url>` in the agent's environment. The agent needs git read access to the hub; if it has none or is
offline, `devmem reuse` quietly falls back to the catalog committed in the repo. A local-folder hub works for you on your machine but
agents cannot reach it, so it is not written to `hub.txt`.

#### Matching by meaning (optional)
Keyword search misses a need phrased unlike the code. With Vertex configured (`pip install google-genai`, `DEVMEM_VERTEX_PROJECT`,
`DEVMEM_VERTEX_EMBED_MODEL`), `devmem-app reuse` and Chat blend embedding similarity into the ranking (cached on disk).
Agents run offline and cannot embed a query, so for them add `--summarize` to `hub push`: the LLM writes a one-sentence description
and related search words per function, stored in the catalog, which the offline keyword search then matches. Only code snippets of the
functions being summarised go to Vertex, and results are cached by code hash.

### 6. Current library docs for agents without internet
Agents trained on your org's code copy its older patterns and miss what libraries now provide. Fetch current docs where there is
internet; they travel as files:

    devmem-app docs add https://docs.pydantic.dev/latest/llms.txt   # llms.txt (follows its links), a docs page, pypi:<pkg> or npm:<pkg>
    devmem-app docs refresh                                          # re-fetch everything; list shows version, date, STALE after 120 days
    devmem-app libs my-service                                       # declared vs latest version of every dependency
    devmem-app publish my-service                                    # copies docs of the libraries it uses into .devmem/docs (+ libraries.md)

`devmem-app hub push` also puts all docs in the hub's `docs/`. Agents run `devmem docs "<need>"` (offline BM25 with stemming; shows source
URL, version and fetch date) and are told to follow it over older code. Name a library after its package (`pydantic`) so it is matched to
repos that depend on it; `--name` overrides. The **Library docs** page does the same from the UI, and Chat cites these docs when you ask about a library.

`devmem reuse` also *modernizes*: when an org solution is found it checks it against the docs on file and prints
`MODERNIZE: ... hand-rolls this: pydantic 2.14 documents a built-in "..."` (or `written against X ==1.0, docs are for 2.3`).

### 7. Chat: ask your repos, or get a prompt for the agent that will build something new
devmem never runs or changes your code; it only prepares knowledge so other agents spend fewer tokens. The **Chat** page is one box
that decides what you mean (no modes to pick; a small link under each answer lets you correct a wrong guess):

- **A question** ("where do we retry failed requests?", "how does X work?") is answered from the indexed repos, their lessons and rules,
  and the current library docs on file, with citations.
- **Something new to build, or a plan you wrote** ("add retry with backoff to the export job", or `## Step N:` headings / a numbered list /
  `- [ ]` items with optional `Docs:` and `Constraints:` lines) becomes a compact **prompt for another agent** (Devin, Claude Code, ...):
  the rules of engagement (plan > current docs > org code > preferences; minimal diff; stop and report instead of guessing), then per
  step the current library docs to follow, the organisation's existing solution in other repos (with `MODERNIZE` notes when a library
  now provides it built in, or the code is written against an older release), what the target repo already has, and protected paths.
  The prompt shows its token estimate and has a copy button. If several repos are onboarded and the message does not name one, Chat asks
  which repo the agent will work in.

It is read-only: nothing is written into any repo. (Plan `Docs:` lines are downloaded into the app's own docs store, not into repos.)
Without the UI: `devmem-app prompt --into my-service --need "add retry with backoff"` or `--plan plan.md`.

For agents that have a plan *file* in their repo, the `devmem-plan` skill and `devmem plan <file> --step N | --done N | --status` give
the same per-step briefs from the committed knowledge and keep progress so a stopped session resumes.

### 8. Improve accuracy (optional)
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
