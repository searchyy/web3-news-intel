from __future__ import annotations

from app.core.config import settings


def default_headers() -> dict[str, str]:
    return {
        "User-Agent": settings.http_user_agent,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.5"
        ),
    }
