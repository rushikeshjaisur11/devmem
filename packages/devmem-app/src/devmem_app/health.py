"""Code health: one ranked list out of devmem's own checks (ruff, vulture, radon, bandit, deptry, sqlfluff, eslint, knip)
and SonarQube's findings, when connected.

What neither a linter nor Sonar knows is how much depends on the code a finding sits in. Each finding is placed in its
enclosing function, and its priority is its severity raised by how many places call that function and by whether any
test reaches it. So "fix first" starts with the serious problem in widely used, untested code, not with the 400th
style nit."""
from __future__ import annotations

import bisect
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

from devmem.index import Index
from devmem.review import gaps as review_gaps

from . import hygiene as hyg
from . import sonar

SEV_ORDER = ("high", "medium", "low")
SEV_WEIGHT = {"high": 9.0, "medium": 3.0, "low": 1.0}
CATEGORIES = {"bug": "Bugs", "security": "Security", "maintainability": "Maintainability", "complexity": "Complexity", "dead-code": "Dead code",
              "style": "Lint and style", "dependency": "Dependencies"}
TOOL_CATEGORY = {"ruff": "style", "eslint": "style", "sqlfluff": "style", "vulture": "dead-code", "knip": "dead-code", "radon": "complexity",
                 "deptry": "dependency", "bandit": "security"}
# ruff/pyflakes rules that are defects, not style: undefined names, syntax errors, redefinitions that hide code
RUFF_BUGS = ("F821", "F822", "F823", "F811", "F601", "F602", "F631", "F632", "F701", "F702", "F704", "F706", "F707", "E9", "B006", "B008", "PLE")


def _local_severity(i: dict) -> tuple[str, str]:
    """(severity, category) for a finding from the built-in tools, which do not all report one."""
    t, code = i["tool"], str(i.get("code", ""))
    if t == "bandit":
        return {"HIGH": "high", "MEDIUM": "medium"}.get(str(i.get("severity", "")).upper(), "medium"), "security"
    if t == "radon":
        cc = i.get("complexity", 0)
        return ("high" if cc > 30 else "medium" if cc > 20 else "low"), "complexity"
    if t == "ruff" and code.startswith(RUFF_BUGS):
        return "medium", "bug"
    if t == "ruff" and code.startswith("S"):
        return "medium", "security"
    if t == "eslint":
        return ("medium" if i.get("severity") == "error" else "low"), "style"
    if t == "deptry":
        return "medium", "dependency"
    if t == "knip" and ("dependency" in code or code == "unresolved-import"):
        return "medium", "dependency"
    return "low", TOOL_CATEGORY.get(t, "style")


class _Locator:
    """file + line -> the innermost function, method or class containing it."""
    def __init__(self, ix: Index):
        self.by_file: dict[str, tuple[list[int], list[dict]]] = {}
        for f, syms in ix.by_file.items():
            ordered = sorted(syms, key=lambda s: s["line"])
            self.by_file[f] = ([s["line"] for s in ordered], ordered)

    def at(self, path: str, line: int) -> dict | None:
        hit = self.by_file.get(path)
        if not hit:
            return None
        best = None
        for s in hit[1][:bisect.bisect_right(hit[0], line)]:
            if s.get("end_line", s["line"]) >= line and (best is None or s["line"] >= best["line"]):
                best = s
        return best


def _tested(ix: Index) -> set[str]:
    """Symbols some test reaches directly or through one caller (the same reach impact uses)."""
    out = set()
    for sid, s in ix.symbols.items():
        if s.get("is_test"):
            for e in ix.out.get(sid, []):
                out.add(e["dst"])
                for e2 in ix.out.get(e["dst"], []):
                    if e2["type"] == "calls":
                        out.add(e2["dst"])
    return out


def build(ix: Index, name: str) -> dict:
    root = Path(ix.root)
    try:
        local = json.loads((root / hyg.HYGIENE_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        local = None
    sq = sonar.load(root)
    if not local and not sq:
        return {"available": False, "sonar": None, "local": None}

    loc, tested = _Locator(ix), _tested(ix)
    findings = []
    for i in (local or {}).get("issues", []):
        sev, cat = _local_severity(i)
        findings.append({"source": i["tool"], "code": str(i.get("code", "")), "msg": i.get("msg", ""), "path": i.get("path", ""), "line": i.get("line") or 1,
                         "severity": sev, "category": cat})
    for i in (sq or {}).get("issues", []) + (sq or {}).get("hotspots", []):
        findings.append({"source": "sonar", "code": i["code"], "msg": i["msg"] + (" (security hotspot: review needed)" if i.get("hotspot") else ""),
                         "path": i["path"], "line": i["line"], "severity": i["severity"], "category": i["category"],
                         "url": sonar.issue_url(sq, i), "effort": i.get("effort", ""), "sonar_severity": i.get("sonar_severity", "")})

    cov = (sq or {}).get("file_coverage") or {}
    per_file: dict[str, dict] = defaultdict(lambda: {"high": 0, "medium": 0, "low": 0, "score": 0.0})
    for f in findings:
        s = loc.at(f["path"], f["line"])
        callers = 0
        if s:
            callers = len({e["src"] for e in ix.inc.get(s["id"], []) if e["type"] == "calls"})
            f["symbol"], f["symbol_id"] = s["qualname"], s["id"]
            # "no test" only where the question makes sense: real functions in files devmem indexed as code
            f["untested"] = s["kind"] in ("function", "method") and not s.get("is_test") and s["id"] not in tested
        f["callers"] = callers
        f["score"] = round(SEV_WEIGHT[f["severity"]] * (1 + math.log2(1 + callers)) * (1.5 if f.get("untested") and callers else 1.0), 2)
        pf = per_file[f["path"]]
        pf[f["severity"]] += 1
        pf["score"] += f["score"]
    findings.sort(key=lambda f: (-f["score"], SEV_ORDER.index(f["severity"]), f["path"], f["line"]))

    files = []
    for path, pf in per_file.items():
        syms = ix.by_file.get(path, [])
        used_by = len({e["src"].split("#")[0] for s in syms for e in ix.inc.get(s["id"], []) if e["type"] == "calls"} - {path})
        files.append({"path": path, "high": pf["high"], "medium": pf["medium"], "low": pf["low"], "total": pf["high"] + pf["medium"] + pf["low"],
                      "score": round(pf["score"], 1), "used_by": used_by, "coverage": cov.get(path)})
    files.sort(key=lambda f: (-f["score"], f["path"]))

    sev = Counter(f["severity"] for f in findings)
    cats = Counter(f["category"] for f in findings)
    srcs = Counter(f["source"] for f in findings)
    trend = []
    hist = hyg.HISTORY_DIR / f"{name}.jsonl"
    if hist.exists():
        for line in hist.read_text().splitlines():
            if line.strip():
                t = json.loads(line)
                trend.append({"ts": t["ts"], "total": sum(v for k, v in t["counts"].items() if k != "clones")})
    return {
        "available": True,
        "summary": {"total": len(findings), **{k: sev.get(k, 0) for k in SEV_ORDER},
                    "risky": sum(1 for f in findings if f["severity"] != "low" and f["callers"] >= 2 and f.get("untested"))},
        "categories": [{"id": k, "label": v, "count": cats.get(k, 0)} for k, v in CATEGORIES.items() if cats.get(k)],
        "sources": [{"id": k, "count": n} for k, n in srcs.most_common()],
        "findings": findings[:1500], "shown": min(len(findings), 1500),
        "files": files[:60],
        "local": None if not local else {"ts": local["ts"], "tools": local["tools"], "counts": local["counts"], "prev_counts": local.get("prev_counts", {})},
        "sonar": None if not sq else {"ts": sq["ts"], "url": sq["url"], "project": sq["project"], "branch": sq.get("branch", ""), "version": sq.get("version", ""),
                                      "measures": sq.get("measures", {}), "gate": sq.get("gate"), "truncated": sq.get("truncated", False),
                                      "total_issues": sq.get("total_issues", 0), "matched": sq.get("matched"), "notes": sq.get("notes", []),
                                      "issues": len(sq.get("issues", [])), "hotspots": len(sq.get("hotspots", [])),
                                      "dashboard": f"{sq['url']}/dashboard?id={sq['project']}"},
        "clones": (local or {}).get("clones", []),
        "gaps": review_gaps(ix, 10),
        "trend": trend,
    }
