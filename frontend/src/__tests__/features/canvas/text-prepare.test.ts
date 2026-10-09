import { describe, expect, it } from "vitest";

import {
  buildTextPrepareApplyPatch,
  buildTextPreparePreview,
  isTextPreparePreviewStale,
} from "@/features/canvas/nodes/text/textPrepare";

function makePreview() {
  return buildTextPreparePreview({
    result: {
      prepared_text: "场景 1\n旧车站的雨声压低了两个人的脚步。",
      source_text: "旧车站里，一封信让两个人重新见面。",
      source_hash: "a".repeat(64),
      mode: "faithful",
      model: "direct/text-primary",
      change_summary: ["补全了场景动作"],
      unresolved: [],
      warnings: [],
    },
    jobId: "job-1",
    taskKey: "task:freezone_text_prepare:demo:0:job-1",
    createdAt: 1,
  });
}

describe("text prepare preview", () => {
  it("only becomes stale after the source text changes", () => {
    const preview = makePreview();

    expect(isTextPreparePreviewStale(preview, preview.sourceText)).toBe(false);
    expect(isTextPreparePreviewStale(preview, `${preview.sourceText} 补充`)).toBe(true);
  });

  it("builds an apply patch without changing the source before confirmation", () => {
    const preview = makePreview();

    expect(buildTextPrepareApplyPatch(preview, `${preview.sourceText} 补充`)).toBeNull();
    expect(buildTextPrepareApplyPatch(preview, preview.sourceText)).toEqual({
      content: preview.preparedText,
      textPreparePreview: null,
      textPrepareError: null,
    });
  });
});
