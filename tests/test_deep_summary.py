from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from backend.deep_summary import (
    ENGLISH_REPORT_PROMPT,
    NUMERIC_TOKEN_RE,
    TRANSLATION_PROMPT,
    build_summary_input_plan,
    generate_english_report,
    normalize_report,
    prepare_page_documents,
    translate_report_blocks,
)
from backend.llm import SummaryError


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


class DeepSummaryPromptIntentTestCase(unittest.TestCase):
    def test_report_prompt_requires_claim_centered_synthesis(self) -> None:
        self.assertIn("researcher-first synthesis", ENGLISH_REPORT_PROMPT)
        self.assertIn("executive synthesis", ENGLISH_REPORT_PROMPT)
        self.assertIn("what the result demonstrates", ENGLISH_REPORT_PROMPT)
        self.assertIn("Do not produce a section-by-section retelling", ENGLISH_REPORT_PROMPT)
        self.assertNotIn("Follow the paper's own narrative and technical order", ENGLISH_REPORT_PROMPT)

    def test_translation_prompt_allows_natural_rewriting_with_alignment(self) -> None:
        self.assertIn("freely reorder", TRANSLATION_PROMPT)
        self.assertIn("sentences within a block", TRANSLATION_PROMPT)
        self.assertIn("Do not copy English syntax", TRANSLATION_PROMPT)
        self.assertIn("Preserve the supplied block IDs and order", TRANSLATION_PROMPT)


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

    def test_report_rejects_empty_headings(self) -> None:
        with self.assertRaisesRegex(SummaryError, "consecutive headings"):
            normalize_report(
                {
                    "paper_title": "Original Title",
                    "blocks": [
                        {"type": "heading", "level": 2, "text_en": "Training", "page_refs": [3]},
                        {"type": "heading", "level": 2, "text_en": "Datasets", "page_refs": [4]},
                        {"type": "heading", "level": 2, "text_en": "Metrics", "page_refs": [4]},
                        {"type": "paragraph", "text_en": "Dataset evidence.", "page_refs": [4]},
                    ],
                },
                {3, 4},
                "Fallback",
            )

    def test_report_allows_parent_and_child_headings(self) -> None:
        report = normalize_report(
            {
                "paper_title": "Original Title",
                "blocks": [
                    {"type": "heading", "level": 2, "text_en": "Training", "page_refs": [3]},
                    {"type": "heading", "level": 3, "text_en": "Datasets", "page_refs": [4]},
                    {"type": "paragraph", "text_en": "Dataset evidence.", "page_refs": [4]},
                ],
            },
            {3, 4},
            "Fallback",
        )
        self.assertEqual(len(report["blocks"]), 3)

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

    def test_transient_provider_error_is_retried_before_failing_report(self) -> None:
        valid = {
            "paper_title": "Original",
            "blocks": [
                {
                    "id": "paragraph-001",
                    "type": "paragraph",
                    "text_en": "Page-grounded evidence.",
                    "page_refs": [1],
                }
            ],
        }
        with patch(
            "backend.deep_summary.request_chat_completion",
            side_effect=[SummaryError("LLM request failed (503): busy"), completion(valid)],
        ) as request, patch("backend.deep_summary.time.sleep") as sleep:
            report, _ = generate_english_report(
                "Original", [{"page": 1, "text": "Evidence."}], settings()
            )
        self.assertEqual(report["blocks"][0]["page_refs"], [1])
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once()

    def test_authentication_error_is_not_retried(self) -> None:
        with patch(
            "backend.deep_summary.request_chat_completion",
            side_effect=SummaryError("LLM request failed (401): unauthorized"),
        ) as request, patch("backend.deep_summary.time.sleep") as sleep:
            with self.assertRaises(SummaryError):
                generate_english_report(
                    "Original", [{"page": 1, "text": "Evidence."}], settings()
                )
        self.assertEqual(request.call_count, 1)
        sleep.assert_not_called()

    def test_invalid_page_grounding_regenerates_report_once(self) -> None:
        invalid = {
            "paper_title": "Original",
            "blocks": [
                {
                    "id": "paragraph-001",
                    "type": "paragraph",
                    "text_en": "Unsupported page.",
                    "page_refs": [99],
                }
            ],
        }
        valid = {
            "paper_title": "Original",
            "blocks": [
                {
                    "id": "paragraph-001",
                    "type": "paragraph",
                    "text_en": "Supported page.",
                    "page_refs": [1],
                }
            ],
        }
        with patch(
            "backend.deep_summary.request_chat_completion",
            side_effect=[completion(invalid), completion(valid)],
        ) as request:
            report, _ = generate_english_report(
                "Original", [{"page": 1, "text": "Evidence."}], settings()
            )
        self.assertEqual(report["blocks"][0]["text_en"], "Supported page.")
        self.assertEqual(request.call_count, 2)
        self.assertIn(
            "failed structural validation",
            request.call_args_list[1].args[0]["messages"][1]["content"],
        )


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

    def test_numeric_tokens_are_detected_next_to_chinese_text(self) -> None:
        self.assertEqual(
            NUMERIC_TOKEN_RE.findall("参数量为1.7B，得分从4.17提升到4.01。"),
            ["1.7B", "4.17", "4.01"],
        )
        self.assertEqual(NUMERIC_TOKEN_RE.findall("Qwen3.5 model"), [])

    def test_translation_can_reorder_complete_numeric_clauses(self) -> None:
        blocks = [
            {
                "id": "paragraph-001",
                "type": "paragraph",
                "text_en": "Method A scores 1.0, while Method B scores 2.0.",
                "page_refs": [4],
            }
        ]
        reordered = completion(
            {
                "translations": [
                    {
                        "id": "paragraph-001",
                        "text_zh": "方法 B 得分为2.0，而方法 A 得分为1.0。",
                    }
                ]
            }
        )
        with patch(
            "backend.deep_summary.request_chat_completion", return_value=reordered
        ) as request:
            merged, _ = translate_report_blocks(blocks, settings())
        self.assertEqual(request.call_count, 1)
        self.assertEqual(merged[0]["text_zh"], "方法 B 得分为2.0，而方法 A 得分为1.0。")

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

    def test_translation_request_includes_page_grounded_source_evidence(self) -> None:
        response = completion(
            {
                "translations": [
                    {"id": "heading-001", "text_zh": "评测结果"},
                    {"id": "paragraph-001", "text_zh": "WER 从 12.5% 改善到 9.1%。"},
                ]
            }
        )
        with patch("backend.deep_summary.request_chat_completion", return_value=response) as request:
            merged, metadata = translate_report_blocks(
                self.blocks,
                settings(),
                [{"page": 4, "text": "The paper reports the WER improvement after retrieval."}],
            )
        payload = json.loads(request.call_args.args[0]["messages"][1]["content"])
        self.assertEqual(payload["source_evidence"][0]["page"], 4)
        self.assertIn("WER improvement", payload["source_evidence"][0]["text"])
        self.assertTrue(metadata["source_grounded"])
        self.assertEqual(merged[1]["text_zh"], "WER 从 12.5% 改善到 9.1%。")

    def test_translation_numeric_mismatch_never_changes_english_blocks(self) -> None:
        wrong_numbers = {
            "translations": [
                {"id": "heading-001", "text_zh": "评测结果"},
                {"id": "paragraph-001", "text_zh": "WER 从 12.5% 改善到 8.0%。"},
            ]
        }
        def fake_request(payload: dict, _settings: dict, **_kwargs: object) -> dict:
            request_data = json.loads(payload["messages"][1]["content"].split("\n\n")[-1])
            requested = request_data["blocks"]
            if len(requested) == 1 and requested[0]["id"] == "heading-001":
                return completion(
                    {"translations": [{"id": "heading-001", "text_zh": "评测结果"}]}
                )
            if len(requested) == 1:
                return completion(
                    {
                        "translations": [
                            {"id": "paragraph-001", "text_zh": "WER 从 12.5% 改善到 8.0%。"}
                        ]
                    }
                )
            return completion(wrong_numbers)

        with patch("backend.deep_summary.request_chat_completion", side_effect=fake_request):
            with self.assertRaises(Exception):
                translate_report_blocks(self.blocks, settings())
        self.assertNotIn("text_zh", self.blocks[1])

    def test_translation_retries_only_numeric_mismatches_with_exact_feedback(self) -> None:
        wrong_numbers = {
            "translations": [
                {"id": "heading-001", "text_zh": "评测结果"},
                {"id": "paragraph-001", "text_zh": "WER 从 12.5% 改善到 8.0%。"},
            ]
        }
        corrected = {
            "translations": [
                {
                    "id": "paragraph-001",
                    "text_zh": "WER 最终为 <PVNUM_B/>，起始为 PVNUM_A。",
                }
            ]
        }
        with patch(
            "backend.deep_summary.request_chat_completion",
            side_effect=[completion(wrong_numbers), completion(corrected)],
        ) as request:
            merged, _ = translate_report_blocks(self.blocks, settings())

        self.assertEqual(request.call_count, 2)
        correction_prompt = request.call_args_list[1].args[0]["messages"][1]["content"]
        correction_payload = json.loads(correction_prompt.split("\n\n")[-1])
        self.assertEqual(
            [block["id"] for block in correction_payload["blocks"]], ["paragraph-001"]
        )
        self.assertEqual(
            correction_payload["blocks"][0]["text_en"],
            "WER improves from [[PVNUM_A]] to [[PVNUM_B]].",
        )
        self.assertIn("Copy every placeholder exactly once", correction_prompt)
        self.assertEqual(merged[0]["text_zh"], "评测结果")
        self.assertEqual(merged[1]["text_zh"], "WER 最终为 9.1%，起始为 12.5%。")

    def test_translation_retries_numeric_placeholder_left_in_final_text(self) -> None:
        initial = {
            "translations": [
                {"id": "heading-001", "text_zh": "评测结果"},
                {
                    "id": "paragraph-001",
                    "text_zh": "截至[[PVNUM_A]]年[[PVNUM_B]]月2026，结果稳定。",
                },
            ]
        }
        corrected = {
            "translations": [
                {
                    "id": "paragraph-001",
                    "text_zh": "截至[[PVNUM_A]]年二月，结果稳定。",
                }
            ]
        }
        blocks = [
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
                "text_en": "As of February 2026, results are stable.",
                "page_refs": [4],
            },
        ]
        with patch(
            "backend.deep_summary.request_chat_completion",
            side_effect=[completion(initial), completion(corrected)],
        ) as request:
            merged, _ = translate_report_blocks(blocks, settings())
        self.assertEqual(request.call_count, 2)
        self.assertEqual(merged[1]["text_zh"], "截至2026年二月，结果稳定。")

    def test_numeric_retry_can_translate_sentence_segments_into_one_block(self) -> None:
        blocks = [
            {
                "id": "paragraph-001",
                "type": "paragraph",
                "text_en": "First result is 1.0. Second result is 2.0.",
                "page_refs": [4],
            }
        ]
        initial = completion(
            {"translations": [{"id": "paragraph-001", "text_zh": "第一项和第二项结果。"}]}
        )
        missing_placeholders = completion(
            {"translations": [{"id": "paragraph-001", "text_zh": "第一项和第二项结果。"}]}
        )
        segmented = completion(
            {
                "translations": [
                    {
                        "id": "paragraph-001-part-A",
                        "text_zh": "第一项结果为 [[PVNUM_A]]。",
                    },
                    {
                        "id": "paragraph-001-part-B",
                        "text_zh": "第二项结果为 [[PVNUM_A]]。",
                    },
                ]
            }
        )
        with patch(
            "backend.deep_summary.request_chat_completion",
            side_effect=[initial, missing_placeholders, missing_placeholders, segmented],
        ) as request:
            merged, _ = translate_report_blocks(blocks, settings())
        self.assertEqual(request.call_count, 4)
        self.assertEqual(
            merged[0]["text_zh"], "第一项结果为 1.0。 第二项结果为 2.0。"
        )

    def test_numeric_retry_can_translate_text_spans_around_original_numbers(self) -> None:
        blocks = [
            {
                "id": "paragraph-001",
                "type": "paragraph",
                "text_en": "Score is 1.0.",
                "page_refs": [4],
            }
        ]
        missing = completion(
            {"translations": [{"id": "paragraph-001", "text_zh": "分数如下。"}]}
        )
        def fake_request(payload: dict, _settings: dict, **_kwargs: object) -> dict:
            request_data = json.loads(payload["messages"][1]["content"].split("\n\n")[-1])
            requested_ids = [item["id"] for item in request_data["blocks"]]
            if requested_ids and all("-span-" in block_id for block_id in requested_ids):
                return completion(
                    {
                        "translations": [
                            {"id": block_id, "text_zh": "分数为"}
                            for block_id in requested_ids
                        ]
                    }
                )
            return missing

        with patch(
            "backend.deep_summary.request_chat_completion", side_effect=fake_request
        ) as request:
            merged, _ = translate_report_blocks(blocks, settings())
        self.assertEqual(request.call_count, 4)
        self.assertEqual(merged[0]["text_zh"], "分数为1.0.")

    def test_translation_blocks_shape_is_normalized_without_retranslation(self) -> None:
        wrong_shape = {
            "blocks": [
                {"id": "heading-001", "type": "heading", "text_zh": "评测结果"},
                {
                    "id": "paragraph-001",
                    "type": "paragraph",
                    "text_zh": "WER 从 12.5% 改善到 9.1%。",
                },
            ]
        }
        with patch(
            "backend.deep_summary.request_chat_completion",
            return_value=completion(wrong_shape),
        ) as request:
            merged, _ = translate_report_blocks(self.blocks, settings())
        self.assertEqual(merged[1]["text_zh"], "WER 从 12.5% 改善到 9.1%。")
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.kwargs["task"], "translation")

    def test_translation_request_declares_its_json_response_schema(self) -> None:
        response = {
            "translations": [
                {"id": "heading-001", "text_zh": "评测结果"},
                {"id": "paragraph-001", "text_zh": "WER 从 12.5% 改善到 9.1%。"},
            ]
        }
        with patch(
            "backend.deep_summary.request_chat_completion", return_value=completion(response)
        ) as request:
            translate_report_blocks(self.blocks, settings())
        payload = request.call_args.args[0]
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        system_prompt = payload["messages"][0]["content"]
        self.assertIn("Return strict JSON only", system_prompt)
        schema_line = next(line for line in system_prompt.splitlines() if line.startswith('{"translations"'))
        schema = json.loads(schema_line)
        self.assertEqual(set(schema), {"translations"})
        self.assertEqual(set(schema["translations"][0]), {"id", "text_zh"})

    def test_translation_rejects_empty_or_non_text_values(self) -> None:
        blocks = [self.blocks[0]]
        for value in (None, "", " \n\t ", 123, False, [], {"text": "评测结果"}):
            with self.subTest(value=value):
                response = {"translations": [{"id": blocks[0]["id"], "text_zh": value}]}
                with patch(
                    "backend.deep_summary.request_chat_completion",
                    return_value=completion(response),
                ):
                    with self.assertRaisesRegex(SummaryError, "Translation was empty or non-text"):
                        translate_report_blocks(blocks, settings())
        with patch(
            "backend.deep_summary.request_chat_completion",
            return_value=completion({"translations": [{"id": blocks[0]["id"]}]}),
        ):
            with self.assertRaisesRegex(SummaryError, "Translation was empty or non-text"):
                translate_report_blocks(blocks, settings())
        self.assertNotIn("text_zh", blocks[0])

    def test_translation_rejects_non_object_and_extra_entries_without_leaking_errors(self) -> None:
        blocks = [self.blocks[0]]
        valid = {"id": blocks[0]["id"], "text_zh": "评测结果"}
        invalid_arrays = [[None], ["评测结果"], [42], [False], [valid, None], [None, valid], [valid, valid]]
        for entries in invalid_arrays:
            with self.subTest(entries=entries):
                with patch(
                    "backend.deep_summary.request_chat_completion",
                    return_value=completion({"translations": entries}),
                ):
                    with self.assertRaisesRegex(SummaryError, "Translation response contained invalid block entries"):
                        translate_report_blocks(blocks, settings())

    def test_translation_retries_invalid_entries_then_accepts_valid_response(self) -> None:
        blocks = [self.blocks[0]]
        valid = {"id": blocks[0]["id"], "text_zh": "评测结果"}
        with patch(
            "backend.deep_summary.request_chat_completion",
            side_effect=[
                completion({"translations": [valid, None]}),
                completion({"translations": [valid]}),
            ],
        ) as request:
            merged, _ = translate_report_blocks(blocks, settings())
        self.assertEqual(merged[0]["text_zh"], "评测结果")
        self.assertEqual(request.call_count, 2)
        self.assertIn("invalid block entries", request.call_args.args[0]["messages"][1]["content"])

    def test_large_translation_starts_with_independent_chunks(self) -> None:
        blocks = [
            {
                "id": f"paragraph-{index:03d}",
                "type": "paragraph",
                "text_en": f"Block {chr(64 + index)} " + "evidence " * 500,
                "page_refs": [index],
            }
            for index in range(1, 5)
        ]
        requested_ids: list[list[str]] = []

        def fake_request(payload: dict, _settings: dict, **_kwargs: object) -> dict:
            request_data = json.loads(payload["messages"][1]["content"].split("\n\n")[-1])
            ids = [item["id"] for item in request_data["blocks"]]
            requested_ids.append(ids)
            return completion(
                {
                    "translations": [
                        {"id": block_id, "text_zh": "对应译文"}
                        for block_id in ids
                    ]
                }
            )

        with patch(
            "backend.deep_summary.request_chat_completion", side_effect=fake_request
        ) as request:
            merged, _ = translate_report_blocks(blocks, settings())
        self.assertGreater(len(requested_ids), 1)
        self.assertTrue(all(len(ids) < len(blocks) for ids in requested_ids))
        self.assertEqual(request.call_count, len(requested_ids))
        self.assertEqual([block["id"] for block in merged], [block["id"] for block in blocks])

    def test_translation_fallback_bisects_invalid_batches_to_single_blocks(self) -> None:
        invalid = completion({"translations": []})
        heading = completion(
            {"translations": [{"id": "heading-001", "text_zh": "评测结果"}]}
        )
        paragraph = completion(
            {
                "translations": [
                    {"id": "paragraph-001", "text_zh": "WER 从 12.5% 改善到 9.1%。"}
                ]
            }
        )
        with patch(
            "backend.deep_summary.request_chat_completion",
            side_effect=[invalid, invalid, heading, paragraph],
        ) as request:
            merged, _ = translate_report_blocks(self.blocks, settings())
        self.assertEqual(merged[0]["text_zh"], "评测结果")
        self.assertEqual(merged[1]["text_zh"], "WER 从 12.5% 改善到 9.1%。")
        self.assertEqual(request.call_count, 4)
        final_payloads = [
            json.loads(call.args[0]["messages"][1]["content"].split("\n\n")[-1])
            for call in request.call_args_list[-2:]
        ]
        self.assertEqual([len(payload["blocks"]) for payload in final_payloads], [1, 1])


if __name__ == "__main__":
    unittest.main()
