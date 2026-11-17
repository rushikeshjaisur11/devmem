"""Plain-language descriptions of findings: what the problem is and how it is usually fixed.

A linter reports `F841` or "B608" and one terse line. People who did not write the rule (and agents asked to fix it) need
to know what is wrong and why it matters. `finding(source, code, category)` returns {"what": ..., "fix": ...}: the exact
rule when it is in the catalog, else its rule family, else the tool, else the category. Nothing here calls a network or a
model; SonarQube rules, which devmem cannot list offline, get the description of their category."""
from __future__ import annotations


def _d(what: str, fix: str) -> dict:
    return {"what": what, "fix": fix}


# exact rule codes, per tool
RULES: dict[str, dict[str, dict]] = {
    "ruff": {
        "F401": _d("A module or name is imported but never used. It slows start-up, hides what the file really depends on, and can keep a dependency alive for nothing.",
                   "Delete the import. If the file re-exports it on purpose, list it in `__all__` or write `import x as x`."),
        "F403": _d("`from module import *` pulls in every name, so a reader (and the linter) cannot tell where a name comes from, and a new name in that module can silently replace one here.",
                   "Import the names you use, one by one."),
        "F405": _d("A name may come from a `import *`, so nothing can check that it exists.", "Import that name explicitly."),
        "F541": _d("An f-string has no `{}` placeholders, so the `f` does nothing; usually a value was meant to be inserted and was forgotten.",
                   "Add the missing placeholder, or drop the `f`."),
        "F601": _d("A dictionary literal repeats a key; the earlier value is thrown away without any error.", "Remove or rename the duplicate key."),
        "F632": _d("`is` compares identity, not value. With a string or number literal it works by accident on some values and fails on others.",
                   "Use `==` (or `!=`)."),
        "F811": _d("A function, class or import is defined a second time under the same name, so the first definition can never be used.",
                   "Rename one of them or delete the one that is dead."),
        "F821": _d("A name is used that is defined nowhere. This line raises NameError when it runs.",
                   "Fix the typo, add the missing import, or define the name."),
        "F822": _d("`__all__` lists a name the module does not define, so `from module import *` fails.", "Remove the name from `__all__` or define it."),
        "F823": _d("A local variable is read before it is assigned; this raises UnboundLocalError.", "Assign it first, or declare `global` / `nonlocal` if the outer variable was meant."),
        "F841": _d("A value is assigned to a local variable that is never read. Either the result was meant to be used and is not (a bug), or the line is leftover work.",
                   "Use the value, or delete the assignment (keep the call if it has a side effect)."),
        "E401": _d("Several modules are imported on one line, which makes diffs and unused-import fixes harder.", "One import per line."),
        "E402": _d("An import sits below other code instead of at the top of the file, so what the module needs is not visible in one place and import order can change behaviour.",
                   "Move it to the top, unless code before it must run first (then say so in a comment)."),
        "E501": _d("The line is longer than the project's limit, so it wraps or scrolls in reviews and side-by-side diffs.",
                   "Break the expression over several lines, or let the formatter do it."),
        "E701": _d("A statement follows a colon on the same line (`if x: return`), which is easy to misread and impossible to set a breakpoint on.", "Put the body on its own line."),
        "E702": _d("Two statements share a line, separated by a semicolon.", "One statement per line."),
        "E711": _d("`== None` asks the object to compare itself, which a class can override. `None` is a single object, so identity is the right test.", "Use `is None` / `is not None`."),
        "E712": _d("Comparing with `== True` / `== False` is redundant and wrong for values that are truthy but not `True`.", "Use `if x:` / `if not x:` (or `is True` when only the boolean counts)."),
        "E713": _d("`not x in y` reads as `(not x) in y` at a glance.", "Write `x not in y`."),
        "E714": _d("`not x is y` reads as `(not x) is y` at a glance.", "Write `x is not y`."),
        "E721": _d("Types are compared with `==`, which ignores subclasses.", "Use `isinstance(x, T)`."),
        "E722": _d("A bare `except:` also catches Ctrl-C and SystemExit, and hides bugs such as a misspelled name as if they were expected failures.",
                   "Catch the exception you expect (`except ValueError:`), or `except Exception:` at the very least."),
        "E731": _d("A lambda is assigned to a name. It shows up as `<lambda>` in tracebacks and cannot have a docstring.", "Write a `def`."),
        "E741": _d("A variable is named `l`, `O` or `I`, which look like `1` and `0` in many fonts.", "Give it a longer name."),
        "W291": _d("Spaces at the end of a line: invisible noise that shows up in every diff.", "Strip them (the formatter does)."),
        "W292": _d("The file does not end with a newline, which some tools report as a change on the last line.", "Add one."),
        "W293": _d("A blank line contains spaces or tabs.", "Strip them (the formatter does)."),
        "W605": _d("A string contains a backslash sequence Python does not know (often in a regular expression). It works today with a warning and becomes an error in a later Python.",
                   "Make it a raw string: `r\"...\"`."),
        "B006": _d("A list, dict or set is used as a default argument. It is created once and shared by every call, so one call's changes leak into the next.",
                   "Default to `None` and create the object inside the function."),
        "B007": _d("The loop variable is never used in the loop body; often the wrong variable is used inside.", "Use it, or name it `_`."),
        "B008": _d("A function call is used as a default argument. It runs once, when the function is defined, not on each call (a timestamp or a new object would be frozen).",
                   "Default to `None` and call it inside the function."),
        "B009": _d("`getattr(x, \"name\")` with a fixed name is a slower, less checkable way to write `x.name`.", "Write `x.name`."),
        "B010": _d("`setattr(x, \"name\", v)` with a fixed name is a less checkable way to write `x.name = v`.", "Write `x.name = v`."),
        "B011": _d("`assert False` disappears when Python runs with `-O`, so the code after it would run.", "Use `raise AssertionError(...)`."),
        "B018": _d("An expression is computed and thrown away. Usually a call or an assignment was meant.", "Use the result, or delete the line."),
        "B023": _d("A function defined inside a loop uses the loop variable. It sees the variable's last value when it finally runs, not the value of its own iteration.",
                   "Bind the value as a default argument (`lambda x=x: ...`) or build the function in a helper."),
        "B904": _d("An exception is raised inside `except` without `from`. The traceback then reads as if handling the first error failed, and the real cause is harder to find.",
                   "`raise NewError(...) from err`, or `from None` to hide the original on purpose."),
        "B905": _d("`zip()` stops at the shortest input without telling you, so a length mismatch silently drops data.", "Pass `strict=True` when the inputs must have the same length."),
        "C901": _d("The function has too many branches to follow or to test completely.", "Split it: pull each branch or loop body into a named helper."),
    },
    "vulture": {
        "unused-function": _d("Nothing in the code calls this function. It is still read, searched and maintained, and agents copy patterns from it. (Framework hooks, routes and plugins are called by name and can be false alarms.)",
                              "Delete it, or keep it and add it to a vulture whitelist if a framework calls it."),
        "unused-method": _d("Nothing in the code calls this method. (Methods a framework or a base class calls by name can be false alarms.)", "Delete it, or whitelist it if it is called from outside."),
        "unused-class": _d("Nothing in the code uses this class.", "Delete it, or whitelist it if it is loaded by name."),
        "unused-variable": _d("This variable is assigned and never read.", "Delete it, or use it."),
        "unused-import": _d("This import is never used.", "Delete it."),
        "unused-attribute": _d("This attribute is set but never read anywhere.", "Delete the assignment, or whitelist it if something outside reads it."),
        "unused-property": _d("Nothing reads this property.", "Delete it, or whitelist it if templates or serializers read it."),
        "unreachable-code": _d("This code comes after a `return`, `raise`, `break` or `continue`, so it can never run.", "Delete it, or move it above the statement that ends the block."),
    },
    "bandit": {
        "B101": _d("`assert` is removed when Python runs with `-O`, so a check written as an assert is not a check in production.", "Use an `if` that raises, outside of tests."),
        "B102": _d("`exec` runs a string as code. If any part of that string comes from outside, the caller can run anything.", "Replace it with a lookup table or a real parser."),
        "B103": _d("A file is made readable or writable by everyone on the machine.", "Use the narrowest mode that works, e.g. `0o600`."),
        "B104": _d("The service listens on every network interface, not only this machine.", "Bind to `127.0.0.1` unless it must be reachable, and then put it behind authentication."),
        "B105": _d("A password or secret appears to be written in the source, where everyone with the repo (and its history) can read it.", "Read it from the environment or a secret store, and rotate the exposed value."),
        "B106": _d("A password or secret appears to be passed as a literal in the source.", "Read it from the environment or a secret store, and rotate the exposed value."),
        "B107": _d("A function has a password as its default argument.", "Remove the default and require the caller to pass it from configuration."),
        "B108": _d("A fixed path under /tmp is used. Another user on the machine can create that file first and read or replace the data.", "Use `tempfile.mkstemp()` / `TemporaryDirectory()`."),
        "B110": _d("An exception is caught and ignored with `pass`, so a failure here leaves no trace.", "Handle it, or at least log it."),
        "B112": _d("An exception is caught and skipped with `continue`, so failures in the loop leave no trace.", "Handle it, or at least log it."),
        "B113": _d("An HTTP request has no timeout. If the server never answers, this call hangs forever and takes the worker with it.", "Pass `timeout=`."),
        "B201": _d("Flask runs with `debug=True`, which lets anyone who reaches the page run code on the server.", "Turn debug off outside local development."),
        "B301": _d("`pickle` runs code while loading. Loading data someone else can influence means running their code.", "Use JSON or another data-only format for anything not produced by this program."),
        "B303": _d("MD5 or SHA-1 is used; both are broken for security purposes.", "Use SHA-256 or better. For a checksum that is not about security, pass `usedforsecurity=False`."),
        "B324": _d("A weak hash (MD5 or SHA-1) is used; both are broken for security purposes.", "Use SHA-256 or better. For a checksum that is not about security, pass `usedforsecurity=False`."),
        "B307": _d("`eval` runs a string as code. If any part of that string comes from outside, the caller can run anything.", "Use `ast.literal_eval` for data, or a real parser."),
        "B310": _d("A URL is opened without checking its scheme, so `file://` could read local files.", "Check that the URL starts with `https://` (or `http://`)."),
        "B311": _d("`random` is predictable. It is fine for sampling and simulations, not for tokens, passwords or anything an attacker should not guess.", "Use the `secrets` module for security values."),
        "B314": _d("The standard XML parser can be made to read local files or exhaust memory by a crafted document.", "Parse untrusted XML with `defusedxml`."),
        "B321": _d("FTP sends the password and the data unencrypted.", "Use SFTP or HTTPS."),
        "B323": _d("TLS certificate checking is switched off, so anyone on the network path can impersonate the server.", "Use the default verified context."),
        "B403": _d("`pickle` is imported. Loading pickled data from outside runs code.", "Fine for data this program wrote itself; otherwise use a data-only format."),
        "B404": _d("`subprocess` is imported. Commands built from outside input can be hijacked.", "Pass arguments as a list and never `shell=True` with outside input."),
        "B501": _d("`verify=False` switches off TLS certificate checking, so anyone on the network path can read and change the traffic.", "Remove it; for a private CA pass its certificate with `verify=\"/path/ca.pem\"`."),
        "B506": _d("`yaml.load` can build arbitrary Python objects, so a crafted file runs code.", "Use `yaml.safe_load`."),
        "B602": _d("A command runs through the shell (`shell=True`). Any outside input in the command string can add its own commands.", "Pass the command as a list without `shell=True`."),
        "B603": _d("A subprocess is started. Bandit asks you to check that no outside input decides which program runs.", "Keep the program path fixed and pass input only as arguments."),
        "B604": _d("A function is called with `shell=True`. Any outside input in the command string can add its own commands.", "Pass the command as a list without the shell."),
        "B605": _d("A command is started through the shell (`os.system` / `os.popen`). Outside input in the string can add its own commands.", "Use `subprocess.run([...])` with a list."),
        "B607": _d("A program is started by name only, so whichever one comes first on PATH runs.", "Use its full path, or accept this for well-known tools."),
        "B608": _d("An SQL statement is built by joining strings. If any piece comes from outside, it can change the query (SQL injection).", "Pass values as query parameters (`execute(sql, params)`); never format them into the text."),
        "B701": _d("Jinja2 runs with autoescaping off, so values are inserted into HTML as written (cross-site scripting).", "Create the environment with `autoescape=True` / `select_autoescape()`."),
    },
    "deptry": {
        "DEP001": _d("The code imports a package that the project does not declare. It only works where something else happens to have installed it.", "Add it to the project's dependencies."),
        "DEP002": _d("The project declares a dependency that no code imports. It is installed, audited and upgraded for nothing.", "Remove it from the dependencies (or keep it if it is a plugin loaded by name)."),
        "DEP003": _d("The code imports a package that is only present because another dependency needs it. When that dependency drops it, this import breaks.", "Declare it as a direct dependency."),
        "DEP004": _d("Code that ships imports a package declared only for development, so it is missing in a normal install.", "Move it to the regular dependencies."),
    },
    "knip": {
        "unused-file": _d("No other file imports this one, so it is not part of the program.", "Delete it, or add it as an entry point in the knip config if it is run directly."),
        "unused-dependency": _d("`package.json` lists a dependency that no code imports.", "Remove it."),
        "unused-dev-dependency": _d("`package.json` lists a dev dependency that nothing uses.", "Remove it."),
        "unlisted-dependency": _d("The code imports a package that `package.json` does not list. It only works where something else installed it.", "Add it to `package.json`."),
        "unresolved-import": _d("An import points at a file or package that cannot be found.", "Fix the path or install the package."),
        "unused-export": _d("This is exported but nothing imports it, so the module's public surface is wider than what is used.", "Remove the `export` (or the code, if nothing in the file uses it either)."),
        "unused-type": _d("This type is exported but nothing imports it.", "Remove the `export`, or the type."),
    },
    "eslint": {
        "no-unused-vars": _d("A variable, import or parameter is declared and never used.", "Delete it, or prefix a deliberately unused parameter with `_`."),
        "@typescript-eslint/no-unused-vars": _d("A variable, import or parameter is declared and never used.", "Delete it, or prefix a deliberately unused parameter with `_`."),
        "no-undef": _d("A name is used that is declared nowhere; this throws ReferenceError when it runs.", "Fix the typo, import it, or declare the global."),
        "eqeqeq": _d("`==` converts types before comparing (`0 == \"\"` is true), which hides bugs.", "Use `===` / `!==`."),
        "no-console": _d("A `console` call is left in the code; it ends up in users' browsers or production logs.", "Remove it or use the project's logger."),
        "prefer-const": _d("A variable declared with `let` is never reassigned.", "Declare it with `const`, so a later reassignment is caught."),
        "no-var": _d("`var` is function-scoped and hoisted, which causes surprises in loops and blocks.", "Use `let` or `const`."),
        "no-empty": _d("An empty block, often a `catch` that swallows the error without a trace.", "Handle the case, or say why nothing is needed in a comment."),
        "no-explicit-any": _d("`any` switches type checking off for this value and everything it flows into.", "Give it a real type, or `unknown` and narrow it."),
        "@typescript-eslint/no-explicit-any": _d("`any` switches type checking off for this value and everything it flows into.", "Give it a real type, or `unknown` and narrow it."),
        "react-hooks/exhaustive-deps": _d("A hook uses a value that is missing from its dependency list, so it keeps working with a stale copy after that value changes.", "Add the dependency, or restructure so the hook does not need it."),
        "react-hooks/rules-of-hooks": _d("A hook is called inside a condition, a loop or a nested function. React matches hooks by call order, so this corrupts state.", "Call hooks at the top level of the component."),
        "no-prototype-builtins": _d("`obj.hasOwnProperty()` fails on objects without a prototype and can be overridden by the data.", "Use `Object.hasOwn(obj, key)`."),
        "no-case-declarations": _d("A `let` / `const` inside a `case` is visible to the other cases but only initialised in this one.", "Wrap the case body in braces."),
        "no-useless-escape": _d("A backslash escapes a character that needs no escaping; often the pattern is not what was intended.", "Remove the backslash."),
        "no-async-promise-executor": _d("An `async` function is passed to `new Promise`; an error thrown inside it is lost instead of rejecting the promise.", "Drop `new Promise` and use the async function directly."),
        "no-fallthrough": _d("A `case` runs into the next one because it has no `break`.", "Add `break` / `return`, or a `// falls through` comment if intended."),
        "parse-error": _d("The file could not be parsed, so nothing else in it was checked.", "Fix the syntax error first."),
    },
}

# (tool, code prefix) for rule families, longest prefix first when looked up
FAMILIES: dict[str, list[tuple[str, dict]]] = {
    "ruff": [
        ("E9", _d("The file has a syntax error or cannot be read, so it does not run at all.", "Fix the syntax error.")),
        ("E1", _d("Indentation is inconsistent (mixed tabs and spaces, or the wrong depth).", "Let the formatter re-indent the file.")),
        ("E2", _d("Whitespace around operators, commas or brackets differs from the standard Python style.", "Run the formatter.")),
        ("E3", _d("The number of blank lines between definitions differs from the standard Python style.", "Run the formatter.")),
        ("E5", _d("A line is too long or badly continued.", "Break it up, or run the formatter.")),
        ("E7", _d("A statement is written in a way that is easy to misread.", "Rewrite it in the form the rule suggests.")),
        ("PLE", _d("Pylint classes this as an error: code that is very likely wrong when it runs.", "Read the message and fix the code, not the warning.")),
        ("PL", _d("A pylint check: code that works but is harder to read or easy to get wrong.", "Apply the suggested rewrite.")),
        ("UP", _d("Older syntax that the project's Python version has a simpler form for.", "Apply the rewrite; `ruff check --fix` does most of these.")),
        ("SIM", _d("The code can be written more simply with the same meaning.", "Apply the suggested simplification.")),
        ("RUF", _d("A ruff-specific check for code that is likely not what was meant.", "Read the message and adjust the code.")),
        ("C4", _d("A comprehension or collection call is written the long way round.", "Use the direct form the message names.")),
        ("C9", _d("The function has too many branches to follow or to test completely.", "Split it into smaller functions.")),
        ("B", _d("A pattern that is legal Python but usually a bug (flake8-bugbear).", "Read the message: these are worth fixing one by one.")),
        ("S", _d("A security check: this code can expose data or run something it should not, depending on where its input comes from.", "Check where the input comes from and use the safe alternative the message names.")),
        ("F", _d("Pyflakes found something that is wrong or dead regardless of style: an unused or undefined name, a duplicate definition.", "Fix the code; these are rarely false alarms.")),
        ("W", _d("A style warning: whitespace or a construct that is being phased out.", "Run the formatter, or apply the rewrite.")),
        ("I", _d("Imports are not sorted or grouped the way the project expects.", "Run `ruff check --fix --select I`.")),
        ("N", _d("A name does not follow Python naming conventions (snake_case functions, CapWords classes).", "Rename it, unless it must match an outside API.")),
        ("D", _d("A docstring is missing or not in the expected format.", "Add or reformat the docstring.")),
        ("T", _d("A `print` or debugger call is left in the code.", "Remove it or use logging.")),
        ("E", _d("The code differs from the standard Python style (PEP 8).", "Run the formatter.")),
    ],
    "radon": [
        ("CCF", _d("This function has more than 40 independent paths through it. Nobody can keep that in their head, tests cannot cover it, and every change risks a path that was not thought about.", "Split it: one helper per branch or loop body, and tables instead of long if/elif chains.")),
        ("CCE", _d("This function has more than 30 independent paths through it, far more than tests usually cover.", "Split it: one helper per branch or loop body, and tables instead of long if/elif chains.")),
        ("CCD", _d("This function has more than 20 independent paths through it. Changes here are likely to break a case nobody tested.", "Pull branches and loop bodies into named helpers; use early returns.")),
        ("CC", _d("This function has more than 10 independent paths through it (cyclomatic complexity), which is where code starts to be hard to follow and to test.", "Pull branches and loop bodies into named helpers; use early returns.")),
    ],
    "bandit": [
        ("B3", _d("A call that is unsafe with input from outside (deserialising, weak cryptography, XML, eval).", "Use the safe alternative named in the message.")),
        ("B4", _d("A module is imported that is risky to use with outside input.", "Check each use of it; the import alone is not the problem.")),
        ("B5", _d("A cryptography or TLS setting is weaker than it should be.", "Use the library defaults or current algorithms.")),
        ("B6", _d("A command or query is built in a way that outside input can take over (injection).", "Pass values as arguments or parameters; never format them into the command text.")),
        ("B7", _d("A template is rendered without escaping, so values can inject HTML or script.", "Turn autoescaping on.")),
        ("B", _d("A security check: this code can expose data or run something it should not, depending on where its input comes from.", "Check where the input comes from and use the safe alternative the message names.")),
    ],
    "sqlfluff": [
        ("PRS", _d("The SQL could not be parsed in the configured dialect, so nothing else in the statement was checked.", "Fix the syntax, or set the right dialect in `.sqlfluff`.")),
        ("TMP", _d("The template (Jinja / dbt) around the SQL could not be rendered.", "Fix the template or give sqlfluff the missing variables.")),
        ("LT", _d("Layout: indentation, spacing or line length differs from the project's SQL style.", "Run `sqlfluff fix`.")),
        ("CP", _d("Keywords, functions or identifiers are not consistently upper or lower case.", "Run `sqlfluff fix`.")),
        ("AL", _d("A table or column alias is missing, implicit or unused, which makes the query harder to read and joins easier to get wrong.", "Add explicit `AS` aliases and remove unused ones.")),
        ("AM", _d("The query is ambiguous: its result depends on something not written down (column order, an implicit join type, `SELECT *` in a union).", "Spell it out: name the columns, the join type or the ordering.")),
        ("RF", _d("A column or table reference is unqualified or inconsistent, so it can bind to the wrong table once a column with that name is added elsewhere.", "Qualify references with the table alias.")),
        ("ST", _d("The query has a structure that can be written more simply (a nested CASE, an unused CTE, a subquery that should be a CTE).", "Apply the suggested restructuring.")),
        ("CV", _d("The query uses a form the project's SQL conventions avoid (for example `!=` versus `<>`, or `COALESCE` versus `IFNULL`).", "Run `sqlfluff fix`.")),
        ("JJ", _d("Jinja tags are not formatted consistently.", "Run `sqlfluff fix`.")),
        ("L0", _d("The SQL differs from the project's style rules.", "Run `sqlfluff fix`.")),
    ],
}

TOOLS = {
    "ruff": _d("A Python lint rule.", "Read the message; `ruff rule <code>` explains the rule in full."),
    "vulture": _d("Code that nothing appears to use. Dead code is still read, searched and maintained. (Code called by name from a framework can be a false alarm.)", "Delete it, or whitelist it if something outside calls it."),
    "radon": _d("The function has many independent paths through it (cyclomatic complexity), so it is hard to follow and to test.", "Split it into smaller functions."),
    "bandit": _d("A security check: this code can expose data or run something it should not, depending on where its input comes from.", "Check where the input comes from and use the safe alternative the message names."),
    "deptry": _d("The imports in the code and the dependencies the project declares do not match.", "Declare what is imported and remove what is not."),
    "sqlfluff": _d("The SQL differs from the project's style rules or could not be parsed.", "Run `sqlfluff fix`, or fix the syntax."),
    "eslint": _d("A JavaScript / TypeScript lint rule.", "Read the message; the rule's page on eslint.org shows correct and incorrect examples."),
    "knip": _d("A file, export or dependency that nothing uses.", "Remove it, or declare it as an entry point if it is used from outside."),
}

CATEGORIES = {
    "bug": _d("Code that is likely to do the wrong thing or fail when it runs.", "Fix the behaviour and add a test that would have caught it."),
    "security": _d("Code that can expose data or be misused, depending on where its input comes from.", "Check where the input comes from and apply the safe alternative."),
    "maintainability": _d("Code that works but is harder to read or change than it needs to be (a \"code smell\").", "Simplify it when you next touch this code."),
    "complexity": _d("Code with too many paths through it to follow or to test completely.", "Split it into smaller functions."),
    "dead-code": _d("Code that nothing uses.", "Delete it."),
    "style": _d("The code differs from the project's style rules.", "Run the formatter or apply the suggested rewrite."),
    "dependency": _d("The declared dependencies and what the code imports do not match.", "Declare what is imported and remove what is not."),
}


def finding(source: str, code: str, category: str = "") -> dict | None:
    """{"what", "fix"} for one finding: its exact rule, else its rule family, else its tool, else its category."""
    code = str(code or "")
    hit = RULES.get(source, {}).get(code)
    if hit:
        return hit
    for prefix, d in sorted(FAMILIES.get(source, []), key=lambda x: -len(x[0])):
        if code.startswith(prefix):
            return d
    if source == "eslint" and "/" in code:           # plugin rule: try the bare rule name
        bare = RULES["eslint"].get(code.split("/", 1)[1])
        if bare:
            return bare
    return TOOLS.get(source) or CATEGORIES.get(category)


# ---------- Modernize: why the hand-written version is worth replacing ----------
MODERNIZE = {
    "py-retry-loop": "Hand-written retry loops usually get the details wrong: they retry errors that can never succeed, wait a fixed time so every client retries at once, or loop forever. A retry library gives backoff, jitter, a retry limit and a choice of which errors to retry, in one decorator.",
    "py-dict-cache": "A dict used as a cache grows without limit, is not safe when two threads fill it at once, and repeats the same check-then-store code in every function. The built-in decorator handles size, eviction and locking.",
    "py-chunk": "Slicing with `range(0, len(x), n)` only works on sequences that have a length, and the index arithmetic is easy to get off by one. The built-in works on any iterable, including generators.",
    "py-pairwise": "`zip(x, x[1:])` copies the whole sequence and fails on iterators. The built-in pairs neighbours lazily on any iterable.",
    "py-flatten": "Nested loops that append to a list are longer and slower than the built-in, which also flattens lazily.",
    "py-removeprefix": "Slicing by `len(prefix)` after a `startswith` check is easy to get wrong (and `lstrip` / `rstrip` remove characters, not a prefix). The string method says what is meant and cannot be off by one.",
    "py-counter": "Counting with `d[k] = d.get(k, 0) + 1` repeats boilerplate; `Counter` does it and adds `most_common()`, addition and subtraction of counts.",
    "py-utcnow": "`datetime.utcnow()` returns a time with no timezone attached, so it is treated as local time by `.timestamp()` and comparisons. It is deprecated since Python 3.12.",
    "py-random-token": "`random` is predictable: anyone who sees a few outputs can compute the next ones. Tokens, passwords and reset links must come from `secrets`.",
    "py-tempdir": "A temporary directory created and removed by hand is left behind whenever an exception happens in between. The context manager removes it on every exit path.",
    "py-argv": "Reading `sys.argv` by index gives no `--help`, no error for a missing or misspelled option, and breaks when an option is added. `argparse` does all of that from a declaration.",
    "py-walk-glob": "`os.walk` with string joins and suffix checks is several lines for what `Path.rglob(\"*.ext\")` does in one, with path objects instead of strings.",
    "py-email-regex": "A regular expression for email addresses rejects valid addresses and accepts invalid ones; the real rules (internationalised domains, quoting, length limits) do not fit in a pattern. A validator library also normalises the address.",
    "py-slugify": "Hand-written slug code usually drops or mangles non-ASCII letters and leaves double or trailing dashes. A slug library transliterates and handles those edge cases.",
    "py-env-config": "Reading settings one `os.environ.get` at a time scatters names and defaults over the code, converts types by hand, and fails late (when the value is first used) instead of at start-up. A settings class declares them once, typed and validated.",
    "py-urllib-json": "`urllib` needs manual encoding, header handling, JSON decoding and status checks, and it is easy to forget the timeout. An HTTP client library does these in one call and reuses connections.",
    "py-manual-dataclass": "An `__init__` that only copies its arguments to attributes, plus hand-written `__repr__` / `__eq__`, has to be edited in three places for every new field. A dataclass generates them from the field list.",
    "js-json-clone": "`JSON.parse(JSON.stringify(x))` silently drops `undefined`, functions and symbols, turns dates into strings and `Map` / `Set` into `{}`, and throws on cycles. `structuredClone` copies all of those correctly and is faster.",
    "js-uuid": "IDs built from `Math.random()` can collide and are predictable. `crypto.randomUUID()` is built in and uses a cryptographic source.",
    "js-fetch-timeout": "An `AbortController` wired to `setTimeout` by hand needs clean-up code that is easy to forget, leaving timers running. `AbortSignal.timeout(ms)` is one expression.",
    "js-query-string": "Building or splitting query strings by hand misses escaping (`&`, `=`, spaces, non-ASCII), which corrupts values and can let input add its own parameters. `URLSearchParams` encodes and decodes correctly.",
    "js-sleep": "Wrapping `setTimeout` in a promise by hand is repeated in every project; Node has it built in as `timers/promises`, with support for cancelling.",
    "js-group-by": "Grouping with `reduce` and a mutable object is hard to read and easy to get wrong for the first item of a group. `Object.groupBy` / `Map.groupBy` state the intent in one call.",
    "js-flatten": "Flattening with `reduce` + `concat` or `[].concat(...xs)` copies arrays repeatedly and can overflow the call stack on large input. `Array.prototype.flat` / `flatMap` are built in.",
    "js-debounce": "A hand-written debounce usually loses `this` and the latest arguments, has no way to cancel or flush, and leaks its timer on unmount. The library version handles those.",
    "js-date-format": "Formatting dates by padding numbers by hand ignores locale and time zone and gets edge cases (midnight, month boundaries) wrong. `Intl.DateTimeFormat` is built in and locale-aware.",
    "docs-match": "This function has the same name as something a library documents. If the library's version does the same job, the copy here is code to maintain and test that you could delete. Names can match by coincidence: compare what both do before replacing.",
    "new-release": "A release newer than the one this repo uses added something whose description matches this function. Code written around the older version often does by hand what the newer one includes. This is a word match on the release notes: read the note before acting.",
    "ai-release": "A model read the library's release notes against this function and thinks a newer release covers it. Treat it as a lead: confirm against the release note.",
}


def modernize(rule: str) -> str:
    return MODERNIZE.get(rule, "")
