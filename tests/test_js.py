import json
import shutil
from pathlib import Path

from devmem import check as chk
from devmem.runner import quote_all
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


def test_js_doc_starts_at_first_line_of_a_comment_block():
    src = "// Retries a request with backoff.\n// Second line of detail.\nexport function retry() {}\n"
    _, syms = jsparse.parse_js("src/r.js", src)
    assert next(s for s in syms if s["name"] == "retry")["doc"] == "Retries a request with backoff."


def test_go_and_java_symbols_edges_and_test_commands(tmp_path):
    for app in ("go_app", "java_app"):
        shutil.copytree(FIX / app, tmp_path / app)
    gx = Index(indexer.index_repo(tmp_path / "go_app", "go_app"), tmp_path / "go_app")
    assert {"retry/retry.go#Do", "svc/client.go#Client", "svc/client.go#Client.Get", "svc/client_test.go#TestLoadUser"} <= set(gx.symbols)
    assert gx.symbols["retry/retry.go#Do"]["doc"] == "Do calls fn up to n times until it succeeds."
    r = gx.impact("retry/retry.go#Do")
    assert [h["id"] for h in r["hop1"]] == ["svc/client.go#Client.Get"] and "svc/client_test.go#TestLoadUser" in r["tests"]
    ids, cmd = chk.tests_for(gx, ["retry/retry.go"])
    assert cmd == "go test ./svc -run " + quote_all(["^(TestLoadUser)$"]) and "TestLoadUser" in ids[0]
    jx = Index(indexer.index_repo(tmp_path / "java_app", "java_app"), tmp_path / "java_app")
    run = "src/main/java/com/acme/Retry.java#Retry.run"
    assert jx.symbols[run]["doc"] == "Runs the task up to n times."
    r = jx.impact(run)
    assert [h["id"] for h in r["hop1"]] == ["src/main/java/com/acme/UserService.java#UserService.load"]
    assert "src/test/java/com/acme/UserServiceTest.java#UserServiceTest.loadsUser" in r["tests"]
    assert chk.tests_for(jx, ["src/main/java/com/acme/Retry.java"])[1] == "mvn -q test -Dtest=UserServiceTest#loadsUser"


def test_rust_and_csharp_index_to_edges_and_test_commands(tmp_path):
    r = tmp_path / "rs"
    (r / "src").mkdir(parents=True)
    (r / "src/lib.rs").write_text("/// Retries a call.\npub fn retry(n: u32) -> u32 { n }\n\npub fn load() -> u32 { retry(3) }\n\n"
                                  "#[cfg(test)]\nmod tests {\n    use super::*;\n    #[test]\n    fn loads() { load(); }\n}\n", encoding="utf-8")
    ix = Index(indexer.index_repo(r, "rs"), r)
    imp = ix.impact("src/lib.rs#retry")
    assert [h["id"] for h in imp["hop1"]] == ["src/lib.rs#load"] and "src/lib.rs#loads" in imp["tests"]
    assert chk.tests_for(ix, ["src/lib.rs"])[1] == "cargo test -- loads"
    c = tmp_path / "cs"
    c.mkdir()
    (c / "Svc.cs").write_text("namespace A {\n  public class Svc {\n    public int Load() { return Retry.Run(3); }\n  }\n"
                              "  public static class Retry { public static int Run(int n) { return n; } }\n}\n", encoding="utf-8")
    (c / "SvcTests.cs").write_text("namespace A {\n  public class SvcTests {\n    [Fact] public void Loads() { new Svc().Load(); }\n  }\n}\n", encoding="utf-8")
    ix = Index(indexer.index_repo(c, "cs"), c)
    imp = ix.impact("Svc.cs#Retry.Run")
    assert [h["id"] for h in imp["hop1"]] == ["Svc.cs#Svc.Load"] and "SvcTests.cs#SvcTests.Loads" in imp["tests"]
    assert "FullyQualifiedName~SvcTests.Loads" in chk.tests_for(ix, ["Svc.cs"])[1]
