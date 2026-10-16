"""devmem — agent-facing CLI. Reads .devmem/index.json; no network, no LLM."""
from __future__ import annotations

import argparse
import sys

from .index import Index


def _line(s: dict) -> str:
    return f"{s['file']}:{s['line']}  {s['kind']} {s['qualname']}" + (f" — {s['doc']}" if s["doc"] else "")


def cmd_find(ix: Index, a) -> int:
    hits = ix.find(a.query, a.limit)
    for _, s in hits:
        print(_line(s))
    if not hits:
        print("no matches")
    return 0


def cmd_show(ix: Index, a) -> int:
    syms = ix.resolve(a.symbol)
    if not syms:
        print(f"symbol not found: {a.symbol}")
        return 1
    if len(syms) > 1:
        print("ambiguous, use one of:")
        for s in syms:
            print(f"  {s['id']}")
        return 1
    s = syms[0]
    print(f"# {s['file']}:{s['line']}-{s['end_line']}")
    print(ix.source(s))
    callees = ix.callees(s)
    if callees:
        print("\n# calls")
        for c in callees:
            print(f"{c['file']}:{c['line']}  {c['sig']}")
    return 0


def cmd_outline(ix: Index, a) -> int:
    syms = ix.outline(a.file)
    if not syms:
        print(f"no symbols indexed for {a.file}")
        return 1
    for s in syms:
        indent = "    " if s["kind"] == "method" else ""
        print(f"{indent}{s['line']}: {s['sig']}" + (f"  # {s['doc']}" if s["doc"] else ""))
    return 0


def cmd_impact(ix: Index, a) -> int:
    r = ix.impact(a.target)
    if "table" in r:
        print(f"{r['table']}\n  readers: {', '.join(r['readers']) or '-'}\n  writers: {', '.join(r['writers']) or '-'}")
        return 0
    print("targets: " + ", ".join(r["targets"]))
    print("hop 1 (must check): " + (", ".join(f"{i['id']} [{i['conf']}]" for i in r["hop1"]) or "-"))
    print("hop 2 (contract check): " + (", ".join(i["id"] for i in r["hop2"]) or "-"))
    print("tests: " + (", ".join(r["tests"]) or "-"))
    for c in r["capped"]:
        print(f"capped: {c['symbol']} has {c['callers']} callers (widely used utility)")
    print("files: " + ", ".join(r["files"]))
    return 0


def cmd_helpers(ix: Index, a) -> int:
    for s, n in ix.helpers(a.limit):
        print(f"{s['file']}:{s['line']}  {s['sig']}  [used in {n} files]" + (f" — {s['doc']}" if s["doc"] else ""))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="devmem", description="Code knowledge for coding agents")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("find", help="ranked path:line hits for a concept")
    s.add_argument("query"); s.add_argument("-n", "--limit", type=int, default=10); s.set_defaults(fn=cmd_find)
    s = sub.add_parser("show", help="one symbol's body + signatures of what it calls")
    s.add_argument("symbol"); s.set_defaults(fn=cmd_show)
    s = sub.add_parser("outline", help="signatures and docstrings of a file")
    s.add_argument("file"); s.set_defaults(fn=cmd_outline)
    s = sub.add_parser("impact", help="callers, dependents, tests for a symbol or table")
    s.add_argument("target"); s.set_defaults(fn=cmd_impact)
    s = sub.add_parser("helpers", help="reusable utilities already in the repo")
    s.add_argument("-n", "--limit", type=int, default=30); s.set_defaults(fn=cmd_helpers)
    a = p.parse_args(argv)
    try:
        ix = Index.load()
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 2
    return a.fn(ix, a)


if __name__ == "__main__":
    sys.exit(main())
