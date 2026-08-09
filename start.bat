@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [PaperVault] Creating the local Python environment...
  python -m venv .venv
  if errorlevel 1 goto :error
)

echo [PaperVault] Checking dependencies...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --timeout 90 -q -r requirements.txt
if errorlevel 1 (
  echo [PaperVault] Retrying dependencies through the Tsinghua PyPI mirror...
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --timeout 120 -q -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
  if errorlevel 1 goto :error
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-offline-translation.ps1"
if errorlevel 1 goto :error

echo [PaperVault] Open http://127.0.0.1:8765 in your browser.
".venv\Scripts\python.exe" -m backend.app --host 127.0.0.1 --port 8765
goto :eof

:error
echo.
echo [PaperVault] Startup failed. Check Python 3.10+, network access, and available disk space.
pause
exit /b 1
