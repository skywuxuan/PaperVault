from __future__ import annotations

import http.client
import json
import tempfile
import unittest
from pathlib import Path

from desktop.app import BackendRuntime, DesktopBridge, build_parser
from desktop.platforms import default_data_dir, get_desktop_platform, resource_root


PROJECT_DIR = Path(__file__).resolve().parent.parent


class DesktopPlatformTestCase(unittest.TestCase):
    def test_windows_platform_uses_local_app_data_and_webview2(self) -> None:
        platform = get_desktop_platform(
            "win32",
            {"LOCALAPPDATA": r"C:\Users\tester\AppData\Local"},
            Path(r"C:\Users\tester"),
        )
        self.assertEqual(platform.name, "windows")
        self.assertEqual(platform.renderer, "edgechromium")
        self.assertEqual(platform.data_dir.name, "PaperVault")
        self.assertEqual(platform.webview_storage_dir.parent, platform.data_dir)

    def test_macos_platform_keeps_application_support_contract(self) -> None:
        home = Path("/Users/tester")
        platform = get_desktop_platform("darwin", {}, home)
        self.assertEqual(platform.name, "macos")
        self.assertIsNone(platform.renderer)
        self.assertTrue(
            platform.data_dir.as_posix().endswith(
                "/Users/tester/Library/Application Support/PaperVault"
            )
        )

    def test_data_directory_override_is_cross_platform(self) -> None:
        override = PROJECT_DIR / "custom-data"
        actual = default_data_dir(
            "win32", {"PAPER_VAULT_DATA_DIR": str(override)}, Path.home()
        )
        self.assertEqual(actual, override.resolve())

    def test_resource_root_defaults_to_project(self) -> None:
        self.assertEqual(resource_root({}), PROJECT_DIR)

    def test_desktop_parser_defaults_to_ephemeral_port(self) -> None:
        args = build_parser().parse_args([])
        self.assertEqual(args.port, 0)
        self.assertIsNone(args.data_dir)

    def test_desktop_version_is_available_without_starting_gui(self) -> None:
        with self.assertRaises(SystemExit) as context:
            build_parser().parse_args(["--version"])
        self.assertEqual(context.exception.code, 0)


class DesktopRuntimeTestCase(unittest.TestCase):
    def test_backend_runtime_serves_health_and_stops(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = BackendRuntime(PROJECT_DIR / "frontend", Path(temp_dir))
            runtime.start()
            connection = http.client.HTTPConnection(
                "127.0.0.1", runtime.server.server_port, timeout=5
            )
            try:
                connection.request("GET", "/api/health")
                response = connection.getresponse()
                payload = json.loads(response.read())
            finally:
                connection.close()
                runtime.stop()
            self.assertEqual(response.status, 200)
            self.assertEqual(payload, {"status": "ok", "version": "1.9.0"})
            self.assertFalse(runtime.thread.is_alive())

    def test_desktop_bridge_exposes_platform_without_secrets(self) -> None:
        platform = get_desktop_platform(
            "win32", {"LOCALAPPDATA": r"C:\Users\tester\AppData\Local"}
        )
        info = DesktopBridge(platform).app_info()
        self.assertEqual(info["platform"], "windows")
        self.assertEqual(info["version"], "1.9.0")
        self.assertNotIn("api_key", info)


if __name__ == "__main__":
    unittest.main()
