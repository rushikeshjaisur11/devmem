"""Onboard a repo by uploading it (a .zip / .tar.gz, or a folder the browser packed into a zip) instead of naming a path
on the machine that runs devmem. This is what makes onboarding work when devmem runs on a server.

An upload is untrusted input: entries that would land outside the target, links and device files are skipped; `.git` is
dropped (devmem later runs git in the folder, and a crafted .git/config can execute commands); the unpacked size and the
number of files are capped, counting bytes actually written rather than trusting the archive's own headers."""
from __future__ import annotations

import os
import re
import shutil
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

from .paths import devmem_home

SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".tox", ".idea"}
MAX_FILES = 200_000
NAME_OK = re.compile(r"[A-Za-z0-9][\w.-]{0,79}")


class UploadError(ValueError):
    pass


def max_upload_bytes() -> int:
    return int(os.environ.get("DEVMEM_MAX_UPLOAD_MB", "300")) * 1024 * 1024


def max_unpacked_bytes() -> int:
    return int(os.environ.get("DEVMEM_MAX_UNPACKED_MB", "2000")) * 1024 * 1024


def uploads_dir() -> Path:
    return devmem_home() / "uploads"


def repo_name(name: str | None, filename: str | None) -> str:
    n = (name or "").strip() or re.sub(r"\.(zip|tar\.gz|tgz|tar\.bz2|tar\.xz|tar)$", "", PurePosixPath((filename or "").replace("\\", "/")).name, flags=re.I)
    n = re.sub(r"[^\w.-]+", "-", n).strip("-.")
    if not NAME_OK.fullmatch(n or ""):
        raise UploadError("give the repo a name (letters, digits, dot, dash, underscore)")
    return n


def _clean(name: str) -> PurePosixPath | None:
    """Archive member name -> safe relative path, or None when it must be skipped."""
    p = PurePosixPath(name.replace("\\", "/"))
    parts = [x for x in p.parts if x not in ("", ".")]
    if not parts or p.is_absolute() or ".." in parts or re.match(r"^[A-Za-z]:", parts[0]) or SKIP_DIRS & set(parts):
        return None
    return PurePosixPath(*parts)


def _members(archive: Path):
    """Yield (relative path, open-a-stream function) for every regular file in the archive."""
    if zipfile.is_zipfile(archive):
        zf = zipfile.ZipFile(archive)
        for info in zf.infolist():
            mode = info.external_attr >> 16
            if info.is_dir() or (mode and (mode & 0o170000) not in (0, 0o100000)):   # directories, symlinks, devices
                continue
            yield info.filename, (lambda i=info: zf.open(i))
    elif tarfile.is_tarfile(archive):
        tf = tarfile.open(archive)
        for m in tf:
            if m.isfile():
                yield m.name, (lambda m=m: tf.extractfile(m))
    else:
        raise UploadError("not a .zip or .tar archive: upload a zip, a tar.gz, or pick the folder")


def extract(archive: Path, dest: Path) -> dict:
    """Unpack into `dest` (created empty). A single top-level folder, as in a GitHub "Download ZIP", is stripped."""
    names = [(c, opener) for raw, opener in _members(archive) if (c := _clean(raw))]
    if not names:
        raise UploadError("the archive has no files devmem can read")
    if len(names) > MAX_FILES:
        raise UploadError(f"too many files ({len(names):,}; the limit is {MAX_FILES:,})")
    tops = {c.parts[0] for c, _ in names}
    strip = 1 if len(tops) == 1 and all(len(c.parts) > 1 for c, _ in names) else 0
    limit, total, files = max_unpacked_bytes(), 0, 0
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    for c, opener in names:
        target = (root / Path(*c.parts[strip:])).resolve()
        if root != target and root not in target.parents:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with opener() as src, open(target, "wb") as out:
            while chunk := src.read(1 << 20):
                total += len(chunk)
                if total > limit:
                    raise UploadError(f"unpacks to more than {limit // (1024 * 1024)} MB (DEVMEM_MAX_UNPACKED_MB raises the limit)")
                out.write(chunk)
        files += 1
    return {"files": files, "bytes": total}


def install(archive: Path, name: str) -> Path:
    """Unpack `archive` as uploads/<name>. An earlier upload of the same name is replaced, keeping its `.devmem/` (lessons,
    rules, descriptions) unless the new upload brings its own."""
    dest = uploads_dir() / name
    tmp = uploads_dir() / f".{name}.incoming"
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        extract(archive, tmp)
        if (dest / ".devmem").is_dir() and not (tmp / ".devmem").exists():
            shutil.move(str(dest / ".devmem"), str(tmp / ".devmem"))
        shutil.rmtree(dest, ignore_errors=True)
        tmp.rename(dest)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return dest


def knowledge_zip(root: Path) -> bytes:
    """What an agent needs from a published repo, as one zip to unpack at the repo root: `.devmem/` (without local caches),
    AGENTS.md and the Copilot instructions file."""
    import io
    skip = {"index.db", "index.db-journal", "usage.jsonl", "graphify-edges.json", "scip-edges.json"}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted((root / ".devmem").rglob("*")):
            if f.is_file() and f.name not in skip:
                z.write(f, f.relative_to(root).as_posix())
        for rel in ("AGENTS.md", ".github/copilot-instructions.md"):
            if (root / rel).is_file():
                z.write(root / rel, rel)
    return buf.getvalue()
