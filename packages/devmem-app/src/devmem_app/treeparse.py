"""Go, Java, C#, Kotlin and Rust parsing with tree-sitter: symbols, imports and raw calls in the same record shape as jsparse, so
graph.build_edges can resolve them.
- Go: functions, methods (`Type.Method`), types; tests are `TestXxx`/`BenchmarkXxx` in `_test.go`.
- Java / C# / Kotlin: classes, interfaces, enums, records, objects (nested ones as `Outer.Inner`), methods and constructors;
  tests carry @Test / [Fact] / [Test]-style annotations.
- Rust: functions, structs/enums/traits, methods in `impl Type` blocks (`Type.method`); tests carry #[test]."""
from __future__ import annotations

import re

from .jsparse import _sig, _t

_PARSERS: dict = {}
COMMENTS = {"comment", "line_comment", "block_comment"}
ATTRS = {"attribute_item", "attribute_list"}   # between a doc comment and its declaration (rust #[..], c# [..])
OO_TYPES = {"class_declaration", "interface_declaration", "enum_declaration", "record_declaration", "annotation_type_declaration",
            "struct_declaration", "record_struct_declaration", "object_declaration"}
OO_METHODS = {"method_declaration", "constructor_declaration", "compact_constructor_declaration", "function_declaration",
              "secondary_constructor"}
TEST_ANNOTATIONS = {"Test", "ParameterizedTest", "RepeatedTest", "TestFactory", "Fact", "Theory", "TestMethod", "TestCase"}
GRAMMARS = {"go": ("tree_sitter_go", "language"), "java": ("tree_sitter_java", "language"), "csharp": ("tree_sitter_c_sharp", "language"),
            "kotlin": ("tree_sitter_kotlin", "language"), "rust": ("tree_sitter_rust", "language")}
LANGS = set(GRAMMARS)


def available() -> bool:
    import importlib.util
    return all(importlib.util.find_spec(m) for m, _ in GRAMMARS.values())


def _parser(lang: str):
    if lang not in _PARSERS:
        import importlib

        from tree_sitter import Language, Parser
        mod, fn = GRAMMARS[lang]
        _PARSERS[lang] = Parser(Language(getattr(importlib.import_module(mod), fn)()))
    return _PARSERS[lang]


def _doc(n) -> str:
    """First line of the comment block right above a declaration (consecutive `//` lines are separate nodes; attributes such as
    #[inline] or [Fact] may sit between the comment and the declaration)."""
    block, prev, top = [], n.prev_sibling, n
    while prev is not None and (prev.type in COMMENTS or prev.type in ATTRS) and prev.end_point[0] >= top.start_point[0] - 1:
        if prev.type in COMMENTS:
            block.insert(0, prev)
        top, prev = prev, prev.prev_sibling
    for ln in "\n".join(_t(c) for c in block).splitlines():
        ln = re.sub(r"<[^>]+>", "", re.sub(r"^\s*(/\*\*?|\*/|\*|//[/!]?)", "", ln)).strip().removesuffix("*/").strip()
        if ln and not ln.startswith("@"):
            return ln[:160]
    return ""


def _attrs_before(n) -> str:
    """Text of the attribute nodes right above a declaration (rust #[test], c# [Fact] when not inside the node)."""
    out, prev = [], n.prev_sibling
    while prev is not None and (prev.type in ATTRS or prev.type in COMMENTS):
        if prev.type in ATTRS:
            out.append(_t(prev))
        prev = prev.prev_sibling
    return " ".join(out)


def parse(path: str, src: str, lang: str) -> tuple[dict, list[dict]]:
    lines = src.splitlines()
    rec = {"lang": lang, "imports": [], "tables": [], "calls": [], "lines": len(lines), "sql": [], "df_columns": []}
    syms: list[dict] = []
    tree = _parser(lang).parse(src.encode("utf-8"))

    def add(n, name: str, qual: str, kind: str, is_test=False, doc_node=None):
        syms.append({"id": f"{path}#{qual}", "name": name, "qualname": qual, "kind": kind, "file": path,
                     "line": n.start_point[0] + 1, "end_line": n.end_point[0] + 1, "sig": _sig(n, lines),
                     "doc": _doc(doc_node or n), "is_test": is_test})

    def call(caller, n, name, recv):
        if name:
            rec["calls"].append({"caller": caller, "name": name, "recv": recv[:80] if recv else None, "line": n.start_point[0] + 1})

    def calls(n, caller: str):
        t = n.type
        if t in ("call_expression", "invocation_expression"):
            fn = n.child_by_field_name("function")
            if fn is None and n.named_children:                           # kotlin: no field names
                fn = n.named_children[0]
            if fn is not None:
                ft = fn.type
                if ft in ("identifier", "simple_identifier"):
                    call(caller, n, _t(fn), None)
                elif ft == "selector_expression":                        # go: pkg.F() / obj.M()
                    call(caller, n, _t(fn.child_by_field_name("field")), _t(fn.child_by_field_name("operand")))
                elif ft == "field_expression":                           # rust: obj.m()
                    call(caller, n, _t(fn.child_by_field_name("field")), _t(fn.child_by_field_name("value")))
                elif ft == "scoped_identifier":                          # rust: Type::new()
                    p = fn.child_by_field_name("path")
                    call(caller, n, _t(fn.child_by_field_name("name")), _t(p) if p is not None else None)
                elif ft == "member_access_expression":                   # c#: obj.M() / Cls.M()
                    call(caller, n, _t(fn.child_by_field_name("name")), _t(fn.child_by_field_name("expression")))
                elif ft == "navigation_expression" and fn.named_children:  # kotlin: obj.m()
                    call(caller, n, _t(fn.named_children[-1]).lstrip("."), _t(fn.named_children[0]))
        elif t == "method_invocation":                                    # java: m(), obj.m(), Cls.m()
            obj = n.child_by_field_name("object")
            call(caller, n, _t(n.child_by_field_name("name")), _t(obj) if obj is not None else None)
        elif t == "object_creation_expression":                           # java / c#: new Cls() -> its constructor
            ty = n.child_by_field_name("type")
            if ty is not None:
                call(caller, n, _t(ty).split("<")[0].split(".")[-1], None)
        for c in n.children:
            calls(c, caller)

    def body_of(n):
        return n.child_by_field_name("body") or next((c for c in n.children if c.type in ("class_body", "function_body", "declaration_list", "enum_class_body", "block")), None)

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

    if lang == "rust":
        def items(parent, in_tests: bool):
            for n in parent.named_children:
                if n.type == "use_declaration":
                    arg = n.child_by_field_name("argument")
                    text = _t(arg) if arg is not None else ""
                    mod, _, last = text.rpartition("::")
                    rec["imports"].append({"module": mod, "name": None if last in ("*", "") or "{" in last else last,
                                           "alias": None if last in ("*", "") or "{" in last else last})
                elif n.type == "function_item":
                    name = _t(n.child_by_field_name("name"))
                    add(n, name, name, "function", "test" in _attrs_before(n) or in_tests and name.startswith("test"))
                    calls(n.child_by_field_name("body") or n, name)
                elif n.type in ("struct_item", "enum_item", "trait_item", "union_item"):
                    name = _t(n.child_by_field_name("name"))
                    add(n, name, name, "class")
                elif n.type == "impl_item":
                    ty = _t(n.child_by_field_name("type")).split("<")[0].split("::")[-1]
                    body = n.child_by_field_name("body")
                    for m in (body.named_children if body is not None else []):
                        if m.type == "function_item":
                            mn = _t(m.child_by_field_name("name"))
                            add(m, mn, f"{ty}.{mn}", "method", "test" in _attrs_before(m))
                            calls(m.child_by_field_name("body") or m, f"{ty}.{mn}")
                elif n.type == "mod_item" and n.child_by_field_name("body") is not None:
                    items(n.child_by_field_name("body"), in_tests or "test" in _attrs_before(n))
        items(tree.root_node, path.startswith("tests/") or "/tests/" in path)
        return rec, syms

    # java, csharp, kotlin: classes and their members
    test_file = "/test/" in f"/{path}" or bool(re.search(r"(Tests?|IT|Spec)\.(java|cs|kt)$", path))

    def annotations(n) -> set[str]:
        names = set()
        for c in n.children:
            if c.type in ("modifiers", "attribute_list"):
                names |= set(re.findall(r"[@\[,]\s*([A-Za-z_][\w.]*)", _t(c)))
        names |= set(re.findall(r"\[\s*([A-Za-z_][\w.]*)", _attrs_before(n)))
        return {x.split(".")[-1] for x in names}

    def oo_type(n, prefix: str):
        nm = n.child_by_field_name("name") or next((c for c in n.named_children if c.type in ("identifier", "type_identifier", "simple_identifier")), None)
        if nm is None:
            return
        name = _t(nm)
        qual = f"{prefix}.{name}" if prefix else name
        add(n, name, qual, "class")
        body = body_of(n)
        for m in (body.named_children if body is not None else []):
            if m.type in OO_TYPES:
                oo_type(m, qual)
            elif m.type in OO_METHODS:
                mnode = m.child_by_field_name("name") or next((c for c in m.named_children if c.type in ("identifier", "simple_identifier")), None)
                mn = _t(mnode) if mnode is not None else name
                is_test = bool(annotations(m) & TEST_ANNOTATIONS) or (test_file and mn.lower().startswith("test"))
                add(m, mn, f"{qual}.{mn}", "method", is_test)
                calls(body_of(m) or m, f"{qual}.{mn}")

    def top(parent):
        for n in parent.named_children:
            if n.type in ("import_declaration", "using_directive", "import"):
                text = re.sub(r"^(import|using)\s+(static\s+)?", "", _t(n)).removesuffix(";").strip()
                text = text.split("=")[-1].strip()   # c#: using Alias = A.B;
                mod, _, last = text.rpartition(".")
                rec["imports"].append({"module": mod, "name": None if last == "*" else last, "alias": None if last == "*" else last})
            elif n.type in OO_TYPES:
                oo_type(n, "")
            elif n.type == "function_declaration":                          # kotlin top-level function
                nm = n.child_by_field_name("name") or next((c for c in n.named_children if c.type in ("identifier", "simple_identifier")), None)
                if nm is not None:
                    name = _t(nm)
                    add(n, name, name, "function", bool(annotations(n) & TEST_ANNOTATIONS))
                    calls(body_of(n) or n, name)
            elif n.type in ("namespace_declaration", "file_scoped_namespace_declaration"):   # c#
                b = n.child_by_field_name("body")
                top(b if b is not None else n)
            elif n.type == "declaration_list":
                top(n)
    top(tree.root_node)
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
    cs = "using System.Text;\nnamespace Acme {\n  /// <summary>Users.</summary>\n  public class Svc {\n    /// <summary>Loads one.</summary>\n    public string Load(string id) { Retry.Run(3); return id; }\n    [Fact] public void LoadsUser() { new Svc().Load(\"1\"); }\n  }\n}\n"
    r, s = parse("src/Svc.cs", cs, "csharp")
    assert {x["qualname"] for x in s} == {"Svc", "Svc.Load", "Svc.LoadsUser"}, s
    assert next(x for x in s if x["qualname"] == "Svc.Load")["doc"] == "Loads one." and next(x for x in s if x["qualname"] == "Svc.LoadsUser")["is_test"]
    assert {"caller": "Svc.Load", "name": "Run", "recv": "Retry", "line": 6} in r["calls"] and r["imports"][0]["module"] == "System"
    kt = "package acme\nimport acme.util.Strings\n/** Users. */\nclass Svc {\n    /** Loads. */\n    fun load(id: String) = Strings.trim(id)\n    @Test fun loadsUser() { Svc().load(\"1\") }\n}\nfun top() = helper()\n"
    r, s = parse("src/Svc.kt", kt, "kotlin")
    assert {x["qualname"] for x in s} == {"Svc", "Svc.load", "Svc.loadsUser", "top"}, s
    assert next(x for x in s if x["qualname"] == "Svc.loadsUser")["is_test"] and next(x for x in s if x["qualname"] == "Svc.load")["doc"] == "Loads."
    assert {"caller": "Svc.load", "name": "trim", "recv": "Strings", "line": 6} in r["calls"]
    rs = "use crate::util::join;\n/// Retries a call.\npub fn retry(n: u32) -> u32 { join(n) }\nstruct Client;\nimpl Client {\n    /// Gets.\n    pub fn get(&self) { retry(3); self.raw(); }\n}\n#[cfg(test)]\nmod tests {\n    use super::*;\n    #[test]\n    fn retries() { retry(1); }\n}\n"
    r, s = parse("src/lib.rs", rs, "rust")
    assert {x["qualname"] for x in s} == {"retry", "Client", "Client.get", "retries"}, s
    assert s[0]["doc"] == "Retries a call." and next(x for x in s if x["qualname"] == "retries")["is_test"]
    assert {"caller": "Client.get", "name": "raw", "recv": "self", "line": 7} in r["calls"] and r["imports"][0]["name"] == "join"
    print("ok")
