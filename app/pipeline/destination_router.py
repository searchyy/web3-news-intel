from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Delivery, Event, NotificationDestination, NotificationRule

SEVERITY_RANK = {"low": 1, "normal": 2, "high": 3, "critical": 4}


@dataclass(slots=True)
class RouteDecision:
    should_send: bool
    reason: str
    delivery_mode: str = "immediate"
    rule: NotificationRule | None = None


class DestinationRouter:
    def __init__(self, session: Session):
        self.session = session

    def should_route(
        self,
        event: Event,
        destination: NotificationDestination,
        *,
        now: datetime | None = None,
    ) -> RouteDecision:
        now = now or datetime.now(UTC)
        if not destination.enabled:
            return RouteDecision(False, "destination_disabled")
        if destination.status != "active":
            return RouteDecision(False, "destination_not_active")
        if destination.activated_at is None:
            return RouteDecision(False, "destination_not_activated")
        if event.first_seen_at and event.first_seen_at < destination.activated_at:
            return RouteDecision(False, "historical_event_protected")
        rules = [rule for rule in destination.rules if rule.enabled]
        if not rules:
            return RouteDecision(False, "no_enabled_rule")
        last_decision: RouteDecision | None = None
        for rule in rules:
            decision = self._evaluate_rule(event, destination, rule, now=now)
            if decision.should_send:
                return decision
            last_decision = decision
        return last_decision or RouteDecision(False, "no_rule_matched")

    def _evaluate_rule(
        self,
        event: Event,
        destination: NotificationDestination,
        rule: NotificationRule,
        *,
        now: datetime,
    ) -> RouteDecision:
        if SEVERITY_RANK.get(event.severity, 0) < SEVERITY_RANK.get(rule.minimum_severity, 0):
            return RouteDecision(False, "severity_below_threshold", rule=rule)
        if rule.categories and event.category not in rule.categories:
            return RouteDecision(False, "category_filtered", rule=rule)
        source_keys = [source.source.key for source in event.sources if source.source]
        if rule.sources and not set(rule.sources).intersection(source_keys):
            return RouteDecision(False, "source_filtered", rule=rule)
        if rule.symbols and not set(symbol.upper() for symbol in rule.symbols).intersection(
            symbol.upper() for symbol in event.symbols
        ):
            return RouteDecision(False, "symbol_filtered", rule=rule)
        if rule.chains and not set(chain.upper() for chain in rule.chains).intersection(
            chain.upper() for chain in event.chains
        ):
            return RouteDecision(False, "chain_filtered", rule=rule)
        if self._quiet_hours_active(rule, now) and not (
            event.severity == "critical" and rule.critical_bypass_quiet_hours
        ):
            return RouteDecision(False, "quiet_hours", rule=rule)
        if self._already_delivered(event, destination, rule.delivery_mode):
            return RouteDecision(False, "delivery_idempotency", rule=rule)
        if self._rate_limited(destination, rule.maximum_messages_per_hour, now):
            return RouteDecision(False, "rate_limited", rule=rule)
        return RouteDecision(True, "matched", delivery_mode=rule.delivery_mode, rule=rule)

    def _already_delivered(
        self, event: Event, destination: NotificationDestination, delivery_variant: str
    ) -> bool:
        stmt = select(Delivery.id).where(
            Delivery.destination_id == destination.id,
            Delivery.event_id == event.id,
            Delivery.delivery_variant == delivery_variant,
        )
        return self.session.scalar(stmt) is not None

    def _rate_limited(
        self, destination: NotificationDestination, maximum_messages_per_hour: int, now: datetime
    ) -> bool:
        since = now - timedelta(hours=1)
        count = self.session.scalar(
            select(func.count(Delivery.id)).where(
                Delivery.destination_id == destination.id,
                Delivery.created_at >= since,
                Delivery.status == "delivered",
            )
        )
        return int(count or 0) >= maximum_messages_per_hour

    def _quiet_hours_active(self, rule: NotificationRule, now: datetime) -> bool:
        if not rule.quiet_hours_start or not rule.quiet_hours_end:
            return False
        try:
            local_now = now.astimezone(ZoneInfo(rule.timezone))
        except ZoneInfoNotFoundError:
            return True
        start = _minutes(rule.quiet_hours_start)
        end = _minutes(rule.quiet_hours_end)
        current = local_now.hour * 60 + local_now.minute
        if start <= end:
            return start <= current < end
        return current >= start or current < end


def _minutes(value: str) -> int:
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)
