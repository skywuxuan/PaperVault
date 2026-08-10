from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


SYSTEM_PROMPT = """Act as a senior research analyst writing a complete, evidence-grounded paper report.
Return strict JSON only with this shape:
{"pairs":[{"section_en":"System architecture","section_zh":"整体系统架构",
"en":"One complete evidence-bearing English statement.",
"zh":"与英文含义严格对应的一条完整中文陈述。","page_refs":[2]}]}

Write 40-55 aligned sentence pairs. Each pair must express exactly one self-contained claim,
configuration, mechanism, result, limitation, or implication; the English and Chinese must be
direct equivalents. A semicolon may connect tightly related clauses, but do not put multiple
independent sentences into one pair. Group the pairs into a detailed research-report outline that
covers, when supported by the paper: basic information; research background and existing problems;
overall framework; every major architecture component; data construction and augmentation;
training stages and trainable/frozen parameters; prompts, objectives, rewards, and inference;
datasets and sample ratios; evaluation metrics; model sizes and hyperparameters; ablations;
all important quantitative results from figures and tables; conclusions; limitations and future
work; and innovations or engineering takeaways.

Prefer concrete facts over generic prose. Preserve exact model names, dataset sizes, sample counts,
ratios, Top-k choices, parameter counts, learning rates, reward definitions, baseline values, and
before/after metrics. Explain what every reported number demonstrates. Do not invent affiliations,
dates, experiments, limitations, or future work that the paper does not support. If the paper does
not state something, omit it. Chinese must be fluent professional Chinese: translate ordinary
English labels such as "General Task" into Chinese, while retaining standard acronyms, model names,
dataset names, formulas, and proper nouns when precision requires them. page_refs must contain the
supporting 1-based PDF page numbers whenever they can be inferred. Do not return markdown, term
mappings, commentary, or any keys other than pairs and the fields shown above."""

TRANSLATION_PROMPT = """Translate an English academic word or short phrase into Chinese using the
provided sentence context. Return strict JSON only:
{"translation_zh":"concise Chinese translation","note_zh":"one short context-specific explanation"}
Translate the selected text itself rather than the whole sentence. Prefer the domain meaning used
in the context. Preserve standard acronyms or model names when needed, but explain their Chinese
meaning. Do not use markdown."""


ZH_TEXT_REPLACEMENTS = {
    "General Task": "通用任务",
}

TERM_ALIASES = [
    ("General Task", "通用任务"),
    ("General", "通用"),
    ("Task", "任务"),
]


TERM_DICTIONARY = [
    ("large language model", "大语言模型"),
    ("automatic speech recognition", "自动语音识别"),
    ("contextual biasing", "上下文偏置"),
    ("hotword retrieval", "热词检索"),
    ("named entities", "命名实体"),
    ("large vocabulary", "大规模词表"),
    ("reinforcement learning", "强化学习"),
    ("fuzzy matching", "模糊匹配"),
    ("data augmentation", "数据增强"),
    ("word error rate", "词错误率"),
    ("keyword error rate", "关键词错误率"),
    ("sentence accuracy", "句级准确率"),
    ("retrieval-augmented generation", "检索增强生成"),
    ("mixture-of-experts", "混合专家模型"),
    ("audio encoder", "音频编码器"),
    ("text encoder", "文本编码器"),
    ("beam search", "束搜索"),
]


class SummaryError(RuntimeError):
    pass


def generate_summary(title: str, text: str, settings: dict[str, str]) -> tuple[list[dict[str, Any]], str]:
    provider = settings.get("provider", "local")
    if provider == "local":
        return local_draft(title, text), "local"
    if provider == "openai_compatible":
        return openai_compatible(title, text, settings), provider
    raise SummaryError(f"Unsupported provider: {provider}")


def local_draft(title: str, text: str) -> list[dict[str, Any]]:
    sentences = _candidate_sentences(text)
    if not sentences:
        sentences = [f"The PDF titled {title} was imported, but no extractable text was found."]
    selected = sentences[:8]
    sections = [
        ("Problem and motivation", "问题与动机", "论文围绕一个具有实际价值的技术问题展开，指出现有方案在准确性、规模化或鲁棒性方面仍有不足。"),
        ("Problem and motivation", "问题与动机", "作者从相关研究与真实应用需求出发，说明该问题为何值得进一步优化。"),
        ("Method overview", "方法总览", "论文提出一套由多个模块协同组成的方法框架，并明确各阶段的输入、处理过程与输出。"),
        ("Key components", "关键组件", "核心组件负责提取有效信息、缩小候选范围，并将结果传递给后续模型完成任务。"),
        ("Training and data", "训练与数据", "训练流程结合论文描述的数据构造、样本配比与优化策略，以提升模型在目标场景中的稳定性。"),
        ("Experiments and metrics", "实验与指标", "实验部分使用针对任务目标设计的数据集、基线与评价指标，对各模块贡献进行对比。"),
        ("Quantitative results", "定量结果", "结果表明完整方法相较基线取得一致改进，但不同参数选择之间仍存在精度与干扰的权衡。"),
        ("Limitations and takeaways", "局限与启示", "现有结论主要受论文所用数据与设置约束，后续仍需在更多语言、领域或部署条件下验证。"),
    ]
    pairs: list[dict[str, Any]] = []
    for index, sentence in enumerate(selected):
        section_en, section_zh, chinese = sections[min(index, len(sections) - 1)]
        lowered = sentence.casefold()
        terms = [
            {"en": en, "zh": zh}
            for en, zh in TERM_DICTIONARY
            if en in lowered and zh in chinese
        ][:8]
        pairs.append(
            {
                "section_en": section_en,
                "section_zh": section_zh,
                "en": sentence,
                "zh": chinese,
                "terms": terms,
                "page_refs": [],
            }
        )
    return pairs


def openai_compatible(title: str, text: str, settings: dict[str, str]) -> list[dict[str, Any]]:
    model = settings.get("model", "gpt-4.1-mini")
    try:
        max_chars = max(5000, min(int(settings.get("max_input_chars", "60000")), 300000))
    except ValueError:
        max_chars = 60000
    excerpt = text[:max_chars]
    payload = {
        "model": model,
        "temperature": 0.2,
        "max_tokens": 12000,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Title: {title}\n\nPaper text:\n{excerpt}",
            },
        ],
    }
    _apply_provider_controls(payload, settings)
    return _parse_pairs(_request_chat_completion(payload, settings, timeout=240))


def translate_english(text: str, context: str, settings: dict[str, str]) -> dict[str, str]:
    if settings.get("provider") != "openai_compatible":
        raise SummaryError("Word translation requires an OpenAI-compatible model")
    payload = {
        "model": settings.get("model", "gpt-4.1-mini"),
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": TRANSLATION_PROMPT},
            {
                "role": "user",
                "content": f"Selected text: {text[:240]}\n\nSentence context: {context[:3000]}",
            },
        ],
    }
    _apply_provider_controls(payload, settings)
    content = _request_chat_completion(payload, settings, timeout=90).strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.I)
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise SummaryError("Translation model returned invalid JSON") from exc
    translation = str(data.get("translation_zh", "")).strip()
    note = str(data.get("note_zh", "")).strip()
    if not translation:
        raise SummaryError("Translation model returned no Chinese translation")
    return {"translation_zh": translation[:500], "note_zh": note[:1000]}


def request_structured_json(
    system_prompt: str,
    user_prompt: str,
    settings: dict[str, str],
    *,
    max_tokens: int = 4000,
    timeout: int = 180,
) -> tuple[dict[str, Any], int]:
    if settings.get("provider") != "openai_compatible":
        raise SummaryError("This analysis requires an OpenAI-compatible model")
    payload = {
        "model": settings.get("model", "gpt-4.1-mini"),
        "temperature": 0.1,
        "max_tokens": max(500, min(max_tokens, 16000)),
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    _apply_provider_controls(payload, settings)
    content, usage = _request_chat_completion_result(payload, settings, timeout)
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I)
    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise SummaryError("LLM returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise SummaryError("LLM returned a non-object JSON response")
    try:
        token_count = int(usage.get("total_tokens", 0))
    except (TypeError, ValueError):
        token_count = 0
    return result, max(0, token_count)


def _apply_provider_controls(payload: dict[str, Any], settings: dict[str, str]) -> None:
    base_url = settings.get("base_url", "")
    hostname = (urllib.parse.urlparse(base_url).hostname or "").casefold()
    model = settings.get("model", "").casefold()
    if hostname == "api.deepseek.com" and model.startswith("deepseek-v4"):
        payload["thinking"] = {"type": "disabled"}


def _request_chat_completion(
    payload: dict[str, Any], settings: dict[str, str], timeout: int
) -> str:
    content, _ = _request_chat_completion_result(payload, settings, timeout)
    return content


def _request_chat_completion_result(
    payload: dict[str, Any], settings: dict[str, str], timeout: int
) -> tuple[str, dict[str, Any]]:
    api_key = os.environ.get("PAPER_VAULT_API_KEY") or settings.get("api_key", "")
    base_url = settings.get("base_url", "https://api.openai.com/v1").rstrip("/")
    endpoint = f"{base_url}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        endpoint, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise SummaryError(f"LLM request failed ({exc.code}): {detail}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        raise SummaryError(f"LLM request failed: {exc}") from exc
    try:
        choice = result["choices"][0]
        message = choice["message"]
        content = message.get("content")
    except (KeyError, IndexError, TypeError) as exc:
        raise SummaryError("LLM response did not contain chat completion content") from exc
    if not content and message.get("reasoning_content"):
        finish_reason = choice.get("finish_reason", "unknown")
        raise SummaryError(
            f"LLM response contained reasoning_content only (finish_reason={finish_reason})"
        )
    if not content:
        raise SummaryError("LLM response contained empty chat completion content")
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    return str(content), usage


def _parse_pairs(content: str) -> list[dict[str, Any]]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise SummaryError("LLM returned invalid JSON") from exc
    raw_pairs = data.get("pairs", []) if isinstance(data, dict) else []
    pairs: list[dict[str, Any]] = []
    for pair in raw_pairs:
        if not isinstance(pair, dict) or not pair.get("en") or not pair.get("zh"):
            continue
        paragraph_en = str(pair.get("en", "")).strip()
        paragraph_zh = _normalize_chinese_text(str(pair.get("zh", "")).strip())
        terms = []
        for term in pair.get("terms", []):
            if not isinstance(term, dict):
                continue
            en_term = str(term.get("en", "")).strip()
            zh_term = _normalize_chinese_text(str(term.get("zh", "")).strip())
            if (
                en_term and zh_term
                and en_term.casefold() in paragraph_en.casefold()
                and zh_term in paragraph_zh
            ):
                terms.append({"en": en_term, "zh": zh_term})
        existing_terms = {(item["en"].casefold(), item["zh"]) for item in terms}
        for en_term, zh_term in TERM_ALIASES:
            key = (en_term.casefold(), zh_term)
            if (
                key not in existing_terms
                and en_term.casefold() in paragraph_en.casefold()
                and zh_term in paragraph_zh
            ):
                terms.append({"en": en_term, "zh": zh_term})
                existing_terms.add(key)
        page_refs = []
        for page in pair.get("page_refs", []):
            try:
                page_number = int(page)
            except (TypeError, ValueError):
                continue
            if page_number > 0:
                page_refs.append(page_number)
        pairs.append(
            {
                "section_en": str(pair.get("section_en", "")).strip(),
                "section_zh": str(pair.get("section_zh", "")).strip(),
                "en": paragraph_en,
                "zh": paragraph_zh,
                "terms": terms[:32],
                "page_refs": list(dict.fromkeys(page_refs))[:8],
            }
        )
    if not pairs:
        raise SummaryError("LLM returned no aligned summary pairs")
    return pairs[:60]


def _normalize_chinese_text(value: str) -> str:
    normalized = value
    for source, target in ZH_TEXT_REPLACEMENTS.items():
        normalized = re.sub(rf"\b{re.escape(source)}\b", target, normalized, flags=re.I)
    return re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", normalized)


def _candidate_sentences(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    chunks = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", normalized)
    seen: set[str] = set()
    candidates: list[str] = []
    for chunk in chunks:
        sentence = chunk.strip()
        if not 70 <= len(sentence) <= 420:
            continue
        key = sentence.casefold()
        if key in seen or re.match(r"^(copyright|references|figure|table)\b", sentence, re.I):
            continue
        seen.add(key)
        candidates.append(sentence)
        if len(candidates) >= 12:
            break
    return candidates
