from __future__ import annotations

import argparse
import json
import time
from typing import Any
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, Request

app = FastAPI(title="compose-external-mock")
_requests: list[dict[str, Any]] = []


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/__mock/reset")
def reset() -> dict[str, int]:
    _requests.clear()
    return {"requests": 0}


@app.get("/__mock/requests")
def requests() -> dict[str, Any]:
    return {"requests": _requests}


@app.get("/__mock/counts")
def counts() -> dict[str, int]:
    result: dict[str, int] = {}
    for item in _requests:
        kind = str(item["kind"])
        result[kind] = result.get(kind, 0) + 1
    result["total"] = len(_requests)
    return result


@app.get("/models")
async def list_models(request: Request) -> dict[str, Any]:
    await _record("deepseek.models", request)
    return {
        "object": "list",
        "data": [
            {
                "id": "deepseek-compose-mock",
                "object": "model",
                "owned_by": "compose-mock",
            }
        ],
    }


@app.post("/chat/completions")
async def chat_completions(request: Request) -> dict[str, Any]:
    body = await request.json()
    await _record("deepseek.chat_completions", request, body)
    content = {
        "headline_zh": "Mock AI：BTC 事件摘要",
        "summary_zh": "Mock DeepSeek 根据受控输入生成中文摘要，仅用于 Compose 验收。",
        "key_facts": [{"text": "输入事件已被 mock AI 处理"}],
        "entities": [{"name": "Binance", "type": "exchange"}],
        "symbols": ["BTC"],
        "chains": ["Bitcoin"],
        "event_type": "exchange_listing",
        "importance_score": 82,
        "risk_level": "medium",
        "sentiment": "neutral",
        "market_impact": "不确定，mock 结果不构成投资建议",
        "facts": [{"text": "事件标题包含 BTC"}],
        "inferences": [{"text": "这是受控测试推断"}],
        "confidence": 0.91,
        "source_event_ids": [],
        "source_urls": [],
    }
    return {
        "id": f"chatcmpl-{uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.get("model") or "deepseek-compose-mock",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": json.dumps(content, ensure_ascii=False),
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 64, "completion_tokens": 96, "total_tokens": 160},
    }


@app.post("/open-apis/auth/v3/tenant_access_token/internal")
async def tenant_access_token(request: Request) -> dict[str, Any]:
    body = await request.json()
    await _record("feishu.tenant_token", request, body)
    return {
        "code": 0,
        "msg": "ok",
        "tenant_access_token": "mock-tenant-access-token",
        "expire": 7200,
    }


@app.post("/open-apis/im/v1/messages")
async def send_message(request: Request) -> dict[str, Any]:
    body = await request.json()
    await _record("feishu.send_message", request, body)
    message_id = f"om_{uuid4().hex[:24]}"
    return {
        "code": 0,
        "msg": "ok",
        "data": {
            "message_id": message_id,
            "message": {"message_id": message_id},
        },
    }


@app.post("/open-apis/im/v1/messages/{message_id}/update")
async def update_message(message_id: str, request: Request) -> dict[str, Any]:
    body = await request.json()
    await _record("feishu.update_message", request, {"message_id": message_id, "body": body})
    return {"code": 0, "msg": "ok", "data": {"message_id": message_id}}


async def _record(
    kind: str, request: Request, payload: dict[str, Any] | None = None
) -> None:
    headers = {
        key.lower(): ("[redacted]" if key.lower() in {"authorization", "cookie"} else value)
        for key, value in request.headers.items()
        if key.lower() in {"authorization", "content-type", "user-agent", "cookie"}
    }
    _requests.append(
        {
            "kind": kind,
            "method": request.method,
            "path": request.url.path,
            "query": str(request.url.query),
            "headers": headers,
            "payload": payload or {},
            "received_at": time.time(),
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9000)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
