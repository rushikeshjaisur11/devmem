// Start the devmem MCP server for plugin hosts (Claude Code, Gemini CLI). Plugin configs cannot pick a command per OS, and
// `python3` does not exist on most Windows machines, so this finds a Python 3.10+ the same way everywhere:
// DEVMEM_PYTHON, then `py -3`, `python`, `python3` on Windows, or `python3`, `python` elsewhere. It then runs mcp_server.py
// with stdio passed straight through.
import { spawn, spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const script = path.join(path.dirname(fileURLToPath(import.meta.url)), "mcp_server.py");
const tried = [
  ...(process.env.DEVMEM_PYTHON ? [[process.env.DEVMEM_PYTHON]] : []),
  ...(process.platform === "win32" ? [["py", "-3"], ["python"], ["python3"]] : [["python3"], ["python"]]),
];
const works = (c) =>
  spawnSync(c[0], [...c.slice(1), "-c", "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"],
    { stdio: "ignore", windowsHide: true, timeout: 15000 }).status === 0;
const py = tried.find(works);
if (!py) {
  process.stderr.write(`devmem: no Python 3.10+ found (tried ${tried.map((c) => c.join(" ")).join(", ")}). ` +
    "Install Python, or set DEVMEM_PYTHON to the full path of python.\n");
  process.exit(1);
}
const child = spawn(py[0], [...py.slice(1), script], { stdio: "inherit", windowsHide: true });
for (const sig of ["SIGINT", "SIGTERM"]) process.on(sig, () => child.kill(sig));
child.on("exit", (code) => process.exit(code ?? 1));
