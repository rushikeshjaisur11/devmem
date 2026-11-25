"""Web search for upgrade research: migration guides, breaking-change write-ups and issues about a version jump.

Providers (Settings -> Upgrades, or environment variables, which win):
  duckduckgo  no key; the default. Best effort: it sometimes answers automated queries with a challenge page.
  searxng     your own SearXNG instance (DEVMEM_SEARXNG_URL), JSON API, no key.
  brave       Brave Search API (BRAVE_API_KEY).
  tavily      Tavily (TAVILY_API_KEY).
  google      Programmable Search (GOOGLE_CSE_KEY + GOOGLE_CSE_ID).
  off         never search.
Only the query leaves the machine: the library name and versions, never code."""
from __future__ import annotations

import html
import json
import os
import re
import urllib.parse
import urllib.request

from devmem.schema import atomic_write

from .paths import devmem_home

PROVIDERS = {
    "duckduckgo": ("DuckDuckGo (no key, best effort)", ()),
    "searxng": ("SearXNG instance (your own, no key)", ("url",)),
    "brave": ("Brave Search API", ("api_key",)),
    "tavily": ("Tavily", ("api_key",)),
    "google": ("Google Programmable Search", ("api_key", "cx")),
    "off": ("Off: no web search", ()),
}
ENV_KEYS = {"brave": "BRAVE_API_KEY", "tavily": "TAVILY_API_KEY", "google": "GOOGLE_CSE_KEY"}
UA_BROWSER = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


class SearchError(RuntimeError):
    pass


def _file():
    return devmem_home() / "settings.json"


def stored() -> dict:
    try:
        return json.loads(_file().read_text(encoding="utf-8")).get("search", {}) or {}
    except (OSError, ValueError):
        return {}


def save(values: dict) -> dict:
    """Merge provider / url / cx / api_key into settings.json. An empty api_key keeps the stored one; None removes it."""
    cur = stored()
    for k in ("provider", "url", "cx", "api_key"):
        if k not in values:
            continue
        v = values[k]
        if k == "api_key" and v == "":
            continue
        if v is None or v == "":
            cur.pop(k, None)
        else:
            cur[k] = str(v).strip()
    if cur.get("provider") not in PROVIDERS:
        cur.pop("provider", None)
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data["search"] = cur
    _file().parent.mkdir(parents=True, exist_ok=True)
    atomic_write(_file(), json.dumps(data, indent=1))
    try:
        os.chmod(_file(), 0o600)
    except OSError:
        pass
    return cur


def effective() -> dict:
    """The provider in use and its settings: the environment overrides the file; a key in the environment alone picks its provider."""
    env = os.environ.get
    cfg = dict(stored())
    if env("DEVMEM_SEARCH"):
        cfg["provider"] = env("DEVMEM_SEARCH", "").strip().lower()
    if env("DEVMEM_SEARXNG_URL"):
        cfg["url"] = env("DEVMEM_SEARXNG_URL")
        cfg.setdefault("provider", "searxng")
    if env("GOOGLE_CSE_ID"):
        cfg["cx"] = env("GOOGLE_CSE_ID")
    for prov, name in ENV_KEYS.items():
        if env(name) and cfg.get("provider", prov) == prov:
            cfg["api_key"] = env(name)
            cfg.setdefault("provider", prov)
    if env("DEVMEM_SEARCH_KEY"):
        cfg["api_key"] = env("DEVMEM_SEARCH_KEY")
    if cfg.get("provider") not in PROVIDERS:
        cfg["provider"] = "duckduckgo"
    return cfg


def public() -> dict:
    cfg = effective()
    need = PROVIDERS[cfg["provider"]][1]
    return {"provider": cfg["provider"], "url": cfg.get("url", ""), "cx": cfg.get("cx", ""), "key_set": bool(cfg.get("api_key")),
            "ready": all(cfg.get(n) for n in need), "from_environment": any(os.environ.get(n) for n in ("DEVMEM_SEARCH", "DEVMEM_SEARXNG_URL", "DEVMEM_SEARCH_KEY", *ENV_KEYS.values())),
            "providers": [{"id": k, "label": v[0], "needs": list(v[1])} for k, v in PROVIDERS.items()]}


def _json(url: str, data: bytes | None = None, headers: dict | None = None, timeout: int = 20) -> dict:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA_BROWSER, "Accept": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(5_000_000).decode("utf-8", "replace"))


def parse_ddg(page: str, n: int = 10) -> list[dict]:
    """Results of DuckDuckGo's HTML page; its result links go through a /l/?uddg=<real url> redirect."""
    out = []
    for m in re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=<a[^>]+class="result__a"|$)', page, re.S):
        href = html.unescape(m.group(1))
        if "uddg=" in href:
            href = urllib.parse.unquote(urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg", [href])[0])
        if href.startswith("//"):
            href = "https:" + href
        if not href.startswith("http") or "duckduckgo.com/y.js" in href:
            continue
        snip = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', m.group(3), re.S)
        out.append({"title": html.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip(), "url": href,
                    "snippet": html.unescape(re.sub(r"<[^>]+>", "", snip.group(1))).strip() if snip else ""})
        if len(out) >= n:
            break
    return out


def _ddg(query: str, n: int) -> list[dict]:
    req = urllib.request.Request("https://html.duckduckgo.com/html/", data=urllib.parse.urlencode({"q": query}).encode(),
                                 headers={"User-Agent": UA_BROWSER, "Content-Type": "application/x-www-form-urlencoded", "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=20) as r:
        status, page = r.status, r.read(2_000_000).decode("utf-8", "replace")
    if status != 200 or "result__a" not in page:
        raise SearchError("DuckDuckGo did not return results to an automated query from this network (it shows a challenge page); "
                          "set up SearXNG, Brave, Tavily or Google on the Upgrades page")
    return parse_ddg(page, n)


def search(query: str, n: int = 6, cfg: dict | None = None) -> list[dict]:
    """[{title, url, snippet}] from the configured provider. Raises SearchError with a reason a person can act on."""
    cfg = cfg or effective()
    prov = cfg.get("provider", "duckduckgo")
    missing = [x for x in PROVIDERS.get(prov, ("", ()))[1] if not cfg.get(x)]
    if prov == "off":
        raise SearchError("web search is turned off (Upgrades page or DEVMEM_SEARCH=off)")
    if missing:
        raise SearchError(f"{PROVIDERS[prov][0]} needs {', '.join(missing)}: set it on the Upgrades page")
    try:
        if prov == "duckduckgo":
            return _ddg(query, n)
        if prov == "searxng":
            d = _json(cfg["url"].rstrip("/") + "/search?" + urllib.parse.urlencode({"q": query, "format": "json"}))
            rows = [{"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")} for r in d.get("results", [])]
        elif prov == "brave":
            d = _json("https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode({"q": query, "count": n}),
                      headers={"X-Subscription-Token": cfg["api_key"]})
            rows = [{"title": r.get("title", ""), "url": r.get("url", ""), "snippet": re.sub(r"<[^>]+>", "", r.get("description", ""))}
                    for r in (d.get("web") or {}).get("results", [])]
        elif prov == "tavily":
            d = _json("https://api.tavily.com/search", json.dumps({"query": query, "max_results": n, "search_depth": "basic"}).encode(),
                      {"Content-Type": "application/json", "Authorization": f"Bearer {cfg['api_key']}"})
            rows = [{"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")} for r in d.get("results", [])]
        else:   # google
            d = _json("https://www.googleapis.com/customsearch/v1?" + urllib.parse.urlencode({"key": cfg["api_key"], "cx": cfg["cx"], "q": query, "num": min(n, 10)}))
            rows = [{"title": r.get("title", ""), "url": r.get("link", ""), "snippet": r.get("snippet", "")} for r in d.get("items", [])]
    except SearchError:
        raise
    except Exception as e:
        raise SearchError(f"{PROVIDERS[prov][0]} failed: {str(e)[:160]}") from e
    return [r for r in rows if str(r.get("url", "")).startswith("http")][:n]
