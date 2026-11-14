"""Hygiene runners: ruff, vulture, radon, deptry, bandit, sqlfluff + clones. Results -> .devmem/hygiene.json,
trend history -> ~/.devmem/hygiene/<repo>.jsonl, summary -> knowledge/hygiene.md."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import tempfile
from collections import Counter
from pathlib import Path

from devmem.runner import find_tool, run_argv

from .clones import clones_for_repo
from devmem.schema import atomic_write

HYGIENE_PATH = ".devmem/hygiene.json"
HISTORY_DIR = Path.home() / ".devmem" / "hygiene"
COMPLEXITY_MIN_RANK = "C"  # cyclomatic complexity > 10
CHUNK = 150


def _issue(tool, path, line, code, msg, **extra):
    return {"tool": tool, "path": path, "line": line, "code": code, "msg": msg, **extra}


def _chunks(items):
    for i in range(0, len(items), CHUNK):
        yield items[i:i + CHUNK]


def _rel(root: Path, p: str) -> str:
    p = p.replace("\\", "/")
    try:
        return Path(p).resolve().relative_to(root.resolve()).as_posix() if os.path.isabs(p) else os.path.normpath(p).replace("\\", "/")
    except ValueError:
        return p


def _has_ruff_config(root: Path) -> bool:
    py = (root / "pyproject.toml")
    return (root / "ruff.toml").exists() or (root / ".ruff.toml").exists() or (py.exists() and "[tool.ruff" in py.read_text(errors="replace"))


def run_ruff(root, py):
    t = find_tool("ruff")
    if not t:
        return None, "unavailable"
    base = [*t, "check", "--output-format", "json", "--no-cache"] + ([] if _has_ruff_config(root) else ["--select", "E,F,W,B"])
    out = []
    for ch in _chunks(py):
        r = run_argv(base + ch, root)
        try:
            for d in json.loads(r["out"] or "[]"):
                out.append(_issue("ruff", _rel(root, d["filename"]), d["location"]["row"], d["code"], d["message"]))
        except json.JSONDecodeError:
            return out, f"error: {r['err'][:100]}"
    return out, "ok"


def parse_eslint(text: str, root: Path) -> list[dict]:
    out = []
    for f in json.loads(text or "[]"):
        for m in f.get("messages", []):
            out.append(_issue("eslint", _rel(root, f["filePath"]), m.get("line", 1), m.get("ruleId") or "parse-error", m["message"],
                              severity="error" if m.get("severity") == 2 else "warning"))
    return out


def parse_knip(text: str) -> list[dict]:
    d = json.loads(text or "{}")
    out = [_issue("knip", f, 1, "unused-file", "file is not imported by anything") for f in d.get("files", [])]
    kinds = (("dependencies", "unused-dependency"), ("devDependencies", "unused-dev-dependency"), ("unlisted", "unlisted-dependency"),
             ("unresolved", "unresolved-import"), ("exports", "unused-export"), ("types", "unused-type"))
    for it in d.get("issues", []):
        for key, code in kinds:
            for e in it.get(key, []):
                out.append(_issue("knip", it["file"], e.get("line", 1), code, e["name"]))
    return out


def _js_tool(root: Path, name: str) -> list[str] | None:
    import shutil
    b = root / "node_modules" / ".bin"
    if not ((b / name).exists() or (b / f"{name}.cmd").exists()):
        return None
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    return [npx, "--no-install", name] if npx else None


def _has_eslint_config(root: Path) -> bool:
    return any(root.glob("eslint.config.*")) or any(root.glob(".eslintrc*"))


def run_eslint(root, js):
    if not js:
        return [], "-"
    if not _has_eslint_config(root):
        return None, "skipped: no eslint config"
    t = _js_tool(root, "eslint")
    if not t:
        return None, "unavailable: run npm install"
    out = []
    for ch in _chunks(js):
        r = run_argv([*t, "-f", "json", *ch], root)
        try:
            out += parse_eslint(r["out"], root)
        except (json.JSONDecodeError, KeyError):
            return out, f"error: {(r['err'] or r['out'])[:100]}"
    return out, "ok"


def run_knip(root, js):
    if not js:
        return [], "-"
    t = _js_tool(root, "knip")
    if not t:
        return None, "unavailable: add knip as a devDependency"
    r = run_argv([*t, "--reporter", "json"], root)
    try:
        return parse_knip(r["out"]), "ok"
    except (json.JSONDecodeError, KeyError):
        return None, f"error: {(r['err'] or r['out'])[:100]}"


def run_vulture(root, py, ix=None):
    t = find_tool("vulture")
    if not t:
        return None, "unavailable"
    out = []
    inbound = {}
    if ix:
        for e in ix["edges"]:
            inbound[e["dst"]] = inbound.get(e["dst"], 0) + 1
    by_file_name = {}
    if ix:
        for s in ix["symbols"]:
            by_file_name.setdefault((s["file"], s["name"]), []).append(s)
    r = run_argv([*t, *py, "--min-confidence", "60"], root)
    for line in r["out"].splitlines():
        m = re.match(r"(.+?):(\d+): unused (\w[\w ]*?) '(.+?)' \((\d+)% confidence\)", line)
        if not m:
            continue
        path, ln, kind, name, conf = _rel(root, m.group(1)), int(m.group(2)), m.group(3), m.group(4), int(m.group(5))
        corroborated = False
        if kind in ("function", "class", "method"):
            syms = by_file_name.get((path, name), [])
            corroborated = bool(syms) and all(inbound.get(s["id"], 0) == 0 for s in syms) and name not in ("main",) \
                and not name.startswith("__")
            if not corroborated and conf < 80:
                continue
        out.append(_issue("vulture", path, ln, f"unused-{kind.replace(' ', '-')}", f"unused {kind} '{name}'",
                          confidence=conf, corroborated=corroborated))
    return out, "ok"


def run_radon(root, py):
    t = find_tool("radon")
    if not t:
        return None, "unavailable"
    out = []
    for ch in _chunks(py):
        r = run_argv([*t, "cc", "-j", "-n", COMPLEXITY_MIN_RANK, *ch], root)
        try:
            for f, items in json.loads(r["out"] or "{}").items():
                for it in items:
                    out.append(_issue("radon", _rel(root, f), it["lineno"], f"CC{it['rank']}",
                                      f"{it['name']} complexity {it['complexity']}", complexity=it["complexity"]))
        except json.JSONDecodeError:
            return out, f"error: {r['err'][:100]}"
    return out, "ok"


def run_deptry(root):
    t = find_tool("deptry")
    if not t:
        return None, "unavailable"
    if not ((root / "pyproject.toml").exists() or list(root.glob("requirements*.txt"))):
        return [], "skipped: no dependency spec"
    with tempfile.TemporaryDirectory() as td:
        j = Path(td) / "d.json"
        r = run_argv([*t, ".", "--json-output", str(j)], root)
        if not j.exists():
            return [], f"error: {(r['err'] or r['out'])[-100:].strip()}"
        data = json.loads(j.read_text() or "[]")
    return [_issue("deptry", d["location"]["file"], d["location"].get("line") or 0, d["error"]["code"],
                   d["error"]["message"]) for d in data], "ok"


def run_bandit(root, py):
    t = find_tool("bandit")
    if not t:
        return None, "unavailable"
    files = [f for f in py if "/tests/" not in f"/{f}" and not Path(f).name.startswith("test_")]
    out = []
    for ch in _chunks(files):
        r = run_argv([*t, "-f", "json", "-q", *ch], root)
        try:
            for d in json.loads(r["out"] or "{}").get("results", []):
                if d["issue_severity"] in ("MEDIUM", "HIGH"):
                    out.append(_issue("bandit", _rel(root, d["filename"]), d["line_number"], d["test_id"],
                                      d["issue_text"], severity=d["issue_severity"]))
        except json.JSONDecodeError:
            return out, f"error: {r['err'][:100]}"
    return out, "ok"


def run_sqlfluff(root, sql):
    t = find_tool("sqlfluff")
    if not t:
        return None, "unavailable"
    if not sql:
        return [], "skipped: no sql files"
    dialect = [] if (root / ".sqlfluff").exists() else ["--dialect", "sparksql"]
    out = []
    for ch in _chunks(sql):
        r = run_argv([*t, "lint", "--format", "json", *dialect, *ch], root)
        try:
            for f in json.loads(r["out"] or "[]"):
                for v in f["violations"]:
                    out.append(_issue("sqlfluff", _rel(root, f["filepath"]), v["start_line_no"], v["code"], v["description"]))
        except json.JSONDecodeError:
            return out, f"error: {(r['err'] or r['out'])[:100]}"
    return out, "ok"


def run_hygiene(root: str | Path, idx: dict, name: str | None = None, history: bool = True) -> dict:
    root = Path(root).resolve()
    files = sorted(idx["files"])
    py = [f for f in files if f.endswith(".py")]
    sql = [f for f in files if f.endswith(".sql")]
    js = [f for f in files if f.endswith((".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts"))]
    issues, tools = [], {}
    for tool, fn in (("ruff", lambda: run_ruff(root, py)), ("vulture", lambda: run_vulture(root, py, idx)),
                     ("radon", lambda: run_radon(root, py)), ("deptry", lambda: run_deptry(root)),
                     ("bandit", lambda: run_bandit(root, py)), ("sqlfluff", lambda: run_sqlfluff(root, sql)),
                     ("eslint", lambda: run_eslint(root, js)), ("knip", lambda: run_knip(root, js))):
        res, status = fn()
        tools[tool] = status
        issues += res or []
    res = {"commit": idx.get("commit"), "ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "tools": tools, "issues": issues, "clones": clones_for_repo(root, files)}
    counts = Counter(i["tool"] for i in issues)
    counts["clones"] = len(res["clones"])
    res["counts"] = dict(counts)
    p = root / HYGIENE_PATH
    prev = {}
    if p.exists():
        try:
            prev = json.loads(p.read_text()).get("counts", {})
        except Exception:
            pass
    res["prev_counts"] = prev
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(p, json.dumps(res, indent=1))
    if history:
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        with open(HISTORY_DIR / f"{name or root.name}.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": res["ts"], "commit": res["commit"], "counts": res["counts"]}) + "\n")
    return res


def render_hygiene_md(h: dict) -> str:
    c, pc = h.get("counts", {}), h.get("prev_counts", {})
    issues = h["issues"]
    by = lambda t: [i for i in issues if i["tool"] == t]
    delta = lambda k: (f" ({c.get(k, 0) - pc[k]:+d})" if k in pc and c.get(k, 0) != pc[k] else "")
    lines = ["# Hygiene", "Current lint / dead-code / complexity / dependency / security state. Do not add new items of "
             "these kinds; fix them only when the task touches that code.", "",
             "| Tool | Findings | Status |", "| --- | --- | --- |"]
    for t in ("ruff", "vulture", "radon", "deptry", "bandit", "sqlfluff", "eslint", "knip"):
        if h["tools"].get(t, "-") == "-":
            continue
        lines.append(f"| {t} | {c.get(t, 0)}{delta(t)} | {h['tools'].get(t, '-')} |")
    lines.append(f"| clones | {c.get('clones', 0)}{delta('clones')} | ok |")

    rules = Counter(i["code"] for i in by("ruff"))
    if rules:
        ex = {i["code"]: i for i in by("ruff")}
        lines += ["", "## Lint rules in force (ruff, most frequent)"]
        lines += [f"- `{k}` x{n}: {ex[k]['msg'][:80]}" for k, n in rules.most_common(6)]
    dead = sorted([i for i in by("vulture") if i["corroborated"] or i["confidence"] >= 80],
                  key=lambda i: (-i["confidence"], i["path"], i["line"]))
    if dead:
        lines += ["", "## Dead code (do not extend or call; remove only if asked)"]
        lines += [f"- `{i['path']}:{i['line']}` {i['msg']}" + (" (no callers in graph)" if i["corroborated"] else "") for i in dead[:10]]
        if len(dead) > 10:
            lines.append(f"- ... {len(dead) - 10} more")
    cx = sorted(by("radon"), key=lambda i: -i["complexity"])
    if cx:
        lines += ["", "## Complex functions (change carefully, keep edits minimal)"]
        lines += [f"- `{i['path']}:{i['line']}` {i['msg']}" for i in cx[:8]]
    dp = by("deptry")
    if dp:
        lines += ["", "## Dependencies"] + [f"- {i['code']} {i['msg']}" for i in dp[:8]]
    sec = by("bandit")
    if sec:
        lines += ["", "## Security (bandit, medium+)"] + [f"- `{i['path']}:{i['line']}` {i['code']} {i['msg'][:90]}" for i in sec[:8]]
    sq = by("sqlfluff")
    if sq:
        sc = Counter(i["code"] for i in sq)
        lines += ["", "## SQL style (sqlfluff)"] + [f"- `{k}` x{n}" for k, n in sc.most_common(5)]
    es = by("eslint")
    if es:
        ec = Counter(i["code"] for i in es)
        lines += ["", "## JS/TS lint rules in force (eslint, most frequent)"] + [f"- `{k}` x{n}" for k, n in ec.most_common(6)]
    kn = by("knip")
    if kn:
        files_ = [i for i in kn if i["code"] == "unused-file"]
        if files_:
            lines += ["", "## Unused JS/TS files (do not extend; remove only if asked)"] + [f"- `{i['path']}`" for i in files_[:8]]
        ex = [i for i in kn if i["code"] in ("unused-export", "unused-type")]
        if ex:
            lines += ["", "## Unused exports"] + [f"- `{i['path']}:{i['line']}` {i['msg']}" for i in ex[:8]]
        dp = [i for i in kn if "dependency" in i["code"] or i["code"] == "unresolved-import"]
        if dp:
            lines += ["", "## JS dependencies"] + [f"- {i['code']} {i['msg']}" for i in dp[:8]]
    if h["clones"]:
        lines += ["", "## Duplicated code (reuse, do not copy again)"]
        lines += [f"- `{c_['a']}:{c_['a_lines'][0]}-{c_['a_lines'][1]}` ~ `{c_['b']}:{c_['b_lines'][0]}-{c_['b_lines'][1]}`"
                  for c_ in h["clones"][:6]]
    return "\n".join(lines)
