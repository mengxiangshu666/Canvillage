// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * Requested size tier vs actual pixel honesty for Freezone media nodes.
 *
 * UI labels (1K/2K/4K, 720p/1080p) are request tiers sent upstream. Providers
 * may return nearby dimensions. This module classifies the gap so the node
 * badge can show "requested · actual" without pretending exact pixels.
 */

export type ResolutionMatchKind = "match" | "below" | "above" | "unknown";

export interface ResolutionHonestyInput {
  /** Requested tier label, e.g. "2K", "720p", "4k". */
  requestedTier: string | null | undefined;
  actualWidth: number | null | undefined;
  actualHeight: number | null | undefined;
  /** Image tiers use long-edge bases; video tiers use common long-edge maps. */
  media: "image" | "video" | "video-upscale";
}

export interface ResolutionHonestyResult {
  requestedTier: string;
  actualWidth: number;
  actualHeight: number;
  actualLongEdge: number;
  expectedLongEdge: number | null;
  match: ResolutionMatchKind;
  /** Compact badge primary line, e.g. "2K · 2048×1152". */
  badgeLabel: string;
  /** Full tooltip for hover. */
  tooltip: string;
  /** True when actual long edge is meaningfully off the requested tier. */
  isMismatch: boolean;
}

/** Image size tier → expected long-edge pixels (Freezone jobs._SIZE_BASE). */
export const IMAGE_TIER_LONG_EDGE: Record<string, number> = {
  "0.5k": 512,
  "1k": 1024,
  "2k": 2048,
  // OpenAI clamps 4K near 3840; native map uses 4096. Accept either as "4K tier".
  "4k": 4096,
};

/** Native video gen resolution → expected long edge (16:9 landscape). */
export const VIDEO_TIER_LONG_EDGE: Record<string, number> = {
  "480p": 854,
  "720p": 1280,
  "1080p": 1920,
  "4k": 3840,
};

/** Video upscale panel long-edge targets (ffmpeg scale). */
export const VIDEO_UPSCALE_TIER_LONG_EDGE: Record<string, number> = {
  "1080p": 1920,
  "2k": 2560,
  "4k": 3840,
};

const RELATIVE_TOLERANCE = 0.12;
const ABSOLUTE_TOLERANCE_PX = 96;

export function normalizeResolutionTier(raw: string | null | undefined): string {
  return String(raw || "")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, "");
}

export function expectedLongEdgeForTier(
  tier: string | null | undefined,
  media: ResolutionHonestyInput["media"],
): number | null {
  const key = normalizeResolutionTier(tier);
  if (!key) return null;
  if (media === "image") {
    if (key === "4k") return 4096;
    return IMAGE_TIER_LONG_EDGE[key] ?? null;
  }
  if (media === "video-upscale") {
    return VIDEO_UPSCALE_TIER_LONG_EDGE[key] ?? null;
  }
  return VIDEO_TIER_LONG_EDGE[key] ?? null;
}

/**
 * 4K image providers often return ~3840 long edge; treat that as matching the 4K tier.
 */
function effectiveExpectedBand(
  expected: number,
  media: ResolutionHonestyInput["media"],
  tierKey: string,
): { min: number; max: number; center: number } {
  if (media === "image" && tierKey === "4k") {
    // Accept OpenAI-style 3840 and full 4096 as the same tier.
    const lo = 3840 * (1 - RELATIVE_TOLERANCE) - ABSOLUTE_TOLERANCE_PX;
    const hi = 4096 * (1 + RELATIVE_TOLERANCE) + ABSOLUTE_TOLERANCE_PX;
    return { min: lo, max: hi, center: 3968 };
  }
  const rel = expected * RELATIVE_TOLERANCE;
  return {
    min: expected - Math.max(rel, ABSOLUTE_TOLERANCE_PX),
    max: expected + Math.max(rel, ABSOLUTE_TOLERANCE_PX),
    center: expected,
  };
}

export function classifyLongEdgeMatch(
  actualLongEdge: number,
  expectedLongEdge: number | null,
  media: ResolutionHonestyInput["media"],
  tierKey: string,
): ResolutionMatchKind {
  if (!expectedLongEdge || actualLongEdge <= 0) return "unknown";
  const band = effectiveExpectedBand(expectedLongEdge, media, tierKey);
  if (actualLongEdge >= band.min && actualLongEdge <= band.max) return "match";
  return actualLongEdge < band.center ? "below" : "above";
}

export function formatPixels(width: number, height: number): string {
  return `${Math.round(width)}×${Math.round(height)}`;
}

export function displayTierLabel(tier: string | null | undefined): string {
  const key = normalizeResolutionTier(tier);
  if (!key) return "—";
  if (key.endsWith("p")) return key.toUpperCase();
  // 1k / 2k / 4k → 1K / 2K / 4K
  if (/^\d+(\.\d+)?k$/.test(key)) return key.toUpperCase();
  return String(tier || "").trim() || "—";
}

export function evaluateResolutionHonesty(
  input: ResolutionHonestyInput,
): ResolutionHonestyResult | null {
  const w = Number(input.actualWidth);
  const h = Number(input.actualHeight);
  if (!Number.isFinite(w) || !Number.isFinite(h) || w <= 0 || h <= 0) {
    return null;
  }
  const tierRaw = String(input.requestedTier || "").trim();
  const tierKey = normalizeResolutionTier(tierRaw);
  const expected = expectedLongEdgeForTier(tierRaw, input.media);
  const actualLongEdge = Math.max(w, h);
  const match = classifyLongEdgeMatch(actualLongEdge, expected, input.media, tierKey);
  const tierLabel = displayTierLabel(tierRaw || null);
  const pixels = formatPixels(w, h);
  const isMismatch = match === "below" || match === "above";

  let badgeLabel: string;
  if (tierRaw) {
    badgeLabel = isMismatch ? `${tierLabel}→${pixels}` : `${tierLabel} · ${pixels}`;
  } else {
    badgeLabel = pixels;
  }

  const matchZh =
    match === "match"
      ? "与请求档位一致"
      : match === "below"
        ? "实际长边低于请求档位"
        : match === "above"
          ? "实际长边高于请求档位"
          : "无法对照请求档位";

  const expectedText =
    expected != null
      ? input.media === "image" && tierKey === "4k"
        ? "约 3840–4096px 长边"
        : `约 ${expected}px 长边`
      : "未知";

  const tooltip = [
    tierRaw ? `请求档位：${tierLabel}（${expectedText}）` : "请求档位：未记录",
    `实际像素：${pixels}（长边 ${Math.round(actualLongEdge)}）`,
    matchZh,
    "说明：档位是上行请求语义，最终像素由模型/提供商决定。",
  ].join("\n");

  return {
    requestedTier: tierLabel,
    actualWidth: Math.round(w),
    actualHeight: Math.round(h),
    actualLongEdge: Math.round(actualLongEdge),
    expectedLongEdge: expected,
    match,
    badgeLabel,
    tooltip,
    isMismatch,
  };
}
