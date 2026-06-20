from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.field_encryption import FieldEncryptor, fingerprint_secret
from app.core.time import utc_now
from app.db.models import AdminAuditLog, FeishuCallbackReceipt, NotificationDestination


def destination_key(provider: str, identifier: str) -> str:
    digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:12]
    return f"{provider}-{digest}"


class NotificationRepository:
    def __init__(self, session: Session):
        self.session = session

    def list_destinations(self) -> list[NotificationDestination]:
        return list(
            self.session.scalars(
                select(NotificationDestination).order_by(NotificationDestination.created_at.desc())
            )
        )

    def get_destination(self, destination_id: uuid.UUID) -> NotificationDestination | None:
        return self.session.get(NotificationDestination, destination_id)

    def active_destinations(self) -> list[NotificationDestination]:
        return list(
            self.session.scalars(
                select(NotificationDestination).where(
                    NotificationDestination.enabled.is_(True),
                    NotificationDestination.status == "active",
                )
            )
        )

    def upsert_feishu_group(
        self, *, chat_id: str, chat_name: str | None, status: str = "pending"
    ) -> NotificationDestination:
        destination = self.session.scalar(
            select(NotificationDestination).where(
                NotificationDestination.provider == "feishu_app",
                NotificationDestination.chat_id == chat_id,
            )
        )
        if destination is None:
            destination = NotificationDestination(
                key=destination_key("feishu-app", chat_id),
                name=chat_name or "Feishu group",
                provider="feishu_app",
                enabled=False,
                status=status,
                chat_id=chat_id,
                chat_name=chat_name,
                config={},
            )
            self.session.add(destination)
            try:
                self.session.flush()
            except IntegrityError:
                self.session.rollback()
                existing = self.session.scalar(
                    select(NotificationDestination).where(
                        NotificationDestination.provider == "feishu_app",
                        NotificationDestination.chat_id == chat_id,
                    )
                )
                if existing is None:
                    raise
                destination = existing
        else:
            if chat_name:
                destination.chat_name = chat_name
                destination.name = chat_name
        return destination

    def create_webhook_destination(
        self,
        *,
        key: str,
        name: str,
        webhook_url: str,
        encryptor: FieldEncryptor,
        config: dict,
    ) -> NotificationDestination:
        destination = NotificationDestination(
            key=key,
            name=name,
            provider="feishu_webhook",
            enabled=False,
            status="pending",
            secret_ciphertext=encryptor.encrypt(webhook_url),
            secret_fingerprint=fingerprint_secret(webhook_url),
            config=config,
        )
        self.session.add(destination)
        self.session.flush()
        return destination

    def approve(self, destination: NotificationDestination) -> None:
        destination.status = "active"
        destination.enabled = True
        destination.activated_at = utc_now()

    def disable(self, destination: NotificationDestination, status: str = "disabled") -> None:
        destination.enabled = False
        destination.status = status

    def record_callback_receipt(
        self,
        *,
        event_id: str,
        callback_type: str,
        payload_hash: str,
    ) -> tuple[FeishuCallbackReceipt, bool]:
        existing = self.session.scalar(
            select(FeishuCallbackReceipt).where(FeishuCallbackReceipt.event_id == event_id)
        )
        if existing is not None:
            return existing, False
        receipt = FeishuCallbackReceipt(
            event_id=event_id,
            callback_type=callback_type,
            payload_hash=payload_hash,
            status="received",
        )
        try:
            with self.session.begin_nested():
                self.session.add(receipt)
                self.session.flush()
            return receipt, True
        except IntegrityError:
            existing = self.session.scalar(
                select(FeishuCallbackReceipt).where(FeishuCallbackReceipt.event_id == event_id)
            )
            if existing is None:
                raise
            return existing, False


def write_audit_log(
    session: Session,
    *,
    admin_subject: str,
    action: str,
    resource_type: str,
    resource_id: str | None,
    metadata: dict,
    request_id: str,
    ip_hash: str | None = None,
) -> AdminAuditLog:
    safe_metadata = _sanitize_metadata(metadata)
    audit = AdminAuditLog(
        admin_subject=admin_subject,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        metadata_=safe_metadata,
        request_id=request_id,
        ip_hash=ip_hash,
    )
    session.add(audit)
    session.flush()
    return audit


def _sanitize_metadata(metadata: dict) -> dict:
    blocked = {"password", "token", "secret", "webhook", "cookie", "authorization", "body"}
    result = {}
    for key, value in metadata.items():
        lowered = key.lower()
        if any(item in lowered for item in blocked):
            result[key] = "[redacted]"
        elif isinstance(value, dict):
            result[key] = _sanitize_metadata(value)
        elif isinstance(value, list):
            result[key] = [
                _sanitize_metadata(item) if isinstance(item, dict) else item for item in value
            ]
        else:
            result[key] = value
    return result


def contains_any(values: Iterable[str], candidates: Iterable[str]) -> bool:
    normalized = {value.upper() for value in values}
    return any(candidate.upper() in normalized for candidate in candidates)
