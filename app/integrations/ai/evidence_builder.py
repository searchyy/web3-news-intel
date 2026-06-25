from __future__ import annotations

import json
import re
from typing import Any

from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field

from app.db.models import Event
from app.integrations.ai.schemas import InputQuality

MAX_EVIDENCE_SOURCES = 3
MAX_EXCERPT_CHARS = 2000
MAX_TOTAL_INPUT_CHARS = 8000
MAX_TITLE_CHARS = 1500
MAX_SUMMARY_CHARS = 1500
MAX_METADATA_VALUE_CHARS = 1200
MAX_METADATA_JSON_CHARS = 2000
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
    "body",
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


class EvidenceSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_name: str | None = None
    source_url: str


class EvidenceExcerpt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_name: str | None = None
    source_url: str
    text: str


class EvidencePack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: int
    title: str
    summary: str | None = None
    sources: list[EvidenceSource] = Field(default_factory=list)
    excerpts: list[EvidenceExcerpt] = Field(default_factory=list)
    input_quality: InputQuality = "title_only"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def source_names(self) -> list[str]:
        names: list[str] = []
        for source in self.sources:
            if source.source_name and source.source_name not in names:
                names.append(source.source_name)
        return names

    @property
    def source_urls(self) -> list[str]:
        return [source.source_url for source in self.sources]


def build_evidence_pack(event: Event) -> EvidencePack:
    return EvidencePackBuilder().build(event)


class EvidencePackBuilder:
    def build(self, event: Event) -> EvidencePack:
        title = _truncate_text(event.title, MAX_TITLE_CHARS)
        summary = _truncate_text(event.summary, MAX_SUMMARY_CHARS) if event.summary else None
        sources = self._sources(event)
        metadata = _fit_metadata_budget(_sanitize_metadata(event.metadata_ or {}))
        metadata = _fit_metadata_to_pack_budget(
            event_id=event.id,
            title=title,
            summary=summary,
            sources=sources,
            metadata=metadata,
        )
        excerpts = self._build_excerpts(event, sources, title=title, summary=summary)
        excerpts = _fit_excerpts_to_pack_budget(
            event_id=event.id,
            title=title,
            summary=summary,
            sources=sources,
            metadata=metadata,
            excerpts=excerpts,
        )
        return EvidencePack(
            event_id=event.id,
            title=title,
            summary=summary,
            sources=sources,
            excerpts=excerpts,
            input_quality=_input_quality(
                summary=summary,
                excerpts=excerpts,
                source_count=len(sources),
            ),
            metadata=metadata,
        )

    def _sources(self, event: Event) -> list[EvidenceSource]:
        sources: list[EvidenceSource] = []
        seen_urls: set[str] = set()
        for event_source in sorted(event.sources, key=lambda item: item.id or 0):
            if len(sources) >= MAX_EVIDENCE_SOURCES:
                break
            if not event_source.url or event_source.url in seen_urls:
                continue
            seen_urls.add(event_source.url)
            sources.append(
                EvidenceSource(
                    source_name=event_source.source.name if event_source.source else None,
                    source_url=event_source.url,
                )
            )
        return sources

    def _build_excerpts(
        self,
        event: Event,
        sources: list[EvidenceSource],
        *,
        title: str,
        summary: str | None,
    ) -> list[EvidenceExcerpt]:
        allowed_urls = {source.source_url for source in sources}
        excerpts: list[EvidenceExcerpt] = []
        seen: set[str] = set()
        for event_source in sorted(event.sources, key=lambda item: item.id or 0):
            if len(excerpts) >= MAX_EVIDENCE_SOURCES:
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
                    EvidenceExcerpt(
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
    excerpts: list[EvidenceExcerpt],
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
        return _truncate_text(value, MAX_METADATA_VALUE_CHARS)
    if isinstance(value, int | float | bool) or value is None:
        return value
    return str(value)[:200]


def _fit_metadata_budget(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    if _json_length(value) <= MAX_METADATA_JSON_CHARS:
        return value
    result: dict[str, Any] = {}
    omitted = False
    for key, item in value.items():
        candidate = dict(result)
        candidate[key] = item
        if _json_length(candidate) <= MAX_METADATA_JSON_CHARS:
            result[key] = item
            continue
        if isinstance(item, str):
            fitted = _fit_metadata_string(result, key, item)
            if fitted is not None:
                result[key] = fitted
                omitted = True
                break
        omitted = True
        break
    if omitted:
        result["truncated"] = True
    return result


def _fit_metadata_string(existing: dict[str, Any], key: str, value: str) -> str | None:
    suffix = "...[truncated]"
    low = 0
    high = min(len(value), MAX_METADATA_VALUE_CHARS)
    best: str | None = None
    while low <= high:
        middle = (low + high) // 2
        candidate_value = _truncate_text(value, middle)
        candidate = dict(existing)
        candidate[key] = candidate_value
        candidate["truncated"] = True
        if _json_length(candidate) <= MAX_METADATA_JSON_CHARS:
            best = candidate_value
            low = middle + 1
        else:
            high = middle - 1
    if best is None or len(best) < len(suffix):
        return None
    return best


def _fit_metadata_to_pack_budget(
    *,
    event_id: int,
    title: str,
    summary: str | None,
    sources: list[EvidenceSource],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    if (
        _pack_length(
            event_id=event_id,
            title=title,
            summary=summary,
            sources=sources,
            metadata=metadata,
            excerpts=[],
        )
        <= MAX_TOTAL_INPUT_CHARS
    ):
        return metadata
    result = dict(metadata)
    while result and (
        _pack_length(
            event_id=event_id,
            title=title,
            summary=summary,
            sources=sources,
            metadata=result,
            excerpts=[],
        )
        > MAX_TOTAL_INPUT_CHARS
    ):
        key = next(reversed(result))
        if key == "truncated" and len(result) == 1:
            return {}
        result.pop(key)
        if result:
            result["truncated"] = True
    return result


def _fit_excerpts_to_pack_budget(
    *,
    event_id: int,
    title: str,
    summary: str | None,
    sources: list[EvidenceSource],
    metadata: dict[str, Any],
    excerpts: list[EvidenceExcerpt],
) -> list[EvidenceExcerpt]:
    bounded: list[EvidenceExcerpt] = []
    for excerpt in excerpts[:MAX_EVIDENCE_SOURCES]:
        text_limit = min(len(excerpt.text), MAX_EXCERPT_CHARS)
        fitted: EvidenceExcerpt | None = None
        while text_limit >= MIN_EXCERPT_CHARS:
            candidate = EvidenceExcerpt(
                source_name=excerpt.source_name,
                source_url=excerpt.source_url,
                text=_truncate_text(excerpt.text, text_limit),
            )
            length = _pack_length(
                event_id=event_id,
                title=title,
                summary=summary,
                sources=sources,
                metadata=metadata,
                excerpts=[*bounded, candidate],
            )
            if length <= MAX_TOTAL_INPUT_CHARS:
                fitted = candidate
                break
            text_limit -= max(1, length - MAX_TOTAL_INPUT_CHARS)
        if fitted is None:
            break
        bounded.append(fitted)
    return bounded


def _pack_length(
    *,
    event_id: int,
    title: str,
    summary: str | None,
    sources: list[EvidenceSource],
    metadata: dict[str, Any],
    excerpts: list[EvidenceExcerpt],
) -> int:
    payload = {
        "event_id": event_id,
        "title": title,
        "summary": summary,
        "sources": [source.model_dump(mode="json") for source in sources],
        "excerpts": [excerpt.model_dump(mode="json") for excerpt in excerpts],
        "input_quality": "multi_source",
        "metadata": metadata,
    }
    return _json_length(payload)


def _json_length(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":")))


def _truncate_text(value: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(value) <= limit:
        return value
    suffix = "...[truncated]"
    if limit <= len(suffix):
        return value[:limit]
    return value[: max(0, limit - len(suffix))] + suffix


def _fingerprint_text(value: str) -> str:
    return WHITESPACE_PATTERN.sub(" ", value.lower()).strip()[:500]


def _redact_secret_like_text(value: str) -> str:
    text = value
    for pattern in SECRET_VALUE_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text
