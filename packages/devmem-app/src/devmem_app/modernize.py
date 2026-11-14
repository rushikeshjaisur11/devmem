"""Modernize: find hand-written code that the language, a library the repo already uses, or a well-known library now provides.

Three sources, none of which needs AI:
  1. a catalog of patterns people commonly write by hand (a retry loop with sleep, a dict used as a cache, chunking a list,
     JSON.parse(JSON.stringify(x)) ...), each matched on the code of one function and tied to what replaces it and since when;
  2. the library docs on file: a function whose name matches a documented feature of a library (the check `devmem reuse` runs);
  3. dependencies behind their latest release, where an upgrade may replace code written around the old version.
Every finding says whether it can be used now: the repo's declared Python / Node version is compared with the version the
replacement needs, and a library replacement says whether the repo already depends on it. An optional LLM pass can
double-check candidates; it never adds any. Results: `.devmem/modernize.json`. Nothing is changed in the code."""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

from devmem import reuse as core_reuse
from devmem.index import Index

from . import docs as docs_mod
from .definitions import TEST_FILE
from .reuse import _entry, _internal_names

PATH = ".devmem/modernize.json"
FETCH_DOCS_CAP, NOTES_CAP = 20, 12     # libraries per scan whose latest docs / release notes are fetched
# import name -> package name, where they differ
IMPORT_ALIAS = {"yaml": "pyyaml", "pil": "pillow", "sklearn": "scikit-learn", "cv2": "opencv-python", "bs4": "beautifulsoup4", "dateutil": "python-dateutil",
                "dotenv": "python-dotenv", "jwt": "pyjwt", "attr": "attrs", "google": "google-cloud", "serial": "pyserial", "magic": "python-magic"}
PY, MDN = "https://docs.python.org/3/library/", "https://developer.mozilla.org/en-US/docs/Web/"
DOC_URL = {"py-retry-loop": "https://tenacity.readthedocs.io/en/latest/", "py-dict-cache": PY + "functools.html#functools.lru_cache", "py-chunk": PY + "itertools.html#itertools.batched",
           "py-pairwise": PY + "itertools.html#itertools.pairwise", "py-flatten": PY + "itertools.html#itertools.chain.from_iterable", "py-removeprefix": PY + "stdtypes.html#str.removeprefix",
           "py-counter": PY + "collections.html#collections.Counter", "py-utcnow": PY + "datetime.html#datetime.datetime.now", "py-random-token": PY + "secrets.html",
           "py-tempdir": PY + "tempfile.html#tempfile.TemporaryDirectory", "py-argv": PY + "argparse.html", "py-walk-glob": PY + "pathlib.html#pathlib.Path.rglob",
           "py-email-regex": "https://github.com/JoshData/python-email-validator#readme", "py-slugify": "https://github.com/un33k/python-slugify#readme",
           "py-env-config": "https://docs.pydantic.dev/latest/concepts/pydantic_settings/", "py-urllib-json": "https://www.python-httpx.org/quickstart/",
           "py-manual-dataclass": PY + "dataclasses.html", "js-json-clone": MDN + "API/Window/structuredClone", "js-uuid": MDN + "API/Crypto/randomUUID",
           "js-fetch-timeout": MDN + "API/AbortSignal/timeout_static", "js-query-string": MDN + "API/URLSearchParams", "js-sleep": "https://nodejs.org/api/timers.html#timers-promises-api",
           "js-group-by": MDN + "JavaScript/Reference/Global_Objects/Object/groupBy", "js-flatten": MDN + "JavaScript/Reference/Global_Objects/Array/flat",
           "js-debounce": "https://lodash.com/docs/#debounce", "js-date-format": MDN + "JavaScript/Reference/Global_Objects/Intl/DateTimeFormat"}
NEWS = re.compile(r"\b(add(ed|s)?|new|support(s|ed)?|introduc\w+|deprecat\w+|remov\w+|breaking|now|built-?in|replac\w+)\b", re.I)
DOCS_SCAN_CAP = 400      # docs matching costs a search per function: the most-called functions first


def _words(name: str) -> set[str]:
    return {w.lower() for w in re.split(r"[^A-Za-z0-9]+", re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)) if w}


# Each rule: id, languages, what was written by hand, what replaces it, where that comes from (stdlib | library), the library
# name for library replacements, the runtime version a stdlib replacement needs, confidence, and a test on (source, name words).
def _rx(*pats: str):
    """Every pattern occurs somewhere in the function. Patterns are single-line (no DOTALL, no nested repetition), so a scan
    of a large repo stays linear; order across lines is checked with _seq."""
    cs = [re.compile(p) for p in pats]
    return lambda src, name: all(c.search(src) for c in cs)


def _seq(*pats: str):
    """The patterns occur in this order, each after the previous match."""
    cs = [re.compile(p, re.M) for p in pats]

    def test(src, name):
        pos = 0
        for c in cs:
            m = c.search(src, pos)
            if not m:
                return False
            pos = m.end()
        return True
    return test


def _named(words: set[str], *pats: str):
    test = _rx(*pats)
    return lambda src, name: bool(words & name) and test(src, name)


RULES: list[dict] = [
    # ---- Python ----
    dict(id="py-retry-loop", langs=("python",), what="a retry loop: try / except inside a loop, with sleep", use="`tenacity.retry` (or `backoff.on_exception`)",
         kind="library", lib="tenacity", alt=("backoff",), conf="high",
         test=_seq(r"^\s*(for|while)\b[^\n]*:\s*$", r"^\s*try\s*:", r"^\s*except\b", r"\bsleep\s*\(")),
    dict(id="py-dict-cache", langs=("python",), what="a dict used as a cache of results", use="`functools.lru_cache` / `functools.cache`", kind="stdlib", since="3.9", conf="medium",
         test=_rx(r"if\s+(\w+)\s+(?:not\s+)?in\s+(_?\w*cache\w*|_?memo\w*)\b", r"(_?\w*cache\w*|_?memo\w*)\[[^\]]+\]\s*=")),
    dict(id="py-chunk", langs=("python",), what="splitting a sequence into chunks with range(0, len(x), n)", use="`itertools.batched`", kind="stdlib", since="3.12", conf="high",
         test=_rx(r"range\(\s*0\s*,\s*len\([^)]*\)\s*,\s*\w+\s*\)", r"\[\s*\w+\s*:\s*\w+\s*\+\s*\w+\s*\]")),
    dict(id="py-pairwise", langs=("python",), what="pairing neighbours with zip(x, x[1:])", use="`itertools.pairwise`", kind="stdlib", since="3.10", conf="high",
         test=_rx(r"zip\(\s*(\w+)\s*,\s*\1\[\s*1\s*:\s*\]\s*\)")),
    dict(id="py-flatten", langs=("python",), what="flattening nested lists by hand", use="`itertools.chain.from_iterable`", kind="stdlib", since="3.0", conf="medium",
         test=_named({"flatten", "flat"}, r"\bfor\s+\w+\s+in\s+\w+|\.extend\(")),
    dict(id="py-removeprefix", langs=("python",), what="stripping a prefix or suffix with startswith / endswith and slicing", use="`str.removeprefix` / `str.removesuffix`", kind="stdlib", since="3.9", conf="high",
         test=_seq(r"\.(startswith|endswith)\(\s*\w+\s*\)", r"\[\s*(len\(\s*\w+\s*\)\s*:|:\s*-\s*len\(\s*\w+\s*\))\s*\]")),
    dict(id="py-counter", langs=("python",), what="counting with d[k] = d.get(k, 0) + 1", use="`collections.Counter`", kind="stdlib", since="3.0", conf="high",
         test=_rx(r"(\w+)\[([^\]]+)\]\s*=\s*\1\.get\(\s*\2\s*,\s*0\s*\)\s*\+\s*1")),
    dict(id="py-utcnow", langs=("python",), what="`datetime.utcnow()` (deprecated; returns a naive time)", use="`datetime.now(datetime.UTC)`", kind="stdlib", since="3.11", conf="high",
         test=_rx(r"\butcnow\s*\(\s*\)")),
    dict(id="py-random-token", langs=("python",), what="building a token or id from random.choice", use="`secrets.token_urlsafe` / `secrets.token_hex`", kind="stdlib", since="3.6", conf="high",
         test=_rx(r"random\.(choice|choices|randint)\(", r"(ascii_letters|ascii_lowercase|hexdigits|digits|join\()"), names={"token", "secret", "password", "key", "id", "uid", "nonce", "salt", "code"}),
    dict(id="py-tempdir", langs=("python",), what="mkdtemp with a manual rmtree cleanup", use="`tempfile.TemporaryDirectory()` as a context manager", kind="stdlib", since="3.2", conf="high",
         test=_rx(r"\bmkdtemp\s*\(", r"\brmtree\s*\(")),
    dict(id="py-argv", langs=("python",), what="reading command-line arguments from sys.argv by position", use="`argparse` (or `click` / `typer`)", kind="stdlib", since="3.2", conf="medium",
         test=_seq(r"sys\.argv\[\s*[1-9]\s*\]", r"sys\.argv\[\s*[1-9]\s*\]")),
    dict(id="py-walk-glob", langs=("python",), what="os.walk with a name filter to find files", use="`pathlib.Path.rglob`", kind="stdlib", since="3.4", conf="medium",
         test=_rx(r"os\.walk\(", r"(fnmatch|\.endswith\()")),
    dict(id="py-email-regex", langs=("python",), what="validating an email address with a regular expression", use="`email-validator` (or pydantic `EmailStr`)", kind="library", lib="email-validator", alt=("pydantic",), conf="high",
         test=_named({"email", "mail"}, r"re\.(match|fullmatch|search|compile)\(", r"@")),
    dict(id="py-slugify", langs=("python",), what="making a URL slug with re.sub", use="`python-slugify`", kind="library", lib="python-slugify", conf="medium",
         test=_named({"slug", "slugify"}, r"re\.sub\(")),
    dict(id="py-env-config", langs=("python",), what="reading many settings from os.environ one by one", use="`pydantic-settings` `BaseSettings`", kind="library", lib="pydantic-settings", alt=("pydantic",), conf="medium",
         test=lambda src, name: len(re.findall(r"os\.(environ\.get|environ\[|getenv)\(?", src)) >= 4),
    dict(id="py-urllib-json", langs=("python",), what="an HTTP call with urllib.request and manual JSON decoding", use="`httpx` / `requests` (`response.json()`)", kind="library", lib="httpx", alt=("requests",), conf="medium", only_if_dep=True,
         test=_rx(r"urllib\.request\.(urlopen|Request)\(", r"json\.loads?\(")),
    dict(id="py-manual-dataclass", langs=("python",), what="a class with a hand-written __init__ and __eq__ / __repr__ over the same fields", use="`@dataclasses.dataclass`", kind="stdlib", since="3.7", conf="medium", on="class",
         test=lambda src, name: len(re.findall(r"self\.(\w+)\s*=\s*\1\b", src)) >= 3 and bool(re.search(r"def __(eq|repr)__", src))),
    # ---- JavaScript / TypeScript ----
    dict(id="js-json-clone", langs=("js", "ts"), what="deep copy with JSON.parse(JSON.stringify(x))", use="`structuredClone(x)`", kind="stdlib", since="17", conf="high",
         test=_rx(r"JSON\.parse\(\s*JSON\.stringify\(")),
    dict(id="js-uuid", langs=("js", "ts"), what="a hand-made random id / UUID from Math.random", use="`crypto.randomUUID()`", kind="stdlib", since="16", conf="high",
         test=_rx(r"Math\.random\(\)", r"(toString\(\s*(16|36)\s*\)|xxxxxxxx-xxxx)")),
    dict(id="js-fetch-timeout", langs=("js", "ts"), what="a fetch timeout built from AbortController and setTimeout", use="`AbortSignal.timeout(ms)`", kind="stdlib", since="18", conf="high",
         test=_rx(r"new\s+AbortController\(", r"setTimeout\(", r"\.abort\(")),
    dict(id="js-query-string", langs=("js", "ts"), what="building a query string with encodeURIComponent and join('&')", use="`URLSearchParams`", kind="stdlib", since="10", conf="high",
         test=_rx(r"encodeURIComponent\(", r"join\(\s*['\"]&['\"]\s*\)")),
    dict(id="js-sleep", langs=("js", "ts"), what="a sleep helper wrapping setTimeout in a Promise", use="`setTimeout` from `node:timers/promises`", kind="stdlib", since="16", conf="medium",
         test=_named({"sleep", "delay", "wait", "pause"}, r"new\s+Promise\(", r"setTimeout\(")),
    dict(id="js-group-by", langs=("js", "ts"), what="grouping items with reduce into an object of arrays", use="`Object.groupBy`", kind="stdlib", since="21", conf="medium",
         test=_rx(r"\.reduce\(\s*\(\s*\w+\s*,", r"\]\s*(\|\|=|\?\?=)\s*\[\s*\]|\]\s*\)?\.push\(")),
    dict(id="js-flatten", langs=("js", "ts"), what="flattening arrays with concat / reduce", use="`Array.prototype.flat()`", kind="stdlib", since="11", conf="high",
         test=_rx(r"(\[\]\.concat\(\s*\.\.\.|\.reduce\(\s*\(\s*\w+\s*,\s*\w+\s*\)\s*=>\s*\w+\.concat\()")),
    dict(id="js-debounce", langs=("js", "ts"), what="a hand-written debounce / throttle", use="`lodash.debounce` / `lodash.throttle`", kind="library", lib="lodash", alt=("lodash-es", "lodash.debounce", "lodash.throttle"), conf="medium",
         test=_named({"debounce", "throttle"}, r"(setTimeout|clearTimeout|Date\.now)\(")),
    dict(id="js-date-format", langs=("js", "ts"), what="formatting a date from getFullYear / getMonth / getDate", use="`Intl.DateTimeFormat` (or `date-fns` `format`)", kind="stdlib", since="12", conf="medium",
         test=_rx(r"getFullYear\(\)", r"getMonth\(\)", r"getDate\(\)")),
]


# the line shown as evidence for each rule: the first line of the function containing one of these
EVIDENCE = {"py-retry-loop": ("sleep(",), "py-dict-cache": ("cache", "memo"), "py-chunk": ("range(0",), "py-pairwise": ("zip(",), "py-flatten": ("for ", ".extend("),
            "py-removeprefix": ("startswith(", "endswith("), "py-counter": (".get(",), "py-utcnow": ("utcnow",), "py-random-token": ("random.",), "py-tempdir": ("mkdtemp",),
            "py-argv": ("sys.argv",), "py-walk-glob": ("os.walk",), "py-email-regex": ("re.",), "py-slugify": ("re.sub",), "py-env-config": ("environ", "getenv"),
            "py-urllib-json": ("urlopen", "Request("), "py-manual-dataclass": ("def __eq__", "def __repr__"), "js-json-clone": ("JSON.parse",), "js-uuid": ("Math.random",),
            "js-fetch-timeout": ("AbortController",), "js-query-string": ("encodeURIComponent",), "js-sleep": ("setTimeout",), "js-group-by": (".reduce(",),
            "js-flatten": ("concat(",), "js-debounce": ("setTimeout", "Date.now"), "js-date-format": ("getFullYear",)}


def _ver(s: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", s)[:2])


def runtime_versions(root: Path) -> dict[str, str]:
    """The oldest Python / Node version the repo says it supports ('' when it does not say)."""
    def read(rel):
        try:
            return (Path(root) / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
    py = ""
    for rel, pat in (("pyproject.toml", r"requires-python\s*=\s*[\"'][^\"']*?(\d+\.\d+)"), ("setup.cfg", r"python_requires\s*=\s*[^\n]*?(\d+\.\d+)"),
                     ("setup.py", r"python_requires\s*=\s*[\"'][^\"']*?(\d+\.\d+)"), (".python-version", r"^\s*(\d+\.\d+)")):
        m = re.search(pat, read(rel), re.M)
        if m:
            py = m.group(1)
            break
    node = ""
    try:
        eng = (json.loads(read("package.json") or "{}").get("engines") or {}).get("node", "")
        m = re.search(r"(\d+)", eng)
        node = m.group(1) if m else ""
    except ValueError:
        pass
    if not node:
        m = re.search(r"v?(\d+)", read(".nvmrc"))
        node = m.group(1) if m else ""
    return {"python": py, "node": node}


def _norm(n: str) -> str:
    return re.sub(r"[-_.]+", "-", n).lower()


def refresh_latest(ix: Index) -> dict:
    """Bring in what is current, from the network: the latest version of every dependency, the latest docs for dependencies
    that have none on file, and the release notes between the version the repo uses and the latest. The scan then compares the
    code with those. Every step is best effort: without network the scan still runs on what is already on file."""
    from concurrent.futures import ThreadPoolExecutor
    root = Path(ix.root)
    out = {"docs_fetched": [], "docs_failed": [], "notes": {}, "latest": {}, "error": "", "notes_error": ""}
    try:
        rows = docs_mod.write_libs(root, online=True)
    except Exception as e:
        out["error"] = f"could not check the package registries: {str(e)[:160]}"
        return out
    used = _usage(ix, rows)
    out["latest"] = {_norm(r["name"]): r["latest"] for r in rows if r.get("latest")}
    need = sorted((r for r in rows if not r.get("docs") and r.get("latest")), key=lambda r: (r.get("behind") not in ("major", "minor"), -len(used.get(_norm(r["name"]), []))))

    def fetch(r):
        try:
            m = docs_mod.add(f"{r['eco']}:{r['name']}", follow=True, max_pages=15)
            return r["name"], f"{m['version']}".strip(), None
        except Exception as e:
            return r["name"], "", str(e)[:120]
    with ThreadPoolExecutor(6) as ex:
        for name, ver, err in ex.map(fetch, need[:FETCH_DOCS_CAP]):
            (out["docs_failed"] if err else out["docs_fetched"]).append({"name": name, "version": ver, **({"error": err} if err else {})})

    def notes(r):
        try:
            repo = docs_mod.source_repo(r["eco"], r["name"])
            if not repo:
                return r["name"], None
            using = re.search(r"\d+(?:\.\d+)*", r.get("locked") or r.get("spec") or "")
            rel = docs_mod.release_notes(repo, using.group(0) if using else "0", r["latest"])
            lines = []
            for tag, body in reversed(rel):                      # newest first
                for ln in body.splitlines():
                    t = re.sub(r"^[\s*+\-#>]+|\s+$", "", ln)
                    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
                    if 20 <= len(t) <= 220 and NEWS.search(t) and not re.search(r"^(full changelog|what's changed|new contributors|@)", t, re.I):
                        lines.append(f"{tag}: {t[:180]}")
                    if len(lines) >= 6:
                        break
                if len(lines) >= 6:
                    break
            return r["name"], {"repo": repo, "url": f"https://github.com/{repo}/releases", "highlights": lines, "releases": len(rel)}
        except Exception as e:   # usually GitHub's rate limit for anonymous calls: say so once, the rest of the scan is unaffected
            out["notes_error"] = f"release notes could not be read from GitHub ({str(e)[:80]}); set GITHUB_TOKEN on the machine that runs devmem"
            return r["name"], None
    behind = [r for r in rows if r.get("behind") in ("major", "minor")]
    behind.sort(key=lambda r: (r["behind"] != "major", -len(used.get(_norm(r["name"]), []))))
    with ThreadPoolExecutor(4) as ex:
        for name, n in ex.map(notes, behind[:NOTES_CAP]):
            if n:
                out["notes"][_norm(name)] = n
    return out


def _usage(ix: Index, libs: list[dict]) -> dict[str, list[dict]]:
    """package name -> the non-test functions in files that import it, most called first."""
    names = {_norm(l["name"]) for l in libs}
    by_lib: dict[str, list[dict]] = {}
    for path, rec in ix.data["files"].items():
        if TEST_FILE.search(path):
            continue
        tops = set()
        for i in rec.get("imports", []):
            mod = i.get("module") or ""
            if rec.get("lang") in ("js", "ts"):
                if mod.startswith("."):
                    continue
                top = "/".join(mod.split("/")[:2]) if mod.startswith("@") else mod.split("/")[0]
            else:
                top = mod.split(".")[0]
                top = IMPORT_ALIAS.get(top.lower(), top)
            if _norm(top) in names:
                tops.add(_norm(top))
        if tops:
            syms = [x for x in ix.by_file.get(path, []) if x["kind"] in ("function", "method") and not x.get("is_test")]
            for t in tops:
                by_lib.setdefault(t, []).extend(syms)
    for t in by_lib:
        by_lib[t].sort(key=lambda x: (-len(ix.inc.get(x["id"], [])), x["id"]))
    return by_lib


def scan(ix: Index, latest: dict | None = None) -> dict:
    """Run the catalog over every non-test function and class, then the docs check over the most-called functions.
    `latest` is what refresh_latest brought in (None keeps what the previous scan stored)."""
    root = Path(ix.root)
    if latest is None:
        latest = (load(root) or {}).get("latest_info") or {}
    rt = runtime_versions(root)
    deps = {_norm(d["name"]): d for d in docs_mod.scan_deps(root)}
    findings, seen = [], set()
    by_lang_rules: dict[str, list[dict]] = {}
    for r in RULES:
        for lang in r["langs"]:
            by_lang_rules.setdefault(lang, []).append(r)
    cache: dict[str, list[str]] = {}

    def body(s):
        if s["file"] not in cache:
            try:
                cache[s["file"]] = (root / s["file"]).read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                cache[s["file"]] = []
        return "\n".join(cache[s["file"]][s["line"] - 1: s.get("end_line", s["line"])])

    for s in ix.symbols.values():
        lang = (ix.data["files"].get(s["file"]) or {}).get("lang")
        rules = by_lang_rules.get(lang)
        if not rules or s["kind"] == "module" or s.get("is_test") or TEST_FILE.search(s["file"]):
            continue
        src = None
        for r in rules:
            if (r.get("on") == "class") != (s["kind"] == "class"):
                continue
            src = body(s) if src is None else src
            if len(src) > 20000 or not src:
                break
            name = _words(s["qualname"].split(".")[-1])
            if r.get("names") and not (r["names"] & name):
                continue
            try:
                if not r["test"](src, name):
                    continue
            except re.error:
                continue
            have = next((deps[_norm(x)] for x in (r.get("lib", ""), *r.get("alt", ())) if x and _norm(x) in deps), None)
            if r["kind"] == "library" and r.get("only_if_dep") and not have:
                continue
            if r["kind"] == "stdlib":
                env = "python" if lang == "python" else "node"
                declared, need = rt[env], r["since"]
                if declared and _ver(declared) < _ver(need):
                    status, needs = "upgrade", f"needs {'Python' if env == 'python' else 'Node.js'} {need}+; this repo declares {declared}"
                elif declared:
                    status, needs = "ready", f"available since {'Python' if env == 'python' else 'Node.js'} {need}; this repo declares {declared}"
                else:
                    status, needs = "ready", f"needs {'Python' if env == 'python' else 'Node.js'} {need}+ (the repo does not declare a minimum version: check it)"
                source = "the standard library" if env == "python" else "the JavaScript runtime"
            elif have:
                status, needs, source = "ready", f"{have['name']} {have.get('spec') or ''} is already a dependency".replace("  ", " "), "a library you already use"
            else:
                status, needs, source = "new-dependency", f"would add {r['lib']} as a dependency: worth it only if this code needs to grow", "a library you do not use yet"
            line_off, evidence = 0, ""
            for n, ln in enumerate(src.splitlines()):
                if any(k in ln for k in EVIDENCE.get(r["id"], ())):
                    line_off, evidence = n, ln.strip()[:160]
                    break
            callers = len({e["src"] for e in ix.inc.get(s["id"], []) if e["type"] == "calls"})
            newest = (latest.get("latest") or {}).get(_norm(have["name"])) if have else None
            if have and newest:
                needs += f"; the latest release is {newest}"
            findings.append({"id": s["id"], "rule": r["id"], "name": s["qualname"], "file": s["file"], "line": s["line"] + line_off, "symbol_line": s["line"],
                             "what": r["what"], "use": r["use"], "source": source, "kind": r["kind"], "status": status, "needs": needs, "confidence": r["conf"],
                             "url": DOC_URL.get(r["id"], ""),
                             "evidence": evidence, "callers": callers, "lines": s.get("end_line", s["line"]) - s["line"] + 1})
            seen.add(s["id"])

    # the docs on file: a function named after something a library documents as built in
    extra = [docs_mod.docs_dir()]
    internal = _internal_names(ix)
    cands = [s for s in ix.symbols.values() if s["kind"] in ("function", "method") and not s.get("is_test") and s["id"] not in seen and not TEST_FILE.search(s["file"])]
    cands.sort(key=lambda s: -len(ix.inc.get(s["id"], [])))
    from devmem import docs as core_docs
    if core_docs.libraries(root, extra):
        for s in cands[:DOCS_SCAN_CAP]:
            try:
                notes = core_reuse.modernize(_entry(ix, s, internal, 0), root, extra)
            except Exception:
                notes = []
            for n in notes[:1]:
                m = re.search(r": (.*?) documents a built-in \"(.*?)\" \((.*?)\)", n) or re.search(r"but (.*?) now documents a built-in \"(.*?)\" \((.*?)\)", n)
                if not m:
                    continue
                uses = " already, but " in n
                callers = len({e["src"] for e in ix.inc.get(s["id"], []) if e["type"] == "calls"})
                findings.append({"id": s["id"], "rule": "docs-match", "name": s["qualname"], "file": s["file"], "line": s["line"], "symbol_line": s["line"],
                                 "what": f"`{s['name']}`, which shares its name with a documented feature", "use": f"{m.group(1)}: “{m.group(2)}”", "url": m.group(3),
                                 "source": "a library you already use" if uses else "library docs on file", "kind": "library", "status": "ready" if uses else "new-dependency",
                                 "needs": "matched by name against the docs on file: read the linked section before replacing anything", "confidence": "low",
                                 "evidence": (s.get("sig") or "")[:160], "callers": callers, "lines": s.get("end_line", s["line"]) - s["line"] + 1})

    order = {"ready": 0, "upgrade": 1, "new-dependency": 2}
    conf = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (order[f["status"]], conf[f["confidence"]], -f["callers"], f["file"], f["line"]))
    try:
        libs = json.loads((root / ".devmem/libs.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        libs = []
    used = _usage(ix, libs)
    have_docs = {_norm(l["name"]) for l in docs_mod.list_libs()}
    behind = []
    for l in libs:
        if l.get("behind") not in ("major", "minor"):
            continue
        fns = used.get(_norm(l["name"]), [])
        n = (latest.get("notes") or {}).get(_norm(l["name"])) or {}
        behind.append({"name": l["name"], "eco": l["eco"], "using": l.get("locked") or l.get("spec") or "", "latest": l.get("latest") or "", "behind": l["behind"],
                       "docs": _norm(l["name"]) in have_docs, "functions": len(fns), "top": [{"id": x["id"], "name": x["qualname"], "file": x["file"], "line": x["line"]} for x in fns[:5]],
                       "highlights": n.get("highlights", []), "notes_url": n.get("url", ""), "releases": n.get("releases", 0)})
    behind.sort(key=lambda l: (l["behind"] != "major", -l["functions"], l["name"]))
    data = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "runtime": rt, "findings": findings, "behind": behind, "latest_info": latest,
            "libraries": len(libs), "docs_on_file": len(have_docs),
            "scanned": sum(1 for s in ix.symbols.values() if s["kind"] != "module" and not s.get("is_test")), "docs_checked": min(len(cands), DOCS_SCAN_CAP)}
    p = root / PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return data


def load(root: Path) -> dict | None:
    try:
        d = json.loads((Path(root) / PATH).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def view(ix: Index) -> dict:
    d = load(ix.root)
    if not d:
        return {"available": False, "runtime": runtime_versions(Path(ix.root))}
    fs = d["findings"]
    return {"available": True, **d, "counts": {k: sum(1 for f in fs if f["status"] == k) for k in ("ready", "upgrade", "new-dependency")},
            "lines": sum(f["lines"] for f in fs if f["status"] == "ready")}


def verify_with_ai(ix: Index, keys: list[str], provider) -> dict:
    """Ask the LLM whether each candidate really is that pattern and whether the replacement keeps its behaviour. It can
    confirm or reject a finding and say what would be lost; it cannot add findings."""
    d = load(ix.root)
    if not d:
        return {"checked": 0, "error": "scan first"}
    want = set(keys)
    todo = [f for f in d["findings"] if f"{f['id']}|{f['rule']}" in want][:20]
    error = ""
    for n in range(0, len(todo), 5):
        chunk = todo[n:n + 5]
        parts = []
        for i, f in enumerate(chunk):
            try:
                code = ix.source(ix.symbols[f["id"]])[:1500]
            except (OSError, KeyError):
                code = ""
            parts.append(f"### {i}\nClaim: this is {f['what']}; it could be replaced with {f['use']}.\n```\n{code}\n```")
        prompt = ("For each item, decide whether the claim is right for the code shown. Answer \"yes\" only if the code really is that "
                  "pattern AND the replacement would keep its behaviour; otherwise \"no\". In one sentence say what the replacement "
                  "would not cover (custom logging, special cases), or \"nothing\". Reply with ONLY a JSON list: "
                  "[{\"i\": 0, \"verdict\": \"yes\" | \"no\", \"note\": \"...\"}]\n\n" + "\n\n".join(parts))
        try:
            reply = provider.generate(prompt)
            for item in json.loads(reply[reply.index("["): reply.rindex("]") + 1]):
                f = chunk[int(item["i"])]
                f["ai"] = {"verdict": "yes" if str(item.get("verdict", "")).lower().startswith("y") else "no", "note": " ".join(str(item.get("note", "")).split())[:240]}
        except Exception as e:
            error = str(e)[:300]
    (Path(ix.root) / PATH).write_text(json.dumps(d, indent=1), encoding="utf-8")
    return {"checked": sum(1 for f in todo if f.get("ai")), "error": error}
