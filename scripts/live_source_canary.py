from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.adapters.registry import registry  # noqa: E402
from app.core.config import SourceConfig, load_sources  # noqa: E402
from app.core.errors import AccessDeniedError, FetchError, RobotsDisallowedError  # noqa: E402
from app.fetch.client import FetchClient  # noqa: E402
from app.fetch.rate_limit import HostRateLimiter  # noqa: E402
from app.schemas.normalized_item import NormalizedItem  # noqa: E402

CANARY_STATUSES = {
    "PASS",
    "DEGRADED",
    "ACCESS_DENIED",
    "EMPTY",
    "PARSER_BROKEN",
    "NETWORK_FAILED",
    "DISABLED",
}
SECRET_NAME_RE = re.compile(r"(token|secret|password|api[_-]?key|private[_-]?key)", re.I)
URL_QUERY_RE = re.compile(r"([?&](?:token|key|secret|signature|password)=)[^&\\s]+", re.I)


async def run(
    path: str,
    *,
    catalog_dir: str | None = None,
    max_sources: int | None = None,
    include_disabled: bool = False,
    max_items_per_source: int = 10,
) -> list[dict[str, Any]]:
    sources_file = load_sources(path)
    sources = (
        list(sources_file.sources.values())
        if include_disabled
        else sources_file.enabled_sources()
    )
    if catalog_dir:
        catalog_sources = _load_catalog_sources(Path(catalog_dir))
        if include_disabled:
            sources.extend(catalog_sources)
        else:
            sources.extend(source for source in catalog_sources if source.enabled)
    if max_sources is not None:
        sources = sources[:max_sources]
    results: list[dict[str, Any]] = []
    for source in sources:
        result: dict[str, Any] = {
            "source_key": source.key,
            "adapter": source.adapter,
            "http_status": None,
            "content_type": None,
            "response_bytes": 0,
            "body_sha256": None,
            "raw_documents": 0,
            "parsed_item_count": 0,
            "newest_published_at": None,
            "sample_title": None,
            "original_url": _redact_source_url(source.url),
            "result": "DISABLED" if not source.enabled else "NETWORK_FAILED",
            "error_reason": None,
        }
        if not source.enabled:
            results.append(result)
            continue
        try:
            adapter = registry.get(source.adapter)
            async with FetchClient(
                timeout_seconds=min(source.timeout_seconds, 15),
                max_response_bytes=min(source.max_response_bytes, 512 * 1024),
                rate_limiter=HostRateLimiter(1),
                max_retries=1,
                allow_private_networks=source.allow_private_networks,
                allow_localhost=source.allow_localhost,
                trust_env=False,
            ) as fetch_client:
                raw_documents = await adapter.fetch(source, fetch_client)
            parsed_items: list[NormalizedItem] = []
            for raw in raw_documents:
                result["http_status"] = raw.status_code
                result["content_type"] = raw.content_type
                result["response_bytes"] += len((raw.body or "").encode("utf-8"))
                if raw.body:
                    result["body_sha256"] = hashlib.sha256(raw.body.encode("utf-8")).hexdigest()
                parsed_items.extend(await adapter.parse(source, raw))
                if len(parsed_items) >= max_items_per_source:
                    parsed_items = parsed_items[:max_items_per_source]
                    break
            result["raw_documents"] = len(raw_documents)
            result["parsed_item_count"] = len(parsed_items)
            _attach_sample(result, parsed_items)
            result["result"] = _success_status(raw_documents, parsed_items)
        except Exception as exc:
            result["result"] = _classify_exception(exc)
            result["error_reason"] = _sanitize_error(exc)
        results.append(result)
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources-file", default="sources.yaml")
    parser.add_argument("--catalog-dir", default=None)
    parser.add_argument("--max-sources", type=int, default=None)
    parser.add_argument("--include-disabled", action="store_true")
    parser.add_argument("--max-items-per-source", type=int, default=10)
    parser.add_argument("--markdown-out", default=None)
    parser.add_argument(
        "--fail-on-fatal",
        action="store_true",
        help="仅本地强验收使用；外部 workflow 默认不因单个来源失败而失败。",
    )
    args = parser.parse_args()
    results = asyncio.run(
        run(
            args.sources_file,
            catalog_dir=args.catalog_dir,
            max_sources=args.max_sources,
            include_disabled=args.include_disabled,
            max_items_per_source=max(1, min(args.max_items_per_source, 10)),
        )
    )
    print(json.dumps(results, indent=2, sort_keys=True))
    if args.markdown_out:
        _write_markdown(args.markdown_out, results)
    fatal = [
        result
        for result in results
        if result["result"] not in {"PASS", "DEGRADED", "DISABLED"}
    ]
    if args.fail_on_fatal and fatal:
        raise SystemExit(1)


def _success_status(
    raw_documents: list[Any],
    parsed_items: list[NormalizedItem],
) -> str:
    if not raw_documents:
        return "EMPTY"
    if not parsed_items:
        return "DEGRADED"
    return "PASS"


def _load_catalog_sources(catalog_dir: Path) -> list[SourceConfig]:
    if not catalog_dir.exists():
        return []
    loaded: list[SourceConfig] = []
    for path in sorted(catalog_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        raw_sources = data.get("sources")
        if not isinstance(raw_sources, dict):
            continue
        for key, raw in raw_sources.items():
            if not isinstance(raw, dict):
                continue
            loaded.append(_catalog_source_config(str(key), raw))
    return loaded


def _catalog_source_config(key: str, raw: dict[str, Any]) -> SourceConfig:
    config = dict(raw.get("config") or {})
    parser = raw.get("parser")
    if parser and not config.get("parser"):
        config["parser"] = parser
    parser_version = raw.get("parser_version")
    if parser_version and not config.get("parser_version"):
        config["parser_version"] = parser_version
    if raw.get("max_items_per_fetch") and not config.get("max_items"):
        config["max_items"] = raw["max_items_per_fetch"]

    payload: dict[str, Any] = {
        "key": key,
        "name": raw["name"],
        "display_name_zh": raw.get("display_name_zh"),
        "source_group": raw.get("source_group", "legacy"),
        "source_type": raw["source_type"],
        "adapter": raw["adapter"],
        "url": raw["url"],
        "canonical_url": raw["canonical_url"],
        "category": raw["category"],
        "language": raw.get("language"),
        "official": bool(raw.get("official", False)),
        "trust_score": raw.get("trust_score", 50),
        "poll_seconds": raw.get("poll_seconds", 300),
        "timeout_seconds": raw.get("timeout_seconds", 15),
        "max_response_bytes": raw.get(
            "max_response_bytes",
            raw.get("maximum_response_bytes", 2 * 1024 * 1024),
        ),
        "max_items_per_fetch": raw.get("max_items_per_fetch", 50),
        "enabled": bool(raw.get("enabled", False)),
        "allow_private_networks": bool(raw.get("allow_private_networks", False)),
        "allow_localhost": bool(raw.get("allow_localhost", False)),
        "ranking_provider": raw.get("ranking_provider"),
        "ranking_position": raw.get("ranking_position"),
        "ranking_snapshot_at": raw.get("ranking_snapshot_at"),
        "parser_version": raw.get("parser_version", "v1"),
        "supported_categories": raw.get("supported_categories") or [],
        "health_status": raw.get("health_status", "unknown"),
        "live_canary_status": raw.get("live_canary_status", "unknown"),
        "last_canary_at": raw.get("last_canary_at"),
        "last_canary_error": raw.get("last_canary_error"),
        "config": config,
    }
    return SourceConfig.model_validate(payload)


def _attach_sample(result: dict[str, Any], items: list[NormalizedItem]) -> None:
    if not items:
        return
    sorted_items = sorted(
        items,
        key=lambda item: item.published_at.isoformat() if item.published_at else "",
        reverse=True,
    )
    newest = sorted_items[0]
    result["newest_published_at"] = (
        newest.published_at.isoformat() if newest.published_at else None
    )
    result["sample_title"] = _truncate(newest.title, limit=160)
    result["original_url"] = _redact_source_url(newest.url)


def _classify_exception(exc: Exception) -> str:
    if isinstance(exc, (AccessDeniedError, RobotsDisallowedError)):
        return "ACCESS_DENIED"
    if isinstance(exc, FetchError):
        if exc.error_code in {"transport_error", "response_too_large"}:
            return "NETWORK_FAILED"
        if exc.status_code in {401, 403, 429}:
            return "ACCESS_DENIED"
        return "DEGRADED"
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError, TimeoutError)):
        return "NETWORK_FAILED"
    if isinstance(exc, (KeyError, ValueError, TypeError, json.JSONDecodeError)):
        return "PARSER_BROKEN"
    return "DEGRADED"


def _sanitize_error(exc: Exception) -> str:
    message = f"{type(exc).__name__}: {exc}"
    message = URL_QUERY_RE.sub(lambda match: f"{match.group(1)}<redacted>", message)
    for name, value in os.environ.items():
        if value and len(value) >= 4 and SECRET_NAME_RE.search(name):
            message = message.replace(value, "<redacted>")
    return _truncate(message.replace("\n", " "), limit=240) or ""


def _redact_source_url(url: str) -> str:
    return URL_QUERY_RE.sub(lambda match: f"{match.group(1)}<redacted>", url)


def _truncate(value: str | None, *, limit: int) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 1]}..."


def _write_markdown(path: str, results: list[dict[str, Any]]) -> None:
    lines = [
        "# Live Source Canary",
        "",
        "该报告只包含脱敏元数据，不包含响应正文、Cookie、Header 或密钥。",
        "",
        "| Source | Adapter | Status | HTTP | Bytes | Parsed | Newest | Sample | Error |",
        "| --- | --- | --- | --- | ---: | ---: | --- | --- | --- |",
    ]
    for item in results:
        lines.append(
            "| {source_key} | {adapter} | {result} | {http_status} | {response_bytes} | "
            "{parsed_item_count} | {newest_published_at} | {sample_title} | "
            "{error_reason} |".format(
                source_key=_escape_markdown(item.get("source_key")),
                adapter=_escape_markdown(item.get("adapter")),
                result=_escape_markdown(item.get("result")),
                http_status=_escape_markdown(item.get("http_status")),
                response_bytes=item.get("response_bytes") or 0,
                parsed_item_count=item.get("parsed_item_count") or 0,
                newest_published_at=_escape_markdown(item.get("newest_published_at")),
                sample_title=_escape_markdown(item.get("sample_title")),
                error_reason=_escape_markdown(item.get("error_reason")),
            )
        )
    output = "\n".join(lines) + "\n"
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(output)


def _escape_markdown(value: object) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("live canary interrupted", file=sys.stderr)
        raise SystemExit(130) from None
