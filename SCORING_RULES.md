# Trust Scoring and Alert Rules

## Source Base Score

```python
SOURCE_BASE_SCORE = {
    "regulator_official": 100,
    "exchange_official": 95,
    "protocol_official": 95,
    "governance_api": 90,
    "onchain_data": 85,
    "security_alert": 85,
    "tier1_media": 75,
    "chinese_media": 70,
    "aggregator": 50,
    "social": 40,
}
```

## Category Severity

```python
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
}
```

## Confirmation Rules

1. Official source with score >= 90:
   - status = confirmed
   - confirmation_count may be 1

2. Media-only event with sensitive category:
   - status = needs_review
   - push only if severity is critical and wording says "reported by..."

3. Two or more independent media sources:
   - add confirmation bonus
   - status may become confirmed if score >= 80 and sources are independent

4. On-chain signal:
   - can raise severity
   - must be labeled as "on-chain signal" or "inference"
   - does not equal official confirmation

5. Social source:
   - never directly confirmed unless source is a verified official project account and configured as official
