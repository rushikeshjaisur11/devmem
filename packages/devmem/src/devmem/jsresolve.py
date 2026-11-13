"""Resolve import specifiers to repo files (shared by the graph builder and test selection): relative JS/TS specifiers
and dotted Python modules."""
import posixpath

JS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts")
JS_SUFFIXES = JS_EXTS


def is_js(path: str) -> bool:
    return path.endswith(JS_EXTS)


def resolve(files, importer: str, spec: str) -> list[str]:
    """`./x`, `../lib/y` -> the matching repo file (first hit); bare package names are external -> []."""
    if not spec.startswith("."):
        return []
    base = posixpath.normpath(posixpath.join(posixpath.dirname(importer), spec))
    cands = [base] + [base + e for e in JS_EXTS] + [f"{base}/index{e}" for e in JS_EXTS]
    if base.endswith((".js", ".jsx")):  # TS projects import './x.js' for x.ts
        stem = base.rsplit(".", 1)[0]
        cands += [stem + e for e in (".ts", ".tsx")]
    return [c for c in cands if c in files][:1]


_mod_cache: list = [None, 0, {}, {}]   # files dict it was built from, its size, suffix -> [files], file -> position


def module_files(files, module: str) -> list[str]:
    """Python files whose path ends with the dotted module (tolerates src/ roots), in the order `files` lists them.
    Backed by a path-suffix table built once per `files` dict: resolving every import of a repo is then linear, where
    scanning all files per import took minutes on a 1,400-file repo."""
    if not module:
        return []
    if _mod_cache[0] is not files or _mod_cache[1] != len(files):
        table: dict[str, list[str]] = {}
        for f in files:
            if f.endswith(".py"):
                parts = f.split("/")
                for i in range(len(parts)):
                    table.setdefault("/".join(parts[i:]), []).append(f)
        _mod_cache[:] = [files, len(files), table, {f: n for n, f in enumerate(files)}]
    table, pos = _mod_cache[2], _mod_cache[3]
    base = module.replace(".", "/")
    a, b = table.get(base + ".py", []), table.get(base + "/__init__.py", [])
    return sorted(a + b, key=pos.__getitem__) if a and b else list(a or b)
