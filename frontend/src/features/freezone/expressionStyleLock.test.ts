// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  affectPrompt,
  compileExpressionStyleLockedPrompt,
  EXPRESSION_PREVIEW_RENDERER,
  expressionModelResolutionOptions,
  pickExpressionResolution,
} from "./ExpressionManagerPanel";
import { MOOD_GRID } from "./emotionMoodGrid";

describe("compileExpressionStyleLockedPrompt", () => {
  it("passes through a short expression edit brief without director art styles", () => {
    const base = "Change only 人物 1's facial expression in Image 1 to 决绝震怒 (clear).";
    const prompt = compileExpressionStyleLockedPrompt(base);
    expect(prompt).toBe(base);
    expect(prompt).not.toMatch(/pixel-faithfully/);
    expect(prompt).not.toMatch(/Chinese ink wash|watercolor|anime|cinematic photorealism/);
    expect(prompt).not.toMatch(/REFERENCE PRIORITY|STYLE LOCK|IDENTITY LOCK/);
  });

  it("uses one ICT 57-Morph head for every semantic state", () => {
    expect(EXPRESSION_PREVIEW_RENDERER).toBe("ict-facekit-57-morph.single-head");
  });

  it("builds a short change+preserve expression edit prompt", () => {
    const anchor = MOOD_GRID.find((item) => item.key === "micro-suspicion");
    expect(anchor).toBeDefined();
    const prompt = affectPrompt(
      "identity source",
      { id: "person-1", label: "人物 1", x: 0.1, y: 0.2, width: 0.3, height: 0.4 },
      0,
      0,
      anchor!.label,
      anchor!.expressionPrompt,
      "neutral",
      "none",
      1,
      0,
      "brows asymmetric; micro squint",
    );
    expect(prompt).toContain(anchor!.expressionPrompt);
    expect(prompt).toContain("Change only");
    expect(prompt).toContain("Image 2");
    expect(prompt).toContain("Keep the same person");
    expect(prompt.length).toBeLessThan(700);
    expect(prompt).not.toMatch(/SELECTED SEMANTIC ANCHOR|FACE EDIT REQUIRED|IDENTITY LOCK/);
    expect(prompt).not.toMatch(/Eyebrows:|EXACT FACIAL RIG BLUEPRINT/);
  });
});

describe("expression model capability contract", () => {
  const editableModel = {
    id: "direct/editable",
    providerId: "direct" as const,
    apiModel: "direct/editable",
    label: "Editable",
    supportedModes: ["imageToImage"],
    resolutionOptions: ["768p", "1536p"],
    parameterDefaults: { resolution: "1536p" },
    capabilitySource: "runtime" as const,
  };

  it("shows only resolutions declared by the image-edit model", () => {
    expect(expressionModelResolutionOptions(editableModel)).toEqual(["768p", "1536p"]);
    expect(pickExpressionResolution("2K", editableModel)).toBe("1536p");
    expect(pickExpressionResolution("768p", editableModel)).toBe("768p");
  });

  it("does not advertise output sizes for a text-only model", () => {
    expect(expressionModelResolutionOptions({
      ...editableModel,
      supportedModes: ["textToImage"],
    })).toEqual([]);
  });
});
