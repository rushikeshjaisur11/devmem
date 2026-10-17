"""JavaScript/TypeScript parsing with tree-sitter (optional: `pip install devmem-app[js]`).

Produces the same file record and symbols as the Python parser: functions, classes, methods, arrow-function consts,
jest/vitest tests as pseudo-symbols (`it("x")` inside `describe("y")`), imports (ES and require), calls per caller."""
from __future__ import annotations

import re

_PARSERS: dict = {}
TEST_CALLS = {"it", "test"}
SUITE_CALLS = {"describe", "suite"}
TEST_MODIFIERS = {"only", "skip", "todo", "concurrent", "each"}
EXTS = {".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".ts": "typescript",
        ".tsx": "tsx", ".mts": "typescript", ".cts": "typescript"}


def available() -> bool:
    try:
        import tree_sitter  # noqa: F401
        import tree_sitter_javascript  # noqa: F401
        import tree_sitter_typescript  # noqa: F401
        return True
    except ImportError:
        return False


def _parser(kind: str):
    if kind not in _PARSERS:
        from tree_sitter import Language, Parser
        if kind == "javascript":
            import tree_sitter_javascript as m
            lang = Language(m.language())
        else:
            import tree_sitter_typescript as m
            lang = Language(m.language_tsx() if kind == "tsx" else m.language_typescript())
        _PARSERS[kind] = Parser(lang)
    return _PARSERS[kind]


def is_test_file(path: str) -> bool:
    p = path.replace("\\", "/")
    base = p.rsplit("/", 1)[-1]
    return "/__tests__/" in f"/{p}" or bool(re.search(r"\.(test|spec)\.[cm]?[jt]sx?$", base))


def _t(n) -> str:
    return n.text.decode("utf-8", "replace") if n is not None else ""


def _str(n) -> str:
    s = _t(n)
    return s[1:-1] if len(s) >= 2 and s[0] in "'\"`" else s


def _sig(n, src_lines: list[str]) -> str:
    line = src_lines[n.start_point[0]].strip()
    return re.sub(r"\s*\{.*$", "", line)[:160]


def _doc(n) -> str:
    prev = n.prev_sibling
    if prev is not None and prev.type == "comment" and prev.end_point[0] >= n.start_point[0] - 1:
        text = _t(prev)
        for ln in text.strip("/* \n").splitlines():
            ln = ln.strip(" */")
            if ln and not ln.startswith("@"):
                return ln[:160]
    return ""


def _callee(fn):
    """(name, recv) for a call's function node."""
    if fn.type == "identifier":
        return _t(fn), None
    if fn.type == "member_expression":
        prop, obj = fn.child_by_field_name("property"), fn.child_by_field_name("object")
        return _t(prop), _t(obj)[:80]
    return None, None


def parse_js(path: str, src: str) -> tuple[dict, list[dict]]:
    ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
    kind = EXTS.get(ext, "javascript")
    lang = "ts" if kind != "javascript" else "js"
    lines = src.splitlines()
    rec = {"lang": lang, "imports": [], "tables": [], "calls": [], "lines": len(lines), "sql": [], "df_columns": [],
           "exports": [], "default_export": None}
    syms: list[dict] = []
    tree = _parser(kind).parse(src.encode("utf-8"))
    test_file = is_test_file(path)
    seen_ids: set[str] = set()

    def add(n, name: str, qual: str, symkind: str, is_test=False, sig=None):
        sid = f"{path}#{qual}"
        if sid in seen_ids:
            return sid
        seen_ids.add(sid)
        syms.append({"id": sid, "name": name, "qualname": qual, "kind": symkind, "file": path,
                     "line": n.start_point[0] + 1, "end_line": n.end_point[0] + 1,
                     "sig": sig or _sig(n, lines), "doc": _doc(n), "is_test": is_test})
        return sid

    def scan(n, caller: str, suites: tuple = ()):
        if n.type == "call_expression":
            fn = n.child_by_field_name("function")
            name, recv = _callee(fn) if fn is not None else (None, None)
            args = n.child_by_field_name("arguments")
            argn = [c for c in (args.named_children if args is not None else []) if c.type != "comment"]
            if name == "require" and recv is None and argn and argn[0].type == "string":
                pass  # handled at declaration sites
            base = name if recv is None else None
            if fn is not None and fn.type == "member_expression":  # it.only / describe.skip / test.each(...)(...)
                o = fn.child_by_field_name("object")
                if o is not None and o.type == "identifier" and _t(fn.child_by_field_name("property")) in TEST_MODIFIERS:
                    base = _t(o)
            if fn is not None and fn.type == "call_expression":  # test.each(table)("name", fn)
                inner = fn.child_by_field_name("function")
                if inner is not None and inner.type == "member_expression" and _t(inner.child_by_field_name("property")) == "each":
                    base = _t(inner.child_by_field_name("object"))
            cb = next((c for c in argn[1:] if c.type in ("arrow_function", "function_expression", "function")), None)
            if base in (TEST_CALLS | SUITE_CALLS) and argn and argn[0].type in ("string", "template_string") and cb is not None:
                title = _str(argn[0])
                if base in SUITE_CALLS:
                    scan_children(cb, caller, suites + (title,))
                else:
                    qual = "test: " + " > ".join(suites + (title,))
                    sid = add(n, title, qual, "function", True, sig=f"{base}(\"{title}\")")
                    scan_children(cb, qual, suites)
                return
            if name:
                rec["calls"].append({"caller": caller, "name": name, "recv": recv, "line": n.start_point[0] + 1})
        elif n.type == "new_expression":
            c = n.child_by_field_name("constructor")
            if c is not None and c.type == "identifier":
                rec["calls"].append({"caller": caller, "name": _t(c), "recv": None, "line": n.start_point[0] + 1})
        scan_children(n, caller, suites)

    def scan_children(n, caller, suites):
        for c in n.children:
            scan(c, caller, suites)

    def fn_symbol(n, name, exported=False, default=False):
        add(n, name, name, "function")
        if exported:
            rec["exports"].append(name)
        if default:
            rec["default_export"] = name
        body = n.child_by_field_name("body")
        scan(body if body is not None else n, name)

    def class_symbol(n, exported=False, default=False):
        nm = n.child_by_field_name("name")
        name = _t(nm) if nm is not None else "default"
        add(n, name, name, "class", sig=_sig(n, lines))
        if exported:
            rec["exports"].append(name)
        if default:
            rec["default_export"] = name
        body = n.child_by_field_name("body")
        for m in (body.named_children if body is not None else []):
            if m.type in ("method_definition", "public_field_definition", "field_definition"):
                mn = m.child_by_field_name("name")
                val = m.child_by_field_name("value") if m.type != "method_definition" else m
                if mn is None or val is None or (m.type != "method_definition" and val.type not in ("arrow_function", "function_expression")):
                    continue
                q = f"{name}.{_t(mn)}"
                add(m, _t(mn), q, "method")
                scan(val.child_by_field_name("body") or val, q)

    def declaration(n, exported=False, default=False) -> bool:
        t = n.type
        if t in ("function_declaration", "generator_function_declaration", "function_signature"):
            nm = n.child_by_field_name("name")
            fn_symbol(n, _t(nm) if nm is not None else "default", exported, default)
            return True
        if t in ("class_declaration", "abstract_class_declaration"):
            class_symbol(n, exported, default)
            return True
        if t in ("lexical_declaration", "variable_declaration"):
            for d in n.named_children:
                if d.type != "variable_declarator":
                    continue
                nm, val = d.child_by_field_name("name"), d.child_by_field_name("value")
                if val is None:
                    continue
                if nm is not None and nm.type == "identifier" and val.type in ("arrow_function", "function_expression", "function"):
                    add(d, _t(nm), _t(nm), "function", sig=_sig(d, lines))
                    if exported:
                        rec["exports"].append(_t(nm))
                    scan(val.child_by_field_name("body") or val, _t(nm))
                elif val.type == "call_expression" and _require(d, nm, val):
                    pass
                else:
                    scan(val, "<module>")
            return True
        return False

    def _require(d, nm, call) -> bool:
        fn = call.child_by_field_name("function")
        args = call.child_by_field_name("arguments")
        if fn is None or _t(fn) != "require" or args is None or not args.named_children or args.named_children[0].type != "string":
            return False
        mod = _str(args.named_children[0])
        if nm.type == "identifier":
            rec["imports"].append({"module": mod, "name": None, "alias": _t(nm)})
        elif nm.type == "object_pattern":
            for p in nm.named_children:
                if p.type == "shorthand_property_identifier_pattern":
                    rec["imports"].append({"module": mod, "name": _t(p), "alias": _t(p)})
                elif p.type == "pair_pattern":
                    k, v = p.child_by_field_name("key"), p.child_by_field_name("value")
                    rec["imports"].append({"module": mod, "name": _t(k), "alias": _t(v)})
        return True

    def import_stmt(n):
        src_n = n.child_by_field_name("source")
        if src_n is None:
            return
        mod = _str(src_n)
        clause = next((c for c in n.named_children if c.type == "import_clause"), None)
        if clause is None:
            return
        for c in clause.named_children:
            if c.type == "identifier":
                rec["imports"].append({"module": mod, "name": "default", "alias": _t(c)})
            elif c.type == "namespace_import":
                ident = next((x for x in c.named_children if x.type == "identifier"), None)
                rec["imports"].append({"module": mod, "name": None, "alias": _t(ident)})
            elif c.type == "named_imports":
                for sp in c.named_children:
                    if sp.type == "import_specifier":
                        nm, al = sp.child_by_field_name("name"), sp.child_by_field_name("alias")
                        rec["imports"].append({"module": mod, "name": _t(nm), "alias": _t(al) if al is not None else _t(nm)})

    module_nodes = []
    for n in tree.root_node.named_children:
        if n.type == "comment":
            continue
        if n.type == "import_statement":
            import_stmt(n)
        elif n.type == "export_statement":
            decl = n.child_by_field_name("declaration")
            is_default = any(c.type == "default" for c in n.children)
            if decl is not None and declaration(decl, True, is_default):
                continue
            val = n.child_by_field_name("value")
            if val is not None:
                if val.type == "identifier":
                    rec["default_export"] = _t(val)
                elif val.type in ("arrow_function", "function_expression"):
                    add(n, "default", "default", "function")
                    rec["default_export"] = "default"
                    scan(val.child_by_field_name("body") or val, "default")
                else:
                    scan(val, "<module>"); module_nodes.append(n)
            else:  # export { a, b } [from './x']
                for c in n.named_children:
                    if c.type == "export_clause":
                        for sp in c.named_children:
                            nm = sp.child_by_field_name("name")
                            if nm is not None:
                                rec["exports"].append(_t(nm))
                src_n = n.child_by_field_name("source")
                if src_n is not None:
                    for c in n.named_children:
                        if c.type == "export_clause":
                            for sp in c.named_children:
                                nm, al = sp.child_by_field_name("name"), sp.child_by_field_name("alias")
                                rec["imports"].append({"module": _str(src_n), "name": _t(nm), "alias": _t(al) if al is not None else _t(nm)})
        elif declaration(n):
            continue
        elif n.type in ("interface_declaration", "type_alias_declaration", "enum_declaration"):
            nm = n.child_by_field_name("name")
            if nm is not None:
                add(n, _t(nm), _t(nm), "class", sig=_sig(n, lines))
        else:
            scan(n, "<module>")
            module_nodes.append(n)
    if module_nodes or any(c["caller"] == "<module>" for c in rec["calls"]):
        syms.append({"id": f"{path}#<module>", "name": "<module>", "qualname": "<module>", "kind": "module", "file": path,
                     "line": 1, "end_line": 1, "sig": f"module {path}", "doc": "", "is_test": False})
    rec["exports"] = sorted(set(rec["exports"]))
    return rec, syms
