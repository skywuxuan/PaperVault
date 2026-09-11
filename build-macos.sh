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

pyinstaller_args=(
  -m PyInstaller
  --noconfirm
  --clean
  --windowed
  --onedir
  --name PaperVault
  --specpath build
  --workpath build/pyinstaller-macos
  --distpath dist
  --icon desktop/assets/papervault.icns
  --osx-bundle-identifier com.rasinwu.papervault
  --target-architecture arm64
  --add-data "frontend:frontend"
  --add-data "desktop/assets:desktop/assets"
  --collect-all webview
  --collect-all cmudict
  --hidden-import webview.platforms.cocoa
  --exclude-module PyQt5
  --exclude-module PyQt6
  --exclude-module PySide2
  --exclude-module PySide6
  --exclude-module cefpython3
)
if [[ -n "${PAPER_VAULT_CODESIGN_IDENTITY:-}" ]]; then
  pyinstaller_args+=(--codesign-identity "$PAPER_VAULT_CODESIGN_IDENTITY")
fi
pyinstaller_args+=(papervault_desktop.py)

"$python_command" "${pyinstaller_args[@]}"

if [[ ! -d "dist/PaperVault.app" ]]; then
  echo "PaperVault macOS build did not produce dist/PaperVault.app." >&2
  exit 1
fi
echo "[PaperVault] Apple Silicon desktop build: dist/PaperVault.app"
