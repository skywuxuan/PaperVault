from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

from backend.database import Database


class LibrarySortTestCase(unittest.TestCase):
    def test_tag_order_is_persisted_and_new_tags_are_appended(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "paper.db")
            database.initialize()
            first = database.create_tag("tag-a", "Alpha", "#111111")
            second = database.create_tag("tag-b", "Beta", "#222222")
            third = database.create_tag("tag-c", "Gamma", "#333333")
            self.assertEqual([tag["id"] for tag in database.list_tags()], [first["id"], second["id"], third["id"]])
            self.assertEqual(
                [tag["id"] for tag in database.reorder_tags([third["id"], first["id"], second["id"]])],
                [third["id"], first["id"], second["id"],],
            )
            fourth = database.create_tag("tag-d", "Delta", "#444444")
            self.assertEqual([tag["id"] for tag in database.list_tags()], [third["id"], first["id"], second["id"], fourth["id"]])

    def test_library_pagination_returns_requested_page(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "paper.db")
            database.initialize()
            for index in range(45):
                database.insert_paper({
                    "id": f"paper-{index}", "title": f"Paper {index:02d}",
                    "original_filename": f"paper-{index}.pdf", "stored_filename": f"paper-{index}.pdf",
                    "created_at": f"2026-01-{(index % 28) + 1:02d}T00:00:00+00:00",
                    "updated_at": f"2026-01-{(index % 28) + 1:02d}T00:00:00+00:00",
                }, [])
            first = database.list_papers(sort="title_asc", page=1, page_size=20)
            third = database.list_papers(sort="title_asc", page=3, page_size=20)
            self.assertEqual(len(first), 20)
            self.assertEqual(len(third), 5)
            self.assertEqual(first[0]["title"], "Paper 00")
            self.assertEqual(third[0]["title"], "Paper 40")
            self.assertEqual(database.count_papers(), 45)

    def test_unknown_years_are_last_in_both_directions_with_stable_same_year_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "paper.db")
            database.initialize()
            records = [
                ("unknown", None, "2026-10-01"),
                ("newer", 2025, "2026-01-02"),
                ("older", 2024, "2026-01-01"),
                ("same-a", 2025, "2026-01-01"),
                ("same-b", 2025, "2026-01-01"),
            ]
            for paper_id, year, date in records:
                database.insert_paper({
                    "id": paper_id, "title": paper_id, "publication_year": year,
                    "original_filename": f"{paper_id}.pdf", "stored_filename": f"{paper_id}.pdf",
                    "created_at": date + "T00:00:00+00:00", "updated_at": date + "T00:00:00+00:00",
                }, [])
            self.assertEqual([p["id"] for p in database.list_papers(sort="publication_desc")],
                             ["newer", "same-a", "same-b", "older", "unknown"])
            self.assertEqual([p["id"] for p in database.list_papers(sort="publication_asc")],
                             ["older", "newer", "same-a", "same-b", "unknown"])

    def test_library_supports_both_directions_for_each_sort_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "paper.db")
            database.initialize()
            database.insert_paper(
                {
                    "id": str(uuid.uuid4()),
                    "title": "Zeta Study",
                    "authors": "Author B",
                    "publication_year": 2022,
                    "rating": 1,
                    "original_filename": "zeta.pdf",
                    "stored_filename": "zeta.pdf",
                    "created_at": "2026-01-02T00:00:00+00:00",
                    "updated_at": "2026-01-02T00:00:00+00:00",
                },
                [],
            )
            database.insert_paper(
                {
                    "id": str(uuid.uuid4()),
                    "title": "Alpha Study",
                    "authors": "Author A",
                    "publication_year": 2024,
                    "rating": 3,
                    "original_filename": "alpha.pdf",
                    "stored_filename": "alpha.pdf",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-01T00:00:00+00:00",
                },
                [],
            )

            def titles(sort: str) -> list[str]:
                return [paper["title"] for paper in database.list_papers(sort=sort)]

            self.assertEqual(titles("recent_desc"), ["Zeta Study", "Alpha Study"])
            self.assertEqual(titles("recent_asc"), ["Alpha Study", "Zeta Study"])
            self.assertEqual(titles("rating_desc"), ["Alpha Study", "Zeta Study"])
            self.assertEqual(titles("rating_asc"), ["Zeta Study", "Alpha Study"])
            self.assertEqual(titles("publication_desc"), ["Alpha Study", "Zeta Study"])
            self.assertEqual(titles("publication_asc"), ["Zeta Study", "Alpha Study"])
            self.assertEqual(titles("title_asc"), ["Alpha Study", "Zeta Study"])
            self.assertEqual(titles("title_desc"), ["Zeta Study", "Alpha Study"])


if __name__ == "__main__":
    unittest.main()
