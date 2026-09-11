from __future__ import annotations

import unittest
import tempfile
import uuid
import http.client
import json
import threading
from pathlib import Path
from unittest.mock import patch

from backend.database import Database, utc_now
from backend.app import PaperVaultServer, load_local_config, retryable_external_translation_error
from backend.doubao import (
    extract_markdown,
    extract_share_title,
    markdown_blocks,
    normalize_markdown,
    translate_markdown_to_english,
    validate_doubao_url,
)
from backend.llm import SummaryError


class DoubaoImportTestCase(unittest.TestCase):
    def test_url_is_restricted_to_public_thread_paths(self) -> None:
        self.assertEqual(
            validate_doubao_url("https://www.doubao.com/thread/demo_123"),
            "https://www.doubao.com/thread/demo_123",
        )
        for value in ("http://www.doubao.com/thread/demo", "https://example.com/thread/demo", "https://www.doubao.com/api/demo"):
            with self.assertRaises(ValueError):
                validate_doubao_url(value)

    def test_extracts_escaped_markdown_without_mojibake(self) -> None:
        markdown = "# 论文《Demo》详细总结\n\n## 方法\n设置 $K_C$ 个候选专家。\n\n- 保留表格\n\n" + ("这是用于验证导入结构的完整研究总结内容。" * 12)
        escaped = markdown.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        page = 'text_block\\\\\\":{\\\\\\"text\\\\\\":\\\\\\"' + escaped + '\\\\\\\",\\\\\\"icon_url\\\\\\":\\\\\\"\\\\\\"}'
        extracted = extract_markdown(page)
        self.assertEqual(extracted, markdown)
        self.assertEqual(extract_share_title(extracted), "Demo")
        self.assertIn("$K_C$", extracted)

    def test_markdown_blocks_keep_structure(self) -> None:
        blocks = markdown_blocks("# Demo\n\n## 方法\n说明。\n\n1. 第一项\n2. 第二项\n\n> 结论")
        self.assertEqual([block["type"] for block in blocks], ["heading", "heading", "paragraph", "bullet", "bullet", "quote"])
        self.assertEqual(blocks[3]["text"], "第一项")

    def test_normalize_markdown_trims_legacy_embedded_page_data(self) -> None:
        dirty = "# Report\n\n正文。\\\"}},\\\"is_finish\\\":true}]\",\"content_status\":0"
        self.assertEqual(normalize_markdown(dirty), "# Report\n\n正文。")

    def test_normalize_markdown_restores_line_breaks_but_keeps_latex_commands(self) -> None:
        value = "# Report\\nRADA improves recall. Formula: $a \\neq b$."
        self.assertEqual(
            normalize_markdown(value),
            "# Report\nRADA improves recall. Formula: $a \\neq b$.",
        )

    def test_english_translation_allows_fenced_json_and_has_sufficient_output_budget(self) -> None:
        source = "# 论文解析\n\n" + ("这是详细内容。" * 640)
        response = {
            "content": '```json\n{"markdown_en":"# Paper analysis\\n\\nDetailed content."}\n```',
            "finish_reason": "stop",
        }
        with patch("backend.doubao.request_chat_completion", return_value=response) as request:
            translated, model = translate_markdown_to_english(
                source,
                {"provider": "openai_compatible", "model": "demo-model"},
            )
        payload = request.call_args.args[0]
        self.assertEqual(translated, "# Paper analysis\n\nDetailed content.")
        self.assertEqual(model, "demo-model")
        self.assertGreaterEqual(payload["max_tokens"], 4096)
        self.assertGreater(payload["max_tokens"], len(source) // 2)

    def test_english_translation_reports_truncated_model_output(self) -> None:
        with patch(
            "backend.doubao.request_chat_completion",
            return_value={"content": '{"markdown_en":"# Partial', "finish_reason": "length"},
        ):
            with self.assertRaisesRegex(SummaryError, "output was truncated"):
                translate_markdown_to_english(
                    "# 论文解析\n\n正文",
                    {"provider": "openai_compatible", "model": "demo-model"},
                )

    def test_english_translation_accepts_direct_markdown_from_compatible_provider(self) -> None:
        with patch(
            "backend.doubao.request_chat_completion",
            return_value={"content": "# Paper analysis\n\nDirect Markdown.", "finish_reason": "stop"},
        ):
            translated, _ = translate_markdown_to_english(
                "# 论文解析\n\n正文",
                {"provider": "openai_compatible", "model": "demo-model"},
            )
        self.assertEqual(translated, "# Paper analysis\n\nDirect Markdown.")

    def test_external_translation_retries_only_transient_provider_failures(self) -> None:
        self.assertTrue(retryable_external_translation_error("LLM request failed (502): upstream"))
        self.assertTrue(retryable_external_translation_error("LLM request failed: <urlopen error timed out>"))
        self.assertFalse(retryable_external_translation_error("LLM request failed (401): invalid token"))
        self.assertFalse(retryable_external_translation_error("Doubao English model returned invalid JSON"))

    def test_summary_variant_and_note_quote_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "paper.db")
            database.initialize()
            paper_id = str(uuid.uuid4())
            now = utc_now()
            database.insert_paper(
                {
                    "id": paper_id,
                    "title": "Round Trip",
                    "original_filename": "round-trip.pdf",
                    "stored_filename": f"{paper_id}.pdf",
                    "created_at": now,
                    "updated_at": now,
                },
                [],
            )
            variant = database.create_summary_variant({
                "id": str(uuid.uuid4()),
                "paper_id": paper_id,
                "provider": "doubao",
                "content_markdown": "# Report",
                "content_blocks": [{"type": "heading", "text": "Report"}],
            })
            self.assertEqual(variant["content_blocks"][0]["text"], "Report")
            note = database.create_note_quote(paper_id, {
                "source_type": "doubao_zh",
                "source_variant_id": variant["id"],
                "source_locator": {"text": "Report"},
                "quote_text": "Report",
            })
            self.assertEqual(note["quotes"][0]["source_variant_id"], variant["id"])

    def test_local_config_parser_maps_env_keys_without_exposing_unknown_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".env.local").write_text(
                "PAPER_VAULT_BASE_URL=https://example.test/v1\n"
                "PAPER_VAULT_API_KEY='secret-token'\n"
                "PAPER_VAULT_MODEL=demo-model\n"
                "IGNORED_VALUE=should-not-load\n",
                encoding="utf-8",
            )
            values = load_local_config(project)
            self.assertEqual(values["provider"], "openai_compatible")
            self.assertEqual(values["base_url"], "https://example.test/v1")
            self.assertEqual(values["api_key"], "secret-token")
            self.assertNotIn("ignored_value", values)

    def test_import_returns_after_chinese_save_without_waiting_for_english(self) -> None:
        project_dir = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as directory:
            server = PaperVaultServer(("127.0.0.1", 0), project_dir / "frontend", Path(directory))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                paper_id = str(uuid.uuid4())
                now = utc_now()
                server.db.insert_paper(
                    {
                        "id": paper_id,
                        "title": "Import Test",
                        "original_filename": "import-test.pdf",
                        "stored_filename": f"{paper_id}.pdf",
                        "created_at": now,
                        "updated_at": now,
                    },
                    [],
                )
                body = json.dumps({"url": "https://www.doubao.com/thread/demo", "generate_english": True}).encode("utf-8")
                with patch(
                    "backend.app.fetch_doubao_markdown",
                    return_value={
                        "title": "Import Test",
                        "markdown": "# Import Test\n\n## 方法\n中文内容。",
                        "url": "https://www.doubao.com/thread/demo",
                    },
                ), patch(
                    "backend.app.translate_markdown_to_english",
                    side_effect=AssertionError("English generation must run in the second request"),
                ):
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
                    connection.request(
                        "POST",
                        f"/api/papers/{paper_id}/summary-variants",
                        body=body,
                        headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
                    )
                    response = connection.getresponse()
                    payload = json.loads(response.read().decode("utf-8"))
                    connection.close()
                self.assertEqual(response.status, 201)
                self.assertEqual(payload["phase"], "chinese_complete")
                self.assertEqual(payload["variant"]["english_status"], "pending")
                self.assertIn("中文内容", payload["variant"]["content_markdown"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)
