import { describe, expect, it } from "vitest";
import {
  aiJobPollInterval,
  inputQualityText,
  normalizeAiJobErrorMessage,
  normalizeAiJobFromSubmitResponse,
  shouldWarnInputQuality
} from "../src/api/aiJobs";

describe("AI job client", () => {
  it("兼容旧 task_id 和新 job_id 响应", () => {
    const legacy = normalizeAiJobFromSubmitResponse({
      queued: true,
      task_id: "celery-task-1",
      event_id: 7,
      status: "queued"
    });
    const current = normalizeAiJobFromSubmitResponse({
      job_id: "job-1",
      event_ids: [7],
      status: "started"
    });

    expect(legacy?.job_id).toBe("celery-task-1");
    expect(legacy?.status).toBe("queued");
    expect(current?.job_id).toBe("job-1");
    expect(current?.status).toBe("started");
  });

  it("按时间窗口计算轮询间隔，并在隐藏页或终态停止", () => {
    const startedAt = 1_000;

    expect(aiJobPollInterval(startedAt, "queued", true, startedAt + 5_000)).toBe(1_000);
    expect(aiJobPollInterval(startedAt, "started", true, startedAt + 12_000)).toBe(2_000);
    expect(aiJobPollInterval(startedAt, "retrying", true, startedAt + 35_000)).toBe(5_000);
    expect(aiJobPollInterval(startedAt, "queued", false, startedAt + 5_000)).toBe(false);
    expect(aiJobPollInterval(startedAt, "succeeded", true, startedAt + 5_000)).toBe(false);
    expect(aiJobPollInterval(startedAt, "queued", true, startedAt + 90_000)).toBe(false);
  });

  it("提供输入质量提示和 Redis/Worker 中文错误", () => {
    expect(inputQualityText("title_only")).toBe("仅标题");
    expect(shouldWarnInputQuality("title_only")).toBe(true);
    expect(normalizeAiJobErrorMessage("Redis connection refused")).toContain("Redis 不可用");
    expect(normalizeAiJobErrorMessage("Celery worker heartbeat expired")).toContain("Celery Worker 未运行");

  });


  it("maps AI budget errors to a clear Chinese message", () => {
    expect(normalizeAiJobErrorMessage("ai_budget_exceeded: AI daily token budget exceeded")).toContain("Token ?????");
    expect(normalizeAiJobErrorMessage("AI daily request budget exceeded")).toContain("???????");
  });
});
