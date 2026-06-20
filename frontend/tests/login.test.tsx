import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ConfigProvider } from "antd";
import { App } from "../src/routes/App";

function renderApp() {
  return render(
    <ConfigProvider>
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter
          initialEntries={["/login"]}
          future={{ v7_relativeSplatPath: true, v7_startTransition: true }}
        >
          <App />
        </MemoryRouter>
      </QueryClientProvider>
    </ConfigProvider>
  );
}

describe("login flow", () => {
  it("logs in with a server-side session response", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ authenticated: true, username: "admin", csrf_token: "csrf" }), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      })
    );
    renderApp();
    await userEvent.type(screen.getByLabelText("用户名"), "admin");
    await userEvent.type(screen.getByLabelText("密码"), "password");
    await userEvent.click(screen.getByRole("button", { name: /登\s*录/ }));
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/admin/auth/login",
      expect.objectContaining({ credentials: "include" })
    );
    fetchMock.mockRestore();
  });
});
