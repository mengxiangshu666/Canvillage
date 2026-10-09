// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";
import {
  estimateVideoDialogueTiming,
  resolveVideoDialogueText,
} from "@/features/canvas/nodes/videoDialogueTiming";

describe("video dialogue timing estimate", () => {
  it("estimates hard and comfortable speaking time from the full dialogue", () => {
    const timing = estimateVideoDialogueTiming("甲".repeat(52));
    expect(timing).toEqual({
      speechUnitCount: 52,
      hardMinimumSeconds: 8.7,
      comfortableSeconds: 13,
    });
  });

  it("prefers full dialogue over its split representation", () => {
    expect(
      resolveVideoDialogueText({
        dialogueText: "  你好，世界  ",
        spokenDialogue: ["你好", "世界"],
      }),
    ).toBe("你好，世界");
    expect(
      resolveVideoDialogueText({ spokenDialogue: ["第一句", "第二句"] }),
    ).toBe("第一句 第二句");
  });

  it("returns no estimate for empty dialogue and ignores whitespace", () => {
    expect(estimateVideoDialogueTiming(" \n\t ")).toBeNull();
    expect(estimateVideoDialogueTiming("甲 乙")).toEqual({
      speechUnitCount: 2,
      hardMinimumSeconds: 0.3,
      comfortableSeconds: 0.5,
    });
  });

  it("ignores punctuation and counts Latin words like the server contract", () => {
    expect(estimateVideoDialogueTiming("你好，world! 2026")).toEqual({
      speechUnitCount: 3,
      hardMinimumSeconds: 0.5,
      comfortableSeconds: 0.8,
    });
  });
});
