"""Measured test coverage, so "which tests reach this function" and "is this code tested" come from a real test run instead
of being inferred from calls (which misses tests that reach code through fixtures, helpers and frameworks).

Reads what test tools already write:
  - coverage.py JSON (`coverage json --show-contexts`): executed lines AND which test ran each line, when the run recorded
    contexts (`[run] dynamic_context = test_function`, or pytest-cov `--cov-context=test`);
  - Cobertura XML (coverage.py `coverage xml`, many JS / .NET tools), lcov (`lcov.info`: Jest, Vitest, c8, nyc, go tools),
    JaCoCo XML (Java / Kotlin): executed lines only.
Line hits are mapped to the functions they fall in and stored per symbol id, so they survive later edits that move lines.
Result: `.devmem/coverage.json`, read by the agent CLI (devmem/index.py)."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from devmem.index import Index
from devmem.schema import atomic_write

PATH = ".devmem/coverage.json"
CANDIDATES = ("coverage.json", "coverage.xml", "lcov.info", "coverage/lcov.info", "coverage/coverage-final.lcov", "coverage/cobertura-coverage.xml",
              "target/site/jacoco/jacoco.xml", "build/reports/jacoco/test/jacocoTestReport.xml", "htmlcov/coverage.json", "cov.json", "cov.xml")


class CoverageError(ValueError):
    pass


def _repo_path(p: str, files: set[str]) -> str | None:
    p = p.replace("\\", "/")
    if p in files:
        return p
    parts = [x for x in p.split("/") if x]
    for i in range(1, len(parts)):                      # an absolute or longer path: strip leading folders until it matches
        cand = "/".join(parts[i:])
        if cand in files:
            return cand
    hits = [f for f in files if f.endswith("/" + p)]     # a shorter path (JaCoCo package/File.java): unique suffix only
    return hits[0] if len(hits) == 1 else None


def _parse(path: Path) -> tuple[str, dict[str, set[int]], dict[str, dict[int, set[str]]]]:
    """(format, {file: executed lines}, {file: {line: test contexts}})."""
    text = path.read_text(encoding="utf-8", errors="replace")
    head = text.lstrip()[:400]
    lines: dict[str, set[int]] = {}
    ctx: dict[str, dict[int, set[str]]] = {}
    if head.startswith("{"):
        try:
            d = json.loads(text)
        except ValueError as e:
            raise CoverageError(f"not valid JSON: {e}") from None
        if not isinstance(d.get("files"), dict):
            raise CoverageError("JSON without a \"files\" section: produce it with `coverage json --show-contexts`")
        for f, rec in d["files"].items():
            lines[f] = set(rec.get("executed_lines") or [])
            for ln, names in (rec.get("contexts") or {}).items():
                named = {n for n in names if n}
                if named:
                    ctx.setdefault(f, {})[int(ln)] = named
        return "coverage.py", lines, ctx
    if head.startswith(("TN:", "SF:")) or "\nSF:" in text[:2000]:
        cur = None
        for ln in text.splitlines():
            if ln.startswith("SF:"):
                cur = ln[3:].strip(); lines.setdefault(cur, set())
            elif ln.startswith("DA:") and cur:
                n, _, hits = ln[3:].partition(",")
                if hits.split(",")[0].strip() not in ("0", ""):
                    lines[cur].add(int(n))
            elif ln.startswith("end_of_record"):
                cur = None
        return "lcov", lines, ctx
    if head.startswith("<"):
        try:
            root = ET.fromstring(text)
        except ET.ParseError as e:
            raise CoverageError(f"not valid XML: {e}") from None
        if root.tag == "report":                         # JaCoCo
            for pkg in root.iter("package"):
                for sf in pkg.iter("sourcefile"):
                    f = f"{pkg.get('name', '')}/{sf.get('name', '')}".lstrip("/")
                    lines[f] = {int(l.get("nr")) for l in sf.iter("line") if int(l.get("ci") or 0) > 0}
            return "JaCoCo", lines, ctx
        if root.tag == "coverage":                       # Cobertura
            for cls in root.iter("class"):
                f = cls.get("filename") or ""
                hit = {int(l.get("number")) for l in cls.iter("line") if int(l.get("hits") or 0) > 0}
                lines.setdefault(f, set()).update(hit)
            return "Cobertura", lines, ctx
    raise CoverageError("unrecognised format: give coverage.py JSON, Cobertura or JaCoCo XML, or an lcov file")


def _test_symbol(ix: Index, name: str, by_qual: dict[str, list[dict]]) -> str | None:
    """A coverage context -> the test's symbol id (or a path#qualname id pytest can still run)."""
    name = name.split("|")[0].strip()
    if "::" in name:                                     # pytest node id: tests/test_x.py::Class::test_y[param]
        f, *rest = name.split("::")
        q = ".".join(re.sub(r"\[.*\]$", "", r) for r in rest)
        sid = f"{f}#{q}"
        if sid in ix.symbols:
            return sid
        files = set(ix.data["files"])
        rf = _repo_path(f, files)
        return f"{rf}#{q}" if rf else None
    parts = name.split(".")                              # dotted: module.function or module.Class.method
    for k in range(1, min(3, len(parts)) + 1):
        q = ".".join(parts[-k:])
        mod = parts[:-k]
        cands = [s for s in by_qual.get(q, []) if s.get("is_test")]
        if mod:
            want = "/".join(mod)
            cands = [s for s in cands if s["file"].rsplit(".", 1)[0].endswith(want)] or cands
        if len(cands) == 1:
            return cands[0]["id"]
    return None


def import_file(ix: Index, path: Path) -> dict:
    fmt, raw_lines, raw_ctx = _parse(Path(path))
    files = set(ix.data["files"])
    by_qual: dict[str, list[dict]] = {}
    for s in ix.symbols.values():
        by_qual.setdefault(s["qualname"], []).append(s)
    lines = {}
    ctx = {}
    unmatched = 0
    newer: list[str] = []
    report_time = Path(path).stat().st_mtime_ns
    for f, ls in raw_lines.items():
        rf = _repo_path(f, files)
        if rf is None:
            unmatched += 1
            continue
        try:   # edited after the test run: its line numbers no longer match the report, so mapping them would be guessing
            if (Path(ix.root) / rf).stat().st_mtime_ns > report_time:
                newer.append(rf)
                continue
        except OSError:
            continue
        lines.setdefault(rf, set()).update(ls)
        if f in raw_ctx:
            ctx.setdefault(rf, {}).update(raw_ctx[f])
    if not lines and newer:
        raise CoverageError(f"every file in this report changed after it was produced ({', '.join(newer[:3])}...): run the tests again and import the new report")
    if not lines:
        raise CoverageError(f"none of the {len(raw_lines)} files in this report are files of this repo: was it produced from this repo?")
    functions, tests, unknown_ctx = {}, {}, set()
    resolved: dict[str, str | None] = {}
    for f, hit in lines.items():
        for s in ix.by_file.get(f, []):
            if s["kind"] not in ("function", "method") or s.get("is_test"):
                continue
            body = range(s["line"] + 1, s.get("end_line", s["line"]) + 1)   # the def line runs at import: it says nothing about tests
            total = len(body)
            covered = sorted(n for n in body if n in hit)
            if total:
                functions[s["id"]] = [len(covered), total]
            names = set()
            for n in covered:
                names |= ctx.get(f, {}).get(n, set())
            ids = set()
            for nm in names:
                if nm not in resolved:
                    resolved[nm] = _test_symbol(ix, nm, by_qual)
                if resolved[nm]:
                    ids.add(resolved[nm])
                else:
                    unknown_ctx.add(nm)
            if ids:
                tests[s["id"]] = sorted(ids)
    hashes = {f: (ix.data["files"].get(f) or {}).get("hash") for f in lines}
    hashes.update({f: None for f in newer})        # None: no usable data, the code moved on after the run
    data = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "source": Path(path).name, "format": fmt, "per_test": bool(raw_ctx),
            "functions": functions, "tests": tests, "hashes": hashes,
            "stats": {"files": len(lines), "unmatched_files": unmatched, "functions": len(functions), "covered": sum(1 for v in functions.values() if v[0]),
                      "with_tests": len(tests), "unknown_contexts": len(unknown_ctx), "newer_than_report": len(newer)}}
    atomic_write(Path(ix.root) / PATH, json.dumps(data, indent=1, sort_keys=True))
    return data


def find_report(root: Path) -> Path | None:
    """A coverage report the repo's own tooling left in a usual place, newest first."""
    found = [Path(root) / c for c in CANDIDATES if (Path(root) / c).is_file()]
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


def auto_import(ix: Index) -> dict | None:
    """Import a report found in the repo when it is newer than the last import. Used on index and publish."""
    rep = find_report(Path(ix.root))
    if not rep:
        return None
    try:
        last = json.loads((Path(ix.root) / PATH).read_text(encoding="utf-8"))
        if last.get("source") == rep.name and last.get("mtime") == rep.stat().st_mtime_ns:
            return None
    except (OSError, ValueError):
        pass
    try:
        data = import_file(ix, rep)
    except CoverageError:
        return None
    data["mtime"] = rep.stat().st_mtime_ns
    atomic_write(Path(ix.root) / PATH, json.dumps(data, indent=1, sort_keys=True))
    return data
