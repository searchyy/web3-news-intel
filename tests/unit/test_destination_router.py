from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.db.models import Event, NotificationDestination, NotificationRule
from app.pipeline.destination_router import DestinationRouter


def _event(**overrides) -> Event:
    now = datetime.now(UTC)
    data = {
        "event_key": "event:test",
        "title": "Test",
        "category": "security",
        "status": "confirmed",
        "severity": "high",
        "trust_score": 90,
        "confirmation_count": 1,
        "symbols": ["ETH"],
        "chains": ["Ethereum"],
        "entities": [],
        "metadata_": {},
        "first_seen_at": now,
        "last_seen_at": now,
    }
    data.update(overrides)
    return Event(**data)


def _destination() -> NotificationDestination:
    now = datetime.now(UTC)
    return NotificationDestination(
        key="feishu-app-test",
        name="Feishu test",
        provider="feishu_app",
        enabled=True,
        status="active",
        chat_id="oc_test",
        activated_at=now - timedelta(minutes=1),
        config={},
    )


def _rule(destination: NotificationDestination, **overrides) -> NotificationRule:
    data = {
        "destination": destination,
        "name": "default",
        "enabled": True,
        "minimum_severity": "normal",
        "categories": [],
        "sources": [],
        "symbols": [],
        "chains": [],
        "delivery_mode": "immediate",
        "timezone": "UTC",
        "maximum_messages_per_hour": 30,
    }
    data.update(overrides)
    return NotificationRule(**data)


def test_historical_event_protection(db_session) -> None:
    destination = _destination()
    destination.activated_at = datetime.now(UTC)
    event = _event(first_seen_at=destination.activated_at - timedelta(seconds=1))
    db_session.add_all([destination, event, _rule(destination)])
    db_session.flush()
    decision = DestinationRouter(db_session).should_route(event, destination)
    assert decision.should_send is False
    assert decision.reason == "historical_event_protected"


def test_symbol_and_severity_filtering(db_session) -> None:
    destination = _destination()
    event = _event(severity="normal", symbols=["BTC"])
    db_session.add_all(
        [
            destination,
            event,
            _rule(destination, minimum_severity="high", symbols=["ETH"]),
        ]
    )
    db_session.flush()
    decision = DestinationRouter(db_session).should_route(event, destination)
    assert decision.should_send is False
    assert decision.reason == "severity_below_threshold"


def test_quiet_hours_default_does_not_allow_critical_bypass(db_session) -> None:
    destination = _destination()
    event = _event(severity="critical")
    db_session.add_all(
        [
            destination,
            event,
            _rule(destination, quiet_hours_start="00:00", quiet_hours_end="23:59"),
        ]
    )
    db_session.flush()
    decision = DestinationRouter(db_session).should_route(
        event,
        destination,
        now=datetime(2026, 6, 19, 12, 0, tzinfo=UTC),
    )
    assert decision.should_send is False
    assert decision.reason == "quiet_hours"
