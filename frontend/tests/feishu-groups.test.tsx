import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { FeishuGroupsPage } from "../src/pages/FeishuGroupsPage";
import { AuthProvider } from "../src/auth/AuthContext";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

describe("Feishu groups page", () => {
  it("uses write-only webhook wording and never renders a full webhook URL by default", () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <AuthProvider>
          <FeishuGroupsPage />
        </AuthProvider>
      </QueryClientProvider>
    );
    expect(screen.getByText("添加飞书 Webhook")).toBeInTheDocument();
    expect(screen.queryByText(/open-apis\/bot\/v2\/hook/)).not.toBeInTheDocument();
  });
});
