from __future__ import annotations

import ast
from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch

from backend import __version__
from tools.desktop_version import (
    DesktopVersion,
    check_tag,
    main,
    read_version,
    windows_resource,
)


class DesktopVersionTestCase(unittest.TestCase):
    def test_version_source_is_backend_literal(self) -> None:
        self.assertEqual(read_version().text, __version__)
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "__init__.py"
            source.write_text('__version__ = "2.3.4"\nraise RuntimeError("must not run")\n', encoding="utf-8")
            self.assertEqual(read_version(source).text, "2.3.4")

    def test_formal_and_prerelease_macos_metadata(self) -> None:
        for version, build in (
            ("1.2.0", "1.2.0"),
            ("1.2.0-alpha.1", "1.2.0a1"),
            ("1.2.0-beta.2", "1.2.0b2"),
            ("1.2.0-rc.1", "1.2.0fc1"),
        ):
            with self.subTest(version=version):
                parsed = DesktopVersion.parse(version)
                metadata = plistlib.loads(plistlib.dumps(parsed.macos_plist()))
                self.assertEqual(metadata["CFBundleShortVersionString"], "1.2.0")
                self.assertEqual(metadata["CFBundleVersion"], build)
                self.assertEqual(metadata["CFBundleGetInfoString"], f"PaperVault {version}")
                self.assertEqual(parsed.prerelease, "-" in version)

    def test_windows_resource_preserves_prerelease_string_and_flag(self) -> None:
        for version, flags in (("1.2.0", 0), ("1.2.0-rc.1", 2)):
            with self.subTest(version=version):
                tree = ast.parse(windows_resource(DesktopVersion.parse(version)))
                calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
                fixed = next(node for node in calls if isinstance(node.func, ast.Name) and node.func.id == "FixedFileInfo")
                values = {item.arg: ast.literal_eval(item.value) for item in fixed.keywords}
                self.assertEqual(values["filevers"], (1, 2, 0, 0))
                self.assertEqual(values["prodvers"], (1, 2, 0, 0))
                self.assertEqual(values["flags"], flags)
                strings = {
                    ast.literal_eval(node.args[0]): ast.literal_eval(node.args[1])
                    for node in calls
                    if isinstance(node.func, ast.Name) and node.func.id == "StringStruct"
                }
                self.assertEqual(strings["FileVersion"], version)
                self.assertEqual(strings["ProductVersion"], version)

    def test_rejects_invalid_or_unrepresentable_versions(self) -> None:
        for value in ("v1.2.0", "1.2", "1.02.0", "1.2.0-rc.0", "1.2.0-rc.01", "1.2.0-preview.1", "65536.0.0"):
            with self.subTest(version=value), self.assertRaises(ValueError):
                DesktopVersion.parse(value)
        for value in ("10000.0.0", "1.100.0", "1.2.100", "1.2.0-rc.256"):
            with self.subTest(version=value), self.assertRaises(ValueError):
                DesktopVersion.parse(value).macos_plist()

    def test_tag_requires_exact_version_and_prerelease(self) -> None:
        version = DesktopVersion.parse("1.2.0-rc.1")
        check_tag(version, "v1.2.0-rc.1")
        for tag in ("v1.2.0", "v1.2.0-rc.2", "1.2.0-rc.1", "vv1.2.0-rc.1"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                check_tag(version, tag)
        check_tag(DesktopVersion.parse("1.2.0"), "v1.2.0")

    def test_cli_outputs_metadata_and_ci_release_type(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            resource = root / "build" / "windows-version.txt"
            github_output = root / "github-output"
            github_output.write_text("existing=value\n", encoding="utf-8")
            for value, expected in (("1.2.0-rc.1", "true"), ("1.2.0", "false")):
                with self.subTest(version=value), patch("tools.desktop_version.read_version", return_value=DesktopVersion.parse(value)), redirect_stdout(io.StringIO()):
                    self.assertEqual(main([
                        "--check-tag", f"v{value}",
                        "--windows-output", str(resource),
                        "--github-output", str(github_output),
                    ]), 0)
                    self.assertIn(f"u'{value}'", resource.read_text(encoding="utf-8"))
                    output = github_output.read_text(encoding="utf-8")
                    self.assertTrue(output.startswith("existing=value\n"))
                    self.assertTrue(output.endswith(f"version={value}\nprerelease={expected}\n"))

    def test_mismatched_tag_does_not_write_build_or_ci_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            resource = root / "windows-version.txt"
            github_output = root / "github-output"
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                main(["--check-tag", "v0.0.0", "--windows-output", str(resource), "--github-output", str(github_output)])
            self.assertEqual(error.exception.code, 2)
            self.assertFalse(resource.exists())
            self.assertFalse(github_output.exists())


if __name__ == "__main__":
    unittest.main()
