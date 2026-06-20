import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "../src/auth/AuthContext";
import { FeishuSettingsPage } from "../src/pages/FeishuSettingsPage";

function renderPage() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <AuthProvider>
        <FeishuSettingsPage />
      </AuthProvider>
    </QueryClientProvider>
  );
}

describe("飞书配置页面", () => {
  it("渲染中文配置表单并且只显示掩码 secret", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          FEISHU_APP_ID: "cli_a_test",
          FEISHU_APP_SECRET: "****alue",
          FEISHU_VERIFICATION_TOKEN: "****oken",
          FEISHU_ENCRYPT_KEY: "****-key",
          FEISHU_TEST_CHAT_ID: "oc_test",
          FEISHU_ENABLED: false,
          FEISHU_SEND_ENABLED: false,
          connection_status: "not_tested"
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );
    renderPage();
    expect(await screen.findByText("飞书配置")).toBeInTheDocument();
    expect(screen.getByText("未测试")).toBeInTheDocument();
    expect(await screen.findByDisplayValue("cli_a_test")).toBeInTheDocument();
    expect(await screen.findByDisplayValue("****alue")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("app-secret-value")).not.toBeInTheDocument();
    vi.restoreAllMocks();
  });

  it("保存配置前展示前端必填校验", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          FEISHU_ENABLED: false,
          FEISHU_SEND_ENABLED: false,
          connection_status: "not_tested"
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "保存配置" }));
    expect(await screen.findByText("请输入 App ID")).toBeInTheDocument();
    expect(await screen.findByText("请输入 App Secret 或保留已掩码值")).toBeInTheDocument();
    vi.restoreAllMocks();
  });
});
