from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from app.adapters.registry import registry
from app.core.config import load_sources
from app.fetch.client import FetchClient
from app.fetch.rate_limit import HostRateLimiter


async def run(path: str, *, max_sources: int | None = None) -> list[dict[str, Any]]:
    sources = load_sources(path).enabled_sources()
    if max_sources is not None:
        sources = sources[:max_sources]
    results: list[dict[str, Any]] = []
    for source in sources:
        adapter = registry.get(source.adapter)
        result: dict[str, Any] = {
            "source_key": source.key,
            "adapter": source.adapter,
            "ok": False,
            "status_code": None,
            "raw_documents": 0,
            "parsed_items": 0,
            "error": None,
        }
        try:
            async with FetchClient(
                timeout_seconds=min(source.timeout_seconds, 15),
                max_response_bytes=min(source.max_response_bytes, 512 * 1024),
                rate_limiter=HostRateLimiter(1),
                max_retries=1,
                allow_private_networks=source.allow_private_networks,
                allow_localhost=source.allow_localhost,
            ) as fetch_client:
                raw_documents = await adapter.fetch(source, fetch_client)
            parsed_count = 0
            for raw in raw_documents:
                result["status_code"] = raw.status_code
                parsed_count += len(await adapter.parse(source, raw))
            result["raw_documents"] = len(raw_documents)
            result["parsed_items"] = parsed_count
            result["ok"] = len(raw_documents) > 0 and parsed_count >= 0
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        results.append(result)
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources-file", default="sources.yaml")
    parser.add_argument("--max-sources", type=int, default=None)
    args = parser.parse_args()
    results = asyncio.run(run(args.sources_file, max_sources=args.max_sources))
    print(json.dumps(results, indent=2, sort_keys=True))
    failed = [result for result in results if not result["ok"]]
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
