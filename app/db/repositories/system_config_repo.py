from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.field_encryption import FieldEncryptor, fingerprint_secret
from app.db.models import SystemConfig

PLAIN_FEISHU_KEYS = {
    "FEISHU_APP_ID": "feishu_app_id",
    "FEISHU_TEST_CHAT_ID": "feishu_test_chat_id",
    "FEISHU_ENABLED": "feishu_enabled",
    "FEISHU_SEND_ENABLED": "feishu_send_enabled",
}
SECRET_FEISHU_KEYS = {
    "FEISHU_APP_SECRET": "feishu_app_secret",
    "FEISHU_VERIFICATION_TOKEN": "feishu_verification_token",
    "FEISHU_ENCRYPT_KEY": "feishu_encrypt_key",
}
MASKED_PLACEHOLDERS = {"••••••", "******", "********"}


class SystemConfigRepository:
    def __init__(self, session: Session):
        self.session = session

    def read_feishu_config(self) -> dict[str, str | bool | None]:
        rows = self._rows()
        result: dict[str, str | bool | None] = {
            "FEISHU_ENABLED": _bool_text(rows.get("FEISHU_ENABLED"), settings.feishu_enabled),
            "FEISHU_SEND_ENABLED": _bool_text(
                rows.get("FEISHU_SEND_ENABLED"), settings.feishu_send_enabled
            ),
            "FEISHU_APP_ID": _plain_text(rows.get("FEISHU_APP_ID"), settings.feishu_app_id),
            "FEISHU_TEST_CHAT_ID": _plain_text(
                rows.get("FEISHU_TEST_CHAT_ID"), settings.feishu_test_chat_id
            ),
        }
        for env_key, attr in SECRET_FEISHU_KEYS.items():
            row = rows.get(env_key)
            if row and row.secret_hint:
                result[env_key] = _mask_hint(row.secret_hint)
            else:
                result[env_key] = _mask_secret(getattr(settings, attr))
        return result

    def read_feishu_plaintext(
        self, encryptor: FieldEncryptor | None
    ) -> dict[str, str | bool | None]:
        rows = self._rows()
        result = self.read_feishu_config()
        for env_key, attr in SECRET_FEISHU_KEYS.items():
            row = rows.get(env_key)
            if row and row.secret_ciphertext:
                if encryptor is None:
                    result[env_key] = None
                else:
                    result[env_key] = encryptor.decrypt(row.secret_ciphertext)
            else:
                result[env_key] = getattr(settings, attr)
        return result

    def save_feishu_config(
        self,
        values: Mapping[str, str | bool | None],
        *,
        encryptor: FieldEncryptor | None,
    ) -> None:
        for key in PLAIN_FEISHU_KEYS:
            if key in values:
                self._upsert_plain(key, _value_to_text(values[key]))
        for key in SECRET_FEISHU_KEYS:
            if key not in values:
                continue
            value = values[key]
            if value is None or value == "" or _looks_masked(str(value)):
                continue
            if encryptor is None:
                raise ValueError("FIELD_ENCRYPTION_KEY is required for Feishu secrets")
            plaintext = str(value)
            self._upsert_secret(
                key,
                ciphertext=encryptor.encrypt(plaintext),
                fingerprint=fingerprint_secret(plaintext),
                hint=plaintext[-4:] if len(plaintext) >= 4 else plaintext,
            )
        self.session.flush()

    def _rows(self) -> dict[str, SystemConfig]:
        keys = [*PLAIN_FEISHU_KEYS, *SECRET_FEISHU_KEYS]
        rows = self.session.scalars(select(SystemConfig).where(SystemConfig.key.in_(keys)))
        return {row.key: row for row in rows}

    def _upsert_plain(self, key: str, value: str | None) -> None:
        row = self.session.get(SystemConfig, key)
        if row is None:
            row = SystemConfig(key=key)
            self.session.add(row)
        row.value_text = value
        row.secret_ciphertext = None
        row.secret_fingerprint = None
        row.secret_hint = None

    def _upsert_secret(
        self, key: str, *, ciphertext: str, fingerprint: str, hint: str
    ) -> None:
        row = self.session.get(SystemConfig, key)
        if row is None:
            row = SystemConfig(key=key)
            self.session.add(row)
        row.value_text = None
        row.secret_ciphertext = ciphertext
        row.secret_fingerprint = fingerprint
        row.secret_hint = hint


def _value_to_text(value: str | bool | None) -> str | None:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return None
    return str(value)


def _plain_text(row: SystemConfig | None, fallback: str | None) -> str | None:
    return row.value_text if row and row.value_text is not None else fallback


def _bool_text(row: SystemConfig | None, fallback: bool) -> bool:
    if row is None or row.value_text is None:
        return fallback
    return row.value_text.lower() == "true"


def _mask_secret(value: str | None) -> str | None:
    if not value:
        return None
    return _mask_hint(value[-4:] if len(value) >= 4 else value)


def _mask_hint(hint: str) -> str:
    return f"****{hint}"


def _looks_masked(value: str) -> bool:
    return value in MASKED_PLACEHOLDERS or value.startswith("****")
