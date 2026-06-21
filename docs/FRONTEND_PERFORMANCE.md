# 前端性能报告

记录日期：2026-06-21
范围：`frontend/` 管理后台。

## 优化前基线

命令：

```powershell
Push-Location frontend
npm run build
Pop-Location
```

Vite 输出：

| 资源 | 原始大小 | gzip |
| --- | ---: | ---: |
| `index.html` | 0.71 KB | 0.35 KB |
| `assets/index-DdD1ndxr.css` | 0.39 KB | 0.26 KB |
| `assets/react-BT-prvNK.js` | 20.33 KB | 7.67 KB |
| `assets/index-DMEj2y8a.js` | 25.36 KB | 8.83 KB |
| `assets/query-kktWLXu3.js` | 43.58 KB | 13.41 KB |
| `assets/antd-BslFR9mb.js` | 1005.10 KB | 317.82 KB |
| `assets/charts-D9K6nuGO.js` | 1052.95 KB | 349.18 KB |

基线首屏 gzip 资源合计约 697.52 KB，其中图表库约 349.18 KB。

## 已实施优化

- 使用 `React.lazy` 拆分 Dashboard、Events、Sources、Feishu Groups、Feishu Settings、AI Settings、Rules、Deliveries、System、Audit。
- Dashboard 内部动态加载 `echarts-for-react`，只有真正渲染图表时才加载 ECharts。
- 新增 `/settings/ai` 页面，AI 配置不进入登录页首屏业务逻辑。
- 事件页改为服务端搜索和分页，支持 URL query 同步、300ms debounce、高级筛选、保存筛选和批量 AI 整理。
- 投递记录和审计日志请求增加 `page/page_size`。
- TanStack Query 默认 `staleTime=30000`，关闭窗口聚焦重复拉取。
- Vite dev proxy 从 `VITE_API_PROXY_TARGET` 读取，默认 `http://127.0.0.1:8000`，不提交临时端口。
- Vite 按 `react`、`query`、`antd`、`charts` 分包。
- Nginx 保留 gzip 和安全响应头，`/assets/` 长期缓存，`index.html` 使用 no-cache 语义。

## 优化后指标

最终 build 输出：

| 资源 | 原始大小 | gzip |
| --- | ---: | ---: |
| `index.html` | 0.64 KB | 0.33 KB |
| `assets/index-yuCknlOn.css` | 0.96 KB | 0.49 KB |
| `assets/index-BY-CnO8Y.js` | 9.11 KB | 3.51 KB |
| `assets/react-Dqay1uEn.js` | 163.56 KB | 53.70 KB |
| `assets/query-BHJm_E-A.js` | 35.76 KB | 10.58 KB |
| `assets/antd-YXjkM88Z.js` | 942.02 KB | 297.07 KB |
| `assets/DashboardPage-CM5jTd8p.js` | 1.77 KB | 0.96 KB |
| `assets/EventsPage-BrqcIwpU.js` | 18.03 KB | 6.36 KB |
| `assets/AiSettingsPage-DKwva5OB.js` | 7.45 KB | 2.97 KB |
| `assets/charts-1ro82zKq.js` | 1052.95 KB | 349.18 KB |

首屏 preload 资源：

- `index-BY-CnO8Y.js`
- `react-Dqay1uEn.js`
- `query-BHJm_E-A.js`
- `antd-YXjkM88Z.js`
- `index-yuCknlOn.css`

优化后首屏 gzip 资源合计约 365.68 KB，较基线减少约 331.84 KB。`charts` 不再由 `index.html` preload，登录路由不加载图表库。

## 请求数量对比

| 页面 | 优化前 | 优化后 |
| --- | --- | --- |
| 登录页 | 初始化路由树时可能解析受保护页面依赖 | 只做会话恢复请求，提交登录时再请求登录接口，不加载 Dashboard/ECharts |
| Dashboard | `/api/admin/dashboard/summary`，全局 30 秒 refetch | `/api/admin/dashboard/summary`，30 秒 staleTime，窗口聚焦不重复拉取 |
| 事件页 | `/api/admin/events?limit=100` | `/api/admin/events` 分页查询、`/api/admin/events/facets`、`/api/admin/saved-searches` |
| 投递记录 | `/api/admin/deliveries` 全量 | `/api/admin/deliveries?page=1&page_size=20` |
| 审计日志 | `/api/admin/audit-logs` 全量 | `/api/admin/audit-logs?page=1&page_size=20` |

## Lighthouse 或等价指标

本次未引入 Lighthouse CLI 依赖，也未访问外部网络安装新工具。等价本地指标采用：

- Vite production build 原始大小和 gzip 大小。
- `dist/index.html` preload 检查。
- `dist/assets` chunk 列表检查。
- Vitest 静态约束：路由懒加载、登录页不静态导入 ECharts、Dashboard 动态加载图表库、dev proxy 不固定临时端口。

## 尚存瓶颈

- Ant Design vendor 仍约 297.07 KB gzip，是当前登录首屏最大资源。
- ECharts vendor 仍约 349.18 KB gzip，但已从登录和非 Dashboard 初始路径移除。
- 真实浏览器 Lighthouse 和请求瀑布未在本机执行。
