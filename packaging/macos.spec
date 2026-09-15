# -*- mode: python ; coding: utf-8 -*-
"""Apple Silicon app bundle; metadata is set before PyInstaller signs it."""

import os
from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_all

project_dir = Path(SPECPATH).resolve().parent
sys.path.insert(0, str(project_dir))
from tools.desktop_version import read_version

version = read_version()
info_plist = version.macos_plist()
# Native wheels selected on current Apple Silicon runners can require macOS 14
# (for example NumPy's Accelerate build). Do not advertise an older OS baseline.
info_plist["LSMinimumSystemVersion"] = "14.0"
datas = [
    (str(project_dir / "frontend"), "frontend"),
    (str(project_dir / "desktop" / "assets"), "desktop/assets"),
]
binaries = []
hiddenimports = ["webview.platforms.cocoa"]
for package in ("webview", "cmudict"):
    package_datas, package_binaries, package_imports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_imports

a = Analysis(
    [str(project_dir / "papervault_desktop.py")],
    pathex=[str(project_dir)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["PyQt5", "PyQt6", "PySide2", "PySide6", "cefpython3"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PaperVault",
    console=False,
    target_arch="arm64",
    codesign_identity=os.environ.get("PAPER_VAULT_CODESIGN_IDENTITY") or None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="PaperVault")
app = BUNDLE(
    coll,
    name="PaperVault.app",
    icon=str(project_dir / "desktop" / "assets" / "papervault.icns"),
    bundle_identifier="com.rasinwu.papervault",
    version=info_plist["CFBundleShortVersionString"],
    info_plist=info_plist,
)
