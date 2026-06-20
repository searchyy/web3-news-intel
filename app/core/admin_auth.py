from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any

import redis
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Header, HTTPException, Request, Response, status

from app.core.config import settings

SESSION_COOKIE = "web3_admin_session"
CSRF_COOKIE = "web3_admin_csrf"
CSRF_HEADER = "x-csrf-token"


@dataclass(slots=True)
class AdminPrincipal:
    subject: str
    session_id: str
    csrf_token: str


class AdminSessionStore:
    def __init__(self) -> None:
        self._memory: dict[str, tuple[float, dict[str, Any]]] = {}
        self._redis: redis.Redis | None = None

    def create(self, subject: str) -> tuple[str, str]:
        session_id = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        data = {"subject": subject, "csrf_token": csrf_token, "created_at": int(time.time())}
        self._set(session_id, data)
        return session_id, csrf_token

    def get(self, session_id: str) -> dict[str, Any] | None:
        key = self._key(session_id)
        client = self._client()
        if client:
            raw = client.get(key)
            return json.loads(raw) if raw else None
        item = self._memory.get(key)
        if item is None:
            return None
        expires_at, data = item
        if expires_at < time.time():
            self._memory.pop(key, None)
            return None
        return data

    def delete(self, session_id: str) -> None:
        key = self._key(session_id)
        client = self._client()
        if client:
            client.delete(key)
        self._memory.pop(key, None)

    def _set(self, session_id: str, data: dict[str, Any]) -> None:
        key = self._key(session_id)
        ttl = settings.admin_session_ttl_seconds
        client = self._client()
        if client:
            client.setex(key, ttl, json.dumps(data, separators=(",", ":")))
            return
        self._memory[key] = (time.time() + ttl, data)

    def _client(self) -> redis.Redis | None:
        if self._redis is not None:
            return self._redis
        try:
            client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=1)
            client.ping()
        except Exception as exc:
            if settings.app_env.lower() == "production":
                raise HTTPException(
                    status_code=503,
                    detail="admin session store unavailable",
                ) from exc
            return None
        self._redis = client
        return client

    @staticmethod
    def _key(session_id: str) -> str:
        digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        return f"admin-session:{digest}"


session_store = AdminSessionStore()
password_hasher = PasswordHasher()
_login_failures: dict[str, list[float]] = {}


def verify_admin_password(username: str, password: str, request: Request) -> bool:
    if username != settings.admin_username or not settings.admin_password_hash:
        _record_failure(username, request)
        return False
    if _rate_limited(username, request):
        raise HTTPException(status_code=429, detail="too many login attempts")
    try:
        ok = password_hasher.verify(settings.admin_password_hash, password)
    except VerifyMismatchError:
        ok = False
    except Exception:
        ok = False
    if not ok:
        _record_failure(username, request)
    return bool(ok)


def set_session_cookies(response: Response, session_id: str, csrf_token: str) -> None:
    signed = _sign_session_id(session_id)
    response.set_cookie(
        SESSION_COOKIE,
        signed,
        httponly=True,
        secure=settings.admin_secure_cookie,
        samesite="strict",
        max_age=settings.admin_session_ttl_seconds,
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        httponly=False,
        secure=settings.admin_secure_cookie,
        samesite="strict",
        max_age=settings.admin_session_ttl_seconds,
    )


def clear_session_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE)
    response.delete_cookie(CSRF_COOKIE)


def require_admin_session(request: Request) -> AdminPrincipal:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    session_id = _unsign_session_id(raw)
    if not session_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    data = session_store.get(session_id)
    if not data:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    return AdminPrincipal(
        subject=str(data["subject"]),
        session_id=session_id,
        csrf_token=str(data["csrf_token"]),
    )


def require_csrf(
    request: Request,
    x_csrf_token: str | None = Header(default=None, alias=CSRF_HEADER),
) -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    principal = require_admin_session(request)
    if not x_csrf_token or not hmac.compare_digest(x_csrf_token, principal.csrf_token):
        raise HTTPException(status_code=403, detail="invalid csrf token")


def request_id(request: Request) -> str:
    value = request.headers.get("x-request-id")
    return value[:128] if value else secrets.token_hex(12)


def ip_hash(request: Request) -> str | None:
    host = request.client.host if request.client else None
    if not host:
        return None
    salt = settings.admin_session_secret or "local"
    return hashlib.sha256(f"{salt}:{host}".encode()).hexdigest()


def _sign_session_id(session_id: str) -> str:
    secret = _session_secret()
    signature = hmac.new(
        secret.encode("utf-8"),
        session_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{session_id}.{signature}"


def _unsign_session_id(value: str) -> str | None:
    try:
        session_id, signature = value.rsplit(".", 1)
    except ValueError:
        return None
    expected = hmac.new(
        _session_secret().encode("utf-8"),
        session_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    return session_id


def _session_secret() -> str:
    if settings.admin_session_secret:
        return settings.admin_session_secret
    if settings.app_env.lower() == "production":
        raise HTTPException(status_code=503, detail="admin session secret is not configured")
    return "local-development-session-secret"


def _record_failure(username: str, request: Request) -> None:
    key = f"{username}:{ip_hash(request) or 'unknown'}"
    now = time.time()
    _login_failures.setdefault(key, []).append(now)
    _login_failures[key] = [item for item in _login_failures[key] if now - item < 300]


def _rate_limited(username: str, request: Request) -> bool:
    key = f"{username}:{ip_hash(request) or 'unknown'}"
    now = time.time()
    attempts = [item for item in _login_failures.get(key, []) if now - item < 300]
    _login_failures[key] = attempts
    return len(attempts) >= 5
