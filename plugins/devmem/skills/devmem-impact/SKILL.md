---
name: devmem-impact
description: Use before changing a function, class, SQL table or table column to see what else breaks (callers, tests, downstream jobs, co-changed files) and keep the edit minimal.
---

# Blast radius with devmem

Run one of:

- `devmem impact <path#qualname>` (a symbol, e.g. `app/client.py#ApiClient.get`; `devmem find` shows ids)
- `devmem impact <table>` or `devmem impact <table.column>` for data changes (works across repos that were onboarded together)

Read the output as:

- **Hop 1 (required):** direct callers or readers. Each must still work after your change.
- **Hop 2 (contract check):** callers of callers. Confirm the return type or shape they rely on is unchanged; do not edit them unless it is not.
- **Tests:** the tests that exercise the change. Run these via `devmem check`.
- **Co-change:** files that historically change together with this one; consider whether they need the same edit.
- Edges marked `precise` come from a type-aware index, `structural` from import resolution, `ambiguous` are guesses: verify ambiguous ones in the code.
- Utilities with many callers are capped and listed as such: change them backwards-compatibly or not at all.

If the radius is larger than the task justifies, change the call site instead of the shared function, or add a new function and leave the old one.
