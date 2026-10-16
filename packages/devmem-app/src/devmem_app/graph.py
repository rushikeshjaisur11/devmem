"""Edge resolution: raw calls/tables -> graph edges with a confidence per edge.

Confidence ladder: precise (SCIP, later) > structural (import/name resolved) > ambiguous > co-change.
Where several sources give the same edge, the highest confidence wins.
"""
from __future__ import annotations

from devmem.schema import CONF_AMBIGUOUS, CONF_STRUCTURAL

RANK = {"precise": 3, "structural": 2, "ambiguous": 1, "co-change": 0}
MAX_AMBIGUOUS = 3


def _module_files(files: dict, module: str) -> list[str]:
    """Files whose path ends with the dotted module (tolerates src/ roots)."""
    if not module:
        return []
    suffixes = (module.replace(".", "/") + ".py", module.replace(".", "/") + "/__init__.py")
    return [f for f in files if any(f == s or f.endswith("/" + s) for s in suffixes)]


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
                if not cands and head not in mod_alias and head not in from_imp:
                    cands = [s for s in by_name.get(name, []) if s["kind"] == "method"]
                elif not cands and head in from_imp:  # Class.method / imported_obj.method
                    cands = [s for s in by_name.get(name, []) if s["kind"] == "method"]
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
