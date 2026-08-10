from __future__ import annotations

import http.client
import io
import json
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from backend.app import PaperVaultServer, friendly_model_error
from backend.llm import SummaryError


PROJECT_DIR = Path(__file__).resolve().parent.parent


class FriendlyModelErrorTestCase(unittest.TestCase):
    def test_translation_validation_errors_have_safe_specific_codes(self) -> None:
        numeric_message, numeric_code = friendly_model_error(
            'Translation changed numeric content for block paragraph-001'
        )
        structure_message, structure_code = friendly_model_error(
            'Translation block IDs or order did not match the English report'
        )
        self.assertEqual(numeric_code, "translation_numeric_mismatch")
        self.assertEqual(structure_code, "translation_structure_error")
        self.assertIn("英文报告已保留", numeric_message)
        self.assertIn("英文报告已保留", structure_message)


class ApiTestCase(unittest.TestCase):
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

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict, http.client.HTTPMessage]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        response_body = response.read()
        status = response.status
        response_headers = response.headers
        connection.close()
        payload = json.loads(response_body.decode("utf-8")) if response_body else {}
        return status, payload, response_headers

    def json_request(self, method: str, path: str, payload: dict) -> tuple[int, dict, http.client.HTTPMessage]:
        return self.request(
            method,
            path,
            json.dumps(payload).encode("utf-8"),
            {"Content-Type": "application/json"},
        )

    def test_full_paper_and_tag_lifecycle(self) -> None:
        status, tag_data, _ = self.json_request(
            "POST", "/api/tags", {"name": "Vision", "color": "#34618d"}
        )
        self.assertEqual(status, 201)
        tag_id = tag_data["tag"]["id"]

        writer = PdfWriter()
        page = writer.add_blank_page(width=595, height=842)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        font_ref = writer._add_object(font)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
        )
        stream = DecodedStreamObject()
        stream.set_data(b"q 72 500 300 180 re S Q BT /F1 14 Tf 72 760 Td (Fig. 1. Test architecture.) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
        writer.add_metadata({"/Title": "Local Research Paper", "/Author": "Ada Researcher"})
        buffer = io.BytesIO()
        writer.write(buffer)
        fields = {
            "title": "",
            "authors": "",
            "publication_year": "2026",
            "doi": "10.1000/local",
            "tag_ids": json.dumps([tag_id]),
            "generate_summary": "1",
        }
        body, content_type = make_multipart(fields, "paper.pdf", buffer.getvalue())
        status, upload_data, _ = self.request(
            "POST", "/api/papers", body, {"Content-Type": content_type, "Content-Length": str(len(body))}
        )
        self.assertEqual(status, 201)
        paper = upload_data["paper"]
        paper_id = paper["id"]
        self.assertEqual(paper["title"], "Local Research Paper")
        self.assertEqual(paper["summary_status"], "draft")
        self.assertEqual(paper["summary_model"], "")
        self.assertEqual(paper["tags"][0]["name"], "Vision")
        self.assertEqual(len(paper["visual_assets"]), 1)
        self.assertEqual(paper["visual_assets"][0]["kind"], "figure")
        self.assertIn("Fig. 1", paper["visual_assets"][0]["caption"])
        self.assertTrue(paper["visual_assets"][0]["filename"].startswith("visual-1-figure-1-"))

        first_paper_id = paper_id
        duplicate_fields = {
            **fields,
            "tag_ids": "[]",
        }
        duplicate_body, duplicate_content_type = make_multipart(
            duplicate_fields, "paper-copy.pdf", buffer.getvalue()
        )
        status, duplicate_upload, _ = self.request(
            "POST",
            "/api/papers",
            duplicate_body,
            {
                "Content-Type": duplicate_content_type,
                "Content-Length": str(len(duplicate_body)),
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(duplicate_upload["deduplicated_count"], 1)
        paper = duplicate_upload["paper"]
        paper_id = paper["id"]
        self.assertNotEqual(paper_id, first_paper_id)
        self.assertEqual(paper["tags"][0]["name"], "Vision")
        status, _, _ = self.request("GET", f"/api/papers/{first_paper_id}")
        self.assertEqual(status, 404)
        self.assertTrue((self.server.upload_dir / f"{first_paper_id}.pdf").exists())
        self.assertTrue((self.server.asset_dir / first_paper_id).exists())
        status, trash_data, _ = self.request("GET", "/api/trash")
        self.assertEqual(status, 200)
        self.assertIn(first_paper_id, {item["id"] for item in trash_data["papers"]})

        asset = paper["visual_assets"][0]
        asset_connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        asset_connection.request("GET", f"/api/papers/{paper_id}/assets/{asset['filename']}")
        asset_response = asset_connection.getresponse()
        asset_bytes = asset_response.read()
        self.assertEqual(
            asset_response.status, 200, asset_bytes.decode("utf-8", errors="replace")
        )
        self.assertEqual(asset_response.getheader("Content-Type"), "image/png")
        self.assertTrue(asset_bytes.startswith(b"\x89PNG"))
        asset_connection.close()

        preview_connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        preview_connection.request("GET", f"/api/papers/{paper_id}/pages/1.png")
        preview_response = preview_connection.getresponse()
        preview_bytes = preview_response.read()
        self.assertEqual(preview_response.status, 200)
        self.assertEqual(preview_response.getheader("Content-Type"), "image/png")
        self.assertTrue(preview_bytes.startswith(b"\x89PNG"))
        preview_connection.close()

        status, page_text, _ = self.request("GET", f"/api/papers/{paper_id}/pages/1/text")
        self.assertEqual(status, 200)
        self.assertEqual(page_text["page"], 1)
        self.assertTrue(any(word["text"] == "Test" for word in page_text["words"]))

        status, search_data, _ = self.request("GET", "/api/papers?q=Vision")
        self.assertEqual(status, 200)
        self.assertEqual(search_data["total"], 1)

        status, updated_data, _ = self.json_request(
            "PUT",
            f"/api/papers/{paper_id}",
            {
                "title": "Updated Paper",
                "authors": "Ada Researcher",
                "publication_year": 2025,
                "rating": 3,
                "read_state": "read",
                "doi": "10.1000/updated",
                "tag_ids": [tag_id],
                "summary_pairs": [
                    {
                        "section_en": "Results",
                        "section_zh": "结果",
                        "en": "A retrieval result.",
                        "zh": "一个检索结果。",
                        "terms": [{"en": "retrieval", "zh": "检索"}],
                        "page_refs": [1],
                    }
                ],
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated_data["paper"]["summary_status"], "edited")
        self.assertEqual(updated_data["paper"]["rating"], 3)
        self.assertEqual(updated_data["paper"]["read_state"], "read")
        self.assertEqual(updated_data["paper"]["summary_model"], "")
        self.assertEqual(updated_data["paper"]["summary_pairs"][0]["terms"][0]["zh"], "检索")
        self.assertEqual(updated_data["paper"]["summary_pairs"][0]["page_refs"], [1])

        status, sorted_data, _ = self.request("GET", "/api/papers?sort=rating")
        self.assertEqual(status, 200)
        self.assertEqual(sorted_data["papers"][0]["rating"], 3)
        self.assertEqual(sorted_data["facets"]["total"], 1)
        self.assertEqual(sorted_data["facets"]["summary"]["ready"], 1)
        self.assertEqual(sorted_data["facets"]["rating"]["3"], 1)

        status, filtered_data, _ = self.request("GET", "/api/papers?summary=ready&rating=3")
        self.assertEqual(status, 200)
        self.assertEqual(filtered_data["total"], 1)

        status, batch_rating, _ = self.json_request(
            "POST",
            "/api/papers/batch",
            {"paper_ids": [paper_id], "action": "set_rating", "rating": 2},
        )
        self.assertEqual(status, 200)
        self.assertEqual(batch_rating["processed_count"], 1)
        self.assertEqual(batch_rating["updated"][0]["rating"], 2)

        status, batch_tags, _ = self.json_request(
            "POST",
            "/api/papers/batch",
            {"paper_ids": [paper_id], "action": "remove_tags", "tag_ids": [tag_id]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(batch_tags["updated"][0]["tags"], [])
        status, batch_tags, _ = self.json_request(
            "POST",
            "/api/papers/batch",
            {"paper_ids": [paper_id], "action": "add_tags", "tag_ids": [tag_id]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(batch_tags["updated"][0]["tags"][0]["id"], tag_id)

        status, annotation_data, _ = self.json_request(
            "POST",
            f"/api/papers/{paper_id}/annotations",
            {
                "page": 1,
                "start_word": 1,
                "end_word": 2,
                "selected_text": "Test architecture",
                "color": "yellow",
                "note": "",
            },
        )
        self.assertEqual(status, 201)
        annotation_id = annotation_data["annotation"]["id"]
        status, annotation_list, _ = self.request(
            "GET", f"/api/papers/{paper_id}/annotations"
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(annotation_list["annotations"]), 1)
        status, annotation_update, _ = self.json_request(
            "PUT",
            f"/api/annotations/{annotation_id}",
            {"color": "green", "note": "Important architecture."},
        )
        self.assertEqual(status, 200)
        self.assertEqual(annotation_update["annotation"]["color"], "green")
        self.assertEqual(annotation_update["annotation"]["note"], "Important architecture.")
        status, _, _ = self.request("DELETE", f"/api/annotations/{annotation_id}")
        self.assertEqual(status, 200)

        self.server.db.update_settings(
            {
                "provider": "openai_compatible",
                "base_url": "http://127.0.0.1:9999/v1",
                "model": "test-model",
            }
        )
        with patch(
            "backend.app.generate_english_report",
            side_effect=SummaryError(
                'LLM request failed (402): {"error":{"message":"Insufficient Balance"}}'
            ),
        ):
            status, failed_summary, _ = self.request(
                "POST", f"/api/papers/{paper_id}/generate-summary"
            )
        self.assertEqual(status, 502)
        self.assertEqual(failed_summary["error_code"], "insufficient_balance")
        self.assertEqual(failed_summary["paper"]["summary_status"], "edited")
        self.assertEqual(len(failed_summary["paper"]["summary_pairs"]), 1)
        self.assertIn("余额不足", failed_summary["error"])
        self.server.db.update_settings({"provider": "local"})

        status, offline_translation, _ = self.json_request(
            "POST",
            "/api/translate",
            {"text": "contextual biasing", "context": "Contextual biasing helps ASR."},
        )
        self.assertEqual(status, 200)
        self.assertEqual(offline_translation["translation_zh"], "上下文偏置")
        self.assertIn("外部词表", offline_translation["definition_zh"])
        self.assertIn("phonetic_us", offline_translation)
        self.assertEqual(offline_translation["note_zh"], "")
        self.assertEqual(offline_translation["source"], "offline")

        vocabulary_payload = {
            "term_en": "retrieval",
            "translation_zh": "检索",
            "paper_id": paper_id,
            "source_pair_index": 0,
        }
        status, vocabulary_data, _ = self.json_request(
            "POST", "/api/vocabulary", vocabulary_payload
        )
        self.assertEqual(status, 201)
        self.assertTrue(vocabulary_data["created"])
        vocabulary_id = vocabulary_data["entry"]["id"]
        self.assertEqual(vocabulary_data["entry"]["paper_title"], "Updated Paper")
        self.assertEqual(vocabulary_data["entry"]["context_zh"], "一个检索结果。")
        self.assertTrue(vocabulary_data["entry"]["phonetic_us"].startswith("/"))

        status, duplicate_data, _ = self.json_request(
            "POST", "/api/vocabulary", vocabulary_payload
        )
        self.assertEqual(status, 200)
        self.assertFalse(duplicate_data["created"])
        self.assertEqual(duplicate_data["entry"]["id"], vocabulary_id)

        status, translation_data, _ = self.json_request(
            "POST",
            "/api/translate",
            {"text": "retrieval", "context": "A retrieval result."},
        )
        self.assertEqual(status, 200)
        self.assertEqual(translation_data["translation_zh"], "检索")
        self.assertEqual(translation_data["source"], "wordbook")

        status, pdf_word_data, _ = self.json_request(
            "POST",
            "/api/vocabulary",
            {
                "term_en": "Test",
                "translation_zh": "测试",
                "paper_id": paper_id,
                "source_page": 1,
                "context_en": "Fig. 1. Test architecture.",
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(pdf_word_data["entry"]["source_page"], 1)
        self.assertEqual(pdf_word_data["entry"]["context_en"], "Fig. 1. Test architecture.")
        status, _, _ = self.request(
            "DELETE", f"/api/vocabulary/{pdf_word_data['entry']['id']}"
        )
        self.assertEqual(status, 200)

        status, vocabulary_data, _ = self.json_request(
            "PUT",
            f"/api/vocabulary/{vocabulary_id}",
            {"status": "mastered", "note": "Useful for RAG systems."},
        )
        self.assertEqual(status, 200)
        self.assertEqual(vocabulary_data["entry"]["status"], "mastered")
        self.assertEqual(vocabulary_data["entry"]["note"], "Useful for RAG systems.")

        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request("GET", f"/api/papers/{paper_id}/file", headers={"Range": "bytes=0-9"})
        response = connection.getresponse()
        pdf_bytes = response.read()
        self.assertEqual(response.status, 206)
        self.assertEqual(len(pdf_bytes), 10)
        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))
        connection.close()

        status, batch_delete, _ = self.json_request(
            "POST",
            "/api/papers/batch",
            {"paper_ids": [paper_id], "action": "delete"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(batch_delete["deleted"], [paper_id])
        self.assertTrue((self.server.asset_dir / paper_id).exists())
        status, vocabulary_list, _ = self.request("GET", "/api/vocabulary")
        self.assertEqual(status, 200)
        self.assertEqual(vocabulary_list["total"], 1)
        self.assertEqual(vocabulary_list["entries"][0]["paper_id"], paper_id)
        self.assertEqual(vocabulary_list["entries"][0]["paper_title"], "Updated Paper")
        status, restored, _ = self.request("POST", f"/api/trash/{paper_id}/restore")
        self.assertEqual(status, 200)
        self.assertEqual(restored["paper"]["id"], paper_id)
        status, _, _ = self.request("DELETE", f"/api/papers/{paper_id}")
        self.assertEqual(status, 200)
        status, _, _ = self.request("DELETE", f"/api/vocabulary/{vocabulary_id}")
        self.assertEqual(status, 200)
        status, list_data, _ = self.request("GET", "/api/papers")
        self.assertEqual(status, 200)
        self.assertEqual(list_data["total"], 0)

    def test_settings_are_masked(self) -> None:
        status, data, _ = self.json_request(
            "PUT",
            "/api/settings",
            {
                "provider": "openai_compatible",
                "base_url": "http://127.0.0.1:9999/v1",
                "model": "local-model",
                "analysis_model": "analysis-model",
                "translation_model": "translation-model",
                "context_window_tokens": "1000000",
                "analysis_reasoning_effort": "high",
                "api_key": "secret-value",
                "max_input_chars": "20000",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["settings"]["api_key"], "********")
        status, data, _ = self.request("GET", "/api/settings")
        self.assertEqual(status, 200)
        self.assertEqual(data["settings"]["api_key"], "********")
        self.assertEqual(data["settings"]["analysis_model"], "analysis-model")
        self.assertEqual(data["settings"]["translation_model"], "translation-model")

    def test_z_deep_summary_persists_english_and_retries_translation_only(self) -> None:
        paper_id = str(uuid.uuid4())
        self.server.db.insert_paper(
            {
                "id": paper_id,
                "title": "Structured Report Test",
                "authors": "",
                "publication_year": None,
                "doi": "",
                "original_filename": "structured.pdf",
                "stored_filename": "missing-structured.pdf",
                "file_size": 0,
                "page_count": 2,
                "extracted_text": "Method evidence on page one. Results on page two.",
                "summary_pairs": [],
                "summary_status": "pending",
                "summary_provider": "",
                "summary_model": "",
                "summary_error": "",
                "rating": 0,
                "created_at": "2026-08-10T00:00:00+00:00",
                "updated_at": "2026-08-10T00:00:00+00:00",
            },
            [],
        )
        self.server.db.update_settings(
            {
                "provider": "openai_compatible",
                "base_url": "http://127.0.0.1:9999/v1",
                "model": "default-model",
                "analysis_model": "analysis-model",
                "translation_model": "translation-model",
            }
        )
        english_blocks = [
            {
                "id": "heading-001",
                "type": "heading",
                "level": 2,
                "text_en": "Method",
                "page_refs": [1],
            },
            {
                "id": "paragraph-001",
                "type": "paragraph",
                "text_en": "The method uses one retrieval stage.",
                "page_refs": [1, 2],
            },
        ]
        try:
            with patch(
                "backend.app.generate_english_report",
                return_value=(
                    {"paper_title": "Structured Report Test", "blocks": english_blocks},
                    {"model": "analysis-model"},
                ),
            ) as generate_mock:
                status, english_data, _ = self.request(
                    "POST", f"/api/papers/{paper_id}/generate-summary"
                )
            self.assertEqual(status, 200)
            self.assertEqual(english_data["phase"], "english_complete")
            self.assertEqual(english_data["paper"]["summary_status"], "english_ready")
            self.assertEqual(english_data["paper"]["summary_translation_status"], "pending")
            self.assertNotIn("text_zh", english_data["paper"]["summary_blocks"][1])
            generate_mock.assert_called_once()

            with patch(
                "backend.app.translate_report_blocks",
                side_effect=SummaryError("translation response ids do not match"),
            ):
                status, failed_data, _ = self.request(
                    "POST", f"/api/papers/{paper_id}/translate-summary"
                )
            self.assertEqual(status, 502)
            self.assertEqual(failed_data["paper"]["summary_status"], "translation_error")
            self.assertEqual(
                failed_data["paper"]["summary_blocks"][1]["text_en"],
                "The method uses one retrieval stage.",
            )

            translated = [
                {**english_blocks[0], "text_zh": "方法"},
                {**english_blocks[1], "text_zh": "该方法使用一个检索阶段。"},
            ]
            with patch("backend.app.generate_english_report") as rerun_mock, patch(
                "backend.app.translate_report_blocks",
                return_value=(translated, {"model": "translation-model"}),
            ) as translation_mock:
                status, translated_data, _ = self.request(
                    "POST", f"/api/papers/{paper_id}/translate-summary"
                )
            self.assertEqual(status, 200)
            self.assertEqual(translated_data["paper"]["summary_status"], "ready")
            self.assertEqual(
                translated_data["paper"]["summary_blocks"][1]["text_zh"],
                "该方法使用一个检索阶段。",
            )
            rerun_mock.assert_not_called()
            translation_mock.assert_called_once()
        finally:
            self.request("DELETE", f"/api/papers/{paper_id}")
            self.server.db.update_settings({"provider": "local"})

    def test_invalid_upload_metadata_returns_400_without_orphan_file(self) -> None:
        existing_uploads = set(self.server.upload_dir.glob("*.pdf"))
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        buffer = io.BytesIO()
        writer.write(buffer)
        body, content_type = make_multipart(
            {"publication_year": "not-a-year", "generate_summary": "0"},
            "invalid-year.pdf",
            buffer.getvalue(),
        )
        status, data, _ = self.request(
            "POST", "/api/papers", body,
            {"Content-Type": content_type, "Content-Length": str(len(body))},
        )
        self.assertEqual(status, 400)
        self.assertIn("year", data["error"].lower())
        self.assertEqual(set(self.server.upload_dir.glob("*.pdf")), existing_uploads)


def make_multipart(fields: dict[str, str], filename: str, file_bytes: bytes) -> tuple[bytes, str]:
    boundary = f"----PaperVaultTest{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )
    chunks.extend(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode(),
            b"Content-Type: application/pdf\r\n\r\n",
            file_bytes,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


if __name__ == "__main__":
    unittest.main()
