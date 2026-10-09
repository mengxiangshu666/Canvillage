import { describe, expect, it } from "vitest";

import {
  compileVideoModelFamily,
  deriveVideoMode,
  isVideoModeSupported,
  resolveVideoModelFamily,
  videoSubmitMediaRejectionReason,
} from "./videoCapabilityCompiler";
import { VIDEO_MODELS } from "../ui/ProviderModelPicker";

describe("Kacang video capability", () => {
  it("maps each selected model to its actual function", () => {
    expect(compileVideoModelFamily("newapi_s-videos-f-933-fast-480-2")).toBe("kacang-933");
    expect(compileVideoModelFamily("newapi_mini-h3")).toBe("kacang-mini-h3");
    expect(compileVideoModelFamily("newapi_kling-v3-omni-v2v-create")).toBe("kacang-kling-v2v");
    expect(compileVideoModelFamily("kling-v3-omni-v2v-create")).toBe("kacang-kling-v2v");
    expect(resolveVideoModelFamily("kling-v3-omni-v2v-create", "direct")).toBe("kacang-kling-v2v");
    expect(isVideoModeSupported("kacang-mini-h3", "textToVideo")).toBe(true);
    expect(isVideoModeSupported("kacang-mini-h3", "imageToVideo")).toBe(false);
    expect(deriveVideoMode("kacang-kling-v2v", { images: 0, videos: 0, audios: 0 }, "textToVideo")).toBe("videoEdit");
    expect(deriveVideoMode("kacang-kling-v2v", { images: 0, videos: 1, audios: 0 }, "allReference")).toBe("videoEdit");
    expect(videoSubmitMediaRejectionReason("videoEdit", "kacang-kling-v2v", {
      images: 0,
      videos: 1,
      audios: 0,
    })).toBeNull();
  });

  it("requires a source video for Kling V3 Omni and keeps video catalog dynamic", () => {
    expect(videoSubmitMediaRejectionReason("videoEdit", "kacang-kling-v2v", { images: 0, videos: 0, audios: 0 }))
      .toContain("源视频");
    expect(VIDEO_MODELS).toEqual([]);
  });
});
