import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ConfigProvider } from "antd";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../src/auth/AuthContext";
import { FeishuGroupsPage } from "../src/pages/FeishuGroupsPage";

function renderPage() {
  return render(
    <ConfigProvider>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AuthProvider>
          <FeishuGroupsPage />
        </AuthProvider>
      </QueryClientProvider>
    </ConfigProvider>
  );
}

describe("飞书群组与汇报页面", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("使用中文只写 Webhook 文案且默认不渲染完整 Webhook URL", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/api/admin/auth/me")) {
        return Promise.resolve(json({ authenticated: true, username: "admin", csrf_token: "csrf" }));
      }
      return Promise.resolve(json([]));
    });

    renderPage();

    expect(await screen.findByText("飞书群组与汇报")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /添加飞书 Webhook/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /新建汇报规则/ })).toBeInTheDocument();
    expect(screen.queryByText(/open-apis\/bot\/v2\/hook/)).not.toBeInTheDocument();
  });

  it("展示汇报规则并支持预览和 Mock 测试发送", async () => {
    const requests: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = String(input);
      requests.push(`${init?.method ?? "GET"} ${url}`);
      if (url.includes("/api/admin/auth/me")) {
        return Promise.resolve(json({ authenticated: true, username: "admin", csrf_token: "csrf" }));
      }
      if (url.endsWith("/api/admin/destinations")) {
        return Promise.resolve(
          json([
            {
              id: "00000000-0000-0000-0000-000000000001",
              key: "feishu-test",
              name: "测试飞书群",
              provider: "feishu_webhook",
              enabled: true,
              status: "active",
              chat_name: "测试飞书群",
              secret_fingerprint: "****abcd"
            }
          ])
        );
      }
      if (url.endsWith("/api/admin/saved-searches")) {
        return Promise.resolve(json([{ id: 1, name: "BTC 高风险", filters: { q: "BTC" } }]));
      }
      if (url.endsWith("/api/admin/report-schedules")) {
        return Promise.resolve(
          json([
            {
              id: 7,
              destination_id: "00000000-0000-0000-0000-000000000001",
              name: "每小时高风险汇报",
              enabled: true,
              report_type: "hourly",
              timezone: "Asia/Taipei",
              interval_minutes: 60,
              hour: null,
              minute: null,
              saved_search_id: 1,
              source_groups: ["exchange_official"],
              categories: ["listing"],
              severities: ["high"],
              symbols: ["BTC"],
              chains: ["Bitcoin"],
              minimum_trust_score: 80,
              include_ai_summary: true,
              maximum_events: 10,
              next_run_at: "2026-06-22T10:00:00Z",
              last_run_at: "2026-06-22T09:00:00Z",
              last_result: "success",
              created_at: "2026-06-22T08:00:00Z",
              updated_at: "2026-06-22T08:00:00Z"
            }
          ])
        );
      }
      if (url.endsWith("/api/admin/report-schedules/7/preview")) {
        return Promise.resolve(
          json({
            schedule_id: 7,
            destination_id: "00000000-0000-0000-0000-000000000001",
            report_type: "hourly",
            window_start: "2026-06-22T09:00:00Z",
            window_end: "2026-06-22T10:00:00Z",
            event_count: 1,
            critical_high_count: 1,
            top_symbols: ["BTC"],
            top_categories: ["listing"],
            summary_zh: "过去一小时出现 1 条高风险 BTC 事件。",
            omitted_count: 0,
            card: {},
            events: [
              {
                id: 1,
                title: "BTC 上币公告",
                severity: "high",
                category: "listing",
                first_seen_at: "2026-06-22T09:10:00Z",
                symbols: ["BTC"],
                chains: ["Bitcoin"]
              }
            ]
          })
        );
      }
      if (url.endsWith("/api/admin/report-schedules/7/test-send")) {
        return Promise.resolve(json({ schedule_id: 7, delivery_id: 12, status: "sent", dry_run: false }));
      }
      return Promise.resolve(json({}));
    });

    renderPage();

    await userEvent.click(await screen.findByRole("tab", { name: "汇报规则" }));
    expect(await screen.findByText("每小时高风险汇报")).toBeInTheDocument();
    expect(screen.getAllByText("测试飞书群").length).toBeGreaterThan(0);
    await userEvent.click(screen.getByRole("button", { name: /预览/ }));
    expect(await screen.findByText("飞书汇报预览")).toBeInTheDocument();
    expect(screen.getByText("过去一小时出现 1 条高风险 BTC 事件。")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    await userEvent.click(screen.getByRole("button", { name: /发送测试汇报/ }));
    await waitFor(() =>
      expect(requests).toContain("POST /api/admin/report-schedules/7/test-send")
    );
  });
});

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" }
  });
}
