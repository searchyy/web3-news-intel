from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime

import httpx

DEFAULT_SOURCES = ["binance_listing", "okx_listing", "blockbeats_newsflash"]


def run_once(base_url: str, sources: list[str], timeout: float) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout) as client:
        for source in sources:
            started_at = datetime.now(UTC).isoformat()
            try:
                response = client.post(f"/dev/run-source/{source}")
                response.raise_for_status()
                payload = response.json()
                results.append({"source_key": source, "started_at": started_at, **payload})
            except Exception as exc:
                results.append(
                    {
                        "source_key": source,
                        "started_at": started_at,
                        "status": "failed",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:59134")
    parser.add_argument("--interval-seconds", type=int, default=120)
    parser.add_argument("--source", action="append", dest="sources", default=[])
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=120)
    args = parser.parse_args()

    sources = args.sources or DEFAULT_SOURCES
    while True:
        results = run_once(args.base_url, sources, args.timeout_seconds)
        print(json.dumps(results, ensure_ascii=False, sort_keys=True), flush=True)
        if args.once:
            break
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
