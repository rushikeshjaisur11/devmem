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


def _pid_alive(pid: int) -> bool:
    import os
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)     # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return bool(ok) and code.value == 259                          # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class repo_lock:
    """One writer per repo at a time (the watcher, the UI and the CLI can all index the same repo). A lock file in `.devmem/`,
    created exclusively, holding `<pid> <host>` of its owner.

    A lock left behind by a process that was killed is replaced at once when that process is gone (same machine), instead of
    making the next run wait. A lock whose owner is still running is never taken over, however long the work takes; one
    whose owner cannot be checked (another machine on a shared disk, an unreadable file) is replaced after `stale` seconds."""
    def __init__(self, root, name: str = "index", wait: float = 120.0, stale: float = 1800.0):
        from pathlib import Path
        self.path, self.wait, self.stale = Path(root) / ".devmem" / f".{name}.lock", wait, stale

    def _abandoned(self) -> bool:
        import socket
        import time
        age = time.time() - self.path.stat().st_mtime
        try:
            pid, _, host = self.path.read_text(encoding="utf-8").strip().partition(" ")
            pid = int(pid)
        except (OSError, ValueError):
            return age > 5.0        # empty or unreadable: its owner died between creating the file and writing to it
        if host in ("", socket.gethostname()):
            return not _pid_alive(pid)
        return age > self.stale

    def __enter__(self):
        import os
        import socket
        import time
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + self.wait
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"{os.getpid()} {socket.gethostname()}".encode())
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    if self._abandoned():
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
