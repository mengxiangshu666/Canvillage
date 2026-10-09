// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { FreezoneVideoCapabilityParameter } from "../../../api/ops";

/** 画布已有独立控件（模式/时长/分辨率/提示词…），不再以裸输入框重复暴露。 */
const COMMON_VIDEO_PARAMETER_KEYS = new Set([
  "mode",
  "duration",
  "resolution",
  "size",
  "aspectRatio",
  "generateAudio",
  "images",
  "videos",
  "audios",
  "prompt",
  "firstFrame",
  "lastFrame",
]);

/**
 * 媒体槽位（`ref_image_N` / `ref_audio_N` / `ref_video` 等）由上游节点连线投喂：
 * 节点自己收集真实 URL，后端再按槽位序号写进请求。把它们渲染成可编辑输入框没有
 * 意义——用户改不动有效值，而写进去的值会被 compile_video_provider_parameters 当成
 * provider 参数合并进请求，覆盖真正的媒体。它们的默认值只是 AutoDL 工作流导出的
 * 占位符（`default_local_path:/blank.png;…`），不是可用参数。
 *
 * 判定用两个信号：能力信封里已声明类型的看 `type`；opaque 字段没有类型声明，只能
 * 按名字认（否则会被推断成 string 而漏网）。
 */
const TRANSPORT_VIDEO_PARAMETER_TYPES = new Set(["image", "video", "audio"]);
const TRANSPORT_VIDEO_PARAMETER_KEY_PATTERN = /^ref_(image|video|audio)(_\d+)?$/;

export function isRenderableVideoParameter(
  parameter: FreezoneVideoCapabilityParameter,
): boolean {
  const key = String(parameter.key ?? "").trim();
  if (key.length === 0 || COMMON_VIDEO_PARAMETER_KEYS.has(key)) return false;
  if (TRANSPORT_VIDEO_PARAMETER_KEY_PATTERN.test(key)) return false;
  const type = String(parameter.type ?? "").trim().toLowerCase();
  return !TRANSPORT_VIDEO_PARAMETER_TYPES.has(type);
}

/**
 * 合并能力信封声明的参数与未能归类的 opaque 字段，返回值得给用户编辑的那些。
 * `opaque` 值没有类型声明，按运行时类型推断。
 */
export function visibleVideoAdvancedParameters(
  parameters: readonly FreezoneVideoCapabilityParameter[],
  opaque: readonly { key: string; value: unknown; source?: string }[],
): FreezoneVideoCapabilityParameter[] {
  const merged = new Map<string, FreezoneVideoCapabilityParameter>();
  parameters.forEach((parameter) => {
    const key = String(parameter.key ?? "").trim();
    if (key.length > 0) merged.set(key, parameter);
  });
  opaque.forEach((field) => {
    const key = String(field.key ?? "").trim();
    if (!key || merged.has(key)) return;
    const value = field.value;
    const type: FreezoneVideoCapabilityParameter["type"] =
      typeof value === "boolean"
        ? "boolean"
        : typeof value === "number"
          ? Number.isInteger(value)
            ? "integer"
            : "number"
          : Array.isArray(value)
            ? "array"
            : value && typeof value === "object"
              ? "object"
              : "string";
    merged.set(key, {
      key,
      providerKey: key,
      type,
      default: value,
      advanced: true,
      source: field.source ?? "catalog",
    });
  });
  return [...merged.values()].filter(isRenderableVideoParameter);
}
