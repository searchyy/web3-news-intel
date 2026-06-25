from __future__ import annotations

from app.db.models import Event
from app.pipeline.scoring import event_priority_score
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
    "security",
    "security_incident",
    "hack_security",
    "deposit_withdrawal",
    "wallet_maintenance",
    "system_maintenance",
}
REVIEW_REQUIRED_CATEGORIES = {"rumor", "price_prediction", "anonymous_social_claim"}
IMMEDIATE_PRIORITY_THRESHOLD = 85


class AlertEngine:
    def should_alert(self, event: Event) -> AlertDecision:
        priority = event_priority_score(event)
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
        if event.severity == "critical" and event.status in {"confirmed", "needs_review"}:
            return AlertDecision(
                should_alert=True,
                requires_review=event.status == "needs_review",
                reason="critical priority alert",
                severity=event.severity,
            )
        if event.category in IMMEDIATE_ALERT_CATEGORIES and event.status == "confirmed":
            if priority >= IMMEDIATE_PRIORITY_THRESHOLD:
                return AlertDecision(
                    should_alert=True,
                    requires_review=False,
                    reason="S-tier immediate-alert event",
                    severity=event.severity,
                )
            return AlertDecision(
                should_alert=False,
                requires_review=False,
                reason="priority below immediate threshold",
                severity=event.severity,
            )
        if event.status == "confirmed" and priority >= IMMEDIATE_PRIORITY_THRESHOLD:
            return AlertDecision(
                should_alert=True,
                requires_review=False,
                reason="S-tier priority event",
                severity=event.severity,
            )
        return AlertDecision(
            should_alert=False,
            requires_review=event.status == "needs_review",
            reason="does not match alert rules",
            severity=event.severity,
        )