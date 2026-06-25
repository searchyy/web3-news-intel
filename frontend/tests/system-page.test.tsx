import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SystemPage } from "../src/pages/SystemPage";

function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false }
    }
  });
}

function renderPage() {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <SystemPage />
    </QueryClientProvider>
  );
}

describe("系统状态页面", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("同时请求 health、queues 和 canary-runs 并展示运营状态", async () => {
    const { requests } = mockSystemFetch({
      health: {
        api: "ok",
        postgresql: "configured",
        redis: "configured",
        celery: "degraded",
        services: {
          scheduler: { status: "healthy", detail: "beat ok" }
        }
      },
      queues: {
        queues: [{ name: "fetch", depth: 7, status: "ready", workers: 2, updated_at: "2026-06-23T01:02:03Z" }]
      },
      canary: {
        runs: [
          {
            id: "canary-1",
            name: "live-source-canary",
            status: "success",
            started_at: "2026-06-23T01:00:00Z",
            finished_at: "2026-06-23T01:00:01Z",
            duration_ms: 321
          }
        ]
      }
    });

    renderPage();

    expect(await screen.findByText("系统状态")).toBeInTheDocument();
    await waitFor(() => {
      expect(requests).toEqual(
        expect.arrayContaining([
          "/api/admin/system/health",
          "/api/admin/system/queues",
          "/api/admin/system/canary-runs"
        ])
      );
    });
    expect(screen.getByText("PostgreSQL")).toBeInTheDocument();
    expect(screen.getByText("scheduler")).toBeInTheDocument();
    expect(screen.getByText("fetch")).toBeInTheDocument();
    expect(screen.getAllByText("7").length).toBeGreaterThan(0);
    expect(screen.getByText("live-source-canary")).toBeInTheDocument();
    expect(screen.getByText("321")).toBeInTheDocument();
  });

  it("字段缺失时保留核心服务并展示 unknown 或 empty", async () => {
    mockSystemFetch({
      health: { api: "ok" },
      queues: { queues: [{ name: "web3-news-intel" }] },
      canary: { runs: [] }
    });

    renderPage();

    expect(await screen.findByText("系统状态")).toBeInTheDocument();
    expect(screen.getByText("PostgreSQL")).toBeInTheDocument();
    expect(screen.getByText("web3-news-intel")).toBeInTheDocument();
    expect(screen.getAllByText("unknown").length).toBeGreaterThan(0);
    expect(screen.getAllByText("empty").length).toBeGreaterThan(0);
  });
});

function mockSystemFetch(options: { health: unknown; queues: unknown; canary: unknown }) {
  const requests: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const url = String(input);
    requests.push(url);
    if (url.includes("/api/admin/system/health")) {
      return Promise.resolve(json(options.health));
    }
    if (url.includes("/api/admin/system/queues")) {
      return Promise.resolve(json(options.queues));
    }
    if (url.includes("/api/admin/system/canary-runs")) {
      return Promise.resolve(json(options.canary));
    }
    return Promise.resolve(json({}));
  });
  return { requests };
}

function json(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" }
  });
}
