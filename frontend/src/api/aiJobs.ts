import type {
  AiJob,
  AiJobStatus,
  AiRuntimeStatus,
  AiSummarySubmitResponse,
  EventAiInsight,
  InputQuality
} from "../types/api";
import { aiApi, aiErrorMessage } from "./ai";
import type { ApiOptions } from "./client";

export const AI_JOB_TIMEOUT_MS = 90_000;

const TERMINAL_STATUSES: AiJobStatus[] = ["succeeded", "failed", "cancelled"];

export function requestEventAiSummary(eventId: number, csrf?: string | null) {
  return aiApi<AiSummarySubmitResponse>(`/api/admin/events/${eventId}/ai-summary`, {
    method: "POST",
    csrf
  });
}

export function requestBatchAiSummary(eventIds: number[], csrf?: string | null) {
  return aiApi<AiSummarySubmitResponse>("/api/admin/events/ai-summary-batch", {
    method: "POST",
    csrf,
    body: JSON.stringify({ event_ids: eventIds })
  });
}

export function getAiJob(jobId: string, options: ApiOptions = {}) {
  return aiApi<AiJob>(`/api/admin/ai/jobs/${encodeURIComponent(jobId)}`, options);
}

export function retryAiJob(jobId: string, csrf?: string | null) {
  return aiApi<AiJob>(`/api/admin/ai/jobs/${encodeURIComponent(jobId)}/retry`, {
    method: "POST",
    csrf
  });
}

export function cancelAiJob(jobId: string, csrf?: string | null) {
  return aiApi<AiJob>(`/api/admin/ai/jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: "POST",
    csrf
  });
}

export function getAiRuntimeStatus() {
  return aiApi<AiRuntimeStatus>("/api/admin/system/ai-runtime");
}

export function isAiInsightResponse(value: AiSummarySubmitResponse): value is EventAiInsight {
  return typeof (value as EventAiInsight).summary_zh === "string" || typeof (value as EventAiInsight).headline_zh === "string";
}

export function normalizeAiJobFromSubmitResponse(value: AiSummarySubmitResponse): AiJob | undefined {
  if (isAiInsightResponse(value)) {
    return undefined;
  }

  const rawJobId = value.job_id ?? value.id ?? value.task_id;
  if (!rawJobId) {
    return undefined;
  }
  const jobId = String(rawJobId);

  return {
    ...value,
    id: value.id ?? jobId,
    job_id: jobId,
    status: normalizeAiJobStatus(value.status ?? (value.queued ? "queued" : "queued"))
  };
}

export function normalizeAiJobStatus(value?: string | null): AiJobStatus {
  if (value === "started" || value === "running") {
    return "started";
  }
  if (value === "retrying") {
    return "retrying";
  }
  if (value === "succeeded" || value === "success" || value === "completed") {
    return "succeeded";
  }
  if (value === "failed" || value === "failure" || value === "error") {
    return "failed";
  }
  if (value === "cancelled" || value === "canceled") {
    return "cancelled";
  }
  return "queued";
}

export function isTerminalAiJobStatus(status?: string | null) {
  return TERMINAL_STATUSES.includes(normalizeAiJobStatus(status));
}

export function aiJobPollInterval(
  createdAtMs: number | undefined,
  status: string | undefined,
  visible: boolean,
  nowMs = Date.now()
) {
  if (!visible || !createdAtMs || isTerminalAiJobStatus(status)) {
    return false;
  }
  const elapsedMs = Math.max(0, nowMs - createdAtMs);
  if (elapsedMs >= AI_JOB_TIMEOUT_MS) {
    return false;
  }
  if (elapsedMs < 10_000) {
    return 1_000;
  }
  if (elapsedMs < 30_000) {
    return 2_000;
  }
  return 5_000;
}

export function aiJobStatusText(status?: string | null) {
  const normalized = normalizeAiJobStatus(status);
  const text: Record<AiJobStatus, string> = {
    queued: "排队中",
    started: "生成中",
    retrying: "重试中",
    succeeded: "已完成",
    failed: "失败",
    cancelled: "已取消"
  };
  return text[normalized];
}

export function aiJobStatusColor(status?: string | null) {
  const normalized = normalizeAiJobStatus(status);
  const color: Record<AiJobStatus, string> = {
    queued: "default",
    started: "processing",
    retrying: "warning",
    succeeded: "success",
    failed: "error",
    cancelled: "default"
  };
  return color[normalized];
}

export function inputQualityText(value?: string | null) {
  const normalized = normalizeInputQuality(value);
  const text: Record<InputQuality, string> = {
    title_only: "仅标题",
    summary: "标题和摘要",
    excerpt: "含正文摘录",
    multi_source: "多来源摘录"
  };
  return normalized ? text[normalized] : undefined;
}

export function shouldWarnInputQuality(value?: string | null) {
  return normalizeInputQuality(value) === "title_only";
}

export function aiJobErrorMessage(error: unknown) {
  return aiErrorMessage(error, "AI 整理失败，请稍后重试");
}

export function normalizeAiJobErrorMessage(message?: string | null) {
  if (!message) {
    return undefined;
  }
  const lower = message.toLowerCase();

  if (lower.includes("daily token budget") || lower.includes("token budget") || lower.includes("ai_budget_exceeded")) {
    return "?? AI Token ???????? AI ?????????? Token ????? UTC 0 ?????";
  }
  if (lower.includes("daily request budget") || lower.includes("request budget")) {
    return "?? AI ?????????? AI ????????????????? UTC 0 ?????";
  }
  if (lower.includes("redis")) {
    return "Redis 不可用，AI 异步任务无法入队。请检查 Redis 服务后重试。";
  }
  if (lower.includes("celery") || lower.includes("worker")) {
    return "Celery Worker 未运行，AI 任务不会被消费。请启动 Worker 后重试。";
  }
  if (lower.includes("deepseek") && (lower.includes("not configured") || lower.includes("api key"))) {
    return "DeepSeek 未配置，请先到 AI 智能整理页面保存密钥和模型。";
  }
  if (lower.includes("timeout") || lower.includes("timed out")) {
    return "AI 任务已超时，请稍后重试或检查 Worker 状态。";
  }
  return message;
}

function normalizeInputQuality(value?: string | null): InputQuality | undefined {
  if (value === "title_only" || value === "summary" || value === "excerpt" || value === "multi_source") {
    return value;
  }
  return undefined;
}
