from __future__ import annotations

import html
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.core.time import ensure_utc
from app.pipeline.category import detect_category
from app.pipeline.entities import extract_chains, extract_entities, extract_symbols
from app.pipeline.language import detect_language
from app.schemas.normalized_item import NormalizedItem

TRACKING_PARAMS_PREFIXES = ("utm_",)
TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_src",
    "spm",
}


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower() or "https"
    netloc = parts.netloc.lower()
    if (scheme == "https" and netloc.endswith(":443")) or (
        scheme == "http" and netloc.endswith(":80")
    ):
        netloc = netloc.rsplit(":", 1)[0]
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=False)
        if key not in TRACKING_PARAMS and not key.startswith(TRACKING_PARAMS_PREFIXES)
    ]
    query = urlencode(sorted(query_pairs), doseq=True)
    return urlunsplit((scheme, netloc, path.rstrip("/") or "/", query, ""))


def clean_title(title: str) -> str:
    value = html.unescape(title)
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(
        r"\s+[-|]\s+(CoinDesk|Binance|OKX|The Block|BlockBeats)\s*$", "", value, flags=re.I
    )
    return value


def title_fingerprint(title: str) -> str:
    cleaned = clean_title(title).lower()
    cleaned = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", cleaned)
    return " ".join(cleaned.split())


def normalize_item(item: NormalizedItem) -> NormalizedItem:
    title = clean_title(item.title)
    text = f"{title} {item.summary or ''}"
    symbols = sorted(set(item.symbols) | set(extract_symbols(text)))
    chains = sorted(set(item.chains) | set(extract_chains(text)))
    entities = sorted(set(item.entities) | set(extract_entities(text)))
    category = detect_category(title, item.summary, item.category)
    language = item.language or detect_language(text)
    return item.model_copy(
        update={
            "title": title,
            "canonical_url": canonicalize_url(item.canonical_url or item.url),
            "published_at": ensure_utc(item.published_at),
            "symbols": symbols,
            "chains": chains,
            "entities": entities,
            "category": category,
            "language": language,
        }
    )
