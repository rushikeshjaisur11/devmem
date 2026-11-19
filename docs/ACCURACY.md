# Edge accuracy

How good are the call/test edges that drive impact, test selection and the repo map? Measured with
`scripts/accuracy.py <repo> <index.scip> <graph.json>` against **SCIP (type-resolved scip-python) as the
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

## Third measurement: receiver classes from declarations (2026-11-17)

Same method (scip-python 0.6.8 as the oracle, `--fast` structural index, files SCIP covered, non-module callers) on four
libraries as installed from PyPI: starlette, fastapi, pydantic, sqlglot. "Before" is the previous section's code. "After"
also reads a receiver's class from `Optional[X]`, `X | None`, a quoted `"X"`, `Annotated[X, ...]`; from the elements of
`list[X]` / `Iterable[X]` and the values of `dict[K, X]` (loop variables, `xs[0]`); from `self.x = param` and
`self.x: X | None = None`; from `y = x`, `(x := X())` and `x = y.method()` when the method declares what it returns; from
alternative constructors (`X.create()` returning `cls(...)` or `Self`); and from subclasses when the declared class does not
define the method. A variable's class is the one assigned above the call, and an assignment inside an `if` / loop / `try`
that disagrees with the one before it makes the class unknown instead of picking one.

| Edges (4 libraries together) | Before: edges / right / precision | After: edges / right / precision |
| --- | --- | --- |
| all | 12,642 / 9,375 / 0.742 | 12,592 / 9,392 / 0.746 |
| `structural` | 9,412 / 8,383 / 0.891 | 9,447 / 8,437 / 0.893 |
| `ambiguous` (name-only guesses) | 3,230 / 992 / 0.307 | 3,145 / 955 / 0.304 |

So on library code the gain is small: 54 more certain edges that are right, 85 fewer guesses, one right edge lost (a loop
variable declared as a class from another library and then narrowed with `isinstance`). Starlette's `structural` precision
went from 0.978 to 0.992. Libraries rarely pass their own objects around through annotated parameters; application code
does. `fixtures/typed_py` is written that way (a service holding `self.store = store`, `Optional[Cache]`, `List[Backend]`):
before, 17 of its 29 call edges were guesses and 9 of those were wrong; now all 27 calls are certain and right, and the one
call whose class depends on a branch stays a two-way guess. `tests/test_accuracy_floor.py` requires exactly that.

Lineage, same date: `UPDATE`, `DELETE`, `DROP`, `ALTER` and `TRUNCATE` now count as writes to their table (they were listed
as reads), and a docstring that opens with "Create ..." / "Delete ..." is no longer parsed as a query (it could add tables
that do not exist, such as `stale` and `cache` from "Delete stale entries from cache").

## Fourth measurement: what the file can see, and which Graphify edges to trust (2026-11-19)

Same oracle (scip-python), now scoring the **final** index (structural, then the Graphify merge) rather than each source
apart, on devmem's own `devmem_app` package and on requests 2.32.3 and click 8.1.7 from PyPI (library code only: in their
`tests/` scip-python cannot resolve `import requests` / `import click`, so the oracle is blind there).

What changed:
- A bare Python call resolves only to what the file can see: a function of the same file, a from-import, or a star import
  (then as a guess). Before, a call to a local closure (`add`) or a builtin (`id`) matched any same-named function in the
  repo, even one in a minified JS bundle, and was marked `structural`.
- A method matched by name alone (receiver of unknown class) is searched among Python methods only, and is a guess
  (`ambiguous`) for generic verbs (`get`, `run`, ...) and regex methods (`search`, `match`, `sub`, ...), which are far more
  often a dict, a response or a compiled pattern than the one class in an imported file with that name.
- A receiver that is an imported library module or name (`from devmem import docs as core_docs`; `core_docs.search()`)
  gets no edge; an imported class used directly (`Store.default()`) resolves through that class.
- Graphify-only edges are added when Graphify resolved them (`EXTRACTED`). Its `INFERRED` / `AMBIGUOUS` links are added
  only from tests (an extra test is cheap, a missed one is not), and never from library code into a test.

| Final index, precision / recall | Before | After |
| --- | --- | --- |
| devmem_app, structural only (`--fast`) | 0.885 / 0.943 | 0.924 / 0.943 |
| devmem_app, with Graphify | 0.948 / 0.953 | 0.996 / 0.949 |
| requests library code, structural only | 0.839 / 0.905 | 0.843 / 0.909 |
| requests library code, with Graphify | 0.936 / 0.905 | 0.969 / 0.909 |
| click library code, structural only | 0.948 / 0.620 | 0.948 / 0.620 |
| click library code, with Graphify | 0.944 / 0.608 | 0.982 / 0.602 |

On click the old Graphify policy made library code slightly *worse* than no Graphify (0.948 to 0.944), because its
guesses were added unchecked (343 added, 342 of them guesses, some linking library code to test functions).

Graphify's own output (graph.json, its cache and manifest) is written to `~/.devmem/graphify/<repo>-<hash>/`
(`DEVMEM_HOME` moves it), never into the repo: Graphify's default is `<repo>/graphify-out/`. A `graphify-out/` folder
left in a repo by an older devmem can be deleted. When Graphify refuses to overwrite a graph that shrank (files deleted),
devmem rebuilds it with `--force` instead of reading the stale graph.

## Final check: eight codebases, and recall on real call sites (2026-11-19)

The final index (structural, then the Graphify merge), scored against scip-python / scip-typescript, library code only:

| Codebase | Precision | Recall (all SCIP references) | Recall (call sites only) |
| --- | --- | --- | --- |
| devmem_app | 0.996 | 0.949 | |
| requests 2.32.3 | 0.969 | 0.909 | |
| click 8.1.7 | 0.982 | 0.602 | |
| starlette 0.41.3 | 0.979 | 0.526 | 0.947 |
| httpx 0.28.1 | 0.989 | 0.503 | 0.964 |
| rich 13.9.4 | 0.982 | 0.511 | 0.964 |
| ky (TypeScript) | 0.917 | 0.790 | |
| zustand (TypeScript) | 1.000 | 0.509 | |

"Recall (all SCIP references)" is low on starlette, httpx and rich because SCIP also counts type annotations,
`isinstance` checks, `except X` and names passed around; devmem does not model those as call edges. Counting only the
references written as a call (`name(` on that line), devmem finds 95-96% of them. On ky the "wrong" edges are
`new TimeoutError(...)`: scip-typescript files them under the constructor and devmem under the class, so the edge is right.

Receiver classes are now also read from attribute chains (`self.console.line()`, `response.headers.get()`, looked up
through `self.x = ...` in the class, an annotation in the class body, or a property's return type, base classes
included) and from conditional values (`a if c else b`, `a or b`: one class when every side agrees). That raised
call-site recall from 0.958 / 0.944 / 0.955 to 0.964 / 0.947 / 0.964 (httpx / starlette / rich) with no loss of precision.

What the remaining real misses are: values produced by a generator's `yield`, loop variables over containers whose
element type is not declared, and receivers that are function results without a declared return type.

## Type references and subclasses (2026-11-19)

Most references scip-python reports that devmem had no edge for were not calls: a class named in a type hint, an
`isinstance` / `issubclass` check, an `except` clause or a bare `raise X`. Renaming or removing the class breaks those
functions as surely as its callers. They are now `uses` edges (Python), resolved through the file's imports like calls, and
`impact` lists them on their own line ("type references") next to the callers, plus every subclass of a class target.

| Library code, against scip-python | Recall: calls only | Recall: calls + `uses` | Precision of the new `uses` edges |
| --- | --- | --- | --- |
| devmem_app | 0.955 | 0.983 | 1.000 |
| requests 2.32.3 | 0.946 | 0.975 | 1.000 |
| click 8.1.7 | 0.613 | 0.929 | 0.987 |
| starlette 0.41.3 | 0.594 | 0.802 | 0.985 |
| httpx 0.28.1 | 0.531 | 0.782 | 1.000 |
| rich 13.9.4 | 0.551 | 0.775 | 0.953 |

The `uses` edges SCIP does not list are class-to-class field annotations (`class ConsoleOptions: size: ConsoleDimensions`),
which this measurement leaves out of the oracle on purpose; read by hand they are right. ("Recall: calls only" here counts
`refs` edges too and every SCIP-covered caller, so it differs slightly from the table above.) What is still missing is
mostly names passed around as values (`cls=Foo`, a class in a registry dict) and module-level aliases (`HeaderTypes = ...`).

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
ambiguous, add Graphify-only edges it resolved itself, and its guesses only from tests) -> SCIP (precise edges win; unconfirmed ambiguous edges dropped).
`devmem impact` prints each hop-1 edge with its confidence, and the skill tells agents to verify `ambiguous` ones.
