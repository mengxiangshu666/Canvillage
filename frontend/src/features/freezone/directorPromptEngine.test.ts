import { describe, expect, it } from "vitest";

import {
  compileDirectorPrompt,
  DEFAULT_DIRECTOR_CONTROLS,
  DIRECTOR_PROMPT_REVISION,
  DIRECTOR_STYLES,
  directorSourcePromptFromNodeData,
} from "./directorPromptEngine";

describe("directorPromptEngine", () => {
  it("compiles all eight style layers with identity and continuity locks", () => {
    const result = compileDirectorPrompt({
      basePrompt: "A woman turns toward camera.",
      modelId: "huimeng/gpt-image-2",
      controls: DEFAULT_DIRECTOR_CONTROLS,
      referenceDescription: "the attached character sheet",
    });

    expect(result.revision).toBe(DIRECTOR_PROMPT_REVISION);
    expect(Object.keys(result.layers)).toEqual([
      "format",
      "medium",
      "linework",
      "shading",
      "palette",
      "texture",
      "lighting",
      "composition",
    ]);
    expect(result.prompt.indexOf("REFERENCE PRIORITY")).toBeLessThan(
      result.prompt.indexOf("A woman turns"),
    );
    expect(result.prompt).toContain("IDENTITY LOCK");
    expect(result.prompt).toContain("CONTINUITY LOCK");
    expect(result.prompt).toContain("AVOID:");
  });

  it("uses a model-specific profile and Midjourney negative syntax", () => {
    const result = compileDirectorPrompt({
      basePrompt: "lonely figure in a corridor",
      modelId: "midjourney-v8.1",
      controls: { ...DEFAULT_DIRECTOR_CONTROLS, styleId: "film-noir" },
    });

    expect(result.profile).toBe("midjourney-style");
    expect(result.prompt).toContain("--no");
    expect(result.negative).toContain("flat front light");
  });

  it("preserves long prompts without an authoring word limit", () => {
    const longPrompt = Array.from({ length: 500 }, (_, index) => `镜头细节${index}`).join(" ");
    const result = compileDirectorPrompt({
      basePrompt: longPrompt,
      modelId: "gpt-image-2",
      controls: DEFAULT_DIRECTOR_CONTROLS,
    });

    expect(result.prompt).toContain(longPrompt);
    expect(result.warnings.some((warning) => warning.includes("180"))).toBe(false);
  });

  it("detects a contradictory realism and anime request", () => {
    const result = compileDirectorPrompt({
      basePrompt: "anime cel shading portrait",
      modelId: "gpt-image-2",
      controls: { ...DEFAULT_DIRECTOR_CONTROLS, styleId: "cinematic-realism" },
    });

    expect(result.warnings.some((warning) => warning.includes("冲突"))).toBe(true);
  });

  it("ships a useful local style palette rather than one hard-coded look", () => {
    expect(DIRECTOR_STYLES.length).toBeGreaterThanOrEqual(10);
    expect(new Set(DIRECTOR_STYLES.map((style) => style.category)).size).toBeGreaterThanOrEqual(6);
  });

  it("keeps repeated Style DNA application idempotent while accepting later user edits", () => {
    const source = "A woman turns toward camera.";
    const first = compileDirectorPrompt({
      basePrompt: source,
      modelId: "gpt-image-2",
      controls: DEFAULT_DIRECTOR_CONTROLS,
    });
    const restoredSource = directorSourcePromptFromNodeData({
      prompt: first.prompt,
      compiledPromptPreview: first.prompt,
      director_source_prompt: source,
    });
    const second = compileDirectorPrompt({
      basePrompt: restoredSource,
      modelId: "gpt-image-2",
      controls: DEFAULT_DIRECTOR_CONTROLS,
    });

    expect(second.prompt).toBe(first.prompt);
    expect(directorSourcePromptFromNodeData({
      prompt: "A later user edit",
      compiledPromptPreview: first.prompt,
      director_source_prompt: source,
    })).toBe("A later user edit");
  });
});
