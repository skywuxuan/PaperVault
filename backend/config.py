from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Mapping, MutableMapping


DATA_DIR_ENV = "PAPER_VAULT_DATA_DIR"
ENV_FILE_ENV = "PAPER_VAULT_ENV_FILE"
SETTING_ENV_VARS = {
    "provider": "PAPER_VAULT_PROVIDER",
    "base_url": "PAPER_VAULT_BASE_URL",
    "api_key": "PAPER_VAULT_API_KEY",
    "model": "PAPER_VAULT_MODEL",
    "analysis_model": "PAPER_VAULT_ANALYSIS_MODEL",
    "translation_model": "PAPER_VAULT_TRANSLATION_MODEL",
    "context_window_tokens": "PAPER_VAULT_CONTEXT_WINDOW_TOKENS",
    "analysis_reasoning_effort": "PAPER_VAULT_ANALYSIS_REASONING_EFFORT",
}
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _parse_value(raw_value: str) -> str:
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1]
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return (
            value[1:-1]
            .replace(r"\n", "\n")
            .replace(r"\r", "\r")
            .replace(r"\t", "\t")
            .replace(r'\"', '"')
            .replace(r"\\", "\\")
        )
    return re.split(r"\s+#", value, maxsplit=1)[0].rstrip()


def read_environment_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        name, raw_value = line.split("=", 1)
        name = name.strip()
        if _ENV_NAME_RE.fullmatch(name):
            values[name] = _parse_value(raw_value)
    return values


def _environment_file_candidates(root: Path, environment: Mapping[str, str]) -> list[Path]:
    explicit = environment.get(ENV_FILE_ENV, "").strip()
    if explicit:
        return [Path(os.path.expandvars(explicit)).expanduser().resolve()]

    candidates = [root / ".env.local"]
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / ".env.local")
    candidates.append(Path.cwd() / ".env.local")
    return list(dict.fromkeys(path.resolve() for path in candidates))


def load_local_environment(
    root: Path,
    environ: MutableMapping[str, str] | None = None,
) -> Path | None:
    environment = os.environ if environ is None else environ
    for path in _environment_file_candidates(root.resolve(), environment):
        if not path.is_file():
            continue
        for name, value in read_environment_file(path).items():
            if name == DATA_DIR_ENV and value and not Path(value).expanduser().is_absolute():
                value = str((path.parent / value).expanduser().resolve())
            environment.setdefault(name, value)
        return path
    return None


def apply_environment_settings(
    settings: Mapping[str, str],
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    environment = os.environ if environ is None else environ
    effective = dict(settings)
    endpoint_configured = False
    for setting_name, environment_name in SETTING_ENV_VARS.items():
        value = environment.get(environment_name, "").strip()
        if not value:
            continue
        effective[setting_name] = value
        if setting_name in {"base_url", "model"}:
            endpoint_configured = True

    if not environment.get(SETTING_ENV_VARS["provider"], "").strip() and endpoint_configured:
        effective["provider"] = "openai_compatible"
    return effective
