"""diffcheck: one synthetic diff per rule, plus the escape hatch."""
import subprocess
import shutil
from pathlib import Path

import pytest
from devmem import diffcheck as dc
from devmem_app.indexer import index_repo

FIX = Path(__file__).parent.parent / "fixtures" / "py_app"

OTHER = '''def other():
    """Unrelated."""
    return 1
'''


def git(root, *a):
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *a],
                   check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path):
    r = tmp_path / "r"
    shutil.copytree(FIX, r)
    (r / "app" / "other.py").write_text(OTHER)
    (r / "requirements.txt").write_text("pyyaml\n")
    (r / ".gitignore").write_text(".devmem/\n")
    git(r, "init", "-q"); git(r, "add", "-A"); git(r, "commit", "-qm", "base")
    index_repo(r)
    return r


def run(root, allow="", base=None, staged=False):
    cfg = dc.load_config(root)
    files = dc.collect_diff(root, base, staged)
    return dc.apply_allow(dc.run_checks(root, files, cfg, base), dc.allowances(allow))


def checks(res):
    return sorted({x.check for x in res[0]})


def edit(root, rel, old, new):
    p = root / rel
    p.write_text(p.read_text().replace(old, new))


def test_clean_change_passes(repo):
    edit(repo, "app/retry.py", "time.sleep(delay)", "time.sleep(delay * 2)")
    assert run(repo)[0] == []


def test_size_budget(repo):
    (repo / ".devmem").mkdir(exist_ok=True)
    (repo / ".devmem/config.yaml").write_text("diff_budget: {max_changed_lines: 2, max_files: 8}\n")
    edit(repo, "app/retry.py", "            time.sleep(delay)", "            a = 1\n            b = 2\n            c = 3\n            time.sleep(delay)")
    assert "size" in checks(run(repo))


def test_out_of_scope(repo):
    edit(repo, "app/retry.py", "            time.sleep(delay)", "            d = delay * 2\n            time.sleep(d)")
    edit(repo, "app/other.py", "return 1", "return 2")
    res = run(repo)
    assert checks(res) == ["out_of_scope"] and res[0][0].path == "app/other.py"


def test_format_only(repo):
    edit(repo, "app/retry.py", "time.sleep(delay)", "time.sleep(delay * 2)")
    edit(repo, "app/client.py", "self.base = base", "self.base  =  base")
    assert "format_only" in checks(run(repo))


def test_new_dependency(repo):
    (repo / "requirements.txt").write_text("pyyaml\nrequests>=2\n")
    assert checks(run(repo)) == ["new_dependency"]


def test_deleted_and_weakened_test(repo):
    (repo / "tests/test_service.py").write_text("from app.service import load_user\n\n\ndef test_load_user():\n    load_user(1)\n")
    res = run(repo)
    assert checks(res) == ["deleted_test"] and "weakened" in res[0][0].msg
    (repo / "tests/test_service.py").write_text("from app.service import load_user\n")
    assert any("removed" in x.msg for x in run(repo)[0])


def test_api_change_but_compatible_default_ok(repo):
    edit(repo, "app/retry.py", "def retry(fn, attempts=3, delay=0.1):", "def retry(fn, attempts=3, delay=0.1, jitter=0):")
    assert run(repo)[0] == []
    edit(repo, "app/retry.py", "jitter=0)", "jitter)")
    assert "api_change" in checks(run(repo))


def test_duplicate_helper(repo):
    (repo / "app/other.py").write_text(OTHER + '''

def again(fn, attempts=3, delay=0.1):
    """Call fn, retrying on exception."""
    for i in range(attempts):
        try:
            return fn()
        except Exception:
            if i == attempts - 1:
                raise
            time.sleep(delay)
''')
    res = run(repo)
    assert any(x.check == "duplicate" and "app/retry.py#retry" in x.msg for x in res[0])
    (repo / "app/other.py").write_text(OTHER + "\n\ndef retry(x):\n    return x\n")
    assert any("already exists" in x.msg for x in run(repo)[0])


def test_escape_hatch(repo):
    edit(repo, "app/retry.py", "def retry(fn, attempts=3, delay=0.1):", "def retry(fn):")
    assert "api_change" in checks(run(repo))
    blocked, passed = run(repo, allow="Fix.\ndevmem-allow: api_change callers updated in follow-up PR")
    assert not [x for x in blocked if x.check == "api_change"]
    assert passed and "follow-up" in passed[0].allowed


def test_staged_and_base_modes(repo):
    edit(repo, "app/retry.py", "time.sleep(delay)", "time.sleep(delay * 2)")
    edit(repo, "app/other.py", "return 1", "return 2")
    git(repo, "add", "app/retry.py")
    assert run(repo, staged=True)[0] == []          # only the in-scope file is staged
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "wip")
    assert "out_of_scope" in checks(run(repo, base="HEAD~1"))


def test_cli_exit_codes(repo, monkeypatch, capsys):
    from devmem.cli import main
    monkeypatch.chdir(repo)
    edit(repo, "app/retry.py", "time.sleep(delay)", "time.sleep(delay * 2)")
    assert main(["diffcheck"]) == 0
    (repo / "requirements.txt").write_text("pyyaml\nrequests>=2\n")
    assert main(["diffcheck"]) == 1
    assert "FAIL    [new_dependency]" in capsys.readouterr().out
    monkeypatch.setenv("DEVMEM_PR_BODY", "devmem-allow: new_dependency needed for http client")
    assert main(["diffcheck"]) == 0


def test_review_summarises_changed_symbols_and_gaps_lists_untested_code(tmp_path, monkeypatch, capsys):
    import shutil
    import subprocess
    from pathlib import Path
    from devmem.cli import main
    from devmem_app.indexer import index_repo
    repo = tmp_path / "py_app"
    shutil.copytree(Path(__file__).parent.parent / "fixtures/py_app", repo)
    g = ["git", "-c", "user.email=a@b", "-c", "user.name=a"]
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(g + ["add", "-A"], cwd=repo, check=True)
    subprocess.run(g + ["commit", "-qm", "base"], cwd=repo, check=True)
    index_repo(repo)
    p = repo / "app/retry.py"
    p.write_text(p.read_text(encoding="utf-8").replace("time.sleep(delay)", "time.sleep(delay * 2)"), encoding="utf-8")
    monkeypatch.chdir(repo)
    assert main(["review"]) == 0
    out = capsys.readouterr().out
    assert "### devmem review" in out and "`app/retry.py#retry`" in out and "Affected tests" in out
    assert main(["gaps"]) == 0
    out = capsys.readouterr().out
    assert "no test reaches it" in out or "no untested code" in out
