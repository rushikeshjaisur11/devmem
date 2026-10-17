"""SCIP (type-resolved) edges: run scip-python / scip-typescript (or import a prebuilt index.scip) and merge precise
call edges into the graph. The protobuf is decoded by hand (only the few fields needed), so no extra dependency.

Policy: SCIP edges are `precise`. A structural/ambiguous edge SCIP also found is upgraded; an *ambiguous* edge from a
file SCIP covered but did not confirm is dropped (SCIP saw the real target). Structural edges are kept."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

CACHE = ".devmem/scip-edges.json"
ROLE_DEFINITION = 1


# ---------- protobuf wire decoding ----------
def _varint(b: bytes, i: int) -> tuple[int, int]:
    n = shift = 0
    while True:
        c = b[i]; i += 1
        n |= (c & 0x7F) << shift
        if not c & 0x80:
            return n, i
        shift += 7


def _fields(b: bytes):
    """Yield (field_number, wire_type, value) for one message; value is int (varint) or bytes (length-delimited)."""
    i, n = 0, len(b)
    while i < n:
        key, i = _varint(b, i)
        f, w = key >> 3, key & 7
        if w == 0:
            v, i = _varint(b, i)
        elif w == 2:
            ln, i = _varint(b, i)
            v, i = b[i:i + ln], i + ln
        elif w == 1:
            v, i = b[i:i + 8], i + 8
        elif w == 5:
            v, i = b[i:i + 4], i + 4
        else:
            raise ValueError(f"unsupported wire type {w}")
        yield f, w, v


def _packed(b: bytes) -> list[int]:
    out, i = [], 0
    while i < len(b):
        v, i = _varint(b, i)
        out.append(v)
    return out


def parse(data: bytes) -> list[dict]:
    """-> [{path, occ: [(line0, symbol, roles)]}] (SCIP Index.documents)."""
    docs = []
    for f, w, v in _fields(data):
        if f != 2 or w != 2:
            continue
        doc = {"path": "", "occ": []}
        for f2, w2, v2 in _fields(v):
            if f2 == 1:
                doc["path"] = v2.decode("utf-8", "replace")
            elif f2 == 2:
                rng, sym, roles = [], "", 0
                for f3, w3, v3 in _fields(v2):
                    if f3 == 1:
                        rng = _packed(v3) if w3 == 2 else rng + [v3]
                    elif f3 == 2:
                        sym = v3.decode("utf-8", "replace")
                    elif f3 == 3:
                        roles = v3
                if rng and sym:
                    doc["occ"].append((rng[0], sym, roles))
        docs.append(doc)
    return docs


# ---------- SCIP -> our edges ----------
def _kind_ok(sym: str) -> bool:
    """Callable or type symbols only: descriptors ending `().` (function/method) or `#` (class/interface)."""
    return not sym.startswith("local ") and (sym.endswith(").") or sym.endswith("#"))


def edges_from(idx: dict, docs: list[dict]) -> tuple[list[dict], set[str]]:
    by_file: dict[str, list[dict]] = {}
    for s in idx["symbols"]:
        if s["kind"] != "module":
            by_file.setdefault(s["file"], []).append(s)

    def containing(path: str, line1: int):
        best = None
        for s in by_file.get(path, []):
            if s["line"] <= line1 <= s["end_line"] and (best is None or s["end_line"] - s["line"] < best["end_line"] - best["line"]):
                best = s
        return best

    defs: dict[str, dict] = {}
    for d in docs:
        for line0, sym, roles in d["occ"]:
            if roles & ROLE_DEFINITION and not sym.startswith("local "):
                t = containing(d["path"], line0 + 1)
                if t is None:
                    continue
                # callables/types always; other terms only when they ARE the symbol (`const f = () => ...`)
                if _kind_ok(sym) or (t["kind"] == "function" and t["line"] == line0 + 1 and "(" not in sym.rsplit("/", 1)[-1].split("`")[-1][-3:]):
                    defs.setdefault(sym, t)
    edges, covered = [], {d["path"] for d in docs}
    seen = set()
    for d in docs:
        for line0, sym, roles in d["occ"]:
            if roles & ROLE_DEFINITION or sym not in defs:
                continue
            src = containing(d["path"], line0 + 1)
            dst = defs[sym]
            if src is None or src["id"] == dst["id"]:
                continue
            if src["kind"] == "class" and dst["kind"] == "class":
                continue  # inheritance / annotation between classes is not a call
            k = (src["id"], dst["id"])
            if k in seen:
                continue
            seen.add(k)
            edges.append({"src": src["id"], "dst": dst["id"], "type": "tests" if src["is_test"] else "calls", "conf": "precise"})
    return edges, covered


def merge(idx: dict, scip_edges: list[dict], covered: set[str]) -> dict:
    """Combine structural edges (idx['edges']) with SCIP edges in place; returns stats."""
    precise = {(e["src"], e["dst"], e["type"]) for e in scip_edges}
    precise_pairs = {(s, d) for s, d, _ in precise}
    kept, dropped, upgraded = [], 0, 0
    for e in idx["edges"]:
        if e["type"] in ("calls", "tests") and not e["dst"].startswith("table:"):
            file = e["src"].split("#")[0]
            if (e["src"], e["dst"]) in precise_pairs:
                upgraded += 1
                continue  # replaced by the precise copy below
            if e["conf"] == "ambiguous" and file in covered:
                dropped += 1
                continue
        kept.append(e)
    idx["edges"] = sorted(kept + scip_edges, key=lambda e: (e["src"], e["dst"], e["type"]))
    return {"edges": len(scip_edges), "upgraded": upgraded, "dropped_ambiguous": dropped}


# ---------- running the indexers ----------
def run_indexer(root: Path, idx: dict, timeout: int = 900) -> tuple[list[Path], list[str]]:
    """Run scip-python and/or scip-typescript for the languages present. Returns (scip files, messages)."""
    langs = {f["lang"] for f in idx["files"].values()}
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    out, msgs = [], []
    jobs = []
    if "python" in langs:
        jobs.append(("scip-python", ["@sourcegraph/scip-python", "index", "--project-name", idx.get("repo", root.name), "--project-version", "0"]))
    if langs & {"ts", "js"} and ((root / "tsconfig.json").exists() or (root / "package.json").exists()):
        jobs.append(("scip-typescript", ["@sourcegraph/scip-typescript", "index"] + ([] if (root / "tsconfig.json").exists() else ["--infer-tsconfig"])))
    if not npx:
        return [], ["npx not found: install Node.js, or pass an existing index with --scip-file"]
    for name, args in jobs:
        dest = Path(tempfile.mkdtemp(prefix="devmem-scip-")) / f"{name}.scip"
        try:
            r = subprocess.run([npx, "--yes", *args, "--output", str(dest)], cwd=str(root), capture_output=True, text=True,
                               errors="replace", timeout=timeout)
        except (subprocess.TimeoutExpired, OSError) as e:
            msgs.append(f"{name}: {e}")
            continue
        if dest.exists() and dest.stat().st_size > 60:
            out.append(dest)
        else:
            text = (r.stderr or r.stdout).strip()
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            why = next((ln for ln in lines if "error" in ln.lower()), lines[-1] if lines else "no output")
            msgs.append(f"{name} failed: {why[:160]}")
    return out, msgs


def build_cache(root: Path, idx: dict, scip_files: list[Path]) -> dict:
    edges, covered = [], set()
    for f in scip_files:
        e, c = edges_from(idx, parse(Path(f).read_bytes()))
        edges += e; covered |= c
    hashes = {p: idx["files"][p]["hash"] for p in covered if p in idx["files"]}
    cache = {"edges": edges, "covered": sorted(covered), "hashes": hashes}
    p = root / CACHE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cache), encoding="utf-8")
    return cache


def apply_cache(root: Path, idx: dict) -> dict | None:
    """Merge cached SCIP edges whose source AND target files are unchanged since the SCIP run."""
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
