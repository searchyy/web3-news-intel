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

describe("事件搜索页面", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("使用 300ms debounce 将中文搜索条件同步到服务端查询", async () => {
    const requests: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      requests.push(url);
      if (url.includes("/api/admin/auth/me")) {
        return Promise.resolve(json({ authenticated: true, username: "admin", csrf_token: "csrf" }));
      }
      if (url.includes("/api/admin/events/facets")) {
        return Promise.resolve(json({ source_groups: [{ value: "exchange_official", label: "交易所官方", count: 2 }] }));
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
                title: "BTC 上币公告",
                display_title: "BTC 上币公告",
                category: "listing",
                severity: "high",
                status: "new",
                trust_score: 95,
                symbols: ["BTC"],
                chains: ["Bitcoin"],
                source_name: "Binance",
                official: true,
                first_seen_at: "2026-06-21T00:00:00Z"
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
    expect(await screen.findByText("BTC 上币公告")).toBeInTheDocument();
    await userEvent.type(screen.getByPlaceholderText("搜索标题、摘要、币种、链、来源、AI 标签或关键事实"), "BTC");

    await waitFor(
      () => {
        expect(requests.some((url) => url.includes("/api/admin/events?") && url.includes("q=BTC"))).toBe(true);
      },
      { timeout: 1200 }
    );
  });
});

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" }
  });
}
