import react from "@vitejs/plugin-react";
import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const apiTarget = env.VITE_API_PROXY_TARGET || "http://127.0.0.1:8000";

  return {
    plugins: [react()],
    build: {
      chunkSizeWarningLimit: 1200,
      rollupOptions: {
        output: {
          manualChunks(id) {
            if (!id.includes("node_modules")) {
              return undefined;
            }
            if (id.includes("echarts") || id.includes("zrender")) {
              return "charts";
            }
            if (id.includes("@tanstack")) {
              return "query";
            }
            if (id.includes("antd") || id.includes("@ant-design") || id.includes("rc-")) {
              return "antd";
            }
            if (id.includes("react") || id.includes("react-router-dom")) {
              return "react";
            }
            return undefined;
          }
        }
      }
    },
    server: {
      port: 5173,
      proxy: {
        "/api": {
          target: apiTarget,
          changeOrigin: true
        },
        "/integrations": {
          target: apiTarget,
          changeOrigin: true
        }
      }
    },
    test: {
      environment: "jsdom",
      setupFiles: "src/test/setup.ts",
      globals: true,
      exclude: ["node_modules/**", "dist/**", "e2e/**"]
    }
  }
});
