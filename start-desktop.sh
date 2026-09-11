#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

python_command="${PAPER_VAULT_PYTHON:-python3}"
if [[ ! -x ".venv/bin/python" ]]; then
  "$python_command" -c 'import sys; raise SystemExit("PaperVault requires Python 3.10 or newer") if sys.version_info < (3, 10) else None'
  echo "[PaperVault] Creating the local Python environment..."
  "$python_command" -m venv .venv
fi
./.venv/bin/python -c 'import sys; raise SystemExit("PaperVault requires Python 3.10 or newer") if sys.version_info < (3, 10) else None'

if ! .venv/bin/python -c "import webview" >/dev/null 2>&1; then
  echo "[PaperVault] Installing desktop dependencies..."
  .venv/bin/python -m pip install --disable-pip-version-check --timeout 120 -r requirements-desktop.txt
fi

exec .venv/bin/python -m desktop.app "$@"
