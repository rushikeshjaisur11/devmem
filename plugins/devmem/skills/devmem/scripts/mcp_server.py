"""Start the devmem MCP server for a plugin host (started by mcp_launch.mjs, or run directly with any Python 3.10+).
Uses the devmem wheel bundled in ../vendor, imported straight from the zip, so the server matches this plugin's version and
needs no install or network; PyYAML comes from the environment if present, else from the bundled pure-Python copy."""
import glob
import os
import sys

vendor = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "vendor")
sys.path.insert(0, sorted(glob.glob(os.path.join(vendor, "devmem-*.whl")))[-1])
try:
    import yaml  # noqa: F401
except ImportError:
    sys.path.append(sorted(glob.glob(os.path.join(vendor, "pyyaml-*.zip")))[-1])
from devmem.cli import main  # noqa: E402

sys.exit(main(["mcp"]))
