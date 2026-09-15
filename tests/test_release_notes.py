from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.release_notes import extract_release_notes, main


CHANGELOG = """# 版本记录

## [Unreleased]

- 未发布的功能。

## [1.10.0] - 2026-09-20

- 其他版本。

## [1.1.0] - 2026-09-11

本次更新支持备份。

### 新增

- 导出资源包。

## [1.0.0] - 2026-08-13

- 首次发布。

[Unreleased]: https://example.test/compare
[1.1.0]: https://example.test/v1.1.0
[1.0.0]: https://example.test/v1.0.0
"""


class ReleaseNotesTestCase(unittest.TestCase):
    def test_extracts_exact_tag_without_other_versions(self) -> None:
        self.assertEqual(
            extract_release_notes(CHANGELOG, "v1.1.0"),
            "本次更新支持备份。\n\n### 新增\n\n- 导出资源包。\n",
        )

    def test_last_release_excludes_global_link_definitions(self) -> None:
        self.assertEqual(extract_release_notes(CHANGELOG, "1.0.0"), "- 首次发布。\n")

    def test_missing_empty_and_unreleased_entries_fail(self) -> None:
        for changelog, version in (
            (CHANGELOG, "v9.0.0"),
            ("## [1.1.0]\n\n## [1.0.0]\n- Previous.\n", "v1.1.0"),
            (CHANGELOG, "Unreleased"),
        ):
            with self.subTest(version=version):
                with self.assertRaises(ValueError):
                    extract_release_notes(changelog, version)

    def test_cli_writes_utf8_release_body(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            changelog = root / "CHANGELOG.md"
            output = root / "release-notes.md"
            changelog.write_text(CHANGELOG, encoding="utf-8-sig")
            self.assertEqual(
                main(["--version", "v1.1.0", "--changelog", str(changelog), "--output", str(output)]),
                0,
            )
            self.assertEqual(output.read_text(encoding="utf-8"), extract_release_notes(CHANGELOG, "v1.1.0"))


if __name__ == "__main__":
    unittest.main()
