"""Load and query .devmem/index.json. Pure stdlib; used by the agent CLI and the app."""
from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from pathlib import Path

from .schema import INDEX_PATH, MAX_HOPS, UTILITY_CALLER_CAP


def find_repo_root(start: Path | None = None) -> Path:
    p = (start or Path.cwd()).resolve()
    for d in [p, *p.parents]:
        if (d / INDEX_PATH).exists():
            return d
    raise FileNotFoundError("no .devmem/index.json found; run `devmem-app index <repo>` first")


class Index:
    def __init__(self, data: dict, root: Path):
        self.data, self.root = data, root
        self.symbols = {s["id"]: s for s in data["symbols"]}
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
        scored = []
        for s in self.symbols.values():
            if s["kind"] == "module":
                continue
            name_t, doc_t, path_t = _tokens(s["qualname"]), _tokens(s["doc"]), _tokens(s["file"])
            score = 3.0 * len(q & name_t) + 1.0 * len(q & doc_t) + 0.5 * len(q & path_t)
            if query.lower() in s["qualname"].lower():
                score += 2
            if s["is_test"]:
                score *= 0.5
            if score > 0:
                scored.append((score, s))
        scored.sort(key=lambda x: (-x[0], x[1]["file"], x[1]["line"]))
        return scored[:limit]

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
        return res

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
