import {
  Alert,
  Button,
  Card,
  Descriptions,
  Divider,
  Form,
  Input,
  InputNumber,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message
} from "antd";
import { ApiOutlined, DeleteOutlined, ReloadOutlined, RobotOutlined, SaveOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo } from "react";
import { api } from "../api/client";
import { normalizePaginated } from "../api/pagination";
import { useAuth } from "../auth/AuthContext";
import type { AiModelInfo, AiModelsResponse, AiProviderConfig, AiRun, PaginatedResponse } from "../types/api";

const DEEPSEEK_API_BASE = "https://api.deepseek.com";

type AiConfigForm = {
  enabled: boolean;
  auto_process_enabled: boolean;
  api_key?: string;
  model?: string;
  max_tokens?: number;
  timeout_seconds?: number;
  max_concurrency?: number;
  daily_token_budget?: number;
  daily_request_budget?: number;
  auto_minimum_severity?: string;
  thinking_enabled?: boolean;
};

const severityOptions = [
  { value: "low", label: "低" },
  { value: "normal", label: "普通" },
  { value: "medium", label: "中" },
  { value: "high", label: "高" },
  { value: "critical", label: "严重" }
];

const statusText: Record<string, string> = {
  success: "连接成功",
  failed: "连接失败",
  not_tested: "未测试",
  passed: "连接成功",
  error: "连接失败"
};

const statusColor: Record<string, string> = {
  success: "green",
  passed: "green",
  failed: "red",
  error: "red",
  not_tested: "gold"
};

export function AiSettingsPage() {
  const [form] = Form.useForm<AiConfigForm>();
  const { csrf } = useAuth();
  const queryClient = useQueryClient();

  const configQuery = useQuery({
    queryKey: ["ai-provider", "deepseek"],
    queryFn: () => api<AiProviderConfig>("/api/admin/ai/providers/deepseek"),
    retry: false,
    staleTime: 30_000
  });

  const modelsQuery = useQuery({
    queryKey: ["ai-provider", "deepseek", "models"],
    queryFn: () => api<AiModelsResponse | string[]>("/api/admin/ai/providers/deepseek/models"),
    enabled: false,
    retry: false,
    staleTime: 600_000
  });

  const runsQuery = useQuery({
    queryKey: ["ai-runs", 1],
    queryFn: async () => {
      const payload = await api<AiRun[] | PaginatedResponse<AiRun>>("/api/admin/ai/runs?page=1&page_size=10");
      return normalizePaginated(payload, 1, 10);
    },
    retry: false,
    staleTime: 30_000
  });

  useEffect(() => {
    const data = configQuery.data;
    if (!data) {
      return;
    }
    form.setFieldsValue({
      enabled: data.enabled,
      auto_process_enabled: data.auto_process_enabled,
      model: data.model ?? undefined,
      max_tokens: data.max_tokens ?? 1200,
      timeout_seconds: data.timeout_seconds ?? 90,
      max_concurrency: data.max_concurrency ?? 2,
      daily_token_budget: data.daily_token_budget ?? 0,
      daily_request_budget: data.daily_request_budget ?? 0,
      auto_minimum_severity: data.auto_minimum_severity ?? "high",
      thinking_enabled: data.thinking_enabled ?? false
    });
  }, [configQuery.data, form]);

  const modelOptions = useMemo(() => normalizeModels(modelsQuery.data), [modelsQuery.data]);
  const configured = Boolean(configQuery.data?.configured || configQuery.data?.api_key_masked);
  const lastStatus = configQuery.data?.last_test_status || "not_tested";
  const usage = configQuery.data?.usage_today ?? {};

  const saveConfig = useMutation({
    mutationFn: (values: AiConfigForm) => {
      const body: Record<string, unknown> = {
        provider: "deepseek",
        enabled: Boolean(values.enabled),
        auto_process_enabled: Boolean(values.auto_process_enabled),
        api_base: DEEPSEEK_API_BASE,
        model: values.model,
        max_tokens: values.max_tokens,
        timeout_seconds: values.timeout_seconds,
        max_concurrency: values.max_concurrency,
        daily_token_budget: values.daily_token_budget,
        daily_request_budget: values.daily_request_budget,
        auto_minimum_severity: values.auto_minimum_severity,
        thinking_enabled: Boolean(values.thinking_enabled)
      };
      if (values.api_key?.trim()) {
        body.api_key = values.api_key.trim();
      }
      return api<AiProviderConfig>("/api/admin/ai/providers/deepseek", {
        method: "PUT",
        csrf,
        body: JSON.stringify(body)
      });
    },
    onSuccess: () => {
      message.success("AI 智能整理配置已保存，API Key 不会明文回显");
      form.setFieldValue("api_key", undefined);
      queryClient.invalidateQueries({ queryKey: ["ai-provider", "deepseek"] });
    }
  });

  const deleteKey = useMutation({
    mutationFn: () =>
      api("/api/admin/ai/providers/deepseek/key", {
        method: "DELETE",
        csrf
      }),
    onSuccess: () => {
      message.success("DeepSeek API Key 已删除");
      form.setFieldValue("api_key", undefined);
      queryClient.invalidateQueries({ queryKey: ["ai-provider", "deepseek"] });
    }
  });

  const testConnection = useMutation({
    mutationFn: () =>
      api("/api/admin/ai/providers/deepseek/test", {
        method: "POST",
        csrf
      }),
    onSuccess: () => {
      message.success("测试连接已完成，结果已刷新");
      queryClient.invalidateQueries({ queryKey: ["ai-provider", "deepseek"] });
    },
    onError: () => {
      message.error("测试连接失败，请检查密钥、模型和预算设置");
      queryClient.invalidateQueries({ queryKey: ["ai-provider", "deepseek"] });
    }
  });

  return (
    <Space direction="vertical" size={16} className="page-stack">
      <Space className="page-title-row" wrap>
        <Typography.Title level={3}>AI 智能整理</Typography.Title>
        <Tag color="blue">Provider：DeepSeek</Tag>
        <Tag color={configured ? "green" : "gold"}>{configured ? "已配置密钥" : "未配置密钥"}</Tag>
      </Space>

      {configQuery.isError ? (
        <Alert
          type="warning"
          showIcon
          message="AI 后端接口暂不可用"
          description="页面已保留配置入口，后端完成 /api/admin/ai/providers/deepseek 后即可联调。"
        />
      ) : null}

      <Card
        title="DeepSeek 配置"
        loading={configQuery.isLoading}
        extra={<Tag color={statusColor[lastStatus] ?? "default"}>{statusText[lastStatus] ?? lastStatus}</Tag>}
      >
        <Form form={form} layout="vertical" onFinish={(values) => saveConfig.mutate(values)}>
          <Space size="large" wrap>
            <Form.Item label="启用 AI" name="enabled" valuePropName="checked" initialValue={false}>
              <Switch checkedChildren="启用" unCheckedChildren="停用" />
            </Form.Item>
            <Form.Item label="启用自动整理" name="auto_process_enabled" valuePropName="checked" initialValue={false}>
              <Switch checkedChildren="自动" unCheckedChildren="手动" />
            </Form.Item>
            <Form.Item label="是否启用 thinking" name="thinking_enabled" valuePropName="checked" initialValue={false}>
              <Switch checkedChildren="启用" unCheckedChildren="关闭" />
            </Form.Item>
          </Space>

          <Form.Item label="API Base">
            <Input value={DEEPSEEK_API_BASE} readOnly />
          </Form.Item>

          <Form.Item label="API Key（只写）" name="api_key">
            <Input.Password
              autoComplete="new-password"
              placeholder={configQuery.data?.api_key_masked || "sk-..."}
            />
          </Form.Item>

          <Space wrap align="end">
            <Form.Item label="模型" name="model" className="ai-model-select">
              <Select
                showSearch
                placeholder="请先获取模型列表"
                options={modelOptions.length ? modelOptions : modelFallback(configQuery.data?.model)}
              />
            </Form.Item>
            <Button
              icon={<ReloadOutlined />}
              onClick={() => void modelsQuery.refetch()}
              loading={modelsQuery.isFetching}
            >
              获取模型列表
            </Button>
          </Space>

          <Space wrap>
            <Form.Item label="最大输出 Tokens" name="max_tokens">
              <InputNumber min={128} max={16_000} step={128} />
            </Form.Item>
            <Form.Item label="超时时间（秒）" name="timeout_seconds">
              <InputNumber min={10} max={300} />
            </Form.Item>
            <Form.Item label="最大并发" name="max_concurrency">
              <InputNumber min={1} max={10} />
            </Form.Item>
            <Form.Item label="每日 Token 预算" name="daily_token_budget">
              <InputNumber min={0} step={1000} />
            </Form.Item>
            <Form.Item label="每日请求预算" name="daily_request_budget">
              <InputNumber min={0} step={10} />
            </Form.Item>
            <Form.Item label="自动处理最低事件级别" name="auto_minimum_severity">
              <Select options={severityOptions} className="ai-severity-select" />
            </Form.Item>
          </Space>

          <Space wrap>
            <Button type="primary" htmlType="submit" icon={<SaveOutlined />} loading={saveConfig.isPending}>
              保存配置
            </Button>
            <Button icon={<ApiOutlined />} onClick={() => testConnection.mutate()} loading={testConnection.isPending}>
              测试连接
            </Button>
            <Button danger icon={<DeleteOutlined />} onClick={() => deleteKey.mutate()} loading={deleteKey.isPending}>
              删除密钥
            </Button>
          </Space>
        </Form>

        <Divider />
        <Descriptions bordered size="small" column={{ xs: 1, md: 2 }}>
          <Descriptions.Item label="最近测试时间">{formatTime(configQuery.data?.last_tested_at)}</Descriptions.Item>
          <Descriptions.Item label="最近测试结果">{statusText[lastStatus] ?? lastStatus}</Descriptions.Item>
          <Descriptions.Item label="今日 Token 使用量">{usage.total_tokens ?? 0}</Descriptions.Item>
          <Descriptions.Item label="今日请求次数">{usage.request_count ?? 0}</Descriptions.Item>
          <Descriptions.Item label="今日失败次数">{usage.failure_count ?? 0}</Descriptions.Item>
          <Descriptions.Item label="错误摘要">{configQuery.data?.last_error_sanitized || "无"}</Descriptions.Item>
        </Descriptions>
      </Card>

      <Card title="最近 AI 任务">
        <Table<AiRun>
          rowKey="id"
          loading={runsQuery.isLoading || runsQuery.isFetching}
          dataSource={runsQuery.data?.items ?? []}
          pagination={false}
          columns={[
            { title: "任务", dataIndex: "job_type" },
            { title: "Provider", dataIndex: "provider" },
            { title: "模型", dataIndex: "model" },
            { title: "事件数", dataIndex: "event_count" },
            {
              title: "Token",
              render: (_, row) => (row.prompt_tokens ?? 0) + (row.completion_tokens ?? 0)
            },
            { title: "耗时 ms", dataIndex: "latency_ms" },
            { title: "状态", dataIndex: "status", render: (value) => <Tag>{value}</Tag> },
            { title: "创建时间", dataIndex: "created_at", render: formatTime }
          ]}
        />
      </Card>
    </Space>
  );
}

function normalizeModels(payload?: AiModelsResponse | string[]) {
  const rows = Array.isArray(payload) ? payload : payload?.models ?? payload?.data ?? [];
  return rows.flatMap((item) => {
    if (typeof item === "string") {
      return [{ value: item, label: item }];
    }
    const value = (item as AiModelInfo).id;
    return value ? [{ value, label: (item as AiModelInfo).name || value }] : [];
  });
}

function modelFallback(model?: string | null) {
  return model ? [{ value: model, label: model }] : [];
}

function formatTime(value?: string | null) {
  if (!value) {
    return "无";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("zh-CN", { hour12: false });
}
