import { describe, expect, it } from "vitest";

import { detectionToNormalizedRegion } from "./expressionFaceDetection";

describe("expressionFaceDetection", () => {
  it("converts and expands a pixel face box into a normalized head box", () => {
    const region = detectionToNormalizedRegion(
      { boundingBox: { originX: 100, originY: 50, width: 80, height: 100, angle: 0 } },
      400,
      200,
    );
    expect(region).not.toBeNull();
    expect(region?.x).toBeCloseTo(0.214);
    expect(region?.y).toBeCloseTo(0.11);
    expect(region?.width).toBeGreaterThan(0.2);
    expect(region?.height).toBeGreaterThan(0.5);
  });
});
