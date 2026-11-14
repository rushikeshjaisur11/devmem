import os
from pathlib import Path


def devmem_home() -> Path:
    """App state dir (workspace.json, hub/). Override with DEVMEM_HOME (used by tests)."""
    return Path(os.environ.get("DEVMEM_HOME") or Path.home() / ".devmem")


def hub_dir() -> Path:
    return devmem_home() / "hub"


def diagnose(area: str, message: str, repo: str = "") -> None:
    """Record a failure that was survived (a refresh that could not reach the network, a step that was skipped), so that it
    shows up on the Overview instead of vanishing. Kept short: the newest 200 entries."""
    import json
    import time
    p = devmem_home() / "diagnostics.jsonl"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        rows = p.read_text(encoding="utf-8").splitlines()[-199:] if p.exists() else []
        rows.append(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "area": area, "repo": repo, "message": str(message)[:400]}))
        p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    except OSError:
        pass


def diagnostics(limit: int = 30) -> list[dict]:
    import json
    p = devmem_home() / "diagnostics.jsonl"
    try:
        rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    except (OSError, ValueError):
        return []
    return rows[-limit:][::-1]


class repo_lock:
    """One writer per repo at a time (the watcher, the UI and the CLI can all index the same repo). A lock file in `.devmem/`,
    created exclusively; a lock older than `stale` seconds is taken to be left behind by a killed process and is replaced."""
    def __init__(self, root, name: str = "index", wait: float = 120.0, stale: float = 1800.0):
        from pathlib import Path
        self.path, self.wait, self.stale = Path(root) / ".devmem" / f".{name}.lock", wait, stale

    def __enter__(self):
        import os
        import time
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + self.wait
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > self.stale:
                        self.path.unlink()
                        continue
                except OSError:
                    continue
                if time.time() > deadline:
                    raise TimeoutError(f"another devmem process has been working on this repo for over {int(self.wait)} s ({self.path}); try again, or delete that file if nothing is running")
                time.sleep(0.2)

    def __exit__(self, *exc):
        try:
            self.path.unlink()
        except OSError:
            pass
        return False
