from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

fetch_attempts_total = Counter(
    "web3_news_fetch_attempts_total",
    "Fetch attempts",
    ["method"],
)
fetch_results_total = Counter(
    "web3_news_fetch_results_total",
    "Fetch results",
    ["method", "outcome", "status_group"],
)
fetch_retries_total = Counter(
    "web3_news_fetch_retries_total",
    "Fetch retries",
    ["method", "reason"],
)
fetch_duration_seconds = Histogram(
    "web3_news_fetch_duration_seconds",
    "Fetch duration",
    ["method", "outcome"],
    buckets=(0.1, 0.5, 1, 2.5, 5, 10, 30, 60),
)
parse_results_total = Counter(
    "web3_news_parse_results_total",
    "Parse results",
    ["adapter", "outcome"],
)
normalized_items_total = Counter(
    "web3_news_normalized_items_total",
    "Normalized item count",
    ["adapter"],
)
event_upserts_total = Counter(
    "web3_news_event_upserts_total",
    "Event upsert outcomes",
    ["outcome"],
)
publisher_results_total = Counter(
    "web3_news_publisher_results_total",
    "Publisher delivery outcomes",
    ["channel", "outcome"],
)
delivery_latency_seconds = Histogram(
    "web3_news_delivery_latency_seconds",
    "Delivery latency",
    ["channel"],
    buckets=(0.1, 0.5, 1, 2.5, 5, 10, 30),
)
queue_length = Gauge(
    "web3_news_queue_length",
    "Queue length where available",
    ["queue"],
)
db_sources = Gauge("web3_news_sources_total", "Configured sources")
db_events = Gauge("web3_news_events_total", "Stored events")
db_deliveries = Gauge("web3_news_deliveries_total", "Delivery records")
feishu_token_refresh_total = Counter(
    "web3_news_feishu_token_refresh_total",
    "Feishu tenant token refresh outcomes",
    ["result"],
)
feishu_send_total = Counter(
    "web3_news_feishu_send_total",
    "Feishu send outcomes",
    ["provider", "result"],
)
feishu_send_duration_seconds = Histogram(
    "web3_news_feishu_send_duration_seconds",
    "Feishu send duration",
    ["provider", "result"],
    buckets=(0.1, 0.5, 1, 2.5, 5, 10, 30),
)
feishu_callback_total = Counter(
    "web3_news_feishu_callback_total",
    "Feishu callback outcomes",
    ["callback_type", "result"],
)
feishu_destination_count = Gauge(
    "web3_news_feishu_destination_count",
    "Feishu destination count",
    ["status"],
)
notification_delivery_total = Counter(
    "web3_news_notification_delivery_total",
    "Notification delivery outcomes",
    ["provider", "result"],
)
notification_rate_limited_total = Counter(
    "web3_news_notification_rate_limited_total",
    "Notification rate limited outcomes",
    ["provider"],
)
notification_digest_total = Counter(
    "web3_news_notification_digest_total",
    "Notification digest outcomes",
    ["provider", "result"],
)


def status_group(status_code: int | None) -> str:
    if status_code is None:
        return "none"
    return f"{status_code // 100}xx"
