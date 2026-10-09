import { describe, expect, it } from "vitest";

import { seedance2ResolutionOptionsForBackend } from "@/components/episode/beat-workbench/video-pane";

describe("video pane resolution capabilities", () => {
  it("keeps Firefly Seedance2 Fast 480P quotes and controls at 480p", () => {
    expect(
      seedance2ResolutionOptionsForBackend("newapi_firefly-seedance2-fast-480p"),
    ).toEqual(["480p"]);
  });

  it("keeps the dedicated Firefly 720P route at 720p", () => {
    expect(
      seedance2ResolutionOptionsForBackend("newapi_firefly-seedance2-fast-720p"),
    ).toEqual(["720p"]);
  });

  it("does not treat an unannotated Firefly backend as a generic 720p route", () => {
    expect(seedance2ResolutionOptionsForBackend("newapi_firefly-seedance2-fast-480p")[0]).toBe(
      "480p",
    );
  });

  it("prefers runtime-declared options over the legacy model-name fallback", () => {
    expect(
      seedance2ResolutionOptionsForBackend("direct_custom", {
        resolution_options: ["1440p", "2048x1152"],
      } as never),
    ).toEqual(["1440p", "2048x1152"]);
  });
});
