#!/usr/bin/env bash
# Install the devmem agent CLI. Order: already installed -> bundled wheel -> git.
# (Never `pip install devmem` from PyPI by bare name: that name is not ours.)
set -u
if command -v devmem >/dev/null 2>&1; then devmem --version; exit 0; fi
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIP="python3 -m pip"
WHL="$(ls "$HERE"/vendor/devmem-*.whl 2>/dev/null | head -1)"
if [ -n "$WHL" ]; then
  $PIP install --quiet "$WHL" && command -v devmem >/dev/null && { echo "installed from bundled wheel"; devmem --version; exit 0; }
fi
if [ -n "${DEVMEM_GIT_URL:-}" ]; then
  $PIP install --quiet "git+${DEVMEM_GIT_URL}#subdirectory=packages/devmem" && command -v devmem >/dev/null && { echo "installed from git"; exit 0; }
fi
echo "devmem install failed (tried bundled wheel and git). Continue without devmem." >&2
exit 1
