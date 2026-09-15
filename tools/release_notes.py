"""Extract one tagged release from CHANGELOG.md for GitHub Releases."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Sequence


_RELEASE_HEADING = re.compile(r"^##\s+\[([^\]]+)\](?:\s.*)?$")
_REFERENCE_LINK = re.compile(r"^\[[^\]]+\]:\s+\S+")


def extract_release_notes(changelog: str, version: str) -> str:
    normalized_version = version.removeprefix("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.+-]+)?", normalized_version):
        raise ValueError(f"Invalid release version: {version}")

    selected = False
    content: list[str] = []
    for line in changelog.splitlines():
        heading = _RELEASE_HEADING.fullmatch(line)
        if heading and heading.group(1) == normalized_version:
            selected = True
            continue
        if selected:
            if line.startswith("## "):
                break
            if not _REFERENCE_LINK.match(line):
                content.append(line)

    notes = "\n".join(content).strip()
    if not selected:
        raise ValueError(f"CHANGELOG.md has no entry for {version}")
    if not notes:
        raise ValueError(f"CHANGELOG.md entry for {version} is empty")
    return notes + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="Release version or tag, e.g. v1.1.0")
    parser.add_argument("--changelog", type=Path, default=Path("CHANGELOG.md"))
    parser.add_argument("--output", type=Path, help="Write the notes to this file instead of stdout")
    args = parser.parse_args(argv)
    try:
        notes = extract_release_notes(args.changelog.read_text(encoding="utf-8-sig"), args.version)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    if args.output:
        args.output.write_text(notes, encoding="utf-8", newline="\n")
    else:
        print(notes, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
