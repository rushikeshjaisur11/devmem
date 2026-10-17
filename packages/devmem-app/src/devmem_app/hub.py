"""Central knowledge hub, app side: `devmem-app hub set <git-url|dir>` then `devmem-app hub push`. The hub is an ordinary git
repo (see devmem.hub for its layout) that agents read with `devmem reuse` / `devmem hub`. This module only writes files and
runs git in the hub's working copy; pushing to the remote happens only when asked for."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from devmem import hub as agent_hub
from devmem.index import Index

from .paths import devmem_home, hub_dir
from .reuse import build_entries, summarize

URL_OK = re.compile(r"^(https?://|ssh://|git://|git@[\w.-]+:)[^\s]+$")
ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}


def _ws_path() -> Path:
    return devmem_home() / "workspace.json"


def _read() -> dict:
    p = _ws_path()
    return json.loads(p.read_text()) if p.exists() else {"repos": {}}


def config() -> dict | None:
    return _read().get("hub")


def configured_url() -> str | None:
    """The URL agents should use (a local path is meaningless on their machine, so it is not published)."""
    c = config()
    return c["spec"] if c and URL_OK.match(c["spec"]) else None


def _git(d: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(d), *args], capture_output=True, text=True, env=ENV, timeout=timeout)


def configure(spec: str) -> dict:
    spec = spec.strip()
    if URL_OK.match(spec):
        d = devmem_home() / "hub-repo"
        if (d / ".git").exists():
            _git(d, "pull", "--ff-only", "-q")
        else:
            d.parent.mkdir(parents=True, exist_ok=True)
            r = subprocess.run(["git", "clone", "-q", "--", spec, str(d)], capture_output=True, text=True, env=ENV, timeout=300)
            if r.returncode:
                raise RuntimeError("git clone failed: " + (r.stderr or r.stdout).strip()[-300:])
    else:
        d = Path(spec).expanduser().resolve()
        d.mkdir(parents=True, exist_ok=True)
        if not (d / ".git").exists() and _git(d, "init", "-q", "-b", "main").returncode:
            raise RuntimeError(f"could not git init {d}")
    ws = _read()
    ws["hub"] = {"spec": spec, "dir": str(d)}
    _ws_path().parent.mkdir(parents=True, exist_ok=True)
    _ws_path().write_text(json.dumps(ws, indent=1))
    return status()


def _origin(root: Path) -> str:
    r = subprocess.run(["git", "-C", str(root), "remote", "get-url", "origin"], capture_output=True, text=True)
    return re.sub(r"//[^@/\s]+@", "//", r.stdout.strip())   # never publish embedded credentials


def _branch(root: Path) -> str:
    r = subprocess.run(["git", "-C", str(root), "symbolic-ref", "--short", "-q", "HEAD"], capture_output=True, text=True)
    return r.stdout.strip()


def status() -> dict:
    c = config()
    if not c:
        return {"configured": False}
    d = Path(c["dir"])
    out = {"configured": True, "spec": c["spec"], "dir": str(d), "agent_visible": bool(URL_OK.match(c["spec"])), "repos": [], "symbols": 0}
    if d.is_dir():
        out["repos"] = agent_hub.repos(d)
        out["symbols"] = len(agent_hub.entries(d))
        log = _git(d, "log", "-1", "--format=%cI %s").stdout.strip() if (d / ".git").exists() else ""
        out["last_commit"] = log
        out["remote"] = bool(_git(d, "remote").stdout.strip())
        out["dirty"] = bool(_git(d, "status", "--porcelain").stdout.strip())
    return out


def _render_index(metas: list[dict]) -> str:
    lines = ["# devmem knowledge hub", "",
             "One entry per onboarded repo. Agents: `devmem hub repos`, `devmem hub show <repo>`, `devmem reuse \"<need>\"`.", "",
             "| repo | languages | symbols | branch | origin |", "|---|---|---|---|---|"]
    for m in metas:
        lines.append(f"| [{m['name']}](repos/{m['name']}/) | {', '.join(m.get('langs', {}))} | {m.get('symbols', '')} | {m.get('branch', '')} | {m.get('origin', '')} |")
    return "\n".join(lines) + "\n"


def push(repos: dict[str, str | Path], per_repo: int = 400, summarize_with=None, commit: bool = True, do_push: bool = False,
         prune: bool = False) -> dict:
    c = config()
    if not c:
        raise RuntimeError("no hub configured: devmem-app hub set <git-url|dir>")
    d = Path(c["dir"])
    (d / "catalog").mkdir(parents=True, exist_ok=True)
    (d / "repos").mkdir(exist_ok=True)
    written, warnings = [], []
    for name, root in sorted(repos.items()):
        root = Path(root)
        try:
            ix = Index.load(root)
        except FileNotFoundError:
            warnings.append(f"{name}: not indexed, skipped")
            continue
        entries = build_entries(ix, per_repo, 60)
        if summarize_with is not None:
            summarize(entries, summarize_with)
        (d / "catalog" / f"{name}.json").write_text(json.dumps(entries), encoding="utf-8")
        rd = d / "repos" / name
        shutil.rmtree(rd, ignore_errors=True)
        rd.mkdir(parents=True)
        kdir = root / ".devmem/knowledge"
        if kdir.is_dir():
            for f in kdir.glob("*.md"):
                shutil.copy2(f, rd / f.name)
        else:
            warnings.append(f"{name}: not published yet (no overview/map in the hub); run publish first")
        langs: dict[str, int] = {}
        for f in ix.data["files"].values():
            if f["lang"] != "other":
                langs[f["lang"]] = langs.get(f["lang"], 0) + 1
        meta = {"name": name, "commit": ix.data.get("commit"), "branch": _branch(root), "origin": _origin(root), "langs": langs,
                "symbols": sum(1 for s in ix.data["symbols"] if s["kind"] != "module"), "files": len(ix.data["files"]),
                "generated": ix.data.get("generated"), "catalog_entries": len(entries)}
        (rd / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
        written.append(name)
    if prune:
        for f in (d / "catalog").glob("*.json"):
            if f.stem not in repos:
                f.unlink()
        for rd in (d / "repos").iterdir():
            if rd.is_dir() and rd.name not in repos:
                shutil.rmtree(rd)
    for kind in ("lessons", "rules"):   # shared memory travels with the hub
        src = hub_dir() / kind
        if src.is_dir() and any(src.iterdir()):
            shutil.copytree(src, d / kind, dirs_exist_ok=True)
    from . import docs as docsmod
    got = docsmod.export(d / "docs")
    if got:
        pass
    (d / "INDEX.md").write_text(_render_index(agent_hub.repos(d)), encoding="utf-8")
    result = {"repos": written, "warnings": warnings, "docs": got, "committed": False, "pushed": False}
    if commit:
        _git(d, "add", "-A")
        if _git(d, "diff", "--cached", "--quiet").returncode:
            ident = [] if _git(d, "config", "user.email").stdout.strip() else ["-c", "user.name=devmem", "-c", "user.email=devmem@localhost"]
            r = subprocess.run(["git", "-C", str(d), *ident, "commit", "-q", "-m", f"devmem: sync {len(written)} repo(s)"], capture_output=True, text=True, env=ENV)
            if r.returncode:
                raise RuntimeError("git commit failed: " + (r.stderr or r.stdout).strip()[-300:])
            result["committed"] = True
        else:
            result["note"] = "no changes since the last sync"
    if do_push:
        if not _git(d, "remote").stdout.strip():
            raise RuntimeError("the hub has no git remote; set it to a git URL to share it")
        r = _git(d, "push", "-u", "origin", "HEAD", timeout=300)
        if r.returncode:
            raise RuntimeError("git push failed: " + (r.stderr or r.stdout).strip()[-300:])
        result["pushed"] = True
    return result
