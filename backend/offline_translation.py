from __future__ import annotations

import threading
from pathlib import Path

from .pronunciation import american_ipa


MODEL_DIRECTORY = (
    Path(__file__).resolve().parent.parent / "data" / "models" / "translate-en_zh-1_9"
)

_translator = None
_tokenizer = None
_load_lock = threading.Lock()


class OfflineTranslationError(RuntimeError):
    pass


ACADEMIC_GLOSSARY = {
    "asr": ("自动语音识别", "将语音信号自动转换为文字的技术或系统。"),
    "adapter": ("适配器；适配层", "连接不同网络模块、统一特征维度的轻量结构。"),
    "augmentation": ("增强；数据扩充", "通过变换或合成样本扩大训练数据的覆盖范围。"),
    "biasing": ("偏置；定向增强", "让模型在特定候选词或上下文上获得更高倾向。"),
    "candidate": ("候选项；候选结果", "检索或解码阶段保留、等待进一步排序的结果。"),
    "contextual biasing": ("上下文偏置", "利用外部词表或上下文信息提升特定词语的识别概率。"),
    "contextual": ("上下文相关的；结合语境的", "需要结合前后文或任务环境理解。"),
    "decoder": ("解码器；解码模块", "把内部表示转换为目标序列或预测结果的模块。"),
    "distractor": ("干扰项；干扰词", "与目标相似但不应被选择的候选内容。"),
    "embedding": ("嵌入；向量表示", "把离散对象编码成可计算的连续向量。"),
    "encoder": ("编码器；编码模块", "把输入转换为高层特征表示的模块。"),
    "fine-tuning": ("微调；适配训练", "在预训练模型上使用任务数据继续训练。"),
    "framework": ("框架；整体方案", "组织多个模块与处理阶段的系统结构。"),
    "general": ("通用的；一般的；总体的", "在论文中通常表示不限定特定领域或场景。"),
    "general task": ("通用任务", "不针对特定垂直领域的基准任务。"),
    "hotword": ("热词；重点词", "业务中需要重点识别的专有名词或高价值词语。"),
    "hotword retrieval": ("热词检索", "从大规模候选词表中筛选与当前输入最相关的热词。"),
    "inference": ("推理；模型预测", "训练完成后使用模型生成结果的过程。"),
    "keyword error rate": ("关键词错误率", "衡量目标关键词漏识别或错误识别比例的指标。"),
    "large language model": ("大语言模型", "基于大规模语料训练、具备生成与理解能力的语言模型。"),
    "learning": ("学习；训练", "从数据或反馈中更新模型能力的过程。"),
    "precision": ("精确率；查准率", "预测为正的结果中真正为正的比例。"),
    "recall": ("召回率；查全率", "所有真实目标中被系统成功找回的比例。"),
    "recognition": ("识别；辨认", "从输入信号中判定其对应类别或文字。"),
    "reinforcement learning": ("强化学习", "通过奖励信号优化策略或模型行为的学习方法。"),
    "reinforcement": ("强化；增强；加固", "表示通过反馈或额外机制加强某种行为或效果。"),
    "retrieval": ("检索；查找；召回", "从候选库中找出与查询最相关内容的过程。"),
    "retriever": ("检索器；召回模块", "负责从候选库中筛选相关结果的组件。"),
    "reward": ("奖励；奖励信号", "强化学习中衡量行为质量并指导优化的反馈值。"),
    "robustness": ("鲁棒性；稳健性", "系统在噪声、扰动或分布变化下保持性能的能力。"),
    "scalable": ("可扩展的；可伸缩的", "表示系统能力可以随数据量、词表或计算规模平稳增长。"),
    "scalability": ("可扩展性；伸缩能力", "系统在规模增长时继续保持效率与可用性的能力。"),
    "sentence accuracy": ("句子准确率", "整句识别完全正确的样本比例。"),
    "transcription": ("转写；转录文本", "把语音或音频内容转换成书面文字。"),
    "utterance": ("语句；话语片段", "一段连续、可独立处理的语音输入。"),
    "word error rate": ("词错误率", "以替换、删除和插入错误衡量识别质量的指标。"),
}


def offline_translation_available() -> bool:
    return (
        (MODEL_DIRECTORY / "model" / "model.bin").is_file()
        and (MODEL_DIRECTORY / "sentencepiece.model").is_file()
    )


def translate_english_offline(
    text: str, context: str = "", known_context_zh: str = ""
) -> dict[str, str]:
    source = " ".join(str(text).strip().split())[:240]
    if not source:
        raise OfflineTranslationError("没有可翻译的英文内容")

    glossary_key = source.casefold()
    glossary_entry = ACADEMIC_GLOSSARY.get(glossary_key) or ACADEMIC_GLOSSARY.get(
        glossary_key.replace("-", " ")
    )
    if glossary_entry:
        translation, definition = glossary_entry
        return {
            "translation_zh": translation,
            "definition_zh": definition,
            "context_translation_zh": _clean_context_translation(known_context_zh),
            "phonetic_us": american_ipa(source),
            "note_zh": "",
        }

    translator, tokenizer = _load_model()
    try:
        relevant_context = _relevant_context(context, source)
        texts = [source]
        if relevant_context and not known_context_zh:
            texts.append(relevant_context)
        encoded = [tokenizer.encode(item, out_type=str) for item in texts]
        results = translator.translate_batch(
            encoded,
            beam_size=4,
            num_hypotheses=3,
            replace_unknowns=True,
            max_decoding_length=256,
        )
        alternatives = _decode_hypotheses(tokenizer, results[0].hypotheses)
        translated = "；".join(alternatives[:3])
        context_translation = _clean_context_translation(known_context_zh)
        if len(results) > 1:
            context_candidates = _decode_hypotheses(tokenizer, results[1].hypotheses)
            context_translation = _clean_context_translation(
                context_candidates[0] if context_candidates else ""
            )
    except Exception as exc:  # CTranslate2 exposes several runtime-specific exceptions.
        raise OfflineTranslationError(f"离线翻译运行失败：{exc}") from exc

    if not translated or translated.casefold() == source.casefold():
        raise OfflineTranslationError("离线模型没有返回有效的中文释义")
    return {
        "translation_zh": translated[:500],
        "definition_zh": "",
        "context_translation_zh": context_translation,
        "phonetic_us": american_ipa(source),
        "note_zh": "",
    }


def _decode_hypotheses(tokenizer, hypotheses: list[list[str]]) -> list[str]:
    decoded: list[str] = []
    for tokens in hypotheses:
        value = tokenizer.decode(tokens).replace("▁", " ").replace("_", " ").strip()
        value = " ".join(value.split())
        if value and value.casefold() not in {item.casefold() for item in decoded}:
            decoded.append(value)
    return decoded


def _relevant_context(context: str, source: str) -> str:
    normalized = " ".join(str(context).strip().split())
    if not normalized:
        return ""
    sentences = [item.strip() for item in normalized.replace("?", ".").replace("!", ".").split(".")]
    match = next((item for item in sentences if source.casefold() in item.casefold()), normalized)
    return match[:500]


def _clean_context_translation(value: str) -> str:
    cleaned = " ".join(str(value).strip().split())
    return cleaned[:180]


def _load_model():
    global _translator, _tokenizer
    if _translator is not None and _tokenizer is not None:
        return _translator, _tokenizer
    if not offline_translation_available():
        raise OfflineTranslationError(
            "离线英译中模型尚未安装，请运行 setup-offline-translation.ps1"
        )
    with _load_lock:
        if _translator is None or _tokenizer is None:
            try:
                import ctranslate2
                import sentencepiece
            except ImportError as exc:
                raise OfflineTranslationError(
                    "离线翻译依赖缺失，请重新运行 start.ps1 安装依赖"
                ) from exc
            _translator = ctranslate2.Translator(
                str(MODEL_DIRECTORY / "model"),
                device="cpu",
                compute_type="auto",
                inter_threads=1,
                intra_threads=0,
            )
            _tokenizer = sentencepiece.SentencePieceProcessor(
                model_file=str(MODEL_DIRECTORY / "sentencepiece.model")
            )
    return _translator, _tokenizer
