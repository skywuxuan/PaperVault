from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.config import (
    apply_environment_settings,
    load_local_environment,
    read_environment_file,
)


class EnvironmentConfigurationTestCase(unittest.TestCase):
    def test_environment_file_parses_quotes_export_and_comments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".env.local"
            path.write_text(
                "export PAPER_VAULT_BASE_URL='https://example.test/v1'\n"
                'PAPER_VAULT_MODEL="model-name"\n'
                "PAPER_VAULT_API_KEY=secret-value # local only\n",
                encoding="utf-8",
            )
            values = read_environment_file(path)
        self.assertEqual(values["PAPER_VAULT_BASE_URL"], "https://example.test/v1")
        self.assertEqual(values["PAPER_VAULT_MODEL"], "model-name")
        self.assertEqual(values["PAPER_VAULT_API_KEY"], "secret-value")

    def test_loading_does_not_override_shell_and_resolves_relative_data_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".env.local").write_text(
                "PAPER_VAULT_API_KEY=file-secret\n"
                "PAPER_VAULT_DATA_DIR=../library\n",
                encoding="utf-8",
            )
            environment = {"PAPER_VAULT_API_KEY": "shell-secret"}
            loaded = load_local_environment(root, environment)
        self.assertEqual(loaded, root / ".env.local")
        self.assertEqual(environment["PAPER_VAULT_API_KEY"], "shell-secret")
        self.assertEqual(
            Path(environment["PAPER_VAULT_DATA_DIR"]),
            (root.parent / "library").resolve(),
        )

    def test_llm_environment_values_override_database_settings(self) -> None:
        effective = apply_environment_settings(
            {"provider": "local", "base_url": "https://old.test/v1", "model": "old"},
            {
                "PAPER_VAULT_BASE_URL": "https://new.test/v1",
                "PAPER_VAULT_API_KEY": "secret",
                "PAPER_VAULT_MODEL": "new-model",
            },
        )
        self.assertEqual(effective["provider"], "openai_compatible")
        self.assertEqual(effective["base_url"], "https://new.test/v1")
        self.assertEqual(effective["api_key"], "secret")
        self.assertEqual(effective["model"], "new-model")


if __name__ == "__main__":
    unittest.main()
