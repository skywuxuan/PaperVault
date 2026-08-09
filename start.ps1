$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "[PaperVault] Creating the local Python environment..."
    python -m venv .venv
}

Write-Host "[PaperVault] Checking dependencies..."
& ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --timeout 90 -q -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "[PaperVault] Retrying dependencies through the Tsinghua PyPI mirror..."
    & ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --timeout 120 -q -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        throw "Dependency installation failed."
    }
}

& "$PSScriptRoot\setup-offline-translation.ps1"

Write-Host "[PaperVault] Open http://127.0.0.1:8765 in your browser."
& ".venv\Scripts\python.exe" -m backend.app --host 127.0.0.1 --port 8765
