from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def sign_custom_webhook(timestamp: int, secret: str) -> str:
    digest = hmac.new(f"{timestamp}\n{secret}".encode(), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def verify_event_signature(
    *,
    timestamp: str | None,
    nonce: str | None,
    body: bytes,
    signature: str | None,
    encrypt_key: str | None,
    max_age_seconds: int = 300,
) -> bool:
    if not signature or not timestamp or not nonce or not encrypt_key:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs(int(time.time()) - ts) > max_age_seconds:
        return False
    expected = hashlib.sha256(
        timestamp.encode() + nonce.encode() + encrypt_key.encode() + body
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def decrypt_event_payload(encrypt_key: str, encrypted_payload: str) -> dict:
    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
    raw = base64.b64decode(encrypted_payload)
    if len(raw) <= 12:
        raise ValueError("invalid encrypted Feishu payload")
    nonce = raw[:12]
    ciphertext = raw[12:]
    plaintext = AESGCM(key).decrypt(nonce, ciphertext, None)
    return json.loads(plaintext)
