// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type {
  DirectVideoModelConfig,
  DirectVideoModelProbeResult,
} from "@/lib/queries/model-gateway";

const VIDEO_CAPABILITY_MODE_LABELS: Record<string, string> = {
  textToVideo: "文生视频",
  imageToVideo: "图生视频（首帧）",
  firstLastFrame: "首尾帧",
  imageReference: "图片参考",
  allReference: "全能参考",
  videoEdit: "视频编辑",
};

const VIDEO_CAPABILITY_REFERENCE_MODE_LABELS: Record<string, string> = {
  imageToVideo: "图生视频",
  firstLastFrame: "首尾帧",
  imageReference: "图片参考",
  allReference: "全能参考",
  videoEdit: "视频编辑",
};

type ProbedVideoCapability = NonNullable<DirectVideoModelProbeResult["capability"]>;

/**
 * 从模型名里读出显式写出的能力段，用于在界面上说明"这些数字是哪来的"。
 *
 * 后端 `model_name_capabilities.py` 是权威实现，这里只做展示用的等价子集：
 * 中转站把能力写进模型名（`S-2.5-10图-内置过脸`），而 `GET /models` 本身不返回
 * 能力字段，所以界面需要能区分"上游声明的"和"本地预设的"。
 */
function parseModelNameCapabilityLabels(modelId: string): string[] {
  const raw = String(modelId ?? "").trim();
  if (!raw) return [];
  const segments = raw.split(/[-_/\s]+/).filter(Boolean);
  const haystack = segments.length > 0 ? segments.join(" ") : raw;
  const labels: string[] = [];
  const images = haystack.match(/(\d{1,3})\s*(?:张|幅|帧)?\s*(?:参考)?\s*图(?![a-zA-Z])/);
  if (images) labels.push(`参考图 ${Number(images[1])}`);
  const videos = haystack.match(/(\d{1,3})\s*(?:段|个|条)?\s*视频(?![a-zA-Z])/);
  if (videos) labels.push(`参考视频 ${Number(videos[1])}`);
  const audios = haystack.match(/(\d{1,3})\s*(?:段|个|条)?\s*音频(?![a-zA-Z])/);
  if (audios) labels.push(`参考音频 ${Number(audios[1])}`);
  const duration = haystack.match(/(?:固定|定长)?\s*(\d{1,3})\s*(?:秒|s)(?![a-zA-Z0-9])/);
  if (duration) labels.push(`固定 ${Number(duration[1])} 秒`);
  if (/(?:内置)?\s*(?:过脸|换脸)|人脸(?:保持|一致|稳定|处理)/.test(haystack)) {
    labels.push("内置过脸");
  }
  return labels;
}

/**
 * 判断界面上的能力数字究竟来自哪里。
 *
 * 中转站的 `GET /models` 不返回能力字段，所以「检测连接通过」不等于
 * 「上游确认了能力」。这里把三种来源分开，供状态文案和能力预览共用，
 * 避免两处各说各话。
 */
export function videoCapabilitySourceFor(
  probed: ProbedVideoCapability | undefined,
  saved: DirectVideoModelConfig | undefined,
  modelId: string,
): "upstream" | "model-name" | "local" {
  if (
    probed?.referenceLimits
    || probed?.sizeSlots
    || probed?.aspectRatios
    || probed?.durationRange
    || probed?.nativeAudio
  ) {
    return "upstream";
  }
  if (saved?.referenceLimits || saved?.supportedModes?.length) return "upstream";
  return parseModelNameCapabilityLabels(modelId).length > 0 ? "model-name" : "local";
}

function positiveCapabilityCount(value: unknown): number | null {
  const count = Number(value);
  return Number.isFinite(count) && count > 0 ? Math.floor(count) : null;
}

function savedVideoReferenceSummaries(
  limits: DirectVideoModelConfig["referenceLimits"],
): string[] {
  if (!limits) return [];
  return Object.entries(limits).flatMap(([mode, values]) => {
    const parts = [
      positiveCapabilityCount(values.image) ? `图 ${positiveCapabilityCount(values.image)}` : "",
      positiveCapabilityCount(values.video) ? `视频 ${positiveCapabilityCount(values.video)}` : "",
      positiveCapabilityCount(values.audio) ? `音频 ${positiveCapabilityCount(values.audio)}` : "",
    ].filter(Boolean);
    if (parts.length === 0) return [];
    return [`${VIDEO_CAPABILITY_REFERENCE_MODE_LABELS[mode] ?? mode}：${parts.join(" / ")}`];
  });
}

function probedVideoReferenceSummaries(
  limits: ProbedVideoCapability["referenceLimits"],
): string[] {
  if (!limits) return [];
  const parts = [
    positiveCapabilityCount(limits.inputImages) ? `输入图 ${positiveCapabilityCount(limits.inputImages)}` : "",
    positiveCapabilityCount(limits.referenceImages) ? `参考图 ${positiveCapabilityCount(limits.referenceImages)}` : "",
    positiveCapabilityCount(limits.referenceVideos) ? `参考视频 ${positiveCapabilityCount(limits.referenceVideos)}` : "",
    positiveCapabilityCount(limits.referenceAudios) ? `参考音频 ${positiveCapabilityCount(limits.referenceAudios)}` : "",
  ].filter(Boolean);
  return parts.length > 0 ? [parts.join(" / ")] : [];
}

/**
 * 把最后一次真实检测的时间压成短标签。拿不到时间就直说「时间未知」，
 * 不假装这是刚验过的结果 —— 缓存里的绿灯必须能看出是旧账。
 */
function formatLastCheckedAt(value?: string): string {
  const raw = (value ?? "").trim();
  if (!raw) return "（时间未知）";
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return "（时间未知）";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `（${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())} ${pad(parsed.getHours())}:${pad(parsed.getMinutes())}）`;
}

export function VideoModelCapabilityPreview({
  saved,
  probed,
}: {
  saved?: DirectVideoModelConfig;
  probed?: ProbedVideoCapability;
}) {
  const modes = probed?.modes !== undefined
    ? probed.modes
    : saved?.supportedModes ?? [];
  const resolutions = probed?.resolutionOptions !== undefined
    ? probed.resolutionOptions
    : saved?.resolutionOptions ?? [];
  const advertisedResolutions = saved?.advertisedResolutionOptions ?? [];
  const runtimeResolutions = saved?.runtimeResolutionOptions ?? [];
  const runtimeNote = saved?.runtimeCapabilityNote?.trim();
  const sizes = probed?.sizeSlots !== undefined
    ? probed.sizeSlots
    : saved?.sizeOptions ?? [];
  const ratios = probed?.aspectRatios !== undefined
    ? probed.aspectRatios
    : saved?.aspectRatioOptions ?? [];
  const durationRange = probed?.durationRange
    ?? (saved?.minDuration != null && saved?.maxDuration != null
      ? [saved.minDuration, saved.maxDuration] as [number, number]
      : undefined);
  const nativeAudio = probed?.nativeAudio ?? saved?.nativeAudio;
  const adapterFamily = probed?.adapterFamily ?? saved?.adapterFamily;
  const verificationStage = probed?.verificationStage ?? saved?.verificationStage ?? "unknown";
  const verificationStageLabel: Record<string, string> = {
    unknown: "未验证",
    catalog: "目录已验证",
    contract: "合同已解析",
    submit: "提交已验证",
    poll: "轮询已验证",
    artifact: "产物已验证",
  };
  const referenceSummaries = savedVideoReferenceSummaries(saved?.referenceLimits);
  if (referenceSummaries.length === 0) {
    referenceSummaries.push(...probedVideoReferenceSummaries(probed?.referenceLimits));
  }
  // 能力来源：中转站 `/models` 不返回能力字段，所以这里的数字通常来自本地预设
  // 或从模型名解析出的声明。两者都不等于"上游确认过"，界面必须说清楚，否则
  // 用户会以为屏幕上的是上游真实能力。
  const capabilitySource = videoCapabilitySourceFor(probed, saved, saved?.modelId ?? "");
  const nameCapabilityLabels = parseModelNameCapabilityLabels(saved?.modelId ?? "");
  const hasCapability = Boolean(
    modes.length
    || resolutions.length
    || sizes.length
    || ratios.length
    || durationRange
    || nativeAudio
    || referenceSummaries.length,
  );
  const audioLabel = nativeAudio === "required"
    ? "原生音频必开"
    : nativeAudio === "optional"
      ? "原生音频可选"
      : nativeAudio === "unsupported"
        ? "无原生音频"
        : "";

  return (
    <div
      className="mb-3 rounded-xl border border-cyan-300/15 bg-cyan-300/[0.035] px-3 py-2.5"
      aria-label="视频节点能力预览"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <strong className="text-[11px] font-medium text-cyan-100">视频节点能力预览</strong>
        <div className="flex items-center gap-2 text-[10px] text-cyan-100/55">
          <span>
            {probed
              ? "本次检测"
              : saved?.runtimeReady
                ? `上次检测通过${formatLastCheckedAt(saved.lastCheckedAt)}`
                : "等待检测"}
          </span>
          <span className="rounded border border-cyan-300/20 px-1.5 py-0.5">
            {verificationStageLabel[verificationStage] ?? "未验证"}
          </span>
        </div>
      </div>
      {hasCapability ? (
        <>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {modes.map((mode) => (
              <span
                key={mode}
                className="rounded-full border border-white/10 bg-black/15 px-2 py-0.5 text-[10px] text-foreground/85"
              >
                {VIDEO_CAPABILITY_MODE_LABELS[mode] ?? mode}
              </span>
            ))}
          </div>
          <div className="mt-2 space-y-1 text-[10px] leading-4 text-muted-foreground">
            {referenceSummaries.length > 0 ? <p>{referenceSummaries.join(" · ")}</p> : null}
            {capabilitySource !== "upstream" && nameCapabilityLabels.length > 0 ? (
              <p className="text-cyan-100/70">
                模型名声明：{nameCapabilityLabels.join(" · ")}
              </p>
            ) : null}
            <p>
              {[
                resolutions.length ? `分辨率 ${resolutions.join(" / ")}` : "",
                sizes.length ? `尺寸 ${sizes.join(" / ")}` : "",
                ratios.length ? `比例 ${ratios.join(" / ")}` : "",
                durationRange ? `时长 ${durationRange[0]}-${durationRange[1]} 秒` : "",
                audioLabel,
                adapterFamily ? `协议族 ${adapterFamily}` : "",
              ].filter(Boolean).join(" · ")}
            </p>
            {runtimeNote ? (
              <p className="text-amber-200/80">
                当前运行能力：{runtimeResolutions.length ? runtimeResolutions.join(" / ") : "待验证"}
                {advertisedResolutions.length > runtimeResolutions.length
                  ? `；目录宣称：${advertisedResolutions.join(" / ")}`
                  : ""}
                {`；${runtimeNote}`}
              </p>
            ) : null}
          </div>
        </>
      ) : (
        <p className="mt-1.5 text-[10px] leading-4 text-muted-foreground">
          检测连接后，这里会显示上游支持的生成模式和参数。
        </p>
      )}
      <p className="mt-1.5 text-[10px] leading-4 text-cyan-100/55">
        {capabilitySource === "upstream"
          ? "以上参数来自上游声明的能力合同。"
          : capabilitySource === "model-name"
            ? "中转站不返回能力字段；以上参数按模型名中的声明推断，可能与上游实际能力有出入。"
            : "中转站不返回能力字段；以上参数为本地预设，不是上游声明。"}
      </p>
      <p className="mt-1.5 text-[10px] leading-4 text-cyan-100/55">
        单张首帧显示为“图生视频”；上游同时支持首帧和尾帧时才显示“首尾帧”。
      </p>
    </div>
  );
}
