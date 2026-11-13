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
                  "then", "catch", "json", "text"}


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

        for c in rec.get("calls", []):
            src_id = f"{path}#{c['caller']}"
            if src_id not in by_id:
                continue
            name, recv = c["name"], c["recv"]
            cands: list[dict] = []
            conf = CONF_STRUCTURAL
            if recv is None:
                if name in from_imp:
                    imp = from_imp[name]
                    for f in _module_files(files, imp["module"]):
                        cands += [s for q, s in by_file.get(f, {}).items() if s["name"] == imp["name"] and "." not in q]
                elif name in local and "." not in name:
                    cands = [local[name]]
                else:
                    cands = [s for s in by_name.get(name, []) if s["kind"] != "method"]
            else:
                head = recv.split(".")[0]
                if recv in ("self", "cls") and "." in c["caller"]:
                    cls = c["caller"].split(".")[0]
                    q = f"{cls}.{name}"
                    cands = [local[q]] if q in local else []
                if not cands and head in mod_alias:
                    for f in _module_files(files, mod_alias[head]):
                        cands += [s for q, s in by_file.get(f, {}).items() if s["name"] == name and "." not in q]
                if not cands and head not in mod_alias:
                    cands = [s for s in by_name.get(name, []) if s["kind"] == "method"]
                elif not cands and head in from_imp:  # Class.method / imported_obj.method
                    cands = [s for s in by_name.get(name, []) if s["kind"] == "method"]
                if cands and name in BUILTIN_METHODS:
                    cands = []
                elif cands and name in COMMON_METHODS:
                    if near is None:   # once per file, not once per call
                        near = _imported_files(files, path, imports)
                    cands = [s for s in cands if s["file"] in near]
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

    return [{"src": s, "dst": d, "type": t, "conf": c} for (s, d, t), c in sorted(best.items())]
