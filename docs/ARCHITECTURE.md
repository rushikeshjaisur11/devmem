# Architecture

devmem is two Python packages that meet at a folder of files, `<repo>/.devmem/`.

| Package | Runs | Depends on | Job |
| --- | --- | --- | --- |
| `packages/devmem-app` (`devmem_app`) | your machine, with network | tree-sitter, SCIP indexers (via npx), Graphify, FastAPI | build the index, publish knowledge, serve the web UI |
| `packages/devmem` (`devmem`) | the agent's sandbox, offline | PyYAML only | read `.devmem/` and answer `find`, `impact`, `tests-for`, `diffcheck`, ... |

The app never runs or edits your code; it writes only under `.devmem/`, `AGENTS.md` and a Copilot managed block, on a branch you review.
The agent CLI never imports the app, so the wheel bundled in `plugins/devmem/skills/devmem/vendor` is enough for an agent with no PyPI access.

## Data flow

```
repo files --indexer.index_repo--> .devmem/index.json   (symbols, edges, per-file hash)
   |  parse: ast (Python), jsparse (JS/TS), sql_tables (SQL)
   |  edges: structural, then SCIP (precise) and Graphify (cross-check); see ACCURACY.md
   v
publisher --> .devmem/knowledge/*.md  (map, tests, commands, conventions, no-touch, ...)
          --> .devmem/{commands,lineage,history,hygiene,reuse-catalog}.json, config.yaml, rules.md
          --> AGENTS.md managed block (<!-- devmem:start --> ... <!-- devmem:end -->)
   v
agent: devmem find / impact / tests-for / check / diffcheck / recall / reuse
```

Re-indexing is incremental: files are cached by content hash (`stale_files`), so an unchanged repo re-indexes in about 0.1 s.

## Index format

`.devmem/index.json`, defined in `packages/devmem/src/devmem/schema.py` (`INDEX_VERSION = 1`):

- `files`: `{path: {hash, lang, imports, tables, calls}}`
- `symbols`: `{id: "path#qualname", name, qualname, kind, file, line, end_line, sig, doc, is_test}`
- `edges`: `{src, dst, type: calls|tests|reads|writes, conf: precise|structural|ambiguous|co-change}`; table edges use `dst = "table:<name>"`.

`Index` (`index.py`) loads the whole file into dicts (`symbols`, `out`, `inc`) and answers every query in memory.
`Index.find` picks candidates with an FTS5 query (any shared token, or the query as a substring of the qualname), then scores only those by token overlap on name, doc and path.
The tables live in `.devmem/index.db` (gitignored), written by the indexer after `index.json` and valid only while its stamp (size + mtime of `index.json`) matches.
Readers never write: a missing, stale or corrupt db is rebuilt in memory, which keeps prompt building read-only.
Candidates are ranked by bm25 (weights mirror the scorer: name 3, doc 1, path 0.5) and capped at `max(CANDIDATE_CAP = 300, 10 * limit)` per source, so a query of very common tokens stays fast. Results are exact unless a query matches more symbols than the cap; then only the lowest-ranked matches can be dropped.
Impact walks `inc` edges up to `MAX_HOPS = 2`, capping utility callers at `UTILITY_CALLER_CAP = 20`.

## Knowledge budgets

Generated files carry a header `<!-- devmem: commit=... generated=... tokens=... -->` (`knowledge_header`).
Budgets in `knowledge.py`: `AGENTS.md` index 300 tokens, `map.md` 1,500, any other file 25,000. `check_knowledge` flags a file whose header commit is behind HEAD or whose size exceeds its budget.

## Other state

| Where | What |
| --- | --- |
| `<repo>/.devmem/` | index, knowledge, lessons (`failures.jsonl`, `feedback.jsonl`), `flaky.txt`, `no-touch.txt`, `hub.txt`; reviewed like code |
| `~/.devmem` (`DEVMEM_HOME`) | workspace, library-docs store, hub clone, `clones/<name>` |
| hub git repo | every repo's reuse catalog and library docs, so agents can find code other repos already wrote |

## Where to start reading

- Indexing: `devmem_app/indexer.py`, `scip.py`, `graphify.py`
- Publishing: `devmem_app/publisher.py`, `commands.py`, `conventions.py`, `ranking.py`
- Agent queries: `devmem/index.py`, `check.py`, `diffcheck.py`, `memory.py`
- Web UI: `devmem_app/server.py` (API under `/api`), `ui/src` (React, built into `devmem_app/ui_dist`)
