import os
from pathlib import Path


def devmem_home() -> Path:
    """App state dir (workspace.json, hub/). Override with DEVMEM_HOME (used by tests)."""
    return Path(os.environ.get("DEVMEM_HOME") or Path.home() / ".devmem")


def hub_dir() -> Path:
    return devmem_home() / "hub"
