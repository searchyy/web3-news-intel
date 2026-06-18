from __future__ import annotations

import re

STOP_SYMBOLS = {
    "A",
    "AI",
    "API",
    "CEO",
    "CFO",
    "DAO",
    "ETF",
    "EU",
    "FAQ",
    "FYI",
    "SEC",
    "US",
    "USD",
    "UTC",
    "VIP",
}

KNOWN_CHAINS = {
    "arbitrum",
    "avalanche",
    "base",
    "bitcoin",
    "bnb chain",
    "ethereum",
    "optimism",
    "polygon",
    "solana",
    "sui",
    "ton",
    "tron",
}

SYMBOL_PATTERNS = [
    re.compile(r"\(([A-Z0-9]{2,10})\)"),
    re.compile(r"\$([A-Z][A-Z0-9]{1,9})\b"),
    re.compile(r"\b([A-Z][A-Z0-9]{1,9})\b"),
]


def extract_symbols(text: str) -> list[str]:
    seen: set[str] = set()
    for pattern in SYMBOL_PATTERNS:
        for match in pattern.findall(text):
            symbol = match.upper()
            if symbol not in STOP_SYMBOLS and not symbol.isdigit():
                seen.add(symbol)
    return sorted(seen)


def extract_chains(text: str) -> list[str]:
    lower = text.lower()
    return sorted(chain for chain in KNOWN_CHAINS if chain in lower)


def extract_entities(text: str) -> list[str]:
    entities: set[str] = set()
    for match in re.findall(r"\b([A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+){0,3})\b", text):
        if match.upper() not in STOP_SYMBOLS and len(match) > 2:
            entities.add(match.strip())
    return sorted(entities)[:20]
