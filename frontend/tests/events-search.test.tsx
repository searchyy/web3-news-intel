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

  it("debounces search, avoids duplicate params, and defers facets", async () => {
    const requests: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      requests.push(url);
      if (url.includes("/api/admin/auth/me")) {
        return Promise.resolve(json({ authenticated: true, username: "admin", csrf_token: "csrf" }));
      }
      if (url.includes("/api/admin/events/facets")) {
        return Promise.resolve(json({ source_groups: [{ value: "exchange_official", label: "Official", count: 2 }] }));
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

    const { container } = renderPage();

    await waitFor(() => {
      expect(requests.filter((url) => url.includes("/api/admin/events?"))).toHaveLength(1);
      expect(requests.filter((url) => url.includes("/api/admin/saved-searches"))).toHaveLength(1);
    });
    expect(requests.filter((url) => url.includes("/api/admin/events/facets"))).toHaveLength(0);

    const searchInput = container.querySelector<HTMLInputElement>(".event-search-input input");
    expect(searchInput).not.toBeNull();
    await userEvent.type(searchInput!, "BTC");

    await waitFor(
      () => {
        expect(requests.filter((url) => url.includes("/api/admin/events?") && url.includes("q=BTC"))).toHaveLength(1);
      },
      { timeout: 1200 }
    );
    expect(requests.filter((url) => url.includes("/api/admin/events/facets"))).toHaveLength(0);
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

    await userEvent.click(await screen.findByRole("button", { name: "BTC listing" }));
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
