param(
    [switch]$SkipInstall,
    [switch]$OneFile
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if ($env:OS -ne "Windows_NT") {
    throw "The Windows package must be built on Windows."
}
$python = ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    if ($SkipInstall) {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if ($null -eq $pythonCommand) {
            throw "Python 3.10 or newer is required."
        }
        $python = $pythonCommand.Source
    } else {
        Write-Host "[PaperVault] Creating the local Python environment..."
        python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw "Python environment creation failed." }
    }
}
& $python -c "import sys; raise SystemExit('PaperVault requires Python 3.10 or newer') if sys.version_info < (3, 10) else None"
if ($LASTEXITCODE -ne 0) { throw "Python 3.10 or newer is required." }
if (-not $SkipInstall) {
    & $python -m pip install --disable-pip-version-check --timeout 120 -r requirements-desktop.txt
    if ($LASTEXITCODE -ne 0) { throw "Desktop dependency installation failed." }
}

& $python -c "import PyInstaller, webview"
if ($LASTEXITCODE -ne 0) { throw "Desktop build dependencies are unavailable." }

& $python "tools\generate_desktop_icons.py"
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

& $python @pyinstallerArgs
if ($LASTEXITCODE -ne 0) { throw "PaperVault Windows build failed." }

$output = if ($OneFile) { "dist\PaperVault.exe" } else { "dist\PaperVault\PaperVault.exe" }
Write-Host "[PaperVault] Windows desktop build: $output"
