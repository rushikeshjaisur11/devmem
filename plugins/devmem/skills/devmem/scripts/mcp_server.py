"""Start the devmem MCP server for a plugin host (Claude Code, Gemini CLI): the installed devmem if there is one, else the wheel
bundled in ../vendor, imported straight from the zip. Without PyYAML installed, lessons/rules and diffcheck need
`bash scripts/bootstrap.sh` first; find, show, impact, docs and tests-for work either way."""
import glob
import os
import sys

try:
    import devmem  # noqa: F401
except ImportError:
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, sorted(glob.glob(os.path.join(here, "..", "vendor", "devmem-*.whl")))[-1])
from devmem.cli import main

sys.exit(main(["mcp"]))
