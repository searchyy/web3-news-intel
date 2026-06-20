from __future__ import annotations

import hashlib
from typing import Any


def payload_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def callback_event_id(payload: dict[str, Any]) -> str:
    header = payload.get("header") or {}
    event = payload.get("event") or {}
    return str(
        header.get("event_id")
        or event.get("event_id")
        or payload.get("uuid")
        or payload.get("event_id")
        or payload_hash(repr(payload).encode("utf-8"))
    )


def callback_type(payload: dict[str, Any]) -> str:
    header = payload.get("header") or {}
    event = payload.get("event") or {}
    return str(header.get("event_type") or event.get("type") or payload.get("type") or "unknown")


def extract_chat(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    event = payload.get("event") or {}
    chat = event.get("chat") or event.get("operator_chat") or {}
    chat_id = chat.get("chat_id") or event.get("chat_id") or event.get("open_chat_id")
    chat_name = chat.get("name") or chat.get("chat_name") or event.get("chat_name")
    return (str(chat_id) if chat_id else None, str(chat_name) if chat_name else None)


def is_bot_added(callback_name: str) -> bool:
    return "bot" in callback_name and ("add" in callback_name or "join" in callback_name)


def is_bot_removed(callback_name: str) -> bool:
    return "bot" in callback_name and ("remove" in callback_name or "leave" in callback_name)
