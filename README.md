# devmem

Cross-repo code knowledge for coding agents (Devin primary, Copilot). Plan: the "devmem — Full Application Plan" doc.

## Status (build steps 1-12)
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
- [ ] Not yet: chat, PR metrics loop; Graphify + scip-python as extra edge sources, SQLite/FTS5 store, eslint/knip (JS/TS) hygiene

## Use
    uv sync   # or: pip install -e packages/devmem -e packages/devmem-app
    devmem-app add /path/to/repo      # onboard + index -> <repo>/.devmem/index.json
    cd /path/to/repo && devmem find "retry logic" && devmem impact retry
    pytest

## Notes
- Python >= 3.10 (plan said 3.11+; relaxed so it runs on the current machine). CLI uses argparse instead of Typer to keep the pip install tiny.
- Index is JSON for now; SQLite/FTS5 arrives with the hub (step 2 extension).
