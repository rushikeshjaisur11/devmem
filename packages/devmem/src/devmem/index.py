"""Load and query .devmem/index.json. Pure stdlib; used by the agent CLI and the app."""
from __future__ import annotations

import json
import re
import sqlite3
import zlib
from collections import defaultdict, deque
from pathlib import Path

from .jsresolve import is_js
from .jsresolve import resolve as js_resolve
from .schema import DB_PATH, INDEX_PATH, MAX_HOPS, UTILITY_CALLER_CAP


def find_repo_root(start: Path | None = None) -> Path:
    p = (start or Path.cwd()).resolve()
    for d in [p, *p.parents]:
        if (d / INDEX_PATH).exists():
            return d
    raise FileNotFoundError("no .devmem/index.json found; run `devmem-app index <repo>` first")


class Index:
    def __init__(self, data: dict, root: Path):
        self.data, self.root = data, root
        self._fts: sqlite3.Connection | None = None
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
        self.out = defaultdict(list)  # src -> edges
        self.inc = defaultdict(list)  # dst -> edges
        for e in data["edges"]:
            self.out[e["src"]].append(e)
            self.inc[e["dst"]].append(e)

    @classmethod
    def load(cls, root: Path | None = None) -> "Index":
        root = find_repo_root(root)
        return cls(json.loads((root / INDEX_PATH).read_text(encoding="utf-8")), root)

    # ---- lookup ----
    def resolve(self, ref: str) -> list[dict]:
        """Symbol by id, 'file:qualname', qualname, or bare name. Real symbols only."""
        real = [s for s in self.symbols.values() if s["kind"] != "module"]
        if ref in self.symbols:
            return [self.symbols[ref]]
        if ":" in ref and "#" not in ref:
            f, q = ref.rsplit(":", 1)
            ref_id = f"{f}#{q}"
            if ref_id in self.symbols:
                return [self.symbols[ref_id]]
        exact = [s for s in real if s["qualname"] == ref]
        return exact or [s for s in real if s["name"] == ref]

    def find(self, query: str, limit: int = 10) -> list[tuple[float, dict]]:
        q = _tokens(query)
        if not q:
            return []
        scored = [(sc, s) for s in map(self.symbols.get, self._candidates(q, query, limit)) if (sc := _score(q, query, s)) > 0]
        scored.sort(key=lambda x: (-x[0], x[1]["file"], x[1]["line"]))
        return scored[:limit]

    def _candidates(self, q: set[str], query: str, limit: int) -> list[str]:
        """Ids of symbols sharing a token with the query (best bm25 first) or containing it in the qualname.
        Capped at max(CANDIDATE_CAP, 10 * limit) per source: exact unless a query matches more symbols than that."""
        if self._fts is None:
            self._fts = _open_fts(self)
        cap = max(CANDIDATE_CAP, 10 * limit)
        match = " OR ".join(f'"{t}"' for t in sorted(q))
        ranked = self._fts.execute(  # column weights mirror _score: name 3, doc 1, path 0.5 (bm25 is lower = better)
            "SELECT id FROM sym WHERE sym MATCH ? ORDER BY bm25(sym, 0, 3, 1, 0.5) LIMIT ?", (match, cap)).fetchall()
        substr = self._fts.execute("SELECT id FROM qual WHERE instr(q, ?) > 0 LIMIT ?", (query.lower(), cap)).fetchall()
        return list(dict.fromkeys(r[0] for r in ranked + substr))

    def source(self, s: dict) -> str:
        lines = (self.root / s["file"]).read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[s["line"] - 1 : s["end_line"]])

    def callees(self, s: dict) -> list[dict]:
        seen, out = set(), []
        for e in self.out.get(s["id"], []):
            d = self.symbols.get(e["dst"])
            if e["type"] == "calls" and d and d["id"] not in seen:
                seen.add(d["id"])
                out.append(d)
        return out

    def outline(self, path: str) -> list[dict]:
        return sorted((s for s in self.symbols.values() if s["file"] == path and s["kind"] != "module"),
                      key=lambda s: s["line"])

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
            callers = [e for e in self.inc.get(cur, []) if e["type"] in ("calls", "tests")]
            real = [e for e in callers if e["type"] == "calls"]
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
                item = {"id": e["src"], "conf": e["conf"]}
                res["hop1" if hop == 0 else "hop2"].append(item)
                frontier.append((e["src"], hop + 1))
        # tests that cover any affected symbol
        for sid in list(seen):
            for e in self.inc.get(sid, []):
                if e["type"] == "tests":
                    tests.add(e["src"])
        res["tests"] = sorted(tests)
        res["files"] = sorted({i.split("#")[0] for k in ("hop1", "hop2") for i in (x["id"] for x in res[k])}
                              | {s["file"] for s in targets})
        res["cochange"] = self._cochange_for(res["files"])
        return res

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
        suf = (module.replace(".", "/") + ".py", module.replace(".", "/") + "/__init__.py")
        return [f for f in self.data["files"] if any(f == x or f.endswith("/" + x) for x in suf)]

    def tests_for_files(self, paths: list[str]) -> dict[str, list[str]]:
        """{test_file: [test qualnames]} covering the given changed files (direct + impact tests; import fallback)."""
        tests_by_file = defaultdict(list)
        for s in self.symbols.values():
            if s["is_test"]:
                tests_by_file[s["file"]].append(s)
        picked: dict[str, set[str]] = defaultdict(set)
        for path in paths:
            if path in tests_by_file:
                picked[path] |= {s["qualname"] for s in tests_by_file[path]}
                continue
            found = False
            for s in self.symbols.values():
                if s["file"] == path and s["kind"] != "module":
                    for t in self.impact(s["id"]).get("tests", []):
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


def _tokens(text: str) -> set[str]:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    return {t.lower() for t in re.split(r"[^A-Za-z0-9]+", text) if len(t) > 1}


def _score(q: set[str], query: str, s: dict) -> float:
    score = 3.0 * len(q & _tokens(s["qualname"])) + 1.0 * len(q & _tokens(s["doc"])) + 0.5 * len(q & _tokens(s["file"]))
    if query.lower() in s["qualname"].lower():
        score += 2
    return score * 0.5 if s["is_test"] else score


CANDIDATE_CAP = 300


def _rebuild_fts(db: sqlite3.Connection, ix: "Index") -> None:
    """`sym` = FTS5 over camel/snake-split tokens; `qual` = plain table (an FTS column is too slow to substring-scan)."""
    db.execute("DROP TABLE IF EXISTS sym")
    db.execute("DROP TABLE IF EXISTS qual")
    db.execute("CREATE VIRTUAL TABLE sym USING fts5(id UNINDEXED, name, doc, path)")
    db.execute("CREATE TABLE qual(id TEXT, q TEXT)")
    for s in ix.symbols.values():
        if s["kind"] != "module":
            db.execute("INSERT INTO sym VALUES (?,?,?,?)",
                       (s["id"], *(" ".join(sorted(_tokens(s[k]))) for k in ("qualname", "doc", "file"))))
            db.execute("INSERT INTO qual VALUES (?,?)", (s["id"], s["qualname"].lower()))


def _stamp(src: Path) -> int:
    st = src.stat()
    return zlib.crc32(f"{st.st_size}:{st.st_mtime_ns}".encode()) & 0x7FFFFFFF  # fits PRAGMA user_version


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
        db = sqlite3.connect(f"file:{cache.as_posix()}?mode=ro", uri=True)
        try:
            if db.execute("PRAGMA user_version").fetchone()[0] == _stamp(src):
                return db
        except sqlite3.DatabaseError:
            pass
        db.close()
    db = sqlite3.connect(":memory:")
    _rebuild_fts(db, ix)
    return db
