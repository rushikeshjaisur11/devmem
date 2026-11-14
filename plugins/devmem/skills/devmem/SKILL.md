---
name: devmem
description: Use at the start of any coding task in a repo that has a .devmem/ folder (or AGENTS.md mentioning devmem). Finds code with a graph instead of reading files, scopes the change by blast radius, runs only affected tests, and blocks oversized or unrelated diffs.
---

# devmem workflow

devmem keeps token use low and diffs small. Follow this order; every step replaces reading files in bulk.

## Token budget (every tool result is re-sent on every later turn, so small outputs compound)

- Start with ONE call: `devmem context "<task in one sentence>"`. It returns the rules and lessons that apply, the best matches, the body of the likeliest symbol, its callers, tests and known issues, inside ~1,200 tokens. Do not also run `recall`, `find`, `show` and `impact` for the same thing.
- Wrong focus? `devmem context "<task>" --symbol <path#name>` (ids are in its MATCHES list). Never re-run a query you already ran.
- Need more: `find "<2-4 words>" -n 5`, `show <symbol>` (80 lines; `--full` only to edit past them), `outline <file>` (`--in <Class>` for one class). When you must open a file, read only the line range these gave you.
- Lists are capped (12 callers, 60 outline lines, 2,500 tokens per answer); add `--all` only for a signature or contract change.
- Open a `.devmem/knowledge/` topic file only when the task touches that topic.
- Pipe nothing big into your context: `check` prints one line per passing step and only the failing lines. Do not run the full test suite or `cat` logs unless `check` says no tests are mapped.
- Keep your own messages short: report what changed and what was verified, not a narrative.

## 0. Make sure the CLI exists (once per session)

Run `devmem --version`. If it is missing, run `bash <this skill folder>/scripts/bootstrap.sh` (it installs the wheel bundled in `vendor/`, or from git if `DEVMEM_GIT_URL` is set). If all fail, tell the user and continue without devmem. Do not retry in a loop. If `devmem reuse --help` fails, the installed CLI is older than this skill: run `python3 -m pip install --force-reinstall <this skill folder>/vendor/devmem-*.whl` (on Windows `python` or `py -3` instead of `python3`).

If `devmem_*` MCP tools are available, call them instead of the shell commands below: same commands, same output, no shell.

If `.devmem/index.json` does not exist in the repo, devmem has no data here: work normally and mention that `devmem-app publish <repo>` has not been run.

## 1. Orient (before opening files)

1. `devmem context "<the task in one sentence>"`. Apply its RULES and LESSONS; rate a lesson you used: `devmem feedback <id> useful` or `useless`. Its FOCUS block already has the callers and tests of the likeliest symbol.
2. Only if the task needs it: `.devmem/knowledge/map.md` (ranked repo map) and the matching topic file (`commands.md`, `conventions.md`, `helpers.md`, `lineage.md`, `no-touch.md`, `hygiene.md`).
3. Before writing anything non-trivial that might exist elsewhere: `devmem reuse "<what you need>"` (source, tests, dependencies and effort to adapt; `--prompt` prints porting steps). Copy with a one-line origin comment and fit this repo's conventions. With `.devmem/hub.txt` or `DEVMEM_HUB`, run `devmem hub sync` once per session; if it fails, carry on.
4. Changing code that uses a library: `devmem docs --for <file|symbol>` gives the latest docs for what it imports; write against that API, not the older pattern in the file. If the pointer warns of another major version than the repo uses, stay within the repo's version and say so in the PR. New requirement: `devmem docs "<need>"`. A `MODERNIZE` note on a `reuse` card means the library now provides it built in: prefer that and tell the user. If the docs on file do not cover the need, say so instead of guessing an API.
5. If the task is "execute this plan" (a plan file in `.devmem/plans/`), stop here and use the `devmem-plan` skill instead.
6. Reuse what `devmem helpers` lists before writing a new helper.

## 2. Scope the change

Before editing a function, class or table column that `context` did not already cover, run `devmem impact <symbol | table | table.column>` (see the `devmem-impact` skill). Hop-1 callers must still work, hop-2 only needs a contract check. Edit only what the task needs. Do not reformat, rename, add dependencies or change public signatures unless the task requires it. Files listed in `no-touch.md` or marked `no_touch` need explicit user approval.

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
