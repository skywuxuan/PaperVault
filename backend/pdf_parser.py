from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from pypdf import PdfReader

try:
    import pymupdf as fitz
except ImportError:  # The core app still works if visual rendering is unavailable.
    fitz = None


FIGURE_CAPTION_RE = re.compile(
    r"(?im)(?:^|\n)\s*(fig(?:ure)?\.?|table)\s*([0-9]+[a-z]?)\s*[.：:\-]?\s*([^\n]{0,300})"
)
VISUAL_PRIORITY_RE = re.compile(
    r"ablation|result|experiment|evaluation|performance|architecture|framework|pipeline|method",
    re.I,
)
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
    title = _clean_metadata(metadata.get("/Title"))
    author = normalize_pdf_authors(metadata.get("/Author"))
    if not author and pages:
        author = infer_authors(pages[0], title or infer_title(text, path.name))
    return {
        "text": text,
        "page_count": len(reader.pages),
        "title": title,
        "author": author,
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


def normalize_pdf_authors(value: Any) -> str:
    author = _clean_metadata(value)
    if not author or not re.search(r"[A-Za-z\u3400-\u9fff]", author):
        return ""
    if re.fullmatch(r"(?:(?:19|20)\d{2}[\s,;/·-]*)+", author):
        return ""
    if re.fullmatch(
        r"anonymous|unknown|admin(?:istrator)?|microsoft word|latex|arxiv",
        author,
        re.I,
    ):
        return ""
    if re.search(r"https?://|\bdoi\b|@", author, re.I):
        return ""
    return author[:1000]


def infer_authors(first_page_text: str, title: str = "") -> str:
    lines = [SPACE_RE.sub(" ", line).strip(" ,;|") for line in first_page_text.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return ""
    start = 0
    title_key = re.sub(r"\W+", "", title).casefold()
    if title_key:
        for index, line in enumerate(lines[:12]):
            if title_key[:80] and title_key[:80] in re.sub(r"\W+", "", line).casefold():
                start = index + 1
                break
    for line in lines[start : start + 12]:
        if re.match(r"^(abstract|摘要|keywords?|index terms?)\b", line, re.I):
            break
        if len(line) < 4 or len(line) > 300:
            continue
        if re.search(
            r"@|https?://|\bdoi\b|\barxiv\b|\b(university|institute|department|laboratory|school|college|research center|conference|proceedings)\b",
            line,
            re.I,
        ):
            continue
        candidate = re.sub(r"\s*[†‡*]+\s*", " ", line).strip()
        if not normalize_pdf_authors(candidate):
            continue
        words = re.findall(r"[A-Za-z][A-Za-z'’-]*|[\u3400-\u9fff]{2,4}", candidate)
        title_case_words = [word for word in words if not word[0].isascii() or word[0].isupper()]
        has_author_separator = bool(re.search(r",|;|\band\b|、|，", candidate, re.I))
        if 2 <= len(words) <= 40 and (
            has_author_separator or len(title_case_words) / len(words) >= 0.75
        ):
            return candidate[:1000]
    return ""


def extract_visual_pages(path: Path, output_dir: Path, max_pages: int = 3) -> list[dict[str, Any]]:
    if fitz is None:
        return []
    document = fitz.open(str(path))
    candidates: list[dict[str, Any]] = []
    try:
        for page_index, page in enumerate(document):
            page_text = page.get_text("text")
            page_area = max(float(page.rect.width * page.rect.height), 1.0)
            image_rects = []
            for image in page.get_image_info():
                rect = fitz.Rect(image.get("bbox", (0, 0, 0, 0)))
                if (
                    rect.width >= page.rect.width * 0.18
                    and rect.height >= page.rect.height * 0.05
                    and rect.get_area() >= page_area * 0.015
                ):
                    image_rects.append(rect)
            drawing_rects = []
            for drawing in page.get_drawings():
                rect = fitz.Rect(drawing.get("rect", (0, 0, 0, 0)))
                if 0 < rect.get_area() < page_area * 0.8:
                    drawing_rects.append(rect)
            seen: set[tuple[str, str]] = set()
            for block in page.get_text("blocks", sort=True):
                block_text = str(block[4])
                caption_rect = fitz.Rect(block[:4])
                for match in FIGURE_CAPTION_RE.finditer(block_text):
                    kind = "table" if match.group(1).casefold().startswith("table") else "figure"
                    number = match.group(2)
                    caption = " ".join(match.group(0).split())[:360]
                    identity = (kind, number.casefold())
                    if identity in seen:
                        continue
                    seen.add(identity)
                    crop = _visual_crop(page.rect, caption_rect, image_rects, drawing_rects, kind)
                    if crop is None:
                        continue
                    context = f"{caption} {page_text}".casefold()
                    score = 20 if kind == "figure" else 18
                    score += min(12, int(crop.get_area() / page_area * 30))
                    if VISUAL_PRIORITY_RE.search(context):
                        score += 12
                    if re.search(r"ablation|result|performance|消融|结果|性能", context, re.I):
                        title_zh = "实验结果与消融图表"
                    elif re.search(r"architecture|framework|pipeline|method|架构|框架|方法", context, re.I):
                        title_zh = "方法与系统结构图"
                    else:
                        title_zh = "关键表格" if kind == "table" else "关键图"
                    candidates.append(
                        {
                            "score": score,
                            "page_index": page_index,
                            "kind": kind,
                            "number": number,
                            "caption": caption,
                            "title_zh": title_zh,
                            "crop": crop,
                        }
                    )

        selected = sorted(
            candidates,
            key=lambda item: (-int(item["score"]), int(item["page_index"]), str(item["number"])),
        )[:max_pages]
        selected.sort(key=lambda item: (int(item["page_index"]), str(item["number"])))
        if not selected:
            return []

        output_dir.mkdir(parents=True, exist_ok=True)
        assets: list[dict[str, Any]] = []
        for item in selected:
            page_index = int(item["page_index"])
            page = document[page_index]
            caption = str(item["caption"])
            digest = hashlib.sha256(caption.encode("utf-8")).hexdigest()[:8]
            filename = f"visual-{page_index + 1}-{item['kind']}-{item['number']}-{digest}.png"
            crop = fitz.Rect(item["crop"])
            pixmap = page.get_pixmap(matrix=fitz.Matrix(1.6, 1.6), clip=crop, alpha=False)
            pixmap.save(str(output_dir / filename))
            assets.append(
                {
                    "filename": filename,
                    "page": page_index + 1,
                    "kind": item["kind"],
                    "caption": caption,
                    "title_en": caption,
                    "title_zh": item["title_zh"],
                    "crop": [round(float(value), 2) for value in crop],
                    "width": pixmap.width,
                    "height": pixmap.height,
                }
            )
        return assets
    finally:
        document.close()


def _visual_crop(
    page_rect: Any,
    caption_rect: Any,
    image_rects: list[Any],
    drawing_rects: list[Any],
    kind: str,
):
    page_rect = fitz.Rect(page_rect)
    caption_rect = fitz.Rect(caption_rect)
    max_distance = page_rect.height * 0.32

    def distance(rect: Any) -> float:
        rect = fitz.Rect(rect)
        if rect.y1 < caption_rect.y0:
            return caption_rect.y0 - rect.y1
        if rect.y0 > caption_rect.y1:
            return rect.y0 - caption_rect.y1 + page_rect.height * 0.03
        return 0.0

    visual = None
    nearby_images = [rect for rect in image_rects if distance(rect) <= max_distance]
    if nearby_images:
        visual = fitz.Rect(min(nearby_images, key=distance))
    else:
        nearby_drawings = [
            fitz.Rect(rect)
            for rect in drawing_rects
            if distance(rect) <= max_distance
            and rect.y0 >= caption_rect.y0 - page_rect.height * 0.48
            and rect.y1 <= caption_rect.y1 + page_rect.height * 0.34
        ]
        if nearby_drawings:
            visual = _union_rects(nearby_drawings)
    if visual is None and kind == "table":
        visual = fitz.Rect(
            page_rect.x0 + page_rect.width * 0.05,
            max(page_rect.y0, caption_rect.y0 - page_rect.height * 0.34),
            page_rect.x1 - page_rect.width * 0.05,
            caption_rect.y1,
        )
    if visual is None:
        return None
    crop = _union_rects([visual, caption_rect])
    crop = fitz.Rect(crop.x0 - 8, crop.y0 - 8, crop.x1 + 8, crop.y1 + 8) & page_rect
    if crop.width < page_rect.width * 0.22 or crop.height < page_rect.height * 0.06:
        return None
    return crop


def _union_rects(rects: list[Any]):
    union = fitz.Rect(rects[0])
    for rect in rects[1:]:
        union.include_rect(fitz.Rect(rect))
    return union


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
                    "block": key[0],
                    "line_number": key[1],
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
