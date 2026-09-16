from __future__ import annotations

import json
from pathlib import Path
import plistlib
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.desktop_version import DesktopVersion
from tools.verify_desktop_bundle import (
    frontend_manifest,
    verify_bundle,
    verify_frontend,
    verify_macos_metadata,
    verify_release_manifests,
    verify_windows_metadata,
)


class DesktopBundleVerificationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.frontend = self.source / "frontend"
        self.frontend.mkdir(parents=True)
        (self.frontend / "index.html").write_bytes(b"<html>\nPaperVault\n</html>\n")
        (self.frontend / "app.js").write_bytes(b"const version = 'current';\n")
        backend = self.source / "backend"
        backend.mkdir()
        (backend / "__init__.py").write_text('__version__ = "1.2.0-rc.2"\n', encoding="utf-8")
        self.version = DesktopVersion.parse("1.2.0-rc.2")

    def copied_frontend(self) -> Path:
        bundled = self.root / "bundled-frontend"
        bundled.mkdir()
        for path in self.frontend.iterdir():
            (bundled / path.name).write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        return bundled

    def test_windows_and_macos_newlines_have_identical_hashes(self) -> None:
        bundled = self.copied_frontend()
        self.assertEqual(verify_frontend(self.frontend, bundled), frontend_manifest(self.frontend))
        self.assertEqual(frontend_manifest(bundled)["index.html"]["normalization"], "utf8-lf")

    def test_rejects_stale_missing_and_extra_frontend_files(self) -> None:
        bundled = self.copied_frontend()
        script = bundled / "app.js"
        original = script.read_bytes()
        script.write_text("const version = 'old';\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "changed=.*app.js"):
            verify_frontend(self.frontend, bundled)
        script.unlink()
        with self.assertRaisesRegex(ValueError, "missing=.*app.js"):
            verify_frontend(self.frontend, bundled)
        script.write_bytes(original)
        (bundled / "stale.css").write_text("body {}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "extra=.*stale.css"):
            verify_frontend(self.frontend, bundled)

    def test_binary_resources_are_not_newline_normalized(self) -> None:
        bundled = self.copied_frontend()
        (self.frontend / "icon.png").write_bytes(b"PNG\r\n")
        (bundled / "icon.png").write_bytes(b"PNG\n")
        with self.assertRaisesRegex(ValueError, "changed=.*icon.png"):
            verify_frontend(self.frontend, bundled)

    def test_rejects_wrong_commit_before_inspecting_bundle(self) -> None:
        with patch("tools.verify_desktop_bundle.command_output", return_value="a" * 40):
            with self.assertRaisesRegex(ValueError, "Source commit mismatch"):
                verify_bundle(self.source, self.root / "no-bundle", "windows-x64", "b" * 40)

    def windows_executable(self, machine: int = 0x8664) -> Path:
        executable = self.root / "PaperVault.exe"
        content = bytearray(0x100)
        content[:2] = b"MZ"
        struct.pack_into("<I", content, 0x3C, 0x80)
        content[0x80:0x84] = b"PE\0\0"
        struct.pack_into("<H", content, 0x84, machine)
        executable.write_bytes(content)
        return executable

    def test_windows_rejects_stale_version_and_wrong_architecture(self) -> None:
        metadata = {"FileVersion": "1.2.0-rc.1", "ProductVersion": "1.2.0-rc.1", "ProductName": "PaperVault", "IsPreRelease": True}
        with patch("tools.verify_desktop_bundle.command_output", return_value=json.dumps(metadata)):
            with self.assertRaisesRegex(ValueError, "metadata mismatch"):
                verify_windows_metadata(self.windows_executable(), self.version)
        with self.assertRaisesRegex(ValueError, "not x64"):
            verify_windows_metadata(self.windows_executable(0x14C), self.version)

    def test_windows_accepts_full_prerelease_metadata(self) -> None:
        metadata = {"FileVersion": self.version.text, "ProductVersion": self.version.text, "ProductName": "PaperVault", "IsPreRelease": True}
        with patch("tools.verify_desktop_bundle.command_output", return_value=json.dumps(metadata)):
            self.assertEqual(verify_windows_metadata(self.windows_executable(), self.version), {**metadata, "architecture": "x64"})

    def macos_bundle(self) -> Path:
        bundle = self.root / "PaperVault.app"
        (bundle / "Contents").mkdir(exist_ok=True, parents=True)
        metadata = {**self.version.macos_plist(), "LSMinimumSystemVersion": "14.0"}
        (bundle / "Contents" / "Info.plist").write_bytes(plistlib.dumps(metadata))
        return bundle

    def test_macos_rejects_wrong_executable_version_or_signature(self) -> None:
        bundle = self.macos_bundle()
        with patch("tools.verify_desktop_bundle.command_output", side_effect=["arm64", "1.2.0-rc.1"]):
            with self.assertRaisesRegex(ValueError, "executable version mismatch"):
                verify_macos_metadata(bundle, self.version)
        with patch("tools.verify_desktop_bundle.command_output", side_effect=["arm64", self.version.text, subprocess.CalledProcessError(1, "codesign")]):
            with self.assertRaises(subprocess.CalledProcessError):
                verify_macos_metadata(bundle, self.version)

    def test_macos_requires_correct_plist_and_arm64(self) -> None:
        bundle = self.macos_bundle()
        with patch("tools.verify_desktop_bundle.command_output", return_value="x86_64"):
            with self.assertRaisesRegex(ValueError, "not arm64"):
                verify_macos_metadata(bundle, self.version)
        with self.assertRaisesRegex(ValueError, "CFBundleVersion mismatch"):
            verify_macos_metadata(bundle, DesktopVersion.parse("1.2.0-rc.3"))

    def test_release_requires_two_matching_receipts_from_same_commit(self) -> None:
        directory = self.root / "dist"
        directory.mkdir()
        sha = "a" * 40
        paths = []
        for platform in ("windows-x64", "macos-arm64"):
            payload = {"schema_version": 1, "status": "passed", "platform": platform, "version": self.version.text, "git_sha": sha, "frontend": frontend_manifest(self.frontend)}
            path = directory / f"PaperVault-v{self.version.text}-{platform}-validation.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            paths.append(path)
        verify_release_manifests(directory, self.source, self.version.text, sha)
        payload["git_sha"] = "b" * 40
        paths[-1].write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "git_sha mismatch"):
            verify_release_manifests(directory, self.source, self.version.text, sha)
        payload["git_sha"] = sha
        payload["frontend"]["app.js"]["sha256"] = "0" * 64
        paths[-1].write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "frontend does not match"):
            verify_release_manifests(directory, self.source, self.version.text, sha)
        paths[-1].unlink()
        with self.assertRaises(FileNotFoundError):
            verify_release_manifests(directory, self.source, self.version.text, sha)


if __name__ == "__main__":
    unittest.main()
