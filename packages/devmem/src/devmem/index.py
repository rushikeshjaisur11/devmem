"""Load and query .devmem/index.json. Pure stdlib; used by the agent CLI and the app."""
from __future__ import annotations

import ast
import hashlib
import json
import re
import sqlite3
import threading
import zlib
from collections import defaultdict, deque
from pathlib import Path

from .jsresolve import is_js, module_files
from .jsresolve import resolve as js_resolve
from .schema import DB_PATH, DESC_PATH, INDEX_PATH, INDEX_VERSION, MAX_HOPS, UTILITY_CALLER_CAP


def find_repo_root(start: Path | None = None) -> Path:
    p = (start or Path.cwd()).resolve()
    for d in [p, *p.parents]:
        if (d / INDEX_PATH).exists():
            return d
    raise FileNotFoundError("no .devmem/index.json found; run `devmem-app index <repo>` first")


def _stat(p: Path):
    try:
        return p.stat()
    except OSError:
        return None


class Index:
    def __init__(self, data: dict, root: Path):
        self.data, self.root = data, root
        self._states: dict[str, tuple[tuple, str]] = {}
        self._fts: sqlite3.Connection | None = None
        self._fts_lock = threading.Lock()   # one connection, queried from the web app's thread pool: one query at a time
        self.symbols = {s["id"]: s for s in data["symbols"]}
        self.cochange: dict[str, list[tuple[str, int]]] = defaultdict(list)
        hp = Path(root) / ".devmem/history.json"
        if hp.exists():
            try:
                for c in json.loads(hp.read_text(encoding="utf-8")).get("cochange", []):
                    self.cochange[c["a"]].append((c["b"], c["count"]))
                    self.cochange[c["b"]].append((c["a"], c["count"]))
            except Exception:
                pass
        # plain-language descriptions written on the Improve descriptions page (by a person or an LLM) for code whose name and
        # docstring say too little; search treats them like a docstring
        self.desc: dict[str, str] = {}
        try:
            for sid, d in json.loads((Path(root) / DESC_PATH).read_text(encoding="utf-8")).items():
                if sid in self.symbols and d.get("text"):
                    self.desc[sid] = d["text"]
        except (OSError, ValueError, AttributeError):
            pass
        # measured coverage (devmem_app/coverage_import.py): {symbol id: [covered lines, body lines]} and {symbol id: [test ids]}
        self.coverage: dict = {}
        try:
            cov = json.loads((Path(root) / ".devmem/coverage.json").read_text(encoding="utf-8"))
            if isinstance(cov, dict) and isinstance(cov.get("functions"), dict):
                self.coverage = cov
        except (OSError, ValueError):
            pass
        self.out = defaultdict(list)  # src -> edges
        self.inc = defaultdict(list)  # dst -> edges
        for e in data["edges"]:
            self.out[e["src"]].append(e)
            self.inc[e["dst"]].append(e)
        # lookup tables (real symbols only, in index order), so resolve / outline / tests-for never scan every symbol
        self.by_name: dict[str, list[dict]] = defaultdict(list)
        self.by_qual: dict[str, list[dict]] = defaultdict(list)
        self.by_file: dict[str, list[dict]] = defaultdict(list)
        for s in data["symbols"]:
            if s["kind"] != "module":
                self.by_name[s.get("name", "")].append(s)      # .get: callers build partial indexes for one query
                self.by_qual[s.get("qualname", "")].append(s)
                self.by_file[s["file"]].append(s)

    @classmethod
    def load(cls, root: Path | None = None) -> "Index":
        root = find_repo_root(root)
        try:
            data = json.loads((root / INDEX_PATH).read_text(encoding="utf-8"))
        except ValueError as e:
            raise FileNotFoundError(f"{INDEX_PATH} is not readable ({e}); rebuild it with `devmem-app index <repo>`") from None
        if not isinstance(data, dict) or "symbols" not in data or "edges" not in data:
            raise FileNotFoundError(f"{INDEX_PATH} is not a devmem index; rebuild it with `devmem-app index <repo>`")
        if int(data.get("version") or 1) > INDEX_VERSION:
            raise FileNotFoundError(f"{INDEX_PATH} was written by a newer devmem (format {data.get('version')}, this CLI reads up to {INDEX_VERSION}); "
                                    "update the devmem CLI before using it")
        return cls(data, root)

    _loaded: dict[str, tuple[tuple, "Index"]] = {}

    @classmethod
    def cached(cls, root: Path | None = None) -> "Index":
        """Like load, but a long-lived process (the MCP server) parses index.json once and again only when it changes."""
        root = find_repo_root(root)
        stamp = tuple((st.st_mtime_ns, st.st_size) if (st := _stat(root / f)) else None
                      for f in (INDEX_PATH, ".devmem/history.json", DESC_PATH))
        hit = cls._loaded.get(str(root))
        if not hit or hit[0] != stamp:
            cls._loaded[str(root)] = hit = (stamp, cls.load(root))
        return hit[1]

    # ---- lookup ----
    def resolve(self, ref: str) -> list[dict]:
        """Symbol by id, 'file:qualname', qualname, or bare name. Real symbols only."""
        if ref in self.symbols:
            return [self.symbols[ref]]
        if ":" in ref and "#" not in ref:
            f, q = ref.rsplit(":", 1)
            ref_id = f"{f}#{q}"
            if ref_id in self.symbols:
                return [self.symbols[ref_id]]
        return list(self.by_qual.get(ref) or self.by_name.get(ref) or [])

    def find(self, query: str, limit: int = 10) -> list[tuple[float, dict]]:
        q = query_tokens(query)
        if not q:
            return []
        cands = [s for s in map(self.symbols.get, self._candidates(q, query, limit)) if s]
        w = self.idf(q)
        scored = [(sc, s) for s in cands if (sc := _score(q, query, s, self.desc.get(s["id"], ""), w)) > 0]
        scored.sort(key=lambda x: (-x[0], x[1]["file"], x[1]["line"]))
        return scored[:limit]

    def idf(self, q: set[str]) -> dict[str, float]:
        """How much each query word says: log(N / symbols containing it), from the search tables' own vocabulary, so a word
        on every other symbol (get, data, value) counts for little and a rare one (interval, refund) for a lot."""
        import math
        with self._fts_lock:
            if self._fts is None:
                self._fts = _open_fts(self)
            ph = ",".join("?" * len(q))
            df = dict(self._fts.execute(f"SELECT term, doc FROM symv WHERE term IN ({ph})", sorted(q)).fetchall()) if q else {}
        n = max(len(self.symbols), 1)
        return {t: math.log(1 + n / (1 + df.get(t, 0))) for t in q}

    def _candidates(self, q: set[str], query: str, limit: int) -> list[str]:
        """Ids of symbols sharing a token with the query (best bm25 first) or containing it in the qualname.
        Capped at max(CANDIDATE_CAP, 10 * limit) per source: exact unless a query matches more symbols than that."""
        cap = max(CANDIDATE_CAP, 10 * limit)
        match = " OR ".join(f'"{t}"' for t in sorted(q))
        with self._fts_lock:
            if self._fts is None:
                self._fts = _open_fts(self)
            ranked = self._fts.execute(  # column weights mirror _score: name 3, doc 1, path 0.5 (bm25 is lower = better)
                "SELECT id FROM sym WHERE sym MATCH ? ORDER BY bm25(sym, 0, 3, 1, 0.5) LIMIT ?", (match, cap)).fetchall()
            substr = self._fts.execute("SELECT id FROM qual WHERE instr(q, ?) > 0 LIMIT ?", (query.lower(), cap)).fetchall()
        return list(dict.fromkeys(r[0] for r in ranked + substr))

    # ---- freshness: the index stores line numbers, and files move on after it was built ----
    def file_state(self, path: str) -> str:
        """'fresh' (same bytes as when indexed), 'changed', or 'missing'. Checked against the content hash the indexer stored;
        cached per file on its size and mtime, so repeated questions do not re-hash."""
        fp = self.root / path
        try:
            st = fp.stat()
        except OSError:
            return "missing"
        key = (st.st_mtime_ns, st.st_size)
        hit = self._states.get(path)
        if hit and hit[0] == key:
            return hit[1]
        want = (self.data["files"].get(path) or {}).get("hash")
        try:
            state = "fresh" if not want or hashlib.sha1(fp.read_bytes()).hexdigest() == want else "changed"
        except OSError:
            state = "missing"
        self._states[path] = (key, state)
        return state

    def changed_files(self, paths) -> list[str]:
        """Of the given files, the ones edited or deleted since the index was built, in a stable order."""
        return [p for p in dict.fromkeys(paths) if p in self.data["files"] and self.file_state(p) != "fresh"]

    def read(self, s: dict) -> tuple[str, int, int, str]:
        """(source, first line, last line, note) of a symbol as it is in the file NOW. When the file changed since indexing
        the symbol is found again rather than read from stale line numbers; the note says so. Raises LookupError when it
        cannot be found: printing the wrong lines would be worse than printing none."""
        state = self.file_state(s["file"])
        if state == "missing":
            raise LookupError(f"{s['file']} no longer exists (the index is older than the code): re-index the repo")
        lines = (self.root / s["file"]).read_text(encoding="utf-8", errors="replace").splitlines()
        start, end = s["line"], s.get("end_line", s["line"])
        if state == "fresh":
            return "\n".join(lines[start - 1: end]), start, end, ""
        found = _relocate(s, lines)
        if not found:
            raise LookupError(f"{s['qualname']} is no longer in {s['file']} (the file changed since it was indexed; it may have been renamed "
                              f"or removed): re-index the repo, or `devmem outline {s['file']}` shows what the index knew")
        start, end, exact = found
        note = (f"{s['file']} changed since it was indexed: `{s['qualname']}` is now at lines {start}-{end}"
                + ("" if exact else " (found by its name; the end line is an estimate)") + ". Callers and tests listed for it may be out of date.")
        return "\n".join(lines[start - 1: end]), start, end, note

    def source(self, s: dict) -> str:
        try:
            return self.read(s)[0]
        except LookupError as e:
            raise OSError(str(e)) from None

    def callees(self, s: dict) -> list[dict]:
        seen, out = set(), []
        for e in self.out.get(s["id"], []):
            d = self.symbols.get(e["dst"])
            if e["type"] == "calls" and d and d["id"] not in seen:
                seen.add(d["id"])
                out.append(d)
        return out

    def outline(self, path: str) -> list[dict]:
        return sorted(self.by_file.get(path, []), key=lambda s: s["line"])

    # ---- impact ----
    def impact(self, ref: str) -> dict:
        """Backward search: hop 1 required, hop 2 contract check, nothing beyond.
        Utilities with > UTILITY_CALLER_CAP callers are capped and listed separately; tests are separate."""
        if ref.startswith("table:") or not self.resolve(ref):
            tname = ref if ref.startswith("table:") else f"table:{ref}"
            return self._table_impact(tname)
        targets = self.resolve(ref)
        res = {"targets": [s["id"] for s in targets], "hop1": [], "hop2": [], "tests": [], "capped": []}
        seen = {s["id"] for s in targets}
        frontier = deque((s["id"], 0) for s in targets)
        tests = set()
        while frontier:
            cur, hop = frontier.popleft()
            if hop >= MAX_HOPS:
                continue
            callers = [e for e in self.inc.get(cur, []) if e["type"] in ("calls", "tests", "refs")]
            real = [e for e in callers if e["type"] in ("calls", "refs")]
            if len(real) > UTILITY_CALLER_CAP:
                res["capped"].append({"symbol": cur, "callers": len(real)})
                real = []
            for e in callers:
                if e["type"] == "tests":
                    tests.add(e["src"])
            for e in real:
                if e["src"] in seen:
                    continue
                seen.add(e["src"])
                item = {"id": e["src"], "conf": "by-name" if e["type"] == "refs" else e["conf"]}
                res["hop1" if hop == 0 else "hop2"].append(item)
                frontier.append((e["src"], hop + 1))
        # tests that cover any affected symbol: from the call graph, and from a measured test run when one was imported
        measured = set()
        for sid in list(seen):
            for e in self.inc.get(sid, []):
                if e["type"] == "tests":
                    tests.add(e["src"])
            measured.update((self.coverage.get("tests") or {}).get(sid, []))
        res["tests"] = sorted(tests | measured)
        res["tests_measured"] = sorted(measured - tests)
        res["coverage"] = {t["id"]: (self.coverage.get("functions") or {}).get(t["id"]) for t in targets} if self.coverage else {}
        res["files"] = sorted({i.split("#")[0] for k in ("hop1", "hop2") for i in (x["id"] for x in res[k])}
                              | {s["file"] for s in targets})
        res["cochange"] = self._cochange_for(res["files"])
        # inheritance: who overrides the target (their signatures must follow it), and whose callers reach it through a base class
        res["overridden_by"] = sorted({e["src"] for t in targets for e in self.inc.get(t["id"], []) if e["type"] == "overrides"})
        bases = sorted({e["dst"] for t in targets for e in self.out.get(t["id"], []) if e["type"] == "overrides"})
        res["overrides"] = bases
        res["via_base"] = sorted({e["src"] for b in bases for e in self.inc.get(b, []) if e["type"] in ("calls", "refs")} - seen)
        res["blind_spots"] = [n for t in targets for n in self.blind_spots(t)]
        return res

    def blind_spots(self, s: dict) -> list[str]:
        """What the call graph cannot show about a symbol, in plain words. "No callers" is only safe to rely on when this is
        empty: a decorator, a public export or the name appearing as text all mean something may call it unseen."""
        out = []
        decos = [d for d in s.get("decorators", []) if d.split(".")[-1] not in ("staticmethod", "classmethod", "property", "abstractmethod", "override", "wraps",
                                                                               "lru_cache", "cache", "cached_property", "dataclass", "contextmanager")]
        if decos:
            out.append(f"{s['qualname']} is decorated with @{', @'.join(decos[:3])}: a framework may call it without any call appearing in the code")
        rec = self.data["files"].get(s["file"], {})
        if s["name"] in rec.get("exports", []) and "." not in s["qualname"]:
            out.append(f"{s['qualname']} is exported in __all__: code outside this repo may use it")
        if "." not in s["qualname"] and len(s["name"]) >= 4:
            named = [f for f, r in self.data["files"].items() if s["name"] in r.get("strs", ())][:3]
            if named:
                out.append(f"the name \"{s['name']}\" appears as text in {', '.join(named)}: it may be looked up by name (getattr, a routing or config table)")
        return out

    def _cochange_for(self, files: list[str], top: int = 5) -> list[str]:
        score: dict[str, int] = defaultdict(int)
        for f in files:
            for other, n in self.cochange.get(f, []):
                if other not in files:
                    score[other] += n
        return [f for f, _ in sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))[:top]]

    def _table_impact(self, tname: str) -> dict:
        readers = sorted({e["src"] for e in self.inc.get(tname, []) if e["type"] == "reads"})
        writers = sorted({e["src"] for e in self.inc.get(tname, []) if e["type"] == "writes"})
        return {"table": tname, "readers": readers, "writers": writers,
                "files": sorted({x.split("#")[0] for x in readers + writers})}

    # ---- tests ----
    def _module_paths(self, module: str) -> list[str]:
        return module_files(self.data["files"], module)

    def coverage_note(self, paths: list[str]) -> str:
        """Where a test selection came from, so the agent knows how far to trust a passing run."""
        cov = self.coverage
        if cov and cov.get("per_test"):
            older = [p for p in paths if p in (cov.get("hashes") or {}) and (cov["hashes"][p] != (self.data["files"].get(p) or {}).get("hash"))]
            return (f"tests chosen from measured coverage ({cov.get('source')}, {cov.get('ts', '')[:10]})"
                    + (f"; {', '.join(older[:3])} changed since that test run" if older else ""))
        return ("tests chosen from the call graph: tests that reach this code through fixtures, helpers or a framework may be missing. "
                "Run the full suite before merging" + ("" if cov else "; import a coverage report (`devmem-app coverage <repo> <file>`) to make this exact"))

    def tests_for_files(self, paths: list[str]) -> dict[str, list[str]]:
        """{test_file: [test qualnames]} covering the given changed files (direct + impact tests; import fallback)."""
        tests_by_file = {f: ts for f, syms in self.by_file.items() if (ts := [s for s in syms if s["is_test"]])}
        picked: dict[str, set[str]] = defaultdict(set)
        for path in paths:
            if path in tests_by_file:
                picked[path] |= {s["qualname"] for s in tests_by_file[path]}
                continue
            found = False
            for s in self.by_file.get(path, []):
                for t in self.impact(s["id"]).get("tests", []):
                    if "#" not in t:
                        continue
                    f, q = t.split("#", 1)
                    picked[f].add(q)
                    found = True
            if not found:  # fallback: test files that import this module
                for tf, ts in tests_by_file.items():
                    for imp in self.data["files"][tf].get("imports", []):
                        if is_js(tf):
                            if path in js_resolve(self.data["files"], tf, imp["module"]):
                                picked[tf] |= {t["qualname"] for t in ts}
                            continue
                        mods = {imp["module"], f"{imp['module']}.{imp['name']}" if imp["name"] else imp["module"]}
                        if any(path in self._module_paths(m) for m in mods):
                            picked[tf] |= {t["qualname"] for t in ts}
        return {f: sorted(q) for f, q in picked.items()}

    @staticmethod
    def pytest_ids(selection: dict[str, list[str]], collapse_over: int = 10) -> list[str]:
        ids = []
        for f, quals in sorted(selection.items()):
            if len(quals) > collapse_over:
                ids.append(f)
            else:
                ids += [f + "::" + q.replace(".", "::") for q in quals]
        return ids

    UTILITY_PATH = re.compile(r"(^|/)(utils?|helpers?|common|shared|lib|core)(/|\.py$|_)", re.I)

    def helpers(self, limit: int = 40) -> list[tuple[dict, int]]:
        """Reusable utilities: public non-test functions called from >= 2 files, or documented functions in
        utility modules (util/helpers/common/shared/lib/core)."""
        out = []
        for s in self.symbols.values():
            if s["kind"] != "function" or s["is_test"] or s["name"].startswith("_"):
                continue
            files = {e["src"].split("#")[0] for e in self.inc.get(s["id"], []) if e["type"] == "calls"}
            if len(files) >= 2 or (s["doc"] and self.UTILITY_PATH.search(s["file"])):
                out.append((s, len(files)))
        out.sort(key=lambda x: (-x[1], x[0]["id"]))
        return out[:limit]


_DEF = re.compile(r"\b(def|class|function|func|fn|fun|interface|struct|enum|trait|impl|const|let|var|public|private|protected|static|async|export)\b")


def _relocate(s: dict, lines: list[str]) -> tuple[int, int, bool] | None:
    """Where a symbol is in the current text of its file: (first line, last line, exact?). Python is re-parsed, so the answer
    is exact. Other languages: the definition line nearest to where it used to be, and the block that follows it."""
    name, qual = s["name"], s["qualname"]
    if s["file"].endswith(".py"):
        try:
            tree = ast.parse("\n".join(lines))
        except SyntaxError:
            tree = None
        if tree is not None:
            def walk(body, prefix):
                for n in body:
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        q = f"{prefix}{n.name}"
                        if q == qual:
                            return n
                        if isinstance(n, ast.ClassDef):
                            hit = walk(n.body, q + ".")
                            if hit is not None:
                                return hit
                return None
            node = walk(tree.body, "")
            return (node.lineno, getattr(node, "end_lineno", node.lineno), True) if node is not None else None
    pat = re.compile(rf"(?<![\w$]){re.escape(name)}(?![\w$])\s*(\(|=|:|<|\{{|$)")
    cands = [i for i, ln in enumerate(lines, 1) if pat.search(ln) and (_DEF.search(ln) or re.match(rf"\s*{re.escape(name)}\s*\(", ln)) and not ln.lstrip().startswith(("//", "#", "*", "/*"))]
    if not cands:
        return None
    start = min(cands, key=lambda i: abs(i - s["line"]))
    length = max(s.get("end_line", s["line"]) - s["line"], 0)
    depth, seen_open, end = 0, False, None
    for i in range(start, min(len(lines), start + max(length * 3, 400)) + 1):
        for ch in lines[i - 1]:
            if ch == "{":
                depth += 1; seen_open = True
            elif ch == "}":
                depth -= 1
        if seen_open and depth <= 0:
            end = i
            break
        if not seen_open and i - start > 3:
            break
    return start, end or min(len(lines), start + length), False


_SUFFIX = ("ators", "ations", "ator", "ation", "ating", "ated", "ate", "ings", "ing", "ers", "er", "ed", "es", "ion", "s")


def _stem(t: str) -> str:
    """Crude suffix stripping so validate / validator / validation and table / tables / parse / parsing meet in the middle."""
    for suf in _SUFFIX:
        if t.endswith(suf) and len(t) - len(suf) >= 4:
            t = t[: -len(suf)]
            break
    return t[:-1] if t.endswith("e") and len(t) > 4 else t


def _tokens(text: str) -> set[str]:
    """Words of an identifier or sentence, stemmed, so "save settings" finds saveSetting / savingSettings."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    return {_stem(t.lower()) for t in re.split(r"[^A-Za-z0-9]+", text) if len(t) > 1}


STOP = {"the", "and", "for", "with", "how", "to", "in", "of", "on", "is", "it", "we", "do", "an", "this", "that", "when", "from", "into", "not",
        "are", "be", "by", "or", "as", "at", "if", "its", "their", "which", "will", "can", "should", "where", "what", "who", "all", "any", "each",
        "one", "only", "been", "has", "have", "was", "were", "there", "these", "those", "than", "then", "so", "such", "also", "but", "no", "yes"}


def query_tokens(query: str) -> set[str]:
    """The words of a query that carry meaning: stop words dropped unless nothing else is left."""
    q = _tokens(query)
    return (q - STOP) or q


def _score(q: set[str], query: str, s: dict, desc: str = "", w: dict[str, float] | None = None, name: set[str] | None = None) -> float:
    """Each query word counts once, in the best place it appears (name 3, docstring or description 1, path 0.5), weighted by
    how rare it is in the repo."""
    name = _tokens(s["qualname"]) if name is None else name
    said = _tokens(f"{s['doc']} {desc}")
    path = _tokens(s["file"])
    w = w or {}
    score, hit = 0.0, 0
    for t in q:
        f = 3.0 if t in name else 1.0 if t in said else 0.5 if t in path else 0.0
        if f:
            score += f * w.get(t, 1.0)
            hit += t in name
    if hit and len(name) > hit + 2:
        score *= 1 - 0.04 * min(len(name) - hit - 2, 5)     # a long name that merely contains the words is a weaker match than a short one
    if len(query) > 2 and query.lower() in s["qualname"].lower():
        score += sum(w.get(t, 1.0) for t in q)                # the query is literally part of the name
    return score * 0.5 if s["is_test"] else score


CANDIDATE_CAP = 300
FTS_FORMAT = 3   # bump when the search tables change (2: stemmed, 3: vocabulary table for word rarity), so older index.db files are rebuilt


def _rebuild_fts(db: sqlite3.Connection, ix: "Index") -> None:
    """`sym` = FTS5 over camel/snake-split tokens; `qual` = plain table (an FTS column is too slow to substring-scan)."""
    db.execute("DROP TABLE IF EXISTS symv")
    db.execute("DROP TABLE IF EXISTS sym")
    db.execute("DROP TABLE IF EXISTS qual")
    db.execute("CREATE VIRTUAL TABLE sym USING fts5(id UNINDEXED, name, doc, path)")
    db.execute("CREATE TABLE qual(id TEXT, q TEXT)")
    db.execute("CREATE VIRTUAL TABLE symv USING fts5vocab(sym, 'row')")   # per word: how many symbols contain it
    for s in ix.symbols.values():
        if s["kind"] != "module":
            db.execute("INSERT INTO sym VALUES (?,?,?,?)",
                       (s["id"], *(" ".join(sorted(_tokens(t))) for t in (s["qualname"], f"{s['doc']} {ix.desc.get(s['id'], '')}", s["file"]))))
            db.execute("INSERT INTO qual VALUES (?,?)", (s["id"], s["qualname"].lower()))


def _stamp(src: Path) -> int:
    """Identifies what the search tables were built from: index.json and, when present, the descriptions file."""
    st, ds = src.stat(), _stat(src.parent / Path(DESC_PATH).name)
    extra = f":{ds.st_size}:{ds.st_mtime_ns}" if ds else ""
    return zlib.crc32(f"{FTS_FORMAT}:{st.st_size}:{st.st_mtime_ns}{extra}".encode()) & 0x7FFFFFFF  # fits PRAGMA user_version


def write_db(ix: "Index") -> None:
    """Cache the search tables in `.devmem/index.db`, derived from index.json and never committed.
    Called by the indexer after it writes index.json; readers never write (prompt building is read-only)."""
    db = sqlite3.connect(ix.root / DB_PATH)
    with db:
        _rebuild_fts(db, ix)
        db.execute(f"PRAGMA user_version = {_stamp(ix.root / INDEX_PATH)}")
    db.close()
    (ix.root / ".devmem/.gitignore").write_text("index.db*\nusage.jsonl\n", encoding="utf-8")


def _open_fts(ix: "Index") -> sqlite3.Connection:
    """The cached db when it matches index.json, else an in-memory build (read-only; no file is created)."""
    src, cache = ix.root / INDEX_PATH, ix.root / DB_PATH
    if src.exists() and cache.exists():
        db = sqlite3.connect(f"file:{cache.as_posix()}?mode=ro", uri=True, check_same_thread=False)   # read-only; the web app queries it from its thread pool
        try:
            if db.execute("PRAGMA user_version").fetchone()[0] == _stamp(src):
                return db
        except sqlite3.DatabaseError:
            pass
        db.close()
    db = sqlite3.connect(":memory:", check_same_thread=False)
    _rebuild_fts(db, ix)
    return db
