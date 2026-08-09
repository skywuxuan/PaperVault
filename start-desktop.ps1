$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "[PaperVault] Creating the local Python environment..."
    python -m venv .venv
}

& ".venv\Scripts\python.exe" -c "import webview" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[PaperVault] Installing desktop dependencies..."
    & ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --timeout 120 -r requirements-desktop.txt
    if ($LASTEXITCODE -ne 0) {
        throw "Desktop dependency installation failed."
    }
}

& ".venv\Scripts\python.exe" -m desktop.app @args
