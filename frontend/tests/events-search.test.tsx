import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ConfigProvider } from "antd";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../src/auth/AuthContext";
import { EventsPage } from "../src/pages/EventsPage";

function renderPage() {
  return render(
    <ConfigProvider>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/events"]}>
          <AuthProvider>
            <EventsPage />
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>
    </ConfigProvider>
  );
}

describe("events search page", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("debounces title header search, avoids duplicate params, and loads header-filter facets", async () => {
    const requests: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      requests.push(url);
      if (url.includes("/api/admin/auth/me")) {
        return Promise.resolve(json({ authenticated: true, username: "admin", csrf_token: "csrf" }));
      }
      if (url.includes("/api/admin/events/facets")) {
        return Promise.resolve(
          json({
            source_keys: [{ value: "binance_announcements", label: "Binance", count: 2 }],
            source_groups: [{ value: "exchange_official", label: "Official", count: 2 }],
            categories: [{ value: "wallet_maintenance", label: "Wallet Maintenance", count: 1 }],
            symbols: [{ value: "BTC", label: "BTC", count: 1 }],
            chains: [{ value: "Bitcoin", label: "Bitcoin", count: 1 }]
          })
        );
      }
      if (url.includes("/api/admin/saved-searches")) {
        return Promise.resolve(json([]));
      }
      if (url.includes("/api/admin/events?")) {
        return Promise.resolve(
          json({
            items: [
              {
                id: 1,
                title: "BTC listing",
                display_title: "BTC listing",
                category: "listing",
                severity: "high",
                status: "new",
                trust_score: 95,
                symbols: ["BTC"],
                chains: ["Bitcoin"],
                source_name: "Binance",
                official: true,
                published_at: "2026-06-23T17:06:52",
                first_seen_at: "2026-06-23T17:12:11"
              }
            ],
            total: 1,
            page: 1,
            page_size: 20
          })
        );
      }
      return Promise.resolve(json({}));
    });

    const { container } = renderPage();

    await waitFor(() => {
      expect(requests.filter((url) => url.includes("/api/admin/events?"))).toHaveLength(1);
      expect(requests.filter((url) => url.includes("/api/admin/saved-searches"))).toHaveLength(1);
      expect(requests.filter((url) => url.includes("/api/admin/events/facets"))).toHaveLength(1);
    });
    expect(await screen.findByText("2026/06/24 01:06:52")).toBeInTheDocument();
    expect(await screen.findByText("获取：2026/06/24 01:12:11")).toBeInTheDocument();
    expect(await screen.findByText("\u5e01\u5b89")).toBeInTheDocument();
    expect(await screen.findByText("\u4e0a\u5e01")).toBeInTheDocument();

    const sourceFilterTrigger = container.querySelectorAll<HTMLElement>(".ant-table-filter-trigger")[1];
    expect(sourceFilterTrigger).toBeTruthy();
    await userEvent.click(sourceFilterTrigger);
    const sourceSelects = document.querySelectorAll<HTMLElement>(".table-filter-dropdown .ant-select-selector");
    expect(sourceSelects.length).toBeGreaterThan(1);
    await userEvent.click(sourceSelects[1]);
    expect(await screen.findByText("\u4ea4\u6613\u6240\u5b98\u65b9 (2)")).toBeInTheDocument();
    expect(screen.queryByText("Official (2)")).not.toBeInTheDocument();
    await userEvent.keyboard("{Escape}");

    const titleFilterTrigger = container.querySelectorAll<HTMLElement>(".ant-table-filter-trigger")[0];
    expect(titleFilterTrigger).toBeTruthy();
    await userEvent.click(titleFilterTrigger);

    let searchInput: HTMLInputElement | null = null;
    await waitFor(() => {
      searchInput = document.querySelector<HTMLInputElement>(".event-search-input input");
      expect(searchInput).not.toBeNull();
    });
    await userEvent.type(searchInput!, "BTC");

    await waitFor(
      () => {
        expect(requests.filter((url) => url.includes("/api/admin/events?") && url.includes("q=BTC"))).toHaveLength(1);
      },
      { timeout: 1200 }
    );
    expect(requests.filter((url) => url.includes("/api/admin/events/facets"))).toHaveLength(1);
  });

  it("提交单事件 AI 整理后显示 job 状态、轮询任务并提示 title_only 输入质量", async () => {
    const requests: Array<{ url: string; method: string }> = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      requests.push({ url, method });
      if (url.includes("/api/admin/auth/me")) {
        return Promise.resolve(json({ authenticated: true, username: "admin", csrf_token: "csrf" }));
      }
      if (url.includes("/api/admin/saved-searches")) {
        return Promise.resolve(json([]));
      }
      if (url.includes("/api/admin/events/1/ai-insight")) {
        return Promise.resolve(json({ event_id: 1, provider: "deepseek", model: "mock" }));
      }
      if (url.includes("/api/admin/events/1/pipeline")) {
        return Promise.resolve(
          json({
            event_id: 1,
            timeline: [
              { id: "fetch", stage: "fetch", status: "succeeded", title: "源站抓取完成" },
              { id: "parse", stage: "parse", status: "succeeded", title: "正文解析完成" },
              { id: "event", stage: "event", status: "succeeded", title: "事件已入库" },
              { id: "ai-queued", stage: "ai", status: "queued", title: "AI 排队中", job_id: "job-1" },
              { id: "ai-started", stage: "ai", status: "started", title: "AI 生成中", job_id: "job-1" },
              { id: "ai-retrying", stage: "ai", status: "retrying", title: "AI 重试中", retry_count: 1 },
              { id: "ai-succeeded", stage: "ai", status: "succeeded", title: "AI 已完成" },
              { id: "ai-failed", stage: "ai", status: "failed", title: "AI 失败", error_sanitized: "模型超时" },
              { id: "ai-cancelled", stage: "ai", status: "cancelled", title: "AI 已取消" },
              { id: "feishu-queued", stage: "feishu", status: "queued", title: "飞书待发送" },
              { id: "feishu-sending", stage: "feishu", status: "sending", title: "飞书发送中" },
              { id: "feishu-delivered", stage: "feishu", status: "delivered", title: "飞书已送达", delivery_id: 12 },
              { id: "feishu-failed", stage: "feishu", status: "failed", title: "飞书发送失败" },
              { id: "feishu-dry-run", stage: "feishu", status: "dry_run", title: "飞书 dry-run" },
              { id: "feishu-suppressed", stage: "feishu", status: "suppressed", title: "飞书已抑制" }
            ],
            delivery: {
              delivery_id: 12,
              status: "dry_run",
              dry_run: true,
              channel: "feishu",
              target: "Mock 飞书群",
              attempts: 1,
              provider_message_id: "dry-run"
            },
            card_preview: {
              title: "BTC 汇报卡片",
              webhook_url: "https://example.invalid/redacted-webhook/secret-token",
              content: "AI 已整理 BTC 上币事件。"
            }
          })
        );
      }
      if (url.includes("/api/admin/events/1/ai-summary") && method === "POST") {
        return Promise.resolve(
          json({
            job_id: "job-1",
            status: "queued",
            event_id: 1,
            input_quality: "title_only",
            poll_url: "/api/admin/ai/jobs/job-1"
          }, 202)
        );
      }
      if (url.includes("/api/admin/ai/jobs/job-1")) {
        return Promise.resolve(
          json({
            job_id: "job-1",
            status: "queued",
            event_id: 1,
            input_quality: "title_only",
            queue_wait_ms: 120
          })
        );
      }
      if (url.includes("/api/admin/events?")) {
        return Promise.resolve(
          json({
            items: [
              {
                id: 1,
                title: "BTC listing",
                display_title: "BTC listing",
                category: "listing",
                severity: "high",
                status: "new",
                trust_score: 95,
                symbols: ["BTC"],
                chains: ["Bitcoin"],
                source_name: "Binance",
                official: true,
                published_at: "2026-06-23T17:06:52",
                first_seen_at: "2026-06-23T17:12:11"
              }
            ],
            total: 1,
            page: 1,
            page_size: 20
          })
        );
      }
      return Promise.resolve(json({}));
    });

    renderPage();

    await userEvent.click(await screen.findByRole("button", { name: "BTC listing" }));
    expect(await screen.findByText("处理时间线")).toBeInTheDocument();
    expect(await screen.findByText("源站抓取完成")).toBeInTheDocument();
    expect(await screen.findByText("AI 已取消")).toBeInTheDocument();
    expect(await screen.findByText("飞书已送达")).toBeInTheDocument();
    expect(await screen.findByText("投递状态")).toBeInTheDocument();
    expect(await screen.findAllByText("未实发")).not.toHaveLength(0);
    expect(await screen.findByText(/BTC 汇报卡片/)).toBeInTheDocument();
    expect(screen.queryByText(/secret-token/)).not.toBeInTheDocument();

    await userEvent.click(await screen.findByRole("button", { name: /重新生成/ }));

    expect(await screen.findAllByText("排队中")).not.toHaveLength(0);
    expect(await screen.findAllByText("输入信息较少，AI 结果可能不完整。")).not.toHaveLength(0);
    await waitFor(() =>
      expect(requests.some((request) => request.url.includes("/api/admin/ai/jobs/job-1"))).toBe(true)
    );
    expect(
      requests.some((request) => request.url.includes("/api/admin/events/1/ai-summary") && request.method === "POST")
    ).toBe(true);
  });
});

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" }
  });
}
