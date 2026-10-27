// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useMemo, useState } from "react";
import {
  Camera,
  CloudRain,
  Film,
  Layers,
  Search,
  ShoppingBag,
  Sparkles,
  Swords,
  Wand2,
  X,
  Zap,
} from "lucide-react";
import { toast } from "sonner";

import {
  CAMERA_MOVEMENT_PRESETS,
  resolveCameraPresetVideoUrl,
  type CameraMovementPreset,
} from "@/features/canvas/domain/cameraMovementPresets";
import {
  cameraDirectionText,
  cameraPresetForPrompt,
  setCameraDirection,
} from "@/features/canvas/domain/promptCamera";
import {
  EFFECT_CATEGORIES,
  EFFECT_PRESETS,
  effectCategoryLabel,
  effectPreviewTemplateId,
  filterEffectPresets,
  type EffectCategoryId,
  type EffectPreset,
} from "@/features/canvas/domain/effectPresets";
import {
  clearEffectDirection,
  effectDirectionText,
  setEffectDirection,
} from "@/features/canvas/domain/promptEffects";
import {
  EFFECT_TEMPLATES,
  filterEffectTemplates,
  type EffectTemplate,
} from "@/features/canvas/domain/effectTemplates";
import { useFreezoneVideoCameraTemplates } from "@/features/canvas/hooks/useFreezoneVideoCameraTemplates";
import {
  isImageGenNode,
  isVideoNode,
} from "@/features/canvas/domain/canvasNodes";
import { selectCanvasNodeById } from "@/features/canvas/application/canvasNodeIndex";
import { useCanvasStore } from "@/stores/canvasStore";

type LibraryTab = "all" | "camera" | "templates" | EffectCategoryId;

const CATEGORY_ICONS = {
  cinematic: Film,
  lighting: Sparkles,
  particles: Zap,
  elements: Swords,
  impact: Swords,
  commerce: ShoppingBag,
  weather: CloudRain,
} as const;

const CATEGORY_TINTS: Record<EffectCategoryId, string> = {
  cinematic: "from-orange-500/25 to-rose-500/10 text-orange-200",
  lighting: "from-amber-400/25 to-yellow-500/10 text-amber-200",
  particles: "from-sky-400/25 to-cyan-500/10 text-sky-200",
  elements: "from-emerald-400/25 to-teal-500/10 text-emerald-200",
  impact: "from-red-500/25 to-fuchsia-500/10 text-rose-200",
  commerce: "from-violet-400/25 to-indigo-500/10 text-violet-200",
  weather: "from-slate-300/20 to-blue-500/10 text-slate-200",
};

const TAB_LABELS: ReadonlyArray<{ id: LibraryTab; label: string }> = [
  { id: "all", label: "全部" },
  { id: "templates", label: "镜头模板" },
  ...EFFECT_CATEGORIES.map((category) => ({
    id: category.id as LibraryTab,
    label: category.label,
  })),
  { id: "camera", label: "镜头运镜" },
];

function nodePrompt(node: unknown): string {
  const data = (node as { data?: { prompt?: unknown } } | null)?.data;
  return typeof data?.prompt === "string" ? data.prompt : "";
}

/** 模板卡：静止时只加载封面，悬停才换成会动的预览视频。 */
function TemplatePreview({
  template,
  className = "aspect-square",
}: {
  template: EffectTemplate;
  className?: string;
}) {
  const [hover, setHover] = useState(false);
  return (
    <div
      className={`relative overflow-hidden bg-black/40 ${className}`}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      {hover ? (
        <video
          src={template.videoUrl}
          autoPlay
          muted
          loop
          playsInline
          preload="none"
          className="size-full object-cover"
        />
      ) : (
        <img
          src={template.posterUrl}
          alt=""
          loading="lazy"
          className="size-full object-cover transition-transform duration-500 group-hover:scale-[1.03]"
        />
      )}
    </div>
  );
}

/**
 * 画布里的特效库：镜头运镜 + 画面特效两类卡片。
 *
 * 点一张不新建节点，而是把对应的运镜段或特效描述写进当前选中节点的提示词。
 * 运镜走 `promptCamera.setCameraDirection`（与参数面板同一套字段）；特效走
 * `promptEffects.setEffectDirection`（已有分镜段时折进光影段，不增加段数）。
 */
export function EffectLibraryModal({
  open,
  project,
  onClose,
}: {
  open: boolean;
  project: string;
  onClose: () => void;
}) {
  const { templates } = useFreezoneVideoCameraTemplates(project);
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState<LibraryTab>("all");
  const selectedNode = useCanvasStore((state) =>
    selectCanvasNodeById(state.nodes, state.selectedNodeId),
  );
  const selectedKind: "image" | "video" | null = selectedNode
    ? isImageGenNode(selectedNode)
      ? "image"
      : isVideoNode(selectedNode)
        ? "video"
        : null
    : null;
  const canApply = selectedKind !== null;

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  const cameraPresets = useMemo<CameraMovementPreset[]>(
    () => (templates.length > 0 ? templates : [...CAMERA_MOVEMENT_PRESETS]),
    [templates],
  );
  const filteredEffects = useMemo(
    () => filterEffectPresets(EFFECT_PRESETS, query),
    [query],
  );
  const filteredTemplates = useMemo(
    () => filterEffectTemplates(EFFECT_TEMPLATES, query),
    [query],
  );
  const templateById = useMemo(
    () => new Map(EFFECT_TEMPLATES.map((template) => [template.id, template])),
    [],
  );
  const filteredCameras = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return cameraPresets;
    return cameraPresets.filter((preset) =>
      `${preset.label} ${preset.promptFragment} 镜头 运镜 摄影机`
        .toLowerCase()
        .includes(q),
    );
  }, [cameraPresets, query]);

  const currentPrompt = nodePrompt(selectedNode);
  const currentCameraId = useMemo(
    () => cameraPresetForPrompt(cameraPresets, currentPrompt)?.id ?? null,
    [cameraPresets, currentPrompt],
  );
  const currentEffectText = useMemo(
    () => effectDirectionText(currentPrompt),
    [currentPrompt],
  );
  const currentCameraText = useMemo(
    () => cameraDirectionText(currentPrompt),
    [currentPrompt],
  );

  const counts = useMemo(() => {
    const map: Record<string, number> = {
      all: cameraPresets.length + EFFECT_PRESETS.length + EFFECT_TEMPLATES.length,
      camera: cameraPresets.length,
      templates: EFFECT_TEMPLATES.length,
    };
    for (const preset of EFFECT_PRESETS) {
      map[preset.category] = (map[preset.category] ?? 0) + 1;
    }
    return map;
  }, [cameraPresets.length]);

  if (!open) return null;

  const showCameras = tab === "all" || tab === "camera";
  const showTemplates = tab === "all" || tab === "templates";
  const showEffects = tab !== "camera" && tab !== "templates";
  const visibleCameras = showCameras ? filteredCameras : [];
  const visibleTemplates = showTemplates ? filteredTemplates : [];
  const visibleEffects = showEffects
    ? filteredEffects.filter((preset) => tab === "all" || preset.category === tab)
    : [];
  const totalVisible =
    visibleCameras.length + visibleTemplates.length + visibleEffects.length;

  const applyCamera = (preset: CameraMovementPreset) => {
    if (!selectedNode || !canApply) {
      toast.info("先在画布上选中一个图片或视频节点，再把镜头特效写进提示词");
      return;
    }
    const next = setCameraDirection(currentPrompt, preset.promptFragment);
    useCanvasStore.getState().updateNodeData(selectedNode.id, { prompt: next });
    toast.success(`已把「${preset.label}」写进当前节点的提示词`);
  };

  const applyEffect = (preset: EffectPreset) => {
    if (!selectedNode || !canApply) {
      toast.info("先在画布上选中一个图片或视频节点，再点特效");
      return;
    }
    if (selectedKind === "image" && preset.scope === "video") {
      toast.info(`「${preset.label}」需要时间维度，请选中视频节点再套用`);
      return;
    }
    const isActive = currentEffectText === preset.promptFragment;
    const next = isActive
      ? clearEffectDirection(currentPrompt)
      : setEffectDirection(currentPrompt, preset.promptFragment);
    useCanvasStore.getState().updateNodeData(selectedNode.id, { prompt: next });
    toast.success(
      isActive
        ? `已从当前节点移除「${preset.label}」`
        : `已把「${preset.label}」写进当前节点的提示词`,
    );
  };

  const applyTemplate = (template: EffectTemplate) => {
    if (!selectedNode || !canApply) {
      toast.info("先在画布上选中一个图片或视频节点，再套用模板");
      return;
    }
    const next =
      template.target === "camera"
        ? setCameraDirection(currentPrompt, template.promptFragment)
        : setEffectDirection(currentPrompt, template.promptFragment);
    useCanvasStore.getState().updateNodeData(selectedNode.id, { prompt: next });
    toast.success(`已把「${template.label}」写进当前节点的提示词`);
  };

  return (
    <div
      className="fixed inset-0 z-[95] flex items-center justify-center bg-black/60 p-6 backdrop-blur-[2px]"
      role="dialog"
      aria-modal="true"
      aria-label="特效库"
      onClick={onClose}
    >
      <div
        className="flex h-[min(760px,88vh)] w-[min(1180px,95vw)] flex-col overflow-hidden rounded-[16px] border border-white/[0.12] bg-[#101114]/97 text-white shadow-[0_30px_90px_rgba(0,0,0,0.6)]"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="flex shrink-0 items-start justify-between gap-4 border-b border-white/[0.07] px-6 py-4">
          <div className="min-w-0">
            <h2 className="flex items-center gap-2 text-[17px] font-semibold">
              特效库
              <span className="rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 text-[10px] font-normal text-white/45">
                {EFFECT_PRESETS.length} 个画面特效 · {EFFECT_TEMPLATES.length} 个模板 ·{" "}
                {cameraPresets.length} 个运镜
              </span>
            </h2>
            <p className="mt-0.5 truncate text-[11.5px] text-white/40">
              {canApply
                ? `选中节点：${String(selectedNode?.data.displayName ?? selectedNode?.id)} · 点一张写入提示词`
                : "先在画布上选中一个图片或视频节点，再点特效"}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <div className="relative">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-white/35" />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索特效、模板或运镜"
                aria-label="搜索特效、模板或运镜"
                className="h-8 w-[200px] rounded-lg border border-white/10 bg-white/[0.04] pl-8 pr-2 text-[12px] text-white outline-none placeholder:text-white/30 focus:border-white/25"
              />
            </div>
            <button
              type="button"
              aria-label="关闭"
              onClick={onClose}
              className="inline-flex size-8 items-center justify-center rounded-lg text-white/55 transition-colors hover:bg-white/10 hover:text-white"
            >
              <X className="size-4" />
            </button>
          </div>
        </header>

        <div className="ui-scrollbar flex shrink-0 items-center gap-1.5 overflow-x-auto border-b border-white/[0.06] px-6 py-2.5">
          {TAB_LABELS.map((item) => {
            const active = tab === item.id;
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => setTab(item.id)}
                aria-pressed={active}
                className={`inline-flex shrink-0 items-center gap-1.5 rounded-full border px-3 py-1.5 text-[12px] transition-colors ${
                  active
                    ? "border-white/25 bg-white/[0.12] text-white"
                    : "border-transparent text-white/50 hover:bg-white/[0.06] hover:text-white/80"
                }`}
              >
                {item.label}
                <span className="text-[10px] text-white/35">{counts[item.id] ?? 0}</span>
              </button>
            );
          })}
          {currentEffectText ? (
            <button
              type="button"
              onClick={() => {
                if (!selectedNode) return;
                useCanvasStore
                  .getState()
                  .updateNodeData(selectedNode.id, {
                    prompt: clearEffectDirection(currentPrompt),
                  });
                toast.success("已清除当前节点的特效");
              }}
              className="ml-auto inline-flex shrink-0 items-center gap-1 rounded-full border border-white/10 px-3 py-1.5 text-[11.5px] text-white/50 transition-colors hover:border-white/25 hover:text-white"
            >
              <X className="size-3" />
              清除特效
            </button>
          ) : null}
        </div>

        <div className="ui-scrollbar min-h-0 flex-1 overflow-y-auto px-6 py-5">
          {totalVisible === 0 ? (
            <p className="py-20 text-center text-[12.5px] text-white/35">
              没有匹配的特效。
            </p>
          ) : (
            <div className="flex flex-col gap-5">
              {visibleTemplates.length > 0 ? (
                <section>
                  <div className="mb-2 flex items-center gap-2 text-[11px] font-medium uppercase tracking-wide text-white/35">
                    <Layers className="size-3.5" />
                    镜头模板
                    <span className="text-white/20">{visibleTemplates.length}</span>
                    <span className="normal-case tracking-normal text-white/25">
                      悬停看动图
                    </span>
                  </div>
                  <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
                    {visibleTemplates.map((template) => {
                      const active =
                        template.target === "camera"
                          ? currentCameraText === template.promptFragment
                          : currentEffectText === template.promptFragment;
                      return (
                        <button
                          key={template.id}
                          type="button"
                          onClick={() => applyTemplate(template)}
                          aria-pressed={active}
                          className={`group overflow-hidden rounded-[12px] border text-left transition-colors ${
                            active
                              ? "border-white/40 bg-white/[0.08]"
                              : "border-white/[0.08] bg-white/[0.03] hover:border-white/20 hover:bg-white/[0.05]"
                          }`}
                        >
                          <div className="relative">
                            <TemplatePreview template={template} />
                            <span className="absolute left-2 top-2 rounded-full bg-black/55 px-1.5 py-0.5 text-[9px] text-white/80">
                              {template.target === "camera" ? "运镜" : "特效"}
                            </span>
                            {active ? (
                              <span className="absolute bottom-2 right-2 rounded-full bg-black/60 px-1.5 py-0.5 text-[9px] text-white/85">
                                已使用
                              </span>
                            ) : null}
                          </div>
                          <div className="p-3">
                            <p className="truncate text-[13px] font-medium">{template.label}</p>
                            <p className="mt-0.5 line-clamp-2 text-[10.5px] leading-relaxed text-white/40">
                              {template.promptFragment}
                            </p>
                            <p className="mt-1 truncate text-[9px] text-white/25">
                              参考 @{template.author}
                            </p>
                          </div>
                        </button>
                      );
                    })}
                  </div>
                </section>
              ) : null}

              {visibleCameras.length > 0 ? (
                <section>
                  <div className="mb-2 flex items-center gap-2 text-[11px] font-medium uppercase tracking-wide text-white/35">
                    <Camera className="size-3.5" />
                    镜头运镜
                    <span className="text-white/20">{visibleCameras.length}</span>
                  </div>
                  <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
                    {visibleCameras.map((preset) => {
                      const active = currentCameraId === preset.id;
                      const previewUrl = resolveCameraPresetVideoUrl(preset);
                      return (
                        <button
                          key={preset.id}
                          type="button"
                          onClick={() => applyCamera(preset)}
                          aria-pressed={active}
                          className={`group overflow-hidden rounded-[12px] border text-left transition-colors ${
                            active
                              ? "border-white/40 bg-white/[0.08]"
                              : "border-white/[0.08] bg-white/[0.03] hover:border-white/20 hover:bg-white/[0.05]"
                          }`}
                        >
                          <div className="relative aspect-video overflow-hidden bg-black/40">
                            {previewUrl ? (
                              <video
                                src={previewUrl}
                                muted
                                loop
                                playsInline
                                preload="metadata"
                                className="size-full object-cover"
                                onMouseEnter={(event) => {
                                  void event.currentTarget.play().catch(() => undefined);
                                }}
                                onMouseLeave={(event) => {
                                  event.currentTarget.pause();
                                  event.currentTarget.currentTime = 0;
                                }}
                              />
                            ) : (
                              <div className="flex size-full items-center justify-center text-white/20">
                                <Wand2 className="size-6" />
                              </div>
                            )}
                            {active ? (
                              <span className="absolute left-2 top-2 rounded-full bg-black/60 px-1.5 py-0.5 text-[9px] text-white/80">
                                已使用
                              </span>
                            ) : null}
                          </div>
                          <div className="p-3">
                            <p className="truncate text-[13px] font-medium">{preset.label}</p>
                            <p className="mt-0.5 truncate text-[10.5px] text-white/35">
                              {preset.promptFragment}
                            </p>
                          </div>
                        </button>
                      );
                    })}
                  </div>
                </section>
              ) : null}

              {visibleEffects.length > 0 ? (
                <section>
                  <div className="mb-2 flex items-center gap-2 text-[11px] font-medium uppercase tracking-wide text-white/35">
                    <Sparkles className="size-3.5" />
                    画面特效
                    <span className="text-white/20">{visibleEffects.length}</span>
                  </div>
                  <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
                    {visibleEffects.map((preset) => {
                      const active = currentEffectText === preset.promptFragment;
                      const Icon = CATEGORY_ICONS[preset.category];
                      const previewTemplate =
                        templateById.get(effectPreviewTemplateId(preset.id) ?? "") ?? null;
                      const disabled =
                        selectedKind === "image" && preset.scope === "video";
                      return (
                        <button
                          key={preset.id}
                          type="button"
                          onClick={() => applyEffect(preset)}
                          aria-pressed={active}
                          disabled={disabled}
                          title={disabled ? "视频专用特效，图片节点用不了" : undefined}
                          className={`group overflow-hidden rounded-[12px] border text-left transition-colors ${
                            active
                              ? "border-white/40 bg-white/[0.08]"
                              : "border-white/[0.08] bg-white/[0.03] hover:border-white/20 hover:bg-white/[0.05]"
                          } ${disabled ? "cursor-not-allowed opacity-35" : ""}`}
                        >
                          <div className="relative">
                            {previewTemplate ? (
                              <TemplatePreview
                                template={previewTemplate}
                                className="aspect-[16/7]"
                              />
                            ) : (
                              <div
                                className={`flex aspect-[16/7] items-center justify-center bg-gradient-to-br ${CATEGORY_TINTS[preset.category]}`}
                              >
                                <Icon className="size-7 opacity-70" />
                              </div>
                            )}
                            <span className="absolute left-2 top-2 rounded-full bg-black/45 px-1.5 py-0.5 text-[9px] text-white/75">
                              {effectCategoryLabel(preset.category)}
                            </span>
                            {preset.scope === "video" ? (
                              <span className="absolute right-2 top-2 rounded-full bg-black/45 px-1.5 py-0.5 text-[9px] text-white/60">
                                视频
                              </span>
                            ) : null}
                            {previewTemplate ? (
                              <span className="absolute bottom-2 left-2 rounded-full bg-black/55 px-1.5 py-0.5 text-[9px] text-white/55">
                                示意
                              </span>
                            ) : null}
                            {active ? (
                              <span className="absolute bottom-2 right-2 rounded-full bg-black/55 px-1.5 py-0.5 text-[9px] text-white/85">
                                已使用
                              </span>
                            ) : null}
                          </div>
                          <div className="p-3">
                            <p className="truncate text-[13px] font-medium">{preset.label}</p>
                            <p className="mt-0.5 line-clamp-2 text-[10.5px] leading-relaxed text-white/40">
                              {preset.promptFragment}
                            </p>
                          </div>
                        </button>
                      );
                    })}
                  </div>
                </section>
              ) : null}
            </div>
          )}
        </div>

        <footer className="shrink-0 border-t border-white/[0.06] px-6 py-2.5 text-[10.5px] text-white/30">
          特效写进提示词；分镜提示词已有 6/8 段时折进「光影几何」，不会增加段数。模板动图已落盘本地，悬停播放。
        </footer>
      </div>
    </div>
  );
}
