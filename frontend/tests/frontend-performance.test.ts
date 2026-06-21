/// <reference types="vite/client" />

import { describe, expect, it } from "vitest";
import appSource from "../src/routes/App.tsx?raw";
import dashboardSource from "../src/pages/DashboardPage.tsx?raw";
import loginSource from "../src/pages/LoginPage.tsx?raw";
import viteConfig from "../vite.config.ts?raw";

describe("前端性能约束", () => {
  it("受保护页面使用 React.lazy 路由懒加载", () => {
    expect(appSource).toContain("lazy(() => import(\"../pages/DashboardPage\")");
    expect(appSource).toContain("lazy(() => import(\"../pages/EventsPage\")");
    expect(appSource).toContain("lazy(() => import(\"../pages/AiSettingsPage\")");
    expect(appSource).not.toContain("import { DashboardPage }");
    expect(appSource).not.toContain("import { EventsPage }");
  });

  it("登录路由不会静态引入 ECharts，Dashboard 渲染时才动态加载图表库", () => {
    expect(loginSource).not.toContain("echarts");
    expect(dashboardSource).toContain("lazy(() => import(\"echarts-for-react\"))");
    expect(dashboardSource).not.toContain("import ReactECharts from \"echarts-for-react\"");
  });

  it("开发代理从 VITE_API_PROXY_TARGET 读取且不固定临时端口", () => {
    expect(viteConfig).toContain("VITE_API_PROXY_TARGET");
    expect(viteConfig).not.toContain("59133");
  });
});
