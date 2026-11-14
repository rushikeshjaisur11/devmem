# devmem

Code knowledge for AI coding agents (Devin, Claude Code, Copilot, Cursor), so they spend fewer tokens and make smaller, safer diffs.
devmem **never runs or changes your code**: it indexes your repos, publishes compact knowledge files agents read instead of whole
files, and builds prompts for the agent that will do the work.

- **Index** repos into a call/test/table graph (Python, JS/TS, Go, Java, C#, Kotlin, Rust, SQL, Spark) with blast-radius analysis, test selection and lineage.
- **Publish** `.devmem/` knowledge into each repo plus an `AGENTS.md` block; agents query it offline with the small `devmem` CLI.
- **Cross-repo reuse**: find what other repos already built, rated by effort to adapt; share it through a central **hub** git repo.
- **Library docs** (llms.txt, web pages, PyPI, npm, or a local docs folder) fetched where there is internet and shipped to agents that have none; reuse
  suggestions are checked against them and flagged `MODERNIZE` when code hand-rolls what a library now provides.
- **Chat** (web UI): ask your repos, or describe something new / paste a plan and get a compact prompt for another agent.
- **Guards**: `devmem diffcheck` blocks oversized, out-of-scope or test-weakening diffs; lessons and rules (added by agents, or by you in the UI) persist what was learned.
- **Token budget**: the `AGENTS.md` block is ~150 tokens and the MCP tool list ~1,000; a task starts with one `devmem context` call
  (~1,200 tokens, measured at about half of the recall + find + show + impact + hygiene answers it replaces on a 21,000-symbol repo);
  every other answer is capped, with a hint for the full version.

- **Runs on a server too**: `devmem-app ui --host 0.0.0.0` adds sign-in with an access token; repos are onboarded by upload or
  Git URL instead of a path (`docs/DEPLOY.md`, `Dockerfile`).

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
devmem-app ui                              # http://127.0.0.1:8765 (Overview, Agents, What breaks if…, Ask & plan, ...; Ctrl+K)
cd /path/to/repo && devmem find "retry logic" && devmem impact retry
```
The UI is prebuilt. To change it: `cd ui && npm ci && npm run dev` (proxies to `devmem-app ui --no-open`), then `npm run build`.

## Usage guide
A first session, end to end. Outputs below are real, from `fixtures/py_app`.

**1. Onboard and publish (you, once per repo).** Open the UI (`devmem-app ui`), go to **Repos** and drop the project folder or a
`.zip` on *Upload* (or give a Git URL, or a path on the machine that runs devmem), then press *Publish*. An uploaded repo has no git
checkout to commit into: download its knowledge from the row menu and unpack it at the root of your repo. Or from the CLI:
```
devmem-app add C:\work\orders-etl
devmem-app publish C:\work\orders-etl --commit   # writes .devmem/ + AGENTS.md on branch devmem/knowledge-sync
```
Push that branch and merge it. **Overview** now shows what agents load per session (~150 tokens) and what each knowledge file costs.

**2. Connect your agent.** On the **Agents** page tick your agents and press *Connect* (or run `devmem connect` in the repo), or install
the plugin (`docs/INSTALL.md`). The Overview checklist ticks this off. From then on the agent starts every task with `recall` and uses
the commands below instead of opening files. A typical agent turn starts with one call:
```
$ devmem context "make retry wait longer between attempts"
FOCUS app/retry.py#retry (lines 5-13)
 callers (must still work): app/client.py: ApiClient.get; app/service.py: load_user
 their callers: 1 (`devmem impact app/retry.py#retry`)
 tests: tests/test_service.py: test_load_user
BODY
def retry(fn, attempts=3, delay=0.1):
    """Call fn, retrying on exception."""
    ...
NEXT: edit, `devmem check`, `devmem diffcheck`. Wrong symbol: add `--symbol <path#name>`.

$ devmem tests-for app/retry.py
python -m pytest -q tests/test_service.py::test_load_user
```
`context` packs the rules and lessons that apply, the likeliest code, its callers, tests and known issues into about 1,200 tokens
(`--budget`). When several functions fit the task equally it lists them with their signatures instead of printing a body that may be
the wrong one; `--symbol` picks. `find`, `show`, `impact` and `outline` remain for follow-ups, and every answer is bounded: callers are
grouped by file, the same definition repeated across files is one hit, `show` prints 80 lines, a large file's `outline` folds methods
into their class, and nothing exceeds 2,500 tokens unless `--full` / `--all` is given (`DEVMEM_MAX_TOKENS`).

**3. Teach it your rules.** In **Lessons & rules → Rules**, add a rule such as *"Never call the payments API from tests; use
FakePayments"*, scope it to `tests/**`, and choose *Only this repo* or *Every repo (hub)*. Agents see targeted rules in `devmem recall`
when they touch matching files; `**` rules apply to every task. Hub rules reach repos on *Sync hub*. Lessons work the same way: agents
save them with `devmem remember` in their PRs, you edit or promote them here.

**4. Add the docs your code depends on.** Docs fetched from the web re-fetch themselves while devmem runs (weekly by default; set it
on the page or with `DEVMEM_DOCS_REFRESH_DAYS`), and the copies in published repos are updated.  In **Library docs**, fetch `pypi:pydantic`, `npm:zod` or `https://<docs-site>/llms.txt`, or
upload a folder or `.zip` of your own guides (Markdown, text, HTML); a local folder such as `C:\docs\internal-api` also works. Publish the repo again. Agents can search them offline (`devmem docs "validate an email"`), and whenever they `show` code that
imports one of these libraries, the matching section of the latest docs is listed with it.

**5. Hand work to an agent.** In **Chat**, describe a change ("add retry with backoff to the export job") or paste a plan. You get a
prompt with the code your org already has, current library docs and the paths not to touch. Paste it into the agent. Every prompt
devmem writes (here, and the fix, test and merge prompts on Code health) has the same shape, so a small model can follow it without
exploring: the task in one sentence, the exact code with line numbers and its callers, numbered steps with the commands to run, short
rules, a done-when checklist and what to report. Untick *Step-by-step prompts* in Chat for a shorter version for a stronger agent.

**See how it all connects.** **Code map** draws every file (or every function and class) of every repo as one force-directed
graph, like Obsidian's graph view: bigger dots have more links, names fade in as you zoom, hovering lights up a node's connections,
and clicking one lists what uses it and what it uses, with a 1-3 hop neighbourhood view. *Graph settings* has filters (tests,
tables, unlinked files, link kinds, repos), colour groups (by repo or kind), display and force sliders; your settings are remembered.
Repos connect through the tables they share and through imports: a file that imports a module another onboarded repo defines
(a Python package, or a JS/TS package by its `package.json` name) gets a heavier *imports* link to it. Colour by folder gives the four
biggest folders a hue; *Saved views* stores the level, filters, groups and forces under a name.

**Check whether it already exists.** **Already built elsewhere** takes a plain description ("retry failed uploads with backoff") and a
target repo, searches every other onboarded repo, and shows each match with the work needed to bring it over (packages to add,
helpers to copy, tables it touches, similar code the target already has) and a ready-to-paste agent prompt. Agents run the same
search with `devmem reuse` or the `reuse` MCP tool.

**Not a developer?** You never need exact names. Wherever the UI asks for code (What breaks if…, Code map, Agents preview), type
what the feature does in your own words ("retry failed upload") and pick from suggestions that show each function's description;
hover any dotted term for a plain explanation, and use **Ask & plan** for questions. `DEVMEM_LOCAL_EMBED=1` adds meaning-based matching.

**Let a model read the repo once (optional).** Indexing never needs AI. **AI analysis** is the optional pass on top: it describes
unclear definitions, summarises each module in two sentences and writes one overview paragraph, showing beforehand roughly how many
requests and tokens that takes. It runs in the background with progress, only sends what is new or changed on later runs, and every
text can be corrected by hand. Publish puts the results into `overview.md` and the module cards; Ask & plan can cite them.

**Describe what the index is unsure about.** Indexing uses no AI, so a function called `run` with no docstring can only be found by
that name. **Improve descriptions** lists those definitions, most used first, with the reason for each rating. Type one sentence, or
select some and let a model draft them (set one up once on **AI setup**: Claude, OpenAI-compatible, Gemini or Vertex), then correct and
approve. They are saved in `.devmem/descriptions.json`, published with the rest, and searched like docstrings by `devmem find` and Chat.

**Know what to fix first.** **Code health** merges devmem's own checks (lint, dead code, complexity, security, dependencies) with
your **SonarQube** project's open issues, security hotspots, quality gate, coverage and duplication into one list. Each finding is
placed in the function it sits in and ordered by severity, by how many places call that function and by whether a test reaches it;
every row has a ready fix prompt and a link back to SonarQube. Connect the server once under **Integrations** (address, user token,
project key per repo; a `sonar-project.properties` in the repo is picked up), then *Sync*. Agents get the same findings from
`devmem hygiene <file>`. CLI and CI: `DEVMEM_SONAR_URL` / `DEVMEM_SONAR_TOKEN` (or Sonar's `SONAR_HOST_URL` / `SONAR_TOKEN`).

**Stop maintaining what a library already does.** **Modernize** scans every function for code written by hand that the language, a
library you already use, or a well-known library provides: a retry loop with sleep (`tenacity`), a dict used as a cache
(`functools.lru_cache`), `zip(x, x[1:])` (`itertools.pairwise`), `JSON.parse(JSON.stringify(x))` (`structuredClone`) and about 25 more
patterns for Python and JS/TS. With *Compare with the latest releases and docs* ticked, the scan first asks the package
registries for the newest release of every library the repo uses, downloads the current docs for libraries that have none on file,
and reads the release notes since the version in use (set `GITHUB_TOKEN` to avoid GitHub's anonymous rate limit); it then shows, per
library that is behind, what changed and which functions here use it, and every candidate links to the current documentation. What
each newer release added is matched against your function names and descriptions ("possible: word match"), and with a model set up
*Find what new releases replace* reads the same notes against the code itself and adds its suggestions, marked as such. When GitHub's
releases API refuses, the project's changelog file is read instead. Each candidate says whether it is usable now: the repo's declared Python / Node version is compared with what the replacement
needs, and a library replacement says whether you already depend on it. The scan needs no AI and changes nothing; a model can
optionally confirm or reject candidates, and each has a step-by-step replace prompt. Agents see the ready ones in `devmem hygiene <file>`.

**Measured test coverage.** Test selection is inferred from calls, which misses tests that reach code through fixtures or helpers.
Import what your test run already produces and `tests-for`, `impact` and "untested" use measured data instead:
`devmem-app coverage <repo> coverage.json` (coverage.py JSON from `pytest --cov --cov-context=test` and `coverage json --show-contexts`;
lcov, Cobertura and JaCoCo XML give line data without per-test detail), or upload it on Code health → Sources. A report left in the
repo is picked up on every re-index; files edited after the test run are skipped rather than mapped onto moved lines. `tests-for`
always says where its selection came from.

**What devmem cannot see, it says.** Answers read code as the file is now: a file edited since indexing is re-parsed (or the
definition searched for) and the answer notes it; renamed or deleted code is refused instead of printing the wrong lines. `impact`
follows inheritance (overrides, callers through a base class) and functions passed by name, and prints a `BLIND SPOT` line when a
decorator, an `__all__` export or the name used as text means a caller may be invisible.

**How far to trust each index.** `devmem-app selfcheck <repo>` (or *Measure it* on the Overview) reports the share of call
edges a second tool confirmed, the share of name-only guesses, whether sampled call sites are still in the code, and how often
search finds a function from a description. Add your own `query => path#qualname` pairs to `.devmem/search-eval.txt`. Measured
numbers and method: `docs/ACCURACY.md`.

**6. Keep it fresh.** While `devmem-app ui` runs it re-indexes any repo whose HEAD moved (pull, merge, commit, branch switch); add
`--publish` to republish too, or `--no-watch` to turn it off. Without the UI: `devmem-app watch` (or `watch --once` from cron or a git
hook). Overview flags an index behind HEAD and any knowledge file over its token budget.

**7. See what it saves.** Every `devmem` query (CLI or MCP) logs its size and the size of the files its answer points to in
`.devmem/usage.jsonl` (gitignored). Overview shows the totals per repo; `devmem report` adds the numbers since the last report to the
PR record, so Metrics shows them per PR. Both sides are estimated as characters / 4.

## Accuracy is not optional
SCIP (type-resolved edges), Graphify (an independent tree-sitter call graph) and tree-sitter (JS/TS) always run: on `add`, `index` and
`publish`, whenever files changed (cached per file hash, so an unchanged repo re-indexes in about a second even at 1,400 files). If one is missing or fails,
indexing stops with the fix instead of quietly producing a worse index. `--fast` / `DEVMEM_ACCURACY=fast` is the explicit opt-out for CI;
it is recorded in the index and shown as "reduced accuracy" in the UI. Measured on psf/requests, edge precision rose from 0.81 to
0.92 with tightened name resolution and to 0.99 where Graphify agrees; with SCIP edges are type-resolved. Details: `docs/ACCURACY.md`.
Go, Java, Kotlin, C# and Rust are parsed with tree-sitter (name-resolved edges); when `scip-go` or `scip-java` is installed it runs too
and their edges become type-resolved. Without them those languages are not held to SCIP.

## Using it with agents
devmem works four ways; pick what your agent supports (the **Agents** page in the UI does this for you). Then run `devmem doctor`
in the repo: it checks every layer, down to a real MCP round trip, and prints the fix for anything wrong. Details: `docs/INSTALL.md`.
- **Any agent, CLI only**: `pip install "devmem @ git+https://github.com/rushikeshjaisur11/devmem.git#subdirectory=packages/devmem"`
  (the PyPI name `devmem` is another project). The `AGENTS.md` block tells the agent which commands to run.
- **MCP, per repo**: `devmem connect` registers the MCP server in each agent's project config, merging into what is there: Claude Code
  (`.mcp.json`, plus `@AGENTS.md` in `CLAUDE.md`), Cursor, VS Code/Copilot, Codex (`.codex/config.toml`, trusted projects), Gemini CLI,
  Kiro, OpenCode; Aider gets `read: AGENTS.md` (it has no MCP); Windsurf only has a user-level config, so it prints a snippet.
  `devmem connect cursor codex` picks agents; `--local` uses this Python's path (machine-specific, keep it out of git).
- **Plugins**: Claude Code `/plugin marketplace add rushikeshjaisur11/devmem` then `/plugin install devmem@devmem` installs the skills
  and starts the MCP server (needs Node.js and any Python 3.10+; set `DEVMEM_PYTHON` if Python is not on PATH; nothing else to install).
  Gemini CLI:
  `gemini extensions install ./plugins/devmem`. Devin: `docs/INSTALL.md`. The skills make agents run `devmem recall`, `reuse`, `docs`,
  `impact`, `check`, `diffcheck` instead of reading files.
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
- **PR review in CI**: the GitHub workflow in `.devmem/ci/github-actions.yml` posts `devmem review` on every PR (changed symbols, their
  callers, which have no test, risk, affected tests) and runs the diff guard. `devmem gaps` lists widely used code no test reaches.
- **Upgrade notes**: `devmem-app docs upgrade-notes <repo>` stores the GitHub release notes between the version a repo uses and the latest
  with that library's docs; agents are pointed at them when versions differ. Set `GITHUB_TOKEN` to avoid API rate limits.
- **Offline meaning-based search in Chat**: `pip install -e "packages/devmem-app[embed]"` and set `DEVMEM_LOCAL_EMBED=1` (downloads a
  small model once, then runs on CPU).
- **Optional LLM features** (see `docs/AI.md` for what needs AI and what works without it): set a model up on the **AI setup** page
  (Claude, any OpenAI-compatible server including Ollama, Gemini, or Vertex) or with `DEVMEM_AI_PROVIDER` / `DEVMEM_AI_KEY` /
  `DEVMEM_AI_MODEL`, for written chat answers, embedding-based ranking and drafted descriptions; `hub push --summarize` has the LLM
  describe functions so offline keyword search matches by meaning. The older `DEVMEM_VERTEX_*` variables still work.

## Commands
| `devmem-app` (your machine) | |
|---|---|
| `add`, `index`, `publish`, `ui`, `watch` | onboard, re-index, write knowledge into a repo, web UI, re-index when HEAD moves |
| `reuse`, `prompt`, `ask`, `find` | cross-repo reuse, agent prompts, retrieval across repos |
| `hub set/push/status`, `docs add/list/refresh/remove/upgrade-notes`, `libs` | central hub, library docs, upgrade notes, dependency freshness |
| `hygiene`, `clones`, `verify`, `flaky`, `impact-column`, `schema`, `memory`, `metrics` | lint/dead-code/complexity, duplicates, test commands, lineage, lessons, PR metrics |

| `devmem` (agents, offline) | |
|---|---|
| `context` | start of a task in one call: rules, likeliest code, callers, tests, known issues, inside a token budget |
| `find`, `show`, `outline`, `impact`, `helpers` | locate code and see blast radius instead of reading files |
| `mcp` | the same commands as MCP tools over stdio |
| `recall`, `remember`, `feedback` | lessons, rules and cross-repo pointers for a task |
| `reuse`, `docs`, `hub`, `plan` | other repos' code, current library docs, the central hub, step-by-step plan briefs |
| `tests-for`, `check`, `diffcheck`, `hygiene`, `report` | affected tests, lint/types, minimal-diff guard, PR metrics |
| `review`, `gaps` | PR summary with blast radius and untested changes; untested code with many callers |
| `connect`, `doctor` | register devmem with coding agents in this repo (MCP + AGENTS.md); check that agents can use it, with fixes |

## Notes
- Index and caches are JSON under `<repo>/.devmem/`; app state (workspace, docs store, hub clone, clones) is under `~/.devmem` (`DEVMEM_HOME`).
- Branch switching and git-URL cloning on the Repos page run `git checkout` / `git clone` in your clones; they refuse on uncommitted changes.
- `index.json` stays the reviewed source of truth. `devmem-app index` also writes `.devmem/index.db`, a gitignored SQLite/FTS5 cache that
  `devmem find` uses to pick candidates; if it is missing or stale, search builds the same tables in memory and writes nothing.
