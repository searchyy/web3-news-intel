from __future__ import annotations

from app.adapters.media.html import MediaHTMLAdapter
from app.adapters.media.json_api import MediaJSONAPIAdapter
from app.adapters.media.rss import MediaRSSAdapter

__all__ = ["MediaHTMLAdapter", "MediaJSONAPIAdapter", "MediaRSSAdapter"]
