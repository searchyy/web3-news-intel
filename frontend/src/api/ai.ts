import type { ApiOptions } from "./client";

export class AiApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "AiApiError";
    this.status = status;
  }
}

export async function aiApi<T>(path: string, options: ApiOptions = {}): Promise<T> {
  const { csrf, authRedirect, ...requestOptions } = options;
  const headers = new Headers(requestOptions.headers);
  if (requestOptions.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (csrf && !headers.has("X-CSRF-Token")) {
    headers.set("X-CSRF-Token", csrf);
  }

  const response = await fetch(path, {
    ...requestOptions,
    headers,
    credentials: "include"
  });

  if (!response.ok) {
    if (response.status === 401 && authRedirect !== false && typeof window !== "undefined") {
      redirectToLogin();
      throw new AiApiError("Authentication required", response.status);
    }
    throw new AiApiError(await readErrorMessage(response), response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export function aiErrorMessage(error: unknown, fallback: string) {
  const message = error instanceof Error ? error.message : String(error || "");
  const normalized = message.toLowerCase();

  if (normalized.includes("daily token budget") || normalized.includes("token budget") || normalized.includes("ai_budget_exceeded")) {
    return "?? AI Token ???????? AI ?????????? Token ????? UTC 0 ?????";
  }
  if (normalized.includes("daily request budget") || normalized.includes("request budget")) {
    return "?? AI ?????????? AI ????????????????? UTC 0 ?????";
  }

  if (message.includes("FIELD_ENCRYPTION_KEY")) {
    return "后端缺少 FIELD_ENCRYPTION_KEY，无法加密保存 DeepSeek Key。请配置后端环境变量并重启服务后再保存。";
  }
  if (message.includes("DeepSeek API Key is not configured") || normalized.includes("api key is not configured")) {
    return "尚未配置 DeepSeek API Key，请先保存密钥后再获取模型或测试连接。";
  }
  if (normalized.includes("redis")) {
    return "Redis 不可用，AI 异步任务无法入队。请检查 Redis 服务后重试。";
  }
  if (normalized.includes("celery") || normalized.includes("worker")) {
    return "Celery Worker 未运行，AI 任务不会被消费。请启动 Worker 后重试。";
  }
  if (normalized.includes("ai is disabled") || normalized.includes("ai disabled")) {
    return "AI 功能未启用，请先在 AI 智能整理页面启用后再试。";
  }
  if (normalized.includes("insufficient input") || normalized.includes("input content")) {
    return "输入内容不足，无法生成可靠摘要。请等待来源补充摘要或正文摘录后再试。";
  }
  if (normalized.includes("timeout") || normalized.includes("timed out")) {
    return "AI 任务已超时，请稍后重试或检查 Worker 状态。";
  }
  if (message.includes("CSRF")) {
    return "登录校验已失效，请刷新页面后重试。";
  }
  if (!message || /^HTTP \d+$/.test(message)) {
    return fallback;
  }
  return message;
}

async function readErrorMessage(response: Response) {
  const text = await response.text();
  if (!text) {
    return `HTTP ${response.status}`;
  }

  try {
    const payload = JSON.parse(text) as { detail?: unknown; message?: unknown; error?: unknown };
    return extractMessage(payload.detail) || extractMessage(payload.message) || extractMessage(payload.error) || text;
  } catch {
    return text;
  }
}

function extractMessage(value: unknown): string | undefined {
  if (!value) {
    return undefined;
  }
  if (typeof value === "string") {
    return value;
  }
  if (Array.isArray(value)) {
    return value.map(extractMessage).filter(Boolean).join("；") || undefined;
  }
  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    return extractMessage(record.msg) || extractMessage(record.message) || JSON.stringify(value);
  }
  return String(value);
}

function redirectToLogin() {
  if (window.location.pathname === "/login") {
    return;
  }
  if (window.history?.pushState) {
    window.history.pushState({}, "", "/login");
    window.dispatchEvent(new PopStateEvent("popstate"));
    return;
  }
  window.location.assign("/login");
}
