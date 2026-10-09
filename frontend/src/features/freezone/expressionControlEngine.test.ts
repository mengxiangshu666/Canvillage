import { describe, expect, it } from "vitest";

import {
  EXPRESSION_CONTROL_REVISION,
  FINE_EXPRESSIONS,
  clampExpressionScale,
  compileExpressionMixContract,
  describeExpressionMix,
  expressionByKey,
} from "./expressionControlEngine";

describe("expressionControlEngine", () => {
  it("exposes the neutral baseline plus PixelSmile's 12 fine expressions", () => {
    expect(EXPRESSION_CONTROL_REVISION).toBe("pixelsmile-compatible.v1");
    expect(FINE_EXPRESSIONS).toHaveLength(13);
    expect(FINE_EXPRESSIONS.map((item) => item.key)).toEqual(expect.arrayContaining([
      "angry", "confused", "contempt", "confident", "disgust", "fear",
      "happy", "sad", "shy", "sleepy", "surprised", "anxious",
    ]));
  });

  it("clamps continuous strength to the supported neutral-to-target range", () => {
    expect(clampExpressionScale(-1)).toBe(0);
    expect(clampExpressionScale(0.75)).toBe(0.75);
    expect(clampExpressionScale(2)).toBe(1.5);
  });

  it("compiles an identity-preserving compound-expression contract", () => {
    const primary = expressionByKey("surprised");
    const secondary = expressionByKey("happy");
    const mix = { primary, secondary, scale: 1.2, secondaryWeight: 0.35 };
    expect(describeExpressionMix(mix)).toContain("惊讶 65% + 开心 35%");
    const contract = compileExpressionMixContract(mix);
    expect(contract).toContain("strong");
    expect(contract).toContain("35%");
    expect(contract.length).toBeLessThan(200);
  });
});
