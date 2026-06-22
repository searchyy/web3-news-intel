from __future__ import annotations

import html
from datetime import UTC
from typing import Any

from app.core.config import settings
from app.core.errors import FetchError
from app.core.url_security import validate_public_http_url
from app.db.models import Event, EventAIInsight
from app.integrations.feishu.report_cards import event_ai_summary

SEVERITY_TEMPLATE = {
    "critical": "red",
    "high": "orange",
    "normal": "blue",
    "low": "grey",
}

RISK_LABEL = {
    "low": "低",
    "medium": "中",
    "high": "高",
    "critical": "严重",
}


def render_event_card(event: Event, *, dashboard_base_url: str | None = None) -> dict[str, Any]:
    insight = _latest_successful_insight(event)
    title = _bounded(insight.headline_zh if insight and insight.headline_zh else event.title, 160)
    summary = _event_summary(event, insight)
    source_name = _source_name(event)
    published = event.published_at or event.first_seen_at
    published_text = published.astimezone(UTC).isoformat() if published else "unknown"
    dashboard_url = _safe_dashboard_url(dashboard_base_url, event.id)
    original_url = _safe_url(event.primary_url)
    actions = []
    if original_url:
        actions.append(_button("查看原文", original_url))
    if dashboard_url:
        actions.append(_button("打开管理后台", dashboard_url))
    actions.extend(
        [
            _action_button("确认处理", {"action": "acknowledge", "event_id": str(event.id)}),
            _action_button(
                "静默币种 1 小时",
                {"action": "mute_symbol", "event_id": str(event.id)},
            ),
        ]
    )
    elements: list[dict[str, Any]] = [
        {
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": "AI 生成内容，仅用于信息整理，不代表来源官方结论。",
                }
            ],
        }
    ]
    if insight and insight.input_quality == "title_only":
        elements.append(
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": "输入信息较少，本摘要主要根据标题生成。",
                    }
                ],
            }
        )
    elements.extend(
        [
            {
                "tag": "div",
                "fields": [
                    _field("来源", source_name),
                    _field("发布时间", published_text),
                    _field("分类", event.category),
                    _field("严重级别", event.severity),
                    _field("可信度", str(event.trust_score)),
                    _field("确认次数", str(event.confirmation_count)),
                    _field("影响币种", ", ".join(event.symbols[:10]) or "无"),
                    _field("影响链", ", ".join(event.chains[:10]) or "无"),
                ],
            },
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": _md(summary)}},
        ]
    )
    if insight:
        elements.extend(_insight_blocks(insight))
    if actions:
        elements.append({"tag": "action", "actions": actions[:4]})
    header_title = f"{event.severity.upper()} {event.category}: {title}"
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": SEVERITY_TEMPLATE.get(event.severity, "blue"),
            "title": {"tag": "plain_text", "content": header_title},
        },
        "elements": elements,
    }


def render_event_text(event: Event) -> str:
    url = _safe_url(event.primary_url)
    insight = _latest_successful_insight(event)
    summary = _event_summary(event, insight)
    parts = [
        f"{event.severity.upper()} {event.category}: {_bounded(event.title, 160)}",
        f"状态: {event.status}",
        f"可信度: {event.trust_score}",
    ]
    if insight:
        parts.append(f"AI 重要度: {insight.importance_score}")
        parts.append(f"AI 风险: {RISK_LABEL.get(insight.risk_level, insight.risk_level)}")
    if event.symbols:
        parts.append(f"币种: {', '.join(event.symbols[:10])}")
    parts.append(summary)
    if url:
        parts.append(url)
    return "\n".join(parts)


def _insight_blocks(insight: EventAIInsight) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [
        {"tag": "hr"},
        {
            "tag": "div",
            "fields": [
                _field("AI 重要度", str(insight.importance_score or 0)),
                _field(
                    "AI 风险级别",
                    RISK_LABEL.get(insight.risk_level, insight.risk_level) or "未知",
                ),
                _field("AI 置信度", f"{(insight.confidence or 0.0) * 100:.0f}%"),
                _field("输入质量", insight.input_quality or "未知"),
            ],
        },
    ]
    facts = _fact_texts(insight)[:3]
    if facts:
        content = "\n".join(f"{index}. {_md(fact)}" for index, fact in enumerate(facts, 1))
        blocks.append({"tag": "div", "text": {"tag": "lark_md", "content": content}})
    if insight.market_impact:
        blocks.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**市场影响**\n{_md(_bounded(insight.market_impact, 300))}",
                },
            }
        )
    return blocks


def _fact_texts(insight: EventAIInsight) -> list[str]:
    values = insight.key_facts or insight.facts or []
    result: list[str] = []
    for item in values:
        if isinstance(item, dict):
            text = item.get("fact") or item.get("text") or item.get("summary")
        else:
            text = str(item)
        if isinstance(text, str) and text.strip():
            result.append(_bounded(text.strip(), 180))
    return result


def _field(label: str, value: str | None) -> dict[str, Any]:
    return {
        "is_short": True,
        "text": {"tag": "lark_md", "content": f"**{_md(label)}**\n{_md(value or '未知')}"},
    }


def _button(label: str, url: str) -> dict[str, Any]:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": label},
        "type": "default",
        "url": url,
    }


def _action_button(label: str, value: dict[str, str]) -> dict[str, Any]:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": label},
        "type": "default",
        "value": value,
    }


def _source_name(event: Event) -> str:
    if event.sources:
        source = event.sources[0].source
        if source:
            return source.display_name_zh or source.name
    return "未知"


def _event_summary(event: Event, insight: EventAIInsight | None) -> str:
    if insight and insight.summary_zh:
        return _bounded(insight.summary_zh, 500)
    return _bounded(event_ai_summary(event) or event.summary or "暂无摘要。", 500)


def _latest_successful_insight(event: Event) -> EventAIInsight | None:
    insights = sorted(
        getattr(event, "ai_insights", []) or [],
        key=lambda item: (item.generated_at is not None, item.generated_at, item.id or 0),
        reverse=True,
    )
    for insight in insights:
        if insight.status in {"succeeded", "success"} and insight.summary_zh:
            return insight
    return None


def _bounded(value: str, limit: int) -> str:
    cleaned = " ".join(value.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "..."


def _md(value: str | None) -> str:
    return html.escape(value or "")


def _safe_url(value: str | None) -> str | None:
    if not value:
        return None
    try:
        validate_public_http_url(
            value,
            allow_private_networks=False,
            allow_localhost=settings.http_allow_localhost,
            resolve_dns=False,
        )
    except FetchError:
        return None
    return value


def _safe_dashboard_url(base_url: str | None, event_id: int | None) -> str | None:
    if not base_url or event_id is None:
        return None
    return _safe_url(f"{base_url.rstrip('/')}/events/{event_id}")
