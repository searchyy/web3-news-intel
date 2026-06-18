from __future__ import annotations


def detect_language(text: str, fallback: str | None = None) -> str | None:
    if any("\u4e00" <= char <= "\u9fff" for char in text):
        return "zh"
    if any(char.isalpha() for char in text):
        return fallback or "en"
    return fallback
