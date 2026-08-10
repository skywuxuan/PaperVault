from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .llm import SummaryError, request_structured_json


QUICK_READ_PROMPT_VERSION = "quick-read-v1"
FIGURE_ANALYSIS_PROMPT_VERSION = "figure-analysis-v2"
QA_PROMPT_VERSION = "citation-qa-v1"


QUICK_READ_PROMPT = """You create a concise evidence-grounded quick read of one research paper.
Return strict JSON with headline and sections. headline is {text,page}. sections is an array of
{key,title,items}, where each item is {text,page}. Required keys: motivation, method, findings,
contributions, limitations, reading_guide. Use only the supplied page-labelled evidence. Every
claim must use a valid 1-based page number. Keep the response concise and do not use markdown."""


FIGURE_ANALYSIS_PROMPT = """Analyze candidate figure and result pages using only their supplied
captions, extracted page text, and metadata. Return strict JSON: {figures:[{page,look_for,
evidence,importance}]}. Do not claim that you inspected pixels. Explain what to inspect, what the
page supports, and why it matters. Every entry must use one of the supplied candidate pages."""


QA_PROMPT = """Answer the question using only the supplied source excerpts. Return strict JSON:
{answer:string,citations:[string]}. citations must contain one or more source IDs such as S1.
Do not use outside knowledge, do not invent citations, and state when the excerpts are insufficient."""


SECTION_SPECS = [
    ("motivation", "研究动机", ("motivation", "problem", "challenge", "background")),
    ("method", "方法步骤", ("method", "architecture", "framework", "approach")),
    ("findings", "关键发现", ("result", "experiment", "evaluation", "performance")),
    ("contributions", "主要贡献", ("contribution", "innovation", "propose", "novel")),
    ("limitations", "局限", ("limitation", "future", "constraint", "however")),
    ("reading_guide", "精读建议", ("architecture", "result", "ablation", "conclusion")),
]


def provider_available(settings: dict[str, str]) -> bool:
    return (
        settings.get("provider") == "openai_compatible"
        and bool(settings.get("base_url", "").strip())
        and bool(settings.get("model", "").strip())
    )


def build_text_chunks(
    paper_id: str, page_texts: list[dict[str, Any]], max_chars: int = 1400
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for page_data in page_texts:
        page = max(1, int(page_data.get("page", 1)))
        text = " ".join(str(page_data.get("text", "")).split())
        if not text:
            continue
        parts = _split_text(text, max_chars)
        section = _section_hint(text)
        for ordinal, content in enumerate(parts):
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            chunk_id = hashlib.sha256(
                f"{paper_id}:{page}:{ordinal}:{content_hash}".encode("utf-8")
            ).hexdigest()
            chunks.append(
                {
                    "id": chunk_id,
                    "paper_id": paper_id,
                    "page": page,
                    "section": section,
                    "ordinal": ordinal,
                    "content": content,
                    "content_hash": content_hash,
                }
            )
    return chunks


def analysis_input_hash(
    paper: dict[str, Any], analysis_type: str, asset_dir: Path
) -> str:
    material: dict[str, Any] = {
        "analysis_type": analysis_type,
        "paper_id": paper.get("id", ""),
        "title": paper.get("title", ""),
        "text": paper.get("extracted_text", ""),
        "summary_pairs": paper.get("summary_pairs", []),
        "summary_blocks": paper.get("summary_blocks", []),
    }
    if analysis_type == "figure_analysis":
        assets = []
        for asset in paper.get("visual_assets", []):
            filename = str(asset.get("filename", ""))
            path = asset_dir / str(paper.get("id", "")) / filename
            file_hash = ""
            if path.is_file():
                file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            assets.append(
                {
                    "filename": filename,
                    "page": asset.get("page"),
                    "content_hash": file_hash,
                }
            )
        material["assets"] = assets
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def generate_quick_read(
    paper: dict[str, Any], chunks: list[dict[str, Any]], settings: dict[str, str]
) -> tuple[dict[str, Any], str, int]:
    if provider_available(settings):
        evidence = _format_evidence(chunks, max_chars=52000)
        raw, token_count = request_structured_json(
            QUICK_READ_PROMPT,
            f"Title: {paper.get('title', '')}\n\nEvidence:\n{evidence}",
            settings,
            max_tokens=5000,
            timeout=180,
        )
        return _normalize_quick_read(raw, chunks), "openai_compatible", token_count
    return local_quick_read(paper, chunks), "local", 0


def local_quick_read(
    paper: dict[str, Any], chunks: list[dict[str, Any]]
) -> dict[str, Any]:
    pairs = [pair for pair in paper.get("summary_pairs", []) if isinstance(pair, dict)]
    if paper.get("summary_blocks"):
        pairs = [
            {
                "section_en": "",
                "section_zh": "",
                "en": block.get("text_en", ""),
                "zh": block.get("text_zh", ""),
                "page_refs": block.get("page_refs", []),
            }
            for block in paper.get("summary_blocks", [])
            if isinstance(block, dict) and block.get("type") != "heading"
        ]
    used: set[int] = set()

    def select(keywords: tuple[str, ...]) -> dict[str, Any]:
        for index, pair in enumerate(pairs):
            haystack = " ".join(
                str(pair.get(field, ""))
                for field in ("section_en", "section_zh", "en", "zh")
            ).casefold()
            if index not in used and any(keyword in haystack for keyword in keywords):
                used.add(index)
                page = _first_page(pair.get("page_refs")) or _fallback_page(chunks)
                return {"text": str(pair.get("zh") or pair.get("en", "")).strip(), "page": page}
        for chunk in chunks:
            content = str(chunk.get("content", ""))
            if any(keyword in content.casefold() for keyword in keywords):
                return {"text": _first_sentence(content), "page": int(chunk.get("page", 1))}
        if chunks:
            chunk = chunks[min(len(used), len(chunks) - 1)]
            return {"text": _first_sentence(str(chunk.get("content", ""))), "page": int(chunk.get("page", 1))}
        return {"text": "当前 PDF 没有可提取的文本证据。", "page": 1}

    headline = select(("conclusion", "result", "method", "propose"))
    sections = [
        {"key": key, "title": title, "items": [select(keywords)]}
        for key, title, keywords in SECTION_SPECS
    ]
    return {"headline": headline, "sections": sections, "analysis_mode": "local_evidence"}


def generate_figure_analysis(
    paper: dict[str, Any], chunks: list[dict[str, Any]], settings: dict[str, str]
) -> tuple[dict[str, Any], str, int]:
    candidates = _figure_candidates(paper, chunks)
    if provider_available(settings) and candidates:
        raw, token_count = request_structured_json(
            FIGURE_ANALYSIS_PROMPT,
            json.dumps({"title": paper.get("title", ""), "candidates": candidates}, ensure_ascii=False),
            settings,
            max_tokens=4000,
            timeout=180,
        )
        return _normalize_figure_analysis(raw, candidates), "openai_compatible", token_count
    figures = []
    for candidate in candidates:
        page = candidate["page"]
        figures.append(
            {
                "page": page,
                "asset_path": candidate["asset_path"],
                "asset_kind": candidate["asset_kind"],
                "caption": candidate["caption"],
                "title": candidate["title"],
                "look_for": (
                    "先看表头、行列定义和关键数值，再比较基线与完整系统。"
                    if candidate["asset_kind"] == "table"
                    else "先看图表标题、坐标轴、图例和相对变化，再核对正文解释。"
                ),
                "evidence": f"图表标题：{candidate['caption']}",
                "importance": "该视觉区域具有明确的图表编号和标题，是精读方法或实验结果的高价值入口。",
            }
        )
    return {
        "figures": figures,
        "analysis_mode": "text_grounded",
        "empty_reason": "" if figures else "未检测到具有明确编号和标题的图表区域。",
    }, "local", 0


def answer_with_citations(
    question: str, chunks: list[dict[str, Any]], settings: dict[str, str]
) -> tuple[str, list[str], int]:
    if not provider_available(settings):
        raise SummaryError("Question answering requires an OpenAI-compatible model configuration")
    source_map = {f"S{index}": chunk for index, chunk in enumerate(chunks, start=1)}
    evidence = "\n\n".join(
        f"[{source_id}] Paper {chunk.get('paper_id')} page {chunk.get('page')}:\n{chunk.get('content')}"
        for source_id, chunk in source_map.items()
    )
    raw, token_count = request_structured_json(
        QA_PROMPT,
        f"Question: {question}\n\nSources:\n{evidence}",
        settings,
        max_tokens=3000,
        timeout=150,
    )
    answer = str(raw.get("answer", "")).strip()
    citations = []
    for value in raw.get("citations", []):
        source_id = str(value).strip().upper()
        if source_id in source_map and source_id not in citations:
            citations.append(source_id)
    if not answer or not citations:
        raise SummaryError("The model did not return an answer with valid retrieved citations")
    return answer, citations, token_count


def _normalize_quick_read(
    raw: dict[str, Any], chunks: list[dict[str, Any]]
) -> dict[str, Any]:
    valid_pages = {int(chunk.get("page", 1)) for chunk in chunks} or {1}

    def fact(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        text = str(value.get("text", "")).strip()[:1800]
        try:
            page = int(value.get("page", 0))
        except (TypeError, ValueError):
            return None
        return {"text": text, "page": page} if text and page in valid_pages else None

    headline = fact(raw.get("headline"))
    sections = []
    for key, title, _ in SECTION_SPECS:
        source = next(
            (item for item in raw.get("sections", []) if isinstance(item, dict) and item.get("key") == key),
            {},
        )
        items = [item for item in (fact(value) for value in source.get("items", [])) if item]
        if items:
            sections.append({"key": key, "title": str(source.get("title") or title)[:80], "items": items[:5]})
    if headline is None or len(sections) < 4:
        raise SummaryError("Quick-read response did not contain enough page-grounded evidence")
    return {"headline": headline, "sections": sections, "analysis_mode": "model"}


def _normalize_figure_analysis(
    raw: dict[str, Any], candidates: list[dict[str, Any]]
) -> dict[str, Any]:
    by_page = {candidate["page"]: candidate for candidate in candidates}
    figures = []
    for item in raw.get("figures", []):
        if not isinstance(item, dict):
            continue
        try:
            page = int(item.get("page", 0))
        except (TypeError, ValueError):
            continue
        candidate = by_page.get(page)
        if candidate is None:
            continue
        values = {
            key: str(item.get(key, "")).strip()[:1800]
            for key in ("look_for", "evidence", "importance")
        }
        if not all(values.values()):
            continue
        figures.append(
            {
                "page": page,
                "asset_path": candidate["asset_path"],
                "asset_kind": candidate["asset_kind"],
                "caption": candidate["caption"],
                "title": candidate["title"],
                **values,
            }
        )
    if not figures:
        raise SummaryError("Figure analysis returned no valid candidate-page evidence")
    return {"figures": figures, "analysis_mode": "text_grounded_model"}


def _figure_candidates(
    paper: dict[str, Any], chunks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_page: dict[int, str] = {}
    for chunk in chunks:
        page = int(chunk.get("page", 1))
        by_page[page] = f"{by_page.get(page, '')} {chunk.get('content', '')}".strip()[:5000]
    candidates = []
    for asset in paper.get("visual_assets", [])[:8]:
        try:
            page = int(asset.get("page", 0))
        except (TypeError, ValueError):
            continue
        kind = str(asset.get("kind", ""))
        caption = str(asset.get("caption", "")).strip()
        filename = str(asset.get("filename", ""))
        if (
            page < 1
            or kind not in {"figure", "table"}
            or not caption
            or not re.fullmatch(r"visual-[0-9]+-(?:figure|table)-[0-9a-z]+-[0-9a-f]{8}\.png", filename)
        ):
            continue
        candidates.append(
            {
                "page": page,
                "asset_path": f"assets/{paper.get('id')}/{filename}",
                "asset_kind": kind,
                "caption": caption,
                "title": caption,
                "page_text": by_page.get(page, caption),
            }
        )
    return candidates


def _format_evidence(chunks: list[dict[str, Any]], max_chars: int) -> str:
    parts = []
    total = 0
    for chunk in chunks:
        part = f"[Page {chunk.get('page')}] {chunk.get('content', '')}"
        if total + len(part) > max_chars:
            break
        parts.append(part)
        total += len(part)
    return "\n\n".join(parts)


def _split_text(text: str, max_chars: int) -> list[str]:
    sentences = [value.strip() for value in re.split(r"(?<=[.!?。！？])\s+", text) if value.strip()]
    chunks: list[str] = []
    current = ""
    for sentence in sentences or [text]:
        if current and len(current) + len(sentence) + 1 > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return chunks


def _section_hint(text: str) -> str:
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return first_line[:160]


def _first_sentence(text: str) -> str:
    value = " ".join(text.split())
    match = re.search(r"^.{40,700}?(?:[.!?。！？](?:\s|$)|$)", value)
    return (match.group(0) if match else value[:700]).strip()


def _first_page(value: Any) -> int | None:
    if not isinstance(value, list):
        return None
    for item in value:
        try:
            page = int(item)
        except (TypeError, ValueError):
            continue
        if page > 0:
            return page
    return None


def _fallback_page(chunks: list[dict[str, Any]]) -> int:
    return int(chunks[0].get("page", 1)) if chunks else 1
