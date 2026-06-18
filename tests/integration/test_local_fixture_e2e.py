from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from app.adapters.registry import registry
from app.core.config import SourceConfig
from app.core.errors import AccessDeniedError, ResponseTooLargeError
from app.db.models import Event, Source
from app.db.repositories.raw_document_repo import RawDocumentRepository
from app.fetch.client import FetchClient
from app.fetch.rate_limit import HostRateLimiter
from app.pipeline.alert_rules import AlertEngine
from app.pipeline.dedupe import DedupeService
from app.publishers.base import DeliveryManager, PublisherResult
from app.scheduler.planner import mark_source_queued


class MockPublisher:
    channel = "webhook"
    target = "https://example.com/mock"

    def __init__(self) -> None:
        self.calls = 0

    async def publish(self, event) -> PublisherResult:
        self.calls += 1
        return PublisherResult(ok=True, external_id=str(event.id))


async def test_local_fixture_pipeline_is_idempotent(db_session) -> None:
    with fixture_server() as base_url:
        sources = [
            _source("fixture_rss", "security_alert", "rss", f"{base_url}/rss.xml", "security"),
            _source("fixture_json", "onchain_data", "json_api", f"{base_url}/json", "security"),
            _source(
                "fixture_graphql",
                "governance_api",
                "graphql",
                f"{base_url}/graphql",
                "governance",
                config={"items_path": "data.proposals", "parser_version": "fixture_graphql_v1"},
            ),
            _source(
                "fixture_html",
                "exchange_official",
                "html",
                f"{base_url}/html",
                "listing",
                config={"parser": "fixture", "parser_version": "fixture_html_v1"},
            ),
            _source("fixture_429", "security_alert", "rss", f"{base_url}/429", "security"),
        ]
        for source in sources:
            db_session.add(source)
        db_session.flush()

        for _ in range(2):
            for source in sources:
                mark_source_queued(db_session, source, trace_id=f"trace-{source.key}")
                await _run_source_once(db_session, source)
            db_session.commit()

        stored_events = db_session.query(Event).all()
        assert len(stored_events) == 3

        exploit = next(event for event in stored_events if event.category == "exploit")
        assert exploit.confirmation_count == 3
        assert len(exploit.sources) == 3
        assert AlertEngine().should_alert(exploit).should_alert is True

        publisher = MockPublisher()
        manager = DeliveryManager(db_session)
        await manager.publish_once(exploit, publisher)
        await manager.publish_once(exploit, publisher)
        db_session.commit()
        assert publisher.calls == 1
        assert len(exploit.deliveries) == 1

        async with FetchClient(
            rate_limiter=HostRateLimiter(0),
            allow_private_networks=True,
            allow_localhost=True,
            max_response_bytes=32,
        ) as fetcher:
            try:
                await fetcher.get_text(f"{base_url}/403")
            except AccessDeniedError:
                pass
            else:
                raise AssertionError("403 endpoint did not raise access denied")
            try:
                await fetcher.get_text(f"{base_url}/oversized")
            except ResponseTooLargeError:
                pass
            else:
                raise AssertionError("oversized endpoint did not raise")


async def _run_source_once(db_session, source: Source) -> None:
    config = SourceConfig(
        key=source.key,
        name=source.name,
        source_type=source.source_type,
        adapter=source.adapter,
        url=source.url,
        canonical_url=source.canonical_url,
        category=source.category,
        language=source.language,
        trust_score=source.trust_score,
        poll_seconds=source.poll_seconds,
        timeout_seconds=source.timeout_seconds,
        max_response_bytes=source.max_response_bytes,
        enabled=source.enabled,
        allow_private_networks=source.allow_private_networks,
        allow_localhost=source.allow_localhost,
        config=source.config,
    )
    adapter = registry.get(config.adapter)
    raw_repo = RawDocumentRepository(db_session)
    dedupe = DedupeService(db_session)
    async with FetchClient(
        rate_limiter=HostRateLimiter(0),
        allow_private_networks=True,
        allow_localhost=True,
        max_retries=1,
        backoff_base_seconds=0,
    ) as fetcher:
        raw_documents = await adapter.fetch(config, fetcher)
    for raw in raw_documents:
        raw_document = raw_repo.upsert(source, raw)
        for item in await adapter.parse(config, raw):
            dedupe.upsert_event(item, source=source, raw_document=raw_document)


def _source(
    key: str,
    source_type: str,
    adapter: str,
    url: str,
    category: str,
    *,
    config: dict[str, Any] | None = None,
) -> Source:
    return Source(
        key=key,
        name=key,
        source_type=source_type,
        adapter=adapter,
        url=url,
        canonical_url=url,
        category=category,
        language="en",
        trust_score=90,
        poll_seconds=300,
        timeout_seconds=5,
        max_response_bytes=1024 * 1024,
        enabled=True,
        allow_private_networks=True,
        allow_localhost=True,
        config=config or {"parser_version": "fixture_v1"},
    )


@contextmanager
def fixture_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


class FixtureHandler(BaseHTTPRequestHandler):
    hits: dict[str, int] = {}

    def do_GET(self) -> None:
        self.__class__.hits[self.path] = self.__class__.hits.get(self.path, 0) + 1
        if self.path == "/robots.txt":
            self._send("User-agent: *\nAllow: /", content_type="text/plain")
        elif self.path == "/rss.xml":
            self._send(_rss_body())
        elif self.path == "/json":
            self._send(json.dumps({"hacks": [_event_payload()]}), content_type="application/json")
        elif self.path == "/html":
            self._send(
                "<html><body><a href=\"/listing\">"
                "Exchange Will List Fixture Token (FIX)</a></body></html>",
                content_type="text/html",
            )
        elif self.path == "/429" and self.__class__.hits[self.path] == 1:
            self.send_response(429)
            self.send_header("Retry-After", "0")
            self.end_headers()
        elif self.path == "/429":
            self._send(_rss_body())
        elif self.path == "/403":
            self.send_response(403)
            self.end_headers()
        elif self.path == "/slow":
            time.sleep(0.2)
            self._send("slow")
        elif self.path == "/oversized":
            self._send("x" * 2048)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        if self.path == "/graphql":
            payload = {
                "data": {
                    "proposals": [
                        {
                            "id": "proposal-1",
                            "title": "Fixture DAO Proposal Passed",
                            "body": "Governance proposal passed.",
                            "created": "2026-06-18T06:00:00Z",
                            "link": "https://snapshot.example/proposal-1",
                        }
                    ]
                }
            }
            self._send(json.dumps(payload), content_type="application/json")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *_args) -> None:
        return

    def _send(self, body: str, *, content_type: str = "application/rss+xml") -> None:
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def _event_payload() -> dict[str, str]:
    return {
        "name": "Example Protocol Exploit (EXP)",
        "description": "Exploit fixture.",
        "date": "2026-06-18T05:00:00Z",
        "url": "https://fixture.example/exploit",
    }


def _rss_body() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Fixture RSS</title>
    <item>
      <title>Example Protocol Exploit (EXP)</title>
      <link>https://fixture.example/exploit</link>
      <pubDate>Thu, 18 Jun 2026 05:00:00 GMT</pubDate>
      <description>Exploit fixture.</description>
    </item>
  </channel>
</rss>"""
