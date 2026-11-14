"""Accuracy floors on code with hand-written ground truth: a change that resolves calls worse fails here.

`fixtures/accuracy_py` is built around the known traps (same method name on several classes, dict.items() next to a user
`items` method, aliased imports, constructors through a module alias, methods inherited through `self`, a function passed
by name). Its expected edges in `truth.json` were written by reading the code, not taken from devmem's output."""
import json
import shutil
from pathlib import Path

import pytest

from devmem import cli
from devmem.index import Index
from devmem_app import indexer, selfcheck

FIX = Path(__file__).parent.parent / "fixtures"
FLOORS = {"precision": 1.0, "recall": 1.0}        # measured 13/13 and 13/13; lower them only with a reason in the commit


def _norm(dst: str) -> str:
    return dst.rsplit(".__init__", 1)[0] if dst.endswith(".__init__") else dst


@pytest.fixture()
def acc(tmp_path):
    root = tmp_path / "acc"
    shutil.copytree(FIX / "accuracy_py", root)
    idx = indexer.index_repo(root, "acc", accuracy="fast")
    return root, idx, json.loads((FIX / "accuracy_py/truth.json").read_text())


def test_call_resolution_meets_the_floors(acc):
    _, idx, truth = acc
    want = {(a, b) for a, b, _ in truth["calls"]}
    got = {(e["src"], _norm(e["dst"])) for e in idx["edges"] if e["type"] in ("calls", "tests", "refs")}
    tp = want & got
    precision, recall = len(tp) / max(len(got), 1), len(tp) / len(want)
    why = {(a, b): w for a, b, w in truth["calls"]}
    assert precision >= FLOORS["precision"], f"wrong edges: {sorted(got - want)}"
    assert recall >= FLOORS["recall"], "missed: " + "; ".join(f"{a} -> {b} ({why[(a, b)]})" for a, b in sorted(want - got))
    assert not {(a, b) for a, b, _ in truth["not_calls"]} & got
    overrides = {(e["src"], e["dst"]) for e in idx["edges"] if e["type"] == "overrides"}
    assert overrides == {tuple(x) for x in truth["overrides"]}


def test_simple_fixtures_stay_exact(tmp_path):
    expected = {
        "go_app": {("svc/client.go#Client.Get", "retry/retry.go#Do"), ("svc/client.go#Client.Get", "svc/client.go#Client.raw"),
                   ("svc/client.go#LoadUser", "svc/client.go#Client.Get"), ("svc/client_test.go#TestLoadUser", "svc/client.go#LoadUser")},
        "java_app": {("src/main/java/com/acme/UserService.java#UserService.load", "src/main/java/com/acme/Retry.java#Retry.run"),
                     ("src/main/java/com/acme/UserService.java#UserService.load", "src/main/java/com/acme/UserService.java#UserService.fetch"),
                     ("src/test/java/com/acme/UserServiceTest.java#UserServiceTest.loadsUser", "src/main/java/com/acme/UserService.java#UserService.load")},
        "js_app": {("src/client.ts#Client.delay", "src/retry.ts#backoff"), ("src/client.ts#Client.get", "src/client.ts#Client.raw"),
                   ("src/client.ts#Client.get", "src/retry.ts#retry"), ("src/client.ts#Client.raw", "src/util.ts#join"),
                   ("src/client.ts#describeClient", "src/format.ts#format"), ("src/legacy.js#legacyUrl", "src/util.ts#join")},
    }
    for name, want in expected.items():
        root = tmp_path / name
        shutil.copytree(FIX / name, root)
        idx = indexer.index_repo(root, name, accuracy="fast")
        got = {(e["src"], e["dst"]) for e in idx["edges"] if e["type"] in ("calls", "tests")}
        extra = {(s, d) for s, d in got - want if not s.startswith("tests/")}       # js test blocks also reach constructors: not labelled here
        assert want <= got and not extra, f"{name}: extra {sorted(extra)}, missed {sorted(want - got)}"


def test_name_only_guesses_are_kept_out_of_must_check(tmp_path, capsys):
    root = tmp_path / "r"
    (root / "a").mkdir(parents=True)
    (root / "a/x.py").write_text("class A:\n    def close(self):\n        return 1\n\n\nclass B:\n    def close(self):\n        return 2\n")
    (root / "a/y.py").write_text("from a.x import A\n\n\ndef shut(thing):\n    return thing.close()\n\n\ndef use_a():\n    return A().close()\n")
    indexer.index_repo(root, "r", accuracy="fast")
    capsys.readouterr()
    cli.run(["impact", "a/x.py#A.close"], root=root)
    out = capsys.readouterr().out
    assert "hop 1 (must check): a/y.py: use_a\n" in out                                   # resolved through the constructor
    assert "possible callers (name match only" in out and "a/y.py: shut" in out           # `thing` has no known type


def test_selfcheck_reports_confirmation_call_sites_and_search(acc, tmp_path):
    root, _, _ = acc
    (root / ".devmem/search-eval.txt").write_text("# query => expected\nkeep a value under a key in the cache => app/store.py#Cache.put\nnonsense => app/nope.py#x\n")
    ix = Index.load(root)
    r = selfcheck.run(ix)
    e = r["edges"]
    assert e["edges"] == e["structural"] + e["ambiguous"] + e["precise"] + e["confirmed"] and e["languages"]["python"]["edges"] == e["edges"]
    assert e["independent"] == 0.0                                                            # fast mode: nothing confirmed by a second tool
    assert r["sites"]["checked"] > 0 and r["sites"]["rate"] == 1.0
    assert r["search"]["yours"] == 1 and r["search"]["queries"] >= 1                          # the unknown id is ignored
    assert json.loads((root / ".devmem/selfcheck.json").read_text())["history"][-1]["sites"] == 1.0

    (root / "app/store.py").write_text((root / "app/store.py").read_text().replace("return self._load(key)", "return key"))
    s2 = selfcheck.site_check(Index.load(root))                                               # the edge get -> _load is now false
    assert s2["missing"] >= 1 and "app/store.py#Store.get -> app/store.py#Store._load" in s2["examples"] and s2["rate"] < 1.0
