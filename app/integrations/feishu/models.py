from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class FeishuSendResult:
    ok: bool
    message_id: str | None = None
    status_code: int | None = None
    retry_after: int | None = None
    error: str | None = None
    dry_run: bool = False


FeishuCard = dict[str, Any]
