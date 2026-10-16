"""diffcheck: minimal-change violations in the current diff. Stdlib + PyYAML; no network.

Checks (config names in .devmem/config.yaml `checks:`):
  size, out_of_scope_files, format_only_changes, new_dependencies, deleted_tests,
  api_signature_changes, duplicate_helpers
Escape hatch: a line `devmem-allow: <check> <reason>` in the PR body / commit message / --allow text lets that
check pass; it is still listed so the reviewer sees it. (lint-fixes-outside-changed-lines needs ruff: later.)
"""
from __future__ import annotations

import ast
import re
import subprocess
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .ignore import Ignore
from .index import Index
from .schema import INDEX_PATH

# check key in config -> short name used in violations and devmem-allow lines
CHECKS = {
    "size": "size",
    "out_of_scope_files": "out_of_scope",
    "format_only_changes": "format_only",
    "new_dependencies": "new_dependency",
    "deleted_tests": "deleted_test",
    "api_signature_changes": "api_change",
    "duplicate_helpers": "duplicate",
    "lint_fixes_outside_changed_lines": "lint_fix",
}
DEFAULTS = {"diff_budget": {"max_changed_lines": 200, "max_files": 8}, "checks": {k: True for k in CHECKS}}
NON_CODE = {".md", ".txt", ".rst", ".yaml", ".yml", ".json", ".toml", ".cfg", ".ini", ".sql", ".csv"}
ALLOW_RE = re.compile(r"devmem-allow:\s*([a-z_]+)\s*(.*)", re.I)


@dataclass
class Violation:
    check: str
    path: str
    msg: str
    allowed: str | None = None


@dataclass
class FileDiff:
    path: str
    status: str  # A, M, D
    hunks: list = field(default_factory=list)  # (old_start, old_count, new_start, new_count, removed, added)

    @property
    def added(self): return sum(len(h[5]) for h in self.hunks)
    @property
    def removed(self): return sum(len(h[4]) for h in self.hunks)


def _git(root: Path, *args: str, check=True) -> str:
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, errors="replace")
    if check and r.returncode:
        raise RuntimeError(r.stderr.strip() or f"git {' '.join(args)} failed")
    return r.stdout


def repo_root() -> Path:
    return Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                               check=True).stdout.strip())


def parse_diff(text: str) -> list[FileDiff]:
    files, cur, hunk = [], None, None
    for line in text.splitlines():
        if line.startswith("diff --git "):
            m = re.match(r"diff --git a/(.*) b/(.*)$", line)
            cur = FileDiff(m.group(2), "M"); files.append(cur); hunk = None
        elif cur and line.startswith("new file mode"):
            cur.status = "A"
        elif cur and line.startswith("deleted file mode"):
            cur.status = "D"
        elif cur and line.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            o, oc, n, nc = int(m.group(1)), int(m.group(2) or 1), int(m.group(3)), int(m.group(4) or 1)
            hunk = [o, oc, n, nc, [], []]; cur.hunks.append(hunk)
        elif hunk is not None and line.startswith("-") and not line.startswith("---"):
            hunk[4].append(line[1:])
        elif hunk is not None and line.startswith("+") and not line.startswith("+++"):
            hunk[5].append(line[1:])
    return files


def collect_diff(root: Path, base: str | None, staged: bool) -> list[FileDiff]:
    if base:
        text = _git(root, "diff", "-U0", "--no-color", "--no-renames", f"{base}...HEAD")
    elif staged:
        text = _git(root, "diff", "--cached", "-U0", "--no-color", "--no-renames")
    else:
        text = _git(root, "diff", "HEAD", "-U0", "--no-color", "--no-renames")
    files = parse_diff(text)
    if not base and not staged:  # untracked files count as added
        for p in _git(root, "ls-files", "--others", "--exclude-standard").splitlines():
            try:
                lines = (root / p).read_text(encoding="utf-8").splitlines()
            except Exception:
                continue
            files.append(FileDiff(p, "A", [[0, 0, 1, len(lines), [], lines]]))
    ig = Ignore.for_repo(root)
    return [f for f in files if not f.path.startswith(".devmem/") and not ig.matches(f.path)]


def load_config(root: Path) -> dict:
    cfg = {"diff_budget": dict(DEFAULTS["diff_budget"]), "checks": dict(DEFAULTS["checks"])}
    p = root / ".devmem/config.yaml"
    if p.exists():
        user = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        for k in ("diff_budget", "checks"):
            cfg[k].update(user.get(k) or {})
    return cfg


def _is_test(path: str) -> bool:
    b = path.rsplit("/", 1)[-1]
    return "/tests/" in f"/{path}" or b.startswith("test_") or b.endswith("_test.py")


def _old(root: Path, base: str | None, path: str) -> str | None:
    ref = f"{base}:{path}" if base else f"HEAD:{path}"
    r = subprocess.run(["git", "-C", str(root), "show", ref], capture_output=True, text=True, errors="replace")
    return r.stdout if r.returncode == 0 else None


def _new(root: Path, path: str) -> str | None:
    try:
        return (root / path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _parse(src: str | None):
    try:
        return ast.parse(src) if src else None
    except SyntaxError:
        return None


def _funcs(tree, public_only=False):
    """{qualname: node} for top-level functions and methods of top-level classes."""
    out = {}
    if tree is None:
        return out
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not (public_only and n.name.startswith("_")):
                out[n.name] = n
        elif isinstance(n, ast.ClassDef) and not (public_only and n.name.startswith("_")):
            if public_only is False or True:
                out[n.name] = n
            for m in n.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not (public_only and m.name.startswith("_") and m.name != "__init__"):
                        out[f"{n.name}.{m.name}"] = m
    return out


def _compatible(old: ast.arguments, new: ast.arguments) -> bool:
    on = [a.arg for a in old.posonlyargs + old.args]
    nn = [a.arg for a in new.posonlyargs + new.args]
    if nn[: len(on)] != on:
        return False
    defaulted = set(nn[len(nn) - len(new.defaults):]) if new.defaults else set()
    if any(a not in defaulted for a in nn[len(on):]):
        return False
    if len(new.defaults) < len(old.defaults):
        return False
    okw = {a.arg for a in old.kwonlyargs}
    nkw = {a.arg: d for a, d in zip(new.kwonlyargs, new.kw_defaults)}
    if not okw <= set(nkw) or any(nkw[a] is None for a in nkw if a not in okw):
        return False
    return not ((old.vararg and not new.vararg) or (old.kwarg and not new.kwarg))


def _sig(n) -> str:
    if isinstance(n, ast.ClassDef):
        return f"class {n.name}"
    return f"def {n.name}({ast.unparse(n.args)})"


def _body_dump(fn) -> str:
    body = list(fn.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    return "\n".join(ast.dump(b) for b in body)


def _edited_symbols(ix: Index | None, files: list[FileDiff]) -> set[str]:
    if ix is None:
        return set()
    by_file: dict[str, list[dict]] = {}
    for s in ix.symbols.values():
        by_file.setdefault(s["file"], []).append(s)
    edited = set()
    for f in files:
        if f.status == "A":
            continue
        syms = [s for s in by_file.get(f.path, []) if s["kind"] != "module"]
        for o, oc, _, _, _, _ in f.hunks:
            lo, hi = (o, o) if oc == 0 else (o, o + oc - 1)
            hit = [s for s in syms if s["line"] <= hi and s["end_line"] >= lo]
            if hit:  # innermost match(es)
                m = min(s["end_line"] - s["line"] for s in hit)
                edited |= {s["id"] for s in hit if s["end_line"] - s["line"] == m}
    return edited


def _ruff_lines(root: Path, path: str, text: str | None) -> list[tuple[int, str]]:
    """[(line, code)] ruff finds in `text` (stdin), same rule set as hygiene. [] if ruff is unavailable."""
    from .runner import find_tool, run_argv
    import json
    t = find_tool("ruff")
    if not t or text is None:
        return []
    cfg = (root / "ruff.toml").exists() or (root / ".ruff.toml").exists() or \
        ((root / "pyproject.toml").exists() and "[tool.ruff" in (root / "pyproject.toml").read_text(errors="replace"))
    r = run_argv([*t, "check", "--output-format", "json", "--no-cache", *([] if cfg else ["--select", "E,F,W,B"]),
                  "--stdin-filename", path, "-"], root, input=text)
    try:
        return [(d["location"]["row"], d["code"]) for d in json.loads(r["out"] or "[]")]
    except Exception:
        return []


def _symbol_at(ix, path: str, lo: int, hi: int) -> str:
    hit = [s for s in ix.symbols.values() if s["file"] == path and s["kind"] != "module"
           and s["line"] <= hi and s["end_line"] >= lo]
    if not hit:
        return f"{path}#<module>"
    return min(hit, key=lambda s: s["end_line"] - s["line"])["id"]


def run_checks(root: Path, files: list[FileDiff], cfg: dict, base: str | None = None) -> list[Violation]:
    v: list[Violation] = []
    on = cfg["checks"]
    try:
        ix = Index.load(root) if (root / INDEX_PATH).exists() else None
    except Exception:
        ix = None

    # size
    if on.get("size", True):
        total = sum(f.added + f.removed for f in files)
        b = cfg["diff_budget"]
        if total > b["max_changed_lines"]:
            v.append(Violation("size", "-", f"{total} changed lines > budget {b['max_changed_lines']}"))
        if len(files) > b["max_files"]:
            v.append(Violation("size", "-", f"{len(files)} files changed > budget {b['max_files']}"))

    # scope: cluster changed files by impact relations; files outside the main (largest) cluster are unrelated
    if on.get("out_of_scope_files", True) and ix is not None:
        changed = {f.path: f for f in files if f.status != "A"}
        parent = {p: p for p in changed}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for sid in _edited_symbols(ix, files):
            sym = ix.symbols[sid]
            related = set(ix.impact(sid).get("files", [])) | {c["file"] for c in ix.callees(sym)}
            related |= {t.split("#")[0] for t in ix.impact(sid).get("tests", [])}
            for other in related & set(changed):
                parent[find(other)] = find(sym["file"])
        groups: dict[str, list[str]] = {}
        for p in changed:
            groups.setdefault(find(p), []).append(p)
        code_groups = {g: [p for p in ps if not _is_test(p) and Path(p).suffix not in NON_CODE]
                       for g, ps in groups.items()}
        code_groups = {g: ps for g, ps in code_groups.items() if ps}
        if len(code_groups) > 1:
            weight = lambda ps: sum(changed[p].added + changed[p].removed for p in ps)
            main = max(code_groups, key=lambda g: (weight(code_groups[g]), g))
            for g, ps in code_groups.items():
                if g != main:
                    for p in ps:
                        v.append(Violation("out_of_scope", p, "unrelated to the main change in "
                                           + ", ".join(sorted(code_groups[main])[:3]) + " (no call/impact link)"))

    lint_hunks: list[tuple[str, str, int, bool]] = []  # (path, symbol, line, is_lint_fix)
    for f in files:
        # format-only hunks
        if on.get("format_only_changes", True) and f.status == "M":
            for o, oc, n, nc, rem, add in f.hunks:
                if rem and add and re.sub(r"\s+", "", "".join(rem)) == re.sub(r"\s+", "", "".join(add)):
                    v.append(Violation("format_only", f"{f.path}:{n}", f"whitespace/format-only change ({len(add)} lines)"))
        # new dependencies
        if on.get("new_dependencies", True):
            name = f.path.rsplit("/", 1)[-1]
            for _, _, n, _, rem, add in f.hunks:
                for line in add:
                    s = line.strip()
                    dep = (re.fullmatch(r"requirements[\w.-]*\.txt", name) and s and not s.startswith(("#", "-")))
                    dep = dep or (name == "pyproject.toml" and re.fullmatch(r'"[A-Za-z0-9_.\-\[\]]+\s*[<>=~!].*",?', s))
                    dep = dep or (name == "package.json" and re.fullmatch(r'"[@\w./-]+":\s*"[\^~<>=\d].*",?', s))
                    if dep:
                        v.append(Violation("new_dependency", f"{f.path}:{n}", f"new dependency line: {s}"))
        if not f.path.endswith(".py"):
            continue
        old_src = None if f.status == "A" else _old(root, base, f.path)
        new_src = None if f.status == "D" else _new(root, f.path)
        old_t, new_t = _parse(old_src), _parse(new_src)

        # lint-only edits (collected here, judged after all files are seen)
        if on.get("lint_fixes_outside_changed_lines", True) and ix is not None and f.status == "M":
            from collections import Counter
            ov, nv = _ruff_lines(root, f.path, old_src), _ruff_lines(root, f.path, new_src)
            oc_, nc_ = Counter(c for _, c in ov), Counter(c for _, c in nv)
            for o, oc, n, nc, rem, add in f.hunks:
                lo, hi = (o, o) if oc == 0 else (o, o + oc - 1)
                fixed = oc > 0 and any(lo <= ln <= hi and nc_[c] < oc_[c] for ln, c in ov)
                lint_hunks.append((f.path, _symbol_at(ix, f.path, lo, hi), n, fixed))

        # tests removed / weakened
        if on.get("deleted_tests", True) and _is_test(f.path) and old_t is not None:
            of, nf = _funcs(old_t), _funcs(new_t)
            for q, node in of.items():
                if not q.split(".")[-1].startswith("test") or isinstance(node, ast.ClassDef):
                    continue
                if q not in nf:
                    v.append(Violation("deleted_test", f"{f.path}", f"test removed: {q}"))
                    continue
                oa = sum(isinstance(x, ast.Assert) for x in ast.walk(node))
                na = sum(isinstance(x, ast.Assert) for x in ast.walk(nf[q]))
                if na < oa:
                    v.append(Violation("deleted_test", f.path, f"test weakened (asserts {oa}->{na}): {q}"))
                old_skip = any("skip" in ast.unparse(d) or "xfail" in ast.unparse(d) for d in node.decorator_list)
                new_skip = any("skip" in ast.unparse(d) or "xfail" in ast.unparse(d) for d in nf[q].decorator_list)
                if new_skip and not old_skip:
                    v.append(Violation("deleted_test", f.path, f"test disabled (skip/xfail added): {q}"))

        # public API signatures
        if on.get("api_signature_changes", True) and not _is_test(f.path) and old_t is not None:
            of, nf = _funcs(old_t, True), _funcs(new_t, True)
            for q, node in of.items():
                if q not in nf:
                    if f.status != "D" or True:
                        v.append(Violation("api_change", f.path, f"public symbol removed: {q}"))
                elif isinstance(node, ast.ClassDef) or isinstance(nf[q], ast.ClassDef):
                    continue
                elif not _compatible(node.args, nf[q].args):
                    v.append(Violation("api_change", f.path, f"signature changed: {_sig(node)} -> {_sig(nf[q])}"))

        # duplicates of existing helpers
        if on.get("duplicate_helpers", True) and ix is not None and new_t is not None and not _is_test(f.path):
            cands = {}
            for s in ix.symbols.values():
                if s["kind"] in ("function", "method") and not s["is_test"] and s["end_line"] - s["line"] >= 3:
                    cands.setdefault(s["end_line"] - s["line"], []).append(s)
            for q, node in _funcs(new_t).items():
                if isinstance(node, ast.ClassDef) or f"{f.path}#{q}" in ix.symbols:
                    continue
                span = node.end_lineno - node.lineno
                dump = _body_dump(node)
                same_name = [s for s in ix.symbols.values() if s["name"] == node.name and s["kind"] == "function"
                             and s["file"] != f.path and not node.name.startswith("_") and node.name != "main"]
                if same_name and "." not in q:
                    v.append(Violation("duplicate", f"{f.path}:{node.lineno}", f"`{node.name}` already exists at {same_name[0]['id']}"))
                    continue
                if span < 3:
                    continue
                for d in range(span - 2, span + 3):
                    for s in cands.get(d, []):
                        if s["file"] == f.path and s["qualname"] == q:
                            continue
                        try:
                            t = ast.parse(textwrap.dedent(ix.source(s)))
                            if _body_dump(t.body[0]) == dump:
                                v.append(Violation("duplicate", f"{f.path}:{node.lineno}", f"`{q}` has the same body as {s['id']}; reuse it"))
                                raise StopIteration
                        except StopIteration:
                            break
                        except Exception:
                            continue
    if lint_hunks and any(not fixed for *_, fixed in lint_hunks):
        by_sym: dict[tuple[str, str], list] = {}
        for path, sym, line, fixed in lint_hunks:
            by_sym.setdefault((path, sym), []).append((line, fixed))
        for (path, sym), hs in by_sym.items():
            if all(fx for _, fx in hs):
                v.append(Violation("lint_fix", f"{path}:{hs[0][0]}", f"lint-only edit in {sym.split('#')[-1]}, unrelated "
                                   "to the substantive change (leave existing lint issues alone)"))
    return v


def allowances(text: str) -> dict[str, str]:
    return {m.group(1).lower(): m.group(2).strip() or "(no reason given)" for m in ALLOW_RE.finditer(text or "")}


def apply_allow(violations: list[Violation], allow: dict[str, str]) -> tuple[list[Violation], list[Violation]]:
    blocked, passed = [], []
    for x in violations:
        if x.check in allow:
            x.allowed = allow[x.check]
            passed.append(x)
        else:
            blocked.append(x)
    return blocked, passed
