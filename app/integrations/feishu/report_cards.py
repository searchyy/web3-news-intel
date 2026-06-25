from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.core.config import settings
from app.core.errors import FetchError
from app.core.url_security import validate_public_http_url
from app.db.models import Event, ReportSchedule
from app.pipeline.scoring import event_priority_score

MAX_CARD_EVENTS = 10
REPORT_TOPIC_DEFINITIONS: tuple[dict[str, object], ...] = (
    {
        "label": "CEX 活动",
        "source_groups": {"exchange_official"},
        "categories": {
            "exchange_listing",
            "listing",
            "delisting",
            "derivatives_listing",
            "derivatives_delisting",
            "deposit_withdrawal",
            "wallet_maintenance",
            "system_maintenance",
            "trading_rule",
            "product",
            "regulatory",
        },
        "keywords": {"上币", "下架", "活动", "公告", "launchpool", "new listing", "delist"},
    },
    {
        "label": "DEX/项目活动",
        "source_groups": {"project_official", "project_news"},
        "categories": {"project_update", "exchange_repost", "token_unlock", "product"},
        "keywords": {
            "dex",
            "hyperliquid",
            "hype",
            "aster",
            "backpack",
            "airdrop",
            "perp",
            "永续",
            "空投",
        },
    },
    {
        "label": "市场情绪/爆仓",
        "source_groups": {"media_zh", "media_en", "onchain"},
        "categories": {"market", "onchain", "funding", "newsflash"},
        "keywords": {
            "爆仓",
            "清算",
            "liquidation",
            "liquidated",
            "funding rate",
            "资金费率",
            "open interest",
            "oi",
            "恐慌",
            "贪婪",
            "情绪",
        },
    },
    {
        "label": "资金流/鲸鱼",
        "source_groups": {"media_zh", "media_en", "onchain"},
        "categories": {"onchain", "market", "funding"},
        "keywords": {
            "巨鲸",
            "大鲸",
            "whale",
            "充值到交易所",
            "转入交易所",
            "exchange inflow",
            "deposit to exchange",
            "仓位",
            "position",
            "大额转账",
        },
    },
    {
        "label": "宏观利率",
        "source_groups": {"media_zh", "media_en", "regulator"},
        "categories": {"policy_regulatory", "regulatory", "market"},
        "keywords": {
            "加息",
            "降息",
            "利率",
            "美联储",
            "fed",
            "fomc",
            "powell",
            "cpi",
            "pce",
            "rate hike",
            "rate cut",
        },
    },
    {
        "label": "安全黑客",
        "source_groups": {"media_zh", "media_en", "protocol", "project_official"},
        "categories": {"security_incident", "hack_security", "security"},
        "keywords": {
            "黑客",
            "攻击",
            "被盗",
            "漏洞",
            "钓鱼",
            "hack",
            "exploit",
            "stolen",
            "phishing",
        },
    },
    {
        "label": "市场热点",
        "source_groups": {"media_zh", "media_en", "project_news"},
        "categories": {"market", "deep_article", "project_update", "funding", "token_unlock"},
        "keywords": {
            "热点",
            "叙事",
            "ai",
            "rwa",
            "etf",
            "meme",
            "stablecoin",
            "稳定币",
            "代币解锁",
        },
    },
)

@dataclass(slots=True)
class ReportPreview:
    schedule: ReportSchedule
    window_start: datetime
    window_end: datetime
    events: list[Event]
    event_count: int
    critical_high_count: int
    top_symbols: list[str]
    top_categories: list[str]
    summary_zh: str
    omitted_count: int
    card: dict[str, Any]


def build_report_preview(
    schedule: ReportSchedule,
    *,
    window_start: datetime,
    window_end: datetime,
    events: list[Event],
    total_count: int,
) -> ReportPreview:
    critical_high_count = sum(1 for event in events if event.severity in {"critical", "high"})
    top_symbols = _top_values(symbol for event in events for symbol in event.symbols)
    top_categories = _top_values(event.category for event in events)
    summary_zh = _build_summary(
        schedule,
        events,
        total_count,
        critical_high_count,
        top_symbols,
        top_categories,
    )
    shown_events = events[:_max_card_events(schedule)]
    omitted_count = max(total_count - len(shown_events), 0)
    preview = ReportPreview(
        schedule=schedule,
        window_start=window_start,
        window_end=window_end,
        events=events,
        event_count=total_count,
        critical_high_count=critical_high_count,
        top_symbols=top_symbols,
        top_categories=top_categories,
        summary_zh=summary_zh,
        omitted_count=omitted_count,
        card={},
    )
    preview.card = render_report_card(preview)
    return preview


def render_report_card(preview: ReportPreview) -> dict[str, Any]:
    schedule = preview.schedule
    title = f"{_report_type_label(schedule.report_type)}：{schedule.name}"
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "fields": [
                _field("汇报周期", _window_text(preview.window_start, preview.window_end)),
                _field("事件总数", str(preview.event_count)),
                _field("Critical/High", str(preview.critical_high_count)),
                _field("主要币种", "、".join(preview.top_symbols[:8]) or "无"),
                _field("主要分类", "、".join(preview.top_categories[:8]) or "无"),
            ],
        },
        {"tag": "hr"},
        {"tag": "div", "text": {"tag": "lark_md", "content": _md(preview.summary_zh)}},
    ]
    shown_events = preview.events[:_max_card_events(schedule)]
    if shown_events:
        elements.append({"tag": "hr"})
        if _is_daily_report(schedule):
            elements.append(
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": "**重点事件参考（非完整新闻列表）**",
                    },
                }
            )
        elements.extend(_event_blocks(shown_events))
    if preview.omitted_count > 0:
        elements.append(
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": f"还有 {preview.omitted_count} 条事件未在卡片中展示。",
                    }
                ],
            }
        )
    actions = _actions(preview)
    if actions:
        elements.append({"tag": "action", "actions": actions[:4]})
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "red" if preview.critical_high_count else "blue",
            "title": {"tag": "plain_text", "content": _bounded(title, 120)},
        },
        "elements": elements,
    }


def event_ai_summary(event: Event) -> str | None:
    for insight in getattr(event, "ai_insights", []) or []:
        if getattr(insight, "status", None) == "success" and getattr(
            insight, "summary_zh", None
        ):
            return _bounded(str(insight.summary_zh).strip(), 280)
    metadata = event.metadata_ or {}
    candidates = [
        metadata.get("ai_summary_zh"),
        metadata.get("summary_zh"),
        (metadata.get("ai") or {}).get("summary_zh")
        if isinstance(metadata.get("ai"), dict)
        else None,
        (metadata.get("ai_insight") or {}).get("summary_zh")
        if isinstance(metadata.get("ai_insight"), dict)
        else None,
    ]
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return _bounded(value.strip(), 280)
    return None


def _build_summary(
    schedule: ReportSchedule,
    events: list[Event],
    total_count: int,
    critical_high_count: int,
    top_symbols: list[str],
    top_categories: list[str],
) -> str:
    if total_count == 0:
        return (
            "本窗口暂无符合规则的事件，"
            "系统仍会按计划继续抓取和评分。"
        )
    if _is_daily_report(schedule):
        return _bounded(
            _build_daily_summary(
                schedule,
                events,
                total_count,
                critical_high_count,
                top_symbols,
                top_categories,
            ),
            2200,
        )
    return _bounded(
        _build_digest_summary(
            schedule,
            events,
            total_count,
            critical_high_count,
            top_symbols,
            top_categories,
        ),
        1600,
    )


def _build_digest_summary(
    schedule: ReportSchedule,
    events: list[Event],
    total_count: int,
    critical_high_count: int,
    top_symbols: list[str],
    top_categories: list[str],
) -> str:
    symbols_text = "、".join(top_symbols[:6]) or "暂无"
    categories_text = "、".join(_category_label(item) for item in top_categories[:6]) or "暂无"
    parts = [
        (
            f"本窗口共 {total_count} 条事件，"
            f"critical/high {critical_high_count} 条；"
            f"主要币种：{symbols_text}；主要分类：{categories_text}。"
        )
    ]
    topic_lines = _topic_summary_lines(events, max_topics=5, events_per_topic=2)
    if topic_lines:
        parts.append("主题总结" + "\n" + "\n".join(topic_lines))
    else:
        parts.append("暂无明显聚类，建议查看全部事件。")
    if schedule.include_ai_summary:
        ai_summaries = [event_ai_summary(event) for event in events]
        ai_summaries = [summary for summary in ai_summaries if summary]
        if ai_summaries:
            parts.append("AI重点：" + "；".join(ai_summaries[:3]))
        else:
            parts.append("AI 不可用或暂无可用摘要，本卡片使用确定性规则模板生成。")
    return "\n".join(parts)


def _build_daily_summary(
    schedule: ReportSchedule,
    events: list[Event],
    total_count: int,
    critical_high_count: int,
    top_symbols: list[str],
    top_categories: list[str],
) -> str:
    window_name = _daily_window_name(schedule)
    topic_lines = _topic_summary_lines(events, max_topics=6, events_per_topic=1)
    important = _important_events(events)
    symbols_text = "、".join(top_symbols[:8]) or "暂无"
    categories_text = "、".join(_category_label(item) for item in top_categories[:6]) or "暂无"
    lead = (
        f"结论：{window_name}共捕捉 {total_count} 条事件，"
        f"critical/high {critical_high_count} 条；主线集中在 {categories_text}；"
        f"重点币种/链：{symbols_text}。"
    )
    parts = [lead]
    if topic_lines:
        parts.append("本窗口发生的重点：" + "\n" + "\n".join(topic_lines))
    else:
        parts.append("本窗口没有形成明显主线，以低频观察为主。")
    parts.append("综合判断：" + _daily_judgement(events, top_symbols))
    follow_up = _follow_up_text(events, important)
    if follow_up:
        parts.append("建议跟进：" + follow_up)
    if important:
        refs = "；".join(_event_brief(event, limit=64) for event in important[:3])
        parts.append("参考事件：" + refs)
    return "\n".join(parts)


def _topic_summary_lines(
    events: list[Event], *, max_topics: int = 7, events_per_topic: int = 2
) -> list[str]:
    lines: list[str] = []
    for definition in REPORT_TOPIC_DEFINITIONS:
        matched = [event for event in events if _event_matches_topic(event, definition)]
        if not matched:
            continue
        matched.sort(key=_event_rank, reverse=True)
        briefs = "；".join(_event_brief(event) for event in matched[:events_per_topic])
        symbols = "、".join(
            _top_values(symbol for event in matched for symbol in event.symbols)[:5]
        )
        suffix = f"。相关：{symbols}" if symbols else ""
        lines.append(f"• {definition['label']}：{len(matched)} 条。{briefs}{suffix}")
        if len(lines) >= max_topics:
            break
    return lines


def _event_brief(event: Event, *, limit: int = 86) -> str:
    headline = event_ai_headline(event) or event_ai_summary(event) or event.title or "未命名事件"
    return _bounded(str(headline).strip(), limit)


def event_ai_headline(event: Event) -> str | None:
    insights = [
        insight
        for insight in getattr(event, "ai_insights", []) or []
        if getattr(insight, "status", None) == "success" and getattr(insight, "headline_zh", None)
    ]
    if not insights:
        return None
    latest = sorted(
        insights,
        key=lambda item: (
            getattr(item, "generated_at", None) or datetime.min.replace(tzinfo=UTC),
            getattr(item, "id", 0) or 0,
        ),
        reverse=True,
    )[0]
    return str(latest.headline_zh).strip() or None


def _important_events(events: list[Event]) -> list[Event]:
    important = [
        event
        for event in events
        if event.severity in {"critical", "high"} or event_priority_score(event) >= 70
    ]
    if not important:
        important = list(events[:3])
    return sorted(important, key=_event_rank, reverse=True)


def _event_rank(event: Event) -> tuple[int, int, datetime, int]:
    timestamp = event.published_at or event.first_seen_at or datetime.min.replace(tzinfo=UTC)
    return (
        event_priority_score(event),
        {"critical": 4, "high": 3, "normal": 2, "low": 1}.get(event.severity, 0),
        timestamp,
        event.id or 0,
    )


def _daily_judgement(events: list[Event], top_symbols: list[str]) -> str:
    labels = [
        str(definition["label"])
        for definition in REPORT_TOPIC_DEFINITIONS
        if any(_event_matches_topic(event, definition) for event in events)
    ]
    focus = "、".join(labels[:3]) if labels else "暂无明显主线"
    symbols = "、".join(top_symbols[:5]) if top_symbols else "未集中到具体币种"
    security_count = sum(
        1 for event in events if _event_matches_topic(event, REPORT_TOPIC_DEFINITIONS[5])
    )
    policy_count = sum(
        1 for event in events if _event_matches_topic(event, REPORT_TOPIC_DEFINITIONS[4])
    )
    judgement = f"当前主线更偏 {focus}，需要围绕 {symbols} 做机会筛选。"
    if security_count:
        judgement += f" 安全/黑客 {security_count} 条，需先排除交互风险。"
    if policy_count:
        judgement += f" 宏观/监管 {policy_count} 条，需留意对大盘的传导。"
    return judgement


def _follow_up_text(events: list[Event], important: list[Event]) -> str:
    if any(_event_matches_topic(event, REPORT_TOPIC_DEFINITIONS[5]) for event in events):
        return "先确认受影响协议/链和资金敞口，避免交互或授权。"
    if any(_event_matches_topic(event, REPORT_TOPIC_DEFINITIONS[0]) for event in events):
        return "优先核对报名时间、充值/交易门槛、快照和奖励规则，符合条件再参与。"
    if any(_event_matches_topic(event, REPORT_TOPIC_DEFINITIONS[2]) for event in events):
        return "关注爆仓、资金费率和 OI 是否延续，避免追高。"
    if important:
        return "把高分事件逐条核对原文，再决定打新、交互或交易动作。"
    return "继续观察，暂不主动加仓或批量交互。"


def _daily_window_name(schedule: ReportSchedule) -> str:
    if schedule.report_type == "daily_evening":
        return "早报到晚报"
    if schedule.report_type == "daily_morning":
        return "昨晚到早报"
    return "本次窗口"


def _is_daily_report(schedule: ReportSchedule) -> bool:
    return schedule.report_type in {"daily_morning", "daily_evening", "custom"}


def _max_card_events(schedule: ReportSchedule) -> int:
    return 5 if _is_daily_report(schedule) else MAX_CARD_EVENTS


def _category_label(value: str) -> str:
    labels = {
        "listing": "上币",
        "exchange_listing": "上币",
        "delisting": "下架",
        "derivatives_listing": "合约上线",
        "derivatives_delisting": "合约下架",
        "market": "市场",
        "newsflash": "快讯",
        "funding": "融资",
        "fundraising": "融资",
        "policy_regulatory": "宏观/监管",
        "regulatory": "宏观/监管",
        "hack_security": "安全黑客",
        "security": "安全黑客",
        "security_incident": "安全黑客",
        "onchain": "链上",
        "project_update": "项目动态",
        "token_unlock": "代币解锁",
        "product": "产品活动",
    }
    return labels.get(value, value)


def _event_matches_topic(event: Event, definition: dict[str, object]) -> bool:
    categories = definition.get("categories")
    if isinstance(categories, set) and event.category in categories:
        return True
    keywords = definition.get("keywords")
    text = _event_search_text(event)
    if isinstance(keywords, set) and any(str(keyword).lower() in text for keyword in keywords):
        return True
    return False


def _event_source_groups(event: Event) -> set[str]:
    groups: set[str] = set()
    for event_source in getattr(event, "sources", []) or []:
        source = getattr(event_source, "source", None)
        if source is None:
            continue
        config = getattr(source, "config", None) or {}
        for value in (
            getattr(source, "source_group", None),
            getattr(source, "source_type", None),
            config.get("source_group"),
            config.get("group"),
        ):
            if value:
                groups.add(str(value).lower())
    return groups


def _event_search_text(event: Event) -> str:
    metadata = event.metadata_ or {}
    metadata_text = " ".join(
        str(value) for value in metadata.values() if isinstance(value, str | int | float)
    )
    return " ".join(
        [
            event.title or "",
            event.summary or "",
            event.category or "",
            " ".join(event.symbols or []),
            " ".join(event.chains or []),
            metadata_text,
        ]
    ).lower()


def _event_blocks(events: list[Event]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for index, event in enumerate(events, start=1):
        published = event.published_at or event.first_seen_at
        pieces = [
            f"**{index}. {_md(_bounded(event.title, 120))}**",
            (
                f"级别：{_md(event.severity)} | 分类：{_md(event.category)} | "
                f"可信度：{event.trust_score}"
            ),
        ]
        priority_score_text = f"重点分：{event_priority_score(event)}"
        pieces.append(_md(priority_score_text))
        if event.symbols:
            pieces.append(f"币种：{_md('、'.join(event.symbols[:8]))}")
        if published:
            pieces.append(f"时间：{published.astimezone(UTC).isoformat()}")
        ai_summary = event_ai_summary(event)
        if ai_summary:
            pieces.append(f"AI：{_md(ai_summary)}")
        original_url = _safe_url(event.primary_url)
        if original_url:
            pieces.append(f"[查看原文]({original_url})")
        blocks.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(pieces)}})
    return blocks


def _actions(preview: ReportPreview) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    panel_url = _dashboard_url(preview)
    if panel_url:
        actions.append(_button("查看全部事件", panel_url))
    for event in preview.events[:2]:
        original_url = _safe_url(event.primary_url)
        if original_url:
            actions.append(_button("事件原文链接", original_url))
            break
    return actions


def _dashboard_url(preview: ReportPreview) -> str | None:
    if not settings.public_base_url:
        return None
    base_url = settings.public_base_url.rstrip("/")
    params = (
        f"published_from={preview.window_start.astimezone(UTC).isoformat()}"
        f"&published_to={preview.window_end.astimezone(UTC).isoformat()}"
    )
    return _safe_url(f"{base_url}/events?{params}")


def _field(label: str, value: str) -> dict[str, Any]:
    return {
        "is_short": True,
        "text": {"tag": "lark_md", "content": f"**{_md(label)}**\n{_md(value)}"},
    }


def _button(label: str, url: str) -> dict[str, Any]:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": label},
        "type": "default",
        "url": url,
    }


def _window_text(start: datetime, end: datetime) -> str:
    return f"{start.astimezone(UTC).isoformat()} 至 {end.astimezone(UTC).isoformat()}"


def _top_values(values) -> list[str]:
    counts: dict[str, int] = {}
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        counts[text] = counts.get(text, 0) + 1
    return [
        value
        for value, _count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:10]
    ]


def _report_type_label(report_type: str) -> str:
    return {
        "immediate": "立即告警",
        "digest_15m": "15 分钟摘要",
        "digest_30m": "30 分钟摘要",
        "hourly": "每小时汇报",
        "daily_morning": "每日早报",
        "daily_evening": "每日晚报",
        "custom": "自定义汇报",
    }.get(report_type, report_type)


def _safe_url(value: str | None) -> str | None:
    if not value:
        return None
    try:
        validate_public_http_url(
            value,
            allow_private_networks=False,
            allow_localhost=False,
            resolve_dns=False,
        )
    except FetchError:
        return None
    return value


def _md(value: str) -> str:
    escaped = html.escape(value)
    for char in ("\\", "[", "]", "(", ")", "*", "_", "`", "~", ">"):
        escaped = escaped.replace(char, f"\\{char}")
    return escaped


def _bounded(value: str, limit: int) -> str:
    cleaned = " ".join(value.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "..."
