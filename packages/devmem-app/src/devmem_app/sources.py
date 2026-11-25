"""Where a library's documentation and release history really live: its git repository and its docs site.

The package registries (PyPI, npm) are used only as an index: they say which version is current and where the project's
repository and docs site are. The docs themselves are read from the repository at the tag of that release (README,
`docs/`, migration guides, changelog), with one shallow, blob-less, sparse git clone: no API, no rate limit, and it works
for GitHub, GitLab, Bitbucket, Codeberg or any other git host. Release notes come from the GitHub releases API when it
answers, else the releases feed (no rate limit), else the changelog in the repository.

Everything here is best effort and network-bound; callers fall back to the registry's own README when nothing is found."""
from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
from pathlib import Path

GIT_TIMEOUT = int(os.environ.get("DEVMEM_GIT_TIMEOUT", "180"))
DOC_EXT = {".md", ".mdx", ".markdown", ".rst", ".txt"}
DOC_DIRS = ("docs", "doc", "documentation", "website/docs", "site/docs", "guide", "guides", "book", "manual")
SKIP_DIRS = {"node_modules", "vendor", "third_party", "tests", "test", "__tests__", "fixtures", "examples", ".github", ".changeset",
             "benchmarks", "dist", "build", "_build", "site-packages", "img", "images", "static", "assets", "blog", "news"}
LANGS = {"ar", "bn", "cs", "da", "de", "el", "es", "fa", "fi", "fr", "he", "hi", "hu", "id", "it", "ja", "ko", "nl", "no", "pl", "pt", "pt-br",
         "ro", "ru", "sv", "th", "tr", "uk", "ur", "vi", "zh", "zh-cn", "zh-hans", "zh-hant", "zh-tw", "az", "em", "fil", "ka", "ms", "my"}
UPGRADE_NAME = re.compile(r"(migrat|upgrad|breaking|porting|whats?[-_ ]?new|release[-_ ]?notes|changelog|changes|history|news)", re.I)
GUIDE_NAME = re.compile(r"(migrat|upgrad|breaking|porting|whats?[-_ ]?new)", re.I)
CHANGELOG_NAME = re.compile(r"^(changelog|changes|history|news|release[-_ ]?notes)(\.\w+)?$", re.I)
HOSTS = ("github.com", "gitlab.com", "bitbucket.org", "codeberg.org", "git.sr.ht", "gitee.com")


def _get(url: str, timeout: int = 20, accept: str = "*/*") -> str:
    from .docs import get
    return get(url, timeout, accept)


def _try(url: str, timeout: int = 15) -> str | None:
    try:
        return _get(url, timeout)
    except Exception:
        return None


# ---------- registry metadata: only to learn the version and where the project lives ----------
def norm_repo(url: str | None) -> dict | None:
    """A repository URL in any of the forms registries use -> {"git": clone URL, "web": browse URL, "host", "path", "subdir"}.
    Accepts git+https://, git://, ssh, git@host:o/r, npm shorthands (github:o/r, o/r), and /tree/<ref>/<subdir> links."""
    if not url:
        return None
    u = str(url).strip()
    m = re.fullmatch(r"(github|gitlab|bitbucket):([\w.-]+/[\w.-]+)", u)
    if m:
        u = f"https://{m.group(1)}.{'org' if m.group(1) == 'bitbucket' else 'com'}/{m.group(2)}"
    elif re.fullmatch(r"[\w.-]+/[\w.-]+", u):          # npm "owner/repo" shorthand means GitHub
        u = f"https://github.com/{u}"
    u = re.sub(r"^git\+", "", u)
    u = re.sub(r"^git@([\w.-]+):", r"https://\1/", u)
    u = re.sub(r"^(ssh|git)://(git@)?", "https://", u)
    p = urllib.parse.urlparse(u)
    host = p.netloc.split("@")[-1].split(":")[0].lower().removeprefix("www.")
    if not host or p.scheme not in ("http", "https"):
        return None
    parts = [x for x in p.path.split("/") if x]
    if not parts:
        return None
    subdir = ""
    for marker in ("-", "tree", "blob", "src"):       # GitLab puts /-/ before tree/blob
        if marker in parts[2:]:
            i = parts.index(marker, 2)
            rest = parts[i + 1:]
            if marker == "-" and rest[:1] in (["tree"], ["blob"]):
                rest = rest[1:]
            subdir = "/".join(rest[1:]) if rest else ""
            parts = parts[:i]
            break
    if host in HOSTS or host.startswith(("github.", "gitlab.")):
        if host in ("github.com", "bitbucket.org", "codeberg.org", "gitee.com"):
            parts = parts[:2]
        if len(parts) < 2:
            return None
    elif not p.path.endswith(".git"):
        return None                                     # some web page, not a repository
    path = "/".join(parts)
    path = re.sub(r"\.git$", "", path)
    if len(path.split("/")) < 2 or re.search(r"(^|/)(issues|discussions|wiki|actions|pulls|releases)$", path):
        return None
    return {"git": f"https://{host}/{path}.git", "web": f"https://{host}/{path}", "host": host, "path": path, "subdir": subdir.strip("/")}


def github_slug(repo: dict | None) -> str | None:
    return repo["path"] if repo and repo["host"] == "github.com" else None


def _pick_repo(urls: list[tuple[str, str]]) -> dict | None:
    """The source repository among a project's links: one labelled source/repository/code first, then any repository URL."""
    ranked = sorted(urls, key=lambda kv: (not re.search(r"source|repo|code|git", kv[0], re.I), not re.search(r"github|gitlab", kv[1] or "", re.I)))
    for _, u in ranked:
        r = norm_repo(u)
        if r:
            return r
    return None


def _pick_docs(urls: list[tuple[str, str]], repo: dict | None) -> str | None:
    for k, u in urls:
        if u and re.search(r"doc", k, re.I) and u.startswith("http") and not (repo and urllib.parse.urlparse(u).netloc.endswith(repo["host"])):
            return u
    for k, u in urls:      # a homepage that is not the repository is usually the docs site
        if u and re.search(r"home", k, re.I) and u.startswith("http") and not norm_repo(u):
            return u
    return None


def package_meta(eco: str, name: str, want: str = "") -> dict:
    """{name, version, summary, readme, repo, docs_url, released, registry_url} for one package release (latest without `want`)."""
    from .docs import REGISTRY_MAX, get
    if eco == "pypi":
        data = json.loads(get(f"https://pypi.org/pypi/{urllib.parse.quote(name)}/{urllib.parse.quote(want) + '/' if want else ''}json", 30, limit=REGISTRY_MAX))
        info = data["info"]
        urls = [(str(k), str(v)) for k, v in (info.get("project_urls") or {}).items()] + [("homepage", info.get("home_page") or ""), ("download", info.get("download_url") or "")]
        repo = _pick_repo(urls)
        released = min((f.get("upload_time_iso_8601") or f.get("upload_time") or "" for f in data.get("urls") or []), default="") or ""
        return {"eco": eco, "name": info.get("name") or name, "version": info.get("version", ""), "summary": info.get("summary") or "",
                "readme": info.get("description") or "", "repo": repo, "docs_url": _pick_docs(urls, repo), "released": released[:10],
                "registry_url": f"https://pypi.org/project/{name}/", "requires_python": info.get("requires_python") or ""}
    qp = urllib.parse.quote(name, safe="@")
    meta = json.loads(get(f"https://registry.npmjs.org/{qp}/{urllib.parse.quote(want) if want else 'latest'}", 20))
    rep = meta.get("repository")
    rurl = rep.get("url", "") if isinstance(rep, dict) else str(rep or "")
    repo = norm_repo(rurl) or _pick_repo([("homepage", meta.get("homepage") or ""), ("bugs", (meta.get("bugs") or {}).get("url", "") if isinstance(meta.get("bugs"), dict) else "")])
    if repo and isinstance(rep, dict) and rep.get("directory"):
        repo["subdir"] = str(rep["directory"]).strip("/")
    home = meta.get("homepage") or ""
    docs_url = home if home.startswith("http") and not norm_repo(home.split("#")[0]) else None
    return {"eco": eco, "name": meta.get("name") or name, "version": meta.get("version", ""), "summary": meta.get("description") or "",
            "readme": meta.get("readme") or "", "repo": repo, "docs_url": docs_url, "released": "",
            "registry_url": f"https://www.npmjs.com/package/{name}", "engines": meta.get("engines") or {}}


# ---------- git ----------
def _git(*args: str, cwd: Path | None = None, timeout: int | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "GCM_INTERACTIVE": "never"}
    return subprocess.run(["git", "-c", "gc.auto=0", "-c", "advice.detachedHead=false", *args], cwd=cwd, capture_output=True, text=True,
                          env=env, timeout=timeout or GIT_TIMEOUT, encoding="utf-8", errors="replace")


def tags(git_url: str) -> list[str]:
    """Every tag of a remote repository (one `git ls-remote`; no API)."""
    r = _git("ls-remote", "--tags", "--refs", git_url, timeout=60)
    if r.returncode:
        raise RuntimeError(f"git ls-remote failed: {(r.stderr or r.stdout).strip()[:200]}")
    return [ln.split("refs/tags/", 1)[1] for ln in r.stdout.splitlines() if "refs/tags/" in ln]


def _vt(s: str) -> tuple:
    from .docs import _ver
    return _ver(s)


def _tag_prefix(tag: str, start: int) -> str:
    """The part of a tag before its version, without separators or a `v`: "@scope/pkg@" -> "@scope/pkg", "pkg-v" -> "pkg"."""
    raw = tag[:start].lower().rstrip("-_@/")
    return re.sub(r"(^|[-_@/])v$", r"\1", raw).rstrip("-_@/")


def tag_for(all_tags: list[str], version: str, pkg: str = "") -> str | None:
    """The tag a release was cut from: v1.2.3, 1.2.3, <pkg>-1.2.3, <pkg>@1.2.3, release-1.2.3 ... In a monorepo the tag naming
    exactly this package wins over a sibling's (`@tanstack/react-query@5` not `@tanstack/preact-query@5`); an exact version
    string beats one that only has the same numbers. None when only other packages' tags carry this version."""
    if not version:
        return None
    full, base = pkg.lower(), pkg.split("/")[-1].lower()
    best, rank = None, 99
    for t in all_tags:
        tt = re.sub(r"(?<=\d)_(?=\d)", ".", t)          # rel_2_1_9 (SQLAlchemy) -> rel_2.1.9
        m = re.search(r"(\d+(?:\.\d+)+(?:[-.]?(?:a|b|rc|alpha|beta|post|dev)\.?\d*)?)$", tt)
        if not m:
            continue
        exact = m.group(1).lstrip("v") == version.lstrip("v")
        if not exact and not (_vt(m.group(1)) == _vt(version) and _vt(version) and version.count(".") == m.group(1).count(".")):
            continue
        prefix = _tag_prefix(tt, m.start())
        last = re.split(r"[/@]", prefix)[-1] if prefix else ""
        r = (0 if full and prefix in (full, full.lstrip("@")) else 1 if prefix in ("", "release", "rel", "version", "releases")
             else 2 if base and last == base else 6 if base and re.search(rf"(^|[-_/@]){re.escape(base)}($|[-_/@])", prefix) else 9)
        r += 0 if exact else 1
        if r < rank:
            best, rank = t, r
    if best and rank >= 9 and pkg:      # a monorepo tag of some other package with the same version number: not ours
        return None
    return best


def _langdir(part: str) -> bool:
    return part.lower() in LANGS and part.lower() != "en"


DOC_SEGMENTS = {"docs", "doc", "documentation", "website", "guide", "guides", "manual", "book", "wiki"}
NOISE = re.compile(r"^(contributing|code[-_]of[-_]conduct|security|license|licence|governance|sponsors?|backers|enterprise[-_]support|"
                   r"help[-_]with[-_]\w+|support|authors|maintainers|funding|issue[-_]template|pull[-_]request[-_]template|requirements|"
                   r"third[-_]party[-_]notices|notice|codeowners|readme[-_.][\w.-]+)$", re.I)


def select_doc_files(listing: list[str], subdir: str = "", max_files: int = 80, pkg: str = "") -> list[str]:
    """From `git ls-tree -r --name-only` (paths only: asking for sizes makes a blob-less clone fetch every file): the README,
    the docs tree (English only), migration guides and the changelog, most useful first. In a monorepo the package's own
    files (`subdir`, or a folder named after it) come first, and docs written for a sibling variant (docs/framework/svelte/
    when the package is react-query) are left out."""
    sub = (subdir.strip("/") + "/") if subdir else ""
    base = pkg.split("/")[-1].lower()
    tokens = {t for t in re.split(r"[-_./@]", pkg.lower()) if len(t) > 2} - {"core", "lib", "python", "node", "tanstack", "types", "client", "sdk"}
    cands = []
    for path in listing:
        p = Path(path)
        if p.suffix.lower() not in DOC_EXT or NOISE.match(p.stem):
            continue
        parts = [x.lower() for x in p.parts]
        dirs = parts[:-1]
        if any(x in SKIP_DIRS or x.startswith((".", "_")) for x in dirs) or any(_langdir(x) for x in dirs):
            continue
        in_sub = bool(sub) and path.startswith(sub)
        rel = path[len(sub):] if in_sub else path
        rdirs = [x.lower() for x in Path(rel).parts[:-1]]
        stem = p.stem.lower()
        in_docs = any(d in DOC_SEGMENTS for d in rdirs[:3])
        own_pkg = bool(base) and len(dirs) >= 1 and dirs[-1] == base
        if p.suffix.lower() == ".txt" and not stem.startswith(("readme", "changelog", "changes", "history", "llms")):
            continue
        if stem.startswith("readme") and (not rdirs or own_pkg):
            order = 0
        elif GUIDE_NAME.search(p.name) and (not rdirs or in_docs or own_pkg):
            order = 1
        elif CHANGELOG_NAME.match(p.name) and (not rdirs or own_pkg or (in_docs and len(rdirs) <= 2)):
            order = 5
        elif in_docs:
            order = 2 if stem in ("index", "readme", "introduction", "intro", "getting-started", "quickstart", "overview") else 3
        else:
            continue
        hit = next((k for k, d in enumerate(dirs) if d in tokens), None) if in_docs else None
        cands.append({"path": path, "order": order, "mine": in_sub or own_pkg or not sub, "hit": hit, "dirs": dirs, "depth": len(rdirs)})
    variants = {"/".join(c["dirs"][:c["hit"]]) + "/" for c in cands if c["hit"] is not None and c["hit"] > 0}
    if variants:          # docs/framework/react/ matched the package name: docs/framework/<anything else>/ is for another variant
        def other_variant(c: dict) -> bool:
            d = "/".join(c["dirs"]) + "/"
            return c["hit"] is None and any(d.startswith(v) and d != v for v in variants)
        cands = [c for c in cands if not other_variant(c)]
    cands.sort(key=lambda c: (not c["mine"], c["order"], c["hit"] is None, c["depth"], c["path"].lower()))
    out = [c["path"] for c in cands]
    changelog = [p for p in out if CHANGELOG_NAME.match(Path(p).name)][:2]
    rest = [p for p in out if p not in changelog]
    return (rest[: max_files - len(changelog)] + changelog)[:max_files]


def _rst_to_md(text: str) -> str:
    """Enough reStructuredText -> Markdown for search and reading: underlined headings, code blocks, roles."""
    lines, out, levels = text.splitlines(), [], []
    i = 0
    while i < len(lines):
        ln = lines[i]
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if ln.strip() and re.fullmatch(r"([=\-~^\"'`#*+])\1{2,}\s*", nxt) and len(nxt.strip()) >= len(ln.strip()) * 0.6:
            ch = nxt.strip()[0]
            if ch not in levels:
                levels.append(ch)
            out.append("#" * min(levels.index(ch) + 1, 4) + " " + ln.strip())
            i += 2
            continue
        if re.fullmatch(r"([=\-~^\"'`#*+])\1{2,}\s*", ln) and nxt.strip() and i + 2 < len(lines) and lines[i + 2].strip() == ln.strip():
            i += 1          # overlined heading: the next iteration sees "title / underline"
            continue
        ln = re.sub(r":(?:py:)?(?:func|class|meth|attr|mod|obj|exc|data|ref|doc|const):`~?([^`<]+?)(?:\s*<[^>]+>)?`", r"`\1`", ln)
        ln = re.sub(r"``([^`]+)``", r"`\1`", ln)
        if re.match(r"^\.\. (code-block|code|sourcecode)::", ln) or ln.rstrip().endswith("::") and not ln.strip().startswith(".."):
            out.append(ln.rstrip()[:-1] if ln.rstrip().endswith("::") and not ln.startswith("..") else "")
            out.append("```")
            i += 1
            while i < len(lines) and (not lines[i].strip() or lines[i].startswith((" ", "\t"))):
                out.append(lines[i][4:] if lines[i].startswith("    ") else lines[i].strip())
                i += 1
            while out and not out[-1].strip():
                out.pop()
            out.append("```")
            continue
        if ln.startswith(".. ") and not ln.startswith(".. note") and not ln.startswith(".. warning"):
            i += 1
            continue
        out.append(ln)
        i += 1
    return "\n".join(out)


def _title(text: str, fallback: str) -> str:
    for ln in text.splitlines()[:40]:
        m = re.match(r"^#{1,3}\s+(.+)", ln)
        if m:
            t = re.sub(r"!\[[^\]]*\]\([^)]*\)|<[^>]+>", "", m.group(1))          # badges and inline HTML
            t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)                      # links -> their text
            t = re.sub(r"\{\s*#[\w-]+\s*\}|[`*_]", "", t).strip(" -:→")
            if len(t) >= 2 and not re.search(r"read the docs|^https?:", t, re.I):
                return t[:120]
    m = re.search(r"^title:\s*['\"]?(.+?)['\"]?\s*$", text[:600], re.M)      # MDX / Docusaurus front matter
    return (m.group(1).strip() if m else fallback)[:120]


def _strip_front_matter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if 0 < end < 3000:
            return text[end + 4:].lstrip()
    return text


def blob_url(repo: dict, ref: str, path: str) -> str:
    if repo["host"] == "gitlab.com" or repo["host"].startswith("gitlab."):
        return f"{repo['web']}/-/blob/{ref}/{path}"
    if repo["host"] == "bitbucket.org":
        return f"{repo['web']}/src/{ref}/{path}"
    if repo["host"] == "codeberg.org":
        return f"{repo['web']}/src/{'tag' if ref != 'HEAD' else 'branch'}/{ref}/{path}"
    return f"{repo['web']}/blob/{ref}/{path}"


def git_docs(repo: dict, version: str = "", pkg: str = "", max_pages: int = 60, ref: str | None = None) -> dict:
    """Clone the repository at the release's tag (default branch when no tag matches) without blobs, check out only the docs
    files, and return {"pages": [(title, url, markdown)], "ref", "tagged", "files"}. Raises RuntimeError when git fails."""
    tmp = Path(tempfile.mkdtemp(prefix="devmem-git-"))
    try:
        if ref is None:
            try:
                ref = tag_for(tags(repo["git"]), version, pkg)
            except Exception:
                ref = None
        dest = tmp / "r"
        args = ["clone", "--quiet", "--depth", "1", "--filter=blob:none", "--no-checkout", *(["--branch", ref] if ref else []), repo["git"], str(dest)]
        r = _git(*args)
        if r.returncode and ref:                          # the tag vanished or is not a branch-like ref: the default branch is still useful
            shutil.rmtree(dest, ignore_errors=True)
            ref = None
            r = _git("clone", "--quiet", "--depth", "1", "--filter=blob:none", "--no-checkout", repo["git"], str(dest))
        if r.returncode:
            raise RuntimeError(f"git clone {repo['web']} failed: {(r.stderr or r.stdout).strip()[:240]}")
        ls = _git("-c", "core.quotePath=false", "ls-tree", "-r", "--name-only", "HEAD", cwd=dest)
        listing = [ln for ln in ls.stdout.splitlines() if ln]
        files = select_doc_files(listing, repo.get("subdir", ""), max_pages, pkg)
        if not files:
            return {"pages": [], "ref": ref or "HEAD", "tagged": bool(ref), "files": []}
        pats = "\n".join("/" + re.sub(r"([\\*?\[\]!#])", r"\\\1", f) for f in files) + "\n"
        sc = subprocess.run(["git", "-c", "gc.auto=0", "sparse-checkout", "set", "--no-cone", "--stdin"], cwd=dest, input=pats, capture_output=True,
                            text=True, timeout=GIT_TIMEOUT, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        co = _git("checkout", "-q", "HEAD", cwd=dest)
        if sc.returncode or co.returncode:
            raise RuntimeError(f"git checkout of the docs failed: {(sc.stderr or co.stderr).strip()[:240]}")
        pages, paths = [], []
        for f in files:
            fp = dest / f
            if not fp.is_file() or fp.stat().st_size > 1_500_000:
                continue
            text = fp.read_text(encoding="utf-8", errors="replace")
            if fp.suffix.lower() == ".rst":
                text = _rst_to_md(text)
            text = _strip_front_matter(text).strip()
            if len(text) < 40:
                continue
            title = _title(text, f)
            if Path(f).stem.lower() == "readme" and "/" not in f.removeprefix((repo.get("subdir") or "") + "/"):
                title = f"{pkg or repo['path']} README: {title}" if title != f else f"{pkg or repo['path']} README"
            pages.append((title, blob_url(repo, ref or "HEAD", f), text))
            paths.append(f)
        return {"pages": pages, "paths": paths, "ref": ref or "HEAD", "tagged": bool(ref), "files": files}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def git_file(repo: dict, names: tuple[str, ...], ref: str | None = None) -> tuple[str, str] | None:
    """(path, text) of the first of `names` that exists at the repository root, through raw file hosting when the host has it,
    else a sparse clone. Used for changelogs."""
    raw = {"github.com": "https://raw.githubusercontent.com/{p}/{r}/{f}", "gitlab.com": "https://gitlab.com/{p}/-/raw/{r}/{f}",
           "codeberg.org": "https://codeberg.org/{p}/raw/{r}/{f}", "bitbucket.org": "https://bitbucket.org/{p}/raw/{r}/{f}"}.get(repo["host"])
    sub = repo.get("subdir") or ""
    cands = [f"{sub}/{n}" for n in names] + list(names) if sub else list(names)
    if raw:
        for n in cands:
            t = _try(raw.format(p=repo["path"], r=ref or "HEAD", f=n))
            if t and not t.lstrip().startswith("<!DOCTYPE"):
                return n, t
        return None
    try:
        got = git_docs(repo, ref=ref, max_pages=200)
    except Exception:
        return None
    for n in cands:
        for (t, u, md), p in zip(got["pages"], got.get("paths", [])):
            if p.lower() == n.lower():
                return p, md
    return None


# ---------- release history ----------
def releases_feed(slug: str, timeout: int = 20) -> list[dict]:
    """GitHub's Atom feed of a repository's releases (the latest ~10): no API token or rate limit. [{tag, title, body, date, url}]."""
    xml = _get(f"https://github.com/{slug}/releases.atom", timeout, "application/atom+xml")
    out = []
    for e in re.findall(r"<entry>(.*?)</entry>", xml, re.S):
        link = re.search(r'<link[^>]+href="([^"]+)"', e)
        tag = urllib.parse.unquote(link.group(1).rsplit("/tag/", 1)[-1]) if link and "/tag/" in link.group(1) else ""
        body = re.search(r"<content[^>]*>(.*?)</content>", e, re.S)
        md = ""
        if body:
            from .docs import html_to_md
            md = html_to_md(html.unescape(body.group(1)))[1]
        title = re.search(r"<title>(.*?)</title>", e, re.S)
        date = re.search(r"<updated>(.*?)</updated>", e)
        out.append({"tag": tag or (html.unescape(title.group(1)).strip() if title else ""), "title": html.unescape(title.group(1)).strip() if title else tag,
                    "body": md, "date": (date.group(1) if date else "")[:10], "url": link.group(1) if link else f"https://github.com/{slug}/releases"})
    return out


def github_releases(slug: str, timeout: int = 20) -> list[dict]:
    """The GitHub releases API (100 newest). GITHUB_TOKEN lifts the anonymous limit of 60 calls an hour."""
    import urllib.request
    from .docs import UA
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    req = urllib.request.Request(f"https://api.github.com/repos/{slug}/releases?per_page=100",
                                 headers={"User-Agent": UA, "Accept": "application/vnd.github+json", **({"Authorization": f"Bearer {tok}"} if tok else {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        rels = json.loads(r.read().decode("utf-8", "replace"))
    if not isinstance(rels, list):
        raise RuntimeError(str(rels)[:200])
    return [{"tag": x.get("tag_name", ""), "title": x.get("name") or x.get("tag_name", ""), "body": x.get("body") or "", "date": (x.get("published_at") or "")[:10],
             "url": x.get("html_url") or "", "prerelease": bool(x.get("prerelease"))} for x in rels]


CHANGELOGS = ("CHANGELOG.md", "CHANGES.md", "HISTORY.md", "NEWS.md", "RELEASE_NOTES.md", "CHANGELOG.rst", "CHANGES.rst", "HISTORY.rst", "NEWS.rst",
              "CHANGELOG", "CHANGES", "docs/changelog.md", "docs/CHANGELOG.md", "docs/changes.rst", "docs/changelog.rst", "docs/history.rst",
              "docs/release-notes.md", "docs/releases.md", "docs/news.rst")


def parse_changelog(text: str) -> list[tuple[str, str]]:
    """[(version, text)] sections of a changelog: `## 1.2.3`, `## [1.2.3] - date`, `v1.2.3 (date)` + underline, `Version 1.2.3`."""
    out, cur, buf = [], None, []
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        m = re.match(r"^\s{0,3}(?:#{1,4}\s*)?(?:version\s+|release\s+|v(?:ersion)?\s*)?\[?v?(\d+\.\d+(?:\.\d+)?(?:[-.]?(?:a|b|rc|alpha|beta|post)\.?\d*)?)\]?(?:\s|\(|:|$|\s*[-–—])", ln, re.I)
        heading = ln.lstrip().startswith("#") or re.fullmatch(r"[=\-~^*]{3,}\s*", nxt or "") is not None or re.match(r"^\s{0,3}\[?v?\d", ln)
        if m and heading and not ln.lstrip().startswith(("-", "*", "+")):
            if cur:
                out.append((cur, "\n".join(buf).strip()))
            cur, buf = m.group(1), []
        elif cur is not None and not re.fullmatch(r"[=\-~^*]{3,}\s*", ln):
            buf.append(ln)
    if cur:
        out.append((cur, "\n".join(buf).strip()))
    return out


def notes_between(repo: dict | None, frm: str, to: str | None, pkg: str = "", cap: int = 80_000) -> dict:
    """What changed after `frm` up to `to`: {"notes": [(version, text)] oldest first, "source": "github releases" | "releases feed"
    | "<changelog path>", "url", "error"}. Tries the GitHub API, the releases feed, then the changelog file."""
    from .docs import _ver
    res = {"notes": [], "source": "", "url": "", "error": ""}
    if not repo:
        res["error"] = "no source repository is known for this package"
        return res
    lo, hi = _ver(frm or "0"), _ver(to) if to else (10 ** 9,)
    base = pkg.split("/")[-1].lower()

    def mine(tag: str) -> bool:      # monorepos tag every package: keep this package's tags (or untagged-by-name ones)
        pre = re.split(r"v?\d", tag, 1)[0].lower().rstrip("-_@/")
        return not pre or not base or base in pre or pre in ("release", "version", "rel")

    def window(rels: list[dict]) -> list[tuple[str, str]]:
        got = sorted(((_ver(x["tag"]), x["tag"], x["body"]) for x in rels if not x.get("prerelease") and _ver(x["tag"]) and mine(x["tag"])
                      and lo < _ver(x["tag"]) <= hi and not re.search(r"(a|b|rc|alpha|beta|dev)\.?\d*$", x["tag"])))
        out, total = [], 0
        for _, tag, body in got:
            total += len(body)
            if total > cap:
                break
            out.append((tag, (body or "").strip()))
        return out
    slug = github_slug(repo)
    errors = []
    if slug:
        for fn, label in ((github_releases, "github releases"), (releases_feed, "releases feed")):
            try:
                rels = fn(slug)
            except Exception as e:
                errors.append(f"{label}: {str(e)[:100]}")
                continue
            picked = [x for x in window(rels) if x[1].strip()]
            if picked:
                res.update(notes=picked, source=label, url=f"https://github.com/{slug}/releases")
                # the API lists 100 releases and is complete for any normal window; the feed only reaches back ~10 releases,
                # so a window older than that is better served by the changelog, tried below
                if label == "github releases" or len(rels) < 10 or min(_ver(x["tag"]) for x in rels if _ver(x["tag"])) <= lo:
                    return res
                break
    got = git_file(repo, CHANGELOGS)
    if got:
        path, text = got
        if path.lower().endswith(".rst"):
            text = _rst_to_md(text)
        picked = [(v, b) for v, b in parse_changelog(text) if lo < _ver(v) <= hi]
        if picked and len(picked) >= len(res["notes"]):
            total, keep = 0, []
            for v, b in sorted(picked, key=lambda x: _ver(x[0])):
                total += len(b)
                if total > cap:
                    break
                keep.append((v, b))
            res.update(notes=keep, source=path, url=blob_url(repo, "HEAD", path), error="")
            return res
    if not res["notes"]:
        res["error"] = "; ".join(errors) or "no release notes or changelog entries found between those versions"
    return res


# ---------- docs site without llms.txt: a small same-site crawl ----------
def crawl(start: str, max_pages: int = 12) -> list[tuple[str, str, str]]:
    """The start page and pages it links to on the same site under the same path, converted to Markdown. Breadth-first,
    small on purpose: an overview of the docs, not a mirror."""
    from concurrent.futures import ThreadPoolExecutor
    from .docs import _page, looks_html
    p0 = urllib.parse.urlparse(start)
    prefix = p0.path.rsplit("/", 1)[0] + "/" if not p0.path.endswith("/") else p0.path
    first = _try(start)
    if not first:
        return []
    title0, md0 = _page(start, first)
    pages = [(title0, start, md0)]
    if not looks_html(first):
        return pages
    links, seen = [], {start.split("#")[0].rstrip("/")}
    for href in re.findall(r'<a[^>]+href="([^"#]+)', first):
        u = urllib.parse.urljoin(start, html.unescape(href)).split("#")[0].split("?")[0]
        pu = urllib.parse.urlparse(u)
        if pu.netloc == p0.netloc and pu.path.startswith(prefix) and not re.search(r"\.(png|jpe?g|svg|gif|css|js|zip|gz|pdf|xml|ico)$", pu.path, re.I) \
                and u.rstrip("/") not in seen:
            seen.add(u.rstrip("/")); links.append(u)
    rank = sorted(links, key=lambda u: (not UPGRADE_NAME.search(u), u.count("/"), u))[: max_pages - 1]

    def one(u):
        t = _try(u)
        if not t or len(t) < 200:
            return None
        title, md = _page(u, t)
        return (title, u, md) if len(md) > 120 else None
    with ThreadPoolExecutor(6) as ex:
        pages += [p for p in ex.map(one, rank) if p]
    return pages
