import { describe, expect, it } from "vitest";

import { optimizePromptForModel } from "./modelPromptOptimizer";

const base = {
  prompt: "一个女孩在雨夜街头，要有电影感和高级感，高清，不要崩",
  modelId: "seedream-4.0",
  apiModel: "doubao-seedream-4-0",
  modelLabel: "Seedream 4.0",
  aspectRatio: "9:16",
  size: "2K",
  quality: "high",
  cameraSummary: "Sony Venice 2 · 50mm · f/1.8",
  styleSummary: "电影写实",
  hasReferences: true,
};

describe("modelPromptOptimizer", () => {
  it("turns vague Chinese into concrete Seedream-oriented structure", () => {
    const result = optimizePromptForModel(base);
    expect(result.profileId).toBe("seedream-structured-zh");
    expect(result.optimizedPrompt).toContain("【创作目标】");
    expect(result.optimizedPrompt).toContain("动机明确的光线");
    expect(result.optimizedPrompt).toContain("准确的材质细节");
    expect(result.optimizedPrompt).toContain("Sony Venice 2 · 50mm · f/1.8");
    expect(result.optimizedPrompt).toContain("9:16 · 2K · high");
    expect(result.optimizedPrompt).toContain("参考图优先");
  });

  it("uses a different instruction hierarchy for GPT Image", () => {
    const result = optimizePromptForModel({
      ...base,
      modelId: "huimeng/gpt-image-2",
      apiModel: "gpt-image-2",
      modelLabel: "GPT Image 2",
    });
    expect(result.profileId).toBe("gpt-image-instruction");
    expect(result.optimizedPrompt).toContain("TASK:");
    expect(result.optimizedPrompt).toContain("MUST PRESERVE / AVOID:");
  });

  it("never mutates an empty prompt into invented content", () => {
    expect(() => optimizePromptForModel({ ...base, prompt: "  " })).toThrow("请先输入提示词");
  });
});
