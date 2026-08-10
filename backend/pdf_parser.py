from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from pypdf import PdfReader

try:
    import pymupdf as fitz
except ImportError:  # The core app still works if visual rendering is unavailable.
    fitz = None


SPACE_RE = re.compile(r"[ \t]+")
BLANK_RE = re.compile(r"\n{3,}")


def extract_pdf(path: Path) -> dict[str, Any]:
    reader = PdfReader(str(path))
    pages: list[str] = []
    for page in reader.pages:
        page_text = page.extract_text() or ""
        page_text = SPACE_RE.sub(" ", page_text)
        pages.append(page_text.strip())
    text = BLANK_RE.sub("\n\n", "\n\n".join(part for part in pages if part)).strip()
    metadata = reader.metadata or {}
    return {
        "text": text,
        "page_count": len(reader.pages),
        "title": _clean_metadata(metadata.get("/Title")),
        "author": _clean_metadata(metadata.get("/Author")),
    }


def extract_page_texts(path: Path) -> list[dict[str, Any]]:
    reader = PdfReader(str(path))
    pages: list[dict[str, Any]] = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        text = SPACE_RE.sub(" ", text)
        text = BLANK_RE.sub("\n\n", text).strip()
        pages.append({"page": page_number, "text": text})
    return pages


def infer_title(text: str, filename: str) -> str:
    candidates = [line.strip() for line in text[:5000].splitlines() if line.strip()]
    for line in candidates[:12]:
        if 12 <= len(line) <= 240 and not re.match(r"^(abstract|arxiv|doi|http)", line, re.I):
            return line
    return Path(filename).stem.replace("_", " ").replace("-", " ").strip()


def infer_publication_year(text: str, filename: str) -> int | None:
    latest_reasonable_year = datetime.now().year + 1
    for source in (Path(filename).stem, text[:6000]):
        for match in re.finditer(r"(?<!\d)(?:19|20)\d{2}(?!\d)", source):
            year = int(match.group(0))
            if 1900 <= year <= latest_reasonable_year:
                return year
    return None


def extract_visual_pages(path: Path, output_dir: Path, max_pages: int = 3) -> list[dict[str, Any]]:
    if fitz is None:
        return []
    document = fitz.open(str(path))
    candidates: list[tuple[int, int, str, str]] = []
    try:
        for page_index, page in enumerate(document):
            text = page.get_text("text")
            lowered = text.casefold()
            figure_count = len(re.findall(r"\bfig(?:ure)?\.?\s*\d+", lowered))
            table_count = len(re.findall(r"\btable\s*\d+", lowered))
            if figure_count == 0 and table_count == 0:
                continue
            score = figure_count * 4 + table_count * 3
            if "architecture" in lowered or "framework" in lowered:
                score += 2
            if table_count > figure_count:
                title_en = "Experimental results and comparisons"
                title_zh = "实验结果与指标对比"
            elif figure_count:
                title_en = "System architecture and method"
                title_zh = "系统架构与方法图"
            else:
                title_en = "Key visual evidence"
                title_zh = "关键图表证据"
            candidates.append((score, page_index, title_en, title_zh))

        selected = sorted(candidates, key=lambda item: (-item[0], item[1]))[:max_pages]
        selected.sort(key=lambda item: item[1])
        if not selected:
            return []

        shutil.rmtree(output_dir, ignore_errors=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        assets: list[dict[str, Any]] = []
        for _, page_index, title_en, title_zh in selected:
            page = document[page_index]
            pixmap = page.get_pixmap(matrix=fitz.Matrix(1.35, 1.35), alpha=False)
            filename = f"page-{page_index + 1}.png"
            pixmap.save(str(output_dir / filename))
            assets.append(
                {
                    "filename": filename,
                    "page": page_index + 1,
                    "title_en": title_en,
                    "title_zh": title_zh,
                    "width": pixmap.width,
                    "height": pixmap.height,
                }
            )
        return assets
    finally:
        document.close()


def render_preview_page(
    path: Path, output_path: Path, page_number: int, scale: float = 1.7
) -> dict[str, int]:
    if fitz is None:
        raise RuntimeError("PyMuPDF is required for PDF page rendering")
    document = fitz.open(str(path))
    try:
        if page_number < 1 or page_number > document.page_count:
            raise ValueError("PDF page is outside the valid range")
        page = document[page_number - 1]
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        pixmap.save(str(output_path))
        return {"width": pixmap.width, "height": pixmap.height}
    finally:
        document.close()


def extract_preview_words(path: Path, page_number: int) -> dict[str, Any]:
    if fitz is None:
        raise RuntimeError("PyMuPDF is required for PDF text extraction")
    document = fitz.open(str(path))
    try:
        if page_number < 1 or page_number > document.page_count:
            raise ValueError("PDF page is outside the valid range")
        page = document[page_number - 1]
        page_width = float(page.rect.width)
        page_height = float(page.rect.height)
        raw_words = page.get_text("words", sort=True)
        lines: dict[tuple[int, int], list[str]] = {}
        for item in raw_words:
            key = (int(item[5]), int(item[6]))
            lines.setdefault(key, []).append(str(item[4]))
        words = []
        for item in raw_words:
            text = str(item[4]).strip()
            if not text:
                continue
            key = (int(item[5]), int(item[6]))
            words.append(
                {
                    "text": text,
                    "x": round(float(item[0]) / page_width * 100, 5),
                    "y": round(float(item[1]) / page_height * 100, 5),
                    "width": round((float(item[2]) - float(item[0])) / page_width * 100, 5),
                    "height": round((float(item[3]) - float(item[1])) / page_height * 100, 5),
                    "line": " ".join(lines.get(key, []))[:1200],
                }
            )
        return {"page": page_number, "width": page_width, "height": page_height, "words": words}
    finally:
        document.close()


def _clean_metadata(value: Any) -> str:
    if not value:
        return ""
    return str(value).replace("\x00", "").strip()
