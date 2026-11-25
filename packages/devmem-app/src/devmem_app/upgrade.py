"""Upgrade a library: everything an agent needs to move a repo from the version it uses to a newer release, in one prompt.

devmem still never edits code. It gathers, from where they are written:
  * the exact dependency line(s) to change and the lockfile command to run,
  * what changed between the two versions (release notes or changelog, read from the project's git repository),
  * the project's own migration guides (from the docs fetched out of its repository / docs site),
  * what people wrote about this upgrade elsewhere (a web search for migration guides and breaking-change write-ups),
  * where this repo uses the library (files, imported names, call sites) and which of those a breaking change names,
  * the tests that reach that code,
and writes a prompt in the fixed shape of prompts.py to `.devmem/upgrades/<lib>.md` (data in `<lib>.json`). Agents print it
offline with `devmem upgrade <lib>`; the notes and search excerpts are also stored as a page of the library's docs, so
`devmem docs "..." --lib <lib>` finds them after the next publish."""
from __future__ import annotations

import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from devmem.index import Index
from devmem.schema import atomic_write

from . import docs as docs_mod
from . import prompts

DIR = ".devmem/upgrades"
BREAK = re.compile(r"\b(breaking|backwards?[- ]incompatib\w*|incompatib\w+|remov(e|ed|es|al)|drop(ped|s)?\b|no longer|renam(e|ed|es)|"
                   r"deprecat\w+|replaced (by|with)|instead of|must now|now requires?|requires? (python|node)|changed? (the )?default|"
                   r"default (is|has changed|changed)|migrat\w+|moved to|is now (a|an|required|keyword)|raises? \w+ instead)\b", re.I)
STRONG = re.compile(r"\b(breaking|backwards?[- ]incompatib\w*|incompatib\w+|removed|no longer|renamed|dropped|must now|instead of|replaced (by|with)|"
                    r"is now required|raises? \w+ instead)\b", re.I)
GONE = re.compile(r"\b(has|have|was|were|is|are|been) (now )?(been )?(removed|dropped)|no longer (exists?|supported|available|accepted)|"
                  r"^removed?\b|\bremoved (the|support)", re.I)
SECURITY = re.compile(r"\b(CVE-\d{4}-\d+|GHSA-[\w-]+|security|vulnerab\w+)\b", re.I)
NOISE = re.compile(r"^(full changelog|what's changed|new contributors|contributors|@\w|bump |update[sd]? dep|chore|ci\b|docs?\b|typo|\*\*full changelog)", re.I)
IDENT = re.compile(r"`([^`]{2,80})`|\b([A-Za-z_][\w]*(?:\.[A-Za-z_]\w*)+)\b|\b([a-z]+_[a-z_]+|[A-Z][a-z]+[A-Z]\w*)\b")
TEST_FILE = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]+$|_test\.\w+$|\.(test|spec)\.\w+$")


def _norm(n: str) -> str:
    return re.sub(r"[-_.]+", "-", n).lower()


def _vtxt(s: str | None) -> str:
    m = re.search(r"\d+(?:\.\d+)*", s or "")
    return m.group(0) if m else ""


def level(frm: str, to: str) -> str:
    """'major' | 'minor' | 'patch' | 'none'. A 0.x minor bump counts as major: semver allows breaking changes there."""
    a, b = docs_mod._ver(frm or ""), docs_mod._ver(to or "")
    if not a or not b or b <= a:
        return "none"
    if a[0] != b[0] or (a[0] == 0 and a[1] != b[1]):
        return "major"
    return "minor" if a[1] != b[1] else "patch"


# ---------- where the dependency is declared, and how to bump it ----------
def declared(root: Path, eco: str, name: str) -> list[dict]:
    """[{file, line, text}] for every line that declares the package: requirements*.txt, pyproject.toml (also Poetry tables),
    setup.cfg / setup.py, package.json."""
    root, out = Path(root), []
    n = re.escape(name).replace(r"\-", "[-_.]").replace(r"\_", "[-_.]").replace(r"\.", "[-_.]")
    if eco == "pypi":
        files = [*root.glob("requirements*.txt"), *root.glob("*/requirements*.txt"), *root.glob("requirements/*.txt"), root / "pyproject.toml",
                 *root.glob("packages/*/pyproject.toml"), root / "setup.cfg", root / "setup.py", root / "Pipfile"]
        rx = re.compile(rf"""(^|["'\s])({n})\s*(\[[^\]]*\])?\s*(?=[<>=!~;"',\s\]]|$|=\s*["'{{])""", re.I)
    else:
        files = [root / "package.json", *root.glob("*/package.json"), *root.glob("packages/*/package.json"), *root.glob("apps/*/package.json")]
        rx = re.compile(rf'^\s*"({re.escape(name)})"\s*:', re.I)
    for f in files:
        if not f.is_file() or "node_modules" in f.parts:
            continue
        for i, ln in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if ln.lstrip().startswith("#") or not rx.search(ln):
                continue
            if eco == "pypi" and f.name in ("pyproject.toml", "setup.cfg", "setup.py", "Pipfile") and not re.search(r"[\"'=<>~]|^\s*[\w.-]+\s*=", ln):
                continue
            out.append({"file": f.relative_to(root).as_posix(), "line": i, "text": ln.strip()[:200]})
    return out


def new_spec(spec: str, to: str) -> str:
    """The same kind of constraint, moved to `to`: ==1.2 -> ==to, ^1.2 -> ^to, ~1.2 -> ~to, >=1.2,<2 -> >=to,<major+1."""
    spec = (spec or "").replace(" ", "")
    major = docs_mod._ver(to)[0] if docs_mod._ver(to) else None
    if not spec or spec in ("*", "latest"):
        return f">={to}" if spec == "" else spec
    if spec.startswith("=="):
        return f"=={to}"
    if spec[0] in "^~" and not spec.startswith("~="):
        return f"{spec[0]}{to}"
    if spec.startswith("~="):
        return f"~={to}"
    parts = spec.split(",")
    out = []
    for p in parts:
        if p.startswith(">="):
            out.append(f">={to}")
        elif p.startswith("<") and major is not None and docs_mod._ver(p) and docs_mod._ver(p) <= docs_mod._ver(to):
            out.append(f"<{major + 1}")
        elif p.startswith(">") and not p.startswith(">="):
            out.append(f">={to}")
        else:
            out.append(p)
    return ",".join(out) if out else f">={to}"


def lock_command(root: Path, eco: str, name: str, to: str) -> str | None:
    root = Path(root)
    if eco == "pypi":
        if (root / "uv.lock").exists():
            return f"uv lock --upgrade-package {name}=={to} && uv sync"
        if (root / "poetry.lock").exists():
            return f"poetry add {name}@^{to}" if not (root / "requirements.txt").exists() else f"poetry update {name}"
        if (root / "Pipfile.lock").exists():
            return f"pipenv install {name}=={to}"
        if (root / "pdm.lock").exists():
            return f"pdm update {name}"
        return f"pip install -U \"{name}=={to}\""
    if (root / "pnpm-lock.yaml").exists():
        return f"pnpm up {name}@{to}"
    if (root / "yarn.lock").exists():
        return f"yarn up {name}@{to}" if (root / ".yarnrc.yml").exists() else f"yarn upgrade {name}@{to}"
    if (root / "bun.lockb").exists() or (root / "bun.lock").exists():
        return f"bun add {name}@{to}"
    return f"npm install {name}@{to}"


# ---------- where the repo uses the library ----------
def _import_tops(eco: str, name: str) -> set[str]:
    """Import names a package goes by: requests -> requests; pyyaml -> yaml; python-dateutil -> dateutil; @scope/pkg -> @scope/pkg."""
    if eco == "npm":
        return {name.lower()}
    from .modernize import IMPORT_ALIAS
    n = _norm(name)
    tops = {n.replace("-", "_"), n.replace("-", ""), n}
    tops |= {imp for imp, pkg in IMPORT_ALIAS.items() if _norm(pkg) == n}
    for pre in ("python-", "py-", "django-", "flask-"):
        if n.startswith(pre) and pre in ("python-", "py-"):
            tops.add(n[len(pre):].replace("-", "_"))
    return tops


def usage(ix: Index, eco: str, name: str, cap: int = 400) -> dict:
    """{"files": [{file, names, aliases, test}], "names": {identifier}, "sites": [{file, line, text, names}]}: every file that
    imports the package, what it imports, and the lines that use those names (tests included, flagged)."""
    tops = _import_tops(eco, name)
    files, names, sites, modules = [], set(), [], set(tops)
    for path, rec in ix.data["files"].items():
        aliases: dict[str, str] = {}
        for i in rec.get("imports", []):
            mod = (i.get("module") or "")
            if rec.get("lang") in ("js", "ts"):
                if mod.startswith("."):
                    continue
                top = "/".join(mod.split("/")[:2]) if mod.startswith("@") else mod.split("/")[0]
            else:
                top = mod.split(".")[0]
            if top.lower() not in tops and _norm(top) not in tops:
                continue
            alias = i.get("alias") or i.get("name") or top
            if alias in ("*", "default") or not re.fullmatch(r"[A-Za-z_$][\w$]*", alias or ""):
                alias = top.split("/")[-1] if re.fullmatch(r"[A-Za-z_$][\w$]*", top.split("/")[-1]) else ""
            if not alias:
                continue
            real = i.get("name") if i.get("name") not in (None, "default", "*") else ""
            aliases[alias] = real or ""
            if not real:
                modules.add(alias)          # `import httpx` / `import * as z from "zod"`: the module itself, not an API
            if real:
                names.add(real)
            sub = mod.split(".")[1:] if rec.get("lang") not in ("js", "ts") else []
            names.update(x for x in sub if x)
        if not aliases:
            continue
        test = bool(TEST_FILE.search(path))
        files.append({"file": path, "aliases": sorted(aliases), "names": sorted(v or k for k, v in aliases.items()), "test": test})
        try:
            text = (Path(ix.root) / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        pat = re.compile(r"(?<![\w$.])(" + "|".join(re.escape(a) for a in sorted(aliases, key=len, reverse=True)) + r")\b(\.[A-Za-z_$][\w$]*)*")
        for n_, ln in enumerate(text.splitlines(), 1):
            s = ln.strip()
            if not s or s.startswith(("import ", "from ", "#", "//", "*")) or re.match(r"^(export\s+)?(const|let|var)\s+\{?.*=\s*require\(", s):
                continue
            used = set()
            for m in pat.finditer(ln):
                a = m.group(1)
                used.add(aliases.get(a) or a)
                used.update(x for x in m.group(0).split(".")[1:] if x)
            if used:
                names |= used
                if len(sites) < cap:
                    sites.append({"file": path, "line": n_, "text": s[:160], "names": sorted(used), "test": test})
    return {"files": files, "names": names, "sites": sites, "modules": modules}


# ---------- what changed ----------
def _clean(line: str) -> str:
    t = re.sub(r"^[\s*+\-#>]+|\s+$", "", line)
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"\s*(\(#\d+\)|#\d+|by @[\w-]+( in \S+)?|\(@[\w-]+\))", "", t)
    return " ".join(t.split())


def idents(text: str) -> set[str]:
    """Code names a change note mentions: `backticked`, dotted.names, snake_case and CamelCase words."""
    out = set()
    for m in IDENT.finditer(text):
        tok = m.group(1) or m.group(2) or m.group(3)
        for part in re.split(r"[^\w$.]+", tok):
            part = part.strip(".")
            if re.search(r"\.(com|org|net|io|dev|html?|md|rst|txt|py|js|ts|json|toml|ya?ml|cfg)$", part, re.I):
                continue          # a domain or a file name, not code
            if len(part) > 2 and not part.isdigit():
                out.add(part)
                out.update(p for p in part.split(".") if len(p) > 2)
    return out


def classify(notes: list[tuple[str, str]], guides: list[tuple[str, str]] | None = None) -> dict:
    """Release notes (and migration guides) -> {"breaking": [...], "deprecated": [...], "security": [...], "added": [...]}, each
    item {version, text, idents, source}. Section headings ("### Breaking changes") mark the bullets under them."""
    from .modernize import ADDED
    out = {"breaking": [], "deprecated": [], "security": [], "added": []}
    seen = set()
    for src, items in (("release notes", notes), ("migration guide", guides or [])):
        for ver, body in items:
            section, sec_indent = "", -1      # a heading labels everything up to the next heading; a bold label only the bullets nested under it
            for raw in (body or "").splitlines():
                indent = len(raw) - len(raw.lstrip())
                if re.match(r"^\s{0,3}#{1,6}\s", raw):
                    section, sec_indent = raw.lower(), -1
                    continue
                if re.match(r"^\s*\*\*[^*]+\*\*:?\s*$", raw) or re.match(r"^\s*[A-Z][\w ]{2,40}:\s*$", raw):
                    section, sec_indent = raw.lower(), indent
                    continue
                if sec_indent >= 0 and raw.strip() and indent <= sec_indent:
                    section, sec_indent = "", -1
                t = _clean(raw)
                if len(t) < 12 or len(t) > 400 or NOISE.search(t) or t.lower() in seen:
                    continue
                low = t.lower()
                kind = ("security" if SECURITY.search(t) else
                        "deprecated" if "deprecat" in low and not GONE.search(low) else   # "deprecated ... will be removed in the future"
                        "breaking" if STRONG.search(t) else          # "the deprecated `proxies` argument has been removed"
                        "deprecated" if "deprecat" in low else
                        "breaking" if BREAK.search(t) or re.search(r"break|removed|incompatib|migration", section) else
                        "added" if ADDED.search(t) or re.search(r"feature|added|new", section) else "")
                if not kind or (src == "migration guide" and kind == "added"):
                    continue
                if src == "migration guide" and not re.match(r"^\s*([-*+]|\d+\.)\s", raw) and not idents(t):
                    continue          # guides are prose: keep bullet points and sentences that name code
                seen.add(low)
                strong = bool(STRONG.search(t) or re.search(r"break|removed|incompatib", section))
                out[kind].append({"version": ver, "text": t[:300], "idents": sorted(idents(t)), "source": src, "strong": strong})
    for kind in ("breaking", "deprecated"):     # explicit removals and renames first, then the releases that cross a major version
        out[kind].sort(key=lambda c: (not c["strong"], c["source"] != "migration guide", not re.fullmatch(r"v?\d+\.0(\.0)?", c["version"] or "")))
    return out


def affected(changes: dict, use: dict, cap: int = 30) -> list[dict]:
    """Breaking and deprecated changes that name something this repo uses, with the lines that use it."""
    used = {n for n in use["names"] if len(n) > 2}
    low = {n.lower(): n for n in used}
    out = []
    for kind in ("breaking", "deprecated", "security"):
        for c in changes[kind]:
            hit = {low[i.lower()] for i in c["idents"] if i.lower() in low} | {low[p.lower()] for i in c["idents"] for p in i.split(".") if p.lower() in low}
            hit -= GENERIC | use.get("modules", set())
            if not hit:
                continue
            sites = [s for s in use["sites"] if hit & set(s["names"])]
            out.append({**c, "kind": kind, "hits": sorted(hit), "sites": sites[:12], "site_count": len(sites)})
    out.sort(key=lambda c: (c["kind"] not in ("breaking", "security"), not c.get("strong"), -c["site_count"]))
    return out[:cap]


GENERIC = {"self", "this", "None", "True", "False", "default", "value", "name", "type", "data", "config", "options", "main", "master", "args",
           "kwargs", "test", "tests", "docs", "error", "errors", "python", "node", "string", "number", "object", "true", "false", "null", "undefined"}


def _neighbours(ix: Index, files: set[str]) -> set[str]:
    """Files that import one of `files` (one hop): where a model or client defined on top of the library is used."""
    mods = {}
    for f in files:
        p = re.sub(r"\.(py|pyi)$", "", f).replace("/", ".")
        mods[p] = f
        if p.endswith(".__init__"):
            mods[p[: -len(".__init__")]] = f
    stems = {re.sub(r"\.(ts|tsx|js|jsx|mjs|cjs)$", "", f) for f in files}
    out = set()
    for path, rec in ix.data["files"].items():
        if path in files:
            continue
        for i in rec.get("imports", []):
            mod = i.get("module") or ""
            if rec.get("lang") in ("js", "ts") and mod.startswith("."):
                target = str(Path(path).parent / mod)
                target = re.sub(r"(^|/)\./", r"\1", target)
                while "/../" in target or target.startswith("../"):
                    target = re.sub(r"[^/]+/\.\./", "", target, 1) if "/../" in target else target[3:]
                if re.sub(r"\.(ts|tsx|js|jsx|mjs|cjs)$", "", target) in stems or f"{target}/index" in stems:
                    out.add(path)
            elif mod in mods or any(mod.startswith(m + ".") for m in mods) or (i.get("name") and f"{mod}.{i['name']}" in mods):
                out.add(path)
    return out


def note_sites(ix: Index, use: dict, changes: dict, cap: int = 400) -> int:
    """Second pass: the names that breaking / deprecated notes mention (`parse_obj`, `orm_mode`, `.dict()`), looked up as attribute
    access, calls and keyword or config assignments in the files that use the library and the files that import those. Adds the
    hits to `use` (so `affected` can match them); returns how many lines were added."""
    names = set()
    for kind in ("breaking", "deprecated"):
        for c in changes[kind]:
            names |= {i.split(".")[-1] for i in c["idents"] if re.fullmatch(r"[A-Za-z_$][\w$]{2,60}", i.split(".")[-1])}
    names -= GENERIC
    if not names:
        return 0
    base = {f["file"] for f in use["files"]}
    files = sorted(base | _neighbours(ix, base))
    rx = re.compile(r"(?:\.\s*(" + "|".join(map(re.escape, sorted(names))) + r")\b|(?<![\w$.])(" + "|".join(map(re.escape, sorted(names))) +
                    r")\s*(?:=(?!=)|:(?!:)|\())")
    have = {(s["file"], s["line"]) for s in use["sites"]}
    added = 0
    for path in files:
        try:
            text = (Path(ix.root) / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        test = bool(TEST_FILE.search(path))
        for n_, ln in enumerate(text.splitlines(), 1):
            st = ln.strip()
            if not st or st.startswith(("#", "//", "import ", "from ")) or re.match(r"^(async\s+)?(def|function|class)\s", st):
                continue
            code = re.sub(r"(?:[rbfuRBFU]{0,2})(\"[^\"\\n]*\"|'[^'\\n]*'|`[^`\\n]*`)", '""', ln)   # not inside string literals
            hit = {m.group(1) or m.group(2) for m in rx.finditer(code)}
            if not hit:
                continue
            use["names"] |= hit
            if (path, n_) in have:
                for s in use["sites"]:
                    if (s["file"], s["line"]) == (path, n_):
                        s["names"] = sorted(set(s["names"]) | hit)
                continue
            if len(use["sites"]) < cap:
                use["sites"].append({"file": path, "line": n_, "text": st[:160], "names": sorted(hit), "test": test, "via": "notes"})
                have.add((path, n_))
                added += 1
                if path not in base:
                    use["files"].append({"file": path, "aliases": [], "names": sorted(hit), "test": test, "indirect": True})
                    base.add(path)
                else:
                    for f in use["files"]:
                        if f["file"] == path:
                            f["names"] = sorted(set(f["names"]) | hit)
    return added


# ---------- guides and research ----------
def guides_on_file(lib_dir: Path, frm: str, to: str) -> list[dict]:
    """Migration / upgrade pages among the docs on file for a library (fetched from its repository or docs site)."""
    from .sources import GUIDE_NAME
    man_p = lib_dir / "manifest.json"
    if not man_p.exists():
        return []
    man = json.loads(man_p.read_text(encoding="utf-8"))
    out = []
    for pg in man.get("pages", []):
        if pg["file"].startswith("upgrade-"):
            continue
        if GUIDE_NAME.search(pg.get("title", "")) or GUIDE_NAME.search(pg.get("url", "").rsplit("/", 1)[-1]):
            try:
                text = (lib_dir / pg["file"]).read_text(encoding="utf-8")
            except OSError:
                continue
            body = re.sub(r"^<!-- source: .*? -->\n", "", text)
            out.append({"title": pg["title"], "url": pg["url"], "text": body, "file": pg["file"]})
    major = docs_mod._ver(to)[0] if docs_mod._ver(to) else None
    if major is not None:    # "migrating to v5" beats "migrating to v3" when going to 5.x
        out.sort(key=lambda g: (not re.search(rf"\bv?{major}\b", g["title"] + " " + g["url"]), len(g["text"])))
    return out[:4]


def _mm(v: str) -> str:
    t = docs_mod._ver(v)
    return f"{t[0]}.{t[1]}" if t else v


def research(name: str, frm: str, to: str, prefer_hosts: list[str], max_pages: int = 4) -> dict:
    """Search the web for this upgrade and keep the passages of the best pages that talk about breaking changes or migration."""
    from . import websearch
    major_to = docs_mod._ver(to)[0] if docs_mod._ver(to) else ""
    queries = [f"{name} {frm and f'{_mm(frm)} to '}{_mm(to)} migration guide", f"{name} {major_to and f'v{major_to} '}breaking changes upgrade"]
    res = {"queries": queries, "results": [], "pages": [], "error": ""}
    seen = set()
    try:
        for q in queries:
            for r in websearch.search(q, 6):
                u = r["url"].split("#")[0]
                if u not in seen:
                    seen.add(u)
                    res["results"].append(r)
    except Exception as e:
        res["error"] = str(e)[:300]
    if not res["results"]:
        return res
    nm = re.escape(name.split("/")[-1])

    def score(r):
        host = re.sub(r"^www\.", "", re.sub(r"^https?://", "", r["url"]).split("/")[0])
        official = any(h and (host == h or host.endswith("." + h)) for h in prefer_hosts)
        txt = f"{r['title']} {r['snippet']} {r['url']}"
        return (not official, not re.search(r"migrat|upgrad|breaking|what.?s new|changelog|release", txt, re.I), not re.search(nm, txt, re.I))
    ranked = sorted(res["results"], key=score)

    def fetch(r):
        try:
            text = docs_mod.get(r["url"], 20)
        except Exception:
            return None
        title, md = docs_mod._page(r["url"], text) if docs_mod.looks_html(text) else (r["title"], text)
        keep = []
        paras = []
        for para in re.split(r"\n\s*\n", md):      # pages converted from HTML often separate paragraphs by one newline
            paras += [para] if len(para) <= 600 or "```" in para else para.split("\n")
        for para in paras:
            p = para.strip()
            if 40 <= len(p) <= 4000 and (BREAK.search(p) or "```" in p or re.search(r"\bv?\d+\.\d+", p)) and re.search(nm, md[:20000], re.I):
                keep.append(p)
        body = "\n\n".join(keep)[:12_000]
        name_ = r["title"] or (title if title and not title.startswith("http") else r["url"])   # the result's title; a page without <title> falls back to its URL
        return {"title": " ".join(name_.split())[:120], "url": r["url"], "text": body} if len(body) > 80 else None
    with ThreadPoolExecutor(4) as ex:
        res["pages"] = [p for p in ex.map(fetch, ranked[: max_pages + 2]) if p][:max_pages]
    return res


def _ai_steps(provider, name: str, frm: str, to: str, aff: list[dict], changes: dict, use: dict, guides: list[dict]) -> list[str]:
    """Ask the model for the concrete edits this repo needs, from the change notes and its own call sites only."""
    lines = [f"A repository uses {name} {frm or '(unpinned)'} and is upgrading to {to}.",
             "From the change notes and the repository's call sites below, list the concrete code changes this repository needs, "
             "most important first. Each item: one sentence naming the file(s) and the old -> new API. Only changes the notes support; "
             "say nothing about other code. Reply with ONLY a JSON list (at most 10 items) like "
             '[{"step": "app/db.py: replace Session.query(...) with select(...)"}].', "", "CHANGES THAT NAME CODE THIS REPO USES"]
    lines += [f"- [{c['kind']}] {c['version']}: {c['text']}" for c in aff[:20]] or ["(none matched by name)"]
    lines += ["", "OTHER BREAKING CHANGES"] + [f"- {c['version']}: {c['text']}" for c in changes["breaking"][:25]]
    if guides:
        lines += ["", "MIGRATION GUIDE (excerpt)", guides[0]["text"][:6000]]
    lines += ["", "CALL SITES IN THIS REPOSITORY"] + [f"{s['file']}:{s['line']}: {s['text']}" for s in use["sites"][:60]]
    from .chat import ask_json_list
    items = [x.get("step") or next(iter(x.values()), "") for x in ask_json_list(provider, "\n".join(lines))]
    return [" ".join(str(x).split())[:300] for x in items if str(x).strip()][:10]


# ---------- the plan ----------
def _lib_dir(eco: str, name: str) -> Path | None:
    for l in docs_mod.list_libs():
        src = str(l.get("source") or "")
        if _norm(l["name"]) in (_norm(name), docs_mod._slug(name.lstrip("@"))) or src in (f"{eco}:{name}",) or src.startswith(f"{eco}:{name}=="):
            return docs_mod.docs_dir() / l["name"]
    return None


def _store_notes_page(lib_dir: Path, name: str, frm: str, to: str, notes: dict, guides: list[dict], web: dict, aff: list[dict]) -> str | None:
    """Release notes + web excerpts as one page of the library's docs (`upgrade-<from>-<to>.md`), replacing older upgrade pages."""
    parts = [f"# {name} upgrade notes {frm or '?'} -> {to}", "",
             "What changed between the version this repo uses and the target, gathered from the project's repository and the web. "
             "Read this before using newer APIs or fixing code the upgrade broke.", ""]
    if aff:
        parts += ["## Changes that touch code in this repo", *(f"- [{c['kind']}] {c['version']}: {c['text']} (used here: {', '.join(c['hits'])})" for c in aff), ""]
    if notes["notes"]:
        parts += [f"## Release notes ({notes['source']}: {notes['url']})", *(f"### {v}\n\n{b}\n" for v, b in notes["notes"])]
    for g in guides[:2]:
        parts += [f"## Migration guide: {g['title']} ({g['url']})", g["text"][:20_000], ""]
    for p in web.get("pages", []):
        parts += [f"## From the web: {p['title']} ({p['url']})", p["text"], ""]
    if len(parts) <= 4:
        return None
    body = "\n".join(parts)
    man_p = lib_dir / "manifest.json"
    man = json.loads(man_p.read_text(encoding="utf-8"))
    fn = f"upgrade-{docs_mod._slug(frm or 'unpinned')}-{docs_mod._slug(to)}.md"
    for old in [p["file"] for p in man.get("pages", []) if p["file"].startswith("upgrade-")]:
        (lib_dir / old).unlink(missing_ok=True)
    (lib_dir / fn).write_text(f"<!-- source: {notes.get('url') or 'devmem upgrade'} -->\n{body[:docs_mod.MAX_PAGE * 2]}\n", encoding="utf-8")
    man["pages"] = [p for p in man.get("pages", []) if not p["file"].startswith("upgrade-")] + \
                   [{"file": fn, "url": notes.get("url") or "", "title": f"{name} upgrade notes {frm or '?'} -> {to}", "sha1": hashlib.sha1(body.encode()).hexdigest()[:12]}]
    man_p.write_text(json.dumps(man, indent=1), encoding="utf-8")
    return fn


def plan(root: Path, lib: str, to: str | None = None, search: bool = True, provider=None, eco: str | None = None,
         progress=None, ix: Index | None = None) -> dict:
    """Build (and save) the upgrade plan and prompt for one dependency of a repo. Network: registry, git, web search."""
    from . import sources
    say = progress or (lambda *_: None)
    root = Path(root)
    ix = ix or Index.load(root)
    deps = docs_mod.scan_deps(root)
    row = next((d for d in deps if _norm(d["name"]) == _norm(lib) and (eco is None or d["eco"] == eco)), None)
    if not row:
        raise LookupError(f"{lib} is not a declared dependency of this repo (requirements, pyproject.toml or package.json)")
    eco, name = row["eco"], row["name"]
    locked = docs_mod.locked_versions(root).get((eco, name))
    frm = _vtxt(locked or row["spec"])
    say("asking the registry for the latest release and where the project lives")
    meta = sources.package_meta(eco, name)
    target = to or meta["version"]
    if frm and docs_mod._ver(frm) >= docs_mod._ver(target):
        raise ValueError(f"{name} is already at {frm}; the newest release is {meta['version']}")
    lib_dir = _lib_dir(eco, name)
    docs_note = ""
    try:          # docs for the target release, read from the project's repository / docs site
        man = json.loads((lib_dir / "manifest.json").read_text(encoding="utf-8")) if lib_dir else None
        if not man or (man.get("kind") in ("pypi", "npm") and docs_mod._ver(man.get("version", "")) < docs_mod._ver(target)):
            say(f"fetching the {target} docs from the project's repository")
            m = docs_mod.add(f"{eco}:{name}{('==' if eco == 'pypi' else '@') + target if to else ''}", None, True, 40)
            lib_dir, docs_note = docs_mod.docs_dir() / m["name"], f"fetched {m['name']} {m['version']} docs ({len(m['pages'])} pages)"
    except Exception as e:
        docs_note = f"docs could not be fetched: {str(e)[:160]}"
    say("reading the release notes between the two versions")
    lo = frm or (f"{docs_mod._ver(target)[0]}.0.0" if docs_mod._ver(target) else "0")
    notes = sources.notes_between(meta["repo"], lo, target, name)
    guides = guides_on_file(lib_dir, frm, target) if lib_dir else []
    web = {"queries": [], "results": [], "pages": [], "error": "web search not requested"}
    if search:
        say("searching the web for migration guides")
        hosts = [h for h in (re.sub(r"^https?://(www\.)?", "", meta.get("docs_url") or "").split("/")[0], meta["repo"]["host"] if meta["repo"] else "") if h]
        web = research(name, frm, target, hosts)
    say("finding where this repo uses the library")
    use = usage(ix, eco, name)
    changes = classify(notes["notes"], [("migration guide", g["text"]) for g in guides]
                       + [("web: " + re.sub(r"^https?://(www\.)?", "", p["url"]).split("/")[0], p["text"]) for p in web.get("pages", [])])
    note_sites(ix, use, changes)
    aff = affected(changes, use)
    ai_steps, ai_error = [], ""
    if provider is not None:
        say("asking the model for the edits this repo needs")
        try:
            ai_steps = _ai_steps(provider, name, frm, target, aff, changes, use, guides)
        except Exception as e:
            ai_error = str(e)[:200]
    notes_page = None
    if lib_dir and (lib_dir / "manifest.json").exists():
        notes_page = _store_notes_page(lib_dir, name, frm, target, notes, guides, web, aff)
        if (root / ".devmem/docs").is_dir():
            docs_mod.copy_relevant(root)
    decl = declared(root, eco, name)
    prod_files = sorted({f["file"] for f in use["files"] if not f["test"]})
    data = {"lib": name, "eco": eco, "from": frm, "locked": locked or "", "spec": row["spec"], "to": target, "latest": meta["version"],
            "level": level(frm, target) if frm else "unknown", "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "repo_url": meta["repo"]["web"] if meta["repo"] else "",
            "docs_url": meta.get("docs_url") or "", "requires_python": meta.get("requires_python", ""), "engines": meta.get("engines") or {},
            "declared": decl, "new_spec": new_spec(row["spec"], target), "lock_command": lock_command(root, eco, name, target),
            "notes": {"source": notes["source"], "url": notes["url"], "error": notes["error"], "versions": [v for v, _ in notes["notes"]]},
            "changes": {k: v[:40] for k, v in changes.items()}, "affected": aff, "guides": [{k: g[k] for k in ("title", "url", "file")} for g in guides],
            "web": {"queries": web["queries"], "results": web["results"][:10], "pages": [{"title": p["title"], "url": p["url"]} for p in web["pages"]], "error": web["error"]},
            "usage": {"files": use["files"], "sites": len(use["sites"]), "names": sorted(use["names"])[:80]}, "prod_files": prod_files,
            "tests": prompts._test_cmd(ix, prod_files) if prod_files else None, "ai_steps": ai_steps, "ai_error": ai_error,
            "docs_note": docs_note, "notes_page": notes_page}
    text = render(ix, data, use)
    data["prompt"], data["tokens"] = text, prompts.est(text)
    d = root / DIR
    d.mkdir(parents=True, exist_ok=True)
    slug = docs_mod._slug(name.lstrip("@"))
    atomic_write(d / f"{slug}.md", text)
    atomic_write(d / f"{slug}.json", json.dumps({k: v for k, v in data.items() if k != "prompt"}, indent=1))
    return data


def render(ix: Index, d: dict, use: dict) -> str:
    """The prompt, in the fixed shape every devmem prompt has (prompts.render)."""
    name, frm, to = d["lib"], d["from"] or "the version in use", d["to"]
    task = (f"Upgrade {name} from {frm} to {to} ({d['level']} upgrade) and update the code that uses it, so the repo builds and its tests pass "
            "with the same behaviour.")
    where = ["- Dependency declared at:" if d["declared"] else "- Dependency: not found in a dependency file; search the repo for it."]
    where += [f"  - `{x['file']}` line {x['line']}: `{x['text']}`" for x in d["declared"][:6]]
    if d["spec"]:
        where.append(f"  - Change the constraint `{d['spec']}` to `{d['new_spec']}`.")
    if d["lock_command"]:
        where.append(f"  - Then update the lockfile / install: `{d['lock_command']}`")
    if d.get("requires_python"):
        where.append(f"  - {name} {to} requires Python {d['requires_python']}: check the repo's Python version first.")
    if (d.get("engines") or {}).get("node"):
        where.append(f"  - {name} {to} requires Node.js {d['engines']['node']}: check the repo's Node version first.")
    prod = [f for f in use["files"] if not f["test"]]
    where.append(f"- Code that uses {name}: {len(prod)} file(s), {len([s for s in use['sites'] if not s['test']])} line(s) outside tests"
                 + (f"; {len(use['files']) - len(prod)} test file(s)." if len(use["files"]) > len(prod) else "."))
    for f in prod[:15]:
        lines = [s["line"] for s in use["sites"] if s["file"] == f["file"]]
        where.append(f"  - `{f['file']}` uses {', '.join(f['names'][:8])}" + (f" at lines {', '.join(map(str, lines[:12]))}" if lines else ""))
    if len(prod) > 15:
        where.append(f"  - ... and {len(prod) - 15} more files (find them with `devmem find {name}` or a search for the import)")
    if d["affected"]:
        where.append(f"- Changes between {frm} and {to} that name code this repo uses (fix these first):")
        for n, c in enumerate(d["affected"][:12], 1):
            where.append(f"  {n}. [{c['kind']}, {c['version']}] {c['text']}")
            for s in c["sites"][:4]:
                where.append(f"     - `{s['file']}` line {s['line']}: `{s['text']}`")
            if c["site_count"] > 4:
                where.append(f"     - ... {c['site_count'] - 4} more line(s) use {', '.join(c['hits'])}")
    other = [c for c in d["changes"]["breaking"] if c["text"] not in {a["text"] for a in d["affected"]}]
    if other:
        where.append(f"- Other breaking changes in {name} between these versions (check whether the code relies on any):")
        where += [f"  - {c['version']}: {c['text']}" for c in other[:12]]
    dep = [c for c in d["changes"]["deprecated"] if c["text"] not in {a["text"] for a in d["affected"]}]
    if dep:
        where.append("- Deprecated (still works, warns; replace if the code uses it):")
        where += [f"  - {c['version']}: {c['text']}" for c in dep[:6]]
    if d["changes"]["security"]:
        where.append("- Security fixes in between (a reason to do this upgrade):")
        where += [f"  - {c['version']}: {c['text']}" for c in d["changes"]["security"][:4]]
    refs = [f"  - Release notes: {d['notes']['url']} ({d['notes']['source']}, {len(d['notes']['versions'])} release(s))"] if d["notes"]["url"] else []
    refs += [f"  - Migration guide: {g['title']}: {g['url']}" for g in d["guides"][:3]]
    refs += [f"  - From the web: {p['title']}: {p['url']}" for p in d["web"]["pages"][:3]]
    if refs:
        where += ["- Read when a step is unclear:", *refs]
    if d.get("notes_page"):
        where.append(f"  - Offline copy of all of the above: `devmem docs \"<what you need>\" --lib {docs_mod._slug(name.lstrip('@')) if d['eco'] == 'npm' else _norm(name)}`")
    if d["ai_steps"]:
        where.append("- A model read the notes against this repo's call sites and suggests (verify each against the notes):")
        where += [f"  - {s}" for s in d["ai_steps"]]
    test = d.get("tests")
    run_tests = f"`{test}`" if test else "the repo's test command (see `.devmem/knowledge/commands.md`)"
    steps = [f"Before changing anything, run {run_tests} and note which tests already fail, so you do not chase failures the upgrade did not cause.",
             (f"Edit the dependency line(s) listed above: `{d['spec']}` becomes `{d['new_spec']}`." if d["spec"] else f"Pin {name} to `{to}` in the dependency file.")
             + (f" Then run `{d['lock_command']}`." if d["lock_command"] else ""),
             f"Upgrade only {name}. If the resolver forces other packages to move, allow it, and list each one with its old and new version in your report.",
             ("Fix the numbered changes above, one at a time, at the lines listed under each. Use the release notes or migration guide "
              "for the new API; do not guess it." if d["affected"] else
              "No change note names code this repo uses. Still read the other breaking changes above and check the listed files for them."),
             f"Run {run_tests}. For each new failure: read the error, find the matching change note, fix the calling code. Do not edit a test's expected values to make it pass.",
             "Search the code for any name a breaking or deprecated note says was removed or renamed, and replace what is left.",
             "Do not adopt new features of the release unless a fix needs them.",
             "Run `devmem diffcheck`. An upgrade can legitimately touch many files; if it blocks only on size, say so in your report."]
    rules = prompts.RULES[:1] + prompts.RULES[2:] + [
        f"Change only the dependency on {name}, its lockfile entries and the code that breaks because of it.",
        "If the upgrade also needs a newer Python / Node.js or a change in more than 20 files, stop after step 1 and report what you found instead."]
    done = [f"{name} is at {to} in the dependency file and the lockfile.", "No line still uses a removed or renamed API from the notes.",
            f"The tests pass: {run_tests} (or fail only as they did before the upgrade).", "`devmem diffcheck` passes or blocks only on diff size."]
    report = ["The dependency line(s) and lockfile you changed, and every other package that moved.",
              "Each numbered change: what you edited, or why it did not apply.", *prompts.REPORT[1:]]
    return prompts.render(task, where, steps, rules, done, report)


# ---------- views ----------
def candidates(ix: Index, online: bool = False) -> list[dict]:
    """Dependencies of a repo with a newer release, most urgent first, with how much code uses each and any saved plan."""
    root = Path(ix.root)
    lp = root / ".devmem/libs.json"
    rows = docs_mod.write_libs(root, True) if online or not lp.exists() else json.loads(lp.read_text(encoding="utf-8"))
    out = []
    for r in rows:
        using = _vtxt(r.get("locked") or r.get("spec"))
        lv = level(using, r.get("latest") or "") if using else ("unknown" if r.get("latest") else "none")
        if lv == "none":
            continue
        u = usage(ix, r["eco"], r["name"], cap=60)
        saved = load(root, r["name"])
        out.append({"eco": r["eco"], "name": r["name"], "spec": r.get("spec") or "", "using": using, "latest": r.get("latest"), "level": lv,
                    "files": len([f for f in u["files"] if not f["test"]]), "sites": len(u["sites"]), "docs": bool(r.get("docs")),
                    "plan": {k: saved[k] for k in ("to", "ts", "tokens", "level") if k in saved} | {"affected": len(saved.get("affected", []))} if saved else None})
    order = {"major": 0, "minor": 1, "patch": 2, "unknown": 3}
    out.sort(key=lambda x: (order.get(x["level"], 4), -x["files"], x["name"]))
    return out


def load(root: Path, lib: str) -> dict | None:
    slug = docs_mod._slug(lib.lstrip("@"))
    p = Path(root) / DIR / f"{slug}.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        md = Path(root) / DIR / f"{slug}.md"
        d["prompt"] = md.read_text(encoding="utf-8") if md.exists() else ""
        return d
    except (OSError, ValueError):
        return None
