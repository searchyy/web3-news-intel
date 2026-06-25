from __future__ import annotations

import re
from datetime import UTC, datetime, time, timedelta, timezone
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from app.core.config import SourceConfig
from app.core.time import parse_datetime
from app.parsers.media.common import (
    SUMMARY_MAX_CHARS,
    classify_media_category,
    clean_text,
    media_source_group,
    safe_media_raw_metadata,
)
from app.pipeline.entities import extract_chains, extract_symbols
from app.pipeline.normalize import canonicalize_url
from app.schemas.normalized_item import NormalizedItem
from app.schemas.raw_document import RawDocumentPayload


def parse_media_html(source: SourceConfig, raw: RawDocumentPayload) -> list[NormalizedItem]:
    soup = BeautifulSoup(raw.body or "", "lxml")
    config = source.config
    if config.get("parser") == "blockbeats_newsflash_html":
        return _parse_blockbeats_newsflash(source, raw, soup)
    if config.get("parser") == "telegram_channel_html":
        return _parse_telegram_channel(source, raw, soup)
    if config.get("parser") == "backpack_blog_html":
        return _parse_backpack_blog(source, raw, soup)
    if config.get("parser") == "aster_product_releases_html":
        return _parse_aster_product_releases(source, raw, soup)
    if config.get("parser") == "betterstack_status_html":
        return _parse_betterstack_status(source, raw, soup)

    max_items = int(config.get("max_items", config.get("max_items_per_fetch", 20)))
    item_selector = str(config.get("item_selector", "article, a[href]"))
    items: list[NormalizedItem] = []
    for node in soup.select(item_selector)[: max_items * 3]:
        title_node = node.select_one(str(config.get("title_selector", ""))) if config.get(
            "title_selector"
        ) else node
        href_node = node.select_one(str(config.get("url_selector", ""))) if config.get(
            "url_selector"
        ) else node
        if title_node is None or href_node is None:
            continue
        title = clean_text(title_node.get_text(" ", strip=True))
        href = href_node.get("href") if hasattr(href_node, "get") else None
        if not title or not href:
            continue
        url = urljoin(raw.url, str(href))
        if urlsplit(url).scheme.lower() not in {"http", "https"}:
            continue
        summary = _optional_text(node, str(config.get("summary_selector", "")))
        if summary:
            summary = clean_text(
                summary,
                max_chars=int(config.get("summary_max_chars", SUMMARY_MAX_CHARS)),
            )
        author = clean_text(_optional_text(node, str(config.get("author_selector", ""))))
        tag_selector = str(config.get("tag_selector", ""))
        tags = []
        if tag_selector:
            tags = [
                clean
                for candidate in node.select(tag_selector)
                if (clean := clean_text(candidate.get_text(" ", strip=True)))
            ]
        published_text = _optional_text(node, str(config.get("date_selector", "")))
        if not published_text:
            published_text = _regex_text(
                node.get_text(" ", strip=True),
                config.get("date_regex"),
            )
        published = parse_datetime(published_text)
        category, signals = classify_media_category(title, summary, tags, source.category)
        text = f"{title} {summary or ''}"
        items.append(
            NormalizedItem(
                title=title,
                summary=summary,
                url=url,
                canonical_url=canonicalize_url(url),
                published_at=published,
                source_key=source.key,
                source_type=source.source_type,
                category=category,
                language=source.language,
                symbols=extract_symbols(text),
                chains=extract_chains(text),
                raw=safe_media_raw_metadata(
                    source_key=source.key,
                    source_group=media_source_group(config, source.language),
                    parser="media_html",
                    parser_version=str(config.get("parser_version", "media_html_v1")),
                    provider_id=node.get(str(config.get("id_attribute", "data-id"))),
                    author=author,
                    tags=tags,
                    category=category,
                    category_signals=signals,
                    title=title,
                    summary=summary,
                    original_url=url,
                    official_confirmation=bool(source.config.get("official", False)),
                ),
            )
        )
        if len(items) >= max_items:
            break
    return items



def _regex_text(text: str, pattern: object) -> str | None:
    if not pattern:
        return None
    try:
        match = re.search(str(pattern), text)
    except re.error:
        return None
    if match is None:
        return None
    if "date" in match.groupdict():
        return match.group("date")
    if match.groups():
        return match.group(1)
    return match.group(0)

def _optional_text(node: object, selector: str) -> str | None:
    if not selector or not hasattr(node, "select_one"):
        return None
    selected = node.select_one(selector)
    if selected is None:
        return None
    return selected.get_text(" ", strip=True)


_BLOCKBEATS_LOCAL_TZ = timezone(timedelta(hours=8))
_BLOCKBEATS_TIME_RE = re.compile(
    r"\b(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?\b"
)
_BLOCKBEATS_DATE_RE = re.compile(
    r"(?P<year>\d{4})[-/.](?P<month>\d{1,2})[-/.](?P<day>\d{1,2})"
)


_ASTER_RELEASE_RE = re.compile(
    r"Week\s+starting\s+(?P<day>\d{1,2})/(?P<month>\d{1,2})/(?P<year>\d{4})",
    re.IGNORECASE,
)


def _parse_telegram_channel(
    source: SourceConfig,
    raw: RawDocumentPayload,
    soup: BeautifulSoup,
) -> list[NormalizedItem]:
    config = source.config
    max_items = int(config.get("max_items", config.get("max_items_per_fetch", 20)))
    item_selector = str(config.get("item_selector", ".tgme_widget_message"))
    items: list[NormalizedItem] = []
    for node in soup.select(item_selector)[: max_items * 3]:
        if not isinstance(node, Tag):
            continue
        text_node = node.select_one(".tgme_widget_message_text")
        raw_text = text_node.get_text(" ", strip=True) if text_node is not None else ""
        summary = clean_text(raw_text, max_chars=SUMMARY_MAX_CHARS)
        if not summary:
            continue
        time_node = node.select_one("time[datetime]")
        published = parse_datetime(time_node.get("datetime") if time_node else None)
        if published is None:
            continue
        href_node = node.select_one("a.tgme_widget_message_date[href]")
        href = href_node.get("href") if href_node else None
        url = urljoin(raw.url, str(href)) if href else raw.url
        title = clean_text(summary, max_chars=180)
        if not title:
            continue
        category, signals = classify_media_category(title, summary, [], source.category)
        text = f"{title} {summary}"
        items.append(
            NormalizedItem(
                title=title,
                summary=summary,
                url=url,
                canonical_url=canonicalize_url(url),
                published_at=published,
                source_key=source.key,
                source_type=source.source_type,
                category=category,
                language=source.language,
                symbols=extract_symbols(text),
                chains=extract_chains(text),
                raw=safe_media_raw_metadata(
                    source_key=source.key,
                    source_group=media_source_group(config, source.language),
                    parser="telegram_channel_html",
                    parser_version=str(config.get("parser_version", "telegram_channel_html_v1")),
                    provider_id=node.get("data-post") or url,
                    author=None,
                    tags=[],
                    category=category,
                    category_signals=signals,
                    title=title,
                    summary=summary,
                    original_url=url,
                    official_confirmation=bool(source.config.get("official", False)),
                ),
            )
        )
        if len(items) >= max_items:
            break
    return items


def _parse_backpack_blog(
    source: SourceConfig,
    raw: RawDocumentPayload,
    soup: BeautifulSoup,
) -> list[NormalizedItem]:
    config = source.config
    max_items = int(config.get("max_items", config.get("max_items_per_fetch", 20)))
    item_selector = str(config.get("item_selector", ".news-item.w-dyn-item, .w-dyn-item"))
    items: list[NormalizedItem] = []
    seen_urls: set[str] = set()
    for node in soup.select(item_selector)[: max_items * 5]:
        if not isinstance(node, Tag):
            continue
        href_node = node.select_one('a[href*="/blog/"]')
        if href_node is None and node.name == "a" and "/blog/" in str(node.get("href", "")):
            href_node = node
        href = href_node.get("href") if isinstance(href_node, Tag) else None
        if not href:
            continue
        url = urljoin(raw.url, str(href))
        canonical = canonicalize_url(url)
        if canonical in seen_urls:
            continue
        title_node = node.select_one('[fs-cmsfilter-field="title"], .h3-24-extra, h3, h2')
        title_text = (
            title_node.get_text(" ", strip=True)
            if title_node is not None
            else node.get_text(" ", strip=True)
        )
        title = clean_text(title_text, max_chars=220)
        date_node = node.select_one("h5.heading-5, time, [fs-cmsfilter-field='date']")
        published = _parse_backpack_date(date_node)
        if not title or published is None:
            continue
        tags = [
            tag
            for candidate in node.select(".news-cat, [fs-cmsfilter-field='category']")
            if (tag := clean_text(candidate.get_text(" ", strip=True)))
        ]
        summary = clean_text(node.get_text(" ", strip=True), max_chars=SUMMARY_MAX_CHARS)
        category, signals = classify_media_category(title, summary, tags, source.category)
        text = f"{title} {summary or ''}"
        seen_urls.add(canonical)
        items.append(
            NormalizedItem(
                title=title,
                summary=summary,
                url=url,
                canonical_url=canonical,
                published_at=published,
                source_key=source.key,
                source_type=source.source_type,
                category=category,
                language=source.language,
                symbols=extract_symbols(text),
                chains=extract_chains(text),
                raw=safe_media_raw_metadata(
                    source_key=source.key,
                    source_group=media_source_group(config, source.language),
                    parser="backpack_blog_html",
                    parser_version=str(config.get("parser_version", "backpack_blog_html_v1")),
                    provider_id=canonical,
                    author=None,
                    tags=tags,
                    category=category,
                    category_signals=signals,
                    title=title,
                    summary=summary,
                    original_url=url,
                    official_confirmation=bool(source.config.get("official", False)),
                ),
            )
        )
        if len(items) >= max_items:
            break
    return items


def _parse_aster_product_releases(
    source: SourceConfig,
    raw: RawDocumentPayload,
    soup: BeautifulSoup,
) -> list[NormalizedItem]:
    config = source.config
    max_items = int(config.get("max_items", config.get("max_items_per_fetch", 20)))
    text = soup.get_text("\n", strip=True)
    matches = list(_ASTER_RELEASE_RE.finditer(text))
    items: list[NormalizedItem] = []
    for index, match in enumerate(matches[:max_items]):
        published = _release_week_datetime(match)
        if published is None:
            continue
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end() : next_start]
        lines = [line.strip(" -\t") for line in body.splitlines() if line.strip(" -\t")]
        summary = clean_text(" ".join(lines), max_chars=SUMMARY_MAX_CHARS)
        day = int(match.group("day"))
        month = int(match.group("month"))
        year = int(match.group("year"))
        title = f"Aster product releases - Week starting {day:02d}/{month:02d}/{year}"
        category, signals = classify_media_category(title, summary, [], source.category)
        item_url = f"{raw.url}?week_starting={year}-{month:02d}-{day:02d}"
        text_for_entities = f"{title} {summary or ''}"
        items.append(
            NormalizedItem(
                title=title,
                summary=summary,
                url=item_url,
                canonical_url=canonicalize_url(item_url),
                published_at=published,
                source_key=source.key,
                source_type=source.source_type,
                category=category,
                language=source.language,
                symbols=extract_symbols(text_for_entities),
                chains=extract_chains(text_for_entities),
                raw=safe_media_raw_metadata(
                    source_key=source.key,
                    source_group=media_source_group(config, source.language),
                    parser="aster_product_releases_html",
                    parser_version=str(
                        config.get("parser_version", "aster_product_releases_html_v1")
                    ),
                    provider_id=f"{year}-{month:02d}-{day:02d}",
                    author=None,
                    tags=["Product releases"],
                    category=category,
                    category_signals=signals,
                    title=title,
                    summary=summary,
                    original_url=item_url,
                    official_confirmation=bool(source.config.get("official", False)),
                ),
            )
        )
    return items


def _parse_betterstack_status(
    source: SourceConfig,
    raw: RawDocumentPayload,
    soup: BeautifulSoup,
) -> list[NormalizedItem]:
    config = source.config
    headline = clean_text(_optional_text(soup, "h1"))
    if not headline or "all services are online" in headline.lower():
        return []
    summary = clean_text(soup.get_text(" ", strip=True), max_chars=SUMMARY_MAX_CHARS)
    category, signals = classify_media_category(headline, summary, ["status"], source.category)
    return [
        NormalizedItem(
            title=f"{source.name}: {headline}",
            summary=summary,
            url=raw.url,
            canonical_url=canonicalize_url(raw.url),
            published_at=raw.fetched_at,
            source_key=source.key,
            source_type=source.source_type,
            category=category,
            language=source.language,
            symbols=extract_symbols(summary or headline),
            chains=extract_chains(summary or headline),
            raw=safe_media_raw_metadata(
                source_key=source.key,
                source_group=media_source_group(config, source.language),
                parser="betterstack_status_html",
                parser_version=str(config.get("parser_version", "betterstack_status_html_v1")),
                provider_id=headline,
                author=None,
                tags=["status"],
                category=category,
                category_signals=signals,
                title=headline,
                summary=summary,
                original_url=raw.url,
                official_confirmation=bool(source.config.get("official", False)),
            ),
        )
    ]


def _parse_backpack_date(node: Tag | None) -> datetime | None:
    if node is None:
        return None
    value = node.get("datetime") if node.name == "time" else node.get_text(" ", strip=True)
    parsed = parse_datetime(value)
    if parsed is None:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _release_week_datetime(match: re.Match[str]) -> datetime | None:
    try:
        return datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
            tzinfo=UTC,
        )
    except ValueError:
        return None

def _parse_blockbeats_newsflash(
    source: SourceConfig,
    raw: RawDocumentPayload,
    soup: BeautifulSoup,
) -> list[NormalizedItem]:
    config = source.config
    max_items = int(config.get("max_items", config.get("max_items_per_fetch", 20)))
    anchor_selector = str(
        config.get("url_selector") or config.get("item_selector") or "a[href^='/flash/']"
    )
    anchors = soup.select(anchor_selector)
    items: list[NormalizedItem] = []
    for anchor in anchors[: max_items * 3]:
        if not isinstance(anchor, Tag):
            continue
        href = anchor.get("href")
        if not href:
            continue
        wrapper = _closest_with_class(anchor, "news-flash-wrapper") or anchor
        title = _blockbeats_title(anchor)
        if not title:
            continue
        published = _blockbeats_published_at(anchor, raw)
        if published is None:
            continue
        url = urljoin(raw.url, str(href))
        summary = _blockbeats_summary(wrapper)
        category, signals = classify_media_category(title, summary, [], source.category)
        text = f"{title} {summary or ''}"
        items.append(
            NormalizedItem(
                title=title,
                summary=summary,
                url=url,
                canonical_url=canonicalize_url(url),
                published_at=published,
                source_key=source.key,
                source_type=source.source_type,
                category=category,
                language=source.language,
                symbols=extract_symbols(text),
                chains=extract_chains(text),
                raw=safe_media_raw_metadata(
                    source_key=source.key,
                    source_group=media_source_group(config, source.language),
                    parser="blockbeats_newsflash_html",
                    parser_version=str(
                        config.get("parser_version", "blockbeats_newsflash_html_v1")
                    ),
                    provider_id=_blockbeats_provider_id(str(href)),
                    author=None,
                    tags=[],
                    category=category,
                    category_signals=signals,
                    title=title,
                    summary=summary,
                    original_url=url,
                    official_confirmation=bool(source.config.get("official", False)),
                ),
            )
        )
        if len(items) >= max_items:
            break
    return items


def _blockbeats_title(anchor: Tag) -> str:
    title_attr = anchor.get("title")
    if isinstance(title_attr, str) and title_attr.strip():
        return clean_text(title_attr)
    title_node = anchor.select_one(".news-flash-title-text")
    if title_node is not None:
        return clean_text(title_node.get_text(" ", strip=True))
    text = clean_text(anchor.get_text(" ", strip=True))
    return _BLOCKBEATS_TIME_RE.sub("", text, count=1).strip()


def _blockbeats_summary(wrapper: Tag) -> str | None:
    content = wrapper.select_one(".news-flash-item-content")
    if content is None:
        return None
    content = BeautifulSoup(str(content), "lxml")
    for link in content.select("a"):
        link.decompose()
    summary = clean_text(content.get_text(" ", strip=True), max_chars=SUMMARY_MAX_CHARS)
    return summary or None


def _blockbeats_published_at(anchor: Tag, raw: RawDocumentPayload) -> datetime | None:
    date_value = _blockbeats_date(anchor)
    if date_value is None and raw.fetched_at is not None:
        date_value = raw.fetched_at.astimezone(_BLOCKBEATS_LOCAL_TZ).date()
    time_value = _blockbeats_time(anchor)
    if date_value is None or time_value is None:
        return None
    local_dt = datetime.combine(date_value, time_value, tzinfo=_BLOCKBEATS_LOCAL_TZ)
    return local_dt.astimezone(UTC)


def _blockbeats_date(anchor: Tag):
    flash_list = _closest_with_class(anchor, "flash-list")
    date_text = _optional_text(flash_list, ".flash-list-today") if flash_list is not None else None
    if not date_text:
        return None
    match = _BLOCKBEATS_DATE_RE.search(date_text)
    if not match:
        return None
    try:
        return datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
            tzinfo=_BLOCKBEATS_LOCAL_TZ,
        ).date()
    except ValueError:
        return None


def _blockbeats_time(anchor: Tag) -> time | None:
    match = _BLOCKBEATS_TIME_RE.search(anchor.get_text(" ", strip=True))
    if not match:
        return None
    try:
        return time(
            int(match.group("hour")),
            int(match.group("minute")),
            int(match.group("second") or 0),
        )
    except ValueError:
        return None


def _closest_with_class(node: Tag, class_name: str) -> Tag | None:
    current = node
    while isinstance(current, Tag):
        classes = current.get("class") or []
        if class_name in classes:
            return current
        parent = current.parent
        if not isinstance(parent, Tag):
            return None
        current = parent
    return None


def _blockbeats_provider_id(href: str) -> str | None:
    match = re.search(r"/flash/([^/?#]+)", href)
    return match.group(1) if match else None
