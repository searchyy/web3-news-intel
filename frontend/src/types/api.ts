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
  published_at?: string;
  first_seen_at: string;
  primary_url?: string;
  display_title?: string;
  display_summary?: string;
  category_label?: string;
  severity_label?: string;
  status_label?: string;
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
