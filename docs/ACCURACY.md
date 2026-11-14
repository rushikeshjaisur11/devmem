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
  in three. With Graphify (always on), unconfirmed *ambiguous* edges from files Graphify covered are dropped.
- With SCIP (always on) the oracle itself is merged in (edges become `precise`), so those numbers are 1.0 by construction.
- "Recall" is a lower bound: SCIP also reports references that are not calls (type annotations, base classes), which
  devmem does not model as edges. The remaining misses on requests are mostly those.
- Dynamic dispatch, decorators, and callbacks passed by name stay invisible to static tools, including SCIP.

Limits of this measurement: one Python repo, one date, SCIP as a proxy for ground truth. JS/TS was only checked on the
small `js_app` fixture, where devmem and scip-typescript agree on every direct call; arrow-function consts needed a
rule to match SCIP's term symbols. Re-run the script on your own repos before trusting numbers for them.

## Second measurement: Starlette, after receiver-type inference (2026-11-14)

Same method (scip-python as the oracle, `--fast` structural index, files SCIP covered, non-module callers), on
`starlette` as vendored in the test corpus. "Before" is the code before receiver types were inferred, "after" with them:
a variable assigned from a constructor or from a function whose return type is known, annotated parameters, `self.x`
attributes assigned in the class, chained constructor calls, `super()`, methods inherited through base classes, and
receivers known to be builtin or library objects (which then get no edge instead of a same-named method in the repo).

| Edge source | Before: edges / precision / recall | After: edges / precision / recall |
| --- | --- | --- |
| all | 433 / 0.778 / 0.507 | 442 / 0.826 / 0.549 |
| `structural` | 333 / 0.970 / 0.486 | 368 / 0.981 / 0.543 |
| `ambiguous` | 100 / 0.140 / 0.021 | 74 / 0.054 / 0.006 |

Name-only guesses (`ambiguous`) were right one time in twenty here, so `impact` and `context` no longer list them among
the callers that must keep working; they appear on a separate "possible callers" line. With Graphify (the default, full
accuracy mode) unconfirmed guesses from files Graphify covered are dropped altogether.

## Checks that run on every change

- `fixtures/accuracy_py` is built around the traps above (the same method name on three classes, `dict.items()` beside a
  user `items` method, aliased imports, constructors through a module alias, inherited methods, a function passed by
  name). Its `truth.json` was written by reading the code. `tests/test_accuracy_floor.py` requires precision and recall
  of 1.0 on it (13 of 13 edges, none extra) and exact edges on the Go, Java and JS/TS fixtures. CI runs it on each PR.
- `devmem-app selfcheck <repo>` (and the strip on each repo's Overview card) measures the repo you onboarded: how many
  call edges a second tool confirmed, the share of name-only guesses, whether a sample of call sites is still in the code,
  and how often `find` / `context` locate a function from a description of it (its docstring with its own name words
  removed, plus your own `query => path#qualname` pairs in `.devmem/search-eval.txt`). The last 30 runs are kept.

On the 21,000-symbol test corpus the search benchmark rose from top-1 0.7% / top-5 11% / `context` focus 0% to
27% / 49% / 16% after `find` began weighting words by how rare they are in the whole index and dropping stop words.
Those queries reuse docstring wording, so they are easier than a person's own phrasing: read them as a regression
signal, not as the rate a user will see.

## What the pipeline does with these results

`structural` (import/name resolved) -> Graphify cross-check (upgrade confirmed ambiguous, drop unconfirmed
ambiguous, add Graphify-only edges) -> SCIP (precise edges win; unconfirmed ambiguous edges dropped).
`devmem impact` prints each hop-1 edge with its confidence, and the skill tells agents to verify `ambiguous` ones.
