"""Token-level clone detection (within a repo or across repos). Identifiers/literals are normalised, so
renamed copies match. A clone is a run of >= WINDOW matching tokens spanning >= MIN_LINES lines."""
from __future__ import annotations

import io
import keyword
import tokenize
from collections import defaultdict
from pathlib import Path

WINDOW = 40
MIN_LINES = 6
SKIP = {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.ENCODING,
        tokenize.ENDMARKER}


def _tokens(src: str) -> list[tuple[str, int]]:
    out = []
    try:
        for t in tokenize.generate_tokens(io.StringIO(src).readline):
            if t.type in SKIP:
                continue
            if t.type == tokenize.NAME:
                out.append((t.string if keyword.iskeyword(t.string) else "I", t.start[0]))
            elif t.type == tokenize.STRING:
                out.append(("S", t.start[0]))
            elif t.type == tokenize.NUMBER:
                out.append(("N", t.start[0]))
            else:
                out.append((t.string, t.start[0]))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    return out


def find_clones(sources: dict[str, str], top: int = 20) -> list[dict]:
    """sources: {label: python source} where label is 'repo:path' or 'path'."""
    toks = {k: _tokens(v) for k, v in sources.items()}
    seen = defaultdict(list)  # window hash -> [(label, start)]
    for label, ts in toks.items():
        kinds = [t[0] for t in ts]
        for i in range(len(kinds) - WINDOW + 1):
            seen[hash(tuple(kinds[i:i + WINDOW]))].append((label, i))
    active: dict[tuple, list[int]] = {}
    done: list[tuple[tuple, list[int]]] = []
    for label, ts in toks.items():
        kinds = [t[0] for t in ts]
        keys_prev = set()
        for i in range(len(kinds) - WINDOW + 1):
            win = tuple(kinds[i:i + WINDOW])
            keys_now = set()
            for other, j in seen[hash(win)]:
                if (other, j) <= (label, i):
                    continue
                if other == label and abs(j - i) < WINDOW:
                    continue
                if win != tuple(t[0] for t in toks[other][j:j + WINDOW]):
                    continue  # hash collision
                keys_now.add((label, other, j - i))
            for key in keys_now:
                if key in active and key in keys_prev:
                    active[key][1] = i + WINDOW
                else:
                    if key in active:
                        done.append((key, active.pop(key)))
                    active[key] = [i, i + WINDOW]
            for key in keys_prev - keys_now:
                done.append((key, active.pop(key)))
            keys_prev = keys_now
        for key in keys_prev:
            done.append((key, active.pop(key)))
    out = []
    for (a, b, delta), (s_, e) in done:
        la, lb = toks[a], toks[b]
        l1, l2 = la[s_][1], la[e - 1][1]
        m1, m2 = lb[s_ + delta][1], lb[e - 1 + delta][1]
        if l2 - l1 + 1 >= MIN_LINES:
            out.append({"a": a, "a_lines": [l1, l2], "b": b, "b_lines": [m1, m2], "tokens": e - s_})
    out.sort(key=lambda c: -c["tokens"])
    dedup, seen_pairs = [], set()
    for c in out:
        k = (c["a"], c["b"], c["a_lines"][0] // 5, c["b_lines"][0] // 5)
        if k not in seen_pairs:
            seen_pairs.add(k); dedup.append(c)
    return dedup[:top]


def clones_for_repo(root: Path, files: list[str], label: str = "") -> list[dict]:
    srcs = {}
    for f in files:
        if f.endswith(".py") and "/tests/" not in f"/{f}" and not Path(f).name.startswith("test_"):
            try:
                srcs[f"{label}{f}"] = (Path(root) / f).read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
    return find_clones(srcs)
