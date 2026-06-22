import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AuthProvider, useAuth } from "../src/auth/AuthContext";

function SessionProbe() {
  const { loading, user } = useAuth();
  return <div>{loading ? "loading" : user?.username ?? "none"}</div>;
}

describe("AuthProvider", () => {
  it("restores an existing server session on mount", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ authenticated: true, username: "admin", csrf_token: "csrf" }), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      })
    );

    render(
      <AuthProvider>
        <SessionProbe />
      </AuthProvider>
    );

    expect(screen.getByText("loading")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("admin")).toBeInTheDocument());
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/admin/auth/me",
      expect.objectContaining({ credentials: "include" })
    );

    fetchMock.mockRestore();
  });
});
