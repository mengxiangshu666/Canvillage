// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback, useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronUp, Film, Library, Music, Pause, Plus, SlidersHorizontal } from "lucide-react";
import type { Seedance2SceneOptimize, VideoGenCount, VideoGenMode, VideoGenQuality, VideoNodeData } from "@/features/canvas/domain/canvasNodes";
import type { FreezoneVideoCapabilityParameter, FreezoneVideoAspectRatio } from "@/api/ops";
import { visibleVideoAdvancedParameters } from "@/features/canvas/domain/videoAdvancedParameters";
import { resolveImageDisplayUrl } from "@/features/canvas/application/imageData";
import { useAnchoredNodePopoverPosition } from "@/features/canvas/ui/anchoredPopover";
import { NODE_CONTEXT_CONTROL_TRIGGER_CLASS, NODE_INLINE_ICON_BUTTON_ACTIVE_CLASS, NODE_INLINE_ICON_BUTTON_CLASS, NODE_REFERENCE_MEDIA_CHIP_CLASS, NODE_REFERENCE_MEDIA_DETACH_CLASS, NODE_TEXT_CONTROL_ICON_CLASS, NODE_TEXT_CONTROL_TRIGGER_CLASS, NODE_FLOATING_PANEL_SURFACE_CLASS } from "@/features/canvas/ui/nodeControlStyles";
import { findCameraMovementPreset, type CameraMovementPreset } from "@/features/canvas/domain/cameraMovementPresets";
import { CameraMovementPickerPopover } from "@/features/canvas/nodes/CameraMovementPickerPopover";
import { compileVideoModelFamily, type VideoModelFamily } from "@/features/canvas/domain/videoCapabilityCompiler";
import { listVisibleVideoModeTabs, resolveActiveVideoModeTab } from "@/features/canvas/domain/videoModeTabs";
import { GENERATION_CONCURRENCY_DEFAULT, generationQueueUnitHint } from "@/features/canvas/application/generationConcurrency";
import { ReferenceDetachButton } from "@/features/canvas/nodes/shared/ReferenceDetachButton";
import { ReferenceChipDurationBadge, ReferenceChipNumberBadge } from "@/features/canvas/nodes/shared/ReferenceStripBadge";
import { referenceMentionName } from "@/features/canvas/nodes/shared/referenceStripBadges";
import type { ReferenceMediaCapEntry, ReferenceMediaItem } from "@/features/canvas/hooks/useVideoReferences";
import { clampVideoDuration } from "./videoNodeModelRules";

const COUNT_OPTIONS: ReadonlyArray<VideoGenCount> = [1, 2, 4, 6, 8, 12];
const VIDEO_PARAM_POPOVER_CLASS = `nodrag nowheel fixed z-[10000] w-[320px] p-4 ${NODE_FLOATING_PANEL_SURFACE_CLASS}`;
const VIDEO_COUNT_POPOVER_CLASS = `nodrag nowheel fixed z-[10000] w-[132px] overflow-hidden p-1 ${NODE_FLOATING_PANEL_SURFACE_CLASS}`;
const VIDEO_PARAM_LABEL_CLASS = "mb-2 text-[11px] font-semibold uppercase tracking-wide text-text-dark/72";
const VIDEO_PARAM_BUTTON_BASE_CLASS = "inline-flex items-center justify-center rounded px-2 py-2 text-xs transition-colors";
const VIDEO_PARAM_ACTIVE_BUTTON_CLASS = "bg-white/[0.13] text-text-dark ring-1 ring-white/24";
const VIDEO_PARAM_IDLE_BUTTON_CLASS = "bg-white/[0.07] text-text-muted/95 hover:bg-white/[0.11] hover:text-text-dark";
const VIDEO_PARAM_ROW_CLASS = "mb-4 gap-2";
const VIDEO_COUNT_OPTION_BASE_CLASS = "block w-full rounded-[6px] px-3 py-1.5 text-left text-xs transition-colors";
const VIDEO_MODE_POPOVER_CLASS = `nodrag nowheel fixed z-[10000] w-[132px] overflow-visible p-1 ${NODE_FLOATING_PANEL_SURFACE_CLASS}`;
const VIDEO_MODE_TOOLTIP_CLASS = "pointer-events-none absolute left-full top-1/2 z-[10001] ml-2 -translate-y-1/2 whitespace-nowrap rounded-md bg-[#1f1f22] px-2.5 py-1.5 text-[11px] font-medium text-white/90 shadow-lg ring-1 ring-white/10";

interface GenModeSelectProps {
  value: VideoGenMode;
  modelId: string | null | undefined;
  modelFamily?: VideoModelFamily;
  supportedModes?: VideoGenMode[];
  upstreamCounts: { videos: number; images: number; audios: number };
  onChange: (next: VideoGenMode) => void;
}

function videoModeDisabledReason(
  mode: VideoGenMode,
  modelId: string | null | undefined,
  upstreamCounts: { videos: number; images: number; audios: number },
  modelFamily?: VideoModelFamily,
): string | null {
  const effectiveModelFamily = modelFamily ?? compileVideoModelFamily(modelId);
  if (effectiveModelFamily === "kacang-kling-v2v") {
    if (mode !== "videoEdit") return "Kling V3 Omni 当前只支持源视频重绘";
    if (upstreamCounts.videos === 0) return "Kling V3 Omni 视频重绘需要连接 1 个源视频";
    if (upstreamCounts.videos > 1) return "Kling V3 Omni 视频重绘仅支持连接 1 个源视频";
    return null;
  }
  // HappyHorse 的模式可用性完全由上游节点类型决定（文档 4 大功能）：
  //   文生视频  — 仅无上游时可用
  //   首帧      — 仅上游正好 1 张图片时可用
  //   图片参考  — 上游 1~9 张图片时可用
  //   视频编辑  — 仅上游有 1 个视频时可用
  // 不可用时返回 hover 文案（提示用户需要连接什么）。
  if (effectiveModelFamily === "happyhorse") {
    const { images, videos } = upstreamCounts;
    switch (mode) {
      case "textToVideo":
        if (videos > 0) return "已连接视频节点，请使用「视频编辑」";
        if (images > 0) return "已连接图片节点，请选择「首帧」或「图片参考」";
        return null;
      case "imageToVideo": // 首帧 (i2v)
        if (videos > 0) return "已连接视频节点，「首帧」不可用";
        if (images === 0) return "需要连接图片节点（1个）";
        if (images > 1) return "「首帧」仅支持单张图片，请用「图片参考」";
        return null;
      case "imageReference": // 图片参考 (r2v)
        if (videos > 0) return "已连接视频节点，「图片参考」不可用";
        if (images === 0) return "需要连接图片节点（1~9个）";
        if (images > 9) return "「图片参考」最多支持 9 张图片";
        return null;
      case "videoEdit":
        if (videos === 0) return "需要连接视频节点（1个）";
        if (videos > 1) return "「视频编辑」仅支持连接 1 个视频节点";
        return null;
      default:
        return "HappyHorse 不支持该模式";
    }
  }
  if (upstreamCounts.videos > 0 && mode !== "allReference") {
    return "上游含视频素材时只能用「全能参考」";
  }
  if (
    mode === "textToVideo" &&
    (upstreamCounts.images > 0 || upstreamCounts.audios > 0)
  ) {
    return "已引用图片/音频素材时不可用";
  }
  if (mode === "imageToVideo" && upstreamCounts.videos >= 2) {
    return "上游有多个视频时不可用";
  }
  if (mode === "firstLastFrame" && upstreamCounts.images > 2) {
    return "上游图片超过 2 张时不可用";
  }
  return null;
}

export function GenModeSelect({ value, modelId, modelFamily, supportedModes, upstreamCounts, onChange }: GenModeSelectProps) {
  const { t } = useTranslation();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const [isOpen, setIsOpen] = useState(false);
  const popoverId = useId();
  const [hoveredKey, setHoveredKey] = useState<VideoGenMode | null>(null);
  const [popoverPosition, setPopoverPosition] = useState<{
    left: number;
    top: number;
  } | null>(null);
  const effectiveModelFamily = modelFamily ?? compileVideoModelFamily(modelId);
  // HappyHorse 的模式面板对齐文档 4 大功能：文生视频 / 首帧 / 图片参考 / 视频编辑。
  //   - 隐藏「首尾帧」「全能参考」：HappyHorse 无这两种能力，点了只会报错。
  //   - 把「图生视频」显示为「首帧」：它本就是单图首帧 i2v，直接叫「首帧」跟「图片
  //     参考」一眼分清。
  //   - 上游接入视频后，「首帧」「图片参考」整项隐藏（文档：视频节点下没有这两个
  //     选项），只保留「文生视频」(禁用) 与「视频编辑」。
  const visibleTabs = useMemo(() => {
    return listVisibleVideoModeTabs({
      family: effectiveModelFamily,
      supportedModes,
      upstreamVideoCount: upstreamCounts.videos,
    });
  }, [effectiveModelFamily, supportedModes, upstreamCounts.videos]);
  const activeTab = resolveActiveVideoModeTab({ value, tabs: visibleTabs });

  const syncPopoverPosition = useCallback(() => {
    const trigger = triggerRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const margin = 8;
    setPopoverPosition({
      left: Math.min(Math.max(margin, rect.left), window.innerWidth - 132 - margin),
      top: rect.bottom + 8,
    });
  }, []);

  useEffect(() => {
    if (!isOpen) {
      setHoveredKey(null);
      return;
    }
    syncPopoverPosition();
    const onPointerDown = (event: MouseEvent) => {
      if (
        triggerRef.current?.contains(event.target as Node) ||
        popoverRef.current?.contains(event.target as Node)
      ) {
        return;
      }
      setIsOpen(false);
    };
    const onViewportChange = () => syncPopoverPosition();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setIsOpen(false);
      triggerRef.current?.focus();
    };
    document.addEventListener("mousedown", onPointerDown, true);
    document.addEventListener("keydown", onKeyDown);
    window.addEventListener("resize", onViewportChange);
    window.addEventListener("scroll", onViewportChange, true);
    return () => {
      document.removeEventListener("mousedown", onPointerDown, true);
      document.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("resize", onViewportChange);
      window.removeEventListener("scroll", onViewportChange, true);
    };
  }, [isOpen, syncPopoverPosition]);

  return (
    <div className="relative shrink-0">
      <button
        ref={triggerRef}
        type="button"
        aria-haspopup="listbox"
        aria-expanded={isOpen}
        aria-controls={isOpen ? popoverId : undefined}
        onClick={(event) => {
          event.stopPropagation();
          setIsOpen((prev) => !prev);
        }}
        className={NODE_CONTEXT_CONTROL_TRIGGER_CLASS}
      >
        <span>{t(activeTab.labelKey)}</span>
        <ChevronDown className="h-3 w-3 text-text-muted/90" />
      </button>
      {isOpen && popoverPosition && createPortal(
        <div
          ref={popoverRef}
          id={popoverId}
          role="listbox"
          aria-label={t("node.videoNode.tabs.mode")}
          className={VIDEO_MODE_POPOVER_CLASS}
          style={{
            left: popoverPosition.left,
            top: popoverPosition.top,
          }}
          onPointerDown={(event) => event.stopPropagation()}
          onClick={(event) => event.stopPropagation()}
        >
          {visibleTabs.map((tab) => {
            const isActive = tab.key === value;
            const disabledReason = videoModeDisabledReason(
              tab.key,
              modelId,
              upstreamCounts,
              effectiveModelFamily,
            );
            const isDisabled = disabledReason != null && !isActive;
            // 禁用按钮在多数浏览器里不触发 mouse 事件，hover 提示挂在外层 div 上；
            // 提示气泡定位到菜单右侧，与设计稿一致。
            return (
              <div
                key={tab.key}
                className="relative"
                onMouseEnter={() =>
                  isDisabled ? setHoveredKey(tab.key) : setHoveredKey(null)
                }
                onMouseLeave={() =>
                  setHoveredKey((prev) => (prev === tab.key ? null : prev))
                }
              >
                <button
                  type="button"
                  role="option"
                  aria-selected={isActive}
                  title={disabledReason ?? undefined}
                  disabled={isDisabled}
                  onClick={() => {
                    if (isDisabled) return;
                    onChange(tab.key);
                    setIsOpen(false);
                  }}
                  className={`block w-full rounded-[6px] px-3 py-1.5 text-left text-xs transition-colors ${
                    isActive
                      ? VIDEO_PARAM_ACTIVE_BUTTON_CLASS
                      : isDisabled
                        ? "cursor-not-allowed text-text-muted/40"
                        : "text-text-muted/95 hover:bg-white/[0.11] hover:text-text-dark"
                  }`}
                >
                  {t(tab.labelKey)}
                </button>
                {isDisabled && hoveredKey === tab.key && disabledReason && (
                  <div className={VIDEO_MODE_TOOLTIP_CLASS}>{disabledReason}</div>
                )}
              </div>
            );
          })}
        </div>,
        document.body,
      )}
    </div>
  );
}

interface VideoConfigChipProps {
  aspectRatio: FreezoneVideoAspectRatio;
  aspectRatioOptions: readonly FreezoneVideoAspectRatio[];
  resolutionOptions: readonly string[];
  quality: VideoGenQuality;
  advertisedQualityOptions: readonly VideoGenQuality[];
  rejectedQualityOptions: ReadonlySet<VideoGenQuality>;
  runtimeCapabilityNote?: string;
  durationSec: number;
  durationBounds: { min: number; max: number };
  durationOptions?: readonly number[];
  durationParameterEnabled: boolean;
  sceneOptimize?: Seedance2SceneOptimize;
  sceneOptimizeOptions: readonly Seedance2SceneOptimize[];
  /**
   * 「生成音频」的开关状态与能力提示。
   *
   * 对齐 libtv 视频参数面板的排布（用户 2026-10-01 实拍：比例 / 清晰度 / 视频时长 /
   * 生成音频，其中生成音频是**开启 / 关闭二选一**，不是拨杆）。原来这项是节点底栏上
   * 一个「原生音频 + 拨杆」的 chip，与 libtv 既不同形也不同位，故搬进面板。
   */
  generateAudio: boolean;
  nativeAudio: "unsupported" | "optional" | "required";
  onGenerateAudioChange: (next: boolean) => void;
  onChange: (patch: Partial<VideoNodeData>) => void;
}

function capabilityParameterValue(
  parameter: FreezoneVideoCapabilityParameter,
  values: Record<string, unknown>,
): unknown {
  return Object.prototype.hasOwnProperty.call(values, parameter.key)
    ? values[parameter.key]
    : parameter.default;
}

interface VideoAdvancedParametersChipProps {
  parameters: readonly FreezoneVideoCapabilityParameter[];
  opaque: readonly { key: string; value: unknown; source?: string }[];
  values: Record<string, unknown>;
  onChange: (values: Record<string, unknown>) => void;
}

/** Provider-specific controls stay data-driven by the model capability envelope. */
export function VideoAdvancedParametersChip({
  parameters,
  opaque,
  values,
  onChange,
}: VideoAdvancedParametersChipProps) {
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const [isOpen, setIsOpen] = useState(false);
  const [jsonDrafts, setJsonDrafts] = useState<Record<string, string>>({});
  const visibleParameters = useMemo(
    () => visibleVideoAdvancedParameters(parameters, opaque),
    [opaque, parameters],
  );
  const popoverPosition = useAnchoredNodePopoverPosition(isOpen, triggerRef, {
    width: 420,
    height: Math.min(620, 180 + visibleParameters.length * 82),
    align: "start",
  });

  useEffect(() => {
    if (!isOpen) return;
    const onPointerDown = (event: MouseEvent) => {
      if (
        triggerRef.current?.contains(event.target as Node) ||
        popoverRef.current?.contains(event.target as Node)
      ) return;
      setIsOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown, true);
    return () => document.removeEventListener("mousedown", onPointerDown, true);
  }, [isOpen]);

  if (visibleParameters.length === 0) return null;

  const writeValue = (parameter: FreezoneVideoCapabilityParameter, value: unknown) => {
    const next = { ...values };
    if (value === undefined) delete next[parameter.key];
    else next[parameter.key] = value;
    onChange(next);
  };

  const commitRawValue = (parameter: FreezoneVideoCapabilityParameter, raw: string) => {
    const type = String(parameter.type ?? "string").toLowerCase();
    if (raw.trim() === "" && (type === "number" || type === "integer")) {
      writeValue(parameter, undefined);
      return;
    }
    if (type === "number" || type === "integer") {
      const parsed = Number(raw);
      if (!Number.isFinite(parsed)) return;
      writeValue(parameter, type === "integer" ? Math.round(parsed) : parsed);
      return;
    }
    if (type === "boolean") {
      writeValue(parameter, raw === "true");
      return;
    }
    if (type === "object" || type === "array") {
      try {
        writeValue(parameter, JSON.parse(raw));
        setJsonDrafts((drafts) => ({ ...drafts, [parameter.key]: raw }));
      } catch {
        // Keep invalid JSON as a draft until the user finishes editing.
      }
      return;
    }
    writeValue(parameter, raw);
  };

  const renderParameterControl = (parameter: FreezoneVideoCapabilityParameter) => {
    const type = String(parameter.type ?? "string").toLowerCase();
    const value = capabilityParameterValue(parameter, values);
    const enumValues = Array.isArray(parameter.enum) ? parameter.enum : [];
    if (enumValues.length > 0) {
      const selected = enumValues.some((option) => Object.is(option, value))
        ? JSON.stringify(value)
        : "";
      return (
        <select
          value={selected}
          onChange={(event) => {
            try {
              commitRawValue(parameter, event.target.value ? JSON.stringify(JSON.parse(event.target.value)) : "");
            } catch {
              commitRawValue(parameter, event.target.value);
            }
          }}
          className="nodrag nowheel h-8 min-w-0 flex-1 rounded border border-white/12 bg-white/[0.07] px-2 text-xs text-text-dark outline-none focus:border-white/28"
        >
          <option value="">按模型默认</option>
          {enumValues.map((option, index) => (
            <option key={`${parameter.key}-${index}`} value={JSON.stringify(option)}>
              {String(option)}
            </option>
          ))}
        </select>
      );
    }
    if (type === "boolean") {
      const checked = value === true;
      return (
        <button
          type="button"
          role="switch"
          aria-checked={checked}
          onClick={() => writeValue(parameter, !checked)}
          className={`nodrag inline-flex h-7 shrink-0 items-center gap-2 rounded-full border px-2.5 text-xs transition-colors ${
            checked ? "border-emerald-400/35 bg-emerald-400/15 text-emerald-100" : "border-white/12 bg-white/[0.07] text-text-muted"
          }`}
        >
          <span>{checked ? "开" : "关"}</span>
          <span className={`relative inline-flex h-4 w-7 items-center rounded-full p-0.5 ${checked ? "bg-emerald-500" : "bg-white/15"}`}>
            <span className={`h-3 w-3 rounded-full bg-white transition-transform ${checked ? "translate-x-3" : "translate-x-0"}`} />
          </span>
        </button>
      );
    }
    if (type === "object" || type === "array") {
      const draft = jsonDrafts[parameter.key] ?? (
        value === undefined ? "" : JSON.stringify(value, null, 2)
      );
      return (
        <textarea
          value={draft}
          placeholder="JSON"
          rows={2}
          onChange={(event) => setJsonDrafts((drafts) => ({ ...drafts, [parameter.key]: event.target.value }))}
          onBlur={(event) => commitRawValue(parameter, event.target.value)}
          className="nodrag nowheel min-h-12 min-w-0 flex-1 resize-y rounded border border-white/12 bg-white/[0.07] px-2 py-1.5 font-mono text-[11px] leading-4 text-text-dark outline-none focus:border-white/28"
        />
      );
    }
    return (
      <input
        type={type === "number" || type === "integer" ? "number" : "text"}
        value={value === undefined || value === null ? "" : String(value)}
        min={parameter.minimum}
        max={parameter.maximum}
        step={parameter.step ?? (type === "integer" ? 1 : "any")}
        placeholder={parameter.default === undefined ? "按模型默认" : String(parameter.default)}
        onChange={(event) => commitRawValue(parameter, event.target.value)}
        className="nodrag nowheel h-8 min-w-0 flex-1 rounded border border-white/12 bg-white/[0.07] px-2 text-xs text-text-dark outline-none focus:border-white/28"
      />
    );
  };

  return (
    <div className="relative">
      <button
        ref={triggerRef}
        type="button"
        aria-expanded={isOpen}
        title="模型参数"
        onClick={(event) => {
          event.stopPropagation();
          setIsOpen((open) => !open);
        }}
        className={`${NODE_INLINE_ICON_BUTTON_CLASS} ${isOpen ? NODE_INLINE_ICON_BUTTON_ACTIVE_CLASS : ""}`}
      >
        <SlidersHorizontal className="h-4 w-4" />
      </button>
      {isOpen && popoverPosition && typeof document !== "undefined" && createPortal(
        <div
          ref={popoverRef}
          className={`${VIDEO_PARAM_POPOVER_CLASS} max-h-[min(620px,80vh)] overflow-y-auto`}
          style={{ left: popoverPosition.left, top: popoverPosition.top }}
          onPointerDown={(event) => event.stopPropagation()}
          onClick={(event) => event.stopPropagation()}
        >
          <div className="mb-3 flex items-center justify-between gap-3">
            <span className="text-xs font-semibold text-text-dark">模型参数</span>
            <span className="text-[10px] text-text-muted/80">{visibleParameters.length} 项</span>
          </div>
          <div className="space-y-3">
            {visibleParameters.map((parameter) => (
              <label key={parameter.key} className="flex min-w-0 flex-col gap-1.5">
                <span className="flex min-w-0 items-baseline justify-between gap-3 text-[11px] text-text-muted">
                  <span className="truncate text-text-dark/90">{parameter.label || parameter.key}</span>
                  <span className="shrink-0 font-mono text-[10px] text-text-muted/65">{parameter.key}</span>
                </span>
                <div className="flex min-w-0 items-start gap-2">{renderParameterControl(parameter)}</div>
                {parameter.description ? (
                  <span className="text-[10px] leading-4 text-text-muted/70">{parameter.description}</span>
                ) : null}
              </label>
            ))}
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
}

export function VideoConfigChip({
  aspectRatio,
  aspectRatioOptions,
  resolutionOptions,
  quality,
  advertisedQualityOptions,
  rejectedQualityOptions,
  runtimeCapabilityNote,
  durationSec,
  durationBounds,
  durationOptions,
  durationParameterEnabled,
  sceneOptimize,
  sceneOptimizeOptions,
  generateAudio,
  nativeAudio,
  onGenerateAudioChange,
  onChange,
}: VideoConfigChipProps) {
  const { t } = useTranslation();
  // 与旧 chip 同一套口径：模型要求必开 / 不支持时，把原因写在开关下面。
  const audioCapabilityHint =
    nativeAudio === "required"
      ? t("node.videoNode.audio.required", {
          defaultValue: "当前模型上游要求带音频，关闭后本地成片仍会静音",
        })
      : nativeAudio === "unsupported"
        ? t("node.videoNode.audio.unsupported", {
            defaultValue: "当前模型不支持原生音频",
          })
        : null;
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const [isOpen, setIsOpen] = useState(false);
  const popoverPosition = useAnchoredNodePopoverPosition(isOpen, triggerRef, {
    width: 320,
    // 新增「生成音频」一行（标签 + 两个按钮 + 可能的提示），面板估高相应上调，
    // 免得贴到画布上下边缘时被裁掉。
    height: sceneOptimizeOptions.length > 0 ? 560 : 470,
    align: "start",
  });
  // Local draft for the direct-entry duration box. The field stays free text
  // while editing so a half-typed value isn't fought by clamping, but we still
  // want the slider and bottom chip to track the box live — so on each keystroke
  // we commit as soon as the draft is a *complete integer already inside* the
  // model's bounds. An out-of-range interim (the "1" of "12" when min is 5) is
  // held as draft only and NOT committed, so the user is never stranded at the
  // min mid-typing; blur/Enter clamps anything still out of range on the way out.
  const [durationDraft, setDurationDraft] = useState<string>(String(durationSec));
  useEffect(() => {
    setDurationDraft(String(durationSec));
  }, [durationSec]);
  const handleDurationInput = (raw: string) => {
    setDurationDraft(raw);
    const parsed = Number(raw);
    const isAllowedDiscreteValue =
      !durationOptions || durationOptions.length === 0 || durationOptions.includes(parsed);
    if (
      raw.trim() !== "" &&
      Number.isInteger(parsed) &&
      parsed >= durationBounds.min &&
      parsed <= durationBounds.max &&
      isAllowedDiscreteValue &&
      parsed !== durationSec
    ) {
      onChange({ durationSec: parsed });
    }
  };
  const commitDuration = () => {
    const parsed = Number(durationDraft);
    if (durationDraft.trim() === "" || !Number.isFinite(parsed)) {
      setDurationDraft(String(durationSec)); // revert empty/garbage to current
      return;
    }
    const clamped = clampVideoDuration(parsed, durationBounds, durationOptions);
    setDurationDraft(String(clamped));
    if (clamped !== durationSec) onChange({ durationSec: clamped });
  };

  useEffect(() => {
    if (!isOpen) return;
    const onPointerDown = (event: MouseEvent) => {
      if (
        triggerRef.current?.contains(event.target as Node) ||
        popoverRef.current?.contains(event.target as Node)
      ) {
        return;
      }
      setIsOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown, true);
    return () => document.removeEventListener("mousedown", onPointerDown, true);
  }, [isOpen]);

  const hasAspectOptions = aspectRatioOptions.length > 0;
  const hasNativeResolutionOptions = resolutionOptions.length > 0;

  return (
    <div className="relative">
      <button
        ref={triggerRef}
        type="button"
        onClick={(event) => {
          event.stopPropagation();
          setIsOpen((prev) => !prev);
        }}
        className={NODE_TEXT_CONTROL_TRIGGER_CLASS}
      >
        <span>
          {aspectRatio === "auto"
            ? t("node.videoNode.aspect.auto")
            : aspectRatio || "由模型决定"}
        </span>
        {aspectRatio ? (
          <span className="text-text-muted/80">·</span>
        ) : null}
        {!hasNativeResolutionOptions && advertisedQualityOptions.length > 0 ? (
          <span>{quality}</span>
        ) : null}
        {durationParameterEnabled ? (
          <>
            <span className="text-text-muted/80">·</span>
            <span>{durationSec}s</span>
          </>
        ) : null}
        <ChevronDown className="h-3 w-3 text-text-muted/90" />
      </button>
      {isOpen && popoverPosition && typeof document !== "undefined" && createPortal(
        <div
          ref={popoverRef}
          className={VIDEO_PARAM_POPOVER_CLASS}
          style={{
            left: popoverPosition.left,
            top: popoverPosition.top,
          }}
          onPointerDown={(event) => event.stopPropagation()}
          onClick={(event) => event.stopPropagation()}
        >
          {hasAspectOptions ? (
            <>
              {aspectRatioOptions.length > 0 ? (
                <>
                  <div className={VIDEO_PARAM_LABEL_CLASS}>
                    {t("node.videoNode.aspect.title")}
                  </div>
                  <div className={`grid grid-cols-5 ${VIDEO_PARAM_ROW_CLASS}`}>
                    {aspectRatioOptions.map((ratio) => {
                      const isActive = aspectRatio === ratio;
                      return (
                        <button
                          key={ratio}
                          type="button"
                          onClick={() => onChange({ aspectRatio: ratio })}
                          className={`${VIDEO_PARAM_BUTTON_BASE_CLASS} ${
                            isActive
                              ? VIDEO_PARAM_ACTIVE_BUTTON_CLASS
                              : VIDEO_PARAM_IDLE_BUTTON_CLASS
                          }`}
                        >
                          {ratio === "auto" ? t("node.videoNode.aspect.auto") : ratio}
                        </button>
                      );
                    })}
                  </div>
                </>
              ) : null}
            </>
          ) : null}

          {!hasNativeResolutionOptions && advertisedQualityOptions.length > 0 ? (
            <>
              <div className={VIDEO_PARAM_LABEL_CLASS}>{t("node.videoNode.quality.title")}</div>
              <div className={`grid grid-cols-3 ${VIDEO_PARAM_ROW_CLASS}`}>
                {advertisedQualityOptions.map((value) => {
                  const q = value;
                  const isActive = quality === q;
                  const isRejected = rejectedQualityOptions.has(q);
                  return (
                    <button
                      key={value}
                      type="button"
                      disabled={isRejected}
                      title={isRejected ? runtimeCapabilityNote || "当前渠道暂不接受该清晰度" : undefined}
                      onClick={() => onChange({ quality: q })}
                      className={`${VIDEO_PARAM_BUTTON_BASE_CLASS} ${
                        isActive
                          ? VIDEO_PARAM_ACTIVE_BUTTON_CLASS
                          : isRejected
                            ? "cursor-not-allowed border-amber-300/15 bg-amber-300/[0.04] text-amber-100/35 line-through"
                            : VIDEO_PARAM_IDLE_BUTTON_CLASS
                      }`}
                    >
                      {q}
                    </button>
                  );
                })}
              </div>
            </>
          ) : null}

          {durationParameterEnabled ? (
            <>
              <div className={VIDEO_PARAM_LABEL_CLASS}>
                {t("node.videoNode.duration.title")}
              </div>
              {durationOptions && durationOptions.length > 0 ? (
                <div className={`grid grid-cols-4 ${VIDEO_PARAM_ROW_CLASS}`}>
                  {durationOptions.map((option) => {
                    const isActive = durationSec === option;
                    return (
                      <button
                        key={option}
                        type="button"
                        onClick={() => onChange({ durationSec: option })}
                        className={`${VIDEO_PARAM_BUTTON_BASE_CLASS} ${
                          isActive
                            ? VIDEO_PARAM_ACTIVE_BUTTON_CLASS
                            : VIDEO_PARAM_IDLE_BUTTON_CLASS
                        }`}
                      >
                        {option}s
                      </button>
                    );
                  })}
                </div>
              ) : (
                <div className="mb-4 flex items-center gap-3">
                  <input
                    type="range"
                    min={durationBounds.min}
                    max={durationBounds.max}
                    step={1}
                    value={durationSec}
                    onChange={(event) =>
                      onChange({
                        durationSec: clampVideoDuration(Number(event.target.value), durationBounds),
                      })
                    }
                    className="video-duration-slider min-w-0 flex-1"
                  />
                  <div className="flex shrink-0 items-center gap-1">
                    <input
                      type="number"
                      inputMode="numeric"
                      min={durationBounds.min}
                      max={durationBounds.max}
                      step={1}
                      value={durationDraft}
                      onChange={(event) => handleDurationInput(event.target.value)}
                      onBlur={commitDuration}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") {
                          event.preventDefault();
                          commitDuration();
                          event.currentTarget.blur();
                        }
                      }}
                      aria-label={t("node.videoNode.duration.title")}
                      className="h-7 w-12 rounded border border-white/12 bg-white/[0.07] px-1.5 text-center text-xs tabular-nums text-text-dark outline-none transition-colors focus:border-white/28 focus:bg-white/[0.11] [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
                    />
                    <span className="text-[11px] text-text-muted/80">s</span>
                  </div>
                </div>
              )}
            </>
          ) : null}

          {/* 「生成音频」：对齐 libtv 的面板排布，二选一而不是拨杆。 */}
          <div className={VIDEO_PARAM_LABEL_CLASS}>
            {t("node.videoNode.audio.title")}
          </div>
          <div className={`grid grid-cols-2 ${VIDEO_PARAM_ROW_CLASS}`}>
            <button
              type="button"
              aria-pressed={generateAudio}
              onClick={() => onGenerateAudioChange(true)}
              className={`${VIDEO_PARAM_BUTTON_BASE_CLASS} ${
                generateAudio
                  ? VIDEO_PARAM_ACTIVE_BUTTON_CLASS
                  : VIDEO_PARAM_IDLE_BUTTON_CLASS
              }`}
            >
              {t("node.videoNode.audio.on")}
            </button>
            <button
              type="button"
              aria-pressed={!generateAudio}
              onClick={() => onGenerateAudioChange(false)}
              className={`${VIDEO_PARAM_BUTTON_BASE_CLASS} ${
                !generateAudio
                  ? VIDEO_PARAM_ACTIVE_BUTTON_CLASS
                  : VIDEO_PARAM_IDLE_BUTTON_CLASS
              }`}
            >
              {t("node.videoNode.audio.off")}
            </button>
          </div>
          {audioCapabilityHint ? (
            <p className="-mt-2 mb-4 text-[11px] leading-4 text-text-muted/80">
              {audioCapabilityHint}
            </p>
          ) : null}

          {sceneOptimizeOptions.length > 0 && (
            <>
              <div className={VIDEO_PARAM_LABEL_CLASS}>
                {t("node.videoNode.sceneOptimize.title")}
              </div>
              <div className={`grid grid-cols-2 ${VIDEO_PARAM_ROW_CLASS}`}>
                {sceneOptimizeOptions.map((option) => {
                  const isActive = sceneOptimize === option;
                  return (
                    <button
                      key={option}
                      type="button"
                      onClick={() => onChange({ sceneOptimize: option })}
                      className={`${VIDEO_PARAM_BUTTON_BASE_CLASS} ${
                        isActive
                          ? VIDEO_PARAM_ACTIVE_BUTTON_CLASS
                          : VIDEO_PARAM_IDLE_BUTTON_CLASS
                      }`}
                    >
                      {t(`node.videoNode.sceneOptimize.options.${option}`)}
                    </button>
                  );
                })}
              </div>
            </>
          )}

        </div>,
        document.body,
      )}
    </div>
  );
}

interface CameraMovementChipProps {
  templates: ReadonlyArray<CameraMovementPreset>;
  isLoading: boolean;
  selectedId: string | null;
  onChange: (next: string | null) => void;
}

const CAMERA_MOVEMENT_POPOVER_WIDTH = 640;
const CAMERA_MOVEMENT_POPOVER_MAX_HEIGHT = 560;
const CAMERA_MOVEMENT_POPOVER_GAP = 8;

export function CameraMovementChip({
  templates,
  isLoading,
  selectedId,
  onChange,
}: CameraMovementChipProps) {
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const [isOpen, setIsOpen] = useState(false);
  const [anchor, setAnchor] = useState<{ left: number; top: number } | null>(
    null,
  );

  // Position above the chip whenever it opens or the viewport changes. We
  // render the popover into <body> via portal so it can sit above the
  // react-flow NodeToolbar (z-[120]) — without portal it lives inside the
  // video node's transformed stacking context and gets covered.
  useEffect(() => {
    if (!isOpen) return;
    const updateAnchor = () => {
      const trigger = triggerRef.current;
      if (!trigger) return;
      const rect = trigger.getBoundingClientRect();
      const popHeight = Math.min(
        CAMERA_MOVEMENT_POPOVER_MAX_HEIGHT,
        rect.top - CAMERA_MOVEMENT_POPOVER_GAP - 8,
      );
      const wantTop = rect.top - popHeight - CAMERA_MOVEMENT_POPOVER_GAP;
      // If we can't fit above, fall back to below.
      const top =
        wantTop < 8 ? rect.bottom + CAMERA_MOVEMENT_POPOVER_GAP : wantTop;
      const wantLeft = rect.left;
      const left = Math.max(
        8,
        Math.min(
          wantLeft,
          window.innerWidth - CAMERA_MOVEMENT_POPOVER_WIDTH - 8,
        ),
      );
      setAnchor({ left, top });
    };
    updateAnchor();
    window.addEventListener("resize", updateAnchor);
    window.addEventListener("scroll", updateAnchor, true);
    return () => {
      window.removeEventListener("resize", updateAnchor);
      window.removeEventListener("scroll", updateAnchor, true);
    };
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return;
    const onPointerDown = (event: MouseEvent) => {
      if (
        triggerRef.current?.contains(event.target as Node) ||
        popoverRef.current?.contains(event.target as Node)
      ) {
        return;
      }
      setIsOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown, true);
    return () => document.removeEventListener("mousedown", onPointerDown, true);
  }, [isOpen]);

  const selectedPreset = findCameraMovementPreset(templates, selectedId);
  const label = selectedPreset?.label ?? "运镜";
  const isActive = Boolean(selectedPreset);

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        onClick={(event) => {
          event.stopPropagation();
          setIsOpen((prev) => !prev);
        }}
        className={`${NODE_TEXT_CONTROL_TRIGGER_CLASS} group/camera px-1.5 ${isActive ? "text-text-dark" : ""}`}
      >
        <Film className={`${NODE_TEXT_CONTROL_ICON_CLASS} group-hover/camera:text-text-dark`} />
        <span>{label}</span>
      </button>
      {isOpen &&
        anchor &&
        createPortal(
          <div
            ref={popoverRef}
            className="fixed z-[10000]"
            style={{ left: anchor.left, top: anchor.top }}
            onPointerDown={(event) => event.stopPropagation()}
            onClick={(event) => event.stopPropagation()}
          >
            <CameraMovementPickerPopover
              templates={templates}
              isLoading={isLoading}
              selectedId={selectedId}
              onConfirm={(nextId) => {
                onChange(nextId);
                setIsOpen(false);
              }}
              onClose={() => setIsOpen(false)}
            />
          </div>,
          document.body,
        )}
    </>
  );
}

interface CharacterLibraryChipProps {
  onOpen: () => void;
}

export function CharacterLibraryChip({ onOpen }: CharacterLibraryChipProps) {
  return (
    <button
      type="button"
      onClick={(event) => {
        event.stopPropagation();
        onOpen();
      }}
      className={`${NODE_TEXT_CONTROL_TRIGGER_CLASS} group/asset px-1.5`}
    >
      <Library className={`${NODE_TEXT_CONTROL_ICON_CLASS} group-hover/asset:text-text-dark`} />
      <span>资产库</span>
    </button>
  );
}

interface ExternalAssetChipProps {
  onOpen: () => void;
}

export function ExternalAssetChip({ onOpen }: ExternalAssetChipProps) {
  return (
    <button
      type="button"
      onClick={(event) => {
        event.stopPropagation();
        onOpen();
      }}
      className={`${NODE_TEXT_CONTROL_TRIGGER_CLASS} group/external px-1.5`}
    >
      <Plus className={`${NODE_TEXT_CONTROL_ICON_CLASS} group-hover/external:text-text-dark`} />
      <span>外部素材</span>
    </button>
  );
}

interface CountPickerProps {
  value: VideoGenCount;
  onChange: (next: VideoGenCount) => void;
}

export function CountPicker({ value, onChange }: CountPickerProps) {
  const { t } = useTranslation();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const [isOpen, setIsOpen] = useState(false);
  const popoverPosition = useAnchoredNodePopoverPosition(isOpen, triggerRef, {
    width: 132,
    height: 210,
    align: "end",
  });

  useEffect(() => {
    if (!isOpen) return;
    const onPointerDown = (event: MouseEvent) => {
      if (
        triggerRef.current?.contains(event.target as Node) ||
        popoverRef.current?.contains(event.target as Node)
      ) {
        return;
      }
      setIsOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown, true);
    return () => document.removeEventListener("mousedown", onPointerDown, true);
  }, [isOpen]);

  return (
    <div className="relative">
      <button
        ref={triggerRef}
        type="button"
        onClick={(event) => {
          event.stopPropagation();
          setIsOpen((prev) => !prev);
        }}
        className={NODE_TEXT_CONTROL_TRIGGER_CLASS}
      >
        <span>{t("node.videoNode.count.format", { count: value })}{value > GENERATION_CONCURRENCY_DEFAULT ? '·排队' : ''}</span>
        <ChevronUp className="h-3 w-3 text-text-muted/90" />
      </button>
      {isOpen && popoverPosition && typeof document !== "undefined" && createPortal(
        <div
          ref={popoverRef}
          className={VIDEO_COUNT_POPOVER_CLASS}
          style={{
            left: popoverPosition.left,
            top: popoverPosition.top,
          }}
          onPointerDown={(event) => event.stopPropagation()}
          onClick={(event) => event.stopPropagation()}
        >
          {COUNT_OPTIONS.map((option) => {
            const isActive = option === value;
            return (
              <button
                key={option}
                type="button"
                onClick={() => {
                  onChange(option);
                  setIsOpen(false);
                }}
                className={`${VIDEO_COUNT_OPTION_BASE_CLASS} ${
                  isActive
                    ? VIDEO_PARAM_ACTIVE_BUTTON_CLASS
                    : "text-text-muted/95 hover:bg-white/[0.11] hover:text-text-dark"
                }`}
              >
                {t("node.videoNode.count.format", { count: option })}{generationQueueUnitHint(option, '条')}
              </button>
            );
          })}
        </div>,
        document.body,
      )}
    </div>
  );
}

interface ReferenceMediaRowProps {
  items: ReadonlyArray<ReferenceMediaCapEntry>;
  /** 当前模型在当前模式的真实素材上限。 */
  limits?: { image?: number; video?: number; audio?: number } | null;
  /** 当前 genMode；用来决定 firstLastFrame 模式下给前两张图片打 首帧/尾帧 角标。 */
  genMode: VideoGenMode;
  onFocus: (nodeId: string) => void;
  onDetach: (nodeId: string) => void;
  // 拖动 chip 换位后，回传新的「按可视顺序排列的上游节点 id 列表」。
  onReorder: (orderedNodeIds: string[]) => void;
  /**
   * 点 chip 缩略图时把它插进提示词。参数是**该 chip 当时的 mention 名**（`图片3`）
   * —— 名字由本组件按 `typeIndex` 现算，保证插进提示词的序号就是用户在角标上看到
   * 的那个。
   */
  onInsert: (mentionName: string) => void;
}

/**
 * 引用缩略图上「点一下写进提示词」的口径见
 * `shared/referenceStripBadges.ts`；序号 / 时长角标由 `shared/ReferenceStripBadge.tsx`
 * 提供，与生图节点共用同一套标签（同一个位置、同一个字号）。
 */
export function ReferenceMediaRow({
  items,
  limits,
  genMode,
  onFocus,
  onDetach,
  onReorder,
  onInsert,
}: ReferenceMediaRowProps) {
  // 同时管理整行音频的「当前播放节点」—— 同一时间只允许一个 audio chip 在
  // 播放。点击另一个会切换；再点同一个会暂停。
  const [playingAudioNodeId, setPlayingAudioNodeId] = useState<string | null>(
    null,
  );
  // 拖拽换位的临时状态：正在被拖的 chip / 当前悬停落点 chip。
  const [dragNodeId, setDragNodeId] = useState<string | null>(null);
  const [overNodeId, setOverNodeId] = useState<string | null>(null);

  const clearDrag = useCallback(() => {
    setDragNodeId(null);
    setOverNodeId(null);
  }, []);

  const handleDrop = useCallback(
    (targetNodeId: string) => {
      const sourceId = dragNodeId;
      clearDrag();
      if (!sourceId || sourceId === targetNodeId) return;
      const ids = items.map((entry) => entry.item.nodeId);
      const from = ids.indexOf(sourceId);
      const to = ids.indexOf(targetNodeId);
      if (from === -1 || to === -1) return;
      ids.splice(from, 1);
      ids.splice(to, 0, sourceId);
      onReorder(ids);
    },
    [dragNodeId, items, onReorder, clearDrag],
  );

  // 整行混了多种类型时，角标才带单字类型前缀（图1 / 视1 / 音1）。只挂图片的一行上
  // 类型是自明的，加个「图」字只是噪声 —— 而且那种情况下三个角标本来就不会撞号。
  const mixedKinds = useMemo(
    () => new Set(items.map((entry) => entry.item.kind)).size > 1,
    [items],
  );

  return (
    <div className="flex min-w-0 flex-wrap items-center gap-1.5">
      {items.map((entry) => {
        const { item, typeIndex, withinCap } = entry;
        // 「超出当前模式上限」只在 REFERENCE_CAPS_BY_MODE 里登记过的模式生效
        // （目前是 allReference / firstLastFrame）；其它模式即便挂了 12 张图，
        // imageReference / firstLastFrame 自己有 slice 兜底，不在 chip 行额
        // 外标记。
        const overCap = limits != null && !withinCap;
        const modeCap = limits?.[item.kind] ?? 0;
        const modeLabel =
          genMode === "firstLastFrame" ? "首尾帧" : "全能参考";
        const overCapTitle = overCap
          ? `${
              item.kind === "image"
                ? "图片"
                : item.kind === "video"
                  ? "视频"
                  : "音频"
            }引用超出${modeLabel}上限（${modeCap}${
              item.kind === "image" ? "张" : "段"
            }），本次生成不会使用该素材`
          : undefined;
        // 首尾帧模式下，前两张图片打 首帧/尾帧 角标；超出 cap 的图片就回退到
        // 数字角标，让用户看到「这张图被忽略」的同时仍能在 prompt 里通过原序号
        // 对照——不过那种状态主要靠自动切换到 allReference 兜底，正常不会发生。
        const slotLabel =
          genMode === "firstLastFrame" &&
          item.kind === "image" &&
          withinCap
            ? typeIndex === 1
              ? "首帧"
              : typeIndex === 2
                ? "尾帧"
                : undefined
            : undefined;
        let chip: ReactNode;
        if (item.kind === "image") {
          chip = (
            <ReferenceImageChip
              item={item}
              index={typeIndex - 1}
              slotLabel={slotLabel}
              mixedKinds={mixedKinds}
              onFocus={onFocus}
              onDetach={onDetach}
              onInsert={() =>
                onInsert(referenceMentionName(item.kind, typeIndex))
              }
            />
          );
        } else if (item.kind === "video") {
          chip = (
            <ReferenceVideoChip
              item={item}
              index={typeIndex - 1}
              mixedKinds={mixedKinds}
              onFocus={onFocus}
              onDetach={onDetach}
              onInsert={() =>
                onInsert(referenceMentionName(item.kind, typeIndex))
              }
            />
          );
        } else {
          chip = (
            <ReferenceAudioChip
              item={item}
              index={typeIndex - 1}
              isPlaying={playingAudioNodeId === item.nodeId}
              mixedKinds={mixedKinds}
              onToggle={(playing) =>
                setPlayingAudioNodeId(playing ? item.nodeId : null)
              }
              onDetach={onDetach}
              onInsert={() =>
                onInsert(referenceMentionName(item.kind, typeIndex))
              }
            />
          );
        }

        const isDragging = dragNodeId === item.nodeId;
        const isDropTarget =
          overNodeId === item.nodeId && dragNodeId !== null && !isDragging;

        return (
          <div
            key={item.nodeId}
            title={overCapTitle}
            draggable
            onDragStart={(event) => {
              event.dataTransfer.effectAllowed = "move";
              event.dataTransfer.setData("text/plain", item.nodeId);
              setDragNodeId(item.nodeId);
            }}
            onDragOver={(event) => {
              if (!dragNodeId) return;
              event.preventDefault();
              event.dataTransfer.dropEffect = "move";
              if (overNodeId !== item.nodeId) setOverNodeId(item.nodeId);
            }}
            onDragLeave={() => {
              setOverNodeId((cur) => (cur === item.nodeId ? null : cur));
            }}
            onDrop={(event) => {
              event.preventDefault();
              event.stopPropagation();
              handleDrop(item.nodeId);
            }}
            onDragEnd={clearDrag}
            className={`nodrag relative cursor-grab rounded-md transition active:cursor-grabbing ${
              isDragging ? "opacity-40" : ""
            } ${
              isDropTarget
                ? "ring-2 ring-accent ring-offset-1 ring-offset-surface-dark"
                : ""
            } ${
              // omni 上限外的 chip：去饱和 + 半透明 + 琥珀色描边；hover 时通过
              // 父层 title 显示「超出上限不会使用」。配 detach 按钮提示用户主动
              // 移除超额素材。
              overCap
                ? "opacity-50 grayscale ring-1 ring-amber-400/45 ring-offset-1 ring-offset-surface-dark"
                : ""
            }`}
          >
            {chip}
            {overCap && (
              <span className="pointer-events-none absolute -bottom-1 -left-1 z-10 flex h-4 w-4 items-center justify-center rounded-full bg-amber-500/90 text-[10px] font-bold leading-none text-surface-dark shadow ring-1 ring-surface-dark">
                !
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}

/**
 * 参考素材悬停预览的尺寸。
 *
 * 旧值 140px 是从**旧面板**（竖排、窄）沿用的：那时 140px 已经和面板同宽。
 * libtv 的参考图悬停预览明显更大 —— 看用户 2026-10-01 给的实拍，
 * 一张四视图角色设定表能整张看清（约 200px 宽、接近正方形）。按同一量级取 240px，
 * 并给高度封顶 320px，避免竖构图长图把预览撑出屏幕。
 *
 * 语料侧没有写到这类预览的具体像素（`libtv §10_规格/UI_UX/CANVAS_UI_SPEC.md`
 * 只规定「浮动 UI 都是 portal」和缩略图列表本身），所以宽度按实拍量级定，不是抄来的数字。
 */
const REFERENCE_PREVIEW_WIDTH = 240;
const REFERENCE_PREVIEW_MAX_HEIGHT = 320;

function useHoverPreviewPos(
  buttonRef: React.RefObject<HTMLElement | null>,
  width: number,
) {
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const PREVIEW_OFFSET = 10;
  const show = useCallback(() => {
    const rect = buttonRef.current?.getBoundingClientRect();
    if (!rect) return;
    const left = Math.max(
      8,
      Math.min(
        window.innerWidth - width - 8,
        rect.left + rect.width / 2 - width / 2,
      ),
    );
    const top = rect.top - PREVIEW_OFFSET;
    setPos({ left, top });
  }, [buttonRef, width]);
  const hide = useCallback(() => setPos(null), []);
  return { pos, show, hide };
}

interface ReferenceImageChipProps {
  item: Extract<ReferenceMediaItem, { kind: "image" }>;
  index: number;
  /** 给角标显示自定义文案（如「首帧」「尾帧」）。未设置时使用数字角标。 */
  slotLabel?: string;
  /** 整行是否混了多种类型；混了才在角标数字前加单字类型前缀。 */
  mixedKinds?: boolean;
  onFocus: (nodeId: string) => void;
  onDetach: (nodeId: string) => void;
  /** 点缩略图 → 把 `@图片N` 写进提示词。 */
  onInsert: () => void;
}

function ReferenceImageChip({
  item,
  index,
  slotLabel,
  mixedKinds,
  onFocus,
  onDetach,
  onInsert,
}: ReferenceImageChipProps) {
  const buttonRef = useRef<HTMLButtonElement>(null);
  const { pos, show, hide } = useHoverPreviewPos(buttonRef, REFERENCE_PREVIEW_WIDTH);
  const label =
    item.displayName?.trim() || slotLabel || `引用 ${index + 1}`;

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        onClick={(event) => {
          event.stopPropagation();
          // 点缩略图 = 把这张引用写进提示词（`@图片N`）；想看上游节点用双击，
          // 免得「插引用」和「跳节点」抢同一个手势。
          onInsert();
        }}
        onDoubleClick={(event) => {
          event.stopPropagation();
          onFocus(item.nodeId);
        }}
        onMouseEnter={show}
        onMouseLeave={hide}
        className={`nodrag ${NODE_REFERENCE_MEDIA_CHIP_CLASS}`}
        title={label}
      >
        <img
          src={resolveImageDisplayUrl(item.imageUrl)}
          alt={label}
          className="h-full w-full object-cover"
          draggable={false}
        />
        <ReferenceChipNumberBadge typeIndex={index + 1} kind="image" mixedKinds={mixedKinds} />
        {/* 首尾帧角标是结构信息（不是序号），压在右下角，与左上角序号并存。 */}
        {slotLabel ? (
          <span
            className="pointer-events-none absolute bottom-1 right-1 z-10 text-[9px] font-medium leading-none text-white"
            style={{ textShadow: "0 0 2px rgba(0,0,0,0.65), 0 1px 1px rgba(0,0,0,0.55)" }}
          >
            {slotLabel}
          </span>
        ) : null}
        <ReferenceDetachButton
          nodeId={item.nodeId}
          onDetach={onDetach}
          className={NODE_REFERENCE_MEDIA_DETACH_CLASS}
        />
      </button>
      {pos &&
        typeof document !== "undefined" &&
        createPortal(
          <div
            className="pointer-events-none fixed z-[400] -translate-y-full"
            style={{ left: pos.left, top: pos.top, width: REFERENCE_PREVIEW_WIDTH }}
          >
            <div className="overflow-hidden rounded-xl border border-white/15 bg-surface-dark/95 shadow-2xl backdrop-blur-sm">
              <img
                src={resolveImageDisplayUrl(item.imageUrl)}
                alt={label}
                className="block h-auto w-full object-contain"
                style={{ maxHeight: REFERENCE_PREVIEW_MAX_HEIGHT }}
                draggable={false}
              />
              {/* 大预览里补一行文件名/序号：缩略图放大后容易被误认成节点自己那张图。 */}
              <div className="flex items-center gap-1.5 border-t border-white/10 bg-black/35 px-2 py-1">
                <span className="shrink-0 rounded bg-white/12 px-1 text-[10px] leading-4 text-white/80">
                  {index + 1}
                </span>
                <span className="min-w-0 truncate text-[10px] leading-4 text-white/70">
                  {label}
                </span>
              </div>
            </div>
          </div>,
          document.body,
        )}
    </>
  );
}

interface ReferenceVideoChipProps {
  item: Extract<ReferenceMediaItem, { kind: "video" }>;
  index: number;
  /** 整行是否混了多种类型；混了才在角标数字前加单字类型前缀。 */
  mixedKinds?: boolean;
  onFocus: (nodeId: string) => void;
  onDetach: (nodeId: string) => void;
  /** 点缩略图 → 把 `@视频N` 写进提示词。 */
  onInsert: () => void;
}

function ReferenceVideoChip({
  item,
  index,
  mixedKinds,
  onFocus,
  onDetach,
  onInsert,
}: ReferenceVideoChipProps) {
  const buttonRef = useRef<HTMLButtonElement>(null);
  const { pos, show, hide } = useHoverPreviewPos(buttonRef, REFERENCE_PREVIEW_WIDTH);
  const label = item.displayName?.trim() || `视频引用 ${index + 1}`;

  // chip 缩略图：有 previewImageUrl 用静态图；否则用一个 muted 静止 <video>
  // 显示首帧。preload=metadata 让 Safari/Chrome 自动定位到首帧。
  const thumb = item.thumbUrl ? (
    <img
      src={resolveImageDisplayUrl(item.thumbUrl)}
      alt={label}
      className="h-full w-full object-cover"
      draggable={false}
    />
  ) : (
    <video
      src={resolveImageDisplayUrl(item.videoUrl)}
      className="h-full w-full object-cover"
      muted
      playsInline
      disablePictureInPicture
      disableRemotePlayback
      controlsList="nodownload noplaybackrate noremoteplayback"
      preload="metadata"
      draggable={false}
    />
  );

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        onClick={(event) => {
          event.stopPropagation();
          // 与图片 chip 同一口径：单击写引用，双击跳上游节点。
          onInsert();
        }}
        onDoubleClick={(event) => {
          event.stopPropagation();
          onFocus(item.nodeId);
        }}
        onMouseEnter={show}
        onMouseLeave={hide}
        className={`nodrag ${NODE_REFERENCE_MEDIA_CHIP_CLASS}`}
        title={label}
      >
        {thumb}
        <ReferenceChipNumberBadge typeIndex={index + 1} kind="video" mixedKinds={mixedKinds} />
        <ReferenceChipDurationBadge kind="video" durationMs={item.durationMs} />
        <ReferenceDetachButton
          nodeId={item.nodeId}
          onDetach={onDetach}
          className={NODE_REFERENCE_MEDIA_DETACH_CLASS}
        />
      </button>
      {pos &&
        typeof document !== "undefined" &&
        createPortal(
          <div
            className="pointer-events-none fixed z-[400] -translate-y-full"
            style={{ left: pos.left, top: pos.top, width: REFERENCE_PREVIEW_WIDTH }}
          >
            <div className="overflow-hidden rounded-xl border border-white/15 bg-surface-dark/95 shadow-2xl backdrop-blur-sm">
              {/* hover 时 autoplay + loop + muted —— 不弹声音不打扰其它正在
                  播放的 audio chip。 */}
              <video
                src={resolveImageDisplayUrl(item.videoUrl)}
                autoPlay
                loop
                muted
                playsInline
                disablePictureInPicture
                disableRemotePlayback
                controlsList="nodownload noplaybackrate noremoteplayback"
                className="block h-auto w-full object-contain"
                style={{ maxHeight: REFERENCE_PREVIEW_MAX_HEIGHT }}
              />
            </div>
          </div>,
          document.body,
        )}
    </>
  );
}

interface ReferenceAudioChipProps {
  item: Extract<ReferenceMediaItem, { kind: "audio" }>;
  index: number;
  isPlaying: boolean;
  /** 整行是否混了多种类型；混了才在角标数字前加单字类型前缀。 */
  mixedKinds?: boolean;
  onToggle: (playing: boolean) => void;
  onDetach: (nodeId: string) => void;
  /** 点缩略图 → 把 `@音频N` 写进提示词。 */
  onInsert: () => void;
}

function ReferenceAudioChip({
  item,
  index,
  isPlaying,
  mixedKinds,
  onToggle,
  onDetach,
  onInsert,
}: ReferenceAudioChipProps) {
  // 用 ref 持有一个 HTMLAudioElement —— 比挂在 DOM 上的 <audio> 简单：可以
  // 直接 .play()/.pause()，也方便处理同时只放一个的逻辑（父层告诉这个
  // chip 它不再是当前正在播的）。
  const audioRef = useRef<HTMLAudioElement | null>(null);
  if (audioRef.current === null && typeof Audio !== "undefined") {
    audioRef.current = new Audio();
  }

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    const src = resolveImageDisplayUrl(item.audioUrl);
    if (audio.src !== src) {
      audio.src = src;
    }
  }, [item.audioUrl]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    if (isPlaying) {
      void audio.play().catch(() => {
        // 自动播放被浏览器拦或资源加载失败 —— 回滚父层状态。
        onToggle(false);
      });
    } else {
      audio.pause();
    }
  }, [isPlaying, onToggle]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    const handleEnded = () => onToggle(false);
    audio.addEventListener("ended", handleEnded);
    return () => audio.removeEventListener("ended", handleEnded);
  }, [onToggle]);

  // 卸载时停掉播放，避免脏状态留在浏览器。
  useEffect(() => {
    return () => {
      const audio = audioRef.current;
      if (!audio) return;
      audio.pause();
      audio.src = "";
    };
  }, []);

  const label = item.displayName?.trim() || `音频引用 ${index + 1}`;

  return (
    <button
      type="button"
      onClick={(event) => {
        event.stopPropagation();
        // 与图片 / 视频 chip 同一口径：**单击写引用**，试听退到双击。
        // 三个 chip 用同一个手势，用户不必记「哪个能插、哪个只能听」；试听是附带的
        // 小功能，写进提示词才是这排缩略图的主要用途。
        onInsert();
      }}
      onDoubleClick={(event) => {
        event.stopPropagation();
        onToggle(!isPlaying);
      }}
      className={`group/refmedia nodrag relative flex h-10 w-10 shrink-0 items-center justify-center overflow-hidden rounded-md border transition-colors ${
        isPlaying
          ? "border-accent/60 bg-[rgb(var(--accent-rgb)/0.15)]"
          : "border-white/10 bg-white/[0.04] hover:border-white/30"
      }`}
      title={`${label}（双击试听）`}
    >
      {isPlaying ? (
        <Pause className="h-4 w-4 text-accent" />
      ) : (
        <Music className="h-4 w-4 text-text-dark/90" />
      )}
      <ReferenceChipNumberBadge typeIndex={index + 1} kind="audio" mixedKinds={mixedKinds} />
      <ReferenceChipDurationBadge kind="audio" durationMs={item.durationMs} />
      <ReferenceDetachButton
        nodeId={item.nodeId}
        onDetach={onDetach}
        className={NODE_REFERENCE_MEDIA_DETACH_CLASS}
      />
    </button>
  );
}
