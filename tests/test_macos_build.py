from __future__ import annotations

from pathlib import Path
import re
import unittest
from unittest.mock import patch


class MacOSBuildPreflightTestCase(unittest.TestCase):
    """Run the Python preflight embedded in the actual macOS build script."""

    @classmethod
    def setUpClass(cls) -> None:
        script = (Path(__file__).resolve().parents[1] / "build-macos.sh").read_text(encoding="utf-8")
        match = re.search(r'^"\$python_command" - <<\'PY\'\n(.*?)\nPY$', script, re.MULTILINE | re.DOTALL)
        if match is None:
            raise AssertionError("macOS build script is missing its Python preflight")
        cls.preflight = compile(match.group(1), "build-macos.sh:preflight", "exec")

    def test_supported_python_continues_build(self) -> None:
        for version in ((3, 10), (3, 12), (3, 14)):
            with self.subTest(version=version), patch("sys.version_info", version), patch("platform.machine", return_value="arm64"):
                exec(self.preflight, {})

    def test_old_python_stops_with_clear_error(self) -> None:
        with patch("sys.version_info", (3, 9)), patch("platform.machine", return_value="arm64"):
            with self.assertRaisesRegex(SystemExit, "Python 3.10 or newer"):
                exec(self.preflight, {})

    def test_rosetta_python_stops_before_installing_or_building(self) -> None:
        with patch("sys.version_info", (3, 12)), patch("platform.machine", return_value="x86_64"):
            with self.assertRaisesRegex(SystemExit, "arm64 Python"):
                exec(self.preflight, {})


if __name__ == "__main__":
    unittest.main()
