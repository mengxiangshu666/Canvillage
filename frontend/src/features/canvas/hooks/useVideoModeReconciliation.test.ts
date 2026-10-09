import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  preferDeclaredMultiReferenceMode,
  reconcileDeclaredVideoMode,
  useVideoModeReconciliation,
} from "./useVideoModeReconciliation";

describe("reconcileDeclaredVideoMode", () => {
  it("keeps default full reference when authoritative metadata has no mode declaration", () => {
    const updateNodeData = vi.fn();
    renderHook(() => useVideoModeReconciliation({
      nodeId: 'video-1', storedMode: 'allReference', renderedMode: 'allReference',
      videoModelFamily: 'generic', mediaCounts: { images: 4, videos: 0, audios: 0 },
      typedCounts: { images: 4, videos: 0, audios: 0 }, isHappyHorseModel: false,
      catalogAuthoritative: true, updateNodeData,
    }));
    expect(updateNodeData).not.toHaveBeenCalled();
    expect(reconcileDeclaredVideoMode('imageToVideo', 'allReference', ['imageToVideo'], 'allReference')).toBe('imageToVideo');
  });
  it("keeps an endpoint-declared video-edit-only model out of a family fallback loop", () => {
    expect(
      reconcileDeclaredVideoMode("textToVideo", "videoEdit", ["videoEdit"]),
    ).toBe("videoEdit");
  });

  it("uses the first declared mode when neither stored nor family mode is legal", () => {
    expect(
      reconcileDeclaredVideoMode("allReference", "videoEdit", [
        "imageToVideo",
        "textToVideo",
      ]),
    ).toBe("imageToVideo");
  });

  it("does not revive a family mode after an explicit empty declaration", () => {
    expect(
      reconcileDeclaredVideoMode("allReference", "imageToVideo", [], "imageToVideo"),
    ).toBe("imageToVideo");
    expect(
      reconcileDeclaredVideoMode("allReference", "imageToVideo", []),
    ).toBe("imageToVideo");
  });

  it("keeps an explicitly selected upstream mode when the family is generic", () => {
    expect(
      reconcileDeclaredVideoMode(
        "imageToVideo",
        "imageToVideo",
        ["textToVideo", "allReference", "imageToVideo"],
        "allReference",
      ),
    ).toBe("allReference");
  });

  it("still auto-switches text mode when media is connected", () => {
    expect(
      reconcileDeclaredVideoMode(
        "imageToVideo",
        "imageToVideo",
        ["textToVideo", "allReference", "imageToVideo"],
        "textToVideo",
      ),
    ).toBe("imageToVideo");
  });

  it("upgrades legacy multi-image nodes to the declared all-reference mode", () => {
    expect(
      preferDeclaredMultiReferenceMode(
        "imageToVideo",
        2,
        ["textToVideo", "allReference", "imageToVideo"],
        9,
      ),
    ).toBe("allReference");
    expect(
      preferDeclaredMultiReferenceMode(
        "imageToVideo",
        2,
        ["textToVideo", "allReference", "imageToVideo"],
        1,
      ),
    ).toBeNull();
    expect(
      preferDeclaredMultiReferenceMode(
        "imageReference",
        2,
        ["allReference", "imageReference"],
        9,
      ),
    ).toBeNull();
  });

  it("writes the declared multi-reference mode instead of restoring the legacy mode", () => {
    const updateNodeData = vi.fn();
    renderHook(() =>
      useVideoModeReconciliation({
        nodeId: "video-1",
        storedMode: "imageToVideo",
        renderedMode: "imageToVideo",
        videoModelFamily: "direct",
        mediaCounts: { images: 3, videos: 0, audios: 0 },
        typedCounts: { images: 3, videos: 0, audios: 0 },
        isHappyHorseModel: false,
        supportedModes: ["textToVideo", "imageToVideo", "allReference"],
        referenceLimits: {
          imageToVideo: { image: 2, video: 0, audio: 0 },
          allReference: { image: 9, video: 3, audio: 3 },
        },
        updateNodeData,
      }),
    );

    expect(updateNodeData).toHaveBeenCalledWith("video-1", {
      genMode: "allReference",
    });
  });

  // 视频目录还没到时模型族退化成 generic、supportedModes 变成 undefined，此时
  // 这条 hook 原本会把已存的 allReference 改写成 imageToVideo 并落盘；目录回来后
  // storedMode 已经是 imageToVideo，用户选的模式再也回不来。调用方必须能关掉写入。
  it("does not rewrite the stored mode while the catalog is not authoritative", () => {
    const updateNodeData = vi.fn();
    renderHook(() =>
      useVideoModeReconciliation({
        nodeId: "video-1",
        storedMode: "allReference",
        renderedMode: "imageToVideo",
        videoModelFamily: "generic",
        mediaCounts: { images: 3, videos: 0, audios: 0 },
        typedCounts: { images: 3, videos: 0, audios: 0 },
        isHappyHorseModel: false,
        supportedModes: undefined,
        referenceLimits: null,
        catalogAuthoritative: false,
        updateNodeData,
      }),
    );

    expect(updateNodeData).not.toHaveBeenCalled();
  });

  it("applies the same rewrite once the catalog becomes authoritative", () => {
    const updateNodeData = vi.fn();
    renderHook(() =>
      useVideoModeReconciliation({
        nodeId: "video-1",
        storedMode: "allReference",
        renderedMode: "imageToVideo",
        videoModelFamily: "generic",
        mediaCounts: { images: 3, videos: 0, audios: 0 },
        typedCounts: { images: 3, videos: 0, audios: 0 },
        isHappyHorseModel: false,
        supportedModes: undefined,
        referenceLimits: null,
        catalogAuthoritative: true,
        updateNodeData,
      }),
    );

    expect(updateNodeData).toHaveBeenCalledWith("video-1", {
      genMode: "imageToVideo",
    });
  });
});
