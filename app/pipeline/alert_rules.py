from __future__ import annotations

from app.db.models import Event
from app.schemas.alert import AlertDecision

IMMEDIATE_ALERT_CATEGORIES = {
    "listing",
    "delisting",
    "enforcement",
    "exploit",
    "depeg",
    "chain_halt",
    "protocol_upgrade",
    "governance_passed",
}

REVIEW_REQUIRED_CATEGORIES = {"rumor", "price_prediction", "anonymous_social_claim"}


class AlertEngine:
    def should_alert(self, event: Event) -> AlertDecision:
        if event.status == "rejected":
            return AlertDecision(
                should_alert=False,
                requires_review=False,
                reason="event rejected",
                severity=event.severity,
            )
        if event.category in REVIEW_REQUIRED_CATEGORIES:
            return AlertDecision(
                should_alert=False,
                requires_review=True,
                reason="category requires manual review",
                severity=event.severity,
            )
        if event.category in IMMEDIATE_ALERT_CATEGORIES and event.status == "confirmed":
            return AlertDecision(
                should_alert=True,
                requires_review=False,
                reason="confirmed immediate-alert category",
                severity=event.severity,
            )
        if event.severity == "critical" and event.status == "needs_review":
            return AlertDecision(
                should_alert=True,
                requires_review=True,
                reason="critical unconfirmed report; alert must be worded as reported",
                severity=event.severity,
            )
        return AlertDecision(
            should_alert=False,
            requires_review=event.status == "needs_review",
            reason="does not match alert rules",
            severity=event.severity,
        )
