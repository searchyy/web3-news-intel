from __future__ import annotations

from collections.abc import Mapping


def conditional_request_headers(
    *, etag: str | None = None, last_modified: str | None = None
) -> dict[str, str]:
    headers: dict[str, str] = {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
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
