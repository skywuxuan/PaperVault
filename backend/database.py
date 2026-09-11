from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


SCHEMA_VERSION = 4


SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS papers (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    title_key TEXT NOT NULL DEFAULT '',
    authors TEXT NOT NULL DEFAULT '',
    publication_year INTEGER,
    doi TEXT NOT NULL DEFAULT '',
    original_filename TEXT NOT NULL,
    stored_filename TEXT NOT NULL UNIQUE,
    file_size INTEGER NOT NULL DEFAULT 0,
    page_count INTEGER NOT NULL DEFAULT 0,
    extracted_text TEXT NOT NULL DEFAULT '',
    summary_pairs TEXT NOT NULL DEFAULT '[]',
    summary_blocks TEXT NOT NULL DEFAULT '[]',
    summary_paper_title TEXT NOT NULL DEFAULT '',
    summary_translation_status TEXT NOT NULL DEFAULT 'none',
    summary_translation_error TEXT NOT NULL DEFAULT '',
    summary_translation_error_code TEXT NOT NULL DEFAULT '',
    summary_analysis_model TEXT NOT NULL DEFAULT '',
    summary_translation_model TEXT NOT NULL DEFAULT '',
    summary_prompt_version TEXT NOT NULL DEFAULT '',
    visual_assets TEXT NOT NULL DEFAULT '[]',
    summary_status TEXT NOT NULL DEFAULT 'pending',
    summary_provider TEXT NOT NULL DEFAULT '',
    summary_model TEXT NOT NULL DEFAULT '',
    summary_error TEXT NOT NULL DEFAULT '',
    summary_error_code TEXT NOT NULL DEFAULT '',
    rating INTEGER NOT NULL DEFAULT 0 CHECK(rating BETWEEN 0 AND 3),
    read_state TEXT NOT NULL DEFAULT 'unread' CHECK(read_state IN ('unread', 'read')),
    deleted_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tags (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    color TEXT NOT NULL DEFAULT '#28786f',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_tags (
    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    tag_id TEXT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (paper_id, tag_id)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vocabulary_entries (
    id TEXT PRIMARY KEY,
    term_en TEXT NOT NULL COLLATE NOCASE,
    translation_zh TEXT NOT NULL DEFAULT '',
    context_en TEXT NOT NULL DEFAULT '',
    context_zh TEXT NOT NULL DEFAULT '',
    paper_id TEXT REFERENCES papers(id) ON DELETE SET NULL,
    paper_title TEXT NOT NULL DEFAULT '',
    source_pair_index INTEGER,
    source_page INTEGER,
    phonetic_us TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'learning' CHECK(status IN ('learning', 'mastered')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS translation_cache (
    cache_key TEXT PRIMARY KEY,
    source_text TEXT NOT NULL,
    translation_zh TEXT NOT NULL,
    note_zh TEXT NOT NULL DEFAULT '',
    definition_zh TEXT NOT NULL DEFAULT '',
    context_translation_zh TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_annotations (
    id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    page INTEGER NOT NULL,
    start_word INTEGER NOT NULL,
    end_word INTEGER NOT NULL,
    selected_text TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL DEFAULT 'yellow' CHECK(color IN ('yellow', 'green', 'blue', 'coral')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS summary_variants (
    id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    source_url TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    content_markdown TEXT NOT NULL DEFAULT '',
    content_blocks TEXT NOT NULL DEFAULT '[]',
    english_markdown TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'ready' CHECK(status IN ('ready', 'error')),
    english_status TEXT NOT NULL DEFAULT 'pending' CHECK(english_status IN ('pending', 'generating', 'ready', 'error')),
    model TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_notes (
    id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL UNIQUE REFERENCES papers(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT '我的读书笔记',
    body TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS note_quotes (
    id TEXT PRIMARY KEY,
    note_id TEXT NOT NULL REFERENCES paper_notes(id) ON DELETE CASCADE,
    source_type TEXT NOT NULL,
    source_variant_id TEXT NOT NULL DEFAULT '',
    source_locator TEXT NOT NULL DEFAULT '{}',
    quote_text TEXT NOT NULL,
    comment TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analysis_runs (
    id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    analysis_type TEXT NOT NULL CHECK(analysis_type IN ('quick_read', 'figure_analysis')),
    status TEXT NOT NULL CHECK(status IN ('succeeded', 'failed')),
    content_json TEXT NOT NULL DEFAULT '{}',
    provider TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    input_hash TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    token_count INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analysis_jobs (
    id TEXT PRIMARY KEY,
    paper_id TEXT REFERENCES papers(id) ON DELETE SET NULL,
    job_type TEXT NOT NULL CHECK(job_type IN ('quick_read', 'figure_analysis')),
    status TEXT NOT NULL CHECK(status IN ('queued', 'running', 'succeeded', 'failed')),
    payload_json TEXT NOT NULL DEFAULT '{}',
    result_run_id TEXT REFERENCES analysis_runs(id) ON DELETE SET NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    error TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    input_hash TEXT NOT NULL DEFAULT '',
    prompt_version TEXT NOT NULL DEFAULT '',
    token_count INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS paper_chunks (
    id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    page INTEGER NOT NULL,
    section TEXT NOT NULL DEFAULT '',
    ordinal INTEGER NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(paper_id, page, ordinal)
);

CREATE INDEX IF NOT EXISTS idx_papers_created_at ON papers(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_paper_tags_tag_id ON paper_tags(tag_id);
CREATE INDEX IF NOT EXISTS idx_vocabulary_updated_at ON vocabulary_entries(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_vocabulary_status ON vocabulary_entries(status);
CREATE INDEX IF NOT EXISTS idx_annotations_paper_page ON paper_annotations(paper_id, page);
CREATE INDEX IF NOT EXISTS idx_summary_variants_paper ON summary_variants(paper_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_note_quotes_note ON note_quotes(note_id, sort_order, created_at);
CREATE INDEX IF NOT EXISTS idx_analysis_runs_paper_type ON analysis_runs(paper_id, analysis_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_analysis_jobs_status_created ON analysis_jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_analysis_jobs_paper_type ON analysis_jobs(paper_id, job_type, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_paper_chunks_paper_page ON paper_chunks(paper_id, page, ordinal);
"""


DEFAULT_SETTINGS = {
    "provider": "local",
    "base_url": "https://api.openai.com/v1",
    "model": "gpt-4.1-mini",
    "api_key": "",
    "analysis_model": "",
    "translation_model": "",
    "context_window_tokens": "",
    "analysis_reasoning_effort": "high",
    "max_input_chars": "60000",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_title_key(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(character for character in normalized if character.isalnum())


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            existing_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if existing_version > SCHEMA_VERSION and existing_version != 5:
                raise RuntimeError(
                    f"Database schema version {existing_version} is newer than supported version {SCHEMA_VERSION}"
                )
            connection.executescript(SCHEMA)
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(papers)").fetchall()
            }
            if "visual_assets" not in columns:
                connection.execute(
                    "ALTER TABLE papers ADD COLUMN visual_assets TEXT NOT NULL DEFAULT '[]'"
                )
            if "summary_model" not in columns:
                connection.execute(
                    "ALTER TABLE papers ADD COLUMN summary_model TEXT NOT NULL DEFAULT ''"
                )
            summary_columns = {
                "summary_blocks": "TEXT NOT NULL DEFAULT '[]'",
                "summary_paper_title": "TEXT NOT NULL DEFAULT ''",
                "summary_translation_status": "TEXT NOT NULL DEFAULT 'none'",
                "summary_translation_error": "TEXT NOT NULL DEFAULT ''",
                "summary_translation_error_code": "TEXT NOT NULL DEFAULT ''",
                "summary_analysis_model": "TEXT NOT NULL DEFAULT ''",
                "summary_translation_model": "TEXT NOT NULL DEFAULT ''",
                "summary_prompt_version": "TEXT NOT NULL DEFAULT ''",
                "summary_error_code": "TEXT NOT NULL DEFAULT ''",
            }
            for column, declaration in summary_columns.items():
                if column not in columns:
                    connection.execute(
                        f"ALTER TABLE papers ADD COLUMN {column} {declaration}"
                    )
            if "rating" not in columns:
                connection.execute(
                    "ALTER TABLE papers ADD COLUMN rating INTEGER NOT NULL DEFAULT 0"
                )
            if "read_state" not in columns:
                connection.execute(
                    "ALTER TABLE papers ADD COLUMN read_state TEXT NOT NULL DEFAULT 'unread'"
                )
            if "deleted_at" not in columns:
                connection.execute("ALTER TABLE papers ADD COLUMN deleted_at TEXT")
            if "title_key" not in columns:
                connection.execute(
                    "ALTER TABLE papers ADD COLUMN title_key TEXT NOT NULL DEFAULT ''"
                )
            for row in connection.execute("SELECT id, title, title_key FROM papers").fetchall():
                title_key = normalize_title_key(row["title"])
                if str(row["title_key"] or "") != title_key:
                    connection.execute(
                        "UPDATE papers SET title_key = ? WHERE id = ?",
                        (title_key, row["id"]),
                    )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_papers_title_key ON papers(title_key, deleted_at)"
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_papers_original_filename
                ON papers(original_filename COLLATE NOCASE, deleted_at)
                """
            )
            try:
                connection.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS paper_chunks_fts USING fts5(
                        chunk_id UNINDEXED,
                        paper_id UNINDEXED,
                        content,
                        tokenize='unicode61'
                    )
                    """
                )
            except sqlite3.OperationalError:
                pass
            schema_version = max(SCHEMA_VERSION, existing_version)
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (schema_version, utc_now()),
            )
            connection.execute(f"PRAGMA user_version = {schema_version}")
            connection.execute(
                """
                UPDATE analysis_jobs
                SET status = 'queued', error = 'Recovered after application restart',
                    updated_at = ?, started_at = NULL
                WHERE status = 'running' AND attempts < max_attempts
                """,
                (utc_now(),),
            )
            connection.execute(
                """
                UPDATE analysis_jobs
                SET status = 'failed', error = 'Retry limit reached after application restart',
                    updated_at = ?, finished_at = ?
                WHERE status = 'running' AND attempts >= max_attempts
                """,
                (utc_now(), utc_now()),
            )
            connection.execute(
                """
                UPDATE summary_variants
                SET english_status = 'error',
                    error = '英文版生成在上次服务退出时中断，请重新生成。',
                    updated_at = ?
                WHERE english_status = 'generating'
                """,
                (utc_now(),),
            )
            from .pdf_parser import infer_publication_year
            for row in connection.execute(
                """
                SELECT id, original_filename, extracted_text FROM papers
                WHERE publication_year IS NULL
                """
            ).fetchall():
                year = infer_publication_year(
                    str(row["extracted_text"]), str(row["original_filename"])
                )
                if year is not None:
                    connection.execute(
                        "UPDATE papers SET publication_year = ? WHERE id = ?",
                        (year, row["id"]),
                    )
            vocabulary_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(vocabulary_entries)").fetchall()
            }
            if "source_page" not in vocabulary_columns:
                connection.execute(
                    "ALTER TABLE vocabulary_entries ADD COLUMN source_page INTEGER"
                )
            if "phonetic_us" not in vocabulary_columns:
                connection.execute(
                    "ALTER TABLE vocabulary_entries ADD COLUMN phonetic_us TEXT NOT NULL DEFAULT ''"
                )
            cache_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(translation_cache)").fetchall()
            }
            if "definition_zh" not in cache_columns:
                connection.execute(
                    "ALTER TABLE translation_cache ADD COLUMN definition_zh TEXT NOT NULL DEFAULT ''"
                )
            if "context_translation_zh" not in cache_columns:
                connection.execute(
                    "ALTER TABLE translation_cache ADD COLUMN context_translation_zh TEXT NOT NULL DEFAULT ''"
                )
            connection.execute(
                """
                UPDATE translation_cache SET note_zh = ''
                WHERE note_zh = '本地学术词典' OR note_zh LIKE 'Argos 离线翻译%'
                """
            )
            connection.execute(
                """
                UPDATE vocabulary_entries SET note = ''
                WHERE note = '本地学术词典' OR note LIKE 'Argos 离线翻译%'
                """
            )
            from .pronunciation import american_ipa
            from .offline_translation import ACADEMIC_GLOSSARY
            for row in connection.execute(
                "SELECT id, term_en, translation_zh FROM vocabulary_entries"
            ).fetchall():
                glossary_key = str(row["term_en"]).casefold().replace("-", " ")
                glossary_entry = ACADEMIC_GLOSSARY.get(glossary_key)
                if not glossary_entry:
                    continue
                richer_translation = glossary_entry[0]
                legacy_first_sense = richer_translation.split("；", 1)[0]
                if str(row["translation_zh"]) == legacy_first_sense and richer_translation != legacy_first_sense:
                    connection.execute(
                        "UPDATE vocabulary_entries SET translation_zh = ? WHERE id = ?",
                        (richer_translation, row["id"]),
                    )
            for row in connection.execute(
                "SELECT id, term_en FROM vocabulary_entries WHERE phonetic_us = ''"
            ).fetchall():
                phonetic = american_ipa(str(row["term_en"]))
                if phonetic:
                    connection.execute(
                        "UPDATE vocabulary_entries SET phonetic_us = ? WHERE id = ?",
                        (phonetic, row["id"]),
                    )
            connection.executemany(
                "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                DEFAULT_SETTINGS.items(),
            )

    def list_tags(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT t.id, t.name, t.color, COUNT(p.id) AS paper_count
                FROM tags t
                LEFT JOIN paper_tags pt ON pt.tag_id = t.id
                LEFT JOIN papers p ON p.id = pt.paper_id AND p.deleted_at IS NULL
                GROUP BY t.id
                ORDER BY t.name COLLATE NOCASE
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def create_tag(self, tag_id: str, name: str, color: str) -> dict[str, Any]:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO tags(id, name, color, created_at) VALUES (?, ?, ?, ?)",
                (tag_id, name.strip(), color, now),
            )
        return {"id": tag_id, "name": name.strip(), "color": color, "paper_count": 0}

    def update_tag(self, tag_id: str, name: str, color: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE tags SET name = ?, color = ? WHERE id = ?",
                (name.strip(), color, tag_id),
            )
            if cursor.rowcount == 0:
                return None
        return next((tag for tag in self.list_tags() if tag["id"] == tag_id), None)

    def delete_tag(self, tag_id: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
        return cursor.rowcount > 0

    def list_papers(
        self,
        query: str = "",
        tag_id: str = "",
        sort: str = "recent",
        summary_filter: str = "",
        rating_filter: str = "",
    ) -> list[dict[str, Any]]:
        clauses: list[str] = ["p.deleted_at IS NULL"]
        params: list[Any] = []
        if query:
            term = f"%{query.strip()}%"
            clauses.append(
                """(
                    p.title LIKE ? OR p.authors LIKE ? OR p.doi LIKE ? OR
                    p.summary_pairs LIKE ? OR p.summary_blocks LIKE ? OR EXISTS (
                        SELECT 1 FROM paper_tags spt
                        JOIN tags st ON st.id = spt.tag_id
                        WHERE spt.paper_id = p.id AND st.name LIKE ?
                    )
                )"""
            )
            params.extend([term, term, term, term, term, term])
        if tag_id:
            clauses.append(
                "EXISTS (SELECT 1 FROM paper_tags fpt WHERE fpt.paper_id = p.id AND fpt.tag_id = ?)"
            )
            params.append(tag_id)
        if summary_filter == "ready":
            clauses.append("p.summary_status IN ('ready', 'draft', 'edited', 'english_ready', 'translating', 'translation_error')")
        elif summary_filter == "error":
            clauses.append("p.summary_status = 'error'")
        elif summary_filter == "pending":
            clauses.append("p.summary_status IN ('pending', 'generating')")
        if rating_filter in {"0", "1", "2", "3"}:
            clauses.append("p.rating = ?")
            params.append(int(rating_filter))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        order_by = {
            "recent": "p.created_at DESC",
            "rating": "p.rating DESC, COALESCE(p.publication_year, 0) DESC, p.created_at DESC",
            "publication": "COALESCE(p.publication_year, 0) DESC, p.created_at DESC",
            "title": "p.title COLLATE NOCASE ASC",
        }.get(sort, "p.created_at DESC")
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT p.*
                FROM papers p
                {where}
                ORDER BY {order_by}
                """,
                params,
            ).fetchall()
            papers = [self._paper_dict(connection, row, include_variants=False) for row in rows]
        return papers

    def library_facets(self) -> dict[str, Any]:
        with self.connect() as connection:
            status_rows = connection.execute(
                "SELECT summary_status, COUNT(*) AS count FROM papers WHERE deleted_at IS NULL GROUP BY summary_status"
            ).fetchall()
            rating_rows = connection.execute(
                "SELECT rating, COUNT(*) AS count FROM papers WHERE deleted_at IS NULL GROUP BY rating"
            ).fetchall()
        raw_status = {str(row["summary_status"]): int(row["count"]) for row in status_rows}
        ratings = {str(value): 0 for value in range(4)}
        ratings.update({str(row["rating"]): int(row["count"]) for row in rating_rows})
        return {
            "total": sum(raw_status.values()),
            "summary": {
                "ready": sum(raw_status.get(status, 0) for status in ("ready", "draft", "edited", "english_ready", "translating", "translation_error")),
                "error": raw_status.get("error", 0),
                "pending": sum(raw_status.get(status, 0) for status in ("pending", "generating")),
            },
            "rating": ratings,
        }

    def get_paper(self, paper_id: str, include_text: bool = False) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM papers WHERE id = ? AND deleted_at IS NULL", (paper_id,)
            ).fetchone()
            if row is None:
                return None
            return self._paper_dict(connection, row, include_text=include_text)

    def find_active_paper_by_title(self, title: str) -> dict[str, Any] | None:
        title_key = normalize_title_key(title)
        if not title_key:
            return None
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id FROM papers
                WHERE title_key = ? AND deleted_at IS NULL
                ORDER BY updated_at DESC, created_at DESC
                LIMIT 1
                """,
                (title_key,),
            ).fetchone()
        return self.get_paper(str(row["id"])) if row is not None else None

    def find_active_paper_by_filename(
        self, filename: str, file_size: int | None = None
    ) -> dict[str, Any] | None:
        normalized = Path(filename).name[:500]
        if not normalized:
            return None
        size_clause = " AND file_size = ?" if file_size is not None else ""
        parameters: tuple[Any, ...] = (
            (normalized, int(file_size)) if file_size is not None else (normalized,)
        )
        with self.connect() as connection:
            row = connection.execute(
                f"""
                SELECT id FROM papers
                WHERE original_filename = ? COLLATE NOCASE
                  AND deleted_at IS NULL{size_clause}
                ORDER BY updated_at DESC, created_at DESC
                LIMIT 1
                """,
                parameters,
            ).fetchone()
        return self.get_paper(str(row["id"])) if row is not None else None

    def insert_paper(self, paper: dict[str, Any], tag_ids: list[str]) -> dict[str, Any]:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO papers(
                    id, title, title_key, authors, publication_year, doi, original_filename,
                    stored_filename, file_size, page_count, extracted_text,
                    summary_pairs, summary_blocks, summary_paper_title, summary_translation_status,
                    summary_translation_error, summary_translation_error_code,
                    summary_analysis_model, summary_translation_model,
                    summary_prompt_version, visual_assets, summary_status, summary_provider,
                    summary_model, summary_error, summary_error_code, rating, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    paper["id"], paper["title"], normalize_title_key(paper["title"]),
                    paper.get("authors", ""),
                    paper.get("publication_year"), paper.get("doi", ""),
                    paper["original_filename"], paper["stored_filename"],
                    paper.get("file_size", 0), paper.get("page_count", 0),
                    paper.get("extracted_text", ""),
                    json.dumps(paper.get("summary_pairs", []), ensure_ascii=False),
                    json.dumps(paper.get("summary_blocks", []), ensure_ascii=False),
                    paper.get("summary_paper_title", ""),
                    paper.get("summary_translation_status", "none"),
                    paper.get("summary_translation_error", ""),
                    paper.get("summary_translation_error_code", ""),
                    paper.get("summary_analysis_model", ""),
                    paper.get("summary_translation_model", ""),
                    paper.get("summary_prompt_version", ""),
                    json.dumps(paper.get("visual_assets", []), ensure_ascii=False),
                    paper.get("summary_status", "pending"),
                    paper.get("summary_provider", ""), paper.get("summary_model", ""),
                    paper.get("summary_error", ""),
                    paper.get("summary_error_code", ""),
                    paper.get("rating", 0),
                    paper["created_at"], paper["updated_at"],
                ),
            )
            self._replace_paper_tags(connection, paper["id"], tag_ids)
        result = self.get_paper(paper["id"])
        assert result is not None
        return result

    def update_paper(self, paper_id: str, fields: dict[str, Any], tag_ids: list[str] | None) -> dict[str, Any] | None:
        allowed = {
            "title", "authors", "publication_year", "doi", "rating", "read_state",
            "summary_pairs", "summary_blocks", "summary_paper_title", "summary_translation_status",
            "summary_translation_error", "summary_translation_error_code",
            "summary_analysis_model", "summary_translation_model",
            "summary_prompt_version", "visual_assets", "summary_status", "summary_provider",
            "summary_model", "summary_error", "summary_error_code",
        }
        updates: list[str] = []
        values: list[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            updates.append(f"{key} = ?")
            values.append(
                json.dumps(value, ensure_ascii=False)
                if key in {"summary_pairs", "summary_blocks", "visual_assets"}
                else value
            )
            if key == "title":
                updates.append("title_key = ?")
                values.append(normalize_title_key(value))
        updates.append("updated_at = ?")
        values.append(utc_now())
        values.append(paper_id)
        with self.connect() as connection:
            cursor = connection.execute(
                f"UPDATE papers SET {', '.join(updates)} WHERE id = ? AND deleted_at IS NULL",
                values,
            )
            if cursor.rowcount == 0:
                return None
            if tag_ids is not None:
                self._replace_paper_tags(connection, paper_id, tag_ids)
        return self.get_paper(paper_id)

    def delete_paper(self, paper_id: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT stored_filename FROM papers WHERE id = ? AND deleted_at IS NULL",
                (paper_id,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE papers SET deleted_at = ?, updated_at = ? WHERE id = ?",
                (utc_now(), utc_now(), paper_id),
            )
            return str(row["stored_filename"])

    def list_trash(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM papers WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC"
            ).fetchall()
            return [self._paper_dict(connection, row, include_variants=False) for row in rows]

    def restore_paper(self, paper_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE papers SET deleted_at = NULL, updated_at = ?
                WHERE id = ? AND deleted_at IS NOT NULL
                """,
                (utc_now(), paper_id),
            )
            if cursor.rowcount == 0:
                return None
        return self.get_paper(paper_id)

    def replace_paper_chunks(self, paper_id: str, chunks: list[dict[str, Any]]) -> None:
        now = utc_now()
        with self.connect() as connection:
            try:
                connection.execute("DELETE FROM paper_chunks_fts WHERE paper_id = ?", (paper_id,))
            except sqlite3.OperationalError:
                pass
            connection.execute("DELETE FROM paper_chunks WHERE paper_id = ?", (paper_id,))
            for chunk in chunks:
                connection.execute(
                    """
                    INSERT INTO paper_chunks(
                        id, paper_id, page, section, ordinal, content, content_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk["id"], paper_id, chunk["page"], chunk.get("section", ""),
                        chunk["ordinal"], chunk["content"], chunk["content_hash"], now,
                    ),
                )
                try:
                    connection.execute(
                        "INSERT INTO paper_chunks_fts(chunk_id, paper_id, content) VALUES (?, ?, ?)",
                        (chunk["id"], paper_id, chunk["content"]),
                    )
                except sqlite3.OperationalError:
                    pass

    def list_paper_chunks(self, paper_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM paper_chunks WHERE paper_id = ?
                ORDER BY page, ordinal
                """,
                (paper_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def has_paper_chunks(self, paper_id: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM paper_chunks WHERE paper_id = ? LIMIT 1", (paper_id,)
            ).fetchone()
        return row is not None

    def search_paper_chunks(
        self, query: str, paper_id: str | None = None, limit: int = 8
    ) -> list[dict[str, Any]]:
        tokens = [token for token in re.findall(r"[\w\u4e00-\u9fff]+", query) if len(token) > 1]
        rows: list[sqlite3.Row] = []
        with self.connect() as connection:
            if tokens:
                safe_tokens = [token.replace('"', "") for token in tokens[:12]]
                match_query = " OR ".join(f'"{token}"' for token in safe_tokens)
                try:
                    sql = """
                        SELECT c.*, bm25(paper_chunks_fts) AS score
                        FROM paper_chunks_fts
                        JOIN paper_chunks c ON c.id = paper_chunks_fts.chunk_id
                        JOIN papers p ON p.id = c.paper_id
                        WHERE paper_chunks_fts MATCH ? AND p.deleted_at IS NULL
                    """
                    params: list[Any] = [match_query]
                    if paper_id:
                        sql += " AND c.paper_id = ?"
                        params.append(paper_id)
                    sql += " ORDER BY score LIMIT ?"
                    params.append(max(1, min(limit, 30)))
                    rows = connection.execute(sql, params).fetchall()
                except sqlite3.OperationalError:
                    rows = []
            if not rows:
                sql = """
                    SELECT c.*, 0.0 AS score FROM paper_chunks c
                    JOIN papers p ON p.id = c.paper_id
                    WHERE c.content LIKE ? AND p.deleted_at IS NULL
                """
                params = [f"%{query.strip()}%"]
                if paper_id:
                    sql += " AND c.paper_id = ?"
                    params.append(paper_id)
                sql += " ORDER BY page, ordinal LIMIT ?"
                params.append(max(1, min(limit, 30)))
                rows = connection.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def create_analysis_job(self, job: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO analysis_jobs(
                    id, paper_id, job_type, status, payload_json, attempts, max_attempts,
                    provider, model, input_hash, prompt_version, created_at, updated_at
                ) VALUES (?, ?, ?, 'queued', ?, 0, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job["id"], job.get("paper_id"), job["job_type"],
                    json.dumps(job.get("payload", {}), ensure_ascii=False),
                    job.get("max_attempts", 3), job.get("provider", ""),
                    job.get("model", ""), job.get("input_hash", ""),
                    job.get("prompt_version", ""), now, now,
                ),
            )
        result = self.get_analysis_job(job["id"])
        assert result is not None
        return result

    def get_analysis_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM analysis_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return self._job_dict(row) if row is not None else None

    def list_analysis_jobs(
        self, paper_id: str | None = None, statuses: tuple[str, ...] = (), limit: int = 100
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if paper_id:
            clauses.append("paper_id = ?")
            params.append(paper_id)
        if statuses:
            clauses.append(f"status IN ({','.join('?' for _ in statuses)})")
            params.extend(statuses)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 500)))
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM analysis_jobs {where} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._job_dict(row) for row in rows]

    def claim_next_analysis_job(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM analysis_jobs
                WHERE status = 'queued' AND attempts < max_attempts
                ORDER BY created_at LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            now = utc_now()
            connection.execute(
                """
                UPDATE analysis_jobs
                SET status = 'running', attempts = attempts + 1, error = '',
                    started_at = ?, finished_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (now, now, row["id"]),
            )
            claimed = connection.execute(
                "SELECT * FROM analysis_jobs WHERE id = ?", (row["id"],)
            ).fetchone()
        return self._job_dict(claimed) if claimed is not None else None

    def complete_analysis_job(
        self, job_id: str, run_id: str, token_count: int, duration_ms: int
    ) -> dict[str, Any] | None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE analysis_jobs
                SET status = 'succeeded', result_run_id = ?, token_count = ?,
                    duration_ms = ?, error = '', finished_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (run_id, token_count, duration_ms, now, now, job_id),
            )
        return self.get_analysis_job(job_id)

    def fail_analysis_job(
        self, job_id: str, error: str, duration_ms: int
    ) -> dict[str, Any] | None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE analysis_jobs
                SET status = 'failed', error = ?, duration_ms = ?,
                    finished_at = ?, updated_at = ? WHERE id = ?
                """,
                (error[:2000], duration_ms, now, now, job_id),
            )
        return self.get_analysis_job(job_id)

    def retry_analysis_job(self, job_id: str) -> dict[str, Any] | None:
        now = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE analysis_jobs
                SET status = 'queued', error = '', finished_at = NULL, updated_at = ?
                WHERE id = ? AND status = 'failed' AND attempts < max_attempts
                """,
                (now, job_id),
            )
            if cursor.rowcount == 0:
                return None
        return self.get_analysis_job(job_id)

    def create_analysis_run(self, run: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO analysis_runs(
                    id, paper_id, analysis_type, status, content_json, provider, model,
                    input_hash, prompt_version, token_count, duration_ms, error, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run["id"], run["paper_id"], run["analysis_type"], run["status"],
                    json.dumps(run.get("content", {}), ensure_ascii=False),
                    run.get("provider", ""), run.get("model", ""), run["input_hash"],
                    run["prompt_version"], run.get("token_count", 0),
                    run.get("duration_ms", 0), run.get("error", ""),
                    run.get("created_at", utc_now()),
                ),
            )
        result = self.get_analysis_run(run["id"])
        assert result is not None
        return result

    def get_analysis_run(self, run_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM analysis_runs WHERE id = ?", (run_id,)
            ).fetchone()
        return self._run_dict(row) if row is not None else None

    def list_analysis_runs(
        self, paper_id: str, analysis_type: str | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM analysis_runs WHERE paper_id = ?"
        params: list[Any] = [paper_id]
        if analysis_type:
            sql += " AND analysis_type = ?"
            params.append(analysis_type)
        sql += " ORDER BY created_at DESC"
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self._run_dict(row) for row in rows]

    def list_annotations(self, paper_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM paper_annotations
                WHERE paper_id = ?
                ORDER BY page, start_word, created_at
                """,
                (paper_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_annotation(self, annotation: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO paper_annotations(
                    id, paper_id, page, start_word, end_word,
                    selected_text, note, color, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    annotation["id"], annotation["paper_id"], annotation["page"],
                    annotation["start_word"], annotation["end_word"],
                    annotation.get("selected_text", ""), annotation.get("note", ""),
                    annotation.get("color", "yellow"), now, now,
                ),
            )
        result = self.get_annotation(annotation["id"])
        assert result is not None
        return result

    def get_annotation(self, annotation_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM paper_annotations WHERE id = ?", (annotation_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def update_annotation(
        self, annotation_id: str, note: str, color: str
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE paper_annotations
                SET note = ?, color = ?, updated_at = ?
                WHERE id = ?
                """,
                (note, color, utc_now(), annotation_id),
            )
            if cursor.rowcount == 0:
                return None
        return self.get_annotation(annotation_id)

    def delete_annotation(self, annotation_id: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM paper_annotations WHERE id = ?", (annotation_id,)
            )
        return cursor.rowcount > 0

    def list_summary_variants(self, paper_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM summary_variants WHERE paper_id = ? ORDER BY updated_at DESC, created_at DESC",
                (paper_id,),
            ).fetchall()
        variants = []
        for row in rows:
            item = dict(row)
            if item.get("provider") == "doubao":
                from .doubao import normalize_markdown

                item["content_markdown"] = normalize_markdown(item.get("content_markdown", ""))
            try:
                item["content_blocks"] = json.loads(item.get("content_blocks") or "[]")
            except json.JSONDecodeError:
                item["content_blocks"] = []
            variants.append(item)
        return variants

    def get_summary_variant(self, variant_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM summary_variants WHERE id = ?", (variant_id,)
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        if item.get("provider") == "doubao":
            from .doubao import normalize_markdown

            item["content_markdown"] = normalize_markdown(item.get("content_markdown", ""))
        try:
            item["content_blocks"] = json.loads(item.get("content_blocks") or "[]")
        except json.JSONDecodeError:
            item["content_blocks"] = []
        return item

    def create_summary_variant(self, variant: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO summary_variants(
                    id, paper_id, provider, source_url, title, content_markdown,
                    content_blocks, english_markdown, status, english_status, model,
                    error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    variant["id"], variant["paper_id"], variant.get("provider", "doubao"),
                    variant.get("source_url", ""), variant.get("title", ""),
                    variant.get("content_markdown", ""),
                    json.dumps(variant.get("content_blocks", []), ensure_ascii=False),
                    variant.get("english_markdown", ""), variant.get("status", "ready"),
                    variant.get("english_status", "pending"), variant.get("model", ""),
                    variant.get("error", ""), now, now,
                ),
            )
        result = self.get_summary_variant(variant["id"])
        assert result is not None
        return result

    def update_summary_variant(self, variant_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        allowed = {"title", "content_markdown", "content_blocks", "english_markdown", "status", "english_status", "model", "error"}
        updates: list[str] = []
        values: list[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            updates.append(f"{key} = ?")
            values.append(json.dumps(value, ensure_ascii=False) if key == "content_blocks" else value)
        if not updates:
            return self.get_summary_variant(variant_id)
        updates.append("updated_at = ?")
        values.extend((utc_now(), variant_id))
        with self.connect() as connection:
            cursor = connection.execute(
                f"UPDATE summary_variants SET {', '.join(updates)} WHERE id = ?", values
            )
            if cursor.rowcount == 0:
                return None
        return self.get_summary_variant(variant_id)

    def get_or_create_note(self, paper_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM paper_notes WHERE paper_id = ?", (paper_id,)).fetchone()
            if row is None:
                note_id = str(__import__("uuid").uuid4())
                now = utc_now()
                connection.execute(
                    "INSERT INTO paper_notes(id, paper_id, title, body, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (note_id, paper_id, "我的读书笔记", "", now, now),
                )
                row = connection.execute("SELECT * FROM paper_notes WHERE id = ?", (note_id,)).fetchone()
            note = dict(row)
            quotes = connection.execute(
                "SELECT * FROM note_quotes WHERE note_id = ? ORDER BY sort_order, created_at", (note["id"],)
            ).fetchall()
        note["quotes"] = [dict(quote) for quote in quotes]
        for quote in note["quotes"]:
            try:
                quote["source_locator"] = json.loads(quote.get("source_locator") or "{}")
            except json.JSONDecodeError:
                quote["source_locator"] = {}
        return note

    def update_note(self, paper_id: str, title: str, body: str) -> dict[str, Any]:
        note = self.get_or_create_note(paper_id)
        with self.connect() as connection:
            connection.execute(
                "UPDATE paper_notes SET title = ?, body = ?, updated_at = ? WHERE id = ?",
                (title[:200] or "我的读书笔记", body[:100000], utc_now(), note["id"]),
            )
        return self.get_or_create_note(paper_id)

    def create_note_quote(self, paper_id: str, quote: dict[str, Any]) -> dict[str, Any]:
        note = self.get_or_create_note(paper_id)
        quote_id = quote.get("id") or str(__import__("uuid").uuid4())
        now = utc_now()
        with self.connect() as connection:
            max_order = connection.execute(
                "SELECT COALESCE(MAX(sort_order), -1) FROM note_quotes WHERE note_id = ?", (note["id"],)
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO note_quotes(
                    id, note_id, source_type, source_variant_id, source_locator,
                    quote_text, comment, sort_order, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    quote_id, note["id"], str(quote.get("source_type", "manual"))[:40],
                    str(quote.get("source_variant_id", ""))[:100],
                    json.dumps(quote.get("source_locator", {}), ensure_ascii=False),
                    str(quote.get("quote_text", "")).strip()[:10000],
                    str(quote.get("comment", "")).strip()[:10000], int(max_order) + 1, now, now,
                ),
            )
        return self.get_or_create_note(paper_id)

    def delete_note_quote(self, quote_id: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute("DELETE FROM note_quotes WHERE id = ?", (quote_id,))
        return cursor.rowcount > 0

    def list_vocabulary_entries(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM vocabulary_entries
                ORDER BY updated_at DESC, term_en COLLATE NOCASE
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_vocabulary_entry(self, entry_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM vocabulary_entries WHERE id = ?", (entry_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def create_vocabulary_entry(self, entry: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        now = utc_now()
        with self.connect() as connection:
            existing = connection.execute(
                """
                SELECT * FROM vocabulary_entries
                WHERE term_en = ? COLLATE NOCASE AND translation_zh = ?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (entry["term_en"], entry["translation_zh"]),
            ).fetchone()
            if existing is not None:
                return dict(existing), False
            connection.execute(
                """
                INSERT INTO vocabulary_entries(
                    id, term_en, translation_zh, context_en, context_zh,
                    paper_id, paper_title, source_pair_index, source_page, phonetic_us, note, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry["id"], entry["term_en"], entry["translation_zh"],
                    entry.get("context_en", ""), entry.get("context_zh", ""),
                    entry.get("paper_id"), entry.get("paper_title", ""),
                    entry.get("source_pair_index"), entry.get("source_page"),
                    entry.get("phonetic_us", ""), entry.get("note", ""),
                    entry.get("status", "learning"), now, now,
                ),
            )
        result = self.get_vocabulary_entry(entry["id"])
        assert result is not None
        return result, True

    def update_vocabulary_entry(self, entry_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        allowed = {"term_en", "translation_zh", "phonetic_us", "note", "status"}
        updates: list[str] = []
        values: list[Any] = []
        for key, value in fields.items():
            if key in allowed:
                updates.append(f"{key} = ?")
                values.append(value)
        if not updates:
            return self.get_vocabulary_entry(entry_id)
        updates.append("updated_at = ?")
        values.extend([utc_now(), entry_id])
        with self.connect() as connection:
            cursor = connection.execute(
                f"UPDATE vocabulary_entries SET {', '.join(updates)} WHERE id = ?",
                values,
            )
            if cursor.rowcount == 0:
                return None
        return self.get_vocabulary_entry(entry_id)

    def delete_vocabulary_entry(self, entry_id: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM vocabulary_entries WHERE id = ?", (entry_id,)
            )
        return cursor.rowcount > 0

    def find_vocabulary_translation(self, term_en: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT term_en, translation_zh, phonetic_us, note, context_zh
                FROM vocabulary_entries
                WHERE term_en = ? COLLATE NOCASE
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (term_en,),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_cached_translation(self, cache_key: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM translation_cache WHERE cache_key = ?", (cache_key,)
            ).fetchone()
        return dict(row) if row is not None else None

    def cache_translation(
        self, cache_key: str, source_text: str, translation_zh: str, note_zh: str,
        definition_zh: str = "", context_translation_zh: str = "",
    ) -> dict[str, Any]:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO translation_cache(
                    cache_key, source_text, translation_zh, note_zh,
                    definition_zh, context_translation_zh, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    translation_zh = excluded.translation_zh,
                    note_zh = excluded.note_zh,
                    definition_zh = excluded.definition_zh,
                    context_translation_zh = excluded.context_translation_zh,
                    updated_at = excluded.updated_at
                """,
                (
                    cache_key, source_text, translation_zh, note_zh,
                    definition_zh, context_translation_zh, now, now,
                ),
            )
        result = self.get_cached_translation(cache_key)
        assert result is not None
        return result

    def get_settings(self, include_secret: bool = False) -> dict[str, str]:
        with self.connect() as connection:
            rows = connection.execute("SELECT key, value FROM settings").fetchall()
        result = {str(row["key"]): str(row["value"]) for row in rows}
        if not include_secret and result.get("api_key"):
            result["api_key"] = "********"
        return result

    def update_settings(self, values: dict[str, Any]) -> dict[str, str]:
        allowed = set(DEFAULT_SETTINGS)
        with self.connect() as connection:
            for key, value in values.items():
                if key not in allowed or (key == "api_key" and value == "********"):
                    continue
                connection.execute(
                    "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, str(value)),
                )
        return self.get_settings()

    @staticmethod
    def _job_dict(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        try:
            data["payload"] = json.loads(data.pop("payload_json") or "{}")
        except json.JSONDecodeError:
            data["payload"] = {}
            data.pop("payload_json", None)
        return data

    @staticmethod
    def _run_dict(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        try:
            data["content"] = json.loads(data.pop("content_json") or "{}")
        except json.JSONDecodeError:
            data["content"] = {}
            data.pop("content_json", None)
        return data

    @staticmethod
    def _replace_paper_tags(connection: sqlite3.Connection, paper_id: str, tag_ids: list[str]) -> None:
        connection.execute("DELETE FROM paper_tags WHERE paper_id = ?", (paper_id,))
        connection.executemany(
            "INSERT OR IGNORE INTO paper_tags(paper_id, tag_id) VALUES (?, ?)",
            [(paper_id, tag_id) for tag_id in dict.fromkeys(tag_ids)],
        )

    @staticmethod
    def _paper_dict(
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        include_text: bool = False,
        include_variants: bool = True,
    ) -> dict[str, Any]:
        data = dict(row)
        for json_field in ("summary_pairs", "summary_blocks", "visual_assets"):
            try:
                data[json_field] = json.loads(data.get(json_field) or "[]")
            except json.JSONDecodeError:
                data[json_field] = []
        tags = connection.execute(
            """
            SELECT t.id, t.name, t.color
            FROM tags t
            JOIN paper_tags pt ON pt.tag_id = t.id
            WHERE pt.paper_id = ?
            ORDER BY t.name COLLATE NOCASE
            """,
            (row["id"],),
        ).fetchall()
        data["tags"] = [dict(tag) for tag in tags]
        if include_variants:
            variants = connection.execute(
                "SELECT * FROM summary_variants WHERE paper_id = ? ORDER BY updated_at DESC, created_at DESC",
                (row["id"],),
            ).fetchall()
            data["summary_variants"] = []
            for variant_row in variants:
                variant = dict(variant_row)
                if variant.get("provider") == "doubao":
                    from .doubao import normalize_markdown

                    variant["content_markdown"] = normalize_markdown(variant.get("content_markdown", ""))
                try:
                    variant["content_blocks"] = json.loads(variant.get("content_blocks") or "[]")
                except json.JSONDecodeError:
                    variant["content_blocks"] = []
                data["summary_variants"].append(variant)
        else:
            data["summary_variants"] = [
                {
                    key: variant[key]
                    for key in ("id", "paper_id", "provider", "source_url", "title", "status", "english_status", "model", "error", "created_at", "updated_at")
                    if key in variant.keys()
                }
                for variant in connection.execute(
                    "SELECT * FROM summary_variants WHERE paper_id = ? ORDER BY updated_at DESC, created_at DESC",
                    (row["id"],),
                ).fetchall()
            ]
        job_rows = connection.execute(
            """
            SELECT * FROM analysis_jobs
            WHERE paper_id = ?
            ORDER BY updated_at DESC
            """,
            (row["id"],),
        ).fetchall()
        latest_jobs: dict[str, dict[str, Any]] = {}
        for job_row in job_rows:
            job = Database._job_dict(job_row)
            latest_jobs.setdefault(str(job["job_type"]), job)
        data["analysis_jobs"] = latest_jobs
        data["text_length"] = len(data.get("extracted_text", ""))
        data.pop("title_key", None)
        if not include_text:
            data.pop("extracted_text", None)
        return data
