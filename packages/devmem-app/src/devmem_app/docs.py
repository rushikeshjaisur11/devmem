"""Library documentation for agents without internet. Runs where there IS internet (your machine): fetches llms.txt / web pages /
PyPI and npm package pages into `~/.devmem/docs/<lib>/` (markdown files + manifest.json). `hub push` copies them to the hub's
`docs/`, and `publish` copies the ones a repo depends on into its `.devmem/docs/`; agents search them with `devmem docs`."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path

from .paths import devmem_home

UA = "devmem-docs/0.1 (+https://github.com/rushikeshjaisur11/devmem)"
MAX_PAGE = 200_000          # bytes kept per page
MAX_LIB = 2_000_000         # bytes kept per library
MAX_REPO_DOCS = 3_000_000   # bytes copied into one repo's .devmem/docs


def docs_dir() -> Path:
    return devmem_home() / "docs"


def get(url: str, timeout: int = 30, accept: str = "text/markdown, text/plain, text/html;q=0.8, application/json;q=0.8") -> str:
    if not re.match(r"^https?://", url):
        raise ValueError(f"only http(s) URLs are supported: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(3_000_000)
        enc = r.headers.get_content_charset() or "utf-8"
    return raw.decode(enc, errors="replace")


# ---------- HTML -> markdown-ish text ----------
class _MD(HTMLParser):
    SKIP = {"script", "style", "nav", "header", "footer", "aside", "noscript", "svg", "form", "button"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []; self.title = ""; self._skip = 0; self._pre = 0; self._in_title = False; self._h = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in ("h1", "h2", "h3", "h4"):
            self.out.append("\n\n" + "#" * int(tag[1]) + " "); self._h = 1
        elif tag in ("p", "div", "section", "br", "tr"):
            self.out.append("\n")
        elif tag == "li":
            self.out.append("\n- ")
        elif tag == "pre":
            self._pre += 1; self.out.append("\n```\n")
        elif tag == "code" and not self._pre:
            self.out.append("`")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag == "title":
            self._in_title = False
        elif tag in ("h1", "h2", "h3", "h4"):
            self.out.append("\n"); self._h = 0
        elif tag == "pre":
            self._pre = max(0, self._pre - 1); self.out.append("\n```\n")
        elif tag == "code" and not self._pre:
            self.out.append("`")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.out.append(data if self._pre else re.sub(r"\s+", " ", data))


def html_to_md(html: str) -> tuple[str, str]:
    p = _MD()
    p.feed(html)
    text = re.sub(r"\n{3,}", "\n\n", "".join(p.out)).strip()
    return p.title.strip(), text


def looks_html(text: str) -> bool:
    return bool(re.search(r"<(html|body|head|div|p|h1)\b", text[:3000], re.I))


def llms_links(text: str, base: str) -> list[tuple[str, str]]:
    """`- [Title](url): note` lines of an llms.txt index."""
    out, seen = [], set()
    for m in re.finditer(r"\[([^\]]+)\]\(([^)\s]+)\)", text):
        url = urllib.parse.urljoin(base, m.group(2).split("#")[0])
        if url.startswith("http") and url not in seen:
            seen.add(url); out.append((m.group(1).strip(), url))
    return out


# ---------- fetch a library ----------
def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "page"


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _default_name(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.split(":")[0]
    parts = [p for p in host.split(".") if p not in ("www", "docs", "doc", "readthedocs", "io", "com", "org", "dev", "net", "github")]
    return _slug(parts[0] if parts else host)


def _page(url: str, text: str) -> tuple[str, str]:
    if looks_html(text):
        title, md = html_to_md(text)
        return title or url, md
    first = next((ln.lstrip("# ").strip() for ln in text.splitlines() if ln.strip()), url)
    return first[:120], text.strip()


def _try(url: str) -> str | None:
    try:
        return get(url, timeout=15)
    except Exception:
        return None


def add(spec: str, name: str | None = None, follow: bool = True, max_pages: int = 40, dest: Path | None = None) -> dict:
    """spec: an llms.txt / llms-full.txt / web page URL, `pypi:<pkg>[==ver]`, `npm:<pkg>[@ver]`, or a local file/folder of docs.
    Without a version the latest release is fetched; with one, that release (for a repo that cannot upgrade yet)."""
    pages: list[tuple[str, str, str]] = []   # (title, url, markdown)
    version, kind = "", "url"
    local = Path(spec).expanduser() if not re.match(r"^(https?|pypi|npm):", spec) else None
    if local and local.exists():
        kind, spec = "local", str(local.resolve())
        pages = _from_local(local.resolve(), max(max_pages, 200))   # a local folder is usually a whole docs tree; MAX_LIB still caps it
        name = name or _slug(local.stem if local.is_file() else local.name)
    elif spec.startswith("pypi:"):
        pkg, _, want = spec[5:].strip().partition("==")
        info = json.loads(get(f"https://pypi.org/pypi/{urllib.parse.quote(pkg)}/{urllib.parse.quote(want) + '/' if want else ''}json"))["info"]
        name, version, kind = name or _norm(pkg), info.get("version", ""), "pypi"
        pages.append((f"{pkg} {version}: overview", f"https://pypi.org/project/{pkg}/", f"# {info.get('name')} {version}\n\n{info.get('summary') or ''}\n\n{info.get('description') or ''}"))
        urls = info.get("project_urls") or {}
        docs = next((v for k, v in urls.items() if re.search(r"doc", k, re.I)), None) or info.get("home_page") or next(iter(urls.values()), None)
        if docs:
            base = docs.rstrip("/") + "/"
            for cand in ("llms-full.txt", "llms.txt"):
                t = _try(urllib.parse.urljoin(base, cand))
                if t and not looks_html(t):
                    spec_llms = (urllib.parse.urljoin(base, cand), t)
                    break
            else:
                spec_llms = None
            if spec_llms:
                pages += _from_llms(spec_llms[0], spec_llms[1], follow, max_pages)
    elif spec.startswith("npm:"):
        pkg, want = spec[4:].strip(), ""
        if "@" in pkg[1:]:   # npm:zod@3.22.4, npm:@scope/pkg@1.0
            pkg, want = pkg[0] + pkg[1:].split("@", 1)[0], pkg[1:].split("@", 1)[1]
        qp = urllib.parse.quote(pkg, safe="@")
        meta = json.loads(get(f"https://registry.npmjs.org/{qp}/{urllib.parse.quote(want) if want else 'latest'}"))   # the full packument can be many MB
        version = meta.get("version", "")
        name, kind = name or _slug(pkg.lstrip("@")), "npm"
        readme = ""
        for cdn in (f"https://unpkg.com/{qp}@{version}/README.md", f"https://cdn.jsdelivr.net/npm/{qp}@{version}/README.md"):
            readme = _try(cdn) or ""
            if readme:
                break
        pages.append((f"{pkg} {version}: readme", f"https://www.npmjs.com/package/{pkg}", f"# {pkg} {version}\n\n{meta.get('description') or ''}\n\n{readme or meta.get('readme') or ''}"))
        home = meta.get("homepage")
        if home and home.startswith("http"):
            for cand in ("llms-full.txt", "llms.txt"):
                t = _try(urllib.parse.urljoin(home.rstrip("/") + "/", cand))
                if t and not looks_html(t):
                    pages += _from_llms(urllib.parse.urljoin(home.rstrip("/") + "/", cand), t, follow, max_pages)
                    break
    else:
        text = get(spec)
        if spec.rstrip("/").endswith("llms.txt") or spec.rstrip("/").endswith("llms-full.txt"):
            kind = "llms"
            pages = _from_llms(spec, text, follow, max_pages)
            h1 = next((ln[2:].strip() for ln in text.splitlines() if ln.startswith("# ")), "")   # llms.txt spec: the H1 is the project name
            host = urllib.parse.urlparse(spec).netloc.split(":")[0]
            by_host = not (re.fullmatch(r"[\d.]+", host) or host == "localhost")   # name after the package/site (docs.pydantic.dev -> pydantic)
            name = name or (_default_name(spec) if by_host or not h1 or len(h1) > 40 else _slug(h1))
        else:
            t, md = _page(spec, text)
            pages = [(t, spec, md)]
            name = name or _default_name(spec)
    if not pages:
        raise RuntimeError(f"nothing fetched from {spec}")
    return _save(name or _slug(spec), spec, kind, version, pages, follow, max_pages, dest)


LOCAL_EXT = {".md", ".mdx", ".markdown", ".txt", ".rst", ".html", ".htm"}


def _from_local(p: Path, max_pages: int) -> list[tuple[str, str, str]]:
    """A docs file, or every docs file under a folder (sorted, hidden/vendored folders skipped). Re-read on refresh."""
    files = [p] if p.is_file() else sorted(f for f in p.rglob("*") if f.is_file() and f.suffix.lower() in LOCAL_EXT
                                           and not any(x.startswith(".") or x == "node_modules" for x in f.relative_to(p).parts))
    pages = []
    for f in files[:max_pages]:
        text = f.read_text(encoding="utf-8", errors="replace")
        t, md = _page(f.as_uri(), text)
        pages.append((t or f.stem, f.as_uri(), md))
    return pages


def _from_llms(url: str, text: str, follow: bool, max_pages: int) -> list[tuple[str, str, str]]:
    first = next((ln.lstrip("# ").strip() for ln in text.splitlines() if ln.strip()), url)
    links = llms_links(text, url)
    if url.rstrip("/").endswith("llms-full.txt") or not follow or not links:
        return [(first[:120], url, text)]
    pages = [(first[:120] + " (index)", url, text)]
    base_host = urllib.parse.urlparse(url).netloc

    def one(item):
        title, u = item
        for cand in ([u] if u.endswith((".md", ".txt")) else [u + ".md", u]):   # many sites serve markdown at <page>.md
            body = _try(cand)
            if body and len(body) > 40:
                t, md = _page(cand, body)
                return (title or t, cand, md)
        return None
    same = [l for l in links if urllib.parse.urlparse(l[1]).netloc == base_host][:max_pages]
    with ThreadPoolExecutor(8) as ex:
        pages += [p for p in ex.map(one, same) if p]
    return pages


def _save(name: str, spec: str, kind: str, version: str, pages: list[tuple[str, str, str]], follow: bool, max_pages: int, dest: Path | None) -> dict:
    d = (dest or docs_dir()) / name
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    meta, total = [], 0
    for i, (title, url, md) in enumerate(pages, 1):
        body = f"<!-- source: {url} -->\n{md[:MAX_PAGE]}\n"
        if total + len(body) > MAX_LIB:
            break
        fn = f"{i:02d}-{_slug(title)}.md"
        (d / fn).write_text(body, encoding="utf-8")
        total += len(body)
        meta.append({"file": fn, "url": url, "title": title[:120], "sha1": hashlib.sha1(md.encode()).hexdigest()[:12]})
    man = {"name": name, "source": spec, "kind": kind, "version": version, "fetched": time.strftime("%Y-%m-%d"), "pages": meta,
           "bytes": total, "follow": follow, "max_pages": max_pages}
    (d / "manifest.json").write_text(json.dumps(man, indent=1), encoding="utf-8")
    return man


def list_libs(base: Path | None = None) -> list[dict]:
    from devmem import docs as core
    out = []
    for m in sorted((base or docs_dir()).glob("*/manifest.json")):
        man = json.loads(m.read_text(encoding="utf-8"))
        out.append({**{k: man.get(k) for k in ("name", "source", "kind", "version", "fetched", "bytes")}, "pages": len(man.get("pages", [])), "age_days": core.age_days(man)})
    return out


def refresh(names: list[str] | None = None) -> dict:
    """Re-fetch libraries from their recorded source. Returns {name: 'old -> new version' | error}."""
    res = {}
    for lib in list_libs():
        if names and lib["name"] not in names:
            continue
        old = lib["version"] or lib["fetched"]
        man = json.loads((docs_dir() / lib["name"] / "manifest.json").read_text(encoding="utf-8"))
        if man.get("kind") == "upload":
            res[lib["name"]] = "uploaded: upload it again to update"
            continue
        try:
            new = add(man["source"], lib["name"], man.get("follow", True), man.get("max_pages", 40))
            res[lib["name"]] = f"{old} -> {new['version'] or new['fetched']} ({len(new['pages'])} pages)"
        except Exception as e:
            res[lib["name"]] = f"failed: {e}"
    return res


def remove(name: str) -> bool:
    d = docs_dir() / _slug(name)
    if d.is_dir():
        shutil.rmtree(d)
        return True
    return False


def export(dest: Path, only: set[str] | None = None, cap: int | None = None) -> list[str]:
    """Copy stored libraries into dest/<lib>/ (hub: dest=<hub>/docs, repo: dest=<repo>/.devmem/docs)."""
    copied, total = [], 0
    dest.mkdir(parents=True, exist_ok=True)
    for lib in list_libs():
        if only is not None and _norm(lib["name"]) not in only:
            continue
        if cap is not None and total + (lib["bytes"] or 0) > cap:
            continue
        shutil.copytree(docs_dir() / lib["name"], dest / lib["name"], dirs_exist_ok=True)
        copied.append(lib["name"]); total += lib["bytes"] or 0
    return copied


# ---------- what each repo depends on, and how current it is ----------
def scan_deps(root: Path) -> list[dict]:
    root = Path(root)
    deps: dict[tuple[str, str], str] = {}
    req = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*([<>=!~]=?[^;#\s,]*(?:\s*,\s*[<>=!~]=?[^;#\s,]*)*)?")
    for f in list(root.glob("requirements*.txt")) + list(root.glob("*/requirements*.txt")):
        for ln in f.read_text(encoding="utf-8", errors="replace").splitlines():
            if ln.strip() and not ln.lstrip().startswith(("#", "-")):
                m = req.match(ln)
                if m:
                    deps[("pypi", _norm(m.group(1)))] = (m.group(2) or "").replace(" ", "")
    for f in [root / "pyproject.toml", *root.glob("packages/*/pyproject.toml")]:
        if not f.exists():
            continue
        specs: list[str] = []
        try:
            import tomllib
            data = tomllib.loads(f.read_text(encoding="utf-8"))
            proj = data.get("project", {})
            specs += proj.get("dependencies", [])
            for v in (proj.get("optional-dependencies") or {}).values():
                specs += v
        except Exception:   # 3.10 has no tomllib: take quoted strings from the dependency arrays
            txt = f.read_text(encoding="utf-8", errors="replace")
            for blk in re.findall(r"dependencies\s*=\s*\[(.*?)\]", txt, re.S):
                specs += re.findall(r"[\"']([^\"']+)[\"']", blk)
        for sp in specs:
            m = req.match(sp)
            if m and not m.group(1).lower().startswith("devmem"):
                deps.setdefault(("pypi", _norm(m.group(1))), (m.group(2) or "").replace(" ", ""))
    for f in [root / "package.json", *root.glob("*/package.json")]:
        if f.exists():
            try:
                pj = json.loads(f.read_text(encoding="utf-8"))
            except ValueError:
                continue
            for k in ("dependencies", "devDependencies"):
                for n, v in (pj.get(k) or {}).items():
                    deps.setdefault(("npm", n.lower()), v)
    return [{"eco": e, "name": n, "spec": s} for (e, n), s in sorted(deps.items())]


def latest_version(eco: str, name: str) -> str | None:
    try:
        if eco == "pypi":
            return json.loads(get(f"https://pypi.org/pypi/{urllib.parse.quote(name)}/json", 10))["info"]["version"]
        return json.loads(get(f"https://registry.npmjs.org/{urllib.parse.quote(name, safe='@')}/latest", 10)).get("version")
    except Exception:
        return None


from devmem.reuse import version_behind as behind  # noqa: E402  (one implementation, shared with the agent side)


def locked_versions(root: Path) -> dict[tuple[str, str], str]:
    """Exact installed versions from lockfiles: package-lock.json, uv.lock / poetry.lock, and `==` pins in requirements."""
    root, out = Path(root), {}
    pl = root / "package-lock.json"
    if pl.exists():
        for k, v in (json.loads(pl.read_text(encoding="utf-8")).get("packages") or {}).items():
            if k.startswith("node_modules/") and "/node_modules/" not in k and v.get("version"):
                out[("npm", k[len("node_modules/"):].lower())] = v["version"]
    for lock in ("uv.lock", "poetry.lock"):
        f = root / lock
        if f.exists():
            for name, ver in re.findall(r'\[\[package\]\]\s*\nname\s*=\s*"([^"]+)"\s*\nversion\s*=\s*"([^"]+)"', f.read_text(encoding="utf-8")):
                out[("pypi", _norm(name))] = ver
    for d in scan_deps(root):
        m = re.fullmatch(r"==([\w.]+)", d["spec"] or "")
        if d["eco"] == "pypi" and m:
            out.setdefault(("pypi", d["name"]), m.group(1))
    return out


def libs_report(root: Path, online: bool = True) -> list[dict]:
    deps = scan_deps(root)
    locked = locked_versions(root)
    have = {_norm(l["name"]) for l in list_libs()}
    if online:
        with ThreadPoolExecutor(8) as ex:
            latest = list(ex.map(lambda d: latest_version(d["eco"], d["name"]), deps))
    else:
        latest = [None] * len(deps)
    return [{**d, "locked": locked.get((d["eco"], d["name"])), "latest": lt, "behind": behind(locked.get((d["eco"], d["name"])) or d["spec"], lt),
             "docs": _norm(d["name"]) in have} for d, lt in zip(deps, latest)]


def write_libs(root: Path, online: bool = True) -> list[dict]:
    rows = libs_report(root, online)
    p = Path(root) / ".devmem/libs.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return rows


def render_libraries_md(rows: list[dict]) -> str:
    out = ["# Libraries", "What this repo depends on and how current it is. **For a new requirement, check the library's CURRENT docs "
           "(`devmem docs \"<need>\"`) before copying older code in this or other repos; prefer built-in features of the latest "
           "version over hand-written helpers.** Rows marked behind use an older API surface than the latest release.", "",
           "| package | declared | latest | status | docs offline |", "|---|---|---|---|---|"]
    order = {"major": 0, "minor": 1, "unpinned": 2, "unknown": 3, "current": 4}
    for r in sorted(rows, key=lambda r: (order.get(r["behind"], 5), r["name"]))[:60]:
        out.append(f"| {r['name']} ({r['eco']}) | {r['spec'] or '-'} | {r['latest'] or '?'} | {r['behind']} | {'yes' if r['docs'] else 'no'} |")
    return "\n".join(out)


def copy_relevant(root: Path) -> list[str]:
    """Docs for the libraries this repo uses -> <repo>/.devmem/docs (agents' offline fallback when there is no hub)."""
    names = {_norm(d["name"]) for d in scan_deps(root)}
    dest = Path(root) / ".devmem/docs"
    if dest.exists():
        shutil.rmtree(dest)
    got = export(dest, names, MAX_REPO_DOCS) if names else []
    if not got and dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    return got


# ---------- upgrade notes: what changed between the version a repo uses and the latest ----------
def _ver(s: str) -> tuple:
    m = re.search(r"(\d+(?:\.\d+)*)", s or "")
    return tuple(int(x) for x in m.group(1).split(".")[:3]) + (0, 0, 0) if m else ()


def source_repo(eco: str, name: str) -> str | None:
    """owner/repo on GitHub for a package, from its PyPI project URLs or npm `repository`."""
    if eco == "pypi":
        info = json.loads(get(f"https://pypi.org/pypi/{urllib.parse.quote(name)}/json", 15))["info"]
        urls = list((info.get("project_urls") or {}).values()) + [info.get("home_page") or ""]
    else:
        meta = json.loads(get(f"https://registry.npmjs.org/{urllib.parse.quote(name, safe='@')}/latest", 15))
        repo = meta.get("repository")
        urls = [repo.get("url", "") if isinstance(repo, dict) else str(repo or ""), meta.get("homepage") or ""]
    for u in urls:
        m = re.search(r"github\.com[/:]([\w.-]+)/([\w.-]+?)(?:\.git)?(?:[/#?]|$)", u or "")
        if m:
            return f"{m.group(1)}/{m.group(2)}"
    return None


def release_notes(repo: str, frm: str, to: str | None, cap: int = 60_000) -> list[tuple[str, str]]:
    """[(tag, body)] of GitHub releases newer than `frm` up to `to`, oldest first. Set GITHUB_TOKEN to lift the API rate limit."""
    import os
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases?per_page=100",
                                 headers={"User-Agent": UA, "Accept": "application/vnd.github+json",
                                          **({"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}"} if os.environ.get("GITHUB_TOKEN") else {})})
    with urllib.request.urlopen(req, timeout=20) as r:
        rels = json.loads(r.read().decode("utf-8", "replace"))
    lo, hi = _ver(frm), _ver(to) if to else (10**9,)
    picked = sorted(((_ver(x.get("tag_name", "")), x.get("tag_name", ""), x.get("body") or "") for x in rels
                     if not x.get("prerelease") and _ver(x.get("tag_name", "")) and lo < _ver(x.get("tag_name", "")) <= hi))
    out, total = [], 0
    for _, tag, body in picked:
        total += len(body)
        if total > cap:
            break
        out.append((tag, body.strip()))
    return out


def add_upgrade_notes(root: Path, only: set[str] | None = None) -> dict:
    """For each dependency a repo uses at an older version than the latest and has docs on file, store the release notes in
    between as a page of that library's docs (`upgrade notes <from> -> <to>`), so agents read what changed offline."""
    res = {}
    dirs = {}
    for l in list_libs():   # stored names differ from package names for npm scopes (@a/b -> a-b): match on both
        dirs[_norm(l["name"])] = dirs[_norm(str(l["source"]).split(":", 1)[-1].split("==")[0])] = docs_dir() / l["name"]
    for row in libs_report(root, online=True):
        name, used = row["name"], row.get("locked") or row["spec"]
        lib = dirs.get(_norm(name), docs_dir() / "-")
        if (only and name not in only) or not row["docs"] or not row["latest"] or not (lib / "manifest.json").exists() \
                or not _ver(used) or _ver(used) >= _ver(row["latest"]):
            continue
        try:
            repo = source_repo(row["eco"], name)
            notes = release_notes(repo, used, row["latest"]) if repo else []
        except Exception as e:   # network or API limit: report per library, keep going
            res[name] = f"failed: {e}"
            continue
        if not notes:
            res[name] = "no GitHub release notes found"
            continue
        frm = re.search(r"(\d+(?:\.\d+)*)", used).group(1)
        title = f"{name} upgrade notes {frm} -> {row['latest']}"
        body = f"# {title}\n\nRelease notes between the version this repo uses and the latest. Read before using newer APIs.\n\n" + \
               "\n\n".join(f"## {tag}\n\n{b}" for tag, b in notes)
        man = json.loads((lib / "manifest.json").read_text(encoding="utf-8"))
        fn = f"upgrade-{_slug(frm)}-{_slug(row['latest'])}.md"
        (lib / fn).write_text(f"<!-- source: https://github.com/{repo}/releases -->\n{body}\n", encoding="utf-8")
        man["pages"] = [p for p in man.get("pages", []) if not p["file"].startswith("upgrade-")] + \
                       [{"file": fn, "url": f"https://github.com/{repo}/releases", "title": title, "sha1": hashlib.sha1(body.encode()).hexdigest()[:12]}]
        (lib / "manifest.json").write_text(json.dumps(man, indent=1), encoding="utf-8")
        res[name] = f"{len(notes)} releases {frm} -> {row['latest']}"
    return res
