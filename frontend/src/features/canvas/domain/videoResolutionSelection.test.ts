import { describe, expect, it } from "vitest";

import {
  normalizeVideoQualityValue,
} from "@/features/canvas/models/imageCapabilityValues";

type VideoQuality = string;

function resolutionToQuality(resolution: string): VideoQuality | null {
  const normalized = normalizeVideoQualityValue(resolution);
  if (normalized) return normalized;
  const raw = String(resolution || "").trim();
  return raw.length > 0 && raw.length <= 120 && !/[\u0000-\u001f\u007f]/.test(raw)
    ? raw
    : null;
}

function normalizeVideoQuality(
  value: VideoQuality | undefined,
  options: readonly VideoQuality[],
  preferred?: VideoQuality | null,
): VideoQuality {
  const fallback = preferred && options.includes(preferred)
    ? preferred
    : options.includes("720P")
      ? "720P"
      : options.includes("480P")
        ? "480P"
        : options.includes("768P")
          ? "768P"
          : options[0] ?? "480P";
  return value && options.includes(value) ? value : fallback;
}

describe("video node resolution selection", () => {
  it("uses the model default when the remembered 720P is not offered", () => {
    const options = ["768p", "2k"].map(resolutionToQuality).filter(Boolean) as string[];
    const preferred = resolutionToQuality("2k");
    expect(normalizeVideoQuality("720P", options, preferred)).toBe("2K");
  });

  it("keeps a provider label that includes orientation", () => {
    const label = "768p横(1376*768)";
    const options = [resolutionToQuality(label)].filter(Boolean) as string[];
    expect(normalizeVideoQuality("720P", options, options[0])).toBe(label);
  });
});
