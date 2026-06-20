from __future__ import annotations

import html
from datetime import UTC
from typing import Any

from app.core.config import settings
from app.core.errors import FetchError
from app.core.url_security import validate_public_http_url
from app.db.models import Event

SEVERITY_TEMPLATE = {
    "critical": "red",
    "high": "orange",
    "normal": "blue",
    "low": "grey",
}


def render_event_card(event: Event, *, dashboard_base_url: str | None = None) -> dict[str, Any]:
    title = _bounded(event.title, 160)
    summary = _bounded(event.summary or "No summary available.", 500)
    source_name = _source_name(event)
    published = event.published_at or event.first_seen_at
    published_text = published.astimezone(UTC).isoformat() if published else "unknown"
    dashboard_url = _safe_dashboard_url(dashboard_base_url, event.id)
    original_url = _safe_url(event.primary_url)
    actions = []
    if original_url:
        actions.append(_button("View original source", original_url))
    if dashboard_url:
        actions.append(_button("Open management dashboard", dashboard_url))
    actions.extend(
        [
            _action_button("Acknowledge", {"action": "acknowledge", "event_id": str(event.id)}),
            _action_button(
                "Mute symbol for one hour",
                {"action": "mute_symbol", "event_id": str(event.id)},
            ),
        ]
    )
    header_title = html.escape(f"{event.severity.upper()} {event.category}: {title}")
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": SEVERITY_TEMPLATE.get(event.severity, "blue"),
            "title": {"tag": "plain_text", "content": header_title},
        },
        "elements": [
            {
                "tag": "div",
                "fields": [
                    _field("Source", source_name),
                    _field("Published", published_text),
                    _field("Trust score", str(event.trust_score)),
                    _field("Confirmations", str(event.confirmation_count)),
                    _field("Symbols", ", ".join(event.symbols[:10]) or "none"),
                    _field("Chains", ", ".join(event.chains[:10]) or "none"),
                    _field("Status", event.status),
                ],
            },
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": html.escape(summary)}},
            {"tag": "action", "actions": actions[:4]},
        ],
    }


def render_event_text(event: Event) -> str:
    url = _safe_url(event.primary_url)
    parts = [
        f"{event.severity.upper()} {event.category}: {html.escape(_bounded(event.title, 160))}",
        f"Status: {event.status}",
        f"Trust score: {event.trust_score}",
    ]
    if event.symbols:
        parts.append(f"Symbols: {', '.join(event.symbols[:10])}")
    if event.summary:
        parts.append(html.escape(_bounded(event.summary, 500)))
    if url:
        parts.append(url)
    return "\n".join(parts)


def _field(label: str, value: str) -> dict[str, Any]:
    return {
        "is_short": True,
        "text": {"tag": "lark_md", "content": f"**{html.escape(label)}**\n{html.escape(value)}"},
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
            return source.name
    return "unknown"


def _bounded(value: str, limit: int) -> str:
    cleaned = " ".join(value.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "..."


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
