# What needs AI, and what works without it

devmem runs fully offline by default. Indexing, the code map, impact analysis, reuse search and agent prompts are plain
code: parsers, a call graph and keyword search (BM25). An LLM is optional. It only does three things: writes answers in
prose, ranks results by meaning, and drafts one-line descriptions of code that has none.

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
| **Descriptions for low-confidence definitions** | Code with a vague name and no docstring is found only by its name; you can type a description yourself on *Improve descriptions* | The model drafts one sentence per selected function; you correct and approve them. Saved in `.devmem/descriptions.json` and searched like docstrings by `devmem find`, Ask & plan and agents | An LLM, then *Improve descriptions* → select → *Draft with …* |
| **AI analysis** (one pass per repo) | `overview.md` lists languages, entry points and modules by name | Adds a paragraph on what the repo does and two sentences per module, and drafts the descriptions above in bulk. Cached per code hash: a later run sends only what changed. Saved in `.devmem/analysis.json` | An LLM, then *AI analysis* → *Run analysis* |
| **Modernize: double-check** | The scan lists hand-written code a built-in replaces, matched by patterns and library docs | The model confirms or rejects each selected candidate and says what the replacement would not cover; it cannot add candidates | An LLM, then *Modernize* → select → *Double-check with …* |
| **Function descriptions in the team hub** | Hub search uses names, signatures and docstrings | The LLM writes a one-line description per function, so hub search matches what the code does | `hub push --summarize` (or *Describe functions with AI* on the Team hub page) with an LLM key |

If the LLM call fails (no network, bad key, missing package), each feature falls back to its offline result instead of
erroring. Ask & plan shows the reason.

## Configuring the LLM

Open **AI setup** in the web UI, pick a provider, paste the key and press *Save and test*. It takes effect at once; there
is nothing to install for the first three and no restart.

| Provider | Needs | Default model |
| --- | --- | --- |
| Claude (Anthropic API) | API key | `claude-haiku-5-5` |
| OpenAI or any OpenAI-compatible server (Ollama, LM Studio, OpenRouter, Azure) | API key, or only a server address for a local server | `gpt-4o-mini` |
| Gemini (Google AI Studio) | API key | `gemini-2.5-flash` |
| Gemini on Vertex AI | `pip install google-genai`, then an API key or a project with `gcloud auth application-default login` | `gemini-2.5-flash` |

The settings are stored in `~/.devmem/settings.json` (mode 0600); the key is never sent back to the browser.
Environment variables win over the page, for CI and servers: `DEVMEM_AI_PROVIDER`, `DEVMEM_AI_KEY`, `DEVMEM_AI_MODEL`,
`DEVMEM_AI_BASE_URL`, `DEVMEM_AI_EMBED_MODEL`. `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` or `GEMINI_API_KEY` alone also turn
their provider on, and the older `DEVMEM_VERTEX_*` variables keep working.

Only the mocked wire formats and an OpenAI-compatible test server are covered by the test suite; run *Save and test*
against your real provider before relying on it.

## What leaves your machine

Nothing, unless you configure an LLM. With one configured:
- written answers send your question and the retrieved snippets (code and docs) to the configured provider;
- meaning-based search with a provider's embedding model sends the question and short text of each candidate (name, signature,
  docstring, and in Ask & plan the first ~300 characters of the code or doc);
- *AI analysis* sends, per module, its file names, the signatures of its most used definitions and the names of the modules
  it calls; for the overview, the module summaries, languages, entry points and table names.
- `--summarize` and *Improve descriptions* send the first lines of each selected function, plus the names of what calls it
  and what it calls.

`DEVMEM_LOCAL_EMBED` sends nothing; it downloads the model once and runs locally.
