// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";

import { uploadFreezoneImage } from "@/api/ops";
import { CANVAS_NODE_TYPES, type CanvasNodeData } from "@/features/canvas/domain/canvasNodes";
import { readUrl } from "@/lib/url-params";
import { useFreezoneImageModels } from "@/features/canvas/hooks/useFreezoneImageModels";
import {
  imageModelSupportsMode,
  runtimeImageModelFromOption,
} from "@/features/canvas/models/runtimeImageModels";
import type { ModelOption } from "@/features/canvas/ui/ProviderModelPicker";
import { useCanvasStore } from "@/stores/canvasStore";
import { GENERATION_CONCURRENCY_DEFAULT } from "@/features/canvas/application/generationConcurrency";
import { compileDirectorPrompt } from "./directorPromptEngine";
import { currentDirectorControls } from "./directorStyleStore";
import { ExpressionHeadPreview, compileExpressionRigContract } from "./ExpressionHeadPreview";
import {
  affectFromMoodCell,
  moodCellFromAffect,
  moodDirectionModifier,
  MOOD_CENTER_ITEM,
  MOOD_CENTER_ROW,
  MOOD_GRID,
  MOOD_GRID_COLUMNS,
  MOOD_GRID_ROWS,
} from "./emotionMoodGrid";
import { detectFaceRegions } from "./expressionFaceDetection";
import {
  containedImageRect,
  moveNormalizedRegion,
  normalizedPointFromClient,
  normalizedRegionFromPoints,
  resizeNormalizedRegion,
  type NormalizedPoint,
} from "./expressionRegionSelection";
import {
  EXPRESSION_CONTROL_REVISION,
  EXPRESSION_CONTROL_SOURCE,
  FINE_EXPRESSIONS,
  describeExpressionMix,
  expressionByKey,
  type FineExpression,
} from "./expressionControlEngine";

const EXPRESSIONS = FINE_EXPRESSIONS;
export const EXPRESSION_PREVIEW_RENDERER = "ict-facekit-57-morph.single-head" as const;
type ExpressionPreset = FineExpression;

interface ViewPreset {
  key: string;
  label: string;
  prompt: string;
}

const VIEWS: ViewPreset[] = [
  { key: "locked", label: "锁定原镜头", prompt: "preserve the exact original camera angle, framing and head pose" },
  { key: "close_up", label: "正面近景", prompt: "front-facing close-up portrait, eyes near camera, head and shoulders" },
  { key: "three_quarter", label: "3/4 侧近景", prompt: "three-quarter close-up portrait, natural 45-degree head angle" },
  { key: "profile", label: "正侧面", prompt: "clean side-profile close-up portrait, 90-degree head angle" },
];

export interface CharacterRegion {
  id: string;
  label: string;
  x: number;
  y: number;
  width: number;
  height: number;
}

interface RegionGesture {
  pointerId: number;
  mode: "draw" | "move" | "resize";
  start: NormalizedPoint;
  regionId?: string;
  initialRegion?: CharacterRegion;
}

const DEFAULT_SELECTED = new Set(["neutral", "happy", "sad", "angry", "surprised", "fear"]);

export function expressionModelResolutionOptions(model: ModelOption | null): string[] {
  if (!model) return [];
  const runtime = runtimeImageModelFromOption(model);
  if (!imageModelSupportsMode(runtime, "image_to_image")) return [];
  return runtime.resolutions.map((option) => option.value);
}

export function pickExpressionResolution(
  requested: string,
  model: ModelOption | null,
): string {
  if (!model) return "";
  const runtime = runtimeImageModelFromOption(model);
  const options = expressionModelResolutionOptions(model);
  return options.includes(requested)
    ? requested
    : runtime.defaultResolution || options[0] || "";
}

function affectLabel(x: number, y: number, expressionKey: string): string {
  const activation = y > 0.45 ? "激动" : y < -0.45 ? "平静" : "克制";
  const valence = x < -0.45 ? "疏离" : x > 0.45 ? "亲近" : "复杂";
  const expression = expressionByKey(expressionKey);
  if (Math.abs(x) < 0.18 && Math.abs(y) < 0.18 && expressionKey === "neutral") return "淡然自若";
  return `${activation} · ${valence} · ${expression.label}`;
}

export function affectPrompt(
  sourceName: string,
  region: CharacterRegion,
  x: number,
  y: number,
  moodLabel: string,
  moodExpressionPrompt: string,
  primaryKey: string,
  secondaryKey: string,
  expressionScale: number,
  secondaryWeight: number,
  rigPrompt: string,
): string {
  // Industry pattern for multi-ref expression edit (GPT-Image / Nano Banana / Seedream):
  // short "change X + preserve Y", let Image 1 carry identity and Image 2 carry geometry.
  // Long FACS essays + stacked LOCK lines fight dual-layer compact and identity models.
  const semantic = `${moodLabel} · ${affectLabel(x, y, primaryKey)}`;
  const intensity =
    expressionScale >= 1.15 ? "strong" : expressionScale <= 0.55 ? "subtle" : "clear";
  const secondary = secondaryKey === "none" ? null : expressionByKey(secondaryKey);
  const blend =
    secondary && secondaryWeight > 0.08
      ? `, lightly blended with ${secondary.prompt}`
      : "";
  const rigHint = rigPrompt.trim() ? ` Match Image 2 geometry: ${rigPrompt.trim()}.` : "";
  // x/y already folded into semantic via affectLabel.
  return [
    `Change only ${region.label}'s facial expression in Image 1 to ${semantic} (${intensity}${blend}).`,
    `Cues: ${moodExpressionPrompt}.`,
    `Image 2 is a 3D face-geometry guide for brows/eyes/mouth/jaw only — ignore gray material and bald head.${rigHint}`,
    `Keep the same person as ${sourceName}: hair, costume, pose, camera, lighting and background. Do not restyle or return a near-copy of Image 1.`,
  ].join("\n");
}

function sourceImageUrl(data: Record<string, unknown>): string | null {
  if (typeof data.videoUrl === "string" && data.videoUrl.trim()) return null;
  for (const key of ["imageUrl", "previewImageUrl", "referenceImageUrl"]) {
    const value = data[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return null;
}

function sourceTitle(data: Record<string, unknown>): string {
  for (const key of ["take_original_display_name", "displayName", "sourceFileName", "label"]) {
    const value = data[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return "角色基准图";
}

export function compileExpressionStyleLockedPrompt(basePrompt: string): string {
  // affectPrompt is already a complete change+preserve edit brief.
  // Do not wrap it in extra LOCK scaffolding that crowds the dual-layer uplink.
  return basePrompt.trim();
}

async function canvasToPngFile(canvas: HTMLCanvasElement): Promise<File> {
  const blob = await new Promise<Blob>((resolve, reject) => canvas.toBlob((value) => value ? resolve(value) : reject(new Error("3D表情控制帧导出失败")), "image/png"));
  return new File([blob], `expression-control-${Date.now()}.png`, { type: "image/png" });
}

function expressionPrompt(
  sourceName: string,
  preset: ExpressionPreset,
  view: ViewPreset,
  intensity: number,
): string {
  const strength =
    intensity >= 1.15 ? "strong" : intensity <= 0.55 ? "subtle" : "clear";
  const viewLine =
    view.key === "locked"
      ? "Keep the original camera, framing and head pose."
      : `View: ${view.prompt}. Keep lighting, costume and body continuity.`;
  return [
    `Change only the facial expression of the person in Image 1 to ${preset.prompt} (${strength}).`,
    viewLine,
    `Same person as ${sourceName}: keep identity, hair, costume, lighting and background. One clean image, no text.`,
  ].join("\n");
}

export function ExpressionManagerPanel({
  onToast,
  onRequestClose,
  projectId = "",
  canvasId = "default",
}: {
  onToast: (message: string) => void;
  onRequestClose: () => void;
  projectId?: string;
  canvasId?: string;
}) {
  // Legacy matrix values remain read-only for backward-compatible node creation metadata.
  const [selectedKeys] = useState<Set<string>>(() => new Set(DEFAULT_SELECTED));
  const [selectedViews] = useState<Set<string>>(() => new Set(["locked"]));
  const [intensity] = useState(1);
  const [affectX, setAffectX] = useState(0);
  const [affectY, setAffectY] = useState(0);
  const [isAffectDragging, setIsAffectDragging] = useState(false);
  const [previewMoodCell, setPreviewMoodCell] = useState<{ row: number; col: number } | null>(null);
  const [primaryExpression, setPrimaryExpression] = useState("neutral");
  const [secondaryExpression] = useState("none");
  const [expressionScale] = useState(1);
  const [secondaryWeight] = useState(0.35);
  const [regions, setRegions] = useState<CharacterRegion[]>([]);
  const [activeRegionId, setActiveRegionId] = useState<string | null>(null);
  const [outputSize, setOutputSize] = useState("");
  const [outputCount, setOutputCount] = useState<1 | 2 | 4 | 6 | 8 | 12>(1);
  const [modelId, setModelId] = useState<string>("");
  const [imageViewport, setImageViewport] = useState({ left: 0, top: 0, width: 1, height: 1 });
  const [draftRegion, setDraftRegion] = useState<CharacterRegion | null>(null);
  const [faceDetectionStatus, setFaceDetectionStatus] = useState<"idle" | "loading" | "ready" | "empty" | "failed">("idle");
  const [visualMode, setVisualMode] = useState<"locate" | "preview">("locate");
  const [promptOpen, setPromptOpen] = useState(false);
  const imageRef = useRef<HTMLImageElement | null>(null);
  const lastAutoDetectedSourceRef = useRef<string | null>(null);
  const imageContainerRef = useRef<HTMLDivElement | null>(null);
  const imageOverlayRef = useRef<HTMLDivElement | null>(null);
  const regionGestureRef = useRef<RegionGesture | null>(null);
  const padRef = useRef<HTMLDivElement | null>(null);
  const affectCursorRef = useRef<HTMLSpanElement | null>(null);
  const pendingAffectRef = useRef<{ x: number; y: number } | null>(null);
  const affectGestureStartRef = useRef<{ x: number; y: number } | null>(null);
  const affectGestureMovedRef = useRef(false);
  const previewMoodCellRef = useRef<{ row: number; col: number } | null>(null);
  const previewSettleTimerRef = useRef<number | null>(null);
  const expressionCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const handleExpressionCanvasReady = useCallback((canvas: HTMLCanvasElement | null) => {
    expressionCanvasRef.current = canvas;
  }, []);
  const { models: imageModels } = useFreezoneImageModels();
  const expressionModels = useMemo(
    () => imageModels.filter((model) => {
      const runtime = runtimeImageModelFromOption(model);
      return imageModelSupportsMode(runtime, "image_to_image");
    }),
    [imageModels],
  );
  const selectedImageModel = useMemo(
    () => expressionModels.find((model) => model.id === modelId) ?? null,
    [expressionModels, modelId],
  );
  const outputSizeOptions = useMemo(
    () => expressionModelResolutionOptions(selectedImageModel),
    [selectedImageModel],
  );
  const selectedNodeId = useCanvasStore((state) => state.selectedNodeId);
  const nodes = useCanvasStore((state) => state.nodes);
  const selectedNode = useMemo(
    () => nodes.find((node) => node.id === selectedNodeId) ?? null,
    [nodes, selectedNodeId],
  );
  const selectedData = selectedNode?.data as Record<string, unknown> | undefined;
  const imageUrl = selectedData ? sourceImageUrl(selectedData) : null;
  const title = selectedData ? sourceTitle(selectedData) : "";
  const activeRegion = regions.find((item) => item.id === activeRegionId) ?? regions[0] ?? null;
  const selectedMood = previewMoodCell
    ? { row: previewMoodCell.row, col: previewMoodCell.col, item: MOOD_GRID[previewMoodCell.row * MOOD_GRID_COLUMNS + previewMoodCell.col] ?? MOOD_CENTER_ITEM }
    : moodCellFromAffect(affectX, affectY);
  // One character, one head: all 50 states stay on the same ICT 57-Morph model.
  // During a drag, update that head only when crossing a semantic cell instead of rendering React every frame.
  const previewAffect = previewMoodCell ? affectFromMoodCell(previewMoodCell.row, previewMoodCell.col) : { x: affectX, y: affectY };
  const previewPrimaryExpression = previewMoodCell ? selectedMood.item.primaryExpression : primaryExpression;
  const moodMixLabel = describeExpressionMix({
    primary: expressionByKey(primaryExpression),
    secondary: secondaryExpression === "none" ? null : expressionByKey(secondaryExpression),
    scale: expressionScale,
    secondaryWeight,
  });

  useEffect(() => {
    setRegions([]);
    setActiveRegionId(null);
    setDraftRegion(null);
    setFaceDetectionStatus("idle");
    setVisualMode("locate");
  }, [selectedNodeId, imageUrl]);

  useEffect(() => () => {
    if (previewSettleTimerRef.current !== null) window.clearTimeout(previewSettleTimerRef.current);
  }, []);

  useLayoutEffect(() => {
    if (isAffectDragging) return;
    const cursor = affectCursorRef.current;
    const pad = padRef.current;
    if (cursor && pad) {
      const rect = pad.getBoundingClientRect();
      cursor.style.transform = `translate3d(${((affectX + 1) / 2) * rect.width}px, ${((1 - affectY) / 2) * rect.height}px, 0) translate(-50%, -50%)`;
    }
  }, [affectX, affectY, isAffectDragging]);

  useEffect(() => {
    const container = imageContainerRef.current;
    const image = imageRef.current;
    if (!container || !image) return;
    const refresh = () => {
      const rect = container.getBoundingClientRect();
      setImageViewport(containedImageRect({
        containerWidth: rect.width,
        containerHeight: rect.height,
        naturalWidth: image.naturalWidth || rect.width,
        naturalHeight: image.naturalHeight || rect.height,
      }));
    };
    const observer = new ResizeObserver(refresh);
    observer.observe(container);
    image.addEventListener("load", refresh);
    refresh();
    return () => {
      observer.disconnect();
      image.removeEventListener("load", refresh);
    };
  }, [imageUrl]);

  useEffect(() => {
    if (modelId && expressionModels.some((model) => model.id === modelId)) return;
    if (expressionModels.length === 0) {
      if (modelId) setModelId("");
      return;
    }
    const inherited = typeof selectedData?.model === "string" ? selectedData.model : "";
    const resolved = expressionModels.find((model) => model.id === inherited)?.id
      ?? expressionModels[0]?.id
      ?? "";
    setModelId(resolved);
  }, [expressionModels, modelId, selectedData?.model]);

  useEffect(() => {
    const resolved = pickExpressionResolution(outputSize, selectedImageModel);
    if (resolved !== outputSize) setOutputSize(resolved);
  }, [outputSize, selectedImageModel]);

  const addRegionAt = (x: number, y: number) => {
    const width = 0.22;
    const height = 0.3;
    const region: CharacterRegion = {
      id: crypto.randomUUID(),
      label: `人物 ${regions.length + 1}`,
      x: Math.max(0, Math.min(1 - width, x - width / 2)),
      y: Math.max(0, Math.min(1 - height, y - height / 2)),
      width,
      height,
    };
    setRegions((current) => [...current, region]);
    setActiveRegionId(region.id);
    setVisualMode("preview");
  };

  const runFaceDetection = async () => {
    const image = imageRef.current;
    if (!image || faceDetectionStatus === "loading") return;
    setFaceDetectionStatus("loading");
    try {
      const detected = await detectFaceRegions(image);
      if (detected.length === 0) {
        setFaceDetectionStatus("empty");
        return;
      }
      const next = detected.map((region, index): CharacterRegion => ({
        id: crypto.randomUUID(),
        label: `人物 ${index + 1}`,
        ...region,
      }));
      setRegions(next);
      setActiveRegionId(next[0]?.id ?? null);
      setFaceDetectionStatus("ready");
      setVisualMode("preview");
    } catch (error) {
      console.warn("[expression] automatic face detection failed", error);
      setFaceDetectionStatus("failed");
    }
  };

  const imagePoint = (clientX: number, clientY: number): NormalizedPoint | null => {
    const bounds = imageOverlayRef.current?.getBoundingClientRect();
    return bounds ? normalizedPointFromClient(clientX, clientY, bounds) : null;
  };

  const beginRegionGesture = (
    event: ReactPointerEvent,
    mode: RegionGesture["mode"],
    region?: CharacterRegion,
  ) => {
    if (!imageUrl) return;
    const point = imagePoint(event.clientX, event.clientY);
    const overlay = imageOverlayRef.current;
    if (!point || !overlay) return;
    event.preventDefault();
    event.stopPropagation();
    overlay.setPointerCapture(event.pointerId);
    regionGestureRef.current = {
      pointerId: event.pointerId,
      mode,
      start: point,
      regionId: region?.id,
      initialRegion: region ? { ...region } : undefined,
    };
    if (region) setActiveRegionId(region.id);
    if (mode === "draw") {
      setDraftRegion({ id: "draft", label: "新人物", x: point.x, y: point.y, width: 0, height: 0 });
    }
  };

  const updateRegionGesture = (event: ReactPointerEvent) => {
    const gesture = regionGestureRef.current;
    if (!gesture || gesture.pointerId !== event.pointerId) return;
    const point = imagePoint(event.clientX, event.clientY);
    if (!point) return;
    if (gesture.mode === "draw") {
      const next = normalizedRegionFromPoints(gesture.start, point, 0);
      if (next) setDraftRegion({ id: "draft", label: "新人物", ...next });
      return;
    }
    if (!gesture.initialRegion || !gesture.regionId) return;
    const next = gesture.mode === "move"
      ? moveNormalizedRegion(
          gesture.initialRegion,
          point.x - gesture.start.x,
          point.y - gesture.start.y,
        )
      : resizeNormalizedRegion(gesture.initialRegion, point);
    setRegions((current) => current.map((item) => item.id === gesture.regionId ? { ...item, ...next } : item));
  };

  const endRegionGesture = (event: ReactPointerEvent) => {
    const gesture = regionGestureRef.current;
    if (!gesture || gesture.pointerId !== event.pointerId) return;
    if (gesture.mode === "draw" && draftRegion) {
      const completed = normalizedRegionFromPoints(
        { x: draftRegion.x, y: draftRegion.y },
        { x: draftRegion.x + draftRegion.width, y: draftRegion.y + draftRegion.height },
      );
      if (completed) {
        const id = crypto.randomUUID();
        const region: CharacterRegion = {
          id,
          label: `人物 ${regions.length + 1}`,
          ...completed,
        };
        setRegions((current) => [...current, region]);
        setActiveRegionId(id);
        setVisualMode("preview");
      }
    }
    regionGestureRef.current = null;
    setDraftRegion(null);
    if (imageOverlayRef.current?.hasPointerCapture(event.pointerId)) {
      imageOverlayRef.current.releasePointerCapture(event.pointerId);
    }
  };

  const updateAffectFromPointer = (clientX: number, clientY: number) => {
    const rect = padRef.current?.getBoundingClientRect();
    if (!rect) return;
    const point = {
      x: Math.max(-1, Math.min(1, ((clientX - rect.left) / rect.width) * 2 - 1)),
      y: Math.max(-1, Math.min(1, 1 - ((clientY - rect.top) / rect.height) * 2)),
    };
    pendingAffectRef.current = point;

    // The hot path is DOM-only: no rAF wait and no React render for cursor movement.
    const cursor = affectCursorRef.current;
    if (cursor) {
      cursor.style.transform = `translate3d(${Math.max(0, Math.min(rect.width, clientX - rect.left))}px, ${Math.max(0, Math.min(rect.height, clientY - rect.top))}px, 0) translate(-50%, -50%)`;
    }
    // React updates the single 3D head only when the snapped semantic cell changes.
    const mood = moodCellFromAffect(point.x, point.y);
    const previous = previewMoodCellRef.current;
    if (!previous || previous.row !== mood.row || previous.col !== mood.col) {
      const next = { row: mood.row, col: mood.col };
      previewMoodCellRef.current = next;
      setPreviewMoodCell(next);
    }
  };

  const beginAffectGesture = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    affectGestureStartRef.current = { x: event.clientX, y: event.clientY };
    affectGestureMovedRef.current = false;
    if (previewSettleTimerRef.current !== null) {
      window.clearTimeout(previewSettleTimerRef.current);
      previewSettleTimerRef.current = null;
    }
    setIsAffectDragging(true);
    updateAffectFromPointer(event.clientX, event.clientY);
  };

  const moveAffectGesture = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
    const start = affectGestureStartRef.current;
    if (start && Math.hypot(event.clientX - start.x, event.clientY - start.y) > 4) {
      affectGestureMovedRef.current = true;
    }
    updateAffectFromPointer(event.clientX, event.clientY);
  };

  const endAffectGesture = (event: ReactPointerEvent<HTMLDivElement>) => {
    const point = pendingAffectRef.current;
    if (point) {
      const mood = moodCellFromAffect(point.x, point.y);
      setAffectX(point.x);
      setAffectY(point.y);
      setPrimaryExpression(mood.item.primaryExpression);
      const next = { row: mood.row, col: mood.col };
      previewMoodCellRef.current = next;
      setPreviewMoodCell(next);
    }
    setIsAffectDragging(false);
    pendingAffectRef.current = null;
    affectGestureStartRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    previewSettleTimerRef.current = window.setTimeout(() => {
      previewMoodCellRef.current = null;
      setPreviewMoodCell(null);
      previewSettleTimerRef.current = null;
    }, 260);
  };

  const cancelAffectGesture = (event: ReactPointerEvent<HTMLDivElement>) => {
    setIsAffectDragging(false);
    pendingAffectRef.current = null;
    affectGestureStartRef.current = null;
    affectGestureMovedRef.current = false;
    previewMoodCellRef.current = null;
    setPreviewMoodCell(null);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  };

  const selectMoodCell = (row: number, col: number) => {
    if (previewSettleTimerRef.current !== null) {
      window.clearTimeout(previewSettleTimerRef.current);
      previewSettleTimerRef.current = null;
    }
    previewMoodCellRef.current = null;
    setPreviewMoodCell(null);
    const affect = affectFromMoodCell(row, col);
    const mood = moodCellFromAffect(affect.x, affect.y);
    setAffectX(affect.x);
    setAffectY(affect.y);
    setPrimaryExpression(mood.item.primaryExpression);
  };

  const createContinuousAffect = async (autoGenerate: boolean) => {
    if (!selectedNode || !imageUrl || !activeRegion) {
      onToast("请先选择角色图片，并在原图上拖框人物；也可以使用自动人脸识别");
      return;
    }
    if (!selectedImageModel) {
      onToast("当前没有已验证支持图生图的图片模型，请先在模型中心检测模型能力");
      return;
    }
    if (autoGenerate && !window.confirm(`将为 ${activeRegion.label} 提交 ${outputCount} 张图片，确认继续？`)) return;
    const expressionCanvas = expressionCanvasRef.current;
    if (!expressionCanvas) {
      setVisualMode("preview");
      onToast("已切换到3D表情预览；确认表情后再次点击，系统会把同一控制帧交给生成模型");
      return;
    }
    const projectId = readUrl().project;
    if (!projectId) {
      onToast("当前URL缺少项目，无法保存3D表情控制帧");
      return;
    }
    let expressionControlImageUrl: string;
    try {
      const controlFile = await canvasToPngFile(expressionCanvas);
      expressionControlImageUrl = (await uploadFreezoneImage(projectId, controlFile, controlFile.name)).url;
    } catch (error) {
      onToast(error instanceof Error ? error.message : "3D表情控制帧保存失败");
      return;
    }
    const store = useCanvasStore.getState();
    const width = Number(selectedNode.measured?.width ?? selectedNode.width ?? 320);
    const aspectRatio = typeof selectedData?.aspectRatio === "string" ? selectedData.aspectRatio : "1:1";
    const semantic = `${selectedMood.item.label} · ${affectLabel(affectX, affectY, primaryExpression)}`;
    const primary = expressionByKey(primaryExpression);
    const secondary = secondaryExpression === "none" ? null : expressionByKey(secondaryExpression);
    const mixLabel = describeExpressionMix({ primary, secondary, scale: expressionScale, secondaryWeight });
    const rigContract = compileExpressionRigContract(affectX, affectY, expressionScale);
    const expressionEditPrompt = affectPrompt(
      title,
      activeRegion,
      affectX,
      affectY,
      selectedMood.item.label,
      selectedMood.item.expressionPrompt,
      primaryExpression,
      secondaryExpression,
      expressionScale,
      secondaryWeight,
      rigContract.prompt,
    );
    // Expression adjustment is an image-edit operation, never a restyle operation.
    // It intentionally ignores the global Director Style panel.
    const director = {
      prompt: compileExpressionStyleLockedPrompt(expressionEditPrompt),
      revision: "expression-style-lock.v1",
      profile: "expression-geometry-edit",
      layers: { style: "source-locked", composition: "source-locked", lighting: "source-locked" },
      warnings: [] as string[],
    };
    const nodeId = store.addNode(CANVAS_NODE_TYPES.imageGen, {
      x: selectedNode.position.x + width + 100,
      y: selectedNode.position.y,
    }, {
      displayName: `🎭 ${activeRegion.label} · ${mixLabel} · ${semantic}`,
      prompt: director.prompt,
      compiledPromptPreview: director.prompt,
      director_style_id: "source-style-locked",
      director_prompt_revision: director.revision,
      director_model_profile: director.profile,
      director_layers: director.layers,
      director_warnings: director.warnings,
      referenceImageUrl: imageUrl,
      expressionControlImageUrl,
      expression_control_reference_role: "geometry_only_image_2",
      imageUrl: null,
      previewImageUrl: imageUrl,
      aspectRatio,
      requestAspectRatio: aspectRatio,
      ...(outputSize ? { size: outputSize } : {}),
      count: outputCount,
      ...(modelId ? { model: modelId } : {}),
      affect_schema: "continuous-affect.v3",
      affect_x_valence: Number(affectX.toFixed(3)),
      affect_y_activation: Number(affectY.toFixed(3)),
      affect_tone: primaryExpression,
      affect_label: semantic,
      affect_mood_key: selectedMood.item.key,
      affect_mood_label: selectedMood.item.label,
      affect_mood_cell: selectedMood.row === MOOD_CENTER_ROW ? null : { row: selectedMood.row, col: selectedMood.col },
      expression_control_revision: EXPRESSION_CONTROL_REVISION,
      expression_control_source: EXPRESSION_CONTROL_SOURCE,
      expression_primary: primaryExpression,
      expression_secondary: secondary?.key ?? null,
      expression_scale: Number(expressionScale.toFixed(2)),
      expression_secondary_weight: secondary ? Number(secondaryWeight.toFixed(2)) : 0,
      expression_mix_label: mixLabel,
      expression_rig_revision: rigContract.revision,
      expression_rig_weights: rigContract.weights,
      expression_rig_features: rigContract.features,
      expression_rig_prompt_contract: rigContract.prompt,
      affect_target_region: activeRegion,
      affect_source_node_id: selectedNode.id,
      expression_set_id: crypto.randomUUID(),
      expression_source_node_id: selectedNode.id,
      expression_key: `continuous:${primaryExpression}${secondary ? `+${secondary.key}` : ""}`,
      expression_label: mixLabel,
      expression_status: "draft",
      expression_identity_lock: true,
      canvas_auto_generate_once: autoGenerate,
    } as Partial<CanvasNodeData>);
    store.addEdge(selectedNode.id, nodeId);
    onToast(autoGenerate ? `已提交 ${activeRegion.label} · ${semantic} · ${outputCount} 张候选` : `已创建连续情绪节点：${semantic}，未产生费用`);
  };

  const createExpressionSet = (autoGenerate: boolean) => {
    if (!selectedNode || !imageUrl) {
      onToast("请先选择一张角色图片节点作为身份基准");
      return;
    }
    const chosen = EXPRESSIONS.filter((item) => selectedKeys.has(item.key));
    const chosenViews = VIEWS.filter((item) => selectedViews.has(item.key));
    if (chosen.length === 0 || chosenViews.length === 0) {
      onToast("至少选择一个表情和一个镜头视角");
      return;
    }
    const requestedJobs = chosen.flatMap((preset) => chosenViews.map((view) => ({ preset, view })));
    const jobs = autoGenerate ? requestedJobs.slice(0, 12) : requestedJobs;
    if (autoGenerate && requestedJobs.length > 12) {
      onToast(`本次最多排队生成 12 个节点；已从 ${requestedJobs.length} 个组合中取前 12 个`);
    }
    if (autoGenerate && !window.confirm(`将生成 ${jobs.length} 个付费图片节点（同时并发生成、并发上限 ${GENERATION_CONCURRENCY_DEFAULT}；有上下游顺序的按顺序跑），确认继续？`)) return;

    const store = useCanvasStore.getState();
    const setId = crypto.randomUUID();
    const width = selectedNode.measured?.width ?? selectedNode.width ?? 320;
    const height = selectedNode.measured?.height ?? selectedNode.height ?? 380;
    const startX = selectedNode.position.x + Number(width) + 100;
    const startY = selectedNode.position.y;
    const columnWidth = 360;
    const rowHeight = Math.max(Number(height), 400) + 36;
    const model = typeof selectedData?.model === "string" ? selectedData.model : undefined;
    const size = selectedData?.size === "4K" || selectedData?.size === "2K" || selectedData?.size === "1K"
      ? selectedData.size
      : "2K";
    const aspectRatio = typeof selectedData?.aspectRatio === "string" ? selectedData.aspectRatio : "1:1";

    jobs.forEach(({ preset, view }, index) => {
      const directorControls = currentDirectorControls(projectId, canvasId);
      const sourcePrompt = expressionPrompt(title, preset, view, intensity);
      const director = compileDirectorPrompt({
        basePrompt: sourcePrompt,
        modelId: model,
        controls: directorControls,
        referenceDescription: `${title}; preserve exact character identity`,
      });
      const nodeId = store.addNode(
        CANVAS_NODE_TYPES.imageGen,
        {
          x: startX + (index % 3) * columnWidth,
          y: startY + Math.floor(index / 3) * rowHeight,
        },
        {
          displayName: `${preset.emoji} ${title} · ${preset.label} · ${view.label}`,
          prompt: director.prompt,
          compiledPromptPreview: director.prompt,
          director_source_prompt: sourcePrompt,
          director_style_id: directorControls.styleId,
          director_prompt_revision: director.revision,
          director_model_profile: director.profile,
          director_layers: director.layers,
          director_warnings: director.warnings,
          referenceImageUrl: imageUrl,
          imageUrl: null,
          previewImageUrl: imageUrl,
          aspectRatio,
          requestAspectRatio: aspectRatio,
          size,
          count: 1,
          ...(model ? { model } : {}),
          expression_set_id: setId,
          expression_source_node_id: selectedNode.id,
          expression_key: preset.key,
          expression_label: preset.label,
          expression_view_key: view.key,
          expression_view_label: view.label,
          expression_intensity: Number(intensity.toFixed(2)),
          expression_control_revision: EXPRESSION_CONTROL_REVISION,
          expression_control_source: EXPRESSION_CONTROL_SOURCE,
          expression_primary: preset.key,
          expression_secondary: null,
          expression_scale: Number(intensity.toFixed(2)),
          expression_status: "draft",
          expression_identity_lock: true,
          expression_created_at: new Date().toISOString(),
          expression_auto_generate: autoGenerate,
        } as Partial<CanvasNodeData>,
      );
      store.addEdge(selectedNode.id, nodeId);
    });
    onToast(
      autoGenerate
        ? `已创建并提交 ${jobs.length} 个表情×视角矩阵候选；全部保留身份源和 lineage`
        : `已创建 ${jobs.length} 个可编辑矩阵节点；检查提示词后可逐个生成`,
    );
  };
  void createExpressionSet;

  return (
    <aside
      aria-label="情绪调节"
      data-expression-preview-renderer={EXPRESSION_PREVIEW_RENDERER}
      className="village-canvas-mood-panel pointer-events-auto absolute right-4 top-20 z-40 w-[600px] max-w-[calc(100vw-32px)] max-h-[calc(100%-120px)] overflow-y-auto rounded-[20px] border border-white/[0.08] bg-[#1b1b1d] text-[#f2f2f2] shadow-[0_24px_64px_rgba(0,0,0,.55)] [animation:mood-panel-in_180ms_ease-out_both]"
    >
      <style>{`@keyframes mood-panel-in { from { opacity: 0; transform: translateY(6px) scale(.99); } to { opacity: 1; transform: none; } }`}</style>

      {/* 顶栏：关闭 / 标题 / 模型 / 尺寸 / 张数 / 生成（对齐参考实现的紧凑排布） */}
      <div className="flex h-11 items-center gap-1.5 px-2.5">
        <button type="button" onClick={onRequestClose} aria-label="关闭情绪调节" className="grid size-7 shrink-0 place-items-center rounded-full text-[16px] leading-none text-white/45 transition-colors duration-150 hover:bg-white/10 hover:text-white">×</button>
        <div className="min-w-0 flex-1 truncate pl-0.5 text-[13px] font-semibold">情绪调节</div>
        <select aria-label="表情生成模型" value={modelId} onChange={(event)=>setModelId(event.target.value)} disabled={expressionModels.length===0} className="max-w-[136px] shrink-0 cursor-pointer rounded-full bg-white/[0.06] px-2.5 py-1 text-[11px] text-white/75 outline-none transition-colors duration-150 hover:bg-white/10 disabled:cursor-default disabled:opacity-40">{expressionModels.length===0?<option value="">没有支持图生图的模型</option>:expressionModels.map((model)=><option key={model.id} value={model.id}>{model.label}</option>)}</select>
        {outputSizeOptions.length > 0 && <select aria-label="输出尺寸" value={outputSize} onChange={(event)=>setOutputSize(event.target.value)} className="shrink-0 cursor-pointer rounded-full bg-white/[0.06] px-2 py-1 text-[11px] text-white/75 outline-none transition-colors duration-150 hover:bg-white/10">{outputSizeOptions.map((option)=><option key={option} value={option}>{option}</option>)}</select>}
        <select aria-label="生成张数" value={outputCount} onChange={(event)=>setOutputCount(Number(event.target.value) as 1|2|4|6|8|12)} className="shrink-0 cursor-pointer rounded-full bg-white/[0.06] px-2 py-1 text-[11px] text-white/75 outline-none transition-colors duration-150 hover:bg-white/10"><option value="1">1张</option><option value="2">2张</option><option value="4">4张</option><option value="6">6张</option><option value="8">8张</option><option value="12">12张</option></select>
        <button type="button" onClick={()=>createContinuousAffect(false)} disabled={!activeRegion||isAffectDragging||!selectedImageModel} className="shrink-0 rounded-full px-2.5 py-1 text-[11px] text-white/50 transition-colors duration-150 hover:bg-white/10 hover:text-white/90 disabled:opacity-30">仅创建</button>
        <button type="button" onClick={()=>createContinuousAffect(true)} disabled={!activeRegion||isAffectDragging||!selectedImageModel} aria-label="生成新图片" className="grid size-8 shrink-0 place-items-center rounded-full bg-white text-[15px] font-semibold text-[#171717] shadow-[0_6px_18px_rgba(0,0,0,.45)] transition-transform duration-150 hover:scale-105 active:scale-95 disabled:opacity-35">↑</button>
      </div>

      {/* 人物芯片 + 手动添加 + 自动识别 */}
      <div className="flex items-center gap-1.5 px-3 pb-2">
        {regions.map((region,index)=><button key={region.id} type="button" onClick={()=>{setActiveRegionId(region.id);setVisualMode("preview")}} className={`flex shrink-0 items-center gap-1.5 rounded-full py-1 pl-1 pr-2.5 text-[11px] transition-colors duration-150 ${activeRegion?.id===region.id?"bg-white/[0.14] text-white":"text-white/55 hover:bg-white/[0.07] hover:text-white/85"}`}>{imageUrl&&<img src={imageUrl} alt="" className="size-4 rounded-full object-cover"/>}角色{index+1}</button>)}
        <button type="button" onClick={()=>{setVisualMode("locate");addRegionAt(.5,.35)}} disabled={!imageUrl} className="shrink-0 rounded-full border border-dashed border-white/20 px-2.5 py-1 text-[11px] text-white/50 transition-colors duration-150 hover:border-white/45 hover:text-white/90 disabled:opacity-30">＋手动添加</button>
        <button type="button" onClick={()=>{setVisualMode("locate");void runFaceDetection()}} disabled={!imageUrl||faceDetectionStatus==="loading"} className="shrink-0 rounded-full px-2.5 py-1 text-[11px] text-white/40 transition-colors duration-150 hover:bg-white/[0.07] hover:text-white/85 disabled:opacity-30">{faceDetectionStatus==="loading"?"识别中…":"自动识别"}</button>
        {activeRegion&&<button type="button" onClick={()=>{setRegions(current=>current.filter(item=>item.id!==activeRegion.id));setActiveRegionId(null);setVisualMode("locate")}} className="ml-auto shrink-0 rounded-full px-2 py-1 text-[10px] text-white/30 transition-colors duration-150 hover:bg-rose-500/15 hover:text-rose-300">删除</button>}
      </div>
      {faceDetectionStatus==="empty"&&<div className="px-3.5 pb-1.5 text-[10px] text-amber-300/90">未自动识别人脸，请直接在原图上拖框。</div>}
      {faceDetectionStatus==="failed"&&<div className="px-3.5 pb-1.5 text-[10px] text-amber-300/90">本地人脸识别不可用，仍可自由拖框，不影响生成。</div>}

      {/* 左：白膜头预览　右：情绪盘 */}
      <div className="flex gap-2.5 px-3">
        <div ref={imageContainerRef} className="relative h-[236px] min-w-0 flex-1 overflow-hidden rounded-2xl border border-white/[0.06] bg-[radial-gradient(circle_at_50%_36%,#4c4c4f_0%,#252528_62%,#131315_100%)]">
          {visualMode==="locate" ? <>
            {imageUrl ? <img ref={imageRef} src={imageUrl} alt="人物定位" draggable={false} onLoad={()=>{const sourceKey=`${selectedNodeId ?? "none"}:${imageUrl}`;if(lastAutoDetectedSourceRef.current!==sourceKey){lastAutoDetectedSourceRef.current=sourceKey;void runFaceDetection()}}} className="h-full w-full select-none object-contain"/> : <div className="flex h-full items-center justify-center text-xs text-white/40">选择图片后添加人物</div>}
            {imageUrl && <div ref={imageOverlayRef} aria-label="人物真实框选区域" style={{left:imageViewport.left,top:imageViewport.top,width:imageViewport.width,height:imageViewport.height}} className="absolute touch-none cursor-crosshair" onPointerDown={(event)=>beginRegionGesture(event,"draw")} onPointerMove={updateRegionGesture} onPointerUp={endRegionGesture} onPointerCancel={endRegionGesture}>
              {[...regions,...(draftRegion?[draftRegion]:[])].map((region,index)=><div key={region.id} role="button" tabIndex={0} aria-label={`${region.label} 定位框`} onPointerDown={(event)=>region.id!=="draft"&&beginRegionGesture(event,"move",region)} style={{left:`${region.x*100}%`,top:`${region.y*100}%`,width:`${region.width*100}%`,height:`${region.height*100}%`}} className={`absolute border ${region.id==="draft"?"border-dashed border-white bg-white/5":activeRegion?.id===region.id?"border-white bg-white/5 shadow-[0_0_0_1px_rgba(0,0,0,.7)]":"border-[#aaa] bg-white/5"}`}><span className="pointer-events-none absolute -top-5 left-0 rounded bg-black/80 px-1.5 text-[9px]">{region.id==="draft"?"松开完成":`人物 ${index+1}`}</span>{region.id!=="draft"&&<span role="button" aria-label={`调整${region.label}大小`} onPointerDown={(event)=>beginRegionGesture(event,"resize",region)} className="absolute -bottom-1.5 -right-1.5 h-3 w-3 cursor-nwse-resize rounded-full border border-black bg-white"/>}</div>)}
            </div>}
            {imageUrl&&regions.length===0&&!draftRegion&&<div className="pointer-events-none absolute bottom-3 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-full bg-black/60 px-2.5 py-1 text-[10px] text-white/70 backdrop-blur">在图片上拖框，或点击自动识别</div>}
            {activeRegion&&<button type="button" onClick={()=>setVisualMode("preview")} className="absolute bottom-2.5 right-2.5 rounded-full bg-white px-3 py-1.5 text-[10px] font-semibold text-[#171717] shadow-[0_6px_18px_rgba(0,0,0,.45)] transition-transform duration-150 hover:scale-[1.03]">查看表情预览</button>}
          </> : <>
            <ExpressionHeadPreview affectX={previewAffect.x} affectY={previewAffect.y} primaryExpression={previewPrimaryExpression} secondaryExpression={secondaryExpression} expressionScale={expressionScale} secondaryWeight={secondaryWeight} onCanvasReady={handleExpressionCanvasReady}/>
            <button type="button" onClick={()=>setVisualMode("locate")} className="absolute left-2 top-2 rounded-full bg-black/45 px-2.5 py-1 text-[10px] text-white/70 backdrop-blur transition-colors duration-150 hover:bg-black/65 hover:text-white">重新定位</button>
          </>}
        </div>
        <div className="relative h-[236px] w-[240px] shrink-0">
          <span className="absolute left-1/2 top-0 -translate-x-1/2 text-[9px] text-white/45" title="高激活">激动</span>
          <span className="absolute bottom-0 left-1/2 -translate-x-1/2 text-[9px] text-white/45" title="低激活">平静</span>
          <span className="absolute left-0 top-1/2 -translate-y-1/2 text-[9px] text-white/45" title="正向效价">亲近</span>
          <span className="absolute right-0 top-1/2 -translate-y-1/2 text-[9px] text-white/45" title="负向效价">疏离</span>
          <div ref={padRef} aria-label="50 个语义锚点与 441 坐标连续情绪空间" onPointerDown={beginAffectGesture} onPointerMove={moveAffectGesture} onPointerUp={endAffectGesture} onPointerCancel={cancelAffectGesture} className={`absolute inset-x-[24px] inset-y-[14px] touch-none overflow-hidden rounded-[18px] border border-white/[0.06] bg-[linear-gradient(180deg,rgba(255,255,255,.15),rgba(255,255,255,.055))] shadow-[inset_0_1px_0_rgba(255,255,255,.14)] ${isAffectDragging?"cursor-grabbing":"cursor-grab"}`}>
            {MOOD_GRID.map((mood,index)=>{const row=Math.floor(index/MOOD_GRID_COLUMNS);const col=index%MOOD_GRID_COLUMNS;const active=row===selectedMood.row&&col===selectedMood.col;return <button key={mood.key} type="button" aria-label={mood.label} title={mood.label} onClick={(event)=>{if(event.detail===0||!affectGestureMovedRef.current)selectMoodCell(row,col)}} style={{left:`${(col/MOOD_GRID_COLUMNS)*100}%`,top:`${(row/MOOD_GRID_ROWS)*100}%`,width:`${100/MOOD_GRID_COLUMNS}%`,height:`${100/MOOD_GRID_ROWS}%`}} className="group absolute z-10 grid place-items-center"><span className={`rounded-full transition-all duration-150 ${active?"size-[10px] bg-white shadow-[0_0_0_3px_rgba(255,255,255,.22),0_0_16px_rgba(255,255,255,.40)]":"size-[6px] bg-white/55 group-hover:size-[8px] group-hover:bg-white/90"}`}/></button>})}
            <span ref={affectCursorRef} className={`pointer-events-none absolute left-0 top-0 z-20 size-7 rounded-full bg-white/95 shadow-[0_6px_16px_rgba(0,0,0,.45),0_0_0_6px_rgba(255,255,255,.10)] will-change-transform ${isAffectDragging?"scale-110":"transition-transform duration-200 ease-out"}`}/>
          </div>
        </div>
      </div>

      {/* 读数行：情绪定位 + 提示词开关 */}
      <div className="flex items-center gap-2 px-3.5 pb-2.5 pt-2.5" title={`${moodMixLabel} · X ${affectX.toFixed(2)} · Y ${affectY.toFixed(2)}`}>
        <span className="shrink-0 text-[10px] text-white/40">情绪定位</span>
        <span className="truncate text-[12px] font-medium text-white">{selectedMood.item.label}</span>
        {moodDirectionModifier(affectX, affectY)&&<span className="shrink-0 rounded-full bg-white/[0.08] px-1.5 py-0.5 text-[9px] text-white/60">{moodDirectionModifier(affectX, affectY)}</span>}
        <button type="button" onClick={()=>setPromptOpen((current)=>!current)} className="ml-auto shrink-0 rounded-full px-2 py-0.5 text-[10px] text-white/40 transition-colors duration-150 hover:bg-white/10 hover:text-white/85">{promptOpen?"收起提示词":"查看提示词"}</button>
      </div>
      {promptOpen&&<div className="mx-3.5 mb-3 max-h-24 overflow-y-auto whitespace-pre-wrap rounded-xl bg-black/30 px-2.5 py-2 text-[10px] leading-4 text-white/55">{selectedMood.item.expressionPrompt}</div>}
    </aside>
  );
}
