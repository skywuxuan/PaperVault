from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.database import Database
from backend.pdf_parser import extract_pdf, infer_publication_year, infer_publication_year_details
from backend.app import parse_year
from datetime import datetime
from pypdf import PdfWriter


class PublicationYearTestCase(unittest.TestCase):
    def test_qwen_report_does_not_take_the_first_citation_year(self) -> None:
        text = (
            "Qwen-Audio-3.1-Realtime Technical Report\nAlibaba Group\nAbstract\n"
            "We evaluate audio reasoning.\n1 Introduction: Realtime Beyond Latency\n"
            "Group Relative Policy Optimization (GRPO) (Shao et al., 2024).\n"
            "Following Tongyi Fun Team et al. (2025)."
        )
        self.assertIsNone(infer_publication_year(text, "Qwen-Audio-3.1-Realtime-Technical-Report.pdf"))

    def test_filename_year_has_priority_over_citations_and_header_dates(self) -> None:
        self.assertEqual(
            infer_publication_year("21 September 2026\nAbstract\nPrior work (2021).", "paper_2025_v2.pdf"),
            2025,
        )
        for filename in ("Model2024.pdf", "arxiv-2024.12345.pdf", "model-12024.pdf"):
            with self.subTest(filename=filename):
                self.assertIsNone(infer_publication_year("Prior work (2022).", filename))

    def test_explicit_front_matter_dates_are_recognized(self) -> None:
        dates = (
            "21 September 2026", "September 21, 2026", "Sep. 2026",
            "2026-09-21", "2026/09/21", "2026年9月21日",
            "Published online: September 21, 2026", "Publication date: 2026",
            "发布日期：2026年9月21日",
        )
        for date in dates:
            with self.subTest(date=date):
                self.assertEqual(infer_publication_year(f"Title\nAuthor\n{date}\nAbstract\nWork (2024).", "paper.pdf"), 2026)

    def test_arxiv_stamp_after_body_uses_its_date_not_its_identifier(self) -> None:
        text = (
            "Title\nAbstract\nPrior work (2020).\n"
            "arXiv:2410.06885v2 [eess.AS] 15 Oct 2025"
        )
        self.assertEqual(infer_publication_year(text, "paper.pdf"), 2025)

    def test_formal_publication_wins_over_an_older_preprint_and_filename(self) -> None:
        text = (
            "HybridFlow\nAuthor\nzhangchi.usc1992@example.com\nAbstract\nMethod.\n"
            "© 2025 Copyright held by the owner/author(s). Publication rights licensed\n"
            "to ACM.\narXiv:2409.19256v2 [cs.LG] 2 Oct 2024"
        )
        result = infer_publication_year_details(text, "HybridFlow-2024.pdf")
        self.assertEqual(result["year"], 2025)
        self.assertEqual(result["source"], "publication")
        self.assertIn("© 2025", result["evidence"])

    def test_conference_footer_and_published_date_are_recognized_after_abstract(self) -> None:
        for line in ("Interspeech 2025", "Publish Date:June 16, 2025"):
            with self.subTest(line=line):
                self.assertEqual(infer_publication_year_details(f"Title\nAbstract\nMethod\n{line}", "paper.pdf")["year"], 2025)

    def test_conflicting_evidence_needs_confirmation(self) -> None:
        for text, filename in (
            ("Title", "paper-2024-2025.pdf"),
            ("Title\nPublished: 2024\nPublication date: 2025", "paper-2026.pdf"),
        ):
            with self.subTest(filename=filename):
                result = infer_publication_year_details(text, filename)
                self.assertIsNone(result["year"])
                self.assertEqual(result["source"], "unknown")
                self.assertIn("Conflicting", result["evidence"])

    def test_invalid_calendar_dates_and_creation_metadata_are_not_used(self) -> None:
        for date in ("2025-02-30", "2025-13-01", "32 January 2025", "Published: 2099"):
            with self.subTest(date=date):
                self.assertIsNone(infer_publication_year(date, "paper.pdf"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "paper.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=200, height=200)
            writer.add_metadata({"/CreationDate": "D:20260921120000Z", "/ModDate": "D:20261014120000Z"})
            with path.open("wb") as output:
                writer.write(output)
            parsed = extract_pdf(path)
            self.assertIsNone(infer_publication_year_details(parsed["text"], path.name, first_page_text=parsed["first_page_text"])["year"])

    def test_api_year_validation_rejects_fractional_boolean_and_future_values(self) -> None:
        for value in (True, False, 2025.5, "2025.5", "202", "2025-01-01", datetime.now().year + 2):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_year(value)
        self.assertEqual(parse_year(" 2025 "), 2025)
        self.assertIsNone(parse_year("  "))

    def test_body_dates_reference_dates_and_unrelated_years_are_not_publication_dates(self) -> None:
        for text in (
            "Paper Title\nAbstract\nSeptember 21, 2025\nDataset created on this date.",
            "Paper Title\n1 Introduction\n2025-09-21",
            "Paper Title\nReferences\nPublished: 2024",
            "Paper Title\n1992 samples were used.",
            "Paper Title\nRelated work (Shao et al., 2024).",
        ):
            with self.subTest(text=text):
                self.assertIsNone(infer_publication_year(text, "paper.pdf"))

    def test_restart_does_not_backfill_citations_or_overwrite_an_existing_year(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "paper.db")
            database.initialize()
            for index, year in enumerate((None, 2026)):
                database.insert_paper(
                    {
                        "id": f"paper-{index}", "title": f"Paper {index}",
                        "publication_year": year, "original_filename": f"paper-{index}.pdf",
                        "stored_filename": f"paper-{index}.pdf",
                        "extracted_text": "Title\nAbstract\nPrior work (Shao et al., 2024).",
                        "created_at": "2026-09-21T00:00:00+00:00",
                        "updated_at": "2026-09-21T00:00:00+00:00",
                    }, [],
                )
            database.initialize()
            self.assertIsNone(database.get_paper("paper-0")["publication_year"])
            self.assertEqual(database.get_paper("paper-1")["publication_year"], 2026)

    def test_manually_cleared_year_stays_blank_on_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "paper.db")
            database.initialize()
            database.insert_paper(
                {"id": "manual", "title": "Manual", "publication_year": None,
                 "publication_year_source": "manual", "publication_year_evidence": "用户留空，待确认",
                 "original_filename": "paper-2025.pdf", "stored_filename": "paper.pdf",
                 "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:00+00:00"}, [],
            )
            database.initialize()
            paper = database.get_paper("manual")
            self.assertIsNone(paper["publication_year"])
            self.assertEqual(paper["publication_year_source"], "manual")


if __name__ == "__main__":
    unittest.main()
