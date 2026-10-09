import { describe, expect, it } from "vitest";

import {
  containedImageRect,
  moveNormalizedRegion,
  normalizedRegionFromPoints,
  resizeNormalizedRegion,
} from "./expressionRegionSelection";

describe("expressionRegionSelection", () => {
  it("creates a true drag rectangle in either pointer direction", () => {
    expect(normalizedRegionFromPoints({ x: 0.8, y: 0.7 }, { x: 0.2, y: 0.1 })).toEqual({
      x: 0.2,
      y: 0.1,
      width: 0.6000000000000001,
      height: 0.6,
    });
  });

  it("moves and resizes while keeping the box inside the image", () => {
    const moved = moveNormalizedRegion({ x: 0.7, y: 0.7, width: 0.25, height: 0.25 }, 0.4, 0.4);
    expect(moved.x).toBe(0.75);
    expect(moved.y).toBe(0.75);
    expect(resizeNormalizedRegion(moved, { x: 1, y: 1 })).toMatchObject({ width: 0.25, height: 0.25 });
  });

  it("maps object-contain overlays to the actual rendered image, not letterboxing", () => {
    expect(containedImageRect({ containerWidth: 400, containerHeight: 200, naturalWidth: 100, naturalHeight: 100 })).toEqual({
      left: 100,
      top: 0,
      width: 200,
      height: 200,
    });
  });
});
