// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  normalizeAspectRatioForLayout,
  resolveConfiguredAspectLayoutSize,
  resolveResizeMinConstraintsByAspect,
} from "@/features/canvas/application/imageNodeSizing";

/**
 * 缩放下限必须随图片比例换算成互相自洽的最小宽高，配合 keepAspectRatio 时短边为
 * 绑定约束；否则两个独立的 min 会把宽/竖屏图片卡成过大的最小框，导致无法缩小。
 */
describe("resolveResizeMinConstraintsByAspect", () => {
  it("scales a wide image's min box so the short edge is the binding floor", () => {
    const result = resolveResizeMinConstraintsByAspect("2:1", {
      minWidth: 140,
      minHeight: 140,
    });
    // 短边(高)=基准140，宽=140*2=280；而不是 140×140（会被 keepAspectRatio 卡到 280×140 但语义不清）
    expect(result.minHeight).toBe(140);
    expect(result.minWidth).toBe(280);
  });

  it("scales a tall image's min box so the short edge (width) is the floor", () => {
    const result = resolveResizeMinConstraintsByAspect("1:2", {
      minWidth: 140,
      minHeight: 140,
    });
    expect(result.minWidth).toBe(140);
    expect(result.minHeight).toBe(280);
  });
});

describe("resolveConfiguredAspectLayoutSize", () => {
  it("turns a landscape empty node into a compact 9:16 portrait node", () => {
    const result = resolveConfiguredAspectLayoutSize("9:16", {
      width: 580,
      height: 360,
    });

    expect(result.width).toBe(343);
    expect(result.height).toBe(609);
    expect(result.width / result.height).toBeCloseTo(9 / 16, 2);
  });

  it("uses 16:9 for an empty auto-layout node", () => {
    const result = resolveConfiguredAspectLayoutSize("auto", {
      width: 580,
      height: 360,
    });

    expect(result.width / result.height).toBeCloseTo(16 / 9, 2);
  });

  it("converts a fixed pixel slot into the node layout ratio", () => {
    expect(normalizeAspectRatioForLayout("992x432")).toBe("992:432");
    const result = resolveConfiguredAspectLayoutSize("992x432", {
      width: 580,
      height: 380,
    });
    expect(result.width / result.height).toBeCloseTo(992 / 432, 2);
  });
});
