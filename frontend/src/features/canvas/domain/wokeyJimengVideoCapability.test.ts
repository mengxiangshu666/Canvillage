import { describe, expect, it } from "vitest";

import {
  compileVideoModelFamily,
  isVideoModeSupported,
  videoEmptyStateCtaModes,
} from "./videoCapabilityCompiler";
import { VIDEO_MODELS } from "../ui/ProviderModelPicker";

describe("Wokey Jimeng video capability", () => {
  it("exposes the verified multimodal-reference path", () => {
    expect(compileVideoModelFamily("newapi_jimeng-seedance-2.0-fast")).toBe("wokey-jimeng");
    expect(isVideoModeSupported("wokey-jimeng", "textToVideo")).toBe(true);
    expect(isVideoModeSupported("wokey-jimeng", "imageToVideo")).toBe(true);
    expect(isVideoModeSupported("wokey-jimeng", "allReference")).toBe(true);
    expect(isVideoModeSupported("wokey-jimeng", "firstLastFrame")).toBe(true);
    expect(videoEmptyStateCtaModes("wokey-jimeng")).toEqual([
      "allReference",
      "imageReference",
      "firstLastFrame",
    ]);
  });

  it("never resurrects stale NewAPI video options when the live catalog is unavailable", () => {
    expect(VIDEO_MODELS).toEqual([]);
  });
});
