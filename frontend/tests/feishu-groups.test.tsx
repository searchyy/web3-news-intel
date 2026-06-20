import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { FeishuGroupsPage } from "../src/pages/FeishuGroupsPage";
import { AuthProvider } from "../src/auth/AuthContext";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

describe("飞书群组页面", () => {
  it("使用中文只写 Webhook 文案且默认不渲染完整 Webhook URL", () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify([]), { status: 200, headers: { "Content-Type": "application/json" } })
    );
    render(
      <QueryClientProvider client={new QueryClient()}>
        <AuthProvider>
          <FeishuGroupsPage />
        </AuthProvider>
      </QueryClientProvider>
    );
    expect(screen.getByText("添加飞书 Webhook")).toBeInTheDocument();
    expect(screen.queryByText(/open-apis\/bot\/v2\/hook/)).not.toBeInTheDocument();
    vi.restoreAllMocks();
  });
});
