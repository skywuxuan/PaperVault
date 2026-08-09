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

## Platform contract

`desktop/platforms.py` is the platform boundary used by the launcher:

| Platform | Native renderer | Default data directory | Icon |
| --- | --- | --- | --- |
| Windows | Edge Chromium / WebView2 | `%LOCALAPPDATA%\PaperVault` | `.ico` |
| macOS | Cocoa WKWebView | `~/Library/Application Support/PaperVault` | `.icns` |
| Linux | System webview | `$XDG_DATA_HOME/papervault` | `.png` |

The desktop bridge intentionally exposes only application name, version,
platform, and local data path. Paper records, settings, and model credentials
continue to flow through the existing `/api/*` contract, so a future macOS
bundle does not require a frontend or backend rewrite.

## Browser version

The existing browser-hosted application remains supported through `start.ps1`
and `start.bat`. It continues to use the repository `data` directory unless
`--data-dir` is supplied directly to `backend.app`.
