from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pymupdf

from backend.llm import (
    _apply_provider_controls,
    _parse_pairs,
    _request_chat_completion_details,
    local_draft,
)
from backend.offline_translation import translate_english_offline
from backend.pronunciation import american_ipa
from backend.pdf_parser import (
    _prefer_complete_title, extract_pdf, extract_visual_pages, infer_authors,
    infer_publication_year, infer_title, normalize_pdf_authors,
)


class SummaryFormatTestCase(unittest.TestCase):
    def test_request_uses_saved_key_instead_of_environment_key(self) -> None:
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(
            {"choices": [{"message": {"content": "ok"}}]}
        ).encode("utf-8")
        with patch.dict(os.environ, {"PAPER_VAULT_API_KEY": "environment-key"}), patch(
            "backend.llm.urllib.request.urlopen", return_value=response
        ) as urlopen:
            result = _request_chat_completion_details(
                {"model": "test-model", "messages": []},
                {
                    "base_url": "https://example.com/v1",
                    "api_key": "saved-key",
                },
                10,
            )
        self.assertEqual(result["content"], "ok")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer saved-key")

    def test_official_deepseek_v4_disables_default_thinking(self) -> None:
        payload = {"model": "deepseek-v4-flash"}
        _apply_provider_controls(
            payload,
            {
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-v4-flash",
            },
        )
        self.assertEqual(payload["thinking"], {"type": "disabled"})

    def test_deepseek_analysis_enables_high_effort_thinking(self) -> None:
        payload = {"model": "deepseek-v4-flash", "temperature": 0.2}
        _apply_provider_controls(
            payload,
            {
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-v4-flash",
                "analysis_reasoning_effort": "high",
            },
            task="analysis",
        )
        self.assertEqual(payload["thinking"], {"type": "enabled"})
        self.assertEqual(payload["reasoning_effort"], "high")
        self.assertNotIn("temperature", payload)
        self.assertNotIn("input_image", payload)
        self.assertNotIn("file", payload)

    def test_deepseek_max_effort_requires_explicit_setting(self) -> None:
        payload = {"model": "deepseek-v4-flash"}
        _apply_provider_controls(
            payload,
            {
                "base_url": "https://api.deepseek.com",
                "analysis_reasoning_effort": "max",
            },
            task="analysis",
        )
        self.assertEqual(payload["reasoning_effort"], "max")

    def test_deepseek_translation_disables_thinking(self) -> None:
        payload = {"model": "deepseek-v4-flash", "reasoning_effort": "high"}
        _apply_provider_controls(
            payload,
            {"base_url": "https://api.deepseek.com"},
            task="translation",
        )
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertNotIn("reasoning_effort", payload)

    def test_other_openai_compatible_providers_are_unchanged(self) -> None:
        payload = {"model": "deepseek-v4-flash"}
        _apply_provider_controls(
            payload,
            {
                "base_url": "https://example.com/v1",
                "model": "deepseek-v4-flash",
            },
        )
        self.assertNotIn("thinking", payload)

    def test_apifusion_disables_thinking_for_translation(self) -> None:
        payload = {
            "model": "deepseek-v4-flash",
            "temperature": 0.1,
            "reasoning_effort": "high",
        }
        _apply_provider_controls(
            payload,
            {
                "base_url": "https://apifusion.aispeech.com.cn/v1",
                "model": "deepseek-v4-flash",
            },
            task="translation",
        )
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertNotIn("reasoning_effort", payload)
        self.assertEqual(payload["temperature"], 0.1)

    def test_apifusion_keeps_analysis_controls_provider_owned(self) -> None:
        payload = {"model": "deepseek-v4-flash"}
        _apply_provider_controls(
            payload,
            {
                "base_url": "https://apifusion.aispeech.com.cn/v1",
                "model": "deepseek-v4-flash",
            },
            task="analysis",
        )
        self.assertNotIn("thinking", payload)

    def test_academic_glossary_translates_without_network(self) -> None:
        translated = translate_english_offline("reinforcement")
        self.assertEqual(translated["translation_zh"], "强化；增强；加固")
        self.assertIn("反馈", translated["definition_zh"])
        self.assertEqual(translated["note_zh"], "")
        self.assertEqual(translated["phonetic_us"], "/ˌriɪnˈfɔrsmənt/")

    def test_american_ipa_uses_dictionary_style_stress(self) -> None:
        self.assertEqual(american_ipa("General"), "/ˈdʒɛnɚəl/")
        self.assertEqual(
            american_ipa("reinforcement learning"),
            "/ˌriɪnˈfɔrsmənt ˈlɝnɪŋ/",
        )

    def test_publication_year_prefers_filename_metadata(self) -> None:
        self.assertEqual(
            infer_publication_year("References include work from 2021.", "paper-title-2025.pdf"),
            2025,
        )

    def test_multiline_title_is_joined_only_while_filename_confirms_it(self) -> None:
        text = """arXiv:2410.06885v2  [eess.AS]  15 Oct 2024
F5-TTS: A Fairytaler that Fakes Fluent
and Faithful Speech with Flow Matching
Yushen Chen, Zhikang Niu, Ziyang Ma
Abstract
Body text."""
        inferred = infer_title(
            text,
            "F5-TTS A Fairytaler that Fakes Fluent and Faithful Speech with Flow Matching.pdf",
        )
        self.assertEqual(
            inferred,
            "F5-TTS: A Fairytaler that Fakes Fluent and Faithful Speech with Flow Matching",
        )
        self.assertNotIn("Yushen Chen", inferred)

    def test_complete_inferred_title_replaces_truncated_pdf_metadata(self) -> None:
        self.assertEqual(
            _prefer_complete_title(
                "Step-Audio: Unified Understanding and",
                "Step-Audio: Unified Understanding and Generation in Intelligent Speech Interaction",
            ),
            "Step-Audio: Unified Understanding and Generation in Intelligent Speech Interaction",
        )
        self.assertEqual(
            _prefer_complete_title("Curated Display Title", "Unrelated inferred title"),
            "Curated Display Title",
        )

    def test_pdf_extraction_uses_original_filename_to_verify_title_continuation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pdf_path = Path(temporary) / "stored-uuid.pdf"
            document = pymupdf.open()
            page = document.new_page()
            page.insert_text(
                (72, 72),
                "Step-Audio: Unified Understanding and\n"
                "Generation in Intelligent Speech Interaction\n"
                "Step-Audio Team\nAbstract",
            )
            document.save(pdf_path)
            document.close()
            parsed = extract_pdf(
                pdf_path,
                "Step-Audio Unified Understanding and Generation in Intelligent Speech Interaction.pdf",
            )
        self.assertEqual(
            parsed["title"],
            "Step-Audio: Unified Understanding and Generation in Intelligent Speech Interaction",
        )

    def test_invalid_year_author_metadata_is_rejected(self) -> None:
        self.assertEqual(normalize_pdf_authors("2024"), "")
        self.assertEqual(normalize_pdf_authors("2024, 2025"), "")
        self.assertEqual(normalize_pdf_authors("Ada Researcher"), "Ada Researcher")

    def test_authors_can_be_inferred_conservatively_from_first_page(self) -> None:
        text = "Reliable Paper Title\nAda Lovelace, Alan Turing\nUniversity of Example\nAbstract\nBody"
        self.assertEqual(
            infer_authors(text, "Reliable Paper Title"), "Ada Lovelace, Alan Turing"
        )

    def test_visual_extraction_ignores_a_text_only_figure_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf_path = root / "reference-only.pdf"
            document = pymupdf.open()
            page = document.new_page()
            page.insert_text((72, 72), "As shown in Figure 2, the prior method is slower.")
            document.save(pdf_path)
            document.close()
            self.assertEqual(extract_visual_pages(pdf_path, root / "assets"), [])

    def test_enriched_pairs_and_verbatim_terms_are_preserved(self) -> None:
        content = json.dumps(
            {
                "pairs": [
                    {
                        "section_en": "Results",
                        "section_zh": "结果",
                        "en": "Keyword error rate improved substantially.",
                        "zh": "关键词错误率得到显著改善。",
                        "terms": [
                            {"en": "Keyword error rate", "zh": "关键词错误率"},
                            {"en": "missing phrase", "zh": "不存在的词语"},
                        ],
                        "page_refs": [4, "4", -1, "bad"],
                    }
                ]
            },
            ensure_ascii=False,
        )
        pairs = _parse_pairs(content)
        self.assertEqual(pairs[0]["section_zh"], "结果")
        self.assertEqual(pairs[0]["terms"], [{"en": "Keyword error rate", "zh": "关键词错误率"}])
        self.assertEqual(pairs[0]["page_refs"], [4])

    def test_local_index_does_not_copy_english_into_chinese(self) -> None:
        pairs = local_draft(
            "Example Paper",
            "Researchers propose a sufficiently detailed method that improves retrieval quality for a challenging benchmark.",
        )
        self.assertTrue(pairs)
        self.assertNotIn(pairs[0]["en"], pairs[0]["zh"])

    def test_parser_keeps_dense_word_alignment(self) -> None:
        en_terms = [f"term{i}" for i in range(40)]
        zh_terms = [f"术语{i}" for i in range(40)]
        content = json.dumps(
            {
                "pairs": [{
                    "en": " ".join(en_terms),
                    "zh": " ".join(zh_terms),
                    "terms": [
                        {"en": en, "zh": zh}
                        for en, zh in zip(en_terms, zh_terms, strict=True)
                    ],
                }]
            },
            ensure_ascii=False,
        )
        self.assertEqual(len(_parse_pairs(content)[0]["terms"]), 32)

    def test_parser_translates_general_task_and_adds_word_aliases(self) -> None:
        content = json.dumps(
            {
                "pairs": [{
                    "en": "General Task SACC improves.",
                    "zh": "General Task 的句级准确率得到提升。",
                    "terms": [{"en": "General Task", "zh": "General Task"}],
                }]
            },
            ensure_ascii=False,
        )
        pair = _parse_pairs(content)[0]
        self.assertEqual(pair["zh"], "通用任务的句级准确率得到提升。")
        self.assertIn({"en": "General", "zh": "通用"}, pair["terms"])
        self.assertIn({"en": "Task", "zh": "任务"}, pair["terms"])

    def test_parser_keeps_a_long_sentence_report(self) -> None:
        content = json.dumps(
            {
                "pairs": [
                    {
                        "section_en": "Results",
                        "section_zh": "实验结果",
                        "en": f"Result statement {index} reports an exact value.",
                        "zh": f"第{index}条结果陈述报告了一个精确数值。",
                        "page_refs": [4],
                    }
                    for index in range(40)
                ]
            },
            ensure_ascii=False,
        )
        self.assertEqual(len(_parse_pairs(content)), 40)


if __name__ == "__main__":
    unittest.main()
