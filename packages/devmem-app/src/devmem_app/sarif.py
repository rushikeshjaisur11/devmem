"""SARIF import: findings from any scanner that writes SARIF 2.1.0 (Checkmarx: `cx scan create --report-format sarif` or the
CxSAST / CxFlow SARIF report; also CodeQL, Semgrep, Snyk Code, Trivy, ESLint's formatter ...), so Code health ranks them
with everything else and agents see them before editing a file.

`.devmem/sarif.json` keeps one entry per tool: importing a newer report from the same tool replaces that tool's findings
and leaves the others. Each rule's own description (shortDescription / fullDescription / help) is kept and shown next to
its findings. A report the repo's CI leaves at the repo root (`*.sarif`, `*.sarif.json`) is picked up on every re-index
when it is newer than the last import."""
from __future__ import annotations

import datetime as dt
import json
import re
import urllib.parse
from pathlib import Path

from devmem.schema import atomic_write

from .sonar import _repo_path

PATH = ".devmem/sarif.json"
MAX_PER_TOOL = 20_000
LEVEL = {"error": "high", "warning": "medium", "note": "low", "none": "low"}
WORD_SEVERITY = {"critical": "high", "high": "high", "medium": "medium", "moderate": "medium", "low": "low", "info": "low", "information": "low", "note": "low"}
SECURITY_TOOLS = ("checkmarx", "cxsast", "codeql", "semgrep", "snyk", "trivy", "bandit", "gitleaks", "fortify", "veracode", "kics", "grype", "brakeman", "gosec")


class SarifError(ValueError):
    pass


def tool_id(name: str) -> str:
    """'Checkmarx AST CLI' -> 'checkmarx', 'CodeQL' -> 'codeql': the source name shown and filtered on in Code health."""
    words = re.findall(r"[A-Za-z0-9]+", name or "")
    return (words[0].lower() if words else "sarif")[:30]


def _txt(m) -> str:
    if isinstance(m, dict):
        return str(m.get("text") or m.get("markdown") or "").strip()
    return str(m or "").strip()


def _sev_of(result: dict, rule: dict) -> str:
    for props in (result.get("properties") or {}, rule.get("properties") or {}):
        ss = props.get("security-severity")
        try:
            if ss is not None:
                v = float(ss)
                return "high" if v >= 7 else "medium" if v >= 4 else "low"
        except (TypeError, ValueError):
            pass
        for k in ("severity", "Severity", "problem.severity", "cx-severity"):
            if str(props.get(k, "")).lower() in WORD_SEVERITY:
                return WORD_SEVERITY[str(props[k]).lower()]
    level = result.get("level") or ((rule.get("defaultConfiguration") or {}).get("level")) or "warning"
    return LEVEL.get(str(level).lower(), "medium")


def _path_of(loc: dict, bases: dict) -> tuple[str, int]:
    pl = (loc or {}).get("physicalLocation") or {}
    art = pl.get("artifactLocation") or {}
    uri = urllib.parse.unquote(str(art.get("uri") or ""))
    base = bases.get(art.get("uriBaseId") or "", "")
    if base and not re.match(r"^[a-z]+:", uri):
        uri = base.rstrip("/") + "/" + uri
    uri = re.sub(r"^file:/*", "/", uri)
    uri = re.sub(r"^/([A-Za-z]:/)", r"\1", uri).replace("\\", "/")      # file:///C:/x -> C:/x
    line = int(((pl.get("region") or {}).get("startLine")) or 1)
    return uri, max(line, 1)


def parse(data: bytes | str | dict, files: set[str]) -> list[dict]:
    """Every run in a SARIF log -> [{tool, name, version, issues, rules}]. Paths are matched to the repo's files when they
    are absolute or carry a CI checkout prefix."""
    try:
        log = data if isinstance(data, dict) else json.loads(data.decode("utf-8-sig") if isinstance(data, bytes) else data)
    except (ValueError, UnicodeDecodeError) as e:
        raise SarifError(f"not a SARIF file: {e}") from None
    if not isinstance(log, dict) or not isinstance(log.get("runs"), list):
        raise SarifError("not a SARIF file: there is no `runs` list (export the report as SARIF 2.1.0)")
    out = []
    for run in log["runs"]:
        driver = ((run.get("tool") or {}).get("driver")) or {}
        name = driver.get("fullName") or driver.get("name") or "SARIF"
        tid = tool_id(driver.get("name") or name)
        rules = list(driver.get("rules") or []) + [r for ext in ((run.get("tool") or {}).get("extensions") or []) for r in (ext.get("rules") or [])]
        by_id = {r.get("id"): r for r in rules if r.get("id")}
        bases = {k: urllib.parse.unquote(str((v or {}).get("uri") or "")).replace("file://", "") for k, v in (run.get("originalUriBaseIds") or {}).items()}
        security = tid in SECURITY_TOOLS or any(t in name.lower() for t in SECURITY_TOOLS)
        issues, about = [], {}
        for res in run.get("results") or []:
            if len(issues) >= MAX_PER_TOOL:
                break
            if (res.get("suppressions") or []) and any(s.get("status", "accepted") == "accepted" for s in res["suppressions"]):
                continue                                               # suppressed in the tool: not shown again
            if str(res.get("baselineState", "")).lower() == "absent":
                continue                                               # fixed since the baseline
            rid = res.get("ruleId") or ((res.get("rule") or {}).get("id"))
            if rid is None and isinstance(res.get("ruleIndex"), int) and 0 <= res["ruleIndex"] < len(rules):
                rid = rules[res["ruleIndex"]].get("id")
            rule = by_id.get(rid) or {}
            msg = _txt(res.get("message"))
            if not msg and (res.get("message") or {}).get("id"):
                msg = _txt((rule.get("messageStrings") or {}).get(res["message"]["id"]))
            msg = msg or _txt(rule.get("shortDescription")) or str(rid or "finding")
            locs = res.get("locations") or []
            raw, line = _path_of(locs[0], bases) if locs else ("", 1)
            tags = [str(t).lower() for t in ((rule.get("properties") or {}).get("tags") or [])]
            cat = "security" if security or "security" in tags or any(t.startswith(("cwe", "external/cwe")) for t in tags) \
                or (rule.get("properties") or {}).get("security-severity") is not None else "bug" if res.get("level") == "error" else "maintainability"
            f = {"tool": tid, "code": str(rid or "finding"), "msg": re.sub(r"\s+", " ", msg)[:400], "path": _repo_path(raw.lstrip("/") if raw.startswith("/") else raw, files),
                 "line": line, "severity": _sev_of(res, rule), "category": cat}
            if f["path"] not in files and raw.startswith("/"):
                f["path"] = _repo_path(raw, files)
            if rule.get("helpUri"):
                f["url"] = rule["helpUri"]
            issues.append(f)
            if rid and rid not in about:
                what = _txt(rule.get("fullDescription")) or _txt(rule.get("shortDescription"))
                fix = _txt(rule.get("help"))
                if what or fix:
                    about[str(rid)] = {"what": re.sub(r"\s+", " ", what or fix)[:700], "fix": re.sub(r"\s+", " ", fix)[:600] if fix and fix != what else ""}
        out.append({"tool": tid, "name": name, "version": driver.get("semanticVersion") or driver.get("version") or "", "issues": issues, "rules": about})
    return out


def load(root: Path) -> dict | None:
    try:
        d = json.loads((Path(root) / PATH).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def save_runs(root: Path, runs: list[dict], source: str) -> dict:
    """Store parsed runs, replacing earlier imports from the same tools. Returns the stored document."""
    d = load(root) or {"tools": {}}
    ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    merged: dict[str, dict] = {}
    for r in runs:                       # one report may hold several runs of the same tool (one per language)
        m = merged.setdefault(r["tool"], {"name": r["name"], "version": r["version"], "issues": [], "rules": {}})
        m["issues"] += r["issues"]
        m["rules"].update(r["rules"])
    for tid, m in merged.items():
        d["tools"][tid] = {**m, "ts": ts, "file": source}
    d["issues"] = [i for t in d["tools"].values() for i in t["issues"]]        # flat list: what agents' readers expect
    p = Path(root) / PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(p, json.dumps(d, indent=1))
    return d


def import_file(root: Path, data: bytes, files: set[str], source: str) -> dict:
    runs = parse(data, files)
    if not runs:
        raise SarifError("the SARIF file has no runs")
    d = save_runs(root, runs, source)
    found = [r["tool"] for r in runs]
    n = sum(len(r["issues"]) for r in runs)
    paths = [i["path"] for r in runs for i in r["issues"] if i["path"]]
    matched = sum(1 for x in paths if x in files) if files else len(paths)
    return {"tools": sorted(set(found)), "names": sorted({r["name"] for r in runs}), "issues": n, "matched": matched,
            "notes": ["none of the paths in the report match files in this repo: was it produced from this repo?"] if paths and files and not matched else [],
            "all_tools": summary(d)}


def remove(root: Path, tool: str) -> dict:
    d = load(root) or {"tools": {}}
    d["tools"].pop(tool, None)
    d["issues"] = [i for t in d["tools"].values() for i in t["issues"]]
    atomic_write(Path(root) / PATH, json.dumps(d, indent=1))
    return d


def summary(d: dict | None) -> list[dict]:
    return [{"tool": k, "name": t["name"], "version": t.get("version", ""), "ts": t["ts"], "file": t.get("file", ""), "issues": len(t["issues"])}
            for k, t in sorted(((d or {}).get("tools") or {}).items())]


def auto_import(root: Path, files: set[str]) -> list[str]:
    """Import SARIF reports left at the repo root (by CI or a local scan) that are newer than the last import of them."""
    root = Path(root)
    done = []
    d = load(root) or {"tools": {}}
    last = {t.get("file"): t.get("ts", "") for t in d.get("tools", {}).values()}
    for f in sorted(list(root.glob("*.sarif")) + list(root.glob("*.sarif.json"))):
        try:
            mt = dt.datetime.fromtimestamp(f.stat().st_mtime, dt.timezone.utc).isoformat(timespec="seconds")
            if last.get(f.name, "") >= mt or f.stat().st_size > 200 * 1024 * 1024:
                continue
            import_file(root, f.read_bytes(), files, f.name)
            done.append(f.name)
        except (OSError, SarifError):
            continue
    return done
