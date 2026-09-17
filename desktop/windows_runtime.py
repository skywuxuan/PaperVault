from __future__ import annotations

import configparser
import sys
from pathlib import Path


# These managed assemblies are required by pywebview's EdgeChromium backend.
# Native WebView2Loader DLLs and unused fallback renderers are not inspected.
_MANAGED_RUNTIME_FILES = (
    Path("pythonnet/runtime/Python.Runtime.dll"),
    Path("webview/lib/Microsoft.Web.WebView2.Core.dll"),
    Path("webview/lib/Microsoft.Web.WebView2.WinForms.dll"),
)


class WindowsRuntimeError(RuntimeError):
    """An extracted desktop runtime has been blocked by Windows."""

    def __init__(self, blocked_files: tuple[Path, ...]):
        self.blocked_files = blocked_files
        filenames = "、".join(path.name for path in blocked_files)
        super().__init__(
            "Windows 阻止了从互联网下载的运行文件，PaperVault 无法启动。\n\n"
            "请对从 PaperVault 官方 GitHub Releases 下载的原始 ZIP 执行以下操作：\n"
            "1. 右键 ZIP 文件，打开“属性”，在“常规”页勾选“解除锁定”，然后点击“应用”。\n"
            "2. 将这个 ZIP 完整解压到一个新目录，不要覆盖之前解压的目录。\n"
            "3. 打开新目录里的 PaperVault 文件夹，再运行 PaperVault.exe。\n\n"
            f"受影响文件：{filenames}"
        )


def _download_zone_id(path: Path) -> int | None:
    """Read Mark-of-the-Web metadata without changing the file or its streams."""
    try:
        if not path.is_file():
            return None
        metadata = Path(f"{path}:Zone.Identifier").read_text(
            encoding="utf-8-sig", errors="replace"
        )
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(metadata)
        return parser.getint("ZoneTransfer", "ZoneId", fallback=None)
    except (OSError, ValueError, configparser.Error):
        # Missing streams are normal. Only a recognized Internet/Restricted
        # zone is actionable; an unreadable stream is not proof of blocking.
        return None


def check_windows_runtime_files(
    bundle_root: Path,
    *,
    platform_name: str | None = None,
    frozen: bool | None = None,
) -> None:
    """Explain known download blocks before loading the packaged Windows GUI.

    ``bundle_root`` is PyInstaller's resource directory (``sys._MEIPASS``),
    not the directory containing the executable. Source launches and other
    platforms are intentionally left alone. No files or security settings
    are modified.
    """
    current_platform = sys.platform if platform_name is None else platform_name
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if current_platform != "win32" or not is_frozen:
        return

    root = Path(bundle_root)
    blocked = tuple(
        root / relative_path
        for relative_path in _MANAGED_RUNTIME_FILES
        if _download_zone_id(root / relative_path) in (3, 4)
    )
    if blocked:
        raise WindowsRuntimeError(blocked)
