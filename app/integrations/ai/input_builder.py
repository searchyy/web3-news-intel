from __future__ import annotations

import json
import re
from typing import Any

from bs4 import BeautifulSoup

from app.db.models import Event
from app.integrations.ai.schemas import AIEventExcerpt, AIEventInput, InputQuality

MAX_EXCERPTS = 3
MAX_EXCERPT_CHARS = 2000
MAX_TOTAL_INPUT_CHARS = 8000
MAX_TITLE_CHARS = 1500
MAX_SUMMARY_CHARS = 1500
MAX_METADATA_CHARS = 1200
MIN_EXCERPT_CHARS = 80

SECRET_KEY_MARKERS = (
    "secret",
    "token",
    "password",
    "cookie",
    "authorization",
    "api_key",
    "apikey",
    "header",
    "raw",
    "html",
)

SAFE_TEXT_KEYS = (
    "content_excerpt",
    "excerpt",
    "summary",
    "description",
    "abstract",
    "snippet",
    "details",
)

HTML_TAG_PATTERN = re.compile(r"<[a-zA-Z!/][^>]{0,200}>")
WHITESPACE_PATTERN = re.compile(r"\s+")
SECRET_VALUE_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9._-]{8,}"),
    re.compile(r"(?i)\b(api[_-]?key|authorization|cookie|token|secret|password)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
)


def build_event_input(event: Event) -> AIEventInput:
    return AIInputBuilder().build(event)


class AIInputBuilder:
    def build(self, event: Event) -> AIEventInput:
        source_names, source_urls = self._source_metadata(event)
        title = _truncate_text(event.title, MAX_TITLE_CHARS)
        summary = _truncate_text(event.summary, MAX_SUMMARY_CHARS) if event.summary else None
        metadata = _sanitize_metadata(event.metadata_ or {})
        excerpts = self._build_excerpts(event, source_urls, title=title, summary=summary)
        excerpts = self._fit_total_budget(
            title=title,
            summary=summary,
            source_names=source_names,
            source_urls=source_urls,
            metadata=metadata,
            excerpts=excerpts,
        )
        return AIEventInput(
            event_id=event.id,
            title=title,
            summary=summary,
            source_names=source_names,
            published_at=event.published_at.isoformat() if event.published_at else None,
            original_urls=source_urls[:10],
            source_urls=source_urls[:10],
            category=event.category,
            severity=event.severity,
            symbols=list(event.symbols or [])[:20],
            chains=list(event.chains or [])[:20],
            excerpts=excerpts,
            input_quality=_input_quality(
                summary=summary,
                excerpts=excerpts,
                source_count=max(len(source_urls), len(source_names)),
            ),
            metadata=metadata,
        )

    def _source_metadata(self, event: Event) -> tuple[list[str], list[str]]:
        source_names: list[str] = []
        source_urls: list[str] = []
        for event_source in sorted(event.sources, key=lambda item: item.id or 0):
            if event_source.source and event_source.source.name not in source_names:
                source_names.append(event_source.source.name)
            if event_source.url and event_source.url not in source_urls:
                source_urls.append(event_source.url)
        return source_names, source_urls

    def _build_excerpts(
        self,
        event: Event,
        source_urls: list[str],
        *,
        title: str,
        summary: str | None,
    ) -> list[AIEventExcerpt]:
        allowed_urls = set(source_urls)
        excerpts: list[AIEventExcerpt] = []
        seen: set[str] = set()
        for event_source in sorted(event.sources, key=lambda item: item.id or 0):
            if len(excerpts) >= MAX_EXCERPTS:
                break
            if event_source.url not in allowed_urls:
                continue
            source_name = event_source.source.name if event_source.source else None
            for candidate in self._excerpt_candidates(event_source):
                text = _clean_text(candidate)
                if not text or _is_low_value_text(text, title=title, summary=summary):
                    continue
                text = _truncate_text(text, MAX_EXCERPT_CHARS)
                fingerprint = _fingerprint_text(text)
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                excerpts.append(
                    AIEventExcerpt(
                        source_name=source_name,
                        source_url=event_source.url,
                        text=text,
                    )
                )
                break
        return excerpts

    def _excerpt_candidates(self, event_source: Any) -> list[Any]:
        candidates: list[Any] = []
        raw_document = getattr(event_source, "raw_document", None)
        if raw_document is not None:
            metadata = raw_document.metadata_ or {}
            candidates.extend(_safe_mapping_texts(metadata))
            if raw_document.body and metadata.get("ai_excerpt_allowed") is True:
                candidates.extend(_body_candidates(raw_document.body, raw_document.content_type))
        return candidates

    def _fit_total_budget(
        self,
        *,
        title: str,
        summary: str | None,
        source_names: list[str],
        source_urls: list[str],
        metadata: dict[str, Any],
        excerpts: list[AIEventExcerpt],
    ) -> list[AIEventExcerpt]:
        base_chars = sum(len(item) for item in source_names)
        base_chars += sum(len(item) for item in source_urls)
        base_chars += len(title) + len(summary or "")
        base_chars += len(json.dumps(metadata, ensure_ascii=False, default=str))
        remaining = max(0, MAX_TOTAL_INPUT_CHARS - base_chars)
        bounded: list[AIEventExcerpt] = []
        for excerpt in excerpts[:MAX_EXCERPTS]:
            if remaining < MIN_EXCERPT_CHARS:
                break
            allowed = min(MAX_EXCERPT_CHARS, remaining)
            text = _truncate_text(excerpt.text, allowed)
            bounded.append(
                AIEventExcerpt(
                    source_name=excerpt.source_name,
                    source_url=excerpt.source_url,
                    text=text,
                )
            )
            remaining -= len(text)
        return bounded


def _safe_mapping_texts(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    candidates: list[str] = []
    for key in SAFE_TEXT_KEYS:
        item = value.get(key)
        if isinstance(item, str):
            candidates.append(item)
    nested_keys = ("item", "article", "entry", "source")
    for key in nested_keys:
        nested = value.get(key)
        if isinstance(nested, dict):
            candidates.extend(_safe_mapping_texts(nested))
    return candidates


def _body_candidates(body: str, content_type: str | None) -> list[str]:
    normalized_type = (content_type or "").split(";", 1)[0].strip().lower()
    if normalized_type in {"application/json", "text/json"} or body.lstrip().startswith(("{", "[")):
        return _json_body_candidates(body)
    if normalized_type in {"text/html", "application/xhtml+xml", "text/plain", ""}:
        return [body]
    if "xml" in normalized_type or body.lstrip().startswith("<"):
        return [body]
    return []


def _json_body_candidates(body: str) -> list[str]:
    try:
        parsed = json.loads(body)
    except Exception:
        return []
    values: list[str] = []
    stack: list[Any] = [parsed]
    while stack and len(values) < 10:
        item = stack.pop(0)
        if isinstance(item, dict):
            values.extend(_safe_mapping_texts(item))
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item[:10])
    return values


def _clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.replace("\x00", " ").strip()
    if not text:
        return None
    if HTML_TAG_PATTERN.search(text):
        soup = BeautifulSoup(text, "lxml")
        for node in soup.select(
            "script, style, nav, header, footer, aside, form, noscript, iframe, svg"
        ):
            node.decompose()
        text = soup.get_text(" ", strip=True)
    text = WHITESPACE_PATTERN.sub(" ", text).strip()
    text = _redact_secret_like_text(text)
    return text or None


def _is_low_value_text(text: str, *, title: str, summary: str | None) -> bool:
    if len(text) < MIN_EXCERPT_CHARS:
        return True
    lowered = text.lower()
    if title and lowered == title.lower():
        return True
    if summary and lowered == summary.lower():
        return True
    return False


def _input_quality(
    *,
    summary: str | None,
    excerpts: list[AIEventExcerpt],
    source_count: int,
) -> InputQuality:
    if source_count > 1 and (summary or excerpts):
        return "multi_source"
    if excerpts:
        return "excerpt"
    if summary:
        return "summary"
    return "title_only"


def _sanitize_metadata(value: Any, *, depth: int = 0) -> Any:
    if depth > 3:
        return "[truncated]"
    if isinstance(value, dict):
        result = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 20:
                result["truncated"] = True
                break
            lowered = str(key).lower()
            if any(marker in lowered for marker in SECRET_KEY_MARKERS):
                result[key] = "[redacted]"
            else:
                result[key] = _sanitize_metadata(item, depth=depth + 1)
        return result
    if isinstance(value, list):
        return [_sanitize_metadata(item, depth=depth + 1) for item in value[:10]]
    if isinstance(value, str):
        return _truncate_text(value, MAX_METADATA_CHARS)
    if isinstance(value, int | float | bool) or value is None:
        return value
    return str(value)[:200]


def _truncate_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - len("...[truncated]"))] + "...[truncated]"


def _fingerprint_text(value: str) -> str:
    return WHITESPACE_PATTERN.sub(" ", value.lower()).strip()[:500]


def _redact_secret_like_text(value: str) -> str:
    text = value
    for pattern in SECRET_VALUE_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text
