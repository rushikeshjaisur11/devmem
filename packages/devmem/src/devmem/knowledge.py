"""Knowledge-file helpers shared by publisher (app) and CLI: freshness stamps, token budgets, managed blocks."""
from __future__ import annotations

import re
from pathlib import Path

START, END = "<!-- devmem:start -->", "<!-- devmem:end -->"
HEADER_RE = re.compile(r"<!-- devmem: commit=(\S+) generated=(\S+) tokens=(\d+) -->")
KNOWLEDGE_DIR = ".devmem/knowledge"
AGENTS_INDEX_BUDGET = 300
MAP_BUDGET = 1500
FILE_BUDGET = 25000


def est_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def parse_header(text: str):
    m = HEADER_RE.search(text.split("\n", 1)[0])
    return {"commit": m.group(1), "generated": m.group(2), "tokens": int(m.group(3))} if m else None


def replace_managed_block(existing: str, block: str) -> str:
    body = f"{START}\n{block.strip()}\n{END}"
    if START in existing and END in existing:
        pre, rest = existing.split(START, 1)
        _, post = rest.split(END, 1)
        return pre + body + post
    sep = "\n\n" if existing.strip() else ""
    return existing.rstrip() + sep + body + "\n"


def managed_block(text: str) -> str | None:
    if START in text and END in text:
        return text.split(START, 1)[1].split(END, 1)[0].strip()
    return None


def check_knowledge(root: Path, head: str | None = None) -> list[str]:
    """Return problems: missing/stale/over-budget knowledge files and an oversized AGENTS.md index."""
    problems = []
    kdir = root / KNOWLEDGE_DIR
    if not kdir.exists():
        return ["no .devmem/knowledge directory"]
    for f in sorted(kdir.rglob("*.md")):
        text = f.read_text(encoding="utf-8")
        h = parse_header(text)
        rel = f.relative_to(root).as_posix()
        if not h:
            problems.append(f"{rel}: missing freshness header")
            continue
        if head and h["commit"] not in (head, "unknown"):
            problems.append(f"{rel}: stale (generated at {h['commit'][:8]}, HEAD is {head[:8]})")
        limit = MAP_BUDGET + 200 if f.name == "map.md" else FILE_BUDGET
        if est_tokens(text) > limit:
            problems.append(f"{rel}: over token budget ({est_tokens(text)} > {limit})")
    for name in ("AGENTS.md", ".github/copilot-instructions.md"):
        p = root / name
        blk = managed_block(p.read_text(encoding="utf-8")) if p.exists() else None
        if blk is None:
            problems.append(f"{name}: missing devmem block")
        elif est_tokens(blk) > AGENTS_INDEX_BUDGET:
            problems.append(f"{name}: devmem block over budget ({est_tokens(blk)} > {AGENTS_INDEX_BUDGET})")
    return problems
