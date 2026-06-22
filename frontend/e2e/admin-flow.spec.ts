import { expect, test } from "@playwright/test";

test("mock 管理端搜索、AI 与飞书汇报流程", async ({ page }) => {
  let authenticated = false;
  let savedSearches: Array<{ id: number; name: string; filters: Record<string, unknown> }> = [];
  let aiSummaryRequests = 0;
  let reportScheduleId = 1;
  let testSendCount = 0;
  const schedules: Array<Record<string, unknown>> = [];

  await page.route("**/api/admin/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();

    if (path === "/api/admin/auth/me") {
      return route.fulfill({ json: authenticated ? authUser() : { authenticated: false, username: "" } });
    }
    if (path === "/api/admin/auth/login" && method === "POST") {
      authenticated = true;
      return route.fulfill({ json: authUser() });
    }
    if (path === "/api/admin/dashboard/summary") {
      return route.fulfill({
        json: {
          events_last_hour: 1,
          events_last_24h: 3,
          critical_high_count: 1,
          enabled_sources: 4,
          failed_sources: 0,
          successful_deliveries: 1,
          failed_deliveries: 0,
          pending_feishu_groups: 0
        }
      });
    }
    if (path === "/api/admin/events/facets") {
      return route.fulfill({
        json: {
          source_groups: [{ value: "exchange_official", label: "交易所官方", count: 1 }],
          categories: [{ value: "listing", label: "上币", count: 1 }],
          severities: [{ value: "high", label: "高", count: 1 }],
          symbols: [{ value: "BTC", label: "BTC", count: 1 }],
          chains: [{ value: "Bitcoin", label: "Bitcoin", count: 1 }]
        }
      });
    }
    if (path === "/api/admin/events" && method === "GET") {
      return route.fulfill({
        json: {
          items: [
            {
              id: 1,
              title: "BTC 上币公告",
              display_title: "BTC 上币公告",
              display_summary: "交易所发布 BTC 上币公告。",
              category: "listing",
              severity: "high",
              status: "new",
              trust_score: 95,
              symbols: ["BTC"],
              chains: ["Bitcoin"],
              source_name: "Binance",
              source_group: "exchange_official",
              official: true,
              first_seen_at: "2026-06-22T01:00:00Z",
              published_at: "2026-06-22T01:00:00Z",
              ai_summary_status: aiSummaryRequests ? "completed" : undefined,
              has_ai_summary: aiSummaryRequests > 0,
              ai_summary_zh: aiSummaryRequests ? "AI 已整理 BTC 上币事件。" : undefined,
              ai_importance_score: aiSummaryRequests ? 88 : undefined,
              ai_risk_level: aiSummaryRequests ? "high" : undefined
            }
          ],
          total: 1,
          page: 1,
          page_size: 20
        }
      });
    }
    if (path === "/api/admin/saved-searches" && method === "GET") {
      return route.fulfill({ json: savedSearches });
    }
    if (path === "/api/admin/saved-searches" && method === "POST") {
      const body = request.postDataJSON() as { name: string; filters: Record<string, unknown> };
      const saved = { id: savedSearches.length + 1, name: body.name, filters: body.filters };
      savedSearches = [saved, ...savedSearches];
      return route.fulfill({ status: 201, json: saved });
    }
    if (path === "/api/admin/events/ai-summary-batch" && method === "POST") {
      aiSummaryRequests += 1;
      return route.fulfill({ status: 202, json: { job_id: "mock-ai-task", status: "queued", event_ids: [1] } });
    }
    if (path === "/api/admin/ai/jobs/mock-ai-task" && method === "GET") {
      return route.fulfill({ json: { job_id: "mock-ai-task", status: "succeeded", event_ids: [1] } });
    }
    if (path === "/api/admin/ai/providers/deepseek" && method === "GET") {
      return route.fulfill({
        json: {
          provider: "deepseek",
          enabled: false,
          auto_process_enabled: false,
          api_base: "https://api.deepseek.com",
          api_key_configured: false,
          model: "deepseek-chat",
          timeout_seconds: 90,
          max_concurrency: 2,
          max_tokens: 1200,
          daily_token_budget: 0,
          daily_request_budget: 0,
          auto_minimum_severity: "high",
          last_test_status: "not_tested",
          tokens_today: 0,
          requests_today: 0,
          failures_today: 0
        }
      });
    }
    if (path === "/api/admin/ai/providers/deepseek" && method === "PUT") {
      return route.fulfill({
        json: {
          provider: "deepseek",
          enabled: true,
          auto_process_enabled: false,
          api_base: "https://api.deepseek.com",
          api_key_configured: true,
          api_key_masked: "sk-****mock",
          model: "deepseek-chat",
          last_test_status: "not_tested"
        }
      });
    }
    if (path === "/api/admin/ai/providers/deepseek/models") {
      return route.fulfill({ json: { models: ["deepseek-chat"] } });
    }
    if (path === "/api/admin/ai/providers/deepseek/test" && method === "POST") {
      return route.fulfill({ json: { status: "success" } });
    }
    if (path === "/api/admin/ai/runs") {
      return route.fulfill({ json: { items: [], total: 0, page: 1, page_size: 10 } });
    }
    if (path === "/api/admin/destinations") {
      return route.fulfill({
        json: [
          {
            id: "00000000-0000-0000-0000-000000000001",
            key: "feishu-mock",
            name: "Mock 飞书群",
            provider: "feishu_webhook",
            enabled: true,
            status: "active",
            chat_name: "Mock 飞书群",
            secret_fingerprint: "****mock"
          }
        ]
      });
    }
    if (path === "/api/admin/report-schedules" && method === "GET") {
      return route.fulfill({ json: schedules });
    }
    if (path === "/api/admin/report-schedules" && method === "POST") {
      const body = request.postDataJSON() as Record<string, unknown>;
      const schedule = {
        id: reportScheduleId++,
        ...body,
        destination_id: "00000000-0000-0000-0000-000000000001",
        saved_search_id: savedSearches[0]?.id ?? null,
        last_result: null,
        next_run_at: "2026-06-22T02:00:00Z",
        last_run_at: null,
        created_at: "2026-06-22T01:30:00Z",
        updated_at: "2026-06-22T01:30:00Z"
      };
      schedules.unshift(schedule);
      return route.fulfill({ status: 201, json: schedule });
    }
    if (path === "/api/admin/report-schedules/1/preview" && method === "POST") {
      return route.fulfill({ json: reportPreview() });
    }
    if (path === "/api/admin/report-schedules/1/test-send" && method === "POST") {
      testSendCount += 1;
      return route.fulfill({
        json: { schedule_id: 1, delivery_id: 1, status: "sent", dry_run: false }
      });
    }
    return route.fulfill({ status: 404, json: { detail: `unhandled mock route ${method} ${path}` } });
  });

  await page.goto("/login");
  await page.getByLabel("用户名").fill("admin");
  await page.getByLabel("密码").fill("password");
  await page.getByRole("button", { name: /登\s*录/ }).click();

  await page.goto("/events");
  await page.getByPlaceholder("搜索标题、摘要、币种、链、来源、AI 标签或关键事实").fill("BTC");
  await expect(page.getByText("BTC 上币公告")).toBeVisible();
  await page.getByRole("button", { name: /保存筛选/ }).click();
  await page.getByLabel("筛选名称").fill("BTC 高风险");
  await page.getByRole("button", { name: /^保\s*存$/ }).click();
  await expect(page.getByText("已保存筛选")).toBeVisible();
  await page.getByRole("checkbox").nth(1).check();
  await page.getByRole("button", { name: /对选中事件进行 AI 整理/ }).click();
  await expect(page.getByText("已提交 AI 整理任务")).toBeVisible();

  await page.goto("/settings/ai");
  await page.getByLabel("API Key（只写）").fill("sk-mock-only");
  await page.getByRole("button", { name: /获取模型列表/ }).click();
  await page.getByRole("button", { name: /保存配置/ }).click();
  await expect(page.getByText(/API Key 不会明文回显/)).toBeVisible();
  await page.getByRole("button", { name: /测试连接/ }).click();

  await page.goto("/feishu-groups");
  await expect(page.getByRole("cell", { name: "Mock 飞书群" }).first()).toBeVisible();
  await page.getByRole("button", { name: /新建汇报规则/ }).click();
  await page.getByLabel("规则名称").fill("每小时 BTC 汇报");
  await page.getByRole("button", { name: /创建规则/ }).click();
  await page.getByRole("tab", { name: "汇报规则" }).click();
  await expect(page.getByText("每小时 BTC 汇报")).toBeVisible();
  await page.getByRole("button", { name: /预\s*览/ }).click();
  await expect(page.getByText("飞书汇报预览")).toBeVisible();
  await expect(page.getByText("Mock 汇报：过去一小时出现 1 条 BTC 高风险事件。")).toBeVisible();
  await page.getByRole("button", { name: "Close" }).click();
  await page.getByRole("button", { name: /发送测试汇报/ }).click();
  await expect.poll(() => testSendCount).toBe(1);
});

function authUser() {
  return { authenticated: true, username: "admin", csrf_token: "csrf" };
}

function reportPreview() {
  return {
    schedule_id: 1,
    destination_id: "00000000-0000-0000-0000-000000000001",
    report_type: "hourly",
    window_start: "2026-06-22T01:00:00Z",
    window_end: "2026-06-22T02:00:00Z",
    event_count: 1,
    critical_high_count: 1,
    top_symbols: ["BTC"],
    top_categories: ["listing"],
    summary_zh: "Mock 汇报：过去一小时出现 1 条 BTC 高风险事件。",
    omitted_count: 0,
    card: {},
    events: [
      {
        id: 1,
        title: "BTC 上币公告",
        severity: "high",
        category: "listing",
        first_seen_at: "2026-06-22T01:00:00Z",
        symbols: ["BTC"],
        chains: ["Bitcoin"]
      }
    ]
  };
}
