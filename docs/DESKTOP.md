# PaperVault Desktop

PaperVault Desktop reuses the existing local HTTP API and frontend. The native
shell owns the localhost server lifecycle and displays it in the operating
system webview. No cloud database or remote web host is introduced.

## Windows development

Requirements:

- Windows 10 or 11
- Python 3.10 or newer
- Microsoft Edge WebView2 Runtime

Start the native application:

```powershell
.\start-desktop.ps1
```

The desktop launcher binds the API to `127.0.0.1` on an ephemeral port. Its
default data directory is `%LOCALAPPDATA%\PaperVault`. To select a different
directory for development or migration:

```powershell
.\start-desktop.ps1 --data-dir "D:\PaperVaultData"
```

`PAPER_VAULT_DATA_DIR` provides the same override for both packaged and source
launches. Back up an existing data directory before opening it with a newer
application version.

For a persistent selection that also applies when `PaperVault.exe` is opened
directly, create `%LOCALAPPDATA%\PaperVault\desktop.json`:

```json
{
  "data_dir": "D:\\PaperVaultData"
}
```

The directory selection order is `--data-dir`, `PAPER_VAULT_DATA_DIR`, the
local `desktop.json`, then the platform default. The configuration file is
machine-local and must not be committed. PDF files, SQLite records, extracted
assets, and offline translation models all use the selected data directory.

## Windows packaging

Build the recommended one-directory package:

```powershell
.\build-windows.ps1
```

The executable is written to `dist\PaperVault\PaperVault.exe`. A single-file
variant is available for distribution testing:

```powershell
.\build-windows.ps1 -OneFile
```

The one-directory build starts faster and is the default release format. The
package uses the installed WebView2 Runtime instead of bundling a browser
engine.

## Apple Silicon macOS development

Requirements:

- An M-series Mac running a supported macOS release
- Arm64 Python 3.10 or newer
- Xcode Command Line Tools

Start the native application:

```bash
./start-desktop.sh
```

Build the application bundle:

```bash
./build-macos.sh
```

The output is `dist/PaperVault.app`. The script rejects Intel Python and Intel runners so the resulting application and native dependencies are consistently arm64. To sign the bundle during the PyInstaller build, export a valid identity first:

```bash
export PAPER_VAULT_CODESIGN_IDENTITY="Developer ID Application: Example Company (TEAMID)"
./build-macos.sh
```

Signing alone does not notarize the application. Public distribution should additionally submit the ZIP to Apple's notary service and staple the ticket. An unsigned local build can be opened from Finder with Control-click → Open when Gatekeeper blocks first launch.

## Local environment

Source launches automatically read `.env.local` from the project root. A typical development configuration is:

```dotenv
PAPER_VAULT_DATA_DIR=../aaa_file/data
PAPER_VAULT_BASE_URL=https://api.openai.com/v1
PAPER_VAULT_API_KEY=your-key
PAPER_VAULT_MODEL=gpt-4.1-mini
```

Shell variables override `.env.local`, and these variables override values stored in SQLite. Relative `PAPER_VAULT_DATA_DIR` values are resolved from the environment file location. `.env.local` is ignored by Git and is not embedded into Windows or macOS packages; installed applications should use system environment variables or configure the model in the application settings.

## Platform contract

`desktop/platforms.py` is the platform boundary used by the launcher:

| Platform | Native renderer | Default data directory | Icon |
| --- | --- | --- | --- |
| Windows | Edge Chromium / WebView2 | `%LOCALAPPDATA%\PaperVault` | `.ico` |
| Apple Silicon macOS | Cocoa WKWebView | `~/Library/Application Support/PaperVault` | `.icns` |
| Linux | System webview | `$XDG_DATA_HOME/papervault` | `.png` |

The desktop bridge intentionally exposes only application name, version,
platform, and local data path. Paper records, settings, and model credentials
continue to flow through the existing `/api/*` contract on both desktop platforms.

## Browser version

The existing browser-hosted application remains supported through `start.ps1`
and `start.bat`. It continues to use the repository `data` directory unless
`--data-dir` is supplied directly to `backend.app`.
