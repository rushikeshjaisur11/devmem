# Edge accuracy

How good are the call/test edges that drive impact, test selection and the repo map? Measured with
`scripts/accuracy.py <repo> <index.scip> <graphify-out/graph.json>` against **SCIP (type-resolved scip-python) as the
oracle** on [psf/requests](https://github.com/psf/requests) (`src/requests`, 292 oracle edges after restricting to
files SCIP covered and non-module callers; measured 2026-10-17).

| Edge source | Edges | Precision | Recall |
| --- | --- | --- | --- |
| devmem structural, before tightening | 293 | 0.81 | 0.69 |
| devmem structural, current | 252 | 0.92 | 0.68 |
| &nbsp;&nbsp;of which `structural` | 244 | 0.94 | 0.67 |
| &nbsp;&nbsp;of which `ambiguous` | 8 | 0.25 | 0.01 |
| Graphify (`calls`, tree-sitter) | 217 | 0.98 | 0.62 |
| devmem and Graphify agree | 210 | 0.99 | 0.61 |
| devmem edges Graphify did not confirm | 36 | 0.67 | |

What this says:

- Name/import resolution alone was 81% precise. The loss was almost all bare method names that exist on builtins
  (`x.items()` matched to a user class that defines `items`). Those no longer resolve by name; generic verbs (`get`,
  `send`, `close`, ...) only resolve to a class in the caller's file or a file it imports. Precision rose to 92% for
  a 1-point recall cost.
- Where devmem and Graphify agree the edge is right 99% of the time. Where only devmem has it, it is right two times
  in three. With `--graphify`, unconfirmed *ambiguous* edges from files Graphify covered are dropped.
- With `--scip` the oracle itself is merged in (edges become `precise`), so those numbers are 1.0 by construction.
- "Recall" is a lower bound: SCIP also reports references that are not calls (type annotations, base classes), which
  devmem does not model as edges. The remaining misses on requests are mostly those.
- Dynamic dispatch, decorators, and callbacks passed by name stay invisible to static tools, including SCIP.

Limits of this measurement: one Python repo, one date, SCIP as a proxy for ground truth. JS/TS was only checked on the
small `js_app` fixture, where devmem and scip-typescript agree on every direct call; arrow-function consts needed a
rule to match SCIP's term symbols. Re-run the script on your own repos before trusting numbers for them.

## What the pipeline does with these results

`structural` (import/name resolved) -> optional Graphify cross-check (upgrade confirmed ambiguous, drop unconfirmed
ambiguous, add Graphify-only edges) -> optional SCIP (precise edges win; unconfirmed ambiguous edges dropped).
`devmem impact` prints each hop-1 edge with its confidence, and the skill tells agents to verify `ambiguous` ones.
