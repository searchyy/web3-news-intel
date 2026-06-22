import {
  Button,
  Descriptions,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Slider,
  Space,
  Switch,
  Table,
  Tabs,
  Tag,
  Typography,
  message
} from "antd";
import type { TableProps } from "antd";
import {
  ApiOutlined,
  CheckCircleOutlined,
  DeleteOutlined,
  EditOutlined,
  EyeOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  SendOutlined,
  StopOutlined
} from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import type {
  Destination,
  ReportEventPreview,
  ReportPreview,
  ReportSchedule,
  ReportSendResult,
  ReportType,
  SavedSearch
} from "../types/api";

type WebhookFormValues = {
  key: string;
  name: string;
  webhook_url: string;
};

type ScheduleFormValues = {
  destination_id?: string;
  name: string;
  enabled: boolean;
  report_type: ReportType;
  timezone: string;
  interval_minutes?: number | null;
  hour?: number | null;
  minute?: number | null;
  saved_search_id?: number | null;
  source_groups: string[];
  categories: string[];
  severities: string[];
  symbols: string[];
  chains: string[];
  minimum_trust_score?: number | null;
  include_ai_summary: boolean;
  maximum_events: number;
};

const reportTypeOptions: Array<{ value: ReportType; label: string }> = [
  { value: "immediate", label: "立即告警" },
  { value: "digest_15m", label: "15 分钟摘要" },
  { value: "digest_30m", label: "30 分钟摘要" },
  { value: "hourly", label: "每小时汇报" },
  { value: "daily_morning", label: "每日早报" },
  { value: "daily_evening", label: "每日晚报" },
  { value: "custom", label: "自定义时间" }
];

const timezoneOptions = [
  { value: "Asia/Taipei", label: "Asia/Taipei" },
  { value: "Asia/Shanghai", label: "Asia/Shanghai" },
  { value: "UTC", label: "UTC" },
  { value: "America/New_York", label: "America/New_York" },
  { value: "Europe/London", label: "Europe/London" }
];

const sourceGroupOptions = [
  { value: "exchange_official", label: "交易所官方" },
  { value: "media_zh", label: "中文媒体" },
  { value: "media_en", label: "英文媒体" },
  { value: "regulator", label: "监管来源" },
  { value: "onchain", label: "链上数据" }
];

const categoryOptions = [
  { value: "listing", label: "上币" },
  { value: "delisting", label: "下币" },
  { value: "derivatives_listing", label: "合约上线" },
  { value: "derivatives_delisting", label: "合约下架" },
  { value: "wallet_maintenance", label: "钱包维护" },
  { value: "deposit_withdrawal", label: "充提暂停" },
  { value: "system_maintenance", label: "系统维护" },
  { value: "security_incident", label: "安全事件" },
  { value: "trading_rule", label: "交易规则" },
  { value: "regulatory", label: "政策监管" },
  { value: "fundraising", label: "融资" },
  { value: "market", label: "市场动态" }
];

const severityOptions = [
  { value: "critical", label: "严重" },
  { value: "high", label: "高" },
  { value: "medium", label: "中" },
  { value: "normal", label: "普通" },
  { value: "low", label: "低" }
];

const statusColor: Record<string, string> = {
  pending: "gold",
  active: "green",
  degraded: "orange",
  disabled: "default",
  success: "green",
  sent: "green",
  duplicate: "blue",
  empty: "default",
  failed: "red",
  error: "red"
};

export function FeishuGroupsPage() {
  const { csrf } = useAuth();
  const queryClient = useQueryClient();
  const [creatingWebhook, setCreatingWebhook] = useState(false);
  const [editingSchedule, setEditingSchedule] = useState<ReportSchedule | null>(null);
  const [scheduleModalOpen, setScheduleModalOpen] = useState(false);
  const [preview, setPreview] = useState<ReportPreview | null>(null);
  const [webhookForm] = Form.useForm<WebhookFormValues>();
  const [scheduleForm] = Form.useForm<ScheduleFormValues>();

  const destinationsQuery = useQuery({
    queryKey: ["destinations"],
    queryFn: () => api<Destination[]>("/api/admin/destinations"),
    staleTime: 30_000
  });

  const schedulesQuery = useQuery({
    queryKey: ["report-schedules"],
    queryFn: () => api<ReportSchedule[]>("/api/admin/report-schedules"),
    staleTime: 30_000
  });

  const savedSearchesQuery = useQuery({
    queryKey: ["saved-searches"],
    queryFn: () => api<SavedSearch[]>("/api/admin/saved-searches"),
    retry: false,
    staleTime: 60_000
  });

  const feishuDestinations = useMemo(
    () => (destinationsQuery.data ?? []).filter((item) => item.provider.startsWith("feishu")),
    [destinationsQuery.data]
  );
  const destinationNameById = useMemo(
    () => new Map(feishuDestinations.map((item) => [item.id, item.name])),
    [feishuDestinations]
  );
  const savedSearchNameById = useMemo(
    () => new Map((savedSearchesQuery.data ?? []).map((item) => [Number(item.id), item.name])),
    [savedSearchesQuery.data]
  );

  const createWebhook = useMutation({
    mutationFn: (values: WebhookFormValues) =>
      api<Destination>("/api/admin/destinations", {
        method: "POST",
        csrf,
        body: JSON.stringify({ ...values, provider: "feishu_webhook" })
      }),
    onSuccess: () => {
      message.success("已保存，Webhook URL 不会再次显示");
      setCreatingWebhook(false);
      webhookForm.resetFields();
      queryClient.invalidateQueries({ queryKey: ["destinations"] });
    }
  });

  const destinationAction = useMutation({
    mutationFn: ({ path }: { path: string; successText: string }) =>
      api<Destination>(path, {
        method: "POST",
        csrf
      }),
    onSuccess: (_, variables) => {
      message.success(variables.successText);
      queryClient.invalidateQueries({ queryKey: ["destinations"] });
    }
  });

  const saveSchedule = useMutation({
    mutationFn: (values: ScheduleFormValues) => {
      const body = buildSchedulePayload(values, Boolean(editingSchedule));
      if (editingSchedule) {
        return api<ReportSchedule>(`/api/admin/report-schedules/${editingSchedule.id}`, {
          method: "PATCH",
          csrf,
          body: JSON.stringify(body)
        });
      }
      return api<ReportSchedule>("/api/admin/report-schedules", {
        method: "POST",
        csrf,
        body: JSON.stringify(body)
      });
    },
    onSuccess: () => {
      message.success(editingSchedule ? "汇报规则已更新" : "汇报规则已创建");
      setScheduleModalOpen(false);
      setEditingSchedule(null);
      scheduleForm.resetFields();
      queryClient.invalidateQueries({ queryKey: ["report-schedules"] });
    }
  });

  const patchSchedule = useMutation({
    mutationFn: ({ schedule, payload }: { schedule: ReportSchedule; payload: Partial<ScheduleFormValues> }) =>
      api<ReportSchedule>(`/api/admin/report-schedules/${schedule.id}`, {
        method: "PATCH",
        csrf,
        body: JSON.stringify(payload)
      }),
    onSuccess: () => {
      message.success("汇报规则已更新");
      queryClient.invalidateQueries({ queryKey: ["report-schedules"] });
    }
  });

  const deleteSchedule = useMutation({
    mutationFn: (schedule: ReportSchedule) =>
      api(`/api/admin/report-schedules/${schedule.id}`, {
        method: "DELETE",
        csrf
      }),
    onSuccess: () => {
      message.success("汇报规则已删除");
      queryClient.invalidateQueries({ queryKey: ["report-schedules"] });
    }
  });

  const previewSchedule = useMutation({
    mutationFn: (schedule: ReportSchedule) =>
      api<ReportPreview>(`/api/admin/report-schedules/${schedule.id}/preview`, {
        method: "POST",
        csrf
      }),
    onSuccess: (result) => setPreview(result)
  });

  const runSchedule = useMutation({
    mutationFn: (schedule: ReportSchedule) =>
      api(`/api/admin/report-schedules/${schedule.id}/run`, {
        method: "POST",
        csrf
      }),
    onSuccess: () => {
      message.success("汇报任务已入队");
      queryClient.invalidateQueries({ queryKey: ["report-schedules"] });
    }
  });

  const testSendSchedule = useMutation({
    mutationFn: (schedule: ReportSchedule) =>
      api<ReportSendResult>(`/api/admin/report-schedules/${schedule.id}/test-send`, {
        method: "POST",
        csrf
      }),
    onSuccess: (result) => {
      message.success(result.dry_run ? "测试汇报已生成，当前为 dry-run" : "测试汇报已发送到 Mock/配置目标");
      queryClient.invalidateQueries({ queryKey: ["report-schedules"] });
    }
  });

  const destinationColumns = useMemo<TableProps<Destination>["columns"]>(
    () => [
      { title: "名称", dataIndex: "name", width: 180 },
      { title: "模式", dataIndex: "provider", width: 150 },
      {
        title: "状态",
        dataIndex: "status",
        width: 110,
        render: (value: string, row) => (
          <Space size={4}>
            <Tag color={statusColor[value] ?? "default"}>{value}</Tag>
            {row.enabled ? <Tag color="green">已启用</Tag> : <Tag>已停用</Tag>}
          </Space>
        )
      },
      { title: "群组", dataIndex: "chat_name", width: 160 },
      { title: "Secret 指纹", dataIndex: "secret_fingerprint", width: 160 },
      { title: "最近成功", dataIndex: "last_success_at", width: 180, render: formatTime },
      { title: "最近失败", dataIndex: "last_failure_at", width: 180, render: formatTime },
      {
        title: "操作",
        width: 320,
        fixed: "right",
        render: (_, row) => (
          <Space wrap>
            <Button
              size="small"
              icon={<CheckCircleOutlined />}
              onClick={() =>
                destinationAction.mutate({
                  path: `/api/admin/destinations/${row.id}/approve`,
                  successText: "已审批"
                })
              }
            >
              审批
            </Button>
            <Button
              size="small"
              icon={row.enabled ? <StopOutlined /> : <CheckCircleOutlined />}
              onClick={() =>
                destinationAction.mutate({
                  path: `/api/admin/destinations/${row.id}/${row.enabled ? "disable" : "enable"}`,
                  successText: row.enabled ? "已停用" : "已启用"
                })
              }
            >
              {row.enabled ? "停用" : "启用"}
            </Button>
            <Button
              size="small"
              icon={<ApiOutlined />}
              onClick={() =>
                destinationAction.mutate({
                  path: `/api/admin/destinations/${row.id}/test`,
                  successText: "测试卡片已提交"
                })
              }
            >
              测试卡片
            </Button>
          </Space>
        )
      }
    ],
    [destinationAction]
  );

  const scheduleColumns = useMemo<TableProps<ReportSchedule>["columns"]>(
    () => [
      {
        title: "规则名称",
        dataIndex: "name",
        width: 180,
        render: (value: string, row) => (
          <Space direction="vertical" size={2}>
            <Typography.Text strong>{value}</Typography.Text>
            <Typography.Text type="secondary">{destinationNameById.get(row.destination_id) ?? row.destination_id}</Typography.Text>
          </Space>
        )
      },
      {
        title: "周期",
        dataIndex: "report_type",
        width: 130,
        render: (_, row) => reportTypeLabel(row)
      },
      {
        title: "状态",
        dataIndex: "enabled",
        width: 100,
        render: (enabled: boolean, row) => (
          <Space direction="vertical" size={2}>
            <Tag color={enabled ? "green" : "default"}>{enabled ? "已启用" : "已停用"}</Tag>
            {row.last_result ? <Tag color={statusColor[row.last_result] ?? "default"}>{row.last_result}</Tag> : null}
          </Space>
        )
      },
      {
        title: "筛选条件",
        width: 260,
        render: (_, row) => (
          <Space wrap size={[4, 4]}>
            {row.saved_search_id ? <Tag color="blue">{savedSearchNameById.get(row.saved_search_id) ?? `筛选 ${row.saved_search_id}`}</Tag> : null}
            {row.source_groups.slice(0, 2).map((item) => (
              <Tag key={item}>{sourceGroupText(item)}</Tag>
            ))}
            {row.categories.slice(0, 2).map((item) => (
              <Tag key={item}>{categoryText(item)}</Tag>
            ))}
            {row.severities.slice(0, 2).map((item) => (
              <Tag key={item} color={severityColor(item)}>
                {severityText(item)}
              </Tag>
            ))}
            {row.symbols.slice(0, 3).map((item) => (
              <Tag key={item}>{item}</Tag>
            ))}
            {row.chains.slice(0, 2).map((item) => (
              <Tag key={item} color="cyan">
                {item}
              </Tag>
            ))}
          </Space>
        )
      },
      {
        title: "AI/数量",
        width: 120,
        render: (_, row) => (
          <Space direction="vertical" size={2}>
            <Tag color={row.include_ai_summary ? "purple" : "default"}>{row.include_ai_summary ? "使用 AI" : "模板摘要"}</Tag>
            <Typography.Text type="secondary">最多 {row.maximum_events} 条</Typography.Text>
          </Space>
        )
      },
      { title: "最低可信度", dataIndex: "minimum_trust_score", width: 110, render: (value) => value ?? "不限" },
      { title: "下次执行", dataIndex: "next_run_at", width: 170, render: formatTime },
      { title: "最近执行", dataIndex: "last_run_at", width: 170, render: formatTime },
      {
        title: "最近状态",
        width: 170,
        render: (_, row) => row.last_error_sanitized || row.last_result || "无"
      },
      {
        title: "操作",
        width: 420,
        fixed: "right",
        render: (_, row) => (
          <Space wrap>
            <Button size="small" icon={<EditOutlined />} onClick={() => openEditSchedule(row)}>
              编辑
            </Button>
            <Button
              size="small"
              icon={row.enabled ? <StopOutlined /> : <CheckCircleOutlined />}
              onClick={() => patchSchedule.mutate({ schedule: row, payload: { enabled: !row.enabled } })}
            >
              {row.enabled ? "停用" : "启用"}
            </Button>
            <Button size="small" icon={<EyeOutlined />} onClick={() => previewSchedule.mutate(row)}>
              预览
            </Button>
            <Button size="small" icon={<PlayCircleOutlined />} onClick={() => runSchedule.mutate(row)}>
              立即运行
            </Button>
            <Button size="small" icon={<SendOutlined />} onClick={() => testSendSchedule.mutate(row)}>
              发送测试汇报
            </Button>
            <Popconfirm
              title="删除汇报规则"
              description="删除后不会再按该规则发送汇报。"
              okText="删除"
              cancelText="取消"
              onConfirm={() => deleteSchedule.mutate(row)}
            >
              <Button size="small" danger icon={<DeleteOutlined />}>
                删除
              </Button>
            </Popconfirm>
          </Space>
        )
      }
    ],
    [deleteSchedule, destinationNameById, patchSchedule, previewSchedule, runSchedule, savedSearchNameById, testSendSchedule]
  );

  return (
    <Space direction="vertical" size={16} className="page-stack">
      <Space className="page-title-row" wrap>
        <Typography.Title level={3}>飞书群组与汇报</Typography.Title>
        <Space wrap>
          <Button icon={<PlusOutlined />} onClick={() => setCreatingWebhook(true)}>
            添加飞书 Webhook
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreateSchedule}>
            新建汇报规则
          </Button>
        </Space>
      </Space>

      <Tabs
        items={[
          {
            key: "destinations",
            label: "飞书群组",
            children: (
              <Table<Destination>
                rowKey="id"
                loading={destinationsQuery.isLoading}
                dataSource={feishuDestinations}
                columns={destinationColumns}
                scroll={{ x: 1280 }}
                pagination={{ pageSize: 10, showSizeChanger: true }}
              />
            )
          },
          {
            key: "schedules",
            label: "汇报规则",
            children: (
              <Table<ReportSchedule>
                rowKey="id"
                loading={schedulesQuery.isLoading}
                dataSource={schedulesQuery.data ?? []}
                columns={scheduleColumns}
                scroll={{ x: 1700 }}
                pagination={{ pageSize: 10, showSizeChanger: true }}
              />
            )
          }
        ]}
      />

      <Modal
        title="添加飞书 Webhook"
        open={creatingWebhook}
        footer={null}
        onCancel={() => setCreatingWebhook(false)}
        destroyOnClose
      >
        <Form form={webhookForm} layout="vertical" onFinish={(values) => createWebhook.mutate(values)}>
          <Form.Item label="Key" name="key" rules={[{ required: true, message: "请输入唯一 Key" }]}>
            <Input />
          </Form.Item>
          <Form.Item label="名称" name="name" rules={[{ required: true, message: "请输入名称" }]}>
            <Input />
          </Form.Item>
          <Form.Item label="Webhook URL（只写）" name="webhook_url" rules={[{ required: true, message: "请输入 Webhook URL" }]}>
            <Input.Password autoComplete="off" />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={createWebhook.isPending}>
            保存
          </Button>
        </Form>
      </Modal>

      <Modal
        title={editingSchedule ? "编辑汇报规则" : "新建汇报规则"}
        open={scheduleModalOpen}
        width={760}
        okText={editingSchedule ? "保存修改" : "创建规则"}
        cancelText="取消"
        confirmLoading={saveSchedule.isPending}
        onOk={() => scheduleForm.submit()}
        onCancel={() => {
          setScheduleModalOpen(false);
          setEditingSchedule(null);
        }}
        destroyOnClose
      >
        <Form form={scheduleForm} layout="vertical" onFinish={(values) => saveSchedule.mutate(values)}>
          <Space wrap align="start" className="form-grid">
            <Form.Item label="规则名称" name="name" rules={[{ required: true, message: "请输入汇报规则名称" }]}>
              <Input className="wide-control" placeholder="例如：每小时高风险事件汇报" />
            </Form.Item>
            <Form.Item label="飞书群组" name="destination_id" rules={[{ required: !editingSchedule, message: "请选择飞书群组" }]}>
              <Select
                disabled={Boolean(editingSchedule)}
                className="wide-control"
                placeholder="选择目标群组"
                options={feishuDestinations.map((item) => ({ value: item.id, label: item.name }))}
              />
            </Form.Item>
            <Form.Item label="启用规则" name="enabled" valuePropName="checked" initialValue>
              <Switch checkedChildren="启用" unCheckedChildren="停用" />
            </Form.Item>
            <Form.Item label="是否使用 AI" name="include_ai_summary" valuePropName="checked" initialValue>
              <Switch checkedChildren="AI 摘要" unCheckedChildren="模板摘要" />
            </Form.Item>
          </Space>

          <Space wrap align="start" className="form-grid">
            <Form.Item label="汇报周期" name="report_type" rules={[{ required: true, message: "请选择汇报周期" }]}>
              <Select className="medium-control" options={reportTypeOptions} />
            </Form.Item>
            <Form.Item label="时区" name="timezone" rules={[{ required: true, message: "请选择时区" }]}>
              <Select className="medium-control" options={timezoneOptions} />
            </Form.Item>
            <Form.Item shouldUpdate noStyle>
              {({ getFieldValue }) => {
                const type = getFieldValue("report_type") as ReportType | undefined;
                if (type === "daily_morning" || type === "daily_evening" || type === "custom") {
                  return (
                    <>
                      <Form.Item label="小时" name="hour">
                        <InputNumber min={0} max={23} className="small-control" />
                      </Form.Item>
                      <Form.Item label="分钟" name="minute">
                        <InputNumber min={0} max={59} className="small-control" />
                      </Form.Item>
                    </>
                  );
                }
                return (
                  <Form.Item label="间隔分钟" name="interval_minutes">
                    <InputNumber min={5} max={1440} className="small-control" />
                  </Form.Item>
                );
              }}
            </Form.Item>
            <Form.Item label="最大事件数" name="maximum_events">
              <InputNumber min={1} max={100} className="small-control" />
            </Form.Item>
          </Space>

          <Form.Item label="关联保存筛选" name="saved_search_id">
            <Select
              allowClear
              placeholder="可选：复用事件页保存的筛选条件"
              options={(savedSearchesQuery.data ?? []).map((item) => ({ value: Number(item.id), label: item.name }))}
            />
          </Form.Item>

          <Form.Item label="来源分组" name="source_groups">
            <Select mode="multiple" allowClear options={sourceGroupOptions} />
          </Form.Item>
          <Form.Item label="分类" name="categories">
            <Select mode="multiple" allowClear options={categoryOptions} />
          </Form.Item>
          <Form.Item label="级别" name="severities">
            <Select mode="multiple" allowClear options={severityOptions} />
          </Form.Item>
          <Form.Item label="币种" name="symbols">
            <Select mode="tags" allowClear tokenSeparators={[",", " "]} placeholder="例如 BTC、ETH" />
          </Form.Item>
          <Form.Item label="链" name="chains">
            <Select mode="tags" allowClear tokenSeparators={[",", " "]} placeholder="例如 Ethereum、Solana" />
          </Form.Item>
          <Form.Item label="最低可信度" name="minimum_trust_score">
            <Slider min={0} max={100} marks={{ 0: "不限", 50: "50", 100: "100" }} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="飞书汇报预览"
        open={Boolean(preview)}
        width={820}
        onCancel={() => setPreview(null)}
        footer={[
          <Button key="close" onClick={() => setPreview(null)}>
            关闭
          </Button>
        ]}
      >
        {preview ? <ReportPreviewPanel preview={preview} /> : null}
      </Modal>
    </Space>
  );

  function openCreateSchedule() {
    setEditingSchedule(null);
    scheduleForm.setFieldsValue(defaultScheduleValues(feishuDestinations[0]?.id));
    setScheduleModalOpen(true);
  }

  function openEditSchedule(schedule: ReportSchedule) {
    setEditingSchedule(schedule);
    scheduleForm.setFieldsValue({
      destination_id: schedule.destination_id,
      name: schedule.name,
      enabled: schedule.enabled,
      report_type: schedule.report_type,
      timezone: schedule.timezone,
      interval_minutes: schedule.interval_minutes ?? defaultInterval(schedule.report_type),
      hour: schedule.hour ?? defaultHour(schedule.report_type),
      minute: schedule.minute ?? 0,
      saved_search_id: schedule.saved_search_id ?? null,
      source_groups: schedule.source_groups ?? [],
      categories: schedule.categories ?? [],
      severities: schedule.severities ?? [],
      symbols: schedule.symbols ?? [],
      chains: schedule.chains ?? [],
      minimum_trust_score: schedule.minimum_trust_score ?? 0,
      include_ai_summary: schedule.include_ai_summary,
      maximum_events: schedule.maximum_events
    });
    setScheduleModalOpen(true);
  }
}

function ReportPreviewPanel({ preview }: { preview: ReportPreview }) {
  const columns = useMemo<TableProps<ReportEventPreview>["columns"]>(
    () => [
      { title: "标题", dataIndex: "title", ellipsis: true },
      { title: "级别", dataIndex: "severity", width: 90, render: (value) => <Tag color={severityColor(value)}>{severityText(value)}</Tag> },
      { title: "分类", dataIndex: "category", width: 120, render: categoryText },
      { title: "发布时间", dataIndex: "published_at", width: 170, render: formatTime },
      {
        title: "币种/链",
        width: 180,
        render: (_, row) => (
          <Space wrap size={[4, 4]}>
            {row.symbols.slice(0, 3).map((symbol) => (
              <Tag key={symbol}>{symbol}</Tag>
            ))}
            {row.chains.slice(0, 2).map((chain) => (
              <Tag key={chain} color="cyan">
                {chain}
              </Tag>
            ))}
          </Space>
        )
      }
    ],
    []
  );
  return (
    <Space direction="vertical" size={16} className="page-stack">
      <Descriptions bordered size="small" column={{ xs: 1, md: 2 }}>
        <Descriptions.Item label="汇报窗口">
          {formatTime(preview.window_start)} - {formatTime(preview.window_end)}
        </Descriptions.Item>
        <Descriptions.Item label="事件总数">{preview.event_count}</Descriptions.Item>
        <Descriptions.Item label="Critical/High">{preview.critical_high_count}</Descriptions.Item>
        <Descriptions.Item label="主要币种">{preview.top_symbols.join("、") || "无"}</Descriptions.Item>
        <Descriptions.Item label="主要分类">{preview.top_categories.map(categoryText).join("、") || "无"}</Descriptions.Item>
        <Descriptions.Item label="未展示数量">{preview.omitted_count}</Descriptions.Item>
      </Descriptions>
      <Typography.Paragraph>{preview.summary_zh}</Typography.Paragraph>
      <Table<ReportEventPreview>
        rowKey="id"
        size="small"
        dataSource={preview.events}
        columns={columns}
        pagination={false}
        scroll={{ x: 760 }}
      />
    </Space>
  );
}

function defaultScheduleValues(destinationId?: string): ScheduleFormValues {
  return {
    destination_id: destinationId,
    name: "",
    enabled: true,
    report_type: "hourly",
    timezone: "Asia/Taipei",
    interval_minutes: 60,
    hour: 9,
    minute: 0,
    saved_search_id: null,
    source_groups: [],
    categories: [],
    severities: [],
    symbols: [],
    chains: [],
    minimum_trust_score: 0,
    include_ai_summary: true,
    maximum_events: 20
  };
}

function buildSchedulePayload(values: ScheduleFormValues, editing: boolean) {
  const payload: Record<string, unknown> = {
    name: values.name,
    enabled: Boolean(values.enabled),
    report_type: values.report_type,
    timezone: values.timezone || "Asia/Taipei",
    interval_minutes: normalizeNullableNumber(values.interval_minutes),
    hour: normalizeNullableNumber(values.hour),
    minute: normalizeNullableNumber(values.minute),
    saved_search_id: normalizeNullableNumber(values.saved_search_id),
    source_groups: values.source_groups ?? [],
    categories: values.categories ?? [],
    severities: values.severities ?? [],
    symbols: (values.symbols ?? []).map((item) => item.toUpperCase()),
    chains: values.chains ?? [],
    minimum_trust_score:
      typeof values.minimum_trust_score === "number" && values.minimum_trust_score > 0
        ? values.minimum_trust_score
        : null,
    include_ai_summary: Boolean(values.include_ai_summary),
    maximum_events: values.maximum_events ?? 20
  };
  if (!editing) {
    payload.destination_id = values.destination_id;
  }
  return payload;
}

function normalizeNullableNumber(value?: number | null) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function reportTypeLabel(schedule: ReportSchedule) {
  const label = reportTypeOptions.find((item) => item.value === schedule.report_type)?.label ?? schedule.report_type;
  if (schedule.report_type === "daily_morning" || schedule.report_type === "daily_evening" || schedule.report_type === "custom") {
    return `${label} ${padTime(schedule.hour ?? 0)}:${padTime(schedule.minute ?? 0)} ${schedule.timezone}`;
  }
  if (schedule.interval_minutes) {
    return `${label} / ${schedule.interval_minutes} 分钟`;
  }
  return label;
}

function defaultInterval(type?: ReportType) {
  if (type === "digest_15m") {
    return 15;
  }
  if (type === "digest_30m") {
    return 30;
  }
  if (type === "hourly") {
    return 60;
  }
  return 5;
}

function defaultHour(type?: ReportType) {
  if (type === "daily_evening") {
    return 18;
  }
  return 9;
}

function padTime(value: number) {
  return String(value).padStart(2, "0");
}

function sourceGroupText(value: string) {
  return sourceGroupOptions.find((item) => item.value === value)?.label ?? value;
}

function categoryText(value?: string) {
  if (!value) {
    return "未知";
  }
  return categoryOptions.find((item) => item.value === value)?.label ?? value;
}

function severityText(value?: string) {
  if (!value) {
    return "未知";
  }
  return severityOptions.find((item) => item.value === value)?.label ?? value;
}

function severityColor(value?: string) {
  if (value === "critical") {
    return "red";
  }
  if (value === "high") {
    return "orange";
  }
  if (value === "medium" || value === "normal") {
    return "gold";
  }
  return "default";
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
