"""Edge resolution: raw calls/tables -> graph edges with a confidence per edge.

Confidence ladder: precise (SCIP, later) > structural (import/name resolved) > ambiguous > co-change.
Where several sources give the same edge, the highest confidence wins.
"""
from __future__ import annotations

import re

from devmem.jsresolve import module_files
from devmem.jsresolve import resolve as _js_resolve
from devmem.schema import CONF_AMBIGUOUS, CONF_STRUCTURAL

RANK = {"precise": 3, "structural": 2, "ambiguous": 1, "co-change": 0}
MAX_AMBIGUOUS = 3
# Method names that collide with builtins (dict/list/str/set): `x.items()` matched to some user class that happens to
# define `items` was wrong 9 times in 10 on psf/requests, so these never resolve by name alone.
BUILTIN_METHODS = {"items", "values", "keys", "pop", "popitem", "setdefault", "update", "copy", "clear", "append", "extend",
                   "insert", "remove", "add", "discard", "join", "split", "strip", "format", "encode", "decode", "replace",
                   "lower", "upper", "sort", "index", "count", "find", "startswith", "endswith", "push", "slice", "map",
                   "filter", "reduce", "forEach", "toString"}
# Generic verbs (also on files, clients, loggers): the candidate must live in the caller's file or a file it imports.
COMMON_METHODS = {"get", "send", "run", "start", "stop", "load", "dump", "dumps", "loads", "read", "write", "close", "open",
                  "then", "catch", "json", "text",
                  "search", "match", "fullmatch", "findall", "finditer", "sub", "group"}   # also every compiled regex


def _module_files(files: dict, module: str) -> list[str]:
    """Files whose path ends with the dotted module (tolerates src/ roots)."""
    return module_files(files, module)


def _js_files(files: dict, importer: str, spec: str) -> list[str]:
    return _js_resolve(files, importer, spec)


def _js_cands(path, rec, c, files, by_id, by_name, by_file, local, from_imp, mod_alias) -> list[dict]:
    name, recv = c["name"], c["recv"]
    if recv is None:
        if name in from_imp:
            imp = from_imp[name]
            out = []
            for f in _js_files(files, path, imp["module"]):
                syms = by_file.get(f, {})
                if imp["name"] == "default":
                    d = files[f].get("default_export")
                    out += [syms[d]] if d in syms else []
                else:
                    out += [s for q, s in syms.items() if s["name"] == imp["name"] and "." not in q]
            return out
        if name in local and "." not in name:
            return [local[name]]
        return [s for s in by_name.get(name, []) if s["kind"] != "method"]
    head = recv.split(".")[0].split("(")[0]
    if recv == "this" and "." in c["caller"]:
        q = f"{c['caller'].split('.')[0]}.{name}"
        return [local[q]] if q in local else []
    if head in mod_alias:
        out = []
        for f in _js_files(files, path, mod_alias[head]):
            out += [s for q, s in by_file.get(f, {}).items() if s["name"] == name and "." not in q]
        return out
    if head in from_imp or head == "this" or recv.startswith("new "):
        out = [] if name in BUILTIN_METHODS else [s for s in by_name.get(name, []) if s["kind"] == "method"]
        if name in COMMON_METHODS:
            near = {path} | {f for i in rec.get("imports", []) for f in _js_files(files, path, i["module"])}
            out = [s for s in out if s["file"] in near]
        return out
    return []  # unknown receiver (builtin, parameter, library): leave it unresolved


def _imported_files(files: dict, path: str, imports: list[dict]) -> set[str]:
    out = {path}
    for i in imports:
        out.update(_module_files(files, i["module"]))
        if i["name"]:
            out.update(_module_files(files, f"{i['module']}.{i['name']}"))
    return out


def _typed_edges(path, rec, by_id, by_name, by_file, add) -> None:
    """Go and Java. Unqualified calls resolve within the file, then the same package (directory); `pkg.F()` / `Cls.m()` through
    the file's imports; `obj.m()` to methods of that name (ambiguous when several). No global guess for unqualified names."""
    pkg = path.rsplit("/", 1)[0] if "/" in path else ""
    aliases = {i["alias"]: i for i in rec.get("imports", []) if i.get("alias")}
    local = by_file.get(path, {})
    same_pkg = lambda s: (s["file"].rsplit("/", 1)[0] if "/" in s["file"] else "") == pkg
    for c in rec.get("calls", []):
        src_id = f"{path}#{c['caller']}"
        if src_id not in by_id:
            continue
        name, recv = c["name"], c["recv"]
        if recv is None:
            cls = c["caller"].rsplit(".", 1)[0] if "." in c["caller"] else None
            cands = [local[f"{cls}.{name}"]] if cls and f"{cls}.{name}" in local else [s for q, s in local.items() if s["name"] == name]
            if not cands:
                cands = [s for s in by_name.get(name, []) if same_pkg(s) and (s["kind"] != "method" or s["qualname"] == f"{name}.{name}")]
        elif recv in aliases and rec["lang"] == "go":                     # pkg.Func: functions in the imported package's directory
            tail = aliases[recv]["module"].split("/")[-1]
            cands = [s for s in by_name.get(name, []) if s["kind"] != "method" and s["file"].rsplit("/", 2)[-2:-1] == [tail]]
        else:   # Cls.m() / Type.M(): methods of a class with that name win; else any method of that name
            new = re.match(r"new\s+([\w.]+)", recv)                     # java: new Cls().m()
            cls = (new.group(1) if new else recv).split(".")[-1]
            cands = [s for s in by_name.get(name, []) if s["qualname"].split(".")[-2:-1] == [cls]]
            if not cands and name not in BUILTIN_METHODS and name not in COMMON_METHODS:
                cands = [s for s in by_name.get(name, []) if s["kind"] == "method"]
            if len(cands) > 1:
                cands = [s for s in cands if same_pkg(s)] or cands
        if not cands or len(cands) > MAX_AMBIGUOUS:
            continue
        conf = CONF_AMBIGUOUS if len(cands) > 1 else CONF_STRUCTURAL
        for d in cands:
            if d["id"] != src_id:
                add(src_id, d["id"], "tests" if by_id[src_id]["is_test"] else "calls", conf)


def build_edges(idx: dict) -> list[dict]:
    files = idx["files"]
    by_id = {s["id"]: s for s in idx["symbols"]}
    by_name: dict[str, list[dict]] = {}
    by_file: dict[str, dict[str, dict]] = {}
    for s in idx["symbols"]:
        if s["kind"] == "module":
            continue
        by_name.setdefault(s["name"], []).append(s)
        by_file.setdefault(s["file"], {})[s["qualname"]] = s

    best: dict[tuple, str] = {}

    def add(src: str, dst: str, typ: str, conf: str):
        k = (src, dst, typ)
        if k not in best or RANK[conf] > RANK[best[k]]:
            best[k] = conf

    # ---- receiver types (Python): which class a variable holds, so `x.get()` resolves to one class, not every class with get ----
    def base_class(cls: dict, name: str) -> dict | None:
        rec = files.get(cls["file"], {})
        simple = name.split(".")[-1]
        loc = by_file.get(cls["file"], {}).get(simple)
        if loc and loc["kind"] == "class" and "." not in name:
            return loc
        for i in rec.get("imports", []):
            if i["name"] and i["alias"] == name.split(".")[0] and "." not in name:
                for f in _module_files(files, i["module"]):
                    hit = by_file.get(f, {}).get(i["name"])
                    if hit and hit["kind"] == "class":
                        return hit
            if not i["name"] and "." in name and i["alias"] == name.split(".")[0]:
                for f in _module_files(files, i["module"]):
                    hit = by_file.get(f, {}).get(simple)
                    if hit and hit["kind"] == "class":
                        return hit
        return None


    def resolve_ref(path: str, text: str) -> dict | None:
        """`Session`, `h.Session`, `LRU`, `make_store` as written in `path` -> the class or function it names (via imports or the
        same file only: no global guess)."""
        rec = files.get(path, {})
        imports = rec.get("imports", [])
        text = text.replace("()", "")
        if "." in text:
            head, rest = text.split(".", 1)
            for i in imports:
                if i["alias"] == head:
                    mod = f"{i['module']}.{i['name']}" if i["name"] else i["module"]
                    for f in _module_files(files, mod):
                        hit = by_file.get(f, {}).get(rest)
                        if hit:
                            return hit
            if "." not in rest:                       # `Store.default`: a method (often an alternative constructor) of a class
                owner = resolve_ref(path, head)
                if owner and owner["kind"] == "class":
                    return method_of(owner, rest)
            return None
        loc = by_file.get(path, {}).get(text)
        if loc and loc["kind"] in ("class", "function"):
            return loc
        for i in imports:
            if i["name"] and i["alias"] == text:
                for f in _module_files(files, i["module"]):
                    hit = by_file.get(f, {}).get(i["name"])
                    if hit and hit["kind"] in ("class", "function"):
                        return hit
        return None

    BUILTIN_TYPES = {"open", "dict", "list", "set", "tuple", "str", "bytes", "int", "float", "frozenset", "bytearray", "iter", "enumerate", "zip",
                     "map", "filter", "sorted", "reversed", "range", "object", "super", "type", "memoryview", "defaultdict", "OrderedDict", "Counter", "deque"}

    def external(path: str, ref: str) -> bool:
        """True when `ref` is a builtin or comes from a library outside the repo: an object of that type has no method here."""
        ref = ref.replace("()", "")
        head = ref.split(".")[0]
        if "." not in ref and ref in BUILTIN_TYPES:
            return True
        for i in files.get(path, {}).get("imports", []):
            if i["alias"] == head:
                mod = i["module"] if not i["name"] else f"{i['module']}.{i['name']}"
                return not (_module_files(files, mod) or _module_files(files, i["module"]))
        return False

    def type_of(path: str, ref: str, depth: int = 0) -> dict | None:
        """The class a constructor call or a function call written as `ref` produces."""
        return produced(resolve_ref(path, ref), depth)

    def produced(t: dict | None, depth: int = 0) -> dict | None:
        """The class you hold after calling `t`: the class itself, or what a function or method is declared to return."""
        if t is None or depth > 2:
            return None
        if t["kind"] == "class":
            return t
        if t.get("returns"):
            if t["kind"] == "method" and t["returns"] in ("cls", "Self", "self", "typing.Self", t["qualname"].split(".")[0]):
                owner = by_file.get(t["file"], {}).get(t["qualname"].rsplit(".", 1)[0])       # `return cls(...)`, `-> Self`
                return owner if owner and owner["kind"] == "class" else None
            return type_of(t["file"], t["returns"], depth + 1)
        return None

    children: dict[str, list[dict]] = {}

    def in_subclasses(cls: dict, name: str) -> list[dict]:
        """`name` as defined by classes that inherit from `cls`: a receiver declared as the base class (then narrowed with
        isinstance, or holding any implementation) calls one of these."""
        if not children:
            children[""] = []
            for c in idx["symbols"]:
                if c["kind"] == "class":
                    for b in c.get("bases", []):
                        bc = base_class(c, b)
                        if bc:
                            children.setdefault(bc["id"], []).append(c)
        out, seen, queue = [], {cls["id"]}, [cls]
        while queue and len(seen) < 200:
            for sub in children.get(queue.pop(0)["id"], []):
                if sub["id"] in seen:
                    continue
                seen.add(sub["id"])
                m = by_file.get(sub["file"], {}).get(f"{sub['qualname']}.{name}")
                if m:
                    out.append(m)
                else:
                    queue.append(sub)             # not defined here: its own subclasses may define it
        return out

    def method_of(cls: dict, name: str, depth: int = 0) -> dict | None:
        """`name` on `cls`, or inherited from its bases (first base first)."""
        hit = by_file.get(cls["file"], {}).get(f"{cls['qualname']}.{name}")
        if hit or depth > 5:
            return hit
        for b in cls.get("bases", []):
            bc = base_class(cls, b)
            if bc:
                m = method_of(bc, name, depth + 1)
                if m:
                    return m
        return None

    EXTERNAL = {"id": "<external>"}       # a value whose class lives outside the repo

    def ref_class(path: str, ref: str):
        t = type_of(path, ref)
        return t if t is not None else (EXTERNAL if ref and external(path, ref) else None)

    MISSING = {"id": "<never assigned>"}
    assign_idx: dict[str, tuple[dict, dict]] = {}

    def assigns_of(path: str) -> tuple[dict, dict]:
        """({function: {var: [assignments, top to bottom]}}, {(class, "self.x"): [assignments]}) for one file."""
        if path not in assign_idx:
            by_caller: dict = {}
            by_attr: dict = {}
            for a in sorted(files.get(path, {}).get("assigns", []), key=lambda x: x["line"]):
                if a["var"].startswith("self."):
                    by_attr.setdefault((a["caller"].split(".")[0], a["var"]), []).append(a)
                else:
                    by_caller.setdefault(a["caller"], {}).setdefault(a["var"], []).append(a)
            assign_idx[path] = (by_caller, by_attr)
        return assign_idx[path]

    def agreed(types: list):
        """One class when every assignment that says something says the same; None when they disagree or none is known."""
        found = None
        for t in types:
            if t is None:
                continue
            if found is not None and found["id"] != t["id"]:
                return None
            found = t
        return found

    def self_attr(path: str, cls_name: str, attr: str, what: str = "type", depth: int = 0):
        """The class held by `self.x` (what="type"), or by its elements ("elem") / mapping values ("val"), from the
        assignments to it in the class's methods: `self.x = Foo()`, `self.x = param`, `self.x: Foo | None = None`."""
        if depth > 12:
            return None
        out = []
        for a in assigns_of(path)[1].get((cls_name, attr), []):
            if what == "type":
                out.append(typed(path, a, depth + 1))
            elif a.get(what):
                out.append(ref_class(path, a[what]))
            elif a.get("from"):
                out.append(part_of(path, a["caller"], a["from"], what, a["line"], depth + 1))
        return agreed(out)

    def window(path: str, caller: str, var: str, line: int) -> list[dict]:
        """The assignments to `var` that decide what it holds at `line`: the last one above it, plus, while that one sits in an
        if / loop / try, the ones before it back to the last unconditional one. Used above its first assignment (a loop): that one."""
        hist = assigns_of(path)[0].get(caller, {}).get(var) or []
        prev = [a for a in hist if a["line"] < line]
        if not prev:
            return [a for a in hist[:1] if a["line"] != line]
        out = []
        for a in reversed(prev):
            out.append(a)
            if not a.get("cond"):
                break
        return out

    def var_at(path: str, caller: str, var: str, line: int, depth: int = 0):
        """What the local `var` holds at `line` of `caller`: a class, EXTERNAL, None (unknown) or MISSING (not a local here)."""
        if depth > 12:
            return None
        win = window(path, caller, var, line)
        params = (by_file.get(path, {}).get(caller) or {}).get("params") or {}
        if not win:
            return ref_class(path, params[var]) if var in params else MISSING
        types = [typed(path, a, depth + 1) for a in win]
        if win[-1].get("cond") and var in params:         # only conditional assignments: the parameter's own class may still be there
            types.append(ref_class(path, params[var]))
        return None if any(t is None for t in types) else agreed(types)

    def part_of(path: str, caller: str, name: str, what: str, line: int, depth: int = 0):
        """Class of the elements (what="elem") or mapping values (what="val") of the variable `name` at `line` of `caller`."""
        if depth > 12:
            return None
        if name.startswith("self."):
            return self_attr(path, caller.split(".")[0], name, what, depth + 1) if "." in caller else None
        win = window(path, caller, name, line)
        if not win:
            ref = ((by_file.get(path, {}).get(caller) or {}).get("elems" if what == "elem" else "vals") or {}).get(name)
            return ref_class(path, ref) if ref else None
        if len(win) != 1:
            return None
        if win[0].get(what):
            return ref_class(path, win[0][what])
        return part_of(path, caller, win[0]["from"], what, win[0]["line"], depth + 1) if win[0].get("from") else None

    def holder(path: str, caller: str, recv: str, line: int, depth: int = 0):
        """The class of a receiver written as `self`, `self.x` or a variable name; None when it cannot be told."""
        cls_name = caller.split(".")[0] if "." in caller else None
        if recv in ("self", "cls") and cls_name:              # in a plain function `self` is an ordinary (often annotated) parameter
            c = by_file.get(path, {}).get(cls_name)
            return c if c and c["kind"] == "class" else None
        if recv.startswith("self.") and recv.count(".") == 1:
            return self_attr(path, cls_name, recv, "type", depth + 1) if cls_name else None
        if recv.isidentifier():
            t = var_at(path, caller, recv, line, depth + 1)
            if t is MISSING and caller != "<module>":
                t = var_at(path, "<module>", recv, 10 ** 9, depth + 1)      # a module-level object (a compiled regex, a client)
            return None if t is MISSING else t
        parts = recv.split(".")
        if len(parts) > 1 and all(p.isidentifier() for p in parts) and depth < 12:
            # `self.console.line()`, `response.headers.get()`: the head's class, then each attribute's class in turn
            t = holder(path, caller, parts[0] if parts[0] not in ("self", "cls") else ".".join(parts[:2]), line, depth + 1)
            for a in parts[(1 if parts[0] not in ("self", "cls") else 2):]:
                t = attr_class(t, a, depth + 1)
                if t is None:
                    return None
            return t
        return None

    def attr_class(cls, attr: str, depth: int = 0):
        """The class held by attribute `attr` of an instance of `cls`: from assignments to `self.attr` in its methods, an
        annotation in its body, or a property declaring what it returns; looked up through the base classes too."""
        if cls is None or cls is EXTERNAL or str(cls.get("id", "")).startswith("<") or depth > 12:
            return None                    # an attribute of a library object may hold anything, even one of ours: unknown
        c, seen = cls, set()
        while c is not None and c["id"] not in seen and len(seen) < 6:
            seen.add(c["id"])
            t = self_attr(c["file"], c["qualname"], f"self.{attr}", "type", depth + 1)
            if t is not None:
                return t
            m = by_file.get(c["file"], {}).get(f"{c['qualname']}.{attr}")
            if m is not None:
                if m["kind"] == "method" and m.get("returns") and any(d.split(".")[-1] in ("property", "cached_property") for d in m.get("decorators", [])):
                    t = produced(m, 1)
                    return t if t is not None else (EXTERNAL if external(m["file"], m["returns"]) else None)
                return None
            c = next((bc for b in c.get("bases", []) if (bc := base_class(c, b))), None)
        return None

    def typed(path: str, a: dict, depth: int = 0):
        """What one recorded assignment says its variable holds: a class, EXTERNAL, or None (unknown)."""
        if a.get("type"):
            return ref_class(path, a["type"])
        if depth > 12:
            return None
        if a.get("call"):
            t = ref_class(path, a["call"])
            if t is None and "." in a["call"]:                # x = y.method(): what the method of y's class returns
                recv, meth = a["call"].rsplit(".", 1)
                owner = holder(path, a["caller"], recv, a["line"], depth + 1)
                if owner is not None and owner is not EXTERNAL:
                    m = method_of(owner, meth)
                    if m is not None and m.get("returns"):
                        t = produced(m, 1)
                        t = t if t is not None else (EXTERNAL if external(m["file"], m["returns"]) else None)
            return t
        if a.get("from"):                       # x = y, self.x = param
            return holder(path, a["caller"], a["from"], a["line"], depth + 1)
        if a.get("of"):                         # for x in items / x = items[0] / for x in d.values()
            whats = {"iter": ("elem",), "values": ("val",), "item": ("elem", "val")}[a.get("via", "iter")]
            return next((t for t in (part_of(path, a["caller"], a["of"], w, a["line"], depth + 1) for w in whats) if t is not None), None)
        return None

    def receiver_class(path: str, caller: str, recv: str, line: int = 10 ** 9) -> tuple[dict | None, bool]:
        """(class, known?) for the receiver expression of a call at `line`. known is False when nothing can be said; (None, True)
        means the receiver is a builtin or library object, so no method in the repo can be the target."""
        local = by_file.get(path, {})
        cls_name = caller.split(".")[0] if "." in caller else None
        if recv in ("self", "cls") and cls_name:
            c = local.get(cls_name)
            return (c, True) if c and c["kind"] == "class" else (None, False)
        if recv == "super()" and cls_name and local.get(cls_name, {}).get("kind") == "class":
            return {"id": "<super>", "of": local[cls_name]}, True
        if recv.endswith(")") and "(" in recv and recv.count("(") == 1:
            ref = recv[: recv.index("(")]
            t = type_of(path, ref)
            if t is not None:
                return t, True
            return (None, True) if external(path, ref) else (None, False)
        if (recv.startswith("self.") and recv.count(".") == 1 and cls_name) or recv.isidentifier() or \
                ("." in recv and all(p.isidentifier() for p in recv.split("."))):
            t = holder(path, caller, recv, line)
            if t is EXTERNAL:
                return None, True
            if t is None and recv.isidentifier() and not _assigned_or_param(path, caller, recv):
                ref = resolve_ref(path, recv)           # `Store.default()`: an imported (or same-file) class used directly
                if ref is not None and ref["kind"] == "class":
                    return ref, True
                if external(path, recv):                # `core_docs.search()`, `np.where()`: a module or name from a library
                    return None, True
            return t, t is not None
        return None, False

    def _assigned_or_param(path: str, caller: str, var: str) -> bool:
        by_caller = assigns_of(path)[0]
        params = (by_file.get(path, {}).get(caller) or {}).get("params") or {}
        return var in params or var in by_caller.get(caller, {}) or var in by_caller.get("<module>", {})

    for path, rec in files.items():
        if rec.get("lang") in ("go", "java", "csharp", "kotlin", "rust"):
            _typed_edges(path, rec, by_id, by_name, by_file, add)
            continue
        if rec.get("lang") in ("js", "ts"):
            imports = rec.get("imports", [])
            from_imp = {i["alias"]: i for i in imports if i["name"]}
            mod_alias = {i["alias"]: i["module"] for i in imports if not i["name"]}
            local = by_file.get(path, {})
            for c in rec.get("calls", []):
                src_id = f"{path}#{c['caller']}"
                if src_id not in by_id:
                    continue
                cands = _js_cands(path, rec, c, files, by_id, by_name, by_file, local, from_imp, mod_alias)
                if not cands or len(cands) > MAX_AMBIGUOUS:
                    continue
                conf = CONF_AMBIGUOUS if len(cands) > 1 else CONF_STRUCTURAL
                for d in cands:
                    if d["id"] != src_id:
                        add(src_id, d["id"], "tests" if by_id[src_id]["is_test"] else "calls", conf)
            continue
        if rec.get("lang") != "python":
            # SQL files: table edges attach to the file itself
            for t in rec.get("tables", []):
                add(path, f"table:{t['name']}", t["mode"], CONF_STRUCTURAL)
            continue
        imports = rec.get("imports", [])
        from_imp = {i["alias"]: i for i in imports if i["name"]}
        mod_alias = {i["alias"]: i["module"] for i in imports if not i["name"]}
        for i in imports:  # `from pkg import mod` may be a module import
            if i["name"] and _module_files(files, f"{i['module']}.{i['name']}"):
                mod_alias[i["alias"]] = f"{i['module']}.{i['name']}"
        local = by_file.get(path, {})
        near: set[str] | None = None
        star = {f for i in imports if i["name"] == "*" for f in _module_files(files, i["module"])}

        for c in rec.get("calls", []):
            src_id = f"{path}#{c['caller']}"
            if src_id not in by_id:
                continue
            name, recv = c["name"], c["recv"]
            cands: list[dict] = []
            conf = CONF_STRUCTURAL
            guessed = False
            if recv is None:
                if name in from_imp:
                    imp = from_imp[name]
                    for f in _module_files(files, imp["module"]):
                        cands += [s for q, s in by_file.get(f, {}).items() if s["name"] == imp["name"] and "." not in q]
                elif name in local and "." not in name:
                    cands = [local[name]]
                elif star:
                    # Python looks a bare name up locally, in the module, then builtins: another file's function is reachable
                    # only through an import. A star import is the one way in that does not name it.
                    cands = [s for s in by_name.get(name, []) if s["kind"] != "method" and "." not in s["qualname"] and s["file"] in star]
                    conf = CONF_AMBIGUOUS
            else:
                head = recv.split(".")[0]
                cls, known = receiver_class(path, c["caller"], recv, c.get("line") or 10 ** 9)
                if known and cls is None:
                    continue                  # a builtin or library object: a same-named method in the repo is not the target
                if known and cls.get("id") == "<super>":
                    base = cls["of"]
                    m = next((mm for b in base.get("bases", []) if (bc := base_class(base, b)) and (mm := method_of(bc, name))), None)
                    if m is not None and m["id"] != src_id:
                        add(src_id, m["id"], "tests" if by_id[src_id]["is_test"] else "calls", CONF_STRUCTURAL)
                    continue
                if known and cls is not None:
                    m = method_of(cls, name)
                    if m is not None:
                        if m["id"] != src_id:
                            add(src_id, m["id"], "tests" if by_id[src_id]["is_test"] else "calls", CONF_STRUCTURAL)
                        continue
                    if recv not in ("self", "cls"):
                        subs = in_subclasses(cls, name)       # declared as the base class, defined by the implementations
                        if 0 < len(subs) <= MAX_AMBIGUOUS:
                            for d in subs:
                                if d["id"] != src_id:
                                    add(src_id, d["id"], "tests" if by_id[src_id]["is_test"] else "calls", CONF_STRUCTURAL if len(subs) == 1 else CONF_AMBIGUOUS)
                        continue          # otherwise the class is known and has no such method (a library base class, a typo): no guess
                if recv in ("self", "cls") and "." in c["caller"]:
                    cls = c["caller"].split(".")[0]
                    q = f"{cls}.{name}"
                    cands = [local[q]] if q in local else []
                if not cands and head in mod_alias:
                    for f in _module_files(files, mod_alias[head]):
                        cands += [s for q, s in by_file.get(f, {}).items() if s["name"] == name and "." not in q]
                if not cands and (head not in mod_alias or head in from_imp):
                    # receiver of unknown class: matched by method name alone, among Python methods only
                    cands = [s for s in by_name.get(name, []) if s["kind"] == "method" and files.get(s["file"], {}).get("lang") == "python"]
                    guessed = True
                if cands and name in BUILTIN_METHODS:
                    cands = []
                elif cands and name in COMMON_METHODS:
                    if near is None:   # once per file, not once per call
                        near = _imported_files(files, path, imports)
                    cands = [s for s in cands if s["file"] in near]
                    if guessed:        # `x.get()` with x unknown is far more often a dict, a response, a file than this class
                        conf = CONF_AMBIGUOUS
            if not cands:
                continue
            if len(cands) > 1:
                if len(cands) > MAX_AMBIGUOUS:
                    continue
                conf = CONF_AMBIGUOUS
            for d in cands:
                if d["id"] == src_id:
                    continue
                typ = "tests" if by_id[src_id]["is_test"] else "calls"
                add(src_id, d["id"], typ, conf)

        for t in rec.get("tables", []):
            src = f"{path}#{t['caller']}"
            add(src if src in by_id else path, f"table:{t['name']}", t["mode"], CONF_STRUCTURAL)

        # functions handed over by name (callbacks, targets, handler tables): the receiver calls them later, so a change to
        # the function affects this code although no call to it is written here
        for r in rec.get("refs", []):
            src_id = f"{path}#{r['caller']}"
            if src_id not in by_id:
                continue
            name, cands = r["name"], []
            if r.get("recv"):
                if r["recv"] in mod_alias:
                    for f in _module_files(files, mod_alias[r["recv"]]):
                        cands += [s for q, s in by_file.get(f, {}).items() if s["name"] == name and "." not in q and s["kind"] == "function"]
                elif r["recv"] in ("self", "cls") and "." in r["caller"]:
                    q = f"{r['caller'].split('.')[0]}.{name}"
                    cands = [local[q]] if q in local else []
            elif name in from_imp:
                imp = from_imp[name]
                for f in _module_files(files, imp["module"]):
                    cands += [s for q, s in by_file.get(f, {}).items() if s["name"] == imp["name"] and "." not in q and s["kind"] == "function"]
            elif name in local and local[name]["kind"] == "function":
                cands = [local[name]]
            for d in cands[:2]:
                if d["id"] != src_id:
                    add(src_id, d["id"], "refs", CONF_STRUCTURAL)

    for cls in idx["symbols"]:
        if cls["kind"] != "class" or not cls.get("bases"):
            continue
        seen_cls, queue = {cls["id"]}, list(cls["bases"])
        owner = cls
        chain = []
        while queue and len(chain) < 6:                      # walk up: direct bases, then theirs
            b = base_class(owner, queue.pop(0))
            if b and b["id"] not in seen_cls:
                seen_cls.add(b["id"]); chain.append(b)
                if owner is cls:
                    add(cls["id"], b["id"], "inherits", CONF_STRUCTURAL)
                if not queue and b.get("bases"):
                    owner, queue = b, list(b["bases"])
        mine = {q.split(".", 1)[1]: s for q, s in by_file.get(cls["file"], {}).items() if q.startswith(cls["qualname"] + ".")}
        for b in chain:
            for m, s in mine.items():
                parent = by_file.get(b["file"], {}).get(f"{b['qualname']}.{m}")
                if parent and not m.startswith("__"):
                    add(s["id"], parent["id"], "overrides", CONF_STRUCTURAL)

    return [{"src": s, "dst": d, "type": t, "conf": c} for (s, d, t), c in sorted(best.items())]
