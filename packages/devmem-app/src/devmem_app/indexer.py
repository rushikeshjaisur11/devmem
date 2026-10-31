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
from devmem.index import Index, write_db
from devmem.schema import INDEX_PATH, new_index

from . import graph, jsparse, treeparse
from . import graphify as graphify_mod
from . import scip as scip_mod

SQL_START = re.compile(r"^\s*(select|insert|create|with|merge|update|delete)\b", re.I)
LANGS = {".py": "python", ".sql": "sql", ".js": "js", ".jsx": "js", ".mjs": "js", ".cjs": "js", ".ts": "ts", ".tsx": "ts", ".mts": "ts", ".yaml": "yaml",
         ".yml": "yaml", ".ipynb": "notebook", ".md": "markdown",
         ".go": "go", ".java": "java"}
MAX_FILE_BYTES = 1_000_000
MAX_SQL_STMTS, MAX_SQL_LEN = 60, 20_000
COLUMN_FUNCS = {"select", "withColumn", "col", "groupBy", "groupby", "orderBy", "sort", "drop", "withColumnRenamed",
                "sum", "avg", "max", "min", "count", "countDistinct", "first", "collect_list", "agg", "join"}
PARSER_VERSION = 6  # bump when per-file records gain fields or parsing changes (forces re-parse of cached files)


class AccuracyError(RuntimeError):
    """A tool that accuracy depends on (SCIP, Graphify, tree-sitter) is missing or failed. Indexing stops instead of quietly
    producing a less accurate index; DEVMEM_ACCURACY=fast (or --fast) is the explicit, recorded opt-out."""


def accuracy_mode(explicit: str | None = None) -> str:
    return (explicit or os.environ.get("DEVMEM_ACCURACY") or "strict").strip().lower()


def stale_files(root: Path, cache_rel: str, idx: dict, langs: set[str]) -> list[str]:
    """Files of `langs` that a cached tool run (.devmem/<cache>) has not seen in their current content."""
    seen: dict[str, str] = {}
    p = root / cache_rel
    if p.exists():
        try:
            c = json.loads(p.read_text(encoding="utf-8"))
            seen = {**c.get("hashes", {}), **c.get("seen", {})}
        except Exception:
            pass
    return [f for f, r in idx["files"].items() if r["lang"] in langs and seen.get(f) != r["hash"]]


def git_head(root: Path) -> str | None:
    try:
        return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
    except Exception:
        return None


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)


def git_branch(root: Path) -> str | None:
    """Current branch name, or None for a detached HEAD / non-git folder."""
    r = _git(root, "symbolic-ref", "--short", "-q", "HEAD")
    return r.stdout.strip() or None if r.returncode == 0 else None


def git_branches(root: Path) -> list[str]:
    """Local branches plus remote-only ones (shown without the remote prefix; checkout creates a tracking branch)."""
    local = _git(root, "for-each-ref", "--format=%(refname:short)", "refs/heads").stdout.split()
    remote = [b.split("/", 1)[1] for b in _git(root, "for-each-ref", "--format=%(refname:short)", "refs/remotes").stdout.split()
              if "/" in b and not b.endswith("/HEAD")]
    return sorted(set(local) | set(remote))


def git_dirty(root: Path) -> list[str]:
    """Tracked files with uncommitted changes (devmem's own .devmem/ output is ignored)."""
    out = _git(root, "status", "--porcelain", "--untracked-files=no").stdout.splitlines()
    return [ln[3:] for ln in out if not ln[3:].startswith(".devmem/")]


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


def _exc_names(node) -> list[str]:
    if node is None:
        return []
    if isinstance(node, ast.Tuple):
        return [n for e in node.elts for n in _exc_names(e)]
    if isinstance(node, ast.Call):
        return _exc_names(node.func)
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        return [node.attr]
    return []


def _features(tree) -> dict:
    """Small per-file counters used for convention detection."""
    exc, raises, decs, bare = {}, {}, {}, 0
    for n in ast.walk(tree):
        if isinstance(n, ast.ExceptHandler):
            if n.type is None:
                bare += 1
            for name in _exc_names(n.type):
                exc[name] = exc.get(name, 0) + 1
        elif isinstance(n, ast.Raise):
            for name in _exc_names(n.exc):
                raises[name] = raises.get(name, 0) + 1
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for d in n.decorator_list:
                head = ast.unparse(d).split("(")[0]
                decs[head] = decs.get(head, 0) + 1
    return {"except": exc, "raises": raises, "decorators": decs, "bare_except": bare}


def parse_python(path: str, src: str) -> tuple[dict, list[dict]]:
    """Return (file_record, symbols)."""
    rec = {"lang": "python", "imports": [], "tables": [], "calls": [], "lines": len(src.splitlines()), "sql": [],
           "df_columns": []}
    syms: list[dict] = []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return rec, syms
    rec["features"] = _features(tree)
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
                    if len(rec["sql"]) < MAX_SQL_STMTS and len(n.value) <= MAX_SQL_LEN:
                        rec["sql"].append({"sql": n.value, "caller": caller, "line": n.lineno})

    def df_cols(body_nodes, caller):
        tables, cols = [], []
        for top in body_nodes:
            for n in ast.walk(top):
                if isinstance(n, ast.Call):
                    name, recv = _call_parts(n)
                    if name == "table" and n.args and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str) \
                            and recv and (recv.endswith("spark") or recv.endswith("spark.read")):
                        tables.append(n.args[0].value)
                    elif name in COLUMN_FUNCS:
                        for a in n.args:
                            if isinstance(a, ast.Constant) and isinstance(a.value, str) and re.fullmatch(r"[A-Za-z_]\w*", a.value):
                                cols.append(a.value)
                elif isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant) and isinstance(n.slice.value, str) \
                        and re.fullmatch(r"[A-Za-z_]\w*", n.slice.value):
                    cols.append(n.slice.value)
        if tables:
            rec["df_columns"].append({"caller": caller, "tables": sorted(set(tables)), "cols": sorted(set(cols))})

    module_body = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            add_symbol(node, node.name, "function", test_file and node.name.startswith("test"))
            scan(node.body, node.name)
            df_cols(node.body, node.name)
        elif isinstance(node, ast.ClassDef):
            cls_test = test_file and node.name.startswith("Test")
            add_symbol(node, node.name, "class", False)
            for m in node.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    q = f"{node.name}.{m.name}"
                    add_symbol(m, q, "method", cls_test and m.name.startswith("test"))
                    scan(m.body, q)
                    df_cols(m.body, q)
        else:
            module_body.append(node)
    if module_body:
        scan(module_body, "<module>")
        df_cols(module_body, "<module>")
        syms.append({"id": f"{path}#<module>", "name": "<module>", "qualname": "<module>", "kind": "module",
                     "file": path, "line": 1, "end_line": 1, "sig": f"module {path}", "doc": "",
                     "is_test": False})
    return rec, syms


def parse_sql_file(src: str) -> dict:
    import sqlglot
    stmts = []
    try:
        for t in sqlglot.parse(src, read="spark"):
            if t is not None and len(stmts) < MAX_SQL_STMTS:
                text = t.sql(dialect="spark")
                if len(text) <= MAX_SQL_LEN:
                    stmts.append({"sql": text, "caller": "<module>", "line": 1})
    except Exception:
        pass
    tables = []
    for tname, mode in sql_tables(src):
        tables.append({"name": tname, "mode": mode, "caller": "<module>", "line": 1})
    return {"lang": "sql", "imports": [], "tables": tables, "calls": [], "sql": stmts, "df_columns": []}


# ---------- driver ----------
def index_repo(root: str | Path, name: str | None = None, write: bool = True, scip: bool | list | None = None,
               graphify: bool | Path | str | None = None, accuracy: str | None = None) -> dict:
    """Accuracy is not optional. By default (accuracy "strict", or DEVMEM_ACCURACY unset) SCIP type-resolved edges and the Graphify
    cross-check run whenever files they have not seen changed (cached per file hash, so unchanged repos re-index instantly), and
    a missing/failed tool or parser raises AccuracyError. accuracy="fast" skips them and records that in stats.
    Explicit scip=True / graphify=True force a re-run; a list of .scip paths / a graph.json path imports prebuilt output."""
    root = Path(root).resolve()
    strict = accuracy_mode(accuracy) != "fast"
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
    js_ok = jsparse.available()
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
        if old and old.get("hash") == h and old.get("v") == PARSER_VERSION:
            idx["files"][rel] = old
            idx["symbols"].extend(prev_syms.get(rel, []))
            stats["reused"] += 1
            continue
        if lang == "python":
            rec, syms = parse_python(rel, raw.decode("utf-8", "replace"))
        elif lang == "sql":
            rec, syms = parse_sql_file(raw.decode("utf-8", "replace")), []
        elif lang in ("js", "ts") and js_ok:
            try:
                rec, syms = jsparse.parse_js(rel, raw.decode("utf-8", "replace"))
            except Exception:
                rec, syms = {"lang": lang, "imports": [], "tables": [], "calls": []}, []
        elif lang in ("go", "java"):
            try:
                rec, syms = treeparse.parse(rel, raw.decode("utf-8", "replace"), lang)
            except Exception:
                rec, syms = {"lang": lang, "imports": [], "tables": [], "calls": []}, []
        elif lang in ("js", "ts"):
            if strict:
                raise AccuracyError("tree-sitter is not installed, so JS/TS cannot be parsed accurately: pip install -e packages/devmem-app")
            stats["js_skipped"] = stats.get("js_skipped", 0) + 1
            rec, syms = {"lang": lang, "imports": [], "tables": [], "calls": []}, []
        else:
            rec, syms = {"lang": lang, "imports": [], "tables": [], "calls": []}, []
        rec["hash"] = h
        rec["v"] = PARSER_VERSION
        idx["files"][rel] = rec
        idx["symbols"].extend(syms)
        stats["parsed"] += 1

    idx["edges"] = graph.build_edges(idx)
    present = {f["lang"] for f in idx["files"].values()}
    if isinstance(scip, list):
        scip_mod.build_cache(root, idx, [Path(f) for f in scip])
    elif scip is True or (scip is None and strict and scip_mod.applicable(root, idx)
                          and stale_files(root, scip_mod.CACHE, idx, scip_mod.applicable(root, idx))):
        files, msgs = scip_mod.run_indexer(root, idx)
        if files:
            scip_mod.build_cache(root, idx, files)
        stats["scip_messages"] = msgs
        if msgs and strict:
            raise AccuracyError("SCIP (type-resolved edges) failed: " + "; ".join(msgs) + ". Needs Node.js 18+ and network on first run "
                                "(npx fetches scip-python / scip-typescript). Fix it, or index with --fast to accept lower accuracy.")
    if graphify is not None and graphify is not False and graphify is not True:
        graphify_mod.build_cache(root, idx, Path(graphify))
    elif graphify is True or (graphify is None and strict and present & graphify_mod.CODE_LANGS
                              and stale_files(root, graphify_mod.CACHE, idx, graphify_mod.CODE_LANGS)):
        gf, msg = graphify_mod.run_graphify(root)
        if gf:
            graphify_mod.build_cache(root, idx, gf)
        elif msg:
            stats["graphify_message"] = msg
            if strict:
                raise AccuracyError(f"Graphify cross-check failed: {msg}. It is installed with devmem-app (pip install -e packages/devmem-app); "
                                    "index with --fast to accept lower accuracy.")
    gs = graphify_mod.apply_cache(root, idx)       # structural second opinion first, then SCIP (type-resolved) wins
    if gs:
        stats["graphify"] = gs
    sc = scip_mod.apply_cache(root, idx)
    if sc:
        stats["scip"] = sc
    stats["accuracy"] = "full" if strict else "fast"
    idx["stats"] = stats
    if write:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(idx, indent=1, sort_keys=True), encoding="utf-8")
        write_db(Index(idx, root))
    return idx
