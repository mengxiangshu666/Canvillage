import { describe, expect, it } from "vitest";

import { estimateAgentContextUsage } from "./agent-context-usage";

describe("agent context usage", () => {
  it("reports an explainable remaining percentage", () => {
    const usage = estimateAgentContextUsage({
      model: { id: "direct/model", label: "模型", contextLength: 10_000, maxOutputTokens: 1_000 },
      messages: [{ id: "1", role: "user", text: "搭建一个三镜头分镜", turnId: "t", timestamp: 1 }],
      draft: "把第二镜改成夜景",
    });
    expect(usage.available).toBe(true);
    expect(usage.remainingPercent).toBeGreaterThan(0);
    expect(usage.detail).toContain("估算");
  });

  it("does not pretend to know a model window that was not returned", () => {
    expect(estimateAgentContextUsage({ model: { id: "model", label: "模型" }, messages: [] }).label).toBe("上下文待模型");
  });
});
