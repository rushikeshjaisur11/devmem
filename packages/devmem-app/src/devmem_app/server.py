"""API + static UI (`devmem-app ui`).

Local (the default): binds to 127.0.0.1, Host must be localhost and mutating calls need the X-Devmem header, which blocks
drive-by browser requests and DNS rebinding.
Deployed (`devmem-app ui --host 0.0.0.0`, or DEVMEM_TOKEN set): every /api call also needs the access token, sent as the
cookie the sign-in page sets or as `Authorization: Bearer`. Repos are then onboarded by upload or Git URL; naming a path
on the server is off unless DEVMEM_ALLOW_PATHS=1."""

import hmac
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

from devmem import lineage as agent_lineage
from devmem import memory as m
from devmem import usage as agent_usage
from devmem.index import Index
from devmem.knowledge import AGENTS_INDEX_BUDGET, MAP_BUDGET, check_knowledge, est_tokens, managed_block, parse_header
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import crossrepo
from . import definitions as defs
from . import settings as settings_mod
from . import uploads
from . import chat as chat_mod
from . import hygiene as hyg
from . import metrics as metrics_mod
from . import lineage as lin_mod
from . import memory as am
from .indexer import AccuracyError, _git, git_branch, git_branches, git_dirty, git_head
from .indexer import index_repo as _index_repo
from .paths import devmem_home
from .publisher import _module_of, commit_branch, publish
from devmem.schema import atomic_write

UI_DIST = Path(__file__).parent / "ui_dist"
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "testserver"}
COOKIE = "devmem_token"
OPEN_API = {"/api/config", "/api/login", "/api/logout"}   # reachable before sign-in


def access_token() -> str:
    """Set => deployed mode. Read per request, so a test or a process manager can set it after import."""
    return os.environ.get("DEVMEM_TOKEN", "").strip()


def paths_allowed() -> bool:
    return not access_token() or os.environ.get("DEVMEM_ALLOW_PATHS") == "1"


def _signed_in(request: Request, token: str) -> bool:
    got = request.cookies.get(COOKIE) or ""
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        got = auth[7:].strip()
    return bool(got) and hmac.compare_digest(got.encode(), token.encode())


def _is_plan(text: str) -> bool:
    from . import intent
    return bool(intent.STEPS.search(text) or intent.PLAN_HINT.search(text))


def create_app() -> FastAPI:
    app = FastAPI(title="devmem", docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = (request.headers.get("host") or "").split(":")[0]
        token = access_token()
        extra = {h.strip() for h in os.environ.get("DEVMEM_ALLOWED_HOSTS", "").split(",") if h.strip()}
        # local: localhost only. Deployed: the token is the guard, so any host is fine unless DEVMEM_ALLOWED_HOSTS narrows it.
        if host not in ALLOWED_HOSTS | extra and not (token and not extra):
            return JSONResponse({"detail": "bad host"}, status_code=403)
        if request.method in ("POST", "PUT", "DELETE") and request.headers.get("x-devmem") != "1":
            return JSONResponse({"detail": "missing X-Devmem header"}, status_code=403)
        path = request.url.path
        if token and path.startswith("/api/") and path not in OPEN_API and not _signed_in(request, token):
            return JSONResponse({"detail": "sign in with the access token"}, status_code=401)
        return await call_next(request)

    # ---------- deployment: sign-in and what this server allows ----------
    class Login(BaseModel):
        token: str

    @app.get("/api/config")
    def config(request: Request):
        token = access_token()
        from importlib import metadata
        try:
            ver = metadata.version("devmem-app")
        except metadata.PackageNotFoundError:
            ver = ""
        return {"deployed": bool(token), "signed_in": not token or _signed_in(request, token), "paths_allowed": paths_allowed(),
                "max_upload_mb": uploads.max_upload_bytes() // (1024 * 1024), "version": ver}

    @app.post("/api/login")
    def login(body: Login, request: Request):
        token = access_token()
        if not token:
            return {"ok": True}
        if not hmac.compare_digest(body.token.strip().encode(), token.encode()):
            time.sleep(1)   # slows guessing; this handler runs in the thread pool, so other requests are not held up
            raise HTTPException(401, "that is not the access token")
        r = JSONResponse({"ok": True})
        https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        r.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=https, max_age=30 * 24 * 3600, path="/")
        return r

    @app.post("/api/logout")
    def logout():
        r = JSONResponse({"ok": True})
        r.delete_cookie(COOKIE, path="/")
        return r

    def index_repo(root, name):
        try:
            return _index_repo(root, name)
        except AccuracyError as e:     # surfaced as an error in the UI instead of quietly indexing with less accuracy
            raise HTTPException(422, f"accuracy requirement not met: {e}")

    # ---------- state ----------
    def ws() -> dict:
        w = devmem_home() / "workspace.json"
        return json.loads(w.read_text()) if w.exists() else {"repos": {}}

    def save_ws(d: dict):
        w = devmem_home() / "workspace.json"
        w.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(w, json.dumps(d, indent=1))

    def root_of(name: str) -> Path:
        repos = ws()["repos"]
        if name not in repos:
            raise HTTPException(404, f"unknown repo {name}")
        return Path(repos[name])

    cache: dict[str, tuple[float, Index]] = {}
    summary_cache: dict[str, tuple[tuple, dict]] = {}

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

    def repo_summary(name: str, path: str) -> dict:
        root = Path(path)
        d = {"name": name, "path": path, "indexed": False}
        ip = root / ".devmem/index.json"
        d["uploaded"] = uploads.uploads_dir().resolve() in root.resolve().parents
        running = [j for j in jobs.values() if j["repo"] == name and j["state"] == "running"]
        if running:
            d["working"] = {"kind": running[0]["kind"], "job": running[0]["id"], "seconds": round(time.time() - running[0]["started"])}
        if ip.exists():
            st = ip.stat()
            hit = summary_cache.get(str(ip))
            if not hit or hit[0] != (st.st_mtime_ns, st.st_size):   # every page lists the repos: parse a large index.json once per change
                idx = json.loads(ip.read_text(encoding="utf-8"))
                langs = Counter(f["lang"] for f in idx["files"].values() if f["lang"] != "other")
                hit = summary_cache[str(ip)] = ((st.st_mtime_ns, st.st_size), {
                    "symbols": sum(1 for s in idx["symbols"] if s["kind"] != "module"), "files": len(idx["files"]), "edges": len(idx["edges"]),
                    "commit": idx.get("commit"), "langs": dict(langs), "generated": idx.get("generated"),
                    "accuracy": (idx.get("stats") or {}).get("accuracy")})
            head = git_head(root)
            problems = check_knowledge(root, head) if (root / ".devmem/knowledge").exists() else ["not published"]
            d.update({"indexed": True, **hit[1], "head": head, "branch": git_branch(root), "stale": bool(head and hit[1]["commit"] != head),
                      "knowledge_problems": problems})
            hp = root / ".devmem/hygiene.json"
            if hp.exists():
                d["hygiene"] = json.loads(hp.read_text()).get("counts", {})
            d["agent_tokens"] = agent_tokens(root)
            d["usage"] = agent_usage.summary(root)
            from devmem import connect as cn
            d["connected"] = [k for k, v in cn.status(root).items() if v]
        return d

    def agent_tokens(root: Path) -> dict:
        """What an agent pays to read the published knowledge: the AGENTS.md block every session, then each topic file on demand."""
        ag = root / "AGENTS.md"
        blk = managed_block(ag.read_text(encoding="utf-8")) if ag.exists() else None
        files = {}
        for f in sorted((root / ".devmem/knowledge").glob("*.md")):
            h = parse_header(f.read_text(encoding="utf-8"))
            files[f.name] = h["tokens"] if h else None
        return {"always": est_tokens(blk) if blk else None, "always_budget": AGENTS_INDEX_BUDGET, "map_budget": MAP_BUDGET, "files": files}


    # ---------- background jobs: long work outlives the request, so a proxy timeout cannot cut it off ----------
    import threading
    import uuid
    jobs: dict[str, dict] = {}
    jobs_lock = threading.Lock()
    REPO_WORK = {"add", "index", "branch", "publish", "hygiene"}     # write the repo's index: one at a time per repo

    def start_job(kind: str, repo: str, fn) -> JSONResponse:
        with jobs_lock:
            if repo and kind in REPO_WORK and any(j["repo"] == repo and j["state"] == "running" and j["kind"] in REPO_WORK for j in jobs.values()):
                raise HTTPException(409, f"{repo} is already being worked on; wait for that to finish")
            jid = uuid.uuid4().hex[:12]
            job = {"id": jid, "kind": kind, "repo": repo, "state": "running", "started": time.time(), "finished": None,
                   "result": None, "error": None, "status": 200}
            jobs[jid] = job
            for old in sorted((j for j in jobs.values() if j["state"] != "running"), key=lambda j: j["started"])[:-50]:
                jobs.pop(old["id"], None)                    # keep the last 50 finished jobs

        def work():
            try:
                job["result"] = fn()
                job["state"] = "done"
            except HTTPException as e:
                job.update(state="failed", error=e.detail if isinstance(e.detail, str) else json.dumps(e.detail), status=e.status_code)
            except Exception as e:
                job.update(state="failed", error=f"{type(e).__name__}: {e}", status=500)
                from .paths import diagnose
                diagnose(kind, f"{type(e).__name__}: {e}", repo)
            finally:
                job["finished"] = time.time()
                cache.pop(repo, None)
        threading.Thread(target=work, daemon=True, name=f"devmem-{kind}-{repo}").start()
        return JSONResponse({"job": jid, "kind": kind, "repo": repo, "state": "running"}, status_code=202)

    def maybe_job(background: bool, kind: str, repo: str, fn):
        return start_job(kind, repo, fn) if background else fn()

    def job_view(j: dict) -> dict:
        end = j["finished"] or time.time()
        return {k: j[k] for k in ("id", "kind", "repo", "state", "result", "error", "status")} | {"seconds": round(end - j["started"], 1)}

    @app.get("/api/jobs/{jid}")
    def job_status(jid: str):
        j = jobs.get(jid)
        if not j:
            raise HTTPException(404, "unknown job (the server may have restarted)")
        return job_view(j)

    @app.get("/api/jobs")
    def job_list(active: bool = True):
        return [job_view(j) for j in sorted(jobs.values(), key=lambda j: -j["started"]) if not active or j["state"] == "running"]

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
    def add_repo(body: AddRepo, background: bool = False):
        def _work():
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
                if not paths_allowed():
                    raise HTTPException(403, "this server does not read paths on its own disk: upload the repo or give a Git URL")
                root = Path(body.path).expanduser().resolve()
                if not root.is_dir():
                    raise HTTPException(400, "not a directory")
                name = body.name or root.name
            else:
                raise HTTPException(400, "give a local path or a git URL")
            index_repo(root, name)                      # register only after a full-accuracy index succeeded
            w = ws(); w["repos"][name] = str(root); save_ws(w)
            return repo_summary(name, str(root))
        return maybe_job(background, "add", body.name or (Path(body.path).name if body.path else ""), _work)

    @app.post("/api/repos/upload")
    async def upload_repo(request: Request, name: str = "", filename: str = "", background: bool = False):
        """Onboard a repo from an uploaded archive: the request body is the .zip / .tar.gz itself (the browser packs a
        picked folder into a zip first). Uploading again under the same name replaces the code and re-indexes it."""
        try:
            rname = uploads.repo_name(name, filename)
        except uploads.UploadError as e:
            raise HTTPException(400, str(e))
        known = ws()["repos"].get(rname)
        dest = uploads.uploads_dir() / rname
        if known and Path(known).resolve() != dest.resolve():
            raise HTTPException(409, f"a repo called {rname} is already onboarded from {known}; give this upload another name")
        limit = uploads.max_upload_bytes()
        if int(request.headers.get("content-length") or 0) > limit:
            raise HTTPException(413, f"upload is larger than {limit // (1024 * 1024)} MB (DEVMEM_MAX_UPLOAD_MB raises the limit)")
        uploads.uploads_dir().mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=uploads.uploads_dir(), suffix=".upload")
        try:
            size = 0
            with os.fdopen(fd, "wb") as out:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > limit:
                        raise HTTPException(413, f"upload is larger than {limit // (1024 * 1024)} MB (DEVMEM_MAX_UPLOAD_MB raises the limit)")
                    out.write(chunk)
            if not size:
                raise HTTPException(400, "empty upload")

        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

        def unpack_and_index():
            try:
                root = uploads.install(Path(tmp), rname)
                try:
                    index_repo(root, rname)
                except Exception:
                    if not known:   # a first upload that cannot be indexed leaves nothing behind
                        shutil.rmtree(root, ignore_errors=True)
                    raise
            except uploads.UploadError as e:
                raise HTTPException(400, str(e))
            finally:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            w = ws(); w["repos"][rname] = str(root); save_ws(w)
            cache.pop(rname, None)
            return repo_summary(rname, str(root))
        if background:
            return start_job("add", rname, unpack_and_index)
        return await run_in_threadpool(unpack_and_index)

    @app.get("/api/repos/{name}/knowledge.zip")
    def knowledge_zip(name: str):
        """The published knowledge as a zip to unpack at the root of the real repo: how an uploaded repo's `.devmem/` and
        AGENTS.md get back to where agents work."""
        root = root_of(name)
        if not (root / ".devmem/knowledge").exists():
            raise HTTPException(409, f"{name} is not published yet")
        return Response(uploads.knowledge_zip(root), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{name}-devmem-knowledge.zip"'})

    @app.delete("/api/repos/{name}")
    def remove_repo(name: str):
        w = ws(); path = w["repos"].pop(name, None); save_ws(w)
        cache.pop(name, None)
        if path and uploads.uploads_dir().resolve() in Path(path).resolve().parents:
            shutil.rmtree(path, ignore_errors=True)   # an upload is devmem's own copy; a path or clone is left alone
        return {"ok": True}

    # ---------- AI setup ----------
    class AiSettings(BaseModel):
        provider: str | None = None
        api_key: str | None = None     # "" or missing keeps the saved key
        clear_key: bool = False
        model: str | None = None
        base_url: str | None = None
        embed_model: str | None = None
        project: str | None = None
        location: str | None = None
        local_embed: str | None = None

    @app.get("/api/settings/ai")
    def ai_settings():
        return settings_mod.public()

    @app.put("/api/settings/ai")
    def ai_settings_save(body: AiSettings):
        vals = {k: ("" if v is None else v) for k, v in body.model_dump(exclude={"clear_key", "api_key"}, exclude_unset=True).items()}
        if body.provider and body.provider not in settings_mod.PROVIDERS:
            raise HTTPException(400, f"unknown provider {body.provider}")
        if body.base_url and not re.match(r"^https?://[^\s]+$", body.base_url.strip()):
            raise HTTPException(400, "the server address must start with http:// or https://")
        if body.clear_key:
            vals["api_key"] = None
        elif body.api_key:
            vals["api_key"] = body.api_key
        settings_mod.save(vals)
        return settings_mod.public()

    @app.post("/api/settings/ai/test")
    def ai_settings_test():
        """One tiny request to the configured model, so a wrong key or model name shows up here and not in the middle of a task."""
        out: dict = {"ok": False}
        t0 = time.time()
        try:
            prov = chat_mod.provider_from_env()
            if prov is None:
                out["error"] = "nothing to test yet: choose a provider and save a key (or a server address)"
            else:
                reply = prov.generate("Reply with the single word: ok")
                out.update({"ok": bool(reply), "model": getattr(prov, "name", ""), "reply": reply[:80], "ms": int((time.time() - t0) * 1000)})
                if not reply:
                    out["error"] = "the model answered with nothing"
        except ImportError as e:
            out["error"] = f"{e}. Vertex needs: pip install google-genai"
        except Exception as e:
            out["error"] = str(e)[:400]
        try:
            emb = chat_mod.local_embedder()
            out["local_embed"] = "ready" if emb else "off"
        except ImportError:
            out["local_embed"] = "not installed: pip install model2vec"
        except Exception as e:
            out["local_embed"] = f"failed: {str(e)[:200]}"
        return out

    # ---------- definitions devmem is unsure about ----------
    class Describe(BaseModel):
        id: str
        text: str

    class DescribeAi(BaseModel):
        ids: list[str]

    @app.get("/api/repos/{name}/definitions")
    def definitions(name: str, level: str = "low", limit: int = 100, q: str = ""):
        return {**defs.review(ix_of(name), level, max(1, min(limit, 500)), q), "llm": chat_mod.llm_status()}

    @app.put("/api/repos/{name}/definitions")
    def describe(name: str, body: Describe):
        try:
            return {"saved": defs.set_description(ix_of(name), body.id, body.text, "human")}
        except KeyError:
            raise HTTPException(404, f"unknown symbol {body.id}")

    @app.post("/api/repos/{name}/definitions/ai")
    def describe_ai(name: str, body: DescribeAi):
        if not body.ids or len(body.ids) > 40:
            raise HTTPException(400, "send 1 to 40 symbol ids per request")
        try:
            prov = chat_mod.provider_from_env()
        except ImportError as e:
            raise HTTPException(409, f"LLM client not installed: {e}")
        if prov is None:
            raise HTTPException(409, "no AI model is set up: open AI setup, or write the descriptions by hand")
        return defs.describe_with_ai(ix_of(name), body.ids, prov)

    @app.post("/api/repos/{name}/index")
    def reindex(name: str, background: bool = False):
        def _work():
            root = root_of(name)
            idx = index_repo(root, name)
            return {**repo_summary(name, str(root)), "stats": idx["stats"]}
        return maybe_job(background, "index", name, _work)

    @app.get("/api/repos/{name}/branches")
    def branches(name: str):
        root = root_of(name)
        return {"current": git_branch(root), "branches": git_branches(root), "dirty": git_dirty(root)}

    class Branch(BaseModel):
        branch: str

    @app.post("/api/repos/{name}/branch")
    def switch_branch(name: str, body: Branch, background: bool = False):
        def _work():
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
        return maybe_job(background, "branch", name, _work)

    class Publish(BaseModel):
        commit: bool = False

    @app.post("/api/repos/{name}/publish")
    def do_publish(name: str, body: Publish, background: bool = False):
        def _work():
            root = root_of(name)
            idx = index_repo(root, name)
            try:
                files = publish(root, idx, ws()["repos"])
            except RuntimeError as e:
                raise HTTPException(422, str(e))
            branch = commit_branch(root) if body.commit else None
            return {"written": files, "branch": branch}
        return maybe_job(background, "publish", name, _work)

    @app.post("/api/repos/{name}/hygiene")
    def run_hygiene(name: str, background: bool = False):
        def _work():
            root = root_of(name)
            idx = index_repo(root, name)
            h = hyg.run_hygiene(root, idx, name)
            try:
                publish(root, idx, ws()["repos"])
            except RuntimeError as e:
                raise HTTPException(422, str(e))
            return {"counts": h["counts"], "tools": h["tools"]}
        return maybe_job(background, "hygiene", name, _work)

    # ---------- code health: local checks + SonarQube, ranked by blast radius ----------
    from . import health as health_mod
    from . import sonar as sonar_mod

    class SonarConn(BaseModel):
        url: str | None = None
        token: str | None = None      # "" or missing keeps the saved token
        clear_token: bool = False

    class SonarProject(BaseModel):
        key: str
        branch: str = ""

    @app.get("/api/repos/{name}/health")
    def health(name: str):
        return {**health_mod.build(ix_of(name), name), "sonar_setup": {k: v for k, v in sonar_mod.public({name: str(root_of(name))}).items() if k != "projects"} |
                {"project": sonar_mod.public({name: str(root_of(name))})["projects"][0]}}

    @app.get("/api/integrations/sonar")
    def sonar_status():
        return sonar_mod.public(ws()["repos"])

    @app.put("/api/integrations/sonar")
    def sonar_save(body: SonarConn):
        try:
            sonar_mod.save_connection(body.url, body.token, body.clear_token)
        except sonar_mod.SonarError as e:
            raise HTTPException(400, str(e))
        return sonar_mod.public(ws()["repos"])

    @app.post("/api/integrations/sonar/test")
    def sonar_test():
        s = sonar_mod.effective()
        try:
            return sonar_mod.test(s["url"], s.get("token", ""))
        except sonar_mod.SonarError as e:
            return {"ok": False, "error": str(e)}

    @app.get("/api/integrations/sonar/projects")
    def sonar_projects(q: str = ""):
        try:
            return sonar_mod.search_projects(q)
        except sonar_mod.SonarError as e:
            raise HTTPException(422, str(e))

    @app.put("/api/repos/{name}/sonar")
    def sonar_map(name: str, body: SonarProject):
        root_of(name)
        if body.key and not re.fullmatch(r"[\w.:-]{1,400}", body.key.strip()) or body.branch and not re.fullmatch(r"[\w./-]{1,250}", body.branch.strip()):
            raise HTTPException(400, "that does not look like a SonarQube project key or branch name")
        sonar_mod.set_project(name, body.key, body.branch)
        return {"ok": True}

    @app.post("/api/repos/{name}/sonar/sync")
    def sonar_sync(name: str, background: bool = False):
        def _work():
            ix = ix_of(name)
            try:
                d = sonar_mod.sync(name, root_of(name), set(ix.data["files"]))
            except sonar_mod.SonarError as e:
                raise HTTPException(422, str(e))
            return {"issues": len(d["issues"]), "hotspots": len(d["hotspots"]), "total": d["total_issues"], "matched": d["matched"],
                    "truncated": d["truncated"], "notes": d["notes"], "gate": d.get("gate")}
        return maybe_job(background, "sonar", name, _work)

    # ---------- modernize: hand-written code that the language or a library now provides ----------
    class ModernizeAi(BaseModel):
        keys: list[str]

    @app.get("/api/repos/{name}/modernize")
    def modernize_view(name: str):
        from . import modernize as mz
        return {**mz.view(ix_of(name)), "llm": chat_mod.llm_status()}

    class ModernizeScan(BaseModel):
        latest: bool = True     # also fetch latest versions, docs and release notes for the repo's libraries (needs the network)

    @app.post("/api/repos/{name}/modernize")
    def modernize_scan(name: str, body: ModernizeScan = ModernizeScan(), background: bool = False):
        def _work():
            from . import modernize as mz
            ix = ix_of(name)
            info = mz.refresh_latest(ix) if body.latest else None
            mz.scan(ix, info)
            if info and info["docs_fetched"]:
                try:   # the docs just fetched travel to agents with the next publish; copy them into the repo now so search finds them
                    from . import docs as dm
                    dm.copy_relevant(root_of(name))
                except Exception as e:
                    from .paths import diagnose
                    diagnose("docs", f"fetched docs could not be copied into the repo: {e}", name)
            return {**mz.view(ix), "llm": chat_mod.llm_status()}
        return maybe_job(background, "modernize", name, _work)

    @app.post("/api/repos/{name}/modernize/discover")
    def modernize_discover(name: str):
        """Optional: the model reads what newer library releases added and reports functions here that do the same by hand."""
        from . import modernize as mz
        try:
            prov = chat_mod.provider_from_env()
        except ImportError as e:
            raise HTTPException(409, f"LLM client not installed: {e}")
        if prov is None:
            raise HTTPException(409, "no AI model is set up: open AI setup. The scan already matches release notes by words without one.")
        return mz.discover_with_ai(ix_of(name), prov)

    @app.post("/api/repos/{name}/modernize/ai")
    def modernize_ai(name: str, body: ModernizeAi):
        from . import modernize as mz
        try:
            prov = chat_mod.provider_from_env()
        except ImportError as e:
            raise HTTPException(409, f"LLM client not installed: {e}")
        if prov is None:
            raise HTTPException(409, "no AI model is set up: open AI setup. The scan itself does not need one.")
        return mz.verify_with_ai(ix_of(name), body.keys, prov)

    @app.post("/api/repos/{name}/coverage")
    async def coverage_upload(name: str, request: Request, filename: str = "coverage"):
        """Import a coverage report (coverage.py JSON, Cobertura or JaCoCo XML, lcov) uploaded as the request body."""
        from . import coverage_import as ci
        raw = await request.body()
        if not raw:
            raise HTTPException(400, "empty upload")
        if len(raw) > uploads.max_upload_bytes():
            raise HTTPException(413, "report too large")
        tmp = Path(tempfile.mkdtemp(prefix="devmem-cov-")) / (re.sub(r"[^\w.-]", "_", filename) or "coverage")
        try:
            tmp.write_bytes(raw)
            d = await run_in_threadpool(ci.import_file, ix_of(name), tmp)
        except ci.CoverageError as e:
            raise HTTPException(400, str(e))
        finally:
            shutil.rmtree(tmp.parent, ignore_errors=True)
        cache.pop(name, None)
        return {k: d[k] for k in ("format", "per_test", "stats", "ts")}

    @app.get("/api/repos/{name}/coverage")
    def coverage_status(name: str):
        p = root_of(name) / ".devmem/coverage.json"
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"available": False}
        return {"available": True, **{k: d.get(k) for k in ("format", "per_test", "stats", "ts", "source")}}

    @app.get("/api/repos/{name}/selfcheck")
    def selfcheck_view(name: str):
        from . import selfcheck
        d = selfcheck.load(root_of(name))
        return {"available": bool(d), **(d or {})}

    @app.post("/api/repos/{name}/selfcheck")
    def selfcheck_run(name: str, background: bool = False):
        def _work():
            from . import selfcheck
            return {"available": True, **selfcheck.run(ix_of(name))}
        return maybe_job(background, "selfcheck", name, _work)

    class PromptReq(BaseModel):
        kind: str
        data: dict = {}

    @app.post("/api/repos/{name}/prompt")
    def make_prompt(name: str, body: PromptReq):
        """A self-contained prompt for a coding agent (see prompts.py). Text only: devmem does not run it."""
        from . import prompts
        if body.kind not in prompts.KINDS:
            raise HTTPException(400, "kind must be one of: " + ", ".join(prompts.KINDS))
        try:
            return prompts.build(ix_of(name), body.kind, body.data)
        except KeyError as e:
            raise HTTPException(400, f"missing or unknown {e}")

    # ---------- AI analysis: the optional first pass that writes descriptions, module summaries and an overview ----------
    from . import analysis as an_mod
    jobs: dict[str, an_mod.Job] = {}

    class RunAnalysis(BaseModel):
        steps: list[str] = list(an_mod.STEPS)
        limit: int | None = 200     # how many weak definitions to describe this run; None = all of them

    class AnalysisText(BaseModel):
        module: str | None = None   # None = the repo overview
        text: str

    def _limit(v: int | None) -> int | None:
        return None if not v or v <= 0 else min(v, 5000)

    @app.get("/api/repos/{name}/analysis")
    def analysis_status(name: str, limit: int = 200):
        job = jobs.get(name)
        return {**an_mod.status(ix_of(name), _limit(limit)), "job": job.public() if job else None, "llm": chat_mod.llm_status()}

    @app.post("/api/repos/{name}/analysis")
    def analysis_run(name: str, body: RunAnalysis):
        import threading
        steps = [s for s in an_mod.STEPS if s in body.steps]
        if not steps:
            raise HTTPException(400, "choose at least one of: " + ", ".join(an_mod.STEPS))
        if name in jobs and jobs[name].state == "running":
            raise HTTPException(409, f"an analysis of {name} is already running")
        ix = ix_of(name)
        try:
            prov = chat_mod.provider_from_env()
        except ImportError as e:
            raise HTTPException(409, f"LLM client not installed: {e}")
        if prov is None:
            raise HTTPException(409, "no AI model is set up: open AI setup first")
        job = jobs[name] = an_mod.Job(steps)
        threading.Thread(target=an_mod.run, args=(ix, prov, steps, _limit(body.limit), job), daemon=True).start()
        return job.public()

    @app.delete("/api/repos/{name}/analysis")
    def analysis_cancel(name: str):
        job = jobs.get(name)
        if job and job.state == "running":
            job.cancel.set()   # the request in flight finishes; nothing further is sent
        return {"ok": True}

    @app.put("/api/repos/{name}/analysis")
    def analysis_edit(name: str, body: AnalysisText):
        try:
            an_mod.set_text(ix_of(name), body.module, body.text)
        except KeyError:
            raise HTTPException(404, f"unknown module {body.module}")
        return {"ok": True}

    # ---------- explorer ----------
    @app.get("/api/repos/{name}/search")
    def search(name: str, q: str, limit: int = 20):
        """Code by plain words. Keyword match on names and docstrings; with DEVMEM_LOCAL_EMBED on, the top 40 are re-ranked by
        meaning too, so "save" also finds persist/store code a user would not know the name of."""
        ix = ix_of(name)
        hits = ix.find(q, max(limit, 40))
        try:
            emb = chat_mod.local_embedder()
        except ImportError:   # DEVMEM_LOCAL_EMBED set without model2vec installed: keyword ranking only
            emb = None
        if emb and hits:
            vecs = chat_mod._embed_cached(emb, [q] + [f"{s['qualname']} {s['doc']} {s['file']}" for _, s in hits])
            top = hits[0][0] or 1
            hits = sorted(((0.5 * sc / top + 0.5 * max(chat_mod._cos(vecs[0], v), 0), s) for (sc, s), v in zip(hits, vecs[1:])), key=lambda x: -x[0])
        return [{**s, "score": round(sc, 2)} for sc, s in hits[:limit]]

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
        if "not_found" in r:
            return {"kind": "not_found", **r}
        if "table" in r:
            return {"kind": "table", **r}
        ids = r["targets"] + [i["id"] for i in r["hop1"] + r["hop2"]] + r["tests"]
        return {"kind": "symbol", **r, "lines": {i: ix.symbols[i]["line"] for i in ids if i in ix.symbols}}

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

    @app.post("/api/docs/upload")
    async def docs_upload(request: Request, name: str = "", filename: str = ""):
        """Add documentation from an uploaded .zip / .tar.gz, or a folder the browser packed (Markdown, text, HTML files)."""
        from . import docs as dm
        try:
            lib = uploads.repo_name(name, filename)
        except uploads.UploadError as e:
            raise HTTPException(400, str(e))
        limit = uploads.max_upload_bytes()
        work = Path(tempfile.mkdtemp(prefix="devmem-docs-"))
        try:
            size = 0
            with open(work / "upload.bin", "wb") as out:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > limit:
                        raise HTTPException(413, f"upload is larger than {limit // (1024 * 1024)} MB")
                    out.write(chunk)
            if not size:
                raise HTTPException(400, "empty upload")

            def unpack_and_add():
                uploads.extract(work / "upload.bin", work / lib)
                m = dm.add(str(work / lib), lib)
                mf = dm.docs_dir() / m["name"] / "manifest.json"     # the unpacked folder is temporary: record where it came from
                man = json.loads(mf.read_text(encoding="utf-8"))
                man.update({"source": f"uploaded: {filename or lib}", "kind": "upload"})
                mf.write_text(json.dumps(man, indent=1), encoding="utf-8")
                return m
            try:
                m = await run_in_threadpool(unpack_and_add)
            except uploads.UploadError as e:
                raise HTTPException(400, str(e))
            except Exception as e:
                raise HTTPException(422, f"could not read these docs: {e}")
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return {"name": m["name"], "version": m["version"], "pages": len(m["pages"]), "bytes": m["bytes"]}

    @app.post("/api/docs")
    def docs_add(body: DocAdd):
        from . import docs as dm
        if not re.match(r"^(https?|pypi|npm):", body.spec.strip()) and not paths_allowed():
            raise HTTPException(403, "this server does not read paths on its own disk: upload the docs folder or a zip")
        try:
            m = dm.add(body.spec.strip(), body.name or None, body.follow)
        except Exception as e:
            raise HTTPException(422, f"could not fetch: {e}")
        return {"name": m["name"], "version": m["version"], "pages": len(m["pages"]), "bytes": m["bytes"]}

    class DocsRefresh(BaseModel):
        names: list[str] | None = None      # None = every library

    class DocsSettings(BaseModel):
        refresh_days: int

    @app.post("/api/docs/refresh")
    def docs_refresh(body: DocsRefresh = DocsRefresh()):
        from . import docs as dm
        return dm.refresh(body.names or None)

    @app.get("/api/docs/settings")
    def docs_settings():
        from . import docs as dm
        return {"refresh_days": dm.refresh_days(), "from_environment": bool(os.environ.get("DEVMEM_DOCS_REFRESH_DAYS"))}

    @app.put("/api/docs/settings")
    def docs_settings_save(body: DocsSettings):
        from . import docs as dm
        return {"refresh_days": dm.set_refresh_days(body.refresh_days), "from_environment": bool(os.environ.get("DEVMEM_DOCS_REFRESH_DAYS"))}

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
                raise HTTPException(422, "no AI model is set up: open AI setup in the web UI, or set DEVMEM_AI_PROVIDER and DEVMEM_AI_KEY")
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

    @app.get("/api/graph/all")
    def graph_all(level: str = "file"):
        """Every file (or symbol) of every repo and every link between them, for the Obsidian-style map.
        Tables are shared nodes, so repos that touch the same table connect through it."""
        nodes, links, keys, loaded = {}, Counter(), {}, {}
        for rname, rpath in ws()["repos"].items():
            ip = Path(rpath) / ".devmem/index.json"
            if not ip.exists():
                continue
            idx = json.loads(ip.read_text(encoding="utf-8"))
            loaded[rname] = (Path(rpath), idx)
            sym = {s["id"]: s for s in idx["symbols"]}
            test_files = {s["file"] for s in idx["symbols"] if s["is_test"]}

            def key(end: str, rname=rname, idx=idx, sym=sym, test_files=test_files) -> str | None:
                if end.startswith("table:"):
                    nodes.setdefault(end, {"id": end, "label": end[6:], "kind": "table", "repo": ""})
                    return end
                s, f = sym.get(end), end.split("#")[0]
                if level == "symbol" and s:
                    nid = f"{rname}:{end}"
                    nodes.setdefault(nid, {"id": nid, "label": s["qualname"], "kind": "test" if s["is_test"] else s["kind"],
                                           "repo": rname, "file": f, "sid": end})
                    return nid
                if f not in idx["files"]:
                    return None
                nid = f"{rname}:{f}"
                nodes.setdefault(nid, {"id": nid, "label": f.rsplit("/", 1)[-1], "kind": "test" if f in test_files else "file",
                                       "repo": rname, "file": f})
                return nid

            for x in (idx["files"] if level != "symbol" else [s["id"] for s in idx["symbols"] if s["kind"] != "module"]):
                key(x)   # unlinked files/symbols too: the map shows orphans
            for e in idx["edges"]:
                a, b = key(e["src"]), key(e["dst"])
                if a and b and a != b:
                    links[(a, b, e["type"])] += 1
            keys[rname] = key
        for x in crossrepo.cross_links(loaded):   # a file importing a module another onboarded repo defines
            a = keys[x["src_repo"]](x["src_file"])
            dst = f"{x['dst_file']}#{x['name']}" if x["name"] else x["dst_file"]
            b = keys[x["dst_repo"]](dst) or keys[x["dst_repo"]](x["dst_file"])
            if a and b:
                links[(a, b, "imports")] += 1
        return {"nodes": list(nodes.values()),
                "links": [{"source": a, "target": b, "kind": t, "weight": n} for (a, b, t), n in links.items()]}

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

    class RuleIn(BaseModel):
        text: str
        applies_to: str = "**"
        severity: str = "rule"
        repo: str | None = None          # None: global rule in the hub, synced into every repo

    @app.post("/api/rules")
    def add_rule(body: RuleIn):
        text = body.text.strip()
        if not text or body.severity not in ("rule", "advice"):
            raise HTTPException(422, "a rule needs text, and severity 'rule' or 'advice'")
        rid = m.slug(" ".join(text.split()[:6]))
        if body.repo:
            p = m.write_rule(root_of(body.repo), rid, text, body.applies_to or "**", "repo", body.severity)
        else:
            p = am._write_global_rule(rid, text, body.applies_to or "**", body.severity)
        return {"id": p.stem}

    @app.delete("/api/rules/{rid}")
    def delete_rule(rid: str, repo: str | None = None):
        d = root_of(repo) / m.RULES_DIR if repo else am.hub_dir() / "rules"
        f = d / f"{rid}.md"
        if not re.fullmatch(r"[a-z0-9-]+", rid) or not f.exists():
            raise HTTPException(404, "no such rule (rules in .devmem/rules.md are edited in that file)")
        f.unlink()
        return {"ok": True}

    # ---------- agents: connect, what they see, what they did ----------
    class ConnectIn(BaseModel):
        agents: list[str] = []
        local: bool = False

    @app.get("/api/repos/{name}/connect")
    def connect_status(name: str):
        from devmem import connect as cn
        return {"agents": cn.AGENTS, "connected": cn.status(root_of(name)), "user_only": list(cn.USER_ONLY)}

    @app.post("/api/repos/{name}/connect")
    def connect_agents(name: str, body: ConnectIn):
        import sys as _sys
        from devmem import connect as cn
        cmd, args = (_sys.executable, ["-m", "devmem.cli", "mcp"]) if body.local else ("devmem", ["mcp"])
        try:
            return {"results": cn.connect(root_of(name), body.agents or cn.AGENTS, cmd, args)}
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.get("/api/repos/{name}/doctor")
    def doctor_view(name: str):
        from devmem import doctor
        return [{"ok": ok, "what": what, "fix": fix} for ok, what, fix in doctor.checks(root_of(name))]

    AGENT_VIEW = {"show", "find", "impact", "recall", "outline", "gaps", "helpers"}

    @app.get("/api/repos/{name}/agent-view")
    def agent_view(name: str, cmd: str, arg: str = ""):
        """Exactly what an agent gets back for one devmem command (not logged as agent usage)."""
        import contextlib
        import io
        from devmem import cli as agent_cli
        if cmd not in AGENT_VIEW:
            raise HTTPException(422, f"previewable commands: {', '.join(sorted(AGENT_VIEW))}")
        root = root_of(name)
        ag = root / "AGENTS.md"
        buf = io.StringIO()
        # ponytail: redirect_stdout is process-wide; two previews at the same instant could mix output. Fine for a local one-user app.
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            try:
                agent_cli.run([cmd, *([arg] if arg else [])], via="ui", root=root)
            except SystemExit:
                pass
        text = buf.getvalue()
        return {"command": f"devmem {cmd} {arg}".strip(), "output": text, "tokens": len(text) // 4,
                "session_start": managed_block(ag.read_text(encoding="utf-8")) if ag.exists() else None}

    @app.get("/api/repos/{name}/activity")
    def activity(name: str):
        return {"sessions": agent_usage.sessions(root_of(name)), "total": agent_usage.summary(root_of(name))}

    @app.get("/api/repos/{name}/gaps")
    def test_gaps(name: str, limit: int = 30):
        from devmem import review
        return review.gaps(ix_of(name), limit)

    @app.get("/api/alerts")
    def alerts():
        """Things to fix, most urgent first: failed automatic re-index, stale index or knowledge, stale docs, unconnected repos."""
        from devmem import connect as cn
        from . import docs as dm
        from . import watch
        out = []
        for name, path in ws()["repos"].items():
            if name in watch.ERRORS:
                out.append({"level": "bad", "repo": name, "text": watch.ERRORS[name], "page": "repos"})
            d = repo_summary(name, path)
            if not d["indexed"]:
                continue
            if d["stale"]:
                out.append({"level": "warn", "repo": name, "text": "index is behind HEAD: agents see old code", "page": "repos"})
            probs = [p for p in d["knowledge_problems"] if p != "not published"]
            if probs:
                out.append({"level": "warn", "repo": name, "text": f"{len(probs)} knowledge issue(s): {probs[0]}", "page": "repos"})
            elif d["knowledge_problems"]:
                out.append({"level": "info", "repo": name, "text": "not published yet: agents have no map", "page": "repos"})
            if not any(cn.status(Path(path)).values()):
                out.append({"level": "info", "repo": name, "text": "no agent connected (MCP)", "page": "agents"})
        for lib in dm.list_libs():
            if (lib.get("age_days") or 0) > 120:
                out.append({"level": "warn", "repo": None, "text": f"{lib['name']} docs are {lib['age_days']} days old: refresh them", "page": "docs"})
        rank = {"bad": 0, "warn": 1, "info": 2}
        import datetime as _dt
        from .paths import diagnostics
        day_ago = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        seen = set()
        for d in diagnostics(50):            # things that failed quietly in the last day: once each, newest first
            if d["ts"] >= day_ago and (d["area"], d["repo"], d["message"]) not in seen:
                seen.add((d["area"], d["repo"], d["message"]))
                out.append({"level": "warn", "repo": d["repo"] or None, "text": f"{d['area']}: {d['message']}", "page": {"docs": "docs", "sonar": "integrations"}.get(d["area"], "repos")})
        return sorted(out, key=lambda x: rank[x["level"]])

    @app.get("/api/diagnostics")
    def diagnostics_list():
        from .paths import diagnostics
        return diagnostics(100)

    class UpgradeIn(BaseModel):
        repo: str

    @app.post("/api/docs/upgrade-notes")
    def docs_upgrade_notes(body: UpgradeIn):
        from . import docs as dm
        return dm.add_upgrade_notes(root_of(body.repo))

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

    @app.get("/api/reuse")
    def reuse_search(need: str, into: str, limit: int = 5):
        """What the other onboarded repos already built for `need`, each fitted against `into`: effort, notes, a port prompt."""
        from .reuse import live
        allr = {n: p for n, p in ws()["repos"].items() if (Path(p) / ".devmem/index.json").exists()}
        if into not in allr:
            raise HTTPException(404, f"unknown or unindexed repo {into}")
        if len(allr) < 2:
            return {"hits": [], "peers": 0}
        try:
            prov = chat_mod.provider_from_env()
        except Exception:   # optional LLM ranking: a missing SDK or bad credentials fall back to keyword ranking
            prov = None
        return {"hits": live(need, into, allr, max(1, min(limit, 10)), provider=prov), "peers": len(allr) - 1}

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
            raise HTTPException(409, "no AI model is set up: open AI setup in the web UI, or set DEVMEM_AI_PROVIDER and DEVMEM_AI_KEY")
        try:
            return {**verdict, **chat_mod.ask(body.question, {n: Path(allr[n]) for n in names}, min(max(body.limit, 1), 20), use_llm=body.llm)}
        except ImportError as e:
            raise HTTPException(409, f"LLM client not installed: {e}")

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
