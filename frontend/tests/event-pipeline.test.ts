import { describe, expect, it } from "vitest";
import {
  formatPipelinePreview,
  normalizeEventPipeline,
  pipelineStatusText,
  redactSensitiveText
} from "../src/api/eventPipeline";

describe("event pipeline client", () => {
  it("归一化处理时间线、AI 状态和飞书 Delivery 状态", () => {
    const pipeline = normalizeEventPipeline({
      event_id: 1,
      fetch: { status: "succeeded", title: "源站抓取完成" },
      parse: { status: "completed", title: "正文解析完成" },
      event: { status: "ok", title: "事件已入库" },
      ai_jobs: [{ job_id: "job-1", status: "retrying", retry_count: 1 }],
      deliveries: [{ delivery_id: 7, status: "sent", channel: "feishu", target: "Mock 飞书群" }]
    });

    expect(pipeline.items.map((item) => item.stage)).toEqual(["fetch", "parse", "event", "ai", "feishu"]);
    expect(pipeline.items.find((item) => item.stage === "ai")?.statusLabel).toBe("重试中");
    expect(pipeline.items.find((item) => item.stage === "feishu")?.statusLabel).toBe("已送达");
    expect(pipeline.delivery?.status).toBe("delivered");
  });

  it("提供飞书队列、发送、dry-run 和抑制状态中文文案", () => {
    expect(pipelineStatusText("feishu", "queued")).toBe("待发送");
    expect(pipelineStatusText("feishu", "sending")).toBe("发送中");
    expect(pipelineStatusText("feishu", "dry_run")).toBe("未实发");
    expect(pipelineStatusText("feishu", "suppressed")).toBe("已抑制");
  });

  it("卡片预览和文本不会回显密钥或 webhook", () => {
    const preview = formatPipelinePreview({
      title: "飞书卡片",
      app_secret_for_test: "__example_secret_value__",
      webhook_url: "https://example.invalid/redacted-webhook/token",
      body: { text: "ok", token: "plain-token" }
    });

    expect(preview).toContain("飞书卡片");
    expect(preview).toContain("已隐藏");
    expect(preview).not.toContain("__example_secret_value__");
    expect(preview).not.toContain("open-apis/bot");
    expect(redactSensitiveText("sk-test-secret-123456")).toBe("已隐藏");
  });
});
