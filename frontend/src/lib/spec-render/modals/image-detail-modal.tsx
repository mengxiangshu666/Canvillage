"use client";

import React, { useEffect, useRef, useState } from "react";
import { Check, Download, X } from "lucide-react";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogTitle,
} from "../dialog";
import { useSpecRendererContext } from "../context";

type ImageModalMode = "preview" | "edit";

type CropRect = { x: number; y: number; width: number; height: number };
type DragHandle =
  | "move"
  | "n"
  | "s"
  | "e"
  | "w"
  | "nw"
  | "ne"
  | "sw"
  | "se";
type DragState = {
  handle: DragHandle;
  startX: number;
  startY: number;
  startCrop: CropRect;
};

type DetailSection = { label: string; value: string };

export type ImageCandidate = {
  id: string;
  src: string;
  label?: string;
};

type ImageDetailModalProps = {
  src: string;
  hasOverlay?: boolean;
  overlayTitle?: string;
  overlayDescription?: string;
  detailType?: string;
  detailTags?: string[];
  detailSections?: DetailSection[];
  historyImages?: string[];
  candidates?: ImageCandidate[];
  onSelectCandidate?: (candidate: ImageCandidate) => void;
  open: boolean;
  setOpen: (open: boolean) => void;
  mode?: ImageModalMode;
};

const DEFAULT_CROP: CropRect = { x: 0.12, y: 0.12, width: 0.76, height: 0.76 };
const MIN_CROP_SIZE = 0.12;

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function normalizeCrop(next: CropRect): CropRect {
  const width = clamp(next.width, MIN_CROP_SIZE, 1);
  const height = clamp(next.height, MIN_CROP_SIZE, 1);
  const x = clamp(next.x, 0, 1 - width);
  const y = clamp(next.y, 0, 1 - height);
  return { x, y, width, height };
}

function applyDrag(
  handle: DragHandle,
  startCrop: CropRect,
  dx: number,
  dy: number,
): CropRect {
  let { x, y, width, height } = startCrop;
  const right = x + width;
  const bottom = y + height;

  switch (handle) {
    case "move":
      x = clamp(startCrop.x + dx, 0, 1 - startCrop.width);
      y = clamp(startCrop.y + dy, 0, 1 - startCrop.height);
      break;
    case "n":
      y = clamp(startCrop.y + dy, 0, bottom - MIN_CROP_SIZE);
      height = bottom - y;
      break;
    case "s":
      height = clamp(startCrop.height + dy, MIN_CROP_SIZE, 1 - startCrop.y);
      break;
    case "w":
      x = clamp(startCrop.x + dx, 0, right - MIN_CROP_SIZE);
      width = right - x;
      break;
    case "e":
      width = clamp(startCrop.width + dx, MIN_CROP_SIZE, 1 - startCrop.x);
      break;
    case "nw":
      x = clamp(startCrop.x + dx, 0, right - MIN_CROP_SIZE);
      y = clamp(startCrop.y + dy, 0, bottom - MIN_CROP_SIZE);
      width = right - x;
      height = bottom - y;
      break;
    case "ne":
      y = clamp(startCrop.y + dy, 0, bottom - MIN_CROP_SIZE);
      width = clamp(startCrop.width + dx, MIN_CROP_SIZE, 1 - startCrop.x);
      height = bottom - y;
      break;
    case "sw":
      x = clamp(startCrop.x + dx, 0, right - MIN_CROP_SIZE);
      width = right - x;
      height = clamp(startCrop.height + dy, MIN_CROP_SIZE, 1 - startCrop.y);
      break;
    case "se":
      width = clamp(startCrop.width + dx, MIN_CROP_SIZE, 1 - startCrop.x);
      height = clamp(startCrop.height + dy, MIN_CROP_SIZE, 1 - startCrop.y);
      break;
  }

  return normalizeCrop({ x, y, width, height });
}

export function ImageDetailModal({
  src,
  overlayTitle,
  overlayDescription,
  detailTags,
  detailSections,
  historyImages,
  candidates,
  onSelectCandidate,
  open,
  setOpen,
  mode = "preview",
}: ImageDetailModalProps) {
  const { onToast } = useSpecRendererContext();
  const editorRef = useRef<HTMLDivElement | null>(null);
  const [crop, setCrop] = useState<CropRect>(DEFAULT_CROP);
  const [dragState, setDragState] = useState<DragState | null>(null);
  const [, setStageSize] = useState({ width: 0, height: 0 });
  const [orderedCandidates, setOrderedCandidates] = useState<ImageCandidate[]>(
    candidates ?? [],
  );
  const [confirmedIdx, setConfirmedIdx] = useState(0);
  const [previewIdx, setPreviewIdx] = useState(0);
  const isEditMode = mode === "edit";
  const hasCandidates = candidates && candidates.length > 0;

  useEffect(() => {
    const node = editorRef.current;
    if (!node) return;
    const updateSize = () => {
      const rect = node.getBoundingClientRect();
      setStageSize({ width: rect.width, height: rect.height });
    };
    updateSize();
    const observer = new ResizeObserver(updateSize);
    observer.observe(node);
    return () => observer.disconnect();
  }, [open, isEditMode]);

  useEffect(() => {
    if (!dragState) return;
    const handleMove = (event: MouseEvent) => {
      const bounds = editorRef.current?.getBoundingClientRect();
      if (!bounds || bounds.width <= 0 || bounds.height <= 0) return;
      const dx = (event.clientX - dragState.startX) / bounds.width;
      const dy = (event.clientY - dragState.startY) / bounds.height;
      setCrop(applyDrag(dragState.handle, dragState.startCrop, dx, dy));
    };
    const handleUp = () => setDragState(null);
    window.addEventListener("mousemove", handleMove);
    window.addEventListener("mouseup", handleUp);
    return () => {
      window.removeEventListener("mousemove", handleMove);
      window.removeEventListener("mouseup", handleUp);
    };
  }, [dragState]);

  function startDrag(handle: DragHandle, event: React.MouseEvent) {
    if (!isEditMode) return;
    event.preventDefault();
    event.stopPropagation();
    setDragState({
      handle,
      startX: event.clientX,
      startY: event.clientY,
      startCrop: crop,
    });
  }

  function handleDownload() {
    const target = displaySrc || src;
    if (!target) return;
    window.open(target, "_blank", "noopener,noreferrer");
  }

  function handleOpenChange(nextOpen: boolean) {
    if (nextOpen) {
      setCrop(DEFAULT_CROP);
      setOrderedCandidates(candidates ?? []);
      setConfirmedIdx(0);
      setPreviewIdx(0);
    } else {
      setDragState(null);
    }
    setOpen(nextOpen);
  }

  function handleConfirmReplace() {
    if (!hasCandidates) return;
    const selected = orderedCandidates[previewIdx];
    if (!selected) return;

    const reordered = [
      selected,
      ...orderedCandidates.filter((_, i) => i !== previewIdx),
    ];
    setOrderedCandidates(reordered);
    setConfirmedIdx(0);
    setPreviewIdx(0);

    onSelectCandidate?.(selected);
    onToast?.("角色替换成功", "success");
  }

  const isPreviewingDifferent = hasCandidates && previewIdx !== confirmedIdx;
  const displaySrc = hasCandidates
    ? (orderedCandidates[previewIdx]?.src ?? src)
    : src;
  const hasDetailInfo =
    overlayTitle ||
    overlayDescription ||
    (detailTags && detailTags.length > 0) ||
    (detailSections && detailSections.length > 0);
  const hasHistory = historyImages && historyImages.length > 0;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        showCloseButton={false}
        className="fixed inset-0 top-0 left-0 flex h-screen w-screen max-w-none translate-x-0 translate-y-0 items-center justify-center rounded-none border-none bg-black/20 p-0 text-white backdrop-blur-xl sm:max-w-none"
      >
        <DialogTitle className="sr-only">
          {isEditMode ? "编辑图片" : "图片详情"}
        </DialogTitle>

        <div className="absolute top-6 right-8 z-50 flex items-center gap-3">
          <button
            className="text-gray-400 transition hover:text-white"
            onClick={handleDownload}
            type="button"
          >
            <Download className="h-5 w-5" />
          </button>
          <DialogClose className="text-gray-400 transition hover:text-white outline-none">
            <X className="h-5 w-5" />
          </DialogClose>
        </div>

        <div className="flex w-full flex-col items-center gap-6 px-6 pt-14 pb-6 max-h-screen overflow-y-auto">
          <div className="w-full max-w-4xl">
            {isEditMode ? (
              <div className="jr-image-editor-panel">
                <div className="jr-image-editor-header">
                  <div>
                    <div className="jr-image-editor-title">裁剪图片</div>
                    <div className="jr-image-editor-subtitle">
                      拖动边线或控制点调整裁剪区域。
                    </div>
                  </div>
                </div>
                <div className="jr-image-editor-body">
                  <div ref={editorRef} className="jr-image-editor-stage">
                    <img
                      src={src}
                      alt={overlayTitle ?? "Editable image"}
                      className="jr-image-editor-image"
                    />
                    <div
                      className="jr-image-crop-box"
                      style={{
                        left: `${crop.x * 100}%`,
                        top: `${crop.y * 100}%`,
                        width: `${crop.width * 100}%`,
                        height: `${crop.height * 100}%`,
                      }}
                      onMouseDown={(e) => startDrag("move", e)}
                    >
                      <div className="jr-image-crop-grid" />
                      {(
                        ["n", "s", "e", "w", "nw", "ne", "sw", "se"] as DragHandle[]
                      ).map((h) => (
                        <button
                          key={h}
                          type="button"
                          className={`jr-crop-handle jr-crop-handle--${h}`}
                          onMouseDown={(e) => startDrag(h, e)}
                        />
                      ))}
                    </div>
                  </div>
                </div>
                <div className="jr-image-editor-actions">
                  <button
                    type="button"
                    className="rounded-md border border-white/20 px-4 py-2 text-sm hover:bg-white/5"
                    onClick={() => handleOpenChange(false)}
                  >
                    取消
                  </button>
                  <button
                    type="button"
                    className="rounded-md bg-white px-4 py-2 text-sm text-black hover:bg-white/90"
                    onClick={() => handleOpenChange(false)}
                  >
                    确定
                  </button>
                </div>
              </div>
            ) : (
              <div className="flex w-full justify-center">
                <div className="grid w-full max-w-[960px] grid-cols-1 items-start gap-8 md:grid-cols-[minmax(280px,400px)_minmax(0,1fr)]">
                  <div className="mx-auto w-full max-w-[400px]">
                    <div className="relative overflow-hidden gap-8 rounded-[20px] shadow-[0_24px_60px_rgba(0,0,0,0.4)]">
                      <img
                        src={displaySrc}
                        alt={overlayTitle ?? "Detail"}
                        className="h-auto w-full object-contain"
                      />
                      {hasCandidates && (
                        <div className="absolute bottom-3 right-3">
                          {isPreviewingDifferent ? (
                            <button
                              type="button"
                              className="rounded-lg bg-cyan-500 px-3 py-1.5 text-xs font-medium text-white shadow-lg transition hover:bg-cyan-400"
                              onClick={handleConfirmReplace}
                            >
                              确认替换
                            </button>
                          ) : (
                            <span className="rounded-lg bg-white/15 px-3 py-1.5 text-xs font-medium text-white/70 backdrop-blur-sm">
                              当前图片
                            </span>
                          )}
                        </div>
                      )}
                    </div>
                  </div>

                  {hasDetailInfo && (
                    <div className="flex flex-col gap-4 pt-2">
                      {overlayTitle && (
                        <h2 className="text-2xl font-semibold tracking-tight text-white/92">
                          {overlayTitle}
                        </h2>
                      )}

                      {detailTags && detailTags.length > 0 && (
                        <div className="flex flex-wrap gap-2">
                          {detailTags.map((tag, i) => (
                            <span
                              key={i}
                              className="rounded-full bg-[#11263b] px-3 py-1 text-xs text-white/80"
                            >
                              {tag}
                            </span>
                          ))}
                        </div>
                      )}

                      {overlayDescription && (
                        <p className="text-sm leading-7 text-white/60 whitespace-pre-wrap">
                          {overlayDescription}
                        </p>
                      )}

                      {detailSections && detailSections.length > 0 && (
                        <div className="space-y-3 border-t border-white/10 pt-4">
                          {detailSections.map((section, i) => (
                            <div
                              key={i}
                              className="flex items-baseline gap-3 text-sm"
                            >
                              <span className="shrink-0 text-white/40">
                                {section.label}
                              </span>
                              <span className="text-white/75">
                                {section.value}
                              </span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>

          {!isEditMode && hasCandidates && (
            <div className="w-full max-w-4xl">
              <div className="mb-3 text-xs text-white/40">历史版本</div>
              <div className="flex gap-3 overflow-x-auto pt-1 pb-2 pl-2">
                {orderedCandidates.map((candidate, i) => (
                  <div key={candidate.id} className="shrink-0">
                    <div
                      className={`relative h-16 w-16 cursor-pointer overflow-hidden rounded-lg transition-all ${
                        previewIdx === i
                          ? "ring-2 ring-cyan-400 ring-offset-2 ring-offset-black"
                          : confirmedIdx === i
                            ? "ring-2 ring-white/30 ring-offset-1 ring-offset-black"
                            : "hover:ring-2 hover:ring-white/40"
                      }`}
                      onClick={() => setPreviewIdx(i)}
                      role="button"
                      tabIndex={0}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          setPreviewIdx(i);
                        }
                      }}
                    >
                      <img
                        src={candidate.src}
                        alt={candidate.label ?? `版本 ${i + 1}`}
                        className="h-full w-full object-cover"
                      />
                      {confirmedIdx === i && (
                        <div className="absolute inset-0 flex items-center justify-center bg-black/30">
                          <Check className="h-4 w-4 text-white/80" />
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {!isEditMode && !hasCandidates && hasHistory && (
            <div className="w-full max-w-4xl">
              <div className="mb-2 text-xs text-white/40">历史版本</div>
              <div className="flex gap-2 overflow-x-auto pb-2">
                {historyImages!.map((imgSrc, i) => (
                  <img
                    key={i}
                    src={imgSrc}
                    alt={`历史版本 ${i + 1}`}
                    className="h-16 w-16 shrink-0 cursor-pointer rounded-lg object-cover transition-all hover:ring-2 hover:ring-white/40"
                  />
                ))}
              </div>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

export default ImageDetailModal;
