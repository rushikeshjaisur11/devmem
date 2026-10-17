"""Resolve relative JS/TS import specifiers to repo files (shared by the graph builder and test selection)."""
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
