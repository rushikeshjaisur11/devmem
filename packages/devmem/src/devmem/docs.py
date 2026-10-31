"""Offline library documentation for agents that have no internet.

`devmem-app docs add ...` fetches docs (llms.txt, web pages, PyPI/npm package pages) on a machine that does have access and stores
them as plain markdown under `docs/<lib>/` (in the central hub) and `.devmem/docs/<lib>/` (in each repo that depends on the
library). Each library has a manifest.json with its source, version and fetch date. This module searches them: pure stdlib."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .reuse import stem_tokens

REPO_DOCS = ".devmem/docs"
CHUNK = 1400
STALE_DAYS = 120


_toks = stem_tokens


def _dirs(root: Path | None, extra: list[Path] | None = None) -> list[Path]:
    out: list[Path] = list(extra or [])
    if root is not None:
        out.append(Path(root) / REPO_DOCS)
        from . import hub
        d, _ = hub.resolve(Path(root), auto_sync=False)
        if d:
            out.append(d / "docs")
    return [p for p in out if p.is_dir()]


def libraries(root: Path | None, extra: list[Path] | None = None) -> list[dict]:
    """Manifests of every available library; when the repo and the hub both have one, the more recently fetched wins."""
    best: dict[str, dict] = {}
    for base in _dirs(root, extra):
        for m in sorted(base.glob("*/manifest.json")):
            try:
                man = json.loads(m.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            man["_dir"] = str(m.parent)
            if man["name"] not in best or str(man.get("fetched", "")) > str(best[man["name"]].get("fetched", "")):
                best[man["name"]] = man
    return sorted(best.values(), key=lambda m: m["name"])


def age_days(man: dict) -> int | None:
    try:
        t = time.mktime(time.strptime(str(man["fetched"])[:10], "%Y-%m-%d"))
    except (KeyError, ValueError):
        return None
    return int((time.time() - t) // 86400)


def _chunks(man: dict) -> list[dict]:
    out = []
    d = Path(man["_dir"])
    for pg in man.get("pages", []):
        f = d / pg["file"]
        if not f.exists():
            continue
        title, heading, buf = pg.get("title", ""), pg.get("title", ""), []

        def flush():
            text = "\n".join(buf).strip()
            while text:
                piece, text = text[:CHUNK], text[CHUNK:]
                if text and "\n" in piece:   # break on a line boundary
                    cut = piece.rfind("\n")
                    piece, text = piece[:cut], piece[cut:].lstrip("\n") + text
                if len(piece.strip()) > 30:
                    out.append({"lib": man["name"], "version": man.get("version", ""), "fetched": man.get("fetched", ""),
                                "url": pg.get("url", ""), "title": title, "heading": heading, "text": piece.strip()})
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("<!-- source:"):
                continue
            if re.match(r"#{1,4} ", line):
                flush(); buf = []
                heading = line.lstrip("# ").strip()
            buf.append(line)
        flush()
    return out


def search(root: Path | None, query: str, limit: int = 4, lib: str | None = None, extra: list[Path] | None = None) -> list[dict]:
    import math
    from collections import Counter
    mans = [m for m in libraries(root, extra) if not lib or m["name"].lower() == lib.lower()]
    chunks = [c for m in mans for c in _chunks(m)]
    q = set(_toks(query))
    if not q or not chunks:
        return []
    tfs = [Counter(_toks(f"{c['heading']} {c['heading']} {c['title']} {c['lib']} {c['text']}")) for c in chunks]
    lens = [sum(t.values()) or 1 for t in tfs]
    avg = sum(lens) / len(lens)
    df: Counter = Counter()
    for t in tfs:
        df.update(t.keys())
    n = len(chunks)
    scored = []
    for c, tf, ln in zip(chunks, tfs, lens):
        c["cover"] = round(len(q & set(tf)) / len(q), 2)                                         # share of query words present
        c["hcover"] = round(len(q & set(_toks(c["heading"] + " " + c["title"]))) / len(q), 2)    # ... present in the heading
        s = sum(math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) * tf[t] * 2.2 / (tf[t] + 1.2 * (0.25 + 0.75 * ln / avg)) for t in q if tf.get(t))
        if s > 0:
            scored.append((s, c))
    scored.sort(key=lambda x: (-x[0], x[1]["lib"], x[1]["url"]))
    top = scored[0][0] if scored else 1.0
    out, seen = [], Counter()
    for s, c in scored:
        if seen[(c["lib"], c["url"])] >= 2:   # at most two chunks per page so one long page cannot crowd out the rest
            continue
        seen[(c["lib"], c["url"])] += 1
        out.append({**c, "score": round(s / top, 3)})
        if len(out) >= limit:
            break
    return out


def render(hit: dict, chars: int = 1100) -> str:
    where = f"{hit['lib']}{' ' + hit['version'] if hit['version'] else ''}"
    head = f"[{where}] {hit['heading'] or hit['title']}   {hit['url']}   (fetched {hit['fetched'] or '?'}; match {hit['score']:.2f})"
    return head + "\n" + "\n".join("  " + ln for ln in hit["text"][:chars].splitlines())


def _pkg(module: str) -> str | None:
    """Top-level package of an import: `a.b` -> `a`, `@scope/pkg/x` -> `@scope/pkg`, `pkg/sub` -> `pkg`; relative imports -> None."""
    if not module or module.startswith((".", "/")):
        return None
    parts = module.split("/")
    if module.startswith("@"):
        return "/".join(parts[:2]) if len(parts) > 1 else None
    return parts[0].split(".")[0]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def for_code(ix, files: list[str], text: str = "", query: str = "", limit: int = 3) -> list[dict]:
    """Latest docs for the libraries this code imports, with the best section for the names it uses from each.
    `text`: the code being changed (only libraries whose imported names appear in it count); empty = every import of the files."""
    used: dict[str, set[str]] = {}
    for f in files:
        for imp in ix.data["files"].get(f, {}).get("imports", []):
            p = _pkg(imp.get("module") or "")
            alias = imp.get("alias") or ""
            if p and (not text or (alias and re.search(rf"\b{re.escape(alias)}\b", text))):
                used.setdefault(_norm(p), set()).update(x for x in (alias, imp.get("name")) if x)
    lp = ix.root / ".devmem/libs.json"
    repo_ver = {_norm(r["name"]): r.get("locked") or r.get("spec") or "" for r in (json.loads(lp.read_text(encoding="utf-8")) if lp.exists() else [])}
    out = []
    for man in libraries(ix.root):
        src = str(man.get("source", ""))
        key = next((k for k in (_norm(man["name"]), _norm(src.split(":", 1)[1]) if src.startswith(("pypi:", "npm:")) else "") if k in used), None)
        if not key:
            continue
        names = " ".join(sorted(used[key]))
        hits = search(ix.root, f"{query} {names}".strip(), 1, man["name"])
        uses = repo_ver.get(key) or repo_ver.get(_norm(man["name"])) or ""
        out.append({"lib": man["name"], "version": man.get("version", ""), "fetched": man.get("fetched", ""), "stale": (age_days(man) or 0) > STALE_DAYS,
                    "names": names, "hit": hits[0] if hits else None, "repo_version": uses if _major(uses) not in (None, _major(man.get("version", ""))) else None})
    return out[:limit]


def _major(v: str) -> int | None:
    m = re.search(r"\d+", v or "")
    return int(m.group()) if m else None


def render_pointer(d: dict) -> str:
    where = f"{d['lib']}{' ' + d['version'] if d['version'] else ''} (fetched {d['fetched'] or '?'}{', STALE' if d['stale'] else ''})"
    sec = f": {d['hit']['heading'] or d['hit']['title']}" if d["hit"] else ""
    warn = (f"\n   ! docs describe {d['version']} but this repo uses {d['repo_version']}: confirm the API exists in that version, "
            f"or ask for pinned docs (devmem-app docs add <eco>:{d['lib']}=={d['repo_version']})") if d.get("repo_version") else ""
    return f"{where}{sec}  -> devmem docs \"{d['names']}\" --lib {d['lib']}{warn}"
