"""Indexer: repo -> .devmem/index.json (symbols, calls, imports, tables, tests).

Python is parsed with the stdlib `ast`; SQL (files and SQL strings in code) with sqlglot.
Graphify and scip-python plug in later as additional edge sources (see graph.py confidences).
Only files whose content hash changed are re-parsed.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from devmem.ignore import Ignore
from devmem.schema import INDEX_PATH, new_index

from . import graph

SQL_START = re.compile(r"^\s*(select|insert|create|with|merge|update|delete)\b", re.I)
LANGS = {".py": "python", ".sql": "sql", ".js": "js", ".ts": "ts", ".tsx": "ts", ".yaml": "yaml",
         ".yml": "yaml", ".ipynb": "notebook", ".md": "markdown"}
MAX_FILE_BYTES = 1_000_000


def git_head(root: Path) -> str | None:
    try:
        return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
    except Exception:
        return None


def _hash(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()


def list_files(root: Path) -> list[str]:
    ig = Ignore.for_repo(root)
    out = []
    for dp, dns, fns in os.walk(root):
        rel_dir = os.path.relpath(dp, root).replace("\\", "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        dns[:] = [d for d in dns if not ig.matches(f"{rel_dir}/{d}/".lstrip("/"))]
        for fn in fns:
            rel = f"{rel_dir}/{fn}".lstrip("/")
            if not ig.matches(rel):
                out.append(rel)
    return sorted(out)


# ---------- SQL ----------
def sql_tables(sql: str) -> list[tuple[str, str]]:
    """Return [(table, 'reads'|'writes')] for a SQL string (Spark dialect first, generic fallback)."""
    import sqlglot
    from sqlglot import exp

    for dialect in ("spark", None):
        try:
            trees = [t for t in sqlglot.parse(sql, read=dialect) if t is not None]
            break
        except Exception:
            trees = None
    if not trees:
        return []
    found: dict[tuple[str, str], None] = {}
    for tree in trees:
        ctes = {c.alias_or_name for c in tree.find_all(exp.CTE)}
        targets = set()
        if isinstance(tree, (exp.Insert, exp.Create, exp.Merge)):
            tgt = tree.this
            tgt = tgt.find(exp.Table) if tgt is not None and not isinstance(tgt, exp.Table) else tgt
            if isinstance(tgt, exp.Table):
                targets.add(id(tgt))
                found[(_tname(tgt), "writes")] = None
        for t in tree.find_all(exp.Table):
            name = _tname(t)
            if not name or name in ctes or id(t) in targets:
                continue
            found[(name, "reads")] = None
    return list(found)


def _tname(t) -> str:
    return ".".join(p for p in (t.catalog, t.db, t.name) if p)


# ---------- Python ----------
def _sig(node) -> str:
    if isinstance(node, ast.ClassDef):
        bases = ", ".join(ast.unparse(b) for b in node.bases)
        return f"class {node.name}({bases})" if bases else f"class {node.name}"
    kw = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"{kw} {node.name}({ast.unparse(node.args)}){ret}"


def _doc(node) -> str:
    d = ast.get_docstring(node) or ""
    return d.strip().splitlines()[0] if d.strip() else ""


def _is_test_file(path: str) -> bool:
    p = path.replace("\\", "/")
    base = p.rsplit("/", 1)[-1]
    return "/tests/" in f"/{p}" or base.startswith("test_") or base.endswith("_test.py")


def _call_parts(call: ast.Call):
    f = call.func
    if isinstance(f, ast.Name):
        return f.id, None
    if isinstance(f, ast.Attribute):
        try:
            recv = ast.unparse(f.value)
        except Exception:
            recv = None
        return f.attr, recv
    return None, None


SPARK_READ = {"table"}
SPARK_WRITE = {"saveAsTable", "insertInto"}


def parse_python(path: str, src: str) -> tuple[dict, list[dict]]:
    """Return (file_record, symbols)."""
    rec = {"lang": "python", "imports": [], "tables": [], "calls": []}
    syms: list[dict] = []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return rec, syms
    test_file = _is_test_file(path)
    pkg_parts = path.replace("\\", "/").split("/")[:-1]

    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                rec["imports"].append({"module": a.name, "name": None, "alias": a.asname or a.name.split(".")[0]})
        elif isinstance(n, ast.ImportFrom):
            mod = n.module or ""
            if n.level:
                base = pkg_parts[: len(pkg_parts) - (n.level - 1)] if n.level > 1 else pkg_parts
                mod = ".".join(base + ([mod] if mod else []))
            for a in n.names:
                rec["imports"].append({"module": mod, "name": a.name, "alias": a.asname or a.name})

    def add_symbol(node, qual, kind, is_test):
        syms.append({
            "id": f"{path}#{qual}", "name": node.name, "qualname": qual, "kind": kind, "file": path,
            "line": node.lineno, "end_line": getattr(node, "end_lineno", node.lineno),
            "sig": _sig(node), "doc": _doc(node), "is_test": is_test,
        })

    def scan(body_nodes, caller: str):
        for top in body_nodes:
            for n in ast.walk(top):
                if isinstance(n, ast.Call):
                    name, recv = _call_parts(n)
                    if name:
                        rec["calls"].append({"caller": caller, "name": name, "recv": recv, "line": n.lineno})
                    if name in SPARK_READ | SPARK_WRITE and n.args and isinstance(n.args[0], ast.Constant) \
                            and isinstance(n.args[0].value, str) and recv is not None:
                        if name in SPARK_WRITE or recv.endswith("spark") or recv.endswith("spark.read"):
                            mode = "reads" if name in SPARK_READ else "writes"
                            rec["tables"].append({"name": n.args[0].value, "mode": mode,
                                                  "caller": caller, "line": n.lineno})
                elif isinstance(n, ast.Constant) and isinstance(n.value, str) and SQL_START.match(n.value) \
                        and len(n.value) > 15:
                    for tname, mode in sql_tables(n.value):
                        rec["tables"].append({"name": tname, "mode": mode, "caller": caller, "line": n.lineno})

    module_body = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            add_symbol(node, node.name, "function", test_file and node.name.startswith("test"))
            scan(node.body, node.name)
        elif isinstance(node, ast.ClassDef):
            cls_test = test_file and node.name.startswith("Test")
            add_symbol(node, node.name, "class", False)
            for m in node.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    q = f"{node.name}.{m.name}"
                    add_symbol(m, q, "method", cls_test and m.name.startswith("test"))
                    scan(m.body, q)
        else:
            module_body.append(node)
    if module_body:
        scan(module_body, "<module>")
        syms.append({"id": f"{path}#<module>", "name": "<module>", "qualname": "<module>", "kind": "module",
                     "file": path, "line": 1, "end_line": 1, "sig": f"module {path}", "doc": "",
                     "is_test": False})
    return rec, syms


def parse_sql_file(src: str) -> dict:
    tables = []
    for tname, mode in sql_tables(src):
        tables.append({"name": tname, "mode": mode, "caller": "<module>", "line": 1})
    return {"lang": "sql", "imports": [], "tables": tables, "calls": []}


# ---------- driver ----------
def index_repo(root: str | Path, name: str | None = None, write: bool = True) -> dict:
    root = Path(root).resolve()
    prev = {}
    p = root / INDEX_PATH
    if p.exists():
        try:
            prev = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            prev = {}
    prev_syms: dict[str, list[dict]] = {}
    for s in prev.get("symbols", []):
        prev_syms.setdefault(s["file"], []).append(s)

    idx = new_index(name or root.name, git_head(root))
    stats = {"parsed": 0, "reused": 0}
    for rel in list_files(root):
        fp = root / rel
        try:
            if fp.stat().st_size > MAX_FILE_BYTES:
                continue
            raw = fp.read_bytes()
        except OSError:
            continue
        h = _hash(raw)
        lang = LANGS.get(fp.suffix.lower(), "other")
        old = prev.get("files", {}).get(rel)
        if old and old.get("hash") == h:
            idx["files"][rel] = old
            idx["symbols"].extend(prev_syms.get(rel, []))
            stats["reused"] += 1
            continue
        if lang == "python":
            rec, syms = parse_python(rel, raw.decode("utf-8", "replace"))
        elif lang == "sql":
            rec, syms = parse_sql_file(raw.decode("utf-8", "replace")), []
        else:
            rec, syms = {"lang": lang, "imports": [], "tables": [], "calls": []}, []
        rec["hash"] = h
        idx["files"][rel] = rec
        idx["symbols"].extend(syms)
        stats["parsed"] += 1

    idx["edges"] = graph.build_edges(idx)
    idx["stats"] = stats
    if write:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(idx, indent=1, sort_keys=True), encoding="utf-8")
    return idx
