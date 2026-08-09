@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-desktop.ps1" %*
if errorlevel 1 (
  echo.
  echo [PaperVault] Desktop startup failed.
  pause
  exit /b 1
)
