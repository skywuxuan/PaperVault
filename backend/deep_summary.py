from __future__ import annotations

import json
import math
import re
from collections import Counter
from typing import Any

from backend.llm import SummaryError, request_chat_completion


DEEP_SUMMARY_PROMPT_VERSION = "deep-summary-blocks-v1"
MAX_REPORT_OUTPUT_TOKENS = 32000
TRANSLATION_OUTPUT_TOKENS = 32000


ENGLISH_REPORT_PROMPT = """You are a senior research scientist writing a complete, detailed and readable
briefing of one research paper.

Read the supplied page-labelled paper in its entirety before composing the report.
Follow the paper's own narrative and technical order. Reconstruct its actual
hierarchy instead of forcing it into a fixed checklist.

The report should normally progress through:
basic information and core problem; background and limitations of prior work;
the proposed system in its real module order; data and training; experimental
setup; evaluation metrics; results and ablations in the order presented by the
paper; conclusions; explicitly stated limitations and future work; and a final
synthesis of the paper's genuine innovations.

These are ordering guidelines, not mandatory empty sections. Omit unsupported
sections and create paper-specific subsection headings when the paper introduces
distinct modules, stages or experiments.

Use coherent paragraphs for explanations, causal relationships and method
walkthroughs. Use bullet blocks only for genuinely parallel items such as module
components, datasets, hyperparameters, reward definitions or comparable results.
Never generate a fixed number of bullets or claims.

Explain not only what the paper reports, but also what important mechanisms and
quantitative results demonstrate. Preserve exact model names, datasets, sample
counts, ratios, parameter counts, hyperparameters, formulas, metrics, baseline
values and before/after results.

Distinguish clearly between:
- the base model architecture;
- newly proposed components;
- training stages;
- inference procedures;
- experimental findings.

Do not misrepresent a base component as a proposed innovation. Do not call a system
two-stage and then present unrelated architectural components as additional stages.

Use only the supplied paper. Do not invent authors, affiliations, publication dates,
experiments, limitations or future work. If bibliographic information is unclear,
omit it rather than infer it from filenames.

Return strict JSON only using paper_title and blocks. Each block must contain id,
type, text_en and page_refs. Heading blocks also contain level. Every page reference
must be supported by the supplied [Page N] labels. Do not return Chinese or markdown."""


CHUNK_EVIDENCE_PROMPT = """You are preparing exhaustive evidence notes for one consecutive part of a
research paper. Follow the supplied page order and preserve the paper's real headings,
method modules, experimental settings, quantitative results, limitations, and causal
explanations. Do not add a generic conclusion or facts from outside this part.

Return strict JSON only using paper_title and blocks. Each block must contain id,
type, text_en and page_refs. Heading blocks also contain level. Use paragraphs for
explanations and bullets only for genuinely parallel facts. Every page reference must
match a supplied [Page N] label. Do not return Chinese or markdown."""


TRANSLATION_PROMPT = """Translate the supplied structured English research report into fluent professional
Chinese.

Return strict JSON containing translations only. Preserve every block id and the
original order. Translate exactly one block into exactly one block. Do not merge,
split, omit or add information.

Preserve all numbers, formulas, model names, dataset names, metric names, citations
and standard acronyms. Translate ordinary technical terminology consistently.
Do not change the certainty, scope, factual meaning or logical relationship of any
statement. Heading translations should be concise and suitable for a professional
research report.

Return no English blocks, page references, markdown or commentary."""


JSON_REPAIR_PROMPT = """Repair the supplied response into valid strict JSON without adding, removing,
summarizing, translating, or changing any factual content. Return JSON only. Preserve
the original object keys, array order, block IDs, text, numbers, and page references."""


PAGE_NUMBER_RE = re.compile(r"^(?:page\s*)?[-–—]?\s*\d+\s*[-–—]?$", re.I)
REFERENCE_HEADING_RE = re.compile(r"^(?:\d+[.\s]+)?(?:references|bibliography)\s*$", re.I)
REFERENCE_ENTRY_RE = re.compile(
    r"^(?:\[?\d{1,4}\]?\s*[.\]])|(?:[A-Z][A-Za-z'’-]+(?:,|\s+and\s+).{0,120}\b(?:19|20)\d{2}\b)|(?:https?://|doi:)"
)
APPENDIX_HEADING_RE = re.compile(r"^(?:appendix|supplementary material)\b", re.I)
NUMERIC_TOKEN_RE = re.compile(
    r"(?<![\w.])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:e[+-]?\d+)?(?:%|[kKMB])?",
    re.I,
)
BLOCK_ID_RE = re.compile(r"^(heading|paragraph|bullet)-\d{3,}$")


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    ascii_count = sum(1 for character in text if ord(character) < 128)
    non_ascii_count = len(text) - ascii_count
    return max(1, math.ceil(ascii_count / 3.7 + non_ascii_count / 1.8))


def model_context_tokens(settings: dict[str, str], model: str) -> int:
    configured = str(settings.get("context_window_tokens", "")).strip()
    if configured:
        try:
            value = int(configured)
        except ValueError:
            value = 0
        if value:
            return max(16000, min(value, 2_000_000))
    if model.casefold().startswith("deepseek-v4"):
        return 1_000_000
    return 128_000


def summary_input_token_budget(settings: dict[str, str], model: str) -> int:
    context_tokens = model_context_tokens(settings, model)
    reserved = MAX_REPORT_OUTPUT_TOKENS + max(8000, context_tokens // 50)
    return max(12000, int(context_tokens * 0.92) - reserved)


def prepare_page_documents(page_texts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    for index, raw_page in enumerate(page_texts, start=1):
        try:
            page_number = max(1, int(raw_page.get("page", index)))
        except (TypeError, ValueError):
            page_number = index
        text = _normalize_page_text(str(raw_page.get("text", "")))
        pages.append({"page": page_number, "text": text})
    if not pages:
        return []

    repeated_candidates: Counter[str] = Counter()
    display_by_key: dict[str, str] = {}
    for page in pages:
        lines = [line.strip() for line in page["text"].splitlines() if line.strip()]
        for line in dict.fromkeys(lines[:2] + lines[-2:]):
            key = _repeated_line_key(line)
            if key and len(line) <= 180:
                repeated_candidates[key] += 1
                display_by_key[key] = line
    threshold = max(3, math.ceil(len(pages) * 0.5))
    repeated = {key for key, count in repeated_candidates.items() if count >= threshold}

    reference_mode = False
    cleaned_pages: list[dict[str, Any]] = []
    for page_index, page in enumerate(pages):
        output_lines: list[str] = []
        lines = page["text"].splitlines()
        for line_index, line in enumerate(lines):
            stripped = line.strip()
            key = _repeated_line_key(stripped)
            at_edge = line_index < 3 or line_index >= max(0, len(lines) - 3)
            if at_edge and (key in repeated or PAGE_NUMBER_RE.fullmatch(stripped)):
                continue
            if page_index >= int(len(pages) * 0.6) and REFERENCE_HEADING_RE.fullmatch(stripped):
                reference_mode = True
                output_lines.append(stripped)
                continue
            if reference_mode and APPENDIX_HEADING_RE.match(stripped):
                reference_mode = False
            if reference_mode and REFERENCE_ENTRY_RE.match(stripped):
                continue
            output_lines.append(line.rstrip())
        text = _collapse_blank_lines("\n".join(output_lines)).strip()
        cleaned_pages.append({"page": page["page"], "text": text})
    return cleaned_pages


def page_labelled_text(pages: list[dict[str, Any]]) -> str:
    return "\n\n".join(
        f"[Page {int(page['page'])}]\n{str(page.get('text', '')).strip() or '[No extractable text]'}"
        for page in pages
    )


def build_summary_input_plan(
    page_texts: list[dict[str, Any]], settings: dict[str, str], model: str | None = None
) -> dict[str, Any]:
    selected_model = model or analysis_model(settings)
    pages = prepare_page_documents(page_texts)
    full_text = page_labelled_text(pages)
    budget = summary_input_token_budget(settings, selected_model)
    if estimate_tokens(full_text) <= budget:
        chunks = [full_text] if full_text else []
        mode = "full"
    else:
        chunks = chunk_page_documents(pages, budget)
        mode = "chunked"
    return {
        "mode": mode,
        "model": selected_model,
        "budget_tokens": budget,
        "estimated_tokens": estimate_tokens(full_text),
        "pages": pages,
        "chunks": chunks,
        "full_text": full_text,
    }


def chunk_page_documents(pages: list[dict[str, Any]], budget_tokens: int) -> list[str]:
    budget_tokens = max(1000, budget_tokens)
    page_segments: list[dict[str, Any]] = []
    for page in pages:
        labelled = page_labelled_text([page])
        if estimate_tokens(labelled) <= budget_tokens:
            page_segments.append(page)
            continue
        for segment in _split_oversized_page(page, budget_tokens):
            page_segments.append(segment)

    chunks: list[str] = []
    current: list[dict[str, Any]] = []
    for segment in page_segments:
        candidate = page_labelled_text(current + [segment])
        if current and estimate_tokens(candidate) > budget_tokens:
            chunks.append(page_labelled_text(current))
            current = [segment]
        else:
            current.append(segment)
    if current:
        chunks.append(page_labelled_text(current))
    return chunks


def generate_english_report(
    title: str, page_texts: list[dict[str, Any]], settings: dict[str, str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    if settings.get("provider") != "openai_compatible":
        raise SummaryError("Detailed paper analysis requires an OpenAI-compatible model")
    plan = build_summary_input_plan(page_texts, settings)
    if not plan["pages"] or not any(page["text"] for page in plan["pages"]):
        raise SummaryError("The PDF contains no extractable text for detailed analysis")

    valid_pages = {int(page["page"]) for page in plan["pages"]}
    usage_total = 0
    if plan["mode"] == "full":
        report, usage = _request_report(
            title,
            plan["full_text"],
            ENGLISH_REPORT_PROMPT,
            settings,
            plan["model"],
            valid_pages,
        )
        usage_total += usage
    else:
        evidence_blocks: list[dict[str, Any]] = []
        for index, chunk in enumerate(plan["chunks"], start=1):
            chunk_pages = _page_labels(chunk)
            chunk_report, usage = _request_report(
                title,
                chunk,
                CHUNK_EVIDENCE_PROMPT,
                settings,
                plan["model"],
                chunk_pages,
                user_prefix=f"Consecutive paper part {index} of {len(plan['chunks'])}.",
            )
            usage_total += usage
            evidence_blocks.extend(chunk_report["blocks"])
        evidence_text = _blocks_as_page_evidence(evidence_blocks)
        if estimate_tokens(evidence_text) > plan["budget_tokens"]:
            raise SummaryError("Page-grounded chunk evidence still exceeds the safe model context budget")
        report, usage = _request_report(
            title,
            evidence_text,
            ENGLISH_REPORT_PROMPT,
            settings,
            plan["model"],
            valid_pages,
            user_prefix="The following page-labelled evidence was prepared from consecutive paper chunks. Reconstruct one coherent report in the original paper order without duplicating facts.",
        )
        usage_total += usage

    return report, {
        "model": plan["model"],
        "input_mode": plan["mode"],
        "input_tokens_estimate": plan["estimated_tokens"],
        "input_budget_tokens": plan["budget_tokens"],
        "token_count": usage_total,
        "prompt_version": DEEP_SUMMARY_PROMPT_VERSION,
    }


def translate_report_blocks(
    blocks: list[dict[str, Any]], settings: dict[str, str]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if settings.get("provider") != "openai_compatible":
        raise SummaryError("Chinese report translation requires an OpenAI-compatible model")
    if not blocks:
        raise SummaryError("No English report blocks are available for translation")
    model = translation_model(settings)
    usage_total = 0
    translations: list[dict[str, str]] | None = None
    validation_error = ""
    for _ in range(2):
        result = _request_translation(blocks, settings, model, validation_error)
        usage_total += _usage_tokens(result.get("usage", {}))
        if _is_truncated(result.get("finish_reason")):
            validation_error = "The previous translation was truncated. Return every requested block."
            continue
        try:
            translations = _parse_translations(result["content"], blocks)
            break
        except SummaryError as exc:
            validation_error = str(exc)
    if translations is None:
        translations = []
        for chunk in _translation_chunks(blocks):
            chunk_result = _request_translation(chunk, settings, model, validation_error)
            usage_total += _usage_tokens(chunk_result.get("usage", {}))
            if _is_truncated(chunk_result.get("finish_reason")):
                raise SummaryError("Chinese translation was truncated after block-level retry")
            translations.extend(_parse_translations(chunk_result["content"], chunk))

    by_id = {item["id"]: item["text_zh"] for item in translations}
    merged = [{**block, "text_zh": by_id[block["id"]]} for block in blocks]
    return merged, {"model": model, "token_count": usage_total}


def analysis_model(settings: dict[str, str]) -> str:
    return str(settings.get("analysis_model") or settings.get("model") or "gpt-4.1-mini").strip()


def translation_model(settings: dict[str, str]) -> str:
    return str(settings.get("translation_model") or settings.get("model") or "gpt-4.1-mini").strip()


def normalize_report(
    data: dict[str, Any], valid_pages: set[int], fallback_title: str
) -> dict[str, Any]:
    raw_blocks = data.get("blocks", []) if isinstance(data, dict) else []
    blocks: list[dict[str, Any]] = []
    counters = {"heading": 0, "paragraph": 0, "bullet": 0}
    for raw in raw_blocks:
        if not isinstance(raw, dict):
            continue
        block_type = str(raw.get("type", "")).strip().casefold()
        if block_type not in counters:
            continue
        text = str(raw.get("text_en", "")).strip()
        if not text:
            continue
        refs = _valid_page_refs(raw.get("page_refs"), valid_pages)
        if block_type != "heading" and not refs:
            continue
        counters[block_type] += 1
        block: dict[str, Any] = {
            "id": f"{block_type}-{counters[block_type]:03d}",
            "type": block_type,
            "text_en": text,
            "page_refs": refs,
        }
        if block_type == "heading":
            try:
                level = int(raw.get("level", 2))
            except (TypeError, ValueError):
                level = 2
            block["level"] = 3 if level == 3 else 2
        blocks.append(block)
    _fill_heading_page_refs(blocks)
    if not any(block["type"] in {"paragraph", "bullet"} for block in blocks):
        raise SummaryError("LLM returned no page-grounded report content blocks")
    if any(not block["page_refs"] for block in blocks):
        raise SummaryError("LLM returned report blocks without valid page references")
    return {
        "paper_title": str(data.get("paper_title", "")).strip() or fallback_title,
        "blocks": blocks,
    }


def _request_report(
    title: str,
    source: str,
    system_prompt: str,
    settings: dict[str, str],
    model: str,
    valid_pages: set[int],
    user_prefix: str = "",
) -> tuple[dict[str, Any], int]:
    user_prompt = "\n\n".join(
        part for part in (user_prefix, f"Paper title metadata: {title}", "Paper content:", source) if part
    )
    result = _chat_json(system_prompt, user_prompt, settings, model, "analysis", MAX_REPORT_OUTPUT_TOKENS, 900)
    usage_total = _usage_tokens(result.get("usage", {}))
    raw_blocks: list[dict[str, Any]] = []
    paper_title = title

    if _is_truncated(result.get("finish_reason")):
        raw_blocks.extend(_salvage_blocks(result["content"]))
        for _ in range(5):
            if not raw_blocks:
                continuation_context = "No complete block was recovered; restart the report from the beginning."
            else:
                last = raw_blocks[-1]
                continuation_context = (
                    "Continue after the following last complete block and do not repeat earlier material:\n"
                    + json.dumps(last, ensure_ascii=False)
                )
            continuation = _chat_json(
                system_prompt,
                f"{user_prompt}\n\n{continuation_context}\nReturn paper_title and only the remaining blocks.",
                settings,
                model,
                "analysis",
                MAX_REPORT_OUTPUT_TOKENS,
                900,
            )
            usage_total += _usage_tokens(continuation.get("usage", {}))
            if _is_truncated(continuation.get("finish_reason")):
                recovered = _salvage_blocks(continuation["content"])
                if not recovered:
                    raise SummaryError("English report continuation was truncated before a complete block")
                raw_blocks.extend(recovered)
                continue
            data = _parse_or_repair_json(continuation["content"], settings, model)
            raw_blocks.extend(data.get("blocks", []))
            paper_title = str(data.get("paper_title", "")).strip() or paper_title
            break
        else:
            raise SummaryError("English report exceeded the continuation limit")
        data = {"paper_title": paper_title, "blocks": raw_blocks}
    else:
        data = _parse_or_repair_json(result["content"], settings, model)
    return normalize_report(data, valid_pages, title), usage_total


def _request_translation(
    blocks: list[dict[str, Any]],
    settings: dict[str, str],
    model: str,
    validation_error: str = "",
) -> dict[str, Any]:
    payload = {
        "blocks": [
            {"id": block["id"], "type": block["type"], "text_en": block["text_en"]}
            for block in blocks
        ]
    }
    prefix = f"The previous response failed validation: {validation_error}\n\n" if validation_error else ""
    return _chat_json(
        TRANSLATION_PROMPT,
        prefix + json.dumps(payload, ensure_ascii=False),
        settings,
        model,
        "translation",
        TRANSLATION_OUTPUT_TOKENS,
        600,
    )


def _chat_json(
    system_prompt: str,
    user_prompt: str,
    settings: dict[str, str],
    model: str,
    task: str,
    max_tokens: int,
    timeout: int,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    if task != "analysis":
        payload["temperature"] = 0.1
    return request_chat_completion(payload, settings, timeout=timeout, task=task)


def _parse_or_repair_json(content: str, settings: dict[str, str], model: str) -> dict[str, Any]:
    try:
        return _parse_json_object(content)
    except SummaryError:
        repaired = _chat_json(
            JSON_REPAIR_PROMPT,
            content,
            settings,
            model,
            "json_repair",
            MAX_REPORT_OUTPUT_TOKENS,
            300,
        )
        if _is_truncated(repaired.get("finish_reason")):
            raise SummaryError("JSON repair response was truncated")
        return _parse_json_object(repaired["content"])


def _parse_json_object(content: str) -> dict[str, Any]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise SummaryError("LLM returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise SummaryError("LLM returned a non-object JSON response")
    return data


def _parse_translations(content: str, blocks: list[dict[str, Any]]) -> list[dict[str, str]]:
    data = _parse_json_object(content)
    raw = data.get("translations", [])
    if not isinstance(raw, list):
        raise SummaryError("Translation response did not contain a translations array")
    expected_ids = [block["id"] for block in blocks]
    actual_ids = [str(item.get("id", "")) for item in raw if isinstance(item, dict)]
    if actual_ids != expected_ids:
        raise SummaryError("Translation block IDs or order did not match the English report")
    translations: list[dict[str, str]] = []
    for block, item in zip(blocks, raw, strict=True):
        text_zh = str(item.get("text_zh", "")).strip()
        if not text_zh:
            raise SummaryError(f"Translation was empty for block {block['id']}")
        if NUMERIC_TOKEN_RE.findall(block["text_en"]) != NUMERIC_TOKEN_RE.findall(text_zh):
            raise SummaryError(f"Translation changed numeric content for block {block['id']}")
        translations.append({"id": block["id"], "text_zh": text_zh})
    return translations


def _translation_chunks(blocks: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_tokens = 0
    limit = 10000
    for block in blocks:
        block_tokens = estimate_tokens(block["text_en"]) + 20
        if current and current_tokens + block_tokens > limit:
            chunks.append(current)
            current = []
            current_tokens = 0
        current.append(block)
        current_tokens += block_tokens
    if current:
        chunks.append(current)
    return chunks


def _blocks_as_page_evidence(blocks: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for block in blocks:
        refs = block.get("page_refs", [])
        labels = " ".join(f"[Page {page}]" for page in refs)
        level = f"Heading level {block.get('level', 2)}: " if block["type"] == "heading" else ""
        parts.append(f"{labels}\n{level}{block['text_en']}")
    return "\n\n".join(parts)


def _fill_heading_page_refs(blocks: list[dict[str, Any]]) -> None:
    for index, block in enumerate(blocks):
        if block["type"] != "heading" or block["page_refs"]:
            continue
        level = block.get("level", 2)
        refs: list[int] = []
        for following in blocks[index + 1 :]:
            if following["type"] == "heading" and following.get("level", 2) <= level:
                break
            refs.extend(following.get("page_refs", []))
        block["page_refs"] = list(dict.fromkeys(refs))


def _valid_page_refs(value: Any, valid_pages: set[int]) -> list[int]:
    if not isinstance(value, list):
        return []
    refs: list[int] = []
    for raw in value:
        try:
            page = int(raw)
        except (TypeError, ValueError):
            continue
        if page in valid_pages and page not in refs:
            refs.append(page)
    return refs


def _salvage_blocks(content: str) -> list[dict[str, Any]]:
    marker = re.search(r'"blocks"\s*:\s*\[', content)
    if not marker:
        return []
    decoder = json.JSONDecoder()
    cursor = marker.end()
    blocks: list[dict[str, Any]] = []
    while cursor < len(content):
        while cursor < len(content) and content[cursor] in " \r\n\t,":
            cursor += 1
        if cursor >= len(content) or content[cursor] == "]":
            break
        try:
            value, end = decoder.raw_decode(content, cursor)
        except json.JSONDecodeError:
            break
        if isinstance(value, dict):
            blocks.append(value)
        cursor = end
    return blocks


def _split_oversized_page(page: dict[str, Any], budget_tokens: int) -> list[dict[str, Any]]:
    units: list[str] = []
    for paragraph in re.split(r"\n\s*\n", str(page.get("text", ""))):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if estimate_tokens(paragraph) <= budget_tokens - 20:
            units.append(paragraph)
            continue
        sentences = re.split(r"(?<=[.!?。！？])\s+|\n(?=[A-Z0-9])", paragraph)
        units.extend(sentence.strip() for sentence in sentences if sentence.strip())
    segments: list[dict[str, Any]] = []
    current: list[str] = []
    for unit in units:
        candidate = "\n\n".join(current + [unit])
        if current and estimate_tokens(candidate) > budget_tokens - 20:
            segments.append({"page": page["page"], "text": "\n\n".join(current)})
            current = [unit]
        else:
            current.append(unit)
    if current:
        segments.append({"page": page["page"], "text": "\n\n".join(current)})
    return segments or [{"page": page["page"], "text": ""}]


def _normalize_page_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    lines = [re.sub(r"[ \t]+", " ", line).rstrip() for line in text.splitlines()]
    return _collapse_blank_lines("\n".join(lines)).strip()


def _collapse_blank_lines(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text)


def _repeated_line_key(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip().casefold()


def _page_labels(text: str) -> set[int]:
    return {int(value) for value in re.findall(r"\[Page\s+(\d+)\]", text)}


def _is_truncated(finish_reason: Any) -> bool:
    return str(finish_reason or "").casefold() in {"length", "max_tokens"}


def _usage_tokens(usage: Any) -> int:
    if not isinstance(usage, dict):
        return 0
    try:
        return max(0, int(usage.get("total_tokens", 0)))
    except (TypeError, ValueError):
        return 0
