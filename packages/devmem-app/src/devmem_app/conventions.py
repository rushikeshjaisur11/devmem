"""Convention detection: how this repo does logging, config, errors, Spark sessions, DB access, retries and tests.
Each category gets the dominant style(s) and one golden example file (+ a short excerpt) agents should copy."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical"}
DB_LIBS = {"sqlite3": "sqlite3", "pyodbc": "pyodbc", "psycopg2": "psycopg2", "pymysql": "pymysql",
           "sqlalchemy": "SQLAlchemy", "google.cloud.bigquery": "BigQuery client", "cx_Oracle": "cx_Oracle",
           "pymssql": "pymssql"}
CONFIG_STYLES = [("environment variables (`os.getenv`/`os.environ`)", "env"), ("YAML config file", "yaml"),
                 ("JSON config file", "json"), ("pydantic settings", "pydantic"), ("configparser", "configparser")]


def _is_test(path: str) -> bool:
    b = path.rsplit("/", 1)[-1]
    return "/tests/" in f"/{path}" or b.startswith("test_") or b.endswith("_test.py") or b == "conftest.py"


def _imports(rec, *mods) -> bool:
    return any(i["module"] == m or i["module"].startswith(m + ".") for i in rec["imports"] for m in mods)


class Detector:
    def __init__(self, idx: dict, root: Path):
        self.idx, self.root = idx, Path(root)
        self.recs = {p: r for p, r in idx["files"].items() if r.get("lang") == "python"}
        self.src = [p for p in self.recs if not _is_test(p)]
        self.inbound = Counter()
        sym = {s["id"]: s for s in idx["symbols"]}
        for e in idx["edges"]:
            d = sym.get(e["dst"])
            if d and e["type"] == "calls":
                self.inbound[d["file"]] += 1

    # ---- helpers ----
    def golden(self, scores: dict[str, tuple[int, int | None]], why: str, tests_ok=False) -> dict | None:
        """scores: {file: (signal_count, line)} -> best file (many signals, used by others, small)."""
        best, best_s = None, -1e9
        for f, (n, line) in scores.items():
            if _is_test(f) and not tests_ok:
                continue
            size = self.recs[f].get("lines", 100)
            sc = 3 * n + min(self.inbound[f], 10) - size / 200
            if sc > best_s:
                best, best_s = f, sc
        if best is None:
            return None
        line = scores[best][1] or 1
        return {"file": best, "line": line, "why": why, "excerpt": self.excerpt(best, line)}

    def excerpt(self, path: str, line: int, n: int = 6) -> str:
        try:
            lines = (self.root / path).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        start = max(0, line - 2)
        return "\n".join(lines[start:start + n]).rstrip()

    # ---- categories ----
    def logging(self):
        styles, scores = Counter(), {}
        for p in self.src:
            r = self.recs[p]
            std = [c for c in r["calls"] if c["name"] == "getLogger"]
            use = [c for c in r["calls"] if c["name"] in LOG_METHODS and (c["recv"] or "").lower().endswith(("log", "logger", "logging"))]
            prints = [c for c in r["calls"] if c["name"] == "print" and c["recv"] is None]
            if std or use:
                styles["stdlib `logging.getLogger(__name__)`"] += 1
                scores[p] = (len(use) + len(std), (std[0]["line"] if std else use[0]["line"]))
            if _imports(r, "structlog"):
                styles["structlog"] += 1
            if _imports(r, "loguru"):
                styles["loguru"] += 1
            if prints:
                styles["`print` (avoid in library code)"] += 1
        return styles, self.golden(scores, "logger set up once per module, used with levels")

    def config(self):
        styles, scores = Counter(), {}
        for p in self.src:
            r = self.recs[p]
            hit = None
            if any(c["name"] == "getenv" or (c["recv"] or "").endswith("environ") for c in r["calls"]):
                hit = ("env", [c for c in r["calls"] if c["name"] == "getenv" or (c["recv"] or "").endswith("environ")])
            elif any(c["name"] in ("safe_load", "load") and (c["recv"] or "") == "yaml" for c in r["calls"]):
                hit = ("yaml", [c for c in r["calls"] if (c["recv"] or "") == "yaml"])
            elif any(c["name"] == "load" and (c["recv"] or "") == "json" for c in r["calls"]):
                hit = ("json", [c for c in r["calls"] if (c["recv"] or "") == "json" and c["name"] == "load"])
            elif _imports(r, "pydantic_settings") or any(i["name"] == "BaseSettings" for i in r["imports"]):
                hit = ("pydantic", [{"line": 1}])
            elif _imports(r, "configparser"):
                hit = ("configparser", [{"line": 1}])
            if hit:
                label = next(l for l, k in CONFIG_STYLES if k == hit[0])
                styles[label] += 1
                scores[p] = (len(hit[1]), hit[1][0]["line"])
        return styles, self.golden(scores, "how configuration is read")

    def errors(self):
        styles = Counter()
        classes = [s for s in self.idx["symbols"] if s["kind"] == "class" and re.search(r"\((?:[\w.]*(?:Exception|Error))", s["sig"])
                   and not _is_test(s["file"])]
        raises, excepts, bare = Counter(), Counter(), 0
        for p in self.src:
            f = self.recs[p].get("features", {})
            raises.update(f.get("raises", {})); excepts.update(f.get("except", {})); bare += f.get("bare_except", 0)
        if classes:
            names = [c["name"] for c in classes]
            used = sorted(names, key=lambda n: -raises[n])
            styles[f"custom exceptions: {', '.join(f'`{n}`' for n in used[:5])}"] += len(classes)
        common = [(n, c) for n, c in excepts.most_common(3) if n not in ("Exception", "BaseException")]
        if common:
            styles["caught: " + ", ".join(f"`{n}` x{c}" for n, c in common)] += 1
        if bare:
            styles[f"bare `except:` x{bare} (avoid)"] += 1
        scores = {}
        if classes:
            for c in classes:
                n = sum(raises[x["name"]] for x in classes if x["file"] == c["file"])
                scores[c["file"]] = (n + 1, c["line"])
        else:
            for p in self.src:
                n = sum(self.recs[p].get("features", {}).get("except", {}).values())
                if n:
                    scores[p] = (n, 1)
        return styles, self.golden(scores, "how errors are defined and raised")

    def spark(self):
        styles, scores = Counter(), {}
        for p in self.src:
            calls = [c for c in self.recs[p]["calls"] if c["name"] == "getOrCreate"]
            if calls:
                styles["`SparkSession.builder...getOrCreate()`"] += 1
                scores[p] = (len(calls), calls[0]["line"])
                where = next((c["caller"] for c in calls), "")
                if where and where != "<module>":
                    styles[f"created inside function `{where}`, shared by callers"] += 1
        return styles, self.golden(scores, "the one place a Spark session is created")

    def db(self):
        styles, scores = Counter(), {}
        for p in self.src:
            r = self.recs[p]
            for mod, label in DB_LIBS.items():
                if _imports(r, mod):
                    styles[label] += 1
                    sig = [c for c in r["calls"] if c["name"] in ("connect", "create_engine", "Client")]
                    scores[p] = (len(sig) + 1, sig[0]["line"] if sig else 1)
        return styles, self.golden(scores, "how a database connection is opened")

    def retries(self):
        styles, scores = Counter(), {}
        syms = [s for s in self.idx["symbols"] if s["kind"] == "function" and re.search(r"retry|backoff", s["name"], re.I)
                and not _is_test(s["file"])]
        for s in syms:
            n = sum(1 for e in self.idx["edges"] if e["dst"] == s["id"] and e["type"] == "calls")
            styles[f"helper `{s['file']}:{s['name']}` (called from {n} places)"] += 1
            scores[s["file"]] = (n + 1, s["line"])
        for p in self.src:
            if _imports(self.recs[p], "tenacity", "backoff"):
                styles["tenacity/backoff decorators"] += 1
                scores.setdefault(p, (1, 1))
        return styles, self.golden(scores, "retry logic lives here; reuse, do not rewrite")

    def tests(self):
        styles, scores = Counter(), {}
        tfiles = [p for p in self.recs if _is_test(p) and not p.endswith("conftest.py")]
        for p in tfiles:
            r = self.recs[p]
            if _imports(r, "pytest"):
                styles["pytest"] += 1
            elif _imports(r, "unittest"):
                styles["unittest"] += 1
        fixtures = sum(v for p in self.recs for k, v in self.recs[p].get("features", {}).get("decorators", {}).items()
                       if k in ("pytest.fixture", "fixture"))
        if fixtures:
            styles[f"{fixtures} pytest fixtures (conftest.py: {sum(1 for p in self.recs if p.endswith('conftest.py'))})"] += 1
        tests_per = Counter(s["file"] for s in self.idx["symbols"] if s["is_test"])
        for p in tfiles:
            uses_fix = any(k in ("pytest.fixture", "fixture") for k in self.recs[p].get("features", {}).get("decorators", {}))
            scores[p] = (min(tests_per[p], 5) + (3 if uses_fix else 0) + (2 if _imports(self.recs[p], "pytest") else 0), 1)
        g = self.golden(scores, "how tests are written here", tests_ok=True)
        if g:
            first = next((s for s in self.idx["symbols"] if s["is_test"] and s["file"] == g["file"]), None)
            if first:
                g["line"] = first["line"]
                g["excerpt"] = self.excerpt(g["file"], first["line"], 8)
        return styles, g


def detect(idx: dict, root: str | Path) -> dict:
    d = Detector(idx, Path(root))
    out = {}
    for name, fn in (("Logging", d.logging), ("Configuration", d.config), ("Errors", d.errors), ("Spark session", d.spark),
                     ("Database access", d.db), ("Retries", d.retries), ("Tests", d.tests)):
        styles, golden = fn()
        if styles:
            out[name] = {"styles": styles.most_common(), "golden": golden}
    return out


def render_conventions_md(conv: dict) -> str:
    lines = ["# Conventions", "How this repo already does things. Follow these; do not introduce a second way. "
             "Copy the golden example's shape.", ""]
    for name, c in conv.items():
        lines.append(f"## {name}")
        for label, n in c["styles"]:
            lines.append(f"- {label}" + (f" ({n} files)" if n > 1 and not label.startswith(("helper", "custom", "caught", "bare")) else ""))
        g = c["golden"]
        if g:
            lines.append(f"- Golden example: `{g['file']}:{g['line']}` ({g['why']})")
            if g["excerpt"]:
                lines += ["```", g["excerpt"], "```"]
        lines.append("")
    return "\n".join(lines).rstrip()


def render_helpers_md(idx: dict, root: str | Path, limit: int = 40) -> str:
    from devmem.index import Index
    ix = Index(idx, Path(root))
    rows = ix.helpers(limit)
    lines = ["# Helpers", "Existing utilities. Reuse these before writing new code (`devmem show <name>` for the body).", ""]
    for s, n in rows:
        lines.append(f"- `{s['file']}:{s['line']}` `{s['sig'][4:]}`" + (f" — {s['doc']}" if s["doc"] else "")
                     + (f" [used in {n} files]" if n else ""))
    if not rows:
        lines.append("- none found yet")
    return "\n".join(lines)
