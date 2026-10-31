"""Knowledge publisher: index -> .devmem/knowledge/*, config/rules, AGENTS.md + Copilot managed blocks."""
from __future__ import annotations

import json

import subprocess
from collections import Counter, defaultdict
from pathlib import Path

from devmem.knowledge import (AGENTS_INDEX_BUDGET, KNOWLEDGE_DIR, check_knowledge, est_tokens,
                              replace_managed_block)
from devmem.schema import knowledge_header

from . import commands as cmdmod
from . import memory as memmod
from . import conventions
from .ranking import pagerank, render_map

DEFAULT_CONFIG = """# devmem repo config (reviewed like code)
diff_budget:
  max_changed_lines: 200
  max_files: 8
checks:
  out_of_scope_files: true
  format_only_changes: true
  new_dependencies: true
  deleted_tests: true
  api_signature_changes: true
  duplicate_helpers: true
  lint_fixes_outside_changed_lines: true
  no_touch_paths: true
"""

DEFAULT_RULES = """# Rules (global + repo)
- Change only what the task needs. No unrelated refactors or reformatting.
- Reuse existing helpers (`devmem helpers`, `devmem find`) before writing new code.
- No new dependencies. Keep public signatures stable unless the task requires it.
- Never edit paths listed in `.devmem/knowledge/no-touch.md`.
- If a rule blocks the correct fix, make the fix and say why in the PR description.
"""

PRECOMMIT = """# Merge into .pre-commit-config.yaml (runs inside Devin's session too, after `pip install devmem`)
repos:
  - repo: local
    hooks:
      - id: devmem-diffcheck
        name: devmem diffcheck
        entry: devmem diffcheck --staged
        language: system
        pass_filenames: false
        always_run: true
"""

GITHUB_CI = """name: devmem diffcheck
on: [pull_request]
jobs:
  diffcheck:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: {fetch-depth: 0}
      - uses: actions/setup-python@v5
        with: {python-version: "3.11"}
      - run: pip install devmem
      - run: devmem diffcheck --base origin/${{ github.base_ref }}
        env:
          DEVMEM_PR_BODY: ${{ github.event.pull_request.body }}
"""

AZURE_CI = """trigger: none
pr: [main]
steps:
  - checkout: self
    fetchDepth: 0
  - script: pip install devmem
  - script: devmem diffcheck --base origin/$(System.PullRequest.TargetBranch)
    displayName: devmem diffcheck
"""

AGENT_BLOCK = """## devmem (code knowledge, saves tokens)
1. `devmem recall "<task>"` once: lessons, rules, code other repos solved (`devmem reuse "<need>"`, `devmem docs "<need>"`).
2. Locate with `devmem find "<concept>"`, `show <symbol>`, `outline <file>`; never read whole files to explore.
3. `devmem impact <symbol|table.column>` before editing; change only what the task needs.
4. `devmem check`, then `devmem diffcheck` before each commit.
Follow `.devmem/rules.md`. More: `.devmem/knowledge/map.md` and topic files there, read only when relevant. Stale stamp: trust the code."""


def _write(root: Path, rel: str, body: str, commit: str | None, written: list[str]):
    text = body.rstrip() + "\n"
    text = knowledge_header(commit, est_tokens(text)) + "\n" + text
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    written.append(rel)


def _module_of(path: str) -> str:
    parts = path.split("/")
    if len(parts) == 1:
        return "(root)"
    return parts[1] if parts[0] in ("src", "lib") and len(parts) > 2 else parts[0]


def publish(root: str | Path, idx: dict, peers: dict | None = None) -> list[str]:
    root = Path(root).resolve()
    commit = idx.get("commit")
    written: list[str] = []
    k = KNOWLEDGE_DIR
    rank = pagerank(idx)
    by_id = {s["id"]: s for s in idx["symbols"]}

    # map.md
    map_text, _ = render_map(idx)
    _write(root, f"{k}/map.md", f"# Repo map: {idx['repo']}\nTop symbols by importance (line: signature). "
           f"Use `devmem show <name>` for bodies.\n\n```\n{map_text}\n```", commit, written)

    # overview.md
    langs = Counter(f["lang"] for f in idx["files"].values() if f["lang"] != "other")
    tests = [s for s in idx["symbols"] if s["is_test"]]
    mains = [s for s in idx["symbols"] if s["name"] == "main" and s["kind"] == "function"]
    tables = Counter(e["dst"][6:] for e in idx["edges"] if e["dst"].startswith("table:"))
    ov = [f"# Overview: {idx['repo']}", "", f"- Files by language: " + ", ".join(f"{l} {n}" for l, n in langs.most_common()),
          f"- Symbols: {sum(1 for s in idx['symbols'] if s['kind'] != 'module')}; tests: {len(tests)}"]
    if mains:
        ov.append("- Entry points: " + ", ".join(f"`{s['id']}`" for s in mains[:5]))
    ov += ["", "## Modules"]
    mods = defaultdict(list)
    for s in idx["symbols"]:
        if s["kind"] != "module" and not s["is_test"]:
            mods[_module_of(s["file"])].append(s)
    for m in sorted(mods):
        ov.append(f"- `{m}`: {len(mods[m])} symbols → modules/{m.strip('()')}.md")
    if tables:
        ov += ["", "## Tables touched"] + [f"- `{t}`" for t, _ in tables.most_common(15)]
    _write(root, f"{k}/overview.md", "\n".join(ov), commit, written)

    # module cards
    mod_files = defaultdict(set)
    for path in idx["files"]:
        mod_files[_module_of(path)].add(path)
    for m, syms in sorted(mods.items()):
        top = sorted(syms, key=lambda s: -rank.get(s["id"], 0))[:15]
        ext_out, ext_in = Counter(), Counter()
        for e in idx["edges"]:
            if e["type"] != "calls":
                continue
            s_, d_ = by_id.get(e["src"]), by_id.get(e["dst"])
            if not s_ or not d_:
                continue
            ms, md = _module_of(s_["file"]), _module_of(d_["file"])
            if ms == m and md != m:
                ext_out[md] += 1
            if md == m and ms != m and not s_["is_test"]:
                ext_in[ms] += 1
        card = [f"# Module `{m}`", f"Files: {', '.join(sorted(mod_files[m])[:12])}", "", "## Key symbols"]
        card += [f"- `{s['file']}:{s['line']}` {s['sig']}" + (f" — {s['doc']}" if s["doc"] else "") for s in top]
        if ext_out:
            card.append("\nDepends on: " + ", ".join(f"`{x}`" for x in ext_out))
        if ext_in:
            card.append("Used by: " + ", ".join(f"`{x}`" for x in ext_in))
        _write(root, f"{k}/modules/{m.strip('()')}.md", "\n".join(card), commit, written)

    # conventions.md + helpers.md
    conv = conventions.detect(idx, root)
    if conv:
        _write(root, f"{k}/conventions.md", conventions.render_conventions_md(conv), commit, written)
    _write(root, f"{k}/helpers.md", conventions.render_helpers_md(idx, root), commit, written)

    # commands.md + tests.md (commands.json keeps verification results across publishes)
    from devmem.check import load_flaky
    cmds = cmdmod.merge_with_existing(root, cmdmod.detect(root))
    cmdmod.save_commands(root, cmds)
    _write(root, f"{k}/commands.md", cmdmod.render_commands_md(cmds), commit, written)
    _write(root, f"{k}/tests.md", cmdmod.render_tests_md(idx, load_flaky(root)), commit, written)

    # hygiene.md (only after `devmem-app hygiene` has run; tools are not run during publish)
    hp = root / ".devmem/hygiene.json"
    if hp.exists():
        import json as _json
        from .hygiene import render_hygiene_md
        _write(root, f"{k}/hygiene.md", render_hygiene_md(_json.loads(hp.read_text(encoding="utf-8"))), commit, written)

    # lineage.md (+ lineage.json, schemas)
    from . import history as hist, lineage as lin_mod
    if any(r.get("sql") or r.get("df_columns") for r in idx["files"].values()):
        lin = lin_mod.write(root, idx)
        peers_lin = lin_mod.combine({n: p for n, p in (peers or {}).items() if Path(p).resolve() != root}) if peers else None
        _write(root, f"{k}/lineage.md", lin_mod.render_lineage_md(idx, lin, peers_lin, idx["repo"]), commit, written)

    # history.md (needs git; hotspots, co-change)
    h = hist.mine(root, idx)
    if h["commits_mined"]:
        _write(root, f"{k}/history.md", hist.render_history_md(h), commit, written)

    # memory: lessons.md, rules.md, cross-repo pointers
    from devmem import memory as m
    if m.load_lessons(root):
        _write(root, f"{k}/lessons.md", memmod.render_lessons_md(root), commit, written)
    if (root / m.RULES_DIR).exists() and m.load_rules(root):
        _write(root, f"{k}/rules.md", memmod.render_rules_md(root), commit, written)
    if peers:
        entries = memmod.build_cross_repo(idx["repo"], root, peers)
        if entries:
            _write(root, f"{k}/cross-repo.md", memmod.render_cross_repo_md(entries), commit, written)
        from . import reuse as reusemod
        if reusemod.write_catalog(idx["repo"], root, peers):
            written.append(".devmem/reuse-catalog.json")
    hub_url = None
    try:
        from . import hub as hubmod2
        hub_url = hubmod2.configured_url()
    except Exception:
        pass
    hp = root / ".devmem/hub.txt"
    if hub_url:
        hp.write_text("# central knowledge hub (git): `devmem hub sync` pulls it, `devmem reuse` searches it\n" + hub_url + "\n", encoding="utf-8")
        written.append(".devmem/hub.txt")
    elif hp.exists():
        hp.unlink()

    # library freshness + offline docs for the libraries this repo uses
    from . import docs as docsmod
    lp = root / ".devmem/libs.json"
    if lp.exists():
        try:
            _write(root, f"{k}/libraries.md", docsmod.render_libraries_md(json.loads(lp.read_text(encoding="utf-8"))), commit, written)
        except ValueError:
            pass
    got = docsmod.copy_relevant(root)
    if got:
        written.append(".devmem/docs/")

    # no-touch.md: defaults + detected + repo-owned .devmem/no-touch.txt
    from devmem import notouch
    nt = ["# No-touch paths", "Agents must not edit these (generated, vendored, migrations, lockfiles, secrets). "
          "`devmem diffcheck` fails edits to them. Add your own in `.devmem/no-touch.txt`."]
    nt += [f"- `{p}`" for p in notouch.patterns(root)]
    _write(root, f"{k}/no-touch.md", "\n".join(nt), commit, written)

    # config + rules: created once, then owned by the repo (never overwritten)
    for rel, body in ((".devmem/config.yaml", DEFAULT_CONFIG), (".devmem/rules.md", DEFAULT_RULES),
                      (".devmem/ci/pre-commit-snippet.yaml", PRECOMMIT), (".devmem/ci/github-actions.yml", GITHUB_CI),
                      (".devmem/ci/azure-pipelines.yml", AZURE_CI)):
        p = root / rel
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
            written.append(rel)

    # managed blocks
    assert est_tokens(AGENT_BLOCK) <= AGENTS_INDEX_BUDGET
    for rel in ("AGENTS.md", ".github/copilot-instructions.md"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(replace_managed_block(p.read_text(encoding="utf-8") if p.exists() else "", AGENT_BLOCK),
                     encoding="utf-8")
        written.append(rel)

    problems = check_knowledge(root)
    if problems:
        raise RuntimeError("knowledge budget/format problems: " + "; ".join(problems))
    return written


def commit_branch(root: str | Path, branch: str = "devmem/knowledge-sync") -> str:
    """Create/switch to branch and commit devmem files; pushing and opening the PR is left to you."""
    root = str(root)
    git = lambda *a: subprocess.run(["git", "-C", root, *a], capture_output=True, text=True, check=True).stdout.strip()
    git("checkout", "-B", branch)
    git("add", ".devmem", "AGENTS.md", ".github/copilot-instructions.md")
    git("commit", "-m", "devmem: knowledge sync")
    return branch
