from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from backend.deep_summary import (
    build_summary_input_plan,
    generate_english_report,
    normalize_report,
    prepare_page_documents,
    translate_report_blocks,
)


def settings(**overrides: str) -> dict[str, str]:
    values = {
        "provider": "openai_compatible",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
        "analysis_model": "deepseek-v4-flash",
        "translation_model": "deepseek-v4-flash",
        "context_window_tokens": "",
        "analysis_reasoning_effort": "high",
    }
    values.update(overrides)
    return values


def completion(content: str | dict, finish_reason: str = "stop") -> dict:
    return {
        "content": json.dumps(content, ensure_ascii=False) if isinstance(content, dict) else content,
        "usage": {"total_tokens": 123},
        "finish_reason": finish_reason,
    }


class DeepSummaryInputTestCase(unittest.TestCase):
    def test_full_input_keeps_content_after_sixty_thousand_characters(self) -> None:
        late_evidence = "LATE-PAPER-ABLATION-RESULT"
        pages = [
            {"page": 1, "text": "Introduction\n" + "A" * 65000},
            {"page": 2, "text": f"Ablation Study\n{late_evidence}"},
        ]
        plan = build_summary_input_plan(pages, settings())
        self.assertEqual(plan["mode"], "full")
        self.assertIn("[Page 1]", plan["full_text"])
        self.assertIn("[Page 2]", plan["full_text"])
        self.assertIn(late_evidence, plan["full_text"])

    def test_chunked_input_preserves_page_labels_and_sentence_boundaries(self) -> None:
        marker = "This complete sentence must remain intact at the page boundary."
        pages = [
            {"page": page, "text": (f"Section {page}. " + "Evidence sentence. " * 800 + marker)}
            for page in range(1, 6)
        ]
        plan = build_summary_input_plan(
            pages,
            settings(model="other-model", analysis_model="other-model", context_window_tokens="16000"),
        )
        self.assertEqual(plan["mode"], "chunked")
        self.assertGreater(len(plan["chunks"]), 1)
        self.assertTrue(all("[Page " in chunk for chunk in plan["chunks"]))
        self.assertTrue(any(marker in chunk for chunk in plan["chunks"]))

    def test_repeated_headers_footers_and_reference_entries_are_filtered(self) -> None:
        pages = [
            {
                "page": page,
                "text": f"Conference Header\nBody evidence on page {page}.\nReferences\n[1] Noisy citation 2020\n{page}",
            }
            for page in range(1, 6)
        ]
        cleaned = prepare_page_documents(pages)
        combined = "\n".join(page["text"] for page in cleaned)
        self.assertNotIn("Conference Header", combined)
        self.assertNotIn("Noisy citation", combined)
        self.assertIn("Body evidence on page 5", combined)


class DeepSummaryStructureTestCase(unittest.TestCase):
    def test_report_uses_natural_block_types_without_fixed_counts(self) -> None:
        report = normalize_report(
            {
                "paper_title": "Original Title",
                "blocks": [
                    {"id": "custom", "type": "heading", "level": 2, "text_en": "Problem", "page_refs": []},
                    {"id": "p", "type": "paragraph", "text_en": "A coherent explanation.", "page_refs": [1]},
                    {"id": "h", "type": "heading", "level": 3, "text_en": "Actual Module", "page_refs": [2]},
                    {"id": "b", "type": "bullet", "text_en": "One parallel configuration.", "page_refs": [2]},
                    {"id": "p", "type": "paragraph", "text_en": "The result meaning follows.", "page_refs": [3]},
                ],
            },
            {1, 2, 3},
            "Fallback",
        )
        self.assertEqual([block["type"] for block in report["blocks"]], ["heading", "paragraph", "heading", "bullet", "paragraph"])
        ids = [block["id"] for block in report["blocks"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(block["page_refs"] for block in report["blocks"]))
        self.assertEqual(report["blocks"][2]["level"], 3)

    def test_truncated_english_output_continues_and_merges_complete_blocks(self) -> None:
        truncated = (
            '{"paper_title":"Original","blocks":['
            '{"id":"heading-001","type":"heading","level":2,'
            '"text_en":"Method","page_refs":[1]},'
        )
        continuation = {
            "paper_title": "Original",
            "blocks": [
                {
                    "id": "paragraph-001",
                    "type": "paragraph",
                    "text_en": "The method follows the stated architecture.",
                    "page_refs": [1],
                }
            ],
        }
        with patch(
            "backend.deep_summary.request_chat_completion",
            side_effect=[completion(truncated, "length"), completion(continuation)],
        ) as request:
            report, metadata = generate_english_report(
                "Original", [{"page": 1, "text": "Method\nArchitecture evidence."}], settings()
            )
        self.assertEqual([block["type"] for block in report["blocks"]], ["heading", "paragraph"])
        self.assertEqual(metadata["input_mode"], "full")
        self.assertEqual(request.call_count, 2)
        self.assertTrue(all(call.kwargs["task"] == "analysis" for call in request.call_args_list))


class DeepSummaryTranslationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.blocks = [
            {
                "id": "heading-001",
                "type": "heading",
                "level": 2,
                "text_en": "Evaluation Results",
                "page_refs": [4],
            },
            {
                "id": "paragraph-001",
                "type": "paragraph",
                "text_en": "WER improves from 12.5% to 9.1%.",
                "page_refs": [4],
            },
        ]

    def test_translation_retries_id_mismatch_and_merges_by_id(self) -> None:
        wrong = {"translations": [{"id": "wrong", "text_zh": "错误"}]}
        correct = {
            "translations": [
                {"id": "heading-001", "text_zh": "评测结果"},
                {"id": "paragraph-001", "text_zh": "WER 从 12.5% 改善到 9.1%。"},
            ]
        }
        captured_payloads: list[dict] = []

        def fake_request(payload: dict, _settings: dict, **kwargs: object) -> dict:
            captured_payloads.append(payload)
            return completion(wrong if len(captured_payloads) == 1 else correct)

        with patch("backend.deep_summary.request_chat_completion", side_effect=fake_request) as request:
            merged, metadata = translate_report_blocks(self.blocks, settings())
        self.assertEqual([block["id"] for block in merged], ["heading-001", "paragraph-001"])
        self.assertEqual(merged[1]["text_zh"], "WER 从 12.5% 改善到 9.1%。")
        self.assertEqual(metadata["model"], "deepseek-v4-flash")
        self.assertEqual(request.call_count, 2)
        self.assertTrue(all(call.kwargs["task"] == "translation" for call in request.call_args_list))
        serialized = json.dumps(captured_payloads, ensure_ascii=False)
        self.assertNotIn("input_image", serialized)
        self.assertNotIn('"file"', serialized)

    def test_translation_numeric_mismatch_never_changes_english_blocks(self) -> None:
        wrong_numbers = {
            "translations": [
                {"id": "heading-001", "text_zh": "评测结果"},
                {"id": "paragraph-001", "text_zh": "WER 从 12.5% 改善到 8.0%。"},
            ]
        }
        with patch(
            "backend.deep_summary.request_chat_completion",
            return_value=completion(wrong_numbers),
        ):
            with self.assertRaisesRegex(Exception, "numeric content"):
                translate_report_blocks(self.blocks, settings())
        self.assertNotIn("text_zh", self.blocks[1])


if __name__ == "__main__":
    unittest.main()
