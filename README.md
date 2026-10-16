# devmem

Cross-repo code knowledge for coding agents (Devin primary, Copilot). Plan: the "devmem — Full Application Plan" doc.

## Status (build steps 1-4 of 13)
- [x] 1. Core formats (`devmem.schema`), `.devmemignore`, fixture repos + golden tests
- [x] 2. Indexer: Python (stdlib `ast`), SQL + Spark table lineage (sqlglot), incremental by content hash
- [x] 3. Graph: call/test/table edges with confidence; impact (hop 1 required, hop 2 contract check, utility cap, tests separate)
- [x] 4. Agent CLI: `find`, `show`, `outline`, `impact`, `helpers` (stdlib + PyYAML only, no network)
- [ ] Not yet: Graphify + scip-python as extra edge sources, SQLite/FTS5 store, PageRank repo map, publisher, diffcheck, hygiene, lessons, UI, chat, metrics

## Use
    uv sync   # or: pip install -e packages/devmem -e packages/devmem-app
    devmem-app add /path/to/repo      # onboard + index -> <repo>/.devmem/index.json
    cd /path/to/repo && devmem find "retry logic" && devmem impact retry
    pytest

## Notes
- Python >= 3.10 (plan said 3.11+; relaxed so it runs on the current machine). CLI uses argparse instead of Typer to keep the pip install tiny.
- Index is JSON for now; SQLite/FTS5 arrives with the hub (step 2 extension).
