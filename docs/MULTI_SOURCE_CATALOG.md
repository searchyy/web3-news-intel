# 多消息源 Catalog

本版本新增统一 source catalog：`source_catalog/exchanges.yaml` 和 `source_catalog/media.yaml`。运行时仍保留 `sources.yaml` 作为默认入口；手动/定时 live canary 已支持通过 `--catalog-dir source_catalog` 覆盖新 catalog。

## 采集原则

- 优先官方公开 RSS、公开 JSON/API、公开结构化接口。
- HTML 只采集公开标题、摘要、发布时间、作者、标签和原文链接。
- 不采集登录后、付费、未授权或受访问控制保护内容。
- 不使用 CAPTCHA 求解、stealth 浏览器、浏览器指纹伪装、403/429 后换 IP 硬打。
- 403/401、挑战页、登录页、空结构或 selector 失效必须标记为失败状态。
- 媒体报道默认不是官方确认；重大事件需要多来源聚类或官方来源确认。
- 外部实时 canary 不作为 PR 必过门禁，结果作为 artifact 上传。

## 交易所官方源

排名快照：CoinGecko Trust Score，快照日期 `2026-06-21`。该快照仅用于初始化 catalog，不代表永久排名。

| Key | 来源 | 排名 | Adapter | 默认 | 支持分类 | 当前 canary 状态 | 已知限制 |
| --- | --- | ---: | --- | --- | --- | --- | --- |
| `coinbase_exchange` | Coinbase Exchange | 1 | RSS | disabled | listing, delisting, product, regulatory, trading_rule | DISABLED | 未确认公告专用公开源 |
| `binance_announcements` | Binance | 2 | JSON/API | enabled | listing, delisting, derivatives_listing, wallet_maintenance, deposit_withdrawal, system_maintenance, trading_rule, product, regulatory | NETWORK_FAILED | 两次本机 canary 有波动；第一次 legacy Binance PASS，第二次 legacy/catalog 均网络失败 |
| `kraken_announcements` | Kraken | 3 | RSS | disabled | listing, delisting, derivatives_listing, product, regulatory, trading_rule | DISABLED | 当前入口偏官方博客 |
| `bitget_announcements` | Bitget | 4 | HTML | disabled | listing, delisting, deposit_withdrawal, wallet_maintenance, system_maintenance, product, regulatory | DISABLED | HTML selector 待验证 |
| `okx_announcements` | OKX | 5 | HTML/app state | enabled | listing, delisting, derivatives_listing, deposit_withdrawal, wallet_maintenance, system_maintenance, trading_rule, product, regulatory | DEGRADED | 两次本机 canary 有波动；第一次 legacy OKX PASS，第二次 legacy 网络失败、catalog DNS 降级 |
| `bybit_announcements` | Bybit | 6 | JSON/API | disabled | listing, delisting, derivatives_listing, derivatives_delisting, deposit_withdrawal, wallet_maintenance, system_maintenance, product, regulatory | DISABLED | 公开结构化入口待确认 |
| `bitstamp_announcements` | Bitstamp by Robinhood | 7 | RSS | disabled | listing, delisting, product, regulatory, trading_rule | DISABLED | 当前入口偏官方博客 |
| `gate_announcements` | Gate | 8 | JSON/API | disabled | listing, delisting, deposit_withdrawal, wallet_maintenance, system_maintenance, product, regulatory | DISABLED | 公开结构化入口待确认 |
| `mexc_announcements` | MEXC | 9 | JSON/API | disabled | listing, delisting, derivatives_listing, deposit_withdrawal, wallet_maintenance, system_maintenance, product, regulatory | DISABLED | 公开结构化入口待确认 |
| `hashkey_announcements` | HashKey Exchange | 10 | HTML | disabled | listing, delisting, deposit_withdrawal, wallet_maintenance, system_maintenance, product, regulatory | DISABLED | HTML selector 待验证 |

候选源默认不计入 Top 10：`kucoin_announcements`、`upbit_announcements`、`htx_announcements`、`crypto_com_exchange_announcements`，均默认 disabled。

## 媒体源

| Key | 来源 | 分组 | Adapter | 默认 | 当前 canary 状态 | 已知限制 |
| --- | --- | --- | --- | --- | --- | --- |
| `blockbeats_newsflash` | 律动 BlockBeats 快讯 | media_zh | HTML | enabled | PASS | 仅采集公开快讯列表字段 |
| `foresight_news` | Foresight News | media_zh | HTML | disabled | DISABLED | 公开入口待 canary |
| `panews_news` | PANews | media_zh | JSON/API | disabled | DISABLED | 公开结构化接口待 canary |
| `odaily_newsflash` | Odaily 星球日报 | media_zh | HTML | disabled | DISABLED | 快讯入口待 canary |
| `chaincatcher_news` | ChainCatcher | media_zh | HTML | disabled | DISABLED | 公开结构化入口待 canary |
| `techflow_news` | 深潮 TechFlow | media_zh | HTML | disabled | DISABLED | 公开列表结构待 canary |
| `jinse_news` | 金色财经 | media_zh | HTML | disabled | DISABLED | 不保存正文，入口待 canary |
| `coindesk_rss` | CoinDesk RSS | media_en | RSS | enabled | NETWORK_FAILED | 两次本机 canary 均网络失败，不伪造成功 |
| `theblock_rss` | The Block | media_en | RSS | disabled | DISABLED | 不绕过付费墙或登录墙 |
| `decrypt_rss` | Decrypt RSS | media_en | RSS | enabled | PASS | RSS 摘要截断，全文不入库 |
| `cointelegraph_rss` | Cointelegraph RSS | media_en | RSS | enabled | PASS | RSS 摘要截断，全文不入库 |

## Canary 输出字段

`scripts/live_source_canary.py` 输出脱敏 JSON 和 Markdown，字段包括：

- `source_key`
- `adapter`
- `http_status`
- `content_type`
- `response_bytes`
- `body_sha256`
- `parsed_item_count`
- `newest_published_at`
- `sample_title`
- `original_url`
- `result`
- `error_reason`

允许状态：`PASS`、`DEGRADED`、`ACCESS_DENIED`、`EMPTY`、`PARSER_BROKEN`、`NETWORK_FAILED`、`DISABLED`。Canary 不保存响应正文，不把 fixture 伪装成 live success。
