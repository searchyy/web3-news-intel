import { describe, expect, it, vi } from "vitest";
import { api } from "../src/api/client";

describe("api client", () => {
  it("redirects unauthorized responses to login without exposing response bodies", async () => {
    window.history.pushState({}, "", "/events");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("sensitive server detail", { status: 401 })
    );

    await expect(api("/api/admin/events")).rejects.toThrow("Authentication required");
    expect(window.location.pathname).toBe("/login");
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/api/admin/events",
      expect.objectContaining({ credentials: "include" })
    );

    vi.restoreAllMocks();
  });

  it("can suppress login redirects for background session checks", async () => {
    window.history.pushState({}, "", "/");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("not authenticated", { status: 401 }));

    await expect(api("/api/admin/auth/me", { authRedirect: false })).rejects.toThrow("HTTP 401");
    expect(window.location.pathname).toBe("/");
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/api/admin/auth/me",
      expect.not.objectContaining({ authRedirect: false })
    );

    vi.restoreAllMocks();
  });
});
