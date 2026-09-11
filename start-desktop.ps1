$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -c "import sys; raise SystemExit('PaperVault requires Python 3.10 or newer') if sys.version_info < (3, 10) else None"
    if ($LASTEXITCODE -ne 0) { throw "Python 3.10 or newer is required." }
    Write-Host "[PaperVault] Creating the local Python environment..."
    python -m venv .venv
}
& ".venv\Scripts\python.exe" -c "import sys; raise SystemExit('PaperVault requires Python 3.10 or newer') if sys.version_info < (3, 10) else None"
if ($LASTEXITCODE -ne 0) { throw "Python 3.10 or newer is required." }

& ".venv\Scripts\python.exe" -c "import webview" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[PaperVault] Installing desktop dependencies..."
    & ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --timeout 120 -r requirements-desktop.txt
    if ($LASTEXITCODE -ne 0) {
        throw "Desktop dependency installation failed."
    }
}

& ".venv\Scripts\python.exe" -m desktop.app @args
