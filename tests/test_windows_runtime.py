from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop.windows_runtime import WindowsRuntimeError, check_windows_runtime_files


class WindowsRuntimeFilesTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.bundle_root = Path(self.temp_dir.name)

    def make_runtime_file(
        self,
        relative_path: str = "pythonnet/runtime/Python.Runtime.dll",
        metadata: str | None = None,
    ) -> Path:
        path = self.bundle_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"runtime-test-fixture")
        if metadata is not None:
            Path(f"{path}:Zone.Identifier").write_text(metadata, encoding="utf-8")
        return path

    def check_bundle(self) -> None:
        check_windows_runtime_files(self.bundle_root, platform_name="win32", frozen=True)

    def test_internet_and_restricted_downloads_explain_manual_recovery(self) -> None:
        for zone_id in (3, 4):
            with self.subTest(zone_id=zone_id):
                path = self.make_runtime_file(metadata=f"[ZoneTransfer]\nZoneId={zone_id}\n")
                with self.assertRaises(WindowsRuntimeError) as context:
                    self.check_bundle()
                self.assertEqual(context.exception.blocked_files, (path,))
                message = str(context.exception)
                for text in ("原始 ZIP", "属性", "解除锁定", "新目录", "PaperVault.exe"):
                    self.assertIn(text, message)

    def test_checks_both_required_webview_assemblies(self) -> None:
        paths = tuple(
            self.make_runtime_file(name, "[ZoneTransfer]\nZoneId=3\n")
            for name in (
                "webview/lib/Microsoft.Web.WebView2.Core.dll",
                "webview/lib/Microsoft.Web.WebView2.WinForms.dll",
            )
        )
        with self.assertRaises(WindowsRuntimeError) as context:
            self.check_bundle()
        self.assertEqual(context.exception.blocked_files, paths)

    def test_detection_does_not_change_file_or_download_metadata(self) -> None:
        path = self.make_runtime_file(metadata="[ZoneTransfer]\r\nZoneId=3\r\n")
        stream = Path(f"{path}:Zone.Identifier")
        original_file, original_stream = path.read_bytes(), stream.read_bytes()
        with self.assertRaises(WindowsRuntimeError):
            self.check_bundle()
        self.assertEqual(path.read_bytes(), original_file)
        self.assertEqual(stream.read_bytes(), original_stream)

    def test_missing_streams_and_files_are_not_download_blocks(self) -> None:
        self.check_bundle()
        self.make_runtime_file()
        self.check_bundle()

    def test_local_intranet_and_trusted_zones_are_not_download_blocks(self) -> None:
        for zone_id in (0, 1, 2):
            with self.subTest(zone_id=zone_id):
                self.make_runtime_file(metadata=f"[ZoneTransfer]\nZoneId={zone_id}\n")
                self.check_bundle()

    def test_malformed_or_unrelated_metadata_is_not_treated_as_blocked(self) -> None:
        for metadata in (
            "not-an-ini-file",
            "[ZoneTransfer]\nZoneId=unknown\n",
            "[ZoneTransfer]\nHostUrl=https://example.com/?ZoneId=3\n",
            "[OtherSection]\nZoneId=3\n",
        ):
            with self.subTest(metadata=metadata):
                self.make_runtime_file(metadata=metadata)
                self.check_bundle()

    def test_utf8_bom_and_crlf_metadata_are_supported(self) -> None:
        self.make_runtime_file(metadata="\ufeff[ZoneTransfer]\r\nZoneId=3\r\n")
        with self.assertRaises(WindowsRuntimeError):
            self.check_bundle()

    def test_unreadable_metadata_is_not_reported_as_a_download_block(self) -> None:
        self.make_runtime_file()
        with patch("desktop.windows_runtime.Path.read_text", side_effect=PermissionError):
            self.check_bundle()

    def test_source_launch_does_not_inspect_runtime_files(self) -> None:
        with patch("desktop.windows_runtime._download_zone_id") as read_zone:
            check_windows_runtime_files(self.bundle_root, platform_name="win32", frozen=False)
        read_zone.assert_not_called()

    def test_other_platforms_do_not_inspect_runtime_files(self) -> None:
        with patch("desktop.windows_runtime._download_zone_id") as read_zone:
            for platform in ("darwin", "linux"):
                check_windows_runtime_files(self.bundle_root, platform_name=platform, frozen=True)
        read_zone.assert_not_called()

    def test_default_detection_uses_current_platform_and_frozen_state(self) -> None:
        self.make_runtime_file(metadata="[ZoneTransfer]\nZoneId=3\n")
        with (
            patch("desktop.windows_runtime.sys.platform", "win32"),
            patch("desktop.windows_runtime.sys.frozen", True, create=True),
            self.assertRaises(WindowsRuntimeError),
        ):
            check_windows_runtime_files(self.bundle_root)

    def test_unrelated_dlls_and_unused_renderers_are_not_inspected(self) -> None:
        self.make_runtime_file("webview/lib/WebBrowserInterop.x86.dll", "[ZoneTransfer]\nZoneId=3\n")
        self.make_runtime_file(
            "webview/lib/runtimes/win-x64/native/WebView2Loader.dll", "[ZoneTransfer]\nZoneId=3\n"
        )
        self.make_runtime_file("unrelated.dll", "[ZoneTransfer]\nZoneId=3\n")
        self.check_bundle()


if __name__ == "__main__":
    unittest.main()
