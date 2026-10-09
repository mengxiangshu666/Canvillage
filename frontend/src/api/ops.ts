// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { apiCall, apiClient } from "./client";
import { storyScriptRewriteBody, type FreezoneStoryScriptResult, type ScriptShotRewritePayloadFields } from "./scriptContract";
import { freezoneVideoModelFields, type FreezoneVideoModelFields } from "./freezoneVideoModelFields";
import type { FreezoneGenerationHistoryRecord, FreezoneJobResult } from './freezoneGenerationHistory';
export type * from './freezoneGenerationHistory';
// 脚本那几条类型搬去 `scriptContract.ts` 后原样转出：`ops.ts` 是登记在案的巨型文件
// （门禁要求只能缩小），而这些类型天然属于合同那一层。
export type * from "./scriptContract";
// Per-node generation history -------------------------------------------- //

/**
 * Optional canvas/node context the backend uses to record a per-node
 * generation history entry. Generation-style endpoints accept these; omitting
 * them is harmless (the backend simply skips history recording). Mixed into
 * each generation payload via {@link FreezoneNodeContext}.
 */
export interface FreezoneNodeContext {
  /** Current canvas id, usually "default". */
  canvasId?: string | null;
  /** Id of the node that triggered the generation. */
  nodeId?: string | null;
}

/**
 * Map the camelCase node context to the backend's snake_case body fields,
 * emitting keys only when present so legacy callers stay byte-identical.
 */
function nodeContextBody(ctx: FreezoneNodeContext): Record<string, string> {
  const out: Record<string, string> = {};
  if (ctx.canvasId) out.canvas_id = ctx.canvasId;
  if (ctx.nodeId) out.node_id = ctx.nodeId;
  return out;
}

/** Keep an explicitly empty model parameter omitted while preserving legacy defaults. */
function videoAspectRatioBody(
  value: FreezoneVideoAspectRatio | string | undefined,
  fallback = "16:9",
): Record<string, string> {
  if (typeof value === "string") {
    const normalized = value.trim();
    if (normalized) return { aspect_ratio: normalized };
    if (value === "") return {};
  }
  return { aspect_ratio: fallback };
}

/**
 * Read a node's recorded generation history (most recent first per the
 * backend). Returns `[]` when the node has no history yet. The endpoint is
 * independent of canvas JSON, so this never touches the saved-canvas flow.
 */
export async function fetchNodeGenerationHistory(
  project: string,
  canvasId: string,
  nodeId: string,
  limit = 100,
): Promise<FreezoneGenerationHistoryRecord[]> {
  const data = await apiCall<{ records?: FreezoneGenerationHistoryRecord[] }>(
    `projects/${encodeURIComponent(project)}/freezone/canvases/${encodeURIComponent(
      canvasId,
    )}/nodes/${encodeURIComponent(nodeId)}/generation-history?limit=${limit}`,
  );
  return data?.records ?? [];
}

/**
 * Read the whole canvas's generation history in one request (most recent
 * first). Unlike {@link fetchNodeGenerationHistory} this is not scoped to a
 * single node, and the backend aggregates across every node that ever recorded
 * history on this canvas — including nodes since deleted from the canvas — so
 * their past attempts stay visible in the history browser.
 */
export async function fetchCanvasGenerationHistory(
  project: string,
  canvasId: string,
  limit = 500,
): Promise<FreezoneGenerationHistoryRecord[]> {
  const data = await apiCall<{ records?: FreezoneGenerationHistoryRecord[] }>(
    `projects/${encodeURIComponent(project)}/freezone/canvases/${encodeURIComponent(
      canvasId,
    )}/generation-history?limit=${limit}`,
  );
  return data?.records ?? [];
}

// /freezone/gen ----------------------------------------------------------- //

export type FreezoneProvider =
  | "openrouter"
  | "huimeng"
  | "openai"
  | "newapi"
  | "direct";

export interface FreezoneGenCamera {
  /** id from /freezone/image/camera-options.camera_bodies */
  cameraBodyId?: string | null;
  /** id from /freezone/image/camera-options.lenses */
  lensId?: string | null;
  focalLengthMm?: number | null;
  aperture?: string | null;
}

export interface FreezoneGenStyle {
  /** id from /freezone/image/style-templates */
  templateId?: string | null;
}

export interface FreezoneGenPayload extends FreezoneNodeContext {
  prompt: string;
  aspectRatio?: string;
  imageSize?: string;
  referenceUrls?: string[];
  camera?: FreezoneGenCamera | null;
  style?: FreezoneGenStyle | null;
  /** Override `NANOBANANA_PROVIDER` env default for the reference-image path. */
  provider?: FreezoneProvider | null;
  /** Override the provider's default model (e.g. "gpt-image-2"). */
  model?: string | null;
  /** 注册表模型 id（还原用；与 provider 拆分后的 model 串不同）。 */
  modelId?: string | null;
  /** 生成模式（还原用）：text_to_image / image_to_image / all_reference / image_reference。 */
  genMode?: string | null;
  /** Only honored by openai gpt-image-2 (low / medium / high / auto). */
  quality?: string | null;
  /**
   * 模型能力合同声明的高级参数（如 MJ 系 stylize / weird / chaos /
   * personalisation）。后端按所选模型的合同过滤并归一化，未知键不会透传上游。
   */
  advancedSettings?: Record<string, unknown> | null;
}

export interface FreezoneJobRef {
  task_type:
    | "freezone_gen"
    | "freezone_edit"
    | "freezone_multi_view"
    | "freezone_relight"
    | "freezone_scene_360"
    | "freezone_template_edit"
    | "freezone_upscale"
    | "freezone_outpaint"
    | "freezone_redraw"
    | "freezone_video_gen"
    | "freezone_video_omni_gen"
    | "freezone_video_i2v"
    | "freezone_video_erase"
    | "freezone_video_compose"
    | "freezone_video_cut"
    | "freezone_video_upscale"
    | "freezone_audio_separate"
    | "freezone_audio_speech"
    | "freezone_audio_eleven_music"
    | "freezone_image_reverse_prompt"
    | "freezone_text_translate"
    | "freezone_prompt_optimize"
    | "freezone_story_script"
    | "freezone_video_story"
    | "stage_asset";
  job_id: string;
  task_key: string;
}

// /freezone/video/gen ----------------------------------------------------- //

export type FreezoneVideoAspectRatio =
  | "auto"
  | "16:9"
  | "4:3"
  | "1:1"
  | "3:4"
  | "9:16"
  | "21:9"
  | (string & {});

export type FreezoneVideoResolution =
  | "480p"
  | "720p"
  | "768p"
  | "1080p"
  | "2k"
  | "4k"
  | (string & {});

/** Lossless provider parameter declaration carried through the model catalog. */
export interface FreezoneVideoCapabilityParameter {
  key: string;
  providerKey?: string;
  provider_key?: string;
  type?: "string" | "number" | "integer" | "boolean" | "object" | "array" | string;
  required?: boolean;
  default?: unknown;
  enum?: unknown[];
  minimum?: number;
  maximum?: number;
  step?: number;
  label?: string;
  description?: string;
  advanced?: boolean;
  source?: string;
}

export interface FreezoneVideoCapabilityOpaqueField {
  key: string;
  value: unknown;
  source?: string;
}

/** Local element marker on the source image, used to anchor subjects/objects. */
export interface FreezoneVideoMark {
  label: string;
  sourceUrl?: string;
  pointX?: number | null;
  pointY?: number | null;
  boxX?: number | null;
  boxY?: number | null;
  boxWidth?: number | null;
  boxHeight?: number | null;
  note?: string;
}

export interface FreezoneVideoGenPayload extends FreezoneNodeContext {
  prompt: string;
  /** e.g. locked_off / follow_tracking / orbit_up */
  cameraTemplateId?: string | null;
  characterIds?: string[];
  marks?: FreezoneVideoMark[];
  aspectRatio?: FreezoneVideoAspectRatio | string;
  resolution?: FreezoneVideoResolution;
  /** seconds; spec only requires ≥1, the UI typically caps higher. */
  durationSeconds?: number;
  generateAudio?: boolean;
  /** True only when the user explicitly changed the native-audio switch. */
  generateAudioExplicit?: boolean;
  /** Structured speech fields; never copied into the visual prompt. */
  dialogueText?: string;
  spokenDialogue?: string[];
  audioType?: "silence" | "narration" | "dialogue" | "";
  speaker?: string;
  nativeAudioStrategy?: "external" | "native";
  audioAssetRef?: string | null;
  /** Backend/API execution model id, e.g. huimeng_seedance20_fast. */
  model?: string;
  /** Stable catalog/picker id, used only for node-history restoration. */
  modelId?: string;
  /** 生成模式（还原用）：textToVideo / imageToVideo / firstLastFrame / imageReference / allReference。 */
  genMode?: string;
  /**
   * Real-person material review. Set `true` when the input contains real
   * human faces so the backend routes the job through the human-review path
   * (may take longer, approval not guaranteed). Omitted/false otherwise.
   */
  humanReview?: boolean;
  sceneOptimize?: "anime" | "realistic" | null;
  /** Provider-specific controls kept losslessly by the selected model contract. */
  parameters?: Record<string, unknown>;
}

function videoDurationBody(durationSeconds?: number): Record<string, number> {
  if (typeof durationSeconds !== "number" || !Number.isFinite(durationSeconds)) {
    return {};
  }
  return { duration_seconds: Math.max(durationSeconds, 1) };
}

function videoAudioTypeBody(
  value: "silence" | "narration" | "dialogue" | "" | null | undefined,
): Record<string, "silence" | "narration" | "dialogue"> {
  // Canvas nodes persist an empty string when the user has not chosen a
  // sound role. The API only accepts the three named roles or an omitted
  // field; sending "" is rejected before generation starts.
  if (value === "silence" || value === "narration" || value === "dialogue") {
    return { audio_type: value };
  }
  return {};
}

type FreezoneVideoCommonPayload = FreezoneNodeContext & {
  prompt?: string;
  cameraTemplateId?: string | null;
  marks?: FreezoneVideoMark[];
  aspectRatio?: FreezoneVideoAspectRatio | string;
  resolution?: FreezoneVideoResolution;
  durationSeconds?: number;
  generateAudio?: boolean;
  generateAudioExplicit?: boolean;
  dialogueText?: string;
  spokenDialogue?: string[];
  audioType?: "silence" | "narration" | "dialogue" | "";
  speaker?: string;
  nativeAudioStrategy?: "external" | "native";
  audioAssetRef?: string | null;
  model?: string;
  modelId?: string;
  genMode?: string;
  humanReview?: boolean;
  sceneOptimize?: "anime" | "realistic" | null;
  parameters?: Record<string, unknown>;
};

/** Build the fields shared by every video submission compatibility endpoint. */
function videoSubmissionBody(
  payload: FreezoneVideoCommonPayload,
  includeSceneOptimize = true,
): Record<string, unknown> {
  return {
    prompt: payload.prompt ?? "",
    camera_template_id: payload.cameraTemplateId ?? null,
    marks: (payload.marks ?? []).map((m) => ({
      label: m.label,
      source_url: m.sourceUrl ?? "",
      point_x: m.pointX ?? null,
      point_y: m.pointY ?? null,
      box_x: m.boxX ?? null,
      box_y: m.boxY ?? null,
      box_width: m.boxWidth ?? null,
      box_height: m.boxHeight ?? null,
      note: m.note ?? "",
    })),
    ...videoAspectRatioBody(payload.aspectRatio),
    resolution: payload.resolution ?? "480p",
    ...videoDurationBody(payload.durationSeconds),
    generate_audio: payload.generateAudio ?? true,
    ...(payload.generateAudioExplicit === undefined
      ? {}
      : { generate_audio_explicit: payload.generateAudioExplicit === true }),
    dialogue_text: payload.dialogueText ?? "",
    spoken_dialogue: payload.spokenDialogue ?? [],
    ...videoAudioTypeBody(payload.audioType),
    speaker: payload.speaker ?? "",
    native_audio_strategy: payload.nativeAudioStrategy ?? "native",
    audio_asset_ref: payload.audioAssetRef ?? "",
    ...(payload.model ? { model: payload.model } : {}),
    ...(payload.modelId
      ? { model_id: payload.modelId }
      : payload.model
        ? { model_id: payload.model }
        : {}),
    ...(payload.genMode ? { gen_mode: payload.genMode } : {}),
    ...(payload.humanReview === undefined ? {} : { human_review: payload.humanReview }),
    ...(includeSceneOptimize ? { scene_optimize: payload.sceneOptimize ?? null } : {}),
    ...(payload.parameters ? { parameters: payload.parameters } : {}),
    ...nodeContextBody(payload),
  };
}

export async function submitFreezoneVideoGen(
  project: string,
  payload: FreezoneVideoGenPayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/video/gen`,
    {
      method: "POST",
      json: {
        ...videoSubmissionBody(payload),
        character_ids: payload.characterIds ?? [],
      },
    },
  );
}

/** Re-query/download an already accepted upstream video task; never submits. */
export async function recoverFreezoneVideoJob(
  project: string,
  jobId: string,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/jobs/freezone_video_gen/${encodeURIComponent(jobId)}/recover`,
    { method: "POST" },
  );
}

// /freezone/video/upscale ------------------------------------------------- //

/** Target clarity tier. Scales by long edge: 1080p=1920, 2k=2560, 4k=3840. */
export type FreezoneVideoUpscaleResolution = "1080p" | "2k" | "4k";

/** Denoise strength. none=off, 1x=light, 2x=medium. */
export type FreezoneVideoUpscaleDenoise = "none" | "1x" | "2x";

export interface FreezoneVideoUpscalePayload extends FreezoneNodeContext {
  /** Static URL of the source video to upscale. */
  sourceUrl: string;
  resolution?: FreezoneVideoUpscaleResolution;
  /** Base version only supports "none" (frame rate unchanged). */
  frameInterpolation?: "none";
  denoiseStrength?: FreezoneVideoUpscaleDenoise;
}

export async function submitFreezoneVideoUpscale(
  project: string,
  payload: FreezoneVideoUpscalePayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/video/upscale`,
    {
      method: "POST",
      json: {
        source_url: payload.sourceUrl,
        resolution: payload.resolution ?? "1080p",
        frame_interpolation: payload.frameInterpolation ?? "none",
        denoise_strength: payload.denoiseStrength ?? "1x",
        ...nodeContextBody(payload),
      },
    },
  );
}

export interface FreezoneVideoKeyframesPayload extends FreezoneNodeContext {
  /** Static URL of the first frame. At least one of first/last must be set. */
  firstFrameUrl?: string | null;
  lastFrameUrl?: string | null;
  prompt?: string;
  cameraTemplateId?: string | null;
  marks?: FreezoneVideoMark[];
  aspectRatio?: FreezoneVideoAspectRatio | string;
  resolution?: FreezoneVideoResolution;
  durationSeconds?: number;
  generateAudio?: boolean;
  /** True only when the user explicitly changed the native-audio switch. */
  generateAudioExplicit?: boolean;
  dialogueText?: string;
  spokenDialogue?: string[];
  audioType?: "silence" | "narration" | "dialogue" | "";
  speaker?: string;
  nativeAudioStrategy?: "external" | "native";
  audioAssetRef?: string | null;
  model?: string;
  /** Stable catalog/picker id, used only for node-history restoration. */
  modelId?: string;
  /** 生成模式（还原用）：textToVideo / imageToVideo / firstLastFrame / imageReference / allReference。 */
  genMode?: string;
  /** See {@link FreezoneVideoGenPayload.humanReview}. */
  humanReview?: boolean;
  sceneOptimize?: "anime" | "realistic" | null;
  /** Provider-specific controls kept losslessly by the selected model contract. */
  parameters?: Record<string, unknown>;
}

export async function submitFreezoneVideoKeyframes(
  project: string,
  payload: FreezoneVideoKeyframesPayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/video/keyframes`,
    {
      method: "POST",
      json: {
        first_frame_url: payload.firstFrameUrl ?? null,
        last_frame_url: payload.lastFrameUrl ?? null,
        ...videoSubmissionBody(payload),
      },
    },
  );
}

// /freezone/video/i2v ----------------------------------------------------- //
//
// Unified endpoint for 图生视频 (single image, treated as first-frame ref)
// and 图片参考视频 (2-9 images, multi-reference). The backend distinguishes
// these two modes by `image_urls.length`.

export interface FreezoneVideoI2vPayload extends FreezoneNodeContext {
  /** 1-9 image static URLs. First entry is the primary/first-frame ref. */
  imageUrls: string[];
  prompt?: string;
  cameraTemplateId?: string | null;
  marks?: FreezoneVideoMark[];
  aspectRatio?: FreezoneVideoAspectRatio | string;
  resolution?: FreezoneVideoResolution;
  durationSeconds?: number;
  generateAudio?: boolean;
  /** True only when the user explicitly changed the native-audio switch. */
  generateAudioExplicit?: boolean;
  dialogueText?: string;
  spokenDialogue?: string[];
  audioType?: "silence" | "narration" | "dialogue" | "";
  speaker?: string;
  nativeAudioStrategy?: "external" | "native";
  audioAssetRef?: string | null;
  /** default huimeng_seedance10_fast (matches keyframes); multi-image prefers seedance 2.0. */
  model?: string;
  /** Stable catalog/picker id, used only for node-history restoration. */
  modelId?: string;
  /** 生成模式（还原用）：textToVideo / imageToVideo / firstLastFrame / imageReference / allReference。 */
  genMode?: string;
  /** See {@link FreezoneVideoGenPayload.humanReview}. */
  humanReview?: boolean;
  sceneOptimize?: "anime" | "realistic" | null;
  /** Provider-specific controls kept losslessly by the selected model contract. */
  parameters?: Record<string, unknown>;
}

export async function submitFreezoneVideoI2v(
  project: string,
  payload: FreezoneVideoI2vPayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/video/i2v`,
    {
      method: "POST",
      json: {
        image_urls: payload.imageUrls.slice(0, 9),
        ...videoSubmissionBody(payload),
      },
    },
  );
}

// /freezone/video/video-edit ---------------------------------------------- //
//
// 视频编辑：1 个源视频 + 模型合同允许的参考图 → 上游 video_url + reference_images。

export interface FreezoneVideoEditPayload extends FreezoneNodeContext {
  /** 源视频静态地址，必填。 */
  videoUrl: string;
  /** 参考图静态地址，数量上限由所选模型合同决定。 */
  imageUrls?: string[];
  prompt?: string;
  cameraTemplateId?: string | null;
  marks?: FreezoneVideoMark[];
  aspectRatio?: FreezoneVideoAspectRatio | string;
  resolution?: FreezoneVideoResolution;
  durationSeconds?: number;
  /** 视频编辑音频策略：auto 自动 / origin 保留原声。 */
  audioSetting?: "auto" | "origin";
  generateAudio?: boolean;
  /** True only when the user explicitly changed the native-audio switch. */
  generateAudioExplicit?: boolean;
  dialogueText?: string;
  spokenDialogue?: string[];
  audioType?: "silence" | "narration" | "dialogue" | "";
  speaker?: string;
  nativeAudioStrategy?: "external" | "native";
  audioAssetRef?: string | null;
  /** default newapi_happyhorse-1.0. */
  model?: string;
  /** Stable catalog/picker id, used only for node-history restoration. */
  modelId?: string;
  /** 生成模式（还原用）：videoEdit。 */
  genMode?: string;
  humanReview?: boolean;
  /** Provider-specific controls kept losslessly by the selected model contract. */
  parameters?: Record<string, unknown>;
}

export async function submitFreezoneVideoEdit(
  project: string,
  payload: FreezoneVideoEditPayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/video/video-edit`,
    {
      method: "POST",
      json: {
        video_url: payload.videoUrl,
        image_urls: payload.imageUrls ?? [],
        ...videoSubmissionBody(payload, false),
        audio_setting: payload.audioSetting ?? "auto",
      },
    },
  );
}

// /freezone/video/omni-gen ------------------------------------------------ //

export type FreezoneVideoReferenceType = "image" | "video" | "audio";

export interface FreezoneVideoReferenceItem {
  type: FreezoneVideoReferenceType;
  url: string;
  role?: string;
  label?: string;
}

export interface FreezoneVideoOmniGenPayload extends FreezoneNodeContext {
  prompt: string;
  theme?: string;
  cameraTemplateId?: string | null;
  /** mixed image/video/audio references. backend caps: image≤9, video≤3, audio≤3, total≤12. */
  references?: FreezoneVideoReferenceItem[];
  marks?: FreezoneVideoMark[];
  aspectRatio?: FreezoneVideoAspectRatio | string;
  resolution?: FreezoneVideoResolution;
  durationSeconds?: number;
  generateAudio?: boolean;
  /** True only when the user explicitly changed the native-audio switch. */
  generateAudioExplicit?: boolean;
  dialogueText?: string;
  spokenDialogue?: string[];
  audioType?: "silence" | "narration" | "dialogue" | "";
  speaker?: string;
  nativeAudioStrategy?: "external" | "native";
  audioAssetRef?: string | null;
  /** default huimeng_seedance20_fast per backend default. */
  model?: string;
  /** Stable catalog/picker id, used only for node-history restoration. */
  modelId?: string;
  /** 生成模式（还原用）：textToVideo / imageToVideo / firstLastFrame / imageReference / allReference。 */
  genMode?: string;
  /** See {@link FreezoneVideoGenPayload.humanReview}. */
  humanReview?: boolean;
  sceneOptimize?: "anime" | "realistic" | null;
  /** Provider-specific controls kept losslessly by the selected model contract. */
  parameters?: Record<string, unknown>;
}

export async function submitFreezoneVideoOmniGen(
  project: string,
  payload: FreezoneVideoOmniGenPayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/video/omni-gen`,
    {
      method: "POST",
      json: {
        ...videoSubmissionBody(payload),
        theme: payload.theme ?? "",
        references: (payload.references ?? []).map((r) => ({
          type: r.type,
          url: r.url,
          role: r.role ?? "",
          label: r.label ?? "",
        })),
      },
    },
  );
}

export async function submitFreezoneGen(
  project: string,
  payload: FreezoneGenPayload,
): Promise<FreezoneJobRef> {
  const camera = payload.camera
    ? {
        camera_body: payload.camera.cameraBodyId ?? "",
        lens: payload.camera.lensId ?? "",
        focal_length_mm: payload.camera.focalLengthMm ?? 0,
        aperture: payload.camera.aperture ?? "",
      }
    : null;
  const style = payload.style?.templateId
    ? { template_id: payload.style.templateId }
    : null;
  // 后端只能按静态路径打开引用图：任何 `data:` base64 先上传换成上传 URL，
  // 其余 http(s)/static 原样保留（仅剥掉 `?v=` 缓存串）。统一在此兜底，
  // 避免各调用方漏做净化把 base64 塞进 reference_urls。
  const referenceUrls = await ensureBackendImageUrls(
    project,
    payload.referenceUrls,
  );
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/gen`,
    {
      method: "POST",
      json: {
        prompt: payload.prompt,
        ...(payload.aspectRatio?.trim() ? { aspect_ratio: payload.aspectRatio.trim() } : {}),
        ...(payload.imageSize ? { image_size: payload.imageSize } : {}),
        reference_urls: referenceUrls,
        camera,
        style,
        provider: payload.provider ?? null,
        model: payload.model ?? null,
        ...(payload.modelId ? { model_id: payload.modelId } : {}),
        ...(payload.genMode ? { gen_mode: payload.genMode } : {}),
        quality: payload.quality ?? null,
        ...(payload.advancedSettings && Object.keys(payload.advancedSettings).length > 0
          ? { advanced_settings: payload.advancedSettings }
          : {}),
        ...nodeContextBody(payload),
      },
    },
  );
}

// /freezone/video/erase --------------------------------------------------- //

/**
 * Per `FreezoneVideoEraseRequest` in openapi.json:
 * - `smart_subtitle`: backend auto-estimates the bottom subtitle band.
 * - `box`: front-end supplies a normalized box (0..1 against source frame).
 */
export type FreezoneVideoEraseMode = "smart_subtitle" | "box";

export interface FreezoneVideoEraseBox {
  /** Top-left x, normalized 0..1 against the source video frame. */
  x: number;
  /** Top-left y, normalized 0..1 against the source video frame. */
  y: number;
  /** Width, normalized (0, 1]. */
  width: number;
  /** Height, normalized (0, 1]. */
  height: number;
}

export interface FreezoneVideoErasePayload {
  sourceUrl: string;
  mode: FreezoneVideoEraseMode;
  /** Required when `mode === "box"`; ignored otherwise. */
  box?: FreezoneVideoEraseBox | null;
}

export async function submitFreezoneVideoErase(
  project: string,
  payload: FreezoneVideoErasePayload,
): Promise<FreezoneJobRef> {
  const body: Record<string, unknown> = {
    source_url: payload.sourceUrl,
    mode: payload.mode,
  };
  if (payload.mode === "box" && payload.box) {
    body.box_x = payload.box.x;
    body.box_y = payload.box.y;
    body.box_width = payload.box.width;
    body.box_height = payload.box.height;
  }
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/video/erase`,
    { method: "POST", json: body },
  );
}

// /freezone/video/compose ------------------------------------------------- //

export type FreezoneVideoComposeResolution = "720p" | "1080p";
export type FreezoneVideoComposeTrackKind = "video" | "audio";

export interface FreezoneVideoComposeItemPayload {
  itemId: string;
  sourceUrl: string;
  /** Position on the output timeline, in seconds. */
  timelineStart?: number;
  /** Trim start within source media, in seconds. */
  sourceStart?: number;
  /** Trim end within source media, in seconds. Must be > sourceStart. */
  sourceEnd: number;
  volume?: number;
  muted?: boolean;
  /**
   * Playback rate (变速). 1 = original speed. ⚠️ Only honored if the BE compose
   * endpoint implements it; otherwise the field is ignored (export stays 1×).
   */
  speed?: number;
}

export interface FreezoneVideoComposeTrackPayload {
  trackId: string;
  kind: FreezoneVideoComposeTrackKind;
  items: FreezoneVideoComposeItemPayload[];
}

export interface FreezoneVideoComposePayload {
  title?: string;
  canvasId?: string;
  resolution?: FreezoneVideoComposeResolution;
  fps?: number;
  backgroundColor?: string;
  keepOriginalAudio?: boolean;
  /**
   * 封面图 URL（已上传到后端的稳定地址）。后端支持时会把它挂成 MP4 的封面流
   * （attached_pic，不改正片时长）；不支持时忽略，前端缩略图仍用同一个 url。
   */
  coverUrl?: string | null;
  tracks: FreezoneVideoComposeTrackPayload[];
}

export async function submitFreezoneVideoCompose(
  project: string,
  payload: FreezoneVideoComposePayload,
): Promise<FreezoneJobRef> {
  const body: Record<string, unknown> = {
    title: payload.title ?? "",
    canvas_id: payload.canvasId ?? "",
    resolution: payload.resolution ?? "1080p",
    fps: payload.fps ?? 30,
    background_color: payload.backgroundColor ?? "#000000",
    keep_original_audio: payload.keepOriginalAudio ?? true,
    cover_url: payload.coverUrl ?? "",
    tracks: payload.tracks.map((track) => ({
      track_id: track.trackId,
      kind: track.kind,
      items: track.items.map((item) => ({
        item_id: item.itemId,
        source_url: item.sourceUrl,
        timeline_start: item.timelineStart ?? 0,
        source_start: item.sourceStart ?? 0,
        source_end: item.sourceEnd,
        volume: item.volume ?? 1,
        muted: item.muted ?? false,
        speed: item.speed ?? 1,
      })),
    })),
  };
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/video/compose`,
    { method: "POST", json: body },
  );
}

// /freezone/video/audio-separate ----------------------------------------- //

/**
 * Per `FreezoneAudioSeparateRequest` in openapi.json the only required field is
 * `source_url`. Backend returns two artifacts via the task SSE: the extracted
 * audio track and a silent (muted) version of the original video.
 */
export interface FreezoneAudioSeparatePayload {
  sourceUrl: string;
  /** 目标 beat;给定后分离出的音频会带上 beat_audio slot_target,可直接 commit。 */
  targetEpisode?: number;
  targetBeat?: number;
}

export async function submitFreezoneAudioSeparate(
  project: string,
  payload: FreezoneAudioSeparatePayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/video/audio-separate`,
    {
      method: "POST",
      json: {
        source_url: payload.sourceUrl,
        target_episode: payload.targetEpisode,
        target_beat: payload.targetBeat,
      },
    },
  );
}

/**
 * Result shape for `freezone_audio_separate` isn't typed in openapi.json — the
 * endpoint declares `{}` (free-form). The light-version backend ships two
 * artifacts (audio track + silent video); callers walk the tree to find the
 * two URLs by extension.
 */
export async function fetchFreezoneAudioSeparateResult(
  project: string,
  jobId: string,
): Promise<Record<string, unknown>> {
  return await apiCall<Record<string, unknown>>(
    `projects/${encodeURIComponent(project)}/freezone/jobs/freezone_audio_separate/${encodeURIComponent(jobId)}/result`,
  );
}

// /freezone/video/cut ----------------------------------------------------- //

/**
 * 一段要切出来的源区间。`index` 是调用方的行标识（分镜表的镜号），只用于把切好的
 * 文件对回是哪一行；落盘文件名按排序后的位置编号，跟这个 index 无关。
 */
export interface FreezoneVideoCutSegmentPayload {
  index: number;
  /** 源视频中的起始秒。 */
  start: number;
  /** 源视频中的结束秒，必须大于 start。 */
  end: number;
}

/** 切出来的一段（后端结果清单里的条目）。 */
export interface FreezoneVideoCutClip {
  index: number;
  start: number;
  end: number;
  url: string;
  size: number;
  /** 实测时长（秒）—— 来自产物本身，不是请求区间相减。 */
  duration_seconds: number;
}

export interface FreezoneVideoCutResultData {
  segments: FreezoneVideoCutClip[];
  segment_count: number;
  source_duration_seconds: number | null;
}

/**
 * 按时间码把一条源视频切成多段（逐镜切片段）。
 *
 * 纯 ffmpeg：切出来的是源片里那几秒的**原样副本**（分辨率、帧率与源一致），
 * 与 `submitFreezoneVideoEdit` 那种让模型重画一镜是两件事 —— 这条不打任何
 * 模型请求，也就不产生费用。
 */
export async function submitFreezoneVideoCut(
  project: string,
  payload: { sourceUrl: string; segments: FreezoneVideoCutSegmentPayload[] },
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/video/cut`,
    {
      method: "POST",
      json: {
        source_url: payload.sourceUrl,
        segments: payload.segments.map((segment) => ({
          index: segment.index,
          start: segment.start,
          end: segment.end,
        })),
      },
    },
  );
}

/** 切段结果：与音频分离同理，声明是 free-form，按约定形状读回来。 */
export async function fetchFreezoneVideoCutResult(
  project: string,
  jobId: string,
): Promise<FreezoneVideoCutResultData> {
  const raw = await apiCall<{ ok?: boolean; data?: FreezoneVideoCutResultData }>(
    `projects/${encodeURIComponent(project)}/freezone/jobs/freezone_video_cut/${encodeURIComponent(jobId)}/result`,
  );
  const data = raw?.data;
  return {
    segments: Array.isArray(data?.segments) ? data.segments : [],
    segment_count: typeof data?.segment_count === "number" ? data.segment_count : 0,
    source_duration_seconds:
      typeof data?.source_duration_seconds === "number"
        ? data.source_duration_seconds
        : null,
  };
}

// /freezone/image/reverse-prompt ----------------------------------------- //

/**
 * The model is an opaque direct-registry id.  The backend resolves the actual
 * endpoint and key, so this payload never contains provider credentials.
 */
export interface FreezoneReversePromptPayload extends FreezoneNodeContext {
  sourceUrl: string;
  model?: string;
}

export async function submitFreezoneReversePrompt(
  project: string,
  payload: FreezoneReversePromptPayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/image/reverse-prompt`,
    {
      method: "POST",
      json: {
        source_url: payload.sourceUrl,
        model: payload.model,
        ...nodeContextBody(payload),
      },
    },
  );
}

// /freezone/image/style-templates ---------------------------------------- //

export interface FreezoneStyleTemplate {
  id: string;
  label: string;
  /** Free-text English style description forwarded as part of the prompt. */
  style_prompt: string;
  author?: string;
  category?: string;
}

export async function listFreezoneStyleTemplates(
  project: string,
): Promise<FreezoneStyleTemplate[]> {
  return await apiCall<FreezoneStyleTemplate[]>(
    `projects/${encodeURIComponent(project)}/freezone/image/style-templates`,
  );
}

// /freezone/image/camera-options ----------------------------------------- //

export interface FreezoneCameraIdLabel {
  id: string;
  label: string;
}

export interface FreezoneCameraOptions {
  camera_bodies: FreezoneCameraIdLabel[];
  lenses: FreezoneCameraIdLabel[];
  focal_lengths_mm: number[];
  apertures: string[];
}

export async function fetchFreezoneCameraOptions(
  project: string,
): Promise<FreezoneCameraOptions> {
  return await apiCall<FreezoneCameraOptions>(
    `projects/${encodeURIComponent(project)}/freezone/image/camera-options`,
  );
}

// /freezone/image/models -------------------------------------------------- //

/**
 * One model-declared advanced parameter, shaped for the node parameter panel.
 * Mirrors `ExtraParamDefinition`; `slider` is folded into `number` here because
 * the shared panel renders sliders and steppers with the same control.
 */
export interface FreezoneAdvancedParamSchema {
  key: string;
  label: string;
  type: 'boolean' | 'enum' | 'number' | 'string';
  description?: string;
  defaultValue?: boolean | number | string;
  options?: Array<{ value: string; label: string }>;
  min?: number;
  max?: number;
  step?: number;
}

export interface FreezoneImageModelInfo {
  /** Stable picker id, e.g. `"huimeng/gpt-image-2"`. */
  id: string;
  /** Provider tab id (`huimeng` / `openrouter` / `openai`). */
  providerId: FreezoneProvider;
  /** Value sent to backend `model` field. */
  apiModel: string;
  /** Real upstream model id for presentation-only capability decisions. */
  upstreamModel?: string;
  /** Display label in the model chip. */
  label: string;
  /** Model-center default used for newly created or stale nodes. */
  isDefault?: boolean;
  /** Model-derived image modes, e.g. textToImage / imageToImage. */
  supportedModes?: string[];
  /** Exact aspect-ratio choices accepted by the configured image adapter. */
  aspectRatioOptions?: string[];
  /** Exact resolution choices accepted by the configured image adapter. */
  resolutionOptions?: string[];
  /** Quality values accepted by the configured image adapter. */
  qualityOptions?: string[];
  supportsCustomAspectRatio?: boolean;
  supportsCustomResolution?: boolean;
  capabilitySource?: 'profile' | 'upstream' | 'runtime' | 'unknown';
  /** Short capability sentence supplied by the backend. */
  useCase?: string;
  /** Backend-selected baseline for common model controls. */
  parameterDefaults?: {
    resolution?: string;
    aspectRatio?: string;
    strategy?: 'balanced';
  };
  /**
   * Advanced parameters declared by the selected model's capability contract
   * (e.g. Midjourney `stylize` / `weird` / `chaos` / `personalisation`). An
   * absent field means the catalog predates advanced parameters; an empty
   * array is an explicit declaration that this model has none.
   */
  advancedParamsSchema?: FreezoneAdvancedParamSchema[];
  /** Contract defaults for `advancedParamsSchema`, keyed by parameter. */
  advancedParamDefaults?: Record<string, unknown>;
  enabled?: boolean;
  disabled?: boolean;
  disabledReason?: string | null;
  channelLabel?: string;
}

// Provider inference for raw model strings the backend may return without
// metadata (e.g. just a flat string list). Order matters — first match wins.
const MODEL_PROVIDER_HINTS: Array<{
  match: (raw: string) => boolean;
  providerId: FreezoneProvider;
}> = [
  { match: (s) => s.toLowerCase().startsWith("direct/"), providerId: "direct" },
  { match: (s) => s.toLowerCase().startsWith("newapi_"), providerId: "newapi" },
  { match: (s) => s.toLowerCase().startsWith("huimeng"), providerId: "huimeng" },
  { match: (s) => s.toLowerCase().includes("/gemini"), providerId: "openrouter" },
  { match: (s) => s.toLowerCase().startsWith("google/"), providerId: "openrouter" },
  { match: (s) => s.toLowerCase().startsWith("anthropic/"), providerId: "openrouter" },
  { match: (s) => s.toLowerCase().startsWith("openrouter/"), providerId: "openrouter" },
  { match: (s) => s.toLowerCase().startsWith("gpt-image"), providerId: "openai" },
  { match: (s) => s.toLowerCase().startsWith("dall-e"), providerId: "openai" },
];

function inferProvider(raw: string): FreezoneProvider {
  for (const hint of MODEL_PROVIDER_HINTS) {
    if (hint.match(raw)) return hint.providerId;
  }
  return "huimeng";
}

function pickString(record: Record<string, unknown>, ...keys: string[]): string | null {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.length > 0) return value;
  }
  return null;
}

function pickNumber(record: Record<string, unknown>, ...keys: string[]): number | null {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string" && value.trim().length > 0) {
      const parsed = Number(value);
      if (Number.isFinite(parsed)) return parsed;
    }
  }
  return null;
}

function pickStringArray(record: Record<string, unknown>, ...keys: string[]): string[] {
  for (const key of keys) {
    const value = record[key];
    if (Array.isArray(value)) {
      return value.filter((item): item is string => typeof item === "string" && item.length > 0);
    }
  }
  return [];
}

function pickNumberArray(record: Record<string, unknown>, ...keys: string[]): number[] {
  for (const key of keys) {
    if (!Object.prototype.hasOwnProperty.call(record, key)) continue;
    const value = record[key];
    if (!Array.isArray(value)) return [];
    const result: number[] = [];
    for (const item of value) {
      const parsed = typeof item === "number" ? item : typeof item === "string" ? Number(item.trim()) : NaN;
      if (!Number.isFinite(parsed) || result.includes(parsed)) continue;
      result.push(parsed);
    }
    return result;
  }
  return [];
}

function isProviderResolutionLabel(value: string): value is FreezoneVideoResolution {
  const normalized = String(value || "").trim();
  // Resolution enums come from the verified upstream capability envelope.
  // Keep provider-specific labels (including spaces, casing, and non-pixel
  // names) intact while bounding control characters and payload size.
  return normalized.length > 0 && normalized.length <= 120 && !/[\u0000-\u001f\u007f]/.test(normalized);
}

function isKnownResolutionLabel(value: string): value is FreezoneVideoResolution {
  return /^(?:[1-9]\d{2,5}p(?:横|竖|\([^)]*\))?|[1-9]\d{0,2}k(?:横|竖|\([^)]*\))?|[1-9]\d{2,5}x[1-9]\d{2,5})$/i.test(value);
}

function hasField(record: Record<string, unknown>, ...keys: string[]): boolean {
  return keys.some((key) => Object.prototype.hasOwnProperty.call(record, key));
}

const ADVANCED_PARAM_TYPES = new Set(['boolean', 'enum', 'number', 'string']);

function coerceAdvancedParamSchema(entry: unknown): FreezoneAdvancedParamSchema | null {
  if (!entry || typeof entry !== 'object') return null;
  const record = entry as Record<string, unknown>;
  const key = pickString(record, 'key', 'name');
  if (!key) return null;
  const rawType = pickString(record, 'type') ?? 'string';
  // `slider` is the backend's name for a bounded numeric control; the shared
  // parameter panel renders it with the same number input.
  const type = rawType === 'slider' ? 'number' : rawType;
  if (!ADVANCED_PARAM_TYPES.has(type)) return null;
  const optionsRaw = record.options;
  const options = Array.isArray(optionsRaw)
    ? optionsRaw
        .map((item) => {
          if (typeof item === 'string') return { value: item, label: item };
          if (!item || typeof item !== 'object') return null;
          const option = item as Record<string, unknown>;
          const value = pickString(option, 'value');
          if (!value) return null;
          return { value, label: pickString(option, 'label') ?? value };
        })
        .filter((item): item is { value: string; label: string } => item !== null)
    : [];
  const defaultValue = record.defaultValue ?? record.default_value;
  const description = pickString(record, 'description');
  const min = pickNumber(record, 'min');
  const max = pickNumber(record, 'max');
  const step = pickNumber(record, 'step');
  return {
    key,
    label: pickString(record, 'label') ?? key,
    type: type as FreezoneAdvancedParamSchema['type'],
    ...(description ? { description } : {}),
    ...(typeof defaultValue === 'boolean' ||
    typeof defaultValue === 'number' ||
    typeof defaultValue === 'string'
      ? { defaultValue }
      : {}),
    ...(options.length > 0 ? { options } : {}),
    ...(min !== null ? { min } : {}),
    ...(max !== null ? { max } : {}),
    ...(step !== null ? { step } : {}),
  };
}

function coerceAdvancedParamsSchema(value: unknown): FreezoneAdvancedParamSchema[] {
  if (!Array.isArray(value)) return [];
  const result: FreezoneAdvancedParamSchema[] = [];
  for (const item of value) {
    const definition = coerceAdvancedParamSchema(item);
    if (definition) result.push(definition);
  }
  return result;
}

function coerceAdvancedParamDefaults(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
  const result: Record<string, unknown> = {};
  for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
    if (typeof item === 'boolean' || typeof item === 'number' || typeof item === 'string') {
      result[key] = item;
    }
  }
  return result;
}

function normalizeProviderId(raw: string | null): FreezoneProvider | null {
  if (!raw) return null;
  const lowered = raw.toLowerCase();
  if (
    lowered === "huimeng" ||
    lowered === "openrouter" ||
    lowered === "openai" ||
    lowered === "newapi" ||
    lowered === "direct"
  ) {
    return lowered;
  }
  return null;
}

function modelEntryFromObject(entry: Record<string, unknown>): FreezoneImageModelInfo | null {
  const apiModel = pickString(entry, "model", "apiModel", "api_model", "name");
  if (!apiModel) return null;
  const providerId =
    normalizeProviderId(pickString(entry, "providerId", "provider_id", "provider")) ??
    inferProvider(apiModel);
  const id = pickString(entry, "id") ?? `${providerId}/${apiModel}`;
  const label = pickString(entry, "label", "displayName", "display_name") ?? apiModel;
  const disabled = entry.disabled === true;
  const enabled = typeof entry.enabled === "boolean" ? entry.enabled : !disabled;
  const parameterDefaultsRaw = entry.parameterDefaults ?? entry.parameter_defaults;
  const parameterDefaults =
    parameterDefaultsRaw && typeof parameterDefaultsRaw === "object"
      ? (() => {
          const raw = parameterDefaultsRaw as Record<string, unknown>;
          const resolution = pickString(raw, "resolution");
          const aspectRatio = pickString(raw, "aspectRatio", "aspect_ratio");
          const strategy = pickString(raw, "strategy");
          return {
            ...(resolution ? { resolution } : {}),
            ...(aspectRatio ? { aspectRatio } : {}),
            ...(strategy === "balanced" ? { strategy: "balanced" as const } : {}),
          };
        })()
      : undefined;
  const aspectRatioOptions = pickStringArray(
    entry,
    "aspectRatioOptions",
    "aspect_ratio_options",
  );
  const resolutionOptions = pickStringArray(
    entry,
    "resolutionOptions",
    "resolution_options",
  );
  const qualityOptions = pickStringArray(entry, "qualityOptions", "quality_options");
  const capabilitySource = pickString(entry, "capabilitySource", "capability_source");
  const advancedParamsSchemaRaw = entry.advancedParamsSchema ?? entry.advanced_params_schema;
  const advancedParamsSchema = coerceAdvancedParamsSchema(advancedParamsSchemaRaw);
  const advancedParamDefaultsRaw = entry.advancedParamDefaults ?? entry.advanced_param_defaults;
  const advancedParamDefaults = coerceAdvancedParamDefaults(advancedParamDefaultsRaw);
  return {
    id,
    providerId,
    apiModel,
    upstreamModel: pickString(entry, "upstreamModel", "upstream_model") ?? undefined,
    label,
    isDefault: entry.isDefault === true || entry.is_default === true,
    ...(hasField(entry, "supportedModes", "supported_modes")
      ? { supportedModes: pickStringArray(entry, "supportedModes", "supported_modes") }
      : {}),
    ...(hasField(entry, "aspectRatioOptions", "aspect_ratio_options")
      ? { aspectRatioOptions }
      : {}),
    ...(hasField(entry, "resolutionOptions", "resolution_options")
      ? { resolutionOptions }
      : {}),
    ...(hasField(entry, "qualityOptions", "quality_options")
      ? { qualityOptions }
      : {}),
    ...(typeof entry.supportsCustomAspectRatio === "boolean"
      ? { supportsCustomAspectRatio: entry.supportsCustomAspectRatio }
      : typeof entry.supports_custom_aspect_ratio === "boolean"
        ? { supportsCustomAspectRatio: entry.supports_custom_aspect_ratio }
        : {}),
    ...(typeof entry.supportsCustomResolution === "boolean"
      ? { supportsCustomResolution: entry.supportsCustomResolution }
      : typeof entry.supports_custom_resolution === "boolean"
        ? { supportsCustomResolution: entry.supports_custom_resolution }
        : {}),
    ...(capabilitySource === "profile" || capabilitySource === "upstream" || capabilitySource === "runtime" || capabilitySource === "unknown"
      ? { capabilitySource }
      : {}),
    useCase: pickString(entry, "useCase", "use_case") ?? undefined,
    parameterDefaults,
    ...(hasField(entry, "advancedParamsSchema", "advanced_params_schema")
      ? { advancedParamsSchema }
      : {}),
    ...(Object.keys(advancedParamDefaults).length > 0 ? { advancedParamDefaults } : {}),
    enabled,
    disabled,
    disabledReason: pickString(entry, "disabledReason", "disabled_reason"),
    channelLabel: pickString(entry, "channelLabel", "channel_label") ?? undefined,
  };
}

function modelEntryFromString(raw: string): FreezoneImageModelInfo {
  const providerId = inferProvider(raw);
  return {
    id: `${providerId}/${raw}`,
    providerId,
    apiModel: raw,
    label: raw,
  };
}

export function coerceModelList(payload: unknown): FreezoneImageModelInfo[] {
  // Accept several shapes the backend might return — schema is empty in
  // openapi.json so we normalize defensively rather than guess one shape.
  let candidate: unknown = payload;
  if (candidate && typeof candidate === "object" && !Array.isArray(candidate)) {
    const wrapper = candidate as Record<string, unknown>;
    if (Array.isArray(wrapper.models)) candidate = wrapper.models;
    else if (Array.isArray(wrapper.data)) candidate = wrapper.data;
    else if (Array.isArray(wrapper.items)) candidate = wrapper.items;
    else {
      // provider→models[] map: { huimeng: [...], openrouter: [...] }
      const flattened: FreezoneImageModelInfo[] = [];
      for (const [providerRaw, value] of Object.entries(wrapper)) {
        const providerId = normalizeProviderId(providerRaw);
        if (!providerId || !Array.isArray(value)) continue;
        for (const item of value) {
          if (typeof item === "string") {
            flattened.push({
              id: `${providerId}/${item}`,
              providerId,
              apiModel: item,
              label: item,
            });
          } else if (item && typeof item === "object") {
            const entry = modelEntryFromObject(item as Record<string, unknown>);
            if (entry) flattened.push({ ...entry, providerId });
          }
        }
      }
      if (flattened.length > 0) return flattened;
    }
  }

  if (!Array.isArray(candidate)) return [];
  const result: FreezoneImageModelInfo[] = [];
  for (const item of candidate) {
    if (typeof item === "string") {
      result.push(modelEntryFromString(item));
    } else if (item && typeof item === "object") {
      const entry = modelEntryFromObject(item as Record<string, unknown>);
      if (entry) result.push(entry);
    }
  }
  return result;
}

export async function fetchFreezoneImageModels(
  project: string,
): Promise<FreezoneImageModelInfo[]> {
  const payload = await apiCall<unknown>(
    `projects/${encodeURIComponent(project)}/freezone/image/models`,
    // Model capabilities are runtime state. Do not let a browser or an
    // intermediary reuse a pre-restart catalog and hide newly detected
    // dimensions from the canvas controls.
    { cache: "no-store" },
  );
  return coerceModelList(payload);
}

/** Legacy browser-only image configuration migrated into the durable registry. */
export interface DirectImageModelRegistration {
  id?: string;
  label: string;
  modelId: string;
  baseUrl: string;
  apiKey: string;
  enabled: boolean;
  isDefault?: boolean;
}

export async function syncFreezoneDirectImageModels(
  models: DirectImageModelRegistration[],
): Promise<void> {
  await apiCall<unknown>("model-gateway/direct-models/image", {
    method: "POST",
    json: { models },
  });
}

// /freezone/video/models -------------------------------------------------- //

/** Provider tab id for video generation models. */
export type FreezoneVideoProvider = "newapi" | "seedance" | "huimeng" | "direct";
export interface FreezoneVideoModelInfo extends FreezoneVideoModelFields {
  /** Stable picker id, e.g. `"seedance_2"` (backend currently keys by api id). */
  id: string;
  /** Provider tab id (`seedance` / `huimeng`). */
  providerId: FreezoneVideoProvider;
  /** Value sent to backend `/freezone/video/gen` `model` field. */
  apiModel: string;
  /** Display label in the model chip. */
  label: string;
  /** Complete upstream parameter declarations; common controls are projected separately below. */
  parameters?: FreezoneVideoCapabilityParameter[];
  /** Deterministic canonical-key -> provider-key mapping used by the adapter. */
  providerMapping?: Record<string, string>;
  provider_mapping?: Record<string, string>;
  mapping?: Record<string, string>;
  /** Inputs and provider fields not yet rendered as a dedicated control. */
  mediaInputs?: Array<Record<string, unknown>>;
  media_inputs?: Array<Record<string, unknown>>;
  /** Meaning of accepted audio references; execution uses this to choose the right asset. */
  audioInputSemantics?: Array<
    "driving_audio" | "voice_profile" | "soundtrack" | "audio_prompt"
  >;
  audio_input_semantics?: Array<
    "driving_audio" | "voice_profile" | "soundtrack" | "audio_prompt"
  >;
  /** AutoDL/ComfyUI workflow evidence used to explain exact node mappings. */
  workflowId?: string;
  workflowName?: string;
  workflowInputRules?: Array<Record<string, unknown>>;
  resolutionMappings?: Array<Record<string, unknown>>;
  workflowDiscovery?: Record<string, unknown>;
  opaque?: FreezoneVideoCapabilityOpaqueField[];
  capabilityEnvelopeVersion?: number;
  /** Supported output resolution values for this model, when advertised by backend. */
  resolutionOptions?: FreezoneVideoResolution[];
  /** Catalog claim retained for diagnostics when runtime verification narrows it. */
  advertisedResolutionOptions?: FreezoneVideoResolution[];
  /** Effective resolutions accepted by this exact endpoint/model pair. */
  runtimeResolutionOptions?: FreezoneVideoResolution[];
  /** Explicit upstream contract allows values outside the suggested list. */
  supportsCustomResolution?: boolean;
  supportsCustomAspectRatio?: boolean;
  /** Deterministic endpoint rejections observed during generation/probing. */
  runtimeRejectedResolutionOptions?: FreezoneVideoResolution[];
  runtimeCapabilityStatus?: "verified" | "degraded" | "unknown";
  runtimeCapabilityNote?: string;
  verificationStage?: "unknown" | "catalog" | "contract" | "submit" | "poll" | "artifact";
  promptRules?: {
    durationConsistency?: boolean;
    referenceConsistency?: boolean;
  };
  failureGracePolls?: number;
  /** Fixed pixel size slots (e.g. Gemini/Veo "992x432") when the model uses literal sizes. */
  sizeOptions?: string[];
  /** Only aspect ratios accepted by the selected upstream model. */
  aspectRatioOptions?: FreezoneVideoAspectRatio[];
  /** Native audio generation contract advertised by the backend. */
  nativeAudio?: "unsupported" | "optional" | "required";
  /** Smallest supported duration in seconds, when advertised by backend. */
  minDuration?: number | null;
  /** Largest supported duration in seconds, when advertised by backend. */
  maxDuration?: number | null;
  /** Discrete duration values accepted by the selected upstream model. */
  durationOptions?: number[];
  /** Discrete frame-rate values accepted by the selected upstream model. */
  fpsOptions?: number[];
  /** Provider-native input slot names for reference media. */
  inputSlots?: string[];
  /** Whether the provider can return a generated tail frame. */
  returnLastFrame?: boolean;
  /** Whether arbitrary duration values are accepted beyond durationOptions. */
  supportsCustomDuration?: boolean;
  /** Explicit transport gate for the duration field. */
  durationParameterEnabled?: boolean;
  /** Supported Seedance 2.0 Value style hints, when advertised by backend. */
  sceneOptimizeOptions?: Array<"anime" | "realistic">;
  /** Default Seedance 2.0 Value style hint, when advertised by backend. */
  defaultSceneOptimize?: "anime" | "realistic" | null;
  /** Backend-selected balanced baseline for a freshly selected model. */
  parameterDefaults?: {
    resolution?: FreezoneVideoResolution;
    durationSeconds?: number;
    aspectRatio?: string;
    generateAudio?: boolean;
    strategy?: "balanced";
  };
  /** Model-center default for newly created video nodes. */
  isDefault?: boolean;
  /** Backend-authored capability family; UI falls back to id inference only for legacy servers. */
  family?: "happyhorse" | "firefly-seedance2" | "grok-video-channel" | "seedance-1x" | "seedance-2" | "seedance-2-value" | "wokey-jimeng" | "kacang-933" | "kacang-mini-h3" | "kacang-kling-v2v" | "prompt-hubs-sd" | "kling-3.0" | "prompt-hubs-flex" | "direct" | "generic";
  supportedModes?: string[];
  referenceLimits?: Record<
    string,
    { image: number; video: number; audio: number; total?: number }
  >;
  supportsHumanReview?: boolean;
  /** Actual route selected by the backend, e.g. `Wokey · 即梦`. */
  channelLabel?: string;
  /** Concise production role shown under the model name. */
  useCase?: string;
  /** Last verified public-price hint or provider-billing hint. */
  priceHint?: string;
  /** UI-only recommendation level. It does not affect routing. */
  recommendation?: "default" | "recommended" | "specialist" | "premium" | "available";
  /** Higher values are displayed first inside the video model picker. */
  sortRank?: number;
  /**
   * Whether this model can start generation right now.
   * When the Freezone video channel is offline, backend returns enabled=false
   * with a disabledReason so the picker does not pretend generation works.
   */
  enabled?: boolean;
  /** True when the option must be greyed out (channel offline or model banned). */
  disabled?: boolean;
  /** Human-readable reason shown as tooltip when disabled. */
  disabledReason?: string | null;
}

/** Channel-level status from GET /freezone/video/models (optional envelope fields). */
export interface FreezoneVideoChannelStatus {
  enabled: boolean;
  generationEnabled: boolean;
  disabledReason: string;
}

const VIDEO_CHANNEL_OFFLINE_REASON =
  "视频渠道未接通：请在设置中至少启用一个直连视频模型后再生成。";

// Provider inference for raw model ids the backend may return without
// metadata. Order matters — first match wins. Anything we don't recognize
// falls back to `seedance` (the primary provider).
const VIDEO_MODEL_PROVIDER_HINTS: Array<{
  match: (raw: string) => boolean;
  providerId: FreezoneVideoProvider;
}> = [
  { match: (s) => s.toLowerCase().startsWith("direct_"), providerId: "direct" },
  { match: (s) => s.toLowerCase().startsWith("newapi"), providerId: "newapi" },
  { match: (s) => s.toLowerCase().startsWith("huimeng"), providerId: "huimeng" },
  { match: (s) => s.toLowerCase().startsWith("seedance"), providerId: "seedance" },
];

function inferVideoProvider(raw: string): FreezoneVideoProvider {
  for (const hint of VIDEO_MODEL_PROVIDER_HINTS) {
    if (hint.match(raw)) return hint.providerId;
  }
  return "newapi";
}

function normalizeVideoProviderId(raw: string | null): FreezoneVideoProvider | null {
  if (!raw) return null;
  const lowered = raw.toLowerCase();
  if (lowered === "newapi" || lowered === "seedance" || lowered === "huimeng" || lowered === "direct") return lowered;
  return null;
}

function pickObjectArray(
  entry: Record<string, unknown>,
  ...keys: string[]
): Array<Record<string, unknown>> {
  for (const key of keys) {
    const raw = entry[key];
    const candidates = Array.isArray(raw)
      ? raw
      : raw && typeof raw === "object" && !Array.isArray(raw) &&
          Array.isArray((raw as Record<string, unknown>).options)
        ? ((raw as Record<string, unknown>).options as unknown[])
        : null;
    if (!candidates) continue;
    return candidates.filter(
      (item): item is Record<string, unknown> =>
        Boolean(item && typeof item === "object" && !Array.isArray(item)),
    ).map((item) => ({ ...item }));
  }
  return [];
}

function videoModelEntryFromObject(
  entry: Record<string, unknown>,
): FreezoneVideoModelInfo | null {
  const apiModel = pickString(entry, "model", "apiModel", "api_model", "name");
  if (!apiModel) return null;
  const providerId =
    normalizeVideoProviderId(pickString(entry, "providerId", "provider_id", "provider")) ??
    inferVideoProvider(apiModel);
  const id = pickString(entry, "id") ?? apiModel;
  const label = pickString(entry, "label", "displayName", "display_name") ?? apiModel;
  const parametersRaw = entry.parameters ?? entry.parameter_schema ?? entry.parameterSchema;
  const parameters = Array.isArray(parametersRaw)
    ? parametersRaw.filter((item): item is FreezoneVideoCapabilityParameter =>
        Boolean(item && typeof item === "object" && typeof (item as Record<string, unknown>).key === "string"),
      )
    : parametersRaw && typeof parametersRaw === "object"
      ? Object.entries(parametersRaw as Record<string, unknown>).flatMap(([key, value]) => {
          const definition = value && typeof value === "object" && !Array.isArray(value)
            ? (value as Record<string, unknown>)
            : { default: value };
          return [{ key, ...definition } as FreezoneVideoCapabilityParameter];
        })
      : [];
  const providerMappingRaw = entry.providerMapping ?? entry.provider_mapping ?? entry.mapping;
  const providerMapping = providerMappingRaw && typeof providerMappingRaw === "object" && !Array.isArray(providerMappingRaw)
    ? Object.fromEntries(
        Object.entries(providerMappingRaw as Record<string, unknown>)
          .filter(([, value]) => typeof value === "string" && value.trim().length > 0)
          .map(([key, value]) => [key, String(value)]),
      )
    : {};
  const opaqueRaw = entry.opaque;
  const opaque = Array.isArray(opaqueRaw)
    ? opaqueRaw.filter((item): item is FreezoneVideoCapabilityOpaqueField =>
        Boolean(item && typeof item === "object" && typeof (item as Record<string, unknown>).key === "string"),
      )
      : [];
  const mediaInputsRaw = entry.mediaInputs ?? entry.media_inputs;
  const mediaInputs = Array.isArray(mediaInputsRaw)
    ? mediaInputsRaw
        .filter(
          (item): item is Record<string, unknown> =>
            Boolean(item && typeof item === "object" && !Array.isArray(item)),
        )
        .map((item) => ({ ...item }))
    : [];
  const audioInputSemanticsPresent = hasField(
    entry,
    "audioInputSemantics",
    "audio_input_semantics",
  );
  const audioInputSemantics = [
    ...new Set(
      pickStringArray(
        entry,
        "audioInputSemantics",
        "audio_input_semantics",
      )
        .map((value) => value.trim().toLowerCase().replace(/-/g, "_"))
        .filter(
          (
            value,
          ): value is
            | "driving_audio"
            | "voice_profile"
            | "soundtrack"
            | "audio_prompt" =>
            value === "driving_audio" ||
            value === "voice_profile" ||
            value === "soundtrack" ||
            value === "audio_prompt",
        ),
    ),
  ];
  const workflowInputRulesPresent = hasField(entry, "workflowInputRules", "workflow_input_rules");
  const workflowInputRules = pickObjectArray(entry, "workflowInputRules", "workflow_input_rules");
  const resolutionMappingsPresent = hasField(entry, "resolutionMappings", "resolution_mappings");
  const resolutionMappings = pickObjectArray(entry, "resolutionMappings", "resolution_mappings");
  const workflowId = pickString(entry, "workflowId", "workflow_id");
  const workflowName = pickString(entry, "workflowName", "workflow_name");
  const workflowDiscoveryRaw = entry.workflowDiscovery ?? entry.workflow_discovery;
  const workflowDiscovery = workflowDiscoveryRaw && typeof workflowDiscoveryRaw === "object" && !Array.isArray(workflowDiscoveryRaw)
    ? { ...(workflowDiscoveryRaw as Record<string, unknown>) }
    : undefined;
  const hasWorkflowSchema = workflowInputRulesPresent || resolutionMappingsPresent || (
    workflowDiscovery && typeof workflowDiscovery === "object" &&
    (String((workflowDiscovery as Record<string, unknown>).status ?? "").toLowerCase() === "discovered" ||
      String((workflowDiscovery as Record<string, unknown>).source ?? "").toLowerCase() === "workflow_schema")
  );
  const resolutionLabelFilter = hasWorkflowSchema ? isProviderResolutionLabel : isKnownResolutionLabel;
  const resolutionOptions = pickStringArray(entry, "resolutionOptions", "resolution_options")
    .map((value) => value.trim())
    .filter(resolutionLabelFilter);
  const sizeOptions = pickStringArray(entry, "sizeOptions", "size_options")
    .map((value) => value.toLowerCase())
    .filter((value) => /^\d+x\d+$/.test(value));
  const aspectRatioOptions = pickStringArray(entry, "aspectRatioOptions", "aspect_ratio_options")
    .filter((value): value is FreezoneVideoAspectRatio => {
      const match = /^(\d{1,6}(?:\.\d{1,4})?):(\d{1,6}(?:\.\d{1,4})?)$/.exec(value.trim());
      if (!match) return false;
      const width = Number(match[1]);
      const height = Number(match[2]);
      return Number.isFinite(width) && Number.isFinite(height) && width > 0 && height > 0;
    });
  const advertisedResolutionOptions = pickStringArray(
    entry,
    "advertisedResolutionOptions",
    "advertised_resolution_options",
  )
    .map((value) => value.trim())
    .filter(resolutionLabelFilter);
  const runtimeResolutionOptions = pickStringArray(
    entry,
    "runtimeResolutionOptions",
    "runtime_resolution_options",
  )
    .map((value) => value.trim())
    .filter(resolutionLabelFilter);
  const runtimeRejectedResolutionOptions = pickStringArray(
    entry,
    "runtimeRejectedResolutionOptions",
    "runtime_rejected_resolution_options",
  )
    .map((value) => value.trim())
    .filter(resolutionLabelFilter);
  const runtimeCapabilityStatusRaw = pickString(entry, "runtimeCapabilityStatus", "runtime_capability_status")?.toLowerCase();
  const supportsCustomResolution = typeof entry.supportsCustomResolution === "boolean"
    ? entry.supportsCustomResolution
    : typeof entry.supports_custom_resolution === "boolean"
      ? entry.supports_custom_resolution
      : undefined;
  const supportsCustomAspectRatio = typeof entry.supportsCustomAspectRatio === "boolean"
    ? entry.supportsCustomAspectRatio
    : typeof entry.supports_custom_aspect_ratio === "boolean"
      ? entry.supports_custom_aspect_ratio
      : undefined;
  const runtimeCapabilityStatus = runtimeCapabilityStatusRaw === "verified" || runtimeCapabilityStatusRaw === "degraded" || runtimeCapabilityStatusRaw === "unknown"
    ? runtimeCapabilityStatusRaw
    : undefined;
  const runtimeCapabilityNote = pickString(entry, "runtimeCapabilityNote", "runtime_capability_note");
  const verificationStageRaw = pickString(entry, "verificationStage", "verification_stage")?.toLowerCase();
  const verificationStage = ["unknown", "catalog", "contract", "submit", "poll", "artifact"].includes(verificationStageRaw ?? "")
    ? (verificationStageRaw as FreezoneVideoModelInfo["verificationStage"])
    : undefined;
  const promptRulesRaw = entry.promptRules ?? entry.prompt_rules;
  const promptRules = promptRulesRaw && typeof promptRulesRaw === "object" && !Array.isArray(promptRulesRaw)
    ? {
        ...(typeof (promptRulesRaw as Record<string, unknown>).durationConsistency === "boolean"
          ? { durationConsistency: (promptRulesRaw as Record<string, unknown>).durationConsistency as boolean }
          : {}),
        ...(typeof (promptRulesRaw as Record<string, unknown>).referenceConsistency === "boolean"
          ? { referenceConsistency: (promptRulesRaw as Record<string, unknown>).referenceConsistency as boolean }
          : {}),
      }
    : undefined;
  const failureGracePolls = pickNumber(entry, "failureGracePolls", "failure_grace_polls");
  const nativeAudioRaw = pickString(entry, "nativeAudio", "native_audio")?.toLowerCase();
  const nativeAudio = ["unsupported", "optional", "required"].includes(nativeAudioRaw ?? "")
    ? (nativeAudioRaw as FreezoneVideoModelInfo["nativeAudio"])
    : undefined;
  const sceneOptimizeOptions = pickStringArray(entry, "sceneOptimizeOptions", "scene_optimize_options")
    .map((value) => value.toLowerCase())
    .filter((value): value is "anime" | "realistic" =>
      value === "anime" || value === "realistic"
    );
  const defaultSceneOptimizeRaw = pickString(entry, "defaultSceneOptimize", "default_scene_optimize")
    ?.toLowerCase();
  const defaultSceneOptimize =
    defaultSceneOptimizeRaw === "anime" || defaultSceneOptimizeRaw === "realistic"
      ? defaultSceneOptimizeRaw
      : null;
  const parameterDefaultsRaw = entry.parameterDefaults ?? entry.parameter_defaults;
  const parameterDefaultsRecord =
    parameterDefaultsRaw && typeof parameterDefaultsRaw === "object" && !Array.isArray(parameterDefaultsRaw)
      ? (parameterDefaultsRaw as Record<string, unknown>)
      : null;
  const parameterResolution = parameterDefaultsRecord
    ? pickString(parameterDefaultsRecord, "resolution")?.toLowerCase()
    : null;
  const parameterDuration = parameterDefaultsRecord
    ? pickNumber(parameterDefaultsRecord, "durationSeconds", "duration_seconds")
    : null;
  const parameterAspectRatio = parameterDefaultsRecord
    ? pickString(parameterDefaultsRecord, "aspectRatio", "aspect_ratio")
    : null;
  const parameterGenerateAudio = parameterDefaultsRecord?.generateAudio ?? parameterDefaultsRecord?.generate_audio;
  const parameterStrategy = parameterDefaultsRecord ? pickString(parameterDefaultsRecord, "strategy") : null;
  const parameterDefaults = parameterDefaultsRecord
    ? {
        ...(parameterResolution
          ? { resolution: parameterResolution as FreezoneVideoResolution }
          : {}),
        ...(typeof parameterDuration === "number" && Number.isFinite(parameterDuration) && parameterDuration > 0
          ? { durationSeconds: Math.round(parameterDuration) }
          : {}),
        ...(parameterAspectRatio ? { aspectRatio: parameterAspectRatio } : {}),
        ...(typeof parameterGenerateAudio === "boolean" ? { generateAudio: parameterGenerateAudio } : {}),
        ...(parameterStrategy === "balanced" ? { strategy: "balanced" as const } : {}),
      }
      : undefined;
  const durationOptionsPresent = hasField(entry, "durationOptions", "duration_options");
  const durationOptions = pickNumberArray(entry, "durationOptions", "duration_options")
    .filter((value) => Number.isInteger(value) && value >= 1 && value <= 300)
    .map((value) => Math.round(value));
  const fpsOptionsPresent = hasField(entry, "fpsOptions", "fps_options");
  const fpsOptions = pickNumberArray(entry, "fpsOptions", "fps_options")
    .filter((value) => value >= 1 && value <= 240)
    .map((value) => Number.isInteger(value) ? value : Number(value.toFixed(3)));
  const inputSlotsPresent = hasField(entry, "inputSlots", "input_slots");
  const inputSlots = pickStringArray(entry, "inputSlots", "input_slots")
    .map((value) => value.trim())
    .filter(Boolean);
  const returnLastFrame = typeof entry.returnLastFrame === "boolean"
    ? entry.returnLastFrame
    : typeof entry.return_last_frame === "boolean"
      ? entry.return_last_frame
      : undefined;
  const supportsCustomDuration = typeof entry.supportsCustomDuration === "boolean"
    ? entry.supportsCustomDuration
    : typeof entry.supports_custom_duration === "boolean"
      ? entry.supports_custom_duration
      : undefined;
  const durationParameterEnabled = typeof entry.durationParameterEnabled === "boolean"
    ? entry.durationParameterEnabled
    : typeof entry.duration_parameter_enabled === "boolean"
      ? entry.duration_parameter_enabled
      : undefined;
  const familyRaw = pickString(entry, "family")?.toLowerCase();
  const family = ["happyhorse", "firefly-seedance2", "grok-video-channel", "seedance-1x", "seedance-2", "seedance-2-value", "wokey-jimeng", "kacang-933", "kacang-mini-h3", "kacang-kling-v2v", "prompt-hubs-sd", "kling-3.0", "prompt-hubs-flex", "direct", "generic"].includes(familyRaw ?? "")
    ? (familyRaw as FreezoneVideoModelInfo["family"])
    : undefined;
  const supportedModes = pickStringArray(entry, "supportedModes", "supported_modes");
  const referenceLimitsRaw = entry.referenceLimits ?? entry.reference_limits;
  const referenceLimits = referenceLimitsRaw && typeof referenceLimitsRaw === "object"
    ? (referenceLimitsRaw as FreezoneVideoModelInfo["referenceLimits"])
    : undefined;
  const disabledReason =
    pickString(entry, "disabledReason", "disabled_reason") ?? null;
  const channelLabel = pickString(entry, "channelLabel", "channel_label", "channel");
  const useCase = pickString(entry, "useCase", "use_case");
  const priceHint = pickString(entry, "priceHint", "price_hint");
  const recommendationRaw = pickString(entry, "recommendation");
  const recommendation = ["default", "recommended", "specialist", "premium", "available"].includes(
    recommendationRaw ?? "",
  )
    ? (recommendationRaw as FreezoneVideoModelInfo["recommendation"])
    : undefined;
  const sortRank = pickNumber(entry, "sortRank", "sort_rank");
  // Prefer explicit enabled/disabled flags; fall back to disabledReason presence.
  let enabled: boolean | undefined;
  if (typeof entry.enabled === "boolean") {
    enabled = entry.enabled;
  } else if (typeof entry.disabled === "boolean") {
    enabled = !entry.disabled;
  } else if (disabledReason) {
    enabled = false;
  }
  const disabled =
    typeof entry.disabled === "boolean"
      ? entry.disabled
      : enabled === false;
  const isDefault = entry.isDefault === true || entry.is_default === true;
  return {
    id,
    providerId,
    apiModel,
    label,
    ...(parameters.length > 0 ? { parameters } : hasField(entry, "parameters", "parameterSchema", "parameter_schema") ? { parameters: [] } : {}),
    ...(Object.keys(providerMapping).length > 0 || hasField(entry, "providerMapping", "provider_mapping", "mapping")
      ? { providerMapping, provider_mapping: providerMapping, mapping: providerMapping }
      : {}),
    ...(mediaInputs.length > 0 || hasField(entry, "mediaInputs", "media_inputs")
      ? { mediaInputs, media_inputs: mediaInputs }
      : {}),
    ...(audioInputSemanticsPresent
      ? {
          audioInputSemantics,
          audio_input_semantics: audioInputSemantics,
        }
      : {}),
    ...(workflowInputRulesPresent ? { workflowInputRules } : {}),
    ...(resolutionMappingsPresent ? { resolutionMappings } : {}),
    ...(workflowId ? { workflowId } : {}),
    ...(workflowName ? { workflowName } : {}),
    ...(workflowDiscovery ? { workflowDiscovery } : {}),
    ...(opaque.length > 0 || hasField(entry, "opaque") ? { opaque } : {}),
    ...(typeof entry.capabilityEnvelopeVersion === "number"
      ? { capabilityEnvelopeVersion: entry.capabilityEnvelopeVersion }
      : typeof entry.capability_envelope_version === "number"
        ? { capabilityEnvelopeVersion: entry.capability_envelope_version }
        : {}),
    ...freezoneVideoModelFields(entry),
    ...(hasField(entry, "resolutionOptions", "resolution_options")
      ? { resolutionOptions }
      : {}),
    ...(hasField(entry, "advertisedResolutionOptions", "advertised_resolution_options")
      ? { advertisedResolutionOptions }
      : {}),
    ...(hasField(entry, "runtimeResolutionOptions", "runtime_resolution_options")
      ? { runtimeResolutionOptions }
      : {}),
    ...(supportsCustomResolution !== undefined ? { supportsCustomResolution } : {}),
    ...(supportsCustomAspectRatio !== undefined ? { supportsCustomAspectRatio } : {}),
    ...(runtimeRejectedResolutionOptions.length > 0 ? { runtimeRejectedResolutionOptions } : {}),
    ...(runtimeCapabilityStatus ? { runtimeCapabilityStatus } : {}),
    ...(runtimeCapabilityNote ? { runtimeCapabilityNote } : {}),
    ...(verificationStage ? { verificationStage } : {}),
    ...(promptRules && Object.keys(promptRules).length > 0 ? { promptRules } : {}),
    ...(failureGracePolls !== null ? { failureGracePolls } : {}),
    ...(hasField(entry, "sizeOptions", "size_options") ? { sizeOptions } : {}),
    ...(hasField(entry, "aspectRatioOptions", "aspect_ratio_options")
      ? { aspectRatioOptions }
      : {}),
    ...(nativeAudio ? { nativeAudio } : {}),
    minDuration: pickNumber(entry, "minDuration", "min_duration"),
    maxDuration: pickNumber(entry, "maxDuration", "max_duration"),
    ...(durationOptionsPresent ? { durationOptions: [...new Set(durationOptions)].sort((a, b) => a - b) } : {}),
    ...(fpsOptionsPresent ? { fpsOptions: [...new Set(fpsOptions)].sort((a, b) => a - b) } : {}),
    ...(inputSlotsPresent ? { inputSlots } : {}),
    ...(returnLastFrame !== undefined ? { returnLastFrame } : {}),
    ...(supportsCustomDuration !== undefined ? { supportsCustomDuration } : {}),
    ...(durationParameterEnabled !== undefined ? { durationParameterEnabled } : {}),
    ...(sceneOptimizeOptions.length > 0 ? { sceneOptimizeOptions } : {}),
    defaultSceneOptimize,
    ...(parameterDefaults && Object.keys(parameterDefaults).length > 0 ? { parameterDefaults } : {}),
    ...(isDefault ? { isDefault: true } : {}),
    family,
    ...(hasField(entry, "supportedModes", "supported_modes")
      ? { supportedModes }
      : {}),
    referenceLimits,
    supportsHumanReview: entry.supportsHumanReview === true || entry.supports_human_review === true,
    ...(channelLabel ? { channelLabel } : {}),
    ...(useCase ? { useCase } : {}),
    ...(priceHint ? { priceHint } : {}),
    ...(recommendation ? { recommendation } : {}),
    ...(sortRank !== null ? { sortRank } : {}),
    ...(enabled !== undefined ? { enabled } : {}),
    ...(disabled ? { disabled: true } : enabled === false ? { disabled: true } : {}),
    ...(disabledReason ? { disabledReason } : disabled ? { disabledReason: VIDEO_CHANNEL_OFFLINE_REASON } : {}),
  };
}

function videoModelEntryFromString(raw: string): FreezoneVideoModelInfo {
  const providerId = inferVideoProvider(raw);
  return {
    id: raw,
    providerId,
    apiModel: raw,
    label: raw,
  };
}

export function coerceVideoModelList(payload: unknown): FreezoneVideoModelInfo[] {
  let candidate: unknown = payload;
  if (candidate && typeof candidate === "object" && !Array.isArray(candidate)) {
    const wrapper = candidate as Record<string, unknown>;
    if (Array.isArray(wrapper.models)) candidate = wrapper.models;
    else if (Array.isArray(wrapper.data)) candidate = wrapper.data;
    else if (Array.isArray(wrapper.items)) candidate = wrapper.items;
    else {
      // provider→models[] map: { seedance: [...], huimeng: [...] }
      const flattened: FreezoneVideoModelInfo[] = [];
      for (const [providerRaw, value] of Object.entries(wrapper)) {
        const providerId = normalizeVideoProviderId(providerRaw);
        if (!providerId || !Array.isArray(value)) continue;
        for (const item of value) {
          if (typeof item === "string") {
            flattened.push({
              id: item,
              providerId,
              apiModel: item,
              label: item,
            });
          } else if (item && typeof item === "object") {
            const entry = videoModelEntryFromObject(item as Record<string, unknown>);
            if (entry) flattened.push({ ...entry, providerId });
          }
        }
      }
      if (flattened.length > 0) return flattened;
    }
  }

  if (!Array.isArray(candidate)) return [];
  const result: FreezoneVideoModelInfo[] = [];
  for (const item of candidate) {
    if (typeof item === "string") {
      result.push(videoModelEntryFromString(item));
    } else if (item && typeof item === "object") {
      const entry = videoModelEntryFromObject(item as Record<string, unknown>);
      if (entry) result.push(entry);
    }
  }
  return result;
}

export interface FreezoneVideoModelsResponse {
  models: FreezoneVideoModelInfo[];
  channel: FreezoneVideoChannelStatus;
}

export function coerceVideoChannelStatus(payload: unknown): FreezoneVideoChannelStatus {
  // The channel envelope is optional for older backends, but an absent or
  // malformed envelope must never make a paid model look live.  Fail closed
  // until the server explicitly confirms that generation is enabled.
  const offline: FreezoneVideoChannelStatus = {
    enabled: false,
    generationEnabled: false,
    disabledReason: VIDEO_CHANNEL_OFFLINE_REASON,
  };
  if (!payload || typeof payload !== "object") {
    return offline;
  }
  const wrapper = Array.isArray(payload)
    ? null
    : (payload as Record<string, unknown>);
  const channelRaw =
    wrapper?.channel && typeof wrapper.channel === "object" && !Array.isArray(wrapper.channel)
      ? (wrapper.channel as Record<string, unknown>)
      : wrapper;
  const disabledReason =
    (channelRaw
      ? pickString(
          channelRaw,
          "disabledReason",
          "disabled_reason",
          "reason",
        )
      : null) ??
    (wrapper
      ? pickString(wrapper, "disabledReason", "disabled_reason", "reason")
      : null) ??
    "";
  // Treat every advertised boolean as a claim about the same channel.  A
  // contradictory response is unsafe, so any explicit `false` wins over
  // `true`; this also handles partially upgraded servers that emit both
  // camelCase and snake_case fields during a rolling deploy.
  const explicitFlags = [
    channelRaw?.enabled,
    channelRaw?.generation_enabled,
    channelRaw?.generationEnabled,
    ...(channelRaw === wrapper || !wrapper
      ? []
      : [wrapper.generation_enabled, wrapper.generationEnabled, wrapper.enabled]),
  ].filter((value): value is boolean => typeof value === "boolean");
  if (explicitFlags.some((value) => value === false)) {
    return {
      enabled: false,
      generationEnabled: false,
      disabledReason: disabledReason || VIDEO_CHANNEL_OFFLINE_REASON,
    };
  }
  if (explicitFlags.some((value) => value === true)) {
    return { enabled: true, generationEnabled: true, disabledReason: "" };
  }

  // apiCall unwraps the canonical `{ ok, data }` response, so current servers
  // may arrive here as the bare model array.  Prefer the channel-level flag
  // repeated on each model; it is authoritative and identical across rows.
  const candidate = Array.isArray(payload)
    ? payload
    : Array.isArray(wrapper?.models)
      ? wrapper.models
      : Array.isArray(wrapper?.data)
        ? wrapper.data
        : Array.isArray(wrapper?.items)
          ? wrapper.items
          : [];
  const modelEntries = candidate.filter(
    (value): value is Record<string, unknown> =>
      Boolean(value) && typeof value === "object" && !Array.isArray(value),
  );
  const modelChannelFlags = modelEntries
    .map((entry) =>
      typeof entry.channelEnabled === "boolean"
        ? entry.channelEnabled
        : typeof entry.channel_enabled === "boolean"
          ? entry.channel_enabled
          : null,
    )
    .filter((value): value is boolean => value !== null);
  // Disabled catalog rows describe those models, not the whole channel. One
  // explicitly live row proves that the shared direct-video channel works;
  // only an all-false catalog is globally offline.
  if (modelChannelFlags.some((value) => value === true)) {
    return { enabled: true, generationEnabled: true, disabledReason: "" };
  }
  if (modelChannelFlags.length > 0) {
    return {
      enabled: false,
      generationEnabled: false,
      disabledReason: disabledReason || VIDEO_CHANNEL_OFFLINE_REASON,
    };
  }

  // Legacy model rows may only expose per-model enabled/disabled flags.  One
  // live model proves the channel is usable; an all-disabled or flagless list
  // remains fail-closed so a stale catalog is never mistaken for connectivity.
  const modelEnabledFlags = modelEntries
    .map((entry) =>
      typeof entry.enabled === "boolean"
        ? entry.enabled
        : typeof entry.disabled === "boolean"
          ? !entry.disabled
          : null,
    )
    .filter((value): value is boolean => value !== null);
  if (modelEnabledFlags.some((value) => value === true)) {
    return { enabled: true, generationEnabled: true, disabledReason: "" };
  }
  return offline;
}

export async function fetchFreezoneVideoModels(
  project: string,
): Promise<FreezoneVideoModelInfo[]> {
  const response = await fetchFreezoneVideoModelsDetailed(project);
  return response.models;
}

/**
 * Full video-models payload including channel honesty flags.
 * Prefer this when the UI needs to grey out generation while the catalog still lists models.
 */
export async function fetchFreezoneVideoModelsDetailed(
  project: string,
): Promise<FreezoneVideoModelsResponse> {
  const payload = await apiCall<unknown>(
    `projects/${encodeURIComponent(project)}/freezone/video/models`,
  );
  const channel = coerceVideoChannelStatus(payload);
  let models = coerceVideoModelList(payload);
  if (!channel.enabled) {
    models = models.map((model) => {
      if (model.enabled === false || model.disabled) return model;
      return {
        ...model,
        enabled: false,
        disabled: true,
        disabledReason: model.disabledReason || channel.disabledReason || VIDEO_CHANNEL_OFFLINE_REASON,
        label: model.label.includes("未接通")
          ? model.label
          : `${model.label}（未接通）`,
      };
    });
  }
  return { models, channel };
}

export { VIDEO_CHANNEL_OFFLINE_REASON };

// /freezone/video/camera-templates --------------------------------------- //

export interface FreezoneVideoCameraTemplate {
  /** Stable id used by the picker + sent to backend as `camera_template_id`. */
  id: string;
  /** Display label (e.g. "镜头下降"). */
  label: string;
  /** Prompt fragment prepended to the user's prompt when this template is active. */
  promptFragment: string;
  /** Optional preview video URL. Falls back to `/video/camera-presets/<id>.mp4`. */
  videoUrl: string | null;
}

function coerceCameraTemplateList(payload: unknown): FreezoneVideoCameraTemplate[] {
  // openapi.json schema is empty `{}` — backend shape isn't documented.
  // Accept several common envelopes defensively.
  let candidate: unknown = payload;
  if (candidate && typeof candidate === "object" && !Array.isArray(candidate)) {
    const wrapper = candidate as Record<string, unknown>;
    if (Array.isArray(wrapper.templates)) candidate = wrapper.templates;
    else if (Array.isArray(wrapper.data)) candidate = wrapper.data;
    else if (Array.isArray(wrapper.items)) candidate = wrapper.items;
    else if (Array.isArray(wrapper.camera_templates)) candidate = wrapper.camera_templates;
  }
  if (!Array.isArray(candidate)) return [];
  const result: FreezoneVideoCameraTemplate[] = [];
  for (const item of candidate) {
    if (!item || typeof item !== "object") continue;
    const entry = item as Record<string, unknown>;
    const id = pickString(entry, "id", "template_id", "templateId", "name", "key");
    if (!id) continue;
    const label =
      pickString(entry, "label", "display_name", "displayName", "title", "name") ?? id;
    const promptFragment =
      pickString(
        entry,
        "promptFragment",
        "prompt_fragment",
        "prompt",
        "fragment",
        "description",
      ) ?? label;
    const videoUrl = pickString(
      entry,
      "videoUrl",
      "video_url",
      "previewUrl",
      "preview_url",
      "thumbnail",
      "thumbnail_url",
    );
    result.push({ id, label, promptFragment, videoUrl });
  }
  return result;
}

export async function fetchFreezoneVideoCameraTemplates(
  project: string,
): Promise<FreezoneVideoCameraTemplate[]> {
  const payload = await apiCall<unknown>(
    `projects/${encodeURIComponent(project)}/freezone/video/camera-templates`,
  );
  return coerceCameraTemplateList(payload);
}

// /freezone/edit ---------------------------------------------------------- //

export interface FreezoneEditPayload extends FreezoneNodeContext {
  prompt: string;
  baseUrl: string;
  extraReferenceUrls?: string[];
  aspectRatio?: string;
  imageSize?: string;
  provider?: FreezoneProvider | null;
  model?: string | null;
  /** 注册表模型 id（还原用；与 provider 拆分后的 model 串不同）。 */
  modelId?: string | null;
  /** 生成模式（还原用）：text_to_image / image_to_image / all_reference / image_reference。 */
  genMode?: string | null;
  quality?: string | null;
  /** 模型能力合同声明的高级参数；后端按模型合同过滤归一化。 */
  advancedSettings?: Record<string, unknown> | null;
}

export async function submitFreezoneEdit(
  project: string,
  payload: FreezoneEditPayload,
): Promise<FreezoneJobRef> {
  // 基准图与额外引用图同样必须是后端可解析的静态 URL，base64 先上传。
  const baseUrl = await ensureBackendImageUrl(project, payload.baseUrl);
  const extraReferenceUrls = await ensureBackendImageUrls(
    project,
    payload.extraReferenceUrls,
  );
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/edit`,
    {
      method: "POST",
      json: {
        prompt: payload.prompt,
        base_url: baseUrl,
        extra_reference_urls: extraReferenceUrls,
        ...(payload.aspectRatio?.trim() ? { aspect_ratio: payload.aspectRatio.trim() } : {}),
        ...(payload.imageSize ? { image_size: payload.imageSize } : {}),
        provider: payload.provider ?? null,
        model: payload.model ?? null,
        ...(payload.modelId ? { model_id: payload.modelId } : {}),
        ...(payload.genMode ? { gen_mode: payload.genMode } : {}),
        quality: payload.quality ?? null,
        ...(payload.advancedSettings && Object.keys(payload.advancedSettings).length > 0
          ? { advanced_settings: payload.advancedSettings }
          : {}),
        ...nodeContextBody(payload),
      },
    },
  );
}

// /freezone/sketch-from-context ------------------------------------------- //

export type FreezoneSketchContextSource =
  | "beat"
  | "selected_background"
  | "director_combined"
  | "background_candidate";

export interface FreezoneSketchFromContextPayload extends FreezoneNodeContext {
  episode: number;
  beat: number;
  sourceKind?: FreezoneSketchContextSource;
  sourceUrl?: string | null;
  provider?: FreezoneProvider | null;
  model?: string | null;
  quality?: string | null;
}

export async function submitFreezoneSketchFromContext(
  project: string,
  payload: FreezoneSketchFromContextPayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/sketch-from-context`,
    {
      method: "POST",
      json: {
        episode: payload.episode,
        beat: payload.beat,
        source_kind: payload.sourceKind ?? "beat",
        source_url: payload.sourceUrl ?? null,
        provider: payload.provider ?? null,
        model: payload.model ?? null,
        quality: payload.quality ?? null,
        ...nodeContextBody(payload),
      },
    },
  );
}

// /freezone/frame-from-context -------------------------------------------- //

export interface FreezoneFrameFromContextPayload extends FreezoneNodeContext {
  episode: number;
  beat: number;
  sketchUrl: string;
  backgroundUrl?: string | null;
  provider?: FreezoneProvider | null;
  model?: string | null;
  quality?: string | null;
}

export async function submitFreezoneFrameFromContext(
  project: string,
  payload: FreezoneFrameFromContextPayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/frame-from-context`,
    {
      method: "POST",
      json: {
        episode: payload.episode,
        beat: payload.beat,
        sketch_url: payload.sketchUrl,
        background_url: payload.backgroundUrl ?? null,
        provider: payload.provider ?? null,
        model: payload.model ?? null,
        quality: payload.quality ?? null,
        ...nodeContextBody(payload),
      },
    },
  );
}

// /freezone/image-to-3gs --------------------------------------------------- //

/** SHARP / 3GS 来源类型；master/reverse 单面 SOG，pano 用 360 全景生成。 */
export type FreezoneImageTo3GSKind = "master" | "reverse" | "pano";

export interface FreezoneImageTo3GSPayload extends FreezoneNodeContext {
  /** 源图静态地址，通常来自 Freezone 图片节点。 */
  sourceUrl: string;
  /** 默认 master；当前 3D 世界节点仅使用 master。 */
  sourceKind?: FreezoneImageTo3GSKind;
}

export async function submitFreezoneImageTo3GS(
  project: string,
  payload: FreezoneImageTo3GSPayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/image-to-3gs`,
    {
      method: "POST",
      json: {
        source_url: payload.sourceUrl,
        source_kind: payload.sourceKind ?? "master",
        ...nodeContextBody(payload),
      },
    },
  );
}

// /freezone/extract-frames ------------------------------------------------ //

export interface FreezoneExtractPayload {
  videoUrl: string;
  maxFrames?: number;
  sceneThreshold?: number;
}

export async function submitFreezoneExtract(
  project: string,
  payload: FreezoneExtractPayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/extract-frames`,
    {
      method: "POST",
      json: {
        video_url: payload.videoUrl,
        max_frames: payload.maxFrames ?? 20,
        scene_threshold: payload.sceneThreshold ?? 0.3,
      },
    },
  );
}

// /freezone/analyze-shots ------------------------------------------------- //

export interface FreezoneAnalyzePayload {
  frameUrls: string[];
  /** Kept for saved payload compatibility; direct is the current model-center path. */
  provider?: "direct" | "openrouter" | null;
  /** Direct model-center registry id override. */
  model?: string | null;
}

export async function submitFreezoneAnalyze(
  project: string,
  payload: FreezoneAnalyzePayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/analyze-shots`,
    {
      method: "POST",
      json: {
        frame_urls: payload.frameUrls,
        provider: payload.provider ?? null,
        model: payload.model ?? null,
      },
    },
  );
}

// /freezone/redraw -------------------------------------------------------- //

export type FreezoneRedrawAspectRatio =
  | "original"
  | "1:1"
  | "4:3"
  | "3:4"
  | "16:9"
  | "9:16";

export interface FreezoneRedrawPayload extends FreezoneNodeContext {
  sourceUrl: string;
  /** Optional mask static URL. Transparent pixels = editable region (局部重绘). */
  maskUrl?: string | null;
  prompt?: string;
  aspectRatio?: FreezoneRedrawAspectRatio;
  numImages?: number;
  imageSize?: string;
  model?: string;
}

export async function submitFreezoneRedraw(
  project: string,
  payload: FreezoneRedrawPayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/redraw`,
    {
      method: "POST",
      json: {
        source_url: payload.sourceUrl,
        mask_url: payload.maskUrl ?? null,
        prompt: payload.prompt ?? "",
        aspect_ratio: payload.aspectRatio ?? "original",
        num_images: payload.numImages ?? 1,
        ...(payload.imageSize ? { image_size: payload.imageSize } : {}),
        ...(payload.model ? { model: payload.model } : {}),
        ...nodeContextBody(payload),
      },
    },
  );
}

// /freezone/upscale ------------------------------------------------------- //

export type FreezoneUpscaleScaleFactor = 2 | 4 | 6;

export interface FreezoneUpscalePayload extends FreezoneNodeContext {
  sourceUrl: string;
  scaleFactor?: FreezoneUpscaleScaleFactor;
  imageSize?: string;
  model?: string;
}

export async function submitFreezoneUpscale(
  project: string,
  payload: FreezoneUpscalePayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/upscale`,
    {
      method: "POST",
      json: {
        source_url: payload.sourceUrl,
        scale_factor: payload.scaleFactor ?? 2,
        ...(payload.imageSize ? { image_size: payload.imageSize } : {}),
        ...(payload.model ? { model: payload.model } : {}),
        ...nodeContextBody(payload),
      },
    },
  );
}

// /freezone/outpaint ------------------------------------------------------ //

export type FreezoneOutpaintAspectRatio =
  | "original"
  | "1:1"
  | "4:3"
  | "3:4"
  | "16:9"
  | "9:16";

export interface FreezoneOutpaintPayload extends FreezoneNodeContext {
  sourceUrl: string;
  targetAspectRatio?: FreezoneOutpaintAspectRatio;
  numImages?: number;
  imageSize?: string;
  model?: string;
}

export async function submitFreezoneOutpaint(
  project: string,
  payload: FreezoneOutpaintPayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/outpaint`,
    {
      method: "POST",
      json: {
        source_url: payload.sourceUrl,
        target_aspect_ratio: payload.targetAspectRatio ?? "original",
        num_images: payload.numImages ?? 1,
        ...(payload.imageSize ? { image_size: payload.imageSize } : {}),
        ...(payload.model ? { model: payload.model } : {}),
        ...nodeContextBody(payload),
      },
    },
  );
}

// /freezone/multi-view ---------------------------------------------------- //

export type FreezoneMultiViewPreset =
  | "custom"
  | "fisheye"
  | "oblique"
  | "front"
  | "front_up"
  | "full_body"
  | "back";

export type FreezoneMultiViewShotSize =
  | "extreme_close_up"
  | "close_up"
  | "medium_close"
  | "medium"
  | "full_body"
  | "wide"
  | "extreme_wide";

export interface FreezoneMultiViewPayload extends FreezoneNodeContext {
  sourceUrl: string;
  preset?: FreezoneMultiViewPreset;
  yawDegrees?: number;
  pitchDegrees?: number;
  shotSize?: FreezoneMultiViewShotSize;
  prompt?: string;
  imageSize?: string;
  model?: string;
}

export async function submitFreezoneMultiView(
  project: string,
  payload: FreezoneMultiViewPayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/multi-view`,
    {
      method: "POST",
      json: {
        source_url: payload.sourceUrl,
        preset: payload.preset ?? "custom",
        yaw_degrees: payload.yawDegrees ?? 0,
        pitch_degrees: payload.pitchDegrees ?? 0,
        shot_size: payload.shotSize ?? "medium",
        prompt: payload.prompt ?? "",
        ...(payload.imageSize ? { image_size: payload.imageSize } : {}),
        ...(payload.model ? { model: payload.model } : {}),
        ...nodeContextBody(payload),
      },
    },
  );
}

// /freezone/relight ------------------------------------------------------- //

export type FreezoneRelightScope = "global" | "local";

export type FreezoneRelightKeyLightDirection =
  | "left"
  | "top"
  | "right"
  | "front"
  | "bottom"
  | "back";

export interface FreezoneRelightPayload extends FreezoneNodeContext {
  sourceUrl: string;
  lightingReferenceUrl?: string | null;
  scope?: FreezoneRelightScope;
  smartMode?: boolean;
  brightness?: number;
  colorHex?: string;
  /** 色温（开尔文）。后端 `color_temperature_kelvin: int | null`，范围 1500-10000。 */
  colorTemperatureKelvin?: number | null;
  keyLightDirection?: FreezoneRelightKeyLightDirection;
  rimLight?: boolean;
  prompt?: string;
  imageSize?: string;
  model?: string;
}

export async function submitFreezoneRelight(
  project: string,
  payload: FreezoneRelightPayload,
): Promise<FreezoneJobRef> {
  // 源图与光照参考图都要走静态 URL，base64 先上传。
  const sourceUrl = await ensureBackendImageUrl(project, payload.sourceUrl);
  const lightingReferenceUrl = payload.lightingReferenceUrl
    ? await ensureBackendImageUrl(project, payload.lightingReferenceUrl)
    : null;
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/relight`,
    {
      method: "POST",
      json: {
        source_url: sourceUrl,
        lighting_reference_url: lightingReferenceUrl,
        scope: payload.scope ?? "global",
        smart_mode: payload.smartMode ?? true,
        brightness: payload.brightness ?? 50,
        color_hex: payload.colorHex ?? "#ffffff",
        color_temperature_kelvin: payload.colorTemperatureKelvin ?? null,
        key_light_direction: payload.keyLightDirection ?? "front",
        rim_light: payload.rimLight ?? false,
        prompt: payload.prompt ?? "",
        ...(payload.imageSize ? { image_size: payload.imageSize } : {}),
        ...(payload.model ? { model: payload.model } : {}),
        ...nodeContextBody(payload),
      },
    },
  );
}

// /freezone/scene-360 ----------------------------------------------------- //

/** 全景输出比例。后端不传时按 "2:1" 处理；传其他值会被 Pydantic 校验拒绝。 */
export type FreezoneScene360AspectRatio = "2:1" | "21:9";

export const FREEZONE_SCENE_360_ASPECT_RATIOS: readonly FreezoneScene360AspectRatio[] =
  ["2:1", "21:9"];

export const DEFAULT_FREEZONE_SCENE_360_ASPECT_RATIO: FreezoneScene360AspectRatio =
  "2:1";

export interface FreezoneScene360Payload extends FreezoneNodeContext {
  referenceUrl: string;
  imageSize?: string;
  aspectRatio?: FreezoneScene360AspectRatio;
  model?: string;
  mode?: "candidate" | "commit";
}

/**
 * **单图 360 (simple pipeline)** — 老路径,只把一张图当 reference 走通用
 * 图编辑生成 panorama。不做 master/reverse overlap 分析、空间合约、缝合,
 * 适合 freezone 自由画布上 \"拿任一张图试试 360\" 的快速生成。
 * Asset-scoped scene 360 generation uses
 * {@link submitFreezoneScene360FromMaster} (复杂工作流),不是这条。
 */
export async function submitFreezoneScene360(
  project: string,
  payload: FreezoneScene360Payload,
): Promise<FreezoneJobRef> {
  // 参考图必须是后端可解析的静态 URL，base64 先上传。
  const referenceUrl = await ensureBackendImageUrl(project, payload.referenceUrl);
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/scene-360`,
    {
      method: "POST",
      json: {
        reference_url: referenceUrl,
        ...(payload.imageSize ? { image_size: payload.imageSize } : {}),
        mode: payload.mode ?? "candidate",
        aspect_ratio:
          payload.aspectRatio ?? DEFAULT_FREEZONE_SCENE_360_ASPECT_RATIO,
        // 前端不能选模型，不传 model 让后端用默认；调用方显式传了才带上。
        ...(payload.model ? { model: payload.model } : {}),
        ...nodeContextBody(payload),
      },
    },
  );
}

// /scenes/{name}/pano/generate-async --------------------------------------- //

export interface ScenePanoFromMasterPayload {
  sceneId: string;
  /** 后端会自动 fallback 到 text 如果 master 不存在;FE 默认传 'master'。 */
  source?: "master" | "text";
  style?: string;
  provider?: string;
  model?: string;
  imageSize?: string;
  quality?: string;
  timeoutSeconds?: number;
}

/**
 * **复杂场景 360 工作流 (complex pipeline)** — 走 stage_asset
 * `pano_from_master` step:
 *  - 读 scene 的 master + reverse_master 文件
 *  - 跑 overlap analyzer (master/reverse 边缘融合分析)
 *  - 跑 spatial contract analyzer (空间合约)
 *  - 调 village_scene_360_gpt_image2.py 生成 pano_360.png
 *  - 写到 stage_manifest canonical 路径 (跟资产画布 pano viewer 同一份文件)
 *
 * Asset-scoped scene 360 generation uses this complex path, not the simple
 * `/freezone/scene-360` endpoint.
 *
 * 注: 走的是 scenes 路由 (不是 freezone 路由),所以返回的是
 * `{ok, task_type:"stage_asset", task_key, scope, task_id, backend}` 结构 —
 * cast 成 FreezoneJobRef shape (task_key + job_id + task_type) 给上层用。
 */
export async function submitFreezoneScene360FromMaster(
  project: string,
  payload: ScenePanoFromMasterPayload,
): Promise<FreezoneJobRef> {
  const sceneId = payload.sceneId;
  if (!sceneId) throw new Error("submitFreezoneScene360FromMaster: missing sceneId");
  const result = await apiCall<{
    ok: boolean;
    task_type: string;
    task_key: string;
    task_id?: string;
    scope?: string;
    backend?: string;
    error?: string;
  }>(
    `projects/${encodeURIComponent(project)}/scenes/${encodeURIComponent(sceneId)}/pano/generate-async`,
    {
      method: "POST",
      json: {
        source: payload.source ?? "master",
        ...(payload.style ? { style: payload.style } : {}),
        ...(payload.provider ? { provider: payload.provider } : {}),
        ...(payload.model ? { model: payload.model } : {}),
        ...(payload.imageSize ? { image_size: payload.imageSize } : {}),
        ...(payload.quality ? { quality: payload.quality } : {}),
        ...(payload.timeoutSeconds ? { timeout_seconds: payload.timeoutSeconds } : {}),
      },
    },
  );
  if (!result.ok || result.error) {
    throw new Error(result.error ?? "scene 360 from master failed");
  }
  return {
    task_key: result.task_key,
    job_id: result.task_id ?? result.scope ?? sceneId,
    task_type: "stage_asset",
  } as FreezoneJobRef;
}

// /freezone/template-edit ------------------------------------------------- //

export type FreezoneTemplateEditMode =
  | "multi_camera_nine_grid"
  | "story_pitch_four_grid"
  | "character_face_three_view"
  | "product_three_view"
  | "storyboard_25_grid"
  | "cinematic_light_correction"
  | "character_three_view_generation"
  | "image_projection_after_3s"
  | "image_projection_before_5s";

export interface FreezoneTemplateEditPayload extends FreezoneNodeContext {
  sourceUrl: string;
  mode: FreezoneTemplateEditMode;
  prompt?: string;
  imageSize?: string;
  model?: string;
}

export async function submitFreezoneTemplateEdit(
  project: string,
  payload: FreezoneTemplateEditPayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/template-edit`,
    {
      method: "POST",
      json: {
        source_url: payload.sourceUrl,
        mode: payload.mode,
        prompt: payload.prompt ?? "",
        ...(payload.imageSize ? { image_size: payload.imageSize } : {}),
        ...(payload.model ? { model: payload.model } : {}),
        ...nodeContextBody(payload),
      },
    },
  );
}

// /freezone/jobs/{type}/{id}/result --------------------------------------- //

export interface FreezoneJobResultRetryOptions {
  attempts?: number;
  delayMs?: number;
}

export async function fetchFreezoneJobResult(
  project: string,
  taskType:
    | "freezone_gen"
    | "freezone_edit"
    | "freezone_upscale"
    | "freezone_extract"
    | "freezone_analyze"
    | "freezone_multi_view"
    | "freezone_relight"
    | "freezone_scene_360"
    | "freezone_template_edit"
    | "freezone_outpaint"
    | "freezone_redraw"
    | "freezone_video_gen"
    | "freezone_video_omni_gen"
    | "freezone_video_i2v"
    | "freezone_video_erase"
    | "freezone_video_compose"
    | "freezone_video_cut"
    | "freezone_video_upscale"
    | "freezone_audio_separate"
    | "freezone_audio_speech"
    | "freezone_audio_eleven_music"
    | "freezone_image_reverse_prompt"
    | "freezone_text_translate"
    | "freezone_prompt_optimize"
    | "freezone_story_script"
    | "freezone_video_story"
    | "stage_asset",
  jobId: string,
  options: { signal?: AbortSignal } = {},
): Promise<FreezoneJobResult> {
  const path = `projects/${encodeURIComponent(project)}/freezone/jobs/${encodeURIComponent(taskType)}/${encodeURIComponent(jobId)}/result`;
  return options.signal
    ? await apiCall<FreezoneJobResult>(path, { signal: options.signal })
    : await apiCall<FreezoneJobResult>(path);
}

export async function fetchFreezoneJobResultWithRetry(
  project: string,
  taskType: Parameters<typeof fetchFreezoneJobResult>[1],
  jobId: string,
  options: FreezoneJobResultRetryOptions = {},
): Promise<FreezoneJobResult> {
  const attempts = Math.max(1, Math.floor(options.attempts ?? 8));
  const delayMs = Math.max(0, Math.floor(options.delayMs ?? 1500));
  let lastError: unknown = new Error("job result not yet available");

  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      const result = await fetchFreezoneJobResult(project, taskType, jobId);
      if (typeof result?.url === "string" && result.url.trim()) {
        return result;
      }
      lastError = new Error("job result completed without a media URL");
    } catch (error) {
      lastError = error;
    }

    if (attempt < attempts && delayMs > 0) {
      await new Promise<void>((resolve) => {
        globalThis.setTimeout(resolve, delayMs);
      });
    }
  }

  throw lastError;
}

/**
 * `freezone_image_reverse_prompt` results aren't files — the dedicated job
 * result endpoint returns `{ prompt: "..." }` directly. SSE `task.result` only
 * carries `{ output_format: "json" }`, so the text must be fetched here.
 */
export interface FreezoneReversePromptResult {
  prompt: string;
}

export async function fetchFreezoneReversePromptResult(
  project: string,
  jobId: string,
): Promise<FreezoneReversePromptResult> {
  return await apiCall<FreezoneReversePromptResult>(
    `projects/${encodeURIComponent(project)}/freezone/jobs/freezone_image_reverse_prompt/${encodeURIComponent(jobId)}/result`,
  );
}

// /freezone/audio/references + /freezone/audio/speech -------------------- //

/**
 * 后端定义的声线 scope。
 * - project_narrator: 项目解说人
 * - character_default: 角色默认声线
 * - character_age_group: 角色年龄段声线（需要 slot）
 * - identity: 身份自己的声线（需要 identity_id）
 * - identity_resolved: 按 身份→年龄段→角色默认 兜底解析后的实际声线
 */
export type FreezoneAudioVoiceRefScope =
  | "project_narrator"
  | "user_custom"
  | "character_default"
  | "character_age_group"
  | "identity"
  | "identity_resolved";

export interface FreezoneAudioVoiceRef {
  scope: FreezoneAudioVoiceRefScope;
  /** scope=character_* / identity* 时必填，匹配项目角色名。 */
  characterName?: string;
  /** scope=identity / identity_resolved 时必填。 */
  identityId?: string;
  /** scope=character_age_group 时必填：child/youth/middle/elder。 */
  slot?: string;
  /** scope=user_custom 时必填：来自 GET/POST /freezone/audio/voices。 */
  voiceId?: string;
  sha256?: string;
  revision?: number;
}

/**
 * `GET /freezone/audio/references` 返回的单条记录。openapi 没给 schema（{}），
 * 这里按后端 description 推测，UI 防御性读取——遇到陌生字段就忽略。
 */
export interface FreezoneAudioReferenceItem {
  scope: FreezoneAudioVoiceRefScope;
  /** scope=character_* 或 identity* 时有值。 */
  character_name?: string | null;
  /** scope=identity / identity_resolved 时有值。 */
  identity_id?: string | null;
  /** scope=character_age_group 时有值：child/youth/middle/elder。 */
  slot?: string | null;
  /** scope=user_custom 时有值：账号级音色 ID（来自 POST /freezone/audio/voices）。 */
  voice_id?: string | null;
  /** 展示名（后端给的，若没给前端兜底拼）。 */
  label?: string | null;
  /** 语言/腔调说明。 */
  language?: string | null;
  /** 性别（如果后端给）。 */
  gender?: string | null;
  /** 预览音频静态 URL（如果后端给）。 */
  preview_url?: string | null;
  /** 声线文件摘要，用于生成前后的身份一致性核验。 */
  sha256?: string | null;
  revision?: number | null;
  [key: string]: unknown;
}

export interface FreezoneAudioReferencesResult {
  /** 项目内可用的声线引用列表。 */
  available: FreezoneAudioReferenceItem[];
  [key: string]: unknown;
}

export async function fetchFreezoneAudioReferences(
  project: string,
): Promise<FreezoneAudioReferencesResult> {
  const payload = await apiCall<unknown>(
    `projects/${encodeURIComponent(project)}/freezone/audio/references`,
  );
  // 容错：后端可能直接返回数组、或 { available: [...] }，或 { items: [...] }。
  if (Array.isArray(payload)) {
    return { available: payload as FreezoneAudioReferenceItem[] };
  }
  if (payload && typeof payload === "object") {
    const wrapper = payload as Record<string, unknown>;
    if (Array.isArray(wrapper.available)) {
      return wrapper as unknown as FreezoneAudioReferencesResult;
    }
    if (Array.isArray(wrapper.items)) {
      return { available: wrapper.items as FreezoneAudioReferenceItem[] };
    }
    if (Array.isArray(wrapper.data)) {
      return { available: wrapper.data as FreezoneAudioReferenceItem[] };
    }
  }
  return { available: [] };
}

export interface FreezoneAudioSpeechPayload extends FreezoneNodeContext {
  /** 要合成的台词 / 旁白文本。 */
  text: string;
  /** 情绪提示词，留空则用项目解说风格。例："紧张、压低声音、带一点恐惧感"。 */
  emotionPrompt?: string;
  /** 声线引用；不传则用项目默认解说人。 */
  voiceRef?: FreezoneAudioVoiceRef | null;
  /** 直连音频模型目录 ID；留空让后端选默认模型。 */
  model?: string;
  /** 目标 beat;给定后生成的音频会带上 beat_audio slot_target,可直接 commit。 */
  targetEpisode?: number;
  targetBeat?: number;
}

export async function submitFreezoneAudioSpeech(
  project: string,
  payload: FreezoneAudioSpeechPayload,
): Promise<FreezoneJobRef> {
  const voiceRefBody = payload.voiceRef
    ? {
        scope: payload.voiceRef.scope,
        character_name: payload.voiceRef.characterName ?? "",
        identity_id: payload.voiceRef.identityId ?? "",
        slot: payload.voiceRef.slot ?? "",
        voice_id: payload.voiceRef.voiceId ?? "",
        sha256: payload.voiceRef.sha256 ?? "",
        revision: payload.voiceRef.revision ?? 0,
      }
    : null;
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/audio/speech`,
    {
      method: "POST",
      json: {
        text: payload.text,
        emotion_prompt: payload.emotionPrompt ?? "",
        voice_ref: voiceRefBody,
        model: payload.model,
        target_episode: payload.targetEpisode,
        target_beat: payload.targetBeat,
        ...nodeContextBody(payload),
      },
    },
  );
}

// /freezone/audio/eleven-music ------------------------------------------- //

/**
 * 文本生成音乐请求。模型目录 ID 来自统一直连音频注册表；为空时由后端解析
 * 当前默认直连音频模型，不使用内置模型名。
 */
export interface FreezoneAudioMusicPayload extends FreezoneNodeContext {
  /** 音乐描述 prompt（风格、乐器、氛围等）；映射到后端 `input`。 */
  prompt: string;
  /** 直连音频模型目录 ID；留空使用模型中心默认音频模型。 */
  model?: string;
  /** 生成长度（毫秒），范围 3000–600000，留空走后端默认 30000。 */
  musicLengthMs?: number;
  /** 是否强制纯音乐，留空走后端默认 true。 */
  forceInstrumental?: boolean;
  /** 是否严格遵守音乐段落时长策略，留空走后端默认 true。 */
  respectSectionsDurations?: boolean;
  /** 目标 beat;给定后生成的音频会带上 beat_audio slot_target,可直接 commit。 */
  targetEpisode?: number;
  targetBeat?: number;
}

/**
 * 文本生成音乐。返回异步任务句柄，结果用
 * fetchFreezoneJobResult('freezone_audio_eleven_music') 取。
 */
export async function submitFreezoneAudioMusic(
  project: string,
  payload: FreezoneAudioMusicPayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/audio/eleven-music`,
    {
      method: "POST",
      json: {
        input: payload.prompt,
        model: payload.model,
        music_length_ms: payload.musicLengthMs,
        force_instrumental: payload.forceInstrumental,
        respect_sections_durations: payload.respectSectionsDurations,
        target_episode: payload.targetEpisode,
        target_beat: payload.targetBeat,
        ...nodeContextBody(payload),
      },
    },
  );
}

// /freezone/audio/voices ------------------------------------------------- //
//
// 「我的音色」(scope=user_custom) 的列表统一通过 /freezone/audio/references
// 拿（混在 available[] 里，按 scope 过滤）。这里只保留 POST：上传一段音频克
// 隆出账号级音色。

/**
 * POST /freezone/audio/voices 的响应。openapi 是空对象，按推测字段定义。
 * 调用方一般只需要 `voice_id`，其它仅用于上传后即时展示。
 */
export interface FreezoneAudioVoiceItem {
  voice_id: string;
  name?: string | null;
  language?: string | null;
  gender?: string | null;
  preview_url?: string | null;
  [key: string]: unknown;
}

export interface CreateFreezoneAudioVoiceOptions {
  /** 同 uploadFreezoneImage：上传大文件时用 false 关闭 ky 默认 30s 超时。 */
  timeoutMs?: number | false;
}

export async function createFreezoneAudioVoice(
  project: string,
  file: File | Blob,
  name?: string,
  options?: CreateFreezoneAudioVoiceOptions,
): Promise<FreezoneAudioVoiceItem> {
  const fd = new FormData();
  fd.append(
    "file",
    file,
    file instanceof File ? file.name : "voice.wav",
  );
  if (name && name.trim()) fd.append("name", name.trim());
  const resp = await apiClient(
    `projects/${encodeURIComponent(project)}/freezone/audio/voices`,
    {
      method: "POST",
      body: fd,
      timeout: options?.timeoutMs ?? false,
    },
  ).json<{ ok: boolean; data?: FreezoneAudioVoiceItem; error?: string }>();
  if (!resp.ok || !resp.data) {
    throw new Error(resp.error ?? "voice upload failed");
  }
  return resp.data;
}

// /freezone/text/translate ------------------------------------------------ //

export type FreezoneTextTranslateNodeType =
  | "generic"
  | "image"
  | "video"
  | "audio"
  | "text";

export interface FreezoneTextTranslatePayload extends FreezoneNodeContext {
  text: string;
  /** Hints the translator at the calling node's tone. Defaults to "generic". */
  nodeType?: FreezoneTextTranslateNodeType;
  /** Direct text-model registry id; omitted uses the server-side default. */
  model?: string;
}

export async function submitFreezoneTextTranslate(
  project: string,
  payload: FreezoneTextTranslatePayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/text/translate`,
    {
      method: "POST",
      json: {
        text: payload.text,
        node_type: payload.nodeType ?? "generic",
        model: payload.model,
        ...nodeContextBody(payload),
      },
    },
  );
}

export interface FreezoneTextTranslateResult {
  translated_text: string;
  source_language: "zh" | "en";
  target_language: "zh" | "en";
  node_type: FreezoneTextTranslateNodeType;
}

export async function fetchFreezoneTextTranslateResult(
  project: string,
  jobId: string,
): Promise<FreezoneTextTranslateResult> {
  return await apiCall<FreezoneTextTranslateResult>(
    `projects/${encodeURIComponent(project)}/freezone/jobs/freezone_text_translate/${encodeURIComponent(jobId)}/result`,
  );
}

export interface FreezonePromptOptimizeReference {
  name: string;
  kind: "image" | "video" | "audio";
  order: number;
  role: string;
  url?: string;
  label?: string;
  visual_summary?: string;
}

export interface FreezonePromptOptimizePayload extends FreezoneNodeContext {
  text: string;
  nodeType: "image" | "video";
  targetModelId: string;
  targetApiModel?: string;
  targetModelLabel?: string;
  params?: Record<string, unknown>;
  references?: FreezonePromptOptimizeReference[];
  guidance?: string;
  researchMode?: "quick" | "standard" | "expert";
}

export interface FreezonePromptOptimizeResult {
  optimized_prompt: string;
  preserved_intent: string;
  changes: string[];
  applied_rules: string[];
  warnings: string[];
  output_language: "zh" | "en" | "mixed";
  optimizer_model: string;
  optimizer_fallback_used?: boolean;
  optimizer_failed_models?: Array<{ model: string; error: string }>;
  reference_vision_used?: Array<{ name: string; model: string }>;
  reference_vision_failed?: Array<{ name: string; error: string }>;
  target_model_profile: string;
  knowledge_profiles: string[];
  knowledge_sources: string[];
  knowledge_live_sources: string[];
  knowledge_retrieval_mode: "live-local-files+curated-profiles" | "curated-profiles-fallback";
  knowledge_profile_details: Array<{
    id: string;
    label: string;
    authority: "official-derived" | "local-practice" | "curated";
  }>;
  research_mode?: "quick" | "standard" | "expert";
  research_used?: boolean;
  research_status?: "not_requested" | "not_needed" | "completed" | "unavailable" | "degraded";
  research_skip_reason?: string;
  research_trigger_reasons?: string[];
  research_sources?: Array<{
    title?: string;
    url?: string;
    content?: string;
    query_role?: string;
    query_variant?: string;
  }>;
  research_assessment?: Record<string, unknown>;
  critic_passed?: boolean;
  critic_issues?: string[];
  critic_repairs?: string[];
  director_recipe_ids?: string[];
  director_recipe_receipt?: Record<string, unknown>;
  model_capability_snapshot?: Record<string, unknown>;
  reference_manifest?: Record<string, unknown>;
  strategy_contract?: {
    schema?: "prompt_strategy_contract.v1";
    workflow?: string;
    strategy?: "image" | "narrative" | "control" | string;
    duration_seconds?: number | null;
    max_primary_actions?: number | null;
    max_temporal_beats?: number | null;
    max_camera_moves?: number | null;
    max_shot_transitions?: number | null;
    reference_semantics?: string;
    negative_prompt_policy?: "targeted" | "positive_only" | string;
    prompt_structure?: string[];
    rationale?: string;
    warnings?: string[];
  };
  revision: string;
}

export async function submitFreezonePromptOptimize(
  project: string,
  payload: FreezonePromptOptimizePayload,
): Promise<FreezoneJobRef> {
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/prompt/optimize`,
    {
      method: "POST",
      json: {
        text: payload.text,
        node_type: payload.nodeType,
        target_model_id: payload.targetModelId,
        target_api_model: payload.targetApiModel ?? "",
        target_model_label: payload.targetModelLabel ?? "",
        params: payload.params ?? {},
        references: payload.references ?? [],
        guidance: payload.guidance ?? "",
        research_mode: payload.researchMode ?? "standard",
        ...nodeContextBody(payload),
      },
    },
  );
}

export async function fetchFreezonePromptOptimizeResult(
  project: string,
  jobId: string,
): Promise<FreezonePromptOptimizeResult> {
  return await apiCall<FreezonePromptOptimizeResult>(
    `projects/${encodeURIComponent(project)}/freezone/jobs/freezone_prompt_optimize/${encodeURIComponent(jobId)}/result`,
  );
}

// /freezone/text/story-script -------------------------------------------- //

/** 角色参考输入项（角色生成脚本用）。 */
export interface FreezoneStoryScriptCharacterRef {
  /** 角色名或角色标签。 */
  name?: string;
  /** 可选：已有角色描述。 */
  description?: string;
  /** 角色参考图静态 URL（/static/...）。 */
  imageUrl?: string;
  /** 可选：角色定位或叙事功能（如「女主」）。 */
  role?: string;
}

export interface FreezoneStoryScriptPayload extends FreezoneNodeContext, ScriptShotRewritePayloadFields {
  /**
   * 文本输入：剧本 / 情节文本。后端要求 source_text / source_url / video_url /
   * character_refs 至少给一个。
   */
  sourceText?: string;
  /** 文本资源静态 URL，后端拉取后走生成流程。 */
  sourceUrl?: string;
  /** 视频输入：已上传视频的静态 URL，后端会先解析视频再生成同样的故事脚本表。 */
  videoUrl?: string;
  /** 视频总时长（秒），让脚本表的时间戳更准。 */
  durationSec?: number;
  /** 角色参考输入：无文本 / 视频时，基于角色图和描述生成故事脚本表。 */
  characterRefs?: FreezoneStoryScriptCharacterRef[];
  /** 用户额外补充的提示词（steering），例如风格、镜头偏好等。 */
  prompt?: string;
  /** Direct text-model registry id; blank uses the server-side default. */
  model?: string;
  /**
   * 视频输入的抽帧上限（3–50，后端默认 20）。只有走 `videoUrl` 时后端才会读它，
   * 但接口一直收这个字段 —— 以前前端两个字段都不发，带视频参考生成时永远 20 帧。
   * 口径与兄弟接口 `submitFreezoneExtractFrames` 一致。
   */
  maxFrames?: number;
  /** 视频抽帧的场景切换阈值（0–1，后端默认 0.3）。 */
  sceneThreshold?: number;
}

export async function submitFreezoneStoryScript(
  project: string,
  payload: FreezoneStoryScriptPayload,
): Promise<FreezoneJobRef> {
  const body: Record<string, unknown> = { ...nodeContextBody(payload) };
  if (payload.sourceText != null) body.source_text = payload.sourceText;
  if (payload.sourceUrl != null) body.source_url = payload.sourceUrl;
  if (payload.videoUrl != null) body.video_url = payload.videoUrl;
  if (payload.durationSec != null) body.duration_sec = payload.durationSec;
  if (payload.prompt != null) body.prompt = payload.prompt;
  if (payload.model != null) body.model = payload.model;
  Object.assign(body, storyScriptRewriteBody(payload));
  // 与 `submitFreezoneExtractFrames` 同一口径：显式带上默认值而不是靠后端兜底，
  // 这样带视频参考生成时的抽帧行为在请求里就是可见、可调的。
  body.max_frames = payload.maxFrames ?? 20;
  body.scene_threshold = payload.sceneThreshold ?? 0.3;
  if (payload.characterRefs && payload.characterRefs.length > 0) {
    body.character_refs = payload.characterRefs.map((ref) => {
      const item: Record<string, unknown> = {};
      if (ref.name != null) item.name = ref.name;
      if (ref.description != null) item.description = ref.description;
      if (ref.imageUrl != null) item.image_url = ref.imageUrl;
      if (ref.role != null) item.role = ref.role;
      return item;
    });
  }
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/text/story-script`,
    { method: "POST", json: body },
  );
}


export async function fetchFreezoneStoryScriptResult(
  project: string,
  jobId: string,
): Promise<FreezoneStoryScriptResult> {
  return await apiCall<FreezoneStoryScriptResult>(
    `projects/${encodeURIComponent(project)}/freezone/jobs/freezone_story_script/${encodeURIComponent(jobId)}/result`,
  );
}

export async function fetchFreezoneVideoStoryResult(
  project: string,
  jobId: string,
): Promise<Record<string, unknown>> {
  return await apiCall<Record<string, unknown>>(
    `projects/${encodeURIComponent(project)}/freezone/jobs/freezone_video_story/${encodeURIComponent(jobId)}/result`,
  );
}

// /freezone/upload (multipart) -------------------------------------------- //

export interface FreezoneUploadResult {
  url: string;
  filename: string;
  size: number;
}

export interface FreezoneUploadOptions {
  /**
   * Override the default ky timeout (30s). Pass `false` to disable —
   * required for multi-MB video uploads on slow links, otherwise ky aborts
   * the in-flight request and DevTools shows the row as `canceled`.
   */
  timeoutMs?: number | false;
}

export async function uploadFreezoneImage(
  project: string,
  file: File | Blob,
  filename?: string,
  options?: FreezoneUploadOptions,
): Promise<FreezoneUploadResult> {
  const fd = new FormData();
  fd.append("file", file, filename ?? (file instanceof File ? file.name : "upload.png"));
  // ky's `body: FormData` skips the json envelope helper, so we go direct.
  const resp = await apiClient(
    `projects/${encodeURIComponent(project)}/freezone/upload`,
    {
      method: "POST",
      body: fd,
      timeout: options?.timeoutMs ?? undefined,
    },
  ).json<{ ok: boolean; data?: FreezoneUploadResult; error?: string }>();
  if (!resp.ok || !resp.data) {
    throw new Error(resp.error ?? "upload failed");
  }
  return resp.data;
}

/** Same endpoint as image upload — backend treats the upload as a generic blob. */
export async function uploadFreezoneVideo(
  project: string,
  file: File | Blob,
  filename?: string,
): Promise<FreezoneUploadResult> {
  // Disable the 30s default timeout: video files routinely run into the tens
  // of MB and the upload streams the body, so a short timeout cancels the
  // request before the server ever sees the end of the body.
  return await uploadFreezoneImage(project, file, filename, { timeoutMs: false });
}

// /freezone/audio/trim ---------------------------------------------------- //

export interface FreezoneAudioTrimResult {
  url: string;
  filename: string;
  duration_ms: number;
  source_url: string;
}

/**
 * 把画布上的音频裁成一段新素材。后端用 ffmpeg 转成 MP3 写到上传目录，
 * 原文件保留 —— 裁错时换回原 URL 即可。
 */
export async function trimFreezoneAudio(
  project: string,
  payload: { sourceUrl: string; startSeconds: number; durationSeconds: number },
): Promise<FreezoneAudioTrimResult> {
  const resp = await apiClient(
    `projects/${encodeURIComponent(project)}/freezone/audio/trim`,
    {
      method: "POST",
      json: {
        source_url: payload.sourceUrl,
        start_seconds: payload.startSeconds,
        duration_seconds: payload.durationSeconds,
      },
    },
  ).json<{ ok: boolean; data?: FreezoneAudioTrimResult; error?: string }>();
  if (!resp.ok || !resp.data) {
    throw new Error(resp.error ?? "audio trim failed");
  }
  return resp.data;
}

function dataUrlToBlob(dataUrl: string): Blob {
  const [meta, payload = ""] = dataUrl.split(",", 2);
  const mimeMatch = /data:([^;]+)/.exec(meta);
  const mime = mimeMatch?.[1] ?? "image/png";
  const binary = atob(payload);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return new Blob([bytes], { type: mime });
}

function extensionForMime(mime: string): string {
  if (mime === "image/png") return "png";
  if (mime === "image/jpeg" || mime === "image/jpg") return "jpg";
  if (mime === "image/webp") return "webp";
  if (mime === "image/gif") return "gif";
  return "png";
}

/**
 * Coerce an arbitrary image reference into a backend-resolvable static path.
 *
 * - `data:` URLs are uploaded via `/freezone/upload` first; the response URL
 *   is what backends like `/freezone/image/reverse-prompt` can actually open.
 * - Anything else (already a `/static/...` path, http(s) URL, blob:, etc.) is
 *   returned as-is, minus the `?v=<ts>` cache buster that breaks backend path
 *   lookup (same strip pattern as `RedrawOverlay`).
 */
export async function ensureBackendImageUrl(
  project: string,
  rawUrl: string,
): Promise<string> {
  if (rawUrl.startsWith("data:")) {
    const blob = dataUrlToBlob(rawUrl);
    const ext = extensionForMime(blob.type);
    const uploaded = await uploadFreezoneImage(
      project,
      blob,
      `paste-${Date.now()}.${ext}`,
    );
    return uploaded.url.split("?")[0];
  }
  return rawUrl.split("?")[0];
}

/**
 * Batch variant of {@link ensureBackendImageUrl}: coerce a list of image
 * references into backend-resolvable static URLs. Empty / blank entries are
 * dropped; original order is preserved; uploads run in parallel.
 */
export async function ensureBackendImageUrls(
  project: string,
  rawUrls: readonly string[] | null | undefined,
): Promise<string[]> {
  if (!rawUrls || rawUrls.length === 0) return [];
  const cleaned = rawUrls.filter(
    (url): url is string => typeof url === "string" && url.trim().length > 0,
  );
  return await Promise.all(
    cleaned.map((url) => ensureBackendImageUrl(project, url)),
  );
}

// /freezone/extract-frames + analyze-shots -------------------------------- //

export interface FreezoneExtractFramesPayload {
  videoUrl: string;
  maxFrames?: number;
  sceneThreshold?: number;
}

export async function submitFreezoneExtractFrames(
  project: string,
  payload: FreezoneExtractFramesPayload,
): Promise<unknown> {
  return await apiCall<unknown>(
    `projects/${encodeURIComponent(project)}/freezone/extract-frames`,
    {
      method: "POST",
      json: {
        video_url: payload.videoUrl,
        max_frames: payload.maxFrames ?? 20,
        scene_threshold: payload.sceneThreshold ?? 0.3,
      },
    },
  );
}

export interface FreezoneAnalyzeShotsPayload {
  frameUrls: string[];
}

export async function submitFreezoneAnalyzeShots(
  project: string,
  payload: FreezoneAnalyzeShotsPayload,
): Promise<unknown> {
  return await apiCall<unknown>(
    `projects/${encodeURIComponent(project)}/freezone/analyze-shots`,
    {
      method: "POST",
      json: {
        frame_urls: payload.frameUrls,
      },
    },
  );
}

// /freezone/analyze-video-story ------------------------------------------ //

export interface FreezoneAnalyzeVideoStoryPayload {
  /** /static/... URL inside the project. */
  videoUrl: string;
  /** 3..50, defaults to 20 backend-side. */
  maxFrames?: number;
  /** 0..1 ffmpeg scene threshold; defaults to 0.3 backend-side. */
  sceneThreshold?: number;
  /** Optional total duration (seconds) — improves table timestamp accuracy. */
  durationSec?: number;
  /** Direct vision model registry id; omitted uses the model-center default. */
  model?: string | null;
  canvasId?: string | null;
  nodeId?: string | null;
}

export async function submitFreezoneAnalyzeVideoStory(
  project: string,
  payload: FreezoneAnalyzeVideoStoryPayload,
): Promise<FreezoneJobRef> {
  const body: Record<string, unknown> = {
    video_url: payload.videoUrl,
  };
  if (payload.maxFrames != null) body.max_frames = payload.maxFrames;
  if (payload.sceneThreshold != null) body.scene_threshold = payload.sceneThreshold;
  if (payload.durationSec != null) body.duration_sec = payload.durationSec;
  if (payload.model != null) body.model = payload.model;
  if (payload.canvasId) body.canvas_id = payload.canvasId;
  if (payload.nodeId) body.node_id = payload.nodeId;
  return await apiCall<FreezoneJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/analyze-video-story`,
    { method: "POST", json: body },
  );
}

// /freezone/video/character-library -------------------------------------- //

export type FreezoneAssetLibraryMedia = "image" | "video" | "audio";
export type FreezoneAssetLibrarySource =
  | "upload"
  | "character"
  | "scene"
  | "prop";

export interface FreezoneVideoCharacterLibraryItem {
  id?: string;
  name: string;
  media?: FreezoneAssetLibraryMedia;
  source?: FreezoneAssetLibrarySource;
  image_urls?: string[];
  video_url?: string | null;
  audio_url?: string | null;
  cover_url?: string | null;
  created_at?: string;
  updated_at?: string;
  [key: string]: unknown;
}

export interface FreezoneAddVideoCharacterLibraryItemPayload {
  name: string;
  media?: FreezoneAssetLibraryMedia;
  imageUrls?: string[];
  videoUrl?: string;
  audioUrl?: string;
}

export async function fetchFreezoneVideoCharacterLibrary(
  project: string,
): Promise<unknown> {
  return await apiCall<unknown>(
    `projects/${encodeURIComponent(project)}/freezone/video/character-library`,
  );
}

export async function submitFreezoneAddVideoCharacterLibraryItem(
  project: string,
  payload: FreezoneAddVideoCharacterLibraryItemPayload,
): Promise<unknown> {
  const body: Record<string, unknown> = {
    name: payload.name,
    media: payload.media ?? "image",
  };
  if (payload.imageUrls && payload.imageUrls.length > 0) {
    body.image_urls = payload.imageUrls;
  }
  if (payload.videoUrl) body.video_url = payload.videoUrl;
  if (payload.audioUrl) body.audio_url = payload.audioUrl;
  return await apiCall<unknown>(
    `projects/${encodeURIComponent(project)}/freezone/video/character-library`,
    { method: "POST", json: body },
  );
}

/**
 * 把主线的人物/场景/道具参考图与人物语音幂等同步进资产库。后端按稳定合成 id
 * upsert,重复同步只更新不重复。返回同步后的完整库(apiCall 会解包 data)。
 */
export async function syncFreezoneAssetLibraryFromMainline(
  project: string,
): Promise<FreezoneVideoCharacterLibraryItem[]> {
  return await apiCall<FreezoneVideoCharacterLibraryItem[]>(
    `projects/${encodeURIComponent(project)}/freezone/video/asset-library/sync-from-mainline`,
    { method: "POST" },
  );
}

export async function deleteFreezoneVideoCharacterLibraryItem(
  project: string,
  itemId: string,
): Promise<unknown> {
  return await apiCall<unknown>(
    `projects/${encodeURIComponent(project)}/freezone/video/character-library/${encodeURIComponent(itemId)}`,
    { method: "DELETE" },
  );
}

// /freezone/init ---------------------------------------------------------- //

export async function initFreezone(project: string): Promise<{ freezone_dir: string }> {
  return await apiCall<{ freezone_dir: string }>(
    `projects/${encodeURIComponent(project)}/freezone/init`,
    { method: "POST" },
  );
}
