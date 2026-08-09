from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import sys
import unicodedata
import uuid
from email.parser import BytesParser
from email.policy import default
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__
from .database import Database, utc_now
from .llm import SummaryError, generate_summary, translate_english
from .offline_translation import OfflineTranslationError, translate_english_offline
from .pronunciation import american_ipa
from .pdf_parser import (
    extract_pdf,
    extract_preview_words,
    extract_visual_pages,
    infer_title,
    infer_publication_year,
    render_preview_page,
)


MAX_UPLOAD_BYTES = 100 * 1024 * 1024
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
PAPER_ROUTE_RE = re.compile(r"^/api/papers/([0-9a-f-]+)$")
PAPER_FILE_RE = re.compile(r"^/api/papers/([0-9a-f-]+)/file$")
PAPER_ASSET_RE = re.compile(r"^/api/papers/([0-9a-f-]+)/assets/(page-[0-9]+\.png)$")
PAPER_PAGE_IMAGE_RE = re.compile(r"^/api/papers/([0-9a-f-]+)/pages/([0-9]+)\.png$")
PAPER_PAGE_TEXT_RE = re.compile(r"^/api/papers/([0-9a-f-]+)/pages/([0-9]+)/text$")
PAPER_SUMMARY_RE = re.compile(r"^/api/papers/([0-9a-f-]+)/generate-summary$")
TAG_ROUTE_RE = re.compile(r"^/api/tags/([0-9a-f-]+)$")
VOCABULARY_ROUTE_RE = re.compile(r"^/api/vocabulary/([0-9a-f-]+)$")
PAPER_ANNOTATIONS_RE = re.compile(r"^/api/papers/([0-9a-f-]+)/annotations$")
ANNOTATION_ROUTE_RE = re.compile(r"^/api/annotations/([0-9a-f-]+)$")
VOCABULARY_STATUSES = {"learning", "mastered"}
ANNOTATION_COLORS = {"yellow", "green", "blue", "coral"}


class PaperVaultServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], frontend_dir: Path, data_dir: Path):
        self.frontend_dir = frontend_dir.resolve()
        self.data_dir = data_dir.resolve()
        self.upload_dir = self.data_dir / "uploads"
        self.asset_dir = self.data_dir / "assets"
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.asset_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(self.data_dir / "paper-vault.db")
        self.db.initialize()
        self.deduplicate_library()
        super().__init__(address, PaperVaultHandler)

    def ensure_visual_assets(self, paper_id: str) -> dict[str, Any] | None:
        paper = self.db.get_paper(paper_id)
        if paper is None or paper.get("visual_assets"):
            return paper
        pdf_path = self.upload_dir / paper["stored_filename"]
        if not pdf_path.is_file():
            return paper
        try:
            assets = extract_visual_pages(pdf_path, self.asset_dir / paper_id)
        except Exception:
            assets = []
        if assets:
            return self.db.update_paper(paper_id, {"visual_assets": assets}, None)
        return paper

    def remove_paper_record(self, paper_id: str) -> bool:
        stored_filename = self.db.delete_paper(paper_id)
        if stored_filename is None:
            return False
        (self.upload_dir / stored_filename).unlink(missing_ok=True)
        shutil.rmtree(self.asset_dir / paper_id, ignore_errors=True)
        return True

    def consolidate_paper_duplicates(self, paper_id: str) -> tuple[dict[str, Any] | None, int]:
        imported = self.db.get_paper(paper_id)
        if imported is None:
            return None, 0
        title_key = normalize_paper_title(imported.get("title", ""))
        if not title_key:
            return imported, 0
        matches = [
            paper for paper in self.db.list_papers()
            if normalize_paper_title(paper.get("title", "")) == title_key
        ]
        if len(matches) < 2:
            return imported, 0
        canonical = max(
            matches,
            key=lambda paper: (*paper_quality_key(paper), int(paper["id"] == paper_id)),
        )
        tag_ids = list(dict.fromkeys(
            tag["id"] for paper in matches for tag in paper.get("tags", [])
        ))
        rating = max(int(paper.get("rating", 0)) for paper in matches)
        canonical = self.db.update_paper(canonical["id"], {"rating": rating}, tag_ids) or canonical
        removed = 0
        for duplicate in matches:
            if duplicate["id"] != canonical["id"] and self.remove_paper_record(duplicate["id"]):
                removed += 1
        return canonical, removed

    def deduplicate_library(self) -> int:
        removed = 0
        processed: set[str] = set()
        for paper in self.db.list_papers():
            title_key = normalize_paper_title(paper.get("title", ""))
            if not title_key or title_key in processed:
                continue
            processed.add(title_key)
            _, count = self.consolidate_paper_duplicates(paper["id"])
            removed += count
        return removed


class PaperVaultHandler(BaseHTTPRequestHandler):
    server: PaperVaultServer
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/health":
            self.send_json({"status": "ok", "version": __version__})
            return
        if path == "/api/papers":
            query = parse_qs(parsed.query)
            papers = self.server.db.list_papers(
                query.get("q", [""])[0],
                query.get("tag", [""])[0],
                query.get("sort", ["recent"])[0],
                query.get("summary", [""])[0],
                query.get("rating", [""])[0],
            )
            self.send_json(
                {"papers": papers, "total": len(papers), "facets": self.server.db.library_facets()}
            )
            return
        match = PAPER_FILE_RE.match(path)
        if match:
            self.serve_pdf(match.group(1))
            return
        match = PAPER_ASSET_RE.match(path)
        if match:
            self.serve_paper_asset(match.group(1), match.group(2))
            return
        match = PAPER_PAGE_IMAGE_RE.match(path)
        if match:
            self.serve_preview_page(match.group(1), int(match.group(2)))
            return
        match = PAPER_PAGE_TEXT_RE.match(path)
        if match:
            self.serve_preview_text(match.group(1), int(match.group(2)))
            return
        match = PAPER_ANNOTATIONS_RE.match(path)
        if match:
            if self.server.db.get_paper(match.group(1)) is None:
                self.send_error_json(HTTPStatus.NOT_FOUND, "Paper not found")
                return
            annotations = self.server.db.list_annotations(match.group(1))
            self.send_json({"annotations": annotations})
            return
        match = PAPER_ROUTE_RE.match(path)
        if match:
            paper = self.server.ensure_visual_assets(match.group(1))
            if paper is None:
                self.send_error_json(HTTPStatus.NOT_FOUND, "Paper not found")
                return
            self.send_json({"paper": paper})
            return
        if path == "/api/tags":
            self.send_json({"tags": self.server.db.list_tags()})
            return
        if path == "/api/settings":
            self.send_json({"settings": self.server.db.get_settings()})
            return
        if path == "/api/vocabulary":
            entries = self.server.db.list_vocabulary_entries()
            self.send_json({"entries": entries, "total": len(entries)})
            return
        if path.startswith("/api/"):
            self.send_error_json(HTTPStatus.NOT_FOUND, "API route not found")
            return
        self.serve_frontend(path)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/papers":
            self.create_paper()
            return
        if path == "/api/papers/batch":
            self.batch_papers()
            return
        match = PAPER_SUMMARY_RE.match(path)
        if match:
            self.generate_paper_summary(match.group(1))
            return
        if path == "/api/tags":
            self.create_tag()
            return
        if path == "/api/vocabulary":
            self.create_vocabulary_entry()
            return
        if path == "/api/translate":
            self.translate_selection()
            return
        match = PAPER_ANNOTATIONS_RE.match(path)
        if match:
            self.create_annotation(match.group(1))
            return
        self.send_error_json(HTTPStatus.NOT_FOUND, "API route not found")

    def do_PUT(self) -> None:
        path = urlparse(self.path).path
        match = PAPER_ROUTE_RE.match(path)
        if match:
            self.update_paper(match.group(1))
            return
        match = TAG_ROUTE_RE.match(path)
        if match:
            self.update_tag(match.group(1))
            return
        match = VOCABULARY_ROUTE_RE.match(path)
        if match:
            self.update_vocabulary_entry(match.group(1))
            return
        match = ANNOTATION_ROUTE_RE.match(path)
        if match:
            self.update_annotation(match.group(1))
            return
        if path == "/api/settings":
            self.update_settings()
            return
        self.send_error_json(HTTPStatus.NOT_FOUND, "API route not found")

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        match = PAPER_ROUTE_RE.match(path)
        if match:
            self.delete_paper(match.group(1))
            return
        match = TAG_ROUTE_RE.match(path)
        if match:
            deleted = self.server.db.delete_tag(match.group(1))
            if not deleted:
                self.send_error_json(HTTPStatus.NOT_FOUND, "Tag not found")
                return
            self.send_json({"deleted": True})
            return
        match = VOCABULARY_ROUTE_RE.match(path)
        if match:
            deleted = self.server.db.delete_vocabulary_entry(match.group(1))
            if not deleted:
                self.send_error_json(HTTPStatus.NOT_FOUND, "Vocabulary entry not found")
                return
            self.send_json({"deleted": True})
            return
        match = ANNOTATION_ROUTE_RE.match(path)
        if match:
            deleted = self.server.db.delete_annotation(match.group(1))
            if not deleted:
                self.send_error_json(HTTPStatus.NOT_FOUND, "Annotation not found")
                return
            self.send_json({"deleted": True})
            return
        self.send_error_json(HTTPStatus.NOT_FOUND, "API route not found")

    def create_paper(self) -> None:
        try:
            fields, files = self.read_multipart()
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        upload = files.get("file")
        if upload is None:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "PDF file is required")
            return
        filename, file_bytes = upload
        if not filename.lower().endswith(".pdf") or not file_bytes.startswith(b"%PDF-"):
            self.send_error_json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Only valid PDF files are accepted")
            return
        paper_id = str(uuid.uuid4())
        stored_filename = f"{paper_id}.pdf"
        destination = self.server.upload_dir / stored_filename
        destination.write_bytes(file_bytes)
        try:
            parsed = extract_pdf(destination)
        except Exception as exc:
            destination.unlink(missing_ok=True)
            self.send_error_json(HTTPStatus.UNPROCESSABLE_ENTITY, f"PDF parsing failed: {exc}")
            return
        title = fields.get("title", "").strip() or parsed["title"] or infer_title(parsed["text"], filename)
        authors = fields.get("authors", "").strip() or parsed["author"]
        try:
            year = parse_year(fields.get("publication_year", "")) or infer_publication_year(
                parsed["text"], filename
            )
            tag_ids = parse_tag_ids(fields.get("tag_ids", ""))
        except ValueError as exc:
            destination.unlink(missing_ok=True)
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        now = utc_now()
        summary_pairs: list[dict[str, Any]] = []
        summary_status = "pending"
        summary_provider = ""
        summary_model = ""
        summary_error = ""
        if fields.get("generate_summary", "1") != "0":
            try:
                settings = self.server.db.get_settings(include_secret=True)
                summary_pairs, summary_provider = generate_summary(title, parsed["text"], settings)
                if summary_provider == "openai_compatible":
                    summary_model = settings.get("model", "")
                summary_status = "draft" if summary_provider == "local" else "ready"
            except SummaryError as exc:
                summary_status = "error"
                summary_error, _ = friendly_model_error(str(exc))
        paper = {
            "id": paper_id,
            "title": title[:500],
            "authors": authors[:1000],
            "publication_year": year,
            "doi": fields.get("doi", "").strip()[:300],
            "original_filename": Path(filename).name[:500],
            "stored_filename": stored_filename,
            "file_size": len(file_bytes),
            "page_count": parsed["page_count"],
            "extracted_text": parsed["text"],
            "summary_pairs": summary_pairs,
            "visual_assets": [],
            "summary_status": summary_status,
            "summary_provider": summary_provider,
            "summary_model": summary_model,
            "summary_error": summary_error,
            "rating": 0,
            "created_at": now,
            "updated_at": now,
        }
        try:
            result = self.server.db.insert_paper(paper, tag_ids)
        except sqlite3.IntegrityError as exc:
            destination.unlink(missing_ok=True)
            self.send_error_json(HTTPStatus.BAD_REQUEST, f"Invalid paper data: {exc}")
            return
        result = self.server.ensure_visual_assets(paper_id) or result
        result, deduplicated_count = self.server.consolidate_paper_duplicates(paper_id)
        self.send_json(
            {"paper": result, "deduplicated_count": deduplicated_count},
            HTTPStatus.CREATED,
        )

    def update_paper(self, paper_id: str) -> None:
        try:
            payload = self.read_json()
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        fields: dict[str, Any] = {}
        try:
            for key in ("title", "authors", "doi"):
                if key in payload:
                    fields[key] = str(payload[key]).strip()
            if "publication_year" in payload:
                fields["publication_year"] = parse_year(payload["publication_year"])
            if "rating" in payload:
                fields["rating"] = parse_rating(payload["rating"])
            if "summary_pairs" in payload:
                fields["summary_pairs"] = validate_pairs(payload["summary_pairs"])
                fields["summary_status"] = "edited"
                fields["summary_provider"] = (
                    "curated" if payload.get("summary_provider") == "curated" else "manual"
                )
                fields["summary_model"] = ""
                fields["summary_error"] = ""
            tag_ids = parse_tag_ids(payload.get("tag_ids")) if "tag_ids" in payload else None
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        try:
            paper = self.server.db.update_paper(paper_id, fields, tag_ids)
        except sqlite3.IntegrityError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, f"Invalid paper data: {exc}")
            return
        if paper is None:
            self.send_error_json(HTTPStatus.NOT_FOUND, "Paper not found")
            return
        self.send_json({"paper": paper})

    def batch_papers(self) -> None:
        try:
            payload = self.read_json()
            raw_ids = payload.get("paper_ids")
            if not isinstance(raw_ids, list):
                raise ValueError("paper_ids must be a list")
            paper_ids = list(dict.fromkeys(str(item) for item in raw_ids if item))
            if not paper_ids:
                raise ValueError("Select at least one paper")
            if len(paper_ids) > 500:
                raise ValueError("A batch can contain at most 500 papers")
            action = str(payload.get("action", ""))
            if action not in {"add_tags", "remove_tags", "set_rating", "delete"}:
                raise ValueError("Unsupported batch action")
            requested_tag_ids = parse_tag_ids(payload.get("tag_ids"))
            known_tag_ids = {tag["id"] for tag in self.server.db.list_tags()}
            if action in {"add_tags", "remove_tags"}:
                if not requested_tag_ids:
                    raise ValueError("Select at least one tag")
                if any(tag_id not in known_tag_ids for tag_id in requested_tag_ids):
                    raise ValueError("One or more tags do not exist")
            rating = parse_rating(payload.get("rating")) if action == "set_rating" else None
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return

        updated: list[dict[str, Any]] = []
        deleted: list[str] = []
        missing: list[str] = []
        requested_tags = set(requested_tag_ids)
        for paper_id in paper_ids:
            paper = self.server.db.get_paper(paper_id)
            if paper is None:
                missing.append(paper_id)
                continue
            if action == "delete":
                if self.server.remove_paper_record(paper_id):
                    deleted.append(paper_id)
                continue
            if action == "set_rating":
                result = self.server.db.update_paper(paper_id, {"rating": rating}, None)
            else:
                current_tag_ids = [tag["id"] for tag in paper.get("tags", [])]
                if action == "add_tags":
                    next_tag_ids = list(dict.fromkeys(current_tag_ids + requested_tag_ids))
                else:
                    next_tag_ids = [tag_id for tag_id in current_tag_ids if tag_id not in requested_tags]
                result = self.server.db.update_paper(paper_id, {}, next_tag_ids)
            if result is not None:
                updated.append(result)

        self.send_json(
            {
                "action": action,
                "updated": updated,
                "deleted": deleted,
                "missing": missing,
                "processed_count": len(updated) + len(deleted),
            }
        )

    def create_annotation(self, paper_id: str) -> None:
        paper = self.server.db.get_paper(paper_id)
        if paper is None:
            self.send_error_json(HTTPStatus.NOT_FOUND, "Paper not found")
            return
        try:
            payload = self.read_json()
            page = int(payload.get("page", 0))
            start_word = int(payload.get("start_word", -1))
            end_word = int(payload.get("end_word", -1))
            color = validate_annotation_color(payload.get("color", "yellow"))
            if page < 1 or page > int(paper.get("page_count", 0)):
                raise ValueError("Annotation page is outside the PDF")
            if start_word < 0 or end_word < start_word or end_word > 200000:
                raise ValueError("Invalid annotation word range")
        except (TypeError, ValueError) as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        annotation = self.server.db.create_annotation(
            {
                "id": str(uuid.uuid4()),
                "paper_id": paper_id,
                "page": page,
                "start_word": start_word,
                "end_word": end_word,
                "selected_text": str(payload.get("selected_text", "")).strip()[:4000],
                "note": str(payload.get("note", "")).strip()[:4000],
                "color": color,
            }
        )
        self.send_json({"annotation": annotation}, HTTPStatus.CREATED)

    def update_annotation(self, annotation_id: str) -> None:
        try:
            payload = self.read_json()
            color = validate_annotation_color(payload.get("color", "yellow"))
            note = str(payload.get("note", "")).strip()[:4000]
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        annotation = self.server.db.update_annotation(annotation_id, note, color)
        if annotation is None:
            self.send_error_json(HTTPStatus.NOT_FOUND, "Annotation not found")
            return
        self.send_json({"annotation": annotation})

    def delete_paper(self, paper_id: str) -> None:
        if not self.server.remove_paper_record(paper_id):
            self.send_error_json(HTTPStatus.NOT_FOUND, "Paper not found")
            return
        self.send_json({"deleted": True})

    def generate_paper_summary(self, paper_id: str) -> None:
        paper = self.server.db.get_paper(paper_id, include_text=True)
        if paper is None:
            self.send_error_json(HTTPStatus.NOT_FOUND, "Paper not found")
            return
        self.server.ensure_visual_assets(paper_id)
        self.server.db.update_paper(paper_id, {"summary_status": "generating", "summary_error": ""}, None)
        settings = self.server.db.get_settings(include_secret=True)
        try:
            pairs, provider = generate_summary(
                paper["title"], paper.get("extracted_text", ""),
                settings,
            )
        except SummaryError as exc:
            message, error_code = friendly_model_error(str(exc))
            previous_status = str(paper.get("summary_status", "pending"))
            if previous_status in {"generating", "error"}:
                if paper.get("summary_pairs"):
                    previous_status = (
                        "ready"
                        if paper.get("summary_provider") == "openai_compatible"
                        else "edited"
                    )
                else:
                    previous_status = "error"
            updated = self.server.db.update_paper(
                paper_id,
                {"summary_status": previous_status, "summary_error": message},
                None,
            )
            self.send_json(
                {"paper": updated, "error": message, "error_code": error_code},
                HTTPStatus.BAD_GATEWAY,
            )
            return
        status = "draft" if provider == "local" else "ready"
        updated = self.server.db.update_paper(
            paper_id,
            {
                "summary_pairs": pairs,
                "summary_status": status,
                "summary_provider": provider,
                "summary_model": settings.get("model", "") if provider == "openai_compatible" else "",
                "summary_error": "",
            },
            None,
        )
        self.send_json({"paper": updated})

    def serve_paper_asset(self, paper_id: str, filename: str) -> None:
        paper = self.server.ensure_visual_assets(paper_id)
        if paper is None:
            self.send_error_json(HTTPStatus.NOT_FOUND, "Paper not found")
            return
        known_files = {str(asset.get("filename", "")) for asset in paper.get("visual_assets", [])}
        if filename not in known_files:
            self.send_error_json(HTTPStatus.NOT_FOUND, "Visual asset not found")
            return
        path = self.server.asset_dir / paper_id / filename
        if not path.is_file():
            self.send_error_json(HTTPStatus.NOT_FOUND, "Visual asset file is missing")
            return
        content = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "private, max-age=86400")
        self.end_headers()
        self.wfile.write(content)

    def serve_preview_page(self, paper_id: str, page_number: int) -> None:
        paper = self.server.db.get_paper(paper_id)
        if paper is None:
            self.send_error_json(HTTPStatus.NOT_FOUND, "Paper not found")
            return
        if page_number < 1 or page_number > int(paper.get("page_count", 0)):
            self.send_error_json(HTTPStatus.NOT_FOUND, "PDF page not found")
            return
        pdf_path = self.server.upload_dir / paper["stored_filename"]
        output_path = self.server.asset_dir / paper_id / f"preview-{page_number}.png"
        try:
            if not output_path.is_file():
                render_preview_page(pdf_path, output_path, page_number)
            content = output_path.read_bytes()
        except (OSError, RuntimeError, ValueError) as exc:
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, f"PDF page rendering failed: {exc}")
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "private, max-age=86400")
        self.end_headers()
        self.wfile.write(content)

    def serve_preview_text(self, paper_id: str, page_number: int) -> None:
        paper = self.server.db.get_paper(paper_id)
        if paper is None:
            self.send_error_json(HTTPStatus.NOT_FOUND, "Paper not found")
            return
        if page_number < 1 or page_number > int(paper.get("page_count", 0)):
            self.send_error_json(HTTPStatus.NOT_FOUND, "PDF page not found")
            return
        pdf_path = self.server.upload_dir / paper["stored_filename"]
        try:
            payload = extract_preview_words(pdf_path, page_number)
        except (OSError, RuntimeError, ValueError) as exc:
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, f"PDF text extraction failed: {exc}")
            return
        self.send_json(payload)

    def create_tag(self) -> None:
        try:
            payload = self.read_json()
            name = str(payload.get("name", "")).strip()
            color = validate_color(payload.get("color", "#28786f"))
            if not name:
                raise ValueError("Tag name is required")
            tag = self.server.db.create_tag(str(uuid.uuid4()), name[:80], color)
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except sqlite3.IntegrityError:
            self.send_error_json(HTTPStatus.CONFLICT, "A tag with this name already exists")
            return
        self.send_json({"tag": tag}, HTTPStatus.CREATED)

    def update_tag(self, tag_id: str) -> None:
        try:
            payload = self.read_json()
            name = str(payload.get("name", "")).strip()
            color = validate_color(payload.get("color", "#28786f"))
            if not name:
                raise ValueError("Tag name is required")
            tag = self.server.db.update_tag(tag_id, name[:80], color)
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except sqlite3.IntegrityError:
            self.send_error_json(HTTPStatus.CONFLICT, "A tag with this name already exists")
            return
        if tag is None:
            self.send_error_json(HTTPStatus.NOT_FOUND, "Tag not found")
            return
        self.send_json({"tag": tag})

    def update_settings(self) -> None:
        try:
            payload = self.read_json()
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        provider = payload.get("provider")
        if provider is not None and provider not in {"local", "openai_compatible"}:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "Invalid LLM provider")
            return
        self.send_json({"settings": self.server.db.update_settings(payload)})

    def create_vocabulary_entry(self) -> None:
        try:
            payload = self.read_json()
            term_en = required_text(payload.get("term_en"), "English term", 240)
            translation_zh = required_text(payload.get("translation_zh"), "Chinese translation", 500)
            status = validate_vocabulary_status(payload.get("status", "learning"))
            note = str(payload.get("note", "")).strip()[:2000]
            paper_id = str(payload.get("paper_id", "")).strip() or None
            source_pair_index = parse_pair_index(payload.get("source_pair_index"))
            source_page = parse_source_page(payload.get("source_page"))
            phonetic_us = str(payload.get("phonetic_us", "")).strip()[:200] or american_ipa(term_en)
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return

        context_en = ""
        context_zh = ""
        paper_title = ""
        if paper_id:
            paper = self.server.db.get_paper(paper_id)
            if paper is None:
                self.send_error_json(HTTPStatus.BAD_REQUEST, "Source paper not found")
                return
            paper_title = paper["title"]
            pairs = paper.get("summary_pairs", [])
            if source_pair_index is not None:
                if source_pair_index >= len(pairs):
                    self.send_error_json(HTTPStatus.BAD_REQUEST, "Source paragraph not found")
                    return
                context_en = str(pairs[source_pair_index].get("en", ""))[:4000]
                context_zh = str(pairs[source_pair_index].get("zh", ""))[:4000]
            elif source_page is not None:
                if source_page > int(paper.get("page_count", 0)):
                    self.send_error_json(HTTPStatus.BAD_REQUEST, "Source PDF page not found")
                    return
                context_en = str(payload.get("context_en", "")).strip()[:4000]
                context_zh = str(payload.get("context_zh", "")).strip()[:4000]

        entry, created = self.server.db.create_vocabulary_entry(
            {
                "id": str(uuid.uuid4()),
                "term_en": term_en,
                "translation_zh": translation_zh,
                "context_en": context_en,
                "context_zh": context_zh,
                "paper_id": paper_id,
                "paper_title": paper_title,
                "source_pair_index": source_pair_index,
                "source_page": source_page,
                "phonetic_us": phonetic_us,
                "note": note,
                "status": status,
            }
        )
        self.send_json(
            {"entry": entry, "created": created},
            HTTPStatus.CREATED if created else HTTPStatus.OK,
        )

    def update_vocabulary_entry(self, entry_id: str) -> None:
        try:
            payload = self.read_json()
            fields: dict[str, Any] = {}
            if "term_en" in payload:
                fields["term_en"] = required_text(payload["term_en"], "English term", 240)
            if "translation_zh" in payload:
                fields["translation_zh"] = required_text(
                    payload["translation_zh"], "Chinese translation", 500
                )
            if "phonetic_us" in payload:
                fields["phonetic_us"] = str(payload["phonetic_us"]).strip()[:200]
            elif "term_en" in fields:
                fields["phonetic_us"] = american_ipa(fields["term_en"])
            if "note" in payload:
                fields["note"] = str(payload["note"]).strip()[:2000]
            if "status" in payload:
                fields["status"] = validate_vocabulary_status(payload["status"])
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        entry = self.server.db.update_vocabulary_entry(entry_id, fields)
        if entry is None:
            self.send_error_json(HTTPStatus.NOT_FOUND, "Vocabulary entry not found")
            return
        self.send_json({"entry": entry})

    def translate_selection(self) -> None:
        try:
            payload = self.read_json()
            source_text = required_text(payload.get("text"), "English text", 240)
            context = str(payload.get("context", "")).strip()[:3000]
            known_context_zh = str(payload.get("context_zh", "")).strip()[:3000]
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        if not re.search(r"[A-Za-z]", source_text):
            self.send_error_json(HTTPStatus.BAD_REQUEST, "English text is required")
            return

        vocabulary = self.server.db.find_vocabulary_translation(source_text)
        if vocabulary is not None:
            self.send_json(
                {
                    "translation_zh": vocabulary["translation_zh"],
                    "definition_zh": "",
                    "context_translation_zh": vocabulary.get("context_zh", ""),
                    "phonetic_us": vocabulary.get("phonetic_us", "") or american_ipa(source_text),
                    "note_zh": "",
                    "source": "wordbook",
                }
            )
            return

        cache_material = f"v3\0{source_text.casefold()}\0{context}\0{known_context_zh}".encode("utf-8")
        cache_key = hashlib.sha256(cache_material).hexdigest()
        cached = self.server.db.get_cached_translation(cache_key)
        if cached is not None:
            self.send_json(
                {
                    "translation_zh": cached["translation_zh"],
                    "definition_zh": cached.get("definition_zh", ""),
                    "context_translation_zh": cached.get("context_translation_zh", ""),
                    "phonetic_us": american_ipa(source_text),
                    "note_zh": "",
                    "source": "cache",
                }
            )
            return

        source = "offline"
        try:
            translated = translate_english_offline(source_text, context, known_context_zh)
        except OfflineTranslationError as offline_exc:
            try:
                translated = translate_english(
                    source_text, context, self.server.db.get_settings(include_secret=True)
                )
                source = "model"
            except SummaryError as model_exc:
                message, _ = friendly_model_error(str(model_exc))
                self.send_error_json(
                    HTTPStatus.BAD_GATEWAY,
                    f"离线翻译不可用（{offline_exc}）；{message}",
                )
                return
        translated.setdefault("definition_zh", "")
        translated.setdefault("context_translation_zh", known_context_zh)
        translated["phonetic_us"] = translated.get("phonetic_us") or american_ipa(source_text)
        translated["note_zh"] = ""
        self.server.db.cache_translation(
            cache_key,
            source_text,
            translated["translation_zh"],
            "",
            translated["definition_zh"],
            translated["context_translation_zh"],
        )
        self.send_json({**translated, "source": source})

    def serve_pdf(self, paper_id: str) -> None:
        paper = self.server.db.get_paper(paper_id)
        if paper is None:
            self.send_error_json(HTTPStatus.NOT_FOUND, "Paper not found")
            return
        path = self.server.upload_dir / paper["stored_filename"]
        if not path.is_file():
            self.send_error_json(HTTPStatus.NOT_FOUND, "PDF file is missing")
            return
        file_size = path.stat().st_size
        range_header = self.headers.get("Range", "")
        start, end = 0, file_size - 1
        status = HTTPStatus.OK
        if range_header.startswith("bytes="):
            try:
                range_value = range_header.removeprefix("bytes=").split(",", 1)[0]
                start_text, end_text = range_value.split("-", 1)
                if start_text:
                    start = int(start_text)
                    end = int(end_text) if end_text else end
                else:
                    suffix = int(end_text)
                    start = max(0, file_size - suffix)
                end = min(end, file_size - 1)
                if start < 0 or start > end:
                    raise ValueError
                status = HTTPStatus.PARTIAL_CONTENT
            except ValueError:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{file_size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Disposition", f"inline; filename*=UTF-8''{quote_filename(paper['original_filename'])}")
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        self.send_header("Cache-Control", "private, max-age=3600")
        self.end_headers()
        with path.open("rb") as file:
            file.seek(start)
            remaining = length
            while remaining:
                chunk = file.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def serve_frontend(self, request_path: str) -> None:
        relative = unquote(request_path).lstrip("/") or "index.html"
        candidate = (self.server.frontend_dir / relative).resolve()
        if self.server.frontend_dir not in candidate.parents and candidate != self.server.frontend_dir:
            self.send_error_json(HTTPStatus.FORBIDDEN, "Invalid path")
            return
        if not candidate.is_file():
            candidate = self.server.frontend_dir / "index.html"
        content = candidate.read_bytes()
        mime_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if candidate.suffix in {".html", ".css", ".js"}:
            mime_type = f"{mime_type}; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(content)

    def read_json(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0 or content_length > 2 * 1024 * 1024:
            raise ValueError("Invalid JSON body size")
        try:
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid JSON body") from exc
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload

    def read_multipart(self) -> tuple[dict[str, str], dict[str, tuple[str, bytes]]]:
        content_type = self.headers.get("Content-Type", "")
        if not content_type.startswith("multipart/form-data"):
            raise ValueError("Expected multipart form data")
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0 or content_length > MAX_UPLOAD_BYTES:
            raise ValueError("PDF must be smaller than 100 MB")
        body = self.rfile.read(content_length)
        envelope = (
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("ascii") + body
        )
        message = BytesParser(policy=default).parsebytes(envelope)
        if not message.is_multipart():
            raise ValueError("Malformed multipart form data")
        fields: dict[str, str] = {}
        files: dict[str, tuple[str, bytes]] = {}
        for part in message.iter_parts():
            if part.get_content_disposition() != "form-data":
                continue
            name = part.get_param("name", header="content-disposition")
            if not name:
                continue
            filename = part.get_filename()
            payload = part.get_payload(decode=True) or b""
            if filename is not None:
                files[name] = (Path(filename).name, payload)
            else:
                charset = part.get_content_charset() or "utf-8"
                fields[name] = payload.decode(charset, errors="replace")
        return fields, files

    def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status: HTTPStatus, message: str) -> None:
        self.send_json({"error": message}, status)

    def log_message(self, format_string: str, *args: Any) -> None:
        if sys.stdout is not None:
            sys.stdout.write(f"[{self.log_date_time_string()}] {format_string % args}\n")


def parse_year(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        year = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Publication year must be a number") from exc
    if year < 1500 or year > 2200:
        raise ValueError("Publication year is outside the valid range")
    return year


def parse_rating(value: Any) -> int:
    try:
        rating = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Rating must be a number from 0 to 3") from exc
    if rating < 0 or rating > 3:
        raise ValueError("Rating must be between 0 and 3")
    return rating


def validate_annotation_color(value: Any) -> str:
    color = str(value or "yellow")
    if color not in ANNOTATION_COLORS:
        raise ValueError("Invalid annotation color")
    return color


def normalize_paper_title(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(character for character in normalized if character.isalnum())


def paper_quality_key(paper: dict[str, Any]) -> tuple[int, str, str]:
    has_summary = bool(paper.get("summary_pairs")) and paper.get("summary_status") in {
        "ready", "draft", "edited"
    }
    return (
        int(has_summary),
        str(paper.get("updated_at", "")),
        str(paper.get("created_at", "")),
    )


def friendly_model_error(message: str) -> tuple[str, str]:
    lowered = message.casefold()
    if "insufficient balance" in lowered or "(402)" in lowered:
        return (
            "模型服务账户余额不足，当前摘要没有被覆盖。请充值或更换 API Key 后重试。",
            "insufficient_balance",
        )
    if "(401)" in lowered or "unauthorized" in lowered or "invalid api key" in lowered:
        return "API Key 无效或已过期，请在模型设置中更新后重试。", "invalid_api_key"
    if "timed out" in lowered or "timeout" in lowered:
        return "模型响应超时，当前摘要没有被覆盖，请稍后重试。", "model_timeout"
    if "connection" in lowered or "urlopen" in lowered:
        return "无法连接模型服务，请检查 VPN、网络和 API 地址。", "model_unreachable"
    return "模型生成失败，当前摘要没有被覆盖。请检查模型设置后重试。", "model_error"


def parse_tag_ids(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = [item for item in value.split(",") if item]
    else:
        parsed = value
    if not isinstance(parsed, list):
        raise ValueError("tag_ids must be a list")
    return [str(item) for item in parsed]


def required_text(value: Any, label: str, max_length: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    return text[:max_length]


def validate_vocabulary_status(value: Any) -> str:
    status = str(value or "learning")
    if status not in VOCABULARY_STATUSES:
        raise ValueError("Invalid vocabulary status")
    return status


def parse_pair_index(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        index = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Source paragraph index must be a number") from exc
    if index < 0:
        raise ValueError("Source paragraph index must not be negative")
    return index


def parse_source_page(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        page = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Source PDF page must be a number") from exc
    if page < 1:
        raise ValueError("Source PDF page must be positive")
    return page


def validate_pairs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("summary_pairs must be a list")
    pairs: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Each summary pair must be an object")
        en = str(item.get("en", "")).strip()
        zh = str(item.get("zh", "")).strip()
        if en or zh:
            terms: list[dict[str, str]] = []
            for term in item.get("terms", []):
                if not isinstance(term, dict):
                    continue
                term_en = str(term.get("en", "")).strip()
                term_zh = str(term.get("zh", "")).strip()
                if term_en and term_zh:
                    terms.append({"en": term_en, "zh": term_zh})
            page_refs: list[int] = []
            for page in item.get("page_refs", []):
                try:
                    page_number = int(page)
                except (TypeError, ValueError):
                    continue
                if page_number > 0:
                    page_refs.append(page_number)
            pairs.append(
                {
                    "section_en": str(item.get("section_en", "")).strip(),
                    "section_zh": str(item.get("section_zh", "")).strip(),
                    "en": en,
                    "zh": zh,
                    "terms": terms[:16],
                    "page_refs": list(dict.fromkeys(page_refs))[:10],
                }
            )
    return pairs[:60]


def validate_color(value: Any) -> str:
    color = str(value)
    if not COLOR_RE.match(color):
        raise ValueError("Tag color must be a six-digit hex value")
    return color.lower()


def quote_filename(filename: str) -> str:
    from urllib.parse import quote

    return quote(filename, safe="")


def main() -> None:
    parser = argparse.ArgumentParser(description="PaperVault local web server")
    parser.add_argument("--host", default=os.environ.get("PAPER_VAULT_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PAPER_VAULT_PORT", "8765")))
    parser.add_argument("--data-dir", type=Path, default=None)
    args = parser.parse_args()

    project_dir = Path(__file__).resolve().parent.parent
    data_dir = args.data_dir.resolve() if args.data_dir else project_dir / "data"
    server = PaperVaultServer((args.host, args.port), project_dir / "frontend", data_dir)
    print(f"PaperVault is running at http://{args.host}:{args.port}")
    print(f"Local data: {data_dir}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nPaperVault stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
