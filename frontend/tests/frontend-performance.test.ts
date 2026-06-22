/// <reference types="vite/client" />

import { describe, expect, it, vi } from "vitest";
import appSource from "../src/routes/App.tsx?raw";
import dashboardSource from "../src/pages/DashboardPage.tsx?raw";
import eventsSource from "../src/pages/EventsPage.tsx?raw";
import loginSource from "../src/pages/LoginPage.tsx?raw";
import mainSource from "../src/main.tsx?raw";
import queryConfigSource from "../src/queryConfig.ts?raw";
import {
  QUERY_REFETCH_INTERVAL,
  queryClientConfig,
  shouldRetryQuery,
  visibleOnlyRefetchInterval
} from "../src/queryConfig";
import viteConfig from "../vite.config.ts?raw";

describe("frontend performance constraints", () => {
  it("uses React.lazy for protected page routes", () => {
    expect(appSource).toContain("lazy(() => import(\"../pages/DashboardPage\")");
    expect(appSource).toContain("lazy(() => import(\"../pages/EventsPage\")");
    expect(appSource).toContain("lazy(() => import(\"../pages/AiSettingsPage\")");
    expect(appSource).not.toContain("import { DashboardPage }");
    expect(appSource).not.toContain("import { EventsPage }");
  });

  it("keeps charts out of the login route and lazy-loads ECharts from dashboard", () => {
    expect(loginSource).not.toContain("echarts");
    expect(dashboardSource).toContain("lazy(() => import(\"echarts-for-react\"))");
    expect(dashboardSource).not.toContain("import ReactECharts from \"echarts-for-react\"");
  });

  it("reads dev proxy target from VITE_API_PROXY_TARGET and avoids stale temp ports", () => {
    expect(viteConfig).toContain("VITE_API_PROXY_TARGET");
    expect(viteConfig).toContain("http://127.0.0.1:8000");
    expect(viteConfig).not.toContain("59133");
    expect(viteConfig).not.toContain("59134");
  });

  it("centralizes React Query defaults and does not retry 401 responses", () => {
    expect(mainSource).toContain("new QueryClient(queryClientConfig)");
    expect(queryConfigSource).toContain("refetchOnWindowFocus: false");
    expect(queryClientConfig.defaultOptions?.queries?.retry).toBe(shouldRetryQuery);
    expect(shouldRetryQuery(0, new Error("Authentication required"))).toBe(false);
    expect(shouldRetryQuery(0, new Error("HTTP 401"))).toBe(false);
    expect(shouldRetryQuery(0, new Error("HTTP 500"))).toBe(true);
    expect(shouldRetryQuery(1, new Error("HTTP 500"))).toBe(false);
  });

  it("gates event refresh by page visibility and defers facets", () => {
    let visibilityState: DocumentVisibilityState = "visible";
    vi.spyOn(document, "visibilityState", "get").mockImplementation(() => visibilityState);

    expect(visibleOnlyRefetchInterval(QUERY_REFETCH_INTERVAL.visibleEventsList)).toBe(10_000);
    expect(visibleOnlyRefetchInterval(QUERY_REFETCH_INTERVAL.visibleEventsList, true, 1)).toBe(20_000);
    expect(visibleOnlyRefetchInterval(QUERY_REFETCH_INTERVAL.visibleEventsList, true, 3)).toBe(false);
    visibilityState = "hidden";
    expect(visibleOnlyRefetchInterval(QUERY_REFETCH_INTERVAL.visibleEventsList)).toBe(false);

    expect(eventsSource).toContain("refetchInterval: (query)");
    expect(eventsSource).toContain("query.state.fetchFailureCount");
    expect(eventsSource).toContain("refetchIntervalInBackground: false");
    expect(eventsSource).toContain("loading={eventsQuery.isLoading}");
    expect(eventsSource).toContain("enabled: filterOpen");
  });
});
