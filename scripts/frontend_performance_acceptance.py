from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path
from typing import Any

VENDOR_MARKERS = ("react", "antd", "charts", "query", "router", "vendor")
CHART_MARKERS = ("echarts", "zrender")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist-dir", default="frontend/dist")
    parser.add_argument("--json-out", default=None)
    parser.add_argument("--markdown-out", default=None)
    parser.add_argument("--max-business-chunk-gzip-bytes", type=int, default=500 * 1024)
    parser.add_argument("--assert-charts-isolated", action="store_true")
    args = parser.parse_args()

    dist_dir = Path(args.dist_dir)
    summary = _build_summary(dist_dir)
    issues: list[str] = []
    if not summary["chunks"]:
        issues.append(f"{dist_dir}: 未找到 JS chunk，请先运行前端 build")
    for chunk in summary["chunks"]:
        if chunk["kind"] == "business" and chunk["gzip_bytes"] > args.max_business_chunk_gzip_bytes:
            issues.append(
                f"{chunk['file']}: 业务 chunk gzip={chunk['gzip_bytes']} 超过 "
                f"{args.max_business_chunk_gzip_bytes}"
            )
    if args.assert_charts_isolated:
        issues.extend(_check_charts_isolated(dist_dir))
    summary["issues"] = issues
    if args.json_out:
        output = Path(args.json_out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.markdown_out:
        output = Path(args.markdown_out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(_markdown(summary), encoding="utf-8")
    for issue in issues:
        print(f"ERROR: {issue}", file=sys.stderr)
    print(
        "frontend-performance: "
        f"chunks={len(summary['chunks'])} raw={summary['total_raw_bytes']} "
        f"gzip={summary['total_gzip_bytes']}"
    )
    return 1 if issues else 0


def _build_summary(dist_dir: Path) -> dict[str, Any]:
    chunks: list[dict[str, Any]] = []
    for path in sorted((dist_dir / "assets").glob("*.js")):
        content = path.read_bytes()
        gzip_bytes = len(gzip.compress(content, compresslevel=9))
        chunks.append(
            {
                "file": path.relative_to(dist_dir).as_posix(),
                "raw_bytes": len(content),
                "gzip_bytes": gzip_bytes,
                "kind": _chunk_kind(path.name),
            }
        )
    return {
        "dist_dir": str(dist_dir),
        "chunks": chunks,
        "total_raw_bytes": sum(chunk["raw_bytes"] for chunk in chunks),
        "total_gzip_bytes": sum(chunk["gzip_bytes"] for chunk in chunks),
    }


def _chunk_kind(filename: str) -> str:
    lowered = filename.lower()
    if any(marker in lowered for marker in VENDOR_MARKERS):
        return "vendor"
    return "business"


def _check_charts_isolated(dist_dir: Path) -> list[str]:
    issues: list[str] = []
    for path in sorted((dist_dir / "assets").glob("*.js")):
        lowered_name = path.name.lower()
        if "charts" in lowered_name:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        if any(marker in text for marker in CHART_MARKERS):
            chunk_name = path.relative_to(dist_dir).as_posix()
            issues.append(f"{chunk_name}: 非 charts chunk 中发现图表库标记")
    return issues


def _markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Frontend Performance Report",
        "",
        f"- JS raw total: {summary['total_raw_bytes']} bytes",
        f"- JS gzip total: {summary['total_gzip_bytes']} bytes",
        "",
        "| Chunk | Kind | Raw bytes | Gzip bytes |",
        "| --- | --- | ---: | ---: |",
    ]
    for chunk in summary["chunks"]:
        lines.append(
            f"| {chunk['file']} | {chunk['kind']} | {chunk['raw_bytes']} | {chunk['gzip_bytes']} |"
        )
    if summary.get("issues"):
        lines.extend(["", "## Issues", ""])
        lines.extend(f"- {issue}" for issue in summary["issues"])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
