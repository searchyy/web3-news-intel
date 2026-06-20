from __future__ import annotations

import pytest

from app.core.field_encryption import FieldEncryptionError, FieldEncryptor, fingerprint_secret


def test_encryption_round_trip_and_fingerprint() -> None:
    encryptor = FieldEncryptor(FieldEncryptor.generate_key())
    plaintext = "write-only-webhook-placeholder"
    ciphertext = encryptor.encrypt(plaintext)
    assert ciphertext != plaintext
    assert ciphertext.startswith("enc:v1:")
    assert encryptor.decrypt(ciphertext) == plaintext
    assert fingerprint_secret(plaintext)


def test_encryption_tampering_fails() -> None:
    encryptor = FieldEncryptor(FieldEncryptor.generate_key())
    ciphertext = encryptor.encrypt("secret")
    tampered = ciphertext[:-4] + "AAAA"
    with pytest.raises(FieldEncryptionError):
        encryptor.decrypt(tampered)


def test_invalid_key_fails_safely() -> None:
    with pytest.raises(FieldEncryptionError):
        FieldEncryptor("not-a-valid-key")
