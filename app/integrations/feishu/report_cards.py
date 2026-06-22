from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.core.config import settings
from app.core.errors import FetchError
from app.core.url_security import validate_public_http_url
from app.db.models import Event, ReportSchedule

MAX_CARD_EVENTS = 10


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
    shown_events = events[:MAX_CARD_EVENTS]
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
    if preview.events:
        elements.append({"tag": "hr"})
        elements.extend(_event_blocks(preview.events[:MAX_CARD_EVENTS]))
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
    if schedule.include_ai_summary:
        ai_summaries = [summary for event in events if (summary := event_ai_summary(event))]
        if ai_summaries:
            joined = "；".join(ai_summaries[:5])
            return _bounded(f"AI 摘要：{joined}", 900)
    symbols_text = "、".join(top_symbols[:6]) or "无集中币种"
    categories_text = "、".join(top_categories[:6]) or "无集中分类"
    return (
        f"本窗口共发现 {total_count} 条事件，其中 critical/high {critical_high_count} 条。"
        f"主要币种：{symbols_text}；主要分类：{categories_text}。"
        "AI 不可用或暂无可用摘要，本卡片使用确定性规则模板生成。"
    )


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
