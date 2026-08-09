from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


APP_NAME = "PaperVault"
DATA_DIR_ENV = "PAPER_VAULT_DATA_DIR"
RESOURCE_DIR_ENV = "PAPER_VAULT_RESOURCE_DIR"


@dataclass(frozen=True)
class DesktopPlatform:
    """Platform-specific desktop settings kept separate from application logic."""

    name: str
    renderer: str | None
    data_dir: Path
    webview_storage_dir: Path
    icon_name: str


def _expanded_path(value: str) -> Path:
    return Path(os.path.expandvars(value)).expanduser().resolve()


def default_data_dir(
    platform_name: str | None = None,
    environ: Mapping[str, str] | None = None,
    home_dir: Path | None = None,
) -> Path:
    environment = os.environ if environ is None else environ
    override = environment.get(DATA_DIR_ENV, "").strip()
    if override:
        return _expanded_path(override)

    platform_key = platform_name or sys.platform
    home = (home_dir or Path.home()).resolve()
    if platform_key == "win32":
        local_app_data = environment.get("LOCALAPPDATA") or environment.get("APPDATA")
        base = _expanded_path(local_app_data) if local_app_data else home / "AppData" / "Local"
        return (base / APP_NAME).resolve()
    if platform_key == "darwin":
        return (home / "Library" / "Application Support" / APP_NAME).resolve()

    xdg_data_home = environment.get("XDG_DATA_HOME", "").strip()
    base = _expanded_path(xdg_data_home) if xdg_data_home else home / ".local" / "share"
    return (base / APP_NAME.lower()).resolve()


def resource_root(
    environ: Mapping[str, str] | None = None,
    bundle_dir: str | Path | None = None,
) -> Path:
    environment = os.environ if environ is None else environ
    override = environment.get(RESOURCE_DIR_ENV, "").strip()
    if override:
        return _expanded_path(override)

    bundled = bundle_dir if bundle_dir is not None else getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled).resolve()
    return Path(__file__).resolve().parent.parent


def get_desktop_platform(
    platform_name: str | None = None,
    environ: Mapping[str, str] | None = None,
    home_dir: Path | None = None,
) -> DesktopPlatform:
    platform_key = platform_name or sys.platform
    data_dir = default_data_dir(platform_key, environ, home_dir)
    if platform_key == "win32":
        name, renderer, icon_name = "windows", "edgechromium", "papervault.ico"
    elif platform_key == "darwin":
        name, renderer, icon_name = "macos", None, "papervault.icns"
    else:
        name, renderer, icon_name = "linux", None, "papervault.png"
    return DesktopPlatform(
        name=name,
        renderer=renderer,
        data_dir=data_dir,
        webview_storage_dir=data_dir / "webview",
        icon_name=icon_name,
    )
