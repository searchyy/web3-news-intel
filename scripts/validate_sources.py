from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import SourceConfig, load_sources  # noqa: E402

CANARY_STATUSES = {
    "PASS",
    "DEGRADED",
    "ACCESS_DENIED",
    "EMPTY",
    "PARSER_BROKEN",
    "NETWORK_FAILED",
    "DISABLED",
    "UNSUPPORTED",
    "NOT_RUN",
    "NOT_EXECUTED",
    "unknown",
}
CATALOG_ADAPTERS = {
    "rss",
    "json_api",
    "graphql",
    "html",
    "exchange_rss",
    "exchange_json",
    "exchange_html",
    "media_rss",
    "media_html",
    "media_json_api",
    "okx_help_app_state",
}
SOURCE_GROUPS = {
    "exchange_official",
    "media_zh",
    "media_en",
    "legacy",
    "regulator_official",
    "protocol_official",
    "onchain_data",
    "project_official",
    "project_news",
}
CATALOG_REQUIRED_FIELDS = {
    "name",
    "display_name_zh",
    "source_group",
    "source_type",
    "adapter",
    "parser",
    "official",
    "language",
    "category",
    "trust_score",
    "poll_seconds",
    "timeout_seconds",
    "maximum_response_bytes",
    "max_items_per_fetch",
    "enabled",
    "url",
    "canonical_url",
    "parser_version",
    "supported_categories",
    "live_canary_status",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", default="sources.yaml")
    parser.add_argument(
        "--strict-contract",
        action="store_true",
        help="校验统一 Source Catalog 所需字段、安全默认值和 canary 状态。",
    )
    parser.add_argument(
        "--catalog-dir",
        default=None,
        help="可选，校验 source_catalog 目录下的 YAML catalog。",
    )
    parser.add_argument("--json-out", default=None, help="可选，将校验摘要写入 JSON 文件。")
    args = parser.parse_args()
    summary: dict[str, Any] = {
        "source_file": args.path,
        "loaded": 0,
        "enabled": 0,
        "catalog_files": [],
        "issues": [],
        "warnings": [],
    }

    try:
        sources_file = load_sources(args.path)
    except Exception as exc:
        summary["issues"].append(f"{args.path}: 无法加载 sources 文件: {type(exc).__name__}: {exc}")
        _finish(summary, args.json_out)
        raise SystemExit(1) from None

    summary["loaded"] = len(sources_file.sources)
    summary["enabled"] = len(sources_file.enabled_sources())

    if args.strict_contract:
        _validate_runtime_sources(sources_file.sources, summary)

    if args.catalog_dir:
        _validate_catalog_dir(Path(args.catalog_dir), summary)

    _finish(summary, args.json_out)
    if summary["issues"]:
        raise SystemExit(1)


def _validate_runtime_sources(
    sources: dict[str, SourceConfig],
    summary: dict[str, Any],
) -> None:
    for key, source in sources.items():
        _require_https(key, source.url, summary)
        _require_https(key, source.canonical_url, summary)
        if source.enabled and source.allow_private_networks:
            summary["issues"].append(f"{key}: enabled source 禁止 allow_private_networks=true")
        if source.enabled and source.allow_localhost:
            summary["issues"].append(f"{key}: enabled source 禁止 allow_localhost=true")
        if source.max_response_bytes > 10 * 1024 * 1024:
            summary["issues"].append(f"{key}: maximum_response_bytes 超过 10MiB")
        if source.adapter == "html" and not source.config.get("parser"):
            summary["issues"].append(f"{key}: HTML source 必须声明受控 parser")
        if source.source_group == "legacy":
            summary["warnings"].append(f"{key}: 尚未迁移到统一 source_group")
        if source.config.get("browser") or source.config.get("stealth"):
            summary["issues"].append(f"{key}: source config 禁止 browser/stealth 抓取模式")
        if source.config.get("trust_env"):
            summary["issues"].append(f"{key}: source config 禁止 trust_env/proxy 出口")


def _validate_catalog_dir(catalog_dir: Path, summary: dict[str, Any]) -> None:
    if not catalog_dir.exists():
        summary["warnings"].append(f"{catalog_dir}: 目录不存在，跳过 catalog 校验")
        return
    for path in sorted(catalog_dir.glob("*.yaml")):
        _validate_catalog_file(path, summary)


def _validate_catalog_file(path: Path, summary: dict[str, Any]) -> None:
    record: dict[str, Any] = {"path": str(path), "sources": 0, "enabled": 0}
    summary["catalog_files"].append(record)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        summary["issues"].append(f"{path}: YAML 无法解析: {type(exc).__name__}: {exc}")
        return
    sources = data.get("sources")
    if not isinstance(sources, dict):
        summary["issues"].append(f"{path}: 缺少 sources mapping")
        return
    record["sources"] = len(sources)
    record["enabled"] = sum(
        1 for item in sources.values() if isinstance(item, dict) and item.get("enabled")
    )
    for key, raw in sources.items():
        if not isinstance(raw, dict):
            summary["issues"].append(f"{path}:{key}: source 必须是 mapping")
            continue
        _validate_catalog_source(path, str(key), raw, summary)


def _validate_catalog_source(
    path: Path,
    key: str,
    raw: dict[str, Any],
    summary: dict[str, Any],
) -> None:
    missing = sorted(field for field in CATALOG_REQUIRED_FIELDS if field not in raw)
    if missing:
        summary["issues"].append(f"{path}:{key}: 缺少字段 {missing}")
    if raw.get("key") not in {None, key}:
        summary["issues"].append(f"{path}:{key}: key 字段与 mapping key 不一致")
    source_group = raw.get("source_group")
    if source_group not in SOURCE_GROUPS:
        summary["issues"].append(f"{path}:{key}: 非受控 source_group={source_group!r}")
    adapter = raw.get("adapter")
    if adapter not in CATALOG_ADAPTERS:
        summary["issues"].append(f"{path}:{key}: 非受控 adapter={adapter!r}")
    if raw.get("live_canary_status") not in CANARY_STATUSES:
        summary["issues"].append(
            f"{path}:{key}: 非受控 live_canary_status={raw.get('live_canary_status')!r}"
        )
    _require_https(f"{path}:{key}", str(raw.get("url", "")), summary)
    _require_https(f"{path}:{key}", str(raw.get("canonical_url", "")), summary)
    _validate_number(path, key, raw, "trust_score", 0, 100, summary)
    _validate_number(path, key, raw, "poll_seconds", 30, 86400, summary)
    _validate_number(path, key, raw, "timeout_seconds", 1, 60, summary)
    _validate_number(path, key, raw, "maximum_response_bytes", 1024, 10 * 1024 * 1024, summary)
    _validate_number(path, key, raw, "max_items_per_fetch", 1, 1000, summary)
    if raw.get("enabled") is False and raw.get("live_canary_status") == "DISABLED":
        if not raw.get("last_canary_error") and not raw.get("known_limitations"):
            summary["issues"].append(f"{path}:{key}: disabled source 必须说明失败原因或限制")
    if source_group == "exchange_official":
        if raw.get("official") is not True:
            summary["issues"].append(f"{path}:{key}: exchange_official 必须 official=true")
        if raw.get("ranking_position") is not None:
            if not raw.get("ranking_provider") or not raw.get("ranking_snapshot_at"):
                summary["issues"].append(f"{path}:{key}: 排名源必须包含 provider 和 snapshot_at")
    if source_group in {"media_zh", "media_en"}:
        if raw.get("official") is not False:
            summary["issues"].append(f"{path}:{key}: media source 必须 official=false")
        trust_score = raw.get("trust_score")
        if isinstance(trust_score, int | float) and trust_score >= 90:
            summary["issues"].append(f"{path}:{key}: media trust_score 必须低于官方源")
    config = raw.get("config")
    if isinstance(config, dict) and (config.get("browser") or config.get("stealth")):
        summary["issues"].append(f"{path}:{key}: catalog 禁止 browser/stealth 抓取模式")
    if isinstance(config, dict) and config.get("trust_env"):
        summary["issues"].append(f"{path}:{key}: catalog 禁止 trust_env/proxy 出口")


def _validate_number(
    path: Path,
    key: str,
    raw: dict[str, Any],
    field: str,
    minimum: int | float,
    maximum: int | float,
    summary: dict[str, Any],
) -> None:
    value = raw.get(field)
    if not isinstance(value, int | float):
        summary["issues"].append(f"{path}:{key}: {field} 必须是数字")
        return
    if value < minimum or value > maximum:
        summary["issues"].append(f"{path}:{key}: {field}={value} 超出范围 {minimum}..{maximum}")


def _require_https(key: str, url: str, summary: dict[str, Any]) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        summary["issues"].append(f"{key}: URL 必须使用 HTTPS: {url!r}")
    if parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        summary["issues"].append(f"{key}: URL 禁止 localhost: {url!r}")


def _finish(summary: dict[str, Any], json_out: str | None) -> None:
    if json_out:
        output_path = Path(json_out)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"loaded={summary['loaded']} enabled={summary['enabled']}")
    if summary["catalog_files"]:
        for item in summary["catalog_files"]:
            print(
                f"catalog={item['path']} sources={item['sources']} enabled={item['enabled']}"
            )
    for warning in summary["warnings"]:
        print(f"WARNING: {warning}", file=sys.stderr)
    for issue in summary["issues"]:
        print(f"ERROR: {issue}", file=sys.stderr)


if __name__ == "__main__":
    main()
