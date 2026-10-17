---
name: devmem
description: Use at the start of any coding task in a repo that has a .devmem/ folder (or AGENTS.md mentioning devmem). Finds code with a graph instead of reading files, scopes the change by blast radius, runs only affected tests, and blocks oversized or unrelated diffs.
---

# devmem workflow

devmem keeps token use low and diffs small. Follow this order; every step replaces reading files in bulk.

## 0. Make sure the CLI exists (once per session)

Run `devmem --version`. If it is missing, run `bash <this skill folder>/scripts/bootstrap.sh` (it installs the wheel bundled in `vendor/`, or from git if `DEVMEM_GIT_URL` is set). If all fail, tell the user and continue without devmem. Do not retry in a loop. If `devmem reuse --help` fails, the installed CLI is older than this skill: run `python3 -m pip install --force-reinstall <this skill folder>/vendor/devmem-*.whl`.

If `.devmem/index.json` does not exist in the repo, devmem has no data here: work normally and mention that `devmem-app publish <repo>` has not been run.

## 1. Orient (before opening files)

1. `devmem recall "<the task in one sentence>"`: lessons already learned, standing rules, and code other repos already solved. Apply them. Rate what you used: `devmem feedback <id> useful` or `useless`.
2. Read `.devmem/knowledge/map.md` (ranked repo map), then only the topic files that match the task (`commands.md`, `conventions.md`, `helpers.md`, `lineage.md`, `no-touch.md`, `hygiene.md`).
3. Before writing anything non-trivial, look for code that already exists in the organisation's other repos: `devmem reuse "<what you need>"`. It shows source, tests and dependencies of matching code and rates the effort to adapt it here (`--prompt` prints porting instructions). Copy with a one-line origin comment and fit this repo's conventions instead of rewriting.
   - If `.devmem/hub.txt` exists (or `DEVMEM_HUB` is set) there is a central knowledge hub with every onboarded repo. Run `devmem hub sync` once per session (needs git read access to it; if it fails, carry on: `devmem reuse` falls back to the catalog committed in this repo). `devmem hub repos` lists what the hub knows; `devmem hub show <repo>` prints another repo's overview.
4. Older org code can predate what the libraries offer today. For any new requirement run `devmem docs "<need>"` (offline copy of the CURRENT library documentation, with version and fetch date; `devmem docs --list` shows what exists). When a `devmem reuse` card shows `MODERNIZE`, tell the user: "this repo/other repo already solves it, but <library> <version> now provides it built in; the existing code hand-rolls it", and prefer the built-in. If the docs on file do not cover the need, say so instead of guessing an API from memory.
5. If the task is "execute this plan" (a plan file in `.devmem/plans/`), stop here and use the `devmem-plan` skill instead.
6. Locate code: `devmem find "<concept>"`, `devmem show <symbol>`, `devmem outline <file>`. Prefer these to `grep`/`cat` on whole files. Reuse what `devmem helpers` lists before writing a new helper.

## 2. Scope the change

Before editing a function, class or table column run `devmem impact <symbol | table | table.column>` (see the `devmem-impact` skill). Hop-1 callers must still work, hop-2 only needs a contract check. Edit only what the task needs. Do not reformat, rename, add dependencies or change public signatures unless the task requires it. Files listed in `no-touch.md` or marked `no_touch` need explicit user approval.

## 3. Verify narrowly

- `devmem check` runs lint, types and only the tests affected by your diff; it prints a few lines instead of a full log. Known-flaky failures are labelled.
- `devmem tests-for` prints the exact test command if you only need that.
- Fix failures before moving on. Do not run the whole suite unless `check` says no tests are mapped.

## 4. Guard the diff, then record it

- `devmem diffcheck` before every commit. If it blocks, reduce the change. Only when the larger change is truly required, add a line to the PR description: `devmem-allow: <check> <reason>` (checks: size, out_of_scope, format_only, new_dependency, deleted_test, api_change, duplicate, lint_fix, no_touch).
- Before opening the PR: `devmem report --base <default-branch> --task "<what you did>"` and commit the new file under `.devmem/metrics/`. Add `--tokens N` only if you know the real number.

## 5. Leave a lesson when it was hard

If you spent real effort on something non-obvious (a failure with a surprising cause, a convention the repo enforces), run the `devmem-remember` skill. Do not record trivia.

## Rules of thumb

- A stale stamp on a knowledge file (`<!-- devmem: commit=... -->` older than HEAD) means trust the code over the file.
- If devmem output and the code disagree, the code wins; mention the mismatch in the PR so the index gets refreshed.
- Never paste large file contents into your reasoning when `show` or `outline` gives the part you need.
