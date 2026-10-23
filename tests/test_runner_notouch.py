import sys

from devmem import notouch
from devmem.ignore import Ignore
from devmem.runner import quote_all, run_cmd, summarize


def test_quote_all_round_trips_through_the_shell(tmp_path):
    args = ["plain", "has space", "tests/test_a.py::test_x", "it's"]
    code = "import sys; print(repr(sys.argv[1:]))"
    r = run_cmd(f'python -c "{code}" {quote_all(args)}', tmp_path)
    assert r["code"] == 0 and repr(args) in r["out"]


def test_run_cmd_uses_devmem_interpreter_and_reports_exit_code(tmp_path):
    r = run_cmd('python -c "import sys; print(sys.executable); sys.exit(3)"', tmp_path)
    assert r["code"] == 3 and sys.executable in r["out"]


def test_summarize_test_output_keeps_failures_and_totals():
    out = "noise\nFAILED tests/a.py::t1 - boom\n1 failed, 4 passed in 0.1s\n"
    assert summarize("test", out) == ["FAILED tests/a.py::t1 - boom", "1 failed, 4 passed in 0.1s"]


def test_no_touch_defaults_and_repo_patterns(tmp_path):
    (tmp_path / ".devmem").mkdir()
    (tmp_path / ".devmem/no-touch.txt").write_text("# c\nlegacy/\n", encoding="utf-8")
    pats = notouch.patterns(tmp_path)
    assert notouch.matches("db/migrations/001.sql", pats) == "migrations/"
    assert notouch.matches("legacy/x.py", pats) == "legacy/"
    assert notouch.matches("proto/a_pb2.py", pats) == "*_pb2.py"
    assert notouch.matches("src/app.py", pats) is None


def test_devmemignore_adds_to_defaults(tmp_path):
    (tmp_path / ".devmemignore").write_text("scratch/\n", encoding="utf-8")
    ig = Ignore.for_repo(tmp_path)
    assert ig.matches("scratch/a.py") and ig.matches("pkg/node_modules/x.js")
    assert not ig.matches("src/app.py")
