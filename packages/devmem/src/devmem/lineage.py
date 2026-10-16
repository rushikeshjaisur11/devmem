"""Column lineage queries over .devmem/lineage.json (built by devmem-app). Stdlib only."""
from __future__ import annotations

import json
from collections import defaultdict, deque
from pathlib import Path

LINEAGE_PATH = ".devmem/lineage.json"


def load(root: Path) -> dict | None:
    p = Path(root) / LINEAGE_PATH
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def known_tables(lin: dict) -> set[str]:
    t = set(lin.get("schemas", {}))
    t |= {r["col"].rsplit(".", 1)[0] for r in lin.get("reads", [])}
    t |= {e["src"].rsplit(".", 1)[0] for e in lin.get("column_edges", [])}
    t |= {e["dst"].rsplit(".", 1)[0] for e in lin.get("column_edges", [])}
    return t


def split_column(lin: dict, ref: str) -> tuple[str, str] | None:
    """'db.tbl.col' -> ('db.tbl', 'col') when db.tbl is a known table."""
    if "." not in ref:
        return None
    table, col = ref.rsplit(".", 1)
    return (table, col) if table in known_tables(lin) else None


def column_impact(lin: dict, table: str, col: str, max_depth: int = 3) -> dict:
    """Who is affected if `table.col` changes: direct readers, and columns derived from it plus their readers.
    Readers of `table.*` (select *, DataFrame reads with no column named) are listed as 'may be affected'."""
    reads = defaultdict(list)
    for r in lin.get("reads", []):
        reads[r["col"]].append(r)
    out_edges = defaultdict(list)
    for e in lin.get("column_edges", []):
        out_edges[e["src"]].append(e)

    def readers(c: str, t: str):
        direct = [{"job": r["job"], "conf": r["conf"]} for r in reads.get(c, [])]
        star = [{"job": r["job"], "conf": r["conf"]} for r in reads.get(f"{t}.*", [])
                if r["job"] not in {d["job"] for d in direct}]
        return direct, star

    start = f"{table}.{col}"
    d0, s0 = readers(start, table)
    res = {"column": start, "readers": d0, "may_be_affected": s0, "derived": []}
    seen, q = {start}, deque([(start, 0)])
    while q:
        cur, depth = q.popleft()
        if depth >= max_depth:
            continue
        for e in out_edges.get(cur, []):
            if e["dst"] in seen:
                continue
            seen.add(e["dst"])
            t = e["dst"].rsplit(".", 1)[0]
            d, st = readers(e["dst"], t)
            res["derived"].append({"column": e["dst"], "via": e["job"], "conf": e["conf"], "readers": d, "may_be_affected": st})
            q.append((e["dst"], depth + 1))
    return res


def format_impact(r: dict) -> str:
    j = lambda xs: ", ".join(f"{x['job']} [{x['conf']}]" for x in xs) or "-"
    lines = [r["column"], f"  read by: {j(r['readers'])}"]
    if r["may_be_affected"]:
        lines.append(f"  may be affected (reads whole table): {j(r['may_be_affected'])}")
    for d in r["derived"]:
        lines.append(f"  -> {d['column']} (written by {d['via']}); read by: {j(d['readers'])}")
        if d["may_be_affected"]:
            lines.append(f"     may be affected: {j(d['may_be_affected'])}")
    return "\n".join(lines)
