#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

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
"$python_command" -c 'import sys; raise SystemExit("PaperVault requires Python 3.10 or newer") if sys.version_info < (3, 10) else None'

if (( ! skip_install )); then
  "$python_command" -m pip install --disable-pip-version-check --timeout 120 -r requirements-desktop.txt
fi
"$python_command" -c "import PyInstaller, webview"
"$python_command" tools/generate_desktop_icons.py

"$python_command" tools/desktop_version.py
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
echo "[PaperVault] Apple Silicon desktop build: dist/PaperVault.app"
