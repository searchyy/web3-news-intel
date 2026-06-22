import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ConfigProvider, message } from "antd";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../src/auth/AuthContext";
import { AiSettingsPage } from "../src/pages/AiSettingsPage";
import type { AiModelsResponse, AiProviderConfig } from "../src/types/api";

type RequestRecord = {
  method: string;
  url: string;
  body?: Record<string, unknown>;
};

function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false }
    }
  });
}

function renderPage(queryClient = createQueryClient()) {
  return {
    queryClient,
    ...render(
      <ConfigProvider>
        <QueryClientProvider client={queryClient}>
          <AuthProvider>
            <AiSettingsPage />
          </AuthProvider>
        </QueryClientProvider>
      </ConfigProvider>
    )
  };
}

describe("AI 智能整理配置", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("展示 DeepSeek 配置且不会回显 API Key 明文", async () => {
    const { requests } = mockAiFetch({
      config: baseConfig({
        api_key_configured: true,
        api_key_masked: "sk-****abcd",
        api_key_fingerprint: "fp-old"
      }),
      models: { models: ["deepseek-chat", "deepseek-reasoner"] }
    });

    renderPage();

    expect(await screen.findByText("AI 智能整理")).toBeInTheDocument();
    expect(screen.getByDisplayValue("https://api.deepseek.com")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("sk-real-secret")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /获取模型列表/ }));
    await waitFor(() =>
      expect(requests.some((request) => request.url.includes("/api/admin/ai/providers/deepseek/models"))).toBe(true)
    );

    await userEvent.click(screen.getByRole("button", { name: /测试连接/ }));
    await waitFor(() =>
      expect(requests.some((request) => request.method === "POST" && request.url === "/api/admin/ai/providers/deepseek/test")).toBe(true)
    );
  });

  it("未配置 Key 时获取模型只给中文提示，不请求模型接口", async () => {
    const warning = vi.spyOn(message, "warning");
    const { requests } = mockAiFetch({
      config: baseConfig({
        configured: false,
        api_key_configured: false,
        api_key_masked: null,
        api_key_fingerprint: null
      })
    });

    renderPage();

    expect(await screen.findByText("未配置密钥")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /获取模型列表/ }));

    expect(warning).toHaveBeenCalledWith("请先保存 DeepSeek API Key，再获取模型列表");
    expect(requests.some((request) => request.url.includes("/api/admin/ai/providers/deepseek/models"))).toBe(false);
  });

  it("保存时不提交空 Key 或掩码值，并用保存响应立即刷新配置", async () => {
    const savedBodies: Array<Record<string, unknown>> = [];
    const storageSetItem = vi.spyOn(Storage.prototype, "setItem");
    mockAiFetch({
      config: baseConfig({
        api_key_configured: true,
        api_key_masked: "sk-****abcd",
        api_key_fingerprint: "fp-old",
        last_test_status: "not_tested"
      }),
      onSave: (body) => {
        savedBodies.push(body);
        return json(
          baseConfig({
            api_key_configured: true,
            api_key_masked: "sk-****abcd",
            api_key_fingerprint: "fp-old",
            model: "deepseek-reasoner",
            last_test_status: "success"
          })
        );
      }
    });

    renderPage();

    expect(await screen.findByText("已配置密钥")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /保存配置/ }));
    await waitFor(() => expect(savedBodies).toHaveLength(1));
    expect(savedBodies[0]).not.toHaveProperty("api_key");
    expect(await screen.findAllByText("连接成功")).not.toHaveLength(0);

    await userEvent.type(screen.getByPlaceholderText("sk-****abcd"), "sk-****abcd");
    await userEvent.click(screen.getByRole("button", { name: /保存配置/ }));
    await waitFor(() => expect(savedBodies).toHaveLength(2));
    expect(savedBodies[1]).not.toHaveProperty("api_key");
    expect(
      storageSetItem.mock.calls.some((call) => call.some((value) => String(value).includes("sk-")))
    ).toBe(false);
  });

  it("保存新 Key 和删除 Key 后清理模型列表缓存", async () => {
    const savedBodies: Array<Record<string, unknown>> = [];
    let modelsCallCount = 0;
    const queryClient = createQueryClient();
    mockAiFetch({
      config: baseConfig({
        api_key_configured: true,
        api_key_masked: "sk-****old",
        api_key_fingerprint: "fp-old"
      }),
      onModels: () => {
        modelsCallCount += 1;
        return json({ models: modelsCallCount === 1 ? ["old-model"] : ["new-model"] });
      },
      onSave: (body) => {
        savedBodies.push(body);
        return json(
          baseConfig({
            api_key_configured: true,
            api_key_masked: "sk-****new",
            api_key_fingerprint: "fp-new"
          })
        );
      },
      onDelete: () =>
        json(
          baseConfig({
            configured: false,
            api_key_configured: false,
            api_key_masked: null,
            api_key_fingerprint: null
          })
        )
    });

    renderPage(queryClient);

    expect(await screen.findByText("已配置密钥")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /获取模型列表/ }));
    await waitFor(() =>
      expect(queryClient.getQueryData(["ai-provider", "deepseek", "models", "fingerprint:fp-old"])).toEqual({
        models: ["old-model"]
      })
    );

    await userEvent.type(screen.getByPlaceholderText("sk-****old"), "sk-new-secret");
    await userEvent.click(screen.getByRole("button", { name: /保存配置/ }));
    await waitFor(() => expect(savedBodies[0]?.api_key).toBe("sk-new-secret"));
    await waitFor(() =>
      expect(queryClient.getQueryData(["ai-provider", "deepseek", "models", "fingerprint:fp-old"])).toBeUndefined()
    );

    await userEvent.click(screen.getByRole("button", { name: /获取模型列表/ }));
    await waitFor(() =>
      expect(queryClient.getQueryData(["ai-provider", "deepseek", "models", "fingerprint:fp-new"])).toEqual({
        models: ["new-model"]
      })
    );

    await userEvent.click(screen.getByRole("button", { name: /删除密钥/ }));
    expect(await screen.findByText("未配置密钥")).toBeInTheDocument();
    await waitFor(() =>
      expect(queryClient.getQueryData(["ai-provider", "deepseek", "models", "fingerprint:fp-new"])).toBeUndefined()
    );
  });

  it("显示 FIELD_ENCRYPTION_KEY 和 DeepSeek Key 缺失的中文可操作提示", async () => {
    const error = vi.spyOn(message, "error");
    mockAiFetch({
      config: baseConfig({
        api_key_configured: true,
        api_key_masked: "sk-****abcd",
        api_key_fingerprint: "fp-old"
      }),
      onModels: () => json({ detail: "ai_configuration_error: DeepSeek API Key is not configured" }, 400),
      onSave: () => json({ detail: "ai_configuration_error: FIELD_ENCRYPTION_KEY is required for AI secrets" }, 400)
    });

    renderPage();

    expect(await screen.findByText("已配置密钥")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /获取模型列表/ }));
    await waitFor(() =>
      expect(error).toHaveBeenCalledWith("尚未配置 DeepSeek API Key，请先保存密钥后再获取模型或测试连接。")
    );

    await userEvent.type(screen.getByPlaceholderText("sk-****abcd"), "sk-new-secret");
    await userEvent.click(screen.getByRole("button", { name: /保存配置/ }));
    await waitFor(() =>
      expect(error).toHaveBeenCalledWith(
        "后端缺少 FIELD_ENCRYPTION_KEY，无法加密保存 DeepSeek Key。请配置后端环境变量并重启服务后再保存。"
      )
    );
  });
});

function mockAiFetch(options: {
  config: AiProviderConfig;
  models?: AiModelsResponse;
  onModels?: () => Response;
  onSave?: (body: Record<string, unknown>) => Response;
  onDelete?: () => Response;
  onTest?: () => Response;
}) {
  const requests: RequestRecord[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const body = parseBody(init);
    requests.push({ method, url, body });

    if (url.includes("/api/admin/auth/me")) {
      return Promise.resolve(json({ authenticated: true, username: "admin", csrf_token: "csrf" }));
    }
    if (url.includes("/api/admin/ai/runs")) {
      return Promise.resolve(json({ items: [], total: 0, page: 1, page_size: 10 }));
    }
    if (url.includes("/api/admin/ai/providers/deepseek/models")) {
      return Promise.resolve(options.onModels?.() ?? json(options.models ?? { models: [] }));
    }
    if (url.includes("/api/admin/ai/providers/deepseek/test")) {
      return Promise.resolve(options.onTest?.() ?? json({ status: "success" }));
    }
    if (url.includes("/api/admin/ai/providers/deepseek/key")) {
      return Promise.resolve(options.onDelete?.() ?? json({}));
    }
    if (url.includes("/api/admin/ai/providers/deepseek") && method === "PUT") {
      return Promise.resolve(options.onSave?.(body ?? {}) ?? json(options.config));
    }
    if (url.includes("/api/admin/ai/providers/deepseek")) {
      return Promise.resolve(json(options.config));
    }
    return Promise.resolve(json({}));
  });
  return { requests };
}

function parseBody(init?: RequestInit) {
  if (typeof init?.body !== "string") {
    return undefined;
  }
  return JSON.parse(init.body) as Record<string, unknown>;
}

function baseConfig(overrides: Partial<AiProviderConfig> = {}): AiProviderConfig {
  return {
    provider: "deepseek",
    enabled: false,
    auto_process_enabled: false,
    api_base: "https://api.deepseek.com",
    configured: true,
    api_key_configured: true,
    api_key_masked: "sk-****abcd",
    api_key_fingerprint: "fp-default",
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
    failures_today: 0,
    ...overrides
  };
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" }
  });
}
