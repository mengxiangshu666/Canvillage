// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { VideoGenMode } from "./canvasNodes";

export type VideoReferenceCounts = {
  images: number;
  videos: number;
  audios: number;
};

export type VideoModelFamily =
  | "happyhorse"
  | "firefly-seedance2"
  | "grok-video-channel"
  | "seedance-1x"
  | "seedance-2"
  | "seedance-2-value"
  | "wokey-jimeng"
  | "kacang-933"
  | "kacang-mini-h3"
  | "kacang-kling-v2v"
  | "prompt-hubs-sd"
  | "kling-3.0"
  | "prompt-hubs-flex"
  | "direct"
  | "generic";

export type VideoNativeAudioCapability =
  | "unsupported"
  | "optional"
  | "required";

export type VideoCapabilityContract = {
  supportedModes?: readonly string[];
  referenceLimits?: Partial<
    Record<string, Partial<Record<"image" | "video" | "audio", number>>>
  >;
};

const VIDEO_MODE_VALUES: readonly VideoGenMode[] = [
  "textToVideo",
  "imageToVideo",
  "firstLastFrame",
  "allReference",
  "imageReference",
  "videoEdit",
];

export function videoModesForCapability(
  capability: VideoCapabilityContract | null | undefined,
): VideoGenMode[] {
  return (capability?.supportedModes ?? []).filter(
    (item): item is VideoGenMode =>
      VIDEO_MODE_VALUES.includes(item as VideoGenMode),
  );
}

/**
 * Capability metadata has three states, not two:
 * - `undefined`: an older catalog omitted the field, so family compatibility
 *   rules may still be used;
 * - `[]`: the upstream explicitly declared no supported canvas modes;
 * - non-empty: only the declared modes are legal.
 */
export function hasDeclaredVideoModes(
  capability: VideoCapabilityContract | null | undefined,
): boolean {
  return capability?.supportedModes !== undefined;
}

export function isVideoModeSupportedForModel(
  family: VideoModelFamily,
  mode: VideoGenMode,
  capability?: VideoCapabilityContract | null,
): boolean {
  const declared = videoModesForCapability(capability);
  return hasDeclaredVideoModes(capability)
    ? declared.includes(mode)
    : mode === 'allReference' && (family === 'generic' || family === 'direct')
      || isVideoModeSupported(family, mode);
}

export function resolveVideoModeForCapability(
  value: VideoGenMode,
  family: VideoModelFamily,
  capability?: VideoCapabilityContract | null,
): VideoGenMode {
  const declared = videoModesForCapability(capability);
  if (hasDeclaredVideoModes(capability)) {
    // There is no valid replacement when the upstream explicitly exposes no
    // canvas mode. Preserve the current value so submit validation can show a
    // precise contract error instead of silently resurrecting a family mode.
    if (declared.length === 0) return value;
    return declared.includes(value) ? value : declared[0];
  }
  if (isVideoModeSupportedForModel(family, value, capability)) return value;
  return (
    VIDEO_MODE_VALUES.find((mode) => isVideoModeSupported(family, mode)) ??
    "textToVideo"
  );
}

/**
 * Normalize the one user-facing audio switch without letting model metadata
 * rewrite it.  A provider capability only decides how the request is sent;
 * the persisted switch is the source of truth for the resulting artifact.
 */
export function normalizeVideoAudioPreference(
  value: boolean | undefined,
  _nativeAudio?: VideoNativeAudioCapability,
  userSet = false,
): boolean {
  return userSet && typeof value === "boolean" ? value : true;
}

/**
 * 视频节点上那条音频开关最终算出来的值。
 *
 * 除了「用户手动拨过」（`generateAudioUserSet`），还有第二个权威来源：**脚本派生的
 * 原生声音路由**（`nativeAudioStrategy === 'native'`）。那是脚本那一行音效列的声明
 * （见 `workflow_runtime/freezone_videos._shot_audio_contract()`，T-152），不是模型
 * 元数据，也不该被当成「用户没拨过 → 默认静音」。
 *
 * 不这么算的后果实测过：开关被压成 false，提交时带出去的
 * `generate_audio_explicit=false` 会被后端当成用户明确关掉了声音，于是 provider 按
 * required 出回来的那条音轨在本地成品里被判成多余而剥掉 —— 有音效设计的镜头拿到静音。
 *
 * 用户显式拨过时用户优先（`generateAudioUserSet === true` 时原样保留那个布尔值）；
 * 没有语义路由的普通视频节点默认开启原生音频。
 */
export function resolveVideoNodeAudioSwitch(data: {
  generateAudio?: boolean;
  generateAudioUserSet?: boolean;
  nativeAudioStrategy?: string | null;
}): boolean {
  if (data.generateAudioUserSet === true) {
    return data.generateAudio === true;
  }
  return true;
}

export type VideoEmptyStateCtaMode =
  | "allReference"
  | "imageReference"
  | "imageToVideo"
  | "firstLastFrame";

export const VIDEO_REFERENCE_CAPS: Partial<
  Record<VideoGenMode, { image: number; video: number; audio: number }>
> = {
  allReference: { image: 9, video: 3, audio: 3 },
  firstLastFrame: { image: 2, video: 0, audio: 0 },
  imageReference: { image: 9, video: 0, audio: 0 },
  videoEdit: { image: 5, video: 1, audio: 0 },
};

/**
 * Prompt-Hubs Firefly Seedance2's documented all-reference ceiling.  The
 * per-kind caps are 9 images, 3 videos, and 3 audio clips, but their sum is
 * deliberately *not* 15: the upstream accepts at most 12 media references
 * in one request.  Keep this beside the capability compiler so the canvas
 * blocks an over-limit graph before it silently drops any connected asset.
 */
export const FIREFLY_SEEDANCE2_TOTAL_REFERENCE_LIMIT = 12;

const FIREFLY_SEEDANCE2_REFERENCE_CAPS: Partial<
  Record<VideoGenMode, { image: number; video: number; audio: number; total: number }>
> = {
  allReference: {
    image: 9,
    video: 3,
    audio: 3,
    total: FIREFLY_SEEDANCE2_TOTAL_REFERENCE_LIMIT,
  },
  imageReference: { image: 9, video: 0, audio: 0, total: 9 },
  imageToVideo: { image: 1, video: 0, audio: 0, total: 1 },
};

// Seedance 2.0 omni-gen validates every audio reference independently.
// There is no aggregate-duration cap for this canvas path.
export const MIN_AUDIO_REFERENCE_DURATION_MS = 1_800;
export const MAX_AUDIO_REFERENCE_DURATION_MS = 15_200;

export type AudioDurationRejection = {
  kind: "tooShort" | "tooLong";
  clips: { label: string; durationMs: number }[];
};

export function audioReferenceDurationRejection(
  clips: readonly { label: string; durationMs: number | null }[],
): AudioDurationRejection | null {
  const measured = clips.filter(
    (clip): clip is { label: string; durationMs: number } =>
      typeof clip.durationMs === "number" && clip.durationMs > 0,
  );
  const tooShort = measured.filter(
    (clip) => clip.durationMs < MIN_AUDIO_REFERENCE_DURATION_MS,
  );
  if (tooShort.length > 0) {
    return { kind: "tooShort", clips: tooShort };
  }
  const tooLong = measured.filter(
    (clip) => clip.durationMs > MAX_AUDIO_REFERENCE_DURATION_MS,
  );
  if (tooLong.length > 0) {
    return { kind: "tooLong", clips: tooLong };
  }
  return null;
}

function formatClipSeconds(durationMs: number): string {
  return (durationMs / 1000).toFixed(3).replace(/\.?0+$/, "");
}

export function formatAudioDurationClips(
  clips: readonly { label: string; durationMs: number }[],
  translate: (key: string, vars?: Record<string, string | number>) => string,
): string {
  return clips
    .map((clip) =>
      translate("node.videoNode.audio.clipDuration", {
        label: clip.label,
        seconds: formatClipSeconds(clip.durationMs),
      }),
    )
    .join(translate("node.videoNode.audio.clipSeparator"));
}

function normalizedModelId(modelId: string | null | undefined): string {
  return String(modelId ?? "").replace(/[\s._-]/g, "").toLowerCase();
}

export function isHappyHorseVideoModel(
  modelId: string | null | undefined,
): boolean {
  return normalizedModelId(modelId).includes("happyhorse");
}

export function isFireflySeedance2VideoModel(
  modelId: string | null | undefined,
): boolean {
  return normalizedModelId(modelId).includes("fireflyseedance2");
}

export function isGrokVideoChannelModel(
  modelId: string | null | undefined,
): boolean {
  return normalizedModelId(modelId).includes("grokvideochannel");
}

export function isSeedance1xVideoModel(
  modelId: string | null | undefined,
): boolean {
  return /seedance1\d/.test(normalizedModelId(modelId));
}

export function isSeedance2VideoModel(
  modelId: string | null | undefined,
): boolean {
  return /seedance2\d/.test(normalizedModelId(modelId));
}

export function isPromptHubsSdVideoModel(
  modelId: string | null | undefined,
): boolean {
  const normalized = normalizedModelId(modelId);
  return normalized.startsWith("newapisd2") || normalized.startsWith("sd20");
}

export function isKling30VideoModel(
  modelId: string | null | undefined,
): boolean {
  return normalizedModelId(modelId).includes("kling30");
}

export function isSeedance2VideoFamily(family: VideoModelFamily): boolean {
  return family === "seedance-2" || family === "seedance-2-value";
}

function isMultiReferenceVideoFamily(family: VideoModelFamily): boolean {
  return (
    isSeedance2VideoFamily(family) ||
    family === "firefly-seedance2" ||
    family === "wokey-jimeng" ||
    family === "kacang-933" ||
    family === "prompt-hubs-sd"
  );
}

export function compileVideoModelFamily(
  modelId: string | null | undefined,
): VideoModelFamily {
  const normalized = normalizedModelId(modelId);
  if (isHappyHorseVideoModel(modelId)) return "happyhorse";
  if (isFireflySeedance2VideoModel(modelId)) return "firefly-seedance2";
  if (isGrokVideoChannelModel(modelId)) return "grok-video-channel";
  if (normalized.startsWith("newapijimengseedance")) return "wokey-jimeng";
  if (normalized === "newapisvideosf933fast4802" || normalized === "svideosf933fast4802") return "kacang-933";
  if (normalized === "newapiminih3" || normalized === "minih3") return "kacang-mini-h3";
  if (normalized === "newapiklingv3omniv2vcreate" || normalized === "klingv3omniv2vcreate") return "kacang-kling-v2v";
  if (isPromptHubsSdVideoModel(modelId)) return "prompt-hubs-sd";
  if (isKling30VideoModel(modelId)) return "kling-3.0";
  if (isSeedance1xVideoModel(modelId)) return "seedance-1x";
  if (
    normalized === "newapiseedance20value" ||
    normalized === "newapiseedance20fastvalue" ||
    normalized === "huimengseedance20value" ||
    normalized === "huimengseedance20fastvalue"
  ) {
    return "seedance-2-value";
  }
  if (isSeedance2VideoModel(modelId)) return "seedance-2";
  return "generic";
}

export function resolveVideoModelFamily(
  modelId: string | null | undefined,
  declaredFamily?: string | null,
): VideoModelFamily {
  const compiled = compileVideoModelFamily(modelId);
  if (compiled !== "generic") return compiled;
  if (
    declaredFamily === "happyhorse" ||
    declaredFamily === "firefly-seedance2" ||
    declaredFamily === "grok-video-channel" ||
    declaredFamily === "seedance-1x" ||
    declaredFamily === "seedance-2" ||
    declaredFamily === "seedance-2-value" ||
    declaredFamily === "wokey-jimeng" ||
    declaredFamily === "kacang-933" ||
    declaredFamily === "kacang-mini-h3" ||
    declaredFamily === "kacang-kling-v2v" ||
    declaredFamily === "prompt-hubs-sd" ||
    declaredFamily === "kling-3.0" ||
    declaredFamily === "prompt-hubs-flex" ||
    declaredFamily === "direct"
  ) {
    return declaredFamily;
  }
  return "generic";
}

export function isVideoModeSupported(
  family: VideoModelFamily,
  mode: VideoGenMode,
): boolean {
  if (family === "happyhorse") {
    return ["textToVideo", "imageToVideo", "imageReference", "videoEdit"].includes(mode);
  }
  if (family === "firefly-seedance2") {
    return ["textToVideo", "imageToVideo", "allReference", "imageReference"].includes(mode);
  }
  if (family === "prompt-hubs-sd") {
    return ["textToVideo", "imageToVideo", "allReference", "imageReference"].includes(mode);
  }
  if (family === "kling-3.0" || family === "prompt-hubs-flex") {
    return ["textToVideo", "imageToVideo", "imageReference"].includes(mode);
  }
  if (family === "wokey-jimeng") {
    return ["textToVideo", "imageToVideo", "allReference", "firstLastFrame", "imageReference"].includes(mode);
  }
  if (family === "kacang-933") {
    return ["textToVideo", "imageToVideo", "allReference", "imageReference"].includes(mode);
  }
  if (family === "kacang-mini-h3") return mode === "textToVideo";
  if (family === "kacang-kling-v2v") return mode === "videoEdit";
  if (mode === "videoEdit") return false;
  if (mode === "allReference" || mode === "firstLastFrame") {
    return isSeedance2VideoFamily(family);
  }
  return true;
}

export function videoEmptyStateCtaModes(
  family: VideoModelFamily,
): VideoEmptyStateCtaMode[] {
  if (family === "happyhorse") {
    return ["imageToVideo", "imageReference"];
  }
  if (family === "firefly-seedance2") {
    return ["allReference", "imageReference"];
  }
  if (family === "prompt-hubs-sd") {
    return ["allReference", "imageReference"];
  }
  if (family === "kling-3.0" || family === "prompt-hubs-flex") {
    return ["imageToVideo", "imageReference"];
  }
  if (family === "wokey-jimeng") {
    return ["allReference", "imageReference", "firstLastFrame"];
  }
  if (family === "kacang-933") return ["allReference", "imageReference"];
  if (family === "kacang-mini-h3" || family === "kacang-kling-v2v") return [];
  if (isSeedance2VideoFamily(family)) {
    return ["allReference", "imageReference", "firstLastFrame"];
  }
  return ["imageToVideo"];
}

export function videoUpstreamImageDefaultMode(
  family: VideoModelFamily,
): VideoGenMode {
  if (family === "kacang-kling-v2v") return "videoEdit";
  if (family === "kacang-mini-h3") return "textToVideo";
  return isMultiReferenceVideoFamily(family) ? "allReference" : "imageToVideo";
}

export function videoModeRequiresPrompt(mode: VideoGenMode): boolean {
  return mode === "textToVideo" || mode === "allReference";
}

const PROMPT_REFERENCE_TOKEN_PATTERN = /@\s*(?:图片|图像|参考图|image|img|reference)\s*[-_#]?\s*\d+/giu;

/**
 * Return an inline error for prompt semantics that upstream gateways reject.
 *
 * The selected node duration is authoritative.  Numbers quoted inside the
 * prose (for example "第 3 秒切到特写") are descriptive and must not block
 * submission.
 */
export function videoPromptContractIssue(
  prompt: string,
  referenceCounts: VideoReferenceCounts,
): string | null {
  PROMPT_REFERENCE_TOKEN_PATTERN.lastIndex = 0;
  if (PROMPT_REFERENCE_TOKEN_PATTERN.test(prompt) && referenceCounts.images === 0) {
    return "提示词引用了参考图片，但当前没有连接可上传的图片素材";
  }
  return null;
}

export function canSubmitVideoGeneration(params: {
  mode: VideoGenMode;
  hasPromptText: boolean;
  referenceCounts: VideoReferenceCounts;
  selectedModelOffline?: boolean;
  mediaRejectionReason?: string | null;
  promptContractReason?: string | null;
  isGenerating?: boolean;
}): boolean {
  return videoSubmitDisabledReason(params) === null;
}

export function videoSubmitDisabledReason(params: {
  mode: VideoGenMode;
  hasPromptText: boolean;
  referenceCounts: VideoReferenceCounts;
  selectedModelOffline?: boolean;
  selectedModelOfflineReason?: string | null;
  mediaRejectionReason?: string | null;
  promptContractReason?: string | null;
  isGenerating?: boolean;
}): string | null {
  if (params.isGenerating) return "当前任务仍在生成中";
  if (params.selectedModelOffline) {
    return params.selectedModelOfflineReason?.trim() || "视频渠道未接通";
  }
  if (params.mediaRejectionReason) return params.mediaRejectionReason;
  if (params.promptContractReason) return params.promptContractReason;
  if (params.mode === "textToVideo" && !params.hasPromptText) {
    return "请先填写提示词";
  }
  if (
    params.mode === "allReference" &&
    !params.hasPromptText &&
    params.referenceCounts.images === 0 &&
    params.referenceCounts.videos === 0 &&
    params.referenceCounts.audios === 0
  ) {
    return "请填写提示词或连接参考素材";
  }
  if (params.mode === "videoEdit" && params.referenceCounts.videos === 0) {
    return "请先连接一个源视频";
  }
  if (
    params.mode !== "textToVideo" &&
    params.mode !== "allReference" &&
    params.mode !== "videoEdit" &&
    params.referenceCounts.images === 0
  ) {
    return "请先连接参考图片";
  }
  return null;
}

export function videoSubmitMediaRejectionReason(
  mode: VideoGenMode,
  family: VideoModelFamily,
  counts: VideoReferenceCounts,
  capability?: VideoCapabilityContract | null,
): string | null {
  if (!isVideoModeSupportedForModel(family, mode, capability)) {
    return "当前模型不支持该生成模式";
  }
  const dynamicLimits = capability?.referenceLimits?.[mode];
  const hasDynamicImageLimit =
    typeof dynamicLimits?.image === "number" &&
    Number.isFinite(dynamicLimits.image);
  if (dynamicLimits) {
    const checks: Array<{
      count: number;
      rawLimit: unknown;
      label: string;
    }> = [
      { count: counts.images, rawLimit: dynamicLimits.image, label: "张图片" },
      { count: counts.videos, rawLimit: dynamicLimits.video, label: "个视频" },
      { count: counts.audios, rawLimit: dynamicLimits.audio, label: "条音频" },
    ];
    for (const check of checks) {
      if (
        typeof check.rawLimit !== "number" ||
        !Number.isFinite(check.rawLimit)
      ) continue;
      const limit = Math.max(0, Math.floor(check.rawLimit));
      if (check.count > limit) {
        if (limit === 0) {
          const media = check.label.replace(/^[张个条]/, "");
          return `当前模型的当前模式不支持${media}参考`;
        }
        return `当前模型的当前模式最多支持 ${limit} ${check.label}`;
      }
    }
  }
  if (counts.videos > 0 && mode !== "allReference" && mode !== "videoEdit") {
    return "该模型不支持视频素材";
  }
  if (
    counts.audios > 0 &&
    mode !== "allReference" &&
    !(family === "kacang-kling-v2v" && mode === "videoEdit")
  ) {
    return "该模型不支持音频素材";
  }
  if (
    family === "kacang-mini-h3" &&
    (counts.images > 0 || counts.videos > 0 || counts.audios > 0)
  ) {
    return "Mini H3 为文生视频模型，请移除参考素材";
  }
  if (family === "kacang-kling-v2v" && counts.videos !== 1) {
    return "Kling V3 Omni 视频重绘需要且仅需要一条源视频";
  }

  if (family === "firefly-seedance2" || family === "kacang-933") {
    const limits = FIREFLY_SEEDANCE2_REFERENCE_CAPS[mode];
    if (limits) {
      const modelName = family === "kacang-933" ? "卡藏 933 Fast" : "Firefly Seedance2";
      if (counts.images > limits.image) {
        return `${modelName} 的当前模式最多支持 ${limits.image} 张图片参考`;
      }
      if (counts.videos > limits.video) {
        return `${modelName} 的当前模式最多支持 ${limits.video} 个视频参考`;
      }
      if (counts.audios > limits.audio) {
        return `${modelName} 的当前模式最多支持 ${limits.audio} 条音频参考`;
      }
      const total = counts.images + counts.videos + counts.audios;
      if (total > limits.total) {
        return `${modelName} 的当前模式最多支持 ${limits.total} 个参考素材（图片、视频、音频合计），请移除多余连线后再生成`;
      }
    }
  }

  if (
    counts.images > 1 &&
    mode !== 'allReference' &&
    !hasDynamicImageLimit &&
    !isMultiReferenceVideoFamily(family) &&
    family !== "happyhorse" &&
    family !== "grok-video-channel" &&
    family !== "kacang-kling-v2v"
  ) {
    return "该模型单次仅支持 1 张图片";
  }
  return null;
}

export function deriveVideoMode(
  family: VideoModelFamily,
  counts: VideoReferenceCounts,
  currentMode: VideoGenMode,
): VideoGenMode {
  // Kling V3 Omni is a video-to-video-only route. Keep its only valid mode
  // selected even before a source video is connected; submit validation then
  // gives the user the precise missing-source message instead of letting
  // generic auto-mode effects move the node into an unsupported mode.
  if (family === "kacang-kling-v2v") return "videoEdit";
  if (family === "kacang-mini-h3") return "textToVideo";
  if (family !== "happyhorse") return currentMode;
  if (counts.videos > 0) return "videoEdit";
  if (counts.images > 1) return "imageReference";
  if (counts.images === 1) {
    return currentMode === "imageReference" ? "imageReference" : "imageToVideo";
  }
  return "textToVideo";
}

export function recoverVideoModeForFamily(
  family: VideoModelFamily,
  counts: VideoReferenceCounts,
  currentMode: VideoGenMode,
): VideoGenMode {
  if (isVideoModeSupported(family, currentMode)) return currentMode;
  if (counts.videos > 0 && isVideoModeSupported(family, "allReference")) {
    return "allReference";
  }
  if (counts.images > 0) {
    return videoUpstreamImageDefaultMode(family);
  }
  if (counts.audios > 0 && isVideoModeSupported(family, "allReference")) {
    return "allReference";
  }
  return isVideoModeSupported(family, "textToVideo")
    ? "textToVideo"
    : videoUpstreamImageDefaultMode(family);
}

export function reconcileVideoModeForNode(
  family: VideoModelFamily,
  counts: VideoReferenceCounts,
  currentMode: VideoGenMode | null | undefined,
): VideoGenMode {
  const mode: VideoGenMode = currentMode ?? "textToVideo";

  if (family === "kacang-kling-v2v") return "videoEdit";
  if (family === "kacang-mini-h3") return "textToVideo";
  if (family === "happyhorse") return deriveVideoMode(family, counts, mode);

  if (counts.videos > 0 && isVideoModeSupported(family, "allReference")) {
    return "allReference";
  }
  if (counts.audios > 0 && isVideoModeSupported(family, "allReference")) {
    return "allReference";
  }

  if (!isVideoModeSupported(family, mode)) {
    return recoverVideoModeForFamily(family, counts, mode);
  }

  if (mode === "textToVideo" && counts.images > 0) {
    return videoUpstreamImageDefaultMode(family);
  }

  if (mode === "firstLastFrame" && counts.images > 2) {
    return isVideoModeSupported(family, "allReference")
      ? "allReference"
      : videoUpstreamImageDefaultMode(family);
  }

  return mode;
}

export function videoReferenceDisabledReason(
  family: VideoModelFamily,
  counts: VideoReferenceCounts,
): string | null {
  if (family === "grok-video-channel") {
    if (counts.videos > 0 || counts.audios > 0) {
      return "Grok Video Channel 仅支持图片素材";
    }
    if (counts.images > 8) {
      return "Grok Video Channel 最多支持 1 张首帧和 7 张参考图";
    }
  }
  if (family === "seedance-1x") {
    if (counts.videos > 0) return "该模型不支持视频素材";
    if (counts.audios > 0) return "该模型不支持音频素材";
    if (counts.images > 1) return "该模型单次仅支持 1 张图片";
  }
  return null;
}
