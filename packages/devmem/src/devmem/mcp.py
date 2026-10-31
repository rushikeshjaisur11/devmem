"""`devmem mcp`: the agent commands as MCP tools over stdio (newline-delimited JSON-RPC 2.0), stdlib only so the bundled wheel
still works offline. Stateless: every tools/call is answered on its own, which is what the 2026-07-28 spec expects; `initialize`
from older clients is still answered so they connect too. Tools run the same code as the CLI, so output budgets and the usage
log apply unchanged. Run it in the repo (or set DEVMEM_ROOT)."""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys

LATEST = "2026-07-28"


def _s(desc: str, **props) -> dict:
    req = [k for k, v in props.items() if not v.pop("optional", False)]
    return {"description": desc, "inputSchema": {"type": "object", "properties": props, "required": req}}


S = lambda d, **kw: {"type": "string", "description": d, **kw}
I = lambda d: {"type": "integer", "description": d, "optional": True}
B = lambda d: {"type": "boolean", "description": d, "optional": True}
L = lambda d: {"type": "array", "items": {"type": "string"}, "description": d, "optional": True}

# name -> (schema, argv builder). Descriptions say when to use the tool instead of reading files.
TOOLS = {
    "devmem_recall": (_s("Start of every task: lessons, rules, code other repos solved, and the latest library docs for the code involved.",
                         task=S("the task in one sentence"), files=L("files you expect to touch")),
                      lambda a: ["recall", a["task"], *(["--files", *a["files"]] if a.get("files") else [])]),
    "devmem_find": (_s("Locate code by concept; returns path:line hits. Use before opening any file.",
                       query=S("2-4 words"), limit=I("max hits, default 10")),
                    lambda a: ["find", a["query"], "-n", str(a.get("limit", 10))]),
    "devmem_show": (_s("One symbol's body (first 80 lines), what it calls, and docs for the libraries it uses. Use instead of reading the file.",
                       symbol=S("path#qualname from find, or a name"), full=B("whole body")),
                    lambda a: ["show", a["symbol"], *(["--full"] if a.get("full") else [])]),
    "devmem_outline": (_s("Signatures and docstrings of a file.", file=S("repo-relative path")), lambda a: ["outline", a["file"]]),
    "devmem_impact": (_s("Before editing: callers (hop 1 must still work), callers of callers, tests, co-changed files. Also table or table.column.",
                         target=S("symbol, table or table.column"), all=B("every item instead of 12 per list")),
                      lambda a: ["impact", a["target"], *(["--all"] if a.get("all") else [])]),
    "devmem_tests_for": (_s("Exact command for the tests affected by changed files.", files=L("default: files changed vs HEAD")),
                         lambda a: ["tests-for", *(a.get("files") or [])]),
    "devmem_reuse": (_s("Code other onboarded repos already wrote for a need, with the effort to adapt it.", need=S("what you need")),
                     lambda a: ["reuse", a["need"]]),
    "devmem_docs": (_s("Offline copy of the latest library docs. Give a query, or `for` a file/symbol to get docs for the libraries it imports.",
                       query=S("what you need", optional=True), lib=S("only this library", optional=True), **{"for": S("file or symbol", optional=True)}),
                    lambda a: ["docs", *([a["query"]] if a.get("query") else []), *(["--lib", a["lib"]] if a.get("lib") else []),
                               *(["--for", a["for"]] if a.get("for") else [])]),
    "devmem_helpers": (_s("Reusable utilities already in this repo; use them before writing a new helper."), lambda a: ["helpers", "-n", "20"]),
    "devmem_check": (_s("Lint, types and only the tests affected by your diff, compact output. Runs the repo's commands."),
                     lambda a: ["check"]),
    "devmem_diffcheck": (_s("Before each commit: blocks oversized, out-of-scope or test-weakening diffs."), lambda a: ["diffcheck"]),
}


def call(name: str, args: dict) -> tuple[str, bool]:
    from .cli import run
    schema = TOOLS[name][0]["inputSchema"]
    missing = [k for k in schema["required"] if not args.get(k)]
    if missing:
        return f"missing argument(s): {', '.join(missing)}; schema: {json.dumps(schema['properties'])}", True
    buf, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        try:
            code = run(TOOLS[name][1](args), via="mcp")
        except SystemExit as e:   # argparse rejects bad arguments by exiting
            code = e.code or 1
    text = (buf.getvalue() + err.getvalue()).strip() or "(no output)"
    return text, code != 0


def handle(msg: dict) -> dict | None:
    mid, method, params = msg.get("id"), msg.get("method", ""), msg.get("params") or {}
    if mid is None:   # notification: never answered
        return None
    if method == "initialize":
        from .cli import _version
        res = {"protocolVersion": params.get("protocolVersion", LATEST), "capabilities": {"tools": {}},
               "serverInfo": {"name": "devmem", "version": _version()}}
    elif method == "ping":
        res = {}
    elif method == "tools/list":
        res = {"tools": [{"name": n, **t[0]} for n, t in TOOLS.items()]}
    elif method == "tools/call" and params.get("name") in TOOLS:
        text, is_err = call(params["name"], params.get("arguments") or {})
        res = {"content": [{"type": "text", "text": text}], "isError": is_err}
    elif method == "tools/call":
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"unknown tool {params.get('name')!r}; tools/list has the names"}}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not supported: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": res}


def serve() -> int:
    root = os.environ.get("DEVMEM_ROOT", "")
    if root and os.path.isdir(root):   # a host that does not expand ${...} passes the literal; then the cwd it chose stands
        os.chdir(root)
    out = sys.stdout
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "not JSON"}}
        else:
            try:
                reply = handle(msg)
            except Exception as e:   # a failing tool must not take the agent's server down with it
                reply = {"jsonrpc": "2.0", "id": msg.get("id"), "error": {"code": -32603, "message": f"{type(e).__name__}: {e}"}}
        if reply is not None:
            out.write(json.dumps(reply) + "\n")
            out.flush()
    return 0
