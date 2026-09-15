#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if (( $# > 1 )); then
  echo "Usage: ./build-macos.sh [--skip-install]" >&2
  exit 2
fi

skip_install=0
if [[ "${1:-}" == "--skip-install" ]]; then
  skip_install=1
elif [[ -n "${1:-}" ]]; then
  echo "Usage: ./build-macos.sh [--skip-install]" >&2
  exit 2
fi

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "The macOS package must be built on macOS." >&2
  exit 1
fi
if [[ "$(uname -m)" != "arm64" ]]; then
  echo "The Apple Silicon package must be built with an arm64 Python on an M-series Mac." >&2
  exit 1
fi

python_command=".venv/bin/python"
if [[ ! -x "$python_command" ]]; then
  if (( skip_install )); then
    python_command="$(command -v python3)"
  else
    echo "[PaperVault] Creating the local Python environment..."
    "${PAPER_VAULT_PYTHON:-python3}" -m venv .venv
  fi
fi
"$python_command" - <<'PY'
import platform
import sys

if sys.version_info < (3, 10):
    raise SystemExit("PaperVault requires Python 3.10 or newer")
if platform.machine() != "arm64":
    raise SystemExit("The Apple Silicon package requires an arm64 Python, not a Rosetta/x86_64 interpreter")
PY

if (( ! skip_install )); then
  "$python_command" -m pip install --disable-pip-version-check --timeout 120 -r requirements-desktop.txt
fi
"$python_command" -c "import PyInstaller, webview, AppKit, Foundation, WebKit; from PyObjCTools import AppHelper"
"$python_command" tools/generate_desktop_icons.py

version="$("$python_command" tools/desktop_version.py)"
echo "[PaperVault] Building macOS version $version..."
"$python_command" -m PyInstaller \
  --noconfirm \
  --clean \
  --workpath build/pyinstaller-macos \
  --distpath dist \
  packaging/macos.spec

if [[ ! -d "dist/PaperVault.app" ]]; then
  echo "PaperVault macOS build did not produce dist/PaperVault.app." >&2
  exit 1
fi

# Validate the signed bundle without modifying any files after signing.
codesign --verify --deep --strict --verbose=2 dist/PaperVault.app
built_version="$(dist/PaperVault.app/Contents/MacOS/PaperVault --version)"
if [[ "$built_version" != "$version" ]]; then
  echo "PaperVault bundle version mismatch: expected $version, got $built_version" >&2
  exit 1
fi
echo "[PaperVault] Apple Silicon desktop build: dist/PaperVault.app"
