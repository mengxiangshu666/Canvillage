// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";
import {
  audioReferenceDurationRejection,
  compileVideoModelFamily,
  deriveVideoMode,
  FIREFLY_SEEDANCE2_TOTAL_REFERENCE_LIMIT,
  formatAudioDurationClips,
  isFireflySeedance2VideoModel,
  isGrokVideoChannelModel,
  isHappyHorseVideoModel,
  isKling30VideoModel,
  isPromptHubsSdVideoModel,
  isSeedance1xVideoModel,
  isSeedance2VideoFamily,
  isSeedance2VideoModel,
  isVideoModeSupported,
  isVideoModeSupportedForModel,
  resolveVideoModeForCapability,
  normalizeVideoAudioPreference,
  resolveVideoNodeAudioSwitch,
  reconcileVideoModeForNode,
  resolveVideoModelFamily,
  recoverVideoModeForFamily,
  videoEmptyStateCtaModes,
  videoModeRequiresPrompt,
  canSubmitVideoGeneration,
  videoReferenceDisabledReason,
  videoSubmitMediaRejectionReason,
  videoSubmitDisabledReason,
  videoPromptContractIssue,
  videoUpstreamImageDefaultMode,
  VIDEO_REFERENCE_CAPS,
} from "./videoCapabilityCompiler";

describe("videoCapabilityCompiler", () => {
  it("treats selected duration as authoritative over prose duration mentions", () => {
    expect(
      videoPromptContractIssue("镜头持续 12 秒，人物向前走", {
        images: 0,
        videos: 0,
        audios: 0,
      }),
    ).toBeNull();
  });

  it("blocks explicit image mentions without connected images", () => {
    expect(
      videoPromptContractIssue("保持 @图片1 的角色外观", {
        images: 0,
        videos: 0,
        audios: 0,
      }),
    ).toContain("没有连接");
    expect(
      videoPromptContractIssue("保持 @图片1 的角色外观", {
        images: 1,
        videos: 0,
        audios: 0,
      }),
    ).toBeNull();
  });

  it("treats the single canvas switch as authoritative across model capabilities", () => {
    expect(normalizeVideoAudioPreference(undefined, "optional")).toBe(true);
    expect(normalizeVideoAudioPreference(undefined, "unsupported")).toBe(true);
    expect(normalizeVideoAudioPreference(false, "unsupported")).toBe(true);
    expect(normalizeVideoAudioPreference(false, "unsupported", true)).toBe(false);
    expect(normalizeVideoAudioPreference(false, "required")).toBe(true);
    expect(normalizeVideoAudioPreference(false, "required", true)).toBe(false);
    expect(normalizeVideoAudioPreference(true, "required", true)).toBe(true);
    expect(normalizeVideoAudioPreference(true, "unsupported", true)).toBe(true);
    expect(normalizeVideoAudioPreference(false, "optional", true)).toBe(false);
    expect(normalizeVideoAudioPreference(true, "optional", true)).toBe(true);
  });

  /**
   * 脚本派生的原生声音路由（T-152 的单镜音频合同）也是权威来源。
   *
   * 实测事故：脚本行写了音效、派生出的节点带着 `nativeAudioStrategy='native'`，但
   * 开关值被当成「用户没拨过」压成 false，提交时 `generate_audio_explicit=false`
   * 让后端判定「用户明确要静音」，provider 出回来的音轨在本地成品里被判成多余剥掉 ——
   * 有音效设计的镜头拿到的是静音。
   */
  it("keeps a script-derived native-audio route switched on", () => {
    // 未触碰开关的视频节点默认生成原生音频。
    expect(resolveVideoNodeAudioSwitch({ generateAudio: false })).toBe(true);
    // 脚本派生：音效列有设计、无台词 → 开关打开。
    expect(
      resolveVideoNodeAudioSwitch({ generateAudio: true, nativeAudioStrategy: "native" }),
    ).toBe(true);
    // 用户拨过就听用户的，派生路由不能把它翻回来。
    expect(
      resolveVideoNodeAudioSwitch({
        generateAudio: false,
        generateAudioUserSet: true,
        nativeAudioStrategy: "native",
      }),
    ).toBe(false);
    expect(
      resolveVideoNodeAudioSwitch({
        generateAudio: true,
        generateAudioUserSet: true,
        nativeAudioStrategy: "external",
      }),
    ).toBe(true);
    // 旧节点自动外部配音标记不能覆盖新的声音默认值。
    expect(
      resolveVideoNodeAudioSwitch({ generateAudio: false, nativeAudioStrategy: "external" }),
    ).toBe(true);
  });

  it("recognizes aliases without scattering provider string checks", () => {
    expect(compileVideoModelFamily("newapi_happyhorse-1.0")).toBe("happyhorse");
    expect(compileVideoModelFamily("grok-video-channel")).toBe("grok-video-channel");
    expect(compileVideoModelFamily("seedance-1.5-pro")).toBe("seedance-1x");
    expect(compileVideoModelFamily("newapi_seedance-2.0-fast")).toBe("seedance-2");
    expect(compileVideoModelFamily("huimeng_seedance-2.0-fast-value")).toBe("seedance-2-value");
    expect(compileVideoModelFamily("newapi_firefly-seedance2-fast-480p")).toBe("firefly-seedance2");
    expect(compileVideoModelFamily("newapi_sd2.0-720p-fast")).toBe("prompt-hubs-sd");
    expect(compileVideoModelFamily("sd-2.0-fast-v1")).toBe("prompt-hubs-sd");
    expect(compileVideoModelFamily("newapi_kling-3.0-omni")).toBe("kling-3.0");
    expect(isHappyHorseVideoModel("newapi_happyhorse-1.0")).toBe(true);
    expect(isFireflySeedance2VideoModel("newapi_firefly-seedance2-fast-480p")).toBe(true);
    expect(isGrokVideoChannelModel("newapi_grok-video-channel")).toBe(true);
    expect(isPromptHubsSdVideoModel("newapi_sd2.0-720p-fast")).toBe(true);
    expect(isPromptHubsSdVideoModel("sd-2.0-fast-v1")).toBe(true);
    expect(isKling30VideoModel("newapi_kling-3.0-omni-ref")).toBe(true);
    expect(isSeedance1xVideoModel("huimeng_seedance-1.0-pro-fast")).toBe(true);
    expect(isSeedance1xVideoModel("newapi_seedance-2.0")).toBe(false);
    expect(isSeedance2VideoModel("huimeng_seedance20_fast")).toBe(true);
    expect(isSeedance2VideoFamily("seedance-2-value")).toBe(true);
  });

  it("prefers model-id facts over a stale generic backend family", () => {
    expect(resolveVideoModelFamily("newapi_seedance-2.0", "generic")).toBe(
      "seedance-2",
    );
    expect(resolveVideoModelFamily("newapi_seedance-1.5-pro", "generic")).toBe(
      "seedance-1x",
    );
    expect(resolveVideoModelFamily("custom-model", "happyhorse")).toBe(
      "happyhorse",
    );
  });

  it("derives all four HappyHorse modes from typed upstream inputs", () => {
    expect(deriveVideoMode("happyhorse", { images: 0, videos: 0, audios: 0 }, "imageToVideo")).toBe("textToVideo");
    expect(deriveVideoMode("happyhorse", { images: 1, videos: 0, audios: 0 }, "textToVideo")).toBe("imageToVideo");
    expect(deriveVideoMode("happyhorse", { images: 2, videos: 0, audios: 0 }, "textToVideo")).toBe("imageReference");
    expect(deriveVideoMode("happyhorse", { images: 1, videos: 1, audios: 0 }, "imageReference")).toBe("videoEdit");
  });

  it("recovers from a stale v2v-only videoEdit mode after switching models", () => {
    expect(
      recoverVideoModeForFamily(
        "wokey-jimeng",
        { images: 1, videos: 0, audios: 0 },
        "videoEdit",
      ),
    ).toBe("allReference");
    expect(
      recoverVideoModeForFamily(
        "kacang-933",
        { images: 1, videos: 0, audios: 0 },
        "videoEdit",
      ),
    ).toBe("allReference");
    expect(
      recoverVideoModeForFamily(
        "wokey-jimeng",
        { images: 0, videos: 0, audios: 0 },
        "videoEdit",
      ),
    ).toBe("textToVideo");
    expect(
      recoverVideoModeForFamily(
        "kacang-kling-v2v",
        { images: 0, videos: 0, audios: 0 },
        "textToVideo",
      ),
    ).toBe("videoEdit");
    expect(
      recoverVideoModeForFamily(
        "kacang-mini-h3",
        { images: 0, videos: 1, audios: 0 },
        "videoEdit",
      ),
    ).toBe("textToVideo");
  });

  it("reconciles video modes with one deterministic writer to prevent React update loops", () => {
    const empty = { images: 0, videos: 0, audios: 0 };

    expect(
      reconcileVideoModeForNode("kacang-kling-v2v", empty, "textToVideo"),
    ).toBe("videoEdit");
    expect(
      reconcileVideoModeForNode("kacang-kling-v2v", empty, "videoEdit"),
    ).toBe("videoEdit");
    expect(
      reconcileVideoModeForNode(
        "kacang-kling-v2v",
        { images: 0, videos: 1, audios: 0 },
        "allReference",
      ),
    ).toBe("videoEdit");
    expect(reconcileVideoModeForNode("generic", empty, "videoEdit")).toBe(
      "textToVideo",
    );
    expect(
      reconcileVideoModeForNode(
        "wokey-jimeng",
        { images: 0, videos: 1, audios: 0 },
        "textToVideo",
      ),
    ).toBe("allReference");
    expect(
      reconcileVideoModeForNode(
        "happyhorse",
        { images: 2, videos: 0, audios: 0 },
        "textToVideo",
      ),
    ).toBe("imageReference");
  });

  it("publishes mode and reference contracts", () => {
    expect(isVideoModeSupported("happyhorse", "videoEdit")).toBe(true);
    expect(isVideoModeSupported("happyhorse", "firstLastFrame")).toBe(false);
    expect(isVideoModeSupported("generic", "videoEdit")).toBe(false);
    expect(isVideoModeSupported("seedance-1x", "allReference")).toBe(false);
    expect(isVideoModeSupported("seedance-1x", "firstLastFrame")).toBe(false);
    expect(isVideoModeSupported("seedance-1x", "imageToVideo")).toBe(true);
    expect(isVideoModeSupported("seedance-2", "allReference")).toBe(true);
    expect(isVideoModeSupported("seedance-2-value", "firstLastFrame")).toBe(true);
    expect(isVideoModeSupported("firefly-seedance2", "allReference")).toBe(true);
    expect(isVideoModeSupported("firefly-seedance2", "imageReference")).toBe(true);
    expect(isVideoModeSupported("firefly-seedance2", "firstLastFrame")).toBe(false);
    expect(isVideoModeSupported("firefly-seedance2", "videoEdit")).toBe(false);
    expect(isVideoModeSupported("prompt-hubs-sd", "allReference")).toBe(true);
    expect(isVideoModeSupported("prompt-hubs-sd", "imageReference")).toBe(true);
    expect(isVideoModeSupported("kling-3.0", "allReference")).toBe(false);
    expect(isVideoModeSupported("grok-video-channel", "allReference")).toBe(false);
    expect(VIDEO_REFERENCE_CAPS.videoEdit).toEqual({ image: 5, video: 1, audio: 0 });
  });

  it("derives model-specific empty CTAs and image defaults", () => {
    expect(videoEmptyStateCtaModes("happyhorse")).toEqual([
      "imageToVideo",
      "imageReference",
    ]);
    expect(videoEmptyStateCtaModes("seedance-2")).toEqual([
      "allReference",
      "imageReference",
      "firstLastFrame",
    ]);
    expect(videoEmptyStateCtaModes("firefly-seedance2")).toEqual([
      "allReference",
      "imageReference",
    ]);
    expect(videoEmptyStateCtaModes("prompt-hubs-sd")).toEqual([
      "allReference",
      "imageReference",
    ]);
    expect(videoEmptyStateCtaModes("kling-3.0")).toEqual([
      "imageToVideo",
      "imageReference",
    ]);
    expect(videoEmptyStateCtaModes("seedance-1x")).toEqual(["imageToVideo"]);
    expect(videoEmptyStateCtaModes("grok-video-channel")).toEqual([
      "imageToVideo",
    ]);
    expect(videoUpstreamImageDefaultMode("seedance-2-value")).toBe(
      "allReference",
    );
    expect(videoUpstreamImageDefaultMode("firefly-seedance2")).toBe(
      "allReference",
    );
    expect(videoUpstreamImageDefaultMode("prompt-hubs-sd")).toBe(
      "allReference",
    );
    expect(videoUpstreamImageDefaultMode("seedance-1x")).toBe("imageToVideo");
  });

  it("keeps every empty-state CTA inside the selected family capability set", () => {
    for (const family of [
      "happyhorse",
      "firefly-seedance2",
      "prompt-hubs-sd",
      "kling-3.0",
      "grok-video-channel",
      "seedance-1x",
      "seedance-2",
      "seedance-2-value",
      "generic",
    ] as const) {
      for (const mode of videoEmptyStateCtaModes(family)) {
        expect(isVideoModeSupported(family, mode)).toBe(true);
      }
    }
  });

  it("requires prompts only on text and omni routes", () => {
    expect(videoModeRequiresPrompt("textToVideo")).toBe(true);
    expect(videoModeRequiresPrompt("allReference")).toBe(true);
    expect(videoModeRequiresPrompt("imageToVideo")).toBe(false);
    expect(videoModeRequiresPrompt("imageReference")).toBe(false);
    expect(videoModeRequiresPrompt("firstLastFrame")).toBe(false);
    expect(videoModeRequiresPrompt("videoEdit")).toBe(false);
  });

  it("treats omitted and explicitly empty mode capabilities differently", () => {
    expect(isVideoModeSupportedForModel("generic", "allReference")).toBe(true);
    expect(isVideoModeSupportedForModel("direct", "allReference", {})).toBe(true);
    expect(resolveVideoModeForCapability("allReference", "generic", {})).toBe("allReference");
    expect(isVideoModeSupportedForModel("generic", "allReference", { supportedModes: [] })).toBe(false);
    expect(videoSubmitMediaRejectionReason("allReference", "generic", { images: 4, videos: 0, audios: 0 }, {})).toBeNull();
    expect(isVideoModeSupportedForModel("seedance-2", "allReference")).toBe(true);
    expect(isVideoModeSupportedForModel("seedance-2", "allReference", {
      supportedModes: [],
    })).toBe(false);
    expect(isVideoModeSupportedForModel("seedance-2", "allReference", {
      supportedModes: ["textToVideo"],
    })).toBe(false);
    expect(resolveVideoModeForCapability("allReference", "seedance-2", {
      supportedModes: [],
    })).toBe("allReference");
    expect(resolveVideoModeForCapability("allReference", "seedance-2", {
      supportedModes: ["imageToVideo"],
    })).toBe("imageToVideo");
  });

  it("allows omni reference submission from connected media without typed prompt", () => {
    expect(
      canSubmitVideoGeneration({
        mode: "allReference",
        hasPromptText: false,
        referenceCounts: { images: 1, videos: 0, audios: 0 },
      }),
    ).toBe(true);
    expect(
      canSubmitVideoGeneration({
        mode: "allReference",
        hasPromptText: false,
        referenceCounts: { images: 0, videos: 1, audios: 0 },
      }),
    ).toBe(true);
    expect(
      canSubmitVideoGeneration({
        mode: "allReference",
        hasPromptText: false,
        referenceCounts: { images: 0, videos: 0, audios: 0 },
      }),
    ).toBe(false);
    expect(
      canSubmitVideoGeneration({
        mode: "textToVideo",
        hasPromptText: false,
        referenceCounts: { images: 1, videos: 0, audios: 0 },
      }),
    ).toBe(false);
  });

  it("explains why a video retry is unavailable", () => {
    expect(videoSubmitDisabledReason({
      mode: "imageToVideo",
      hasPromptText: true,
      referenceCounts: { images: 3, videos: 0, audios: 0 },
      mediaRejectionReason: "当前模型的当前模式最多支持 1 张图片",
    })).toBe("当前模型的当前模式最多支持 1 张图片");
    expect(videoSubmitDisabledReason({
      mode: "textToVideo",
      hasPromptText: false,
      referenceCounts: { images: 0, videos: 0, audios: 0 },
    })).toBe("请先填写提示词");
    expect(videoSubmitDisabledReason({
      mode: "videoEdit",
      hasPromptText: false,
      referenceCounts: { images: 0, videos: 0, audios: 0 },
    })).toBe("请先连接一个源视频");
  });

  it("blocks media that the selected model and mode cannot consume", () => {
    expect(
      videoSubmitMediaRejectionReason("allReference", "seedance-1x", {
        images: 1,
        videos: 0,
        audios: 0,
      }),
    ).toContain("不支持该生成模式");
    expect(
      videoSubmitMediaRejectionReason("imageToVideo", "seedance-1x", {
        images: 1,
        videos: 1,
        audios: 0,
      }),
    ).toContain("不支持视频素材");
    expect(
      videoSubmitMediaRejectionReason("imageToVideo", "seedance-1x", {
        images: 2,
        videos: 0,
        audios: 0,
      }),
    ).toContain("仅支持 1 张图片");
    expect(
      videoSubmitMediaRejectionReason("allReference", "seedance-2", {
        images: 3,
        videos: 2,
        audios: 1,
      }),
    ).toBeNull();
    expect(
      videoSubmitMediaRejectionReason("allReference", "firefly-seedance2", {
        images: 9,
        videos: 2,
        audios: 1,
      }),
    ).toBeNull();
    expect(
      videoSubmitMediaRejectionReason("allReference", "prompt-hubs-sd", {
        images: 9,
        videos: 3,
        audios: 3,
      }),
    ).toBeNull();
    expect(
      videoSubmitMediaRejectionReason("videoEdit", "happyhorse", {
        images: 0,
        videos: 1,
        audios: 0,
      }),
    ).toBeNull();
    expect(
      videoSubmitMediaRejectionReason("imageReference", "grok-video-channel", {
        images: 8,
        videos: 0,
        audios: 0,
      }),
    ).toBeNull();
  });

  it("prefers dynamic capabilities over family fallbacks at submit time", () => {
    const dynamicCapability = {
      supportedModes: ["allReference"],
      referenceLimits: {
        allReference: { image: 1, video: 0, audio: 0 },
      },
    };

    expect(
      videoSubmitMediaRejectionReason(
        "allReference",
        "direct",
        { images: 1, videos: 0, audios: 0 },
        dynamicCapability,
      ),
    ).toBeNull();
    expect(
      videoSubmitMediaRejectionReason(
        "textToVideo",
        "direct",
        { images: 0, videos: 0, audios: 0 },
        dynamicCapability,
      ),
    ).toContain("不支持该生成模式");
    expect(
      videoSubmitMediaRejectionReason(
        "allReference",
        "direct",
        { images: 2, videos: 0, audios: 0 },
        dynamicCapability,
      ),
    ).toContain("最多支持 1 张图片");
  });

  it("enforces every dynamic all-reference media limit", () => {
    const capability = {
      supportedModes: ["allReference"],
      referenceLimits: {
        allReference: { image: 0, video: 1, audio: 0 },
      },
    };

    expect(
      videoSubmitMediaRejectionReason(
        "allReference",
        "direct",
        { images: 0, videos: 1, audios: 0 },
        capability,
      ),
    ).toBeNull();
    expect(
      videoSubmitMediaRejectionReason(
        "allReference",
        "direct",
        { images: 1, videos: 0, audios: 0 },
        capability,
      ),
    ).toContain("不支持图片参考");
    expect(
      videoSubmitMediaRejectionReason(
        "allReference",
        "direct",
        { images: 0, videos: 2, audios: 0 },
        capability,
      ),
    ).toContain("最多支持 1 个视频");
    expect(
      videoSubmitMediaRejectionReason(
        "allReference",
        "direct",
        { images: 0, videos: 0, audios: 1 },
        capability,
      ),
    ).toContain("不支持音频参考");
  });

  it("treats omitted dynamic reference limits as unspecified", () => {
    expect(
      videoSubmitMediaRejectionReason(
        "allReference",
        "direct",
        { images: 1, videos: 1, audios: 1 },
        {
          supportedModes: ["allReference"],
          referenceLimits: { allReference: { image: 1 } },
        },
      ),
    ).toBeNull();
  });

  it("enforces Firefly Seedance2 per-kind and total reference ceilings before submit", () => {
    expect(FIREFLY_SEEDANCE2_TOTAL_REFERENCE_LIMIT).toBe(12);
    expect(
      videoSubmitMediaRejectionReason("allReference", "firefly-seedance2", {
        images: 10,
        videos: 0,
        audios: 0,
      }),
    ).toContain("9 张图片");
    expect(
      videoSubmitMediaRejectionReason("allReference", "firefly-seedance2", {
        images: 9,
        videos: 4,
        audios: 0,
      }),
    ).toContain("3 个视频");
    expect(
      videoSubmitMediaRejectionReason("allReference", "firefly-seedance2", {
        images: 9,
        videos: 3,
        audios: 1,
      }),
    ).toContain("12 个参考素材");
    expect(
      videoSubmitMediaRejectionReason("imageReference", "firefly-seedance2", {
        images: 10,
        videos: 0,
        audios: 0,
      }),
    ).toContain("9 张图片");
  });

  it("returns actionable provider-specific disabled reasons", () => {
    expect(videoReferenceDisabledReason("grok-video-channel", { images: 1, videos: 1, audios: 0 })).toContain("仅支持图片");
    expect(videoReferenceDisabledReason("seedance-1x", { images: 1, videos: 0, audios: 0 })).toBeNull();
    expect(videoReferenceDisabledReason("seedance-1x", { images: 2, videos: 0, audios: 0 })).toContain("仅支持 1 张图片");
    expect(videoReferenceDisabledReason("seedance-1x", { images: 0, videos: 0, audios: 1 })).toContain("不支持音频素材");
    expect(videoReferenceDisabledReason("seedance-2", { images: 9, videos: 3, audios: 3 })).toBeNull();
  });

  it("accepts the exact Seedance audio duration boundaries", () => {
    expect(
      audioReferenceDurationRejection([
        { label: "minimum.wav", durationMs: 1_800 },
        { label: "maximum.wav", durationMs: 15_200 },
      ]),
    ).toBeNull();
  });

  it("rejects each clip below 1.8 seconds or above 15.2 seconds", () => {
    expect(
      audioReferenceDurationRejection([
        { label: "short.wav", durationMs: 1_799 },
      ]),
    ).toEqual({
      kind: "tooShort",
      clips: [{ label: "short.wav", durationMs: 1_799 }],
    });
    expect(
      audioReferenceDurationRejection([
        { label: "long.wav", durationMs: 15_201 },
      ]),
    ).toEqual({
      kind: "tooLong",
      clips: [{ label: "long.wav", durationMs: 15_201 }],
    });
  });

  it("reports all clips in the same rejection class and prioritizes too-short clips", () => {
    expect(
      audioReferenceDurationRejection([
        { label: "short-a.wav", durationMs: 900 },
        { label: "valid.wav", durationMs: 6_000 },
        { label: "short-b.wav", durationMs: 1_000 },
      ]),
    ).toEqual({
      kind: "tooShort",
      clips: [
        { label: "short-a.wav", durationMs: 900 },
        { label: "short-b.wav", durationMs: 1_000 },
      ],
    });

    expect(
      audioReferenceDurationRejection([
        { label: "long.wav", durationMs: 20_000 },
        { label: "short.wav", durationMs: 500 },
      ]),
    ).toEqual({
      kind: "tooShort",
      clips: [{ label: "short.wav", durationMs: 500 }],
    });
  });

  it("does not impose an aggregate-duration cap", () => {
    expect(
      audioReferenceDurationRejection([
        { label: "one.wav", durationMs: 6_000 },
        { label: "two.wav", durationMs: 6_000 },
        { label: "three.wav", durationMs: 6_000 },
      ]),
    ).toBeNull();
  });

  it("lets unknown durations fall through to the backend", () => {
    expect(
      audioReferenceDurationRejection([
        { label: "unknown.wav", durationMs: null },
        { label: "unmeasured.wav", durationMs: 0 },
      ]),
    ).toBeNull();
  });

  it("formats violating clip durations without rounding across a boundary", () => {
    const translations: Record<string, string> = {
      "node.videoNode.audio.clipDuration": "{{label}}（{{seconds}}s）",
      "node.videoNode.audio.clipSeparator": "、",
    };
    const translate = (key: string, vars?: Record<string, string | number>) =>
      Object.entries(vars ?? {}).reduce(
        (value, [name, replacement]) =>
          value.replace(`{{${name}}}`, String(replacement)),
        translations[key] ?? key,
      );

    expect(
      formatAudioDurationClips(
        [
          { label: "short.wav", durationMs: 1_799 },
          { label: "long.wav", durationMs: 15_201 },
        ],
        translate,
      ),
    ).toBe("short.wav（1.799s）、long.wav（15.201s）");
  });
});
