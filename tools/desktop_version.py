"""Derive desktop package metadata and release checks from backend/__init__.py."""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Sequence


PROJECT_DIR = Path(__file__).resolve().parent.parent
_VERSION = re.compile(
    r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-(alpha|beta|rc)\.([1-9]\d*))?"
)


@dataclass(frozen=True)
class DesktopVersion:
    text: str
    major: int
    minor: int
    patch: int
    stage: str | None = None
    iteration: int | None = None

    @classmethod
    def parse(cls, value: str) -> DesktopVersion:
        match = _VERSION.fullmatch(value)
        if not match:
            raise ValueError(
                f"Unsupported desktop version: {value!r}; use X.Y.Z or X.Y.Z-rc.N "
                "(alpha and beta are also supported)"
            )
        major, minor, patch = (int(part) for part in match.group(1, 2, 3))
        if max(major, minor, patch) > 65535:
            raise ValueError("Windows version components must not exceed 65535")
        iteration = int(match.group(5)) if match.group(5) else None
        return cls(value, major, minor, patch, match.group(4), iteration)

    @property
    def prerelease(self) -> bool:
        return self.stage is not None

    @property
    def numeric(self) -> tuple[int, int, int, int]:
        # Windows fixed metadata only accepts integers. The string metadata and
        # VS_FF_PRERELEASE flag preserve the complete prerelease identity.
        return self.major, self.minor, self.patch, 0

    def macos_plist(self) -> dict[str, str]:
        if self.major > 9999 or self.minor > 99 or self.patch > 99:
            raise ValueError("macOS bundle version components must fit 9999.99.99")
        if self.iteration is not None and self.iteration > 255:
            raise ValueError("macOS prerelease iteration must be between 1 and 255")
        core = f"{self.major}.{self.minor}.{self.patch}"
        suffix = {"alpha": "a", "beta": "b", "rc": "fc"}.get(self.stage, "")
        build = f"{core}{suffix}{self.iteration}" if self.prerelease else core
        return {
            "CFBundleShortVersionString": core,
            "CFBundleVersion": build,
            "CFBundleGetInfoString": f"PaperVault {self.text}",
        }


def read_version(source: Path = PROJECT_DIR / "backend" / "__init__.py") -> DesktopVersion:
    # Read the literal without importing application code or its dependencies.
    module = ast.parse(source.read_text(encoding="utf-8-sig"), filename=str(source))
    for statement in module.body:
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in statement.targets
        ):
            value = ast.literal_eval(statement.value)
            if not isinstance(value, str):
                raise ValueError("backend.__version__ must be a string literal")
            return DesktopVersion.parse(value)
    raise ValueError(f"Missing __version__ in {source}")


def check_tag(version: DesktopVersion, tag: str) -> None:
    expected = f"v{version.text}"
    if tag != expected:
        raise ValueError(f"Release tag {tag!r} does not match backend.__version__: expected {expected!r}")


def windows_resource(version: DesktopVersion) -> str:
    template = (PROJECT_DIR / "packaging" / "windows-version.template").read_text(encoding="utf-8")
    return template.format(
        version=version.text,
        numeric_version=version.numeric,
        flags="0x2" if version.prerelease else "0x0",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-tag", help="Require this exact v-prefixed release tag")
    parser.add_argument("--windows-output", type=Path, help="Write PyInstaller's Windows version resource")
    parser.add_argument("--github-output", type=Path, help="Append version and prerelease to GITHUB_OUTPUT")
    args = parser.parse_args(argv)
    try:
        version = read_version()
        if args.check_tag is not None:
            check_tag(version, args.check_tag)
        # Validate both platform formats before producing a release or package.
        version.macos_plist()
        if args.windows_output:
            args.windows_output.parent.mkdir(parents=True, exist_ok=True)
            args.windows_output.write_text(windows_resource(version), encoding="utf-8", newline="\n")
        if args.github_output:
            with args.github_output.open("a", encoding="utf-8", newline="\n") as output:
                output.write(f"version={version.text}\nprerelease={str(version.prerelease).lower()}\n")
    except (OSError, SyntaxError, ValueError) as error:
        parser.error(str(error))
    print(version.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
