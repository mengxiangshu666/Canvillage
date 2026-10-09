// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import { evaluateDurationHonesty } from "@/features/canvas/domain/durationHonesty";

describe("evaluateDurationHonesty", () => {
  it("flags a real shortfall with both sides in the label", () => {
    const result = evaluateDurationHonesty({
      requestedSeconds: 30,
      actualSeconds: 15.05,
    });
    expect(result).not.toBeNull();
    expect(result!.isMismatch).toBe(true);
    expect(result!.badgeLabel).toBe("30s → 15.05s");
    expect(result!.tooltip).toContain("请求时长：30s");
    expect(result!.tooltip).toContain("实际时长：15.05s");
  });

  it("treats sub-tolerance drift as consistent", () => {
    const result = evaluateDurationHonesty({
      requestedSeconds: 5,
      actualSeconds: 5.4,
    });
    expect(result!.isMismatch).toBe(false);
    expect(result!.badgeLabel).toBe("5.4s");
  });

  it("does not judge when either side is missing or nonsense", () => {
    expect(evaluateDurationHonesty({ requestedSeconds: 30 })).toBeNull();
    expect(evaluateDurationHonesty({ actualSeconds: 15 })).toBeNull();
    expect(
      evaluateDurationHonesty({ requestedSeconds: 0, actualSeconds: 15 }),
    ).toBeNull();
    expect(
      evaluateDurationHonesty({
        requestedSeconds: 30,
        actualSeconds: Number.NaN,
      }),
    ).toBeNull();
    expect(evaluateDurationHonesty({})).toBeNull();
  });

  it("honors an explicit tolerance override", () => {
    const strict = evaluateDurationHonesty({
      requestedSeconds: 5,
      actualSeconds: 5.4,
      toleranceSeconds: 0.1,
    });
    expect(strict!.isMismatch).toBe(true);
  });
});
