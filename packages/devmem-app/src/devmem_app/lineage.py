"""Table/column lineage: SQL statements (files + strings in PySpark) via sqlglot, plus a DataFrame-API heuristic.

Output (.devmem/lineage.json): schemas (snapshots + inferred), column_edges (src col -> dst col, per job),
reads (every column a job reads, incl. filters/joins; `t.*` = whole table), unresolved statements.
Schema snapshots live in .devmem/schemas.json (repo-owned; import with `devmem-app schema import <csv>`).
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.lineage import lineage as sg_lineage
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope

from devmem.lineage import LINEAGE_PATH

SCHEMAS_PATH = ".devmem/schemas.json"
WITH_KEY = "with_" if "with_" in exp.Select.arg_types else "with"


def _tname(t) -> str:
    return ".".join(p for p in (t.catalog, t.db, t.name) if p)


def _nested(schemas: dict[str, list[str]]) -> dict:
    out: dict = {}
    for table, cols in schemas.items():
        parts = table.split(".")
        d = out
        for p in parts[:-1]:
            d = d.setdefault(p, {})
        d[parts[-1]] = {c: "unknown" for c in cols}
    return out


def load_schemas(root: Path) -> dict[str, list[str]]:
    p = root / SCHEMAS_PATH
    if not p.exists():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8"))
    return {t: (list(c) if isinstance(c, (list, dict)) else []) for t, c in raw.items()}


def import_schema_csv(root: Path, csv_path: Path) -> int:
    """CSV columns: table,column[,type]. Merged into .devmem/schemas.json."""
    cur = load_schemas(root)
    n = 0
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            t, c = row["table"].strip(), row["column"].strip()
            if c not in cur.setdefault(t, []):
                cur[t].append(c); n += 1
    p = root / SCHEMAS_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cur, indent=1, sort_keys=True), encoding="utf-8")
    return n


def _split(tree):
    """-> (target table name | None, [explicit target columns], query expression | None, ddl columns)."""
    if isinstance(tree, exp.Insert):
        tgt, cols = tree.this, []
        if isinstance(tgt, exp.Schema):
            cols = [c.name for c in tgt.expressions]
            tgt = tgt.this
        q = tree.expression
        if q is not None and tree.args.get("with_" if "with_" in tree.args else "with") is not None and hasattr(q, "set"):
            q = q.copy()
            q.set(WITH_KEY, tree.args.get("with_" if "with_" in tree.args else "with").copy())
        return (_tname(tgt) if isinstance(tgt, exp.Table) else None), cols, q, []
    if isinstance(tree, exp.Create):
        tgt = tree.this
        ddl = []
        if isinstance(tgt, exp.Schema):
            ddl = [c.name for c in tgt.expressions if isinstance(c, exp.ColumnDef)]
            tgt = tgt.this
        return (_tname(tgt) if isinstance(tgt, exp.Table) else None), [], tree.expression, ddl
    if isinstance(tree, (exp.Select, exp.Union, exp.Subquery)):
        return None, [], tree, []
    return None, [], None, []


def _leaves(node):
    return [n for n in node.walk() if not n.downstream]


def build(idx: dict, root: str | Path) -> dict:
    root = Path(root)
    user_schema = load_schemas(root)
    stmts = []  # (job, tree)
    for path, rec in idx["files"].items():
        for item in rec.get("sql", []):
            job = path if rec["lang"] == "sql" else f"{path}#{item['caller']}"
            try:
                for t in sqlglot.parse(item["sql"], read="spark"):
                    if t is not None:
                        stmts.append((job, t))
            except Exception:
                continue

    # pass 1: infer schemas of tables we write or define (output names), without overriding snapshots
    inferred: dict[str, list[str]] = {}
    parsed = []
    for job, tree in stmts:
        target, tcols, q, ddl = _split(tree)
        outs = []
        if q is not None and hasattr(q, "named_selects"):
            outs = [c for c in q.named_selects if c != "*"]
        if target:
            cols = ddl or tcols or outs
            if cols:
                inferred.setdefault(target, [])
                inferred[target] += [c for c in cols if c not in inferred[target]]
        parsed.append((job, tree, target, tcols, q, outs))
    schemas = {**inferred, **user_schema}
    nested = _nested(schemas)

    edges, reads, unresolved = {}, {}, []

    def add_edge(src, dst, job, conf):
        edges.setdefault((src, dst, job), conf)

    def add_read(col, job, conf):
        reads.setdefault((col, job), conf)

    # pass 2: column lineage + all column reads
    for job, tree, target, tcols, q, outs in parsed:
        if q is None:
            continue
        if isinstance(q, exp.Subquery):
            q = q.this
        qsql = q.sql(dialect="spark")
        names = list(q.named_selects)
        dst_names = tcols if (tcols and len(tcols) == len(names)) else names
        # reads (select, where, join, group by ...): resolved through scopes
        try:
            qq = qualify(q.copy(), schema=nested, dialect="spark", validate_qualify_columns=False)
            for scope in traverse_scope(qq):
                tables = [s for s in scope.sources.values() if isinstance(s, exp.Table)]
                for c in scope.columns:
                    src = scope.sources.get(c.table) if c.table else (tables[0] if len(tables) == 1 else None)
                    if isinstance(src, exp.Table):
                        add_read(f"{_tname(src)}.{'*' if isinstance(c.this, exp.Star) else c.name}", job, "sql")
        except Exception as e:  # keep table-level facts even if columns can't be resolved
            unresolved.append({"job": job, "why": f"qualify failed: {str(e)[:80]}"})
        # output mapping
        for out, dst in zip(names, dst_names):
            if out == "*":
                for t in q.find_all(exp.Table):
                    add_read(f"{_tname(t)}.*", job, "sql")
                continue
            try:
                node = sg_lineage(out, qsql, schema=nested, dialect="spark")
                for leaf in _leaves(node):
                    if isinstance(leaf.source, exp.Table):
                        add_edge(f"{_tname(leaf.source)}.{leaf.name.rsplit('.', 1)[-1]}",
                                 f"{target}.{dst}" if target else f"(result of {job}).{dst}", job, "sql")
            except Exception as e:
                unresolved.append({"job": job, "why": f"{out}: {str(e)[:80]}"})
    # edges into a non-table result are only reads
    col_edges = [{"src": s, "dst": d, "job": j, "conf": c} for (s, d, j), c in edges.items() if not d.startswith("(result of")]

    # DataFrame-API heuristic: columns named as strings in a function that reads exactly one table
    for path, rec in idx["files"].items():
        for item in rec.get("df_columns", []):
            job = f"{path}#{item['caller']}"
            if len(item["tables"]) == 1:
                t = item["tables"][0]
                known = set(schemas.get(t, []))
                cols = [c for c in item["cols"] if not known or c in known]
                for c in cols or ["*"]:
                    add_read(f"{t}.{c}", job, "heuristic")
            else:
                for t in item["tables"]:
                    add_read(f"{t}.*", job, "heuristic")

    return {
        "schemas": {t: sorted(c) for t, c in schemas.items()},
        "column_edges": sorted(col_edges, key=lambda e: (e["src"], e["dst"], e["job"])),
        "reads": sorted(({"col": c, "job": j, "conf": conf} for (c, j), conf in reads.items()), key=lambda r: (r["col"], r["job"])),
        "unresolved": unresolved,
    }


def write(root: str | Path, idx: dict) -> dict:
    lin = build(idx, root)
    p = Path(root) / LINEAGE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(lin, indent=1), encoding="utf-8")
    return lin


def combine(repos: dict[str, Path]) -> dict:
    """Merge the lineage.json of several onboarded repos (jobs prefixed `repo:`) for cross-repo impact."""
    out = {"schemas": {}, "column_edges": [], "reads": []}
    for name, root in repos.items():
        p = Path(root) / LINEAGE_PATH
        if not p.exists():
            continue
        lin = json.loads(p.read_text(encoding="utf-8"))
        for t, cols in lin.get("schemas", {}).items():
            out["schemas"].setdefault(t, sorted(set(out["schemas"].get(t, [])) | set(cols)))
        out["column_edges"] += [{**e, "job": f"{name}:{e['job']}"} for e in lin["column_edges"]]
        out["reads"] += [{**r, "job": f"{name}:{r['job']}"} for r in lin["reads"]]
    return out


def render_lineage_md(idx: dict, lin: dict, peers_lin: dict | None = None, repo: str = "") -> str:
    reads_t, writes_t = defaultdict(set), defaultdict(set)
    for e in idx["edges"]:
        if e["dst"].startswith("table:"):
            (reads_t if e["type"] == "reads" else writes_t)[e["dst"][6:]].add(e["src"])
    cols_by_table = defaultdict(set)
    for r in lin["reads"]:
        t, c = r["col"].rsplit(".", 1)
        cols_by_table[t].add(c)
    lines = ["# Data lineage", "Tables this repo touches, columns read, and what is derived from what. "
             "Use `devmem impact <table.column>` before changing a column.", ""]
    if reads_t:
        lines.append("## Reads")
        for t in sorted(reads_t):
            cols = sorted(cols_by_table.get(t, []))
            lines.append(f"- `{t}`: " + (", ".join(f"`{c}`" for c in cols[:12]) + (" ..." if len(cols) > 12 else "") if cols else "columns unknown")
                         + f" — by {', '.join(sorted(j.split('#')[-1] if '#' in j else j for j in reads_t[t])[:3])}")
    if writes_t:
        lines += ["", "## Writes"]
        for t in sorted(writes_t):
            cols = lin["schemas"].get(t, [])
            lines.append(f"- `{t}`: " + (", ".join(f"`{c}`" for c in cols[:12]) if cols else "columns unknown")
                         + f" — by {', '.join(sorted(j.split('#')[-1] if '#' in j else j for j in writes_t[t])[:3])}")
    derived = lin["column_edges"][:15]
    if derived:
        lines += ["", "## Column derivation (source -> target)"]
        lines += [f"- `{e['src']}` -> `{e['dst']}`" for e in derived]
        if len(lin["column_edges"]) > 15:
            lines.append(f"- ... {len(lin['column_edges']) - 15} more in .devmem/lineage.json")
    if peers_lin:
        consumers = defaultdict(set)
        for r in peers_lin["reads"]:
            t = r["col"].rsplit(".", 1)[0]
            if t in writes_t:
                consumers[t].add(r["job"])
        if consumers:
            lines += ["", "## Used by other repos (changing these tables affects them)"]
            for t in sorted(consumers):
                lines.append(f"- `{t}`: " + ", ".join(sorted(consumers[t])[:4]))
    if lin["unresolved"]:
        lines += ["", f"Note: {len(lin['unresolved'])} statement(s) could not be fully resolved; add schema snapshots to "
                      ".devmem/schemas.json for more precise lineage."]
    return "\n".join(lines)
