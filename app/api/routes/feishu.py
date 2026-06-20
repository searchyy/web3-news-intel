from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import utc_now
from app.db.models import Event
from app.db.repositories.notification_repo import NotificationRepository, write_audit_log
from app.db.session import get_session
from app.integrations.feishu.events import (
    callback_event_id,
    callback_type,
    extract_chat,
    is_bot_added,
    is_bot_removed,
    payload_hash,
)
from app.integrations.feishu.signatures import decrypt_event_payload, verify_event_signature
from app.observability.metrics import feishu_callback_total

router = APIRouter(prefix="/integrations/feishu", tags=["feishu"])
MAX_CALLBACK_BYTES = 256 * 1024


@router.post("/events")
async def feishu_events(
    request: Request,
    x_lark_request_timestamp: str | None = Header(default=None),
    x_lark_request_nonce: str | None = Header(default=None),
    x_lark_signature: str | None = Header(default=None),
    session: Session = Depends(get_session),
) -> dict:
    payload, body = await _verified_payload(
        request,
        x_lark_request_timestamp=x_lark_request_timestamp,
        x_lark_request_nonce=x_lark_request_nonce,
        x_lark_signature=x_lark_signature,
    )
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge")}
    event_id = callback_event_id(payload)
    event_type = callback_type(payload)
    receipt, inserted = NotificationRepository(session).record_callback_receipt(
        event_id=event_id,
        callback_type=event_type,
        payload_hash=payload_hash(body),
    )
    if not inserted:
        feishu_callback_total.labels(callback_type=event_type, result="duplicate").inc()
        session.commit()
        return {"status": "duplicate"}
    repo = NotificationRepository(session)
    chat_id, chat_name = extract_chat(payload)
    if chat_id and is_bot_added(event_type):
        destination = repo.upsert_feishu_group(chat_id=chat_id, chat_name=chat_name)
        write_audit_log(
            session,
            admin_subject="feishu_callback",
            action="group_pending",
            resource_type="destination",
            resource_id=str(destination.id),
            metadata={"callback_type": event_type},
            request_id=event_id,
        )
    elif chat_id and is_bot_removed(event_type):
        destination = repo.upsert_feishu_group(chat_id=chat_id, chat_name=chat_name)
        repo.disable(destination, status="disabled")
        write_audit_log(
            session,
            admin_subject="feishu_callback",
            action="group_removed",
            resource_type="destination",
            resource_id=str(destination.id),
            metadata={"callback_type": event_type},
            request_id=event_id,
        )
    receipt.status = "processed"
    receipt.processed_at = utc_now()
    session.commit()
    feishu_callback_total.labels(callback_type=event_type, result="success").inc()
    return {"status": "ok"}


@router.post("/card-actions")
async def feishu_card_actions(
    request: Request,
    x_lark_request_timestamp: str | None = Header(default=None),
    x_lark_request_nonce: str | None = Header(default=None),
    x_lark_signature: str | None = Header(default=None),
    session: Session = Depends(get_session),
) -> dict:
    payload, body = await _verified_payload(
        request,
        x_lark_request_timestamp=x_lark_request_timestamp,
        x_lark_request_nonce=x_lark_request_nonce,
        x_lark_signature=x_lark_signature,
    )
    event_id = callback_event_id(payload)
    event_type = callback_type(payload)
    receipt, inserted = NotificationRepository(session).record_callback_receipt(
        event_id=event_id,
        callback_type=event_type,
        payload_hash=payload_hash(body),
    )
    if not inserted:
        feishu_callback_total.labels(callback_type=event_type, result="duplicate").inc()
        session.commit()
        return {"toast": {"type": "info", "content": "Already processed"}}
    action = _action_value(payload)
    result_text = "Action recorded"
    if action.get("action") == "acknowledge" and action.get("event_id"):
        event = session.get(Event, int(action["event_id"]))
        if event:
            event.status = "acknowledged"
            result_text = "Event acknowledged"
    write_audit_log(
        session,
        admin_subject="feishu_card_action",
        action=str(action.get("action") or "unknown"),
        resource_type="event",
        resource_id=str(action.get("event_id") or ""),
        metadata={"callback_type": event_type},
        request_id=event_id,
    )
    receipt.status = "processed"
    receipt.processed_at = utc_now()
    session.commit()
    feishu_callback_total.labels(callback_type=event_type, result="success").inc()
    return {"toast": {"type": "success", "content": result_text}}


async def _verified_payload(
    request: Request,
    *,
    x_lark_request_timestamp: str | None,
    x_lark_request_nonce: str | None,
    x_lark_signature: str | None,
) -> tuple[dict, bytes]:
    content_type = request.headers.get("content-type", "")
    if "application/json" not in content_type:
        raise HTTPException(status_code=415, detail="unsupported content type")
    body = await request.body()
    if len(body) > MAX_CALLBACK_BYTES:
        raise HTTPException(status_code=413, detail="callback body too large")
    if not settings.feishu_verification_token and not settings.feishu_encrypt_key:
        raise HTTPException(status_code=503, detail="callback verification is not configured")
    if settings.feishu_encrypt_key and x_lark_signature:
        if not verify_event_signature(
            timestamp=x_lark_request_timestamp,
            nonce=x_lark_request_nonce,
            body=body,
            signature=x_lark_signature,
            encrypt_key=settings.feishu_encrypt_key,
        ):
            raise HTTPException(status_code=401, detail="invalid callback signature")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="malformed callback payload") from exc
    if "encrypt" in payload:
        if not settings.feishu_encrypt_key:
            raise HTTPException(status_code=401, detail="callback encryption is not configured")
        payload = decrypt_event_payload(settings.feishu_encrypt_key, str(payload["encrypt"]))
    token = payload.get("token") or (payload.get("header") or {}).get("token")
    if settings.feishu_verification_token and token != settings.feishu_verification_token:
        raise HTTPException(status_code=401, detail="invalid callback verification")
    return payload, body


def _action_value(payload: dict) -> dict:
    action = payload.get("action") or {}
    value = action.get("value") or payload.get("value") or {}
    return value if isinstance(value, dict) else {}
