from __future__ import annotations

CATEGORY_SEVERITY = {
    "exploit": "critical",
    "depeg": "critical",
    "chain_halt": "critical",
    "enforcement": "critical",
    "delisting": "high",
    "listing": "high",
    "protocol_upgrade": "high",
    "governance_passed": "high",
    "funding": "normal",
    "partnership": "normal",
    "media": "low",
    "rumor": "low",
}


def severity_for_category(category: str) -> str:
    return CATEGORY_SEVERITY.get(category, "normal")
