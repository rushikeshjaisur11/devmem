"""Token economy of agent-facing output: one-call context, grouped caller lists, distinct hits, capped answers."""
import json
import shutil
from pathlib import Path

from devmem import cli, context, mcp
from devmem.index import Index
from devmem_app import indexer

FIX = Path(__file__).parent.parent / "fixtures"


def _repo(tmp_path, name="py_app"):
    root = tmp_path / name
    shutil.copytree(FIX / name, root)
    indexer.index_repo(root, name, accuracy="fast")
    return root


def _run(capsys, root, *argv):
    capsys.readouterr()
    code = cli.run(list(argv), root=root)
    return code, capsys.readouterr().out


def test_grouped_prints_each_path_once():
    ids = ["a/x.py#f", "a/x.py#K.g", "b/y.py#h", "sql/q.sql"]
    assert context.grouped(ids) == "a/x.py: f, K.g; b/y.py: h; sql/q.sql"
    assert context.grouped(ids, 2) == "a/x.py: f, K.g (+2 more: --all)"
    assert context.grouped(ids[:2], tags={"a/x.py#f": "ambiguous"}) == "a/x.py: f [ambiguous], K.g"
    assert context.grouped([]) == "-"
    assert len(context.grouped(ids)) < len(", ".join(ids))


def test_impact_groups_callers_and_drops_what_it_repeated(tmp_path, capsys):
    root = _repo(tmp_path)
    code, out = _run(capsys, root, "impact", "retry")
    assert code == 0 and out.splitlines() == [
        "targets: app/retry.py#retry",
        "hop 1 (must check): app/client.py: ApiClient.get; app/service.py: load_user",
        "hop 2 (contract check): app/service.py: main",
        "tests: tests/test_service.py: test_load_user"]


def test_context_is_one_call_with_rules_focus_callers_tests_and_body(tmp_path, capsys, monkeypatch):
    root = _repo(tmp_path)
    (root / ".devmem/rules").mkdir(parents=True, exist_ok=True)
    from devmem import memory
    memory.write_rule(root, "no-sleep", "Never call time.sleep in retry code without a cap", "app/retry.py")
    (root / ".devmem/hygiene.json").write_text(json.dumps({"issues": [
        {"tool": "ruff", "path": "app/retry.py", "line": 6, "code": "B904", "msg": "raise without from"},
        {"tool": "ruff", "path": "app/retry.py", "line": 99, "code": "E501", "msg": "outside the function"}]}))
    code, out = _run(capsys, root, "context", "fix the retry helper")          # "fix" and "the" are not search words
    assert code == 0
    assert "RULES" in out and "Never call time.sleep" in out
    assert "FOCUS app/retry.py#retry (lines 5-" in out
    assert " callers (must still work): app/client.py: ApiClient.get; app/service.py: load_user" in out
    assert " tests: tests/test_service.py: test_load_user" in out
    assert "known issues here: ruff B904 line 6: raise without from" in out and "E501" not in out
    assert "BODY\ndef retry(" in out and out.rstrip().splitlines()[-1].startswith("NEXT:")
    separate = sum(len(_run(capsys, root, *a)[1]) for a in (["recall", "fix the retry helper"], ["find", "retry helper"], ["show", "retry"], ["impact", "retry"], ["hygiene", "app/retry.py"]))
    assert len(out) <= separate * 1.1    # about the size of the five answers it replaces even on a 9-line function (smaller on real code), in one round trip
    usage = [json.loads(l) for l in (root / ".devmem/usage.jsonl").read_text().splitlines()]
    assert usage[0]["cmd"] == "context" and usage[0]["file_tokens"] > 0
    monkeypatch.chdir(root)
    text, err = mcp.call("devmem_context", {"task": "fix the retry helper", "budget": 300})
    assert not err and "FOCUS app/retry.py#retry" in text


def test_context_respects_the_budget_and_never_cuts_rules(tmp_path, capsys):
    root = tmp_path / "r"
    (root / "pkg").mkdir(parents=True)
    body = "".join(f"    step_{i} = compute_value({i}) + another_long_expression_number({i})\n" for i in range(300))
    (root / "pkg/big.py").write_text(f"def reconcile_ledger(batch):\n{body}    return batch\n")
    indexer.index_repo(root, "r", accuracy="fast")
    ix = Index.load(root)
    small, large = context.build(ix, "reconcile ledger", budget=300), context.build(ix, "reconcile ledger", budget=1200)
    assert context.est(small) <= 330 and 900 <= context.est(large) <= 1250
    assert "more lines: `devmem show pkg/big.py#reconcile_ledger --full`" in small and "FOCUS pkg/big.py#reconcile_ledger" in small
    assert context.build(ix, "zzz qqq") .startswith("MATCHES: none")


def test_context_lists_candidates_instead_of_guessing_a_body(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    (root / "a.py").write_text("def parse_interval(x):\n    return x\n\n\ndef parse_expression(x):\n    return x\n\n\ndef parse_number(x):\n    return x\n")
    indexer.index_repo(root, "r", accuracy="fast")
    ix = Index.load(root)
    out = context.build(ix, "parse")
    assert out.startswith("CANDIDATES") and "BODY" not in out and "--symbol <path#name>" in out
    assert "a.py#parse_expression:5  def parse_expression(x)  [0 callers, 0 tests]" in out
    picked = context.build(ix, "parse", symbol="a.py#parse_number")                # naming one gets its body
    assert "FOCUS a.py#parse_number" in picked and "BODY\ndef parse_number(x):" in picked
    assert context.build(ix, "fix bug in parse interval").count("FOCUS a.py#parse_interval") == 1   # a clear winner is shown directly


def test_find_collapses_the_same_definition_across_files_and_prefers_rare_words(tmp_path, capsys):
    root = tmp_path / "r"
    (root / "dialects").mkdir(parents=True)
    for d in ("ansi", "mysql", "oracle", "tsql"):
        (root / f"dialects/{d}.py").write_text('class IntervalSegment:\n    """An interval segment."""\n\n\ndef parse_statement(x):\n    return x\n')
    (root / "core.py").write_text("def parse_interval(x):\n    return x\n")
    indexer.index_repo(root, "r", accuracy="fast")
    code, out = _run(capsys, root, "find", "parse interval", "-n", "3")
    lines = out.splitlines()
    assert lines[0] == "core.py:1  function parse_interval"                       # both words, and "interval" is the rarer one
    assert len(lines) == 3 and sum("IntervalSegment" in l for l in lines) == 1
    seg = next(l for l in lines if "IntervalSegment" in l)
    assert "(+3 same name: dialects/mysql.py:1, dialects/oracle.py:1, dialects/tsql.py:1)" in seg


def test_outline_folds_methods_of_large_files(tmp_path, capsys):
    root = tmp_path / "r"
    root.mkdir()
    methods = "".join(f"    def step_{i}(self):\n        return {i}\n\n" for i in range(90))
    (root / "big.py").write_text(f"class Engine:\n{methods}\ndef helper():\n    return 1\n")
    (root / "small.py").write_text("class A:\n    def m(self):\n        return 1\n")
    indexer.index_repo(root, "r", accuracy="fast")
    _, out = _run(capsys, root, "outline", "big.py")
    assert out.splitlines() == ["1: class Engine  [90 methods: `devmem outline big.py --in Engine`]", "273: def helper()"]
    _, inside = _run(capsys, root, "outline", "big.py", "--in", "Engine")
    assert len(inside.splitlines()) == cli.OUTLINE_CAP + 1 and "more:" in inside.splitlines()[-1]
    _, everything = _run(capsys, root, "outline", "big.py", "--all")
    assert len(everything.splitlines()) == 92
    _, small = _run(capsys, root, "outline", "small.py")
    assert small.splitlines() == ["1: class A", "    2: def m(self)"]               # short files are printed whole


def test_no_answer_exceeds_the_token_cap_unless_asked(tmp_path, capsys, monkeypatch):
    root = tmp_path / "r"
    root.mkdir()
    (root / "h.py").write_text("".join(f'def util_{i}(a, b, c):\n    """Utility number {i} does some shared work for callers."""\n    return a\n\n' for i in range(60)))
    indexer.index_repo(root, "r", accuracy="fast")
    monkeypatch.setenv("DEVMEM_MAX_TOKENS", "120")
    _, out = _run(capsys, root, "outline", "h.py")
    assert len(out) // 4 <= 160 and "cut at ~120 tokens" in out
    _, full = _run(capsys, root, "outline", "h.py", "--all")
    assert "cut at" not in full and len(full.splitlines()) == 60
    monkeypatch.setenv("DEVMEM_MAX_TOKENS", "0")
    assert "cut at" not in _run(capsys, root, "outline", "h.py")[1]


def test_mcp_tool_list_stays_small():
    tools = [{"name": n, **t[0]} for n, t in mcp.TOOLS.items()]
    assert list(mcp.TOOLS)[0] == "devmem_context"                                 # the first tool an agent reads is the one to start with
    assert len(json.dumps(tools)) // 4 <= 1050                                    # paid on every session, before any work
