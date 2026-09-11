from __future__ import annotations

import argparse
import hashlib
import json
import math
import mimetypes
import os
import re
import shutil
import sqlite3
import sys
import threading
import time
import uuid
from email.parser import BytesParser
from email.policy import default
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__
from .analysis import (
    QA_PROMPT_VERSION,
    analysis_input_hash,
    answer_with_citations,
    build_text_chunks,
    is_paper_overview_question,
    provider_available,
    representative_chunks,
)
from .config import DATA_DIR_ENV, load_local_environment
from .database import SCHEMA_VERSION, Database, normalize_title_key, utc_now
from .doubao import fetch_doubao_markdown, markdown_blocks, translate_markdown_to_english
from .deep_summary import (
    DEEP_SUMMARY_PROMPT_VERSION,
    analysis_model,
    generate_english_report,
    translate_report_blocks,
    translation_model,
)
from .llm import SummaryError, generate_summary, translate_english
from .offline_translation import OfflineTranslationError, translate_english_offline
from .pronunciation import american_ipa
from .pdf_parser import (
    extract_pdf,
    extract_page_texts,
    extract_preview_words,
    extract_visual_pages,
    infer_title,
    infer_publication_year,
    render_preview_page,
)
from .resource_package import (
    MAX_PACKAGE_BYTES,
    ResourcePackageError,
    estimate_resource_package,
    export_resource_package,
    inspect_resource_package,
    restore_resource_package,
)


MAX_UPLOAD_BYTES = 100 * 1024 * 1024
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
PAPER_ROUTE_RE = re.compile(r"^/api/papers/([0-9a-f-]+)$")
PAPER_FILE_RE = re.compile(r"^/api/papers/([0-9a-f-]+)/file$")
PAPER_ASSET_RE = re.compile(
    r"^/api/papers/([0-9a-f-]+)/assets/((?:page-[0-9]+|visual-[0-9]+-(?:figure|table)-[0-9a-z]+-[0-9a-f]{8})\.png)$"
)
PAPER_PAGE_IMAGE_RE = re.compile(r"^/api/papers/([0-9a-f-]+)/pages/([0-9]+)\.png$")
PAPER_PAGE_TEXT_RE = re.compile(r"^/api/papers/([0-9a-f-]+)/pages/([0-9]+)/text$")
PAPER_SUMMARY_RE = re.compile(r"^/api/papers/([0-9a-f-]+)/generate-summary$")
PAPER_SUMMARY_TRANSLATION_RE = re.compile(r"^/api/papers/([0-9a-f-]+)/translate-summary$")
TRASH_RESTORE_RE = re.compile(r"^/api/trash/([0-9a-f-]+)/restore$")
TAG_ROUTE_RE = re.compile(r"^/api/tags/([0-9a-f-]+)$")
VOCABULARY_ROUTE_RE = re.compile(r"^/api/vocabulary/([0-9a-f-]+)$")
PAPER_ANNOTATIONS_RE = re.compile(r"^/api/papers/([0-9a-f-]+)/annotations$")
ANNOTATION_ROUTE_RE = re.compile(r"^/api/annotations/([0-9a-f-]+)$")
PAPER_VARIANTS_RE = re.compile(r"^/api/papers/([0-9a-f-]+)/summary-variants$")
PAPER_NOTES_RE = re.compile(r"^/api/papers/([0-9a-f-]+)/notes$")
VARIANT_TRANSLATION_RE = re.compile(r"^/api/summary-variants/([0-9a-f-]+)/translate-english$")
NOTE_QUOTES_RE = re.compile(r"^/api/papers/([0-9a-f-]+)/notes/quotes$")
NOTE_QUOTE_ROUTE_RE = re.compile(r"^/api/note-quotes/([0-9a-f-]+)$")
RESOURCE_PACKAGE_INCOMING_RE = re.compile(r"^/api/resource-packages/incoming/([0-9a-f-]+)$")
VOCABULARY_STATUSES = {"learning", "mastered"}
ANNOTATION_COLORS = {"yellow", "green", "blue", "coral"}


class PaperVaultServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], frontend_dir: Path, data_dir: Path):
        self.frontend_dir = frontend_dir.resolve()
        self.project_dir = self.frontend_dir.parent
        self.data_dir = data_dir.resolve()
        self.upload_dir = self.data_dir / "uploads"
        self.asset_dir = self.data_dir / "assets"
        self.model_directory = self.data_dir / "models" / "translate-en_zh-1_9"
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.asset_dir.mkdir(parents=True, exist_ok=True)
        self.resource_backup_dir = self.data_dir / "backups"
        self.resource_incoming_dir = self.resource_backup_dir / "incoming"
        self.resource_incoming_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(self.data_dir / "paper-vault.db")
        self.db.initialize()
        self.deduplicate_library()
        self._job_stop = threading.Event()
        self._job_wakeup = threading.Event()
        self._job_thread: threading.Thread | None = None
        self.resource_package_lock = threading.RLock()
        super().__init__(address, PaperVaultHandler)

    def local_config_values(self) -> dict[str, str]:
        return load_local_config(self.project_dir)

    def local_config_metadata(self) -> dict[str, Any]:
        values = self.local_config_values()
        return {
            "available": bool(values),
            "has_api_key": bool(values.get("api_key")),
            "keys": sorted(key for key in values if key != "api_key"),
        }

    def serve_forever(self, poll_interval: float = 0.5) -> None:
        try:
            super().serve_forever(poll_interval=poll_interval)
        finally:
            self.stop_job_worker()

    def server_close(self) -> None:
        self.stop_job_worker()
        super().server_close()

    def start_job_worker(self) -> None:
        if self._job_thread is not None and self._job_thread.is_alive():
            return
        self._job_stop.clear()
        self._job_thread = threading.Thread(
            target=self._job_loop,
            name="papervault-analysis",
            daemon=True,
        )
        self._job_thread.start()

    def stop_job_worker(self) -> None:
        self._job_stop.set()
        self._job_wakeup.set()
        if (
            self._job_thread is not None
            and self._job_thread.is_alive()
            and threading.current_thread() is not self._job_thread
        ):
            self._job_thread.join(timeout=5)

    def notify_job_worker(self) -> None:
        self._job_wakeup.set()

    def _job_loop(self) -> None:
        while not self._job_stop.is_set():
            job = self.db.claim_next_analysis_job()
            if job is None:
                self._job_wakeup.wait(0.5)
                self._job_wakeup.clear()
                continue
            self.execute_analysis_job(job)

    def execute_analysis_job(self, job: dict[str, Any]) -> None:
        started = time.perf_counter()
        paper_id = str(job.get("paper_id") or "")
        prompt_version = str(job.get("prompt_version") or "")
        input_hash = str(job.get("input_hash") or "")
        settings = self.db.get_settings(include_secret=True)
        try:
            paper = self.db.get_paper(paper_id, include_text=True)
            if paper is None:
                raise SummaryError("Paper is unavailable or in the recycle bin")
            chunks = self.ensure_text_index(paper_id)
            input_hash = analysis_input_hash(paper, job["job_type"], self.asset_dir)
            raise SummaryError("Analysis jobs are disabled")
            duration_ms = max(0, int((time.perf_counter() - started) * 1000))
            run_id = str(uuid.uuid4())
            self.db.create_analysis_run(
                {
                    "id": run_id,
                    "paper_id": paper_id,
                    "analysis_type": job["job_type"],
                    "status": "succeeded",
                    "content": content,
                    "provider": provider,
                    "model": settings.get("model", "") if provider == "openai_compatible" else "",
                    "input_hash": input_hash,
                    "prompt_version": prompt_version,
                    "token_count": token_count,
                    "duration_ms": duration_ms,
                }
            )
            self.db.complete_analysis_job(job["id"], run_id, token_count, duration_ms)
        except Exception as exc:
            duration_ms = max(0, int((time.perf_counter() - started) * 1000))
            message = friendly_model_error(str(exc))[0] if isinstance(exc, SummaryError) else str(exc)
            if paper_id and self.db.get_paper(paper_id) is not None:
                self.db.create_analysis_run(
                    {
                        "id": str(uuid.uuid4()),
                        "paper_id": paper_id,
                        "analysis_type": job["job_type"],
                        "status": "failed",
                        "content": {},
                        "provider": str(job.get("provider") or ""),
                        "model": str(job.get("model") or ""),
                        "input_hash": input_hash,
                        "prompt_version": prompt_version,
                        "duration_ms": duration_ms,
                        "error": message,
                    }
                )
            self.db.fail_analysis_job(job["id"], message, duration_ms)

    def ensure_text_index(self, paper_id: str) -> list[dict[str, Any]]:
        if self.db.has_paper_chunks(paper_id):
            return self.db.list_paper_chunks(paper_id)
        paper = self.db.get_paper(paper_id, include_text=True)
        if paper is None:
            return []
        pdf_path = self.upload_dir / paper["stored_filename"]
        try:
            page_texts = extract_page_texts(pdf_path)
        except Exception:
            page_texts = [{"page": 1, "text": paper.get("extracted_text", "")}]
        chunks = build_text_chunks(paper_id, page_texts)
        self.db.replace_paper_chunks(paper_id, chunks)
        return self.db.list_paper_chunks(paper_id)

    def queue_analysis_job(
        self, paper_id: str, job_type: str
    ) -> tuple[dict[str, Any] | None, bool]:
        paper = self.db.get_paper(paper_id, include_text=True)
        if paper is None:
            return None, False
        return None, False

    def ensure_visual_assets(
        self, paper_id: str, refresh_legacy: bool = False
    ) -> dict[str, Any] | None:
        paper = self.db.get_paper(paper_id)
        if paper is None:
            return paper
        assets = paper.get("visual_assets", [])
        modern_assets = bool(assets) and all(
            asset.get("kind") in {"figure", "table"} and asset.get("caption")
            for asset in assets
        )
        if assets and (not refresh_legacy or modern_assets):
            return paper
        pdf_path = self.upload_dir / paper["stored_filename"]
        if not pdf_path.is_file():
            return paper
        try:
            refreshed_assets = extract_visual_pages(pdf_path, self.asset_dir / paper_id)
        except Exception:
            return paper
        if refreshed_assets or refresh_legacy:
            return self.db.update_paper(paper_id, {"visual_assets": refreshed_assets}, None)
        return paper

    def remove_paper_record(self, paper_id: str) -> bool:
        stored_filename = self.db.delete_paper(paper_id)
        return stored_filename is not None

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
        if path == "/api/trash":
            papers = self.server.db.list_trash()
            self.send_json({"papers": papers, "total": len(papers)})
            return
        if path == "/api/search/chunks":
            query = parse_qs(parsed.query)
            term = query.get("q", [""])[0].strip()
            paper_id = query.get("paper_id", [""])[0].strip() or None
            if not term:
                self.send_error_json(HTTPStatus.BAD_REQUEST, "Search query is required")
                return
            if paper_id:
                if self.server.db.get_paper(paper_id) is None:
                    self.send_error_json(HTTPStatus.NOT_FOUND, "Paper not found")
                    return
                self.server.ensure_text_index(paper_id)
            else:
                for paper in self.server.db.list_papers():
                    self.server.ensure_text_index(paper["id"])
            chunks = self.server.db.search_paper_chunks(term, paper_id, limit=20)
            self.send_json({"chunks": self.chunk_sources(chunks), "total": len(chunks)})
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
            query = parse_qs(parsed.query)
            raw_scale = query.get("scale", [""])[0].strip()
            try:
                scale = normalize_preview_scale(raw_scale) if raw_scale else None
            except ValueError as exc:
                self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self.serve_preview_page(match.group(1), int(match.group(2)), scale)
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
        match = PAPER_VARIANTS_RE.match(path)
        if match:
            if self.server.db.get_paper(match.group(1)) is None:
                self.send_error_json(HTTPStatus.NOT_FOUND, "Paper not found")
                return
            self.send_json({"variants": self.server.db.list_summary_variants(match.group(1))})
            return
        match = PAPER_NOTES_RE.match(path)
        if match:
            if self.server.db.get_paper(match.group(1)) is None:
                self.send_error_json(HTTPStatus.NOT_FOUND, "Paper not found")
                return
            self.send_json({"note": self.server.db.get_or_create_note(match.group(1))})
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
            self.send_json(
                {
                    "settings": self.server.db.get_settings(),
                    "local_config": self.server.local_config_metadata(),
                }
            )
            return
        if path == "/api/vocabulary":
            entries = self.server.db.list_vocabulary_entries()
            self.send_json({"entries": entries, "total": len(entries)})
            return
        if path == "/api/resource-packages/estimate":
            self.resource_package_estimate()
            return
        if path.startswith("/api/"):
            self.send_error_json(HTTPStatus.NOT_FOUND, "API route not found")
            return
        self.serve_frontend(path)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/resource-packages/export":
            self.export_resource_package()
            return
        if path == "/api/resource-packages/inspect":
            self.inspect_resource_package_upload()
            return
        if path == "/api/resource-packages/restore":
            self.restore_resource_package()
            return
        if path == "/api/settings/import-local":
            self.import_local_settings()
            return
        if path == "/api/papers":
            self.create_paper()
            return
        if path == "/api/papers/batch":
            self.batch_papers()
            return
        match = TRASH_RESTORE_RE.match(path)
        if match:
            paper = self.server.db.restore_paper(match.group(1))
            if paper is None:
                self.send_error_json(HTTPStatus.NOT_FOUND, "Trashed paper not found")
                return
            self.send_json({"paper": paper})
            return
        match = PAPER_SUMMARY_RE.match(path)
        if match:
            self.generate_paper_summary(match.group(1))
            return
        match = PAPER_SUMMARY_TRANSLATION_RE.match(path)
        if match:
            self.translate_paper_summary(match.group(1))
            return
        match = PAPER_VARIANTS_RE.match(path)
        if match:
            self.import_summary_variant(match.group(1))
            return
        match = NOTE_QUOTES_RE.match(path)
        if match:
            self.create_note_quote(match.group(1))
            return
        match = VARIANT_TRANSLATION_RE.match(path)
        if match:
            self.translate_summary_variant(match.group(1))
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
        match = PAPER_NOTES_RE.match(path)
        if match:
            self.update_note(match.group(1))
            return
        if path == "/api/settings":
            self.update_settings()
            return
        self.send_error_json(HTTPStatus.NOT_FOUND, "API route not found")

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        match = RESOURCE_PACKAGE_INCOMING_RE.match(path)
        if match:
            self.delete_resource_package_upload(match.group(1))
            return
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
        match = NOTE_QUOTE_ROUTE_RE.match(path)
        if match:
            if not self.server.db.delete_note_quote(match.group(1)):
                self.send_error_json(HTTPStatus.NOT_FOUND, "Note quote not found")
                return
            self.send_json({"deleted": True})
            return
        self.send_error_json(HTTPStatus.NOT_FOUND, "API route not found")

    def create_analysis_job(self, paper_id: str, job_type: str) -> None:
        job, created = self.server.queue_analysis_job(paper_id, job_type)
        if job is None:
            self.send_error_json(HTTPStatus.NOT_FOUND, "Paper not found")
            return
        self.send_json(
            {"job": job, "created": created},
            HTTPStatus.ACCEPTED if created else HTTPStatus.OK,
        )

    def batch_analysis_jobs(self) -> None:
        try:
            payload = self.read_json()
            raw_ids = payload.get("paper_ids")
            if not isinstance(raw_ids, list):
                raise ValueError("paper_ids must be a list")
            paper_ids = list(dict.fromkeys(str(value) for value in raw_ids if value))
            if not paper_ids or len(paper_ids) > 500:
                raise ValueError("Select between 1 and 500 papers")
            job_type = str(payload.get("job_type", ""))
            if job_type not in {"figure_analysis"}:
                raise ValueError("Invalid analysis job type")
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        jobs = []
        missing = []
        created_count = 0
        for paper_id in paper_ids:
            job, created = self.server.queue_analysis_job(paper_id, job_type)
            if job is None:
                missing.append(paper_id)
            else:
                jobs.append(job)
                created_count += int(created)
        self.send_json(
            {
                "jobs": jobs,
                "queued_count": created_count,
                "existing_count": len(jobs) - created_count,
                "missing": missing,
            },
            HTTPStatus.ACCEPTED,
        )

    def retry_analysis_job(self, job_id: str) -> None:
        job = self.server.db.retry_analysis_job(job_id)
        if job is None:
            self.send_error_json(
                HTTPStatus.CONFLICT,
                "Only failed jobs below the retry limit can be retried",
            )
            return
        self.server.notify_job_worker()
        self.send_json({"job": job}, HTTPStatus.ACCEPTED)

    def answer_question(self) -> None:
        try:
            payload = self.read_json()
            question = required_text(payload.get("question"), "Question", 2000)
            scope = str(payload.get("scope", "paper"))
            paper_id = str(payload.get("paper_id", "")).strip() or None
            if scope not in {"paper", "library"}:
                raise ValueError("Invalid question scope")
            if scope == "paper" and not paper_id:
                raise ValueError("paper_id is required for paper questions")
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        settings = self.server.db.get_settings(include_secret=True)
        if not provider_available(settings):
            self.send_json(
                {
                    "error": "请先在模型设置中配置 OpenAI-compatible 服务后再使用论文问答。",
                    "error_code": "qa_unavailable",
                },
                HTTPStatus.CONFLICT,
            )
            return
        if scope == "paper":
            if self.server.db.get_paper(str(paper_id)) is None:
                self.send_error_json(HTTPStatus.NOT_FOUND, "Paper not found")
                return
            self.server.ensure_text_index(str(paper_id))
        else:
            for paper in self.server.db.list_papers():
                self.server.ensure_text_index(paper["id"])
        if scope == "paper" and is_paper_overview_question(question):
            chunks = representative_chunks(
                self.server.db.list_paper_chunks(str(paper_id)), limit=8
            )
        else:
            chunks = self.server.db.search_paper_chunks(
                question, str(paper_id) if scope == "paper" else None, limit=8
            )
        if not chunks:
            self.send_json(
                {
                    "error": "没有检索到足以回答该问题的论文内容。",
                    "error_code": "no_retrieval_context",
                },
                HTTPStatus.UNPROCESSABLE_ENTITY,
            )
            return
        try:
            answer, citation_ids, token_count = answer_with_citations(
                question, chunks, settings
            )
        except SummaryError as exc:
            message, error_code = friendly_model_error(str(exc))
            self.send_json(
                {"error": message, "error_code": error_code},
                HTTPStatus.BAD_GATEWAY,
            )
            return
        sources = self.chunk_sources(chunks)
        source_by_id = {
            f"S{index}": {**source, "id": f"S{index}"}
            for index, source in enumerate(sources, start=1)
        }
        cited_sources = [source_by_id[value] for value in citation_ids if value in source_by_id]
        self.send_json(
            {
                "answer": answer,
                "sources": cited_sources,
                "scope": scope,
                "model": settings.get("model", ""),
                "prompt_version": QA_PROMPT_VERSION,
                "token_count": token_count,
            }
        )

    def chunk_sources(self, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        papers: dict[str, dict[str, Any] | None] = {}
        sources = []
        for chunk in chunks:
            paper_id = str(chunk.get("paper_id", ""))
            if paper_id not in papers:
                papers[paper_id] = self.server.db.get_paper(paper_id)
            paper = papers[paper_id]
            sources.append(
                {
                    "chunk_id": chunk.get("id"),
                    "paper_id": paper_id,
                    "paper_title": paper.get("title", "") if paper else "",
                    "page": int(chunk.get("page", 1)),
                    "section": str(chunk.get("section", "")),
                    "excerpt": str(chunk.get("content", ""))[:700],
                    "content_hash": str(chunk.get("content_hash", "")),
                }
            )
        return sources

    def resource_package_estimate(self) -> None:
        try:
            with self.server.resource_package_lock:
                estimate = estimate_resource_package(self.server.data_dir)
        except ResourcePackageError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self.send_json({"estimate": estimate})

    def export_resource_package(self) -> None:
        try:
            payload = self.read_json()
            mode = str(payload.get("mode", "standard")).strip() or "standard"
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        try:
            with self.server.resource_package_lock:
                package_path, manifest = export_resource_package(
                    self.server.data_dir,
                    mode=mode,
                    app_version=__version__,
                    schema_version=SCHEMA_VERSION,
                )
        except ResourcePackageError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self.send_download_file(package_path, "application/vnd.papervault+zip")

    def _read_resource_package_upload(self) -> tuple[str, Path]:
        content_length_text = self.headers.get("Content-Length", "0")
        try:
            content_length = int(content_length_text)
        except (TypeError, ValueError) as exc:
            raise ValueError("资源包大小无效") from exc
        if content_length <= 0:
            raise ValueError("请选择有效的 .pvault 资源包")
        if content_length > MAX_PACKAGE_BYTES:
            raise ValueError("资源包超过 2 GB")
        package_id = str(uuid.uuid4())
        partial = self.server.resource_incoming_dir / f"{package_id}.part"
        destination = self.server.resource_incoming_dir / f"{package_id}.pvault"
        remaining = content_length
        try:
            with partial.open("wb") as target:
                while remaining:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("资源包上传不完整")
                    target.write(chunk)
                    remaining -= len(chunk)
            partial.replace(destination)
        except Exception:
            partial.unlink(missing_ok=True)
            destination.unlink(missing_ok=True)
            raise
        return package_id, destination

    def inspect_resource_package_upload(self) -> None:
        try:
            package_id, package_path = self._read_resource_package_upload()
            manifest = inspect_resource_package(package_path, max_schema_version=SCHEMA_VERSION)
        except (OSError, ResourcePackageError, ValueError) as exc:
            if "package_path" in locals():
                package_path.unlink(missing_ok=True)
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self.send_json({"package_id": package_id, "manifest": manifest})

    def restore_resource_package(self) -> None:
        try:
            payload = self.read_json()
            package_id = str(payload.get("package_id", "")).strip()
            parsed_id = uuid.UUID(package_id)
        except (ValueError, AttributeError) as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "资源包确认信息无效")
            return
        package_path = self.server.resource_incoming_dir / f"{parsed_id}.pvault"
        if not package_path.is_file():
            self.send_error_json(HTTPStatus.NOT_FOUND, "待恢复的资源包不存在或已过期")
            return
        temporary_package = self.server.data_dir.parent / f".{parsed_id}.restore.pvault"
        try:
            with self.server.resource_package_lock:
                shutil.copy2(package_path, temporary_package)
                settings = self.server.db.get_settings(include_secret=True)
                result = restore_resource_package(
                    temporary_package,
                    self.server.data_dir,
                    preserve_api_key=str(settings.get("api_key", "")),
                    app_version=__version__,
                    schema_version=SCHEMA_VERSION,
                    backup_output_dir=self.server.data_dir.parent / "PaperVault-backups",
                )
                self.server.db.initialize()
        except (OSError, ResourcePackageError, sqlite3.Error) as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        finally:
            temporary_package.unlink(missing_ok=True)
        self.send_json({"restored": True, **result})

    def delete_resource_package_upload(self, package_id: str) -> None:
        try:
            parsed_id = uuid.UUID(package_id)
        except ValueError:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "资源包 ID 无效")
            return
        path = self.server.resource_incoming_dir / f"{parsed_id}.pvault"
        path.unlink(missing_ok=True)
        self.send_json({"deleted": True})

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
        supplied_title = fields.get("title", "").strip()
        if supplied_title:
            duplicate = self.server.db.find_active_paper_by_title(supplied_title)
            if duplicate is not None:
                self.send_json(
                    {
                        "paper": duplicate,
                        "duplicate_skipped": True,
                        "deduplicated_count": 0,
                    }
                )
                return
        duplicate = self.server.db.find_active_paper_by_filename(filename, len(file_bytes))
        if duplicate is not None:
            self.send_json(
                {
                    "paper": duplicate,
                    "duplicate_skipped": True,
                    "deduplicated_count": 0,
                }
            )
            return
        paper_id = str(uuid.uuid4())
        stored_filename = f"{paper_id}.pdf"
        destination = self.server.upload_dir / stored_filename
        destination.write_bytes(file_bytes)
        try:
            parsed = extract_pdf(destination, filename)
        except Exception as exc:
            destination.unlink(missing_ok=True)
            self.send_error_json(HTTPStatus.UNPROCESSABLE_ENTITY, f"PDF parsing failed: {exc}")
            return
        title = supplied_title or parsed["title"] or infer_title(parsed["text"], filename)
        duplicate = self.server.db.find_active_paper_by_title(title)
        if duplicate is not None:
            destination.unlink(missing_ok=True)
            self.send_json(
                {
                    "paper": duplicate,
                    "duplicate_skipped": True,
                    "deduplicated_count": 0,
                }
            )
            return
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
        generate_requested = fields.get("generate_summary", "1") != "0"
        settings = self.server.db.get_settings(include_secret=True)
        if generate_requested and settings.get("provider") == "local":
            try:
                summary_pairs, summary_provider = generate_summary(title, parsed["text"], settings)
                summary_status = "draft"
            except SummaryError as exc:
                summary_status = "error"
                summary_error, summary_error_code = friendly_model_error(str(exc))
            else:
                summary_error_code = ""
        else:
            summary_error_code = ""
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
            "summary_blocks": [],
            "summary_paper_title": "",
            "summary_translation_status": "none",
            "summary_translation_error": "",
            "summary_translation_error_code": "",
            "summary_analysis_model": "",
            "summary_translation_model": "",
            "summary_prompt_version": "",
            "visual_assets": [],
            "summary_status": summary_status,
            "summary_provider": summary_provider,
            "summary_model": summary_model,
            "summary_error": summary_error,
            "summary_error_code": summary_error_code,
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
        if generate_requested and settings.get("provider") == "openai_compatible":
            self.server.db.update_paper(
                paper_id,
                {
                    "summary_status": "generating",
                    "summary_error": "",
                    "summary_error_code": "",
                },
                None,
            )
            try:
                result = self._generate_english_summary(paper_id, settings)
            except SummaryError as exc:
                message, error_code = friendly_model_error(str(exc))
                result = self.server.db.update_paper(
                    paper_id,
                    {
                        "summary_status": "error",
                        "summary_error": message,
                        "summary_error_code": error_code,
                    },
                    None,
                )
            else:
                try:
                    result = self._translate_summary_blocks(paper_id, settings)
                except SummaryError as exc:
                    message, error_code = friendly_model_error(str(exc))
                    result = self.server.db.update_paper(
                        paper_id,
                        {
                            "summary_status": "translation_error",
                            "summary_translation_status": "error",
                            "summary_translation_error": message,
                            "summary_translation_error_code": error_code,
                        },
                        None,
                    )
        result, deduplicated_count = self.server.consolidate_paper_duplicates(paper_id)
        self.send_json(
            {
                "paper": result,
                "duplicate_skipped": False,
                "deduplicated_count": deduplicated_count,
            },
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
            if "read_state" in payload:
                read_state = str(payload["read_state"])
                if read_state not in {"read", "unread"}:
                    raise ValueError("Invalid read state")
                fields["read_state"] = read_state
            if "summary_pairs" in payload:
                fields["summary_pairs"] = validate_pairs(payload["summary_pairs"])
                fields["summary_status"] = "edited"
                fields["summary_provider"] = (
                    "curated" if payload.get("summary_provider") == "curated" else "manual"
                )
                fields["summary_model"] = ""
                fields["summary_error"] = ""
                fields["summary_error_code"] = ""
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

    def import_summary_variant(self, paper_id: str) -> None:
        if self.server.db.get_paper(paper_id) is None:
            self.send_error_json(HTTPStatus.NOT_FOUND, "Paper not found")
            return
        try:
            payload = self.read_json()
            imported = fetch_doubao_markdown(str(payload.get("url", "")))
        except (ValueError, SummaryError) as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        variant = self.server.db.create_summary_variant(
            {
                "id": str(uuid.uuid4()),
                "paper_id": paper_id,
                "provider": "doubao",
                "source_url": imported["url"],
                "title": imported["title"],
                "content_markdown": imported["markdown"],
                "content_blocks": markdown_blocks(imported["markdown"]),
                "english_status": "pending",
            }
        )
        self.send_json({"variant": variant, "phase": "chinese_complete"}, HTTPStatus.CREATED)

    def translate_summary_variant(self, variant_id: str) -> None:
        variant = self.server.db.get_summary_variant(variant_id)
        if variant is None:
            self.send_error_json(HTTPStatus.NOT_FOUND, "Summary variant not found")
            return
        self.server.db.update_summary_variant(
            variant_id, {"english_status": "generating", "error": ""}
        )
        settings = self.server.db.get_settings(include_secret=True)
        source_markdown = str(variant.get("content_markdown", ""))
        try:
            english = model = ""
            last_error: Exception | None = None
            for attempt in range(3):
                try:
                    english, model = translate_markdown_to_english(source_markdown, settings)
                    last_error = None
                    break
                except (SummaryError, ValueError) as exc:
                    last_error = exc
                    if attempt >= 2 or not retryable_external_translation_error(str(exc)):
                        raise
                    time.sleep(2 ** attempt)
            if last_error is not None:
                raise last_error
        except (SummaryError, ValueError) as exc:
            message, error_code = friendly_model_error(str(exc))
            self.server.db.update_summary_variant(variant_id, {"english_status": "error", "error": message})
            self.send_json({"error": message, "error_code": error_code}, HTTPStatus.BAD_GATEWAY)
            return
        updated = self.server.db.update_summary_variant(
            variant_id, {"english_markdown": english, "english_status": "ready", "model": model, "error": ""}
        )
        self.send_json({"variant": updated})

    def create_note_quote(self, paper_id: str) -> None:
        if self.server.db.get_paper(paper_id) is None:
            self.send_error_json(HTTPStatus.NOT_FOUND, "Paper not found")
            return
        try:
            payload = self.read_json()
            quote_text = str(payload.get("quote_text", "")).strip()
            if not quote_text:
                raise ValueError("Quote text is required")
            if len(quote_text) > 10000:
                raise ValueError("Quote text is too long")
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        note = self.server.db.create_note_quote(paper_id, payload)
        self.send_json({"note": note}, HTTPStatus.CREATED)

    def update_note(self, paper_id: str) -> None:
        if self.server.db.get_paper(paper_id) is None:
            self.send_error_json(HTTPStatus.NOT_FOUND, "Paper not found")
            return
        try:
            payload = self.read_json()
            title = str(payload.get("title", "我的读书笔记"))
            body = str(payload.get("body", ""))
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self.send_json({"note": self.server.db.update_note(paper_id, title, body)})

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
        settings = self.server.db.get_settings(include_secret=True)
        if settings.get("provider") != "openai_compatible":
            self.send_error_json(
                HTTPStatus.CONFLICT,
                "Detailed paper analysis requires an OpenAI-compatible model",
            )
            return
        self.server.db.update_paper(
            paper_id,
            {
                "summary_status": "generating",
                "summary_error": "",
                "summary_error_code": "",
            },
            None,
        )
        try:
            updated = self._generate_english_summary(paper_id, settings)
        except SummaryError as exc:
            message, error_code = friendly_model_error(str(exc))
            previous_status = self._restored_summary_status(paper)
            updated = self.server.db.update_paper(
                paper_id,
                {
                    "summary_status": previous_status,
                    "summary_error": message,
                    "summary_error_code": error_code,
                },
                None,
            )
            self.send_json(
                {"paper": updated, "error": message, "error_code": error_code},
                HTTPStatus.BAD_GATEWAY,
            )
            return
        self.send_json({"paper": updated, "phase": "english_complete"})

    def translate_paper_summary(self, paper_id: str) -> None:
        paper = self.server.db.get_paper(paper_id)
        if paper is None:
            self.send_error_json(HTTPStatus.NOT_FOUND, "Paper not found")
            return
        if not paper.get("summary_blocks"):
            self.send_error_json(
                HTTPStatus.CONFLICT,
                "Generate the English report before translating it",
            )
            return
        settings = self.server.db.get_settings(include_secret=True)
        self.server.db.update_paper(
            paper_id,
            {
                "summary_status": "translating",
                "summary_translation_status": "translating",
                "summary_translation_error": "",
                "summary_translation_error_code": "",
            },
            None,
        )
        try:
            updated = self._translate_summary_blocks(paper_id, settings)
        except SummaryError as exc:
            message, error_code = friendly_model_error(str(exc))
            updated = self.server.db.update_paper(
                paper_id,
                {
                    "summary_status": "translation_error",
                    "summary_translation_status": "error",
                    "summary_translation_error": message,
                    "summary_translation_error_code": error_code,
                },
                None,
            )
            self.send_json(
                {"paper": updated, "error": message, "error_code": error_code},
                HTTPStatus.BAD_GATEWAY,
            )
            return
        self.send_json({"paper": updated})

    def _generate_english_summary(
        self, paper_id: str, settings: dict[str, str]
    ) -> dict[str, Any]:
        paper = self.server.db.get_paper(paper_id, include_text=True)
        if paper is None:
            raise SummaryError("Paper is unavailable")
        page_texts = self._summary_page_texts(paper)
        report, metadata = generate_english_report(paper["title"], page_texts, settings)
        blocks = [
            {key: value for key, value in block.items() if key != "text_zh"}
            for block in report["blocks"]
        ]
        updated = self.server.db.update_paper(
            paper_id,
            {
                "summary_blocks": blocks,
                "summary_paper_title": report["paper_title"],
                "summary_status": "english_ready",
                "summary_provider": "openai_compatible",
                "summary_model": metadata["model"],
                "summary_analysis_model": metadata["model"],
                "summary_translation_model": translation_model(settings),
                "summary_prompt_version": DEEP_SUMMARY_PROMPT_VERSION,
                "summary_translation_status": "pending",
                "summary_translation_error": "",
                "summary_translation_error_code": "",
                "summary_error": "",
                "summary_error_code": "",
            },
            None,
        )
        if updated is None:
            raise SummaryError("Paper became unavailable while saving the English report")
        return updated

    def _translate_summary_blocks(
        self, paper_id: str, settings: dict[str, str]
    ) -> dict[str, Any]:
        paper = self.server.db.get_paper(paper_id, include_text=True)
        if paper is None:
            raise SummaryError("Paper is unavailable")
        english_blocks = [
            {key: value for key, value in block.items() if key != "text_zh"}
            for block in paper.get("summary_blocks", [])
            if isinstance(block, dict)
        ]
        merged, metadata = translate_report_blocks(
            english_blocks,
            settings,
            self._summary_page_texts(paper),
        )
        updated = self.server.db.update_paper(
            paper_id,
            {
                "summary_blocks": merged,
                "summary_status": "ready",
                "summary_translation_status": "ready",
                "summary_translation_error": "",
                "summary_translation_error_code": "",
                "summary_translation_model": metadata["model"],
            },
            None,
        )
        if updated is None:
            raise SummaryError("Paper became unavailable while saving the translation")
        return updated

    def _summary_page_texts(self, paper: dict[str, Any]) -> list[dict[str, Any]]:
        pdf_path = self.server.upload_dir / str(paper.get("stored_filename", ""))
        try:
            return extract_page_texts(pdf_path)
        except Exception:
            fallback = str(paper.get("extracted_text", "")).strip()
            return [{"page": 1, "text": fallback}] if fallback else []

    @staticmethod
    def _restored_summary_status(paper: dict[str, Any]) -> str:
        blocks = paper.get("summary_blocks", [])
        if blocks:
            if all(str(block.get("text_zh", "")).strip() for block in blocks):
                return "ready"
            if paper.get("summary_translation_status") == "error":
                return "translation_error"
            return "english_ready"
        if paper.get("summary_pairs"):
            return "ready" if paper.get("summary_provider") == "openai_compatible" else "edited"
        return "error"

    def serve_paper_asset(self, paper_id: str, filename: str) -> None:
        paper = self.server.ensure_visual_assets(paper_id)
        if paper is None:
            self.send_error_json(HTTPStatus.NOT_FOUND, "Paper not found")
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

    def serve_preview_page(
        self, paper_id: str, page_number: int, scale: float | None = None
    ) -> None:
        paper = self.server.db.get_paper(paper_id)
        if paper is None:
            self.send_error_json(HTTPStatus.NOT_FOUND, "Paper not found")
            return
        if page_number < 1 or page_number > int(paper.get("page_count", 0)):
            self.send_error_json(HTTPStatus.NOT_FOUND, "PDF page not found")
            return
        pdf_path = self.server.upload_dir / paper["stored_filename"]
        render_scale = scale if scale is not None else 1.7
        scale_suffix = "" if scale is None else f"-{round(render_scale * 100):03d}"
        output_path = self.server.asset_dir / paper_id / f"preview-{page_number}{scale_suffix}.png"
        try:
            if not output_path.is_file():
                render_preview_page(pdf_path, output_path, page_number, render_scale)
            content = output_path.read_bytes()
        except (OSError, RuntimeError, ValueError) as exc:
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, f"PDF page rendering failed: {exc}")
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "private, max-age=86400")
        self.send_header("X-PaperVault-Render-Scale", f"{render_scale:g}")
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
        effort = payload.get("analysis_reasoning_effort")
        if effort is not None and effort not in {"high", "max"}:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "Invalid analysis reasoning effort")
            return
        context_tokens = str(payload.get("context_window_tokens", "")).strip()
        if context_tokens:
            try:
                parsed_context = int(context_tokens)
            except ValueError:
                self.send_error_json(HTTPStatus.BAD_REQUEST, "Invalid model context length")
                return
            if parsed_context < 16000 or parsed_context > 2_000_000:
                self.send_error_json(
                    HTTPStatus.BAD_REQUEST,
                    "Model context length must be between 16000 and 2000000 tokens",
                )
                return
        self.send_json({"settings": self.server.db.update_settings(payload)})

    def import_local_settings(self) -> None:
        values = self.server.local_config_values()
        if not values:
            self.send_error_json(HTTPStatus.NOT_FOUND, "未找到可识别的 .env.local 配置")
            return
        settings = self.server.db.update_settings(values)
        self.send_json(
            {
                "settings": settings,
                "imported_keys": sorted(values),
                "local_config": self.server.local_config_metadata(),
            }
        )

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
            translated = translate_english_offline(
                source_text,
                context,
                known_context_zh,
                model_directory=self.server.model_directory,
            )
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

    def send_download_file(self, path: Path, content_type: str) -> None:
        try:
            size = path.stat().st_size
            filename = path.name
            encoded_name = quote_filename(filename)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            self.send_header(
                "Content-Disposition",
                f"attachment; filename=\"{filename}\"; filename*=UTF-8''{encoded_name}",
            )
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            return

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


def normalize_preview_scale(value: Any) -> float:
    try:
        scale = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("PDF preview scale must be a number") from exc
    if not math.isfinite(scale):
        raise ValueError("PDF preview scale must be finite")
    return min(5.0, max(1.0, math.ceil(scale * 4) / 4))


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
    return normalize_title_key(value)


def paper_quality_key(paper: dict[str, Any]) -> tuple[int, str, str]:
    blocks = [block for block in paper.get("summary_blocks", []) if isinstance(block, dict)]
    pairs = [pair for pair in paper.get("summary_pairs", []) if isinstance(pair, dict)]
    summary_rank = 0
    if pairs:
        summary_rank = 4
    if blocks:
        summary_rank = max(summary_rank, 3)
        has_complete_translation = paper.get("summary_translation_status") == "ready" and all(
            str(block.get("text_zh", "")).strip() for block in blocks
        )
        if has_complete_translation:
            summary_rank = 5
    return (
        summary_rank,
        str(paper.get("updated_at", "")),
        str(paper.get("created_at", "")),
    )


def friendly_model_error(message: str) -> tuple[str, str]:
    lowered = message.casefold()
    if "translation changed numeric content" in lowered:
        return (
            "中文翻译未通过数字一致性校验，英文报告已保留。请重新翻译。",
            "translation_numeric_mismatch",
        )
    if (
        "translation block ids or order" in lowered
        or "translation response ids" in lowered
        or "translations array" in lowered
    ):
        return (
            "中文翻译的段落 ID 或顺序不完整，英文报告已保留。请重新翻译。",
            "translation_structure_error",
        )
    if "page-grounded" in lowered or "valid page references" in lowered:
        return (
            "模型返回的摘要缺少有效正文或页码引用，自动重试后仍未通过校验。",
            "summary_evidence_validation_error",
        )
    if "(429)" in lowered or "rate limit" in lowered or "too many requests" in lowered:
        return (
            "模型服务当前繁忙或受到限流，自动重试后仍未成功。请稍后重试。",
            "model_rate_limited",
        )
    if any(f"({status})" in lowered for status in (500, 502, 503, 504)):
        return (
            "模型服务暂时异常，自动重试后仍未成功。请稍后重试。",
            "model_service_error",
        )
    if "chat completion content" in lowered or "reasoning_content only" in lowered:
        return (
            "模型没有返回可用的最终内容，自动重试后仍未成功。请稍后重试。",
            "model_empty_response",
        )
    if "doubao english model returned empty content" in lowered:
        return (
            "模型返回了空的豆包英文解析，当前结果没有被覆盖。请重试。",
            "model_empty_response",
        )
    if "invalid json" in lowered or "non-object json" in lowered:
        return (
            "模型返回的 JSON 结构无效，当前结果没有被覆盖。请重试。",
            "model_invalid_json",
        )
    if "truncated" in lowered or "continuation limit" in lowered:
        return (
            "模型输出被截断，当前结果没有被覆盖。请重试或调整模型输出限制。",
            "model_output_truncated",
        )
    if "insufficient balance" in lowered or "(402)" in lowered:
        return (
            "模型服务账户余额不足，当前摘要没有被覆盖。请充值或更换 API Key 后重试。",
            "insufficient_balance",
        )
    if "(401)" in lowered or "unauthorized" in lowered or "invalid api key" in lowered or "invalid token" in lowered:
        return "API Key 缺失、无效或已过期，请在模型设置中导入配置并保存后重试。", "invalid_api_key"
    if "timed out" in lowered or "timeout" in lowered:
        return "模型响应超时，当前摘要没有被覆盖，请稍后重试。", "model_timeout"
    if "connection" in lowered or "urlopen" in lowered:
        return "无法连接模型服务，请检查 VPN、网络和 API 地址。", "model_unreachable"
    return "模型生成失败，当前摘要没有被覆盖。请检查模型设置后重试。", "model_error"


def retryable_external_translation_error(message: str) -> bool:
    """Retry only transient provider failures; never retry auth or payload errors."""
    lowered = str(message or "").casefold()
    return (
        any(f"({status})" in lowered for status in (500, 502, 503, 504))
        or "timed out" in lowered
        or "timeout" in lowered
        or "urlopen error" in lowered
        or "connection" in lowered
    )


LOCAL_CONFIG_KEYS = {
    "provider": "provider",
    "paper_vault_provider": "provider",
    "base_url": "base_url",
    "paper_vault_base_url": "base_url",
    "model": "model",
    "paper_vault_model": "model",
    "analysis_model": "analysis_model",
    "paper_vault_analysis_model": "analysis_model",
    "translation_model": "translation_model",
    "paper_vault_translation_model": "translation_model",
    "context_window_tokens": "context_window_tokens",
    "paper_vault_context_window_tokens": "context_window_tokens",
    "analysis_reasoning_effort": "analysis_reasoning_effort",
    "paper_vault_analysis_reasoning_effort": "analysis_reasoning_effort",
    "api_key": "api_key",
    "paper_vault_api_key": "api_key",
}


def load_local_config(project_dir: Path) -> dict[str, str]:
    path = (project_dir / ".env.local").resolve()
    if not path.is_file() or path.stat().st_size > 256 * 1024:
        return {}
    try:
        source = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return {}
    values: dict[str, str] = {}
    for line in source.splitlines():
        cleaned = line.strip()
        if not cleaned or cleaned.startswith("#"):
            continue
        cleaned = re.sub(r"^export\s+", "", cleaned)
        if "=" not in cleaned:
            continue
        raw_key, raw_value = cleaned.split("=", 1)
        setting_key = LOCAL_CONFIG_KEYS.get(raw_key.strip().casefold())
        if not setting_key:
            continue
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if setting_key == "api_key" and not value:
            continue
        values[setting_key] = value
    if not values:
        return {}
    if not values.get("provider") and any(values.get(key) for key in ("base_url", "model", "api_key")):
        values["provider"] = "openai_compatible"
    return values


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
    project_dir = Path(__file__).resolve().parent.parent
    load_local_environment(project_dir)
    parser = argparse.ArgumentParser(description="PaperVault local web server")
    parser.add_argument("--host", default=os.environ.get("PAPER_VAULT_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PAPER_VAULT_PORT", "8765")))
    parser.add_argument("--data-dir", type=Path, default=None)
    args = parser.parse_args()

    configured_data_dir = os.environ.get(DATA_DIR_ENV, "").strip()
    selected_data_dir = args.data_dir or (Path(configured_data_dir) if configured_data_dir else None)
    data_dir = selected_data_dir.expanduser().resolve() if selected_data_dir else project_dir / "data"
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
