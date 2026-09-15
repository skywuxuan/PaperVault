from __future__ import annotations

import http.client
import json
import tempfile
import unittest
from unittest.mock import Mock, patch
from pathlib import Path

from backend import __version__
from desktop.app import BackendRuntime, DesktopBridge, DesktopCloseGuard, build_parser
from desktop.platforms import (
    configured_data_dir,
    default_data_dir,
    desktop_config_path,
    get_desktop_platform,
    resource_root,
)


PROJECT_DIR = Path(__file__).resolve().parent.parent


class DesktopCloseGuardTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.window = Mock()
        self.window.events.loaded.is_set.return_value = True
        self.guard = DesktopCloseGuard(self.window)

    def test_close_waits_for_note_save_without_blocking_the_ui_thread(self) -> None:
        with patch("desktop.app.threading.Thread") as thread:
            self.assertFalse(self.guard.request_close())
            self.assertFalse(self.guard.request_close())
            thread.assert_called_once()
        self.window.destroy.assert_not_called()
        self.guard._save()
        callback = self.window.evaluate_js.call_args.kwargs["callback"]
        callback(True)
        self.window.destroy.assert_called_once()
        self.assertTrue(self.guard.request_close())

    def test_failed_save_keeps_window_open_and_allows_retry(self) -> None:
        with patch("desktop.app.threading.Thread") as thread:
            self.assertFalse(self.guard.request_close())
            self.guard._saved(False)
            self.assertFalse(self.guard.request_close())
            self.assertEqual(thread.call_count, 2)
        self.window.destroy.assert_not_called()

    def test_webview_failure_does_not_discard_the_note(self) -> None:
        self.window.evaluate_js.side_effect = RuntimeError("WebView unavailable")
        self.guard._pending = True
        self.guard._save()
        self.assertFalse(self.guard._pending)
        self.window.destroy.assert_not_called()

    def test_window_can_close_before_frontend_is_loaded(self) -> None:
        self.window.events.loaded.is_set.return_value = False
        self.assertTrue(self.guard.request_close())
        self.window.evaluate_js.assert_not_called()


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

    def test_persisted_desktop_config_selects_data_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "desktop.json"
            configured = Path(temp_dir) / "library"
            config_file.write_text(
                json.dumps({"data_dir": str(configured)}), encoding="utf-8"
            )
            platform = get_desktop_platform(
                "win32",
                {"LOCALAPPDATA": str(Path(temp_dir) / "app-data")},
                config_file=config_file,
            )
            self.assertEqual(platform.data_dir, configured.resolve())

    def test_environment_override_wins_over_desktop_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "desktop.json"
            configured = Path(temp_dir) / "configured"
            override = Path(temp_dir) / "override"
            config_file.write_text(
                json.dumps({"data_dir": str(configured)}), encoding="utf-8"
            )
            actual = configured_data_dir(
                "win32",
                {
                    "LOCALAPPDATA": str(Path(temp_dir) / "app-data"),
                    "PAPER_VAULT_DATA_DIR": str(override),
                },
                config_file=config_file,
            )
            self.assertEqual(actual, override.resolve())

    def test_invalid_desktop_config_falls_back_to_platform_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "desktop.json"
            config_file.write_text('{"data_dir": "relative-data"}', encoding="utf-8")
            environment = {"LOCALAPPDATA": str(Path(temp_dir) / "app-data")}
            actual = configured_data_dir(
                "win32", environment, config_file=config_file
            )
            self.assertEqual(actual, desktop_config_path("win32", environment).parent)

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
            self.assertEqual(payload, {"status": "ok", "version": __version__})
            self.assertEqual(
                runtime.server.model_directory,
                Path(temp_dir).resolve() / "models" / "translate-en_zh-1_9",
            )
            self.assertFalse(runtime.thread.is_alive())

    def test_desktop_bridge_exposes_platform_without_secrets(self) -> None:
        platform = get_desktop_platform(
            "win32", {"LOCALAPPDATA": r"C:\Users\tester\AppData\Local"}
        )
        info = DesktopBridge(platform).app_info()
        self.assertEqual(info["platform"], "windows")
        self.assertEqual(info["version"], __version__)
        self.assertNotIn("api_key", info)


if __name__ == "__main__":
    unittest.main()
