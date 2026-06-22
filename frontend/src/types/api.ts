export type AuthMe = { authenticated: boolean; username: string; csrf_token?: string };
export type DashboardSummary = {
  events_last_hour: number;
  events_last_24h: number;
  critical_high_count: number;
  enabled_sources: number;
  failed_sources: number;
  successful_deliveries: number;
  failed_deliveries: number;
  pending_feishu_groups: number;
};
export type EventRow = {
  id: number;
  title: string;
  category: string;
  severity: string;
  status: string;
  trust_score: number;
  symbols: string[];
  chains?: string[];
  entities?: string[];
  source_key?: string;
  source_name?: string;
  source_group?: string;
  language?: string;
  official?: boolean;
  published_at?: string;
  first_seen_at: string;
  primary_url?: string;
  display_title?: string;
  display_summary?: string;
  category_label?: string;
  severity_label?: string;
  status_label?: string;
  ai_summary_status?: string;
  ai_headline_zh?: string;
  ai_summary_zh?: string;
  ai_importance_score?: number;
  ai_risk_level?: string;
  ai_tags?: string[];
  has_ai_summary?: boolean;
};
export type PaginatedResponse<T> = {
  items: T[];
  total: number;
  page: number;
  page_size: number;
};
export type FacetOption = {
  value?: string;
  key?: string;
  label?: string;
  count?: number;
};
export type EventFacets = {
  source_keys?: FacetOption[];
  source_groups?: FacetOption[];
  categories?: FacetOption[];
  severities?: FacetOption[];
  statuses?: FacetOption[];
  symbols?: FacetOption[];
  chains?: FacetOption[];
  languages?: FacetOption[];
};
export type SavedSearch = {
  id: string | number;
  name: string;
  filters: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
};
export type EventAiInsight = {
  id?: string;
  event_id: number;
  provider: string;
  model: string;
  prompt_version?: string;
  headline_zh?: string;
  summary_zh?: string;
  key_facts?: unknown[];
  entities?: unknown[];
  symbols?: string[];
  chains?: string[];
  event_type?: string;
  importance_score?: number;
  risk_level?: string;
  sentiment?: string;
  market_impact?: string;
  facts?: unknown[];
  inferences?: unknown[];
  confidence?: number;
  source_event_ids?: number[];
  source_urls?: string[];
  generated_at?: string;
  status?: string;
  error_sanitized?: string;
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
};
export type SourceRow = {
  id: number;
  key: string;
  name: string;
  adapter: string;
  enabled: boolean;
  poll_seconds: number;
  access_denied_reason?: string;
};
export type AiProviderConfig = {
  provider: "deepseek";
  enabled: boolean;
  auto_process_enabled: boolean;
  api_base?: string;
  configured?: boolean;
  api_key_configured?: boolean;
  api_key_masked?: string | null;
  model?: string | null;
  timeout_seconds?: number;
  max_concurrency?: number;
  max_tokens?: number;
  temperature?: number;
  thinking_enabled?: boolean;
  daily_token_budget?: number;
  daily_request_budget?: number;
  auto_minimum_severity?: string;
  last_tested_at?: string | null;
  last_test_status?: string | null;
  last_error_sanitized?: string | null;
  tokens_today?: number;
  requests_today?: number;
  failures_today?: number;
};
export type AiModelInfo = {
  id: string;
  name?: string;
};
export type AiModelsResponse = {
  models?: Array<string | AiModelInfo>;
  data?: Array<string | AiModelInfo>;
};
export type AiRun = {
  id: string;
  job_type: string;
  provider: string;
  model?: string;
  event_count?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  estimated_cost?: number;
  latency_ms?: number;
  status: string;
  retry_count?: number;
  error_code?: string;
  error_sanitized?: string;
  created_at: string;
  finished_at?: string;
};
export type Destination = {
  id: string;
  key: string;
  name: string;
  provider: string;
  enabled: boolean;
  status: string;
  chat_id?: string;
  chat_name?: string;
  secret_fingerprint?: string;
  last_success_at?: string;
  last_failure_at?: string;
  last_error_message?: string;
};
export type ReportType =
  | "immediate"
  | "digest_15m"
  | "digest_30m"
  | "hourly"
  | "daily_morning"
  | "daily_evening"
  | "custom";
export type ReportSchedule = {
  id: number;
  destination_id: string;
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
  activated_at?: string | null;
  last_window_start?: string | null;
  last_window_end?: string | null;
  last_run_at?: string | null;
  next_run_at?: string | null;
  last_result?: string | null;
  last_error_sanitized?: string | null;
  created_at: string;
  updated_at: string;
};
export type ReportEventPreview = {
  id: number;
  title: string;
  severity: string;
  category: string;
  published_at?: string | null;
  first_seen_at: string;
  primary_url?: string | null;
  symbols: string[];
  chains: string[];
  ai_summary_zh?: string | null;
};
export type ReportPreview = {
  schedule_id: number;
  destination_id: string;
  report_type: string;
  window_start: string;
  window_end: string;
  event_count: number;
  critical_high_count: number;
  top_symbols: string[];
  top_categories: string[];
  summary_zh: string;
  omitted_count: number;
  card: Record<string, unknown>;
  events: ReportEventPreview[];
};
export type ReportSendResult = {
  schedule_id: number;
  delivery_id?: number | null;
  status: string;
  dry_run: boolean;
  message?: string | null;
};
export type Rule = {
  id: string;
  destination_id: string;
  name: string;
  enabled: boolean;
  minimum_severity: string;
  categories: string[];
  sources: string[];
  symbols: string[];
  chains: string[];
  delivery_mode: string;
  digest_interval_minutes?: number;
  timezone: string;
  maximum_messages_per_hour: number;
  critical_bypass_quiet_hours: boolean;
};
export type Delivery = {
  id: number;
  event_id: number;
  destination_id?: string;
  channel: string;
  target: string;
  status: string;
  attempts: number;
  response_status?: number;
  provider_message_id?: string;
  delivered_at?: string;
  last_error?: string;
};
export type AuditLog = {
  id: string;
  admin_subject: string;
  action: string;
  resource_type: string;
  resource_id?: string;
  request_id: string;
  created_at: string;
};

export type FeishuConfig = {
  FEISHU_APP_ID?: string | null;
  FEISHU_APP_SECRET?: string | null;
  FEISHU_VERIFICATION_TOKEN?: string | null;
  FEISHU_ENCRYPT_KEY?: string | null;
  FEISHU_TEST_CHAT_ID?: string | null;
  FEISHU_ENABLED: boolean;
  FEISHU_SEND_ENABLED: boolean;
  connection_status: "not_tested" | "connected" | "failed";
};

export type FeishuTestResult = {
  status: "success" | "failed";
  latency_ms?: number | null;
  message?: string | null;
  error?: string | null;
};
