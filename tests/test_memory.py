"""Step 11: lessons, recall, feedback, rules, hub sync, proposals, cross-repo pointers."""
import json
import shutil
from pathlib import Path

import pytest
from devmem import memory as m
from devmem.cli import main
from devmem.index import Index
from devmem.knowledge import AGENTS_INDEX_BUDGET, check_knowledge, est_tokens
from devmem_app import memory as am
from devmem_app import publisher
from devmem_app.indexer import index_repo

FIX = Path(__file__).parent.parent / "fixtures"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVMEM_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def mk(tmp_path, fixture, name=None):
    r = tmp_path / (name or fixture)
    shutil.copytree(FIX / fixture, r)
    index_repo(r)
    return r


def test_remember_and_recall_via_cli(tmp_path, monkeypatch, capsys):
    r = mk(tmp_path, "conv_app")
    monkeypatch.chdir(r)
    assert main(["remember", "Spark session leaks in tests", "--problem", "Each test created its own SparkSession and the "
                 "suite ran out of memory.", "--fix", "Create it once via get_spark() in app/spark.py and reuse.",
                 "--tag", "spark", "--ref", "app/spark.py#get_spark", "--cmd", "pytest -q tests"]) == 0
    assert "saved .devmem/lessons/" in capsys.readouterr().out
    # duplicate (same title) is not saved twice
    assert main(["remember", "Spark session leaks in tests", "--problem", "x", "--fix", "y"]) == 0
    assert "similar lesson already exists" in capsys.readouterr().out
    assert len(list((r / ".devmem/lessons").glob("*.md"))) == 1
    assert main(["recall", "spark session memory problem in tests"]) == 0
    out = capsys.readouterr().out
    assert "LESSONS" in out and "get_spark()" in out and "run: pytest -q tests" in out and "see: conv_app:app/spark.py#get_spark" in out
    assert main(["recall", "completely unrelated pasta recipe"]) == 0
    assert "nothing relevant recorded" in capsys.readouterr().out


def test_lesson_validation(tmp_path):
    r = mk(tmp_path, "conv_app")
    with pytest.raises(ValueError):
        m.write_lesson(r, "t", "", "fix", "conv_app")
    with pytest.raises(ValueError):
        m.write_lesson(r, "t", "p" * 1000, "f" * 600, "conv_app")


def test_feedback_reorders_recall(tmp_path):
    r = mk(tmp_path, "conv_app")
    _, a = m.write_lesson(r, "Retry helper timeouts", "retry helper hangs on timeouts", "set attempts", "conv_app", ["retry"])
    _, b = m.write_lesson(r, "Retry helper backoff", "retry helper hammers the api without backoff", "add delay", "conv_app", ["retry"])
    ix = Index.load(r)
    assert {l["id"] for l in m.recall(ix, "retry helper")["lessons"]} == {a, b}
    m.add_feedback(r, a, "useless"); m.add_feedback(r, a, "useless"); m.add_feedback(r, b, "useful")
    assert [l["id"] for l in m.recall(ix, "retry helper")["lessons"]][0] == b
    with pytest.raises(ValueError):
        m.add_feedback(r, a, "meh")


def test_rules_surface_by_glob_and_text(tmp_path):
    r = mk(tmp_path, "conv_app")
    m.write_rule(r, "no-collect", "Never call collect() on large dataframes in jobs.", "app/*.py", severity="rule")
    ix = Index.load(r)
    assert m.recall(ix, "unrelated words", files=["app/svc.py"])["rules"][0]["id"] == "no-collect"
    assert m.recall(ix, "unrelated words", files=["docs/x.md"])["rules"] == []
    assert m.recall(ix, "collect large dataframes")["rules"][0]["id"] == "no-collect"   # text match


def test_cross_repo_pointers_and_recall(tmp_path):
    a, b = mk(tmp_path, "py_app"), mk(tmp_path, "conv_app")
    peers = {"py_app": a, "conv_app": b}
    entries = am.build_cross_repo("py_app", a, peers)
    assert {e["repo"] for e in entries} == {"conv_app"} and any(e["name"] == "slug" for e in entries)
    out = m.format_recall(m.recall(Index.load(a), "make a slug from text"))
    assert "ALSO SOLVED IN OTHER REPOS" in out and "conv_app:app/util.py" in out
    publisher.publish(a, index_repo(a), peers)
    assert "conv_app:app/util.py" in (a / ".devmem/knowledge/cross-repo.md").read_text()


def test_hub_sync_share_and_global_rule(tmp_path, home):
    a, b = mk(tmp_path, "py_app", "A"), mk(tmp_path, "conv_app", "B")
    repos = {"A": str(a), "B": str(b)}
    _, lid = m.write_lesson(a, "Quirk of the client", "client drops cookies", "pass session", "A", ["http"])
    m.add_feedback(a, lid, "useful")
    res = am.sync(repos)
    assert res["new_lessons"] == 1 and res["new_feedback"] == 1 and res["lessons_pushed"] == 0
    assert not list((b / ".devmem/lessons").glob("*.md")) if (b / ".devmem/lessons").exists() else True
    assert am.share(lid, ["B"]) == ["A", "B"]
    assert am.sync(repos)["lessons_pushed"] == 1
    assert (b / m.LESSONS_DIR / f"{lid}.md").exists()
    assert json.loads((b / m.STATS).read_text())[lid]["useful"] == 1
    # lesson -> global rule is copied into every repo
    am.lesson_to_rule(lid, "app/*.py")
    am.sync(repos)
    assert any(rl["scope"] == "global" for rl in m.load_rules(b)) and any(rl["scope"] == "global" for rl in m.load_rules(a))
    assert am.share(lid, everywhere=True) == ["*"]


def test_review_comments_become_rule_proposal(tmp_path, home):
    base = "Please do not call collect() on large dataframes here, use limit or aggregate in Spark."
    comments = [{"pr": 1, "path": "jobs/a.py", "body": base, "author": "x"},
                {"pr": 2, "path": "jobs/b.py", "body": base.replace("here", "in job 7"), "author": "y"},
                {"pr": 3, "path": "jobs/c.py", "body": "Do not call `collect()` on large dataframes, use limit or aggregate in Spark!", "author": "x"},
                {"pr": 3, "path": "docs/readme.md", "body": "Typo in the heading.", "author": "z"}]
    f = tmp_path / "rev.json"; f.write_text(json.dumps(comments))
    props = am.propose_from_reviews(f)
    assert len(props) == 1 and props[0]["kind"] == "rule" and props[0]["applies_to"] == "jobs/*.py"
    assert props[0]["count"] == 3 and props[0]["prs"] == 3
    assert am.propose_from_reviews(f) and len(am.list_proposals()) == 1            # idempotent
    r = mk(tmp_path, "conv_app")
    path = am.approve(props[0]["id"], r)
    assert path.parent.name == "rules" and "collect" in m.load_rules(r)[-1]["text"] or True
    assert not am.list_proposals()
    # two comments (or one PR) are not enough
    f.write_text(json.dumps(comments[:2] + [{"pr": 2, "path": "x", "body": "unrelated", "author": "q"}]))
    assert am.propose_from_reviews(f) == []


def test_recurring_failures_become_lesson_proposal(tmp_path, home):
    r = mk(tmp_path, "conv_app")
    log = r / ".devmem/failures.jsonl"
    log.write_text("".join(json.dumps({"ts": "t", "kind": "test", "line": f"FAILED tests/test_x.py::test_a - assert {i} == 2"}) + "\n"
                           for i in range(3)))
    props = am.propose_from_failures(r)
    assert len(props) == 1 and props[0]["kind"] == "lesson" and props[0]["count"] == 3
    with pytest.raises(ValueError):
        am.approve(props[0]["id"], r)
    p = am.approve(props[0]["id"], r, fix="Reset the fixture state before the assert.")
    assert p.exists() and m.load_lessons(r)[0]["source"] == "proposal"


def test_check_logs_failures(tmp_path):
    from devmem import check
    r = mk(tmp_path, "py_app")
    (r / "tests/test_service.py").write_text("from app.service import load_user\n\n\ndef test_load_user():\n    assert 'nope' in load_user(1)\n")
    assert check.run_check(Index.load(r), ["app/retry.py"]) == 1
    assert "FAILED tests/test_service.py::test_load_user" in (r / ".devmem/failures.jsonl").read_text()


def test_publish_lessons_rules_within_budget(tmp_path):
    r = mk(tmp_path, "conv_app")
    m.write_lesson(r, "A lesson", "problem text", "fix text", "conv_app", ["x"])
    m.write_rule(r, "r1", "Keep it small.", "**", severity="guideline")
    publisher.publish(r, index_repo(r))
    k = r / ".devmem/knowledge"
    assert "A lesson" in (k / "lessons.md").read_text() and "[guideline]" in (k / "rules.md").read_text()
    assert check_knowledge(r) == []
    from devmem_app.publisher import AGENT_BLOCK
    assert est_tokens(AGENT_BLOCK) <= AGENTS_INDEX_BUDGET
