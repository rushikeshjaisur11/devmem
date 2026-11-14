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
- Callers are grouped by file (`path: caller, caller; path: caller`). A caller tagged `[ambiguous]` is a guess: verify it in the code. Untagged callers were resolved by imports or a type-aware index.
- Utilities with many callers are capped and listed as such: change them backwards-compatibly or not at all.

If the radius is larger than the task justifies, change the call site instead of the shared function, or add a new function and leave the old one.
- `[passed by name]` means the function is handed over (a callback, `Thread(target=...)`, a handler table) rather than called: treat it as a caller.
- `overridden by` lists subclass methods whose signatures must follow a change to this one; `overrides` means callers of the base method reach this code too.
- A `BLIND SPOT` line means the call graph cannot see every caller (a decorator that registers the function, an export in `__all__`, the name used as text). "No callers found" is then not proof that nothing uses it: search for the name before removing or renaming it.
