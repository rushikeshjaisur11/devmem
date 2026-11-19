"""Edge accuracy benchmark: score devmem's final index against SCIP on real code, and compare with a saved run.

    python scripts/benchmark.py                                  # the default corpus (PyPI + pinned TypeScript repos)
    python scripts/benchmark.py --save docs/benchmark.json       # ... and records it
    python scripts/benchmark.py --check docs/benchmark.json      # fails if precision or recall fell against that record
    python scripts/benchmark.py --repo ~/src/myapp --repo ~/src/web   # your own checkouts (Python or TypeScript)

Needs network the first time (pip downloads the sdists, npx fetches scip-python / scip-typescript), Node.js 18+ and
graphifyy. Each corpus entry is indexed in a temporary copy: the oracle is SCIP (type-resolved), Graphify runs as in
production (output under a temporary devmem home), and only library code is scored, since in a sdist's tests/ the
package itself is not importable and SCIP cannot resolve it.

Columns:
  precision      share of devmem's call edges (calls, tests, refs) that SCIP also has; `fast` = structural only,
                 `full` = after the Graphify merge (what an index built in full mode serves, before SCIP is merged in)
  recall         share of SCIP's references devmem has an edge for, counting `uses` (type hints, isinstance, except)
  call recall    the same, counting only references written as a call (`name(` on that line)
  uses prec.     share of `uses` edges that are not also call edges and that SCIP confirms (from functions and methods:
                 SCIP's edges leave out class -> class pairs, so a class's field types cannot be scored)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

CORPUS = [  # Python: (pip requirement, package directory inside the sdist); TypeScript: (git URL@commit, None)
    ("requests==2.32.3", "src/requests"),
    ("click==8.1.7", "src/click"),
    ("starlette==0.41.3", "starlette"),
    ("httpx==0.28.1", "httpx"),
    ("rich==13.9.4", "rich"),
    ("https://github.com/sindresorhus/ky@3541888878c4d303a8bc2ad61815f4e048f55a5d", None),
    ("https://github.com/pmndrs/zustand@9fed2eb2f3af672da9f7bf694b996df3831fc207", None),
    ("https://github.com/sindresorhus/p-queue@7f272ab5ff2254c0dc2537b7977e88ede1cd55e8", None),
    ("https://github.com/honojs/hono@75ff8e3ea18485589816881c6278bc9783e56602", None),
]
TEST_DIRS = ("tests/", "test/", "testing/")
TOLERANCE = 0.01


def sh(argv, cwd=None, timeout=900, env=None) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, errors="replace", timeout=timeout, env=env)


def fetch_sdist(req: str, sub: str, work: Path) -> Path:
    dl = work / "dl"
    dl.mkdir(parents=True, exist_ok=True)
    r = sh([sys.executable, "-m", "pip", "download", "--no-deps", "--no-binary", ":all:", "-q", "-d", str(dl), req])
    name = req.split("==")[0].lower().replace("_", "-")
    tgz = next((p for p in dl.glob("*.tar.gz") if p.name.lower().replace("_", "-").startswith(name + "-")), None)
    if tgz is None:
        raise RuntimeError(f"{req}: pip download failed: {(r.stderr or r.stdout).strip()[-200:]}")
    with tarfile.open(tgz) as t:
        t.extractall(dl / name, filter="data")
    top = next((dl / name).iterdir())
    dest = work / name
    shutil.rmtree(dest, ignore_errors=True)
    (dest / sub).parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(top / sub, dest / sub)
    return dest


def fetch_git(spec: str, work: Path) -> Path:
    url, sha = spec.rsplit("@", 1)
    dest = work / url.rstrip("/").rsplit("/", 1)[-1]
    if not (dest / ".git").exists():
        shutil.rmtree(dest, ignore_errors=True)
        dest.mkdir(parents=True)
        for argv in (["git", "init", "-q"], ["git", "remote", "add", "origin", url], ["git", "fetch", "-q", "--depth", "1", "origin", sha],
                     ["git", "checkout", "-q", "FETCH_HEAD"]):
            r = sh(argv, cwd=dest)
            if r.returncode:
                raise RuntimeError(f"{spec}: {' '.join(argv[:2])} failed: {(r.stderr or r.stdout).strip()[-200:]}")
    return dest


def scip_index(root: Path, out: Path) -> Path:
    ts = (root / "tsconfig.json").exists() or (root / "package.json").exists()
    if ts and not (root / "node_modules").exists():
        sh(["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"], cwd=root)
    args = (["@sourcegraph/scip-typescript", "index"] + ([] if (root / "tsconfig.json").exists() else ["--infer-tsconfig"]) if ts
            else ["@sourcegraph/scip-python", "index", "--project-name", root.name, "--project-version", "0"])
    r = sh([shutil.which("npx") or "npx", "--yes", *args, "--output", str(out)], cwd=root)
    if not out.exists() or out.stat().st_size < 60:
        raise RuntimeError(f"{root.name}: SCIP failed: {(r.stderr or r.stdout).strip()[-200:]}")
    return out


def score(root: Path, scip_file: Path) -> dict:
    from devmem_app import graphify, scip
    from devmem_app.indexer import index_repo

    copy = Path(tempfile.mkdtemp(prefix="devmem-bench-")) / root.name
    shutil.copytree(root, copy, ignore=shutil.ignore_patterns("node_modules", ".git", ".devmem", "graphify-out"))
    graph, msg = graphify.run_graphify(copy)
    if graph is None:
        raise RuntimeError(f"{root.name}: {msg}")

    def build(**kw):
        (copy / graphify.CACHE).unlink(missing_ok=True)
        return index_repo(copy, root.name, write=True, accuracy="fast", **kw)

    idx = build()
    syms = {s["id"]: s for s in idx["symbols"]}
    docs = scip.parse(scip_file.read_bytes())
    oracle, covered = scip.edges_from(idx, docs)

    def lib(sid: str) -> bool:
        f = sid.split("#")[0]
        return f in covered and not f.startswith(TEST_DIRS) and ".test." not in f and syms.get(sid, {}).get("kind") not in (None, "module")
    O = {(e["src"], e["dst"]) for e in oracle if lib(e["src"])}

    def edges(ix, types):
        return {(e["src"], e["dst"]) for e in ix["edges"] if e["type"] in types and lib(e["src"]) and e["dst"] in syms}
    CALLS = ("calls", "tests", "refs")
    fast = edges(idx, CALLS)
    full_idx = build(graphify=graph)
    full, uses = edges(full_idx, CALLS), edges(full_idx, ("uses",))

    # which oracle references are written as a call: the referenced name followed by "(" on one of its lines
    by_file: dict[str, list[dict]] = {}
    for s in idx["symbols"]:
        if s["kind"] != "module":
            by_file.setdefault(s["file"], []).append(s)

    def containing(path, line):
        best = None
        for s in by_file.get(path, []):
            if s["line"] <= line <= s["end_line"] and (best is None or s["end_line"] - s["line"] < best["end_line"] - best["line"]):
                best = s
        return best
    defs, sites = {}, {}
    for d in docs:
        for line0, sym, roles in d["occ"]:
            if roles & scip.ROLE_DEFINITION and not sym.startswith("local "):
                t = containing(d["path"], line0 + 1)
                if t:
                    defs.setdefault(sym, t)
    text: dict[str, list[str]] = {}
    for d in docs:
        for line0, sym, roles in d["occ"]:
            if roles & scip.ROLE_DEFINITION or sym not in defs:
                continue
            s = containing(d["path"], line0 + 1)
            if s is None:
                continue
            if d["path"] not in text:
                try:
                    text[d["path"]] = (copy / d["path"]).read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    text[d["path"]] = []
            ln = text[d["path"]][line0] if line0 < len(text[d["path"]]) else ""
            if re.search(rf"\b{re.escape(defs[sym]['name'])}\s*\(", ln):
                sites.setdefault((s["id"], defs[sym]["id"]), True)
    called = {k for k in O if k in sites}
    # SCIP's edges leave out class -> class pairs (a base class, a field's type), so those `uses` edges cannot be scored
    new_uses = {k for k in uses - full if syms[k[0]]["kind"] != "class"}

    def ratio(a, b):
        return round(a / b, 3) if b else None
    return {"oracle": len(O), "edges": len(full),
            "precision_fast": ratio(len(fast & O), len(fast)), "precision_full": ratio(len(full & O), len(full)),
            "recall": ratio(len((full | uses) & O), len(O)), "call_recall": ratio(len((full | uses) & called), len(called)),
            "uses_precision": ratio(len(new_uses & O), len(new_uses))}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", action="append", default=[], help="a local checkout to score (repeatable); replaces the default corpus")
    ap.add_argument("--save", help="write the results to this JSON file")
    ap.add_argument("--check", help="compare with this JSON file; exit 1 if a precision or recall fell by more than %.2f" % TOLERANCE)
    ap.add_argument("--work", help="working directory (default: a temporary one)")
    a = ap.parse_args()
    work = Path(a.work or tempfile.mkdtemp(prefix="devmem-bench-work-"))
    work.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("DEVMEM_HOME", str(work / "home"))       # Graphify's output and devmem state stay out of ~/.devmem
    targets = [(Path(p).expanduser().resolve(), None) for p in a.repo] or [(None, c) for c in CORPUS]
    results, failed = {}, []
    for path, entry in targets:
        name = path.name if path else entry[0].split("@")[0].rstrip("/").rsplit("/", 1)[-1] if entry[1] is None else entry[0]
        try:
            root = path or (fetch_git(entry[0], work) if entry[1] is None else fetch_sdist(entry[0], entry[1], work))
            results[name] = score(root, scip_index(root, work / f"{root.name}.scip"))
        except Exception as e:
            failed.append(f"{name}: {e}")
            continue
        r = results[name]
        print(f"{name:20} precision fast {r['precision_fast']}  full {r['precision_full']}  recall {r['recall']}  "
              f"call recall {r['call_recall']}  uses prec. {r['uses_precision']}  ({r['edges']} edges, {r['oracle']} oracle)", flush=True)
    for f in failed:
        print(f"FAILED {f}", file=sys.stderr)
    if a.save:
        Path(a.save).write_text(json.dumps(results, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    worse = []
    if a.check:
        base = json.loads(Path(a.check).read_text(encoding="utf-8"))
        for name, r in results.items():
            for k in ("precision_fast", "precision_full", "recall", "call_recall", "uses_precision"):
                b = (base.get(name) or {}).get(k)
                if b is not None and r.get(k) is not None and r[k] < b - TOLERANCE:
                    worse.append(f"{name} {k}: {b} -> {r[k]}")
        print("\n".join(["WORSE than the record:"] + worse) if worse else f"no metric fell by more than {TOLERANCE} against {a.check}")
    return 1 if failed or worse else 0


if __name__ == "__main__":
    sys.exit(main())
