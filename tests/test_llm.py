from __future__ import annotations

import json
import unittest

from backend.llm import _apply_provider_controls, _parse_pairs, local_draft
from backend.offline_translation import translate_english_offline
from backend.pronunciation import american_ipa
from backend.pdf_parser import infer_publication_year


class SummaryFormatTestCase(unittest.TestCase):
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
