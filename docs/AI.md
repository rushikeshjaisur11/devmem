# What needs AI, and what works without it

devmem runs fully offline by default. Indexing, the code map, impact analysis, reuse search and agent prompts are plain
code: parsers, a call graph and keyword search (BM25). An LLM is optional. It only does three things: writes answers in
prose, ranks results by meaning, and writes one-line descriptions of functions for the team hub.

## Works without any AI

| Feature | Where | How it works |
| --- | --- | --- |
| Indexing (functions, calls, tests, tables) | Repos, `devmem-app index` | Parsers (tree-sitter, SCIP, Graphify), no model |
| What breaks if I change… (blast radius, tests to run) | Impact page, `devmem impact` | Walks the call graph |
| Code map (files or functions, cross-repo links) | Code map | Index, tables and import matching |
| Data flow (table and column lineage) | Data flow, `devmem lineage` | SQL/Spark parsing |
| Already built elsewhere (reuse search and fit check) | Reuse page, `devmem reuse`, `reuse` MCP tool | Keyword search, then rules for effort, missing packages, name clashes |
| Ask & plan: finding the code and docs that answer a question | Ask & plan | Keyword search; the answer is the matching snippets, each with its file and line |
| Ask & plan: turning a requirement or plan into an agent prompt | Ask & plan | Search plus templates; the prompt lists existing code, docs and paths not to touch |
| Lessons, rules, proposals | Lessons & rules, `devmem remember`/`recall` | Stored Markdown, matched by keywords |
| Library docs that follow the code | Library docs, `devmem docs` | Downloads `llms.txt`/package docs, then searches them by keywords |
| Code health, results, PR review, test gaps | Code health, Results, `devmem review`/`gaps` | Linters, git history, the index |
| Publishing the map and the AGENTS.md block | Repos, `publish` | Templates over the index |
| MCP server and agent CLI | `devmem mcp`, `devmem …` | Same offline commands |

## Better with AI (optional)

| Feature | Without AI | With AI | Turn it on |
| --- | --- | --- | --- |
| **Written answers** in Ask & plan | The matching snippets, quoted with file and line | A prose answer that cites those snippets as [1], [2]; citations that point at nothing are removed | An LLM key (below), then tick *Write answers with …* |
| **Search by meaning** (Ask & plan, reuse, plan prompts) | Keyword match: "retry" finds `retry`, not "make flaky calls reliable" | Keyword and meaning scores blended, so other wordings still match | Either `DEVMEM_LOCAL_EMBED=1` (small local model, **no API, runs offline on CPU** after one download) or `DEVMEM_VERTEX_EMBED_MODEL` with an LLM key |
| **Function descriptions in the team hub** | Hub search uses names, signatures and docstrings | The LLM writes a one-line description per function, so hub search matches what the code does | `hub push --summarize` (or *Describe functions with AI* on the Team hub page) with an LLM key |

If the LLM call fails (no network, bad key, missing package), each feature falls back to its offline result instead of
erroring. Ask & plan shows the reason.

## Configuring the LLM

Gemini on Vertex AI through `google-genai` (`pip install google-genai`). Use either one:

- **API key** (Vertex express mode): `DEVMEM_VERTEX_API_KEY=<key>` (`GOOGLE_API_KEY` also works). Gemini needs no secret.
- **Project + login**: `DEVMEM_VERTEX_PROJECT=<gcp project>` (optional `DEVMEM_VERTEX_LOCATION`, default `us-central1`) after
  `gcloud auth application-default login`.

Optional: `DEVMEM_VERTEX_MODEL` (default `gemini-2.5-flash`), `DEVMEM_VERTEX_EMBED_MODEL` for meaning-based search.

## What leaves your machine

Nothing, unless you configure an LLM. With one configured:
- written answers send your question and the retrieved snippets (code and docs) to Vertex;
- meaning-based search with a Vertex embed model sends the question and short text of each candidate (name, signature,
  docstring, and in Ask & plan the first ~300 characters of the code or doc);
- `--summarize` sends function source snippets.

`DEVMEM_LOCAL_EMBED` sends nothing; it downloads the model once and runs locally.
