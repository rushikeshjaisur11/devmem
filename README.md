# devmem

Code knowledge for AI coding agents (Devin, Claude Code, Copilot, Cursor), so they spend fewer tokens and make smaller, safer diffs.
devmem **never runs or changes your code**: it indexes your repos, publishes compact knowledge files agents read instead of whole
files, and builds prompts for the agent that will do the work.

- **Index** repos into a call/test/table graph (Python, JS/TS, SQL, Spark) with blast-radius analysis, test selection and lineage.
- **Publish** `.devmem/` knowledge into each repo plus an `AGENTS.md` block; agents query it offline with the small `devmem` CLI.
- **Cross-repo reuse**: find what other repos already built, rated by effort to adapt; share it through a central **hub** git repo.
- **Library docs** (llms.txt, web pages, PyPI, npm) fetched where there is internet and shipped to agents that have none; reuse
  suggestions are checked against them and flagged `MODERNIZE` when code hand-rolls what a library now provides.
- **Chat** (web UI): ask your repos, or describe something new / paste a plan and get a compact prompt for another agent.
- **Guards**: `devmem diffcheck` blocks oversized, out-of-scope or test-weakening diffs; lessons and rules persist what was learned.

## Requirements
Python 3.10+, git, **Node.js 18+**, and network access the first time (npx downloads the SCIP indexers; docs are fetched from the web).

## Install
```
git clone https://github.com/rushikeshjaisur11/devmem.git && cd devmem
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e packages/devmem -e "packages/devmem-app[test]"
devmem-app --help && pytest                              # 120+ tests
```
Everything accuracy depends on is a hard dependency (see below). Agents only need `pip install -e packages/devmem` (PyYAML, offline).

## Quick start
```
devmem-app add /path/to/repo               # or a Git URL; clones into ~/.devmem/clones/<name>
devmem-app publish /path/to/repo --commit  # writes .devmem/ + AGENTS.md on branch devmem/knowledge-sync; you push and merge it
devmem-app ui                              # http://127.0.0.1:8765 (Repos, Explorer, Impact, Graph, Lineage, Hygiene, Memory, Chat, ...)
cd /path/to/repo && devmem find "retry logic" && devmem impact retry
```
The UI is prebuilt. To change it: `cd ui && npm ci && npm run dev` (proxies to `devmem-app ui --no-open`), then `npm run build`.

## Accuracy is not optional
SCIP (type-resolved edges), Graphify (an independent tree-sitter call graph) and tree-sitter (JS/TS) always run: on `add`, `index` and
`publish`, whenever files changed (cached per file hash, so an unchanged repo re-indexes in ~0.1 s). If one is missing or fails,
indexing stops with the fix instead of quietly producing a worse index. `--fast` / `DEVMEM_ACCURACY=fast` is the explicit opt-out for CI;
it is recorded in the index and shown as "reduced accuracy" in the UI. Measured on psf/requests, edge precision rose from 0.81 to
0.92 with tightened name resolution and to 0.99 where Graphify agrees; with SCIP edges are type-resolved. Details: `docs/ACCURACY.md`.

## Using it with agents
- **Devin / Claude Code / Copilot**: install the plugin (`docs/INSTALL.md`); its skills make agents run `devmem recall`, `reuse`, `docs`,
  `impact`, `check`, `diffcheck` instead of reading files. The wheel is bundled, so no PyPI access is needed.
- **Hub (recommended with several repos)**: `devmem-app hub set git@github.com:acme/devmem-hub.git`, then `devmem-app hub push --push`.
  `publish` writes the URL to `.devmem/hub.txt` (or set `DEVMEM_HUB`); agents run `devmem hub sync` and `devmem reuse "<need>"`. With no
  hub or no access they fall back to the catalog committed in the repo. Agents need git read access to the hub.
- **Library docs**: `devmem-app docs add https://docs.example.dev/llms.txt` (or a page URL, `pypi:<pkg>`, `npm:<pkg>`), `docs refresh`,
  `devmem-app libs <repo>` (declared vs latest versions). Docs ship via `hub push` and `publish`; agents search them with `devmem docs`.
- **Chat → agent prompt**: one box, no modes. A question gets a cited answer; a new requirement or a plan (`## Step N:` headings,
  numbered list or `- [ ]`, optional `Docs:` / `Constraints:` lines) becomes a prompt with the existing org solution, `MODERNIZE` notes,
  current docs, what the target repo already has and protected paths. Read-only: nothing is written to any repo.
  CLI: `devmem-app prompt --into <repo> --need "..."` or `--plan plan.md`.
- **Optional LLM features** (Vertex): `pip install google-genai`, set `DEVMEM_VERTEX_PROJECT` (and `DEVMEM_VERTEX_EMBED_MODEL`) for written
  chat answers and embedding-based ranking; `hub push --summarize` has the LLM describe functions so offline keyword search matches by meaning.

## Commands
| `devmem-app` (your machine) | |
|---|---|
| `add`, `index`, `publish`, `ui` | onboard, re-index, write knowledge into a repo, web UI |
| `reuse`, `prompt`, `ask`, `find` | cross-repo reuse, agent prompts, retrieval across repos |
| `hub set/push/status`, `docs add/list/refresh/remove`, `libs` | central hub, library docs, dependency freshness |
| `hygiene`, `clones`, `verify`, `flaky`, `impact-column`, `schema`, `memory`, `metrics` | lint/dead-code/complexity, duplicates, test commands, lineage, lessons, PR metrics |

| `devmem` (agents, offline) | |
|---|---|
| `find`, `show`, `outline`, `impact`, `helpers` | locate code and see blast radius instead of reading files |
| `recall`, `remember`, `feedback` | lessons, rules and cross-repo pointers for a task |
| `reuse`, `docs`, `hub`, `plan` | other repos' code, current library docs, the central hub, step-by-step plan briefs |
| `tests-for`, `check`, `diffcheck`, `hygiene`, `report` | affected tests, lint/types, minimal-diff guard, PR metrics |

## Notes
- Index and caches are JSON under `<repo>/.devmem/`; app state (workspace, docs store, hub clone, clones) is under `~/.devmem` (`DEVMEM_HOME`).
- Branch switching and git-URL cloning on the Repos page run `git checkout` / `git clone` in your clones; they refuse on uncommitted changes.
- `index.json` stays the reviewed source of truth. `devmem-app index` also writes `.devmem/index.db`, a gitignored SQLite/FTS5 cache that
  `devmem find` uses to pick candidates; if it is missing or stale, search builds the same tables in memory and writes nothing.
