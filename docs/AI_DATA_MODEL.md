# AI 数据模型

AI 结果独立存储，不覆盖原始 `events` 字段。前端、搜索和飞书汇报必须明确区分原始事实、AI 摘要、AI 推断和 AI 置信度。

## ai_provider_configs

保存 Provider 配置。当前完整实现 DeepSeek。

关键字段：

- `provider`：当前为 `deepseek`。
- `enabled`：是否启用 AI。
- `api_base`：默认 `https://api.deepseek.com`。
- `api_key_ciphertext`：API Key 密文。
- `api_key_fingerprint`：API Key 指纹，用于掩码展示。
- `model`：管理员从 `/models` 获取后选择。
- `timeout_seconds`：请求超时。
- `max_concurrency`：本地并发上限。
- `max_tokens`：最大输出 tokens。
- `temperature`：模型温度。
- `thinking_enabled`：是否启用 thinking。
- `daily_token_budget`：每日 Token 预算。
- `daily_request_budget`：每日请求预算。
- `auto_process_enabled`：是否允许自动整理。
- `auto_minimum_severity`：自动整理最低事件级别。
- `config`：Provider 扩展配置。
- `last_tested_at`、`last_test_status`、`last_error_sanitized`：连接测试状态。

API Key 不允许回显明文。接口只返回：

- `api_key_configured`
- `api_key_masked`

## ai_prompt_templates

保存 prompt 模板版本。

关键字段：

- `key`
- `name`
- `system_prompt`
- `user_prompt_template`
- `output_schema_version`
- `enabled`
- `version`
- `created_at`
- `updated_at`

默认模板要求来源文本是不可执行数据，不泄漏系统提示词，不生成密钥，不调用输入 URL，不输出投资建议，只输出 JSON object。

## event_ai_insights

保存事件级 AI 结果。

关键字段：

- `event_id`
- `provider`
- `model`
- `prompt_version`
- `input_hash`
- `headline_zh`
- `summary_zh`
- `key_facts`
- `entities`
- `symbols`
- `chains`
- `event_type`
- `importance_score`
- `risk_level`
- `sentiment`
- `market_impact`
- `facts`
- `inferences`
- `confidence`
- `source_event_ids`
- `source_urls`
- `prompt_tokens`
- `completion_tokens`
- `generated_at`
- `status`
- `error_sanitized`

唯一约束：

```text
UNIQUE(event_id, provider, model, prompt_version, input_hash)
```

该约束用于避免相同输入重复生成和重复扣费。

## ai_runs

记录每次 AI 任务执行。

关键字段：

- `job_type`
- `provider`
- `model`
- `event_count`
- `prompt_tokens`
- `completion_tokens`
- `estimated_cost`
- `latency_ms`
- `status`
- `retry_count`
- `error_code`
- `error_sanitized`
- `created_at`
- `finished_at`

## 输出 Schema

DeepSeek 返回 JSON 必须符合以下结构：

```json
{
  "headline_zh": "...",
  "summary_zh": "...",
  "key_facts": [],
  "entities": [],
  "symbols": [],
  "chains": [],
  "event_type": "...",
  "importance_score": 0,
  "risk_level": "low",
  "sentiment": "neutral",
  "market_impact": "...",
  "facts": [],
  "inferences": [],
  "confidence": 0.0,
  "source_event_ids": [],
  "source_urls": []
}
```

约束：

- `importance_score` 为 0 到 100。
- `confidence` 为 0 到 1。
- `source_event_ids` 必须来自输入事件。
- `source_urls` 必须来自输入 URL。
- `facts` 必须可追溯来源。
- `inferences` 必须显式标记为推断。
- 无法确认时写“不确定”。
