"""AI settings: which LLM (if any) devmem may call, set on the AI setup page instead of through environment variables.

Stored in ~/.devmem/settings.json (mode 0600). An environment variable always wins over the file, so a deployment can pin
its configuration. The API key is never sent back to the browser; `public()` returns only whether one is set."""
from __future__ import annotations

import json
import os

from .paths import devmem_home

# provider id -> (label, default model, default embedding model, key needed, environment keys that also select it)
PROVIDERS = {
    "anthropic": ("Claude (Anthropic API)", "claude-haiku-5-5", "", True, ("ANTHROPIC_API_KEY",)),
    "openai": ("OpenAI or any OpenAI-compatible server (Ollama, LM Studio, OpenRouter, Azure)", "gpt-4o-mini", "", False, ("OPENAI_API_KEY",)),
    "gemini": ("Gemini (Google AI Studio API key)", "gemini-2.5-flash", "gemini-embedding-001", True, ("GEMINI_API_KEY",)),
    "vertex": ("Gemini on Vertex AI (API key, or project + gcloud login)", "gemini-2.5-flash", "", False,
               ("DEVMEM_VERTEX_API_KEY", "DEVMEM_VERTEX_PROJECT")),
}
FIELDS = ("provider", "api_key", "model", "base_url", "embed_model", "project", "location", "local_embed")
ENV = {"provider": "DEVMEM_AI_PROVIDER", "api_key": "DEVMEM_AI_KEY", "model": "DEVMEM_AI_MODEL", "base_url": "DEVMEM_AI_BASE_URL",
       "embed_model": "DEVMEM_AI_EMBED_MODEL", "local_embed": "DEVMEM_LOCAL_EMBED"}


def _path():
    return devmem_home() / "settings.json"


def stored() -> dict:
    try:
        return json.loads(_path().read_text(encoding="utf-8")).get("ai", {})
    except (OSError, ValueError):
        return {}


def save(values: dict) -> dict:
    """Merge the given fields into the file. An empty api_key keeps the stored one; api_key None removes it."""
    cur = stored()
    for k in FIELDS:
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
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    data["ai"] = cur
    p.write_text(json.dumps(data, indent=1), encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return cur


def effective() -> dict:
    """Stored settings overlaid with the environment. Older DEVMEM_VERTEX_* variables and the providers' usual key
    variables (ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY) are picked up when nothing else chose a provider."""
    env = os.environ.get
    cfg = dict(stored())
    src = {k: "saved" for k in cfg}
    for k, name in ENV.items():
        if env(name):
            cfg[k], src[k] = env(name), "environment"
    if not cfg.get("provider"):
        if env("DEVMEM_VERTEX_API_KEY") or env("DEVMEM_VERTEX_PROJECT"):
            cfg["provider"], src["provider"] = "vertex", "environment"
        else:
            for pid, spec in PROVIDERS.items():
                if pid != "vertex" and any(env(v) for v in spec[4]):
                    cfg["provider"], src["provider"] = pid, "environment"
                    break
    pid = cfg.get("provider")
    if pid == "vertex":
        for k, name in (("api_key", "DEVMEM_VERTEX_API_KEY"), ("project", "DEVMEM_VERTEX_PROJECT"), ("location", "DEVMEM_VERTEX_LOCATION"),
                        ("model", "DEVMEM_VERTEX_MODEL"), ("embed_model", "DEVMEM_VERTEX_EMBED_MODEL")):
            if env(name) and src.get(k) != "environment":
                cfg[k], src[k] = env(name), "environment"
        if not cfg.get("api_key") and env("GOOGLE_API_KEY"):
            cfg["api_key"], src["api_key"] = env("GOOGLE_API_KEY"), "environment"
    elif pid in PROVIDERS and not cfg.get("api_key"):
        for v in PROVIDERS[pid][4]:
            if env(v):
                cfg["api_key"], src["api_key"] = env(v), "environment"
                break
    if pid in PROVIDERS:
        cfg.setdefault("model", PROVIDERS[pid][1])
    cfg["_source"] = src
    return cfg


def configured(cfg: dict | None = None) -> bool:
    cfg = cfg or effective()
    pid = cfg.get("provider")
    if pid not in PROVIDERS:
        return False
    if pid == "vertex":
        return bool(cfg.get("api_key") or cfg.get("project"))
    if pid == "openai":   # a local server (Ollama, LM Studio) needs a base URL but no key
        return bool(cfg.get("api_key") or cfg.get("base_url"))
    return bool(cfg.get("api_key"))


def public() -> dict:
    """What the browser may see: everything except the key itself."""
    cfg = effective()
    src = cfg.pop("_source")
    key = cfg.pop("api_key", "")
    return {**{k: cfg.get(k, "") for k in FIELDS if k != "api_key"}, "has_key": bool(key),
            "key_hint": f"…{key[-4:]}" if len(key) >= 12 else "", "configured": configured({**cfg, "api_key": key}),
            "from_environment": sorted(k for k, v in src.items() if v == "environment"),
            "providers": [{"id": pid, "label": s[0], "model": s[1], "embed_model": s[2], "needs_key": s[3]} for pid, s in PROVIDERS.items()]}
