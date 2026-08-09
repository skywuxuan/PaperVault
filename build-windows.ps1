param(
    [switch]$SkipInstall,
    [switch]$OneFile
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if ($env:OS -ne "Windows_NT") {
    throw "The Windows package must be built on Windows."
}
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    throw "Run start-desktop.ps1 once to create the local environment."
}
if (-not $SkipInstall) {
    & ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --timeout 120 -r requirements-desktop.txt
    if ($LASTEXITCODE -ne 0) { throw "Desktop dependency installation failed." }
}

& ".venv\Scripts\python.exe" "tools\generate_desktop_icons.py"
if ($LASTEXITCODE -ne 0) { throw "Desktop icon generation failed." }

$bundleMode = if ($OneFile) { "--onefile" } else { "--onedir" }
$frontendPath = (Resolve-Path "frontend").Path
$iconPath = (Resolve-Path "desktop\assets\papervault.ico").Path
$assetPath = (Resolve-Path "desktop\assets").Path
$versionPath = (Resolve-Path "packaging\windows-version.txt").Path
$pyinstallerArgs = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--windowed",
    $bundleMode,
    "--name", "PaperVault",
    "--specpath", "build",
    "--workpath", "build\pyinstaller",
    "--distpath", "dist",
    "--icon", $iconPath,
    "--version-file", $versionPath,
    "--add-data", "$frontendPath;frontend",
    "--add-data", "$assetPath;desktop\assets",
    "--collect-all", "webview",
    "--collect-all", "cmudict",
    "--hidden-import", "webview.platforms.edgechromium",
    "--exclude-module", "PyQt5",
    "--exclude-module", "PyQt6",
    "--exclude-module", "PySide2",
    "--exclude-module", "PySide6",
    "--exclude-module", "cefpython3",
    "papervault_desktop.py"
)

& ".venv\Scripts\python.exe" @pyinstallerArgs
if ($LASTEXITCODE -ne 0) { throw "PaperVault Windows build failed." }

$output = if ($OneFile) { "dist\PaperVault.exe" } else { "dist\PaperVault\PaperVault.exe" }
Write-Host "[PaperVault] Windows desktop build: $output"
