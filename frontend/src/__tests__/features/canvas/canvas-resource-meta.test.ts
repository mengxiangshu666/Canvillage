// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  durationCheckLabel,
  formatByteSize,
  formatDurationSec,
  formatDurationSecondsShort,
  parseDurationCheck,
  parseResourceMeta,
  resourceMetaSummary,
} from "@/features/canvas/domain/canvasResourceMeta";

describe("parseResourceMeta", () => {
  it("keeps the facts the backend reported", () => {
    expect(
      parseResourceMeta({
        byteSize: 2048,
        mimeType: "video/mp4",
        durationSec: 5.125,
        width: 1280,
        height: 720,
        kind: "video",
      }),
    ).toEqual({
      byteSize: 2048,
      mimeType: "video/mp4",
      durationSec: 5.125,
      width: 1280,
      height: 720,
      kind: "video",
    });
  });

  it("accepts the snake_case spelling", () => {
    expect(parseResourceMeta({ byte_size: 512, mime_type: "image/png" })).toEqual({
      byteSize: 512,
      mimeType: "image/png",
    });
  });

  it("drops nonsense instead of inventing a zero", () => {
    expect(parseResourceMeta({ byteSize: 0, durationSec: -3, width: null })).toBeNull();
    expect(parseResourceMeta({})).toBeNull();
    expect(parseResourceMeta(null)).toBeNull();
    expect(parseResourceMeta("2048")).toBeNull();
    expect(parseResourceMeta([1, 2])).toBeNull();
  });

  it("reads numeric strings (JSON round-trips through the history file)", () => {
    expect(parseResourceMeta({ byteSize: "4096", width: "1920" })).toEqual({
      byteSize: 4096,
      width: 1920,
    });
  });

  it("ignores unrelated result keys rather than mistaking them for facts", () => {
    // 生成历史把整包 result 喂进来；只有具名键算数。
    expect(parseResourceMeta({ output_url: "/static/a.png", prompt: "猫" })).toBeNull();
  });
});

describe("formatByteSize", () => {
  it("keeps bytes whole and switches to 1024-based units", () => {
    expect(formatByteSize(512)).toBe("512 B");
    expect(formatByteSize(2048)).toBe("2 KB");
    expect(formatByteSize(1536)).toBe("1.5 KB");
    expect(formatByteSize(5 * 1024 * 1024)).toBe("5 MB");
  });

  it("returns null for a missing or zero size", () => {
    expect(formatByteSize(0)).toBeNull();
    expect(formatByteSize(undefined)).toBeNull();
    expect(formatByteSize(Number.NaN)).toBeNull();
  });
});

describe("formatDurationSec", () => {
  it("renders m:ss and rounds to the nearest second", () => {
    expect(formatDurationSec(5.125)).toBe("0:05");
    expect(formatDurationSec(65.4)).toBe("1:05");
    expect(formatDurationSec(600)).toBe("10:00");
  });

  it("returns null when the length is unknown", () => {
    expect(formatDurationSec(0)).toBeNull();
    expect(formatDurationSec(undefined)).toBeNull();
  });
});

describe("resourceMetaSummary", () => {
  it("joins the facts it has, in a fixed order", () => {
    expect(
      resourceMetaSummary({
        width: 1280,
        height: 720,
        byteSize: 2048,
        durationSec: 5.125,
      }),
    ).toBe("1280×720 · 2 KB · 0:05");
  });

  it("shows a partial strip when only some facts exist", () => {
    expect(resourceMetaSummary({ byteSize: 2048 })).toBe("2 KB");
    expect(resourceMetaSummary({ width: 1280 })).toBeNull();
  });

  it("is null when there is nothing to show", () => {
    expect(resourceMetaSummary(null)).toBeNull();
    expect(resourceMetaSummary({})).toBeNull();
  });
});

describe("parseDurationCheck", () => {
  it("keeps both sides and the backend verdict", () => {
    expect(
      parseDurationCheck({
        requestedSeconds: 30,
        actualSeconds: 15.05,
        match: false,
      }),
    ).toEqual({ requestedSeconds: 30, actualSeconds: 15.05, match: false });
  });

  it("accepts snake_case and numeric strings", () => {
    expect(
      parseDurationCheck({ requested_seconds: "30", actual_seconds: "30.0" }),
    ).toEqual({ requestedSeconds: 30, actualSeconds: 30 });
  });

  it("returns null when no side is provable (no invented attribution)", () => {
    expect(parseDurationCheck(null)).toBeNull();
    expect(parseDurationCheck({})).toBeNull();
    expect(
      parseDurationCheck({ requestedSeconds: 0, actualSeconds: -1 }),
    ).toBeNull();
    expect(parseDurationCheck([30, 15])).toBeNull();
  });

  it("keeps a partial payload when only the verdict is present", () => {
    expect(parseDurationCheck({ match: false })).toEqual({ match: false });
  });
});

describe("formatDurationSecondsShort", () => {
  it("keeps integers bare and trims fraction noise", () => {
    expect(formatDurationSecondsShort(30)).toBe("30s");
    expect(formatDurationSecondsShort(15.05)).toBe("15.05s");
    expect(formatDurationSecondsShort(15.1)).toBe("15.1s");
    expect(formatDurationSecondsShort(5.126)).toBe("5.13s");
  });
});

describe("durationCheckLabel", () => {
  it("names both sides", () => {
    expect(
      durationCheckLabel({ requestedSeconds: 30, actualSeconds: 15.05 }),
    ).toBe("请求 30s · 实得 15.05s");
  });

  it("is null unless both sides exist", () => {
    expect(durationCheckLabel(null)).toBeNull();
    expect(durationCheckLabel({ requestedSeconds: 30 })).toBeNull();
    expect(durationCheckLabel({ match: false })).toBeNull();
  });
});
