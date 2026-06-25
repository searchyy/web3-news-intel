from __future__ import annotations

from collections.abc import Mapping

from app.core.config import SourceConfig


def conditional_request_headers(
    *, etag: str | None = None, last_modified: str | None = None
) -> dict[str, str]:
    headers: dict[str, str] = {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    return headers


def source_request_headers(
    source: SourceConfig,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
) -> dict[str, str]:
    headers: dict[str, str] = {}
    configured = source.config.get("request_headers")
    if isinstance(configured, Mapping):
        headers.update(
            {
                str(key): str(value)
                for key, value in configured.items()
                if value not in (None, "")
            }
        )
    headers.update(conditional_request_headers(etag=etag, last_modified=last_modified))
    return headers


def conditional_response_metadata(headers: Mapping[str, str]) -> dict[str, str]:
    metadata: dict[str, str] = {}
    etag = headers.get("etag")
    if etag:
        metadata["etag"] = etag
    last_modified = headers.get("last-modified")
    if last_modified:
        metadata["last_modified"] = last_modified
    return metadata
