import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ConfigProvider } from "antd";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../src/auth/AuthContext";
import { AiSettingsPage } from "../src/pages/AiSettingsPage";

function renderPage() {
  return render(
    <ConfigProvider>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AuthProvider>
          <AiSettingsPage />
        </AuthProvider>
      </QueryClientProvider>
    </ConfigProvider>
  );
}

describe("AI 智能整理配置", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("展示 DeepSeek 配置且不会回显 API Key 明文", async () => {
    const requests: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = String(input);
      requests.push(`${init?.method ?? "GET"} ${url}`);
      if (url.includes("/api/admin/auth/me")) {
        return Promise.resolve(json({ authenticated: true, username: "admin", csrf_token: "csrf" }));
      }
      if (url.includes("/api/admin/ai/providers/deepseek/models")) {
        return Promise.resolve(json({ models: ["deepseek-chat", "deepseek-reasoner"] }));
      }
      if (url.includes("/api/admin/ai/runs")) {
        return Promise.resolve(json({ items: [], total: 0, page: 1, page_size: 10 }));
      }
      if (url.includes("/api/admin/ai/providers/deepseek/test")) {
        return Promise.resolve(json({ status: "success" }));
      }
      if (url.includes("/api/admin/ai/providers/deepseek")) {
        return Promise.resolve(
          json({
            provider: "deepseek",
            enabled: false,
            auto_process_enabled: false,
            api_base: "https://api.deepseek.com",
            configured: true,
            api_key_masked: "sk-****abcd",
            model: "deepseek-chat",
            timeout_seconds: 90,
            max_concurrency: 2,
            max_tokens: 1200,
            daily_token_budget: 0,
            daily_request_budget: 0,
            auto_minimum_severity: "high",
            last_test_status: "not_tested",
            usage_today: { total_tokens: 0, request_count: 0, failure_count: 0 }
          })
        );
      }
      return Promise.resolve(json({}));
    });

    renderPage();

    expect(await screen.findByText("AI 智能整理")).toBeInTheDocument();
    expect(screen.getByDisplayValue("https://api.deepseek.com")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("sk-real-secret")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /获取模型列表/ }));
    await waitFor(() =>
      expect(requests.some((request) => request.includes("/api/admin/ai/providers/deepseek/models"))).toBe(true)
    );

    await userEvent.click(screen.getByRole("button", { name: /测试连接/ }));
    await waitFor(() =>
      expect(requests.some((request) => request === "POST /api/admin/ai/providers/deepseek/test")).toBe(true)
    );
  });
});

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" }
  });
}
