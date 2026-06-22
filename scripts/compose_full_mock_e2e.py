from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import timedelta
from hashlib import sha256
from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy import func, select

from app.adapters.rss import RSSAdapter
from app.core.config import SourceConfig
from app.core.time import utc_now
from app.db.models import (
    AIProviderConfig,
    Delivery,
    Event,
    EventAIInsight,
    FetchRun,
    RawDocument,
    ReportSchedule,
    SavedSearch,
    Source,
)
from app.db.repositories.raw_document_repo import RawDocumentRepository
from app.db.session import SessionLocal
from app.pipeline.dedupe import DedupeService
from app.schemas.raw_document import RawDocumentPayload

API_BASE = os.getenv("COMPOSE_E2E_API_BASE", "http://127.0.0.1:8000").rstrip("/")
FRONTEND_BASE = os.getenv("COMPOSE_E2E_FRONTEND_BASE", "http://frontend:8080").rstrip("/")
MOCK_DEEPSEEK_BASE = os.getenv(
    "COMPOSE_E2E_MOCK_DEEPSEEK_BASE", "http://mock-deepseek:9001"
).rstrip("/")
MOCK_FEISHU_BASE = os.getenv(
    "COMPOSE_E2E_MOCK_FEISHU_BASE", "http://mock-feishu:9002"
).rstrip("/")
ADMIN_USERNAME = os.getenv("COMPOSE_E2E_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("COMPOSE_E2E_ADMIN_PASSWORD", "compose-password")
FEISHU_CHAT_ID = os.getenv("COMPOSE_E2E_FEISHU_CHAT_ID", "oc_compose_mock")


class AcceptanceError(RuntimeError):
    pass


def main() -> None:
    run_id = f"compose-{uuid4().hex[:12]}"
    _reset_mocks()
    _check_public_health()
    fixture = _create_fixture_event(run_id)
    with httpx.Client(base_url=API_BASE, timeout=30.0, follow_redirects=False) as client:
        csrf = _login(client)
        headers = {"x-csrf-token": csrf}
        _load_fixture_sources(client)
        search = _search_btc_event(client, fixture["event_id"])
        saved_search = _create_saved_search(client, headers, run_id)
        _configure_mock_deepseek(client, headers)
        models = _get_mock_models(client)
        ai_task = _queue_ai_summary(client, headers, fixture["event_id"])
        ai_insight = _wait_for_ai_insight(client, ai_task["task_id"], fixture["event_id"])
        destination = _create_feishu_destination(client, headers, run_id)
        _approve_destination(client, headers, destination["id"])
        schedule = _create_report_schedule(
            client,
            headers,
            run_id,
            destination["id"],
            saved_search["id"],
        )
        window = _prepare_report_window(schedule["id"], fixture["event_id"])
        preview = _preview_report(client, headers, schedule["id"])
        _run_report(client, headers, schedule["id"])
        _wait_for_delivery(schedule["id"], expected_feishu_cards=1)
        _prepare_report_window(
            schedule["id"],
            fixture["event_id"],
            window_start=window["window_start"],
            window_end=window["window_end"],
        )
        _run_report(client, headers, schedule["id"])
        _wait_for_duplicate(schedule["id"])
        deliveries_page = _request(client, "GET", "/api/admin/deliveries")

    mock_deepseek = _mock_requests(MOCK_DEEPSEEK_BASE)
    mock_feishu = _mock_requests(MOCK_FEISHU_BASE)
    _assert_mock_payloads_are_sanitized(mock_deepseek, mock_feishu)
    result = _build_result(
        fixture=fixture,
        saved_search_id=saved_search["id"],
        ai_insight_id=ai_insight["id"],
        schedule_id=schedule["id"],
        delivery_items=deliveries_page["items"],
        search_total=search["total"],
        model_count=len(models),
        preview_event_count=preview["event_count"],
        mock_deepseek=mock_deepseek,
        mock_feishu=mock_feishu,
    )
    _assert_result(result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


def _reset_mocks() -> None:
    for base in (MOCK_DEEPSEEK_BASE, MOCK_FEISHU_BASE):
        response = httpx.post(f"{base}/__mock/reset", timeout=10.0)
        response.raise_for_status()


def _check_public_health() -> None:
    checks = {
        "api_health": f"{API_BASE}/health",
        "frontend_health": f"{FRONTEND_BASE}/health",
        "frontend_login": f"{FRONTEND_BASE}/login",
        "mock_deepseek_health": f"{MOCK_DEEPSEEK_BASE}/health",
        "mock_feishu_health": f"{MOCK_FEISHU_BASE}/health",
    }
    for name, url in checks.items():
        response = httpx.get(url, timeout=10.0)
        if response.status_code >= 400:
            raise AcceptanceError(f"{name} failed with HTTP {response.status_code}")
        if name == "frontend_login" and "Web3 News Intel" not in response.text:
            raise AcceptanceError("frontend login page did not render expected app name")


def _login(client: httpx.Client) -> str:
    payload = {"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
    body = _request(client, "POST", "/api/admin/auth/login", json=payload)
    csrf = body.get("csrf_token")
    if not csrf:
        raise AcceptanceError("admin login did not return csrf_token")
    return str(csrf)


def _load_fixture_sources(client: httpx.Client) -> None:
    sources = _request(client, "GET", "/api/admin/sources")
    if not isinstance(sources, list) or not sources:
        raise AcceptanceError("source catalog was not loaded through admin API")


def _create_fixture_event(run_id: str) -> dict[str, int]:
    now = utc_now()
    published = (now - timedelta(minutes=5)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    body = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Compose Mock Exchange Feed</title>
    <item>
      <title>Binance lists BTC compose validation market {run_id}</title>
      <link>https://example.com/compose/{run_id}/btc-listing</link>
      <guid>{run_id}-btc-listing</guid>
      <pubDate>{published}</pubDate>
      <description>BTC listing validation event for Compose mock E2E.</description>
    </item>
  </channel>
</rss>
"""
    body_hash = sha256(body.encode("utf-8")).hexdigest()
    with SessionLocal() as session:
        source = session.scalar(select(Source).where(Source.key == "compose_mock_exchange"))
        if source is None:
            source = Source(
                key="compose_mock_exchange",
                name="Compose Mock Exchange",
                display_name_zh="Compose Mock 交易所",
                source_group="exchange_official",
                source_type="exchange_official",
                adapter="rss",
                url="https://example.com/compose/mock-exchange.xml",
                canonical_url="https://example.com/compose/mock-exchange.xml",
                category="listing",
                language="en",
                official=True,
                trust_score=95,
                poll_seconds=300,
                timeout_seconds=5,
                max_response_bytes=65536,
                max_items_per_fetch=10,
                enabled=True,
                allow_private_networks=False,
                allow_localhost=False,
                parser_version="compose_mock_rss_v1",
                supported_categories=["listing"],
                health_status="healthy",
                live_canary_status="disabled",
                config={"parser_version": "compose_mock_rss_v1"},
            )
            session.add(source)
            session.flush()
        fetch_run = FetchRun(source_id=source.id, status="running", trace_id=run_id)
        session.add(fetch_run)
        session.flush()
        raw = RawDocumentPayload(
            source_key=source.key,
            url=source.url,
            canonical_url=source.canonical_url,
            content_type="application/rss+xml",
            status_code=200,
            body_hash=body_hash,
            body=body,
            metadata={"acceptance_run_id": run_id},
        )
        raw_document = RawDocumentRepository(session).upsert(
            source,
            raw,
            fetch_run_id=fetch_run.id,
        )
        source_config = SourceConfig(
            key=source.key,
            name=source.name,
            display_name_zh=source.display_name_zh,
            source_group=source.source_group,
            source_type=source.source_type,
            adapter="rss",
            url=source.url,
            canonical_url=source.canonical_url,
            category=source.category,
            language=source.language,
            official=source.official,
            trust_score=source.trust_score,
            poll_seconds=source.poll_seconds,
            timeout_seconds=source.timeout_seconds,
            max_response_bytes=source.max_response_bytes,
            max_items_per_fetch=source.max_items_per_fetch,
            enabled=source.enabled,
            allow_private_networks=source.allow_private_networks,
            allow_localhost=source.allow_localhost,
            parser_version=source.parser_version,
            supported_categories=source.supported_categories,
            health_status=source.health_status,
            live_canary_status=source.live_canary_status,
            config=source.config,
        )
        items = asyncio.run(RSSAdapter().parse(source_config, raw))
        if len(items) != 1:
            raise AcceptanceError(f"expected one parsed fixture item, got {len(items)}")
        event = DedupeService(session).upsert_event(
            items[0],
            source=source,
            raw_document=raw_document,
        )
        fetch_run.status = "success"
        fetch_run.item_count = len(items)
        fetch_run.http_status = 200
        fetch_run.finished_at = utc_now()
        session.commit()
        return {
            "source_id": int(source.id),
            "fetch_run_id": int(fetch_run.id),
            "raw_document_id": int(raw_document.id),
            "event_id": int(event.id),
        }


def _search_btc_event(client: httpx.Client, event_id: int) -> dict[str, Any]:
    body = _request(
        client,
        "GET",
        "/api/admin/events",
        params={"q": "BTC", "q_mode": "all", "page": 1, "page_size": 20},
    )
    ids = {item["id"] for item in body["items"]}
    if event_id not in ids:
        raise AcceptanceError("BTC event search did not return fixture event")
    return body


def _create_saved_search(
    client: httpx.Client, headers: dict[str, str], run_id: str
) -> dict[str, Any]:
    payload = {
        "name": f"Compose BTC 筛选 {run_id}",
        "description": "Compose Mock E2E 保存筛选",
        "filters": {
            "q": "BTC",
            "q_mode": "all",
            "source_groups": ["exchange_official"],
            "categories": ["listing"],
            "symbols": ["BTC"],
            "minimum_trust_score": 50,
            "page": 1,
            "page_size": 20,
        },
    }
    return _request(client, "POST", "/api/admin/saved-searches", json=payload, headers=headers)


def _configure_mock_deepseek(client: httpx.Client, headers: dict[str, str]) -> None:
    payload = {
        "enabled": True,
        "api_base": MOCK_DEEPSEEK_BASE,
        "api_key": "compose-mock-api-key",
        "model": "deepseek-compose-mock",
        "timeout_seconds": 30,
        "max_concurrency": 2,
        "max_tokens": 1200,
        "temperature": 0.2,
        "thinking_enabled": False,
        "daily_token_budget": 100000,
        "daily_request_budget": 100,
        "auto_process_enabled": False,
        "auto_minimum_severity": "high",
        "config": {"requests_per_minute": 60},
    }
    body = _request(
        client,
        "PUT",
        "/api/admin/ai/providers/deepseek",
        json=payload,
        headers=headers,
    )
    if not body.get("api_key_configured") or "compose-mock-api-key" in json.dumps(body):
        raise AcceptanceError("DeepSeek config did not mask API key")


def _get_mock_models(client: httpx.Client) -> list[dict[str, Any]]:
    models = _request(client, "GET", "/api/admin/ai/providers/deepseek/models")
    if not models or models[0]["id"] != "deepseek-compose-mock":
        raise AcceptanceError(f"unexpected model list: {models}")
    return models


def _queue_ai_summary(
    client: httpx.Client, headers: dict[str, str], event_id: int
) -> dict[str, Any]:
    body = _request(
        client,
        "POST",
        f"/api/admin/events/{event_id}/ai-summary",
        json={"force": True},
        headers=headers,
    )
    if not body.get("queued") or not body.get("task_id"):
        raise AcceptanceError(f"AI summary was not queued: {body}")
    return body


def _wait_for_ai_insight(
    client: httpx.Client, task_id: str, event_id: int
) -> dict[str, Any]:
    last_task: dict[str, Any] | None = None
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        last_task = _request(client, "GET", f"/api/admin/ai/tasks/{task_id}")
        insight_response = client.get(f"/api/admin/events/{event_id}/ai-insight")
        if insight_response.status_code == 200:
            insight = insight_response.json()
            if insight["status"] == "success" and insight["summary_zh"]:
                return insight
        if last_task["status"] in {"FAILURE", "REVOKED"}:
            raise AcceptanceError(f"AI task failed: {last_task}")
        time.sleep(2)
    raise AcceptanceError(f"timed out waiting for AI insight, last task={last_task}")


def _create_feishu_destination(
    client: httpx.Client, headers: dict[str, str], run_id: str
) -> dict[str, Any]:
    payload = {
        "key": f"compose-feishu-{run_id}",
        "name": f"Compose Mock 飞书群 {run_id}",
        "provider": "feishu_app",
        "chat_id": FEISHU_CHAT_ID,
        "chat_name": "Compose Mock 飞书群",
        "enabled": True,
        "config": {"acceptance_run_id": run_id},
    }
    return _request(client, "POST", "/api/admin/destinations", json=payload, headers=headers)


def _approve_destination(
    client: httpx.Client, headers: dict[str, str], destination_id: str
) -> None:
    body = _request(
        client,
        "POST",
        f"/api/admin/destinations/{destination_id}/approve",
        headers=headers,
    )
    if body["status"] != "active" or not body["enabled"]:
        raise AcceptanceError(f"destination was not activated: {body}")


def _create_report_schedule(
    client: httpx.Client,
    headers: dict[str, str],
    run_id: str,
    destination_id: str,
    saved_search_id: int,
) -> dict[str, Any]:
    payload = {
        "destination_id": destination_id,
        "name": f"Compose 每小时汇报 {run_id}",
        "enabled": True,
        "report_type": "hourly",
        "timezone": "Asia/Taipei",
        "saved_search_id": saved_search_id,
        "source_groups": ["exchange_official"],
        "categories": ["listing"],
        "severities": ["high", "normal", "low"],
        "symbols": ["BTC"],
        "chains": [],
        "minimum_trust_score": 50,
        "include_ai_summary": True,
        "maximum_events": 10,
    }
    return _request(client, "POST", "/api/admin/report-schedules", json=payload, headers=headers)


def _prepare_report_window(
    schedule_id: int,
    event_id: int,
    *,
    window_start=None,
    window_end=None,
) -> dict[str, Any]:
    with SessionLocal() as session:
        event = session.get(Event, event_id)
        schedule = session.get(ReportSchedule, schedule_id)
        if event is None or schedule is None:
            raise AcceptanceError("fixture event or report schedule was not found")
        start = window_start or (event.first_seen_at - timedelta(minutes=1))
        end = window_end or (event.first_seen_at + timedelta(minutes=1))
        schedule.activated_at = start
        schedule.last_window_start = None
        schedule.last_window_end = start
        schedule.next_run_at = end
        session.commit()
        return {"window_start": start, "window_end": end}


def _preview_report(
    client: httpx.Client, headers: dict[str, str], schedule_id: int
) -> dict[str, Any]:
    body = _request(
        client,
        "POST",
        f"/api/admin/report-schedules/{schedule_id}/preview",
        headers=headers,
    )
    if body["event_count"] < 1 or not body["events"]:
        raise AcceptanceError(f"report preview did not include fixture event: {body}")
    return body


def _run_report(client: httpx.Client, headers: dict[str, str], schedule_id: int) -> None:
    body = _request(
        client,
        "POST",
        f"/api/admin/report-schedules/{schedule_id}/run",
        headers=headers,
    )
    if body != {"schedule_id": schedule_id, "queued": True}:
        raise AcceptanceError(f"report run was not queued: {body}")


def _wait_for_delivery(schedule_id: int, *, expected_feishu_cards: int) -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        with SessionLocal() as session:
            schedule = session.get(ReportSchedule, schedule_id)
            delivered = _delivery_count_for_schedule(session, schedule_id)
            last_result = schedule.last_result if schedule else None
        feishu_counts = _mock_counts(MOCK_FEISHU_BASE)
        if (
            delivered == 1
            and last_result == "sent"
            and feishu_counts.get("feishu.send_message", 0) == expected_feishu_cards
        ):
            return
        time.sleep(2)
    raise AcceptanceError("timed out waiting for first Feishu report delivery")


def _wait_for_duplicate(schedule_id: int) -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        with SessionLocal() as session:
            schedule = session.get(ReportSchedule, schedule_id)
            delivered = _delivery_count_for_schedule(session, schedule_id)
            last_result = schedule.last_result if schedule else None
        feishu_counts = _mock_counts(MOCK_FEISHU_BASE)
        if (
            delivered == 1
            and last_result == "duplicate"
            and feishu_counts.get("feishu.send_message", 0) == 1
        ):
            return
        time.sleep(2)
    raise AcceptanceError("timed out waiting for duplicate-window idempotency result")


def _build_result(
    *,
    fixture: dict[str, int],
    saved_search_id: int,
    ai_insight_id: int,
    schedule_id: int,
    delivery_items: list[dict[str, Any]],
    search_total: int,
    model_count: int,
    preview_event_count: int,
    mock_deepseek: list[dict[str, Any]],
    mock_feishu: list[dict[str, Any]],
) -> dict[str, Any]:
    with SessionLocal() as session:
        delivery_count = _delivery_count_for_schedule(session, schedule_id)
        duplicate_delivery_count = max(delivery_count - 1, 0)
        result = {
            "raw_documents": _count_by_id(session, RawDocument, fixture["raw_document_id"]),
            "events": _count_by_id(session, Event, fixture["event_id"]),
            "saved_searches": _count_by_id(session, SavedSearch, saved_search_id),
            "ai_insights": _count_by_id(session, EventAIInsight, ai_insight_id),
            "report_schedules": _count_by_id(session, ReportSchedule, schedule_id),
            "deliveries": delivery_count,
            "duplicate_deliveries": duplicate_delivery_count,
            "search_total": search_total,
            "mock_model_count": model_count,
            "preview_event_count": preview_event_count,
            "mock_deepseek_models": _kind_count(mock_deepseek, "deepseek.models"),
            "mock_deepseek_chat_completions": _kind_count(
                mock_deepseek,
                "deepseek.chat_completions",
            ),
            "mock_feishu_token_requests": _kind_count(mock_feishu, "feishu.tenant_token"),
            "mock_feishu_cards": _kind_count(mock_feishu, "feishu.send_message"),
            "delivery_api_items": len(delivery_items),
            "status": "success",
        }
        config = session.scalar(
            select(AIProviderConfig).where(AIProviderConfig.provider == "deepseek")
        )
        result["deepseek_key_configured"] = bool(config and config.api_key_ciphertext)
        return result


def _assert_result(result: dict[str, Any]) -> None:
    expected = {
        "raw_documents": 1,
        "events": 1,
        "saved_searches": 1,
        "ai_insights": 1,
        "report_schedules": 1,
        "deliveries": 1,
        "duplicate_deliveries": 0,
        "mock_deepseek_chat_completions": 1,
        "mock_feishu_cards": 1,
    }
    mismatches = {
        key: {"expected": value, "actual": result.get(key)}
        for key, value in expected.items()
        if result.get(key) != value
    }
    if mismatches:
        raise AcceptanceError(f"unexpected Compose E2E result: {mismatches}")
    if not result["deepseek_key_configured"]:
        raise AcceptanceError("DeepSeek API key was not persisted as configured ciphertext")


def _assert_mock_payloads_are_sanitized(
    mock_deepseek: list[dict[str, Any]], mock_feishu: list[dict[str, Any]]
) -> None:
    deepseek_payload = json.dumps(mock_deepseek, ensure_ascii=False)
    feishu_payload = json.dumps(mock_feishu, ensure_ascii=False)
    forbidden_for_deepseek = [
        "compose-password",
        "mock-session-not-secret",
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        "mock-not-real",
        "web3_admin_session",
    ]
    for token in forbidden_for_deepseek:
        if token in deepseek_payload:
            raise AcceptanceError(f"mock DeepSeek received forbidden secret marker: {token}")
    if "compose-mock-api-key" in deepseek_payload:
        raise AcceptanceError("mock DeepSeek recorded API key outside authorization header")
    if _kind_count(mock_feishu, "feishu.send_message") != 1:
        raise AcceptanceError("mock Feishu did not receive exactly one card")
    if "compose-mock-api-key" in feishu_payload:
        raise AcceptanceError("mock Feishu received DeepSeek API key")


def _delivery_count_for_schedule(session, schedule_id: int) -> int:
    schedule = session.get(ReportSchedule, schedule_id)
    if schedule is None:
        return 0
    return int(
        session.scalar(
            select(func.count(Delivery.id)).where(
                Delivery.destination_id == schedule.destination_id,
                Delivery.delivery_variant.like("report:%"),
            )
        )
        or 0
    )


def _count_by_id(session, model, item_id: int) -> int:
    return int(session.scalar(select(func.count(model.id)).where(model.id == item_id)) or 0)


def _kind_count(items: list[dict[str, Any]], kind: str) -> int:
    return sum(1 for item in items if item.get("kind") == kind)


def _mock_counts(base: str) -> dict[str, int]:
    response = httpx.get(f"{base}/__mock/counts", timeout=10.0)
    response.raise_for_status()
    return response.json()


def _mock_requests(base: str) -> list[dict[str, Any]]:
    response = httpx.get(f"{base}/__mock/requests", timeout=10.0)
    response.raise_for_status()
    return list(response.json()["requests"])


def _request(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    json: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> Any:
    response = client.request(method, path, headers=headers, json=json, params=params)
    if response.status_code >= 400:
        raise AcceptanceError(f"{method} {path} failed: {response.status_code} {response.text}")
    if not response.content:
        return None
    return response.json()


if __name__ == "__main__":
    main()
