from __future__ import annotations

import json
import os
import statistics
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import insert, text

from app.db.models import Event, EventAIInsight, EventSource, Source
from app.db.repositories.event_search_repo import EventSearchRepository
from app.schemas.event_search import EventSearchParams

pytestmark = pytest.mark.postgres


def test_postgres_event_search_10000_rows_explain_analyze(postgres_session) -> None:
    suffix = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    now = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)
    source_ids = _insert_sources(postgres_session, suffix)
    event_ids = _insert_events(postgres_session, suffix, now)
    _insert_event_sources(postgres_session, suffix, now, source_ids, event_ids)
    _insert_ai_insights(postgres_session, event_ids, now)
    postgres_session.flush()
    postgres_session.execute(text("ANALYZE sources"))
    postgres_session.execute(text("ANALYZE events"))
    postgres_session.execute(text("ANALYZE event_sources"))
    postgres_session.execute(text("ANALYZE event_ai_insights"))

    repo = EventSearchRepository(postgres_session)
    query_cases = [
        (
            "keyword_all_btc_listing",
            EventSearchParams(
                q="BTC listing",
                q_mode="all",
                symbols=["BTC"],
                categories=["listing"],
                minimum_trust_score=80,
                sort="published_at",
                direction="desc",
                page=1,
                page_size=25,
            ),
        ),
        (
            "chinese_phrase",
            EventSearchParams(
                q="以太坊 钱包维护",
                q_mode="phrase",
                languages=["zh"],
                source_groups=["exchange_official"],
                sort="first_seen_at",
                direction="desc",
                page=1,
                page_size=25,
            ),
        ),
        (
            "english_any_official",
            EventSearchParams(
                q="exploit derivatives",
                q_mode="any",
                official_only=True,
                source_groups=["exchange_official"],
                severities=["critical", "high"],
                page=1,
                page_size=25,
            ),
        ),
        (
            "chain_and_date_range",
            EventSearchParams(
                chains=["Ethereum"],
                published_from=now - timedelta(hours=2),
                published_to=now,
                sort="published_at",
                direction="desc",
                page=2,
                page_size=50,
            ),
        ),
        (
            "ai_summary_filter",
            EventSearchParams(
                q="risk",
                q_mode="all",
                has_ai_summary=True,
                sort="trust_score",
                direction="desc",
                page=1,
                page_size=25,
            ),
        ),
    ]

    measurements = []
    for name, params in query_cases:
        warm = repo.search(params)
        assert warm.total > 0, name
        samples = []
        for _ in range(6):
            started = time.perf_counter()
            page = repo.search(params)
            samples.append((time.perf_counter() - started) * 1000)
            assert len(page.items) <= params.page_size
            assert page.total == warm.total
        measurements.append(
            {
                "name": name,
                "total": warm.total,
                "page_size": params.page_size,
                "p50_ms": round(statistics.median(samples), 3),
                "p95_ms": round(_percentile(samples, 95), 3),
                "max_ms": round(max(samples), 3),
                "samples_ms": [round(sample, 3) for sample in samples],
            }
        )

    p95_ms = _percentile([sample for item in measurements for sample in item["samples_ms"]], 95)
    assert p95_ms < 500

    facets = repo.facets(EventSearchParams(q="BTC", q_mode="all"))
    assert _bucket_count(facets.symbols, "BTC") > 0
    assert _bucket_count(facets.chains, "Ethereum") > 0
    assert _bucket_count(facets.source_groups, "exchange_official") > 0

    sql_injection = repo.search(EventSearchParams(q="%' OR 1=1 --", q_mode="phrase"))
    assert sql_injection.total == 0

    explain_plan = _explain_json(postgres_session)
    plan_nodes = list(_walk_plan_nodes(explain_plan))
    index_names = sorted(
        {
            str(node["Index Name"])
            for node in plan_nodes
            if isinstance(node, dict) and node.get("Index Name")
        }
    )
    assert any("Index" in str(node.get("Node Type", "")) for node in plan_nodes)
    assert any(
        name
        in {
            "ix_events_symbols_gin",
            "ix_events_category_first_seen",
            "ix_events_trust_score",
            "ix_events_title_trgm",
        }
        for name in index_names
    )

    report = {
        "dataset": {
            "events": len(event_ids),
            "sources": len(source_ids),
            "event_sources": len(event_ids),
            "ai_insights": len(event_ids) // 5,
        },
        "thresholds": {"common_query_p95_ms": 500},
        "measurements": measurements,
        "overall": {
            "p50_ms": round(
                statistics.median(
                    [sample for item in measurements for sample in item["samples_ms"]]
                ),
                3,
            ),
            "p95_ms": round(p95_ms, 3),
            "max_ms": round(max(item["max_ms"] for item in measurements), 3),
            "passed": p95_ms < 500,
        },
        "explain": {
            "execution_time_ms": explain_plan[0].get("Execution Time"),
            "planning_time_ms": explain_plan[0].get("Planning Time"),
            "uses_index": True,
            "index_names": index_names,
            "top_node": explain_plan[0]["Plan"].get("Node Type"),
            "plan": explain_plan,
        },
    }
    _write_artifacts(report)


def _insert_sources(session, suffix: str) -> list[int]:
    rows = []
    for index in range(20):
        group = "exchange_official" if index % 2 == 0 else "media_zh"
        rows.append(
            {
                "key": f"perf_source_{suffix}_{index}",
                "name": f"性能来源 {index}",
                "display_name_zh": f"性能来源 {index}",
                "source_group": group,
                "source_type": group,
                "adapter": "fixture",
                "url": f"https://example.com/source/{suffix}/{index}",
                "canonical_url": f"https://example.com/source/{suffix}/{index}",
                "category": "exchange" if index % 2 == 0 else "media",
                "language": "zh" if index % 2 else "en",
                "official": index % 2 == 0,
                "trust_score": 95 if index % 2 == 0 else 70,
                "poll_seconds": 120,
                "timeout_seconds": 10,
                "max_response_bytes": 2_097_152,
                "max_items_per_fetch": 50,
                "enabled": True,
                "allow_private_networks": False,
                "allow_localhost": False,
                "parser_version": "perf_v1",
                "supported_categories": ["listing", "security_incident", "market"],
                "health_status": "healthy",
                "live_canary_status": "disabled",
                "config": {},
            }
        )
    return list(session.scalars(insert(Source).returning(Source.id), rows))


def _insert_events(session, suffix: str, now: datetime) -> list[int]:
    rows = []
    for index in range(10_000):
        symbol = "BTC" if index % 10 == 0 else f"T{index % 97}"
        chain = "Ethereum" if index % 2 == 0 else "Solana"
        if index % 3 == 0:
            category = "listing"
        elif index % 11 == 0:
            category = "security_incident"
        else:
            category = "market"
        is_chinese = index % 4 == 0
        title = (
            f"以太坊 钱包维护 {symbol} 第 {index} 行"
            if is_chinese
            else f"{symbol} exchange listing derivatives exploit performance row {index}"
        )
        summary = (
            f"性能测试数据 {index}，用于中文搜索和 {symbol} 筛选"
            if is_chinese
            else f"Search dataset row {index} for {symbol} risk and market filters"
        )
        rows.append(
            {
                "event_key": f"perf:{suffix}:{index}",
                "title": title,
                "summary": summary,
                "category": category,
                "status": "confirmed" if index % 7 else "needs_review",
                "severity": _severity_for_index(index),
                "language": "zh" if is_chinese else "en",
                "primary_url": f"https://example.com/events/{suffix}/{index}",
                "published_at": now - timedelta(seconds=index),
                "first_seen_at": now - timedelta(seconds=index),
                "last_seen_at": now - timedelta(seconds=index),
                "trust_score": 90 if index % 10 == 0 else 60,
                "confirmation_count": 1 + (index % 3),
                "symbols": [symbol],
                "chains": [chain],
                "entities": ["Exchange", chain],
                "metadata_": {"fixture": "postgres_search_perf", "row": index},
            }
        )
    return list(session.scalars(insert(Event).returning(Event.id), rows))


def _insert_event_sources(
    session,
    suffix: str,
    now: datetime,
    source_ids: list[int],
    event_ids: list[int],
) -> None:
    rows = []
    for index, event_id in enumerate(event_ids):
        source_id = source_ids[index % len(source_ids)]
        rows.append(
            {
                "event_id": event_id,
                "source_id": source_id,
                "url": f"https://example.com/events/{suffix}/{index}",
                "title": f"source title {index}",
                "published_at": now - timedelta(seconds=index),
                "source_score": 95 if index % 2 == 0 else 70,
            }
        )
    session.execute(insert(EventSource), rows)


def _insert_ai_insights(session, event_ids: list[int], now: datetime) -> None:
    rows = []
    for index, event_id in enumerate(event_ids):
        if index % 5 != 0:
            continue
        rows.append(
            {
                "event_id": event_id,
                "provider": "deepseek",
                "model": "mock-model",
                "prompt_version": "v1",
                "input_hash": f"perf-hash-{index}",
                "summary_zh": f"AI 风险摘要 {index}，包含 risk 关键字",
                "headline_zh": f"AI 标题 {index}",
                "key_facts": [{"source_event_id": str(event_id), "fact": "fixture"}],
                "entities": [{"name": "Exchange"}],
                "symbols": ["BTC"] if index % 10 == 0 else [],
                "chains": ["Ethereum"] if index % 2 == 0 else ["Solana"],
                "event_type": "listing" if index % 3 == 0 else "market",
                "importance_score": 80,
                "risk_level": "high",
                "sentiment": "neutral",
                "market_impact": "不确定",
                "facts": [{"source_event_id": str(event_id), "fact": "fixture"}],
                "inferences": [{"label": "fixture"}],
                "confidence": 0.8,
                "source_event_ids": [str(event_id)],
                "source_urls": [f"https://example.com/events/perf/{index}"],
                "prompt_tokens": 20,
                "completion_tokens": 30,
                "generated_at": now,
                "status": "success",
            }
        )
    session.execute(insert(EventAIInsight), rows)


def _explain_json(session) -> list[dict[str, Any]]:
    raw = session.execute(
        text(
            """
            EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
            SELECT id
            FROM events
            WHERE symbols && ARRAY['BTC']::text[]
              AND category = 'listing'
              AND trust_score >= 80
              AND lower(coalesce(title, '')) LIKE '%btc%'
            ORDER BY published_at DESC NULLS LAST, id DESC
            LIMIT 25
            """
        )
    ).scalar_one()
    return json.loads(raw) if isinstance(raw, str) else raw


def _severity_for_index(index: int) -> str:
    if index % 17 == 0:
        return "critical"
    if index % 10 == 0:
        return "high"
    return "normal"


def _walk_plan_nodes(plan: Any):
    if isinstance(plan, list):
        for item in plan:
            yield from _walk_plan_nodes(item)
    elif isinstance(plan, dict):
        if "Node Type" in plan:
            yield plan
        for child in plan.get("Plans", []):
            yield from _walk_plan_nodes(child)
        if "Plan" in plan:
            yield from _walk_plan_nodes(plan["Plan"])


def _write_artifacts(report: dict[str, Any]) -> None:
    artifact_dir = Path(os.environ.get("ACCEPTANCE_ARTIFACT_DIR", "artifacts"))
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "search-performance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        "# PostgreSQL 事件搜索性能验收",
        "",
        f"- 数据量：{report['dataset']['events']} events / {report['dataset']['sources']} sources",
        f"- p50：{report['overall']['p50_ms']} ms",
        f"- p95：{report['overall']['p95_ms']} ms",
        f"- 最大耗时：{report['overall']['max_ms']} ms",
        f"- 目标：p95 < {report['thresholds']['common_query_p95_ms']} ms",
        f"- 是否达标：{'是' if report['overall']['passed'] else '否'}",
        f"- EXPLAIN 执行耗时：{report['explain']['execution_time_ms']} ms",
        f"- 使用索引：{'是' if report['explain']['uses_index'] else '否'}",
        f"- 索引：{', '.join(report['explain']['index_names']) or '无'}",
        "",
        "| 查询 | total | p50 ms | p95 ms | max ms |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for item in report["measurements"]:
        lines.append(
            f"| {item['name']} | {item['total']} | {item['p50_ms']} | "
            f"{item['p95_ms']} | {item['max_ms']} |"
        )
    lines.append("")
    (artifact_dir / "search-performance.md").write_text("\n".join(lines), encoding="utf-8")


def _percentile(values: list[float], percentile: int) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    rank = (len(ordered) - 1) * percentile / 100
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _bucket_count(buckets, key: str) -> int:
    for bucket in buckets:
        if bucket.key == key:
            return bucket.count
    return 0
