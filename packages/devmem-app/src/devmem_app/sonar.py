"""SonarQube Server integration: pull a project's open issues, security hotspots, quality gate, measures and per-file
coverage, and store them next to devmem's own checks so Code health can rank them by blast radius and agents see them
before touching a file.

Read-only against Sonar's Web API (also works with SonarQube Cloud by giving its URL; an organization is not needed for
the calls used here). The connection (URL and token) is in ~/.devmem/settings.json and never in a repo; what is written
into the repo is `.devmem/sonar.json`: findings with repo-relative paths. Parameter names differ a little between
Sonar versions, so each call falls back to the older spelling when the newer one is refused."""
from __future__ import annotations

import base64
import datetime as dt
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .paths import devmem_home

PATH = ".devmem/sonar.json"
MAX_ISSUES = 10_000          # Sonar never returns more than 10,000 results for one search
PAGE = 500
METRICS = ["alert_status", "bugs", "vulnerabilities", "code_smells", "security_hotspots", "coverage", "duplicated_lines_density", "ncloc",
           "sqale_index", "reliability_rating", "security_rating", "sqale_rating"]
SEVERITY = {"BLOCKER": "high", "CRITICAL": "high", "MAJOR": "medium", "MINOR": "low", "INFO": "low",
            "HIGH": "high", "MEDIUM": "medium", "LOW": "low"}     # the second row: impact severities and hotspot probabilities
CATEGORY = {"BUG": "bug", "VULNERABILITY": "security", "CODE_SMELL": "maintainability", "SECURITY_HOTSPOT": "security",
            "RELIABILITY": "bug", "SECURITY": "security", "MAINTAINABILITY": "maintainability"}


class SonarError(RuntimeError):
    pass


# ---------- settings ----------
def _settings_path() -> Path:
    return devmem_home() / "settings.json"


def _all() -> dict:
    try:
        d = json.loads(_settings_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def stored() -> dict:
    s = _all().get("sonar") or {}
    s.setdefault("projects", {})
    return s


def _write(s: dict) -> None:
    data = _all()
    data["sonar"] = s
    p = _settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1), encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def effective() -> dict:
    """Stored connection overlaid with the environment (DEVMEM_SONAR_URL / DEVMEM_SONAR_TOKEN, or Sonar's own
    SONAR_HOST_URL / SONAR_TOKEN)."""
    s = stored()
    env = os.environ.get
    url = env("DEVMEM_SONAR_URL") or env("SONAR_HOST_URL")
    tok = env("DEVMEM_SONAR_TOKEN") or env("SONAR_TOKEN")
    s["from_environment"] = [k for k, v in (("url", url), ("token", tok)) if v]
    if url:
        s["url"] = url
    if tok:
        s["token"] = tok
    s["url"] = (s.get("url") or "").strip().rstrip("/")
    return s


def save_connection(url: str | None = None, token: str | None = None, clear_token: bool = False) -> None:
    s = stored()
    if url is not None:
        url = url.strip().rstrip("/")
        if url and not re.match(r"^https?://[^\s/]+", url):
            raise SonarError("the server address must start with http:// or https://")
        s["url"] = url
    if clear_token:
        s.pop("token", None)
    elif token:
        s["token"] = token.strip()
    _write(s)


def set_project(repo: str, key: str, branch: str = "") -> None:
    s = stored()
    key, branch = key.strip(), branch.strip()
    if key:
        s["projects"][repo] = {"key": key, **({"branch": branch} if branch else {})}
    else:
        s["projects"].pop(repo, None)
    _write(s)


def detect_key(root: Path) -> str:
    """The project key the repo itself declares, when it has a sonar-project.properties or a Maven/Gradle property."""
    for rel, pat in (("sonar-project.properties", r"^\s*sonar\.projectKey\s*=\s*(\S+)"), ("pom.xml", r"<sonar\.projectKey>\s*([^<\s]+)"),
                     ("build.gradle", r"sonar\.projectKey[\"']?\s*[,=]\s*[\"']([^\"']+)"), ("build.gradle.kts", r"sonar\.projectKey\"?\s*,\s*\"([^\"]+)")):
        try:
            m = re.search(pat, (Path(root) / rel).read_text(encoding="utf-8", errors="replace"), re.M)
        except OSError:
            continue
        if m:
            return m.group(1)
    return ""


def public(repos: dict[str, str]) -> dict:
    """What the browser may see: the connection without the token, and each repo's project mapping."""
    s = effective()
    tok = s.get("token") or ""
    rows = []
    for name, path in sorted(repos.items()):
        m = s["projects"].get(name) or {}
        last = load(Path(path))
        rows.append({"repo": name, "key": m.get("key", ""), "branch": m.get("branch", ""), "detected": detect_key(Path(path)),
                     "synced": last.get("ts") if last else None, "issues": len(last.get("issues", [])) if last else None})
    return {"url": s["url"], "has_token": bool(tok), "token_hint": f"…{tok[-4:]}" if len(tok) >= 12 else "",
            "configured": bool(s["url"]), "from_environment": s["from_environment"], "projects": rows}


# ---------- HTTP ----------
class Client:
    def __init__(self, url: str, token: str = "", timeout: int = 60):
        self.url, self.token, self.timeout = url.rstrip("/"), token or "", timeout
        self.scheme = "bearer"   # current servers; older ones take the token as a Basic username, tried on the first 401

    def _auth(self) -> dict:
        if not self.token:
            return {}
        if self.scheme == "bearer":
            return {"Authorization": f"Bearer {self.token}"}
        return {"Authorization": "Basic " + base64.b64encode(f"{self.token}:".encode()).decode()}

    def get(self, path: str, **params) -> dict:
        q = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
        for attempt in (0, 1):
            req = urllib.request.Request(f"{self.url}{path}{'?' + q if q else ''}", headers={"Accept": "application/json", "User-Agent": "devmem", **self._auth()})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    body = r.read().decode("utf-8", "replace")
                    try:
                        return json.loads(body) if body.strip() else {}
                    except ValueError:
                        raise SonarError(f"{self.url} did not answer with JSON: is this the SonarQube address (without /dashboard…)?") from None
            except urllib.error.HTTPError as e:
                if e.code == 401 and self.token and attempt == 0 and self.scheme == "bearer":
                    self.scheme = "basic"
                    continue
                detail = e.read().decode("utf-8", "replace")[:400]
                try:
                    detail = "; ".join(x.get("msg", "") for x in json.loads(detail).get("errors", [])) or detail
                except (ValueError, AttributeError):
                    pass
                if e.code == 401:
                    raise SonarError("SonarQube refused the token (401). Create a user token under My Account → Security and save it here.") from None
                if e.code == 403:
                    raise SonarError(f"the token may not read this ({path}): it needs Browse permission on the project. {detail}") from None
                err = SonarError(f"SonarQube answered HTTP {e.code} for {path}: {detail}")
                err.code, err.detail = e.code, detail
                raise err from None
            except (urllib.error.URLError, OSError) as e:
                raise SonarError(f"cannot reach {self.url}: {getattr(e, 'reason', e)}") from None
        raise SonarError("unreachable")

    def either(self, path: str, variants: list[dict], **params) -> dict:
        """Try each spelling of a parameter in turn (newer first); a 400/404 moves on to the next."""
        last: SonarError | None = None
        for v in variants:
            try:
                return self.get(path, **params, **v)
            except SonarError as e:
                if getattr(e, "code", 0) not in (400, 404):
                    raise
                last = e
        raise last or SonarError("no variant worked")


def test(url: str, token: str) -> dict:
    """Is the server there, which version, and does the token work?"""
    if not url:
        raise SonarError("enter the SonarQube server address first")
    c = Client(url, token, timeout=20)
    st = c.get("/api/system/status")
    valid = c.get("/api/authentication/validate").get("valid", False) if token else None
    if token and not valid:
        raise SonarError("SonarQube refused the token. Create a user token under My Account → Security and save it here.")
    return {"ok": True, "version": st.get("version", ""), "status": st.get("status", ""), "token_valid": valid}


def search_projects(q: str = "", limit: int = 20) -> list[dict]:
    s = effective()
    if not s["url"]:
        raise SonarError("connect SonarQube first")
    r = Client(s["url"], s.get("token", ""), 20).get("/api/components/search", qualifiers="TRK", q=q if len(q) >= 2 else None, ps=limit)
    return [{"key": c["key"], "name": c.get("name", c["key"])} for c in r.get("components", [])]


# ---------- sync ----------
def _path_of(component: str, paths: dict[str, str], project: str) -> str:
    return paths.get(component) or (component[len(project) + 1:] if component.startswith(project + ":") else component.split(":", 1)[-1])


def _repo_path(p: str, files: set[str]) -> str:
    """Sonar paths are relative to where the scanner ran; the repo may be a sub-folder of that, or the other way round."""
    if p in files or not files:
        return p
    parts = p.split("/")
    for i in range(1, len(parts)):
        if "/".join(parts[i:]) in files:
            return "/".join(parts[i:])
    tail = "/" + p
    hit = [f for f in files if f.endswith(tail)]
    return hit[0] if len(hit) == 1 else p


def fetch(url: str, token: str, project: str, branch: str = "", files: set[str] | None = None) -> dict:
    c = Client(url, token)
    files = files or set()
    ver = c.get("/api/system/status").get("version", "")
    out: dict = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "url": url, "project": project, "branch": branch,
                 "version": ver, "issues": [], "hotspots": [], "truncated": False, "notes": []}

    page, total = 1, None
    while (page - 1) * PAGE < MAX_ISSUES:
        r = c.either("/api/issues/search", [{"components": project}, {"componentKeys": project}], resolved="false", ps=PAGE, p=page,
                     s="SEVERITY", asc="false", branch=branch)
        total = (r.get("paging") or {}).get("total", r.get("total", 0))
        paths = {x["key"]: x["path"] for x in r.get("components", []) if x.get("path")}
        for i in r.get("issues", []):
            imp = (i.get("impacts") or [{}])[0]
            typ = i.get("type") or imp.get("softwareQuality") or "CODE_SMELL"
            sev = i.get("severity") or imp.get("severity") or "MAJOR"
            out["issues"].append({"tool": "sonar", "key": i["key"], "code": i.get("rule", ""), "msg": i.get("message", ""),
                                  "path": _repo_path(_path_of(i.get("component", ""), paths, project), files), "line": i.get("line") or 1,
                                  "severity": SEVERITY.get(sev, "medium"), "sonar_severity": sev, "category": CATEGORY.get(typ, "maintainability"),
                                  "effort": i.get("effort", ""), "created": (i.get("creationDate") or "")[:10], "tags": i.get("tags") or []})
        if not r.get("issues") or len(out["issues"]) >= total:   # count what arrived: a server may cap the page size below ours
            break
        page += 1
    out["total_issues"] = total or 0
    out["truncated"] = (total or 0) > len(out["issues"])

    try:
        page = 1
        while page <= 10:
            r = c.either("/api/hotspots/search", [{"project": project}, {"projectKey": project}], status="TO_REVIEW", ps=PAGE, p=page, branch=branch)
            paths = {x["key"]: x["path"] for x in r.get("components", []) if x.get("path")}
            for h in r.get("hotspots", []):
                prob = h.get("vulnerabilityProbability", "MEDIUM")
                out["hotspots"].append({"tool": "sonar", "key": h["key"], "code": h.get("ruleKey", h.get("securityCategory", "hotspot")),
                                        "msg": h.get("message", ""), "path": _repo_path(_path_of(h.get("component", ""), paths, project), files),
                                        "line": h.get("line") or 1, "severity": SEVERITY.get(prob, "medium"), "sonar_severity": prob,
                                        "category": "security", "hotspot": True})
            if not r.get("hotspots") or len(out["hotspots"]) >= (r.get("paging") or {}).get("total", 0):
                break
            page += 1
    except SonarError as e:   # hotspots need their own permission; the rest is still worth having
        out["notes"].append(f"security hotspots not read: {e}")

    keys = list(METRICS)
    for _ in range(3):
        try:
            r = c.get("/api/measures/component", component=project, metricKeys=",".join(keys), branch=branch)
            out["measures"] = {m["metric"]: m.get("value", (m.get("period") or {}).get("value")) for m in (r.get("component") or {}).get("measures", [])}
            break
        except SonarError as e:   # newer servers dropped some metrics: ask again without the ones it names
            missing = set(re.findall(r"[a-z_]{3,}", getattr(e, "detail", "").split(":", 1)[-1])) & set(keys)
            if getattr(e, "code", 0) not in (400, 404) or not missing:
                out["notes"].append(f"measures not read: {e}")
                break
            keys = [k for k in keys if k not in missing]
    out.setdefault("measures", {})

    try:
        g = c.get("/api/qualitygates/project_status", projectKey=project, branch=branch).get("projectStatus") or {}
        out["gate"] = {"status": g.get("status", ""), "failed": [{"metric": x.get("metricKey", ""), "actual": x.get("actualValue", ""), "threshold": x.get("errorThreshold", "")}
                                                                  for x in g.get("conditions", []) if x.get("status") == "ERROR"]}
    except SonarError as e:
        out["notes"].append(f"quality gate not read: {e}")

    cov: dict[str, float] = {}
    try:
        page, seen = 1, 0
        while page <= 20:
            r = c.get("/api/measures/component_tree", component=project, metricKeys="coverage", qualifiers="FIL", ps=PAGE, p=page, branch=branch)
            seen += len(r.get("components", []))
            for comp in r.get("components", []):
                for m in comp.get("measures", []):
                    if m.get("metric") == "coverage" and m.get("value") is not None and comp.get("path"):
                        cov[_repo_path(comp["path"], files)] = float(m["value"])
            if not r.get("components") or seen >= (r.get("paging") or {}).get("total", 0):
                break
            page += 1
    except (SonarError, ValueError) as e:
        out["notes"].append(f"per-file coverage not read: {e}")
    out["file_coverage"] = cov
    return out


def load(root: Path) -> dict | None:
    try:
        d = json.loads((Path(root) / PATH).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def sync(repo: str, root: Path, files: set[str]) -> dict:
    """Fetch the repo's mapped project and write `.devmem/sonar.json`. Raises SonarError with what to fix."""
    s = effective()
    m = s["projects"].get(repo) or {}
    key = m.get("key") or detect_key(root)
    if not s["url"]:
        raise SonarError("SonarQube is not connected: open Integrations and enter the server address")
    if not key:
        raise SonarError(f"no SonarQube project is set for {repo}: choose its project key on the Integrations page")
    data = fetch(s["url"], s.get("token", ""), key, m.get("branch", ""), files)
    data["matched"] = sum(1 for i in data["issues"] if i["path"] in files) if files else len(data["issues"])
    if data["issues"] and files and not data["matched"]:
        data["notes"].append("none of the issue paths match files in this repo: is this the right project and branch?")
    p = Path(root) / PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return data


def issue_url(data: dict, issue: dict) -> str:
    q = urllib.parse.quote
    if issue.get("hotspot"):
        return f"{data['url']}/security_hotspots?id={q(data['project'])}&hotspots={q(issue['key'])}"
    return f"{data['url']}/project/issues?id={q(data['project'])}&open={q(issue['key'])}"
