import { api } from "./client";

export type PipelineStage = "fetch" | "parse" | "event" | "ai" | "feishu";

export type EventPipelineStatus =
  | "queued"
  | "started"
  | "retrying"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "sending"
  | "delivered"
  | "dry_run"
  | "suppressed";

export type EventPipelineRawItem = Record<string, unknown>;

export type EventPipelineDelivery = {
  id?: string | number;
  delivery_id?: string | number;
  destination_name?: string | null;
  destination_key?: string | null;
  status?: string | null;
  dry_run?: boolean | null;
  channel?: string | null;
  target?: string | null;
  attempts?: number | null;
  response_status?: number | null;
  provider_message_id?: string | null;
  delivered_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  last_error?: string | null;
  error_message_sanitized?: string | null;
  suppressed_reason?: string | null;
};

export type EventPipelineResponse = {
  event_id?: number;
  timeline?: EventPipelineRawItem[];
  stages?: EventPipelineRawItem[];
  source?: EventPipelineRawItem | null;
  fetch?: EventPipelineRawItem | EventPipelineRawItem[] | null;
  parse?: EventPipelineRawItem | EventPipelineRawItem[] | null;
  event?: EventPipelineRawItem | EventPipelineRawItem[] | null;
  ai?: EventPipelineRawItem | EventPipelineRawItem[] | null;
  ai_jobs?: EventPipelineRawItem[];
  feishu?: EventPipelineRawItem | EventPipelineRawItem[] | null;
  delivery?: EventPipelineDelivery | null;
  deliveries?: EventPipelineDelivery[];
  card_preview?: unknown;
  preview?: unknown;
  card?: unknown;
};

export type NormalizedPipelineItem = {
  id: string;
  stage: PipelineStage;
  stageLabel: string;
  status: EventPipelineStatus;
  statusLabel: string;
  title: string;
  description?: string;
  time?: string;
  error?: string;
  retryCount?: number;
  deliveryId?: string | number;
  jobId?: string;
};

export type NormalizedEventPipeline = {
  eventId?: number;
  items: NormalizedPipelineItem[];
  delivery?: EventPipelineDelivery;
  cardPreview?: unknown;
};

const STAGE_LABELS: Record<PipelineStage, string> = {
  fetch: "抓取",
  parse: "解析",
  event: "事件",
  ai: "AI",
  feishu: "飞书"
};

const GENERIC_STATUS_LABELS: Record<EventPipelineStatus, string> = {
  queued: "排队中",
  started: "处理中",
  retrying: "重试中",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
  sending: "发送中",
  delivered: "已送达",
  dry_run: "未实发",
  suppressed: "已抑制"
};

const AI_STATUS_LABELS: Partial<Record<EventPipelineStatus, string>> = {
  queued: "排队中",
  started: "生成中",
  retrying: "重试中",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消"
};

const FEISHU_STATUS_LABELS: Partial<Record<EventPipelineStatus, string>> = {
  queued: "待发送",
  sending: "发送中",
  delivered: "已送达",
  failed: "发送失败",
  dry_run: "未实发",
  suppressed: "已抑制"
};

const SENSITIVE_KEY_PATTERN =
  /(secret|token|api[_-]?key|authorization|password|credential|webhook|encrypt|verification)/i;
const SENSITIVE_VALUE_PATTERN =
  /(sk-[A-Za-z0-9_-]{8,}|https?:\/\/[^\s"]*(?:webhook|hook|bot)[^\s"]*)/i;

export function getEventPipeline(eventId: number) {
  return api<EventPipelineResponse>(
    `/api/admin/events/${encodeURIComponent(String(eventId))}/pipeline`
  );
}

export function normalizeEventPipeline(
  payload?: EventPipelineResponse | null
): NormalizedEventPipeline {
  if (!payload) {
    return { items: [] };
  }
  const timelineItems = collection(payload.timeline ?? payload.stages);
  const rawItems = timelineItems.length ? timelineItems : buildItemsFromSections(payload);
  const items = rawItems.map((item, index) => normalizePipelineItem(item, index));
  return {
    eventId: payload.event_id,
    items,
    delivery: normalizeDelivery(payload),
    cardPreview: sanitizePipelinePreview(payload.card_preview ?? payload.preview ?? payload.card)
  };
}

export function pipelineStatusText(stage: PipelineStage, status: string | undefined | null) {
  const normalized = normalizePipelineStatus(stage, status);
  if (stage === "ai") {
    return AI_STATUS_LABELS[normalized] ?? GENERIC_STATUS_LABELS[normalized];
  }
  if (stage === "feishu") {
    return FEISHU_STATUS_LABELS[normalized] ?? GENERIC_STATUS_LABELS[normalized];
  }
  return GENERIC_STATUS_LABELS[normalized];
}

export function pipelineStatusColor(
  status: string | undefined | null,
  stage: PipelineStage = "event"
) {
  const normalized = normalizePipelineStatus(stage, status);
  const color: Record<EventPipelineStatus, string> = {
    queued: "default",
    started: "processing",
    retrying: "warning",
    succeeded: "success",
    failed: "error",
    cancelled: "default",
    sending: "processing",
    delivered: "success",
    dry_run: "warning",
    suppressed: "default"
  };
  return color[normalized];
}

export function pipelineTimelineColor(
  status: string | undefined | null,
  stage: PipelineStage = "event"
) {
  const normalized = normalizePipelineStatus(stage, status);
  if (normalized === "failed") return "red";
  if (normalized === "succeeded" || normalized === "delivered") return "green";
  if (normalized === "retrying" || normalized === "dry_run") return "orange";
  if (normalized === "cancelled" || normalized === "suppressed") return "gray";
  return "blue";
}

export function normalizePipelineStatus(
  stage: PipelineStage,
  rawStatus?: string | null,
  raw?: EventPipelineRawItem
): EventPipelineStatus {
  const status = String(rawStatus ?? raw?.status ?? "").trim().toLowerCase();
  if (stage === "feishu") {
    if (raw?.dry_run === true || status === "dry_run" || status === "dry-run") return "dry_run";
    if (["suppressed", "skipped", "muted", "empty", "not_routed"].includes(status)) {
      return "suppressed";
    }
    if (["delivered", "sent", "success", "succeeded", "completed", "ok"].includes(status)) {
      return "delivered";
    }
    if (["sending", "started", "running", "processing", "in_progress"].includes(status)) {
      return "sending";
    }
    if (["failed", "failure", "error"].includes(status)) return "failed";
    return "queued";
  }
  if (stage === "ai") {
    if (["started", "running", "processing", "in_progress"].includes(status)) return "started";
    if (status === "retrying") return "retrying";
    if (["succeeded", "success", "completed", "delivered", "ok"].includes(status)) {
      return "succeeded";
    }
    if (["failed", "failure", "error", "skipped"].includes(status)) return "failed";
    if (["cancelled", "canceled"].includes(status)) return "cancelled";
    return "queued";
  }
  if (["failed", "failure", "error", "network_failed", "parse_failed"].includes(status)) {
    return "failed";
  }
  if (["started", "running", "processing", "in_progress", "fetching"].includes(status)) {
    return "started";
  }
  if (["cancelled", "canceled"].includes(status)) return "cancelled";
  if (["suppressed", "skipped"].includes(status)) return "suppressed";
  if (["queued", "pending", "new"].includes(status)) return "queued";
  return "succeeded";
}

export function formatPipelinePreview(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return redactSensitiveText(value);
  try {
    return JSON.stringify(sanitizePipelinePreview(value), null, 2);
  } catch {
    return "[无法预览]";
  }
}

export function sanitizePipelinePreview(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.slice(0, 20).map((item) => sanitizePipelinePreview(item));
  }
  if (value && typeof value === "object") {
    const result: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
      if (SENSITIVE_KEY_PATTERN.test(key)) {
        result[key] = "已隐藏";
      } else {
        result[key] = sanitizePipelinePreview(item);
      }
    }
    return result;
  }
  if (typeof value === "string") {
    return redactSensitiveText(value);
  }
  return value;
}

function buildItemsFromSections(payload: EventPipelineResponse): EventPipelineRawItem[] {
  const items: EventPipelineRawItem[] = [];
  pushSection(items, "fetch", payload.source ?? payload.fetch);
  pushSection(items, "parse", payload.parse);
  pushSection(items, "event", payload.event);
  pushSection(items, "ai", payload.ai_jobs?.length ? payload.ai_jobs : payload.ai);
  pushSection(items, "feishu", payload.deliveries?.length ? payload.deliveries : payload.feishu);
  return items;
}

function pushSection(
  target: EventPipelineRawItem[],
  stage: PipelineStage,
  section?: EventPipelineRawItem | EventPipelineRawItem[] | null
) {
  for (const item of collection(section)) {
    target.push({ ...item, stage });
  }
}

function normalizePipelineItem(item: EventPipelineRawItem, index: number): NormalizedPipelineItem {
  const stage = normalizeStage(item.stage);
  const status = normalizePipelineStatus(stage, stringValue(item.status), item);
  const deliveryId = item.delivery_id ?? item.id;
  const jobId = item.job_id ?? item.task_id;
  return {
    id: `${stage}-${deliveryId ?? jobId ?? index}`,
    stage,
    stageLabel: STAGE_LABELS[stage],
    status,
    statusLabel: pipelineStatusText(stage, status),
    title: normalizeTitle(stage, item),
    description: normalizeDescription(stage, item),
    time: stringValue(item.finished_at ?? item.delivered_at ?? item.generated_at ?? item.created_at),
    error: stringValue(item.error_message_sanitized ?? item.last_error ?? item.error),
    retryCount: numberValue(item.retry_count ?? item.attempts),
    deliveryId: typeof deliveryId === "string" || typeof deliveryId === "number" ? deliveryId : undefined,
    jobId: typeof jobId === "string" || typeof jobId === "number" ? String(jobId) : undefined
  };
}

function normalizeStage(value: unknown): PipelineStage {
  const stage = String(value ?? "").toLowerCase();
  if (stage.includes("fetch") || stage.includes("source")) return "fetch";
  if (stage.includes("parse")) return "parse";
  if (stage.includes("ai")) return "ai";
  if (stage.includes("feishu") || stage.includes("delivery")) return "feishu";
  return "event";
}

function normalizeTitle(stage: PipelineStage, item: EventPipelineRawItem) {
  const provided = stringValue(item.title ?? item.name);
  if (provided) return provided;
  if (stage === "fetch") return "消息源抓取";
  if (stage === "parse") return "内容解析";
  if (stage === "ai") return `AI 整理${item.job_id ? ` #${String(item.job_id)}` : ""}`;
  if (stage === "feishu") return `飞书投递${item.delivery_id ? ` #${String(item.delivery_id)}` : ""}`;
  return "事件入库";
}

function normalizeDescription(stage: PipelineStage, item: EventPipelineRawItem) {
  if (stage === "fetch") {
    return compactParts([
      stringValue(item.source_name ?? item.source_key),
      numberValue(item.total_duration_ms) ? `总耗时 ${numberValue(item.total_duration_ms)}ms` : undefined,
      numberValue(item.http_status) ? `HTTP ${numberValue(item.http_status)}` : undefined
    ]);
  }
  if (stage === "ai") {
    return compactParts([
      stringValue(item.input_quality) ? `输入质量 ${stringValue(item.input_quality)}` : undefined,
      numberValue(item.queue_wait_ms) ? `排队 ${numberValue(item.queue_wait_ms)}ms` : undefined,
      numberValue(item.provider_latency_ms) ? `模型 ${numberValue(item.provider_latency_ms)}ms` : undefined,
      stringValue(item.model) ? `模型 ${stringValue(item.model)}` : undefined
    ]);
  }
  if (stage === "feishu") {
    return compactParts([
      stringValue(item.destination_name ?? item.destination_key ?? item.target),
      numberValue(item.attempts) ? `尝试 ${numberValue(item.attempts)} 次` : undefined,
      numberValue(item.response_status) ? `HTTP ${numberValue(item.response_status)}` : undefined
    ]);
  }
  return compactParts([
    stringValue(item.event_key),
    numberValue(item.confirmation_count) ? `确认 ${numberValue(item.confirmation_count)} 次` : undefined
  ]);
}

function normalizeDelivery(payload: EventPipelineResponse): EventPipelineDelivery | undefined {
  const delivery = payload.delivery ?? payload.deliveries?.[0];
  if (!delivery) return undefined;
  const sanitized = sanitizePipelinePreview(delivery) as EventPipelineDelivery;
  return {
    ...sanitized,
    status: normalizePipelineStatus("feishu", sanitized.status, sanitized as EventPipelineRawItem)
  };
}

function collection<T>(value?: T | T[] | null): T[] {
  if (!value) return [];
  return Array.isArray(value) ? value : [value];
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function numberValue(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function compactParts(parts: Array<string | undefined>) {
  return parts.filter(Boolean).join(" · ") || undefined;
}

export function redactSensitiveText(value: string) {
  if (SENSITIVE_VALUE_PATTERN.test(value)) {
    return value.replace(SENSITIVE_VALUE_PATTERN, "已隐藏");
  }
  return value;
}
