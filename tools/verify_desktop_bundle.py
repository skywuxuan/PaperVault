"""Verify that a desktop bundle contains the frontend and version being released."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import struct
import subprocess
import sys
from typing import Any, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.desktop_version import DesktopVersion, PROJECT_DIR, read_version


TEXT_SUFFIXES = {".css", ".html", ".js", ".json", ".map", ".svg", ".txt", ".webmanifest"}


def frontend_manifest(directory: Path) -> dict[str, dict[str, str]]:
    """Hash text with LF newlines so Windows and macOS checkouts are comparable."""
    if not directory.is_dir():
        raise ValueError(f"Frontend directory is missing: {directory}")
    manifest: dict[str, dict[str, str]] = {}
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        content = path.read_bytes()
        normalization = "binary"
        if path.suffix.lower() in TEXT_SUFFIXES:
            content = content.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
            normalization = "utf8-lf"
        manifest[path.relative_to(directory).as_posix()] = {
            "sha256": hashlib.sha256(content).hexdigest(),
            "normalization": normalization,
        }
    if "index.html" not in manifest:
        raise ValueError(f"Frontend index.html is missing: {directory}")
    return manifest


def verify_frontend(source: Path, bundled: Path) -> dict[str, dict[str, str]]:
    expected = frontend_manifest(source)
    actual = frontend_manifest(bundled)
    missing = sorted(expected.keys() - actual.keys())
    extra = sorted(actual.keys() - expected.keys())
    changed = sorted(name for name in expected.keys() & actual.keys() if expected[name] != actual[name])
    if missing or extra or changed:
        raise ValueError(f"Bundled frontend differs from source: missing={missing}, extra={extra}, changed={changed}")
    return actual


def command_output(command: Sequence[str], **kwargs: Any) -> str:
    result = subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8", timeout=60, **kwargs)
    return result.stdout.strip()


def verify_windows_metadata(executable: Path, version: DesktopVersion) -> dict[str, Any]:
    # Windowed Windows executables have no stdout; inspect their embedded version
    # resource instead of relying on --version output from a console subsystem.
    with executable.open("rb") as binary:
        if binary.read(2) != b"MZ":
            raise ValueError("Windows executable has no DOS header")
        binary.seek(0x3C)
        pe_offset = struct.unpack("<I", binary.read(4))[0]
        binary.seek(pe_offset)
        if binary.read(4) != b"PE\0\0" or struct.unpack("<H", binary.read(2))[0] != 0x8664:
            raise ValueError("Windows executable is not x64")
    environment = dict(os.environ, PAPER_VAULT_VERIFY_EXE=str(executable.resolve()))
    script = (
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
        "$v = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($env:PAPER_VAULT_VERIFY_EXE); "
        "@{FileVersion=$v.FileVersion; ProductVersion=$v.ProductVersion; "
        "ProductName=$v.ProductName; IsPreRelease=$v.IsPreRelease} | ConvertTo-Json -Compress"
    )
    metadata = json.loads(command_output(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], env=environment))
    expected = {
        "FileVersion": version.text,
        "ProductVersion": version.text,
        "ProductName": "PaperVault",
        "IsPreRelease": version.prerelease,
    }
    if metadata != expected:
        raise ValueError(f"Windows version metadata mismatch: expected={expected}, actual={metadata}")
    return {**metadata, "architecture": "x64"}


def verify_macos_metadata(bundle: Path, version: DesktopVersion) -> dict[str, Any]:
    metadata = plistlib.loads((bundle / "Contents" / "Info.plist").read_bytes())
    expected = {**version.macos_plist(), "LSMinimumSystemVersion": "14.0"}
    for key, value in expected.items():
        if metadata.get(key) != value:
            raise ValueError(f"macOS {key} mismatch: expected={value!r}, actual={metadata.get(key)!r}")
    executable = bundle / "Contents" / "MacOS" / "PaperVault"
    architecture = command_output(["lipo", "-archs", str(executable)])
    if architecture.split() != ["arm64"]:
        raise ValueError(f"macOS executable is not arm64: {architecture}")
    executable_version = command_output([str(executable), "--version"])
    if executable_version != version.text:
        raise ValueError(f"macOS executable version mismatch: {executable_version!r}")
    command_output(["codesign", "--verify", "--deep", "--strict", str(bundle)])
    return {**expected, "architecture": "arm64", "executable_version": executable_version, "codesign_verified": True}


def verify_bundle(source_root: Path, bundle: Path, platform: str, expected_git_sha: str | None = None) -> dict[str, Any]:
    source_root = source_root.resolve()
    bundle = bundle.resolve()
    version = read_version(source_root / "backend" / "__init__.py")
    git_sha = command_output(["git", "rev-parse", "HEAD"], cwd=source_root)
    if expected_git_sha is not None and git_sha != expected_git_sha:
        raise ValueError(f"Source commit mismatch: expected={expected_git_sha}, actual={git_sha}")
    if platform == "windows-x64":
        frontend = bundle / "_internal" / "frontend"
        metadata = verify_windows_metadata(bundle / "PaperVault.exe", version)
    elif platform == "macos-arm64":
        frontend = bundle / "Contents" / "Resources" / "frontend"
        metadata = verify_macos_metadata(bundle, version)
    else:
        raise ValueError(f"Unsupported desktop platform: {platform}")
    return {
        "schema_version": 1,
        "status": "passed",
        "platform": platform,
        "version": version.text,
        "git_sha": git_sha,
        "frontend": verify_frontend(source_root / "frontend", frontend),
        "metadata": metadata,
    }


def verify_release_manifests(directory: Path, source_root: Path, version: str, git_sha: str) -> None:
    """Require both build receipts to match the release checkout before uploading."""
    expected_frontend = frontend_manifest(source_root / "frontend")
    for platform in ("windows-x64", "macos-arm64"):
        receipt = directory / f"PaperVault-v{version}-{platform}-validation.json"
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        expected = {"schema_version": 1, "status": "passed", "version": version, "git_sha": git_sha, "platform": platform}
        for key, value in expected.items():
            if payload.get(key) != value:
                raise ValueError(f"{receipt.name}: {key} mismatch")
        if payload.get("frontend") != expected_frontend:
            raise ValueError(f"{receipt.name}: frontend does not match the release checkout")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", required=True, choices=("windows-x64", "macos-arm64"))
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--source-root", type=Path, default=PROJECT_DIR)
    parser.add_argument("--expected-git-sha")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = verify_bundle(args.source_root, args.bundle, args.platform, args.expected_git_sha)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    except (OSError, ValueError, struct.error, subprocess.SubprocessError) as error:
        parser.error(str(error))
    print(f"Verified {result['platform']} {result['version']} at {result['git_sha']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
