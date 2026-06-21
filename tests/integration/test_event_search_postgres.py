from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.db.models import Event
from app.db.repositories.event_search_repo import EventSearchRepository
from app.schemas.event_search import EventSearchParams

pytestmark = pytest.mark.postgres


def test_postgres_event_search_10000_rows_explain_analyze(postgres_session) -> None:
    suffix = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    now = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)
    rows = []
    for index in range(10_000):
        symbol = "BTC" if index % 10 == 0 else f"T{index % 97}"
        category = "listing" if index % 3 == 0 else "market"
        rows.append(
            Event(
                event_key=f"perf:{suffix}:{index}",
                title=f"{symbol} exchange listing performance row {index}",
                summary=f"Search dataset row {index} for {symbol}",
                category=category,
                status="confirmed",
                severity="high" if index % 10 == 0 else "normal",
                language="en",
                published_at=now - timedelta(seconds=index),
                first_seen_at=now - timedelta(seconds=index),
                last_seen_at=now - timedelta(seconds=index),
                trust_score=90 if index % 10 == 0 else 60,
                confirmation_count=1,
                symbols=[symbol],
                chains=["Ethereum"] if index % 2 == 0 else ["Solana"],
                entities=[],
                metadata_={},
            )
        )
    postgres_session.bulk_save_objects(rows)
    postgres_session.flush()

    params = EventSearchParams(
        q="BTC listing",
        q_mode="all",
        symbols=["BTC"],
        categories=["listing"],
        minimum_trust_score=80,
        sort="published_at",
        direction="desc",
        page=1,
        page_size=25,
    )
    page = EventSearchRepository(postgres_session).search(params)
    assert page.total > 0
    assert len(page.items) == 25
    assert page.items[0].published_at >= page.items[-1].published_at

    explain_rows = postgres_session.execute(
        text(
            """
            EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
            SELECT id
            FROM events
            WHERE symbols && ARRAY['BTC']::text[]
              AND category = 'listing'
              AND trust_score >= 80
              AND lower(coalesce(title, '')) LIKE lower('%BTC%')
            ORDER BY published_at DESC NULLS LAST, id DESC
            LIMIT 25
            """
        )
    ).scalars()
    summary = "\n".join(str(row) for row in explain_rows)
    assert "Execution Time" in summary
    print("\nPOSTGRES_EVENT_SEARCH_10000_EXPLAIN\n" + summary)
