// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  evaluateResolutionHonesty,
  expectedLongEdgeForTier,
  normalizeResolutionTier,
} from "./resolutionHonesty";

describe("resolutionHonesty", () => {
  it("normalizes tier labels", () => {
    expect(normalizeResolutionTier("2K")).toBe("2k");
    expect(normalizeResolutionTier("720P")).toBe("720p");
    expect(normalizeResolutionTier(" 4k ")).toBe("4k");
  });

  it("maps image and video expected long edges", () => {
    expect(expectedLongEdgeForTier("1K", "image")).toBe(1024);
    expect(expectedLongEdgeForTier("2K", "image")).toBe(2048);
    expect(expectedLongEdgeForTier("4K", "image")).toBe(4096);
    expect(expectedLongEdgeForTier("720p", "video")).toBe(1280);
    expect(expectedLongEdgeForTier("1080P", "video")).toBe(1920);
    expect(expectedLongEdgeForTier("4k", "video-upscale")).toBe(3840);
  });

  it("marks true 2K image output as match", () => {
    const result = evaluateResolutionHonesty({
      media: "image",
      requestedTier: "2K",
      actualWidth: 2048,
      actualHeight: 1152,
    });
    expect(result?.match).toBe("match");
    expect(result?.isMismatch).toBe(false);
    expect(result?.badgeLabel).toContain("2K");
    expect(result?.badgeLabel).toContain("2048×1152");
  });

  it("flags undersized image output as below", () => {
    const result = evaluateResolutionHonesty({
      media: "image",
      requestedTier: "2K",
      actualWidth: 1024,
      actualHeight: 576,
    });
    expect(result?.match).toBe("below");
    expect(result?.isMismatch).toBe(true);
    expect(result?.badgeLabel).toContain("→");
  });

  it("accepts OpenAI-style 3840 long edge as 4K match", () => {
    const result = evaluateResolutionHonesty({
      media: "image",
      requestedTier: "4K",
      actualWidth: 3840,
      actualHeight: 2160,
    });
    expect(result?.match).toBe("match");
    expect(result?.isMismatch).toBe(false);
  });

  it("evaluates video 720p honesty", () => {
    const ok = evaluateResolutionHonesty({
      media: "video",
      requestedTier: "720p",
      actualWidth: 1280,
      actualHeight: 720,
    });
    expect(ok?.match).toBe("match");

    const low = evaluateResolutionHonesty({
      media: "video",
      requestedTier: "1080p",
      actualWidth: 1280,
      actualHeight: 720,
    });
    expect(low?.match).toBe("below");
  });

  it("returns null without actual pixels", () => {
    expect(
      evaluateResolutionHonesty({
        media: "image",
        requestedTier: "2K",
        actualWidth: 0,
        actualHeight: 0,
      }),
    ).toBeNull();
  });
});
