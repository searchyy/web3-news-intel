from __future__ import annotations

import uuid

import structlog


def bind_trace_id(trace_id: str | None = None) -> str:
    value = trace_id or str(uuid.uuid4())
    structlog.contextvars.bind_contextvars(trace_id=value)
    return value
