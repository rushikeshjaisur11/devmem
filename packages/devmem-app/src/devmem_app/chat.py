"""Chat: retrieval over every onboarded repo (symbols, files, tables, lessons, rules) plus an optional LLM answer.

Retrieval is local BM25 and needs no network. An LLM is used only when configured (Vertex via DEVMEM_VERTEX_PROJECT)
and only ever sees the retrieved snippets, never whole repos. Every answer carries code pointers (repo:path:line)."""

import hashlib
import json
import math
import os
import re
from collections import Counter
from pathlib import Path
from typing import Protocol

from devmem import memory as m
from devmem.index import Index

from .paths import devmem_home

STOP = m.STOP | {"what", "where", "which", "does", "who", "when", "why", "are", "was", "be", "can", "use", "used", "uses",
                 "find", "show", "me", "about", "from", "by", "or", "if", "i", "our", "my", "there"}
MAX_SNIPPET_LINES = 14
MAX_CONTEXT_CHARS = 6000


def tokenize(text: str) -> list[str]:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    out = []
    for w in re.findall(r"[A-Za-z][A-Za-z0-9]*|\d+", text):
        w = w.lower()
        if w in STOP or len(w) < 2:
            continue
        out.append(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w)
    return out


# ---------- corpus ----------
def build_docs(repo: str, root: Path, ix: Index) -> list[dict]:
    docs = []
    for s in ix.data["symbols"]:
        if s["kind"] == "module":
            continue
        name_toks = f"{s['name']} {s['qualname']} " * 3
        docs.append({"repo": repo, "kind": "test" if s.get("is_test") else "symbol", "ref": s["id"], "path": s["file"],
                     "line": s["line"], "title": f"{s['qualname']}{s.get('sig') or ''}" if s["kind"] != "class" else f"class {s['qualname']}",
                     "text": f"{name_toks} {s['file']} {s.get('sig') or ''} {s.get('doc') or ''}"})
    for path, f in ix.data["files"].items():
        if f["lang"] == "other":
            continue
        docs.append({"repo": repo, "kind": "file", "ref": path, "path": path, "line": 1, "title": path,
                     "text": f"{path} {path} {' '.join(i['module'] for i in f.get('imports', []))} {' '.join(t['name'] for t in f.get('tables', []))}"})
    tables = {}
    for e in ix.data["edges"]:
        if e["dst"].startswith("table:"):
            tables.setdefault(e["dst"][6:], set()).add((e["src"], e["type"]))
    for t, users in tables.items():
        src = sorted({u[0] for u in users})
        docs.append({"repo": repo, "kind": "table", "ref": f"table:{t}", "path": src[0].split("#")[0], "line": 1,
                     "title": f"table {t}", "text": f"{t} {t} {t} table {' '.join(src)}",
                     "note": ", ".join(f"{u[1]} by {u[0]}" for u in sorted(users))[:300]})
    for l in m.load_lessons(root):
        docs.append({"repo": repo, "kind": "lesson", "ref": l["id"], "path": Path(l["path"]).name, "line": 1,
                     "title": l.get("title", l["id"]),
                     "text": f"{l.get('title', '')} {l.get('title', '')} {l['problem']} {l['fix']} {' '.join(l.get('tags') or [])}",
                     "body": f"Problem: {l['problem']}\nFix: {l['fix']}"})
    for r in m.load_rules(root):
        docs.append({"repo": repo, "kind": "rule", "ref": r["id"], "path": f".devmem/rules/{r['id']}", "line": 1,
                     "title": f"rule {r['id']}", "text": r["text"], "body": r["text"]})
    return docs


class Corpus:
    def __init__(self, docs: list[dict]):
        self.docs = docs
        self.tf = [Counter(tokenize(d["text"])) for d in docs]
        self.len = [sum(c.values()) or 1 for c in self.tf]
        self.avg = sum(self.len) / max(len(docs), 1)
        df: Counter = Counter()
        for c in self.tf:
            df.update(c.keys())
        n = len(docs)
        self.idf = {t: math.log(1 + (n - k + 0.5) / (k + 0.5)) for t, k in df.items()}

    def search(self, query: str, limit: int = 30) -> list[tuple[float, dict]]:
        q = tokenize(query)
        if not q:
            return []
        scored = []
        for i, d in enumerate(self.docs):
            s = 0.0
            for t in set(q):
                f = self.tf[i].get(t, 0)
                if f:
                    s += self.idf.get(t, 0) * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * self.len[i] / self.avg))
            if s > 0:
                scored.append((s, d))
        scored.sort(key=lambda x: -x[0])
        return scored[:limit]


_cache: dict[tuple, tuple[float, Corpus, Index]] = {}


def corpus_for(repo: str, root: Path) -> tuple[Corpus, Index] | None:
    p = root / ".devmem/index.json"
    if not p.exists():
        return None
    mt = (p.stat().st_mtime, max([f.stat().st_mtime for f in (root / ".devmem/lessons").glob("*.md")] or [0]),
          max([f.stat().st_mtime for f in (root / ".devmem/rules").glob("*.md")] or [0]))
    key = (repo, str(root))
    hit = _cache.get(key)
    if not hit or hit[0] != mt:
        ix = Index.load(root)
        _cache[key] = (mt, Corpus(build_docs(repo, root, ix)), ix)
    return _cache[key][1], _cache[key][2]


# ---------- providers (optional) ----------
class Provider(Protocol):
    name: str
    def generate(self, prompt: str) -> str: ...
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class VertexProvider:
    """Gemini on Vertex AI through google-genai (pip install google-genai): an API key (Vertex express mode)
    when one is given, otherwise a project with ADC credentials (gcloud auth application-default login)."""
    def __init__(self, project: str | None, location: str, model: str, embed_model: str | None, api_key: str | None = None):
        from google import genai  # lazy: only needed when configured
        self.client = (genai.Client(vertexai=True, api_key=api_key) if api_key
                       else genai.Client(vertexai=True, project=project, location=location))
        self.model, self.embed_model, self.name = model, embed_model, f"vertex:{model}"

    def generate(self, prompt: str) -> str:
        r = self.client.models.generate_content(model=self.model, contents=prompt)
        return (r.text or "").strip()

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.embed_model:
            raise NotImplementedError
        out = []
        for i in range(0, len(texts), 50):
            r = self.client.models.embed_content(model=self.embed_model, contents=texts[i:i + 50])
            out += [list(e.values) for e in r.embeddings]
        return out


class LocalEmbedder:
    """Offline meaning-based ranking with a small static embedding model (pip install model2vec). Opt in with
    DEVMEM_LOCAL_EMBED=1 (default model) or =<huggingface model id>; the model is downloaded once, then runs offline on CPU."""
    def __init__(self, model: str):
        from model2vec import StaticModel   # lazy: optional dependency
        self.m, self.name = StaticModel.from_pretrained(model), f"local:{model}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [list(map(float, v)) for v in self.m.encode(texts)]


_local: dict = {}


def local_embedder() -> LocalEmbedder | None:
    spec = os.environ.get("DEVMEM_LOCAL_EMBED", "")
    if not spec or spec == "0":
        return None
    model = "minishlab/potion-base-8M" if spec == "1" else spec
    if model not in _local:
        _local[model] = LocalEmbedder(model)
    return _local[model]


def _api_key() -> str | None:
    return os.environ.get("DEVMEM_VERTEX_API_KEY") or os.environ.get("GOOGLE_API_KEY") or None


def provider_from_env() -> Provider | None:
    project, key = os.environ.get("DEVMEM_VERTEX_PROJECT"), _api_key()
    if not (project or key):
        return None
    return VertexProvider(project, os.environ.get("DEVMEM_VERTEX_LOCATION", "us-central1"),
                          os.environ.get("DEVMEM_VERTEX_MODEL", "gemini-2.5-flash"),
                          os.environ.get("DEVMEM_VERTEX_EMBED_MODEL") or None, key)


def llm_status() -> dict:
    p = os.environ.get("DEVMEM_VERTEX_PROJECT") or _api_key()
    return {"configured": bool(p), "model": os.environ.get("DEVMEM_VERTEX_MODEL", "gemini-2.5-flash") if p else None,
            "embeddings": bool(p and os.environ.get("DEVMEM_VERTEX_EMBED_MODEL")) or os.environ.get("DEVMEM_LOCAL_EMBED", "0") != "0"}


def _cos(a, b) -> float:
    d = sum(x * y for x, y in zip(a, b))
    n = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return d / n if n else 0.0


def _embed_cached(provider: Provider, texts: list[str]) -> list[list[float]]:
    path = devmem_home() / "embed-cache.json"
    cache = json.loads(path.read_text()) if path.exists() else {}
    keys = [hashlib.sha1(f"{getattr(provider, 'name', '')}|{t}".encode()).hexdigest() for t in texts]
    miss = [i for i, k in enumerate(keys) if k not in cache]
    if miss:
        for i, v in zip(miss, provider.embed([texts[i] for i in miss])):
            cache[keys[i]] = v
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache))
    return [cache[k] for k in keys]


# ---------- retrieval + answer ----------
def snippet(ix: Index, d: dict) -> str:
    if d["kind"] in ("lesson", "rule"):
        return d["body"]
    if d["kind"] in ("symbol", "test"):
        s = ix.symbols.get(d["ref"])
        if s:
            try:
                lines = ix.source(s).splitlines()
            except Exception:
                return ""
            more = len(lines) - MAX_SNIPPET_LINES
            return "\n".join(lines[:MAX_SNIPPET_LINES]) + (f"\n… (+{more} lines)" if more > 0 else "")
    return d.get("note", "")


def retrieve(question: str, repos: dict[str, Path], limit: int = 8, provider: Provider | None = None) -> list[dict]:
    cands: list[tuple[float, dict, Index]] = []
    for name, root in repos.items():
        got = corpus_for(name, root)
        if not got:
            continue
        c, ix = got
        cands += [(s, d, ix) for s, d in c.search(question, 30)]
    cands.sort(key=lambda x: -x[0])
    cands = cands[:30]
    if not cands:
        return []
    top = cands[0][0]
    scores = [s / top for s, _, _ in cands]
    emb = provider if provider is not None and getattr(provider, "embed_model", None) else None
    try:
        emb = emb or local_embedder()
    except ImportError:
        emb = None   # DEVMEM_LOCAL_EMBED set but model2vec not installed: keyword ranking only
    if emb is not None:
        try:
            vecs = _embed_cached(emb, [question] + [d["title"] + " " + d["text"][:300] for _, d, _ in cands])
            scores = [0.5 * s + 0.5 * max(_cos(vecs[0], v), 0) for s, v in zip(scores, vecs[1:])]
        except Exception:
            pass  # embeddings are an optional boost; BM25 order stands
    order = sorted(range(len(cands)), key=lambda i: -scores[i])
    # lessons and rules are already-solved knowledge: float them above plain code hits when equally relevant
    hits, per_file = [], Counter()
    for i in order:
        _, d, ix = cands[i]
        if d["kind"] == "file" and any(h["path"] == d["path"] and h["repo"] == d["repo"] for h in hits):
            continue
        if per_file[(d["repo"], d["path"])] >= 3:
            continue
        per_file[(d["repo"], d["path"])] += 1
        hits.append({"n": len(hits) + 1, "repo": d["repo"], "kind": d["kind"], "ref": d["ref"], "path": d["path"],
                     "line": d["line"], "title": d["title"], "snippet": snippet(ix, d), "score": round(scores[i], 3),
                     "pointer": f"{d['repo']}:{d['path']}:{d['line']}"})
        if len(hits) >= limit:
            break
    return hits


def build_prompt(question: str, hits: list[dict]) -> str:
    ctx, used = [], 0
    for h in hits:
        block = f"[{h['n']}] {h['kind']} {h['title']} ({h['pointer']})\n{h['snippet']}".rstrip()
        if used + len(block) > MAX_CONTEXT_CHARS:
            break
        ctx.append(block); used += len(block)
    return ("You answer questions about a company's code repositories using ONLY the numbered context below. "
            "Cite sources as [n] after each claim. If the context does not contain the answer, say so and name what to look at next. "
            "Be concise (under 150 words). Do not invent files or functions.\n\n"
            f"Context:\n" + "\n\n".join(ctx) + f"\n\nQuestion: {question}\nAnswer:")


def extractive(hits: list[dict]) -> str:
    if not hits:
        return "No matches in the indexed repos. Try other words, or index the repo that should contain this."
    kinds = Counter(h["kind"] for h in hits)
    lines = [f"{len(hits)} relevant places ({', '.join(f'{v} {k}' for k, v in kinds.items())}), best first:"]
    for h in hits[:5]:
        lines.append(f"[{h['n']}] {h['title']}  — {h['pointer']}")
    return "\n".join(lines)


def _doc_hits(question: str, start: int) -> list[dict]:
    """Library docs from the docs store (devmem-app docs add) as extra sources, so answers can cite the current API."""
    from devmem import docs as core_docs

    from .docs import docs_dir
    out = []
    for h in [h for h in core_docs.search(None, question, 3, extra=[docs_dir()]) if h["cover"] >= 0.34 and h["score"] >= 0.5][:2]:
        out.append({"n": start + len(out), "repo": h["lib"], "kind": "doc", "ref": h["url"], "path": h["url"], "line": 0,
                    "title": f"{h['lib']} {h['version']}: {h['heading'] or h['title']}".replace("  ", " "), "snippet": h["text"][:900],
                    "score": h["score"], "pointer": f"{h['lib']} docs {h['url']} (fetched {h['fetched']})"})
    return out


def ask(question: str, repos: dict[str, Path], limit: int = 8, use_llm: bool = False, provider: Provider | None = None) -> dict:
    question = question.strip()
    if not question:
        return {"answer": "Ask a question about your repos.", "hits": [], "mode": "none"}
    prov = provider if provider is not None else (provider_from_env() if use_llm else None)
    hits = retrieve(question, repos, limit, prov)
    hits += _doc_hits(question, len(hits) + 1)
    if prov is None or not hits:
        return {"answer": extractive(hits), "hits": hits, "mode": "retrieval"}
    try:
        text = prov.generate(build_prompt(question, hits))
    except Exception as e:
        return {"answer": extractive(hits), "hits": hits, "mode": "retrieval", "llm_error": str(e)[:300]}
    valid = {h["n"] for h in hits}
    cited = sorted({int(n) for n in re.findall(r"\[(\d+)\]", text)})
    bad = [n for n in cited if n not in valid]
    for n in bad:
        text = text.replace(f"[{n}]", "")
    return {"answer": text, "hits": hits, "mode": "llm", "model": getattr(prov, "name", "llm"),
            "cited": [n for n in cited if n in valid], "dropped_citations": bad}
