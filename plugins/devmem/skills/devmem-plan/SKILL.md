---
name: devmem-plan
description: Use when the task is "execute this plan" (a markdown plan written by a human: a plan file in the repo, or a prompt that already lists numbered steps). Run the plan step by step exactly as written, with current library docs and existing org code supplied per step, no redesign, no library swaps, no extra scope.
---

# Execute a plan with devmem

The human already made every decision. Your job is to carry out the plan, not to improve it. Precedence when sources disagree:
**the plan > the current library docs (`devmem docs`) > existing org code > your own preferences**.

## Rules

1. Do not redesign, rename, add scope, swap libraries or "modernise" anything the plan does not ask for.
2. Use the library APIs the docs and plan name, at the versions they describe. Older org code is a reference for *what the business logic is*, not for *how to call libraries*; if it conflicts with current docs, follow the docs and say so in the commit message.
3. If a step cannot be done as written (symbol missing, protected path, plan contradicts the code, docs nowhere to be found): **stop, mark nothing, and report exactly what blocks it.** Never guess an API and never silently deviate.
4. Never touch protected paths (`devmem` shows them in the brief). New dependencies: only if the plan says so; then add `devmem-allow: new_deps <plan step>` to the commit message.

## Loop

If the prompt you were given already contains the steps with their docs and existing-code notes, follow it directly (steps 3-5 below, skipping the `devmem plan` calls). If there is a plan file, use the full loop:

0. Make sure the CLI exists (`devmem --version`; else `bash <devmem skill folder>/scripts/bootstrap.sh`). If a hub is configured run `devmem hub sync` once.
1. `devmem plan <plan-file> --status` to see where you are (it resumes after a stopped session).
2. For the next unfinished step N: `devmem plan <plan-file> --step N` and read the whole brief. It contains the step text, the constraints, the current library docs to follow, the existing solution in other org repos (with `MODERNIZE` notes where a library now provides it built in), what this repo already has, and protected paths.
3. Implement only that step, minimal diff. Before editing a symbol, `devmem impact <symbol>`.
4. `devmem check` then `devmem diffcheck`. Fix what they report. Commit with a message naming the step.
5. `devmem plan <plan-file> --done N --note "<anything that deviated, or nothing>"`. Go to step 2.

## Finish

When `--status` says all steps are done: `devmem report --base <default-branch> --task "<plan title>"`, commit `.devmem/metrics/`, and open the PR. In the PR description list, per step, which library docs and org code you used, and every deviation (there should be none).
