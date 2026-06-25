from __future__ import annotations

import re
from dataclasses import dataclass

from app.db.models import Event, EventSource
from app.pipeline.category import all_media_source_types, is_official_source, is_sensitive_category
from app.pipeline.severity import severity_for_category
from app.schemas.alert import ScoreResult

SOURCE_BASE_SCORE = {
    "regulator_official": 100,
    "exchange_official": 95,
    "protocol_official": 95,
    "project_official": 90,
    "governance_api": 90,
    "onchain_data": 85,
    "security_alert": 85,
    "tier1_media": 75,
    "chinese_media": 70,
    "news_aggregator": 55,
    "aggregator": 50,
    "social": 40,
}

SEVERITY_RANK = {"low": 1, "normal": 2, "medium": 3, "high": 4, "critical": 5}
SEVERITY_BASE_PRIORITY = {
    "critical": 78,
    "high": 56,
    "medium": 42,
    "normal": 32,
    "low": 18,
}
HIGH_SIGNAL_CATEGORIES = {
    "exploit",
    "security",
    "security_incident",
    "hack_security",
    "depeg",
    "chain_halt",
    "enforcement",
    "regulatory",
    "policy_regulatory",
    "listing",
    "exchange_listing",
    "delisting",
    "derivatives_listing",
    "derivatives_delisting",
    "deposit_withdrawal",
    "wallet_maintenance",
    "system_maintenance",
}
ALPHA_CATEGORY_BONUS = {
    "listing": 24,
    "exchange_listing": 24,
    "derivatives_listing": 20,
    "deposit_withdrawal": 14,
    "trading_rule": 10,
    "product": 12,
    "project_update": 16,
    "token_unlock": 14,
    "onchain": 18,
    "market": 12,
    "funding": 10,
    "fundraising": 10,
    "newsflash": 8,
}
WATCHLIST_SYMBOLS = {
    "BTC",
    "ETH",
    "SOL",
    "BNB",
    "HYPE",
    "ASTER",
    "BP",
    "USDT",
    "USDC",
    "ENA",
    "PUMP",
}
PRIORITY_KEYWORDS: tuple[tuple[str, int, tuple[str, ...]], ...] = (
    (
        "security incident",
        28,
        (
            "hack",
            "exploit",
            "stolen",
            "drained",
            "phishing",
            "vulnerability",
            "attack",
            "黑客",
            "攻击",
            "被盗",
            "漏洞",
            "钓鱼",
        ),
    ),
    (
        "market stress",
        24,
        (
            "liquidation",
            "liquidated",
            "forced liquidation",
            "爆仓",
            "清算",
            "open interest",
            "funding rate",
            "资金费率",
        ),
    ),
    (
        "exchange fund flow",
        24,
        (
            "exchange inflow",
            "deposit to exchange",
            "transferred to binance",
            "transferred to okx",
            "转入交易所",
            "充值到交易所",
            "流入交易所",
        ),
    ),
    (
        "whale movement",
        20,
        ("whale", "large transfer", "large position", "大鲸", "巨鲸", "大额转账", "仓位"),
    ),
    (
        "macro rates",
        18,
        (
            "fed",
            "fomc",
            "powell",
            "cpi",
            "pce",
            "rate hike",
            "rate cut",
            "interest rate",
            "加息",
            "降息",
            "利率",
            "美联储",
        ),
    ),
    (
        "listing or delisting",
        18,
        ("listing", "will list", "new listing", "delist", "上线", "上币", "下架"),
    ),
    (
        "project campaign",
        12,
        ("airdrop", "launchpool", "campaign", "trading competition", "空投", "活动", "奖励"),
    ),
    (
        "hot narrative",
        8,
        ("etf", "stablecoin", "rwa", "meme", "ai", "热点", "叙事", "稳定币"),
    ),
)
ALPHA_KEYWORDS: tuple[tuple[str, int, tuple[str, ...]], ...] = (
    (
        "alpha:exchange activity",
        30,
        (
            "launchpool",
            "launchpad",
            "new listing",
            "will list",
            "spot trading",
            "pre-market",
            "premarket",
            "trading competition",
            "campaign",
            "reward",
            "rewards",
            "earn",
            "staking",
            "farming",
            "token sale",
            "tge",
            "airdrop",
            "deposit opens",
            "withdrawal opens",
            "上币",
            "上线",
            "打新",
            "活动",
            "奖励",
            "空投",
            "交易赛",
            "质押挖矿",
            "开放充值",
            "开放提现",
        ),
    ),
    (
        "alpha:project interaction",
        28,
        (
            "testnet",
            "mainnet",
            "points",
            "quest",
            "quests",
            "galxe",
            "zealy",
            "xp",
            "faucet",
            "bridge",
            "mint",
            "whitelist",
            "node",
            "restaking",
            "invite",
            "交互",
            "积分",
            "测试网",
            "主网",
            "任务",
            "白名单",
            "铸造",
            "水龙头",
            "跨链",
        ),
    ),
    (
        "alpha:on-chain flow",
        26,
        (
            "smart money",
            "whale",
            "exchange inflow",
            "exchange outflow",
            "deposit to exchange",
            "dex volume",
            "tvl",
            "wallet",
            "address",
            "accumulate",
            "position",
            "large transfer",
            "funding rate",
            "open interest",
            "liquidation",
            "聪明钱",
            "巨鲸",
            "大鲸",
            "链上",
            "资金流",
            "流入",
            "流出",
            "买入",
            "建仓",
            "仓位",
            "爆仓",
        ),
    ),
    (
        "alpha:hot project",
        22,
        (
            "hype",
            "hyperliquid",
            "aster",
            "backpack",
            "bp",
            "perp dex",
            "meme",
            "ai agent",
            "rwa",
            "stablecoin",
            "defi",
            "热点",
            "叙事",
        ),
    ),
    (
        "alpha:macro market",
        16,
        (
            "btc",
            "bitcoin",
            "eth",
            "ethereum",
            "sol",
            "etf",
            "fed",
            "fomc",
            "powell",
            "cpi",
            "pce",
            "rate cut",
            "rate hike",
            "yield",
            "dxy",
            "大盘",
            "降息",
            "加息",
            "美联储",
            "通胀",
        ),
    ),
)
NOISE_KEYWORDS: tuple[tuple[str, int, tuple[str, ...]], ...] = (
    ("recap", 18, ("weekly recap", "daily recap", "newsletter", "周报", "日报", "回顾")),
    ("education", 14, ("learn", "guide", "how to", "101", "教程", "科普")),
    ("soft marketing", 12, ("ama", "spaces", "community call", "直播", "AMA")),
    (
        "generic analysis",
        20,
        (
            "price prediction",
            "prediction market",
            "price target",
            "forecast",
            "opinion",
            "analyst says",
            "could soar",
            "by 2030",
            "2030",
            "50x",
            "revival",
            "marketing",
            "speculation",
            "real-world risk",
            "market downturn",
            "record hack activity",
            "预测",
            "价格预测",
            "观点",
        ),
    ),
    (
        "traditional market/equity",
        28,
        (
            "stock perps",
            "hot stock perps",
            "stock perpetual",
            "tokenized equity",
            "xstocks",
            "xstock",
            "spcxx",
            "sonyusdt",
            "mvllusdt",
            "zhipu",
            "minimax",
            "wendy's",
            "micron",
            "semiconductor",
            "美股",
            "股票",
            "股票永续",
            "美光",
            "索尼",
            "迈威尔",
            "半导体",
            "财报",
        ),
    ),
    (
        "admin update",
        22,
        (
            "appoints",
            "appointment",
            "director",
            "office",
            "officer",
            "role",
            "salary",
            "joins as",
            "任命",
            "主任",
            "办公室",
            "职位",
            "薪资",
        ),
    ),
)


@dataclass(slots=True)
class PriorityResult:
    score: int
    tier: str
    reasons: list[str]
    noise_reasons: list[str]


class ScoringService:
    def score(self, event: Event, event_sources: list[EventSource]) -> ScoreResult:
        source_types = [
            event_source.source.source_type
            for event_source in event_sources
            if getattr(event_source, "source", None) is not None
        ]
        source_scores = [event_source.source_score for event_source in event_sources]
        max_source_score = max(source_scores, default=event.trust_score)
        independent_sources = len({event_source.source_id for event_source in event_sources})
        bonus = min(15, max(0, independent_sources - 1) * 8)
        trust_score = min(100, max_source_score + bonus)
        severity = severity_for_category(event.category)
        reasons: list[str] = []

        has_official = any(
            is_official_source(source_type) and score >= 90
            for source_type, score in zip(source_types, source_scores, strict=False)
        )
        if has_official:
            status = "confirmed"
            reasons.append("official source")
        elif is_sensitive_category(event.category) and all_media_source_types(source_types):
            status = (
                "confirmed" if independent_sources >= 2 and trust_score >= 80 else "needs_review"
            )
            reasons.append("sensitive media-only event")
        elif independent_sources >= 2 and trust_score >= 80:
            status = "confirmed"
            reasons.append("cross-source confirmation")
        else:
            status = "needs_review"
            reasons.append("single non-official source")

        if any(source_type == "onchain_data" for source_type in source_types):
            reasons.append("on-chain signal is labeled as inference")

        priority = calculate_event_priority(event, event_sources, severity=severity)
        if priority.score >= 85 and SEVERITY_RANK.get(severity, 0) < SEVERITY_RANK["high"]:
            severity = "high"
            reasons.append("priority promoted severity")
        if priority.score >= 92 and _has_security_signal(event):
            severity = "critical"
            reasons.append("security priority promoted severity")

        return ScoreResult(
            trust_score=trust_score,
            status=status,
            severity=severity,
            confirmation_count=max(1, independent_sources),
            reasons=reasons + priority.reasons,
            priority_score=priority.score,
            priority_tier=priority.tier,
            noise_reasons=priority.noise_reasons,
        )


def calculate_event_priority(
    event: Event,
    event_sources: list[EventSource] | None = None,
    *,
    severity: str | None = None,
) -> PriorityResult:
    metadata = event.metadata_ or {}
    stored = metadata.get("priority_score")
    stored_tier = metadata.get("priority_tier")
    if event_sources is None and isinstance(stored, int) and isinstance(stored_tier, str):
        return PriorityResult(
            score=_clamp(stored),
            tier=stored_tier,
            reasons=[str(item) for item in metadata.get("priority_reasons") or []],
            noise_reasons=[str(item) for item in metadata.get("noise_reasons") or []],
        )

    event_sources = event_sources or []
    source_types = [
        event_source.source.source_type
        for event_source in event_sources
        if getattr(event_source, "source", None) is not None
    ]
    source_scores = [event_source.source_score for event_source in event_sources]
    independent_sources = len({event_source.source_id for event_source in event_sources})
    max_source_score = max(source_scores, default=getattr(event, "trust_score", 50) or 50)
    severity_value = (
        severity
        or getattr(event, "severity", None)
        or severity_for_category(event.category)
    )
    score = SEVERITY_BASE_PRIORITY.get(severity_value, 28)
    reasons = [f"severity:{severity_value}"]
    noise_reasons: list[str] = []

    if event.category in HIGH_SIGNAL_CATEGORIES:
        score += 12
        reasons.append(f"category:{event.category}")
    alpha_category_bonus = ALPHA_CATEGORY_BONUS.get(event.category, 0)
    if alpha_category_bonus:
        score += alpha_category_bonus
        reasons.append(f"alpha category:{event.category}")
    if max_source_score >= 90:
        score += 12
        reasons.append("high-trust source")
    elif max_source_score >= 75:
        score += 6
        reasons.append("medium-trust source")
    if any(
        is_official_source(source_type) or source_type == "project_official"
        for source_type in source_types
    ):
        score += 10
        reasons.append("official source")
    if independent_sources >= 2:
        score += min(14, (independent_sources - 1) * 7)
        reasons.append("multi-source confirmation")
    elif getattr(event, "confirmation_count", 1) and event.confirmation_count >= 2:
        score += min(14, (event.confirmation_count - 1) * 7)
        reasons.append("multi-source confirmation")

    text = _event_text(event)
    for reason, weight, keywords in PRIORITY_KEYWORDS:
        if any(_keyword_matches(text, keyword) for keyword in keywords):
            score += weight
            reasons.append(reason)
    for reason, weight, keywords in ALPHA_KEYWORDS:
        if any(_keyword_matches(text, keyword) for keyword in keywords):
            score += weight
            reasons.append(reason)
    for reason, weight, keywords in NOISE_KEYWORDS:
        if any(_keyword_matches(text, keyword) for keyword in keywords):
            score -= weight
            noise_reasons.append(reason)
    if _is_low_alpha_regulatory(event, text):
        score -= 14
        noise_reasons.append("low alpha regulatory update")
    if "generic analysis" in noise_reasons:
        score = min(score - 10, 62)
    if "traditional market/equity" in noise_reasons:
        score = min(score - 12, 58)
    if set(symbol.upper() for symbol in (event.symbols or [])) & WATCHLIST_SYMBOLS:
        score += 6
        reasons.append("watchlist symbol")
    if not getattr(event, "published_at", None):
        score -= 8
        noise_reasons.append("missing published_at")
    if source_types and all(
        source_type in {"news_aggregator", "aggregator", "social"}
        for source_type in source_types
    ):
        score -= 12
        noise_reasons.append("aggregator-only source")

    score = _clamp(score)
    return PriorityResult(
        score=score,
        tier=priority_tier(score),
        reasons=reasons[:10],
        noise_reasons=noise_reasons[:8],
    )


def event_priority_score(event: Event) -> int:
    return calculate_event_priority(event).score


def priority_tier(score: int) -> str:
    if score >= 85:
        return "S"
    if score >= 70:
        return "A"
    if score >= 55:
        return "B"
    return "noise"


def source_base_score(source_type: str) -> int:
    return SOURCE_BASE_SCORE.get(source_type, 50)


def _event_text(event: Event) -> str:
    metadata = event.metadata_ or {}
    pieces = [
        event.title or "",
        event.summary or "",
        event.category or "",
        " ".join(event.symbols or []),
        " ".join(event.chains or []),
        " ".join(event.entities or []),
    ]
    for key in ("source_key", "topic", "event_type"):
        value = metadata.get(key)
        if isinstance(value, str):
            pieces.append(value)
    return re.sub(r"\s+", " ", " ".join(pieces)).lower()


def _has_security_signal(event: Event) -> bool:
    text = _event_text(event)
    return event.category in {"exploit", "security", "security_incident", "hack_security"} or any(
        keyword in text
        for keyword in ("hack", "exploit", "stolen", "drained", "黑客", "攻击", "被盗", "漏洞")
    )


def _keyword_matches(text: str, keyword: str) -> bool:
    needle = keyword.lower()
    if not needle:
        return False
    if re.fullmatch(r"[a-z0-9]+", needle):
        return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", text) is not None
    return needle in text


def _is_low_alpha_regulatory(event: Event, text: str) -> bool:
    if event.category not in {"regulatory", "policy_regulatory", "regulation"}:
        return False
    if any(
        keyword in text
        for keyword in (
            "etf",
            "fed",
            "fomc",
            "cpi",
            "pce",
            "rate cut",
            "rate hike",
            "enforcement",
            "lawsuit",
            "sues",
            "fine",
            "settlement",
            "加息",
            "降息",
            "执法",
            "起诉",
            "罚款",
        )
    ):
        return False
    return any(
        keyword in text
        for keyword in (
            "appoints",
            "appointment",
            "director",
            "office",
            "officer",
            "role",
            "salary",
            "statement",
            "speech",
            "任命",
            "主任",
            "办公室",
            "声明",
            "讲话",
        )
    )


def _clamp(value: int) -> int:
    return max(0, min(100, int(value)))