import json
import shutil
from pathlib import Path

from devmem import check as chk
from devmem.index import Index
from devmem_app import commands, hygiene, indexer, jsparse
from devmem_app.publisher import publish

FIX = Path(__file__).parent.parent / "fixtures"


def make(tmp_path):
    d = tmp_path / "js_app"
    shutil.copytree(FIX / "js_app", d)
    idx = indexer.index_repo(d, "js_app")
    return d, idx


def test_js_symbols_and_edges(tmp_path):
    d, idx = make(tmp_path)
    ids = {s["id"] for s in idx["symbols"]}
    assert {"src/retry.ts#retry", "src/client.ts#Client.get", "src/client.ts#Client", "src/legacy.js#legacyUrl"} <= ids
    assert "src/retry.ts#<module>" not in ids
    tests = [s for s in idx["symbols"] if s["is_test"]]
    assert {s["qualname"] for s in tests} == {"test: Client > builds delays", "test: Client > fetches", "test: retry > retries"}
    edges = {(e["src"], e["dst"], e["type"]) for e in idx["edges"]}
    assert ("src/client.ts#Client.get", "src/retry.ts#retry", "calls") in edges           # named import
    assert ("src/client.ts#Client.get", "src/client.ts#Client.raw", "calls") in edges     # this.method
    assert ("src/client.ts#Client.raw", "src/util.ts#join", "calls") in edges             # namespace import
    assert ("src/client.ts#describeClient", "src/format.ts#format", "calls") in edges     # default import renamed
    assert ("src/legacy.js#legacyUrl", "src/util.ts#join", "calls") in edges              # require destructuring
    assert ("tests/client.test.ts#test: retry > retries", "src/retry.ts#retry", "tests") in edges
    assert not any(e[1].endswith("#fn") for e in edges)                                   # parameter calls stay unresolved


def test_js_impact_and_test_selection(tmp_path):
    d, _ = make(tmp_path)
    ix = Index.load(d)
    imp = ix.impact("src/retry.ts#retry")
    assert "tests/client.test.ts#test: retry > retries" in imp["tests"]
    ids, cmds = chk.test_commands(ix, ["src/retry.ts"])
    assert ids == ["tests/client.test.ts"] and cmds == ["npm test -- tests/client.test.ts"]
    ids, _ = chk.test_commands(ix, ["src/format.ts"])
    assert ids == []


def test_js_commands_detected(tmp_path):
    d, _ = make(tmp_path)
    pj = json.loads((d / "package.json").read_text())
    c = commands.detect(d)
    assert c["test_one_js"]["cmd"] == "npx vitest run {tests}" and c["test"]["cmd"] == "npm test"
    assert pj["scripts"]["lint"] and c["lint"]["cmd"] == "npm run lint"
    (d / "tsconfig.json").write_text("{}")
    assert commands.detect(d)["types_js"]["cmd"] == "npx tsc --noEmit"


def test_publish_js_repo(tmp_path):
    d, idx = make(tmp_path)
    publish(d, idx)
    assert "retry" in (d / ".devmem/knowledge/map.md").read_text()


def test_js_skipped_without_tree_sitter(tmp_path, monkeypatch):
    monkeypatch.setattr(jsparse, "available", lambda: False)
    d = tmp_path / "js_app"
    shutil.copytree(FIX / "js_app", d)
    idx = indexer.index_repo(d, "js_app")
    assert idx["stats"]["js_skipped"] == 6 and not [s for s in idx["symbols"] if s["file"].endswith(".ts")]


def test_eslint_and_knip_parsers(tmp_path):
    es = json.dumps([{"filePath": str(tmp_path / "src/a.ts"), "messages": [
        {"ruleId": "no-var", "severity": 2, "message": "Unexpected var", "line": 2}, {"ruleId": None, "severity": 2, "message": "Parsing error", "line": 1}]}])
    got = hygiene.parse_eslint(es, tmp_path)
    assert [(i["path"], i["line"], i["code"]) for i in got] == [("src/a.ts", 2, "no-var"), ("src/a.ts", 1, "parse-error")]
    kn = json.dumps({"files": ["src/dead.ts"], "issues": [{"file": "src/client.ts", "exports": [{"name": "describeClient", "line": 17}],
                                                          "devDependencies": [{"name": "vitest"}], "unlisted": [], "dependencies": []}]})
    got = hygiene.parse_knip(kn)
    assert {(i["code"], i["path"]) for i in got} == {("unused-file", "src/dead.ts"), ("unused-export", "src/client.ts"), ("unused-dev-dependency", "src/client.ts")}


def test_hygiene_js_without_node_modules(tmp_path):
    d, idx = make(tmp_path)
    h = hygiene.run_hygiene(d, idx, history=False)
    assert h["tools"]["knip"].startswith("unavailable") and h["tools"]["eslint"].startswith("skipped")
    md = hygiene.render_hygiene_md(h)
    assert "| eslint |" in md and "| knip |" in md
