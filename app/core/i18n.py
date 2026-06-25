from __future__ import annotations

from typing import Any

DEFAULT_LANGUAGE = "zh-CN"

CATEGORY_LABELS_ZH = {
    "security": "????",
    "security_incident": "????",
    "hack_security": "????",
    "exploit": "????",
    "regulatory": "????",
    "policy_regulatory": "????",
    "listing": "?????",
    "delisting": "?????",
    "derivatives_listing": "????",
    "derivatives_delisting": "????",
    "wallet_maintenance": "????",
    "deposit_withdrawal": "????",
    "system_maintenance": "????",
    "trading_rule": "????",
    "product": "????",
    "project_update": "????",
    "token_unlock": "????",
    "exchange_repost": "?????",
    "protocol": "????",
    "governance": "????",
    "media": "????",
    "newsflash": "??",
    "deep_article": "????",
    "market": "????",
    "funding": "??",
    "fundraising": "??",
    "onchain": "????",
    "system": "????",
    "exchange": "???",
}
SEVERITY_LABELS_ZH = {
    "critical": "??",
    "high": "?",
    "medium": "?",
    "normal": "??",
    "low": "?",
}
STATUS_LABELS_ZH = {
    "new": "???",
    "needs_review": "???",
    "confirmed": "???",
    "triaged": "???",
    "ignored": "???",
    "acknowledged": "?????",
    "resolved": "???",
}

ERROR_MESSAGES_ZH = {
    "invalid username or password": "用户名或密码错误",
    "too many login attempts": "登录尝试过多，请稍后再试",
    "not authenticated": "未登录",
    "invalid csrf token": "CSRF 校验失败",
    "event not found": "事件不存在",
    "source not found": "数据源不存在",
    "destination not found": "通知目标不存在",
    "rule not found": "规则不存在",
    "delivery not found": "投递记录不存在",
    "unsupported sort": "不支持的排序字段",
    "field encryption is not configured": "字段加密未配置",
    "webhook_url is required": "Webhook URL 为必填项",
    "callback verification is not configured": "飞书回调验证未配置",
    "unsupported content type": "不支持的内容类型",
    "callback body too large": "回调请求体过大",
    "malformed callback payload": "回调数据格式错误",
    "invalid callback verification": "飞书回调验证失败",
}


def preferred_language(accept_language: str | None) -> str:
    if not accept_language:
        return DEFAULT_LANGUAGE
    lowered = accept_language.lower()
    if lowered.startswith("en") or ",en" in lowered:
        return "en-US"
    return DEFAULT_LANGUAGE


def translate_error(detail: Any, *, language: str) -> Any:
    if language.startswith("en"):
        return detail
    if isinstance(detail, str):
        return ERROR_MESSAGES_ZH.get(detail, detail)
    return detail


def category_label(value: str | None) -> str | None:
    if value is None:
        return None
    return CATEGORY_LABELS_ZH.get(value, value)


def severity_label(value: str | None) -> str | None:
    if value is None:
        return None
    return SEVERITY_LABELS_ZH.get(value, value)


def status_label(value: str | None) -> str | None:
    if value is None:
        return None
    return STATUS_LABELS_ZH.get(value, value)
