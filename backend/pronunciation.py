from __future__ import annotations

import re
from functools import lru_cache


ARPABET_TO_IPA = {
    "AA": "ɑ",
    "AE": "æ",
    "AH": "ə",
    "AO": "ɔ",
    "AW": "aʊ",
    "AY": "aɪ",
    "B": "b",
    "CH": "tʃ",
    "D": "d",
    "DH": "ð",
    "EH": "ɛ",
    "ER": "ɚ",
    "EY": "eɪ",
    "F": "f",
    "G": "ɡ",
    "HH": "h",
    "IH": "ɪ",
    "IY": "i",
    "JH": "dʒ",
    "K": "k",
    "L": "l",
    "M": "m",
    "N": "n",
    "NG": "ŋ",
    "OW": "oʊ",
    "OY": "ɔɪ",
    "P": "p",
    "R": "r",
    "S": "s",
    "SH": "ʃ",
    "T": "t",
    "TH": "θ",
    "UH": "ʊ",
    "UW": "u",
    "V": "v",
    "W": "w",
    "Y": "j",
    "Z": "z",
    "ZH": "ʒ",
}


@lru_cache(maxsize=1)
def _pronunciations() -> dict[str, list[list[str]]]:
    try:
        import cmudict
    except ImportError:
        return {}
    return cmudict.dict()


def american_ipa(term: str) -> str:
    words = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", str(term).casefold())
    if not words or len(words) > 8:
        return ""
    rendered: list[str] = []
    dictionary = _pronunciations()
    for word in words:
        variants = dictionary.get(word)
        if not variants:
            return ""
        rendered.append(_phones_to_ipa(variants[0]))
    return f"/{' '.join(rendered)}/"


def _phones_to_ipa(phones: list[str]) -> str:
    stress_markers: dict[int, str] = {}
    previous_vowel = -1
    for index, phone in enumerate(phones):
        match = re.fullmatch(r"([A-Z]+)([012])?", phone)
        if not match or match.group(1) not in {
            "AA", "AE", "AH", "AO", "AW", "AY", "EH", "ER", "EY",
            "IH", "IY", "OW", "OY", "UH", "UW",
        }:
            continue
        stress = match.group(2)
        if stress in {"1", "2"}:
            onset = 0 if previous_vowel < 0 else max(previous_vowel + 1, index - 1)
            stress_markers[onset] = "ˈ" if stress == "1" else "ˌ"
        previous_vowel = index

    parts: list[str] = []
    for index, phone in enumerate(phones):
        match = re.fullmatch(r"([A-Z]+)([012])?", phone)
        if not match:
            continue
        symbol, stress = match.groups()
        ipa = ARPABET_TO_IPA.get(symbol, "")
        if symbol == "AH" and stress in {"1", "2"}:
            ipa = "ʌ"
        elif symbol == "ER" and stress in {"1", "2"}:
            ipa = "ɝ"
        if index in stress_markers:
            ipa = f"{stress_markers[index]}{ipa}"
        parts.append(ipa)
    return "".join(parts)
