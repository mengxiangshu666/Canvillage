// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

export type PromptReferenceKind = "image" | "video" | "audio";

export interface PromptReferenceMedia {
  kind: PromptReferenceKind;
  withinCap?: boolean;
  url?: string | null;
  label?: string | null;
  visualSummary?: string | null;
}

export interface PromptOptimizationReference {
  name: string;
  kind: PromptReferenceKind;
  order: number;
  role: string;
  url?: string;
  label?: string;
  visual_summary?: string;
}

const REFERENCE_PREFIX: Record<PromptReferenceKind, string> = {
  image: "图片",
  video: "视频",
  audio: "音频",
};

const LOCKED_CONSTRAINT_MARKER =
  /(?:锁定|固定|必须|保持|不得|不要|禁止|负向|negative\b|must\b|keep\b|preserve\b|do not\b|don't\b|never\b)/i;
const REFERENCE_MENTION = /@(?:图片|图|视频|音频)\d+/i;

/**
 * Preserve hard prompt constraints verbatim when an optimizer paraphrases or
 * drops them. Only explicitly locked/negative/reference clauses are appended;
 * ordinary creative prose remains fully replaceable by the optimized draft.
 */
export function mergePromptOptimizationLockedConstraints(
  originalPrompt: string,
  optimizedPrompt: string,
): string {
  const optimized = optimizedPrompt.trim();
  if (!optimized) return originalPrompt.trim();

  const lockedClauses = originalPrompt
    .split(/(?:\r?\n)+|[；;]+/)
    .map((clause) => clause.trim())
    .filter(Boolean)
    .filter((clause) => LOCKED_CONSTRAINT_MARKER.test(clause) || REFERENCE_MENTION.test(clause));
  if (lockedClauses.length === 0) return optimized;

  const normalize = (value: string) => value.toLocaleLowerCase().replace(/\s+/g, "");
  const normalizedOptimized = normalize(optimized);
  const missing = Array.from(new Set(lockedClauses)).filter(
    (clause) => !normalizedOptimized.includes(normalize(clause)),
  );
  if (missing.length === 0) return optimized;
  return `${optimized}\n\n锁定约束（原文保留）：\n${missing.map((clause) => `- ${clause}`).join("\n")}`;
}

/**
 * Build the exact reference vocabulary exposed by the video node. Numbering is
 * per media type, matching PromptMentionEditor and backend submission order.
 * References the active generation mode will not submit are intentionally
 * omitted so the optimizer cannot invent unusable @ mentions.
 */
export function buildVideoPromptOptimizationReferences(
  genMode: string,
  media: readonly PromptReferenceMedia[],
): PromptOptimizationReference[] {
  if (genMode === "textToVideo") return [];

  const typeCounts: Record<PromptReferenceKind, number> = {
    image: 0,
    video: 0,
    audio: 0,
  };
  const output: PromptOptimizationReference[] = [];

  for (const item of media) {
    typeCounts[item.kind] += 1;
    if (item.withinCap === false) continue;
    const typeIndex = typeCounts[item.kind];

    if (
      (genMode === "imageToVideo" || genMode === "imageReference" || genMode === "firstLastFrame") &&
      item.kind !== "image"
    ) {
      continue;
    }
    if (genMode === "videoEdit" && item.kind === "audio") continue;

    let role = `${item.kind}_reference`;
    if (genMode === "firstLastFrame" && item.kind === "image") {
      role = typeIndex === 1 ? "first_frame" : typeIndex === 2 ? "last_frame" : "unused";
      if (role === "unused") continue;
    } else if (genMode === "imageToVideo" && item.kind === "image") {
      if (typeIndex > 9) continue;
      role = typeIndex === 1 ? "first_frame" : "identity_or_scene_reference";
    } else if (genMode === "imageReference" && item.kind === "image") {
      if (typeIndex > 9) continue;
      role = "identity_or_scene_reference";
    } else if (genMode === "videoEdit" && item.kind === "video") {
      role = typeIndex === 1 ? "source_video" : "unused";
      if (role === "unused") continue;
    } else if (genMode === "videoEdit" && item.kind === "image") {
      if (typeIndex > 5) continue;
      role = "edit_reference_image";
    } else if (genMode === "allReference") {
      role = item.kind === "video"
        ? "motion_or_camera_reference"
        : item.kind === "audio"
          ? "music_or_audio_reference"
          : "identity_scene_or_prop_reference";
    }

    const reference: PromptOptimizationReference = {
      name: `@${REFERENCE_PREFIX[item.kind]}${typeIndex}`,
      kind: item.kind,
      order: output.length + 1,
      role,
    };
    if (item.url) reference.url = item.url;
    if (item.label) reference.label = item.label;
    if (item.visualSummary) reference.visual_summary = item.visualSummary;
    output.push(reference);
  }
  return output;
}
