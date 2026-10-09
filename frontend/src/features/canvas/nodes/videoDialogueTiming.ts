// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

export interface VideoDialogueTiming {
  speechUnitCount: number;
  hardMinimumSeconds: number;
  comfortableSeconds: number;
}

const SPEECH_UNIT_PATTERN = /[\u4e00-\u9fff]|[A-Za-z]+/g;

export function resolveVideoDialogueText(data: {
  dialogueText?: unknown;
  spokenDialogue?: unknown;
}): string {
  if (typeof data.dialogueText === "string" && data.dialogueText.trim()) {
    return data.dialogueText.trim();
  }
  if (!Array.isArray(data.spokenDialogue)) return "";
  return data.spokenDialogue
    .filter((line): line is string => typeof line === "string")
    .join(" ")
    .trim();
}

export function estimateVideoDialogueTiming(text: string): VideoDialogueTiming | null {
  const speechUnitCount = text.match(SPEECH_UNIT_PATTERN)?.length ?? 0;
  if (speechUnitCount === 0) return null;
  return {
    speechUnitCount,
    hardMinimumSeconds: Math.round((speechUnitCount / 6) * 10) / 10,
    comfortableSeconds: Math.round((speechUnitCount / 4) * 10) / 10,
  };
}
