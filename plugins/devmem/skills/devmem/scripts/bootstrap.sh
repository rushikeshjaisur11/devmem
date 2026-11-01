#!/usr/bin/env bash
# Install the devmem agent CLI. Order: already installed -> bundled wheel -> git.
# (Never `pip install devmem` from PyPI by bare name: that name is not ours.)
# Works on Linux, macOS and Windows (Git Bash): finds a Python 3.10+ under any of its usual names, falls back to uv / pipx
# when pip refuses (externally managed environments), and installs without dependencies when there is no network.
set -u
if command -v devmem >/dev/null 2>&1; then devmem --version; exit 0; fi
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY=""
for c in "${DEVMEM_PYTHON:-}" python3 python "py -3"; do
  [ -n "$c" ] && $c -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1 && { PY="$c"; break; }
done
if [ -z "$PY" ]; then echo "devmem: no Python 3.10+ found (set DEVMEM_PYTHON). Continue without devmem." >&2; exit 1; fi
WHL="$(ls "$HERE"/vendor/devmem-*.whl 2>/dev/null | head -1)"
SRC="${WHL:-${DEVMEM_GIT_URL:+git+${DEVMEM_GIT_URL}#subdirectory=packages/devmem}}"
if [ -z "$SRC" ]; then echo "devmem: no bundled wheel and no DEVMEM_GIT_URL. Continue without devmem." >&2; exit 1; fi
$PY -m pip install --quiet "$SRC" 2>/dev/null \
  || $PY -m pip install --quiet --user "$SRC" 2>/dev/null \
  || { command -v uv >/dev/null && uv tool install --quiet "$SRC"; } \
  || { command -v pipx >/dev/null && pipx install "$SRC"; } \
  || $PY -m pip install --quiet --no-deps "$SRC" \
  || { echo "devmem install failed (pip, pip --user, uv, pipx). Continue without devmem." >&2; exit 1; }
if command -v devmem >/dev/null 2>&1; then echo "installed"; devmem --version; exit 0; fi
BIN="$($PY -c 'import sysconfig; print(sysconfig.get_path("scripts"))')"
echo "installed, but $BIN is not on PATH. Run: export PATH=\"$BIN:\$PATH\"   (or call: $PY -m devmem.cli <command>)"
exit 0
