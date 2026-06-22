import {
  Alert,
  Button,
  Card,
  Descriptions,
  Divider,
  Drawer,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Slider,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message
} from "antd";
import type { TableProps } from "antd";
import {
  ClearOutlined,
  FilterOutlined,
  ReloadOutlined,
  RobotOutlined,
  SaveOutlined,
  SearchOutlined
} from "@ant-design/icons";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import type { Key } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { normalizePaginated } from "../api/pagination";
import { useAuth } from "../auth/AuthContext";
import { QUERY_REFETCH_INTERVAL, QUERY_STALE_TIME, useDocumentVisible, visibleOnlyRefetchInterval } from "../queryConfig";
import type {
  EventAiInsight,
  EventFacets,
  EventRow,
  FacetOption,
  PaginatedResponse,
  SavedSearch
} from "../types/api";

const PAGE_SIZE = 20;
const API_PAGE_SIZE_LIMIT = 100;

type QueryMode = "all" | "any" | "phrase";
type Direction = "asc" | "desc";

type EventFilters = {
  q: string;
  q_mode: QueryMode;
  source_keys: string[];
  source_groups: string[];
  categories: string[];
  severities: string[];
  statuses: string[];
  symbols: string[];
  chains: string[];
  languages: string[];
  official_only?: boolean;
  has_ai_summary?: boolean;
  minimum_trust_score?: number;
  published_from?: string;
  published_to?: string;
  first_seen_from?: string;
  first_seen_to?: string;
  sort: string;
  direction: Direction;
  page: number;
  page_size: number;
};

type AdvancedFilterForm = Omit<EventFilters, "q" | "page">;

const severityOptions = [
  { value: "critical", label: "严重" },
  { value: "high", label: "高" },
  { value: "medium", label: "中" },
  { value: "normal", label: "普通" },
  { value: "low", label: "低" }
];

const statusOptions = [
  { value: "new", label: "新事件" },
  { value: "confirmed", label: "已确认" },
  { value: "triaged", label: "已研判" },
  { value: "ignored", label: "已忽略" }
];

const categoryOptions = [
  { value: "listing", label: "上币" },
  { value: "delisting", label: "下币" },
  { value: "derivatives_listing", label: "合约上线" },
  { value: "security_incident", label: "安全事件" },
  { value: "regulatory", label: "政策监管" },
  { value: "fundraising", label: "融资" },
  { value: "market", label: "市场动态" },
  { value: "exchange", label: "交易所" }
];

const sourceGroupFallback = [
  { value: "exchange_official", label: "交易所官方" },
  { value: "media_zh", label: "中文媒体" },
  { value: "media_en", label: "英文媒体" },
  { value: "regulator", label: "监管源" },
  { value: "onchain", label: "链上数据" }
];

const languageOptions = [
  { value: "zh", label: "中文" },
  { value: "en", label: "英文" }
];

const qModeOptions = [
  { value: "all", label: "全部关键词" },
  { value: "any", label: "任意关键词" },
  { value: "phrase", label: "短语匹配" }
];

const sortOptions = [
  { value: "first_seen_at", label: "首次发现时间" },
  { value: "published_at", label: "发布时间" },
  { value: "severity", label: "级别" },
  { value: "trust_score", label: "可信度" }
];

const directionOptions = [
  { value: "desc", label: "降序" },
  { value: "asc", label: "升序" }
];

const riskText: Record<string, string> = {
  low: "低",
  medium: "中",
  high: "高",
  critical: "严重"
};

const riskColor: Record<string, string> = {
  low: "green",
  medium: "gold",
  high: "orange",
  critical: "red"
};

export function EventsPage() {
  const { csrf } = useAuth();
  const queryClient = useQueryClient();
  const documentVisible = useDocumentVisible();
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = useMemo(() => parseEventFilters(searchParams), [searchParams]);
  const [keyword, setKeyword] = useState(filters.q);
  const [filterOpen, setFilterOpen] = useState(false);
  const [saveOpen, setSaveOpen] = useState(false);
  const [selected, setSelected] = useState<EventRow | null>(null);
  const [selectedRowKeys, setSelectedRowKeys] = useState<Key[]>([]);
  const [filterForm] = Form.useForm<AdvancedFilterForm>();
  const [saveForm] = Form.useForm<{ name: string }>();

  useEffect(() => {
    setKeyword(filters.q);
  }, [filters.q]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const current = parseEventFilters(searchParams);
      if (keyword.trim() !== current.q) {
        setSearchParams(filtersToSearchParams({ ...current, q: keyword.trim(), page: 1 }), { replace: true });
      }
    }, 300);
    return () => window.clearTimeout(timer);
  }, [keyword, searchParams, setSearchParams]);

  useEffect(() => {
    if (filterOpen) {
      filterForm.setFieldsValue({
        ...filters,
        minimum_trust_score: filters.minimum_trust_score ?? 0
      });
    }
  }, [filterForm, filterOpen, filters]);

  const eventsQuery = useQuery({
    queryKey: ["events", filters],
    queryFn: async () => {
      const payload = await api<EventRow[] | PaginatedResponse<EventRow>>(
        `/api/admin/events?${buildEventsQuery(filters)}`
      );
      return normalizePaginated(payload, filters.page, filters.page_size);
    },
    placeholderData: keepPreviousData,
    staleTime: QUERY_STALE_TIME.eventsList,
    refetchInterval: (query) =>
      visibleOnlyRefetchInterval(
        QUERY_REFETCH_INTERVAL.visibleEventsList,
        documentVisible,
        query.state.fetchFailureCount
      ),
    refetchIntervalInBackground: false
  });

  const facetsQuery = useQuery({
    queryKey: ["event-facets"],
    queryFn: () => api<EventFacets>("/api/admin/events/facets"),
    enabled: filterOpen,
    retry: false,
    staleTime: QUERY_STALE_TIME.eventFacets
  });

  const savedSearchesQuery = useQuery({
    queryKey: ["saved-searches"],
    queryFn: () => api<SavedSearch[]>("/api/admin/saved-searches"),
    retry: false,
    staleTime: QUERY_STALE_TIME.savedSearches
  });

  const saveSearch = useMutation({
    mutationFn: (values: { name: string }) =>
      api<SavedSearch>("/api/admin/saved-searches", {
        method: "POST",
        csrf,
        body: JSON.stringify({
          name: values.name,
          filters: filtersToPlainObject(filters)
        })
      }),
    onSuccess: () => {
      message.success("筛选条件已保存");
      setSaveOpen(false);
      saveForm.resetFields();
      queryClient.invalidateQueries({ queryKey: ["saved-searches"] });
    }
  });

  const summarizeBatch = useMutation({
    mutationFn: (eventIds: number[]) =>
      api("/api/admin/events/ai-summary-batch", {
        method: "POST",
        csrf,
        body: JSON.stringify({ event_ids: eventIds })
      }),
    onSuccess: () => {
      message.success("已提交 AI 整理任务");
      setSelectedRowKeys([]);
      queryClient.invalidateQueries({ queryKey: ["events"] });
    }
  });

  const summarizeSingle = useMutation({
    mutationFn: (eventId: number) =>
      api(`/api/admin/events/${eventId}/ai-summary`, {
        method: "POST",
        csrf
      }),
    onSuccess: (_, eventId) => {
      message.success("已提交重新生成任务");
      queryClient.invalidateQueries({ queryKey: ["event-ai-insight", eventId] });
      queryClient.invalidateQueries({ queryKey: ["events"] });
    }
  });

  const insightQuery = useQuery({
    queryKey: ["event-ai-insight", selected?.id],
    queryFn: () => api<EventAiInsight>(`/api/admin/events/${selected?.id}/ai-insight`),
    enabled: Boolean(selected?.id),
    retry: false,
    staleTime: QUERY_STALE_TIME.eventInsight
  });

  const data = eventsQuery.data ?? {
    items: [],
    total: 0,
    page: filters.page,
    page_size: filters.page_size
  };

  const columns = useMemo<TableProps<EventRow>["columns"]>(
    () => [
      {
        title: "标题",
        dataIndex: "display_title",
        ellipsis: true,
        render: (_, row) => (
          <Space direction="vertical" size={2} className="event-title-cell">
            <Button type="link" className="event-title-link" onClick={() => setSelected(row)}>
              {row.display_title || row.title}
            </Button>
            {row.ai_summary_zh ? (
              <Typography.Text type="secondary" ellipsis>
                {row.ai_summary_zh}
              </Typography.Text>
            ) : null}
          </Space>
        )
      },
      {
        title: "来源",
        dataIndex: "source_name",
        width: 160,
        render: (_, row) => (
          <Space direction="vertical" size={0}>
            <span>{row.source_name || row.source_key || "未知来源"}</span>
            <Space size={4}>
              {row.official ? <Tag color="blue">官方</Tag> : <Tag>媒体</Tag>}
              {row.language ? <Tag>{row.language === "zh" ? "中文" : "英文"}</Tag> : null}
            </Space>
          </Space>
        )
      },
      {
        title: "分类",
        dataIndex: "category_label",
        width: 120,
        render: (_, row) => row.category_label || categoryOptions.find((item) => item.value === row.category)?.label || row.category
      },
      {
        title: "级别",
        dataIndex: "severity_label",
        width: 100,
        render: (_, row) => <Tag color={severityColor(row.severity)}>{row.severity_label || row.severity}</Tag>
      },
      {
        title: "AI",
        dataIndex: "ai_summary_status",
        width: 170,
        render: (_, row) => (
          <Space direction="vertical" size={2}>
            {row.has_ai_summary || row.ai_summary_status === "completed" ? (
              <Tag color="green">已整理</Tag>
            ) : (
              <Tag>未整理</Tag>
            )}
            {typeof row.ai_importance_score === "number" ? (
              <Typography.Text type="secondary">重要度 {row.ai_importance_score}</Typography.Text>
            ) : null}
            {row.ai_risk_level ? (
              <Tag color={riskColor[row.ai_risk_level] ?? "default"}>{riskText[row.ai_risk_level] ?? row.ai_risk_level}</Tag>
            ) : null}
          </Space>
        )
      },
      {
        title: "币种/链",
        dataIndex: "symbols",
        width: 160,
        render: (_, row) => (
          <Space wrap size={[4, 4]}>
            {(row.symbols ?? []).slice(0, 3).map((symbol) => (
              <Tag key={symbol}>{symbol}</Tag>
            ))}
            {(row.chains ?? []).slice(0, 2).map((chain) => (
              <Tag key={chain} color="cyan">
                {chain}
              </Tag>
            ))}
          </Space>
        )
      },
      {
        title: "可信度",
        dataIndex: "trust_score",
        width: 90
      },
      {
        title: "发布时间",
        dataIndex: "published_at",
        width: 170,
        render: (value) => formatTime(value)
      },
      {
        title: "操作",
        width: 160,
        fixed: "right",
        render: (_, row) => (
          <Space>
            <Button size="small" onClick={() => setSelected(row)}>
              详情
            </Button>
            {row.primary_url ? (
              <Button size="small" href={row.primary_url} target="_blank" rel="noreferrer">
                原文
              </Button>
            ) : null}
          </Space>
        )
      }
    ],
    []
  );

  return (
    <>
      <Space direction="vertical" size={16} className="page-stack">
        <Space align="center" className="page-title-row" wrap>
          <Typography.Title level={3}>事件搜索</Typography.Title>
          <Typography.Text type="secondary">共 {data.total} 条结果</Typography.Text>
        </Space>

        <Card className="event-search-card">
          <Space className="event-search-toolbar" wrap>
            <Input
              allowClear
              size="large"
              prefix={<SearchOutlined />}
              placeholder="搜索标题、摘要、币种、链、来源、AI 标签或关键事实"
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              className="event-search-input"
            />
            <Select
              value={filters.q_mode}
              options={qModeOptions}
              className="event-q-mode"
              onChange={(q_mode) => updateFilters({ q_mode, page: 1 })}
            />
            <Button icon={<FilterOutlined />} onClick={() => setFilterOpen(true)}>
              高级筛选
            </Button>
            <Button
              icon={<ClearOutlined />}
              onClick={() => {
                setKeyword("");
                setSearchParams(filtersToSearchParams(defaultEventFilters()), { replace: true });
              }}
            >
              清空筛选
            </Button>
            <Button icon={<SaveOutlined />} onClick={() => setSaveOpen(true)}>
              保存筛选
            </Button>
            <Button
              type="primary"
              icon={<RobotOutlined />}
              loading={summarizeBatch.isPending}
              onClick={() => {
                const ids = selectedRowKeys.map(Number).filter((id) => Number.isFinite(id));
                if (!ids.length) {
                  message.warning("请先选择事件");
                  return;
                }
                summarizeBatch.mutate(ids);
              }}
            >
              对选中事件进行 AI 整理
            </Button>
          </Space>

          {savedSearchesQuery.data?.length ? (
            <Space className="saved-search-row" wrap>
              <Typography.Text type="secondary">已保存筛选</Typography.Text>
              <Select
                placeholder="选择一个筛选条件"
                className="saved-search-select"
                options={savedSearchesQuery.data.map((item) => ({ value: item.id, label: item.name }))}
                onChange={(id) => {
                  const saved = savedSearchesQuery.data?.find((item) => item.id === id);
                  if (saved) {
                    setSearchParams(filtersToSearchParams(savedSearchToFilters(saved.filters)), { replace: true });
                    message.success("已加载保存的筛选条件");
                  }
                }}
              />
            </Space>
          ) : null}
        </Card>

        <Table<EventRow>
          rowKey="id"
          loading={eventsQuery.isLoading}
          dataSource={data.items}
          columns={columns}
          rowSelection={{
            selectedRowKeys,
            onChange: setSelectedRowKeys
          }}
          scroll={{ x: 1240 }}
          pagination={{
            current: data.page,
            pageSize: data.page_size,
            total: data.total,
            showSizeChanger: true,
            pageSizeOptions: [10, 20, 50, API_PAGE_SIZE_LIMIT],
            showTotal: (total) => `共 ${total} 条`,
            onChange: (page, pageSize) => updateFilters({ page, page_size: pageSize }, false)
          }}
        />
      </Space>

      <Drawer
        title="高级筛选"
        open={filterOpen}
        width={520}
        onClose={() => setFilterOpen(false)}
        destroyOnClose
        extra={
          <Space>
            <Button onClick={() => filterForm.resetFields()}>重置表单</Button>
            <Button
              type="primary"
              onClick={() => {
                const values = filterForm.getFieldsValue();
                updateFilters(
                  {
                    ...values,
                    minimum_trust_score:
                      typeof values.minimum_trust_score === "number" && values.minimum_trust_score > 0
                        ? values.minimum_trust_score
                        : undefined,
                    official_only: values.official_only ? true : undefined,
                    has_ai_summary: values.has_ai_summary ? true : undefined,
                    page: 1
                  },
                  false
                );
                setFilterOpen(false);
              }}
            >
              应用筛选
            </Button>
          </Space>
        }
      >
        <Form form={filterForm} layout="vertical">
          <Form.Item label="关键词匹配模式" name="q_mode">
            <Select options={qModeOptions} />
          </Form.Item>
          <Form.Item label="来源" name="source_keys">
            <Select mode="multiple" allowClear showSearch options={facetOptions(facetsQuery.data?.source_keys)} />
          </Form.Item>
          <Form.Item label="来源分组" name="source_groups">
            <Select
              mode="multiple"
              allowClear
              options={facetOptions(facetsQuery.data?.source_groups, sourceGroupFallback)}
            />
          </Form.Item>
          <Form.Item label="分类" name="categories">
            <Select mode="multiple" allowClear options={facetOptions(facetsQuery.data?.categories, categoryOptions)} />
          </Form.Item>
          <Form.Item label="级别" name="severities">
            <Select mode="multiple" allowClear options={facetOptions(facetsQuery.data?.severities, severityOptions)} />
          </Form.Item>
          <Form.Item label="状态" name="statuses">
            <Select mode="multiple" allowClear options={facetOptions(facetsQuery.data?.statuses, statusOptions)} />
          </Form.Item>
          <Form.Item label="币种" name="symbols">
            <Select mode="tags" allowClear tokenSeparators={[",", " "]} options={facetOptions(facetsQuery.data?.symbols)} />
          </Form.Item>
          <Form.Item label="链" name="chains">
            <Select mode="tags" allowClear tokenSeparators={[",", " "]} options={facetOptions(facetsQuery.data?.chains)} />
          </Form.Item>
          <Form.Item label="语言" name="languages">
            <Select mode="multiple" allowClear options={facetOptions(facetsQuery.data?.languages, languageOptions)} />
          </Form.Item>
          <Space size="large" wrap>
            <Form.Item label="仅官方来源" name="official_only" valuePropName="checked">
              <Switch checkedChildren="开启" unCheckedChildren="关闭" />
            </Form.Item>
            <Form.Item label="仅有 AI 摘要" name="has_ai_summary" valuePropName="checked">
              <Switch checkedChildren="开启" unCheckedChildren="关闭" />
            </Form.Item>
          </Space>
          <Form.Item label="最低可信度" name="minimum_trust_score">
            <Slider min={0} max={100} marks={{ 0: "0", 50: "50", 100: "100" }} />
          </Form.Item>
          <Space className="time-filter-grid" wrap>
            <Form.Item label="发布时间从" name="published_from">
              <Input type="datetime-local" />
            </Form.Item>
            <Form.Item label="发布时间到" name="published_to">
              <Input type="datetime-local" />
            </Form.Item>
            <Form.Item label="首次发现从" name="first_seen_from">
              <Input type="datetime-local" />
            </Form.Item>
            <Form.Item label="首次发现到" name="first_seen_to">
              <Input type="datetime-local" />
            </Form.Item>
          </Space>
          <Space wrap>
            <Form.Item label="排序字段" name="sort">
              <Select options={sortOptions} className="filter-sort-select" />
            </Form.Item>
            <Form.Item label="排序方向" name="direction">
              <Select options={directionOptions} className="filter-direction-select" />
            </Form.Item>
            <Form.Item label="每页数量" name="page_size">
              <InputNumber min={10} max={API_PAGE_SIZE_LIMIT} step={10} />
            </Form.Item>
          </Space>
        </Form>
      </Drawer>

      <Modal
        title="保存筛选"
        open={saveOpen}
        confirmLoading={saveSearch.isPending}
        onOk={() => saveForm.submit()}
        onCancel={() => setSaveOpen(false)}
        okText="保存"
        cancelText="取消"
      >
        <Form form={saveForm} layout="vertical" onFinish={(values) => saveSearch.mutate(values)}>
          <Form.Item label="筛选名称" name="name" rules={[{ required: true, message: "请输入筛选名称" }]}>
            <Input placeholder="例如：高危交易所上币" />
          </Form.Item>
        </Form>
      </Modal>

      <Drawer
        open={Boolean(selected)}
        onClose={() => setSelected(null)}
        title="事件详情"
        width={720}
        extra={
          selected ? (
            <Space>
              <Button
                icon={<ReloadOutlined />}
                loading={summarizeSingle.isPending}
                onClick={() => summarizeSingle.mutate(selected.id)}
              >
                重新生成
              </Button>
              <Button onClick={() => message.info("已记录 AI 结果反馈")}>标记 AI 结果有误</Button>
            </Space>
          ) : null
        }
      >
        {selected ? (
          <Space direction="vertical" size={16} className="page-stack">
            <Descriptions bordered column={1} size="small">
              <Descriptions.Item label="标题">{selected.display_title || selected.title}</Descriptions.Item>
              <Descriptions.Item label="摘要">{selected.display_summary || "暂无摘要"}</Descriptions.Item>
              <Descriptions.Item label="来源">{selected.source_name || selected.source_key || "未知来源"}</Descriptions.Item>
              <Descriptions.Item label="分类">{selected.category_label || selected.category}</Descriptions.Item>
              <Descriptions.Item label="级别">{selected.severity_label || selected.severity}</Descriptions.Item>
              <Descriptions.Item label="可信度">{selected.trust_score}</Descriptions.Item>
              <Descriptions.Item label="发布时间">{formatTime(selected.published_at)}</Descriptions.Item>
              <Descriptions.Item label="原文">
                {selected.primary_url ? (
                  <a href={selected.primary_url} target="_blank" rel="noreferrer">
                    {selected.primary_url}
                  </a>
                ) : (
                  "无"
                )}
              </Descriptions.Item>
            </Descriptions>
            <AiInsightPanel
              insight={insightQuery.data}
              loading={insightQuery.isLoading}
              error={insightQuery.isError}
              fallback={selected}
            />
          </Space>
        ) : null}
      </Drawer>
    </>
  );

  function updateFilters(patch: Partial<EventFilters>, resetPage = true) {
    const next = {
      ...filters,
      ...patch,
      page: resetPage ? 1 : patch.page ?? filters.page,
      page_size: clampPageSize(patch.page_size ?? filters.page_size)
    };
    setSearchParams(filtersToSearchParams(next), { replace: true });
  }
}

function AiInsightPanel({
  insight,
  loading,
  error,
  fallback
}: {
  insight?: EventAiInsight;
  loading: boolean;
  error: boolean;
  fallback: EventRow;
}) {
  if (loading) {
    return <Card title="AI 摘要" loading />;
  }
  if (error && !fallback.ai_summary_zh) {
    return (
      <Alert
        type="info"
        showIcon
        message="暂无 AI 摘要"
        description="可以点击右上角重新生成，系统会在后台提交整理任务。"
      />
    );
  }

  const summary = insight?.summary_zh || fallback.ai_summary_zh;
  const headline = insight?.headline_zh || fallback.ai_headline_zh;
  const importance = insight?.importance_score ?? fallback.ai_importance_score;
  const risk = insight?.risk_level ?? fallback.ai_risk_level;
  const totalTokens = insight?.total_tokens ?? sumNumbers(insight?.prompt_tokens, insight?.completion_tokens);

  return (
    <Card title="AI 摘要">
      {summary || headline ? (
        <Space direction="vertical" size={12} className="page-stack">
          {headline ? <Typography.Title level={5}>{headline}</Typography.Title> : null}
          {summary ? <Typography.Paragraph>{summary}</Typography.Paragraph> : null}
          <Space wrap>
            {typeof importance === "number" ? <Tag color="blue">重要度 {importance}</Tag> : null}
            {risk ? <Tag color={riskColor[risk] ?? "default"}>风险 {riskText[risk] ?? risk}</Tag> : null}
            {typeof insight?.confidence === "number" ? <Tag>置信度 {(insight.confidence * 100).toFixed(0)}%</Tag> : null}
            {insight?.model ? <Tag>模型 {insight.model}</Tag> : null}
            {insight?.generated_at ? <Tag>生成时间 {formatTime(insight.generated_at)}</Tag> : null}
            {typeof totalTokens === "number" ? <Tag>Token {totalTokens}</Tag> : null}
          </Space>
          <Divider />
          <ListBlock title="关键事实" values={insight?.key_facts ?? insight?.facts} />
          <ListBlock title="推断内容" values={insight?.inferences} />
          <ListBlock title="来源链接" values={insight?.source_urls} link />
          {insight?.market_impact ? (
            <Alert type="warning" showIcon message="市场影响" description={insight.market_impact} />
          ) : null}
        </Space>
      ) : (
        <Empty description="暂无 AI 摘要" />
      )}
    </Card>
  );
}

function ListBlock({ title, values, link }: { title: string; values?: unknown[]; link?: boolean }) {
  if (!values?.length) {
    return null;
  }
  return (
    <div>
      <Typography.Text strong>{title}</Typography.Text>
      <ul className="detail-list">
        {values.slice(0, 8).map((item, index) => {
          const text = stringifyInsightItem(item);
          return (
            <li key={`${title}-${index}`}>
              {link && /^https:\/\//i.test(text) ? (
                <a href={text} target="_blank" rel="noreferrer">
                  {text}
                </a>
              ) : (
                text
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function defaultEventFilters(): EventFilters {
  return {
    q: "",
    q_mode: "all",
    source_keys: [],
    source_groups: [],
    categories: [],
    severities: [],
    statuses: [],
    symbols: [],
    chains: [],
    languages: [],
    sort: "first_seen_at",
    direction: "desc",
    page: 1,
    page_size: PAGE_SIZE
  };
}

function parseEventFilters(params: URLSearchParams): EventFilters {
  const defaults = defaultEventFilters();
  return {
    ...defaults,
    q: params.get("q") ?? defaults.q,
    q_mode: parseQMode(params.get("q_mode")),
    source_keys: getArrayParam(params, "source_keys"),
    source_groups: getArrayParam(params, "source_groups"),
    categories: getArrayParam(params, "categories"),
    severities: getArrayParam(params, "severities"),
    statuses: getArrayParam(params, "statuses"),
    symbols: getArrayParam(params, "symbols").map((item) => item.toUpperCase()),
    chains: getArrayParam(params, "chains"),
    languages: getArrayParam(params, "languages"),
    official_only: parseBoolean(params.get("official_only")),
    has_ai_summary: parseBoolean(params.get("has_ai_summary")),
    minimum_trust_score: parseNumber(params.get("minimum_trust_score")),
    published_from: params.get("published_from") ?? undefined,
    published_to: params.get("published_to") ?? undefined,
    first_seen_from: params.get("first_seen_from") ?? undefined,
    first_seen_to: params.get("first_seen_to") ?? undefined,
    sort: params.get("sort") || defaults.sort,
    direction: parseDirection(params.get("direction")),
    page: Math.max(1, parseInt(params.get("page") || "1", 10) || 1),
    page_size: clampPageSize(parseInt(params.get("page_size") || String(PAGE_SIZE), 10) || PAGE_SIZE)
  };
}

function filtersToSearchParams(filters: EventFilters) {
  const params = new URLSearchParams();
  appendIfPresent(params, "q", filters.q);
  appendIfPresent(params, "q_mode", filters.q_mode !== "all" ? filters.q_mode : "");
  appendArray(params, "source_keys", filters.source_keys);
  appendArray(params, "source_groups", filters.source_groups);
  appendArray(params, "categories", filters.categories);
  appendArray(params, "severities", filters.severities);
  appendArray(params, "statuses", filters.statuses);
  appendArray(params, "symbols", filters.symbols);
  appendArray(params, "chains", filters.chains);
  appendArray(params, "languages", filters.languages);
  appendIfPresent(params, "official_only", filters.official_only ? "true" : "");
  appendIfPresent(params, "has_ai_summary", filters.has_ai_summary ? "true" : "");
  appendIfPresent(params, "minimum_trust_score", filters.minimum_trust_score ? String(filters.minimum_trust_score) : "");
  appendIfPresent(params, "published_from", filters.published_from);
  appendIfPresent(params, "published_to", filters.published_to);
  appendIfPresent(params, "first_seen_from", filters.first_seen_from);
  appendIfPresent(params, "first_seen_to", filters.first_seen_to);
  appendIfPresent(params, "sort", filters.sort !== "first_seen_at" ? filters.sort : "");
  appendIfPresent(params, "direction", filters.direction !== "desc" ? filters.direction : "");
  if (filters.page > 1) {
    params.set("page", String(filters.page));
  }
  if (filters.page_size !== PAGE_SIZE) {
    params.set("page_size", String(filters.page_size));
  }
  return params;
}

function buildEventsQuery(filters: EventFilters) {
  const params = filtersToSearchParams(filters);
  params.set("page", String(filters.page));
  params.set("page_size", String(filters.page_size));
  params.set("q_mode", filters.q_mode);
  params.set("sort", filters.sort);
  params.set("direction", filters.direction);
  return params.toString();
}

function filtersToPlainObject(filters: EventFilters) {
  const result: Record<string, string | string[]> = {};
  filtersToSearchParams(filters).forEach((value, key) => {
    const existing = result[key];
    if (Array.isArray(existing)) {
      existing.push(value);
    } else if (typeof existing === "string") {
      result[key] = [existing, value];
    } else {
      result[key] = value;
    }
  });
  return result;
}

function savedSearchToFilters(value: Record<string, unknown>): EventFilters {
  const params = new URLSearchParams();
  Object.entries(value).forEach(([key, raw]) => {
    if (Array.isArray(raw)) {
      raw.forEach((item) => params.append(key, String(item)));
    } else if (raw !== undefined && raw !== null && raw !== "") {
      params.set(key, String(raw));
    }
  });
  return parseEventFilters(params);
}

function facetOptions(facets?: FacetOption[], fallback: Array<{ value: string; label: string }> = []) {
  const seen = new Set<string>();
  const options = [...(facets ?? []), ...fallback].flatMap((item) => {
    const value = item.value ?? ("key" in item ? item.key : undefined);
    if (!value || seen.has(value)) {
      return [];
    }
    seen.add(value);
    const label = "count" in item && typeof item.count === "number" ? `${item.label ?? value} (${item.count})` : item.label ?? value;
    return [{ value, label }];
  });
  return options;
}

function getArrayParam(params: URLSearchParams, key: string) {
  return params
    .getAll(key)
    .flatMap((value) => value.split(","))
    .map((value) => value.trim())
    .filter(Boolean);
}

function appendArray(params: URLSearchParams, key: string, values?: string[]) {
  values?.filter(Boolean).forEach((value) => params.append(key, value));
}

function appendIfPresent(params: URLSearchParams, key: string, value?: string) {
  if (value) {
    params.set(key, value);
  }
}

function parseBoolean(value: string | null) {
  return value === "true" ? true : undefined;
}

function parseNumber(value: string | null) {
  if (!value) {
    return undefined;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function parseQMode(value: string | null): QueryMode {
  return value === "any" || value === "phrase" ? value : "all";
}

function parseDirection(value: string | null): Direction {
  return value === "asc" ? "asc" : "desc";
}

function clampPageSize(value: number) {
  return Math.min(API_PAGE_SIZE_LIMIT, Math.max(10, value));
}

function severityColor(value: string) {
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

function formatTime(value?: string) {
  if (!value) {
    return "未知";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("zh-CN", { hour12: false });
}

function stringifyInsightItem(item: unknown) {
  if (typeof item === "string") {
    return item;
  }
  if (typeof item === "number" || typeof item === "boolean") {
    return String(item);
  }
  if (item && typeof item === "object") {
    const record = item as Record<string, unknown>;
    const text = record.text ?? record.fact ?? record.summary ?? record.url;
    if (typeof text === "string") {
      return text;
    }
    return JSON.stringify(record);
  }
  return "不确定";
}

function sumNumbers(...values: Array<number | undefined>) {
  const numbers = values.filter((value): value is number => typeof value === "number");
  return numbers.length ? numbers.reduce((total, value) => total + value, 0) : undefined;
}
