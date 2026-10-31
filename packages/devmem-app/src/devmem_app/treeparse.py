"""Go and Java parsing with tree-sitter: symbols, imports and raw calls in the same record shape as jsparse, so graph.build_edges
can resolve them. Go: functions, methods (`Type.Method`), types; tests are `TestXxx`/`BenchmarkXxx` in `_test.go`. Java: classes,
interfaces, enums and records (nested ones as `Outer.Inner`), methods and constructors; tests carry @Test-style annotations."""
from __future__ import annotations

import re

from .jsparse import _sig, _t

_PARSERS: dict = {}
COMMENTS = {"comment", "line_comment", "block_comment"}
JAVA_TYPES = {"class_declaration", "interface_declaration", "enum_declaration", "record_declaration", "annotation_type_declaration"}
JAVA_TEST = {"Test", "ParameterizedTest", "RepeatedTest", "TestFactory"}


def available() -> bool:
    try:
        import tree_sitter_go  # noqa: F401
        import tree_sitter_java  # noqa: F401
        return True
    except ImportError:
        return False


def _parser(lang: str):
    if lang not in _PARSERS:
        from tree_sitter import Language, Parser
        if lang == "go":
            import tree_sitter_go as m
        else:
            import tree_sitter_java as m
        _PARSERS[lang] = Parser(Language(m.language()))
    return _PARSERS[lang]


def _doc(n) -> str:
    """First sentence line of the comment block right above a declaration (consecutive `//` lines are separate nodes)."""
    block, prev = [], n.prev_sibling
    while prev is not None and prev.type in COMMENTS and prev.end_point[0] >= (block[0] if block else n).start_point[0] - 1:
        block.insert(0, prev)
        prev = prev.prev_sibling
    for ln in "\n".join(_t(c) for c in block).splitlines():
        ln = re.sub(r"^\s*(/\*\*?|\*/|\*|//)", "", ln).strip().removesuffix("*/").strip()
        if ln and not ln.startswith("@"):
            return ln[:160]
    return ""


def parse(path: str, src: str, lang: str) -> tuple[dict, list[dict]]:
    lines = src.splitlines()
    rec = {"lang": lang, "imports": [], "tables": [], "calls": [], "lines": len(lines), "sql": [], "df_columns": []}
    syms: list[dict] = []
    tree = _parser(lang).parse(src.encode("utf-8"))

    def add(n, name: str, qual: str, kind: str, is_test=False, doc_node=None):
        syms.append({"id": f"{path}#{qual}", "name": name, "qualname": qual, "kind": kind, "file": path,
                     "line": n.start_point[0] + 1, "end_line": n.end_point[0] + 1, "sig": _sig(n, lines),
                     "doc": _doc(doc_node or n), "is_test": is_test})

    def calls(n, caller: str):
        if n.type == "call_expression":                                   # go: f(), pkg.F(), obj.M()
            fn = n.child_by_field_name("function")
            if fn is not None and fn.type == "identifier":
                rec["calls"].append({"caller": caller, "name": _t(fn), "recv": None, "line": n.start_point[0] + 1})
            elif fn is not None and fn.type == "selector_expression":
                op, fld = fn.child_by_field_name("operand"), fn.child_by_field_name("field")
                rec["calls"].append({"caller": caller, "name": _t(fld), "recv": _t(op)[:80], "line": n.start_point[0] + 1})
        elif n.type == "method_invocation":                               # java: m(), obj.m(), Cls.m()
            obj, nm = n.child_by_field_name("object"), n.child_by_field_name("name")
            rec["calls"].append({"caller": caller, "name": _t(nm), "recv": _t(obj)[:80] if obj is not None else None, "line": n.start_point[0] + 1})
        elif n.type == "object_creation_expression":                      # java: new Cls() -> its constructor
            ty = n.child_by_field_name("type")
            if ty is not None:
                name = _t(ty).split("<")[0].split(".")[-1]
                rec["calls"].append({"caller": caller, "name": name, "recv": None, "line": n.start_point[0] + 1})
        for c in n.children:
            calls(c, caller)

    if lang == "go":
        test_file = path.endswith("_test.go")
        for n in tree.root_node.named_children:
            if n.type == "import_declaration":
                for sp in [x for x in n.named_children if x.type == "import_spec"] + \
                          [y for x in n.named_children if x.type == "import_spec_list" for y in x.named_children if y.type == "import_spec"]:
                    p, al = sp.child_by_field_name("path"), sp.child_by_field_name("name")
                    mod = _t(p).strip('"`')
                    rec["imports"].append({"module": mod, "name": None, "alias": _t(al) if al is not None else mod.split("/")[-1]})
            elif n.type == "function_declaration":
                name = _t(n.child_by_field_name("name"))
                add(n, name, name, "function", test_file and re.match(r"(Test|Benchmark|Example|Fuzz)[A-Z_]?", name) is not None)
                calls(n.child_by_field_name("body") or n, name)
            elif n.type == "method_declaration":
                recv = n.child_by_field_name("receiver")
                ty = re.search(r"[A-Za-z_]\w*(?=\s*(\[[^\]]*\])?\s*\)?$)", _t(recv).strip("()")) if recv is not None else None
                name = _t(n.child_by_field_name("name"))
                qual = f"{ty.group(0)}.{name}" if ty else name
                add(n, name, qual, "method")
                calls(n.child_by_field_name("body") or n, qual)
            elif n.type == "type_declaration":
                for sp in n.named_children:
                    if sp.type == "type_spec":
                        nm = _t(sp.child_by_field_name("name"))
                        add(sp, nm, nm, "class", doc_node=n)
        return rec, syms

    test_file = "/test/" in f"/{path}" or path.endswith(("Test.java", "Tests.java", "IT.java"))

    def annotations(n) -> set[str]:
        mods = next((c for c in n.children if c.type == "modifiers"), None)
        return {_t(a.child_by_field_name("name")).split(".")[-1] for a in (mods.named_children if mods is not None else [])
                if a.type in ("marker_annotation", "annotation")}

    def java_type(n, prefix: str):
        name = _t(n.child_by_field_name("name"))
        qual = f"{prefix}.{name}" if prefix else name
        add(n, name, qual, "class")
        body = n.child_by_field_name("body")
        for m in (body.named_children if body is not None else []):
            if m.type in JAVA_TYPES:
                java_type(m, qual)
            elif m.type in ("method_declaration", "constructor_declaration", "compact_constructor_declaration"):
                mn = _t(m.child_by_field_name("name")) if m.child_by_field_name("name") is not None else name
                is_test = bool(annotations(m) & JAVA_TEST) or (test_file and mn.startswith("test"))
                add(m, mn, f"{qual}.{mn}", "method", is_test)
                calls(m.child_by_field_name("body") or m, f"{qual}.{mn}")

    for n in tree.root_node.named_children:
        if n.type == "import_declaration":
            text = _t(n).removeprefix("import").removesuffix(";").replace("static ", "").strip()
            mod, _, last = text.rpartition(".")
            rec["imports"].append({"module": mod, "name": None if last == "*" else last, "alias": None if last == "*" else last})
        elif n.type in JAVA_TYPES:
            java_type(n, "")
    return rec, syms


if __name__ == "__main__":   # self-check on the shapes the indexer relies on
    go = "package svc\n\nimport (\n\t\"fmt\"\n\tg \"github.com/x/gin\"\n)\n\n// Retry calls fn until it works.\n// More.\nfunc Retry(n int) error { return fmt.Errorf(\"x\") }\n\ntype Client struct{}\n\nfunc (c *Client) Get(p string) string { Retry(1); return c.join(p) }\n"
    r, s = parse("svc/retry.go", go, "go")
    assert {x["qualname"] for x in s} == {"Retry", "Client", "Client.Get"}, s
    assert s[0]["doc"] == "Retry calls fn until it works." and r["imports"][1] == {"module": "github.com/x/gin", "name": None, "alias": "g"}
    assert {"caller": "Client.Get", "name": "Retry", "recv": None, "line": 14} in r["calls"]
    _, s = parse("svc/retry_test.go", "package svc\nfunc TestRetry(t *testing.T) { Retry(1) }\n", "go")
    assert s[0]["is_test"]
    java = "package a;\nimport java.util.List;\n/** Users. */\npublic class S {\n  /** Loads. */\n  public U load(String id) { return Strings.trim(id); }\n  @Test void loads() { new S().load(\"1\"); }\n  static class In { void x() {} }\n}\n"
    r, s = parse("src/main/java/a/S.java", java, "java")
    assert {x["qualname"] for x in s} == {"S", "S.load", "S.loads", "S.In", "S.In.x"}, s
    assert next(x for x in s if x["qualname"] == "S.load")["doc"] == "Loads." and next(x for x in s if x["qualname"] == "S.loads")["is_test"]
    assert r["imports"] == [{"module": "java.util", "name": "List", "alias": "List"}]
    assert {"caller": "S.loads", "name": "S", "recv": None, "line": 7} in r["calls"]
    print("ok")
