"""Release watcher: notices when a library any onboarded repo depends on publishes a new release, says what changed and which
repos are affected, refreshes the docs on file to the new version, and (optionally) prepares an upgrade plan per repo.

Runs inside `devmem-app ui` (every `interval_hours`, default 12; 0 = off; DEVMEM_RELEASE_CHECK_HOURS), or on demand with
`devmem-app releases check` (cron-friendly). State is `~/.devmem/releases.json`: the last version seen per package and the
events (newest first). The first check of a package records its version without an event; later checks report anything newer.
An optional webhook (Slack, Teams, Discord or anything that takes a JSON POST) gets one message per check that found news."""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from devmem.schema import atomic_write

from . import docs as docs_mod
from .paths import devmem_home

MAX_EVENTS = 300
LOCK = threading.Lock()


def _path() -> Path:
    return devmem_home() / "releases.json"


def load() -> dict:
    try:
        d = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    d.setdefault("libs", {}); d.setdefault("events", []); d.setdefault("last_check", "")
    return d


def _store(d: dict) -> None:
    _path().parent.mkdir(parents=True, exist_ok=True)
    atomic_write(_path(), json.dumps(d, indent=1))


# ---------- settings ----------
def settings() -> dict:
    try:
        s = (json.loads((devmem_home() / "settings.json").read_text(encoding="utf-8")).get("releases") or {})
    except (OSError, ValueError):
        s = {}
    env = os.environ.get("DEVMEM_RELEASE_CHECK_HOURS")
    hours = s.get("interval_hours", 12)
    try:
        hours = int(env) if env else int(hours)
    except ValueError:
        hours = 12
    return {"interval_hours": max(0, hours), "webhook": os.environ.get("DEVMEM_RELEASE_WEBHOOK") or s.get("webhook", ""),
            "auto_plan": bool(s.get("auto_plan", False)), "refresh_docs": bool(s.get("refresh_docs", True)),
            "from_environment": bool(env or os.environ.get("DEVMEM_RELEASE_WEBHOOK"))}


def save_settings(values: dict) -> dict:
    p = devmem_home() / "settings.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    cur = data.setdefault("releases", {})
    if "interval_hours" in values and values["interval_hours"] is not None:
        cur["interval_hours"] = max(0, min(int(values["interval_hours"]), 24 * 30))
    for k in ("auto_plan", "refresh_docs"):
        if values.get(k) is not None:
            cur[k] = bool(values[k])
    if "webhook" in values and values["webhook"] is not None:
        w = str(values["webhook"]).strip()
        if w and not re.match(r"^https?://", w):
            raise ValueError("the webhook must be an http(s) URL")
        cur["webhook"] = w
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(p, json.dumps(data, indent=1))
    return settings()


# ---------- what to watch ----------
def tracked(repos: dict[str, str]) -> dict[str, dict]:
    """"eco:name" -> {eco, name, repos: {repo: version in use}} over every onboarded repo's dependencies, plus every library
    whose docs came from a registry (so docs you added by hand are watched too)."""
    out: dict[str, dict] = {}
    for repo, path in repos.items():
        root = Path(path)
        if not root.is_dir():
            continue
        try:
            locked = docs_mod.locked_versions(root)
            for d in docs_mod.scan_deps(root):
                key = f"{d['eco']}:{d['name']}"
                using = locked.get((d["eco"], d["name"])) or d["spec"] or ""
                out.setdefault(key, {"eco": d["eco"], "name": d["name"], "repos": {}})["repos"][repo] = using
        except Exception:
            continue
    for lib in docs_mod.list_libs():
        m = re.match(r"^(pypi|npm):(.+?)(?:==.*|@\d.*)?$", str(lib.get("source") or ""))
        if m and lib.get("kind") in ("pypi", "npm"):
            out.setdefault(f"{m.group(1)}:{m.group(2).strip()}", {"eco": m.group(1), "name": m.group(2).strip(), "repos": {}})
    return out


def _vt(s: str) -> str:
    m = re.search(r"\d+(?:\.\d+)*", s or "")
    return m.group(0) if m else ""


def _summary(notes: list[tuple[str, str]]) -> dict:
    from .upgrade import classify
    c = classify(notes)
    pick = lambda k, n: [f"{x['version']}: {x['text']}" for x in c[k][:n]]
    return {"breaking": pick("breaking", 6), "deprecated": pick("deprecated", 4), "security": pick("security", 4), "added": pick("added", 6),
            "counts": {k: len(v) for k, v in c.items()}}


def _post(url: str, ev: list[dict]) -> str:
    lines = [f"devmem: {len(ev)} new release{'s' if len(ev) != 1 else ''}"]
    for e in ev[:20]:
        users = ", ".join(f"{r} ({v or 'unpinned'})" for r, v in e["repos"].items()) or "no onboarded repo"
        flag = " SECURITY" if e["summary"]["security"] else " BREAKING" if e["level"] == "major" or e["summary"]["breaking"] else ""
        lines.append(f"- {e['name']} {e['from']} -> {e['to']} ({e['level']}{flag}); used by {users}" + (f"; notes: {e['url']}" if e.get("url") else ""))
    text = "\n".join(lines)
    req = urllib.request.Request(url, data=json.dumps({"text": text, "content": text[:1900]}).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": docs_mod.UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return f"webhook {r.status}"
    except Exception as e:
        return f"webhook failed: {str(e)[:120]}"


def check(repos: dict[str, str], names: list[str] | None = None, log: Callable[[str], None] = lambda _: None, notes: bool = True) -> dict:
    """One pass over every tracked package. Returns {checked, new: [event], errors: {key: why}, refreshed: {...}, planned: {...}}."""
    from . import sources
    from .upgrade import level
    with LOCK:
        state = load()
    cfg = settings()
    want = tracked(repos)
    if names:
        sel = {n.lower() for n in names}
        want = {k: v for k, v in want.items() if v["name"].lower() in sel or k.lower() in sel}
    errors, events = {}, []

    def one(item):
        key, t = item
        try:
            return key, t, sources.package_meta(t["eco"], t["name"]), None
        except Exception as e:
            return key, t, None, str(e)[:160]
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(one, sorted(want.items())))
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for key, t, meta, err in results:
        prev = state["libs"].get(key) or {}
        if err or not meta or not meta.get("version"):
            errors[key] = err or "no version in the registry answer"
            if prev:
                prev["error"] = errors[key]
            continue
        latest = meta["version"]
        rec = {"eco": t["eco"], "name": t["name"], "latest": latest, "released": meta.get("released", ""), "checked": now,
               "repo": meta["repo"]["web"] if meta.get("repo") else "", "docs_url": meta.get("docs_url") or "",
               "repos": {r: _vt(v) or v for r, v in t["repos"].items()}, "first_seen": prev.get("first_seen") or now,
               "history": prev.get("history", [])[-19:]}
        old = prev.get("latest")
        if old and docs_mod._ver(latest) > docs_mod._ver(old):
            rec["history"].append({"version": latest, "seen": now, "previous": old})
            summ, url = {"breaking": [], "deprecated": [], "security": [], "added": [], "counts": {}}, ""
            if notes:
                try:
                    got = sources.notes_between(meta.get("repo"), old, latest, t["name"])
                    summ, url = _summary(got["notes"]), got["url"]
                except Exception as e:
                    summ["error"] = str(e)[:160]
            ev = {"id": uuid.uuid4().hex[:10], "key": key, "eco": t["eco"], "name": t["name"], "from": old, "to": latest,
                  "level": level(old, latest), "released": meta.get("released", ""), "seen_at": now, "url": url, "summary": summ,
                  "repos": {r: _vt(v) or v for r, v in t["repos"].items()},
                  "behind": {r: level(_vt(v), latest) for r, v in t["repos"].items() if _vt(v)}, "read": False}
            events.append(ev)
            log(f"{t['name']} {old} -> {latest} ({ev['level']})")
        state["libs"][key] = rec
    refreshed, planned, hook = {}, {}, ""
    if events and cfg["refresh_docs"]:
        have = {docs_mod._norm(l["name"]): l["name"] for l in docs_mod.list_libs() if l.get("kind") in ("pypi", "npm")}
        names_on_file = [have[docs_mod._norm(e["name"])] for e in events if docs_mod._norm(e["name"]) in have]
        if names_on_file:
            refreshed = docs_mod.refresh(names_on_file)
            for path in repos.values():
                try:
                    if (Path(path) / ".devmem/docs").is_dir():
                        docs_mod.copy_relevant(Path(path))
                except Exception:
                    pass
    for e in events:                    # keep each repo's libs.json (Upgrades page, libraries.md) in step with what was just seen
        for r in e["repos"]:
            lp = Path(repos.get(r, "")) / ".devmem/libs.json"
            try:
                rows = json.loads(lp.read_text(encoding="utf-8"))
                for row in rows:
                    if row["eco"] == e["eco"] and row["name"] == e["name"]:
                        row["latest"] = e["to"]
                        row["behind"] = docs_mod.behind(row.get("locked") or row["spec"], e["to"])
                atomic_write(lp, json.dumps(rows, indent=1))
            except (OSError, ValueError, KeyError):
                pass
    if events and cfg["auto_plan"]:
        from .upgrade import plan
        for e in events:
            for r in e["repos"]:
                if r in repos and (Path(repos[r]) / ".devmem/index.json").exists() and e["behind"].get(r) not in (None, "none"):
                    try:
                        d = plan(Path(repos[r]), e["name"], e["to"], search=False, eco=e["eco"])
                        planned[f"{r}:{e['name']}"] = f"{len(d['affected'])} change(s) touch this repo"
                    except Exception as ex:
                        planned[f"{r}:{e['name']}"] = f"failed: {str(ex)[:120]}"
    if events and cfg["webhook"]:
        hook = _post(cfg["webhook"], events)
    with LOCK:
        cur = load()                    # merge: a "mark read" may have happened while the network calls ran
        cur["libs"].update(state["libs"])
        cur["events"] = (events + cur["events"])[:MAX_EVENTS]
        cur["last_check"] = now
        cur["last_errors"] = errors
        _store(cur)
    return {"checked": len(results), "new": events, "errors": errors, "refreshed": refreshed, "planned": planned, "webhook": hook, "at": now}


def status(repos: dict[str, str] | None = None) -> dict:
    """What the Upgrades page shows: every watched package (behind first), the event feed and the settings."""
    from .upgrade import level
    st = load()
    want = tracked(repos or {}) if repos is not None else {}
    libs = []
    for key, rec in st["libs"].items():
        using = {r: _vt(v) or v for r, v in (want.get(key, {}).get("repos") or rec.get("repos") or {}).items()}
        behind = {r: level(v, rec["latest"]) for r, v in using.items() if _vt(v)}
        libs.append({**{k: rec.get(k) for k in ("eco", "name", "latest", "released", "checked", "repo", "docs_url", "error")}, "key": key,
                     "repos": using, "behind": {r: b for r, b in behind.items() if b != "none"}})
    rank = {"major": 0, "minor": 1, "patch": 2}
    libs.sort(key=lambda x: (min((rank.get(b, 3) for b in x["behind"].values()), default=4), x["name"]))
    pending = [k for k in want if k not in st["libs"]]
    return {"libs": libs, "events": st["events"][:100], "unread": sum(1 for e in st["events"] if not e.get("read")), "last_check": st["last_check"],
            "last_errors": st.get("last_errors", {}), "not_checked_yet": len(pending), "settings": settings()}


def mark_read(ids: list[str] | None = None) -> int:
    with LOCK:
        st = load()
        n = 0
        for e in st["events"]:
            if (ids is None or e["id"] in ids) and not e.get("read"):
                e["read"] = True
                n += 1
        _store(st)
    return n


def due(now: float | None = None) -> bool:
    h = settings()["interval_hours"]
    if h <= 0:
        return False
    last = load().get("last_check") or ""
    if not last:
        return True
    try:
        import calendar
        t = calendar.timegm(time.strptime(last, "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return True
    return (now or time.time()) - t >= h * 3600


def start(repos: Callable[[], dict[str, str]], log: Callable[[str], None] = print) -> threading.Thread:
    """Background loop for `devmem-app ui`: wait a little after start-up, then check whenever the interval has passed."""
    def loop():
        from .paths import diagnose
        time.sleep(90)
        while True:
            try:
                if due():
                    r = check(repos(), log=lambda m: log(f"new release: {m}"))
                    if r["new"]:
                        log(f"release watcher: {len(r['new'])} new release(s); see the Upgrades page")
            except Exception as e:
                diagnose("releases", f"release check failed: {type(e).__name__}: {e}")
            time.sleep(900)
    th = threading.Thread(target=loop, daemon=True, name="devmem-release-watch")
    th.start()
    return th
