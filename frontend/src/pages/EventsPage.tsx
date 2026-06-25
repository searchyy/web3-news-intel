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
  Timeline,
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
import {
  aiJobErrorMessage,
  aiJobPollInterval,
  aiJobStatusColor,
  aiJobStatusText,
  getAiJob,
  inputQualityText,
  isAiInsightResponse,
  isTerminalAiJobStatus,
  normalizeAiJobErrorMessage,
  normalizeAiJobFromSubmitResponse,
  normalizeAiJobStatus,
  requestBatchAiSummary,
  requestEventAiSummary,
  shouldWarnInputQuality
} from "../api/aiJobs";
import { api } from "../api/client";
import {
  formatPipelinePreview,
  getEventPipeline,
  normalizeEventPipeline,
  pipelineStatusColor,
  pipelineStatusText,
  pipelineTimelineColor,
  redactSensitiveText
} from "../api/eventPipeline";
import type { EventPipelineDelivery, NormalizedEventPipeline } from "../api/eventPipeline";
import { normalizePaginated } from "../api/pagination";
import { useAuth } from "../auth/AuthContext";
import { QUERY_REFETCH_INTERVAL, QUERY_STALE_TIME, useDocumentVisible, visibleOnlyRefetchInterval } from "../queryConfig";
import type {
  EventAiInsight,
  EventFacets,
  EventRow,
  FacetOption,
  AiJob,
  AiJobStatus,
  PaginatedResponse,
  SavedSearch
} from "../types/api";

const PAGE_SIZE = 20;
const API_PAGE_SIZE_LIMIT = 100;

type QueryMode = "all" | "any" | "phrase";
type Direction = "asc" | "desc";
type PublishedQuickRange = "all" | "1h" | "6h" | "24h" | "7d" | "custom";
type AiQuickFilter = "all" | "analyzed" | "unanalyzed" | "ai_key";
type TrustQuickFilter = "all" | "high" | "medium" | "low" | "custom";
type FocusQuickFilter = "all" | "important" | "exclude" | "custom";

type EventFilters = {
  q: string;
  q_mode: QueryMode;
  source_keys: string[];
  source_groups: string[];
  categories: string[];
  severities: string[];
  priority_tiers: string[];
  statuses: string[];
  symbols: string[];
  chains: string[];
  languages: string[];
  official_only?: boolean;
  has_ai_summary?: boolean;
  minimum_trust_score?: number;
  maximum_trust_score?: number;
  minimum_priority_score?: number;
  maximum_priority_score?: number;
  minimum_ai_importance_score?: number;
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

type ActiveAiJob = {
  jobId: string;
  eventIds: number[];
  status: AiJobStatus;
  createdAtMs: number;
  inputQuality?: string | null;
  queueWaitMs?: number | null;
  providerLatencyMs?: number | null;
  totalLatencyMs?: number | null;
  retryCount?: number;
  error?: string | null;
};

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
  { value: "derivatives_delisting", label: "合约下线" },
  { value: "security_incident", label: "安全事件" },
  { value: "hack_security", label: "黑客安全" },
  { value: "regulatory", label: "政策监管" },
  { value: "policy_regulatory", label: "政策监管" },
  { value: "fundraising", label: "融资" },
  { value: "funding", label: "融资" },
  { value: "market", label: "市场动态" },
  { value: "exchange", label: "交易所" },
  { value: "wallet_maintenance", label: "钱包维护" },
  { value: "deposit_withdrawal", label: "充提公告" },
  { value: "system_maintenance", label: "系统维护" },
  { value: "trading_rule", label: "交易规则" },
  { value: "product", label: "产品更新" },
  { value: "project_update", label: "项目更新" },
  { value: "token_unlock", label: "代币解锁" },
  { value: "exchange_repost", label: "交易所转载" },
  { value: "newsflash", label: "快讯" },
  { value: "deep_article", label: "深度文章" },
  { value: "onchain", label: "链上数据" }
];

const sourceGroupFallback = [
  { value: "exchange_official", label: "交易所官方" },
  { value: "project_official", label: "项目官方" },
  { value: "project_news", label: "项目新闻" },
  { value: "media_zh", label: "中文媒体" },
  { value: "media_en", label: "英文媒体" },
  { value: "regulator", label: "监管源" },
  { value: "regulator_official", label: "监管官方" },
  { value: "protocol", label: "协议官方" },
  { value: "onchain", label: "链上数据" },
  { value: "legacy", label: "旧版来源" }
];

const sourceLabelMap: Record<string, string> = {
  coinbase_exchange: "Coinbase 交易所官方",
  binance: "币安",
  binance_announcements: "币安官方公告",
  kraken: "Kraken 官方公告",
  kraken_announcements: "Kraken 官方公告",
  bitget: "Bitget 官方公告",
  bitget_announcements: "Bitget 官方公告",
  okx: "OKX 官方公告",
  okx_announcements: "OKX 官方公告",
  bybit: "Bybit 官方公告",
  bybit_announcements: "Bybit 官方公告",
  bitstamp_announcements: "Bitstamp 官方公告",
  gate_announcements: "Gate 官方公告",
  mexc: "抹茶官方公告",
  mexc_announcements: "抹茶官方公告",
  hashkey_announcements: "HashKey 官方公告",
  kucoin_announcements: "KuCoin 官方公告",
  upbit_announcements: "Upbit 官方公告",
  htx_announcements: "HTX 官方公告",
  crypto_com_exchange_announcements: "Crypto.com 交易所公告",
  blockbeats_newsflash: "律动快讯",
  foresight_news: "Foresight News 媒体",
  panews_news: "PANews 媒体",
  odaily_newsflash: "星球日报快讯",
  chaincatcher_news: "ChainCatcher 媒体",
  techflow_news: "深潮 TechFlow",
  jinse_news: "金色财经",
  coindesk_rss: "CoinDesk 媒体",
  theblock_rss: "The Block 媒体",
  decrypt_rss: "Decrypt 媒体",
  cointelegraph_rss: "Cointelegraph 媒体",
  aster_medium: "Aster 官方 Medium",
  aster_product_releases: "Aster 产品更新",
  aster_api_docs_commits: "Aster API 文档更新",
  hyperliquid_telegram_announcements: "Hyperliquid 官方公告",
  backpack_blog: "Backpack 官方博客",
  backpack_status: "Backpack 状态公告",
  hyperliquid_news_search: "HYPE 新闻搜索",
  aster_news_search: "Aster 新闻搜索",
  backpack_news_search: "Backpack BP 新闻搜索",
  binance_listing: "币安上币公告",
  okx_listing: "OKX 上币公告",
  sec_press: "SEC 监管公告",
  cftc_press: "CFTC 监管公告",
  ethereum_blog: "以太坊官方博客",
  defillama_hacks: "DefiLlama 黑客事件",
  coindesk: "CoinDesk 媒体",
  theblock: "The Block 媒体",
  decrypt: "Decrypt 媒体",
  cointelegraph: "Cointelegraph 媒体"
};

const symbolLabelMap: Record<string, string> = {
  BTC: "比特币 BTC",
  ETH: "以太坊 ETH",
  SOL: "Solana SOL",
  BNB: "BNB",
  XRP: "瑞波 XRP",
  DOGE: "狗狗币 DOGE",
  ADA: "卡尔达诺 ADA",
  TON: "TON",
  TRX: "波场 TRX",
  AVAX: "雪崩 AVAX",
  OP: "Optimism OP",
  ARB: "Arbitrum ARB",
  HYPE: "Hyperliquid HYPE",
  ASTER: "Aster ASTER",
  BP: "Backpack BP",
  USDT: "泰达币 USDT",
  USDC: "USDC"
};

const chainLabelMap: Record<string, string> = {
  bitcoin: "比特币链",
  btc: "比特币链",
  ethereum: "以太坊",
  eth: "以太坊",
  solana: "Solana 链",
  bnb: "BNB Chain",
  "bnb chain": "BNB Chain",
  base: "Base 链",
  arbitrum: "Arbitrum 链",
  optimism: "Optimism 链",
  polygon: "Polygon 链",
  avalanche: "Avalanche 链",
  tron: "波场",
  ton: "TON 链",
  hyperliquid: "Hyperliquid 链",
  aster: "Aster",
  backpack: "Backpack"
};

const languageOptions = [
  { value: "zh", label: "中文" },
  { value: "en", label: "英文" }
];

const qModeOptions = [
  { value: "all", label: "全部关键词" },
  { value: "any", label: "任意关键词" },
  { value: "phrase", label: "短语匹配" }
];

const publishedQuickOptions = [
  { value: "all", label: "全部时间" },
  { value: "1h", label: "1小时内" },
  { value: "6h", label: "6小时内" },
  { value: "24h", label: "24小时内" },
  { value: "7d", label: "7天内" },
  { value: "custom", label: "自定义时间" }
];

const aiQuickOptions = [
  { value: "all", label: "全部 AI" },
  { value: "analyzed", label: "已分析" },
  { value: "unanalyzed", label: "未分析" },
  { value: "ai_key", label: "有 AI 重点" }
];

const trustQuickOptions = [
  { value: "all", label: "全部可信度" },
  { value: "high", label: "高 80+" },
  { value: "medium", label: "中 60-79" },
  { value: "low", label: "低 <60" },
  { value: "custom", label: "自定义可信度", disabled: true }
];

const focusQuickOptions = [
  { value: "important", label: "只看重点" },
  { value: "all", label: "全部重点" },
  { value: "exclude", label: "排除重点" },
  { value: "custom", label: "自定义重点", disabled: true }
];

const sortOptions = [
  { value: "first_seen_at", label: "首次发现时间" },
  { value: "published_at", label: "发布时间" },
  { value: "severity", label: "级别" },
  { value: "trust_score", label: "可信度" },
  { value: "priority_score", label: "重点分" }
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
  const [activeAiJob, setActiveAiJob] = useState<ActiveAiJob | null>(null);
  const [aiJobTimedOut, setAiJobTimedOut] = useState(false);
  const [reportedTerminalJobId, setReportedTerminalJobId] = useState<string | null>(null);
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
        minimum_trust_score: filters.minimum_trust_score ?? 0,
        maximum_trust_score: filters.maximum_trust_score ?? 100,
        minimum_priority_score: filters.minimum_priority_score ?? 0,
        maximum_priority_score: filters.maximum_priority_score ?? 100,
        minimum_ai_importance_score: filters.minimum_ai_importance_score ?? 0
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
    mutationFn: (eventIds: number[]) => requestBatchAiSummary(eventIds, csrf),
    onSuccess: (result, eventIds) => {
      setSelectedRowKeys([]);
      handleAiSubmitResult(result, eventIds, "已提交 AI 整理任务");
    },
    onError: (error) => {
      message.error(aiJobErrorMessage(error));
    }
  });

  const summarizeSingle = useMutation({
    mutationFn: (eventId: number) => requestEventAiSummary(eventId, csrf),
    onSuccess: (result, eventId) => {
      handleAiSubmitResult(result, [eventId], "已提交重新生成任务");
    },
    onError: (error) => {
      message.error(aiJobErrorMessage(error));
    }
  });

  const aiJobQuery = useQuery({
    queryKey: ["ai-job", activeAiJob?.jobId],
    queryFn: () => getAiJob(activeAiJob!.jobId),
    enabled: Boolean(
      activeAiJob?.jobId &&
        documentVisible &&
        !aiJobTimedOut &&
        !isTerminalAiJobStatus(activeAiJob.status)
    ),
    retry: false,
    staleTime: 0,
    refetchInterval: (query) =>
      aiJobPollInterval(
        activeAiJob?.createdAtMs,
        query.state.data?.status ?? activeAiJob?.status,
        documentVisible
      ),
    refetchIntervalInBackground: false
  });

  const insightQuery = useQuery({
    queryKey: ["event-ai-insight", selected?.id],
    queryFn: () => api<EventAiInsight | null>(`/api/admin/events/${selected?.id}/ai-insight`),
    enabled: Boolean(selected?.id),
    retry: false,
    staleTime: QUERY_STALE_TIME.eventInsight
  });

  const pipelineQuery = useQuery({
    queryKey: ["event-pipeline", selected?.id],
    queryFn: () => getEventPipeline(selected!.id),
    enabled: Boolean(selected?.id),
    retry: false,
    staleTime: 5_000,
    refetchInterval: (query) => {
      if (!documentVisible) {
        return false;
      }
      const pipeline = normalizeEventPipeline(query.state.data);
      return pipeline.items.some((item) => ["queued", "started", "retrying", "sending"].includes(item.status))
        ? 5_000
        : false;
    },
    refetchIntervalInBackground: false
  });

  const selectedPipeline = useMemo(() => normalizeEventPipeline(pipelineQuery.data), [pipelineQuery.data]);

  useEffect(() => {
    if (!activeAiJob || isTerminalAiJobStatus(activeAiJob.status)) {
      return undefined;
    }
    const remainingMs = Math.max(0, 90_000 - (Date.now() - activeAiJob.createdAtMs));
    const timer = window.setTimeout(() => {
      setAiJobTimedOut(true);
      setActiveAiJob((current) =>
        current?.jobId === activeAiJob.jobId
          ? { ...current, status: "failed", error: "AI 任务超过 90 秒仍未完成，请检查 Worker 或稍后重试。" }
          : current
      );
      message.error("AI 任务超过 90 秒仍未完成，请检查 Worker 或稍后重试。");
    }, remainingMs);
    return () => window.clearTimeout(timer);
  }, [activeAiJob?.createdAtMs, activeAiJob?.jobId, activeAiJob?.status]);

  useEffect(() => {
    if (!activeAiJob || !aiJobQuery.error || reportedTerminalJobId === activeAiJob.jobId) {
      return;
    }
    const errorMessage = aiJobErrorMessage(aiJobQuery.error);
    setReportedTerminalJobId(activeAiJob.jobId);
    setActiveAiJob((current) =>
      current?.jobId === activeAiJob.jobId ? { ...current, status: "failed", error: errorMessage } : current
    );
    message.error(errorMessage);
  }, [activeAiJob, aiJobQuery.error, reportedTerminalJobId]);

  useEffect(() => {
    if (!activeAiJob || !aiJobQuery.data) {
      return;
    }

    const job = aiJobQuery.data;
    const status = normalizeAiJobStatus(job.status);
    const eventIds = normalizeJobEventIds(job, activeAiJob.eventIds);
    const sanitizedError = normalizeAiJobErrorMessage(job.error_message_sanitized ?? job.error_sanitized);
    setActiveAiJob((current) =>
      current?.jobId === activeAiJob.jobId
        ? {
            ...current,
            status,
            eventIds,
            inputQuality: job.input_quality ?? current.inputQuality,
            queueWaitMs: job.queue_wait_ms ?? current.queueWaitMs,
            providerLatencyMs: job.provider_latency_ms ?? current.providerLatencyMs,
            totalLatencyMs: job.total_latency_ms ?? current.totalLatencyMs,
            retryCount: job.retry_count ?? current.retryCount,
            error: sanitizedError ?? current.error
          }
        : current
    );

    if (!isTerminalAiJobStatus(status) || reportedTerminalJobId === activeAiJob.jobId) {
      return;
    }

    setReportedTerminalJobId(activeAiJob.jobId);
    if (status === "succeeded") {
      if (job.insight?.event_id) {
        queryClient.setQueryData(["event-ai-insight", job.insight.event_id], job.insight);
      }
      eventIds.forEach((eventId) => {
        queryClient.invalidateQueries({ queryKey: ["event-ai-insight", eventId], exact: true });
        queryClient.invalidateQueries({ queryKey: ["event-pipeline", eventId], exact: true });
      });
      queryClient.invalidateQueries({ queryKey: ["ai-job", activeAiJob.jobId], exact: true });
      queryClient.invalidateQueries({ queryKey: ["events", filters], exact: true });
      message.success("AI 整理已完成");
      return;
    }

    eventIds.forEach((eventId) => {
      queryClient.invalidateQueries({ queryKey: ["event-pipeline", eventId], exact: true });
    });
    message.error(sanitizedError ?? "AI 整理任务失败，请检查 Worker 状态后重试。");
  }, [activeAiJob, aiJobQuery.data, filters, queryClient, reportedTerminalJobId]);

  const data = eventsQuery.data ?? {
    items: [],
    total: 0,
    page: filters.page,
    page_size: filters.page_size
  };

  const headerFilterIcon = (filtered: boolean) => <FilterOutlined style={{ color: filtered ? "#1677ff" : undefined }} />;

  const columns = useMemo<TableProps<EventRow>["columns"]>(
    () => [
      {
        title: "标题",
        dataIndex: "display_title",
        ellipsis: true,
        filteredValue: filters.q ? [filters.q] : null,
        filterIcon: headerFilterIcon,
        filterDropdown: () => (
          <div className="table-filter-dropdown" onKeyDown={(event) => event.stopPropagation()}>
            <Typography.Text strong>标题搜索</Typography.Text>
            <Input
              allowClear
              prefix={<SearchOutlined />}
              placeholder="搜索标题、摘要、币种、链、来源、AI 标签"
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              className="event-search-input"
            />
            <Select
              value={filters.q_mode}
              options={qModeOptions}
              className="table-filter-control"
              onChange={(q_mode) => updateFilters({ q_mode, page: 1 })}
            />
            <Button
              size="small"
              icon={<ClearOutlined />}
              onClick={() => {
                setKeyword("");
                updateFilters({ q: "", q_mode: "all", page: 1 });
              }}
            >
              清空标题筛选
            </Button>
          </div>
        ),
        render: (_, row) => (
          <Space direction="vertical" size={2} className="event-title-cell">
            <Button type="link" className="event-title-link" onClick={() => setSelected(row)}>
              {eventDisplayTitle(row)}
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
        filteredValue: filters.source_keys.length || filters.source_groups.length || filters.official_only ? ["active"] : null,
        filterIcon: headerFilterIcon,
        filterDropdown: () => (
          <div className="table-filter-dropdown" onKeyDown={(event) => event.stopPropagation()}>
            <Typography.Text strong>来源筛选</Typography.Text>
            <Select
              mode="multiple"
              allowClear
              showSearch
              maxTagCount="responsive"
              placeholder="来源"
              value={filters.source_keys}
              options={facetOptions(facetsQuery.data?.source_keys, [], sourceFacetLabel)}
              className="table-filter-control"
              onChange={(source_keys: string[]) => updateFilters({ source_keys, page: 1 })}
            />
            <Select
              mode="multiple"
              allowClear
              maxTagCount="responsive"
              placeholder="来源分组"
              value={filters.source_groups}
              options={facetOptions(facetsQuery.data?.source_groups, sourceGroupFallback)}
              className="table-filter-control"
              onChange={(source_groups: string[]) => updateFilters({ source_groups, page: 1 })}
            />
            <Select
              value={filters.official_only ? "official" : "all"}
              options={[
                { value: "all", label: "全部来源" },
                { value: "official", label: "仅官方来源" }
              ]}
              className="table-filter-control"
              onChange={(value) => updateFilters({ official_only: value === "official" ? true : undefined, page: 1 })}
            />
          </div>
        ),
        render: (_, row) => (
          <Space direction="vertical" size={0}>
            <span>{eventSourceLabel(row)}</span>
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
        filteredValue: filters.categories.length ? filters.categories : null,
        filterIcon: headerFilterIcon,
        filterDropdown: () => (
          <div className="table-filter-dropdown" onKeyDown={(event) => event.stopPropagation()}>
            <Typography.Text strong>分类筛选</Typography.Text>
            <Select
              mode="multiple"
              allowClear
              maxTagCount="responsive"
              placeholder="分类"
              value={filters.categories}
              options={facetOptions(facetsQuery.data?.categories, categoryOptions, categoryFacetLabel)}
              className="table-filter-control"
              onChange={(categories: string[]) => updateFilters({ categories, page: 1 })}
            />
          </div>
        ),
        render: (_, row) => eventCategoryLabel(row)
      },
      {
        title: "级别",
        dataIndex: "severity_label",
        width: 100,
        filteredValue: filters.severities.length ? filters.severities : null,
        filterIcon: headerFilterIcon,
        filterDropdown: () => (
          <div className="table-filter-dropdown" onKeyDown={(event) => event.stopPropagation()}>
            <Typography.Text strong>级别筛选</Typography.Text>
            <Select
              mode="multiple"
              allowClear
              maxTagCount="responsive"
              placeholder="级别"
              value={filters.severities}
              options={facetOptions(facetsQuery.data?.severities, severityOptions)}
              className="table-filter-control"
              onChange={(severities: string[]) => updateFilters({ severities, page: 1 })}
            />
          </div>
        ),
        render: (_, row) => <Tag color={severityColor(row.severity)}>{eventSeverityLabel(row)}</Tag>
      },
      {
        title: "AI",
        dataIndex: "ai_summary_status",
        width: 170,
        filteredValue: aiQuickFilter(filters) !== "all" ? [aiQuickFilter(filters)] : null,
        filterIcon: headerFilterIcon,
        filterDropdown: () => (
          <div className="table-filter-dropdown" onKeyDown={(event) => event.stopPropagation()}>
            <Typography.Text strong>AI 筛选</Typography.Text>
            <Select
              value={aiQuickFilter(filters)}
              options={aiQuickOptions}
              className="table-filter-control"
              onChange={(value) => applyAiQuickFilter(value as AiQuickFilter)}
            />
          </div>
        ),
        render: (_, row) => {
          const rowJob = activeAiJob?.eventIds.includes(row.id) ? activeAiJob : undefined;
          return (
            <Space direction="vertical" size={2}>
              {rowJob ? (
                <Tag color={aiJobStatusColor(rowJob.status)}>{aiJobStatusText(rowJob.status)}</Tag>
              ) : row.has_ai_summary || row.ai_summary_status === "completed" ? (
                <Tag color="green">已整理</Tag>
              ) : (
                <Tag>未整理</Tag>
              )}
              {shouldWarnInputQuality(rowJob?.inputQuality) ? (
                <Typography.Text type="warning">输入信息较少</Typography.Text>
              ) : null}
              {typeof row.ai_importance_score === "number" ? (
                <Typography.Text type="secondary">重要度 {row.ai_importance_score}</Typography.Text>
              ) : null}
              {row.ai_risk_level ? (
                <Tag color={riskColor[row.ai_risk_level] ?? "default"}>{riskText[row.ai_risk_level] ?? row.ai_risk_level}</Tag>
              ) : null}
            </Space>
          );
        }
      },
      {
        title: "币种/链",
        dataIndex: "symbols",
        width: 160,
        filteredValue: filters.symbols.length || filters.chains.length ? ["active"] : null,
        filterIcon: headerFilterIcon,
        filterDropdown: () => (
          <div className="table-filter-dropdown" onKeyDown={(event) => event.stopPropagation()}>
            <Typography.Text strong>币种/链筛选</Typography.Text>
            <Select
              mode="tags"
              allowClear
              showSearch
              maxTagCount="responsive"
              tokenSeparators={[",", " "]}
              placeholder="币种"
              value={filters.symbols}
              options={facetOptions(facetsQuery.data?.symbols, [], symbolFacetLabel)}
              className="table-filter-control"
              onChange={(symbols: string[]) => updateFilters({ symbols: symbols.map((item) => item.toUpperCase()), page: 1 })}
            />
            <Select
              mode="tags"
              allowClear
              showSearch
              maxTagCount="responsive"
              tokenSeparators={[",", " "]}
              placeholder="链"
              value={filters.chains}
              options={facetOptions(facetsQuery.data?.chains, [], chainFacetLabel)}
              className="table-filter-control"
              onChange={(chains: string[]) => updateFilters({ chains, page: 1 })}
            />
          </div>
        ),
        render: (_, row) => (
          <Space wrap size={[4, 4]}>
            {(row.symbols ?? []).slice(0, 3).map((symbol) => (
              <Tag key={symbol}>{symbolFacetLabel(symbol)}</Tag>
            ))}
            {(row.chains ?? []).slice(0, 2).map((chain) => (
              <Tag key={chainFacetLabel(chain)} color="cyan">
                {chain}
              </Tag>
            ))}
          </Space>
        )
      },
      {
        title: "可信度",
        dataIndex: "trust_score",
        width: 90,
        filteredValue: trustQuickFilter(filters) !== "all" ? [trustQuickFilter(filters)] : null,
        filterIcon: headerFilterIcon,
        filterDropdown: () => (
          <div className="table-filter-dropdown" onKeyDown={(event) => event.stopPropagation()}>
            <Typography.Text strong>可信度筛选</Typography.Text>
            <Select
              value={trustQuickFilter(filters)}
              options={trustQuickOptions}
              className="table-filter-control"
              onChange={(value) => applyTrustQuickFilter(value as TrustQuickFilter)}
            />
          </div>
        )
      },
      {
        title: "重点",
        dataIndex: "priority_score",
        width: 110,
        filteredValue: focusQuickFilter(filters) !== "all" ? [focusQuickFilter(filters)] : null,
        filterIcon: headerFilterIcon,
        filterDropdown: () => (
          <div className="table-filter-dropdown" onKeyDown={(event) => event.stopPropagation()}>
            <Typography.Text strong>重点筛选</Typography.Text>
            <Select
              value={focusQuickFilter(filters)}
              options={focusQuickOptions}
              className="table-filter-control"
              onChange={(value) => applyFocusQuickFilter(value as FocusQuickFilter)}
            />
          </div>
        ),
        render: (_, row) => (
          <Space direction="vertical" size={2}>
            <Tag color={priorityColor(row.priority_score)}>{row.priority_tier ?? "-"} {row.priority_score ?? 0}</Tag>
            <Typography.Text type="secondary">源 {row.source_count ?? row.confirmation_count ?? 1}</Typography.Text>
          </Space>
        )
      },
      {
        title: "发布时间",
        dataIndex: "published_at",
        width: 240,
        filteredValue: publishedQuickRange(filters) !== "all" ? [publishedQuickRange(filters)] : null,
        filterIcon: headerFilterIcon,
        filterDropdown: () => (
          <div className="table-filter-dropdown" onKeyDown={(event) => event.stopPropagation()}>
            <Typography.Text strong>发布时间筛选</Typography.Text>
            <Select
              value={publishedQuickRange(filters)}
              options={publishedQuickOptions}
              className="table-filter-control"
              onChange={(range) => applyPublishedQuickRange(range as PublishedQuickRange)}
            />
          </div>
        ),
        render: (_, row) => (
          <Space direction="vertical" size={0}>
            <span>{formatUtc8Time(row.published_at)}</span>
            <Typography.Text type="secondary">获取：{formatUtc8Time(row.first_seen_at)}</Typography.Text>
          </Space>
        )
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
    [activeAiJob, facetsQuery.data, filters, keyword]
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

          {activeAiJob ? <AiJobStatusAlert job={activeAiJob} loading={aiJobQuery.isFetching} /> : null}

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
        destroyOnHidden
        forceRender
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
                    maximum_trust_score:
                      typeof values.maximum_trust_score === "number" && values.maximum_trust_score < 100
                        ? values.maximum_trust_score
                        : undefined,
                    minimum_priority_score:
                      typeof values.minimum_priority_score === "number" && values.minimum_priority_score > 0
                        ? values.minimum_priority_score
                        : undefined,
                    maximum_priority_score:
                      typeof values.maximum_priority_score === "number" && values.maximum_priority_score < 100
                        ? values.maximum_priority_score
                        : undefined,
                    minimum_ai_importance_score:
                      typeof values.minimum_ai_importance_score === "number" && values.minimum_ai_importance_score > 0
                        ? values.minimum_ai_importance_score
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
            <Select mode="multiple" allowClear showSearch options={facetOptions(facetsQuery.data?.source_keys, [], sourceFacetLabel)} />
          </Form.Item>
          <Form.Item label="来源分组" name="source_groups">
            <Select
              mode="multiple"
              allowClear
              options={facetOptions(facetsQuery.data?.source_groups, sourceGroupFallback)}
            />
          </Form.Item>
          <Form.Item label="分类" name="categories">
            <Select mode="multiple" allowClear options={facetOptions(facetsQuery.data?.categories, categoryOptions, categoryFacetLabel)} />
          </Form.Item>
          <Form.Item label="级别" name="severities">
            <Select mode="multiple" allowClear options={facetOptions(facetsQuery.data?.severities, severityOptions)} />
          </Form.Item>
          <Form.Item label="状态" name="statuses">
            <Select mode="multiple" allowClear options={facetOptions(facetsQuery.data?.statuses, statusOptions)} />
          </Form.Item>
          <Form.Item label="币种" name="symbols">
            <Select mode="tags" allowClear tokenSeparators={[",", " "]} options={facetOptions(facetsQuery.data?.symbols, [], symbolFacetLabel)} />
          </Form.Item>
          <Form.Item label="链" name="chains">
            <Select mode="tags" allowClear tokenSeparators={[",", " "]} options={facetOptions(facetsQuery.data?.chains, [], chainFacetLabel)} />
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
          <Form.Item label="最低重点分" name="minimum_priority_score">
            <Slider min={0} max={100} marks={{ 0: "0", 60: "60", 85: "85", 100: "100" }} />
          </Form.Item>
          <Form.Item label="最高重点分" name="maximum_priority_score">
            <Slider min={0} max={100} marks={{ 0: "0", 59: "59", 100: "100" }} />
          </Form.Item>
          <Form.Item label="最低可信度" name="minimum_trust_score">
            <Slider min={0} max={100} marks={{ 0: "0", 60: "60", 80: "80", 100: "100" }} />
          </Form.Item>
          <Form.Item label="最高可信度" name="maximum_trust_score">
            <Slider min={0} max={100} marks={{ 0: "0", 59: "59", 79: "79", 100: "100" }} />
          </Form.Item>
          <Form.Item label="最低 AI 重要度" name="minimum_ai_importance_score">
            <Slider min={0} max={100} marks={{ 0: "0", 60: "60", 85: "85", 100: "100" }} />
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
        forceRender
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
              <Descriptions.Item label="标题">{eventDisplayTitle(selected)}</Descriptions.Item>
              <Descriptions.Item label="摘要">{selected.display_summary || "暂无摘要"}</Descriptions.Item>
              <Descriptions.Item label="来源">{eventSourceLabel(selected)}</Descriptions.Item>
              <Descriptions.Item label="分类">{eventCategoryLabel(selected)}</Descriptions.Item>
              <Descriptions.Item label="级别">{eventSeverityLabel(selected)}</Descriptions.Item>
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
              job={activeAiJob?.eventIds.includes(selected.id) ? activeAiJob : undefined}
            />
            <EventPipelinePanel
              pipeline={selectedPipeline}
              loading={pipelineQuery.isLoading}
              error={pipelineQuery.isError}
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

  function applyPublishedQuickRange(range: PublishedQuickRange) {
    if (range === "custom") {
      setFilterOpen(true);
      return;
    }
    updateFilters(publishedQuickPatch(range));
  }

  function applyAiQuickFilter(value: AiQuickFilter) {
    if (value === "analyzed") {
      updateFilters({ has_ai_summary: true, minimum_ai_importance_score: undefined, page: 1 });
      return;
    }
    if (value === "unanalyzed") {
      updateFilters({ has_ai_summary: false, minimum_ai_importance_score: undefined, page: 1 });
      return;
    }
    if (value === "ai_key") {
      updateFilters({ has_ai_summary: true, minimum_ai_importance_score: 60, page: 1 });
      return;
    }
    updateFilters({ has_ai_summary: undefined, minimum_ai_importance_score: undefined, page: 1 });
  }

  function applyTrustQuickFilter(value: TrustQuickFilter) {
    if (value === "custom") {
      setFilterOpen(true);
      return;
    }
    updateFilters(trustQuickPatch(value));
  }

  function applyFocusQuickFilter(value: FocusQuickFilter) {
    if (value === "custom") {
      setFilterOpen(true);
      return;
    }
    updateFilters(focusQuickPatch(value));
  }

  function handleAiSubmitResult(result: unknown, eventIds: number[], successText: string) {
    const typedResult = result as Parameters<typeof normalizeAiJobFromSubmitResponse>[0];
    if (isAiInsightResponse(typedResult)) {
      queryClient.setQueryData(["event-ai-insight", typedResult.event_id], typedResult);
      queryClient.invalidateQueries({ queryKey: ["event-ai-insight", typedResult.event_id], exact: true });
      queryClient.invalidateQueries({ queryKey: ["event-pipeline", typedResult.event_id], exact: true });
      queryClient.invalidateQueries({ queryKey: ["events", filters], exact: true });
      message.success("AI 摘要已生成");
      return;
    }

    const job = normalizeAiJobFromSubmitResponse(typedResult);
    if (!job?.job_id) {
      message.warning("AI 任务已提交，但后端未返回 job_id，无法追踪任务状态。");
      return;
    }

    const nextEventIds = normalizeJobEventIds(job, eventIds);
    setAiJobTimedOut(false);
    setReportedTerminalJobId(null);
    setActiveAiJob({
      jobId: job.job_id,
      eventIds: nextEventIds,
      status: normalizeAiJobStatus(job.status),
      createdAtMs: Date.now(),
      inputQuality: job.input_quality,
      queueWaitMs: job.queue_wait_ms,
      providerLatencyMs: job.provider_latency_ms,
      totalLatencyMs: job.total_latency_ms,
      retryCount: job.retry_count,
      error: normalizeAiJobErrorMessage(job.error_message_sanitized ?? job.error_sanitized)
    });
    queryClient.setQueryData(["ai-job", job.job_id], job);
    nextEventIds.forEach((eventId) => {
      queryClient.invalidateQueries({ queryKey: ["event-ai-insight", eventId], exact: true });
      queryClient.invalidateQueries({ queryKey: ["event-pipeline", eventId], exact: true });
    });
    message.success(successText);
    if (shouldWarnInputQuality(job.input_quality)) {
      message.warning("输入信息较少，AI 结果可能不完整。");
    }
  }
}

function AiInsightPanel({
  insight,
  loading,
  error,
  fallback,
  job
}: {
  insight?: EventAiInsight | null;
  loading: boolean;
  error: boolean;
  fallback: EventRow;
  job?: ActiveAiJob;
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
      {job ? <AiJobStatusAlert job={job} compact /> : null}
      {shouldWarnInputQuality(job?.inputQuality ?? insight?.input_quality) ? (
        <Alert type="warning" showIcon message="输入信息较少，AI 结果可能不完整。" />
      ) : null}
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
            {typeof totalTokens === "number" ? <Tag>用量 {totalTokens}</Tag> : null}
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

function EventPipelinePanel({
  pipeline,
  loading,
  error
}: {
  pipeline: NormalizedEventPipeline;
  loading: boolean;
  error: boolean;
}) {
  const previewText = formatPipelinePreview(pipeline.cardPreview);

  if (loading) {
    return <Card title="处理时间线" loading />;
  }

  if (error) {
    return (
      <Card title="处理时间线">
        <Alert
          type="info"
          showIcon
          message="处理时间线暂不可用"
          description="已调用 GET /api/admin/events/{event_id}/pipeline，后端路由未接入或当前不可用。"
        />
      </Card>
    );
  }

  return (
    <Card title="处理时间线">
      <Space direction="vertical" size={16} className="page-stack">
        {pipeline.items.length ? (
          <Timeline
            items={pipeline.items.map((item) => ({
              key: item.id,
              color: pipelineTimelineColor(item.status, item.stage),
              children: (
                <Space direction="vertical" size={4} className="page-stack">
                  <Space wrap>
                    <Typography.Text strong>{item.title}</Typography.Text>
                    <Tag color={pipelineStatusColor(item.status, item.stage)}>{item.statusLabel}</Tag>
                    <Typography.Text type="secondary">{item.stageLabel}</Typography.Text>
                  </Space>
                  {item.time ? <Typography.Text type="secondary">{formatTime(item.time)}</Typography.Text> : null}
                  {item.description ? <Typography.Text>{item.description}</Typography.Text> : null}
                  {item.error ? <Typography.Text type="danger">{item.error}</Typography.Text> : null}
                  {item.jobId || item.deliveryId || item.retryCount ? (
                    <Space wrap size={[4, 4]}>
                      {item.jobId ? <Tag>任务 {redactSensitiveText(item.jobId)}</Tag> : null}
                      {item.deliveryId ? <Tag>投递 {redactSensitiveText(String(item.deliveryId))}</Tag> : null}
                      {item.retryCount ? <Tag>重试 {item.retryCount} 次</Tag> : null}
                    </Space>
                  ) : null}
                </Space>
              )
            }))}
          />
        ) : (
          <Empty description="暂无处理时间线" />
        )}

        {previewText ? (
          <>
            <Divider />
            <Typography.Text strong>卡片预览</Typography.Text>
            <pre
              style={{
                margin: 0,
                maxHeight: 260,
                overflow: "auto",
                padding: 12,
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                background: "#f6f8fa",
                border: "1px solid #edf0f2",
                borderRadius: 6
              }}
            >
              {previewText}
            </pre>
          </>
        ) : null}

        {pipeline.delivery ? <DeliveryStatusPanel delivery={pipeline.delivery} /> : null}
      </Space>
    </Card>
  );
}

function DeliveryStatusPanel({ delivery }: { delivery: EventPipelineDelivery }) {
  const deliveryId = delivery.delivery_id ?? delivery.id;
  const status = String(delivery.status ?? "queued");
  return (
    <>
      <Divider />
      <Typography.Text strong>投递状态</Typography.Text>
      <Descriptions bordered column={1} size="small">
        {deliveryId ? <Descriptions.Item label="投递编号">{redactSensitiveText(String(deliveryId))}</Descriptions.Item> : null}
        <Descriptions.Item label="状态">
          <Space wrap>
            <Tag color={pipelineStatusColor(status, "feishu")}>{pipelineStatusText("feishu", status)}</Tag>
            {delivery.dry_run ? <Tag color="warning">未实发</Tag> : null}
          </Space>
        </Descriptions.Item>
        {delivery.channel ? <Descriptions.Item label="通道">{delivery.channel}</Descriptions.Item> : null}
        {delivery.target ? <Descriptions.Item label="目标">{delivery.target}</Descriptions.Item> : null}
        {typeof delivery.attempts === "number" ? <Descriptions.Item label="尝试次数">{delivery.attempts}</Descriptions.Item> : null}
        {typeof delivery.response_status === "number" ? (
          <Descriptions.Item label="响应状态">{delivery.response_status}</Descriptions.Item>
        ) : null}
        {delivery.provider_message_id ? (
          <Descriptions.Item label="回执">{delivery.provider_message_id}</Descriptions.Item>
        ) : null}
        {delivery.delivered_at ? <Descriptions.Item label="送达时间">{formatTime(delivery.delivered_at)}</Descriptions.Item> : null}
        {delivery.last_error ? <Descriptions.Item label="错误">{delivery.last_error}</Descriptions.Item> : null}
        {delivery.suppressed_reason ? (
          <Descriptions.Item label="抑制原因">{redactSensitiveText(delivery.suppressed_reason)}</Descriptions.Item>
        ) : null}
      </Descriptions>
    </>
  );
}

function AiJobStatusAlert({
  job,
  loading = false,
  compact = false
}: {
  job: ActiveAiJob;
  loading?: boolean;
  compact?: boolean;
}) {
  const status = normalizeAiJobStatus(job.status);
  const terminal = isTerminalAiJobStatus(status);
  const qualityText = inputQualityText(job.inputQuality);
  const error = normalizeAiJobErrorMessage(job.error);
  const descriptionItems = [
    qualityText ? `输入质量：${qualityText}` : undefined,
    typeof job.queueWaitMs === "number" ? `排队 ${job.queueWaitMs}ms` : undefined,
    typeof job.providerLatencyMs === "number" ? `模型 ${job.providerLatencyMs}ms` : undefined,
    typeof job.totalLatencyMs === "number" ? `总耗时 ${job.totalLatencyMs}ms` : undefined,
    typeof job.retryCount === "number" && job.retryCount > 0 ? `重试 ${job.retryCount} 次` : undefined,
    error && status === "failed" ? error : undefined
  ].filter(Boolean);

  return (
    <Alert
      className={compact ? undefined : "ai-job-status-alert"}
      type={status === "failed" ? "error" : status === "succeeded" ? "success" : "info"}
      showIcon
      message={
        <Space wrap>
          <span>AI 任务</span>
          <Tag color={aiJobStatusColor(status)}>{aiJobStatusText(status)}</Tag>
          {loading && !terminal ? <Tag color="processing">正在刷新</Tag> : null}
          <Typography.Text type="secondary">任务 {job.jobId}</Typography.Text>
        </Space>
      }
      description={descriptionItems.length ? descriptionItems.join(" · ") : undefined}
    />
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
    priority_tiers: [],
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
    priority_tiers: getArrayParam(params, "priority_tiers"),
    statuses: getArrayParam(params, "statuses"),
    symbols: getArrayParam(params, "symbols").map((item) => item.toUpperCase()),
    chains: getArrayParam(params, "chains"),
    languages: getArrayParam(params, "languages"),
    official_only: parseBoolean(params.get("official_only")),
    has_ai_summary: parseBoolean(params.get("has_ai_summary")),
    minimum_trust_score: parseNumber(params.get("minimum_trust_score")),
    maximum_trust_score: parseNumber(params.get("maximum_trust_score")),
    minimum_priority_score: parseNumber(params.get("minimum_priority_score")),
    maximum_priority_score: parseNumber(params.get("maximum_priority_score")),
    minimum_ai_importance_score: parseNumber(params.get("minimum_ai_importance_score")),
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
  appendArray(params, "priority_tiers", filters.priority_tiers);
  appendArray(params, "statuses", filters.statuses);
  appendArray(params, "symbols", filters.symbols);
  appendArray(params, "chains", filters.chains);
  appendArray(params, "languages", filters.languages);
  appendIfPresent(params, "official_only", filters.official_only ? "true" : "");
  appendIfPresent(params, "has_ai_summary", typeof filters.has_ai_summary === "boolean" ? String(filters.has_ai_summary) : "");
  appendIfPresent(params, "minimum_trust_score", filters.minimum_trust_score ? String(filters.minimum_trust_score) : "");
  appendIfPresent(params, "maximum_trust_score", filters.maximum_trust_score ? String(filters.maximum_trust_score) : "");
  appendIfPresent(params, "minimum_priority_score", filters.minimum_priority_score ? String(filters.minimum_priority_score) : "");
  appendIfPresent(params, "maximum_priority_score", filters.maximum_priority_score ? String(filters.maximum_priority_score) : "");
  appendIfPresent(
    params,
    "minimum_ai_importance_score",
    filters.minimum_ai_importance_score ? String(filters.minimum_ai_importance_score) : ""
  );
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

type FacetOptionWithLabel = FacetOption | { value: string; label: string };
type FacetLabelResolver = (value: string, item: FacetOptionWithLabel) => string | undefined;

function facetOptions(
  facets?: FacetOption[],
  fallback: Array<{ value: string; label: string }> = [],
  labelResolver?: FacetLabelResolver
) {
  const seen = new Set<string>();
  const fallbackLabels = new Map(fallback.map((item) => [item.value, item.label]));
  const options = [...(facets ?? []), ...fallback].flatMap((item) => {
    const value = item.value ?? ("key" in item ? item.key : undefined);
    if (!value || seen.has(value)) {
      return [];
    }
    seen.add(value);
    const localizedLabel = labelResolver?.(value, item) ?? fallbackLabels.get(value) ?? item.label ?? value;
    const label = "count" in item && typeof item.count === "number" ? `${localizedLabel} (${item.count})` : localizedLabel;
    return [{ value, label }];
  });
  return options;
}

function categoryFacetLabel(value: string) {
  return categoryOptions.find((item) => item.value === value)?.label;
}

function sourceFacetLabel(value: string, item: FacetOptionWithLabel) {
  const mapped = lookupLabel(sourceLabelMap, value);
  if (mapped) {
    return mapped;
  }
  const normalized = value.toLowerCase();
  if (normalized.includes("binance")) return "币安官方公告";
  if (normalized.includes("okx")) return "OKX 官方公告";
  if (normalized.includes("bybit")) return "Bybit 官方公告";
  if (normalized.includes("bitget")) return "Bitget 官方公告";
  if (normalized.includes("mexc")) return "抹茶官方公告";
  if (normalized.includes("coinbase")) return "Coinbase 交易所官方";
  if (normalized.includes("hyperliquid") || normalized.includes("hype")) return "Hyperliquid 相关来源";
  if (normalized.includes("aster")) return "Aster 相关来源";
  if (normalized.includes("backpack") || normalized.includes("bp")) return "Backpack 相关来源";
  if (item.label && hasChinese(item.label)) {
    return item.label;
  }
  return `其他来源：${humanizeFacetValue(value)}`;
}

function symbolFacetLabel(value: string) {
  return lookupLabel(symbolLabelMap, value) ?? `代币：${value.toUpperCase()}`;
}

function chainFacetLabel(value: string) {
  return lookupLabel(chainLabelMap, value) ?? `链：${humanizeFacetValue(value)}`;
}

function lookupLabel(map: Record<string, string>, value: string) {
  return map[value] ?? map[value.toLowerCase()] ?? map[value.toUpperCase()];
}

function hasChinese(value: string) {
  return /[\u3400-\u9fff]/.test(value);
}

function humanizeFacetValue(value: string) {
  return value.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
}

function eventDisplayTitle(row: EventRow) {
  return row.ai_headline_zh || row.display_title || row.title;
}

function eventSourceLabel(row: EventRow) {
  const value = row.source_key || row.source_name;
  if (!value) {
    return "未知来源";
  }
  return sourceFacetLabel(value, { value, label: row.source_name ?? value });
}

function eventCategoryLabel(row: EventRow) {
  return categoryFacetLabel(row.category) ?? row.category_label ?? row.category;
}

function eventSeverityLabel(row: EventRow) {
  return severityOptions.find((item) => item.value === row.severity)?.label ?? row.severity_label ?? row.severity;
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
  if (value === "true") {
    return true;
  }
  if (value === "false") {
    return false;
  }
  return undefined;
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

function publishedQuickRange(filters: EventFilters): PublishedQuickRange {
  if (!filters.published_from && !filters.published_to) {
    return "all";
  }
  if (!filters.published_from || filters.published_to) {
    return "custom";
  }
  const from = Date.parse(filters.published_from);
  if (!Number.isFinite(from)) {
    return "custom";
  }
  const diffMs = Date.now() - from;
  const toleranceMs = 5 * 60 * 1000;
  const candidates: Array<[PublishedQuickRange, number]> = [
    ["1h", 60 * 60 * 1000],
    ["6h", 6 * 60 * 60 * 1000],
    ["24h", 24 * 60 * 60 * 1000],
    ["7d", 7 * 24 * 60 * 60 * 1000]
  ];
  return candidates.find(([, ms]) => Math.abs(diffMs - ms) <= toleranceMs)?.[0] ?? "custom";
}

function publishedQuickPatch(range: Exclude<PublishedQuickRange, "custom">): Partial<EventFilters> {
  if (range === "all") {
    return { published_from: undefined, published_to: undefined, page: 1 };
  }
  const hoursByRange = { "1h": 1, "6h": 6, "24h": 24, "7d": 24 * 7 } as const;
  const from = new Date(Date.now() - hoursByRange[range] * 60 * 60 * 1000).toISOString();
  return { published_from: from, published_to: undefined, sort: "published_at", direction: "desc", page: 1 };
}

function aiQuickFilter(filters: EventFilters): AiQuickFilter {
  if ((filters.minimum_ai_importance_score ?? 0) >= 60) {
    return "ai_key";
  }
  if (filters.has_ai_summary === true) {
    return "analyzed";
  }
  if (filters.has_ai_summary === false) {
    return "unanalyzed";
  }
  return "all";
}

function trustQuickFilter(filters: EventFilters): TrustQuickFilter {
  const min = filters.minimum_trust_score;
  const max = filters.maximum_trust_score;
  if (min === undefined && max === undefined) {
    return "all";
  }
  if ((min ?? 0) >= 80 && max === undefined) {
    return "high";
  }
  if (min === 60 && max === 79) {
    return "medium";
  }
  if (min === undefined && max === 59) {
    return "low";
  }
  return "custom";
}

function trustQuickPatch(value: Exclude<TrustQuickFilter, "custom">): Partial<EventFilters> {
  if (value === "high") {
    return { minimum_trust_score: 80, maximum_trust_score: undefined, page: 1 };
  }
  if (value === "medium") {
    return { minimum_trust_score: 60, maximum_trust_score: 79, page: 1 };
  }
  if (value === "low") {
    return { minimum_trust_score: undefined, maximum_trust_score: 59, page: 1 };
  }
  return { minimum_trust_score: undefined, maximum_trust_score: undefined, page: 1 };
}

function focusQuickFilter(filters: EventFilters): FocusQuickFilter {
  const min = filters.minimum_priority_score;
  const max = filters.maximum_priority_score;
  if (min === undefined && max === undefined) {
    return "all";
  }
  if ((min ?? 0) >= 60 && max === undefined) {
    return "important";
  }
  if (min === undefined && max === 59) {
    return "exclude";
  }
  return "custom";
}

function focusQuickPatch(value: Exclude<FocusQuickFilter, "custom">): Partial<EventFilters> {
  if (value === "important") {
    return { minimum_priority_score: 60, maximum_priority_score: undefined, sort: "priority_score", direction: "desc", page: 1 };
  }
  if (value === "exclude") {
    return { minimum_priority_score: undefined, maximum_priority_score: 59, sort: "first_seen_at", direction: "desc", page: 1 };
  }
  return { minimum_priority_score: undefined, maximum_priority_score: undefined, sort: "first_seen_at", direction: "desc", page: 1 };
}

function priorityColor(value?: number) {
  if (typeof value !== "number") {
    return "default";
  }
  if (value >= 85) {
    return "red";
  }
  if (value >= 70) {
    return "orange";
  }
  if (value >= 55) {
    return "blue";
  }
  return "default";
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

function formatTime(value?: string | null) {
  return formatUtc8Time(value);
}

function formatUtc8Time(value?: string | null) {
  if (!value) {
    return "未知";
  }
  const date = parseBackendUtcTime(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false
  }).format(date);
}

function parseBackendUtcTime(value: string) {
  const trimmed = value.trim();
  const normalized = trimmed.includes("T") ? trimmed : trimmed.replace(" ", "T");
  const hasTimezone = /(?:z|[+-]\d{2}:?\d{2})$/i.test(normalized);
  return new Date(hasTimezone ? normalized : `${normalized}Z`);
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

function normalizeJobEventIds(job: AiJob, fallback: number[]) {
  const eventIds = job.event_ids?.length ? job.event_ids : typeof job.event_id === "number" ? [job.event_id] : fallback;
  return Array.from(new Set(eventIds.filter((eventId) => Number.isFinite(eventId))));
}

function sumNumbers(...values: Array<number | undefined>) {
  const numbers = values.filter((value): value is number => typeof value === "number");
  return numbers.length ? numbers.reduce((total, value) => total + value, 0) : undefined;
}
