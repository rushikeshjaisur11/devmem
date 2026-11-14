"""Prompts for coding agents, written so a small model can follow them without exploring.

A capable agent recovers from a vague instruction by reading around; a small one does not, and every file it opens to find
its bearings costs tokens. So each prompt here is self-contained and has the same fixed shape:

    Task        one sentence, the outcome
    Where       the exact file and lines, the code itself, who calls it, the command that tests it
    Steps       numbered, one action each, with the commands to run
    Rules       what not to do, short
    Done when   a checklist the agent can verify
    Report      what to say at the end

devmem never runs these: they are text for the person to paste into the agent that does the work."""
from __future__ import annotations

from pathlib import Path

from devmem import check as chk
from devmem import memory, notouch
from devmem.context import grouped
from devmem.index import Index

BODY_LINES = 60
RULES = ["Change only what this task needs. No renames, reformatting, new dependencies or unrelated fixes.",
         "Keep every function's name, parameters and return value unless a step says to change them.",
         "Do not open files that are not named here unless a step tells you to.",
         "If a step cannot be done as written, stop and say which step and why. Do not guess."]
REPORT = ["Files and functions you changed (one line each).", "The exact commands you ran and whether they passed.",
          "Anything you could not do, or did differently from the steps."]


def est(text: str) -> int:
    return max(1, len(text) // 4)


def _test_cmd(ix: Index, files: list[str]) -> str | None:
    try:
        return chk.tests_for(ix, files)[1]
    except Exception:
        return None


def symbol_block(ix: Index, s: dict, lines: int = BODY_LINES, mark: int | None = None) -> list[str]:
    """Everything about one symbol an agent needs in order to edit it without opening anything else."""
    out = [f"- `{s['file']}` lines {s['line']}-{s.get('end_line', s['line'])}: `{s.get('sig') or s['qualname']}`"]
    said = s.get("doc") or ix.desc.get(s["id"], "")
    if said:
        out.append(f"  What it does: {said[:200]}")
    imp = ix.impact(s["id"])
    callers = [i["id"] for i in imp["hop1"]]
    out.append("  Called by: " + (grouped(callers, 8) if callers else "nothing in this repo") + (". These must keep working." if callers else "."))
    for c in imp["capped"]:
        out.append(f"  Widely used ({c['callers']} callers): do not change its signature.")
    try:
        text, first, last, note = ix.read(s)       # as the file is now, not as it was when indexed
        body = text.splitlines()
    except LookupError as e:
        body, first, note = [], s["line"], ""
        out.append(f"  WARNING: {e}")
    if note:
        out[0] = f"- `{s['file']}` lines {first}-{last}: `{s.get('sig') or s['qualname']}`"
        out.append(f"  Note: {note}")
    if body:
        start = 0
        if mark and len(body) > lines:      # a long function: show the part around the line in question
            start = max(0, min(mark - first - lines // 2, len(body) - lines))
        part = body[start:start + lines]
        out.append("  ```")
        out += [f"  {first + start + n:>5}  {ln}" for n, ln in enumerate(part)]
        out.append("  ```")
        if len(body) > len(part):
            out.append(f"  (lines {first + start}-{first + start + len(part) - 1} of {len(body)}; read the rest of this function in the file if you need it)")
    return out


def _rules(ix: Index, files: list[str]) -> list[str]:
    extra = [f"Repo rule: {r['text'][:220]}" for r in memory.load_rules(ix.root) if memory.rule_applies(r, files)][:4]
    pats = notouch.patterns(ix.root)
    prot = [f for f in files if notouch.matches(f, pats)]
    if prot:
        extra.append("PROTECTED, ask before editing: " + ", ".join(prot))
    return RULES + extra


def render(task: str, where: list[str], steps: list[str], rules: list[str], done: list[str], report: list[str] | None = None) -> str:
    out = ["# Task", task, "", "## Where (everything you need is here)", *where, "", "## Steps (do them in this order)"]
    out += [f"{n}. {s}" for n, s in enumerate(steps, 1)]
    out += ["", "## Rules", *(f"- {r}" for r in rules), "", "## Done when", *(f"- [ ] {d}" for d in done),
            "", "## Report back", *(f"- {r}" for r in (report or REPORT))]
    return "\n".join(out) + "\n"


def _verify_steps(ix: Index, files: list[str]) -> tuple[list[str], list[str]]:
    cmd = _test_cmd(ix, files)
    steps = [f"Run the tests that cover this code: `{cmd}`. Fix what you broke; do not edit a test to make it pass." if cmd else
             "No test is mapped to this code. Run the repo's test command (see `.devmem/knowledge/commands.md`) and say in your report that no test covers the change.",
             "Run `devmem diffcheck`. If it blocks, make the change smaller until it passes."]
    done = [f"`{cmd}` passes." if cmd else "The repo's tests pass.", "`devmem diffcheck` passes.", "Only the files named above are changed."]
    return steps, done


def _symbol_at(ix: Index, path: str, line: int) -> dict | None:
    best = None
    for s in ix.by_file.get(path, []):
        if s["line"] <= line <= s.get("end_line", s["line"]) and (best is None or s["line"] >= best["line"]):
            best = s
    return best


def finding(ix: Index, f: dict) -> str:
    """Fix one code-health or SonarQube finding."""
    path, line = f.get("path", ""), int(f.get("line") or 1)
    s = ix.symbols.get(f.get("symbol_id") or "") or _symbol_at(ix, path, line)
    src = "SonarQube" if f.get("source") == "sonar" else f.get("source", "a code check")
    task = f"Fix one finding from {src} in `{path}` at line {line}: {f.get('msg', '').strip()} (rule `{f.get('code', '')}`, {f.get('severity', 'medium')} severity)."
    where = symbol_block(ix, s, mark=line) if s else [f"- `{path}` line {line}. Read 20 lines above and below it, nothing more."]
    if f.get("url"):
        where.append(f"- Rule details and examples: {f['url']}")
    v_steps, done = _verify_steps(ix, [path])
    steps = [f"Read the code above and find line {line}.", "Make the smallest change that removes the finding. Keep what the code does for its callers exactly the same.", *v_steps]
    return render(task, where, steps, _rules(ix, [path]), [f"The finding at line {line} no longer applies.", *done])


def file_findings(ix: Index, path: str, findings: list[dict]) -> str:
    """Fix every finding in one file, most severe first."""
    order = {"high": 0, "medium": 1, "low": 2}
    fs = sorted(findings, key=lambda f: (order.get(f.get("severity"), 3), f.get("line") or 0))[:25]
    task = f"Fix the {len(fs)} code health finding{'s' if len(fs) != 1 else ''} listed below in `{path}`."
    where = [f"- `{path}`. Findings, most severe first:"]
    where += [f"  {n}. line {f.get('line')}: {f.get('msg', '').strip()[:160]} (`{f.get('code', '')}`, {f.get('severity', '')}" +
              (f", in `{f['symbol']}`" if f.get("symbol") else "") + ")" for n, f in enumerate(fs, 1)]
    used = len({e["src"].split("#")[0] for s in ix.by_file.get(path, []) for e in ix.inc.get(s["id"], []) if e["type"] == "calls"} - {path})
    if used:
        where.append(f"- {used} other file{'s' if used != 1 else ''} call code in this file. Do not change any function's name, parameters or return value.")
    v_steps, done = _verify_steps(ix, [path])
    steps = ["Take the findings one at a time, in the order listed. For each: read the lines around it, make the smallest change that removes it.",
             "After every third finding, run the tests (next step) so a mistake is caught early.", *v_steps]
    return render(task, where, steps, _rules(ix, [path]), ["Each listed finding is fixed, or named in your report with the reason it was left.", *done])


def test_gap(ix: Index, sid: str) -> str:
    """Write tests for a widely used function no test reaches."""
    s = ix.symbols[sid]
    callers = {e["src"] for e in ix.inc.get(sid, []) if e["type"] == "calls"}
    task = f"Write tests for `{s['qualname']}` in `{s['file']}`. {len(callers)} place{'s' if len(callers) != 1 else ''} call it and no test covers it."
    where = symbol_block(ix, s)
    tests = sorted({x["file"] for x in ix.symbols.values() if x.get("is_test")})
    near = [t for t in tests if Path(t).stem.replace("test_", "").replace("_test", "") in s["file"]] or tests[:1]
    where.append(f"- Put the tests in `{near[0]}` and copy the style of the tests already there." if near else
                 "- This repo has no test file yet: create one where its test command looks for tests (see `.devmem/knowledge/commands.md`).")
    steps = ["Read the function above. List for yourself: the normal case, each branch (`if`, `except`, early `return`), and the edge inputs (empty, zero, None).",
             "Write one test per item on that list. Each test calls the function and asserts on what it returns or raises.",
             "Do not change the function. If a test shows a real bug, keep the test, mark it as expected to fail, and describe the bug in your report.",
             "Run the new tests, then `devmem diffcheck`."]
    return render(task, where, steps, _rules(ix, [s["file"]]), ["Every branch of the function is exercised by a test.", "The new tests pass (or are marked as expected failures with a reason).",
                                                                 "No production code is changed."])


def duplicate(ix: Index, c: dict) -> str:
    """Merge two copies of the same code."""
    a, b = f"`{c['a']}` lines {c['a_lines'][0]}-{c['a_lines'][1]}", f"`{c['b']}` lines {c['b_lines'][0]}-{c['b_lines'][1]}"
    task = f"The same code exists twice: {a} and {b}. Move it into one function and call that from both places."
    where = [f"- First copy: {a}", f"- Second copy: {b}"]
    for path, lines in ((c["a"], c["a_lines"]), (c["b"], c["b_lines"])):
        s = _symbol_at(ix, path, lines[0])
        if s:
            where += symbol_block(ix, s, 30, mark=lines[0])
    v_steps, done = _verify_steps(ix, sorted({c["a"], c["b"]}))
    steps = ["Read both copies. Note every difference between them; each difference becomes a parameter of the shared function.",
             "Create the shared function next to the first copy (or in the module both files already import from, if there is one).",
             "Replace both copies with a call to it. Behaviour must stay identical in both places.", *v_steps]
    return render(task, where, steps, _rules(ix, [c["a"], c["b"]]), ["Both places call the one shared function.", *done])


def modernize(ix: Index, f: dict) -> str:
    """Replace hand-written code with what the language or a library provides."""
    s = ix.symbols[f["id"]]
    task = f"`{s['qualname']}` in `{s['file']}` hand-writes {f['what']}. Replace that part with {f['use']}, keeping its behaviour for callers."
    where = symbol_block(ix, s, mark=f.get("line"))
    where.append(f"- Replacement: {f['use']} (from {f['source']}). Status: {f['needs']}.")
    if f.get("url"):
        where.append(f"- Current documentation: {f['url']} (you may not have web access: `devmem docs \"{f['use'].strip('`').split('`')[0]}\"` searches the copy in the repo)")
    if (f.get("ai") or {}).get("note") and f["ai"]["note"].lower() != "nothing":
        where.append(f"- Watch out: {f['ai']['note']}")
    v_steps, done = _verify_steps(ix, [s["file"]])
    first = ("Confirm the version first: check the repo's minimum Python / Node version. If it is older than the replacement needs, stop and report that."
             if f["kind"] == "stdlib" else
             "Confirm the library is installed at a version that has this feature (look in the dependency file). If it is not a dependency, stop and ask before adding it."
             if f["status"] != "new-dependency" else "This adds a new dependency. Stop and ask for approval before adding it; continue only if approved.")
    steps = [first, "Read the function above. Write down every behaviour of the hand-written part: what it returns, what it raises, its defaults, any logging or special cases.",
             "Replace only that part with the built-in. Keep the function's name, parameters and return value, so no caller changes.",
             "If the built-in cannot reproduce a behaviour from your list, keep the hand-written code for that case, or stop and report it. Do not silently drop it.", *v_steps]
    return render(task, where, steps, _rules(ix, [s["file"]]), ["The hand-written part is gone and the built-in is used.", "Every behaviour on your list still holds.", *done])


KINDS = {"modernize": lambda ix, d: modernize(ix, d), "finding": lambda ix, d: finding(ix, d), "file": lambda ix, d: file_findings(ix, d["path"], d.get("findings") or []),
         "test": lambda ix, d: test_gap(ix, d["id"]), "duplicate": lambda ix, d: duplicate(ix, d)}


def build(ix: Index, kind: str, data: dict) -> dict:
    text = KINDS[kind](ix, data)
    return {"prompt": text, "tokens": est(text)}
