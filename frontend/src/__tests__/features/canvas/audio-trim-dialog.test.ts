// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  audibleRangeMs,
  clampTrimRangeMs,
  formatTrimClock,
  MIN_TRIM_MS,
} from "@/features/canvas/ui/AudioTrimDialog";

/** 构造一段「静音 → 有声 → 静音」的峰值，便于断言自动裁剪窗口。 */
function peaksWithAudible(silentBefore: number, audible: number, silentAfter: number) {
  const values = [
    ...Array.from({ length: silentBefore }, () => 0.01),
    ...Array.from({ length: audible }, () => 0.9),
    ...Array.from({ length: silentAfter }, () => 0.01),
  ];
  return new Float32Array(values);
}

describe("audibleRangeMs", () => {
  it("returns the full clip when peaks are missing or entirely silent", () => {
    expect(audibleRangeMs(null, 10_000)).toEqual({ startMs: 0, endMs: 10_000 });
    expect(audibleRangeMs(new Float32Array([]), 10_000)).toEqual({
      startMs: 0,
      endMs: 10_000,
    });
    const silent = new Float32Array(100).fill(0.01);
    expect(audibleRangeMs(silent, 10_000)).toEqual({ startMs: 0, endMs: 10_000 });
  });

  it("trims leading and trailing silence with a small pad", () => {
    // 100 桶 / 10 秒 → 每桶 100ms；有声区在 20–59 桶。
    const peaks = peaksWithAudible(20, 40, 40);
    const range = audibleRangeMs(peaks, 10_000, { padMs: 100 });

    expect(range.startMs).toBe(1900);
    expect(range.endMs).toBe(6100);
  });

  it("keeps the full clip when the audible part is shorter than the minimum", () => {
    // 1000 桶 / 10 秒 → 每桶 10ms；只响一桶且不留余量 → 短于 MIN_TRIM_MS。
    const peaks = peaksWithAudible(500, 1, 499);
    expect(audibleRangeMs(peaks, 10_000, { padMs: 0 })).toEqual({
      startMs: 0,
      endMs: 10_000,
    });
  });

  it("ignores duration-less audio instead of producing an empty range", () => {
    expect(audibleRangeMs(peaksWithAudible(10, 20, 10), 0)).toEqual({
      startMs: 0,
      endMs: 0,
    });
  });
});

describe("clampTrimRangeMs", () => {
  it("clamps a dragged handle inside the clip", () => {
    expect(clampTrimRangeMs({ startMs: -500, endMs: 12_000 }, 10_000)).toEqual({
      startMs: 0,
      endMs: 10_000,
    });
  });

  it("pushes a too-short range right, then left at the clip end", () => {
    expect(
      clampTrimRangeMs({ startMs: 5_000, endMs: 5_050 }, 10_000),
    ).toEqual({ startMs: 5_000, endMs: 5_000 + MIN_TRIM_MS });
    expect(
      clampTrimRangeMs({ startMs: 9_990, endMs: 9_995 }, 10_000),
    ).toEqual({ startMs: 10_000 - MIN_TRIM_MS, endMs: 10_000 });
  });
});

describe("formatTrimClock", () => {
  it("renders mm:ss.t and floors negatives", () => {
    expect(formatTrimClock(0)).toBe("00:00.0");
    expect(formatTrimClock(65_400)).toBe("01:05.4");
    expect(formatTrimClock(-1)).toBe("00:00.0");
  });
});
