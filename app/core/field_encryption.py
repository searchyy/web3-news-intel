from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass, field

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class FieldEncryptionError(ValueError):
    pass


def _decode_key(raw_key: str) -> bytes:
    try:
        key = base64.urlsafe_b64decode(raw_key.encode("ascii"))
    except Exception as exc:
        raise FieldEncryptionError("FIELD_ENCRYPTION_KEY must be base64 encoded") from exc
    if len(key) != 32:
        raise FieldEncryptionError("FIELD_ENCRYPTION_KEY must decode to 32 bytes")
    return key


@dataclass(slots=True)
class FieldEncryptor:
    raw_key: str
    _key: bytes = field(init=False, repr=False)
    _aesgcm: AESGCM = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._key = _decode_key(self.raw_key)
        self._aesgcm = AESGCM(self._key)

    @staticmethod
    def generate_key() -> str:
        return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")

    def encrypt(self, plaintext: str) -> str:
        nonce = os.urandom(12)
        ciphertext = self._aesgcm.encrypt(nonce, plaintext.encode("utf-8"), b"web3-news-intel:v1")
        payload = {
            "v": 1,
            "alg": "AES-256-GCM",
            "n": base64.urlsafe_b64encode(nonce).decode("ascii"),
            "c": base64.urlsafe_b64encode(ciphertext).decode("ascii"),
        }
        return "enc:v1:" + base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).decode("ascii")

    def decrypt(self, payload: str) -> str:
        if not payload.startswith("enc:v1:"):
            raise FieldEncryptionError("unsupported encrypted payload version")
        try:
            raw = base64.urlsafe_b64decode(payload.removeprefix("enc:v1:").encode("ascii"))
            data = json.loads(raw)
            if data.get("v") != 1 or data.get("alg") != "AES-256-GCM":
                raise FieldEncryptionError("unsupported encrypted payload format")
            nonce = base64.urlsafe_b64decode(data["n"].encode("ascii"))
            ciphertext = base64.urlsafe_b64decode(data["c"].encode("ascii"))
            plaintext = self._aesgcm.decrypt(nonce, ciphertext, b"web3-news-intel:v1")
            return plaintext.decode("utf-8")
        except InvalidTag as exc:
            raise FieldEncryptionError("encrypted field failed authentication") from exc
        except FieldEncryptionError:
            raise
        except Exception as exc:
            raise FieldEncryptionError("invalid encrypted field payload") from exc


def fingerprint_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def mask_fingerprint(fingerprint: str | None) -> str | None:
    if not fingerprint:
        return None
    return f"sha256:{fingerprint[:10]}..."
