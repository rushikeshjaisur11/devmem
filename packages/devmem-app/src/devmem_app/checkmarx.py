"""Checkmarx One integration: read the latest completed scan of a project (SAST, SCA, KICS / IaC and container results)
and store its open findings next to devmem's own checks, so Code health ranks them by blast radius and agents see them
before they touch a file.

Read-only. Authentication follows the `cx` CLI: the API key is a refresh token (a JWT) that names its own login realm in
its `aud` claim and its client in `azp` (default `ast-app`); it is exchanged at `<realm>/protocol/openid-connect/token` for a
short-lived access token. When the key carries no realm, the tenant name and the IAM address are used instead
(`https://iam.checkmarx.net/auth/realms/<tenant>`, or the region's `eu.iam...` beside `eu.ast...`).
The connection lives in ~/.devmem/settings.json (mode 0600), never in a repo; `.devmem/checkmarx.json` holds the findings
with repo-relative paths. On-premises CxSAST and anything else that can write SARIF is read through `sarif.py`."""
from __future__ import annotations

import base64
import datetime as dt
import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from devmem.schema import atomic_write

from .paths import devmem_home
from .sonar import _repo_path

PATH = ".devmem/checkmarx.json"
PAGE, MAX_RESULTS = 500, 10_000
SEVERITY = {"CRITICAL": "high", "HIGH": "high", "MEDIUM": "medium", "LOW": "low", "INFO": "low", "INFORMATION": "low"}
IGNORED_STATES = {"NOT_EXPLOITABLE"}            # triaged away in Checkmarx: not shown again here
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
# SCA: the package manager named at the start of a package identifier -> the manifests that declare such packages
MANIFESTS = {"npm": ("package.json",), "python": ("requirements.txt", "pyproject.toml", "Pipfile", "setup.py"), "pypi": ("requirements.txt", "pyproject.toml"),
             "maven": ("pom.xml", "build.gradle", "build.gradle.kts"), "nuget": ("packages.config", "Directory.Packages.props"), "go": ("go.mod",),
             "rubygems": ("Gemfile",), "ruby": ("Gemfile",), "php": ("composer.json",), "composer": ("composer.json",), "cargo": ("Cargo.toml",)}


class CheckmarxError(RuntimeError):
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
    s = dict(_all().get("checkmarx") or {})
    s.setdefault("projects", {})
    return s


def _write(s: dict) -> None:
    data = _all()
    data["checkmarx"] = s
    p = _settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(p, json.dumps(data, indent=1))
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


ENV = {"url": ("DEVMEM_CX_URL", "CX_BASE_URI"), "api_key": ("DEVMEM_CX_API_KEY", "CX_APIKEY"), "tenant": ("DEVMEM_CX_TENANT", "CX_TENANT"),
       "iam_url": ("DEVMEM_CX_IAM_URL", "CX_BASE_AUTH_URI")}      # the second names are the ones the `cx` CLI reads


def effective() -> dict:
    s = stored()
    s["from_environment"] = []
    for k, names in ENV.items():
        v = next((os.environ[n] for n in names if os.environ.get(n, "").strip()), None)
        if v:
            s[k] = v.strip()
            s["from_environment"].append(k)
    claims = _claims(s.get("api_key", ""))
    if not s.get("url") and claims.get("ast-base-url"):
        s["url"] = claims["ast-base-url"]
    s["url"] = (s.get("url") or "").strip().rstrip("/")
    return s


def save_connection(url: str | None = None, api_key: str | None = None, tenant: str | None = None, iam_url: str | None = None,
                    clear_key: bool = False) -> None:
    s = stored()
    for k, v in (("url", url), ("iam_url", iam_url)):
        if v is not None:
            v = v.strip().rstrip("/")
            if v and not re.match(r"^https?://[^\s/]+", v):
                raise CheckmarxError("addresses must start with https:// (or http://)")
            s[k] = v
    if tenant is not None:
        if tenant.strip() and not re.fullmatch(r"[\w.-]+", tenant.strip()):
            raise CheckmarxError("the tenant name has letters, digits, dots, dashes and underscores only")
        s["tenant"] = tenant.strip()
    if clear_key:
        s.pop("api_key", None)
    elif api_key:
        s["api_key"] = api_key.strip()
    _write(s)


def set_project(repo: str, project: str, branch: str = "") -> None:
    s = stored()
    project, branch = project.strip(), branch.strip()
    if project:
        s["projects"][repo] = {"project": project, **({"branch": branch} if branch else {})}
    else:
        s["projects"].pop(repo, None)
    _write(s)


def _claims(token: str) -> dict:
    """The payload of a JWT, unverified: only used to find where the key logs in. {} for anything that is not a JWT."""
    try:
        part = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError, TypeError):
        return {}


def realm_url(s: dict) -> str:
    """Where the API key is exchanged: the realm the key names itself, else IAM address + tenant."""
    aud = _claims(s.get("api_key", "")).get("aud")
    aud = aud[0] if isinstance(aud, list) and aud else aud
    if isinstance(aud, str) and re.match(r"^https?://.+/auth/realms/[^/]+/?$", aud):
        return aud.rstrip("/")
    tenant = s.get("tenant") or ""
    if not tenant:
        raise CheckmarxError("this API key does not say which tenant it belongs to: enter the tenant name (shown in the Checkmarx One address and on its login page)")
    iam = (s.get("iam_url") or "").rstrip("/")
    if not iam:
        if not s.get("url"):
            raise CheckmarxError("enter the Checkmarx One address, e.g. https://ast.checkmarx.net (or https://eu.ast.checkmarx.net)")
        host = urllib.parse.urlparse(s["url"])
        iam = f"{host.scheme}://" + re.sub(r"(^|\.)ast\.", r"\1iam.", host.netloc, count=1)
    if "/auth/realms/" in iam:
        return iam
    return f"{iam}/auth/realms/{tenant}"


def public(repos: dict[str, str]) -> dict:
    """What the browser may see: the connection without the key, and each repo's project mapping."""
    s = effective()
    key = s.get("api_key") or ""
    try:
        realm = realm_url(s) if key else ""
    except CheckmarxError:
        realm = ""
    rows = []
    for name, path in sorted(repos.items()):
        m = s["projects"].get(name) or {}
        last = load(Path(path))
        rows.append({"repo": name, "project": m.get("project", ""), "branch": m.get("branch", ""), "guess": name,
                     "synced": last.get("ts") if last else None, "issues": len(last.get("issues", [])) if last else None,
                     "scan": (last or {}).get("scan", {}).get("id", "")})
    return {"url": s["url"], "tenant": s.get("tenant", ""), "iam_url": s.get("iam_url", ""), "realm": realm, "has_key": bool(key),
            "key_hint": f"…{key[-4:]}" if len(key) >= 12 else "", "configured": bool(s["url"] and key),
            "from_environment": s["from_environment"], "projects": rows}


# ---------- HTTP ----------
class Client:
    def __init__(self, s: dict, timeout: int = 60):
        if not s.get("url"):
            raise CheckmarxError("Checkmarx One is not connected: enter its address on the Integrations page")
        if not s.get("api_key"):
            raise CheckmarxError("no Checkmarx One API key is saved: create one under your user menu → API Keys, and save it on the Integrations page")
        self.s, self.url, self.timeout = s, s["url"].rstrip("/"), timeout
        self.token, self.expires = "", 0.0

    def _login(self) -> None:
        claims = _claims(self.s["api_key"])
        form = urllib.parse.urlencode({"grant_type": "refresh_token", "client_id": claims.get("azp") or "ast-app",
                                       "refresh_token": self.s["api_key"]}).encode()
        url = realm_url(self.s) + "/protocol/openid-connect/token"
        req = urllib.request.Request(url, data=form, method="POST", headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json",
                                                                            "User-Agent": "devmem"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.loads(r.read().decode("utf-8", "replace") or "{}")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            if e.code in (400, 401):
                raise CheckmarxError(f"Checkmarx One refused the API key ({e.code}): it may be expired, revoked, or for another tenant. {body}") from None
            raise CheckmarxError(f"login at {url} failed with HTTP {e.code}: {body}") from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise CheckmarxError(f"cannot reach the Checkmarx login at {url}: {getattr(e, 'reason', e)}") from None
        if not d.get("access_token"):
            raise CheckmarxError("Checkmarx One answered the login without an access token")
        self.token, self.expires = d["access_token"], time.time() + float(d.get("expires_in") or 300) - 60

    def get(self, path: str, **params) -> dict:
        q = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")}, doseq=True)
        for attempt in range(4):
            if not self.token or time.time() > self.expires:
                self._login()
            req = urllib.request.Request(f"{self.url}{path}{'?' + q if q else ''}",
                                         headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json; version=1.0", "User-Agent": "devmem"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    body = r.read().decode("utf-8", "replace")
                try:
                    return json.loads(body) if body.strip() else {}
                except ValueError:
                    raise CheckmarxError(f"{self.url} did not answer with JSON: is this the Checkmarx One address (e.g. https://ast.checkmarx.net)?") from None
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:300]
                if e.code == 401 and attempt == 0:
                    self.token = ""                       # expired early: log in again once
                    continue
                if e.code in (502, 503, 504) and attempt < 3:
                    time.sleep(1.5 * (attempt + 1))       # the results endpoint is known to time out under load
                    continue
                if e.code == 403:
                    raise CheckmarxError(f"the API key may not read {path}: its user needs a role that can view projects and results. {detail}") from None
                err = CheckmarxError(f"Checkmarx One answered HTTP {e.code} for {path}: {detail}")
                err.code = e.code
                raise err from None
            except (urllib.error.URLError, OSError) as e:
                raise CheckmarxError(f"cannot reach {self.url}: {getattr(e, 'reason', e)}") from None
        raise CheckmarxError(f"Checkmarx One kept failing for {path}; try again later")


def test(s: dict | None = None) -> dict:
    s = s or effective()
    c = Client(s, timeout=20)
    r = c.get("/api/projects", limit=1)
    return {"ok": True, "realm": realm_url(s), "projects": r.get("totalCount", len(r.get("projects") or []))}


def search_projects(q: str = "", limit: int = 20) -> list[dict]:
    r = Client(effective(), 20).get("/api/projects", name=q if len(q) >= 2 else None, limit=limit)
    return [{"id": p["id"], "name": p.get("name", p["id"])} for p in r.get("projects") or []]


# ---------- sync ----------
def find_project(c: Client, project: str) -> dict:
    if UUID.match(project):
        p = c.get(f"/api/projects/{project}")
        return {"id": p.get("id", project), "name": p.get("name", project)}
    for params in ({"names": project}, {"name": project}):        # exact names first, then the contains-search
        try:
            ps = c.get("/api/projects", limit=100, **params).get("projects") or []
        except CheckmarxError as e:
            if getattr(e, "code", 0) == 400:
                continue
            raise
        exact = [p for p in ps if p.get("name") == project] or [p for p in ps if str(p.get("name", "")).lower() == project.lower()]
        if exact:
            return {"id": exact[0]["id"], "name": exact[0]["name"]}
    raise CheckmarxError(f"no Checkmarx One project is called {project!r}: pick it from the list on the Integrations page, or paste its ID")


def latest_scan(c: Client, project_id: str, branch: str = "") -> dict:
    r = c.get("/api/scans", **{"project-id": project_id, "statuses": "Completed", "branch": branch, "sort": "-created_at", "limit": 1})
    scans = r.get("scans") or []
    if not scans:
        raise CheckmarxError("this project has no completed scan" + (f" on branch {branch}" if branch else "") + " yet: run one in CI, then sync again")
    return scans[0]


def _text(s) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", str(s or "")))).strip()


def _manifest(pkg: str, files: set[str]) -> str:
    eco = re.split(r"[-:]", pkg or "", maxsplit=1)[0].lower()
    names = MANIFESTS.get(eco) or tuple(n for v in MANIFESTS.values() for n in v)
    for n in names:
        hit = sorted((f for f in files if f == n or f.endswith("/" + n)), key=len)
        if hit:
            return hit[0]
    return ""


def normalise(r: dict, files: set[str]) -> dict | None:
    """One Checkmarx One result -> a devmem finding, or None for results triaged as not exploitable or already fixed."""
    if str(r.get("state", "")).upper() in IGNORED_STATES or str(r.get("status", "")).upper() == "FIXED":
        return None
    typ = str(r.get("type") or "").lower()
    d = r.get("data") or {}
    vd = r.get("vulnerabilityDetails") or {}
    sev_raw = str(r.get("severity") or "MEDIUM").upper()
    out = {"tool": "checkmarx", "key": r.get("id") or r.get("similarityId") or "", "severity": SEVERITY.get(sev_raw, "medium"), "checkmarx_severity": sev_raw,
           "category": "security", "scanner": typ or "sast", "state": r.get("state", ""), "status": r.get("status", ""), "line": 1, "path": ""}
    about = _text(d.get("description") or r.get("description"))
    cwe = vd.get("cweId")
    if typ in ("sca", "sca-container", "containers"):
        pkg = d.get("packageIdentifier") or d.get("packageName") or ""
        cve = vd.get("cveName") or r.get("id") or "vulnerability"
        score = vd.get("cvssScore")
        out.update(code=cve, path=_manifest(pkg, files) if typ == "sca" else _repo_path(str(d.get("imageFilePath") or "").lstrip("/"), files),
                   msg=f"{pkg or 'a dependency'} has {cve}" + (f" (CVSS {score})" if score else "") + (f"; fixed in {d['recommendedVersion']}" if d.get("recommendedVersion") else ""),
                   package=pkg)
    elif typ == "kics":
        out.update(code=d.get("queryName") or d.get("queryId") or "kics", path=_repo_path(str(d.get("filename") or "").lstrip("/"), files), line=int(d.get("line") or 1),
                   msg=(d.get("queryName") or "infrastructure misconfiguration") + (f": expected {d['expectedValue']}, found {d['actualValue']}" if d.get("expectedValue") and d.get("actualValue") else ""))
    else:          # sast and anything with a data flow: point at the last node (where the data is used), mention where it came from
        nodes = d.get("nodes") or []
        sink, src = (nodes[-1] if nodes else {}), (nodes[0] if nodes else {})
        q = d.get("queryName") or r.get("label") or "finding"
        out.update(code=q, path=_repo_path(str(sink.get("fileName") or "").lstrip("/"), files), line=int(sink.get("line") or 1),
                   msg=q.replace("_", " ") + (f": data from {str(src.get('fileName', '')).lstrip('/')}:{src.get('line')}" if len(nodes) > 1 and src.get("fileName") else ""),
                   steps=len(nodes))
        if d.get("group"):
            out["group"] = d["group"]
    if cwe:
        out["cwe"] = cwe if str(cwe).upper().startswith("CWE") else f"CWE-{cwe}"
    if not about:                  # what each engine's finding means, when the result carries no description of its own
        about = {"sca": f"A version of {out.get('package') or 'a dependency'} this project uses has a published vulnerability ({out['code']}). Code that calls the affected part of the package inherits it.",
                 "kics": "A setting in an infrastructure or container file (Dockerfile, Terraform, Kubernetes, CloudFormation …) does not follow the secure configuration Checkmarx checks for.",
                 "containers": "A package inside the container image has a published vulnerability."}.get(typ, "")
        if about:
            out["about"] = {"what": about, "fix": {"sca": "Upgrade the package to a version without the vulnerability" + (f" ({d['recommendedVersion']} or later)" if d.get("recommendedVersion") else "") + ", then run the tests.",
                                                    "kics": "Change the setting to the expected value shown in the finding.",
                                                    "containers": "Rebuild the image on a patched base image or upgrade the package."}[typ]}
            about = ""
    if about and "about" not in out:
        out["about"] = {"what": about[:700], "fix": _text(r.get("recommendations") or d.get("recommendations") or "")[:500]
                        or ("Upgrade the package to a version without this vulnerability." if typ == "sca" else "")}
    return out


def fetch(project: str, branch: str = "", files: set[str] | None = None, s: dict | None = None) -> dict:
    s = s or effective()
    c = Client(s)
    files = files or set()
    p = find_project(c, project)
    scan = latest_scan(c, p["id"], branch)
    out = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "url": s["url"], "project": p,
           "scan": {"id": scan.get("id"), "branch": scan.get("branch", branch), "created": scan.get("createdAt", ""), "engines": scan.get("engines") or []},
           "issues": [], "skipped": 0, "truncated": False, "notes": []}
    offset, total = 0, None
    while offset < MAX_RESULTS:
        r = c.get("/api/results", **{"scan-id": scan["id"], "limit": PAGE, "offset": offset})
        got = r.get("results") or []
        total = r.get("totalCount", total)
        for x in got:
            f = normalise(x, files)
            if f is None:
                out["skipped"] += 1
            else:
                out["issues"].append(f)
        offset += len(got)
        if not got or (total is not None and offset >= total):
            break
    out["total"] = total if total is not None else offset
    out["truncated"] = (total or 0) > offset
    by = {}
    for f in out["issues"]:
        by[f["scanner"]] = by.get(f["scanner"], 0) + 1
    out["by_scanner"] = by
    return out


def load(root: Path) -> dict | None:
    try:
        d = json.loads((Path(root) / PATH).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def sync(repo: str, root: Path, files: set[str]) -> dict:
    """Read the repo's project's latest completed scan and write `.devmem/checkmarx.json`. Raises CheckmarxError with what to fix."""
    s = effective()
    m = s["projects"].get(repo) or {}
    data = fetch(m.get("project") or repo, m.get("branch", ""), files, s)
    paths = [f["path"] for f in data["issues"] if f["path"]]
    data["matched"] = sum(1 for p in paths if p in files) if files else len(paths)
    if paths and files and not data["matched"]:
        data["notes"].append("none of the result paths match files in this repo: is this the right project and branch?")
    p = Path(root) / PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(p, json.dumps(data, indent=1))
    return data
