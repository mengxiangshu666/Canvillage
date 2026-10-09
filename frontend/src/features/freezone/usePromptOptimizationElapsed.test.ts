import { describe, expect, it } from "vitest";

import { promptOptimizationProgressLabel } from "./usePromptOptimizationElapsed";

describe("promptOptimizationProgressLabel", () => {
  it("shows real phases and elapsed time instead of a frozen spinner label", () => {
    expect(promptOptimizationProgressLabel(0)).toBe("读取模型与知识");
    expect(promptOptimizationProgressLabel(7)).toBe("AI 改写 7s");
    expect(promptOptimizationProgressLabel(28)).toBe("模型仍在处理 28s");
  });
});
