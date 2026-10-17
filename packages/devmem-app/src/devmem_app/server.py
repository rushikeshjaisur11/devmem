"""Local API + static UI for the desktop app (`devmem-app ui`). Binds to 127.0.0.1 only; mutating calls need the
X-Devmem header and Host must be localhost (blocks drive-by browser requests and DNS rebinding)."""

import json
import os
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

from devmem import lineage as agent_lineage
from devmem import memory as m
from devmem.index import Index
from devmem.knowledge import check_knowledge
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import chat as chat_mod
from . import hygiene as hyg
from . import metrics as metrics_mod
from . import lineage as lin_mod
from . import memory as am
from .indexer import _git, git_branch, git_branches, git_dirty, git_head, index_repo
from .paths import devmem_home
from .publisher import _module_of, commit_branch, publish

UI_DIST = Path(__file__).parent / "ui_dist"
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "testserver"}


def _is_plan(text: str) -> bool:
    from . import intent
    return bool(intent.STEPS.search(text) or intent.PLAN_HINT.search(text))


def create_app() -> FastAPI:
    app = FastAPI(title="devmem", docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = (request.headers.get("host") or "").split(":")[0]
        if host not in ALLOWED_HOSTS:
            return JSONResponse({"detail": "bad host"}, status_code=403)
        if request.method in ("POST", "PUT", "DELETE") and request.headers.get("x-devmem") != "1":
            return JSONResponse({"detail": "missing X-Devmem header"}, status_code=403)
        return await call_next(request)

    # ---------- state ----------
    def ws() -> dict:
        w = devmem_home() / "workspace.json"
        return json.loads(w.read_text()) if w.exists() else {"repos": {}}

    def save_ws(d: dict):
        w = devmem_home() / "workspace.json"
        w.parent.mkdir(parents=True, exist_ok=True)
        w.write_text(json.dumps(d, indent=1))

    def root_of(name: str) -> Path:
        repos = ws()["repos"]
        if name not in repos:
            raise HTTPException(404, f"unknown repo {name}")
        return Path(repos[name])

    cache: dict[str, tuple[float, Index]] = {}

    def ix_of(name: str) -> Index:
        root = root_of(name)
        p = root / ".devmem/index.json"
        if not p.exists():
            raise HTTPException(409, f"{name} is not indexed yet")
        mt = p.stat().st_mtime
        hit = cache.get(name)
        if not hit or hit[0] != mt:
            cache[name] = (mt, Index.load(root))
        return cache[name][1]

    def safe_file(root: Path, rel: str) -> Path:
        p = (root / rel).resolve()
        try:
            p.relative_to(root.resolve())
        except ValueError:
            raise HTTPException(400, "path outside repo")
        return p

    def repo_summary(name: str, path: str) -> dict:
        root = Path(path)
        d = {"name": name, "path": path, "indexed": False}
        ip = root / ".devmem/index.json"
        if ip.exists():
            idx = json.loads(ip.read_text(encoding="utf-8"))
            head = git_head(root)
            problems = check_knowledge(root, head) if (root / ".devmem/knowledge").exists() else ["not published"]
            langs = Counter(f["lang"] for f in idx["files"].values() if f["lang"] != "other")
            d.update({"indexed": True, "symbols": sum(1 for s in idx["symbols"] if s["kind"] != "module"),
                      "files": len(idx["files"]), "edges": len(idx["edges"]), "commit": idx.get("commit"),
                      "head": head, "branch": git_branch(root), "stale": bool(head and idx.get("commit") != head), "langs": dict(langs),
                      "knowledge_problems": problems, "generated": idx.get("generated")})
            hp = root / ".devmem/hygiene.json"
            if hp.exists():
                d["hygiene"] = json.loads(hp.read_text()).get("counts", {})
        return d


    # ---------- repos ----------
    class AddRepo(BaseModel):
        path: str | None = None      # local clone
        url: str | None = None       # or a git URL to clone into ~/.devmem/clones/<name>
        branch: str | None = None
        name: str | None = None

    URL_OK = re.compile(r"^(https?://|ssh://|git://|git@[\w.-]+:)[^\s]+$")

    def clone_url(url: str, name: str | None, branch: str | None) -> tuple[str, Path]:
        url = url.strip()
        if not URL_OK.match(url):   # also rejects `-flag` and `ext::` transports
            raise HTTPException(400, "use an https://, ssh:// or git@host:path URL")
        if branch and not re.fullmatch(r"[\w./-]+", branch) or (branch or "").startswith("-"):
            raise HTTPException(400, "bad branch name")
        name = name or re.sub(r"\.git$", "", url.rstrip("/").split("/")[-1].split(":")[-1]) or "repo"
        if not re.fullmatch(r"[\w.-]+", name):
            raise HTTPException(400, "bad repo name")
        dest = devmem_home() / "clones" / name
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}   # fail instead of hanging on a credential prompt
        try:
            if (dest / ".git").exists():
                r = subprocess.run(["git", "-C", str(dest), "fetch", "--all", "--prune"], capture_output=True, text=True, env=env, timeout=600)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                cmd = ["git", "clone", *(["--branch", branch] if branch else []), "--", url, str(dest)]
                r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=900)
        except (subprocess.TimeoutExpired, OSError) as e:
            raise HTTPException(422, f"git: {e}")
        if r.returncode:
            raise HTTPException(422, "git failed: " + (r.stderr or r.stdout).strip()[-300:])
        return name, dest

    @app.get("/api/repos")
    def list_repos():
        return [repo_summary(n, p) for n, p in sorted(ws()["repos"].items())]

    @app.post("/api/repos")
    def add_repo(body: AddRepo):
        if body.url:
            name, root = clone_url(body.url, body.name, body.branch)
            if body.branch and git_branch(root) != body.branch:
                if body.branch in git_branches(root):
                    r = _git(root, "checkout", body.branch, "--")
                    if r.returncode:
                        raise HTTPException(422, (r.stderr or r.stdout).strip()[-300:])
                else:
                    raise HTTPException(400, f"unknown branch {body.branch}")
        elif body.path:
            root = Path(body.path).expanduser().resolve()
            if not root.is_dir():
                raise HTTPException(400, "not a directory")
            name = body.name or root.name
        else:
            raise HTTPException(400, "give a local path or a git URL")
        w = ws(); w["repos"][name] = str(root); save_ws(w)
        index_repo(root, name)
        return repo_summary(name, str(root))

    @app.delete("/api/repos/{name}")
    def remove_repo(name: str):
        w = ws(); w["repos"].pop(name, None); save_ws(w)
        cache.pop(name, None)
        return {"ok": True}

    @app.post("/api/repos/{name}/index")
    def reindex(name: str):
        root = root_of(name)
        idx = index_repo(root, name)
        return {**repo_summary(name, str(root)), "stats": idx["stats"]}

    @app.get("/api/repos/{name}/branches")
    def branches(name: str):
        root = root_of(name)
        return {"current": git_branch(root), "branches": git_branches(root), "dirty": git_dirty(root)}

    class Branch(BaseModel):
        branch: str

    @app.post("/api/repos/{name}/branch")
    def switch_branch(name: str, body: Branch):
        """git checkout <branch> in the clone, then re-index it. Refuses when tracked files have uncommitted changes."""
        root = root_of(name)
        if body.branch not in git_branches(root):
            raise HTTPException(400, f"unknown branch {body.branch}")
        dirty = git_dirty(root)
        if dirty:
            raise HTTPException(409, "uncommitted changes would be affected; commit or stash first: " + ", ".join(dirty[:5]))
        if body.branch != git_branch(root):
            r = _git(root, "checkout", body.branch, "--")
            if r.returncode:
                raise HTTPException(422, (r.stderr or r.stdout).strip()[-300:])
        idx = index_repo(root, name)
        return {**repo_summary(name, str(root)), "stats": idx["stats"]}

    class Publish(BaseModel):
        commit: bool = False

    @app.post("/api/repos/{name}/publish")
    def do_publish(name: str, body: Publish):
        root = root_of(name)
        idx = index_repo(root, name)
        try:
            files = publish(root, idx, ws()["repos"])
        except RuntimeError as e:
            raise HTTPException(422, str(e))
        branch = commit_branch(root) if body.commit else None
        return {"written": files, "branch": branch}

    @app.post("/api/repos/{name}/hygiene")
    def run_hygiene(name: str):
        root = root_of(name)
        idx = index_repo(root, name)
        h = hyg.run_hygiene(root, idx, name)
        publish(root, idx, ws()["repos"])
        return {"counts": h["counts"], "tools": h["tools"]}

    # ---------- explorer ----------
    @app.get("/api/repos/{name}/tree")
    def tree(name: str):
        ix = ix_of(name)
        counts = Counter(s["file"] for s in ix.symbols.values() if s["kind"] != "module")
        root: dict = {"name": name, "path": "", "type": "dir", "children": []}
        for path in sorted(ix.data["files"]):
            node = root
            parts = path.split("/")
            for i, part in enumerate(parts):
                last = i == len(parts) - 1
                p = "/".join(parts[: i + 1])
                nxt = next((c for c in node["children"] if c["name"] == part), None)
                if not nxt:
                    nxt = {"name": part, "path": p, "type": "file" if last else "dir"}
                    if last:
                        nxt.update({"lang": ix.data["files"][path]["lang"], "symbols": counts.get(path, 0)})
                    else:
                        nxt["children"] = []
                    node["children"].append(nxt)
                node = nxt
        return root

    @app.get("/api/repos/{name}/file")
    def file_view(name: str, path: str):
        ix = ix_of(name)
        root = root_of(name)
        if path not in ix.data["files"]:
            raise HTTPException(404, "file not indexed")
        f = safe_file(root, path)
        text = f.read_text(encoding="utf-8", errors="replace")
        rec = ix.data["files"][path]
        issues = []
        hp = root / ".devmem/hygiene.json"
        if hp.exists():
            issues = [i for i in json.loads(hp.read_text())["issues"] if i["path"] == path]
        return {"path": path, "lang": rec["lang"], "text": text, "symbols": ix.outline(path), "issues": issues,
                "tables": rec.get("tables", [])}

    @app.get("/api/repos/{name}/search")
    def search(name: str, q: str, limit: int = 20):
        ix = ix_of(name)
        return [{**s, "score": round(sc, 1)} for sc, s in ix.find(q, limit)]

    @app.get("/api/repos/{name}/symbol")
    def symbol(name: str, id: str):
        ix = ix_of(name)
        s = ix.symbols.get(id)
        if not s:
            raise HTTPException(404, "unknown symbol")
        callers = [{"id": e["src"], "type": e["type"], "conf": e["conf"]} for e in ix.inc.get(id, [])]
        callees = [{"id": e["dst"], "type": e["type"], "conf": e["conf"]} for e in ix.out.get(id, [])]
        return {"symbol": s, "source": ix.source(s), "callers": callers, "callees": callees}

    # ---------- impact / lineage ----------
    @app.get("/api/repos/{name}/impact")
    def impact(name: str, target: str):
        ix = ix_of(name)
        lin = agent_lineage.load(ix.root)
        if lin and not ix.resolve(target) and target not in agent_lineage.known_tables(lin):
            sc = agent_lineage.split_column(lin, target)
            if sc:
                return {"kind": "column", **agent_lineage.column_impact(lin, *sc)}
        r = ix.impact(target)
        if "table" in r:
            return {"kind": "table", **r}
        return {"kind": "symbol", **r}

    @app.get("/api/impact-column")
    def impact_column(column: str):
        lin = lin_mod.combine({n: Path(p) for n, p in ws()["repos"].items()})
        sc = agent_lineage.split_column(lin, column)
        if not sc:
            raise HTTPException(404, f"unknown column {column}")
        return {"kind": "column", **agent_lineage.column_impact(lin, *sc)}

    @app.get("/api/lineage")
    def lineage_graph(repo: str | None = None, table: str | None = None):
        """Tables with readers/writers across repos; with `table`, the column-level edges around it."""
        repos = {n: Path(p) for n, p in ws()["repos"].items() if not repo or n == repo}
        tables: dict[str, dict] = {}
        for rname, root in repos.items():
            ip = root / ".devmem/index.json"
            if not ip.exists():
                continue
            idx = json.loads(ip.read_text(encoding="utf-8"))
            for e in idx["edges"]:
                if e["dst"].startswith("table:") and e["type"] in ("reads", "writes"):
                    t = tables.setdefault(e["dst"][6:], {"name": e["dst"][6:], "readers": [], "writers": [], "columns": []})
                    t["readers" if e["type"] == "reads" else "writers"].append({"repo": rname, "job": e["src"]})
        lin = lin_mod.combine(repos)
        for t, cols in lin["schemas"].items():
            tables.setdefault(t, {"name": t, "readers": [], "writers": [], "columns": []})["columns"] = cols
        out = {"tables": sorted(tables.values(), key=lambda t: t["name"])}
        if table:
            out["column_edges"] = [e for e in lin["column_edges"]
                                   if e["src"].rsplit(".", 1)[0] == table or e["dst"].rsplit(".", 1)[0] == table]
            out["column_reads"] = [r for r in lin["reads"] if r["col"].rsplit(".", 1)[0] == table]
        return out

    # ---------- offline library docs ----------
    class DocAdd(BaseModel):
        spec: str
        name: str | None = None
        follow: bool = True

    @app.get("/api/docs")
    def docs_list():
        from . import docs as dm
        return dm.list_libs()

    @app.post("/api/docs")
    def docs_add(body: DocAdd):
        from . import docs as dm
        try:
            m = dm.add(body.spec.strip(), body.name or None, body.follow)
        except Exception as e:
            raise HTTPException(422, f"could not fetch: {e}")
        return {"name": m["name"], "version": m["version"], "pages": len(m["pages"]), "bytes": m["bytes"]}

    @app.post("/api/docs/refresh")
    def docs_refresh():
        from . import docs as dm
        return dm.refresh()

    @app.delete("/api/docs/{name}")
    def docs_remove(name: str):
        from . import docs as dm
        return {"ok": dm.remove(name)}

    @app.post("/api/repos/{name}/libs")
    def repo_libs(name: str):
        from . import docs as dm
        return dm.write_libs(root_of(name), True)

    @app.get("/api/docs/search")
    def docs_search(q: str, repo: str, limit: int = 5):
        from devmem import docs as core_docs
        return core_docs.search(root_of(repo), q, max(1, min(limit, 10)))

    # ---------- central knowledge hub ----------
    class HubSet(BaseModel):
        spec: str

    class HubPush(BaseModel):
        push: bool = False
        summarize: bool = False
        prune: bool = False

    @app.get("/api/hub")
    def hub_status():
        from . import hub as hubmod
        return {**hubmod.status(), "llm": chat_mod.llm_status()}

    @app.post("/api/hub")
    def hub_set(body: HubSet):
        from . import hub as hubmod
        try:
            return hubmod.configure(body.spec)
        except (RuntimeError, subprocess.TimeoutExpired) as e:
            raise HTTPException(422, str(e))

    @app.post("/api/hub/push")
    def hub_push(body: HubPush):
        from . import hub as hubmod
        prov = None
        if body.summarize:
            try:
                prov = chat_mod.provider_from_env()
            except Exception:
                prov = None
            if prov is None:
                raise HTTPException(422, "summaries need DEVMEM_VERTEX_PROJECT and google-genai installed")
        try:
            return hubmod.push(ws()["repos"], 400, prov, True, body.push, body.prune)
        except (RuntimeError, subprocess.TimeoutExpired) as e:
            raise HTTPException(422, str(e))

    # ---------- graph ----------
    @app.get("/api/graph/overview")
    def graph_overview():
        nodes, edges, tables = [], [], defaultdict(lambda: {"writers": set(), "readers": set()})
        for rname, rpath in ws()["repos"].items():
            ip = Path(rpath) / ".devmem/index.json"
            if not ip.exists():
                continue
            idx = json.loads(ip.read_text(encoding="utf-8"))
            sym = {s["id"]: s for s in idx["symbols"]}
            nodes.append({"data": {"id": f"repo:{rname}", "label": rname, "kind": "repo"}})
            mods = Counter(_module_of(s["file"]) for s in idx["symbols"] if s["kind"] != "module" and not s["is_test"])
            for f in idx["files"]:   # files without symbols (SQL, config) still need a module node
                if f.split("/")[0] not in ("tests",) and f.endswith((".py", ".sql")):
                    mods.setdefault(_module_of(f), 0)
            for mod, n in mods.items():
                nodes.append({"data": {"id": f"{rname}:{mod}", "label": mod, "parent": f"repo:{rname}", "kind": "module", "size": n}})
            agg = Counter()
            for e in idx["edges"]:
                s, d = sym.get(e["src"]), sym.get(e["dst"])
                if e["type"] == "calls" and s and d:
                    a, b = _module_of(s["file"]), _module_of(d["file"])
                    if a != b:
                        agg[(a, b)] += 1
                elif e["dst"].startswith("table:") and e["type"] in ("reads", "writes"):
                    src_file = e["src"].split("#")[0]
                    mod = _module_of(src_file)
                    tables[e["dst"][6:]]["readers" if e["type"] == "reads" else "writers"].add(f"{rname}:{mod}")
                    tables[e["dst"][6:]].setdefault("repos", set()).add(rname)
            for (a, b), n in agg.items():
                edges.append({"data": {"id": f"{rname}:{a}>{b}", "source": f"{rname}:{a}", "target": f"{rname}:{b}",
                                       "kind": "calls", "weight": n}})
        for t, d in tables.items():
            if len(d.get("repos", ())) < 2:
                continue   # only tables that link repos
            nodes.append({"data": {"id": f"table:{t}", "label": t, "kind": "table"}})
            for w in d["writers"]:
                edges.append({"data": {"id": f"w:{w}>{t}", "source": w, "target": f"table:{t}", "kind": "writes"}})
            for r in d["readers"]:
                edges.append({"data": {"id": f"r:{t}>{r}", "source": f"table:{t}", "target": r, "kind": "reads"}})
        return {"nodes": nodes, "edges": edges}

    @app.get("/api/repos/{name}/graph")
    def graph_focus(name: str, focus: str, depth: int = 1, limit: int = 60):
        ix = ix_of(name)
        if focus not in ix.symbols:
            raise HTTPException(404, "unknown symbol")
        seen, frontier, edges = {focus}, [focus], {}
        for _ in range(max(1, min(depth, 3))):
            nxt = []
            for cur in frontier:
                for e in [*ix.out.get(cur, []), *ix.inc.get(cur, [])]:
                    if e["type"] not in ("calls", "tests"):
                        continue
                    for end in (e["src"], e["dst"]):
                        if end not in seen and len(seen) < limit and end in ix.symbols:
                            seen.add(end); nxt.append(end)
                    if e["src"] in seen and e["dst"] in seen:
                        edges[(e["src"], e["dst"], e["type"])] = e["conf"]
            frontier = nxt
        nodes = [{"data": {"id": i, "label": ix.symbols[i]["qualname"], "file": ix.symbols[i]["file"],
                           "kind": "test" if ix.symbols[i]["is_test"] else ix.symbols[i]["kind"], "focus": i == focus}} for i in seen]
        es = [{"data": {"id": f"{s}>{d}:{t}", "source": s, "target": d, "kind": t, "conf": c}} for (s, d, t), c in edges.items()]
        return {"nodes": nodes, "edges": es}

    # ---------- hygiene / metrics ----------
    @app.get("/api/repos/{name}/hygiene")
    def hygiene_view(name: str):
        root = root_of(name)
        hp = root / ".devmem/hygiene.json"
        if not hp.exists():
            return {"available": False}
        h = json.loads(hp.read_text())
        trend = []
        hist = hyg.HISTORY_DIR / f"{name}.jsonl"
        if hist.exists():
            trend = [json.loads(l) for l in hist.read_text().splitlines() if l.strip()]
        return {"available": True, "counts": h["counts"], "prev_counts": h.get("prev_counts", {}), "tools": h["tools"],
                "issues": h["issues"][:500], "clones": h["clones"], "ts": h["ts"], "trend": trend}

    @app.get("/api/repos/{name}/metrics")
    def metrics(name: str):
        root = root_of(name)
        idx_path = root / ".devmem/index.json"
        if not idx_path.exists():
            raise HTTPException(409, "not indexed")
        idx = json.loads(idx_path.read_text(encoding="utf-8"))
        use = m.usefulness(root)
        lessons = m.load_lessons(root)
        hist = hyg.HISTORY_DIR / f"{name}.jsonl"
        hh = root / ".devmem/history.json"
        return {"symbols": sum(1 for s in idx["symbols"] if s["kind"] != "module"),
                "tests": sum(1 for s in idx["symbols"] if s["is_test"]), "files": len(idx["files"]),
                "lessons": len(lessons), "rules": len(m.load_rules(root)),
                "feedback": {"useful": sum(v[0] for v in use.values()), "useless": sum(v[1] for v in use.values())},
                "knowledge_problems": check_knowledge(root, git_head(root)) if (root / ".devmem/knowledge").exists() else ["not published"],
                "hygiene_trend": [json.loads(l) for l in hist.read_text().splitlines() if l.strip()] if hist.exists() else [],
                "hotspots": json.loads(hh.read_text()).get("hotspots", []) if hh.exists() else [],
                "pr_metrics": metrics_mod.summary(root)}

    @app.post("/api/metrics/devin")
    async def ingest_devin(request: Request):
        import tempfile
        raw = await request.body()
        with tempfile.NamedTemporaryFile("wb", suffix=".json", delete=False) as t:
            t.write(raw)
        try:
            return metrics_mod.ingest_devin(Path(t.name))
        except (ValueError, KeyError, AttributeError, TypeError) as e:
            raise HTTPException(422, f"not a session export: {e}")
        finally:
            Path(t.name).unlink(missing_ok=True)

    # ---------- memory ----------
    @app.get("/api/memory")
    def memory_view():
        out = {"repos": {}, "hub_lessons": [], "hub_rules": [], "proposals": am.list_proposals()}
        for name, p in ws()["repos"].items():
            root = Path(p)
            out["repos"][name] = {"lessons": m.load_lessons(root), "rules": m.load_rules(root),
                                  "usefulness": {k: list(v) for k, v in m.usefulness(root).items()}}
        hub = am.hub_dir()
        hl = hub / "lessons"
        if hl.exists():
            for f in sorted(hl.glob("*.md")):
                meta, body = m.parse_front(f.read_text(encoding="utf-8"))
                meta.update({"problem": m.section(body, "Problem"), "fix": m.section(body, "Fix")})
                out["hub_lessons"].append(meta)
        hr = hub / "rules"
        if hr.exists():
            out["hub_rules"] = [{**m.parse_front(f.read_text(encoding="utf-8"))[0], "text": m.parse_front(f.read_text(encoding="utf-8"))[1].strip()}
                                for f in sorted(hr.glob("*.md"))]
        return out

    class LessonIn(BaseModel):
        title: str
        problem: str
        fix: str
        tags: list[str] = []

    @app.post("/api/repos/{name}/lessons")
    def add_lesson(name: str, body: LessonIn):
        try:
            status, lid = m.write_lesson(root_of(name), body.title, body.problem, body.fix, name, body.tags, (), (), "human", False)
        except ValueError as e:
            raise HTTPException(422, str(e))
        return {"status": status, "id": lid}

    @app.put("/api/repos/{name}/lessons/{lid}")
    def edit_lesson(name: str, lid: str, body: LessonIn):
        root = root_of(name)
        f = root / m.LESSONS_DIR / f"{lid}.md"
        if not re.fullmatch(r"[a-z0-9-]+", lid) or not f.exists():
            raise HTTPException(404, "no such lesson")
        meta, _ = m.parse_front(f.read_text(encoding="utf-8"))
        meta.update({"title": body.title, "tags": sorted({t.lower() for t in body.tags})})
        f.write_text(m.dump_front(meta, f"## Problem\n{body.problem}\n\n## Fix\n{body.fix}"), encoding="utf-8")
        return {"ok": True}

    class ShareIn(BaseModel):
        repos: list[str] = []
        everywhere: bool = False

    @app.post("/api/memory/sync")
    def memory_sync():
        return am.sync(ws()["repos"])

    @app.post("/api/memory/lessons/{lid}/share")
    def memory_share(lid: str, body: ShareIn):
        try:
            return {"repos": am.share(lid, body.repos, body.everywhere)}
        except FileNotFoundError:
            raise HTTPException(404, "lesson is not in the hub yet; run sync first")

    class ToRule(BaseModel):
        applies_to: str = "**"
        repo: str | None = None
        severity: str = "rule"

    @app.post("/api/memory/lessons/{lid}/to-rule")
    def memory_to_rule(lid: str, body: ToRule):
        try:
            p = am.lesson_to_rule(lid, body.applies_to, root_of(body.repo) if body.repo else None, body.severity)
        except FileNotFoundError:
            raise HTTPException(404, "lesson is not in the hub yet; run sync first")
        return {"path": str(p)}

    class Approve(BaseModel):
        repo: str | None = None
        fix: str | None = None
        severity: str = "rule"

    @app.post("/api/memory/proposals/{pid}/approve")
    def proposal_approve(pid: str, body: Approve):
        try:
            p = am.approve(pid, root_of(body.repo) if body.repo else None, body.severity, body.fix)
        except ValueError as e:
            raise HTTPException(422, str(e))
        except FileNotFoundError:
            raise HTTPException(404, "no such proposal")
        return {"path": str(p)}

    @app.post("/api/memory/proposals/{pid}/reject")
    def proposal_reject(pid: str):
        try:
            am.reject(pid)
        except FileNotFoundError:
            raise HTTPException(404, "no such proposal")
        return {"ok": True}

    # ---------- chat ----------
    class Chat(BaseModel):
        question: str
        repos: list[str] | None = None
        llm: bool = False
        limit: int = 8
        target: str | None = None        # repo the agent will work in (only needed for a new requirement with several repos)
        force: str | None = None         # "ask" | "implement": the user's correction of the automatic decision
        detail: str = "pointers"         # "code": also include source of existing solutions in an agent prompt
        fetch_docs: bool = True

    @app.get("/api/chat/status")
    def chat_status():
        return chat_mod.llm_status()

    @app.post("/api/chat")
    def chat(body: Chat):
        """One entry point: decides whether the message is a question or a new requirement (see intent.py) and answers accordingly.
        Read-only either way: a requirement yields a prompt for another agent; nothing is written into any repo."""
        from . import intent as intent_mod
        from .implement import build
        allr = ws()["repos"]
        names = body.repos or list(allr)
        unknown = [n for n in names if n not in allr]
        if unknown:
            raise HTTPException(404, f"unknown repo {unknown[0]}")
        verdict = intent_mod.classify(body.question)
        if body.force in ("ask", "implement"):
            verdict = {"intent": body.force, "reason": "you asked for this"}
        if verdict["intent"] == "implement":
            indexed = [n for n in allr if (Path(allr[n]) / ".devmem/index.json").exists()]
            if not indexed:
                raise HTTPException(409, "no indexed repo yet: onboard one on the Repos page")
            target = body.target if body.target in indexed else None
            if target is None:
                cand = intent_mod.pick_target(body.question, [n for n in (body.repos or []) if n in indexed], indexed)
                if len(cand) != 1:
                    return {**verdict, "needs_repo": cand}
                target = cand[0]
            try:
                prov = chat_mod.provider_from_env()
            except Exception:
                prov = None
            plan = _is_plan(body.question)
            try:
                out = build(target, allr, "" if plan else body.question, body.question if plan else "",
                            "code" if body.detail == "code" else "pointers", body.fetch_docs, prov)
                return {**verdict, "target": target, **out}
            except ValueError as e:
                raise HTTPException(422, str(e))
        if body.llm and not chat_mod.llm_status()["configured"]:
            raise HTTPException(409, "no LLM configured: set DEVMEM_VERTEX_PROJECT (and pip install google-genai)")
        try:
            return {**verdict, **chat_mod.ask(body.question, {n: Path(allr[n]) for n in names}, min(max(body.limit, 1), 20), use_llm=body.llm)}
        except ImportError as e:
            raise HTTPException(409, f"LLM client not installed: {e}")

    # ---------- share code between repos ----------
    COMMENT = {".py": "#", ".sh": "#", ".yaml": "#", ".yml": "#", ".sql": "--", ".js": "//", ".ts": "//", ".tsx": "//"}

    @app.get("/api/share/preview")
    def share_preview(repo: str, file: str, symbol: str | None = None):
        ix = ix_of(repo)
        root = root_of(repo)
        if file not in ix.data["files"]:
            raise HTTPException(404, "file not indexed")
        if symbol:
            s = ix.symbols.get(symbol)
            if not s or s["file"] != file:
                raise HTTPException(404, "unknown symbol")
            text = ix.source(s)
        else:
            text = safe_file(root, file).read_text(encoding="utf-8", errors="replace")
        imports = [l for l in safe_file(root, file).read_text(encoding="utf-8", errors="replace").splitlines()
                   if re.match(r"\s*(import|from)\s+\w", l)] if file.endswith(".py") else []
        return {"text": text, "review_imports": imports}

    class ShareCode(BaseModel):
        from_repo: str
        file: str
        symbol: str | None = None
        to_repo: str
        dest: str
        append: bool = False

    @app.post("/api/share")
    def share_code(body: ShareCode):
        ix = ix_of(body.from_repo)
        src_root, dst_root = root_of(body.from_repo), root_of(body.to_repo)
        prev = share_preview(body.from_repo, body.file, body.symbol)
        dest = safe_file(dst_root, body.dest)
        if dest.exists() and not body.append:
            raise HTTPException(409, "destination exists (use append to add to it)")
        commit = (ix.data.get("commit") or "unknown")[:10]
        ref = f"{body.from_repo}:{body.file}" + (f"#{ix.symbols[body.symbol]['qualname']}" if body.symbol else "") + f"@{commit}"
        cm = COMMENT.get(Path(body.file).suffix)
        header = f"{cm} devmem: copied from {ref}\n" if cm else ""
        dest.parent.mkdir(parents=True, exist_ok=True)
        text = header + prev["text"].rstrip() + "\n"
        if dest.exists():
            dest.write_text(dest.read_text(encoding="utf-8").rstrip() + "\n\n\n" + text, encoding="utf-8")
        else:
            dest.write_text(text, encoding="utf-8")
        return {"written": body.dest, "origin": ref, "review_imports": prev["review_imports"], "staged": False,
                "note": "left unstaged for your review" + ("" if cm else "; no origin header for this file type")}

    # ---------- static UI ----------
    if UI_DIST.exists():
        app.mount("/assets", StaticFiles(directory=UI_DIST / "assets"), name="assets") if (UI_DIST / "assets").exists() else None

        @app.get("/{full_path:path}")
        def spa(full_path: str):
            f = (UI_DIST / full_path).resolve()
            if full_path and f.is_file() and UI_DIST.resolve() in f.parents:
                return FileResponse(f)
            return FileResponse(UI_DIST / "index.html")
    else:
        @app.get("/")
        def no_ui():
            return {"detail": "UI not built; run `npm run build` in ui/ (the API is available under /api)"}

    return app


app = create_app()
