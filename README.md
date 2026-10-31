# devmem

Code knowledge for AI coding agents (Devin, Claude Code, Copilot, Cursor), so they spend fewer tokens and make smaller, safer diffs.
devmem **never runs or changes your code**: it indexes your repos, publishes compact knowledge files agents read instead of whole
files, and builds prompts for the agent that will do the work.

- **Index** repos into a call/test/table graph (Python, JS/TS, Go, Java, SQL, Spark) with blast-radius analysis, test selection and lineage.
- **Publish** `.devmem/` knowledge into each repo plus an `AGENTS.md` block; agents query it offline with the small `devmem` CLI.
- **Cross-repo reuse**: find what other repos already built, rated by effort to adapt; share it through a central **hub** git repo.
- **Library docs** (llms.txt, web pages, PyPI, npm, or a local docs folder) fetched where there is internet and shipped to agents that have none; reuse
  suggestions are checked against them and flagged `MODERNIZE` when code hand-rolls what a library now provides.
- **Chat** (web UI): ask your repos, or describe something new / paste a plan and get a compact prompt for another agent.
- **Guards**: `devmem diffcheck` blocks oversized, out-of-scope or test-weakening diffs; lessons and rules (added by agents, or by you in the UI) persist what was learned.
- **Token budget**: the `AGENTS.md` block is ~150 tokens; agent commands print capped output (`show` 80 lines, `impact` 12 items per
  list) with a hint for the full version, and the skill tells agents how to keep tool output small.

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
devmem-app ui                              # http://127.0.0.1:8765 (Overview, Repos, Impact, Graph, Chat, ...; Ctrl+K to jump)
cd /path/to/repo && devmem find "retry logic" && devmem impact retry
```
The UI is prebuilt. To change it: `cd ui && npm ci && npm run dev` (proxies to `devmem-app ui --no-open`), then `npm run build`.

## Usage guide
A first session, end to end. Outputs below are real, from `fixtures/py_app`.

**1. Onboard and publish (you, once per repo).** Open the UI (`devmem-app ui`), go to **Repos**, paste a local path or Git URL, press
*Add and index*, then *Publish*. Or from the CLI:
```
devmem-app add C:\work\orders-etl
devmem-app publish C:\work\orders-etl --commit   # writes .devmem/ + AGENTS.md on branch devmem/knowledge-sync
```
Push that branch and merge it. **Overview** now shows what agents load per session (~150 tokens) and what each knowledge file costs.

**2. Give the agent the skill.** Install the plugin (`docs/INSTALL.md`). From then on the agent starts every task with `recall` and uses
the commands below instead of opening files. A typical agent turn:
```
$ devmem find "retry"
app/retry.py:5  function retry — Call fn, retrying on exception.

$ devmem impact retry
targets: app/retry.py#retry
hop 1 (must check): app/client.py#ApiClient.get [structural], app/service.py#load_user [structural]
hop 2 (contract check): app/service.py#main
tests: tests/test_service.py#test_load_user
files: app/client.py, app/retry.py, app/service.py

$ devmem tests-for app/retry.py
python -m pytest -q tests/test_service.py::test_load_user
```
Three short answers replace reading three files and the whole test suite. `show retry` prints the function body (first 80 lines,
`--full` for more), and `impact` lists are capped at 12 items (`--all` for every one).

**3. Teach it your rules.** In **Lessons & rules → Rules**, add a rule such as *"Never call the payments API from tests; use
FakePayments"*, scope it to `tests/**`, and choose *Only this repo* or *Every repo (hub)*. Agents see targeted rules in `devmem recall`
when they touch matching files; `**` rules apply to every task. Hub rules reach repos on *Sync hub*. Lessons work the same way: agents
save them with `devmem remember` in their PRs, you edit or promote them here.

**4. Add the docs your code depends on.** In **Library docs**, add `https://<docs-site>/llms.txt`, `pypi:pydantic`, `npm:zod`, or a
local folder such as `C:\docs\internal-api`. Publish the repo again. Agents can search them offline (`devmem docs "validate an email"`), and whenever they `show` code that
imports one of these libraries, the matching section of the latest docs is listed with it.

**5. Hand work to an agent.** In **Chat**, describe a change ("add retry with backoff to the export job") or paste a plan. You get a
compact prompt with the code your org already has, current library docs and the paths not to touch. Paste it into the agent.

**6. Keep it fresh.** While `devmem-app ui` runs it re-indexes any repo whose HEAD moved (pull, merge, commit, branch switch); add
`--publish` to republish too, or `--no-watch` to turn it off. Without the UI: `devmem-app watch` (or `watch --once` from cron or a git
hook). Overview flags an index behind HEAD and any knowledge file over its token budget.

**7. See what it saves.** Every `devmem` query (CLI or MCP) logs its size and the size of the files its answer points to in
`.devmem/usage.jsonl` (gitignored). Overview shows the totals per repo; `devmem report` adds the numbers since the last report to the
PR record, so Metrics shows them per PR. Both sides are estimated as characters / 4.

## Accuracy is not optional
SCIP (type-resolved edges), Graphify (an independent tree-sitter call graph) and tree-sitter (JS/TS) always run: on `add`, `index` and
`publish`, whenever files changed (cached per file hash, so an unchanged repo re-indexes in ~0.1 s). If one is missing or fails,
indexing stops with the fix instead of quietly producing a worse index. `--fast` / `DEVMEM_ACCURACY=fast` is the explicit opt-out for CI;
it is recorded in the index and shown as "reduced accuracy" in the UI. Measured on psf/requests, edge precision rose from 0.81 to
0.92 with tightened name resolution and to 0.99 where Graphify agrees; with SCIP edges are type-resolved. Details: `docs/ACCURACY.md`.

## Using it with agents
- **Devin / Claude Code / Copilot**: install the plugin (`docs/INSTALL.md`); its skills make agents run `devmem recall`, `reuse`, `docs`,
  `impact`, `check`, `diffcheck` instead of reading files. The wheel is bundled, so no PyPI access is needed.
- **Any MCP client** (Claude Code, Cursor, VS Code, ...): `devmem mcp` serves the same commands as tools over stdio (`devmem_recall`,
  `devmem_find`, `devmem_show`, `devmem_impact`, `devmem_docs`, ...), stdlib only. Add it to the repo's `.mcp.json`:
  `{"mcpServers": {"devmem": {"command": "devmem", "args": ["mcp"]}}}` (set `DEVMEM_ROOT` if the client starts it elsewhere).
- **Library versions**: docs default to the latest release; `pypi:<pkg>==<ver>` or `npm:<pkg>@<ver>` fetches a specific one for a repo
  that cannot upgrade. Publish records the versions the repo declares and locks (`package-lock.json`, `uv.lock`, `poetry.lock`, `==`
  pins), and agents are warned when the docs on file are another major version than the repo uses.
- **Open in editor**: file and symbol references in the UI open in VS Code or Cursor (choose in the sidebar footer).
- **Hub (recommended with several repos)**: `devmem-app hub set git@github.com:acme/devmem-hub.git`, then `devmem-app hub push --push`.
  `publish` writes the URL to `.devmem/hub.txt` (or set `DEVMEM_HUB`); agents run `devmem hub sync` and `devmem reuse "<need>"`. With no
  hub or no access they fall back to the catalog committed in the repo. Agents need git read access to the hub.
- **Library docs**: `devmem-app docs add https://docs.example.dev/llms.txt` (or a page URL, `pypi:<pkg>`, `npm:<pkg>`, a local file or folder), `docs refresh`,
  `devmem-app libs <repo>` (declared vs latest versions). Docs ship via `hub push` and `publish`; agents search them with `devmem docs`.
  When an agent opens code (`devmem show`, `recall`), devmem lists the latest docs section for each library that code imports, so
  edits follow the current API rather than the older pattern in the file; `devmem docs --for <file|symbol>` prints those sections.
- **Chat → agent prompt**: one box, no modes. A question gets a cited answer; a new requirement or a plan (`## Step N:` headings,
  numbered list or `- [ ]`, optional `Docs:` / `Constraints:` lines) becomes a prompt with the existing org solution, `MODERNIZE` notes,
  current docs, what the target repo already has and protected paths. Read-only: nothing is written to any repo.
  CLI: `devmem-app prompt --into <repo> --need "..."` or `--plan plan.md`.
- **Optional LLM features** (Vertex): `pip install google-genai`, set `DEVMEM_VERTEX_PROJECT` (and `DEVMEM_VERTEX_EMBED_MODEL`) for written
  chat answers and embedding-based ranking; `hub push --summarize` has the LLM describe functions so offline keyword search matches by meaning.

## Commands
| `devmem-app` (your machine) | |
|---|---|
| `add`, `index`, `publish`, `ui`, `watch` | onboard, re-index, write knowledge into a repo, web UI, re-index when HEAD moves |
| `reuse`, `prompt`, `ask`, `find` | cross-repo reuse, agent prompts, retrieval across repos |
| `hub set/push/status`, `docs add/list/refresh/remove`, `libs` | central hub, library docs, dependency freshness |
| `hygiene`, `clones`, `verify`, `flaky`, `impact-column`, `schema`, `memory`, `metrics` | lint/dead-code/complexity, duplicates, test commands, lineage, lessons, PR metrics |

| `devmem` (agents, offline) | |
|---|---|
| `find`, `show`, `outline`, `impact`, `helpers` | locate code and see blast radius instead of reading files |
| `mcp` | the same commands as MCP tools over stdio |
| `recall`, `remember`, `feedback` | lessons, rules and cross-repo pointers for a task |
| `reuse`, `docs`, `hub`, `plan` | other repos' code, current library docs, the central hub, step-by-step plan briefs |
| `tests-for`, `check`, `diffcheck`, `hygiene`, `report` | affected tests, lint/types, minimal-diff guard, PR metrics |

## Notes
- Index and caches are JSON under `<repo>/.devmem/`; app state (workspace, docs store, hub clone, clones) is under `~/.devmem` (`DEVMEM_HOME`).
- Branch switching and git-URL cloning on the Repos page run `git checkout` / `git clone` in your clones; they refuse on uncommitted changes.
- `index.json` stays the reviewed source of truth. `devmem-app index` also writes `.devmem/index.db`, a gitignored SQLite/FTS5 cache that
  `devmem find` uses to pick candidates; if it is missing or stale, search builds the same tables in memory and writes nothing.
