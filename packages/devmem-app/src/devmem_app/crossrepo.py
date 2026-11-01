"""Links between onboarded repos: a file in one repo that imports a module another onboarded repo defines.

Each repo's index only resolves imports inside that repo, so a shared library used by several services shows up as
unrelated islands. This matches import strings across repos instead:
- Python: `pkg.sub.mod` -> `pkg/sub/mod.py` or `pkg/sub/__init__.py` (optionally under `src/`) in another repo;
- JS/TS: `<package.json name>/sub/path` -> that path (with the usual extensions or `index.*`), or the package's entry file.
An import that resolves inside the importing repo itself never counts. Pure stdlib, works on the index JSON."""
from __future__ import annotations

import json
from pathlib import Path

JS_EXT = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


def _py_modules(files: dict) -> dict[str, str]:
    out = {}
    for f, meta in files.items():
        if meta.get("lang") != "python" or not f.endswith(".py"):
            continue
        parts = f[:-3].split("/")
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if not parts:
            continue
        out.setdefault(".".join(parts), f)
        if parts[0] == "src" and len(parts) > 1:
            out.setdefault(".".join(parts[1:]), f)
    return out


def _js_package(root: Path, files: dict) -> tuple[str, dict[str, str]] | None:
    pj = root / "package.json"
    if not pj.exists():
        return None
    try:
        meta = json.loads(pj.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    name = meta.get("name")
    if not isinstance(name, str) or not name:
        return None
    paths = {}
    for f in files:
        if f.endswith(JS_EXT):
            stem = f.rsplit(".", 1)[0]
            paths.setdefault(stem, f)
            if stem.endswith("/index"):
                paths.setdefault(stem[:-6], f)
            for pre in ("src/", "lib/"):
                if stem.startswith(pre):
                    paths.setdefault(stem[len(pre):], f)
    main = str(meta.get("module") or meta.get("main") or "index").lstrip("./").rsplit(".", 1)[0]
    entry = paths.get(main) or paths.get("index") or paths.get("src/index")
    if entry:
        paths[""] = entry
    return name, paths


def cross_links(repos: dict[str, tuple[Path, dict]]) -> list[dict]:
    """repos: name -> (root, index data). Returns one record per (importing file, imported file, imported name)."""
    py = {r: _py_modules(d["files"]) for r, (_, d) in repos.items()}
    js = {r: p for r, (root, d) in repos.items() if (p := _js_package(root, d["files"]))}
    out, seen = [], set()
    for src, (_, d) in repos.items():
        for f, meta in d["files"].items():
            for imp in meta.get("imports", []):
                mod, name = imp.get("module") or "", imp.get("name")
                if not mod or mod.startswith("."):
                    continue
                hit = None
                if meta.get("lang") == "python":
                    cands = ([f"{mod}.{name}"] if name else []) + [mod]
                    if any(c in py[src] for c in cands):
                        continue   # resolves at home
                    for dst, mods in py.items():
                        if dst == src:
                            continue
                        for c in cands:
                            if c in mods:
                                hit = (dst, mods[c], None if c.endswith(f".{name}") and c != mod else name)
                                break
                        if hit:
                            break
                else:
                    for dst, (pkg, paths) in js.items():
                        if dst != src and (mod == pkg or mod.startswith(pkg + "/")):
                            sub = mod[len(pkg):].lstrip("/")
                            if sub in paths:
                                hit = (dst, paths[sub], name)
                            break
                if hit and (key := (src, f, *hit)) not in seen:
                    seen.add(key)
                    out.append({"src_repo": src, "src_file": f, "dst_repo": hit[0], "dst_file": hit[1], "name": hit[2]})
    return out


if __name__ == "__main__":   # self-check: a service importing a shared Python package and a JS library
    lib = {"files": {"shared/__init__.py": {"lang": "python"}, "shared/retry.py": {"lang": "python"},
                     "src/index.ts": {"lang": "ts"}, "src/fmt/date.ts": {"lang": "ts"}}}
    app = {"files": {"svc/main.py": {"lang": "python", "imports": [
        {"module": "shared.retry", "name": "with_backoff"}, {"module": "svc.util", "name": "x"}, {"module": "os", "name": None}]},
        "svc/util.py": {"lang": "python"},
        "web/a.ts": {"lang": "ts", "imports": [{"module": "@acme/lib/fmt/date", "name": "fmt"}, {"module": "@acme/lib", "name": None}]}}}
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        (Path(t) / "package.json").write_text('{"name": "@acme/lib", "main": "src/index.ts"}')
        got = cross_links({"lib": (Path(t), lib), "app": (Path(t) / "none", app)})
    pairs = {(g["src_file"], g["dst_file"], g["name"]) for g in got}
    assert ("svc/main.py", "shared/retry.py", "with_backoff") in pairs, got
    assert ("web/a.ts", "src/fmt/date.ts", "fmt") in pairs and ("web/a.ts", "src/index.ts", None) in pairs, got
    assert not any(g["src_repo"] == "lib" for g in got) and len(got) == 3, got
    print("ok")
