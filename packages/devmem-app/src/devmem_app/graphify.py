"""Graphify integration (https://graphify.com, `pip install graphifyy`): import its tree-sitter call graph as an
independent second opinion on our edges. No LLM or network is needed: `graphify update <repo>` writes
graphify-out/graph.json (node-link JSON: nodes with source_file/source_location "L14"; links with relation and
confidence EXTRACTED|INFERRED|AMBIGUOUS).

Policy (measured against SCIP on psf/requests, see README): an edge both tools find is almost always right, an
ambiguous edge of ours that Graphify did not confirm is almost always wrong. So: confirmed ambiguous -> structural;
unconfirmed ambiguous edges from files Graphify covered are dropped; edges only Graphify found are added."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

CACHE = ".devmem/graphify-edges.json"
CALL_RELATIONS = {"calls", "indirect_call"}
CONF = {"EXTRACTED": "structural", "INFERRED": "ambiguous", "AMBIGUOUS": "ambiguous"}


def run_graphify(root: Path, timeout: int = 900) -> tuple[Path | None, str]:
    exe = shutil.which("graphify")
    if not exe:
        return None, "graphify not found: pip install graphifyy (or pass an existing graph with --graphify-file)"
    try:
        r = subprocess.run([exe, "update", "."], cwd=str(root), capture_output=True, text=True, errors="replace", timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as e:
        return None, f"graphify: {e}"
    g = root / "graphify-out/graph.json"
    return (g, "") if g.exists() else (None, f"graphify failed: {(r.stderr or r.stdout).strip()[-160:]}")


def _suffix_match(files: set[str], p: str) -> str | None:
    p = p.replace("\\", "/")
    if p in files:
        return p
    parts = p.split("/")
    for i in range(1, len(parts)):  # graph built from a parent directory: strip leading components
        cand = "/".join(parts[i:])
        if cand in files:
            return cand
    return None


def edges_from(idx: dict, graph: dict) -> tuple[list[dict], set[str]]:
    by_file: dict[str, list[dict]] = {}
    for s in idx["symbols"]:
        if s["kind"] != "module":
            by_file.setdefault(s["file"], []).append(s)
    files = set(idx["files"])

    def locate(n):
        f = _suffix_match(files, n.get("source_file") or "")
        m = re.match(r"L(\d+)", n.get("source_location") or "")
        if not f or not m:
            return None, f
        line = int(m.group(1))
        best = None
        for s in by_file.get(f, []):
            if s["line"] <= line <= s["end_line"] and (best is None or s["end_line"] - s["line"] < best["end_line"] - best["line"]):
                best = s
        return best, f

    nodes = {n["id"]: n for n in graph.get("nodes", [])}
    covered = {f for n in nodes.values() for f in [_suffix_match(files, n.get("source_file") or "")] if f}
    out, seen = [], set()
    for e in graph.get("links", graph.get("edges", [])):
        if e.get("relation") not in CALL_RELATIONS:
            continue
        a, b = nodes.get(e["source"]), nodes.get(e["target"])
        if not a or not b:
            continue
        sa, _ = locate(a)
        sb, _ = locate(b)
        if not sa or not sb or sa["id"] == sb["id"]:
            continue
        k = (sa["id"], sb["id"])
        if k in seen:
            continue
        seen.add(k)
        out.append({"src": sa["id"], "dst": sb["id"], "type": "tests" if sa["is_test"] else "calls",
                    "conf": CONF.get(e.get("confidence", "EXTRACTED"), "ambiguous")})
    return out, covered


def merge(idx: dict, g_edges: list[dict], covered: set[str]) -> dict:
    gk = {(e["src"], e["dst"]): e for e in g_edges}
    kept, upgraded, dropped = [], 0, 0
    have = set()
    for e in idx["edges"]:
        if e["type"] in ("calls", "tests") and not e["dst"].startswith("table:"):
            have.add((e["src"], e["dst"]))
            if e["conf"] == "ambiguous":
                if (e["src"], e["dst"]) in gk and gk[(e["src"], e["dst"])]["conf"] == "structural":
                    e = {**e, "conf": "structural"}; upgraded += 1
                elif e["src"].split("#")[0] in covered:
                    dropped += 1
                    continue
        kept.append(e)
    added = [e for k, e in gk.items() if k not in have]
    idx["edges"] = sorted(kept + added, key=lambda e: (e["src"], e["dst"], e["type"]))
    return {"edges": len(g_edges), "upgraded": upgraded, "dropped_ambiguous": dropped, "added": len(added)}


CODE_LANGS = {"python", "js", "ts"}


def build_cache(root: Path, idx: dict, graph_file: Path) -> dict:
    edges, covered = edges_from(idx, json.loads(Path(graph_file).read_text(encoding="utf-8")))
    cache = {"edges": edges, "covered": sorted(covered), "hashes": {p: idx["files"][p]["hash"] for p in covered if p in idx["files"]},
             "seen": {p: r["hash"] for p, r in idx["files"].items() if r["lang"] in CODE_LANGS}}
    p = root / CACHE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cache), encoding="utf-8")
    return cache


def apply_cache(root: Path, idx: dict) -> dict | None:
    p = root / CACHE
    if not p.exists():
        return None
    try:
        c = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    ok = {f for f, h in c["hashes"].items() if idx["files"].get(f, {}).get("hash") == h}
    ids = {s["id"] for s in idx["symbols"]}
    edges = [e for e in c["edges"] if e["src"].split("#")[0] in ok and e["dst"].split("#")[0] in ok and e["src"] in ids and e["dst"] in ids]
    stats = merge(idx, edges, ok)
    stats["stale_files"] = len(c["hashes"]) - len(ok)
    return stats
