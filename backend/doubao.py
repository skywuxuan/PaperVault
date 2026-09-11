from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.request
from typing import Any

from .llm import SummaryError, request_chat_completion


DOUBAO_HOSTS = {"www.doubao.com", "doubao.com"}
MAX_PAGE_BYTES = 8 * 1024 * 1024
DOUBAO_MARKDOWN_PROMPT = """You are translating a structured Chinese research report into polished English.
Preserve the Markdown structure exactly: headings, list markers, blockquotes, tables, inline code,
and LaTeX math delimiters. Translate the meaning faithfully, without adding claims or page references.
Return strict JSON only: {\"markdown_en\": \"...\"}."""


def validate_doubao_url(url: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(str(url or "").strip())
    if parsed.scheme != "https" or (parsed.hostname or "").casefold() not in DOUBAO_HOSTS:
        raise ValueError("只支持 https://www.doubao.com/thread/... 分享链接")
    if not re.match(r"^/thread/[A-Za-z0-9_-]+/?$", parsed.path):
        raise ValueError("豆包链接必须是 /thread/{id} 分享页")
    return f"https://{parsed.hostname}{parsed.path}"


def fetch_doubao_markdown(url: str, timeout: int = 20) -> dict[str, str]:
    safe_url = validate_doubao_url(url)
    request = urllib.request.Request(
        safe_url,
        headers={"User-Agent": "PaperVault/1.0 (local research importer)"},
        method="GET",
    )
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
            return None

    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            content_type = str(response.headers.get("Content-Type", ""))
            if "text/html" not in content_type.casefold():
                raise ValueError("豆包分享页不是 HTML 文档")
            body = response.read(MAX_PAGE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        if isinstance(exc, ValueError):
            raise
        raise SummaryError(f"读取豆包分享页失败: {exc}") from exc
    if len(body) > MAX_PAGE_BYTES:
        raise ValueError("豆包分享页超过 8 MB，已停止导入")
    markdown = extract_markdown(body.decode("utf-8", errors="replace"))
    if not markdown:
        raise ValueError("未能从豆包分享页提取 Markdown 内容")
    title = extract_share_title(markdown) or "豆包论文解析"
    return {"title": title, "markdown": markdown, "url": safe_url}


def extract_markdown(page_html: str) -> str:
    decoded = html.unescape(page_html)
    candidates: list[str] = []
    marker_patterns = (
        re.compile(r'"text_block"\s*:\s*\{\s*"text"\s*:\s*"'),
        re.compile(r'text_block\\{1,3}"\s*:\s*\{\\{1,3}"text\\{1,3}"\s*:\s*\\{1,3}"'),
    )
    for marker_re in marker_patterns:
        for match in marker_re.finditer(decoded):
            start = match.end()
            if match.re.pattern.startswith('text_block\\'):
                escape_match = re.search(r'(\\+)"$', match.group())
                escape_depth = len(escape_match.group(1)) if escape_match else 1
                escaped_quote = "\\" * escape_depth + '"'
                end = decoded.find(f'{escaped_quote},{escaped_quote}icon_url', start)
                if end < 0:
                    end = decoded.find(f'{escaped_quote}}},', start)
                if end < 0:
                    continue
                raw = '"' + decoded[start:end] + '"'
            else:
                end = _json_string_end(decoded, start - 1)
                if end < 0:
                    continue
                raw = decoded[start - 1 : end + 1]
            try:
                candidate = json.loads(raw)
                if isinstance(candidate, str) and "\\n" in candidate:
                    candidate = candidate.replace("\\\\n", "\\n")
                    candidate = re.sub(r"\\n(?!eq\b|abla\b|ot\b|eg\b|u\b)", "\n", candidate)
                    candidate = re.sub(r"\\r(?!ho\b)", "\r", candidate)
            except (json.JSONDecodeError, UnicodeDecodeError):
                candidate = ""
            if isinstance(candidate, str) and "#" in candidate and len(candidate) > 200:
                candidates.append(candidate)
    headed = [candidate for candidate in candidates if re.match(r"^\s*#\s+", candidate)]
    if headed:
        candidates = headed
    if not candidates:
        return ""
    clean_candidates = [
        candidate
        for candidate in candidates
        if not any(marker in candidate for marker in ('"content_status"', '"content_block"', '"sec_sender"', '"tts_content"'))
    ]
    selected = min(clean_candidates or candidates, key=len)
    return normalize_markdown(selected)


def _json_string_end(value: str, start: int) -> int:
    escaped = False
    for index in range(start + 1, len(value)):
        char = value[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
        elif char == '"':
            return index
    return -1


def normalize_markdown(markdown: str) -> str:
    value = str(markdown or "").replace("\r\n", "\n").replace("\r", "\n")
    garbage_boundary = re.search(
        r'(?:\\+)?"}},(?:\\+)?"is_finish|(?:\\+)?",(?:\\+)?"icon_url',
        value,
    )
    if garbage_boundary and garbage_boundary.start() > 0:
        value = value[:garbage_boundary.start()]
    value = re.sub(
        r"\\u([0-9a-fA-F]{4})",
        lambda match: chr(int(match.group(1), 16)),
        value,
    )
    value = re.sub(r"\\n(?!eq\b|abla\b|ot\b|eg\b|u\b)", "\n", value)
    value = re.sub(r"\\r(?!ho\b)", "\r", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    value = re.sub(r"^[ \t]+(?=#)", "", value, flags=re.MULTILINE)
    heading_match = re.search(r"(?m)^#\s+", value)
    if heading_match:
        value = value[heading_match.start():]
    return value.strip()


def extract_share_title(markdown: str) -> str:
    for line in markdown.splitlines():
        match = re.match(r"^#\s+(.+?)\s*$", line)
        if match:
            title = match.group(1).strip()
            title = re.sub(r"^论文[《\"]", "", title)
            title = re.sub(r"[》\"]详细总结.*$", "", title).strip()
            return title or "豆包论文解析"
    return ""


def markdown_blocks(markdown: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    lines = normalize_markdown(markdown).splitlines()
    paragraph: list[str] = []
    list_items: list[dict[str, Any]] = []

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append({"type": "paragraph", "text": " ".join(item.strip() for item in paragraph)})
            paragraph.clear()

    def flush_list() -> None:
        if list_items:
            blocks.extend(list_items)
            list_items.clear()

    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            flush_paragraph()
            flush_list()
            index += 1
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            flush_paragraph()
            flush_list()
            blocks.append({"type": "heading", "level": min(4, len(heading.group(1))), "text": heading.group(2).strip()})
            index += 1
            continue
        list_match = re.match(r"^\s*(?:[-*+] |\d+[.)] )(.*)$", line)
        if list_match:
            flush_paragraph()
            list_items.append({"type": "bullet", "text": list_match.group(1).strip()})
            index += 1
            continue
        if line.lstrip().startswith(">"):
            flush_paragraph()
            flush_list()
            blocks.append({"type": "quote", "text": line.lstrip()[1:].strip()})
            index += 1
            continue
        if "|" in line and index + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{3,}", lines[index + 1]):
            flush_paragraph()
            flush_list()
            table_lines = [line]
            index += 1
            while index < len(lines) and lines[index].strip() and "|" in lines[index]:
                table_lines.append(lines[index])
                index += 1
            blocks.append({"type": "table", "text": "\n".join(table_lines)})
            continue
        paragraph.append(line)
        index += 1
    flush_paragraph()
    flush_list()
    return blocks


def translate_markdown_to_english(markdown: str, settings: dict[str, str]) -> tuple[str, str]:
    if settings.get("provider") != "openai_compatible":
        raise SummaryError("豆包英文版需要先配置 OpenAI-compatible 模型")
    model = str(settings.get("translation_model") or settings.get("model") or "gpt-4.1-mini").strip()
    # English prose plus JSON escaping can require substantially more output tokens
    # than the source's Chinese character count suggests.
    max_tokens = min(12000, max(4096, len(markdown) * 2))
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": DOUBAO_MARKDOWN_PROMPT},
            {"role": "user", "content": markdown},
        ],
        "temperature": 0.1,
    }
    result = request_chat_completion(payload, settings, timeout=900, task="translation")
    if str(result.get("finish_reason", "")).casefold() in {"length", "max_tokens"}:
        raise SummaryError("Doubao English model output was truncated")
    content = str(result.get("content", "")).strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json|markdown)?\s*|\s*```$", "", content, flags=re.I)
    try:
        data = json.loads(content, strict=False)
        translated = normalize_markdown(str(data.get("markdown_en", "")))
    except (json.JSONDecodeError, AttributeError, TypeError) as exc:
        # Some compatible providers ignore response_format and return the
        # requested Markdown directly. It is still a valid translation result.
        translated = normalize_markdown(content) if re.match(r"^\s*#\s+", content) else ""
        if not translated:
            raise SummaryError("Doubao English model returned invalid JSON") from exc
    if not translated:
        raise SummaryError("Doubao English model returned empty content")
    return translated, model
