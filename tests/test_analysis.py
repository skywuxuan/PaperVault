from __future__ import annotations

import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
import uuid
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from backend.analysis import build_text_chunks
from backend.app import PaperVaultServer
from backend.database import Database, utc_now


PROJECT_DIR = Path(__file__).resolve().parent.parent


class DatabaseMigrationTestCase(unittest.TestCase):
    def test_interrupted_summaries_recover_without_discarding_saved_content(self) -> None:
        cases = [
            ("generating", [], [], "none", "error"),
            ("generating", [], [{"en": "Saved", "zh": "已保存"}], "none", "edited"),
            ("generating", [{"text_en": "Saved", "text_zh": "已保存"}], [], "ready", "ready"),
            ("translating", [{"text_en": "Saved"}], [], "translating", "translation_error"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "paper-vault.db")
            database.initialize()
            records = []
            for status, blocks, pairs, translation_status, expected_status in cases:
                paper_id = insert_test_paper(database)
                database.update_paper(
                    paper_id,
                    {
                        "summary_status": status,
                        "summary_blocks": blocks,
                        "summary_pairs": pairs,
                        "summary_translation_status": translation_status,
                    },
                    None,
                )
                records.append((paper_id, blocks, pairs, expected_status))
            Database(database.path).initialize()
            for paper_id, blocks, pairs, expected_status in records:
                with self.subTest(expected_status=expected_status):
                    paper = database.get_paper(paper_id)
                    self.assertEqual(paper["summary_status"], expected_status)
                    self.assertEqual(paper["summary_blocks"], blocks)
                    self.assertEqual(paper["summary_pairs"], pairs)
                    self.assertNotEqual(paper["summary_translation_status"], "translating")
                    error_key = "summary_translation_error_code" if expected_status == "translation_error" else "summary_error_code"
                    self.assertEqual(paper[error_key], "interrupted")

    def test_legacy_database_is_migrated_without_changing_paper_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "paper-vault.db"
            with closing(sqlite3.connect(path)) as connection:
                connection.executescript(
                    """
                    CREATE TABLE papers (
                        id TEXT PRIMARY KEY, title TEXT NOT NULL, authors TEXT NOT NULL DEFAULT '',
                        publication_year INTEGER, doi TEXT NOT NULL DEFAULT '',
                        original_filename TEXT NOT NULL, stored_filename TEXT NOT NULL UNIQUE,
                        file_size INTEGER NOT NULL DEFAULT 0, page_count INTEGER NOT NULL DEFAULT 0,
                        extracted_text TEXT NOT NULL DEFAULT '', summary_pairs TEXT NOT NULL DEFAULT '[]',
                        summary_status TEXT NOT NULL DEFAULT 'pending', summary_provider TEXT NOT NULL DEFAULT '',
                        summary_error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    """
                )
                connection.execute(
                    """
                    INSERT INTO papers VALUES (
                        'legacy-paper', 'Legacy title', 'Legacy author', 2024, '', 'legacy.pdf',
                        'legacy-paper.pdf', 12, 2, 'Stable legacy text',
                        '[{"en":"Evidence","zh":"证据","page_refs":[2]}]',
                        'ready', 'curated', '', '2024-01-01T00:00:00+00:00',
                        '2024-01-01T00:00:00+00:00'
                    )
                    """
                )
                connection.commit()
            database = Database(path)
            database.initialize()
            paper = database.get_paper("legacy-paper", include_text=True)
            self.assertIsNotNone(paper)
            self.assertEqual(paper["title"], "Legacy title")
            self.assertEqual(paper["extracted_text"], "Stable legacy text")
            self.assertEqual(paper["summary_pairs"][0]["zh"], "证据")
            self.assertEqual(paper["summary_blocks"], [])
            self.assertEqual(paper["summary_translation_status"], "none")
            self.assertEqual(paper["read_state"], "unread")
            with database.connect() as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 4)
                title_key = connection.execute(
                    "SELECT title_key FROM papers WHERE id = 'legacy-paper'"
                ).fetchone()[0]
                self.assertEqual(title_key, "legacytitle")

    def test_chunks_and_running_jobs_survive_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "paper-vault.db")
            database.initialize()
            paper_id = insert_test_paper(database)
            chunks = build_text_chunks(
                paper_id,
                [
                    {"page": 1, "text": "The architecture retrieves evidence from a local index."},
                    {"page": 2, "text": "Ablation results show the retrieval component is important."},
                ],
            )
            database.replace_paper_chunks(paper_id, chunks)
            results = database.search_paper_chunks("retrieval architecture", paper_id)
            self.assertTrue(results)
            self.assertEqual(results[0]["paper_id"], paper_id)
            self.assertEqual(len(results[0]["content_hash"]), 64)
            database.delete_paper(paper_id)
            self.assertEqual(database.search_paper_chunks("retrieval architecture"), [])
            database.restore_paper(paper_id)
            self.assertTrue(database.search_paper_chunks("retrieval architecture", paper_id))
            job = database.create_analysis_job(
                {
                    "id": str(uuid.uuid4()),
                    "paper_id": paper_id,
                    "job_type": "quick_read",
                    "input_hash": "a" * 64,
                    "prompt_version": "quick-read-v1",
                }
            )
            with database.connect() as connection:
                connection.execute(
                    "UPDATE analysis_jobs SET status = 'running', attempts = 1 WHERE id = ?",
                    (job["id"],),
                )
            Database(database.path).initialize()
            recovered = database.get_analysis_job(job["id"])
            self.assertEqual(recovered["status"], "queued")
            self.assertIn("restart", recovered["error"].lower())


class AnalysisApiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.server = PaperVaultServer(
            ("127.0.0.1", 0), PROJECT_DIR / "frontend", Path(cls.temp_dir.name)
        )
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)
        cls.temp_dir.cleanup()

    def setUp(self) -> None:
        self.paper_id = insert_test_paper(self.server.db)
        asset_directory = self.server.asset_dir / self.paper_id
        asset_directory.mkdir(parents=True, exist_ok=True)
        filename = "visual-1-figure-1-a1b2c3d4.png"
        (asset_directory / filename).write_bytes(b"\x89PNG\r\n\x1a\nfigure-content")
        self.server.db.update_paper(
            self.paper_id,
            {
                "visual_assets": [
                    {
                        "page": 1,
                        "filename": filename,
                        "kind": "figure",
                        "caption": "Figure 1. Method and retrieval architecture",
                        "title_en": "Method and retrieval architecture",
                        "title_zh": "方法与检索架构",
                    }
                ]
            },
            None,
        )

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=8)
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        data = json.loads(response.read().decode("utf-8"))
        status = response.status
        connection.close()
        return status, data

    def test_removed_analysis_and_qa_routes_stay_unavailable(self) -> None:
        original_pairs = self.server.db.get_paper(self.paper_id)["summary_pairs"]
        for method, path, payload in (
            ("POST", f"/api/papers/{self.paper_id}/analyses/quick-read", {}),
            ("POST", f"/api/papers/{self.paper_id}/analyses/figure-analysis", {}),
            ("GET", f"/api/papers/{self.paper_id}/analyses", None),
            ("POST", "/api/qa", {"question": "What does this paper discuss?"}),
        ):
            with self.subTest(path=path):
                status, data = self.request(method, path, payload)
                self.assertEqual(status, 404)
                self.assertEqual(data["error"], "API route not found")
        self.assertEqual(self.server.db.get_paper(self.paper_id)["summary_pairs"], original_pairs)

    def test_visual_refresh_failure_preserves_existing_asset_index(self) -> None:
        legacy_assets = [{"page": 1, "filename": "page-1.png"}]
        self.server.db.update_paper(
            self.paper_id,
            {"visual_assets": legacy_assets},
            None,
        )
        upload = self.server.upload_dir / f"{self.paper_id}.pdf"
        upload.write_bytes(b"%PDF-1.4\n")

        with patch("backend.app.extract_visual_pages", side_effect=RuntimeError("render failed")):
            paper = self.server.ensure_visual_assets(self.paper_id, refresh_legacy=True)

        self.assertIsNotNone(paper)
        self.assertEqual(paper["visual_assets"], legacy_assets)
        self.assertEqual(
            self.server.db.get_paper(self.paper_id)["visual_assets"], legacy_assets
        )

    def test_soft_delete_keeps_files_and_can_be_restored(self) -> None:
        upload = self.server.upload_dir / f"{self.paper_id}.pdf"
        upload.write_bytes(b"%PDF-1.4\n")
        asset_filename = self.server.db.get_paper(self.paper_id)["visual_assets"][0]["filename"]
        status, _ = self.request("DELETE", f"/api/papers/{self.paper_id}")
        self.assertEqual(status, 200)
        self.assertTrue(upload.is_file())
        self.assertTrue((self.server.asset_dir / self.paper_id / asset_filename).is_file())
        status, _ = self.request("GET", f"/api/papers/{self.paper_id}")
        self.assertEqual(status, 404)
        status, trash = self.request("GET", "/api/trash")
        self.assertEqual(status, 200)
        self.assertIn(self.paper_id, {paper["id"] for paper in trash["papers"]})
        status, restored = self.request("POST", f"/api/trash/{self.paper_id}/restore")
        self.assertEqual(status, 200)
        self.assertEqual(restored["paper"]["id"], self.paper_id)


def insert_test_paper(database: Database) -> str:
    paper_id = str(uuid.uuid4())
    now = utc_now()
    database.insert_paper(
        {
            "id": paper_id,
            "title": f"Retrieval Architecture {paper_id[:8]}",
            "authors": "Ada Researcher",
            "publication_year": 2026,
            "doi": "",
            "original_filename": f"{paper_id}.pdf",
            "stored_filename": f"{paper_id}.pdf",
            "file_size": 0,
            "page_count": 2,
            "extracted_text": (
                "The motivation is to retrieve reliable evidence. The method uses a local retrieval "
                "architecture. Results show improved evidence grounding. The limitation is evaluation scale."
            ),
            "summary_pairs": [
                {
                    "section_en": "Method and results",
                    "section_zh": "方法与结果",
                    "en": "The retrieval architecture improves evidence grounding.",
                    "zh": "检索架构提升了证据约束。",
                    "terms": [],
                    "page_refs": [1],
                }
            ],
            "visual_assets": [],
            "summary_status": "ready",
            "summary_provider": "curated",
            "summary_model": "",
            "summary_error": "",
            "rating": 0,
            "created_at": now,
            "updated_at": now,
        },
        [],
    )
    return paper_id


if __name__ == "__main__":
    unittest.main()
