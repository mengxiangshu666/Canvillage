// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { DragEvent } from "react";
import { isExportImageNode, isImageEditNode, isImageGenNode, isStoryboardGenNode, isUploadNode, type CanvasNode, type Seedance2SceneOptimize, type VideoGenQuality } from "@/features/canvas/domain/canvasNodes";
import type { FreezoneVideoAspectRatio, FreezoneVideoResolution } from "@/api/ops";
import { mediaNeedsCrossOrigin } from "@/features/canvas/application/imageData";
import { isVideoFile } from "@/features/canvas/application/videoFileTypes";
import { isValidImageAspectRatio, normalizeVideoQualityValue } from "@/features/canvas/models/imageCapabilityValues";
import { VIDEO_REFERENCE_CAPS } from "@/features/canvas/domain/videoCapabilityCompiler";
export const REFERENCE_CAPS_BY_MODE = VIDEO_REFERENCE_CAPS;

export const ASPECT_RATIOS: ReadonlyArray<FreezoneVideoAspectRatio> = [
  "auto",
  "16:9",
  "4:3",
  "1:1",
  "3:4",
  "9:16",
  "21:9",
];
export const QUALITIES: ReadonlyArray<VideoGenQuality> = ["480P", "720P", "768P", "1080P", "2K", "4K"];
export const SCENE_OPTIMIZE_OPTIONS: ReadonlyArray<Seedance2SceneOptimize> = ["anime", "realistic"];
export const DEFAULT_DURATION_MIN = 5;
export const DEFAULT_DURATION_MAX = 15;
export const RECOVERABLE_VIDEO_QUERY_ERROR_CODES = new Set([
  "VIDEO_UPSTREAM_TASK_FAILED_NO_REASON",
  "VIDEO_UPSTREAM_TIMEOUT",
  "VIDEO_QUERY_TRANSIENT",
  "VIDEO_STATUS_MISSING",
  "provider_result_download_pending",
]);

export function resolutionToQuality(resolution: string): VideoGenQuality | null {
  const normalized = normalizeVideoQualityValue(resolution);
  if (normalized) return normalized as VideoGenQuality;
  const raw = String(resolution || "").trim();
  return raw.length > 0 && raw.length <= 120 && !/[\u0000-\u001f\u007f]/.test(raw)
    ? (raw as VideoGenQuality)
    : null;
}

export function clampVideoDuration(
  value: number,
  bounds: { min: number; max: number },
  durationOptions?: readonly number[],
): number {
  const clamped = Math.min(Math.max(Math.round(value), bounds.min), bounds.max);
  if (!durationOptions || durationOptions.length === 0) return clamped;
  return durationOptions.reduce((closest, option) =>
    Math.abs(option - clamped) < Math.abs(closest - clamped) ? option : closest,
    durationOptions[0],
  );
}

export function qualityToResolution(q: VideoGenQuality): FreezoneVideoResolution {
  const raw = String(q || "").trim();
  const normalized = normalizeVideoQualityValue(raw);
  return (normalized ? normalized.toLowerCase() : raw) as FreezoneVideoResolution;
}
export function videoQualityOptionsForModel(
  model: {
    resolutionOptions?: string[];
    runtimeResolutionOptions?: string[];
    capabilitySource?: string;
  } | null | undefined,
): readonly VideoGenQuality[] {
  if (!model) return [];
  const declared = model?.runtimeResolutionOptions?.length
    ? model.runtimeResolutionOptions
    : model?.resolutionOptions ?? [];
  const options = declared
    .map(resolutionToQuality)
    .filter((item): item is VideoGenQuality => Boolean(item));
  // A non-empty capability contract is authoritative even for provider-
  // specific values such as 1440p. Do not advertise local defaults here.
  if (declared.length > 0) return options;
  return model.capabilitySource === "unknown" || model.resolutionOptions !== undefined
    ? []
    : QUALITIES;
}

export function advertisedVideoQualityOptionsForModel(
  model: { advertisedResolutionOptions?: string[] } | null | undefined,
  effective: readonly VideoGenQuality[],
): readonly VideoGenQuality[] {
  // The catalog claim is diagnostic only. The runtime contract is the only
  // list that may reach an actionable node control; otherwise a stale
  // advertised value can be selected and rejected after submission.
  void model;
  return effective;
}

export function isValidVideoAspectRatio(value: string): boolean {
  const match = /^(\d{1,6}(?:\.\d{1,4})?):(\d{1,6}(?:\.\d{1,4})?)$/.exec(value.trim());
  if (!match) return false;
  const width = Number(match[1]);
  const height = Number(match[2]);
  return Number.isFinite(width) && Number.isFinite(height) && width > 0 && height > 0;
}

export function rejectedVideoQualityOptionsForModel(
  model: { runtimeRejectedResolutionOptions?: string[] } | null | undefined,
): ReadonlySet<VideoGenQuality> {
  return new Set(
    (model?.runtimeRejectedResolutionOptions ?? [])
      .map(resolutionToQuality)
      .filter((item): item is VideoGenQuality => Boolean(item)),
  );
}

export function normalizeVideoQuality(
  value: VideoGenQuality | undefined,
  options: readonly VideoGenQuality[],
  preferred?: VideoGenQuality | null,
): VideoGenQuality {
  // A remembered quality from another model is not a submission candidate.
  // Prefer the selected model's own default, then its first declared option.
  const fallback = preferred && options.includes(preferred)
    ? preferred
    : options[0] ?? "480P";
  return value && options.includes(value) ? value : fallback;
}

export function videoDurationOptionsForModel(
  model: {
    durationOptions?: number[];
  } | null | undefined,
): readonly number[] | undefined {
  if (!model || model.durationOptions === undefined) return undefined;
  return [...new Set(
    model.durationOptions.filter(
      (value) => Number.isFinite(value) && Number.isInteger(value) && value >= 1 && value <= 300,
    ),
  )].sort((a, b) => a - b);
}

export function videoDurationParameterEnabledForModel(
  model: {
    durationOptions?: number[];
    supportsCustomDuration?: boolean;
    durationParameterEnabled?: boolean;
  } | null | undefined,
): boolean {
  if (!model) return true;
  if (model.durationParameterEnabled !== undefined) {
    return model.durationParameterEnabled;
  }
  const options = videoDurationOptionsForModel(model);
  if (options !== undefined) return options.length > 0 || model.supportsCustomDuration === true;
  return true;
}

export function videoDurationBoundsForModel(
  model: {
    minDuration?: number | null;
    maxDuration?: number | null;
    durationOptions?: number[];
  } | null | undefined,
): { min: number; max: number } {
  const durationOptions = videoDurationOptionsForModel(model);
  if (durationOptions && durationOptions.length > 0) {
    return { min: durationOptions[0], max: durationOptions[durationOptions.length - 1] };
  }
  const min = Number(model?.minDuration);
  const max = Number(model?.maxDuration);
  const resolvedMin = Number.isFinite(min) && min > 0 ? min : DEFAULT_DURATION_MIN;
  const resolvedMax = Number.isFinite(max) && max >= resolvedMin ? max : DEFAULT_DURATION_MAX;
  return { min: resolvedMin, max: resolvedMax };
}

export function videoAspectRatioOptionsForModel(
  model: {
    aspectRatioOptions?: string[];
    supportsCustomAspectRatio?: boolean;
    capabilitySource?: string;
  } | null | undefined,
): readonly FreezoneVideoAspectRatio[] {
  if (!model) return [];
  const declared = (model?.aspectRatioOptions ?? []).filter(
    (value): value is FreezoneVideoAspectRatio =>
      isValidImageAspectRatio(value, false),
  );
  if (model.aspectRatioOptions !== undefined) return declared;
  return model.capabilitySource === "unknown" ? [] : ASPECT_RATIOS;
}

export function modelParameterDefaults(
  model: {
    parameterDefaults?: {
      resolution?: string;
      durationSeconds?: number;
      aspectRatio?: string;
      generateAudio?: boolean;
    };
  } | null | undefined,
) {
  return model?.parameterDefaults;
}

// 音频节点的 durationMs 是懒加载的（波形播放器挂载读元数据后才写入），刚上传、
// 从未渲染过的音频节点可能为 null。提交前用一个临时 <audio> 探测真实时长兜底，
// 探测失败（CORS/网络等）返回 null，不阻断提交，交由后端兜底。
export function probeAudioDurationMs(url: string): Promise<number | null> {
  return new Promise((resolve) => {
    if (!url) {
      resolve(null);
      return;
    }
    const audio = document.createElement("audio");
    let settled = false;
    const finish = (ms: number | null) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timer);
      audio.onloadedmetadata = null;
      audio.onerror = null;
      audio.removeAttribute("src");
      audio.load();
      resolve(ms);
    };
    const timer = window.setTimeout(() => finish(null), 8000);
    audio.preload = "metadata";
    audio.onloadedmetadata = () => {
      const secs = audio.duration;
      finish(Number.isFinite(secs) && secs > 0 ? Math.round(secs * 1000) : null);
    };
    audio.onerror = () => finish(null);
    audio.src = url;
  });
}

export function isSeedance2ValueModel(modelId: string | null | undefined): boolean {
  const normalized = String(modelId ?? "").trim().toLowerCase();
  return normalized === "newapi_seedance-2.0-value" ||
    normalized === "newapi_seedance-2.0-fast-value" ||
    normalized === "huimeng_seedance-2.0-value" ||
    normalized === "huimeng_seedance-2.0-fast-value";
}

export function sceneOptimizeOptionsForModel(
  model: {
    id?: string;
    apiModel?: string;
    sceneOptimizeOptions?: Array<"anime" | "realistic">;
  } | null | undefined,
): readonly Seedance2SceneOptimize[] {
  if (model?.sceneOptimizeOptions?.length) {
    return model.sceneOptimizeOptions;
  }
  return isSeedance2ValueModel(model?.apiModel ?? model?.id) ? SCENE_OPTIMIZE_OPTIONS : [];
}

export function defaultSceneOptimizeForModel(
  model: {
    id?: string;
    apiModel?: string;
    defaultSceneOptimize?: "anime" | "realistic" | null;
  } | null | undefined,
): Seedance2SceneOptimize {
  if (model?.defaultSceneOptimize === "anime" || model?.defaultSceneOptimize === "realistic") {
    return model.defaultSceneOptimize;
  }
  const modelId = String(model?.apiModel ?? model?.id ?? "").toLowerCase();
  return modelId.includes("fast-value") ? "realistic" : "anime";
}

export function normalizeSceneOptimize(
  value: Seedance2SceneOptimize | undefined,
  options: readonly Seedance2SceneOptimize[],
  fallback: Seedance2SceneOptimize,
): Seedance2SceneOptimize | undefined {
  if (options.length === 0) return undefined;
  return value && options.includes(value) ? value : fallback;
}

export function submittableImageUrl(
  node: CanvasNode | undefined | null,
): string | null {
  if (!node) return null;
  // Script state keyframes have a referenceImageUrl while they are queued.
  // That URL is an input to the image job, not a completed keyframe output.
  // Treating it as submittable makes a downstream video start early and
  // silently duplicates the shot's first frame.
  if (node.data.scriptShotKeyframeRowKey && !node.data.imageUrl) return null;
  if (isImageGenNode(node)) {
    const data = node.data;
    const ref =
      typeof data.referenceImageUrl === "string" &&
      data.referenceImageUrl.length > 0
        ? data.referenceImageUrl
        : null;
    return data.imageUrl || ref;
  }
  if (
    isUploadNode(node) ||
    isImageEditNode(node) ||
    isExportImageNode(node) ||
    isStoryboardGenNode(node)
  ) {
    return node.data.imageUrl || null;
  }
  return null;
}

export function resolveDroppedVideoFile(event: DragEvent<HTMLElement>): File | null {
  const directFile = event.dataTransfer.files?.[0];
  if (directFile && isVideoFile(directFile)) {
    return directFile;
  }
  // items[].type 同样对 .mxf 为空串，先按 MIME 粗筛拿到 File 再用扩展名兜底。
  const candidates = Array.from(event.dataTransfer.items || []).filter(
    (candidate) => candidate.kind === "file",
  );
  for (const candidate of candidates) {
    const file = candidate.getAsFile();
    if (file && isVideoFile(file)) return file;
  }
  return null;
}

/**
 * Render a single frame from a video URL into a PNG blob using an offscreen
 * <video>. Cross-origin CDN media (absolute http(s) URL, the production case)
 * must load with CORS, otherwise drawing it to the canvas taints it and
 * `toBlob` throws. Same-origin /static (the dev vite proxy) skips crossOrigin
 * since that origin doesn't echo Access-Control-Allow-Origin and isn't tainted.
 */
export async function captureVideoFrameBlob(
  src: string,
  seekSec: number,
): Promise<Blob> {
  return await new Promise((resolve, reject) => {
    const video = document.createElement("video");
    video.muted = true;
    video.playsInline = true;
    video.preload = "auto";
    if (mediaNeedsCrossOrigin(src)) video.crossOrigin = "anonymous";

    const cleanup = () => {
      video.removeAttribute("src");
      try {
        video.load();
      } catch {
        // ignored
      }
    };
    const fail = (reason: unknown) => {
      cleanup();
      reject(reason instanceof Error ? reason : new Error(String(reason)));
    };

    video.addEventListener("error", () => fail("video element error"));
    video.addEventListener(
      "loadeddata",
      () => {
        const duration = video.duration;
        if (!Number.isFinite(duration) || duration <= 0) {
          fail("invalid video duration");
          return;
        }
        const targetTime = Math.max(
          0,
          Math.min(seekSec, Math.max(0, duration - 0.05)),
        );
        video.addEventListener(
          "seeked",
          () => {
            const canvas = document.createElement("canvas");
            canvas.width = video.videoWidth;
            canvas.height = video.videoHeight;
            const ctx = canvas.getContext("2d");
            if (!ctx) {
              fail("canvas context unavailable");
              return;
            }
            try {
              ctx.drawImage(video, 0, 0);
            } catch (error) {
              fail(error);
              return;
            }
            canvas.toBlob((blob) => {
              cleanup();
              if (blob) resolve(blob);
              else reject(new Error("canvas.toBlob returned null"));
            }, "image/png");
          },
          { once: true },
        );
        try {
          video.currentTime = targetTime;
        } catch (error) {
          fail(error);
        }
      },
      { once: true },
    );

    video.src = src;
    try {
      video.load();
    } catch {
      // ignored
    }
  });
}
