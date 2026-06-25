import { Alert, Card, Col, Empty, Row, Skeleton, Space, Statistic, Table, Tag, Typography } from "antd";
import type { TableProps } from "antd";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";

type SystemHealthPayload = Record<string, unknown>;
type SystemQueuesPayload = unknown[] | Record<string, unknown>;
type SystemCanaryRunsPayload = unknown[] | Record<string, unknown>;

type ServiceStatusRow = {
  key: string;
  name: string;
  status: string;
  detail: string;
};

type QueueRow = {
  key: string;
  name: string;
  depth: number | null;
  status: string;
  workers: number | null;
  updatedAt: string | null;
};

type CanaryRunRow = {
  key: string;
  name: string;
  status: string;
  startedAt: string | null;
  finishedAt: string | null;
  durationMs: number | null;
  detail: string;
};

const coreServices = ["api", "postgresql", "redis", "celery"] as const;

const serviceLabels: Record<string, string> = {
  api: "API",
  postgresql: "PostgreSQL",
  postgres: "PostgreSQL",
  redis: "Redis",
  celery: "Celery"
};

const healthyStatuses = new Set(["ok", "healthy", "ready", "configured", "connected", "success", "up"]);
const warningStatuses = new Set(["degraded", "warning", "pending", "not_configured", "partial"]);
const errorStatuses = new Set(["failed", "error", "down", "unhealthy", "unavailable", "disconnected"]);
const runningStatuses = new Set(["running", "processing", "started", "active"]);

export function SystemPage() {
  const healthQuery = useQuery({
    queryKey: ["system-health"],
    queryFn: () => api<SystemHealthPayload>("/api/admin/system/health"),
    retry: false
  });
  const queuesQuery = useQuery({
    queryKey: ["system-queues"],
    queryFn: () => api<SystemQueuesPayload>("/api/admin/system/queues"),
    retry: false
  });
  const canaryRunsQuery = useQuery({
    queryKey: ["system-canary-runs"],
    queryFn: () => api<SystemCanaryRunsPayload>("/api/admin/system/canary-runs"),
    retry: false
  });

  const services = normalizeServiceRows(healthQuery.data);
  const queues = normalizeQueueRows(queuesQuery.data);
  const canaryRuns = normalizeCanaryRuns(canaryRunsQuery.data);
  const totalQueueDepth = queues.reduce((sum, queue) => sum + (queue.depth ?? 0), 0);
  const knownServices = services.filter((service) => service.status !== "unknown");
  const healthyServiceCount = services.filter((service) => isHealthyStatus(service.status)).length;
  const latestCanary = canaryRuns[0];
  const isInitialLoading = healthQuery.isLoading && queuesQuery.isLoading && canaryRunsQuery.isLoading;

  if (isInitialLoading) {
    return <Skeleton active />;
  }

  return (
    <Space direction="vertical" size={16} className="page-stack">
      <Typography.Title level={3}>系统状态</Typography.Title>

      <QueryAlerts
        healthError={healthQuery.isError}
        queuesError={queuesQuery.isError}
        canaryError={canaryRunsQuery.isError}
      />

      <Row gutter={[12, 12]}>
        <Metric title="健康服务" value={`${healthyServiceCount}/${knownServices.length || services.length}`} />
        <Metric title="队列积压" value={queues.length ? totalQueueDepth : "empty"} />
        <Metric title="Canary 最近运行" value={latestCanary?.status ?? "empty"} />
      </Row>

      <Card title="服务状态" loading={healthQuery.isFetching && !healthQuery.data}>
        <Table<ServiceStatusRow>
          rowKey="key"
          size="small"
          pagination={false}
          dataSource={services}
          columns={serviceColumns}
        />
      </Card>

      <Card title="队列状态" loading={queuesQuery.isFetching && !queuesQuery.data}>
        <Table<QueueRow>
          rowKey="key"
          size="small"
          pagination={false}
          dataSource={queues}
          columns={queueColumns}
          locale={{ emptyText: <Empty description="empty" /> }}
        />
      </Card>

      <Card title="Canary 运行记录" loading={canaryRunsQuery.isFetching && !canaryRunsQuery.data}>
        <Table<CanaryRunRow>
          rowKey="key"
          size="small"
          pagination={false}
          dataSource={canaryRuns}
          columns={canaryColumns}
          locale={{ emptyText: <Empty description="empty" /> }}
        />
      </Card>
    </Space>
  );
}

function Metric({ title, value }: { title: string; value: string | number }) {
  return (
    <Col xs={24} md={8}>
      <Card>
        <Statistic title={title} value={value} />
      </Card>
    </Col>
  );
}

function QueryAlerts({
  healthError,
  queuesError,
  canaryError
}: {
  healthError: boolean;
  queuesError: boolean;
  canaryError: boolean;
}) {
  const failed = [
    healthError ? "health" : null,
    queuesError ? "queues" : null,
    canaryError ? "canary-runs" : null
  ].filter(Boolean);

  if (!failed.length) {
    return null;
  }

  return (
    <Alert
      type="warning"
      showIcon
      message="部分系统状态暂不可用"
      description={`请求失败：${failed.join("、")}。页面已保留可用数据，缺失项显示为 unknown 或 empty。`}
    />
  );
}

const serviceColumns: TableProps<ServiceStatusRow>["columns"] = [
  { title: "服务", dataIndex: "name" },
  {
    title: "状态",
    dataIndex: "status",
    render: (value: string) => <StatusTag status={value} />
  },
  { title: "详情", dataIndex: "detail" }
];

const queueColumns: TableProps<QueueRow>["columns"] = [
  { title: "队列", dataIndex: "name" },
  {
    title: "Depth",
    dataIndex: "depth",
    render: (value: number | null) => formatNumber(value)
  },
  {
    title: "状态",
    dataIndex: "status",
    render: (value: string) => <StatusTag status={value} />
  },
  {
    title: "Workers",
    dataIndex: "workers",
    render: (value: number | null) => formatNumber(value)
  },
  { title: "更新时间", dataIndex: "updatedAt", render: formatTime }
];

const canaryColumns: TableProps<CanaryRunRow>["columns"] = [
  { title: "任务", dataIndex: "name" },
  {
    title: "状态",
    dataIndex: "status",
    render: (value: string) => <StatusTag status={value} />
  },
  { title: "开始时间", dataIndex: "startedAt", render: formatTime },
  { title: "结束时间", dataIndex: "finishedAt", render: formatTime },
  {
    title: "耗时 ms",
    dataIndex: "durationMs",
    render: (value: number | null) => formatNumber(value)
  },
  { title: "详情", dataIndex: "detail" }
];

function StatusTag({ status }: { status: string }) {
  return <Tag color={statusColor(status)}>{status || "unknown"}</Tag>;
}

function normalizeServiceRows(payload?: SystemHealthPayload): ServiceStatusRow[] {
  const rows = new Map<string, ServiceStatusRow>();
  const health = isRecord(payload) ? payload : {};

  for (const key of coreServices) {
    rows.set(key, serviceRowFromValue(key, health[key]));
  }

  for (const collection of [health.services, health.checks, health.components]) {
    for (const row of serviceRowsFromCollection(collection)) {
      rows.set(row.key, row);
    }
  }

  return [
    ...coreServices.map((key) => rows.get(key) ?? serviceRowFromValue(key, undefined)),
    ...Array.from(rows.values()).filter((row) => !coreServices.includes(row.key as (typeof coreServices)[number]))
  ];
}

function serviceRowsFromCollection(collection: unknown): ServiceStatusRow[] {
  if (Array.isArray(collection)) {
    return collection.map((item, index) => {
      const record = isRecord(item) ? item : {};
      const key = stringField(record, ["key", "name", "service", "component"]) ?? `service-${index + 1}`;
      return serviceRowFromValue(key, item);
    });
  }

  if (isRecord(collection)) {
    return Object.entries(collection).map(([key, value]) => serviceRowFromValue(key, value));
  }

  return [];
}

function serviceRowFromValue(key: string, value: unknown): ServiceStatusRow {
  const normalizedKey = key.toLowerCase();
  if (isRecord(value)) {
    const status = stringField(value, ["status", "health", "state", "result"]) ?? "unknown";
    const label = stringField(value, ["label", "display_name", "name"]) ?? serviceLabels[normalizedKey] ?? key;
    const detail = stringField(value, ["detail", "message", "error", "reason"]) ?? "unknown";
    return {
      key: normalizedKey,
      name: label,
      status,
      detail
    };
  }

  return {
    key: normalizedKey,
    name: serviceLabels[normalizedKey] ?? key,
    status: stringifyStatus(value),
    detail: "unknown"
  };
}

function normalizeQueueRows(payload?: SystemQueuesPayload): QueueRow[] {
  return collectionRows(payload, ["queues", "items", "data", "results"]).map((item, index) => {
    const record = isRecord(item) ? item : {};
    const name = stringField(record, ["name", "queue", "queue_name", "key"]) ?? `queue-${index + 1}`;
    return {
      key: stringField(record, ["id", "key", "name", "queue", "queue_name"]) ?? name,
      name,
      depth: numberField(record, ["depth", "pending", "pending_count", "queued", "queued_count", "length", "messages", "size"]),
      status: stringField(record, ["status", "health", "state"]) ?? "unknown",
      workers: numberField(record, ["workers", "worker_count", "consumers", "consumer_count"]),
      updatedAt: stringField(record, ["updated_at", "updatedAt", "checked_at", "checkedAt", "timestamp"]) ?? null
    };
  });
}

function normalizeCanaryRuns(payload?: SystemCanaryRunsPayload): CanaryRunRow[] {
  return collectionRows(payload, ["runs", "items", "data", "results"]).map((item, index) => {
    const record = isRecord(item) ? item : {};
    const name =
      stringField(record, ["name", "job_type", "task", "source", "source_key", "canary", "script"]) ??
      `canary-${index + 1}`;
    return {
      key: stringField(record, ["id", "run_id", "job_id", "task_id"]) ?? `${name}-${index}`,
      name,
      status: stringField(record, ["status", "result", "state"]) ?? "unknown",
      startedAt: stringField(record, ["started_at", "startedAt", "created_at", "createdAt", "run_at", "runAt"]) ?? null,
      finishedAt: stringField(record, ["finished_at", "finishedAt", "completed_at", "completedAt", "ended_at", "endedAt"]) ?? null,
      durationMs: numberField(record, ["duration_ms", "latency_ms", "elapsed_ms", "runtime_ms"]),
      detail: stringField(record, ["detail", "message", "error", "error_sanitized", "reason"]) ?? "unknown"
    };
  });
}

function collectionRows(payload: unknown, keys: string[]): unknown[] {
  if (Array.isArray(payload)) {
    return payload;
  }

  if (!isRecord(payload)) {
    return [];
  }

  for (const key of keys) {
    const value = payload[key];
    if (Array.isArray(value)) {
      return value;
    }
  }

  return [];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function stringField(record: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) {
      return value;
    }
    if (typeof value === "number" || typeof value === "boolean") {
      return String(value);
    }
  }
  return undefined;
}

function numberField(record: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "number" && Number.isFinite(value)) {
      return value;
    }
    if (typeof value === "string" && value.trim() && Number.isFinite(Number(value))) {
      return Number(value);
    }
  }
  return null;
}

function stringifyStatus(value: unknown) {
  if (typeof value === "string" && value.trim()) {
    return value;
  }
  if (typeof value === "boolean") {
    return value ? "ok" : "failed";
  }
  if (typeof value === "number") {
    return String(value);
  }
  return "unknown";
}

function statusColor(status: string) {
  const normalized = status.toLowerCase();
  if (healthyStatuses.has(normalized)) {
    return "success";
  }
  if (runningStatuses.has(normalized)) {
    return "processing";
  }
  if (warningStatuses.has(normalized)) {
    return "warning";
  }
  if (errorStatuses.has(normalized)) {
    return "error";
  }
  return "default";
}

function isHealthyStatus(status: string) {
  return healthyStatuses.has(status.toLowerCase());
}

function formatNumber(value: number | null) {
  return typeof value === "number" ? value : "unknown";
}

function formatTime(value?: string | null) {
  if (!value) {
    return "unknown";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("zh-CN", { hour12: false });
}
